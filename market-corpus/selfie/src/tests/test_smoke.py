"""Packaged discovery must not expose a duplicate agent-routed selfie entry."""
import shutil
import tomllib
from pathlib import Path

from plugin.neko_plugin_cli.core.build_rules import load_build_rules, should_skip_path, walk_plugin_tree
from plugin.server.infrastructure.packaged_metadata import read_packaged_metadata


def test_packaged_metadata_matches_the_source_tree(tmp_path):
    root = Path(__file__).resolve().parents[1]
    rules = load_build_rules(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")))
    # Workflow/IDE files and private runtime profiles are not part of the shipped payload.
    for source in walk_plugin_tree(root):
        relative = source.relative_to(root)
        if not source.is_file() or should_skip_path(relative, is_dir=False, rules=rules):
            continue
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    metadata = read_packaged_metadata(tmp_path)
    assert metadata is not None
    assert metadata.entries == []
    assert "send_selfie" not in metadata.entry_methods
