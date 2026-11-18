#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin-forge · 部署面一致性检查（零第三方依赖，Python 3.8+）

为什么要有它（外部审工单 W09）：仓库改了以后，装机面靠人工 cp，漂移每轮复发。
2026-11-17 实测两次：挂载态 hook 停在旧字节，仓库已经前进三个提交。
人记得住的检查不是检查，这一步把「忘了 cp」变成红的。

它只管 install_sync.py 够不着的那一类：**宿主真执行、且按字节比对的仓外副本**。
skill 树、agents、prompts 那三面由 tools/install_sync.py 自己管（它按目录列文件，
覆盖面跟着目录走），本脚本不重复它的清单，也不与它比。

用法
  python -I scripts/verify_installs.py            # 比一比，等价于 --check
  python -I scripts/verify_installs.py --self-test # 离线夹具自检（CI 跑这条）

退出码
  0 = 字典里每一对都逐字节相同
  1 = 任一处字节不同或副本缺失（打印两侧 sha256 前 12 位与路径）
  2 = 用法错误

shortcut: 字典是硬清单，漏一个新部署面就漏检一次。加面时在本文件加一行，
并在 AUDIT.md 记一句；出现第三类需要按目录展开的副本时，升级为读 install_sync 的清单。
"""
import argparse
import hashlib
import io
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (仓库内路径, [仓外执行副本的绝对路径模板，~ 会展开])
PAIRS = [
    ("scripts/session_capability_refresh.py", [
        "~/.workbuddy/hooks/session_capability_refresh.py",
        "~/.workbuddy/plugins/marketplaces/my-experts/plugins/neko-plugin-ops/scripts/"
        "session_capability_refresh.py",
    ]),
]


def sha12(path):
    return hashlib.sha256(io.open(path, "rb").read()).hexdigest()[:12]


TREE_PAIRS = [
    # (仓库里的 skill 树, 宿主里的同名树)。
    # 2026-11-18 实测过一次漏检：dry_load.py 进了仓库、进了 CI、进了人设指令，
    # 但三棵宿主树里都没有它——因为 install_sync 只在有人手动跑它时才对比。
    # 这里做同样的字节核对，判据不重写：只比 .py 与 .md，忽略宿主独有文件。
    # (仓库里的 skill 树, 宿主里的相对树名)：宿主清单在 HOSTS 里，两个维度相乘
    ("skills/plugin-forge", "skills/plugin-forge"),
    ("skills/neko-plugin-sandbox", "skills/neko-plugin-sandbox"),
]
SKIP_NAMES = ("__pycache__", ".git", ".bak")


def repo_tree_files(repo_root, rel_root):
    out = []
    base = os.path.join(repo_root, rel_root.replace("/", os.sep))
    if not os.path.isdir(base):
        return out
    for dp, dn, fns in os.walk(base):
        dn[:] = [x for x in dn if x not in ("__pycache__", ".git")]
        for f in sorted(fns):
            if f.endswith((".pyc", ".bak")) or ".bak-" in f:
                continue
            if not f.endswith((".py", ".md", ".json", ".toml", ".mjs", ".js")):
                continue
            out.append(os.path.relpath(os.path.join(dp, f), base).replace("\\", "/"))
    return out


HOSTS = ("~/.claude", "~/.codex", "~/.zcode")


def compare_trees(pairs, repo_root=None, hosts=None):
    """比仓库 skill 树与宿主安装面。返回 (一致数, 问题列表)。"""
    root = repo_root or REPO
    ok, problems = 0, []
    hosts = hosts or [os.path.expanduser(h) for h in HOSTS]
    for rel_repo, rel_host in pairs:
        files = repo_tree_files(root, rel_repo)
        if not files:
            problems.append(("仓库侧没有这棵树", "-", rel_repo, rel_host))
            continue
        for host in hosts:
            dst_base = os.path.join(os.path.expanduser(host), rel_host.replace("/", os.sep))
            if not os.path.isdir(dst_base):
                problems.append(("宿主树整个缺失", "-", rel_repo, dst_base))
                continue
            for rel in files:
                src = os.path.join(root, rel_repo.replace("/", os.sep), rel.replace("/", os.sep))
                dst = os.path.join(dst_base, rel.replace("/", os.sep))
                if not os.path.isfile(dst):
                    problems.append((sha12(src), "缺失", rel_repo + "/" + rel, dst_base + "/" + rel))
                    continue
                if sha12(src) == sha12(dst):
                    ok += 1
                else:
                    problems.append((sha12(src), sha12(dst), rel_repo + "/" + rel,
                                     dst_base + "/" + rel))
    return ok, problems


def compare(pairs, repo_root=None):
    """返回 (一致条数, 问题列表)。问题形如 (仓库 sha, 副本 sha 或 '缺失', 两侧路径)。"""
    root = repo_root or REPO
    ok, problems = 0, []
    for rel, copies in pairs:
        src = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            problems.append(("无源文件", "-", rel, rel))
            continue
        want = sha12(src)
        for cp in copies:
            full = os.path.expanduser(cp)
            if not os.path.isfile(full):
                problems.append((want, "缺失", rel, cp))
                continue
            got = sha12(full)
            if got == want:
                ok += 1
            else:
                problems.append((want, got, rel, cp))
    return ok, problems


def _self_test():
    """夹具：字节相同绿、追加一个换行红、副本缺失红。"""
    cases = []
    src_body = b"x = 1" + bytes([10])
    good = tempfile.mkdtemp(prefix="forge-verify-good-")
    drift = tempfile.mkdtemp(prefix="forge-verify-drift-")
    miss = tempfile.mkdtemp(prefix="forge-verify-miss-")
    try:
        for d in (good, drift, miss):
            os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
            io.open(os.path.join(d, "scripts", "f.py"), "wb").write(src_body)
        for d in (good, drift, miss):
            os.makedirs(os.path.join(d, "deploy"), exist_ok=True)
        io.open(os.path.join(good, "deploy", "f.py"), "wb").write(src_body)
        io.open(os.path.join(drift, "deploy", "f.py"), "wb").write(src_body + bytes([10]))
        pairs = lambda d: [("scripts/f.py", [d + "/deploy/f.py"])]
        ok_g, bad_g = compare(pairs(good), repo_root=good)
        cases.append(("字节相同应判一致且无问题", (ok_g, bad_g), (1, [])))
        ok_d, bad_d = compare(pairs(drift), repo_root=drift)
        cases.append(("副本多一个换行必须红", (ok_d, len(bad_d)), (0, 1)))
        cases.append(("红的那条要带两侧 sha", bool(bad_d) and bad_d[0][0] != bad_d[0][1], True))
        cases.append(("红的那条要指名副本路径", bool(bad_d) and bad_d[0][3].endswith("f.py"), True))
        ok_m, bad_m = compare([("scripts/f.py", [miss + "/deploy/nope.py"])], repo_root=miss)
        cases.append(("副本缺失也算红", (ok_m, len(bad_m), bad_m[0][1] if bad_m else ""), (0, 1, "缺失")))
        # 树比对：宿主少一个文件必须报，两边一致必须不报
        tdir = os.path.join(good, "skills", "x")
        os.makedirs(os.path.join(tdir, "scripts"), exist_ok=True)
        io.open(os.path.join(tdir, "scripts", "a.py"), "wb").write(src_body)
        host_tree = os.path.join(good, "host", "skills", "x", "scripts")
        os.makedirs(host_tree, exist_ok=True)
        io.open(os.path.join(host_tree, "a.py"), "wb").write(src_body)
        pairs_t = [("skills/x", good + "/host/skills/x")]
        ok_t, bad_t = compare_trees(pairs_t, repo_root=good, hosts=[good + "/host"])
        cases.append(("树一致时报 0 处问题", (ok_t, len(bad_t)), (1, 0)))
        os.remove(os.path.join(host_tree, "a.py"))
        ok_t2, bad_t2 = compare_trees(pairs_t, repo_root=good, hosts=[good + "/host"])
        cases.append(("宿主少一个文件要报缺失", (ok_t2, len(bad_t2),
                                                bad_t2[0][1] if bad_t2 else ""), (0, 1, "缺失")))
        ok_n, bad_n = compare([("scripts/gone.py", [good + "/deploy/f.py"])], repo_root=good)
        cases.append(("源文件没了也算红", (ok_n, len(bad_n), bad_n[0][0] if bad_n else ""),
                      (0, 1, "无源文件")))
        # 字典自己也要被核：仓库里那份源文件必须存在，否则整条检查是空转
        real_ok, real_bad = compare(PAIRS)
        cases.append(("PAIRS 里的仓库源文件都在", all(b[0] != "无源文件" for b in real_bad), True))
    finally:
        for d in (good, drift, miss):
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    failed = [(n, g, w) for n, g, w in cases if g != w]
    for n, g, w in failed:
        print("  自检失败 %s：得到 %r 期望 %r" % (n, g, w))
    print("VERIFY_INSTALLS SELF-TEST: %s（%d 项）" % ("PASS" if not failed else "FAIL", len(cases)))
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="verify_installs.py", description="部署副本与仓库字节一致性检查")
    ap.add_argument("--check", action="store_true", help="比一比（默认行为）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--trees", action="store_true",
                    help="再比一遍宿主里那两棵 skill 树（比 install_sync 的写面）")
    ap.add_argument("--self-test", action="store_true", help="离线夹具自检")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    ok, problems = compare(PAIRS)
    if a.trees:
        t_ok, t_problems = compare_trees(TREE_PAIRS)
        ok += t_ok
        problems += t_problems
    if a.json:
        print(json.dumps({"consistent": ok, "problems": [list(p) for p in problems]},
                         ensure_ascii=False))
    else:
        print("部署面 %d 处一致，%d 处有问题" % (ok, len(problems)))
        for want, got, rel, cp in problems:
            print("  [%s vs %s] %s → %s" % (want, got, rel, cp))
        if problems:
            print("修法：先跑 install_sync.py 的预演（路径见 README 的安装一节），看它管不管这一面；"
                  "install_sync 够不着的副本（挂载 hook、marketplace 安装态）先备份再 cp，"
                  "cp 完重跑本脚本。")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
