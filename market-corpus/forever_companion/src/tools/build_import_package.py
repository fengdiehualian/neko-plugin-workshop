"""Run official packaging with isolated paths and verify the resulting archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path


def build_and_verify(plugin: Path, output: Path, *, check_only: bool = False) -> None:
    if os.environ.get("NEKO_ISOLATED_BUILD_ACTIVE") != "1":
        raise RuntimeError("packaging must run with the isolated bootstrap")

    from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir
    from plugin.neko_plugin_cli.core.archive_utils import (
        collect_plugin_folders,
        compute_archive_payload_hash,
        read_metadata,
        validate_archive_structure,
        validate_dependency_layout,
        validate_plugin_layout,
        verify_payload_hash,
    )
    from plugin.neko_plugin_cli.core.build import build_plugin

    issues = validate_plugin_dir(plugin, strict=True)
    errors = [message for level, message in issues if level == "error"]
    if errors:
        raise RuntimeError("\n".join(errors))
    print(json.dumps({"strict_check": "passed", "issues": issues}, ensure_ascii=False))
    if check_only:
        return
    source_manifest = tomllib.loads((plugin / "plugin.toml").read_text(encoding="utf-8"))
    plugin_id = source_manifest["plugin"]["id"]
    version = source_manifest["plugin"]["version"]
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing package: {output}")
    result = build_plugin(plugin, output)
    with zipfile.ZipFile(result.package_path) as archive:
        validate_archive_structure(archive)
        folders = collect_plugin_folders(archive)
        assert folders == [plugin_id], folders
        validate_plugin_layout(archive, folders)
        validate_dependency_layout(archive, folders)
        assert archive.testzip() is None, "ZIP CRC failed"
        assert verify_payload_hash(read_metadata(archive), compute_archive_payload_hash(archive)) is True
        manifest = tomllib.loads(archive.read("manifest.toml").decode("utf-8"))
        assert manifest["version"] == version and manifest["id"] == plugin_id
        prefix = f"payload/plugins/{plugin_id}/"
        entries = archive.namelist()
        assert prefix + "plugin.meta.json" in entries, "official metadata derivation failed"
        forbidden = {
            ".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__",
            "tests", "tools", "store.db", ".env", "data", "wallpapers",
            "temp", "staging", "backup", "backups", "published", "wallpaper_playback",
        }
        source_files = 0
        for name in entries:
            relative = Path(name)
            assert not forbidden.intersection(part.casefold() for part in relative.parts), name
            assert relative.suffix.lower() not in {
                ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo",
                ".mp4", ".webm", ".part", ".bin",
            }, name
            assert ".db-" not in relative.name.casefold(), name
            if name.startswith(prefix) and name != prefix + "plugin.meta.json":
                path = plugin / name.removeprefix(prefix)
                assert path.is_file() and path.read_bytes() == archive.read(name), name
                source_files += 1
        metadata = json.loads(archive.read(prefix + "plugin.meta.json"))
        assert metadata.get("handlers"), "package has no handler metadata"
    report = {
        "package": str(result.package_path),
        "version": version,
        "bytes": result.package_path.stat().st_size,
        "entries": len(entries),
        "source_files_matched": source_files,
        "zip_crc": "passed",
        "payload_hash_verified": True,
        "sha256": hashlib.sha256(result.package_path.read_bytes()).hexdigest(),
        "payload_sha256": result.payload_hash,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    plugin = Path(__file__).resolve().parents[1]
    host = args.host.resolve()
    output = args.output.resolve()
    if args.worker:
        build_and_verify(plugin, output, check_only=args.check_only)
        return 0
    python = host / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise FileNotFoundError(python)
    with tempfile.TemporaryDirectory(prefix="neko-companion-build-") as temporary:
        sandbox = Path(temporary).resolve()
        env = os.environ.copy()
        env.update({
            "NEKO_PACKAGE_SANDBOX": str(sandbox),
            "NEKO_STORAGE_SELECTED_ROOT": str(sandbox / "runtime"),
            "NEKO_STORAGE_ANCHOR_ROOT": str(sandbox / "runtime"),
            "LOCALAPPDATA": str(sandbox / "local"),
            "APPDATA": str(sandbox / "roaming"),
            "USERPROFILE": str(sandbox / "home"),
            "HOME": str(sandbox / "home"),
            "PLUGIN_CONFIG_ROOT": str(sandbox / "exec" / "plugins"),
            "PACKAGE_PROFILES_ROOT": str(sandbox / "profiles"),
            "PLUGIN_PACKAGES_ROOT": str(sandbox / "packages"),
            "NEKO_LOG_DIR": str(sandbox / "logs"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join((str(plugin / "tools" / "package_bootstrap"), str(host))),
        })
        for directory in ("runtime", "local", "roaming", "home", "logs"):
            (sandbox / directory).mkdir()
        command = [
            str(python), "-B", str(Path(__file__).resolve()),
            "--host", str(host), "--output", str(output), "--worker",
        ]
        if args.check_only:
            command.append("--check-only")
        return subprocess.run(command, cwd=sandbox, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
