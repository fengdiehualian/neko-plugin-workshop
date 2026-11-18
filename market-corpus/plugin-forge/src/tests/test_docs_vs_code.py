# -*- coding: utf-8 -*-
"""文档抄代码清单的地方，必须由机器核（工单 W19 的机器杆）。

W19 那 12 条里有一半的根因是同一个：文档把代码里的名单、日期、路径抄了一遍，
代码改了文档没跟着改，而没有任何一步会发现。
本轮已经改掉的几条（W19.1 词表、W19.9 NOTICE 承诺、W19.10 目录树、W19.12 自检口径）
在这里各钉一条断言；撤掉修法就红，不再等下一轮外部审阅来发现。
"""
import io
import os
import re


def _read(repo_root, rel):
    return io.open(os.path.join(repo_root, rel), encoding="utf-8").read()


def test_沙箱文档不许抄环境变量名单(repo_root):
    """词表只有一处真源：sandbox.py 的三个常量。"""
    doc = _read(repo_root, "skills/neko-plugin-sandbox/SKILL.md")
    code = _read(repo_root, "skills/neko-plugin-sandbox/scripts/sandbox.py")
    for sym in ("SECRET_ENV_RE", "SECRET_ENV_SUB", "KEEP_EXACT"):
        assert sym in code, "代码里的符号改名了，文档指过去会指空：%s" % sym
        assert sym in doc, "文档必须点名列的出处 %s" % sym
    # 抄表的老写法：TOKEN / SECRET / KEY / PASSWORD 连排
    assert not re.search(r"TOKEN\s*/\s*SECRET\s*/\s*KEY\s*/\s*PASSWORD", doc), \
        "文档又把词表抄了一遍（工单 W19.1）"
    # 两个真实后果必须还在，否则读者排查不到原因
    assert "WT_SESSION" in doc and "SSH_AUTH_SOCK" in doc, \
        "文档必须点名哪些变量会被删、哪些会保留"


def test_NOTICE不许承诺所有条目都在AUDIT(repo_root):
    """NOTICE 说过「every entry is recorded in AUDIT.md」，实测四个键在 AUDIT.md 零命中。"""
    notice = _read(repo_root, "NOTICE")
    assert not re.search(r"every entry[^\n]{0,80}AUDIT\.md", notice, re.I), \
        "NOTICE 的全称承诺不成立，要按族写落点（工单 W19.9）"
    for anchor in ("mattpocock-skills.md", "neko-court-source-notes.md"):
        assert anchor in notice, "落点文件必须写全名：%s" % anchor
        assert os.path.isfile(os.path.join(repo_root, "skills", "plugin-forge",
                                           "references", "neko", anchor)), \
            "NOTICE 指的落点文件必须真在：%s" % anchor


def test_README目录树必须列全顶层(repo_root):
    """树里少列一项，新人就找不到回归集与开发依赖（工单 W19.10）。"""
    readme = _read(repo_root, "README.md")
    # 认「## 目录」之后第一个代码块，别用「含 roster.json」这种关键词筛块：
    # 文末还有一块讲回归集的运行命令，那里面也有 roster.json 与 agents，筛错就全「缺」
    i = readme.index("## 目录")
    seg = readme[i:]
    m = re.search(r"```(.*?)```", seg, re.S)
    tree = m.group(1) if m else ""
    assert tree, "README 里找不到目录树那一段，判据没法核"
    must = ["agents", "skills/plugin-forge", "skills/neko-plugin-sandbox", "codex",
            "scripts", "roster.json", ".claude-plugin", "tests", "pytest.ini",
            "requirements-dev.txt", ".github", "AUDIT.md", "NOTICE", "LICENSE"]
    missing = [n for n in must if n not in tree]
    assert not missing, "README 的目录树缺：%s" % missing


def test_刷新脚本不许声称自检不读用户目录(repo_root):
    """S10 那段跑 main()，实测会只读三个真实配置文件（工单 W19.12）。"""
    src = _read(repo_root, "scripts/session_capability_refresh.py")
    assert "不读用户目录" not in src, \
        "这句口径与实测行为相反：自检确实会只读用户目录里的配置"
    # 断言要指着具体句子，不能只查两个常见词是否出现（那种断言几乎恒真）
    assert "settings.json" in src and "mcp.json" in src,         "口径要点名到底读哪几个文件，光写「只读」没有信息量"
    assert "open()" in src, "口径要写明这条是用什么测出来的"


def test_rbb_钉版四处必须一致(repo_root):
    """先搜后建的上游钉版散在四个文件里，只改两处就会自相矛盾（A791 文档项）。

    这条杆只核「四个文件写的是同一组版本号 + commit」，不核上游真有没有那个 tag：
    真伪靠人跑 GitHub API 核对，日期与出处记在 AUDIT.md 第 17 行那一格里。
    每个文件取第一个匹配：当前钉版写在最前，历史版本跟在「此前」后面。
    """
    pats = {
        "skills/plugin-forge/SKILL.md":
            r"v(\d+\.\d+\.\d+)\s*/\s*([0-9a-f]{7})",
        "skills/plugin-forge/references/workflows.md":
            r"v(\d+\.\d+\.\d+)\s*/\s*commit\s*([0-9a-f]{7})",
        "NOTICE":
            r"v(\d+\.\d+\.\d+),\s*commit\s*([0-9a-f]{7})",
        "AUDIT.md":
            r"v(\d+\.\d+\.\d+)\s*/\s*commit\s*([0-9a-f]{7})",
    }
    found = {}
    for rel, pat in pats.items():
        m = re.search(pat, _read(repo_root, rel))
        assert m, "%s 里找不到 rbb 钉版（版本号 + commit）" % rel
        found[rel] = m.groups()
    distinct = set(found.values())
    assert len(distinct) == 1, "rbb 钉版四处不一致，只改了两处？%s" % found
