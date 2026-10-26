"""Offline worker entry point.

Usage
-----
``bootstrap.py --check [--bundle-root C:\\path\\to\\bundle]``
    Print one structured self-check JSON line to stdout.  Exit 0 only when the
    bundle reports ``ready``; exit 2 for ``blocked``/``degraded``.

``bootstrap.py [--serve] [--bundle-root C:\\path\\to\\bundle]``
    Serve the JSON-lines protocol on stdin/stdout.

The bootstrap module imports only the standard library plus the local
``worker`` package.  Optional numpy/Pillow/cv2/onnxruntime/rapidocr imports are
performed inside ``package_check`` with errors captured, so ``--check`` never
crashes when they are absent or ABI-incompatible.  It never imports the host
SDK and never pushes messages to the host.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, Sequence

sys.dont_write_bytecode = True

_PACKAGE_PARENT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

package_check = importlib.import_module("worker.package_check")
protocol = importlib.import_module("worker.protocol")

USAGE = (
    "usage: bootstrap.py [--check | --serve] [--bundle-root ABSOLUTE_WINDOWS_PATH]\n"
    "  --check   print one offline runtime self-check JSON object (exit 0=ready, 2=blocked)\n"
    "  --serve   serve JSON-lines requests on stdin/stdout (default)\n"
)


class UsageError(ValueError):
    pass


def _parse_args(argv: Sequence[str]) -> tuple[str, str | None]:
    mode = "serve"
    explicit_mode = ""
    bundle_root: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"-h", "--help"}:
            return "help", None
        if arg == "--check":
            if explicit_mode and explicit_mode != "check":
                raise UsageError("--check and --serve are mutually exclusive")
            explicit_mode = "check"
            mode = "check"
        elif arg == "--serve":
            if explicit_mode and explicit_mode != "serve":
                raise UsageError("--check and --serve are mutually exclusive")
            explicit_mode = "serve"
            mode = "serve"
        elif arg == "--bundle-root":
            index += 1
            if index >= len(argv):
                raise UsageError("--bundle-root requires a value")
            if bundle_root is not None:
                raise UsageError("--bundle-root may be given only once")
            bundle_root = argv[index]
        else:
            raise UsageError("unknown argument: %s" % arg)
        index += 1
    return mode, bundle_root


def _write_json_line(stream: BinaryIO, value: dict[str, Any]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    stream.write(payload + b"\n")
    stream.flush()


class WorkerHandler:
    """Phase-1 request handler: ping, selfcheck, shutdown only."""

    def __init__(self, layout: package_check.BundleLayout) -> None:
        self.layout = layout
        self.stopping = False

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        op = str(request.get("op"))
        if op == "ping":
            return {
                "op": "ping",
                "protocol_version": protocol.PROTOCOL_VERSION,
                "pid": os.getpid(),
                "runtime_root": str(self.layout.python_root),
                "bundle_root": str(self.layout.bundle_root),
                "binary_frames": False,
                "host_push": False,
            }
        if op == "selfcheck":
            return package_check.check_bundle(self.layout.bundle_root)
        if op == "shutdown":
            self.stopping = True
            return {
                "op": "shutdown",
                "acknowledged": True,
                "protocol_version": protocol.PROTOCOL_VERSION,
            }
        if op == "frame.process":
            raise protocol.ProtocolError(
                "not_implemented",
                "frame.process is reserved for a later phase; no frame bytes were accepted",
                retryable=False,
            )
        raise protocol.ProtocolError("unsupported_op", "unsupported op %r" % op)


def serve(layout: package_check.BundleLayout) -> int:
    """Serve protocol lines until EOF, shutdown, or a closed pipe."""
    reader: BinaryIO = sys.stdin.buffer
    writer: BinaryIO = sys.stdout.buffer
    handler = WorkerHandler(layout)

    while not handler.stopping:
        try:
            raw = protocol.read_limited_line(reader)
        except protocol.ProtocolError as exc:
            try:
                writer.write(protocol.encode_message(protocol.error_response(exc)))
                writer.flush()
            except OSError:
                return 0
            continue
        except OSError:
            return 0
        if raw is None:
            break

        received_at = time.monotonic()
        try:
            response = protocol.process_line(raw, handler, received_at=received_at)
        except protocol.ProtocolError as exc:
            response = protocol.encode_message(protocol.error_response(exc))
        try:
            writer.write(response)
            writer.flush()
        except (BrokenPipeError, OSError):
            return 0
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        mode, bundle_root = _parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        sys.stderr.write("worker: %s\n%s" % (exc, USAGE))
        return 64

    if mode == "help":
        sys.stdout.write(USAGE)
        return 0

    if mode == "check":
        result = package_check.check_bundle(bundle_root)
        _write_json_line(sys.stdout.buffer, result)
        return 0 if result.get("status") == "ready" else 2

    try:
        layout = package_check.resolve_layout(bundle_root)
    except package_check.BundlePathError as exc:
        sys.stderr.write("worker: %s: %s\n" % (exc.code, exc.message))
        return 64

    try:
        return serve(layout)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - never emit a traceback to the host
        sys.stderr.write("worker: fatal: %s\n" % type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
