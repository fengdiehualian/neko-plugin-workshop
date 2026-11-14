# [草稿,未发布] Galgame游玩助手 与猫爪重写 #3683 的兼容点(确实已坏(切换点 #3705 已合并))

> 自动生成,尚未提交。发布前逐条人工核实文件/行号属实——批量自动发=骚扰,禁止。
目标仓库: https://github.com/MomiJiSan/n.e.k.o_plugin_galgame_plugin · 扫描源码: commit d3748e4 · 日期 2026-11-14
依据: https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 4.2/第 5 节

---

标题建议: [兼容性] 猫爪重写 #3683:运行时代码命中已删/将删面(确实已坏(切换点 #3705 已合并))

正文建议:

1. `host_agent_adapter.py:171` (deleted_endpoint): `"/computer_use/run",`

逐条处理建议:

- deleted_endpoint: 命中的路由已随 #3705 从 main 删除(回归测试 tests/test_agent_rewrite_regression.py 断言其不存在),请求会 404;48915 端口与 /health 本身仍在,勿整体下结论。可改走自带兜底输入、或等猫爪使魔的 computer 能力,或在 #3683 讨论。
注意:48915 端口与 /health 本身仍在,勿写「端口要删」或「插件整体坏」;带兜底(degrade)的命中不要求必须改。
若同时依赖 GET/POST /api/agent/tasks/{id} 轮询契约:路由仍在,但 #3714 后委托号与 Run 编号合并可能影响用法,建议合并后复测。


待核项(不构成 issue 理由,复核时顺带确认):
- `host_agent_adapter.py:13` legacy_agent_port — 只命中裸 48915;若仅访问 /health 或探测端口列表,无需改动。
- `tests/migrated/test_galgame_host_agent_adapter.py:31` legacy_agent_port — 只命中裸 48915;若仅访问 /health 或探测端口列表,无需改动。
- `tests/migrated/test_galgame_host_agent_adapter.py:32` legacy_agent_port — 只命中裸 48915;若仅访问 /health 或探测端口列表,无需改动。
- `tests/migrated/test_galgame_host_agent_adapter.py:61` legacy_agent_port — 只命中裸 48915;若仅访问 /health 或探测端口列表,无需改动。