#!/usr/bin/env python3
"""离线 smoke test：不联网也能跑，只依赖标准库。

跑法：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(ROOT))
# 单独运行本目录时，pytest 也会导入仓库根的插件入口；先提供离线 SDK 桩。
STUB = os.path.join(REPO_ROOT, "tests", "stub")
if os.path.isdir(STUB) and STUB not in sys.path:
    sys.path.insert(0, STUB)
SCRIPT = os.path.join(ROOT, "scripts", "repo_scan.py")

_SPEC = importlib.util.spec_from_file_location("repo_scan", SCRIPT)
repo_scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(repo_scan)


def run(args, env=None, timeout=90):
    return subprocess.run(
        [sys.executable, "-X", "utf8", SCRIPT, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


class TestHelpers(unittest.TestCase):
    def test_md_cell_escapes_pipe(self):
        self.assertEqual(repo_scan.md_cell("a|b"), "a\\|b")

    def test_md_cell_flattens_newlines(self):
        self.assertEqual(repo_scan.md_cell("a\nb\rc"), "a b c")

    def test_md_cell_empty(self):
        self.assertEqual(repo_scan.md_cell(None), "-")
        self.assertEqual(repo_scan.md_cell(""), "-")

    def test_default_since_survives_leap_day(self):
        # 2028 是闰年；旧实现 replace(year-1) 会 ValueError。减 365 天落到 2027-03-01。
        self.assertEqual(repo_scan.default_since(365, dt.date(2028, 2, 29)), "2027-03-01")

    def test_default_since_normal_date(self):
        self.assertEqual(repo_scan.default_since(365, dt.date(2026, 11, 9)), "2025-11-09")

    def test_render_table_escapes_every_cell(self):
        row = {
            "full_name": "a/b|c", "stargazersCount": 3, "license": {"key": "mit"},
            "pushedAt": "2026-01-01T00:00:00Z", "language": "Py\nthon",
            "openIssuesCount": 1, "url": "https://x|y", "matched_keyword": ["k|w"],
            "_activity": 2, "_lic_label": "MIT",
        }
        lines = repo_scan.render_table([row]).splitlines()
        self.assertEqual(len(lines), 3, "表头 + 分隔行 + 1 行数据")
        for line in lines:
            # 未被反斜杠转义的竖线才是列分隔符，必须正好 9 个（8 列）
            self.assertEqual(len(re.findall(r"(?<!\\)\|", line)), 9, f"列数被破坏：{line}")
        self.assertIn("a/b\\|c", lines[2])

    def test_render_table_stalled_marker(self):
        row = {
            "full_name": "a/b", "stargazersCount": 1, "pushedAt": "2024-01-01T00:00:00Z",
            "language": "Go", "openIssuesCount": 0, "url": "https://x",
            "matched_keyword": ["k"], "_activity": 0, "_lic_label": "MIT",
        }
        self.assertIn("⚠停滞", repo_scan.render_table([row]))


class TestOutputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rbb_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_stubbed(self, keywords, extra_args, scan_result):
        old = sys.argv, repo_scan.auth_ok, repo_scan.scan, repo_scan.which_path_only
        try:
            repo_scan.which_path_only = lambda _name: os.path.join(self.tmp, "gh.exe")
            repo_scan.auth_ok = lambda *args, **kwargs: (True, "")
            repo_scan.scan = lambda *args, **kwargs: scan_result
            sys.argv = [SCRIPT, *keywords, *extra_args]
            err, out = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                code = repo_scan.main()
        finally:
            sys.argv, repo_scan.auth_ok, repo_scan.scan, repo_scan.which_path_only = old
        return code, out.getvalue(), err.getvalue()

    def test_md_and_json_out_are_written(self):
        row = {"full_name": "o/r", "stargazersCount": 9, "pushedAt": "2026-11-01T00:00:00Z",
               "license": {"key": "mit"}, "language": "Rust", "openIssuesCount": 2,
               "url": "https://github.com/o/r", "matched_keyword": "kw"}
        md = os.path.join(self.tmp, "c.md")
        js = os.path.join(self.tmp, "c.json")
        code, out, _ = self._run_stubbed(
            ["kw"], ["--out-dir", self.tmp, "--md-out", "c.md", "--json-out", "c.json"],
            ([row], True),
        )
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(md))
        with open(md, encoding="utf-8") as fh:
            self.assertIn("o/r", fh.read())
        with open(js, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data[0]["full_name"], "o/r")
        self.assertIn("_lic_score", data[0])

    def test_no_files_written_on_rc3(self):
        md = os.path.join(self.tmp, "c.md")
        js = os.path.join(self.tmp, "c.json")
        code, _, err = self._run_stubbed(
            ["kw"], ["--out-dir", self.tmp, "--md-out", "c.md", "--json-out", "c.json"],
            ([], False),
        )
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(md))
        self.assertFalse(os.path.exists(js))
        self.assertIn("未写出", err)


class TestCli(unittest.TestCase):
    def test_help(self):
        proc = run(["--help"])
        self.assertEqual(proc.returncode, 0)
        for flag in ("--stars", "--updated-since", "--limit", "--md-out", "--json-out"):
            self.assertIn(flag, proc.stdout)

    def test_missing_keywords(self):
        proc = run([])
        self.assertEqual(proc.returncode, 2)

    def test_gh_absent_returns_4(self):
        env = dict(os.environ)
        with tempfile.TemporaryDirectory() as empty_path:
            env["PATH"] = empty_path
            proc = run(["x"], env=env)
        self.assertEqual(proc.returncode, 4)
        self.assertIn("未找到 gh CLI", proc.stderr)

    def _run_without_network(self, auth_result, scan_result):
        old = sys.argv, repo_scan.auth_ok, repo_scan.scan, repo_scan.which_path_only
        with tempfile.TemporaryDirectory() as tmp:
            try:
                repo_scan.which_path_only = lambda _name: os.path.join(tmp, "gh.exe")
                repo_scan.auth_ok = lambda *args, **kwargs: auth_result
                repo_scan.scan = lambda *args, **kwargs: scan_result
                sys.argv = [SCRIPT, "obsidian sync plugin"]
                err, out = io.StringIO(), io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                    code = repo_scan.main()
            finally:
                sys.argv, repo_scan.auth_ok, repo_scan.scan, repo_scan.which_path_only = old
        return code, err.getvalue()

    def test_all_keywords_fail_returns_3(self):
        code, err = self._run_without_network((True, ""), ([], False))
        self.assertEqual(code, 3)
        self.assertIn("检索失败 ≠ 没有同类项目", err)

    def test_gh_host_unreachable_returns_4(self):
        code, err = self._run_without_network((False, "offline"), ([], True))
        self.assertEqual(code, 4)
        self.assertIn("gh 无法确认可用", err)


class TestVerifier(unittest.TestCase):
    """本地化校验器：必须能抓到"偷偷削规则"，也必须放过合规副本。"""

    VERIFY = os.path.join(ROOT, "scripts", "verify_localized.py")
    SKILL = os.path.join(ROOT, "SKILL.md")
    ZH = os.path.join(ROOT, "references", "zh-guide.md")
    CONTRACT = os.path.join(ROOT, "localization.contract.json")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rbb_verify_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def verify(self, path, *extra):
        return subprocess.run(
            [sys.executable, "-X", "utf8", self.VERIFY, path, *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )

    def _copy_with(self, src, mutate=None, append=""):
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        if mutate:
            text = mutate(text)
        text += append
        dst = os.path.join(self.tmp, os.path.basename(src))
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return dst

    def test_canonical_skill_passes_verbatim(self):
        p = self.verify(self.SKILL, "--canonical")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PASS", p.stdout)
        self.assertIn("verbatim", p.stdout)

    def test_canonical_zh_passes_as_translated(self):
        p = self.verify(self.ZH, "--canonical")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("translated(zh)", p.stdout)

    def test_contract_hash_matches_skill(self):
        """改了 SKILL.md 的规则区却忘记更新契约时，这条会先炸。"""
        with open(self.CONTRACT, encoding="utf-8") as fh:
            want = json.load(fh)["block"]["canonical_sha256"]
        p = subprocess.run(
            [sys.executable, "-X", "utf8", self.VERIFY, "--print-hash", self.SKILL],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), want)

    def test_print_section_hash_matches_contract(self):
        with open(self.CONTRACT, encoding="utf-8") as fh:
            sections = json.load(fh)["critical_sections"]
        p = subprocess.run(
            [sys.executable, "-X", "utf8", self.VERIFY, "--print-section-hash", self.SKILL],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        got = dict(line.split(" ", 1) for line in p.stdout.strip().splitlines())
        self.assertEqual(got, {item["id"]: item["sha256"] for item in sections})

    def test_removing_the_gate_fails(self):
        def mutate(text):
            return "\n".join(line for line in text.splitlines()
                             if "Not reusable because" not in line)

        p = self.verify(self._copy_with(self.SKILL, mutate, self.DECL))
        self.assertEqual(p.returncode, 1)
        self.assertIn("gate", p.stdout)

    def test_editing_inside_block_without_lang_marker_fails(self):
        def mutate(text):
            return text.replace("Assume it already exists", "Assume it probably exists", 1)

        p = self.verify(self._copy_with(self.SKILL, mutate, self.DECL))
        self.assertEqual(p.returncode, 1)
        self.assertIn("invariants_hash", p.stdout)

    def test_localized_copy_with_declaration_passes(self):
        line = ("\nlocalized: host=test-host | kept=five rules, gate, verdict order, "
                "four-part deliverable, hard constraints | changed=none | unsupported=none\n")
        p = self.verify(self._copy_with(self.SKILL, append=line))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_localized_copy_without_declaration_fails(self):
        p = self.verify(self._copy_with(self.SKILL))
        self.assertEqual(p.returncode, 1)
        self.assertIn("declaration", p.stdout)

    def test_translated_block_without_lang_marker_fails(self):
        def mutate(text):
            return "\n".join(line for line in text.splitlines()
                             if "RBB:INVARIANTS:LANG:" not in line)

        p = self.verify(self._copy_with(self.ZH, mutate, self.DECL))
        self.assertEqual(p.returncode, 1)
        self.assertIn("invariants_hash", p.stdout)

    DECL = ("\nlocalized: host=test | kept=all | changed=packaging | unsupported=none\n")

    def test_deleting_a_rule_fails_in_translated_mode(self):
        """翻译模式不能靠"只比 6 个锚点"混过删条这一关。"""
        def mutate(text):
            return "\n".join(line for line in text.splitlines()
                             if "默认目标是加入已有公开仓库" not in line)

        p = self.verify(self._copy_with(self.ZH, mutate, self.DECL))
        self.assertEqual(p.returncode, 1)
        self.assertIn("rules_count", p.stdout)
        self.assertIn("rule:rule_2", p.stdout)

    def test_renumbering_fails(self):
        def mutate(text):
            return text.replace("4. **贡献必须真的有用", "6. **贡献必须真的有用", 1)

        p = self.verify(self._copy_with(self.ZH, mutate, self.DECL))
        self.assertEqual(p.returncode, 1)
        self.assertIn("rules_numbering", p.stdout)

    def test_softening_a_rule_title_fails(self):
        def mutate(text):
            return text.replace("贡献必须真的有用", "贡献应当有价值", 1)

        p = self.verify(self._copy_with(self.ZH, mutate, self.DECL))
        self.assertEqual(p.returncode, 1)
        self.assertIn("rule:rule_4", p.stdout)

    def test_canonical_is_refused_outside_the_package(self):
        p = self.verify(self._copy_with(self.SKILL, append=self.DECL), "--canonical")
        self.assertEqual(p.returncode, 2)
        self.assertIn("REFUSED", p.stderr)

    def test_footer_after_declaration_still_passes(self):
        p = self.verify(self._copy_with(self.SKILL, append=self.DECL + "© 2026 someone\n"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_marker_fence_demo_before_the_real_block_is_ignored(self):
        """前面用代码围栏演示一对标记时，不能把示例当真理块。"""
        demo = "```markdown\n<!-- RBB:INVARIANTS:BEGIN -->\n1. demo rule\n<!-- RBB:INVARIANTS:END -->\n```\n\n"
        p = self.verify(self._copy_with(self.SKILL, lambda t: demo + t, self.DECL))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("verbatim", p.stdout)

    def test_indented_sub_items_are_not_counted_as_rules(self):
        def mutate(text):
            return text.replace("4. **贡献必须真的有用",
                                "4. **贡献必须真的有用**\n   1. 子项甲\n   2. 子项乙", 1)

        p = self.verify(self._copy_with(self.ZH, mutate, self.DECL))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("translated(zh)", p.stdout)

    def test_marker_mentioned_in_prose_does_not_shift_the_block(self):
        """说明文字里提到标记名时，块边界仍按整行锚定，不会切错区间。"""
        prose = "说明：块用 `<!-- RBB:INVARIANTS:BEGIN -->` 与 `<!-- RBB:INVARIANTS:END -->` 圈住。\n\n"
        p = self.verify(self._copy_with(self.SKILL, lambda t: prose + t, self.DECL))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("verbatim", p.stdout)


class TestDocs(unittest.TestCase):
    """文档级回归：可改性硬闸、薄适配层、分支纪律必须真的写进正典，不能只存在于某次对话。"""

    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_integration_seam_reference_exists_and_is_linked(self):
        seam = self.read("references", "integration-seam.md")
        self.assertIn("薄适配层", seam)
        self.assertIn("150", seam)
        self.assertIn("契约测试", seam)
        paths = [("SKILL.md",), ("references", "zh-guide.md"), ("references", "contribution-protocol.md")]
        if os.path.exists(os.path.join(ROOT, "README.md")):
            # 有的宿主形态（如 N.E.K.O 插件包）按规范不随附 README，跳过该链接检查
            paths.append(("README.md",))
        for path in paths:
            self.assertIn("integration-seam.md", self.read(*path), f"未链接适配层参考：{path}")

    def test_modifiability_gate_is_in_triage_and_both_editions(self):
        triage = self.read("references", "repo-triage.md")
        self.assertIn("可改性硬闸", triage)
        self.assertIn("即使总分 ≥ 8 也不允许 Join", triage)
        self.assertIn("屎山检测", triage)
        self.assertIn("Modifiability gate", self.read("SKILL.md"))
        self.assertIn("可改性硬闸", self.read("references", "zh-guide.md"))

    def test_branch_discipline_is_in_protocol_and_hard_constraints(self):
        protocol = self.read("references", "contribution-protocol.md")
        self.assertIn("分支纪律", protocol)
        self.assertIn("禁止直接向", protocol)
        self.assertIn("Never commit or push directly to the default branch", self.read("SKILL.md"))
        self.assertIn("不直接向默认分支", self.read("references", "zh-guide.md"))

    def test_never_join_red_and_adapter_constraints(self):
        skill = self.read("SKILL.md")
        self.assertIn("Never Join a repo whose modifiability", skill)
        self.assertIn("Never let business code import upstream internals", skill)
        zh = self.read("references", "zh-guide.md")
        self.assertIn("不 Join 可改性判红的仓库", zh)
        self.assertIn("不让业务代码 import 上游内部路径", zh)


class TestPromptDigests(unittest.TestCase):
    """粘贴型提示词的版本摘要：登记表不能和文档分叉，反查不能把改过的文本当好版本。"""

    SCRIPT = os.path.join(ROOT, "scripts", "prompt_digest.py")
    HOST_ADAPTERS = os.path.join(ROOT, "references", "host-adapters.md")
    REGISTRY = os.path.join(ROOT, "prompt-digests.json")

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def run_digest(self, *args):
        return subprocess.run(
            [sys.executable, "-X", "utf8", self.SCRIPT, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )

    def block_text(self, pid, text=None):
        text = text if text is not None else self.read(self.HOST_ADAPTERS)
        m = re.search(r"id=" + re.escape(pid) + r" -->\n```text\n(.*?)\n```", text, re.S)
        if not m:
            self.fail(f"文档里没有这段提示词：{pid}")
        return m.group(1)

    def make_tree(self, adapters_text):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, "references"))
        shutil.copy2(self.SCRIPT, os.path.join(tmp, "scripts", "prompt_digest.py"))
        shutil.copy2(self.REGISTRY, os.path.join(tmp, "prompt-digests.json"))
        with open(os.path.join(tmp, "references", "host-adapters.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(adapters_text)
        return os.path.join(tmp, "scripts", "prompt_digest.py")

    def run_verify(self, script):
        return subprocess.run(
            [sys.executable, "-X", "utf8", script, "--verify"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )

    def test_registry_matches_the_shipped_prompts(self):
        p = self.run_digest("--verify")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for pid in ("zh-condensed", "en-condensed", "strict-gate", "localize-instruction"):
            self.assertIn(pid, p.stdout)

    def test_check_identifies_a_shipped_prompt(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "en.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.block_text("en-condensed"))
        p = self.run_digest("--check", path)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("MATCH  CURRENT  id=en-condensed", p.stdout)

    def test_check_rejects_a_single_word_drift(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        body = self.block_text("zh-condensed")
        drifted = body.replace("必须先搜索", "应当先搜索", 1)
        self.assertNotEqual(drifted, body)
        path = os.path.join(tmp, "drift.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(drifted)
        p = self.run_digest("--check", path)
        self.assertEqual(p.returncode, 1)
        self.assertIn("NO MATCH", p.stdout)

    def test_check_tolerates_crlf_and_trailing_whitespace(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "crlf.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.block_text("zh-condensed").replace("\n", "  \r\n") + "\r\n\r\n")
        p = self.run_digest("--check", path)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("MATCH  CURRENT  id=zh-condensed", p.stdout)

    def test_verify_catches_a_doc_change_without_a_registry_update(self):
        script = self.make_tree(self.read(self.HOST_ADAPTERS).replace("必须先搜索", "应当先搜索", 1))
        p = self.run_verify(script)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL  zh-condensed", p.stderr)

    def test_unregistered_prompt_block_is_flagged(self):
        extra = (
            "\n## 例子\n\n<!-- RBB:PROMPT:BEGIN id=not-registered -->\n"
            "```text\n随手写的一段\n```\n<!-- RBB:PROMPT:END id=not-registered -->\n"
        )
        script = self.make_tree(self.read(self.HOST_ADAPTERS) + extra)
        p = self.run_verify(script)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("not-registered", p.stderr)

    def test_broken_registry_returns_rc2_instead_of_traceback(self):
        """登记表结构坏了要报 FAIL/rc=2，不能把 KeyError traceback 甩给使用者。"""
        script = self.make_tree(self.read(self.HOST_ADAPTERS))
        bad = os.path.join(os.path.dirname(os.path.dirname(script)), "prompt-digests.json")
        for payload in (
            '{"prompts": [{"versions": []}]}',
            '{"prompts": [{"id": "x", "versions": "oops"}]}',
            '{"prompts": [{"id": "x", "versions": []}, {"id": "x", "versions": []}]}',
            '{"prompts": "not-a-list"}',
        ):
            with open(bad, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
            for args in (["--verify"], ["--check", bad]):
                p = subprocess.run(
                    [sys.executable, "-X", "utf8", script, *args],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=60, check=False,
                )
                self.assertEqual(p.returncode, 2, f"{payload} {args}: {p.stdout}{p.stderr}")
                self.assertIn("FAIL", p.stderr)
                self.assertNotIn("Traceback", p.stderr)


if __name__ == "__main__":
    unittest.main()
