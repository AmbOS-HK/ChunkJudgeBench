"""生成评测用问答集：gold_quote 从语料原文逐字截取，避免手工转写误差。

两种取法：
- article：按《民法典》「第X条」整行截取，保证法条原文一字不差；
- anchor：按锚点字符串定位整行，用于 README 类文档。

每条都会做三重校验：引文必须能在原文中找到、长度不少于 30 字、不得是问题的子串
（防止检索被问题原文直接命中而虚高）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus"
OUT = ROOT / "data" / "qa.jsonl"

LAW = "民法典"

# 每条问答在公开仓库里会被再分发，因此必须逐条标明引文所属来源的授权状态，
# 而不是只在 README 里笼统说明「第三方语料不入库」。见 NOTICE.md。
DOC_LICENSE = {
    LAW: "public-domain",
    "coding-interview-university-cn": "CC-BY-SA-4.0",
    "rust-course-README": "NOASSERTION",
}


def license_of(doc_id: str) -> str:
    return DOC_LICENSE[doc_id]


def quote_digest(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()

# (qid, doc, question, 条号)
ARTICLE_SPECS = [
    ("law-01", LAW, "民事活动违背公序良俗会有什么后果？", "第八条"),
    ("law-02", LAW, "法律没有明确规定时，处理民事纠纷还能依据什么？", "第十条"),
    ("law-03", LAW, "一个民事法律行为要有效，需要同时满足哪些条件？", "第一百四十三条"),
    ("law-04", LAW, "向人民法院请求保护民事权利的诉讼时效期间是多久？", "第一百八十八条"),
    ("law-05", LAW, "不动产物权的设立和转让，什么时候才算生效？", "第二百零九条"),
    ("law-06", LAW, "无处分权人把不动产转让出去，受让人在什么情形下能取得所有权？", "第三百一十一条"),
    ("law-07", LAW, "依法成立的合同对当事人有什么约束力？", "第四百六十五条"),
    ("law-08", LAW, "当事人履行合同义务时应当遵循哪些基本要求？", "第五百零九条"),
    ("law-09", LAW, "出现哪些情形，当事人可以解除合同？", "第五百六十三条"),
    ("law-10", LAW, "一方不履行合同义务或者履行不符合约定，要承担什么责任？", "第五百七十七条"),
    ("law-11", LAW, "租赁合同的定义是什么，出租人要履行什么义务？", "第七百零三条"),
    ("law-12", LAW, "建设工程的价款被拖欠时，承包人有什么救济手段？", "第八百零七条"),
    ("law-13", LAW, "自然人享有哪些一般人格权？", "第九百九十条"),
    ("law-14", LAW, "自然人的个人信息受不受法律保护？", "第一百一十一条"),
    ("law-15", LAW, "夫妻在婚姻关系存续期间所得的哪些财产属于共同财产？", "第一千零六十二条"),
    ("law-16", LAW, "夫妻一方要求离婚，可以直接向法院起诉吗？", "第一千零七十九条"),
    ("law-17", LAW, "法定继承的第一顺序继承人包括哪些人？", "第一千一百二十七条"),
    ("law-18", LAW, "从建筑物中抛掷物品造成他人损害，责任怎么认定？", "第一千二百五十四条"),
    ("law-19", LAW, "家庭暴力是不是法律明令禁止的行为？", "第一千零四十二条"),
    ("law-20", LAW, "所有权人对自己的不动产或者动产享有哪些权能？", "第二百四十条"),
    ("law-21", LAW, "一方用欺诈手段让对方在违背真实意愿的情况下签了合同，受欺诈方可以怎么办？", "第一百四十八条"),
    ("law-22", LAW, "违反法律强制性规定的民事法律行为，效力如何？", "第一百五十三条"),
    ("law-23", LAW, "行为人没有代理权，但相对人有理由相信其有代理权，代理行为有效吗？", "第一百七十二条"),
    ("law-24", LAW, "动产物权的设立和转让，从什么时候发生效力？", "第二百二十四条"),
    ("law-25", LAW, "自己的东西被别人无权占有，所有权人可以要求什么？", "第二百三十五条"),
    ("law-26", LAW, "抵押权是什么，债务人不履行到期债务时抵押权人能做什么？", "第三百九十四条"),
    ("law-27", LAW, "法律对高利放贷是什么态度，借款利息应当怎么约定？", "第六百八十条"),
    ("law-28", LAW, "承租人可以把租来的房子转租给别人吗？", "第七百一十六条"),
    ("law-29", LAW, "隐私权包括哪些内容，哪些行为属于侵害他人隐私？", "第一千零三十二条"),
    ("law-30", LAW, "行为人造成他人损害，侵权责任按什么原则认定？", "第一千一百六十五条"),
    ("law-31", LAW, "侵害他人造成人身损害，应当赔偿哪些费用？", "第一千一百七十九条"),
    ("law-32", LAW, "在公共场所或者道路上施工致人损害，由谁承担责任？", "第一千二百五十八条"),
    ("law-33", LAW, "解除合同的通知什么时候生效？", "第五百六十五条"),
    ("law-34", LAW, "未经同意用别人的照片做宣传，算不算侵犯肖像权？", "第一千零一十九条"),
]

# (qid, doc, question, 锚点)
ANCHOR_SPECS = [
    ("ciu-01", "coding-interview-university-cn",
     "想去大公司做软件工程师，需要具备哪些知识？",
     "如果你想在一家大公司担任软件工程师"),
    ("ciu-02", "coding-interview-university-cn",
     "不用 Git 的情况下，怎么把这个仓库的资料下载下来？",
     "在该页面上，单击顶部附近的 Code 按钮"),
    ("ciu-03", "coding-interview-university-cn",
     "这份学习计划主要用哪两种编程语言？",
     "在这个学习计划中，我主要使用了两种编程语言"),
    ("ciu-04", "coding-interview-university-cn",
     "为什么用 C 语言来理解数据结构更有帮助？",
     "C: 非常底层。它允许你处理指针"),
    ("ciu-05", "coding-interview-university-cn",
     "总觉得自己不够聪明、做不了程序员，怎么调整心态？",
     "大多数成功的软件工程师都非常聪明"),
    ("ciu-06", "coding-interview-university-cn",
     "没有计算机学位的人，跟这个计划能补上什么？",
     "如果你错过了计算机科学的学位"),
    ("rust-01", "rust-course-README",
     "这套 Rust 教程一共写了多少章、多少字，花了多长时间？",
     "Rust 语言圣经已写 170 余章"),
    ("rust-02", "rust-course-README",
     "这个教程采用什么开源协议，能不能改完再分发？",
     "在开源版权上，我们选择了"),
    ("ciu-07", "coding-interview-university-cn",
     "这份学习计划应该按什么顺序推进？",
     "下面所有的东西都只是一个概述"),
    ("ciu-08", "coding-interview-university-cn",
     "作者是怎么跟踪学习计划完成进度的？",
     "在学习过程中，我使用 GitHub 特殊语法的 Markdown"),
]


def load_doc(doc_id: str) -> str:
    path = CORPUS / f"{doc_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"缺少语料：{path}")
    return path.read_text(encoding="utf-8")


def pick_article(doc: str, article: str) -> str:
    """取「第X条」整条：条首行 + 紧接的款项行（到空行或下一条/标题为止）。"""
    lines = doc.split("\n")
    boundary = re.compile(r"^(第[一二三四五六七八九十百千零]+条|#{1,6}\s|第[一二三四五六七八九十百千零]+[编分章节])")
    for i, line in enumerate(lines):
        if line.startswith(article) and (len(line) == len(article) or not line[len(article)].strip()):
            block = [line.rstrip()]
            for nxt in lines[i + 1:]:
                if not nxt.strip() or boundary.match(nxt):
                    break
                block.append(nxt.rstrip())
            return "\n".join(block)
    raise ValueError(f"未找到 {article}")


def pick_anchor(doc: str, anchor: str) -> str:
    for line in doc.split("\n"):
        if anchor in line:
            return line.strip()
    raise ValueError(f"未找到锚点 {anchor}")


def main() -> int:
    records: list[dict] = []
    failures: list[str] = []

    for qid, doc_id, question, article in ARTICLE_SPECS:
        doc = load_doc(doc_id)
        try:
            quote = pick_article(doc, article)
        except ValueError as exc:
            failures.append(f"{qid}: {exc}")
            continue
        records.append({
            "qid": qid, "doc": doc_id, "question": question,
            "gold_quote": quote, "source": f"《民法典》{article}", "type": "article",
            "source_license": license_of(doc_id), "quote_sha256": quote_digest(quote),
        })

    for qid, doc_id, question, anchor in ANCHOR_SPECS:
        doc = load_doc(doc_id)
        try:
            quote = pick_anchor(doc, anchor)
        except ValueError as exc:
            failures.append(f"{qid}: {exc}")
            continue
        records.append({
            "qid": qid, "doc": doc_id, "question": question,
            "gold_quote": quote, "source": anchor, "type": "prose",
            "source_license": license_of(doc_id), "quote_sha256": quote_digest(quote),
        })

    # 校验
    for rec in records:
        doc = load_doc(rec["doc"])
        if rec["gold_quote"] not in doc:
            failures.append(f"{rec['qid']}: 引文不在原文中")
        if len(re.sub(r"\s+", "", rec["gold_quote"])) < 30:
            failures.append(f"{rec['qid']}: 引文过短（{len(rec['gold_quote'])} 字符）")
        if rec["gold_quote"] in rec["question"]:
            failures.append(f"{rec['qid']}: 引文是问题的子串，会虚高")

    if failures:
        for f in failures:
            print("[FAIL]", f)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    by_doc: dict[str, int] = {}
    for rec in records:
        by_doc[rec["doc"]] = by_doc.get(rec["doc"], 0) + 1
    print(f"[OK] 写入 {len(records)} 条问答：{by_doc}")
    print("[OK] 引文长度区间：",
          min(len(r["gold_quote"]) for r in records),
          "~", max(len(r["gold_quote"]) for r in records), "字符")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
