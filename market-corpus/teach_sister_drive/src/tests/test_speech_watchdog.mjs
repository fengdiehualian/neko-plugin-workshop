import test from 'node:test';
import assert from 'node:assert/strict';
import {PlaybackTracker} from '../ui/playback.mjs';
import {InteractionTimings} from '../ui/interaction-timing.mjs';

const flush = async () => { for (let i=0;i<16;i++) await Promise.resolve(); };
function setup() {
  let now = 1000, nextTimer = 0;
  const timers = new Map(), sent = [], progress = [];
  const tracker = new PlaybackTracker({now:()=>now,send:body=>sent.push(body.status),onProgress:event=>progress.push(event),
    setTimer:(callback,ms)=>{const id=++nextTimer;timers.set(id,{callback,at:now+ms});return id;},
    clearTimer:id=>timers.delete(id)});
  const advance = ms => {
    const end=now+ms;
    for (;;) {
      const next=[...timers].filter(([,timer])=>timer.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
      if (!next) break;
      now=next[1].at;timers.delete(next[0]);next[1].callback();
    }
    now=end;
  };
  const state = patch => ({requestId:'speech',speechId:'host',turnId:'turn',playbackTurnId:'turn',updatedAt:now,
    active:true,audioContextState:'running',audioContextTime:now/1000,playbackStartAudioTime:.5,...patch});
  return {tracker,sent,progress,advance,state,timers};
}

test('no first audio warns at five seconds and fails at fifteen; late success never advances the lesson',async()=>{
  const h=setup(), done=h.tracker.begin('speech');
  h.advance(5000);await flush();
  assert.equal(h.progress.at(-1).phase,'preparation_slow');assert.deepEqual(h.sent,[]);
  h.advance(10000);
  assert.equal(await done,'failed');assert.deepEqual(h.sent,['failed']);
  assert.equal(h.progress.at(-1).reason,'first_audio_timeout');
  h.tracker.accept(h.state());h.tracker.generated('speech',{ok:true});h.tracker.playerEnded('turn',true);
  await flush();assert.deepEqual(h.sent,['failed']);assert.equal(h.timers.size,0);
});

test('a long narration has a playback deadline separate from first audio',async()=>{
  const h=setup(), done=h.tracker.begin('speech');
  h.advance(14000);h.tracker.accept(h.state());h.tracker.generated('speech',{ok:true});
  for(let i=0;i<10;i++){h.advance(3000);h.tracker.accept(h.state());}
  assert.equal(h.progress.some(event=>event.phase==='failed'),false);
  assert.ok(h.timers.size<=3,'progress timers must remain bounded');
  h.tracker.playerEnded('turn',true);assert.equal(await done,'ended');assert.equal(h.timers.size,0);
});

test('heartbeat samples with a frozen audio clock cannot conceal stalled playback',async()=>{
  const h=setup(), done=h.tracker.begin('speech');
  h.tracker.accept(h.state({audioContextTime:1}));h.tracker.generated('speech',{ok:true});
  for(let i=0;i<11;i++){h.advance(1000);h.tracker.accept(h.state({audioContextTime:1}));}
  assert.ok(h.progress.some(event=>event.phase==='playback_slow'));
  h.advance(1000);assert.equal(await done,'failed');
  assert.equal(h.progress.at(-1).reason,'playback_stalled');assert.equal(h.sent.includes('ended'),false);
});

test('a running context clock and pending decoder work do not count as audible progress',async()=>{
  const h=setup(), done=h.tracker.begin('speech');
  h.tracker.accept(h.state({remainingSeconds:1}));h.tracker.generated('speech',{ok:true});
  for(let i=0;i<11;i++){h.advance(1000);h.tracker.accept(h.state({remainingSeconds:0,pendingAudioWork:true}));}
  h.advance(1000);assert.equal(await done,'failed');
  assert.equal(h.progress.at(-1).reason,'playback_stalled');assert.equal(h.sent.includes('ended'),false);
});

test('real playback progress recovers a warning without replaying or failing the line',async()=>{
  const h=setup(), done=h.tracker.begin('speech');
  h.tracker.accept(h.state());h.tracker.generated('speech',{ok:true});
  h.advance(6000);assert.equal(h.progress.at(-1).phase,'playback_slow');
  h.tracker.accept(h.state());assert.equal(h.progress.at(-1).phase,'resumed');
  h.advance(6000);h.tracker.accept(h.state());h.tracker.playerEnded('turn',true);
  assert.equal(await done,'ended');assert.equal(h.sent.includes('failed'),false);
});

test('completed playback waits for HTTP confirmation under its own bounded deadline',async()=>{
  const h=setup(), done=h.tracker.begin('speech');
  h.tracker.accept(h.state());h.tracker.playerEnded('turn',true);
  assert.equal(h.progress.at(-1).phase,'awaiting_confirmation');
  h.advance(12000);await flush();assert.deepEqual(h.sent,['started']);
  h.advance(3000);assert.equal(await done,'failed');
  assert.equal(h.progress.at(-1).reason,'completion_confirmation_timeout');
});

test('retired scene timers cannot fail a new scene',async()=>{
  const h=setup(), old=h.tracker.begin('speech',{scene:'old'});
  h.tracker.close({notify:false});assert.equal(await old,'interrupted');
  const fresh=h.tracker.begin('fresh',{scene:'new'});
  h.advance(5000);assert.equal(h.progress.at(-1).id,'fresh');
  h.tracker.close({notify:false});assert.equal(await fresh,'interrupted');assert.equal(h.timers.size,0);
});

test('timing exports contain only bounded structural fields, without user content or external identifiers',()=>{
  let now=100;const timing=new InteractionTimings({now:()=>now,limit:2});
  const id=timing.begin('secret character / user dialogue');
  now=125;timing.mark(id,'decision_ready','secret API key');timing.mark(id,'secret transcript');
  now=150;timing.finish(id,'failed','first_audio_timeout');
  assert.deepEqual(timing.get(id),{id:'interaction-1',source:'text',outcome:'failed',events:[
    {stage:'received',ms:0},{stage:'decision_ready',ms:25},{stage:'failed',ms:50,reason:'first_audio_timeout'}]});
  assert.equal(JSON.stringify(timing.snapshot()).includes('secret'),false);
  assert.equal(timing.snapshot().asrMeasured,false);
  const copy=timing.get(id);copy.events[0].stage='changed';assert.equal(timing.get(id).events[0].stage,'received');
  timing.begin('voice');timing.begin('recovery');assert.equal(timing.get(id),null);assert.equal(timing.snapshot().entries.length,2);
});

test('diagnostics report failed feedback instead of a successful lesson completion',async()=>{
  const progress=[];
  const tracker=new PlaybackTracker({send:body=>{if(body.status==='ended')throw new Error('offline');},onProgress:event=>progress.push(event)});
  const done=tracker.begin('speech');
  tracker.accept({requestId:'speech',turnId:'turn',playbackTurnId:'turn',updatedAt:Date.now(),active:true,
    audioContextState:'running',audioContextTime:2,playbackStartAudioTime:1});
  tracker.generated('speech',{ok:true});tracker.playerEnded('turn',true);
  assert.equal(await done,'failed');
  assert.equal(progress.some(event=>event.phase==='ended'),false);
  assert.equal(progress.at(-1).reason,'feedback_failed');
});
