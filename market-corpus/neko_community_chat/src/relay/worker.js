/**
 * neko_community_chat 公网中继（Cloudflare Worker + Durable Object）
 *
 * 部署：https://dash.cloudflare.com → Workers & Pages → Create → Worker
 *  1. 粘贴本文件保存部署
 *  2. 绑定 Durable Object：Settings → Bindings → Durable Object
 *     namespace 名：RELAY，类名：Relay
 *  3. 可选：在 Relay 类里设置 RELAY_TOKEN（防滥用），节点设置里填 token
 *
 * 协议（全部 JSON）：
 *  POST /relay/send   {peer_id, peer_name, target?: "peer_id", message:{...}}
 *  GET  /relay/poll   ?peer_id=...&since=N   → {since, messages:[...]}
 *  GET  /relay/health → {ok:true}
 *
 * 行为：每个 peer 独立消息队列（DO 内内存），poll 增量取；send 广播给
 * 所有活跃 peer（或定向 target）；无消息 120s 的 peer 视为离线清理。
 */

import { DurableObject } from "cloudflare:workers";

const RELAY_TOKEN = ""; // 可选：非空时 send/poll 需带 ?token= 或 X-Relay-Token 头

const corsHeaders = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-Relay-Token",
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // 浏览器面板跨域调用前的 CORS 预检
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    if (path === "/relay/health") {
      return json({ ok: true, service: "neko-community-relay" });
    }

    if (!env || !env.RELAY) {
      return json({ ok: false, error: "relay DO not bound" }, 500);
    }
    const id = env.RELAY.idFromName("relay");
    const stub = env.RELAY.get(id);

    if (path === "/relay/send" && request.method === "POST") {
      if (RELAY_TOKEN && !authed(request, url, RELAY_TOKEN)) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
      let body;
      try { body = await request.json(); } catch { return json({ ok: false, error: "bad json" }, 400); }
      return stub.handleSend(body);
    }

    if (path === "/relay/poll" && request.method === "GET") {
      if (RELAY_TOKEN && !authed(request, url, RELAY_TOKEN)) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
      const peerId = url.searchParams.get("peer_id") || "";
      const since = Math.max(0, parseInt(url.searchParams.get("since") || "0", 10) || 0);
      const name = url.searchParams.get("name") || "";
      return stub.handlePoll(peerId, since, name);
    }

    return json({ ok: false, error: "not found" }, 404);
  },
};

function authed(request, url, token) {
  const q = url.searchParams.get("token");
  const h = request.headers.get("X-Relay-Token") || "";
  return q === token || h === token;
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: corsHeaders,
  });
}

const PEER_TTL_MS = 120_000;      // 无心跳视为离线
const MAX_MSG_LEN = 2000;
const RATE_WINDOW_MS = 10_000;
const RATE_LIMIT = 60;            // 每 peer 每 10s 最多 60 条

export class Relay extends DurableObject {
  constructor(state, env) {
    super(state, env);
    this.state = state;
    // 结构化数据（非事务持久，够用；重启清空可接受）
    this.queues = new Map();    // peer_id -> [{seq, msg}]
    this.peers = new Map();     // peer_id -> {last_seen, name}
    this.rates = new Map();     // peer_id -> [ts]
    this._gseq = 0;             // 全局单调序号（避免多发送者 seq 混排导致 poll 游标跳跃）
  }

  _nextGseq() {
    this._gseq = (this._gseq || 0) + 1;
    return this._gseq;
  }

  _touch(peerId, name) {
    this.peers.set(peerId, { last_seen: Date.now(), name: (name || "").slice(0, 40) });
    if (!this.queues.has(peerId)) this.queues.set(peerId, []);
    this._sweep();
  }

  _sweep() {
    const now = Date.now();
    for (const [pid, p] of this.peers) {
      if (now - p.last_seen > PEER_TTL_MS) {
        this.peers.delete(pid);
        this.queues.delete(pid);
      }
    }
  }

  _rateOk(peerId) {
    const now = Date.now();
    const hits = (this.rates.get(peerId) || []).filter((t) => now - t < RATE_WINDOW_MS);
    if (hits.length >= RATE_LIMIT) return false;
    hits.push(now);
    this.rates.set(peerId, hits);
    return true;
  }

  async handleSend(body) {
    const peerId = String(body.peer_id || "").slice(0, 64);
    if (!peerId) return json({ ok: false, error: "missing peer_id" }, 400);
    if (!this._rateOk(peerId)) return json({ ok: false, error: "rate limited" }, 429);
    this._touch(peerId, body.peer_name);
    const m = body.message || {};
    const text = String(m.text || "").trim();
    if (!text || text.length > MAX_MSG_LEN) return json({ ok: false, error: "bad message" }, 400);

    const envelope = {
      seq: this._nextGseq(),
      message: Object.assign({}, m, {
        peer_id: peerId,
        peer_name: this.peers.get(peerId).name,
        origin: "relay",
      }),
    };

    const target = String(body.target || "").slice(0, 64);
    if (target && target !== peerId) {
      // 定向投递
      this._push(target, envelope);
      return json({ ok: true, delivered: this.peers.has(target) });
    }

    // 广播给所有活跃 peer（不含自己）
    let delivered = 0;
    for (const pid of [...this.peers.keys()]) {
      if (pid !== peerId) {
        this._push(pid, envelope);
        delivered += 1;
      }
    }
    return json({ ok: true, delivered });
  }

  _push(peerId, envelope) {
    if (!this.peers.has(peerId)) return;
    const q = this.queues.get(peerId) || [];
    q.push(envelope);
    if (q.length > 200) q.splice(0, q.length - 200);
  }

  async handlePoll(peerId, sinceRaw, name) {
    peerId = String(peerId || "").slice(0, 64);
    if (!peerId) return json({ ok: false, error: "missing peer_id" }, 400);
    this._touch(peerId, name || "");
    const since = Math.max(0, parseInt(sinceRaw || "0", 10) || 0);
    const q = this.queues.get(peerId) || [];
    const fresh = q.filter((e) => e.seq > since);
    // 清理已投递
    if (fresh.length < q.length) {
      this.queues.set(peerId, q.filter((e) => e.seq > since));
    } else if (q.length > 0) {
      // 全量命中说明自上次后没有新消息，保留
    }
    return json({ since: this._gseq || 0, messages: fresh.map((e) => e.message) });
  }
}
