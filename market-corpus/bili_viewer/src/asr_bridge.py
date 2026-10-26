"""bili_viewer ASR 外部桥（v0.7.14）。

N.E.K.O 打包运行时（projectneko_server.exe）没有 faster-whisper 依赖，
插件进程内无法加载 Whisper 模型。本脚本由外部 Python 解释器执行
（例如 C:\\Users\\<user>\\N.E.K.O\\.venv\\Scripts\\python.exe，已装 faster-whisper），
以常驻进程方式提供转写服务：

协议（stdin/stdout 逐行 JSON）：
  请求  {"op":"transcribe","path":"<wav 绝对路径>","lang":"zh"}
  响应  {"ok":true,"text":"..."} 或 {"ok":false,"error":"..."}

模型懒加载并缓存于进程内；环境变量 HF_ENDPOINT（镜像）与
HF_HUB_DISABLE_XET 由本脚本兜底设置。失败只输出错误响应，绝不崩溃。
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

_model = None
_model_err: str | None = None
_MAX_TEXT = 500


def _get_model():
    """懒加载 WhisperModel（tiny/int8/cpu）。"""
    global _model, _model_err
    if _model is not None:
        return _model
    if _model_err:
        raise RuntimeError(_model_err)
    try:
        from faster_whisper import WhisperModel  # 外部 python 已安装（1.2.1）
        _model = WhisperModel("tiny", device="cpu", compute_type="int8")
        return _model
    except Exception as exc:  # noqa: BLE001
        _model_err = str(exc)[:300]
        raise


def main() -> None:
    for line in sys.stdin:
        line = (line or "").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        op = req.get("op")
        if op != "transcribe":
            sys.stdout.write(json.dumps({"ok": False, "error": "unknown-op"}) + "\n")
            sys.stdout.flush()
            continue
        path = req.get("path") or ""
        if not path or not os.path.isfile(path):
            sys.stdout.write(json.dumps({"ok": False, "error": "file-not-found"}) + "\n")
            sys.stdout.flush()
            continue
        try:
            model = _get_model()
            segs, _info = model.transcribe(
                path, language=req.get("lang") or "zh", vad_filter=False
            )
            text = "".join(s.text for s in segs).strip()
            sys.stdout.write(json.dumps({"ok": True, "text": text[:_MAX_TEXT]}) + "\n")
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)[:200]}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
