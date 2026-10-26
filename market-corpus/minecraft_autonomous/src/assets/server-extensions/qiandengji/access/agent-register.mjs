#!/usr/bin/env node
// Standalone Node 22+ helper. No framework/model dependency, no credential
// arguments or secret output. Keep its identity file outside source control.
import fs from 'node:fs/promises';
import path from 'node:path';
import { randomBytes, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export function portalOrigin(value) {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) throw Error('PORTAL_HTTPS_REQUIRED');
  return url.origin;
}
export function parseInviteLink(value) {
  if (typeof value !== 'string' || value.length > 8192) throw Error('INVALID_INVITE_LINK');
  const url = new URL(value.trim());
  const origin = portalOrigin(url.origin), params = new URLSearchParams(url.hash.slice(1));
  if (url.username || url.password || url.pathname !== '/join' || url.search || [...params.keys()].length !== 1 || !/^qdi_[A-Za-z0-9_-]{43}$/.test(params.get('invite') || '')) throw Error('INVALID_INVITE_LINK');
  return { origin, token: params.get('invite') };
}
async function privateWrite(file, value, replace = false) {
  const absolute = path.resolve(file), directory = path.dirname(absolute);
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  try { if ((await fs.lstat(absolute)).isSymbolicLink()) throw Error('IDENTITY_SYMLINK_REJECTED'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const bytes = JSON.stringify(value, null, 2) + '\n';
  if (!replace) return fs.writeFile(absolute, bytes, { flag: 'wx', mode: 0o600 });
  const temporary = absolute + '.' + randomUUID() + '.tmp';
  await fs.writeFile(temporary, bytes, { flag: 'wx', mode: 0o600 });
  try { await fs.rename(temporary, absolute); } catch (error) { await fs.unlink(temporary); throw error; }
}
export async function runAgentAccess(args, output = console.log) {
  const [action, ...rest] = args;
  if (!action || ['help', '--help', '-h'].includes(action)) {
    output('千灯纪 Agent 注册工具（Node 22+）\nregister --username ag_名字 --invite-url-file 私有链接文件 --identity-file 私有身份文件\n或 register --portal https://mc.kangqiang.site --username ag_名字 --invite-file 私有邀请码文件 --identity-file 私有身份文件\nlogin|status|activate|state|logout --identity-file 私有身份文件\n先注册，再登录和查状态；仅建号成功后进入游戏。邀请码、API 密钥及游戏密码不出现在命令参数或输出中。'); return 0;
  }
  if (!['register', 'login', 'status', 'activate', 'state', 'logout'].includes(action)) throw Error('UNKNOWN_COMMAND');
  const options = {};
  for (let i = 0; i < rest.length; i += 2) {
    if (!['--portal', '--username', '--invite-file', '--invite-url-file', '--identity-file'].includes(rest[i]) || !rest[i + 1] || rest[i + 1].startsWith('--') || options[rest[i]]) throw Error('INVALID_OPTIONS');
    options[rest[i]] = rest[i + 1];
  }
  let invitation;
  if (action === 'register') {
    if (!!options['--invite-file'] === !!options['--invite-url-file']) throw Error('ONE_INVITATION_FILE_REQUIRED');
    if (options['--invite-url-file']) {
      const link = parseInviteLink(await fs.readFile(options['--invite-url-file'], 'utf8'));
      if (options['--portal'] && portalOrigin(options['--portal']) !== link.origin) throw Error('INVITE_PORTAL_MISMATCH');
      options['--portal'] = link.origin; invitation = link.token;
    } else invitation = (await fs.readFile(options['--invite-file'], 'utf8')).trim();
    if (!/^qdi_[A-Za-z0-9_-]{43}$/.test(invitation)) throw Error('INVALID_INVITE_FILE');
  }
  const file = options['--identity-file']; if (!file) throw Error('IDENTITY_FILE_REQUIRED');
  let identity;
  try { identity = JSON.parse(await fs.readFile(file, 'utf8')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!identity) {
    if (action !== 'register' || !options['--portal'] || !options['--username'] || !invitation) throw Error('REGISTER_OPTIONS_REQUIRED');
    if (!/^ag_[A-Za-z0-9_]{1,13}$/.test(options['--username'])) throw Error('INVALID_AGENT_NAME');
    identity = { schemaVersion: 1, portal: portalOrigin(options['--portal']), username: options['--username'], clientId: randomUUID(),
      clientSecret: randomBytes(32).toString('base64url'), gameRequestId: randomUUID(), gamePassword: randomBytes(24).toString('base64url') };
    // Save BEFORE exchange: retrying with this exact identity is idempotent.
    await privateWrite(file, identity);
  }
  if (identity.schemaVersion !== 1 || typeof identity.clientId !== 'string' || !/^[a-f0-9-]{36}$/i.test(identity.clientId) || typeof identity.clientSecret !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(identity.clientSecret) || !/^ag_[A-Za-z0-9_]{1,13}$/.test(identity.username || '') || !/^[a-f0-9-]{36}$/i.test(identity.gameRequestId || '') || typeof identity.gamePassword !== 'string' || identity.gamePassword.length < 6 || identity.gamePassword.length > 64 || /\s/.test(identity.gamePassword)) throw Error('INVALID_IDENTITY_FILE');
  const origin = portalOrigin(identity.portal);
  if (options['--portal'] && portalOrigin(options['--portal']) !== origin || options['--username'] && options['--username'] !== identity.username) throw Error('IDENTITY_CONFLICT');
  async function call(endpoint, method = 'GET', body, token = identity.token) {
    let response;
    try { response = await fetch(origin + '/api/v1/agent-access/' + endpoint, { method, redirect: 'error', headers: { ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(15000) }); }
    catch { throw Error('RESULT_UNKNOWN_KEEP_IDENTITY_AND_QUERY_STATUS'); }
    let value; try { value = await response.json(); } catch { throw Error('INVALID_API_RESPONSE_QUERY_STATUS'); }
    if (!response.ok) throw Object.assign(Error(value.error?.reason || 'API_REQUEST_FAILED'), { reason: value.error?.reason, status: response.status });
    return value;
  }
  async function login() {
    const result = await call('sessions', 'POST', { clientId: identity.clientId, clientSecret: identity.clientSecret }, null);
    if (!/^qda_[A-Za-z0-9_-]{43}$/.test(result.token || '') || result.clientId !== identity.clientId || result.username !== identity.username) throw Error('LOGIN_RESPONSE_MISMATCH');
    identity.token = result.token; identity.expiresAt = result.expiresAt; await privateWrite(file, identity, true);
    return { state: 'authenticated', username: identity.username, expiresAt: result.expiresAt, identityFile: path.resolve(file) };
  }
  if (action === 'register') {
    const result = await call('register', 'POST', { requestId: identity.clientId, username: identity.username, clientSecret: identity.clientSecret }, invitation);
    if (result.clientId !== identity.clientId || result.username !== identity.username) throw Error('REGISTER_RESPONSE_MISMATCH');
    output(JSON.stringify({ state: result.state, username: identity.username, clientId: identity.clientId, duplicate: result.duplicate, identityFile: path.resolve(file), nextAction: 'login' })); return 0;
  }
  if (action === 'login') { output(JSON.stringify(await login())); return 0; }
  if (!identity.token || !Number.isFinite(Date.parse(identity.expiresAt)) || Date.parse(identity.expiresAt) <= Date.now() + 5000) await login();
  let result;
  try { result = await call(action === 'state' ? 'state' : action === 'logout' ? 'session' : 'status', action === 'logout' ? 'DELETE' : 'GET'); }
  catch (error) {
    // Only a read that failed authentication is retried. An activation with
    // an uncertain outcome is never automatically resubmitted.
    if (error.status !== 401 || action === 'logout') throw error;
    await login(); result = await call(action === 'state' ? 'state' : 'status');
  }
  if (action === 'activate' && result.state === 'not_created') {
    if (!result.game?.ready) { output(JSON.stringify({ state: 'awaiting_game_bridge', username: identity.username, nextAction: 'status', message: '接入身份已保存；游戏建号桥待上线，请稍后查询，不要重复注册。' })); return 2; }
    result = await call('activate', 'POST', { requestId: identity.gameRequestId, gamePassword: identity.gamePassword });
  }
  if (action === 'logout') { delete identity.token; delete identity.expiresAt; await privateWrite(file, identity, true); }
  output(JSON.stringify(result)); return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try { process.exitCode = await runAgentAccess(process.argv.slice(2)); }
  catch (error) { console.error(JSON.stringify({ error: /^[A-Z0-9_]{3,100}$/.test(error.message) ? error.message : 'LOCAL_IDENTITY_OR_API_UNAVAILABLE', message: '保留原身份文件；结果未知时先查 status，不要另建身份或重复发起操作。' })); process.exitCode = 1; }
}
