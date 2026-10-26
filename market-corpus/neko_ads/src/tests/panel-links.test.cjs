const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const NekoAds = require('../static/engine.js');

function panelLink({shell, parent = {}, electron = false} = {}) {
  const handlers = {}, status = {textContent: ''};
  const link = {href: 'http://127.0.0.1:48916/plugin/neko_ads/ui/website.html',
    addEventListener: (type, fn) => { handlers[type] = fn; }};
  const forbidden = () => { throw new Error('Website entry must not control or poll the game'); };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/panel-links.js'), 'utf8'), {
    window: {NekoAds, electronShell: shell, parent}, navigator: {userAgent: electron ? 'Electron' : 'Browser'},
    document: {getElementById: id => id === 'open-website' ? link : status},
    fetch: forbidden, setTimeout: forbidden, setInterval: forbidden,
  });
  const event = {type: 'click', button: 0, prevented: false, preventDefault() { this.prevented = true; }};
  return {handlers, status, event, link};
}

test('website entry opens its separate local URL through the parent system browser', async () => {
  const opened = [], h = panelLink({parent: {electronShell: {openExternal: async url => opened.push(url)}}});
  await h.handlers.click(h.event);
  assert.deepEqual(opened, [h.link.href]); assert.equal(h.event.prevented, true); assert.equal(h.status.textContent, '');
  const middle = {...h.event, type: 'auxclick', button: 1, prevented: false};
  await h.handlers.auxclick(middle); assert.equal(middle.prevented, true); assert.equal(opened.length, 2);
});

test('ordinary browsers retain the anchor new-tab behavior without changing the panel', async () => {
  const h = panelLink(); await h.handlers.click(h.event);
  assert.equal(h.event.prevented, false); assert.equal(h.status.textContent, '');
});

test('missing or failed native opening shows an error instead of an unclosable child', async () => {
  for (const options of [{electron: true}, {shell: {openExternal: async () => false}},
    {shell: {openExternal: async () => { throw new Error('Unavailable'); }}}]) {
    const h = panelLink(options); await h.handlers.click(h.event);
    assert.equal(h.event.prevented, true); assert.match(h.status.textContent, /浏览器/);
  }
});
