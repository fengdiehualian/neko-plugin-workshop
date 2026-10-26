"""A local-day view of observed companionship; never invent timestamps for old clicks."""
from datetime import datetime, timedelta


def parsed(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def build_diary(sessions, observations, at=None):
    at = at or datetime.now().astimezone()
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = midnight + timedelta(days=1)
    intervals, entries = [], []
    counts = {"pet": 0, "ask": 0}
    partial_counts = False
    partial_duration = False

    def entry(identifier, stamp, kind, text):
        point = parsed(stamp)
        if point is not None and midnight <= point <= at:
            entries.append({"id": identifier, "at": point.astimezone(at.tzinfo).isoformat(),
                            "kind": kind, "text": text, "_stamp": point.timestamp()})

    for row in sessions:
        start = parsed(row.get("start"))
        end = at if row.get("active") else parsed(row.get("end"))
        overlaps = start is not None and end is not None and start <= at and end >= midnight
        presence = parsed(row.get("presence_start"))
        if presence is not None and end is not None:
            left, right = max(presence, midnight), min(end, at)
            if left < right:
                intervals.append((left.timestamp(), right.timestamp()))
                entry(row["id"] + "-start", row["presence_start"], "companion", "YUI 开始在猫粮挂件里陪你")
                if not row.get("active"):
                    entry(row["id"] + "-end", row.get("end"), "companion", "收起挂件，留下这段陪伴")
        elif overlaps and not row.get("diary_version"):
            partial_duration = True
        clicks = row.get("interaction_events", [])
        for kind in counts:
            known = [item for item in clicks if item.get("kind") == kind]
            today = [item for item in known if (point := parsed(item.get("at"))) is not None and midnight <= point <= at]
            counts[kind] += len(today)
            legacy = max(0, row.get(kind, 0) - len(known))
            if overlaps and legacy:
                if start >= midnight and end < tomorrow:
                    counts[kind] += legacy
                else:
                    partial_counts = True
            if today:
                entry(row["id"] + "-" + kind, today[-1]["at"], kind,
                      "摸了摸 YUI" if kind == "pet" else "查看过猫粮或空闲状态")
        stories = {"start": "小白猫开始追逐大肥鱼", "reverse": "攻防转换，大肥鱼开始反追 YUI",
                   "rescue": "把小白猫抱回家，YUI 恢复精神", "pause": "让追逐歇一会儿"}
        for index, game in enumerate(row.get("game_events", [])):
            if game.get("kind") in stories:
                entry(row["id"] + "-game-" + str(index), game.get("at"), "game", stories[game["kind"]])
    for row in observations:
        if row.get("kind") == "low_food":
            entry(row["id"], row.get("start"), "low_food", row.get("summary", "YUI 留意到猫粮不足"))
        elif row.get("kind") == "task_completed":
            entry(row["id"], row.get("start"), "task_completed", "忙完一件啦喵～")
    seconds, last_end = 0, float("-inf")
    for left, right in sorted(intervals):
        seconds += max(0, right - max(left, last_end))
        last_end = max(last_end, right)
    entries.sort(key=lambda item: item.pop("_stamp"), reverse=True)
    return {"date": midnight.date().isoformat(), "seconds": int(seconds), **counts,
            "partial_counts": partial_counts, "partial_duration": partial_duration, "entries": entries[:50]}
