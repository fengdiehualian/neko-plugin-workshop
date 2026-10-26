"""Pinned optional OpenCV wheel, installed outside both plugin and N.E.K.O.

No pip invocation through the frozen host executable. A wheel is a standard ZIP;
only the verified official distribution is unpacked, with traversal/symlink
checks. The dependency is loaded in this plugin process, never installed globally.
"""

from __future__ import annotations

import importlib
import json
import os
import struct
import sys
import threading
import urllib.request
import zipfile
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

VERSION = "4.12.0.88"
WHEEL_URL = "https://files.pythonhosted.org/packages/f2/35/0858e9e71b36948eafbc5e835874b63e515179dc3b742cbe3d76bc683439/opencv_python_headless-4.12.0.88-cp37-abi3-win_amd64.whl"
WHEEL_SHA256 = "86b413bdd6c6bf497832e346cd5371995de148e579b9774f8eba686dee3f5528"
WHEEL_SIZE = 38923559


class VisionMatchRuntime:
    def __init__(self, *, root: Path):
        self.root = root / "vision-runtime" / ("opencv-" + VERSION + "-win-amd64")
        self._module: Any = None
        self._dll_handles: list[Any] = []
        self._lock = threading.RLock()
        self._state = "missing"
        self._message = "头像匹配组件尚未准备，文字识别仍可使用"
        self._downloaded = 0

    def load(self):
        with self._lock:
            if self._module is not None:
                return self._module
            try:
                module = importlib.import_module("cv2")
            except ImportError:
                if not (self.root / "INSTALL.json").is_file():
                    raise ImportError("头像匹配组件尚未下载") from None
                path = str(self.root)
                inserted = path not in sys.path
                if inserted:
                    sys.path.insert(0, path)
                try:
                    importlib.invalidate_caches()
                    if hasattr(os, "add_dll_directory") and not self._dll_handles:
                        self._dll_handles.append(os.add_dll_directory(str(self.root / "cv2")))
                    module = importlib.import_module("cv2")
                finally:
                    if inserted:
                        sys.path.remove(path)
            if not hasattr(module, "SIFT_create"):
                raise ImportError("视觉匹配运行库不支持所需特征识别")
            self._module = module
            return module

    def status(self):
        try:
            module = self.load()
            return {"available": True, "state": "ready", "message": "头像匹配组件已就绪", "version": module.__version__}
        except Exception as exc:
            message = self._message
            if self._state == "installing":
                message = f"正在下载头像匹配组件：{self._downloaded / 1024 / 1024:.1f} / {WHEEL_SIZE / 1024 / 1024:.1f} MB，存放在插件外部"
            return {
                "available": False,
                "state": self._state,
                "message": message,
                "downloaded_bytes": self._downloaded,
                "total_bytes": WHEEL_SIZE,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def install(self):
        if self.status()["available"]:
            return self.status()
        if sys.platform != "win32" or struct.calcsize("P") != 8:
            self._state = "error"
            self._message = "当前平台需自行提供 OpenCV；文字识别仍可使用"
            return self.status()
        self._state = "installing"
        self._downloaded = 0
        self._message = "正在下载头像匹配组件（约 38 MB），存放在插件外部"
        try:
            request = urllib.request.Request(WHEEL_URL, headers={"User-Agent": "NEKO-hsr-vision-runtime/0.9"})
            chunks = []
            with urllib.request.urlopen(request, timeout=45) as response:
                while chunk := response.read(256 * 1024):
                    chunks.append(chunk)
                    self._downloaded += len(chunk)
                    if self._downloaded > WHEEL_SIZE:
                        raise ValueError("视觉匹配组件大小不符")
            data = b"".join(chunks)
            if len(data) != WHEEL_SIZE or sha256(data).hexdigest() != WHEEL_SHA256:
                raise ValueError("视觉匹配组件校验失败")
            self._unpack(data)
            self.load()
            self._state = "ready"
        except Exception:
            self._state = "error"
            self._message = "头像匹配组件准备失败，请点击‘更新资料’重试；文字识别仍可使用"
        return self.status()

    def _unpack(self, data: bytes):
        self.root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 160 * 1024 * 1024:
                raise ValueError("视觉匹配组件解压大小异常")
            for item in archive.infolist():
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("视觉匹配组件包含非法路径")
                if not path.parts or not (path.parts[0] == "cv2" or path.parts[0].startswith("opencv_python_headless")):
                    raise ValueError("视觉匹配组件包含意外文件")
            archive.extractall(self.root)
        if not (self.root / "cv2" / "__init__.py").is_file():
            raise ValueError("视觉匹配组件内容缺失")
        (self.root / "INSTALL.json").write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "url": WHEEL_URL,
                    "sha256": WHEEL_SHA256,
                    "license": "Apache-2.0; included third-party license files retained",
                }
            ),
            encoding="utf-8",
        )
