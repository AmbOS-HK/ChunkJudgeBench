"""检索器：中文 BM25（字符二元组索引），零外部依赖。

为什么用 bigram 而不是分词：
- 中文没有词边界，绑定某个分词器会把评测结论绑到那个词典上；
- 字符二元组是中文 IR 的经典做法，对未登录词、专有名词、条款编号都稳定；
- 评测只比较「切片策略」，检索器固定不变，因此这个选择不影响策略间的相对结论。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from .splitters import Chunk

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    for word in _ALNUM.findall(text):
        tokens.append(word.lower())
    return tokens


@dataclass
class Hit:
    chunk: Chunk
    score: float


class BM25Index:
    """标准 Okapi BM25，k1=1.5，b=0.75。"""

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b
        self._tf: list[Counter] = []
        self._dl: list[int] = []
        df: Counter = Counter()
        for chunk in self.chunks:
            toks = tokenize(chunk.text)
            tf = Counter(toks)
            self._tf.append(tf)
            self._dl.append(len(toks))
            for t in tf:
                df[t] += 1
        self._n = len(self.chunks)
        self._avgdl = (sum(self._dl) / self._n) if self._n else 0.0
        self._idf = {
            t: math.log(1 + (self._n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.chunks:
            return []
        scores: list[float] = []
        for tf, dl in zip(self._tf, self._dl):
            score = 0.0
            for t in q_tokens:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = self._idf.get(t, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1))
                score += idf * (f * (self.k1 + 1)) / denom
            scores.append(score)
        order = sorted(range(self._n), key=lambda i: scores[i], reverse=True)[:top_k]
        return [Hit(self.chunks[i], scores[i]) for i in order if scores[i] > 0]
