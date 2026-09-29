[plugin]
id = "{{PLUGIN_ID}}"
name = "{{PLUGIN_NAME}}"
version = "0.1.0"
type = "plugin"
entry = "plugin.plugins.{{PLUGIN_ID}}:{{CLASS_NAME}}Plugin"

[plugin.author]
name = "N.E.K.O. Workbench"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = true
timeout = 60
startup_failure = "warn"
