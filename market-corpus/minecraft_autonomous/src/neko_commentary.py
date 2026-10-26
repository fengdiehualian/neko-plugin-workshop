"""Host speaking opportunities, separate from strategic acknowledgement and play."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from agent_mc_autonomy.commentary import commentary_changes
from agent_mc_autonomy.review_evidence import preview

SPEAK = ("你正亲自在玩Minecraft，用自己的角色性格用中文边玩边聊1—2句。"
         "分享自然反应、好奇、心情或小打算；平静时也能轻松闲聊，不作汇报。"
         "不要念背包清单、坐标、工具名、受理回执或‘先读取现场’等过程；不用先复述状态。"
         "行动未完成就只说打算，不说已完成。用户直接提问仍正常回答。")
QUIET = ("这是策略交流的延续，可自然分享当前打算或感受；具体依据留在工具判断里。"
         "若同一判断已通过spoken_text排入语音，接话时推进话题，不复述那句打算。")


@lru_cache(maxsize=1)
def speaking_skill():
    return Path(__file__).with_name("skills").joinpath("gameplay-speaking.md").read_text(encoding="utf-8")


def commentary_delivery(note, *, include_evidence=True):
    """Dynamic presentation only; the skill lives in the registered tool description."""
    delivery = {"allowed": note["allowed"],
                "instruction": "沿用工具内边玩边聊技能，可分享一个感受或打算。" if note["allowed"] else QUIET,
                "skill_source": "registered_tool_description"}
    if not include_evidence or not note["allowed"]:
        return delivery
    evidence = note["evidence"]
    feedback = evidence.get("body_feedback") or {}
    # Select one actual conversational anchor, not another strategy report.
    # Detailed validation and originals remain in inspect/detail queries.
    episode = evidence.get("death_episode")
    outcome = next((row for row in reversed(feedback.get("native_outcomes", [])) if not row.get("observation_only")), {})
    body = next(iter(reversed(feedback.get("body_events", []))), {})
    if episode:
        selected = {"death_episode": {key: episode[key] for key in ("id", "source_event") if key in episode}}
    elif evidence.get("progress"):
        selected = {"progress": evidence["progress"], "task_outcome": evidence.get("task_outcome")}
    elif outcome:
        selected = {"native_outcome": {key: outcome[key] for key in ("phase", "receipt", "original_event", "goal_verified") if key in outcome}}
    elif body:
        selected = {"body_event": {key: body[key] for key in ("type", "phase", "text", "original_event") if key in body}}
    else:
        selected = {}  # Ordinary curiosity needs no invented event or filler.
    if selected:
        delivery["本轮重点证据"] = preview(selected, 600)
    return delivery


def speech_contract(receipt):
    """Interpret explicit output receipts without implying audible playback."""
    status = (receipt or {}).get("status", "absent")
    prefix = "控制指令不口播。先提交最终判断；spoken_text可选。"
    if status in {"enqueued", "accepted"}:
        guidance = "已排入语音，无需另写口播，不复述那一句；受理不等于已播放。"
    elif status in {"unknown", "reserved_unknown"}:
        guidance = "语音投递未知，不重投或复述那一句，也不声称已播放；可推进话题。"
    elif status == "repeat_suppressed":
        guidance = "这句与近期已受理或投递未知的自动口播完全相同，本次未排入语音；不重投、不复述、不念抑制标记，无需另起决策；行动照常。"
    else:
        guidance = "未排入则用正常最终回复自然说一句；busy/rejected/disabled或缺省不表示已播放。"
    return {"status": status, "playback_verified": False, "instruction": prefix + guidance + "不以静默或No commentary替代实质判断。"}


def commentary_plan(plugin, settings, target, event, now, interval, *, review_id="", manual=False):
    identity = hashlib.sha256((settings.identity + target).encode()).hexdigest()[:24]
    path = plugin.data_path("presentation", identity + ".json")
    previous = load_commentary(path)
    episode = event["payload"].get("death_episode")
    review_key = hashlib.sha256(json.dumps([event["signature"], episode.get("id") if episode else None]).encode()).hexdigest()
    if review_id and previous.get("review_id") == review_id and previous.get("review_key") == review_key:
        return path, previous, previous["commentary"]
    changed = commentary_changes(event, previous.get("baseline"))
    new_death = bool(episode and episode.get("id") != previous.get("death_episode_id"))
    if new_death:
        changed = {"topics": ["死亡后的感想与复盘"], "urgent": True}
    opportunity = event["payload"].get("opportunity", {})
    if opportunity.get("kind") == "idle_presence":
        changed = {"topics": ["idle_presence"], "urgent": False}
    ready = now - previous.get("submitted_at", 0) >= (30 if manual else interval)
    retrying = now < previous.get("retry_after", 0)
    allowed = not retrying and (manual or bool(changed["topics"])) and (ready or changed["urgent"] or opportunity.get("kind") == "idle_presence")
    note = {"opportunity_id": opportunity.get("id", review_id), "allowed": allowed, "topics": changed["topics"], "urgent": changed["urgent"],
            "evidence": {"progress": event["payload"].get("progress", ""),
                         "task_outcome": event["payload"].get("task_outcome"),
                         "body_feedback": event["payload"].get("body_feedback", {}),
                         **({"death_episode": episode} if new_death else {})},
            "reason": "highlight" if allowed else ("unchanged" if not manual and not changed["topics"]
                                                     else "rate_limited"),
            "instruction": SPEAK if allowed else QUIET, "playback_verified": False}
    state = {**previous, "signature": event["signature"],
             "baseline": event["baseline"] if allowed else previous.get("baseline"),
             "review_id": review_id, "review_key": review_key, "commentary": note,
             "death_episode_id": episode["id"] if new_death and allowed else previous.get("death_episode_id")}
    return path, state, note


def load_commentary(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_commentary(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
