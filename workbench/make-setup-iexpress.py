"""用 IExpress 打自解压 Setup.exe(不依赖 csc/.NET 编译器)。

背景:原 make-exe.ps1 用 csc.exe 编译 SFX 壳,新版环境里 csc 被安全策略拦;
IExpress 是 Windows 自带的自解压打包器,产物同样是一个 exe,双击即装。

用法:
    python workbench/make-setup-iexpress.py --zip build/payload.zip --out build/N.E.K.O.PluginWorkshop-Setup.exe

打包内容 = payload.zip + install.cmd(纯 ASCII,解压到 %LOCALAPPDATA%\\Programs\\NEKOWorkshop
并启动 start.cmd;runtime/ 与 workspace/ 不在包内,升级时保留)。
"""
import argparse
import os
import shutil
import subprocess
import tempfile

INSTALL_CMD = r"""@echo off
setlocal
set "DST=%LOCALAPPDATA%\Programs\NEKOWorkshop"
if /I "%~1"=="--silent" goto run
if "%~1"=="" goto run
set "DST=%~1"
:run
if not exist "%DST%" mkdir "%DST%" >nul 2>&1

tasklist /FI "IMAGENAME eq bun.exe" 2>nul | "%SystemRoot%\System32\findstr.exe" /I "bun.exe" >nul
if not errorlevel 1 (
  taskkill /IM bun.exe /F >nul 2>&1
  ping -n 3 127.0.0.1 >nul
)

"%SystemRoot%\System32\tar.exe" -xf "%~dp0payload.zip" -C "%DST%"
if errorlevel 1 exit /b 1

start "" "%DST%\start.cmd"
exit /b 0
"""

SED = """[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=1
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName={out}
FriendlyName=N.E.K.O. Plugin Workshop
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[Strings]
FILE0="install.cmd"
FILE1="payload.zip"
[SourceFiles]
SourceFiles0={work}\\
[SourceFiles0]
%FILE0%=
%FILE1%=
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="payload.zip(工作台文件,已排除 runtime/ workspace/)")
    ap.add_argument("--out", required=True, help="输出 Setup.exe 路径")
    ap.add_argument("--keep", action="store_true", help="保留临时打包目录")
    a = ap.parse_args()

    work = tempfile.mkdtemp(prefix="neko_sfx_")
    shutil.copyfile(a.zip, os.path.join(work, "payload.zip"))
    with open(os.path.join(work, "install.cmd"), "w", encoding="ascii", newline="\r\n") as f:
        f.write(INSTALL_CMD)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sed_path = os.path.join(work, "build.sed")
    with open(sed_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(SED.format(out=out, work=work.rstrip("\\")))

    iexpress = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "iexpress.exe")
    # /N 不弹 UI,/Q 静默;必须屏蔽 MSYS 的路径转换,否则 /N 会被当成 N:\
    env = dict(os.environ, MSYS2_ARG_CONV_EXCL="*")
    r = subprocess.run([iexpress, "/N", "/Q", sed_path], env=env, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(out):
        raise SystemExit(f"IExpress 失败: rc={r.returncode} {r.stderr[:200]}")
    print(f"OK {out}  {os.path.getsize(out):,} 字节")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
