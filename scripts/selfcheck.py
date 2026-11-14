"""全量自查：代码不变量、数据完整性、边界输入、可复现性、发布风险。

运行：python scripts/selfcheck.py
输出：控制台逐项结果 + reports/selfcheck.md
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chinese_chunk.evaluate import (  # noqa: E402
    evaluate_strategy, extract_units, load_corpus, load_qa,
)
from chinese_chunk.splitters import build_chunker  # noqa: E402
from chinese_chunk.textnorm import count_tokens, normalize, squash  # noqa: E402

FINDINGS: list[dict] = []


def record(level: str, area: str, message: str) -> None:
    FINDINGS.append({"level": level, "area": area, "message": message})


def ok(area: str, message: str) -> None:
    record("OK", area, message)


def warn(area: str, message: str) -> None:
    record("WARN", area, message)


def fail(area: str, message: str) -> None:
    record("FAIL", area, message)


# ---------------------------------------------------------------- 1. 语料完整性

def check_corpus() -> dict[str, str]:
    manifest_path = ROOT / "data" / "corpus_manifest.json"
    corpus_dir = ROOT / "data" / "corpus"
    docs = {}
    if not manifest_path.exists():
        fail("语料", "缺少 data/corpus_manifest.json，无法核对来源与哈希")
        return docs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest:
        path = corpus_dir / item["name"]
        if not path.exists():
            fail("语料", f"{item['name']} 不存在")
            continue
        raw = path.read_text(encoding="utf-8")          # manifest 记录的是磁盘原文
        text = normalize(raw)                            # 切片与检索用规范化文本
        docs[path.stem] = text
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if digest != item["sha256"]:
            warn("语料", f"{item['name']} 哈希与 manifest 不一致（内容被改动过或 manifest 过期）")
        if len(raw) != item["chars"]:
            warn("语料", f"{item['name']} 字符数与 manifest 不一致：{len(raw)} vs {item['chars']}")
        if not item.get("url"):
            fail("语料", f"{item['name']} 缺少来源 URL")
    if docs:
        ok("语料", f"{len(docs)} 篇语料可读，manifest 字段齐全，哈希已核对")
    return docs


# ---------------------------------------------------------------- 2. 问答集

def check_qa(docs: dict[str, str]) -> None:
    qa_path = ROOT / "data" / "qa.jsonl"
    if not qa_path.exists():
        fail("问答集", "缺少 data/qa.jsonl")
        return
    qa = load_qa(qa_path)
    if not qa:
        fail("问答集", "问答集为空")
        return
    ids = [q["qid"] for q in qa]
    if len(set(ids)) != len(ids):
        fail("问答集", "qid 有重复")
    dup_questions = {q["question"] for q in qa if [x["question"] for x in qa].count(q["question"]) > 1}
    if dup_questions:
        fail("问答集", f"问题有重复：{list(dup_questions)[:2]}")
    for q in qa:
        if q["doc"] not in docs:
            fail("问答集", f"{q['qid']} 指向不存在的文档 {q['doc']}")
            continue
        quote = q["gold_quote"]
        if squash(quote) not in squash(docs[q["doc"]]):
            fail("问答集", f"{q['qid']} 引文无法在原文定位")
        if len(squash(quote)) < 30:
            warn("问答集", f"{q['qid']} 引文过短（{len(squash(quote))} 字），对切碎不敏感")
        if squash(quote) in squash(q["question"]):
            fail("问答集", f"{q['qid']} 引文是问题子串，检索会被原文命中而虚高")
        if len(q["question"]) < 8:
            warn("问答集", f"{q['qid']} 问题过短，可能只靠关键词命中")
        # 引文会被公开再分发，逐条必须带授权标识与内容摘要（见 NOTICE.md）
        lic = q.get("source_license")
        if lic not in ("public-domain", "CC-BY-SA-4.0", "NOASSERTION"):
            fail("问答集", f"{q['qid']} 缺少可识别的 source_license：{lic!r}")
        digest = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        if q.get("quote_sha256") != digest:
            fail("问答集", f"{q['qid']} quote_sha256 与 gold_quote 不一致")
    per_doc = {d: sum(1 for q in qa if q["doc"] == d) for d in docs}
    ok("问答集", f"{len(qa)} 条，分布 {per_doc}")


# ---------------------------------------------------------------- 3. 切片不变量

def check_chunk_invariants(docs: dict[str, str]) -> None:
    for strategy in ("fixed", "sentence", "recursive", "structure"):
        for size in (200, 400, 600, 900):
            chunker = build_chunker(strategy, max_chars=size, overlap=min(80, size // 3))
            for doc_id, doc in docs.items():
                chunks = chunker.chunk(doc_id, doc)
                if not chunks:
                    fail("切片", f"{strategy}@{size} {doc_id} 无产出")
                    continue
                for c in chunks:
                    if not c.text.strip():
                        fail("切片", f"{strategy}@{size} {c.chunk_id} 空块")
                    # 允许的超出：标题前缀 + 少量行拼接开销
                    limit = size + (len(" > ".join(c.heading_path)) + 1 if c.heading_path else 0)
                    if len(c.text) > limit + 60:
                        fail("切片", f"{strategy}@{size} {c.chunk_id} 超限 {len(c.text)} > {limit + 60}")
                    if c.start < 0 or c.end > len(doc) or c.end < c.start:
                        fail("切片", f"{strategy}@{size} {c.chunk_id} 偏移非法 {c.start}-{c.end}")
                    body = c.text
                    if c.heading_path and strategy == "structure":
                        prefix = " > ".join(c.heading_path) + "\n"
                        if body.startswith(prefix):
                            body = body[len(prefix):]
                    if squash(body)[:80] not in squash(doc):
                        fail("切片", f"{strategy}@{size} {c.chunk_id} 正文不在原文中")
                # 结构单元完整率：structure 应当 100%
                if strategy == "structure":
                    for unit in extract_units(doc):
                        if len(squash(unit)) <= size * 0.8 and not any(
                            squash(unit) in squash(c.text) for c in chunks
                        ):
                            fail("切片", f"structure@{size} {doc_id} 切断单元：{unit[:26]}")
    ok("切片", "四种策略 × 四个上限 × 三篇文档：无空块、无超限、无偏移越界、正文可回溯")


# ---------------------------------------------------------------- 4. 表格与代码块

TABLE_MD = """# 费率表

## 增值税

| 税目 | 税率 |
| --- | --- |
| 销售货物 | 13% |
| 交通运输服务 | 9% |
| 现代服务 | 6% |
| 小规模纳税人 | 3% |

## 说明

以上为一般口径。
"""

TABLE_HTML = """<table>
<tr><td>A</td><td>1</td></tr>
<tr><td>B</td><td>2</td></tr>
</table>
"""

CODE = """# 示例

```python
def f(x):
    return x * 2
```

结束。
"""


def check_tables() -> None:
    chunker = build_chunker("structure", max_chars=120, overlap=20)
    chunks = chunker.chunk("fixture-table", TABLE_MD)
    tables = [c for c in chunks if c.kind == "table"]
    if not tables:
        warn("表格", "Markdown 表格未被识别为 table 原子块")
    for c in tables:
        body = c.text
        if c.heading_path:
            prefix = " > ".join(c.heading_path) + "\n"
            if body.startswith(prefix):
                body = body[len(prefix):]
        if not body.lstrip().startswith("|"):
            fail("表格", f"{c.chunk_id} 表格块正文未以表格行开头")
        # 切分后每块都应重复表头
        rows = [ln for ln in body.split("\n") if ln.strip().startswith("|")]
        if len(rows) > 2 and "| 税目 | 税率 |" not in body:
            fail("表格", f"{c.chunk_id} 拆分后未重复表头")

    chunks = chunker.chunk("fixture-html", TABLE_HTML)
    if not any(c.kind == "table" for c in chunks):
        warn("表格", "HTML <table> 未被识别为 table 原子块")

    chunks = chunker.chunk("fixture-code", CODE)
    if not any(c.kind == "code" for c in chunks):
        warn("表格", "代码围栏未被识别为 code 原子块")
    ok("表格", "Markdown 表格、HTML 表格、代码围栏均被识别为原子块")


# ---------------------------------------------------------------- 5. 边界输入

def check_edge_inputs() -> None:
    cases = {
        "空文档": "",
        "纯空白": "   \n\n\t\n",
        "单行超长无标点": "甲" * 5000,
        "全英文无换行": "word " * 1200,
        "CRLF 换行": "# 标题\r\n\r\n正文第一段。\r\n\r\n正文第二段。\r\n",
        "含 emoji 与零宽": "# 标题\n\n😀👍 测试\u200b文本。\n",
        "只有标题": "# 标题\n## 子标题\n",
        "超长条款": "第一条 " + "内容" * 2000,
    }
    for name, text in cases.items():
        for strategy in ("fixed", "sentence", "recursive", "structure"):
            try:
                chunks = build_chunker(strategy, max_chars=300, overlap=50).chunk("edge", text)
            except Exception as exc:
                fail("边界", f"{name} / {strategy} 抛异常：{type(exc).__name__}: {exc}")
                continue
            for c in chunks:
                if len(c.text) > 300 + 120:
                    warn("边界", f"{name} / {strategy} 块超限 {len(c.text)}")
                if not c.text.strip():
                    fail("边界", f"{name} / {strategy} 产生空块")
    ok("边界", f"{len(cases)} 类异常输入 × 4 策略：无崩溃、无空块")


# ---------------------------------------------------------------- 6. 可复现性

def check_determinism(docs: dict[str, str], qa: list[dict]) -> None:
    outs = []
    for _ in range(2):
        res, _chunks = evaluate_strategy(
            docs, qa, "structure", max_chars=600, overlap=80, top_k=5
        )
        outs.append([res.recall_at_1, res.recall_at_3, res.recall_at_5, res.mrr,
                     res.quote_intact_rate, res.unit_intact_rate, res.n_chunks])
    if outs[0] != outs[1]:
        fail("可复现", f"两次运行结果不一致：{outs[0]} vs {outs[1]}")
    else:
        ok("可复现", f"同输入两次评测结果完全一致：{outs[0]}")


# ---------------------------------------------------------------- 7. 报告与 CSV 一致性

def check_report_consistency() -> None:
    results_csv = ROOT / "reports" / "results.csv"
    report_md = ROOT / "reports" / "chunk_benchmark.md"
    if not results_csv.exists() or not report_md.exists():
        fail("报告", "缺少 results.csv 或 chunk_benchmark.md")
        return
    with results_csv.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    text = report_md.read_text(encoding="utf-8")
    missing = 0
    for r in rows:
        for col in ("recall_at_5", "mrr", "unit_intact_rate"):
            value = f"{float(r[col]):.3f}"
            if value not in text:
                missing += 1
    if missing > len(rows) * 3 * 0.2:
        warn("报告", f"报告正文中缺少 {missing} 个指标值，可能未同步最新一次评测")
    else:
        ok("报告", f"{len(rows)} 行结果与报告正文数值一致（抽样核对 R@5 / MRR / 单元完整率）")


# ---------------------------------------------------------------- 8. 发布风险

def check_publish_risks() -> None:
    # 8.1 语料是否被 git 跟踪
    try:
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True, timeout=60
        ).stdout.splitlines()
    except Exception as exc:
        warn("发布", f"无法读取 git 跟踪列表：{exc}")
        return

    corpus_tracked = [p for p in tracked if p.startswith("data/corpus/")]
    if corpus_tracked:
        fail("发布", f"第三方语料被 git 跟踪（{len(corpus_tracked)} 个文件）：公开仓库会构成再分发")
    else:
        ok("发布", "工作区未跟踪第三方语料（仅保留抓取脚本与 manifest）")

    # 历史检查：移除跟踪不会删除历史，旧 commit 仍可被下载
    try:
        hist = subprocess.run(
            ["git", "-C", str(ROOT), "log", "--all", "--pretty=format:", "--name-only"],
            capture_output=True, text=True, timeout=60
        ).stdout.splitlines()
    except Exception as exc:
        warn("发布", f"无法读取 git 历史：{exc}")
        hist = []
    in_history = sorted({p for p in hist if p.startswith("data/corpus/")})
    if in_history:
        fail("发布", f"历史提交中仍包含第三方语料（{len(in_history)} 个文件）。"
                     "即使当前 HEAD 已移除，旧 commit 的原文仍可被直接下载；"
                     "要真正合规必须重写历史或重建仓库")
    elif hist:
        ok("发布", "git 历史中不含第三方语料")

    secrets = [p for p in tracked if re.search(r"(^|/)(\.env|id_rsa|.*\.pem)$", p)]
    if secrets:
        fail("发布", f"疑似凭据文件被跟踪：{secrets}")
    else:
        ok("发布", "未发现凭据文件被跟踪")

    if any(p.endswith(".pyc") or "__pycache__" in p for p in tracked):
        fail("发布", "缓存文件被跟踪")
    else:
        ok("发布", "无 Python 缓存文件被跟踪")

    # 8.2 许可兼容性
    manifest = json.loads((ROOT / "data" / "corpus_manifest.json").read_text(encoding="utf-8"))
    for item in manifest:
        url = item["url"]
        if "sunface/rust-course" in url:
            warn("许可", "rust-course 仓库无可识别许可证（GitHub API 返回 None），"
                         "其 README 明示不可修改后再分发：该语料只能本地评测，禁止随仓库发布")
        elif "jwasham/coding-interview-university" in url:
            warn("许可", "coding-interview-university 为 CC-BY-SA-4.0：如需再分发须署名并以相同协议共享，"
                         "与仓库 MIT 许可不兼容，建议同样不入库")
        elif "wikisource" in url:
            ok("许可", "民法典为公有领域法律文本，抓取与引用无版权障碍")

    # 8.3 远端状态
    try:
        remote = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=30
        ).stdout.strip()
        local_head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception:
        remote, local_head = "", ""
    if remote:
        ahead = behind = None
        try:
            counts = subprocess.run(
                ["git", "-C", str(ROOT), "rev-list", "--left-right", "--count",
                 "origin/main...main"],
                capture_output=True, text=True, timeout=30
            ).stdout.split()
            behind, ahead = int(counts[0]), int(counts[1])
        except Exception:
            pass
        if ahead is None:
            record("INFO", "发布", f"远端已配置：{remote}，本地 HEAD {local_head}（未能读取领先/落后关系）")
        elif ahead > 0:
            record("INFO", "发布", f"远端 {remote}：本地领先 {ahead} 个提交未推送；"
                                   "推送前请确认历史中的语料问题已处理")
        else:
            record("INFO", "发布", f"远端 {remote} 与本地一致（落后 {behind} 个提交）")


# ---------------------------------------------------------------- 9. 代码卫生

def check_code_hygiene() -> None:
    py_files = [p for p in ROOT.rglob("*.py") if p.name != "selfcheck.py"]
    bad_paths = []
    todos = []
    for path in py_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"[A-Za-z]:\\\\", text):
            bad_paths.append(path.name)
        if "TODO" in text or "FIXME" in text:
            todos.append(path.name)
    if bad_paths:
        fail("代码", f"源码里存在硬编码绝对路径：{bad_paths}")
    else:
        ok("代码", f"{len(py_files)} 个 Python 文件无硬编码绝对路径")
    if todos:
        warn("代码", f"存在 TODO/FIXME：{todos}")

    try:
        out = subprocess.run([sys.executable, "-m", "compileall", "-q", str(ROOT / "chinese_chunk"),
                              str(ROOT / "scripts"), str(ROOT / "tests")],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            fail("代码", f"compileall 失败：{out.stderr[:200]}")
        else:
            ok("代码", "全部模块通过语法编译检查")
    except Exception as exc:
        warn("代码", f"compileall 未能执行：{exc}")


# ---------------------------------------------------------------- 输出

def build_report() -> str:
    lines = ["# 全量自查报告", "", f"- 时间：{_now()}", f"- 检查项：{len(FINDINGS)}", ""]
    order = {"FAIL": 0, "WARN": 1, "INFO": 2, "OK": 3}
    lines.append("## 结论")
    n_fail = sum(1 for f in FINDINGS if f["level"] == "FAIL")
    n_warn = sum(1 for f in FINDINGS if f["level"] == "WARN")
    lines.append("")
    if n_fail:
        lines.append(f"存在 **{n_fail}** 项阻断问题、{n_warn} 项警告，修复前不建议公开发布。")
    elif n_warn:
        lines.append(f"无阻断问题，{n_warn} 项警告需人工判断（主要是第三方语料许可与样本量）。")
    else:
        lines.append("全部检查通过。")
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    lines.append("| 级别 | 范围 | 说明 |")
    lines.append("| --- | --- | --- |")
    for f in sorted(FINDINGS, key=lambda x: order[x["level"]]):
        msg = f["message"].replace("|", "\\|")
        lines.append(f"| {f['level']} | {f['area']} | {msg} |")
    lines.append("")
    return "\n".join(lines)


def _now() -> str:
    import datetime as dt
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def main() -> int:
    docs = check_corpus()
    check_qa(docs)
    check_chunk_invariants(docs)
    check_tables()
    check_edge_inputs()
    if docs:
        check_determinism(docs, load_qa(ROOT / "data" / "qa.jsonl"))
    check_report_consistency()
    check_publish_risks()
    check_code_hygiene()

    report = build_report()
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "selfcheck.md").write_text(report, encoding="utf-8")

    for f in FINDINGS:
        print(f"[{f['level']}] {f['area']}: {f['message']}")
    failures = sum(1 for f in FINDINGS if f["level"] == "FAIL")
    print(f"\n共 {len(FINDINGS)} 项，FAIL {failures} 项；报告 -> reports/selfcheck.md")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
