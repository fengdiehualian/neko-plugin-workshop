"""天气码表、数据抓取与"人话"播报整形。

设计要点：
- 纯数据与纯函数放这里，方便离线单测
- 网络调用集中在 `geocode` / `fetch_forecast` 两个函数
- 输出同时给「猫娘播报用的自然语言」和「UI 用的结构化数据」
"""

from __future__ import annotations

from typing import Any, Optional

from ._geo import (
    display_name,
    normalize_query,
    parse_coordinates,
    pick_best,
)
from ._http import HttpError, get_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO 天气现象码 -> (中文描述, 英文描述, 符号)
WMO_CODES: dict[int, tuple[str, str, str]] = {
    0: ("晴", "Clear sky", "☀️"),
    1: ("大致晴朗", "Mainly clear", "🌤️"),
    2: ("局部多云", "Partly cloudy", "⛅"),
    3: ("阴", "Overcast", "☁️"),
    45: ("有雾", "Fog", "🌫️"),
    48: ("雾凇", "Depositing rime fog", "🌫️"),
    51: ("毛毛雨（弱）", "Light drizzle", "🌦️"),
    53: ("毛毛雨", "Moderate drizzle", "🌦️"),
    55: ("毛毛雨（强）", "Dense drizzle", "🌦️"),
    56: ("冻毛毛雨（弱）", "Light freezing drizzle", "🌧️"),
    57: ("冻毛毛雨（强）", "Dense freezing drizzle", "🌧️"),
    61: ("小雨", "Slight rain", "🌦️"),
    63: ("中雨", "Moderate rain", "🌧️"),
    65: ("大雨", "Heavy rain", "🌧️"),
    66: ("冻雨（弱）", "Light freezing rain", "🌧️"),
    67: ("冻雨（强）", "Heavy freezing rain", "🌧️"),
    71: ("小雪", "Slight snow", "🌨️"),
    73: ("中雪", "Moderate snow", "🌨️"),
    75: ("大雪", "Heavy snow", "❄️"),
    77: ("雪粒", "Snow grains", "🌨️"),
    80: ("阵雨（弱）", "Slight rain showers", "🌦️"),
    81: ("阵雨（中）", "Moderate rain showers", "🌧️"),
    82: ("阵雨（强）", "Violent rain showers", "⛈️"),
    85: ("阵雪（弱）", "Slight snow showers", "🌨️"),
    86: ("阵雪（强）", "Heavy snow showers", "❄️"),
    95: ("雷阵雨", "Thunderstorm", "⛈️"),
    96: ("雷阵雨伴小冰雹", "Thunderstorm with slight hail", "⛈️"),
    99: ("雷阵雨伴大冰雹", "Thunderstorm with heavy hail", "⛈️"),
}

_RAIN_CODES = {
    51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99,
}
_SNOW_CODES = {71, 73, 75, 77, 85, 86}
_FOG_CODES = {45, 48}
_STORM_CODES = {95, 96, 99}


def describe(code: Any) -> tuple[str, str, str]:
    """返回 (中文, 英文, 符号)。"""
    try:
        key = int(code)
    except (TypeError, ValueError):
        return ("未知", "Unknown", "❓")
    return WMO_CODES.get(key, ("未知", "Unknown", "❓"))


def is_rainy(code: Any) -> bool:
    try:
        return int(code) in _RAIN_CODES
    except (TypeError, ValueError):
        return False


def is_snowy(code: Any) -> bool:
    try:
        return int(code) in _SNOW_CODES
    except (TypeError, ValueError):
        return False


def is_stormy(code: Any) -> bool:
    try:
        return int(code) in _STORM_CODES
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------- 抓取


async def geocode(city: str, *, language: str = "zh", timeout: float = 15.0) -> dict[str, Any]:
    """城市名 -> 坐标。支持 "东京"、"Tokyo, JP"、"30.29,120.16"。"""
    raw = str(city or "").strip()
    if not raw:
        raise HttpError("没有指定城市")

    coords = parse_coordinates(raw)
    if coords:
        lat, lon = coords
        return {
            "name": f"{lat:.3f}, {lon:.3f}",
            "admin1": "",
            "country": "",
            "country_code": "",
            "latitude": lat,
            "longitude": lon,
            "timezone": "auto",
            "population": 0,
            "score": 100.0,
        }

    query, country_hint, admin_hint = normalize_query(raw)
    data = await get_json(
        GEOCODE_URL,
        params={"name": query, "count": 10, "language": language, "format": "json"},
        timeout=timeout,
    )
    best = pick_best(
        list(data.get("results") or []),
        query=query,
        country_hint=country_hint,
        admin_hint=admin_hint,
    )
    if best is None:
        raise HttpError(f"找不到这个地点：{raw}")
    return best


async def fetch_forecast(
    latitude: float,
    longitude: float,
    *,
    timezone: str = "auto",
    forecast_days: int = 3,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """拉取当前天气 + 逐日预报 + 未来若干小时。"""
    days = max(1, min(7, int(forecast_days or 3)))
    data = await get_json(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                    "sunrise",
                    "sunset",
                ]
            ),
            "timezone": timezone or "auto",
            "forecast_days": days,
        },
        timeout=timeout,
    )
    return data


# ------------------------------------------------------------------- 整形


def _safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


# --------------------------------------------------------------- 猫娘语气

# 可选的语气后缀，用配置 weather.cat_suffix 覆盖
DEFAULT_CAT_SUFFIX = "喵～"


def cat_suffix(cute: bool = True, custom: str = "") -> str:
    if not cute:
        return ""
    return (custom or "").strip() or DEFAULT_CAT_SUFFIX


def _meow(cute: bool, custom: str = "") -> str:
    return cat_suffix(cute, custom)


def _cute_summary(code: Any, temp: Optional[float], *, cute: bool) -> str:
    """把天气现象说成猫娘的口吻。"""
    zh, _en, icon = describe(code)
    t = "?" if temp is None else f"{temp:.0f}"
    meow = _meow(cute)
    if not cute:
        return f"{zh}{icon} {t}°C"
    if is_stormy(code):
        return f"外面在打雷哦{icon} 人家有点怕…{t}°C{meow}"
    if is_snowy(code):
        return f"下雪啦{icon} 好想堆雪人{t}°C{meow}"
    if is_rainy(code):
        return f"在下雨呢{icon} 别忘了带伞哦{t}°C{meow}"
    if code is not None and int(code) in (0, 1):
        return f"是个大晴天{icon} 心情也跟着亮闪闪的{t}°C{meow}"
    if code is not None and int(code) == 2:
        return f"有点云{icon} 不过还不错{t}°C{meow}"
    if code is not None and int(code) in _FOG_CODES:
        return f"有雾{icon} 出门要小心呀{t}°C{meow}"
    if code is not None and int(code) == 3:
        return f"阴阴的{icon} 人家想窝在家里{t}°C{meow}"
    return f"{zh}{icon} {t}°C{meow}"


def clothing_advice(temp: Optional[float], *, feels: Optional[float] = None, cute: bool = False) -> str:
    """按体感温度给穿衣建议。"""
    value = feels if feels is not None else temp
    meow = _meow(cute)

    if value is None:
        return "温度数据缺失，建议出门前看一眼窗外～"
    if not cute:
        if value >= 32:
            return "很热，短袖短裤最舒服，注意防晒补水"
        if value >= 27:
            return "偏热，短袖或薄衬衫就够了"
        if value >= 22:
            return "温暖舒适，长袖 T 恤或薄外套都合适"
        if value >= 17:
            return "有点凉，建议加一件薄外套"
        if value >= 12:
            return "偏冷，外套或卫衣别忘带"
        if value >= 5:
            return "冷，需要厚外套，怕冷的记得戴围巾"
        if value >= 0:
            return "很冷，羽绒服或大衣，注意手脚保暖"
        return "严寒，尽量少在户外待，出门务必厚羽绒 + 手套帽子"

    if value >= 32:
        return f"热热的！短袖短裤最舒服啦，记得防晒多喝水{meow}"
    if value >= 27:
        return f"暖暖的～短袖或薄衬衫就很合适{meow}"
    if value >= 22:
        return f"温度刚刚好呢，长袖 T 恤或薄外套都可以{meow}"
    if value >= 17:
        return f"有点凉凉的，加一件薄外套吧{meow}"
    if value >= 12:
        return f"偏冷哦，外套或卫衣别忘了{meow}"
    if value >= 5:
        return f"好冷好冷…要穿厚外套，怕冷的话围巾也戴上{meow}"
    if value >= 0:
        return f"非常冷！羽绒服或大衣不能少，手脚也要保暖哦{meow}"
    return f"呜…冻僵了{meow} 尽量别在户外待着，厚羽绒加手套帽子都要"


def umbrella_advice(
    *,
    code: Any = None,
    rain_probability: Optional[float] = None,
    precipitation: Optional[float] = None,
    cute: bool = False,
) -> str:
    """带伞建议。"""
    meow = _meow(cute)
    if is_snowy(code):
        return f"在下雪呢，路面滑要注意防滑{meow} 带把伞挡挡雪也好"
    if is_stormy(code):
        return f"有雷雨！一定要带伞{meow} 也别在空旷的地方逗留哦"
    if is_rainy(code) or (precipitation or 0) > 0.2:
        return f"正在下雨哦，出门记得带伞{meow}"
    if rain_probability is not None and rain_probability >= 70:
        return f"今天大概率要下雨，伞一定要带上{meow}"
    if rain_probability is not None and rain_probability >= 40:
        return f"有可能下雨呢，带把伞比较安心{meow}"
    if rain_probability is not None and rain_probability >= 20:
        return f"降水概率不高，不过带把折叠伞也不亏{meow}"
    return f"不用担心下雨，伞可以放家里{meow}"


def activity_advice(
    *, code: Any = None, wind: Optional[float] = None, temp: Optional[float] = None, cute: bool = False
) -> str:
    """出行/运动建议。"""
    meow = _meow(cute)
    if is_stormy(code):
        return f"雷雨天气，还是待在室内比较安全{meow}"
    if is_snowy(code):
        return f"路面可能湿滑，走路慢一点点{meow}"
    if wind is not None and wind >= 40:
        return f"风好大！骑车的话要小心{meow}"
    if temp is not None and temp >= 35:
        return f"太热了，避开中午出门比较好{meow}"
    if temp is not None and temp <= 0:
        return f"外面很冷，户外别待太久哦{meow}"
    if is_rainy(code):
        return f"有雨，穿双防水的鞋子会舒服些{meow}"
    return f"适合出门玩耍{meow}"


def summarize_current(current: dict[str, Any]) -> dict[str, Any]:
    code = current.get("weather_code")
    zh, en, icon = describe(code)
    temp = _safe_float(current.get("temperature_2m"))
    feels = _safe_float(current.get("apparent_temperature"))
    humidity = _safe_float(current.get("relative_humidity_2m"))
    wind = _safe_float(current.get("wind_speed_10m"))
    precip = _safe_float(current.get("precipitation"))
    return {
        "time": current.get("time"),
        "weather_code": code,
        "weather": zh,
        "weather_en": en,
        "icon": icon,
        "temperature": temp,
        "apparent_temperature": feels,
        "humidity": humidity,
        "wind_speed": wind,
        "precipitation": precip,
    }


def summarize_daily(daily: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    dates = list(daily.get("time") or [])
    codes = list(daily.get("weather_code") or [])
    highs = list(daily.get("temperature_2m_max") or [])
    lows = list(daily.get("temperature_2m_min") or [])
    rains = list(daily.get("precipitation_probability_max") or [])
    sunrises = list(daily.get("sunrise") or [])
    sunsets = list(daily.get("sunset") or [])

    out: list[dict[str, Any]] = []
    for index, date in enumerate(dates[:limit]):
        code = codes[index] if index < len(codes) else None
        zh, _en, icon = describe(code)
        out.append(
            {
                "date": date,
                "weekday": weekday_cn(date),
                "weather_code": code,
                "weather": zh,
                "icon": icon,
                "temp_max": _safe_float(highs[index]) if index < len(highs) else None,
                "temp_min": _safe_float(lows[index]) if index < len(lows) else None,
                "rain_probability": (
                    _safe_float(rains[index]) if index < len(rains) else None
                ),
                "sunrise": (sunrises[index].split("T")[-1] if index < len(sunrises) else None),
                "sunset": (sunsets[index].split("T")[-1] if index < len(sunsets) else None),
            }
        )
    return out


def weekday_cn(date_str: str) -> str:
    """'2026-10-05' -> '周一'；今天/明天会显示为「今天」「明天」。"""
    import datetime as _dt

    try:
        date = _dt.date.fromisoformat(str(date_str)[:10])
    except (TypeError, ValueError):
        return ""
    today = _dt.date.today()
    delta = (date - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return names[date.weekday()]


def upcoming_rain(daily: list[dict[str, Any]], *, threshold: float = 40.0) -> list[dict[str, Any]]:
    """挑出降水概率较高的日子。"""
    hits = []
    for day in daily:
        prob = day.get("rain_probability")
        if prob is not None and prob >= threshold:
            hits.append(day)
    return hits


def build_report(
    location: dict[str, Any],
    forecast: dict[str, Any],
    *,
    days: int = 3,
    hourly_hours: int = 6,
    cute: bool = True,
    cat_suffix_text: str = "",
) -> dict[str, Any]:
    """把原始响应整理成 {结构化数据, 自然语言播报}。"""
    current = summarize_current(dict(forecast.get("current") or {}))
    daily = summarize_daily(dict(forecast.get("daily") or {}), limit=days)
    hourly = summarize_hourly(dict(forecast.get("hourly") or {}), limit=hourly_hours)
    today = daily[0] if daily else {}
    rain_prob = today.get("rain_probability")

    clothing = clothing_advice(
        current.get("temperature"), feels=current.get("apparent_temperature"), cute=cute
    )
    umbrella = umbrella_advice(
        code=current.get("weather_code"),
        rain_probability=rain_prob,
        precipitation=current.get("precipitation"),
        cute=cute,
    )
    activity = activity_advice(
        code=current.get("weather_code"),
        wind=current.get("wind_speed"),
        temp=current.get("temperature"),
        cute=cute,
    )

    place = display_name(location)
    speech = _build_speech(
        place=place,
        current=current,
        daily=daily,
        clothing=clothing,
        umbrella=umbrella,
        activity=activity,
        cute=cute,
        cat_suffix_text=cat_suffix_text,
    )

    return {
        "location": {
            "name": location.get("name"),
            "admin1": location.get("admin1"),
            "country": location.get("country"),
            "country_code": location.get("country_code"),
            "display": place,
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "timezone": forecast.get("timezone") or location.get("timezone"),
        },
        "current": current,
        "daily": daily,
        "hourly": hourly,
        "advice": {
            "clothing": clothing,
            "umbrella": umbrella,
            "activity": activity,
        },
        "rainy_days": [day.get("weekday") or day.get("date") for day in upcoming_rain(daily)],
        "speech": speech,
    }


def summarize_hourly(hourly: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    """取「从现在开始」的未来若干小时。

    Open-Meteo 的 hourly 是按自然日给的，深夜查询时今天剩下的小时不足，
    此时直接顺延到明天的数据，而不是只返回一两条。
    """
    times = list(hourly.get("time") or [])
    temps = list(hourly.get("temperature_2m") or [])
    rains = list(hourly.get("precipitation_probability") or [])
    codes = list(hourly.get("weather_code") or [])

    import datetime as _dt

    now = _dt.datetime.now()
    parsed: list[_dt.datetime | None] = []
    for stamp in times:
        try:
            parsed.append(_dt.datetime.fromisoformat(str(stamp)))
        except (TypeError, ValueError):
            parsed.append(None)

    start = 0
    for index, when in enumerate(parsed):
        if when is not None and when >= now:
            start = index
            break
    else:
        start = 0

    out: list[dict[str, Any]] = []
    for index in range(start, len(times)):
        if len(out) >= limit:
            break
        code = codes[index] if index < len(codes) else None
        _zh, _en, icon = describe(code)
        out.append(
            {
                "time": str(times[index]).split("T")[-1],
                "date": str(times[index]).split("T")[0],
                "temperature": _safe_float(temps[index]) if index < len(temps) else None,
                "rain_probability": _safe_float(rains[index]) if index < len(rains) else None,
                "icon": icon,
            }
        )
    return out


def _fmt_temp(value: Optional[float]) -> str:
    return "?" if value is None else f"{value:.0f}"


def _build_speech(
    *,
    place: str,
    current: dict[str, Any],
    daily: list[dict[str, Any]],
    clothing: str,
    umbrella: str,
    activity: str,
    cute: bool = True,
    cat_suffix_text: str = "",
) -> str:
    """给猫娘播报用的一段自然语言。"""
    meow = cat_suffix(cute, cat_suffix_text)

    if cute:
        # 猫娘口吻：先撒娇式报天气，再逐条叮嘱
        lines = [
            f"主人主人～人家帮你看好啦{meow} "
            f"{place}现在{current['weather']}{current['icon']}，"
            f"气温 {_fmt_temp(current.get('temperature'))}°C"
            + (
                f"（体感 {_fmt_temp(current.get('apparent_temperature'))}°C）"
                if current.get("apparent_temperature") is not None
                else ""
            )
            + "。",
        ]

        if current.get("humidity") is not None:
            lines.append(
                f"湿度 {_fmt_temp(current.get('humidity'))}%，"
                f"风速 {current.get('wind_speed') or '?'} km/h。"
            )

        if len(daily) > 1:
            parts = []
            for day in daily[1:]:
                label = day.get("weekday") or day.get("date")
                span = f"{_fmt_temp(day.get('temp_min'))}~{_fmt_temp(day.get('temp_max'))}°C"
                rain = day.get("rain_probability")
                rain_text = f"，降水概率 {_fmt_temp(rain)}%" if rain is not None else ""
                parts.append(f"{label}{day.get('weather')}{day['icon']}{span}{rain_text}")
            lines.append("接下来几天嘛：" + "；".join(parts) + "。")

        lines.append(f"要穿什么的话～{clothing}。")
        lines.append(f"伞的事情交给人家记着：{umbrella}。")
        lines.append(f"出门的话{activity}。")
        lines.append(f"记得照顾好自己哦{meow}")
        return "".join(lines)

    # 中性播报（cute = false 时使用）
    lines = [
        f"{place}现在{current['weather']}{current['icon']}，"
        f"气温 {_fmt_temp(current.get('temperature'))}°C"
        + (
            f"（体感 {_fmt_temp(current.get('apparent_temperature'))}°C）"
            if current.get("apparent_temperature") is not None
            else ""
        )
        + "。",
    ]

    if current.get("humidity") is not None:
        lines.append(f"湿度 {_fmt_temp(current.get('humidity'))}%，风速 {current.get('wind_speed') or '?'} km/h。")

    if len(daily) > 1:
        parts = []
        for day in daily[1:]:
            label = day.get("weekday") or day.get("date")
            span = f"{_fmt_temp(day.get('temp_min'))}~{_fmt_temp(day.get('temp_max'))}°C"
            rain = day.get("rain_probability")
            rain_text = f"，降水概率 {_fmt_temp(rain)}%" if rain is not None else ""
            parts.append(f"{label}{day.get('weather')}{day['icon']}{span}{rain_text}")
        lines.append("接下来：" + "；".join(parts) + "。")

    lines.append(f"穿衣：{clothing}。")
    lines.append(f"带伞：{umbrella}。")
    lines.append(f"出行：{activity}。")

    return "".join(lines)


def build_hourly_speech(
    place: str,
    hourly: list[dict[str, Any]],
    *,
    cute: bool = True,
    cat_suffix_text: str = "",
) -> str:
    """逐小时预报的猫娘口吻播报。"""
    if not hourly:
        return "人家拿不到逐小时的数据呢…"

    meow = cat_suffix(cute, cat_suffix_text)
    parts = []
    for item in hourly:
        temp = item.get("temperature")
        temp_text = f"{temp:.0f}°C" if temp is not None else "?"
        rain = item.get("rain_probability")
        rain_text = f"降雨{rain:.0f}%" if rain is not None else ""
        parts.append(f"{item['time']} {item['icon']}{temp_text}{rain_text}")

    body = "，".join(parts)
    if cute:
        return f"人家数了数接下来的天气{meow} {place}：{body}～"
    return f"{place}接下来：{body}"
