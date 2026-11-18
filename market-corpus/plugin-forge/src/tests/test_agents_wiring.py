# -*- coding: utf-8 -*-
"""人设文档的三条口径必须有机器杆（工单 W19.6、W19.7、W19.8）。

为什么放在测试里而不是 AUDIT 里写一句：上一轮就是「写了规矩没人核」，
14 个人设里 8 处裸相对路径、1 处写死宿主路径、安全人设零脚本命令，
全靠外部审阅才发现。文字承诺会被无声改回去，断言不会。
"""
import io
import os
import re

BARE = re.compile(r"(?<![\w$/{}-])(references/[\w.\-/]+|scripts/[\w.\-/]+|skills/[\w.\-/]+)")


def _agent_texts(repo_root):
    d = os.path.join(repo_root, "agents")
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".md"):
            yield fn, io.open(os.path.join(d, fn), encoding="utf-8").read()


def test_人设里的引用路径必须带_FORGE_HOME前缀(repo_root):
    """人设文件会被复制到各宿主的 agents 目录，cwd 不固定。

    裸 `references/x.md` 在宿主里解析到哪儿没人知道。同目录写法在 SKILL.md 与 README
    里合法，在人设里不合法——所以这条只扫 agents/。
    """
    bad = []
    for fn, text in _agent_texts(repo_root):
        for n, line in enumerate(text.split(chr(10)), 1):
            for m in BARE.finditer(line):
                # 同一行 40 个字符以内有 ${FORGE_HOME} 前缀就算带上了出处
                if "${FORGE_HOME}" in line[max(0, m.start() - 40):m.start()]:
                    continue
                bad.append("%s:%d %s" % (fn, n, m.group(1)))
    assert not bad, "人设里的裸相对路径（工单 W19.6）：%s" % bad


def test_人设不许写死宿主绝对路径(repo_root):
    """外部 kit 的脚本路径随安装位置变，写死就等于换机即断（工单 W19.7）。"""
    bad = []
    for fn, text in _agent_texts(repo_root):
        if "$HOME/.claude" in text or "C:\\Users\\" in text:
            bad.append(fn)
    assert not bad, "人设里写死了宿主路径：%s" % bad


def test_安全人设必须给机械分诊命令(repo_root):
    """执行门禁的角色不提脚本，就等于把可机械发现的一类交给人眼（工单 W19.8）。"""
    p = os.path.join(repo_root, "agents", "plugin-forge-security.md")
    text = io.open(p, encoding="utf-8").read()
    assert "security_audit" in text, "安全人设里没有 security_audit.py 这一步"
    assert "先跑机械分诊" in text, "安全人设的脚本步骤必须写明先跑分诊、再人工复核"
