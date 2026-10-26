"""Game catalogue for the Half-Life / Portal player plugin.

Everything that differs between the supported Valve games lives here: Steam
app ids, executable names, window-title patterns, default key bindings, HUD
geometry, chapter hints and a short strategy brief.  The rest of the plugin is
deliberately game-agnostic and reads this table.

The data was gathered from files actually present on the reference machine:

* ``Half-Life/valve/config.cfg``      -> HL1 binds, sensitivity, m_yaw
* ``Portal 2/portal2/cfg/config.cfg`` -> Portal 2 binds, sensitivity
* Steam ``appmanifest_*.acf``         -> app ids and install directories

Only *public, documented* default bindings are used.  A user with a custom
layout can override every key in ``plugin.toml`` without touching code.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "CHAPTERS",
    "GAMES",
    "GAME_BY_ID",
    "GameProfile",
    "SUPPORTED_IDS",
    "aliases_for",
    "describe_installed",
    "family_games",
    "normalise_text",
    "resolve_game",
]


# ---------------------------------------------------------------------------
# Key maps
# ---------------------------------------------------------------------------

#: Canonical action -> default key.  ``mouse1`` / ``mouse2`` are understood by
#: :mod:`input` as mouse buttons, not keyboard scan codes.
HL1_KEYS: dict[str, str] = {
    "forward": "w",
    "back": "s",
    "left": "a",
    "right": "d",
    "use": "e",
    "jump": "space",
    "crouch": "ctrl",
    "walk": "shift",
    "sprint": "",
    "reload": "r",
    "flashlight": "f",
    "attack": "mouse1",
    "alt_attack": "mouse2",
    "last_weapon": "q",
    "weapon_1": "1",
    "weapon_2": "2",
    "weapon_3": "3",
    "weapon_4": "4",
    "weapon_5": "5",
}

HL2_KEYS: dict[str, str] = {
    "forward": "w",
    "back": "s",
    "left": "a",
    "right": "d",
    "use": "e",
    "jump": "space",
    "crouch": "ctrl",
    "walk": "shift",
    "sprint": "shift",
    "reload": "r",
    "flashlight": "f",
    "attack": "mouse1",
    "alt_attack": "mouse2",
    "last_weapon": "q",
    "weapon_1": "1",
    "weapon_2": "2",
    "weapon_3": "3",
    "weapon_4": "4",
    "weapon_5": "5",
    "weapon_6": "6",
    "gravity_gun": "2",
}

PORTAL_KEYS: dict[str, str] = {
    "forward": "w",
    "back": "s",
    "left": "a",
    "right": "d",
    "use": "e",
    "jump": "space",
    "crouch": "ctrl",
    "walk": "",
    "sprint": "",
    "reload": "",
    "flashlight": "",
    "attack": "mouse1",
    "alt_attack": "mouse2",
    "portal_blue": "mouse1",
    "portal_orange": "mouse2",
    "last_weapon": "",
    "weapon_1": "1",
}

PORTAL2_KEYS: dict[str, str] = {
    **PORTAL_KEYS,
    # Portal 2 keeps LMB/RMB as blue/orange and uses E to pick up cubes.
    # R is swap_ss_input and Q is taunt -- neither is a reload/last-weapon key,
    # so leave them empty rather than sending a surprising command.
    "reload": "",
    "last_weapon": "",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chapter:
    """One chapter / test-chamber group with a short tactical hint."""

    title: str
    hint: str

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "hint": self.hint}


@dataclass(frozen=True)
class GameProfile:
    id: str
    name_zh: str
    name_en: str
    family: str          # hl1 | hl2 | portal
    engine: str          # goldsrc | source | source2(vr)
    app_id: str
    exe: str
    install_dirs: tuple[str, ...]
    title_patterns: tuple[str, ...]
    aliases: tuple[str, ...]
    keys: Mapping[str, str]
    strategy: str
    chapters: tuple[Chapter, ...] = ()
    supports: tuple[str, ...] = ()
    hud_style: str = "source"
    notes: str = ""
    vr_only: bool = False
    multiplayer_only: bool = False
    coop_only: bool = False

    # -- presentation -----------------------------------------------------

    @property
    def display(self) -> str:
        return self.name_zh

    @property
    def playable(self) -> bool:
        return not (self.vr_only or self.multiplayer_only or self.coop_only)

    def supports_feature(self, feature: str) -> bool:
        return feature in self.supports

    def key(self, action: str, overrides: Mapping[str, str] | None = None) -> str:
        if overrides and action in overrides and overrides[action]:
            return str(overrides[action])
        return str(self.keys.get(action, "") or "")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name_zh,
            "name_en": self.name_en,
            "family": self.family,
            "engine": self.engine,
            "app_id": self.app_id,
            "exe": self.exe,
            "playable": self.playable,
            "vr_only": self.vr_only,
            "multiplayer_only": self.multiplayer_only,
            "coop_only": self.coop_only,
            "supports": list(self.supports),
            "chapters": len(self.chapters),
            "notes": self.notes,
        }

    def strategy_brief(self, *, max_chapters: int = 6) -> str:
        """Compact brief injected into prompts/status (never a wall of text)."""
        chapters = list(self.chapters[:max_chapters])
        tail = ""
        if len(self.chapters) > len(chapters):
            tail = f"…共 {len(self.chapters)} 章"
        chapter_text = "；".join(f"{c.title}：{c.hint}" for c in chapters) + tail
        parts = [f"{self.name_zh}（{self.name_en}）", self.strategy]
        if chapter_text:
            parts.append("章节提示：" + chapter_text)
        if self.notes:
            parts.append(self.notes)
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Chapter hints
# ---------------------------------------------------------------------------

HL1_CHAPTERS = (
    Chapter("黑山基地入场", "坐完电车后跟着保安走，闸门按 E，别乱跳。"),
    Chapter("异常材料", "穿 HEV 防护服，进测试舱，把样本车推到光束下。"),
    Chapter("不可预见的后果", "爆炸后爬出来，撬棍打碎木箱，沿幸存者的方向去办公区。"),
    Chapter("办公区", "线性走廊，遇到陆战队先找掩体，点射而不是扫射。"),
    Chapter("我们被盯上了", "敌人火力猛，优先用掩体；找通风管绕后。"),
    Chapter("爆炸坑", "启动燃料/氧气/电力三个开关，避开触手，乘火箭测试台升降。"),
    Chapter("电力供应", "恢复电力后坐轨道车，留意高处狙击手。"),
    Chapter("在铁轨上", "轨道车关卡，清除路障，遇岔路先看红灯。"),
    Chapter("逮捕", "被抓后醒来，先拿回装备，蹲着穿过管道。"),
    Chapter("残留物处理", "传送带与粉碎机谜题，蹲下钻管道，别被压死。"),
    Chapter("可疑伦理", "实验室区域，激光和异形，先清场再解谜。"),
    Chapter("地表张力", "开阔地，先找沙袋/岩石掩体，注意远处狙击手。"),
    Chapter("忘记弗里曼", "城市战斗，跟着友军小队推进，别一个人冲。"),
    Chapter("Lambda 核心", "启动传送门前先清场，进入 Xen 后注意低重力。"),
    Chapter("Xen", "低重力跳跃，沿着发光平台找传送门，掉下去会死。"),
    Chapter("冈纳克巢穴", "Boss 打腹部；它产小怪时先清小怪再输出。"),
    Chapter("闯入者", "Xen 工厂，避开炮塔，用掩体接近。"),
    Chapter("尼赫兰斯", "最终 Boss：打头部，躲传送球，利用平台回血。"),
    Chapter("结局", "G-Man 对话，按 E/前进继续。"),
)

BLACK_MESA_CHAPTERS = (
    Chapter("黑山基地入场", "电车到站后跟随保安，按 E 开闸门。"),
    Chapter("异常材料", "穿 HEV 服，进测试舱，推样本车。"),
    Chapter("不可预见的后果", "爆炸后爬出，拿撬棍，沿办公区推进。"),
    Chapter("办公区", "走廊遭遇战，找掩体点射。"),
    Chapter("我们被盯上了", "陆战队强攻，利用手雷和掩体。"),
    Chapter("爆炸坑", "三个开关启动火箭，避开触手。"),
    Chapter("电力供应", "恢复电力，乘轨道车。"),
    Chapter("在铁轨上", "轨道车清障，注意狙击手。"),
    Chapter("逮捕", "醒来后夺回装备，钻管道。"),
    Chapter("残留物处理", "粉碎机与传送带，蹲行。"),
    Chapter("可疑伦理", "实验室激光与异形。"),
    Chapter("地表张力", "开阔地掩体推进。"),
    Chapter("忘记弗里曼", "城市巷战，跟小队。"),
    Chapter("Lambda 核心", "传送至 Xen。"),
    Chapter("Xen", "重制版 Xen 更立体，沿发光路径跳。"),
    Chapter("冈纳克巢穴", "打腹部，清小怪。"),
    Chapter("闯入者", "工厂炮塔，掩体接近。"),
    Chapter("尼赫兰斯", "打头，躲传送球。"),
)

HL2_CHAPTERS = (
    Chapter("点插入", "火车站跟随守卫，上车，别乱开枪。"),
    Chapter("红色信件日", "克莱纳实验室，拿 HEV 服和撬棍，进传送机。"),
    Chapter("运河路线", "下水道与警察，沿运河走，找汽艇。"),
    Chapter("水灾", "汽艇关卡，过闸门，用汽艇撞/绕开敌人。"),
    Chapter("黑山基地东", "见伊莱和爱丽克斯，拿重力枪。"),
    Chapter("别去莱文霍姆", "僵尸镇，跟神父走，用锯片/陷阱，护送幸存者。"),
    Chapter("17 号公路", "越野车关卡，过桥，加油站补给。"),
    Chapter("沙地陷阱", "海滩蚁狮，先铺沙袋/木板，再撤油器。"),
    Chapter("新星矿场", "监狱潜入，找爱丽克斯，别触发警报。"),
    Chapter("纠缠", "传送门实验室，守住传送门，用炮塔。"),
    Chapter("反公民一号", "城市起义，跟小队推进。"),
    Chapter("跟随弗里曼", "联合军大战，摧毁三脚机甲。"),
    Chapter("我们的恩人", "堡垒内部，重力枪强化后吸球。"),
    Chapter("暗能量", "最终 Boss，吸能量球砸反应堆。"),
)

EP1_CHAPTERS = (
    Chapter("不当警报", "从城堡逃出，帮爱丽克斯，躲联合军。"),
    Chapter("直接干预", "关闭传送门，核心区，注意坠落。"),
    Chapter("低生活", "地下医院，僵尸，找电梯。"),
    Chapter("城市逃生", "街道战斗，跟爱丽克斯去车站。"),
    Chapter("17 号出口", "护送市民上火车，守住月台。"),
)

EP2_CHAPTERS = (
    Chapter("到白森林", "森林与桥，开车，注意直升机。"),
    Chapter("这只沃塔利克", "蚁狮巢，救爱丽克斯，拿心结。"),
    Chapter("弗里曼主教", "教堂防御，布置炮塔。"),
    Chapter("副驾驶", "车战，打直升机。"),
    Chapter("雷达之下", "导弹基地，发射井，拿火箭。"),
    Chapter("我们共同的敌人", "白森林防御，打三脚机甲。"),
    Chapter("T 减一", "最终火箭防御，撑到发射。"),
)

PORTAL_CHAPTERS = (
    Chapter("测试室 00-09", "基础传送门：蓝门/橙门，按钮，方块，跳。"),
    Chapter("测试室 10-15", "激光、炮塔、信仰之跃；先观察再动。"),
    Chapter("测试室 16-19", "复杂平台与按钮，利用动量。"),
    Chapter("逃脱", "火灾后进后台，避开 GLaDOS。"),
    Chapter("最终战", "打 GLaDOS 的球，用传送门把球扔进熔炉。"),
)

PORTAL2_CHAPTERS = (
    Chapter("欢迎回来", "醒来后跟随，拿传送门枪。"),
    Chapter("冷启动", "基础测试：按钮、方块、蓝橙门。"),
    Chapter("回归", "光桥、激光、凝胶。"),
    Chapter("惊喜", "炮塔与信仰之跃。"),
    Chapter("逃脱", "破坏计划，后台区域。"),
    Chapter("坠落", "旧光圈地下，找路。"),
    Chapter("重聚", "与惠特利会合，替换核心。"),
    Chapter("痒", "核心替换与最终测试。"),
    Chapter("杀死你的部分", "最终测试，注意炮塔。"),
    Chapter("终曲", "月球传送门，按提示做。"),
)

LOST_COAST_CHAPTERS = (
    Chapter("Lost Coast", "短关卡，沿路走到教堂，用重力枪解谜。"),
)


# ---------------------------------------------------------------------------
# Game profiles
# ---------------------------------------------------------------------------

GAMES: tuple[GameProfile, ...] = (
    GameProfile(
        id="half_life",
        name_zh="半条命",
        name_en="Half-Life",
        family="hl1",
        engine="goldsrc",
        app_id="70",
        exe="hl.exe",
        install_dirs=("Half-Life",),
        title_patterns=(r"^Half-Life$", r"^Half-Life\b(?!.*(?:Source|Opposing|Blue))"),
        aliases=(
            "半条命", "半衰期", "半条命1", "半条命一", "半条命1998",
            "half-life", "half life", "hl1", "hl", "黑山起源",
        ),
        keys=HL1_KEYS,
        strategy=(
            "线性走廊 FPS：沿光源、门和地上的黄色导引走；门/按钮按 E；"
            "通风管按 Ctrl 蹲行；遇到敌人先停住、把准星对上去再点射，"
            "不要边跑边扫。血包和 HEV 充电站通常在墙角。"
        ),
        chapters=HL1_CHAPTERS,
        supports=("combat", "flashlight", "crouch", "platform", "puzzle_physics"),
        hud_style="hl1",
        notes="Shift 是走路不是冲刺；F 是手电；GoldSrc 的准星不一定像 Source 那样在敌人身上变红。",
    ),
    GameProfile(
        id="half_life_source",
        name_zh="半条命：起源",
        name_en="Half-Life: Source",
        family="hl1",
        engine="source",
        app_id="280",
        exe="hl2.exe",
        install_dirs=("Half-Life 2", "Half-Life Source"),
        title_patterns=(r"Half-Life:\s*Source",),
        aliases=("半条命起源", "半条命 source", "hl source", "hls"),
        keys=HL2_KEYS,
        strategy="HL1 的 Source 移植版，操作接近 HL2：Shift 冲刺、E 使用、Ctrl 蹲。",
        chapters=HL1_CHAPTERS,
        supports=("combat", "flashlight", "crouch", "platform"),
        hud_style="hl2",
        notes="窗口标题必须带 Source 才会被认成这一项。",
    ),
    GameProfile(
        id="opposing_force",
        name_zh="半条命：针锋相对",
        name_en="Half-Life: Opposing Force",
        family="hl1",
        engine="goldsrc",
        app_id="50",
        exe="hl.exe",
        install_dirs=("Half-Life",),
        title_patterns=(r"Opposing Force", r"针锋相对"),
        aliases=("针锋相对", "对抗力量", "opposing force", "op4", "opfor"),
        keys=HL1_KEYS,
        strategy="HL1 资料片，扮演士兵；键位与 HL1 相同，多用掩体和手雷。",
        chapters=HL1_CHAPTERS,
        supports=("combat", "flashlight", "crouch"),
        hud_style="hl1",
        notes="与 HL1 共用 hl.exe，只有窗口标题能区分；标题被改过时会退回 HL1。",
    ),
    GameProfile(
        id="blue_shift",
        name_zh="半条命：蓝色行动",
        name_en="Half-Life: Blue Shift",
        family="hl1",
        engine="goldsrc",
        app_id="130",
        exe="hl.exe",
        install_dirs=("Half-Life",),
        title_patterns=(r"Blue Shift", r"蓝色行动"),
        aliases=("蓝色行动", "蓝移", "blue shift", "bshift"),
        keys=HL1_KEYS,
        strategy="HL1 资料片，扮演保安；键位与 HL1 相同，优先保护 NPC。",
        chapters=HL1_CHAPTERS,
        supports=("combat", "flashlight", "crouch"),
        hud_style="hl1",
        notes="与 HL1 共用 hl.exe，只有窗口标题能区分。",
    ),
    GameProfile(
        id="black_mesa",
        name_zh="黑山",
        name_en="Black Mesa",
        family="hl1",
        engine="source",
        app_id="362890",
        exe="bms.exe",
        install_dirs=("Black Mesa",),
        title_patterns=(r"Black Mesa", r"黑山"),
        aliases=(
            "黑山", "黑山基地", "黑山重制版", "半条命重制版", "半衰期重制版",
            "black mesa", "bms", "bm",
        ),
        keys=HL2_KEYS,
        strategy=(
            "HL1 的 Source 重制版：Shift 冲刺、Ctrl 蹲、E 使用、F 手电；"
            "Xen 章节更立体，沿发光平台走，别在边缘恋战。"
        ),
        chapters=BLACK_MESA_CHAPTERS,
        supports=("combat", "flashlight", "crouch", "platform", "puzzle_physics"),
        hud_style="hl2",
        notes="这是「半条命重制版」通常指的游戏；没装时会退回《半条命》。",
    ),
    GameProfile(
        id="half_life_2",
        name_zh="半条命 2",
        name_en="Half-Life 2",
        family="hl2",
        engine="source",
        app_id="220",
        exe="hl2.exe",
        install_dirs=("Half-Life 2",),
        title_patterns=(r"^Half-Life 2$", r"^Half-Life 2\s*-\s*"),
        aliases=("半条命2", "半条命二", "半衰期2", "half-life 2", "half life 2", "hl2"),
        keys=HL2_KEYS,
        strategy=(
            "线性 FPS + 物理谜题：E 使用/捡物，Shift 冲刺，Ctrl 蹲；"
            "重力枪（2）能吸起物体、也能用来挡子弹/解谜；"
            "遇到载具按住 W，鼠标控制方向；队友会帮你开门。"
        ),
        chapters=HL2_CHAPTERS,
        supports=("combat", "flashlight", "crouch", "vehicle", "gravity_gun", "squad"),
        hud_style="hl2",
        notes="重力枪在武器槽 2；载具关不要下车恋战。",
    ),
    GameProfile(
        id="hl2_ep1",
        name_zh="半条命 2：第一章",
        name_en="Half-Life 2: Episode One",
        family="hl2",
        engine="source",
        app_id="380",
        exe="hl2.exe",
        install_dirs=("Half-Life 2",),
        title_patterns=(r"Episode One", r"第一章"),
        aliases=(
            "半条命2第一章", "半条命二第一章", "第一章", "ep1", "episode one",
            "hl2 ep1", "半条命2 ep1",
        ),
        keys=HL2_KEYS,
        strategy="延续 HL2：跟爱丽克斯走，多用重力枪和掩体；城堡内注意坠落。",
        chapters=EP1_CHAPTERS,
        supports=("combat", "flashlight", "crouch", "gravity_gun", "squad"),
        hud_style="hl2",
    ),
    GameProfile(
        id="hl2_ep2",
        name_zh="半条命 2：第二章",
        name_en="Half-Life 2: Episode Two",
        family="hl2",
        engine="source",
        app_id="420",
        exe="hl2.exe",
        install_dirs=("Half-Life 2",),
        title_patterns=(r"Episode Two", r"第二章"),
        aliases=(
            "半条命2第二章", "半条命二第二章", "第二章", "ep2", "episode two",
            "hl2 ep2", "半条命2 ep2",
        ),
        keys=HL2_KEYS,
        strategy="载具与防守关较多：开车时按住 W，防守时找掩体、布炮塔、优先打三脚机甲。",
        chapters=EP2_CHAPTERS,
        supports=("combat", "flashlight", "crouch", "vehicle", "gravity_gun", "turret"),
        hud_style="hl2",
    ),
    GameProfile(
        id="hl2_lost_coast",
        name_zh="半条命 2：消失的海岸线",
        name_en="Half-Life 2: Lost Coast",
        family="hl2",
        engine="source",
        app_id="340",
        exe="hl2.exe",
        install_dirs=("Half-Life 2",),
        title_patterns=(r"Lost Coast", r"消失的海岸线"),
        aliases=("消失的海岸线", "迷失海岸", "lost coast", "hl2 lost coast"),
        keys=HL2_KEYS,
        strategy="短技术演示关：沿路到教堂，用重力枪解谜，遇到联合军找掩体。",
        chapters=LOST_COAST_CHAPTERS,
        supports=("combat", "gravity_gun"),
        hud_style="hl2",
    ),
    GameProfile(
        id="portal",
        name_zh="传送门",
        name_en="Portal",
        family="portal",
        engine="source",
        app_id="400",
        exe="hl2.exe",
        install_dirs=("Portal",),
        title_patterns=(r"^Portal$", r"^Portal\b(?!\s*2)"),
        aliases=("传送门", "传送门1", "传送门一", "portal", "portal 1", "光圈科技"),
        keys=PORTAL_KEYS,
        strategy=(
            "解谜优先：先站着环顾四周，找白色可放传送门的面板；"
            "左键蓝门、右键橙门、E 捡方块；按钮要压住或把方块放上去；"
            "看到出口/按钮再动，别乱跳进酸液。"
        ),
        chapters=PORTAL_CHAPTERS,
        supports=("puzzle_portal", "crouch", "platform"),
        hud_style="portal",
        notes="Portal 1 没有冲刺；R 不是换弹。",
    ),
    GameProfile(
        id="portal2",
        name_zh="传送门 2",
        name_en="Portal 2",
        family="portal",
        engine="source",
        app_id="620",
        exe="portal2.exe",
        install_dirs=("Portal 2",),
        title_patterns=(r"PORTAL\s*2", r"^Portal 2"),
        aliases=("传送门2", "传送门二", "portal 2", "portal2", "p2"),
        keys=PORTAL2_KEYS,
        strategy=(
            "先观察再开枪：白色面板才能放传送门；左键蓝、右键橙、E 拿方块；"
            "光桥/激光/凝胶要按顺序处理；卡住时退后看全景，别在同一个点反复试。"
        ),
        chapters=PORTAL2_CHAPTERS,
        supports=("puzzle_portal", "crouch", "platform", "gels", "lasers"),
        hud_style="portal2",
        notes="合作模式需要两个玩家，本插件只玩单人。",
    ),
    GameProfile(
        id="half_life_alyx",
        name_zh="半条命：爱莉克斯",
        name_en="Half-Life: Alyx",
        family="hl1",
        engine="source2",
        app_id="546560",
        exe="hlvr.exe",
        install_dirs=("Half-Life Alyx",),
        title_patterns=(r"Half-Life:\s*Alyx", r"爱莉克斯"),
        aliases=("半条命爱莉克斯", "半条命alyx", "爱莉克斯", "alyx", "hl alyx"),
        keys={},
        strategy="VR 专用，需要头显和双手柄；键鼠无法正常游玩。",
        supports=(),
        hud_style="vr",
        notes="VR-only：插件会明确拒绝，而不是假装能玩。",
        vr_only=True,
    ),
)

GAME_BY_ID: dict[str, GameProfile] = {game.id: game for game in GAMES}
SUPPORTED_IDS: tuple[str, ...] = tuple(game.id for game in GAMES)

#: Chapter hints keyed by game id, for callers that want the raw table rather
#: than a whole ``GameProfile``.
CHAPTERS: dict[str, tuple[Chapter, ...]] = {gid: game.chapters for gid, game in GAME_BY_ID.items()}

FAMILIES: dict[str, tuple[str, ...]] = {
    "hl1": ("half_life", "black_mesa", "half_life_source", "opposing_force", "blue_shift"),
    "hl2": ("half_life_2", "hl2_ep1", "hl2_ep2", "hl2_lost_coast"),
    "portal": ("portal", "portal2"),
}

#: Family aliases, checked only after every specific alias missed.
FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "hl1": ("半条命", "半衰期", "half-life", "half life", "hl1", "黑山", "black mesa"),
    "hl2": ("半条命2", "半条命二", "half-life 2", "half life 2", "hl2"),
    "portal": ("传送门", "portal"),
}

MAINLINE = {"hl1": "half_life", "hl2": "half_life_2", "portal": "portal"}


# ---------------------------------------------------------------------------
# Text normalisation / resolution
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[\s\u3000·•,，。.!！?？:：;；'\"“”‘’、/\\|()（）\[\]【】<>《》_\-—+]+")


def normalise_text(text: Any) -> str:
    """Lowercase, NFKC-normalise and strip punctuation for alias matching."""
    raw = unicodedata.normalize("NFKC", str(text or "")).lower()
    return _PUNCT.sub("", raw)


_ALIAS_INDEX: list[tuple[str, str]] = sorted(
    (
        (normalise_text(alias), game.id)
        for game in GAMES
        for alias in game.aliases
        if normalise_text(alias)
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)


def aliases_for(game_id: str) -> tuple[str, ...]:
    game = GAME_BY_ID.get(game_id)
    return tuple(game.aliases) if game else ()


def family_games(family: str) -> tuple[GameProfile, ...]:
    return tuple(GAME_BY_ID[gid] for gid in FAMILIES.get(family, ()) if gid in GAME_BY_ID)


@dataclass(frozen=True)
class Resolution:
    """Result of interpreting a "go play X" sentence."""

    game: GameProfile | None
    reason: str = ""
    assumed: bool = False
    candidates: tuple[GameProfile, ...] = ()
    unsupported: GameProfile | None = None

    @property
    def ok(self) -> bool:
        return self.game is not None

    def summary(self) -> dict[str, Any]:
        return {
            "game": self.game.id if self.game else None,
            "name": self.game.name_zh if self.game else None,
            "reason": self.reason,
            "assumed": self.assumed,
            "candidates": [g.id for g in self.candidates],
            "unsupported": self.unsupported.id if self.unsupported else None,
        }


def _pick_from_family(
    family: str,
    *,
    installed: Iterable[str],
    running: Iterable[str],
) -> GameProfile | None:
    installed_set = set(installed)
    running_set = set(running)
    members = family_games(family)
    for game in members:
        if game.id in running_set and game.playable:
            return game
    for game in members:
        if game.id in installed_set and game.playable:
            return game
    # Nothing installed: still return the mainline so the plugin can tell the
    # user exactly what to open.
    for game in members:
        if game.id == MAINLINE.get(family):
            return game
    return members[0] if members else None


def resolve_game(
    text: Any,
    *,
    installed: Iterable[str] = (),
    running: Iterable[str] = (),
) -> Resolution:
    """Resolve a free-form request such as ``去玩半条命重制版`` to a profile.

    Resolution order is deliberately specific-before-generic:

    1. an explicit game alias (longest first, so ``半条命2`` beats ``半条命``);
    2. a family alias, preferring a running game, then an installed mainline;
    3. a VR/unsupported mention, so the caller can explain *why* it is refused.
    """
    body = normalise_text(text)
    if not body:
        return Resolution(None, reason="empty")

    # "半条命重制版" is ambiguous on a machine that owns Half-Life but not Black
    # Mesa.  Resolve it to Black Mesa only when that game is actually present;
    # otherwise play the Half-Life the user owns and say the assumption out loud
    # instead of silently selecting the wrong game (or refusing the request).
    remaster = any(token in body for token in ("重制版", "重置版", "remaster", "remake"))
    explicit_black_mesa = any(
        token in body for token in ("黑山", "blackmesa", "black mesa", "bms")
    )
    if remaster and not explicit_black_mesa:
        installed_set = set(installed)
        running_set = set(running)
        if "black_mesa" not in installed_set and "black_mesa" not in running_set:
            if "half_life" in installed_set or "half_life" in running_set:
                return Resolution(
                    GAME_BY_ID["half_life"],
                    reason="remaster-fallback",
                    assumed=True,
                )

    for alias, game_id in _ALIAS_INDEX:
        if alias and alias in body:
            game = GAME_BY_ID[game_id]
            if not game.playable:
                return Resolution(None, reason="unsupported", unsupported=game)
            return Resolution(game, reason=f"alias:{alias}")

    # "重制版" without "黑山" is usually Black Mesa, but only claim that when it
    # is actually installed; otherwise fall back to the HL1 the user owns and
    # say so explicitly (the user should not get a silent wrong game).
    if any(token in body for token in ("重制版", "重置版", "remaster", "remake")):
        installed_set = set(installed)
        running_set = set(running)
        if "black_mesa" in running_set or "black_mesa" in installed_set:
            return Resolution(GAME_BY_ID["black_mesa"], reason="remaster")
        if "half_life" in running_set or "half_life" in installed_set:
            return Resolution(
                GAME_BY_ID["half_life"],
                reason="remaster-fallback",
                assumed=True,
            )

    for family, aliases in FAMILY_ALIASES.items():
        if any(normalise_text(alias) in body for alias in aliases):
            game = _pick_from_family(family, installed=installed, running=running)
            if game is not None:
                return Resolution(game, reason=f"family:{family}", assumed=True)

    # A bare "半条命" / "传送门" already matched a specific alias (mainline), so
    # reaching here means nothing recognisable was said.
    candidates = tuple(
        GAME_BY_ID[gid] for gid in SUPPORTED_IDS
        if gid in set(installed) and GAME_BY_ID[gid].playable
    )
    return Resolution(None, reason="no-match", candidates=candidates)


def describe_installed(installed: Iterable[str]) -> str:
    names = [GAME_BY_ID[gid].name_zh for gid in installed if gid in GAME_BY_ID]
    return "、".join(names) if names else "（未检测到已安装的系列游戏）"
