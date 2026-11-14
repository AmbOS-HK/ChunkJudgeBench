# ChunkJudgeBench 维护说明

更新时间：2026-11-13
工作目录：`D:\ChunkJudgeBench`
远端仓库：`https://github.com/AmbOS-HK/ChunkJudgeBench.git`（公开）

## 1. 项目定位（不要扩大范围）

- 中文知识库**切片策略评测工具**：比较 `fixed` / `sentence` / `recursive` / `structure` 四种策略，输出可复现指标。
- **生产切片器原型**：为知识库切片环节提供算法参考与 JSONL 输出格式。
- 数据只用**公开中文语料**搭骨架；不接真实业务数据，不面向公众提供服务。
- 本项目不负责检索服务、生成回答或业务流程合规审查，不写成业务系统或合规项目。

## 2. 当前状态

- 语言：Python 3.13，核心逻辑只用标准库；联网抓语料用 `requests`。
- `data/corpus/`、`out/`、`reports/` 已在 `.gitignore` 中，语料不入库，本地文件保留。
- 全量自查：切片不变量、表格/代码块识别、边界异常输入、可复现性、报告一致性均通过。
- 工程设施（2026-11-14 补齐）：`pyproject.toml` 打包与 `chunkjudgebench` 命令、CI（离线检查 +
  全量自查）、Issue/PR 模板、Dependabot、`.gitattributes` 显式 LF；`v0.1.0` tag 与 Release
  已发布；GitHub 社区健康度 100%。

## 3. 已验证的评测结果（不要夸大）

语料 3 篇：《民法典》109,993 字符、CIU 中文 README 95,642、rust-course README 4,230。问答集 44 条，`gold_quote` 全部由脚本从原文截取并校验。

同尺寸 600 字符：

| 策略 | 块数 | R@5 | MRR | 引文完整率 | 单元完整率 |
|---|---:|---:|---:|---:|---:|
| fixed | 392 | 0.9318 | 0.7746 | 0.9773 | 0.9476 |
| sentence | 360 | 0.8182 | 0.6845 | 0.8864 | 0.9238 |
| recursive | 425 | 0.9318 | 0.7689 | 0.9773 | 0.9683 |
| structure | 498 | 0.9318 | 0.7973 | 1.0000 | 1.0000 |

必须保留的限定：

- R@5 已饱和，多个组合并列 0.9318，不能用它单独证明结构优先更好。
- `sentence@900` 的 MRR 为 0.800，略高于 `structure@600` 的 0.7973，不能声称 structure 全面最优。
- 可复现性核验值：`[0.6818, 0.9091, 0.9318, 0.7973, 1.0, 1.0, 498]`。
- 当前结论只是“检索证据层”的指标，未接向量检索、rerank 和端到端回答评测。

## 4. 已修复的真实问题

1. 结构解析拼接行时漏了 `\n`，导致偏移算短、正文被截断；已修并加不变量。
2. 超长单元降级顺序改为 行 → 句 → 分句 → 字符，避免在 README 链接中间劈开。
3. 小上限下条款判长误用扣除标题前缀的 `body_limit`，导致完整法条被切；改为按 `max_chars` 判定，允许标题前缀小幅超出。
4. 表格自检未剔除标题路径前缀造成误报；已修。
5. manifest 哈希口径误用规范化文本对比原始文件，属自检误报；已修，并支持 `--rebuild-manifest`。

## 5. 工程待办（按优先级）

1. 用真实业务语料复测（数据由使用者提供），问答集扩到 100 条以上。
2. 接入实际 embedding + rerank，补端到端回答正确率评测。
3. 补同一父节点下的小节点合并，避免碎列表文档过度切分。
4. 补 HTML 标题识别（如 `<h1>`）、列表内嵌标题识别。
5. PDF/扫描件走 MinerU 或 Docling 解析后再进本流程。
6. 已完成：`pyproject.toml`、CI（离线 + 全量自查）、`v0.1.0` Release。剩余：把 `tests/` 改成
   pytest 形式、把 `StructureFirstChunker` 固化成稳定对外 API，并在 README 明确 `max_chars`
   是字符不是 token。

## 6. 关键文件

- `chinese_chunk/splitters.py`：四种策略与生产切片器。
- `chinese_chunk/textnorm.py`：规范化、分句、降级切分。
- `chinese_chunk/retrieval.py`：字符二元组 BM25。
- `chinese_chunk/evaluate.py` / `report.py` / `cli.py`：评测、报告、命令行。
- `scripts/fetch_corpus.py`：公开语料抓取与 manifest。
- `scripts/build_qa.py`：44 条问答生成与校验。
- `scripts/selfcheck.py`：全量自查。
- `tests/test_invariants.py`：切片不变量。
- `data/qa.jsonl`：问答集（已跟踪）。
- `data/corpus_manifest.json`：来源与哈希（已跟踪）。
- `NOTICE.md`：第三方引文的授权标注与移除方式（2026-11-14 新增）。
- `.github/workflows/ci.yml`：离线检查与全量自查。
- `reports/`：本地评测产物，被 gitignore。
- `out/`：切片 JSONL 导出，被 gitignore。

## 7. 常用命令

```
python scripts/selfcheck.py
python -m chinese_chunk.cli eval
python -m chinese_chunk.cli chunk --strategy structure --max-chars 600
python scripts/fetch_corpus.py --rebuild-manifest
```

## 8. 维护约束

- 指标并列或样本不足要直说，不推测、不夸大、不写讨好性结论。
- 不要重新把 `data/corpus/` 加入 git 跟踪。
- 修改切片逻辑后必须重跑不变量测试、自查和评测，并更新报告；引用块数时以当前磁盘文件为准。
