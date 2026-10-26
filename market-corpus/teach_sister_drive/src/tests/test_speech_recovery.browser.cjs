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
    const probe=window.__recovery={plans:[],speaks:[],feedback:[],retries:[],aborts:0,models:0,completed:0,events:[],retryable:[]};
    const native=window.fetch;
    const tutorial={version:1,active:true,known:true,input_available:true,seatbelt_fastened:false,engine_started:false,
      pending_command_speech:false,waiting_step:'FastenSeatbelt',current_action:'NotSet',microphone_stage:'',speech:{}};
    const state=probe.state={ok:true,protocol_version:2,possession_available:true,scene_id:'private-scene',scene:'Tutorial',
      car:'TutorialSceneCarController',game_active:true,body:{native_input_available:true,tutorial_state:tutorial}};
    window.fetch=async(url,options={})=>{
      if(!String(url).startsWith('http://127.0.0.1:8765'))return native(url,options);
      const path=new URL(url).pathname, body=options.body?JSON.parse(options.body):{};
      let result={ok:true};
      if(path==='/status'||path==='/session')result=state;
      else if(path==='/events')result={ok:true,scene_id:state.scene_id,session_id:'private-session',cursor:probe.retries.length,
        events:probe.events.splice(0),retryable:probe.retryable,error:probe.retryable.length?'speech failed':''};
      else if(path==='/plan') {
        probe.plans.push(body);tutorial.waiting_step='None';tutorial.current_action='FastenSeatbelt';
        tutorial.seatbelt_fastened=true;tutorial.pending_command_speech=true;
        result={ok:true,status:'executed',scene_id:state.scene_id,request_id:body.request_id,requires_speech_completion:true,action_count:1};
      } else if(path==='/speech')result={ok:true,speech_id:body.request_id};
      else if(path==='/playback') {
        if(probe.failFeedback)throw new TypeError('Failed to fetch');
        probe.feedback.push(body);
        if(body.status==='failed')probe.retryable=[{speech_id:body.speech_id,status:'failed'}];
        if(body.status==='ended') {probe.completed++;tutorial.pending_command_speech=false;tutorial.waiting_step='StartEngine';}
      } else if(path==='/speech/retry') {
        if(!probe.feedback.some(f=>f.speech_id===body.speech_id&&f.status==='failed'))throw new Error('Retry preceded failed acknowledgement');
        probe.retries.push(body);probe.retryable=[];
        const replacement='replacement-'+probe.retries.length;
        probe.events.push({speech_id:replacement,text:'我来系好安全带～',interrupt_existing:false});
        result={ok:true,speech_id:replacement};
      }
      return new Response(JSON.stringify(result));
    };
    window.appState={socket:{readyState:1},gameRouteInstanceId:'private-route',incomingAudioEpoch:1};
    window.appAudioPlayback={pendingAudioCountForSpeech:()=>0};window.lanlan_config={lanlan_name:'private-character'};
    window.open=()=>window;
    const tag=document.createElement('script');tag.type='application/json';tag.src='/app-game-voice-control.js';document.head.append(tag);
    window.nekoMiniGameSameOriginHostReady=Promise.resolve(()=>({}));
    window.NekoMiniGame={connect:async()=>({
      runtime:{session:{id:'private-session',routeInstanceId:'private-route'},bindCharacter:async()=>({name:'private-character'}),configure(){},start:async()=>{},end:async()=>{}},
      events:{on(){}},dispose(){},voice:{query:async()=>({ok:true,available:true}),onTranscript(){},onState(){},onError(){}},
      dialogue:{request:async()=>{probe.models++;throw new Error('No model needed for speech retry');}},
      speech:{onState:callback=>{window.__pushPlayback=callback;},preload:async()=>({ok:true}),
        speak:(body,options)=>{
          probe.speaks.push(body);
          if(body.requestId.startsWith('replacement-'))return Promise.resolve({ok:true,data:{speech_id:'host-replacement',audio_sent:true}});
          return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{
            probe.aborts++;reject(new DOMException('Aborted','AbortError'));
          },{once:true}));
        }},
    })};
    document.getElementById('memory').checked=false;
  });
  await page.click('#connect');await page.waitForFunction(()=>!document.getElementById('send').disabled);
  await page.fill('#command','系上安全带');await page.click('#send');
  await page.waitForFunction(()=>window.__recovery.speaks.length===1);
}

async function finishReplacement(page) {
  await page.clock.fastForward(300);
  await page.waitForFunction(()=>window.__recovery.speaks.length===2);
  await page.evaluate(()=>{
    const id=window.__recovery.speaks[1].requestId;
    window.__pushPlayback({requestId:id,speechId:'host-replacement',turnId:'replacement-turn',playbackTurnId:'replacement-turn',
      updatedAt:Date.now(),active:true,audioContextState:'running',audioContextTime:2,playbackStartAudioTime:1});
    window.appState.assistantAudioStreamClosedTurnId='replacement-turn';
    window.appState.assistantAudioStreamClosedEpoch=1;
    window.dispatchEvent(new CustomEvent('neko-assistant-speech-end',{detail:{turnId:'replacement-turn'}}));
  });
  await page.waitForFunction(()=>window.__recovery.completed===1);
  const result=await page.evaluate(()=>window.__recovery);
  assert.equal(result.plans.length,1,'recovery must not execute seatbelt twice');
  assert.equal(result.retries.length,1);assert.equal(result.aborts,1);assert.equal(result.models,0);
  assert.deepEqual(result.feedback.map(f=>f.status),['failed','started','ended']);
}

test('five-second warning offers speech-only recovery and exports structural timings',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.clock.fastForward(5100);
    await page.waitForFunction(()=>!document.getElementById('retry-current-speech').hidden);
    assert.match(await page.locator('#speech-state').textContent(),/超过5秒/);
    assert.equal(await page.evaluate(()=>window.__recovery.completed),0);
    await page.click('#retry-current-speech');await finishReplacement(page);
    const downloaded=page.waitForEvent('download');await page.locator('details').evaluate(element=>element.open=true);
    await page.click('#export-timing');
    const download=await downloaded, stream=await download.createReadStream();
    let raw='';for await(const chunk of stream)raw+=chunk.toString();
    const data=JSON.parse(raw);
    assert.equal(data.asrMeasured,false);assert.equal(data.entries.length,2);
    assert.equal(data.entries[0].outcome,'failed');assert.equal(data.entries[1].source,'recovery');
    assert.equal(data.entries[1].outcome,'ended');
    for(const secret of ['系上安全带','我来系好安全带','private-character','private-session','private-scene'])assert.equal(raw.includes(secret),false);
    const id=await page.evaluate(()=>window.__recovery.plans[0].request_id);assert.equal(raw.includes(id),false);
  } finally {await browser.close();}
});

test('first-audio timeout cancels synthesis, preserves the lesson and offers the existing replay button',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.clock.fastForward(15100);
    await page.waitForFunction(()=>window.__recovery.aborts===1);
    await page.clock.fastForward(300);
    await page.waitForFunction(()=>!document.getElementById('retry').hidden);
    assert.match(await page.locator('#speech-state').textContent(),/超过15秒/);
    const initial=await page.evaluate(()=>window.__recovery);
    assert.equal(initial.completed,0);assert.equal(initial.plans.length,1);
    // A delayed player sample from the cancelled request cannot change UI or advance.
    await page.evaluate(()=>window.__pushPlayback({requestId:window.__recovery.speaks[0].requestId,
      updatedAt:Date.now(),active:true,audioContextState:'running',audioContextTime:2,playbackStartAudioTime:1}));
    assert.match(await page.locator('#speech-state').textContent(),/超过15秒/);
    await page.click('#retry');await finishReplacement(page);
  } finally {await browser.close();}
});

test('late playback feedback after a scene change never starts recovery in the new scene',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.clock.fastForward(5100);
    await page.evaluate(()=>{window.__recovery.state.scene_id='next-scene';window.__recovery.state.scene='Next';});
    await page.clock.fastForward(1100);
    await page.waitForFunction(()=>document.getElementById('scene').textContent==='Next');
    assert.equal(await page.locator('#retry-current-speech').isVisible(),false);
    await page.clock.fastForward(16000);
    const result=await page.evaluate(()=>window.__recovery);
    assert.equal(result.retries.length,0);assert.equal(result.completed,0);
    assert.equal(result.feedback.some(f=>f.status==='ended'),false);
  } finally {await browser.close();}
});

test('a stalled playing line keeps its warning across frozen heartbeats and never completes the lesson',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.evaluate(()=>{
      window.__frozenSample=()=>({requestId:window.__recovery.speaks[0].requestId,speechId:'original-host',turnId:'original-turn',playbackTurnId:'original-turn',
        updatedAt:Date.now(),active:true,audioContextState:'running',audioContextTime:2,playbackStartAudioTime:1});
      window.NekoSpeechPlaybackState={speechId:'original-host'};
      window.appAudioPlayback.clearAudioQueue=()=>{window.__recovery.interrupts=(window.__recovery.interrupts||0)+1;};
      window.__pushPlayback(window.__frozenSample());
    });
    await page.clock.fastForward(5100);
    await page.evaluate(()=>window.__pushPlayback(window.__frozenSample()));
    assert.match(await page.locator('#speech-state').textContent(),/没有继续播放/);
    await page.clock.fastForward(7100);
    await page.waitForFunction(()=>window.__recovery.aborts===1);
    assert.match(await page.locator('#speech-state').textContent(),/停滞/);
    const result=await page.evaluate(()=>window.__recovery);
    assert.equal(result.completed,0);assert.equal(result.interrupts,1);
    assert.deepEqual(result.feedback.map(f=>f.status),['started','playing','failed']);
  } finally {await browser.close();}
});

test('failed playback acknowledgement prevents retrying an unconfirmed native record',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await openLesson(page);
    await page.clock.fastForward(5100);
    await page.evaluate(()=>{window.__recovery.failFeedback=true;});
    await page.click('#retry-current-speech');
    await page.waitForFunction(()=>document.getElementById('error').textContent.includes('未确认收到'));
    const result=await page.evaluate(()=>window.__recovery);
    assert.equal(result.retries.length,0);assert.equal(result.plans.length,1);assert.equal(result.completed,0);
  } finally {await browser.close();}
});
