## 改了什么

<!-- 一句话说明，以及涉及的文件 -->

## 为什么

<!-- 关联 issue：Fixes #... -->

## 怎么验证的

- [ ] `python tests/test_smoke.py`
- [ ] `python tests/test_invariants.py`（需要 `data/corpus`）
- [ ] `python scripts/selfcheck.py`
- [ ] 改了切片逻辑时重跑 `python -m chinese_chunk.cli eval` 并更新 README 的「评测结果摘要」

关键输出：

```
（贴 [OK] 行或指标变化，不要贴整屏日志）
```

## 自查

- [ ] 没有提交第三方语料正文（`data/corpus/` 仍被忽略）
- [ ] 新增问答引文时补了 `source_license` 与 `quote_sha256`
- [ ] 结论没有夸大：样本量、饱和、并列、反例都写清楚
- [ ] 文本文件为 UTF-8、LF；没有顺手重排无关文件
