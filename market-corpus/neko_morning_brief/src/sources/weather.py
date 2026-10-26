"""``builtin.weather`` - Open-Meteo forecast, no API key, no account."""

from __future__ import annotations

from typing import Any

from ..models import BriefItem, SourceConfig
from .base import BaseSource, source_timeout
from .http import FetchError

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO_TEXT = {
    0: "晴",
    1: "大致晴",
    2: "多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "强雷阵雨伴冰雹",
}


def fetch_timeout(cfg: SourceConfig, default: float) -> float:
    return source_timeout(cfg, default)


# Process-wide geocoding cache: survives the per-run HTTP session.
_GEO_CACHE: dict[str, dict[str, Any]] = {}


def parse_geo(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    try:
        lat = float(first["latitude"])
        lon = float(first["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    label = str(first.get("name") or "").strip()
    admin = str(first.get("admin1") or "").strip()
    country = str(first.get("country") or "").strip()
    display = " ".join(part for part in (label, admin or country) if part)
    return {"latitude": lat, "longitude": lon, "name": display or label}


def build_item(
    cfg: SourceConfig,
    *,
    place: str,
    payload: Any,
) -> list[BriefItem]:
    daily = (payload or {}).get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict):
        raise FetchError("PARSE_ERROR", "missing daily block")

    def series(name: str) -> list[Any]:
        value = daily.get(name)
        return value if isinstance(value, list) else []

    highs = series("temperature_2m_max")
    lows = series("temperature_2m_min")
    codes = series("weather_code")
    pops = series("precipitation_probability_max")
    if not highs or not lows or not codes:
        raise FetchError("PARSE_ERROR", "incomplete daily series")

    try:
        high = float(highs[0])
        low = float(lows[0])
        code = int(codes[0])
    except (TypeError, ValueError) as exc:
        raise FetchError("PARSE_ERROR", "non-numeric forecast values") from exc

    description = WMO_TEXT.get(code, f"天气码 {code}")
    title = f"{place} {low:.0f}~{high:.0f}℃ {description}".strip()

    parts: list[str] = []
    if pops and pops[0] is not None:
        try:
            parts.append(f"降水概率 {float(pops[0]):.0f}%")
        except (TypeError, ValueError):
            pass
    parts.append(f"最高 {high:.0f}℃ / 最低 {low:.0f}℃")
    return [
        BriefItem(
            source_id=cfg.id,
            title=title,
            summary="，".join(parts),
            weight=cfg.weight,
            tags=tuple(cfg.tags) + ("weather",),
            snapshot=True,
        )
    ]


class WeatherSource(BaseSource):
    type = "builtin.weather"

    def __init__(self, http) -> None:
        super().__init__(http)

    async def _geocode(self, city: str, timeout: float) -> dict[str, Any] | None:
        key = city.strip().lower()
        if key in _GEO_CACHE:
            return _GEO_CACHE[key]
        payload = await self.http.get_json(
            GEO_URL,
            params={"name": city, "count": 1, "language": "zh", "format": "json"},
            timeout=timeout,
        )
        geo = parse_geo(payload)
        if geo:
            _GEO_CACHE[key] = geo
        return geo

    async def fetch(self, cfg: SourceConfig) -> list[BriefItem]:
        timeout = fetch_timeout(cfg, 6.0)
        params = dict(cfg.params or {})
        params.pop("days", None)

        lat = params.get("lat") or params.get("latitude")
        lon = params.get("lon") or params.get("longitude")
        place = str(params.get("name") or params.get("label") or "").strip()

        if lat is None or lon is None:
            city = str(params.get("city") or "").strip()
            if not city:
                raise FetchError("CONFIG_INVALID", "weather source needs city or lat/lon")
            geo = await self._geocode(city, timeout)
            if not geo:
                raise FetchError("NOT_FOUND", f"unknown city: {city}")
            lat, lon = geo["latitude"], geo["longitude"]
            place = place or city

        try:
            days = int(params.get("days") or 1)
        except (TypeError, ValueError):
            days = 1
        days = min(max(days, 1), 7)

        payload = await self.http.get_json(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": days,
            },
            timeout=timeout,
        )
        if isinstance(payload, dict) and payload.get("error"):
            raise FetchError("PARSE_ERROR", str(payload.get("reason") or "api error"))
        return build_item(cfg, place=place, payload=payload)


__all__ = ["WeatherSource", "WMO_TEXT", "build_item", "parse_geo"]
