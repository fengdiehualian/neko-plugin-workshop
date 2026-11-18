# -*- coding: utf-8 -*-
"""契约门禁的判定面回归：scope/glob、过期、根外、verify_commands、plan_sha256。

每条都对应一次真实的历史缺陷或一段没人核过的判据。
"""
import datetime
import hashlib
import io
import json
import os

import pytest


def _rec(**kw):
    rec = {"schema": "plugin-forge/contract-approval@1", "task": "T", "platform": "neko@x",
           "decision": "approved", "issued_by": "plugin-forge-contract",
           "issued_at": "2020-01-01T00:00:00+08:00", "scope": ["src/**"]}
    rec.update(kw)
    return rec


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("正文\n", encoding="utf-8")
    return tmp_path


def _one(gate, ws, target, **kw):
    p = ws / "contract-approval.json"
    p.write_text(json.dumps(_rec(**kw), ensure_ascii=False), encoding="utf-8")
    res = gate.check_targets(gate.load_approval(str(p))[0], str(ws), [str(target)])
    return res[0]


def test_scope_内允许_外拒绝(gate, ws):
    assert _one(gate, ws, ws / "src" / "a.py")[1] is True
    rel, allowed, why = _one(gate, ws, ws / "docs" / "note.py")
    assert allowed is False and "scope" in why


def test_glob_只认两种通配(gate, ws):
    """历史缺陷：`?` 与 `[ab]` 曾按正则处理，`src/[b-a].py` 直接抛 PatternError 让整轮判定消失。

    现在只认 * 与 **：`?` 与 `[...]` 按字面字符处理 → 不匹配。方向是变严：少覆盖就多用准奏。
    """
    deep = ws / "src" / "deep"
    deep.mkdir(exist_ok=True)
    (deep / "nested.py").write_text("x = 1" + chr(10), encoding="utf-8")
    cases = [
        ("src/*.py", ws / "src" / "a.py", True),
        ("src/*.py", ws / "src" / "deep" / "nested.py", False),   # 单星不跨 /
        ("src/**", ws / "src" / "deep" / "nested.py", True),
        ("src/?.py", ws / "src" / "a.py", False),
        ("src/[ab].py", ws / "src" / "a.py", False),
        ("src/[b-a].py", ws / "src" / "a.py", False),             # 旧实现在这里抛 PatternError
    ]
    for pat, target, want in cases:
        rel, allowed, why = _one(gate, ws, target, scope=[pat])
        assert allowed is want, (pat, str(target), allowed, want, why)


def test_不含斜线的模式按文件名匹配(gate, ws):
    assert _one(gate, ws, ws / "src" / "a.py", scope=["a.py"])[1] is True
    assert _one(gate, ws, ws / "src" / "b.py", scope=["a.py"])[1] is False


def test_过期就拒_未到放行(gate, ws):
    future = (datetime.datetime.now().astimezone() + datetime.timedelta(days=30)).isoformat()
    past = "2020-01-02T00:00:00+08:00"
    assert _one(gate, ws, ws / "src" / "a.py", expires_at=future)[1] is True
    rel, allowed, why = _one(gate, ws, ws / "src" / "a.py", expires_at=past)
    assert allowed is False and "过期" in why


def test_decision_非_approved_一律拒(gate, ws):
    rel, allowed, why = _one(gate, ws, ws / "src" / "a.py", decision="rejected")
    assert allowed is False and "rejected" in why


def test_根外路径必须拒(gate, ws, tmp_path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    rel, allowed, why = _one(gate, ws, outside)
    assert allowed is False and "之外" in why
    outside.unlink()


def test_非代码文件不在门禁范围(gate, ws):
    p = ws / "docs" / "note.md"
    rel, allowed, why = _one(gate, ws, p)
    assert allowed is True and "非代码" in why


def test_宿主执行面即使扩展名普通也要准奏(gate, ws):
    """历史缺陷：ui/panel.html 与 .github/workflows/*.yml 曾被当「非代码」放行。"""
    for rel_name in ("ui/panel.html", ".github/workflows/ci.yml", "assets/icon.svg"):
        f = ws / rel_name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n", encoding="utf-8")
        _rel, allowed, why = _one(gate, ws, f)
        assert allowed is False, (rel_name, allowed, why)


def test_带后缀的构建文件也是执行面(gate, ws):
    for name in ("Dockerfile.dev", "Makefile.am"):
        f = ws / name
        f.write_text("FROM x\n", encoding="utf-8")
        assert _one(gate, ws, f)[1] is False, name
    f = ws / "notes.dockerfile"
    f.write_text("讲 Dockerfile 的文章\n", encoding="utf-8")
    assert _one(gate, ws, f)[1] is True, "讲构建文件的文档不该被钉成代码"


def test_verify_commands_三种形状(gate, ws):
    # 命令本身可解析：拿本仓里真存在的脚本当验收命令
    ok = _one(gate, ws, ws / "src" / "a.py",
              verify_commands=[__file__ + " --help"])[1]
    assert ok is True
    _rel, allowed, why = _one(gate, ws, ws / "src" / "a.py",
                              verify_commands=["python " + str(ws / "ghost.py")])
    assert allowed is False and "验收命令核不过" in why
    _rel2, allowed2, _w2 = _one(gate, ws, ws / "src" / "a.py", verify_commands=[])
    assert allowed2 is False


def test_版本化解释器名不算工作区路径(gate, ws, monkeypatch):
    """A791 第 2 条①：`python3.11 -m pytest` 曾被 `"." in last` 判成工作区文件，
    报「命令指向的文件不在工作区」。

    现在只有带分隔符（或以 ./ ../ 开头）的才算路径，其余交给 which。
    which 在这里被打桩，判据方向不依赖本机装没装 python3.11。
    """
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/" + name)

    _rel, allowed, why = _one(gate, ws, ws / "src" / "a.py",
                              verify_commands=["python3.11 -m pytest"])
    assert allowed is True, why

    # exe 放宽了，参数没放宽：带后缀的路径参数照样要在工作区里真存在。
    # （`.txt` 不在后缀表里，`-r requirements-dev.txt` 这种形状仍只验可解析——
    #   那是「只验形状不执行」的已知边界，记在 SKILL.md 与 task-size-routing.md 里。）
    _r2, allowed2, why2 = _one(gate, ws, ws / "src" / "a.py",
                               verify_commands=["pip3.12 install -r ghost.py"])
    assert allowed2 is False and "引用的文件不在工作区" in why2, why2

    # 放宽 exe 判定不等于放宽参数：带后缀的路径参数照样要在工作区里真存在
    _r3, allowed3, why3 = _one(gate, ws, ws / "src" / "a.py",
                               verify_commands=["python3.11 ghost.py"])
    assert allowed3 is False and "引用的文件不在工作区" in why3, why3


def test_plan_sha256_变了旧准奏失效(gate, ws):
    plan = ws / "PLAN.md"
    plan.write_text("第一版方案\n", encoding="utf-8")
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    ok = _one(gate, ws, ws / "src" / "a.py", plan_file="PLAN.md", plan_sha256=digest)
    assert ok[1] is True
    plan.write_text("第二版方案\n", encoding="utf-8")
    _rel, allowed, why = _one(gate, ws, ws / "src" / "a.py",
                              plan_file="PLAN.md", plan_sha256=digest)
    assert allowed is False and "方案已改动" in why


def test_填了摘要却没有方案文件必须拒(gate, ws):
    _rel, allowed, why = _one(gate, ws, ws / "src" / "a.py",
                              plan_sha256="a" * 64)
    assert allowed is False and "plan_file" in why


def test_无效准奏文件对所有代码目标一致拒绝(gate, ws):
    p = ws / "broken.json"
    p.write_text("{不是 JSON", encoding="utf-8")
    res = gate.check_targets(None, str(ws), [str(ws / "src" / "a.py")])
    assert res[0][1] is False
