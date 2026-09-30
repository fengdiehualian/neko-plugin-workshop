[plugin]
id = "{{PLUGIN_ID}}"
name = "{{PLUGIN_NAME}}"
version = "0.1.0"
type = "plugin"
description = "最简单的入门模板:提供 hello 打招呼入口并返回问候语,适合第一次生成插件、验证安装链路。"
short_description = "入门模板:打个招呼"
keywords = ["hello", "你好", "打招呼", "问候", "入门", "模板", "greet"]
entry = "plugin.plugins.{{PLUGIN_ID}}:{{CLASS_NAME}}Plugin"

[plugin.author]
name = "{{PLUGIN_AUTHOR}}"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = true
timeout = 60
startup_failure = "warn"
