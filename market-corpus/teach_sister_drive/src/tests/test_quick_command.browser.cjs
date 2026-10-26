// Exercises the actual adapter in a browser, with in-memory game/voice doubles.
// Does not acquire the real game, microphone, memory or AI services.
const test = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('../../../../frontend/plugin-manager/node_modules/@playwright/test');
const {serveLocalStaticAssets}=require('./browser-assets.cjs');

test('quick input bypasses stalled memory; both external interruption and new input retire pending TTS without replaying actions',async()=>{
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    await serveLocalStaticAssets(page.context());
    await page.goto('http://localhost:48911/static/game/teach-sister-drive/index.html');
    await page.evaluate(async()=>{
      await window.nekoMiniGameSameOriginHostReady;
      const probe=window.__quickProbe={plans:[],speaks:0,aborts:0,models:0,memory:false,preloaded:[],cacheFlags:[]};
      const state={ok:true,protocol_version:2,possession_available:true,scene_id:'test:1',
        scene:'Road',car:'RoadCar',game_active:true,body:{native_input_available:true}};
      const nativeFetch=window.fetch;
      window.fetch=async(url,options={})=>{
        if (!String(url).startsWith('http://127.0.0.1:8765')) return nativeFetch(url,options);
        const path=new URL(url).pathname,body=options.body?JSON.parse(options.body):{};
        let response={ok:true};
        if (path==='/status'||path==='/session') response=state;
        else if(path==='/neko-context'){probe.memory=true;return new Promise(()=>{});}
        else if(path==='/events') response={ok:true,scene_id:state.scene_id,session_id:'test-session',cursor:0,events:[]};
        else if(path==='/plan'){
          probe.plans.push(body);
          response={ok:true,status:'executed',request_id:body.request_id,scene_id:state.scene_id,action_count:body.actions.length};
        } else if(path==='/speech') response={ok:true,speech_id:body.request_id};
        return new Response(JSON.stringify(response));
      };
      window.appState={socket:{readyState:1},gameRouteInstanceId:'test-route'};
      window.appAudioPlayback={};window.lanlan_config={lanlan_name:'Test'};
      window.open=()=>window; // Isolated fake player, never the running app.
      const tag=document.createElement('script');tag.type='application/json';tag.src='/app-game-voice-control.js';document.head.append(tag);
      window.nekoMiniGameSameOriginHostReady=Promise.resolve(()=>({}));
      window.NekoMiniGame={connect:async()=>({
        runtime:{session:{id:'test-session',routeInstanceId:'test-route'},bindCharacter:async()=>({name:'Test'}),configure(){},start:async()=>{},end:async()=>{}},
        events:{on(){}},dispose(){},voice:{query:async()=>({ok:true,available:true}),onTranscript(){},onState(){},onError(){}},
        dialogue:{request:async()=>{probe.models++;throw new Error('Model must not be called');}},
        speech:{onState:callback=>{window.__testSpeechState=callback;},preload:lines=>{probe.preloaded=lines;return new Promise(()=>{});},
          speak:(request,options)=>{probe.speaks++;probe.cacheFlags.push(request.reuseSynthesizedAudio);return new Promise((resolve,reject)=>{
          options.signal.addEventListener('abort',()=>{probe.aborts++;reject(new DOMException('Aborted','AbortError'));},{once:true});
        });}},
      })};
    });
    await page.click('#connect');
    await page.waitForFunction(()=>window.__quickProbe.memory&&!document.getElementById('send').disabled);
    await page.fill('#command','方向回正');await page.click('#send');
    await page.waitForFunction(()=>window.__quickProbe.speaks===1);
    // An interruption from the real player, before a new input reaches the
    // adapter, must release the still-pending backend synthesis request.
    await page.evaluate(()=>window.__testSpeechState({requestId:window.__quickProbe.plans[0].request_id,
      updatedAt:Date.now(),reason:'interrupt',active:false,audioContextState:'running'}));
    await page.waitForFunction(()=>window.__quickProbe.aborts===1);
    await page.fill('#command','向左一点');await page.click('#send');
    await page.waitForFunction(()=>window.__quickProbe.speaks===2);
    await page.fill('#command','向右一点');await page.click('#send');
    await page.waitForFunction(()=>window.__quickProbe.speaks===3);
    const result=await page.evaluate(()=>({
      actions:window.__quickProbe.plans.map(p=>p.actions),
      ids:window.__quickProbe.plans.map(p=>p.request_id),
      models:window.__quickProbe.models,aborts:window.__quickProbe.aborts,
      preloaded:window.__quickProbe.preloaded,cacheFlags:window.__quickProbe.cacheFlags,
      error:document.getElementById('error').textContent,
      interpretation:document.getElementById('intent-state').textContent,
    }));
    assert.deepEqual(result.actions,[[{action:'drive',steering:0}],[{action:'drive',steering:-.12}],[{action:'drive',steering:.12}]]);
    assert.equal(new Set(result.ids).size,3);assert.equal(result.models,0);
    assert.equal(result.aborts,2);assert.equal(result.error,'');
    assert.ok(result.preloaded.includes('方向回正啦～'));assert.deepEqual(result.cacheFlags,[true,true,true]);
    assert.match(result.interpretation,/向右转/);
  } finally {await browser.close();}
});
