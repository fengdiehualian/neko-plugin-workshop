const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const NekoAds = require('../static/engine.js');

function node() {
  return {handlers: {}, hidden: false, disabled: false, textContent: '',
    addEventListener(type, fn) { this.handlers[type] = fn; },
    focus() { this.focused = true; }, scrollIntoView() { this.scrolled = true; }};
}
function page({electron = false, shell, parent} = {}) {
  const link = Object.assign(node(), {href: 'https://market.project-neko.cn/#/plugins'});
  const status = node(), content = node(), empty = Object.assign(node(), {hidden: true});
  const response = node(), close = node(), reset = node(), decoys = [node(), node()];
  const selectors = {'.sample-content': content, '.sample-empty': empty, '.demo-response': response,
    '[data-demo-close]': close, '[data-demo-reset]': reset};
  const sample = {querySelector: key => selectors[key], querySelectorAll: () => decoys};
  const window = {NekoAds, electronShell: shell, parent: parent || {}};
  // This page must not issue game commands or schedule additional polling.
  const forbidden = () => { throw new Error('Showroom attempted a runtime action'); };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/site.js'), 'utf8'), {
    window, navigator: {userAgent: electron ? 'Electron' : 'Browser'}, fetch: forbidden,
    setTimeout: forbidden, setInterval: forbidden,
    document: {getElementById: () => status, querySelectorAll: selector => selector === '[data-demo]' ? [sample] : [link]},
  });
  const click = () => ({type: 'click', button: 0, prevented: false, preventDefault() { this.prevented = true; }});
  return {link, status, content, empty, response, close, reset, decoys, click};
}

test('showroom tricks, close and replay stay local and never start a desktop round', () => {
  const h = page(); h.decoys[0].handlers.click();
  assert.match(h.response.textContent, /ざぁこ/); assert.equal(h.content.hidden, false);
  h.close.handlers.click(); assert.equal(h.content.hidden, true); assert.equal(h.empty.hidden, false);
  assert.equal(h.reset.focused, true); assert.ok(h.decoys.every(n => n.disabled));
  h.reset.handlers.click(); assert.equal(h.content.hidden, false); assert.equal(h.empty.hidden, true);
  assert.ok(h.decoys.every(n => !n.disabled)); assert.equal(h.close.focused, true);
});

test('static panel iframe uses the host system-browser bridge, including middle click', async () => {
  const opened = [], h = page({electron: true, parent: {electronShell: {openExternal: async url => opened.push(url)}}});
  const event = h.click(); await h.link.handlers.click(event); assert.equal(event.prevented, true);
  const middle = {...h.click(), type: 'auxclick', button: 1}; await h.link.handlers.auxclick(middle);
  assert.equal(middle.prevented, true); assert.equal(opened.length, 2);
});

test('unavailable or inaccessible Electron opener cancels the child window and shows a visible error', async () => {
  const inaccessible = Object.defineProperty({}, 'electronShell', {get() { throw new Error('cross origin'); }});
  for (const options of [{parent: inaccessible}, {shell: {openExternal: async () => ({success: false})}}]) {
    const h = page({electron: true, ...options}), event = h.click(); await h.link.handlers.click(event);
    assert.equal(event.prevented, true); assert.match(h.status.textContent, /浏览器/); assert.equal(h.status.scrolled, true);
  }
});

test('ordinary browser image links keep normal closable new-tab navigation', async () => {
  const h = page(), event = h.click(); await h.link.handlers.click(event);
  assert.equal(event.prevented, false); assert.equal(h.status.textContent, '');
});

test('a native bridge with a custom user agent still blocks unavailable native opening', async () => {
  const h = page({shell: {}}), event = h.click(); await h.link.handlers.click(event);
  assert.equal(event.prevented, true); assert.match(h.status.textContent, /浏览器/);
});
