"""Split an unchanged ZIP into Git-friendly chunks and a checksum manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CHUNK_BYTES = 64 * 1024 * 1024


def split_pack(source: Path, destination: Path, *, chunk_bytes: int = CHUNK_BYTES) -> Path:
    if not 0 < chunk_bytes <= CHUNK_BYTES:
        raise ValueError("chunk size must be between 1 and 64 MiB")
    size = source.stat().st_size
    if not 0 < size <= 512 * 1024 * 1024:
        raise ValueError("archive size must be between 1 and 512 MiB")
    if (size + chunk_bytes - 1) // chunk_bytes > 128:
        raise ValueError("too many chunks")
    destination.mkdir(parents=True, exist_ok=True)
    rows = []
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        index = 0
        while data := stream.read(chunk_bytes):
            index += 1
            name = f"official_pack.zip.part{index:03d}"
            temporary = destination / (name + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(destination / name)
            digest.update(data)
            rows.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if sum(row["size"] for row in rows) != size or source.stat().st_size != size:
        raise ValueError("source changed while splitting")
    manifest = destination / "official_pack.zip.parts.json"
    temporary = manifest.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({
        "schema_version": 1, "size": size, "sha256": digest.hexdigest(), "parts": rows,
    }, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "official")
    args = parser.parse_args()
    print(split_pack(args.source, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
