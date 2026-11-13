"""中文文本规范化、句子切分与 token 计数。

设计原则：
- 不引入分词依赖。中文检索按字符二元组（bigram）建索引，切片按标点与结构判定，
  都不需要词典，避免 jieba 等外部分词器带来的口径漂移。
- token 计数默认按「非空白字符数」，并明确记录口径；如安装了 tiktoken 可切换，
  但中文下不同 tokenizer 差异很大，评测报告必须写清用的是哪一种。
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

# 句末标点：句子结束
SENT_END = "。！？!?…"
# 分句标点：句子内部的停顿
CLAUSE = "；;，,、：:"
# 成对引号与括号内的标点不切（简化处理：只处理单层配对）
_PAIRS = {"「": "」", "“": "”", "（": "）", "(": ")", "《": "》", "[": "]"}

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)


def normalize(text: str) -> str:
    """统一换行、去掉零宽字符、压缩多余空行，保留所有正文内容。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_ZERO_WIDTH)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def squash(text: str) -> str:
    """去掉所有空白，用于「原文包含」判断，避免空格差异造成的假阴性。"""
    return re.sub(r"\s+", "", text)


def split_sentences(paragraph: str) -> list[str]:
    """按中文句末标点切句，标点留在句尾；不切断引号/括号内部。"""
    sentences: list[str] = []
    buf: list[str] = []
    stack: list[str] = []
    for ch in paragraph:
        buf.append(ch)
        if ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif ch in SENT_END and not stack:
            sentences.append("".join(buf).strip())
            buf = []
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)
    return [s for s in sentences if s]


def split_clauses(sentence: str) -> list[str]:
    """把句子按分句标点拆成更小的语义片段，标点留在片段尾部。"""
    clauses: list[str] = []
    buf: list[str] = []
    stack: list[str] = []
    for ch in sentence:
        buf.append(ch)
        if ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif ch in CLAUSE and not stack:
            clauses.append("".join(buf).strip())
            buf = []
    tail = "".join(buf).strip()
    if tail:
        clauses.append(tail)
    return [c for c in clauses if c]


def count_tokens(text: str, tokenizer: str = "char") -> int:
    """统计 token 数。

    tokenizer="char"    非空白字符数（默认，与 embedding 无关的口径）
    tokenizer="cjk"     汉字 + 西文单词 + 数字串的个数，接近中文 BPE 的实际切分粒度
    tokenizer="tiktoken" 需要安装 tiktoken，按 cl100k_base 计数（仅作参考）
    """
    if tokenizer == "char":
        return len(re.sub(r"\s+", "", text))
    if tokenizer == "cjk":
        return len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z]+|[0-9]+", text))
    if tokenizer == "tiktoken":
        try:
            import tiktoken
        except ImportError as exc:  # 显式失败，不静默降级
            raise RuntimeError("未安装 tiktoken，请改用 tokenizer='char' 或 'cjk'") from exc
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    raise ValueError(f"未知 tokenizer: {tokenizer}")


def tail_overlap(text: str, overlap_chars: int) -> str:
    """取尾部重叠片段，尽量从一个句子边界开始，避免重叠区从词中间截断。"""
    if overlap_chars <= 0:
        return ""
    if len(text) <= overlap_chars:
        return text
    cut = text[-overlap_chars:]
    for i, ch in enumerate(cut):
        if ch in SENT_END:
            return cut[i + 1:].lstrip()
    return cut


def iter_lines(text: str) -> Iterator[tuple[int, str]]:
    """逐行产出 (行号从 1 开始, 行内容)。"""
    for i, line in enumerate(text.split("\n"), start=1):
        yield i, line


def ensure_list(items: Iterable[str]) -> list[str]:
    return [x for x in items if x]
