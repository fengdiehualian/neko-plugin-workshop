"""Maintainer-only outer installer; the plugin itself uses the official SDK packer."""

import argparse
import html
import re
import zipfile
from pathlib import Path

from agent_mc_autonomy.runtime_bundle.build import compose_delivery


def compose(runtime: Path, plugin: Path, output: Path, instructions: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.neko-plugin", plugin.name):
        raise ValueError("Use the official ASCII plugin artifact filename")
    result = compose_delivery(runtime, plugin, output, instructions)
    page = Path(__file__).with_name("start-here.html").read_text(encoding="utf-8")
    # This small wrapper only guides normal official import. It never writes
    # into a Neko installation, discovers credentials, or starts host/game/model.
    launcher = ('@echo off\r\nsetlocal\r\nchcp 65001 >nul\r\n'
        'echo 正在校验并准备运行资源，首次安装可能需要几分钟，请勿关闭窗口。\r\n'
        'echo 自动使用当前用户的短缓存目录，无需管理员权限或修改注册表。\r\n'
        'call "%~dp0prepare.cmd"\r\n'
        'if errorlevel 1 (\r\n  echo 准备未完成，请保留本窗口的错误信息。确认已经完整解压后再试。\r\n'
        '  if /i not "%~1"=="--no-open" pause\r\n  exit /b 1\r\n)\r\n'
        'if /i "%~1"=="--no-open" exit /b 0\r\n'
        'start "" "%~dp0start-here.html"\r\n'
        'echo 资源已准备好。请按已打开的安装指南，在 Neko 插件中心导入相邻的插件包。\r\nexit /b 0\r\n')
    with zipfile.ZipFile(result, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("一键安装.cmd", launcher.encode("utf-8"))
        archive.writestr("install.cmd", launcher.encode("utf-8"))
        archive.writestr("start-here.html", page.replace("@PLUGIN_FILE@", html.escape(plugin.name, quote=True)))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "plugin", "output", "instructions"):
        parser.add_argument("--" + name, type=Path, required=True)
    print(compose(**vars(parser.parse_args())))
