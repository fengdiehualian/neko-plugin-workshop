// Real browser timers and HTTP cancellation, with no live host/TTS/native keys.
const {createServer} = require('node:http');
const {readFile, mkdir, writeFile} = require('node:fs/promises');
const {createRequire} = require('node:module');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const {chromium, expect} = createRequire(path.join(root, 'frontend/plugin-manager/package.json'))('@playwright/test');
const state = {ready:true,active:false,owner:null,round_id:null,shortcut:'Ctrl+Shift+1',voice_enabled:false,feedback:{},
  connection_policy:{poll_timeout_ms:6000,reconnect_grace_ms:20000,active_poll_ms:1500,idle_poll_ms:3000,lease_seconds:30}};
let mode = 'normal', slowCompleted = 0, starts = 0;
const held = new Set(), timers = new Set(), retired = new Set();
const server = createServer(async (req,res) => {
  const json = data => { if (!res.destroyed) {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));} };
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/') {
      res.setHeader('Content-Type','text/html; charset=utf-8');
      res.end('<!doctype html><meta charset="utf-8"><textarea aria-label="聊天输入"></textarea><script>window.lanlan_config={lanlan_name:"测试猫娘"}</script><script defer src="/plugin/neko_ads/ui/desktop-bridge.js"></script>');return;
    }
    if (url.pathname.startsWith('/plugin/neko_ads/ui/')) {
      const name=url.pathname.split('/').at(-1);
      if (!['desktop-bridge.js','desktop.css','engine.js','ad-templates.js'].includes(name)) {res.writeHead(404);res.end();return;}
      res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');
      res.end(await readFile(path.join(__dirname,'../static',name)));return;
    }
    if (url.pathname === '/plugin/status') {json({status:{status:'running'}});return;}
    if (url.pathname === '/market/status') {json({market_web_url:'https://market.project-neko.cn'});return;}
    if (url.pathname.includes('/market/catalog/')) {json({items:[]});return;}
    if (url.pathname.endsWith('/action/desktop_command')) {
      let body='';for await (const chunk of req) body+=chunk;
      const {command,payload}=JSON.parse(body).args;
      const perform=()=>{
        if(command==='start'&&!retired.has(payload.round_id)) {starts++;Object.assign(state,{active:true,owner:payload.owner,round_id:payload.round_id,shortcut:payload.shortcut});}
        if(command==='stop') {if(payload.round_id)retired.add(payload.round_id);if(!payload.round_id||payload.round_id===state.round_id)state.active=false;}
        if(command==='shortcut')state.shortcut=payload.shortcut;
        if(command==='poll')Object.assign(state,{desktop_ready:true,desktop:{client_id:payload.client_id,character:payload.character}});
        json({result:{data:{...state}}});
      };
      if(mode==='hold') {held.add(perform);return;}
      if(mode==='slow'&&command==='poll') {
        const timer=setTimeout(()=>{timers.delete(timer);slowCompleted++;perform();},4700);timers.add(timer);return;
      }
      perform();return;
    }
    res.writeHead(404);res.end();
  } catch(e) {if(!res.destroyed){res.writeHead(500);res.end(e.message);}}
});
const recover=()=>{mode='normal';for(const finish of held)finish();held.clear();};
(async()=>{
  let browser;
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    browser=await chromium.launch({channel:'msedge',headless:true});
    const page=await browser.newPage();page.setDefaultTimeout(10000);
    await page.goto('http://127.0.0.1:'+server.address().port);
    const cards=page.locator('.neko-ad-placeholder'), launcher=page.locator('#neko-ads-launcher');
    const begin=async()=>{await expect(launcher).toContainText('待机中');await launcher.click();await page.locator('#neko-ads-start').click();await expect(cards.first()).toBeVisible();};
    await begin();const first=state.round_id;mode='slow';
    await expect.poll(()=>slowCompleted,{timeout:14000}).toBeGreaterThan(0);
    assert.equal(state.round_id,first);assert.equal(state.active,true);assert.equal(starts,1);
    await expect(cards.first()).toBeVisible();mode='normal';

    mode='hold';await expect(launcher).toContainText('重连',{timeout:14000});
    await page.getByRole('textbox',{name:'聊天输入'}).fill('广告下班');
    await expect(cards).toHaveCount(0,{timeout:1000});recover();
    await expect(launcher).toContainText('待机中');await page.waitForTimeout(3500);await expect(cards).toHaveCount(0);

    await launcher.click();await page.locator('#neko-ads-key').press('Control+Alt+K');
    await page.locator('#neko-ads-start').click();await expect(cards.first()).toBeVisible();
    await expect(page.locator('#neko-ads-notice')).toContainText('Ctrl+Alt+K');
    mode='hold';await expect(launcher).toContainText('重连',{timeout:12000});
    await page.getByRole('textbox',{name:'聊天输入'}).press('Control+Shift+1');await expect(cards.first()).toBeVisible();
    await page.getByRole('textbox',{name:'聊天输入'}).press('Control+Alt+K');
    await expect(cards).toHaveCount(0,{timeout:1000});recover();await expect(launcher).toContainText('待机中');

    await begin();mode='hold';
    // The grace starts at the first failed query, after its own request timeout.
    await expect(cards).toHaveCount(0,{timeout:35000});
    await expect(page.locator('#neko-ads-state')).toContainText('持续失联');
    recover();await page.waitForTimeout(4000);await expect(cards).toHaveCount(0);assert.equal(starts,3);
    const output=path.join(root,'tmp/neko-ads-install/v0.2.0-connection-fix');await mkdir(output,{recursive:true});
    await writeFile(path.join(output,'browser-result.json'),JSON.stringify({slow_4700ms_same_round:true,offline_safety_word_immediate:true,offline_custom_key_immediate:true,bounded_loss_ends:true,late_replies_do_not_revive:true},null,2));
    console.log('Connection browser checks passed: real 4.7s response, stalled HTTP, immediate safety/custom exits, bounded 20s loss and no stale revival.');
  } finally {
    for(const timer of timers)clearTimeout(timer);
    if(browser)await browser.close();
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
