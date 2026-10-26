"""bili_viewer 系统音频桥（替代扩展 tabCapture 采集，全自动无需浏览器授权）。

用 soundcard 的 WASAPI loopback 录制系统输出音频（所有应用播放的声音），
能量门限（RMS）判定有声段，faster-whisper 转写后以 JSON 行输出到 stdout。

依赖（N.E.K.O venv 已装）：soundcard、faster-whisper、numpy。

stdout 事件（逐行 JSON）：
  {"op":"ready","device":"<loopback 设备名>"}     桥就绪
  {"op":"heard","text":"...","at":<unix 秒>}       听到并转写的内容
  {"op":"error","error":"..."}                     恢复性错误（桥继续运行）
stdin 命令：
  {"op":"ping"} → {"op":"pong","at":...}
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import wave

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

_SEG_SECS = 8          # 每段时长（秒）
_RATE = 48000          # loopback 采样率
_TARGET_RATE = 16000   # whisper 目标采样率
_RMS_GATE = 0.002      # 能量门限：低于视为静音跳过
_MAX_TEXT = 500

_model = None


def _get_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel
    _model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _model


def _pick_loopback():
    import soundcard as sc
    mics = sc.all_microphones(include_loopback=True)
    if not mics:
        raise RuntimeError("no microphone/loopback device")
    # 优先：默认输出设备对应的 loopback（蓝牙耳机/扬声器切换后自动跟随）
    try:
        default = sc.default_speaker()
        dname = (default.name or "").strip()
        if dname:
            for m in mics:
                if m.name == dname or m.name.startswith(dname.split("(")[0].strip()):
                    return m
    except Exception:
        pass
    # 兜底：名字含扬声器/speaker
    for m in mics:
        if "扬声器" in m.name or "speaker" in m.name.lower():
            return m
    return mics[0]


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _downmix_and_resample(data):
    """stereo float32(48k) -> mono int16(16k)。简单平均+3x抽取（低通）。"""
    import numpy as np
    arr = np.asarray(data, dtype=np.float32)
    mono = arr.mean(axis=1)
    n = len(mono) // 3
    if n == 0:
        raise RuntimeError("segment too short")
    r16 = mono[: n * 3].reshape(n, 3).mean(axis=1)
    # 归一化到 int16 范围
    return np.clip(r16 * 32767.0, -32768.0, 32767.0).astype(np.int16)


def _write_wav(buf: bytes) -> str:
    import tempfile
    fd, path = tempfile.mkstemp(prefix="bili_sys_", suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_TARGET_RATE)
        w.writeframes(buf)
    return path


def _rms(data) -> float:
    import numpy as np
    arr = np.asarray(data, dtype=np.float32)
    return float(np.sqrt(np.mean(arr ** 2)))


def _stdin_loop() -> None:
    for line in sys.stdin:
        line = (line or "").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get("op") == "ping":
            _emit({"op": "pong", "at": time.time()})


def main() -> None:
    import numpy as np

    threading.Thread(target=_stdin_loop, daemon=True).start()
    try:
        device = _pick_loopback()
    except Exception as exc:  # noqa: BLE001
        _emit({"op": "error", "error": "loopback-unavailable: %s" % str(exc)[:150]})
        time.sleep(2)
        return
    _emit({"op": "ready", "device": device.name})

    try:
        with device.recorder(samplerate=_RATE) as rec:
            chunk = []
            chunk_len = 0
            while True:
                try:
                    data = rec.record(numframes=_RATE * 1)  # 1 秒块
                except Exception as exc:  # noqa: BLE001
                    _emit({"op": "error", "error": "record: %s" % str(exc)[:120]})
                    time.sleep(1)
                    continue
                chunk.append(data)
                chunk_len += 1
                if chunk_len >= _SEG_SECS:
                    buf = None
                    for seg in chunk:
                        arr = seg
                        # 合并成连续数组
                        if buf is not None:
                            buf = np.concatenate([buf, np.asarray(arr, dtype=np.float32)], axis=0)
                        else:
                            buf = np.asarray(arr, dtype=np.float32)
                    chunk = []
                    chunk_len = 0
                    try:
                        rms = _rms(buf)
                        if rms < _RMS_GATE:
                            continue  # 静音段：跳过转写
                        pcm = _downmix_and_resample(buf)
                        path = _write_wav(pcm.tobytes())
                        try:
                            model = _get_model()
                            segs, _info = model.transcribe(path, language="zh", vad_filter=False)
                            text = "".join(s.text for s in segs).strip()
                            if text:
                                _emit({"op": "heard", "text": text[:_MAX_TEXT], "at": time.time()})
                        finally:
                            try:
                                os.remove(path)
                            except OSError:
                                pass
                    except Exception as exc:  # noqa: BLE001
                        _emit({"op": "error", "error": "process: %s" % str(exc)[:200]})
    except Exception as exc:  # noqa: BLE001
        _emit({"op": "error", "error": "fatal: %s" % str(exc)[:200]})


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        _emit({"op": "error", "error": "fatal: " + traceback.format_exc()[-300:]})
