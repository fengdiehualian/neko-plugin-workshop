"""scan_nekoclaw_compat 规则边界自检.跑法: python test/scan_rules.test.py(bun 经 test/rules.test.ts 代跑)

口径出处: RFC #3683 第 4.2 与第 5 节.
v4 变化: openclaw 拆成 RE_NEO_CHANNEL(strong,删除的通道文件)与 RE_OPENCLAW_WORD(review,同名);
try/except 兜底分支内的宿主导入降为 review(不发 issue).
"""
import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import scan_nekoclaw_compat as S

Q = chr(34)
HIT = [
    (S.RE_HOST_STRONG, "from brain.task_executor import DirectTaskExecutor"),
    (S.RE_HOST_STRONG, "import brain.deduper"),
    (S.RE_HOST_STRONG, "from brain import plugin_filter"),
    (S.RE_HOST_STRONG, "from neko_qt.ui.widget.plugins.brain import plugin_filter"),
    (S.RE_HOST_STRONG, "from app.agent_server.routes import x"),
    (S.RE_HOST_STRONG, "from app import agent_server"),
    (S.RE_HOST_STRONG, "importlib.import_module(" + Q + "brain.deduper" + Q + ")"),
    (S.RE_ENDPOINT, "GET http://127.0.0.1:48915/plugin/execute"),
    (S.RE_PORT_ONLY, "ports = (48912, 48915, 48916)"),
    (S.RE_CAPABILITIES, "r" + Q + "/capabilities" + Q + " in app.routes"),
    (S.RE_PW_IMPORT, "from playwright.sync_api import sync_playwright"),
    (S.RE_PW_IMPORT, "playwright==1.48.0"),
    (S.RE_PW_MENTION, "path = " + Q + "playwright_browsers" + Q),
    (S.RE_NEO_CHANNEL, "from scripts.custom_channels.neko_channel import connect"),
    (S.RE_OPENCLAW_WORD, '"openclaw": AgentSpec(binary="openclaw")'),
    (S.RE_OPENCLAW_WORD, "CONNECT_PATH = " + Q + "/qqbot/openclaw/connect.html" + Q),
    (S.RE_DEFERRED, "return {" + Q + "deferred" + Q + ": True}"),
]
MISS = [
    (S.RE_HOST_STRONG, "from .brain.actions import check_existing"),
    (S.RE_HOST_STRONG, "from . import brain"),
    (S.RE_HOST_STRONG, "import brainrot_model"),
    (S.RE_HOST_STRONG, "from brainlib import helper"),
    (S.RE_PORT_ONLY, "port 48916 ok"),
    (S.RE_PW_IMPORT, "3. ``PLAYWRIGHT_BROWSERS_PATH`` 与 playwright_browsers 说明"),
    (S.RE_NEO_CHANNEL, 'bun add -g openclaw"'),
    (S.RE_OPENCLAW_WORD, "neko_channel_import"),
    (S.RE_DEFERRED, "terminal_deferred = True"),
]
fail = 0
for rx, line in HIT:
    if not rx.search(line):
        print("漏报:", rx.pattern[:24], "::", line); fail += 1
for rx, line in MISS:
    if rx.search(line):
        print("误报:", rx.pattern[:24], "::", line); fail += 1

with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, "src"))
    open(os.path.join(td, "src", "a.py"), "w", encoding="utf-8").write(textwrap.dedent("""
        import os
        try:
            import brain.deduper
        except ImportError:
            brain = None
        """))
    open(os.path.join(td, "src", "b.py"), "w", encoding="utf-8").write(textwrap.dedent("""
        try:
            from . import brain
        except Exception:
            import brain  # type: ignore
        x = 1
        import brain.task_executor
        """))
    open(os.path.join(td, "src", "c.py"), "w", encoding="utf-8").write("from brain import deduper\n")
    open(os.path.join(td, "README.md"), "w", encoding="utf-8").write("调用 48915 上的 /plugin/execute 探测\n")
    hits = S.scan_dir(td)
    by = {(h["file"], h["line"]): h for h in hits}
    a1 = [h for h in hits if h["file"] == "src/a.py"]
    if len(a1) != 1 or a1[0]["strength"] != "review":
        print("集成a: try 内导入应为 review:", a1); fail += 1
    b3 = by.get(("src/b.py", 5))  # except 兜底后的 import brain
    if not b3 or b3["strength"] != "review":
        print("集成b: except 兜底导入应为 review:", b3); fail += 1
    b6 = by.get(("src/b.py", 7))
    if not b6 or b6["strength"] != "strong":
        print("集成b: 平层 brain.task_executor 应为 strong:", b6); fail += 1
    c1 = by.get(("src/c.py", 1))
    if not c1 or c1["strength"] != "strong":
        print("集成c: from brain import deduper 应 strong:", c1); fail += 1
    rd = [h for h in hits if h["file"] == "README.md"]
    if not any(h["rule"] == "deleted_endpoint" and h["where"] == "dev" for h in rd) or \
       not any(h["rule"] == "legacy_agent_port" for h in rd):
        print("集成README: dev 面/端口 review 分级错:", rd); fail += 1

print("scan_rules:", "FAIL " + str(fail) if fail else "PASS")
sys.exit(1 if fail else 0)
