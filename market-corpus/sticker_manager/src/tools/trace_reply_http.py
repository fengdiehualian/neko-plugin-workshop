"""Opt-in OpenAI HTTP observer. No host imports, config writes or raw chat logs.

Run with --upstream-base and --out, then route a text test session to the
printed loopback base URL. Requests and streamed response bodies are forwarded
without rewriting their JSON. Only lengths, keyed fingerprints and comparisons
are saved. Ctrl+C stops the observer; restore the session's original API URL.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_BODY = 16 * 1024 * 1024
MAX_TEXT = 128 * 1024
MAX_EVENT = 2 * 1024 * 1024
MAX_TRACE = 8 * 1024 * 1024
HOP_HEADERS = {
    "host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length", "content-encoding",
}


def normalized(text):
    return "".join(
        char for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def text_of(content):
    if isinstance(content, str):
        return content[:MAX_TEXT]
    if isinstance(content, list):
        return "".join(
            item.get("text", "")[:MAX_TEXT] for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
            and item.get("type") == "text"
        )[:MAX_TEXT]
    return ""


def safe_tool(name):
    return name if name in {"sticker_send", "sticker_list"} else "other"


class Trace:
    def __init__(self, path):
        self.path = Path(path)
        self.key = secrets.token_bytes(32)
        self.lock = threading.Lock()
        self.previous = OrderedDict()
        self.sequence = 0
        self.write_failed = False
        # Exclusive creation prevents mixing traces whose keys differ.
        self.stream = self.path.open("x", encoding="utf-8")
        self.written = 0

    def fingerprint(self, text):
        return hmac.new(
            self.key, text.encode("utf-8", errors="surrogatepass"), hashlib.sha256,
        ).hexdigest()[:24]

    def write(self, record):
        line = json.dumps(record, ensure_ascii=True) + "\n"
        with self.lock:
            if self.write_failed:
                return
            try:
                if self.written + len(line) > MAX_TRACE:
                    self.write_failed = True
                    print("Trace size limit reached; forwarding continues.", flush=True)
                    return
                self.stream.write(line)
                self.stream.flush()
                self.written += len(line)
            except OSError:
                self.write_failed = True
                print("Trace write failed; forwarding continues.", flush=True)

    def begin(self, body):
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        last_user_at = max(
            (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"),
            default=-1,
        )
        # Freeze the context through this user turn; tool follow-ups append after it.
        scope = self.fingerprint(json.dumps([
            [m.get("role"), text_of(m.get("content"))]
            for m in messages[:last_user_at + 1] if isinstance(m, dict)
        ], ensure_ascii=True))
        last_round = next((
            message for message in reversed(messages)
            if isinstance(message, dict) and message.get("role") == "assistant"
            and message.get("tool_calls")
        ), None)
        # Only link a follow-up when the latest user message precedes the round.
        round_at = next((i for i, m in enumerate(messages) if m is last_round), -1)
        parent = None
        history_text = text_of(last_round.get("content")) if last_round else ""
        if last_round and round_at > last_user_at:
            calls = last_round.get("tool_calls")
            if isinstance(calls, list):
                with self.lock:
                    for call in calls:
                        call_id = call.get("id") if isinstance(call, dict) else None
                        if isinstance(call_id, str):
                            parent = self.previous.get((scope, self.fingerprint(call_id)))
                            if parent:
                                break
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
        self.write({
            "event": "request", "time": time.time(), "request": request_id,
            "scope": scope, "stream": body.get("stream") is True,
            "message_count": len(messages), "parent": parent["request"] if parent else None,
            "history_pretool_chars": len(history_text),
            "history_matches_parent": history_text == parent["text"] if parent else None,
            "history_matches_parent_normalized": (
                normalized(history_text) == normalized(parent["text"]) if parent else None
            ),
        })
        return Observation(self, request_id, scope, parent)

    def close(self):
        self.stream.close()


class Observation:
    def __init__(self, trace, request_id, scope, parent):
        self.trace, self.request_id, self.scope, self.parent = trace, request_id, scope, parent
        self.text = ""
        self.tools = {}
        self.finish = None
        self.first_text_ms = None
        self.started = time.monotonic()
        self.truncated = False
        self.parse_errors = 0

    def accept(self, payload):
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", choice.get("message", {}))
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                if self.first_text_ms is None:
                    self.first_text_ms = round((time.monotonic() - self.started) * 1000, 1)
                self.truncated |= len(self.text) + len(content) > MAX_TEXT
                self.text = (self.text + content)[:MAX_TEXT]
            calls = delta.get("tool_calls", [])
            for index, call in enumerate(calls if isinstance(calls, list) else []):
                if not isinstance(call, dict):
                    continue
                slot = call.get("index", index)
                if type(slot) is not int or not 0 <= slot < 64:
                    continue
                tool = self.tools.setdefault(slot, {"id": "", "name": "", "arguments": ""})
                if isinstance(call.get("id"), str):
                    tool["id"] = (tool["id"] + call["id"])[:1024]
                function = call.get("function")
                if isinstance(function, dict):
                    for key in ("name", "arguments"):
                        if isinstance(function.get(key), str):
                            tool[key] = (tool[key] + function[key])[:MAX_TEXT]
            if isinstance(choice.get("finish_reason"), str):
                value = choice["finish_reason"]
                self.finish = value if value in {"stop", "tool_calls", "length", "content_filter"} else "other"

    def end(self, *, complete, status, error=None):
        old = normalized(self.parent["text"]) if self.parent else ""
        current = normalized(self.text)
        tool_rows = []
        for tool in self.tools.values():
            caption_chars = None
            try:
                args = json.loads(tool["arguments"])
                if isinstance(args, dict) and isinstance(args.get("text", ""), str):
                    caption_chars = len(args.get("text", ""))
            except (ValueError, TypeError):
                pass
            tool_rows.append({
                "name": safe_tool(tool["name"]),
                "call_id_hash": (
                    hashlib.sha256(tool["id"].encode("utf-8", errors="replace")).hexdigest()[:16]
                    if tool["id"] else None
                ),
                "caption_chars": caption_chars,
            })
        evidence_complete = (
            complete and 200 <= status < 300 and not self.truncated and self.parse_errors == 0
            and self.finish is not None
            and (not self.parent or self.parent["complete"])
        )
        self.trace.write({
            "event": "response", "time": time.time(), "request": self.request_id,
            "parent": self.parent["request"] if self.parent else None, "status": status,
            "complete": complete, "evidence_complete": evidence_complete,
            "text_chars": len(self.text), "text_fingerprint": self.trace.fingerprint(self.text),
            "finish": self.finish, "tools": tool_rows, "truncated": self.truncated,
            "parse_errors": self.parse_errors, "error": error,
            "first_text_ms": self.first_text_ms,
            "elapsed_ms": round((time.monotonic() - self.started) * 1000, 1),
            "provider_replayed_pretool": (
                bool(old and current.startswith(old)) if self.parent and evidence_complete else None
            ),
            "replayed_normalized_chars": (
                len(old) if old and current.startswith(old) and evidence_complete else 0
            ),
        })
        if complete and 200 <= status < 300:
            with self.trace.lock:
                for tool in self.tools.values():
                    if tool["id"]:
                        key = (self.scope, self.trace.fingerprint(tool["id"]))
                        self.trace.previous[key] = {
                            "request": self.request_id, "text": self.text, "complete": evidence_complete,
                        }
                        self.trace.previous.move_to_end(key)
                while len(self.trace.previous) > 128:
                    self.trace.previous.popitem(last=False)


class SSE:
    def __init__(self, observation):
        self.observation = observation
        self.pending = b""
        self.data = []
        self.size = 0
        self.dropping = False
        self.done = False

    def feed(self, chunk):
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            line = line.removesuffix(b"\r")
            if not line:
                self.dispatch()
            elif line.startswith(b"data:") and not self.dropping:
                part = line[5:].removeprefix(b" ")
                self.size += len(part)
                if self.size > MAX_EVENT:
                    self.observation.parse_errors += 1
                    self.data.clear()
                    self.dropping = True
                else:
                    self.data.append(part)
        if len(self.pending) > MAX_EVENT:
            self.pending = b""
            self.dropping = True
            self.observation.parse_errors += 1

    def dispatch(self):
        if self.data and not self.dropping:
            raw = b"\n".join(self.data)
            if raw == b"[DONE]":
                self.done = True
            else:
                try:
                    self.observation.accept(json.loads(raw))
                except (ValueError, TypeError):
                    self.observation.parse_errors += 1
        self.data.clear()
        self.size = 0
        self.dropping = False


def validate_upstream(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1"})
    ):
        raise ValueError("Upstream must be an HTTPS base URL (HTTP allowed for loopback tests).")
    return value.rstrip("/") + "/chat/completions"


def make_handler(upstream, trace):
    parsed = urlsplit(upstream)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection

    class Handler(BaseHTTPRequestHandler):
        timeout = 120

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"observer":true}')
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                self.send_error(400)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    self.send_error(413)
                    return
                raw = self.rfile.read(length)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                self.send_error(400)
                return
            observation = trace.begin(body)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            headers["Accept-Encoding"] = "identity"
            started = False
            status = 502
            complete = False
            error = None
            connection = connection_type(parsed.hostname, parsed.port, timeout=120)
            try:
                connection.request("POST", parsed.path, body=raw, headers=headers)
                response = connection.getresponse()
                status = response.status
                self.send_response(status)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_HEADERS or key.lower() == "content-encoding":
                        self.send_header(key, value)
                self.end_headers()
                started = True
                is_sse = "text/event-stream" in response.headers.get("content-type", "")
                encoded = response.headers.get("content-encoding", "identity") != "identity"
                if encoded:
                    observation.parse_errors += 1
                parser = SSE(observation)
                buffered = bytearray()
                while chunk := response.read1(64 * 1024):
                    # Forward first, observe second. No accumulated text is replayed.
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if encoded:
                        continue
                    if is_sse:
                        parser.feed(chunk)
                    elif len(buffered) + len(chunk) <= MAX_EVENT:
                        buffered.extend(chunk)
                    else:
                        observation.truncated = True
                if is_sse and not encoded:
                    if parser.pending:
                        parser.feed(b"\n\n")
                    parser.dispatch()
                    complete = parser.done
                elif not encoded and not observation.truncated:
                    try:
                        observation.accept(json.loads(buffered))
                        complete = True
                    except (ValueError, TypeError):
                        observation.parse_errors += 1
            except (http.client.HTTPException, OSError) as exc:
                error = type(exc).__name__
                if not started:
                    self.send_error(502)
            finally:
                connection.close()
                observation.end(complete=complete, status=status, error=error)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-base", required=True)
    parser.add_argument("--port", type=int, default=49321)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    upstream = validate_upstream(args.upstream_base)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    trace = Trace(args.out)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(upstream, trace))
    server.daemon_threads = False
    print(f"Observer base URL: http://127.0.0.1:{server.server_port}/v1", flush=True)
    print(f"Fingerprint trace: {args.out}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        trace.close()


if __name__ == "__main__":
    main()
