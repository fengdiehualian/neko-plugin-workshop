"""Lossless binary extension; protocol-1 PNG/JSON remains compatible."""
import base64
import io
import json
import struct
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest
from PIL import Image

from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
from plugin.plugins.hsr_companion.assistant_runtime import server


def pixels():
    im = Image.new("RGB", (2560, 1600), (17, 29, 41))
    im.putpixel((2445, 777), (255, 254, 253))
    return im


def test_fast_native_transport_is_pixel_exact_bmp(monkeypatch):
    im = pixels()
    monkeypatch.setattr(server, "assert_capturable_window", lambda w: None)
    monkeypatch.setattr(server, "capture_isolated_window", lambda w: (im, 123.0))
    data, mime, stamp = server._capture(SimpleNamespace(), codec="bmp")
    with Image.open(io.BytesIO(data)) as received:
        assert received.size == im.size and received.tobytes() == im.tobytes()
    assert mime == "image/bmp" and stamp == 123.0


def test_large_native_frame_keeps_existing_png_capacity_instead_of_exceeding_transport_limit(monkeypatch):
    im = Image.new("RGB", (3840, 2160), (17, 29, 41))
    monkeypatch.setattr(server, "assert_capturable_window", lambda w: None)
    monkeypatch.setattr(server, "capture_isolated_window", lambda w: (im, 123.0))
    data, mime, stamp = server._capture(SimpleNamespace(), codec="bmp")
    assert mime == "image/png" and len(data) < 16 * 1024 * 1024 and stamp == 123.0
    with Image.open(io.BytesIO(data)) as received:
        assert received.size == im.size and received.tobytes() == im.tobytes()


def test_explicitly_unsupported_binary_extension_falls_back_to_legacy_but_corruption_does_not(monkeypatch):
    import urllib.error
    client = VisionAssistantClient()
    client._binary_frames = True
    def unsupported(seq):
        raise urllib.error.HTTPError(client.base_url, 404, "not found", None, None)
    monkeypatch.setattr(client, "_get_binary_frame", unsupported)
    monkeypatch.setattr(client, "_get_json", lambda path: {
        "frame_seq": 1, "timestamp": time.time(), "mime": "image/png",
        "capture": {"state": "capturing", "isolated_window": True}, "game": {"running": True},
        "image_base64": base64.b64encode(b"legacy").decode(),
    })
    assert client.fetch_frame().image_bytes == b"legacy"
    assert client._binary_frames is False


def test_binary_and_legacy_routes_bind_same_capture_and_pixels():
    state = server.CaptureState()
    image = pixels()
    stream = io.BytesIO()
    image.save(stream, format="BMP")
    state.image, state.mime = stream.getvalue(), "image/bmp"
    state.frame_seq, state.timestamp, state.signature = 7, time.time(), "opaque-content-signature"
    state.capture = {"state": "capturing", "isolated_window": True}
    state.game = {"running": True, "minimized": False, "pid": 1}
    http = server.ThreadingHTTPServer(("127.0.0.1", 0), server._handler(state))
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    client = VisionAssistantClient(f"http://127.0.0.1:{http.server_port}", timeout=3)
    try:
        assert client.health()["connected"]
        assert client._binary_frames is True
        frame = client.fetch_frame()
        assert frame.frame_seq == 7 and frame.timestamp == state.timestamp and frame.mime == "image/bmp"
        with Image.open(io.BytesIO(frame.image_bytes)) as received:
            assert received.tobytes() == image.tobytes()
        with urllib.request.urlopen(client.base_url + "/api/frame?since_frame=0", timeout=3) as response:
            old = json.load(response)
        assert old["mime"] == "image/png" and old["timestamp"] == frame.timestamp
        with Image.open(io.BytesIO(base64.b64decode(old["image_base64"]))) as received:
            assert received.tobytes() == image.tobytes()
        assert client.fetch_frame(7) is None
    finally:
        http.shutdown()
        http.server_close()
        worker.join(2)


@pytest.mark.parametrize("packet", [b"", struct.pack("!I", 70000), struct.pack("!I", 2) + b"{}oops"])
def test_corrupt_binary_packet_is_not_silently_reinterpreted_as_a_valid_frame(monkeypatch, packet):
    module = __import__(VisionAssistantClient.__module__, fromlist=["urllib"])
    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/vnd.neko.hsr-frame"}
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **kw: Response(packet))
    client = VisionAssistantClient()
    client._binary_frames = True
    with pytest.raises(RuntimeError):
        client.fetch_frame()
