"""mqtt.py 协议帧与去重逻辑单元测试（纯本地，不依赖外网）。"""

import json
import socket
import struct
import threading

from plugin.plugins.neko_community_chat import mqtt as mqtt_mod
from plugin.plugins.neko_community_chat.node import Node


def test_varint_roundtrip():
    for n in (0, 1, 127, 128, 16383, 16384, 2 ** 24 - 1):
        b = mqtt_mod._varint(n)
        # 用 socket 对 _read_varint 做字节流验证
        rsock, wsock = socket.socketpair()
        wsock.sendall(b)
        got = mqtt_mod._read_varint(rsock)
        assert got == n
        rsock.close()
        wsock.close()


def test_encode_str():
    b = mqtt_mod._encode_str("MQTT")
    assert b == b"\x00\x04MQTT"


def test_build_packet():
    pkt = mqtt_mod._build_packet(0x10, b"\x00")
    assert pkt == b"\x10\x01\x00"


def test_handle_publish_dispatch():
    got = {}
    link = mqtt_mod.MQTTLink(
        host="localhost", port=1883, channel="t", peer_id="node-A",
        peer_name="A", on_message=lambda d: got.update(d),
    )
    body = mqtt_mod._encode_str("neko_cc/t/broadcast") + json.dumps(
        {"peer_id": "node-B", "peer_name": "B", "message": {"text": "hi", "origin": "x"}}
    ).encode("utf-8")
    link._handle_publish(0x30, body)  # QoS0
    assert got["peer_id"] == "node-B"
    assert got["message"]["text"] == "hi"


def test_handle_publish_qos1_acks():
    sent = []
    link = mqtt_mod.MQTTLink(
        host="localhost", port=1883, channel="t", peer_id="node-A",
        peer_name="A", on_message=lambda d: None,
    )
    body = (mqtt_mod._encode_str("neko_cc/t/broadcast")
            + struct.pack(">H", 7)
            + json.dumps({"peer_id": "node-B", "message": {"text": "q1"}}).encode("utf-8"))

    class FakeSock:
        def sendall(self, data):
            sent.append(data)

    link._handle_publish(0x33, body, FakeSock())  # QoS1 → 应回 PUBACK(0x40, id=7)
    assert any(b[:1] == b"\x40" and b"\x00\x07" in b for b in sent)


def test_handle_publish_self_loop():
    got = []
    link = mqtt_mod.MQTTLink(
        host="localhost", port=1883, channel="t", peer_id="node-A",
        peer_name="A", on_message=got.append,
    )
    body = mqtt_mod._encode_str("neko_cc/t/broadcast") + json.dumps(
        {"peer_id": "node-A", "message": {"text": "loop"}}
    ).encode("utf-8")
    link._handle_publish(0x30, body)
    assert got == []


def test_handle_publish_bad_json():
    got = []
    link = mqtt_mod.MQTTLink(
        host="localhost", port=1883, channel="t", peer_id="node-A",
        peer_name="A", on_message=got.append,
    )
    body = mqtt_mod._encode_str("neko_cc/t/broadcast") + b"{not-json"
    link._handle_publish(0x30, body)
    assert got == []


def test_publish_topic_routing():
    """广播/定向主题正确性（通过伪造 socket 的 _send_publish）。"""
    link = mqtt_mod.MQTTLink(
        host="localhost", port=1883, channel="myroom", peer_id="node-A",
        peer_name="A", on_message=lambda d: None,
    )
    sent = []

    class FakeSock:
        def sendall(self, data):
            sent.append(data)

    link._send_publish(FakeSock(), {"message": {"text": "hi"}}, target=None)
    link._send_publish(FakeSock(), {"message": {"text": "dm"}}, target="node-B")
    assert b"neko_cc/myroom/broadcast" in sent[0]
    assert b"neko_cc/myroom/peer/node-B" in sent[1]


def test_dedup_seen():
    n = object.__new__(Node)
    n._mqtt_dedup = []
    n._mqtt_lock = threading.Lock()
    m1 = {"peer_id": "node-B", "message": {"text": "hello", "ts": 100}}
    assert n._mqtt_dedup_seen(m1) is True
    assert n._mqtt_dedup_seen(m1) is False  # 重复丢弃
    m2 = {"peer_id": "node-B", "message": {"text": "hello", "ts": 101}}
    assert n._mqtt_dedup_seen(m2) is True  # 时间戳不同 → 放行


def test_hosts_list_dedup():
    """fallback_hosts 与主 host 去重、顺序正确。"""
    link = mqtt_mod.MQTTLink(
        host="h1", port=1883, channel="t", peer_id="node-A", peer_name="A",
        on_message=lambda d: None,
        fallback_hosts=["h2", "h1", "h3", "h2"],
    )
    assert link._hosts == ["h1", "h2", "h3"]
    link2 = mqtt_mod.MQTTLink(
        host="h1", port=1883, channel="t", peer_id="node-A", peer_name="A",
        on_message=lambda d: None,
        fallback_hosts=["h2"],
    )
    assert link2._hosts == ["h1", "h2"]


def test_connect_fallback_to_local_broker():
    """主 host 不可达（连接拒绝，不依赖外部 DNS）时自动回退到可达的本地 mock broker，并更新 host。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(2)
    connected_flag = threading.Event()

    def broker_loop():
        try:
            while not connected_flag.is_set():
                try:
                    srv.settimeout(3.0)
                    conn, _ = srv.accept()
                except socket.timeout:
                    break
                except OSError:
                    break
                try:
                    with conn:
                        conn.settimeout(3.0)
                        header = conn.recv(1)
                        if not header or header[0] >> 4 != 0x01:  # CONNECT
                            continue
                        conn.recv(256)
                        conn.sendall(b"\x20\x02\x00\x00")  # CONNACK accepted
                        for _ in range(2):
                            data = conn.recv(256)
                            if not data:
                                break
                            conn.sendall(b"\x90\x03\x00\x01\x00")  # SUBACK QoS1 granted
                        connected_flag.set()
                except OSError:
                    pass
        finally:
            srv.close()

    t = threading.Thread(target=broker_loop, daemon=True)
    t.start()
    link = mqtt_mod.MQTTLink(
        host="127.0.0.2", port=port, channel="t",
        peer_id="node-A", peer_name="A", on_message=lambda d: None,
        fallback_hosts=["127.0.0.1"],
    )
    thread = threading.Thread(target=link._connect_and_loop, daemon=True)
    thread.start()
    try:
        assert connected_flag.wait(5), "fallback broker not reached"
        assert link.connected.wait(2), "link not online after connect"
        assert link.online
        assert link.host == "127.0.0.1"  # 已切到实际连上的 broker
    finally:
        link.stop()
        srv.close()
