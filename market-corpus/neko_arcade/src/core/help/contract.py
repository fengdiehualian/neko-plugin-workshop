# -*- coding: utf-8 -*-
"""帮助配置契约: 把游戏侧的 help.json 归一化成文档模型, 并做三层寻址。

层次(与插件统一帮助系统一致):

    游戏 → 分组(group) → 指令(command)

设计原则:
- **向后兼容**: 老的 ``{"text": ..., "commands": [["指令", "说明"], ...]}`` 完全照旧;
  ``groups`` 是新增的可选字段, 不写就还是"扁平单页帮助", 10 个既有游戏零改动。
- **游戏只给数据**: 分组名/别名/语义图标名/版式块/指令表 —— 样式全部由插件主题决定。
- **零配置兜底**: ``auto_groups: true`` 时按命名模式自动聚簇, 细分的游戏也能自动得到分组。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 帮助意图: 聊天里出现这些词时, 由插件统一拦截并渲染帮助(游戏无需实现)
HELP_INTENT_WORDS = ("帮助", "攻略", "玩法", "怎么玩", "说明", "指令表", "help")

# 指令数达到该值时, 游戏可用 ``"auto_groups": true`` 请插件代劳分组(见 auto_groups)
AUTO_GROUP_MIN = 6

# 自动分组规则表: (命中关键词, 语义图标名, 分组名) —— 顺序即优先级, 先命中先用。
# 插件侧通用规则, 覆盖当前全部小游戏的指令词汇; 新增游戏若词汇特殊, 加一行即可。
GROUP_RULES: Sequence[Tuple[Sequence[str], str, str]] = (
    (("签到", "每日", "周年", "任务", "成就", "领奖", "提交"), "daily", "日常与任务"),
    (("排行", "排行榜", "天榜", "榜"), "star", "排行榜"),
    (("探索", "打劫", "切磋", "讨伐", "团本", "秘境", "比试", "开枪", "装弹",
      "逃跑", "攻打", "战斗", "挑战", "渡劫"), "sword", "冒险与对战"),
    (("钓", "抛竿", "猜", "占卜", "塔罗", "抽牌", "海龟汤", "提问", "猜汤底",
      "下注", "轮盘"), "star", "玩法"),
    (("发图", "来张图", "看图", "自拍", "照片"), "heart", "看图与晒图"),
    (("背包", "纳戒", "物品", "道具", "装备", "鱼缸", "仓库", "卸下", "穿戴", "服用"),
     "bag", "背包与装备"),
    (("商店", "鱼店", "市场", "市集", "购买", "出售", "售鱼", "换竿", "换饵",
      "拍卖", "竞价", "交易", "钱袋"), "coin", "商店与交易"),
    (("随机", "编号", "阶段", "数字", "选"), "misc", "选择与随机"),
    (("技能", "天赋", "属性", "进化", "升级", "进阶", "培养", "喂", "领养",
      "修炼", "突破", "炼体", "闭关", "修为"), "meditate", "养成与成长"),
    (("仙宠", "宠物", "灵宠", "出战"), "pet", "仙宠与伙伴"),
    (("宗门", "门派", "俸禄", "贡献", "药田", "开宗", "道侣", "亲密度", "拜师"),
     "heart", "宗门与道侣"),
    (("炼丹", "炼器", "采药", "采矿", "合成", "加工", "配方", "生活", "职业"),
     "furnace", "生活与制作"),
    (("诸天", "小世界", "位面", "投影", "开辟", "栽种", "演化"), "star", "诸天秘境"),
    (("魔", "神石", "堕"), "flame", "魔道与神道"),
    (("状态", "查看", "面板", "我的", "图鉴", "相册", "图库", "纪录", "战绩",
      "猫粮", "资料", "等级"), "book", "查看与记录"),
    (("换", "设置", "主题", "切换", "重开", "重启", "刷新"), "misc", "开局与设置"),
    (("退出", "结束", "放弃", "停止", "取消"), "misc", "会话控制"),
)


def _norm(text: Any) -> str:
    """归一化用于匹配: 去空白/标点, 转小写。"""
    return re.sub(r"[\s\u3000·,，。.、:：!！?？()（）\[\]【】<>《》\"'`~-]+", "",
                  str(text or "")).lower()


@dataclass
class Command:
    """一条指令。老格式 ``["装备 X", "穿戴装备"]`` 归一化后 aliases/params 为空。

    ``blocks`` 让"查看类"指令拥有**自己的专属图**(如「我的纳戒」画背包格、
    「我的装备」画装备栏): 指令命中时优先按这些版式块出图, 而不是只列一行表格。
    """

    cmd: str
    desc: str = ""
    aliases: List[str] = field(default_factory=list)
    kind: str = "action"          # view / action
    params: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    group: str = ""
    blocks: List[Dict[str, Any]] = field(default_factory=list)   # 专属版式块

    @property
    def is_view(self) -> bool:
        """只读面板类指令(如「我的纳戒」): 无参数占位符, 或显式 kind=view。"""
        if self.kind == "view":
            return True
        return not self.params and " " not in self.cmd

    @property
    def has_own_page(self) -> bool:
        """是否声明了专属版式(命中时单独出一张图)。"""
        return bool(self.blocks)

    def names(self) -> List[str]:
        return [self.cmd, *self.aliases]

    def as_pair(self) -> Tuple[str, str]:
        return self.cmd, self.desc


@dataclass
class Group:
    """一个功能分组(如「装备与道具」)。

    指令多的游戏(修仙 61 条)必须分组; 组内还能再嵌子分组(二级目录),
    子分组同样挂指令与版式块, 渲染时收在父卡片里。
    """

    id: str
    name: str
    icon: str = ""
    aliases: List[str] = field(default_factory=list)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    commands: List[Command] = field(default_factory=list)
    groups: List["Group"] = field(default_factory=list)   # 子分组(可选, 一层)

    def names(self) -> List[str]:
        return [self.name, *self.aliases]

    def all_commands(self) -> List[Command]:
        """本组及其子分组的全部指令。"""
        out = list(self.commands)
        for sub in self.groups:
            out.extend(sub.all_commands())
        return out

    def flat_groups(self) -> List["Group"]:
        """本组 + 全部子分组(寻址时用)。"""
        out = [self]
        for sub in self.groups:
            out.extend(sub.flat_groups())
        return out


@dataclass
class HelpDoc:
    """一个游戏的完整帮助文档。groups 为空 = 老的扁平单页模式。"""

    game_id: str = ""
    game_name: str = ""
    title: str = ""
    subtitle: str = ""
    text: str = ""
    banner: str = "auto"
    theme: str = "inherit"
    groups: List[Group] = field(default_factory=list)
    flat: List[Command] = field(default_factory=list)

    @property
    def grouped(self) -> bool:
        return bool(self.groups)

    @property
    def command_count(self) -> int:
        if self.groups:
            return sum(len(g.all_commands()) for g in self.groups)
        return len(self.flat)

    def all_commands(self) -> List[Command]:
        if not self.groups:
            return list(self.flat)
        out: List[Command] = []
        for g in self.groups:
            out.extend(g.all_commands())
        return out


@dataclass
class Page:
    """寻址结果: 渲染哪一页。"""

    kind: str                     # catalog / group / command / flat
    title: str = ""
    subtitle: str = ""
    group: Optional[Group] = None
    command: Optional[Command] = None
    miss: bool = False            # 主题没命中(渲染目录页 + 提示)
    suggestions: List[str] = field(default_factory=list)
    topic: str = ""               # 原始主题词(未命中时回显给用户)


def _parse_command(raw: Any, group_id: str = "") -> Optional[Command]:
    """接受两种写法: ``["指令", "说明"]`` 或 ``{"cmd": ..., "desc": ...}``。"""
    if isinstance(raw, dict):
        cmd = str(raw.get("cmd") or raw.get("name") or "").strip()
        if not cmd:
            return None
        return Command(
            cmd=cmd,
            desc=str(raw.get("desc") or raw.get("description") or ""),
            aliases=[str(a) for a in (raw.get("aliases") or []) if str(a).strip()],
            kind=str(raw.get("kind") or "action"),
            params=[str(p) for p in (raw.get("params") or [])],
            related=[str(r) for r in (raw.get("related") or [])],
            group=str(raw.get("group") or group_id),
            blocks=[b for b in (raw.get("blocks") or []) if isinstance(b, dict)],
        )
    if isinstance(raw, (list, tuple)) and len(raw) >= 2 and str(raw[0]).strip():
        return Command(cmd=str(raw[0]).strip(), desc=str(raw[1] or ""), group=group_id)
    return None


def _parse_group(raw: Dict[str, Any], index: int) -> Optional[Group]:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("title") or "").strip()
    gid = str(raw.get("id") or "").strip() or (name or f"g{index}")
    if not name:
        name = gid
    commands = [c for c in (_parse_command(r, gid) for r in (raw.get("commands") or []))
                if c is not None]
    subs = [g for g in (_parse_group(r, i) for i, r in enumerate(raw.get("groups") or []))
            if g is not None]
    icon = str(raw.get("icon") or "").strip()
    if not icon:
        icon = _icon_for(name, commands)      # 游戏没写图标名 → 插件按语义补一个
    return Group(
        id=gid,
        name=name,
        icon=icon,
        aliases=[str(a) for a in (raw.get("aliases") or []) if str(a).strip()],
        blocks=[b for b in (raw.get("blocks") or []) if isinstance(b, dict)],
        commands=commands,
        groups=subs,
    )


def _icon_for(group_name: str, commands: Sequence[Command]) -> str:
    """给一个分组挑语义图标名(按 GROUP_RULES 的关键词表)。"""
    haystack = group_name + "".join(c.cmd for c in commands[:8])
    for words, icon, _name in GROUP_RULES:
        if any(w in haystack for w in words):
            return icon
    return "star"


def _is_help_command(cmd: str) -> bool:
    """指令名本身就是"帮助/攻略"这类 → 由插件统一处理, 不进帮助列表(避免自我指涉)。"""
    name = _norm(cmd)
    return name in {_norm(w) for w in HELP_INTENT_WORDS}


def _match_rule(cmd: Command) -> Tuple[str, str]:
    """给一条指令挑分组: **先看指令名, 再看说明**(说明里的词太容易误伤)。"""
    for words, icon, name in GROUP_RULES:
        if any(w in cmd.cmd for w in words):
            return icon, name
    for words, icon, name in GROUP_RULES:
        if any(w in cmd.desc for w in words):
            return icon, name
    return "misc", "其他玩法"


def auto_groups(flat: Sequence[Command]) -> List[Group]:
    """插件侧自动分组: 按指令语义把扁平指令表聚成功能分组(游戏零改动)。

    规则见 :data:`GROUP_RULES`(关键词 → 语义图标 + 分组名), 先命中先用;
    都不命中进「其他玩法」。只做分组, 不改游戏数据。
    """
    buckets: Dict[str, List[Command]] = {}
    meta: Dict[str, Tuple[str, str]] = {}
    order: List[str] = []
    for c in flat:
        if _is_help_command(c.cmd):
            continue                      # 「帮助」由插件拦截, 不占帮助图位置
        icon, name = _match_rule(c)
        if name not in buckets:
            buckets[name] = []
            meta[name] = (icon, name)
            order.append(name)
        buckets[name].append(c)
    return [Group(id=f"auto-{i}", name=name, icon=meta[name][0],
                  commands=buckets[name]) for i, name in enumerate(order)]


def normalize_help(raw: Optional[Dict[str, Any]], game_id: str = "",
                   game_name: str = "") -> HelpDoc:
    """把 help.json 的原始 dict 归一化成 :class:`HelpDoc`(新旧格式通吃)。

    **结构由游戏自己决定, 插件照做**:
    - 游戏写了 ``groups``(或两级 groups) → 按游戏的分组渲染
    - 游戏只写 ``commands`` → 平铺单页渲染(插件不擅自改结构)
    - 游戏写 ``"auto_groups": true`` → 明确请求插件代劳分组(可选帮手, 不是默认)
    """
    raw = raw or {}
    flat = [c for c in (_parse_command(r) for r in (raw.get("commands") or []))
            if c is not None]
    groups = [g for g in (_parse_group(r, i) for i, r in enumerate(raw.get("groups") or []))
              if g is not None]
    if not groups and raw.get("auto_groups") is True and flat:
        groups = auto_groups(flat)       # 游戏显式请求"帮我分一下组"
    return HelpDoc(
        game_id=game_id,
        game_name=game_name,
        title=str(raw.get("title") or game_name or ""),
        subtitle=str(raw.get("subtitle") or ""),
        text=str(raw.get("text") or ""),
        banner=str(raw.get("banner") or "auto"),
        theme=str(raw.get("theme") or "inherit"),
        groups=groups,
        flat=flat,
    )


def _score(needle: str, candidates: Sequence[str]) -> int:
    """匹配打分: 2=完全相等, 1=包含。均以归一化形式比较。"""
    n = _norm(needle)
    if not n:
        return 0
    best = 0
    for cand in candidates:
        c = _norm(cand)
        if not c:
            continue
        if c == n:
            return 2
        if n in c or c in n:
            best = max(best, 1)
    return best


def resolve_topic(doc: HelpDoc, topic: str = "") -> Page:
    """三层寻址: 指令 > 分组 > 游戏目录; 未命中回目录页并给出近似建议。"""
    topic = (topic or "").strip()
    if not doc.groups:
        return Page(kind="flat", title=doc.title or doc.game_name,
                    subtitle=doc.subtitle, miss=bool(topic))
    if not topic:
        return Page(kind="catalog", title=doc.title or doc.game_name,
                    subtitle=doc.subtitle)

    groups: List[Group] = []
    for g in doc.groups:
        groups.extend(g.flat_groups())

    # ① 精确匹配(指令 → 别名 → 分组 → 分组别名), 精确永远优先于包含
    for g in groups:
        for c in g.commands:
            if _score(topic, c.names()) == 2:
                return Page(kind="command", title=c.cmd, group=g, command=c,
                            subtitle=g.name, miss=False)
    for g in groups:
        if _score(topic, g.names()) == 2:
            return Page(kind="group", title=g.name, group=g, subtitle=doc.title,
                        miss=False)
    # ② 包含匹配: 用户把分组名整个打出来(如「鱼缸与鱼市怎么用」)时优先给分组页
    norm_topic = _norm(topic)
    for g in groups:
        if any(_norm(n) and _norm(n) in norm_topic for n in g.names()):
            return Page(kind="group", title=g.name, group=g, subtitle=doc.title,
                        miss=False)
    for g in groups:
        for c in g.commands:
            if _score(topic, c.names()) == 1:
                return Page(kind="command", title=c.cmd, group=g, command=c,
                            subtitle=g.name, miss=False)
    for g in groups:
        if _score(topic, g.names()) == 1:
            return Page(kind="group", title=g.name, group=g, subtitle=doc.title,
                        miss=False)
    # ③ 未命中 → 目录页 + 近似建议
    n = _norm(topic)
    suggestions = [g.name for g in groups
                   if any(ch in _norm(g.name) for ch in n)][:3]
    if not suggestions:
        suggestions = [g.name for g in groups[:3]]
    return Page(kind="catalog", title=doc.title or doc.game_name, subtitle=doc.subtitle,
                miss=True, suggestions=suggestions, topic=topic)


def split_help_intent(text: str, strip_words: Sequence[str] = ()) -> Optional[str]:
    """识别帮助意图, 返回主题部分(无主题返回空串); 不是帮助意图返回 None。

    ``strip_words`` 用于去掉游戏名/触发词残留(调用方传游戏名 + keywords.json):
    例: 「修仙帮助」→ ""; 「修仙帮助 战斗」→ "战斗"(strip_words=("修仙",)) → "战斗";
    「怎么玩」→ ""; 「钓鱼攻略」→ "".
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    hit = next((w for w in HELP_INTENT_WORDS if w in raw.lower() and w in raw), None)
    if hit is None:
        hit = next((w for w in HELP_INTENT_WORDS if w in raw.lower()), None)
    if hit is None:
        return None
    rest = re.sub(re.escape(hit), " ", raw, flags=re.IGNORECASE)
    # 去掉游戏名与触发词残留(长的先去掉, 避免"修仙"先吃掉"修仙帮助"的一部分)
    for word in sorted({str(w) for w in strip_words if str(w).strip()},
                       key=len, reverse=True):
        rest = rest.replace(word, " ")
    return re.sub(r"[\s\u3000]+", " ", rest).strip()
