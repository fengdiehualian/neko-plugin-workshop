"""neko_jam_band 纯逻辑核心：DSP + 乐理（零音频设备依赖，可单测）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

BPM_DEFAULT = 92
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler 调性感知权重（音级 0 = 主音）
KEY_PROFILES: Dict[str, Tuple[float, ...]] = {
    "major": (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88),
    "minor": (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17),
}


def midi_to_freq(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def note_name(midi: Optional[int]) -> str:
    if midi is None:
        return "?"
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def chord_tones(root_midi: int) -> List[int]:
    """大三/小三和弦三音组（根据 root 音级奇偶简化判断大/小，由 progressor 控制）。"""
    return [root_midi, root_midi + 4, root_midi + 7, root_midi + 12]


def minor_chord_tones(root_midi: int) -> List[int]:
    return [root_midi, root_midi + 3, root_midi + 7, root_midi + 12]


# ═══════════════════════════ YIN 音高检测 ═══════════════════════════

class YINPitchDetector:
    """简化 YIN：差分函数 + 累积均值归一 + 抛物线插值。"""

    def __init__(self, sample_rate: int = 16000, frame_size: int = 800,
                 fmin: float = 70.0, fmax: float = 1000.0, threshold: float = 0.15):
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.fmin = fmin
        self.fmax = fmax
        self.threshold = threshold
        self.tau_min = int(sample_rate / fmax)
        self.tau_max = min(int(sample_rate / fmin), frame_size // 2)

    def detect(self, frame: np.ndarray) -> Optional[float]:
        """返回基频 Hz；无声/不确定时返回 None。"""
        x = np.asarray(frame, dtype=np.float64)
        if len(x) < self.frame_size:
            x = np.pad(x, (0, self.frame_size - len(x)))
        x = x[: self.frame_size]
        rms = float(np.sqrt(np.mean(x * x)))
        if rms < 0.015:  # 静音门限
            return None

        tau_min, tau_max = self.tau_min, self.tau_max
        if tau_max - tau_min < 8:
            return None
        w = tau_max
        # 差分函数 d(tau)
        diff = np.zeros(tau_max)
        for tau in range(1, tau_max):
            seg = x[: w] - x[tau: tau + w]
            diff[tau] = float(np.dot(seg, seg))
        # 累积均值归一 d'(tau)
        cm = np.cumsum(diff)
        denom = cm[1:] if len(cm) > 1 else cm
        with np.errstate(divide="ignore", invalid="ignore"):
            dprime = diff[1:] * np.arange(1, tau_max) / np.where(denom == 0, 1e-9, denom)
        # 第一个低于阈值的谷
        tau_est = None
        for i in range(1, len(dprime) - 1):
            if dprime[i] < self.threshold:
                while i + 1 < len(dprime) - 1 and dprime[i + 1] < dprime[i]:
                    i += 1
                tau_est = i + 1
                break
        if tau_est is None:
            # 退而求其次取最小值点（若足够深）
            i = int(np.argmin(dprime)) + 1
            if dprime[i - 1] > 0.5:
                return None
            tau_est = i
        # 抛物线插值精化
        if 1 <= tau_est < len(dprime):
            a, b, c = dprime[tau_est - 1], dprime[tau_est], dprime[tau_est + 1]
            denom_p = a - 2 * b + c
            shift = 0.5 * (a - c) / denom_p if abs(denom_p) > 1e-12 else 0.0
            tau_est = tau_est + max(-1.0, min(1.0, shift))
        freq = self.sample_rate / tau_est
        if self.fmin <= freq <= self.fmax:
            return float(freq)
        return None


# ═══════════════════════════ 音符追踪 ═══════════════════════════

class NoteTracker:
    """滑动中值滤波 + 稳定音判定 + 音级直方图。"""

    def __init__(self, median_window: int = 5, stability: int = 3, silence_gap: float = 0.12):
        self.median_window = median_window
        self.stability = stability
        self.silence_gap = silence_gap
        self._midi_hist: List[Optional[int]] = []
        self._pitch_class_hist: Dict[int, int] = {}
        self._hold_start: float = 0.0
        self._hold_midi: Optional[int] = None
        self._last_voiced: float = 0.0
        self._recent_names: List[str] = []

    def _to_midi(self, freq: float) -> int:
        return int(round(69 + 12 * math.log2(freq / 440.0)))

    def feed(self, freq: Optional[float], level: float, now: float):
        midi: Optional[int] = None
        if freq is not None and level > 0.02:
            midi = self._to_midi(freq)
        self._midi_hist.append(midi)
        if len(self._midi_hist) > self.median_window:
            self._midi_hist.pop(0)
        if midi is not None:
            pc = midi % 12
            self._pitch_class_hist[pc] = self._pitch_class_hist.get(pc, 0) + 1
            self._last_voiced = now
            stable = self.stable_midi()
            if stable is not None:
                if self._hold_midi != stable:
                    self._hold_midi = stable
                    self._hold_start = now
                    self._recent_names.append(note_name(stable))
                    if len(self._recent_names) > 64:
                        self._recent_names.pop(0)

    def stable_midi(self) -> Optional[int]:
        vals = [v for v in self._midi_hist[-self.stability * 2:] if v is not None]
        if len(vals) < self.stability:
            return None
        vals.sort()
        mid = vals[len(vals) // 2]
        spread = max(vals) - min(vals)
        if spread > 1:  # 抖动超过 1 半音视为不稳定
            return None
        return mid

    def hold_seconds(self, now: float) -> float:
        if self._hold_midi is None or now - self._last_voiced > self.silence_gap:
            return 0.0
        return max(0.0, now - self._hold_start)

    def pitch_class_histogram(self) -> np.ndarray:
        hist = np.zeros(12)
        total = sum(self._pitch_class_hist.values()) or 1
        for pc, n in self._pitch_class_hist.items():
            hist[pc] = n / total
        return hist

    def recent_names(self, limit: int = 16) -> List[str]:
        return self._recent_names[-limit:]


# ═══════════════════════════ 调性估计 ═══════════════════════════

class KeyEstimator:
    """音级直方图 × Krumhansl 权重 → 24 调取最大相关。"""

    def __init__(self, min_samples: int = 12):
        self.min_samples = min_samples
        self._hist: Dict[int, int] = {}
        self._cache: Optional[Dict[str, Any]] = None

    def feed(self, pitch_class: int):
        self._hist[pitch_class] = self._hist.get(pitch_class, 0) + 1
        self._cache = None

    def current_key(self) -> Optional[Dict[str, Any]]:
        if self._cache is None:
            total = sum(self._hist.values())
            if total < self.min_samples:
                return None
            hist = np.zeros(12)
            for pc, n in self._hist.items():
                hist[pc] = n / total
            best, best_corr = None, -2.0
            for root in range(12):
                for mode, profile in KEY_PROFILES.items():
                    p = np.roll(np.array(profile), root)
                    corr = float(np.corrcoef(hist, p)[0, 1])
                    if corr > best_corr:
                        best_corr = corr
                        best = {"root": root, "minor": mode == "minor"}
            self._cache = best
        return dict(self._cache)

    def confidence(self) -> float:
        key = self.current_key()
        if key is None:
            return 0.0
        hist = np.zeros(12)
        total = sum(self._hist.values()) or 1
        for pc, n in self._hist.items():
            hist[pc] = n / total
        p = np.roll(np.array(KEY_PROFILES["minor" if key["minor"] else "major"]), key["root"])
        return max(0.0, float(np.corrcoef(hist, p)[0, 1]))

    @staticmethod
    def parse_key(text: str) -> Optional[Dict[str, Any]]:
        """'C' 'G' 'Am' 'F#m' → {'root': pc, 'minor': bool}。"""
        t = (text or "").strip()
        if not t:
            return None
        minor = t.endswith("m") and not t.endswith("maj")
        base = t[:-1] if minor and not t.endswith("maj") else t
        base = base.strip()
        if base not in NOTE_NAMES:
            return None
        return {"root": NOTE_NAMES.index(base), "minor": minor}


# ═══════════════════════════ 和弦进程 ═══════════════════════════

class ChordProgressor:
    """大调 I-V-vi-IV / 小调 i-VI-III-VII，每小节一换。"""

    MAJOR_DEGREES = [0, 7, 9, 5]    # I, V, vi, IV（相对主音的半音）
    MINOR_DEGREES = [0, 8, 3, 10]   # i, VI, III, VII
    MAJOR_IS_MINOR = [False, False, True, False]
    MINOR_IS_MINOR = [True, False, False, False]

    def __init__(self, key: Optional[Dict[str, Any]] = None):
        self.key = key or {"root": 0, "minor": False}
        self._degree = 0

    def set_key(self, key: Optional[Dict[str, Any]]):
        if key:
            self.key = key
            self._degree = 0

    def current_root(self) -> int:
        degrees = self.MINOR_DEGREES if self.key["minor"] else self.MAJOR_DEGREES
        root_pc = (self.key["root"] + degrees[self._degree]) % 12
        # 用 C4=60 作为伴奏音区中心
        midi = 60 + root_pc
        return midi - 12 if midi > 67 else midi

    def current_tones(self) -> List[int]:
        root = self.current_root()
        minor = (self.MINOR_IS_MINOR if self.key["minor"] else self.MAJOR_IS_MINOR)[self._degree]
        return minor_chord_tones(root) if minor else chord_tones(root)

    def advance_bar(self) -> int:
        self._degree = (self._degree + 1) % 4
        return self.current_root()


# ═══════════════════════════ 合成器 ═══════════════════════════

class SynthVoice:
    """四种合成音色（泛音叠加 + ADSR），零采样文件。"""

    HARMONICS = {
        "piano":     [(1, 1.0), (2, 0.45), (3, 0.22), (4, 0.10)],
        "epiano":    [(1, 1.0), (2, 0.18), (3, 0.06)],
        "music_box": [(1, 0.8), (2, 0.5), (4, 0.35), (6, 0.18)],
        "ukulele":   [(1, 1.0), (2, 0.55), (3, 0.30), (5, 0.12)],
    }
    ATTACKS = {"piano": 0.004, "epiano": 0.02, "music_box": 0.002, "ukulele": 0.003}
    DECAY_POWERS = {"piano": 2.2, "epiano": 1.2, "music_box": 2.8, "ukulele": 2.6}

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate

    def render(self, instrument: str, freq: float, dur: float, gain: float = 0.4) -> np.ndarray:
        n = max(int(self.sample_rate * dur), 1)
        t = np.arange(n) / self.sample_rate
        partials = self.HARMONICS.get(instrument, self.HARMONICS["piano"])
        wave = np.zeros(n)
        for mult, amp in partials:
            wave += amp * np.sin(2 * np.pi * freq * mult * t)
        # ADSR：快 attack + 指数衰减 + 短 release
        env = np.ones(n)
        a = max(int(self.ATTACKS.get(instrument, 0.005) * self.sample_rate), 1)
        env[:a] = np.linspace(0, 1, a)
        env *= np.exp(-self.DECAY_POWERS.get(instrument, 2.0) * t / max(dur, 0.05))
        r = min(int(0.03 * self.sample_rate), n)
        if r > 0:
            env[-r:] *= np.linspace(1, 0, r)
        wave *= env
        peak = float(np.abs(wave).max()) or 1.0
        return (wave / peak * gain).astype(np.float32)
