"""N.E.K.O 猫娘伴奏团 (neko_jam_band)。

你唱歌，猫娘用乐器伴奏::

    麦克风 → YIN 音高检测 → 调性估计 → 和弦进程 → 合成器 → 音箱
                (numpy, 零重依赖)

设计原则：
  - 拍点驱动而非逐音跟随：猫娘按 BPM 稳定弹 I-V-vi-IV 琶音，
    你的歌声用于校准调性，而不是让伴奏忽快忽慢
  - 唱稳定长音时猫娘叠三度和声（可关闭）
  - 音频栈（sounddevice）延迟导入：无音频环境的 CI 也能跑测试
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)

from .neko_jam_core import (
    BPM_DEFAULT,
    ChordProgressor,
    KeyEstimator,
    NoteTracker,
    SynthVoice,
    YINPitchDetector,
    chord_tones,
    midi_to_freq,
    note_name,
)

_VALID_INSTRUMENTS = ("piano", "epiano", "music_box", "ukulele")
_VALID_PATTERNS = ("arpeggio", "chord_pad", "offbeat")
_BEATS_PER_BAR = 4


class JamAudioEngine:
    """音频引擎：录音线程 + 拍点线程 + 播放线程。

    sounddevice 延迟导入，实例化永远安全。
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.detector = YINPitchDetector(sample_rate=sample_rate)
        self.tracker = NoteTracker()
        self.estimator = KeyEstimator()
        self.progressor = ChordProgressor()
        self.voice = SynthVoice(sample_rate=sample_rate)

        self.running = False
        self.key_locked = False  # 手动定调后不再自动改
        self.harmony = True      # 猫娘和声开关
        self.beat_thread: Optional[threading.Thread] = None
        self.mic_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._beat_index = 0
        self._bar_count = 0
        self._long_note_at: float = 0.0
        self._last_harmony_time: float = 0.0
        # 回调（插件注入）
        self.on_harmony: Optional[Any] = None  # fn(note_name)
        # 音频流句柄（导入成功后才有）
        self._in_stream = None
        self._out_stream = None
        self._pending: List[Any] = []

    # ── 生命周期 ─────────────────────────────────────────────────────────

    def start(self, instrument: str, pattern: str, bpm: int) -> Dict[str, Any]:
        try:
            import sounddevice as sd  # noqa: PLC0415
        except Exception as e:  # OSError/ImportError：无 PortAudio 等
            return {"ok": False, "error": f"音频库不可用: {e}"}

        self.running = True
        self._stop.clear()
        try:
            self._in_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.detector.frame_size,
                callback=self._mic_callback,
            )
            self._in_stream.start()
            self._out_stream = sd.OutputStream(
                samplerate=self.sample_rate, channels=1, dtype="float32"
            )
            self._out_stream.start()
        except Exception as e:
            self.stop()
            return {"ok": False, "error": f"音频设备打开失败: {e}"}

        self.beat_thread = threading.Thread(
            target=self._beat_loop, args=(instrument, pattern, bpm), daemon=True,
            name="neko-jam-beat",
        )
        self.beat_thread.start()
        return {"ok": True}

    def stop(self):
        self.running = False
        self._stop.set()
        for attr in ("_in_stream", "_out_stream"):
            stream = getattr(self, attr, None)
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    # ── 麦克风回调：音高 → 音级统计 → 调性 ────────────────────────────────

    def _mic_callback(self, indata, frames, time_info, status):  # noqa: ANN001
        if not self.running:
            return
        now = time.time()
        mono = indata[:, 0]
        freq = self.detector.detect(mono)
        level = float((mono * mono).sum() / max(len(mono), 1)) ** 0.5
        with self._lock:
            self.tracker.feed(freq, level, now)
            midi = self.tracker.stable_midi()
            if midi is not None:
                pc = midi % 12
                if not self.key_locked:
                    self.estimator.feed(pc)
                    # 长音结束（稳定音消失）时触发和声
                else:
                    self.estimator.feed(pc)
                if (
                    self.harmony
                    and self.tracker.hold_seconds(now) >= 0.8
                    and now - self._last_harmony_time > 2.5
                ):
                    self._last_harmony_time = now
                    if self.on_harmony:
                        try:
                            chord_root = self.progressor.current_root()
                            third = chord_tones(chord_root)[:2][1]  # 和弦三音
                            self.on_harmony(third)
                        except Exception:
                            pass

    # ── 拍点循环：按 BPM 触发伴奏 ─────────────────────────────────────────

    def _beat_loop(self, instrument: str, pattern: str, bpm: int):
        interval = 60.0 / max(bpm, 30)
        # 先奏一次主和弦提示调性
        with self._lock:
            root = self.progressor.current_root()
        self._play_chord(instrument, root, "down")
        while not self._stop.wait(interval):
            try:
                with self._lock:
                    beat = self._beat_index % _BEATS_PER_BAR
                    bar_done = beat == _BEATS_PER_BAR - 1
                    if bar_done:
                        self._bar_count += 1
                        # 每小节根据最新调性推进和弦
                        if not self.key_locked:
                            key = self.estimator.current_key()
                            self.progressor.set_key(key)
                        root = self.progressor.advance_bar()
                    else:
                        root = self.progressor.current_root()
                    self._beat_index += 1
                if pattern == "chord_pad":
                    if beat == 0:
                        self._play_chord(instrument, root, "long")
                elif pattern == "offbeat":
                    if beat in (1, 3):
                        self._play_chord(instrument, root, "short")
                else:  # arpeggio
                    idx = {0: 0, 1: 1, 2: 2, 3: 1}[beat]
                    tones = chord_tones(root)
                    self._play_note(instrument, tones[idx % len(tones)], 0.32)
                    if beat == 0:
                        self._play_note(instrument, tones[0] - 12, 0.5)  # 低音
            except Exception:
                continue

    # ── 合成与播放 ───────────────────────────────────────────────────────

    def _render(self, instrument: str, midi: int, dur: float):
        return self.voice.render(instrument, midi_to_freq(midi), dur)

    def _play_note(self, instrument: str, midi: int, dur: float):
        wave = self._render(instrument, midi, dur)
        self._emit(wave)

    def _play_chord(self, instrument: str, root_midi: int, style: str):
        tones = chord_tones(root_midi)
        dur = {"down": 0.9, "long": 1.8, "short": 0.25}[style]
        total = self._render(instrument, tones[0], dur)
        for t in tones[1:]:
            w = self._render(instrument, t, dur)
            m = min(len(total), len(w))
            total[:m] += w[:m] * 0.7
        peak = float(abs(total).max()) or 1.0
        self._emit(total * (0.5 / peak))

    def _emit(self, wave):
        out = self._out_stream
        if out is None:
            return
        try:
            out.write(wave.astype("float32").reshape(-1, 1))
        except Exception:
            pass

    # ── 状态 ─────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        with self._lock:
            key = self.estimator.current_key()
            midi = self.tracker.stable_midi()
            return {
                "running": self.running,
                "key": key,
                "key_confidence": round(self.estimator.confidence(), 2),
                "current_note": note_name(midi) if midi is not None else None,
                "bars_played": self._bar_count,
            }

    def history_notes(self, limit: int = 16) -> List[str]:
        with self._lock:
            return list(self.tracker.recent_names(limit))


# ═══════════════════════════════════════════════════════════════════════════

@neko_plugin
class NekoJamBandPlugin(NekoPluginBase):
    """猫娘伴奏团 - 你唱歌，猫娘用乐器给你伴奏。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.engine = JamAudioEngine()
        self.instrument = "piano"
        self.pattern = "arpeggio"
        self.bpm = BPM_DEFAULT
        self.engine.on_harmony = self._on_harmony

    # ── 生命周期 ─────────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            self.register_static_ui("static")
        except Exception as e:
            self.logger.warning("register_static_ui failed: {}", e)
        self.logger.info("NekoJamBand ready (audio engine lazy-loaded)")
        return Ok({"status": "ready", "instrument": self.instrument})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self.engine.stop()
        return Ok({"status": "shutdown"})

    # ── 猫娘行为 ─────────────────────────────────────────────────────────

    def _on_harmony(self, third_midi: int):
        """用户唱长音时猫娘叠三度和声。"""
        self.engine._play_note(self.instrument, third_midi + 12, 0.6)

    def _announce(self, text: str):
        try:
            self.push_message(
                source="neko_jam_band",
                visibility=["chat"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": text}],
                priority=5,
                metadata={"activity_type": "jam"},
            )
        except Exception as e:
            self.logger.warning("push_message failed: {}", e)

    # ── 插件入口点 ───────────────────────────────────────────────────────

    @plugin_entry(
        id="jam_start",
        name="开始伴奏",
        description="开始听主人唱歌并用乐器伴奏。当主人说「给我伴奏」「弹起来」「一起玩音乐」时调用",
    )
    async def jam_start(self, **_):
        if self.engine.running:
            return Ok({"status": "already_running", **self.engine.status()})
        result = self.engine.start(self.instrument, self.pattern, self.bpm)
        if not result.get("ok"):
            return Err(SdkError(result.get("error", "启动失败")))
        key = self.engine.status()["key"]
        self._announce(f"伴奏开始~ {self.instrument} 已就绪，{'听你的 ' + key + ' 调喵' if key else '先哼两句让我找到调喵'}！")
        return Ok({"status": "started", "instrument": self.instrument, **self.engine.status()})

    @plugin_entry(
        id="jam_stop",
        name="停止伴奏",
        description="停止伴奏。当主人说「停」「够了」「别弹了」时调用",
    )
    async def jam_stop(self, **_):
        st = self.engine.status()
        self.engine.stop()
        bars = st.get("bars_played", 0)
        if bars > 0:
            self._announce(f"伴奏结束~ 刚才陪你弹了 {bars} 个小节，唱得不错喵！")
        return Ok({"status": "stopped", "last": st})

    @plugin_entry(
        id="set_instrument",
        name="切换乐器",
        description="切换猫娘的伴奏乐器：piano 钢琴 / epiano 电钢琴 / music_box 八音盒 / ukulele 尤克里里",
        input_schema={
            "type": "object",
            "properties": {
                "instrument": {
                    "type": "string",
                    "enum": list(_VALID_INSTRUMENTS),
                    "description": "乐器名",
                },
            },
            "required": ["instrument"],
        },
    )
    async def set_instrument(self, instrument: str, **_):
        if instrument not in _VALID_INSTRUMENTS:
            return Err(SdkError(f"未知乐器：{instrument}，可选 {', '.join(_VALID_INSTRUMENTS)}"))
        self.instrument = instrument
        return Ok({"instrument": instrument})

    @plugin_entry(
        id="set_key",
        name="定调",
        description="手动指定伴奏调性（如 C、Am），或用 auto 让猫娘听你哼歌自动找调",
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "调性，如 C、G、Am；auto 表示自动"},
            },
            "required": ["key"],
        },
    )
    async def set_key(self, key: str, **_):
        if key.lower() == "auto":
            self.engine.key_locked = False
            return Ok({"key": "auto"})
        parsed = KeyEstimator.parse_key(key)
        if parsed is None:
            return Err(SdkError(f"无法解析调性：{key}（示例：C、G、Am、F#m）"))
        self.engine.progressor.set_key(parsed)
        self.engine.key_locked = True
        return Ok({"key": note_name(parsed["root"]) + ("m" if parsed["minor"] else "")})

    @plugin_entry(
        id="set_pattern",
        name="切换伴奏型",
        description="切换伴奏型：arpeggio 琶音 / chord_pad 铺底和弦 / offbeat 反拍",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "enum": list(_VALID_PATTERNS)},
            },
            "required": ["pattern"],
        },
    )
    async def set_pattern(self, pattern: str, **_):
        if pattern not in _VALID_PATTERNS:
            return Err(SdkError(f"未知伴奏型：{pattern}"))
        self.pattern = pattern
        return Ok({"pattern": pattern})

    @plugin_entry(
        id="jam_status",
        name="伴奏状态",
        description="获取当前伴奏状态：调性、主人正在唱的音、已伴奏小节数",
        llm_result_fields=["running", "key", "current_note", "bars_played"],
    )
    async def jam_status(self, **_):
        return Ok({"instrument": self.instrument, **self.engine.status()})

    @plugin_entry(
        id="recent_notes",
        name="最近唱的音",
        description="获取主人最近唱的音名序列（调试/回顾用）",
    )
    async def recent_notes(self, **_):
        return Ok({"notes": self.engine.history_notes()})
