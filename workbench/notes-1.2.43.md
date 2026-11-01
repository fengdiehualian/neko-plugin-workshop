## 1.2.43 — 新增「思考强度」设置

设置 → 🔁 重试 页多了一行 **思考强度**:自动 / 关 / 低 / 中 / 高 / 最高。

- **ZCode 底座**:写进 `defaultModelSelection.options.reasoningLevel`,下一个任务即生效(用的是 ZCode 官方支持的档位:`none/minimal/low/medium/high/xhigh`)
- **OpenCode 底座**:写进模型配置的 `options.reasoningEffort`(OpenAI 系只认 minimal/low/medium/high,「关」映射为 minimal、「最高」映射为 high),保存后下一轮任务会先重启引擎再生效
- 「自动」= 不写任何字段,完全交给模型默认;切回自动会把配置里的档位字段清掉
- 持久化在 `runtime/settings.json` 的 `thinkingLevel`,升级安装包不丢

实测:设「高」→ `runtime/zcode-provider.json` 出现 `"options":{"reasoningLevel":"high"}`,任务正常返回并记录 usage;切回「自动」→ 字段移除、引擎配置同步收敛。
