import pytest
from kards_companion.runtime import timeline as timeline_module
from kards_companion.runtime.timeline import RuntimeTimeline, TimelineEntry


def test_entries_are_bounded_and_drop_counter_tracks_evictions() -> None:
    timeline = RuntimeTimeline(max_entries=3)
    for index in range(5):
        timeline.record("stage", "outcome", ts=float(index), seq=index,
                        reason=f"r{index}", detail={"n": index})

    snapshot = timeline.as_dict()
    assert snapshot["dropped"] == 2
    assert [entry["seq"] for entry in snapshot["recent"]] == [2, 3, 4]
    assert len(timeline.recent(limit=100)) == 3


def test_entry_contract_and_limit_cap() -> None:
    timeline = RuntimeTimeline(max_entries=150)
    for index in range(150):
        timeline.record("stage", "ok", ts=float(index), seq=index)

    assert len(timeline.recent(limit=1000)) == 100
    latest = timeline.recent(limit=1)[0]
    assert set(latest) == {"ts", "stage", "outcome", "seq", "reason", "detail"}
    assert latest["seq"] == 149

    entry = TimelineEntry(ts=1.0, stage="s", outcome="o", seq=1,
                          reason="reason", detail={"a": 1})
    assert entry.to_dict()["detail"] == {"a": 1}


def test_image_and_raw_ocr_keys_are_redacted() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    timeline.record(
        "capture", "rejected", ts=1.0, seq=1,
        detail={
            "image": b"\x89PNG",
            "pixels": b"\x00\x01",
            "raw_ocr": "SECRET OCR TEXT",
            "screenshot": object(),
            "api_key": "secret",
            "reason_code": "stale_frame",
        },
    )
    detail = timeline.recent(limit=1)[0]["detail"]
    assert detail == {"reason_code": "stale_frame"}
    assert "SECRET OCR TEXT" not in str(timeline.as_dict())


def test_nested_sensitive_keys_are_redacted() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    timeline.record(
        "capture", "rejected", ts=1.0, seq=1,
        detail={"frame": {"raw_ocr": "SECRET OCR TEXT", "image": b"\x89PNG"},
                "reason_code": "stale_frame"},
    )
    detail = timeline.recent(limit=1)[0]["detail"]
    assert detail == {"frame": {}, "reason_code": "stale_frame"}


def test_sensitive_keys_are_redacted_at_every_depth_and_case_insensitively() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    timeline.record(
        "capture", "rejected", ts=1.0, seq=1,
        detail={
            "level1": {
                "level2": {
                    "level3": {
                        "API_KEY": "secret",
                        "Pixels": [1, 2, 3],
                        "Screenshot": b"png",
                        "Raw_OCR": "SECRET OCR TEXT",
                        "kept": "ok",
                    }
                }
            }
        },
    )
    detail = timeline.recent(limit=1)[0]["detail"]
    assert detail == {"level1": {"level2": {"level3": {"kept": "ok"}}}}
    assert "SECRET OCR TEXT" not in str(timeline.as_dict())


def test_detail_depth_is_bounded() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    nested: dict = {"leaf": "value"}
    for _ in range(timeline_module.MAX_DETAIL_DEPTH + 10):
        nested = {"child": nested}
    timeline.record("stage", "ok", ts=1.0, seq=1, detail={"root": nested})

    detail = timeline.recent(limit=1)[0]["detail"]
    depth = 0
    node: object = detail["root"]
    while isinstance(node, dict):
        depth += 1
        node = next(iter(node.values()), None)
    assert depth <= timeline_module.MAX_DETAIL_DEPTH + 1
    assert node == timeline_module.TRUNCATED


def test_detail_strings_and_collections_are_bounded() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    timeline.record(
        "stage", "ok", ts=1.0, seq=1,
        detail={
            "text": "x" * (timeline_module.MAX_DETAIL_STRING * 4),
            "items": list(range(timeline_module.MAX_DETAIL_ITEMS * 4)),
            "mapping": {str(i): i for i in range(timeline_module.MAX_DETAIL_ITEMS * 4)},
            "key" * timeline_module.MAX_DETAIL_KEY: "long-key",
            "blob": b"\x89PNG\r\n",
        },
    )
    detail = timeline.recent(limit=1)[0]["detail"]
    assert len(detail["text"]) == timeline_module.MAX_DETAIL_STRING
    assert len(detail["items"]) == timeline_module.MAX_DETAIL_ITEMS
    assert len(detail["mapping"]) == timeline_module.MAX_DETAIL_ITEMS
    assert detail["blob"] == "<bytes>"
    assert all(len(key) <= timeline_module.MAX_DETAIL_KEY for key in detail)


def test_resize_keeps_most_recent_entries() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    for index in range(6):
        timeline.record("stage", "outcome", ts=float(index), seq=index)

    timeline.resize(2)
    assert [entry["seq"] for entry in timeline.recent(limit=10)] == [4, 5]

    timeline.resize(10)
    assert [entry["seq"] for entry in timeline.recent(limit=10)] == [4, 5]


def test_resize_counts_entries_dropped_by_shrinking() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    for index in range(6):
        timeline.record("stage", "outcome", ts=float(index), seq=index)

    timeline.resize(2)
    assert timeline.as_dict()["dropped"] == 4

    timeline.resize(10)  # growing never drops
    assert timeline.as_dict()["dropped"] == 4

    timeline.resize(1)  # [4, 5] -> [5]
    assert timeline.as_dict()["dropped"] == 5
    assert [entry["seq"] for entry in timeline.recent(limit=10)] == [5]


@pytest.mark.parametrize("limit", [0, -1, -100])
def test_non_positive_limit_returns_no_entries(limit: int) -> None:
    timeline = RuntimeTimeline(max_entries=10)
    timeline.record("stage", "outcome", ts=1.0, seq=1)

    assert timeline.recent(limit=limit) == []
    assert timeline.as_dict(limit=limit)["recent"] == []
    assert timeline.as_dict(limit=limit)["dropped"] == 0


def test_default_limit_still_returns_recent_entries() -> None:
    timeline = RuntimeTimeline(max_entries=10)
    timeline.record("stage", "outcome", ts=1.0, seq=1)

    assert [entry["seq"] for entry in timeline.recent()] == [1]
    assert [entry["seq"] for entry in timeline.recent(limit=1)] == [1]
