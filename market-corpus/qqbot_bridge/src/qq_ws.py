"""Minimal RFC6455 WebSocket client (client-only, standard library only).

Used to connect to the official QQ 机器人 WebSocket gateway without any
third-party dependency (the N.E.K.O plugin runtime may not ship `websockets`).

Supports: HTTP upgrade handshake, masked client frames, unmasked server frames,
ping/pong, close, and a background receive loop. Fragmentation across multiple
frames is not supported (QQ gateway dispatches single text frames, which is fine
for our use case).
"""

from __future__ import annotations

import asyncio
import base64
import os
import ssl
import struct
from typing import Awaitable, Callable, Optional

__all__ = ["WebSocketClient", "WebSocketError"]

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(Exception):
    """Raised for handshake / protocol failures."""


# --- pure, testable frame helpers -------------------------------------------


def make_frame(opcode: int, data: bytes) -> bytes:
    """Build a single masked client frame (FIN + opcode)."""
    if isinstance(data, str):  # pragma: no cover - defensive
        data = data.encode("utf-8")
    length = len(data)
    header = bytearray()
    header.append(0x80 | (opcode & 0x0F))
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", length)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return bytes(header) + mask + masked


def parse_frame_header(header: bytes):
    """Parse the first 2 bytes (and any extended length bytes) of a frame.

    Returns (fin, opcode, masked, length, header_len). Raises ValueError on
    malformed headers (e.g. a client frame that is itself masked is ignored by
    the caller, but structurally this just reports the bits).
    """
    if len(header) < 2:
        raise ValueError("frame header too short")
    b0, b1 = header[0], header[1]
    fin = (b0 & 0x80) != 0
    opcode = b0 & 0x0F
    masked = (b1 & 0x80) != 0
    length = b1 & 0x7F
    off = 2
    if length == 126:
        if len(header) < off + 2:
            raise ValueError("truncated 16-bit length")
        length = struct.unpack("!H", header[off : off + 2])[0]
        off += 2
    elif length == 127:
        if len(header) < off + 8:
            raise ValueError("truncated 64-bit length")
        length = struct.unpack("!Q", header[off : off + 8])[0]
        off += 8
    return fin, opcode, masked, length, off


# --- client -----------------------------------------------------------------


class WebSocketClient:
    def __init__(
        self,
        uri: str,
        *,
        on_message: Optional[Callable[[str], Awaitable[None]]] = None,
        on_close: Optional[Callable[[], Awaitable[None]]] = None,
        logger=None,
    ):
        self.uri = uri
        self.on_message = on_message
        self.on_close = on_close
        self.logger = logger
        self._reader = None
        self._writer = None
        self._closed = False
        self._recv_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        use_ssl, hostport = self._split_uri(self.uri)
        host, port, path = self._split_hostport_path(hostport, use_ssl)
        ssl_ctx = ssl.create_default_context() if use_ssl else None
        self._reader, self._writer = await asyncio.open_connection(
            host, port, ssl=ssl_ctx
        )
        await self._do_handshake(host, port, path)
        self._recv_task = asyncio.create_task(self._recv_loop())

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _split_uri(uri: str):
        if uri.startswith("wss://"):
            return True, uri[len("wss://") :]
        if uri.startswith("ws://"):
            return False, uri[len("ws://") :]
        raise WebSocketError("uri must start with ws:// or wss://")

    @staticmethod
    def _split_hostport_path(hostport: str, use_ssl: bool):
        if "/" in hostport:
            host, path = hostport.split("/", 1)
            path = "/" + path
        else:
            host, path = hostport, "/"
        port = 443 if use_ssl else 80
        if ":" in host:
            host, port_s = host.split(":", 1)
            port = int(port_s)
        return host, port, path

    async def _do_handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Origin: https://{host}\r\n"
            f"\r\n"
        )
        assert self._writer is not None
        self._writer.write(req.encode("utf-8"))
        await self._writer.drain()
        status = await self._reader.readline()  # type: ignore[union-attr]
        if not status.startswith(b"HTTP/1.1 101"):
            raise WebSocketError(f"handshake failed: {status!r}")
        # consume remaining headers until empty line
        while True:
            line = await self._reader.readline()  # type: ignore[union-attr]
            if line in (b"\r\n", b"\n", b""):
                break

    async def _recv_loop(self) -> None:
        try:
            while not self._closed:
                frame = await self._read_frame()
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x1:  # text
                    text = payload.decode("utf-8", errors="replace")
                    if self.on_message is not None:
                        await self.on_message(text)
                elif opcode == 0x9:  # ping -> pong
                    await self._send_frame(0xA, payload)
                elif opcode == 0x8:  # close
                    await self.close()
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.warning("ws recv loop error: %s", exc)
        finally:
            await self._fire_close()

    async def _read_frame(self):
        assert self._reader is not None
        # peek at least 2 bytes for the header
        hdr = await self._reader.readexactly(2)
        try:
            fin, opcode, masked, length, off = parse_frame_header(hdr)
        except ValueError:
            return None
        # read any extra length bytes we did not peek
        if off > 2:
            hdr += await self._reader.readexactly(off - 2)
        if masked:
            mask = await self._reader.readexactly(4)
        else:
            mask = None
        payload = await self._reader.readexactly(length) if length else b""
        if mask is not None:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        _ = fin  # single-frame messages are assumed for QQ gateway
        return opcode, payload

    async def _send_frame(self, opcode: int, data: bytes) -> None:
        if self._writer is None or self._closed:
            raise WebSocketError("not connected")
        self._writer.write(make_frame(opcode, data))
        await self._writer.drain()

    async def send_text(self, text: str) -> None:
        await self._send_frame(0x1, text.encode("utf-8"))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._recv_task is not None:
            self._recv_task.cancel()
        try:
            if self._writer is not None:
                self._writer.write(make_frame(0x8, b""))
                await self._writer.drain()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._writer is not None:
                self._writer.close()
        except Exception:  # noqa: BLE001
            pass

    async def _fire_close(self) -> None:
        if self.on_close is not None:
            try:
                await self.on_close()
            except Exception:  # noqa: BLE001
                pass
