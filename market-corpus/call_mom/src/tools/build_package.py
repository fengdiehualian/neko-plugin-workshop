"""
构建 call_mom 的 .neko-plugin 安装包（无需 neko-plugin CLI / N.E.K.O 源码）。

复刻 plugin/neko_plugin_cli 的打包算法：
- manifest.toml  schema_version 1.0 / package_type plugin
- payload/plugins/call_mom/  插件运行文件（entry 改写为安装态约定 plugins.<id>:Class）
- payload/dependencies.toml  依赖清单（本插件无第三方依赖）
- payload/profiles/default.toml  默认 profile（enabled + auto_start + 业务配置）
- metadata.toml  payload 的 SHA-256（排序后 path + NUL + content + NUL）

产物自校验：重新打开 zip 复算哈希并核对 id 一致性。

用法：python tools/build_package.py
输出：dist/call_mom.neko-plugin
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tomllib
import unicodedata
import zipfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = PLUGIN_DIR / "dist"

# 打包进 payload 的源文件（相对插件根目录；目录会递归）。
# tools/ 与 dist/ 是开发工具和产物，不属于插件。
PAYLOAD_ITEMS = ["plugin.toml", "config.example.toml", "__init__.py", "pyproject.toml", "README.md", "tests"]

# 安装态 entry 约定：包内 plugin.toml 的 entry 必须用 plugins.<id>:Class
# （对照本机已安装的 gocode_agent / pi_agent；源码树里才是 plugin.plugins.<id>）。
_ENTRY_RE = re.compile(r'^entry\s*=\s*"[^"]*"(.*)$', re.MULTILINE)

_MANIFEST = """\
schema_version = "1.0"
package_type = "plugin"

id = "{id}"
package_name = "{name}"
version = "{version}"
package_description = "{description}"
"""

_DEPENDENCIES = """\
schema_version = "1.0"

[plugins.call_mom]
python_requirements = []
host_python_requirements = []
plugin_dependencies = []
advanced_plugin_dependencies = []
vendor_path = "plugins/call_mom/vendor"
vendor_present = false
"""


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _payload_files(staging: Path) -> list[tuple[str, Path]]:
    entries = []
    for path in staging.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(staging).as_posix()
        entries.append((unicodedata.normalize("NFC", rel), path))
    return sorted(entries, key=lambda item: item[0])


def compute_payload_hash(staging: Path) -> str:
    """与 neko_plugin_cli compute_payload_hash / compute_archive_payload_hash 同算法。"""
    digest = hashlib.sha256()
    for rel, path in _payload_files(staging):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> None:
    manifest = tomllib.loads((PLUGIN_DIR / "plugin.toml").read_text(encoding="utf-8"))["plugin"]
    plugin_id = manifest["id"]
    version = manifest["version"]
    assert plugin_id == PLUGIN_DIR.name, "文件夹名必须与插件 id 一致"

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    staging = DIST_DIR / "_staging"
    payload_plugins = staging / "payload" / "plugins" / plugin_id
    payload_plugins.mkdir(parents=True)

    # 1) 插件文件 → payload/plugins/call_mom/（entry 改写为安装态约定）
    for item in PAYLOAD_ITEMS:
        src = PLUGIN_DIR / item
        dst = payload_plugins / item
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    plugin_toml_path = payload_plugins / "plugin.toml"
    text = plugin_toml_path.read_text(encoding="utf-8")
    entry = f"plugins.{plugin_id}:{manifest['entry'].rsplit(':', 1)[-1]}"
    text, count = _ENTRY_RE.subn(f'entry = "{entry}"\\1', text, count=1)
    assert count == 1, "plugin.toml 中未找到 entry 行"
    plugin_toml_path.write_text(text, encoding="utf-8", newline="\n")

    # 2) payload/dependencies.toml 与 payload/profiles/default.toml
    (staging / "payload" / "dependencies.toml").write_text(_DEPENDENCIES, encoding="utf-8", newline="\n")
    config = tomllib.loads((PLUGIN_DIR / "config.example.toml").read_text(encoding="utf-8"))
    runtime = config.get("plugin_runtime", {})
    profile = [
        'name = "default"',
        f'enabled_plugins = ["{_escape(plugin_id)}"]',
        "",
        f"[plugin.{plugin_id}]",
        "enabled = true",
    ]
    if isinstance(runtime.get("auto_start"), bool):
        profile.append(f"auto_start = {str(runtime['auto_start']).lower()}")
    for section, values in config.items():
        if section == "plugin_runtime":
            continue
        profile.append("")
        profile.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, str):
                profile.append(f'{key} = "{_escape(value)}"')
            else:
                profile.append(f"{key} = {value}")
    profiles_dir = staging / "payload" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "default.toml").write_text("\n".join(profile).rstrip() + "\n", encoding="utf-8", newline="\n")

    # 3) manifest.toml + metadata.toml（payload 哈希）
    (staging / "manifest.toml").write_text(
        _MANIFEST.format(
            id=_escape(plugin_id),
            name=_escape(manifest["name"]),
            version=_escape(version),
            description=_escape(manifest.get("description", "")),
        ),
        encoding="utf-8",
        newline="\n",
    )
    payload_hash = compute_payload_hash(staging / "payload")
    (staging / "metadata.toml").write_text(
        "[payload]\n"
        'hash_algorithm = "sha256"\n'
        f'hash = "{payload_hash}"\n'
        "\n"
        "[source]\n"
        'kind = "local"\n'
        f'paths = ["{_escape(plugin_id)}"]\n',
        encoding="utf-8",
        newline="\n",
    )

    # 4) 打 zip（官方产物只含文件条目，按 arcname 排序）
    package_path = DIST_DIR / f"{plugin_id}.neko-plugin"
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel, path in _payload_files(staging):
            if not rel.startswith("payload/"):
                continue  # manifest.toml / metadata.toml 在下面单独写入
            archive.write(path, rel)
        for top in ("manifest.toml", "metadata.toml"):
            archive.write(staging / top, top)

    # 5) 自校验：重开 zip 复算哈希 + 核对结构规则
    with zipfile.ZipFile(package_path) as archive:
        names = archive.namelist()
        assert "manifest.toml" in names and "metadata.toml" in names
        assert f"payload/plugins/{plugin_id}/plugin.toml" in names
        packaged = tomllib.loads(archive.read(f"payload/plugins/{plugin_id}/plugin.toml").decode("utf-8"))["plugin"]
        assert packaged["id"] == plugin_id, "包内 plugin.toml id 不一致"
        assert packaged["entry"] == entry, "包内 entry 改写失败"
        # compute_archive_payload_hash 的规范路径不含 "payload/" 前缀
        payload_entries = sorted(
            (
                unicodedata.normalize("NFC", n[len("payload/"):])
                for n in names
                if n.startswith("payload/") and not n.endswith("/")
            ),
        )
        body = b"".join(
            rel.encode("utf-8") + b"\0" + archive.read(f"payload/{rel}") + b"\0"
            for rel in payload_entries
        )
        zip_hash = hashlib.sha256(body).hexdigest()
        meta = tomllib.loads(archive.read("metadata.toml").decode("utf-8"))
        assert meta["payload"]["hash"] == payload_hash == zip_hash, "payload 哈希不一致"

    shutil.rmtree(staging)
    print(f"OK {package_path}  payload_sha256={payload_hash}  entry={entry}")


if __name__ == "__main__":
    main()
