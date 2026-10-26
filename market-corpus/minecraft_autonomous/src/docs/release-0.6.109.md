# 0.6.109 公开测试版

Minecraft Autonomous 0.6.109 · 安装修复与 MC 核心 rc.99

Windows 首次安装：下载 [NEKO 完整安装包](https://mc.kangqiang.site/downloads/neko)，完整解压后双击「一键安装.cmd」，按中文指南导入同目录的 Windows 插件；再绑定自己的模型、选择服务器并开始。一个 ZIP 包含 Node、后端、3D 观战、贴图、音频和截图浏览器，无需再拼合公共组件。需要本人已有的 Neko、可用模型和 Java 1.21.1 服务器。

升级前正常停止旧版游玩，保留账号、模型、记忆和数据。若提示旧离线环境不匹配，点击「使用已安装的新版运行环境」再开始。自动采用短用户缓存和 Windows 扩展路径校验，不要求管理员或修改注册表；确实缺失/篡改/越界的文件仍拒绝。自定义路径和外部执行器不会被自动接管。

附件按平台区分：官方工作流生成的 `minecraft_autonomous.neko-plugin` 为 Linux x64 / CPython 3.11，保持原包与市场凭据。Windows x64 / CPython 3.11 用户使用官网完整包；已有匹配 rc.99 资源时，也可解压附件 `Minecraft-Autonomous-0.6.109-Windows-x64-cp311-plugin-only.zip` 后导入。不要把 Linux 二进制依赖包直接用于 Windows。

固定组合：插件 0.6.109 / 框架 0.2.103 / 核心 0.5.7-rc.99 / 共享观战 8b89927d。核心源码 `9b93af4523ddb0556c89a2a5edc9a0e8e2c0f05e`；框架固定 `addb016b01c968e56a93931f0812b045bfaccad0`；此 tag 源码 `3657f5faab94e4bcc0a7e7ded6b5ec26f8846a08`。

验证：核心 2251 通过 / 6 可选跳过；框架源码与真安装 wheel 各 1556；插件 443 通过 / 1 平台跳过、12 项界面逻辑；正式 SDK 构建、独立接收目录导入、全新中文/空格路径准备、移动原目录后的缓存解析及公网完整下载 SHA256 核对通过。自然游玩已继续走动、采收补种和复盘；这不是全部玩法、长期生存或语义视觉已完成验收的保证。Microsoft 本人授权、全部特殊外观、桌面声音和方法受控收益仍有待分别验证。

完整 Windows ZIP SHA256：`624ef18e011ef6681cfa1c2e963745e815582130a77b812e0fc525e2fcd4e5d4`（1107341175 字节）。
Windows 补充 ZIP SHA256：`af03cb43ec771429575b3eeb1c8f8b1db62c508f12a389ff6e7d585ea982a327`。


发布记录：UTC 2026-10-26T12:51:12，官方 `neko-plugin publish market` 成功；公开插件 ID 106 的 latest_version=0.6.109、verification_status=passed，source_commit 与官方工作流证据匹配。官方直接插件和 market-evidence.json 保持原字节，Windows 补充插件使用 ZIP，tag v0.6.109 固定 3657f5faab94e4bcc0a7e7ded6b5ec26f8846a08。官网完整包逐字节公网读回 SHA256 一致；市场包、Windows 包和完整资源的验收分别留证。
