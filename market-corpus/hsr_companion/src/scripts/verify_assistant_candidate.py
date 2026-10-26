"""Test a separately built helper, never install it or replace port 8219."""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import time
from pathlib import Path


def verify(executable: Path, port: int):
    import psutil
    from PIL import Image
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient

    root = Path(__file__).resolve().parents[1]
    executable = executable.resolve(strict=True)
    if not executable.is_relative_to(root / "dist") or port == 8219:
        raise ValueError("Only plugin dist candidates on a separate port may be tested")
    # Fail before launching if another process owns the test port.
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    checked = subprocess.run([str(executable), "--self-test"], capture_output=True, text=True,
                             timeout=45, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    print(json.dumps({"kind": "packaged_self_test", "result": json.loads(checked.stdout)}, ensure_ascii=True), flush=True)
    process = subprocess.Popen([str(executable), "--port", str(port), "--parent-pid", str(os.getpid())],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    owned = psutil.Process(process.pid)
    client = VisionAssistantClient(f"http://127.0.0.1:{port}")
    samples = []
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            if client.health().get("connected"):
                break
            time.sleep(.25)
        else:
            raise RuntimeError("Candidate failed to start")
        # Allow a bounded first capture; the ten fixed samples remain separate.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            frame = client.fetch_frame(0)
            if frame:
                break
            time.sleep(.25)
        for index in range(10):
            started = time.monotonic()
            frame = client.fetch_frame(0)
            age = time.time() - frame.timestamp if frame else None
            size = None
            if frame:
                with Image.open(io.BytesIO(frame.image_bytes)) as image:
                    image.load()
                    size = list(image.size)
            sample = {"sample": index + 1, "age_at_receive": round(age, 4) if age is not None else None,
                      "seconds": round(time.monotonic() - started, 4), "seq": frame.frame_seq if frame else None,
                      "size": size, "fresh_under_two_seconds": age is not None and 0 <= age <= 2}
            samples.append(sample)
            print(json.dumps({"kind": "packaged_frame", **sample}), flush=True)
            time.sleep(.5)
        print(json.dumps({"kind": "packaged_batch_summary", "samples": len(samples),
            "fresh_count": sum(s["fresh_under_two_seconds"] for s in samples),
            "distinct_sequences": len({s["seq"] for s in samples if s["seq"] is not None}),
            "service_version": client.service_version, "installed": False}), flush=True)
    finally:
        # Terminate only this Popen's owned tree; never touch the live 8219 helper.
        try:
            tree = owned.children(recursive=True) + [owned]
        except psutil.NoSuchProcess:
            tree = []
        for child in tree:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(tree, timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--port", type=int, default=8221)
    args = parser.parse_args()
    verify(args.executable, args.port)
