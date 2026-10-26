# Yui 手机/电脑锁（N.E.K.O. Plugin）

（SHA256 与证书指纹见下方「安装」章节，安装前务必核对；正式渠道优先 GitHub Releases）

让 Yui 可以惩罚主人：

- **锁手机**：熄屏锁屏（要密码才能解锁）；或 **应用锁**——手机上打开任何 App 立刻弹回桌面
- **锁电脑**：打开任何程序立刻被关掉、弹回桌面（**N.E.K.O. 自己不受影响**），右上角仅显示 🔒 角标，不提示解锁方式
- **由 Yui 自主决定**：4 个工具注册给 LLM，她生气时自己决定是否调用

解锁：手机应用锁由 Yui 解锁或重启手机；电脑输入秘密按键组合（↑↑↓↓←→←→BA，仅口头告知，界面不显示）、Yui 的 `unlock_pc`、或重启电脑。

## N.E.K.O. 内置面板

插件自带 Hosted UI 面板（`ui/panel.tsx`）：在 N.E.K.O. 插件管理器里打开本插件即可看到——

- 手机在线状态实时刷新（电量 / 应用锁 / 锁屏权限）
- **配对二维码**直接显示在面板上，手机「扫码配对」扫它即可
- 手动控制按钮：锁手机屏幕 / 应用锁 / 解锁手机 / 锁电脑 / 解锁电脑（危险操作带二次确认）

二维码渲染需要 qrcode 库（可选）：`pip install qrcode pillow`；没装也能看到配对码文本。

## 电脑端 Web 面板

不用记命令，浏览器里点点点：

```bash
uv run --with qrcode --with pillow python web_panel.py
```

打开 <http://127.0.0.1:48913>（仅本机可访问）：
- 实时状态：手机在线/电量/应用锁、电脑锁
- 按钮：锁手机屏幕 / 手机应用锁 / 解锁手机 / 锁电脑 / 解锁电脑
- 内置配对二维码，手机「扫码配对」直接扫

## LLM 工具

| 工具 | 作用 |
|---|---|
| `lock_phone(mode=screen/apps)` | 熄屏锁屏 / 应用锁 |
| `unlock_phone` | 解除手机应用锁 |
| `lock_pc` | 锁电脑（程序全弹回桌面） |
| `unlock_pc` | 解锁电脑 |

另有手动测试入口：`yuilock_status` / `lock_phone_now` / `lock_phone_apps_now` / `unlock_phone_now` / `lock_pc_now` / `unlock_pc_now`。

## 安装（插件端）

1. N.E.K.O. 插件管理器 → 「开发模式」→ 开启 → 「加载未打包插件」选择本目录（`plugin.toml` 所在目录）。
2. 校验 → 加载 → 启动插件（`auto_start = true`，随 N.E.K.O. 自启）。
3. 编辑 `plugin.toml` 的 `[yuilock]` 段并重载插件：
   - `token`：手机 App 显示的配对令牌（推荐设置）；
   - `host = "auto"`：UDP 广播自动发现手机，或固定填手机 IP；
   - `transport`：`auto`（先局域网后蓝牙）/ `lan` / `bt`。
4. 蓝牙通道（可选）：先在 Windows 蓝牙设置配对手机，再在 N.E.K.O. 的 Python 环境安装：
   `pip install winrt-runtime winrt-Windows.Devices.Bluetooth winrt-Windows.Devices.Bluetooth.Rfcomm winrt-Windows.Devices.Enumeration winrt-Windows.Networking.Sockets winrt-Windows.Storage.Streams`

## 安装（手机端 APK）

**APK 下载（分享直链）：<http://8.152.0.186:40033/s/vXfj>**

> ⚠ v1.4.0 起签名密钥已轮换，与旧版本签名不同，需要**卸载旧版后重装**。
> 正式渠道优先 GitHub Releases；HTTP 网盘为便利分发，安装前建议核对：
> - APK SHA256：`54C7353056E5DA366F4D299E2C8DC612754C4CA6112A9D3D9D9829D7C574FB48`
> - 证书指纹（SHA-256）：`C7:16:82:B4:3F:E2:7B:8D:77:60:4D:A0:7C:B2:D9:65:A1:D4:36:90:D9:DE:8A:1A:86:C2:59:8F:70:6E:AD:7B`

也可以自己编译：`android-app/` 目录（`build.ps1` 一键编译，产物在 `android-app/out/YuiLock.apk`；
签名密钥放在仓库外，默认读 `../keystore-backup/yuilock.keystore` 或环境变量 `YUILOCK_KS_PATH`/`YUILOCK_KS_PASS`）。

1. 安装 APK，按顺序：① 激活锁屏权限（设备管理器）→ ② 授予使用情况访问权限（应用锁需要）→ ③ 扫码配对 → ④ 启动监听服务 → ⑤ 点「忽略电池优化」和「后台运行设置」。
2. 手机与电脑同一 Wi-Fi（局域网）或完成蓝牙配对（SPP）。
3. **⑤ 很重要**：很多国产系统（vivo/小米/华为/OPPO/三星…）会冻结后台进程，冻结后收不到电脑命令。
   点 App 里的「后台运行设置」按钮，在打开的页面里允许本应用「自启动 / 后台运行 / 后台高耗电」。
   未做这一步时，手机息屏或离开 App 一会儿后就会「发现不了」。

## 发现不了手机？排查顺序

1. 手机 Yui Lock 是否已点「④ 启动监听服务」（通知栏应有「Yui Lock 运行中」常驻通知）。
2. 手机与电脑是否同一 Wi-Fi；部分路由器开了「AP 隔离」会导致互相不可见。
3. 手机是否被系统冻结：打开 App 看「最近事件」是否还在刷新；点「后台运行设置」放行。
4. `plugin.toml` 的 `token` 是否与手机 App 里显示的一致（不一致会连上但命令被拒）。
5. 也可在 `plugin.toml` 里把 `host = "auto"` 改成固定手机 IP（`host = "192.168.x.x"`）跳过广播发现。

## 扫码配对（推荐，不用手打令牌）

1. 在电脑上、本插件目录里运行：
   ```bash
   uv run --with qrcode --with pillow python pair_qr.py
   ```
   （首次没有依赖时会提示；也可 `pip install qrcode pillow` 后直接 `python pair_qr.py`）
2. 电脑会弹出二维码窗口（自动探测局域网 IP、读取/生成配对令牌）。
3. 手机 Yui Lock 点 **「③ 扫码配对」** 对准二维码 → 端口和令牌自动填好并保存。
4. 二维码同时保存为 `pair_qr.png`，也兼容系统相机扫码（识别 `yuilock://pair?...` 深链）。

## CI

- `verify.yml`：push/PR 时调用 N.E.K.O. 官方 `plugin-market-verify` 做市场校验
- `release.yml`：推送 `v*` tag 或手动触发时调用官方 `plugin-market-release` 发布

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

## 安全设计

- **挑战-应答认证**：控制命令（lock/applock/unlock）需要手机下发一次性随机 challenge、电脑端在**同一条连接**上回 `HMAC-SHA256(token, "cmd|nonce|challenge")` 才执行。proof 与命令、客户端 nonce、挑战三者绑定，不可挪用到别的命令、不可重放；挑战按连接隔离互不打断；token 不在任何网络通道上明文传输。
- **拒绝未认证"成功"**：控制命令的第一包若直接 `ok=true`（未要求验证），客户端视为遭劫持并拒绝执行；UDP 发现请求带随机数并要求原样回显，防旧包重放。
- **空令牌保护**：手机端 token 为空时直接拒绝一切控制命令（只允许 ping）。
- **防 PID 复用误杀**：解锁电脑前必须三重校验（可执行文件路径一致 + 命令行含完整 `--instance <uuid>` 参数对 + pid 存活）才执行 taskkill；已有锁进程时拒绝二次锁定。
- **电脑锁白名单**：按可执行文件完整路径/同目录精确放行；系统外壳用主名规范化（`explorer.exe`→`explorer`）；查不到路径的进程一律不放行；不做任何子串模糊匹配。
- **TCP/蓝牙加固**：指令行上限 4KB、首包 5s / 空闲 20s 超时、并发连接上限 4（accept 前抢名额，收不下立即断开）、线程池队列有界、单连接最多 30 条命令。
- **应用锁状态一致**：拦截线程真正在跑才算"开"；服务重启按已保存状态恢复并做权限预检，权限缺失自动清为关；重启手机即解除。
- **Web 面板防滥用**：只监听 127.0.0.1；校验 Host 头防 DNS rebinding；POST 动作必须带自定义头 `X-YuiLock-Panel`（跨站请求带不了，CORS 预检即失败）；危险操作二次确认；状态接口不回显配对令牌。
- **配对令牌不落地**：`pair_qr.py` 生成的令牌自动写回 `plugin.toml`（`--no-save` 可跳过），二维码图片存系统临时目录不进插件目录。
- **分发与签名**：签名口令不入库（环境变量 `YUILOCK_KS_PASS` 或本地 `keystore.password`，均已 gitignore）；每次构建输出 APK SHA256 与证书指纹，下载优先走 GitHub Releases 并核对校验和；HTTP 网盘链接仅作便利分发，存在被替换风险。
- 电脑锁**不做防拆卸**（任务管理器/重启可解除）——这是玩家玩法工具，不是恶意软件。
- Android 签名密钥与口令不入库（`*.keystore`、`keystore.password` 已忽略）。

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

覆盖：插件结构 / plugin.toml 入口 / 客户端挑战-应答认证 / 错误令牌拒绝 / 空令牌手机端拒绝 / ping 免认证。
