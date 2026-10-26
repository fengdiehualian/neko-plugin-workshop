import tomllib
from pathlib import Path

from plugin.neko_plugin_cli.core.build_rules import load_build_rules, should_skip_path


def test_public_ca_only_pem_exception():
    root = Path(__file__).resolve().parents[1]
    rules = load_build_rules(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")))
    for name in (".git", "credentials.pem", "CREDENTIALS.PEM", "nested/Custom.PeM", "nested/custom.pem", "vendor/unexpected.pem", "vendor/certifi/private.pem", ".superpowers/a.txt", "local/a.txt", ".env.private", "credentials.key", "__init__.py.bak", "ui/panel.tsx.orig", "a.py.rej"):
        assert should_skip_path(Path(name), is_dir=False, rules=rules), name
    for name in ("vendor/certifi/cacert.pem", "dashboard.py", "ui/panel.tsx", "vendor/certifi/core.py", "LICENSE", "assets/a.png"):
        assert not should_skip_path(Path(name), is_dir=False, rules=rules), name


def test_pem_complement_is_case_sensitive_platform_independent():
    from fnmatch import fnmatchcase

    root = Path(__file__).resolve().parents[1]
    patterns = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["neko"]["build"]["include"]
    for name in ("secret.pem", "nested/secret.PEM", "vendor/secret.pEm", "vendor/certifi/private.PeM"):
        assert not any(fnmatchcase(name, pattern) for pattern in patterns), name
    assert any(fnmatchcase("vendor/certifi/cacert.pem", pattern) for pattern in patterns)
