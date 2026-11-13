"""四种中文切片策略，统一输出 Chunk 结构，便于同口径评测。

策略：
1. fixed        固定字符窗口 + 重叠（最弱基线，只保证不超长）
2. sentence     按句打包（中文常见基线，句边界完整但不看文档结构）
3. recursive    递归字符降级（段落→句末标点→分句标点→字符），LangChain 思路的中文版
4. structure    结构优先 + 字符兜底：识别标题层级/条款/表格/代码，结构边界内打包，
                超长结构单元再按句→分句→字符二次切分，保留 heading_path 与条款区间

所有策略都受 max_chars 约束；评测时使用同一检索器，只有切片不同，保证可比。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Iterable, Sequence

from .textnorm import (
    count_tokens, normalize, split_clauses, split_sentences, squash, tail_overlap,
)

# ---------------------------------------------------------------- 数据结构


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    seq: int
    text: str
    start: int
    end: int
    kind: str = "text"                # text / table / code / heading_orphan
    heading_path: tuple[str, ...] = ()
    article_range: str = ""           # 如 "第三百九十五条-第三百九十七条"
    parent_id: str = ""
    parent_text: str = ""
    tokens: int = 0
    tokens_cjk: int = 0

    def to_dict(self, with_parent: bool = True) -> dict:
        d = asdict(self)
        d["heading_path"] = list(self.heading_path)
        if not with_parent:
            d.pop("parent_text", None)
        return d


def _finalize(
    doc_id: str,
    seq: int,
    text: str,
    start: int,
    end: int,
    *,
    kind: str,
    heading_path: Sequence[str],
    article_range: str,
    tokenizer: str,
    parent_id: str = "",
    parent_text: str = "",
) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}#{seq:04d}",
        doc_id=doc_id,
        seq=seq,
        text=text,
        start=start,
        end=end,
        kind=kind,
        heading_path=tuple(heading_path),
        article_range=article_range,
        parent_id=parent_id,
        parent_text=parent_text,
        tokens=count_tokens(text, tokenizer),
        tokens_cjk=count_tokens(text, "cjk"),
    )


class BaseChunker:
    name = "base"

    def __init__(self, max_chars: int = 600, overlap: int = 80, tokenizer: str = "char") -> None:
        if max_chars <= 0:
            raise ValueError("max_chars 必须为正")
        if overlap >= max_chars:
            raise ValueError("overlap 必须小于 max_chars")
        self.max_chars = max_chars
        self.overlap = overlap
        self.tokenizer = tokenizer

    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        raise NotImplementedError

    # 通用：超长文本按 行→句→分句→字符 四级降级切分
    def _split_long(self, text: str, limit: int) -> list[str]:
        """先按行切（保留列表/款项的原始行边界），单行超长再降级到句、分句、字符。

        先切行的理由：README、条款款项这类文本，行本身就是语义单位；若一上来按句切，
        会把一个列表项从中间劈开，块首尾出现半截链接或半截编号，检索时会引入噪声。
        """
        pieces: list[str] = []
        for line in text.split("\n"):
            if len(line) <= limit:
                pieces.append(line)
                continue
            for sent in split_sentences(line):
                if len(sent) <= limit:
                    pieces.append(sent)
                    continue
                for clause in split_clauses(sent):
                    if len(clause) <= limit:
                        pieces.append(clause)
                        continue
                    for i in range(0, len(clause), limit):
                        pieces.append(clause[i:i + limit])
        return self._pack(pieces, limit, joiner="\n")

    def _pack(self, pieces: Iterable[str], limit: int, joiner: str = "") -> list[str]:
        """把片段打包到 limit 以内；片段之间默认直接拼接（中文无需空格）。"""
        out: list[str] = []
        buf = ""
        for p in pieces:
            if not p:
                continue
            if len(buf) + len(joiner) + len(p) <= limit:
                buf = p if not buf else buf + joiner + p
            else:
                if buf:
                    out.append(buf)
                buf = p
        if buf:
            out.append(buf)
        return out


# ---------------------------------------------------------------- 1. 固定窗口


class FixedSizeChunker(BaseChunker):
    name = "fixed"

    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        doc = normalize(text)
        chunks: list[Chunk] = []
        start = 0
        seq = 0
        step = self.max_chars - self.overlap
        while start < len(doc):
            end = min(start + self.max_chars, len(doc))
            piece = doc[start:end]
            if piece.strip():
                chunks.append(_finalize(
                    doc_id, seq, piece, start, end,
                    kind="text", heading_path=(), article_range="",
                    tokenizer=self.tokenizer,
                ))
                seq += 1
            if end >= len(doc):
                break
            start += step
        return chunks


# ---------------------------------------------------------------- 2. 按句打包


class SentenceChunker(BaseChunker):
    name = "sentence"

    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        doc = normalize(text)
        chunks: list[Chunk] = []
        # 记录句子在原文中的位置，便于回填 start/end
        sentences: list[tuple[str, int, int]] = []
        pos = 0
        for para in doc.split("\n"):
            if not para.strip():
                pos += len(para) + 1
                continue
            for sent in split_sentences(para):
                idx = doc.find(sent, pos)
                if idx < 0:
                    idx = pos
                sentences.append((sent, idx, idx + len(sent)))
                pos = idx + len(sent)
            pos += 1

        buf: list[tuple[str, int, int]] = []

        def flush() -> None:
            nonlocal buf
            if not buf:
                return
            chunks.append(_finalize(
                doc_id, len(chunks), doc[buf[0][1]:buf[-1][2]], buf[0][1], buf[-1][2],
                kind="text", heading_path=(), article_range="",
                tokenizer=self.tokenizer,
            ))
            buf = []

        for sent, s, e in sentences:
            if len(sent) > self.max_chars:
                flush()
                for piece in self._split_long(sent, self.max_chars):
                    idx = doc.find(piece, s)
                    idx = s if idx < 0 else idx
                    chunks.append(_finalize(
                        doc_id, len(chunks), piece, idx, idx + len(piece),
                        kind="text", heading_path=(), article_range="",
                        tokenizer=self.tokenizer,
                    ))
                continue
            # 按原文切片的实际跨度复核（句间换行也占长度），避免打包后溢出
            if buf and e - buf[0][1] > self.max_chars:
                flush()
            buf.append((sent, s, e))
        flush()
        return chunks


# ---------------------------------------------------------------- 3. 递归字符


_RECURSIVE_SEPS = [
    r"\n\n", r"\n", r"。|！|？|!|\?", r"；|;", r"，|,|、|：|:", "",
]


class RecursiveCharChunker(BaseChunker):
    name = "recursive"

    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        doc = normalize(text)
        pieces = self._split_recursive(doc, self.max_chars)
        chunks: list[Chunk] = []
        pos = 0
        for piece in pieces:
            idx = doc.find(piece, pos)
            idx = pos if idx < 0 else idx
            chunks.append(_finalize(
                doc_id, len(chunks), piece, idx, idx + len(piece),
                kind="text", heading_path=(), article_range="",
                tokenizer=self.tokenizer,
            ))
            pos = max(idx + len(piece) - self.overlap, idx + 1)
        return chunks

    def _split_recursive(self, text: str, limit: int, sep_index: int = 0) -> list[str]:
        if len(text) <= limit:
            return [text.strip()] if text.strip() else []
        if sep_index >= len(_RECURSIVE_SEPS) or _RECURSIVE_SEPS[sep_index] == "":
            return self._hard_split(text, limit)

        sep = _RECURSIVE_SEPS[sep_index]
        parts = self._split_keep_sep(text, sep)
        if len(parts) <= 1:
            return self._split_recursive(text, limit, sep_index + 1)

        out: list[str] = []
        for part in parts:
            if len(part) <= limit:
                out.append(part)
            else:
                out.extend(self._split_recursive(part, limit, sep_index + 1))
        return self._pack(out, limit)

    @staticmethod
    def _split_keep_sep(text: str, sep: str) -> list[str]:
        pattern = f"({sep})"
        parts = re.split(pattern, text, flags=re.M)
        merged: list[str] = []
        i = 0
        while i < len(parts):
            cur = parts[i]
            if i + 1 < len(parts) and re.fullmatch(sep, parts[i + 1], flags=re.M):
                cur = cur + parts[i + 1]
                i += 2
            else:
                i += 1
            if cur.strip():
                merged.append(cur)
        return merged

    def _hard_split(self, text: str, limit: int) -> list[str]:
        out = []
        step = max(1, limit - self.overlap)
        for i in range(0, len(text), step):
            piece = text[i:i + limit].strip()
            if piece:
                out.append(piece)
            if i + limit >= len(text):
                break
        return out


# ---------------------------------------------------------------- 4. 结构优先


_HEADING_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HEADING_LAW = re.compile(r"^第([一二三四五六七八九十百千零]+)(编|分编|章|节)\s*(.*)$")
_ARTICLE = re.compile(r"^第([一二三四五六七八九十百千零]+)条")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_FENCE = re.compile(r"^\s*```")

_LAW_LEVEL = {"编": 1, "分编": 2, "章": 3, "节": 4}


@dataclass
class _Unit:
    """结构解析后的最小块单元。"""
    text: str
    start: int
    end: int
    kind: str                      # text / table / code
    heading_path: tuple[str, ...]
    article_no: str
    section_level: int


@dataclass
class _Section:
    heading_path: tuple[str, ...]
    level: int
    units: list[_Unit] = field(default_factory=list)


class StructureFirstChunker(BaseChunker):
    """结构优先、字符兜底。

    规则：
    - 标题（Markdown #、第X编/分编/章/节）建立 section，section 内才允许合并；
    - 每条「第X条」作为不得跨越的强边界，但同节内可把多条打包到上限；
    - 表格与代码块作为原子块，不参与合并，超长时按行切并重复表头；
    - 单个结构单元超过上限时，按句→分句→字符三级降级（带重叠）；
    - 保留 heading_path、条款区间、父子块（父 = 完整 section 文本）。
    """

    name = "structure"

    def __init__(self, max_chars: int = 600, overlap: int = 80,
                 tokenizer: str = "char", context_prefix: bool = True,
                 with_parent: bool = True) -> None:
        super().__init__(max_chars, overlap, tokenizer)
        self.context_prefix = context_prefix
        self.with_parent = with_parent

    # ---- 解析
    def _parse(self, doc: str) -> list[_Section]:
        sections: list[_Section] = []
        heading_stack: list[tuple[int, str]] = []   # (level, title)
        cur = _Section(heading_path=(), level=0)
        sections.append(cur)

        buf_lines: list[str] = []
        buf_start = 0
        article_no = ""

        def path() -> tuple[str, ...]:
            return tuple(t for _, t in heading_stack)

        def flush_body(end_offset: int) -> None:
            nonlocal buf_lines, buf_start
            # 必须用 "\n" 连接：原文中行与行之间就是换行符，否则计算出的偏移会比原文短，
            # 后续按偏移切片就会把句子截断（这个坑已经踩过一次）。
            raw = "\n".join(buf_lines)
            if raw.strip():
                # 段落内按空行再拆，保证单元不过粗
                for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", raw):
                    seg = m.group(0)
                    off = buf_start + m.start()
                    cur.units.append(_Unit(
                        text=seg.strip(),
                        start=off,
                        end=off + len(seg),
                        kind="text",
                        heading_path=path(),
                        article_no=article_no,
                        section_level=len(heading_stack),
                    ))
            buf_lines = []
            buf_start = end_offset

        offset = 0
        in_fence = False
        fence_buf: list[str] = []
        fence_start = 0
        table_buf: list[str] = []
        table_start = 0
        in_html_table = False
        html_buf: list[str] = []
        html_start = 0

        for line in doc.split("\n"):
            line_start = offset
            offset += len(line) + 1

            # HTML 表格（README 常见 <table> 块）作为原子单元
            if in_html_table:
                html_buf.append(line)
                if "</table>" in line.lower():
                    in_html_table = False
                    cur.units.append(_Unit("\n".join(html_buf), html_start, offset,
                                           "table", path(), article_no, len(heading_stack)))
                    html_buf = []
                continue
            if re.match(r"^\s*<table", line, re.I):
                flush_body(line_start)
                in_html_table = True
                html_buf = [line]
                html_start = line_start
                continue

            if in_fence:
                fence_buf.append(line)
                if _FENCE.match(line):
                    in_fence = False
                    text = "\n".join(fence_buf)
                    cur.units.append(_Unit(text, fence_start, offset, "code", path(), article_no, len(heading_stack)))
                    fence_buf = []
                    buf_start = offset
                continue
            if _FENCE.match(line):
                flush_body(line_start)
                in_fence = True
                fence_buf = [line]
                fence_start = line_start
                continue

            if _TABLE_ROW.match(line):
                if not table_buf:
                    flush_body(line_start)
                    table_start = line_start
                table_buf.append(line)
                continue
            elif table_buf:
                text = "\n".join(table_buf)
                cur.units.append(_Unit(text, table_start, offset, "table", path(), article_no, len(heading_stack)))
                table_buf = []
                buf_start = offset

            md = _HEADING_MD.match(line)
            law = _HEADING_LAW.match(line)
            if md or law:
                flush_body(line_start)
                if md:
                    level = len(md.group(1))
                    title = md.group(2).strip()
                else:
                    level = _LAW_LEVEL[law.group(2)]
                    title = line.strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                cur = _Section(heading_path=path(), level=len(heading_stack))
                sections.append(cur)
                article_no = ""
                buf_start = offset
                continue

            art = _ARTICLE.match(line)
            if art:
                flush_body(line_start)
                article_no = f"第{art.group(1)}条"
                buf_start = line_start
                buf_lines = [line]
                continue

            if not line.strip():
                flush_body(offset)
                buf_start = offset
                continue

            if not buf_lines:
                buf_start = line_start
            buf_lines.append(line)

        flush_body(offset)
        if table_buf:
            cur.units.append(_Unit("\n".join(table_buf), table_start, offset, "table", path(), article_no, len(heading_stack)))
        return [s for s in sections if s.units]

    # ---- 切分
    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        doc = normalize(text)
        sections = self._parse(doc)
        chunks: list[Chunk] = []

        for sec in sections:
            parent_text = doc[sec.units[0].start:sec.units[-1].end].strip()
            parent_id = f"{doc_id}@sec{len(chunks):04d}"
            self._emit_section(doc_id, doc, sec, chunks, parent_id, parent_text)
        return chunks

    def _prefix_for(self, heading_path: tuple[str, ...]) -> str:
        """上下文前缀（标题路径）；前缀长度会从块上限中预留。"""
        if self.context_prefix and heading_path:
            return " > ".join(heading_path) + "\n"
        return ""

    def _emit_section(self, doc_id: str, doc: str, sec: _Section,
                      chunks: list[Chunk], parent_id: str, parent_text: str) -> None:
        pending: list[_Unit] = []

        def emit(units: list[_Unit], kind: str, text_override: str | None = None) -> None:
            nonlocal pending
            if not units:
                return
            start, end = units[0].start, units[-1].end
            body = text_override if text_override is not None else doc[start:end].strip()
            arts = [u.article_no for u in units if u.article_no]
            art_range = ""
            if arts:
                art_range = arts[0] if len(set(arts)) == 1 else f"{arts[0]}-{arts[-1]}"
            heading_path = units[0].heading_path
            prefix = self._prefix_for(heading_path)
            chunks.append(_finalize(
                doc_id, len(chunks), prefix + body, start, end,
                kind=kind, heading_path=heading_path, article_range=art_range,
                tokenizer=self.tokenizer,
                parent_id=parent_id if parent_text else "",
                parent_text=parent_text if self.with_parent else "",
            ))
            pending = []

        for unit in sec.units:
            prefix = self._prefix_for(unit.heading_path)
            # 前缀占长度，正文可用上限相应下调；留一个最小下限防止前缀过长时切空
            body_limit = max(80, self.max_chars - len(prefix))

            if unit.kind in ("table", "code"):
                emit(pending, "text")
                for piece, sub_kind in self._split_atomic(unit, body_limit):
                    chunks.append(_finalize(
                        doc_id, len(chunks), prefix + piece, unit.start, unit.end,
                        kind=sub_kind, heading_path=unit.heading_path,
                        article_range=unit.article_no, tokenizer=self.tokenizer,
                        parent_id=parent_id, parent_text=parent_text if self.with_parent else "",
                    ))
                continue

            # 判定「是否超长」用 max_chars，而不是扣掉前缀后的 body_limit：
            # 一个 160 字的完整法条在 200 字上限下应当整条保留，允许最终块因标题前缀
            # 略微超过上限，也好过把条款劈成两半。只有正文本身超过上限才降级切分。
            if len(unit.text) > self.max_chars:
                emit(pending, "text")
                for piece in self._split_long(unit.text, body_limit):
                    idx = doc.find(piece, unit.start)
                    idx = unit.start if idx < 0 else idx
                    chunks.append(_finalize(
                        doc_id, len(chunks), prefix + piece, idx, idx + len(piece),
                        kind="text", heading_path=unit.heading_path,
                        article_range=unit.article_no, tokenizer=self.tokenizer,
                        parent_id=parent_id, parent_text=parent_text if self.with_parent else "",
                    ))
                continue

            # 按原文切片的实际跨度复核；前缀长度已在 body_limit 中预留
            if pending and unit.end - pending[0].start > body_limit:
                emit(pending, "text")
            pending.append(unit)
        emit(pending, "text")

    def _split_atomic(self, unit: _Unit, limit: int) -> list[tuple[str, str]]:
        if len(unit.text) <= limit:
            return [(unit.text, unit.kind)]
        if unit.kind == "code":
            lines = unit.text.split("\n")
            out, buf = [], ""
            for ln in lines:
                if len(buf) + len(ln) + 1 > limit and buf:
                    out.append(buf)
                    buf = ""
                buf = ln if not buf else buf + "\n" + ln
            if buf:
                out.append(buf)
            return [(x, "code") for x in out]

        if unit.text.lstrip().lower().startswith("<table"):
            rows = re.split(r"(?=<tr)", unit.text, flags=re.I)
            head = rows[0]
            out, buf = [], ""

            def flush_html() -> None:
                nonlocal buf
                if buf:
                    out.append(head + buf + "</table>")
                    buf = ""

            for row in rows[1:]:
                if len(head) + len(buf) + len(row) > limit and buf:
                    flush_html()
                if len(row) > limit:
                    # 单行 <tr> 仍超长：先按 <td> 单元格拆，再按字符兜底
                    flush_html()
                    cells = re.split(r"(?=<td)", row, flags=re.I)
                    for cell in cells:
                        for piece in self._split_long(cell, limit):
                            out.append(piece)
                    continue
                buf += row
            if buf:
                out.append(head + buf + "</table>")
            return [(x, "table") for x in out] if out else [(unit.text, "table")]

        lines = [ln for ln in unit.text.split("\n") if ln.strip()]
        header = lines[:2]
        rows = lines[2:] if len(lines) > 2 else []
        out, buf = [], ""
        for row in rows:
            cand = "\n".join(header) + ("\n" + buf if buf else "") + "\n" + row
            if len(cand) > limit and buf:
                out.append("\n".join(header) + "\n" + buf)
                buf = row
            elif len(cand) > limit and not buf:
                out.append(row)          # 单行超长，退化为整行保留
            else:
                buf = row if not buf else buf + "\n" + row
        if buf:
            out.append("\n".join(header) + "\n" + buf)
        return [(x, "table") for x in out] if out else [(unit.text, "table")]


STRATEGIES = {
    "fixed": FixedSizeChunker,
    "sentence": SentenceChunker,
    "recursive": RecursiveCharChunker,
    "structure": StructureFirstChunker,
}


def build_chunker(name: str, **kwargs) -> BaseChunker:
    if name not in STRATEGIES:
        raise ValueError(f"未知策略 {name}，可选：{sorted(STRATEGIES)}")
    return STRATEGIES[name](**kwargs)
