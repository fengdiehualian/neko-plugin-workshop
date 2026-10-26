from __future__ import annotations

import os
import subprocess
import tempfile


def run_node_script(
    node_path: str,
    script: str,
    **kwargs,
) -> subprocess.CompletedProcess[str]:
    """Run a generated JavaScript harness from a UTF-8 temporary file."""

    merged = dict(kwargs)
    merged.setdefault("text", True)
    merged["encoding"] = "utf-8"
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".js",
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        return subprocess.run([node_path, script_path], **merged)
    finally:
        os.unlink(script_path)
