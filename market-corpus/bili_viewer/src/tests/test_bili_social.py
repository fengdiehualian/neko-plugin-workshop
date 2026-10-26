"""bili_social（v0.8.0 bili-cli 精华移植）离线单元测试：护栏 / 解析 / 工具注册。

与 test_cdp_cookie.py 一致：模块在函数内导入，避免 pytest 收集期环境差异。
"""

from __future__ import annotations

import json
import os

import pytest


def _social():
    from plugin.plugins.bili_viewer import bili_social

    return bili_social


def _bili():
    from plugin.plugins.bili_viewer import bili

    return bili


@pytest.fixture(autouse=True)
def social_dir(tmp_path):
    """把限流/审计/设备ID 状态目录隔离到临时目录，避免污染真实状态与测试互踩。"""
    env = os.environ.get("BILI_SOCIAL_DIR", "")
    os.environ["BILI_SOCIAL_DIR"] = str(tmp_path)
    yield tmp_path
    if env:
        os.environ["BILI_SOCIAL_DIR"] = env
    else:
        os.environ.pop("BILI_SOCIAL_DIR", None)


# ══════════ 护栏：限流 ══════════


def test_check_limit_allows_and_blocks_interval(social_dir):
    bili_social = _social()
    bili = _bili()
    bili_social.record_limit("dm", {"uid": 1}, "ok", account="yui")
    with pytest.raises(bili.BiliError) as ei:
        bili_social.check_limit("dm", account="yui")
    assert ei.value.code == "RATE_LIMITED"


def test_check_limit_daily_coin_cap(social_dir):
    bili_social = _social()
    bili = _bili()
    # coin 每日 ≤5：连续 5 次成功后再检查应被拒
    for _ in range(5):
        bili_social.record_limit("coin", {"coins": 1}, "ok", units=1, account="yui")
    with pytest.raises(bili.BiliError) as ei:
        bili_social.check_limit("coin", units=1, account="yui")
    assert ei.value.code == "RATE_LIMITED"
    # 不同账号独立计数
    bili_social.check_limit("coin", units=1, account="main")


def test_record_limit_writes_audit(social_dir):
    bili_social = _social()
    bili_social.record_limit("dm", {"uid": 42}, {"ok": True}, account="yui")
    with open(os.path.join(str(social_dir), "audit.log"), encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    assert lines[0]["action"] == "dm"
    assert lines[0]["params"] == {"uid": 42}


def test_device_id_stable(social_dir):
    bili_social = _social()
    a = bili_social._device_id()
    b = bili_social._device_id()
    assert a == b and len(a) >= 16


# ══════════ 护栏：错误码表 ══════════


def test_classify_bili_code():
    bili_social = _social()
    assert bili_social.classify_bili_code(-101) == ("AUTH_FAILED", "登录态失效，请重新配置 Cookie")
    assert bili_social.classify_bili_code(-412)[0] == "RISK_CONTROL"
    assert bili_social.classify_bili_code(12051)[0] == "RISK_CONTROL"
    assert bili_social.classify_bili_code(21007)[0] == "BIZ_ERROR"
    assert bili_social.classify_bili_code(36703)[0] == "RATE_LIMITED"
    assert bili_social.classify_bili_code(999999)[0].startswith("BIZ_")


def test_guard_exc_remap():
    bili_social = _social()
    bili = _bili()
    exc = bili.BiliError("接口错误", "BIZ_-412")
    remapped = bili_social._guard_exc(exc)
    assert remapped.code == "RISK_CONTROL"
    # 非 BIZ_ 原样保留
    orig = bili.BiliError("未登录", "AUTH_FAILED")
    assert bili_social._guard_exc(orig) is orig


# ══════════ 解析辅助 ══════════


def test_notice_item_reply_with():
    bili_social = _social()
    raw = {
        "id": 100,
        "reply_time": 1700000000,
        "user": {"mid": 88, "nickname": "路人"},
        "item": {
            "business_id": 1,
            "source_id": 12345,
            "root_id": 100,
            "source_content": "写得不错",
            "target_reply_content": "谢谢支持",
            "title": "我的视频标题",
            "uri": "https://www.bilibili.com/video/BV1xx411c7mD",
            "subject_id": 999,
        },
    }
    n = bili_social._notice_item(raw, "reply_time", True)
    assert n["user"]["name"] == "路人"
    assert n["on"]["bvid"] == "BV1xx411c7mD"
    assert n["rpid"] == 12345
    assert "bili_reply_comment" in n["reply_with"]
    assert "12345" in n["reply_with"]


def test_dm_content_types():
    bili_social = _social()
    text = bili_social._dm_content({"msg_type": 1, "content": json.dumps({"content": "你好"})})
    assert text == "你好"
    img = bili_social._dm_content({"msg_type": 2, "content": json.dumps({"url": "http://x/y.png"})})
    assert "[图片]" in img
    share = bili_social._dm_content({"msg_type": 7, "content": json.dumps({"title": "猫", "bvid": "BV1xx"})})
    assert "[分享视频]" in share and "BV1xx" in share
    # 非法 JSON 直接原样返回
    assert bili_social._dm_content({"msg_type": 1, "content": "不是json"}) == "不是json"


def test_notice_item_business_type_string():
    """business_id 为字符串时也要正确映射类型（防漏映射）。"""
    bili_social = _social()
    raw = {
        "id": 1,
        "reply_time": 1700000000,
        "user": {"mid": 9, "nickname": "A"},
        "item": {
            "business_id": "1",
            "source_id": 55,
            "source_content": "x",
            "uri": "https://www.bilibili.com/video/BV1xx411c7mD",
            "subject_id": 7,
        },
    }
    n = bili_social._notice_item(raw, "reply_time", True)
    assert n["on"]["type"] == "video"
    assert n["on"]["bvid"] == "BV1xx411c7mD"
    assert n["reply_with"].startswith("bili_reply_comment target=BV1xx411c7mD parent=55")


def test_resolve_dm_uid_digit():
    bili_social = _social()
    assert bili_social._resolve_dm_uid("123456789", "yui") == 123456789


# ══════════ 私信已读游标（v0.8.3：B站 unread 计数不可靠的修复） ══════════


def _mk_session(talker, sender_uid, seqno, ts, me=100):
    return {
        "talker_id": talker,
        "unread_count": 0,  # 模拟 B站 网页打开后计数归 0（新消息仍真实存在）
        "last_msg": {"sender_uid": sender_uid, "msg_seqno": seqno, "timestamp": ts},
    }


def _read_seen(social_dir):
    p = os.path.join(social_dir, "dm_seen.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def test_is_new_msg_no_cursor_window(social_dir):
    """无本地游标时：只认 6 小时内对方消息；超过 6 小时不算新；自己发的永远不算。"""
    import time

    bili_social = _social()
    now = time.time()
    assert bili_social._is_new_msg(_mk_session(11, 999, 50, int(now - 60)), "yui", 100) is True
    assert bili_social._is_new_msg(_mk_session(22, 999, 60, int(now - 8 * 3600)), "yui", 100) is False
    assert bili_social._is_new_msg(_mk_session(33, 100, 70, int(now - 10)), "yui", 100) is False


def test_is_new_msg_with_cursor(social_dir):
    """有游标后：seqno 大于游标才算新；小于等于不算。"""
    bili_social = _social()
    bili_social.record_dm_seen("yui", 11, 100)
    assert bili_social._is_new_msg(_mk_session(11, 999, 101, 1700000000), "yui", 100) is True
    assert bili_social._is_new_msg(_mk_session(11, 999, 100, 1700000000), "yui", 100) is False
    assert bili_social._is_new_msg(_mk_session(11, 999, 99, 1700000000), "yui", 100) is False


def test_record_dm_seen_keeps_max_and_isolated(social_dir):
    bili_social = _social()
    bili_social.record_dm_seen("yui", 11, 50)
    bili_social.record_dm_seen("yui", 11, 40)  # 旧值不回退
    bili_social.record_dm_seen("main", 11, 5)  # 分账号隔离
    seen = _read_seen(social_dir)
    assert seen["yui"]["11"] == 50
    assert seen["main"]["11"] == 5


def test_new_dm_count_and_unread_only_filter(social_dir, monkeypatch):
    """new_dm_count / dm_conversations(unread_only=True) 用游标判定，不再信 unread_count=0。"""
    import time

    bili_social = _social()
    bili = _bili()
    now = time.time()
    sessions = [
        _mk_session(11, 999, 100, int(now - 60)),        # 对方新发（无游标 → 6h 窗口）
        _mk_session(22, 100, 5, int(now - 10)),          # 自己发的 → 不算
        _mk_session(33, 999, 8, int(now - 9 * 3600)),    # 对方但超 6h → 不算
    ]
    monkeypatch.setattr(bili_social, "_fetch_sessions", lambda account, limit=50: sessions)
    monkeypatch.setattr(bili, "cookie_uid", lambda cookie: 100)
    assert bili_social.new_dm_count("yui") == 1
    convs = bili_social.dm_conversations("yui", unread_only=True, limit=20)
    assert [c["uid"] for c in convs] == [11]
    assert convs[0]["new"] is True
    assert convs[0]["unread"] == 0  # 即使 B站 计数为 0，仍判定为新（修复核心）


def test_inbox_private_messages_uses_cursor(social_dir, monkeypatch):
    """inbox 的私信未读数与列表同源（游标判定），修复 msgfeed.chat=0 漏报。"""
    import time

    bili_social = _social()
    bili = _bili()
    now = time.time()
    sessions = [_mk_session(11, 999, 100, int(now - 30))]
    monkeypatch.setattr(bili_social, "_fetch_sessions", lambda account, limit=50: sessions)
    monkeypatch.setattr(bili, "cookie_uid", lambda cookie: 100)
    monkeypatch.setattr(
        bili_social, "unread_counts",
        lambda account="yui": {"replies": 0, "mentions": 0, "likes": 0, "private_messages": 0, "system": 0},
    )
    out = bili_social.inbox("yui", 5)
    assert out["unread"]["private_messages"] == 1
    assert len(out["private_messages"]) == 1
    assert out["private_messages"][0]["uid"] == 11


def test_limit_table_complete():
    bili_social = _social()
    # 新模块引用的 action 都必须出现在 LIMITS 表中
    for action in ("comment", "danmaku", "video_like", "favorite", "coin", "dm"):
        assert action in bili_social.LIMITS


def test_wbi_sign_shape():
    """wbi 签名结果必须带 wts + w_rid（fetch_session_msgs/send_msg 依赖）。"""
    bili = _bili()
    mixin = "0" * 32
    signed = bili._wbi_sign({"talker_id": 123, "session_type": 1}, mixin)
    assert "wts" in signed and "w_rid" in signed
    assert signed["w_rid"] == bili._wbi_sign({"talker_id": 123, "session_type": 1}, mixin)["w_rid"]
    # 参数顺序不影响签名结果（内部排序）
    signed2 = bili._wbi_sign({"session_type": 1, "talker_id": 123}, mixin)
    assert signed["w_rid"] == signed2["w_rid"]


def test_wbi_only_endpoints_use_signing():
    """wbi:true 端点（fetch_session_msgs/send_msg）必须走 _wbi_params 签名，不能裸调。"""
    bili_social = _social()
    with open(bili_social.__file__, encoding="utf-8") as f:
        src = f.read()
    assert "_FETCH_MSGS_URL,\n        _wbi_params(" in src or "_FETCH_MSGS_URL, _wbi_params(" in src
    assert "_SEND_MSG_URL," in src and "_wbi_params({" in src.split("_SEND_MSG_URL")[-1][:400]
    # 创作中心接口 verify-only：不得 wbi 签名
    assert "_OVERVIEW_URL" in src and "_wbi_get_account(_OVERVIEW_URL" not in src


# ══════════ 工具注册（__init__.py） ══════════


def test_tools_registered():
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    cls = BiliViewerPlugin
    tools = set(m for m in dir(cls) if m.startswith("bili_"))
    expected = {
        "bili_inbox", "bili_unread", "bili_feed_replies", "bili_feed_mentions",
        "bili_feed_likes", "bili_feed_system", "bili_dm_list", "bili_dm_read",
        "bili_dm_send", "bili_video_relation", "bili_video_like", "bili_video_coin",
        "bili_video_fav", "bili_video_triple", "bili_my_favs", "bili_rank", "bili_my_stats",
    }
    assert expected <= tools
