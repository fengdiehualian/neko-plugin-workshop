// Real Chromium, HTTP routes, product controller and Win32 callbacks in an isolated host.
const {spawn} = require('node:child_process');
const {createRequire} = require('node:module');
const {mkdir, writeFile} = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const {chromium, expect} = createRequire(path.join(root, 'frontend/plugin-manager/package.json'))('@playwright/test');
const child = spawn(path.join(root,'.venv/Scripts/python.exe'), ['-u',path.join(__dirname,'safety_host_fixture.py')], {cwd:root,windowsHide:true});
let stderr = ''; child.stderr.on('data',chunk=>stderr+=chunk);
const ready = new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(new Error('Fixture did not start: '+stderr)),20000);
  child.stdout.on('data',chunk=>{text+=chunk;const match=text.match(/\{"port":\s*(\d+),\s*"pid":\s*(\d+)\}/);if(match){clearTimeout(timer);resolve({port:+match[1],pid:+match[2]});}});
  child.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+stderr));});
});
(async()=>{
  let browser, base; const results={environment:'isolated source host; no Steam host changes; no TTS', checks:[]};
  try {
    const identity=await ready;base='http://127.0.0.1:'+identity.port;results.fixture=identity;
    const post=async(url,data={})=>{const r=await fetch(base+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert.equal(r.ok,true,await r.text());};
    const state=async()=>await (await fetch(base+'/__test__/state')).json();
    browser=await chromium.launch({channel:'msedge',headless:true});
    const context=await browser.newContext();const page=await context.newPage();page.setDefaultTimeout(10000);
    results.page_errors=[];page.on('pageerror',error=>results.page_errors.push(error.message));
    await page.goto(base);const launcher=page.locator('#neko-ads-launcher'),cards=page.locator('.neko-ad-placeholder');
    const openDialog=async()=>{if(!await page.locator('#neko-ads-dialog').evaluate(el=>el.open))await launcher.click();};
    const start=async(shortcut='Ctrl+Shift+1')=>{
      await expect(launcher).toContainText('待机中');await openDialog();
      if(shortcut!=='Ctrl+Shift+1')await page.locator('#neko-ads-key').press(shortcut.replaceAll('Ctrl','Control').replaceAll('+','+'));
      await page.locator('#neko-ads-voice').uncheck();await page.locator('#neko-ads-start').click();
      await expect(cards.first()).toBeVisible();
    };
    for(const shortcut of ['Ctrl+Shift+1','Ctrl+Alt+K']){
      await post('/__test__/mode',{poll_delay:0});await start(shortcut);
      await post('/__test__/mode',{poll_delay:8});
      await page.waitForTimeout(2000); // A real action query is now awaiting its reply.
      const began=Date.now();await post('/__test__/native-exit');
      await expect(cards).toHaveCount(0,{timeout:1800});
      assert.equal((await state()).active,false);
      results.checks.push({name:'native key with hung action '+shortcut,clear_ms:Date.now()-began,passed:true});
      await post('/__test__/mode',{poll_delay:0});await expect(launcher).toContainText('待机中',{timeout:12000});
    }
    await start('Ctrl+Alt+K');await post('/__test__/mode',{poll_delay:8});
    await expect(launcher).toContainText('重连',{timeout:12000});
    const chat=await context.newPage();await chat.goto(base+'/chat');
    await chat.getByRole('textbox',{name:'聊天输入'}).fill('广告下班');
    await expect(cards).toHaveCount(0,{timeout:1000});await expect.poll(async()=>(await state()).active).toBe(false);
    results.checks.push({name:'fresh chat safety word during reconnection, local and backend',passed:true});
    await chat.close();await post('/__test__/mode',{poll_delay:0});
    await expect(launcher).toContainText('待机中',{timeout:12000});

    await openDialog();await post('/__test__/mode',{start_before:2});
    await page.locator('#neko-ads-start').click();
    await expect(page.locator('#neko-ads-dialog')).toHaveAttribute('data-phase','starting');
    const pendingChat=await context.newPage();await pendingChat.goto(base+'/chat');
    await pendingChat.getByRole('textbox',{name:'聊天输入'}).fill('广告下班！');
    await page.waitForTimeout(3000);assert.equal((await state()).active,false);await expect(cards).toHaveCount(0);
    results.checks.push({name:'stop arrives before delayed start executes; no backend revival',passed:true});
    await post('/__test__/mode',{start_before:0});
    await pendingChat.close();
    await expect(launcher).toContainText('待机中');await openDialog();
    await post('/__test__/mode',{start_after:8});await page.locator('#neko-ads-start').click();
    await expect(page.locator('#neko-ads-dialog')).toHaveAttribute('data-phase','starting');
    await page.keyboard.press('Control+Alt+K');
    await expect.poll(async()=>(await state()).active).toBe(false);await page.waitForTimeout(8500);
    await expect(cards).toHaveCount(0);assert.equal((await state()).active,false);
    results.checks.push({name:'custom exit while accepted start acknowledgement is delayed; no revival',passed:true});
    const output=path.join(root,'tmp/neko-ads-install/v0.2.0-step5');await mkdir(output,{recursive:true});
    await writeFile(path.join(output,'safety-browser.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  } finally {
    if(browser)await browser.close();
    if(base)await fetch(base+'/__test__/shutdown',{method:'POST'}).catch(()=>{});
    const ended=new Promise(resolve=>child.once('exit',resolve));
    await Promise.race([ended,new Promise(resolve=>setTimeout(resolve,5000))]);
    if(child.exitCode===null)child.kill();
  }
})().catch(error=>{console.error(error,stderr.slice(-1000));process.exitCode=1;});
