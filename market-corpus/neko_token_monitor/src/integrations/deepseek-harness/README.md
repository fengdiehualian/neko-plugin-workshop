# 猫粮 · DeepSeek Harness 桥接

在 Harness 插件管理器「添加插件」中填写本文件所在目录，安装并启用「猫粮余额桥接」。猫粮账户设置选择独立的 DeepSeek Harness 档案并保存；不填写 API Key。当前兼容目标为本机 Harness 0.2.0-rc.2。源码依据官方 1600d8a1e140dcde43a1a0941940a0480712c011 的账户与桌面桥接实现。

安装后需要完整退出并重新打开 Harness，使页面加载本机桥接配置。也可使用官方 CLI：`dsh plugin --profile desktop add <此目录的绝对路径> --ignore-scripts --offline`。在 macOS 未安装 dsh 命令时，应用附带的 CLI 位于 `/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh`。保留此目录供本地插件加载；移动目录后需在插件管理器重新选择位置。

Host 调用已有 deepseekAccount.getBalance，返回充值余额和赠金的金额与币种，不读取资料、聊天或导出登录凭据。仅绑定 127.0.0.1:48929，接口需要随机桥接密钥。连接文件保存在本机应用数据目录 NekoCatfood/bridges/deepseek_harness/connection.json，权限 0600；这是猫粮自己的局域连接密钥，不是 Harness 登录 token，不应分享或打包。

Client 在 shell.overlay 注册原生充值视图，用户通过猫粮发出打开命令后调用 Harness 的 dshPlatform.open('top-up')。接口只允许这一目的地，不接收任意 URL，不选择金额、套餐、协议或支付；页面加载成功才报告已打开。返回按钮销毁原生视图。官网入口继续备用。

关闭或禁用此插件会关闭本机服务并删除它自己的连接文件。只卸载本桥接包即可撤销接入；不删除 Harness 或猫粮账户数据。没有依赖安装脚本或外部运行时下载。
