// Isolated renderer regression: no user chat, installed plugin, or OS browser is opened.
const {createServer} = require('node:http');
const {readFile} = require('node:fs/promises');
const path = require('node:path');
const {createRequire} = require('node:module');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const {chromium, expect} = createRequire(path.join(root, 'frontend/plugin-manager/package.json'))('@playwright/test');
const assets = path.resolve(__dirname, '../static');
let state, voiceJob, voiceMode, releaseResource, failedPoll, cancelRequests = [];
const pcm = Buffer.alloc(96000).toString('base64');
const waveHeader = Buffer.alloc(44);
waveHeader.write('RIFF'); waveHeader.writeUInt32LE(96036, 4); waveHeader.write('WAVEfmt ', 8);
waveHeader.writeUInt32LE(16, 16); waveHeader.writeUInt16LE(1, 20); waveHeader.writeUInt16LE(1, 22);
waveHeader.writeUInt32LE(48000, 24); waveHeader.writeUInt32LE(96000, 28);
waveHeader.writeUInt16LE(2, 32); waveHeader.writeUInt16LE(16, 34); waveHeader.write('data', 36); waveHeader.writeUInt32LE(96000, 40);
const cachedAudio = Buffer.concat([waveHeader, Buffer.alloc(96000)]).toString('base64');
const reset = () => { state = {ready: true, active: false, owner: null, round_id: null, shortcut: 'Ctrl+Shift+1', voice_enabled: false,
  voice_preparation:{state:'ready',ready:9,total:9,token:'test-voice'},
  feedback: {start: {text: '测试开业', emotion: 'happy'}, fake: {text: '你关闭的是广告的广告哦', emotion: 'happy'}, view: {text: '点进去了嘛', emotion: 'happy'}}}; };
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end('<!doctype html><meta charset="utf-8"><textarea aria-label="聊天输入"></textarea><script>window.lanlan_config={lanlan_name:"测试猫娘"}</script><script defer src="/plugin/neko_ads/ui/desktop-bridge.js"></script>');
    } else if (url.pathname.startsWith('/plugin/neko_ads/ui/voice-cache/')) {
      if (voiceMode === 'voice-resource-pending') await new Promise(resolve => { releaseResource = resolve; });
      res.setHeader('Content-Type','audio/wav');res.end(Buffer.from(cachedAudio,'base64'));
    } else if (url.pathname.startsWith('/plugin/neko_ads/ui/')) {
      const name = url.pathname.split('/').at(-1);
      if (!['desktop-bridge.js', 'desktop.css', 'engine.js', 'ad-templates.js'].includes(name)) { res.writeHead(404); res.end(); return; }
      res.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
      res.end(await readFile(path.join(assets, name)));
    } else if (url.pathname.endsWith('/action/desktop_command')) {
      let body = ''; for await (const chunk of req) body += chunk;
      const {command, payload} = JSON.parse(body).args;
      if (command==='poll' && voiceMode==='voice-reconnect' && state.active && voiceJob && !failedPoll) {
        failedPoll=true;res.statusCode=503;json({detail:'Transient test disconnect'});return;
      }
      if (command === 'voice') state.voice_enabled = payload.enabled;
      if (command === 'shortcut') state.shortcut = payload.shortcut;
      if (command === 'start') Object.assign(state, {active: true, round_id: payload.round_id, owner: payload.owner, voice_enabled: payload.voice_enabled === true});
      if (command === 'stop' && (!payload.round_id || payload.round_id === state.round_id)) Object.assign(state, {active: false, round_id: null, owner: null});
      if (command === 'speech') {
        voiceJob = {id: 'voice-' + payload.sequence, sequence: payload.sequence, polls: 0, released: false};
        json({result: {data: voiceMode.startsWith('voice-resource') ? {audio_url:'/ui/voice-cache/11111111-1111-4111-8111-111111111111_'+payload.event+'.wav'} : {job_id: voiceJob.id}}}); return;
      }
      if (command === 'cancel_speech') {
        cancelRequests.push(payload);
        if (voiceJob && payload.sequence >= voiceJob.sequence) voiceJob.cancelled = true;
      }
      if (command === 'speech_result') {
        let data = {ready: true};
        if (state.active && voiceJob && payload.job_id === voiceJob.id && !voiceJob.cancelled) {
          data = voiceMode === 'voice-parts' ? (() => { const bytes = Buffer.from(cachedAudio,'base64'), offset = voiceJob.polls++ * 48000; return {ready:offset+48000>=bytes.length,audio_part:bytes.subarray(offset,offset+48000).toString('base64')}; })() :
            ['voice-cache','voice-queued','voice-reconnect'].includes(voiceMode) ? {ready: true, audio: cachedAudio, mime: 'audio/wav'} :
            voiceJob.polls++ < 2 ? {ready: false, pcm, sample_rate: 48000} : {ready: voiceJob.released};
        }
        json({result: {data}}); return;
      }
      json({result: {data: {...state}}});
    } else if (url.pathname === '/market/status') json({market_web_url: 'https://market.project-neko.cn'});
    else if (url.pathname.includes('/market/catalog/')) json({items: []});
    else { res.writeHead(404); res.end(); }
  } catch (error) { res.writeHead(500); res.end(error.message); }
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({channel: 'msedge', headless: true});
    async function setup(mode) {
      reset(); voiceJob = null; voiceMode = mode; releaseResource = null; failedPoll = false; cancelRequests = [];
      if (mode.startsWith('voice-resource')) state.voice_preparation.clips = Object.fromEntries(['start','fake','view'].map(event=>[event,'/ui/voice-cache/11111111-1111-4111-8111-111111111111_'+event+'.wav']));
      const context = await browser.newContext({viewport: {width: 1100, height: 800}, ...(mode === 'missing' ? {userAgent: 'N.E.K.O Electron/40.0.0'} : {})});
      await context.route('https://**/*', route => route.fulfill({contentType: 'text/html', body: '<h1>可关闭的测试标签页</h1>'}));
      const page = await context.newPage(); page.setDefaultTimeout(8000);
      await page.addInitScript(mode => {
        window.__openedAds = [];
        window.__adsSounds = {started: 0, stopped: 0};
        const start = AudioBufferSourceNode.prototype.start, stop = AudioBufferSourceNode.prototype.stop;
        AudioBufferSourceNode.prototype.start = function (...args) { window.__adsSounds.started++; return start.apply(this, args); };
        AudioBufferSourceNode.prototype.stop = function (...args) { window.__adsSounds.stopped++; return stop.apply(this, args); };
        if (mode !== 'web' && mode !== 'missing') window.electronShell = {openExternal: url => {
          window.__openedAds.push(url);
          if (mode === 'reject') return Promise.reject(new Error('Unavailable'));
          if (mode === 'fail') return Promise.resolve({success: false});
          if (mode === 'pending') return new Promise(resolve => { window.__finishAdsOpen = resolve; });
          return Promise.resolve({success: true});
        }};
      }, mode);
      await page.goto(base);
      await page.locator('#neko-ads-launcher').click();
      if (mode.startsWith('voice')) await page.locator('#neko-ads-voice').check();
      else await page.locator('#neko-ads-voice').uncheck();
      await page.locator('#neko-ads-start').click();
      await page.locator('.neko-ad-placeholder').first().waitFor();
      return {context, page, link: page.locator('.neko-ad-placeholder a').first()};
    }
    async function exit(page) {
      await page.getByRole('textbox', {name: '聊天输入'}).fill('广告下班');
      await expect(page.locator('.neko-ad-placeholder')).toHaveCount(0);
    }
    for (const button of ['left', 'middle']) {
      const {context, page, link} = await setup('native');
      const target = await link.getAttribute('href');
      await link.click({button});
      await expect.poll(() => page.evaluate(() => window.__openedAds.length)).toBe(1);
      assert.deepEqual(await page.evaluate(() => window.__openedAds), [target]);
      await exit(page); assert.equal(context.pages().length, 1);
      await context.close();
    }
    for (const mode of ['reject', 'fail', 'missing']) {
      const {context, page, link} = await setup(mode);
      await link.click();
      await expect(page.locator('[role="alert"]')).toContainText('系统默认浏览器');
      await expect(page.locator('.neko-ad-placeholder')).toHaveCount(1);
      await exit(page); assert.equal(context.pages().length, 1);
      await context.close();
    }
    {
      const {context, page, link} = await setup('web');
      const target = await link.getAttribute('href'), opened = page.waitForEvent('popup');
      await link.click(); const popup = await opened;
      await popup.waitForURL(target); await popup.close(); await page.bringToFront();
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await expect(page.locator('.neko-ad-placeholder')).toHaveCount(1);
      await exit(page); await context.close();
    }
    {
      const {context, page, link} = await setup('pending');
      await link.click(); await exit(page);
      await page.evaluate(async () => { window.__finishAdsOpen({success: false}); await Promise.resolve(); });
      await expect(page.locator('[role="alert"]')).toHaveCount(0);
      await expect(page.locator('.neko-ad-placeholder')).toHaveCount(0);
      assert.equal(context.pages().length, 1); await context.close();
    }
    {
      const {context,page} = await setup('voice-resource-pending');
      await expect.poll(()=>typeof releaseResource).toBe('function');
      await exit(page); releaseResource();
      await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,200)));
      assert.equal(await page.evaluate(()=>window.__adsSounds.started),0,'a late local audio response cannot play after the safety word');
      await context.close();
    }
    {
      const {context,page} = await setup('voice-reconnect');
      await expect.poll(()=>page.evaluate(()=>window.__adsSounds.started)).toBe(1);
      await expect.poll(()=>failedPoll).toBe(true);
      await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,1100)));
      assert.equal(await page.evaluate(()=>window.__adsSounds.stopped),0,'one transient status failure must not cut the current line');
      await expect(page.locator('.neko-ad-placeholder')).toHaveCount(1);
      await exit(page);await context.close();
    }
    for (const mode of ['voice-stream', 'voice-cache', 'voice-resource', 'voice-parts', 'voice-replaced']) {
      const {context, page} = await setup(mode);
      const count = ['voice-cache','voice-resource','voice-parts'].includes(mode) ? 1 : 2;
      await expect.poll(() => page.evaluate(() => window.__adsSounds.started)).toBe(count);
      if (mode === 'voice-resource') assert.equal(voiceJob,null,'prepared playback must not issue a speech action');
      else assert.equal(voiceJob.released, false, 'the first sound must not wait for generation to finish');
      if (mode === 'voice-replaced') {
        await page.locator('.neko-ad-placeholder header button').first().click();
        await expect(page.locator('.neko-ad-placeholder h3').first()).toContainText('广告的广告');
        assert.equal(await page.evaluate(() => window.__adsSounds.stopped), 0, 'a quick text reaction must not cut the current voice');
        const previous = cancelRequests.length;
        await page.evaluate(() => window.dispatchEvent(new Event('blur')));
        await expect.poll(() => cancelRequests.slice(previous).some(request => request.preserve === false)).toBe(true);
      }
      await exit(page);
      await expect.poll(() => page.evaluate(() => window.__adsSounds.stopped)).toBe(count);
      if (voiceJob) voiceJob.released = true;
      await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 200)));
      assert.equal(await page.evaluate(() => window.__adsSounds.started), count, 'late voice packets cannot resume after exit');
      await context.close();
    }
    {
      const {context,page} = await setup('voice-queued');
      await expect.poll(() => page.evaluate(() => window.__adsSounds.started)).toBe(1);
      await page.locator('.neko-ad-placeholder header button').first().click();
      assert.equal(await page.evaluate(() => window.__adsSounds.stopped),0);
      await expect.poll(() => page.evaluate(() => window.__adsSounds.started)).toBe(2);
      await page.evaluate(() => window.dispatchEvent(new Event('neko-assistant-speech-start')));
      await expect.poll(() => page.evaluate(() => window.__adsSounds.stopped)).toBe(1);
      await exit(page); await context.close();
    }
    for (const shortcut of ['Control+Shift+Digit1','Control+Alt+K']) {
      const {context,page} = await setup('voice-stream');
      await expect.poll(() => page.evaluate(() => window.__adsSounds.started)).toBe(2);
      if (shortcut.includes('Alt')) {
        await page.locator('#neko-ads-launcher').click();
        await page.locator('#neko-ads-key').press(shortcut);
        await page.getByRole('button',{name:'杂鱼♡先放你回去（返回主页面）',exact:true}).click();
      }
      await page.locator('textarea').press(shortcut);
      await expect(page.locator('.neko-ad-placeholder')).toHaveCount(0);
      await expect.poll(() => page.evaluate(() => window.__adsSounds.stopped)).toBe(2);
      await context.close();
    }
    console.log('Renderer passed: external links; streamed and cached speech; quick reactions preserve current audio; pause and safety exit cancel all audio.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
