# 配套专家团 / Expert packs

本目录存放与工坊配套的 **Agent 专家包**（WorkBuddy / CodeBuddy 专家包格式：`.codebuddy-plugin/plugin.json` + `agents/*.md` + `references/` + `avatars/`）。

和 `packs/` 的区别：

| 目录 | 形态 | 谁用 |
|---|---|---|
| `packs/` | 工坊内部生成规范（规则 / 模板 / skill），作用于「写插件」 | 工坊引擎自动挂载 |
| `experts/` | 独立专家包，作用于「谁来带你写插件」 | 使用者手动导入到自己的 Agent 底座 |

## neko-plugin-ops

「NEKO 插件设计与自动运维专家团」——1 名主理人 + 9 名专家：插件开发（云雨）、TDD（云测）、架构（云远）、极简（云简）、审查（云严）、技能运营（云技）、规划（云问）、可靠性（云稳）、安全（云安）。

- 定位：把「从零做插件 / 修跑不起来的插件 / 极简重构 / skill 沉淀审计」四条链路拆成可执行分工，输出给新手与 ADHD 用户看得懂的交付物。
- 依据：N.E.K.O 插件规范（与 `packs/neko-plugin` 同源）+ `references/system.llm` 执行纪律手册 v4。

### 安装

```bash
# 打成 zip 后，在 WorkBuddy「专家中心 → 导入专家包」中选择该 zip
cd experts
zip -r neko-plugin-ops.zip neko-plugin-ops -x '*.DS_Store'
```

```powershell
Compress-Archive -Path experts/neko-plugin-ops -DestinationPath experts/neko-plugin-ops.zip -Force
```

详细说明见 [`neko-plugin-ops/README.md`](neko-plugin-ops/README.md)。

### 一致性校验

`test/experts.test.ts` 会校验每个专家包的清单：`plugin.json` 可解析、`agents`/`avatars` 引用的文件都存在、`members` 与 `teamInfo` 对得上、每个 agent 文档都有 frontmatter。新增专家包时不需要改测试，目录一放进去就会被纳入校验。

```bash
bun test test/experts.test.ts
```
