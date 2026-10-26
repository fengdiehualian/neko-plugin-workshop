const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the actual chat-side bridge with a deliberately delayed context reply.
// Safety-word input must work even before a newly opened window learns a round ID.
function chatBridge(round = null) {
  const listeners = {}, calls = [], broadcasts = [];
  let reply, contextReads = 0;
  const firstContext = new Promise(resolve => { reply = resolve; });
  const window = {nekoChatWindow: {}, addEventListener() {}};
  const document = {
    readyState: 'complete', currentScript: {src: 'http://127.0.0.1:48916/plugin/neko_ads/ui/desktop-bridge.js'},
    addEventListener(name, fn) { listeners[name] = fn; },
  };
  class Channel {
    postMessage(message) { broadcasts.push(message); }
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/desktop-bridge.js'), 'utf8'), {
    window, document, location: {pathname: '/chat'}, URL, AbortController,
    crypto:{randomUUID:()=> 'test-chat'}, performance:{now:()=>0},
    localStorage:{getItem:key=>key === 'neko-ads-stop-epoch' ? 'scoped-epoch' : key === 'neko-ads-current-round' && round ? JSON.stringify({round_id:round}) : null},
    BroadcastChannel: Channel, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options) => {
      if (url.includes('/context')) { contextReads++; return firstContext; }
      calls.push(JSON.parse(options.body));
      return {ok:true,json:async()=>({result:{data:{active:false,stop_epoch:'next-epoch',stopped_round_id:round}}})};
    },
  });
  return {
    input: text => listeners.input({target: {type: 'text', value: text}}),
    compose: text => listeners.compositionend({data: text}),
    context: state => reply({json: async () => ({state})}),
    calls, broadcasts,
    get contextReads() { return contextReads; },
  };
}

test('chat safety word uses the shared stop generation before its first state reply', async () => {
  const bridge = chatBridge();
  bridge.input('广告下班');
  assert.equal(bridge.calls.length, 1);
  assert.equal(bridge.calls[0].args.command, 'stop');
  assert.deepEqual(bridge.calls[0].args.payload, {stop_epoch:'scoped-epoch'});
  assert.deepEqual(JSON.parse(JSON.stringify(bridge.broadcasts)), [{type:'stop-epoch',stop_epoch:'scoped-epoch'}]);
  bridge.context({active: true, round_id: 'late-old-round'});
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(bridge.calls.length, 1, 'a delayed state reply cannot restart anything');
});

test('chat IME safety word does not rely on a cached old round ID', async () => {
  const bridge = chatBridge('shared-current-round');
  bridge.context({active: true, round_id: 'stale-round'});
  await Promise.resolve();
  await Promise.resolve();
  bridge.compose('广 告 下 班！');
  assert.equal(bridge.calls.at(-1).args.command, 'stop');
  assert.deepEqual(bridge.calls.at(-1).args.payload, {round_id:'shared-current-round'});
});

test('ordinary chat input never calls plugin actions', () => {
  const bridge = chatBridge();
  bridge.input('今天的广告很有意思');
  bridge.compose('广告');
  assert.equal(bridge.calls.length, 0);
  assert.equal(bridge.contextReads, 0, 'chat safety listeners need no background context polling');
});
