const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('../../../../frontend/plugin-manager/node_modules/@playwright/test');

test('driving possession retains the selected avatar; other game rendering and close restoration still work',async()=>{
  const root=path.resolve(__dirname,'../../../..');
  const template=fs.readFileSync(path.join(root,'templates/index.html'),'utf8');
  const start=template.lastIndexOf('<script>',template.indexOf('var pngtuberHiddenForGameWindow'))+8;
  const script=template.slice(start,template.indexOf('</script>',start));
  const css=fs.readFileSync(path.join(root,'static/css/index.css'),'utf8');
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();
    await page.setContent('<body><div id="live2d-container"></div><div id="vrm-container" style="display:none"></div><div id="pngtuber-container" style="display:none"></div><div id="react-chat-window-overlay"></div><div id="live2d-floating-buttons"><button id="settings">Settings</button></div><div id="live2d-lock-icon"></div><div id="live2d-return-button-container" style="display:none"></div><div id="vrm-floating-buttons" style="display:none"></div></body>');
    await page.addStyleTag({content:css+'\n#live2d-floating-buttons,#live2d-lock-icon {pointer-events:auto}'});
    await page.evaluate(()=>{
      window.appState={lanlan_name:'Test',gameRouteActive:false,gameRouteGameType:'teach-sister-drive'};window.fetch=async()=>({ok:true,json:async()=>({active:false})});
      window.__renders=[];window.live2dManager={pauseRendering:()=>window.__renders.push('pause'),resumeRendering:()=>window.__renders.push('resume')};
    });
    await page.addScriptTag({content:script});
    const companion=fs.readFileSync(path.join(__dirname,'../ui/companion.mjs'),'utf8');
    await page.addScriptTag({type:'module',content:companion.replaceAll('export ','')+'\nwindow.__releaseCompanion=keepCompanionVisible(window);'});
    await page.waitForFunction(()=>typeof window.__releaseCompanion==='function');
    const result=await page.evaluate(()=>{
      const dispatch=(action,gameType)=>dispatchEvent(new CustomEvent('neko-game-window-state-change',{detail:{action,gameType}}));
      const display=id=>getComputedStyle(document.getElementById(id)).display;
      window.appState.gameRouteActive=true;
      dispatch('opened','teach-sister-drive');
      const driving={avatar:display('live2d-container'),inactiveAvatar:display('vrm-container'),chat:display('react-chat-window-overlay'),route:document.body.classList.contains('neko-game-active'),runtimeActive:window.appState.gameRouteActive,renders:[...window.__renders],buttons:display('live2d-floating-buttons'),pointer:getComputedStyle(document.getElementById('live2d-floating-buttons')).pointerEvents,lock:display('live2d-lock-icon'),returnButton:display('live2d-return-button-container'),inactiveButtons:display('vrm-floating-buttons')};
      dispatch('opened','badminton');
      const other={avatar:display('live2d-container'),keep:document.body.classList.contains('neko-sister-drive-companion'),buttons:display('live2d-floating-buttons')};
      dispatch('closed','badminton');
      const closed={avatar:display('live2d-container'),keep:document.body.classList.contains('neko-sister-drive-companion'),route:document.body.classList.contains('neko-game-active')};
      window.__releaseCompanion();
      return {driving,other,closed,renders:window.__renders};
    });
    assert.notEqual(result.driving.avatar,'none');assert.equal(result.driving.inactiveAvatar,'none');
    assert.equal(result.driving.chat,'none');assert.equal(result.driving.route,false);assert.equal(result.driving.runtimeActive,true);
    assert.deepEqual(result.driving.renders,['pause','resume']);
    assert.equal(result.driving.buttons,'flex');assert.equal(result.driving.pointer,'auto');
    assert.notEqual(result.driving.lock,'none');assert.equal(result.driving.returnButton,'none');assert.equal(result.driving.inactiveButtons,'none');
    assert.deepEqual(result.other,{avatar:'none',keep:false,buttons:'none'});
    assert.equal(result.closed.keep,false);assert.equal(result.closed.route,false);assert.notEqual(result.closed.avatar,'none');
    assert.deepEqual(result.renders,['pause','resume','pause','resume']);
  } finally {await browser.close();}
});

test('all avatar control groups stay clickable while explicit hiding and lock settings still work',async()=>{
  const root=path.resolve(__dirname,'../../../..');
  const css=fs.readFileSync(path.join(root,'static/css/index.css'),'utf8');
  const companion=fs.readFileSync(path.join(__dirname,'../ui/companion.mjs'),'utf8');
  const browser=await chromium.launch({headless:true});
  try {
    for(const name of ['live2d','vrm','mmd','pngtuber']) {
      const page=await browser.newPage();
      await page.setContent(`<body><div id="${name}-container"></div><div id="${name}-floating-buttons"><button id="settings">Settings</button></div><div id="${name}-lock-icon"></div><div id="${name}-return-button-container" style="display:none"></div></body>`);
      await page.addStyleTag({content:css+`\n#${name}-floating-buttons {pointer-events:auto}.hidden,[hidden]{display:none!important}`});
      await page.evaluate(()=>{window.__clicks=0;document.getElementById('settings').onclick=()=>window.__clicks++;});
      await page.addScriptTag({type:'module',content:companion.replaceAll('export ','')+'\nwindow.__releaseCompanion=keepCompanionVisible(window);'});
      await page.waitForFunction(()=>typeof window.__releaseCompanion==='function');
      await page.evaluate(()=>{
        document.body.classList.add('neko-game-active');
        dispatchEvent(new CustomEvent('neko-game-window-state-change',{detail:{action:'opened',gameType:'teach-sister-drive'}}));
      });
      await page.click('#settings');
      assert.equal(await page.evaluate(()=>window.__clicks),1);
      const controls=page.locator(`#${name}-floating-buttons`);
      await controls.evaluate(el=>{el.style.display='none';});assert.equal(await controls.isVisible(),false);
      await controls.evaluate(el=>{el.style.display='flex';});assert.equal(await controls.isVisible(),true);
      await controls.evaluate(el=>el.classList.add('hidden'));assert.equal(await controls.isVisible(),false);
      await controls.evaluate(el=>el.classList.remove('hidden'));
      await controls.evaluate(el=>{el.hidden=true;});assert.equal(await controls.isVisible(),false);
      await controls.evaluate(el=>{el.hidden=false;el.style.pointerEvents='none';});
      assert.equal(await controls.evaluate(el=>getComputedStyle(el).pointerEvents),'none');
      await controls.evaluate(el=>{el.style.pointerEvents='auto';});await page.click('#settings');
      assert.equal(await page.evaluate(()=>window.__clicks),2);
      await page.evaluate(()=>window.__releaseCompanion());assert.equal(await controls.isVisible(),false);
      await page.evaluate(()=>document.body.classList.remove('neko-game-active'));assert.equal(await controls.isVisible(),true);
      await page.close();
    }
  } finally {await browser.close();}
});
