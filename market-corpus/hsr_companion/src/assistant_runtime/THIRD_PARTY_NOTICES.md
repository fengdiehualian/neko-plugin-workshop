# 星铁后台感知助手第三方组件

v0.9.3 Windows 助手候选由以下固定版本组件构建。独立归档保留本文件、
实际构建版本记录（BUILD_VERSIONS.json）及组件许可证文件（licenses/）。

- CPython 3.11 — PSF License — https://www.python.org/downloads/
- PyInstaller 6.12.0 — GPL-2.0-or-later with the PyInstaller bootloader exception — https://pyinstaller.org/
- Pillow 11.3.0 — HPND — https://python-pillow.org/
- psutil 7.2.2 — BSD-3-Clause — https://github.com/giampaolo/psutil
- pywin32 311 — PSF and bundled component-specific notices — https://github.com/mhammond/pywin32
- windows-capture 2.0.1 — MIT — https://github.com/NiiightmareXD/windows-capture/tree/main/windows-capture-python
- NumPy 2.2.6 — BSD-3-Clause and bundled library notices — https://numpy.org/
- opencv-python-headless 4.12.0.88 — MIT packaging / Apache-2.0 OpenCV and bundled notices — https://github.com/opencv/opencv-python

该助手只在本机捕获被明确识别出的《崩坏：星穹铁道》窗口，并在内存中通过
`127.0.0.1` 提供最新帧；不包含游戏素材、模型权重或星铁客户端文件。

窗口采集通过 windows-capture 对 Windows Graphics Capture 的封装实现，按 HWND
选择游戏窗口，不捕获显示器、不注入客户端、不隐藏或移动猫娘。依赖只随独立助手
分发，许可证副本和实际构建版本保留在发布归档中。

重建时在独立构建目录中安装 `requirements-build.txt` 的固定版本，使用 `--no-deps`。
windows-capture 上游元数据声明 opencv-python；本助手使用 API 相同的 headless
发行包提供 cv2，不同时安装两个 OpenCV 发行包，也不引入 GUI 组件。
