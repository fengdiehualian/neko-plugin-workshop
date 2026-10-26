"""Optional Neko narration of observed changes; never drives the Minecraft body."""

import time
from dataclasses import dataclass

from agent_mc_autonomy.commentary import commentary_observation
from agent_mc_autonomy.configuration import Settings, _boolean, _integer, _text
from plugin.sdk.plugin import Ok, plugin_entry, timer_interval, ui

from .neko_commentary import SPEAK, commentary_plan, load_commentary, save_commentary
from .neko_heartbeat import HeartbeatSettings
from .neko_tools import unwrap


@dataclass(frozen=True)
class PresentationSettings:
    enabled: bool = False
    target: str = ""
    min_interval_seconds: int = 90

    @classmethod
    def parse(cls, raw):
        section = raw.get("presentation", {})
        if not isinstance(section, dict):
            raise ValueError("presentation must be a table")
        target = _text(section, "target")
        if len(target) > 80:
            raise ValueError("presentation.target is too long")
        return cls(_boolean(section, "enabled", False), target,
                   _integer(section, "min_interval_seconds", 90, 30, 900))


def presentation_event(status, now):
    return commentary_observation(status, now)


def narration_text(payload):
    """Fit the host's small notification preview, with actual facts first."""
    facts = payload["facts"]
    def brief(text, size):
        raw = text.encode("utf-8")
        return text if len(raw) <= size else raw[:size - 3].decode("utf-8", errors="ignore") + "…"
    state = "已连接" if payload["connected"] else "已断开：" + str(payload["phase"])
    readings = "；".join(brief(facts.get(key, ""), size) for key, size in (("body", 65), ("queue", 90)))
    return ("请 {LANLAN_NAME} 向 {MASTER_NAME} 说一个游戏新重点，无需查询或派任务。"
            + SPEAK + "新鲜证据仅供理解：" + state + "；" + readings)


class NekoPresentation:
    @timer_interval(id="observe_game_changes", seconds=10,
                    name="观察游戏进展", description="只读当前游戏快照；开启解说后仅将重要变化送给 Neko。")
    async def observe_game_changes(self, **_):
        return await self._present_game()

    @ui.action(label="解说当前游戏状态", tone="default")
    @plugin_entry(id="present_status", name="解说当前游戏状态", kind="service",
                  description="将新鲜游戏观测交给当前或指定 Neko 角色解说；不投递游戏动作。提交不代表已播报。")
    async def present_status(self, **_):
        return await self._present_game(manual=True)

    async def _present_game(self, manual=False):
        raw = await self.config.dump()
        if not manual and HeartbeatSettings.parse(raw).enabled:
            return Ok({"submitted": False, "reason": "decision_heartbeat_owns_narration"})
        config = PresentationSettings.parse(raw)
        settings = Settings.parse(raw)
        if not manual and not config.enabled:
            return Ok({"submitted": False, "reason": "disabled"})
        status = unwrap(await self.status())
        now = time.time()
        event = presentation_event(status, now)
        if event is None:
            return Ok({"submitted": False, "reason": "no_fresh_observation"})
        with self._control_lock:
            path, state, note = commentary_plan(self, settings, config.target, event, now,
                                               config.min_interval_seconds, manual=manual)
            if not note["allowed"]:
                return Ok({"submitted": False, "reason": note["reason"]})
            payload = event["payload"]
            text = narration_text(payload)
            result = self.push_message(visibility=[], ai_behavior="respond",
                parts=[{"type": "text", "text": text}], source="minecraft_autonomous",
                target_lanlan=config.target or None, coalesce_key="minecraft_progress_" + path.stem,
                metadata={"event_signature": event["signature"], "sampled_at": payload["sampled_at"], "expires_in_s": 30})
            accepted = result.get("submitted") is True
            if accepted:
                save_commentary(path, {**state, "submitted_at": now, "retry_after": 0, "playback_verified": False})
            else:
                # Keep the previous baseline: a rejected event is still retryable.
                save_commentary(path, {**load_commentary(path), "retry_after": now + 30})
        return Ok({"submitted": accepted, "reason": result.get("reason"), "playback_verified": False})
