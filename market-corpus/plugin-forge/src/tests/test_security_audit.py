# -*- coding: utf-8 -*-
"""security_audit 的豁免信道回归：被审对象不能给自己发通行证。

A791 CRITICAL 的原始形状：被审仓在真 token 那行末尾写一条格式合法的
`forge-declared: SEC-2 …`，再放一本含锚点的 `AUDIT.md`，旧判据就把它算成豁免、
rc=0 放行。现在豁免分两条信道（A791 第 1 条）：

- 本仓自扫：行尾声明照旧生效（仓库自持的账，零回归）；
- 审外部仓：行尾声明一律不生效，只认审核方 `--declared-list` 清单
  （仓库外 JSON，每条按行正文 SHA-256 绑定，带 reviewer 与到期日）。

夹具在 `tests/fixtures/ext_repo_selfexempt/`，跑前把 `TOKEN_SLOT` 换成真形状的假 token。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "ext_repo_selfexempt")


def _script(repo_root):
    return os.path.join(repo_root, "skills", "plugin-forge", "scripts",
                        "security_audit.py")


def _ext_repo(tmp_path):
    """复制夹具并注入假 token。

    只替换带引号的那一处赋值：docstring 里也写着 TOKEN_SLOT 这三个词，
    全量替换会让说明文字也变成假 token 形状，凭空多出一次 SEC-2 命中。
    """
    d = tmp_path / "ext_repo"
    shutil.copytree(FIXTURE, d)
    p = d / "__init__.py"
    src = p.read_text(encoding="utf-8")
    fake = "gh" + "p_" + "ForgeSelfExemptDemo0123456789"
    p.write_text(src.replace('"TOKEN_SLOT"', '"' + fake + '"'),
                 encoding="utf-8", newline="\n")
    return d


def _run(repo_root, args):
    r = subprocess.run([sys.executable, _script(repo_root)] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _json_run(repo_root, args):
    rc, out = _run(repo_root, list(args) + ["--json"])
    start = out.find("{")
    assert start >= 0, out[:400]
    return rc, json.loads(out[start:])


def _declared_line(d):
    src = (d / "__init__.py").read_text(encoding="utf-8").splitlines()
    hits = [x for x in src if "DEMO_TOKEN =" in x and "forge-declared" in x]
    assert len(hits) == 1, hits
    return hits[0]


def test_外部仓的假凭据不许被行尾声明豁免(repo_root, tmp_path):
    """自带 AUDIT.md + 合法行尾声明 + 真凭据形状：仍必须一票否决。"""
    d = _ext_repo(tmp_path)
    rc, body = _json_run(repo_root, [str(d)])
    assert rc == 2, body
    assert body["inline_declarations_honored"] is False
    assert len(body["findings"]["SEC-2"]) >= 1
    assert body["declared_ok"] == 0


def test_审核方清单可以豁免同一行(repo_root, tmp_path):
    """清单在仓库外、按行正文哈希绑定：这是外部仓唯一的豁免信道。"""
    d = _ext_repo(tmp_path)
    digest = hashlib.sha256(_declared_line(d).strip().encode("utf-8")).hexdigest()
    lst = tmp_path / "declared.json"
    lst.write_text(json.dumps({
        "schema": 1,
        "entries": [{"file": "__init__.py", "sec": "SEC-2", "sha256": digest,
                     "reviewer": "审核人甲", "expires": "2099-01-01",
                     "reason": "教学示例"}],
    }, ensure_ascii=False), encoding="utf-8", newline="\n")

    rc, body = _json_run(repo_root, [str(d), "--declared-list", str(lst)])
    assert rc == 0, body
    assert body["declared_ok"] >= 1
    assert body["findings"]["SEC-2"] == []


def test_清单哈希对不上仍然阻断(repo_root, tmp_path):
    """被审方改一行字，清单条目就作废——这正是 sha256 绑定的意义。"""
    d = _ext_repo(tmp_path)
    lst = tmp_path / "declared.json"
    lst.write_text(json.dumps({
        "schema": 1,
        "entries": [{"file": "__init__.py", "sec": "SEC-2", "sha256": "0" * 64,
                     "reviewer": "审核人甲", "expires": "2099-01-01"}],
    }, ensure_ascii=False), encoding="utf-8", newline="\n")

    rc, body = _json_run(repo_root, [str(d), "--declared-list", str(lst)])
    assert rc == 2, body
    assert body["declared_ok"] == 0


def test_清单条目缺字段就不算豁免(repo_root, tmp_path):
    """少 reviewer / 到期日 / 完整哈希：整条丢掉，不猜、不降级。"""
    d = _ext_repo(tmp_path)
    digest = hashlib.sha256(_declared_line(d).strip().encode("utf-8")).hexdigest()
    lst = tmp_path / "declared.json"
    lst.write_text(json.dumps({
        "entries": [
            {"file": "__init__.py", "sec": "SEC-2", "sha256": digest,
             "expires": "2099-01-01"},                      # 缺 reviewer
            {"file": "__init__.py", "sec": "SEC-2", "sha256": digest,
             "reviewer": "甲"},                             # 缺到期日
            {"file": "__init__.py", "sec": "SEC-2", "sha256": digest[:32],
             "reviewer": "甲", "expires": "2099-01-01"},    # 哈希截断
        ],
    }, ensure_ascii=False), encoding="utf-8", newline="\n")

    rc, body = _json_run(repo_root, [str(d), "--declared-list", str(lst)])
    assert rc == 2, body
    assert body["declared_ok"] == 0


def test_本仓自扫的行尾声明零回归(repo_root):
    """分信道不能顺手把本仓自己的账也砍了：51 处声明照旧生效。"""
    rc, body = _json_run(repo_root, [repo_root])
    assert body["inline_declarations_honored"] is True
    assert body["declared_ok"] >= 30, body["declared_ok"]
    assert body["findings"]["SEC-2"] == []
    assert rc == 0, body["verdict"]
