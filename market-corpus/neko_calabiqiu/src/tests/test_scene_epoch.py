from __future__ import annotations

from neko_calabiqiu.adapters import scene_client as scene_client_module
from neko_calabiqiu.adapters.scene_client import SceneClient
from neko_calabiqiu.core.contracts import IN_GAME, CbqConfig, SceneState
from neko_calabiqiu.core.runtime import CompanionRuntime


def _events(event_seq: int) -> list[dict[str, object]]:
    return [
        {
            "event_seq": event_seq,
            "type": "target_appeared",
            "ts": float(event_seq),
            "payload": {
                "track_id": "t1",
                "cls": "robot",
                "threat_level": "mid",
                "bearing": "screen_center",
            },
        }
    ]


def _payload(instance_id: str, event_seq: int) -> dict[str, object]:
    return {
        "frame_seq": event_seq,
        "event_seq": event_seq,
        "game": {"running": True},
        "session": {"phase": "likely_gameplay", "active": True},
        "flags": {"game_running": True, "target_in_view": True},
        "events_tail": _events(event_seq),
        "meta": {
            "service_version": "0.1.5",
            "service_instance_id": instance_id,
            "infer_mode": "stub",
        },
    }


def test_scene_client_refetches_events_from_zero_after_service_restart(monkeypatch):
    requested: list[int] = []
    payloads = iter([_payload("old", 50), _payload("new", 1), _payload("new", 1)])

    def _fetch(_base_url: str, _timeout: float, since_event: int):
        requested.append(since_event)
        return next(payloads)

    monkeypatch.setattr(scene_client_module, "fetch_scene", _fetch)
    client = SceneClient("http://127.0.0.1:8212")

    # 首次连接：事件环里的旧事件（seq=50，上一会话残留）不回放，
    # 游标直接对齐到最新 seq，只关注之后的新事件。
    assert client.poll().events_tail == []
    restarted = client.poll()
    assert restarted.data_layer_instance_id == "new"
    assert restarted.events_tail == []
    assert [event["event_seq"] for event in client.poll().events_tail] == [1]
    assert requested == [0, 50, 0]


def test_scene_client_first_connect_discards_ring_history(monkeypatch):
    """回归：刚打开插件时数据层进程可能还活着，事件环里残留上一会话的
    target_lost。首次连接不得回放这些历史事件（否则一打开就爆出
    “敌人丢失视野”），游标应直接推进到环内最新 seq。"""
    payloads = iter(
        [
            _payload("srv", 42),
            {**_payload("srv", 43), "events_tail": _events(43)},
            {**_payload("srv", 44), "events_tail": _events(44)},
        ]
    )

    def _fetch(_base_url: str, _timeout: float, since_event: int):
        return next(payloads)

    monkeypatch.setattr(scene_client_module, "fetch_scene", _fetch)
    client = SceneClient("http://127.0.0.1:8212")

    # 首次连接：seq=42 的历史事件被丢弃，游标对齐到 42
    first = client.poll()
    assert first.events_tail == []
    assert client._since_event == 42

    # 之后只接收 seq > 42 的新事件（43 和 44 各投递一次，不重复）
    second = client.poll()
    assert [event["event_seq"] for event in second.events_tail] == [43]
    third = client.poll()
    assert [event["event_seq"] for event in third.events_tail] == [44]


def test_runtime_accepts_reused_event_sequence_in_new_service_epoch():
    runtime = CompanionRuntime(CbqConfig(dry_run=True))
    old = SceneState(
        connected=True,
        data_layer_instance_id="old",
        game_running=True,
        scenario=IN_GAME,
        flags={"target_in_view": True},
        events_tail=_events(1),
    )
    new = SceneState(
        connected=True,
        data_layer_instance_id="new",
        game_running=True,
        scenario=IN_GAME,
        flags={"target_in_view": True},
        events_tail=_events(1),
    )

    first = runtime.evaluate(SceneState(), old, now=10.0)
    second = runtime.evaluate(old, new, now=20.0)

    assert any(event.event_id == "enemy_spotted" for event in first.candidates)
    assert any(event.event_id == "enemy_spotted" for event in second.candidates)
