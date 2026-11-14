"""猫爪重写(#3683)兼容静态扫描 v5:全量市场插件,浅克隆+增量fetch,ast 区分真代码与注释/docstring.

分级(severity,写进每条 hit):
  now     = 调用/导入 #3705 已删除的路由或模块,且不在 try 兜底里——现在就坏,进草稿
  will    = 依赖 #3714(未合并)将删除的东西且无后备——将坏,进草稿(措辞区分)
  degrade = 同类依赖但包在 try/except 或函数内导入有后备——降级,不发 issue,进「#3683 回帖候选」
  review  = 裸 48915 端口、/capabilities、openclaw 同名、deferred 同名、/tasks 契约——待核,不发 issue
  dev     = 命中位于 tests/docs(.md)——仅文档测试面
排除:仓库根存在同名本地模块(如自带 brain.py)时,绝对导入不算宿主命中,记入 local_excluded(核实报告#2).

用法:
    python tools/fetch_market_plugins.py --out plugins_all.json
    python tools/scan_nekoclaw_compat.py --plugins plugins_all.json --out scan_result.json [--cache DIR] [--jobs 8]
"""
import argparse
import ast
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

DELETED_NOW_ENDPOINT = re.compile(
    r"/plugin/execute|/computer_use/run|/browser_use/run"
    r"|/api/agent/internal/analyze_request"
    r"|/api/agent/tasks/[^\"' ]+/correction|/openclaw/availability|/mcp/availability",
)
DELETED_NOW_MODULES = {"task_executor", "plugin_filter", "deduper", "openclaw_adapter"}
WILL_MODULES = {"computer_use", "browser_use_adapter", "browser_use_anthropic"}
RE_PORT_ONLY = re.compile(r"(?<![\w.])48915(?![\d])")
RE_CAPABILITIES = re.compile(r"/capabilities(?![\w/-])")
RE_NEO_CHANNEL = re.compile(r"custom_channels|neko_channel\.py")
RE_OPENCLAW_WORD = re.compile(r"(?<![\w.])openclaw(?![\w.])", re.I)
RE_PW_MENTION = re.compile(r"(?<![\w.])playwright_browsers(?![\w.])|PLAYWRIGHT_BROWSERS_PATH")
RE_BINDTASK_DEF = re.compile(r"(?:async\s+)?def\s+bind_task\s*\(")
RE_DEFERRED = re.compile(
    r"(?<![\w.])deferred['\"]?\s*\]?\s*[:=]\s*['\"]?[Tt]rue|/api/agent/tasks/[^\"' ]+/complete",
)
DEV_PATH = re.compile(r"(^|/)(tests?|docs?|\.github)/|\.md$")


def local_module_names(root):
    names = set()
    try:
        for e in os.scandir(root):
            if e.is_file() and e.name.endswith(".py") and e.name != "__init__.py":
                names.add(e.name[:-3])
            elif e.is_dir() and os.path.isfile(os.path.join(e.path, "__init__.py")):
                names.add(e.name)
    except OSError:
        pass
    return names


def py_import_facts(path):
    """[(root_name, sub, line, guarded, fn_level)];相对导入(level>0)直接跳过."""
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except (SyntaxError, ValueError, OSError):
        return []
    parent = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node

    def ctx(node):
        guarded = fn = False
        cur = node
        while cur in parent:
            cur = parent[cur]
            if isinstance(cur, ast.Try):
                guarded = True
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = True
        return guarded, fn

    facts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                p = al.name.split(".")
                g, f = ctx(node)
                facts.append((p[0], ".".join(p[1:]), node.lineno, g, f))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            p = (node.module or "").split(".")
            if not p[0]:
                continue
            g, f = ctx(node)
            facts.append((p[0], ".".join(p[1:]), node.lineno, g, f))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "import_module" and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            p = node.args[0].value.split(".")
            g, f = ctx(node)
            facts.append((p[0], ".".join(p[1:]), node.lineno, g, f))
    return facts


def hit(rule, severity, rel, line, w, code, **extra):
    h = {"rule": rule, "severity": severity, "file": rel, "line": line, "where": w, "code": code[:160]}
    h.update(extra)
    return h


def scan_dir(root):
    hits = []
    locals_ = local_module_names(root)
    files = []
    for dp, dn, fn_ in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn_:
            if os.path.splitext(f)[1].lower() in SCAN_EXTS:
                files.append(os.path.join(dp, f))
    pw_declared = False
    for depf in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "Pipfile"):
        for fp in files:
            if os.path.basename(fp) == depf:
                try:
                    if "playwright" in open(fp, encoding="utf-8", errors="replace").read().lower():
                        pw_declared = True
                except OSError:
                    pass
    for fp in files:
        rel = os.path.relpath(fp, root).replace("\\", "/")
        try:
            if os.path.getsize(fp) > MAX_FILE:
                continue
            lines = open(fp, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        w = "dev" if DEV_PATH.search(rel) else "runtime"
        for i, raw in enumerate(lines):
            s = raw.strip()
            if DELETED_NOW_ENDPOINT.search(s):
                hits.append(hit("deleted_endpoint", "now", rel, i + 1, w, s))
            if RE_NEO_CHANNEL.search(s):
                hits.append(hit("openclaw_channel", "now", rel, i + 1, w, s))
            elif RE_OPENCLAW_WORD.search(s):
                hits.append(hit("openclaw_name", "review", rel, i + 1, w, s, note="同名嫌疑,需人工确认调用对象"))
            if RE_CAPABILITIES.search(s) and not DELETED_NOW_ENDPOINT.search(s):
                hits.append(hit("capabilities_unconfirmed", "review", rel, i + 1, w, s))
            elif RE_PORT_ONLY.search(s):
                extra = {}
                if re.search(r"[\[\(,]|port|fallback", s, re.I):
                    extra["note"] = "疑似端口列表/兜底常量,非定向请求"
                hits.append(hit("legacy_agent_port", "review", rel, i + 1, w, s, **extra))
            if RE_PW_MENTION.search(s):
                hits.append(hit("playwright_mention", "review", rel, i + 1, w, s))
            if RE_DEFERRED.search(s) and not RE_BINDTASK_DEF.match(s):
                hits.append(hit("deferred_symbol", "review", rel, i + 1, w, s))
        if rel.endswith(".py"):
            for rn, sub, line, guarded, fnlv in py_import_facts(fp):
                head = sub.split(".")[0] if sub else ""
                src = f"{rn}.{sub}" if sub else rn
                if rn == "playwright":
                    if pw_declared:
                        continue
                    sev = "degrade" if (guarded or fnlv) else "will"
                    hits.append(hit("bundled_playwright", sev, rel, line, w, "import " + src,
                                    **({"note": "函数内/try 内导入,有 ImportError 后备"} if sev == "degrade" else {})))
                elif rn in ("brain", "app", "neko_qt"):
                    if rn in locals_:
                        hits.append(hit("local_module_excluded", "excluded", rel, line, w, "import " + src,
                                        note="仓库自带同名模块,非宿主"))
                        continue
                    if rn == "app" and head != "agent_server":
                        continue
                    if rn == "neko_qt" and "brain" not in src and head not in DELETED_NOW_MODULES | WILL_MODULES:
                        continue
                    if rn == "brain":
                        when = "now" if head in DELETED_NOW_MODULES else "will"
                    else:
                        when = "now" if head in DELETED_NOW_MODULES else ("will" if head in WILL_MODULES else "will")
                    sev = "degrade" if guarded else when
                    hits.append(hit("host_internal_import", sev, rel, line, w, "import " + src,
                                    named_module=head or rn,
                                    **({"note": "try/except 兜底,常规安装不走"} if sev == "degrade" else {})))
    out, cnt = [], {}
    for h in hits:
        k = (h["rule"], h["file"])
        cnt[k] = cnt.get(k, 0) + 1
        if cnt[k] <= 3:
            out.append(h)
    return out


def norm_repo(url):
    u = (url or "").strip().rstrip("/")
    return u[len("https://"):].lower() if u.lower().startswith("https://github.com/") else None


def sync_cache(repo, dest):
    if os.path.isdir(os.path.join(dest, ".git")):
        subprocess.run(["git", "-C", dest, "fetch", "--depth", "1", "-q", "origin"],
                       capture_output=True, text=True, timeout=120)
        subprocess.run(["git", "-C", dest, "reset", "--hard", "-q", "FETCH_HEAD"],
                       capture_output=True, text=True, timeout=60)
    else:
        r = subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "-q",
                            "https://" + repo, dest], capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            return None
    return subprocess.run(["git", "-C", dest, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, timeout=30).stdout.strip() or None


def one_plugin(item, cache):
    base = {"name": item["name"], "slug": item["slug"], "repo": item.get("repo_url"),
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
        allh = scan_dir(dest)
        hits = [h for h in allh if h["severity"] != "excluded"]
        excl = [h for h in allh if h["severity"] == "excluded"]
        base.update(sha=sha, verdict="affected" if hits else "compatible", reason="",
                    hits=hits, local_excluded=excl,
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
            out.append(f"#{n} " + (("merged " + (d["merged_at"] or "")[:10]) if d["merged"] else d["state"]))
        return "; ".join(out) + "(实时)"
    except Exception:
        return "unknown(离线)"


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
               "rfc_state": rfc_state(), "total": len(results),
               "counts": {v: sum(1 for r in results if r["verdict"] == v)
                          for v in ("compatible", "affected", "unverified")},
               "plugins": results}
    json.dump(summary, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
