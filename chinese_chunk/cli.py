"""命令行入口：切分与评测。

用法：
  python -m chinese_chunk.cli chunk --corpus data/corpus --strategy structure --out out/chunks.jsonl
  python -m chinese_chunk.cli eval  --corpus data/corpus --qa data/qa.jsonl --out-dir reports
  python -m chinese_chunk.cli eval  --corpus data/corpus --qa data/qa.jsonl \\
      --chunks out/aec_batch.jsonl --label aec-template-v1 --max-chars 1200 --overlap 0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluate import (
    evaluate_chunks, evaluate_strategy, load_chunks_jsonl, load_corpus, load_qa,
    write_csv, write_details_csv,
)
from .report import build_markdown_report
from .splitters import build_chunker


def _cmd_chunk(args: argparse.Namespace) -> int:
    docs = load_corpus(Path(args.corpus))
    chunker = build_chunker(
        args.strategy, max_chars=args.max_chars, overlap=args.overlap,
        tokenizer=args.tokenizer,
        **({"context_prefix": not args.no_context, "with_parent": not args.no_parent}
           if args.strategy == "structure" else {}),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with out.open("w", encoding="utf-8") as fh:
        for doc_id, text in docs.items():
            chunks = chunker.chunk(doc_id, text)
            total += len(chunks)
            for c in chunks:
                fh.write(json.dumps(c.to_dict(with_parent=not args.no_parent), ensure_ascii=False) + "\n")
    print(f"[OK] {args.strategy}: {total} 块 -> {out}")
    return 0


def _print_result(res) -> None:
    print(f"[OK] {res.strategy:9s} size={res.max_chars:<4d} chunks={res.n_chunks:<4d} "
          f"R@1={res.recall_at_1:.3f} R@3={res.recall_at_3:.3f} "
          f"R@5={res.recall_at_5:.3f} MRR={res.mrr:.3f} "
          f"intact={res.quote_intact_rate:.3f}")


def _cmd_eval(args: argparse.Namespace) -> int:
    docs = load_corpus(Path(args.corpus))
    qa = load_qa(Path(args.qa))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.chunks:
        if not args.label:
            print("[FAIL] eval --chunks 需要同时给出 --label", file=sys.stderr)
            return 2
        try:
            chunks = load_chunks_jsonl(Path(args.chunks))
        except FileNotFoundError:
            print(f"[FAIL] 找不到切片文件 {args.chunks}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        res = evaluate_chunks(
            docs, qa, chunks,
            label=args.label,
            max_chars=args.max_chars,
            overlap=args.overlap,
            top_k=max(5, args.top_k),
        )
        results = [res]
        _print_result(res)
    else:
        if args.label or args.max_chars is not None:
            print("[FAIL] --label 与 --max-chars 只在传入 --chunks 时使用", file=sys.stderr)
            return 2
        strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
        sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
        results = []
        for strategy in strategies:
            for size in sizes:
                res, _chunks = evaluate_strategy(
                    docs, qa, strategy, max_chars=size, overlap=args.overlap,
                    tokenizer=args.tokenizer, top_k=max(5, args.top_k),
                )
                results.append(res)
                _print_result(res)

    write_csv(results, out_dir / "results.csv")
    write_details_csv(results, out_dir / "per_question.csv")
    report = build_markdown_report(results, qa, docs, overlap=args.overlap)
    (out_dir / "chunk_benchmark.md").write_text(report, encoding="utf-8")
    print(f"[OK] 报告 -> {out_dir / 'chunk_benchmark.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chinese_chunk")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_chunk = sub.add_parser("chunk", help="对语料执行切分并导出 JSONL")
    p_chunk.add_argument("--corpus", default="data/corpus")
    p_chunk.add_argument("--strategy", default="structure",
                         choices=["fixed", "sentence", "recursive", "structure"])
    p_chunk.add_argument("--max-chars", type=int, default=600)
    p_chunk.add_argument("--overlap", type=int, default=80)
    p_chunk.add_argument("--tokenizer", default="char", choices=["char", "cjk", "tiktoken"])
    p_chunk.add_argument("--out", default="out/chunks.jsonl")
    p_chunk.add_argument("--no-context", action="store_true", help="不把标题路径写进块文本")
    p_chunk.add_argument("--no-parent", action="store_true", help="不输出父子块的父文本")
    p_chunk.set_defaults(func=_cmd_chunk)

    p_eval = sub.add_parser("eval", help="多策略、多尺寸评测并生成报告")
    p_eval.add_argument("--corpus", default="data/corpus")
    p_eval.add_argument("--qa", default="data/qa.jsonl")
    p_eval.add_argument("--strategies", default="fixed,sentence,recursive,structure",
                        help="逗号分隔的内置策略；传入 --chunks 时忽略")
    p_eval.add_argument("--sizes", default="400,600,900",
                        help="内置策略的字符上限；传入 --chunks 时忽略")
    p_eval.add_argument("--chunks", default=None,
                        help="已切好的 JSONL（doc/index/text/start_offset/end_offset/heading_path），跳过内置切分")
    p_eval.add_argument("--label", default=None,
                        help="外部切片在报告中的名称，须与 --chunks 同用")
    p_eval.add_argument("--max-chars", type=int, default=None,
                        help="外部切片的字符上限，只用于超限率与单元完整率；缺省取最长块")
    p_eval.add_argument("--overlap", type=int, default=80,
                        help="重叠字符数。外部切片只把该值写入报告，不重新切分")
    p_eval.add_argument("--tokenizer", default="char", choices=["char", "cjk", "tiktoken"])
    p_eval.add_argument("--top-k", type=int, default=5)
    p_eval.add_argument("--out-dir", default="reports")
    p_eval.set_defaults(func=_cmd_eval)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
