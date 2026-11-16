# 先搜后建 · 使用说明

## 这插件干什么

写新东西之前，先确认世上是否已经有同类实现，并优先加入上游而不是重造。

插件会做三件事：

1. **给规则**：AI 接到「做个 X」时先取回固定的规则正典，按四步走（检索 → 打分 → 贡献 → 交付）。
   v0.2.0 起，打分之后多一道**可改性硬闸**（屎山判红就不许 Join，只能 Extend/Fork/Build）；要调用上游能力时按 `references/integration-seam.md` 走**稳定接入口 + 薄适配层**；动手改代码一律**先切分支再开 PR**，不直推默认分支——自己的仓库也一样。
   v0.3.0 起，`references/host-adapters.md` 里的粘贴型提示词都带 `RBB:PROMPT` 锚点和 sha256 登记（`prompt-digests.json`）：把提示词粘到别的聊天 AI 之后，用随包 `scripts/prompt_digest.py --check <文件>` 就能知道装的是哪一版，改过就报 `NO MATCH`。
2. **给检索**：用你本机的 `gh` 直接查 GitHub，出一张候选表（★、最近推送、语言、许可、open issues）。
3. **给验收**：谁把规则改写成别的格式（单文件系统提示、别的语言），跑一次校验器就知道规则有没有被偷偷削掉。

## 首次使用

1. 确认 **启用 GitHub 检索** 为开（设置项 `enable_github_scan`）。关掉也能用规则与格式检查，只是不联网。
2. `gh 可执行文件` 只接受 `gh` / `gh.exe`，从 PATH 解析绝对路径；不支持填任意路径或 `.bat` / `.cmd`。
3. 点 **自检规则完整性**：
   - `rule_sha256 = fa0f991f…bc93` → 规则本体没被改动；
   - `skill_exit_code = 0` 且输出含 `PASS … verbatim` → 随包校验通过。

任何一项不对，重装插件包，不要手改 `skills/` 里的文件。

## 设置项

| 项 | 默认 | 说明 |
|---|---|---|
| `enable_github_scan` | 开 | 关掉后 `rbb_scan` 直接返回「检索已关闭」，不会静默失败 |
| `gh_binary` | `gh` | 只允许 `gh` / `gh.exe`；填其它值会回退到 `gh` 并给出 warning |
| `default_stars` | 50 | 星数门槛；查不到时降到 0 再试 |
| `default_limit` | 6 | 每个关键词条数，工具里最大 10 |
| `updated_days` | 365 | 只看最近 N 天有推送的仓库 |
| `scan_timeout` | 30 | 整次检索总预算（登录检查 + 所有关键词共享），不是每个关键词各一份 |

默认值只在 `plugin.toml` 的 `[settings]` 写一份。`rbb_status.settings` 会显示 `source`（`runtime` / `plugin.toml` / `emergency-defaults`）与 `warning`；看到 `emergency-defaults` 说明配置文件坏了，正在用降级值（检索会被关掉、条数压到 1），修好 `plugin.toml` 再重载。

## 怎么看结果

**检索成功但 0 命中**：这是「这一组词没搜到」，不是「不存在」。换英文同义词、降星数门槛、放宽时间窗再跑一轮。

**检索失败（code=2 / 3 / 4）**：`gh` 没装、没登录、主机不通，或预算（code=4）用完。修 `gh auth status` / 增大 `scan_timeout` 后重跑，或按 `references/discovery-playbook.md` 用网页检索串人工查。**此时不得断言没有同类项目。**

**格式检查不通过**：`rbb_check` 返回结构化 `verdict / modifiability / lane`，并列出缺项。完整档必须有四段、明确结论和可改性证据；只有绿档允许 Join，黄档只能 Extend，红档不得 Join。快速档只写 `复用 / 改动 / 风险`（或 `Reused / Change / Risk`）三条非空行。判定新建时，首个非空行必须有 `不可复用原因 / Not reusable because:` 和具体原因。

## 安全边界

- 本插件**只读**：不建 issue、不提 PR、不发评论、不改任何远端状态。要公开写，由你逐次确认。
- 不写你的文件系统，不改 N.E.K.O 配置，权限只有检索与只读校验。
- 核心不变块按固定 SHA-256 基线随包校验；N.E.K.O 适配修复留在本仓库，通用规则变更再同步到[上游仓库](https://github.com/1-dcjiang/reuse-before-build)。
