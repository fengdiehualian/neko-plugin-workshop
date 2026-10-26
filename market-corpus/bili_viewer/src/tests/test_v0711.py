"""v0.7.11/0.7.12 新增能力测试：声音通道（_receive_ext_audio / bili_listen_audio）、
网络层自动重试（_open_with_retry）、AUTH_FAILED 自愈（_wrap_auth + 60s 冷却）、
三扩展包标识对称（manifest 0.2.6 + EXT_SOURCE + 新增 offscreen 文件）。

注意：测试全程不触网（urlopen 由 monkeypatch 接管；ASR 模型懒加载不触发）。
"""

from __future__ import annotations

import base64
import json
import os
import time

import pytest


def _wav_base64(seconds: float = 1.0, rate: int = 16000) -> str:
    """生成一个 16kHz/16bit/mono 的静音 wav（带短促音）的 base64 dataURL。"""
    n = int(seconds * rate)
    data_len = n * 2
    buf = bytearray(44 + data_len)
    buf[0:4] = b"RIFF"
    buf[8:12] = b"WAVE"
    buf[12:16] = b"fmt "
    buf[36:40] = b"data"
    import struct as _s
    _s.pack_into("<IHHIIHH", buf, 16, 16, 1, 1, rate, rate * 2, 2, 16)
    _s.pack_into("<I", buf, 4, 36 + data_len)
    _s.pack_into("<I", buf, 40, data_len)
    for i in range(n):
        _s.pack_into("<h", buf, 44 + i * 2, 0)  # 静音
    return "data:audio/wav;base64," + base64.b64encode(bytes(buf)).decode()


def _pkg():
    # 双模块教训（v0.7.10）：patch 包模块而非 importlib 副本，才能影响类方法全局
    import plugin.plugins.bili_viewer as pkg

    return pkg


def _make_instance(pkg) -> object:
    return pkg.BiliViewerPlugin.__new__(pkg.BiliViewerPlugin)


# ══════════════ 扩展声音接收 ══════════════


def test_receive_ext_audio_valid_saves_and_kicks_asr(tmp_path, monkeypatch):
    pkg = _pkg()
    inst = _make_instance(pkg)
    monkeypatch.setattr(pkg, "_AUDIO_PATH", os.path.join(str(tmp_path), "seg.wav"))
    kicked = []

    def fake_kick():
        kicked.append(True)

    monkeypatch.setattr(inst, "_kick_asr", fake_kick)
    body = {"audio": _wav_base64(1.0), "sample_rate": 16000, "seq": 7, "at": 1234567890}
    res = inst._receive_ext_audio(body)
    assert res.get("ok") is True
    assert os.path.isfile(pkg._AUDIO_PATH)
    seg = inst._ext_audio()
    assert seg is not None and seg["seq"] == 7
    assert kicked == [True]


def test_receive_ext_audio_rejects_bad_payloads(tmp_path, monkeypatch):
    pkg = _pkg()
    inst = _make_instance(pkg)
    monkeypatch.setattr(pkg, "_AUDIO_PATH", os.path.join(str(tmp_path), "seg.wav"))
    assert inst._receive_ext_audio({}).get("ok") is False
    assert inst._receive_ext_audio({"audio": "not-a-dataurl"}).get("ok") is False
    # 超体积拒绝（构造 5MB base64 静音）
    big = "data:audio/wav;base64," + base64.b64encode(b"\x00" * (5 * 1024 * 1024)).decode()
    assert inst._receive_ext_audio({"audio": big}).get("ok") is False
    assert not os.path.exists(pkg._AUDIO_PATH)


def test_receive_ext_audio_real_kick_starts_asr_worker(tmp_path, monkeypatch):
    """回归：_asr_lock 存储属性与方法同名会导致首次 _kick_asr 抛
    "'method' object does not support the context manager protocol"。
    此处不替换 _kick_asr，真实走一遍落盘 → 锁 → worker 启动，并验证二次推送不再抛错。"""
    pkg = _pkg()
    inst = _make_instance(pkg)
    monkeypatch.setattr(pkg, "_AUDIO_PATH", os.path.join(str(tmp_path), "seg_real.wav"))
    monkeypatch.setattr(inst, "_whisper_model", lambda: None)  # 不拉起真实 whisper
    body = {"audio": _wav_base64(1.0), "sample_rate": 16000, "seq": 1, "at": 1234567890}
    res = inst._receive_ext_audio(body)
    assert res.get("ok") is True
    assert os.path.isfile(pkg._AUDIO_PATH)
    worker = getattr(inst, "_asr_worker_thread", None)
    assert worker is not None and worker.is_alive()
    # 第二次推送：_asr_lock 可复用，不应抛异常
    res2 = inst._receive_ext_audio({"audio": _wav_base64(1.0), "sample_rate": 16000, "seq": 2, "at": 1234567891})
    assert res2.get("ok") is True
    # 收尾：停 worker
    inst._asr_worker_stop = True
    inst._asr_worker_evt.set()
    worker.join(timeout=2)


# ══════════════ bili_listen_audio 工具 ══════════════


def test_listen_audio_returns_fresh_asr_text():
    pkg = _pkg()
    inst = _make_instance(pkg)
    inst._ext_asr_data = {"text": "主播说今天比赛很精彩", "lang": "zh", "at": 9999999999.0, "seq": 3}
    import asyncio

    res = asyncio.run(inst.bili_listen_audio())
    assert res["is_error"] is False
    assert "今天比赛很精彩" in res["output"]


def test_listen_audio_empty_when_stale():
    pkg = _pkg()
    inst = _make_instance(pkg)
    inst._ext_asr_data = {"text": "旧内容", "lang": "zh", "at": 1.0, "seq": 3}  # 已过期
    import asyncio

    res = asyncio.run(inst.bili_listen_audio())
    assert res["is_error"] is True
    assert res["error"] == "NO_AUDIO"


# ══════════════ 网络层自动重试（_open_with_retry） ══════════════


def test_http_get_retries_transient_network_error(monkeypatch):
    import plugin.plugins.bili_viewer.bili as bili

    calls = {"n": 0}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"code":0,"data":{"ok":1}}'

    def fake_urlopen(req, timeout=12.0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("boom")
        return _FakeResp()

    import urllib.error

    monkeypatch.setattr(bili.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(bili, "_cookie", lambda account="main": "")
    data = bili._http_get("https://api.bilibili.com/x/test", {"a": 1})
    assert data["code"] == 0
    assert calls["n"] == 3  # 2 次失败重试 + 1 次成功


def test_http_post_jitter_retries_and_passes_business_errors(monkeypatch):
    import plugin.plugins.bili_viewer.bili as bili

    calls = {"n": 0}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"code":0,"data":{}}'

    def fake_urlopen(req, timeout=12.0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("boom")
        return _FakeResp()

    import urllib.error

    monkeypatch.setattr(bili.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(bili, "_cookie", lambda account="main": "")
    data = bili._http_post("https://api.bilibili.com/x/post", {"a": 1})
    assert data["code"] == 0
    assert calls["n"] == 3

    # 业务错误不重试
    calls["n"] = 0

    def fake_biz(req, timeout=12.0):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 400, "bad request", {}, None)

    monkeypatch.setattr(bili.urllib.request, "urlopen", fake_biz)
    with pytest.raises(bili.BiliError) as ei:
        bili._http_post("https://api.bilibili.com/x/post", {"a": 1})
    assert ei.value.code == "HTTP_ERROR"
    assert calls["n"] == 1  # 4xx 不重试


# ══════════════ AUTH_FAILED 自愈 ══════════════


def test_wrap_auth_triggers_rebind_on_auth_failed():
    pkg = _pkg()
    inst = _make_instance(pkg)
    rebound = []

    def fake_bg_bind():
        rebound.append(True)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(inst, "_bg_bind", fake_bg_bind)
    exc = pkg.bili.BiliError("登录态失效", "AUTH_FAILED")
    res = inst._wrap_auth(exc, "发送弹幕")
    monkeypatch.undo()
    assert res["is_error"] is True
    assert res["error"] == "AUTH_FAILED"
    assert "自动尝试重新接入" in res["output"]
    assert rebound == [True]


def test_wrap_auth_passthrough_other_errors():
    pkg = _pkg()
    inst = _make_instance(pkg)
    exc = pkg.bili.BiliError("风控", "RISK_CONTROL")
    res = inst._wrap_auth(exc, "发送弹幕")
    assert res["is_error"] is True
    assert res["error"] == "RISK_CONTROL"
    assert "自动尝试" not in res["output"]


def test_wrap_auth_rebind_cooldown_60s():
    """v0.7.12：同实例 60s 内重复 AUTH_FAILED 不反复触发后台重绑。"""
    pkg = _pkg()
    inst = _make_instance(pkg)
    rebound = []

    def fake_bg_bind():
        rebound.append(True)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(inst, "_bg_bind", fake_bg_bind)
    exc = pkg.bili.BiliError("登录态失效", "AUTH_FAILED")
    inst._auth_rebind_at = 0.0
    inst._wrap_auth(exc, "发送弹幕")   # 首次：触发
    assert rebound == [True]
    inst._wrap_auth(exc, "发送弹幕")   # 冷却内：不再触发
    assert rebound == [True]
    inst._auth_rebind_at = time.time() - 61.0
    inst._wrap_auth(exc, "发送弹幕")   # 冷却过期：再次触发
    assert rebound == [True, True]
    monkeypatch.undo()


# ══════════════ 三扩展包标识对称（真实文件字节级） ══════════════

_EXT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "browser_extension"
)


def _read_ext(pkg_name: str, fname: str) -> str:
    with open(os.path.join(_EXT_ROOT, pkg_name, fname), "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("pkg_name", ["bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360"])
def test_ext_package_has_all_six_files(pkg_name):
    for f in ("manifest.json", "background.js", "popup.html", "popup.js", "offscreen.html", "offscreen.js"):
        assert os.path.isfile(os.path.join(_EXT_ROOT, pkg_name, f)), f"{pkg_name}/{f} 缺失"


@pytest.mark.parametrize("pkg_name", ["bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360"])
def test_ext_manifest_has_audio_permissions(pkg_name):
    manifest = json.loads(_read_ext(pkg_name, "manifest.json"))
    assert manifest["version"] == "0.2.25"
    assert "tabCapture" in manifest["permissions"]
    assert "offscreen" in manifest["permissions"]


@pytest.mark.parametrize(
    ("pkg_name", "source"),
    [
        ("bili-bridge-chrome", "bili-bridge-chrome-extension"),
        ("bili-bridge-edge", "bili-bridge-edge-extension"),
        ("bili-bridge-360", "bili-bridge-360-extension"),
    ],
)
def test_ext_extension_sources_distinct(pkg_name, source):
    bg = _read_ext(pkg_name, "background.js")
    assert f'const EXT_SOURCE = "{source}";' in bg
    # 同包内不得出现其他浏览器的 source
    for other in ("chrome-extension", "edge-extension", "360-extension"):
        if other != source:
            assert f'EXT_SOURCE = "{other}"' not in bg


def test_ext_offscreen_pushes_to_panel_audio_endpoint():
    js = _read_ext("bili-bridge-chrome", "offscreen.js")
    assert "/api/extension-audio" in js
    assert "127.0.0.1" in js
    html = _read_ext("bili-bridge-chrome", "offscreen.html")
    assert "offscreen.js" in html


def test_ext_background_has_audio_state_and_recovery():
    bg = _read_ext("bili-bridge-chrome", "background.js")
    assert "audio_capture_enabled" in bg
    assert "start-audio" in bg
    assert "stop-audio" in bg
    assert "ensureAudioSession" in bg


# ══════════════ v0.7.14：ASR 外部桥 + 声音状态回显 ══════════════


def test_ext_background_pushes_audio_state_all_packages():
    """三扩展包都要向插件推送声音捕获开关状态（/api/extension-audio-state）。"""
    for pkg_name in ("bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360"):
        bg = _read_ext(pkg_name, "background.js")
        assert "pushAudioState" in bg
        assert "/api/extension-audio-state" in bg
        assert "audioEnabled" in bg
        # v0.2.8 永久开启：启动恢复默认开（从未设置过=开）
        assert "!== false" in bg
        # v0.2.8 不在 B站 页不抛错（记住意图），切到 B站 自动启动
        assert "not-on-bilibili" not in bg
        assert "记住开关意图" in bg
        # 标签切换自动重绑音频捕获
        assert "startAudioCapture()" in bg
        popup = _read_ext(pkg_name, "popup.js")
        assert "!== false" in popup  # popup 状态兜底同样默认开


def test_ext_background_recovers_enabled_default_on():
    """扩展启动恢复逻辑：storage 未设置或值为 true → audioEnabled 为 true（永久开启）。"""
    # 恢复表达式 `saved[AUDIO_STATE_KEY] !== false`：undefined/true→开，仅 false→关
    for pkg_name in ("bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360"):
        bg = _read_ext(pkg_name, "background.js")
        assert "saved[AUDIO_STATE_KEY] !== false" in bg
        assert "if (audioEnabled) startAudioCapture()" in bg


def test_receive_ext_audio_state_records_enabled():
    pkg = _pkg()
    inst = _make_instance(pkg)
    res = inst._receive_ext_audio_state({"enabled": True, "at": 123.0})
    assert res["ok"] is True
    state = getattr(inst, "_ext_audio_state", None)
    assert state is not None and state["enabled"] is True and state["at"] == 123.0
    inst._receive_ext_audio_state({"enabled": False, "at": 456.0})
    assert getattr(inst, "_ext_audio_state", None)["enabled"] is False


def test_listen_audio_diagnostic_reason_capture_disabled():
    """声音开关未开启时：output 简短如实说明，result 给出链路诊断。"""
    import asyncio

    pkg = _pkg()
    inst = _make_instance(pkg)
    inst._receive_ext_audio_state({"enabled": False, "at": time.time()})
    res = asyncio.run(inst.bili_listen_audio())
    assert res["is_error"] is True
    assert res["output"] == "暂时没有听到声音。"
    assert res["result"]["reason"] == "capture_disabled"
    assert res["result"]["audio_enabled"] is False


def test_listen_audio_diagnostic_reason_no_audio():
    """开关开启但从未收到音频段：reason=no_audio_received。"""
    import asyncio

    pkg = _pkg()
    inst = _make_instance(pkg)
    inst._receive_ext_audio_state({"enabled": True, "at": time.time()})
    res = asyncio.run(inst.bili_listen_audio())
    assert res["result"]["reason"] == "no_audio_received"


def test_find_asr_python_prefers_env(monkeypatch):
    """BILI_ASR_PYTHON 环境变量优先于候选路径（v0.7.14 外部桥）。"""
    import tempfile

    pkg = _pkg()
    inst = _make_instance(pkg)
    fake = os.path.join(tempfile.gettempdir(), "fake_asr_python.exe")
    try:
        open(fake, "w").close()
        monkeypatch.setenv("BILI_ASR_PYTHON", fake)
        assert inst._find_asr_python() == os.path.normpath(fake)
    finally:
        try:
            os.remove(fake)
        except OSError:
            pass


def test_whisper_model_returns_callable_bridge():
    """_whisper_model 返回可调用桥（不 import faster-whisper），不可用时返回 None。"""
    pkg = _pkg()
    inst = _make_instance(pkg)
    fn = inst._whisper_model()
    assert callable(fn)
    assert inst._whisper_model() is fn  # 缓存


def test_asr_bridge_transcribe_protocol(monkeypatch):
    """外部桥协议：发送 transcribe 请求、解析响应文本；桥关闭时降级 None。"""
    pkg = _pkg()
    inst = _make_instance(pkg)
    written = []

    class FakeStdin:
        def write(self, s):
            written.append(s)

        def flush(self):
            pass

    class FakeStdout:
        def __init__(self, lines):
            self._lines = list(lines)

        def readline(self):
            return self._lines.pop(0) if self._lines else ""

    class FakeProc:
        """鸭子类型 Popen：poll/kill/returncode 自持，不触碰真实句柄。"""

        def __init__(self, lines):
            self.returncode = None
            self.killed = False
            self.stdin = FakeStdin()
            self.stdout = FakeStdout(list(lines))

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = 1

    fake_proc = FakeProc([json.dumps({"ok": True, "text": "主播说大家好"}) + "\n"])
    inst._asr_bridge_proc = fake_proc
    text = inst._asr_transcribe_via_bridge(r"C:\fake.wav", "zh")
    assert text == "主播说大家好"
    assert "fake.wav" in written[0]

    # 桥返回错误 → None 且进程被清理（可立即重启）
    fake_proc2 = FakeProc([json.dumps({"ok": False, "error": "boom"}) + "\n"])
    inst._asr_bridge_proc = fake_proc2
    inst._asr_bridge_at = 0.0
    assert inst._asr_transcribe_via_bridge(r"C:\fake.wav") is None
    assert getattr(inst, "_asr_bridge_proc", None) is None
    assert getattr(inst, "_asr_bridge_at", 0.0) == 0.0  # 冷却立即释放
    assert fake_proc2.killed is True

    # 桥无输出（关闭）→ None
    fake_proc3 = FakeProc([])
    inst._asr_bridge_proc = fake_proc3
    inst._asr_bridge_at = 0.0
    assert inst._asr_transcribe_via_bridge(r"C:\fake.wav") is None


def test_asr_worker_uses_callable_bridge(monkeypatch):
    """ASR worker 用桥 callable 转写，文本落入 _ext_asr_data（v0.7.14）。"""
    import threading

    pkg = _pkg()
    inst = _make_instance(pkg)
    calls = []

    def fake_bridge(path, lang):
        calls.append((path, lang))
        return " 直播里在说 hello  "

    monkeypatch.setattr(inst, "_whisper_model", lambda: fake_bridge)
    inst._ext_audio_data = {"path": r"C:\fake.wav", "sample_rate": 16000, "seq": 7, "at": time.time()}
    inst._asr_worker_evt = threading.Event()
    inst._asr_worker_evt.set()
    t = threading.Thread(target=inst._asr_worker_loop, name="t-asr", daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        if getattr(inst, "_ext_asr_data", None):
            break
        time.sleep(0.05)
    inst._asr_worker_stop = True
    t.join(timeout=2)
    asr = getattr(inst, "_ext_asr_data", None)
    assert asr is not None and asr["text"] == "直播里在说 hello" and asr["seq"] == 7
    assert calls and calls[0] == (r"C:\fake.wav", "zh")


def test_receive_ext_audio_sets_asr_event(monkeypatch):
    """音频到达会唤醒 ASR worker（v0.7.19 回归：_asr_worker_evt 运行时此前从不被 set，
    转写线程永远沉睡——ASR 锁修复后仍听不到声音的漏网根因）。"""
    import base64
    import threading

    pkg = _pkg()
    inst = _make_instance(pkg)
    inst._asr_worker_evt = threading.Event()
    inst._asr_worker_evt.clear()
    monkeypatch.setattr(inst, "_whisper_model", lambda: None)  # 不引外部桥，只验证事件

    raw = b"RIFF" + b"\x00" * 600  # 最小伪 wav 载荷（仅验证落盘+事件链路）
    resp = inst._receive_ext_audio(
        {
            "audio": "data:audio/wav;base64," + base64.b64encode(raw).decode(),
            "sample_rate": 16000,
            "seq": 42,
            "at": time.time(),
        }
    )
    assert resp.get("ok") is True
    assert resp.get("detail") == "已保存当前声音段"
    assert inst._asr_worker_evt.is_set()  # 关键：事件被触发，worker 才会醒来转写


def test_kick_asr_sets_event_on_worker_start(monkeypatch):
    """_kick_asr 启动 worker 时唤醒一次（v0.7.19）。"""
    import threading

    pkg = _pkg()
    inst = _make_instance(pkg)
    inst._asr_worker_evt = threading.Event()
    inst._asr_worker_evt.clear()
    monkeypatch.setattr(inst, "_whisper_model", lambda: None)
    inst._kick_asr()
    assert getattr(inst, "_asr_worker_thread", None) is not None
    assert inst._asr_worker_evt.is_set()
    inst._asr_worker_stop = True
    inst._asr_worker_evt.set()
    inst._asr_worker_thread.join(timeout=2)


# ══════════════ v0.7.16：感知输出完成话术机器阻断 ══════════════


def _src() -> str:
    import pathlib

    p = pathlib.Path(__file__).resolve().parents[1] / "__init__.py"
    return p.read_text(encoding="utf-8")


def test_sense_paths_forbid_completion_phrasing():
    """所有感知/查询路径输出与描述都必须声明'不是任务'并严禁完成话术（v0.7.16）。"""
    src = _src()
    # run 入口感知分支 output 与 description
    assert "不是任务、无需汇报" in src
    assert "严禁'任务完成/搞定啦/完成啦/已确认'等完成话术与开场白" in src
    # 内部感知上下文（_perceive_context）
    assert "这不是'任务'，只是感知到的实况" in src
    # bili_now_watching llm_tool output
    assert "感知到的实况，不是任务、无需汇报" in src
    # see_live / see_video 描述
    assert "感知实况不是'任务'" in src
    # bili_see_screen 描述
    assert "看到/没看到都是感知实况，不是'任务'" in src


# ══════════════ v0.7.17：web_ui 路由顺序回归（audio-state 不能被 audio 前缀吞掉） ══════════════


def _web_ui_src() -> str:
    import pathlib

    p = pathlib.Path(__file__).resolve().parents[1] / "web_ui.py"
    return p.read_text(encoding="utf-8")


def test_web_ui_audio_state_routed_before_audio():
    """/api/extension-audio-state 必须先于 /api/extension-audio 判断，
    否则 audio-state 请求体（无 base64 声音字段）会被 audio 处理器吞掉，
    插件永远收不到捕获开关状态（v0.7.17 现场实测：POST audio-state 返回
    '声音数据为空或格式异常'）。"""
    src = _web_ui_src()
    idx_state = src.index('startswith("/api/extension-audio-state")')
    idx_audio = src.index('startswith("/api/extension-audio")')
    assert idx_state < idx_audio, "audio-state 路由判断必须排在 audio 之前"


# ══════════════ v0.7.21：扩展实况推送去焦点门控 + source 标记（三包字节级同步） ══════════════


@pytest.mark.parametrize("pkg_name", ["bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360"])
def test_ext_no_focus_gate_on_live_pushes(pkg_name):
    """tab/dom/shot/cookie 实况推送不再带窗口焦点门控（失焦仍持续推送，YUI 感知跟得上切换）。"""
    bg = _read_ext(pkg_name, "background.js")
    for fn in ("pushTab", "pushDom", "pushShot", "pushCookie"):
        i = bg.index(f"async function {fn}()")
        head = bg[i : i + 400]
        assert "if (!windowFocused) return;" not in head, f"{pkg_name} {fn} 不应有焦点门控"
    assert "let windowFocused = true;" in bg  # 定义保留（onFocusChanged 监听仍存在）


@pytest.mark.parametrize("pkg_name", ["bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360"])
def test_ext_focus_gate_keeps_cookie_and_audio_unrestricted(pkg_name):
    """cookie / 声音开关推送不能带焦点门控（后台必须持续同步与听到声音）。"""
    bg = _read_ext(pkg_name, "background.js")
    i = bg.index("async function pushCookie()")
    head = bg[i : i + 300]
    assert "if (!windowFocused) return;" not in head
    assert "audio_capture_enabled" in bg


@pytest.mark.parametrize("pkg_name", ["bili-bridge-chrome", "bili-bridge-edge", "bili-bridge-360"])
def test_ext_live_pushes_carry_source(pkg_name):
    """tab/dom/shot/cookie 推送体必须带 source: EXT_SOURCE（插件按 cookie 来源过滤，多浏览器互不覆盖）。"""
    bg = _read_ext(pkg_name, "background.js")
    for fn in ("pushCookie", "pushTab", "pushDom", "pushShot"):
        i = bg.index(f"async function {fn}()")
        head = bg[i : i + 800]
        assert "source: EXT_SOURCE" in head, f"{pkg_name} {fn} 缺 source 标记"


def test_ext_source_synced_across_packages():
    """实况推送 source 相关代码在三包中必须字节级一致（除头部标识行）。"""
    chrome = _read_ext("bili-bridge-chrome", "background.js")
    edge = _read_ext("bili-bridge-edge", "background.js")
    pkg360 = _read_ext("bili-bridge-360", "background.js")

    def _source_block(text: str) -> str:
        start = text.index("async function pushCookie()")
        end = text.index("async function pushAudioState(")
        return text[start:end]

    for src in (edge, pkg360):
        assert _source_block(src) == _source_block(chrome), "实况推送代码三包必须字节级一致"

