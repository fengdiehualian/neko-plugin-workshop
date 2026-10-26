import test from 'node:test';
import assert from 'node:assert/strict';
import {PlaybackTracker} from '../ui/playback.mjs';

test('retiring a scene does not send new playback acknowledgements into the next scene',async()=>{
  const sent=[];
  const tracker=new PlaybackTracker({send:async value=>sent.push(value)});
  const finished=tracker.begin('old-scene',{scene:'tutorial'});
  tracker.close({notify:false});
  assert.equal(await finished,'interrupted');
  assert.deepEqual(sent,[]);
});

test('late playback failures retain their original scene for error filtering',async()=>{
  const scope={scene:'tutorial',token:1};let reported;
  const tracker=new PlaybackTracker({send:async()=>{throw new Error('speech belongs to previous scene')},
    onError:(_,context)=>{reported=context}});
  const finished=tracker.begin('old-scene',scope);
  tracker.fail('old-scene');
  assert.equal(await finished,'failed');
  assert.equal(reported,scope);
});

const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
function setup(send) {
  const messages = [], timers = new Set();
  const tracker = new PlaybackTracker({send:send || (body => { messages.push(body); }), now:() => 1000,
    setTimer:fn => { timers.add(fn); return fn; }, clearTimer:fn => timers.delete(fn)});
  const state = patch => ({requestId:'own-1', speechId:'host-1', turnId:'turn-1', playbackTurnId:'turn-1', updatedAt:1000,
    active:true,audioContextState:'running',audioContextTime:1,playbackStartAudioTime:.5,
    remainingSeconds:2,mouthFrame:{rms:.2}, ...patch});
  return {tracker,messages,timers,state};
}

test('HTTP success and empty inter-chunk gaps never complete native tutorial', async () => {
  const {tracker,messages,state} = setup();
  const done = tracker.begin('own-1');
  tracker.accept(state());
  tracker.generated('own-1', {ok:true,data:{speech_id:'host-1',audio_sent:true}});
  tracker.accept(state({active:false,remainingSeconds:0,reason:'source_ended'}));
  await flush();
  assert.deepEqual(messages.map(x=>x.status), ['started']);
  tracker.accept(state({reason:'chunk_scheduled'}));
  tracker.playerEnded('turn-1', true);
  assert.equal(await done, 'ended');
  assert.deepEqual(messages.map(x=>x.status), ['started','playing','ended']);
});

test('forced native end without audio_done does not complete', async () => {
  const {tracker,messages,state} = setup();
  const done = tracker.begin('own-1');
  tracker.accept(state());
  tracker.generated('own-1', {ok:true});
  tracker.playerEnded('turn-1', false);
  await flush();
  assert.deepEqual(messages.map(x=>x.status), ['started']);
  tracker.close();
  assert.equal(await done, 'interrupted');
});

test('native end may arrive before HTTP response, but requires matching start', async () => {
  const {tracker,messages,state} = setup();
  const done = tracker.begin('own-1');
  tracker.accept(state());
  tracker.playerEnded('turn-1', true);
  await flush();
  assert.equal(messages.length, 1);
  tracker.generated('own-1', {ok:true});
  assert.equal(await done, 'ended');
});

test('stale, unrelated and suspended player samples never start speech', async () => {
  const {tracker,messages,state} = setup();
  const done = tracker.begin('own-1');
  tracker.accept(state({requestId:'other',speechId:'other'}));
  tracker.accept(state({updatedAt:100}));
  tracker.accept(state({audioContextState:'suspended'}));
  tracker.accept(state({playbackStartAudioTime:0,playbackTurnId:'',pendingAudioWork:true}));
  tracker.generated('own-1', {ok:true});
  tracker.playerEnded('turn-1', true);
  await flush();
  assert.equal(messages.length, 0);
  tracker.close();
  assert.equal(await done, 'interrupted');
});

test('real cancellation never becomes completion', async () => {
  const {tracker,messages,state} = setup();
  const done = tracker.begin('own-1');
  tracker.accept(state());
  tracker.accept(state({active:false,reason:'speech_cancel'}));
  tracker.playerEnded('turn-1', true);
  tracker.generated('own-1', {ok:true});
  assert.equal(await done, 'interrupted');
  assert.deepEqual(messages.map(x=>x.status), ['started','interrupted']);
});

test('slow HTTP preserves start and terminal feedback order', async () => {
  const messages = [];
  let release;
  const gate = new Promise(resolve=>{ release=resolve; });
  const {tracker,state} = setup(async body => { messages.push(body.status); if(body.status==='started') await gate; });
  const done = tracker.begin('own-1');
  tracker.accept(state());
  tracker.generated('own-1', {ok:true});
  tracker.playerEnded('turn-1', true);
  await flush();
  assert.deepEqual(messages, ['started']);
  release();
  assert.equal(await done, 'ended');
  assert.deepEqual(messages, ['started','ended']);
});

test('failed start acknowledgement prevents a success acknowledgement', async () => {
  const messages=[];
  const {tracker,state} = setup(body=>{messages.push(body.status); if(body.status==='started') throw new Error('offline');});
  const done = tracker.begin('own-1');
  tracker.accept(state());
  await flush();
  tracker.generated('own-1', {ok:true});
  tracker.playerEnded('turn-1', true);
  assert.equal(await done, 'failed');
  assert.deepEqual(messages, ['started']);
});

test('completion racing a rejected start is failed without sending ended', async () => {
  const messages=[];
  const {tracker,state} = setup(body=>{messages.push(body.status); throw new Error('offline');});
  const done = tracker.begin('own-1');
  tracker.accept(state());
  tracker.generated('own-1', {ok:true});
  tracker.playerEnded('turn-1', true);
  assert.equal(await done, 'failed');
  assert.deepEqual(messages, ['started']);
});

test('default browser timers keep the Window receiver', async () => {
  const previousSet = globalThis.setTimeout, previousClear = globalThis.clearTimeout;
  try {
    globalThis.setTimeout = function () { assert.equal(this, globalThis); return 1; };
    globalThis.clearTimeout = function () { assert.equal(this, globalThis); };
    const tracker = new PlaybackTracker({send:async()=>{}});
    const done = tracker.begin('timer');
    tracker.close();
    assert.equal(await done, 'interrupted');
  } finally { globalThis.setTimeout=previousSet; globalThis.clearTimeout=previousClear; }
});
