from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_writer_power_analysis_testpkg"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

nodes_package = types.ModuleType(f"{PACKAGE}.nodes")
nodes_package.__path__ = [str(ROOT / "nodes")]
sys.modules[f"{PACKAGE}.nodes"] = nodes_package

load_module(f"{PACKAGE}.nodes.constants", ROOT / "nodes" / "constants.py")
load_module(f"{PACKAGE}.nodes.models", ROOT / "nodes" / "models.py")
utils = load_module(f"{PACKAGE}.nodes.utils", ROOT / "nodes" / "utils.py")


def test_build_model_list_url_accepts_absolute_url() -> None:
    assert (
        utils.build_model_list_url("https://ignored.example/v1", "https://models.example/list")
        == "https://models.example/list"
    )


def test_build_model_list_url_joins_relative_path() -> None:
    assert utils.build_model_list_url("https://api.example/v1/", "models") == "https://api.example/v1/models"


def test_normalize_analysis_rejects_missing_dimensions() -> None:
    try:
        utils.normalize_analysis({})
    except TypeError as exc:
        assert "dimensions" in str(exc)
    else:
        raise AssertionError("normalize_analysis should require dimensions")


def test_calculate_final_score_handles_nan() -> None:
    dimensions = [{"name": "base", "score": math.nan}]
    assert utils.calculate_final_score(dimensions) == 0.0


if __name__ == "__main__":
    test_build_model_list_url_accepts_absolute_url()
    test_build_model_list_url_joins_relative_path()
    test_normalize_analysis_rejects_missing_dimensions()
    test_calculate_final_score_handles_nan()
