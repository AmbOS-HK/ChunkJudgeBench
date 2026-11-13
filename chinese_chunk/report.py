"""生成 Markdown 评测报告。所有结论句都由指标直接生成，不写没有数字支撑的判断。"""
from __future__ import annotations

import datetime as _dt
from typing import Sequence

from .evaluate import StrategyResult


def _fmt(x: float, digits: int = 3) -> str:
    return f"{x:.{digits}f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _pick(results: Sequence[StrategyResult], strategy: str, size: int) -> StrategyResult | None:
    for r in results:
        if r.strategy == strategy and r.max_chars == size:
            return r
    return None


def _per_doc_recall(result: StrategyResult, k: int = 5) -> dict[str, tuple[int, int]]:
    stats: dict[str, list[int]] = {}
    for d in result.details:
        hit = 1 if (d["hit_rank"] is not None and d["hit_rank"] <= k) else 0
        stats.setdefault(d["doc"], [0, 0])
        stats[d["doc"]][0] += hit
        stats[d["doc"]][1] += 1
    return {doc: (v[0], v[1]) for doc, v in stats.items()}


def build_markdown_report(
    results: Sequence[StrategyResult],
    qa: Sequence[dict],
    docs: dict[str, str],
    *,
    overlap: int,
) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    sizes = sorted({r.max_chars for r in results})
    strategies = sorted({r.strategy for r in results})

    lines: list[str] = []
    lines.append("# 中文切片质量评测报告")
    lines.append("")
    lines.append(f"- 生成时间：{now}")
    lines.append(f"- 语料：{len(docs)} 篇公开中文文档，合计 {sum(len(t) for t in docs.values()):,} 字符")
    for doc_id, text in docs.items():
        lines.append(f"  - `{doc_id}`：{len(text):,} 字符")
    lines.append(f"- 问答集：{len(qa)} 条（引文从原文逐字截取，已校验可定位）")
    lines.append(f"- 重叠：{overlap} 字符；token 口径：非空白字符数")
    lines.append("- 检索器：字符二元组 BM25（k1=1.5, b=0.75），全部策略共用")
    lines.append("")

    lines.append("## 1. 评测口径")
    lines.append("")
    lines.append("| 指标 | 含义 |")
    lines.append("| --- | --- |")
    lines.append("| Recall@k | 问题对应原文引文完整落在前 k 个召回块中的比例 |")
    lines.append("| MRR | 首个命中块的排名倒数均值 |")
    lines.append("| 引文完整率 | 引文能被单个块完整覆盖的比例（越高说明越不容易被切碎） |")
    lines.append("| 边界完整率 | 块尾落在句末标点、或属于表格/代码块的比例 |")
    lines.append("| p50/p95 | 块大小的中位数与 95 分位（字符） |")
    lines.append("| 冗余率 | 全部块字符总量相对原文的膨胀比例（重叠造成） |")
    lines.append("")
    lines.append("> 这些指标衡量「检索能否拿到完整证据」，不等于大模型最终回答的正确率。")
    lines.append("")

    lines.append("## 2. 主结果")
    lines.append("")
    headers = ["策略", "上限", "块数", "R@1", "R@3", "R@5", "MRR",
               "引文完整率", "单元完整率", "边界完整率", "p50", "p95", "最大", "冗余率"]
    rows = []
    for r in sorted(results, key=lambda x: (x.strategy, x.max_chars)):
        rows.append([
            r.strategy, str(r.max_chars), str(r.n_chunks),
            _fmt(r.recall_at_1), _fmt(r.recall_at_3), _fmt(r.recall_at_5), _fmt(r.mrr),
            _fmt(r.quote_intact_rate), _fmt(r.unit_intact_rate), _fmt(r.boundary_intact_rate),
            f"{r.chars_per_chunk_p50:g}", f"{r.chars_per_chunk_p95:g}",
            str(r.chars_per_chunk_max), _fmt(r.redundancy_rate),
        ])
    lines.append(_table(headers, rows))
    lines.append("")
    lines.append("> 单元完整率 = 文档中的完整结构单元（法条/段落，长度 ≤ 上限 80%）能被单个块完整覆盖的比例，"
                 f"参与统计的单元数在各尺寸下为 {', '.join(str(r.unit_count) for r in results if r.strategy == results[0].strategy)}；"
                 "它与检索排名无关，是确定性的切片质量指标。")
    lines.append("")

    # ---- 同尺寸对比
    compare_size = 600 if 600 in sizes else sizes[len(sizes) // 2]
    lines.append(f"## 3. 同尺寸对比（上限 {compare_size} 字符）")
    lines.append("")
    at_size = [r for r in results if r.max_chars == compare_size]
    base = _pick(results, "recursive", compare_size)
    rows = []
    for r in sorted(at_size, key=lambda x: -x.recall_at_5):
        delta = ""
        if base and r.strategy != "recursive":
            delta = f"{r.recall_at_5 - base.recall_at_5:+.3f}"
        rows.append([r.strategy, _fmt(r.recall_at_5), _fmt(r.mrr),
                     _fmt(r.quote_intact_rate), _fmt(r.unit_intact_rate),
                     _fmt(r.boundary_intact_rate), delta or "—"])
    lines.append(_table(["策略", "R@5", "MRR", "引文完整率", "单元完整率", "边界完整率", "R@5 相对 recursive"], rows))
    lines.append("")

    # ---- 分文档
    lines.append("## 4. 分文档表现（R@5）")
    lines.append("")
    doc_ids = sorted(docs.keys())
    rows = []
    for r in sorted(at_size, key=lambda x: (x.strategy)):
        vals = _per_doc_recall(r, 5)
        cells = []
        for doc in doc_ids:
            hit, total = vals.get(doc, (0, 0))
            cells.append(f"{hit}/{total}" if total else "—")
        rows.append([r.strategy] + cells)
    lines.append(_table(["策略"] + doc_ids, rows))
    lines.append("")

    # ---- 失败案例
    st = _pick(results, "structure", compare_size)
    rc = _pick(results, "recursive", compare_size)
    fail_rows = []
    for r in [x for x in (st, rc) if x]:
        for d in r.details:
            if d["hit_rank"] is None or not d["quote_intact"]:
                fail_rows.append([r.strategy, d["qid"], d["doc"],
                                  str(d["hit_rank"]), "是" if d["quote_intact"] else "否",
                                  _fmt(d["best_coverage"])])
    lines.append(f"## 5. 未召回或被切碎的案例（上限 {compare_size}）")
    lines.append("")
    if fail_rows:
        lines.append(_table(["策略", "qid", "文档", "命中排名", "引文完整", "最佳单块覆盖率"], fail_rows))
    else:
        lines.append("无：structure 与 recursive 在该尺寸下所有问题都被召回且引文完整。")
    lines.append("")

    # ---- 结论（由数字生成）
    lines.append("## 6. 结论")
    lines.append("")
    best_r5 = max(r.recall_at_5 for r in results)
    ties = [r for r in results if abs(r.recall_at_5 - best_r5) < 1e-9]
    tie_desc = "、".join(f"`{r.strategy}`@{r.max_chars}" for r in sorted(ties, key=lambda x: x.max_chars))
    lines.append(f"- R@5 最高值为 {_fmt(best_r5)}，由 {len(ties)} 个组合并列达到：{tie_desc}。"
                 f"在 {len(qa)} 条问答上该指标已接近饱和，单看 R@5 无法区分策略。")
    best_mrr = max(results, key=lambda x: x.mrr)
    lines.append(f"- MRR 最高的组合是 `{best_mrr.strategy}` + 上限 {best_mrr.max_chars} 字符，"
                 f"MRR={_fmt(best_mrr.mrr)}（排名越靠前越有价值，这一项比 R@5 更能反映差异）。")
    if st and rc:
        d5 = st.recall_at_5 - rc.recall_at_5
        di = st.quote_intact_rate - rc.quote_intact_rate
        du = st.unit_intact_rate - rc.unit_intact_rate
        db = st.boundary_intact_rate - rc.boundary_intact_rate
        lines.append(f"- 同为 {compare_size} 字符上限：structure 相对 recursive 的 R@5 差值为 "
                     f"{d5:+.3f}，引文完整率差值 {di:+.3f}，单元完整率差值 {du:+.3f}，"
                     f"边界完整率差值 {db:+.3f}。")
        n_diff = round(abs(d5) * len(qa))
        if abs(d5) * len(qa) <= 1.5:
            lines.append(f"- R@5 的差值折合约 {n_diff} 道题，在 {len(qa)} 条问答的样本里属于随机波动区间，"
                         "不足以据此判定策略优劣；单元完整率是确定性指标，参考价值更高。")
        if du > 0.01:
            lines.append(f"- 单元完整率高出 {du:+.3f}，对应约 "
                         f"{round(du * st.unit_count)} 个结构单元从「被切断」变为「完整」，"
                         "这是结构优先的直接收益。")
    for strat in strategies:
        sub = [r for r in results if r.strategy == strat]
        if len(sub) >= 2:
            lo, hi = min(sub, key=lambda x: x.max_chars), max(sub, key=lambda x: x.max_chars)
            lines.append(f"- `{strat}` 从 {lo.max_chars} 到 {hi.max_chars} 字符："
                         f"块数 {lo.n_chunks}→{hi.n_chunks}，R@5 {_fmt(lo.recall_at_5)}→{_fmt(hi.recall_at_5)}，"
                         f"引文完整率 {_fmt(lo.quote_intact_rate)}→{_fmt(hi.quote_intact_rate)}。")
    lines.append("")

    # ---- 局限
    lines.append("## 7. 局限与未验证项")
    lines.append("")
    lines.append(f"- 样本量：{len(docs)} 篇文档 / {len(qa)} 条问答，只足以给出方向性结论，不能外推为通用定论。")
    lines.append("- 检索层只用 BM25，未接入向量检索与 rerank；混合检索下策略排序可能变化，需另测。")
    lines.append("- token 口径是非空白字符数，不等于任何具体模型的 token；跨模型比较前须换算。")
    lines.append("- 表格只覆盖 Markdown 管道表与 HTML `<table>` 原子块，未涉及扫描件跨页表格。")
    lines.append("- 结构识别依赖 Markdown 标题与「第X条」正则；PDF/扫描件必须先做版面还原（MinerU/Docling），"
                 "否则识别出的是假结构。")
    lines.append("- 未做端到端回答质量评测（忠实度、答案正确率），本报告只覆盖检索证据层。")
    lines.append("")
    return "\n".join(lines)
