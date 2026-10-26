"""本地管理员 + 清空聊天 测试。

覆盖：
- delete_post：删除帖子及其回复
- clear_local_chat：清空本机与 YUI 聊天，保留远端消息与帖子
- is_visible：禁言/拉黑节点的内容不可见
- HTTP 层：/api/admin（delete_post/mute/unmute/block/unblock）、/api/clear_chat、/api/settings.admin
"""

import json
import urllib.request

import pytest
from plugin.plugins.neko_community_chat import server as srv


@pytest.fixture(autouse=True)
def _isolated_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    yield
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


@pytest.fixture()
def http_server(tmp_path):
    """起一个真实 HTTP 服务做端点测试。"""
    server = srv.start_server(port=0)
    port = srv.server_port()
    yield port
    server.shutdown()


def _request(port: int, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class TestDeletePost:
    def test_delete_post_with_replies(self):
        srv.append_message({"sender": "社区用户", "text": "第一条动态", "msg_type": "post",
                            "post_id": "post-abc", "origin": "local"})
        srv.append_message({"sender": "远端", "text": "回复一下", "msg_type": "post_reply",
                            "reply_to": "post-abc", "origin": "relay"})
        assert len(srv.read_posts()) == 1
        assert srv.delete_post("post-abc") is True
        remaining = srv.read_all_messages()
        assert all(str(m.get("msg_type") or "") != "post" for m in remaining)
        assert all(str(m.get("reply_to") or "") != "post-abc" for m in remaining)

    def test_delete_missing(self):
        assert srv.delete_post("nope") is False

    def test_http_delete_post(self, http_server):
        port = http_server
        _request(port, "/api/posts", "POST", {"text": "待删动态"})
        posts = _request(port, "/api/feed")[1]["posts"]
        assert len(posts) == 1
        st, j = _request(port, "/api/admin", "POST",
                         {"action": "delete_post", "post_id": posts[0]["post_id"]})
        assert st == 200 and j.get("ok") is True
        assert _request(port, "/api/feed")[1]["posts"] == []
        # 重复删除 → 404
        st2, _ = _request(port, "/api/admin", "POST",
                          {"action": "delete_post", "post_id": posts[0]["post_id"]})
        assert st2 == 404


class TestClearChat:
    def test_clear_keeps_remote_and_posts(self):
        srv.append_message({"sender": "本机", "text": "本地消息", "role": "user", "origin": "local"})
        srv.append_message({"sender": "YUI", "text": "YUI 回复", "origin": "local"})
        srv.append_message({"sender": "远端", "text": "远端消息", "origin": "relay",
                            "peer_id": "remote-1"})
        srv.append_message({"sender": "我", "text": "我的动态", "msg_type": "post",
                            "post_id": "post-1", "origin": "local"})
        removed = srv.clear_local_chat()
        assert removed == 2  # 本机消息 + YUI 回复被清
        msgs = srv.read_all_messages()
        assert [m["text"] for m in msgs] == ["远端消息", "我的动态"]

    def test_http_clear_chat(self, http_server):
        port = http_server
        _request(port, "/api/messages", "POST", {"sender": "我", "text": "hello"})
        _request(port, "/api/messages", "POST", {"sender": "YUI", "text": "hi"})
        _request(port, "/api/relay/inbox", "POST", {})
        st, j = _request(port, "/api/clear_chat", "POST", {})
        assert st == 200 and j.get("removed") == 2


class TestAdminVisibility:
    def test_mute_hides_remote(self):
        srv.save_settings({"muted": ["remote-1"]})
        m = {"sender": "远端", "text": "x", "origin": "relay", "peer_id": "remote-1"}
        assert srv.is_visible(m) is False
        assert srv.is_visible({**m, "peer_id": "remote-2"}) is True

    def test_block_hides_too(self):
        srv.save_settings({"blocked": ["remote-9"]})
        assert srv.is_visible({"peer_id": "remote-9"}) is False

    def test_http_mute_and_feed_filtered(self, http_server):
        port = http_server
        # 远端发帖（模拟已落地的远端帖子）
        srv.append_message({"sender": "远端", "text": "远端动态", "msg_type": "post",
                            "post_id": "rp-1", "origin": "relay", "peer_id": "remote-1"})
        st, j = _request(port, "/api/admin", "POST", {"action": "mute", "peer_id": "remote-1"})
        assert st == 200
        feed = _request(port, "/api/feed")[1]
        assert feed["posts"] == []  # 被禁言节点帖子不可见
        st2, _ = _request(port, "/api/admin", "POST", {"action": "unmute", "peer_id": "remote-1"})
        assert st2 == 200
        assert len(_request(port, "/api/feed")[1]["posts"]) == 1  # 解除后恢复

    def test_http_block_roundtrip(self, http_server):
        port = http_server
        _request(port, "/api/admin", "POST", {"action": "block", "peer_id": "r2"})
        st, j = _request(port, "/api/settings", "GET", {})
        assert st == 200 and j["settings"]["admin"] is True
        assert "r2" in j["settings"]["blocked"]
        _request(port, "/api/admin", "POST", {"action": "unblock", "peer_id": "r2"})
        _, j2 = _request(port, "/api/settings", "GET", {})
        assert "r2" not in j2["settings"]["blocked"]
