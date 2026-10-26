"""自动把 B站接入桥 扩展包投放为用户桌面（安装/启动插件时）。

- 从插件安装目录复制三个浏览器扩展本体（Chrome / Edge / 360 各一包，
  每包 4 个文件，扩展名与推送 source 相互独立，多浏览器共存互不干扰）；
- 代码内嵌生成各浏览器「一键安装B站接入桥(browser).bat」
  （CRLF + GBK，cmd 可正常解析；各自打开对应浏览器的扩展管理页）；
- 代码内嵌生成「使用说明.txt」（UTF-8 BOM + CRLF，记事本可正常打开）；
- 再把整个扩展包压成单个 zip（含三扩展各自独立文件夹+名字），
  方便下载/分享/保留，版本更新后自动重打；
- 幂等：投放目录写 .delivered_version 标记，插件版本一致且三扩展齐全则跳过。

投放是纯本地文件操作（毫秒级），任何失败都静默忽略，绝不影响插件启动。
"""

from __future__ import annotations

import os
import shutil
import threading
import zipfile

_DELIVERY_DIRNAME = "B站接入桥扩展"
_VERSION_MARK = ".delivered_version"
_REQUIRED_EXT_FILES = ("manifest.json", "background.js", "popup.html", "popup.js", "offscreen.html", "offscreen.js")

# 三浏览器扩展包：ext_dir 为源码目录名，name 为浏览器名，
# dst 为目标目录（用户文档下），page 为扩展管理页，exes 为可执行文件候选路径
_EXT_PACKAGES: tuple[dict, ...] = (
    {
        "ext_dir": "bili-bridge-chrome",
        "name": "Chrome",
        "dst": "bili-bridge-chrome-extension",
        "page": "chrome://extensions",
        "exes": (
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ),
    },
    {
        "ext_dir": "bili-bridge-edge",
        "name": "Edge",
        "dst": "bili-bridge-edge-extension",
        "page": "edge://extensions",
        "exes": (
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        ),
    },
    {
        "ext_dir": "bili-bridge-360",
        "name": "360",
        "dst": "bili-bridge-360-extension",
        "page": "chrome://extensions",
        "exes": (
            r"%ProgramFiles(x86)%\360\360se6\Application\360se.exe",
            r"%ProgramFiles%\360\360se6\Application\360se.exe",
            r"%ProgramFiles(x86)%\360\360Chrome\Chrome\Application\360chrome.exe",
            r"%ProgramFiles%\360\360Chrome\Chrome\Application\360chrome.exe",
            r"%LOCALAPPDATA%\360Chrome\Chrome\Application\360chrome.exe",
        ),
    },
)


def _build_bat(ext_dir: str, name: str, dst: str, page: str, exes: tuple[str, ...]) -> str:
    """按浏览器参数生成一键安装 bat（调用方以 CRLF + GBK 写入）。"""
    exe_lines: list[str] = []
    for i, exe in enumerate(exes):
        if i == 0:
            exe_lines.append(f'if exist "{exe}" set "BEXE={exe}"')
        else:
            exe_lines.append(f'if not defined BEXE if exist "{exe}" set "BEXE={exe}"')
    exe_block = "\r\n".join(exe_lines) if exe_lines else 'set "BEXE="'
    return f"""@echo off
title B站接入桥 ({name}) 一键安装
echo ============================================================
echo      B站接入桥 ({name}) - 一键安装 / 更新
echo ============================================================
echo.

setlocal
set "EXT_SRC=%~dp0{ext_dir}"
set "EXT_DST=%USERPROFILE%\\Documents\\{dst}"

if not exist "%EXT_SRC%\\manifest.json" (
    echo [错误] 找不到扩展文件。
    echo        请确认 {ext_dir} 文件夹与本脚本在同一目录，然后重新运行。
    echo.
    pause
    exit /b 1
)

if exist "%EXT_DST%" (
    echo [提示] 检测到已安装的扩展，正在覆盖更新...
    rmdir /s /q "%EXT_DST%"
)
xcopy "%EXT_SRC%" "%EXT_DST%" /e /i /q >nul
if errorlevel 1 (
    echo [错误] 复制扩展文件失败。
    pause
    exit /b 1
)
echo [完成] 扩展已就绪：%EXT_DST%
echo.

echo 下一步（最后一步手动，约 10 秒）：
echo   1. 已为你打开扩展管理页面
echo   2. 打开页面右上角的"开发者模式"开关
echo   3. 点"加载已解压的扩展程序"，选择上面的目录
echo.
echo 安装后扩展会随浏览器自动运行，把 B站登录态实时同步给 YUI。
echo.

rem 打开对应浏览器扩展管理页（按真实路径找，找不到用默认浏览器）
{exe_block}
if defined BEXE (
    start "" "%BEXE%" "{page}"
) else (
    start "" "{page}"
)

echo.
pause
"""


# ── 使用说明 txt（UTF-8 BOM + CRLF 写入）──
README_TXT = """============================================================
  B站接入桥 + B站全能助手（bili_viewer）使用说明
============================================================

【这是什么】
本包用于让 N.E.K.O 的 AI（YUI）直接接入你的 B站：
- 「B站接入桥」是浏览器扩展，负责把你在 bilibili.com 的登录态
  实时同步给本机插件（只发到本机 127.0.0.1，不经过任何外部网络）；
- 「bili_viewer」是 N.E.K.O 客户端插件，YUI 靠它搜索/看视频/
  弹幕/评论/直播/管理你的稿件/回复直播弹幕等。

本包内置三个浏览器专用扩展（相互独立，可多浏览器共存互不干扰）：
  bili-bridge-chrome   → Chrome 专用（名称：B站接入桥 (Chrome)）
  bili-bridge-edge     → Edge 专用（名称：B站接入桥 (Edge)）
  bili-bridge-360      → 360 安全/极速浏览器专用（名称：B站接入桥 (360)）
按你实际使用的浏览器，运行对应的安装脚本即可。

装一次扩展，之后登录/退出 B站都会自动同步，不用再管它。


------------------------------------------------------------
一、安装（约 1 分钟）
------------------------------------------------------------
1. 双击本文件夹里对应浏览器的安装脚本：
     Chrome 用户 →「一键安装B站接入桥(Chrome).bat」
     Edge 用户   →「一键安装B站接入桥(Edge).bat」
     360 用户    →「一键安装B站接入桥(360).bat」
2. 脚本会自动把扩展复制到「文档\\bili-bridge-扩展名」，并打开
   对应浏览器的扩展管理页面；
3. 在页面右上角打开「开发者模式」开关；
4. 点「加载已解压的扩展程序」，选择第 2 步复制好的那个目录；
5. 完成后工具栏出现「🅱️ B站接入桥」图标即成功。

* 360 极速浏览器 / 安全浏览器（13 以上）的扩展管理页同为
  chrome://extensions，步骤一样；
* 扩展图标如果是灰色，点一下图标选「允许」。


------------------------------------------------------------
二、绑定 B站账号（一次授权）
------------------------------------------------------------
装好扩展后，打开 N.E.K.O，对 YUI 说：
     “允许你读取浏览器 Cookie 绑定 B站账号”
或  “打开接入面板”
YUI 会打开本地面板（http://127.0.0.1:8765），
面板显示「扩展在线 / 已绑定」就说明接上了。

如果面板显示「扩展未在线」：
- 确认扩展已加载（工具栏有 🅱️ 图标）；
- 确认你在浏览器里登录过 bilibili.com；
- 稍等几秒，扩展每分钟自动推送一次。


------------------------------------------------------------
三、日常用法（对 YUI 说就行）
------------------------------------------------------------
【看内容】
  “搜一下B站：猫meme”
  “看看这个视频：BV1xx411c7mD”
  “给我这个视频的弹幕 / 评论 / CC字幕 / AI概要”
  “这个直播间现在多少人，弹幕说什么”
【管理自己的账号】
  “看看我的B站账号信息 / 我的投稿”
  “把三连视频的简介改成XXX”
【互动】
  “帮我在这个直播间发条弹幕：主播好帅”
  “直播时帮我回复弹幕”（可让 YUI 自动答谢/答疑）
【感知】
  YUI 会自动感知你正在看的视频/页面，随时可以问
  “我现在看的这个视频讲了什么”
【声音（v0.7.11 新增）】
  点扩展图标 →「开启声音」，让 YUI 能听到直播/视频里的声音
  （后台静默采集标签页声音，本机转成文字，全部本地处理）；
  “直播里主播在说什么”“这个视频在讲什么”
  不需要时再点一次图标关闭即可；开关会记住，重启浏览器自动恢复。


------------------------------------------------------------
四、安全说明
------------------------------------------------------------
- 扩展只把 B站登录态推送到本机 127.0.0.1:8765 的插件面板，
  Cookie 不上传任何外部网络、不写日志、不发给 AI；
- 声音捕获同样只发本机：标签页声音在本机转成文字，不上传外部；
- 扩展权限仅包含 bilibili.com 与 127.0.0.1，无其它站点访问权；
- 写操作（改稿件、发弹幕）只作用于你自己账号的内容，且需要
  你与 YUI 明确确认。

============================================================
常见问题排查
============================================================
Q1 双击 bat 报错？
   - 确认对应扩展文件夹与 bat 在同一目录（本包默认就是）；
   - 右键 bat →“以管理员身份运行”再试（一般不需要）。

Q2 扩展装上了但 YUI 说没绑定？
   - 对 YUI 说“打开接入面板”，看扩展是否“在线”；
   - 浏览器里重新登录一次 bilibili.com（登录态过期）；
   - 等 1 分钟让扩展自动推送，或点扩展图标手动查看。

Q3 不用扩展，还能绑定吗？
   - 可以：对 YUI 说“允许读取浏览器 Cookie 绑定”，走 CDP
     自动接入（无需关浏览器）；或在插件配置里手动填 Cookie。

Q4 怎么更新扩展？
   - 重新双击对应浏览器的安装脚本覆盖更新即可，插件升级后
     建议也重跑一次。

Q5 插件在哪更新？
   - N.E.K.O 客户端 → 插件中心 → B站全能助手 → 更新到最新版。

============================================================
（本包：Chrome/Edge/360 三扩展本体 + 对应一键安装脚本 + 本说明）
============================================================
"""


def _desktop_dir() -> str:
    """探测真实桌面路径（注册表 User Shell Folders 优先，兼容 OneDrive 重定向）。"""
    try:
        import winreg  # Windows only

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "Desktop")
        val = os.path.expandvars(val)
        if os.path.isdir(val):
            return val
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _plugin_version(plugin_dir: str) -> str:
    try:
        for line in open(os.path.join(plugin_dir, "plugin.toml"), encoding="utf-8"):
            line = line.strip()
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.0.0"


def _mark_ok(delivery_dir: str, version: str) -> bool:
    mark = os.path.join(delivery_dir, _VERSION_MARK)
    try:
        if open(mark, encoding="utf-8").read().strip() != version:
            return False
    except Exception:
        return False
    return all(
        os.path.isfile(os.path.join(delivery_dir, pkg["ext_dir"], f))
        for pkg in _EXT_PACKAGES
        for f in _REQUIRED_EXT_FILES
    )


def _zip_delivered(zip_path: str, version: str) -> bool:
    """zip 是否已包含指定版本（zip 内 .delivered_version == version）。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return zf.read(".delivered_version").decode("utf-8").strip() == version
    except Exception:
        return False


def _write_delivery_zip(delivery: str, target_root: str, version: str) -> str | None:
    """把投放目录整体压成单个 zip（含三扩展各自独立文件夹+名字），返回 zip 路径。

    同一版本已打过 zip 则跳过；打包在投放目录内容落盘之后进行。
    """
    try:
        zip_path = os.path.join(target_root, f"B站接入桥扩展_v{version}.zip")
        if _zip_delivered(zip_path, version):
            return zip_path
        tmp: str | None = zip_path + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(os.path.join(delivery, _VERSION_MARK), ".delivered_version")
            for pkg in _EXT_PACKAGES:
                for f in _REQUIRED_EXT_FILES:
                    zf.write(
                        os.path.join(delivery, pkg["ext_dir"], f),
                        f"{pkg['ext_dir']}/{f}",
                    )
            for pkg in _EXT_PACKAGES:
                zf.write(
                    os.path.join(delivery, f"一键安装B站接入桥({pkg['name']}).bat"),
                    f"一键安装B站接入桥({pkg['name']}).bat",
                )
            zf.write(os.path.join(delivery, "使用说明.txt"), "使用说明.txt")
        os.replace(tmp, zip_path)
        return zip_path
    except Exception:
        try:
            if tmp:
                os.remove(tmp)
        except Exception:
            pass
        return None


def ensure_desktop_package(plugin_dir: str, version: str | None = None, desktop: str | None = None) -> str | None:
    """把 B站接入桥 扩展包（Chrome/Edge/360）投放/更新到桌面。幂等，失败静默返回 None。

    参数 plugin_dir 为插件安装目录（含 browser_extension/bili-bridge-* 三包）。
    desktop 仅供测试注入；返回投放后的完整目录路径（未投放返回 None）。
    """
    try:
        src_root = os.path.join(plugin_dir, "browser_extension")
        if not os.path.isfile(os.path.join(src_root, "bili-bridge-chrome", "manifest.json")):
            return None
        ver = version or _plugin_version(plugin_dir)
        target_root = desktop or _desktop_dir()
        delivery = os.path.join(target_root, _DELIVERY_DIRNAME)
        if _mark_ok(delivery, ver):
            _write_delivery_zip(delivery, target_root, ver)
            return delivery

        os.makedirs(delivery, exist_ok=True)
        for pkg in _EXT_PACKAGES:
            # 1) 扩展本体（覆盖更新）
            src_ext = os.path.join(src_root, pkg["ext_dir"])
            dst_ext = os.path.join(delivery, pkg["ext_dir"])
            os.makedirs(dst_ext, exist_ok=True)
            for f in _REQUIRED_EXT_FILES:
                shutil.copy2(os.path.join(src_ext, f), os.path.join(dst_ext, f))
            # 2) 一键安装 bat（CRLF + GBK）
            bat_path = os.path.join(delivery, f"一键安装B站接入桥({pkg['name']}).bat")
            with open(bat_path, "w", encoding="gbk", newline="\r\n") as fh:
                fh.write(_build_bat(**pkg))
        # 3) 使用说明 txt（UTF-8 BOM + CRLF）
        txt_path = os.path.join(delivery, "使用说明.txt")
        with open(txt_path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
            fh.write(README_TXT)
        # 4) 版本标记
        with open(os.path.join(delivery, _VERSION_MARK), "w", encoding="utf-8") as fh:
            fh.write(ver)
        # 5) 三扩展压缩包（下载/分享用，独立文件夹+名字，版本更新自动重打）
        _write_delivery_zip(delivery, target_root, ver)
        return delivery
    except Exception:
        return None


def ensure_desktop_package_async(plugin_dir: str, version: str | None = None) -> None:
    """后台线程投放（on_startup 调用，绝不阻塞启动）。"""

    def _run() -> None:
        ensure_desktop_package(plugin_dir, version)

    threading.Thread(target=_run, name="bili-desktop-deliver", daemon=True).start()


__all__ = [
    "ensure_desktop_package",
    "ensure_desktop_package_async",
    "README_TXT",
    "_build_bat",
    "_EXT_PACKAGES",
    "_desktop_dir",
    "_DELIVERY_DIRNAME",
]
