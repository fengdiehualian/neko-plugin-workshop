import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("xcrun"), reason="macOS native geometry")
def test_moved_model_geometry_in_native_implementation(tmp_path):
    root = Path(__file__).resolve().parent.parent
    binary = tmp_path / "host-geometry"
    subprocess.run(["xcrun", "clang", "-fobjc-arc", "-framework", "Cocoa", "-I", str(root / "native"),
                    str(root / "tests/native_host_geometry.m"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=15)
