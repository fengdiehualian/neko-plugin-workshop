// Actual renderer, engine and template files; only the host, TTS and browser opener are faked.
const {createServer} = require('node:http');
const {readFile, mkdir} = require('node:fs/promises');
const {createRequire} = require('node:module');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const {chromium, expect} = createRequire(path.join(root, 'frontend/plugin-manager/package.json'))('@playwright/test');
const assets = path.resolve(__dirname, '../static');
const state = {ready: true, active: false, owner: null, round_id: null, shortcut: 'Ctrl+Shift+1', voice_enabled: false, feedback: {}};
const server = createServer(async (req, res) => {
  const json = body => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); };
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(`<!doctype html><meta charset="utf-8"><style>body{background:#edf4fb}textarea{position:fixed;bottom:10px;left:12px;width:280px;height:40px}</style>
        <textarea aria-label="聊天输入"></textarea><script src="/plugin/neko_ads/ui/engine.js"></script>
        <script>window.__opened=[];window.lanlan_config={lanlan_name:'测试猫娘'};window.electronShell={openExternal:url=>{window.__opened.push(url);return Promise.resolve({success:true});}};
        const Original=NekoAds.Game;NekoAds.Game=class extends Original{constructor(options){super(options);window.__game=this;}};</script>
        <script defer src="/plugin/neko_ads/ui/desktop-bridge.js"></script>`); return;
    }
    if (url.pathname.startsWith('/plugin/neko_ads/ui/')) {
      const name = url.pathname.slice('/plugin/neko_ads/ui/'.length), file = path.resolve(assets, name);
      if (!file.startsWith(assets + path.sep)) throw new Error('invalid asset path');
      res.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : name.endsWith('.svg') ? 'image/svg+xml' : name.endsWith('.png') ? 'image/png' : 'text/javascript');
      res.end(await readFile(file)); return;
    }
    if (url.pathname === '/plugin/status') { json({status: {status: 'running'}}); return; }
    if (url.pathname === '/market/status') { json({market_web_url: 'https://market.project-neko.cn'}); return; }
    if (url.pathname.includes('/market/catalog/')) { json({items: []}); return; }
    if (url.pathname.endsWith('/action/desktop_command')) {
      let body = ''; for await (const chunk of req) body += chunk;
      const {command, payload} = JSON.parse(body).args;
      if (command === 'start') Object.assign(state, {active: true, owner: payload.owner, round_id: payload.round_id, shortcut: payload.shortcut});
      if (command === 'stop' && (!payload.round_id || payload.round_id === state.round_id)) state.active = false;
      if (command === 'shortcut') state.shortcut = payload.shortcut;
      json({result: {data: {...state}}}); return;
    }
    res.writeHead(404); res.end();
  } catch (error) { res.writeHead(500); res.end(error.message); }
});

(async () => {
  let browser;
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    browser = await chromium.launch({channel: 'msedge', headless: true});
    const page = await browser.newPage({viewport: {width: 1100, height: 850}});
    const errors = []; page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(8000);
    await page.goto('http://127.0.0.1:' + server.address().port);
    const start = async () => {
      await page.locator('#neko-ads-launcher').click();
      await page.locator('#neko-ads-voice').uncheck();
      await page.locator('#neko-ads-start').click();
      await expect(page.locator('.neko-ad-placeholder').first()).toBeVisible();
    };
    const show = async seed => page.evaluate(seed => {
      const game = window.__game; game.pause(); game.paused = false; game.cards = []; game.random = () => seed;
      for (const kind of ['news', 'webgame']) {
        game.cursor = NekoAds.BUILTINS.findIndex(card => card.kind === kind); game.spawn();
        Object.assign(game.cards.at(-1), {x: kind === 'news' ? 0 : 1, y: .1});
      }
      game.onChange();
    }, seed);
    const countOpens = () => page.evaluate(() => window.__opened.length);
    await start();
    for (const [index, target] of ['frame', 'strip', 'corner'].entries()) {
      await show((index + .1) / 3);
      const card = page.locator('.neko-ad-webgame');
      await expect(card.locator('[data-ad-close]')).toHaveCount(3);
      await expect(card.locator('.neko-ad-art-link')).toHaveText('');
      await expect(card.locator('.neko-ad-art')).toHaveCount(1);
      const before = await countOpens();
      for (const fake of ['frame', 'strip', 'corner'].filter(value => value !== target)) {
        await card.locator('[data-ad-close=' + fake + ']').click();
        await expect(card).toBeVisible();
      }
      await page.getByRole('button', {name: '插件发现', exact: true}).click();
      await expect(page.locator('.neko-ad-news-story')).toHaveCount(2);
      await card.locator('[data-ad-close=' + target + ']').click();
      await expect(card).toHaveCount(0); await expect(page.locator('.neko-ad-news')).toHaveCount(1);
      assert.equal(await countOpens(), before, 'no X may accidentally activate the underlying image');
      assert.equal(state.active, true);
    }
    await show(.5);
    await page.locator('.neko-ad-art-link').click();
    assert.equal(await countOpens(), 1);
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.locator('.neko-ad-game-cta').click();
    assert.deepEqual(await page.evaluate(() => window.__opened), ['https://project-neko.online/', 'https://project-neko.online/']);
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    // An actual image uses the same link; loading errors return to the empty slot.
    await page.evaluate(() => { const game = window.__game; game.cards.find(card => card.kind === 'webgame').image = '/plugin/neko_ads/ui/assets/neko-logo.png'; game.onChange(); });
    await expect(page.locator('.neko-ad-art')).toBeVisible();
    await page.locator('.neko-ad-art').click(); assert.equal(await countOpens(), 3);
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.evaluate(() => { const game = window.__game; game.cards.find(card => card.kind === 'webgame').image = '/missing-image.png'; game.onChange(); });
    await expect(page.locator('.neko-ad-art')).toHaveCount(0);
    await page.getByRole('textbox', {name: '聊天输入'}).fill('广告下班');
    await expect(page.locator('.neko-ad-placeholder')).toHaveCount(0);
    await start(); await show(.5);
    await page.locator('.neko-ad-close-corner').press('Control+Shift+1');
    await expect(page.locator('.neko-ad-placeholder')).toHaveCount(0);
    await page.locator('#neko-ads-launcher').click();
    await page.locator('#neko-ads-key').press('Control+Alt+K');
    await page.locator('#neko-ads-start').click();
    await expect(page.locator('.neko-ad-placeholder').first()).toBeVisible(); await show(.5);
    await page.locator('.neko-ad-close-corner').press('Control+Alt+K');
    await expect(page.locator('.neko-ad-placeholder')).toHaveCount(0);
    await start(); await show(.5); await page.setViewportSize({width: 390, height: 740});
    const boxes = await page.locator('.neko-ad-modern').evaluateAll(nodes => nodes.map(node => ({left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right, scroll: node.scrollWidth, width: node.clientWidth})));
    for (const box of boxes) { assert.ok(box.left >= 0 && box.right <= 390); assert.ok(box.scroll <= box.width + 1); }
    const clear = await page.locator('.neko-ad-webgame [data-ad-close=strip]').evaluate(node => {
      const box = node.getBoundingClientRect(), launcher = document.getElementById('neko-ads-launcher').getBoundingClientRect();
      return box.bottom <= launcher.top || box.top >= launcher.bottom || box.right <= launcher.left || box.left >= launcher.right;
    });
    assert.equal(clear, true, 'the fixed host launcher must not cover a candidate X');
    const out = path.join(root, 'tmp/neko-ads-design/ad-layout-v020/qa'); await mkdir(out, {recursive: true});
    await page.screenshot({path: path.join(out, 'desktop-narrow.png')});
    await page.getByRole('textbox', {name: '聊天输入'}).fill('广告下班'); await expect(page.locator('.neko-ad-placeholder')).toHaveCount(0);
    assert.deepEqual(errors, []);
    console.log('Templates passed: all three true-close positions; rerender stability; no click-through from X; tabs; blank/image/CTA links; failed image; safety word; default/custom shortcuts; narrow viewport; no page errors.');
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
