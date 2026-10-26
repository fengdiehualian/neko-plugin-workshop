const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/script.js'), 'utf8');

function panel(publish) {
  const elements = new Map();
  for (const id of ['dynamic-text', 'dynamic-urls', 'dynamic-files', 'dynamic-result', 'toast']) {
    elements.set(id, {
      value: '', files: [], hidden: false, disabled: false, textContent: '',
      classList: { toggle() {}, add() {}, remove() {} },
      replaceChildren(...children) { this.children = children; },
    });
  }
  const context = vm.createContext({
    location: { pathname: '/plugin/bilibili_integration/ui/' },
    window: { addEventListener() {} },
    document: {
      getElementById: (id) => elements.get(id),
      querySelectorAll: () => [],
      createElement: () => ({}),
    },
    FileReader: class {
      readAsDataURL(file) { this.result = file.data; this.onload(); }
    },
    setTimeout: () => 1, clearTimeout() {}, console,
  });
  vm.runInContext(source, context);
  context.callPlugin = publish;
  return { elements, submit: () => context.publishDynamic({ preventDefault() {} }) };
}

test('publishes text, remote images and selected files, then clears the draft', async () => {
  const calls = [];
  const { elements, submit } = panel(async (entry, args) => {
    calls.push({ entry, args });
    assert.equal(elements.get('dynamic-text').disabled, true);
    return { dynamic_id: '1234567890123456789' };
  });
  elements.get('dynamic-text').value = 'hello';
  elements.get('dynamic-urls').value = 'https://example.com/a.png\n';
  elements.get('dynamic-files').files = [{ type: 'image/png', size: 100, data: 'data:image/png;base64,eA==' }];
  await submit();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].entry, 'publish_dynamic');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].args)), {
    text: 'hello', images: ['https://example.com/a.png', 'data:image/png;base64,eA=='],
  });
  assert.equal(elements.get('dynamic-text').value, '');
  assert.equal(elements.get('dynamic-urls').value, '');
  assert.equal(elements.get('dynamic-files').value, '');
  assert.equal(elements.get('dynamic-result').children[0].href, 'https://t.bilibili.com/1234567890123456789');
  assert.equal(elements.get('dynamic-text').disabled, false);
});

test('keeps the draft and displays uncertainty when publishing fails', async () => {
  let calls = 0;
  const { elements, submit } = panel(async () => {
    calls += 1;
    throw new Error('DYNAMIC_PUBLISH_UNCERTAIN: private backend detail');
  });
  elements.get('dynamic-text').value = 'keep my draft';
  await submit();
  assert.equal(calls, 1);
  assert.equal(elements.get('dynamic-text').value, 'keep my draft');
  assert.equal(elements.get('dynamic-text').disabled, false);
  assert.match(elements.get('dynamic-result').textContent, /Check Bilibili/);
  assert.doesNotMatch(elements.get('dynamic-result').textContent, /private backend detail/);
});

test('suppresses repeated submits while a request is in flight', async () => {
  let calls = 0;
  let finish;
  const done = new Promise((resolve) => { finish = resolve; });
  const { elements, submit } = panel(async () => { calls += 1; return done; });
  elements.get('dynamic-text').value = 'hello';
  const first = submit();
  await submit();
  finish({ dynamic_id: '42' });
  await first;
  assert.equal(calls, 1);
});

test('rejects invalid or excessive content before making a request', async () => {
  const invalid = [
    { text: '' }, { text: 'x'.repeat(2001) },
    { text: 'hello', urls: 'file:///private/a.png' },
    { text: 'hello', urls: Array(10).fill('https://example.com/a.png').join('\n') },
    { files: [{ type: 'image/svg+xml', size: 10 }] },
    { files: [{ type: 'image/png', size: 10 * 1024 * 1024 + 1 }] },
  ];
  for (const item of invalid) {
    let calls = 0;
    const { elements, submit } = panel(async () => { calls += 1; });
    elements.get('dynamic-text').value = item.text || '';
    elements.get('dynamic-urls').value = item.urls || '';
    elements.get('dynamic-files').files = item.files || [];
    await submit();
    assert.equal(calls, 0);
    assert.notEqual(elements.get('toast').textContent, '');
  }
});

test('an invalid success response keeps the draft and cannot create an unsafe link', async () => {
  const { elements, submit } = panel(async () => ({ dynamic_id: 'javascript:alert(1)' }));
  elements.get('dynamic-text').value = 'keep my draft';
  await submit();
  assert.equal(elements.get('dynamic-text').value, 'keep my draft');
  assert.match(elements.get('dynamic-result').textContent, /Check Bilibili/);
  assert.equal(elements.get('dynamic-result').children, undefined);
});
