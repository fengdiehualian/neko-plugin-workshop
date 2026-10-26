"""意图识别：一句话到底是"做完就完"还是"一直做下去"。

这是长期任务体验的关键判断。同样是挖矿：
    "挖10个铁"   → 有限任务，挖够就停，走前台执行器
    "挖铁"       → 长期任务，一直挖到主人喊停，走后台常驻
    "跟着我"     → 天生的长期任务
    "别跟了"     → 停止某个长期任务

只做"是不是长期"这一件事，具体怎么干交给 standing_jobs，
复杂多步规划仍然交给 task_brain。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

# 天生长期的行为（没有自然终点）
FOLLOW_WORDS = ("跟着", "跟上", "跟我", "跟随", "别走丢", "一起走")
# 贴身跟随：跟在身边/贴贴（距离阈值更近，5-8格触发 / 2-3格停止）
STICK_WORDS = ("身边", "贴身", "贴贴", "寸步不离", "别离开我", "别走远", "黏着我")
GUARD_WORDS = ("守着", "守在", "待在这", "别乱跑", "原地待命")
MINE_WORDS = ("挖", "采", "开采", "砍")

# 明确要求停止
STOP_WORDS = ("别跟", "不用跟", "停下", "别挖", "停止", "歇着", "不用挖",
              "别守", "结束任务", "取消任务", "停手", "别砍", "不用砍", "别砍了", "先停")

# 表示"一直/持续"的强化词
FOREVER_WORDS = ("一直", "持续", "不停", "一路", "继续")

# 常见矿物/材料别名
ORE_ALIAS = {
    "铁": "铁矿", "铜": "铜矿", "银": "银矿", "金": "金矿",
    "锡": "锡矿", "铅": "铅矿", "钨": "钨矿", "铂金": "铂金矿",
    "陨石": "陨石", "恶魔石": "恶魔石", "石头": "石块", "木": "木材",
    "树": "木材", "木材": "木材",
    "钯金": "钯金矿", "精金": "精金矿", "钛金": "钛金矿",
    "秘银": "秘银矿", "山铜": "山铜矿", "叶绿": "叶绿矿",
    "魔矿": "魔矿", "猩红矿": "猩红矿", "钴": "钴矿",
    "黑曜石": "黑曜石", "狱石": "狱石", "木头": "木材",
}

# 常用量词（"个/块/条/根/只"等）
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

AMOUNT_UNITS = ("个", "块", "颗", "组", "次", "只", "条", "根", "把")


@dataclass
class Intent:
    """一次意图解析结果。"""

    mode: str                       # longterm / finite / stop / unknown
    kind: str = ""                  # follow / mine / guard
    target: str = ""
    amount: int = 0                 # 0 = 不限量
    raw: str = ""
    reason: str = ""
    # 结构化步骤，直接喂给 TaskBrain（不能是纯字符串，否则规划不出东西）
    steps: List[Dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        return {"mode": self.mode, "kind": self.kind, "target": self.target,
                "amount": self.amount, "reason": self.reason}


def _parse_amount(text: str) -> int:
    """从话里抠出数量，没有就返回 0（= 不限量 = 长期）。"""
    # "一组" 优先于单字匹配（否则先命中"一"返回 1）
    if "一组" in text:
        return 99
    unit_pat = "|".join(AMOUNT_UNITS)
    m = re.search(rf"(\d+)\s*({unit_pat})?", text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    for cn, n in CN_NUM.items():
        if re.search(cn + rf"\s*({unit_pat})", text):
            return n
    return 0


def _parse_ore(text: str) -> str:
    """保留完整物品名；不能把铂金、钯金或模组矿名中的单字当成金矿。"""
    match = re.search(
        r"(?:开采|挖|采|砍|找一找|找找|找|寻)\s*(?:一下|一点|一些|点|些)?"
        r"\s*(?:(?:\d+|[一二两三四五六七八九十百]+)(?:个|块|颗|组|根)?)?"
        r"\s*(.+)", text)
    if not match:
        return ""
    target = re.split(r"[，。！？,!?]|(?:然后|之后|并且|给我|给主人)", match.group(1))[0]
    target = target.strip().rstrip("吧呀喵~～")
    aliases = dict(ORE_ALIAS)
    aliases.update({full: full for full in ORE_ALIAS.values()})
    if target.endswith("矿石") and target[:-1] in aliases:
        target = target[:-1]
    return aliases.get(target, target)


def parse(text: str) -> Intent:
    """把主人的一句话解析成意图。"""
    t = (text or "").strip()
    low = t.replace(" ", "")

    # 1) 先看是不是喊停（"别停下/不要停止"这类否定不算停止）
    neg_stop = any(w in low for w in ("别停下", "不要停", "别停止", "不要停止", "别停手"))
    english_stop = re.match(r"^(?:please\s+)?(?:stop|cancel|halt)\b", t.lower())
    if not neg_stop and (any(w in low for w in STOP_WORDS) or low in ("停", "别动") or english_stop):
        kind = ""
        if any(w in low.lower() for w in ("跟", "follow")):
            kind = "follow"
        elif any(w in low.lower() for w in ("砍", "chop")):
            kind = "chop"  # "别砍了/不用砍"→ 停砍树（长期砍树注册为 chop）
        elif any(w in low.lower() for w in (*MINE_WORDS, "mining", "digging")):
            kind = "mine"
        elif any(w in low.lower() for w in ("守", "guard")):
            kind = "guard"
        elif any(w in low.lower() for w in ("钓", "fish")):
            kind = "fish"
        return Intent(mode="stop", kind=kind, raw=t, reason="主人喊停")

    # 2) 跟随：天生长期（先查贴身模式，再查普通跟随）
    if any(w in low for w in STICK_WORDS):
        return Intent(mode="longterm", kind="follow", target="stick", raw=t,
                      reason="主人让我跟在身边")
    if any(w in low for w in FOLLOW_WORDS):
        return Intent(mode="longterm", kind="follow", raw=t,
                      reason="主人让我跟着")

    # 3) 守点：天生长期
    if any(w in low for w in GUARD_WORDS):
        return Intent(mode="longterm", kind="guard", raw=t,
                      reason="主人让我守着")

    # 4) 挖矿：看有没有数量决定长期还是有限
    if any(w in low for w in MINE_WORDS):
        ore = _parse_ore(low)
        amount = _parse_amount(low)
        forever = any(w in low for w in FOREVER_WORDS)
        if amount and not forever:
            # 说了数量 → 有限任务，交给前台执行器
            return Intent(mode="finite", kind="mine", target=ore,
                          amount=amount, raw=t,
                          reason=f"挖{amount}个{ore}",
                          steps=[{"action": "mine", "item": ore or "矿",
                                  "amount": amount}])
        # 没说数量 → 一直挖
        return Intent(mode="longterm", kind="mine", target=ore,
                      amount=0 if forever else amount, raw=t,
                      reason=f"一直挖{ore or '矿'}")

    # 5) 探索：有限任务（"去地下看看/探索一下/帮我找铁矿"）
    EXPLORE_WORDS = ("探索", "去地下", "到地下", "去洞", "找找", "找一找",
                     "转转", "溜达", "去看看", "探查", "探探", "寻宝")
    if any(w in low for w in EXPLORE_WORDS):
        target = "附近"
        if any(w in low for w in ("左",)):
            target = "左"
        elif any(w in low for w in ("右",)):
            target = "右"
        elif any(w in low for w in ("地下", "洞")):
            target = "地下"
        # "帮我找铁矿" → 目标性探索：找矿
        ore = _parse_ore(low)
        if ore and ("找" in low or "寻" in low):
            target = ore
        return Intent(mode="finite", kind="explore", target=target, raw=t,
                      reason=f"去{target}探索",
                      steps=[{"action": "explore", "item": target, "amount": 1}])

    # 5.5) 回家整理：明确的生活指令不能被当成闲聊或普通移动。
    # 真实存储由 BaseManager 核验回家、箱子和背包结果。
    if any(w in low for w in ("背包满", "回家放箱", "回家存箱", "回家存东西",
                              "回去放东西", "回去存", "回去整理", "存箱",
                              "整理背包", "回基地")):
        return Intent(mode="finite", kind="resupply", target="基地", amount=1, raw=t,
                      reason="回基地整理背包",
                      steps=[{"action": "resupply", "item": "基地", "amount": 1}])

    # 6) 钓鱼：有限行为（钓几条 / 去钓鱼 / 甩一竿）
    #    裸"钓"也算（"钓10条鱼"），但排除"钓竿/钓具"等名词语境
    FISH_WORDS = ("钓鱼", "钓点", "钓些", "钓几", "甩一竿", "去钓")
    is_fish = any(w in low for w in FISH_WORDS)
    if not is_fish and "钓" in low:
        # "钓N条/钓到"等以钓开头的动作；含"竿/具/鱼饵"名词不算
        if re.search(r"钓\s*\d", low) or (low.startswith("钓") and "竿" not in low and "具" not in low):
            is_fish = True
    if is_fish:
        amount = _parse_amount(low) or 3
        return Intent(mode="finite", kind="fish", target="鱼", amount=amount,
                      raw=t, reason="去钓鱼",
                      steps=[{"action": "fish", "item": "鱼", "amount": amount}])

    # 7) 合成/制作：有限行为（做把镐子/合成铁锭/锻造装备/做2个火把）
    #    "做" 单独太泛（"做什么/做饭"），必须带对象或工具词才判合成
    CRAFT_WORDS = ("合成", "制作", "锻造", "打一把", "做一把", "做把",
                   "做个", "做一个", "造把", "帮我做", "帮我造", "给我做")
    is_craft = any(w in low for w in CRAFT_WORDS)
    if not is_craft:
        # "做2个火把"：做 + 数字 + 量词 + 物
        unit_pat = "|".join(AMOUNT_UNITS)
        if re.search(rf"做\s*\d+\s*({unit_pat})", low) or re.search(rf"做[一二两三四五六七八九十]+\s*({unit_pat})", low):
            is_craft = True
    if is_craft:
        target = ""
        # 数字量词场景（"做2个火把"/"做2火把"）→ 直接剥数字量词取物名
        unit_pat = "|".join(AMOUNT_UNITS)
        num_lead = re.match(
            rf"^做\s*(\d+|[一二两三四五六七八九十]+)?\s*(?:{unit_pat})?\s*(.+)",
            low)
        if num_lead and num_lead.group(2):
            target = num_lead.group(2).strip(" 的")
        else:
            # "做把铜镐" / "合成铁锭" 等：对象在触发词后
            for w in ("合成", "制作", "锻造", "做一把", "做把", "做个",
                      "做一个", "造把", "帮我做", "帮我造", "给我做"):
                if w in low:
                    rest = low.split(w)[-1].strip(" 的")
                    # 去掉前导数量
                    m2 = re.match(
                        rf"(\d+|[一二两三四五六七八九十]+)?\s*(?:{unit_pat})?\s*(.+)?",
                        rest)
                    if m2 and m2.group(2):
                        target = m2.group(2).strip(" 的")
                    break
        amount = _parse_amount(low) or 1
        return Intent(mode="finite", kind="craft", target=target or "物品",
                      amount=amount, raw=t, reason=f"制作{target or '物品'}",
                      steps=[{"action": "craft", "item": target or "物品",
                              "amount": amount}])

    # 8) 给物：把东西给主人（给我铁/把木材给我/给我3个铁）
    #    排除感官动词（"给我看/吃/听"不是给物品）
    if (any(w in low for w in ("给我", "丢给我", "递给我", "拿给我", "分我", "交给我"))
            or re.search(r"把[^，。]{1,8}给我", low)) \
            and not re.search(r"给我(看看?|吃|听|闻|讲讲?|说|介绍|解释|用|玩|试)", low):
        # 物品在"给我"前（"铁矿给我"）或"把X给我"中间
        target = ""
        m = re.search(r"把([^，。]{1,8}?)给我", low)
        if m:
            target = m.group(1).strip(" 的")
        else:
            # "给我X" / "X给我"
            for w in ("给我", "丢给我", "递给我", "拿给我", "分我", "交给我"):
                if w in low:
                    # 若东西在词后面（"给我铁"）
                    after = low.split(w)[-1].strip(" 的")
                    before = low.split(w)[0].strip(" 的")
                    target = (after or before).strip(" 的")
                    break
        # 剥掉 target 里带的数量（"3个铁" → "铁"；"3铁" → "铁"）
        unit_pat2 = "|".join(AMOUNT_UNITS)
        tm = re.match(
            rf"(\d+|[一二两三四五六七八九十]+)?\s*(?:{unit_pat2})?\s*(.+)?",
            target)
        if tm and tm.group(2):
            target = tm.group(2).strip(" 的")
        amount = _parse_amount(low) or 1
        if target:
            return Intent(mode="finite", kind="give", target=target,
                          amount=amount, raw=t, reason=f"给主人{target}",
                          steps=[{"action": "give", "item": target,
                                  "amount": amount}])

    return Intent(mode="unknown", raw=t)


def is_longterm(text: str) -> bool:
    return parse(text).mode == "longterm"
