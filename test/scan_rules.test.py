"""scan_nekoclaw_compat v5 自检(ast+severity).跑法: python test/scan_rules.test.py"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import scan_nekoclaw_compat as S
import gen_compat_report as G

Q = chr(34)


def mk(files, extra_root=None):
    td = tempfile.mkdtemp()
    for rel, txt in files.items():
        fp = os.path.join(td, *rel.split("/"))
        os.makedirs(os.path.dirname(fp) or td, exist_ok=True)
        open(fp, "w", encoding="utf-8").write(txt)
    if extra_root:
        open(os.path.join(td, extra_root), "w", encoding="utf-8").write("CLASSES = {}\n")
    return td


def find(hits, rule, line=None, file=None):
    for h in hits:
        if h["rule"] == rule and (line is None or h["line"] == line) and (file is None or h["file"] == file):
            return h
    return None

fail = 0

def want(cond, msg):
    global fail
    if not cond:
        print("FAIL:", msg); fail += 1

# 1) 本地同名模块排除(endless_test 形态)
td = mk({"__init__.py": "x=1\n", "src/main.py": "import brain\n"}, extra_root="brain.py")
hits = S.scan_dir(td)
want(find(hits, "local_module_excluded"), "根 brain.py 应记 local_excluded")
want(not find(hits, "host_internal_import"), "本地同名不应算宿主命中")

# 2) try 兜底导入 → degrade;平铺导入 now/will
hits2 = S.scan_dir(mk({"src/a.py": "try:\n    from brain.task_executor import X\nexcept ImportError:\n    X=None\n",
                       "src/c.py": "from brain.deduper import y\n"}))
h1 = find(hits2, "host_internal_import", 2, "src/a.py")
h2 = find(hits2, "host_internal_import", 1, "src/c.py")
hw = find(S.scan_dir(mk({"src/w.py": "import brain.browser_use_adapter\n"})), "host_internal_import")
want(h1 and h1["severity"] == "degrade", "try 内导入应 degrade:" + str(h1))
want(h2 and h2["severity"] == "now", "平铺 task_executor 应 now:" + str(h2))
want(hw and hw["severity"] == "will", "browser_use_adapter 未合并应 will:" + str(hw))

# 3) playwright:函数内=degrade;模块级未声明=will;已声明=不记
tdp = mk({"src/p1.py": "def f():\n    from playwright.sync_api import s\n    return s\n"})
hp = find(S.scan_dir(tdp), "bundled_playwright")
want(hp and hp["severity"] == "degrade", "函数内 pw 应 degrade:" + str(hp))
tdp2 = mk({"src/p2.py": "from playwright.sync_api import s\n"})
hp2 = find(S.scan_dir(tdp2), "bundled_playwright")
want(hp2 and hp2["severity"] == "will", "模块级未声明 pw 应 will:" + str(hp2))
tdp3 = mk({"src/p2.py": "from playwright.sync_api import s\n", "pyproject.toml": "dependencies = [" + Q + "playwright" + Q + "]\n"})
want(not find(S.scan_dir(tdp3), "bundled_playwright"), "自带声明应豁免")

# 4) 注释/docstring 不再产生 import 命中(arcade 形态)
tdc = mk({"src/m.py": "\"\"\"\n3. 宿主用 playwright_browsers 与 PLAYWRIGHT_BROWSERS_PATH\n\"\"\"\nimport os\n"})
hc = [h for h in S.scan_dir(tdc) if h["rule"] == "bundled_playwright"]
want(not hc, "docstring 不应产生 pw import 命中:" + str(hc))
want(find(S.scan_dir(tdc), "playwright_mention"), "docstring 提及应进 review 留痕")

# 5) 字符串接口面 + dev 面
tde = mk({"src/e.py": "url = " + Q + "http://127.0.0.1:48915/computer_use/run" + Q + "\n",
          "tests/t.py": "from brain.task_executor import X\n",
          "README.md": "调用 /computer_use/run 探测\n"})
he = S.scan_dir(tde)
want(find(he, "deleted_endpoint", None, "src/e.py"), "运行时会删路由应 now 命中")
ht = find(he, "host_internal_import", None, "tests/t.py")
want(ht and ht["severity"] == "now" and ht["where"] == "dev", "tests 面应 now+dev:" + str(ht))

# 6) 裸端口 review + 列表注记
tdpt = mk({"src/l.py": "_FALLBACK_PORTS = (48912, 48915, 48916)\n"})
hpp = find(S.scan_dir(tdpt), "legacy_agent_port")
want(hpp and hpp["severity"] == "review" and "note" in hpp, "端口列表应 review+注记:" + str(hpp))

# 7) classify 端到端
pl = lambda hs, slug="x": {"name": slug, "slug": slug, "hits": hs, "verdict": "affected" if hs else "compatible", "author": "a", "downloads": 0, "sha": "c0ffee"}
want(G.classify(pl(he)) == "breaks_now", "含 now+runtime 应 breaks_now:" + G.classify(pl(he)))
want(G.classify(pl([{"severity": "will", "where": "runtime", "rule": "bundled_playwright"}])) == "will_break", "will+runtime 应 will_break")
want(G.classify(pl([{"severity": "degrade", "where": "runtime", "rule": "bundled_playwright"}])) == "degrade", "degrade 应降级类")
want(G.classify(pl([{"severity": "review", "where": "runtime", "rule": "legacy_agent_port"}])) == "review", "纯 review 应待核")
want(G.classify(pl([{"severity": "now", "where": "dev", "rule": "host_internal_import"}])) == "affected_dev", "dev-only 应文档面")
want(G.classify(pl([])) == "compatible", "零命中应兼容")
want(G.classify({"verdict": "unverified", "hits": []}) == "unverified", "unverified 保持")

print("scan_rules:", "FAIL " + str(fail) if fail else "PASS")
sys.exit(1 if fail else 0)
