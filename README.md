# ChunkJudgeBench

> Benchmark Chinese text chunking strategies (fixed-window, sentence, recursive-character, structure-first) under a fixed BM25 bigram retriever, comparing recall@k, MRR, quote/unit boundary integrity and chunk-size distribution.

中文文本切片（chunking）策略的同口径评测项目。在固定检索器的前提下，只改变切片方式，对比不同切片策略在检索证据完整性上的表现。

## 四种切片策略

| 策略 | 标识 | 说明 |
| --- | --- | --- |
| 固定窗口 | `fixed` | 固定字符窗口 + 重叠，最弱基线，只保证不超长。 |
| 按句打包 | `sentence` | 按句末标点打包，句边界完整，不看文档结构。 |
| 递归字符 | `recursive` | 段落 → 句末标点 → 分句标点 → 字符逐级降级切分。 |
| 结构优先 | `structure` | 识别标题层级 / 条款 / 表格 / 代码，结构边界内打包，超长单元再二次切分，保留标题路径与条款区间。 |

## 目录结构

```
chinese_chunk/
  textnorm.py    文本规范化、分句、token 计数、尾部重叠
  splitters.py   四种切片策略与统一 Chunk 结构
  retrieval.py    字符二元组 BM25 检索器（零外部分词依赖）
  evaluate.py    切片质量评测与指标统计
  report.py      由指标生成 Markdown 评测报告
  cli.py         命令行入口（chunk / eval）
scripts/
  fetch_corpus.py  下载公开中文语料，记录来源与内容哈希
  build_qa.py      从语料原文逐字截取引文，生成问答集
  selfcheck.py     全量自查（切片不变量、表格代码块、边界、可复现性、报告一致性）
data/
  corpus/         原始语料文本（不入库，需先运行抓取脚本；见「数据与许可」）
  corpus_manifest.json  语料来源、字符数、行数与 sha256
  qa.jsonl        评测问答集（question / gold_quote / doc / source_license / quote_sha256）
reports/          评测产物（results.csv、per_question.csv、chunk_benchmark.md，不入库）
tests/
  test_invariants.py  不变量自检（切片可定位、不超限、引文自洽；需要语料）
  test_smoke.py       离线冒烟（不需要语料，CI 用）
  test_external_chunks.py  外部 JSONL 切片的加载与评测（不需要语料）
.github/          CI 工作流、Issue 与 PR 模板、dependabot
pyproject.toml    打包与版本声明（pip install -e ".[fetch]"）
NOTICE.md         第三方引文的授权标注与移除方式
CONTRIBUTING.md   贡献流程与硬性约束
SECURITY.md       漏洞报告渠道与威胁面
requirements.txt  抓取语料用依赖（requests）
```

## 环境与依赖

- Python 3.10+
- 必装：`requests`（仅用于抓取语料），安装：`pip install -r requirements.txt`
- 可选：`tiktoken`（仅在 tokenizer 选 `tiktoken` 时需要）

其余功能均使用 Python 标准库，不依赖外部分词器。

两种安装方式（任选其一）：

```bash
# 方式一：可直接安装的包（含抓取依赖，并注册 chunkjudgebench 命令）
pip install -e ".[fetch]"

# 方式二：只装依赖清单
pip install -r requirements.txt
```

## 快速开始

在项目根目录执行：

```bash
# 1. 下载 / 校验语料（--force 强制重新抓取）
python scripts/fetch_corpus.py

# 2. 从原文生成评测问答集
python scripts/build_qa.py

# 3. 多策略、多尺寸评测并生成报告
python -m chinese_chunk.cli eval \
  --corpus data/corpus --qa data/qa.jsonl --out-dir reports

# 4. 单独导出某一策略的切片结果（JSONL）
python -m chinese_chunk.cli chunk \
  --corpus data/corpus --strategy structure --out out/chunks.jsonl
```

`eval` 常用参数：

- `--strategies`：逗号分隔，默认 `fixed,sentence,recursive,structure`
- `--sizes`：字符上限，默认 `400,600,900`
- `--overlap`：重叠字符数，默认 `80`
- `--tokenizer`：`char`（非空白字符数，默认）/ `cjk` / `tiktoken`

### 评测已经切好的块

传入 `--chunks` 时跳过内置切分，用同一套指标评测外部 JSONL。不传 `--chunks` 时仍走原来的策略 × 尺寸网格，默认评测数字不变。

```bash
python -m chinese_chunk.cli eval \
  --corpus data/corpus --qa data/qa.jsonl \
  --chunks out/aec_batch.jsonl --label aec-template-v1 \
  --max-chars 1200 --overlap 0 --out-dir reports
```

每行一个 JSON 对象，字段为 `doc`、`index`、`text`、`start_offset`、`end_offset`、`heading_path`。`heading_path` 可以是字符串数组，也可以是用 ` > ` 连接的字符串。本仓库 `chunk` 子命令写出的 `doc_id` / `seq` / `start` / `end` 也能读；可选 `kind`（`text` / `table` / `code`）和 `chunk_id`。

`--max-chars` 只参与超限率，以及「长度不超过上限 80%」的单元完整率，不会把外部块再切一次。不传时按最长块的字符数计。`--overlap` 只写入报告。`--strategies` 和 `--sizes` 在传入 `--chunks` 时不生效。

```python
from chinese_chunk.evaluate import evaluate_chunks, load_chunks_jsonl, load_corpus, load_qa

docs = load_corpus("data/corpus")
qa = load_qa("data/qa.jsonl")
chunks = load_chunks_jsonl("out/aec_batch.jsonl")
result = evaluate_chunks(docs, qa, chunks, label="aec-template-v1", max_chars=1200, overlap=0)
```

## 评测口径

- 检索器固定为字符二元组 BM25（k1=1.5，b=0.75），所有策略共用；评测只改变切片方式。
- 命中判定：引文去掉空白后完整出现在某个已召回块中。
- 指标包括 Recall@1/3/5、MRR、引文完整率、单元完整率、边界完整率、块大小分布（p50/p95/最大）、冗余率、标题元信息占比。
- 这些指标衡量「检索能否拿到完整证据」，不等于大模型最终回答的正确率。

## 评测结果摘要

在当前语料（3 篇 / 20.4 万字符）与 44 条问答上，`structure` + 600 字符上限的表现：

| 指标 | structure | recursive | fixed | sentence |
| --- | --- | --- | --- | --- |
| R@5 | 0.932 | 0.932 | 0.932 | 0.818 |
| MRR | 0.797 | 0.769 | 0.775 | 0.684 |
| 引文完整率 | 1.000 | 0.977 | 0.977 | 0.886 |
| 单元完整率 | 1.000 | 0.968 | 0.948 | 0.924 |
| 冗余率 | 0.042 | -0.001 | 0.153 | -0.002 |

召回率在 44 条样本上已饱和（并列 0.932），差异主要体现在确定性的完整性指标上：
structure 把所有 1260 个可容纳的结构单元都保住了，recursive 有 40 个被切断。
完整数据见 `reports/chunk_benchmark.md` 与 `reports/results.csv`。

## 数据与许可

- 自研代码以仓库根目录 LICENSE（MIT，Copyright (c) 2026 AmbOS-HK）为准。
- **第三方语料正文不入库**：`data/corpus/` 已在 `.gitignore` 中排除，克隆后须先运行
  `python scripts/fetch_corpus.py` 从原始来源下载，再用 `--rebuild-manifest` 核对哈希。
  原因：`rust-course` 仓库无可识别许可证（其 README 明示不可修改后再分发），
  `coding-interview-university` 为 CC-BY-SA-4.0（要求署名并以相同协议共享），
  两者都与本仓库的 MIT 许可不兼容，随仓库发布将构成未经许可的再分发。
- **但问答集里保留了逐字引文**：`data/qa.jsonl` 已入库，44 条 `gold_quote` 是从原文截取的
  片段（民法典 34 条 / CIU 8 条 / rust-course 2 条，合计 4372 字符）。这部分会随仓库公开，
  因此每条记录都带 `source_license`（`public-domain` / `CC-BY-SA-4.0` / `NOASSERTION`）
  与 `quote_sha256`，由 `scripts/selfcheck.py` 强制校验。各来源的署名、授权状态与移除方法
  见 [NOTICE.md](NOTICE.md)。
- `民法典` 为公有领域法律文本，可自由引用；本仓库只在本地评测中使用全文，同样不入库。
- 各文档来源 URL、字符数与哈希见 `data/corpus_manifest.json`。

## 已知局限

- 样本量小（当前 3 篇文档 / 44 条问答），结论只具方向性，不能外推为通用定论。
- 检索层仅用 BM25，未接入向量检索与 rerank；混合检索下策略排序可能变化。
- token 口径为非空白字符数，不等于任何具体模型的 token。
- 结构识别依赖 Markdown 标题与「第X条」正则；PDF / 扫描件需先做版面还原。
- 未做端到端回答质量评测（忠实度、答案正确率），本项目只覆盖检索证据层。

## 测试与 CI

```bash
python tests/test_smoke.py            # 离线冒烟，不需要语料
python tests/test_external_chunks.py  # 外部切片加载与评测，不需要语料
python tests/test_invariants.py       # 切片不变量，需要 data/corpus
python scripts/selfcheck.py           # 全量自查，需要 data/corpus
```

CI 分两个作业（`.github/workflows/ci.yml`）：

- **离线检查**：Python 3.10 / 3.13 双版本，编译 + 离线冒烟 + 外部切片评测 + 打包元数据 + CLI 可用性；
- **全量自查**：抓取语料后跑不变量与自查，产物上传为 artifact。因为语料抓取依赖外部
  站点，该作业只在推送 `main` 与手动触发时运行。

## 贡献与引用

- 贡献流程与硬性约束见 [CONTRIBUTING.md](CONTRIBUTING.md)，行为准则见
  [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)，漏洞报告见 [SECURITY.md](SECURITY.md)。
- 引用本项目时请注明仓库地址与所使用的提交或版本；评测结论请连同样本量与口径一起引用。
