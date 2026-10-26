import test from 'node:test';
import assert from 'node:assert/strict';
import {quickDecision,tutorialKeywordDecision,interruptSupersededReply} from '../ui/quick-command.mjs';

const road = {game_active:true,body:{native_input_available:true}};
test('only affirmative keywords for the current tutorial step activate a lesson',()=>{
  for (const [waiting,action,words] of [
    ['FastenSeatbelt','fasten_seatbelt',['安全带','把安全带系好','系上安全带']],
    ['StartEngine','start_engine',['打火','点火','启动引擎']],
    ['Go','shift_drive',['出发','起步','油门踩到底','慢慢往前开','向前开一点','缓慢前行']],
  ]) {
    const state={...road,car:'TutorialSceneCarController',body:{native_input_available:true,
      tutorial:`教学中，等待 ${waiting}，当前动作 None`}};
    for (const word of words) assert.deepEqual(tutorialKeywordDecision(word,state).actions,[{action}],word);
    for (const text of ['你好','今天吃什么','不要系安全带','安全带系好了吗？','不要打火','引擎启动了吗？',
      '他说“出发”','如果准备好了就出发','别踩油门','油门踩到底吗？',
      '不要慢慢往前开','慢慢往前开吗？','他说“慢慢往前开”','如果准备好了就慢慢往前开'])
      assert.equal(tutorialKeywordDecision(text,state),null,text);
    if (waiting !== 'Go') assert.equal(tutorialKeywordDecision('慢慢往前开',state),null);
    assert.equal(tutorialKeywordDecision(words[0],{...state,body:{...state.body,native_input_available:false}}),null);
  }
  assert.equal(tutorialKeywordDecision('安全带',road),null);
});
test('clear directions change only steering; explicit speeds retain the other axis',()=>{
  assert.deepEqual(quickDecision('方向回正',road).actions,[{action:'drive',steering:0}]);
  assert.deepEqual(quickDecision('向左一点',road).actions,[{action:'drive',steering:-.12}]);
  assert.deepEqual(quickDecision('以五公里每小时前进',road).actions,[{action:'drive',target_speed:5}]);
  assert.deepEqual(quickDecision('倒车3公里每小时',road).actions,[{action:'drive',target_speed:-3}]);
});
test('ambiguous, quoted, conditional, negative and timed inputs retain model reasoning',()=>{
  for (const text of ['前进一点','加速','慢慢前进','慢慢往前开','前进5米','向左两秒','5公里每小时前进半秒然后刹车',
    '你会向左吗','不要向左','如果到了就回正','他说“向左”','刹车'])
    assert.equal(quickDecision(text,road),null,text);
});
test('scripted input locks and ended games cannot use the quick route',()=>{
  for (const state of [{body:{native_input_available:false}},{body:{}},{...road,game_active:false}])
    assert.equal(quickDecision('方向回正',state),null);
});
test('tutorial quick route permits only its pending lesson',()=>{
  const state = {...road,car:'TutorialSceneCarController',body:{native_input_available:true,
    tutorial:'教学中，等待 FastenSeatbelt，当前动作 None'}};
  assert.equal(quickDecision('系上安全带',state).actions[0].action,'fasten_seatbelt');
  for (const text of ['方向回正','打火','5公里每小时前进']) assert.equal(quickDecision(text,state),null);
  assert.equal(quickDecision('系上安全带',{...state,body:{...state.body,native_input_available:false}}),null);
});

test('repeating a completed tutorial step restores its confirmation through the same native action',()=>{
  for (const [current,text,action] of [['FastenSeatbelt','系上安全带','fasten_seatbelt'],['StartEngine','打火','start_engine']]) {
    const pending={...road,car:'TutorialSceneCarController',body:{native_input_available:true,
      tutorial:`教学中，安全带已系，引擎已启动，等待 None，当前动作 ${current}`,
      tutorial_state:{version:1,active:true,known:true,input_available:true,seatbelt_fastened:true,engine_started:true,
        waiting_step:'None',current_action:current,pending_command_speech:true,recoverable_step:current}}};
    assert.deepEqual(quickDecision(text,pending).actions,[{action}]);
    assert.equal(quickDecision('方向回正',pending),null);
  }
});

test('tutorial departure language uses only the native Go step',()=>{
  const go={...road,car:'TutorialSceneCarController',body:{native_input_available:true,
    tutorial:'教学中，安全带已系，引擎已启动，等待 Go，当前动作 StartEngine'}};
  for(const text of ['出发','起步','踩一下油门','前进'])
    assert.deepEqual(quickDecision(text,go).actions,[{action:'shift_drive'}],text);
  assert.equal(quickDecision('前进5公里每小时',go),null);
});
function fixture(replyGeneration=1,ownAudio=true) {
  const calls=[];
  const job={id:'reply-1',replyGeneration,controller:{abort:()=>calls.push('abort')},
    tracker:{fail:(id,status)=>calls.push([id,status])},
    sample:{requestId:'reply-1',speechId:'host-1'}};
  const player={NekoSpeechPlaybackState:{speechId:ownAudio?'host-1':'native-other'},
    appAudioPlayback:{interruptCurrentAssistantSpeech:()=>calls.push('interrupt')}};
  return {job,player,calls};
}
test('new commands retire their own ordinary reply with interrupted, never ended',()=>{
  const {job,player,calls}=fixture();
  assert.equal(interruptSupersededReply(job,player),true);
  assert.deepEqual(calls,['interrupt','abort',['reply-1','interrupted']]);
});
test('tutorial and native callback lines remain protected',()=>{
  const {job,player,calls}=fixture(null);
  assert.equal(interruptSupersededReply(job,player),false);assert.deepEqual(calls,[]);
});
test('an obsolete reply cannot interrupt unrelated host audio or claim playback completion',()=>{
  const {job,player,calls}=fixture(1,false);
  interruptSupersededReply(job,player);
  assert.deepEqual(calls,['abort',['reply-1','interrupted']]);
  job.sample=null;calls.length=0;interruptSupersededReply(job,player);
  assert.deepEqual(calls,['abort',['reply-1','interrupted']]);
});
