const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('../../../../frontend/plugin-manager/node_modules/@playwright/test');
const BASE='http://127.0.0.1:48916';

async function fixture(browser,{main='http://localhost:48911',ready=true,hasOpener=true,dropProbe=false}={}) {
  const context=await browser.newContext();
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    const html=body=>route.fulfill({contentType:'text/html',body});
    if(url.port==='48911' && url.pathname==='/')return html(`<!doctype html><title>Existing player</title>${ready?'<script>window.appState={socket:{readyState:1}};window.appAudioPlayback={};</script><script type="application/json" src="/static/app-game-voice-control.js"></script>':''}`);
    if(url.origin===BASE && url.pathname==='/manager')
      return html('<!doctype html><iframe src="/plugin/teach_sister_drive/ui/origin-test.html"></iframe>');
    if(url.origin===BASE && url.pathname.endsWith('/origin-test.html'))return html('<!doctype html><body></body>');
    let file=null;
    if(url.origin===BASE && url.pathname==='/plugin/teach_sister_drive/ui/player-origin.mjs')
      file=path.join(__dirname,'../static/player-origin.mjs');
    if(url.port==='48911' && url.pathname.startsWith('/static/game/teach-sister-drive/')) {
      if(dropProbe)return route.fulfill({status:404,body:''});
      const name=url.pathname.split('/').pop();
      if(['player-probe.html','player-probe.mjs','player-window.mjs'].includes(name))file=path.join(__dirname,'../ui',name);
    }
    if(file)return route.fulfill({path:file,contentType:file.endsWith('.mjs')?'text/javascript':'text/html'});
    return route.fulfill({status:404,body:''});
  });
  const root=await context.newPage();let manager=root;
  if(hasOpener) {
    await root.goto(main);
    const popup=root.waitForEvent('popup');
    await root.evaluate(url=>window.open(url,'test-manager'),BASE+'/manager');
    manager=await popup;
    await manager.waitForLoadState('load');
  } else await manager.goto(BASE+'/manager');
  const frame=manager.frames().find(f=>f.url().endsWith('/origin-test.html'));
  assert.ok(frame);
  return {context,root,manager,frame};
}

for(const main of ['http://localhost:48911','http://127.0.0.1:48911']) {
  test(`missing source hint finds existing ${main} player through cross-origin manager`,async()=>{
    const browser=await chromium.launch({headless:true});
    try {
      const {context,manager,frame}=await fixture(browser,{main});
      const wrong=main.includes('localhost')?'http://127.0.0.1:48911':'http://localhost:48911';
      const result=await frame.evaluate(async preferred=>{
        const {resolvePlayerOrigin}=await import('./player-origin.mjs?v=0.1.0-origin1');
        return resolvePlayerOrigin(window,preferred);
      },wrong);
      assert.equal(result,main);
      assert.equal(context.pages().length,2,'discovery must not open another player');
      assert.equal(manager.frames().length,2,'temporary discovery frames must be removed');
    } finally {await browser.close();}
  });
}

test('initializing main window is found even before its audio globals exist',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {frame}=await fixture(browser,{ready:false});
    assert.equal(await frame.evaluate(async()=>{
      const {resolvePlayerOrigin}=await import('./player-origin.mjs?v=0.1.0-origin1');
      return resolvePlayerOrigin(window,'http://127.0.0.1:48911');
    }),'http://localhost:48911');
  } finally {await browser.close();}
});
test('direct browser panel without opener keeps the standalone player fallback',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {context,manager,frame}=await fixture(browser,{hasOpener:false});
    assert.equal(await frame.evaluate(async()=>{
      const {resolvePlayerOrigin}=await import('./player-origin.mjs?v=0.1.0-origin1');
      return resolvePlayerOrigin(window,'http://127.0.0.1:48911');
    }),'http://127.0.0.1:48911');
    assert.equal(context.pages().length,1);assert.equal(manager.frames().length,2);
  } finally {await browser.close();}
});

test('unrelated messages cannot impersonate a discovered player',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {frame}=await fixture(browser);
    assert.equal(await frame.evaluate(async()=>{
      const {resolvePlayerOrigin}=await import('./player-origin.mjs?v=0.1.0-origin1');
      const result=resolvePlayerOrigin(window,'http://127.0.0.1:48911');
      const probe=document.querySelector('iframe');
      const nonce=new URL(probe.src).searchParams.get('nonce');
      window.dispatchEvent(new MessageEvent('message',{source:window,origin:'http://127.0.0.1:48911',
        data:{type:'neko-drive-player-origin',nonce,ready:true,available:true}}));
      return result;
    }),'http://localhost:48911');
  } finally {await browser.close();}
});

test('missing probe assets time out, remove temporary frames, and keep a local fallback',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {manager,frame}=await fixture(browser,{dropProbe:true});
    assert.equal(await frame.evaluate(async()=>{
      const {resolvePlayerOrigin,mainOrigin}=await import('./player-origin.mjs?v=0.1.0-origin1');
      for(const input of ['https://localhost:48911','http://example.com:48911','http://user:pass@localhost:48911','http://localhost:48916'])
        if(mainOrigin(input)!==null)throw new Error('Invalid origin accepted');
      return resolvePlayerOrigin(window,'http://example.com:48911');
    }),'http://127.0.0.1:48911');
    assert.equal(manager.frames().length,2);
  } finally {await browser.close();}
});

