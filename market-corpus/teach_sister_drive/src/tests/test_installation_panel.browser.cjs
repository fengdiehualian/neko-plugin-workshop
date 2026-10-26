const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('../../../../frontend/plugin-manager/node_modules/@playwright/test');
const BASE='http://127.0.0.1:48916';
const URL=BASE+'/plugin/teach_sister_drive/ui/';

async function panel(browser,{oldHost=false,unsupported=false,running=false,missing=false,staleToken=false,stopped=false,installed=false,takeover=false}={}) {
  const page=await browser.newPage();const calls=[],runs=new Map();let tokens=0,rejected=false,starts=0;
  let modInstalled=installed,loaderInstalled=installed;
  await page.route(BASE+'/**',async route=>{
    const request=route.request(),url=new globalThis.URL(request.url());
    const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(url.pathname==='/security/csrf-token') {
      tokens++;return oldHost?route.fulfill({status:404,body:'not found'}):json({csrf_token:'token-'+tokens});
    }
    if(url.pathname==='/plugin/teach_sister_drive/start') {starts++;return json({success:true});}
    if(url.pathname==='/runs'&&request.method()==='POST') {
      if(staleToken&&!rejected){rejected=true;return route.fulfill({status:403,headers:{'X-CSRF-Failure':'token'},contentType:'application/json',body:JSON.stringify({detail:{csrf_failure:'token'}})});}
      const body=request.postDataJSON();calls.push(body);
      assert.match(request.headers()['x-csrf-token'],/^token-/);
      const id=String(calls.length);runs.set(id,body.entry_id);return json({run_id:id});
    }
    const match=/^\/runs\/(\d+)(\/export)?$/.exec(url.pathname);
    if(match) {
      if(!match[2])return stopped && !starts?json({status:'failed',error:{code:'NOT_READY',message:'plugin stopped'}}):json({status:'succeeded'});
      const installing=runs.get(match[1])==='install_mod',uninstalling=runs.get(match[1])==='uninstall_mod';
      if(!running && (installing||uninstalling)){modInstalled=installing;loaderInstalled=true;}
      const game={name:'妹妹开车 Demo',path:'D:/游戏 空格',supported:!unsupported,mod_installed:modInstalled,mod_current:modInstalled,loader_installed:loaderInstalled};
      const result=installing||uninstalling ? running?{success:false,error:{message:'请先正常退出游戏，再安装或卸载模组'}}
        :{success:true,data:{...game,status:installing?'installed':'uninstalled',message:installing?'模组已安装，请重新启动游戏后连接':'模组已卸载并备份；共享加载器和游戏存档已保留，可重新安装'}}
        :{success:true,data:{games:missing?[]:[game],version:'0.1.0',game_running:running,takeover_available:takeover,takeover_reason:takeover?'source_plugin_launcher':'source_launcher_required',message:missing?'未找到已安装的游戏':'已找到游戏，请安装模组'}};
      return json({items:[{type:'json',json:result}]});
    }
    if(url.pathname.startsWith('/plugin/teach_sister_drive/ui/')) {
      const file=url.pathname.slice('/plugin/teach_sister_drive/ui/'.length)||'index.html';
      if(['panel.mjs','plugin-api.mjs'].includes(file))assert.ok(url.searchParams.get('v'),'installer modules need an update-specific cache key');
      const type=path.extname(file)==='.mjs'?'text/javascript':path.extname(file)==='.css'?'text/css':'text/html';
      return route.fulfill({path:path.join(__dirname,'../static',file),contentType:type});
    }
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto(URL);return {page,calls,tokens:()=>tokens,starts:()=>starts};
}

test('two clicks install the mod and source launcher exposes takeover',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page,calls}=await panel(browser,{takeover:true});
    assert.equal(await page.locator('#install-mod').isDisabled(),true);
    await page.click('#detect-game');await page.waitForFunction(()=>!document.getElementById('install-mod').disabled);
    await page.click('#install-mod');await page.waitForFunction(()=>document.getElementById('installation-state').textContent.includes('已安装'));
    assert.deepEqual(calls.map(c=>c.entry_id),['detect_game','install_mod']);
    assert.equal(calls[1].args.game_path,'D:/游戏 空格');
    assert.match(await page.locator('#installation-state').textContent(),/已安装/);
    assert.equal(await page.locator('#installation-error').textContent(),'');
    assert.equal(await page.locator('#open-drive-panel').isDisabled(),false);
    assert.match(await page.locator('#takeover-state').textContent(),/源码版接管已就绪/);
    assert.equal(await page.locator('#prepare-steam').count(),0);
  } finally {await browser.close();}
});
test('old Steam host is rejected before any plugin action is sent',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page,calls}=await panel(browser,{oldHost:true});
    await page.click('#detect-game');await page.waitForFunction(()=>document.getElementById('installation-error').textContent.includes('当前 Steam 版不受支持'));
    assert.equal(calls.length,0);assert.equal(await page.locator('#open-drive-panel').isDisabled(),true);
    assert.equal(await page.locator('#prepare-steam').count(),0);
  } finally {await browser.close();}
});

test('running game errors leave install available to retry without reporting success',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page}=await panel(browser,{running:true});
    await page.click('#detect-game');await page.waitForFunction(()=>!document.getElementById('install-mod').disabled);
    await page.click('#install-mod');await page.waitForFunction(()=>document.getElementById('installation-error').textContent.includes('退出游戏'));
    assert.equal(await page.locator('#open-drive-panel').isDisabled(),true);
    assert.equal(await page.locator('#install-mod').isDisabled(),false);
    assert.match(await page.locator('#installation-state').textContent(),/未完成/);
  } finally {await browser.close();}
});

test('unknown game versions disable installation; missing game keeps manual selection available',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    for(const options of [{unsupported:true},{missing:true}]) {
      const {page}=await panel(browser,options);await page.click('#detect-game');
      await page.waitForFunction(()=>!document.getElementById('detect-game').disabled);
      assert.equal(await page.locator('#install-mod').isDisabled(),true);
      await page.locator('summary').click();assert.equal(await page.locator('#manual-path').isVisible(),true);
    }
  } finally {await browser.close();}
});

test('stale CSRF credentials are renewed before sending one installation action',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page,calls,tokens}=await panel(browser,{staleToken:true});await page.click('#detect-game');
    await page.waitForFunction(()=>!document.getElementById('install-mod').disabled);
    assert.equal(calls.length,1);assert.equal(tokens(),2);
  } finally {await browser.close();}
});

test('first detection starts only this plugin when the entry was confirmed not ready',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page,calls,starts}=await panel(browser,{stopped:true});await page.click('#detect-game');
    await page.waitForFunction(()=>!document.getElementById('install-mod').disabled);
    await page.click('#install-mod');await page.waitForFunction(()=>document.getElementById('installation-state').textContent.includes('已安装'));
    assert.equal(starts(),1);assert.deepEqual(calls.map(c=>c.entry_id),['detect_game','detect_game','install_mod']);
  } finally {await browser.close();}
});

test('uninstall becomes available after detection, hides takeover and enables reinstall',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page,calls}=await panel(browser,{installed:true});
    assert.equal(await page.locator('#uninstall-mod').isDisabled(),true);
    await page.click('#detect-game');await page.waitForFunction(()=>!document.getElementById('uninstall-mod').disabled);
    await page.locator('#drive-panel').evaluate(frame=>{frame.src='about:blank';frame.hidden=false;});
    await page.click('#uninstall-mod');
    assert.equal(await page.locator('#install-mod').isDisabled(),true);
    await page.waitForFunction(()=>document.getElementById('installation-state').textContent.includes('已卸载'));
    assert.equal(await page.locator('#uninstall-mod').isDisabled(),true);
    assert.equal(await page.locator('#open-drive-panel').isDisabled(),true);
    assert.equal(await page.locator('#drive-panel').isHidden(),true);
    assert.equal(await page.locator('#drive-panel').getAttribute('src'),null);
    await page.click('#install-mod');await page.waitForFunction(()=>!document.getElementById('uninstall-mod').disabled);
    assert.equal(await page.locator('#uninstall-mod').isDisabled(),false);
    assert.deepEqual(calls.map(c=>c.entry_id),['detect_game','uninstall_mod','install_mod']);
    assert.equal(calls[1].args.game_path,'D:/游戏 空格');
  } finally {await browser.close();}
});

test('uninstall rejects running game without losing installed state',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page}=await panel(browser,{installed:true,running:true});
    await page.click('#detect-game');await page.waitForFunction(()=>!document.getElementById('uninstall-mod').disabled);
    await page.click('#uninstall-mod');await page.waitForFunction(()=>document.getElementById('installation-error').textContent.includes('退出游戏'));
    assert.equal(await page.locator('#uninstall-mod').isDisabled(),false);
    assert.equal(await page.locator('#install-mod').isDisabled(),true);
    assert.match(await page.locator('#installation-state').textContent(),/卸载未完成/);
  } finally {await browser.close();}
});

test('updated game can still uninstall an existing mod',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const {page,calls}=await panel(browser,{unsupported:true,installed:true});
    await page.click('#detect-game');await page.waitForFunction(()=>!document.getElementById('uninstall-mod').disabled);
    assert.equal(await page.locator('#install-mod').isDisabled(),true);
    await page.click('#uninstall-mod');await page.waitForFunction(()=>document.getElementById('installation-state').textContent.includes('已卸载'));
    assert.equal(await page.locator('#uninstall-mod').isDisabled(),true);
    assert.deepEqual(calls.map(c=>c.entry_id),['detect_game','uninstall_mod']);
  } finally {await browser.close();}
});

