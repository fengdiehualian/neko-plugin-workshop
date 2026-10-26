import assert from 'node:assert/strict';
import test from 'node:test';
import {applyConnectionPreset} from '../ui/connection-presets.js';

test('Qiandeng preset only returns a new draft and preserves explicit authentication and unrelated settings', () => {
  const original = {server:{host:'localhost',port:25565,auth:'microsoft',username:'my-account'},operator:{port:0},autonomy:{enabled:false}};
  const before = structuredClone(original), draft = applyConnectionPreset(original, 'qiandeng');
  assert.deepEqual(original, before);
  assert.deepEqual(draft.server, {connection_type:'custom',host:'micro.kangqiang.site',port:25702,auth:'microsoft',version:'1.21.1',username:'my-account'});
  assert.deepEqual(draft.autonomy, {enabled:false});
  assert.deepEqual(draft.operator, {port:0});
  assert.throws(() => applyConnectionPreset(original, 'unknown'));
});

test('blank account stays blank and auth stays explicit', () => {
  const original = {server:{username:'',auth:'offline'}};
  const draft = applyConnectionPreset(original, 'qiandeng');
  assert.equal(draft.server.username, '');
  assert.equal(draft.server.auth, 'offline');
  assert.deepEqual(original, {server:{username:'',auth:'offline'}});
});
