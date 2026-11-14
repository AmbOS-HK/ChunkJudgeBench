"""离线冒烟：不依赖 data/corpus，用于 CI 与快速回归。

运行：python tests/test_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chinese_chunk  # noqa: E402
from chinese_chunk.evaluate import _contained, extract_units  # noqa: E402
from chinese_chunk.splitters import build_chunker  # noqa: E402
from chinese_chunk.textnorm import normalize, squash  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)


SAMPLE = normalize(
    "# 一级标题\n\n"
    "## 条款\n\n"
    "第一条 为了测试切片行为，这里写一段足够长的中文正文，用来验证块边界是否完整。\n"
    "第二条 第二段继续写下去，包含列表与表格，\n"
    "- 列表项一\n"
    "- 列表项二\n\n"
    "| 列A | 列B |\n| --- | --- |\n| 值1 | 值2 |\n\n"
    "```python\nprint('code block')\n```\n"
)


def main() -> int:
    check(bool(chinese_chunk.__version__), "__version__ 为空")

    for strategy in ("fixed", "sentence", "recursive", "structure"):
        for size in (200, 600):
            chunker = build_chunker(strategy, max_chars=size, overlap=min(80, size // 3))
            chunks = chunker.chunk("sample", SAMPLE)
            check(bool(chunks), f"{strategy}@{size} 无产出")
            ids = [c.chunk_id for c in chunks]
            check(len(ids) == len(set(ids)), f"{strategy}@{size} chunk_id 重复")
            for c in chunks:
                check(c.text.strip() != "", f"{strategy}@{size} 出现空块")
                body = c.text
                if c.heading_path and strategy == "structure":
                    prefix = " > ".join(c.heading_path) + "\n"
                    if body.startswith(prefix):
                        body = body[len(prefix):]
                check(len(body) <= size, f"{strategy}@{size} 正文超限：{len(body)}")
                check(squash(body)[:40] in squash(SAMPLE),
                      f"{strategy}@{size} 正文无法在原文定位")

    # 空引文不得判命中（空串是任意串的子串）
    check(not _contained("", "任意块文本"), "空引文被判定为命中")

    # 畸形输入不得整块丢失
    malformed = "<table>\n<tr><td>A</td></tr>"
    for strat in ("fixed", "sentence", "recursive", "structure"):
        chunks = build_chunker(strat, max_chars=300, overlap=50).chunk("adv", normalize(malformed))
        covered = "".join(squash(c.text) for c in chunks)
        check(all(ch in covered for ch in squash(normalize(malformed))),
              f"未闭合表格/{strat} 内容丢失")

    # structure 的短单元应完整落在某块
    units = extract_units(SAMPLE)
    chunks = build_chunker("structure", max_chars=600, overlap=80).chunk("sample", SAMPLE)
    for unit in units:
        if len(squash(unit)) <= 600 * 0.8:
            check(any(squash(unit) in squash(c.text) for c in chunks),
                  f"单元被切断：{unit[:20]}")

    if FAILS:
        print(f"[FAIL] {len(FAILS)} 项")
        for f in FAILS[:20]:
            print("  -", f)
        return 1
    print("[OK] 离线冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
