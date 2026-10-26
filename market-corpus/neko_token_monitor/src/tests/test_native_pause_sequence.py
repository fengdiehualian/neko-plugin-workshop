import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("cc"), reason="C compiler unavailable")
def test_pause_protocol_handles_accepted_but_ignored_clicks(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "pause-sequence"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(root / "native"),
                    str(root / "tests/native_pause_sequence.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=10)
