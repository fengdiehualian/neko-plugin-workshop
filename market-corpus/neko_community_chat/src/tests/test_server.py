"""neko_community_chat 测试。

覆盖：
- server：消息 API（发/轮询/历史）、安全边界（空消息/超长/坏 JSON）、inbox 写入、聊天页
- 插件桥：append_message 单调 id、增量轮询

跑法：`uv run --project <N.E.K.O repo> -- python -m pytest plugin/plugins/neko_community_chat/tests/ -q`
"""

import json
import os
import threading
import urllib.request

import pytest
from plugin.plugins.neko_community_chat import server as srv


@pytest.fixture(autouse=True)
def _isolated_data(monkeypatch, tmp_path):
    """每个测试用独立数据目录（LOCALAPPDATA 指向临时目录），互不污染。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    yield
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def _request(url: str, method: str = "GET", body: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = body.encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class TestServer:
    def test_health(self):
        s = srv.start_server(port=0)  # 随机端口
        port = s.server_port
        try:
            status, data = _request(f"http://127.0.0.1:{port}/api/health")
            assert status == 200 and data["ok"] is True
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_send_and_poll(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/messages", method="POST",
                body=json.dumps({"text": "你好 YUI", "sender": "user"}),
            )
            assert status == 201 and data["ok"] is True
            mid = int(data["message"]["id"])
            # 回读轮询
            status, poll = _request(f"http://127.0.0.1:{port}/api/messages?after_id={mid - 1}")
            assert status == 200 and len(poll["messages"]) == 1
            assert poll["messages"][0]["text"] == "你好 YUI"
            assert poll["messages"][0]["sender"] == "user"
            # 增量：after=mid 无新消息
            status, poll2 = _request(f"http://127.0.0.1:{port}/api/messages?after_id={mid}")
            assert poll2["messages"] == []
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_send_empty_rejected(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/messages", method="POST",
                body=json.dumps({"text": "   "}),
            )
            assert status == 400 and data["ok"] is False
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_send_too_long_rejected(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/messages", method="POST",
                body=json.dumps({"text": "长" * 2001}),
            )
            assert status == 400 and data["ok"] is False
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_bad_json_rejected(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/messages", method="POST", body="{not json",
            )
            assert status == 400 and data["ok"] is False
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_inbox_written(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            _request(
                f"http://127.0.0.1:{port}/api/messages", method="POST",
                body=json.dumps({"text": "唤醒我", "sender": "user"}),
            )
            inbox_dir = os.path.join(srv.data_dir(), "inbox")
            names = [n for n in os.listdir(inbox_dir) if n.endswith(".json")]
            assert len(names) == 1
            with open(os.path.join(inbox_dir, names[0]), "r", encoding="utf-8") as f:
                assert json.load(f)["text"] == "唤醒我"
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_chat_page_served(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/chat")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
                assert "喵宇宙社区聊天" in resp.read().decode("utf-8")
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()


class TestPluginBridge:
    def test_append_message_monotonic_id(self, _isolated_data):
        m1 = srv.append_message({"sender": "user", "text": "a", "role": "user"})
        m2 = srv.append_message({"sender": "YUI", "text": "b", "role": "yui"})
        assert int(m2["id"]) == int(m1["id"]) + 1
        assert m2["sender"] == "YUI"
        assert srv.read_all_messages()[1]["text"] == "b"

    def test_read_messages_after(self, _isolated_data):
        m1 = srv.append_message({"sender": "user", "text": "x", "role": "user"})
        m2 = srv.append_message({"sender": "YUI", "text": "y", "role": "yui"})
        after = int(m1["id"])
        got = srv.read_messages(after)
        assert len(got) == 1 and got[0]["id"] == m2["id"]
