"""切片质量评测：召回、排名、切碎率、边界完整率、块大小分布。

评测口径说明（报告里会原样写出，避免误读）：
- 检索器固定为字符二元组 BM25，k1=1.5 / b=0.75，所有策略共用；
- 只改变切片方式，其余环节完全一致，因此指标差异归因于切片；
- 命中判定 = gold_quote 去掉空白后，完整出现在某个已召回块中；
- 「切碎」= gold_quote 被拆到两个及以上的块里，任何单块都无法完整覆盖；
- 指标不是「大模型回答正确率」，而是「检索能否拿到完整证据」。
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import statistics
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Sequence

from .retrieval import BM25Index
from .splitters import Chunk, build_chunker
from .textnorm import count_tokens, normalize, squash

_SENT_TAIL = "。！？；…!?;"
_CLOSERS = "”」』）)》】\"'"


def load_corpus(corpus_dir: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for path in sorted(corpus_dir.glob("*.md")):
        docs[path.stem] = normalize(path.read_text(encoding="utf-8"))
    return docs


def load_qa(qa_path: Path) -> list[dict]:
    items = []
    for line in qa_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def load_chunks_jsonl(path: Path | str) -> list[Chunk]:
    """从外部 JSONL 还原 Chunk，不重新切分。

    每行一个对象，字段为 doc、index、text、start_offset、end_offset、heading_path。
    heading_path 可以是字符串数组，或用「 > 」连接的字符串。
    也接受本仓库 chunk 导出的别名：doc_id、seq、start、end，以及可选的 chunk_id、kind。
    """
    path = Path(path)
    chunks: list[Chunk] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 第 {lineno} 行：不是合法 JSON（{exc.msg}）") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"{path} 第 {lineno} 行：必须是 JSON 对象")
        chunks.append(_chunk_from_record(obj, path, lineno))
    return chunks


def _require(obj: dict, names: tuple[str, ...], path: Path, lineno: int):
    for name in names:
        if name in obj and obj[name] is not None:
            return obj[name]
    raise ValueError(f"{path} 第 {lineno} 行：缺少 {' / '.join(names)}")


def _as_int(value, name: str, path: Path, lineno: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} 第 {lineno} 行：{name} 必须是整数")
    return value


def _as_str(value, name: str, path: Path, lineno: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} 第 {lineno} 行：{name} 必须是字符串")
    return value


def _heading_path(value, path: Path, lineno: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        if " > " in text:
            return tuple(part.strip() for part in text.split(" > ") if part.strip())
        return (text,)
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{path} 第 {lineno} 行：heading_path 的每一项必须是字符串")
            parts.append(item)
        return tuple(parts)
    raise ValueError(f"{path} 第 {lineno} 行：heading_path 必须是字符串或字符串数组")


def _chunk_from_record(obj: dict, path: Path, lineno: int) -> Chunk:
    doc_id = _as_str(_require(obj, ("doc", "doc_id"), path, lineno), "doc", path, lineno)
    if not doc_id.strip():
        raise ValueError(f"{path} 第 {lineno} 行：doc 不能为空")
    text = _as_str(_require(obj, ("text",), path, lineno), "text", path, lineno)
    seq = _as_int(_require(obj, ("index", "seq"), path, lineno), "index", path, lineno)
    if seq < 0:
        raise ValueError(f"{path} 第 {lineno} 行：index 不能为负")
    start = _as_int(
        _require(obj, ("start_offset", "start"), path, lineno), "start_offset", path, lineno,
    )
    end = _as_int(
        _require(obj, ("end_offset", "end"), path, lineno), "end_offset", path, lineno,
    )
    if start < 0 or end < start:
        raise ValueError(f"{path} 第 {lineno} 行：end_offset 必须大于等于 start_offset，且不能为负")
    heading = _heading_path(obj["heading_path"], path, lineno) if "heading_path" in obj else ()
    kind = "text"
    if obj.get("kind") is not None:
        kind = _as_str(obj["kind"], "kind", path, lineno)
    chunk_id = f"{doc_id}#{seq:04d}"
    if obj.get("chunk_id"):
        chunk_id = _as_str(obj["chunk_id"], "chunk_id", path, lineno)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        seq=seq,
        text=text,
        start=start,
        end=end,
        kind=kind,
        heading_path=heading,
        article_range=_as_str(obj["article_range"], "article_range", path, lineno) if obj.get("article_range") else "",
        parent_id=_as_str(obj["parent_id"], "parent_id", path, lineno) if obj.get("parent_id") else "",
        parent_text=_as_str(obj["parent_text"], "parent_text", path, lineno) if obj.get("parent_text") else "",
        tokens=count_tokens(text, "char"),
        tokens_cjk=count_tokens(text, "cjk"),
    )


def _contained(quote: str, chunk_text: str) -> bool:
    q = squash(quote)
    return bool(q) and q in squash(chunk_text)


def _best_coverage(quote: str, chunks: Sequence[Chunk]) -> float:
    """quote 在单个块内被连续覆盖的最大比例（0~1）。"""
    q = squash(quote)
    if not q:
        return 0.0
    best = 0
    for chunk in chunks:
        c = squash(chunk.text)
        if len(c) < 8:
            continue
        m = difflib.SequenceMatcher(None, q, c, autojunk=False)
        block = m.find_longest_match(0, len(q), 0, len(c))
        best = max(best, block.size)
        if best >= len(q):
            break
    return best / len(q)


def _boundary_intact(chunk: Chunk) -> bool:
    if chunk.kind in ("table", "code"):
        return True
    text = chunk.text.rstrip()
    if not text:
        return False
    if text[-1] in _SENT_TAIL:
        return True
    if text[-1] in _CLOSERS and len(text) > 1 and text[-2] in _SENT_TAIL:
        return True
    return False


_ARTICLE_HEAD = re.compile(r"^第[一二三四五六七八九十百千零]+条")
_HEAD_LINE = re.compile(r"^(#{1,6}\s|第[一二三四五六七八九十百千零]+[编分章节])")


def extract_units(doc: str, min_len: int = 40) -> list[str]:
    """抽取文档的结构单元：法条按「第X条」整条；无条号文档按空行分段。

    这个指标与检索无关，直接回答「切完之后，一个完整语义单元还能不能完整拿到」，
    样本量远大于人工标注的问答集，比召回率更能稳定反映切片质量。
    """
    lines = doc.split("\n")
    if any(_ARTICLE_HEAD.match(ln) for ln in lines):
        units, buf = [], []
        for ln in lines:
            if _ARTICLE_HEAD.match(ln) or _HEAD_LINE.match(ln):
                if buf:
                    units.append("\n".join(buf).strip())
                buf = [ln] if _ARTICLE_HEAD.match(ln) else []
                continue
            if not ln.strip() and buf:
                units.append("\n".join(buf).strip())
                buf = []
            elif buf:
                buf.append(ln)
        if buf:
            units.append("\n".join(buf).strip())
    else:
        units = [ln.strip() for ln in re.split(r"\n\s*\n", doc) if ln.strip()]

    out = []
    for u in units:
        u = re.sub(r"\n{2,}", "\n", u).strip()
        if len(squash(u)) >= min_len and not u.startswith("#"):
            out.append(u)
    return out


@dataclass
class StrategyResult:
    strategy: str
    max_chars: int
    overlap: int
    n_chunks: int
    chars_per_chunk_p50: float
    chars_per_chunk_p95: float
    chars_per_chunk_max: int
    over_limit_rate: float
    boundary_intact_rate: float
    redundancy_rate: float          # 所有块字符总量 / 原文总量 - 1，衡量重叠与重复
    heading_meta_rate: float        # 带 heading_path 的块占比
    unit_count: int                 # 参与统计的结构单元数
    unit_intact_rate: float         # 结构单元能被单个块完整覆盖的比例
    quote_intact_rate: float        # gold_quote 完整落在某一块内的比例
    quote_cut_rate: float           # gold_quote 被切断的比例（1 - intact）
    mean_best_coverage: float       # 单块对 quote 的最大覆盖比例均值
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    details: list[dict] = field(default_factory=list)

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("details")
        return d


def evaluate_strategy(
    docs: dict[str, str],
    qa: Sequence[dict],
    strategy: str,
    *,
    max_chars: int,
    overlap: int,
    tokenizer: str = "char",
    top_k: int = 5,
    chunker_kwargs: dict | None = None,
) -> tuple[StrategyResult, list[Chunk]]:
    chunker = build_chunker(
        strategy, max_chars=max_chars, overlap=overlap, tokenizer=tokenizer,
        **(chunker_kwargs or {}),
    )
    all_chunks: list[Chunk] = []
    for doc_id, text in docs.items():
        all_chunks.extend(chunker.chunk(doc_id, text))
    res = _score_chunks(
        docs, qa, all_chunks,
        strategy=strategy, max_chars=max_chars, overlap=overlap, top_k=top_k,
    )
    return res, all_chunks


def evaluate_chunks(
    docs: dict[str, str],
    qa: Sequence[dict],
    chunks: Sequence[Chunk],
    *,
    label: str = "external",
    max_chars: int | None = None,
    overlap: int = 0,
    top_k: int = 5,
) -> StrategyResult:
    """用已有切片计算与 evaluate_strategy 相同的指标，不调用切分器。"""
    all_chunks = list(chunks)
    if max_chars is None:
        max_chars = max((len(c.text) for c in all_chunks), default=0)
    return _score_chunks(
        docs, qa, all_chunks,
        strategy=label, max_chars=max_chars, overlap=overlap, top_k=top_k,
    )


def _score_chunks(
    docs: dict[str, str],
    qa: Sequence[dict],
    all_chunks: Sequence[Chunk],
    *,
    strategy: str,
    max_chars: int,
    overlap: int,
    top_k: int,
) -> StrategyResult:
    all_chunks = list(all_chunks)
    index = BM25Index(all_chunks)

    intact = 0
    cut = 0
    coverages: list[float] = []
    r1 = r3 = r5 = 0
    rr_sum = 0.0
    details: list[dict] = []

    for item in qa:
        quote = item["gold_quote"]
        hits = index.search(item["question"], top_k=top_k)
        # 只统计该题所属文档的块，避免跨文档假命中
        ranks = [i + 1 for i, h in enumerate(hits) if h.chunk.doc_id == item["doc"]]
        hit_rank = next((i + 1 for i, h in enumerate(hits)
                         if h.chunk.doc_id == item["doc"] and _contained(quote, h.chunk.text)), None)

        doc_chunks = [c for c in all_chunks if c.doc_id == item["doc"]]
        full = any(_contained(quote, c.text) for c in doc_chunks)
        intact += int(full)
        cut += int(not full)
        cov = _best_coverage(quote, doc_chunks)
        coverages.append(cov)

        r1 += int(hit_rank is not None and hit_rank <= 1)
        r3 += int(hit_rank is not None and hit_rank <= 3)
        r5 += int(hit_rank is not None and hit_rank <= 5)
        rr_sum += (1.0 / hit_rank) if hit_rank else 0.0

        details.append({
            "qid": item["qid"],
            "doc": item["doc"],
            "hit_rank": hit_rank,
            "quote_intact": full,
            "best_coverage": round(cov, 4),
            "top1_chunk_id": hits[0].chunk.chunk_id if hits else "",
        })

    sizes = sorted(len(c.text) for c in all_chunks)
    total_chars = sum(len(t) for t in docs.values())
    over = sum(1 for c in all_chunks if len(c.text) > max_chars + 40)  # 允许结构前缀带来的少量超出

    # 结构单元完整率：只统计长度不超过上限 80% 的单元（超长单元必然要二次切分，不计入）
    units_all: list[tuple[str, str]] = []
    for doc_id, text in docs.items():
        for u in extract_units(text):
            units_all.append((doc_id, u))
    eligible = [(d, u) for d, u in units_all if len(squash(u)) <= max_chars * 0.8]
    intact_units = 0
    for doc_id, unit in eligible:
        q = squash(unit)
        if any(c.doc_id == doc_id and q in squash(c.text) for c in all_chunks):
            intact_units += 1

    res = StrategyResult(
        strategy=strategy,
        max_chars=max_chars,
        overlap=overlap,
        n_chunks=len(all_chunks),
        chars_per_chunk_p50=round(statistics.median(sizes), 1) if sizes else 0.0,
        chars_per_chunk_p95=round(_percentile(sizes, 0.95), 1) if sizes else 0.0,
        chars_per_chunk_max=sizes[-1] if sizes else 0,
        over_limit_rate=round(over / len(all_chunks), 4) if all_chunks else 0.0,
        boundary_intact_rate=round(sum(_boundary_intact(c) for c in all_chunks) / len(all_chunks), 4) if all_chunks else 0.0,
        redundancy_rate=round((sum(sizes) / total_chars - 1), 4) if total_chars else 0.0,
        heading_meta_rate=round(sum(1 for c in all_chunks if c.heading_path) / len(all_chunks), 4) if all_chunks else 0.0,
        unit_count=len(eligible),
        unit_intact_rate=round(intact_units / len(eligible), 4) if eligible else 0.0,
        quote_intact_rate=round(intact / len(qa), 4) if qa else 0.0,
        quote_cut_rate=round(cut / len(qa), 4) if qa else 0.0,
        mean_best_coverage=round(sum(coverages) / len(coverages), 4) if coverages else 0.0,
        recall_at_1=round(r1 / len(qa), 4) if qa else 0.0,
        recall_at_3=round(r3 / len(qa), 4) if qa else 0.0,
        recall_at_5=round(r5 / len(qa), 4) if qa else 0.0,
        mrr=round(rr_sum / len(qa), 4) if qa else 0.0,
        details=details,
    )
    return res


def _percentile(sorted_values: Sequence[int], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def write_csv(results: Sequence[StrategyResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r.to_row() for r in results]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_details_csv(results: Sequence[StrategyResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["strategy", "max_chars", "qid", "doc", "hit_rank",
                         "quote_intact", "best_coverage", "top1_chunk_id"])
        for r in results:
            for d in r.details:
                writer.writerow([r.strategy, r.max_chars, d["qid"], d["doc"],
                                 d["hit_rank"], d["quote_intact"],
                                 d["best_coverage"], d["top1_chunk_id"]])
