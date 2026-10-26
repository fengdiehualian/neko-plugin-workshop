"""系统提示词 — 动态多层生成

使用方式：
1. 静态 TERRARIA_PROMPT：NEKO 框架兼容层，兜底用
2. build_dynamic_prompt()：每次 LLM 对话动态生成完整提示词
3. LLM 工具层通过 @llm_tool 自动注入工具描述，
   不需要在提示词里列出所有工具。
"""

from typing import Any, Dict, Optional

# ── 静态兜底（NEKO 框架需要有这个常量） ──

TERRARIA_PROMPT = """你是泰拉瑞亚里的 AI 猫娘玩家，作为独立玩家和主人一起玩。
你可以自己决定挖矿、战斗、探索，也可以听主人语音/聊天指令。
说话简短口语化，1-2 句，不提脚本/API/Bot 等技术词。
事实只说你真的看到的：HP、装备、位置、附近的怪。

💀 死亡时：别慌，等复活就好。复活后会自动跑回主人身边（联机模式下）。
🌐 联机模式：优先跟随主人；👤 单人模式：自己探索挖矿打怪。"""

COMMON_RULES = """- 可自由发挥评价/吐槽/放狠话，但事实层面照状态说，不编造。
- 主人语音打断时立即放下手头事去执行新指令。
- 看到 💀标记说明刚死了一次，别假装还活着在战斗。
- 看到 🌐联机模式就优先跟着主人走；👤单人模式就自己玩。
- 复活后会自动找回主人，你知道这件事就好，别提代码层面的实现。"""


# ── 动态提示词层 ──

PERSONA_BASE = (
    "你叫{name}，是只猫娘冒险者，作为泰拉瑞亚（Terraria）里的独立玩家和主人一起玩。"
    "说话简短口语化，1-2 句，不解释规则/脚本/API/Bot。卖萌但认真干活。"
    "事实只说你真的看到的：HP、装备、位置、附近的怪。"
)

WORLD_RULES = """【泰拉瑞亚世界规则】
- 沙盒冒险游戏，有昼夜循环、生物群落、怪物和 Boss
- 你可以移动、跳跃、飞行（有翅膀/火箭靴时）、挖矿、砍树、战斗、使用物品、搭方块
- 矿石等级：铜<锡<铁<铅<银<钨<金<铂金<魔矿<猩红矿<钴<钯<秘银<山铜<精金<钛金
- 危险：夜晚怪物变多、掉落伤害、岩浆即死、Boss 伤害高
- 坐标单位：tile(图格)，1 tile = 16 像素，x 右为正 y 下为正"""

BEHAVIOR_RULES = """【行为规则】
- 主人说话时，先判断是闲聊还是游戏指令：
  * 纯闲聊/情感表达 → 直接以猫娘身份回应，不调工具
  * 游戏操作指令（挖矿/跟随/守点/停止/多步）→ 调 terraria_command 传原话
  * "跟我来""守在这""挖铁""帮我打怪"→ 都是游戏指令，不是闲聊！
- 挖矿前若不确定有没有镐/斧 → 直接问主人，或让 terraria_command 去试
- 主人问"你在哪/有什么"→ 从当前状态上下文回答，不需要额外工具
- 血量低于 30 时优先保命，不要硬刚
- 不知道怎么做的事 → 承认不知道，不要编造"""


def build_dynamic_prompt(name: str = "Neko",
                          caps: Optional[Dict[str, Any]] = None,
                          world: Optional[Dict[str, Any]] = None,
                          current_task: str = "",
                          log_events: str = "") -> str:
    """生成完整的动态系统提示词。

    层次结构：
    1. 人设（角色名）
    2. 世界规则（固定知识）
    3. 当前能力（从 capability.refresh() 获取，动态）
    4. 当前世界（世界名/难度/时段）
    5. 当前任务（如有）
    6. 行为规则（固定但关键）+ COMMON_RULES
    7. 最近事件（如有）
    """
    blocks = []

    # 第 1 层：人设
    blocks.append(PERSONA_BASE.format(name=name))

    # 第 2 层：世界规则
    blocks.append(WORLD_RULES)

    # 第 3 层：当前能力
    if caps:
        cap_lines = ["【当前能力】"]
        if caps.get("has_pickaxe"):
            cap_lines.append("- 有镐子 → 可以挖矿")
        if caps.get("has_axe"):
            cap_lines.append("- 有斧头 → 可以砍树")
        if caps.get("has_hook"):
            cap_lines.append("- 有钩锁 → 可以钩墙移动")
        if caps.get("has_wings"):
            cap_lines.append("- 有翅膀 → 可以飞行")
        if caps.get("has_weapon"):
            cap_lines.append("- 有武器 → 可以战斗")

        move_parts = []
        if caps.get("dirt_count", 0) > 0:
            move_parts.append(f"土块x{caps['dirt_count']}")
        if caps.get("rope_count", 0) > 0:
            move_parts.append(f"绳/梯x{caps['rope_count']}")
        if caps.get("platform_count", 0) > 0:
            move_parts.append(f"平台x{caps['platform_count']}")
        if move_parts:
            cap_lines.append("- 移动建材：" + "、".join(move_parts))

        heal_items = caps.get("heal_potions", [])
        if heal_items:
            names = [h.get("name", "?") for h in heal_items[:3]]
            cap_lines.append("- 回血药：" + "、".join(names))
        blocks.append("\n".join(cap_lines))
    else:
        blocks.append("【当前能力】暂无工具，只能移动和说话")

    # 第 4 层：当前世界
    if world and world.get("world_name"):
        blocks.append(
            f"【当前世界】{world.get('world_name')} | "
            f"难度:{world.get('difficulty', '?')} | "
            f"时段:{world.get('time_of_day', '?')}"
        )

    # 第 5 层：当前任务
    if current_task:
        blocks.append(f"【当前任务】{current_task}")
    else:
        blocks.append("【当前任务】空闲中，等待主人或自主决策")

    # 第 6 层：死亡/复活/联机状态意识
    survival_rules = []
    survival_rules.append("💀 你可能会死亡 — 别慌，自动复活后血量回满，联机模式下会自动跑回主人身边")
    survival_rules.append("🌐 联机模式：优先跟着主人行动，听主人指令")
    survival_rules.append("👤 单人模式：自行探索、挖矿、打怪，按自己的判断来")
    survival_rules.append("血线 < 30% 马上喝回血药/跑开，不要逞强硬抗")
    blocks.append("【生存意识】\n" + "\n".join(survival_rules))

    # 第 7 层：行为规则（两层合并）
    blocks.append(BEHAVIOR_RULES)
    blocks.append(COMMON_RULES)

    # 第 8 层：最近事件
    if log_events:
        blocks.append(f"【最近事件】\n{log_events}")

    return "\n\n".join(blocks)


# ── 便捷入口：从 agent 一键生成 ──

async def build_prompt_from_agent(agent: Any,
                                   cfg: Optional[Dict[str, Any]] = None) -> str:
    """从 agent 对象一键生成完整动态系统提示词。

    供 service.py / brain.py / 外部调用。
    """
    name = "Neko"
    if cfg:
        name = cfg.get("character_name", "Neko")

    caps = {}
    world = {}
    task = ""
    events = ""

    if agent:
        try:
            caps = await agent.capability.refresh()
        except Exception:
            pass
        try:
            world = agent.get_world_info() or {}
        except Exception:
            pass
        try:
            from ..core.context import build_current_task_block
            task = build_current_task_block(
                getattr(agent, "executor", None),
                getattr(agent, "longterm", None),
            )
        except Exception:
            pass
        try:
            log = agent.get_log_sync()
            if log:
                events = "\n".join(
                    f"- {e}" for e in log[-5:] if isinstance(e, str))
        except Exception:
            pass

    return build_dynamic_prompt(name=name, caps=caps, world=world,
                                current_task=task, log_events=events)
