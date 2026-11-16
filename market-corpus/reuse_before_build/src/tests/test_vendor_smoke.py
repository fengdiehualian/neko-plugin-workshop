"""只使用解压后的 vendor 文件做独立运行验证。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vendor_release", ROOT / "scripts" / "vendor_release.py")
vendor_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vendor_release)


class TestExtractedVendor(unittest.TestCase):
    def test_extracted_vendor_runs_without_source_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            version = vendor_release.plugin_version(ROOT)
            archive = vendor_release.build_vendor_archive(Path(tmp), f"v{version}", ROOT)
            extracted = Path(tmp) / "extracted"
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)
            vendor_root = extracted / f"neko-reuse-before-build-v{version}"

            core_spec = importlib.util.spec_from_file_location("extracted_rbb_core", vendor_root / "rbb_core.py")
            core = importlib.util.module_from_spec(core_spec)
            core_spec.loader.exec_module(core)
            self.assertNotEqual(Path(core.__file__).resolve(), (ROOT / "rbb_core.py").resolve())
            redacted = core.redact_text("https://user:password@example.com/repo Cookie: session=secret")
            self.assertNotIn("password", redacted)
            self.assertNotIn("secret", redacted)
            self.assertTrue(core.core_rules_block())
            rule_hash, error = core.canonical_hash()
            self.assertEqual(error, "")
            self.assertEqual(len(rule_hash), 64)

            repo_scan = vendor_root / "skills" / "reuse-before-build" / "scripts" / "repo_scan.py"
            help_run = subprocess.run(
                [sys.executable, str(repo_scan), "--help"],
                cwd=tmp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(help_run.returncode, 0, help_run.stdout + help_run.stderr)
            self.assertIn("--updated-since", help_run.stdout)

            verifier = vendor_root / "skills" / "reuse-before-build" / "scripts" / "verify_localized.py"
            verify_run = subprocess.run(
                [sys.executable, str(verifier), str(vendor_root / "skills" / "reuse-before-build" / "SKILL.md")],
                cwd=tmp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(verify_run.returncode, 0, verify_run.stdout + verify_run.stderr)
            self.assertIn("verbatim", verify_run.stdout)


if __name__ == "__main__":
    unittest.main()
