"""Time the real playback-copy helper on a read-only asset and temporary SDK data."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--mime", choices=["video/mp4", "video/webm"], default="video/mp4")
    args = parser.parse_args()
    # Load only pure core modules; never initialize the real plugin or Store.
    package = types.ModuleType("_playback_benchmark")
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "core")]
    sys.modules[package.__name__] = package
    playback = importlib.import_module("_playback_benchmark.media_playback")
    state = importlib.import_module("_playback_benchmark.state")
    with tempfile.TemporaryDirectory(prefix="neko-playback-benchmark-") as temporary:
        base = Path(temporary)
        plugin = types.SimpleNamespace(data_path=lambda relative="": base / relative)
        (base / "wallpapers").mkdir()
        storage_id = "b" * 32
        original = base / "wallpapers" / (storage_id + ".bin")
        shutil.copyfile(args.asset, original)
        hashes = []
        with original.open("rb") as handle:
            while block := handle.read(state._GALLERY_VIDEO_CHUNK_BYTES):
                hashes.append(hashlib.sha256(block).hexdigest())
        manifest = {"mime": args.mime, "size": original.stat().st_size, "hashes": hashes, "storage_id": storage_id}
        playback.initialize(plugin)
        verified = {}
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            _name, reused = playback.prepare(plugin, manifest, verified)
            samples.append({"reused": reused, "elapsed_ms": round((time.perf_counter() - start) * 1000, 2)})
        print(json.dumps({"bytes": manifest["size"], "samples": samples}, indent=2))


if __name__ == "__main__":
    main()
