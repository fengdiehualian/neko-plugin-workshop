# [草稿,未发布] Galgame游玩助手 与猫爪重写 #3683 的兼容点

> 本文件由 tools/gen_compat_report.py 自动生成,尚未提交。
> 发布前必须由维护者逐条人工核实下面每一行属实,再发到 https://github.com/MomiJiSan/n.e.k.o_plugin_galgame_plugin 的 Issues。批量自动发=骚扰,禁止。

目标仓库: https://github.com/MomiJiSan/n.e.k.o_plugin_galgame_plugin
依据: RFC https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 第 4.2「会删除」/ 第 5 节自查
扫描源码版本: commit d3748e4

## 建议的 issue 正文

标题: [兼容性] 猫爪重写 #3683:本插件命中以下会删面,请自查

参考: https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683

以下位置使用了将删除的内部模块/接口/随包依赖(扫描日期 2026-11-14,均为运行时代码):

- `host_agent_adapter.py:171` (deleted_endpoint): `"/computer_use/run",`

## 处理建议

- deleted_endpoint: 停止请求 4.2 列出的具名接口;可用接口以 main 分支 docs/zh-CN/api/ 为准。

行为项(触发时机/入口被使魔调用/取消中断)静态扫描证不了,请一并按 RFC 第 5 节自查。

## 证据(全部命中;待核类不要求改动,仅备查)

- [review/runtime] legacy_agent_port :: `host_agent_adapter.py:13` :: `_TOOL_SERVER_PORT = 48915`
- [strong/runtime] deleted_endpoint :: `host_agent_adapter.py:171` :: `"/computer_use/run",`
- [review/dev] legacy_agent_port :: `tests/migrated/test_galgame_host_agent_adapter.py:31` :: `assert _tls_verify_for_base_url("http://127.0.0.1:48915") is False`
- [review/dev] legacy_agent_port :: `tests/migrated/test_galgame_host_agent_adapter.py:32` :: `assert _tls_verify_for_base_url("https://localhost:48915") is False`
- [strong/dev] deleted_endpoint :: `tests/migrated/test_galgame_host_agent_adapter.py:46` :: `@app.post("/computer_use/run")`
- [review/dev] legacy_agent_port :: `tests/migrated/test_galgame_host_agent_adapter.py:61` :: `adapter = HostAgentAdapter(_Logger(), tool_server_port=48915)`
- [review/dev] legacy_agent_port :: `tests/migrated/test_galgame_host_agent_adapter.py:86` :: `adapter = HostAgentAdapter(_Logger(), tool_server_port=48915)`
- [review/dev] legacy_agent_port :: `tests/migrated/test_galgame_host_agent_adapter.py:107` :: `adapter = HostAgentAdapter(_Logger(), tool_server_port=48915)`