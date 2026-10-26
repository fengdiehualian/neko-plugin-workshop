"""Explicit diagnostic inference through the same public model adapter; no game I/O."""

import asyncio
import base64
import json
import struct
import zlib

from .neko_planner import NekoPlanner


def calibration_png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = (b"\0" + b"\xff\0\0" * 64) * 64
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
    return base64.b64encode(png + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")).decode()


async def check_model(gateway, mode):
    if mode not in {"text", "vision"}:
        raise ValueError("invalid_model_check")
    text = "Call report_probe with answer TEXT_OK. This is a diagnostic, not a game action."
    content = text if mode == "text" else [
        {"type": "text", "text": "This synthetic calibration image is not gameplay. Call report_probe with its dominant color: RED, GREEN, BLUE or UNKNOWN. Without an image use UNKNOWN."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + calibration_png()}},
    ]
    tools = [{"type": "function", "function": {"name": "report_probe", "description": "Return a diagnostic answer; never dispatched.",
        "parameters": {"type": "object", "properties": {"answer": {"enum": ["TEXT_OK", "RED", "GREEN", "BLUE", "UNKNOWN"]}},
                       "required": ["answer"], "additionalProperties": False}}}]
    planner = NekoPlanner(gateway, 512)
    try:
        result = await asyncio.wait_for(planner.decide([{"role": "user", "content": content}], tools), timeout=25)
        calls = result["tool_calls"]
        expected = "TEXT_OK" if mode == "text" else "RED"
        confirmed = (len(calls) == 1 and calls[0]["function"]["name"] == "report_probe"
                     and json.loads(calls[0]["function"]["arguments"]).get("answer") == expected)
        status = result["model_status"]
        return {"mode": mode, "model_status": status, "structured_probe_passed": confirmed,
                "vision_probe_passed": confirmed and status["route"] == "vision" if mode == "vision" else None,
                "synthetic_image": mode == "vision", "game_action_dispatched": False}
    except (TimeoutError, ValueError, TypeError, KeyError, AttributeError):
        return {"mode": mode, "structured_probe_passed": False, "reason": "probe_timeout_or_invalid_response", "game_action_dispatched": False}
    finally:
        await planner.aclose()
