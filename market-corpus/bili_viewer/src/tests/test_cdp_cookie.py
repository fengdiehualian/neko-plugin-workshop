"""cdp_cookie 模块单元测试：Cookie 过滤、Keep 名单、WS 帧封包等纯函数。

与 test_bili.py 一致：模块在函数内导入，避免 pytest 收集期环境差异。
"""

from __future__ import annotations


def _cdp():
    from plugin.plugins.bili_viewer import cdp_cookie

    return cdp_cookie


def _cookie(name, value, domain=".bilibili.com"):
    return {"name": name, "value": value, "domain": domain}


def test_extract_bili_cookie_keeps_login():
    cdp_cookie = _cdp()
    cookies = [
        _cookie("SESSDATA", "abc123"),
        _cookie("bili_jct", "jct456"),
        _cookie("DedeUserID", "123456"),
        _cookie("other_site", "zzz", ".taobao.com"),
        _cookie("DedeUserID__ckMd5", "hash1"),
    ]
    out = cdp_cookie._extract_bili_cookie(cookies)
    assert "SESSDATA=abc123" in out
    assert "bili_jct=jct456" in out
    assert "DedeUserID=123456" in out
    assert "other_site" not in out
    assert out.count("; ") == 3


def test_extract_bili_cookie_skips_non_keep_names():
    cdp_cookie = _cdp()
    cookies = [_cookie("_uuid", "u1"), _cookie("buvid3", "v3"), _cookie("unknown", "x")]
    out = cdp_cookie._extract_bili_cookie(cookies)
    assert "_uuid" not in out  # 不在 _KEEP_NAMES 中
    assert "buvid3=v3" in out
    assert "unknown" not in out


def test_extract_bili_cookie_deduplicates():
    cdp_cookie = _cdp()
    cookies = [_cookie("SESSDATA", "a"), _cookie("SESSDATA", "b"), _cookie("SESSDATA", "a")]
    out = cdp_cookie._extract_bili_cookie(cookies)
    assert out.count("SESSDATA=") == 1


def test_extract_bili_cookie_skips_hostonly_non_bili():
    cdp_cookie = _cdp()
    cookies = [
        {"name": "SESSDATA", "value": "secret", "domain": "example.com"},
        {"name": "SESSDATA", "value": "real", "domain": ".bilibili.com"},
    ]
    out = cdp_cookie._extract_bili_cookie(cookies)
    assert "real" in out
    assert "secret" not in out


def test_extract_bili_cookie_filters_injection_values():
    cdp_cookie = _cdp()
    cookies = [_cookie("SESSDATA", 'x; Set-Cookie: evil=1'), _cookie("bili_jct", 'a"b'), _cookie("buvid3", "ok")]
    out = cdp_cookie._extract_bili_cookie(cookies)
    assert "SESSDATA" not in out  # 含 ; 等注入字符被丢弃
    assert "bili_jct" not in out  # 含引号被丢弃
    assert "buvid3=ok" in out


def test_keep_names_contains_core_login():
    cdp_cookie = _cdp()
    assert "SESSDATA" in cdp_cookie._KEEP_NAMES
    assert "bili_jct" in cdp_cookie._KEEP_NAMES
    assert "DedeUserID" in cdp_cookie._KEEP_NAMES
    assert "buvid3" in cdp_cookie._KEEP_NAMES


def test_ws_send_frame_single_fragment():
    """验证单帧文本帧封包结构（FIN+opcode=1, mask, payload）。"""
    cdp_cookie = _cdp()

    class _Fake:
        def __init__(self):
            self.buf = b""

        def sendall(self, b):
            self.buf += b

    f = _Fake()
    cdp_cookie._ws_send(f, b"hello")
    assert f.buf[0] == 0x81  # FIN + text
    assert f.buf[1] == 0x80 | 5  # masked + len 5
    mask = f.buf[2:6]
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(f.buf[6:]))
    assert payload == b"hello"


def test_ws_send_frame_long_payload_126():
    cdp_cookie = _cdp()

    class _Fake:
        def __init__(self):
            self.buf = b""

        def sendall(self, b):
            self.buf += b

    f = _Fake()
    payload = b"x" * 200
    cdp_cookie._ws_send(f, payload)
    assert f.buf[1] == 0x80 | 126
    assert int.from_bytes(f.buf[2:4], "big") == 200
