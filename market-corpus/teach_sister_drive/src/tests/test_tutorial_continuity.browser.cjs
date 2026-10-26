// Runs the actual adapter with isolated native and speech contracts. No live
// game commands, microphone, model calls or synthetic mission-clear shortcut.
const test=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('../../../../frontend/plugin-manager/node_modules/@playwright/test');
const {serveLocalStaticAssets}=require('./browser-assets.cjs');

async function openLesson(page) {
  await serveLocalStaticAssets(page.context());
  await page.goto('http://localhost:48911/static/game/teach-sister-drive/index.html');
  await page.clock.install();
  await page.evaluate(async()=>{
    await window.nekoMiniGameSameOriginHostReady;
    const probe=window.__continuity={plans:[],speaks:[],feedback:[],commands:0,models:0,turns:0,aborts:0,abortedIds:[],receivedTexts:[],
      mode:'valid',memoryPending:false,delayReceipt:false,events:[]};
    const tutorial={version:1,active:true,known:true,input_available:true,seatbelt_fastened:false,engine_started:false,
      pending_command_speech:false,waiting_step:'FastenSeatbelt',current_action:'NotSet',microphone_stage:'',speech:{}};
    const state=probe.state={ok:true,protocol_version:2,possession_available:true,scene_id:'lesson:1',scene:'Tutorial',
      car:'TutorialSceneCarController',game_active:true,body:{native_input_available:true,tutorial_state:tutorial}};
    const native=window.fetch;
    window.fetch=async(url,options={})=>{
      if(!String(url).startsWith('http://127.0.0.1:8765'))return native(url,options);
      const path=new URL(url).pathname,body=options.body?JSON.parse(options.body):{};
      let result={ok:true};
      if(path==='/status'||path==='/session')result=state;
      else if(path==='/events')result={ok:true,scene_id:state.scene_id,session_id:'lesson-session',cursor:0,events:probe.events.splice(0)};
      else if(path==='/neko-context'){probe.memoryPending=true;return new Promise(()=>{});}
      else if(path==='/turn')probe.turns++;
      else if(path==='/command') {
        probe.commands++;result={ok:true,status:'executed',scene_id:state.scene_id,request_id:body.request_id};
      }
      else if(path==='/plan') {
        probe.plans.push(body);
        if(body.actions.length) {
          const expected={FastenSeatbelt:'fasten_seatbelt',StartEngine:'start_engine',Go:'shift_drive'}[tutorial.waiting_step];
          if(body.actions.length!==1 || body.actions[0].action!==expected)
            return new Response(JSON.stringify({ok:false,error:'wrong_native_step'}));
          const step=tutorial.waiting_step;tutorial.current_action=step;tutorial.waiting_step='None';
          tutorial.seatbelt_fastened ||= step==='FastenSeatbelt';tutorial.engine_started ||= step==='StartEngine';
          tutorial.pending_command_speech=step!=='Go';tutorial.input_available=step!=='Go';
          if(step!=='Go')probe.lessonSpeechId=body.request_id;
          tutorial.speech={queued:step!=='Go'?1:0};
          result={ok:true,status:'executed',scene_id:state.scene_id,request_id:body.request_id,
            action_count:1,requires_speech_completion:step!=='Go'};
          if(probe.delayReceipt)return new Promise(resolve=>{probe.releaseReceipt=()=>resolve(new Response(JSON.stringify(result)));});
        } else result={ok:true,status:'executed',scene_id:state.scene_id,request_id:body.request_id,action_count:0};
      } else if(path==='/speech')result={ok:true,speech_id:body.request_id};
      else if(path==='/playback') {
        probe.feedback.push(body);
        if(body.status==='started')tutorial.speech={playing:1};
        if(body.status==='ended' && body.speech_id===probe.lessonSpeechId && tutorial.pending_command_speech) {
          tutorial.pending_command_speech=false;tutorial.speech={};
          tutorial.waiting_step=tutorial.current_action==='FastenSeatbelt'?'StartEngine':'Go';
        }
      }
      return new Response(JSON.stringify(result));
    };
    window.appState={socket:{readyState:1},gameRouteInstanceId:'lesson-route',incomingAudioEpoch:1};
    window.appAudioPlayback={pendingAudioCountForSpeech:()=>0};window.lanlan_config={lanlan_name:'Test'};
    window.open=()=>window;
    const tag=document.createElement('script');tag.type='application/json';tag.src='/app-game-voice-control.js';document.head.append(tag);
    window.nekoMiniGameSameOriginHostReady=Promise.resolve(()=>({}));
    window.NekoMiniGame={connect:async()=>({
      runtime:{session:{id:'lesson-session',routeInstanceId:'lesson-route'},bindCharacter:async()=>({name:'Test'}),configure(){},start:async()=>{},end:async()=>{}},
      events:{on(){}},dispose(){},
      voice:{query:async()=>({ok:true,available:true}),onTranscript:callback=>{probe.transcript=callback;},onState(){},onError(){}},
      dialogue:{request:async input=>{
        probe.models++;probe.lastKind=input.event.kind;
        const context=JSON.parse([...input.prompt.messages].reverse().find(m=>m.role==='user').content);
        probe.receivedTexts.push(context.text);
        const reply={data:{line:`我听到你说${context.text}啦～`,control:{body:{actions:[],emotion:''}}}};
        if(probe.mode==='provider')throw new Error('provider_unavailable');
        if(probe.mode==='invalid')reply.data.control.body.actions=[{action:'drive',target_speed:60}];
        if(probe.mode==='hang')return new Promise(resolve=>{probe.releaseModel=()=>resolve(reply);});
        return reply;
      }},
      speech:{onState:callback=>{probe.playback=callback;},preload:async()=>({ok:true}),
        speak:async(body,options)=>{
          probe.speaks.push(body);options.signal.addEventListener('abort',()=>{probe.aborts++;probe.abortedIds.push(body.requestId);},{once:true});
          return {ok:true,data:{speech_id:'host-'+body.requestId,audio_sent:true}};
        }},
    })};
  });
  await page.click('#connect');
  await page.waitForFunction(()=>window.__continuity.memoryPending&&!document.getElementById('send').disabled);
}

async function say(page,text) {
  await page.fill('#command',text);await page.click('#send');
}

async function finishSpeech(page,index) {
  const id=await page.evaluate(index=>{
    const probe=window.__continuity,id=probe.speaks[index].requestId,turn='turn-'+id;
    probe.playback({requestId:id,speechId:'host-'+id,turnId:turn,playbackTurnId:turn,
      updatedAt:Date.now(),active:true,audioContextState:'running',audioContextTime:2,playbackStartAudioTime:1});
    window.appState.assistantAudioStreamClosedTurnId=turn;
    window.appState.assistantAudioStreamClosedEpoch=1;
    window.dispatchEvent(new CustomEvent('neko-assistant-speech-end',{detail:{turnId:turn}}));
    return id;
  },index);
  await page.waitForFunction(id=>window.__continuity.feedback.some(f=>f.speech_id===id&&f.status==='ended'),id);
}

test('chat is understood during protected story speech, answers follow it, and keywords still activate each native lesson',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.uncheck('#memory');
    for(const text of ['你好','不要系安全带','安全带系好了吗？']) {
      await say(page,text);
      await page.waitForFunction(count=>window.__continuity.plans.length===count, ['你好','不要系安全带','安全带系好了吗？'].indexOf(text)+1);
    }
    let result=await page.evaluate(()=>window.__continuity);
    assert.ok(result.plans.every(p=>p.actions.length===0));assert.equal(result.lastKind,'conversation_only');
    await say(page,'安全带');
    await page.waitForFunction(()=>window.__continuity.speaks.length===4);
    assert.match(await page.locator('#intent-state').textContent(),/识别到.*系安全带/);
    result=await page.evaluate(()=>window.__continuity);
    const protectedId=result.speaks[3].requestId,turnsBefore=result.turns;
    assert.equal(result.state.body.tutorial_state.waiting_step,'None','receipt cannot unlock the next lesson');
    await page.evaluate(()=>window.__continuity.transcript({text:'打火',requestId:'voice-during-lesson'}));
    await page.waitForFunction(()=>window.__continuity.plans.length===5);
    await say(page,'再系一次安全带');
    await page.waitForFunction(()=>window.__continuity.plans.length===6);
    result=await page.evaluate(()=>window.__continuity);
    assert.equal(result.turns,turnsBefore+2);assert.equal(result.speaks.length,4,'chat audio waits for the protected line');
    assert.equal(result.plans.filter(p=>p.actions.length).length,1);
    assert.ok(result.receivedTexts.includes('打火'));assert.ok(result.receivedTexts.includes('再系一次安全带'));
    assert.equal(result.abortedIds.includes(protectedId),false);
    await finishSpeech(page,3);
    await page.waitForFunction(()=>window.__continuity.speaks.length===5);
    assert.match(await page.evaluate(()=>window.__continuity.speaks[4].text),/再系一次安全带/);
    await page.clock.fastForward(1100);
    await page.waitForFunction(()=>document.getElementById('tutorial-state').textContent.includes('请说“启动引擎”'));
    await page.evaluate(()=>{window.__continuity.mode='provider';});
    await say(page,'打火');
    await page.waitForFunction(()=>window.__continuity.speaks.length===6);
    await finishSpeech(page,5);
    await page.clock.fastForward(1100);
    await page.waitForFunction(()=>document.getElementById('tutorial-state').textContent.includes('请说“踩油门出发”'));
    await page.evaluate(()=>window.__continuity.transcript({text:'慢慢往前开',requestId:'voice-tutorial-go'}));
    await page.waitForFunction(()=>window.__continuity.plans.length===8);
    result=await page.evaluate(()=>window.__continuity);
    assert.deepEqual(result.plans.filter(p=>p.actions.length).map(p=>p.actions),[[{action:'fasten_seatbelt'}],[{action:'start_engine'}],[{action:'shift_drive'}]]);
    assert.equal(result.commands,0);assert.equal(result.abortedIds.includes(protectedId),false);assert.equal(result.models,5);
    assert.equal(result.state.body.tutorial_state.current_action,'Go');
    assert.equal(result.state.body.tutorial_state.input_available,false);
    assert.equal(await page.locator('#error').textContent(),'');
  } finally {await browser.close();}
});

test('a delayed lesson receipt preserves the action and resumes only the latest chat without queuing later commands',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.evaluate(()=>{window.__continuity.delayReceipt=true;});
    await say(page,'系上安全带');
    await page.waitForFunction(()=>window.__continuity.plans.length===1);
    for(const text of ['你好','系上安全带','打火','刹车'])await say(page,text);
    assert.equal(await page.evaluate(()=>window.__continuity.turns),1);
    await page.evaluate(()=>window.__continuity.releaseReceipt());
    await page.waitForFunction(()=>window.__continuity.speaks.length===1);
    await page.waitForFunction(()=>window.__continuity.plans.length===2);
    assert.deepEqual(await page.evaluate(()=>window.__continuity.receivedTexts),['刹车']);
    await page.evaluate(()=>window.__continuity.transcript({text:'别打火',requestId:'voice-during-lesson'}));
    await page.waitForFunction(()=>window.__continuity.plans.length===3);
    const beforeEnd=await page.evaluate(()=>window.__continuity);
    assert.equal(beforeEnd.plans.filter(p=>p.actions.length).length,1);assert.equal(beforeEnd.models,2);assert.equal(beforeEnd.commands,0);
    assert.equal(beforeEnd.aborts,0);assert.equal(beforeEnd.feedback.filter(f=>f.status==='ended').length,0);
    await finishSpeech(page,0);
    await page.waitForFunction(()=>window.__continuity.speaks.length===2);
    assert.match(await page.evaluate(()=>window.__continuity.speaks[1].text),/别打火/);
    assert.equal(await page.evaluate(()=>window.__continuity.state.body.tutorial_state.waiting_step),'StartEngine');
  } finally {await browser.close();}
});

test('chat model failures during a lesson cannot interrupt its speech or advance its callback',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await say(page,'系安全带');await page.waitForFunction(()=>window.__continuity.speaks.length===1);
    await page.evaluate(()=>{window.__continuity.mode='provider';});
    await say(page,'你紧张吗？');await page.waitForFunction(()=>window.__continuity.models===1);
    const result=await page.evaluate(()=>window.__continuity);
    assert.equal(result.plans.length,1);assert.equal(result.aborts,0);
    assert.equal(result.feedback.filter(f=>f.status==='ended').length,0);
    await finishSpeech(page,0);
    assert.equal(await page.evaluate(()=>window.__continuity.state.body.tutorial_state.waiting_step),'StartEngine');
  } finally {await browser.close();}
});

test('fixed introduction allows conversation while native input is locked without replacing its speech',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.evaluate(()=>{
      const probe=window.__continuity;
      Object.assign(probe.state.body.tutorial_state,{input_available:false,waiting_step:'None',current_action:'NotSet'});
      probe.state.body.native_input_available=false;
      probe.events.push({speech_id:'intro-fixed',text:'我们先熟悉一下车内操作。',interrupt_existing:false});
    });
    await page.clock.fastForward(350);
    await page.waitForFunction(()=>window.__continuity.speaks.length===1);
    await say(page,'你以前教过别人吗？');
    await page.waitForFunction(()=>window.__continuity.plans.length===1);
    let result=await page.evaluate(()=>window.__continuity);
    assert.equal(result.lastKind,'conversation_only');assert.equal(result.models,2);
    assert.equal(result.speaks[0].requestId,'intro-fixed');assert.equal(result.speaks.length,1);
    assert.deepEqual(result.plans[0].actions,[]);assert.equal(result.aborts,0);
    await finishSpeech(page,0);
    await page.waitForFunction(()=>window.__continuity.speaks.length===2);
    result=await page.evaluate(()=>window.__continuity);
    assert.match(result.speaks[1].text,/你以前教过别人吗/);
    assert.equal(result.state.body.tutorial_state.input_available,false);
    assert.equal(result.state.body.tutorial_state.current_action,'NotSet');
  } finally {await browser.close();}
});

test('invalid model actions during chat cannot advance the tutorial',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.uncheck('#memory');
    await page.evaluate(()=>{window.__continuity.mode='invalid';});
    await say(page,'你好');await page.waitForFunction(()=>window.__continuity.models===2);
    assert.equal(await page.evaluate(()=>window.__continuity.plans.length),0);
    await say(page,'系安全带');
    await page.waitForFunction(()=>window.__continuity.speaks.length===1);
    const result=await page.evaluate(()=>window.__continuity);
    assert.equal(result.plans.length,1);assert.deepEqual(result.plans[0].actions,[{action:'fasten_seatbelt'}]);
  } finally {await browser.close();}
});

test('scene replacement retires an unfinished tutorial response before any action is submitted',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.uncheck('#memory');
    await page.evaluate(()=>{window.__continuity.mode='hang';});
    await say(page,'你好');await page.waitForFunction(()=>window.__continuity.models===1);
    await page.evaluate(()=>{
      Object.assign(window.__continuity.state,{scene_id:'road:2',scene:'Road',car:'RoadCar',body:{native_input_available:true}});
    });
    await page.clock.fastForward(1100);
    await page.waitForFunction(()=>document.getElementById('scene').textContent==='Road');
    await page.evaluate(()=>window.__continuity.releaseModel());
    await page.waitForTimeout(30);
    assert.equal(await page.evaluate(()=>window.__continuity.plans.length),0);
  } finally {await browser.close();}
});

test('the explicit stop button interrupts pending tutorial reasoning and never starts the next lesson',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.uncheck('#memory');
    await page.evaluate(()=>{window.__continuity.mode='hang';});
    await say(page,'你好');await page.waitForFunction(()=>window.__continuity.models===1);
    await page.click('#emergency');
    await page.waitForFunction(()=>window.__continuity.speaks.length===1);
    const result=await page.evaluate(()=>window.__continuity);
    assert.equal(result.commands,1);assert.deepEqual(result.plans.map(p=>p.actions),[[]]);
    assert.equal(result.state.body.tutorial_state.waiting_step,'FastenSeatbelt');
    assert.equal(result.state.body.tutorial_state.seatbelt_fastened,false);
    assert.equal(result.models,1);assert.match(result.speaks[0].text,/刹车/);
  } finally {await browser.close();}
});
