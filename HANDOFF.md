# ChunkJudgeBench 维护与 Grok Bot 交接说明

更新时间：2026-11-14
工作目录：`D:\ChunkJudgeBench`
远端仓库：`https://github.com/AmbOS-HK/ChunkJudgeBench.git`（公开）
交接基线：`main = 5997d78`，发布标签：`v0.1.0`

## 0. 角色与交接方式

- 项目所有者：决定范围、数据、模型和发布事项。
- Grok Bot：按本文任务顺序施工，每个任务单独建分支、单独提 PR。
- 豆包：负责规划、查检与验收；Grok Bot 完成一个 PR 后停止继续施工，等待验收。
- 本仓与 `ChineseChunkJudge` 并存且独立，不是新旧替代关系；不要把两个仓的代码、历史或任务混用。
- 只允许普通提交和 PR。禁止直接推 `main`、强推、改写历史、删库、创建 Release 或擅自修改发布标签。
- 文档、功能与修复都走分支和 PR。`main` 已保护，禁止直推 `main`。全量联网自查不是必过检查。

## 1. 项目定位（不要扩大范围）

- 中文知识库**切片策略评测工具**：比较 `fixed` / `sentence` / `recursive` / `structure` 四种策略，输出可复现指标。
- **生产切片器原型**：为知识库切片环节提供算法参考与 JSONL 输出格式。
- 数据只用**公开中文语料**搭骨架；不接真实业务数据，不面向公众提供服务。
- 本项目不负责检索服务、生成回答或业务流程合规审查，不写成业务系统或合规项目。

## 2. 当前状态

- 语言：Python 3.13，核心逻辑只用标准库；联网抓语料用 `requests`。
- `data/corpus/`、`out/`、`reports/` 已在 `.gitignore` 中，语料不入库，本地文件保留。
- 三条检查对语料的要求不同，全新克隆不能把后两项的失败当成产品回归：
  - `tests/test_smoke.py` 是离线冒烟，不需要语料。CI 的离线作业只跑它。
  - `tests/test_invariants.py` 与 `scripts/selfcheck.py` 需要本地已经抓取的 `data/corpus/`（`python scripts/fetch_corpus.py`）。没有这份本地语料时，这两项按设计失败。
- 全量自查（切片不变量、表格/代码块识别、边界异常输入、可复现性、报告一致性）在已经备好 `data/corpus/` 的环境中通过。
- 2026-11-14 交接前实测：`tests/test_smoke.py`、`tests/test_invariants.py`、`scripts/selfcheck.py`
  全部退出码为 0；17 项自查 0 FAIL。该记录取自已经备好 `data/corpus/` 的环境，无语料的全新克隆不能原样复现。
- 该次自查的两条 WARN 是预期中的第三方语料许可限制：`rust-course` 无可识别许可证，`coding-interview-university` 为 CC-BY-SA-4.0。不得通过提交语料正文来“消除”这两条 WARN。
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

## 5. Grok Bot 拉取与开工

```bash
git clone https://github.com/AmbOS-HK/ChunkJudgeBench.git
cd ChunkJudgeBench
git switch -c grok/pytest-migration origin/main
python -m pip install -e ".[fetch,dev]"
python scripts/fetch_corpus.py
python tests/test_smoke.py
python tests/test_invariants.py
python scripts/selfcheck.py
```

先确认基线通过再改代码。其中 `tests/test_smoke.py` 不需要语料；
`tests/test_invariants.py` 与 `scripts/selfcheck.py` 依赖刚刚抓到的本地 `data/corpus/`。
没有这份语料时，后两项失败是预期，不是产品回归。语料抓取依赖外部站点；若失败，记录真实错误并停止，
不得换用未经核实的镜像或伪造语料，也不得把语料正文提交进仓库来让自查变绿。
自查里关于 `rust-course` 与 `coding-interview-university` 的两条许可 WARN 是已知限制，
不得通过提交语料正文来消除。

## 6. 施工顺序与验收标准

### PR-01：迁移到 pytest（首个任务，只做这一项）

- 把 `tests/test_smoke.py` 与 `tests/test_invariants.py` 的断言迁移为 pytest 测试，保持原有测试覆盖，不改切片与检索行为。
- 更新 CI 与文档中的测试命令；无语料环境仍可单独运行离线测试。
- 验收：`pytest -q tests/test_smoke.py` 通过；有语料时 `pytest -q` 与 `python scripts/selfcheck.py` 通过；CI 配置与 README、CONTRIBUTING 不互相矛盾。
- 完成后提交 PR 并停止，不要顺带做 API、切片算法或格式化改造。

### PR-02：固化公开切片 API（PR-01 验收后）

- 明确包级公开入口、`Chunk` 返回结构和兼容性约定，保持现有 CLI 可用。
- README 明确 `max_chars` 是字符口径而非模型 token，并说明结构标题前缀与正文上限的关系。
- 验收：公开 API 有直接测试；全新克隆安装后可按 README 示例调用；不改变现有评测数字。

### PR-03：同一父节点的小节点合并（PR-02 验收后）

- 只合并同一父结构下的小节点，不跨标题、不拆表格/代码原子块、不破坏正文回溯与块上限。
- 新增碎列表、连续短标题、长短混合单元的回归测试。
- 验收：列出合并前后块数与指标差异；三种测试/自查全过；若指标变化，同步 README 摘要并解释原因。

### PR-04：HTML 标题与列表内嵌标题识别（PR-03 验收后）

- 覆盖 `<h1>` 至 `<h6>`、列表项中的 Markdown 标题，以及混合 Markdown/HTML 文档。
- 保持 HTML 表格与代码块原子化，不向核心路径引入第三方依赖。
- 验收：新增正反对例；三种测试/自查全过；报告任何不能可靠识别的结构，不宣称支持完整 HTML 解析。

### 等待项目所有者输入，不得擅自施工

1. 真实业务语料与 100 条以上问答：等待所有者提供数据和授权，不得自行收集或编造。
2. embedding、rerank 与端到端回答评测：等待所有者指定模型、服务方式、数据及验收指标。
3. PDF/扫描件：等待所有者在 MinerU、Docling 或其他解析器之间作出选择；解析依赖不得进入核心包。

## 7. 关键文件

- `chinese_chunk/splitters.py`：四种策略与生产切片器。
- `chinese_chunk/textnorm.py`：规范化、分句、降级切分。
- `chinese_chunk/retrieval.py`：字符二元组 BM25。
- `chinese_chunk/evaluate.py` / `report.py` / `cli.py`：评测、报告、命令行。
- `scripts/fetch_corpus.py`：公开语料抓取与 manifest。
- `scripts/build_qa.py`：44 条问答生成与校验。
- `scripts/selfcheck.py`：全量自查（需要本地 `data/corpus/`；两条许可 WARN 属预期）。
- `tests/test_invariants.py`：切片不变量（需要本地 `data/corpus/`）。
- `tests/test_smoke.py`：不依赖语料的离线冒烟。
- `data/qa.jsonl`：问答集（已跟踪）。
- `data/corpus_manifest.json`：来源与哈希（已跟踪）。
- `NOTICE.md`：第三方引文的授权标注与移除方式。
- `.github/workflows/ci.yml`：离线检查与全量自查。
- `reports/`、`out/`：本地评测产物，被 gitignore，不随 PR 提交。

## 8. 修改切片逻辑后的必跑命令

```bash
pytest -q
python -m chinese_chunk.cli eval
python scripts/selfcheck.py
```

`reports/` 与 `out/` 虽不入库，但若摘要数字变化，必须把变化写入 PR 和 README。

## 9. PR 交付格式与维护约束

PR 描述必须包含：

1. 任务编号、分支名、改动文件和改动原因。
2. 实际执行的命令、退出结果和关键输出，不得只写“已测试”。
3. 修改算法时的前后指标对比；无变化也要明确写“指标未变”。
4. 未解决项、外部依赖和需要所有者决定的问题。

硬性约束：

- 指标并列或样本不足要直说，不推测、不夸大、不写讨好性结论。
- 不提交 `data/corpus/`、第三方语料正文、真实业务数据、凭据或本地绝对路径。
  自查的两条许可 WARN 是已知限制，不得靠提交语料正文来消除。
- 新增问答引文必须同时补 `source_license` 与 `quote_sha256`；含原文引文的逐题报告不得公开再分发。
- 核心路径只使用标准库；不得擅自改变检索器参数、命中判定、token 口径或基线数据。
- 文本统一 UTF-8、LF；不做无关重排，不修改与本任务无关的文件。
- 不宣称“生产成熟”“全面最优”或“符合国家标准”；本项目只覆盖检索证据层。
