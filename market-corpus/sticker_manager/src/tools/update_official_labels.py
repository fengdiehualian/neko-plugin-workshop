"""Apply reviewed per-image labels without changing any image or delivery bytes."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import zipfile
from pathlib import Path

from bundled_media import open_pack


def update_labels(source: Path, labels: Path, target: Path) -> dict:
    changes = json.loads(labels.read_text(encoding="utf-8"))
    version = changes["pack_version"]
    overrides = changes["stickers"]
    with open_pack(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(version, int) or isinstance(version, bool) or version <= manifest["pack_version"]:
            raise ValueError("Label version must increase")
        entries = manifest["stickers"]
        keys = {row["file"].split("_", 1)[0] for row in entries}
        if set(overrides) - keys:
            raise ValueError("Unknown label asset")
        for row in entries:
            key = row["file"].split("_", 1)[0]
            patch = overrides.get(key)
            if patch is None:
                continue
            if set(patch) != {"caption", "tags"}:
                raise ValueError(f"Invalid label fields: {key}")
            if not isinstance(patch["caption"], str) or not 1 <= len(patch["caption"]) <= 300:
                raise ValueError(f"Invalid caption: {key}")
            tags = patch["tags"]
            if not isinstance(tags, list) or not 1 <= len(tags) <= 12:
                raise ValueError(f"Invalid tags: {key}")
            if any(not isinstance(tag, str) or not 1 <= len(tag) <= 24 for tag in tags):
                raise ValueError(f"Invalid tag: {key}")
            row.update(patch)
        manifest["pack_version"] = version
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        os.close(descriptor)
        temporary_path = Path(temporary)
        try:
            with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as output:
                for info in archive.infolist():
                    data = (
                        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                        if info.filename == "manifest.json" else archive.read(info.filename)
                    )
                    output.writestr(info, data)
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)
    return {"version": version, "reviewed": len(overrides), "entries": len(entries), "path": str(target)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source", type=Path, default=root / "official/official_pack.zip")
    parser.add_argument("--labels", type=Path, default=root / "tools/official_labels_v3.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(update_labels(args.source, args.labels, args.out), ensure_ascii=False))
