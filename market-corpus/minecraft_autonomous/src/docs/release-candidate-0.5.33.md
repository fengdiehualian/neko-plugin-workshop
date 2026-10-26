# 0.5.33 维护候选

现场发生过一次独立执行器异常退出：模型查询 `mc_do` 的 `/inputSchema/properties/steps/items/properties/take`，但原生扁平技能契约用 `skill: take` 表示技能，并没有这个 schema 属性。查询触发本地 `KeyError`，此前代码已经记录“可能派发”，从而留下并不存在的未知游戏操作。

运行层 0.1.50 在派发日志之前验证本地文档工具名和 JSON-pointer 路径。缺失属性、越界索引、不合法路径和不可用工具都返回已知的参数拒绝，并保留原生失败上下文；有效路径的原始 schema 查询不变。新增 9 项回归，源码与隔离 wheel 均通过 434 项离线测试。

固定运行层来源为 `minecraft-ai-friend@0dea1b52ed5c3540231bf65e928a211dddc0dda9`；游戏核心仍为已验证的 0.5.4，来源 `7e8b9750015bb31b7307da6dfb03a90302ce9cef`。Neko 主程序和 SDK 无补丁。

原异常请求已依据原始决策、堆栈、空的原生待办和本地执行路径核对为未派发查询。对账保留停止状态，没有重放请求或自动启动游戏。修复代码与安装包检查不能替代恢复运行后的现场验收。

薄插件通过 58 项 Python 测试、4 项界面数据测试、Ruff 和官方 `check --release --market-release`；载荷哈希核验通过。候选包 `minecraft_autonomous-0.5.33.neko-plugin` 的 SHA256 为 `a47a812f82c73af550d4dc6975f8be807c51b75ec281a1a9a610c3c87224c734`。官方 CLI 保留模型别名字段未识别及未设置 tag 对齐环境两项提示；没有修改 SDK 或创建 tag 来绕过检查。

长期自主生存、清洁机器安装和完整观战功能仍待验收；尚未创建发布 tag、GitHub Release 或提交 Market。
