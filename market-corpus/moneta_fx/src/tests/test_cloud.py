# isort: skip_file
"""云端后端客户端测试：URL 拼装、cookie 管理、错误透传。

不发起真实网络请求——用假的 urlopen 替身断言请求形状与错误处理。
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from cloud import CloudClient, RemoteError


class _FakeResponse:
    def __init__(self, payload, set_cookie=None):
        self._payload = payload
        self.headers = {"Set-Cookie": set_cookie} if set_cookie else {}

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture()
def calls(monkeypatch):
    """记录 (method, url, body) 并允许自定义响应或异常。"""

    recorded = []
    state = {"response": {}, "cookie": None, "raise": None}

    def fake_urlopen(request, timeout=None):
        recorded.append((request.method, request.full_url, request.data))
        if state["raise"] is not None:
            raise state["raise"]
        return _FakeResponse(state["response"], state["cookie"])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return recorded, state


def test_base_url_trailing_slash_is_stripped():
    assert CloudClient("http://host:4173/").base_url == "http://host:4173"


def test_get_config(calls):
    recorded, state = calls
    state["response"] = {"configured": True}
    client = CloudClient("http://host:4173")
    assert client.config() == {"configured": True}
    method, url, body = recorded[0]
    assert method == "GET"
    assert url == "http://host:4173/api/config"
    assert body is None


def test_post_sends_json_body(calls):
    recorded, state = calls
    state["response"] = {"user": {"id": "u1"}}
    client = CloudClient("http://host:4173")
    result = client.login("alice", "secret")
    assert result["user"]["id"] == "u1"
    method, url, body = recorded[0]
    assert method == "POST"
    assert url == "http://host:4173/api/auth/login"
    assert json.loads(body) == {"username": "alice", "password": "secret"}


def test_set_cookie_is_captured_and_replayed(calls):
    recorded, state = calls
    state["response"] = {"user": {"id": "u1"}}
    state["cookie"] = "moneta_session=abc123; Path=/; HttpOnly"
    client = CloudClient("http://host:4173")
    client.login("alice", "secret")
    # 只保留 name=value
    assert client.cookie == "moneta_session=abc123"

    state["response"] = {"book": {}}
    client.world_state("EUR/USD")
    # 第二个请求带上了 cookie
    assert len(recorded) == 2


def test_quotes_joins_symbols(calls):
    recorded, state = calls
    state["response"] = {"quotes": {}}
    client = CloudClient("http://host:4173")
    client.quotes(["EUR/USD", "GBP/USD"])
    _, url, _ = recorded[0]
    assert url == "http://host:4173/api/quotes?symbols=EUR/USD,GBP/USD"


def test_trade_wraps_action_and_payload(calls):
    recorded, state = calls
    state["response"] = {"book": {"account": {}}}
    client = CloudClient("http://host:4173")
    client.trade("order", {"order": {"symbol": "EUR/USD"}, "requestId": "r1"})
    _, url, body = recorded[0]
    assert url == "http://host:4173/api/world/trade"
    payload = json.loads(body)
    assert payload["action"] == "order"
    assert payload["requestId"] == "r1"


def test_http_error_message_is_translated(calls):
    _, state = calls
    state["raise"] = urllib.error.HTTPError(
        "http://host/api", 403, "Forbidden", None, None
    )
    client = CloudClient("http://host:4173")
    with pytest.raises(RemoteError):
        client.config()


def test_ping_returns_false_when_unreachable(calls):
    _, state = calls
    state["raise"] = urllib.error.URLError("connection refused")
    client = CloudClient("http://host:4173")
    assert client.ping() is False


def test_ping_returns_true_when_ok(calls):
    _, state = calls
    state["response"] = {"configured": True}
    client = CloudClient("http://host:4173")
    assert client.ping() is True
