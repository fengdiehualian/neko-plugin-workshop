## 改了什么

<!-- 一句话说明这个 PR 做了什么；涉及多处改动时分条列出。 -->

## 为什么

<!-- 关联 issue 或实际痛点。没有 issue 就写清触发场景。 -->

## 怎么验证的

<!-- 贴真实命令与结果，不要只写「已测试」。 -->

```bash
bun test
python tools/syntax_check.py
python tools/static_check.py
```

- [ ] `bun test` 全绿
- [ ] `python tools/static_check.py` 全绿
- [ ] `python tools/syntax_check.py` 全绿

## 规则与编号（改到 `packs/neko-plugin/**` 时必填）

- [ ] 没有新增第二套规则编号；所有 R/W 号都能在 `packs/neko-plugin/rules.json` 里找到
- [ ] 改 `SKILL.md` / `AGENTS.md` 时，AGENTS 的 `- R<n>` 摘要与 `x_hard_rules` 一一对应
- [ ] 没有恢复 v0.9 已移除 API（`type=extension`、`[plugin.host]`、`plugin._types.result`、`plugin.sdk.extension` 等）

## 风险与边界

<!-- 哪些没验证、为什么没验证、需要什么环境才能验证。不写「应该没问题」。 -->

## 审查提示

- CodeRabbit 会自动审查；配置见 `.coderabbit.yaml`。
- 大段语料（`market-corpus/`）与打包产物不在自动审查范围内。
