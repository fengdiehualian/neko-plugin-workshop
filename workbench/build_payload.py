"""组装工坊安装包的载荷 payload.zip。

坑(2026-11 真出过):裸 `git archive` 只有仓库源码,**不含 _zcode(node.exe 91MB + zcode.cjs +
provider/zcode-builtin.json)**,发出去的包小 30 多 MB、ZCode 底座直接不可用,而且表面上一切正常。
所以这里把「源码 + _zcode」写成一条命令,并在收尾**强制核对 _zcode 三项齐全、体积合理**,
少了就当场报错,绝不让缺件的安装包出厂。

用法:
    python workbench/build_payload.py [--tag v1.2.63] [--out build/payload.zip]
                                      [--zcode "<带 _zcode 的目录>"]
环境变量 NEKO_ZCODE_DIR 可指定 _zcode 来源(默认取本机安装实例)。
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT = os.environ.get("GIT_BIN") or shutil.which("git") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0", "cmd", "git.exe"
)
DEFAULT_ZCODE = os.environ.get("NEKO_ZCODE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "NEKOWorkshop", "_zcode"
)
# 载荷里必须出现的条目:底座三件 + 工坊入口
REQUIRED = [
    ("wb-studio.mjs", 100_000),
    ("wb-agent-lib.mjs", 10_000),
    ("package.json", 100),
    ("_zcode/node.exe", 50_000_000),   # 91MB:小于 50MB 就是没打进去或打了残件
    ("_zcode/zcode.cjs", 1_000_000),
    ("_zcode/provider/zcode-builtin.json", 10_000),
]


def git_archive(tag, out):
    env = dict(os.environ, GIT_SSL_NO_VERIFY="1")
    r = subprocess.run(
        [GIT, "-c", "http.proxy=", "archive", "--format=zip", "-o", out, tag],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit("git archive 失败:%s" % ((r.stderr or r.stdout).strip()[:400]))


def add_zcode(zip_path, zcode_dir):
    if not os.path.isdir(zcode_dir):
        sys.exit("找不到 _zcode 目录:%s(设 NEKO_ZCODE_DIR 或指向安装实例)" % zcode_dir)
    n = 0
    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as z:
        for rel in ("node.exe", "zcode.cjs", os.path.join("provider", "zcode-builtin.json")):
            src = os.path.join(zcode_dir, rel)
            if not os.path.isfile(src):
                sys.exit("_zcode 缺文件:%s" % src)
            arc = "_zcode/" + rel.replace(os.sep, "/")
            if arc in z.namelist():
                continue
            z.write(src, arc)
            n += 1
    return n


def verify(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        sizes = {i.filename: i.file_size for i in z.infolist()}
        count = len(names)
    missing = [p for p, _ in REQUIRED if p not in names]
    small = [(p, sizes.get(p, 0)) for p, mn in REQUIRED if p in names and sizes.get(p, 0) < mn]
    return count, missing, small


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="HEAD", help="要打包的 git 引用(默认 HEAD)")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "payload.zip"))
    ap.add_argument("--zcode", default=DEFAULT_ZCODE, help="_zcode 来源目录")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".src.zip"
    git_archive(a.tag, tmp)
    with zipfile.ZipFile(tmp) as z:
        src_count = len(z.namelist())
    added = add_zcode(tmp, a.zcode)
    # 重排成 deflate 一致的成品(git archive 已是 zip,这里只做原子替换)
    if os.path.exists(a.out):
        os.remove(a.out)
    os.replace(tmp, a.out)

    count, missing, small = verify(a.out)
    if missing or small:
        sys.exit(
            "载荷不完整,拒绝出包!\n  缺条目:%s\n  体积异常:%s\n  (历史上就因缺 _zcode 少打 30MB,别放过)"
            % (missing, small)
        )
    size = os.path.getsize(a.out)
    print("payload.zip:源码 %d 条 + _zcode 补 %d 条 = %d 条,%s 字节(压缩后)" % (src_count, added, count, format(size, ",")))
    print("必查条目全部就位:", ", ".join(p for p, _ in REQUIRED))


if __name__ == "__main__":
    main()
