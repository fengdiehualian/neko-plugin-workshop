"""vendor 发布形态与接入文档的离线契约测试。"""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vendor_release", ROOT / "scripts" / "vendor_release.py")
vendor_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vendor_release)


class TestVendorArchive(unittest.TestCase):
    def setUp(self):
        self.version = vendor_release.plugin_version(ROOT)
        self.prefix = f"neko-reuse-before-build-v{self.version}"

    def build(self, directory: Path) -> Path:
        return vendor_release.build_vendor_archive(directory, f"v{self.version}", ROOT)

    def test_repeated_builds_are_byte_identical_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.build(Path(tmp) / "first")
            second = self.build(Path(tmp) / "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            result = vendor_release.verify_vendor_archive(first)
            self.assertEqual(result["version"], self.version)
            self.assertEqual(result["archive"], first.name)
            self.assertGreater(result["files"], 15)

    def test_tag_must_match_plugin_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "完全一致"):
                vendor_release.build_vendor_archive(Path(tmp), "v0.0.1", ROOT)

    def test_archive_contains_runtime_docs_and_shared_redaction_without_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self.build(Path(tmp))
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
            for required in (
                "plugin.toml",
                "rbb_core.py",
                "docs/vendor.md",
                "skills/reuse-before-build/scripts/redaction.py",
                "skills/reuse-before-build/scripts/gh_helpers.py",
                "skills/reuse-before-build/scripts/repo_scan.py",
                "VENDOR-SHA256SUMS.txt",
            ):
                self.assertIn(f"{self.prefix}/{required}", names)
            self.assertFalse(any("/tests/" in name or "/.github/" in name for name in names))
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

    def test_tampered_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.build(Path(tmp))
            tampered = Path(tmp) / "tampered.zip"
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as changed:
                for info in original.infolist():
                    payload = original.read(info.filename)
                    if info.filename == f"{self.prefix}/rbb_core.py":
                        payload += b"# unauthorized drift\n"
                    changed.writestr(info, payload)
            with self.assertRaisesRegex(ValueError, "文件哈希不匹配"):
                vendor_release.verify_vendor_archive(tampered)

    def test_unregistered_extra_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.build(Path(tmp))
            with zipfile.ZipFile(source, "a") as bundle:
                bundle.writestr(f"{self.prefix}/unregistered.py", "pass\n")
            with self.assertRaisesRegex(ValueError, "文件集合与清单不符"):
                vendor_release.verify_vendor_archive(source)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape", "no\n")
            with self.assertRaisesRegex(ValueError, "非法清单路径"):
                vendor_release.verify_vendor_archive(archive)

    def test_copied_tree_detects_drift_and_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self.build(Path(tmp))
            extracted = Path(tmp) / "extracted"
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)
            root = extracted / self.prefix
            self.assertGreater(vendor_release.verify_vendor_tree(root), 15)
            target = root / "rbb_core.py"
            original = target.read_bytes()
            target.write_bytes(original + b"# drift\n")
            with self.assertRaisesRegex(ValueError, "文件哈希不匹配"):
                vendor_release.verify_vendor_tree(root)
            target.write_bytes(original)
            (root / "skills" / "reuse-before-build" / "scripts" / "redaction.py").unlink()
            with self.assertRaisesRegex(ValueError, "文件缺失"):
                vendor_release.verify_vendor_tree(root)

    def test_release_sums_cover_assets_and_detect_zip_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            archive = self.build(assets)
            (assets / "reuse_before_build.neko-plugin").write_bytes(b"plugin fixture")
            (assets / "market-evidence.json").write_text("{}\n", encoding="utf-8")
            (assets / "vendor_release.py").write_bytes((ROOT / "scripts" / "vendor_release.py").read_bytes())
            sums = assets / "SHA256SUMS.txt"
            names = vendor_release.write_release_sums(assets, sums)
            self.assertEqual(
                names,
                sorted([archive.name, "reuse_before_build.neko-plugin", "market-evidence.json", "vendor_release.py"]),
            )
            vendor_release.verify_vendor_archive(archive, sums)
            with archive.open("ab") as fh:
                fh.write(b"drift")
            with self.assertRaisesRegex(ValueError, "Release ZIP 哈希不匹配"):
                vendor_release.verify_vendor_archive(archive, sums)


class TestVendorDocsAndWorkflow(unittest.TestCase):
    def test_vendor_workflow_follows_official_release_without_editing_managed_caller(self):
        standard = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        text = (ROOT / ".github" / "workflows" / "vendor-release.yml").read_text(encoding="utf-8")
        self.assertIn("Project-N-E-K-O/N.E.K.O/.github/workflows/plugin-market-release.yml@main", standard)
        self.assertNotIn("vendor:", standard)
        self.assertIn('workflows: ["Release N.E.K.O Plugin"]', text)
        self.assertIn("types: [completed]", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("github.event.workflow_run.event == 'push'", text)
        self.assertIn("ref: ${{ github.event.workflow_run.head_sha }}", text)
        for required in ("vendor_release.py build", "verify-archive", "SHA256SUMS.txt", "gh release upload"):
            self.assertIn(required, text)
        offline = (ROOT / ".github" / "workflows" / "offline-tests.yml").read_text(encoding="utf-8")
        self.assertIn("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2", offline)
        self.assertIn("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065  # v5.6.0", offline)
        verify = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
        self.assertIn("Project-N-E-K-O/N.E.K.O/.github/workflows/plugin-market-verify.yml@main", verify)

    def test_vendor_guide_pins_neko_repository_version_and_target(self):
        text = (ROOT / "docs" / "vendor.md").read_text(encoding="utf-8")
        self.assertIn("1-dcjiang/neko-reuse-before-build", text)
        self.assertIn(f"v{vendor_release.plugin_version(ROOT)}", text)
        self.assertIn("plugin/plugins/reuse_before_build", text)
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn("VENDOR-SHA256SUMS.txt", text)
        self.assertIn("不等于签名", text)
        versions = set(re.findall(r"(?<!\d)\d+\.\d+\.\d+(?!\d)", text))
        self.assertEqual(versions, {vendor_release.plugin_version(ROOT)})


if __name__ == "__main__":
    unittest.main()
