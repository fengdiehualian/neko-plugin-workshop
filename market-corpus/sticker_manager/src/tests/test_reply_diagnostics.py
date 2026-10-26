from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import FakeHostContext, build_plugin
from sticker_manager.services.reply_diagnostics import reply_context_fields

from .test_host_reply_tail import CONTEXT

SPEC = importlib.util.spec_from_file_location(
    "trace_reply_http", Path(__file__).resolve().parents[1] / "tools/trace_reply_http.py",
)
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)
REPORT_SPEC = importlib.util.spec_from_file_location(
    "diagnose_reply_repetition", Path(__file__).resolve().parents[1] / "tools/diagnose_reply_repetition.py",
)
reporter = importlib.util.module_from_spec(REPORT_SPEC)
REPORT_SPEC.loader.exec_module(reporter)


def test_context_logs_identifiers_without_capability_token_or_raw_values():
    context = {**CONTEXT, "attempt": 0, "invocation_id": "invocation-private"}
    fields = reply_context_fields({"_ctx": {"host_reply": context}})
    assert "host_context=v1" in fields and "attempt=0" in fields
    for key in ("request_id", "reply_id", "call_id", "invocation_id"):
        assert f"{key}_hash=" in fields
        assert context[key] not in fields
    assert "token" not in fields and CONTEXT["token"] not in fields
    assert reply_context_fields({}) == "host_context=absent"
    assert reply_context_fields({"_ctx": {"host_reply": {"version": True}}}) == "host_context=absent"
    assert "request_id_hash=" in reply_context_fields({
        "_ctx": {"host_reply": {"version": 1, "request_id": "\ud800"}},
    })


@pytest.mark.parametrize("method", ["tool_sticker_list", "tool_sticker_send"])
def test_diagnostics_never_add_speech_or_change_tool_results(tmp_path, run_async, caplog, method):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    caplog.set_level("INFO")
    result = run_async(getattr(plugin, method)(_ctx={"host_reply": CONTEXT}))
    assert result["reason"] == "not_enabled"
    assert f"reply tool enter: name={method.removeprefix('tool_')}" in caplog.text
    assert f"reply tool return: name={method.removeprefix('tool_')}" in caplog.text
    assert CONTEXT["token"] not in caplog.text and not host.push.calls


def tool_round(text, call_id="unique-call"):
    return {
        "role": "assistant", "content": text,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "sticker_send"}}],
    }


def emit_first(trace, text="A complete reply!", call_id="unique-call", user="user-private"):
    observation = trace.begin({"messages": [{"role": "user", "content": user}], "stream": True})
    observation.accept({"choices": [{"delta": {
        "content": text, "tool_calls": [{
            "index": 0, "id": call_id,
            "function": {"name": "sticker_send", "arguments": '{"text":""}'},
        }],
    }, "finish_reason": "tool_calls"}]})
    observation.end(complete=True, status=200)
    return observation


def follow_up(trace, history="A complete reply!", text="A complete reply!"):
    observation = trace.begin({"messages": [
        {"role": "user", "content": "user-private"}, tool_round(history),
        {"role": "tool", "tool_call_id": "unique-call", "content": '{"ok":true}'},
    ]})
    observation.accept({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})
    observation.end(complete=True, status=200)


def rows(trace):
    trace.stream.flush()
    return [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]


def test_provider_replay_is_distinguished_from_missing_history_without_raw_chat(tmp_path):
    trace = observer.Trace(tmp_path / "trace.jsonl")
    emit_first(trace)
    follow_up(trace)
    captured = rows(trace)
    assert captured[2]["history_matches_parent"] is True
    assert captured[3]["provider_replayed_pretool"] is True
    assert captured[3]["parent"] == 1
    raw = trace.path.read_text()
    assert "A complete reply" not in raw and "user-private" not in raw and "unique-call" not in raw
    trace.close()


@pytest.mark.parametrize(("history", "answer", "matches", "replayed"), [
    ("", "A complete reply!", False, True),
    ("A complete reply!", "A complete reply! Additional words.", True, True),
    ("A complete reply!", "Additional words.", True, False),
    ("A complete reply!", "", True, False),
    ("A complete reply!", "A complete reply.", True, True),
])
def test_follow_up_reports_both_history_loss_and_provider_replay(tmp_path, history, answer, matches, replayed):
    trace = observer.Trace(tmp_path / "trace.jsonl")
    emit_first(trace)
    follow_up(trace, history=history, text=answer)
    captured = rows(trace)
    assert captured[2]["history_matches_parent"] is matches
    assert captured[3]["provider_replayed_pretool"] is replayed
    trace.close()


def test_same_call_id_in_other_turn_is_not_linked(tmp_path):
    trace = observer.Trace(tmp_path / "trace.jsonl")
    emit_first(trace)
    observation = trace.begin({"messages": [
        {"role": "user", "content": "user-private"}, tool_round("A complete reply!"),
        {"role": "tool", "tool_call_id": "unique-call", "content": "{}"},
        {"role": "user", "content": "user-private"},
    ]})
    assert observation.parent is None
    trace.close()


def test_sse_split_utf8_crlf_comments_done_and_tool_fragments(tmp_path):
    trace = observer.Trace(tmp_path / "trace.jsonl")
    observation = trace.begin({"messages": []})
    parser = observer.SSE(observation)
    payloads = [
        {"choices": [{"delta": {"content": "你好", "tool_calls": [{
            "index": 0, "id": "abc", "function": {"name": "sticker_", "arguments": '{"text":'},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "function": {"name": "send", "arguments": '"extra"}'},
        }]}, "finish_reason": "tool_calls"}]},
    ]
    raw = b": heartbeat\r\n\r\n"
    for payload in payloads:
        raw += b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\r\n\r\n"
    raw += b"data: [DONE]\r\n\r\n"
    for byte in raw:
        parser.feed(bytes([byte]))
    assert parser.done and observation.text == "你好" and observation.parse_errors == 0
    observation.end(complete=True, status=200)
    captured = rows(trace)[-1]
    assert captured["tools"][0]["name"] == "sticker_send"
    assert captured["tools"][0]["caption_chars"] == 5
    trace.close()


def test_partial_or_oversized_stream_cannot_claim_a_provider_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "MAX_TEXT", 5)
    trace = observer.Trace(tmp_path / "trace.jsonl")
    emit_first(trace, text="long text", call_id="unique-call")
    observation = trace.begin({"messages": [
        {"role": "user", "content": "user-private"}, tool_round("long text"),
        {"role": "tool", "tool_call_id": "unique-call", "content": "{}"},
    ]})
    observation.accept({"choices": [{"delta": {"content": "long text"}}]})
    observation.end(complete=False, status=200)
    assert rows(trace)[-1]["provider_replayed_pretool"] is None
    trace.close()


@pytest.mark.parametrize("base", [
    "http://remote.invalid/v1", "https://user:password@remote.invalid/v1",
    "https://remote.invalid/v1?key=secret", "https://remote.invalid/v1#fragment",
])
def test_proxy_rejects_credential_urls_and_insecure_remote_upstreams(base):
    with pytest.raises(ValueError):
        observer.validate_upstream(base)


@pytest.mark.parametrize("sse", [True, False])
@pytest.mark.skipif(
    hasattr(sys, "_sticker_isolation_root"), reason="isolation gate blocks all socket connections",
)
def test_http_observer_preserves_request_auth_and_response_bytes(tmp_path, sse):
    captured = []
    payload = {"choices": [{"delta" if sse else "message": {"content": "private answer"},
                            "finish_reason": "stop"}]}
    response_body = json.dumps(payload).encode()
    if sse:
        response_body = b"data: " + response_body + b"\n\ndata: [DONE]\n\n"

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            captured.append((self.path, raw, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
            self.end_headers()
            self.wfile.write(response_body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    trace = observer.Trace(tmp_path / "trace.jsonl")
    proxy = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        observer.make_handler(f"http://127.0.0.1:{upstream.server_port}/text/v1/chat/completions", trace),
    )
    workers = [threading.Thread(target=server.serve_forever) for server in (upstream, proxy)]
    for worker in workers:
        worker.start()
    client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
    raw = b'{"messages":[{"role":"user","content":"private user"}],"stream":true}'
    try:
        client.request("POST", "/v1/chat/completions", raw, headers={
            "Authorization": "Bearer private-api-key", "Content-Type": "application/json",
        })
        response = client.getresponse()
        assert response.status == 200 and response.read() == response_body
    finally:
        client.close()
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()
        for worker in workers:
            worker.join(timeout=5)
        trace.close()
    assert captured == [("/text/v1/chat/completions", raw, "Bearer private-api-key")]
    saved = trace.path.read_text()
    for private in ("private-api-key", "private answer", "private user"):
        assert private not in saved
    assert json.loads(saved.splitlines()[-1])["evidence_complete"] is True


def test_report_detects_replay_with_interposed_interjection_without_printing_content():
    text = "Thisisacompletesentence"
    assert reporter.repeat_count(text + text) == 2
    assert reporter.repeat_count(text + "Hmm" + text) == 1
    assert reporter.repeated_head_chars(text + "Hmm" + text) == len(text)
    assert reporter.repeated_head_chars(text) == 0


def test_trace_report_never_treats_unmatched_or_partial_stream_as_exoneration(tmp_path):
    trace = observer.Trace(tmp_path / "trace.jsonl")
    emit_first(trace)
    follow_up(trace)
    trace.close()
    report = reporter.trace_report(trace.path)
    assert report["confirmed_provider_replays"] == [2]
    assert report["complete_followups_without_pretool_replay"] == []


def test_http_failure_never_exonerates_provider_or_supplies_a_parent(tmp_path):
    trace = observer.Trace(tmp_path / "trace.jsonl")
    emit_first(trace)
    observation = trace.begin({"messages": [
        {"role": "user", "content": "user-private"}, tool_round("A complete reply!"),
        {"role": "tool", "tool_call_id": "unique-call", "content": "{}"},
    ]})
    observation.accept({"error": {"message": "private provider error"}})
    observation.end(complete=True, status=429)
    assert rows(trace)[-1]["provider_replayed_pretool"] is None
    assert "private provider error" not in trace.path.read_text()
    trace.close()
