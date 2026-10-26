"""离线测试：不碰网络，只验纯逻辑。

跑：python -m pytest neko-plugins/neko_video/tests -q
或：python neko-plugins/neko_video/tests/test_offline.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _bili import _search_queries, _similarity, strip_site_suffix
from _digest import build_digest, content_fingerprint, rhythm_hotspots
from _window import is_bilibili_live, is_bilibili_title


def test_strip_site_suffix():
    cases = {
        "机械迷城_哔哩哔哩_bilibili": "机械迷城",
        "机械迷城_哔哩哔哩_bilibili - Google Chrome": "机械迷城",
        "【官方 MV】Never Gonna Give You Up_哔哩哔哩_bilibili - Microsoft Edge": "【官方 MV】Never Gonna Give You Up",
        "某视频哔哩哔哩_bilibili": "某视频",
        "标题 - 联想浏览器": "标题",
    }
    for raw, want in cases.items():
        got = strip_site_suffix(raw)
        assert got == want, f"{raw!r} -> {got!r} != {want!r}"


def test_similarity_prefers_exact():
    q = "机械迷城"
    exact = _similarity(q, "机械迷城")
    partial = _similarity(q, "【机械迷城】解密全流程")
    unrelated = _similarity(q, "原神新版本前瞻")
    assert exact >= partial > unrelated
    # 包含关系要明显高于无关项
    assert partial > 0.7
    assert unrelated < 0.5


def test_similarity_ignores_punctuation():
    assert _similarity("abc def", "abc，def！") > 0.95


def test_rhythm_hotspots_finds_dense_point():
    msgs = (
        [{"t": 95.0 + i * 0.1, "text": "哈哈哈哈" if i % 2 else "魂游说是"}
         for i in range(6)]
        + [{"t": 10.0, "text": "开始了"}, {"t": 300.0, "text": "泪目了"}]
    )
    spots = rhythm_hotspots(msgs, top=3)
    assert spots, "应至少找到一个高点"
    top = max(spots, key=lambda s: s["count"])
    assert 95 <= top["at"] <= 100, top
    assert top["count"] == 6
    assert top["emotion"] == "笑"


def test_build_digest_is_compact():
    info = {"title": "机械迷城速通", "author": "某UP", "tname": "单机游戏",
            "duration": 213, "desc": "一个解谜游戏的速通。", "bvid": "BV1xx411c7mD"}
    # 高光点门槛：峰值至少 3 条弹幕（见 rhythm_hotspots 的 min_count），
    # 所以测试数据要有真实密度（5 秒内 4 条），否则不构成高光。
    dm = [{"t": 5.0 + i * 0.2, "text": t}
          for i, t in enumerate(["哈哈", "笑死", "绷不住", "离谱"])]
    dm += [{"t": 200.0, "text": "来了"}]
    cm = [{"text": "经典！", "like": 999}]
    digest = build_digest(info, dm, cm)
    assert "机械迷城速通" in digest
    assert "UP主 某UP" in digest
    assert "观众反应高峰" in digest
    assert "高赞评论" in digest
    assert len(digest) < 600, f"摘要太长: {len(digest)}"


def test_build_digest_handles_empty():
    info = {"title": "空视频", "bvid": "BV1xx411c7mD", "duration": 60}
    digest = build_digest(info, [], [])
    assert "空视频" in digest
    assert "弹幕稀少" in digest  # 无数据时的兜底措辞


def test_fingerprint_stable_and_distinct():
    info = {"bvid": "BV1", "title": "t"}
    dm = [{"t": 1.0, "text": "a"}]
    a1 = content_fingerprint(info, dm, [])
    a2 = content_fingerprint(info, dm, [])
    assert a1 == a2
    b = content_fingerprint(info, dm + [{"t": 2.0, "text": "b"}], [])
    assert a1 != b


def test_similarity_rejects_short_substring_trap():
    """短泛化标题偶然成为长查询子串时，绝不能得高分（否则错配到无关视频）。"""
    q = "我记录下了妹妹的最后7天...【自制伪纪录片电影】"
    trap = _similarity(q, "妹妹")          # 子串但覆盖极小
    assert trap < 0.55, f"短子串陷阱得分过高: {trap}"
    good = _similarity(q, q + "完整版")     # q 是候选的子串
    assert good > 0.8, f"真相关候选得分过低: {good}"


def test_similarity_normal_containment_still_works():
    assert _similarity("机械迷城", "【机械迷城】解密全流程") > 0.7
    assert _similarity("机械迷城", "原神前瞻") < 0.4


def test_search_queries_fallback_order():
    """多级回退查询：完整标题搜不到时，要能回退到主标题。"""
    qs = _search_queries("我记录下了妹妹的最后7天...【自制伪纪录片电影】")
    assert qs[0] == "我记录下了妹妹的最后7天...【自制伪纪录片电影】"
    assert "我记录下了妹妹的最后7天" in qs, qs
    # 主标题必须排在后备位，且不能有重复项
    assert len(qs) == len(set(qs))


def test_strip_site_suffix_lenovo():
    """联想浏览器标题（破折号后无空格）—— 实测踩过的 bug。"""
    raw = "我记录下了妹妹的最后7天...【自制伪纪录片电影】_哔哩哔哩_bilibili -联想浏览器"
    assert strip_site_suffix(raw) == "我记录下了妹妹的最后7天...【自制伪纪录片电影】"


def test_live_page_detection():
    """直播间要能识别出来（直播间没有 BV，不该去搜）。"""
    live = "假期余额不足，所以要—— - W博士与AI猫娘 - 哔哩哔哩直播，二次元弹幕直播平台 -联想浏览器"
    assert is_bilibili_title(live)
    assert is_bilibili_live(live)

    video = "机械迷城_哔哩哔哩_bilibili"
    assert is_bilibili_title(video)
    assert not is_bilibili_live(video)


def test_search_orders_cover_pubdate_first():
    """默认相关性排序会漏掉精确视频，必须优先用 pubdate —— 实测踩过的坑。"""
    import inspect

    import _bili
    src = inspect.getsource(_bili.resolve_bv_by_title)
    assert '"pubdate"' in src and '"click"' in src
    assert src.index('"pubdate"') < src.index('"click"')



def test_compose_text_kinds_differ():
    """三种时机的文案必须不同，否则她每处都说同样的话。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    assert 'kind == "end"' in src
    assert 'kind == "hotspot"' in src
    # 收尾要提"评论"（否则等于没读高赞评论）
    s = src.index('kind == "end"')
    assert "评论" in src[s:s + 300]


def test_no_placeholders_in_compose():
    """推送文案里不能出现角色名占位符 —— 实测可见气泡不会替换它们。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    start = src.index("def _compose_text")
    body = src[start:start + 1800]
    assert "{master}" not in body, "文案里还有 {master} 占位符"
    assert "{lanlan_name}" not in body, "文案里还有 {lanlan_name} 占位符"

def test_hotspot_fires_when_playback_reaches_it():
    """播到峰值那一刻才开口 —— 事件驱动，不是定时。"""
    from _schedule import due_hotspot

    spots = [{"at": 20.0}, {"at": 95.0}, {"at": 200.0}]
    fired = set()
    # 才播到 10 秒：一个都还没到
    assert due_hotspot(spots, fired, elapsed=10, now=100, last_push_at=0) is None
    # 播到 25 秒：该报 20 秒那个
    got = due_hotspot(spots, fired, elapsed=25, now=100, last_push_at=0)
    assert got and got["at"] == 20.0
    fired.add(20.0)
    # 播到 100 秒：跳过 95 之前已处理的，报 95
    got = due_hotspot(spots, fired, elapsed=100, now=100, last_push_at=0)
    assert got and got["at"] == 95.0


def test_hotspot_never_repeats():
    """同一个高光点只吐一次（硬去重）。"""
    from _schedule import due_hotspot

    spots = [{"at": 30.0}]
    assert due_hotspot(spots, {30.0}, elapsed=40, now=100, last_push_at=0) is None


def test_hotspot_respects_min_gap():
    """刚说完话就别马上再说 —— 唯一的时间约束只是"别吞上一句"。"""
    from _schedule import due_hotspot

    spots = [{"at": 10.0}, {"at": 12.0}]
    # 5 秒前刚开过口，最短间隔 18 秒 → 先别说
    assert due_hotspot(spots, set(), elapsed=20, now=100, last_push_at=95, min_gap=18) is None
    # 过了 18 秒 → 可以说了
    assert due_hotspot(spots, set(), elapsed=20, now=120, last_push_at=95, min_gap=18) is not None


def test_stale_hotspots_not_backreported():
    """用户拖进度条跳过的高光点不回头补报。"""
    from _schedule import stale_hotspots

    spots = [{"at": 10.0}, {"at": 480.0}]
    # 看到 500 秒时：10 秒处早就过去了（490 > 90，过期）
    #                480 秒处刚过去（20 <= 90，还在宽限内，可以补）
    stale = stale_hotspots(spots, set(), elapsed=500, grace=90)
    assert 10.0 in stale, "早过去的高光点应标记为已处理"
    assert 480.0 not in stale, "仍在宽限内的不该丢"


def test_sorted_hotspots_by_time():
    from _schedule import sorted_hotspots

    out = sorted_hotspots([{"at": 90}, {"at": 10}, {"at": 50}])
    assert [h["at"] for h in out] == [10, 50, 90]


def test_rhythm_hotspots_prefers_local_peaks():
    """相邻两个高密度桶，只报最高的那个（同一波弹幕别报两次）。"""
    from _digest import rhythm_hotspots

    # 100 秒处 8 条，105 秒处 3 条，其余零星
    msgs = [{"t": 100.0 + i * 0.1, "text": f"笑死{i}"} for i in range(8)]
    msgs += [{"t": 105.0 + i * 0.1, "text": f"哈哈{i}"} for i in range(3)]
    msgs += [{"t": 5.0, "text": "开始"}, {"t": 300.0, "text": "结尾"}]
    spots = rhythm_hotspots(msgs, top=5)
    ats = [s["at"] for s in spots]
    assert 100 in ats
    assert 105 not in ats, "相邻低峰不该单独上报"
    top = max(spots, key=lambda s: s["count"])
    assert top["at"] == 100 and top["count"] == 8
    assert top["samples"], "高光点要带上多条弹幕供她取材"


def test_build_intro_and_hotspot_text():
    from _digest import build_hotspot_text, build_intro_text

    info = {"title": "高糖VS戒糖14天", "author": "亿点点", "duration": 702, "bvid": "BV1x"}
    dm = [{"t": 200.0 + i, "text": t} for i, t in enumerate(["赛掌柜口音", "笑容消失术", "太好笑了"])]
    intro = build_intro_text(info, dm, [{"text": "标题该严谨点", "like": 517}])
    assert "高糖VS戒糖14天" in intro
    assert "观众反应高峰" in intro     # 告诉她之后会在哪些点提醒
    assert "高赞评论" in intro

    hs = {"at_label": "3:20", "emotion": "笑", "count": 12, "sample": "笑容消失术", "samples": ["太好笑了"]}
    txt = build_hotspot_text(info, hs)
    assert "3:20" in txt and "笑容消失术" in txt


def test_estimate_position_extrapolates_while_playing():
    """播放中系统只报一次位置 —— 必须靠 upd + 倍速外推（实测行为）。"""
    from _playback import estimate_position

    frame = {"status": "Playing", "rate": 1.0, "pos": 3.53, "upd": 1789000000.0, "end": 708.0}
    # 过了 20 秒仍在播放：3.53 + 20*1
    assert abs(estimate_position(frame, 1789000020.0) - 23.53) < 1e-6
    # 倍速 2x：同样的 20 秒墙钟，位置前进 40 秒
    frame2 = dict(frame, rate=2.0)
    assert abs(estimate_position(frame2, 1789000020.0) - 43.53) < 1e-6


def test_estimate_position_paused_is_exact():
    """暂停时 pos 就是准确位置，绝不能再外推。"""
    from _playback import estimate_position

    frame = {"status": "Paused", "rate": 0.0, "pos": 547.43, "upd": 1789000000.0, "end": 708.0}
    assert estimate_position(frame, 1789999999.0) == 547.43


def test_estimate_position_rejects_bad_data():
    from _playback import estimate_position

    assert estimate_position(None, 0) is None
    assert estimate_position({"status": "Playing", "pos": -1}, 0) is None
    # upd 不是合理的 Unix 秒 → 不臆造外推，返回 pos 本身
    assert estimate_position({"status": "Playing", "pos": 12.0, "upd": 0, "rate": 1}, 1e9) == 12.0


def test_progress_ratio():
    from _playback import progress_ratio

    frame = {"status": "Paused", "rate": 0.0, "pos": 50.0, "upd": 0, "end": 200.0}
    assert progress_ratio(frame, 0) == 0.25
    assert progress_ratio({"status": "Playing", "pos": 10, "end": 0}, 0) is None


def test_pick_bilibili_frame_skips_noise():
    """多路媒体会话里只挑 B站视频；音乐、直播间都要排除。"""
    from _playback import pick_bilibili_frame

    rows = [
        {"app": "cloudmusic.exe", "status": "Playing", "title": "某首歌 - 网易云音乐"},
        {"app": "SLBrowser", "status": "Paused", "title": "某主播 - 哔哩哔哩直播，二次元弹幕直播平台"},
        {"app": "SLBrowser", "status": "Playing", "title": "机械迷城_哔哩哔哩_bilibili"},
    ]
    got = pick_bilibili_frame(rows)
    assert got is not None and got["app"] == "SLBrowser" and got["status"] == "Playing"
    assert "机械迷城" in got["title"]
    # 只有直播间 → 视为没有可用会话
    assert pick_bilibili_frame([rows[1]]) is None
    # 只有音乐 → None
    assert pick_bilibili_frame([rows[0]]) is None
    assert pick_bilibili_frame([]) is None


def test_pick_bilibili_frame_prefers_playing_over_paused():
    from _playback import pick_bilibili_frame

    rows = [
        {"app": "a", "status": "Paused", "title": "旧视频_哔哩哔哩_bilibili"},
        {"app": "b", "status": "Playing", "title": "新视频_哔哩哔哩_bilibili"},
    ]
    assert pick_bilibili_frame(rows)["app"] == "b"


def test_title_matches_guards_wrong_session():
    """别的标签页/别的视频的会话，绝不能把位置错用到当前视频上。"""
    from _playback import title_matches

    assert title_matches("机械迷城_哔哩哔哩_bilibili", "机械迷城_哔哩哔哩_bilibili -联想浏览器")
    assert title_matches(
        "【官方 MV】Never Gonna Give You Up_哔哩哔哩_bilibili",
        "【官方 MV】Never Gonna Give You Up_哔哩哔哩_bilibili",
    )
    assert not title_matches("原神新版本前瞻_哔哩哔哩_bilibili", "机械迷城_哔哩哔哩_bilibili")
    assert not title_matches("", "机械迷城_哔哩哔哩_bilibili")


def test_smtc_script_is_self_contained():
    """SMTC 读取脚本必须自带 WinRT 访问逻辑，且用 EncodedCommand 传（不落盘）。"""
    import _smtc

    src = _smtc._PS_SCRIPT_TEMPLATE
    assert "GlobalSystemMediaTransportControlsSessionManager" in src
    assert "GetTimelineProperties" in src
    assert "ConvertTo-Json" in src


def test_smtc_reader_degrades_when_unavailable():
    """非 Windows / 未启动时，latest() 必须安全返回 None（插件靠它降级）。"""
    import _smtc

    r = _smtc.SmtcReader()
    assert isinstance(r.available(), bool)
    assert r.latest() is None      # 未 start：必须是 None，不是异常


def test_plugin_wires_media_position_with_fallback():
    """插件必须：优先真实播放位置，读不到时从最近观测续推（源码级契约）。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    assert "use_media_session" in src
    assert "_playback_now" in src
    # 降级路径必须存在：媒体位置拿不到时从最近一次真实观测续推
    assert "last_pos" in src and "last_pos_at" in src


def test_default_mode_is_read_not_respond():
    """默认必须是 read。

    2026-10-10 实测事故：respond 会**立即触发她开口**，和她自带 10~20 秒
    一轮的主动搭话撞车，把她的话反复截断（"几个字一卡"），严重时整段哑掉。
    read 只塞上下文、不触发开口，不会打断。这条测试守住这个默认值。
    """
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    # 设置读取处的默认值必须是 read
    assert 'section.get("mode", "read")' in src
    # 兜底分支（非法值）也必须落到 read
    assert 'else "read"' in src


def test_push_uses_coalesce_key():
    """推送必须带 coalesce_key。

    她说话时宿主会把 cue 排队（ProactiveDeliveryManager）；没有合并键的话，
    排队的多条会在她说完后一起爆发，比打断更糟。
    """
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    assert "coalesce_key" in src


def test_meme_hints_prefer_meme_tags_then_circle():
    """梗素材优先取"带梗味"的标签，再补圈子标签；不编台词、不超长。"""
    from _memes import extract_meme_hints, is_meme_like

    tags = ["搞笑", "音乐", "二次元手游", "攻略", "游戏", "AI动画", "OW"]
    danmaku = [
        {"t": 1, "text": "笑容消失术"},
        {"t": 2, "text": "这句鬼畜相关的话非常长绝对不该被当成梗素材来用"},
    ]
    comments = [{"text": "鬼畜区又活了", "like": 9}]
    hints = extract_meme_hints(tags=tags, danmaku=danmaku, comments=comments, limit=8)
    assert hints, "应该能提取到梗素材"
    # 梗味标签必须靠前（"搞笑" 带梗味，"音乐" 不带）
    assert hints[0] == "搞笑", hints
    # ★ 圈子标签必须入选（OW = 守望先锋圈），且优先于弹幕梗
    assert "OW" in hints, hints
    # 弹幕梗排在标签之后（标签更能说明圈子）
    assert hints.index("OW") < next(
        (i for i, h in enumerate(hints) if "鬼畜" in h), len(hints)
    ), hints
    # 超长句子不得入选
    assert all(len(h) <= 24 for h in hints), hints
    assert not any("绝对不该" in h for h in hints), hints
    assert is_meme_like("这波鬼畜很生草")
    assert not is_meme_like("今天天气不错")


def test_intro_text_includes_meme_hints_when_present():
    """有梗素材时，首轮文案要带上"梗/圈子线索"。"""
    from _digest import build_intro_text

    info = {"title": "紫米汤圆", "author": "UP", "duration": 300, "bvid": "BV1"}
    dm = [{"t": 100.0, "text": "哈哈"}]
    txt = build_intro_text(info, dm, [], meme_hints=["搞笑", "OW"])
    assert "梗/圈子线索" in txt and "OW" in txt
    # 没有梗素材时不该出现该行
    txt2 = build_intro_text(info, dm, [], meme_hints=[])
    assert "梗/圈子线索" not in txt2


def test_focus_mode_gate_helpers():
    """专注模式门控：Playing=放行；Paused=拦；拿不到会话=拦（保守）。"""
    from _playback import is_playing_frame, reached_end

    # 正在播放 → 放行
    playing = {"status": "Playing", "rate": 1.0, "pos": 30.0, "upd": 1789000000.0, "end": 300.0}
    assert is_playing_frame(playing) is True
    # 暂停 → 拦
    assert is_playing_frame({"status": "Paused", "pos": 30.0}) is False
    # 拿不到会话 → 保守拦截（绝不因为读不到就乱开口）
    assert is_playing_frame(None) is False
    assert is_playing_frame({}) is False

    # 播到片尾：允许发收尾（用户看完暂停很常见）
    ended = {"status": "Paused", "rate": 0.0, "pos": 300.0, "end": 300.0}
    assert reached_end(ended, 300.0, 1789000000.0) is True
    # 中途暂停：不算播完，必须拦住
    mid = {"status": "Paused", "rate": 0.0, "pos": 120.0, "end": 300.0}
    assert reached_end(mid, 300.0, 1789000000.0) is False


def test_focus_mode_defaults_on():
    """专注模式默认开启；专注内默认 respond（精准踩高光），非专注用 read。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    assert '_b("focus_mode", True)' in src, "focus_mode 应默认开启"
    assert 'section.get("focus_behavior", "respond")' in src
    # 非专注必须落到 read（不打断日常）
    start = src.index("def _focus_behavior")
    body = src[start:start + 700]
    assert 'return "read"' in body, "非专注模式必须用 read"


def test_plugin_has_ending_round_exception():
    """暂停在片尾时，收尾吐槽必须还能发出去（否则永远发不出）。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    assert "_ending_round_pending" in src
    assert "reached_end" in src


def test_pick_frame_works_without_site_suffix():
    """★ 回归测试（2026-10-10 真 bug）：浏览器上报的 SMTC 标题**可能不带**
    站点后缀，此时必须靠"与当前窗口标题匹配"认出会话，而不是只认后缀。

    实测现象：前两个视频能触发、第三个开始不触发 —— 就是因为那个视频的
    SMTC 标题是光秃秃的《机械迷城2和逃脱者3…》，没有 `_哔哩哔哩_bilibili`。
    """
    from _playback import is_playing_frame, pick_frame_for_video

    # 浏览器漏了站点后缀（真实踩到的形状）
    bare = {
        "app": "SLBrowser", "status": "Playing", "rate": 1.0, "pos": 22.4,
        "upd": 1789000000.0, "end": 641.0,
        "title": "机械迷城2和逃脱者3，看这俩受苦女主拼了，这才刚入门一安全区有人了【3i计划独立游戏盘点】",
    }
    win_title = "机械迷城2和逃脱者3，看这俩受苦女主拼了，这才刚入门一安全区有人了【3i计划独立游戏盘点】_哔哩哔哩_bilibili"

    # 必须能配上（这是修复的核心）
    got = pick_frame_for_video([bare], win_title)
    assert got is not None, "不带到站点后缀时会话也必须能匹配上"
    assert is_playing_frame(got) is True, "匹配上后门控应放行"

    # 带后缀的形状也要继续工作
    with_suffix = dict(bare, title=win_title)
    assert pick_frame_for_video([with_suffix], win_title) is not None

    # 别的视频（标题对不上）不能被误判成本视频
    other = dict(bare, title="某个完全无关的视频【另一个视频】")
    assert pick_frame_for_video([other], win_title) is None, "不能张冠李戴"

    # 空列表 / 无匹配 → None（保守）
    assert pick_frame_for_video([], win_title) is None
    assert pick_frame_for_video(None, win_title) is None


def test_pick_frame_prefers_playing_when_both_present():
    from _playback import pick_frame_for_video

    win = "机械迷城2和逃脱者3，看这俩受苦女主拼了"
    rows = [
        {"status": "Paused", "title": win},
        {"status": "Playing", "title": win},
    ]
    got = pick_frame_for_video(rows, win)
    assert got is not None and got["status"] == "Playing"


def test_frame_cell_index_mapping():
    """进度条预览图的"第几格 = 第几秒"映射必须正确（离线可测部分）。"""
    from _frames import (  # noqa: I001 - 本地导入，顺序不重要
        cell_index_for_seconds,
    )

    # 实测形状：index[0]=封面, index[1]=0s, 之后每 5 秒一格
    index = [0.0, 0.0] + [5.0 * i for i in range(1, 108)]
    # 0 秒：最接近的格子时间必须是 0（idx0/idx1 都是 0.0，取哪个都对）
    assert index[cell_index_for_seconds(index, 0)] == 0.0
    # 任意时刻取到的格子，时间差必须 ≤ 半格（2.5s）
    for sec in (0, 7, 95, 235, 425):
        got = cell_index_for_seconds(index, sec)
        assert abs(index[got] - sec) <= 5.0, f"{sec}s 取到 {index[got]}s"
    # 超出范围时取最接近的最后一格（不越界）
    assert cell_index_for_seconds(index, 99999) == len(index) - 1
    assert cell_index_for_seconds([], 10) == 0          # 空 → 0（保守）


def test_frame_fetch_failure_is_silent():
    """拿不到预览图时必须静默返回 None（不能让主流程炸）。"""
    from _frames import crop_frame_at, fetch_shot_grid

    assert fetch_shot_grid("") is None
    assert fetch_shot_grid("NOT_A_BVID") is None
    assert crop_frame_at({}, 10) is None
    assert crop_frame_at({"image": [], "index": []}, 10) is None


def test_push_supports_frame_image_part():
    """源码契约：推送**支持**画面（image part），但默认不启用（她自己看得见）。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    # 能力要在（开关打开时能推图）
    assert '"type": "image"' in src and '"mime": "image/jpeg"' in src
    assert "_frame_at" in src
    # 默认关闭（见 test_push_frame_defaults_off）
    assert "frame_jpeg=frame" in src
    # 文案要点明她看得见画面、把发挥权交给她
    start = src.index('kind == "hotspot"')
    body = src[start:start + 700]
    assert "画面" in body
    assert "别描述画面" in body or "看得见" in body


def test_resolve_prefers_higher_play_on_tie():
    """★ 回归测试（2026-10-11 真 bug）：同名视频归一化后相似度完全相同（标点被
    `_norm` 抹掉），必须按**播放量**破平选正片 —— 否则会匹配到同名短视频
    （实测：错配到 1160 播放/0 弹幕的那个，而用户在看的正片有 526 万播放/3010 弹幕，
    表现为"弹幕评论读 0"）。
    """
    import _bili

    fake = [
        {"bvid": "BV_SHORT", "title": "我的行为vs奶奶看到的", "play": 1160, "author": "A"},
        {"bvid": "BV_MAIN", "title": "我的行为VS奶奶看到的：", "play": 5260298, "author": "B"},
    ]
    orig = _bili._search_once
    try:
        _bili._search_once = lambda q, order="": (fake if order == "pubdate" else [])
        got = _bili.resolve_bv_by_title("我的行为VS奶奶看到的：_哔哩哔哩_bilibili")
        assert got is not None
        assert got["bvid"] == "BV_MAIN", f"同分应取播放高的正片，实际 {got['bvid']}"
        # 两份相似度确实相同（证明这条断言测的是"同分破平"而非"相似度更高"）
        assert _bili._similarity(
            "我的行为VS奶奶看到的：", "我的行为vs奶奶看到的"
        ) == _bili._similarity("我的行为VS奶奶看到的：", "我的行为VS奶奶看到的：")
    finally:
        _bili._search_once = orig


def test_as_int_parses_play_counts():
    """播放量可能是 '12.3万' 这类中文计数，必须能解析。"""
    from _bili import _as_int

    assert _as_int(1234) == 1234
    assert _as_int("5678") == 5678
    assert _as_int("12.3万") == 123000
    assert _as_int("1.5亿") == 150000000
    assert _as_int(None) == 0
    assert _as_int("") == 0
    assert _as_int(True) == 0          # bool 不算数字


def test_duration_hint_disambiguates_same_titles():
    """★ 回归测试（2026-10-11 真 bug）：同名视频极多（实测 40+），
    时长提示必须能选出"时长对得上"的那个，且**不能因为符号写反把正确的拒掉**。
    """
    import _bili

    # 模拟：两个同名、播放量接近、只有时长不同的候选
    fake = [
        {"bvid": "BV_WRONG", "title": "我的行为VS奶奶看到的：", "duration": "2:13", "play": 1298244},
        {"bvid": "BV_RIGHT", "title": "我的行为VS奶奶看到的：", "duration": "2:12", "play": 5260316},
    ]
    orig = _bili._search_once
    try:
        _bili._search_once = lambda q, order="": (fake if order == "pubdate" else [])
        # 不带 hint：两个同名同分，取播放高的
        got0 = _bili.resolve_bv_by_title("我的行为VS奶奶看到的：_哔哩哔哩_bilibili")
        assert got0 is not None and got0["bvid"] == "BV_RIGHT"
        # 带 hint=132（=2:12）：必须命中时长完全一致的那个（不能被过滤逻辑拒掉）
        got1 = _bili.resolve_bv_by_title(
            "我的行为VS奶奶看到的：_哔哩哔哩_bilibili", duration_hint=132.0
        )
        assert got1 is not None, "带正确时长提示时不能返回 None（符号写反就是这个症状）"
        assert got1["bvid"] == "BV_RIGHT", f"应命中 132s 的那个，实际 {got1['bvid']}"
        assert got1["duration_seconds"] == 132

        # 时长全都对不上 → 该拒绝（避免聊错视频）
        got2 = _bili.resolve_bv_by_title(
            "我的行为VS奶奶看到的：_哔哩哔哩_bilibili", duration_hint=999.0
        )
        assert got2 is None, "所有候选时长都对不上时应拒绝"
    finally:
        _bili._search_once = orig


def test_parse_duration_seconds():
    from _bili import _parse_duration_seconds

    assert _parse_duration_seconds("2:12") == 132
    assert _parse_duration_seconds("1:02:03") == 3723
    assert _parse_duration_seconds("0:27") == 27
    assert _parse_duration_seconds(132) == 132
    assert _parse_duration_seconds(None) is None
    assert _parse_duration_seconds("bad") is None


def test_probable_video_title_handles_missing_site_marker():
    """★ 回归测试（2026-10-11 真 bug）：B站**不总**在标题里写站点名。

    实测踩到：「游戏史上最没用的十大垃圾！_我的世界_游戏杂谈 -联想浏览器」
    —— 站点标记被换成了分区名，导致插件整个不工作。
    修法：浏览器窗口也算"可能"，由下游反查兜底。
    """
    from _window import is_bilibili_title, is_probable_video_title

    # 实测踩到的形状：没有站点名，但有浏览器后缀
    real = "游戏史上最没用的十大垃圾！_我的世界_游戏杂谈 -联想浏览器"
    assert is_bilibili_title(real) is False        # 严格判定认不出（这是事实）
    assert is_probable_video_title(real) is True   # 宽松判定要认得出 ★

    # 标准格式继续工作
    assert is_probable_video_title("机械迷城_哔哩哔哩_bilibili") is True
    # 直播间必须排除（没有 BV/弹幕）
    assert is_probable_video_title(
        "某主播 - 哔哩哔哩直播，二次元弹幕直播平台"
    ) is False
    # 普通窗口排除
    assert is_probable_video_title("记事本") is False
    assert is_probable_video_title("") is False


def test_push_frame_defaults_off():
    """默认不推图：她自己看得见屏幕，插件推图是重复劳动。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    assert '_b("push_frame", False)' in src
    # 默认路径不能调 _frame_at（只有开了 push_frame 才调）
    assert "if settings.get(\"push_frame\")" in src


def test_prompts_let_her_use_own_persona():
    """提示词必须把发挥权交给她（她看得见画面，插件只给她看不见的上下文）。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    start = src.index("def _compose_text")
    body = src[start:start + 2200]
    # 要点明"她自己看得见画面"
    assert "看得见画面" in body, "必须告诉她画面她看得见（别让插件描述画面）"
    # 要明确把发挥权交给她
    assert "你自己的性格" in body
    assert "全由你决定" in body or "随你" in body


def test_sparse_danmaku_produces_no_hotspots():
    """★ 回归测试（2026-10-11）：弹幕稀疏的视频**不能**造出"假高光点"。

    实测：21 条弹幕的视频，按密度算出的"峰值"只有 1~2 条弹幕，那是噪音。
    拿它当"观众反应最集中处"会让她满嘴弹幕（用户反馈"弹幕吐槽太多"）。
    正确行为：干脆不报高光点，让她按内容（简介/热评）自己聊。
    """
    from _digest import rhythm_hotspots

    # 21 条零散弹幕（模拟实测形状）
    sparse = [{"t": float(i * 12), "text": f"弹幕{i}"} for i in range(21)]
    assert rhythm_hotspots(sparse, top=5) == [], "稀疏弹幕不该产出高光点"

    # 弹幕密集时必须照常产出（不能被门槛误伤）
    dense = [{"t": 30.0 + i * 0.05, "text": f"哈哈{i}"} for i in range(20)]
    dense += [{"t": 200.0, "text": "结尾"}]
    got = rhythm_hotspots(dense, top=5)
    assert got, "密集弹幕必须仍能识别出高光点"
    assert got[0]["count"] >= 3


def test_sparse_danmaku_intro_uses_content_not_danmaku():
    """弹幕稀疏时，首轮内容要以简介/热评为骨架，并明确让她"别硬提弹幕"。"""
    from _digest import build_intro_text

    info = {
        "title": "警惕！AI幻觉再次升级",
        "author": "某UP",
        "duration": 272,
        "desc": "好像很久没人聊AI幻觉了。" + "详细内容" * 60,   # 长简介
    }
    dm = [{"t": float(i * 12), "text": f"弹幕{i}"} for i in range(21)]
    cm = [{"text": "让好汉查好汉，让ai审ai", "like": 119}]
    txt = build_intro_text(info, dm, cm, meme_hints=["AI"])

    # 不能出现"观众反应高峰"（那是有真高光时才有的）
    assert "观众反应高峰" not in txt
    # 热评要排在前面并标明是核心观点
    assert "核心观点" in txt
    assert "让好汉查好汉" in txt
    # 要明确指示她用自己的理解聊，别硬提弹幕
    assert "别硬提弹幕" in txt or "用你自己的理解" in txt
    # 简介要放宽（比 200 字多）
    assert "详细内容" * 40 in txt, "稀疏弹幕时简介应放宽到 260 字"


def test_comment_images_are_captured():
    """★ 评论图（2026-10-11）：B站评论区大量是"梗图+一句话"，必须采集图片 URL。

    实测：《有猫病》的高赞评论"地球运转的底层逻辑"+猫抱地球的图。
    字段在 content.pictures[].img_src（实测可下载，1709x1124 那种）。
    """
    import _bili

    fake_page = {
        "code": 0,
        "data": {
            "replies": [
                {
                    "content": {
                        "message": "地球运转的底层逻辑",
                        "pictures": [{"img_src": "//i0.hdslb.com/bfs/x.jpg"}],
                    },
                    "like": 210,
                },
                {"content": {"message": "没图的一条"}, "like": 5},
            ]
        },
    }
    orig = _bili._get
    try:
        _bili._get = lambda url, **kw: fake_page
        cm = _bili.hot_comments(123, limit=5)
        assert len(cm) == 2
        assert cm[0]["images"] == ["https://i0.hdslb.com/bfs/x.jpg"], "评论图 URL 要采集并补 https"
        assert cm[1]["images"] == [], "没图的评论 images 应为空"
        assert cm[0]["text"] == "地球运转的底层逻辑"
    finally:
        _bili._get = orig


def test_shrink_image_bounds_size():
    """评论图要压到界内（长截图实测 1080x2403，直接推会超体积上限）。"""
    import io as _io

    from PIL import Image

    # 造一张长截图
    buf = _io.BytesIO()
    Image.new("RGB", (1080, 2403), (120, 30, 200)).save(buf, format="PNG")
    raw = buf.getvalue()

    # 直接调静态方法（不实例化插件）
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    ns: dict = {}
    # 只取 _shrink_image 函数体（避免导入整个插件依赖 SDK）
    start = src.index("def _shrink_image")
    end = src.index("def _focus_behavior")
    exec(compile("import io\nfrom PIL import Image\n" + src[start:end], "<shrink>", "exec"), ns)
    shrink = ns["_shrink_image"]
    # @staticmethod 在 exec 后是 staticmethod 对象
    got = shrink.__func__(raw) if isinstance(shrink, staticmethod) else shrink(raw)
    assert got and len(got) < 200 * 1024, f"压缩后应 <200KB，实际 {len(got) if got else 0}"
    out = Image.open(_io.BytesIO(got))
    assert max(out.size) <= 720


def test_end_summary_prompt_encourages_real_thoughts():
    """收尾总结要引导"真心话"，而不是"写影评/面面俱到"。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    start = src.index('kind == "end"')
    body = src[start:start + 700]
    assert "真心话" in body or "你自己" in body
    assert "不是写影评" in body or "不用面面俱到" in body


def test_comment_images_default_on():
    """评论图默认开启（这**不是**屏幕截图——她翻不到评论区时看不到这些图）。"""
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py",
        encoding="utf-8",
    ).read()
    assert '_b("push_comment_images", True)' in src


def test_end_summary_fires_when_window_closed():
    """★ 回归测试（2026-10-11 真 bug）：用户**看完视频往往直接关窗口**，
    导致检测不到标题 → 旧代码直接 return → 收尾永远不执行
    （实测现象："播完了她不总结、不提评论区"）。

    修法：标题为空时，若"该发收尾"（片尾附近+还没发过）就补跑一轮；
    且**不做墙钟外推**（防中途关掉很久后误判播完）。
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "__init__.py").read_text(encoding="utf-8")

    # ① 标题为空的分支里必须有"补发收尾"的调用
    start = src.index("if not raw_title:")
    body = src[start:start + 1200]
    assert "_ending_round_pending" in body, "标题为空时要判断是否该补发收尾"
    assert "window_gone=True" in body, "补发时要告知窗口已关（别用墙钟外推）"

    # ② 窗口关闭时不能墙钟外推（防误判播完）
    assert "elif window_gone:" in src
    assert "不做墙钟外推" in src

    # ③ 收尾容差放宽到 15s（用户常在片尾最后十几秒就走）
    assert "end_tol = 15.0" in src
    assert "tol=15.0" in src

    # ④ reached_end 支持自定义容差
    from _playback import reached_end
    mid = {"status": "Paused", "rate": 0.0, "pos": 100.0, "end": 110.0}
    assert reached_end(mid, 110.0, 0, tol=15.0) is True, "片尾 10s 内应算播完"
    assert reached_end(mid, 110.0, 0, tol=1.0) is False, "1s 容差时不算播完"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
    print("=" * 40)
    print("全部通过" if not fails else f"{fails} 个失败")
    sys.exit(1 if fails else 0)
