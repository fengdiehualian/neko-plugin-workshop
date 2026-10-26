import test from 'node:test';
import assert from 'node:assert/strict';
import {PossessionBrain,normalizeDecision} from '../ui/possession.mjs';

const decision=(actions=[])=>({line:'我先往前挪一点，再停下。',control:{body:{actions,emotion:'neutral'}}});
test('preserves ordered native-style wait and explicit stop',()=>{
  const result=normalizeDecision(decision([{action:'drive',target_speed:5,duration_ms:500},{action:'brake'}]));
  assert.equal(result.actions[0].duration_ms,500);assert.equal(result.actions[1].action,'brake');
});
test('chat changes no driving axis',()=>assert.deepEqual(normalizeDecision(decision()).actions,[]));
test('missing control never turns arbitrary model text into a driving command',()=>{
  assert.throws(()=>normalizeDecision({line:'我已经开过去了。'}));
});
test('rejects invalid numbers, unexpected fields and oversized plans before execution',()=>{
  for(const actions of [[{action:'drive',target_speed:NaN}],[{action:'drive',target_speed:61}],
    [{action:'drive',steering:-2}],[{action:'drive',distance:2}],[{action:'brake',target_speed:0}],
    [{action:'drive'}],Array(9).fill({action:'brake'}),
    [{action:'drive',target_speed:5,duration_ms:10000},{action:'drive',steering:0,duration_ms:10000}],
    [{action:'fasten_seatbelt'},{action:'drive',target_speed:5}]]) assert.throws(()=>normalizeDecision(decision(actions)));
});
test('host identity, authorized memory, road state and confirmed history reach the character model',async()=>{
  let payload;
  const brain=new PossessionBrain({dialogue:{request:async input=>{payload=input;return {data:decision()};}}});
  brain.setMemory({recent:'上次一起练车。',persona:'喜欢慢慢开。'});
  brain.remember('停下',normalizeDecision(decision()),{status:'executed'});
  await brain.decide({text:'你还紧张吗',state:{scene:'Mine',body:{situation:'前方有地雷'}}});
  assert.equal(payload.prompt.mode,'author-managed');
  assert.ok(payload.prompt.messages.some(m=>m.content.includes('上次一起练车')));
  assert.ok(payload.prompt.messages.at(-1).content.includes('前方有地雷'));
  assert.ok(payload.prompt.messages.some(m=>m.content.includes('executed')));
  brain.clear();assert.equal(brain.history.length,0);assert.deepEqual(brain.memory,{});
});
test('native dialogue and an already executed emergency stop cannot add driving',async()=>{
  const brain=new PossessionBrain({dialogue:{request:async()=>({data:decision([{action:'drive',target_speed:10}])})}});
  for(const kind of ['native_line','already_braked','tutorial_automatic','conversation_only']) await assert.rejects(brain.decide({kind,text:'你好'}));
});
test('request cancellation reaches host dialogue',async()=>{
  const controller=new AbortController();let received;
  const brain=new PossessionBrain({dialogue:{request:async(_,options)=>{received=options.signal;return {data:decision()};}}});
  await brain.decide({text:'你好',signal:controller.signal});assert.equal(received,controller.signal);
});

test('scripted line rewriting has one short attempt before original-text fallback',async()=>{
  let calls=0,deadline;
  const brain=new PossessionBrain({dialogue:{request:async(_,options)=>{
    calls++;deadline=options.timeoutMs;throw Object.assign(new Error('invalid control contract'),{code:'invalid_contract'});
  }}});
  await assert.rejects(brain.decide({kind:'native_line',text:'下一步请系安全带'}));
  assert.equal(calls,1);assert.equal(deadline,3000);
});
test('repairs one unexecuted format failure and leaves body application to caller',async()=>{
  let calls=0;
  const brain=new PossessionBrain({dialogue:{request:async()=>{
    if(++calls===1) throw Object.assign(new Error('control.body.actions[0] missing required field'),{code:'invalid_contract'});
    return {data:decision([{action:'drive',target_speed:5,duration_ms:500},{action:'brake'}])};
  }}});
  const result=await brain.decide({text:'前进半秒然后停'});
  assert.equal(calls,2);assert.equal(result.actions.length,2);
});
test('provider failures are surfaced without automatic regeneration',async()=>{
  let calls=0;
  const brain=new PossessionBrain({dialogue:{request:async()=>{calls++;throw new Error('provider_unavailable');}}});
  await assert.rejects(brain.decide({text:'你好'}));assert.equal(calls,1);
});

test('natural language reaches the model unchanged with actual vehicle limits capped by the control contract',async()=>{
  let context;
  const brain=new PossessionBrain({dialogue:{request:async payload=>{
    context=JSON.parse(payload.prompt.messages.at(-1).content);
    return {data:decision()};
  }}});
  for(const [forward,reverse,expectedForward,expectedReverse] of [[72,21.6,60,-20],[45,14.4,45,-14.4],[NaN,-5,null,null]]) {
    await brain.decide({text:'油门踩到底',state:{game_active:true,telemetry:{MaxForwardKmh:forward,MaxReverseKmh:reverse,
      SpeedKmh:7,TargetSpeedKmh:12},body:{native_input_available:true}}});
    assert.equal(context.text,'油门踩到底');assert.equal(context.game_active,true);
    assert.deepEqual(context.control_limits,{forward_max_kmh:expectedForward,reverse_min_kmh:expectedReverse,steering_min:-1,steering_max:1});
    assert.equal(context.telemetry.TargetSpeedKmh,12);
  }
});
