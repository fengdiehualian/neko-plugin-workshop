# [草稿,未发布] 心理测评室 与猫爪重写 #3683 的兼容点

> 本文件由 tools/gen_compat_report.py 自动生成,尚未提交.
> 发布前必须由维护者人工核实下面每一行确实中招,再发到 https://github.com/mengyaoyue/n.e.k.o_plugin_neko_assessment 的 Issues.

目标仓库: https://github.com/mengyaoyue/n.e.k.o_plugin_neko_assessment
依据: RFC https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 第 4.2「会删除」/ 第 5 节自查

## 建议的 issue 正文

标题: [兼容性] 猫爪重写 #3683:本插件命中以下静态特征,请自查

参考: https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683

以下位置使用了将删除的内部模块/接口/随包依赖(扫描日期 2026-11-14):

- `_yui_link.py:166` (deleted_endpoint): `_FALLBACK_PORTS = (48912, 48911, 48913, 48910, 48914, 48915, 48916, 48917)`

## 处理建议

- deleted_endpoint: 停止请求 48915 上的旧接口;新健康检查/接口以 main 分支 docs/zh-CN/api/ 为准.

行为项(触发时机/入口被使魔调用/取消中断)静态扫描证不了,请一并按 RFC 第 5 节自查.

## 证据(全部命中,含仅文档/测试面)

- [runtime] deleted_endpoint :: `_yui_link.py:166` :: `_FALLBACK_PORTS = (48912, 48911, 48913, 48910, 48914, 48915, 48916, 48917)`