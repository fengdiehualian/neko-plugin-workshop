"""N.E.K.O 适配版的离线自检（不依赖宿主 SDK，只用标准库）。

跑法（在仓库根目录）：
    python -m unittest discover -s tests -v
"""

import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SPEC = importlib.util.spec_from_file_location("rbb_core", os.path.join(ROOT, "rbb_core.py"))
rbb_core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rbb_core)

SKILL_DIR = os.path.join(ROOT, "skills", "reuse-before-build")
CONTRACT = os.path.join(SKILL_DIR, "localization.contract.json")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")


class TestManifest(unittest.TestCase):
    def setUp(self):
        try:
            import tomllib
        except ModuleNotFoundError:  # 3.10 及以下
            self.skipTest("需要 Python 3.11+ 的 tomllib")
        with open(os.path.join(ROOT, "plugin.toml"), "rb") as fh:
            self.manifest = tomllib.load(fh)

    def test_identity_fields(self):
        p = self.manifest["plugin"]
        self.assertEqual(p["id"], "reuse_before_build")
        self.assertTrue(p["name"])
        self.assertGreater(len(p["description"]), 40)
        self.assertGreater(len(p["short_description"]), 40)
        self.assertRegex(p["version"], r"^\d+\.\d+\.\d+$")
        self.assertGreaterEqual(len(p["keywords"]), 8)

    def test_entry_names_the_class(self):
        self.assertRegex(self.manifest["plugin"]["entry"],
                         r"^plugin\.plugins\.reuse_before_build:[A-Za-z_][A-Za-z0-9_]*$")

    def test_runtime_timeout_in_range(self):
        t = self.manifest["plugin_runtime"]["timeout"]
        self.assertTrue(0 < t <= 300, t)

    def test_ui_guide_exists(self):
        guide = self.manifest["plugin"]["ui"]["guide"][0]
        self.assertTrue(os.path.isfile(os.path.join(ROOT, guide["entry"])), guide["entry"])


class TestEntryTools(unittest.TestCase):
    """入口方法必须都挂 llm_tool，否则 AI 看不到。"""

    def setUp(self):
        with open(os.path.join(ROOT, "__init__.py"), encoding="utf-8") as fh:
            self.tree = ast.parse(fh.read())

    def _tool_names(self):
        names = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                        and dec.func.id == "llm_tool"):
                    continue
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        names.append(kw.value.value)
        return names

    def test_expected_tools_exist(self):
        got = set(self._tool_names())
        self.assertEqual(got, {"rbb_rules", "rbb_scan", "rbb_check", "rbb_verify", "rbb_status"})

    def test_tool_descriptions_and_schemas(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                        and dec.func.id == "llm_tool"):
                    continue
                kw = {k.arg: k.value for k in dec.keywords}
                desc = kw.get("description")
                self.assertTrue(isinstance(desc, ast.Constant) and len(str(desc.value)) > 60,
                                f"{node.name} 的 description 太短")
                params = kw.get("parameters")
                self.assertTrue(isinstance(params, ast.Dict), f"{node.name} 缺 parameters")


class TestBundledSkill(unittest.TestCase):
    def test_contract_hash_matches_block(self):
        with open(CONTRACT, encoding="utf-8") as fh:
            want = json.load(fh)["block"]["canonical_sha256"]
        block = rbb_core.core_rules_block()
        norm = "\n".join(line.rstrip() for line in block.strip().splitlines())
        self.assertEqual(hashlib.sha256(norm.encode("utf-8")).hexdigest(), want)

    def test_rules_have_five_numbered_items(self):
        nums = [int(n) for n in re.findall(r"^(\d+)\.\s", rbb_core.core_rules_block(), re.M)]
        self.assertEqual(nums, [1, 2, 3, 4, 5])

    def test_verifier_passes_on_bundled_copy(self):
        p = subprocess.run(
            [sys.executable, "-X", "utf8", rbb_core.VERIFY_SCRIPT, SKILL_MD],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("verbatim", p.stdout)
        self.assertIn("rules=[1, 2, 3, 4, 5]/5", p.stdout)

    def test_verifier_rejects_deleted_rule(self):
        with open(SKILL_MD, encoding="utf-8") as fh:
            text = fh.read()
        broken = "\n".join(line for line in text.splitlines() if "Assume it already exists" not in line)
        broken += "\nlocalized: host=test | kept=all | changed=none | unsupported=none\n"
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "SKILL.md")
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(broken)
            p = subprocess.run(
                [sys.executable, "-X", "utf8", rbb_core.VERIFY_SCRIPT, dst],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("invariants_hash", p.stdout)


class TestSearchWrapper(unittest.TestCase):
    def setUp(self):
        # 直接测试 gh 调用时提供一个明确的 PATH 解析结果；路径拒绝逻辑另有独立回归。
        patcher = unittest.mock.patch.object(rbb_core, "which_path_only", return_value="C:/Tools/gh.exe")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_gh_returns_code_2(self):
        res = rbb_core.search_candidates(["anything"], gh_binary="gh-not-installed-xyz")
        self.assertEqual(res["code"], 2)
        self.assertFalse(res["ok"])
        self.assertIn("gh", res["reason"])

    def test_empty_keywords_returns_code_2(self):
        res = rbb_core.search_candidates(["", "  "])
        self.assertEqual(res["code"], 2)

    def test_default_since_survives_leap_day(self):
        import datetime as dt
        self.assertEqual(rbb_core.default_since(365, dt.date(2028, 2, 29)), "2027-03-01")

    def test_keyword_starting_with_dash_goes_after_terminator(self):
        """`-foo` 不能被 gh 当成选项：所有 flag 在 `--` 前，query 在 `--` 后。"""
        argv = rbb_core.search_args("-foo", 50, "2025-01-01", 6)
        self.assertEqual(argv[:2], ["search", "repos"])
        self.assertEqual(argv[-2], "--")
        self.assertTrue(argv[-1].startswith("-foo "))
        self.assertLess(argv.index("--archived=false"), argv.index("--"))

    def test_search_args_use_inclusive_star_and_date_bounds(self):
        argv = rbb_core.search_args("clip tool", 50, "2025-01-01", 6)
        self.assertIn("clip tool stars:>=50 pushed:>=2025-01-01", argv[-1])

    def test_keyword_with_nul_or_qualifier_is_rejected_before_search(self):
        for bad in ("bad\x00kw", "clip stars:>999999", "clip user:someone", "x" * 201):
            res = rbb_core.search_candidates([bad], gh_binary="gh")
            self.assertEqual(res["code"], 2, bad)
            self.assertIn("关键词", res["reason"])

    def test_scan_uses_resolved_absolute_gh_path(self):
        """只把 `gh` 字面量交给 subprocess 仍可能被当前目录同名文件劫持。"""
        seen = {}

        def fake_scan(kw, exe, stars, since, limit, timeout):
            seen.update(exe=exe)
            return [], ""

        with unittest.mock.patch.object(
            rbb_core, "resolve_gh_path", return_value="C:/Tools/gh.exe"
        ):
            with unittest.mock.patch.object(rbb_core, "auth_ok", return_value=(True, "")):
                with unittest.mock.patch.object(rbb_core, "_scan_one", side_effect=fake_scan):
                    res = rbb_core.search_candidates(["video clip"], timeout=5)
        self.assertEqual(res["code"], 0)
        self.assertEqual(seen["exe"], "C:/Tools/gh.exe")

    def test_command_path_accepts_its_own_resolved_absolute_gh(self):
        """search_candidates 传给 auth_ok/_scan_one 的绝对路径不能被自家白名单拦掉。"""
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "gh.exe")
            with open(exe, "w", encoding="utf-8") as fh:
                fh.write("")
            with unittest.mock.patch.object(rbb_core, "which_path_only", return_value=exe):
                self.assertEqual(rbb_core.command_path(exe), exe)
                self.assertEqual(rbb_core.resolve_gh_path("gh.exe"), exe)

    def test_command_path_rejects_unrelated_absolute_gh(self):
        """绝对路径必须与 PATH 解析结果一致，不能凭 basename 放行任意 gh.exe。"""
        with tempfile.TemporaryDirectory() as td:
            allowed = os.path.join(td, "allowed", "gh.exe")
            evil = os.path.join(td, "evil", "gh.exe")
            os.makedirs(os.path.dirname(allowed))
            os.makedirs(os.path.dirname(evil))
            for p in (allowed, evil):
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write("")
            with unittest.mock.patch.object(rbb_core, "which_path_only", return_value=allowed):
                self.assertEqual(rbb_core.command_path(evil), "")
                self.assertEqual(rbb_core.command_path(os.path.join(td, "evil", "curl.exe")), "")

    def test_search_candidates_absolute_gh_path_survives_auth(self):
        """真实 auth_ok + command_path 的白名单链路，不能只靠 mock auth_ok 掩盖回归。"""
        with open(os.path.join(ROOT, "tests", "fixtures", "gh_search_repos.json"),
                  encoding="utf-8") as fh:
            payload = fh.read()
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "gh.exe")
            with open(exe, "w", encoding="utf-8") as fh:
                fh.write("")
            seen = []

            def fake_gh(binary, args, timeout):
                seen.append((binary, args))
                if args[:2] == ["auth", "status"]:
                    return subprocess.CompletedProcess([binary, *args], 0, "", "")
                return subprocess.CompletedProcess([binary, *args], 0, payload, "")

            with unittest.mock.patch.object(rbb_core, "resolve_gh_path", return_value=exe):
                with unittest.mock.patch.object(rbb_core, "which_path_only", return_value=exe):
                    with unittest.mock.patch.object(rbb_core, "_gh", side_effect=fake_gh):
                        res = rbb_core.search_candidates(["video clip"], timeout=10)
        self.assertEqual(res["code"], 0, res["reason"])
        self.assertTrue(res["rows"])
        self.assertTrue(all(pair[0] == exe for pair in seen), seen)

    def test_embedded_nul_from_subprocess_is_a_runtime_error(self):
        with unittest.mock.patch.object(rbb_core, "_gh", side_effect=ValueError("embedded null byte")):
            rows, err = rbb_core._scan_one("k", "gh", 50, "2025-01-01", 6, 5)
        self.assertEqual(rows, [])
        self.assertIn("gh 参数非法", err)

    def test_recorded_gh_json_parses_into_rows(self):
        """契约测试：直接喂录好的 `gh search repos --json` 输出，不 mock 解析函数。"""
        path = os.path.join(ROOT, "tests", "fixtures", "gh_search_repos.json")
        with open(path, encoding="utf-8") as fh:
            rows = rbb_core.parse_search_output(fh.read())
        self.assertEqual(len(rows), 2)
        row = rbb_core.normalize_search_row(rows[0], "video clip")
        self.assertEqual(row["full_name"], "owner/autoclip")
        self.assertEqual(row["matched_keyword"], "video clip")
        self.assertEqual(row["license"]["spdxId"], "MIT")
        self.assertEqual(rbb_core.license_flags(rows[1]["license"])[0], 0)

    def test_scan_one_end_to_end_with_fake_gh(self):
        """用录好的 JSON 回放 gh 输出，验证 _scan_one 的 argv（含 `--` 位置）。"""
        with open(os.path.join(ROOT, "tests", "fixtures", "gh_search_repos.json"),
                  encoding="utf-8") as fh:
            payload = fh.read()
        seen = {}

        def fake_gh(exe, args, timeout):
            seen.update(exe=exe, argv=args, timeout=timeout)
            return subprocess.CompletedProcess(["gh", *args], 0, payload, "")

        with unittest.mock.patch.object(rbb_core, "_gh", side_effect=fake_gh):
            rows, err = rbb_core._scan_one("video clip", "gh", 50, "2025-01-01", 6, 30)
        self.assertEqual(err, "")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["full_name"], "owner/autoclip")
        argv = seen["argv"]
        self.assertIn("--", argv)
        self.assertTrue(argv[-1].startswith("video clip "))
        self.assertLess(argv.index("--archived=false"), argv.index("--"))


class TestGhHardening(unittest.TestCase):
    """第二轮复查的回归：异常不漏、gh 不可换程序、stderr 脱敏、坏 JSON 不崩。"""

    def setUp(self):
        patcher = unittest.mock.patch.object(rbb_core, "which_path_only", return_value="C:/Tools/gh.exe")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_scan_timeout_is_a_total_budget(self):
        """scan_timeout 是整次检索的总预算：第二个关键词拿到的是剩余时间，不是又一次 30s。"""
        seen = []

        def fake_scan(kw, exe, stars, since, limit, timeout):
            seen.append(timeout)
            time.sleep(0.05)
            return [], "boom"

        with unittest.mock.patch.object(rbb_core, "resolve_gh_path", return_value="gh"):
            with unittest.mock.patch.object(rbb_core, "auth_ok", return_value=(True, "")):
                with unittest.mock.patch.object(rbb_core, "_scan_one", side_effect=fake_scan):
                    res = rbb_core.search_candidates(["a", "b"], timeout=5)
        self.assertEqual(res["code"], 3)
        self.assertEqual(len(seen), 2)
        self.assertLess(seen[1], 5.0, f"第二个关键词没有共享总预算：{seen}")

    def test_budget_skips_are_not_reported_as_search_failures(self):
        """预算耗尽的 skip 不是「gh 坏了」：失败与跳过必须分开，报 code=4。"""
        clock = [0.0]

        def fake_clock():
            return clock[0]

        def fake_scan(kw, exe, stars, since, limit, timeout):
            clock[0] += 4.9
            return [], "boom"

        with unittest.mock.patch.object(rbb_core, "resolve_gh_path", return_value="gh"):
            with unittest.mock.patch.object(rbb_core, "auth_ok", return_value=(True, "")):
                with unittest.mock.patch.object(rbb_core, "_scan_one", side_effect=fake_scan):
                    res = rbb_core.search_candidates(
                        ["a", "b", "c"], timeout=5, clock=fake_clock
                    )
        self.assertEqual(res["code"], 4, res)
        self.assertEqual(res["failed"], ["a"])
        self.assertEqual(res["skipped"], ["b", "c"])
        self.assertIn("预算", res["reason"])
        self.assertNotIn("全部关键词检索失败", res["reason"])
        self.assertNotIn(rbb_core.EMPTY_HINT, res["note"])

    def test_non_gh_executable_is_rejected(self):
        self.assertEqual(rbb_core.resolve_gh_executable("evil-payload.exe"), "")
        self.assertEqual(rbb_core.resolve_gh_executable("C:/tmp/evil.exe"), "")
        self.assertEqual(rbb_core.resolve_gh_executable("C:/tmp/gh.exe"), "")
        self.assertEqual(rbb_core.resolve_gh_executable("C:/tmp/gh.bat"), "")
        self.assertEqual(rbb_core.resolve_gh_executable("gh.cmd"), "")
        self.assertEqual(rbb_core.resolve_gh_executable("gh"), "gh")

    def test_config_gh_binary_cannot_point_to_arbitrary_program(self):
        got = rbb_core.resolve_settings({"gh_binary": "C:/tmp/evil.exe"})
        self.assertEqual(got["gh_binary"], "gh")
        self.assertIn("gh_binary", got["settings_warning"])

    def test_permission_error_becomes_runtime_error(self):
        with unittest.mock.patch.object(rbb_core, "_gh", side_effect=PermissionError("denied")):
            rows, err = rbb_core._scan_one("k", "gh", 50, "2025-01-01", 6, 5)
        self.assertEqual(rows, [])
        self.assertIn("gh 启动失败", err)

    def test_gh_stderr_is_redacted(self):
        exc = subprocess.CompletedProcess(
            ["gh"], 1, "", "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 denied"
        )
        with unittest.mock.patch.object(rbb_core, "_gh", return_value=exc):
            with self.assertRaises(RuntimeError) as ctx:
                rbb_core.gh_json("gh", ["search"], 5)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", str(ctx.exception))
        self.assertIn("[redacted]", str(ctx.exception))

    def test_non_dict_json_rows_are_dropped(self):
        rows = rbb_core.parse_search_output('[1, "x", {"url": "u"}]')
        self.assertEqual(rows, [{"url": "u"}])

    def test_bad_json_stdout_is_redacted(self):
        with self.assertRaises(RuntimeError) as ctx:
            rbb_core.parse_search_output('not-json api_key=abc123')
        self.assertNotIn("abc123", str(ctx.exception))
        self.assertIn("[redacted]", str(ctx.exception))

    def test_naive_pushed_at_does_not_crash(self):
        self.assertEqual(rbb_core.activity_points("2026-10-01T00:00:00"), 0)

    def test_non_string_pushed_at_does_not_crash(self):
        self.assertEqual(rbb_core.activity_points(123), 0)
        self.assertEqual(rbb_core.activity_points(["2026-10-01"]), 0)

    def test_auth_check_gets_partial_budget(self):
        seen = []

        def fake_auth(exe, timeout):
            seen.append(timeout)
            return True, ""

        with unittest.mock.patch.object(rbb_core, "resolve_gh_path", return_value="gh"):
            with unittest.mock.patch.object(rbb_core, "auth_ok", side_effect=fake_auth):
                with unittest.mock.patch.object(rbb_core, "_scan_one", return_value=([], "boom")):
                    rbb_core.search_candidates(["a"], timeout=30)
        self.assertEqual(seen, [10.0])

    def test_auth_failure_is_code2_not_code3(self):
        with unittest.mock.patch.object(rbb_core, "resolve_gh_path", return_value="gh"):
            with unittest.mock.patch.object(rbb_core, "auth_ok", return_value=(False, "超时")):
                res = rbb_core.search_candidates(["a"], timeout=5)
        self.assertEqual(res["code"], 2)
        self.assertIn("gh 未就绪", res["reason"])

    def test_direct_params_are_clamped(self):
        seen = {}

        def fake_scan(kw, exe, stars, since, limit, timeout):
            seen.update(stars=stars, since=since, limit=limit)
            return [], ""

        with unittest.mock.patch.object(rbb_core, "resolve_gh_path", return_value="gh"):
            with unittest.mock.patch.object(rbb_core, "auth_ok", return_value=(True, "")):
                with unittest.mock.patch.object(rbb_core, "_scan_one", side_effect=fake_scan):
                    res = rbb_core.search_candidates(
                        ["a"], stars=-5, limit=999, updated_since="not-a-date", timeout=30
                    )
        self.assertEqual(seen["stars"], 0)
        self.assertEqual(seen["limit"], 10)
        self.assertEqual(seen["since"], rbb_core.default_since(365))
        self.assertIn("updated_since", res["note"])

    def test_basic_compact_date_is_not_silently_accepted(self):
        """`20250101` 在部分 Python 版本能被 fromisoformat 吃掉，但不是约定格式。"""
        _, _, since, note = rbb_core.normalize_scan_params(50, 6, "20250101", 365)
        self.assertEqual(since, rbb_core.default_since(365))
        self.assertIn("YYYY-MM-DD", note)

    def test_api_key_and_bearer_are_redacted(self):
        msg = rbb_core.redact_text("api_key=abc123 Bearer sk-abcdefghij")
        self.assertNotIn("abc123", msg)
        self.assertNotIn("sk-abcdefghij", msg)
        self.assertIn("[redacted]", msg)

    def test_python_resolver_falls_back_to_py_launcher(self):
        with unittest.mock.patch.object(rbb_core.sys, "executable", ""):
            with unittest.mock.patch.object(
                rbb_core, "which_path_only",
                side_effect=lambda name: "C:/Windows/py.exe" if name == "py" else None,
            ):
                self.assertEqual(rbb_core.resolve_python(), "C:/Windows/py.exe")

    def test_embedded_host_executable_is_not_used_as_python(self):
        """嵌入式宿主的 sys.executable 是宿主进程，不能再起一份它当解释器。"""
        with unittest.mock.patch.object(
            rbb_core.sys, "executable", r"C:/Apps/N.E.K.O/neko-host.exe"
        ):
            with unittest.mock.patch.object(
                rbb_core, "which_path_only",
                side_effect=lambda name: "C:/Python312/python.exe" if name == "python" else None,
            ):
                self.assertEqual(rbb_core.resolve_python(), "C:/Python312/python.exe")


    GOOD = """## 找到了什么
| 仓库 | ★ | 最近推送 |
|---|---|---|
| owner/autoclip | 4200 | 2026-10-01 |

## 为什么选它
活跃、MIT、缺字幕挑高光。

## 我要加什么
基于字幕的高光片段挑选。
可改性：绿（内核清晰且有测试）。

## 备选与风险
备选：fork 自维护。风险：维护者响应慢。

结论：Join。
"""

    def test_good_deliverable_passes(self):
        res = rbb_core.check_deliverable(self.GOOD)
        self.assertTrue(res["ok"], res["missing"])

    def test_missing_sections_fail(self):
        res = rbb_core.check_deliverable("## 找到了什么\n随便写写。")
        self.assertFalse(res["ok"])
        self.assertIn("section:why", res["missing"])

    def test_build_requires_gate_line(self):
        text = self.GOOD.replace("结论：Join。", "结论：Build（新建）。")
        res = rbb_core.check_deliverable(text)
        self.assertFalse(res["ok"])
        self.assertIn("gate_line", res["missing"])

    def test_build_with_gate_line_passes(self):
        text = "不可复用原因 / Not reusable because: 许可为 GPL-3.0，与用户闭源要求冲突。\n\n"
        text += self.GOOD.replace("结论：Join。", "结论：Build。")
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])

    def test_join_with_build_in_alternatives_does_not_require_gate(self):
        """回归：结论 Join、备选段提到 Build 时，不得再误报 gate_line。"""
        text = self.GOOD.replace(
            "备选：fork 自维护。风险：维护者响应慢。",
            "备选：fork 自维护；实在不成立才 Build，那时再补理由。风险：维护者响应慢。",
        )
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])
        self.assertNotIn("gate_line", res["missing"])

    def test_ambiguous_verdict_without_conclusion_line_does_not_force_gate(self):
        """没有结论行、结论词又不唯一时，只提示补结论行，不硬要门禁行。"""
        text = self.GOOD.replace("结论：Join。", "").replace(
            "备选：fork 自维护。", "备选：fork 自维护，或者 Build 自建。"
        )
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])
        self.assertTrue(any("结论不唯一" in note for note in res["notes"]), res["notes"])

    def test_rebuild_building_do_not_trigger_build_gate(self):
        """回归：rebuild / building 里的 build 不是结论词，不能误触发 Build 门禁。"""
        text = self.GOOD.replace(
            "备选：fork 自维护。风险：维护者响应慢。",
            "备选：在现有仓库里 rebuild 或继续 building 新模块。风险：维护者响应慢。",
        )
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])
        self.assertNotIn("gate_line", res["missing"])

    def test_single_build_verdict_without_conclusion_line_requires_gate(self):
        """没有结论行但全文只有 Build 这一个结论词时，门禁仍然生效。"""
        text = self.GOOD.replace("结论：Join。", "").replace("备选：fork 自维护。", "备选：Build 自建。")
        res = rbb_core.check_deliverable(text)
        self.assertFalse(res["ok"])
        self.assertIn("gate_line", res["missing"])

    def test_chinese_body_words_do_not_count_as_a_verdict(self):
        """正文出现「新建文件」「加入」不是结论；没有结论行就该报 missing，不得误触发门禁。"""
        text = self.GOOD.replace("结论：Join。", "").replace(
            "备选：fork 自维护。", "备选：维护者失联时再看。"
        ).replace(
            "基于字幕的高光片段挑选。", "这里会新建文件，并把补丁加入上游。"
        )
        res = rbb_core.check_deliverable(text)
        self.assertIn("verdict", res["missing"])
        self.assertNotIn("gate_line", res["missing"])

    def test_chinese_conclusion_join_is_not_shadowed_by_body_words(self):
        text = self.GOOD.replace(
            "基于字幕的高光片段挑选。", "这里会新建文件、加入上游，但最终结论仍是复用。"
        )
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])
        self.assertNotIn("gate_line", res["missing"])

    def test_markdown_decorated_gate_and_build_conclusion_pass(self):
        text = "**不可复用原因：** 许可为 GPL-3.0，与闭源要求冲突。\n\n"
        text += self.GOOD.replace("结论：Join。", "> 结论：**Build**。")
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])

    def test_last_conclusion_wins_over_earlier_alternative(self):
        """备选段先写「结论：Build」时，不能被它盖掉最后的 Join。"""
        text = self.GOOD.replace(
            "结论：Join。",
            "备选方案先记：结论：Build，理由待补。\n\n结论：Join。",
        )
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])
        self.assertNotIn("gate_line", res["missing"])

    def test_bold_headings_and_numbered_headings_are_accepted(self):
        text = self.GOOD
        for old, new in (
            ("## 找到了什么", "**找到了什么**"),
            ("## 为什么选它", "### 1. 为什么选它"),
            ("## 我要加什么", "**我要加什么：**"),
            ("## 备选与风险", "> ### 备选与风险"),
        ):
            text = text.replace(old, new)
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])

    def test_section_names_inside_body_do_not_count(self):
        text = self.GOOD.replace("## 为什么选它", "为什么选它不是一句话能说清的")
        res = rbb_core.check_deliverable(text)
        self.assertFalse(res["ok"])
        self.assertIn("section:why", res["missing"])


class TestTable(unittest.TestCase):
    def test_md_cell_escapes_pipe_and_newline(self):
        self.assertEqual(rbb_core.md_cell("a|b\nc"), "a\\|b c")
        self.assertEqual(rbb_core.md_cell(None), "-")

    def test_render_table_row_cells(self):
        rows = [{"full_name": "o/r", "stargazersCount": 1, "pushedAt": "2026-10-01T00:00:00Z",
                 "language": "Python", "_lic_label": "mit", "openIssuesCount": 2,
                 "matched_keyword": ["kw"], "url": "https://github.com/o/r", "_activity": 2}]
        out = rbb_core.render_table(rows)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(re.findall(r"(?<!\\)\|", lines[2])), 9)


class TestVerifyPathJail(unittest.TestCase):
    """rbb_verify 的 path 不能变成任意文件读取器：必须在插件目录内且是 .md。"""

    def test_bundled_skill_is_allowed(self):
        real, err = rbb_core.resolve_localized_path(SKILL_MD)
        self.assertEqual(err, "")
        self.assertEqual(real, os.path.realpath(SKILL_MD))

    def test_relative_path_resolves_against_plugin_root(self):
        real, err = rbb_core.resolve_localized_path("skills/reuse-before-build/SKILL.md")
        self.assertEqual(err, "")
        self.assertEqual(real, os.path.realpath(SKILL_MD))

    def test_outside_plugin_dir_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "copy.md")
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write("x")
            res = rbb_core.verify_localized(dst)
            self.assertEqual(res["rc"], 2)
            self.assertIn("不在插件工作目录内", res["err"])

    def test_non_markdown_is_rejected(self):
        dst = os.path.join(ROOT, "plugin.toml")
        real, err = rbb_core.resolve_localized_path(dst)
        self.assertEqual(real, "")
        self.assertIn(".md", err)

    def test_parent_traversal_is_rejected(self):
        real, err = rbb_core.resolve_localized_path("../README.md")
        self.assertEqual(real, "")
        self.assertIn("不在插件工作目录内", err)

    def test_extra_flags_cannot_enable_canonical_bypass(self):
        """`--canonical` 不给模型侧注入：即使传进来也只会走完整声明校验。"""
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            dst = os.path.join(tmp, "localized.md")
            with open(SKILL_MD, encoding="utf-8") as fh:
                text = fh.read()
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text.rstrip() + "\n\nlocalized: host=test | kept=all | changed=none | unsupported=none\n")
            res = rbb_core.verify_localized(dst, extra=["--canonical"])
        self.assertEqual(res["rc"], 0, res["err"] or res["out"])


class TestSettings(unittest.TestCase):
    """配置解析是纯函数，宿主读不到配置时也必须给出可运行的一组值。"""

    def test_manifest_defaults_when_section_missing(self):
        got = rbb_core.resolve_settings(None)
        self.assertTrue(got["enable_github_scan"])
        self.assertEqual(got["gh_binary"], "gh")
        self.assertEqual(got["default_limit"], 6)
        self.assertEqual(got["scan_timeout"], 30)

    def test_runtime_section_wins_over_defaults(self):
        got = rbb_core.resolve_settings({"default_stars": 120, "enable_github_scan": False})
        self.assertEqual(got["default_stars"], 120)
        self.assertFalse(got["enable_github_scan"])

    def test_bad_values_are_clamped_not_raised(self):
        got = rbb_core.resolve_settings(
            {"default_limit": 0, "updated_days": "bad", "scan_timeout": 9999, "gh_binary": "   "})
        self.assertEqual(got["default_limit"], 1)
        self.assertEqual(got["updated_days"], rbb_core.SETTINGS_DEFAULTS["updated_days"])
        self.assertEqual(got["scan_timeout"], 300)
        self.assertEqual(got["gh_binary"], "gh")

    def test_unknown_keys_are_ignored(self):
        got = rbb_core.resolve_settings({"something_else": 1})
        self.assertNotIn("something_else", got)

    def test_string_false_is_not_truthy(self):
        """bool("false") 是 True；配置解析必须显式认字符串。"""
        got = rbb_core.resolve_settings({"enable_github_scan": "false"})
        self.assertFalse(got["enable_github_scan"])
        self.assertTrue(rbb_core.resolve_settings({"enable_github_scan": "yes"})["enable_github_scan"])
        self.assertFalse(rbb_core.resolve_settings({"enable_github_scan": 0})["enable_github_scan"])

    def test_missing_manifest_reports_warning_and_emergency_source(self):
        got = rbb_core.resolve_settings(None, plugin_toml=os.path.join(ROOT, "no-such-plugin.toml"))
        self.assertEqual(got["settings_source"], "emergency-defaults")
        self.assertIn("plugin.toml", got["settings_warning"])
        self.assertFalse(got["enable_github_scan"], "兜底必须关掉检索")
        self.assertEqual(got["default_stars"], 0)
        self.assertEqual(got["default_limit"], rbb_core.SETTINGS_DEFAULTS["default_limit"])

    def test_bundled_manifest_is_the_default_source(self):
        got = rbb_core.resolve_settings(None)
        self.assertEqual(got["settings_source"], "plugin.toml")
        self.assertEqual(got["settings_warning"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
