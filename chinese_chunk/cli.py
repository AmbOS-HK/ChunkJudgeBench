"""命令行入口：切分与评测。

用法：
  python -m chinese_chunk.cli chunk --corpus data/corpus --strategy structure --out out/chunks.jsonl
  python -m chinese_chunk.cli eval  --corpus data/corpus --qa data/qa.jsonl --out-dir reports
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluate import evaluate_strategy, load_corpus, load_qa, write_csv, write_details_csv
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


def _cmd_eval(args: argparse.Namespace) -> int:
    docs = load_corpus(Path(args.corpus))
    qa = load_qa(Path(args.qa))
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for strategy in strategies:
        for size in sizes:
            res, _chunks = evaluate_strategy(
                docs, qa, strategy, max_chars=size, overlap=args.overlap,
                tokenizer=args.tokenizer, top_k=max(5, args.top_k),
            )
            results.append(res)
            print(f"[OK] {strategy:9s} size={size:<4d} chunks={res.n_chunks:<4d} "
                  f"R@1={res.recall_at_1:.3f} R@3={res.recall_at_3:.3f} "
                  f"R@5={res.recall_at_5:.3f} MRR={res.mrr:.3f} "
                  f"intact={res.quote_intact_rate:.3f}")

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
    p_eval.add_argument("--strategies", default="fixed,sentence,recursive,structure")
    p_eval.add_argument("--sizes", default="400,600,900")
    p_eval.add_argument("--overlap", type=int, default=80)
    p_eval.add_argument("--tokenizer", default="char", choices=["char", "cjk", "tiktoken"])
    p_eval.add_argument("--top-k", type=int, default=5)
    p_eval.add_argument("--out-dir", default="reports")
    p_eval.set_defaults(func=_cmd_eval)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
