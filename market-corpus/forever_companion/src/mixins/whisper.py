"""身体状态注入引擎（核心循环）：总线轮询 / 注入门控 / 轻语组装 / 阶段开场白。

拆分自 __init__.py（方法逐字搬移，仅换文件与导入）；经 Mixin 组合回
ForeverCompanionPlugin。每 10 秒 timer 轮询用户消息总线（memory 总线不可订阅，
轮询是插件侧唯一通道），检测到新消息且该角色开启模拟时按注入策略
（every_user_message / interval_n / on_trigger / off）与变化门控，把当前阶段的
身体感受 + 活动感知行 + 情绪行以 read 轻语定向注入该角色上下文。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from ..core.birthday import (
    birthday_due,
    birthday_is_today,
    make_birthday_diary_record,
    mark_birthday_pushed,
)
from ..core.cycle import TideConfigError, _time_bucket, build_body_whisper
from ..core.journal import journal_due, next_page_no
from ..core.state import (
    _JOURNAL_DEFAULT_INTERVAL_DAYS,
    _JOURNAL_INVITE_THROTTLE_SEC,
    _JOURNAL_RESPOND_COOLDOWN_SEC,
    _LanlanShard,
    _now_utc,
    _parse_iso_ts,
)

# 宿主在 LLM 注入边界展开为当前会话的角色名；插件侧不得自行替换
MASTER_NAME_TOKEN = "{MASTER_NAME}"

JsonObject = dict[str, Any]


class WhisperMixin:
    """注入引擎：总线轮询、频控门控、身体轻语组装与推送、阶段开场白。"""

    # ==========================================
    # 身体状态注入（核心循环）
    # ==========================================

    @staticmethod
    def _unwrap_bus_record(record: object) -> object:
        """取回 bus 记录的原始 payload dict。

        SDK 包装层契约不一致：宿主 MemoryList.dump_records() 给的是 MemoryRecord
        对象序列而非 dict，SdkBusMemoryRecord.from_raw 对非 Mapping 会包成
        {"value": <record>}，把 type 字段吞掉（运行时症状：心跳 latest_type=?，
        用户消息永远被过滤掉）。这里解开一层；各层形态（宿主 MemoryRecord.raw /
        SDK .payload / 裸 dict）都兼容。
        """
        raw = getattr(record, "payload", None) or getattr(record, "raw", record)
        if isinstance(raw, dict) and set(raw) == {"value"}:
            inner = raw.get("value")
            raw = getattr(inner, "raw", None) or getattr(inner, "payload", None) or inner
        return raw

    async def _poll_latest_user_message(self) -> tuple[float, str, bool, str] | None:
        """读取总线上最新的用户消息，返回 (时间戳, 内容预览, 是否语音, 归属角色)。

        正确 API 是 SDK v2 的 ctx.bus.memory.get(bucket_id=..., limit=..., timeout=...)：
        返回值可能是 awaitable 或 SdkBusList，记录经 _unwrap_bus_record 解包成原始
        dict（真实运行时任一包装形态都兼容：SDK 可能把宿主 MemoryRecord 对象再包成
        payload={"value": ...}，不解包会把 type 吞掉、用户消息永远被过滤）。
        取最近 10 条倒序找最新 user_message：bucket 可能混入其他类型记录，
        只盲取最后一条会让用户消息被永久遮蔽。归属角色取 payload 的 lanlan
        字段（宿主双 bucket 写入时带档案名原文）；缺失时回落"当前角色"。
        """
        bus = getattr(self.ctx, "bus", None)
        memory = getattr(bus, "memory", None) if bus is not None else None
        mem_get = getattr(memory, "get", None) if memory is not None else None
        if not callable(mem_get):
            if self._last_bus_error_logged < 0:
                self._last_bus_error_logged = time.monotonic()
                self.logger.warning("ctx.bus.memory.get unavailable on ctx; message polling disabled")
            return None
        try:
            # to_thread 挪到工作线程：timer handler scope 里同步调 bus.memory.get
            # 会触发宿主 "Sync call invoked inside handler" 告警，且 ZMQ IPC 下
            # 最多阻塞事件循环 1s；返回 awaitable 时兜底 await
            # （有事件循环的宿主环境 get 返回协程，测试桩同步/异步两种都有）
            result = await asyncio.to_thread(mem_get, bucket_id="default", limit=10, timeout=1.0)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - 总线不可用不应拖垮 timer
            now_mono = time.monotonic()
            if now_mono - self._last_bus_error_logged > 300:
                self._last_bus_error_logged = now_mono
                # 必须 warning 而非 debug：总线彻底断开时这是唯一线索，
                # debug 级别不进日志文件会造成"注入失效但零日志"的盲区
                self.logger.warning("bus memory read failed (throttled 5min): {}", exc)
            return None
        if result is None or getattr(result, "error", None) is not None:
            now_mono = time.monotonic()
            if now_mono - self._last_bus_error_logged > 300:
                self._last_bus_error_logged = now_mono
                self.logger.warning("bus memory read returned error (throttled 5min): {}", getattr(result, "error", None))
            return None
        try:
            seq = list(result)
        except TypeError:
            seq = []
        for record in reversed(seq):
            raw = self._unwrap_bus_record(record)
            if not isinstance(raw, dict) or str(raw.get("type") or "") != "user_message":
                continue
            try:
                ts = float(raw.get("_ts") or getattr(record, "timestamp", None) or 0.0)
            except (TypeError, ValueError):
                continue
            content = str(raw.get("content") or "")
            # 宿主同时写入了 lanlan / is_voice 字段（语音转写与文字输入都会发布）；
            # lanlan 是注入定向与 shard 归属的依据，缺失时回落当前角色
            is_voice = bool(raw.get("is_voice"))
            lanlan = str(raw.get("lanlan") or "").strip() or await self._resolve_current_lanlan()
            return (ts, content, is_voice, lanlan)
        # 心跳留痕：区分"消息没进总线"（桶空）与"被其他类型记录遮蔽"；
        # 5 分钟节流，仅在总开关开启时才会走到这里（tick 有 enabled 门控）
        now_mono = time.monotonic()
        if now_mono - self._last_bus_heartbeat_logged > 300:
            self._last_bus_heartbeat_logged = now_mono
            if not seq:
                self.logger.info("bus heartbeat: bucket=default empty (no user message within TTL)")
            else:
                last_raw = self._unwrap_bus_record(seq[-1])
                latest_type = str(last_raw.get("type") or "?") if isinstance(last_raw, dict) else "?"
                self.logger.info(
                    "bus heartbeat: no user_message in last {} records; latest_type={}",
                    len(seq), latest_type,
                )
        return None

    def _should_inject(self, shard: _LanlanShard, message_text: str) -> bool:
        mode = str(self._tide_cfg.get("inject_mode") or "every_user_message")
        if mode == "off":
            return False
        if mode == "on_trigger":
            keywords = self._tide_cfg.get("trigger_keywords")
            words = [str(k).lower() for k in keywords] if isinstance(keywords, list) else []
            text = message_text.lower()
            return any(word and word in text for word in words)
        if mode == "interval_n":
            n = max(1, int(self._tide_cfg.get("inject_interval_n") or 3))
            shard.user_message_count_since_inject += 1
            return shard.user_message_count_since_inject >= n
        return True

    async def _prime_inject_on_enable(
        self, was_enabled: bool, lanlan: str | None = None, shard: _LanlanShard | None = None
    ) -> None:
        """开启某角色总开关的瞬间先注入一次当前身体状态。

        不这样的话用户要等满 interval_n 条消息（默认 3）才能看到效果，
        容易误以为插件没生效（语音会话中 read 送达仍受平台时序约束，
        建议用文字对话验证）。配置无效时 _inject_now 静默返回空列表。
        """
        shard = shard or self._current_shard()
        if not was_enabled and self._enabled(shard):
            shard.last_injected_whisper_key = ""
            await self._inject_now(lanlan, shard)

    async def _handle_new_user_message(self, ts: float, text: str, lanlan: str | None = None) -> bool:
        """统一的新用户消息处理入口（轮询路径与未来其他驱动源共用），按归属角色 shard 驱动。

        水位/interval 计数/变化门控都是 per-shard 的：多角色并行会话互不影响。
        interval_n 计数只在真正注入成功后清零：变化门控拦截时不消耗
        计数，保证实际注入频率不低于配置。
        """
        name = str(lanlan or "").strip() or self._current_shard_name()
        shard = await self._ensure_shard(name)
        if ts <= shard.last_injected_message_ts:
            return False
        shard.last_injected_message_ts = ts
        # 我的日记素材：每条新用户消息计一轮（含心情采样）；统计量小、
        # 与注入无关，放在水位推进之后必经路径上。落盘随下一趟 mood/diary
        # 保存搭车太脆——这里直接存（store.set 是本地 IPC，开销可忽略）
        self._feed_review_turn(name, shard)
        if shard.review_stats.get("turns"):
            await self._save_shard_review(name, shard)
        # 相处统计（1.1.0）：同一驱动点、独立口径（不清零、不受 [review] 闸控制），
        # 与我的日记落盘合并为一次 store.set 之后，避免两趟 IPC
        self._feed_stats_turn(shard, ts)
        await self._save_shard_stats(name, shard)
        # 和好提醒与注入频控解耦：每条新消息都要检查（冷战中注入被静音，
        # 提醒却是唯一能把"该调 mood_rising_tide 了"送到她面前的通道）
        await self._maybe_nudge_reconcile(text, name)
        # 静默类情绪动作期间不注入身体轻语（监督循环仍在跑暂停/加固）
        if self._is_silent_mood_active(shard):
            return False
        # 生日轻语（1.3.1）：不受 inject_mode 频控辖（一天至多一条、自带角色
        # 当天水位），但服从沉默闸——她正冷战/已读不回时这条消息本就是她
        # 选择不理的；水位未盖，当天静默解除后的下一条消息仍能补递
        await self._maybe_birthday_push(name, shard)
        if not self._should_inject(shard, text):
            # 可观测性：频控拦截必须留痕，否则"注入失效"无从排查（用户消息节奏低，日志量可控）
            self.logger.info(
                "inject skipped by frequency strategy: lanlan={} mode={} counter={}",
                name,
                str(self._tide_cfg.get("inject_mode") or "every_user_message"),
                shard.user_message_count_since_inject,
            )
            return False
        parts = await self._inject_now(name, shard)
        if not parts:
            self.logger.info("inject skipped: whisper content unchanged since last injection")
            return False
        shard.user_message_count_since_inject = 0
        return True

    # 和好信号关键词：冷战/小情绪中用户消息命中任一关键词时提醒她该调工具了。
    # 只取高置信度的道歉/安抚词，避免日常聊天误触发。
    _RECONCILE_HINT_KEYWORDS = (
        "对不起", "抱歉", "原谅", "别生气", "不要生气", "别不理",
        "我错了", "和好", "理理我", "不要不理", "错了嘛",
    )

    async def _maybe_nudge_reconcile(self, text: str, lanlan: str | None = None) -> None:
        """沉默/小情绪期间，用户像在道歉/哄她时，推一条 read 提醒（定向到该角色）。

        背景：模型常常"演"出和好却忘了调 mood_rising_tide 切状态，
        导致面板情绪卡在冷战不动。提醒只递一句，是否和好仍由她自己决定；
        5 分钟节流（per-shard），新情绪回合开始时重置（见 _apply_mood_action）。
        名单含心有涟漪："能哄好"正是这个中间态的存在意义。
        """
        name = str(lanlan or "").strip() or self._current_shard_name()
        shard = self._get_shard(name)
        if not self._mood_enabled(shard) or not shard.mood.is_active():
            return
        if shard.mood.action not in ("ebb_tide", "sea_fog", "shallow_reef", "ripple"):
            return
        lowered = str(text or "").lower()
        if not any(word in lowered for word in self._RECONCILE_HINT_KEYWORDS):
            return
        now = time.time()
        if now - shard.last_reconcile_nudge_ts < 300:
            return
        shard.last_reconcile_nudge_ts = now
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": (
                "（内心状态提醒）对方似乎在道歉或哄你。如果你心里已经软下来想和好，"
                "必须先调用 mood_rising_tide 工具把情绪切换为心情转晴，再用恢复后的语气说话——"
                "不要只在嘴上和好，状态不切换的话系统会一直按当前情绪约束你。"
                "如果你还想再气一会儿，也可以继续不理，由你自己决定。"
            )}],
            source=self.plugin_id,
            target_lanlan=name,
            coalesce_key=f"{self.plugin_id}.reconcile_nudge",
            metadata={"message_type": f"{self.plugin_id}.reconcile_nudge", "during_action": shard.mood.action},
        )
        self.logger.info("reconcile nudge pushed during {} for {}", shard.mood.action, name)

    def _whisper_state_key(self, state, shard: _LanlanShard | None = None) -> str:
        """当前注入内容的变化指纹：阶段 + 时段 + 心情动作 + 活动感知上下文。

        read 推送会随用户消息永久写入对话历史并进入记忆抽取管线，
        内容没变就不该重复注入（低侵入：少说、说了才算数）。
        """
        shard = shard or self._current_shard()
        bucket = _time_bucket(str(self._tide_cfg.get("timezone") or "auto"))
        mood = shard.mood.action if self._mood_enabled(shard) and shard.mood.is_active() else ""
        return f"{state.phase}:{bucket}:{mood}:{shard.last_activity_context_key}"

    async def _build_activity_context_line(self, state, shard: _LanlanShard | None = None) -> str:
        """活动感知：把"用户在不在、在忙什么"纳入身体轻语（性价比最高的宿主结合点）。

        数据源是 SDK 的 get_os_activity_snapshot（前台类别/系统空闲时长/隐私态，
        study_companion 同款）。隐私态 private/unavailable 时绝不描述用户行为；
        结果缓存为指纹（per-shard）参与变化门控，上下文不变不重复注入。
        """
        shard = shard or self._current_shard()
        if not self._cap_effective("activity_sense", shard=shard):
            shard.last_activity_context_key = ""
            return ""
        override = self._activity_override
        if isinstance(override, dict):
            # 调试模式覆写：不访问真实系统信号，直接按设定值构造上下文，
            # 便于不开机不动地验证全部分支（详见 debug_force_activity）
            privacy = str(override.get("privacy") or "visible")
            raw_idle = override.get("idle")
            try:
                idle = float(raw_idle) if raw_idle is not None else None
            except (TypeError, ValueError):
                idle = None
            category = str(override.get("category") or "").strip().lower()
        else:
            try:
                from plugin.sdk.plugin import get_os_activity_snapshot

                snap = await get_os_activity_snapshot(self.plugin_id)
            except Exception as exc:  # noqa: BLE001 - 活动感知失败不影响基础注入
                self.logger.debug("activity snapshot failed: {}", exc)
                return ""
            privacy = str(getattr(snap, "privacy_state", "unavailable") or "unavailable")
            idle = getattr(snap, "system_idle_seconds", None)
            category = str(getattr(snap, "foreground_category", "") or "").strip().lower()
        if privacy != "visible":
            shard.last_activity_context_key = ""
            return ""
        key = ""
        line = ""
        if idle is not None and idle >= 1800:
            key = "idle"
            if state.phase == "menstrual":
                line = (
                    "[生活感知] {MASTER_NAME}已经有一阵子没在电脑前了。你本来就倦，"
                    "索性让自己也松下来、养养神；他回来时不用假装一直精神十足。"
                )
            elif state.phase == "ovulatory":
                line = (
                    "[生活感知] {MASTER_NAME}已经有一阵子没在电脑前了。"
                    "你有点想他，但先自己找点轻松的事做，别干等。"
                )
            else:
                line = (
                    "[生活感知] {MASTER_NAME}已经有一阵子没在电脑前了。"
                    "你也顺势放松一下，他回来再自然地接上话。"
                )
        elif category == "work":
            key = "focus:work"
            line = (
                "[生活感知] {MASTER_NAME}现在正在专注做事。你陪着但别黏人："
                "回应简短体贴，不主动拉他聊天，他告一段落你自然能感觉到。"
            )
        elif category == "gaming" and state.phase == "ovulatory":
            key = "gaming"
            line = (
                "[生活感知] {MASTER_NAME}在玩游戏。你这几天本来就黏人，"
                "可以饶有兴致地围观两句，但别打断他的节奏。"
            )
        shard.last_activity_context_key = key
        return line

    async def _build_whisper_parts(self, shard: _LanlanShard, state=None) -> list[dict[str, str]] | None:
        if state is None:
            # 独立调用时自算阶段状态；_inject_now 会传入已算好的 state，
            # 一次注入流程只推算一次（compute_phase_state 不便宜）
            try:
                state = self._current_phase_state(shard)
            except TideConfigError as exc:
                self.logger.warning("skip inject, bad config: {}", exc)
                return None
        whisper = build_body_whisper(
            self._phases_cfg,
            state,
            timezone_name=str(self._tide_cfg.get("timezone") or "auto"),
            forbidden_words=self._forbidden_words,
        )
        if not whisper:
            return None
        parts = [{"type": "text", "text": whisper}]
        activity_line = await self._build_activity_context_line(state, shard)
        if activity_line:
            parts.append({"type": "text", "text": activity_line})
        mood_line = self._build_mood_context_line(shard)
        if mood_line:
            parts.append({"type": "text", "text": mood_line})
        return parts

    async def _inject_now(
        self, lanlan: str | None = None, shard: _LanlanShard | None = None
    ) -> list[dict[str, str]]:
        """执行注入（定向到归属角色）；返回实际推送的 parts（空列表 = 未注入）。"""
        name = str(lanlan or "").strip() or self._current_shard_name()
        shard = shard or self._get_shard(name)
        # 能力中心（1.2.7）：身体轻语独立否决闸——关掉后统计/情绪/感知链路
        # 照跑，只是不再往上下文递身体感受（变化水位同步清零，重新打开后
        # 即使内容未变也会重新递一次）
        if not self._cap_effective("whisper", shard=shard):
            shard.last_injected_whisper_key = ""
            return []
        try:
            state = self._current_phase_state(shard)
        except TideConfigError:
            return []
        key = self._whisper_state_key(state, shard)
        if key == shard.last_injected_whisper_key:
            # 身体状态、心情与活动上下文都没变：不重复注入，避免污染历史与记忆抽取
            return []
        parts = await self._build_whisper_parts(shard, state)  # 复用上面已算好的阶段状态
        if not parts:
            return []
        shard.last_injected_whisper_key = key
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=parts,
            source=self.plugin_id,
            target_lanlan=name,
            coalesce_key=f"{self.plugin_id}.body_whisper",
            metadata={
                "message_type": f"{self.plugin_id}.body_whisper",
                "delivery_semantics": "passive",
                "phase": state.phase,
                "lanlan": name,
            },
        )
        # 可观测性：read 注入对用户隐形，日志是现场验证注入是否发生的唯一途径；
        # parts 数量反映构成（1=仅身体轻语，2=+活动感知或情绪行，3=三者都有）
        self.logger.info(
            "body whisper injected: lanlan={} phase={} parts={} whisper_key={}",
            name, state.phase, len(parts), key,
        )
        return parts

    def _last_phase_name(self, shard: _LanlanShard | None = None) -> str:
        try:
            return self._current_phase_state(shard).phase
        except Exception:  # noqa: BLE001 - metadata only
            return "unknown"

    # ==========================================
    # 融入宿主：阶段开场主动搭话 / 恢复开口
    # ==========================================

    _PHASE_OPENERS: dict[str, list[str]] = {
        "menstrual": [
            "唔……今天有点没精神，{MASTER_NAME}要是找我说话，我可能回得慢一点……",
            "今天浑身有点沉……不想动。{MASTER_NAME}别嫌我懒。",
            "小腹有点不舒服……今天想安静待着，{MASTER_NAME}说话轻一点嘛。",
        ],
        "follicular": [
            "今天感觉缓过来一些了！{MASTER_NAME}有什么想做的吗？",
            "嗯——今天状态不错，脑子也清楚，趁现在多聊点？",
        ],
        "ovulatory": [
            "今天莫名心情很好！{MASTER_NAME} {MASTER_NAME}，在忙什么呀？",
            "感觉身体轻飘飘的，特别想找人说话——{MASTER_NAME}陪我聊聊嘛！",
        ],
        "luteal": [
            "今天一切正常。{MASTER_NAME}最近怎么样？",
        ],
    }

    async def _maybe_phase_opener(
        self, state, lanlan: str | None = None, shard: _LanlanShard | None = None
    ) -> bool:
        """进入与上次不同的阶段时让她主动开口一次（每个阶段只说一次，per-shard）。

        水位记录在内存 + store 的 cycle@<lanlan>.phase_seen。只比较阶段名，
        不带日期：同一阶段内跨天、重启都不再重复；周期回绕进入下一轮
        同名阶段时会经过其他阶段，天然会再次触发。
        """
        name = str(lanlan or "").strip() or self._current_shard_name()
        shard = shard or self._get_shard(name)
        if not self._cap_effective("phase_openers", shard=shard):
            return False
        if state.phase == "before_start":
            return False
        # 静默类情绪动作期间绝不主动搭话
        if self._is_silent_mood_active(shard):
            return False
        seen = str(shard.cycle.get("phase_seen") or "")
        if state.phase == seen:
            return False
        openers = self._PHASE_OPENERS.get(state.phase) or []
        if not openers:
            return False
        # 先落水位再发消息：即使进程在发送瞬间退出，最多损失一句开场白，
        # 不会重启后反复打扰
        shard.cycle["phase_seen"] = marker = state.phase
        await self._save_shard_cycle(name, shard)

        text = openers[int(time.time()) % len(openers)]
        self.push_message(
            visibility=["chat"],
            ai_behavior="respond",
            parts=[{"type": "text", "text": f"[潮汐·今日状态] 你刚意识到自己进入了新的身体阶段（仅给你看的内部提示，不要复述本句）。请以你自己的口吻，自然地把这句话的意思说给 {MASTER_NAME_TOKEN} 听：{text}"}],
            source=self.plugin_id,
            target_lanlan=name,
            priority=3,
            coalesce_key=f"{self.plugin_id}.phase_opener",
            metadata={"message_type": f"{self.plugin_id}.phase_opener", "phase": state.phase},
        )
        self.logger.info("phase opener sent: {} for {}", marker, name)
        return True

    def _journal_enabled(self, shard: _LanlanShard | None = None) -> bool:
        """个人日记开关（1.2.7 起走能力中心）：总开关 ∧ 情绪引擎 ∧
        [journal].enabled ∧ 用户否决。管"递邀请"这条主动链路；写日记工具
        仍只认情绪引擎闸（关掉后她自愿写仍可写，既有语义不变）。"""
        return self._cap_effective("journal", shard=shard)

    def _journal_int_cfg(self, key: str, default: int) -> int:
        try:
            return max(1, int(self._journal_cfg.get(key, default)))
        except (TypeError, ValueError):
            return default

    async def _maybe_journal_invite(
        self, lanlan: str, shard: _LanlanShard, force: bool = False
    ) -> tuple[bool, str]:
        """个人日记邀请：递一条写日记的邀请（附素材）。返回 (是否递出, 投递方式)。

        投递方式（第二个返回值）取值：``""`` 未递出（开关未开 / 周期未到节奏）；
        ``"failed"`` 传输层拒收（宿主背压等，面板应如实报"没递到"）；
        ``"respond"`` 当面递到；``"read"`` 安静流进上下文。

        与 drift_bottle 同构——插件只递邀请，写不写、怎么写由她自己决定。
        24h 内存节流防刷屏；重度负面情绪期间不拦截——把委屈写进日记是合理叙事。
        0.7.0 起替代潮汐周记邀请：不再要求"攒够 N 条手记"，节奏只看距上次落笔的天数
        （续写同样重置计时）。邀请附带写作素材（自上次落笔以来的心情词频 + 新碎片数），
        让她下笔有东西可写。

        投递方式分两档（1.2.3）：
        - tick 周期递邀 → read（安静流进上下文，等她下次开口自然想起，不打扰）；
        - 面板「请她写一篇」(force) → respond 当面递到她手上：点击即起一轮，
          她当场收到邀请、当场决定（写就调用工具；不想写也可以只是心里记下，
          或由着她回一句什么——回应什么仍由她决定）。read 的旧问题：按钮点完
          邀请要等用户下一次开口才"寄到"，用户体感是"点了但她不知道"。
        - respond 带 10 分钟冷却：刚递过再按则回落为 read 补递（note 说明"给她
          留点考虑空间"），防连点成骚扰；同 coalesce_key 下队列内只留最新一条。
        """
        if not self._journal_enabled(shard):
            return False, ""
        interval = self._journal_int_cfg("interval_days", _JOURNAL_DEFAULT_INTERVAL_DAYS)
        due, _reason = journal_due(shard.journal, interval_days=interval)
        now = time.time()
        if not force:
            if not due:
                return False, ""
            if now - shard.last_journal_invite_ts < _JOURNAL_INVITE_THROTTLE_SEC:
                return False, ""
            deliver = "read"
        else:
            # 冷却判定用同一节流水位：距上一次递出（周期或手动）满 10 分钟才起轮
            deliver = "respond" if now - shard.last_journal_invite_ts >= _JOURNAL_RESPOND_COOLDOWN_SEC else "read"
        prev_invite_ts = shard.last_journal_invite_ts
        shard.last_journal_invite_ts = now
        # ---- 写作素材：自上次落笔以来的心情词频（top3）与新碎片数 ----
        last_ts = None
        for page in shard.journal:
            for item in page.get("entries") or []:
                ts = _parse_iso_ts(item.get("ts") if isinstance(item, dict) else None)
                if ts and (last_ts is None or ts > last_ts):
                    last_ts = ts
        tally: dict[str, int] = {}
        new_fragments = 0
        for item in shard.diary:
            ts = _parse_iso_ts(item.get("ts"))
            if last_ts is not None and (ts is None or ts <= last_ts):
                continue
            if str(item.get("source") or "self") == "auto":
                new_fragments += 1
            else:
                word = str(item.get("mood") or "").strip()
                if word:
                    tally[word] = tally.get(word, 0) + 1
        top = sorted(tally.items(), key=lambda kv: -kv[1])[:3]
        hints: list[str] = []
        if top:
            hints.append("这段时间你的心情：" + "、".join(f"{w}×{n}" for w, n in top))
        if new_fragments:
            hints.append(f"心里还新记了 {new_fragments} 笔关于他的片段（可调 mood_recall_fragments 翻翻）")
        material = f"（{'；'.join(hints)}）" if hints else ""
        body = (
            f"距你上一篇日记已经有些日子了（这篇会写进你的第 "
            f"{next_page_no(shard.journal)} 页）。{material}"
            "如果你愿意，可以调用 mood_journal_write 工具写一篇日记——"
            "按「这段时间/我在想/对他的感觉」几个栏目，用你自己的话写连贯的几段。"
            "不想写也完全没关系，由你自己决定。"
        )
        if deliver == "respond":
            # 当面递到（respond 起轮）：她此刻就"意识到"这份邀请。落笔则写；
            # 不想写就先轻轻放下，不必为它硬找话说
            text = (
                f"[潮汐·日记邀请]（仅给你看的内部提示）你忽然想起该写日记了。{body}"
                f"如果决定写，写完可以对 {MASTER_NAME_TOKEN} 说一句“刚写了点日记，记得翻翻”之类的话；"
                "如果暂时不想写，就把它先轻轻放下，不用刻意提起这件事。"
            )
            push_kwargs: JsonObject = {
                "visibility": [],
                "ai_behavior": "respond",
                "priority": 3,
            }
        else:
            text = f"（内心状态提醒）{body}"
            push_kwargs = {"visibility": [], "ai_behavior": "read"}
        pushed = self.push_message(
            parts=[{"type": "text", "text": text}],
            source=self.plugin_id,
            target_lanlan=lanlan,
            coalesce_key=f"{self.plugin_id}.journal_invite",
            metadata={
                "message_type": f"{self.plugin_id}.journal_invite",
                "pages": len(shard.journal),
                "deliver": deliver,
            },
            **push_kwargs,
        )
        # 提交结果必须看（1.2.4 审查修复）：submitted=False 表示这条邀请根本没上线
        # （宿主背压/传输不可用/超大），而 1.2.3 起面板把话说到"已当面递到她手上"，
        # 还会挂"正等她落笔"挂到她写出新页为止——递空了就变成挂着几天的假提示。
        # 手动按钮当场回滚水位（用户可立刻重按）；tick 周期递邀不回滚，
        # 否则传输一直坏时会每趟监督重推一次刷屏，让它按 24h 节流等下一轮。
        # 只把显式 submitted=False 判为失败：桩/旧返回按成功处理
        if isinstance(pushed, dict) and pushed.get("submitted") is False:
            reason = str(pushed.get("reason") or "unknown")
            self.logger.warning(
                "journal invite NOT submitted for {} via {} (force={}): {}",
                lanlan, deliver, force, reason,
            )
            if force:
                shard.last_journal_invite_ts = prev_invite_ts
            return False, "failed"
        self.logger.info(
            "journal invite pushed for {} via {} ({} pages, force={})",
            lanlan, deliver, len(shard.journal), force,
        )
        return True, deliver

    def _journal_invite_pending(self, shard: _LanlanShard) -> bool:
        """「邀请已递出，等她落笔」面板挂起态判定（1.2.3）。

        最近一次递邀晚于全部日记段落的末笔时刻即为挂起——read 邀请只进上下文
        不打断对话，她什么时候落笔完全自主，过去面板上这段时间是纯静默，
        用户体感"点了没反应、过一会凭空多一页"。判定不新增持久字段：邀请时刻
        就是内存节流水位 last_journal_invite_ts（重启即弃，状态随之消失，
        最多重新递邀一次），落笔时刻取书页里最新一段的 ts；没写过日记本就没有
        页，递过邀请即挂起。
        """
        if not shard.last_journal_invite_ts:
            return False
        last_write_epoch = 0.0
        for page in shard.journal:
            entries = page.get("entries") if isinstance(page, dict) else None
            if not isinstance(entries, list):
                continue
            for item in entries:
                parsed = _parse_iso_ts(item.get("ts") if isinstance(item, dict) else None)
                if parsed is None:
                    continue
                epoch = parsed.timestamp()
                if epoch > last_write_epoch:
                    last_write_epoch = epoch
        return shard.last_journal_invite_ts > last_write_epoch

    # ==========================================
    # 生日轻语（1.3.1）
    # ==========================================

    async def _maybe_birthday_push(self, lanlan: str, shard: _LanlanShard) -> bool:
        """主人生日当天，首次聊天时递一条 read 轻语（说不说、怎么说由她）。

        与纪念日轻语同构但**消息驱动**：玩家开口她才收到，read 语义随她
        回复自然带出祝福，不触发独立回复。per-shard 当天水位存
        stats["birthday"]（盖水位以提交成功为前提，未盖则当天下一条消息重试）；
        keep_diary 开启时顺手在时光日记代笔一条纪念手记（README/功能介绍
        如实说明代笔）。能力中心 birthday 闸；日期未填/非法自然休眠。
        """
        cfg = getattr(self, "_birthday_cfg", None) or {}
        if not self._cap_effective("birthday", lanlan=lanlan):
            return False
        birthday = str(cfg.get("date") or "").strip()
        today = self._stats_today()
        if not birthday_is_today(birthday, today):
            return False
        if not birthday_due(shard.stats, today):
            return False
        pushed = self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": (
                "（日期感知）今天是主人的生日，一年一度属于他的日子。"
                "送上你的祝福，用你的方式庆祝，或者只是让他知道你记得——"
                "说不说、怎么说都由你自己决定，绝不要提及这条提醒本身。"
            )}],
            source=self.plugin_id,
            target_lanlan=lanlan,
            coalesce_key=f"{self.plugin_id}.birthday",
            metadata={"message_type": f"{self.plugin_id}.birthday"},
        )
        # 提交结果必须看（与日记邀请同一契约，1.2.4 审查先例）：submitted=False
        # 表示这条根本没上线，水位不盖——当天下一条消息重试；桩/旧返回按成功处理
        if isinstance(pushed, dict) and pushed.get("submitted") is False:
            self.logger.warning(
                "birthday whisper NOT submitted for {}: {}",
                lanlan, str(pushed.get("reason") or "unknown"),
            )
            return False
        shard.stats = mark_birthday_pushed(shard.stats, today)
        saves = [await self._save_shard_stats(lanlan, shard)]
        keep_diary = cfg.get("keep_diary", True) is not False
        if keep_diary:
            shard.diary.append(make_birthday_diary_record(
                _now_utc().isoformat(timespec="seconds"),
                self._last_phase_name(shard),
            ))
            saves.append(await self._save_shard_diary(lanlan, shard))
        persist_err = self._persist_error(*saves)
        if persist_err is not None:
            # 轻语已上线、水位已盖：落盘失败只影响面板回显，不回滚水位
            # （重推生日祝福比漏一次更碍事），细节由统一出口留痕
            self.logger.warning("birthday watermark/diary persist degraded for {}", lanlan)
        self.logger.info(
            "birthday whisper pushed for {} (keep_diary={})", lanlan, keep_diary,
        )
        return True

