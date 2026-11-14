"""猫爪重写(#3683)兼容静态扫描:全量市场插件,逐个浅克隆源码后按会删面扫.

口径来自 https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 第 4.2「会删除」与第 5 节自查.
命中分两级:
  strong(会真坏):绝对导入 brain.*/app.agent_server.*(含 importlib 变体)、4.2 具名接口、
                 openclaw/custom_channels、playwright 真 import 或 requirements 依赖行.
  review(待人工核):裸 48915 端口(/health 等仍存活接口不算坏,须看调用对象)、
                 /capabilities(可能是插件自己的路由)、playwright 字样提及(非 import)、
                 deferred 同名符号(RFC 第 6 节保持现状,用了才回帖).
行为题 4/5/6 静态证不了,兼容一律带 caveat.
插件自己的相对 from .brain 是随包本地目录,不算命中.

用法:
    python tools/fetch_market_plugins.py --out plugins_all.json
    python tools/scan_nekoclaw_compat.py --plugins plugins_all.json --out scan_result.json [--cache DIR] [--jobs 8]
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import re
import subprocess
import urllib.request

SCAN_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".json", ".toml", ".md",
             ".mjs", ".cjs", ".txt", ".cfg", ".ini", ".yaml", ".yml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv", "vendor"}
MAX_FILE = 1_500_000
RFC_PRS = [3685, 3692, 3705, 3714]

RE_HOST_STRONG = re.compile(
    r"(?:^|\s)from\s+brain(?:\.[\w.]+)?\s+import"
    r"|(?:^|\s)(?<!\. )import\s+brain(?:\.[\w.]+)?(?=\s|$)"
    r"|(?:^|\s)from\s+neko_qt(?:\.\w+)*\.brain(?:\.[\w.]+)?\s+import"
    r"|(?:^|\s)(?:from|import)\s+app\.agent_server"
    r"|(?:^|\s)from\s+app\s+import\s+agent_server"
    r"|import_module\(\s*['\"](?:brain|app\.agent_server)",
)
RE_RELATIVE_IMPORT = re.compile(r"from\s+\.\S*\s+import\b")
DELETED_MODULES = ("task_executor", "plugin_filter", "deduper", "openclaw_adapter",
                   "computer_use", "browser_use_adapter", "browser_use_anthropic")
RE_ENDPOINT = re.compile(
    r"/plugin/execute|/computer_use/run|/browser_use/run"
    r"|/api/agent/internal/analyze_request"
    r"|/api/agent/tasks/[^\"' ]+/correction"
    r"|/openclaw/availability|/mcp/availability",
)
RE_PORT_ONLY = re.compile(r"(?<![\w.])48915(?![\d])")
RE_CAPABILITIES = re.compile(r"/capabilities(?![\w/-])")
RE_NEO_CHANNEL = re.compile(r"custom_channels|neko_channel\.py")
RE_OPENCLAW_WORD = re.compile(r"(?<![\w.])openclaw(?![\w.])", re.I)
RE_PW_IMPORT = re.compile(
    r"(?:^|\s)(?:import|from)\s+playwright|require\(['\"]playwright"
    r"|^\s*playwright[\w.-]*[=><!\[]",
)
RE_PW_MENTION = re.compile(r"(?<![\w.])playwright_browsers(?![\w.])|PLAYWRIGHT_BROWSERS_PATH")
RE_BINDTASK_DEF = re.compile(r"(?:async\s+)?def\s+bind_task\s*\(")
RE_DEFERRED = re.compile(
    r"(?<![\w.])deferred['\"]?\s*\]?\s*[:=]\s*['\"]?[Tt]rue|/api/agent/tasks/[^\"' ]+/complete",
)
DEV_PATH = re.compile(r"(^|/)(tests?|docs?|\.github)/|\.md$")


def hit(rule, strength, file, line, where, s):
    return {"rule": rule, "strength": strength, "file": file, "line": line,
            "where": where, "code": s[:160]}


def scan_dir(root):
    hits = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            ext = os.path.splitext(f)[1].lower()
            if ext not in SCAN_EXTS:
                continue
            p = os.path.join(dp, f)
            try:
                if os.path.getsize(p) > MAX_FILE:
                    continue
                lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace("\\", "/")
            dev = bool(DEV_PATH.search(rel))
            for i, raw in enumerate(lines):
                s = raw.strip()
                w = "dev" if dev else "runtime"
                if RE_HOST_STRONG.search(s) and not RE_RELATIVE_IMPORT.match(s):
                    named = any(m in s for m in DELETED_MODULES)
                    h = hit("host_internal_import", "strong", rel, i + 1, w, s)
                    h["named_module"] = named
                    back = [lines[k].strip() for k in range(max(0, i - 5), i)]
                    guarded = bool(back) and (back[-1].startswith("except") or back[-1].endswith("try:"))
                    if s.startswith("try:") or guarded:
                            h = hit("host_internal_import", "review", rel, i + 1, w, s)
                            h["named_module"] = named
                            h["ctx"] = "位于 try/except 兜底分支,常规安装路径不走这里——待核,不发 issue"
                    hits.append(h)
                if RE_ENDPOINT.search(s):
                    hits.append(hit("deleted_endpoint", "strong", rel, i + 1, w, s))
                if RE_NEO_CHANNEL.search(s):
                    hits.append(hit("openclaw_channel", "strong", rel, i + 1, w, s))
                elif RE_OPENCLAW_WORD.search(s):
                    h = hit("openclaw_name", "review", rel, i + 1, w, s)
                    h["note"] = "同名嫌疑:腾讯连接页/第三方 openclaw CLI 等,需人工确认调用对象"
                    hits.append(h)
                if RE_PW_IMPORT.search(s):
                    hits.append(hit("bundled_playwright", "strong", rel, i + 1, w, s))
                elif RE_PW_MENTION.search(s):
                    hits.append(hit("playwright_mention", "review", rel, i + 1, w, s))
                if RE_CAPABILITIES.search(s) and not RE_ENDPOINT.search(s):
                    hits.append(hit("capabilities_unconfirmed", "review", rel, i + 1, w, s))
                elif RE_PORT_ONLY.search(s):
                    hits.append(hit("legacy_agent_port", "review", rel, i + 1, w, s))
                if RE_DEFERRED.search(s):
                    if not RE_BINDTASK_DEF.match(s):
                        hits.append(hit("deferred_symbol", "review", rel, i + 1, w, s))
    return hits


def norm_repo(url):
    u = (url or "").strip().rstrip("/")
    if u.lower().startswith("https://github.com/"):
        return u[len("https://"):].lower()
    return None


def sync_cache(repo, dest):
    if os.path.isdir(os.path.join(dest, ".git")):
        subprocess.run(["git", "-C", dest, "fetch", "--depth", "1", "-q", "origin"],
                       capture_output=True, text=True, timeout=120)
        subprocess.run(["git", "-C", dest, "reset", "--hard", "-q", "FETCH_HEAD"],
                       capture_output=True, text=True, timeout=60)
    else:
        r = subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "-q",
                            "https://" + repo, dest],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            return None
    sha = subprocess.run(["git", "-C", dest, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    return sha or None


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
        sha = sync_cache(repo, dest)
        if not sha:
            base.update(verdict="unverified", reason="git clone 失败(私有/删除)", hits=[])
            return base
        hits = scan_dir(dest)
        base.update(sha=sha, verdict="affected" if hits else "compatible", reason="", hits=hits,
                    caveat="行为项 4/5/6(触发时机/中断状态/入口描述)需作者自查,静态不可证")
    except subprocess.TimeoutExpired:
        base.update(verdict="unverified", reason="clone/fetch 超时", hits=[])
    return base


def rfc_state():
    try:
        out = []
        for n in RFC_PRS:
            d = json.load(urllib.request.urlopen(
                f"https://api.github.com/repos/Project-N-E-K-O/N.E.K.O/pulls/{n}", timeout=20))
            st = ("merged " + (d["merged_at"] or "")[:10]) if d["merged"] else d["state"]
            out.append(f"#{n} {st}")
        return "; ".join(out) + "(实时)"
    except Exception:
        return "unknown(离线,扫描机无法访问 GitHub API)"


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
    results.sort(key=lambda r: r["slug"])
    summary = {"scan_date": datetime.date.today().isoformat(),
               "source_issue": "https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683",
               "rfc_state": rfc_state(),
               "total": len(results),
               "counts": {v: sum(1 for r in results if r["verdict"] == v)
                          for v in ("compatible", "affected", "unverified")},
               "plugins": results}
    json.dump(summary, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
