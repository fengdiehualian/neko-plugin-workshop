"""Describe whether the loaded panel, data layer, and model belong together."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def _file_name(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return PurePosixPath(text).name if text else ""


def runtime_sync_status(
    *,
    connected: bool,
    plugin_version: str,
    data_layer_version: str,
    expected_weights: str,
    actual_weights: str,
    expected_infer_mode: str = "",
    actual_infer_mode: str = "",
    fallback_from: str = "",
) -> dict[str, Any]:
    expected_model = _file_name(expected_weights)
    actual_model = _file_name(actual_weights)
    fallback_model = _file_name(fallback_from)
    expected_mode = str(expected_infer_mode or "").strip().lower()
    actual_mode = str(actual_infer_mode or "").strip().lower()

    status = "ok"
    message = "面板、陪伴助手和模型版本一致"
    if not connected:
        status = "waiting"
        message = "陪伴助手尚未连接，暂时无法核验运行版本"
    elif not data_layer_version:
        status = "mismatch"
        message = "陪伴助手未上报版本，可能仍在运行旧版本；请使用配套版本的陪伴助手，并关闭旧助手后重新启动"
    elif data_layer_version != plugin_version:
        status = "mismatch"
        message = (
            f"面板 v{plugin_version} 与陪伴助手 v{data_layer_version} 不一致；"
            "请使用配套版本的陪伴助手，并关闭旧助手后重新启动"
        )
    elif expected_mode and actual_mode != expected_mode:
        status = "mismatch"
        message = f"期望推理模式 {expected_mode}，实际运行 {actual_mode or '未知模式'}"
    elif expected_mode == "stub":
        pass
    elif expected_mode == "yolo" and not expected_model:
        status = "mismatch"
        message = "已选择 yolo 推理，但未找到期望模型"
    elif fallback_model:
        status = "mismatch"
        message = f"主模型 {fallback_model} 加载失败，当前已回退到 {actual_model or '未知模型'}"
    elif not actual_model:
        status = "mismatch"
        message = "陪伴助手未上报实际模型"
    elif expected_model and actual_model != expected_model:
        status = "mismatch"
        message = f"期望模型 {expected_model}，实际加载 {actual_model}"

    return {
        "status": status,
        "message": message,
        "plugin_version": plugin_version,
        "data_layer_version": data_layer_version,
        "expected_model": expected_model,
        "actual_model": actual_model,
        "expected_infer_mode": expected_mode,
        "actual_infer_mode": actual_mode,
    }
