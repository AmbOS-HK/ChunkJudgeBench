"""下载公开中文语料并落盘，记录来源 URL 与内容哈希，保证评测可复现。

只抓取公开、允许引用的文本（法律法规、开源项目 README）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "data" / "corpus"


class _BlockTextExtractor(HTMLParser):
    """把 HTML 按块级元素还原成带换行的纯文本，保留段落结构。"""

    BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "table", "ul", "ol", "blockquote", "pre",
    }
    SKIP_TAGS = {"script", "style", "head", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[\t\u3000 ]+", " ", ln).strip() for ln in raw.splitlines()]
        lines = [ln for ln in lines if ln]
        return "\n".join(lines)


def html_to_text(html: str) -> str:
    parser = _BlockTextExtractor()
    parser.feed(html)
    return parser.text()


def normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


SOURCES = [
    {
        "name": "民法典.md",
        "url": "https://zh.wikisource.org/w/api.php",
        "kind": "wikisource",
        "note": "《中华人民共和国民法典》全文，中文维基文库（公有领域法律文本），按编/章/节/条组织",
        "api_title": "中华人民共和国民法典",
    },
    {
        "name": "rust-course-README.md",
        "url": "https://cdn.jsdelivr.net/gh/sunface/rust-course@main/README.md",
        "kind": "markdown",
        "note": "《Rust 语言圣经》中文 README，多级标题结构",
    },
    {
        "name": "coding-interview-university-cn.md",
        "url": "https://raw.githubusercontent.com/jwasham/coding-interview-university/master/translations/README-cn.md",
        "kind": "markdown",
        "note": "CS 自学路线中文版 README，含列表与表格的长文档",
    },
]


def _get_with_retry(url: str, params: dict | None = None, attempts: int = 4) -> requests.Response:
    last: Exception | None = None
    for i in range(attempts):
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=(10, 60),
                headers={
                    "User-Agent": "ChunkJudgeBench/1.0 (corpus research; contact: local)",
                    "Accept": "text/html,application/json,text/plain,*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last = exc
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"{attempts} 次尝试均失败: {last}")


def wiki_heading_to_markdown(text: str) -> str:
    """把 MediaWiki 的 == 标题 == 转成 Markdown 标题，保留层级。"""
    def repl(m: re.Match) -> str:
        level = len(m.group(1))
        return "#" * min(level, 6) + " " + m.group(2).strip()

    return re.sub(r"^(=+)\s*(.+?)\s*\1$", repl, text, flags=re.M)


def fetch_wikisource(src: dict) -> str:
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "titles": src["api_title"],
    }
    resp = _get_with_retry(src["url"], params=params)
    pages = resp.json()["query"]["pages"]
    extract = pages[0].get("extract", "")
    if not extract:
        raise RuntimeError(f"维基文库未返回正文: {pages[0].get('title')}")
    return normalize_markdown(wiki_heading_to_markdown(extract))


def corpus_stats(src: dict, text: str) -> dict:
    return {
        "name": src["name"],
        "url": src["url"],
        "note": src["note"],
        "chars": len(text),
        "lines": text.count("\n") + 1,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "heading_like_lines": len(re.findall(r"^(第[一二三四五六七八九十百千]+[编章节]|#+ |第[一二三四五六七八九十百千]+条)", text, re.M)),
        "table_rows": len(re.findall(r"^\s*\|.*\|\s*$", text, re.M)),
    }


def fetch_one(src: dict, force: bool = False) -> dict:
    target = CORPUS_DIR / src["name"]
    if target.exists() and not force:
        text = target.read_text(encoding="utf-8")
    else:
        if src["kind"] == "wikisource":
            text = fetch_wikisource(src)
        else:
            resp = _get_with_retry(src["url"])
            if src["kind"] == "html":
                resp.encoding = resp.apparent_encoding or "utf-8"
                text = html_to_text(resp.text)
            else:
                text = normalize_markdown(resp.text)
        target.write_text(normalize_markdown(text), encoding="utf-8")

    return corpus_stats(src, target.read_text(encoding="utf-8"))


def rebuild_manifest() -> int:
    """只按磁盘上的现有文件重算清单，不联网。

    用于语料被外部改动（例如本地清洗）后同步 manifest；哈希会随之更新，
    因此它只能保证「清单与当前文件一致」，不能证明文件仍等于原始来源。
    要证明后者，应执行 fetch_corpus.py --force 重新抓取并比对。
    """
    manifest = []
    for src in SOURCES:
        target = CORPUS_DIR / src["name"]
        if not target.exists():
            print(f"[FAIL] 缺少 {src['name']}", file=sys.stderr)
            return 1
        manifest.append(corpus_stats(src, target.read_text(encoding="utf-8")))
        print(f"[OK] {src['name']}: {manifest[-1]['chars']} 字符")
    (ROOT / "data" / "corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[OK] manifest 已按当前文件重建")
    return 0


def main() -> int:
    if "--rebuild-manifest" in sys.argv:
        return rebuild_manifest()
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    manifest = []
    for src in SOURCES:
        try:
            stats = fetch_one(src, force=force)
        except Exception as exc:  # 网络问题要显式暴露，不静默跳过
            print(f"[FAIL] {src['name']}: {exc}", file=sys.stderr)
            return 1
        manifest.append(stats)
        print(f"[OK] {stats['name']}: {stats['chars']} 字符, "
              f"{stats['heading_like_lines']} 个结构行, {stats['table_rows']} 行表格")
    (ROOT / "data" / "corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

