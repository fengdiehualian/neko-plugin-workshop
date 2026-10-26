"""Load the dependency-free bundled archive reader for standalone tools."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "sticker_bundled_pack_tool", Path(__file__).resolve().parents[1] / "services/bundled_pack.py",
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)
open_pack = reader.open_pack
