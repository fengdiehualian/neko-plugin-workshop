# BiliLearn 桥接插件（N.E.K.O）

让 N.E.K.O 猫娘桥接**独立的** BiliLearn（B站 AI 学习机器人）程序。

## 它能做什么

- **拉起独立程序**：宿主启动 N.E.K.O 时，按配置以无头模式（`--serve`）拉起 `BiliLearn Web.exe`。
  启动**不弹出面板**（默认仅后台运行，托盘入口由 exe 自己提供）。
- **桥接 Web API**：通过 BiliLearn 自带的本地 HTTP 接口（`http://127.0.0.1:<port>`）
  查询与控制：健康探测、机器人启动/停止、运行状态摘要。
- **手动打开面板**：提供「打开面板」入口，猫娘/用户在需要时再弹出网页面板。

## 入口（plugin_entry）

| id | 名称 | 说明 |
|----|------|------|
| `start` | 启动 BiliLearn | 确保程序运行（已在运行则附接，否则无头拉起） |
| `stop` | 停止 BiliLearn | 仅停止本插件拉起的进程 |
| `restart` | 重启 BiliLearn | 停止后重新拉起 |
| `status` | 状态 | 是否运行、端口、地址、摘要 |
| `open_panel` | 打开面板 | 在浏览器弹出网页面板（手动触发） |
| `bot_start` | 启动机器人 | 启动刷视频/学习机器人（auto/normal/lite） |
| `bot_stop` | 停止机器人 | 停止主循环 |
| `bot_status` | 机器人状态 | 运行状态/模式/输出摘要 |

## 配置（config.toml）

```toml
[bililearn_bridge]
exe_path = ""        # BiliLearn Web.exe 路径；留空自动搜索
port = 18083         # Web 面板端口
launch_mode = "serve" # serve=无头服务器; silent=无头无托盘
auto_launch = true   # N.E.K.O 启动时自动拉起
```

## 前置

1. 先把 `bilibili_learning_bot` 按本项目说明构建为 `BiliLearn Web.exe`
   （`python -m PyInstaller BiliLearn.spec`）。
2. 在插件配置里填好 `exe_path`（或放到常见位置由插件自动发现）。

## 依赖

无。仅使用 Python 标准库，可直接随 N.E.K.O 加载。
