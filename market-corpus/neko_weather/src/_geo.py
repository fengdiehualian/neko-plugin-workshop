"""地名解析：把用户说的城市名变成经纬度。

Open-Meteo 的地理编码接口会返回同名候选，而且**中文查询会优先命中中国境内的同名小地方**
（实测「东京」会返回江苏和浙江的两个村子，而不是日本东京）。因此这里做两件事：

1. 维护一份常见别名表，把「东京 / Tokyo」这类用户真实意图直接钉到确定的坐标
2. 对接口返回的候选打分，把「在北京问北京」而不是「在美国问北京」排到前面

纯函数，无网络、无 SDK 依赖，方便单测。
"""

from __future__ import annotations

import re
from typing import Any, Optional

# 别名 -> (标准查询名, 期望国家码, 期望一级行政区关键字)
# 只收录容易歧义的常见城市，其余交给接口 + 打分。
CITY_ALIASES: dict[str, tuple[str, str, str]] = {
    # 中国
    "北京": ("Beijing", "CN", ""),
    "beijing": ("Beijing", "CN", ""),
    "上海": ("Shanghai", "CN", ""),
    "shanghai": ("Shanghai", "CN", ""),
    "广州": ("Guangzhou", "CN", ""),
    "深圳": ("Shenzhen", "CN", ""),
    "杭州": ("Hangzhou", "CN", "浙江"),
    "南京": ("Nanjing", "CN", "江苏"),
    "成都": ("Chengdu", "CN", "四川"),
    "重庆": ("Chongqing", "CN", ""),
    "武汉": ("Wuhan", "CN", "湖北"),
    "西安": ("Xi'an", "CN", "陕西"),
    "天津": ("Tianjin", "CN", ""),
    "苏州": ("Suzhou", "CN", "江苏"),
    "长沙": ("Changsha", "CN", "湖南"),
    "郑州": ("Zhengzhou", "CN", "河南"),
    "青岛": ("Qingdao", "CN", "山东"),
    "沈阳": ("Shenyang", "CN", "辽宁"),
    "大连": ("Dalian", "CN", "辽宁"),
    "厦门": ("Xiamen", "CN", "福建"),
    "福州": ("Fuzhou", "CN", "福建"),
    "昆明": ("Kunming", "CN", "云南"),
    "合肥": ("Hefei", "CN", "安徽"),
    "济南": ("Jinan", "CN", "山东"),
    "哈尔滨": ("Harbin", "CN", "黑龙江"),
    "长春": ("Changchun", "CN", "吉林"),
    "石家庄": ("Shijiazhuang", "CN", "河北"),
    "太原": ("Taiyuan", "CN", "山西"),
    "南昌": ("Nanchang", "CN", "江西"),
    "贵阳": ("Guiyang", "CN", "贵州"),
    "南宁": ("Nanning", "CN", "广西"),
    "兰州": ("Lanzhou", "CN", "甘肃"),
    "乌鲁木齐": ("Urumqi", "CN", "新疆"),
    "拉萨": ("Lhasa", "CN", "西藏"),
    "呼和浩特": ("Hohhot", "CN", "内蒙古"),
    "银川": ("Yinchuan", "CN", "宁夏"),
    "西宁": ("Xining", "CN", "青海"),
    "海口": ("Haikou", "CN", "海南"),
    "三亚": ("Sanya", "CN", "海南"),
    "香港": ("Hong Kong", "HK", ""),
    "hongkong": ("Hong Kong", "HK", ""),
    "澳门": ("Macau", "MO", ""),
    "台北": ("Taipei", "TW", ""),
    "台湾": ("Taipei", "TW", ""),
    # 其他常见
    "东京": ("Tokyo", "JP", ""),
    "tokyo": ("Tokyo", "JP", ""),
    "大阪": ("Osaka", "JP", ""),
    "osaka": ("Osaka", "JP", ""),
    "京都": ("Kyoto", "JP", ""),
    "首尔": ("Seoul", "KR", ""),
    "seoul": ("Seoul", "KR", ""),
    "釜山": ("Busan", "KR", ""),
    "新加坡": ("Singapore", "SG", ""),
    "singapore": ("Singapore", "SG", ""),
    "曼谷": ("Bangkok", "TH", ""),
    "bangkok": ("Bangkok", "TH", ""),
    "吉隆坡": ("Kuala Lumpur", "MY", ""),
    "巴黎": ("Paris", "FR", ""),
    "paris": ("Paris", "FR", ""),
    "伦敦": ("London", "GB", ""),
    "london": ("London", "GB", ""),
    "纽约": ("New York", "US", "New York"),
    "newyork": ("New York", "US", "New York"),
    "洛杉矶": ("Los Angeles", "US", "California"),
    "losangeles": ("Los Angeles", "US", "California"),
    "旧金山": ("San Francisco", "US", "California"),
    "西雅图": ("Seattle", "US", "Washington"),
    "seattle": ("Seattle", "US", "Washington"),
    "芝加哥": ("Chicago", "US", "Illinois"),
    "波士顿": ("Boston", "US", "Massachusetts"),
    "华盛顿": ("Washington", "US", "District of Columbia"),
    "多伦多": ("Toronto", "CA", "Ontario"),
    "温哥华": ("Vancouver", "CA", "British Columbia"),
    "悉尼": ("Sydney", "AU", "New South Wales"),
    "墨尔本": ("Melbourne", "AU", "Victoria"),
    "莫斯科": ("Moscow", "RU", ""),
    "柏林": ("Berlin", "DE", ""),
    "慕尼黑": ("Munich", "DE", "Bavaria"),
    "罗马": ("Rome", "IT", ""),
    "米兰": ("Milan", "IT", ""),
    "马德里": ("Madrid", "ES", ""),
    "巴塞罗那": ("Barcelona", "ES", ""),
    "阿姆斯特丹": ("Amsterdam", "NL", ""),
    "苏黎世": ("Zurich", "CH", ""),
    "迪拜": ("Dubai", "AE", ""),
    "孟买": ("Mumbai", "IN", ""),
    "新德里": ("New Delhi", "IN", "Delhi"),
    "圣保罗": ("Sao Paulo", "BR", ""),
    "开罗": ("Cairo", "EG", ""),
}

# 中文里的行政区/国家后缀，解析时先剥掉
_STRIP_SUFFIXES = (
    "市",
    "省",
    "区",
    "县",
    "特别行政区",
    "自治区",
    "自治州",
)

_RE_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_coordinates(text: str) -> Optional[tuple[float, float]]:
    """支持用户直接给 "30.29,120.16" 这样的坐标。"""
    if not text:
        return None
    match = _RE_LATLON.match(str(text))
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def clean_city_name(text: str) -> str:
    """去掉空白和常见行政后缀，便于查别名表。"""
    name = str(text or "").strip()
    name = re.sub(r"\s+", "", name)
    for suffix in _STRIP_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            name = name[: -len(suffix)]
    return name


def normalize_query(city: str) -> tuple[str, Optional[str], str]:
    """返回 (用于请求的查询串, 期望国家码, 期望行政区关键字)。"""
    raw = str(city or "").strip()
    if not raw:
        return "", None, ""

    # 支持 "东京,日本" / "Tokyo, JP" 这种显式限定
    country_hint: Optional[str] = None
    admin_hint = ""
    parts = [p.strip() for p in re.split(r"[,，]", raw) if p.strip()]
    if len(parts) >= 2:
        tail = parts[-1].upper()
        if len(tail) == 2 and tail.isalpha():
            country_hint = tail
            raw = parts[0]
        else:
            admin_hint = parts[-1]
            raw = parts[0]

    cleaned = clean_city_name(raw)
    key = cleaned.lower()

    alias = CITY_ALIASES.get(cleaned) or CITY_ALIASES.get(key)
    if alias:
        query, alias_country, alias_admin = alias
        return query, country_hint or alias_country, admin_hint or alias_admin

    return cleaned or raw, country_hint, admin_hint


def score_candidate(
    candidate: dict[str, Any],
    *,
    query: str,
    country_hint: Optional[str] = None,
    admin_hint: str = "",
) -> float:
    """给地理编码候选打分，越高越可能是用户想要的地方。"""
    score = 0.0

    name = str(candidate.get("name") or "")
    country = str(candidate.get("country_code") or "").upper()
    admin1 = str(candidate.get("admin1") or "")
    feature = str(candidate.get("feature_code") or "")
    population = candidate.get("population") or 0

    # 名字完全一致加分
    if name == query or name.lower() == query.lower():
        score += 40
    elif query and (query in name or name in query):
        score += 20

    # 国家提示
    if country_hint:
        score += 60 if country == country_hint.upper() else -80

    # 行政区提示
    if admin_hint:
        score += 35 if admin_hint in admin1 or admin1 in admin_hint else -30

    # 城市/城镇优先于机场、村庄等
    if feature.startswith("PPL"):  # PPL / PPLA / PPLC 等都是居民点
        score += 15
    if feature == "PPLC":  # 首都
        score += 10
    if feature in {"AIRP", "AIRF", "ADM1", "ADM2"}:
        score -= 25

    # 人口越多越可能是用户想的大城市
    try:
        pop = float(population)
    except (TypeError, ValueError):
        pop = 0.0
    if pop > 0:
        score += min(30.0, pop / 200000.0)  # 600 万人口封顶 +30

    return score


def pick_best(
    candidates: list[dict[str, Any]],
    *,
    query: str,
    country_hint: Optional[str] = None,
    admin_hint: str = "",
) -> Optional[dict[str, Any]]:
    """从候选里挑最合适的一个，返回规范化后的地点字典。"""
    if not candidates:
        return None

    scored = [
        (score_candidate(c, query=query, country_hint=country_hint, admin_hint=admin_hint), c)
        for c in candidates
        if isinstance(c, dict) and c.get("latitude") is not None and c.get("longitude") is not None
    ]
    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]

    return {
        "name": best.get("name") or query,
        "admin1": best.get("admin1") or "",
        "country": best.get("country") or "",
        "country_code": (best.get("country_code") or "").upper(),
        "latitude": float(best["latitude"]),
        "longitude": float(best["longitude"]),
        "timezone": best.get("timezone") or "auto",
        "population": best.get("population") or 0,
        "score": round(best_score, 1),
    }


def display_name(location: dict[str, Any]) -> str:
    """给用户看的完整地名，例如「杭州 · 浙江 · 中国」。"""
    parts = [str(location.get("name") or "")]
    admin1 = str(location.get("admin1") or "")
    country = str(location.get("country") or "")
    if admin1 and admin1 not in parts:
        parts.append(admin1)
    if country and country not in parts:
        parts.append(country)
    return " · ".join(p for p in parts if p)
