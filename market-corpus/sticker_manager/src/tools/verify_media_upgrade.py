"""Verify original animation timing and upgrade a read-only copy of an installed library."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

from bundled_media import open_pack

ROOT = Path(__file__).resolve().parents[1]


def verify(pack_path: Path, installed: Path, source: Path) -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    from build_official_pack import frames_of

    # The SDK facade is stubbed; all writes go to a disposable plugin-local copy.
    spec = importlib.util.spec_from_file_location("conftest", ROOT / "tests" / "conftest.py")
    fixtures = importlib.util.module_from_spec(spec)
    sys.modules["conftest"] = fixtures
    spec.loader.exec_module(fixtures)
    fixtures.ensure_sdk_stub()
    fixtures.register_plugin_package()
    from sticker_manager.services.library import Library

    motions = 0
    with open_pack(pack_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for row in manifest["stickers"]:
            original = (source / row["source_file"]).read_bytes()
            a, times_a, loop_a = frames_of(original)
            assert archive.read("stickers/" + row["file"]) == original
            b, times_b, loop_b = frames_of(archive.read("delivery/" + row["delivery_file"]))
            assert sum(times_a) == sum(times_b) and loop_a == loop_b, row["file"]
            original_boundaries, encoded_boundaries = set(), set()
            elapsed = 0
            for duration in times_a:
                elapsed += duration
                original_boundaries.add(elapsed)
            elapsed = 0
            for duration in times_b:
                elapsed += duration
                encoded_boundaries.add(elapsed)
            assert encoded_boundaries.issubset(original_boundaries), row["file"]
            motions += len(a)
    gate = ROOT / ".tmpgate"
    gate.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="upgrade-qa-", dir=gate) as folder:
        copied = Path(folder) / "library"
        copied.mkdir()
        for name in ("catalog.json", "usage.json"):
            if (installed / name).exists():
                shutil.copyfile(installed / name, copied / name)
        shutil.copytree(installed / "stickers", copied / "stickers")
        lib = Library(copied)
        assert lib.load().ok
        old = {sticker.id: sticker for sticker in lib.all()}
        active = lib.active_zone()
        usage_before = lib.usage_path().read_bytes() if lib.usage_path().exists() else None
        outcome = lib.seed_official(pack_path)
        assert outcome["refresh"]["status"] == "refreshed", outcome
        assert set(old) == {sticker.id for sticker in lib.all()}
        for sticker in lib.all():
            before = old[sticker.id]
            for field in ("disabled", "added_at", "use_count", "last_used_at", "owner_edited", "zone"):
                assert getattr(sticker, field) == getattr(before, field), (sticker.id, field)
            assert lib.image_path(sticker).exists()
        assert lib.active_zone() == active
        if usage_before is not None:
            assert lib.usage_path().read_bytes() == usage_before
        after_disk = lib.catalog_path.read_bytes()
        again = Library(copied)
        assert again.load().ok
        second = again.seed_official(pack_path)
        assert second["refresh"]["status"] == "current"
        assert again.catalog_path.read_bytes() == after_disk
        return {"unique_motion_frames": motions, "library_count": len(old), "upgrade": outcome, "restart": second}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "official" / "official_pack.zip")
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=ROOT.parent / "GIF")
    args = parser.parse_args()
    print(json.dumps(verify(args.pack, args.library, args.source), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
