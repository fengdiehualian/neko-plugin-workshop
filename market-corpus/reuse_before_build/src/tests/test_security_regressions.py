"""v0.3.8 安全与包装回归；全部离线运行。"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import tomllib
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rbb_core = load_module("rbb_core", os.path.join(ROOT, "rbb_core.py"))
repo_scan = load_module(
    "rbb_repo_scan",
    os.path.join(ROOT, "skills", "reuse-before-build", "scripts", "repo_scan.py"),
)


class TestSharedRedaction(unittest.TestCase):
    def test_core_and_cli_redact_the_same_credentials(self):
        cases = {
            "https://alice:url-password@example.com/owner/repo.git": ("alice", "url-password"),
            "Cookie: session= cookie-secret\nnext line": ("cookie-secret",),
            "Authorization: Bearer header-secret-123": ("header-secret-123",),
            "access_token= token-secret-123": ("token-secret-123",),
            '{"access_token":"json-secret-123"}': ("json-secret-123",),
        }
        for text, secrets in cases.items():
            with self.subTest(text=text):
                core = rbb_core.redact_text(text)
                cli = repo_scan.redact_text(text)
                self.assertEqual(core, cli)
                self.assertIn("[redacted]", core)
                for secret in secrets:
                    self.assertNotIn(secret, core)

    def test_home_path_variants_are_redacted(self):
        home = os.path.expanduser("~")
        text = f"open {home}/secret.txt and {home.replace(os.sep, '/')}/other.txt"
        redacted = rbb_core.redact_text(text)
        self.assertIn("~", redacted)
        self.assertNotIn(home, redacted)
        if os.name == "nt":
            windows_case = home.upper().replace("\\", "/")
            result = rbb_core.redact_text(f"{windows_case}/third.txt")
            self.assertIn("~", result)
            self.assertNotIn(windows_case, result)

    def test_root_home_does_not_redact_path_separators(self):
        with mock.patch("os.path.expanduser", return_value="/"):
            self.assertEqual(rbb_core.redact_text("/api/v1/repo"), "/api/v1/repo")

    def test_drive_root_home_does_not_redact_every_drive_reference(self):
        for home in ("C:", "C:\\", "C:/", "c:\\\\"):
            with self.subTest(home=home), mock.patch("os.path.expanduser", return_value=home):
                text = f"build C:/one and C:\\two; home={home}"
                self.assertEqual(rbb_core.redact_text(text), text)
                self.assertEqual(repo_scan.redact_text(text), text)

    def test_only_the_shared_module_owns_redaction_regexes(self):
        for module in (rbb_core, repo_scan):
            for name in ("_SECRET_RX", "_URL_CRED_RX", "_HEADER_RX"):
                self.assertFalse(hasattr(module, name), f"{module.__name__} 又维护了 {name}")
        self.assertEqual(
            os.path.realpath(rbb_core.REDACTION_SCRIPT),
            os.path.realpath(os.path.join(os.path.dirname(repo_scan.__file__), "redaction.py")),
        )


class TestSharedGhHelpers(unittest.TestCase):
    def test_core_and_cli_expose_the_same_shared_helper_functions(self):
        for module in (rbb_core, repo_scan):
            for name in (
                "which_path_only", "validate_keyword", "parse_search_output", "search_args",
                "normalize_search_row", "unique_keywords", "default_since", "activity_points",
                "license_flags", "md_cell", "render_table", "GH_CREATIONFLAGS", "PERMISSIVE",
                "AUTH_HINT", "FAIL_HINT",
            ):
                with self.subTest(module=module.__name__, helper=name):
                    self.assertIs(getattr(module, name), getattr(module._gh_helpers, name))
        self.assertEqual(
            os.path.realpath(rbb_core.GH_HELPERS_SCRIPT),
            os.path.realpath(os.path.join(os.path.dirname(repo_scan.__file__), "gh_helpers.py")),
        )

    def test_keyword_and_json_contracts_hold_at_both_entry_points(self):
        for module in (rbb_core, repo_scan):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.validate_keyword("  clip tool  "), "clip tool")
                with self.assertRaisesRegex(ValueError, "检索限定符"):
                    module.validate_keyword("clip stars:>10")
                self.assertEqual(module.parse_search_output('[1, {"url": "u"}]'), [{"url": "u"}])
                argv = module.search_args("-clip", 0, "2026-01-01", 2)
                self.assertEqual(argv[-2], "--")
                self.assertTrue(argv[-1].startswith("-clip stars:>=0"))

    def test_keyword_dedupe_is_case_insensitive(self):
        self.assertEqual(repo_scan.unique_keywords(["Kw", "kw", "other"]), ["Kw", "other"])
        self.assertEqual(rbb_core.normalize_keywords(("Kw", "kw", "other")), (["Kw", "other"], ""))


class TestGhExecutableResolution(unittest.TestCase):
    def test_missing_path_result_never_falls_back_to_bare_gh(self):
        with mock.patch.object(rbb_core, "which_path_only", return_value=""):
            self.assertEqual(rbb_core.command_path("gh"), "")
            self.assertEqual(rbb_core.command_path("gh.exe"), "")
            with mock.patch.object(rbb_core, "_gh") as gh:
                with self.assertRaisesRegex(RuntimeError, "gh_binary"):
                    rbb_core.gh_json("gh", ["search", "repos"], 5)
                gh.assert_not_called()
            with mock.patch.object(rbb_core, "_gh") as gh:
                ok, _ = rbb_core.auth_ok("gh", 5)
                self.assertFalse(ok)
                gh.assert_not_called()

    def test_resolved_absolute_path_still_reaches_gh(self):
        payload = subprocess.CompletedProcess(["gh"], 0, "[]", "")
        with mock.patch.object(rbb_core, "which_path_only", return_value="C:/Tools/gh.exe"):
            with mock.patch.object(rbb_core, "_gh", return_value=payload) as gh:
                self.assertEqual(rbb_core.gh_json("gh", ["search", "repos"], 5), [])
                gh.assert_called_once_with("C:/Tools/gh.exe", ["search", "repos"], 5)


class TestSingleVersions(unittest.TestCase):
    def test_localized_verifier_has_one_version_assignment(self):
        path = os.path.join(ROOT, "skills", "reuse-before-build", "scripts", "verify_localized.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        versions = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
            and isinstance(node.value, ast.Constant)
        ]
        self.assertEqual(versions, ["1.2.15"])

    def test_repo_scan_patch_version_tracks_behavior_change(self):
        self.assertEqual(repo_scan.__version__, "1.2.9")

    def test_plugin_and_project_versions_match(self):
        with open(os.path.join(ROOT, "plugin.toml"), "rb") as fh:
            plugin = tomllib.load(fh)
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            project = tomllib.load(fh)
        self.assertEqual(plugin["plugin"]["version"], project["project"]["version"])


if __name__ == "__main__":
    unittest.main()
