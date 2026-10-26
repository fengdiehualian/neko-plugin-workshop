"""Read local turn sinks and logs, or summarize an opt-in HTTP fingerprint trace."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from tomllib import loads


def repeat_count(text):
    if len(text) < 12:
        return 1
    period = (text + text).find(text, 1)
    return len(text) // period if 0 < period < len(text) and len(text) % period == 0 else 1


def repeated_head_chars(text):
    """Find a nonoverlapping head replay, allowing an interposed interjection."""
    text = text[:2048]
    best = 0
    for offset in range(6, len(text) - 5):
        if text[offset:offset + 6] != text[:6]:
            continue
        length = 6
        while length < min(offset, len(text) - offset) and text[length] == text[offset + length]:
            length += 1
        best = max(best, length)
    return best


def log_events(path, patterns):
    found = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                when = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                continue
            for name, pattern in patterns.items():
                if re.search(pattern, line):
                    found.append((when, name, number))
    return found


def profile_report(profile, start, end, role):
    manifest = profile / ".neko-plugin-installations/plugins/sticker_manager/plugin.toml"
    signals = profile / "state/topic_signals.json"
    data = json.loads(signals.read_text(encoding="utf-8"))
    turns = data.get("characters", {}).get(role, [])
    date = datetime.fromtimestamp(start).strftime("%Y%m%d")
    logs = profile / "logs"
    main = log_events(logs / f"N.E.K.O_Main_{date}.log", {
        "input": rf"\[{re.escape(role)}\] voice user_transcript ",
        "complete": "Response complete",
        "turn_end": rf"\[{re.escape(role)}\] turn_end analyze check:",
    })
    plugin = log_events(logs / "plugin" / f"N.E.K.O_Plugin_sticker_manager_{date}.log", {
        "tool_send": "tool call: name=sticker_send ",
        "tool_list": "tool call: name=sticker_list ",
        "sent": "sticker sent:",
    })
    replies = []
    previous_user = None
    for turn in turns:
        timestamp = turn.get("timestamp", 0)
        if turn.get("actor") == "user":
            previous_user = timestamp
            continue
        if turn.get("actor") != "ai" or not start <= timestamp <= end:
            continue
        text = str(turn.get("text", ""))
        normalized = "".join(
            c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P")
        )
        low = previous_user if isinstance(previous_user, (int, float)) else timestamp
        counts = {}
        # Log timestamps have second precision; include the completion's second.
        for when, name, _line in main + plugin:
            if int(low) <= when <= int(timestamp):
                counts[name] = counts.get(name, 0) + 1
        replies.append({
            "completed_at": datetime.fromtimestamp(timestamp).isoformat(timespec="milliseconds"),
            "chars": len(text), "exact_copies": repeat_count(text),
            "normalized_copies": repeat_count(normalized),
            "repeated_head_normalized_chars": repeated_head_chars(normalized),
            "events_in_user_reply_window": counts,
        })
    return {
        "installed_version": loads(manifest.read_text(encoding="utf-8"))["plugin"]["version"],
        "window": [datetime.fromtimestamp(start).isoformat(), datetime.fromtimestamp(end).isoformat()],
        "source": "topic_signals retained turns; not a complete historical ledger",
        "role": role, "replies": replies,
        "conclusion_limit": "No raw provider stream in these logs; pre/post-tool cause remains unconfirmed.",
    }


def trace_report(path):
    responses = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("event") == "response":
                responses.append(row)
    linked = [row for row in responses if row.get("parent") is not None]
    return {
        "responses": len(responses), "linked_followups": len(linked),
        "confirmed_provider_replays": [
            row["request"] for row in linked if row.get("provider_replayed_pretool") is True
        ],
        "complete_followups_without_pretool_replay": [
            row["request"] for row in linked if row.get("provider_replayed_pretool") is False
        ],
        "inconclusive_followups": [
            row["request"] for row in linked if row.get("provider_replayed_pretool") is None
        ],
        "conclusion_limit": (
            "A linked replay confirms repeated provider text. Absence needs the same turn's host/UI "
            "evidence; unmatched, partial, encoded or truncated streams cannot exclude provider replay."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--role", default="YUI")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.trace:
        report = trace_report(args.trace)
    elif args.profile and args.start and args.end:
        report = profile_report(
            args.profile, datetime.fromisoformat(args.start).timestamp(),
            datetime.fromisoformat(args.end).timestamp(), args.role,
        )
    else:
        parser.error("Provide --trace, or --profile with --start and --end.")
    result = json.dumps(report, ensure_ascii=True, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(result + "\n")
    print(result)


if __name__ == "__main__":
    main()
