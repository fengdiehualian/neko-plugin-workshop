"""阴阳怪气翻译腔彩蛋。

设计原则（照搬那份建议里最对的一条）：**天气数据由代码算好，彩蛋只负责套壳**。
模板里只有占位符，所有数值都从已经算好的 report 里取，彩蛋本身**不解析也不修改**
任何天气数据 —— 这样永远不会出现"17°C 被写成 27°C"。

分层：
    snark_category()  先判断这次天气属于哪一类（雨/雪/高温/…）
    build_vars()      把 report 摊成占位符字典
    decide_snark()    概率 + 冷却判定（随机数由外部注入，方便单测）
    render_snark()    抽模板 + 填充 + 兜底校验
    maybe_snark()     上面几步的编排入口

模板池按 `style` 分层，目前有：
    "zh"  中文阴阳怪气翻译腔（默认）
    "en"  English passive-aggressive butler

用户可以整体选一套，也可以通过 `snark_custom_templates` 追加自己的模板。
"""

from __future__ import annotations

import random
from typing import Any, Callable, Optional

# --------------------------------------------------------------- 占位符工具


def fmt_num(value: Any, template: str = "{:.0f}") -> str:
    """把数值格式化成字符串；缺失就返回 '?'，绝不抛异常。"""
    if value is None:
        return "?"
    try:
        return template.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def fill(template: str, variables: dict[str, str]) -> str:
    """只做占位符替换。缺哪个占位符就留原样，不会炸。"""
    out = template
    for key, value in variables.items():
        out = out.replace("{" + key + "}", value)
    return out


# ------------------------------------------------------------- 天气分类


CATEGORY_ORDER = (
    "storm",
    "snow",
    "rain",
    "hot",
    "cold",
    "wind",
    "fog",
    "sunny",
    "cloudy",
    "general",
)


def snark_category(current: dict[str, Any], daily: list[dict[str, Any]]) -> str:
    """给这次天气定一个类别，决定抽哪个模板池。

    优先级：雷暴 > 雪 > 雨 > 高温 > 严寒 > 大风 > 雾 > 晴 > 多云 > 通用。
    """
    code = current.get("weather_code")
    temp = current.get("temperature")
    feels = current.get("apparent_temperature")
    wind = current.get("wind_speed")

    value = feels if feels is not None else temp

    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None

    if code_int is not None and code_int in {95, 96, 99}:
        return "storm"
    if code_int is not None and code_int in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code_int is not None and code_int in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if code_int is not None and code_int in {45, 48}:
        return "fog"
    if value is not None and value >= 33:
        return "hot"
    if value is not None and value <= 2:
        return "cold"
    if wind is not None and wind >= 30:
        return "wind"
    if code_int in {0, 1}:
        return "sunny"
    if code_int in {2, 3}:
        return "cloudy"

    # 天气码缺失时，用当日降水概率再兜一层
    if daily:
        rain = daily[0].get("rain_probability")
        if rain is not None and rain >= 60:
            return "rain"
    return "general"


# --------------------------------------------------------------- 模板池

# 注意：模板里**只允许出现占位符**，不要在里面写死任何天气数值。
TEMPLATE_POOLS: dict[str, dict[str, list[str]]] = {
    "zh": {
        "general": [
            "喵？等等……哦，我亲爱的老伙计，我的猫娘语音包好像被隔壁老约翰的威士忌腌入味了。"
            "让我看看：{location}，现在{current_condition}，气温{temp}°C，体感{feels_like}°C。"
            "湿度{humidity}%，风速{wind} km/h。未来几天：{forecast}。"
            "穿衣服？我发誓，{clothing}。伞？{umbrella}。出门？{outing}。"
            "看在上帝的份上，别用你那该死的靴子踢我，我只是个无辜的天气插件。喵。",
            "哦，天哪，我亲爱的老伙计。让我用我这副刚从罐头里捞出来的嗓子给你念念："
            "{location}，{current_condition}，{temp}°C，体感{feels_like}°C。"
            "湿度{humidity}%，风{wind} km/h。{forecast}。"
            "{clothing}。{umbrella}。{outing}。"
            "我向上帝发誓，这鬼天气和我的语法一样不讲道理。喵。",
        ],
        "sunny": [
            "让我看看明天有哪个倒霉蛋要被雨淋湿？哦，该死，居然是晴天，"
            "你可真是走了狗屎运，我的老伙计。{location}现在{current_condition}，"
            "气温{temp}°C，体感{feels_like}°C。未来：{forecast}。{clothing}。{umbrella}。{outing}。"
            "我向上帝发誓，太阳会像隔壁苏珊婶婶的烤箱一样热情，别把自己晒成猫干。喵。",
            "哦，阳光，我亲爱的老朋友，你今天可真是毫不吝啬。{location}现在{current_condition}，"
            "{temp}°C，体感{feels_like}°C。湿度{humidity}%，风{wind} km/h。{forecast}。"
            "{clothing}。{umbrella}。{outing}。好好享受吧，毕竟这种好运不会跟着你一辈子。喵。",
        ],
        "cloudy": [
            "多云，像你老板的脸色。{location}现在{current_condition}，气温{temp}°C，体感{feels_like}°C。"
            "未来：{forecast}。{clothing}。{umbrella}。{outing}。"
            "别高兴太早，老伙计，云可不会替你付账单。喵。",
            "哦，一片云，或者两片，谁知道呢。{location}现在{current_condition}，"
            "{temp}°C，体感{feels_like}°C。湿度{humidity}%，风{wind} km/h。{forecast}。"
            "{clothing}。{umbrella}。{outing}。天空和我一样，都在假装自己很有想法。喵。",
        ],
        "rain": [
            "哦，天哪，我亲爱的老伙计，我敢打赌你上辈子一定踢翻过上帝的水桶。"
            "{location}现在{current_condition}，气温{temp}°C，体感{feels_like}°C。"
            "湿度{humidity}%，风{wind} km/h。未来：{forecast}。{clothing}。{umbrella}。{outing}。"
            "带上伞，除非你想像落汤鸡一样在街上表演悲剧。喵。",
            "下雨了，真是令人惊喜，就好像这件事从来没有发生过一样。"
            "{location}，{current_condition}，{temp}°C，体感{feels_like}°C。{forecast}。"
            "{clothing}。{umbrella}。{outing}。我发誓，你的鞋子会在今天完成它的成人礼。喵。",
        ],
        "storm": [
            "哦，老天爷在发脾气，而你就是那个正好站在窗边的人。"
            "{location}现在{current_condition}，气温{temp}°C，体感{feels_like}°C。"
            "风{wind} km/h。未来：{forecast}。{clothing}。{umbrella}。{outing}。"
            "我亲爱的老伙计，打雷的时候别站在树下，除非你想提前见到上帝。喵。",
        ],
        "snow": [
            "下雪了，老伙计。美得像圣诞贺卡，滑得像你的人生。"
            "{location}现在{current_condition}，气温{temp}°C，体感{feels_like}°C。未来：{forecast}。"
            "{clothing}。{umbrella}。{outing}。走路小心，别用屁股和地面打招呼。喵。",
        ],
        "hot": [
            "我的老天，{temp}°C？体感{feels_like}°C？我发誓地狱的锅炉工都想请假。"
            "{location}现在{current_condition}。湿度{humidity}%，风速{wind} km/h。未来：{forecast}。"
            "{clothing}。{umbrella}。{outing}。别出门，除非你想变成五分熟。喵。",
            "哦，我亲爱的老伙计，今天的太阳决定把你当成一份需要翻面的煎蛋。"
            "{location}，{current_condition}，{temp}°C，体感{feels_like}°C。{forecast}。"
            "{clothing}。{umbrella}。{outing}。多喝水，别指望影子能救你。喵。",
        ],
        "cold": [
            "看在上帝的份上，{temp}°C，体感{feels_like}°C。我亲爱的老伙计，"
            "你的屁股准备冻成冰坨子了吗？{location}现在{current_condition}。"
            "湿度{humidity}%，风{wind} km/h。未来：{forecast}。{clothing}。{umbrella}。{outing}。"
            "穿厚点，别逞强，你不是冬天里的超级英雄。喵。",
            "哦，冷得如此有诚意，我都要感动了。{location}现在{current_condition}，"
            "{temp}°C，体感{feels_like}°C。{forecast}。{clothing}。{umbrella}。{outing}。"
            "我发誓，你的膝盖会比天气预报更早知道明天的温度。喵。",
        ],
        "wind": [
            "哦，风{wind} km/h。我向上帝发誓，这风能把你的假发吹到隔壁郡去。"
            "{location}现在{current_condition}，气温{temp}°C，体感{feels_like}°C。"
            "未来：{forecast}。{clothing}。{umbrella}。{outing}。抓紧你的帽子，老伙计。喵。",
        ],
        "fog": [
            "哦，天哪，空气糟得像老约翰的烟斗。{location}现在{current_condition}，"
            "气温{temp}°C，体感{feels_like}°C。湿度{humidity}%，风速{wind} km/h。"
            "未来：{forecast}。{clothing}。{umbrella}。{outing}。戴上口罩，别用肺当过滤器。喵。",
            "雾来了，把整座城市裹得像一份没人想打开的礼物。{location}现在{current_condition}，"
            "{temp}°C，体感{feels_like}°C。{forecast}。{clothing}。{umbrella}。{outing}。"
            "开车慢点，老伙计，我看不见路，你也别指望能看见。喵。",
        ],
    },
    "en": {
        "general": [
            "Oh, my dear old friend. Let me consult the heavens for you. {location}: {current_condition}, "
            "{temp}°C, feels like {feels_like}°C. Humidity {humidity}%, wind {wind} km/h. "
            "Ahead: {forecast}. As for clothing — I do swear, {clothing}. Umbrella? {umbrella}. "
            "Going out? {outing}. Do try not to blame the messenger. Meow.",
        ],
        "sunny": [
            "Well, well. Somebody up there likes you today. {location}: {current_condition}, "
            "{temp}°C, feels like {feels_like}°C. {forecast}. {clothing}. {umbrella}. {outing}. "
            "Enjoy it, my friend — good fortune has a terrible memory. Meow.",
        ],
        "rain": [
            "Ah, rain. Because of course it is. {location}: {current_condition}, {temp}°C, "
            "feels like {feels_like}°C. Ahead: {forecast}. {clothing}. {umbrella}. {outing}. "
            "Take the umbrella, unless you enjoy being a cautionary tale. Meow.",
        ],
        "hot": [
            "Good heavens, {temp}°C, and it feels like {feels_like}°C. {location}: {current_condition}. "
            "Humidity {humidity}%, wind {wind} km/h. {forecast}. {clothing}. {umbrella}. {outing}. "
            "Stay inside, unless you fancy being medium-rare. Meow.",
        ],
        "cold": [
            "For pity's sake, {temp}°C, feels like {feels_like}°C. {location}: {current_condition}. "
            "{forecast}. {clothing}. {umbrella}. {outing}. Dress warmly. You are not the hero of winter. Meow.",
        ],
        "snow": [
            "Snow, my friend. Pretty as a postcard, slippery as your life choices. "
            "{location}: {current_condition}, {temp}°C, feels like {feels_like}°C. {forecast}. "
            "{clothing}. {umbrella}. {outing}. Mind your footing. Meow.",
        ],
        "storm": [
            "The sky is throwing a tantrum, and you are the one standing by the window. "
            "{location}: {current_condition}, {temp}°C, feels like {feels_like}°C, wind {wind} km/h. "
            "{forecast}. {clothing}. {umbrella}. {outing}. Do stay away from tall trees. Meow.",
        ],
        "wind": [
            "Ah, {wind} km/h of pure spite. {location}: {current_condition}, {temp}°C, "
            "feels like {feels_like}°C. {forecast}. {clothing}. {umbrella}. {outing}. Hold your hat. Meow.",
        ],
        "fog": [
            "The air is thick as old tobacco smoke. {location}: {current_condition}, {temp}°C, "
            "feels like {feels_like}°C. {forecast}. {clothing}. {umbrella}. {outing}. Drive slowly. Meow.",
        ],
        "cloudy": [
            "Cloudy — rather like your employer's expression. {location}: {current_condition}, "
            "{temp}°C, feels like {feels_like}°C. {forecast}. {clothing}. {umbrella}. {outing}. "
            "Don't celebrate yet. Clouds don't pay your bills. Meow.",
        ],
    },
}

# 结尾随机拼一句（决定论测试里可以关掉）
TEMPLATE_TAGS: dict[str, list[str]] = {
    "zh": [
        "愿上帝保佑你，别感冒。",
        "别用靴子踢我，我只是个插件。",
        "我敢打赌，隔壁苏珊婶婶会同意我的。",
        "阿门，喵。",
        "喵……我恢复正常了，主人。",
    ],
    "en": [
        "Bless your heart. Don't catch a cold.",
        "Kindly do not kick me. I am merely a plugin.",
        "Amen. Meow.",
        "Meow... I am quite myself again, master.",
    ],
}

# 每个模板**必须**包含的占位符：不给这些，彩蛋就失去了天气信息的意义
REQUIRED_PLACEHOLDERS = (
    "location",
    "current_condition",
    "temp",
    "forecast",
    "clothing",
    "umbrella",
    "outing",
)

# 可选占位符：模板可以省略（短模板里不放湿度和风速是正常的）
OPTIONAL_PLACEHOLDERS = (
    "feels_like",
    "humidity",
    "wind",
)

# 变量字典里应当存在的全部键（供测试校验）
ALL_PLACEHOLDERS = REQUIRED_PLACEHOLDERS + OPTIONAL_PLACEHOLDERS


def validate_template(text: str) -> list[str]:
    """检查模板是否齐全；返回缺失的**必需**占位符列表（空表示合格）。"""
    return [name for name in REQUIRED_PLACEHOLDERS if "{" + name + "}" not in text]


# ------------------------------------------------------------- 变量构造


def build_vars(report: dict[str, Any], *, forecast_days: int = 2) -> dict[str, str]:
    """把已算好的 report 摊成占位符字典 —— 纯搬运，不做任何重新计算。"""
    current = report.get("current") or {}
    daily = report.get("daily") or []
    advice = report.get("advice") or {}

    forecast_parts: list[str] = []
    for day in daily[1 : 1 + max(0, forecast_days)]:
        label = day.get("weekday") or day.get("date") or ""
        condition = day.get("weather") or ""
        icon = day.get("icon") or ""
        low = fmt_num(day.get("temp_min"))
        high = fmt_num(day.get("temp_max"))
        rain = day.get("rain_probability")
        rain_text = f"，降水概率{fmt_num(rain)}%" if rain is not None else ""
        forecast_parts.append(f"{label}{condition}{icon}{low}~{high}°C{rain_text}")

    return {
        "location": str((report.get("location") or {}).get("display") or "此地"),
        "current_condition": f"{current.get('weather') or ''}{current.get('icon') or ''}".strip(),
        "temp": fmt_num(current.get("temperature")),
        "feels_like": fmt_num(current.get("apparent_temperature")),
        "humidity": fmt_num(current.get("humidity")),
        "wind": fmt_num(current.get("wind_speed"), "{:.1f}") if current.get("wind_speed") is not None else "?",
        "forecast": "；".join(forecast_parts) if forecast_parts else "未来几天的数据人家没拿到",
        "clothing": str(advice.get("clothing") or ""),
        "umbrella": str(advice.get("umbrella") or ""),
        "outing": str(advice.get("activity") or ""),
    }


# ------------------------------------------------------------- 概率与冷却


NORMALIZE_PROBABILITY: dict[str, Callable[[Any], Optional[float]]] = {
    "0": lambda _v: 0.0,
    "0.05": lambda _v: 0.05,
    "0.1": lambda _v: 0.10,
    "0.15": lambda _v: 0.15,
    "0.2": lambda _v: 0.20,
    "0.3": lambda _v: 0.30,
    "0.5": lambda _v: 0.50,
    "0.8": lambda _v: 0.80,
    "1": lambda _v: 1.0,
}


def normalize_probability(value: Any, default: float = 0.20) -> float:
    """把配置里的概率折成 0.0~1.0。支持 0.2 / "20%" / 20 这几种写法。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        num = float(value)
        # 20 这种写法按百分比理解，0.2 按比例理解
        return min(1.0, max(0.0, num / 100.0 if num > 1 else num))
    text = str(value).strip()
    if not text:
        return default
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        num = float(text)
    except ValueError:
        return default
    if percent:
        return min(1.0, max(0.0, num / 100.0))
    return min(1.0, max(0.0, num / 100.0 if num > 1 else num))


def decide_snark(
    probability: float,
    *,
    cooldown: int = 2,
    normal_since_last: int = 999,
    rng: Callable[[], float] = random.random,
) -> bool:
    """判定这次要不要走彩蛋。随机数由外部注入，方便测试。"""
    if probability <= 0:
        return False
    if probability >= 1:
        # 即使 100% 也尊重冷却，避免连着刷
        return normal_since_last >= max(0, cooldown)
    if normal_since_last < max(0, cooldown):
        return False
    return rng() < probability


# ------------------------------------------------------------- 抽模板与渲染


def _pool_for(style: str, custom: Optional[dict[str, list[str]]] = None) -> dict[str, list[str]]:
    base = TEMPLATE_POOLS.get(style) or TEMPLATE_POOLS["zh"]
    pool = {key: list(value) for key, value in base.items()}
    if isinstance(custom, dict):
        for key, items in custom.items():
            if isinstance(items, list) and items:
                # 自定义模板整体替换该类别，便于用户完全掌控
                pool[str(key)] = [str(x) for x in items if str(x).strip()]
    return pool


def pick_templates(
    category: str,
    *,
    style: str = "zh",
    custom: Optional[dict[str, list[str]]] = None,
    rng: Any = random,
) -> list[str]:
    """取某个类别的模板候选；缺类别时逐级回退到 general。"""
    pool = _pool_for(style, custom)
    if category in pool and pool[category]:
        return pool[category]
    # storm/hot/cold 等没有专用模板时先试同族，再退通用
    for fallback in ("rain", "cloudy", "general"):
        if fallback in pool and pool[fallback]:
            return pool[fallback]
    return ["{location} {current_condition} {temp}°C"]

def render_snark(
    report: dict[str, Any],
    *,
    style: str = "zh",
    custom: Optional[dict[str, list[str]]] = None,
    with_tags: bool = True,
    rng: Any = random,
    forecast_days: int = 2,
) -> Optional[str]:
    """渲染一段彩蛋。返回 None 表示没有可用模板。"""
    current = report.get("current") or {}
    daily = report.get("daily") or []
    category = snark_category(current, daily)
    candidates = pick_templates(category, style=style, custom=custom, rng=rng)
    if not candidates:
        return None

    variables = build_vars(report, forecast_days=forecast_days)
    template = rng.choice(candidates)
    text = fill(template, variables)

    # 兜底：模板残缺时补一句正常信息，但绝不改数值
    missing = validate_template(template)
    if missing:
        if "temp" in missing:
            text = f"{text}（气温{ variables['temp'] }°C）"

    if with_tags and TEMPLATE_TAGS.get(style):
        text = f"{text}{rng.choice(TEMPLATE_TAGS[style])}"

    return text


def maybe_snark(
    report: dict[str, Any],
    *,
    enabled: bool = True,
    probability: float = 0.20,
    cooldown: int = 2,
    normal_since_last: int = 999,
    style: str = "zh",
    custom: Optional[dict[str, list[str]]] = None,
    with_tags: bool = True,
    rng: Any = random,
    random_value: Optional[float] = None,
) -> Optional[str]:
    """编排入口：要不要彩蛋 → 渲染。

    返回彩蛋文本；返回 None 表示这次走正常播报。
    """
    if not enabled:
        return None
    if random_value is not None:
        roll = random_value
    else:
        roll = rng.random()
    if not decide_snark(
        probability, cooldown=cooldown, normal_since_last=normal_since_last, rng=lambda: roll
    ):
        return None
    return render_snark(
        report, style=style, custom=custom, with_tags=with_tags, rng=rng
    )
