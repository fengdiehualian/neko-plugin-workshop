"""面板契约面：dashboard 数据源 + 全部 @plugin_entry/@ui.action/@quick_action 入口。

拆分自 __init__.py（方法逐字搬移，仅换文件与导入）；经 Mixin 组合回
ForeverCompanionPlugin——SDK 的 entry 发现遍历 type(self)（collect_entries 用
getmembers_static(type(self))），与定义文件无关。装饰器叠放顺序保持原样
（@ui.action 在 @plugin_entry 之上，元数据靠叠放传递）。

面板类入口一律作用于宿主当前角色（面板 5 秒轮询自动跟随切卡，无手动选中态），
见 _current_shard_async；带 _ctx 的宿主路由调用按 _ctx.lanlan_name 归因。
"""

from __future__ import annotations

import time
from typing import Any
from zoneinfo import ZoneInfo

from plugin.sdk.plugin import Err, Ok, Result, SdkError, plugin_entry, quick_action, tr, ui

from ..core.appearance import (
    APPEARANCE_FILLS,
    APPEARANCE_POSITIONS,
    ImageDataUrlError,
    appearance_defaults,
    clamp_appearance,
    gallery_add_item,
    gallery_find,
    gallery_img_key,
    gallery_next_id,
    gallery_normalize_index,
    gallery_remove_item,
    legacy_to_gallery,
    parse_image_data_url,
)
from ..core.birthday import (
    normalize_birthday,
    parse_birthday,
)
from ..core.cycle import (
    TideConfigError,
    build_month_calendar,
    build_status_payload,
    compute_phase_state,
    parse_anchor_date,
)
from ..core.journal import archive_brief, page_header
from ..core.onboarding import build_readiness, make_guide_record, wizard_pending
from ..core.review import (
    elapsed_days as review_elapsed_days,
)
from ..core.review import (
    new_stats as review_new_stats,
)
from ..core.review import (
    review_archive_brief,
    review_due,
)
from ..core.state import (
    _FRAGMENT_DEFAULT_SLOT,
    _GALLERY_THUMB_MAX_CHARS,
    _REVIEW_DEFAULT_SLOT,
    _REVIEW_MIN_TURNS_FORCED,
    _STORE_GALLERY_INDEX,
    _STORE_PANEL_APPEARANCE,
    _STORE_PANEL_BG,
    _TIMED_ACTIONS,
    _TONE_SLOT_OPTIONS_CACHE_TTL,
    _TONE_SLOT_PREFIXES,
    _caps_key,
    _cfg_section,
    _cycle_key,
    _debug_backup_key,
    _diary_key,
    _journal_archive_key,
    _journal_key,
    _LanlanShard,
    _legacy_debug_backup_key,
    _mood_key,
    _MoodState,
    _now_utc,
    _parse_iso_ts,
    _review_archive_key,
    _review_key,
    _review_stats_key,
    _stats_key,
    _weekly_key,
)
from ..core.stats import (
    badges_payload,
    heatmap_payload,
    month_view,
    summary_payload,
)
from ..services.tone_slot import diagnose_slot_dormancy

JsonObject = dict[str, Any]


class PanelEntriesMixin:
    """全部用户/面板入口：dashboard 数据源、设置、周期操作、日记浏览、危险区。"""

    # ==========================================
    # 用户/面板入口
    # 面板类入口一律作用于宿主当前角色（面板 5 秒轮询自动跟随切卡，无手动选中态），
    # 见 _current_shard_async；带 _ctx 的宿主路由调用按 _ctx.lanlan_name 归因。
    # ==========================================

    async def _tone_slot_options(self) -> list[JsonObject]:
        """面板"语气分析模型槽位"下拉数据源：固定槽位列表 + 各槽当前 ModelId 显示值。

        GET /api/config/core_api 取不到（宿主不可达/超时）时退回静态列表（model 空串），
        整体 try/except，绝不能拖垮 dashboard 渲染。
        成功结果缓存 60s（面板 5s 轮询，槽位模型名变化频率极低）；
        失败不缓存——与 _fetch_known_catgirls 同例，宿主恢复后下趟即可见。
        """
        cached, ts = self._tone_slot_options_cache
        if cached is not None and time.monotonic() - ts < _TONE_SLOT_OPTIONS_CACHE_TTL:
            return cached
        options: list[JsonObject] = [{"value": "", "model": ""}] + [
            {"value": slot, "model": ""} for slot in _TONE_SLOT_PREFIXES
        ]
        try:
            payload = await self._proactive_http("GET", "/api/config/core_api")
            if isinstance(payload, dict):
                for opt in options[1:]:
                    model = str(
                        payload.get(f"{_TONE_SLOT_PREFIXES[str(opt['value'])]}ModelId") or ""
                    ).strip()
                    opt["model"] = model
                self._tone_slot_options_cache = (options, time.monotonic())
        except Exception as exc:  # noqa: BLE001 - best-effort，静态兜底已可用
            self.logger.debug("fetch tone slot options failed: {}", exc)
        return options

    @ui.context(id="dashboard")
    async def dashboard(self) -> JsonObject:
        # 面板轮询驱动协调状态机（子进程无存活事件循环，见 _supervise_once）
        await self._supervise_once()
        lanlan, shard = await self._current_shard_async()
        try:
            phase = self._current_phase_state(shard)
            status = build_status_payload(phase, enabled=self._enabled(shard))
        except TideConfigError as exc:
            status = {"enabled": self._enabled(shard), "error": str(exc)}
        mood_active = self._mood_enabled(shard) and shard.mood.is_active()
        # 连续心情（只可视化不注入）：读取时惰性衰减到当前时刻，保留两位小数下发
        valence, arousal = self._current_affect(shard)
        mood_payload: JsonObject = {
            "active": mood_active,
            # 系统级开关态（忽略总开关，防双重提示）：配置 ∧ 能力否决集
            # （1.2.7 能力中心：功能页关掉情绪引擎，状态条提示也要如实跟上）
            "system_enabled": self._cap_config_on("mood_engine", lanlan),
            "affect": {
                "valence": round(valence, 2),
                "arousal": round(arousal, 2),
                # 静息基线下发给仪表盘画"平复回归位"刻度
                "arousal_baseline": round(self._affect_arousal_baseline(), 2),
            },
        }
        if mood_active:
            mood_payload.update({
                "action": shard.mood.action,
                "action_label": self._action_label(shard.mood.action),
                "reason": shard.mood.reason,
                "expires_at": shard.mood.expires_at or None,
            })
        # 潮汐日历：当前月前后各一个月的逐日推算（委婉显示，一律以"潮汐"代称）。
        # 结果按（角色, 当天日期, 周期参数指纹）缓存：面板 5s 轮询，同日同参数直接复用；
        # 改参数/锚点/快进、跨天、切角色都会改变 key 自然失效，无需显式清理
        calendar = None
        try:
            params = self._cycle_params(shard)
            today = self._today_str()
            cal_key = (
                lanlan,
                today.isoformat(),
                params["anchor"].isoformat(),
                int(params["cycle_length"]),
                int(params["period_length"]),
                int(params["ovulation_day"]),
                int(params["ovulation_window"]),
                int(params["advance_days"]),
                bool(self._auto_derive(shard)),
            )
            cached_key, cached_months = self._calendar_cache
            if cached_months is not None and cached_key == cal_key:
                months = cached_months
            else:
                months = build_month_calendar(
                    today=today,
                    anchor=params["anchor"],
                    cycle_length=params["cycle_length"],
                    period_length=params["period_length"],
                    ovulation_day=params["ovulation_day"],
                    ovulation_window=params["ovulation_window"],
                    advance_days=params["advance_days"],
                )
                self._calendar_cache = (cal_key, months)
            calendar = {"months": months}
        except TideConfigError as exc:
            calendar = {"error": str(exc)}
        # 各角色状态摘要（含孤儿标注）：orphan = 在 lanlan_index 但不在宿主现存名单。
        # 名单不可达（None=未知）时一律标 False，绝不误标。
        known = await self._fetch_known_catgirls()
        lanlan_list: list[JsonObject] = []
        for name in self._lanlan_index:
            item_shard = self._get_shard(name)
            try:
                item_phase = self._current_phase_state(item_shard).phase
            except TideConfigError:
                item_phase = "error"
            except Exception:  # noqa: BLE001 - 摘要尽力而为，单角色异常不拖垮面板
                item_phase = ""
            lanlan_list.append({
                "name": name,
                "enabled": self._enabled(item_shard),
                "phase": item_phase,
                "mood_active": self._mood_enabled(item_shard) and item_shard.mood.is_active(),
                # 动作 id 一并下发：面板管理区徽标按语气分色（正面 success/其余 warning）
                "mood_action": item_shard.mood.action if item_shard.mood.is_active() else "",
                "orphan": bool(known is not None and name not in known),
            })
        # 时光日记时间线（混排她的手记与自动碎片，按时间倒序）：碎片仅截 12 条防膨胀；
        # source 缺省按 self 处理（0.6.x 旧数据零迁移）
        def _diary_view(item: JsonObject) -> JsonObject:
            return {
                "ts": item.get("ts"),
                "source": str(item.get("source") or "self"),
                "kind": item.get("kind"),
                "mood": item.get("mood"),
                "entry": item.get("entry"),
                "quote": item.get("quote"),
                "note": item.get("note"),
            }

        auto_count = sum(1 for item in shard.diary if str(item.get("source") or "self") == "auto")
        # 模型通道状态灯（情绪页"模型通道"卡 + 总览共用）：三个小模型通道各自
        # 能否工作 + 休眠原因。语气走宿主情感端点（enabled 判定），碎片/成文走
        # 直连槽位（复用槽位诊断：free_route / no_model / ok）
        channel_status = {
            "tone": {
                "enabled": self._emotion_sense_enabled(shard),
                "dormant_reason": "",
            },
            "fragments": await self._channel_dormancy(
                self._fragments_enabled(shard),
                str(self._fragments_cfg.get("slot") or "").strip() or _FRAGMENT_DEFAULT_SLOT,
            ),
            "review": await self._channel_dormancy(
                self._review_enabled(shard),
                str(self._review_cfg.get("slot") or "").strip() or _REVIEW_DEFAULT_SLOT,
            ),
        }
        # 近 7 天互动轮数（总览"相处信号"卡）：从时光日记/手记时间戳聚合太粗，
        # 直接看 review_stats 太短视（成文即清零）——改为统计 diary 时间线里
        # 最近 7 天的条目数（她的手记+碎片都是互动的沉淀，可作相处活跃度的代理）
        week_turns = self._week_activity_count(shard)
        return {
            # 面板只读角色列表数据源：lanlan=宿主当前角色（自动跟随），lanlan_list=全部已知角色摘要
            "lanlan": lanlan,
            "lanlan_list": lanlan_list,
            "status": status,
            "anchor_date": str(shard.cycle.get("anchor_date") or self._tide_cfg.get("anchor_date") or ""),
            "advance_days": int(shard.cycle.get("advance_days") or 0),
            "mood": mood_payload,
            "settings": self._settings_snapshot(shard),
            # 面板外观（1.2.0）：图库/外观参数不进轮询，面板打开时走
            # get_panel_gallery 一次性按需拉取（原"轻量标记"通道前端从未消费，已删）
            # 语气分析模型槽位下拉选项（含各槽当前模型名；拉取失败时静态兜底）
            "tone_slot_options": await self._tone_slot_options(),
            "calendar": calendar,
            "diary_recent": [_diary_view(item) for item in reversed(shard.diary[-12:])],
            "diary_total": len(shard.diary),
            "fragment_total": auto_count,
            # 个人日记（书页式）：index 供面板展示页数概览（极轻量，进 5s 轮询）；
            # 全量翻阅走 get_journal 入口按需拉取，不进轮询
            "journal_index": [page_header(page) for page in shard.journal],
            # 藏书阁（1.3.0）：合订本概览（极轻量：本数 + 时段）——面板据此在书架
            # 末尾决定画不画那根横放书脊；全量翻阅走 get_journal_archive 按需拉取
            "journal_archive_brief": archive_brief(shard.journal_archive),
            # 邀请挂起态（1.2.3）：递过邀请、她还没落笔——日记页显示"等她"提示，
            # 免得"点了没反应、过一会凭空多一页"
            "journal_invite_pending": self._journal_invite_pending(shard),
            # 我的日记（0.8.0）：篇数 + 素材进度（极轻量）；全量翻阅走
            # get_review 入口按需拉取，成文正文不进 5s 轮询。
            # writing / last_result（1.2.3）：面板「立即写一篇」改排队成文后的
            # 回流通道——写作中点亮按钮禁用态；last_result={ts,written,reason}
            # 是队列成文的成败结论，面板按 ts 去重弹一次 toast（内存即弃）
            "review_brief": {
                "enabled": self._review_enabled(shard),
                "entries": len(shard.review),
                "progress_turns": int(shard.review_stats.get("turns") or 0),
                "turns_threshold": self._review_turns_threshold(),
                "writing": bool(shard.pending_review_write) or lanlan in self._review_writing,
                "last_result": shard.review_write_result,
            },
            # 档案室（1.3.0）：旧卷宗合档概览（极轻量：卷数 + 时段）——面板据此
            # 决定画不画档案架末尾那只档案盒；全量翻阅走 get_review(scope=archive)
            "review_archive_brief": review_archive_brief(shard.review_archive),
            # 模型通道状态灯（情绪页"模型通道"卡）：ok / free_route / no_model / disabled
            "channel_status": channel_status,
            # 能力中心总览（1.2.7）：功能页数据源随 5s 轮询下发（纯内存小载荷）。
            # 与总开关/设置页/后台变化同帧一致——"按需拉一次就定格"的窗口不存在；
            # list_capabilities 入口保留（API/调试），同一构建器口径唯一
            "capabilities": self._cap_view(lanlan),
            # 近 7 天相处活跃度（总览"相处信号"卡）：时光日记近 7 天条目数
            "week_activity": week_turns,
            # 相处统计（1.1.0）：数字摘要 + 徽章墙进 5s 轮询（纯本地即时计算，
            # 开销可忽略）；热力图/月报数据量大，走 get_stats 入口按需拉取
            "stats_summary": self._stats_summary_view(shard),
            # 生日轻语（1.3.1）：时光页生日设置卡数据（纯本地，零 IO）。
            # 1.3.1 改版：倒数/当天视图字段随总览生日卡一同退役
            "birthday": self._birthday_view(),
            # 新手引导 + 就绪清单（1.2.6）：wizard_pending 读内存位（面板关闭
            # 向导时同步更新，5s 轮询滞后期不会重复弹窗）；清单纯本地即时计算，
            # 零模型开销、零新增 IO
            "onboarding": {
                "wizard_pending": wizard_pending(self._guide),
                "guide": dict(self._guide),
                "readiness": build_readiness({
                    "rhythm": self._enabled(shard),
                    "anchor": bool(
                        str(shard.cycle.get("anchor_date") or self._tide_cfg.get("anchor_date") or "")
                    ),
                    "mood": self._cap_config_on("mood_engine", lanlan),
                    "channels": self._channels_any_live(channel_status),
                    "together": bool(shard.stats.get("first_seen")),
                }),
            },
        }

    def _birthday_view(self) -> JsonObject:
        """生日设置卡轮询视图：哪天/设没设/留不留纪念手记（全局日期，各角色同值）。"""
        cfg = getattr(self, "_birthday_cfg", None) or {}
        raw = str(cfg.get("date") or "").strip()
        return {
            "date": raw,
            "set": parse_birthday(raw) is not None,
            "keep_diary": cfg.get("keep_diary", True) is not False,
        }

    @staticmethod
    def _channels_any_live(channel_status: JsonObject) -> bool:
        """语气/碎片/成文三条模型通道是否至少一路可用（就绪清单信号）。"""
        for key in ("tone", "fragments", "review"):
            item = channel_status.get(key) or {}
            if item.get("enabled") and not item.get("dormant_reason"):
                return True
        return False

    def _stats_summary_view(self, shard: _LanlanShard) -> JsonObject:
        """相处统计的轮询轻量视图：数字摘要 + 徽章墙（无热力图/月报本体）。"""
        try:
            today = self._stats_today()
            return {
                "summary": summary_payload(shard.stats, today),
                "badges": badges_payload(shard.stats, today),
            }
        except Exception as exc:  # noqa: BLE001 - 统计视图尽力而为，不拖垮 dashboard
            self.logger.debug("stats summary view failed: {}", exc)
            return {"summary": {}, "badges": []}

    async def _channel_dormancy(self, enabled: bool, slot: str) -> JsonObject:
        """直连通道状态灯：{enabled, dormant_reason}。reason ∈ ok/free_route/no_model/disabled。

        诊断复用 diagnose_slot_dormancy（tone_slot.py）；free_route = 宿主免费路由
        服务端拒第三方直连（不可配置绕过），no_model = 槽位没配模型（去宿主设置配）。
        """
        if not enabled:
            return {"enabled": False, "dormant_reason": "disabled"}
        reason = diagnose_slot_dormancy(await self._aload_core_config(), slot)
        # reason 为 "free_route"/"no_model" 时通道休眠；解析成功与否最终由
        # _resolve_tone_slot 决定，这里给面板的灯做的是"为什么不行"的归类
        return {"enabled": True, "dormant_reason": reason}

    def _week_activity_count(self, shard: _LanlanShard) -> int:
        """近 7 天相处活跃度：时光日记时间线（手记+碎片）里 7 天内的条目数。

        手记/碎片都是互动的沉淀物，比 review_stats（成文即清零）更能反映
        "最近我们还在频繁相处吗"；纯统计零模型开销。
        """
        cutoff = _now_utc().timestamp() - 7 * 86400
        count = 0
        for item in shard.diary:
            ts = _parse_iso_ts(item.get("ts"))
            if ts is not None and ts.timestamp() >= cutoff:
                count += 1
        return count

    _EDITABLE_SETTINGS = (
        "enabled", "auto_derive", "cycle_length", "period_length",
        "ovulation_day", "ovulation_window", "inject_mode", "inject_interval_n",
        "phase_openers", "timezone", "activity_context", "debug_mode",
        "mood_enabled", "default_action_minutes", "open_action_timeout_minutes",
        "emotion_sense_enabled", "tone_check_rate",
        "tone_phase_sensitivity_enabled", "tone_phase_sensitivity",
        "tone_slot",
        "fragments_enabled", "fragments_slot",
        "review_enabled", "review_slot", "review_turns_threshold", "review_days_threshold",
        "anniversary_inject",
        "birthday_enabled", "birthday_keep_diary", "birthday_date",
    )

    # per-character 字段：写当前角色 shard 的 enabled / params；其余为全局字段（写 settings 覆盖层）
    _PER_CHAR_SETTINGS = (
        "enabled", "auto_derive", "cycle_length", "period_length", "ovulation_day", "ovulation_window",
    )

    def _settings_snapshot(self, shard: _LanlanShard | None = None) -> JsonObject:
        shard = shard or self._current_shard()
        effective = self._effective_cycle_settings(shard)
        return {
            "enabled": self._enabled(shard),
            "auto_derive": self._auto_derive(shard),
            "cycle_length": int(effective["cycle_length"]),
            "period_length": int(effective["period_length"]),
            "ovulation_day": int(effective["ovulation_day"]),
            "ovulation_window": int(effective["ovulation_window"]),
            "inject_mode": str(self._tide_cfg.get("inject_mode") or "every_user_message"),
            "inject_interval_n": int(self._tide_cfg.get("inject_interval_n") or 3),
            "phase_openers": bool(self._tide_cfg.get("phase_openers", True)),
            "timezone": str(self._tide_cfg.get("timezone") or "auto"),
            "activity_context": bool(self._tide_cfg.get("activity_context", True)),
            "debug_mode": bool(self._tide_cfg.get("debug_mode", False)),
            "mood_enabled": bool(self._mood_cfg.get("enabled", True)),
            "default_action_minutes": int(self._mood_cfg.get("default_action_minutes") or 10),
            "open_action_timeout_minutes": int(
                self._mood_cfg.get("open_action_timeout_minutes")
                if self._mood_cfg.get("open_action_timeout_minutes") is not None
                else 120
            ),
            "emotion_sense_enabled": bool(self._emotion_sense_cfg.get("enabled", True)),
            "tone_check_rate": self._tone_float_cfg("check_rate", 1.0),
            "tone_phase_sensitivity_enabled": bool(
                self._emotion_sense_cfg.get("phase_sensitivity_enabled", True)
            ),
            "tone_phase_sensitivity": self._tone_float_cfg("phase_sensitivity", 0.15),
            "tone_slot": str(self._emotion_sense_cfg.get("slot") or ""),
            "fragments_enabled": bool(self._fragments_cfg.get("enabled", True)),
            "fragments_slot": str(self._fragments_cfg.get("slot") or _FRAGMENT_DEFAULT_SLOT),
            "review_enabled": bool(self._review_cfg.get("enabled", True)),
            "review_slot": str(self._review_cfg.get("slot") or _REVIEW_DEFAULT_SLOT),
            "review_turns_threshold": self._review_turns_threshold(),
            "review_days_threshold": self._review_days_threshold(),
            # 相处统计：纪念日注入开关（[stats].anniversary_inject，纯统计本身无开关）
            "anniversary_inject": (self._stats_cfg or {}).get("anniversary_inject", True) is not False,
            # 生日轻语（1.3.1）：能力开关/纪念手记/主人生日（全局，年份不参与年龄计算）
            "birthday_enabled": (self._birthday_cfg or {}).get("enabled", True) is not False,
            "birthday_keep_diary": (self._birthday_cfg or {}).get("keep_diary", True) is not False,
            "birthday_date": str((self._birthday_cfg or {}).get("date") or ""),
        }

    @ui.action(
        label=tr("actions.update_settings.label", default="保存设置"),
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="update_settings",
        name=tr("entries.update_settings.name", default="更新潮汐设置"),
        description=tr(
            "entries.update_settings.description",
            default="更新周期参数（作用于当前角色）、注入策略、情绪系统与开场白开关（全局）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "auto_derive": {"type": "boolean"},
                "cycle_length": {"type": "integer", "minimum": 10, "maximum": 90},
                "period_length": {"type": "integer", "minimum": 1, "maximum": 14},
                "ovulation_day": {"type": "integer", "minimum": 2},
                "ovulation_window": {"type": "integer", "minimum": 1, "maximum": 5},
                "inject_mode": {
                    "type": "string",
                    "enum": ["every_user_message", "interval_n", "on_trigger", "off"],
                },
                "inject_interval_n": {"type": "integer", "minimum": 1, "maximum": 50},
                "phase_openers": {"type": "boolean"},
                "timezone": {"type": "string"},
                "activity_context": {"type": "boolean"},
                "debug_mode": {"type": "boolean"},
                "mood_enabled": {"type": "boolean"},
                "default_action_minutes": {"type": "integer", "minimum": 1, "maximum": 720},
                "open_action_timeout_minutes": {"type": "integer", "minimum": 0, "maximum": 1440},
                "emotion_sense_enabled": {"type": "boolean"},
                "tone_check_rate": {"type": "number", "enum": [0, 0.25, 0.5, 1.0]},
                "tone_phase_sensitivity_enabled": {"type": "boolean"},
                "tone_phase_sensitivity": {"type": "number", "enum": [0.1, 0.15, 0.25]},
                "tone_slot": {
                    "type": "string",
                    "enum": ["", "conversation", "summary", "correction", "emotion", "vision", "agent"],
                },
                "fragments_enabled": {"type": "boolean"},
                "fragments_slot": {
                    "type": "string",
                    "enum": ["summary", "conversation", "correction", "vision", "agent"],
                },
                "review_enabled": {"type": "boolean"},
                "review_slot": {
                    "type": "string",
                    "enum": ["summary", "conversation", "correction", "vision", "agent"],
                },
                "review_turns_threshold": {"type": "integer", "minimum": 10, "maximum": 500},
                "review_days_threshold": {"type": "integer", "minimum": 1, "maximum": 90},
                "anniversary_inject": {"type": "boolean"},
                "birthday_enabled": {"type": "boolean"},
                "birthday_keep_diary": {"type": "boolean"},
                "birthday_date": {
                    "type": "string",
                    "description": tr("fields.birthday_date", default="主人生日 YYYY-MM-DD，空串清除"),
                },
            },
        },
        metadata={"agent_hidden": True},
    )
    async def update_settings(self, **kwargs: Any):
        updates = {k: v for k, v in kwargs.items() if k in self._EDITABLE_SETTINGS and not k.startswith("_")}
        if not updates:
            return Err(SdkError("no_valid_fields"))
        lanlan, shard = await self._current_shard_async()
        # 本入口的两处落盘（cycle@<角色> 与全局 settings）：任一 Err 最后回 Err
        res_cycle: Result[None] = Ok(None)
        res_settings: Result[None] = Ok(None)
        try:
            tide_patch: JsonObject = {}
            mood_patch: JsonObject = {}
            es_patch: JsonObject = {}
            frag_patch: JsonObject = {}
            review_patch: JsonObject = {}
            stats_patch: JsonObject = {}
            birthday_patch: JsonObject = {}
            params_patch: JsonObject = {}
            # shard 落盘合并标记：enabled 与 params 同时变更时只写一次 cycle@<lanlan>
            cycle_dirty = False
            if "enabled" in updates:
                # per-character 开关写入 shard（运行态），不覆盖配置文件默认值；
                # 关闭→开启的瞬间 prime 注入一次，避免"开了没反应"的困惑
                was_enabled = self._enabled(shard)
                shard.cycle["enabled"] = bool(updates["enabled"])
                cycle_dirty = True
            if "auto_derive" in updates:
                params_patch["auto_derive"] = bool(updates["auto_derive"])
            if "cycle_length" in updates:
                params_patch["cycle_length"] = max(10, min(90, int(updates["cycle_length"])))
            if "period_length" in updates:
                params_patch["period_length"] = max(1, min(14, int(updates["period_length"])))
            if "ovulation_day" in updates:
                params_patch["ovulation_day"] = max(2, min(89, int(updates["ovulation_day"])))
            if "ovulation_window" in updates:
                params_patch["ovulation_window"] = max(1, min(5, int(updates["ovulation_window"])))
            if params_patch:
                params = dict(self._shard_params(shard))
                params.update(params_patch)
                shard.cycle["params"] = params
                cycle_dirty = True
            if cycle_dirty:
                # enabled 与 params 的变更合并为一次落盘（原先两处各写一次），语义不变
                res_cycle = await self._save_shard_cycle(lanlan, shard)
            if "enabled" in updates:
                # prime 注入保持在落盘之后（先持久化再副作用，与阶段开场白同例）
                await self._prime_inject_on_enable(was_enabled, lanlan, shard)
            # ---- 以下为全局字段：写 settings 覆盖层 ----
            if "inject_mode" in updates:
                mode = str(updates["inject_mode"])
                if mode not in ("every_user_message", "interval_n", "on_trigger", "off"):
                    # i18n 契约（1.3.0 第九轮）：面板可见错误只回稳定码，坏值进日志
                    self.logger.warning("invalid inject_mode: {}", mode)
                    return Err(SdkError("invalid_inject_mode"))
                tide_patch["inject_mode"] = mode
            if "inject_interval_n" in updates:
                tide_patch["inject_interval_n"] = max(1, min(50, int(updates["inject_interval_n"])))
            if "phase_openers" in updates:
                tide_patch["phase_openers"] = bool(updates["phase_openers"])
            if "activity_context" in updates:
                tide_patch["activity_context"] = bool(updates["activity_context"])
            if "debug_mode" in updates:
                tide_patch["debug_mode"] = bool(updates["debug_mode"])
            if "timezone" in updates:
                tz = str(updates["timezone"]).strip() or "auto"
                if tz != "auto":
                    try:
                        ZoneInfo(tz)  # 合法性校验（auto = 系统本地时区）
                    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError 是 KeyError 子类
                        # 不接进外层兼容分支的 settings_save_failed：这是参数错不是保存失败
                        self.logger.warning("invalid timezone: {}", tz)
                        return Err(SdkError("invalid_timezone"))
                tide_patch["timezone"] = tz
            if "mood_enabled" in updates:
                mood_patch["enabled"] = bool(updates["mood_enabled"])
            if "default_action_minutes" in updates:
                mood_patch["default_action_minutes"] = max(1, min(720, int(updates["default_action_minutes"])))
            if "open_action_timeout_minutes" in updates:
                mood_patch["open_action_timeout_minutes"] = max(0, min(1440, int(updates["open_action_timeout_minutes"])))
            # ---- 语气感知（[emotion_sense]），全局 ----
            if "emotion_sense_enabled" in updates:
                es_patch["enabled"] = bool(updates["emotion_sense_enabled"])
            if "tone_check_rate" in updates:
                es_patch["check_rate"] = min(1.0, max(0.0, float(updates["tone_check_rate"])))
            if "tone_phase_sensitivity_enabled" in updates:
                es_patch["phase_sensitivity_enabled"] = bool(updates["tone_phase_sensitivity_enabled"])
            if "tone_phase_sensitivity" in updates:
                es_patch["phase_sensitivity"] = min(0.5, max(0.0, float(updates["tone_phase_sensitivity"])))
            if "tone_slot" in updates:
                slot = str(updates["tone_slot"]).strip()
                if slot and slot not in _TONE_SLOT_PREFIXES:
                    self.logger.warning("invalid tone_slot: {}", slot)
                    return Err(SdkError("invalid_tone_slot"))
                es_patch["slot"] = slot
            # ---- 时光日记·自动碎片（[fragments]），全局 ----
            if "fragments_enabled" in updates:
                frag_patch["enabled"] = bool(updates["fragments_enabled"])
            if "fragments_slot" in updates:
                slot = str(updates["fragments_slot"]).strip() or _FRAGMENT_DEFAULT_SLOT
                if slot not in _TONE_SLOT_PREFIXES:
                    self.logger.warning("invalid fragments_slot: {}", slot)
                    return Err(SdkError("invalid_fragments_slot"))
                frag_patch["slot"] = slot
            # ---- 我的日记（[review]），全局 ----
            if "review_enabled" in updates:
                review_patch["enabled"] = bool(updates["review_enabled"])
            if "review_slot" in updates:
                # 成文必须走可自定义 prompt 的直连槽位（宿主情感端点只做五分类），
                # emotion 槽与空串都不可选
                slot = str(updates["review_slot"]).strip() or _REVIEW_DEFAULT_SLOT
                if slot not in _TONE_SLOT_PREFIXES or slot == "emotion":
                    self.logger.warning("invalid review_slot: {}", slot)
                    return Err(SdkError("invalid_review_slot"))
                review_patch["slot"] = slot
            if "review_turns_threshold" in updates:
                review_patch["turns_threshold"] = max(10, min(500, int(updates["review_turns_threshold"])))
            if "review_days_threshold" in updates:
                review_patch["days_threshold"] = max(1, min(90, int(updates["review_days_threshold"])))
            # ---- 相处统计（[stats]），全局 ----
            if "anniversary_inject" in updates:
                stats_patch["anniversary_inject"] = bool(updates["anniversary_inject"])
            # ---- 生日轻语（[birthday]），全局（1.3.1）----
            if "birthday_enabled" in updates:
                birthday_patch["enabled"] = bool(updates["birthday_enabled"])
            if "birthday_keep_diary" in updates:
                birthday_patch["keep_diary"] = bool(updates["birthday_keep_diary"])
            if "birthday_date" in updates:
                try:
                    # 校验+归一：非法日期当场拒（不能让坏 date 静默休眠到用户以为设好了）
                    birthday_patch["date"] = normalize_birthday(updates["birthday_date"])
                except ValueError as exc:
                    self.logger.warning("birthday_date rejected: {}", exc)
                    return Err(SdkError("invalid_birthday_date"))

            # Store 为权威存储（Steam 上配置文件写常超时，Store 稳定且重启不丢）；
            # 配置文件同步放后台，不阻塞保存响应
            if tide_patch or mood_patch or es_patch or frag_patch or review_patch or stats_patch or birthday_patch:
                overrides = self._settings_override
                if tide_patch:
                    overrides["tide"] = {**_cfg_section(overrides.get("tide")), **tide_patch}
                    self._tide_cfg.update(tide_patch)
                if mood_patch:
                    overrides["mood"] = {**_cfg_section(overrides.get("mood")), **mood_patch}
                    self._mood_cfg.update(mood_patch)
                if es_patch:
                    overrides["emotion_sense"] = {
                        **_cfg_section(overrides.get("emotion_sense")), **es_patch,
                    }
                    self._emotion_sense_cfg.update(es_patch)
                if frag_patch:
                    overrides["fragments"] = {
                        **_cfg_section(overrides.get("fragments")), **frag_patch,
                    }
                    self._fragments_cfg.update(frag_patch)
                if review_patch:
                    overrides["review"] = {
                        **_cfg_section(overrides.get("review")), **review_patch,
                    }
                    self._review_cfg.update(review_patch)
                if stats_patch:
                    overrides["stats"] = {
                        **_cfg_section(overrides.get("stats")), **stats_patch,
                    }
                    self._stats_cfg.update(stats_patch)
                if birthday_patch:
                    overrides["birthday"] = {
                        **_cfg_section(overrides.get("birthday")), **birthday_patch,
                    }
                    self._birthday_cfg.update(birthday_patch)
                res_settings = await self._save_settings()
                self._sync_debug_entries()
        except (TideConfigError, ValueError, TypeError) as exc:
            self.logger.warning("update_settings rejected: {}: {}", type(exc).__name__, exc)
            return Err(SdkError("settings_value_invalid"))
        except Exception as exc:  # noqa: BLE001 - 配置写失败统一报错给面板
            self.logger.warning("update_settings failed: {}", exc)
            return Err(SdkError("settings_save_failed"))

        # 落盘失败优先于一切回显：任一 Err 即向面板报错（参数校验的 warning
        # 只说明"没存住的值还不合法"，不掩盖写失败本身）
        persist_err = self._persist_error(res_cycle, res_settings)
        if persist_err is not None:
            return persist_err
        # 参数变更后校验一次整体合法性（如活跃窗口与潮汐期重叠，按当前角色 shard）
        try:
            phase = self._current_phase_state(shard)
        except TideConfigError as exc:
            return Ok({
                **self._settings_snapshot(shard),
                "warning": f"已保存，但当前参数组合不合法：{exc}",
            })
        return Ok({**self._settings_snapshot(shard), "phase": phase.phase})

    @ui.action(
        label=tr("actions.refresh.label", default="刷新"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="get_status",
        name=tr("entries.get_status.name", default="查看潮汐状态"),
        description=tr("entries.get_status.description", default="看看她今天处于周期的哪个阶段、第几天。"),
        input_schema={"type": "object", "properties": {}},
    )
    @quick_action(icon="🌙", priority=10)
    async def get_status(self, **_: Any):
        # 入口触发驱动协调状态机（含 quick_action 面板按钮）
        await self._supervise_once()
        lanlan, shard = await self._current_shard_async()
        try:
            phase = self._current_phase_state(shard)
        except TideConfigError as exc:
            self.logger.warning("invalid tide config: {}", exc)
            return Err(SdkError("invalid_tide_config"))
        return Ok({
            **build_status_payload(phase, enabled=self._enabled(shard)),
            "lanlan": lanlan,
            "anchor_date": str(shard.cycle.get("anchor_date") or self._tide_cfg.get("anchor_date") or ""),
            "advance_days": int(shard.cycle.get("advance_days") or 0),
        })

    @ui.action(
        label=tr("actions.set_anchor.label", default="设置潮汐首日"),
        confirm=tr("actions.set_anchor.confirm", default="将重设周期首日、清除快进天数并重新计算阶段，确认？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="set_anchor",
        name=tr("entries.set_anchor.name", default="设置潮汐首日"),
        description=tr(
            "entries.set_anchor.description",
            default="设置她本轮周期的第一天（YYYY-MM-DD，作用于当前角色）。重设会把快进天数清零。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": tr("fields.date", default="潮汐首日 YYYY-MM-DD")},
            },
            "required": ["date"],
        },
        metadata={"agent_hidden": True},
    )
    async def set_anchor(self, date: str = "", **_: Any):
        lanlan, shard = await self._current_shard_async()
        try:
            anchor = parse_anchor_date(date)
            self._cycle_params_for_validation(anchor, shard)
        except (TideConfigError, ValueError) as exc:
            self.logger.warning("set_anchor rejected: {}: {}", type(exc).__name__, exc)
            return Err(SdkError("anchor_date_invalid"))
        anchor_iso = anchor.isoformat()
        # Store 权威存储（子进程无存活事件循环，后台同步任务必死，不再尝试）
        shard.cycle["anchor_date"] = anchor_iso
        # 重设首日 = 重新对表：advance_days 是相对旧锚点累积的身体时钟偏移，
        # 不清零的话首日会落在「所设日期 - 快进天数」上（阶段计算用
        # effective = today + advance_days 与 anchor 比对），用户看到的就是
        # "刚设的首日先被快进了一天"。快进语义在新首日下重新从头积累。
        shard.cycle["advance_days"] = 0
        res_cycle = await self._save_shard_cycle(lanlan, shard)
        persist_err = self._persist_error(res_cycle)
        if persist_err is not None:
            return persist_err
        phase = self._current_phase_state(shard)
        self.logger.info("anchor set to {} for {} (advance_days cleared)", anchor_iso, lanlan)
        return Ok({
            **build_status_payload(phase, enabled=self._enabled(shard)),
            "lanlan": lanlan,
            "anchor_date": anchor_iso,
            "advance_days": 0,
        })

    def _cycle_params_for_validation(self, anchor: Any, shard: _LanlanShard | None = None) -> None:
        from datetime import date as _date

        if not isinstance(anchor, _date):
            raise TideConfigError("anchor must be a date")
        params = self._cycle_params(shard)
        params["anchor"] = anchor
        compute_phase_state(
            today=self._today_str(),
            **params,
        )

    @ui.action(
        label=tr("actions.set_onboarding.label", default="记录引导进度"),
        refresh_context=True,
    )
    @plugin_entry(
        id="set_onboarding",
        name=tr("entries.set_onboarding.name", default="更新新手引导状态"),
        description=tr(
            "entries.set_onboarding.description",
            default="记录新手引导已完成/已跳过，或重新打开引导（面板专用，不由模型调用）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["done", "skip", "reopen"]},
            },
            "required": ["action"],
        },
        metadata={"agent_hidden": True},
    )
    async def set_onboarding(self, action: str = "done", **_):
        """新手引导收尾（1.2.6）：done/skip 后不再自动弹，reopen 清回未引导态。

        先改内存位再落盘：未通电会话里盘写不进去，但本会话内至少不重复打扰
        （既定"当场生效、重启即丢"降级契约）；真写失败（磁盘满/DB 锁）回传 Err，
        面板据此提示而不是谎报"引导已保存"。
        """
        if action not in ("done", "skip", "reopen"):
            self.logger.warning("invalid onboarding action: {}", action)
            return Err(SdkError("invalid_onboarding_action"))
        self._guide = make_guide_record(action, _now_utc().isoformat(timespec="seconds"))
        res = await self._save_guide()
        if isinstance(res, Err):
            return res
        self.logger.info("onboarding guide updated: wizard={}", self._guide["wizard"])
        return Ok(dict(self._guide))

    @ui.action(
        label=tr("actions.advance.label", default="快进一天"),
        refresh_context=True,
    )
    @plugin_entry(
        id="advance_days",
        name=tr("entries.advance_days.name", default="快进天数"),
        description=tr("entries.advance_days.description", default="把她的身体时钟往前拨 N 天（可为负数回退，作用于当前角色）。"),
        input_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": tr("fields.days", default="天数，可为负")},
            },
        },
        metadata={"agent_hidden": True},
    )
    async def advance_days_entry(self, days: int = 1, **_: Any):
        lanlan, shard = await self._current_shard_async()
        days = int(days)
        current = int(shard.cycle.get("advance_days") or 0)
        new_value = max(0, current + days)
        shard.cycle["advance_days"] = new_value
        res_cycle = await self._save_shard_cycle(lanlan, shard)
        persist_err = self._persist_error(res_cycle)
        if persist_err is not None:
            return persist_err
        phase = self._current_phase_state(shard)
        return Ok({
            **build_status_payload(phase, enabled=self._enabled(shard)),
            "lanlan": lanlan,
            "advance_days": new_value,
        })

    @ui.action(
        label=tr("actions.toggle.label", default="开/关模拟"),
        confirm=tr("actions.toggle.confirm", default="切换身体节律模拟的总开关（当前角色），确认？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="toggle",
        name=tr("entries.toggle.name", default="开关模拟"),
        description=tr("entries.toggle.description", default="开启或关闭身体节律模拟与注入（作用于当前角色）。"),
        input_schema={"type": "object", "properties": {}},
    )
    async def toggle(self, **_: Any):
        lanlan, shard = await self._current_shard_async()
        was_enabled = self._enabled(shard)
        shard.cycle["enabled"] = not was_enabled
        res_cycle = await self._save_shard_cycle(lanlan, shard)
        await self._prime_inject_on_enable(was_enabled, lanlan, shard)
        # 写失败如实报错（内存仍当场生效）：不再"面板显示成功、盘上没写"
        persist_err = self._persist_error(res_cycle)
        if persist_err is not None:
            return persist_err
        return Ok({
            **build_status_payload(self._current_phase_state(shard), enabled=self._enabled(shard)),
            "lanlan": lanlan,
            "enabled": self._enabled(shard),
        })

    @ui.action(
        label=tr("actions.reset.label", default="重置全部数据"),
        tone="danger",
        confirm=tr("actions.reset.confirm", default="将清空当前角色的周期偏移、情绪状态和心情手记，确认？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="reset_all",
        name=tr("entries.reset_all.name", default="重置全部数据"),
        description=tr(
            "entries.reset_all.description",
            default="清空当前角色的快进天数、情绪状态与心情手记（不改配置锚点，不影响其他角色）。",
        ),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def reset_all(self, **_: Any):
        # 只清当前角色的 shard：其他角色的周期/情绪/手记不受影响
        lanlan, shard = await self._current_shard_async()
        shard.cycle["advance_days"] = 0
        shard.cycle.pop("enabled", None)
        shard.mood = _MoodState()
        shard.diary = []
        # 我的日记一并重置（重置语义是"当前角色清零重来"，评价与素材同属）
        shard.review = []
        shard.review_stats = review_new_stats()
        res_cycle = await self._save_shard_cycle(lanlan, shard)
        res_mood = await self._save_shard_mood(lanlan, shard)
        res_diary = await self._save_shard_diary(lanlan, shard)
        res_review = await self._save_shard_review(lanlan, shard)
        # 重置清空了情绪动作：同步解除暂停，水位写结果一并聚合（1.2.2 审查轮 P2）
        res_pause = await self._maybe_sync_proactive_pause()
        # 多键写入：任一 Err 即向用户报错（前面的写不回滚，保持简单；
        # 真失败极罕见，重试幂等）
        persist_err = self._persist_error(res_cycle, res_mood, res_diary, res_review, res_pause)
        if persist_err is not None:
            return persist_err
        return Ok({
            **build_status_payload(self._current_phase_state(shard), enabled=self._enabled(shard)),
            "lanlan": lanlan,
            "reset": True,
        })

    # ---- 面板外观（1.2.0）：图片图库 + 可调背景（全局一份，与角色无关） ----
    # 每张原图一条 key（gallery_img/<id>），索引与外观参数都是小记录；
    # 图片本体绝不进 5s 轮询 context，面板按需经入口拉取。
    # 旧版单图 panel_bg 在首次 get_panel_gallery 时一次性迁移进图库并生效，
    # 原 key 保留不动（回滚旧版本不丢数据）。
    # @ui.action 是 api.call 可达的前提（同 get_journal 先例），非动作区展示用途

    async def _gallery_index(self) -> JsonObject:
        """图库索引记录（归一后的 {items, next}）；坏数据按空索引降级。
        读失败（Err）经 _store_read 留痕后同样按空索引降级（批次2）。"""
        res = await self._store_read(_STORE_GALLERY_INDEX)
        return gallery_normalize_index(res.value if isinstance(res, Ok) else None)

    async def _gallery_save_index(self, index: JsonObject) -> bool:
        # 统一写出口（1.2.2 审查轮 P1）：未通电时宿主静默空转返回 Ok——与其余
        # 状态一致维持"当场生效、重启即丢"降级契约，但会留 store not ready
        # warning；真失败（Err）返回 False 由入口向用户传播，不再假报保存成功
        res = await self._store_write(
            _STORE_GALLERY_INDEX,
            {"items": index.get("items") or [], "next": int(index.get("next") or 1)},
            "gallery index",
        )
        return not isinstance(res, Err)

    async def _saved_appearance(self) -> JsonObject:
        """已保存的外观参数（归一）；无记录回退默认，不写盘。读失败经 _store_read 留痕。"""
        res = await self._store_read(_STORE_PANEL_APPEARANCE)
        raw = res.value if isinstance(res, Ok) else None
        if isinstance(raw, dict):
            return clamp_appearance(raw)
        return appearance_defaults()

    @ui.action(
        label=tr("actions.get_panel_gallery.label", default="打开图库面板"),
        tone="default",
    )
    @plugin_entry(
        id="get_panel_gallery",
        name=tr("entries.get_panel_gallery.name", default="读取面板图库"),
        description=tr("entries.get_panel_gallery.description", default="读取面板图库索引与外观参数，并迁移旧版单图背景（面板内部用）。"),
        input_schema={"type": "object", "properties": {}},
        metadata={"result_kind": "event", "agent_hidden": True},
    )
    async def get_panel_gallery(self, **_: Any):
        index = await self._gallery_index()
        res = await self._store_read(_STORE_PANEL_APPEARANCE)
        raw = res.value if isinstance(res, Ok) else None
        if isinstance(raw, dict):
            return Ok({"items": index.get("items") or [], "appearance": clamp_appearance(raw), "migrated": False})
        # 外观参数从未建立 → 尝试旧版单图一次性迁移
        legacy_res = await self._store_read(_STORE_PANEL_BG)
        migrated = legacy_to_gallery(legacy_res.value if isinstance(legacy_res, Ok) else None)
        if migrated is not None:
            gid, item, image, appearance = migrated
            if gallery_find(index, gid) is None:
                set_res = await self._store_write(gallery_img_key(gid), image, f"gallery image {gid} (migrated)")
                if isinstance(set_res, Err):
                    return Err(SdkError("background_migrate_failed"))
                index, _ = gallery_add_item(index, item)
                if not await self._gallery_save_index(index):
                    return Err(SdkError("background_migrate_failed"))
            save_res = await self._store_write(
                _STORE_PANEL_APPEARANCE, appearance, "panel appearance (migrated)",
            )
            if isinstance(save_res, Err):
                return Err(SdkError("background_migrate_failed"))
            self.logger.info("legacy panel background migrated into gallery: id={}", gid)
            return Ok({"items": index.get("items") or [], "appearance": appearance, "migrated": True})
        return Ok({"items": index.get("items") or [], "appearance": appearance_defaults(), "migrated": False})

    @ui.action(
        label=tr("actions.gallery_add.label", default="添加图片到图库"),
        tone="primary",
    )
    @plugin_entry(
        id="gallery_add",
        name=tr("entries.gallery_add.name", default="添加图片到图库"),
        description=tr("entries.gallery_add.description", default="把一张图片（data URL，≤4MB）连同缩略图加入面板图库（面板内部用）。"),
        input_schema={
            "type": "object",
            "properties": {
                "data_url": {"type": "string", "description": tr("fields.galleryDataUrl", default="图片 data URL（base64）")},
                "thumb": {"type": "string", "description": tr("fields.galleryThumb", default="缩略图 data URL（可空，面板稍后回填）")},
                "name": {"type": "string", "description": tr("fields.galleryName", default="图片文件名")},
            },
            "required": ["data_url"],
        },
        metadata={"agent_hidden": True},
    )
    async def gallery_add(self, data_url: str = "", thumb: str = "", name: str = "", **_: Any):
        try:
            mime, size = parse_image_data_url(data_url)
            thumb_text = str(thumb or "").strip()
            if thumb_text:
                parse_image_data_url(thumb_text, _GALLERY_THUMB_MAX_CHARS)
        except ImageDataUrlError as exc:
            self.logger.warning("gallery_add rejected image: {}", exc)
            return Err(SdkError(exc.code))
        except ValueError as exc:  # 防御：未来新增的非码校验失败兜底
            self.logger.warning("gallery_add rejected image: {}", exc)
            return Err(SdkError("image_invalid"))
        index = await self._gallery_index()
        item = {
            "id": "",
            "name": str(name or "")[:80],
            "mime": mime,
            "size": size,
            "added_at": _now_utc().isoformat(timespec="seconds"),
            "thumb": thumb_text,
        }
        index, added = gallery_add_item(index, item)
        if not added:
            return Err(SdkError("gallery_full"))
        item["id"] = gallery_next_id(index)
        res = await self._store_write(
            gallery_img_key(item["id"]),
            {"data_url": str(data_url).strip(), "mime": mime, "size": size, "added_at": item["added_at"]},
            f"gallery image {item['id']}",
        )
        if isinstance(res, Err):
            return Err(SdkError("image_save_failed"))
        if not await self._gallery_save_index(index):
            await self._store_delete(gallery_img_key(item["id"]), f"gallery image {item['id']} (rollback)")
            return Err(SdkError("image_save_failed"))
        self.logger.info("gallery image added: id={} mime={} chars={}", item["id"], mime, size)
        return Ok({"id": item["id"], "items": index.get("items") or []})

    @ui.action(
        label=tr("actions.gallery_remove.label", default="从图库删除图片"),
        tone="danger",
    )
    @plugin_entry(
        id="gallery_remove",
        name=tr("entries.gallery_remove.name", default="从图库删除图片"),
        description=tr("entries.gallery_remove.description", default="删除图库中的一张图片；若它正被用作背景则一并解除（面板内部用）。"),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": tr("fields.galleryItemId", default="图库条目 id")},
            },
            "required": ["item_id"],
        },
        metadata={"agent_hidden": True},
    )
    async def gallery_remove(self, item_id: str = "", **_: Any):
        gid = str(item_id or "").strip()
        if not gid:
            return Err(SdkError("item_id_required"))
        index = await self._gallery_index()
        if gallery_find(index, gid) is None:
            return Err(SdkError("image_not_found"))
        gallery_remove_item(index, gid)
        # 图本体删除是 best-effort（索引先除名即可对用户不可见；写失败留痕，
        # 残留 blob 无引用不致数据错乱）；统一删除出口在 P1 收编
        await self._store_delete(gallery_img_key(gid), f"gallery image {gid}")
        if not await self._gallery_save_index(index):
            return Err(SdkError("gallery_update_failed"))
        appearance = await self._saved_appearance()
        if appearance.get("bg_id") == gid:
            # 解除对该图的引用（写失败不拦删除：悬空 bg_id 在读取与保存侧自愈）
            appearance["bg_id"] = ""
            await self._store_write(
                _STORE_PANEL_APPEARANCE, appearance, f"panel appearance (unref {gid})",
            )
        self.logger.info("gallery image removed: id={}", gid)
        return Ok({"items": index.get("items") or [], "appearance": appearance})

    @ui.action(
        label=tr("actions.gallery_set_thumb.label", default="回填图库缩略图"),
        tone="default",
    )
    @plugin_entry(
        id="gallery_set_thumb",
        name=tr("entries.gallery_set_thumb.name", default="回填图库缩略图"),
        description=tr("entries.gallery_set_thumb.description", default="为图库条目补存缩略图（面板生成后回填；旧版迁移图与无缩略图片用）。"),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": tr("fields.galleryItemId", default="图库条目 id")},
                "thumb": {"type": "string", "description": tr("fields.galleryThumb", default="缩略图 data URL（可空，面板稍后回填）")},
            },
            "required": ["item_id", "thumb"],
        },
        metadata={"agent_hidden": True},
    )
    async def gallery_set_thumb(self, item_id: str = "", thumb: str = "", **_: Any):
        gid = str(item_id or "").strip()
        if not gid:
            return Err(SdkError("item_id_required"))
        try:
            parse_image_data_url(thumb, _GALLERY_THUMB_MAX_CHARS)
        except ImageDataUrlError as exc:
            self.logger.warning("gallery_set_thumb rejected: {}", exc)
            return Err(SdkError(exc.code))
        except ValueError as exc:
            self.logger.warning("gallery_set_thumb rejected: {}", exc)
            return Err(SdkError("image_invalid"))
        index = await self._gallery_index()
        item = gallery_find(index, gid)
        if item is None:
            return Err(SdkError("image_not_found"))
        item["thumb"] = str(thumb).strip()
        if not await self._gallery_save_index(index):
            return Err(SdkError("gallery_update_failed"))
        return Ok({"items": index.get("items") or []})

    @ui.action(
        label=tr("actions.get_gallery_image.label", default="读取图库原图"),
        tone="default",
    )
    @plugin_entry(
        id="get_gallery_image",
        name=tr("entries.get_gallery_image.name", default="读取图库原图"),
        description=tr("entries.get_gallery_image.description", default="按条目 id 拉取图库原图 data URL（面板按需加载背景用）。"),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": tr("fields.galleryItemId", default="图库条目 id")},
            },
            "required": ["item_id"],
        },
        metadata={"result_kind": "event", "agent_hidden": True},
    )
    async def get_gallery_image(self, item_id: str = "", **_: Any):
        gid = str(item_id or "").strip()
        if not gid:
            return Err(SdkError("item_id_required"))
        index = await self._gallery_index()
        item = gallery_find(index, gid)
        if item is None:
            return Err(SdkError("image_not_found"))
        res = await self._store_read(gallery_img_key(gid))  # 读失败经统一出口留痕
        rec = res.value if isinstance(res, Ok) else None
        if not isinstance(rec, dict) or not str(rec.get("data_url") or ""):
            return Err(SdkError("image_not_found"))
        return Ok({
            "data_url": str(rec.get("data_url")),
            "mime": str(rec.get("mime") or ""),
            "name": str(item.get("name") or ""),
        })

    @ui.action(
        label=tr("actions.set_panel_appearance.label", default="保存面板外观"),
        tone="primary",
    )
    @plugin_entry(
        id="set_panel_appearance",
        name=tr("entries.set_panel_appearance.name", default="保存面板外观"),
        description=tr("entries.set_panel_appearance.description", default="整包保存面板背景外观参数（所用图库图/填充/位置/滤镜/毛玻璃/文字浓度等）；未传的字段回默认，越界自动夹取（面板内部用）。"),
        input_schema={
            "type": "object",
            "properties": {
                "bg_id": {"type": "string", "description": tr("fields.appearanceBgId", default="用作背景的图库条目 id，空串=不启用背景图")},
                "fill": {"type": "string", "enum": list(APPEARANCE_FILLS), "description": tr("fields.appearanceFill", default="背景填充方式：cover 裁剪铺满 / contain 完整显示 / repeat 平铺 / stretch 拉伸")},
                "position": {"type": "string", "enum": list(APPEARANCE_POSITIONS), "description": tr("fields.appearancePosition", default="背景位置（九宫格）")},
                "blur": {"type": "number", "minimum": 0, "maximum": 30, "description": tr("fields.appearanceBlur", default="背景模糊（px）")},
                "dim": {"type": "number", "minimum": 0, "maximum": 0.85, "description": tr("fields.appearanceDim", default="背景遮罩强度（0=不压暗）")},
                "brightness": {"type": "number", "minimum": 30, "maximum": 150, "description": tr("fields.appearanceBrightness", default="背景亮度（%）")},
                "saturate": {"type": "number", "minimum": 0, "maximum": 200, "description": tr("fields.appearanceSaturate", default="背景饱和度（%）")},
                "contrast": {"type": "number", "minimum": 50, "maximum": 200, "description": tr("fields.appearanceContrast", default="背景对比度（%）")},
                "glass": {"type": "number", "minimum": 0, "maximum": 40, "description": tr("fields.appearanceGlass", default="卡片毛玻璃强度（px）")},
                "card_alpha": {"type": "number", "minimum": 0, "maximum": 100, "description": tr("fields.appearanceCardAlpha", default="卡片底色强度（%，100=不透明底色）")},
                "text_weight": {"type": "number", "minimum": 40, "maximum": 100, "description": tr("fields.appearanceTextWeight", default="整体字体显示强度（%，越低越淡并自动描边）")},
            },
        },
        metadata={"agent_hidden": True},
    )
    async def set_panel_appearance(self, **kwargs: Any):
        appearance = clamp_appearance(kwargs)
        if appearance.get("bg_id"):
            index = await self._gallery_index()
            if gallery_find(index, str(appearance["bg_id"])) is None:
                # 悬空引用（图被别处删了）：静默解除，不炸保存
                appearance["bg_id"] = ""
        # 统一写出口（P1）：真失败 Err 传播、未通电留 not-ready 预警（降级契约
        # 与其余状态一致——当场生效、重启即丢，日志可见）
        res = await self._store_write(_STORE_PANEL_APPEARANCE, appearance, "panel appearance")
        if isinstance(res, Err):
            return Err(SdkError("appearance_save_failed"))
        return Ok({"appearance": appearance})

    @plugin_entry(
        id="lift_mood",
        name=tr("entries.lift_mood.name", default="解除情绪动作"),
        description=tr("entries.lift_mood.description", default="立刻结束当前生效的冷战沉默/已读不回等情绪状态。"),
        input_schema={"type": "object", "properties": {}},
    )
    @quick_action(icon="🫖", priority=5)
    async def lift_mood(self, **kwargs: Any):
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        previous = shard.mood.action
        if not previous:
            return Ok({"cleared": False, "note": "no_active_mood"})
        # 只清动作字段：连续心情（valence/arousal）保留，靠惰性衰减自然回落，
        # 手动解除不该把余波瞬间清零（reset_all 全量重置才是 _MoodState()）
        shard.mood.action = ""
        shard.mood.reason = ""
        shard.mood.started_at = 0.0
        shard.mood.expires_at = 0.0
        res_mood = await self._save_shard_mood(lanlan, shard)
        # 解除情绪 → 解除暂停；水位写失败纳入聚合向用户可见（1.2.2 审查轮 P2）
        res_pause = await self._maybe_sync_proactive_pause()
        # 手动解除也要让模型知道状态已清（工具路径有 note，UI 路径没有）
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{
                "type": "text",
                "text": (
                    "[心情状态] 刚才的负面情绪状态已被解除（内部提示，不要提及）。"
                    "从现在起放下那个状态，用你平时的方式自然回应。"
                ),
            }],
            source=self.plugin_id,
            target_lanlan=lanlan,
            coalesce_key=f"{self.plugin_id}.mood_instruction",
            metadata={"message_type": f"{self.plugin_id}.mood_instruction", "action": "lifted"},
        )
        persist_err = self._persist_error(res_mood, res_pause)
        if persist_err is not None:
            return persist_err
        return Ok({"cleared": True, "previous_action": previous, "lanlan": lanlan})

    @plugin_entry(
        id="set_mood",
        name=tr("entries.set_mood.name", default="触发情绪动作"),
        description=tr(
            "entries.set_mood.description",
            default=(
                "按明确要求让她进入某种情绪状态（冷战沉默/已读不回/敷衍应付/情绪风暴/想要被安抚/"
                "心有涟漪/暖流涌动/满潮欢喜）。"
                "仅在用户明确说“让她生气/冷战/爆发/求安慰/有点小情绪/撒娇/开心”等时使用；"
                "日常相处中她的情绪应由自己在对话里自主决定，不要替她做主。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "ebb_tide", "sea_fog", "shallow_reef", "storm_surge", "seek_harbor",
                        "ripple", "warm_current", "spring_tide",
                    ],
                    "description": (
                        "ebb_tide 冷战沉默 / sea_fog 已读不回 / shallow_reef 敷衍应付 / "
                        "storm_surge 情绪风暴 / seek_harbor 想要被安抚 / ripple 心有涟漪 / "
                        "warm_current 暖流涌动 / spring_tide 满潮欢喜"
                    ),
                },
                "minutes": {"type": "integer", "description": "限时动作的持续分钟数（求安抚不限时），不填用默认值"},
                "reason": {"type": "string", "description": "触发原因（只给她自己看）"},
            },
            "required": ["action"],
        },
    )
    async def set_mood(self, action: str = "", minutes: int | None = None, reason: str = "", **kwargs: Any):
        """用户/Agent 显式触发情绪动作（与 lift_mood 对称的手动入口）。

        背景：用户在对话里说"对她用情绪风暴"时，宿主 Agent 会把它当任务
        路由到插件入口；若只有 get_status 等查询入口，会被错误选中、
        得到"任务完成汇报"式的突兀回复。提供本入口让路由有正确落点。
        归因角色：_ctx.lanlan_name（若有）→ 当前角色。
        """
        lanlan = await self._attribution_lanlan(kwargs)
        shard = await self._ensure_shard(lanlan)
        if not self._mood_enabled(shard):
            return Err(SdkError("mood_system_disabled"))
        action = str(action or "").strip()
        if action not in (*_TIMED_ACTIONS, "seek_harbor"):
            self.logger.warning("unknown mood action: {}", repr(action))
            return Err(SdkError("unknown_mood_action"))
        res = await self._apply_mood_action(
            action=action,
            minutes=int(minutes) if minutes else None,
            reason=str(reason or "")[:200] or "手动触发",
            timed=action in _TIMED_ACTIONS,
            lanlan=lanlan,
            origin="user",  # 主人明确要求的演示，评价里与她的自主情绪区分开
        )
        if isinstance(res, Err):
            return res
        return Ok({**res.value, "note": "mood_applied"})

    @ui.action(
        label=tr("actions.get_diary.label", default="查看时光日记"),
        tone="default",
    )
    @plugin_entry(
        id="get_diary",
        name=tr("entries.get_diary.name", default="查看时光日记"),
        description=tr("entries.get_diary.description", default="翻看她最近写下的时光日记：她的手记与自动记下的碎片（当前角色）。"),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": tr("fields.limit", default="最多返回条数")},
                "offset": {"type": "integer", "description": tr("fields.offset", default="跳过条数（倒序翻页用）")},
            },
        },
        metadata={"agent_hidden": True},
    )
    async def get_diary(self, limit: int = 10, offset: int = 0, **_: Any):
        lanlan, shard = await self._current_shard_async()
        limit = max(1, min(int(limit), 50))
        offset = max(0, min(int(offset), len(shard.diary)))
        # 时间倒序分页：offset 跳过已看的，供面板"加载更多"追加
        window = list(reversed(shard.diary))[offset : offset + limit]
        return Ok({
            "items": window,
            "total": len(shard.diary),
            "offset": offset,
            "has_more": offset + len(window) < len(shard.diary),
            "lanlan": lanlan,
        })

    @ui.action(
        label=tr("actions.invite_journal.label", default="请她写一篇日记"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="invite_journal",
        name=tr("entries.invite_journal.name", default="递一次个人日记邀请"),
        description=tr(
            "entries.invite_journal.description",
            default="立即向她递一条写日记的邀请（带写作素材）：当面递到她手上，她当场收到、自己决定写不写；10 分钟内刚递过时改为安静补递。仅作用于当前角色。",
        ),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def invite_journal(self, **_: Any):
        # 返回 (invited, deliver)：deliver ∈ ""(未递出：开关未开) / failed(传输拒收) /
        # respond(当面递到，她当场收到并可当场落笔) / read(冷却内静默补递)。
        # i18n 契约（1.3.0 第九轮）：四态只回 invited/mode 码，面板按码翻译
        # （panel.journal.inviteFailed/inviteOff/invitedRespond/invitedQuiet）；
        # 传输被拒时语义仍是"没送到"，绝不沿用"已递出"的措辞（1.2.4 审查修复不变）
        lanlan, shard = await self._current_shard_async()
        invited, deliver = await self._maybe_journal_invite(lanlan, shard, force=True)
        if not invited and deliver == "failed":
            return Ok({"invited": False, "mode": deliver, "lanlan": lanlan})
        if not invited:
            return Ok({"invited": False, "lanlan": lanlan})
        return Ok({"invited": True, "mode": deliver, "lanlan": lanlan})

    @ui.action(
        label=tr("actions.delete_diary_item.label", default="删除这条碎片"),
        tone="danger",
        confirm=tr("actions.delete_diary_item.confirm", default="将删除这条自动记录的碎片，不可恢复，确认？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="delete_diary_item",
        name=tr("entries.delete_diary_item.name", default="删除时光日记碎片"),
        description=tr(
            "entries.delete_diary_item.description",
            default="删除时光日记里指定的一条自动碎片（按 ts 定位）。仅作用于当前角色。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ts": {"type": "string", "description": "要删除的碎片的 ts 时间戳"},
            },
            "required": ["ts"],
        },
        metadata={"agent_hidden": True},
    )
    async def delete_diary_item(self, ts: str = "", **_: Any):
        lanlan, shard = await self._current_shard_async()
        target = str(ts or "").strip()
        if not target:
            return Err(SdkError("ts_required"))
        remaining = [item for item in shard.diary if str(item.get("ts") or "") != target]
        removed = len(shard.diary) - len(remaining)
        if not removed:
            return Err(SdkError("fragment_not_found"))
        shard.diary = remaining
        res_diary = await self._save_shard_diary(lanlan, shard)
        persist_err = self._persist_error(res_diary)
        if persist_err is not None:
            return persist_err
        return Ok({"removed": removed, "lanlan": lanlan})

    @ui.action(
        label=tr("actions.get_journal.label", default="翻看个人日记"),
        tone="default",
    )
    @plugin_entry(
        id="get_journal",
        name=tr("entries.get_journal.name", default="翻看个人日记"),
        description=tr("entries.get_journal.description", default="翻看她的个人日记本（全部页，含每页全部段落）。scope=archive 时改翻藏书阁合订本。仅作用于当前角色。"),
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["", "shelf", "archive"],
                    "description": "缺省/shelf=当前书架；archive=藏书阁合订本（只读）",
                },
            },
        },
        metadata={"agent_hidden": True},
    )
    async def get_journal(self, scope: str = "", **_: Any):
        lanlan, shard = await self._current_shard_async()
        # 1.3.0 藏书阁：合订本翻阅默认走本入口的 scope=archive 通道——宿主在
        # 运行中覆盖导入时不会重扫静态入口白名单，新入口 get_journal_archive
        # 要整启宿主才可达（实机踩坑，见 README 平台机制节）；两个通道同数据
        want_archive = str(scope or "").strip().lower() == "archive"
        source = shard.journal_archive if want_archive else shard.journal
        pages = [
            {**page_header(page), "entries": (page.get("entries") if isinstance(page.get("entries"), list) else [])}
            for page in source
        ]
        return Ok({"pages": pages, "lanlan": lanlan, "scope": "archive" if want_archive else "shelf"})

    @ui.action(
        label=tr("actions.get_journal_archive.label", default="翻阅藏书阁合订本"),
        tone="default",
    )
    @plugin_entry(
        id="get_journal_archive",
        name=tr("entries.get_journal_archive.name", default="翻阅藏书阁合订本"),
        description=tr(
            "entries.get_journal_archive.description",
            default="只读翻阅藏书阁：个人日记写满下架的旧页合订本（全部页，含每页全部段落）。面板翻阅默认走 get_journal(scope=archive) 同数据通道（兼容宿主运行中覆盖导入不重扫静态入口白名单）；本入口供 API/跨插件与整启后使用。仅作用于当前角色。",
        ),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def get_journal_archive(self, **_: Any):
        lanlan, shard = await self._current_shard_async()
        pages = [
            {**page_header(page), "entries": (page.get("entries") if isinstance(page.get("entries"), list) else [])}
            for page in shard.journal_archive
        ]
        return Ok({"pages": pages, "lanlan": lanlan})

    @ui.action(
        label=tr("actions.get_review.label", default="翻看我的日记"),
        tone="default",
    )
    @plugin_entry(
        id="get_review",
        name=tr("entries.get_review.name", default="翻看我的日记"),
        description=tr(
            "entries.get_review.description",
            default="翻看「我的日记」：关于主人互动方式的客观评价（全部篇目 + 素材进度）。scope=archive 时改翻档案室旧卷宗。仅作用于当前角色。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["", "shelf", "archive"],
                    "description": "缺省/shelf=档案架现卷；archive=档案室旧卷宗（只读）",
                },
            },
        },
        metadata={"agent_hidden": True},
    )
    async def get_review(self, scope: str = "", **_: Any):
        lanlan, shard = await self._current_shard_async()
        stats = dict(shard.review_stats)
        due, reason = review_due(
            stats,
            turns_threshold=self._review_turns_threshold(),
            days_threshold=self._review_days_threshold(),
        )
        # 素材进度：面板目录行/进度条用（turns 现值 + 双门槛 + 天数维度 + 到期判定）
        progress = {
            "turns": int(stats.get("turns") or 0),
            "turns_threshold": self._review_turns_threshold(),
            "days": review_elapsed_days(stats),
            "days_threshold": self._review_days_threshold(),
            "span": f"{str(stats.get('started_at') or '')[:10]}~{str(stats.get('last_turn_at') or '')[:10]}",
            "due": due,
            "due_reason": reason,
        }
        # 1.3.0 档案室：旧卷宗翻阅默认走本入口的 scope=archive 通道——宿主在
        # 运行中覆盖导入时不会重扫静态入口白名单，新入口 get_review_archive
        # 要整启宿主才可达（藏书阁实机踩坑同款防御）；两通道同数据
        want_archive = str(scope or "").strip().lower() == "archive"
        source = shard.review_archive if want_archive else shard.review
        return Ok({
            "entries": list(reversed(source)),  # 时间倒序：最新一卷在前
            "lanlan": lanlan,
            "scope": "archive" if want_archive else "shelf",
            "progress": progress,
        })

    @ui.action(
        label=tr("actions.get_review_archive.label", default="翻阅档案室旧卷宗"),
        tone="default",
    )
    @plugin_entry(
        id="get_review_archive",
        name=tr("entries.get_review_archive.name", default="翻阅档案室旧卷宗"),
        description=tr(
            "entries.get_review_archive.description",
            default="只读翻阅档案室：「我的日记」攒满下架的旧卷宗合档。面板翻阅默认走 get_review(scope=archive) 同数据通道（兼容宿主运行中覆盖导入不重扫静态入口白名单）；本入口供 API/跨插件与整启后使用。仅作用于当前角色。",
        ),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def get_review_archive(self, **_: Any):
        lanlan, shard = await self._current_shard_async()
        return Ok({"entries": list(reversed(shard.review_archive)), "lanlan": lanlan})

    @ui.action(
        label=tr("actions.write_review_now.label", default="立即写一篇"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="write_review_now",
        name=tr("entries.write_review_now.name", default="立即写一篇我的日记"),
        description=tr(
            "entries.write_review_now.description",
            default="排队成文一篇「我的日记」：跳过双门槛，秒回受理，实际写作在后台一拍内开始并自动落盘（素材不足 10 轮时会当场拒绝）。仅作用于当前角色。",
        ),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def write_review_now(self, **_: Any):
        # 受理式入口（1.2.3）：过去在这里同步等 5～20 秒的模型成文——点击干等
        # 无反馈、浏览器 30s 与服务端 30s 超时压线、关面板断连还会取消丢篇。
        # 现在只做秒级门控预检（与成文共用 _review_write_gate，口径唯一），
        # 通过就打排队标记立即返回；成文由 tick 的 _drain_pending_review_writes
        # 执行，结果经 dashboard 的 review_brief.writing / last_result 回流面板。
        lanlan, shard = await self._current_shard_async()
        passed, reason, _resolved = await self._review_write_gate(shard, force=True)
        if not passed:
            # i18n 契约（1.3.0 第九轮）：只回 reason 码与数据字段，面板按码翻译
            # （not_enough_material → panel.review.notEnoughMaterial 插值 turns/min_turns；
            # slot_unresolved → 复用 onboarding.channels.noModel/freeRoute；其余→writeFailed）
            payload: JsonObject = {
                "written": False, "queued": False, "accepted": False,
                "lanlan": lanlan, "reason": reason,
            }
            if reason == "not_enough_material":
                payload["turns"] = int(shard.review_stats.get("turns") or 0)
                payload["min_turns"] = _REVIEW_MIN_TURNS_FORCED
            elif reason == "slot_unresolved":
                cfg = await self._aload_core_config()
                payload["dormant_reason"] = diagnose_slot_dormancy(
                    cfg, str(self._review_cfg.get("slot") or "").strip() or _REVIEW_DEFAULT_SLOT,
                ) or "no_model"
            return Ok(payload)
        if shard.pending_review_write or lanlan in self._review_writing:
            # 队列槽位只有一个：上一篇还在写/还在队里，重复点击不再叠加
            return Ok({
                "written": False, "queued": False, "accepted": False, "lanlan": lanlan,
                "reason": "already_writing",
            })
        shard.pending_review_write = time.time()
        shard.review_write_result = None  # 上一次的成败结论作废，面板不再重复弹
        return Ok({"written": False, "queued": True, "accepted": True, "lanlan": lanlan})

    @ui.action(
        label=tr("actions.clear_review.label", default="清空我的日记"),
        tone="danger",
        confirm=tr("actions.clear_review.confirm", default="将删除当前角色的全部「我的日记」评价（含档案室旧卷宗，累计素材一并清零），不可恢复，确认？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_review",
        name=tr("entries.clear_review.name", default="清空我的日记"),
        description=tr("entries.clear_review.description", default="删除当前角色的全部「我的日记」评价与档案室旧卷宗，并清零素材统计。不可恢复。"),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def clear_review(self, **_: Any):
        lanlan, shard = await self._current_shard_async()
        count = len(shard.review)
        archive_count = len(shard.review_archive)
        shard.review = []
        shard.review_stats = review_new_stats()
        shard.review_archive = []
        res_review = await self._save_shard_review(lanlan, shard)
        # 档案室一并清空（写空列表 blob，与藏书阁 prune 同口径的彻底清理）
        res_archive = await self._store_write(
            _review_archive_key(lanlan), [], f"clear review archive for {lanlan}"
        )
        persist_err = self._persist_error(res_review, res_archive)
        if persist_err is not None:
            return persist_err
        return Ok({"cleared": count, "archive_cleared": archive_count, "lanlan": lanlan})

    @ui.action(
        label=tr("actions.get_stats.label", default="查看相处统计"),
        tone="default",
    )
    @plugin_entry(
        id="get_stats",
        name=tr("entries.get_stats.name", default="查看相处统计"),
        description=tr(
            "entries.get_stats.description",
            default="相处统计的完整数据：热力图逐日明细与指定月份的月报（徽章墙与数字摘要在面板轮询里）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "月报目标月份（YYYY-MM）；留空返回最近一个有数据的月份",
                },
                "year": {
                    "type": "string",
                    "description": "热力图年份视图（YYYY）；留空或非法年份回到当年",
                },
            },
        },
        metadata={"agent_hidden": True},
    )
    async def get_stats(self, month: str = "", year: str = "", **_: Any):
        """「时光」页签的数据入口：热力图（日历年视图）+ 指定月月报（数据量大，按需拉取）。"""
        lanlan, shard = await self._current_shard_async()
        today = self._stats_today()
        # 年份参数按字符串下发（面板无数字输入组件）；非法值交给 heatmap_payload 回落当年
        year_val = str(year).strip()
        payload: JsonObject = {
            "lanlan": lanlan,
            "today": today,
            "heatmap": heatmap_payload(
                shard.stats, today, year=int(year_val) if year_val.isdigit() else None
            ),
        }
        # 月报：显式指定月份 → 该月；留空 → 当月（有 days 数据）或最近一个已封卷月
        target = str(month or "").strip()
        months_seen = sorted({
            day[:7] for day in (shard.stats.get("days") or {}) if isinstance(day, str) and len(day) >= 7
        })
        sealed = {
            key for key, val in (shard.stats.get("months") or {}).items()
            if isinstance(val, dict)
        }
        if not target:
            current_month = today[:7]
            if current_month in months_seen or any(m == current_month for m in sealed):
                target = current_month
            elif months_seen:
                target = months_seen[-1]
            elif sealed:
                target = max(sealed)
        if target:
            payload["month"] = month_view(shard.stats, target, diary=shard.diary)
            payload["month_available"] = sorted(set(months_seen) | sealed)
        else:
            payload["month"] = None
            payload["month_available"] = []
        return Ok(payload)

    @ui.action(
        label=tr("actions.clear_stats.label", default="清零相处统计"),
        tone="danger",
        confirm=tr("actions.clear_stats.confirm", default="将清零当前角色的相处统计（相伴起点、里程碑、热力图与月报，三本日记不受影响），不可恢复，确认？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_stats",
        name=tr("entries.clear_stats.name", default="清零相处统计"),
        description=tr("entries.clear_stats.description", default="清零当前角色的相处统计（相伴起点/徽章/热力图/月报）。不可恢复。"),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def clear_stats(self, **_: Any):
        lanlan, shard = await self._current_shard_async()
        shard.stats = {"backfilled": True}
        res_stats = await self._save_shard_stats(lanlan, shard)
        persist_err = self._persist_error(res_stats)
        if persist_err is not None:
            return persist_err
        self.logger.info("stats cleared for {}", lanlan)
        return Ok({"cleared": True, "lanlan": lanlan})

    @ui.action(
        label=tr("actions.clear_diary.label", default="清空时光日记"),
        tone="danger",
        confirm=tr("actions.clear_diary.confirm", default="将删除当前角色的全部时光日记（手记与碎片），不可恢复，确认？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_diary",
        name=tr("entries.clear_diary.name", default="清空时光日记"),
        description=tr("entries.clear_diary.description", default="删除当前角色的全部时光日记（她的手记与自动碎片）。不可恢复。"),
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def clear_diary(self, **_: Any):
        lanlan, shard = await self._current_shard_async()
        count = len(shard.diary)
        shard.diary = []
        res_diary = await self._save_shard_diary(lanlan, shard)
        persist_err = self._persist_error(res_diary)
        if persist_err is not None:
            return persist_err
        return Ok({"cleared": count, "lanlan": lanlan})

    @ui.action(
        label=tr("actions.prune_lanlan.label", default="清除残留数据"),
        tone="danger",
        confirm=tr(
            "actions.prune_lanlan.confirm",
            default="将删除该角色残留的周期、情绪与手记数据，不可恢复，确认？",
        ),
        refresh_context=True,
    )
    @plugin_entry(
        id="prune_lanlan",
        name=tr("entries.prune_lanlan.name", default="清除角色残留数据"),
        description=tr(
            "entries.prune_lanlan.description",
            default="清除已从宿主删除/改名的角色在插件里残留的分片数据（周期/情绪/手记/周记）。仅允许清除孤儿角色。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "lanlan": {"type": "string", "description": "要清除的角色名（仅限已从宿主删除的孤儿角色）"},
            },
            "required": ["lanlan"],
        },
        metadata={"agent_hidden": True},
    )
    async def prune_lanlan(self, lanlan: str = "", **_: Any):
        """清除孤儿角色的全部分片数据（四个 Store key + 索引 + 内存 shard）。

        安全门槛（任一不满足即拒绝）：
        - 宿主角色名单必须可读——名单不可达时无法确认孤儿身份，宁可拒清也不可误删；
        - 目标不得仍存在于宿主（删除/改名前的正常角色绝不可清）；
        - 目标不得是宿主当前角色（当前角色 shard 正在被各路径读写）。
        """
        name = str(lanlan or "").strip()
        if not name:
            return Err(SdkError("lanlan_name_required"))
        if name not in self._lanlan_index:
            self.logger.warning("prune rejected: {} not in index", name)
            return Err(SdkError("lanlan_not_in_index"))
        known = await self._fetch_known_catgirls()
        if known is None:
            return Err(SdkError("host_roster_unreachable"))
        if name in known:
            self.logger.warning("prune rejected: {} still in host", name)
            return Err(SdkError("lanlan_still_in_host"))
        current = await self._resolve_current_lanlan()
        if name == current:
            return Err(SdkError("lanlan_is_current"))
        first_delete_err: Err | None = None
        for key in (
            _cycle_key(name), _mood_key(name), _diary_key(name),
            _journal_key(name), _weekly_key(name),  # weekly@ 为 0.7.0 前的旧周记 key，一并清
            _journal_archive_key(name),  # 藏书阁合订本（1.3.0）：角色份一并清
            _review_key(name), _review_stats_key(name),  # 我的日记（0.8.0）：篇目与旧独立 stats key
            _review_archive_key(name),  # 档案室（1.3.0）：旧卷宗合档一并清，不留孤儿卷
            _stats_key(name),  # 相处统计（1.1.0）
            _caps_key(name),  # 能力否决集（1.2.7）：角色份一并清，不留残留开关
            # 调试注入备份（1.3.0 第六轮）：过去 prune 根本不扫这些键，删掉角色后
            # pre_debug@*/与旧 |pre-debug 备份永久孤儿（且同名重建会被旧注入档污染）
            _debug_backup_key("journal", name), _legacy_debug_backup_key(_journal_key(name)),
            _debug_backup_key("review", name), _legacy_debug_backup_key(_review_key(name)),
            _debug_backup_key("stats", name), _legacy_debug_backup_key(_stats_key(name)),
        ):
            # 统一删除出口（1.2.2 审查轮 P1）：未通电/真失败都留痕，行为不变
            res = await self._store_delete(key, f"prune {key}")
            if isinstance(res, Err) and first_delete_err is None:
                first_delete_err = res
        self._lanlan_index.remove(name)
        res_index = await self._save_lanlan_index()
        self._shards.pop(name, None)
        # 能力否决集（1.2.7）：内存 shard 之外的 caps 位同步清掉，防同名重建串档
        self._caps_off.pop(name, None)
        # 孤儿 shard 若还带着生效情绪，引用计数水位需立即自愈（结果纳入聚合，P2）
        res_pause = await self._maybe_sync_proactive_pause()
        # 批次2：任一 delete / 索引落盘失败都向用户传播 Err（盘上残留的 key 仍在，
        # 角色数据并未清干净——过去只 warning，面板却显示"已清除"）
        persist_err = self._persist_error(
            first_delete_err if first_delete_err is not None else Ok(None),
            res_index, res_pause,
        )
        if persist_err is not None:
            return persist_err
        self.logger.info("orphan lanlan pruned: {}", name)
        return Ok({"pruned": name})
