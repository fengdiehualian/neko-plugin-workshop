[plugin]
id = "{{PLUGIN_ID}}"
name = "{{PLUGIN_NAME}}"
version = "0.1.0"
type = "plugin"
description = "本地便签模板:记录与搜索笔记,数据保存在本机插件数据目录;原子写入,损坏文件自动备份不丢数据。"
short_description = "本地便签:记点和搜点"
keywords = ["笔记", "便签", "记录", "搜索", "备忘", "收藏", "notes", "memo"]
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
