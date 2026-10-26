"""neko_community_chat 节点互联层（v0.3.0）。

三通道互联：
- LAN：UDP 广播（7872）发现同网段 peer，HTTP 直推 7870/api/relay/inbox
- 公网（中继）：HTTP 轮询中继（Cloudflare Worker，relay_url 可配），register/send/poll
- 公网（MQTT）：公共 broker 零配置全球互通（mqtt_enabled 默认开，broker.emqx.io:1883）

纯标准库实现（socket/threading/urllib），零第三方依赖。
"""

from __future__ import annotations

import json
import logging
import os
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

from . import mqtt as mqtt_mod

logger = logging.getLogger(__name__)

LAN_PORT = 7872
LAN_HELLO_PREFIX = "neko-cc-v1"
PEER_TTL = 30.0          # 2 轮广播未到即移除
LAN_PUSH_RETRY = 2
RELAY_POLL_INTERVAL = 3.0
RELAY_HTTP_TIMEOUT = 5.0    # 中继请求超时（秒）
RELAY_BACKOFF_AFTER = 3     # 连续失败 N 次后放慢轮询
RELAY_BACKOFF_MULT = 3      # 放慢倍数
RELAY_URL_ENV = "NEKO_COMMUNITY_RELAY"
# 内置默认中继：所有安装者开箱即用（零配置）。为保护隐私，此地址不落盘、
# 不出现在任何界面与接口返回中；面板仅显示"内置默认"状态。
DEFAULT_RELAY_URL = "https://neko-community-relay.1691421357.workers.dev"
PORT_ENV = "NEKO_COMMUNITY_CHAT_PORT"
DEFAULT_PORT_HTTP = 7870
MAX_TEXT_LEN = 2000
RATE_LIMIT_PER_PEER = (10.0, 60)  # 10 秒窗口最多 60 条

MQTT_DEFAULT_HOST = "broker.hivemq.com"  # 实测国内可达；不可达时自动回退 mqtt_mod.MQTT_DEFAULT_HOSTS
MQTT_DEFAULT_CHANNEL = "neko-universe"
MQTT_ENV_ENABLED = "NEKO_COMMUNITY_MQTT"  # "0" 关闭，空/其他视为跟随设置
MQTT_DEDUP_WINDOW = 300
MQTT_DEDUP_MAX = 200


def _parse_hello(payload: str) -> dict | None:
    """解析 UDP hello 报文。"""
    parts = payload.split("|")
    if len(parts) < 4 or parts[0] != LAN_HELLO_PREFIX:
        return None
    try:
        return {
            "peer_id": parts[1],
            "name": parts[2][:40],
            "port": int(parts[3]),
            "ts": parts[4] if len(parts) > 4 else "",
        }
    except (ValueError, IndexError):
        return None


class Node:
    """节点：peer 身份 + LAN 发现/互推 + 公网中继轮询。

    on_incoming: Callable[[dict], None]  —— 收到远端消息时回调（server 侧负责
    落库 + inbox 唤醒），返回后不再转发（防环）。
    on_settings: 面板设置写回（昵称/中继URL）后由外部调用 node.refresh()。
    """

    def __init__(self, data_dir: str, on_incoming, http_port: int | None = None) -> None:
        self.data_dir = data_dir
        self.on_incoming = on_incoming
        self.peer_file = os.path.join(data_dir, "peer.json")
        self.settings_file = os.path.join(data_dir, "settings.json")
        try:
            self.http_port = int(http_port or os.environ.get(PORT_ENV) or DEFAULT_PORT_HTTP)
        except (TypeError, ValueError):
            self.http_port = DEFAULT_PORT_HTTP

        ident = self._load_identity()
        self.peer_id: str = ident["peer_id"]
        self.name: str = ident["name"]
        self.relay_url: str = self._load_relay_url()
        self.mqtt_cfg: dict = self._load_mqtt_cfg()

        self.peers: dict[str, dict] = {}  # peer_id -> {ip, port, name, last_seen}
        self._peer_lock = threading.Lock()
        self._rate: dict[str, list[float]] = {}
        self._relay_since = 0
        self._relay_state: dict = {"ok": None, "err": "", "last": None}
        self._warp_attempt: dict = {"state": "idle", "err": "", "last": 0}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._mqtt: mqtt_mod.MQTTLink | None = None
        self._mqtt_lock = threading.Lock()
        self._mqtt_dedup: list[tuple[str, float]] = []

    # ── 身份与配置 ──────────────────────────────────────────────

    def _load_identity(self) -> dict:
        try:
            with open(self.peer_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("peer_id") and d.get("name"):
                return d
        except (OSError, ValueError):
            pass
        host = socket.gethostname() or "pc"
        ident = {
            "peer_id": f"node-{host[:16]}-{random.randrange(16 ** 8):08x}",
            "name": host[:40],
        }
        try:
            with open(self.peer_file, "w", encoding="utf-8") as f:
                json.dump(ident, f, ensure_ascii=False)
        except OSError:
            pass
        return ident

    def _load_relay_url(self) -> str:
        url = os.environ.get(RELAY_URL_ENV, "").strip()
        if url:
            return url.rstrip("/")
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                url = str(json.load(f).get("relay_url") or "").strip().rstrip("/")
        except (OSError, ValueError):
            url = ""
        return url or DEFAULT_RELAY_URL

    def _load_mqtt_cfg(self) -> dict:
        env_off = os.environ.get(MQTT_ENV_ENABLED, "").strip() == "0"
        cfg = {"enabled": not env_off, "host": MQTT_DEFAULT_HOST, "port": mqtt_mod.MQTT_TCP_PORT,
               "channel": MQTT_DEFAULT_CHANNEL}
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if "mqtt_enabled" in d:
            cfg["enabled"] = bool(d["mqtt_enabled"])
        if d.get("mqtt_host"):
            cfg["host"] = str(d["mqtt_host"]).strip()[:120]
        if d.get("mqtt_port"):
            try:
                cfg["port"] = int(d["mqtt_port"])
            except (TypeError, ValueError):
                pass
        if d.get("mqtt_channel"):
            cfg["channel"] = str(d["mqtt_channel"]).strip()[:64]
        return cfg

    def refresh(self) -> None:
        """设置变更后重载：昵称/中继 URL/MQTT 生效（线程安全：只改下次使用）。"""
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        name = str(d.get("name") or "").strip()[:40]
        if name:
            self.name = name
            ident = {"peer_id": self.peer_id, "name": name}
            try:
                with open(self.peer_file, "w", encoding="utf-8") as f:
                    json.dump(ident, f, ensure_ascii=False)
            except OSError:
                pass
        self.relay_url = str(d.get("relay_url") or "").strip().rstrip("/") or DEFAULT_RELAY_URL
        self._apply_mqtt_cfg(d)

    def _apply_mqtt_cfg(self, d: dict) -> None:
        """MQTT 配置变更时重建链接（保留未变配置）。"""
        cfg = dict(self.mqtt_cfg)
        if "mqtt_enabled" in d:
            cfg["enabled"] = bool(d["mqtt_enabled"])
        if d.get("mqtt_host"):
            cfg["host"] = str(d["mqtt_host"]).strip()[:120]
        if d.get("mqtt_port"):
            try:
                cfg["port"] = int(d["mqtt_port"])
            except (TypeError, ValueError):
                pass
        if d.get("mqtt_channel"):
            cfg["channel"] = str(d["mqtt_channel"]).strip()[:64]
        changed = cfg != self.mqtt_cfg
        self.mqtt_cfg = cfg
        if changed and self._mqtt is not None:
            self._mqtt.stop()
            self._mqtt = None
            self._start_mqtt_if_enabled()

    # ── 生命周期 ────────────────────────────────────────────────

    def start(self) -> None:
        if self._threads:
            return
        lan = threading.Thread(target=self._lan_loop, daemon=True, name="neko-cc-lan")
        relay = threading.Thread(target=self._relay_loop, daemon=True, name="neko-cc-relay")
        self._threads = [lan, relay]
        lan.start()
        relay.start()
        self._start_mqtt_if_enabled()
        logger.info("node %s (%s) started, relay=%s, mqtt=%s",
                    self.peer_id, self.name,
                    "default" if self.relay_url == DEFAULT_RELAY_URL else ("custom" if self.relay_url else "-"),
                    self.mqtt_cfg)

    def _start_mqtt_if_enabled(self) -> None:
        if not self.mqtt_cfg.get("enabled"):
            return
        with self._mqtt_lock:
            if self._mqtt is not None:
                return
            self._mqtt = mqtt_mod.MQTTLink(
                host=self.mqtt_cfg["host"],
                port=self.mqtt_cfg["port"],
                channel=self.mqtt_cfg["channel"],
                peer_id=self.peer_id,
                peer_name=self.name,
                on_message=self._on_mqtt_message,
                fallback_hosts=list(mqtt_mod.MQTT_DEFAULT_HOSTS),
            )
            self._mqtt.start()

    def stop(self) -> None:
        self._stop.set()
        with self._mqtt_lock:
            if self._mqtt is not None:
                self._mqtt.stop()

    def _on_mqtt_message(self, data: dict) -> None:
        """收到 MQTT 消息：去重后走统一远端入口。"""
        if not self._mqtt_dedup_seen(data):
            return
        m = data.get("message") or {}
        if isinstance(m, dict):
            m["origin"] = "mqtt"
            m.setdefault("peer_id", data.get("peer_id"))
            m.setdefault("peer_name", data.get("peer_name"))
            if data.get("target_peer") and data["target_peer"] != self.peer_id:
                return
            try:
                self.on_incoming(m)
            except Exception:  # noqa: BLE001
                logger.debug("mqtt incoming failed", exc_info=True)

    def _mqtt_dedup_seen(self, data: dict) -> bool:
        m = data.get("message") or {}
        if not isinstance(m, dict):
            return False
        text = str(m.get("text") or "")[:64]
        if not text:
            return False
        fingerprint = f"{data.get('peer_id')}|{text}|{m.get('ts') or ''}"
        now = time.time()
        with self._mqtt_lock:
            self._mqtt_dedup = [(f, t) for f, t in self._mqtt_dedup if now - t < MQTT_DEDUP_WINDOW]
            if any(f == fingerprint for f, _ in self._mqtt_dedup):
                return False
            self._mqtt_dedup.append((fingerprint, now))
            if len(self._mqtt_dedup) > MQTT_DEDUP_MAX:
                self._mqtt_dedup = self._mqtt_dedup[-MQTT_DEDUP_MAX:]
        return True

    # ── 对外消息分发（本机产生的消息 → 全网络）────────────────────

    def dispatch(self, msg: dict) -> None:
        """本机产生的消息广播到所有 peer（LAN 直推 + 中继广播 + MQTT 广播）。"""
        m = dict(msg)
        m.setdefault("peer_id", self.peer_id)
        m.setdefault("peer_name", self.name)
        m.setdefault("ts", int(time.time()))
        peers = self._snapshot_peers()
        for peer in peers:
            self._lan_push(peer, m)
        if self.relay_url:
            self._relay_send(m, target=None)
        self._mqtt_broadcast(m)

    def send_to(self, target_peer: str, msg: dict) -> bool:
        """定向投递给指定 peer（YUI 回复远端用户用）。"""
        m = dict(msg)
        m.setdefault("peer_id", self.peer_id)
        m.setdefault("peer_name", self.name)
        m.setdefault("ts", int(time.time()))
        m["target_peer"] = target_peer
        peer = self._get_peer(target_peer)
        if peer is not None:
            return self._lan_push(peer, m)
        if self.relay_url:
            return self._relay_send(m, target=target_peer)
        return self._mqtt_send_to(target_peer, m)

    # ── MQTT 通道 ──────────────────────────────────────────────

    def _mqtt_broadcast(self, msg: dict) -> bool:
        with self._mqtt_lock:
            link = self._mqtt
        if link is None:
            return False
        payload = {
            "peer_id": self.peer_id,
            "peer_name": self.name,
            "message": msg,
        }
        return link.publish(payload, target_peer=None)

    def _mqtt_send_to(self, target_peer: str, msg: dict) -> bool:
        with self._mqtt_lock:
            link = self._mqtt
        if link is None:
            return False
        payload = {
            "peer_id": self.peer_id,
            "peer_name": self.name,
            "target_peer": target_peer,
            "message": msg,
        }
        return link.publish(payload, target_peer=target_peer)

    def mqtt_status(self) -> dict:
        with self._mqtt_lock:
            link = self._mqtt
        if link is None:
            return {"enabled": bool(self.mqtt_cfg.get("enabled")), "online": False}
        out = link.status()
        out["enabled"] = bool(self.mqtt_cfg.get("enabled"))
        return out

    # ── LAN 通道 ────────────────────────────────────────────────

    def _lan_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", LAN_PORT))
        except OSError as exc:
            logger.warning("LAN bind failed: %s", exc)
            return
        sock.settimeout(1.0)
        last_announce = 0.0
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(512)
                hello = _parse_hello(data.decode("utf-8", errors="replace"))
                if hello and hello["peer_id"] != self.peer_id:
                    self._upsert_peer(hello, ip=addr[0])
            except socket.timeout:
                pass
            except OSError:
                break
            now = time.time()
            if now - last_announce >= 5.0:
                last_announce = now
                try:
                    hello = f"{LAN_HELLO_PREFIX}|{self.peer_id}|{self.name}|{self.http_port}|{int(now)}"
                    sock.sendto(hello.encode("utf-8"), ("255.255.255.255", LAN_PORT))
                except OSError:
                    pass
            self._sweep_peers(now)
        sock.close()

    def _upsert_peer(self, hello: dict, ip: str) -> None:
        with self._peer_lock:
            self.peers[hello["peer_id"]] = {
                "ip": ip,
                "port": hello["port"],
                "name": hello["name"],
                "last_seen": time.time(),
            }

    def _sweep_peers(self, now: float) -> None:
        stale = [pid for pid, p in self.peers.items() if now - p["last_seen"] > PEER_TTL]
        if stale:
            with self._peer_lock:
                for pid in stale:
                    self.peers.pop(pid, None)

    def _snapshot_peers(self) -> list[dict]:
        with self._peer_lock:
            return [dict(p) for p in self.peers.values()]

    def _get_peer(self, peer_id: str) -> dict | None:
        with self._peer_lock:
            p = self.peers.get(peer_id)
            return dict(p) if p else None

    def _lan_push(self, peer: dict, msg: dict) -> bool:
        m = dict(msg)
        m["origin"] = "lan"  # 强制来源标签，远端落地后按局域网来源显示
        url = f"http://{peer['ip']}:{peer['port']}/api/relay/inbox"
        payload = json.dumps(m, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json", "X-Peer-Id": self.peer_id},
        )
        for attempt in range(LAN_PUSH_RETRY + 1):
            try:
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                if attempt >= LAN_PUSH_RETRY:
                    return False
                time.sleep(0.5)
        return False

    def relay_status(self) -> dict:
        """中继通道状态（面板展示 + 故障提示）。"""
        st = dict(self._relay_state)
        st["configured"] = bool(self.relay_url)
        st["default_used"] = self.relay_url == DEFAULT_RELAY_URL
        st["accel"] = self.accel_status()
        if st["ok"] is False:
            st["hint"] = "中继不可达：聊天自动走公共通道（MQTT）；开启系统代理（如 Clash）后可自动重连中继。"
        elif st["ok"] is None:
            st["hint"] = "中继尚未连通，等待轮询中；当前可走公共通道（MQTT）。"
        else:
            st["hint"] = "中继在线。"
        return st

    def _mark_relay(self, ok: bool, err: str = "") -> None:
        self._relay_state = {"ok": ok, "err": err[:200], "last": time.strftime("%H:%M:%S")}

    # ── 公网中继通道（HTTP 轮询，兼容 Cloudflare Worker）──────────
    # 自动加速：请求自动跟随系统代理（WARP/Clash 等常以 HTTPS_PROXY 或
    # Windows 系统代理形式存在），直连超时后连续失败自动放慢轮询（backoff）。

    LOCAL_PROXY_PORTS = (7890, 7897, 10808, 10809, 1080, 2080)

    def _detect_proxy(self) -> str | None:
        """探测可用代理：环境变量 → Windows 系统代理 → 常见本地代理端口。
        结果缓存 30 秒，避免每次轮询都做端口探测。"""
        now = time.time()
        cached = getattr(self, "_proxy_cache", None)
        if cached and now - cached[1] < 30:
            return cached[0]
        proxy = self._probe_proxy()
        self._proxy_cache = (proxy, now)
        return proxy

    def _probe_proxy(self) -> str | None:
        """代理探测实现：环境变量优先，其次系统代理，再探测常见本地代理端口。"""
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            v = os.environ.get(var, "").strip()
            if v:
                return v
        try:
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                enable, _ = winreg.QueryValueEx(k, "ProxyEnable")
                server, _ = winreg.QueryValueEx(k, "ProxyServer")
            if enable and server:
                return server if "://" in server else "http://" + server
        except Exception:  # noqa: BLE001
            pass
        # 常见本地代理端口（Clash/Clash Verge/V2rayN 默认端口；即使系统代理未开启也能自动走）
        for port in self.LOCAL_PROXY_PORTS:
            try:
                import socket

                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.3)
                try:
                    s.connect(("127.0.0.1", port))
                    return f"http://127.0.0.1:{port}"
                finally:
                    s.close()
            except Exception:  # noqa: BLE001
                continue
        return None

    def _detect_warp(self) -> bool:
        """检测 Cloudflare WARP 是否在运行：WARP 是全局隧道，直连即可过墙，无需代理配置。"""
        if not sys.platform.startswith("win"):
            return False
        try:
            import subprocess

            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq CloudflareWARP.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and "CloudflareWARP.exe" in out.stdout:
                return True
            # WARP 的 WireGuard 隧道网卡处于 Up
            out2 = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-NetAdapter -Name '*Cloudflare*' -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up').Name"],
                capture_output=True, text=True, timeout=8,
            )
            return bool(out2.stdout.strip())
        except Exception:  # noqa: BLE001
            return False

    # ── 自动加速：启用已安装的 WARP（代理模式，只给中继用，不接管系统流量）──
    WARP_MSI_URL = "https://1111-releases.cloudflareclient.com/windows/Cloudflare_WARP_Release-x64.msi"
    WARP_CLI = r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"

    def _warp_ensure(self) -> dict:
        """自动启用已安装的 WARP 作为中继加速（proxy 模式，系统其他流量不受影响）。
        未安装 WARP 时不静默下载安装（避免拖慢用户系统），只提示可手动安装。限频 10 分钟。"""
        import subprocess

        st = self._warp_attempt
        if st["state"] in ("connecting",):
            return st  # 进行中，不并发
        if time.time() - st["last"] < 600:
            return st  # 限频：10 分钟内不重复
        st["last"] = time.time()
        if not os.path.exists(self.WARP_CLI):
            st.update({"state": "failed", "err": "WARP 未安装：可选装后自动加速（仅中继走代理），或直接用公共通道（MQTT）"})
            return st
        try:
            st["state"] = "connecting"
            subprocess.run([self.WARP_CLI, "mode", "proxy"], capture_output=True, timeout=30)
            subprocess.run([self.WARP_CLI, "connect"], capture_output=True, timeout=60)
            time.sleep(4)
            if self._detect_warp_proxy() or self._detect_warp():
                st.update({"state": "connected", "err": ""})
            else:
                st.update({"state": "failed", "err": "WARP 连接未就绪，稍后自动重试"})
            return st
        except Exception as exc:  # noqa: BLE001
            st.update({"state": "failed", "err": str(exc)[:200]})
            return st

    def accel_status(self) -> dict:
        """加速系统状态：WARP 代理 / 系统代理 / 直连。"""
        warp_proxy = self._detect_warp_proxy()
        warp = self._detect_warp()
        proxy = self._detect_proxy()
        st = {"warp": warp, "warp_proxy": warp_proxy, "proxy": proxy, "auto": dict(self._warp_attempt)}
        if warp_proxy:
            st["mode"], st["label"] = "warp_proxy", "WARP 代理加速中（仅中继，不接管系统流量）"
        elif proxy:
            st["mode"], st["label"] = "proxy", f"系统代理加速中（{proxy}）"
        elif warp:
            st["mode"], st["label"] = "warp", "WARP 已运行（未用代理模式，建议在 WARP 设置里选仅代理）"
        else:
            st["mode"], st["label"] = "direct", "直连（中继不可达时自动提示走公共通道 MQTT）"
        return st

    def _detect_warp_proxy(self) -> str | None:
        """WARP 代理模式端口 40000（仅代理流量，不接管系统）：监听中即视为可用。"""
        if not sys.platform.startswith("win"):
            return None
        try:
            import socket

            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", 40000))
                return "http://127.0.0.1:40000"
            finally:
                s.close()
        except Exception:  # noqa: BLE001
            return None

    def _relay_opener(self):
        proxy = self._detect_warp_proxy() or self._detect_proxy()
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            return urllib.request.build_opener(handler)
        return urllib.request.build_opener()

    def _relay_loop(self) -> None:
        fails = 0
        while not self._stop.is_set():
            if not self.relay_url:
                self._stop.wait(RELAY_POLL_INTERVAL)
                continue
            try:
                self._relay_poll()
                fails = 0
            except Exception as exc:  # noqa: BLE001
                fails += 1
                logger.debug("relay poll failed: %s", exc)
            # 全自动加速：中继连续失败且无任何加速通道时，自动启用 WARP
            if (self.relay_url and fails >= RELAY_BACKOFF_AFTER
                    and not self._detect_warp() and not self._detect_proxy()):
                self._warp_ensure()
                if self._detect_warp_proxy() or self._detect_warp():
                    fails = 0  # 加速就绪，立即恢复轮询
            interval = RELAY_POLL_INTERVAL * (RELAY_BACKOFF_MULT if fails >= RELAY_BACKOFF_AFTER else 1)
            self._stop.wait(interval)

    def _relay_poll(self) -> None:
        from urllib.parse import quote
        url = (f"{self.relay_url}/relay/poll?peer_id={self.peer_id}"
               f"&since={self._relay_since}&name={quote(self.name or '')}")
        req = urllib.request.Request(url, headers={"User-Agent": "neko-community-chat/0.2"})
        try:
            with self._relay_opener().open(req, timeout=RELAY_HTTP_TIMEOUT) as resp:
                data = json.loads(resp.read(2 * 1024 * 1024).decode("utf-8") or "{}")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
            self._mark_relay(False, str(exc))
            return
        self._mark_relay(True)
        msgs = data.get("messages") or []
        since = int(data.get("since") or 0)
        if since > self._relay_since:
            self._relay_since = since
        for m in msgs:
            if m.get("peer_id") == self.peer_id:
                continue  # 自己的消息回环丢弃
            if m.get("target_peer") and m["target_peer"] != self.peer_id:
                continue  # 不是给我的定向消息
            try:
                self.on_incoming(m)
            except Exception:  # noqa: BLE001
                logger.debug("relay incoming failed", exc_info=True)

    def _relay_send(self, msg: dict, target: str | None) -> bool:
        m = dict(msg)
        m["origin"] = "relay"  # 中继来源标签
        payload = {
            "peer_id": self.peer_id,
            "peer_name": self.name,
            "target": target,
            "message": m,
        }
        req = urllib.request.Request(
            f"{self.relay_url}/relay/send",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "neko-community-chat/0.2"},
        )
        try:
            with self._relay_opener().open(req, timeout=RELAY_HTTP_TIMEOUT) as resp:
                ok = resp.status == 200
                self._mark_relay(ok)
                return ok
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            self._mark_relay(False, str(exc))
            return False

    # ── 供 server.py 使用的 peer 查询 ────────────────────────────

    def peers_snapshot(self) -> list[dict]:
        with self._peer_lock:
            items = [(pid, dict(p)) for pid, p in self.peers.items()]
        out = [{"peer_id": pid, "name": p["name"], "ip": p["ip"], "port": p["port"]} for pid, p in items]
        out.sort(key=lambda x: x["name"])
        return out

    def rate_limited(self, peer_id: str) -> bool:
        """防刷：同一 peer 每 10 秒最多 60 条。"""
        now = time.time()
        window, limit = RATE_LIMIT_PER_PEER
        with self._peer_lock:
            hits = [t for t in self._rate.get(peer_id, []) if now - t < window]
            if len(hits) >= limit:
                return True
            hits.append(now)
            self._rate[peer_id] = hits
            return False
