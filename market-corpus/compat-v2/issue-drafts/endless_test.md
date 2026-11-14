# [草稿,未发布] 无尽测试 与猫爪重写 #3683 的兼容点

> 本文件由 tools/gen_compat_report.py 自动生成,尚未提交.
> 发布前必须由维护者人工核实下面每一行确实中招,再发到 https://github.com/tangbai-ER/n.e.k.o_plugin_endless_test 的 Issues.

目标仓库: https://github.com/tangbai-ER/n.e.k.o_plugin_endless_test
依据: RFC https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 第 4.2「会删除」/ 第 5 节自查

## 建议的 issue 正文

标题: [兼容性] 猫爪重写 #3683:本插件命中以下静态特征,请自查

参考: https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683

以下位置使用了将删除的内部模块/接口/随包依赖(扫描日期 2026-11-14):

- `__init__.py:53` (host_internal_import): `import brain  # type: ignore`

## 处理建议

- host_internal_import: 改为只用公开接口(/runs HTTP 或 plugin.sdk.*);brain.* / app.agent_server.* 是宿主内部模块,已在 PR3 删除或将随 PR4 删除.

行为项(触发时机/入口被使魔调用/取消中断)静态扫描证不了,请一并按 RFC 第 5 节自查.

## 证据(全部命中,含仅文档/测试面)

- [runtime] host_internal_import :: `__init__.py:53` :: `import brain  # type: ignore`