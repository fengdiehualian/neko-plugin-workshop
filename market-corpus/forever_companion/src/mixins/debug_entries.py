"""调试模式入口（[tide].debug_mode = true 时才注册）。

把难以自然触发的内部机制"快进"到秒级可验证：兜底超时、
限时到期、活动感知分支、注入内容、工具韧性、消息水位。
关闭时不注册任何入口，面板/命令面板零痕迹；生产包默认关闭。
带角色维度的入口支持可选 lanlan 参数（缺省 = 宿主当前角色）。

拆分自 __init__.py（方法逐字搬移，仅换文件与导入）；经 Mixin 组合回
ForeverCompanionPlugin——SDK 的 entry 发现遍历 type(self)，与定义文件无关。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from plugin.sdk.plugin import Err, Ok, Result, SdkError

from ..core.fragments import build_fragment_prompt, parse_fragment_response
from ..core.journal import (
    archive_brief,
    fabricate_demo_pages,
    journal_due,
    journal_write,
)
from ..core.review import fabricate_demo_reviews, review_due
from ..core.state import (
    _FRAGMENT_DEFAULT_CONFIDENCE,
    _FRAGMENT_DEFAULT_SLOT,
    _JOURNAL_DEFAULT_INTERVAL_DAYS,
    _JOURNAL_MAX_PAGES,
    _TIMED_ACTIONS,
    _TONE_COLD_LABELS,
    _debug_backup_key,
    _journal_archive_key,
    _journal_key,
    _LanlanShard,
    _legacy_debug_backup_key,
    _review_archive_key,
    _review_key,
    _stats_key,
)
from ..core.stats import (
    fabricate_demo_stats,
    heatmap_payload,
    new_stats,
    summary_payload,
)
from ..services.tone_slot import _slot_dormancy_hint

JsonObject = dict[str, Any]


class DebugEntriesMixin:
    """全部 debug_* 调试入口。宿主运行时经 type(self) 发现，定义位置无关。"""

    _DEBUG_LANLAN_PROP: JsonObject = {
        "lanlan": {"type": "string", "description": "目标角色名（缺省 = 宿主当前角色）"},
    }

    # ---- 调试备份：真实数据进、真实数据出的唯一通道（一律走 _store_* 统一出口） ----

    async def _debug_backup_load(self, which: str, data_key: str, lanlan: str) -> tuple[str, Any]:
        """读注入前备份。返回 (状态, 值)，状态 ∈ found / absent / unreadable。

        - "found"      已有备份（值即备份内容）
        - "absent"     确认从未注入过 —— 调用方应当当场补备份
        - "unreadable" 两个键名都读失败 —— 调用方**必须中止注入**：此时既不能
          覆盖已有备份（可能把好备份刷成假页），也不能注入（真数据将无从还原）。
          过去这里用 `not isinstance(res, Err)` 表达同一意图，但读失败后仍继续
          注入，等于把"备份失败即中止"写成只防写不防读。

        新键 `pre_debug@<which>@<角色>` 优先，回落旧 `<数据键>|pre-debug`：1.3.0
        正在真机使用，改键名不能让"已注入未还原"的现场丢备份。
        """
        absent_seen = False
        for key in (_debug_backup_key(which, lanlan), _legacy_debug_backup_key(data_key)):
            res = await self._store_read(key)
            if isinstance(res, Err):
                continue  # 留痕由 _store_read 负责
            if res.value is not None:
                return "found", res.value
            absent_seen = True
        return ("absent", None) if absent_seen else ("unreadable", None)

    async def _debug_backup_save(self, which: str, lanlan: str, payload: Any) -> Result[None]:
        """写备份（未通电预警 + 真失败留痕，语义同 _store_write）。"""
        return await self._store_write(
            _debug_backup_key(which, lanlan), payload, f"debug backup {which} for {lanlan}",
        )

    async def _debug_backup_drop(self, which: str, data_key: str, lanlan: str) -> None:
        """清备份：新键与旧键名一并删，不留孤儿；失败由 _store_delete 留痕。"""
        for key in (_debug_backup_key(which, lanlan), _legacy_debug_backup_key(data_key)):
            await self._store_delete(key, f"debug backup for {lanlan}")

    _DEBUG_ENTRIES: tuple[tuple[str, str, str, JsonObject], ...] = (
        (
            "debug_inject",
            "调试：立即注入一次身体轻语",
            "绕过变化门控立即注入，并返回实际注入的全部文本（含活动感知/情绪行），用于验证注入内容。",
            {"type": "object", "properties": dict(_DEBUG_LANLAN_PROP)},
        ),
        (
            "debug_simulate_user_message",
            "调试：模拟一条用户消息",
            "按真实消息路径推进注入状态机（水位/interval_n 计数/变化门控），返回是否注入与计数。",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "模拟的消息文本（供触发词匹配）"},
                    **_DEBUG_LANLAN_PROP,
                },
            },
        ),
        (
            "debug_set_mood",
            "调试：直接设置情绪动作",
            "直接设置指定情绪动作，可选把开始时间拨到 N 分钟前，用于快速验证兜底/到期解除。",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "ebb_tide", "sea_fog", "shallow_reef", "storm_surge", "seek_harbor",
                            "ripple", "warm_current", "spring_tide",
                        ],
                    },
                    "minutes_ago": {"type": "integer", "description": "把开始时间拨到多少分钟前（默认 0）"},
                    "duration_minutes": {"type": "integer", "description": "限时动作的时长（分钟），不填用默认值"},
                    **_DEBUG_LANLAN_PROP,
                },
                "required": ["action"],
            },
        ),
        (
            "debug_expire_mood",
            "调试：立即让当前限时情绪到期",
            "把当前限时情绪动作的到期时间拨到过去，下一趟监督循环（面板轮询约 5 秒）即触发到期恢复台词。",
            {"type": "object", "properties": dict(_DEBUG_LANLAN_PROP)},
        ),
        (
            "debug_force_activity",
            "调试：覆写活动感知快照",
            "模拟系统活动信号（空闲时长/前台类别/隐私态）验证生活感知全部分支；三个参数全空则清除覆写回到真实信号。",
            {
                "type": "object",
                "properties": {
                    "idle_seconds": {"type": "integer", "description": "模拟的系统空闲秒数（≥1800 触发离开分支）"},
                    "category": {"type": "string", "description": "模拟的前台类别：work / gaming / entertainment / communication"},
                    "privacy": {"type": "string", "description": "模拟隐私态：visible / private / unavailable"},
                    **_DEBUG_LANLAN_PROP,
                },
            },
        ),
        (
            "debug_check_tools",
            "调试：立即校验 LLM 工具在位情况",
            "不等 5 分钟周期，立即 GET /api/tools 校验全部情绪/日记工具是否在位，缺失则当场重注册并返回结果。",
            {"type": "object", "properties": {}},
        ),
        (
            "debug_journal",
            "调试：检查个人日记资格并可选立即邀请",
            "返回当前角色的个人日记资格判定（due/reason/页数）；force=true 时清零节流并立即推一次日记邀请。",
            {
                "type": "object",
                "properties": {
                    "force": {"type": "boolean", "description": "true = 清零 24h 节流并立即推一次邀请（默认 false）"},
                    **_DEBUG_LANLAN_PROP,
                },
            },
        ),
        (
            "debug_journal_fill",
            "调试：藏书阁假书架注入/还原/清空",
            "用确定性假页把个人日记活架垫到写满，再连翻数页走生产淘汰链路入阁——面板书架末尾的横放合订本当场出现；真实日记与阁数据先备份，restore=true 一键还原；clear=true 清空当前角色的个人日记活架与合订本（清空前同样先备份，测完仍可 restore 找回）。",
            {
                "type": "object",
                "properties": {
                    "restore": {"type": "boolean", "description": "true = 从备份还原真实日记与合订本（默认 false = 注入）"},
                    "clear": {"type": "boolean", "description": "true = 清空个人日记活架与藏书阁（清空前先备份，可 restore 还原；与 restore 互斥）"},
                    "pages": {"type": "integer", "description": "注入后再翻几页触发淘汰（默认 3，上限 8；每翻一页挤下最旧一页）"},
                    **_DEBUG_LANLAN_PROP,
                },
            },
        ),
        (
            "debug_journal_clear",
            "调试：清空个人日记",
            "单独清空当前角色的个人日记（活架手记与藏书阁合订本一并清）。清空前先走备份通道，误清可用 debug_journal_fill(restore=true) 原样找回。面板无此入口时的调试专用出口。",
            {"type": "object", "properties": dict(_DEBUG_LANLAN_PROP)},
        ),
        (
            "debug_capture_fragment",
            "调试：立即对最近一轮做碎片提取",
            "绕过水位/间隔门控，立即对最近一轮用户消息做碎片提取，返回提取原文、解析结果与是否落盘。",
            {"type": "object", "properties": dict(_DEBUG_LANLAN_PROP)},
        ),
        (
            "debug_emotion_sense",
            "调试：立即分析最近一轮互动的语气",
            "绕过水位/概率/节流门控，立即对最近一轮回复做语气分析，返回 label/置信度/生效阈值/命中分支等全链路判定细节。",
            {"type": "object", "properties": dict(_DEBUG_LANLAN_PROP)},
        ),
        (
            "debug_review",
            "调试：检查我的日记资格与素材统计",
            "返回当前角色的我的日记素材统计（轮数/语气分布/动作事件/碎片数）与双门槛资格判定；force=true 时立即成文一篇。",
            {
                "type": "object",
                "properties": {
                    "force": {"type": "boolean", "description": "true = 跳过门槛立即成文（素材不足仍会拒绝）"},
                    **_DEBUG_LANLAN_PROP,
                },
            },
        ),
        (
            "debug_review_fill",
            "调试：我的日记假卷宗注入/还原",
            "用确定性假篇目（中性观察者口吻、冷暖轮换带本卷依据快照）填满「我的日记」档案架——验证盒脊/卷宗阅读页/朱印落位/档案室搬家链路；注入超 52 卷时旧卷经真实落盘链入档案室。真实篇目、素材与档案室先整包备份，restore=true 一键还原。",
            {
                "type": "object",
                "properties": {
                    "entries": {"type": "integer", "description": "注入篇目数（默认 4，上限 60；>52 验溢出入档案室）"},
                    "restore": {"type": "boolean", "description": "true = 从备份还原真实篇目与素材（默认 false = 注入）"},
                    **_DEBUG_LANLAN_PROP,
                },
            },
        ),
        (
            "debug_stats",
            "调试：相处统计假数据注入/还原",
            "为时光页（热力图/徽章/月报/数字摘要）注入 120 天确定性假数据用于界面验证；真实 stats 先备份，restore=true 还原，clear=true 清空。",
            {
                "type": "object",
                "properties": {
                    "seed": {"type": "boolean", "description": "true = 备份现有数据并注入假数据（默认 false）"},
                    "restore": {"type": "boolean", "description": "true = 从备份还原真实数据（默认 false）"},
                    "clear": {"type": "boolean", "description": "true = 清空为空白统计（不自动备份，默认 false）"},
                    **_DEBUG_LANLAN_PROP,
                },
            },
        ),
    )

    def _sync_debug_entries(self) -> None:
        """按 debug_mode 开关同步调试入口的注册/注销（幂等）。"""
        enabled = bool(self._tide_cfg.get("debug_mode", False))
        if enabled == self._debug_registered:
            return
        register = getattr(self, "register_dynamic_entry", None)
        if enabled:
            if not callable(register):
                return
            count = 0
            for entry_id, name, description, schema in self._DEBUG_ENTRIES:
                handler = getattr(self, f"_{entry_id}", None)
                if not callable(handler):
                    continue
                try:
                    register(
                        entry_id=entry_id,
                        handler=handler,
                        name=name,
                        description=description,
                        input_schema=schema,
                        kind="action",
                    )
                    count += 1
                except Exception as exc:  # noqa: BLE001 - 单个入口失败不阻塞其余
                    self.logger.warning("debug entry {} register failed: {}", entry_id, exc)
            self._debug_registered = True
            self.logger.info("debug mode on: {} debug entries registered", count)
        else:
            unregister = getattr(self, "unregister_dynamic_entry", None)
            if callable(unregister):
                for entry_id, *_ in self._DEBUG_ENTRIES:
                    try:
                        unregister(entry_id)
                    except Exception:  # noqa: BLE001 - 注销失败最多残留一个入口，无害
                        pass
            self._debug_registered = False
            self.logger.info("debug mode off: debug entries unregistered")

    async def _debug_target_shard(self, lanlan: str = "") -> tuple[str, _LanlanShard]:
        """调试入口的角色解析：显式 lanlan → 宿主当前角色。"""
        name = str(lanlan or "").strip() or await self._resolve_current_lanlan()
        return name, await self._ensure_shard(name)

    async def _debug_inject(self, lanlan: str = "", **_: Any):
        name, shard = await self._debug_target_shard(lanlan)
        shard.last_injected_whisper_key = ""  # 绕过变化门控，强制来一次
        parts = await self._inject_now(name, shard)
        return Ok({
            "pushed": bool(parts),
            "lanlan": name,
            "texts": [str(p.get("text") or "") for p in parts],
            "activity_key": shard.last_activity_context_key,
            "whisper_key": shard.last_injected_whisper_key,
        })

    async def _debug_simulate_user_message(self, text: str = "", lanlan: str = "", **_: Any):
        name, shard = await self._debug_target_shard(lanlan)
        # 连续调用时系统时钟可能落在同一粒度刻度（Windows 约 15ms），
        # 强制水位单调递增，否则第二条会被 ts 门控误判为重复消息
        ts = max(time.time(), shard.last_injected_message_ts + 0.001)
        injected = await self._handle_new_user_message(ts, str(text or ""), name)
        return Ok({
            "ts": ts,
            "lanlan": name,
            "injected": injected,
            "message_counter": shard.user_message_count_since_inject,
        })

    async def _debug_set_mood(
        self,
        action: str = "",
        minutes_ago: int = 0,
        duration_minutes: int = 0,
        lanlan: str = "",
        **_: Any,
    ):
        name, shard = await self._debug_target_shard(lanlan)
        action = str(action or "").strip()
        if action not in (*_TIMED_ACTIONS, "seek_harbor"):
            return Err(SdkError(f"unknown mood action: {action!r}"))
        timed = action in _TIMED_ACTIONS
        await self._apply_mood_action(
            action=action,
            minutes=int(duration_minutes) if duration_minutes else None,
            reason="调试设定",
            timed=timed,
            lanlan=name,
        )
        minutes_ago = max(0, int(minutes_ago or 0))
        if minutes_ago:
            shift = minutes_ago * 60.0
            shard.mood.started_at -= shift
            if timed and shard.mood.expires_at > 0:
                shard.mood.expires_at -= shift
            await self._save_shard_mood(name, shard)
        remaining = ""
        if timed and shard.mood.expires_at > 0:
            seconds = shard.mood.expires_at - time.time()
            remaining = (
                f"将在 {max(0, int(seconds))} 秒后到期（下一趟监督循环触发恢复台词）"
                if seconds > 0 else "已过期，下一趟监督循环即触发恢复台词"
            )
        elif action == "seek_harbor":
            limit = self._mood_cfg.get("open_action_timeout_minutes")
            remaining = f"兜底阈值 {limit if limit is not None else 120} 分钟；把 minutes_ago 调到超过阈值即可验证自动解除"
        return Ok({
            "action": action,
            "lanlan": name,
            "started_at": shard.mood.started_at,
            "expires_at": shard.mood.expires_at,
            "note": remaining,
        })

    async def _debug_expire_mood(self, lanlan: str = "", **_: Any):
        name, shard = await self._debug_target_shard(lanlan)
        state = shard.mood
        if state.action not in _TIMED_ACTIONS or not state.is_active():
            return Ok({"forced": False, "note": "当前没有生效的限时情绪动作。"})
        state.expires_at = time.time() - 1
        await self._save_shard_mood(name, shard)
        return Ok({
            "forced": True,
            "action": state.action,
            "lanlan": name,
            "note": "已把到期时间拨到过去；面板开着时约 5 秒内、否则下一趟 10 秒轮询即触发恢复台词。",
        })

    async def _debug_journal(self, force: bool = False, lanlan: str = "", **_: Any):
        name, shard = await self._debug_target_shard(lanlan)
        interval = self._journal_int_cfg("interval_days", _JOURNAL_DEFAULT_INTERVAL_DAYS)
        due, reason = journal_due(shard.journal, interval_days=interval)
        payload: JsonObject = {
            "due": due,
            "reason": reason,
            "pages_total": len(shard.journal),
            "interval_days": interval,
            "lanlan": name,
        }
        if force:
            shard.last_journal_invite_ts = 0.0
            # 必须带 force：非 force 档还有一道 journal_due（距上次落笔满 interval_days）
            # 闸，昨天刚写过日记的机器上 debug_journal(force=true) 会静默 invited=false，
            # 与 README"立即推一次邀请"和 1.2.3 注释"必走 respond 当面递到档"都不符。
            # 1.2.4 审查修复：force 跳过节奏与节流，只保留 _journal_enabled 三道闸
            invited, deliver = await self._maybe_journal_invite(name, shard, force=True)
            payload["invited"] = invited
            payload["deliver"] = deliver
            payload["note"] = (
                "已清零节流并强制推送邀请（invited=false 时看 deliver："
                "空=开关未开，failed=传输拒收）。"
            )
        return Ok(payload)

    async def _debug_journal_fill(
        self, restore: bool = False, pages: int = 3, clear: bool = False, lanlan: str = "", **_: Any
    ):
        """藏书阁秒级验证（1.3.0 第二轮）：垫满活架→真翻页→生产链路淘汰入阁。

        注入档：真实 journal@/journal_archive@ 先整包备份到 pre_debug@journal@ 键（首次
        才备，连续注入不覆盖最早备份），再把 52 页确定性假页垫上活架，连翻
        pages 页走 **生产 journal_write 路径**（新页按页码递增、淘汰页经
        _append_journal_archive 落盘）——验的是真链路，不是面板假渲染。
        restore 档：两本整包还原 + 清备份。只动当前角色的日记两键，周期/情绪/
        时光日记/统计一概不碰（debug_stats 同款纪律）。
        clear 档（十六轮追加）：清空当前角色的个人日记活架与合订本（面板无此入口，
        重置全部又会连周期/情绪一起清掉——调试现场需要单独清日记本）。与 stats 的
        clear 不同档：这里清的是她手写的真内容，故清空前同样先走备份通道（首次才
        备），测完 restore=true 可原样找回。与 restore 互斥。
        """
        name, shard = await self._debug_target_shard(lanlan)
        state, backup = await self._debug_backup_load("journal", _journal_key(name), name)
        if restore and clear:
            return Err(SdkError("restore / clear 互斥，一次只传一个"))
        if restore:
            if state != "found" or not isinstance(backup, dict):
                return Ok({"restored": False, "lanlan": name, "note": "没有可还原的备份（从未注入过）。"})
            raw_j = backup.get("journal")
            raw_a = backup.get("archive")
            shard.journal = [
                dict(item) for item in (raw_j if isinstance(raw_j, list) else []) if isinstance(item, dict)
            ]
            shard.journal_archive = [
                dict(item) for item in (raw_a if isinstance(raw_a, list) else []) if isinstance(item, dict)
            ]
            res_journal = await self._save_shard_journal(name, shard)
            res_archive = await self._store_write(
                _journal_archive_key(name), list(shard.journal_archive), f"journal archive for {name}",
            )
            await self._debug_backup_drop("journal", _journal_key(name), name)
            self.logger.info("debug journal fill restored for {}", name)
            persist_err = self._persist_error(res_journal, res_archive)
            if persist_err is not None:
                return persist_err
            return Ok({
                "restored": True,
                "lanlan": name,
                "note": "真实日记与合订本已还原，备份已清除。",
                **self._debug_journal_fill_brief(shard),
            })
        if clear:
            if state == "unreadable":
                return Err(SdkError("备份状态读不出来，已中止清空（读失败时清掉真数据将无从还原）"))
            if state == "absent":
                saved = await self._debug_backup_save(
                    "journal", name,
                    {"journal": list(shard.journal), "archive": list(shard.journal_archive)},
                )
                if isinstance(saved, Err):
                    return Err(SdkError("备份真实数据失败，已中止清空"))
            count = len(shard.journal)
            archive_count = len(shard.journal_archive)
            shard.journal = []
            shard.journal_archive = []
            res_journal = await self._save_shard_journal(name, shard)
            # 合订本一并清空（写空列表 blob，clear_review 同口径的彻底清理）
            res_archive = await self._store_write(
                _journal_archive_key(name), [], f"clear journal archive for {name}",
            )
            self.logger.info(
                "debug journal cleared for {} (pages {}, archive {})", name, count, archive_count,
            )
            persist_err = self._persist_error(res_journal, res_archive)
            if persist_err is not None:
                return persist_err
            return Ok({
                "cleared": count,
                "archive_cleared": archive_count,
                "lanlan": name,
                "note": "个人日记活架与合订本已清空（清空前已备份，测完 restore=true 可找回）。",
                **self._debug_journal_fill_brief(shard),
            })
        flip = max(1, min(int(pages or 3), 8))
        if state == "unreadable":
            return Err(SdkError("备份状态读不出来，已中止注入（读失败时覆盖真数据将无从还原）"))
        if state == "absent":
            saved = await self._debug_backup_save(
                "journal", name,
                {"journal": list(shard.journal), "archive": list(shard.journal_archive)},
            )
            if isinstance(saved, Err):
                return Err(SdkError("备份真实数据失败，已中止注入"))
        demo = fabricate_demo_pages(_JOURNAL_MAX_PAGES)
        evicted_all: list[JsonObject] = []
        last_no = 0
        for i in range(flip):
            demo, last_no, _, evicted = journal_write(
                demo, f"【这段时间】调试翻页 {i + 1}：活架写满后被挤下的那一页，会原样出现在藏书阁合订本里。",
                True, affect=0.2,
            )
            evicted_all.extend(evicted)
        shard.journal = demo
        res_journal = await self._save_shard_journal(name, shard)
        res_archive = await self._append_journal_archive(name, shard, evicted_all)
        self.logger.info(
            "debug journal fill seeded for {} (flipped {}, archived {})", name, flip, len(evicted_all),
        )
        persist_err = self._persist_error(res_journal, res_archive)
        if persist_err is not None:
            return persist_err
        return Ok({
            "seeded": True,
            "lanlan": name,
            "flipped": flip,
            "newest_page_no": last_no,
            "note": "打开日记页即见写满的书架与末尾横放的合订本（假页带 demo 标记）；测完 restore=true 还原。",
            **self._debug_journal_fill_brief(shard),
        })

    def _debug_journal_fill_brief(self, shard: _LanlanShard) -> JsonObject:
        """debug_journal_fill 回显：活架/藏书阁本数与阁 brief（与面板 journal_archive_brief 同口径）。"""
        return {
            "shelf_pages": len(shard.journal),
            "archive": archive_brief(shard.journal_archive),
        }

    async def _debug_journal_clear(self, lanlan: str = "", **_: Any):
        """独立调试入口（十八轮）：清空个人日记。

        逻辑零新卷：直接转发 debug_journal_fill 的 clear 档（备份/中止纪律/
        互斥闸全复用），只是把"藏在参数里的第三档"提成入口列表里看得见、
        一键能用的独立条目——用户实机反馈：清档藏在 fill 的参数里不好找。
        注册表多一行，注销链/sync 逻辑零触碰（都按 _DEBUG_ENTRIES 驱动）。
        """
        return await self._debug_journal_fill(clear=True, lanlan=lanlan)

    async def _debug_capture_fragment(self, lanlan: str = "", **_: Any):
        """立即对最近一轮用户消息做碎片提取（绕过水位/间隔门控），返回全链路细节。"""
        name, shard = await self._debug_target_shard(lanlan)
        turn = await self._poll_recent_turns(shard, lanlan=name, peek=True)
        if turn is None:
            return Ok({"captured": False, "lanlan": name, "note": "recent.json 里没有可分析的轮次。"})
        user_text, her_text = turn
        slot = str(self._fragments_cfg.get("slot") or "").strip() or _FRAGMENT_DEFAULT_SLOT
        # 一趟只取一次：过去这里连读两遍宿主配置（None 分支又读一遍）
        core_cfg = await self._aload_core_config()
        resolved = self._resolve_tone_slot(core_cfg, slot)
        if resolved is None:
            return Ok({
                "captured": False,
                "lanlan": name,
                "slot": slot,
                "note": _slot_dormancy_hint(core_cfg, slot),
            })
        raw = await asyncio.to_thread(
            self._post_chat_completion,
            resolved["base_url"], resolved["api_key"], resolved["model"],
            build_fragment_prompt(user_text, her_text),
        )
        parsed = parse_fragment_response(raw or "")
        payload: JsonObject = {
            "lanlan": name,
            "slot": slot,
            "user_text": user_text[:200],
            "raw": (raw or "")[:400],
            "parsed": parsed,
            "threshold": self._fragments_float_cfg("confidence_threshold", _FRAGMENT_DEFAULT_CONFIDENCE),
        }
        if parsed and parsed.get("capture"):
            payload["captured"] = True
            payload["total_fragments"] = sum(
                1 for item in shard.diary if str(item.get("source") or "self") == "auto"
            )
        else:
            payload["captured"] = False
            payload["note"] = "模型判定本轮无需记（capture=false）或回复无法解析。"
        return Ok(payload)

    async def _debug_emotion_sense(self, lanlan: str = "", **_: Any):
        """立即对最近一轮互动做语气分析（peek 不推进水位），返回全链路判定细节。"""
        name, shard = await self._debug_target_shard(lanlan)
        turn = await self._poll_recent_turns(shard, lanlan=name, peek=True)
        if turn is None:
            return Ok({"analyzed": False, "lanlan": name, "note": "recent.json 里没有可分析的回复。"})
        user_text, her_text = turn
        mood_active = self._mood_enabled(shard) and shard.mood.is_active()
        text = her_text[:500] if mood_active else f"用户：{user_text[:300]}\n她：{her_text[:500]}"
        result = await self._analyze_turn_tone(text, name)
        slot = str(self._emotion_sense_cfg.get("slot") or "").strip()
        payload: JsonObject = {
            "analyzed": result is not None,
            "lanlan": name,
            "mode": "correction" if mood_active else "screen",
            "mood_action": shard.mood.action if mood_active else "",
            "enabled": self._emotion_sense_enabled(shard),
            "check_rate": self._tone_float_cfg("check_rate", 1.0),
            "effective_threshold": self._effective_tone_threshold(shard),
            "tone_window": list(shard.tone_window),
            "her_reply_preview": her_text[:80],
            "slot": slot,
        }
        if slot and slot != "emotion":
            # 直连路径：回显解析出的 model/脱敏 base_url（只留协议+主机），永不输出 key
            resolved = self._resolve_tone_slot(await self._aload_core_config(), slot)
            if resolved is not None:
                from urllib.parse import urlparse

                host = urlparse(str(resolved["base_url"]))
                payload["resolved_model"] = resolved["model"]
                payload["resolved_base_url"] = (
                    f"{host.scheme}://{host.netloc}/…" if host.netloc else "（已配置）"
                )
            else:
                payload["resolved"] = None
        if result is not None:
            label, confidence = result
            payload["emotion"] = label
            payload["confidence"] = confidence
            payload["would_screen_nudge"] = (
                not mood_active
                and confidence >= self._effective_tone_threshold(shard)
                and (label in _TONE_COLD_LABELS or label == "happy")
            )
        else:
            payload["note"] = (
                "分析失败/降级：检查宿主情感模型配置（或 [emotion_sense].slot 所选槽位的"
                "端点可达性）、CSRF token 获取；详见插件日志。"
            )
        return Ok(payload)

    async def _debug_review(self, force: bool = False, lanlan: str = "", **_: Any):
        """返回我的日记素材统计与资格判定；force=true 立即成文（素材不足拒绝）。"""
        name, shard = await self._debug_target_shard(lanlan)
        due, reason = review_due(
            shard.review_stats,
            turns_threshold=self._review_turns_threshold(),
            days_threshold=self._review_days_threshold(),
        )
        payload: JsonObject = {
            "lanlan": name,
            "enabled": self._review_enabled(shard),
            "due": due,
            "reason": reason,
            "entries_total": len(shard.review),
            "stats": {
                "turns": int(shard.review_stats.get("turns") or 0),
                "tone": dict(shard.review_stats.get("tone") or {}),
                "affect_samples": len(shard.review_stats.get("affect_samples") or []),
                "actions": len(shard.review_stats.get("actions") or []),
                "fragments": len(shard.review_stats.get("fragments") or []),
                "started_at": str(shard.review_stats.get("started_at") or ""),
            },
            "thresholds": {
                "turns": self._review_turns_threshold(),
                "days": self._review_days_threshold(),
            },
        }
        if force:
            written, write_reason = await self._maybe_write_review(name, shard, force=True)
            payload["forced_write"] = written
            payload["write_reason"] = write_reason
        return Ok(payload)

    async def _debug_review_fill(
        self, entries: int = 4, restore: bool = False, lanlan: str = "", **_: Any
    ):
        """我的日记秒级验收（1.3.0）：假卷宗上架/还原，debug_journal_fill 同款纪律。

        注入档：真实 review@（篇目+素材）与 review_archive@（档案室）整包备份到
        pre_debug@review@ 键（首次才备），档案架换成确定性假篇目（字段全是存储里现成的
        口径：面板卷宗页/盒脊/朱印/本卷依据一次验齐）；restore 档：整包还原 +
        清备份。只动当前角色 review 两键。entries 可给 53~60：注入超活架上限，
        溢出的旧卷经真实落盘链搬家进档案室（验的是真链路，不是面板假渲染）。
        """
        name, shard = await self._debug_target_shard(lanlan)
        state, backup = await self._debug_backup_load("review", _review_key(name), name)
        if restore:
            if state != "found" or not isinstance(backup, dict):
                return Ok({"restored": False, "lanlan": name, "note": "没有可还原的备份（从未注入过）。"})
            raw = backup
            shard.review = [
                dict(item) for item in (raw.get("entries") or []) if isinstance(item, dict)
            ]
            stats = raw.get("stats")
            shard.review_stats = dict(stats) if isinstance(stats, dict) else {}
            # 档案室一并还原（旧版备份无 archive 字段：保持现状不动）
            archive = raw.get("archive")
            res_archive = Ok(None)
            if isinstance(archive, list):
                shard.review_archive = [
                    dict(item) for item in archive if isinstance(item, dict)
                ]
                res_archive = await self._store_write(
                    _review_archive_key(name), list(shard.review_archive),
                    f"review archive restore for {name}",
                )
            res = await self._save_shard_review(name, shard)
            await self._debug_backup_drop("review", _review_key(name), name)
            self.logger.info("debug review fill restored for {}", name)
            persist_err = self._persist_error(res, res_archive)
            if persist_err is not None:
                return persist_err
            return Ok({
                "restored": True,
                "lanlan": name,
                "entries_total": len(shard.review),
                "archive_total": len(shard.review_archive),
                "note": "真实篇目、素材统计与档案室已还原，备份已清除。",
            })
        n = max(1, min(int(entries or 4), 60))
        if state == "unreadable":
            return Err(SdkError("备份状态读不出来，已中止注入（读失败时覆盖真数据将无从还原）"))
        if state == "absent":
            saved = await self._debug_backup_save(
                "review", name,
                {
                    "entries": list(shard.review),
                    "stats": dict(shard.review_stats),
                    "archive": list(shard.review_archive),
                },
            )
            if isinstance(saved, Err):
                return Err(SdkError("备份真实数据失败，已中止注入"))
        shard.review = fabricate_demo_reviews(n)
        # 走真实落盘链：注入超 52 卷时 _save_shard_review 的溢出兜底会把旧卷
        # 搬进档案室——秒级验收也能验到搬家链路本身
        res = await self._save_shard_review(name, shard)
        self.logger.info(
            "debug review fill seeded for {} (entries {}, live {}, archive {})",
            name, n, len(shard.review), len(shard.review_archive),
        )
        persist_err = self._persist_error(res)
        if persist_err is not None:
            return persist_err
        return Ok({
            "seeded": True,
            "lanlan": name,
            "entries_total": len(shard.review),
            "archive_total": len(shard.review_archive),
            "note": "打开日记页「我的日记」即见档案架与卷宗（假篇目带 demo 标记；注入超 52 卷时溢出入档案室）；测完 restore=true 还原。",
        })

    async def _debug_stats(
        self,
        seed: bool = False,
        restore: bool = False,
        clear: bool = False,
        lanlan: str = "",
        **_: Any,
    ):
        """相处统计假数据注入/还原（时光页界面验证，零随机可复现）。

        seed：真实 stats 备份到 pre_debug@stats@<角色> 后覆盖为 fabricate_demo_stats
        的 120 天分布（热力图全档位/色条、徽章、月报、数字摘要全覆盖）；
        restore：从备份还原；clear：清空为空白（不备份，适合反复 seed 用）。
        三个参数互斥，都不带时只回显当前统计概要。只动当前角色的 stats 分片，
        周期/情绪/日记等其他数据一概不碰。
        """
        name, shard = await self._debug_target_shard(lanlan)
        flags = [bool(seed), bool(restore), bool(clear)]
        if sum(flags) > 1:
            return Err(SdkError("seed / restore / clear 互斥，一次只传一个"))
        state, backup = await self._debug_backup_load("stats", _stats_key(name), name)
        today = self._stats_today()
        if seed:
            if state == "unreadable":
                return Err(SdkError("备份状态读不出来，已中止注入（读失败时覆盖真数据将无从还原）"))
            if state == "absent":
                # 首次 seed 才备份：连续 seed 不覆盖最早的备份（还原永远回到最初真实数据）
                saved = await self._debug_backup_save("stats", name, dict(shard.stats))
                if isinstance(saved, Err):
                    return Err(SdkError("备份真实数据失败，已中止注入"))
            shard.stats = fabricate_demo_stats(today)
            res_stats = await self._save_shard_stats(name, shard)
            self.logger.info("debug stats seeded for {}", name)
            persist_err = self._persist_error(res_stats)
            if persist_err is not None:
                return persist_err
            return Ok({
                "seeded": True,
                "lanlan": name,
                "note": "已注入 120 天假数据；打开时光页即可验证热力图/徽章/月报/摘要。"
                        "测完 restore=true 还原真实数据。",
                **self._debug_stats_brief(shard, today),
            })
        if restore:
            if state != "found" or backup is None:
                return Ok({"restored": False, "lanlan": name, "note": "没有可还原的备份（从未 seed 过）。"})
            shard.stats = dict(backup)
            res_stats = await self._save_shard_stats(name, shard)
            await self._debug_backup_drop("stats", _stats_key(name), name)
            self.logger.info("debug stats restored for {}", name)
            persist_err = self._persist_error(res_stats)
            if persist_err is not None:
                return persist_err
            return Ok({
                "restored": True,
                "lanlan": name,
                "note": "真实相处统计已还原，备份已清除。",
                **self._debug_stats_brief(shard, today),
            })
        if clear:
            shard.stats = new_stats()
            res_stats = await self._save_shard_stats(name, shard)
            persist_err = self._persist_error(res_stats)
            if persist_err is not None:
                return persist_err
            return Ok({"cleared": True, "lanlan": name, **self._debug_stats_brief(shard, today)})
        return Ok({
            "lanlan": name,
            "note": "带 seed=true 注入假数据 / restore=true 还原 / clear=true 清空。",
            **self._debug_stats_brief(shard, today),
        })

    def _debug_stats_brief(self, shard: _LanlanShard, today: str) -> JsonObject:
        """debug_stats 回显用的统计概要（与面板时光页同源的派生视图）。"""
        return {
            "summary": summary_payload(shard.stats, today),
            "heat_days": len(heatmap_payload(shard.stats, today).get("days") or []),
        }

    async def _debug_force_activity(
        self,
        idle_seconds: int | None = None,
        category: str = "",
        privacy: str = "",
        lanlan: str = "",
        **_: Any,
    ):
        # 覆写本身是全局的（模拟的是"用户"这一个真实人的系统信号）；
        # lanlan 只决定清哪个 shard 的注入指纹、用哪个 shard 预览
        name, shard = await self._debug_target_shard(lanlan)
        if idle_seconds is None and not str(category or "").strip() and not str(privacy or "").strip():
            self._activity_override = None
            shard.last_injected_whisper_key = ""
            return Ok({"override": None, "lanlan": name, "note": "已清除覆写，恢复真实系统信号。"})
        self._activity_override = {
            "idle": int(idle_seconds) if idle_seconds is not None else None,
            "category": str(category or "").strip().lower() or None,
            "privacy": str(privacy or "visible").strip() or "visible",
        }
        shard.last_injected_whisper_key = ""  # 让下次注入立即反映覆写
        line = await self._build_activity_context_line(self._current_phase_state(shard), shard)
        return Ok({"override": self._activity_override, "lanlan": name, "preview_line": line})

    async def _debug_check_tools(self, **_: Any):
        self._last_tool_health_ts = 0.0
        reachable, missing = await self._tool_health_scan()
        re_emitted = False
        if reachable and missing:
            self._reemit_missing_tools(missing)
            re_emitted = True
        llm_tools = getattr(self, "_llm_tools", None)
        total = len(llm_tools) if isinstance(llm_tools, dict) else 0
        return Ok({
            "main_server_reachable": reachable,
            "local_tool_count": total,
            "missing": missing,
            "re_emitted": re_emitted,
            "note": (
                "全部在位" if reachable and not missing
                else ("main_server 不可达" if not reachable else f"已重注册 {len(missing)} 个缺失工具")
            ),
        })
