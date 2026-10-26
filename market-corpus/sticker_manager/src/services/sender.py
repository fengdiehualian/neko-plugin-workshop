"""发送链路：把一张图落到她的聊天里，并记一次台账。

两条投递通道（由 `SendSettings` 决定走哪条）：
1. **内联**（默认，≤ `inline_max_bytes`）：`parts=[{"type":"image","data":bytes,"mime":...}]`。
   GIF/动画 WebP 默认走这条——上传会归一成 JPEG，压平动画；内联保留原字节。
2. **上传换 URL**（大图 / 显式配置）：`await ctx.images.upload(data)` 拿回
   `{"type":"image","url":...,"mime":"image/jpeg"}` 的 part 再投递。
配套宿主的尾发 HTTP 通道独立于普通消息通道，允许直接保留最多 1 MiB 的原图。

投递用 `visibility=["chat"]`（用户在聊天里看到图）；工具选图无论即时还是排队都用
`ai_behavior="blind"`，避免自产配文与图片反灌到下一轮上下文；
面板与 Agent 路径保留 `ai_behavior="read"`。
`submitted=True` 只代表已交给传输，不代表宿主已消费（our_life 陷阱 §5）。

冷却是**按角色卡的内存表**：不持久化。跨重启的发送节奏没必要留痕，
反而持久化会让"刚重启就误判冷却中"这种体验变糟。

去重（轮 D①）与冷却相反，读的是**持久台账**（usage.json 里该角色卡最近
 N 张成功发送的 distinct id）："最近发过什么"是事实记忆，重启不该失忆。
概率闸门（轮 D②）掷后复用，判定缓存是内存表（重启重掷，与冷却同纪律）：
同一角色卡在复用窗口内只掷一次——multi_candidates→拿 id 二次定夺是同一次
意愿的延续，不重掷（外部系统的 p² 教训）。两者都拦 `source` 为 tool 或 agent 的模型路径：
面板"试发"是主人的直接动作，不该被她的行为节奏闸拦下。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Any

from ..core.catalog import Sticker, detect_image_format
from ..core.configuration import StickerManagerSettings
from ..core.media import preserves_animation
from .library import Library
from .official_delivery import OfficialDelivery
from .turn_end import EndTicket, TurnEndLogs

# 稳定错误码（模型/面板各自的文案层翻译它们）
ERR_NOT_ENABLED = "not_enabled"
ERR_MISSING_FILE = "sticker_file_missing"
ERR_BAD_IMAGE = "sticker_image_unreadable"
ERR_TOO_LARGE = "sticker_too_large"
ERR_COOLDOWN = "send_cooldown"
ERR_TRANSPORT = "transport_unavailable"
# 轮 D 新增：两个节奏闸的稳定码（进台账、面板可翻译）。
ERR_RECENT_REPEAT = "recent_repeat"
ERR_PROBABILITY = "probability_declined"

# 掷骰用模块级实例：测试里 monkeypatch `sender._RNG` 即可钉死随机序列。
_RNG = random.Random()
_TAIL_TIMEOUT_SEC = 300.0
_MAX_PENDING = 8
_MAX_HOST_ACCOUNTED = 512
# V1 accepts 2 MiB of JSON parts. One 1 MiB image leaves ample base64 headroom.
_HOST_REPLY_TAIL_INLINE_MAX_BYTES = 1024 * 1024


@dataclass
class SendResult:
    ok: bool
    code: str = ""
    detail: str = ""
    sticker_id: str = ""
    desc: str = ""
    queued: bool = False
    text_submitted: bool = False
    failure_recorded: bool = False
    delivery_recorded: bool = False

    @classmethod
    def failure(
        cls, code: str, *, sticker_id: str = "", detail: str = "", text_submitted: bool = False,
    ) -> "SendResult":
        return cls(
            ok=False, code=code, sticker_id=sticker_id, detail=detail,
            text_submitted=text_submitted,
        )

    @classmethod
    def success(cls, sticker: Sticker, *, text_submitted: bool = False) -> "SendResult":
        return cls(
            ok=True, sticker_id=sticker.id, desc=sticker.desc, text_submitted=text_submitted,
        )


@dataclass
class PendingSend:
    sticker: Sticker
    parts: list[dict[str, Any]]
    ticket: EndTicket
    source: str
    started: float
    text_len: int
    display_buffer_sec: float
    end_observed_at: float | None = None


@dataclass
class PendingHostSend:
    sticker: Sticker
    context: dict[str, Any]
    registration_id: str
    client: Any
    started: float
    text_len: int
    duplicate: bool = False
    registering: bool = True
    uncertain: bool = False
    cancel_reason: str = ""
    reservation: object | None = None
    terminal_result: SendResult | None = None
    terminal_deferred: bool = False


class Sender:
    """发送器。`plugin` 提供 `ctx`（push_message / images）。"""

    def __init__(
        self, plugin: Any, library: Library, *, logger: Any = None, turn_end: TurnEndLogs | None = None
    ):
        self._plugin = plugin
        self._library = library
        self._logger = logger
        self._last_sent: dict[str, float] = {}  # lanlan -> 上次成功投递的时刻
        self._prob_rolls: dict[str, tuple[float, bool]] = {}  # lanlan -> (掷骰时刻, 命中)
        self._inflight: set[str] = set()
        self._inflight_owners: dict[str, object] = {}
        self._inflight_lock = threading.Lock()
        self._turn_end = turn_end
        self._pending: dict[str, PendingSend] = {}
        self._host_pending: dict[str, PendingHostSend] = {}
        self._host_accounted: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._drain_lock = threading.Lock()
        self._closed = False
        self._epoch = 0
        self._official_delivery: OfficialDelivery | None = None
        self._attempt_versions: dict[str, int] = {}

    def activity(self, lanlan: str) -> tuple[int, bool, float]:
        """A fallback must not compete with a normal or pending send."""
        with self._inflight_lock:
            return (
                self._attempt_versions.get(lanlan, 0),
                lanlan in self._inflight,
                self._last_sent.get(lanlan, 0.0),
            )

    def _log(self, message: str, *, exc: bool = False) -> None:
        if self._logger is None:
            return
        try:
            if exc:
                self._logger.warning(message, exc_info=True)
            else:
                self._logger.info(message)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 冷却
    # ------------------------------------------------------------------

    def cooldown_remaining(self, lanlan: str, settings: StickerManagerSettings, *, now: float) -> float:
        if settings.send.cooldown_sec <= 0.0:
            return 0.0
        last = self._last_sent.get(lanlan)
        if last is None:
            return 0.0
        return max(0.0, settings.send.cooldown_sec - (now - last))

    # ------------------------------------------------------------------
    # 节奏闸（轮 D）
    # ------------------------------------------------------------------

    def recent_sent_ids(self, lanlan: str, settings: StickerManagerSettings) -> set[str]:
        """该角色卡最近 `recent_dedup_count` 张**成功发出**的 distinct id（台账源，跨重启）。

        读整本台账（封顶 usage_history_keep，文件很小）再筛：ok=True 且角色卡匹配，
        新的在前，取前 N 个 distinct id。一次成功发送只占一个名额（同一张连发两次
        不挤掉更早的另一张）。
        """
        n = settings.send.recent_dedup_count
        if n <= 0:
            return set()
        out: set[str] = set()
        for row in self._library.read_usage(limit=settings.storage.usage_history_keep):
            if not row.get("ok"):
                continue
            if str(row.get("lanlan") or "") != lanlan:
                continue
            sid = str(row.get("id") or "")
            if sid:
                out.add(sid)
                if len(out) >= n:
                    break
        return out

    def probability_allow(self, lanlan: str, settings: StickerManagerSettings, *, now: float) -> bool:
        """概率闸门：掷一次、缓存、复用窗口内不重掷（防 p²）。

        `probability >= 1.0` 短路放行（默认关闭，不白耗随机）；判定缓存是内存表，
        重启重掷——与冷却同纪律（DESIGN 陷阱 9 的适用面就是节奏状态，去重不在内）。
        """
        p = settings.send.probability
        if p >= 1.0:
            return True
        rolled = self._prob_rolls.get(lanlan)
        if rolled is not None and (now - rolled[0]) < settings.send.probability_reuse_sec:
            return rolled[1]
        hit = _RNG.random() < p
        self._prob_rolls[lanlan] = (now, hit)
        return hit

    # ------------------------------------------------------------------
    # 投递
    # ------------------------------------------------------------------

    async def send(
        self,
        sticker: Sticker,
        *,
        lanlan: str,
        settings: StickerManagerSettings,
        source: str,
        now: float | None = None,
        force: bool = False,
        text: str = "",
        host_reply: dict[str, Any] | None = None,
        expected_activity: int | None = None,
        submission_guard: Callable[[], bool] | None = None,
        submission_lock: Any = None,
    ) -> SendResult:
        """投递一张表情包并记账。source 为 tool / agent / panel，不面向用户。

        `text` 非空时先投配文消息，再投独立图片消息，仍共用一次排队、节奏判定与计数。
        先准备图片，准备失败时不投配文；传输没有事务或回滚，配文提交后图片仍可能失败，
        此时通过 `text_submitted` 如实报告部分提交，不重发，也不算发表情成功。
        两条消息署名仍是插件，不冒充她自己的回复气泡。
        """
        moment = time.time() if now is None else now
        if source == "tool" and host_reply is not None:
            with self._inflight_lock:
                previous = self._host_pending.get(lanlan)
                reconcile = previous is not None and not previous.registering
            if reconcile:
                outcomes = await self._drain_host(settings=settings, target_lanlan=lanlan)
                observer = getattr(self._plugin, "_note_deferred_outcomes", None)
                if callable(observer):
                    observer(outcomes)
        # Reservation spans awaits and works across the host's separate event loops.
        with self._inflight_lock:
            if self._closed:
                return SendResult.failure("send_stopped", sticker_id=sticker.id)
            if lanlan in self._inflight:
                return SendResult.failure(ERR_COOLDOWN, sticker_id=sticker.id)
            if expected_activity is not None and expected_activity != self._attempt_versions.get(lanlan, 0):
                return SendResult.failure("reply_attachment_superseded", sticker_id=sticker.id)
            if source != "eager":
                self._attempt_versions[lanlan] = self._attempt_versions.get(lanlan, 0) + 1
            self._inflight.add(lanlan)
            reservation = object()
            self._inflight_owners[lanlan] = reservation
            epoch = self._epoch
        handed_off = False
        try:
            result = await self._send_reserved(
                sticker, lanlan=lanlan, settings=settings, source=source, now=moment,
                force=force, text=text, epoch=epoch,
                host_reply=host_reply,
                submission_guard=submission_guard,
                submission_lock=submission_lock,
            )
            handed_off = result.queued
            return result
        finally:
            with self._inflight_lock:
                if (
                    not handed_off and lanlan not in self._host_pending
                    and self._inflight_owners.get(lanlan) is reservation
                ):
                    self._release_reservation(lanlan, reservation)

    async def _send_reserved(
        self,
        sticker: Sticker,
        *,
        lanlan: str,
        settings: StickerManagerSettings,
        source: str,
        now: float,
        force: bool,
        text: str,
        epoch: int,
        host_reply: dict[str, Any] | None,
        submission_guard: Callable[[], bool] | None = None,
        submission_lock: Any = None,
    ) -> SendResult:
        moment = now
        started = time.monotonic()
        if not settings.enabled:
            return SendResult.failure(ERR_NOT_ENABLED, sticker_id=sticker.id)
        if sticker.disabled:
            # 被主人禁用的图不该从任何通道漏出去。
            return SendResult.failure("sticker_disabled", sticker_id=sticker.id)
        remaining = self.cooldown_remaining(lanlan, settings, now=moment)
        if remaining > 0.0:
            return SendResult.failure(ERR_COOLDOWN, sticker_id=sticker.id)
        # Both model paths share the rhythm gates; panel sends keep their existing behavior.
        # （冷却仍生效——那是防刷屏，不是表达问题）。先查重（确定性）再掷骰，
        # 不让重复图白耗一次判定。
        if source in {"tool", "agent", "eager"} and not force:
            if sticker.id in self.recent_sent_ids(lanlan, settings):
                return SendResult.failure(ERR_RECENT_REPEAT, sticker_id=sticker.id)
            if not self.probability_allow(lanlan, settings, now=moment):
                return SendResult.failure(ERR_PROBABILITY, sticker_id=sticker.id)

        try:
            data = self._library.image_path(sticker).read_bytes()
        except FileNotFoundError:
            return SendResult.failure(ERR_MISSING_FILE, sticker_id=sticker.id)
        except Exception:
            return SendResult.failure(ERR_BAD_IMAGE, sticker_id=sticker.id)
        detected = detect_image_format(data)
        if detected is None:
            return SendResult.failure(ERR_BAD_IMAGE, sticker_id=sticker.id)
        _ext, mime = detected
        host_client = None
        if source == "tool" and settings.send.host_reply_tail and host_reply is not None:
            try:
                host_client = getattr(self._plugin.ctx, "reply_tail", None)
            except (AttributeError, RuntimeError):
                host_client = None
            if not all(callable(getattr(host_client, name, None)) for name in ("register", "status", "cancel")):
                host_client = None
            if host_client is not None and host_reply.get("role") != lanlan:
                return SendResult.failure("reply_tail_target_mismatch", sticker_id=sticker.id)

        inline_budget = (
            _HOST_REPLY_TAIL_INLINE_MAX_BYTES if host_client is not None else settings.send.inline_max_bytes
        )
        if len(data) > inline_budget:
            pack_path = getattr(self._plugin, "_official_pack_path", None)
            if callable(pack_path):
                try:
                    if self._official_delivery is None:
                        self._official_delivery = OfficialDelivery(pack_path())
                    variant = self._official_delivery.resolve(data)
                except Exception:
                    self._log("official delivery asset unavailable", exc=True)
                    return SendResult.failure(ERR_BAD_IMAGE, sticker_id=sticker.id)
                if variant is not None:
                    data = variant
                    mime = "image/webp"
                    self._log(f"official delivery variant: id={sticker.id} bytes={len(data)}")

        ticket = None
        if host_client is None and self._turn_end is not None and (
            source == "agent" or (source == "tool" and settings.send.defer_tool_sends)
        ):
            # Tool ordering is opt-in: log-based waiting can lose selections
            # on the next input. Background Agent requests retain their guard.
            defer = source == "tool"
            if source == "agent":
                states = await asyncio.to_thread(self._turn_end.busy_states)
                defer = states.get(lanlan) is not False
            if defer:
                ticket = self._turn_end.arm(lanlan)
                if ticket is None:
                    return SendResult.failure("turn_end_unavailable", sticker_id=sticker.id)

        part = await self._build_part(data, mime, settings=settings, inline_budget=inline_budget)
        if part is None:
            return SendResult.failure(ERR_TOO_LARGE, sticker_id=sticker.id)

        # 图片准备完成后才允许投配文；有配文的两条消息共用这次预约与排队。
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        parts.append(part)
        if host_client is not None:
            # Extra captions keep their existing independent channel; the
            # normal reply is never moved into plugin-generated text.
            text_submitted = False
            if text:
                with self._inflight_lock:
                    if self._closed or self._epoch != epoch:
                        return SendResult.failure("send_stopped", sticker_id=sticker.id)
                    with self._library.locked():
                        current, code = self._current_for_submission(sticker.id, source=source)
                        if current is None or code:
                            return SendResult.failure(code or ERR_MISSING_FILE, sticker_id=sticker.id)
                        sticker = current
                        try:
                            text_submitted = bool(
                                self._push([parts[0]], lanlan=lanlan, ai_behavior="blind").get("submitted")
                            )
                        except Exception:
                            text_submitted = False
                if not text_submitted:
                    return SendResult.failure(ERR_TRANSPORT, sticker_id=sticker.id)
            result = await self._submit_host(
                sticker, part, lanlan=lanlan, settings=settings, client=host_client,
                context=host_reply, text_len=len(text), epoch=epoch,
            )
            result.text_submitted = text_submitted
            return result
        with (submission_lock if submission_lock is not None else nullcontext()), self._inflight_lock:
            if self._closed:
                return SendResult.failure("send_stopped", sticker_id=sticker.id)
            if epoch != self._epoch:
                return SendResult.failure(ERR_NOT_ENABLED, sticker_id=sticker.id)
            if submission_guard is not None and not submission_guard():
                return SendResult.failure("reply_attachment_superseded", sticker_id=sticker.id)
            if ticket is not None:
                with self._library.locked():
                    current, code = self._current_for_submission(sticker.id, source=source)
                if current is None or code:
                    return SendResult.failure(code or ERR_MISSING_FILE, sticker_id=sticker.id)
                if len(self._pending) >= _MAX_PENDING:
                    return SendResult.failure("send_queue_full", sticker_id=sticker.id)
                self._pending[lanlan] = PendingSend(
                    current, parts, ticket, source, time.monotonic(), len(text),
                    settings.send.reply_tail_display_buffer_sec,
                )
                self._log(
                    f"sticker queued: id={sticker.id} source={source} "
                    f"buffer_sec={settings.send.reply_tail_display_buffer_sec:g}"
                )
                return SendResult(ok=True, sticker_id=current.id, desc=current.desc, queued=True)

            moment += max(0.0, time.monotonic() - started)
            return self._submit(
                sticker, parts, lanlan=lanlan, settings=settings,
                source=source, moment=moment, text_len=len(text),
                submission_guard=submission_guard,
            )

    def _submit(
        self, sticker: Sticker, parts: list[dict[str, Any]], *, lanlan: str,
        settings: StickerManagerSettings, source: str, moment: float, text_len: int,
        submission_guard: Callable[[], bool] | None = None,
    ) -> SendResult:
        # Uploads yield control. Recheck current state and submit under the same
        # lock as management writes so an intervening disable/delete cannot leak.
        with self._library.locked():
            current, code = self._current_for_submission(sticker.id, source=source)
            if current is None or code:
                return SendResult.failure(code or ERR_MISSING_FILE, sticker_id=sticker.id)
            if submission_guard is not None and not submission_guard():
                return SendResult.failure("reply_attachment_superseded", sticker_id=sticker.id)
            return self._submit_checked(
                current, parts, lanlan=lanlan, settings=settings,
                source=source, moment=moment, text_len=text_len,
            )

    def _current_for_submission(self, sticker_id: str, *, source: str) -> tuple[Sticker | None, str]:
        loaded = self._library.load()
        if not loaded.ok:
            return None, loaded.code
        current = self._library.get(sticker_id)
        if current is None or not self._library.image_path(current).is_file():
            return None, ERR_MISSING_FILE
        if current.disabled:
            return None, "sticker_disabled"
        if source in {"tool", "agent", "eager"} and self._library.zone_of_sticker(current) != self._library.active_zone():
            return None, "sticker_zone_changed"
        return current, ""

    def _submit_checked(
        self, sticker: Sticker, parts: list[dict[str, Any]], *, lanlan: str,
        settings: StickerManagerSettings, source: str, moment: float, text_len: int,
    ) -> SendResult:
        # Tool results already describe the selection. Reading our own image
        # again would make the next text-chat turn switch to a vision model.
        ai_behavior = "blind" if source in {"tool", "eager"} else "read"
        text_submitted = False
        for part in parts:
            partial = " text_submitted=True" if text_submitted else ""
            detail = "caption submitted; image not submitted" if text_submitted else ""
            try:
                pushed = self._push([part], lanlan=lanlan, ai_behavior=ai_behavior)
            except Exception:
                self._log(f"push failed{partial}", exc=True)
                return SendResult.failure(
                    ERR_TRANSPORT, sticker_id=sticker.id, detail=detail,
                    text_submitted=text_submitted,
                )
            if not pushed.get("submitted"):
                reason = str(pushed.get("reason", ERR_TRANSPORT))
                self._log(f"push rejected: reason={reason}{partial}")
                return SendResult.failure(
                    reason or ERR_TRANSPORT, sticker_id=sticker.id, detail=detail,
                    text_submitted=text_submitted,
                )
            if part["type"] == "text":
                text_submitted = True

        self._record_submission(
            sticker, lanlan=lanlan, settings=settings, source=source,
            moment=moment, text_len=text_len,
        )
        return SendResult.success(sticker, text_submitted=text_submitted)

    def _record_submission(
        self, sticker: Sticker, *, lanlan: str, settings: StickerManagerSettings,
        source: str, moment: float, text_len: int,
    ) -> None:
        # Registration is not submission. Neither path counts merely queued images.
        self._last_sent[lanlan] = moment
        self._library.touch_used(sticker.id, now=moment)
        self._library.append_usage(
            {
                "at": moment,
                "id": sticker.id,
                "lanlan": lanlan or "",
                "source": source,
                "ok": True,
                # 配文只留长度痕：**只记长度不记内容**——那句是她生成的话，
                # 台账的既有纪律是"只放非隐私字段"（时刻/id/角色/来源/成败）。
                "text_len": text_len,
            },
            keep=settings.storage.usage_history_keep,
        )
        self._log(
            f"sticker sent: id={sticker.id} source={source} "
            f"ai_behavior={'blind' if source in {'tool', 'eager'} else 'read'}"
            + (f" text_len={text_len}" if text_len else "")
        )

    async def _submit_host(
        self, sticker: Sticker, part: dict[str, Any], *, lanlan: str,
        settings: StickerManagerSettings, client: Any, context: dict[str, Any],
        text_len: int, epoch: int,
    ) -> SendResult:
        call_key = hashlib.sha256(str(context.get("token", "")).encode("utf-8")).hexdigest()[:16]
        pending = PendingHostSend(
            sticker, dict(context), f"sticker:{sticker.id}:{call_key}", client, time.monotonic(), text_len,
        )
        with self._inflight_lock:
            if self._closed or self._epoch != epoch:
                return SendResult.failure("send_stopped", sticker_id=sticker.id)
            with self._library.locked():
                current, code = self._current_for_submission(sticker.id, source="tool")
                if current is None or code:
                    return SendResult.failure(code or ERR_MISSING_FILE, sticker_id=sticker.id)
                pending.sticker = current
            if len(self._host_pending) >= _MAX_PENDING:
                return SendResult.failure("send_queue_full", sticker_id=sticker.id)
            pending.reservation = self._inflight_owners.get(lanlan)
            self._host_pending[lanlan] = pending
        registration_started = time.perf_counter()
        try:
            receipt = await client.register(
                pending.context, registration_id=pending.registration_id,
                parts=[part], ai_behavior="blind", timeout=5.0,
            )
        except asyncio.CancelledError:
            with self._inflight_lock:
                pending.uncertain = True
                pending.registering = False
            raise
        except Exception:
            # The HTTP request may have reached the host. Query this identity,
            # never fall back to another immediate image on uncertainty.
            receipt = {"status": "uncertain", "accepted": False}
        if not isinstance(receipt, dict):
            receipt = {"status": "uncertain", "accepted": False}
        state = receipt.get("status")
        self._log(
            f"sticker host tail registration: id={sticker.id}"
            f" elapsed_ms={(time.perf_counter() - registration_started) * 1000:.1f} state={state}"
        )
        with self._inflight_lock:
            still_owned = self._host_pending.get(lanlan) is pending and not self._closed and self._epoch == epoch
            pending.uncertain = state == "uncertain"
            pending.duplicate = bool(receipt.get("duplicate"))
            pending.registering = False
            if receipt.get("accepted") is True and state == "submitted":
                return self._settle_host(
                    pending, lanlan=lanlan, settings=settings, receipt=receipt,
                )
            if pending.terminal_result is not None:
                return self._late_host_result(pending)
            if still_owned:
                if state in {"registered", "uncertain"}:
                    self._log(f"sticker host tail queued: id={sticker.id} state={state}")
                    return SendResult(
                        ok=True, queued=True, sticker_id=sticker.id, desc=pending.sticker.desc,
                        detail="host_reply_tail_uncertain" if state == "uncertain" else "host_reply_tail",
                    )
                return self._settle_host(
                    pending, lanlan=lanlan, settings=settings, receipt=receipt,
                )
        try:
            cancellation = await client.cancel(
                pending.context, registration_id=pending.registration_id, timeout=5.0,
            )
        except Exception:
            cancellation = {"status": "uncertain"}
        if not isinstance(cancellation, dict):
            cancellation = {"status": "uncertain"}
        with self._inflight_lock:
            if cancellation.get("status") == "submitted" and cancellation.get("accepted") is True:
                return self._settle_host(
                    pending, lanlan=lanlan, settings=settings, receipt=cancellation,
                )
            if pending.terminal_result is not None:
                return self._late_host_result(pending)
            if cancellation.get("status") in {"registered", "uncertain"}:
                if (
                    self._host_pending.get(lanlan) is pending and not self._closed
                    and time.monotonic() - pending.started < _TAIL_TIMEOUT_SEC
                ):
                    pending.cancel_reason = pending.cancel_reason or "send_stopped"
                    pending.uncertain = True
                    return SendResult(
                        ok=True, queued=True, sticker_id=sticker.id, desc=sticker.desc,
                        detail="host_reply_tail_uncertain",
                    )
                reason = "reply_tail_cancel_uncertain"
            else:
                reason = pending.cancel_reason or "send_stopped"
            return self._settle_host(
                pending, lanlan=lanlan, settings=settings, receipt=cancellation, reason=reason,
            )

    def _release_reservation(self, lanlan: str, reservation: object | None = None) -> None:
        if reservation is not None and self._inflight_owners.get(lanlan) is not reservation:
            return
        self._inflight.discard(lanlan)
        self._inflight_owners.pop(lanlan, None)

    @staticmethod
    def _late_host_result(pending: PendingHostSend) -> SendResult:
        result = pending.terminal_result
        assert result is not None
        if not pending.terminal_deferred and not result.delivery_recorded:
            return result
        # Claiming the original for the observer also covers its cross-loop delivery window.
        return replace(
            result, delivery_recorded=True,
            detail="host_reply_tail_duplicate" if result.ok else result.detail,
        )

    def _settle_host(
        self, pending: PendingHostSend, *, lanlan: str, settings: StickerManagerSettings,
        receipt: dict[str, Any], reason: str = "",
    ) -> SendResult:
        """Claim a terminal receipt once while the caller holds the role lock."""
        submitted = receipt.get("status") == "submitted" and receipt.get("accepted") is True
        previous = pending.terminal_result
        if previous is not None and (previous.ok or not submitted):
            return self._late_host_result(pending)
        if self._host_pending.get(lanlan) is pending:
            self._host_pending.pop(lanlan)
            self._release_reservation(lanlan, pending.reservation)
        if submitted:
            identity = (
                str(pending.context.get("reply_id", "")),
                str(pending.context.get("source", "")),
                pending.registration_id,
            )
            duplicate = pending.duplicate or identity in self._host_accounted
            if not duplicate:
                self._record_submission(
                    pending.sticker, lanlan=lanlan, settings=settings, source="tool",
                    moment=self._submission_time(receipt), text_len=pending.text_len,
                )
            self._host_accounted[identity] = None
            self._host_accounted.move_to_end(identity)
            while len(self._host_accounted) > _MAX_HOST_ACCOUNTED:
                self._host_accounted.popitem(last=False)
            result = SendResult.success(pending.sticker)
            if duplicate:
                result.detail = "host_reply_tail_duplicate"
        else:
            code = str(reason or receipt.get("reason") or "reply_tail_rejected")
            self.note_attempt_failed(
                lanlan=lanlan, sticker_id=pending.sticker.id, code=code,
                settings=settings, now=time.time(), source="tool",
                text_submitted=pending.text_len > 0,
            )
            result = SendResult.failure(code, sticker_id=pending.sticker.id)
            result.failure_recorded = True
        pending.terminal_result = result
        pending.terminal_deferred = False
        return result

    @staticmethod
    def _submission_time(receipt: dict[str, Any]) -> float:
        now = time.time()
        value = receipt.get("submitted_at")
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            return min(now, value)
        return now

    async def _drain_host(
        self, *, settings: StickerManagerSettings, cancel: bool = False, target_lanlan: str | None = None,
    ) -> list[tuple[str, SendResult]]:
        outcomes: list[tuple[str, SendResult]] = []
        with self._inflight_lock:
            snapshot = list(self._host_pending.items())
        loaded = self._library.load() if snapshot else None
        for lanlan, pending in snapshot:
            if target_lanlan is not None and lanlan != target_lanlan:
                continue
            if pending.registering and not cancel and settings.enabled and not self._closed:
                continue
            sticker = self._library.get(pending.sticker.id)
            code = pending.cancel_reason
            if cancel or self._closed:
                code = "send_stopped"
            elif not settings.enabled:
                code = ERR_NOT_ENABLED
            elif not settings.send.host_reply_tail:
                code = "reply_tail_disabled"
            elif loaded is not None and not loaded.ok:
                code = loaded.code
            elif sticker is None or not self._library.image_path(sticker).is_file():
                code = ERR_MISSING_FILE
            elif sticker.disabled:
                code = "sticker_disabled"
            elif sticker.id not in {item.id for item in self._library.active_pool()}:
                code = "sticker_zone_changed"
            elif time.monotonic() - pending.started >= _TAIL_TIMEOUT_SEC:
                code = "reply_tail_timeout"
            if code:
                pending.cancel_reason = code
            try:
                method = pending.client.cancel if code else pending.client.status
                receipt = await method(pending.context, registration_id=pending.registration_id, timeout=5.0)
            except Exception:
                receipt = {"status": "uncertain"}
            state = receipt.get("status") if isinstance(receipt, dict) else "uncertain"
            # A submission that won the cancellation race cannot be undone.
            submitted = state == "submitted" and receipt.get("accepted") is True
            if code and state in {"registered", "uncertain"}:
                if not self._closed and time.monotonic() - pending.started < _TAIL_TIMEOUT_SEC:
                    continue
                code = "reply_tail_cancel_uncertain"
            if (
                not code and pending.uncertain and state == "failed"
                and receipt.get("reason") == "registration_not_found"
            ):
                continue
            if not submitted and not code and state in {"registered", "uncertain"}:
                continue
            with self._inflight_lock:
                if self._host_pending.get(lanlan) is not pending:
                    continue
                result = self._settle_host(
                    pending, lanlan=lanlan, settings=settings,
                    receipt=receipt if isinstance(receipt, dict) else {}, reason=code,
                )
                pending.terminal_deferred = True
                outcomes.append(("tool", result))
        return outcomes

    async def cancel_host_pending(self, *, settings: StickerManagerSettings) -> list[tuple[str, SendResult]]:
        # May overlap a status poll during shutdown. The ownership check after
        # each await ensures that only one path records the terminal outcome.
        return await self._drain_host(settings=settings, cancel=True)

    async def drain(self, *, settings: StickerManagerSettings) -> list[tuple[str, SendResult]]:
        """Buffer display after generation ends, watching for superseding input.

        The buffer is a configurable compatibility aid, not a frontend completion
        signal. Each timer tick continues to read new logs and the busy guard.
        """
        if not self._drain_lock.acquire(blocking=False):
            return []
        outcomes: list[tuple[str, SendResult]] = []
        try:
            outcomes.extend(await self._drain_host(settings=settings))
            if self._turn_end is None:
                return outcomes
            with self._inflight_lock:
                snapshot = list(self._pending.items())
            states = None
            for lanlan, pending in snapshot:
                code = ""
                ready = False
                if not settings.enabled:
                    code = ERR_NOT_ENABLED
                elif time.monotonic() - pending.started >= _TAIL_TIMEOUT_SEC:
                    code = "turn_end_timeout"
                else:
                    ready = await asyncio.to_thread(self._turn_end.poll, pending.ticket)
                    if pending.ticket.matched and pending.end_observed_at is None:
                        pending.end_observed_at = time.monotonic()
                        self._log(
                            f"sticker tail observed: id={pending.sticker.id} "
                            f"buffer_sec={pending.display_buffer_sec:g}"
                        )
                    if pending.ticket.invalid:
                        code = "turn_end_lost"
                    elif pending.ticket.superseded:
                        code = "turn_superseded"
                    elif pending.ticket.matched:
                        if states is None:
                            states = await asyncio.to_thread(self._turn_end.busy_states)
                        busy = states.get(lanlan)
                        if busy is True:
                            code = "turn_superseded"
                        elif time.monotonic() - pending.end_observed_at < pending.display_buffer_sec:
                            ready = False
                with self._inflight_lock:
                    if self._pending.get(lanlan) is not pending:
                        continue
                    if not code:
                        loaded = self._library.load()
                        sticker = self._library.get(pending.sticker.id)
                        if not loaded.ok:
                            code = loaded.code
                        elif sticker is None or not self._library.image_path(sticker).is_file():
                            code = ERR_MISSING_FILE
                        elif sticker.disabled:
                            code = "sticker_disabled"
                        elif sticker.id not in {s.id for s in self._library.active_pool()}:
                            code = "sticker_zone_changed"
                    if not code and not ready:
                        continue
                    try:
                        if code:
                            result = SendResult.failure(code, sticker_id=pending.sticker.id)
                        else:
                            result = self._submit(
                                sticker, pending.parts, lanlan=lanlan, settings=settings,
                                source=pending.source, moment=time.time(), text_len=pending.text_len,
                            )
                        if not result.ok:
                            self.note_attempt_failed(
                                lanlan=lanlan, sticker_id=pending.sticker.id, code=result.code,
                                settings=settings, now=time.time(), source=pending.source,
                                text_submitted=result.text_submitted,
                            )
                            end_wait = (
                                "no-end" if pending.end_observed_at is None
                                else f"{max(0.0, time.monotonic() - pending.end_observed_at):.2f}"
                            )
                            self._log(
                                f"sticker deferred rejected: id={pending.sticker.id} "
                                f"reason={result.code} end_wait_sec={end_wait}"
                                + (" text_submitted=True" if result.text_submitted else "")
                            )
                        outcomes.append((pending.source, result))
                    finally:
                        self._pending.pop(lanlan, None)
                        self._release_reservation(lanlan)
            return outcomes
        finally:
            self._drain_lock.release()

    def cancel_pending(
        self, *, settings: StickerManagerSettings, closing: bool = False
    ) -> list[tuple[str, SendResult]]:
        outcomes = []
        with self._inflight_lock:
            self._closed = self._closed or closing
            self._epoch += 1
            for lanlan, pending in list(self._pending.items()):
                code = "send_stopped" if closing else ERR_NOT_ENABLED
                self.note_attempt_failed(
                    lanlan=lanlan, sticker_id=pending.sticker.id, code=code,
                    settings=settings, now=time.time(), source=pending.source,
                )
                outcomes.append((pending.source, SendResult.failure(code, sticker_id=pending.sticker.id)))
                self._release_reservation(lanlan)
            self._pending.clear()
        return outcomes

    async def _build_part(
        self, data: bytes, mime: str, *, settings: StickerManagerSettings, inline_budget: int | None = None,
    ) -> dict[str, Any] | None:
        """构造 push_message 的 image part；无法投递时返回 None。

        配文是独立消息，不占用图片的内联预算。
        """
        if inline_budget is None:
            inline_budget = settings.send.inline_max_bytes
        if preserves_animation(data, mime) and not settings.send.animated_via_upload:
            # GIF and animated WebP must keep their original bytes.
            if len(data) > inline_budget:
                return None
            return {"type": "image", "data": data, "mime": mime}
        if len(data) <= inline_budget:
            return {"type": "image", "data": data, "mime": mime}
        # 大图：换 URL part。
        try:
            upload = await self._plugin.ctx.images.upload(data, mime=mime, timeout=10.0)
        except Exception:
            self._log("image upload failed", exc=True)
            return None
        if isinstance(upload, dict) and upload.get("type") == "image":
            return dict(upload)
        return None

    def _push(
        self, parts: list[dict[str, Any]], *, lanlan: str, ai_behavior: str = "read"
    ) -> dict[str, Any]:
        ctx = self._plugin.ctx
        kwargs: dict[str, Any] = {
            "visibility": ["chat"],
            "ai_behavior": ai_behavior,
            "parts": parts,
            "description": "sticker_manager:send",
        }
        if lanlan:
            kwargs["target_lanlan"] = lanlan
        result = ctx.push_message(**kwargs)
        if isinstance(result, dict):
            return result
        return {"submitted": False, "reason": ERR_TRANSPORT}

    def note_attempt_failed(
        self, *, lanlan: str, sticker_id: str, code: str, settings, now: float,
        source: str = "tool", text_submitted: bool = False,
    ) -> None:
        """失败的发送也进台账（ok=False），面板"她最近想用但没成"看得见。"""
        entry = {
            "at": now,
            "id": sticker_id,
            "lanlan": lanlan or "",
            "source": source,
            "ok": False,
            "code": code,
        }
        if text_submitted:
            entry["text_submitted"] = True
        self._library.append_usage(entry, keep=settings.storage.usage_history_keep)
