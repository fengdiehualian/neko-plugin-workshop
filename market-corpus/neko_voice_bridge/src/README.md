# 猫娘语音桥 · 1对1版猫娘语音频道

<p align="center">
  <img src="icon.png" alt="猫娘语音桥：拿着薄荷绿电话听筒的猫娘" width="180">
</p>

让 [N.E.K.O 猫娘计划](https://github.com/Project-N-E-K-O/N.E.K.O)进入 KOOK 语音频道，听取频道发言并把猫娘回复送回频道。此分支固定发布 **0.7.1**，面向一对一使用；未实现 OOPZ。

**[下载 0.7.1 安装包和插件](https://github.com/qiguang113/n.e.k.o_plugin_neko_voice_bridge/releases/tag/v0.7.1)**

## 安装

插件面板顶部的“新手教程”提供 Windows EXE 下载按钮，可直接打开本仓库 Releases。

1. Windows 10/11 x64 用户下载并运行 `NekoVoiceBridge-Setup-0.7.1-x64.exe`。自带运行环境，无需安装 Node.js、Python 或浏览器。猫娘本体需另行安装。
2. 下载独立的 `neko_voice_bridge.neko-plugin`，在猫娘插件管理器中导入，重启插件并确认面板版本为 0.7.1。
3. 在 EXE 复制连接链接，粘贴到插件连接面板并保存。无需调用插件入口；连接链接包含本机凭据，不要公开。
4. 在 EXE 内登录给猫娘准备的 KOOK 账号，加入语音频道并开启麦克风，按配置向导测试声音。

默认开启“仅回应称呼”：说话时叫到猫娘当前称呼才会把回复播回频道；称呼直接同步猫娘设置。可在 EXE 中关闭该过滤。语音和回复沿用猫娘本体配置，免费模式不要求另购 API。

## 本体有声音、频道没有声音

可以使用 VB-CABLE 虚拟音频线：本体输出选 **CABLE Input**，EXE 采集 **CABLE Output**，KOOK 输入选 **猫娘语音桥（内置虚拟麦克风）**。驱动需从 [VB-Audio 官网](https://vb-audio.com/Cable/) 单独安装，未打包在 EXE 中。不要改 Windows 默认输出或把 KOOK 输出送入这条线。

详细步骤、试音顺序和常见错误见 [使用说明](docs/使用说明.md)。

## 版本范围

- 0.7.1 安装器使用当前用户目录，尚不提供后续版本的安装路径选择。
- 同时只处理一句话；本体会话与记忆共用。此版虽包含频道昵称提示，但不能保证多人称呼准确，不提供后续版本的本地识别和成员别名方案。
- 音频线按句收集，有额外等待时间。猫娘本体窗口需保持打开，并关闭其桌面麦克风，避免重复输入。
- 安装包未签名。自动检查及模拟测试不能替代 Windows 安装、升级、卸载和实际频道试听。

## 源码与开发

- `app/`：从原始 0.7.1 安装包提取的完整应用文件，29 个文件的 SHA-256 与留存构建清单一致。
- 仓库根目录的 `plugin.toml`、Python 模块及 `ui/`、`i18n/`：根据 0.7.1 开发记录恢复的插件源码，单独构建成标准 `.neko-plugin`。
- [版本来源与校验说明](docs/版本来源.md)：原始安装包与重新构建插件的区别。

开发运行：安装 Node.js 后在仓库根目录执行 `npm install`、`npm start`。Python 插件需在 N.E.K.O 宿主中运行；其隔离测试可执行 `npm run test:plugin`。本仓库不包含猫娘本体或账号数据。

插件构建参考上游提交 `cd17a21164ae5e734040042efd2b7cb810944e9f`：准备该版本猫娘源码及 CLI 依赖后，执行：

```sh
python scripts/upstream_cli.py /path/to/N.E.K.O check --release .
python scripts/upstream_cli.py /path/to/N.E.K.O build . -t dist
```

该脚本调用上游 CLI 进行静态检查和打包，不启动本体，也不代表完整运行时兼容验证。安装版 EXE 为历史原包，本仓库没有宣称可逐字节复现其 Windows 安装器。

Electron 及其第三方许可随安装包提供；N.E.K.O 和 VB-CABLE 分别遵循其自身许可。本项目不是 KOOK、VB-Audio 或 N.E.K.O 的官方产品。

## 插件市场投稿

仓库根目录包含 `plugin.toml`、插件入口 `__init__.py`、`ui/` 和 `i18n/`，符合独立插件仓库布局。`app/` 仅保存 EXE 源码，插件构建时排除它及开发资料。目录调整不改变 0.7.1 功能；历史 Release 附件保持不变。
