"""天气猫娘（N.E.K.O 插件）

让猫娘能查天气、给穿衣/带伞建议，并可以在每天早上主动播报今日天气。

数据源：Open-Meteo（完全免费、**不需要 API Key**）
- 地理编码：geocoding-api.open-meteo.com
- 预报：api.open-meteo.com/v1/forecast

暴露的能力
- LLM 工具 `get_weather` / `set_home_city`：猫娘在对话中按需调用
- 运行时入口 `get_weather` / `get_hourly` / `push_daily_brief`：插件管理器可手动触发
- 定时任务：按配置的本地时间每天推送一次天气播报

代码约定
- 运行时入口一律 `async def`
- 纯逻辑与网络在 `_weather.py` / `_geo.py` / `_http.py`，本文件只做编排
- 模块导入期无副作用
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    quick_action,
    timer_interval,
)

from ._http import HttpError
from ._snark import maybe_snark, normalize_probability, snark_category
from ._weather import (
    build_hourly_speech,
    build_report,
    fetch_forecast,
    geocode,
)

_MAX_DAYS = 7
_TICK_SECONDS = 60


@neko_plugin
class NekoWeatherPlugin(NekoPluginBase):
    """天气猫娘。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: dict[str, Any] = {}
        self._last_push_date: Optional[str] = None
        # 运行时记住这次会话里用户口头指定的城市
        self._runtime_city: Optional[str] = None
        # 距上次彩蛋已经过了多少次正常播报（用于冷却）
        self._normal_since_snark: int = 999

    # ------------------------------------------------------------------ 配置

    async def _config(self) -> dict[str, Any]:
        if self._cfg:
            return self._cfg
        cfg: dict[str, Any] = {}
        try:
            dumped = await self.config.dump(timeout=5.0)
            if isinstance(dumped, dict):
                cfg = dumped
        except Exception as exc:  # noqa: BLE001 - 配置读不到就退回默认值
            self.logger.warning("读取配置失败，使用默认值: %s", exc)
        self._cfg = cfg
        return cfg

    async def _settings(self) -> dict[str, Any]:
        cfg = await self._config()
        section = cfg.get("weather")
        section = section if isinstance(section, dict) else {}

        try:
            timeout = float(section.get("timeout_seconds", 15))
        except (TypeError, ValueError):
            timeout = 15.0
        try:
            default_days = int(section.get("default_days", 3))
        except (TypeError, ValueError):
            default_days = 3

        return {
            "home_city": str(section.get("home_city") or "").strip() or self._runtime_city or "",
            "language": str(section.get("geocode_language") or "zh"),
            "timeout": timeout,
            "default_days": max(1, min(_MAX_DAYS, default_days)),
            "temperature_unit": str(section.get("temperature_unit") or "celsius"),
            "daily_push": bool(section.get("daily_push", True)),
            "push_time": str(section.get("push_time") or "07:30"),
            "push_window_minutes": int(section.get("push_window_minutes", 180) or 180),
            "push_city": str(section.get("push_city") or "").strip(),
            "include_advice": bool(section.get("include_advice", True)),
            # 猫娘语气
            "cute": bool(section.get("cute", True)),
            "cat_suffix": str(section.get("cat_suffix") or ""),
            # 语音：push_message 的 ai_behavior
            "voice": bool(section.get("voice", True)),
            # 阴阳怪气翻译腔彩蛋
            "snark_enabled": bool(section.get("snark_enabled", True)),
            "snark_probability": normalize_probability(section.get("snark_probability"), 0.20),
            "snark_cooldown": max(0, int(section.get("snark_cooldown", 2) or 0)),
            "snark_style": str(section.get("snark_style") or "zh"),
            "snark_tags": bool(section.get("snark_tags", True)),
            "snark_custom": section.get("snark_custom_templates")
            if isinstance(section.get("snark_custom_templates"), dict)
            else None,
        }

    # --------------------------------------------------------------- 生命周期

    @lifecycle(id="startup")
    async def startup(self, **_):
        settings = await self._settings()
        self.logger.info(
            "天气猫娘已就绪：home_city=%r，每日推送=%s@%s",
            settings["home_city"] or "(未设置)",
            settings["daily_push"],
            settings["push_time"],
        )
        return Ok(
            {
                "status": "running",
                "home_city": settings["home_city"],
                "daily_push": settings["daily_push"],
                "push_time": settings["push_time"],
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.logger.info("天气猫娘已停止")
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config=None, new_config=None, mode=None, **_):
        self._cfg = {}
        settings = await self._settings()
        self.logger.info("天气猫娘配置已刷新：%s", settings)
        return Ok({"status": "reloaded"})

    # ------------------------------------------------------------- 内部实现

    def _resolve_city(self, city: Optional[str], settings: dict[str, Any]) -> str:
        candidate = str(city or "").strip()
        if candidate:
            return candidate
        if settings["home_city"]:
            return settings["home_city"]
        if settings["push_city"]:
            return settings["push_city"]
        raise SdkError(
            "我还不知道你在哪个城市～可以说「把主页城市设成杭州」，或者直接问「杭州天气怎么样」"
        )

    async def _report(
        self,
        city: Optional[str],
        *,
        days: Optional[int] = None,
        hourly_hours: int = 6,
    ) -> dict[str, Any]:
        settings = await self._settings()
        resolved_city = self._resolve_city(city, settings)
        want_days = max(1, min(_MAX_DAYS, int(days or settings["default_days"])))
        want_hours = max(1, min(24, int(hourly_hours or 6)))
        # 逐小时数据按自然日返回：深夜查询时今天剩余小时不足，
        # 多取一天才能真正凑够 want_hours 个小时。
        if days is not None and want_days < 2:
            want_days = 2

        try:
            location = await geocode(
                resolved_city, language=settings["language"], timeout=settings["timeout"]
            )
            forecast = await fetch_forecast(
                location["latitude"],
                location["longitude"],
                timezone=location.get("timezone") or "auto",
                forecast_days=want_days,
                timeout=settings["timeout"],
            )
        except HttpError as exc:
            raise SdkError(f"查天气失败了：{exc}") from exc

        report = build_report(
            location,
            forecast,
            days=want_days,
            hourly_hours=want_hours,
            cute=settings["cute"],
            cat_suffix_text=settings["cat_suffix"],
        )
        report["requested_city"] = resolved_city
        return report

    async def _speak(self, report: dict[str, Any], *, rng=None) -> str:
        """决定这次说正常播报还是阴阳怪气彩蛋，并维护冷却计数。

        彩蛋只换措辞，所有天气数值都来自已经算好的 report，不会被改写。
        """
        settings = await self._settings()
        if not settings["snark_enabled"]:
            self._normal_since_snark += 1
            return report["speech"]

        kwargs: dict[str, Any] = {
            "enabled": True,
            "probability": settings["snark_probability"],
            "cooldown": settings["snark_cooldown"],
            "normal_since_last": self._normal_since_snark,
            "style": settings["snark_style"],
            "custom": settings["snark_custom"],
            "with_tags": settings["snark_tags"],
        }
        if rng is not None:
            kwargs["rng"] = rng

        snark = maybe_snark(report, **kwargs)
        if snark:
            self._normal_since_snark = 0
            self.logger.info(
                "触发阴阳怪气彩蛋（类别=%s，概率=%.0f%%）",
                snark_category(report.get("current") or {}, report.get("daily") or []),
                settings["snark_probability"] * 100,
            )
            return snark

        self._normal_since_snark += 1
        return report["speech"]

    async def _push_speech(
        self, text: str, *, priority: int = 5, force_ai_behavior: Optional[str] = None
    ) -> bool:
        """把播报推到聊天框；失败不影响主流程。

        ai_behavior 决定猫娘会不会**说出来**：
        - "respond" —— 模型读取并回应，会走角色语音（TTS）播报
        - "read"    —— 只进上下文，猫娘不一定开口
        - "blind"   —— 只显示，模型完全不知道

        所以「主动播报 + 让猫娘念出来」用 respond；
        「只是把数据摆到聊天里」用 read。
        """
        settings = await self._settings()
        behavior = force_ai_behavior or ("respond" if settings["voice"] else "read")
        try:
            result = self.push_message(
                source="neko_weather",
                visibility=["chat"],
                ai_behavior=behavior,
                priority=priority,
                parts=[{"type": "text", "text": text}],
            )
            if isinstance(result, dict) and not result.get("submitted", True):
                self.logger.warning("天气播报推送被拒绝: %s", result.get("reason"))
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - 推送失败不应打断定时任务
            self.logger.warning("天气播报推送失败: %s", exc)
            return False

    @staticmethod
    def _in_daily_window(settings: dict[str, Any]) -> tuple[bool, str]:
        """判断当前是否处在每日播报的时间窗内。"""
        now = _dt.datetime.now()
        hour, minute = _parse_hhmm(settings["push_time"])
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < scheduled:
            return False, "还没到时间"
        window = max(5, settings["push_window_minutes"])
        if (now - scheduled).total_seconds() > window * 60:
            return False, "已错过今天的播报窗口"
        return True, ""

    # -------------------------------------------------------------- 运行时入口

    @plugin_entry(
        id="get_weather",
        name="查天气",
        description=(
            "查询某个城市当前的天气，并给出穿衣、带伞和出行建议。"
            "可以指定城市名，也可以不填（使用已保存的常用城市）。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，例如 '杭州'、'东京'、'Tokyo, JP'，也支持 '30.29,120.16' 坐标",
                },
                "days": {
                    "type": "integer",
                    "description": "要看的预报天数，1-7，默认 3",
                },
                "push_to_chat": {
                    "type": "boolean",
                    "description": "是否把播报推到聊天框，默认 true",
                },
            },
        },
        timeout=45.0,
        llm_result_fields=["speech", "location_text", "current", "advice"],
    )
    @quick_action(icon="🌤️", priority=10)
    async def get_weather(
        self,
        city: str = "",
        days: Optional[int] = None,
        push_to_chat: bool = True,
        **_,
    ):
        try:
            report = await self._report(city, days=days)
        except SdkError as exc:
            return Err(exc)

        current = report["current"]
        speech = await self._speak(report)
        self.logger.info(
            "天气查询成功：%s %s %s°C",
            report["location"]["display"],
            current["weather"],
            current.get("temperature"),
        )

        if push_to_chat:
            await self._push_speech(speech)

        return Ok(
            {
                "location_text": report["location"]["display"],
                "requested_city": report["requested_city"],
                "current": current,
                "daily": report["daily"],
                "advice": report["advice"],
                "rainy_days": report["rainy_days"],
                "speech": speech,
            }
        )

    @plugin_entry(
        id="get_hourly",
        name="逐小时天气",
        description="查看接下来几个小时的温度与降水概率，适合判断「等会儿会不会下雨」。",
        input_schema={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，不填则用常用城市"},
                "hours": {"type": "integer", "description": "要看几小时，默认 6"},
            },
        },
        timeout=45.0,
        llm_result_fields=["summary", "hourly"],
    )
    async def get_hourly(self, city: str = "", hours: int = 6, **_):
        settings = await self._settings()
        limit = max(1, min(24, int(hours or 6)))
        try:
            report = await self._report(city, days=1, hourly_hours=limit)
        except SdkError as exc:
            return Err(exc)

        hourly = report["hourly"]
        summary = build_hourly_speech(
            report["location"]["display"],
            hourly,
            cute=settings["cute"],
            cat_suffix_text=settings["cat_suffix"],
        )
        return Ok({"summary": summary, "hourly": hourly})

    @plugin_entry(
        id="set_home_city",
        name="设置常用城市",
        description="把某个城市设为默认城市，之后查天气和每日播报都用它。",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
        timeout=30.0,
        llm_result_fields=["message", "location_text"],
    )
    async def set_home_city(self, city: str, **_):
        raw = str(city or "").strip()
        if not raw:
            return Err(SdkError("要设置的城市名不能为空"))

        settings = await self._settings()
        try:
            location = await geocode(
                raw, language=settings["language"], timeout=settings["timeout"]
            )
        except HttpError as exc:
            return Err(SdkError(f"我没找到这个城市：{raw}（{exc}）"))

        from ._geo import display_name

        display = display_name(location)
        # 写回运行配置，重启后仍然生效
        try:
            await self.ctx.update_own_config({"weather": {"home_city": raw}})
        except Exception as exc:  # noqa: BLE001 - 写配置失败时至少记住本次会话
            self.logger.warning("写入配置失败，仅本次会话生效: %s", exc)
        self._runtime_city = raw
        self._cfg = {}

        return Ok(
            {
                "location_text": display,
                "message": f"好，以后我就默认看{display}的天气啦～",
            }
        )

    @plugin_entry(
        id="push_daily_brief",
        name="立即推送天气播报",
        description="立刻生成并推送一次今日天气播报（可以用来测试每日播报效果）。",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名，不填则用常用城市"}},
        },
        timeout=45.0,
        llm_result_fields=["speech", "submitted"],
    )
    async def push_daily_brief(self, city: str = "", **_):
        try:
            report = await self._report(city)
        except SdkError as exc:
            return Err(exc)
        speech = await self._speak(report)
        submitted = await self._push_speech(speech, priority=6)
        return Ok(
            {
                "speech": speech,
                "location_text": report["location"]["display"],
                "submitted": submitted,
            }
        )

    # ---------------------------------------------------------------- 定时任务

    @timer_interval(
        id="daily_weather_push",
        # 注意：seconds 必须是字面量 —— neko-plugin check 会用 AST 静态读取这个值，
        # 写模块常量会让校验报 "must declare seconds > 0"。
        seconds=60,
        name="每日天气播报",
        auto_start=True,
    )
    async def daily_tick(self, **_):
        """每分钟检查一次是否到了当天该播报的时间。"""
        settings = await self._settings()
        if not settings["daily_push"]:
            return Ok({"skipped": "daily_push 已关闭"})

        target_city = settings["push_city"] or settings["home_city"]
        if not target_city:
            return Ok({"skipped": "还没设置常用城市"})

        today = _dt.datetime.now().date().isoformat()
        if self._last_push_date == today:
            return Ok({"skipped": "今天已经播报过了"})

        in_window, reason = self._in_daily_window(settings)
        if not in_window:
            # 过了窗口就记一天，避免开机瞬间补推过期信息
            if "错过" in reason:
                self._last_push_date = today
            return Ok({"skipped": reason})

        try:
            report = await self._report(target_city)
        except SdkError as exc:
            self.logger.warning("每日天气播报生成失败: %s", exc)
            return Ok({"error": str(exc)})

        sent = await self._push_speech(await self._speak(report), priority=6)
        if sent:
            self._last_push_date = today
            self.logger.info("已推送每日天气播报：%s", report["location"]["display"])
        return Ok({"submitted": sent, "location": report["location"]["display"]})

    # --------------------------------------------------------------- LLM 工具

    @llm_tool(
        name="get_weather",
        description=(
            "查询城市天气。用户问「今天天气怎么样」「外面冷不冷」「明天要带伞吗」"
            "「杭州天气」「现在几度」「今天热不热」「要不要穿外套」这类问题时调用。\n"
            "调用后完整播报会由插件直接显示在聊天里，用户已经看到了。\n"
            "你的返回值只是一句确认，不要复述天气内容（否则会和上面那条重复）。"
            "用一句可爱的短话回应即可，比如「人家把天气写在上面啦～」。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，如 '杭州'、'东京'。用户没说城市时留空，会使用已保存的常用城市。",
                },
                "days": {
                    "type": "integer",
                    "description": "需要看几天预报，1-7，默认 3",
                },
                "include_hourly": {
                    "type": "boolean",
                    "description": (
                        "用户关心「等会儿会不会下雨」「下午要不要带伞」时设为 true，"
                        "会额外附上未来几小时的逐小时预报。"
                    ),
                },
            },
        },
    )
    async def llm_get_weather(
        self, *, city: str = "", days: Optional[int] = None, include_hourly: bool = False
    ):
        settings = await self._settings()
        try:
            report = await self._report(city, days=days)
        except SdkError as exc:
            return {"error": str(exc)}

        speech = await self._speak(report)

        # 逐小时：用户明确问了才附上
        if include_hourly:
            speech = (
                f"{speech}\n"
                + build_hourly_speech(
                    report["location"]["display"],
                    report["hourly"],
                    cute=settings["cute"],
                    cat_suffix_text=settings["cat_suffix"],
                )
            )

        # ★ 关键：把完整播报**直接推进聊天流**。
        # 实测 N.E.K.O 的对话模型会固定地"用自己的话复述"工具返回的内容，
        # 无论怎么强化措辞、加粗强调、还是只返回纯文本，湿度/风速/未来几天预报
        # 都会在复述时被丢掉。走 push_message 则完全绕开模型改写，
        # 主进程日志里能看到 [MESSAGE FORWARD] 原样转发。
        # 这里用 read：让模型知道"已经播报过了"，但不要它再重复一遍，
        # 避免聊天里出现两条一模一样的长文本。
        pushed = await self._push_speech(speech, priority=6, force_ai_behavior="read")
        self.logger.info(
            "天气播报（LLM 工具）%s %s，speech %d 字，推送=%s",
            report["location"]["display"],
            report["current"]["weather"],
            len(speech),
            pushed,
        )

        if not pushed:
            # 推送失败就退回让模型转述，总比什么都没有强
            return speech
        return (
            f"完整播报已经显示在聊天里了（{report['location']['display']}）。"
            "用一句可爱的短话回应主人即可，不要重复天气内容。"
        )

    @llm_tool(
        name="set_home_city",
        description="记住用户所在城市。用户说「我住在杭州」「以后看上海的天气」时调用。",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    )
    async def llm_set_home_city(self, *, city: str):
        raw = str(city or "").strip()
        if not raw:
            return {"error": "城市名不能为空"}
        try:
            await self.ctx.update_own_config({"weather": {"home_city": raw}})
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("写入配置失败，仅本次会话生效: %s", exc)
        self._runtime_city = raw
        self._cfg = {}
        return {"ok": True, "home_city": raw}

    @llm_tool(
        name="get_hourly_weather",
        description=(
            "查询未来几小时的逐小时天气与降水概率。用户问「等会儿会不会下雨」"
            "「下午要带伞吗」「今晚冷不冷」「过两小时天气怎么样」时调用。\n"
            "返回的 speech 字段已经是猫娘口吻的完整播报。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，留空则用常用城市"},
                "hours": {"type": "integer", "description": "看未来几小时，1-24，默认 6"},
            },
        },
    )
    async def llm_get_hourly_weather(self, *, city: str = "", hours: int = 6):
        settings = await self._settings()
        limit = max(1, min(24, int(hours or 6)))
        try:
            report = await self._report(city, days=1, hourly_hours=limit)
        except SdkError as exc:
            return {"error": str(exc)}
        return {
            "speech": build_hourly_speech(
                report["location"]["display"],
                report["hourly"],
                cute=settings["cute"],
                cat_suffix_text=settings["cat_suffix"],
            ),
            "location": report["location"]["display"],
            "hourly": report["hourly"],
        }

    @llm_tool(
        name="push_weather_brief",
        description=(
            "让猫娘主动播报一次今日天气。用户说「给我播报一下天气」「念一下今天天气」"
            "「早上好，说一下天气」时调用。\n"
            "播报会由插件直接显示在聊天里并由猫娘念出来。你只需回一句可爱的确认，"
            "不要重复天气内容。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，留空则用常用城市"},
            },
        },
    )
    async def llm_push_weather_brief(self, *, city: str = ""):
        try:
            report = await self._report(city)
        except SdkError as exc:
            return {"error": str(exc)}
        speech = await self._speak(report)
        # respond：让模型读取并回应，从而走角色语音把播报念出来
        submitted = await self._push_speech(
            speech, priority=6, force_ai_behavior="respond"
        )
        if not submitted:
            return speech
        return (
            f"已经主动播报过 {report['location']['display']} 的天气了。"
            "回一句可爱的短话即可，不要重复天气内容。"
        )

    @llm_tool(
        name="check_daily_weather_push",
        description=(
            "检查今天的每日天气播报是不是到点该发了，到了就直接发出去。"
            "当用户问「今天播报了吗」「说好的天气呢」「早上好」并且现在正好在播报时段时调用。"
        ),
        parameters={"type": "object", "properties": {}},
    )
    async def llm_check_daily_push(self):
        settings = await self._settings()
        if not settings["daily_push"]:
            return {"sent": False, "reason": "每日播报已关闭"}

        target_city = settings["push_city"] or settings["home_city"]
        if not target_city:
            return {"sent": False, "reason": "还没设置常用城市"}

        today = _dt.datetime.now().date().isoformat()
        if self._last_push_date == today:
            return {"sent": False, "reason": "今天已经播报过了"}

        in_window, reason = self._in_daily_window(settings)
        if not in_window:
            return {"sent": False, "reason": reason, "push_time": settings["push_time"]}

        try:
            report = await self._report(target_city)
        except SdkError as exc:
            return {"error": str(exc)}

        speech = await self._speak(report)
        sent = await self._push_speech(speech, priority=6, force_ai_behavior="respond")
        if sent:
            self._last_push_date = today
        return {
            "sent": sent,
            "location": report["location"]["display"],
            "speech": speech,
        }


def _parse_hhmm(value: str) -> tuple[int, int]:
    """解析 'HH:MM'，非法值退回 07:30。"""
    text = str(value or "").strip()
    try:
        hour_str, _, minute_str = text.partition(":")
        hour = int(hour_str)
        minute = int(minute_str) if minute_str else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    return 7, 30
