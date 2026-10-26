"""bili_viewer 单元测试：纯逻辑部分（不依赖网络）。

与 code_sandbox 测试一致：插件模块在函数内导入，conftest.py 注入 sys.path，
避免 isort first-party 判定在本地/CI 间分歧。
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib

# ══════════ BV 号解析 ══════════


def test_parse_bvid() -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, _parse_bvid

    assert _parse_bvid("BV1xx411c7mD") == "BV1xx411c7mD"
    assert _parse_bvid("https://www.bilibili.com/video/BV1xx411c7mD?p=2") == "BV1xx411c7mD"
    try:
        _parse_bvid("av12345")
        raise AssertionError("should raise")
    except BiliError:
        pass
    try:
        _parse_bvid("随便一个链接")
        raise AssertionError("should raise")
    except BiliError:
        pass


# ══════════ 时长/时间格式化 ══════════


def test_fmt_duration() -> None:
    from plugin.plugins.bili_viewer.bili import _fmt_duration

    assert _fmt_duration(0) == "0:00"
    assert _fmt_duration(65) == "1:05"
    assert _fmt_duration(3661) == "1:01:01"
    assert _fmt_duration(None) == "0:00"


def test_fmt_time() -> None:
    from plugin.plugins.bili_viewer.bili import _fmt_time

    assert "2020" in _fmt_time(1577836800)
    assert _fmt_time(0) != ""


# ══════════ wbi 签名 ══════════


def test_wbi_sign() -> None:
    from plugin.plugins.bili_viewer.bili import _wbi_sign

    mixin = "a" * 32
    params = {"keyword": "测试", "page": 1}
    signed = _wbi_sign(params, mixin)
    assert "wts" in signed
    assert "w_rid" in signed
    assert len(signed["w_rid"]) == 32
    # 校验 md5：排序后的 query + mixin
    items = sorted((k, str(v)) for k, v in signed.items() if k != "w_rid")
    import urllib.parse

    query = urllib.parse.urlencode(items)
    expect = hashlib.md5((query + mixin).encode()).hexdigest()
    assert signed["w_rid"] == expect


def test_mixin_table_length() -> None:
    from plugin.plugins.bili_viewer.bili import _MIXIN_TABLE

    assert len(_MIXIN_TABLE) == 64
    assert sorted(_MIXIN_TABLE) == list(range(64))


# ══════════ 弹幕协议封包/解包 ══════════


def test_bili_pack_unpack_roundtrip() -> None:
    from plugin.plugins.bili_viewer.bili import _bili_pack, _bili_unpack

    body = b'{"hello":"world"}'
    pkt = _bili_pack(body, op=7, version=1)
    assert len(pkt) == 16 + len(body)
    total, header_len, version, op, seq = struct.unpack_from(">IHHII", pkt, 0)
    assert total == len(pkt) and header_len == 16 and version == 1 and op == 7 and seq == 1
    unpacked = _bili_unpack(pkt)
    assert len(unpacked) == 1
    assert unpacked[0] == (7, 1, body)


def test_bili_unpack_multiple() -> None:
    from plugin.plugins.bili_viewer.bili import _bili_pack, _bili_unpack

    buf = _bili_pack(b"a", op=5, version=0) + _bili_pack(b"b", op=2, version=1)
    out = _bili_unpack(buf)
    assert len(out) == 2
    assert out[0] == (5, 0, b"a")
    assert out[1] == (2, 1, b"b")


def test_danmaku_messages_zlib() -> None:
    from plugin.plugins.bili_viewer.bili import _bili_pack, _danmaku_messages

    inner = _bili_pack('{"cmd":"DANMU_MSG","info":[1,"你好"]}'.encode(), op=5, version=0)
    compressed = zlib.compress(inner)
    outer = _bili_pack(compressed, op=5, version=2)
    msgs = _danmaku_messages(outer)
    assert len(msgs) == 1
    assert msgs[0][0] == 0
    assert b"DANMU_MSG" in msgs[0][1]


def test_danmaku_messages_skips_non5() -> None:
    from plugin.plugins.bili_viewer.bili import _bili_pack, _danmaku_messages

    buf = _bili_pack(b"[]", op=2, version=1)  # 心跳响应
    assert _danmaku_messages(buf) == []


# ══════════ 搜索类型映射 ══════════


def test_search_type_mapping() -> None:
    from plugin.plugins.bili_viewer.bili import search

    # type 映射逻辑不直接导出，验证默认值路径不会崩（keyword 校验）
    try:
        search("", "video")
        raise AssertionError("should raise for empty keyword")
    except Exception as exc:  # noqa: BLE001
        assert "关键词" in str(exc) or "keyword" in str(exc)


def test_wrap_err_shape() -> None:
    from plugin.plugins.bili_viewer import _wrap_err
    from plugin.plugins.bili_viewer.bili import BiliError

    out = _wrap_err(BiliError("测试错误", "TEST_CODE"))
    assert out["is_error"] is True
    assert out["error"] == "TEST_CODE"
    assert "测试错误" in out["output"]


# ══════════ 账号 / 稿件（登录态） ══════════


def test_csrf_extract(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc123; bili_jct=xyz789; other=1")
    assert bili._csrf() == "xyz789"
    monkeypatch.delenv("BILI_COOKIE")
    assert bili._csrf() == ""


def test_require_login(monkeypatch) -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, _require_login

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    try:
        _require_login()
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "NEED_LOGIN"


def test_resolve_uid_owner_alias(monkeypatch) -> None:
    """owner/main/主人/主账号/大号 必须解析为主账号 uid，绝不按昵称搜索（v0.8.1 关注错人修复）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=s; bili_jct=c; DedeUserID=88888888; sid=1")
    for alias in ("owner", "main", "主人", "主账号", "大号"):
        assert bili._resolve_uid(alias, "yui") == 88888888

    # 未配置主账号时：抛 NEED_MAIN_LOGIN，绝不过度回落到昵称搜索
    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_main_cookie_from_file", lambda: "")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=s2; bili_jct=c2; DedeUserID=99999999")
    try:
        bili._resolve_uid("owner", "yui")
        raise AssertionError("should raise")
    except bili.BiliError as exc:
        assert exc.code == "NEED_MAIN_LOGIN"


def test_edit_video_requires_cookie(monkeypatch) -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, edit_video

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    try:
        edit_video("BV1xx411c7mD", title="新标题")
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "NEED_LOGIN"


def test_edit_video_wbi_signed_query(monkeypatch) -> None:
    """v0.7.6 复查修复：/x/vu/edit 必须带 wbi 签名 query（wts+w_rid），否则 -352。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=m; bili_jct=mj")
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url, form, timeout=12.0, account="main", query=None):
        calls.append((url, dict(form or {}), dict(query or {})))
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post", fake_post)
    monkeypatch.setattr(bili, "video_info", lambda bvid, account="main": {"aid": 123, "title": "旧标题", "desc": "", "tags": ["a"]})
    monkeypatch.setattr(bili, "_get_mixin_key", lambda: "m" * 32)
    r = bili.edit_video("BV1xx411c7mD", title="新标题")
    assert r["ok"] is True
    assert len(calls) == 1
    url, form, query = calls[0]
    assert "/x/vu/edit" in url
    assert form["title"] == "新标题"
    assert "wts" in query and "w_rid" in query


def test_edit_video_default_account_uses_yui(monkeypatch) -> None:
    """v0.7.9 复查优化：未指定账号时 edit_video 默认走 YUI 小号。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj")
    calls: list[tuple[str, str]] = []

    def fake_post(url, form, timeout=12.0, account="main", query=None):
        calls.append((url, account))
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post", fake_post)
    monkeypatch.setattr(bili, "video_info", lambda bvid, account="main": {"aid": 123, "title": "t", "desc": "", "tags": ["a"]})
    monkeypatch.setattr(bili, "_get_mixin_key", lambda: "m" * 32)
    r = bili.edit_video("BV1xx411c7mD", title="新标题")
    assert r["ok"] is True
    assert calls[0][1] == "yui"  # 默认小号


def test_my_info_default_account_uses_yui(monkeypatch) -> None:
    """v0.7.9 复查优化：未指定账号时账号信息查询默认走 YUI 小号。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj")
    seen: list[str] = []

    def fake_get(url, params=None, timeout=12.0, account="main"):
        seen.append(account)
        return {"code": 0, "data": {"mid": 9, "uname": "yui", "level_info": {"current_level": 5}, "vipStatus": 1, "money": 1}}

    monkeypatch.setattr(bili, "_http_get", fake_get)
    info = bili.my_info()
    assert seen == ["yui", "yui"]  # nav + relation/stat 补查（v0.7.11+），两次都走小号
    assert info["mid"] == 9


def test_live_send_requires_cookie(monkeypatch) -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, live_send_danmaku

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    try:
        live_send_danmaku("1", "你好")
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "NEED_LOGIN"


def test_msg_length_validation(monkeypatch) -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, live_send_danmaku

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc; bili_jct=tok")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=abc; bili_jct=tok")  # 默认 yui
    try:
        live_send_danmaku("1", "这" * 40)
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "MSG_TOO_LONG"
    monkeypatch.delenv("BILI_COOKIE", raising=False)


def test_receive_ext_dom_stores_realtime(monkeypatch) -> None:
    """扩展页面 DOM 实况推送：存储并 120s 内可读（v0.6.34）。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin, web_ui

    inst = BiliViewerPlugin(ctx=None)
    now = int(__import__("time").time())
    resp = inst._receive_ext_dom({
        "url": "https://live.bilibili.com/1025219",
        "kind": "live",
        "title": "北极僵尸从入门到入土",
        "uname": "干凝冰羽",
        "online": "15",
        "area": "星际争霸",
        "danmaku": [{"user": "观众A", "text": "666"}, {"user": "观众B", "text": "毒爆一个神两个"}],
        "at": now,
    })
    assert resp["ok"] is True
    dom = inst._ext_dom()
    assert dom is not None and dom["kind"] == "live"
    assert dom["title"] == "北极僵尸从入门到入土"
    assert len(dom["danmaku"]) == 2
    # 过期后不可读
    stale = {"kind": "live", "at": now - web_ui._EXT_ONLINE_WINDOW - 10}
    inst._ext_dom_data = stale
    assert inst._ext_dom() is None


def test_receive_ext_shot_stores_and_see_screen(monkeypatch) -> None:
    """扩展截屏推送：落盘、120s 内可读，bili_see_screen 返回画面路径（v0.6.36）。"""
    import asyncio
    import base64

    from plugin.plugins.bili_viewer import BiliViewerPlugin, web_ui

    inst = BiliViewerPlugin(ctx=None)
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0fakejpegdata").decode()
    now = int(__import__("time").time())
    resp = inst._receive_ext_shot({
        "image": f"data:image/jpeg;base64,{jpeg}",
        "url": "https://live.bilibili.com/1025219",
        "title": "北极僵尸从入门到入土",
        "at": now,
    })
    assert resp["ok"] is True
    shot = inst._ext_shot()
    assert shot is not None and shot["title"] == "北极僵尸从入门到入土"
    assert shot["size"] > 0 and os.path.exists(shot["path"])
    out = asyncio.run(inst.bili_see_screen())
    assert out["is_error"] is False and shot["path"] in out["output"]
    # 过期后不可读
    inst._ext_shot_data = {"at": now - web_ui._EXT_ONLINE_WINDOW - 10, "path": shot["path"]}
    out2 = asyncio.run(inst.bili_see_screen())
    assert out2["is_error"] is True and out2["error"] == "NO_SHOT"


def test_render_see_live_and_video() -> None:
    from plugin.plugins.bili_viewer.bili import render_see

    live = render_see({
        "kind": "live", "title": "测试直播间", "uname": "主播A", "status": "直播中",
        "online": 123, "parent_area": "网游", "area": "星际争霸",
        "recent_danmaku": [{"user": "观众1", "text": "666"}],
    })
    assert "测试直播间" in live and "观众1：666" in live and "人气：123" in live
    video = render_see({
        "kind": "video", "title": "测试视频", "uname": "UP主B", "duration": 125,
        "tname": "游戏", "view": 1000, "recent_danmaku": ["前排"], "subtitle_head": "大家好",
    })
    assert "测试视频" in video and "UP主B" in video and "2:05" in video and "前排" in video and "大家好" in video


def test_live_send_danmaku_uses_msg_send(monkeypatch) -> None:
    """直播弹幕走 /msg/send，roomid 用页面号（不能用 room_init 解析号，v0.6.30）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc; bili_jct=tok")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=abc; bili_jct=tok")  # 默认 yui（v0.7.36+）
    posted: dict = {}

    def _fake_post(url, form, timeout=12.0, account="main"):
        assert url.endswith("/msg/send")
        posted.update(url=url, form=form, account=account)
        return {"code": 0, "message": "0", "data": {"dmid": "99999"}}

    monkeypatch.setattr(bili, "_http_post", _fake_post)
    try:
        result = bili.live_send_danmaku("live.bilibili.com/1025219", "美味的食材")
    finally:
        monkeypatch.delenv("BILI_COOKIE", raising=False)
    assert result["ok"] is True
    assert result["room_id"] == 1025219
    assert posted["form"]["roomid"] == 1025219
    assert posted["form"]["msg"] == "美味的食材"
    assert posted["form"]["csrf"] == "tok"
    assert posted["form"]["csrf_token"] == "tok"


def test_no_csrf_for_write(monkeypatch) -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, live_send_danmaku

    # 有 SESSDATA 但缺 bili_jct：应明确提示缺 CSRF（默认 yui，需配 yui 侧 cookie）
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=abc")
    try:
        live_send_danmaku("1", "你好")
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "NO_CSRF"
    monkeypatch.delenv("BILI_COOKIE", raising=False)


def test_video_send_danmaku_requires_cookie(monkeypatch) -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, send_video_danmaku

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    try:
        send_video_danmaku("BV1xx411c7mD", "你好")
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "NEED_LOGIN"


def test_video_send_danmaku_posts_with_cid(monkeypatch) -> None:
    """视频弹幕：解析第一P cid，带 csrf 表单投递，progress 毫秒换算，WBI 签名。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc; bili_jct=tok")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=abc; bili_jct=tok")  # 默认 yui
    posted: dict = {}

    def _fake_video_info(bvid, account="main"):
        assert bvid == "BV1xx411c7mD"
        return {"title": "测试", "pages": [{"cid": 10086}, {"cid": 20086}]}

    def _fake_post(url, form, timeout=12.0, account="main"):
        assert url.endswith("/x/v2/dm/post")
        posted.update(url=url, form=form, account=account)
        return {"code": 0, "message": "0", "data": {"dmid": 900123}}  # v0.7.50: 需 dmid 才确认成功

    monkeypatch.setattr(bili, "video_info", _fake_video_info)
    monkeypatch.setattr(bili, "_http_post", _fake_post)
    monkeypatch.setattr(bili, "_get_mixin_key", lambda: "mixin-key")
    try:
        result = bili.send_video_danmaku("BV1xx411c7mD", "前排围观", progress_ms=45000)
    finally:
        monkeypatch.delenv("BILI_COOKIE", raising=False)
    assert result["ok"] is True
    assert posted["form"]["oid"] == 10086  # 第一P cid
    assert posted["form"]["bvid"] == "BV1xx411c7mD"  # bvid 必填（缺失返回 -400 请求错误）
    assert posted["form"]["msg"] == "前排围观"
    assert posted["form"]["progress"] == 45000
    assert posted["form"]["csrf"] == "tok"
    # WBI 签名：wts 与 w_rid 必须存在（缺失触发 -352 风控）
    assert posted["form"]["wts"] is not None
    assert posted["form"]["w_rid"] is not None


def test_video_send_danmaku_keeps_biz_code(monkeypatch) -> None:
    """视频弹幕：API 业务失败时保留原始 BIZ code（如 BIZ_-352），供面板诊断定位风控。"""
    from plugin.plugins.bili_viewer import bili
    from plugin.plugins.bili_viewer.bili import BiliError

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc; bili_jct=tok")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=abc; bili_jct=tok")  # 默认 yui

    def _fake_video_info(bvid, account="main"):
        return {"title": "测试", "pages": [{"cid": 10086}]}

    def _fake_post(url, form, timeout=12.0, account="main"):
        raise BiliError("请求错误", "BIZ_-352")

    monkeypatch.setattr(bili, "video_info", _fake_video_info)
    monkeypatch.setattr(bili, "_http_post", _fake_post)
    try:
        send_video_danmaku = bili.send_video_danmaku
        send_video_danmaku("BV1xx411c7mD", "666")
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "SEND_FAILED:BIZ_-352"
    finally:
        monkeypatch.delenv("BILI_COOKIE", raising=False)


def test_video_send_danmaku_msg_limit(monkeypatch) -> None:
    from plugin.plugins.bili_viewer.bili import BiliError, send_video_danmaku

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc; bili_jct=tok")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=abc; bili_jct=tok")  # 默认 yui
    try:
        send_video_danmaku("BV1xx411c7mD", "这" * 120)
        raise AssertionError("should raise")
    except BiliError as exc:
        assert exc.code == "MSG_TOO_LONG"
    monkeypatch.delenv("BILI_COOKIE", raising=False)


def test_live_info_fallback_uses_wbi(monkeypatch) -> None:
    """get_info 风控时回退到 getInfoByRoom，且必须带 wbi 签名。"""
    from plugin.plugins.bili_viewer import bili
    from plugin.plugins.bili_viewer.bili import BiliError

    def _raise(*_args, **_kwargs):
        raise BiliError("风控", "RISK_CONTROL")

    called = {"wbi": False}

    def _fake_wbi_get(url, params, account="main"):
        called["wbi"] = True
        assert "/getInfoByRoom" in url
        return {"code": 0, "data": {"room_info": {"room_id": 1, "title": "测试直播间", "live_status": 1, "online": 99}}}

    monkeypatch.setattr(bili, "_resolve_room", lambda room: {"room_id": 1, "live_status": 1})
    monkeypatch.setattr(bili, "_http_get", _raise)
    monkeypatch.setattr(bili, "_wbi_get", _fake_wbi_get)
    info = bili.live_info("1")
    assert called["wbi"] is True
    assert info["title"] == "测试直播间"
    assert info["status_text"] == "直播中"


def test_live_danmaku_uses_wbi(monkeypatch) -> None:
    """getDanmuInfo 必须带 wbi 签名；连接阶段再走 websocket。"""
    import types

    from plugin.plugins.bili_viewer import bili

    called = {"wbi": False}

    def _fake_wbi_get(url, params, account="main"):
        called["wbi"] = True
        assert "/getDanmuInfo" in url
        return {"code": 0, "data": {"token": "t0k", "host_list": [{"host": "broadcastlv.chat.bilibili.com", "wss_port": 443}]}}

    def _fake_ws_connect(host, port, path, timeout):
        return types.SimpleNamespace(settimeout=lambda _t: None, close=lambda: None)

    def _fake_resolve(room):
        return {"room_id": 1, "live_status": 1}

    monkeypatch.setattr(bili, "_http_get", lambda *a, **k: {"code": 0, "data": {"room_id": 1, "live_status": 1}})
    monkeypatch.setattr(bili, "_wbi_get", _fake_wbi_get)
    monkeypatch.setattr(bili, "_resolve_room", _fake_resolve)
    monkeypatch.setattr(bili, "_ws_connect", _fake_ws_connect)
    monkeypatch.setattr(bili, "_ws_send", lambda *a, **k: None)
    monkeypatch.setattr(bili, "_ws_recv", lambda *a, **k: b"")  # 无消息，直到超时
    try:
        bili.live_danmaku("1", duration=3, limit=5)
        raise AssertionError("should raise NO_DANMAKU")
    except Exception as exc:
        assert getattr(exc, "code", "") == "NO_DANMAKU"
    assert called["wbi"] is True


def test_parse_danmaku_proto() -> None:
    """seg.so protobuf 弹幕解析：文本 field 7、进度 field 15。"""
    from plugin.plugins.bili_viewer.bili import _parse_danmaku_proto

    def _varint(v: int) -> bytes:
        out = b""
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out += bytes([b | 0x80])
            else:
                out += bytes([b])
                return out

    def _field(no: int, payload: bytes) -> bytes:
        return _varint((no << 3) | 2) + _varint(len(payload)) + payload

    def _vfield(no: int, v: int) -> bytes:
        return _varint((no << 3) | 0) + _varint(v)

    def _elem(progress: int, text: str) -> bytes:
        return _vfield(1, 12345) + _vfield(3, 0) + _field(7, text.encode("utf-8")) + _vfield(15, progress)

    raw = _field(1, _elem(12, "第一条弹幕")) + _field(1, _elem(35, "第二条弹幕")) + _vfield(2, 1)
    items = _parse_danmaku_proto(raw)
    assert items == [(12, "第一条弹幕"), (35, "第二条弹幕")]


def test_video_danmaku_uses_seg_so(monkeypatch) -> None:
    """video_danmaku 应优先走 seg.so protobuf，而非失效的 dm/list.so XML。"""
    from plugin.plugins.bili_viewer import bili

    def _fake_video_info(bvid, account="main"):
        return {"pages": [{"cid": 1001, "page": 1, "part": "正片"}]}

    def _fake_fetch_raw(url, params):
        assert "/x/v2/dm/web/seg.so" in url
        # 第一段返回 1 条弹幕；后续段返回空（触发采集结束）
        if params["segment_index"] != 1:
            return b""
        assert params == {"type": 1, "oid": 1001, "segment_index": 1}

        def _varint(v):
            out = b""
            while True:
                b = v & 0x7F
                v >>= 7
                if v:
                    out += bytes([b | 0x80])
                else:
                    out += bytes([b])
                    return out

        def _field(no, payload):
            return _varint((no << 3) | 2) + _varint(len(payload)) + payload

        def _vfield(no, v):
            return _varint((no << 3) | 0) + _varint(v)

        elem = _field(7, "测试弹幕".encode()) + _vfield(15, 42)
        return _field(1, elem)

    monkeypatch.setattr(bili, "video_info", _fake_video_info)
    monkeypatch.setattr(bili, "_fetch_raw", _fake_fetch_raw)
    got = bili.video_danmaku("BV1xx411c7mD", limit=5)
    assert got == ["[0:42] 测试弹幕"]


# ══════════ 当前观看感知（通用窗口感知） ══════════


def test_parse_window_title_bili_video() -> None:
    from plugin.plugins.bili_viewer.bili import parse_window_title

    p = parse_window_title("【原神】4.0 剧情PV_哔哩哔哩_bilibili", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
    assert p["is_bili"] is True
    assert p["kind"] == "video"
    assert p["site"] == "bilibili"
    assert p["is_browser"] is True
    assert p["subject"] == "【原神】4.0 剧情PV"


def test_parse_window_title_bili_live() -> None:
    from plugin.plugins.bili_viewer.bili import parse_window_title

    p = parse_window_title("某某游戏直播中_哔哩哔哩直播", "chrome.exe")
    assert p["is_bili"] is True and p["kind"] == "live" and p["subject"] == "某某游戏直播中"


def test_parse_window_title_site() -> None:
    from plugin.plugins.bili_viewer.bili import parse_window_title

    p = parse_window_title("如何评价XXX - 知乎 - Google Chrome", "chrome.exe")
    assert p["is_bili"] is False
    assert p["site"] == "zhihu"
    assert p["kind"] == "web"
    assert "如何评价XXX" in p["subject"]


def test_parse_window_title_app() -> None:
    from plugin.plugins.bili_viewer.bili import parse_window_title

    p = parse_window_title("工作文档 - Word", "C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE")
    assert p["is_bili"] is False
    assert p["kind"] == "app"
    assert p["site"] == ""
    assert p["app"] == "winword.exe"
    assert p["subject"] == "工作文档 - Word"


def test_parse_window_title_empty() -> None:
    from plugin.plugins.bili_viewer.bili import parse_window_title

    p = parse_window_title("", "")
    assert p["kind"] == "unknown" and p["is_bili"] is False


def test_now_watching_app(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setattr(bili, "_visible_windows", lambda: [{"title": "记事本 - 未命名", "exe": "notepad.exe"}])
    info = bili.now_watching()
    assert info["is_bili"] is False
    assert info["kind"] == "app"
    assert info["app"] == "notepad.exe"


def test_now_watching_bili_search_fails(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili
    from plugin.plugins.bili_viewer.bili import BiliError

    def _raise(*_args, **_kwargs):
        raise BiliError("风控", "RISK_CONTROL")

    monkeypatch.setattr(bili, "_visible_windows", lambda: [{"title": "测试视频_哔哩哔哩_bilibili", "exe": "chrome.exe"}])
    monkeypatch.setattr(bili, "search", _raise)
    info = bili.now_watching()
    assert info["is_bili"] is True and info["kind"] == "video"
    assert "match" not in info and "video" not in info


# ══════════ 扩展标签页精确感知（parse_bili_url / now_watching(tab=)） ══════════


def test_parse_bili_url_video():
    from plugin.plugins.bili_viewer.bili import parse_bili_url

    p = parse_bili_url("https://www.bilibili.com/video/BV1GJ411x7h7?p=2", "原神 PV_哔哩哔哩_bilibili")
    assert p is not None and p["is_bili"] is True
    assert p["kind"] == "video" and p["bvid"] == "BV1GJ411x7h7"
    assert p["subject"] == "原神 PV"


def test_parse_bili_url_live():
    from plugin.plugins.bili_viewer.bili import parse_bili_url

    p = parse_bili_url("https://live.bilibili.com/123456", "直播中：测试-哔哩哔哩直播")
    assert p is not None and p["is_bili"] is True
    assert p["kind"] == "live" and p["room_id"] == "123456"


def test_parse_bili_url_bangumi_and_shortlink():
    from plugin.plugins.bili_viewer.bili import parse_bili_url

    ep = parse_bili_url("https://www.bilibili.com/bangumi/play/ep123456", "番剧_哔哩哔哩_bilibili")
    assert ep is not None and ep["kind"] == "bangumi" and ep["ep"] == "123456"
    b23 = parse_bili_url("https://b23.tv/abc123", "测试")
    assert b23 is not None and b23["is_bili"] is True


def test_parse_bili_url_non_bili_returns_none():
    from plugin.plugins.bili_viewer.bili import parse_bili_url

    assert parse_bili_url("https://www.zhihu.com/question/1", "知乎") is None
    assert parse_bili_url("", "") is None


def test_now_watching_prefers_extension_tab(monkeypatch):
    """扩展标签页优先：bvid 直接精确匹配视频详情，无需窗口/搜索。"""
    from plugin.plugins.bili_viewer import bili

    called = {}

    def _fake_video_info(bvid, account="main"):
        called["bvid"] = bvid
        return {"title": "精确视频", "owner_name": "UP", "view": 10, "danmaku": 2,
                "duration": 3600, "pubdate": 1700000000, "desc": "描述"}

    monkeypatch.setattr(bili, "video_info", _fake_video_info)
    monkeypatch.setattr(bili, "pick_now_watching", lambda: {"kind": "unknown"})  # 不应被调用
    info = bili.now_watching(tab={"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "title": "精确视频_哔哩哔哩_bilibili", "at": 0})
    assert info["from"] == "extension-tab"
    assert info["kind"] == "video" and info["bvid"] == "BV1GJ411x7h7"
    assert called.get("bvid") == "BV1GJ411x7h7"
    assert info["video"]["title"] == "精确视频"


def test_now_watching_extension_tab_live(monkeypatch):
    """扩展标签页直播：room_id 直接匹配直播间信息。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setattr(bili, "live_info", lambda room: {"title": "测试直播间", "uname": "主播",
                                                         "status_text": "直播中", "online": 88,
                                                         "area": "网游", "parent_area": "游戏"})
    info = bili.now_watching(tab={"url": "https://live.bilibili.com/666", "title": "测试直播间-哔哩哔哩直播", "at": 0})
    assert info["kind"] == "live" and info["room_id"] == "666"
    assert info["live"]["title"] == "测试直播间" and info["live"]["online"] == 88


def test_now_watching_extension_tab_non_bili_uses_title(monkeypatch):
    """非 B站标签页：按标题通用解析（site 识别）。"""
    from plugin.plugins.bili_viewer import bili

    info = bili.now_watching(tab={"url": "https://www.zhihu.com/question/1", "title": "如何评价XX - 知乎 - Google Chrome", "at": 0})
    assert info["from"] == "extension-tab"
    assert info["site"] == "zhihu" and info["is_bili"] is False


# ══════════ 实时感知同步（后台注入） ══════════


def test_sync_text_bili() -> None:
    from plugin.plugins.bili_viewer import _sync_text

    t = _sync_text({"kind": "video", "is_bili": True, "subject": "原神 PV"})
    assert t is not None and "原神 PV" in t and "B站视频" in t


def test_sync_text_site() -> None:
    from plugin.plugins.bili_viewer import _sync_text

    t = _sync_text({"kind": "web", "is_bili": False, "site": "zhihu", "subject": "如何评价"})
    assert t is not None and "zhihu" in t and "如何评价" in t


def test_sync_text_unknown() -> None:
    from plugin.plugins.bili_viewer import _sync_text

    assert _sync_text({"kind": "unknown", "is_bili": False}) is None


def test_window_fingerprint() -> None:
    from plugin.plugins.bili_viewer import _window_fingerprint

    a = _window_fingerprint({"app": "chrome.exe", "site": "bilibili", "kind": "video", "subject": "A"})
    b = _window_fingerprint({"app": "chrome.exe", "site": "bilibili", "kind": "video", "subject": "B"})
    assert a != b
    assert _window_fingerprint({"app": "x", "site": "", "kind": "app", "subject": "y"}) != a


def test_is_terminal_window() -> None:
    from plugin.plugins.bili_viewer.bili import _is_terminal_window

    for app in ("windowsterminal.exe", "powershell.exe", "cmd.exe", "python.exe", "pwsh.exe", "wt.exe", "bash.exe"):
        assert _is_terminal_window({"app": app, "kind": "app"}), app
    for app in ("chrome.exe", "msedge.exe", "steam.exe", "WeChat.exe", "explorer.exe", "QQMusic.exe"):
        assert not _is_terminal_window({"app": app, "kind": "app"}), app
    assert not _is_terminal_window({"app": "", "kind": "unknown"})


def test_is_neko_client() -> None:
    from plugin.plugins.bili_viewer.bili import _is_neko_client

    for app in ("N.E.K.O.exe", "n.e.k.o.exe", "neko.exe", "launcher.exe"):
        assert _is_neko_client({"app": app, "kind": "app"}), app
    assert not _is_neko_client({"app": "chrome.exe", "kind": "web"})


def test_skip_window() -> None:
    from plugin.plugins.bili_viewer.bili import _skip_window

    assert _skip_window({"kind": "unknown", "window": "x", "app": ""})
    assert _skip_window({"kind": "app", "window": "", "app": "steam.exe"})
    assert _skip_window({"kind": "app", "window": "term", "app": "powershell.exe"})
    assert _skip_window({"kind": "app", "window": "N.E.K.O", "app": "n.e.k.o.exe"})
    assert not _skip_window({"kind": "web", "window": "视频_哔哩哔哩_bilibili", "app": "chrome.exe"})


def test_window_score() -> None:
    from plugin.plugins.bili_viewer.bili import _window_score

    assert _window_score({"kind": "video", "is_bili": True, "site": "bilibili"}) == 100
    assert _window_score({"kind": "web", "is_bili": False, "site": "zhihu"}) == 90
    assert _window_score({"kind": "web", "is_bili": False, "site": ""}) == 80
    assert _window_score({"kind": "app", "is_bili": False, "site": ""}) == 70
    assert _window_score({"kind": "other", "is_bili": False, "site": ""}) == 60


def test_pick_now_watching(monkeypatch) -> None:
    """多窗口场景：聊天客户端在前台也不影响——后台的 B站视频优先。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setattr(
        bili,
        "_visible_windows",
        lambda: [
            {"title": "N.E.K.O", "exe": r"C:\apps\N.E.K.O.exe"},
            {"title": "Terminal", "exe": r"C:\Windows\System32\WindowsTerminal.exe"},
            {"title": "丧尸爆发合集_游戏热门视频 - Google Chrome", "exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
            {"title": "QQ", "exe": r"C:\Program Files\Tencent\QQ\QQ.exe"},
        ],
    )
    got = bili.pick_now_watching()
    assert got is not None
    assert got["app"] == "chrome.exe"
    assert "丧尸爆发合集" in got["subject"]


def test_pick_now_watching_all_skipped(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setattr(
        bili,
        "_visible_windows",
        lambda: [
            {"title": "N.E.K.O", "exe": r"C:\apps\N.E.K.O.exe"},
            {"title": "Terminal", "exe": r"C:\Windows\System32\WindowsTerminal.exe"},
        ],
    )
    assert bili.pick_now_watching() is None


def test_parse_bili_section_suffix() -> None:
    """B站新版视频页标题「视频_分区名」（不带'哔哩哔哩'字样）应识别为 B站视频。"""
    from plugin.plugins.bili_viewer.bili import parse_window_title

    p = parse_window_title("1939锦标赛事件到底发生了什么？一个视频给你讲清楚_游戏杂谈 - Google Chrome", "chrome.exe")
    assert p["is_bili"] is True and p["kind"] == "video"
    assert p["site"] == "bilibili"
    assert p["subject"] == "1939锦标赛事件到底发生了什么？一个视频给你讲清楚"


def test_parse_watt_toolkit_not_steam() -> None:
    """Steam++（Watt Toolkit）不应被误判为 Steam 站点。"""
    from plugin.plugins.bili_viewer.bili import parse_window_title

    p = parse_window_title("Watt Toolkit", r"D:\Steam++\Steam++.exe")
    assert p["site"] == "" and p["is_bili"] is False and p["kind"] == "app"


def test_pick_prefers_bili_over_watt_toolkit(monkeypatch) -> None:
    """Watt Toolkit 在前台/可见时，B站视频窗口仍应胜出。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setattr(
        bili,
        "_visible_windows",
        lambda: [
            {"title": "Watt Toolkit", "exe": r"D:\Steam++\Steam++.exe"},
            {"title": "1939锦标赛事件到底发生了什么？一个视频给你讲清楚_游戏杂谈 - Google Chrome", "exe": "chrome.exe"},
            {"title": "N.E.K.O", "exe": r"D:\SteamLibrary\steamapps\common\n.e.k.o\N.E.K.O.exe"},
        ],
    )
    got = bili.pick_now_watching()
    assert got is not None and got["site"] == "bilibili" and got["is_bili"] is True


def test_sync_text_prefix_is_inference() -> None:
    from plugin.plugins.bili_viewer import _sync_text

    t = _sync_text({"kind": "video", "is_bili": True, "subject": "原神 PV"})
    assert t is not None and "内部感知" in t and "B站视频" in t
    t2 = _sync_text({"kind": "app", "is_bili": False, "app": "steam.exe", "subject": "赛博朋克"})
    assert t2 is not None and "实时感知" in t2 and "steam.exe" in t2


def test_sync_text_live_marks_live() -> None:
    from plugin.plugins.bili_viewer import _sync_text

    t = _sync_text({"kind": "live", "is_bili": True, "subject": "某直播间"})
    assert t is not None and "B站直播" in t


def test_sync_text_live_carries_room_id() -> None:
    """直播感知文本必须带房间号，YUI 才能直接发弹幕/查详情。"""
    from plugin.plugins.bili_viewer import _sync_text

    t = _sync_text({"kind": "live", "is_bili": True, "subject": "点播我的卡组", "room_id": "1426057300"})
    assert t is not None and "房间号 1426057300" in t and "点播我的卡组" in t


def test_sync_text_video_carries_bvid() -> None:
    """视频感知文本带 BV 号，YUI 可直接查详情/弹幕。"""
    from plugin.plugins.bili_viewer import _sync_text

    t = _sync_text({"kind": "video", "is_bili": True, "subject": "原神 PV", "bvid": "BV1GJ411x7h7"})
    assert t is not None and "BV号 BV1GJ411x7h7" in t


def test_watcher_not_started_without_host(monkeypatch) -> None:
    """无宿主 ctx（测试环境）不启动后台线程。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = BiliViewerPlugin(ctx=None)
    assert inst._sync_thread is None


def test_ext_tab_property_does_not_shadow_method(monkeypatch) -> None:
    """扩展 tab 推送后 _ext_tab() 仍可调用（防止实例属性遮蔽方法的回归）。

    历史 bug：_receive_ext_tab 写 self._ext_tab 属性会遮蔽 def _ext_tab 方法，
    导致 watcher/感知调用 self._ext_tab() 抛 TypeError、感知静默退化。
    """
    import time as _t

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = BiliViewerPlugin(ctx=None)
    now = int(_t.time())
    inst._receive_ext_tab("https://www.bilibili.com/video/BV1GJ411x7h7", "测试_哔哩哔哩", now)
    tab = inst._ext_tab()
    assert tab is not None
    assert tab["url"].endswith("BV1GJ411x7h7")
    assert tab["title"] == "测试_哔哩哔哩"
    assert tab["at"] == float(now)


def test_ext_tab_expires_after_window(monkeypatch) -> None:
    """超过 120s 新鲜窗口后 _ext_tab() 返回 None。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = BiliViewerPlugin(ctx=None)
    inst._receive_ext_tab("https://www.bilibili.com/video/BV1GJ411x7h7", "测试", int(__import__("time").time()) - 200)
    assert inst._ext_tab() is None


def test_ext_tab_force_bypasses_window(monkeypatch) -> None:
    """v0.9.20：force=True 时每次询问强制重新检测——标签页数据即使超过新鲜窗口仍使用。"""
    import time as _t

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = BiliViewerPlugin(ctx=None)
    inst._receive_ext_tab("https://www.bilibili.com/video/BV1GJ411x7h7", "测试", int(_t.time()) - 200)
    assert inst._ext_tab() is None          # 默认：过期丢弃
    tab = inst._ext_tab(force=True)          # 感知工具：强制重新检测
    assert tab is not None
    assert tab["url"].endswith("BV1GJ411x7h7")


def test_watcher_not_started_when_disabled(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.setenv("BILLI_SYNC_DISABLE", "1")
    inst = BiliViewerPlugin(ctx=None)
    assert inst._sync_thread is None


# ══════════ BILI_COOKIE 配置注入 ══════════


def test_cookie_env_priority() -> None:
    from plugin.plugins.bili_viewer import _apply_cookie_from_config

    cfg = {"settings": {"bili_cookie": "cfg=SESSDATA_CFG"}}
    env = {"BILI_COOKIE": "env=SESSDATA_ENV"}
    assert _apply_cookie_from_config(cfg, env) == "env=SESSDATA_ENV"


def test_cookie_from_config_when_no_env() -> None:
    from plugin.plugins.bili_viewer import _apply_cookie_from_config

    cfg = {"settings": {"bili_cookie": "cfg=SESSDATA_CFG;bili_jct=abc"}}
    assert _apply_cookie_from_config(cfg, {}) == "cfg=SESSDATA_CFG;bili_jct=abc"


def test_cookie_empty_when_unset() -> None:
    from plugin.plugins.bili_viewer import _apply_cookie_from_config

    assert _apply_cookie_from_config({}, {}) == ""
    assert _apply_cookie_from_config({"settings": {"bili_cookie": "   "}}, {}) == ""
    assert _apply_cookie_from_config({"settings": {}}, {"BILI_COOKIE": ""}) == ""


# ══════════ 自动绑定（allow_cookie_scan / cookie_auto） ══════════


def test_allow_cookie_scan_values() -> None:
    from plugin.plugins.bili_viewer import _allow_cookie_scan

    assert _allow_cookie_scan({}) is False
    assert _allow_cookie_scan({"settings": {}}) is False
    assert _allow_cookie_scan({"settings": {"allow_cookie_scan": True}}) is True
    assert _allow_cookie_scan({"settings": {"allow_cookie_scan": "true"}}) is True
    assert _allow_cookie_scan({"settings": {"allow_cookie_scan": "1"}}) is True
    assert _allow_cookie_scan({"settings": {"allow_cookie_scan": False}}) is False
    assert _allow_cookie_scan({"settings": {"allow_cookie_scan": "no"}}) is False


def test_cookie_summary_no_leak() -> None:
    from plugin.plugins.bili_viewer import _cookie_summary

    assert _cookie_summary("") == "未配置"
    assert "SESSDATA" in _cookie_summary("SESSDATA=abc; bili_jct=xyz")
    assert "abc" not in _cookie_summary("SESSDATA=abc; bili_jct=xyz")  # 不泄露值
    assert _cookie_summary("buvid3=xx") == "已配置（无登录态字段）"


def test_cookie_uid_extraction() -> None:
    from plugin.plugins.bili_viewer import _cookie_uid

    assert _cookie_uid("") == ""
    assert _cookie_uid("buvid3=xx") == ""
    assert _cookie_uid("DedeUserID=123456; SESSDATA=a") == "123456"
    assert _cookie_uid("SESSDATA=a; DedeUserID=98765; bili_jct=b") == "98765"
    assert _cookie_uid("DedeUserID=abc") == ""  # 非数字不误取


def test_live_danmaku_duration_clamped(monkeypatch) -> None:
    """工具层 clamp duration 到 [1,30]，与实际采集上限一致。

    真实宿主把 LLM 的 JSON 参数展开为命名参数调用（entry trigger 路径），
    因此这里以命名参数形态调用；`_` 在真实调用中不含 "parameters" 键。
    """
    import asyncio

    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    captured: dict = {}
    def fake_live_danmaku(room, duration=8):
        captured["duration"] = duration
        return []

    monkeypatch.setattr(bili, "live_danmaku", fake_live_danmaku)
    inst = BiliViewerPlugin(ctx=None)

    # LLM 传了超大的 duration → 收紧到 30
    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(inst.bili_live_danmaku(room="1", duration=3600))
    finally:
        loop.close()
    assert captured["duration"] == 30
    assert r["is_error"] is False
    assert "采集 30 秒" in r["output"]

    # LLM 未提供 duration → 默认 8
    captured.clear()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(inst.bili_live_danmaku(room="1", duration=0))
    finally:
        loop.close()
    assert captured["duration"] == 8


def test_build_cookie_string_sanitizes() -> None:
    from plugin.plugins.bili_viewer.cookie_auto import build_cookie_string

    s = build_cookie_string(
        [("SESSDATA", "a"), ("bili_jct", "b"), ("SESSDATA", "dup"), ("bad", "x;y"), ("bad2", 'x"y'), ("ok", "v")]
    )
    assert s == "SESSDATA=a; bili_jct=b; ok=v"


def test_detect_no_browser(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import cookie_auto

    monkeypatch.setenv("LOCALAPPDATA", "Z:\\nonexistent\\dir")
    status, cookie, detail = cookie_auto.detect_bili_cookie()
    assert status == cookie_auto._STATUS_NO_BROWSER and cookie == "" and detail


def test_detect_locked_when_browser_running(monkeypatch, tmp_path) -> None:
    """模拟浏览器进程在运行且 Cookies 文件不可读 → 锁定状态。"""
    from plugin.plugins.bili_viewer import cookie_auto

    monkeypatch.setattr(
        cookie_auto,
        "AUTO_COOKIE_SOURCES",
        (("Chrome", os.path.join("Google", "Chrome", "User Data")),),
    )
    root = tmp_path / "Google" / "Chrome" / "User Data"
    (root / "Default" / "Network").mkdir(parents=True)
    with open(root / "Default" / "Network" / "Cookies", "w", encoding="utf-8") as f:
        f.write("")  # 文件存在但打开被模拟为锁定
    with open(root / "Local State", "w", encoding="utf-8") as f:
        f.write('{"os_crypt":{"encrypted_key":"QQ=="}}')
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(cookie_auto, "_load_aes_key", lambda path: b"\x00" * 32)  # 绕过真实 DPAPI
    monkeypatch.setattr(cookie_auto, "_open_cookies_db", lambda path: None)  # 模拟锁
    monkeypatch.setattr(cookie_auto, "_browser_process_running", lambda name: True)  # 浏览器在跑
    status, cookie, _ = cookie_auto.detect_bili_cookie()
    assert status == cookie_auto._STATUS_LOCKED and cookie == ""


def test_detect_db_error_when_browser_closed(monkeypatch, tmp_path) -> None:
    """浏览器进程已退出但 Cookie 库仍打不开 → 数据库异常（fail），不再误报 locked。"""
    from plugin.plugins.bili_viewer import cookie_auto

    monkeypatch.setattr(
        cookie_auto,
        "AUTO_COOKIE_SOURCES",
        (("Chrome", os.path.join("Google", "Chrome", "User Data")),),
    )
    root = tmp_path / "Google" / "Chrome" / "User Data"
    (root / "Default" / "Network").mkdir(parents=True)
    with open(root / "Default" / "Network" / "Cookies", "w", encoding="utf-8") as f:
        f.write("")
    with open(root / "Local State", "w", encoding="utf-8") as f:
        f.write('{"os_crypt":{"encrypted_key":"QQ=="}}')
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(cookie_auto, "_load_aes_key", lambda path: b"\x00" * 32)
    monkeypatch.setattr(cookie_auto, "_open_cookies_db", lambda path: None)
    monkeypatch.setattr(cookie_auto, "_browser_process_running", lambda name: False)  # 浏览器已关
    status, cookie, _ = cookie_auto.detect_bili_cookie()
    assert status == cookie_auto._STATUS_FAIL and cookie == ""


def test_receive_ext_shot_rejects_non_image_prefix(monkeypatch) -> None:
    """截屏推送必须 data:image/*;base64 前缀：其他 dataURL（如 text/html）拒绝落盘（v0.6.39）。"""
    import base64

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = BiliViewerPlugin(ctx=None)
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0fake").decode()
    resp = inst._receive_ext_shot({"image": f"data:text/html;base64,{jpeg}", "url": "https://x", "title": "t", "at": 1})
    assert resp["ok"] is False
    assert inst._ext_shot() is None


def test_receive_ext_shot_rejects_oversized(monkeypatch) -> None:
    """截屏 raw 超过 20MB 上限时拒绝落盘，防磁盘耗尽（v0.6.39）。"""
    import base64

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = BiliViewerPlugin(ctx=None)
    # 21MB 全零字节的 base64（避免 OOM：用 padding 构造）
    big_raw = b"\x00" * (21 * 1024 * 1024)
    payload = base64.b64encode(big_raw).decode()
    resp = inst._receive_ext_shot({"image": f"data:image/jpeg;base64,{payload}", "url": "https://x", "title": "t", "at": 1})
    assert resp["ok"] is False
    assert "过大" in resp.get("detail", "")


def test_receive_ext_dom_danmaku_capped(monkeypatch) -> None:
    """DOM 弹幕数组截断：条数与单条长度都设上限，防异常推送撑爆内存（v0.6.39）。"""
    import time as _t

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = BiliViewerPlugin(ctx=None)
    resp = inst._receive_ext_dom({
        "url": "https://live.bilibili.com/1025219",
        "kind": "live",
        "title": "直播",
        "danmaku": ["长弹幕" * 500] + [f"dm{i}" for i in range(500)],
        "at": int(_t.time()),
    })
    assert resp["ok"] is True
    dom = inst._ext_dom()
    assert dom is not None
    assert len(dom["danmaku"]) == inst._EXT_DOM_MAX_DANMAKU  # 100 条
    assert all(len(x) <= inst._EXT_DOM_MAX_DM_LEN for x in dom["danmaku"])  # 每条 ≤200 字


def test_sync_text_truncates_long_subject() -> None:
    """同步注入文本对超长标题截断，防止恶意/异常标题撑爆上下文（v0.6.39）。"""
    from plugin.plugins.bili_viewer import _sync_text

    t = _sync_text({"kind": "video", "is_bili": True, "subject": "超长标题" * 200})
    assert t is not None and "内部感知" in t
    assert len(t) < 1000


def test_live_send_danmaku_dedup_once(monkeypatch) -> None:
    """同一房间同一条弹幕 60 秒内重复触发只发一次（v0.6.38 幂等去重）。"""
    import asyncio

    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(bili, "live_send_danmaku", lambda room, text, account="main": (sent.append((room, text)), {"dmid": "D1", "server": "ok"})[1])
    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = object.__new__(BiliViewerPlugin)
    inst._ext_tab = lambda **kw: None

    r1 = asyncio.run(inst.bili_live_send_danmaku(room="1025219", text="主播好帅"))
    assert r1["is_error"] is False and r1["output"] == "弹幕已发送。"
    # 第二次（模拟模型重复触发/兜底入口再次调用）：同房间同内容 → 去重，不再真实发送
    r2 = asyncio.run(inst.bili_live_send_danmaku(room="1025219", text="主播好帅"))
    assert r2["is_error"] is False and r2["output"] == "弹幕已发送。"
    assert r2["result"].get("dedup") is True
    assert len(sent) == 1
    # 不同内容 → 允许再发
    r3 = asyncio.run(inst.bili_live_send_danmaku(room="1025219", text="再来一条"))
    assert r3["is_error"] is False and len(sent) == 2
    assert r3["result"].get("dedup") is None


def test_live_send_danmaku_dedup_expires(monkeypatch) -> None:
    """去重窗口过期后同一弹幕可再次发送。"""
    import asyncio

    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(bili, "live_send_danmaku", lambda room, text, account="main": (sent.append((room, text)), {"dmid": "D1", "server": "ok"})[1])
    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = object.__new__(BiliViewerPlugin)
    inst._ext_tab = lambda **kw: None

    asyncio.run(inst.bili_live_send_danmaku(room="1025219", text="过期测试"))
    inst._danmaku_sent.clear()  # 模拟窗口已过（property 只读，clear 内部 dict 即可）
    r = asyncio.run(inst.bili_live_send_danmaku(room="1025219", text="过期测试"))
    assert r["is_error"] is False and r["result"].get("dedup") is None
    assert len(sent) == 2


def test_video_send_danmaku_dedup_once(monkeypatch) -> None:
    """同一视频同一进度同一条弹幕 60 秒内重复触发只发一次。"""
    import asyncio

    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    sent: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        bili,
        "send_video_danmaku",
        lambda bvid, text, progress_ms=0, account="main": (sent.append((bvid, text, progress_ms)), {"dmid": "D2"})[1],
    )
    monkeypatch.delenv("BILLI_SYNC_DISABLE", raising=False)
    inst = object.__new__(BiliViewerPlugin)
    inst._ext_tab = lambda **kw: None

    r1 = asyncio.run(inst.bili_video_send_danmaku(bvid="BV1GJ411x7h7", text="镇站之宝", progress=5))
    assert r1["is_error"] is False
    r2 = asyncio.run(inst.bili_video_send_danmaku(bvid="BV1GJ411x7h7", text="镇站之宝", progress=5))
    assert r2["is_error"] is False and r2["result"].get("dedup") is True
    assert len(sent) == 1
    # 同视频不同进度 → 不判重
    r3 = asyncio.run(inst.bili_video_send_danmaku(bvid="BV1GJ411x7h7", text="镇站之宝", progress=30))
    assert r3["is_error"] is False and r3["result"].get("dedup") is None
    assert len(sent) == 2

# ══════════ v0.7.50：评论回读验证 / 弹幕 dmid 校验（防假成功） ══════════


def test_reply_visible_hit(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    def fake_wbi_get(url, params, timeout=12.0, account="main"):
        return {
            "code": 0,
            "data": {
                "replies": [
                    {
                        "member": {"mid": 3494363678116792, "uname": "yui"},
                        "content": {"message": "天梯T0卡组，美澳跳很强"},
                        "rpid": 1,
                        "folder": {"is_folded": False},
                        "invisible": False,
                        "state": 0,
                    }
                ]
            },
        }

    monkeypatch.setattr(bili, "_wbi_get", fake_wbi_get)
    assert bili._reply_visible(123, 1, "3494363678116792", "美澳跳很强") == (True, False, False)


def test_reply_visible_miss(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setattr(bili, "_wbi_get", lambda url, params, timeout=12.0, account="main": {"code": 0, "data": {"replies": []}})
    assert bili._reply_visible(123, 1, "3494363678116792", "不存在的内容") == (False, False, False)


def test_reply_visible_folded_detected(monkeypatch) -> None:
    """v0.9.25 对照 bili-cli comment check：命中但 folder.is_folded=True → 识别为折叠。"""
    from plugin.plugins.bili_viewer import bili

    def fake_wbi_get(url, params, timeout=12.0, account="main"):
        return {
            "code": 0,
            "data": {
                "replies": [
                    {
                        "member": {"mid": 3494363678116792, "uname": "yui"},
                        "content": {"message": "新号评论易折叠"},
                        "rpid": 2,
                        "folder": {"is_folded": True},
                        "invisible": False,
                        "state": 0,
                    }
                ]
            },
        }

    monkeypatch.setattr(bili, "_wbi_get", fake_wbi_get)
    assert bili._reply_visible(123, 1, "3494363678116792", "新号评论易折叠") == (True, True, False)


def test_add_reply_unverified_when_not_visible(monkeypatch) -> None:
    """接口 code=0 但回读看不到自己的评论 → 必须抛 REPLAY_UNVERIFIED，禁止假成功。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili, "_http_post", lambda url, form, timeout=12.0, account="main", query=None: {"code": 0})
    monkeypatch.setattr(bili, "my_info", lambda account: {"mid": 3494363678116792})
    monkeypatch.setattr(bili, "_reply_visible", lambda oid, type_id, uid, text, tries=2, account="main": (False, False, False))
    try:
        bili.add_reply(999999999999, 1, "测试评论")
        raise AssertionError("应当抛 REPLAY_UNVERIFIED")
    except bili.BiliError as exc:
        assert exc.code == "REPLY_UNVERIFIED"


def test_add_reply_folded_reported_not_failure(monkeypatch) -> None:
    """v0.9.25：评论已发出但回读检测到被折叠 → 返回 ok=True + folded=True 警告，不抛错不假成功。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili, "_http_post", lambda url, form, timeout=12.0, account="main", query=None: {"code": 0})
    monkeypatch.setattr(bili, "my_info", lambda account: {"mid": 3494363678116792})
    monkeypatch.setattr(bili, "_reply_visible", lambda oid, type_id, uid, text, tries=2, account="main": (True, True, False))
    r = bili.add_reply(123, 1, "折叠测试评论")
    assert r["ok"] is True
    assert r["folded"] is True
    assert "折叠" in r["note"]


def test_add_reply_verify_false_skips_check(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili, "_http_post", lambda url, form, timeout=12.0, account="main", query=None: {"code": 0})
    called: list[bool] = []
    monkeypatch.setattr(bili, "_reply_visible", lambda *a, **k: called.append(True) or True)
    r = bili.add_reply(123, 1, "测试评论", verify=False)
    assert r["ok"] is True
    assert not called  # verify=False 不做回读


def test_send_video_danmaku_unverified_without_dmid(monkeypatch) -> None:
    """弹幕接口 code=0 但不返回 dmid → 抛 DANMAKU_UNVERIFIED，禁止声称已发出。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili, "video_info", lambda bvid, account="main": {"aid": 123, "pages": [{"cid": 456}]})
    monkeypatch.setattr(bili, "_get_mixin_key", lambda: "m" * 32)
    monkeypatch.setattr(bili, "_wbi_sign", lambda params, mixin: params)
    monkeypatch.setattr(bili, "_http_post", lambda url, form, timeout=12.0, account="main", query=None: {"code": 0, "data": {}})
    try:
        bili.send_video_danmaku("BV1xx411c7mD", "测试弹幕")
        raise AssertionError("应当抛 DANMAKU_UNVERIFIED")
    except bili.BiliError as exc:
        assert exc.code == "DANMAKU_UNVERIFIED"


def test_send_video_danmaku_ok_with_dmid(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili, "video_info", lambda bvid, account="main": {"aid": 123, "pages": [{"cid": 456}]})
    monkeypatch.setattr(bili, "_get_mixin_key", lambda: "m" * 32)
    monkeypatch.setattr(bili, "_wbi_sign", lambda params, mixin: params)
    monkeypatch.setattr(bili, "_http_post", lambda url, form, timeout=12.0, account="main", query=None: {"code": 0, "data": {"dmid": 8888}})
    r = bili.send_video_danmaku("BV1xx411c7mD", "测试弹幕")
    assert r["ok"] is True
    assert r["dmid"] == "8888"


def test_live_send_danmaku_unverified_without_dmid(monkeypatch) -> None:
    """直播弹幕接口 code=0 但不返回 dmid → 视为已发出（unverified=True），不抛错（v0.9.16）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili, "_http_post", lambda url, form, timeout=12.0, account="main": {"code": 0, "data": {}})
    result = bili.live_send_danmaku("1025219", "测试弹幕")
    assert result["ok"] is True
    assert result["unverified"] is True


def test_delete_dynamic_verified_ok(monkeypatch) -> None:
    """删除后回读确认动态已不存在 → ok=True（v0.9.8 防假成功）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    called: list[bool] = []
    monkeypatch.setattr(bili, "_http_post_json", lambda *a, **k: called.append(True) or {"code": 0})
    monkeypatch.setattr(bili, "_dynamic_deleted_verified", lambda account, dynamic_id, tries=3: True)
    r = bili.delete_dynamic("123456", account="yui")
    assert r["ok"] is True
    assert called  # 删除接口真实调用


def test_delete_dynamic_unverified_still_exists(monkeypatch) -> None:
    """删除后回读仍能查到动态 → 抛 DYN_DEL_UNVERIFIED，严禁声称已删除（v0.9.8）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili, "_http_post_json", lambda *a, **k: {"code": 0})
    monkeypatch.setattr(bili, "_dynamic_deleted_verified", lambda account, dynamic_id, tries=3: False)
    try:
        bili.delete_dynamic("123456", account="yui")
        raise AssertionError("应当抛 DYN_DEL_UNVERIFIED")
    except bili.BiliError as exc:
        assert exc.code == "DYN_DEL_UNVERIFIED"


def test_post_dynamic_invisible_in_feed(monkeypatch) -> None:
    """详情能查到但动态未进对外可见动态流 → 抛 DYN_INVISIBLE（v0.9.9 修复'新动态没出来'）。"""
    import json as _json

    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")

    def fake_post_json(url, body, csrf, account="yui", timeout=12.0, query=None):
        return {"code": 0, "data": {"dynamic_id": 111, "dynamic_id_str": "111"}}

    def fake_http_get(url, params=None, account="yui", timeout=12.0):
        if "get_dynamic_detail" in str(url):
            return {"code": 0, "data": {"card": _json.dumps({"item": {"content": "新动态"}})}}
        if "nav" in str(url):
            return {"code": 0, "data": {"mid": 3494363678116792}}
        if "feed/space" in str(url):
            return {"code": 0, "data": {"items": [{"id_str": "999", "visible": True}]}}  # 新动态不在流里
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post_json", fake_post_json)
    monkeypatch.setattr(bili, "_http_get", fake_http_get)
    try:
        bili.post_dynamic("新动态", account="yui")
        raise AssertionError("应当抛 DYN_INVISIBLE")
    except bili.BiliError as exc:
        assert exc.code == "DYN_INVISIBLE"


def test_post_dynamic_visible_in_feed_ok(monkeypatch) -> None:
    """详情验证通过且动态在对外可见流里（visible=True）→ ok（v0.9.9）。"""
    import json as _json

    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")

    def fake_post_json(url, body, csrf, account="yui", timeout=12.0, query=None):
        return {"code": 0, "data": {"dynamic_id": 222, "dynamic_id_str": "222"}}

    def fake_http_get(url, params=None, account="yui", timeout=12.0):
        if "get_dynamic_detail" in str(url):
            return {"code": 0, "data": {"card": _json.dumps({"item": {"content": "成功动态"}})}}
        if "nav" in str(url):
            return {"code": 0, "data": {"mid": 3494363678116792}}
        if "feed/space" in str(url):
            return {"code": 0, "data": {"items": [{"id_str": "222", "visible": True}]}}
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post_json", fake_post_json)
    monkeypatch.setattr(bili, "_http_get", fake_http_get)
    r = bili.post_dynamic("成功动态", account="yui")
    assert r["ok"] is True
    assert r["dynamic_id"] == "222"


def test_main_uid_detects_cookie_misconfig(monkeypatch) -> None:
    """main_cookie.txt 被误写成小号 Cookie（uid 相同）→ 抛 MAIN_COOKIE_WRONG（v0.9.10）。

    实测发现 main_cookie.txt 与 yui_cookie.txt 的 DedeUserID 相同（都是小号），
    导致 owner 解析成 YUI 自己 → 私信回复 SELF_DM、白名单误判。
    """
    from plugin.plugins.bili_viewer import bili

    # 两个 cookie 都是同一个 uid（小号）
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj; DedeUserID=3494363678116792")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792")
    try:
        bili._main_uid()
        raise AssertionError("应当抛 MAIN_COOKIE_WRONG")
    except bili.BiliError as exc:
        assert exc.code == "MAIN_COOKIE_WRONG"


def test_may_message_follower(monkeypatch) -> None:
    """粉丝（对方关注了我，attribute=2）允许私信；互关 attribute=6 仍返回 mutual_follow（v0.9.10）。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792")
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj; DedeUserID=1426057300")
    # 主账号解析正常（uid 不同），owner 判定走主账号
    monkeypatch.setattr(bili, "_cookie", lambda account: {"yui": "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792", "main": "SESSDATA=m; bili_jct=mj; DedeUserID=1426057300"}[account])

    def fake_http_get(url, params=None, account="yui", timeout=12.0):
        if "x/relation" in str(url) and "fid" in str(params or {}):
            attr = {"2222": 2, "3333": 6}.get(str(params["fid"]), 0)
            return {"code": 0, "data": {"attribute": attr}}
        if "fetch_session_msgs" in str(url):
            return {"code": 0, "data": {"messages": []}}
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_get", fake_http_get)
    assert bili_social._may_message("yui", 2222) == "follower"
    assert bili_social._may_message("yui", 3333) == "mutual_follow"
    assert bili_social._may_message("yui", 1426057300) == "owner"
    assert bili_social._may_message("yui", 9999) is None


def test_may_message_survives_main_cookie_wrong(monkeypatch) -> None:
    """main cookie 配错（与 yui 相同）时，粉丝判定仍工作，不抛错（v0.9.10）。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    # main cookie 被误写成小号（uid 相同）→ _main_uid 抛 MAIN_COOKIE_WRONG
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792")
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=x; bili_jct=xj; DedeUserID=3494363678116792")

    def fake_http_get(url, params=None, account="yui", timeout=12.0):
        if "x/relation" in str(url) and "fid" in str(params or {}):
            return {"code": 0, "data": {"attribute": 2}}
        if "fetch_session_msgs" in str(url):
            return {"code": 0, "data": {"messages": []}}
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_get", fake_http_get)
    assert bili_social._may_message("yui", 2222) == "follower"


def test_fans_list(monkeypatch) -> None:
    """粉丝列表：返回 uid/昵称/互关标记（v0.9.11）。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792")

    def fake_http_get(url, params=None, account="yui", timeout=12.0):
        if "x/relation/fans" in str(url):
            return {
                "code": 0,
                "data": {
                    "total": 2,
                    "list": [
                        {"mid": 1426057300, "uname": "直到她按下快门", "attribute": 6},
                        {"mid": 555, "uname": "新粉丝", "attribute": 1},
                    ],
                },
            }
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_get", fake_http_get)
    data = bili_social.fans_list("yui", 5)
    items = data["items"]
    assert items[0] == {"uid": 1426057300, "name": "直到她按下快门", "mutual": True, "relation": "mutual_follow"}
    assert items[1]["uid"] == 555
    assert data["total"] == 2


def test_danmaku_loop_tick_send_and_finish(monkeypatch) -> None:
    """弹幕连发任务：发送→回读确认→sent 递增→发满自动停止（v0.9.18 真实验证）。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    inst = object.__new__(BiliViewerPlugin)

    calls = []

    def fake_send(room, text, account="yui", verify=True):
        calls.append((room, text, account))
        return {"ok": True, "dmid": "", "unverified": True, "confirmed": False}

    monkeypatch.setattr(bili, "live_send_danmaku", fake_send)
    monkeypatch.setattr(bili, "_live_recent_danmaku", lambda room_id, account="yui": [{"uid": 3494363678116792, "text": "666"}])
    monkeypatch.setattr(bili, "_yui_uid", lambda account: 3494363678116792)
    st = {
        "room": "1", "text": "666", "total": 2, "sent": 0, "unconfirmed": 0,
        "interval": 5.0, "active": True, "fail_streak": 0, "last_error": "",
        "next_due": 0, "account": "yui", "pending": None,
    }
    monkeypatch.setattr(inst, "_loop_load", lambda: st)
    monkeypatch.setattr(inst, "_loop_save", lambda s: st.update(s))
    inst._danmaku_loop_tick()  # 发送#1 → pending
    assert st["pending"] is not None and st["sent"] == 0
    st["pending"]["verify_due"] = 0  # 模拟 3 秒后
    st["next_due"] = 0
    inst._danmaku_loop_tick()  # 回读#1 确认 → sent=1 → 发送#2 → pending
    assert st["sent"] == 1 and st["pending"] is not None
    st["pending"]["verify_due"] = 0
    st["next_due"] = 0
    inst._danmaku_loop_tick()  # 回读#2 确认 → sent=2 → 完成停止
    assert st["sent"] == 2
    assert st["active"] is False and st["stopped_reason"] == "已完成全部次数"
    assert len(calls) == 2


def test_danmaku_loop_tick_unconfirmed_stops(monkeypatch) -> None:
    """弹幕连发任务：接口接受但回读未在直播间出现 → 立即停止，绝不假成功（v0.9.18）。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    inst = object.__new__(BiliViewerPlugin)

    def fake_send(room, text, account="yui", verify=True):
        return {"ok": True, "dmid": "", "unverified": True, "confirmed": False}

    monkeypatch.setattr(bili, "live_send_danmaku", fake_send)
    monkeypatch.setattr(bili, "_live_recent_danmaku", lambda room_id, account="yui": [])  # 回读无此弹幕
    monkeypatch.setattr(bili, "_yui_uid", lambda account: 3494363678116792)
    st = {
        "room": "1", "text": "666", "total": 10, "sent": 0, "unconfirmed": 0,
        "interval": 5.0, "active": True, "fail_streak": 0, "last_error": "",
        "next_due": 0, "account": "yui", "pending": None,
    }
    monkeypatch.setattr(inst, "_loop_load", lambda: st)
    monkeypatch.setattr(inst, "_loop_save", lambda s: st.update(s))
    inst._danmaku_loop_tick()  # 发送 → pending
    st["pending"]["verify_due"] = 0  # 模拟 3 秒后
    st["next_due"] = 0
    inst._danmaku_loop_tick()  # 回读未命中 → 停止
    assert st["active"] is False
    assert "未在直播间出现" in (st.get("stopped_reason") or "")
    assert st["sent"] == 0


def test_danmaku_loop_interval_min_is_5(monkeypatch) -> None:
    """连发任务间隔被强制钳制到最小 5 秒（B站 风控，v0.9.17）。"""
    import asyncio

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    inst = object.__new__(BiliViewerPlugin)
    saved: dict = {}

    def fake_load():
        return saved

    def fake_save(s):
        saved.clear()
        saved.update(s)

    monkeypatch.setattr(inst, "_loop_load", fake_load)
    monkeypatch.setattr(inst, "_loop_save", fake_save)
    monkeypatch.setattr(inst, "_danmaku_loop_tick", lambda: None)
    r = asyncio.run(inst.bili_live_danmaku_loop(room="1025219", text="喵", times=3, interval=1))
    assert r["is_error"] is False
    assert saved["interval"] == 5.0  # 请求 1 秒被钳到 5 秒


def test_danmaku_loop_tick_stop_on_fail(monkeypatch) -> None:
    """弹幕连发任务：连续 3 次真失败自动停止（v0.9.15）。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    inst = object.__new__(BiliViewerPlugin)

    def fake_send(room, text, account="yui", verify=True):
        raise bili.BiliError("接口失败", "SEND_FAILED")

    monkeypatch.setattr(bili, "live_send_danmaku", fake_send)
    st = {
        "room": "1", "text": "666", "total": 10, "sent": 0, "unconfirmed": 0,
        "interval": 5.0, "active": True, "fail_streak": 0, "last_error": "",
        "next_due": 0, "account": "yui",
    }
    monkeypatch.setattr(inst, "_loop_load", lambda: st)
    monkeypatch.setattr(inst, "_loop_save", lambda s: st.update(s))
    for _ in range(3):
        st["next_due"] = 0
        inst._danmaku_loop_tick()
    assert st["active"] is False
    assert st["stopped_reason"] == "连续 3 次发送失败"
    assert st["sent"] == 0


def test_post_dynamic_short_window_dedup(monkeypatch) -> None:
    """同账号同内容 60s 内重复发布 → DYN_DUP 拒绝（v0.9.22 防'发一次变3条'）。"""
    import json as _json

    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792")
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    bili._dyn_last_posts.clear()
    sent_text: dict[str, str] = {"text": "去重测试"}

    def fake_post_json(url, body, csrf, account="yui", timeout=12.0, query=None):
        return {"code": 0, "data": {"dynamic_id": 333, "dynamic_id_str": "333"}}

    def fake_http_get(url, params=None, account="yui", timeout=12.0):
        if "get_dynamic_detail" in str(url):
            return {"code": 0, "data": {"card": _json.dumps({"item": {"content": sent_text["text"]}})}}
        if "nav" in str(url):
            return {"code": 0, "data": {"mid": 3494363678116792}}
        if "feed/space" in str(url):
            return {"code": 0, "data": {"items": [{"id_str": "333", "visible": True}]}}
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post_json", fake_post_json)
    monkeypatch.setattr(bili, "_http_get", fake_http_get)
    r = bili.post_dynamic("去重测试", account="yui")
    assert r["ok"] is True
    # 接口接受后立即记录：60s 内同内容重复 → DYN_DUP
    try:
        bili.post_dynamic("去重测试", account="yui")
        raise AssertionError("应当抛 DYN_DUP")
    except bili.BiliError as exc:
        assert exc.code == "DYN_DUP"
    # 不同内容不受影响
    bili._dyn_last_posts.clear()
    sent_text["text"] = "另一条内容"
    assert bili.post_dynamic("另一条内容", account="yui")["ok"] is True


def test_dm_send_short_window_dedup(monkeypatch) -> None:
    """同对象同内容 60s 内重复发送 → DM_DUP 拒绝（v0.9.22 防私信重复）。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792")
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj; DedeUserID=1426057300")
    bili_social._dm_last_sends.clear()

    def fake_cookie(account="yui"):
        return {"yui": "SESSDATA=y; bili_jct=yj; DedeUserID=3494363678116792", "main": "SESSDATA=m; bili_jct=mj; DedeUserID=1426057300"}[account]

    monkeypatch.setattr(bili, "_cookie", fake_cookie)
    monkeypatch.setattr(bili, "_require_login", lambda account: None)
    monkeypatch.setattr(bili, "_csrf", lambda account: "tok")
    monkeypatch.setattr(bili_social, "_resolve_dm_uid", lambda uid, account: int(uid))
    monkeypatch.setattr(bili_social, "_may_message", lambda account, talker: "follower")
    monkeypatch.setattr(bili_social, "check_limit", lambda action, units=1, account="yui": None)
    monkeypatch.setattr(bili_social, "record_limit", lambda *a, **k: None)
    monkeypatch.setattr(bili_social, "_device_id", lambda: "devtest")

    def fake_post(url, payload, account="yui", query=None, timeout=12.0):
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post", fake_post)
    monkeypatch.setattr(bili_social, "_dm_verified", lambda account, talker, text: True)
    r = bili_social.dm_send("yui", uid="2222", text="你好喵", allow_new=True)
    assert r["sent"] is True
    # 接口接受后立即记录：60s 内同内容 → DM_DUP
    try:
        bili_social.dm_send("yui", uid="2222", text="你好喵", allow_new=True)
        raise AssertionError("应当抛 DM_DUP")
    except bili.BiliError as exc:
        assert exc.code == "DM_DUP"
    # 同一对象不同内容可发
    bili_social._dm_last_sends.clear()
    assert bili_social.dm_send("yui", uid="2222", text="换一句话", allow_new=True)["sent"] is True
    # 不同对象同内容可发
    bili_social._dm_last_sends.clear()
    assert bili_social.dm_send("yui", uid="3333", text="你好喵", allow_new=True)["sent"] is True
