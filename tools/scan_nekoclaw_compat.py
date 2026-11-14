"""猫爪重写(#3683)兼容静态扫描:全量市场插件,逐个浅克隆源码后按会删面扫.

口径来自 https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 第 4.2「会删除」与第 5 节自查.
静态可证四类:host 内部模块 import(自查题1,绝对导入 brain.*/app.agent_server.* 一律算,
不只七个具名模块——职责搬走后整个包都没了)、会删接口与 48915(题2)、随包 playwright(题3)、
deferred(题7)。行为题 4/5/6 静态证不了,兼容一律带 caveat,由作者自查确认.
插件自己的相对 from . import brain / from .brain 是随包本地目录,不算命中(RFC 问的是绝对导入).
命中再按文件位置分 runtime 与 dev(tests/、.md、注释文档串),报告器据此分强弱.

用法:
    python tools/scan_nekoclaw_compat.py --plugins plugins_all.json --out scan_result.json [--cache DIR] [--jobs 8]
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import re
import subprocess

SCAN_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".json", ".toml", ".md", ".mjs", ".cjs"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv", "vendor"}
MAX_FILE = 1_500_000

RE_HOST_IMPORT = re.compile(
    r"(?:^|\s)from\s+brain\.[\w.]+ |(?:^|\s)import\s+brain(?:\.[\w.]+)?(?=\s|$)"
    r"|(?:^|\s)from\s+neko_qt(?:\.\w+)*\.brain(?:\.[\w.]+)?\s+import"
    r"|(?:^|\s)(?:from|import)\s+app\.agent_server",
)
RE_RELATIVE_IMPORT = re.compile(r"from\s+\.\S*\s+import\b")
DELETED_MODULES = ("task_executor", "plugin_filter", "deduper", "openclaw_adapter",
                   "computer_use", "browser_use_adapter", "browser_use_anthropic")
RE_ENDPOINT = re.compile(
    r"/plugin/execute|/computer_use/run|/browser_use/run"
    r"|/api/agent/internal/analyze_request"
    r"|/api/agent/tasks/[^\"' ]+/correction"
    r"|/mcp/availability|/openclaw/availability"
    r"|(?<![\w.])48915(?![\d])",
)
RE_PLAYWRIGHT = re.compile(
    r"(?:^|\s)(?:import|from)\s+playwright|require\(['\"]playwright|playwright_browsers",
)
RE_DEFERRED = re.compile(
    r"(?<![\w.])deferred['\"]?\s*\]?\s*[:=]\s*['\"]?[Tt]rue|/api/agent/tasks/[^\"' ]+/complete|(?<![\w.])bind_task(?![\w])",
)
RULES = [("host_internal_import", RE_HOST_IMPORT), ("deleted_endpoint", RE_ENDPOINT),
         ("bundled_playwright", RE_PLAYWRIGHT), ("deferred_channel", RE_DEFERRED)]
RE_BINDTASK_DEF = re.compile(r"(?:async\s+)?def\s+bind_task\s*\(")
DEV_PATH = re.compile(r"(^|/)(tests?|docs?|\.github)/|\.md$")


def norm_repo(url):
    u = (url or "").strip().rstrip("/")
    if u.lower().startswith("https://github.com/"):
        return u[len("https://"):].lower()
    return None


def scan_dir(root):
    hits = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if os.path.splitext(f)[1].lower() not in SCAN_EXTS:
                continue
            p = os.path.join(dp, f)
            try:
                if os.path.getsize(p) > MAX_FILE:
                    continue
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace("\\", "/")
            dev = bool(DEV_PATH.search(rel))
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                rel_imp = bool(RE_RELATIVE_IMPORT.match(s))
                for rule, rx in RULES:
                    if rule == "host_internal_import" and rel_imp:
                        continue
                    if rule == "deferred_channel" and RE_BINDTASK_DEF.match(s):
                        continue  # 宿主用 entry_id 调 bind_task,裸定义不算协议使用
                    if rx.search(line):
                        named = any(m in s for m in DELETED_MODULES) if rule == "host_internal_import" else None
                        hits.append({"rule": rule, "file": rel, "line": i, "where": "dev" if dev else "runtime",
                                     "named_module": named, "code": s[:160]})
    return hits


def one_plugin(item, cache):
    base = {"name": item["name"], "slug": item["slug"], "repo": item.get("repo_url"),
            "plugin_directory": item.get("plugin_directory"),
            "author": item.get("author_name"), "downloads": item.get("download_count")}
    repo = norm_repo(item.get("repo_url"))
    if not repo:
        base.update(verdict="unverified", reason="repo_url 不是公开 GitHub 仓库", hits=[])
        return base
    dest = os.path.join(cache, repo.replace("/", "__"))
    try:
        if not os.path.isdir(os.path.join(dest, ".git")):
            r = subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "-q",
                                "https://" + repo, dest],
                               capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                base.update(verdict="unverified", reason="git clone 失败(私有/删除)", hits=[])
                return base
        hits = scan_dir(dest)
        base.update(verdict="affected" if hits else "compatible", reason="", hits=hits,
                    caveat="行为项 4/5/6(触发时机/中断状态/入口描述)需作者自查,静态不可证")
    except subprocess.TimeoutExpired:
        base.update(verdict="unverified", reason="clone 超时 180s", hits=[])
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugins", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=os.path.expanduser("~/nekoclaw-scan-cache"))
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    items = json.load(open(a.plugins, encoding="utf-8"))
    os.makedirs(a.cache, exist_ok=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(one_plugin, it, a.cache) for it in items]
        for i, fu in enumerate(futs, 1):
            results.append(fu.result())
            if i % 20 == 0 or i == len(items):
                print(f"[{i}/{len(items)}]", flush=True)
    summary = {"scan_date": datetime.date.today().isoformat(),
               "source_issue": "https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683",
               "rfc_state": "PR1 #3685 已合并; PR2 #3692 已合并; 切换点 PR3 #3705 已于 2026-11-14 合并; PR4-6 未开",
               "total": len(results),
               "counts": {v: sum(1 for r in results if r["verdict"] == v)
                          for v in ("compatible", "affected", "unverified")},
               "plugins": results}
    json.dump(summary, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
