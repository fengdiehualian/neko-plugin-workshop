# 从心开始桥接设计说明

## Identity Lock

- plugin_id: `from_the_heart`
- folder: `plugin/plugins/from_the_heart`
- name: `从心开始桥接`
- entry: `plugin.plugins.from_the_heart:FromTheHeartPlugin`
- package type: `plugin`

## Purpose

为 `NEKO-FromTheHeart-DEMO` 提供一个受合同约束的单轮对白槽位。插件只返回对白、语义标签、视觉标签和内置资源描述，不拥有游戏剧情状态。

## First Version Scope

- `resolve_interaction` 一个运行入口
- 合同 `1.2.4`
- 确定性 exact-answer 路由
- 可选的 `conversation` 模型分类与对白生成
- 本地 policy 收敛和 fail-closed fallback
- 通过 N.E.K.O `/runs` 调用

## Out of Scope

- `ensure_cg` 和动态 CG
- 中央 CG 服务、worker、SQLite、WebP 上传
- 静态或 Hosted UI
- 修改 N.E.K.O 核心
- 写入游戏存档、好感度、变量、长期记忆或剧情跳转

## Contract Ownership

合同 JSON 是游戏合同的版本化副本。当前支持 `game_version=0.5.0-demo` 和 `node_contract_version=1.2.4`。合同变化必须同时更新游戏端、插件副本和兼容性测试。

## Runtime and Failure Policy

插件保持 `auto_start=false`，由 N.E.K.O 手动启动。插件不可用、模型超时、合同不匹配或结果校验失败时，游戏端继续使用本地 fallback。

## Response Boundary

返回值分成两层，避免把运行时传输字段误当成游戏业务命令：

- **受控业务字段**：`reply_text`、`intent_key`、`reaction_key`、`semantic`、`local_facts`、`visual_signature`、`visual_variant_key`、`asset`、`generation` 和 `fallback_used`。这些字段只能使用合同中的枚举、资源哈希和长度限制。
- **传输兼容字段**：`protocol_version`、`game_id`、`game_version`、`node_id`、`node_contract_version`、`interaction_id`、`accepted` 与 `cache_miss_fallback`。它们用于 `/runs`、游戏端二次校验和缓存未命中回退，不表示剧情状态变更。

以下字段永远不属于响应合同，也不能由模型或插件产生：

```text
jump
next_node
ending
relationship_delta
set_variable
unlock
memory_write
```

`cache_miss_fallback` 是传输层的内置回退描述；它不能被模型覆盖，也不能启用动态 CG。游戏端收到未知字段、禁止字段、未知枚举、资源哈希不一致或不符合长度限制的结果时，必须丢弃结果并使用本地 fallback。

## Verification Boundary

插件测试在 N.E.K.O 宿主测试环境中运行，因为入口使用宿主公开 SDK，且对白模型路径还依赖宿主的 `utils.*` 工具。独立仓库可以通过严格静态检查和 clean clone 构建，但不能在没有 N.E.K.O 宿主的纯 Python 环境中声称可独立运行。

发布前必须重复验证：合同身份拒绝、输入边界拒绝、未知节点拒绝、exact answer 绕过模型、模型失败的 fail-closed 回退、响应禁止字段缺失、`/runs → polling → export` round-trip，以及构建包中没有 `__pycache__` 或 `.pyc`。
