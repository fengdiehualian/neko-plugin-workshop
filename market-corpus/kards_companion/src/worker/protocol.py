"""Versioned JSON-lines protocol for the offline perception worker.

Wire contract (version 1)
-------------------------
One UTF-8 JSON object per line on stdin; one JSON object per line on stdout.
There is no host SDK import, no host push channel, and no inline image data:
a request may carry ``frame_id`` (an integer identity) but never a full frame
or a base64 blob.  Every response is either a structured ``state`` object or a
structured ``error`` object.

Phase 1 enforces:
* protocol version ``1``;
* a hard line-length limit before JSON parsing;
* request-id / session-id / frame-id validation;
* a bounded ``timeout_ms`` budget with a structured ``timeout`` error;
* rejection of image/base64-style payload keys and oversized inline strings.

The timeout is cooperative in phase 1: the handler runs in-process and the
worker reports a timeout when the budget has elapsed.  A later phase may add
hard cancellation/isolation; this module does not spawn threads or processes.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, BinaryIO, Callable

PROTOCOL_VERSION = 1
PROTOCOL_NAME = "kards-worker-jsonl"

MAX_LINE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_INLINE_STRING_CHARS = 16 * 1024
MAX_PAYLOAD_DEPTH = 16
MAX_REQUEST_ID_LENGTH = 128
MAX_SESSION_ID_LENGTH = 128
MAX_OP_LENGTH = 64

DEFAULT_TIMEOUT_MS = 15_000
MAX_TIMEOUT_MS = 60_000
MAX_FRAME_ID = 2**63 - 1

# Ops understood by this phase.  ``frame.process`` is recognized so that a
# caller gets a clear "not implemented" answer with valid correlation ids;
# every other unknown op is rejected as ``unsupported_op``.
FRAME_OPS = frozenset({"frame.process"})
SUPPORTED_OPS = frozenset({"ping", "selfcheck", "shutdown"})
KNOWN_OPS = SUPPORTED_OPS | FRAME_OPS

_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {"v", "id", "op", "session_id", "frame_id", "timeout_ms", "payload"}
)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OP_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")

_BINARY_KEYS_EXACT = frozenset(
    {
        "pixels",
        "frame_b64",
        "image_b64",
        "jpeg_b64",
        "png_b64",
        "screenshot",
        "image_data",
        "frame_data",
        "raw_frame",
        "raw_image",
        "raw_bytes",
        "bytes",
        "blob",
    }
)
_BINARY_KEYS_SUFFIXES = ("_b64", "_base64", "_binary", "_bytes")
_DATA_URI_PREFIX = "data:"


class ProtocolError(Exception):
    """A structured protocol error that can be returned to the caller."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
        session_id: str | None = None,
        frame_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = bool(retryable)
        self.request_id = request_id
        self.session_id = session_id
        self.frame_id = frame_id


def _valid_request_id(value: Any) -> str | None:
    if isinstance(value, str) and _REQUEST_ID_RE.match(value):
        return value
    return None


def _valid_session_id(value: Any) -> str | None:
    if isinstance(value, str) and _SESSION_ID_RE.match(value):
        return value
    return None


def _valid_frame_id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value <= 0 or value > MAX_FRAME_ID:
        return None
    return value


def _correlation(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    out: dict[str, Any] = {}
    request_id = _valid_request_id(obj.get("id"))
    session_id = _valid_session_id(obj.get("session_id"))
    frame_id = _valid_frame_id(obj.get("frame_id"))
    if request_id is not None:
        out["request_id"] = request_id
    if session_id is not None:
        out["session_id"] = session_id
    if frame_id is not None:
        out["frame_id"] = frame_id
    return out


def _error(code: str, message: str, obj: Any = None, *, retryable: bool = False) -> ProtocolError:
    correlation = _correlation(obj)
    return ProtocolError(
        code,
        message,
        retryable=retryable,
        request_id=correlation.get("request_id"),
        session_id=correlation.get("session_id"),
        frame_id=correlation.get("frame_id"),
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON number %s is not allowed" % value)


def _check_payload(value: Any, path: str = "payload", depth: int = 0) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ProtocolError("payload_too_deep", "payload nesting exceeds %d" % MAX_PAYLOAD_DEPTH)
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("invalid_request", "%s contains a non-string key" % path)
            lowered = key.lower()
            child = "%s.%s" % (path, key)
            if lowered in _BINARY_KEYS_EXACT or lowered.endswith(_BINARY_KEYS_SUFFIXES):
                raise ProtocolError(
                    "binary_payload_rejected",
                    "%s is a binary/image payload field; full frames are not accepted" % child,
                )
            if lowered in {"image", "frame"} and isinstance(item, str):
                raise ProtocolError(
                    "binary_payload_rejected",
                    "%s cannot carry inline image text; pass a frame_id instead" % child,
                )
            _check_payload(item, child, depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_payload(item, "%s[%d]" % (path, index), depth + 1)
        return
    if isinstance(value, str):
        if len(value) > MAX_INLINE_STRING_CHARS:
            raise ProtocolError(
                "inline_payload_too_large",
                "%s is %d characters; inline string limit is %d"
                % (path, len(value), MAX_INLINE_STRING_CHARS),
            )
        if value[:32].lower().startswith(_DATA_URI_PREFIX) and "base64," in value[:128].lower():
            raise ProtocolError(
                "binary_payload_rejected",
                "%s carries a base64 data URI; full frames are not accepted" % path,
            )
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError("invalid_request", "%s contains a non-finite number" % path)
        return
    raise ProtocolError("invalid_request", "%s has an unsupported JSON value" % path)


def validate_request(obj: Any) -> dict[str, Any]:
    """Validate a decoded request and return a normalized copy.

    Raises :class:`ProtocolError` with a machine-readable code.  Correlated ids
    are preserved on the error whenever they are syntactically safe.
    """
    if not isinstance(obj, dict):
        raise _error("invalid_request", "request must be a JSON object", obj)

    unknown = sorted(set(obj) - _ALLOWED_TOP_LEVEL_KEYS)
    if unknown:
        raise _error("invalid_request", "unknown top-level fields: %s" % ", ".join(unknown), obj)

    version = obj.get("v")
    if isinstance(version, bool) or not isinstance(version, int):
        raise _error("invalid_request", "v must be protocol version %d" % PROTOCOL_VERSION, obj)
    if version != PROTOCOL_VERSION:
        raise _error(
            "unsupported_protocol_version",
            "protocol version %r is not supported; expected %d" % (version, PROTOCOL_VERSION),
            obj,
        )

    request_id = obj.get("id")
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.match(request_id):
        raise _error(
            "invalid_request_id",
            "id must match %s" % _REQUEST_ID_RE.pattern,
            obj,
        )

    op = obj.get("op")
    if not isinstance(op, str) or not _OP_RE.match(op):
        raise _error("invalid_request", "op must match %s" % _OP_RE.pattern, obj)
    if op not in KNOWN_OPS:
        raise _error("unsupported_op", "unsupported op %r" % op, obj)

    session_id = obj.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise _error("invalid_session_id", "session_id must be a string", obj)
    if isinstance(session_id, str) and not _SESSION_ID_RE.match(session_id):
        raise _error(
            "invalid_session_id",
            "session_id must match %s" % _SESSION_ID_RE.pattern,
            obj,
        )
    if op in FRAME_OPS and not session_id:
        raise _error("missing_session_id", "op %s requires session_id" % op, obj)

    frame_id = obj.get("frame_id")
    if frame_id is not None and _valid_frame_id(frame_id) is None:
        raise _error(
            "invalid_frame_id",
            "frame_id must be an integer in 1..%d" % MAX_FRAME_ID,
            obj,
        )
    if op in FRAME_OPS and _valid_frame_id(frame_id) is None:
        raise _error("missing_frame_id", "op %s requires a positive frame_id" % op, obj)

    timeout_ms = obj.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise _error("invalid_timeout", "timeout_ms must be an integer", obj)
    if timeout_ms < 1 or timeout_ms > MAX_TIMEOUT_MS:
        raise _error(
            "invalid_timeout",
            "timeout_ms must be in 1..%d" % MAX_TIMEOUT_MS,
            obj,
        )

    payload = obj.get("payload", {})
    if not isinstance(payload, dict):
        raise _error("invalid_request", "payload must be a JSON object", obj)
    _check_payload(payload)

    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "op": op,
        "session_id": session_id,
        "frame_id": frame_id,
        "timeout_ms": timeout_ms,
        "payload": payload,
    }


def decode_line(raw: bytes | bytearray | str) -> dict[str, Any]:
    """Decode one bounded JSON line and validate it as a request."""
    if isinstance(raw, str):
        candidate = raw.encode("utf-8", errors="strict")
    elif isinstance(raw, (bytes, bytearray)):
        candidate = bytes(raw)
    else:
        raise ProtocolError("invalid_request", "protocol input must be bytes or text")

    if candidate.endswith(b"\n"):
        candidate = candidate[:-1]
    if candidate.endswith(b"\r"):
        candidate = candidate[:-1]
    if len(candidate) > MAX_LINE_BYTES:
        raise ProtocolError(
            "line_too_large",
            "line is %d bytes; limit is %d" % (len(candidate), MAX_LINE_BYTES),
        )
    if not candidate:
        raise ProtocolError("invalid_json", "empty protocol line")

    try:
        text = candidate.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_encoding", "protocol line is not UTF-8: %s" % exc) from exc

    try:
        obj = json.loads(text, parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ProtocolError(
            "payload_too_deep",
            "JSON nesting exceeds the parser limit",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError("invalid_json", "invalid JSON: %s" % exc) from exc
    return validate_request(obj)


def read_limited_line(stream: BinaryIO) -> bytes | None:
    """Read one line without ever buffering an unbounded line.

    Oversized lines are drained through the next newline and raised as
    ``line_too_large``; the next call can continue with the next line.
    """
    chunk = stream.readline(MAX_LINE_BYTES + 1)
    if not chunk:
        return None
    if len(chunk) > MAX_LINE_BYTES and not chunk.endswith(b"\n"):
        while True:
            drained = stream.readline(64 * 1024)
            if not drained or drained.endswith(b"\n"):
                break
        raise ProtocolError(
            "line_too_large",
            "line exceeds %d bytes and was discarded" % MAX_LINE_BYTES,
        )
    content = chunk
    if content.endswith(b"\n"):
        content = content[:-1]
    if content.endswith(b"\r"):
        content = content[:-1]
    if len(content) > MAX_LINE_BYTES:
        raise ProtocolError(
            "line_too_large",
            "line is %d bytes; limit is %d" % (len(content), MAX_LINE_BYTES),
        )
    return chunk


def encode_message(message: dict[str, Any]) -> bytes:
    """Serialize one response line, enforcing the response size budget."""
    try:
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except RecursionError as exc:
        raise ProtocolError("internal_error", "response nesting is too deep") from exc
    except (TypeError, ValueError) as exc:
        raise ProtocolError("internal_error", "response is not JSON serializable: %s" % exc) from exc
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ProtocolError(
            "response_too_large",
            "response is %d bytes; limit is %d" % (len(encoded), MAX_RESPONSE_BYTES),
        )
    return encoded + b"\n"


def _response_base(
    *,
    request_id: str | None,
    session_id: str | None = None,
    frame_id: int | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"v": PROTOCOL_VERSION, "id": request_id}
    if session_id is not None:
        response["session_id"] = session_id
    if frame_id is not None:
        response["frame_id"] = frame_id
    return response


def state_response(request: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    response = _response_base(
        request_id=request.get("id"),
        session_id=request.get("session_id"),
        frame_id=request.get("frame_id"),
    )
    response["ok"] = True
    response["state"] = state
    return response


def error_response(error: ProtocolError) -> dict[str, Any]:
    response = _response_base(
        request_id=error.request_id,
        session_id=error.session_id,
        frame_id=error.frame_id,
    )
    response["ok"] = False
    response["error"] = {
        "code": error.code,
        "message": error.message,
        "retryable": bool(error.retryable),
    }
    return response


def handle_request(
    request: dict[str, Any],
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    received_at: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Dispatch one validated request and enforce the cooperative timeout.

    The timeout budget starts at ``received_at`` (or "now").  If the handler
    finishes after the budget, its result is discarded and a retryable
    ``timeout`` error is returned.  The process is never killed here.
    """
    started = clock() if received_at is None else float(received_at)
    deadline = started + (int(request["timeout_ms"]) / 1000.0)
    if clock() >= deadline:
        return error_response(
            ProtocolError(
                "timeout",
                "request deadline expired before dispatch",
                retryable=True,
                request_id=request.get("id"),
                session_id=request.get("session_id"),
                frame_id=request.get("frame_id"),
            )
        )

    try:
        state = handler(request)
    except ProtocolError as exc:
        exc.request_id = exc.request_id or request.get("id")
        exc.session_id = exc.session_id or request.get("session_id")
        exc.frame_id = exc.frame_id or request.get("frame_id")
        return error_response(exc)
    except Exception as exc:  # noqa: BLE001 - return a structured error, never a traceback
        return error_response(
            ProtocolError(
                "internal_error",
                "handler failed: %s" % type(exc).__name__,
                retryable=False,
                request_id=request.get("id"),
                session_id=request.get("session_id"),
                frame_id=request.get("frame_id"),
            )
        )

    if clock() > deadline:
        return error_response(
            ProtocolError(
                "timeout",
                "handler exceeded timeout_ms and its result was discarded",
                retryable=True,
                request_id=request.get("id"),
                session_id=request.get("session_id"),
                frame_id=request.get("frame_id"),
            )
        )
    if not isinstance(state, dict):
        return error_response(
            ProtocolError(
                "internal_error",
                "handler returned a non-object state",
                retryable=False,
                request_id=request.get("id"),
                session_id=request.get("session_id"),
                frame_id=request.get("frame_id"),
            )
        )
    return state_response(request, state)


def process_line(
    raw: bytes | bytearray | str,
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    received_at: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """Decode, dispatch and encode one protocol line.

    All failures become a single structured error line.  No image data, host
    push, or free-form logging is ever written to stdout.
    """
    try:
        request = decode_line(raw)
    except ProtocolError as exc:
        return encode_message(error_response(exc))
    try:
        response = handle_request(request, handler, received_at=received_at, clock=clock)
    except Exception as exc:  # noqa: BLE001 - keep the loop alive
        response = error_response(
            ProtocolError(
                "internal_error",
                "dispatch failed: %s" % type(exc).__name__,
                request_id=request.get("id"),
                session_id=request.get("session_id"),
                frame_id=request.get("frame_id"),
            )
        )
    try:
        return encode_message(response)
    except ProtocolError as exc:
        exc.request_id = request.get("id")
        exc.session_id = request.get("session_id")
        exc.frame_id = request.get("frame_id")
        return encode_message(error_response(exc))
