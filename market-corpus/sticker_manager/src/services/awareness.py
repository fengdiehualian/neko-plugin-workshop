"""存在感注入的有状态层（v0.2.0）：什么时候注、注给谁、怎么兜底。

链路（v0.16.0 起挂在 10s `turns` 拍上；独立 `watch` 拍只管 tool_watch）：

1. **什么时候**：**由用户开的新一轮触发**（`services/turns.py` 轮询 `bus.memory` 的
   `user_message`），按 `inject_mode` 决定这轮注不注——每轮都注，或攒够 N 轮注一次；
   两种模式都吃 `min_interval_sec` 这把地板（连珠炮防刷屏）。
   老写法是挂钟 `interval_sec`（默认 3600s）打点，v0.15.0 实机量出来 44 轮对话只覆盖
   12 次提醒、3 次发表情：**瓶颈是提醒没赶上话轮，不是措辞不够狠**。挂钟只在总线一次
   都没读通过时作为降级路径保留（宿主换了桶形状也不许静默失声）。
2. **给谁**：轮次记录自带的归属角色（memory 桶的 `lanlan`）优先，其次
   `services/lanlan.py` 的解析链（入口 ctx → 宿主 `current_catgirl` → 粘滞 → 总线）。
   没人聊天就不注——往不存在的对话里注提示是纯浪费。
3. **注什么**：core/awareness.build_awareness_text（库大小 + 套图分类概览 + 最近常用前 N 行）。
   空库返回空串 = 这拍不该注。
4. **怎么注**：`push_message(visibility=[], ai_behavior="read")`——用户看不见、
   不打断话轮，只进她的上下文（our_life injector 同通道，timer 里可用已被验证）。

三条纪律（与 tool_watch 逐字对齐的形态）：
- **永不炸拍**：maybe_run 吞掉一切异常回 `{"status": "failed"}`——
  timer 无 watchdog，注入坏掉不许连带心跳、更不许把表标黄；
- **随业务冻结**：`[sticker_manager].enabled=false` 时不注（存在感是行为链路，
  与"注册韧性是在场性"不同，这里就该跟着总开关联动）；
- **被拒不推进时钟**：`submitted=False`（本地拦截）时下一拍重试同一条不算轰炸，
  真推进时钟/清计数只发生在交到传输之后（与 sender 的冷却前进条件对偶）。

目标是谁（她在跟哪张角色卡说话）不在本模块判：交给 `services/lanlan.py` 的解析链。
本模块只管节奏与投递。
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.awareness import build_awareness_text, emotional_signal, injection_due_for_turn
from ..core.eagerness import effective_inject_interval_n
from .lanlan import LanlanResolver
from .library import Library
from .turns import TurnWatcher, UserTurn

logger = logging.getLogger("sticker_manager.awareness")


class Awareness:
    """低频存在感注入器。`maybe_run` 每拍调用，内部按角色卡时钟自节流。"""

    def __init__(
        self,
        plugin: Any,
        library: Library,
        *,
        logger: Any = None,
        resolver: LanlanResolver | None = None,
        turns: TurnWatcher | None = None,
        tool_watch: Any = None,
    ):
        self._plugin = plugin
        self._library = library
        self._logger = logger
        self._tool_watch = tool_watch
        # 目标解析交给 LanlanResolver（入口 ctx → 宿主权威 → 粘滞 → 总线）；
        # 缺席时自建一个，形状与老调用点完全兼容。
        self._resolver = resolver or LanlanResolver(plugin, logger=logger)
        # v0.16.0：节奏的驱动源换成轮次（她每开一轮，我们才有一次决策点）。
        self._turns = turns if turns is not None else TurnWatcher(plugin, logger=logger)
        self._last_injected: dict[str, float] = {}
        # 面板可观测面（纯展示，丢了不心疼）：
        self.last_status = ""
        self.last_inject_at = 0.0
        self.last_target = ""
        # v0.20.0：这一枪是谁开的（signal / count / wall_clock）+ 各开了几次。
        # 事件门控的成败只看这两个读数，注入总数会骗人。
        self.last_trigger = ""
        self.trigger_counts: dict[str, int] = {}
        # v0.20.1 事件门控的可解释性：读到的轮里有文本 / 空文本 / 命中情绪各几次。
        # `signal_hits=0` 配 `turn_empty>0` = 总线形状问题；配 `turn_texts>0` = 词表没命中。
        self.turn_texts = 0
        self.turn_empty = 0
        self.signal_hits = 0
        self.last_turn_chars = 0
        self.last_tool_check: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 时钟与快照
    # ------------------------------------------------------------------

    def remaining_for(self, lanlan: str, *, interval_sec: float, now: float) -> float:
        last = self._last_injected.get(lanlan)
        if last is None:
            return 0.0
        return max(0.0, interval_sec - (now - last))

    @property
    def turns_seen(self) -> int:
        """本次运行见过的用户轮总数（v0.17.1 面板读数的分母，转交给轮次看门狗）。"""
        return int(getattr(self._turns, "turns_seen", 0) or 0)

    @staticmethod
    def interval_n(settings: Any) -> int:
        """这一档实际每几轮点名一次（v0.19.0）。

        配置里 `inject_interval_n` 写 0（默认）= 跟随「配表情积极度」档位；
        写正数 = 主人自己定死，档位不再插手。**只裁提醒密度**——
        发送层的四把闸（冷却/去重/概率/复用窗口）一口都不吃档位，陷阱 26 那条没动。
        """
        awareness = getattr(settings, "awareness", None)
        send = getattr(settings, "send", None)
        configured = int(getattr(awareness, "inject_interval_n", 0) or 0)
        tier = str(getattr(send, "eagerness", "natural") or "natural")
        resolved = effective_inject_interval_n(tier, configured)
        # `every_user_message` 档下每轮都注，这把尺不参与；仍返回一个可展示的数。
        return max(1, resolved)

    def snapshot(self, *, settings: Any, now: float) -> dict[str, Any]:
        """给面板的状态行：驱动源活着没、按什么节奏注、下次最快多久以后。"""
        awareness = settings.awareness
        turn_snap = self._turns.snapshot()
        counts = turn_snap["turns_since_inject"]
        # 单个标量给面板：优先最近注入过的那张卡，没注过（target 为空）就看攒得最多的
        # 那张——只回 dict 会让第一次注入之前恒显示 0，看着像"轮次源没工作"。
        turns_since = counts.get(self.last_target) if self.last_target else None
        if turns_since is None:
            turns_since = max(counts.values(), default=0)
        tool_check = dict(self.last_tool_check)
        checked_at = tool_check.get("checked_at")
        if tool_check:
            tool_check["age_sec"] = (
                round(max(0.0, now - checked_at), 3)
                if isinstance(checked_at, (int, float)) and not isinstance(checked_at, bool)
                else None
            )
        return {
            "status": self.last_status,
            "target": self.last_target,
            "last_inject_at": self.last_inject_at or None,
            "driver": turn_snap["source"],
            "inject_mode": awareness.inject_mode,
            # 面板显示的是**生效值**（档位驱动的 3/6/12 或主人写死的数），
            # 回 0 会让人以为"每 0 轮一次"——那是把哨兵值当读数，仪器说谎的一种。
            "inject_interval_n": self.interval_n(settings),
            "min_interval_sec": awareness.min_interval_sec,
            # v0.20.0：点名是谁开的这一枪（signal = 那句用户话带情绪，见 emotional_signal）。
            "last_trigger": self.last_trigger,
            "trigger_counts": dict(self.trigger_counts),
            # v0.20.1：解释 `pointer=0/N` 用的三把数（只记长度与次数，不记原话）。
            "turn_texts": self.turn_texts,
            "turn_empty": self.turn_empty,
            "signal_hits": self.signal_hits,
            "tool_watch": tool_check,
            "turns_since": turns_since,
            "turns_since_inject": counts,
            "min_next_wait_sec": min(
                (
                    self.remaining_for(
                        k, interval_sec=awareness.min_interval_sec, now=now
                    )
                    for k in self._last_injected
                ),
                default=0.0,
            ),
        }

    # ------------------------------------------------------------------
    # 节拍
    # ------------------------------------------------------------------

    async def maybe_run(self, *, settings: Any, now: float) -> dict[str, Any]:
        """自动一拍：先问轮次源有没有新轮，再按模式决定注不注。

        永不抛异常（纪律见模块 docstring），结果只用于可观测。
        """
        try:
            turns = await self._turns.poll_all()
            if not turns:
                return await self._run(settings=settings, now=now, force=False)
            latest: dict[str, UserTurn] = {}
            for turn in turns:
                if settings.enabled and settings.awareness.enabled:
                    self._observe_turn(turn, settings)
                latest[turn.lanlan] = turn
            # Count the full batch, but never send cues for superseded messages.
            result: dict[str, Any] = {}
            for turn in sorted(latest.values(), key=lambda item: item.ts):
                result = await self._run(settings=settings, now=now, force=False, turn=turn, observed=True)
            return result
        except Exception:  # noqa: BLE001 - timer 无 watchdog，一切异常就地消化
            self._log("sticker_manager awareness leaked", exc=True)
            return {"status": "failed"}

    async def inject_now(self, *, settings: Any, lanlan: str, now: float) -> dict[str, Any]:
        """Manual injection using the same gates except the interval."""
        return await self._run(settings=settings, now=now, force=True, lanlan_hint=lanlan)

    async def observe_pending(self, *, settings: Any) -> None:
        """Account for retained turns without injecting a cue during shutdown."""
        turns = await self._turns.poll_all()
        if settings.enabled and settings.awareness.enabled:
            for turn in turns:
                self._observe_turn(turn, settings)

    async def _run(
        self,
        *,
        settings: Any,
        now: float,
        force: bool,
        lanlan_hint: str = "",
        turn: UserTurn | None = None,
        observed: bool = False,
    ) -> dict[str, Any]:
        if not settings.enabled or not settings.awareness.enabled:
            return {"status": "disabled"}
        awareness = settings.awareness
        target = (lanlan_hint or "").strip() or (turn.lanlan if turn is not None else "") or await self._active_lanlan()
        if not target:
            # 没有可归属的角色卡：不推进任何时钟，也不报错——没人说话就没地方注。
            return {"status": "no_target"}
        pointer_event = False
        if not force:
            if turn is None and self._turns.available:
                # 轮次源活着但这拍没有新轮：什么都不做。挂钟模式那条"每小时注一次"
                # 的老路在这里正式退场——v0.15.0 实机 44 轮只覆盖 12 次提醒，
                # 就是这个"到点就注、不管她有没有在说话"造成的。
                return {"status": "idle"}
            if turn is not None:
                waiting_floor = self.remaining_for(
                    target, interval_sec=awareness.min_interval_sec, now=now
                )
                turns_since = self._turns.turns_since(target)
                # v0.20.0 事件门控：这句用户话里有情绪反应时不等计数攒满就点名。
                # 载体仍是 `read`（不是 respond）——同门 `emotion_sense.py:480-520` 那条
                # 她真会照着调的提醒用的就是 read，差别在触发源是"刚读完的这句"。
                event_gated = bool(getattr(awareness, "event_gated", True))
                # v0.20.1 观测：`pointer=0/N` 这种读数必须能解释。两种病完全相反——
                # "这句确实没情绪"（判据正常，别改词表）vs "`turn.text` 是空串"
                # （总线形状变了，事件门控恒假）。只记**长度**，原话一律不落盘（隐私纪律同 sender）。
                self.last_turn_chars = len(str(turn.text or ""))
                pointer_event = (
                    event_gated and emotional_signal(turn.text) if observed else self._observe_turn(turn, settings)
                )
                if not injection_due_for_turn(
                    awareness.inject_mode,
                    turns_since_inject=turns_since,
                    interval_n=self.interval_n(settings),
                    floor_remaining_sec=waiting_floor,
                    signal=pointer_event,
                    event_gated=event_gated,
                ):
                    return {"status": "not_due", "turns_since": turns_since}
            else:
                # 降级：总线一次都没读通过（宿主换了桶形状/总线断开），退回挂钟节奏，
                # 好过整条存在感链路静默死掉。
                waiting = self.remaining_for(target, interval_sec=awareness.interval_sec, now=now)
                if waiting > 0.0:
                    return {"status": "waiting", "wait_sec": round(waiting, 1)}
        text = build_awareness_text(
            self._library.active_pool(),
            # J-1：存在感只报她当前世界（激活区）的家底，不报跨区总量。
            # v0.14.0：意愿段按「配表情积极度」选档——档位只改这段文案，不碰发送层的闸。
            # v0.17.0：目录不再随注入走（它在工具描述里每轮都在场），这里只剩一句"想起来"。
            eagerness=settings.send.eagerness,
            event=pointer_event,
        )
        if not text:
            return {"status": "empty_library"}
        tool_check = await self._before_reminder(target=target, now=now)
        pushed = self._push(text, target)
        if not pushed.get("submitted"):
            reason = str(pushed.get("reason", "push_rejected"))
            self._log(f"awareness push rejected: reason={reason}")
            return {"status": "push_rejected", "reason": reason, "tool_watch": tool_check}
        self._last_injected[target] = now
        # 计数只在真注成功之后清零：被地板/空库挡掉的那些轮不消耗配额，
        # 否则"每 3 轮注一次"会静默退化成"每 4、5 轮注一次"。
        self._turns.reset_count(target)
        self.last_status = "injected"
        self.last_inject_at = now
        self.last_target = target
        # 是哪一路把她叫到这一轮的：signal（这句用户话有情绪）/ count（攒够轮次）/
        # wall_clock（总线不可达的降级）。事件门控上线后要看的是 signal 占比，
        # 不是注入总数——只涨总数说明门控没在工作。
        trigger = (
            "signal" if pointer_event else ("count" if turn is not None else "wall_clock")
        )
        self.last_trigger = trigger
        self.trigger_counts[trigger] = self.trigger_counts.get(trigger, 0) + 1
        self._log(
            f"awareness injected: target={target} chars={len(text)}"
            f" driver={'turn' if turn is not None else 'wall_clock'} trigger={trigger}"
            f" turn_chars={self.last_turn_chars if turn is not None else 0}"
        )
        return {
            "status": "injected",
            "target": target,
            "chars": len(text),
            "trigger": trigger,
            "tool_watch": tool_check,
        }

    # ------------------------------------------------------------------
    # 宿主通道（全部 getattr 化：测试替身/形状缺失一律降级不炸）
    # ------------------------------------------------------------------

    async def _active_lanlan(self) -> str:
        return await self._resolver.resolve()

    async def _before_reminder(self, *, target: str, now: float) -> dict[str, Any]:
        checker = getattr(self._tool_watch, "before_reminder", None)
        if not callable(checker):
            return {}
        try:
            result = await checker(role=target, now=now)
            if not isinstance(result, dict):
                result = {"status": "unknown", "scope": "registry", "role": target}
        except Exception:  # noqa: BLE001 - 观测失败不改变提醒触发与投递
            result = {"status": "watch_failed", "scope": "registry", "role": target}
        self.last_tool_check = dict(result)
        self._log(
            f"awareness tool preflight: scope=registry target={target}"
            f" status={result.get('status', 'unknown')}"
            f" confirmed={bool(result.get('confirmed', False))}"
            f" cached={bool(result.get('cached', False))}"
            f" checked_at={result.get('checked_at')} age_sec={result.get('age_sec')}"
            f" missing_count={len(result.get('missing') or [])}"
        )
        return result

    def _observe_turn(self, turn: UserTurn, settings: Any) -> bool:
        self.last_turn_chars = len(str(turn.text or ""))
        if self.last_turn_chars:
            self.turn_texts += 1
        else:
            self.turn_empty += 1
        signal = bool(getattr(settings.awareness, "event_gated", True)) and emotional_signal(turn.text)
        if signal:
            self.signal_hits += 1
        return signal

    def _push(self, text: str, lanlan: str) -> dict[str, Any]:
        ctx = getattr(self._plugin, "ctx", None)
        push = getattr(ctx, "push_message", None)
        if not callable(push):
            return {"submitted": False, "reason": "transport_unavailable"}
        result = push(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": text}],
            target_lanlan=lanlan,
            # v0.17.0：同一张角色卡的提醒共用一个合并键。宿主为这件事专门造了尺——
            # 带同一个 key 的新 cue 会把还排在队里的旧 cue 收掉（`proactive.py:3330-3340`：
            # "a rapid ai_behavior='read' stream reusing one key would pile up stale
            # snapshots"）。语音模式下 cue 可能排队好几个话轮，不合并就会有一次对话
            # 突然收到三条内容相同的旧提醒。空键=不合并，是宿主给的默认值，我们不用。
            coalesce_key=f"sticker_manager:awareness:{lanlan}",
            description="sticker_manager:awareness",
        )
        return result if isinstance(result, dict) else {"submitted": False}

    def _log(self, message: str, *, exc: bool = False) -> None:
        target = self._logger if self._logger is not None else logger
        try:
            if exc:
                target.warning(message, exc_info=True)
            else:
                target.info(message)
        except Exception:  # noqa: BLE001 - 日志面坏掉不许影响注入
            pass
