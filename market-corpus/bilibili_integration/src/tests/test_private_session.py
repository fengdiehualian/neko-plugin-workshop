from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from apscheduler.events import EVENT_JOB_ERROR
from bilibili_api import session as upstream
from plugin.plugins.bilibili_integration.bili_client import BiliDMClient


def message(key: int, kind: int = 1, content: dict | str | None = None) -> dict:
    if content is None:
        content = {"content": f"text-{key}"}
    return {
        "receiver_id": 999,
        "receiver_type": 1,
        "sender_uid": 42,
        "talker_id": 42,
        "msg_seqno": key,
        "msg_type": kind,
        "msg_key": key,
        "timestamp": 1,
        "content": json.dumps(content) if not isinstance(content, str) else content,
    }


def picture_content() -> dict:
    return {
        "url": "https://example.invalid/photo.png",
        "width": 1,
        "height": 1,
        "imageType": "png",
        "size": 1,
    }


def make_client() -> BiliDMClient:
    client = BiliDMClient("offline-session", "offline-csrf", enable_comment_notifications=False)
    client._get_user_nickname = AsyncMock(return_value="sender")
    return client


@pytest.mark.parametrize("kind", [2, 6])
@pytest.mark.parametrize("original", ["missing", 0, 1, None])
def test_picture_original_is_optional_and_payload_is_unchanged(kind, original):
    session = make_client()._create_session()
    session.uid = 999
    content = picture_content()
    if original != "missing":
        content["original"] = original
    payload = message(2, kind, content)
    original_json = payload["content"]

    event = session._parse_event(payload)

    assert isinstance(event.content, upstream.Picture)
    assert event.content.url == content["url"]
    assert (event.content.width, event.content.height) == (1, 1)
    assert payload["content"] == original_json
    # The compatibility change is local; other users of bilibili_api are untouched.
    if original == "missing":
        with pytest.raises(KeyError, match="original"):
            upstream.Event(payload, 999)


@pytest.mark.parametrize(
    "kind,content",
    [
        (2, "private-invalid-json"),
        (2, "[]"),
        (2, {**picture_content(), "unexpected": "private-extra-field"}),
        (10, {"text": "private-missing-title"}),
        (999, {"content": "private-unknown-type"}),
    ],
)
def test_malformed_or_unknown_message_is_skipped_without_logging_content(kind, content, caplog):
    session = make_client()._create_session()
    session.uid = 999

    with caplog.at_level(logging.WARNING, logger="Session"):
        event = session._parse_event(message(7, kind, content))

    assert event is None
    assert "msg_key=7" in caplog.text
    assert "private-" not in caplog.text
    assert "offline-session" not in caplog.text


@pytest.mark.asyncio
async def test_real_scheduler_delivers_picture_and_later_messages(monkeypatch):
    monkeypatch.setattr(upstream, "get_self_info", AsyncMock(return_value={"mid": 999}))
    monkeypatch.setattr(upstream, "get_sessions", AsyncMock(return_value={"session_list": []}))
    calls = 0

    async def new_sessions(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"session_list": [{"talker_id": 42, "session_type": 1, "session_ts": 1, "max_seqno": 5}]}
        return {"session_list": []}

    monkeypatch.setattr(upstream, "new_sessions", new_sessions)
    # Newest first, as returned by Bilibili: malformed message 4 must not lose 5.
    monkeypatch.setattr(upstream, "fetch_session_msgs", AsyncMock(return_value={"messages": [
        message(5), message(4, 2, "malformed-json"), message(3),
        message(2, 2, picture_content()), message(1),
    ]}))
    client = make_client()
    session = client._create_session()
    errors = []
    session.sched.add_listener(lambda event: errors.append(event.exception), EVENT_JOB_ERROR)

    try:
        await session.run()
        # Use the real registered job, with an aware clock and a shorter interval.
        session.sched.reschedule_job("query", trigger="interval", seconds=0.01)
        session.sched.modify_job("query", next_run_time=datetime.now(timezone.utc))
        received = []
        for _ in range(4):
            received.append(await asyncio.wait_for(client._message_queue.get(), timeout=2))

        assert [item["msg_key"] for item in received] == ["1", "2", "3", "5"]
        assert received[1]["content_type"] == "image_url"
        assert received[1]["content"] == picture_content()["url"]
        assert session.get_status() == 1
        assert not errors
    finally:
        session.close()
        session.sched.shutdown(wait=False)
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("exclude_self", [True, False])
async def test_registered_query_preserves_self_filter_and_withdrawals(monkeypatch, exclude_self):
    monkeypatch.setattr(upstream, "get_self_info", AsyncMock(return_value={"mid": 999}))
    monkeypatch.setattr(upstream, "get_sessions", AsyncMock(return_value={"session_list": []}))
    monkeypatch.setattr(upstream, "new_sessions", AsyncMock(return_value={"session_list": [
        {"talker_id": 42, "session_type": 1, "session_ts": 1, "max_seqno": 3},
    ]}))
    own_message = {**message(2), "sender_uid": 999, "receiver_id": 42}
    monkeypatch.setattr(upstream, "fetch_session_msgs", AsyncMock(return_value={"messages": [
        message(3, 5, "\"1\""), own_message, message(1),
    ]}))
    client = make_client()
    session = client._create_session()
    try:
        await session.run(exclude_self=exclude_self)
        session.sched.pause()
        await session.sched.get_job("query").func()
        await asyncio.sleep(0)

        assert client._message_queue.get_nowait()["sender_uid"] == "42"
        if exclude_self:
            assert client._message_queue.empty()
        else:
            assert client._message_queue.get_nowait()["sender_uid"] == "999"
        assert set(session.events) == {"1", "2", "3"}
        assert session.events["3"].content == "1"
    finally:
        session.close()
        session.sched.shutdown(wait=False)
        await asyncio.sleep(0)
