"""Config keys must live in the section the plugin actually reads them from.

A key placed under the wrong TOML table parses fine, is never looked up, and
silently reports a value the runtime ignores -- which is exactly how 16 tuning
keys ended up inert under ``[host]`` while ``_num``/``_int``/``_text`` only ever
read ``[search]``. These tests derive the read-side inventory straight from the
source instead of restating it, so the two cannot drift apart again.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("search", "net", "ui")

# ``_num``/``_int``/``_text`` are the [search]-bound helpers (frozen from v0.1).
SEARCH_ONLY = re.compile(r"self\._(?P<get>num|int|text)\(\s*\"(?P<key>[a-z0-9_]+)\"")
# A few list/dict shaped keys are read straight off the [search] view.
SEARCH_DIRECT = re.compile(r"self\._cfg\.get\(\s*\"(?P<key>[a-z0-9_]+)\"")
# The section-aware helpers take the table name as their first argument.
SCOPED = re.compile(
    r"self\._(?P<get>raw|text_in|flag_in|list_in)\(\s*\"(?P<section>search|net|ui)\""
    r"\s*,\s*\"(?P<key>[a-z0-9_]+)\""
)
# Per-backend overrides are read through f-strings, e.g. f"{route}_proxy".
DYNAMIC_PATTERNS = (("_proxy", 'f"{route}_proxy"'), ("_method", 'f"{name}_method"'))
# The code-side default chain, as written in __init__.py.
DEFAULT_CHAIN_RE = re.compile(r"^DEFAULT_CHAIN = \(([^)]*)\)", re.MULTILINE)


def _source() -> str:
    return (ROOT / "__init__.py").read_text(encoding="utf-8")


def _toml(name: str) -> dict:
    with (ROOT / name).open("rb") as stream:
        return tomllib.load(stream)


def _read_keys() -> dict[str, set[str]]:
    source = _source()
    keys: dict[str, set[str]] = {section: set() for section in SECTIONS}
    for match in SEARCH_ONLY.finditer(source):
        keys["search"].add(match.group("key"))
    for match in SEARCH_DIRECT.finditer(source):
        keys["search"].add(match.group("key"))
    for match in SCOPED.finditer(source):
        keys[match.group("section")].add(match.group("key"))
    return keys


def _is_dynamic(key: str) -> bool:
    return any(
        key.endswith(suffix) and template in _source()
        for suffix, template in DYNAMIC_PATTERNS
    )


def test_every_key_read_by_the_plugin_is_declared_in_the_manifest() -> None:
    manifest = _toml("plugin.toml")
    missing = [
        f"[{section}] {key}"
        for section, names in _read_keys().items()
        for key in sorted(names)
        if key not in (manifest.get(section) or {}) and not _is_dynamic(key)
    ]
    assert not missing, f"read but undeclared in plugin.toml: {missing}"


def test_every_declared_key_is_actually_read_from_that_section() -> None:
    read = _read_keys()
    inert = []
    for name in ("plugin.toml", "config.example.toml"):
        document = _toml(name)
        for section in SECTIONS:
            for key in sorted((document.get(section) or {})):
                if key in read[section] or _is_dynamic(key):
                    continue
                inert.append(f"{name}: [{section}] {key}")
    assert not inert, f"declared but never read from that section (inert default): {inert}"


def test_manifest_and_example_agree_on_the_same_key_set() -> None:
    manifest, example = _toml("plugin.toml"), _toml("config.example.toml")
    for section in SECTIONS:
        left, right = set(manifest.get(section) or {}), set(example.get(section) or {})
        assert left == right, (
            f"[{section}] keys differ: only in plugin.toml={sorted(left - right)} "
            f"only in config.example.toml={sorted(right - left)}"
        )


def test_shipped_backend_chain_is_the_code_default() -> None:
    """Both manifests must ship the same order ``DEFAULT_CHAIN`` uses.

    Which engine answers first is decided by that order, so a stale list in one
    file gives a fresh install a different head than an empty config does.
    """
    declared = [item.strip().strip('"')
                for item in DEFAULT_CHAIN_RE.search(_source()).group(1).split(",")
                if item.strip()]
    for name in ("plugin.toml", "config.example.toml"):
        shipped = list(_toml(name)["search"]["backend_chain"])
        assert shipped == declared, f"{name} ships {shipped}, code default is {declared}"
