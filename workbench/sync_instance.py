"""把刚构建好的 payload 覆盖同步到本机安装实例(升级口径与安装器一致)。

规矩:
- runtime\\ 与 workspace\\ 绝不参与(payload 里本来就没有,同步时也不碰):用户的 API 配置、会话、记忆都在 runtime 下;
- 先停实例(运行中的 bun.exe 会锁文件),再解包覆盖,再带窗口重新启动;
- 解包后核对关键文件与版本,确认新代码真的在跑,再谈「已同步」。

用法:python -I workbench/sync_instance.py [--exe build/N.E.K.O.PluginWorkshop-Setup.exe]
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

INST = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "NEKOWorkshop")
PY = sys.executable


def kill_instance():
    """按端口找 pid 杀掉实例(连同它拉起的引擎),避免残留进程让「改了没生效」"""
    for port in ("5099", "4096"):
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, encoding="latin1").stdout
        except Exception:
            continue
        for line in out.splitlines():
            if (":%s " % port) in line and "LISTENING" in line:
                pid = line.split()[-1]
                if pid.isdigit() and int(pid) > 4:
                    subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
                    print("  已停 pid %s(端口 %s)" % (pid, port))


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "payload.zip"))
    a = ap.parse_args()
    if not os.path.isfile(a.zip):
        sys.exit("找不到载荷:%s" % a.zip)

    with zipfile.ZipFile(a.zip) as z:
        names = z.namelist()
        bad = [n for n in names if n.startswith("runtime/") or n.startswith("workspace/")]
        if bad:
            sys.exit("载荷里不该含 runtime/ workspace(会覆盖用户数据),拒绝同步:%s" % bad[:5])
        for must in ("wb-studio.mjs", "wb-agent-lib.mjs", "package.json", "_zcode/node.exe"):
            if must not in names:
                sys.exit("载荷缺关键条目:%s" % must)
    print("载荷自检通过(%d 条,不含 runtime/workspace)" % len(names))

    kill_instance()
    lock = os.path.join(INST, "runtime", "studio.lock")
    if os.path.exists(lock):
        os.remove(lock)
    tar = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
    r = subprocess.run([tar, "-xf", a.zip, "-C", INST], capture_output=True, text=True, encoding="latin1")
    if r.returncode != 0:
        sys.exit("解包覆盖失败:%s" % (r.stderr or r.stdout).strip()[:300])
    print("已覆盖到", INST)

    ver = ""
    try:
        import json
        ver = json.load(open(os.path.join(INST, "package.json"), encoding="utf-8")).get("version", "")
    except Exception as e:
        print("读版本失败:", e)
    studio = os.path.join(INST, "wb-studio.mjs")
    lib = os.path.join(INST, "wb-agent-lib.mjs")
    txt = open(studio, encoding="utf-8").read() + open(lib, encoding="utf-8").read()
    markers = {
        # 本轮(issue #1-#7)装上去必须能看到这些
        "外发写确认队列": "/api/approvals" in txt,
        "引擎权限一律先问": '"*": "ask"' in txt or '"*":"ask"' in txt,
        "更新包 SHA256 校验": "verifyUpdateIntegrity" in txt,
        "清单验签(信任根)": "verifyUpdateManifest" in txt and "UPDATE_TRUST" in txt,
        "清单核对版本/资产/体积": "与要装的版本" in txt,
        "引擎随机密码": "engine-auth.json" in txt,
        "老配置启动自愈": "upgradeEnginePermission" in txt,
        "底座页门禁说明": "gateNote" in txt,
        "更新状态在模块作用域": txt.index("const updateState = {") < txt.index("const srv = createServer"),
    }
    print("实例内代码标记:", markers)
    if not all(markers.values()) or not ver:
        sys.exit("同步后校验未通过,先别启动:%s v%s" % (INST, ver))

    # 带窗口重新启动:会话后台任务会回收进程树,必须用 schtasks 一次性任务拉起
    launcher = os.path.join(INST, "start.cmd")
    if not os.path.isfile(launcher):
        sys.exit("安装实例里没有 start.cmd,无法这样拉起:%s" % launcher)
    sch = subprocess.run(
        ["schtasks", "/Create", "/TN", "neko_workshop_reopen", "/TR", launcher, "/SC", "ONCE", "/ST", "00:00", "/F"],
        capture_output=True, text=True, encoding="latin1")
    print(sch.stdout.strip() or sch.stderr.strip())
    subprocess.run(["schtasks", "/Run", "/TN", "neko_workshop_reopen"], capture_output=True, text=True, encoding="latin1")
    print("同步完成:v%s → 已请求带窗口重启(NEKOWorkshop)" % ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
