# 发布到 N.E.K.O 插件市场（source_player）

这份文档记录 `source_player`（半条命 & 传送门 陪玩）**已经验证到什么程度**，
以及把 v1.0.0 发布到 [N.E.K.O 插件市场](https://market.project-neko.cn) 需要谁做什么。

> 市场流程分两段：**首次审核**在网页提交（需要你的市场账号登录），
> 审核通过后 `neko-plugin publish` 才会创建 GitHub Release 并在市场生成可安装版本。
> `publish` 不能替代首次投稿。

---

## 1. 已经做完并且可复现的验证

在 N.E.K.O 源码目录（`N.E.K.O/`）用官方 CLI 跑的真实结果：

```text
check --release --market-release            passed（0 error / 0 warning）
  version=1.0.0
  payload_hash_verified=True
  tests=passed
ruff（CI 同参数 E4,E7,E9,F,I）              All checks passed
解包后按宿主路径 import + startup            6 entries / 12 tools，status=waiting
活体宿主机（Steam 版 N.E.K.O）实测           插件启动成功，selfcheck 三个后端全部出图
```

> 每次构建的确切 `package_sha256` / `payload_hash` 由 `check -r` 打印；市场侧会以
> GitHub Release 中的 `market-evidence.json` 为准，不要手工抄写哈希。

活体宿主机上调用的只读工具结果：

| 工具 | 结果 |
|------|------|
| `source_selfcheck` | `ok=true`，DXGI/GDI/PIL 均 2560×1600 出图，`INPUT` 结构 40 字节 |
| `source_games` | 识别到已安装：半条命 1、半条命 2、第一章、第二章、Lost Coast、针锋相对、蓝色行动、传送门 2 |
| `source_status` | `mode=waiting`，`autopilot=false`，未选游戏时不抢键盘 |

`check --release` 就是 GitHub Actions 里 `plugin-market-verify.yml` / `release.yml`
使用的同一套发布检查（严格 manifest、支持文件、插件内测试、构建、payload hash 校验），
外加 `--market-release` 的市场约定：插件 ID 必须匹配 `^[a-z][a-z0-9_]*$`、
仓库名必须是 `n.e.k.o_plugin_<id>`、tag 必须等于 `plugin.toml` 的版本。

---

## 2. 需要账号才能做的三步

### 2.1 建仓库并推送源码

GitHub 上创建**公开空仓库**（不要勾选 README / .gitignore / License）：

```text
n.e.k.o_plugin_source_player
```

然后在本仓库里推送。远端已经配好：

```powershell
cd <本目录>
git push -u origin main
```

### 2.2 等待 Verify 工作流

推送后打开仓库的 **Actions**，等 `Verify N.E.K.O Plugin` 变绿。
它会在 Ubuntu 上跑 ruff → `sync` → `check -r` → 生成并校验安装包与 `market-evidence.json`。

### 2.3 提交首次审核

1. 打开 <https://market.project-neko.cn/#/upload> 并登录。
2. 填仓库地址 `https://github.com/<你的用户名>/n.e.k.o_plugin_source_player`。
3. 点「读取仓库信息」，确认名称，选分区和 1–5 个标签。
4. 提交审核申请；在「我的插件」里看检查结果与审核意见。

审核通过后，回到 N.E.K.O 源码目录执行：

```powershell
uv run neko-plugin publish C:\path\to\source_player
```

它会再次跑发布检查 → 确认 HEAD 已推送 → 打 `v1.0.0` 标签 → 等 Actions 产出
`source_player.neko-plugin`、`source_player.market-release-check.txt`、
`market-evidence.json` → 通知市场发布 stable 版本。

> 发布用的 `release.yml` 是官方受管模板，会调用
> `Project-N-E-K-O/N.E.K.O/.github/workflows/plugin-market-release.yml@main`，
> 不需要自己写构建脚本。市场只接受「审核通过 + 仓库匹配 + 标准发布验证成功」的版本。

---

## 3. 审核时最可能被看的点（本仓库的对应做法）

| 审核关注 | 本仓库的处理 |
|----------|--------------|
| 插件能否加载、entry 是否 async | 6 个 `@plugin_entry` 全部 `async def`；活体宿主已验证启动 |
| 是否会在启用时抢键盘 / 乱点 | `auto_start=false`、`autopilot_enabled=false`；必须 `source_play` 才动 |
| 是否只在前台注入 | `require_foreground=true`，非前台一律拒绝并记录真实原因 |
| 是否会点「新游戏」覆盖存档 | `start_in_game_required=true`；没见过游戏画面就不按键，菜单只按 Esc |
| 是否会读内存 / 改存档 / 作弊 | 只截屏 + SendInput；不读内存、不写存档、不发成就、不执行控制台命令 |
| 是否有失控风险 | F8 硬暂停、发言「停手」、`max_session_minutes=45`、玩家碰键即让位 |
| 文档是否说清楚边界 | README「已知限制」明确写：不是外挂、不保证 100% 通关、VR/合作模式明确拒绝 |
| 依赖是否可复现 | 无第三方 Python 依赖（用宿主自带 numpy/Pillow）；无 `requirements.txt` |
| i18n | `i18n/zh-CN.json` + `i18n/en.json`，键集一致 |
| 测试 | `tests/` 里是可在 Linux CI 跑的纯 stdlib 契约测试 |

---

## 4. 发布后自查

```powershell
# 1. GitHub Releases 里有三个资产
#    source_player.neko-plugin / source_player.market-release-check.txt / market-evidence.json
# 2. 市场详情页出现 1.0.0，stable 最新版不为空
# 3. 在 N.E.K.O → 插件管理 → 导入 下载回来的包，再跑一次 source_selfcheck
```

---

## 5. 版本号纪律

- 插件 ID `source_player` 发布后不再变。
- 已发布过的版本号不能复用；改代码就升版本（例如 `1.0.1`），commit、push、再 `publish`。
- 撤回是单向的：撤回过或发布过的版本号都不能再用，只能发新版本。
