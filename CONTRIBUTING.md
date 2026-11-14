# 参与贡献

感谢你关注 ChunkJudgeBench。这个仓库的目标很窄：**在固定检索器的前提下，比较中文
文本切片策略**。提交前请先确认你的改动落在这个范围内。

## 环境

- Python 3.10+（仓库在 3.13 上开发验证）
- 安装：`python -m pip install -e ".[fetch]"`（开发期加 `.[dev]` 装 pytest）
- 语料不入库，先跑：`python scripts/fetch_corpus.py`

## 提交前必须跑的三件事

```bash
python tests/test_smoke.py          # 离线冒烟（不需要语料）
python tests/test_invariants.py     # 切片不变量（需要本地 data/corpus）
python scripts/selfcheck.py         # 全量自查（需要本地 data/corpus；两条许可 WARN 属预期）
```

只要改动了 `chinese_chunk/splitters.py`、`textnorm.py`、`retrieve` 相关逻辑，
就必须重跑上面三项，并重跑 `python -m chinese_chunk.cli eval` 更新 `reports/`
（`reports/` 与 `out/` 不入库，但结论要同步到 README 的「评测结果摘要」）。

## 硬性约束（会被 review 直接打回）

1. **不提交第三方语料正文**。`data/corpus/` 必须保持被忽略状态；也不提交真实业务数据、
   凭据或本地绝对路径。自查关于 `rust-course` 与 `coding-interview-university` 的两条
   许可 WARN 是预期限制，不得靠提交语料正文来消除。问答集新增引文时必须同时补
   `source_license` 与 `quote_sha256`（见 [NOTICE.md](NOTICE.md)）。
2. **不夸大指标**。样本量、饱和、并列、反例都要写清楚；不得用 R@5 单独证明某策略更优。
3. **不改变评测口径**（命中判定、检索器参数、token 口径）而不在 PR 中说明原因与影响。
4. **不引入非标准库依赖**到核心路径（`chinese_chunk/`）。抓取语料可以用 `requests`。
5. 文本文件统一 UTF-8、LF（见 `.gitattributes`）。不要为了「好看」重排无关文件。

## 提交信息

格式：`<前缀>：<一句话说明>`

前缀用：`修复` / `新增` / `重构` / `测试` / `文档` / `构建`。示例：

```
修复：未闭合 HTML 表格兜底保留，避免整块丢失
新增：structure 策略支持 <h1> 标题识别
文档：补充第三方引文的授权标注
```

一次提交只做一件事；不要在一个提交里同时改逻辑、改格式、改文档。

## 分支与 PR

- `main` 为唯一长期分支。不直接推 `main` 是项目纪律：文档、功能与修复都走分支和 PR。
- 是否对 `main` 启用 GitHub 分支保护，由项目所有者决定。本文不修改这项仓库设置，
  也不宣称保护已经开启。撰写时（2026-11-14）`main` 的 `protected` 为 false；
  纪律和仓库设置是两件事。
- PR 描述里写：改了什么、为什么、怎么验证（贴关键输出）。
- PR 模板见 `.github/pull_request_template.md`。

## 空语料环境

`tests/test_smoke.py` 是离线冒烟，不需要语料，CI 的离线作业只跑它。
没有本地 `data/corpus/` 时，`tests/test_invariants.py` 与 `scripts/selfcheck.py` 会按设计失败；
这不是产品回归。`scripts/selfcheck.py` 关于 `rust-course`（无可识别许可证）和
`coding-interview-university`（CC-BY-SA-4.0）的两条 WARN 是预期的许可限制，
不得通过提交语料正文来消除。HANDOFF.md 里 2026-11-14 三项退出码为 0 的记录，
取自已经备好 `data/corpus/` 的环境。

## 许可

提交即表示你同意以本仓 MIT 许可提供你的贡献（第三方材料除外，见 NOTICE.md）。
