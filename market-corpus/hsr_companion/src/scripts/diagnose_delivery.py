"""Observe one explicit HSR test cue on the local message-plane PUB.

No messages are consumed from queues and no other plugin payloads are printed.
The optional --probe sends exactly one normal hosted-UI connection test.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

import zmq


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="tcp://127.0.0.1:38866")
    parser.add_argument("--port", type=int, default=48916)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if not args.endpoint.startswith("tcp://127.0.0.1:") or not 1024 <= args.port <= 65535:
        raise ValueError("diagnostics only support local endpoints")
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt_string(zmq.SUBSCRIBE, "messages.")
    socket.connect(args.endpoint)
    try:
        time.sleep(0.3)
        if args.probe:
            request = urllib.request.Request(
                f"http://127.0.0.1:{args.port}/plugin/hsr_companion/hosted-ui/action/test_proactive_message",
                data=json.dumps({"args": {}, "kind": "panel", "surface_id": "demo"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.load(response).get("result") or {}
            print(json.dumps({"submitted": (result.get("proactive_companionship") or {}).get("submitted")}, ensure_ascii=True))
        deadline = time.monotonic() + 8
        found = False
        while time.monotonic() < deadline:
            if not socket.poll(500):
                continue
            frames = socket.recv_multipart()
            if len(frames) < 2:
                continue
            event = json.loads(frames[1])
            payload = event.get("payload") or event.get("data") or event
            if payload.get("plugin_id") != "hsr_companion":
                continue
            found = True
            print(json.dumps({
                "topic": frames[0].decode(), "envelope_keys": list(event),
                "payload_keys": list(payload), "ai_behavior": payload.get("ai_behavior"),
                "part_count": len(payload.get("parts") or []),
                "target": payload.get("target_lanlan"),
                "kind": (payload.get("metadata") or {}).get("kind"),
            }, ensure_ascii=True))
        print(json.dumps({"hsr_publication_observed": found}))
    finally:
        socket.close()
        context.term()


if __name__ == "__main__":
    main()
