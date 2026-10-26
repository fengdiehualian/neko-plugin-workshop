# 从心开始桥接

这是 `NEKO-FromTheHeart-DEMO` 的独立 N.E.K.O 插件。第一阶段只提供一个 `resolve_interaction` 入口，通过 N.E.K.O `/runs` 接收一个受版本合同约束的对白槽位请求。

## 边界

插件不修改游戏剧情、好感度、变量、结局、存档或长期记忆，也不操作外部游戏窗口。游戏仍然是这些状态的唯一权威；插件不可用时游戏使用本地 fallback。

第一阶段不启用动态 CG、中央 CG 服务、worker、SQLite 队列或插件 UI。

## 兼容性

```text
plugin version: 0.1.x
game id: from_the_heart
game version: 0.5.0-demo
node contract: 1.2.4
```

合同副本位于 `contracts/ch2_restaurant_favorite_food.v2.json`。合同版本、资源哈希、允许的反应和 `cache_miss_fallback` 必须与游戏端同步；不兼容时插件拒绝请求。

## 本地开发

### 从 GitHub 加载

将仓库放到 N.E.K.O 的插件源码目录后，在 N.E.K.O 源码根目录运行：

```powershell
git clone https://github.com/MomiJiSan/n.e.k.o_plugin_from_the_heart.git plugin/plugins/from_the_heart
uv run neko-plugin check from_the_heart --strict
```

也可以把本仓库目录注册为开发插件，具体路径以当前 N.E.K.O 的开发模式配置为准。插件目录必须直接包含 `plugin.toml`。

### 启动和停止

第一阶段不自动启动插件。启动 N.E.K.O 插件服务后，手动启动：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:48916/plugin/from_the_heart/start
```

停止插件或回滚到游戏端 fallback：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:48916/plugin/from_the_heart/stop
```

游戏端使用：

```text
NEKO_PLUGIN_SERVER_ORIGIN=http://127.0.0.1:48916
```

插件停止、服务不可达或合同不兼容时，游戏继续使用本地 fallback，不要求联网才能推进主线。

### 本地检查

在 N.E.K.O 源码根目录运行：

```powershell
uv run neko-plugin check from_the_heart --strict
uv run pytest -q plugin/plugins/from_the_heart/tests
```

构建本地包时建议关闭 Python 字节码写入，避免缓存文件进入临时包：

```powershell
$env:PYTHONDONTWRITEBYTECODE="1"
uv run neko-plugin build from_the_heart -o .\from_the_heart.neko-plugin
```

这只是本地验收包，不代表已经创建 Release。

GitHub CI 还会在构建后检查 `.neko-plugin` 内容，拒绝 `__pycache__`、`.pyc`、`.pyo` 和路径穿越项。该守卫只检查插件产物，不修改 N.E.K.O 构建器；如果构建器在 metadata probe 后重新生成缓存，CI 会直接失败。

### 运行时兼容边界

入口类只依赖公开插件 SDK。对白模型路径目前还使用 N.E.K.O 宿主提供的 `conversation` 配置和模型工具模块，因此插件需要在兼容的 N.E.K.O 插件宿主中运行；它不能在没有 N.E.K.O 宿主的纯 Python 环境中独立启动。插件不会导入游戏代码，也不会访问游戏存档目录。

完整验证记录见 [`VALIDATION.md`](VALIDATION.md)。

插件默认 `auto_start=false`，游戏端不会负责启动或停止插件。
