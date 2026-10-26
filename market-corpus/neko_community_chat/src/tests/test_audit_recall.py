"""审核 + msg_uid + 撤回 测试。

覆盖：
- audit_text：正常放行 / 违禁词拦截 / 刷屏拦截
- HTTP 层：发消息 / 发帖命中审核 → 400
- append_message 自动分配 msg_uid
- 撤回：本机标记、远端 recall 标记、HTTP 端点、重复撤回拒绝
"""

import json
import threading
import urllib.request

import pytest
from plugin.plugins.neko_community_chat import server as srv


@pytest.fixture(autouse=True)
def _isolated_data(monkeypatch, tmp_path):
    """每个测试用独立数据目录，互不污染。"""
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


class TestAudit:
    def test_normal_pass(self):
        assert srv.audit_text("今天天气不错") == (True, "")

    def test_forbidden_word_blocked(self):
        ok, reason = srv.audit_text("这里有刷单广告")
        assert ok is False and "刷单" in reason

    def test_spam_repeat_blocked(self):
        ok, _ = srv.audit_text("啊" * 30)
        assert ok is False

    def test_http_message_blocked(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/messages", method="POST",
                body=json.dumps({"text": "加微信 刷单 返利"}),
            )
            assert status == 400 and data["ok"] is False
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()

    def test_http_post_blocked(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/posts", method="POST",
                body=json.dumps({"text": "办假证 联系我"}),
            )
            assert status == 400 and data["ok"] is False
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()


class TestMsgUid:
    def test_msg_uid_assigned(self, _isolated_data):
        m = srv.append_message({"sender": "user", "text": "hi", "role": "user"})
        assert m.get("msg_uid") and len(m["msg_uid"]) == 12


class TestRecall:
    def test_recall_own_message(self, _isolated_data):
        m = srv.append_message({"sender": "user", "text": "要撤回", "role": "user", "origin": "local"})
        assert srv.mark_recalled(m["msg_uid"]) is True
        assert srv.read_all_messages()[0].get("recalled") is True

    def test_recall_unknown(self, _isolated_data):
        assert srv.mark_recalled("nope") is False

    def test_incoming_recall_marks(self, _isolated_data):
        m = srv.append_message(
            {"sender": "远端", "text": "hello", "role": "user", "origin": "relay", "peer_id": "remote-node"}
        )
        srv.incoming_remote({"msg_type": "recall", "msg_uid": m["msg_uid"], "origin": "relay"})
        assert srv.read_all_messages()[0].get("recalled") is True

    def test_recall_before_message_then_apply(self, _isolated_data):
        """recall 先于消息到达（竞态）：缓存待处理，消息落地后自动补标记。"""
        assert srv.mark_recalled("pending-uid-1") is False  # 消息未到 → 缓存
        srv.append_message({"sender": "远端", "text": "迟到消息", "role": "user",
                            "origin": "relay", "msg_uid": "pending-uid-1"})
        assert srv.read_all_messages()[-1].get("recalled") is True  # append 时补标记
        assert srv._pending_recalls == set()

    def test_http_recall_endpoint(self):
        s = srv.start_server(port=0)
        port = s.server_port
        try:
            status, data = _request(
                f"http://127.0.0.1:{port}/api/messages", method="POST",
                body=json.dumps({"text": "撤回我", "sender": "user"}),
            )
            assert status == 201
            uid = data["message"]["msg_uid"]
            status, r = _request(
                f"http://127.0.0.1:{port}/api/recall", method="POST",
                body=json.dumps({"msg_uid": uid}),
            )
            assert status == 200 and r["ok"] is True
            # 已撤回的消息再撤回 → 400
            status, r2 = _request(
                f"http://127.0.0.1:{port}/api/recall", method="POST",
                body=json.dumps({"msg_uid": uid}),
            )
            assert status == 400
        finally:
            threading.Thread(target=s.shutdown, daemon=True).start()
