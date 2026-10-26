"""Build the separately installed Windows assistant and its release manifest."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def assistant_version(root: Path) -> str:
    """Read helper metadata without importing or starting the capture service."""
    module = ast.parse((root / "assistant_runtime" / "server.py").read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SERVICE_VERSION" for t in node.targets):
            return str(ast.literal_eval(node.value))
    raise ValueError("视觉助手未声明 SERVICE_VERSION")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=None)
    parser.add_argument("--output-dir", default="dist",
        help="Plugin-relative candidate artifact directory; does not install or publish.")
    parser.add_argument("--repository", default="WhiteFrostune/n.e.k.o_plugin_hsr_companion")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = assistant_version(root)
    dist = (root / args.output_dir).resolve()
    if not dist.is_relative_to(root.resolve()) or dist == root.resolve():
        raise ValueError("助手构建产物必须在插件的独立子目录中")
    tag = args.tag or f"v{version}"
    dist.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--name", "hsr-vision-assistant", "--distpath", str(dist),
            "--workpath", str(root / "build" / "assistant" / version),
            "--specpath", str(root / "build"),
            "--paths", str(root / "assistant_runtime"), "--hidden-import", "window_capture",
            "--hidden-import", "psutil", "--hidden-import", "win32gui",
            "--hidden-import", "win32process",
            "--hidden-import", "PIL.Image", "--collect-all", "windows_capture",
            "--hidden-import", "numpy", "--hidden-import", "cv2",
            str(root / "assistant_runtime" / "server.py"),
        ],
        cwd=root,
        check=True,
    )
    # Static analysis of a standalone entrypoint does not always resolve its
    # sibling imports. Refuse to produce an apparently valid release without
    # the native-capture adapter in the executable's Python archive.
    from PyInstaller.archive.readers import CArchiveReader

    executable_archive = CArchiveReader(str(dist / "hsr-vision-assistant.exe"))
    python_archives = [name for name in executable_archive.toc if name.lower().endswith(".pyz")]
    if not python_archives or "window_capture" not in executable_archive.open_embedded_archive(python_archives[0]).toc:
        raise RuntimeError("独立助手缺少窗口采集模块，停止生成安装包")
    archive = dist / "hsr-vision-assistant-win-x64.zip"
    versions = {name: importlib.metadata.version(name) for name in (
        "pyinstaller", "pillow", "psutil", "pywin32", "windows-capture", "numpy", "opencv-python-headless",
    )}
    source_hashes = {name: hashlib.sha256((root / "assistant_runtime" / name).read_bytes()).hexdigest()
                     for name in ("server.py", "window_capture.py")}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(dist / "hsr-vision-assistant.exe", "hsr-vision-assistant.exe")
        bundle.write(root / "LICENSE.md", "LICENSE.md")
        bundle.write(root / "assistant_runtime" / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
        bundle.writestr("BUILD_VERSIONS.json", json.dumps(
            {"python": sys.version, **versions, "source_sha256": source_hashes}, indent=2))
        for name in versions:
            distribution = importlib.metadata.distribution(name)
            for item in distribution.files or []:
                if any(term in item.name.lower() for term in ("license", "licence", "copying")):
                    source = Path(distribution.locate_file(item))
                    if source.is_file():
                        bundle.write(source, f"licenses/{name}/{str(item).replace('../', '')}")
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
        if python_license.is_file():
            bundle.write(python_license, "licenses/CPython-LICENSE.txt")
    manifest = {
        "version": version,
        "protocol_version": "1",
        "platform": "windows-x64",
        "archive_url": f"https://github.com/{args.repository}/releases/download/{tag}/{archive.name}",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "executable": "hsr-vision-assistant.exe",
        "image_storage": False,
        "capture_backend": "windows_graphics_capture",
        "isolated_window": True,
        "focus_required": False,
        "native_resolution": True,
        "source_sha256": source_hashes,
    }
    (dist / "hsr-vision-assistant-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Local testing installs exactly the same archive, without publishing a release.
    local_install = {
        **manifest,
        "executable": f"{version}-native-hud/hsr-vision-assistant.exe",
        "source": "local-build",
    }
    (dist / "hsr-vision-assistant-local-install.json").write_text(json.dumps(local_install, indent=2), encoding="utf-8")
    print(f"Built separate assistant: {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
