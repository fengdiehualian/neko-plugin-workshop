import http from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {errorCode} from './engine.mjs';

function authorized(req, token) {
  const a = Buffer.from(req.headers.authorization || ''), b = Buffer.from(`Bearer ${token}`);
  return a.length === b.length && timingSafeEqual(a, b);
}
async function body(req, limit = 65536) {
  let size = 0; const chunks = [];
  for await (const part of req) {size += part.length; if (size > limit) throw new Error('REQUEST_TOO_LARGE'); chunks.push(part);}
  try {return JSON.parse(Buffer.concat(chunks).toString() || '{}');} catch {throw new Error('INVALID_JSON');}
}
export function createServer(engine, token) {
  if (typeof token !== 'string' || token.length < 32) throw new Error('BRIDGE_TOKEN_TOO_SHORT');
  const server = http.createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    const send = (status, value) => {res.writeHead(status); res.end(JSON.stringify(value));};
    if (req.headers.origin) return send(403, {code: 'BROWSER_ORIGIN_FORBIDDEN'});
    if (!authorized(req, token)) return send(401, {code: 'UNAUTHORIZED'});
    const url = new URL(req.url, 'http://127.0.0.1');
    try {
      if (req.method === 'GET' && url.pathname === '/v1/status') return send(200, engine.status());
      if (req.method === 'GET' && url.pathname === '/v1/events') return send(200, engine.readEvents(Number(url.searchParams.get('after') || 0)));
      if (req.method !== 'POST') return send(404, {code: 'NOT_FOUND'});
      const data = await body(req, url.pathname === '/v1/speech/result' ? 3900000 : 65536);
      if (url.pathname === '/v1/plugin/heartbeat') return send(200, engine.heartbeat(data));
      if (url.pathname === '/v1/plugin/disconnect') return send(200, await engine.unlinkPlugin(data.session_id));
      if (url.pathname === '/v1/speech/next' && engine.providers.next) return send(200, engine.providers.next(data.active_id));
      if (url.pathname === '/v1/speech/result' && engine.providers.complete) return send(200, engine.providers.complete(data));
      if (url.pathname === '/v1/speech/progress' && engine.providers.progress) return send(200, engine.providers.progress(data));
      if (url.pathname === '/v1/speech/disconnect' && engine.providers.disconnect) return send(200, engine.providers.disconnect());
      if (url.pathname === '/v1/join') await engine.join();
      else if (url.pathname === '/v1/leave') await engine.leave();
      else if (url.pathname === '/v1/listening') engine.setListening(data.enabled);
      else if (url.pathname === '/v1/mute') engine.setMuted(data.enabled);
      else if (url.pathname === '/v1/cancel') engine.cancelReply();
      else if (url.pathname === '/v1/bind') engine.bind(data.peer_id, data.user_id, data.display_name, data.epoch);
      else if (url.pathname === '/v1/reply') return send(200, await engine.reply(data));
      else return send(404, {code: 'NOT_FOUND'});
      send(200, engine.status());
    } catch (e) {send(400, {code: errorCode(e)});}
  });
  server.requestTimeout = 10000; server.headersTimeout = 5000;
  return server;
}
