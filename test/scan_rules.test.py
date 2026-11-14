"""scan_nekoclaw_compat 规则边界自检.跑法: python test/scan_rules.test.py

断言只认两类错误:会删面必须命中(漏报),文档里合法写法必须不命中(误报).
口径出处: RFC #3683 第 4.2 与第 5 节.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import scan_nekoclaw_compat as S

Q = chr(34)
POS = [  # (rule, 样例行) 必须命中
    ("host", "from brain.task_executor import DirectTaskExecutor"),
    ("host", "import brain.deduper"),
    ("host", "from neko_qt.ui.widget.plugins.brain import plugin_filter"),
    ("host", "from app.agent_server.routes import x"),
    ("host", "import app.agent_server"),
    ("endp", "GET http://127.0.0.1:48915/plugin/execute"),
    ("endp", "url = " + Q + "/api/agent/tasks/12/correction" + Q),
    ("endp", "48915"),
    ("pw", "from playwright.sync_api import sync_playwright"),
    ("pw", "require(" + Q + "playwright" + Q + ")"),
    ("pw", "path = " + Q + "playwright_browsers" + Q),
    ("defer", "return {" + Q + "deferred" + Q + ": True}"),
    ("defer", "result[" + Q + "deferred" + Q + "] = True"),
    ("defer", "requests.post(host + f" + Q + "/api/agent/tasks/{rid}/complete" + Q + ")"),
    ("defer", "@plugin_entry(id=" + Q + "bind_task" + Q + "):"),
]
NEG = [  # 必须不命中(合法写法)
    "from .brain.actions import check_existing",
    "from . import brain",
    "from .brain import x",
    "port 48916 ok",
    "terminal_deferred = True",
    "self._deferred_count = 3",
    "POST http://127.0.0.1:48916/runs",
    "import brainrot_model",
    "from brainlib import helper",
    "PLAYWRIGHT_NOTE = " + Q + Q,
    "async def bind_task(self, task, scheduler):",
    "def bind_task(self):",
]
MAP = {"host": S.RE_HOST_IMPORT, "endp": S.RE_ENDPOINT,
       "pw": S.RE_PLAYWRIGHT, "defer": S.RE_DEFERRED}

fail = 0
for rule, line in POS:
    if not MAP[rule].search(line):
        print("漏报:", rule, "::", line); fail += 1
for line in NEG:
    if line.startswith("from ."):
        if MAP["host"].search(line) and not S.RE_RELATIVE_IMPORT.match(line):
            print("误报(相对导入未豁免):", line); fail += 1
        continue
    for rule, rx in MAP.items():
        if rule == "defer" and S.RE_BINDTASK_DEF.match(line):
            continue  # 与 scan_dir 同守卫:裸定义 bind_task 不算 deferred 使用
        if rx.search(line):
            print("误报:", rule, "::", line); fail += 1
print("scan_rules:", "FAIL " + str(fail) if fail else "PASS")
sys.exit(1 if fail else 0)
