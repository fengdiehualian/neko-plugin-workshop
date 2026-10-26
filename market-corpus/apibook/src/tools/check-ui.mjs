// Run against the actual N.E.K.O Hosted UI compiler, types, kit and iframe bridge.
import { createRequire } from 'node:module';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const neko = resolve(process.argv[2] || process.env.NEKO_SOURCE || '');
if (!process.argv[2] && !process.env.NEKO_SOURCE) throw new Error('Pass the N.E.K.O source root as the first argument.');
const require = createRequire(resolve(neko, 'frontend/plugin-manager/package.json'));
const ts = require('typescript');
const { transform } = require('sucrase');
const { chromium, expect } = require('@playwright/test');
const hosted = resolve(neko, 'frontend/plugin-manager/src/components/plugin/hosted');
const { bundleHostedTsxSource } = await import(pathToFileURL(resolve(hosted, 'hostedTsxModule.mjs')));
const panel = resolve(root, 'ui/panel.tsx');
const program = ts.createProgram([panel, resolve(neko, 'plugin/sdk/hosted-ui/globals.d.ts')], {
  jsx: ts.JsxEmit.React, jsxFactory: 'h', jsxFragmentFactory: 'Fragment',
  module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020,
  moduleResolution: ts.ModuleResolutionKind.Bundler, baseUrl: neko,
  paths: { '@neko/plugin-ui': ['plugin/sdk/hosted-ui'] },
  noEmit: true, strict: false, skipLibCheck: true, esModuleInterop: true,
});
const diagnostics = ts.getPreEmitDiagnostics(program);
if (diagnostics.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
  getCanonicalFileName: f => f, getCurrentDirectory: () => root, getNewLine: () => '\n',
}));
const runtime = transform(readFileSync(resolve(hosted, 'tsxRuntime.ts'), 'utf8').replace(/^import .+;?\s*$/gm, ''), { transforms: ['typescript'] }).code
  .replace('export function buildHostedTsxDocument', 'function buildHostedTsxDocument');
const build = new Function('transform', 'buildUiKitBundle', 'bundleHostedTsxSource', 'window', runtime + '\nreturn buildHostedTsxDocument;')(
  transform, () => ({ styles: readFileSync(resolve(hosted, 'ui-kit/styles.css'), 'utf8'), runtime: readFileSync(resolve(hosted, 'ui-kit/runtime.js'), 'utf8') }),
  bundleHostedTsxSource, { location: { origin: 'http://127.0.0.1:48911' } },
);
const messages = JSON.parse(readFileSync(resolve(root, 'i18n/zh-CN.json'), 'utf8'));
const state = { presets: [], max_presets: 100, catalog: {
  core: ['free', 'qwen', 'openai'], assist: ['free', 'qwen', 'openai'], labels: { free: '免费版', qwen: 'Qwen', openai: 'OpenAI', elevenlabs: 'ElevenLabs' },
  key_fields: { qwen: 'assistApiKeyQwen', openai: 'assistApiKeyOpenai', elevenlabs: 'assistApiKeyElevenlabs' }, slot_providers: Object.fromEntries(
    ['conversation','summary','gameMain','gameSummary','correction','emotion','vision','agent','omni','tts','image'].map(s => [s,
      s === 'omni' ? ['follow_core', 'custom'] : s === 'image' ? ['disabled', 'custom'] : s === 'gameMain' ? ['follow_conversation', 'follow_core', 'follow_assist', 'custom'] : s === 'gameSummary' ? ['follow_summary', 'follow_core', 'follow_assist', 'custom'] : s === 'tts' ? ['follow_core', 'follow_assist', 'custom'] : ['follow_assist', 'custom']]),
  ),
} };
const context = { state, plugin: { id: 'apibook' }, i18n: { locale: 'zh-CN', default_locale: 'en', messages: { 'zh-CN': messages } } };
const html = build({ source: readFileSync(panel, 'utf8'), pluginId: 'apibook', surface: { id: 'main', kind: 'panel', entry: 'ui/panel.tsx' }, context, locale: 'zh-CN' });
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const failures = [];
try {
  const page = await browser.newPage({ viewport: { width: 1360, height: 980 } });
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => failures.push(error.message));
  const calls = [];
  // Test host keeps plaintext only in its backend simulation; context always masked.
  const secrets = new Map();
  const bookSecrets = new Map();
  const vendorSecrets = new Map();
  await page.exposeFunction('hostAction', data => {
    calls.push(data);
    if (data.method === 'refresh') return context;
    const { actionId: id, args } = data.payload;
    if (id === 'save_preset') {
      for (const vendor of Object.values(args.preset.vendors || {})) {
        const url = new URL(vendor.url);
        if (!['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol)) throw new Error('Invalid URL');
      }
      state.connectivity = null;
      const saved = structuredClone(args.preset);
      const item = { id: args.preset_id || 'preset-' + (state.presets.length + 1), revision: args.revision + 1, preset: saved };
      const storedKeys = Object.fromEntries(Object.entries(saved.keys).map(([provider, key]) => [provider, key === '__API_PRESETS_KEEP_SECRET__' ? bookSecrets.get(item.id)?.[provider] || '' : key]));
      bookSecrets.set(item.id, storedKeys); secrets.set(item.id, storedKeys.qwen);
      const vendorKeys = {};
      for (const [vendorId, vendor] of Object.entries(saved.vendors || {})) {
        vendorKeys[vendorId] = vendor.key === '__API_PRESETS_KEEP_SECRET__' ? vendorSecrets.get(item.id)?.[vendorId] || '' : vendor.key;
        vendor.key = vendorKeys[vendorId] ? '__API_PRESETS_KEEP_SECRET__' : '';
      }
      vendorSecrets.set(item.id, vendorKeys);
      saved.keys = Object.fromEntries(Object.entries(storedKeys).map(([provider, key]) => [provider, key ? '__API_PRESETS_KEEP_SECRET__' : '']));
      state.presets = [...state.presets.filter(p => p.id !== item.id), item];
      return { result: item };
    }
    if (id === 'copy_preset') {
      const source = state.presets.find(p => p.id === args.preset_id);
      const item = structuredClone(source); item.id = 'preset-' + (state.presets.length + 1); item.revision = 1; item.preset.name = args.name;
      secrets.set(item.id, secrets.get(source.id)); bookSecrets.set(item.id, structuredClone(bookSecrets.get(source.id))); vendorSecrets.set(item.id, structuredClone(vendorSecrets.get(source.id))); state.presets.push(item); return { result: item };
    }
    if (id === 'current_api_draft') return { result: { preset: {
      name: '', notes: '', core: 'qwen', assist: 'free', enableCustomApi: false, keys: {}, slots: {
        conversation: { provider: 'custom', url: 'https://import.example.test/v1', model: 'import-model', key: '' },
      },
    }, skipped_slots: ['vision'] } };
    if (id === 'delete_preset') { state.presets = state.presets.filter(p => p.id !== args.preset_id); return { result: { deleted: true } }; }
    if (id === 'apply_preset') { assert.equal(args.confirmed, true); return { result: { applied: true } }; }
    if (id === 'test_preset') return new Promise(resolve => setTimeout(() => {
      assert.equal(args.revision, state.presets.find(item => item.id === args.preset_id).revision);
      state.connectivity = { preset_id: args.preset_id, revision: args.revision, tested: 3, groups: [
        { functions: ['core', 'omni'], model: 'voice-model', status: 'connected' },
        { functions: ['assist', 'conversation', 'summary', 'gameMain', 'gameSummary', 'correction', 'vision', 'agent'], model: 'shared-model', status: 'connected', free_policy: true },
        { functions: ['emotion'], model: 'small-model', status: 'failed', error_code: 'auth_failed', http_status: 401 },
        { functions: ['tts'], model: '', status: 'unsupported', note: 'voice_required' },
        { functions: ['image'], model: '', status: 'not_configured', note: 'disabled' },
      ] };
      resolve({ result: state.connectivity });
    }, 160));
    throw new Error('Unexpected action: ' + id);
  });
  await page.route('http://127.0.0.1:48911/api-presets-qa', route => route.fulfill({ contentType: 'text/html', body: '<iframe id="hosted" style="width:100%;height:950px;border:0"></iframe>' }));
  await page.goto('http://127.0.0.1:48911/api-presets-qa');
  await page.evaluate(() => window.addEventListener('message', async event => {
    if (event.data.type !== 'neko-hosted-surface-request') return;
    try {
      const result = await window.hostAction(event.data);
      event.source.postMessage({ type: 'neko-hosted-surface-response', requestId: event.data.requestId, ok: true, result }, '*');
    } catch (error) {
      event.source.postMessage({ type: 'neko-hosted-surface-response', requestId: event.data.requestId, ok: false, error: error.message }, '*');
    }
  }));
  await page.locator('#hosted').evaluate((frame, content) => { frame.srcdoc = content; }, html);
  const frame = page.frameLocator('#hosted');
  async function clickCentered(locator) {
    await locator.evaluate(element => element.scrollIntoView({ block: 'center' }));
    await locator.click();
  }
  page.on('console', msg => { if (msg.type() === 'error') failures.push(msg.text()); });
  assert.equal(await frame.getByRole('button', { name: '复制一个预设', exact: true }).isDisabled(), true);
  await frame.getByRole('button', { name: '新建', exact: true }).click();
  const qwenKey = frame.locator('[data-provider="qwen"] input[type=password]');
  const openaiKey = frame.locator('[data-provider="openai"] input[type=password]');
  const sidebar = await frame.locator('.api-presets-layout > .neko-card').boundingBox();
  const editor = await frame.locator('.api-presets-editor').boundingBox();
  assert.equal(editor.width > sidebar.width * 2.5, true);
  const toolbar = frame.locator('.api-presets-editor-bar');
  assert.equal(await toolbar.getByRole('button', { name: '保存预设', exact: true }).count(), 1);
  assert.equal(await toolbar.getByRole('button', { name: '应用到 N.E.K.O', exact: true }).count(), 1);
  assert.equal(await toolbar.getByRole('button', { name: '删除预设', exact: true }).count(), 1);
  await frame.locator('input').first().fill('工作账号');
  await frame.locator('textarea').fill('用于日常对话');
  await frame.locator('select').first().selectOption('qwen');
  await frame.getByRole('button', { name: 'API 管理簿', exact: true }).click();
  assert.equal(await frame.locator('.api-presets-key-row').count(), 3);
  await qwenKey.fill('test-only-secret');
  // Store an unselected provider now, then choose it without losing either key.
  await openaiKey.fill('test-only-backup-key');
  await frame.locator('select').first().selectOption('openai');
  assert.equal(await qwenKey.inputValue(), 'test-only-secret');
  assert.equal(await openaiKey.inputValue(), 'test-only-backup-key');
  await frame.locator('select').first().selectOption('qwen');
  assert.equal(await frame.getByRole('button', { name: '应用到 N.E.K.O', exact: true }).isDisabled(), true);
  await frame.getByRole('button', { name: '保存预设', exact: true }).click();
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.equal(calls.filter(c => c.payload?.actionId === 'test_preset').length, 1);
  assert.equal(await frame.locator('.api-presets-light').count(), 5);
  assert.equal(await frame.locator('.api-presets-light[data-status=connected]').count(), 2);
  assert.equal(await frame.locator('.api-presets-light[data-status=failed]').count(), 1);
  const sharedLight = frame.locator('.api-presets-light-tip').nth(1);
  await sharedLight.hover();
  assert.match(await sharedLight.getByRole('tooltip').innerText(), /功能：辅助 API、对话、总结/);
  assert.match(await sharedLight.getByRole('tooltip').innerText(), /模型：shared-model/);
  assert.equal((await sharedLight.getByRole('tooltip').innerText()).includes('HTTP 400'), false);
  await expect(sharedLight.getByRole('tooltip')).toHaveCSS('opacity', '1');
  await sharedLight.focus();
  assert.equal(await sharedLight.getByRole('tooltip').evaluate(element => getComputedStyle(element).visibility), 'visible');
  const failedLight = frame.locator('.api-presets-light-tip').nth(2);
  await failedLight.hover();
  assert.match(await failedLight.getByRole('tooltip').innerText(), /HTTP 401/);
  assert.equal((await failedLight.getByRole('tooltip').innerText()).includes(messages['probe.error.auth_failed']), true);
  await frame.locator('textarea').fill('更新备注');
  assert.equal(await frame.locator('.api-presets-light[data-status=connected]').count(), 0);
  assert.equal(await frame.getByRole('button', { name: '测试连通性', exact: true }).isDisabled(), true);
  await frame.locator('textarea').fill('用于日常对话');
  await frame.getByRole('button', { name: '测试连通性', exact: true }).click();
  await frame.locator('.api-presets-light[data-status=testing]').first().waitFor();
  await frame.getByText(messages['probe.finished'], { exact: true }).waitFor();
  assert.equal(calls.filter(c => c.payload?.actionId === 'test_preset').length, 2);
  assert.equal(await frame.locator('.api-presets-layout > .neko-card').getByText('核心 API：Qwen', { exact: true }).count(), 1);
  assert.equal(await frame.locator('.api-presets-layout > .neko-card').getByText('辅助 API：免费版', { exact: true }).count(), 1);
  assert.equal(await qwenKey.inputValue(), '');
  assert.equal(await openaiKey.inputValue(), '');
  assert.equal(bookSecrets.get(state.presets[0].id).openai, 'test-only-backup-key');
  assert.equal(calls.filter(c => c.payload?.actionId === 'apply_preset').length, 0);
  await frame.getByRole('button', { name: '复制一个预设', exact: true }).click();
  assert.equal(await frame.getByRole('button', { name: '创建副本', exact: true }).isDisabled(), true);
  assert.equal(calls.filter(c => c.payload?.actionId === 'copy_preset').length, 0);
  await frame.getByRole('combobox').first().selectOption(state.presets[0].id);
  await frame.getByRole('button', { name: '创建副本', exact: true }).click();
  await frame.getByText('已创建独立副本，可在下方修改名称。', { exact: true }).waitFor();
  await frame.locator('input').first().fill('备用账号');
  await frame.locator('textarea').fill('独立副本');
  await frame.getByRole('button', { name: '保存预设', exact: true }).click();
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.equal(state.presets[0].preset.name, '工作账号');
  assert.equal(state.presets[1].preset.name, '备用账号');
  // Copy a different preset from the one currently selected in the editor.
  await frame.getByRole('button', { name: '工作账号', exact: true }).click();
  await frame.getByRole('button', { name: '复制一个预设', exact: true }).click();
  await frame.getByRole('combobox').first().selectOption(state.presets[1].id);
  await frame.getByRole('button', { name: '取消', exact: true }).click();
  assert.equal(calls.filter(c => c.payload?.actionId === 'copy_preset').length, 1);
  await frame.getByRole('button', { name: '复制一个预设', exact: true }).click();
  await frame.getByRole('combobox').first().selectOption(state.presets[1].id);
  await frame.getByRole('button', { name: '创建副本', exact: true }).click();
  await frame.getByText('已创建独立副本，可在下方修改名称。', { exact: true }).waitFor();
  assert.equal(calls.filter(c => c.payload?.actionId === 'copy_preset').at(-1).payload.args.preset_id, state.presets[1].id);
  assert.equal(state.presets[2].preset.notes, '独立副本');
  assert.equal(secrets.get(state.presets[2].id), 'test-only-secret');
  assert.equal(bookSecrets.get(state.presets[2].id).openai, 'test-only-backup-key');
  const savesBeforeImport = calls.filter(c => c.payload?.actionId === 'save_preset').length;
  await frame.getByRole('button', { name: '基于当前 API 页面新建', exact: true }).click();
  await frame.getByText('已基于 N.E.K.O 当前 API 设置创建草稿。', { exact: true }).waitFor();
  assert.equal(await frame.locator('input').first().inputValue(), '');
  assert.equal(await qwenKey.inputValue(), '');
  assert.equal(await openaiKey.inputValue(), '');
  assert.equal(calls.filter(c => c.payload?.actionId === 'save_preset').length, savesBeforeImport);
  assert.equal(calls.filter(c => c.payload?.actionId === 'apply_preset').length, 0);
  await frame.getByText(messages['panel.importHelp'], { exact: true }).waitFor();
  await frame.getByText(/这些槽位的选择暂不支持.*视觉/).waitFor();
  // Copy remains usable with an unsaved new draft and no selected saved preset.
  assert.equal(await frame.getByRole('button', { name: '复制一个预设', exact: true }).isDisabled(), false);
  await frame.locator('input').first().fill('当前页面草稿');
  await frame.getByRole('button', { name: '保存预设', exact: true }).click();
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.equal(state.presets[3].preset.slots.conversation.model, 'import-model');
  assert.equal(state.presets[3].preset.enableCustomApi, false);
  await frame.getByText('自定义模型与跟随方式', { exact: true }).click();
  const customSwitch = frame.getByRole('checkbox', { name: '启用自定义 API', exact: true });
  assert.equal(await customSwitch.isChecked(), false);
  const importedCard = frame.locator('.neko-card').filter({ has: frame.getByRole('heading', { name: '对话', exact: true }) }).last();
  assert.equal(await importedCard.locator('input').nth(1).inputValue(), 'import-model');
  await customSwitch.click();
  assert.equal(await customSwitch.isChecked(), true);
  await customSwitch.click();
  assert.equal(await importedCard.locator('input').nth(1).inputValue(), 'import-model');
  await frame.locator('textarea').fill('关闭开关后仍保留模型');
  await toolbar.getByRole('button', { name: '保存预设', exact: true }).click();
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.equal(state.presets[3].preset.enableCustomApi, false);
  assert.equal(state.presets[3].preset.slots.conversation.model, 'import-model');
  await frame.getByText('自定义模型与跟随方式', { exact: true }).click();
  assert.deepEqual(state.presets[3].preset.keys, {});
  await frame.getByRole('button', { name: '备用账号', exact: true }).click();
  await frame.getByText('自定义模型与跟随方式', { exact: true }).click();
  await customSwitch.click();
  await frame.locator('select').nth(2).selectOption('custom');
  // The slot-local reset clears its custom triple before the next save.
  await frame.getByRole('button', { name: '恢复为跟随核心／辅助 API', exact: true }).first().click();
  await frame.getByRole('button', { name: '保存预设', exact: true }).click();
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.deepEqual(state.presets.find(item => item.preset.name === '备用账号').preset.slots.conversation, { provider: 'follow_assist' });
  await frame.getByRole('button', { name: '＋', exact: true }).click();
  const vendorEditor = frame.locator('.api-presets-vendor-editor');
  assert.equal(await vendorEditor.getByRole('button', { name: '删除厂商', exact: true }).count(), 0);
  assert.equal(await toolbar.getByRole('button', { name: '保存预设', exact: true }).isDisabled(), true);
  await vendorEditor.locator('input').nth(0).fill('自建厂商');
  await vendorEditor.locator('input').nth(1).fill('111');
  await vendorEditor.locator('input[type=password]').fill('vendor-test-secret');
  await clickCentered(vendorEditor.getByRole('button', { name: '保存厂商', exact: true }));
  await frame.getByText('Invalid URL', { exact: true }).waitFor();
  assert.equal(await vendorEditor.count(), 1);
  assert.equal(await frame.locator('.api-presets-vendor-row').count(), 0);
  await vendorEditor.locator('input').nth(1).fill('https://vendor.example.test/v1');
  await clickCentered(vendorEditor.getByRole('button', { name: '保存厂商', exact: true }));
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.equal(await vendorEditor.count(), 0);
  const vendorRow = frame.locator('.api-presets-vendor-row').first();
  await expect(vendorRow.locator('.api-presets-vendor-name')).toHaveCSS('color', 'rgb(32, 134, 60)');
  assert.equal(await vendorRow.getByRole('button', { name: '编辑厂商', exact: true }).innerText(), '📝');
  assert.equal(await vendorRow.getByRole('button', { name: '删除厂商', exact: true }).innerText(), '×');
  const conversationCard = frame.locator('.neko-card').filter({ has: frame.getByRole('heading', { name: '对话', exact: true }) }).last();
  await conversationCard.locator('select').selectOption('vendor:vendor_1');
  await conversationCard.locator('input').nth(1).fill('vendor-model');
  assert.equal(await conversationCard.locator('input').first().inputValue(), 'https://vendor.example.test/v1');
  assert.equal(await conversationCard.locator('input').first().isDisabled(), true);
  assert.equal(await vendorRow.getByRole('button', { name: '删除厂商', exact: true }).isDisabled(), true);
  await frame.getByRole('button', { name: '保存预设', exact: true }).click();
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  const vendorPreset = state.presets.find(item => item.preset.name === '备用账号');
  assert.equal(vendorPreset.preset.vendors.vendor_1.key, '__API_PRESETS_KEEP_SECRET__');
  assert.equal(vendorSecrets.get(vendorPreset.id).vendor_1, 'vendor-test-secret');
  assert.deepEqual(vendorPreset.preset.slots.conversation, { provider: 'custom', vendor_id: 'vendor_1', model: 'vendor-model' });
  assert.equal(await vendorRow.locator('input[type=password]').inputValue(), '');
  assert.equal(await frame.locator('select').first().locator('option[value="vendor:vendor_1"]').count(), 0);
  // Cancel leaves the saved provider and linked model unchanged.
  await clickCentered(vendorRow.getByRole('button', { name: '编辑厂商', exact: true }));
  assert.equal(await vendorEditor.locator('input[type=password]').inputValue(), '');
  await vendorEditor.locator('input').nth(1).fill('https://cancelled.example.test/v1');
  await clickCentered(vendorEditor.getByRole('button', { name: '取消', exact: true }));
  assert.equal(await conversationCard.locator('input').first().inputValue(), 'https://vendor.example.test/v1');
  // Saved book edits flow through the slot reference, with the stored key retained.
  await clickCentered(vendorRow.getByRole('button', { name: '编辑厂商', exact: true }));
  await vendorEditor.locator('input').nth(1).fill('https://changed.example.test/v1');
  assert.equal(await conversationCard.locator('input').first().inputValue(), 'https://vendor.example.test/v1');
  await clickCentered(vendorEditor.getByRole('button', { name: '保存厂商', exact: true }));
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.equal(await conversationCard.locator('input').first().inputValue(), 'https://changed.example.test/v1');
  assert.equal(vendorSecrets.get(vendorPreset.id).vendor_1, 'vendor-test-secret');
  await frame.getByRole('button', { name: '＋', exact: true }).click();
  await clickCentered(vendorEditor.getByRole('button', { name: '取消', exact: true }));
  assert.equal(await frame.locator('.api-presets-vendor-row').count(), 1);
  await frame.getByRole('button', { name: '＋', exact: true }).click();
  await vendorEditor.locator('input').nth(0).fill('待删除厂商');
  await vendorEditor.locator('input').nth(1).fill('https://unused.example.test/v1');
  await clickCentered(vendorEditor.getByRole('button', { name: '保存厂商', exact: true }));
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  await frame.locator('[data-vendor="vendor_2"]').getByRole('button', { name: '删除厂商', exact: true }).click();
  await frame.getByRole('dialog').getByRole('button', { name: '删除厂商', exact: true }).click();
  assert.equal(await frame.locator('[data-vendor="vendor_2"]').count(), 0);
  await toolbar.getByRole('button', { name: '保存预设', exact: true }).click();
  await frame.getByText(messages['probe.savedTested'], { exact: true }).waitFor();
  assert.equal(state.presets.find(item => item.id === vendorPreset.id).preset.vendors.vendor_2, undefined);
  const vendorOutput = resolve(root, 'artifacts'); mkdirSync(vendorOutput, { recursive: true });
  const bookColumns = frame.locator('.api-presets-key-book .neko-columns');
  await bookColumns.evaluate(element => element.scrollIntoView({ block: 'center' }));
  await bookColumns.screenshot({ path: resolve(vendorOutput, 'vendor-list.png') });
  await frame.getByRole('button', { name: '应用到 N.E.K.O', exact: true }).click();
  await frame.getByRole('dialog').getByRole('button', { name: '应用到 N.E.K.O', exact: true }).click();
  await frame.getByText('N.E.K.O 已确认配置应用成功。', { exact: true }).waitFor();
  assert.equal(calls.filter(c => c.payload?.actionId === 'apply_preset').length, 1);
  await frame.getByText('自定义模型与跟随方式', { exact: true }).click();
  await frame.locator('body').evaluate(() => window.scrollTo(0, 0));
  // The editor toolbar stays reachable while scrolling a long key book.
  const saveTop = await toolbar.getByRole('button', { name: '保存预设', exact: true }).boundingBox();
  await frame.locator('body').evaluate(() => window.scrollTo(0, 500));
  const saveScrolled = await toolbar.getByRole('button', { name: '保存预设', exact: true }).boundingBox();
  assert.equal(saveScrolled.y > 0 && saveScrolled.y < saveTop.y, true);
  await frame.locator('body').evaluate(() => window.scrollTo(0, 0));
  const output = resolve(root, 'artifacts'); mkdirSync(output, { recursive: true });
  await page.screenshot({ path: resolve(output, 'panel-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await frame.locator('body').evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
  for (const index of [0, 4]) {
    const tip = frame.locator('.api-presets-light-tip').nth(index);
    await tip.hover();
    await expect(tip.getByRole('tooltip')).toHaveCSS('opacity', '1');
    const rect = await tip.getByRole('tooltip').evaluate(element => ({ left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right, width: window.innerWidth }));
    assert.equal(rect.left >= 0 && rect.right <= rect.width, true, `Mobile tooltip ${index} must fit inside the viewport`);
  }
  await page.screenshot({ path: resolve(output, 'panel-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 820, height: 980 });
  const mediumSidebar = await frame.locator('.api-presets-layout > .neko-card').boundingBox();
  const mediumEditor = await frame.locator('.api-presets-editor').boundingBox();
  assert.equal(mediumEditor.y > mediumSidebar.y + mediumSidebar.height, true);
  assert.equal(await frame.locator('body').evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
  assert.deepEqual(failures, []);
  writeFileSync(resolve(output, 'ui-check.json'), JSON.stringify({ types: 'passed', browser: 'Chromium', actions: calls.filter(c => c.method === 'call').length, desktop: 'passed', mobile: 'passed', errors: failures }, null, 2) + '\n', 'utf8');
  console.log('Hosted TSX types, compiler, iframe actions, key book, wide editor, sticky toolbar and responsive layout passed.');
} finally { await browser.close(); }
