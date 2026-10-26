"""Safe speech diagnostics: no exception messages, audio, text, URLs or keys."""
import logging
import re
import traceback
from pathlib import Path

_logger = None


def configure(logger):
    global _logger
    _logger = logger


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,120}", value) else None


def speech_failure(error, kind, stage):
    prefix = "NEKO_TTS" if kind == "tts" else "NEKO_ASR"
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if not isinstance(status, int):
        status = getattr(getattr(error, "response", None), "status_code", None)
    status = status if isinstance(status, int) and 100 <= status <= 599 else None
    if re.fullmatch(r"NEKO_[A-Z0-9_]{1,65}", str(error)):
        code = str(error)
    elif type(error).__name__ in ("ConnectionClosedError", "ConnectionClosedOK", "ConnectionClosed"):
        code = prefix + "_CONNECTION_CLOSED"
    elif isinstance(error, ImportError):
        code = prefix + "_HOST_IMPORT_FAILED"
    elif isinstance(error, (AttributeError, TypeError)):
        code = prefix + "_HOST_API_MISMATCH"
    elif isinstance(error, TimeoutError):
        code = prefix + "_TIMEOUT"
    elif status in (401, 403):
        code = prefix + "_AUTH_FAILED"
    elif isinstance(error, OSError):
        code = prefix + "_CONNECTION_FAILED"
    else:
        code = prefix + "_HOST_ERROR"
    report = {"code": code, "kind": kind if kind in ("asr", "tts") else "unknown",
              "stage": identifier(stage) or "unknown", "exception": type(error).__name__}
    progress = getattr(error, "host_progress", None)
    if isinstance(progress, dict):
        report["host_progress"] = {key: value for key, value in progress.items()
            if key in ("sent_frames", "audio_frames", "transcript_chars", "reply_chars", "audio_bytes", "audio_done", "turn_done")
            and isinstance(value, (bool, int)) and 0 <= value <= 10_000_000}
    if status:
        report["http_status"] = status
    for side in ("rcvd", "sent"):
        close_code = getattr(getattr(error, side, None), "code", None)
        if isinstance(close_code, int) and 1000 <= close_code <= 4999:
            report["ws_" + side + "_code"] = close_code
    missing = identifier(getattr(error, "name", None))
    if missing:
        report["missing_symbol"] = missing
    # Function/file/line only: traceback.format_exc() would leak provider URLs,
    # request headers and user text through exception strings or source lines.
    report["frames"] = [{"file": Path(f.filename.replace("\\", "/")).name,
                         "line": f.lineno, "function": f.name}
                        for f in traceback.extract_tb(error.__traceback__)[-8:]]
    cause = error.__cause__ or error.__context__
    if cause:
        report["cause_type"] = type(cause).__name__
    return report


def log_failure(report):
    import json

    line = "语音桥语音调用失败（已脱敏）：" + json.dumps(report, ensure_ascii=False)
    try:
        (_logger or logging.getLogger("neko_voice_bridge")).error(line)
    except Exception:
        pass
