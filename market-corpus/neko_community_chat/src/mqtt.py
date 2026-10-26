"""neko_community_chat 公网通道：最小 MQTT 3.1.1 客户端（纯标准库，零依赖）。

公共 MQTT broker（如 broker.hivemq.com:1883）免费开放，节点订阅统一主题即可
全球互通，无需部署任何服务器/账号。消息为明文，适合公开聊天房间。
多 broker 自动回退：主 host 不可达时按 MQTT_DEFAULT_HOSTS 依次尝试，
连接成功后 status() 的 host 会显示实际连上的 broker。

协议（QoS 0，够用）：
  broadcast topic: neko_cc/{channel}/broadcast
  directed  topic: neko_cc/{channel}/peer/{peer_id}
  payload: JSON {"peer_id","peer_name","target_peer"?, "message":{...}}

断线自动重连（指数退避 2s→30s），30s 心跳保活。
"""

from __future__ import annotations

import json
import logging
import random
import socket
import struct
import threading
import time

logger = logging.getLogger(__name__)

MQTT_TCP_PORT = 1883
KEEPALIVE = 30
PING_INTERVAL = KEEPALIVE / 2
RECONNECT_MIN = 2.0
RECONNECT_MAX = 30.0
SOCK_TIMEOUT = 8.0
PUB_ACK_TIMEOUT = 6.0
MAX_PAYLOAD = 8 * 1024 * 1024
TOPIC_PREFIX = "neko_cc"

# 公共 broker 回退列表（实测可达优先）：主 host 连接失败时依次尝试。
# broker.emqx.io 国际公共实例国内经常不可达，故排在回退位。
MQTT_DEFAULT_HOSTS = ("broker.hivemq.com", "broker.mqttdashboard.com", "broker.emqx.io")


def _encode_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        d = n % 128
        n //= 128
        if n:
            out.append(d | 0x80)
        else:
            out.append(d)
            return bytes(out)


def _build_packet(header: int, body: bytes) -> bytes:
    return bytes([header]) + _varint(len(body)) + body


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def _read_varint(sock: socket.socket) -> int:
    mult, value, count = 1, 0, 0
    while True:
        b = _read_exact(sock, 1)[0]
        value += (b & 0x7F) * mult
        mult *= 128
        count += 1
        if count > 4:
            raise ConnectionError("bad remaining length")
        if not (b & 0x80):
            return value


class MQTTLink(threading.Thread):
    """单向长连接：订阅广播/定向主题，收到消息回调 on_message(peer_payload: dict)。

    publish(payload, target_peer=None) 线程安全；断线自动重连。
    """

    def __init__(
        self,
        host: str,
        peer_id: str,
        peer_name: str,
        channel: str,
        on_message,
        port: int = MQTT_TCP_PORT,
        client_id: str | None = None,
        fallback_hosts: list[str] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="neko-cc-mqtt")
        self.host = host
        self.port = int(port)
        self.channel = channel.strip() or "neko-universe"
        self.peer_id = peer_id
        self.peer_name = (peer_name or "").strip()[:40]
        self.on_message = on_message
        # 固定 client_id + 持久会话（clean_session=0）：离线消息由 broker 缓存，
        # 重连后送达；QoS1 带确认，发送方重试。
        self.client_id = client_id or f"neko-{peer_id[-32:]}"
        self._hosts: list[str] = [host]
        for h in fallback_hosts or ():
            if h and h != host and h not in self._hosts:
                self._hosts.append(h)
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()  # 保护 sock（publish 与读循环）
        self._stop = threading.Event()
        self._last_ping = 0.0
        self._pending_pub: list[tuple[dict, str | None]] = []
        self._awaiting_ack: dict[int, threading.Event] = {}  # pkt_id -> 等待 PUBACK 的事件
        self._next_pkt_id = 1
        self.connected = threading.Event()
        self.online = False  # 最近一次连接是否成功（供面板显示）
        self.error = ""

    # ── 对外接口 ───────────────────────────────────────────────

    def publish(self, payload: dict, target_peer: str | None = None) -> bool:
        """广播（target=None）或定向投递。QoS1：发 PUBLISH 后由读循环收 PUBACK，
        事件等待超时重发一次；断线入队补发。socket 仅读循环一个读者，无竞争。"""
        with self._lock:
            if self._sock is None or not self.connected.is_set():
                self._pending_pub.append((payload, target_peer))
                return False
            ok = self._send_publish_waited(self._sock, payload, target_peer)
            if not ok:
                self._pending_pub.append((payload, target_peer))
            return ok

    def _send_publish_waited(self, sock: socket.socket, payload: dict, target_peer: str | None) -> bool:
        """QoS1：发 PUBLISH 建事件等待 PUBACK（超时重发一次）。须持 _lock 调用。"""
        topic = self._topic_for(target_peer)
        body = (
            _encode_str(topic)
            + struct.pack(">H", self._next_pkt_id)
            + json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        pkt = _build_packet(0x32, body)
        pkt_id = self._next_pkt_id
        self._next_pkt_id = self._next_pkt_id % 0xFFFF + 1
        for _ in range(2):
            ev = threading.Event()
            self._awaiting_ack[pkt_id] = ev
            try:
                sock.sendall(pkt)
            except (OSError, ConnectionError):
                self._awaiting_ack.pop(pkt_id, None)
                self.connected.clear()
                return False
            if ev.wait(PUB_ACK_TIMEOUT):
                self._awaiting_ack.pop(pkt_id, None)
                return True
            self._awaiting_ack.pop(pkt_id, None)
        return False

    def stop(self) -> None:
        self._stop.set()
        self.connected.clear()
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    # ── 主循环 ─────────────────────────────────────────────────

    def run(self) -> None:
        backoff = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                self._connect_and_loop()
                backoff = RECONNECT_MIN
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)[:160]
                self.online = False
                self.connected.clear()
                logger.info("mqtt %s:%s link error: %s", self.host, self.port, exc)
                self._close_sock()
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 1.6, RECONNECT_MAX)

    def _connect_and_loop(self) -> None:
        sock: socket.socket | None = None
        last_err: Exception | None = None
        for host in self._hosts:
            try:
                sock = socket.create_connection((host, self.port), timeout=SOCK_TIMEOUT)
                sock.settimeout(SOCK_TIMEOUT)
                self._send_connect(sock)
                self._recv_connack(sock)
                topics = self._topics()
                for t in topics:
                    self._send_subscribe(sock, t)
                    self._recv_suback(sock)
                break
            except Exception as exc:  # noqa: BLE001 —— 该 broker 不可达，试下一个
                last_err = exc
                self._close_raw(sock)
                sock = None
                logger.info("mqtt broker %s:%s unreachable: %s", host, self.port, exc)
        if sock is None:
            raise ConnectionError(f"all brokers unreachable: {last_err}")
        self.host = host  # 更新为实际连上的 broker（状态显示用）
        with self._lock:
            self._sock = sock
        self.online = True
        self.error = ""
        self.connected.set()
        self._last_ping = time.time()
        logger.info("mqtt linked: %s:%s channel=%s peer=%s", self.host, self.port, self.channel, self.peer_id)
        with self._lock:
            pending, self._pending_pub = self._pending_pub, []
        for payload, target in pending:
            try:
                self._send_publish_waited(sock, payload, target)
            except (OSError, ConnectionError):
                break
        self._read_loop(sock)

    @staticmethod
    def _close_raw(sock: socket.socket | None) -> None:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _read_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                header = _read_exact(sock, 1)[0]
            except ConnectionError:
                raise
            except OSError as exc:
                raise ConnectionError(str(exc)) from exc
            ptype = header >> 4
            length = _read_varint(sock)
            if length:
                body = _read_exact(sock, length)
            else:
                body = b""
            if ptype == 0x03:  # PUBLISH
                self._handle_publish(header, body, sock)
            elif ptype == 0x04:  # PUBACK：唤醒等待方
                if len(body) >= 2:
                    acked = struct.unpack(">H", body[0:2])[0]
                    ev = self._awaiting_ack.pop(acked, None)
                    if ev is not None:
                        ev.set()
            elif ptype == 0x0D:  # PINGRESP
                self._last_ping = time.time()
            # SUBACK(0x09)/其他：忽略
            now = time.time()
            if now - self._last_ping >= PING_INTERVAL:
                self._send_pingreq(sock)
                self._last_ping = now

    # ── 收包 ───────────────────────────────────────────────────

    def _handle_publish(self, header: int, body: bytes, sock: socket.socket | None = None) -> None:
        qos = (header >> 1) & 0x03
        pos, topic_len = 0, struct.unpack(">H", body[0:2])[0]
        pos += 2
        # topic（QoS 无需区分，仅推进游标）
        pos += topic_len
        pkt_id = None
        if qos > 0:
            pkt_id = struct.unpack(">H", body[pos:pos + 2])[0]
            pos += 2
        payload = body[pos:]
        if pkt_id is not None and sock is not None:
            try:
                sock.sendall(_build_packet(0x40, struct.pack(">H", pkt_id)))  # PUBACK
            except (OSError, ConnectionError):
                pass
        if not payload:
            return
        try:
            data = json.loads(payload.decode("utf-8", errors="replace"))
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if data.get("peer_id") == self.peer_id:
            return  # 自己的消息回环
        try:
            self.on_message(data)
        except Exception:  # noqa: BLE001
            logger.debug("mqtt incoming failed", exc_info=True)

    # ── 发包 ───────────────────────────────────────────────────

    def _topic_for(self, target: str | None) -> str:
        return (f"{TOPIC_PREFIX}/{self.channel}/peer/{target}" if target
                else f"{TOPIC_PREFIX}/{self.channel}/broadcast")

    def _send_publish(self, sock: socket.socket, payload: dict, target: str | None) -> None:
        """QoS0 发布（重连补发用，不等待确认）。"""
        body = _encode_str(self._topic_for(target)) + json.dumps(payload, ensure_ascii=False).encode("utf-8")
        sock.sendall(_build_packet(0x30, body))

    def _send_pingreq(self, sock: socket.socket) -> None:
        sock.sendall(bytes([0xC0, 0x00]))

    def _send_connect(self, sock: socket.socket) -> None:
        flags = 0x00  # clean_session=0：持久会话（离线消息由 broker 缓存，重连送达）
        body = (
            _encode_str("MQTT") + bytes([0x04, flags])
            + struct.pack(">H", KEEPALIVE)
            + _encode_str(self.client_id)
        )
        sock.sendall(_build_packet(0x10, body))

    def _recv_connack(self, sock: socket.socket) -> None:
        header = _read_exact(sock, 1)[0]
        if header >> 4 != 0x02:
            raise ConnectionError(f"expected CONNACK, got {header:#x}")
        _read_varint(sock)
        body = _read_exact(sock, 2)
        if body[1] != 0:
            raise ConnectionError(f"CONNACK refused: code={body[1]}")

    def _send_subscribe(self, sock: socket.socket, topic: str) -> None:
        pkt_id = random.randrange(1, 0xFFFF)
        body = struct.pack(">H", pkt_id) + _encode_str(topic) + bytes([0x01])  # 请求 QoS1
        sock.sendall(_build_packet(0x82, body))

    def _recv_suback(self, sock: socket.socket) -> None:
        header = _read_exact(sock, 1)[0]
        if header >> 4 != 0x09:
            raise ConnectionError(f"expected SUBACK, got {header:#x}")
        length = _read_varint(sock)
        if length:
            _read_exact(sock, length)

    def _close_sock(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    # ── 主题 ───────────────────────────────────────────────────

    def _topics(self) -> list[str]:
        return [
            f"{TOPIC_PREFIX}/{self.channel}/broadcast",
            f"{TOPIC_PREFIX}/{self.channel}/peer/{self.peer_id}",
        ]

    def status(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "channel": self.channel,
            "online": self.connected.is_set(),
            "error": self.error,
        }
