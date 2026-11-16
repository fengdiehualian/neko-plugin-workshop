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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from release_tls import resolve_git  # noqa: E402

# git 路径统一由 release_tls 解析(PATH → $GIT_BIN → 本机已知位置),不在这里写死
GIT = resolve_git()
DEFAULT_ZCODE = os.environ.get("NEKO_ZCODE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "NEKOWorkshop", "_zcode"
)
# 载荷里**不许出现**的条目(issue #4):第三方市场语料不打包分发。
# 光在 THIRD_PARTY_NOTICES.md 里写「不分发」不算数,必须在出包前真的拦下来。
FORBIDDEN = ["market-corpus/", "tools/probes/",
               # 第三方市场插件副本(许可未声明),不随包分发;要读请本机 fetch_corpus
               "packs/neko-plugin/skill/neko-plugin-dev/references/market-examples/"]
# 本机配置也不许进包(v1.2.65 实测):它被仓库跟踪,覆盖安装会把用户的端口/引擎口/模型/界面设定
# 冲回开发机默认值(隔离实例被改成真实例的 5099/4096 就是当场证据)。工作室读不到该文件会走 defaultConfig,
# 所以「包里不带」才是对的:首启默认值,用户改过的东西随升级保留。
NOT_PACKAGED = ["wb-studio.config.json"]
# 载荷里必须出现的条目:底座三件 + 工坊入口
REQUIRED = [
    ("wb-studio.mjs", 100_000),
    ("wb-agent-lib.mjs", 10_000),
    ("package.json", 100),
    ("_zcode/node.exe", 50_000_000),   # 91MB:小于 50MB 就是没打进去或打了残件
    ("_zcode/zcode.cjs", 1_000_000),
    ("_zcode/provider/zcode-builtin.json", 10_000),
    # 出处凭证:哪个提交打的包。缺它就等于"包来路不明",下游不许继续
    ("BUILD_INFO.json", 60),
]


def resolve_ref(ref):
    """把要打包的引用解析成提交号,解析不了就当场停手。

    本轮实测踩到的坑:`--tag v1.2.65` 指一个还没创建的 tag 时,git archive 会失败退出,
    可磁盘上**还留着上一版的 payload.zip**——后面打 exe、装实例照用不误,
    发出去的是旧代码,表面上一切正常。所以两件事一起做:先解析引用,再在开工时删掉旧产物。"""
    r = subprocess.run(
        [GIT, "rev-parse", "--verify", ref + "^{commit}"],
        cwd=ROOT, capture_output=True, text=True, encoding="latin1",
    )
    if r.returncode != 0:
        sys.exit(
            "引用 %r 解析不到提交:%s\n"
            "  要打当前提交用 --tag HEAD;发版若要用 tag,先 git tag 再构建(别拿旧 payload.zip 凑数)。"
            % (ref, (r.stderr or r.stdout).strip()[:200])
        )
    return r.stdout.strip()


def git_archive(tag, out):
    # `git archive <本地 ref>` 不联网,不需要任何 TLS 妥协(issue #11:
    # 原来这里跟着抄了 GIT_SSL_NO_VERIFY,那是把「关校验」当成默认姿势的惯性)
    r = subprocess.run(
        [GIT, "archive", "--format=zip", "-o", out, tag],
        cwd=ROOT, capture_output=True, text=True, encoding="latin1",
    )
    if r.returncode != 0:
        sys.exit("git archive 失败:%s" % ((r.stderr or r.stdout).strip()[:400]))


def add_zcode(zip_path, zcode_dir):
    """把 _zcode 三件补进货载荷,并逐项比对 workbench/zcode_bundle.json 登记的哈希。
    issue #13:以前只是「从本机安装实例拷一份」,拷贝来源、版本、完整性一概没记录——
    拷错一份旧版或中途被改动都不会被发现,而且包变小 30MB 那次也没人拦得住。"""
    if not os.path.isdir(zcode_dir):
        sys.exit("找不到 _zcode 目录:%s(设 NEKO_ZCODE_DIR 或指向安装实例)" % zcode_dir)
    prov_path = os.path.join(ROOT, "workbench", "zcode_bundle.json")
    prov = {}
    if os.path.isfile(prov_path):
        prov = json.load(open(prov_path, encoding="utf-8"))
    want = {f["path"]: f for f in (prov.get("files") or [])}
    # 没有来源登记 = 我们不知道这份 91MB 的运行时的哪来的、该是哪个哈希 → 不允许出包。
    # 以前这里是「打印一句警告继续」,那等于登记形同虚设(issue #6 指出的就是这种「说了但没拦」)
    missing_prov = [p for p in ("node.exe", "zcode.cjs", "provider/zcode-builtin.json") if p not in want]
    if missing_prov:
        sys.exit(
            "workbench/zcode_bundle.json 缺少这些条目的来源登记:%s\n"
            "补齐(要含下载地址与上游哈希出处)再打包:node workbench/collect_zcode.cjs" % ", ".join(missing_prov)
        )
    # 来源必须逐项写清楚:只记哈希等于没记(哈希是谁给的、对着哪儿核验的,出问题查不到)
    no_upstream = [p for p, f in want.items() if not str(f.get("upstream") or "").strip() or not str(f.get("hash_source") or "").strip()]
    if no_upstream:
        sys.exit("zcode_bundle.json 里这些条目没有 upstream/hash_source,拒绝出包:%s" % ", ".join(no_upstream))
    # issue #13 第 3 点:地址不能是浮动的 latest-vXX.x —— 那种地址过几天就不再包含本版本的校验和,
    # 「可复核」会变成空话。另外 node.exe 必须同时登记官方 zip 的哈希(zip 与解包后的 exe 是两个对象)。
    floating = [p for p, f in want.items() if re.search(r"latest-v\d+\.x", json.dumps(f))]
    if floating:
        sys.exit("zcode_bundle.json 里还写着浮动版本地址 latest-vXX.x,拒绝出包:%s\n  重跑:node workbench/collect_zcode.cjs" % ", ".join(floating))
    nd = want.get("node.exe") or {}
    if not str(nd.get("zip_sha256") or "").strip() or not str(nd.get("zip_artifact") or "").strip():
        sys.exit("node.exe 的登记缺 zip_sha256/zip_artifact(官方 SHASUMS256.txt 里那一行的哈希),拒绝出包\n"
                 "  补齐并复核:node workbench/collect_zcode.cjs && node workbench/collect_zcode.cjs --verify")
    if not re.search(r"^v\d+\.\d+\.\d+$", str(prov.get("nodeVersion") or "")):
        sys.exit("nodeVersion 不是确切版本号:%r" % (prov.get("nodeVersion"),))
    n = 0
    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as z:
        existing = set(z.namelist())
        for rel in ("node.exe", "zcode.cjs", os.path.join("provider", "zcode-builtin.json")):
            src = os.path.join(zcode_dir, rel)
            if not os.path.isfile(src):
                sys.exit("_zcode 缺文件:%s" % src)
            arc = "_zcode/" + rel.replace(os.sep, "/")
            rec = want.get(rel.replace(os.sep, "/"))
            if rec:
                h = hashlib.sha256(open(src, "rb").read()).hexdigest()
                if h != rec["sha256"]:
                    sys.exit(
                        "_zcode/%s 哈希与登记不符!\n  期望 %s\n  实算 %s\n"
                        "来源不明或已被改动的底座不能进包——请确认版本后更新 workbench/zcode_bundle.json。"
                        % (rel, rec["sha256"][:24] + "…", h[:24] + "…")
                    )
                if int(rec.get("size") or 0) != os.path.getsize(src):
                    sys.exit("_zcode/%s 体积与登记不符(登记 %s,实际 %s)" % (rel, rec.get("size"), os.path.getsize(src)))
            if arc in existing:
                continue
            z.write(src, arc)
            n += 1
    if want:
        print("  _zcode 三项哈希与登记一致 ✓(%s / %s)" % (prov.get("nodeVersion", "?"), prov.get("agentVersion", "?")))
    return n


def verify(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        sizes = {i.filename: i.file_size for i in z.infolist()}
        count = len(names)
    missing = [p for p, _ in REQUIRED if p not in names]
    small = [(p, sizes.get(p, 0)) for p, mn in REQUIRED if p in names and sizes.get(p, 0) < mn]
    # 语料/探针混进包里 = 分发口径出错(许可问题)或本机路径泄露,一律不出包
    leaked = [p for p in FORBIDDEN for n in names if n.startswith(p) or n == p.rstrip("/")]
    clobber = [p for p in NOT_PACKAGED if p in names]
    return count, missing, small, leaked, clobber


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
    # 第一件事就是删旧产物,连"引用解析不了"这种早退也不能留下陈包:
    # 失败后宁可没有包(下一步会明确报"文件不存在"),也不能有一个上一版的包被误用
    if os.path.exists(a.out):
        print("先删除旧产物(避免本轮失败后被误用):%s" % a.out)
        os.remove(a.out)
    commit = resolve_ref(a.tag)
    tmp = a.out + ".src.zip"
    git_archive(a.tag, tmp)
    with zipfile.ZipFile(tmp) as z:
        src_count = len(z.namelist())
    added = add_zcode(tmp, a.zcode)
    # 重排成 deflate 一致的成品(git archive 已是 zip,这里只做原子替换)
    if os.path.exists(a.out):
        os.remove(a.out)
    os.replace(tmp, a.out)

    # 出处写进包内:下游(打 exe / 装完自检)据此证明"这就是那个提交",而不是"大概是新的吧"
    try:
        version = json.load(open(os.path.join(ROOT, "package.json"), encoding="utf-8"))["version"]
    except Exception as e:
        sys.exit("读不到 package.json 的版本号:%s" % e)
    info = {
        "ref": a.tag, "commit": commit, "version": version,
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generator": "workbench/build_payload.py",
    }
    with zipfile.ZipFile(a.out, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("BUILD_INFO.json", json.dumps(info, ensure_ascii=False, indent=1) + "\n")

    count, missing, small, leaked, clobber = verify(a.out)
    if missing or small:
        sys.exit(
            "载荷不完整,拒绝出包!\n  缺条目:%s\n  体积异常:%s\n  (历史上就因缺 _zcode 少打 30MB,别放过)"
            % (missing, small)
        )
    if leaked:
        sys.exit(
            "载荷里混进了不该分发的内容,拒绝出包!\n  命中:%s\n"
            "  market-corpus 是第三方源码(实测 GPL/AGPL/未声明占多数),按 THIRD_PARTY_NOTICES.md 的口径不打包分发;"
            "tools/probes 里是本机排查脚本(含路径线索)。\n"
            "  出现它们通常意味着 .gitattributes 的 export-ignore 被删了,"
            "或者只写了 `dir/**` 没写 `dir`(两行都要,少一行就还会导出来)。"
            % ", ".join(sorted(set(leaked))[:6])
        )
    if clobber:
        sys.exit(
            "载荷里带着本机配置文件,拒绝出包:%s\n"
            "  覆盖安装会把用户的端口/引擎口/模型/界面设定冲回开发机默认值(本轮实测踩到)。\n"
            "  修:在 .gitattributes 里给它加 export-ignore(改完要先提交,git archive 才认新属性)。"
            % ", ".join(clobber)
        )
    size = os.path.getsize(a.out)
    print("payload.zip:源码 %d 条 + _zcode 补 %d 条 = %d 条,%s 字节(压缩后)" % (src_count, added, count, format(size, ",")))
    print("打包出处:%s = %s(v%s),已写进包内 BUILD_INFO.json" % (a.tag, commit[:12], version))
    print("必查条目全部就位:", ", ".join(p for p, _ in REQUIRED))
    print("禁含条目检查通过:", ", ".join(FORBIDDEN + NOT_PACKAGED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
