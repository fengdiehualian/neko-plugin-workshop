# -*- coding: utf-8 -*-
"""generate_roster_tables 的边界回归：历史上它静默不写、缺标记不报。

覆盖三条老 bug：
  1. 缺 BEGIN/END 标记必须报 error，不许静默跳过（曾经 --write 退 0 而表格没更新）
  2. re.sub 会把表格写坏，所以 --write 的结果要逐字对得住 render_table
  3. 人数声明整句消失时不能算「一致」（名单空转）
"""
import io
import json
import os
import shutil
import subprocess
import sys

import pytest

MEMBERS = [
    {"agent_id": "a-lead", "role": "lead", "name_zh": "甲", "name_en": "A", "duty": "定方向",
     "when_to_dispatch": "开头", "max_turns": "8", "file": "agents/a-lead.md"},
    {"agent_id": "a-exp", "role": "expert", "name_zh": "乙", "name_en": "B", "duty": "写代码",
     "when_to_dispatch": "实现", "max_turns": "12", "file": "agents/a-exp.md"},
    {"agent_id": "a-exp2", "role": "expert", "name_zh": "丙", "name_en": "C", "duty": "审代码",
     "when_to_dispatch": "复核", "max_turns": "12", "file": "agents/a-exp2.md"},
]


@pytest.fixture
def fake_repo(tmp_path, monkeypatch, roster):
    """造一份最小仓库：roster.json、agents/*.md、带标记的目标文档。"""
    root = str(tmp_path)
    (tmp_path / "agents").mkdir()
    for m in MEMBERS:
        (tmp_path / "agents" / os.path.basename(m["file"])).write_text(
            "---\nname: %s\ndescription: 最大轮数 12 与 displayName.en 齐全\n---\n正文\n"
            % m["agent_id"], encoding="utf-8")
    (tmp_path / "roster.json").write_text(
        json.dumps({"members": MEMBERS}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roster, "ROOT", root)
    monkeypatch.setattr(roster, "ROSTER_PATH", os.path.join(root, "roster.json"))
    targets = []
    for rel, marker, subset, cols in roster.TARGETS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        block = roster.render_table(MEMBERS, subset, cols)
        p.write_text("抬头\n<!-- BEGIN %s -->\n%s\n<!-- END %s -->\n尾巴\n"
                     % (marker, block, marker), encoding="utf-8")
    return root


def test_新鲜表格_check_必须_ok(fake_repo, roster):
    errs = []
    assert roster.sync_tables(roster.load_roster(), False, errs) == []
    assert errs == []


def test_缺标记必须报_error_不许静默跳过(fake_repo, roster, monkeypatch):
    """历史 bug：标记被删掉后 --check 仍然退 0，等于没人管表格新不新鲜。"""
    monkeypatch.setattr(roster, "TARGETS",
                        [(roster.TARGETS[0][0], "no-such-marker", "all", roster.TARGETS[0][3])])
    errs = []
    roster.sync_tables(roster.load_roster(), False, errs)
    assert any("缺少标记" in x for x in errs), errs


def test_write_之后必须真新鲜_且不动标记外的文字(fake_repo, roster):
    first = os.path.join(fake_repo, roster.TARGETS[0][0].replace("/", os.sep))
    before = io.open(first, encoding="utf-8").read()
    stale = roster.sync_tables(roster.load_roster(), False, [])
    assert stale == []
    # 真不新鲜的情形：把表里一行删掉
    lines = before.split("\n")
    cut = "\n".join([x for x in lines if not x.startswith("| a-exp |")])
    io.open(first, "w", encoding="utf-8", newline="\n").write(cut)
    assert roster.sync_tables(roster.load_roster(), False, [])
    roster.sync_tables(roster.load_roster(), True, [])
    after = io.open(first, encoding="utf-8").read()
    assert "| a-exp |" in after, after[:400]
    assert "抬头" in after and "尾巴" in after, "标记外的文字被吃掉了"
    assert roster.sync_tables(roster.load_roster(), False, []) == []


def test_人数声明整句消失要报空转(fake_repo, roster, monkeypatch):
    """历史 bug：所有声明句子被删光后，sync_counts 0 命中，--check 退 0。"""
    docs = tmp = fake_repo
    d = os.path.join(tmp, "docs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "claim.md")
    io.open(p, "w", encoding="utf-8", newline="\n").write("这里不写编制人数。\n")
    monkeypatch.setattr(roster, "COUNT_FILES", ["docs/claim.md"])
    monkeypatch.setattr(roster, "COUNT_EXEMPT", [])
    problems = roster.sync_counts(roster.load_roster(), False)
    assert any("空转" in x for x in problems), problems


def test_名单外的活声明要报(fake_repo, roster, monkeypatch):
    d = os.path.join(fake_repo, "docs")
    os.makedirs(d, exist_ok=True)
    io.open(os.path.join(d, "stray.md"), "w", encoding="utf-8", newline="\n").write(
        "现有 1 主理人 + 9 专家。\n")
    monkeypatch.setattr(roster, "COUNT_FILES", ["docs/stray.md"])
    monkeypatch.setattr(roster, "COUNT_EXEMPT", [])
    problems = roster.sync_counts(roster.load_roster(), False)
    assert any("9 专家" in x for x in problems), problems


def test_交付证据规范句必须逐字在名单文件里(fake_repo, roster, monkeypatch):
    d = os.path.join(fake_repo, "docs")
    os.makedirs(d, exist_ok=True)
    src = os.path.join(fake_repo, roster.REPORT_SOURCE.replace("/", os.sep))
    os.makedirs(os.path.dirname(src), exist_ok=True)
    io.open(src, "w", encoding="utf-8", newline="\n").write("基准：" + roster.REPORT_FORMULA + "\n")
    miss = os.path.join(d, "miss.md")
    io.open(miss, "w", encoding="utf-8", newline="\n").write("没有那句。\n")
    monkeypatch.setattr(roster, "REPORT_FILES", ["docs/miss.md"])
    problems = roster.check_report_formula()
    assert any("缺交付证据硬格式" in x for x in problems), problems
    io.open(miss, "w", encoding="utf-8", newline="\n").write(
        roster.REPORT_FORMULA + "\n" + roster.REPORT_FORMULA + "\n")
    assert any("出现 2 次" in x for x in roster.check_report_formula())


def test_闸门快照接线必须在位(repo_root):
    """工单 W15：SKILL §6 是强制口径，人设与验收清单必须各有一处接线文字。

    这条是机器杆。上一轮实测 `grep -c 闸门快照` 在 14 个人设与 workflows.md 全是 0：
    §6 写了强制，接线却是断的，lead 可以交付而不触发它。
    """
    import io as _io
    import os
    pairs = [
        ("agents/plugin-forge-lead.md", "闸门快照"),
        ("skills/plugin-forge/references/workflows.md", "闸门快照"),
    ]
    for rel, needle in pairs:
        text = _io.open(os.path.join(repo_root, rel), encoding="utf-8").read()
        assert needle in text, "%s 里 §6 闸门快照的接线文字不见了（工单 W15）" % rel
    # 边界不许被这次接线冲掉
    skill = _io.open(os.path.join(repo_root, "skills/plugin-forge/SKILL.md"),
                     encoding="utf-8").read()
    assert "可选参考" in skill, "SKILL 里「delivery-self-check 是可选参考」那句必须还在"


def test_仓内真实文件当前必须全绿(roster, repo_root):
    """这条不用临时仓库：直接核真仓现状，防止「测试夹具绿了、仓库本身烂着」。"""
    assert roster.sync_tables(roster.load_roster(), False, []) == []
    assert roster.sync_counts(roster.load_roster(), False) == []
    assert roster.check_report_formula() == []
    assert roster.stray_count_claims() == []


# --- main() 的接线本身也要有用例 ---
# 上面的用例都直接调函数，接线断掉（函数还在、没人调用）它们全都会绿。
# 这两条钉的就是「有没有人调用」。

def test_main_check_在真仓必须退_0(roster, repo_root):
    assert roster.main(["--check"]) == 0


def test_main_必须真调用口径核对(roster, repo_root, tmp_path, capsys):
    """把仓库复制一份，删掉某个文件里的规范句：main --check 必须退 1。"""
    dst = str(tmp_path / "repo")
    shutil.copytree(repo_root, dst, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".drift-cache", ".pytest_cache", "node_modules"))
    victim = os.path.join(dst, "agents", "plugin-forge-docs.md")
    t = io.open(victim, encoding="utf-8").read()
    assert roster.REPORT_FORMULA in t
    io.open(victim, "w", encoding="utf-8", newline=chr(10)).write(
        t.replace(roster.REPORT_FORMULA, "（这句被测试删掉了）"))
    # ROOT 由 __file__ 推出来，所以这里跑副本里的脚本，而不是改全局
    r = subprocess.run([sys.executable, os.path.join(dst, "scripts", "generate_roster_tables.py"),
                        "--check"], capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, PYTHONUTF8="1"))
    assert r.returncode == 1, r.stdout[-400:]
    assert "缺交付证据硬格式" in r.stdout, r.stdout[-400:]
