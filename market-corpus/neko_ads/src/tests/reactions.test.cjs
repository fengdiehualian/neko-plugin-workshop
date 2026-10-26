const {test} = require('node:test');
const assert = require('node:assert/strict');
const {ReactionQueue, Game} = require('../static/engine.js');
const flush = async () => { for (let i=0; i<12; i++) await Promise.resolve(); };
function queue() {
  let now=0, next=0, cancelled=0; const timers=new Map(), played=[], releases=[];
  const speaker = new ReactionQueue({now:()=>now, schedule:(fn,ms)=>{const id=++next;timers.set(id,{fn,at:now+ms});return id;}, unschedule:id=>timers.delete(id),
    cancel:()=>cancelled++, play:(event,valid)=>{played.push({event,valid});return new Promise(resolve=>releases.push(resolve));}});
  return {speaker,played,releases,get cancelled(){return cancelled;},async advance(ms){now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);t.fn();}await flush();}};
}
test('new reactions never interrupt the current line and only the latest one waits', async()=>{
  const h=queue();h.speaker.request('start');h.speaker.request('fake');h.speaker.request('closed');
  assert.deepEqual(h.played.map(x=>x.event),['start']);assert.equal(h.cancelled,0);
  await h.advance(2400);h.releases.shift()();await flush();
  assert.deepEqual(h.played.map(x=>x.event),['start','closed']);h.speaker.clear();
});
test('cooldown waits without cancelling audio; old pending reactions expire',async()=>{
  const h=queue();h.speaker.request('start');h.releases.shift()();await flush();h.speaker.request('fake');
  await h.advance(2199);assert.equal(h.played.length,1);await h.advance(1);assert.equal(h.played.length,2);
  h.speaker.request('streak');await h.advance(5000);h.releases.shift()();await flush();
  assert.equal(h.played.length,2);h.speaker.clear();
});
test('exit invalidates waiting and in-flight work, including late completion',async()=>{
  const h=queue();h.speaker.request('start');h.speaker.request('fake');const old=h.played[0].valid;
  h.speaker.clear();assert.equal(old(),false);h.releases.shift()();await flush();await h.advance(10000);
  assert.equal(h.played.length,1);assert.equal(h.cancelled,1);
  h.speaker.request('view');assert.equal(h.played.length,2);h.speaker.clear();
});
test('streak feedback is occasional; ordinary and split closes keep their own reactions',()=>{
  let now=0;const events=[];const game=new Game({now:()=>now,schedule:()=>1,cancel(){},onFeedback:(_line,_speak,event)=>events.push(event)});game.start();
  function close(trick='plain'){const id=++game.nextId;game.cards=[{id,trick}];game.close(id);}
  close();close();close();close();close('split');close();close();
  assert.deepEqual(events.slice(1),['closed','closed','streak','closed','split','closed','closed']);
  now=21000;close();assert.equal(events.at(-1),'streak');
  game.cards=[{id:999,trick:'fake',tries:0}];game.close(999);assert.equal(game.successStreak,0);game.stop();
});
