"""记账猫娘 · 金额与条目解析（纯函数，无 SDK / 无网络依赖）。

把「记账，面条10元」这种口语拆成结构化数据。

设计原则：**能解析的尽量解析，解析不了的返回明确原因** ——
绝不猜金额，猜错比不识别更糟。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ------------------------------------------------------------------ 中文数字

_CN_DIGITS = {
    "零": 0, "〇": 0,
    "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2, "俩": 2,
    "三": 3, "叁": 3, "四": 4, "肆": 4, "五": 5, "伍": 5,
    "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}


def cn_to_number(text: str) -> Optional[float]:
    """把中文数字转成数值。支持「十五」「二十」「一百二十三」「两块五」。

    只处理万以下的常见口语写法；不认识的返回 None。
    """
    if not text:
        return None

    # 纯阿拉伯数字
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)

    # 「两块五」「十块五」这类省略写法：X块Y -> X + Y/10
    m = re.fullmatch(r"([零〇一壹二贰两俩三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟]+)[块元]([零〇一壹二贰两俩三叁四肆五伍六陆七柒八捌九玖])", text)
    if m:
        whole = _cn_int(m.group(1))
        frac = _CN_DIGITS.get(m.group(2))
        if whole is not None and frac is not None:
            return whole + frac / 10.0
        return None

    return _cn_int(text)


def _cn_int(text: str) -> Optional[float]:
    """解析纯中文整数（不含小数）。"""
    if not text:
        return None
    if not any(ch in _CN_DIGITS or ch in _CN_UNITS for ch in text):
        return None

    total = 0
    section = 0
    current = 0
    saw_any = False

    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
            saw_any = True
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            saw_any = True
            if current == 0:
                # 「十五」里的「十」= 1*10
                current = 1
            section += current * unit
            current = 0
        else:
            return None

    if not saw_any:
        return None
    total = section + current
    return float(total)


# ------------------------------------------------------------------ 金额提取

# 单位：元 / 块 / 圆 / 块钱 / RMB / ￥ / ¥
_UNIT = r"(?:块钱|块|元钱|元|圆|RMB|rmb|￥|¥)"
_NUM = r"(?:\d+(?:\.\d+)?|[零〇一壹二贰两俩三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟]+)"

# 「10元」「10块5」「￥10」「10 元」
_PATTERNS = [
    re.compile(rf"[￥¥]\s*({_NUM})"),
    re.compile(rf"({_NUM})\s*{_UNIT}\s*([零〇一壹二贰两俩三叁四肆五伍六陆七柒八捌九玖])?"),
    re.compile(rf"({_NUM})\s*{_UNIT}"),
]

# 省略「元」的写法：「买了个键盘399」「花了50」「充值100」
# 只在明确的消费动词之后才认，避免把版本号、数量误当成金额。
_BARE_NUM_VERBS = (
    "花了", "花掉", "花了我", "买了", "买了个", "支付", "付了", "充值", "充了",
    "消费", "开销", "花了钱", "用掉", "用了",
)
_BARE_NUM = re.compile(rf"(?:{'|'.join(_BARE_NUM_VERBS)})\s*[^\d]{{0,12}}?(\d+(?:\.\d+)?)\s*$")
_BARE_NUM_CN = re.compile(rf"(?:{'|'.join(_BARE_NUM_VERBS)})\s*[^\d]{{0,12}}?([零〇一壹二贰两俩三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟]+)\s*$")

# 出现的税费/折扣等词，命中就提示用户确认（不做自动计算）
_AMBIGUOUS = ("大概", "左右", "差不多", "打折", "优惠", "满减", "凑单", "好像")


@dataclass
class ParsedExpense:
    """解析结果。"""

    amount: float
    item: str
    raw: str
    ambiguous: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.amount > 0 and bool(self.item)

    def to_dict(self) -> dict:
        return {
            "amount": round(self.amount, 2),
            "item": self.item,
            "ambiguous": self.ambiguous,
        }


@dataclass
class AmountHit:
    """一次金额命中：值 + 在原文里的位置（用于精确剔除）。"""

    value: float
    start: int
    end: int

    @property
    def text(self) -> str:
        return ""

    def slice_of(self, source: str) -> str:
        return source[self.start:self.end]


def find_amount(text: str) -> Optional[AmountHit]:
    """定位文本里的金额，返回值和位置。找不到返回 None。

    先试带单位的写法，再试「买了个键盘399」这种省略「元」的写法。
    位置信息很重要 —— 剥条目名时要把金额**精确**挖掉，
    否则会出现「键盘399」这种残留。
    """
    if not text:
        return None

    for pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if not groups:
            continue

        # 处理「10块5」这种：主数 + 角
        if len(groups) >= 2 and groups[1] and len(groups[1]) == 1:
            whole = cn_to_number(groups[0])
            frac = _CN_DIGITS.get(groups[1])
            if whole is not None and frac is not None:
                return AmountHit(whole + frac / 10.0, m.start(), m.end())

        value = cn_to_number(groups[0])
        if value is not None:
            return AmountHit(value, m.start(), m.end())

    # 省略单位的写法（必须出现在消费动词之后）
    for pattern, is_cn in ((_BARE_NUM, False), (_BARE_NUM_CN, True)):
        m = pattern.search(text)
        if not m:
            continue
        # 只把数字部分算作金额，动词和中间的字不属于金额
        num_span = m.span(1)
        value = cn_to_number(m.group(1)) if is_cn else float(m.group(1))
        if value is not None:
            return AmountHit(value, num_span[0], num_span[1])

    return None


def extract_amount(text: str) -> Optional[float]:
    """只要金额值。找不到返回 None。"""
    hit = find_amount(text)
    return hit.value if hit else None


# 记账动词：出现这些词才认为用户在记账
_LEDGER_VERBS = (
    "记账", "记一笔", "记一下", "记录", "花了我", "花掉", "花了", "买了", "买了个",
    "支付", "付了", "支出", "开销", "入手了", "充了", "交费", "缴费", "消费",
)

# 明显不是支出的动词（避免把收入当支出）
_NEGATIVE_HINTS = ("收入", "工资", "赚了", "退款", "报销", "发了", "红包收到", "中奖")


def looks_like_expense(text: str) -> bool:
    """粗略判断这句话是不是在记一笔支出。"""
    if not text:
        return False
    if any(h in text for h in _NEGATIVE_HINTS):
        return False
    return any(v in text for v in _LEDGER_VERBS) or bool(re.search(rf"{_NUM}\s*{_UNIT}", text))


# 金额前面/后面的连接词，剥掉之后剩下的才是条目名
_STRIP_PREFIX = (
    "记账", "记一笔", "记一下", "记录一下", "记录", "帮我记", "帮我", "给我",
    "我", "今天", "刚刚", "刚才", "又", "了", "，", ",", "。", "、", "：", ":",
    "花了我", "花掉", "花了", "买了个", "买了", "支付了", "支付", "付了", "支出",
    "开销", "入手了", "入手", "充了", "充值", "交费", "缴费", "消费", "用了",
)

# 条目名里不该出现的尾巴
_STRIP_SUFFIX = (
    "元", "块钱", "块", "元钱", "圆", "RMB", "rmb", "￥", "¥",
    "，", ",", "。", "、", "：", ":", "了", "的", "钱",
)


def extract_item(text: str, hit: Optional["AmountHit"] = None) -> str:
    """从文本里抠出「买了什么」。

    先**按位置**把金额片段挖掉（比正则整体替换可靠，能处理省略单位的写法），
    再剥掉前后的连接词。
    """
    if not text:
        return ""

    body = text
    # 优先按精确位置剔除
    if hit is not None:
        body = body[: hit.start] + " " + body[hit.end:]
    else:
        # 没有位置信息时退化成正则清理
        body = re.sub(rf"[￥¥]?\s*{_NUM}\s*{_UNIT}\s*[零〇一壹二贰两俩三叁四肆五伍六陆七柒八捌九玖]?", " ", body)
        body = re.sub(r"[￥¥]\s*\d+(?:\.\d+)?", " ", body)

    # 剩下的数字也清掉（金额常伴随找零、打折等附加数字）
    body = re.sub(r"\d+(?:\.\d+)?", " ", body)

    # 剥前缀（反复剥，直到稳定）
    changed = True
    while changed:
        changed = False
        body = body.strip()
        for p in sorted(_STRIP_PREFIX, key=len, reverse=True):
            if body.startswith(p):
                body = body[len(p):]
                changed = True
                break

    body = body.strip().lstrip("，,。、：: ").strip()

    # 剥后缀
    changed = True
    while changed:
        changed = False
        body = body.rstrip()
        for s in sorted(_STRIP_SUFFIX, key=len, reverse=True):
            if body.endswith(s):
                body = body[: -len(s)]
                changed = True
                break

    body = body.strip().strip("，,。、：: ").strip()
    # 中间被空格切开的情况（「键盘 399」挖掉后不会留下尾巴，但保险起见压一下）
    body = re.sub(r"\s+", "", body) if len(body) <= 12 else body
    return body


def parse_expense(text: str) -> Optional[ParsedExpense]:
    """解析一句记账。解析不出金额则返回 None。"""
    if not text or not text.strip():
        return None

    raw = text.strip()
    hit = find_amount(raw)
    if hit is None or hit.value <= 0:
        return None

    item = extract_item(raw, hit)
    if not item:
        # 没有条目名就退化成「未命名」，仍算解析成功
        item = "未命名"

    ambiguous = next((w for w in _AMBIGUOUS if w in raw), None)
    return ParsedExpense(amount=hit.value, item=item, raw=raw, ambiguous=ambiguous)


# ------------------------------------------------------------------ 分类

# 顺序有意义：先匹配「猫咪/AI」这类特殊类，再匹配普通类
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("猫咪", (
        "猫粮", "猫砂", "猫条", "猫罐头", "猫抓板", "猫窝", "猫玩具", "逗猫棒",
        "猫爬架", "猫薄荷", "化毛膏", "猫疫苗", "宠物医院", "猫", "喵",
    )),
    ("AI", (
        "ai", "AI", "Ai", "人工智能", "大模型", "模型", "算力", "token", "Token",
        "api", "API", "接口费", "订阅", "gpt", "GPT", "chatgpt", "claude", "Claude",
        "deepseek", "DeepSeek", "会员", "开会员", "包月",
    )),
    ("餐饮", (
        "饭", "午饭", "晚饭", "早饭", "早餐", "午餐", "晚餐", "外卖", "面", "米线",
        "粉", "烧烤", "火锅", "奶茶", "咖啡", "蛋糕", "面包", "零食", "水果", "菜",
        "小吃", "汉堡", "披萨", "寿司", "餐", "食堂", "饮料", "可乐",
    )),
    ("交通", (
        "打车", "出租车", "地铁", "公交", "高铁", "火车", "机票", "飞机", "油费",
        "加油", "停车", "过路费", "共享单车", "骑车", "车费", "动车",
    )),
    ("购物", (
        "衣服", "裤子", "鞋", "帽子", "包", "化妆品", "护肤", "洗面奶", "日用品",
        "纸巾", "洗衣液", "牙膏", "耳机", "键盘", "鼠标", "电脑", "手机", "充电",
        "数据线", "显示器",
    )),
    ("居住", (
        "房租", "水电", "水费", "电费", "燃气", "物业", "宽带", "网费", "话费",
        "暖气", "家具",
    )),
    ("娱乐", (
        "游戏", "steam", "Steam", "氪金", "皮肤", "手办", "电影", "电影票", "演唱会",
        "门票", "ktv", "KTV", "桌游", "盲盒", "乐高",
    )),
    ("医疗", ("药", "医院", "挂号", "体检", "看病", "牙医", "疫苗", "口罩")),
    ("学习", ("书", "书本", "教材", "网课", "课程", "学费", "文具", "笔记本", "考试")),
]


def categorize(item: str, raw: str = "") -> str:
    """给条目分类。匹配不到返回「其他」。"""
    haystack = f"{item} {raw}"
    lowered = haystack.lower()
    for name, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw.lower() in lowered:
                return name
    return "其他"
