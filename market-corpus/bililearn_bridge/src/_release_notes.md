# N.E.K.O 桥接插件 v1.0.0

桥接独立程序 **BiliLearn（B站 AI 学习机器人）** 的 exe，使其能被 N.E.K.O 猫娘拉起并控制。

## 功能
- 宿主启动 N.E.K.O 时，按配置以无头 `--serve` 模式拉起 BiliLearn（不弹浏览器面板、不建系统托盘）
- 提供入口：`start` / `stop` / `restart` / `status` / `open_panel` / `bot_start` / `bot_stop` / `bot_status`
- 通过 BiliLearn 本地 Web API（默认 `127.0.0.1:18083`）查询与控制
- 纯标准库实现，零第三方依赖

## 用法
1. 把本插件目录放入 N.E.K.O 的插件目录
2. 在 `config.toml` 填 `exe_path`（或让它自动在 `dist/BiliLearn Web/` 查找 BiliLearn exe）
3. 重启 `agent_server`，即可由猫娘桥接管理

详见 `README.md`。
