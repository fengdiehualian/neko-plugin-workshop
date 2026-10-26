"""出可导入的 `.neko-plugin` 包（v0.16.0 起有这个脚本）。

**为什么需要它**：0.13.0~0.15.0 四个包都是手抄 zip 出来的，抄漏过一次关键字段——
`payload/profiles/default.toml` 里 `[plugin.sticker_manager]` 被写成
`enabled = true` 后紧跟 `enabled = false`（TOML 取后者），于是每次重装宿主都把插件
钉成"只注册不启动"，主人实机每次都得手动点启动。打包这一步一旦靠手工，就会把这种
错复制四轮。现在把选材规则、包内三份 toml 与载荷哈希算法都钉在这一个文件里。

用法（在插件仓根）：

    .venv/Scripts/python.exe tools/build_package.py            # 出到 ../dist/
    .venv/Scripts/python.exe tools/build_package.py --out /tmp

元数据在实际发行文件的临时副本上由宿主官方 probe 自动生成并验证，
不再复制开发目录的 plugin.meta.json。宿主默认在 ../N.E.K.O，
可用 --host-root 或 NEKO_HOST_ROOT 指定。宿主 Python 不可用或探测失败时拒绝出包。

载荷哈希与宿主/官方 CLI 同算法：NFC 规范化后的 posix 相对路径（去掉 `payload/` 前缀）
按大小写敏感排序，每条贡献 `path + NUL + 内容 + NUL`。改了算法宿主会判包损坏。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from tomllib import loads as toml_loads

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ID = "sticker_manager"
# 载荷目录前缀：宿主安装时按 `payload/plugins/<id>/` 解包。
PLUGIN_PREFIX = f"payload/plugins/{PACKAGE_ID}"

# 进包的规则是白名单，不是黑名单——新加的调试脚本/测试/文档不该跟着上路。
INCLUDE_DIRS = ("core", "services", "i18n")
INCLUDE_SUFFIXES = {"core": {".py"}, "services": {".py"}, "i18n": {".json"}, "ui": {".ts", ".tsx"}}
UI_ENTRIES = (
    "ui/panel.tsx",
    "ui/shared.ts",
    "ui/preview.ts",
    "ui/library_model.ts",
    "ui/components",
)
ROOT_FILES = (
    "__init__.py",
    "plugin.toml",
    "pyproject.toml",
    "config.example.toml",
    "README.md",
    "LICENSE",
    "NOTICE",
    "MEDIA_NOTICE.md",
)
OFFICIAL_MANIFEST = "official/official_pack.zip.parts.json"
_EXCLUDE_NAMES = {"__pycache__", ".ruff_cache", ".pytest_cache"}


def collect_plugin_files() -> list[Path]:
    """要进包的插件源文件（相对仓根的 posix 路径），排序稳定以便复算哈希。"""
    picked: list[Path] = []

    def keep(path: Path) -> bool:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError(f"Refusing linked build input: {path.relative_to(ROOT)}")
        parts = set(path.relative_to(ROOT).parts)
        return not (parts & _EXCLUDE_NAMES)

    def add_dir(directory: Path) -> None:
        if not directory.is_dir():
            return
        if not keep(directory):
            return
        for child in sorted(directory.rglob("*")):
            allowed = INCLUDE_SUFFIXES[directory.relative_to(ROOT).parts[0]]
            if child.is_file() and child.suffix in allowed and keep(child):
                picked.append(child)

    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file() and keep(path):
            picked.append(path)
    for name in INCLUDE_DIRS:
        add_dir(ROOT / name)
    for name in UI_ENTRIES:
        target = ROOT / name
        if target.exists() and not keep(target):
            continue
        if target.is_dir():
            add_dir(target)
        elif target.is_file():
            picked.append(target)
    official = ROOT / OFFICIAL_MANIFEST
    keep(official)
    if not official.is_file():
        raise ValueError("Missing official archive parts manifest; run tools/split_official_pack.py")
    manifest = json.loads(official.read_text(encoding="utf-8"))
    for name in [official.name, *(row["name"] for row in manifest["parts"])]:
        if Path(name).name != name or not name.startswith("official_pack.zip.") or any(c in name for c in "/\\:"):
            raise ValueError("Unsafe official archive part name")
        path = official.parent / name
        if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError(f"Missing or oversized official archive part: {name}")
        if keep(path):
            picked.append(path)
    return sorted(set(picked), key=lambda p: p.relative_to(ROOT).as_posix())


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def manifest_toml() -> str:
    """包清单：名字/版本/描述一律取自 plugin.toml，杜绝第二处真值。"""
    meta = toml_loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))["plugin"]
    return "\n".join(
        [
            'schema_version = "1.0"',
            'package_type = "plugin"',
            "",
            f'id = "{_escape(str(meta["id"]))}"',
            f'package_name = "{_escape(str(meta["name"]))}"',
            f'version = "{_escape(str(meta["version"]))}"',
            f'package_description = "{_escape(str(meta.get("description", "")))}"',
            "",
        ]
    )


def dependencies_toml() -> str:
    return "\n".join(
        [
            'schema_version = "1.0"',
            "",
            f"[plugins.{PACKAGE_ID}]",
            "python_requirements = []",
            "host_python_requirements = []",
            "plugin_dependencies = []",
            "advanced_plugin_dependencies = []",
            f'vendor_path = "plugins/{PACKAGE_ID}/vendor"',
            "vendor_present = false",
            "",
        ]
    )


def _inline(table: dict) -> str:
    parts: list[str] = []
    for key, value in table.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, str):
            text = f'"{_escape(value)}"'
        else:
            text = repr(value)
        parts.append(f"{key} = {text}")
    return "{ " + ", ".join(parts) + " }"


def profile_toml() -> str:
    """装机默认档：业务段的数字全部取自 plugin.toml（三处同源的第三源在这里也成立）。

    `[plugin.sticker_manager]` 只写一次 `enabled`/`auto_start`——重复键取后者，
    那正是 0.13.0~0.15.0 把插件钉成"只注册不启动"的地方。
    """
    section = toml_loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))["sticker_manager"]
    lines = [
        'name = "default"',
        f'enabled_plugins = ["{PACKAGE_ID}"]',
        "",
        f"[plugin.{PACKAGE_ID}]",
        "enabled = true",
        "auto_start = true",
    ]
    for key in ("send", "storage", "awareness"):
        table = section.get(key)
        if isinstance(table, dict) and table:
            lines.append(f"{key} = {_inline(table)}")
    lines.append("")
    return "\n".join(lines)


def payload_hash(entries: list[tuple[str, bytes]]) -> str:
    """`entries` 的键必须是**去掉 payload/ 前缀**的 posix 相对路径。"""
    digest = hashlib.sha256()
    ordered = sorted(entries, key=lambda item: unicodedata.normalize("NFC", item[0]))
    for relative, content in ordered:
        digest.update(unicodedata.normalize("NFC", relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def metadata_toml(hash_value: str) -> str:
    return "\n".join(
        [
            "[payload]",
            'hash_algorithm = "sha256"',
            f'hash = "{hash_value}"',
            "",
            "[source]",
            'kind = "local"',
            f'paths = ["{PACKAGE_ID}"]',
            "",
        ]
    )


def stage_plugin_entries(files: list[Path], host_root: Path) -> list[tuple[str, bytes]]:
    """Probe the shipped tree with the host SDK, not the development tree."""
    python = host_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise SystemExit(f"[FAIL] Host Python not found: {python}; pass --host-root")
    spec = importlib.util.spec_from_file_location("sticker_host_isolation", Path(__file__).with_name("host_isolation.py"))
    isolation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolation)
    with isolation.isolated_host(host_root) as sandbox, tempfile.TemporaryDirectory(
        prefix="sticker-package-", dir=sandbox.root,
    ) as temp:
        staged = Path(temp) / PACKAGE_ID
        for source in files:
            if source.name == "plugin.meta.json":
                continue
            target = staged / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        code = (
            "import json,os,sys; "
            "assert getattr(sys,'_sticker_isolation_root',None)=="
            "os.path.normcase(os.path.realpath(os.environ['STICKER_ISOLATION_ROOT'])); "
            "from pathlib import Path; "
            "from plugin.neko_plugin_cli.core.metadata_probe import derive_plugin_metadata; "
            "from plugin.server.infrastructure.packaged_metadata import read_packaged_metadata; "
            "p=Path(sys.argv[1]); m=derive_plugin_metadata(p, source_only=True); "
            "(p/'plugin.meta.json').write_bytes((json.dumps(m,ensure_ascii=False,indent=2)+'\\n').encode('utf-8')); "
            "assert read_packaged_metadata(p) is not None, 'host rejected staged metadata'"
        )
        result = subprocess.run(
            [str(python), "-c", code, str(staged)], cwd=sandbox.snapshot, env=sandbox.env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        if result.returncode:
            raise SystemExit(f"[FAIL] Staged metadata probe failed:\n{result.stdout}\n{result.stderr}")
        payload = (staged / "plugin.meta.json").read_bytes()
        expected = {p.relative_to(ROOT).as_posix() for p in files if p.name != "plugin.meta.json"}
        if set(json.loads(payload)["source_files"]) != expected:
            raise SystemExit("[FAIL] Metadata file list does not match the payload")
        # Archive the same bytes that were probed, even if source files change during the build.
        entries = [(f"plugins/{PACKAGE_ID}/{name}", (staged / name).read_bytes()) for name in sorted(expected)]
        entries.append((f"plugins/{PACKAGE_ID}/plugin.meta.json", payload))
        return entries


def build(out_dir: Path, *, host_root: Path | None = None) -> Path:
    """装配 zip。归档名与哈希用的"相对路径"是两回事，这里分开算清楚：

    - 归档名：`payload/plugins/<id>/<仓内相对路径>`、`payload/dependencies.toml`、
      `payload/profiles/default.toml`；
    - 哈希键：**去掉 `payload/` 前缀**后的路径（所以插件文件带 `plugins/<id>/` 一段）。
      少算这一截，宿主安装时会判包损坏——手抄那四轮就是这么差点栽的。
    """
    plugin_files = collect_plugin_files()
    plugin_entries = stage_plugin_entries(
        plugin_files, (host_root or Path(os.environ.get("NEKO_HOST_ROOT", ROOT.parent / "N.E.K.O"))).resolve()
    )
    generated = [
        ("dependencies.toml", dependencies_toml().encode("utf-8")),
        ("profiles/default.toml", profile_toml().encode("utf-8")),
    ]
    hash_entries = plugin_entries + generated
    version = toml_loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))["plugin"]["version"]
    target = out_dir / f"{PACKAGE_ID}_v{version}.neko-plugin"
    out_dir.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, content in hash_entries:
            archive.writestr(f"payload/{relative}", content)
        archive.writestr("manifest.toml", manifest_toml())
        archive.writestr("metadata.toml", metadata_toml(payload_hash(hash_entries)))
    temporary.replace(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--host-root", help="Host source directory with .venv and plugin/sdk")
    parser.add_argument("--out", default=str(ROOT.parent / "dist"), help="输出目录（默认 ../dist）")
    args = parser.parse_args()
    target = build(Path(args.out), host_root=Path(args.host_root) if args.host_root else None)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        raw = sum(info.file_size for info in archive.infolist())
    print(f"[OK] {target}")
    print(f"     entries={len(names)} raw_bytes={raw / 1024 / 1024:.1f} MiB")
    print(f"     sha256={hashlib.sha256(target.read_bytes()).hexdigest()[:16]}")
    required = (
        f"payload/plugins/{PACKAGE_ID}/services/turns.py",
        f"payload/plugins/{PACKAGE_ID}/core/eagerness.py",
        f"payload/{'profiles/default.toml'}",
    )
    missing = [want for want in required if want not in names]
    if missing:  # 新文件没进包=白干；宁可红也不出"装上没变化"的包
        print(f"[FAIL] 包内缺: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
