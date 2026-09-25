# N.E.K.O 战舰世界猫娘陪玩

`neko_wows` 是 [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) 的《战舰世界》陪玩插件。它读取本机 `8111_for_wows` 提供的战局遥测，将连续快照整理成战斗事件，再交给当前猫娘进行有优先级的主动播报。

## 功能与边界

- 读取自身状态、已加载舰船、伤害、地图等本地战局遥测。
- 识别低血量、短时高额受伤、近距离威胁、以少打多等事件，并控制播报优先级与频率。
- 默认可复用 N.E.K.O 宿主已发送给模型的近期共享屏幕帧；该帧总线由多个插件共享，并非插件隔离。可在插件设置中关闭。
- 支持战术文档、提示词、播报偏好、诊断和 dry-run 预览。
- 不读取游戏内存，不修改网络封包，不控制键鼠，也不会自动操作游戏。

## 使用前准备

本插件目前配合 WG 国际服《战舰世界》和 [`8111_for_wows`](https://github.com/Tz-WIND/8111_for_wows) 使用。完整链路由三部分组成：

```text
游戏内 WowsExtractor Mod → 本机 8111_for_wows Server → N.E.K.O 插件
```

请先准备：

- 已安装并能正常运行的 N.E.K.O；
- WG 国际服 `World_of_Warships` 游戏客户端；
- Windows 上可用的 [`uv`](https://docs.astral.sh/uv/getting-started/installation/)；
- `PnFModsLoader.py`。如果游戏的 `res_mods` 目录中已经有该文件，可直接复用。

## 安装教程

### 1. 从 N.E.K.O 插件市场安装插件

1. 打开 N.E.K.O 的插件管理/插件市场。
2. 搜索“战舰世界猫娘陪玩”或插件 ID `neko_wows`。
3. 安装插件，并在插件管理中启用、启动它。

插件本体安装完成后仍不会立即获得战局数据；还需要继续安装下面的游戏内 Mod 和本地 Server。

### 2. 下载并安装 `8111_for_wows` Server

用 Git 克隆仓库：

```powershell
git clone https://github.com/Tz-WIND/8111_for_wows.git
cd 8111_for_wows
uv sync --no-dev
```

也可以在 [`8111_for_wows` 仓库页面](https://github.com/Tz-WIND/8111_for_wows) 选择 **Code → Download ZIP**，解压后进入仓库根目录执行：

```powershell
uv sync --no-dev
```

建议将仓库放在固定位置，例如 `D:\Tools\8111_for_wows`；插件自动启动 Server 时会使用这个目录。

### 3. 安装游戏内 `WowsExtractor` Mod

1. 打开游戏安装目录，进入当前最新版本的：

   ```text
   World_of_Warships\bin\<最新 build 数字>\res_mods\
   ```

2. 确认 `res_mods` 下存在 `PnFModsLoader.py`。如果没有，请从现成的 Unbound2 Mod 包（如 TTaroTeamPanel、StreamerMode）或 Wargaming ModsSDK 中取得一份。
3. 将 `8111_for_wows` 仓库中的整个目录：

   ```text
   mod\PnFMods\WowsExtractor\
   ```

   复制到：

   ```text
   World_of_Warships\bin\<最新 build 数字>\res_mods\PnFMods\WowsExtractor\
   ```

4. 不要只复制 `Main.py`，也不要把整个 `8111_for_wows` 仓库塞进 `res_mods`。完成后的关键结构应为：

   ```text
   res_mods\
   ├─ PnFModsLoader.py
   └─ PnFMods\
      └─ WowsExtractor\
         ├─ Main.py
         ├─ battle_identity.py
         ├─ emit_guard.py
         └─ config.example.ini
   ```

5. 可选：在游戏内的 `WowsExtractor` 目录中，将 `config.example.ini` 复制为 `config.ini`。默认约每 `0.1` 秒写入一次；若想降低频率，可把 `state_interval` 调为 `0.15` 或 `0.2`。

启动游戏并进入任意战斗（训练房即可）。在游戏目录的 `python.log` 中搜索：

```text
[WowsExtractor] writing telemetry to:
```

对应目录中应出现开局生成的 `meta.json`，以及持续更新的 `state.json`。

> 游戏更新后，`bin` 下的最新 build 数字通常会变化。届时需要把 `PnFModsLoader.py` 和 `WowsExtractor` 重新复制到新的 `res_mods` 目录。

### 4. 连接并启动 Server

推荐让插件托管 Server：

1. 打开“战舰世界猫娘陪玩”面板的“概览”页。
2. “地址”保持 `http://127.0.0.1:8111`。
3. “服务源码目录”选择 `8111_for_wows` 的仓库根目录，例如 `D:/Tools/8111_for_wows`。
4. “游戏目录”选择 `World_of_Warships` 的安装根目录，不要选择某个具体的 `bin/<build>` 目录。
5. 点击“保存并重连”。插件会自动启动本地 Server，并在插件停止时只关闭自己启动的进程。

也可以手动启动 Server：

1. 在 `8111_for_wows` 仓库根目录将 `config.example.ini` 复制为 `config.ini`。
2. 编辑 `config.ini`，把 `game_dir` 指向 `World_of_Warships` 安装根目录。
3. 双击 `run_server.bat`，或在仓库根目录执行：

   ```powershell
   uv run --no-dev python server/server.py
   ```

4. 在插件“概览”页只填写地址 `http://127.0.0.1:8111`，将“服务源码目录”留空，然后保存并重连。插件会复用外部 Server，不会将它关闭。

### 5. 验证连接

1. 浏览器打开 <http://127.0.0.1:8111/healthz>。响应中的 `serviceId` 应为 `8111_for_wows`。
2. 进入一场战斗，确认 `state.json` 持续更新。
3. 回到插件“概览”页，确认数据源已连接、战局状态为实时，并能看到不断增长的帧数与事件数。

## 常见问题

### 没有生成 `state.json`

- 确认复制目标是最新 build 下的 `res_mods/PnFMods/WowsExtractor/`。
- 确认 `res_mods` 根目录存在 `PnFModsLoader.py`。
- 检查 `python.log` 中是否出现 `[WowsExtractor] loaded` 或报错信息。
- 游戏更新后重新复制 Mod。

### Server 找不到战局文件

- “游戏目录”或 Server 配置中的 `game_dir` 应指向 `World_of_Warships` 安装根目录。
- 以 `python.log` 中 `writing telemetry to:` 后显示的绝对路径为准。
- 确认已经进入战斗；港口中没有实时战局文件是正常的。

### 8111 端口被占用

War Thunder 的遥测也会使用 `8111`。先确认占用端口的进程；需要换端口时，同时修改 `8111_for_wows` Server 和插件“概览”页中的地址，例如改为 `http://127.0.0.1:8125`。

### 插件无法自动启动 Server

- 在命令行执行 `uv --version`，确认 `uv` 已安装并加入 `PATH`。
- 确认“服务源码目录”中存在 `server/server.py`。
- 在 `8111_for_wows` 根目录重新执行 `uv sync --no-dev`。
- 如果连续启动失败触发自动暂停，请先修正错误，再在插件面板中恢复或手动重连。

更多采集器配置、接口和排障信息见 [`8111_for_wows` 文档](https://github.com/Tz-WIND/8111_for_wows#readme)。

## 隐私与使用说明

- 遥测来自游戏官方 ModsAPI，只包含玩家本来可以在游戏界面中看到或已经加载的数据，不提供隐藏敌情。
- `live_vision_enabled` 默认开启时，插件可读取 N.E.K.O 宿主全局共享帧总线中近期的屏幕帧；该总线跨插件共享，不是按插件隔离。此复用路径本身不会截图或保存帧，可在插件设置中关闭。
- 主动截图功能默认关闭。若手动开启，屏幕图像会写入插件数据目录并发送给所配置的模型服务商。
- Mod 与第三方工具的使用请遵守所在服务器的规则，相关风险由使用者自行承担。

## 开发

建议将本仓库与 N.E.K.O 主仓库放在同一目录下：

```text
workspace/
├─ N.E.K.O/
└─ N.E.K.O_plugin_neko_wows/
```

在本仓库根目录运行测试：

```powershell
uv run --project ../N.E.K.O pytest -q
```

校验并构建插件包：

```powershell
uv run --project ../N.E.K.O neko-plugin check -r .
uv run --project ../N.E.K.O neko-plugin build .
```

发布由版本标签或手动触发的 GitHub Actions 工作流完成。

## 仓库结构

- `neko_wows/`：插件运行时代码、界面和本地化资源；
- `tests/`：单元测试与回放测试；
- `scripts/`：离线舰船目录构建工具；
- `plugin.toml`：N.E.K.O 插件清单与默认配置。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。
