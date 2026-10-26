"""Local release checks in disposable snapshots, never the real host.

The release gate skips host-Python tests; pytest uses the plugin dev Python.
GitHub identity, tags and remote Market CI require separate verification.
Ruff uses the pinned Market version from an offline uv cache.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES = ("pytest", "ruff", "check", "release", "hosted-tsx")


def gate_command(name: str, host: Path) -> list[str]:
    if name == "ruff":
        uvx = shutil.which("uvx")
        if not uvx:
            raise SystemExit("uvx required; prewarm with uvx ruff==0.12.4 --version")
        return [
            uvx, "--offline", "ruff==0.12.4", "check", "--ignore-noqa", "--isolated",
            "--target-version", "py311", "--line-length", "120", "--select", "E4,E7,E9,F,I",
            "--exclude", "vendor,.venv,venv,.tmpgate,__pycache__,.pytest_cache,.ruff_cache", ".",
        ]
    if name not in GATES:
        raise ValueError(f"unknown gate: {name}")
    return [sys.executable, str(ROOT / "tools/host_isolation.py"), "--host-root", str(host), "--only", name]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", type=Path)
    parser.add_argument("--only", default=",".join(GATES))
    args = parser.parse_args()
    selected = [name.strip() for name in args.only.split(",")]
    if not selected or any(name not in GATES for name in selected):
        parser.error("unknown or empty gate")
    host = (args.host_root or Path(os.environ.get("NEKO_HOST_ROOT", ROOT.parent / "N.E.K.O"))).resolve()
    if not (host / "plugin/sdk").is_dir():
        parser.error("host SDK not found; pass --host-root")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    for name in selected:
        print(f"[GATE] {name}", flush=True)
        result = subprocess.run(gate_command(name, host), cwd=ROOT, env=env, timeout=1800, check=False)
        if result.returncode:
            return result.returncode
    print("[OK] Local gates passed; remote Market CI is not verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
