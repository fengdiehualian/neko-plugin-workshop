from __future__ import annotations

import tomllib
from pathlib import Path

from neko_calabiqiu.build_info import DATA_LAYER_VERSION, PLUGIN_VERSION
from neko_calabiqiu.core.runtime_identity import runtime_sync_status


def test_build_version_matches_both_manifests():
    root = Path(__file__).resolve().parents[1]
    for manifest in (root / "plugin.toml", root / "config" / "plugin.toml"):
        data = tomllib.loads(manifest.read_text(encoding="utf-8-sig"))
        assert data["plugin"]["version"] == PLUGIN_VERSION == DATA_LAYER_VERSION


def test_runtime_sync_accepts_matching_data_layer_and_model():
    result = runtime_sync_status(
        connected=True,
        plugin_version="0.1.5",
        data_layer_version="0.1.5",
        expected_weights=r"D:\plugin\data_layer\weights\best-v8-640.nekomodel",
        actual_weights="data_layer/weights/best-v8-640.nekomodel",
        expected_infer_mode="yolo",
        actual_infer_mode="yolo",
    )

    assert result["status"] == "ok"
    assert result["expected_model"] == "best-v8-640.nekomodel"
    assert result["actual_model"] == "best-v8-640.nekomodel"


def test_runtime_sync_rejects_old_or_unversioned_data_layer():
    old = runtime_sync_status(
        connected=True,
        plugin_version="0.1.5",
        data_layer_version="0.1.4",
        expected_weights="best-v8-640.nekomodel",
        actual_weights="best-v8-640.nekomodel",
    )
    unversioned = runtime_sync_status(
        connected=True,
        plugin_version="0.1.5",
        data_layer_version="",
        expected_weights="best-v8-640.nekomodel",
        actual_weights="best-v8-640.nekomodel",
    )

    assert old["status"] == "mismatch"
    assert "v0.1.4" in old["message"]
    assert unversioned["status"] == "mismatch"
    assert "旧版本" in unversioned["message"]


def test_runtime_sync_reports_model_fallback():
    result = runtime_sync_status(
        connected=True,
        plugin_version="0.1.5",
        data_layer_version="0.1.5",
        expected_weights="best-v8-640.nekomodel",
        actual_weights="best.nekomodel",
        fallback_from="best-v8-640.nekomodel",
    )

    assert result["status"] == "mismatch"
    assert result["actual_model"] == "best.nekomodel"
    assert "回退" in result["message"]


def test_runtime_sync_accepts_matching_stub_mode_without_model_weights():
    result = runtime_sync_status(
        connected=True,
        plugin_version="0.1.5",
        data_layer_version="0.1.5",
        expected_weights="stub",
        actual_weights="",
        expected_infer_mode="stub",
        actual_infer_mode="stub",
    )

    assert result["status"] == "ok"


def test_runtime_sync_rejects_yolo_mode_without_expected_model():
    result = runtime_sync_status(
        connected=True,
        plugin_version="0.1.5",
        data_layer_version="0.1.5",
        expected_weights="",
        actual_weights="unexpected.onnx",
        expected_infer_mode="yolo",
        actual_infer_mode="yolo",
    )

    assert result["status"] == "mismatch"
    assert "未找到期望模型" in result["message"]
