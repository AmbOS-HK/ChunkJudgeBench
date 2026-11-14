"""外部切片评测：不依赖 data/corpus。

运行：python tests/test_external_chunks.py

覆盖 load_chunks_jsonl、evaluate_chunks（跳过切分、复用指标），
以及 eval --chunks --label。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chinese_chunk.cli import main  # noqa: E402
from chinese_chunk.evaluate import (  # noqa: E402
    evaluate_chunks,
    evaluate_strategy,
    load_chunks_jsonl,
    load_corpus,
    load_qa,
)
from chinese_chunk.splitters import Chunk  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILS.append(msg)


DOC = (
    "# 总则\n\n"
    "第一条 民事主体从事民事活动，不得违反法律，不得违背公序良俗。\n"
    "第二条 处理民事纠纷，应当依照法律；法律没有规定的，可以适用习惯。\n"
)

QA = [
    {
        "qid": "q1",
        "doc": "sample",
        "question": "民事活动不得违背什么？",
        "gold_quote": "不得违反法律，不得违背公序良俗。",
    }
]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _external_row(chunk: Chunk, *, kind: bool = True) -> dict:
    row = {
        "doc": chunk.doc_id,
        "index": chunk.seq,
        "text": chunk.text,
        "start_offset": chunk.start,
        "end_offset": chunk.end,
        "heading_path": list(chunk.heading_path),
    }
    if kind:
        row["kind"] = chunk.kind
    return row


def test_load_small_loop_fields(tmp: Path) -> None:
    path = tmp / "chunks.jsonl"
    _write_jsonl(path, [
        {
            "doc": "sample",
            "index": 0,
            "text": "第一条 不得违背公序良俗。",
            "start_offset": 0,
            "end_offset": 14,
            "heading_path": ["总则"],
        },
        {
            "doc": "sample",
            "index": 1,
            "text": "第二条 应当依照法律。",
            "start_offset": 15,
            "end_offset": 26,
            "heading_path": "总则 > 附则",
            "note": "ignored",
        },
    ])
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    chunks = load_chunks_jsonl(path)
    check(len(chunks) == 2, f"应读入 2 块，实际 {len(chunks)}")
    if len(chunks) != 2:
        return
    first, second = chunks
    check(first.chunk_id == "sample#0000", f"chunk_id={first.chunk_id}")
    check(first.doc_id == "sample" and first.seq == 0, "首块 doc/index 未还原")
    check(first.text == "第一条 不得违背公序良俗。", "首块正文不符")
    check(first.start == 0 and first.end == 14, f"偏移 {first.start},{first.end}")
    check(first.heading_path == ("总则",), f"标题路径 {first.heading_path}")
    check(first.kind == "text", f"缺省 kind={first.kind}")
    check(second.heading_path == ("总则", "附则"), f"字符串标题路径 {second.heading_path}")
    check(second.chunk_id == "sample#0001", f"次块 chunk_id={second.chunk_id}")
    check(first.tokens > 0 and first.tokens_cjk > 0, "token 计数应被填上")


def test_load_native_aliases(tmp: Path) -> None:
    path = tmp / "native.jsonl"
    _write_jsonl(path, [{
        "chunk_id": "keep-me",
        "doc_id": "sample",
        "seq": 3,
        "text": "表格行",
        "start": 4,
        "end": 7,
        "heading_path": [],
        "kind": "table",
    }])
    chunks = load_chunks_jsonl(path)
    check(len(chunks) == 1, "别名行应读入 1 块")
    if not chunks:
        return
    chunk = chunks[0]
    check(chunk.chunk_id == "keep-me", f"应保留 chunk_id，实际 {chunk.chunk_id}")
    check(chunk.doc_id == "sample" and chunk.seq == 3, "doc_id/seq 别名未还原")
    check(chunk.start == 4 and chunk.end == 7, "start/end 别名未还原")
    check(chunk.heading_path == () and chunk.kind == "table", "空路径或 kind 丢失")


def test_load_rejects_bad_lines(tmp: Path) -> None:
    bad_json = tmp / "bad.jsonl"
    bad_json.write_text('{"doc":"a","index":0,"text":"t","start_offset":0,"end_offset":1,"heading_path":[]}\n{', encoding="utf-8")
    try:
        load_chunks_jsonl(bad_json)
        check(False, "非法 JSON 应失败")
    except ValueError as exc:
        check("2" in str(exc), f"错误应指出行号，实际：{exc}")

    missing = tmp / "missing.jsonl"
    _write_jsonl(missing, [{"doc": "a", "text": "only"}])
    try:
        load_chunks_jsonl(missing)
        check(False, "缺字段应失败")
    except ValueError as exc:
        check("index" in str(exc) or "start_offset" in str(exc), f"错误应指出缺字段，实际：{exc}")

    backwards = tmp / "backwards.jsonl"
    _write_jsonl(backwards, [{
        "doc": "a",
        "index": 0,
        "text": "t",
        "start_offset": 5,
        "end_offset": 1,
        "heading_path": [],
    }])
    try:
        load_chunks_jsonl(backwards)
        check(False, "end < start 应失败")
    except ValueError as exc:
        check("end" in str(exc), f"错误应说明偏移，实际：{exc}")


def test_evaluate_chunks_skips_split_and_matches_strategy(tmp: Path) -> None:
    docs = {"sample": DOC}
    # 一整块覆盖全文：若仍走切分，固定窗口不会只有 1 块。
    whole = Chunk(
        chunk_id="sample#0000",
        doc_id="sample",
        seq=0,
        text=DOC,
        start=0,
        end=len(DOC),
        heading_path=("总则",),
    )
    result = evaluate_chunks(docs, QA, [whole], label="external-whole", max_chars=80, overlap=0)
    check(result.strategy == "external-whole", f"label 未写入 strategy：{result.strategy}")
    check(result.n_chunks == 1, f"不应重新切分，n_chunks={result.n_chunks}")
    check(result.max_chars == 80 and result.overlap == 0, "上限与重叠应沿用调用参数")
    check(result.quote_intact_rate == 1.0, f"整块应保住引文，intact={result.quote_intact_rate}")
    check(result.quote_cut_rate == 0.0, f"quote_cut={result.quote_cut_rate}")
    check(result.recall_at_1 == 1.0, f"单块应召回，R@1={result.recall_at_1}")
    check(result.heading_meta_rate == 1.0, f"heading_meta={result.heading_meta_rate}")

    quote = QA[0]["gold_quote"]
    cut_at = quote.index("不得违背")
    left = Chunk("sample#0000", "sample", 0, quote[:cut_at], 0, cut_at)
    right = Chunk("sample#0001", "sample", 1, quote[cut_at:], cut_at, len(quote))
    cut = evaluate_chunks(docs, QA, [left, right], label="cut", max_chars=80)
    check(cut.n_chunks == 2, "切断用例应保持 2 块")
    check(cut.quote_intact_rate == 0.0, f"切断后 intact={cut.quote_intact_rate}")
    check(cut.quote_cut_rate == 1.0, f"切断后 cut={cut.quote_cut_rate}")
    check(cut.recall_at_5 == 0.0, f"半截引文不应算命中，R@5={cut.recall_at_5}")
    check(0 < cut.mean_best_coverage < 1, f"覆盖率应介于 0 与 1：{cut.mean_best_coverage}")

    for strategy in ("fixed", "structure"):
        built, chunks = evaluate_strategy(
            docs, QA, strategy, max_chars=200, overlap=20,
        )
        path = tmp / f"{strategy}.jsonl"
        _write_jsonl(path, [_external_row(c) for c in chunks])
        loaded = load_chunks_jsonl(path)
        external = evaluate_chunks(
            docs, QA, loaded, label=strategy, max_chars=200, overlap=20,
        )
        check(built.to_row() == external.to_row(),
              f"{strategy} 外部切片与内置策略指标不一致：{built.to_row()} vs {external.to_row()}")
        check(built.details == external.details, f"{strategy} 逐题明细不一致")


def test_infer_max_chars_when_omitted() -> None:
    docs = {"sample": DOC}
    chunk = Chunk("sample#0000", "sample", 0, "短块。", 0, 3)
    result = evaluate_chunks(docs, QA, [chunk])
    check(result.strategy == "external", f"缺省 label={result.strategy}")
    check(result.max_chars == len(chunk.text), f"应按最长块推断上限，实际 {result.max_chars}")
    check(result.overlap == 0, f"缺省 overlap={result.overlap}")
    check(result.n_chunks == 1, "空参数仍应使用传入的块")


def test_cli_default_still_splits(tmp: Path) -> None:
    corpus = tmp / "corpus-default"
    corpus.mkdir()
    (corpus / "sample.md").write_text(DOC, encoding="utf-8")
    qa_path = tmp / "qa-default.jsonl"
    _write_jsonl(qa_path, QA)
    out_dir = tmp / "default-reports"
    code = main([
        "eval",
        "--corpus", str(corpus),
        "--qa", str(qa_path),
        "--strategies", "fixed,sentence",
        "--sizes", "200",
        "--overlap", "20",
        "--out-dir", str(out_dir),
    ])
    check(code == 0, f"默认切分评测应成功，退出码 {code}")
    text = (out_dir / "results.csv").read_text(encoding="utf-8-sig")
    check("fixed" in text and "sentence" in text, "未传 --chunks 时应跑内置策略")


def test_cli_chunks_label(tmp: Path) -> None:
    corpus = tmp / "corpus"
    corpus.mkdir()
    (corpus / "sample.md").write_text(DOC, encoding="utf-8")
    qa_path = tmp / "qa.jsonl"
    _write_jsonl(qa_path, QA)
    docs = load_corpus(corpus)
    _res, chunks = evaluate_strategy(docs, load_qa(qa_path), "fixed", max_chars=180, overlap=10)
    chunks_path = tmp / "external.jsonl"
    _write_jsonl(chunks_path, [_external_row(c) for c in chunks])
    out_dir = tmp / "reports"

    code = main([
        "eval",
        "--corpus", str(corpus),
        "--qa", str(qa_path),
        "--chunks", str(chunks_path),
        "--label", "aec-template-v1",
        "--max-chars", "180",
        "--overlap", "10",
        "--out-dir", str(out_dir),
    ])
    check(code == 0, f"CLI 应成功，退出码 {code}")
    csv_text = (out_dir / "results.csv").read_text(encoding="utf-8-sig")
    rows = [line for line in csv_text.splitlines() if line.strip()]
    check(len(rows) == 2, f"外部评测应只有 1 条结果，实际 {len(rows) - 1} 条")
    check("aec-template-v1" in csv_text, "结果表应使用 --label")
    check("fixed" not in csv_text.splitlines()[1], "不应再跑内置策略网格")
    report = (out_dir / "chunk_benchmark.md").read_text(encoding="utf-8")
    check("aec-template-v1" in report, "报告应写出外部标签")
    check((out_dir / "per_question.csv").exists(), "应写出逐题明细")

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        missing_label = main([
            "eval",
            "--corpus", str(corpus),
            "--qa", str(qa_path),
            "--chunks", str(chunks_path),
            "--out-dir", str(tmp / "no-label"),
        ])
    check(missing_label != 0, "只有 --chunks 没有 --label 应失败")
    check("label" in err.getvalue(), f"应提示缺少 --label，实际：{err.getvalue()}")

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        bad = main([
            "eval",
            "--corpus", str(corpus),
            "--qa", str(qa_path),
            "--chunks", str(tmp / "no-such-chunks.jsonl"),
            "--label", "missing",
            "--out-dir", str(tmp / "missing-out"),
        ])
    check(bad != 0, "切片文件不存在应失败")
    check("找不到" in err.getvalue(), f"应提示文件不存在，实际：{err.getvalue()}")


def main_test() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_load_small_loop_fields(tmp)
        test_load_native_aliases(tmp)
        test_load_rejects_bad_lines(tmp)
        test_evaluate_chunks_skips_split_and_matches_strategy(tmp)
        test_infer_max_chars_when_omitted()
        test_cli_default_still_splits(tmp)
        test_cli_chunks_label(tmp)
    if FAILS:
        print(f"[FAIL] {len(FAILS)} 项")
        for item in FAILS:
            print("  -", item)
        return 1
    print("[OK] 外部切片评测通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_test())
