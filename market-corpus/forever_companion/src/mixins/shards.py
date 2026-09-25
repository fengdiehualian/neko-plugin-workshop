"""分片基建与配置状态：shard 存取 / 当前角色解析 / 周期参数 / 落盘 / legacy 迁移。

拆分自 __init__.py（方法逐字搬移，仅换文件与导入）；经 Mixin 组合回
ForeverCompanionPlugin。含：_get_shard/_ensure_shard 的惰性载入、
_resolve_current_lanlan 的两级回落（宿主 HTTP → ctx 粘滞缓存）、
_refresh_config 的 settings 覆盖层合并、_save_shard_* 的分片持久化、
旧版单角色数据的一次性迁移，以及给测试保留的属性代理
（_mood_state/_diary/_last_injected_* 等直读当前 shard 字段）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from plugin.sdk.plugin import Err, Ok, Result, SdkError

from ..core.cycle import (
    compute_phase_state,
    derive_cycle_params,
    parse_anchor_date,
    randomized_default_anchor,
)
from ..core.journal import journal_entries, migrate_weekly_to_pages
from ..core.onboarding import norm_guide_record
from ..core.state import (
    _CURRENT_LANLAN_CACHE_TTL,
    _DIARY_MAX_ENTRIES,
    _JOURNAL_ARCHIVE_MAX_PAGES,
    _JOURNAL_MAX_PAGES,
    _KNOWN_CATGIRLS_CACHE_TTL,
    _REVIEW_ARCHIVE_MAX_ENTRIES,
    _REVIEW_MAX_ENTRIES,
    _STORE_CAPS_GLOBAL,
    _STORE_CYCLE,
    _STORE_DIARY,
    _STORE_GUIDE,
    _STORE_LANLAN_INDEX,
    _STORE_MOOD,
    _STORE_PROACTIVE,
    _STORE_SETTINGS,
    _cfg_section,
    _cycle_key,
    _diary_key,
    _journal_archive_key,
    _journal_key,
    _LanlanShard,
    _mood_key,
    _MoodState,
    _parse_iso_ts,
    _review_archive_key,
    _review_key,
    _review_stats_key,
    _snapshot_proactive,
    _stats_key,
    _weekly_key,
)
from ..core.stats import backfill_day

JsonObject = dict[str, Any]

# Store 就绪探测：宿主在构造插件实例时 effective config 尚未就位，SDK 会把
# PluginStore 构造成 enabled=False；disabled 态下 get 静默返回 default、set 静默
# 丢弃（都不报错），于是本插件全部持久状态在启动读时"看起来都是空的"。所以
# _load_state 之前先探测/唤醒。重试次数、单次读超时与间隔都刻意很小：startup 是宿主
# 同步等待的拉起路径（[plugin_runtime].timeout 有上限），探测绝不能把它拖到超时。
_STORE_READY_ATTEMPTS = 3
_STORE_READY_RETRY_S = 0.2
_STORE_READY_PROBE_TIMEOUT_S = 2.0


class ShardsMixin:
    """per-lanlan 状态分片：存取、当前角色解析、配置覆盖层、持久化。"""

    # ==========================================
    # 分片基建：shard 存取 / 当前角色解析
    # ==========================================

    def _get_shard(self, lanlan: str) -> _LanlanShard:
        """同步取 shard：不存在则建空壳（loaded=False，待 _ensure_shard 从 Store 载入）。

        同步路径（属性代理、纯计算）只允许读；任何写入前必须先经过 _ensure_shard。
        """
        shard = self._shards.get(lanlan)
        if shard is None:
            shard = self._shards[lanlan] = _LanlanShard()
        shard.lanlan = lanlan  # 1.2.7：能力中心按角色解析否决集需 shard 自报家门
        return shard

    async def _ensure_shard(self, lanlan: str) -> _LanlanShard:
        """确保角色 shard 已从 Store 载入，并登记进 lanlan_index（面板只读角色列表数据源）。"""
        # 中途通电门控（1.2.2）：载入不可信但 store 现已回电——先把幻影分片换成
        # 盘上真实数据，再放行本次写入。_load_state 没读到真数据的会话里，内存
        # 分片是从"空"起步的幻影、改动又从未落过盘；store 醒来后任何一条单路
        # 整包覆写（面板操作/消息驱动落盘）都会吃掉盘上真实历史。v1.2.1 只把
        # shutdown 整体回写挡在门外，这条路是剩下的覆写入口。所有写路径（入口、
        # tick、工具归因）都先过本方法，挂这里即全覆盖。
        if not self._state_trusted and not self._retrusting and self._store_ready:
            await self._retrust_state()
        # 能力否决集（1.2.7）：首次接触该角色时载入（幂等：载入过即命中键）。
        # 不挂在 shard.loaded 早退之后：旧版迁移路径会直接把 shard 标 loaded，
        # 挂 loaded 分支上会让迁移角色的 caps 永不落载
        if lanlan not in self._caps_off:
            await self._cap_load(lanlan)
        shard = self._get_shard(lanlan)
        if shard.loaded:
            return shard
        cycle_res = await self._store_read(_cycle_key(lanlan))
        if isinstance(cycle_res, Ok) and isinstance(cycle_res.value, dict):
            shard.cycle = dict(cycle_res.value)
        mood_res = await self._store_read(_mood_key(lanlan))
        if isinstance(mood_res, Ok):
            shard.mood = _MoodState.from_mapping(mood_res.value)
        diary_res = await self._store_read(_diary_key(lanlan))
        if isinstance(diary_res, Ok) and isinstance(diary_res.value, list):
            shard.diary = [dict(item) for item in diary_res.value if isinstance(item, dict)]
        # 个人日记（0.7.0）：journal@ 优先；缺失且存在旧版 weekly@ 时一次性迁移
        # （每条周记成为独立一页，legacy 标记），旧 key 原样保留作备份不再写入
        journal_res = await self._store_read(_journal_key(lanlan))
        if isinstance(journal_res, Ok) and isinstance(journal_res.value, list):
            shard.journal = [dict(item) for item in journal_res.value if isinstance(item, dict)]
        else:
            weekly_res = await self._store_read(_weekly_key(lanlan))
            if isinstance(weekly_res, Ok) and isinstance(weekly_res.value, list):
                legacy = [dict(item) for item in weekly_res.value if isinstance(item, dict)]
                shard.journal = migrate_weekly_to_pages(legacy)
                if shard.journal:
                    await self._save_shard_journal(lanlan, shard)
                    self.logger.info(
                        "weekly reviews migrated to journal pages for {} ({} pages)",
                        lanlan, len(shard.journal),
                    )
        # 藏书阁（1.3.0）：活架淘汰下来的旧页合订本，只读翻阅；与 journal@ 同法载入
        archive_res = await self._store_read(_journal_archive_key(lanlan))
        if isinstance(archive_res, Ok) and isinstance(archive_res.value, list):
            shard.journal_archive = [
                dict(item) for item in archive_res.value if isinstance(item, dict)
            ]
        # 我的日记（0.8.0）：已成文篇目 + 累计中的素材统计合并存一个 key
        #（成文时篇目追加与 stats 清零原子地一次写入；独立 stats key 仅作
        # 旧数据兼容读取，不再写入）
        review_res = await self._store_read(_review_key(lanlan))
        if isinstance(review_res, Ok) and isinstance(review_res.value, dict):
            raw_review = review_res.value
            shard.review = [dict(item) for item in raw_review.get("entries") or [] if isinstance(item, dict)]
            stats = raw_review.get("stats")
            shard.review_stats = dict(stats) if isinstance(stats, dict) else {}
        else:
            stats_res = await self._store_read(_review_stats_key(lanlan))
            if isinstance(stats_res, Ok) and isinstance(stats_res.value, dict):
                shard.review_stats = dict(stats_res.value)
        # 档案室（1.3.0）：活架淘汰下来的旧卷宗合档，只读翻阅；与藏书阁同法载入
        review_archive_res = await self._store_read(_review_archive_key(lanlan))
        if isinstance(review_archive_res, Ok) and isinstance(review_archive_res.value, list):
            shard.review_archive = [
                dict(item) for item in review_archive_res.value if isinstance(item, dict)
            ]
        # 相处统计（1.1.0）：stats@<角色>；首载时从三本日记时间戳一次性回填
        # "那天有互动"的活跃标记（轮数无法回填，只点亮天数让热力图有起点）
        stats_store_res = await self._store_read(_stats_key(lanlan))
        if isinstance(stats_store_res, Ok) and isinstance(stats_store_res.value, dict):
            shard.stats = dict(stats_store_res.value)
        if not shard.stats.get("backfilled"):
            for source in (shard.diary, journal_entries(shard.journal), [
                {"ts": item.get("ts"), "source": "review"}
                for item in shard.review
            ]):
                for item in source:
                    ts = _parse_iso_ts(item.get("ts") if isinstance(item, dict) else None)
                    if ts is not None:
                        shard.stats = backfill_day(
                            shard.stats,
                            ts.astimezone(self._stats_tz()).date().isoformat(),
                        )
            if str(shard.stats.get("first_seen") or ""):
                # first_seen 回填为最早互动痕迹（比"装版当天"更真实的相伴起点）
                earliest = min(
                    (
                        _parse_iso_ts(item.get("ts"))
                        for source in (shard.diary, journal_entries(shard.journal), shard.review)
                        for item in source
                        if _parse_iso_ts(item.get("ts") if isinstance(item, dict) else None) is not None
                    ),
                    default=None,
                )
                if earliest is not None:
                    shard.stats["first_seen"] = earliest.isoformat(timespec="seconds")
            shard.stats["backfilled"] = True
            # 载入不可信时不落盘：此刻 shard.stats 是从空 store 推出的幻影，
            # 写下去会把上一次真实积累的统计覆写成"今天刚开始"
            if self._state_trusted:
                await self._save_shard_stats(lanlan, shard)
        shard.loaded = True
        if lanlan not in self._lanlan_index:
            self._lanlan_index.append(lanlan)
            if self._state_trusted:
                await self._save_lanlan_index()
            self.logger.info("lanlan shard registered: {}", lanlan)
        return shard

    def _current_shard_name(self) -> str:
        """同步路径的角色名回落：最近一次解析结果 → default。"""
        return self._last_resolved_lanlan or "default"

    def _current_shard(self) -> _LanlanShard:
        return self._get_shard(self._current_shard_name())

    async def _resolve_current_lanlan(self) -> str:
        """解析宿主当前角色：HTTP current_catgirl（权威，15s 缓存）→ ctx._current_lanlan → "default"。

        ctx._current_lanlan 只是"最近触发角色"的粘滞缓存，不能当权威来源，
        仅作 HTTP 不可达时的兜底。角色名取档案名原文，不做 normalize。
        """
        name, ts = self._current_lanlan_cache
        if name and time.monotonic() - ts < _CURRENT_LANLAN_CACHE_TTL:
            return name
        resolved = ""
        payload = await self._proactive_http("GET", "/api/characters/current_catgirl")
        if isinstance(payload, dict):
            resolved = str(payload.get("current_catgirl") or "").strip()
        if not resolved:
            resolved = str(getattr(self.ctx, "_current_lanlan", "") or "").strip()
        if not resolved:
            resolved = "default"
        self._current_lanlan_cache = (resolved, time.monotonic())
        self._last_resolved_lanlan = resolved
        return resolved

    async def _fetch_known_catgirls(self) -> set[str] | None:
        """拉取宿主现存角色档案名单（GET /api/characters 响应里 ["猫娘"] dict 的 keys，档案名原文）。

        孤儿判定的唯一权威来源。带 Accept-Language: zh-CN 让宿主跳过 persona
        翻译开销（我们只取 key，不读人设正文）。成功结果缓存 60s；任何失败
        （不可达/结构异常）返回 None 表示"未知"且不缓存——调用方必须把未知
        按"非孤儿"处理，绝不可因宿主暂时不可达而误标/误删。
        """
        names, ts = self._known_catgirls_cache
        if names is not None and time.monotonic() - ts < _KNOWN_CATGIRLS_CACHE_TTL:
            return names
        payload = await self._proactive_http(
            "GET", "/api/characters", headers={"Accept-Language": "zh-CN"}
        )
        catgirls = payload.get("猫娘") if isinstance(payload, dict) else None
        if not isinstance(catgirls, dict):
            return None
        names = {str(k) for k in catgirls}
        self._known_catgirls_cache = (names, time.monotonic())
        return names

    async def _attribution_lanlan(self, kwargs: JsonObject) -> str:
        """工具/入口调用的归因角色：宿主传入的 ``_ctx.lanlan_name`` 优先。

        LLM 工具调用未证实一定携带 _ctx，故兜底为宿主当前角色。
        study_companion 同款归因模式。
        """
        ctx = kwargs.get("_ctx") if isinstance(kwargs, dict) else None
        name = ""
        if isinstance(ctx, dict):
            name = str(ctx.get("lanlan_name") or "").strip()
        elif ctx is not None:
            name = str(getattr(ctx, "lanlan_name", "") or "").strip()
        if name:
            return name
        return await self._resolve_current_lanlan()

    async def _current_shard_async(self) -> tuple[str, _LanlanShard]:
        """面板/用户入口的作用对象：始终跟随宿主当前角色（无手动选中态）。"""
        name = await self._resolve_current_lanlan()
        return name, await self._ensure_shard(name)

    # ---- 同步属性代理：指向"当前 shard"（最近解析 → default）----
    # 多角色路径一律显式传 shard；这些代理只服务无 lanlan 上下文的同步调用方与既有测试。

    @property
    def _mood_state(self) -> _MoodState:
        return self._current_shard().mood

    @_mood_state.setter
    def _mood_state(self, value: _MoodState) -> None:
        self._current_shard().mood = value

    @property
    def _diary(self) -> list[JsonObject]:
        return self._current_shard().diary

    @_diary.setter
    def _diary(self, value: list[JsonObject]) -> None:
        self._current_shard().diary = value

    @property
    def _last_injected_message_ts(self) -> float:
        return self._current_shard().last_injected_message_ts

    @_last_injected_message_ts.setter
    def _last_injected_message_ts(self, value: float) -> None:
        self._current_shard().last_injected_message_ts = value

    @property
    def _user_message_count_since_inject(self) -> int:
        return self._current_shard().user_message_count_since_inject

    @_user_message_count_since_inject.setter
    def _user_message_count_since_inject(self, value: int) -> None:
        self._current_shard().user_message_count_since_inject = value

    @property
    def _last_injected_whisper_key(self) -> str:
        return self._current_shard().last_injected_whisper_key

    @_last_injected_whisper_key.setter
    def _last_injected_whisper_key(self, value: str) -> None:
        self._current_shard().last_injected_whisper_key = value

    @property
    def _last_activity_context_key(self) -> str:
        return self._current_shard().last_activity_context_key

    @_last_activity_context_key.setter
    def _last_activity_context_key(self, value: str) -> None:
        self._current_shard().last_activity_context_key = value

    # ==========================================
    # 配置与状态
    # ==========================================

    async def _refresh_config(self) -> None:
        """加载全局配置：plugin.toml 默认值 + Store settings 覆盖层（权威）。

        这里只产全局配置（注入策略/提示词/时区等）；per-character 的开关/锚点/
        周期参数在 shard 上，读取时按"全局默认 ← shard.params 覆盖 ← shard.anchor_date"
        合成（见 _cycle_params）。Steam 环境下配置文件持久化常超时（4.5s 硬上限），
        因此用户在面板改过的设置以 PluginStore 为准，重启不丢。
        """
        raw = await self.config.dump(timeout=5.0)
        cfg = _cfg_section(raw)
        tide = _cfg_section(cfg.get("tide"))
        mood = _cfg_section(cfg.get("mood"))
        fragments = _cfg_section(cfg.get("fragments"))
        journal = _cfg_section(cfg.get("journal"))
        review = _cfg_section(cfg.get("review"))
        emotion_sense = _cfg_section(cfg.get("emotion_sense"))
        stats_cfg = _cfg_section(cfg.get("stats"))
        birthday_cfg = _cfg_section(cfg.get("birthday"))
        capabilities = _cfg_section(cfg.get("capabilities"))

        # Store 全局覆盖层：面板保存过的全局字段优先于 toml 默认
        tide.update(_cfg_section(self._settings_override.get("tide")))
        mood.update(_cfg_section(self._settings_override.get("mood")))
        fragments.update(_cfg_section(self._settings_override.get("fragments")))
        journal.update(_cfg_section(self._settings_override.get("journal")))
        review.update(_cfg_section(self._settings_override.get("review")))
        emotion_sense.update(_cfg_section(self._settings_override.get("emotion_sense")))
        stats_cfg.update(_cfg_section(self._settings_override.get("stats")))
        birthday_cfg.update(_cfg_section(self._settings_override.get("birthday")))
        capabilities.update(_cfg_section(self._settings_override.get("capabilities")))

        self._tide_cfg = tide
        phases = _cfg_section(tide.get("phases"))
        self._phases_cfg = {key: _cfg_section(val) for key, val in phases.items()}
        words = tide.get("forbidden_words")
        self._forbidden_words = [str(w) for w in words] if isinstance(words, list) else []
        self._mood_cfg = mood
        self._fragments_cfg = fragments
        self._journal_cfg = journal
        self._review_cfg = review
        self._emotion_sense_cfg = emotion_sense
        self._stats_cfg = stats_cfg
        self._birthday_cfg = birthday_cfg
        self._caps_cfg = capabilities

        # 锚点缺失（shard 与全局配置都没有）：随机化的默认锚点--反推一个日期
        # 使"今天"落在本轮平稳期的随机位置（见 cycle.randomized_default_anchor）。
        # 装完前几天必是平稳期、每次安装起点各不相同。1.2.2 审查轮（P4）起
        # 可信会话当场落盘固化（重启不重新随机，也不依赖后续任意 cycle 写捎带）；
        # 不可信会话只写内存——此刻落盘就是幻影覆写（不写配置文件，见上）
        if not str(tide.get("anchor_date") or "").strip():
            today = self._today_str(tide)
            for lanlan, shard in self._shards.items():
                if shard.loaded and not str(shard.cycle.get("anchor_date") or "").strip():
                    params = self._effective_cycle_settings(shard)
                    anchor = randomized_default_anchor(
                        today=today,
                        cycle_length=int(params["cycle_length"]),
                        period_length=int(params["period_length"]),
                        ovulation_day=int(params["ovulation_day"]),
                        ovulation_window=int(params["ovulation_window"]),
                    )
                    shard.cycle["anchor_date"] = anchor.isoformat()
                    if self._state_trusted:
                        await self._save_shard_cycle(lanlan, shard)
                    self.logger.info(
                        "randomized default anchor {} assigned (luteal-phase start)",
                        anchor.isoformat(),
                    )

    def _shard_params(self, shard: _LanlanShard) -> JsonObject:
        params = shard.cycle.get("params")
        return params if isinstance(params, dict) else {}

    def _auto_derive(self, shard: _LanlanShard | None = None) -> bool:
        """自动演算开关：开启时潮汐期长度/活跃日/活跃窗口由周期长度推导。"""
        params = self._shard_params(shard or self._current_shard())
        if "auto_derive" in params:
            return bool(params["auto_derive"])
        return bool((self._tide_cfg or {}).get("auto_derive", True))

    def _effective_cycle_settings(self, shard: _LanlanShard | None = None) -> JsonObject:
        """某角色生效的周期参数：shard.params 覆盖全局默认；自动演算开启时由周期长度推导。"""
        shard = shard or self._current_shard()
        params = self._shard_params(shard)
        tide = self._tide_cfg or {}
        cycle_length = int(params.get("cycle_length") or tide.get("cycle_length") or 28)
        if self._auto_derive(shard):
            return derive_cycle_params(cycle_length)
        return {
            "cycle_length": cycle_length,
            "period_length": int(params.get("period_length") or tide.get("period_length") or 5),
            "ovulation_day": int(params.get("ovulation_day") or tide.get("ovulation_day") or 14),
            "ovulation_window": int(params.get("ovulation_window") or tide.get("ovulation_window") or 3),
        }

    def _cycle_params(self, shard: _LanlanShard | None = None) -> JsonObject:
        """合成某角色的完整周期参数：全局默认 ← shard.params 覆盖 ← shard.anchor_date。"""
        shard = shard or self._current_shard()
        anchor_raw = str(shard.cycle.get("anchor_date") or "").strip()
        if not anchor_raw:
            anchor_raw = str(self._tide_cfg.get("anchor_date") or "").strip()
        if not anchor_raw:
            # shard 与全局都缺锚点：随机化兜底（正常路径下 _refresh_config 已落
            # 随机默认值；这里只是异常路径的兜底，同样取平稳期随机位置）
            today = self._today_str()
            params = self._effective_cycle_settings(shard)
            anchor_raw = randomized_default_anchor(
                today=today,
                cycle_length=int(params["cycle_length"]),
                period_length=int(params["period_length"]),
                ovulation_day=int(params["ovulation_day"]),
                ovulation_window=int(params["ovulation_window"]),
            ).isoformat()
        return {
            "anchor": parse_anchor_date(anchor_raw),
            **self._effective_cycle_settings(shard),
            "advance_days": max(0, int(shard.cycle.get("advance_days") or 0)),
        }

    def _enabled(self, shard: _LanlanShard | None = None) -> bool:
        shard = shard or self._current_shard()
        enabled = shard.cycle.get("enabled")
        if enabled is None:
            # 与 plugin.toml [tide] enabled = false 一致：配置缺失时保持关闭（fail-closed）。
            # 新角色 shard 没有任何覆写时同样回落到这里——默认不对未设置的角色开启模拟
            enabled = bool((self._tide_cfg or {}).get("enabled", False))
        return bool(enabled)

    def _current_phase_state(self, shard: _LanlanShard | None = None):
        params = self._cycle_params(shard)
        today = self._today_str()
        return compute_phase_state(today=today, **params)

    @property
    def _store_ready(self) -> bool:
        """宿主 store 是否已通电（enabled）。

        FakeStore / 无该属性的自定义实现按可用处理：getattr 缺省 True，既不
        阻塞测试，也在 SDK 未来去掉门控时退化为"永远可用"。
        """
        return bool(getattr(self.store, "enabled", True))

    async def _ensure_store_ready(self) -> bool:
        """确认 PluginStore 可用；disabled 时读一次 effective config 尝试唤醒宿主。

        宿主在插件实例构造那一刻 effective config 还没就位，SDK 据此把 store
        建成 disabled，而 disabled 态读写全部静默空转（宿主 storage/store.py 的
        `if not self.enabled` 早退）——本插件所有持久状态在启动读时会被误判为
        "从未设置"，重启后总开关、锚点、日记一律"看起来是空的"。读一次 config 会
        触发宿主把配置回灌进实例并刷新 store.enabled；读到了但开关还没翻上来就短促
        重试几次（唤醒也可能来自宿主稍后推下来的 CONFIG_UPDATE）。读配置本身失败则
        立刻放弃——宿主不可达时重试同样唤不醒，白等还会顶到拉起超时。
        探测失败绝不抛错：调用方据此走"不可信载入"降级（见 _load_state / shutdown）。
        """
        for attempt in range(_STORE_READY_ATTEMPTS):
            if self._store_ready:
                if attempt:
                    self.logger.info("plugin store ready after {} attempts", attempt + 1)
                return True
            try:
                await self.config.dump(timeout=_STORE_READY_PROBE_TIMEOUT_S)
            except Exception as exc:
                self.logger.warning(
                    "store readiness probe failed ({}); treating this boot's load "
                    "as untrusted instead of waiting", exc.__class__.__name__
                )
                return False
            if self._store_ready:
                self.logger.info("plugin store ready after {} attempts", attempt + 1)
                return True
            if attempt + 1 < _STORE_READY_ATTEMPTS:
                await asyncio.sleep(_STORE_READY_RETRY_S)
        self.logger.warning(
            "PluginStore still disabled after {} attempts; this boot's loaded "
            "state is treated as untrusted and will NOT overwrite persisted data",
            _STORE_READY_ATTEMPTS,
        )
        return False

    async def _retrust_state(self) -> None:
        """载入不可信后 store 中途回电：把盘上真实状态重新载入，替换幻影分片。

        未通电期间的读全是空值、写全被静默丢弃——内存里那份是从"空"起步的幻影，
        盘上的才是权威数据。store 一醒，在任何一次写入放行之前整体重来：清掉
        各 shard 的载入标记与全局容器，重跑 _load_state（这次读到真数据、
        _state_trusted 置真）+ _refresh_config。未通电期间"当场生效但没存住"的
        改动会随重载回退——它们本就没落过盘，回到盘上值是预期（面板下个轮询
        即见真实状态）。

        _retrusting 防重入：_load_state → _ensure_shard 会绕回门控自身。
        """
        self._retrusting = True
        try:
            self.logger.warning(
                "store became ready after an untrusted load: reloading persisted "
                "state before accepting any write"
            )
            for shard in self._shards.values():
                shard.loaded = False
            self._lanlan_index = []
            self._settings_override = {}
            self._caps_off = {}  # 能力否决集重载：不可信期的改动的同构降级（_load_state/_ensure_shard 重填）
            self._proactive_state = {"prev": None, "paused_by": []}
            self._guide = {"wizard": "", "at": "", "version": ""}
            await self._load_state()
            await self._refresh_config()
        finally:
            self._retrusting = False

    async def _load_state(self) -> None:
        self._state_trusted = await self._ensure_store_ready()
        settings_res = await self._store_read(_STORE_SETTINGS)
        if isinstance(settings_res, Ok) and isinstance(settings_res.value, dict):
            self._settings_override = dict(settings_res.value)
        index_res = await self._store_read(_STORE_LANLAN_INDEX)
        if isinstance(index_res, Ok) and isinstance(index_res.value, list):
            self._lanlan_index = [str(n) for n in index_res.value if str(n)]
        proactive_res = await self._store_read(_STORE_PROACTIVE)
        if isinstance(proactive_res, Ok) and isinstance(proactive_res.value, dict):
            raw = proactive_res.value
            prev = raw.get("prev")
            self._proactive_state = {
                "prev": dict(prev) if isinstance(prev, dict) else None,
                "paused_by": [str(n) for n in raw.get("paused_by") or [] if str(n)],
            }
        # 水位快照与盘上同步起点（1.2.2 审查轮 P2）：此后 _persist_proactive_state
        # 的脏检查以此为参照，只写真正发生过的变化
        self._proactive_persisted = _snapshot_proactive(self._proactive_state)
        # 新手引导（1.2.6）：全局一份，宽容载入（坏字段自愈为未引导态）；
        # 未通电会话读到空→向导会弹，用户在向导里完成/跳过后改内存位
        # 并经入口写盘（当场生效、未通电则重启重弹，既定降级契约）
        guide_res = await self._store_read(_STORE_GUIDE)
        self._guide = norm_guide_record(
            guide_res.value if isinstance(guide_res, Ok) else None
        )
        # 能力否决集（1.2.7）：全局份 caps@*（角色份随 _ensure_shard 懒载）
        caps_res = await self._store_read(_STORE_CAPS_GLOBAL)
        self._caps_off["*"] = self._cap_norm_off(
            caps_res.value if isinstance(caps_res, Ok) else None
        )
        await self._migrate_legacy_state_if_needed()
        # 载入所有已知角色的 shard：主动搭话引用计数要看全量生效情绪，
        # 只载当前角色会把"别的角色还在冷战"漏算
        for lanlan in list(self._lanlan_index):
            await self._ensure_shard(lanlan)
        current = await self._resolve_current_lanlan()
        await self._ensure_shard(current)

    async def _migrate_legacy_state_if_needed(self) -> None:
        """0.4.0 单角色数据 → 0.5.0 分片：归属"当前角色"，一次性、幂等。

        判定：Store 里没有 lanlan_index 且存在旧 cycle_state。
        cycle_state.settings 抽进全局 settings；旧 mood_state.proactive_prev
        （暂停水位）搬进 proactive_state——升级瞬间若正处于暂停中，水位不跟着走
        会导致主动搭话永远卡死。旧 key 全部保留作备份，不再写入。
        """
        if not self._state_trusted:
            # 载入不可信（store 未通电）：所有读都是空值，此刻迁移会把"空"当成
            # 旧数据搬进分片并落盘，宁可推迟到下次可信启动再迁
            return
        index_res = await self._store_read(_STORE_LANLAN_INDEX)
        if isinstance(index_res, Ok) and index_res.value is not None:
            return  # 已迁移过（或全新安装已写过索引）：幂等跳过
        legacy_res = await self._store_read(_STORE_CYCLE)
        legacy = legacy_res.value if isinstance(legacy_res, Ok) else None
        if not isinstance(legacy, dict):
            return  # 无旧数据：全新安装，索引随首个 shard 创建落盘
        lanlan = await self._resolve_current_lanlan()
        shard = self._get_shard(lanlan)
        params = legacy.get("params")
        shard.cycle = {
            "enabled": legacy.get("enabled"),
            "anchor_date": legacy.get("anchor_date"),
            "advance_days": legacy.get("advance_days") or 0,
            "phase_seen": legacy.get("phase_seen") or "",
            "params": dict(params) if isinstance(params, dict) else {},
        }
        mood_res = await self._store_read(_STORE_MOOD)
        if isinstance(mood_res, Ok) and isinstance(mood_res.value, dict):
            raw_mood = mood_res.value
            shard.mood = _MoodState.from_mapping(raw_mood)
            prev = raw_mood.get("proactive_prev")
            if isinstance(prev, dict):
                self._proactive_state["prev"] = dict(prev)
                if shard.mood.is_active():
                    self._proactive_state["paused_by"] = [lanlan]
        diary_res = await self._store_read(_STORE_DIARY)
        if isinstance(diary_res, Ok) and isinstance(diary_res.value, list):
            shard.diary = [dict(item) for item in diary_res.value if isinstance(item, dict)]
        shard.loaded = True
        settings = legacy.get("settings")
        if isinstance(settings, dict):
            self._settings_override = dict(settings)
            await self._save_settings()
        self._lanlan_index = [lanlan]
        await self._save_lanlan_index()
        await self._save_shard_cycle(lanlan, shard)
        await self._save_shard_mood(lanlan, shard)
        await self._save_shard_diary(lanlan, shard)
        # 迁移改过水位（prev 搬入/paused_by 标记）：走脏检查出口，成功后同步快照
        await self._persist_proactive_state()
        self.logger.info("legacy single-character state migrated to shard {}", lanlan)

    # ==========================================
    # 落盘统一出口（1.2.2 批次2）：_store_write / _store_read
    # ==========================================

    async def _store_write(self, key: str, value: Any, label: str) -> Result[None]:
        """统一落盘：未通电预警 + 真失败 warning + 回传宿主 Result（调用方自行决定对外语义）。

        两条路径的语义必须分清：
        - 未通电（enabled=False）：宿主 set 静默空转返回 Ok——不是失败，
          维持"当场生效、重启即丢"的既定降级契约（批次1 锁死），只留 warning；
        - 真失败（磁盘满/DB 锁等返回 Err）：留 warning 并把 Err 原样回传，
          由用户可见的写入口决定是否向宿主报失败（不再"面板显示成功、盘上没写"）。
        """
        if not self._store_ready:
            self.logger.warning("store not ready: {} not persisted", label)
        res = await self.store.set(key, value)
        if isinstance(res, Err):
            self.logger.warning("persist {} failed: {}", label, res.error)
        return res

    async def _store_read(self, key: str) -> Result[Any]:
        """统一读取：store.get 返回 Err（DB 抖动/读失败）时留痕，与"值不存在
        （Ok(None)）"区分——过去读失败被静默按缺省处理，表现为"数据全空"且零日志。
        只加日志，不改变降级行为：调用方拿到原始 Result，仍按缺省继续。"""
        res = await self.store.get(key)
        if not isinstance(res, Ok):
            self.logger.warning("store read failed for {}: {}", key, res.error)
        return res

    async def _store_delete(self, key: str, label: str) -> Result[Any]:
        """统一删除出口（1.2.2 审查轮 P1）：与 _store_write 同款语义——未通电
        留 warning（宿主静默空转返回 Ok(False/None)，不是失败），真失败（Err）
        留 warning 并原样回传 Result，由调用方决定是否向用户传播。
        过去 prune 之外的删除（图库删图等）直连 store.delete 全部零留痕。"""
        if not self._store_ready:
            self.logger.warning("store not ready: {} not deleted", label)
        res = await self.store.delete(key)
        if isinstance(res, Err):
            self.logger.warning("delete {} failed: {}", label, res.error)
        return res

    @staticmethod
    def _persist_error(*results: Result[None]) -> Err | None:
        """多键写入聚合：任一 Err 即取第一个，向调用方回**稳定码** persist_failed；
        真实失败细节进日志（不进用户可见文案，1.3.0 第九轮 i18n 契约）；全 Ok 返回 None。
        前面的写不回滚（保持简单）：真失败极罕见，重试幂等。"""
        for res in results:
            if isinstance(res, Err):
                # 失败细节已由 _store_write/_store_delete 统一出口留痕，此处不重复落日志
                return Err(SdkError("persist_failed"))
        return None

    async def _save_shard_cycle(self, lanlan: str, shard: _LanlanShard) -> Result[None]:
        return await self._store_write(
            _cycle_key(lanlan), dict(shard.cycle), f"cycle state for {lanlan}",
        )

    async def _save_shard_mood(self, lanlan: str, shard: _LanlanShard) -> Result[None]:
        return await self._store_write(
            _mood_key(lanlan), shard.mood.to_mapping(), f"mood state for {lanlan}",
        )

    async def _save_shard_diary(self, lanlan: str, shard: _LanlanShard) -> Result[None]:
        # 内存与落盘保持同一截断语义：都只保留最近 _DIARY_MAX_ENTRIES 条，
        # 避免本次会话 total 与重启后 total 不一致
        shard.diary = list(shard.diary[-_DIARY_MAX_ENTRIES:])
        return await self._store_write(_diary_key(lanlan), list(shard.diary), f"diary for {lanlan}")

    async def _save_diary(self) -> Result[None]:
        """兼容包装：保存"当前"shard 的手记（无 lanlan 上下文的旧调用点/测试用）。"""
        name = self._current_shard_name()
        return await self._save_shard_diary(name, self._get_shard(name))

    async def _save_shard_journal(self, lanlan: str, shard: _LanlanShard) -> Result[None]:
        # 与手记同一截断语义：内存与落盘都只保留最近 _JOURNAL_MAX_PAGES 页。
        # 1.3.0 藏书阁：截断掉的溢出页不再静默丢弃，先 append 进合订本再写书 blob
        # （工具路径的淘汰页由 journal_write 返回值带出、调用点另行入阁；这里兜住
        # 旧版磁盘残留 / 迁移超长等一切截断路径）
        overflow: list[JsonObject] = []
        if len(shard.journal) > _JOURNAL_MAX_PAGES:
            overflow = shard.journal[:-_JOURNAL_MAX_PAGES]
        shard.journal = list(shard.journal[-_JOURNAL_MAX_PAGES:])
        res_journal = await self._store_write(
            _journal_key(lanlan), list(shard.journal), f"journal for {lanlan}",
        )
        if not overflow:
            return res_journal
        res_archive = await self._append_journal_archive(lanlan, shard, overflow)
        if isinstance(res_journal, Err):
            return res_journal
        return res_archive

    async def _append_journal_archive(
        self, lanlan: str, shard: _LanlanShard, pages: list[JsonObject]
    ) -> Result[None]:
        """藏书阁追加（1.3.0）：把淘汰页按时间正序 append 进 journal_archive@ 并落盘。

        阁内再满才从最旧一页真删（翻阅按“距最近一本”倒计数，删旧不挪位）；
        空入参不碰存储（零淘汰的常态写入不多花一次写）。只追加、不进当前书 blob
        ——写日记的热路径永远只重写 52 页那一块，阁 blob 只在淘汰那一拍重写。
        """
        if not pages:
            return Ok(None)
        for page in pages:
            if isinstance(page, dict):
                shard.journal_archive.append(
                    {**dict(page), "entries": list(page.get("entries") or [])}
                )
        if len(shard.journal_archive) > _JOURNAL_ARCHIVE_MAX_PAGES:
            shard.journal_archive = shard.journal_archive[-_JOURNAL_ARCHIVE_MAX_PAGES:]
        return await self._store_write(
            _journal_archive_key(lanlan),
            list(shard.journal_archive),
            f"journal archive for {lanlan}",
        )

    async def _save_shard_review(self, lanlan: str, shard: _LanlanShard) -> Result[None]:
        """我的日记落盘：成文篇目与素材统计合并写进一个 key（stats 随篇目一起走，
        成文时原子清零——两个独立 key 反而会在中途崩溃时出现篇目已加而 stats
        未清的错位；加载侧对旧独立 stats key 只读迁移）。

        1.3.0 档案室：与 _save_shard_journal 同款兜底——内存里超出活架上限的
        旧卷（磁盘残留/调试注入超长等一切截断路径）先搬进档案室再写篇目 blob；
        工具/成文路径的淘汰篇由 append_review 返回值带出、调用点另行入阁。"""
        overflow: list[JsonObject] = []
        if len(shard.review) > _REVIEW_MAX_ENTRIES:
            overflow = shard.review[:-_REVIEW_MAX_ENTRIES]
        shard.review = list(shard.review[-_REVIEW_MAX_ENTRIES:])
        res_review = await self._store_write(
            _review_key(lanlan),
            {"entries": list(shard.review), "stats": dict(shard.review_stats)},
            f"review for {lanlan}",
        )
        if not overflow:
            return res_review
        res_archive = await self._append_review_archive(lanlan, shard, overflow)
        if isinstance(res_review, Err):
            return res_review
        return res_archive

    async def _append_review_archive(
        self, lanlan: str, shard: _LanlanShard, reviews: list[JsonObject]
    ) -> Result[None]:
        """档案室追加（1.3.0）：把淘汰卷宗按时间正序 append 进 review_archive@ 并落盘。

        与藏书阁 _append_journal_archive 同款纪律：阁内再满才从最旧一卷真删；
        空入参不碰存储；只追加、不进 review@ blob——成文热路径永远只重写 52 篇那块。
        """
        if not reviews:
            return Ok(None)
        for item in reviews:
            if isinstance(item, dict):
                shard.review_archive.append(dict(item))
        if len(shard.review_archive) > _REVIEW_ARCHIVE_MAX_ENTRIES:
            shard.review_archive = shard.review_archive[-_REVIEW_ARCHIVE_MAX_ENTRIES:]
        return await self._store_write(
            _review_archive_key(lanlan),
            list(shard.review_archive),
            f"review archive for {lanlan}",
        )

    async def _save_shard_stats(self, lanlan: str, shard: _LanlanShard) -> Result[None]:
        """相处统计落盘（stats@<角色>；只增不清零，纯本地）。"""
        return await self._store_write(_stats_key(lanlan), dict(shard.stats), f"stats for {lanlan}")

