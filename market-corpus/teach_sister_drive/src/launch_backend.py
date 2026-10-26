"""Explicit plugin-enabled alternative to `uv run python launcher.py`."""

import argparse
import os
import sys
from pathlib import Path


def source_root(explicit=""):
    candidates = [Path(explicit)] if explicit else [Path.cwd(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "launcher_core/runtime.py").is_file():
            return candidate.resolve()
    raise RuntimeError("请从 N.E.K.O 源码目录启动，或用 --neko-root 指定源码目录")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neko-root", default="")
    options, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    root = source_root(options.neko_root)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(root))
    os.chdir(root)
    from launcher_core.bootstrap import _ensure_utf8_filesystem_encoding

    _ensure_utf8_filesystem_encoding()
    from host_runtime import run_main_server
    from launcher_core import runtime

    # The scoped callback runs only in the main-server child. Keep all other
    # orchestration, shutdown and service startup in the stock launcher.
    os.environ["NEKO_MERGED"] = "0"
    runtime.run_main_server = run_main_server
    return runtime.start_launcher()


if __name__ == "__main__":
    raise SystemExit(main())
