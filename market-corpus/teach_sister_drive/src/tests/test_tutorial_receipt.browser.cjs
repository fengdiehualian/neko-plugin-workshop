// Fault injection: the game accepts a lesson, but its HTTP receipt is delayed.
// New input must not cancel that pending lesson or its protected speech;
// a new scene must retire it instead.
const test=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('../../../../frontend/plugin-manager/node_modules/@playwright/test');
const {serveLocalStaticAssets}=require('./browser-assets.cjs');

for (const step of ['FastenSeatbelt','StartEngine','Microphone','Loading','LoadingAbort','LoadingScene','LoadingGeneration','OwnerConflict']) test(step.startsWith('Loading')
  ? `temporary Unity ${step} timeout retries ownership without ending the adapter`
  : step==='OwnerConflict' ? 'another browser owner ends the adapter without a takeover retry'
  : `accepted ${step} retains speech while a newer input waits for its receipt`,async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();
    page.setDefaultTimeout(5000);
    await serveLocalStaticAssets(page.context());
    await page.goto('http://localhost:48911/static/game/teach-sister-drive/index.html');
    await page.evaluate(async waiting=>{
      await window.nekoMiniGameSameOriginHostReady;
      const probe=window.__lessonProbe={turns:0,plans:[],speechRegistrations:[],speaks:[],sessions:[],aborts:0};
      const state={ok:true,protocol_version:2,possession_available:true,scene_id:'lesson:1',scene:'Tutorial',
        car:'TutorialSceneCarController',game_active:true,body:{native_input_available:true,
          tutorial:`教学中，安全带${waiting==='StartEngine'?'已系':'未系'}，引擎未启动，等待 ${waiting}，当前动作 None`}};
      if(waiting==='Microphone')state.body.tutorial_state={version:1,active:true,known:true,input_available:true,
        seatbelt_fastened:false,engine_started:false,pending_command_speech:false,
        waiting_step:'None',current_action:'NotSet',microphone_stage:'waiting_input',speech:{}};
      const original=window.fetch;
      window.fetch=async(url,options={})=>{
        if(!String(url).startsWith('http://127.0.0.1:8765'))return original(url,options);
        const body=options.body?JSON.parse(options.body):{},path=new URL(url).pathname;
        let data={ok:true};
        if(path==='/status')data=state;
        else if(path==='/session') {
          probe.sessions.push(body);
          if(waiting==='LoadingAbort' && probe.sessions.length===2)throw new DOMException('signal is aborted without reason','AbortError');
          const errors={Loading:'game thread busy; command cancelled before execution',
            LoadingScene:'scene changed before command execution',LoadingGeneration:'stale scene generation',
            OwnerConflict:'session_owned_by_browser'};
          if(['LoadingScene','LoadingGeneration'].includes(waiting) && probe.sessions.length===2) {
            state.scene_id='lesson:2';state.scene='TutorialAfterLoad';
          }
          data=errors[waiting] && probe.sessions.length===2 ? {ok:false,error:errors[waiting]} : state;
        }
        else if(path==='/turn')probe.turns++;
        else if(path==='/neko-context')return new Promise(()=>{});
        else if(path==='/events')data={ok:true,scene_id:state.scene_id,session_id:'lesson-session',cursor:0,events:[]};
        else if(path==='/plan'){
          probe.plans.push(body);
          state.body.tutorial=`教学中，安全带已系，引擎${waiting==='StartEngine'?'已启动':'未启动'}，等待 None，当前动作 ${waiting}`;
          if(waiting==='Microphone') {
            if(body.microphone_input!==true || body.actions.length)throw new Error('Microphone exercise must not drive');
            state.body.tutorial_state.microphone_stage='awaiting_speech';
          }
          data={ok:true,status:'executed',scene_id:state.scene_id,request_id:body.request_id,
            action_count:1,requires_speech_completion:true};
          return new Promise(resolve=>{window.__releaseLessonReceipt ||= ()=>resolve(new Response(JSON.stringify(data)));});
        } else if(path==='/speech'){
          probe.speechRegistrations.push(body.request_id);data={ok:true,speech_id:body.request_id};
        }
        return new Response(JSON.stringify(data));
      };
      window.appState={socket:{readyState:1},gameRouteInstanceId:'lesson-route'};
      window.appAudioPlayback={};window.lanlan_config={lanlan_name:'Test'};
      window.open=()=>window; // Isolated fake player, never the running app.
      const tag=document.createElement('script');tag.type='application/json';tag.src='/app-game-voice-control.js';document.head.append(tag);
      window.nekoMiniGameSameOriginHostReady=Promise.resolve(()=>({}));
      window.NekoMiniGame={connect:async()=>({
        runtime:{session:{id:'lesson-session',routeInstanceId:'lesson-route'},bindCharacter:async()=>({name:'Test'}),configure(){},start:async()=>{},end:async()=>{}},
        events:{on(){}},dispose(){},voice:{query:async()=>({ok:true,available:true}),onTranscript(){},onState(){},onError(){}},
        dialogue:{request:async()=>{throw new Error('Memory is intentionally pending');}},
        speech:{onState(){},preload:async()=>({ok:false}),speak:(request,options)=>{
          probe.speaks.push(request.requestId);return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{
            probe.aborts++;reject(new DOMException('Aborted','AbortError'));
          },{once:true}));
        }},
      })};
    },step);
    await page.click('#connect');
    try {await page.waitForFunction(()=>!document.getElementById('send').disabled);}
    catch {throw new Error(JSON.stringify(await page.evaluate(()=>({error:document.getElementById('error').textContent,
      connection:document.getElementById('connection').textContent,ready:document.readyState,
      route:window.appState?.gameRouteInstanceId,player:Boolean(window.appAudioPlayback),
      tag:Boolean(document.querySelector('script[src*="/app-game-voice-control.js"]')),calls:window.__lessonProbe}))));}
    if(step.startsWith('Loading')) {
      await page.waitForFunction(()=>window.__lessonProbe.sessions.length>=3);
      assert.equal(await page.locator('#disconnect').isDisabled(),false);
      assert.equal((await page.evaluate(()=>window.__lessonProbe.sessions)).some(s=>s.active===false),false);
      await page.waitForFunction(()=>document.getElementById('connection').textContent==='Test 已接管游戏');
      if(['LoadingScene','LoadingGeneration'].includes(step))
        await page.waitForFunction(()=>document.getElementById('scene').textContent==='TutorialAfterLoad');
      return;
    }
    if(step==='OwnerConflict') {
      await page.waitForFunction(()=>document.getElementById('disconnect').disabled);
      const sessions=await page.evaluate(()=>window.__lessonProbe.sessions);
      assert.equal(sessions.filter(s=>s.active===true).length,2);
      assert.equal(sessions.filter(s=>s.active===false).length,1);
      return;
    }
    await page.fill('#command',step==='Microphone'?'你好':step==='FastenSeatbelt'?'系上安全带':'打火');await page.click('#send');
    await page.waitForFunction(()=>window.__lessonProbe.plans.length===1);
    await page.fill('#command','你好');await page.click('#send');
    await page.waitForFunction(()=>document.getElementById('heard').textContent==='你好');
    assert.equal(await page.evaluate(()=>window.__lessonProbe.turns),1);
    await page.evaluate(()=>window.__releaseLessonReceipt());
    await page.waitForFunction(()=>window.__lessonProbe.speaks.length===1);
    // A third ordinary input must not cancel the completion-bound lesson line.
    await page.fill('#command','你紧张吗');await page.click('#send');
    await page.waitForFunction(()=>document.getElementById('heard').textContent==='你紧张吗');
    const result=await page.evaluate(()=>window.__lessonProbe);
    assert.equal(result.turns,3);assert.equal(result.plans.length,1);
    assert.deepEqual(result.speechRegistrations,[result.plans[0].request_id]);
    assert.deepEqual(result.speaks,result.speechRegistrations);assert.equal(result.aborts,0);
  } finally {await browser.close();}
});
