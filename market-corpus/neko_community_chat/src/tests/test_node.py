"""neko_community_chat 节点互联层测试（node.py）。

覆盖：
- LAN hello 报文解析
- 身份持久化（peer.json）
- 限速（rate_limited）
- server 新端点：relay/inbox（LAN 互推+鉴权+限速）、posts 社区墙、settings、feed 合并、定向回复

跑法：`uv run --project <N.E.K.O repo> -- python -m pytest plugin/plugins/neko_community_chat/tests/test_node.py -q`
"""

import json
import os
import threading
import urllib.error
import urllib.request

import pytest
from plugin.plugins.neko_community_chat import node as node_mod
from plugin.plugins.neko_community_chat import server as srv


@pytest.fixture(autouse=True)
def _isolated_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    yield
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def _request(url: str, method: str = "GET", body: str | None = None, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = body.encode("utf-8")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class TestNode:
    def test_parse_hello(self):
        h = node_mod._parse_hello("neko-cc-v1|node-x|我的电脑|7870|1700000000")
        assert h == {"peer_id": "node-x", "name": "我的电脑", "port": 7870, "ts": "1700000000"}

    def test_parse_hello_bad(self):
        assert node_mod._parse_hello("garbage") is None
        assert node_mod._parse_hello("neko-cc-v1|a|b|7870|ts|extra") is not None  # 忽略多余段
        assert node_mod._parse_hello("neko-cc-v1|a|b|notnum") is None  # 端口非法 → 拒绝

    def test_identity_persisted(self, tmp_path):
        n = node_mod.Node(str(tmp_path), on_incoming=lambda m: None)
        assert n.peer_id.startswith("node-")
        n2 = node_mod.Node(str(tmp_path), on_incoming=lambda m: None)
        assert n2.peer_id == n.peer_id  # 二次加载保持同一身份

    def test_peers_snapshot(self, tmp_path):
        n = node_mod.Node(str(tmp_path), on_incoming=lambda m: None)
        n._upsert_peer({"peer_id": "node-a", "port": 7870, "name": "电脑A", "ts": "0"}, ip="192.168.1.5")
        n._upsert_peer({"peer_id": "node-b", "port": 7871, "name": "电脑B", "ts": "0"}, ip="192.168.1.6")
        snap = n.peers_snapshot()
        assert len(snap) == 2
        assert {s["peer_id"] for s in snap} == {"node-a", "node-b"}
        a = next(s for s in snap if s["peer_id"] == "node-a")
        assert a["ip"] == "192.168.1.5" and a["port"] == 7870

    def test_rate_limit(self, tmp_path):
        n = node_mod.Node(str(tmp_path), on_incoming=lambda m: None)
        peer = "node-attacker"
        limited = 0
        for _ in range(70):
            if n.rate_limited(peer):
                limited += 1
        assert limited > 0  # 超限被挡

    def test_settings_load_relay(self, tmp_path):
        with open(os.path.join(str(tmp_path), "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"relay_url": "https://r.example.com/"}, f)
        n = node_mod.Node(str(tmp_path), on_incoming=lambda m: None)
        assert n.relay_url == "https://r.example.com"

    def test_default_relay_when_unset(self, tmp_path):
        """无任何中继配置 → 自动使用内置默认中继（所有安装者零配置开箱即用）。"""
        n = node_mod.Node(str(tmp_path), on_incoming=lambda m: None)
        assert n.relay_url == node_mod.DEFAULT_RELAY_URL
        assert n.relay_status()["default_used"] is True

    def test_refresh_keeps_default_relay(self, tmp_path):
        """settings 变更（仅昵称）后空中继仍回退默认。"""
        with open(os.path.join(str(tmp_path), "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "新昵称"}, f)
        n = node_mod.Node(str(tmp_path), on_incoming=lambda m: None)
        n.refresh()
        assert n.relay_url == node_mod.DEFAULT_RELAY_URL
        assert n.relay_status()["default_used"] is True


class TestServerV2:
    def _start(self):
        s = srv.start_server(port=0)
        return s, s.server_port

    def test_relay_inbox_requires_private_ip_and_peer(self, monkeypatch):
        s, port = self._start()
        try:
            # 无 X-Peer-Id → 403
            status, data = _request(
                f"http://127.0.0.1:{port}/api/relay/inbox", method="POST",
                body=json.dumps({"text": "hi", "sender": "x"}),
            )
            assert status == 403
            # 带 X-Peer-Id → 落地到消息流
            status, data = _request(
                f"http://127.0.0.1:{port}/api/relay/inbox", method="POST",
                body=json.dumps({"text": "远端你好", "sender": "x", "peer_name": "远端的电脑"}),
                headers={"X-Peer-Id": "node-remote-abc"},
            )
            assert status == 200 and data["received"] is True
            _, hist = _request(f"http://127.0.0.1:{port}/api/history")
            assert any(m.get("peer_id") == "node-remote-abc" and m["text"] == "远端你好" for m in hist["messages"])
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_posts_wall(self):
        s, port = self._start()
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/posts", method="POST",
                body=json.dumps({"text": "大家好，我是新节点"}),
            )
            assert status == 201 and data["post"]["msg_type"] == "post"
            # feed 合并返回自建墙帖子
            status, feed = _request(f"http://127.0.0.1:{port}/api/feed")
            assert status == 200
            assert any(p.get("msg_type") == "post" for p in feed["posts"])
            # posts 不会进聊天轮询
            status, msgs = _request(f"http://127.0.0.1:{port}/api/messages")
            assert all((m.get("msg_type") or "chat") != "post" for m in msgs["messages"])
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_settings_roundtrip(self):
        s, port = self._start()
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/settings", method="POST",
                body=json.dumps({"name": "新昵称", "relay_url": "https://relay.example.dev"}),
            )
            assert status == 200 and data["settings"]["name"] == "新昵称"
            status, data = _request(f"http://127.0.0.1:{port}/api/settings")
            # 隐私：中继地址明文永不返回；自定义中继时 relay_default=False
            assert "relay_url" not in data["settings"]
            assert data["settings"]["relay_default"] is False
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_post_reply(self):
        """社区动态可回复：post_reply 带 reply_to，feed 聚合到主帖下。"""
        s, port = self._start()
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/posts", method="POST",
                body=json.dumps({"text": "主帖标题"}),
            )
            assert status == 201
            pid = data["post"]["post_id"]
            assert pid and data["post"]["msg_type"] == "post"
            # 回复该帖
            status, data = _request(
                f"http://127.0.0.1:{port}/api/posts", method="POST",
                body=json.dumps({"text": "这是对主帖的回复", "reply_to": pid}),
            )
            assert status == 201
            assert data["post"]["msg_type"] == "post_reply"
            assert data["post"]["reply_to"] == pid
            # feed 聚合：主帖下挂回复
            status, feed = _request(f"http://127.0.0.1:{port}/api/feed")
            assert status == 200
            main = [p for p in feed["posts"] if p.get("post_id") == pid]
            assert main, "post not in feed"
            replies = main[0].get("replies", [])
            assert any(r.get("text") == "这是对主帖的回复" for r in replies), f"reply not aggregated: {replies}"
            # 回复不带 post_id（只有主帖有），不会出现在 feed 顶层
            assert not any(p.get("msg_type") == "post_reply" for p in feed["posts"])
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_targeted_message_skips_inbox(self):
        """面板定向回复远端：带 target_peer 的消息不唤醒本机 YUI（不写 inbox）。"""
        s, port = self._start()
        try:
            # 预先注册一个 peer 表项（模拟 LAN 发现过）——直接注入 node 单例
            n = node_mod.Node(os.path.join(os.environ["LOCALAPPDATA"], "n"), on_incoming=srv.incoming_remote)
            n.peers["node-remote-abc"] = {"ip": "127.0.0.1", "port": port, "name": "远端的电脑", "last_seen": 0}
            srv.set_node(n)
            try:
                status, data = _request(
                    f"http://127.0.0.1:{port}/api/messages", method="POST",
                    body=json.dumps({"text": "收到你的消息了", "sender": "user", "target_peer": "node-remote-abc"}),
                )
                assert status == 201
                # inbox 不应有这条（不唤醒本机 YUI）
                inbox_dir = os.path.join(os.environ["LOCALAPPDATA"], "N.E.K.O", "neko_community_chat", "inbox")
                inbox_files = [f for f in os.listdir(inbox_dir) if f.endswith(".json")] if os.path.isdir(inbox_dir) else []
                assert len(inbox_files) == 0
            finally:
                srv.set_node(None)
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_incoming_remote_post_not_in_inbox(self):
        """远端帖子只进消息流，不唤醒 YUI。"""
        s, port = self._start()
        try:
            status, _ = _request(
                f"http://127.0.0.1:{port}/api/relay/inbox", method="POST",
                body=json.dumps({"text": "墙帖", "msg_type": "post", "peer_name": "n2"}),
                headers={"X-Peer-Id": "node-remote-abc"},
            )
            assert status == 200
            inbox_dir = os.path.join(os.environ["LOCALAPPDATA"], "N.E.K.O", "neko_community_chat", "inbox")
            inbox_files = [f for f in os.listdir(inbox_dir) if f.endswith(".json")] if os.path.isdir(inbox_dir) else []
            assert len(inbox_files) == 0
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()
