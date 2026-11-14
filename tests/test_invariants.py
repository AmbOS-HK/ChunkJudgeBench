"""不变量自检：切片必须满足的可验证条件，防止出现「块内容与原文对不上」这类静默错误。

运行：python tests/test_invariants.py（需要本地 data/corpus/；没有语料时失败是预期）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chinese_chunk.evaluate import _contained, extract_units, load_corpus, load_qa  # noqa: E402
from chinese_chunk.splitters import build_chunker  # noqa: E402
from chinese_chunk.textnorm import normalize, squash  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)


def main() -> int:
    docs = load_corpus(ROOT / "data" / "corpus")
    qa = load_qa(ROOT / "data" / "qa.jsonl")

    for strategy in ("fixed", "sentence", "recursive", "structure"):
        for size in (400, 600, 900):
            chunker = build_chunker(strategy, max_chars=size, overlap=80)
            seen_ids: set[str] = set()
            for doc_id, doc in docs.items():
                chunks = chunker.chunk(doc_id, doc)
                check(bool(chunks), f"{strategy}@{size} {doc_id} 未产出任何块")
                for c in chunks:
                    check(c.text.strip() != "", f"{strategy}@{size} {c.chunk_id} 空块")
                    check(c.chunk_id not in seen_ids, f"{strategy}@{size} 重复 chunk_id：{c.chunk_id}")
                    seen_ids.add(c.chunk_id)
                    # 块必须能在原文定位：正文（去掉标题前缀）应是原文切片的子串
                    body = c.text
                    if c.heading_path and strategy == "structure":
                        prefix = " > ".join(c.heading_path) + "\n"
                        if body.startswith(prefix):
                            body = body[len(prefix):]
                    check(squash(body)[:60] in squash(doc),
                          f"{strategy}@{size} {c.chunk_id} 正文不在原文中")
                    # 硬上限：正文（扣除标题上下文前缀）不得超过 size；
                    # 前缀是附加上下文，不计入正文上限（与切片器 body_limit 预留口径一致）
                    check(len(body) <= size,
                          f"{strategy}@{size} {c.chunk_id} 正文超限：{len(body)} > {size}")

    # 引文必须能从原文定位（评测集自洽）
    for item in qa:
        doc = docs[item["doc"]]
        check(squash(item["gold_quote"]) in squash(doc),
              f"{item['qid']} 引文不在原文中")

    # 对抗回归：空引文不得被判为命中（空串是任意串子串，需显式拒认）
    check(not _contained("", "任意块文本"), "空引文被判定为命中")

    # 对抗回归：未闭合的 HTML 表格 / 代码围栏不得整块丢失
    malformed = {
        "未闭合HTML表格": "<table>\n<tr><td>A</td></tr>",
        "未闭合代码围栏": "```python\nprint(1)\n未收尾围栏",
    }
    for name, text in malformed.items():
        mal_doc = normalize(text)
        for strat in ("fixed", "sentence", "recursive", "structure"):
            chunks = build_chunker(strat, max_chars=300, overlap=50).chunk("adv", mal_doc)
            check(bool(chunks), f"{name}/{strat} 未产出任何块")
            covered = "".join(squash(c.text) for c in chunks)
            check(all(ch in covered for ch in squash(mal_doc)),
                  f"{name}/{strat} 内容丢失")

    # 对抗回归：极端下限不得产生纯空白空块
    for text in ("The quick brown fox. " * 20, "- 项目一\n- 项目二\n" * 30):
        edge_doc = normalize(text)
        for strat in ("fixed", "sentence", "recursive", "structure"):
            chunks = build_chunker(strat, max_chars=1, overlap=0).chunk("adv", edge_doc)
            check(all(c.text.strip() for c in chunks),
                  f"mc=1 {strat} 出现纯空白空块")

    # 结构单元覆盖：structure 策略下所有短单元都应完整落在某一块
    for size in (600,):
        chunker = build_chunker("structure", max_chars=size, overlap=80)
        for doc_id, doc in docs.items():
            chunks = chunker.chunk(doc_id, doc)
            for unit in extract_units(doc):
                if len(squash(unit)) <= size * 0.8:
                    check(any(squash(unit) in squash(c.text) for c in chunks),
                          f"structure@{size} {doc_id} 单元被切断：{unit[:30]}")

    if FAILS:
        print(f"[FAIL] {len(FAILS)} 项")
        for f in FAILS[:20]:
            print("  -", f)
        return 1
    print("[OK] 所有不变量通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
