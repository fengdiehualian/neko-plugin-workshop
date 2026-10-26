"""Kards 规则数据 — 全部来自官方 wiki（wiki.biligame.com/kards）与官方教程（decks.kards.com），不编造。

Kards 是冰岛工作室 1939 Games 开发的二战题材卡牌游戏（Steam appid 544810）。
本模块提供规则查询、卡组检查、术语解释等学习辅助功能，不做完整对局模拟
（Kards 机制复杂：前线控制、指挥点行动消耗、5种单位类型、20+种能力关键词、
国家特性等，完整模拟需大量卡牌数据，超出插件第一版范围）。
"""
from __future__ import annotations

# ══════════ 基本规则 ══════════

BASIC_RULES = """## Kards 基本规则

**开发商**：冰岛 1939 Games（Steam appid 544810），二战题材卡牌对战游戏。

**胜利条件**：摧毁敌方总部（HQ）。总部初始 20 点防御点数。

**卡组构成**：必须 40 张 = 1 张总部 + 39 张单位与指令。1 个主国 + 1 个盟国。

**手牌上限**：9 张，超过后抽到的牌自动丢弃。

**重新调度**：开局有一次机会更换起始手牌（选牌→随机替换等量卡牌）。

**士气伤害（疲劳）**：卡组抽空后抽牌，总部受递增伤害：第1次1点、第2次2点、第3次3点……以此类推。

**主要国（5个，选1个作为主国）**：德国、英国、苏联、美国、日本。
**盟国（可选1个搭配）**：法国、意大利、波兰、芬兰。
"""

# ══════════ 资源系统 ══════════

RESOURCE_RULES = """## 指挥点（Kredit）

指挥点是使用卡牌和单位行动（移动/攻击）的资源。

- 每回合获得 1 个指挥点槽，上限 12 个
- 卡牌效果可增加指挥点槽，最多可达 24 个
- 回合开始时，指挥点填满至当前槽位数
- 回合结束时，剩余指挥点不保留（不结转）
- 每张卡牌左上角显示指挥点花费
- 单位移动或攻击也需消耗指挥点（具体花费显示在单位卡上）

**黄色指挥点数字**的卡牌当前即可使用；**白色指挥点数字**的卡牌需要等指挥点上限提升后才能使用。
"""

# ══════════ 战场 ══════════

BATTLEFIELD_RULES = """## 战场（三条战线）

战场分为三条战线：

### 己方支援阵线
- 总部设立于此，单位首先部署于此
- 敌方单位不可移动至你的支援阵线
- **最多容纳 4 个单位**（加上总部共占 5 个位置）

### 前线
- 位于双方之间，是争夺的关键区域
- **同一时间只有一方玩家可以占领前线**
- **最多容纳 5 个单位**
- 步兵和坦克必须移动至前线，才能攻击敌方支援阵线上的总部和单位
- 将单位移入前线即宣告占领

### 敌方支援阵线
- 敌方总部和单位所在，你的单位不可进入

**撤退规则**：单位从前线撤退回到己方支援阵线；若支援阵线已满或直接从支援阵线撤退，则返回手牌。被控制的单位撤退时返回原拥有者手牌。
"""

# ══════════ 卡牌类型 ══════════

CARD_TYPES = """## 卡牌类型

### 单位
- 部署至战场，可移动和攻击
- 部署当回合通常不能行动（召唤眩晕），除非有「闪击」能力
- 防御力降至 0 以下时被消灭

### 指令
- 一次性效果，使用后进入墓地

### 反制指令
- 隐藏放置，激活后在对手回合满足条件时触发
- 激活时花费的指挥点对方不可见
- 可取消激活状态

### 总部
- 卡组必须且只能包含 1 张
- 初始 20 点防御，被摧毁即失败
"""

# ══════════ 单位类型 ══════════

UNIT_TYPES = """## 单位类型（5种）

### 步兵
- 只能攻击相邻战线中的敌人
- 陆军基础单位

### 坦克
- 可以在同一回合中移动并攻击（顺序不限）
- 只能攻击相邻战线中的敌人
- 装甲突击核心

### 炮兵
- 攻击时不会受到反击伤害
- 能够无视「被守护」状态
- 远程火力支援

### 轰炸机
- 攻击时不受反击伤害（除非攻击目标是战斗机）
- 能够无视「被守护」状态
- 防御时不会造成反击伤害
- **限制**：当敌方某战线有战斗机时，轰炸机无法攻击该战线中战斗机以外的目标

### 战斗机
- 可以阻止敌方轰炸机攻击位于同一战线中的其他目标
- 制空权核心
"""

# ══════════ 能力关键词 ══════════

KEYWORDS = {
    "闪击": "单位可以在加入战场的同一回合中行动（移动/攻击），无视召唤眩晕。",
    "部署": "部署效果在单位被部署时触发。",
    "亡计": "亡计效果在单位被消灭时触发。",
    "奋战": "具有奋战的单位在一回合中可以攻击两次。",
    "守护": "相邻的非守护单位无法被轰炸机和炮兵之外的敌军攻击。",
    "被守护": "此单位只能被轰炸机和炮兵攻击。",
    "伏击": "此单位每回合第一次受到攻击时，会首先造成伤害；如果敌方因此被消灭，则无法造成反击伤害。",
    "冲击": "冲击单位攻击时，被攻击单位不会造成任何反击伤害；攻击后移除冲击。优先级高于伏击。",
    "重甲": "单位被其他单位攻击时，受到的伤害减少（最多可叠加重甲3）。",
    "免疫": "单位或总部处于免疫状态时不会受到伤害。",
    "动员": "单位在其回合开始时获得 +1/+1；受到伤害后失去动员。",
    "烟幕": "单位无法被敌方单位攻击；移动或攻击后失去烟幕。前线单位、守护单位及战斗机无法具有烟幕。",
    "压制": "被压制的单位不能移动或攻击；状态于单位拥有者的下一个回合结束时解除。",
    "钳击": "部署时可选择另一个友方单位形成钳击；两个单位都在战场上时都获得钳击加成。",
    "山地": "部署或增加新的山地单位时，战场上每有一个其他山地单位，新单位获得 +1/+1。",
    "控制": "此单位的控制权已改变，新控制者可任意指挥；所有者指向原控制者。",
    "被动效果": "在战场上有特殊的持续游戏规则。",
}

# ══════════ 国家特色 ══════════

NATIONS = {
    "德国": {
        "role": "主要国",
        "style": "闪击战术与装甲突击，单位可回手重组",
        "desc": "德国依靠闪电战（Blitzkrieg）战术和装甲优势，擅长快速机动和单位回收重组。",
    },
    "英国": {
        "role": "主要国",
        "style": "坚定的步兵与持续性，防御稳固",
        "desc": "英国以顽强的步兵和持续作战能力著称，擅长防守反击和长线运营。",
    },
    "苏联": {
        "role": "主要国",
        "style": "凶猛的战斗关键词，人海与重装",
        "desc": "苏联强调凶猛的正面作战能力，大量战斗关键词配合人海战术和重装单位。",
    },
    "美国": {
        "role": "主要国",
        "style": "空军优势与卡组内升级激励",
        "desc": "美国掌握制空权，指令多聚焦于部队强化和卡组内升级，后期爆发力强。",
    },
    "日本": {
        "role": "主要国",
        "style": "步兵与空军支柱，特攻（kamikaze）能力与便宜指令",
        "desc": "日本以步兵和空军为支柱，单位可能不强但靠特攻（撤退）能力和廉价有效的指令弥补，适合快攻和前线压制。",
    },
    "法国": {
        "role": "盟国",
        "style": "抵抗运动，反抗与勇气",
        "desc": "法国通过抵抗运动机制体现反抗精神，适合作为盟国提供韧性和持续作战能力。",
    },
    "意大利": {
        "role": "盟国",
        "style": "盟国增援，扩展军备",
        "desc": "意大利作为盟国提供多样化的增援卡牌，扩展卡组战术选择。",
    },
    "波兰": {
        "role": "盟国",
        "style": "盟国增援，卡差与持续收益",
        "desc": "波兰作为盟国提供卡差优势和持续收益类卡牌，如蓝灰师、达努塔等。",
    },
    "芬兰": {
        "role": "盟国",
        "style": "盟国增援",
        "desc": "芬兰作为盟国提供特色增援卡牌。",
    },
}

# ══════════ 学习路径建议 ══════════

LEARNING_PATH = """## Kards 学习路径建议

### 第1阶段：基础（1-3天）
1. 完成游戏内新手教程
2. 理解三条战线（支援线/前线/敌方支援线）和前线争夺机制
3. 掌握指挥点系统（每回合+1上限、不结转、单位行动也消耗）
4. 理解5种单位类型的差异（步兵/坦克/炮兵/轰炸机/战斗机）
5. 用系统赠送的卡组打 10-20 局人机

### 第2阶段：进阶（1-2周）
1. 学习核心能力关键词：闪击、部署、亡计、守护、伏击、冲击、重甲
2. 理解空军机制：战斗机拦截轰炸机、轰炸机无视守护
3. 学会前线控制的战略意义（控制前线才能攻击总部）
4. 开始构筑自己的卡组（40张=1总部+39张，1主国+1盟国）
5. 研究 2-3 个主流卡组 archetype

### 第3阶段：精通（持续）
1. 掌握所有 20+ 种能力关键词
2. 理解国家特色与卡组配合
3. 学习调度（mulligan）策略
4. 掌握底池赔率与节奏控制
5. 观看高手对局和卡组分析

### 推荐资源
- 官方教程：https://decks.kards.com/zh/how-to-play
- 官方卡组数据库：https://decks.kards.com/zh
- B站 KARDSWIKI：https://wiki.biligame.com/kards/
- Steam 社区：https://store.steampowered.com/app/544810
"""


# ══════════ 公开查询函数 ══════════


def get_rules(topic: str = "") -> str:
    """查询 Kards 规则。topic 可选：basic/resource/battlefield/card_types/unit_types/learning/all。"""
    topic = (topic or "").lower().strip()
    sections = {
        "basic": BASIC_RULES,
        "resource": RESOURCE_RULES,
        "battlefield": BATTLEFIELD_RULES,
        "card_types": CARD_TYPES,
        "unit_types": UNIT_TYPES,
        "learning": LEARNING_PATH,
    }
    if topic in sections:
        return sections[topic]
    if topic == "all" or not topic:
        return "\n\n".join([BASIC_RULES, RESOURCE_RULES, BATTLEFIELD_RULES,
                            CARD_TYPES, UNIT_TYPES, LEARNING_PATH])
    # 模糊匹配
    for key, val in sections.items():
        if topic in key or key in topic:
            return val
    return f"未找到主题 '{topic}'。可用主题：{', '.join(sections.keys())}，all（全部）。"


def get_keyword(name: str = "") -> str:
    """查询能力关键词。不填则列出全部关键词。"""
    if not name:
        lines = ["## Kards 能力关键词（全部）\n"]
        for k, v in KEYWORDS.items():
            lines.append(f"- **{k}**：{v}")
        return "\n".join(lines)
    for k, v in KEYWORDS.items():
        if name.lower() in k.lower() or k.lower() in name.lower():
            return f"**{k}**：{v}"
    return f"未找到关键词 '{name}'。可用：{', '.join(KEYWORDS.keys())}"


def get_nation(name: str = "") -> str:
    """查询国家特色。不填则列出全部国家。"""
    if not name:
        lines = ["## Kards 国家列表\n"]
        for n, info in NATIONS.items():
            lines.append(f"- **{n}**（{info['role']}）：{info['style']}")
        return "\n".join(lines)
    for n, info in NATIONS.items():
        if name.lower() in n.lower() or n.lower() in name.lower():
            return f"**{n}**（{info['role']}）\n- 风格：{info['style']}\n- 说明：{info['desc']}"
    return f"未找到国家 '{name}'。可用：{', '.join(NATIONS.keys())}"


def check_deck(deck: list[dict]) -> dict:
    """检查卡组构成是否合法。

    deck: [{name, type, nation, count}]，type: HQ/unit/order，nation: 国家名
    返回 {valid, errors, warnings, summary}
    """
    total = sum(c.get("count", 1) for c in deck)
    hq_count = sum(c.get("count", 1) for c in deck if c.get("type", "").lower() in ("hq", "总部", "headquarters"))
    unit_count = sum(c.get("count", 1) for c in deck if c.get("type", "").lower() in ("unit", "单位"))
    order_count = sum(c.get("count", 1) for c in deck if c.get("type", "").lower() in ("order", "指令", "counter", "反制"))

    nations = set(c.get("nation", "") for c in deck if c.get("nation"))
    main_nations = {"德国", "英国", "苏联", "美国", "日本"}
    ally_nations = {"法国", "意大利", "波兰", "芬兰"}

    errors = []
    warnings = []

    if total != 40:
        errors.append(f"卡组必须 40 张，当前 {total} 张")
    if hq_count != 1:
        errors.append(f"必须且只能包含 1 张总部，当前 {hq_count} 张")
    if unit_count + order_count + hq_count != total:
        warnings.append(f"卡牌类型统计不匹配（单位{unit_count}+指令{order_count}+总部{hq_count}={unit_count+order_count+hq_count} ≠ {total}），请检查 type 字段")

    deck_nations = nations - {""}
    main_in_deck = deck_nations & main_nations
    ally_in_deck = deck_nations & ally_nations
    other_nations = deck_nations - main_nations - ally_nations

    if len(main_in_deck) > 1:
        errors.append(f"只能有 1 个主要国，当前有 {len(main_in_deck)} 个：{', '.join(main_in_deck)}")
    if len(ally_in_deck) > 1:
        errors.append(f"只能有 1 个盟国，当前有 {len(ally_in_deck)} 个：{', '.join(ally_in_deck)}")
    if other_nations:
        errors.append(f"未知国家：{', '.join(other_nations)}（主要国：德国/英国/苏联/美国/日本；盟国：法国/意大利/波兰/芬兰）")
    if not main_in_deck:
        warnings.append("未检测到主要国卡牌，请确认卡组包含主国")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "total": total,
            "headquarters": hq_count,
            "units": unit_count,
            "orders": order_count,
            "main_nations": sorted(main_in_deck),
            "ally_nations": sorted(ally_in_deck),
        },
    }
