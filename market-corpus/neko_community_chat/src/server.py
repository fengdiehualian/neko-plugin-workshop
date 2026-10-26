"""neko_community_chat 本地消息服务（127.0.0.1:7870）+ 节点互联（v0.2.0）。

纯标准库实现（threading + http.server + json + node.py），零第三方依赖：
- POST /api/messages           面板发消息（写入 inbox 供 YUI 唤醒消费，并广播给 peer）
- GET  /api/messages?after_id= 面板轮询消息（含 YUI 回复，id 递增）
- POST /api/relay/inbox        远端 peer LAN 直推入口（校验来源 + 限速）
- GET  /api/peers              在线节点列表（LAN + 中继可见 peer）
- POST /api/posts              社区墙发帖（msg_type=post，广播给所有 peer）
- GET  /api/feed               社区墙帖子（所有节点发帖）
- GET  /api/settings POST /api/settings   节点昵称/公网中继 URL
- GET  /api/history            全量历史（面板首屏）
- GET  /chat                   聊天面板页面（客户端面板）
- GET  /api/health             健康检查

消息目录：%LOCALAPPDATA%/N.E.K.O/neko_community_chat/
  messages.jsonl  全量消息流（面板读写）
  inbox/*.json    待 YUI 消费的新用户消息（插件 tick 读）
  outbox/*.json   YUI 的回复（community_chat_send 写入，插件 tick 读后归入消息流）
  peer.json       节点身份（peer_id / name）
  settings.json   面板设置（昵称、中继 URL）
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import node as node_mod

DEFAULT_PORT = 7870
LAN_HTTP_PORT = 7871  # 局域网互推专用口（绑 0.0.0.0，只开放 /api/relay/inbox）

# 模块级节点（server 启动时由插件注入）
_NODE: node_mod.Node | None = None
_NODE_LOCK = threading.Lock()


def set_node(n: node_mod.Node | None) -> None:
    global _NODE
    with _NODE_LOCK:
        _NODE = n


def get_node() -> node_mod.Node | None:
    with _NODE_LOCK:
        return _NODE


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "N.E.K.O", "neko_community_chat")
    os.makedirs(os.path.join(d, "inbox"), exist_ok=True)
    os.makedirs(os.path.join(d, "outbox"), exist_ok=True)
    return d


def messages_path() -> str:
    return os.path.join(data_dir(), "messages.jsonl")


def next_id() -> int:
    """消息 ID = 文件行数 + 1（追加写，线程锁保证单调）。"""
    p = messages_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            return sum(1 for _ in f) + 1
    except OSError:
        return 1


_ID_LOCK = threading.Lock()


def append_message(msg: dict) -> dict:
    """追加一条消息到消息流，返回带 id/ts/msg_uid 的完整记录。"""
    with _ID_LOCK:
        msg["id"] = next_id()
        msg.setdefault("msg_uid", uuid.uuid4().hex[:12])
        msg["ts"] = time.strftime("%H:%M:%S")
        p = messages_path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    _apply_pending_recalls()  # 撤回先于消息到达时补标记
    return msg


def write_inbox(msg: dict) -> None:
    """写 inbox：待 YUI 消费的新用户消息（插件后台 tick 扫描）。"""
    rid = f"{int(time.time() * 1000)}"
    path = os.path.join(data_dir(), "inbox", f"{rid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(msg, f, ensure_ascii=False)
    msg["inbox_file"] = path


def read_messages(after_id: int = 0) -> list[dict]:
    """读消息流中 id > after_id 的消息（轮询增量）。"""
    out: list[dict] = []
    try:
        with open(messages_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if int(m.get("id") or 0) > after_id:
                    out.append(m)
    except OSError:
        pass
    return out


def read_all_messages() -> list[dict]:
    try:
        with open(messages_path(), "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []


def read_posts() -> list[dict]:
    """自建社区墙帖子（本地消息流中的 msg_type=post）。"""
    return [m for m in read_all_messages() if (m.get("msg_type") or "chat") == "post"]


def _rewrite_all(msgs: list[dict]) -> None:
    """整文件重写消息流（删除帖子/清空聊天用），保持 id 行号稳定。"""
    with _ID_LOCK:
        p = messages_path()
        with open(p, "w", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")


def admin_lists() -> dict:
    """本地管理员设置：禁言(muted)/拉黑(blocked) 的远端 peer_id 列表。"""
    s = load_settings()
    return {"muted": list(s.get("muted") or []), "blocked": list(s.get("blocked") or [])}


def is_visible(m: dict, s: dict | None = None) -> bool:
    """本地可见性：被禁言/拉黑的远端节点内容（聊天+帖子）不展示。"""
    if s is None:
        s = load_settings()
    hidden = set(s.get("muted") or []) | set(s.get("blocked") or [])
    return str(m.get("peer_id") or "") not in hidden


def delete_post(post_id: str) -> bool:
    """删除本地帖子及其回复（消息流按 post_id / reply_to 匹配）。"""
    if not post_id:
        return False
    msgs = read_all_messages()
    matched = [m for m in msgs
               if (m.get("msg_type") or "chat") == "post" and str(m.get("post_id") or "") == post_id]
    if not matched:
        return False  # 帖子不存在
    post_keys = {str(m.get("id") or "") for m in matched} | {str(m.get("post_id") or "") for m in matched}
    nxt = [m for m in msgs
           if not ((m.get("msg_type") or "chat") == "post" and str(m.get("post_id") or "") == post_id)
           and str(m.get("reply_to") or "") not in post_keys]
    if len(nxt) == len(msgs):
        return False
    _rewrite_all(nxt)
    return True


def clear_local_chat() -> int:
    """清空本机与 YUI 的聊天：保留远端消息（origin=relay）与全部帖子，删除其余本地聊天。"""
    msgs = read_all_messages()
    keep = [m for m in msgs
            if (m.get("origin") or "local") == "relay" or (m.get("msg_type") or "chat") == "post"]
    removed = len(msgs) - len(keep)
    if removed:
        _rewrite_all(keep)
    return removed


# ── 本地内容审核（YUI 审核机制的即时兜底）──────────────────────
# 规则 + 可扩展词表：词表存 data_dir/audit_words.txt（每行一个词，可自行增删）。
_AUDIT_SPAM_RE = re.compile(r"(.)\1{19,}")  # 连续 20+ 相同字符（刷屏）
_DEFAULT_AUDIT_WORDS = (
    "刷单", "代开发票", "办假证", "出售驾照", "博彩", "赌球", "赌博网站",
    "裸聊", "卖淫", "嫖娼", "毒品", "冰毒", "海洛因", "枪支", "军火", "爆炸物", "制毒",
    "杀猪盘", "电信诈骗", "洗钱",
)


def _load_audit_words() -> tuple[str, ...]:
    p = os.path.join(data_dir(), "audit_words.txt")
    try:
        with open(p, encoding="utf-8") as f:
            extra = [ln.strip() for ln in f if ln.strip()]
        return _DEFAULT_AUDIT_WORDS + tuple(extra)
    except OSError:
        return _DEFAULT_AUDIT_WORDS


def audit_text(text: str) -> tuple[bool, str]:
    """本地审核：返回 (是否通过, 拒绝原因)。不通过时消息不允许发布。"""
    if not text:
        return False, "内容为空"
    if _AUDIT_SPAM_RE.search(text):
        return False, "内容疑似刷屏，已拦截"
    if len(set(text)) <= 2 and len(text) > 20:
        return False, "内容疑似重复刷屏，已拦截"
    for word in _load_audit_words():
        if word in text:
            return False, f"内容包含违规词「{word}」，已拦截"
    return True, ""


def _rewrite_messages(updates: dict) -> None:
    """按 msg_uid/id 就地更新消息（不改变行数，id 行号保持稳定）。"""
    p = messages_path()
    with _ID_LOCK:
        try:
            with open(p, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            return
        changed = False
        for i, ln in enumerate(lines):
            if not ln.strip():
                continue
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            if str(m.get("msg_uid") or "") == updates.get("msg_uid") or str(m.get("id") or "") == updates.get("id"):
                m.update({k: v for k, v in updates.items() if v is not None and k not in ("msg_uid", "id")})
                lines[i] = json.dumps(m, ensure_ascii=False)
                changed = True
        if changed:
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")


# ── 撤回待处理缓存（recall 先于消息到达时暂存，消息落地后补标记）──────
_pending_recalls: set[str] = set()


def mark_recalled(uid: str) -> bool:
    """标记一条消息为已撤回（msg_uid 或本地 id 兜底）。消息未到时缓存待处理。"""
    if not uid:
        return False
    before = None
    for m in read_all_messages():
        if str(m.get("msg_uid") or "") == uid or str(m.get("id") or "") == uid:
            before = m
            break
    if before is None:
        _pending_recalls.add(uid)  # 消息还没落地，等 append 后补标记
        return False
    if before.get("recalled"):
        _pending_recalls.discard(uid)
        return False
    if str(before.get("msg_uid") or "") == uid:
        _rewrite_messages({"msg_uid": uid, "recalled": True})
    else:
        _rewrite_messages({"id": uid, "recalled": True})
    _pending_recalls.discard(uid)
    return True


def _apply_pending_recalls() -> None:
    """消息落地后尝试补标记缓存中的撤回。"""
    for uid in list(_pending_recalls):
        if mark_recalled(uid):
            _pending_recalls.discard(uid)


def local_posts_feed(visible_filter=None) -> dict:
    """社区墙：帖子 + 回复（post_reply 按 reply_to 挂到对应帖子下）。"""

    def _all(_m: dict) -> bool:
        return True

    if visible_filter is None:
        visible_filter = _all
    posts = []
    replies_by: dict[str, list[dict]] = {}
    for m in read_all_messages():
        if not visible_filter(m):
            continue
        mt = m.get("msg_type") or "chat"
        if mt == "post":
            posts.append(m)
        elif mt == "post_reply":
            replies_by.setdefault(str(m.get("reply_to") or ""), []).append(m)
    for p in posts:
        key = str(p.get("post_id") or "")
        by_id = str(p.get("id") or "")
        got = list(replies_by.get(key, [])) + list(replies_by.get(by_id, [])) + list(replies_by.get("id-" + by_id, []))
        # 去重（同一回复可能同时命中多个 key）
        seen, uniq = set(), []
        for r in got:
            rk = str(r.get("id"))
            if rk not in seen:
                seen.add(rk)
                uniq.append(r)
        p["replies"] = uniq
    return {"ok": True, "posts": posts}


def incoming_remote(msg: dict) -> None:
    """远端 peer 消息落地：recall → 标记撤回；post / post_reply → 仅进消息流；
    chat → 消息流 + inbox（唤醒本机 YUI）。"""
    m = dict(msg)
    m.setdefault("origin", "lan")
    msg_type = m.get("msg_type") or "chat"
    if msg_type == "recall":
        mark_recalled(str(m.get("msg_uid") or "") or str(m.get("id") or ""))
        return
    if msg_type in ("post", "post_reply"):
        append_message(m)
    else:
        recorded = append_message(m)
        try:
            write_inbox(recorded)
        except OSError:
            pass


def _is_private_ip(ip: str) -> bool:
    if ip == "127.0.0.1" or ip == "::1":
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    a = int(parts[0])
    if a == 10:
        return True
    if a == 172 and 16 <= int(parts[1]) <= 31:
        return True
    if a == 192 and parts[1] == "168":
        return True
    return False


def save_settings(d: dict) -> dict:
    """保存面板设置（昵称/中继 URL/MQTT 公网通道），返回合并后的完整设置。"""
    p = os.path.join(data_dir(), "settings.json")
    old = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        pass
    merged = dict(old)
    for k in ("name", "relay_url"):
        if d.get(k) is not None:
            merged[k] = str(d[k]).strip()[:200]
    for k in ("mqtt_enabled",):
        if d.get(k) is not None:
            merged[k] = bool(d[k])
    for k in ("mqtt_host", "mqtt_channel"):
        if d.get(k) is not None:
            merged[k] = str(d[k]).strip()[:200]
    for k in ("muted", "blocked"):
        if d.get(k) is not None:
            merged[k] = [str(x).strip()[:64] for x in d[k] if str(x).strip()]
    if d.get("mqtt_port") is not None:
        try:
            merged["mqtt_port"] = int(d["mqtt_port"])
        except (TypeError, ValueError):
            pass
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    n = get_node()
    if n is not None:
        n.refresh()
    return merged


def load_settings() -> dict:
    p = os.path.join(data_dir(), "settings.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class _Handler(BaseHTTPRequestHandler):
    server_version = "neko_community_chat/0.3"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._json({"ok": True, "port": DEFAULT_PORT, "service": "neko_community_chat"})
        if path == "/api/history":
            s = load_settings()
            return self._json({"ok": True, "messages": [m for m in read_all_messages() if is_visible(m, s)]})
        if path == "/api/messages":
            after = 0
            try:
                after = int((self.path.split("?", 1)[1] or "").split("after_id=")[1])
            except (IndexError, ValueError):
                pass
            s = load_settings()
            # 聊天轮询只返回 chat 消息（社区墙帖子走 /api/feed）
            return self._json({"ok": True, "messages": [m for m in read_messages(after)
                                                        if (m.get("msg_type") or "chat") == "chat" and is_visible(m, s)]})
        if path == "/api/feed":
            return self._json(local_posts_feed(visible_filter=is_visible))
        if path == "/api/peers":
            n = get_node()
            peers = n.peers_snapshot() if n is not None else []
            self_info = {}
            mqtt_state = None
            relay_state = None
            if n is not None:
                self_info = {"peer_id": n.peer_id, "name": n.name, "self": True}
                mqtt_state = n.mqtt_status()
                relay_state = n.relay_status()
            return self._json({"ok": True, "self": self_info, "peers": peers, "mqtt": mqtt_state, "relay": relay_state})
        if path == "/api/settings":
            s = load_settings()
            n = get_node()
            if n is not None:
                s.setdefault("name", n.name)
                s.setdefault("mqtt_enabled", bool(n.mqtt_cfg.get("enabled", True)))
                s.setdefault("mqtt_host", n.mqtt_cfg.get("host", ""))
                s.setdefault("mqtt_port", n.mqtt_cfg.get("port", 1883))
                s.setdefault("mqtt_channel", n.mqtt_cfg.get("channel", ""))
            s.pop("relay_url", None)  # 隐私：中继地址明文永不返回（默认/自定义均不可见）
            s["relay_default"] = bool(n is not None and n.relay_url == node_mod.DEFAULT_RELAY_URL)
            s["admin"] = True  # 本机固定为本地管理员
            s["muted"] = list(s.get("muted") or [])
            s["blocked"] = list(s.get("blocked") or [])
            return self._json({"ok": True, "settings": s})
        if path == "/chat" or path == "/":
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "chat.html"), "rb") as f:
                    return self._html(f.read())
            except OSError:
                return self._json({"ok": False, "error": "chat.html missing"}, 500)
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/relay/inbox":
            return self._post_relay_inbox()
        if path == "/api/posts":
            return self._post_posts()
        if path == "/api/recall":
            return self._post_recall()
        if path == "/api/settings":
            return self._post_settings()
        if path == "/api/admin":
            return self._post_admin()
        if path == "/api/clear_chat":
            removed = clear_local_chat()
            return self._json({"ok": True, "removed": removed})
        if path != "/api/messages":
            return self._json({"ok": False, "error": "not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._json({"ok": False, "error": "bad json"}, 400)
        text = str(data.get("text") or "").strip()
        sender = str(data.get("sender") or "user").strip()[:40]
        if not text:
            return self._json({"ok": False, "error": "empty message"}, 400)
        if len(text) > 2000:
            return self._json({"ok": False, "error": "message too long"}, 400)
        ok, reason = audit_text(text)
        if not ok:
            return self._json({"ok": False, "error": reason}, 400)
        target_peer = str(data.get("target_peer") or "").strip()[:64]
        msg = {"sender": sender, "text": text, "role": "user", "msg_type": "chat", "origin": "local",
               "target_peer": target_peer or None}
        recorded = append_message(msg)
        n = get_node()
        if target_peer:
            # 面板定向回复远端用户：不唤醒本机 YUI，直接投递目标节点
            if n is not None:
                n.send_to(target_peer, recorded)
            return self._json({"ok": True, "message": recorded}, 201)
        try:
            write_inbox(recorded)  # 交给 YUI 唤醒消费
        except OSError:
            pass  # inbox 写失败不阻断回显，唤醒可能失败由插件侧重试
        if n is not None:
            n.dispatch(recorded)  # 广播给所有 peer（本机用户消息）
        return self._json({"ok": True, "message": recorded}, 201)

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return None

    def _post_relay_inbox(self):
        """LAN peer 直推入口：来源 IP 须为私有网段 + 限速；chat→唤醒，post→仅记录。"""
        peer_id = str(self.headers.get("X-Peer-Id") or "").strip()[:64]
        client_ip = self.client_address[0] if self.client_address else ""
        if not peer_id or not _is_private_ip(client_ip):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        n = get_node()
        if n is not None and n.peer_id == peer_id:
            return self._json({"ok": True, "dropped": "self"})  # 自己的消息回环丢弃
        if n is not None and n.rate_limited(peer_id):
            return self._json({"ok": False, "error": "rate limited"}, 429)
        data = self._read_json_body()
        if data is None:
            return self._json({"ok": False, "error": "bad json"}, 400)
        text = str(data.get("text") or "").strip()
        msg_type = data.get("msg_type") or "chat"
        if msg_type != "recall":  # 撤回通知无正文，不审核
            if not text or len(text) > 2000:
                return self._json({"ok": False, "error": "bad message"}, 400)
            ok, reason = audit_text(text)
            if not ok:
                return self._json({"ok": False, "error": reason}, 400)
        msg = {
            "sender": str(data.get("sender") or peer_id[:12])[:40],
            "text": text,
            "role": "user",
            "msg_type": msg_type,
            "origin": "lan",
            "peer_id": peer_id,
            "peer_name": str(data.get("peer_name") or peer_id[:12])[:40],
        }
        incoming_remote(msg)
        return self._json({"ok": True, "received": True}, 200)

    def _post_posts(self):
        """社区墙发帖：新帖（post）或回复（post_reply），本地审核后广播给所有 peer。"""
        data = self._read_json_body()
        if data is None:
            return self._json({"ok": False, "error": "bad json"}, 400)
        text = str(data.get("text") or "").strip()
        if not text or len(text) > 2000:
            return self._json({"ok": False, "error": "bad post"}, 400)
        ok, reason = audit_text(text)
        if not ok:
            return self._json({"ok": False, "error": reason}, 400)
        n = get_node()
        name = n.name if n is not None else "社区用户"
        reply_to = str(data.get("reply_to") or "").strip()[:64]
        if reply_to:
            msg = {"sender": name, "text": text, "role": "post_reply", "msg_type": "post_reply",
                   "reply_to": reply_to, "origin": "local"}
        else:
            msg = {"sender": name, "text": text, "role": "post", "msg_type": "post",
                   "post_id": uuid.uuid4().hex[:12], "origin": "local"}
        if n is not None:
            # 本机帖也带 peer_id/peer_name：前端才能显示自己的昵称、精确定位本人消息
            msg["peer_id"] = n.peer_id
            msg["peer_name"] = name
        recorded = append_message(msg)
        if n is not None:
            n.dispatch(recorded)
        return self._json({"ok": True, "post": recorded}, 201)

    def _post_recall(self):
        """撤回自己发送的消息（本机消息）并广播撤回通知。"""
        data = self._read_json_body()
        if data is None:
            return self._json({"ok": False, "error": "bad json"}, 400)
        uid = str(data.get("msg_uid") or "").strip()[:32]
        if not uid:
            return self._json({"ok": False, "error": "bad recall"}, 400)
        n = get_node()
        me = n.peer_id if n is not None else None
        target = None
        for m in read_all_messages():
            if str(m.get("msg_uid") or "") == uid or str(m.get("id") or "") == uid:
                target = m
                break
        if target is None:
            return self._json({"ok": False, "error": "消息不存在"}, 404)
        if target.get("recalled"):
            return self._json({"ok": False, "error": "消息已撤回"}, 400)
        owner = str(target.get("peer_id") or "")
        # 权限：仅能撤回本机产生的消息（无 peer_id 的本机消息也允许）
        if owner and me and owner != me:
            return self._json({"ok": False, "error": "只能撤回自己发送的消息"}, 403)
        if not mark_recalled(uid):
            return self._json({"ok": False, "error": "撤回失败"}, 500)
        if n is not None:
            # text 占位：中继 worker 要求消息必有非空 text；远端按 msg_type=recall 处理，忽略正文
            n.dispatch({"msg_type": "recall", "msg_uid": uid, "text": "（撤回一条消息）", "origin": "local"})
        return self._json({"ok": True}, 200)

    def _post_settings(self):
        data = self._read_json_body()
        if data is None:
            return self._json({"ok": False, "error": "bad json"}, 400)
        merged = save_settings(data)
        return self._json({"ok": True, "settings": merged})

    def _post_admin(self):
        """本地管理员操作：delete_post / mute / unmute / block / unblock。
        服务只绑 127.0.0.1，天然仅本机可调用。"""
        data = self._read_json_body()
        if data is None:
            return self._json({"ok": False, "error": "bad json"}, 400)
        action = str(data.get("action") or "").strip()
        if action == "delete_post":
            post_id = str(data.get("post_id") or "").strip()
            if delete_post(post_id):
                return self._json({"ok": True, "deleted": post_id})
            return self._json({"ok": False, "error": "帖子不存在"}, 404)
        peer = str(data.get("peer_id") or "").strip()[:64]
        if not peer:
            return self._json({"ok": False, "error": "bad peer_id"}, 400)
        s = load_settings()
        if action == "mute":
            s.setdefault("muted", [])
            if peer not in s["muted"]:
                s["muted"].append(peer)
            save_settings(s)
            return self._json({"ok": True, "settings": s})
        if action == "unmute":
            s["muted"] = [p for p in s.get("muted") or [] if p != peer]
            save_settings(s)
            return self._json({"ok": True, "settings": s})
        if action == "block":
            s.setdefault("blocked", [])
            if peer not in s["blocked"]:
                s["blocked"].append(peer)
            save_settings(s)
            return self._json({"ok": True, "settings": s})
        if action == "unblock":
            s["blocked"] = [p for p in s.get("blocked") or [] if p != peer]
            save_settings(s)
            return self._json({"ok": True, "settings": s})
        return self._json({"ok": False, "error": "unknown action"}, 400)


_SERVER_PORT: int = DEFAULT_PORT
_LAN_SERVER: object | None = None


def server_port() -> int:
    """当前绑定的 HTTP 端口（start_server 实际绑定端口；未启动时为默认 7870）。"""
    return _SERVER_PORT


def lan_port() -> int:
    """局域网互推端口（start_lan_server 实际绑定；失败时仍返回默认 7871 供广播参考）。"""
    return LAN_HTTP_PORT


def start_server(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """启动本地消息服务（独立线程）。只绑本机回环，不暴露局域网。"""
    server = ThreadingHTTPServer((host, port), _Handler)
    global _SERVER_PORT
    _SERVER_PORT = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True, name="neko-community-chat-http")
    t.start()
    return server


class _LanHandler(_Handler):
    """局域网互推口：只接受 /api/relay/inbox，其余一律拒绝（防 LAN 内读取聊天历史）。"""

    server_version = "neko_community_chat/0.2-lan"

    def do_GET(self) -> None:  # noqa: N802
        return self._json({"ok": False, "error": "lan endpoint only"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/relay/inbox":
            return self._json({"ok": False, "error": "lan endpoint only"}, 404)
        return self._post_relay_inbox()

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        pass  # 局域网口静默日志，避免刷屏


def start_lan_server(port: int = LAN_HTTP_PORT, host: str = "0.0.0.0") -> ThreadingHTTPServer | None:
    """启动局域网互推口（0.0.0.0）。端口被占（多实例/防火墙）返回 None，不阻断主服务。"""
    global _LAN_SERVER
    try:
        server = ThreadingHTTPServer((host, port), _LanHandler)
    except OSError:
        return None
    t = threading.Thread(target=server.serve_forever, daemon=True, name="neko-community-chat-lan")
    t.start()
    _LAN_SERVER = server
    return server
