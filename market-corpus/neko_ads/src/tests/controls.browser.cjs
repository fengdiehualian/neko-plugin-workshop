// Isolated two-page check: actual control panel and main renderer, without live TTS or user sessions.
const {createServer} = require('node:http');
const {readFile, mkdir} = require('node:fs/promises');
const path = require('node:path');
const {createRequire} = require('node:module');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const {chromium, expect} = createRequire(path.join(root, 'frontend/plugin-manager/package.json'))('@playwright/test');
const assets = path.resolve(__dirname, '../static');
const state = {ready: false, active: false, owner: null, round_id: null, shortcut: 'Ctrl+Shift+1',
  desktop: null, desktop_ready: false, character: null, voice_enabled: false, feedback: {}};
let starts = 0, enables = 0, holdStart = false, releaseStart;
const retired = new Set();
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end('<!doctype html><meta charset="utf-8"><textarea aria-label="聊天输入"></textarea><script>window.lanlan_config={lanlan_name:"测试猫娘"}</script><script defer src="/plugin/neko_ads/ui/desktop-bridge.js"></script>');
    } else if (url.pathname.startsWith('/plugin/neko_ads/ui/')) {
      const name = url.pathname.split('/').at(-1) || 'index.html';
      if (!['index.html', 'main.js', 'desktop-bridge.js', 'desktop.css', 'engine.js', 'ad-templates.js'].includes(name)) { res.writeHead(404); res.end(); return; }
      res.setHeader('Content-Type', name.endsWith('.html') ? 'text/html; charset=utf-8' : name.endsWith('.css') ? 'text/css' : 'text/javascript');
      res.end(await readFile(path.join(assets, name)));
    } else if (url.pathname === '/plugin/neko_ads/start') { enables++; state.ready = true; json({success: true}); }
    else if (url.pathname === '/plugin/neko_ads/stop') { Object.assign(state, {ready:false, active:false, round_id:null, owner:null}); json({success:true}); }
    else if (url.pathname === '/plugin/status') json({status: {status: state.ready ? 'running' : 'stopped'}});
    else if (url.pathname.endsWith('/context')) {
      if (!state.ready) { res.statusCode = 409; json({detail: 'Plugin not running'}); }
      else json({state: {...state}});
    }
    else if (url.pathname.endsWith('/action/desktop_command')) {
      let body = ''; for await (const chunk of req) body += chunk;
      const {command, payload} = JSON.parse(body).args;
      if (!state.ready) { res.statusCode = 409; json({detail:'Plugin not running'}); return; }
      if (command === 'poll') Object.assign(state, {desktop_ready: true, desktop: {client_id: payload.client_id, character: payload.character}});
      if (command === 'start') {
        starts++; assert.equal(payload.confirmed, true);
        if (holdStart) await new Promise(resolve => { releaseStart = resolve; });
        if (!retired.has(payload.round_id)) Object.assign(state, {active: true, round_id: payload.round_id, owner: payload.owner, character: state.desktop.character, shortcut: payload.shortcut});
      }
      if (command === 'stop') {
        if (payload.round_id) retired.add(payload.round_id);
        if (!payload.round_id || payload.round_id === state.round_id) Object.assign(state, {active: false, round_id: null, owner: null});
      }
      if (command === 'shortcut') state.shortcut = payload.shortcut;
      if (command === 'voice') state.voice_enabled = payload.enabled;
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
    const context = await browser.newContext({viewport: {width: 1100, height: 900}});
    const host = await context.newPage(), panel = await context.newPage();
    host.setDefaultTimeout(8000); panel.setDefaultTimeout(8000);
    await host.goto(base); await panel.goto(base + '/plugin/neko_ads/ui/');
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'disabled');
    await expect(panel.getByRole('button', {name: '一键启动插件', exact: true})).toBeEnabled();
    await panel.getByRole('button', {name: '一键启动插件', exact: true}).click();
    await expect(host.locator('#neko-ads-launcher')).toContainText('待机中');
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'standby');
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0); assert.equal(starts, 0);
    assert.equal(enables, 1); await expect(panel.locator('#enable')).toHaveText('一键关闭插件');
    await expect(panel.locator('#enable')).toBeEnabled();
    await expect(panel.locator('#start')).toHaveText(/^杂鱼♡让本喵\s*占领桌面！$/);
    await expect(panel.locator('#stop')).toHaveText('杂鱼♡这就认输啦？');
    await panel.locator('#voice').uncheck();
    await panel.locator('#start').click();
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'playing');
    // A cold renderer validates again on its next scheduled poll after loading assets.
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(1, {timeout:10000});
    await panel.locator('#stop').click();
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'standby'); assert.equal(state.ready, true);

    await host.bringToFront(); await host.locator('#neko-ads-launcher').click();
    await expect(host.locator('#neko-ads-start')).toHaveText('杂鱼♡让本喵占领桌面！');
    await expect(host.locator('#neko-ads-stop')).toHaveText('杂鱼♡这就认输啦？');
    await expect(host.locator('#neko-ads-back')).toHaveText('杂鱼♡先放你回去（返回主页面）');
    await expect(host.locator('#neko-ads-dialog')).not.toContainText('点击「');
    await expect(host.locator('#neko-ads-dialog')).not.toContainText('插件管理');
    await host.locator('#neko-ads-back').click();
    await expect(host.locator('#neko-ads-dialog')).not.toBeVisible(); assert.equal(starts, 1);
    await host.locator('#neko-ads-launcher').click();
    await host.locator('#neko-ads-key').press('Control+Alt+K');
    await expect(host.locator('#neko-ads-notice')).toContainText('Ctrl+Alt+K');
    await host.locator('#neko-ads-start').click();
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(1);
    await host.locator('textarea').press('Control+Alt+K');
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);
    await expect(host.locator('#neko-ads-launcher')).toContainText('待机中');
    assert.equal(state.active, false);

    await host.locator('#neko-ads-launcher').click(); await host.locator('#neko-ads-start').click();
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(1);
    await host.getByRole('textbox', {name: '聊天输入'}).fill('广告下班');
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);

    await host.locator('#neko-ads-launcher').click(); await host.locator('#neko-ads-start').click();
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(1);
    await host.locator('#neko-ads-launcher').click(); await host.locator('#neko-ads-stop').click();
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);
    await host.locator('#neko-ads-back').click();

    await panel.locator('#enable').click();
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'disabled');
    await expect(panel.locator('#start')).toBeDisabled();
    await panel.getByRole('button', {name: '一键启动插件', exact: true}).click();
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'standby');
    await expect(host.locator('#neko-ads-launcher')).toContainText('待机中');
    assert.equal(starts, 4); await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);
    assert.equal(enables, 2);

    await panel.locator('#start').click(); await expect(host.locator('.neko-ad-placeholder')).toHaveCount(1);
    await panel.locator('#enable').click();
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'disabled');
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);
    await expect(host.locator('#neko-ads-launcher')).not.toBeVisible();
    await panel.locator('#enable').click();
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'standby');
    await expect(host.locator('#neko-ads-launcher')).toContainText('待机中');
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);

    holdStart = true; await panel.locator('#start').click();
    await expect(panel.locator('#stop')).toHaveText('杂鱼♡这就反悔啦？');
    await expect.poll(() => typeof releaseStart).toBe('function');
    await panel.locator('#stop').click(); releaseStart();
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'standby');
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0); assert.equal(state.active, false);

    releaseStart = undefined;
    await panel.locator('#start').click();
    await expect.poll(() => typeof releaseStart).toBe('function');
    await panel.locator('#enable').click(); releaseStart();
    await expect(panel.locator('body')).toHaveAttribute('data-phase', 'disabled');
    await expect(host.locator('.neko-ad-placeholder')).toHaveCount(0);
    await panel.locator('#enable').click(); await expect(panel.locator('body')).toHaveAttribute('data-phase', 'standby');
    const output = path.join(root, 'tmp/neko-ads-install/v0.2.0-step1');
    await mkdir(output, {recursive: true});
    await panel.screenshot({path: path.join(output, 'control-panel.png'), fullPage: true});
    await host.bringToFront(); await host.locator('#neko-ads-launcher').click();
    await host.screenshot({path: path.join(output, 'desktop-dialog.png'), fullPage: true});
    await context.close();
    console.log('Controls passed: plugin on/off in standby, during game and pending start; both popup/game button sets; annotated return; concise dialog; safety word and customized shortcut; no stale revival.');
  } finally {
    if (browser) await browser.close();
    if (releaseStart) releaseStart();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
