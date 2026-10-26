"""bili_viewer v0.7.0：YUI 独立小号（凭证隔离/默认账号回退/节流/评论目标/投稿安全/动态上限/自主唤醒冷却）。

与 test_bili.py 一致：插件模块在函数内导入，conftest.py 注入 sys.path；
全部用 monkeypatch 模拟网络与宿主上下文，不依赖真实网络。
"""

from __future__ import annotations

import asyncio
import json
import os


def _make_inst(monkeypatch) -> "object":
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    return BiliViewerPlugin(ctx=None)


def _fake_post(url, form, timeout=12.0, account="main"):
    return {"code": 0, "data": {}}


# ══════════ 凭证隔离：main / yui 两套 Cookie ══════════


def test_cookie_account_segregation(monkeypatch, tmp_path) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=main1; bili_jct=mainj")
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    assert bili._cookie("main") == "SESSDATA=main1; bili_jct=mainj"
    assert bili._cookie("yui") == ""  # 未配置小号

    # 小号文件注入（可注入目录）
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    (tmp_path / "yui_cookie.txt").write_text("SESSDATA=yui9; bili_jct=yuij", encoding="utf-8")
    assert bili._cookie("yui") == "SESSDATA=yui9; bili_jct=yuij"

    # 环境变量优先于文件
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=yui8; bili_jct=yui8j")
    assert bili._cookie("yui") == "SESSDATA=yui8; bili_jct=yui8j"


# ══════════ 双账号归位（v0.7.10）：第一个登录=大号，第二个登录=小号，可后期互换 ══════════


def test_main_cookie_file_fallback(monkeypatch, tmp_path) -> None:
    """v0.7.10：主账号 Cookie 也支持插件目录 main_cookie.txt（绑定持久化）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    (tmp_path / "main_cookie.txt").write_text("SESSDATA=mainf; bili_jct=mainfj", encoding="utf-8")
    assert bili._cookie("main") == "SESSDATA=mainf; bili_jct=mainfj"
    # env 优先于文件
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=maine; bili_jct=mainej")
    assert bili._cookie("main") == "SESSDATA=maine; bili_jct=mainej"


def test_cookie_uid_parse(monkeypatch) -> None:
    """v0.7.10：从 Cookie 解析 uid（DedeUserID，无需网络）。"""
    from plugin.plugins.bili_viewer import bili

    assert bili.cookie_uid("SESSDATA=x; DedeUserID=123456; bili_jct=y") == 123456
    assert bili.cookie_uid("SESSDATA=x; DedeUserID=88; bili_jct=y") == 88
    assert bili.cookie_uid("SESSDATA=x; bili_jct=y") is None


def _pkg() -> "object":
    """包模块即 __init__.py（BiliViewerPlugin 的全局命名空间）。"""
    import plugin.plugins.bili_viewer as _pkg_mod

    return _pkg_mod


def test_assign_cookie_first_is_main_second_is_yui(monkeypatch, tmp_path) -> None:
    """v0.7.10：按登录顺序归位——第一个登录的账号=大号，第二个=小号（默认操作账号）。"""
    from plugin.plugins.bili_viewer import bili

    pkg = _pkg()

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    monkeypatch.setattr(pkg, "_accounts_path", lambda: str(tmp_path / "accounts.json"))
    inst = _make_inst(monkeypatch)

    # 第一个登录（uid=111）
    r1 = inst._assign_cookie("SESSDATA=a; DedeUserID=111; bili_jct=a1", 0, "extension")
    assert r1["bound"] is True and "大号" in r1["detail"]
    assert bili._cookie("main") == "SESSDATA=a; DedeUserID=111; bili_jct=a1"
    assert (tmp_path / "main_cookie.txt").read_text(encoding="utf-8") == "SESSDATA=a; DedeUserID=111; bili_jct=a1"
    acc = pkg._read_accounts()
    assert acc["main"]["uid"] == 111

    # 第二个登录（uid=222）→ 自动成为小号
    r2 = inst._assign_cookie("SESSDATA=b; DedeUserID=222; bili_jct=b2", 0, "extension")
    assert r2["bound"] is True and "小号" in r2["detail"]
    assert bili._cookie("yui") == "SESSDATA=b; DedeUserID=222; bili_jct=b2"
    assert (tmp_path / "yui_cookie.txt").read_text(encoding="utf-8") == "SESSDATA=b; DedeUserID=222; bili_jct=b2"
    acc = pkg._read_accounts()
    assert acc["yui"]["uid"] == 222

    # 重复推送同一 uid → 刷新不新增
    r3 = inst._assign_cookie("SESSDATA=b2; DedeUserID=222; bili_jct=b2b", 1, "extension")
    assert r3["bound"] is True and "小号" in r3["detail"]
    acc = pkg._read_accounts()
    assert set(acc) == {"main", "yui"}
    assert acc["yui"]["uid"] == 222

    # 第三个账号 → 拒绝（大小号都已绑定）
    r4 = inst._assign_cookie("SESSDATA=c; DedeUserID=333; bili_jct=c3", 0, "extension")
    assert r4["bound"] is False
    assert bili._cookie("yui") == "SESSDATA=b2; DedeUserID=222; bili_jct=b2b"  # 未被覆盖
    assert bili._cookie("main") == "SESSDATA=a; DedeUserID=111; bili_jct=a1"


def test_set_roles_swap(monkeypatch, tmp_path) -> None:
    """v0.7.10：后期说明修改——交换大小号（文件互换 + 角色映射互换 + env 同步）。"""
    from plugin.plugins.bili_viewer import bili

    pkg = _pkg()

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    monkeypatch.setattr(pkg, "_accounts_path", lambda: str(tmp_path / "accounts.json"))
    inst = _make_inst(monkeypatch)
    inst._assign_cookie("SESSDATA=a; DedeUserID=111; bili_jct=a1", 0, "extension")
    inst._assign_cookie("SESSDATA=b; DedeUserID=222; bili_jct=b2", 0, "extension")

    out = asyncio.run(inst.bili_set_roles(action="swap"))
    assert out.get("is_error") is False
    # 文件互换：原小号（uid222）现在是大号
    assert bili._cookie("main") == "SESSDATA=b; DedeUserID=222; bili_jct=b2"
    assert bili._cookie("yui") == "SESSDATA=a; DedeUserID=111; bili_jct=a1"
    acc = pkg._read_accounts()
    assert acc["main"]["uid"] == 222
    assert acc["yui"]["uid"] == 111
    # 运行期 env 同步
    assert os.environ.get("BILI_COOKIE") == "SESSDATA=b; DedeUserID=222; bili_jct=b2"
    assert os.environ.get("BILI_YUI_COOKIE") == "SESSDATA=a; DedeUserID=111; bili_jct=a1"


def test_set_roles_by_uid(monkeypatch, tmp_path) -> None:
    """v0.7.10：按 uid 把账号设为指定角色。"""
    from plugin.plugins.bili_viewer import bili

    pkg = _pkg()

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    monkeypatch.setattr(pkg, "_accounts_path", lambda: str(tmp_path / "accounts.json"))
    inst = _make_inst(monkeypatch)
    inst._assign_cookie("SESSDATA=a; DedeUserID=111; bili_jct=a1", 0, "extension")
    inst._assign_cookie("SESSDATA=b; DedeUserID=222; bili_jct=b2", 0, "extension")
    # 把 uid=222 设为小号（它当前是大号 → 等价互换）
    out = asyncio.run(inst.bili_set_roles(uid=222, role="yui"))
    assert out.get("is_error") is False
    assert bili._cookie("yui") == "SESSDATA=b; DedeUserID=222; bili_jct=b2"
    assert bili._cookie("main") == "SESSDATA=a; DedeUserID=111; bili_jct=a1"
    # 再设回大号
    out2 = asyncio.run(inst.bili_set_roles(uid=222, role="main"))
    assert out2.get("is_error") is False
    assert bili._cookie("main") == "SESSDATA=b; DedeUserID=222; bili_jct=b2"


def test_set_roles_env_locked(monkeypatch, tmp_path) -> None:
    """v0.7.10：env 覆盖文件层的账号无法互换（避免误清空）。"""
    from plugin.plugins.bili_viewer import bili

    pkg = _pkg()

    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    monkeypatch.setattr(pkg, "_accounts_path", lambda: str(tmp_path / "accounts.json"))
    inst = _make_inst(monkeypatch)
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=a; DedeUserID=111; bili_jct=a1")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=b; DedeUserID=222; bili_jct=b2")
    out = asyncio.run(inst.bili_set_roles(action="swap"))
    assert out.get("error") == "ENV_LOCKED"
    # 文件层账号可互换
    (tmp_path / "main_cookie.txt").write_text("SESSDATA=a; DedeUserID=111; bili_jct=a1", encoding="utf-8")
    (tmp_path / "yui_cookie.txt").write_text("SESSDATA=b; DedeUserID=222; bili_jct=b2", encoding="utf-8")
    out2 = asyncio.run(inst.bili_set_roles(action="swap"))
    assert out2.get("is_error") is False


def test_bili_accounts_view(monkeypatch, tmp_path) -> None:
    """v0.7.10：YUI 分清大小号——bili_accounts 返回双账号视图与默认角色。"""
    from plugin.plugins.bili_viewer import bili

    pkg = _pkg()

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    monkeypatch.setattr(pkg, "_accounts_path", lambda: str(tmp_path / "accounts.json"))
    inst = _make_inst(monkeypatch)
    inst._assign_cookie("SESSDATA=a; DedeUserID=111; bili_jct=a1", 0, "extension")
    inst._assign_cookie("SESSDATA=b; DedeUserID=222; bili_jct=b2", 0, "extension")
    monkeypatch.setattr(bili, "my_info", lambda role: {"uname": "大号名" if role == "main" else "小号名", "mid": 111 if role == "main" else 222, "level": 6})
    out = asyncio.run(inst.bili_accounts())
    assert out.get("is_error") is False
    assert "大号" in out["output"] and "小号" in out["output"]
    assert "默认使用" in out["output"] and "yui" in out["output"]
    assert out["result"]["main"]["uid"] == 111
    assert out["result"]["yui"]["uid"] == 222
    assert out["result"]["default_role"] == "yui"


def test_yui_require_login_message(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", "/nonexistent-dir")
    try:
        bili.live_send_danmaku("12345", "hi", account="yui")
        raise AssertionError("should raise")
    except bili.BiliError as exc:
        assert exc.code == "NEED_LOGIN"
        assert "YUI" in exc.message


# ══════════ 默认账号回退：有 yui 用小号，无 yui 回退主账号 ══════════


def test_default_account_fallback_to_main(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj")
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", "/nonexistent-dir")
    captured: list[str] = []

    def fake_send(room, text, account="main"):
        captured.append(account)
        return {"ok": True}

    monkeypatch.setattr(bili, "live_send_danmaku", fake_send)
    inst = _make_inst(monkeypatch)
    out = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="测试"))
    assert out.get("is_error") is False
    assert captured == ["main"]  # 未配置小号 → 回退主账号


def test_default_account_uses_yui_when_configured(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj")
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    captured: list[str] = []

    def fake_send(room, text, account="main"):
        captured.append(account)
        return {"ok": True}

    monkeypatch.setattr(bili, "live_send_danmaku", fake_send)
    inst = _make_inst(monkeypatch)
    out = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="测试"))
    assert out.get("is_error") is False
    assert captured == ["yui"]  # 配置了小号 → 默认小号


# ══════════ 节流：同目标 60s 内限 1 条弹幕（小号维度） ══════════


def test_yui_danmaku_rate_limit(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")

    def fake_send(room, text, account="main"):
        return {"ok": True}

    monkeypatch.setattr(bili, "live_send_danmaku", fake_send)
    inst = _make_inst(monkeypatch)
    r1 = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="第一条", account="yui"))
    assert r1.get("is_error") is False
    r2 = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="第二条", account="yui"))
    assert r2.get("is_error") is True
    assert r2.get("error") == "RATE_LIMITED"
    # 主账号不受小号节流影响
    r3 = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="主号", account="main"))
    assert r3.get("is_error") is False


def test_yui_comment_rate_limit(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "add_reply", lambda oid, t, text, account="yui": {"ok": True})
    monkeypatch.setattr(bili, "video_info", lambda bvid, account="main": {"aid": 123456, "title": "t"})
    inst = _make_inst(monkeypatch)
    r1 = asyncio.run(inst.bili_reply_comment(target="BV1xx411c7mD", text="写得好", account="yui"))
    assert r1.get("is_error") is False
    r2 = asyncio.run(inst.bili_reply_comment(target="BV1xx411c7mD", text="再来一条", account="yui"))
    assert r2.get("error") == "RATE_LIMITED"
    # 不同视频不受限
    monkeypatch.setattr(bili, "video_info", lambda bvid, account="main": {"aid": 999, "title": "t2"})
    r3 = asyncio.run(inst.bili_reply_comment(target="BV1xx411c7mE", text="另一个", account="yui"))
    assert r3.get("is_error") is False


def test_yui_rate_reserve_atomic_blocks_concurrent(monkeypatch) -> None:
    """v0.7.8 复查优化：reserve 原子占用——并发第二个请求在同窗口内被拒绝，
    且发送失败后 rollback 释放窗口允许重试。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "live_send_danmaku", lambda room, text, account="main": {"ok": True})
    inst = _make_inst(monkeypatch)
    # 第一次发送：成功（占用窗口）
    r1 = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="第一条", account="yui"))
    assert r1.get("is_error") is False
    # 第二次同目标：即使不加 dedup（换内容），窗口内仍被拒
    r2 = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="第二条不同内容", account="yui"))
    assert r2.get("error") == "RATE_LIMITED"


def test_yui_rate_rollback_after_failure(monkeypatch) -> None:
    """v0.7.8 复查优化：发送失败回退占用，稍后可重试（不被误限流）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    state = {"fail": True}

    def fake_send(room, text, account="main"):
        if state["fail"]:
            raise bili.BiliError("接口失败", "SEND_FAILED")
        return {"ok": True}

    monkeypatch.setattr(bili, "live_send_danmaku", fake_send)
    inst = _make_inst(monkeypatch)
    r1 = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="失败", account="yui"))
    assert r1.get("is_error") is True
    # 失败已回退，立即重试成功（不在窗口内）
    state["fail"] = False
    r2 = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="成功", account="yui"))
    assert r2.get("is_error") is False


def test_yui_upload_mutex_blocks_second(monkeypatch) -> None:
    """v0.7.8 复查优化：投稿互斥——第二个并发投稿返回 BUSY，不跑双流程。"""
    import os as _os

    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    calls: list[str] = []
    upload_dir = _os.path.join(_os.path.dirname(_os.path.abspath(bili.__file__)), "yui_uploads")
    _os.makedirs(upload_dir, exist_ok=True)
    fake_file = _os.path.join(upload_dir, "v.mp4")
    with open(fake_file, "wb") as _f:
        _f.write(b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * (80 * 1024))
    try:

        def fake_submit(file_path, title, desc="", tags="", tid=171, account="yui"):
            calls.append(title)
            return {"ok": True}

        monkeypatch.setattr(bili, "submit_video", fake_submit)
        inst = _make_inst(monkeypatch)
        # 手动占住上传互斥（模拟自主引擎正在上传）
        with inst._yui_upload_lock:
            inst._yui_uploading = True
        r = asyncio.run(inst.bili_yui_upload(file="v.mp4", title="第二个"))
        inst._yui_uploading = False
        assert r.get("error") == "BUSY"
        assert calls == []  # 第二个流程没有启动
    finally:
        try:
            _os.remove(fake_file)
        except OSError:
            pass


# ══════════ 评论目标解析 ══════════


def test_reply_comment_no_target(monkeypatch) -> None:
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    inst = _make_inst(monkeypatch)
    # 无 target 且感知不到内容
    monkeypatch.setattr(inst, "_ext_tab", lambda **kw: None)
    out = asyncio.run(inst.bili_reply_comment(text="评论", account="yui"))
    assert out.get("error") == "NO_TARGET"


def test_reply_comment_video_aid_resolution(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    seen: list[tuple] = []

    def fake_add(oid, t, text, account="yui"):
        seen.append((oid, t, text))
        return {"ok": True}

    monkeypatch.setattr(bili, "add_reply", fake_add)
    monkeypatch.setattr(bili, "video_info", lambda bvid, account="main": {"aid": 7777, "title": "t"})
    inst = _make_inst(monkeypatch)
    out = asyncio.run(inst.bili_reply_comment(target="https://www.bilibili.com/video/BV1xx411c7mD", text="顶一个", account="yui"))
    assert out.get("is_error") is False
    assert seen == [(7777, 1, "顶一个")]  # 视频评论：oid=aid, type=1


def test_reply_comment_main_account_resolution(monkeypatch) -> None:
    """评论目标解析使用实际账号（v0.9.23 修复：原硬编码 account=yui，只有主账号时评论会解析失败）。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=m; bili_jct=mj")
    got: list = []
    seen: list[tuple] = []

    def fake_vinfo(bvid, account="main"):
        got.append(account)
        return {"aid": 8888, "title": "t"}

    def fake_add(oid, t, text, account="yui"):
        seen.append((oid, t, text))
        return {"ok": True}

    monkeypatch.setattr(bili, "video_info", fake_vinfo)
    monkeypatch.setattr(bili, "add_reply", fake_add)
    inst = _make_inst(monkeypatch)
    out = asyncio.run(inst.bili_reply_comment(target="BV1xx411c7mD", text="好", account="main"))
    assert out.get("is_error") is False
    assert got and got[-1] == "main"  # 解析目标用的是实际账号 main
    assert seen == [(8888, 1, "好")]


# ══════════ 动态日上限 ══════════


def test_yui_dynamic_daily_cap(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili, "post_dynamic", lambda text, account="yui": {"ok": True})
    # v0.9.25 对照 bili-cli guard.py 新增 dynamic 60s 限流：本测试聚焦每日 6 条上限，绕开间隔限流
    monkeypatch.setattr(bili_social, "check_limit", lambda *a, **k: None)
    monkeypatch.setattr(bili_social, "record_limit", lambda *a, **k: None)
    inst = _make_inst(monkeypatch)
    ok_count = 0
    for i in range(7):
        out = asyncio.run(inst.bili_yui_dynamic(text=f"动态{i}"))
        if out.get("is_error") is False:
            ok_count += 1
        else:
            assert out.get("error") == "RATE_LIMITED"
    assert ok_count == 6  # 每日上限 6 条


def test_yui_dynamic_failure_releases_daily_quota(monkeypatch) -> None:
    """v0.7.8 复查优化：动态发布失败回退天级占用，稍后可重试。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setattr(bili_social, "check_limit", lambda *a, **k: None)
    monkeypatch.setattr(bili_social, "record_limit", lambda *a, **k: None)
    state = {"fail": True}

    def fake_dyn(text, account="yui"):
        if state["fail"]:
            raise bili.BiliError("接口失败", "DYN_FAILED")
        return {"ok": True}

    monkeypatch.setattr(bili, "post_dynamic", fake_dyn)
    inst = _make_inst(monkeypatch)
    r1 = asyncio.run(inst.bili_yui_dynamic(text="失败的动态"))
    assert r1.get("is_error") is True
    state["fail"] = False
    r2 = asyncio.run(inst.bili_yui_dynamic(text="重试的动态"))
    assert r2.get("is_error") is False


# ══════════ 投稿安全：仅接受 yui_uploads 目录内文件 ══════════


def test_yui_upload_path_safety(monkeypatch) -> None:
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    inst = _make_inst(monkeypatch)
    # 路径穿越拒绝
    out = asyncio.run(inst.bili_yui_upload(file="../evil.mp4", title="t"))
    assert out.get("error") == "BAD_PATH"
    out2 = asyncio.run(inst.bili_yui_upload(file="C:\\windows\\evil.mp4", title="t"))
    assert out2.get("error") == "BAD_PATH"
    # 目录内不存在 → NO_FILE
    out3 = asyncio.run(inst.bili_yui_upload(file="ok.mp4", title="t"))
    assert out3.get("error") == "NO_FILE"


# ══════════ 视频直链投稿（v0.9.26 接入视频制作系统：URL→yui_uploads→投稿） ══════════


def test_upload_video_url_bad_inputs(monkeypatch) -> None:
    """缺 url/标题、非 http 链接 → 拒绝，不下载。"""
    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    inst = _make_inst(monkeypatch)
    out1 = asyncio.run(inst.bili_yui_upload_video_url(title="t"))
    assert out1.get("error") == "NO_URL"
    out2 = asyncio.run(inst.bili_yui_upload_video_url(url="ftp://x/v.mp4", title="t"))
    assert out2.get("error") == "BAD_URL"
    out3 = asyncio.run(inst.bili_yui_upload_video_url(url="https://x/v.mp4"))
    assert out3.get("error") == "NO_TITLE"


def test_upload_video_url_delegates_to_upload(monkeypatch) -> None:
    """下载成功后委托 bili_yui_upload 投稿（日限额/互斥/安全全复用）。"""
    import urllib.request as _ur

    from plugin.plugins.bili_viewer import bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    # mock 下载：返回带 ftyp 头、>64KB 的有效视频体
    class _FakeResp:
        def __init__(self):
            head = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41"
            self._data = head + b"\x00" * (80 * 1024)
            self._sent = False

        def read(self, n):
            if self._sent:
                return b""
            self._sent = True
            return self._data[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(_ur, "urlopen", lambda req, timeout=30: _FakeResp())
    monkeypatch.setattr(bili_social, "check_limit", lambda *a, **k: None)
    monkeypatch.setattr(bili_social, "record_limit", lambda *a, **k: None)
    from plugin.plugins.bili_viewer import bili as _bili

    upload_dir = os.path.join(os.path.dirname(os.path.abspath(_bili.__file__)), "yui_uploads")
    before = set(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else set()
    inst = _make_inst(monkeypatch)
    calls: list[tuple] = []

    async def fake_upload(**kw):
        calls.append((kw.get("file"), kw.get("title")))
        return {"output": "稿件已提交审核。", "is_error": False}

    monkeypatch.setattr(inst, "bili_yui_upload", fake_upload)
    try:
        out = asyncio.run(inst.bili_yui_upload_video_url(url="https://cdn.example.com/video.mp4", title="测试投稿", tags="测试"))
        assert out.get("is_error") is False
        assert calls and calls[0][0].startswith("yui_url_upload_") and calls[0][0].endswith(".mp4")
        assert calls[0][1] == "测试投稿"
    finally:
        # 清理 mock 下载产物，避免 pytest 运行污染真实 yui_uploads 目录
        for name in set(os.listdir(upload_dir)) - before:
            try:
                os.remove(os.path.join(upload_dir, name))
            except OSError:
                pass
    # 下载产物应落在 yui_uploads（由委托的 fake 跳过，这里验证扩展名与命名即可）


def test_upload_video_url_download_failure_cleans_up(monkeypatch) -> None:
    """下载异常 → 报错且不留半成品文件。"""
    import urllib.request as _ur

    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")

    def boom(req, timeout=30):
        raise OSError("connection refused")

    monkeypatch.setattr(_ur, "urlopen", boom)
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(bili.__file__)), "yui_uploads")
    before = set(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else set()
    inst = _make_inst(monkeypatch)
    out = asyncio.run(inst.bili_yui_upload_video_url(url="https://cdn.example.com/v.mp4", title="t"))
    assert out.get("error") == "DOWNLOAD_FAILED"
    after = set(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else set()
    assert after <= before  # 无新增残留文件


# ══════════ 视频有效性校验（v0.9.30：防 4KB 占位/空壳被当成功视频） ══════════


def test_looks_like_video_container_heads() -> None:
    """常见视频容器文件头识别；占位/空数据不识别。"""
    import plugin.plugins.bili_viewer as _pkg

    assert _pkg._looks_like_video(b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41") is True
    assert _pkg._looks_like_video(b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00") is True
    assert _pkg._looks_like_video(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x00\x00\x00") is True
    assert _pkg._looks_like_video(b"RIFF\x24\x00\x00\x00AVI LIST") is True
    assert _pkg._looks_like_video(b"\x00\x01\x00\x01\x00\x01\x00\x01\x00\x01\x00\x01") is False
    assert _pkg._looks_like_video(b"") is False
    assert _pkg._looks_like_video(b"\x00" * 16) is False
    assert _pkg._looks_like_video(b"<html>too slow</html>") is False


def test_upload_video_url_bad_content_rejected(monkeypatch) -> None:
    """下载得到 4KB 占位（无视频容器头）→ DOWNLOAD_BAD_CONTENT，删除半成品，不投稿。"""
    import urllib.request as _ur

    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")

    class _FakeResp:
        def __init__(self):
            self._data = b"\x00\x01" * 2048
            self._sent = False

        def read(self, n):
            if self._sent:
                return b""
            self._sent = True
            return self._data[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(_ur, "urlopen", lambda req, timeout=30: _FakeResp())
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(bili.__file__)), "yui_uploads")
    before = set(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else set()
    inst = _make_inst(monkeypatch)
    called: list[str] = []

    async def fake_upload(**kw):
        called.append(kw.get("file", ""))
        return {"output": "ok", "is_error": False}

    monkeypatch.setattr(inst, "bili_yui_upload", fake_upload)
    out = asyncio.run(inst.bili_yui_upload_video_url(url="https://cdn.example.com/v.mp4", title="t"))
    assert out.get("error") == "DOWNLOAD_BAD_CONTENT"
    assert called == []  # 未委托投稿
    after = set(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else set()
    assert after <= before  # 半成品已删除，无残留


def test_yui_upload_rejects_invalid_file(monkeypatch) -> None:
    """bili_yui_upload 对 yui_uploads 里的无效空壳文件 → INVALID_VIDEO_FILE，不消耗额度。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(bili.__file__)), "yui_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    shell = os.path.join(upload_dir, "yui_shell_test.mp4")
    with open(shell, "wb") as f:
        f.write(b"\x00\x01" * 2048)
    try:
        inst = _make_inst(monkeypatch)
        out = asyncio.run(inst.bili_yui_upload(file="yui_shell_test.mp4", title="t"))
        assert out.get("error") == "INVALID_VIDEO_FILE"
    finally:
        try:
            os.remove(shell)
        except OSError:
            pass


# ══════════ 自主唤醒冷却 + 后台静默（read 不抢界面） ══════════


class _FakeCtx:
    def __init__(self):
        self.pushed: list[dict] = []

    def push_message(self, **kwargs):
        self.pushed.append(kwargs)


def test_autonomy_tick_cooldown(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.delenv("YUI_AUTONOMY", raising=False)
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    # 用户在看 B站直播 → 跟随
    parsed = {"is_bili": True, "kind": "live", "room_id": "12345", "subject": "星际争霸2比赛"}
    monkeypatch.setattr(inst, "_yui_resolve_target", lambda p: ("live", "12345", {"title": "星际争霸2比赛", "source": "follow"}))
    monkeypatch.setattr(inst, "_yui_autonomy_text", lambda k, t, i: f"自主任务 {k}:{t}")
    inst._yui_autonomy_tick(parsed)
    assert len(fake_ctx.pushed) == 1
    # 冷却期内不再推送
    inst._yui_autonomy_tick(parsed)
    assert len(fake_ctx.pushed) == 1
    # 冷却过期后再次推送
    inst._yui_cool["live:12345"] = 0
    inst._yui_autonomy_tick(parsed)
    assert len(fake_ctx.pushed) == 2


def test_autonomy_push_is_background_silent(monkeypatch) -> None:
    """v0.7.1 后台化：自主唤醒必须 read（不触发 YUI 开口/不弹气泡）、低优先级、带 autonomy 标记。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.delenv("YUI_AUTONOMY", raising=False)
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    parsed = {"is_bili": True, "kind": "video", "bvid": "BV1xx411c7mD", "subject": "测试视频"}
    monkeypatch.setattr(inst, "_yui_resolve_target", lambda p: ("video", "BV1xx411c7mD", {"title": "测试视频", "source": "follow"}))
    monkeypatch.setattr(inst, "_yui_autonomy_text", lambda k, t, i: "自主任务")
    inst._yui_autonomy_tick(parsed)
    assert len(fake_ctx.pushed) == 1
    kw = fake_ctx.pushed[0]
    assert kw.get("ai_behavior") == "read"  # 静默注入，绝不 respond
    assert kw.get("priority") == 0
    assert kw.get("metadata", {}).get("autonomy") is True
    assert kw.get("visibility") == []
    assert kw.get("coalesce_key") == "yui_autonomy"


def test_autonomy_disabled_env(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    monkeypatch.setenv("YUI_AUTONOMY", "0")
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    inst._yui_autonomy_tick({"is_bili": True, "kind": "live", "room_id": "1", "subject": "s"})
    assert fake_ctx.pushed == []


def test_autonomy_popular_cache_throttle(monkeypatch) -> None:
    """v0.7.1 复查优化：无跟随目标时 popular 拉取 60s 缓存，轮询不重复打 B站 API。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili

    calls = {"n": 0}

    def fake_popular(limit=3):
        calls["n"] += 1
        return [{"bvid": "BV1xx411c7mD", "title": "热门视频", "uname": "u"}]

    monkeypatch.setattr(bili, "popular", fake_popular)
    inst = BiliViewerPlugin(ctx=None)
    k1, t1, i1 = inst._yui_resolve_target(None)
    assert (k1, t1) == ("video", "BV1xx411c7mD")
    k2, t2, _ = inst._yui_resolve_target(None)
    assert (k2, t2) == ("video", "BV1xx411c7mD")
    assert calls["n"] == 1  # 第二次命中缓存
    inst._yui_popular_ts = 0  # 缓存过期
    inst._yui_resolve_target(None)
    assert calls["n"] == 2


# ══════════ bili 层：评论/动态写操作 ══════════


def test_bili_add_reply_posts(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    posted: list[tuple] = []

    def fake_post(url, form, timeout=12.0, account="main"):
        posted.append((url, form.get("oid"), form.get("type"), form.get("message")))
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post", fake_post)
    r = bili.add_reply(7777, 1, "好活", account="yui")
    assert r["ok"] is True
    assert posted == [("https://api.bilibili.com/x/v2/reply/add", 7777, 1, "好活")]


def test_bili_post_dynamic_requires_login(monkeypatch) -> None:
    from plugin.plugins.bili_viewer import bili

    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", "/nonexistent-dir")
    try:
        bili.post_dynamic("测试动态", account="yui")
        raise AssertionError("should raise")
    except bili.BiliError as exc:
        assert exc.code == "NEED_LOGIN"


def test_bili_post_dynamic_vc_stable_endpoint(monkeypatch) -> None:
    """v0.8.8 接口修复：原 /x/dynamic/web/create/v1/dyn 已 404，改用旧版稳定接口
    api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create。
    v0.9.3 修复：content 必须是纯文本（原 json.dumps({"content": text}) 会导致动态显示为
    {"content": "..."} 而非纯文本）；新增回读验证（_dynamic_verified）。"""
    import json as _json

    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    calls: list[tuple[str, dict]] = []

    def fake_post_json(url, body, csrf, account="yui", timeout=12.0, query=None):
        calls.append((url, dict(body or {})))
        return {"code": 0, "data": {"dynamic_id": 1262306092504842241, "dynamic_id_str": "1262306092504842241"}}

    def fake_http_get(url, params=None, account="yui", timeout=12.0):
        # 动态详情接口回读验证：返回正确的内容
        if "get_dynamic_detail" in str(url):
            return {"code": 0, "data": {"card": _json.dumps({"item": {"content": "测试动态"}})}}
        # 对外可见动态流验证（v0.9.9）：返回 items 包含发布的 dyn_id
        if "feed/space" in str(url):
            return {"code": 0, "data": {"items": [{"id_str": "1262306092504842241", "visible": True}]}}
        if "nav" in str(url):
            return {"code": 0, "data": {"mid": 3494363678116792}}
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post_json", fake_post_json)
    monkeypatch.setattr(bili, "_http_get", fake_http_get)
    r = bili.post_dynamic("测试动态", account="yui")
    assert r["ok"] is True
    assert r["dynamic_id"] == "1262306092504842241"
    assert len(calls) == 1
    url, body = calls[0]
    assert "api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create" in url
    assert body["type"] == 4
    assert body["from"] == "create.client.web"
    # v0.9.3：content 必须是纯文本（不能是 JSON 字符串）
    assert body["content"] == "测试动态"


def test_bili_delete_dynamic(monkeypatch) -> None:
    """v0.8.8 新增：删除动态走 rm_dynamic 接口。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    calls: list[tuple[str, dict]] = []

    def fake_post_json(url, body, csrf, account="yui", timeout=12.0, query=None):
        calls.append((url, dict(body or {})))
        return {"code": 0, "data": {}}

    monkeypatch.setattr(bili, "_http_post_json", fake_post_json)
    monkeypatch.setattr(bili, "_dynamic_deleted_verified", lambda account, dynamic_id, tries=3: True)
    r = bili.delete_dynamic(1262306092504842241, account="yui")
    assert r["ok"] is True
    assert len(calls) == 1
    url, body = calls[0]
    assert "dynamic_svr/rm_dynamic" in url
    assert body["dynamic_id"] == 1262306092504842241


def test_bili_safe_multipart_name(monkeypatch) -> None:
    """v0.7.3 防御：multipart 文件名清洗，引号/换行/反斜杠不破坏 boundary。"""
    from plugin.plugins.bili_viewer import bili

    assert bili._safe_multipart_name('a"b') == "a_b"
    assert bili._safe_multipart_name("a\r\nb") == "a__b"
    assert bili._safe_multipart_name("a\\b") == "a_b"
    assert bili._safe_multipart_name("ok.mp4") == "ok.mp4"


# ══════════ 自主查看私信（v0.8.2） ══════════


def test_unread_tick_push_and_cooldown(monkeypatch) -> None:
    """自主查私信：有未读且冷却已过时静默注入；无未读/冷却内不注入。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    fake_counts = {
        "replies": 1, "mentions": 0, "likes": 0, "private_messages": 2, "system": 1,
    }
    monkeypatch.setattr(bili_social, "unread_counts", lambda account="yui": fake_counts)
    # 有未读 → 注入
    inst._unread_tick()
    assert len(fake_ctx.pushed) == 1
    kw = fake_ctx.pushed[0]
    assert kw.get("ai_behavior") == "read"
    assert kw.get("visibility") == []
    assert kw.get("coalesce_key") == "bili_unread_sync"
    assert "私信2" in kw["parts"][0]["text"]
    assert "4 条未读" in kw["parts"][0]["text"]
    # 冷却期内不重复注入
    inst._unread_tick()
    assert len(fake_ctx.pushed) == 1
    # 无未读 → 不注入
    monkeypatch.setattr(bili_social, "unread_counts", lambda account="yui": {"replies": 0, "mentions": 0, "likes": 0, "private_messages": 0, "system": 0})
    inst._unread_inject_ts = 0.0
    inst._unread_tick()
    assert len(fake_ctx.pushed) == 1


# ══════════ 弹幕@自主回复（v0.8.5）：带房间号可自主回弹幕，同直播间冷却 ══════════


def test_handle_mention_auto_reply_with_room(monkeypatch) -> None:
    """@消息带房间号：注入自主回复任务（bili_live_send_danmaku），静默 read。"""
    import time

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    inst._handle_mention({"at": time.time(), "text": "@yui小猫娘 你好呀", "sender": "路人A", "room": "12345"})
    assert len(fake_ctx.pushed) == 1
    kw = fake_ctx.pushed[0]
    assert kw.get("ai_behavior") == "read"
    assert kw.get("coalesce_key") == "danmaku_mention"
    assert kw.get("metadata", {}).get("mention_auto_reply") is True
    assert "bili_live_send_danmaku" in kw["parts"][0]["text"]
    assert "房间 12345" in kw["parts"][0]["text"]


def test_handle_mention_cooldown_same_room(monkeypatch) -> None:
    """同一直播间冷却期内再次@：只提醒不催自主回复（防刷屏）。"""
    import time

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    m = {"at": time.time(), "text": "@小猫娘 在吗", "sender": "B", "room": "12345"}
    inst._handle_mention(m)
    inst._handle_mention(m)  # 冷却期内
    assert len(fake_ctx.pushed) == 2
    assert fake_ctx.pushed[0]["metadata"]["mention_auto_reply"] is True
    assert fake_ctx.pushed[1]["metadata"]["mention_auto_reply"] is False
    # 冷却过期后恢复自主回复
    inst._mention_reply_cool["mention_reply:12345"] = 0.0
    inst._handle_mention(m)
    assert fake_ctx.pushed[2]["metadata"]["mention_auto_reply"] is True


def test_handle_mention_no_room_only_remind(monkeypatch) -> None:
    """无房间号（扩展未带）：只注入提醒，不催自主回复。"""
    import time

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    inst._handle_mention({"at": time.time(), "text": "yui 快看", "sender": "C", "room": ""})
    assert len(fake_ctx.pushed) == 1
    assert fake_ctx.pushed[0]["metadata"]["mention_auto_reply"] is False
    assert "下次自然对话" in fake_ctx.pushed[0]["parts"][0]["text"]


def test_handle_mention_stale_skipped(monkeypatch) -> None:
    """超过 30 秒的旧@：不注入（感知循环已消费或过时）。"""
    import time

    from plugin.plugins.bili_viewer import BiliViewerPlugin

    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    inst._handle_mention({"at": time.time() - 40, "text": "@yui 你好", "sender": "D", "room": "9"})
    assert len(fake_ctx.pushed) == 0


def test_mention_notify_room_roundtrip(monkeypatch) -> None:
    """web_ui 的@通知 room 字段写入与 clear 清空（v0.8.5 新增字段）。"""
    import web_ui

    old = web_ui._MENTION_NOTIFY
    try:
        web_ui._MENTION_NOTIFY = {"at": 1.0, "text": "@小猫娘 在吗", "sender": "E", "room": "888"}
        info = web_ui.get_mention_notify(clear=True)
        assert info["room"] == "888"
        assert web_ui._MENTION_NOTIFY["text"] == ""
        assert web_ui._MENTION_NOTIFY["sender"] == ""
        assert web_ui._MENTION_NOTIFY["room"] == ""
    finally:
        web_ui._MENTION_NOTIFY = old


# ══════════ 直播间活跃观众（v0.8.6）：弹幕发言者 → 可关注对象 ══════════


def test_get_recent_room_prefers_fresh_ws_room(monkeypatch) -> None:
    """弹幕 WS 房间提取：取最新带 room 的记录、过滤空、超龄返回空（v0.8.6 定位权威源）。"""
    import time

    import web_ui

    old = web_ui._DANMAKU_BUF
    try:
        web_ui._DANMAKU_BUF.clear()
        now = time.time()
        web_ui._DANMAKU_BUF.extend([
            ("旧人", "x", now - 300, "111"),       # 超龄 → 忽略
            ("系统提示", "公告", now - 5, ""),      # 无房间号 → 忽略
            ("新人", "y", now - 1, "22996016"),     # 最新有效
        ])
        assert web_ui.get_recent_room(max_age=120) == "22996016"
        assert web_ui.get_recent_room(max_age=0.5) == ""  # 全部超龄 → 空
    finally:
        web_ui._DANMAKU_BUF = old


def test_ext_tab_danmaku_room_fallback(monkeypatch) -> None:
    """_ext_tab 定位纠偏：标签页过期时用弹幕 WS 房间兜底；冲突时以弹幕房间为准（v0.8.6）。"""
    import time

    from plugin.plugins.bili_viewer import BiliViewerPlugin, web_ui

    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    old = web_ui._DANMAKU_BUF
    try:
        web_ui._DANMAKU_BUF.clear()
        now = time.time()
        # 1) 标签页过期 + 弹幕房间新鲜 → 弹幕房间兜底
        inst._ext_tab_data = {"url": "https://www.bilibili.com/", "title": "首页", "source": "chrome", "at": now - 300}
        web_ui._DANMAKU_BUF.append(("A", "hi", now - 1, "22996016"))
        tab = inst._ext_tab()
        assert tab is not None and tab["source"] == "danmaku-ws"
        assert "22996016" in tab["url"]
        # 2) 标签页新鲜但解析出的直播房间与弹幕 WS 不一致 → 以弹幕房间为准
        inst._ext_tab_data = {"url": "https://live.bilibili.com/888", "title": "别的直播", "source": "chrome", "at": now}
        tab2 = inst._ext_tab()
        assert "22996016" in tab2["url"]
        # 3) 标签页新鲜且与弹幕房间一致 → 保持标签页
        inst._ext_tab_data = {"url": "https://live.bilibili.com/22996016", "title": "直播", "source": "chrome", "at": now}
        tab3 = inst._ext_tab()
        assert tab3["source"] == "chrome"
        # 4) 无弹幕房间且标签页过期 → None
        web_ui._DANMAKU_BUF.clear()
        inst._ext_tab_data = {"url": "https://www.bilibili.com/", "title": "首页", "source": "chrome", "at": now - 300}
        assert inst._ext_tab() is None
        # 5) 标签页新鲜为视频页（弹幕房间来自刚关掉的直播）→ 尊重标签页，不覆盖
        inst._ext_tab_data = {"url": "https://www.bilibili.com/video/BV1xx411c7mD", "title": "视频", "source": "chrome", "at": now}
        web_ui._DANMAKU_BUF.append(("B", "hi", now - 1, "22996016"))
        tab5 = inst._ext_tab()
        assert tab5["source"] == "chrome" and "BV1xx411c7mD" in tab5["url"]
    finally:
        web_ui._DANMAKU_BUF = old
        inst._ext_tab_data = None



def test_get_recent_viewers_filters_and_dedups(monkeypatch) -> None:
    """viewers 提取：按房间过滤、去重、排除系统消息与空昵称。"""
    import web_ui

    old = web_ui._DANMAKU_BUF
    try:
        web_ui._DANMAKU_BUF.clear()
        web_ui._DANMAKU_BUF.extend([
            ("卡比梦", "x", 1, "22996016"),
            ("夜半烟未散", "y", 2, "22996016"),
            ("卡比梦", "z", 3, "22996016"),          # 同人重复
            ("系统提示", "公告", 4, "22996016"),       # 系统消息排除
            ("", "空", 5, "22996016"),                # 空昵称排除
            ("范海鸽", "w", 6, "999"),                # 别的房间
        ])
        v = web_ui.get_recent_viewers("22996016", limit=10)
        assert v == ["卡比梦", "夜半烟未散"]  # 新→旧去重，排除系统/空/异房间（卡比梦最新发言）
        # 不指定房间：全部发言者（仍去重、排除系统/空）
        all_v = web_ui.get_recent_viewers("", limit=10)
        assert "范海鸽" in all_v and "卡比梦" in all_v and "系统提示" not in all_v
    finally:
        web_ui._DANMAKU_BUF = old


def test_live_viewers_tool_resolves_uid_and_skips_ambiguous(monkeypatch) -> None:
    """bili_live_viewers：昵称→uid；解析失败的用户绝不返回 uid（防关注错人）。"""
    import asyncio

    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili, web_ui

    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    old = web_ui._DANMAKU_BUF
    try:
        web_ui._DANMAKU_BUF.clear()
        web_ui._DANMAKU_BUF.extend([
            ("卡比梦", "x", 1, "12345"),
            ("同名路人", "y", 2, "12345"),
        ])
        def fake_resolve(name, account="yui"):
            if name == "卡比梦":
                return 10001
            raise bili.BiliError("USER_AMBIGUOUS 未找到昵称「同名路人」完全匹配的用户", "USER_AMBIGUOUS")
        monkeypatch.setattr(bili, "_resolve_uid", fake_resolve)
        out = asyncio.run(inst.bili_live_viewers(room="12345"))
        assert out["is_error"] is False
        assert out["result"]["viewers"] == [{"uid": 10001, "name": "卡比梦"}]
        assert "同名路人" in out["result"]["unresolved"]
        assert "不要猜 uid" in out["output"]
        assert "bili_follow_user" in out["output"]
    finally:
        web_ui._DANMAKU_BUF = old


def test_live_info_online_fallback(monkeypatch) -> None:
    """直播中但旧接口 online=0：兜底 getInfoByRoom 人气值（v0.8.6 修'在线人数0'误报）。"""
    from plugin.plugins.bili_viewer import bili

    http_seq = iter([
        {"code": 0, "data": {"room_id": 888, "live_status": 1, "online": 0, "uid": 7, "title": "t"}},
        {"code": 0, "data": {"name": "主播A"}},  # acc/info 反查昵称
    ])
    wbi_seq = iter([
        {"code": 0, "data": {"room_info": {"online": 123}, "anchor_info": {}}},
    ])
    monkeypatch.setattr(bili, "_resolve_room", lambda room: {"room_id": 888, "live_status": 1})
    monkeypatch.setattr(bili, "_http_get", lambda *a, **k: next(http_seq))
    monkeypatch.setattr(bili, "_wbi_get", lambda *a, **k: next(wbi_seq))
    info = bili.live_info("888")
    assert info["online"] == 123  # 0 → 兜底成功


def test_yui_cookie_file_mtime_cache(monkeypatch, tmp_path) -> None:
    """v0.7.3 优化：小号 Cookie 文件 mtime 缓存，内容未变不重复读盘。"""
    from plugin.plugins.bili_viewer import bili

    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    bili._yui_cookie_file_cache = (0.0, "")  # 清全局缓存
    (tmp_path / "yui_cookie.txt").write_text("SESSDATA=abc; bili_jct=def", encoding="utf-8")
    # 确定性 mtime：1.0 期间内容不变 → 只读一次；2.0 视为文件已改 → 重读
    mtimes = iter([1.0, 1.0, 1.0, 2.0, 2.0])
    monkeypatch.setattr(bili.os.path, "getmtime", lambda _p: next(mtimes))
    reads = {"n": 0}
    import builtins

    orig_open = builtins.open

    def counting_open(path, *a, **kw):
        if str(path).endswith("yui_cookie.txt"):
            reads["n"] += 1
        return orig_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", counting_open)
    assert bili._cookie("yui") == "SESSDATA=abc; bili_jct=def"
    assert bili._cookie("yui") == "SESSDATA=abc; bili_jct=def"  # 命中缓存，不重复读
    assert reads["n"] == 1
    assert bili._cookie("yui") == "SESSDATA=abc; bili_jct=def"  # mtime 仍 1.0，继续命中
    assert reads["n"] == 1
    # mtime 变化（文件被改）→ 重读
    assert bili._cookie("yui") == "SESSDATA=abc; bili_jct=def"  # 内容文件未真正改，重读仍同值
    assert reads["n"] == 2

# ══════════ v0.9.24：run 批量点赞 + 防编造 + 通知防复述 + accounts 一致性 ══════════


def _fake_videos():
    return [
        {"bvid": "BV1", "title": "视频一"},
        {"bvid": "BV2", "title": "视频二"},
        {"bvid": "BV3", "title": "视频三"},
    ]


def test_like_all_success(monkeypatch) -> None:
    """v0.9.24：批量点赞全部成功 → 汇总输出，逐个真实调用 + 回读。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=yui; bili_jct=y")
    called = []

    def fake_user_videos(mid, page=1, limit=15):
        called.append(("list", mid, page, limit))
        return _fake_videos()

    def fake_like(bvid, account, undo=False):
        called.append(("like", bvid, account, undo))
        return {"bvid": bvid, "liked": True}

    monkeypatch.setattr(bili, "user_videos", fake_user_videos)
    monkeypatch.setattr(bili_social, "video_like", fake_like)
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_like_all(mid="1426057300", account="yui", limit=3))
    assert r["is_error"] is False
    assert "3 个视频点赞" in r["output"]
    assert [c[0] for c in called] == ["list", "like", "like", "like"]
    assert called[0] == ("list", 1426057300, 1, 3)


def test_like_all_partial_fail_no_interrupt(monkeypatch) -> None:
    """v0.9.24：风控/限流单个失败不中断，其余继续 → 部分成功汇总。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=yui; bili_jct=y")
    likes = iter([
        {"bvid": "BV1", "liked": True},
        None,  # BV2 抛错
        {"bvid": "BV3", "liked": True},
    ])

    def fake_like(bvid, account, undo=False):
        r = next(likes)
        if r is None:
            raise bili.BiliError("被风控拦截，请降低操作频率", "RISK_CONTROL")
        return r

    monkeypatch.setattr(bili, "user_videos", lambda mid, page=1, limit=15: _fake_videos())
    monkeypatch.setattr(bili_social, "video_like", fake_like)
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_like_all(mid="1426057300", account="yui", limit=3))
    assert r["is_error"] is False
    assert "已点赞 2 个，1 个未点成" in r["output"]
    assert r["result"]["liked"][0]["bvid"] == "BV1"
    assert r["result"]["failed"][0]["error"] == "RISK_CONTROL"


def test_like_all_missing_mid(monkeypatch) -> None:
    """v0.9.24：缺 mid → EMPTY_MID，提示传 UP主 uid。"""
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_like_all(mid="", account="yui"))
    assert r["is_error"] is True and r["error"] == "EMPTY_MID"
    assert "mid" in r["output"]


def test_like_all_all_failed(monkeypatch) -> None:
    """v0.9.24：全部失败 → is_error=True，禁止假成功。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=yui; bili_jct=y")

    def fake_like(bvid, account, undo=False):
        raise bili.BiliError("登录态失效或触发风控", "AUTH_FAILED")

    monkeypatch.setattr(bili, "user_videos", lambda mid, page=1, limit=15: _fake_videos())
    monkeypatch.setattr(bili_social, "video_like", fake_like)
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_like_all(mid="1426057300", account="yui", limit=3))
    assert r["is_error"] is True and r["error"] == "LIKE_ALL_FAILED"
    assert "点赞失败" in r["output"]


def test_entry_run_like_branch(monkeypatch) -> None:
    """v0.9.24：run 入口 action=like → 路由批量点赞（mid 必填）。"""
    from plugin.plugins.bili_viewer import bili, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=yui; bili_jct=y")
    monkeypatch.setattr(bili, "user_videos", lambda mid, page=1, limit=15: _fake_videos())
    monkeypatch.setattr(bili_social, "video_like", lambda bvid, account, undo=False: {"bvid": bvid, "liked": True})
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.entry_run(action="like", mid="1426057300", account="yui", limit=3))
    assert r["is_error"] is False and "点赞" in r["output"]


def test_run_no_action_anti_lying(monkeypatch) -> None:
    """v0.9.24：不支持的 action → NO_ACTION，明确禁止编造风控/成功。"""
    inst = _make_inst(monkeypatch)
    monkeypatch.setattr(inst, "_ext_tab", lambda force=False: None)
    r = asyncio.run(inst.entry_run(action="give_me_a_pony"))
    assert r["is_error"] is True and r["error"] == "NO_ACTION"
    assert "严禁编造" in r["output"]
    assert "本插件暂不支持" in r["output"]


def test_assign_cookie_refresh_syncs_uid(monkeypatch, tmp_path) -> None:
    """v0.9.24：刷新分支同步 accounts uid；未知新 uid 拒绝不改写角色映射。"""
    from plugin.plugins.bili_viewer import bili

    pkg = _pkg()
    monkeypatch.delenv("BILI_COOKIE", raising=False)
    monkeypatch.delenv("BILI_YUI_COOKIE", raising=False)
    monkeypatch.setattr(bili, "_YUI_COOKIE_DIR", str(tmp_path))
    monkeypatch.setattr(pkg, "_accounts_path", lambda: str(tmp_path / "accounts.json"))
    inst = _make_inst(monkeypatch)
    # 先绑定 main=111、yui=222
    inst._assign_cookie("SESSDATA=a; DedeUserID=111; bili_jct=a1", 0, "extension")
    inst._assign_cookie("SESSDATA=b; DedeUserID=222; bili_jct=b2", 0, "extension")
    # 刷新 yui=222 → 账号条目 uid 保持 222，bound_at 更新
    r = inst._assign_cookie("SESSDATA=b3; DedeUserID=222; bili_jct=b3b", 5, "extension")
    assert r["bound"] is True and "小号" in r["detail"]
    acc = pkg._read_accounts()
    assert acc["yui"]["uid"] == 222
    # 未知新 uid（如主账号 333）→ 拒绝，不改写任何角色映射
    r2 = inst._assign_cookie("SESSDATA=c; DedeUserID=333; bili_jct=c3", 6, "extension")
    assert r2["bound"] is False
    acc = pkg._read_accounts()
    assert acc["main"]["uid"] == 111 and acc["yui"]["uid"] == 222


# ══════════ 指令桥（v0.9.27）：YUI 命令豆包 ══════════


def test_command_doubao_writes_inbox(monkeypatch, tmp_path) -> None:
    """v0.9.27：bili_yui_command_doubao 写入 inbox JSON 并返回请求号。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_yui_command_doubao(instruction="生成一个关于猫娘的10秒视频并投稿"))
    assert r["is_error"] is False
    rid = r["result"]["request_id"]
    assert rid.startswith("cmd_")
    inbox = tmp_path / "N.E.K.O" / "doubao_bridge" / "inbox"
    assert (inbox / f"{rid}.json").is_file()
    data = json.loads((inbox / f"{rid}.json").read_text(encoding="utf-8"))
    assert data["request_id"] == rid
    assert "猫娘" in data["instruction"]
    assert data["from"] == "yui"


def test_command_doubao_empty_instruction(monkeypatch) -> None:
    """v0.9.27：空指令 → NO_INSTRUCTION。"""
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_yui_command_doubao(instruction=""))
    assert r["is_error"] is True and r["error"] == "NO_INSTRUCTION"


def test_read_doubao_reply_processing(monkeypatch, tmp_path) -> None:
    """v0.9.27：inbox 有指令、outbox 无回执 → status=processing。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    inst = _make_inst(monkeypatch)
    rid = "cmd_test_1"
    inbox = tmp_path / "N.E.K.O" / "doubao_bridge" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{rid}.json").write_text('{"request_id":"cmd_test_1"}', encoding="utf-8")
    r = asyncio.run(inst.bili_yui_read_doubao_reply(request_id=rid))
    assert r["is_error"] is False and r["result"]["status"] == "processing"
    assert "还在处理" in r["output"]


def test_read_doubao_reply_done(monkeypatch, tmp_path) -> None:
    """v0.9.27：outbox 有回执 → 返回豆包答复原文。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    inst = _make_inst(monkeypatch)
    rid = "cmd_done_1"
    outbox = tmp_path / "N.E.K.O" / "doubao_bridge" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    payload = {"request_id": rid, "reply": "视频已生成并投稿，稿件标题《猫娘的一天》。", "done_at": "2026-10-23 20:00:00"}
    (outbox / f"{rid}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    r = asyncio.run(inst.bili_yui_read_doubao_reply(request_id=rid))
    assert r["is_error"] is False and r["result"]["status"] == "done"
    assert "猫娘的一天" in r["output"]


def test_read_doubao_reply_bad_request_id(monkeypatch) -> None:
    """v0.9.27：路径穿越等非法请求号 → BAD_REQUEST_ID。"""
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_yui_read_doubao_reply(request_id="../../etc/passwd"))
    assert r["is_error"] is True and r["error"] == "BAD_REQUEST_ID"


def test_read_doubao_reply_not_found(monkeypatch, tmp_path) -> None:
    """v0.9.27：inbox/outbox 都没有 → NOT_FOUND。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    inst = _make_inst(monkeypatch)
    r = asyncio.run(inst.bili_yui_read_doubao_reply(request_id="cmd_ghost_9"))
    assert r["is_error"] is True and r["error"] == "NOT_FOUND"


# ══════════ 弹幕指令原文防呆（v0.9.28） ══════════


def test_looks_like_danmaku_instruction() -> None:
    """v0.9.28：指令型文本命中、正常内容不误伤。"""
    from plugin.plugins.bili_viewer import _looks_like_danmaku_instruction as chk

    assert chk("用弹幕自我介绍一下") is True
    assert chk("发弹幕说你好") is True
    assert chk("弹幕：大家好啊") is True
    assert chk("帮我发条弹幕") is True
    assert chk("我是YUI小猫娘，很高兴认识大家") is False
    assert chk("弹幕说得好有趣") is False  # 豁免：感叹不是指令
    assert chk("") is False


def test_live_danmaku_rejects_instruction_text(monkeypatch) -> None:
    """v0.9.28：直播弹幕收到指令原文 → INSTRUCTION_AS_CONTENT，不发不编。"""
    inst = _make_inst(monkeypatch)
    monkeypatch.setattr(inst, "_ext_tab", lambda force=False: None)
    r = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="用弹幕自我介绍一下", account="yui"))
    assert r["is_error"] is True and r["error"] == "INSTRUCTION_AS_CONTENT"
    assert "内容本身" in r["output"]


def test_video_danmaku_rejects_instruction_text(monkeypatch) -> None:
    """v0.9.28：视频弹幕收到指令原文 → INSTRUCTION_AS_CONTENT。"""
    inst = _make_inst(monkeypatch)
    monkeypatch.setattr(inst, "_ext_tab", lambda force=False: None)
    r = asyncio.run(inst.bili_video_send_danmaku(bvid="BV1xx411c7mD", text="发弹幕说你好", account="yui"))
    assert r["is_error"] is True and r["error"] == "INSTRUCTION_AS_CONTENT"


def test_live_danmaku_normal_text_passes_guard(monkeypatch) -> None:
    """v0.9.28：正常内容不命中指令防呆（继续走后续发送流程）。"""
    from plugin.plugins.bili_viewer import bili

    inst = _make_inst(monkeypatch)
    monkeypatch.setattr(inst, "_ext_tab", lambda force=False: None)
    monkeypatch.setattr(bili, "live_send_danmaku", lambda room, text, account="yui": {"room": room, "text": text})
    monkeypatch.setattr(inst, "_danmaku_recently_sent", lambda *a, **k: False)
    r = asyncio.run(inst.bili_live_send_danmaku(room="12345", text="我是YUI小猫娘，很高兴认识大家", account="yui"))
    assert r["is_error"] is False and "已发送" in r["output"]


# ══════════ 视频素材自给自足编排（v0.9.29） ══════════


def test_yui_autonomy_text_video_pipeline(monkeypatch) -> None:
    """v0.9.29：自主运营注入含'视频素材自给自足'编排，严禁向玩家索要链接。"""
    inst = _make_inst(monkeypatch)
    text = inst._yui_autonomy_text("live", "12345", {"title": "测试直播间", "source": "autonomy"})
    assert "严禁向玩家索要视频链接" in text
    assert "bili_yui_command_doubao" in text
    assert "bili_yui_read_doubao_reply" in text
    assert "bili_yui_upload_video_url" in text


def test_command_doubao_desc_has_video_pipeline(monkeypatch) -> None:
    """v0.9.29：三个桥工具的描述含视频素材自给自足编排与'严禁向玩家索要'。"""
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods

    inst = _make_inst(monkeypatch)
    metas = {m.name: m for m, _ in collect_llm_tool_methods(inst)}
    desc1 = metas["bili_yui_command_doubao"].description
    assert "严禁" in desc1 and "bili_yui_read_doubao_reply" in desc1 and "bili_yui_upload_video_url" in desc1
    desc2 = metas["bili_yui_read_doubao_reply"].description
    assert "严禁把直链转手丢给玩家" in desc2
    desc3 = metas["bili_yui_upload_video_url"].description
    assert "严禁因为拿不到链接就向玩家索要" in desc3


# ══════════ 豆包指令桥回执就绪自动唤醒（v0.9.31） ══════════


def test_bridge_reply_wakeup_once(monkeypatch, tmp_path) -> None:
    """v0.9.31：outbox 有 done 回执时注入一次 respond 信号唤醒 YUI，且不重复通知。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    class _Ctx:
        def __init__(self) -> None:
            self.pushes = []

        def push_message(self, **kwargs) -> None:
            self.pushes.append(kwargs)

    monkeypatch.setenv("BILLI_SYNC_DISABLE", "1")  # 防启动真实后台线程
    ctx = _Ctx()
    inst = BiliViewerPlugin(ctx=ctx)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    rid = "cmd_test123"
    (outbox / f"{rid}.json").write_text(
        json.dumps(
            {"request_id": rid, "reply": "视频已生成：https://example.com/v.mp4", "status": "done", "done_at": "2026-10-24 01:12:55"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(inst, "_doubao_bridge_dir", lambda: str(tmp_path))

    inst._bridge_reply_tick()
    inst._bridge_reply_tick()  # 同一回执第二次 tick 不再通知

    assert len(ctx.pushes) == 1
    body = ctx.pushes[0]
    assert body.get("ai_behavior") == "respond"
    assert body.get("coalesce_key") == f"doubao_reply_{rid}"
    text = body["parts"][0]["text"]
    assert rid in text
    assert "bili_yui_read_doubao_reply" in text
    assert body.get("metadata", {}).get("request_id") == rid


def test_bridge_reply_skip_processing(monkeypatch, tmp_path) -> None:
    """v0.9.31：回执未完成（processing）不注入唤醒信号。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    class _Ctx:
        def __init__(self) -> None:
            self.pushes = []

        def push_message(self, **kwargs) -> None:
            self.pushes.append(kwargs)

    monkeypatch.setenv("BILLI_SYNC_DISABLE", "1")
    ctx = _Ctx()
    inst = BiliViewerPlugin(ctx=ctx)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    rid = "cmd_proc1"
    (outbox / f"{rid}.json").write_text(
        json.dumps({"request_id": rid, "status": "processing"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(inst, "_doubao_bridge_dir", lambda: str(tmp_path))

    inst._bridge_reply_tick()

    assert ctx.pushes == []
    assert rid not in inst._bridge_notified


# ══════════ 工具入口参数类型防御（v0.9.32）：宿主把 parameters 键传成标量 ══════════


def test_tool_params_scalar_int_defense(monkeypatch) -> None:
    """v0.9.32 复现回归：宿主把 _ 里 parameters 键传成 int（如 limit=30 落入该键），
    bili_dm_read 不再抛 'int' object has no attribute 'get'，参数从显式形参兜底。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin, bili_social

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)

    def _fake_dm_messages(account, uid, limit, mark_read=False):
        return {
            "uid": 456,
            "marked_read": False,
            "messages": [
                {"msg_seqno": 1, "from": "them", "time": "10-24 11:00", "text": "在吗", "sender_uid": 456},
                {"msg_seqno": 2, "from": "me", "time": "10-24 11:01", "text": "在的", "sender_uid": 123},
            ],
        }

    monkeypatch.setattr(bili_social, "dm_messages", _fake_dm_messages)
    # 模拟宿主异常传参：parameters 键值为标量 int（实测 bili_dm_read 崩溃根因）
    out = asyncio.run(inst.bili_dm_read(uid="456", parameters=30))
    assert out["is_error"] is False
    assert "在吗" in out["output"]


def test_tool_params_scalar_str_defense(monkeypatch) -> None:
    """v0.9.32 复现回归：parameters 键值为 str 时（bili_see_live 实测崩溃场景），
    工具不抛 'str' object has no attribute 'get'，返回正常的缺参错误。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.setenv("BILLI_SYNC_DISABLE", "1")
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    inst._ext_dom = lambda: None
    inst._ext_tab = lambda force=False: None
    out = asyncio.run(inst.bili_see_live(parameters="abc"))
    assert out["is_error"] is True
    assert out["error"] == "NO_ROOM"


def test_entry_run_scalar_parameters_defense(monkeypatch) -> None:
    """v0.9.32：总入口 entry_run 收到标量 parameters 时不崩溃，action 从 kwargs 正常解析。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.setenv("BILLI_SYNC_DISABLE", "1")
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    inst._ext_tab = lambda force=False: None
    out = asyncio.run(inst.entry_run(action="query", parameters=30))
    assert out["is_error"] is False
    assert "未感知到当前 B站 页面" in out["output"]


def test_tool_params_dm_send_scalar_int_defense(monkeypatch) -> None:
    """v0.9.32：bili_dm_send 收到标量 parameters（int）时不崩溃，返回缺参错误。"""
    from plugin.plugins.bili_viewer import BiliViewerPlugin

    monkeypatch.setenv("BILI_YUI_COOKIE", "SESSDATA=y; bili_jct=yj")
    fake_ctx = _FakeCtx()
    inst = BiliViewerPlugin(ctx=fake_ctx)
    out = asyncio.run(inst.bili_dm_send(parameters=30))
    assert out["is_error"] is True
    assert out["error"] == "EMPTY_PARAMS"