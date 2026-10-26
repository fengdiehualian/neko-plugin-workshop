# 浏览器强制深色兼容

2026-10-20：用户确认使用 Dark Reader。故障表现为外围背景变成深棕色，头像框、下拉框和按钮也被重新配色，Canvas 水池与猫仍保持图像颜色。

## 修复

在游戏自身 HTML 的 head 前部声明 `meta name="darkreader-lock"`，使用 Dark Reader 官方支持的页面级退出机制；同时在 HTML 与根样式使用 `color-scheme: only light`，防止浏览器自动深色继续改变游戏颜色。声明只存在于本插件游戏文档，不修改宿主界面、扩展设置或其他网页。运行时不引入 Dark Reader、不加载外部脚本，也不删除扩展注入节点或持续争抢 CSS。

依据：[Dark Reader 官方说明](https://github.com/darkreader/darkreader/blob/main/CONTRIBUTING.md#disabling-dark-reader-on-your-site)、[Chrome 自动深色退出说明](https://developer.chrome.com/blog/auto-dark-theme#how-to-opt-out-of-auto-dark-theme)。

## 实际验证

使用官方 npm 包 Dark Reader 4.9.133 的真实动态改色引擎，在独立本地 Chromium 测试页面中检查；没有安装或改变用户浏览器扩展。引擎文件、许可、版本来源和报告只保留在被忽略的 `tests/artifacts/darkreader-check/`，不作为游戏依赖分发。

- 对照页面去掉保护 meta 后，真实引擎成功启用，背景颜色由 `rgb(255, 237, 201)` 改成 `rgb(63, 42, 0)`，复现深棕色表现。
- 正式页面保留声明，在系统深色偏好下调用同一引擎，不产生动态主题样式；背景仍为 `rgb(255, 237, 201)`，重开按钮仍为奶油色。背景 PNG 成功解码，原始尺寸 1536×1024。
- 启用 Chromium 自动深色模拟后，声明保持原色。
- 设置弹窗、真实鼠标拖拽发射和重开通过，无页面脚本错误；原对局规则未改。
- IAB 独立预览核对 head 声明、实际背景地址及正常画面，没有重新加载用户正在使用的旧对局页。

测试覆盖动态改色引擎和 Chromium 自动深色；没有直接验证用户现有浏览器配置文件、具体扩展版本及其所有模式。页面重新加载后才会读取新声明；若旧版本或特殊模式仍改色，可在 Dark Reader 中只对此游戏地址停用，不需要全局关闭。

本次仅处理浏览器兼容。概念图的原样拆层与比例还原仍待后续工作，不能将背景恢复当成美术还原已通过。保持 0.1.0.dev9，未提交或推送 GitHub。
