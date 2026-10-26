"""Run SDK checks and metadata probes without writing to the source host."""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE_DIRS = ("plugin", "config", "utils")
SOURCE_SUFFIXES = {".py", ".json", ".toml", ".ts", ".tsx", ".mjs"}
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".tmpgate", "tests", "vendor", "target", "logs",
    ".benchmarks", ".mypy_cache",
}
FRONTEND_FILES = (
    "frontend/plugin-manager/scripts/check-hosted-tsx.mjs",
    "frontend/plugin-manager/src/components/plugin/hosted/hostedTsxModule.mjs",
)

# Python descendants inherit this via PYTHONPATH, including the SDK's scanner.
# The host's logger can otherwise delete old files in its source logs directory.
WRITE_GUARD = r'''import os
import sys

_root = os.path.normcase(os.path.realpath(os.environ["STICKER_ISOLATION_ROOT"]))
_write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

def _allowed(path):
    if isinstance(path, int) or path is None:
        return
    text = os.fsdecode(path)
    if text.casefold() in {"nul", "\\\\.\\nul", "/dev/null"}:
        return
    resolved = os.path.normcase(os.path.realpath(text))
    try:
        inside = os.path.commonpath((_root, resolved)) == _root
    except ValueError:
        inside = False
    if not inside:
        raise PermissionError("isolated host refused filesystem write: " + resolved)

def _audit(event, args):
    if event == "open":
        mode = args[1]
        flags = args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or flags & _write_flags:
            _allowed(args[0])
    elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "os.truncate"}:
        _allowed(args[0])
    elif event in {"os.rename", "os.link"}:
        _allowed(args[0])
        _allowed(args[1])
    elif event == "os.symlink":
        _allowed(args[1])
    elif event == "socket.connect":
        caller = sys._getframe(1).f_code
        if caller.co_name != "_fallback_socketpair" or os.path.basename(caller.co_filename) != "socket.py":
            raise PermissionError("isolated host refused network connection")

sys.addaudithook(_audit)
sys._sticker_isolation_root = _root
'''


def host_python(host_root: Path) -> Path:
    executable = host_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file():
        raise SystemExit(f"[FAIL] Host Python not found: {executable}; pass --host-root")
    return executable


def copy_host_sources(host_root: Path, destination: Path) -> None:
    """Copy regular SDK support source files, never a checkout or runtime data."""
    host_root = host_root.resolve()
    destination = destination.resolve()
    if destination == host_root or destination.is_relative_to(host_root):
        raise ValueError("isolation directory must be outside the source host")
    destination.mkdir(parents=True, exist_ok=True)
    for dirname in HOST_SOURCE_DIRS:
        source_root = host_root / dirname
        if not source_root.is_dir():
            continue
        for directory, dirs, files in os.walk(source_root, followlinks=False):
            relative_dir = Path(directory).relative_to(host_root)
            dirs[:] = [
                name for name in dirs
                if name not in EXCLUDED_DIRS
                and not (dirname == "plugin" and relative_dir == Path("plugin") and name == "plugins")
                and not (Path(directory) / name).is_symlink()
            ]
            for name in files:
                source = Path(directory) / name
                if source.is_symlink() or source.suffix not in SOURCE_SUFFIXES:
                    continue
                target = destination / source.relative_to(host_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    for relative in (*FRONTEND_FILES, "plugin/plugins/__init__.py"):
        source = host_root / relative
        if not source.is_file() or source.is_symlink():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (destination / "sitecustomize.py").write_text(WRITE_GUARD, encoding="utf-8")


def isolated_environment(snapshot: Path, root: Path) -> dict[str, str]:
    root = root.resolve()
    home = root / "home"
    local = home / "AppData" / "Local"
    roaming = home / "AppData" / "Roaming"
    data = root / "data"
    temp = root / "temp"
    for path in (home, local, roaming, data, temp):
        path.mkdir(parents=True, exist_ok=True)
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("NEKO_") and key not in {"PLUGIN_CONFIG_ROOT", "PYTHONPATH", "PYTHONHOME"}
    }
    env.update({
        "STICKER_ISOLATION_ROOT": str(root),
        "PYTHONPATH": str(snapshot.resolve()),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "NEKO_STORAGE_SELECTED_ROOT": str(data),
        "NEKO_STORAGE_ANCHOR_ROOT": str(data),
        "NEKO_LOG_LEVEL": "INFO",
        "NEKO_PLUGIN_METADATA_SCAN_TIMEOUT": "30",
        "PLUGIN_CONFIG_ROOT": str(root / "installed-plugins"),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(local),
        "APPDATA": str(roaming),
        "TEMP": str(temp),
        "TMP": str(temp),
    })
    return env


@contextlib.contextmanager
def isolated_host(host_root: Path, *, parent: Path | None = None) -> Iterator[SimpleNamespace]:
    executable = host_python(host_root)
    parent = parent or PLUGIN_ROOT / ".tmpgate"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="isolated-host-", dir=parent) as temporary:
        root = Path(temporary)
        snapshot = root / "host"
        copy_host_sources(host_root, snapshot)
        yield SimpleNamespace(
            root=root, snapshot=snapshot, python=executable,
            env=isolated_environment(snapshot, root),
        )


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, check=False, timeout=1800)
    if result.returncode:
        raise SystemExit(result.returncode)


def run_checks(host_root: Path, *, node_modules: Path, gates: list[str]) -> None:
    if not (node_modules / "typescript").is_dir():
        node_modules = host_root / "frontend" / "plugin-manager" / "node_modules"
    with isolated_host(host_root) as sandbox:
        mounted = sandbox.snapshot / "plugin" / "plugins" / "sticker_manager"
        shutil.copytree(
            PLUGIN_ROOT, mounted,
            ignore=shutil.ignore_patterns(*EXCLUDED_DIRS, "dist", "build"),
        )
        # Tests are development inputs; the runtime snapshot deliberately skips
        # host tests, but this plugin's own tests still belong in the check copy.
        shutil.copytree(PLUGIN_ROOT / "tests", mounted / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        assertion = (
            "import os,sys; "
            "assert getattr(sys,'_sticker_isolation_root',None)=="
            "os.path.normcase(os.path.realpath(os.environ['STICKER_ISOLATION_ROOT'])); "
            "from pathlib import Path; from utils.logger_config import _get_application_root; "
            f"assert _get_application_root().resolve()==Path({str(sandbox.snapshot)!r}).resolve()"
        )
        _run([str(sandbox.python), "-c", assertion], cwd=sandbox.snapshot, env=sandbox.env)
        for gate in gates:
            print(f"[GATE] {gate}", flush=True)
            if gate == "pytest":
                test_python = host_python(PLUGIN_ROOT)
                _run(
                    [str(test_python), "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
                     f"--basetemp={sandbox.root / 'pytest'}"],
                    cwd=mounted, env=sandbox.env,
                )
            elif gate == "check":
                _run(
                    [str(sandbox.python), "-m", "plugin.neko_plugin_cli", "check", "sticker_manager"],
                    cwd=sandbox.snapshot, env=sandbox.env,
                )
            elif gate == "hosted-tsx":
                # The primary test gate already ran with the plugin's dev Python.
                typescript = node_modules / "typescript"
                if not typescript.is_dir():
                    raise SystemExit(f"[FAIL] TypeScript dependency not found: {typescript}")
                manager = sandbox.snapshot / "frontend" / "plugin-manager"
                target = manager / "node_modules" / "typescript"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(typescript, target)
                node = shutil.which("node")
                if not node:
                    raise SystemExit("[FAIL] Node.js not found")
                _run(
                    [node, str(manager / "scripts" / "check-hosted-tsx.mjs"),
                     "plugin/plugins/sticker_manager"],
                    cwd=sandbox.snapshot, env=sandbox.env,
                )
            else:
                if gate != "release":
                    raise ValueError(f"unknown gate: {gate}")
                _run(
                    [str(sandbox.python), "-m", "plugin.neko_plugin_cli", "check", "-r",
                     "sticker_manager", "--skip-tests", "--target-dir", str(sandbox.root / "packages")],
                    cwd=sandbox.snapshot, env=sandbox.env,
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", type=Path, default=PLUGIN_ROOT.parent / "N.E.K.O")
    parser.add_argument("--node-modules", type=Path, default=PLUGIN_ROOT.parent / "dist/panel-study/node_modules")
    parser.add_argument("--only", default="pytest,check,hosted-tsx")
    args = parser.parse_args()
    run_checks(args.host_root.resolve(), node_modules=args.node_modules.resolve(), gates=args.only.split(","))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
