import test from 'node:test';
import assert from 'node:assert/strict';
import {tutorialState,tutorialHint,tutorialPlaybackBusy,validateTutorialActions} from '../ui/tutorial.mjs';
import {PossessionBrain} from '../ui/possession.mjs';

const state = (waiting='Go',available=true,current=available?'StartEngine':'Go',pending=false) => ({car:'TutorialSceneCarController',body:{
  tutorial:`教学中，安全带已系，引擎已启动，等待 ${waiting}，当前动作 ${available?'StartEngine':'Go'}`,
  native_input_available:available,
  tutorial_state:{version:1,active:true,known:true,input_available:available,waiting_step:waiting,current_action:current,
    seatbelt_fastened:true,engine_started:true,pending_command_speech:pending,recoverable_step:pending?current:'',speech:{}},
}});
test('final tutorial request uses the native Go step, never continuous driving',()=>{
  assert.equal(tutorialState(state()).allowedAction,'shift_drive');
  validateTutorialActions([{action:'shift_drive'}],state());
  assert.throws(()=>validateTutorialActions([{action:'drive',target_speed:5}],state()),{code:'tutorial_action_mismatch'});
});

test('microphone exercise exposes input separately from driving lessons',()=>{
  const progress=state('None',true,'NotSet');
  progress.body.tutorial_state.microphone_stage='waiting_input';
  assert.equal(tutorialState(progress).phase,'microphone_input');
  assert.equal(tutorialState(progress).microphoneInput,true);
  assert.equal(tutorialState(progress).allowedAction,null);
  assert.match(tutorialHint(progress),/试说一句话/);
  assert.throws(()=>validateTutorialActions([{action:'fasten_seatbelt'}],progress));
  progress.body.tutorial_state.microphone_stage='awaiting_speech';
  assert.equal(tutorialState(progress).phase,'awaiting_speech');
  progress.body.tutorial_state.microphone_stage='intro';
  assert.equal(tutorialState(progress).microphoneInput,false);
  assert.equal(tutorialState(progress).phase,'microphone_intro');
});

test('native completion panel takes precedence over old speech or input state',()=>{
  const progress=state('None',false,'Go');
  progress.body.tutorial_state.completed=true;
  progress.body.tutorial_state.speech.retryable=1;
  assert.equal(tutorialState(progress).phase,'completed');
  assert.equal(tutorialState(progress).allowedAction,null);
  assert.match(tutorialHint(progress,true),/教程已通过.*下一关/);
  assert.throws(()=>validateTutorialActions([{action:'shift_drive'}],progress));
});
test('interrupted final speech offers continuation without bypassing the script',()=>{
  const locked=state('None',false);
  assert.match(tutorialHint(locked,true),/继续剧情语音/);
  assert.equal(tutorialState(locked).allowedAction,null);
  assert.throws(()=>validateTutorialActions([{action:'shift_drive'}],locked));
  validateTutorialActions([],locked);
});
test('ordinary driving outside the tutorial is unaffected',()=>{
  validateTutorialActions([{action:'drive',target_speed:5}],{car:'SLaneMineCarController'});
  assert.equal(tutorialHint({car:'SLaneMineCarController'}),'');
});

test('completed seatbelt/engine can recover their own pending confirmation without unlocking driving',()=>{
  for (const current of ['FastenSeatbelt','StartEngine']) {
    const pending=state('None',true,current,true);
    const expected=current==='FastenSeatbelt'?'fasten_seatbelt':'start_engine';
    assert.equal(tutorialState(pending).recovering,true);
    assert.equal(tutorialState(pending).allowedAction,expected);
    assert.match(tutorialHint(pending),/恢复播报/);
    validateTutorialActions([{action:expected}],pending);
    assert.throws(()=>validateTutorialActions([{action:'drive',target_speed:5}],pending));
    pending.body.native_input_available=false;
    pending.body.tutorial_state.input_available=false;
    assert.equal(tutorialState(pending).recovering,false);
    assert.throws(()=>validateTutorialActions([{action:expected}],pending));
  }
});

test('triggered flags without a pending native callback do not imply stuck speech',()=>{
  const progress=state('None',true,'StartEngine',false);
  assert.equal(tutorialState(progress).phase,'transition');
  assert.equal(tutorialState(progress).recovering,false);
  assert.match(tutorialHint(progress),/切换步骤/);
  assert.throws(()=>validateTutorialActions([{action:'start_engine'}],progress));
});
test('native tutorial fields take precedence over conflicting display text and report real playback phases',()=>{
  const progress=state('StartEngine',true,'FastenSeatbelt');
  progress.body.tutorial='教学中，安全带未系，引擎未启动，等待 Go，当前动作 None';
  assert.equal(tutorialState(progress).allowedAction,'start_engine');
  assert.equal(tutorialState(progress).seatbelt,true);
  progress.body.tutorial_state.speech.playing=1;
  assert.equal(tutorialState(progress).phase,'speaking');
  assert.match(tutorialHint(progress),/正在播放/);
  progress.body.tutorial_state.speech.retryable=1;
  assert.equal(tutorialState(progress).phase,'speech_failed');
  assert.match(tutorialHint(progress),/播报未完成/);
});
test('missing or unrecognized tutorial data is unknown, not a claimed animation or completed action',()=>{
  const progress=state();delete progress.body.tutorial_state.current_action;
  assert.equal(tutorialState(progress).known,false);
  assert.equal(tutorialState(progress).allowedAction,null);
  assert.match(tutorialHint(progress),/尚未确认/);
  assert.throws(()=>validateTutorialActions([{action:'shift_drive'}],progress));
  assert.equal(tutorialState({car:'TutorialSceneCarController',body:{}}).phase,'unknown');
});
test('a previously triggered lesson is not permission to repeat it at a different step',()=>{
  assert.throws(()=>validateTutorialActions([{action:'fasten_seatbelt'}],state('Go')));
});
test('an older pending confirmation cannot mask the next native waiting step',()=>{
  const progress=state('StartEngine',true,'FastenSeatbelt',true);
  assert.equal(tutorialState(progress).recovering,false);
  assert.equal(tutorialState(progress).allowedAction,'start_engine');
  assert.equal(tutorialState(progress).phase,'waiting');
});
test('an unexecuted incorrect tutorial decision gets one specific repair',async()=>{
  let calls=0,repair='';
  const brain=new PossessionBrain({dialogue:{request:async input=>{
    calls++;repair=input.prompt.messages.at(-1).content;
    return {data:{line:'准备出发。',control:{body:{actions:[calls===1
      ? {action:'drive',target_speed:5}:{action:'shift_drive'}]}}}};
  }}});
  const result=await brain.decide({text:'踩油门出发',state:state()});
  assert.equal(calls,2);assert.match(repair,/Go.*shift_drive/);
  assert.deepEqual(result.actions,[{action:'shift_drive'}]);
});

test('tutorial protects animation, speech, transitions and the microphone introduction after a keyword',()=>{
  assert.equal(tutorialPlaybackBusy(state('None',false,'Go')),true);
  const progress=state('None',true,'StartEngine',true);
  assert.equal(tutorialPlaybackBusy(progress),true);
  progress.body.tutorial_state.speech.playing=1;
  assert.equal(tutorialPlaybackBusy(progress),true);
  progress.body.tutorial_state.speech={retryable:1};
  assert.equal(tutorialPlaybackBusy(progress),false,'speech recovery must remain available');
  const microphone=state('None',true,'NotSet');microphone.body.tutorial_state.microphone_stage='intro';
  assert.equal(tutorialPlaybackBusy(microphone),true);
  microphone.body.tutorial_state.microphone_stage='waiting_input';
  assert.equal(tutorialPlaybackBusy(microphone),false);
});

test('a waiting lesson still requires its keyword, and a completed tutorial releases the input gate',()=>{
  const waiting=state();assert.equal(tutorialPlaybackBusy(waiting),false);
  assert.match(tutorialHint(waiting),/有效口令.*完整执行/);
  waiting.body.tutorial_state.speech.playing=1;
  assert.equal(tutorialPlaybackBusy(waiting),false,'ordinary chat must not block the next ready keyword');
  waiting.body.tutorial_state.completed=true;
  assert.equal(tutorialPlaybackBusy(waiting),false);assert.equal(tutorialPlaybackBusy({}),false);
});
