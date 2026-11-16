"""v0.3.7 补深审遗留项的离线回归；只操作临时副本。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skills", "reuse-before-build")
SKILL = os.path.join(SKILL_DIR, "SKILL.md")
ZH = os.path.join(SKILL_DIR, "references", "zh-guide.md")
VERIFY = os.path.join(SKILL_DIR, "scripts", "verify_localized.py")
VENDOR = os.path.join(ROOT, "docs", "vendor.md")
DECL = "\nlocalized: host=test | kept=all | changed=packaging | unsupported=none\n"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_module("rbb_core_deep_review", os.path.join(ROOT, "rbb_core.py"))
repo_scan = load_module(
    "rbb_repo_scan_deep_review", os.path.join(SKILL_DIR, "scripts", "repo_scan.py")
)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


FULL = """## What I found
| Repo | Date |
|---|---|
| owner/demo | 2026-10-01 |

## Why this one
The requested feature fits upstream.

## What I will add (issue / PR plan)
Add the feature and tests.
Modifiability: green (clean core and tests).

## Alternatives and risks
Extend if the maintainer rejects the feature.

Verdict: Join.
"""


class TestDeliverableContract(unittest.TestCase):
    def test_canonical_templates_pass_their_own_checker(self):
        for path, marker in ((SKILL, "## What I found"), (ZH, "## 找到了什么")):
            with self.subTest(path=path):
                blocks = re.findall(r"```markdown\n(.*?)```", read(path), re.S)
                template = next(block for block in blocks if marker in block)
                result = core.check_deliverable(template)
                self.assertTrue(result["ok"], result)

    def test_every_complete_worked_example_passes(self):
        blocks = re.findall(
            r"```markdown\n(.*?)```",
            read(os.path.join(SKILL_DIR, "references", "worked-examples.md")), re.S,
        )
        self.assertEqual(len(blocks), 4)
        for block in blocks:
            with self.subTest(verdict=core.check_deliverable(block)["verdict"]):
                result = core.check_deliverable(block)
                self.assertTrue(result["ok"], result)

    def test_quick_lane_is_three_nonempty_lines_in_either_language(self):
        samples = (
            "Reused: owner/demo (MIT) — fits\nChange: add a caller\nRisk: API mismatch",
            "复用：owner/demo（MIT）——合用\n改动：增加调用\n风险：接口不匹配",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                result = core.check_deliverable(sample)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["lane"], "quick")
        bad = samples[0].replace("add a caller", "")
        self.assertIn("quick:change_empty", core.check_deliverable(bad)["missing"])

    def test_build_gate_must_be_first_and_nonempty(self):
        build = FULL.replace("Verdict: Join.", "Verdict: Build.")
        late = build + "\nNot reusable because: requirement mismatch\n"
        self.assertIn("gate_line", core.check_deliverable(late)["missing"])
        empty = "不可复用原因：\n" + build
        self.assertIn("gate_reason", core.check_deliverable(empty)["missing"])
        valid = "不可复用原因：需求本质不同。\n" + build
        self.assertTrue(core.check_deliverable(valid)["ok"])

    def test_quick_lane_no_hit_is_one_line_with_searches(self):
        text = "未找到同类项目；检索了 GitHub、PyPI、awesome-lists。"
        result = core.check_deliverable(text)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["lane"], "quick-no-match")
        self.assertIn("no hit", " ".join(result["notes"]))

    def test_quick_lane_no_hit_without_searches_still_fails(self):
        result = core.check_deliverable("没找到。")
        self.assertFalse(result["ok"])
        self.assertIn("quick:searches", result["missing"])

    def test_quick_lane_no_hit_accepts_leading_build_gate(self):
        text = (
            "不可复用原因：没有同类项目。\n"
            "未找到同类项目；检索了 GitHub、PyPI、awesome-lists。"
        )
        result = core.check_deliverable(text)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["lane"], "quick-no-match")
        self.assertEqual(result["verdict"], "Build")
        empty = "不可复用原因：\n未找到同类项目；检索了 GitHub、PyPI。"
        self.assertIn("gate_reason", core.check_deliverable(empty)["missing"])

    def test_contract_section_baseline_cannot_be_removed_or_weakened(self):
        verify = load_module("verify_localized_deep_review", VERIFY)
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        self.assertEqual(verify.check_contract_baseline(contract), [])
        contract["critical_sections"] = [
            item for item in contract["critical_sections"] if item["id"] != "step2"
        ]
        missing = verify.check_contract_baseline(contract)
        self.assertIn("critical_sections:step2", missing)
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["critical_sections"][0]["min_chars"] = 1
        self.assertIn(
            "critical_sections_floor:step2(1<500)",
            verify.check_contract_baseline(contract),
        )
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["critical_sections"][0]["anchors"].pop()
        self.assertIn(
            "critical_sections_anchors:step2",
            verify.check_contract_baseline(contract),
        )
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["rules_min"] = 1
        self.assertIn("rules_min:1!=5", verify.check_contract_baseline(contract))
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["must_have"].pop()
        self.assertIn("must_have_ids", verify.check_contract_baseline(contract))
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["declaration"]["keys"] = ["host"]
        self.assertIn("declaration_keys", verify.check_contract_baseline(contract))
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["must_have"][0]["any"] = [""]
        self.assertIn(
            "must_have:gate(any_empty_or_non_string)",
            verify.check_contract_baseline(contract),
        )
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["rule_anchors"][0]["any"] = [None]
        self.assertIn(
            "rule_anchors:rule_1(any_empty_or_non_string)",
            verify.check_contract_baseline(contract),
        )
        contract = json.loads(read(os.path.join(SKILL_DIR, "localization.contract.json")))
        contract["critical_sections"][0]["headings"] = [1]
        self.assertIn(
            "critical_sections_headings:step2(invalid)",
            verify.check_contract_baseline(contract),
        )

    def test_contract_root_and_block_must_be_objects(self):
        verify = load_module("verify_localized_contract_root", VERIFY)
        self.assertEqual(verify.contract_root_errors([]), ["contract(not_object)"])
        self.assertEqual(verify.contract_root_errors({"block": []}), ["block(not_object)"])
        self.assertEqual(verify.contract_root_errors({"block": {}}), [])
        self.assertEqual(
            verify.contract_root_errors({"block": {"begin": 1}}),
            ["block.begin(not_string)"],
        )
        self.assertEqual(
            verify.contract_root_errors({"block": {"canonical_files": [1]}}),
            ["block.canonical_files(not_string_list)"],
        )
        self.assertEqual(verify.contract_root_errors({"declaration": []}), ["declaration(not_object)"])

    def test_bad_critical_section_shape_does_not_crash(self):
        verify = load_module("verify_localized_section_shape", VERIFY)
        self.assertEqual(
            verify.check_critical_sections("## Evaluate\nactivity", ["bad"], False),
            ["critical_sections(not_object)"],
        )
        self.assertEqual(
            verify.check_critical_sections(
                "## Evaluate\nactivity",
                [{"id": "step2", "headings": [1], "anchors": []}],
                False,
            ),
            ["section:step2(bad_headings)"],
        )
        self.assertEqual(
            verify.check_critical_sections(
                "## Evaluate\nactivity",
                [{"id": "extra", "headings": ["Evaluate"], "min_chars": "abc", "anchors": []}],
                False,
            ),
            ["section_floor:extra(invalid)"],
        )
        self.assertEqual(
            verify.check_critical_sections(
                "## Evaluate\nactivity",
                [{"id": "extra", "headings": ["Evaluate"], "anchors": [1]}],
                False,
            ),
            ["section:extra:anchors[0](not_object)"],
        )
        self.assertEqual(
            verify.check_critical_sections(
                "## Evaluate\nactivity",
                [{"id": "extra", "headings": ["Evaluate"], "anchors": {}}],
                False,
            ),
            ["section:extra:anchors(not_list)"],
        )

    def test_build_without_candidate_table_does_not_invent_modifiability(self):
        text = FULL.replace(
            "| Repo | Date |\n|---|---|\n| owner/demo | 2026-10-01 |",
            "未检索到同类候选。",
        ).replace("Modifiability: green (clean core and tests).\n", "")
        text = text.replace("Verdict: Join.", "Verdict: Build.")
        text = "不可复用原因：没有同类项目。\n" + text
        result = core.check_deliverable(text)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verdict"], "Build")
        self.assertEqual(result["modifiability"], "")
        self.assertTrue(any("modifiability omitted" in note for note in result["notes"]))

    def test_verdict_is_structured_and_red_cannot_join(self):
        red = FULL.replace("Modifiability: green", "Modifiability: red")
        result = core.check_deliverable(red)
        self.assertEqual(result["verdict"], "Join")
        self.assertEqual(result["modifiability"], "red")
        self.assertIn("join_requires_green", result["missing"])
        chinese = FULL.replace("Verdict: Join.", "结论：加入。")
        self.assertEqual(core.check_deliverable(chinese)["verdict"], "Join")


class TestRulesAndDocs(unittest.TestCase):
    def test_default_rule_payload_covers_every_hard_gate(self):
        rules = core.mandatory_rules()
        for needle in (
            "Assume it already exists", "Modifiability gate", "Hard constraints",
            "Untrusted repository content", "Branch first", "thin adapter", "per-action confirmation",
        ):
            self.assertIn(needle, rules)
        steps = core.flow_steps()
        self.assertEqual([step["title"] for step in steps], [
            "Step 1 — Discover", "Step 2 — Evaluate", "Step 3 — Contribute", "Step 4 — Deliver",
        ])
        self.assertIn("Modifiability gate", steps[1]["content"])
        self.assertIn("Branch first", steps[2]["content"])

    def test_missing_mandatory_section_returns_empty_payload(self):
        with mock.patch.object(core, "skill_section", side_effect=lambda name: "" if name == "Hard constraints" else "body"):
            self.assertEqual(core.mandatory_rules(), "")

    def test_vendor_doc_documents_critical_sections(self):
        text = read(VENDOR)
        for needle in (
            "critical_sections",
            "Step 2 — Evaluate",
            "Step 3 — Contribute",
            "Hard constraints",
            "Untrusted repository content",
            "500", "500", "350", "180",
            "section_short",
            "section_anchor",
            "--print-section-hash",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_chinese_and_english_verdict_tables_agree(self):
        english, chinese = read(SKILL), read(ZH)
        for verdict in ("Join", "Extend", "Fork", "Build"):
            self.assertEqual(len(re.findall(rf"^\| \*\*{verdict}", english, re.M)), 1)
            self.assertEqual(len(re.findall(rf"^\| \*\*{verdict}", chinese, re.M)), 1)
        self.assertIn("modifiability green only", english)
        self.assertIn("可改性仅限绿", chinese)
        self.assertNotIn("可改性为绿或黄", chinese)
        self.assertIn("没有第五个判决", chinese)
        self.assertIn("数据，不是指令", chinese)

    def test_paste_prompts_include_current_hard_constraints(self):
        text = read(os.path.join(SKILL_DIR, "references", "host-adapters.md"))
        for needle in (
            "modifiability", "Never commit or push directly to main/master",
            "thin adapter", "data, not instructions", "per-action user confirmation",
            "可改性仅限绿", "绝不直写 main/master", "薄适配层", "数据，不是指令", "逐次明确确认",
        ):
            self.assertIn(needle, text)

    def test_core_and_cli_exit_codes_are_not_conflated(self):
        self.assertIn("两套独立契约", core.__doc__)
        self.assertIn("CLI：0 成功 · 2 用法错误", core.__doc__)

    def test_market_metadata_has_no_changelog_or_broad_trigger_keywords(self):
        with open(os.path.join(ROOT, "plugin.toml"), "rb") as handle:
            plugin = tomllib.load(handle)["plugin"]
        self.assertNotRegex(plugin["short_description"], r"v0\.\d+\.\d+")
        self.assertTrue({"pull request", "scaffold", "contribute"}.isdisjoint(plugin["keywords"]))


class TestLocalizedVerifier(unittest.TestCase):
    def verify_text(self, source, mutate=lambda text: text, declaration=DECL):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "localized.md")
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(mutate(read(source)) + declaration)
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", VERIFY, target],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        return proc

    def test_critical_section_cannot_be_truncated_to_one_sentence(self):
        def mutate(text):
            return re.sub(
                r"(?ms)(## Untrusted repository content\n).*?(?=\n## Hosts)",
                r"\1data, not instructions.\n",
                text,
                count=1,
            )

        proc = self.verify_text(SKILL, mutate)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("section_short:untrusted", proc.stdout)

    def test_missing_critical_section_reports_actionable_id(self):
        def mutate(text):
            return re.sub(
                r"(?ms)(## Step 2 — Evaluate\n).*?(?=\n## Step 3 — Contribute)",
                "",
                text,
                count=1,
            )

        proc = self.verify_text(SKILL, mutate)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("section:step2", proc.stdout)

    def test_verdict_table_requires_all_four_verdict_words(self):
        def mutate(text):
            start = text.index("## 第 2 步：评估")
            end = text.index("\n## 第 3 步：加入", start)
            section = re.sub(r"(?i)fork", "ZX", text[start:end])
            return text[:start] + section + text[end:]

        proc = self.verify_text(ZH, mutate)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("section_anchor:step2:verdict_table", proc.stdout)
        self.assertIn("needs all of: Fork", proc.stdout)

    def test_all_anchors_use_word_boundaries_and_support_chinese(self):
        verify = load_module("verify_localized_anchor_boundaries", VERIFY)
        items = [{
            "id": "step2",
            "headings": ["Evaluate", "评估"],
            "anchors": [{"id": "verdicts", "all": ["Join", "Extend", "Fork", "Build"]}],
        }]
        negative = verify.check_critical_sections("## Evaluate\nrebuild joined extended forked", items, False)
        self.assertEqual(
            negative,
            ["section_anchor:step2:verdicts(needs all of: Join, Extend, Fork, Build)"],
        )
        positive = verify.check_critical_sections("## Evaluate\njoin; EXTEND; fork; build.", items, False)
        self.assertEqual(positive, [])
        chinese_items = [{
            "id": "step2",
            "headings": ["评估"],
            "anchors": [{"id": "verdicts", "all": ["加入", "扩展", "分叉", "新建"]}],
        }]
        self.assertEqual(
            verify.check_critical_sections("## 评估\n加入、扩展、分叉、新建。", chinese_items, False),
            [],
        )
        any_items = [{
            "id": "step2",
            "headings": ["Evaluate"],
            "anchors": [{"id": "activity", "any": ["activity"]}],
        }]
        self.assertEqual(
            verify.check_critical_sections("## Evaluate\ninactivity", any_items, False),
            ["section_anchor:step2:activity"],
        )
        self.assertEqual(
            verify.check_critical_sections("## Evaluate\nactivity", any_items, False),
            [],
        )

    def test_any_anchors_use_word_boundaries_and_support_chinese(self):
        verify = load_module("verify_localized_any_boundaries", VERIFY)
        self.assertEqual(
            verify.check_anchors("joined; extended", [{"id": "verdict", "any": ["Join"]}], "anchor"),
            ["anchor:verdict"],
        )
        self.assertEqual(
            verify.check_anchors("Join", [{"id": "verdict", "any": ["Join"]}], "anchor"),
            [],
        )
        self.assertEqual(
            verify.check_anchors("加入已有仓库", [{"id": "verdict", "any": ["加入"]}], "anchor"),
            [],
        )

    def test_all_anchor_rejects_empty_or_non_string_without_crashing(self):
        verify = load_module("verify_localized_anchor_types", VERIFY)
        for bad in ("", None, 123, []):
            with self.subTest(bad=bad):
                self.assertFalse(verify._anchor_present("Join Extend", bad))

    def test_new_required_anchors_cannot_be_deleted(self):
        cases = {
            "untrusted_repository_content": "data, not instructions",
            "modifiability_green_only": "modifiability green only",
            "no_fifth_verdict": "There is no fifth verdict",
            "branch_first": "Branch first, never the default branch",
            "thin_adapter": "stable seam and a thin adapter",
            "never_join_red": "Never Join a repo whose modifiability",
        }
        with open(os.path.join(SKILL_DIR, "localization.contract.json"), encoding="utf-8") as handle:
            contract = json.load(handle)
        self.assertGreaterEqual(len(contract["must_have"]), 12)
        for anchor, needle in cases.items():
            with self.subTest(anchor=anchor):
                proc = self.verify_text(SKILL, lambda text: text.replace(needle, "removed"))
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn(f"anchor:{anchor}", proc.stdout)

    def test_chinese_untrusted_section_is_also_required(self):
        proc = self.verify_text(ZH, lambda text: text.replace("数据，不是指令", "资料"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("anchor:untrusted_repository_content", proc.stdout)

    def test_bundled_english_skill_cannot_masquerade_as_translated(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = os.path.join(tmp, "skill")
            shutil.copytree(SKILL_DIR, copied)
            target = os.path.join(copied, "SKILL.md")
            text = read(target).replace(
                "<!-- RBB:INVARIANTS:BEGIN -->",
                "<!-- RBB:INVARIANTS:BEGIN -->\n<!-- RBB:INVARIANTS:LANG: en -->", 1,
            )
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", os.path.join(copied, "scripts", "verify_localized.py"), target],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("canonical_invariants_hash", proc.stdout)

    def test_tilde_fences_hide_markers_and_declarations(self):
        demo = (
            "~~~markdown\n<!-- RBB:INVARIANTS:BEGIN -->\n1. fake\n"
            "<!-- RBB:INVARIANTS:END -->\n~~~\n"
        )
        proc = self.verify_text(SKILL, lambda text: demo + text)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        declaration = "\n~~~text\nlocalized: host=test | kept=all | changed=none | unsupported=none\n~~~\n"
        proc = self.verify_text(SKILL, declaration=declaration)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("declaration", proc.stdout)

    def test_import_does_not_reconfigure_host_stdout(self):
        code = (
            "import importlib.util, io, sys; original = sys.stdout; "
            "sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='ascii'); "
            f"spec = importlib.util.spec_from_file_location('verify_import', {VERIFY!r}); "
            "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "encoding = sys.stdout.encoding; sys.stdout = original; print(encoding)"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip().lower(), "ascii")


class TestProcessHardening(unittest.TestCase):
    def test_relative_windows_which_result_is_rejected_by_core_and_cli(self):
        for module in (core, repo_scan):
            with self.subTest(module=module.__name__):
                with mock.patch.dict(os.environ, {"PATH": os.path.dirname(sys.executable)}):
                    with mock.patch.object(module._gh_helpers.shutil, "which", return_value=".\\gh.exe"):
                        self.assertEqual(module.which_path_only("gh"), "")

    def test_core_retries_rate_limit_once_within_budget(self):
        rows = [{"url": "https://github.com/owner/demo", "name": "demo", "owner": {"login": "owner"}}]
        with mock.patch.object(core, "gh_json", side_effect=[RuntimeError("HTTP 429 rate limit"), rows]) as call:
            with mock.patch.object(core.time, "sleep") as sleep:
                got, error = core._scan_one("demo", "gh", 0, "2026-01-01", 3, 10)
        self.assertEqual(error, "")
        self.assertEqual(got[0]["full_name"], "owner/demo")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[1].args[2], 8)
        sleep.assert_called_once_with(2)

    def test_gh_subprocesses_request_no_console_window(self):
        with mock.patch.object(core.subprocess, "run") as run:
            core._gh("gh", ["auth", "status"], 1)
        self.assertEqual(run.call_args.kwargs["creationflags"], core.GH_CREATIONFLAGS)
        completed = subprocess.CompletedProcess(["gh"], 0, "[]", "")
        with mock.patch.object(repo_scan.subprocess, "run", return_value=completed) as run:
            repo_scan.gh_json(["search"], gh_binary="gh")
        self.assertEqual(run.call_args.kwargs["creationflags"], repo_scan.GH_CREATIONFLAGS)

    def test_missing_python_never_falls_back_to_bare_name(self):
        with mock.patch.object(core.sys, "executable", ""):
            with mock.patch.object(core, "which_path_only", return_value=""):
                self.assertEqual(core.resolve_python(), "")

    def test_core_deduplicates_keywords_before_gh_search(self):
        with mock.patch.object(core, "resolve_gh_path", return_value="C:/Tools/gh.exe"), \
                mock.patch.object(core, "auth_ok", return_value=(True, "")), \
                mock.patch.object(core, "gh_json", return_value=[]) as search:
            result = core.search_candidates(["kw", "kw"], timeout=10)
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(result["keywords"], ["kw"])
        search.assert_called_once()
        self.assertTrue(search.call_args.args[1][-1].startswith("kw "))

    def test_cli_does_not_duplicate_repeated_keywords(self):
        row = {
            "url": "https://github.com/owner/demo",
            "name": "demo",
            "owner": {"login": "owner"},
            "stargazersCount": 120,
            "pushedAt": "2026-10-01T00:00:00Z",
            "language": "Python",
            "license": {"key": "mit", "spdxId": "MIT"},
            "openIssuesCount": 3,
        }
        with mock.patch.object(repo_scan, "which_path_only", return_value="C:/Tools/gh.exe"), \
                mock.patch.object(repo_scan, "auth_ok", return_value=(True, "")), \
                mock.patch.object(repo_scan, "gh_json", return_value=[dict(row)]) as search, \
                mock.patch.object(sys, "argv", ["repo_scan.py", "kw", "kw", "--max-keywords", "1"]), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = repo_scan.main()
        self.assertEqual(code, 0)
        table = out.getvalue()
        self.assertIn("| kw |", table)
        self.assertNotIn("kw, kw", table)
        search.assert_called_once()
        self.assertTrue(search.call_args.args[0][-1].startswith("kw "))
        self.assertIn("共 1 个去重候选（1 个关键词）", err.getvalue())


if __name__ == "__main__":
    unittest.main()
