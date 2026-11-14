# NOTICE —— 版权与第三方材料声明

本仓为 **ChunkJudgeBench**（中文文本切片策略评测）。根目录 `LICENSE` 中的 MIT 许可
**只覆盖本仓自研代码与本仓自行撰写的文档**，不覆盖下列第三方材料。第三方材料按其
各自授权条款提供，两者相互独立。

## 1. 语料正文不入库

`data/corpus/` 已在 `.gitignore` 中排除。克隆仓库后须运行
`python scripts/fetch_corpus.py` 从原始来源下载。仓库内只保留抓取脚本与
`data/corpus_manifest.json`（来源 URL、字符数、行数、sha256）。

## 2. `data/qa.jsonl` 中的第三方引文（会被公开再分发）

评测问答集的 `gold_quote` 字段由 `scripts/build_qa.py` 从原文逐字截取（脚本会校验
引文必须能在原文定位）。因此这部分内容随仓库公开，构成对第三方文本的**片段引用**，
与"语料正文不入库"是两件事。当前共 44 条、4372 字符（含空白）：

| 来源 | 文档标识 | 条数 | 引文字符 | 授权状态 |
| --- | --- | ---: | ---: | --- |
| 《中华人民共和国民法典》（中文维基文库） | `民法典` | 34 | 3834 | 公有领域法律文本 |
| jwasham/coding-interview-university（中文 README） | `coding-interview-university-cn` | 8 | 334 | CC-BY-SA-4.0 |
| sunface/rust-course（README） | `rust-course-README` | 2 | 204 | 无可识别许可证（作者明示 No License） |

每条记录都带两个字段，用于追溯与校验：

- `source_license`：`public-domain` / `CC-BY-SA-4.0` / `NOASSERTION`
- `quote_sha256`：该条 `gold_quote` UTF-8 字节的 sha256

`scripts/selfcheck.py` 会强制校验这两个字段，缺失或不一致即判 FAIL。

### 2.1 coding-interview-university（CC-BY-SA-4.0）

- 来源：<https://github.com/jwasham/coding-interview-university>，
  中文版 <https://github.com/jwasham/coding-interview-university/blob/master/translations/README-cn.md>
- 作者：John Washam 及译者
- 许可：Creative Commons Attribution-ShareAlike 4.0 International
  （<https://creativecommons.org/licenses/by-sa/4.0/>）
- 本仓用法：仅在评测问答集中引用 8 条、合计 334 字符的原文片段，用于「检索能否召回
  完整证据」的定位基准，未修改其文字内容。
- 需要说明的边界：CC-BY-SA-4.0 的「相同方式共享」条款是否延伸至包含这些片段的整体
  文件（`data/qa.jsonl`），属于许可解释问题，本仓不在此下法律结论。若权利人认为当前
  引用方式不合适，请联系我们，我们会移除对应条目或改为只存定位指纹（见第 4 节）。

### 2.2 sunface/rust-course（无可识别许可证）

- 来源：<https://github.com/sunface/rust-course> 的 README
- 该 README 自述采用 No License，并明示「可以随意 fork 和阅读，但不能私下修改后再
  包装分发」。
- 本仓用法：仅引用 2 条、合计 204 字符的原文片段作为评测定位基准，并在此标注来源与
  授权状态，未做修改、未做二次包装。
- 若权利人要求，我们会直接删除这 2 条（`rust-01`、`rust-02`）。

### 2.3 《民法典》（公有领域）

中华人民共和国法律文本不受著作权保护（《著作权法》第五条），可自由使用。本仓只在
本地评测中读取全文，正文不入库；问答集中保留 34 条法条引文作为定位基准。

## 3. 商标与背书

文中出现的第三方项目名、仓库名与链接仅用于标识数据来源，不表示其作者对本项目的
认可或背书。

## 4. 如何移除第三方引文

1. 删除 `scripts/build_qa.py` 中 `ANCHOR_SPECS` 对应条目（及 `DOC_LICENSE` 中该来源）；
2. 或改为只存定位信息：去掉 `gold_quote` 原文，保留 `doc`、`start`、`length`、`quote_sha256`，
   由本地语料在运行时还原；
3. 重跑 `python scripts/build_qa.py`，再跑 `python scripts/selfcheck.py` 确认引文仍可定位。

## 5. 联系方式

版权或授权问题请联系：support@ambos.cn。
安全问题请按 [SECURITY.md](SECURITY.md) 的渠道报告。
