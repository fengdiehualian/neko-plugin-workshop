const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

// Run the control page itself; replace only the DOM, clock and host transport.
async function panel(initial = {}) {
  let now = 0, timerId = 0, uuid = 0, offline = false, contextDown = false, enableError = false, disableError = false;
  let installedVersion = '0.1.0';
  const timers = new Map(), calls = [], nodes = {}, held = {context: [], start: [], enable: [], disable: []};
  for (const id of ['enable', 'shortcut', 'voice', 'voice-status', 'voice-retry', 'notice', 'start', 'stop', 'status', 'connection', 'message', 'plugin-version', 'phase-label']) nodes[id] = {setAttribute() {}};
  nodes.voice.checked = true;
  const document = {body: {dataset: {}}, getElementById: id => nodes[id]};
  const state = {ready: true, active: false, desktop_ready: true, desktop: {client_id: 'owner', character: '当前猫娘'},
    character: null, round_id: null, shortcut: 'Ctrl+Shift+1', voice_enabled: true, ...initial};
  const answer = data => ({ok: true, json: async () => data});
  async function fetch(url, options) {
    if (offline) throw new Error('offline');
    let kind = 'context', data;
    if (url === '/plugin/neko_ads/start') {
      calls.push({command: 'enable'}); kind = 'enable';
      if (enableError) return {ok: false, json: async () => ({detail: '启动权限不足，请在宿主插件管理中操作。'})};
      state.ready = true; data = {success: true};
    } else if (url === '/plugin/neko_ads/stop') {
      calls.push({command: 'disable'}); kind = 'disable';
      if (disableError) return {ok: false, json: async () => ({detail: '插件关闭失败，请查看日志。'})};
      Object.assign(state, {ready: false, active: false, round_id: null}); data = {success: true};
    } else if (url === '/plugin/status?plugin_id=neko_ads') data = {status: {status: state.ready ? 'running' : 'stopped'}};
    else if (url.includes('/action/')) {
      const {command, payload} = JSON.parse(options.body).args; calls.push({command, payload}); kind = command;
      if (command === 'start') Object.assign(state, {active: true, round_id: payload.round_id, character: state.desktop.character, voice_enabled: payload.voice_enabled});
      if (command === 'stop' && (!payload.round_id || payload.round_id === state.round_id)) Object.assign(state, {active: false, round_id: null});
      if (command === 'shortcut') state.shortcut = payload.shortcut;
      if (command === 'voice') state.voice_enabled = payload.enabled;
      data = {result: {data: {...state}}};
    } else {
      if (contextDown) return {ok: false, json: async () => ({detail: 'Plugin not running'})};
      data = {plugin: {version: installedVersion}, state: state.ready ? {...state} : {}};
    }
    if (held[kind] && held[kind].length) return held[kind].shift()(data);
    return answer(data);
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/main.js'), 'utf8'), {
    document, fetch, AbortController, crypto: {randomUUID: () => 'round-' + ++uuid},
    localStorage: {getItem: () => null, setItem() {}},
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, {fn, at: now + ms}); return id; }, clearTimeout: id => timers.delete(id),
  });
  await flush();
  return {nodes, state, calls, get phase() { return document.body.dataset.phase; }, offline(value) { offline = value; },
    installedVersion(value) { installedVersion = value; },
    start: () => nodes.start.onclick(), stop: () => nodes.stop.onclick(), enable: () => nodes.enable.onclick(),
    contextDown(value) { contextDown = value; }, enableError(value) { enableError = value; }, disableError(value) { disableError = value; },
    hold(kind) {
      let release, reject;
      held[kind].push(data => new Promise((resolve, fail) => { release = (value = data) => resolve(answer(value)); reject = fail; }));
      const finish = value => release(value);
      finish.timeout = () => reject(Object.assign(new Error('Operation aborted'), {name:'AbortError'}));
      return finish;
    },
    async advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at; timers.delete(next[0]); next[1].fn(); await flush();
      }
      now = end; await flush();
    },
  };
}

test('version follows host metadata updates without starting a game', async () => {
  const h = await panel();
  assert.equal(h.nodes['plugin-version'].textContent, 'v0.1.0');
  h.installedVersion('0.2.0-rc.1'); await h.advance(2000);
  assert.equal(h.nodes['plugin-version'].textContent, 'v0.2.0-rc.1');
  assert.equal(h.calls.length, 0); assert.equal(h.state.active, false);
});

test('stopped plugins still show the host manifest version', async () => {
  const h = await panel({ready:false});
  h.installedVersion('v1.2.3'); await h.advance(2000);
  assert.equal(h.nodes['plugin-version'].textContent, 'v1.2.3');
  assert.equal(h.phase, 'disabled'); assert.equal(h.calls.length, 0);
});

test('missing metadata never invents a fallback version', async () => {
  const h = await panel();
  h.installedVersion(null); await h.advance(2000);
  assert.equal(h.nodes['plugin-version'].textContent, '版本暂不可用');
  assert.equal(h.nodes.start.disabled, false);
});

test('opening the panel and polling cannot start a game', async () => {
  const h = await panel(); await h.advance(20000);
  assert.equal(h.phase, 'standby'); assert.equal(h.calls.length, 0);
  assert.equal(h.nodes.start.disabled, false); assert.equal(h.nodes.stop.disabled, true);
});

test('unavailable plugin and missing renderer both block starting', async () => {
  const h = await panel({ready: false}); await h.start();
  assert.equal(h.phase, 'disabled'); assert.equal(h.calls.length, 0);
  Object.assign(h.state, {ready: true, desktop_ready: false, desktop: null}); await h.advance(2000); await h.start();
  assert.equal(h.phase, 'waiting'); assert.equal(h.nodes.start.disabled, true); assert.equal(h.calls.length, 0);
});

test('one click starts once; ending the round leaves the plugin ready', async () => {
  const h = await panel(); await h.start(); await h.start();
  assert.equal(h.phase, 'playing'); assert.equal(h.calls.filter(c => c.command === 'start').length, 1);
  assert.equal(h.calls[0].payload.confirmed, true); assert.equal(h.calls[0].payload.shortcut, 'Ctrl+Shift+1');
  await h.stop(); await h.advance(10000);
  assert.equal(h.phase, 'standby'); assert.equal(h.state.ready, true); assert.equal(h.state.active, false);
  assert.equal(h.calls.filter(c => c.command === 'start').length, 1);
});

test('a late inactive context cannot undo the successful start display', async () => {
  const h = await panel(), release = h.hold('context'); await h.advance(2000);
  await h.start(); release(); await flush();
  assert.equal(h.phase, 'playing'); assert.equal(h.nodes.start.disabled, true); assert.equal(h.nodes.stop.disabled, false);
});

test('a context requested during start is discarded after the action completes', async () => {
  const h = await panel(), startReply = h.hold('start'), starting = h.start(); await flush();
  const contextReply = h.hold('context'); await h.advance(2000);
  startReply(); await starting; contextReply({state: {...h.state, active: false}}); await flush();
  assert.equal(h.phase, 'playing'); assert.equal(h.nodes.stop.disabled, false);
});

test('cancel during start wins over its delayed acknowledgement', async () => {
  const h = await panel(), release = h.hold('start'), starting = h.start(); await flush();
  assert.equal(h.phase, 'starting'); assert.equal(h.nodes.stop.disabled, false);
  await h.stop(); release(); await starting; await h.advance(10000);
  assert.equal(h.state.active, false); assert.equal(h.phase, 'standby');
  assert.equal(h.calls.filter(c => c.command === 'start').length, 1);
});

test('a late active context cannot resurrect the ended round display', async () => {
  const h = await panel(); await h.start(); const release = h.hold('context'); await h.advance(2000);
  await h.stop(); release(); await flush();
  assert.equal(h.phase, 'standby'); assert.equal(h.nodes.stop.disabled, true);
});

test('connection loss retains the end action and directs the user to real exits', async () => {
  const h = await panel(); await h.start(); h.offline(true); await h.advance(2000);
  assert.equal(h.phase, 'reconnecting'); assert.equal(h.nodes.start.disabled, true); assert.equal(h.nodes.stop.disabled, false);
  await h.stop(); assert.match(h.nodes.message.textContent, /安全词.*全局退出快捷键/);
  h.offline(false); h.state.active = false; await h.advance(2000); assert.equal(h.phase, 'standby');
});

test('the stopped plugin can be enabled from its static panel without starting a round', async () => {
  const h = await panel({ready: false});
  assert.equal(h.phase, 'disabled'); assert.equal(h.nodes.enable.disabled, false);
  await h.enable(); await h.advance(10000);
  assert.equal(h.phase, 'standby'); assert.equal(h.nodes.enable.disabled, false);
  assert.equal(h.nodes.enable.textContent, '一键关闭插件');
  assert.equal(h.state.ready, true); assert.equal(h.state.active, false);
  assert.deepEqual(h.calls.map(call => call.command), ['enable']);
});

test('plugin startup does not require an attached main renderer', async () => {
  const h = await panel({ready: false, desktop_ready: false, desktop: null}); await h.enable();
  assert.equal(h.phase, 'waiting'); assert.equal(h.nodes.start.disabled, true);
  assert.equal(h.state.ready, true); assert.equal(h.state.active, false);
  assert.deepEqual(h.calls.map(call => call.command), ['enable']);
});

test('a slow plugin startup disables duplicate startup, game start and key changes', async () => {
  const h = await panel({ready: false}), release = h.hold('enable'), enabling = h.enable(); await flush();
  assert.equal(h.phase, 'enabling'); assert.equal(h.nodes.enable.disabled, true); assert.equal(h.nodes.shortcut.disabled, true);
  await h.enable(); await h.start();
  release(); await enabling; assert.equal(h.phase, 'standby');
  assert.deepEqual(h.calls.map(call => call.command), ['enable']);
});

test('failed host startup leaves the plugin idle and reports the real error', async () => {
  const h = await panel({ready: false}); h.enableError(true); await h.enable();
  assert.equal(h.phase, 'disabled'); assert.equal(h.state.ready, false); assert.equal(h.state.active, false);
  assert.match(h.nodes.message.textContent, /启动权限不足/); assert.equal(h.nodes.enable.disabled, false);
  assert.deepEqual(h.calls.map(call => call.command), ['enable']);
});

test('a running host with unavailable context still offers shutdown', async () => {
  const h = await panel(); h.contextDown(true); await h.advance(2000);
  assert.equal(h.nodes.enable.disabled, false); assert.equal(h.nodes.start.disabled, true);
  assert.equal(h.nodes.enable.textContent, '一键关闭插件');
  await h.enable(); assert.equal(h.state.ready, false); assert.equal(h.phase, 'disabled');
  assert.deepEqual(h.calls.map(call => call.command), ['disable']);
});

test('successful host startup remains successful while waiting for its context', async () => {
  const h = await panel({ready: false}); h.contextDown(true); await h.enable();
  assert.equal(h.nodes.enable.disabled, false); assert.equal(h.nodes.start.disabled, true);
  assert.match(h.nodes.message.textContent, /插件已启动/); assert.equal(h.state.active, false);
  h.contextDown(false); await h.advance(2000); assert.equal(h.phase, 'standby');
});

test('one plugin button toggles standby off and on without ever starting a round', async () => {
  const h = await panel();
  assert.equal(h.nodes.enable.textContent, '一键关闭插件');
  await h.enable(); assert.equal(h.phase, 'disabled'); assert.equal(h.nodes.enable.textContent, '一键启动插件');
  await h.enable(); await h.advance(2000); assert.equal(h.phase, 'standby');
  assert.equal(h.state.active, false); assert.deepEqual(h.calls.map(call => call.command), ['disable', 'enable']);
});

test('closing the plugin retires its game first and re-enabling never resumes it', async () => {
  const h = await panel(); await h.start(); const rid = h.state.round_id;
  assert.equal(h.nodes.enable.disabled, false);
  await h.enable(); assert.equal(h.state.ready, false); assert.equal(h.state.active, false);
  assert.deepEqual(h.calls.map(call => call.command), ['start', 'stop', 'disable']);
  assert.equal(h.calls[1].payload.round_id, rid);
  await h.enable(); await h.advance(2000); assert.equal(h.phase, 'standby'); assert.equal(h.state.active, false);
});

test('closing the plugin cancels a pending game start and ignores its late success', async () => {
  const h = await panel(), release = h.hold('start'), starting = h.start(); await flush();
  await h.enable(); release(); await starting; await h.advance(2000);
  assert.equal(h.state.ready, false); assert.equal(h.state.active, false); assert.equal(h.phase, 'disabled');
  assert.equal(h.calls.filter(c => c.command === 'start').length, 1);
});

test('a rejected plugin shutdown is reported and leaves shutdown available to retry', async () => {
  const h = await panel(); h.disableError(true); await h.enable();
  assert.equal(h.phase, 'standby'); assert.equal(h.nodes.enable.textContent, '一键关闭插件');
  assert.equal(h.nodes.enable.disabled, false); assert.match(h.nodes.message.textContent, /插件关闭失败/);
});

test('a stale active context cannot undo a confirmed plugin shutdown', async () => {
  const h = await panel(); await h.start(); const release = h.hold('context'); await h.advance(2000);
  await h.enable(); release(); await flush(); assert.equal(h.phase, 'disabled');
  assert.equal(h.nodes.enable.textContent, '一键启动插件'); assert.equal(h.nodes.start.disabled, true);
});

test('observed runtime readiness confirms a slow startup even if its HTTP reply times out', async () => {
  const h = await panel({ready:false}), release = h.hold('enable'), starting = h.enable(); await flush();
  await h.advance(2000); release.timeout(); await starting;
  assert.equal(h.phase, 'standby'); assert.match(h.nodes.message.textContent, /插件已启动/);
  assert.doesNotMatch(h.nodes.message.textContent, /超时/); assert.equal(h.nodes.enable.textContent, '一键关闭插件');
});
