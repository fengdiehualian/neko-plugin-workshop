[plugin]
id = "{{PLUGIN_ID}}"
name = "{{PLUGIN_NAME}}"
version = "0.1.0"
type = "plugin"
description = "定时提醒模板:添加一条提醒,到点主动推送到对话;支持检查间隔与附加问候语配置,推送失败自动重试不丢提醒。"
short_description = "定时提醒:到点主动推送"
keywords = ["提醒", "定时", "倒计时", "喝水", "闹钟", "备忘", "reminder", "notify", "推送"]
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
