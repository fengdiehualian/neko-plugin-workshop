"""``qq_official_bind`` 的平台契约回归测试。

本模块此前**零覆盖**，代价是一次真机扫码：平台回了 ``status=2`` + ``bot_appid`` + 密文，
而 :func:`poll_bind_result` 只认 ``appid`` 键，把一次**成功**的绑定判成"凭据不完整"直接
丢弃（现象是连接页一直"连接中"，凭据永远落不了盘）。第一条用例就是钉死这个字段名。

平台回包样本取自 2026-09-12 真机实测（密钥已隐去）。
"""
from __future__ import annotations

import base64
from asyncio import run as asyncio_run
from types import SimpleNamespace

import httpx
import pytest
from plugin.plugins.qq_auto_reply import qq_official_bind as bind

# ── 夹具 ────────────────────────────────────────────────────

def _session() -> bind.BindSession:
    return bind.BindSession(task_id="task-1", bind_key=bind.generate_bind_key(), qrcode="")


def _stub_poll(monkeypatch, payload: dict) -> None:
    """把轮询替换成固定回包 —— 逐字模拟平台的 ``{retcode, msg, data}`` 外壳。"""
    async def fake_post(url, body, *, timeout_ms=bind.API_TIMEOUT_MS):
        return {"retcode": 0, "msg": "success", "data": payload}

    monkeypatch.setattr(bind, "_post_json", fake_post)


def _encrypt(secret: str, bind_key: str) -> str:
    """按平台的载荷格式加密：base64(12 字节 nonce + 密文 + 16 字节 GCM tag)。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = b"\x00" * 12
    blob = AESGCM(base64.b64decode(bind_key)).encrypt(nonce, secret.encode("utf-8"), None)
    return base64.b64encode(nonce + blob).decode("ascii")


# ── 回归：字段名必须是 bot_appid ────────────────────────────

async def test_completed_binding_reads_bot_appid(monkeypatch):
    """平台实际回的是 ``bot_appid`` —— 认它，且绝不能判成 failed。

    这条就是真机抓到的 bug：代码只读 ``appid``，于是 status=2 被当成"凭据不完整"，
    一次成功的绑定连同凭据一起被丢掉。
    """
    session = _session()
    _stub_poll(monkeypatch, {
        "status": 2,
        "bot_appid": "1903565393",
        "bot_encrypt_secret": _encrypt("s3cr3t-from-platform", session.bind_key),
        "user_openid": "0" * 32,
    })

    result = await bind.poll_bind_result(session)

    assert result["status"] == "completed", result
    assert result["raw_status"] == 2
    assert result["appid"] == "1903565393"
    assert result["secret"] == "s3cr3t-from-platform"


async def test_completed_binding_still_accepts_bare_appid(monkeypatch):
    """``appid`` 兜底保留 —— 「创建新的机器人」那条路径未实测，不能赌字段名相同。"""
    session = _session()
    _stub_poll(monkeypatch, {
        "status": 2,
        "appid": "2000000001",
        "bot_encrypt_secret": _encrypt("another-secret", session.bind_key),
    })

    result = await bind.poll_bind_result(session)

    assert result["status"] == "completed", result
    assert result["appid"] == "2000000001"


async def test_completed_without_credentials_is_still_failed(monkeypatch):
    """凭据真缺时仍要报 failed —— 修字段名不等于把这道校验拆掉。"""
    _stub_poll(monkeypatch, {"status": 2, "user_openid": "0" * 32})

    result = await bind.poll_bind_result(_session())

    assert result["status"] == "failed"
    assert "不完整" in result["error"]


# ── 状态码映射 ──────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (0, "none"),
    (1, "pending"),
    (3, "expired"),
    (99, "none"),      # 未知码不得当成成功
])
async def test_status_code_mapping(monkeypatch, raw, expected):
    _stub_poll(monkeypatch, {"status": raw})
    assert (await bind.poll_bind_result(_session()))["status"] == expected


async def test_pending_does_not_leak_credentials(monkeypatch):
    """未完成时不得把 appid/secret 塞进结果 —— 调用方按 status 分支，多给字段会诱人误用。"""
    _stub_poll(monkeypatch, {"status": 1, "bot_appid": "1903565393",
                             "bot_encrypt_secret": "x" * 80})

    result = await bind.poll_bind_result(_session())

    assert result["status"] == "pending"
    assert "appid" not in result and "secret" not in result


# ── 解密（此前亦无覆盖）─────────────────────────────────────

def test_decrypt_round_trip():
    """加密→解密的载荷格式自洽（nonce 前 12、tag 后 16）。"""
    key = bind.generate_bind_key()
    assert bind.decrypt_bot_secret(_encrypt("hello-平台", key), key) == "hello-平台"


def test_decrypt_rejects_tampered_ciphertext():
    """密文被改动必须抛 ValueError —— 拿不到可用密钥就该停，别拿空 secret 去连网关。"""
    key = bind.generate_bind_key()
    raw = bytearray(base64.b64decode(_encrypt("secret", key)))
    raw[20] ^= 0xFF

    with pytest.raises(ValueError):
        bind.decrypt_bot_secret(base64.b64encode(bytes(raw)).decode("ascii"), key)


def test_decrypt_rejects_wrong_key():
    key = bind.generate_bind_key()
    other = bind.generate_bind_key()

    with pytest.raises(ValueError):
        bind.decrypt_bot_secret(_encrypt("secret", key), other)


@pytest.mark.parametrize("blob", ["", "!!!not-base64!!!", "c2hvcnQ="])
def test_decrypt_rejects_malformed_payload(blob):
    with pytest.raises(ValueError):
        bind.decrypt_bot_secret(blob, bind.generate_bind_key())


# ── 二维码渲染 ──────────────────────────────────────────────

def test_render_qr_png_writes_a_real_png(tmp_path):
    """qrcode / pillow 都是硬依赖（requirements.txt），所以这里硬断言，不留静默 skip。"""
    dest = tmp_path / "static" / "cache" / "bind_qrcode.png"

    assert bind.render_qr_png("https://q.qq.com/qqbot/openclaw/connect.html?task_id=t", dest) is True
    assert dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_qr_png_returns_false_instead_of_raising(tmp_path):
    """渲染失败返回 False 由调用方降级 —— 不能把绑定流程整个炸掉。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")

    assert bind.render_qr_png("data", blocker / "sub" / "qr.png") is False


# ── 机器人账本（"防重复建"的闸门读的就是它）─────────────────

def test_remember_bot_dedupes_and_marks_active():
    settings: dict = {}
    first = bind.remember_bot(settings, appid="1001", secret="s1")
    created_at = first["created_at"]

    bind.remember_bot(settings, appid="1001", secret="s2")

    assert len(bind.list_bots(settings)) == 1
    assert bind.find_bot(settings, "1001")["secret"] == "s2"
    # created_at 标的是"何时建的"，重复记账不该刷新它
    assert bind.find_bot(settings, "1001")["created_at"] == created_at
    assert settings[bind.ACTIVE_KEY] == "1001"


def test_pick_reusable_prefers_active_then_latest():
    settings: dict = {}
    bind.remember_bot(settings, appid="1001", secret="s1")
    bind.remember_bot(settings, appid="1002", secret="s2")
    # 显式钉死创建时间：两条在同一瞬间记账，靠 time.time() 分不出先后。
    settings["qq_open_bots"][0]["created_at"] = 2000.0     # 1001 = 最近创建
    settings["qq_open_bots"][1]["created_at"] = 1000.0

    assert settings[bind.ACTIVE_KEY] == "1002"                   # 最后一次记账即当前选中
    assert bind.pick_reusable_bot(settings)["appid"] == "1002"    # 选中的优先于时间

    settings[bind.ACTIVE_KEY] = "missing"
    assert bind.pick_reusable_bot(settings)["appid"] == "1001"   # 选中的没了 → 退到最近创建


def test_pick_reusable_is_none_on_empty_ledger():
    """账本为空 ⇒ 闸门放行，允许走一次扫码。这正是当前实盘状态。

    注意：实盘里 ``qq_open_app_id`` 有值但账本为空，所以闸门**拦不住** —— 别把
    "已有机器人"当成账本有记录。
    """
    assert bind.pick_reusable_bot({}) is None
    assert bind.pick_reusable_bot({"qq_open_app_id": "1903565393"}) is None


def test_list_bots_tolerates_garbage():
    """账本是用户配置，写坏时返回空表而不是抛 —— 否则界面整页打不开。"""
    assert bind.list_bots({"qq_open_bots": "not-a-list"}) == []
    assert bind.list_bots({"qq_open_bots": [{"no_appid": 1}, "junk", None]}) == []


# ── 凭据校验（绑定回执说 completed ≠ 这对凭据现在能用）──────────

class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    """极简 httpx.AsyncClient 替身：按序吐出预设响应/异常。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def _stub_http(monkeypatch, script):
    client = _Client(script)
    # 只替换本模块引用的那个名字，不动全局 httpx
    monkeypatch.setattr(bind, "httpx", SimpleNamespace(AsyncClient=lambda *a, **k: client))
    return client


async def test_verify_credentials_ok(monkeypatch):
    client = _stub_http(monkeypatch, [_Resp({"access_token": "tok", "expires_in": 7200})])

    r = await bind.verify_credentials("1001", "s", attempts=3, delay=0)

    assert r == {"ok": True, "attempts": 1, "expires_in": 7200, "error": ""}
    assert client.calls == 1


async def test_verify_credentials_reports_platform_rejection(monkeypatch):
    """平台明确拒绝（本例即真机抓到的 100016）—— 重试满次数后如实报出来。"""
    _stub_http(monkeypatch, [_Resp({"code": 100016, "message": "invalid appid or secret"})])

    r = await bind.verify_credentials("1001", "bad", attempts=3, delay=0)

    assert r["ok"] is False
    assert r["attempts"] == 3
    assert "100016" in r["error"]


async def test_verify_credentials_retries_then_succeeds(monkeypatch):
    """首次失败、再试通过 —— 别把一次偶发失败直接报成"凭据无效"。"""
    client = _stub_http(monkeypatch, [
        _Resp({"code": 100016, "message": "nope"}),
        _Resp({"access_token": "tok", "expires_in": 3600}),
    ])

    r = await bind.verify_credentials("1001", "s", attempts=3, delay=0)

    assert r["ok"] is True
    assert r["attempts"] == 2
    assert client.calls == 2


async def test_verify_credentials_tolerates_network_error(monkeypatch):
    """网络异常不该把整个校验炸掉，也不该被误当成"凭据无效"的证据类型。"""
    _stub_http(monkeypatch, [httpx.ConnectError("boom")])

    r = await bind.verify_credentials("1001", "s", attempts=2, delay=0)

    assert r["ok"] is False
    assert "ConnectError" in r["error"]


async def test_verify_credentials_rejects_empty_inputs(monkeypatch):
    """缺 appid/secret 时不必发请求 —— 直接判否，且 attempts 为 0。"""
    client = _stub_http(monkeypatch, [_Resp({"access_token": "tok"})])

    assert (await bind.verify_credentials("", "s"))["ok"] is False
    assert (await bind.verify_credentials("1001", "  "))["ok"] is False
    assert client.calls == 0


def test_completed_binding_reports_the_scanners_openid(monkeypatch):
    """``user_openid`` 是扫码者本人的 openid —— 调用方拿它把扫码者设成管理员。

    这是唯一一个"谁是主人"由**平台背书**的时刻；丢了它，开放平台上就只能靠
    "第一个私聊的人"去猜（那条 bootstrap 只在名单为空时生效一次）。
    """
    session = _session()
    _stub_poll(monkeypatch, {
        "status": 2,
        "bot_appid": "1903565393",
        "bot_encrypt_secret": _encrypt("s", session.bind_key),
        "user_openid": "OPENID_OF_SCANNER",
    })

    result = asyncio_run(bind.poll_bind_result(session))
    assert result["user_openid"] == "OPENID_OF_SCANNER"


def test_missing_openid_is_an_empty_string_not_a_crash(monkeypatch):
    """平台没给（或改字段名）时给空串，别让上面那条路抛 —— 设不成管理员
    不该把整次绑定拖垮。"""
    session = _session()
    _stub_poll(monkeypatch, {
        "status": 2,
        "bot_appid": "1903565393",
        "bot_encrypt_secret": _encrypt("s", session.bind_key),
    })

    assert asyncio_run(bind.poll_bind_result(session))["user_openid"] == ""
