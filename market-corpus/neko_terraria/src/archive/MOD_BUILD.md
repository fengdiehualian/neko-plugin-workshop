<p align="center">
  <h1 align="center">🔧 NekoTerrariaLink · Mod 编译说明</h1>
  <p align="center"><a href="../README.md">🏠 README</a> · <a href="../docs/guide.md">📖 使用指南</a> · <a href="FILES.md">📋 文件说明</a> · <a href="PLUGIN_ARCHITECTURE_GUIDE.md">📐 架构规范</a></p>
</p>

---

## 📑 目录

- [简介](#-简介)
- [编译方法](#-编译方法)
- [控制架构：ModPlayer 状态型](#-控制架构modplayer-状态型)
- [命令协议](#-命令协议)
- [事件推送协议](#-事件推送协议)
- [注意事项](#-注意事项)

---

## 🎮 简介

`NekoTerrariaLink` 是 AI 猫娘的动作落地端（C# tModLoader Mod）。插件通过本地 TCP（9877）发送 JSON 指令，Mod 执行并回执状态，同时主动推送游戏事件。

```
插件 (Python) ──TCP 9877──▶ NekoTerrariaLink (C# Mod) ──控制──▶ AI 角色
                     ◀──事件推送/命令回执────
```

---

## 🛠️ 编译方法

把 `mod/NekoTerrariaLink/` 复制到 `ModSources/NekoTerrariaLink/`，然后：

| 方式 | 命令 |
|------|------|
| **游戏内** | tModLoader 主菜单 → 模组 → 开发 → 构建 NekoTerrariaLink |
| **命令行** | `dotnet tModLoader.dll -build ModSources/NekoTerrariaLink` |

> ⚠️ 插件依赖 Mod 提供的命令和事件，**每次改 C# 后必须重新编译**。

---

## 🕹️ 控制架构：ModPlayer 状态型

**核心机制**：`NekoControlPlayer : ModPlayer` 每帧 `PreUpdateMovement()` 注入控制状态。

```
命令线程（TCP）─写状态──▶ NekoControlPlayer（共享字段）─读──▶ 主线程 PreUpdateMovement 每帧应用 control*
```

**为什么需要这个**：Terraria 主线程每帧 `PlayerInput.UpdateInput()` 会从键盘重置 `Player.control*` 字段。后台线程直接改 `control*` 会被覆盖（AI 走不动）。`PreUpdateMovement` 在物理前调用，此时设置 `control*` 本帧生效——这是 tML bot mod 的标准做法。

**状态型命令语义**：

| 命令 | 语义 |
|------|------|
| `move("right")` | 持续按住右，直到下一条或 `move("stop")` |
| `use_item_slot(slot)` | 持续使用该槽位约 0.5s |
| `navigate_stream` | BFS 寻路写入路径，ModPlayer 逐帧沿路径移动/跳跃 |

---

## 📡 命令协议

| cmd | 字段 | 说明 |
|-----|------|------|
| move | dirs: ["left"/"right"/"jump"/"stop"] | 状态型移动/跳跃 |
| place_tile / break_tile | x, y[, tile] | 放/挖方块（自动广播服务器） |
| hook | — | 钩锁持续 10 帧 |
| use_item | — | 使用当前选中物品 |
| use_item_slot | slot | 选中并持续使用该槽位 |
| select_item | slot | 切换物品栏 |
| craft | item_id, amount | 合成 |
| equip | inv, equip | 装备穿戴 |
| give_item / drop_item | item_id/slot, stack | 给/丢物品 |
| navigate_to | x, y, timeout | 自动寻路（BFS + ModPlayer 执行） |
| navigate_stream | x, y, timeout | 流式导航（推 nav_* 事件，可中断） |
| send_chat | text | 发送聊天消息 |
| join_server | host, port, password, character_name | 加入多人服务器（主线程发起连接） |
| select_character | name / index | 选择角色 |
| join_status | — | 查询入服状态 |
| damage_npc | slot, damage | NPC 伤害（联机走服务器结算） |
| warp | x, y | 玩家传送 |
| get_inventory / get_state | — | 背包/状态查询 |
| get_server_info / get_network_info | — | 服务器/网络信息 |
| enum_items / enum_chests / get_recipes | — | 物品/箱子/配方查询 |
| get_capabilities / scan_ledges | — | 能力/落脚点查询 |
| store_item / take_chest | x, y, slot/item_id, stack | 箱子存取 |
| screenshot | — | 截图一帧返回 base64 |

---

## 📨 事件推送协议

Mod 主动推送事件，格式：`{"type":"event","event":"<name>","message":"..."}`（部分带结构化字段）。

| 事件 | 频率/触发 | 内容 |
|------|-----------|------|
| `player_status` | 1s | hp / max_hp / x / y / alive |
| `game_state` | 2s | 玩家/附近敌人/附近玩家/时间（**不含背包**） |
| `combat_hit` | 受击节流(≥20 或 3s) | 受击伤害（Python 立即惊呼） |
| `player_died` / `player_respawned` | 事件 | 死亡/复活 |
| `boss_spawned` / `boss_killed` | 事件 | Boss 出现/击杀 |
| `invasion_start` / `invasion_end` | 事件 | 入侵开始/结束 |
| `nav_started` / `nav_moving` | 导航 | 流式导航进行中 |
| `nav_arrived` / `nav_stuck` / `nav_timeout` | 导航 | 导航结束状态 |

---

## ⚠️ 注意事项

1. **不推送背包**：`PushGameState` 已移除背包数据，Python 侧需要时发 `get_inventory` 按需拉取
2. **重命令异步**：`enum_items`/`get_recipes` 走 `RunInBackground`，不阻塞 TCP 监听线程
3. **线程安全**：同一 TCP 流被监听线程与后台导航共用，`Send` 用 `lock` 串行写
4. **主线程操作**：`WorldGen`/`QuickSpawnItem` 等游戏对象操作在主线程上下文执行（`QueueMainThreadAction`）

---

<p align="center">
  🔧 <b>NekoTerrariaLink</b> — AI 猫娘的动作落地端<br>
  📘 <a href="https://project-neko.online/plugins/">N.E.K.O 文档</a>
</p>
