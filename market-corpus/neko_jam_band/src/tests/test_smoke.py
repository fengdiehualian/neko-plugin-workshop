"""neko_jam_band 冒烟测试：纯 DSP/乐理逻辑，零音频设备依赖。"""

from __future__ import annotations

import importlib

import numpy as np


def test_module_imports():
    mod = importlib.import_module("plugin.plugins.neko_jam_band")
    assert hasattr(mod, "NekoJamBandPlugin")
    core = importlib.import_module("plugin.plugins.neko_jam_band.neko_jam_core")
    assert hasattr(core, "YINPitchDetector")
    assert hasattr(core, "SynthVoice")


def test_yin_detects_sine_pitch():
    from plugin.plugins.neko_jam_band.neko_jam_core import YINPitchDetector

    sr = 16000
    det = YINPitchDetector(sample_rate=sr)
    for freq in (110.0, 220.0, 330.0, 440.0):
        t = np.arange(sr) / sr
        frame = 0.3 * np.sin(2 * np.pi * freq * t)[: det.frame_size]
        got = det.detect(frame)
        assert got is not None, freq
        assert abs(got - freq) / freq < 0.03, (freq, got)


def test_yin_silence_returns_none():
    from plugin.plugins.neko_jam_band.neko_jam_core import YINPitchDetector

    det = YINPitchDetector(sample_rate=16000)
    assert det.detect(np.zeros(det.frame_size)) is None
    assert det.detect(np.random.default_rng(7).normal(0, 0.001, det.frame_size)) is None


def test_note_tracker_stability():
    from plugin.plugins.neko_jam_band.neko_jam_core import NoteTracker

    tr = NoteTracker()
    t0 = 100.0
    for i in range(8):  # 220Hz ≈ A3=57
        tr.feed(220.0, 0.2, t0 + i * 0.05)
    assert tr.stable_midi() == 57
    assert tr.hold_seconds(t0 + 0.42) > 0  # 距最后有声帧 < silence_gap
    # 喂入静音后 hold 归零
    for i in range(4):
        tr.feed(None, 0.0, t0 + 1 + i * 0.05)
    assert tr.hold_seconds(t0 + 2) == 0


def test_key_estimator_detects_c_major():
    from plugin.plugins.neko_jam_band.neko_jam_core import KeyEstimator

    est = KeyEstimator(min_samples=10)
    # C 大调音级序列
    for pc in [0, 2, 4, 5, 7, 9, 11] * 3:
        est.feed(pc)
    key = est.current_key()
    assert key is not None
    assert key["root"] == 0 and not key["minor"]
    assert est.confidence() > 0.5


def test_key_estimator_detects_a_minor():
    from plugin.plugins.neko_jam_band.neko_jam_core import KeyEstimator

    est = KeyEstimator(min_samples=10)
    # A 自然小调：主音 A 权重最高，属音 E、下属 D 次之
    seq = [9, 9, 4, 5, 7, 9, 2, 0, 9, 11, 4, 9] * 3
    for pc in seq:
        est.feed(pc)
    key = est.current_key()
    assert key is not None and key["minor"] and key["root"] == 9


def test_parse_key():
    from plugin.plugins.neko_jam_band.neko_jam_core import KeyEstimator

    assert KeyEstimator.parse_key("C") == {"root": 0, "minor": False}
    assert KeyEstimator.parse_key("Am") == {"root": 9, "minor": True}
    assert KeyEstimator.parse_key("F#m") == {"root": 6, "minor": True}
    assert KeyEstimator.parse_key("H") is None


def test_chord_progressor():
    from plugin.plugins.neko_jam_band.neko_jam_core import ChordProgressor

    prog = ChordProgressor({"root": 0, "minor": False})
    roots = [prog.current_root() % 12]
    roots += [prog.advance_bar() % 12 for _ in range(3)]
    assert roots == [0, 7, 9, 5]  # C-G-Am-F
    tones = prog.current_tones()
    assert len(tones) == 4 and tones[1] - tones[0] in (3, 4)


def test_synth_render_all_instruments():
    from plugin.plugins.neko_jam_band.neko_jam_core import SynthVoice

    voice = SynthVoice(sample_rate=16000)
    for inst in ("piano", "epiano", "music_box", "ukulele"):
        w = voice.render(inst, 440.0, 0.5)
        assert w.dtype == np.float32
        assert len(w) == 8000
        assert 0 < float(np.abs(w).max()) <= 0.5
