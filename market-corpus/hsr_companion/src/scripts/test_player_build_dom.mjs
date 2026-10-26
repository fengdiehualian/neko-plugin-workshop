// Real Hosted linker + UI kit in an isolated DOM. No app, account or network.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { test } from 'node:test'

const root = resolve(import.meta.dirname, '..')
const repo = resolve(root, '../../..')
const require = createRequire(resolve(repo, 'frontend/plugin-manager/package.json'))
const { Window } = require('happy-dom')
const { transform } = require('sucrase')
const { bundleHostedTsxSource } = await import(pathToFileURL(resolve(repo, 'frontend/plugin-manager/src/components/plugin/hosted/hostedTsxModule.mjs')))
const source = readFileSync(resolve(root, 'ui/panel_v09.tsx'), 'utf8')
const dependencies = ['ui/player_build_editor.tsx', 'ui/build_picker.tsx'].map(path => ({ path, source: readFileSync(resolve(root, path), 'utf8') }))
const compiled = transform(bundleHostedTsxSource(source, dependencies, 'ui/panel_v09.tsx'), {
  transforms: ['typescript', 'jsx'], jsxPragma: 'h', jsxFragmentPragma: 'Fragment', production: true,
}).code.replace(/\bexport\s+default\s+function\s+(\w+)?\s*\(/, (_, name) => `const __Panel = function ${name || ''}(`)
const runtime = readFileSync(resolve(repo, 'frontend/plugin-manager/src/components/plugin/hosted/ui-kit/runtime.js'), 'utf8')
const messages = JSON.parse(readFileSync(resolve(root, 'i18n/zh-CN.json'), 'utf8'))
const tick = () => new Promise(resolve => setTimeout(resolve, 0))

test('actual hosted surface switches parallel inputs, prefills, saves only changed field and retains draft across polling', async () => {
  const window = new Window({ url: 'http://127.0.0.1/' })
  const calls = [], errors = []
  window.addEventListener('error', event => errors.push(String(event.error || event.message)))
  const payload = { plugin: { id: 'hsr_companion', name: '隔离源码测试' }, locale: 'zh-CN',
    i18n: { locale: 'zh-CN', default_locale: 'zh-CN', messages: { 'zh-CN': messages } },
    actions: [{ id: 'save_manual_build' }, { id: 'preview_uid_import' }],
    state: { uid_import: { uid: '100000001', status: 'error', error_code: 'provider_unavailable', roster: { characters: [] } },
      player_builds: { characters: [{ form_id: 'character_form.1503', name: '真珠', level: 80, eidolon: 0,
        fetched_at: '2026-10-19T02:09:04Z', source: 'enka.network',
        edit_revision: 'r1', light_cone: { id: 'light_cone.23055', name: '献给明日的色彩', level: 80, superimposition: 1 },
        sets: { outer: null, inner: null } }, { form_id: 'character_form.1501', name: '火花', edit_revision: 'fire-r1',
          level: 70, eidolon: 1, panel: { values: { def: '1800' } }, manual_fields: ['panel'] }] },
      build_options: { characters: [{ id: 'character_form.1503', name: '真珠' }, { id: 'character_form.1501', name: '火花' }],
        light_cones: [{ id: 'light_cone.23055', name: '献给明日的色彩', signature_for: ['真珠'], signature_form_ids: ['character_form.1503'] }], outer_sets: [], inner_sets: [] } } }
  window.__NEKO_PAYLOAD = payload
  window.__fixtureCall = async (id, args) => {
    calls.push(JSON.parse(JSON.stringify({ id, args })))
    const build = { ...payload.state.player_builds.characters[0], edit_revision: 'r2',
      panel: { values: args.changes.panel, state: 'partial' }, manual_fields: ['panel'], manual_updated_at: '2026-10-19T03:00:00Z' }
    payload.state.player_builds.characters[0] = build
    return { result: { player_build_save: { status: 'saved', form_id: args.form_id, build, readback_state: 'confirmed' } } }
  }
  window.fetch = async () => { throw new Error('No network allowed in this test') }
  window.document.body.innerHTML = '<main id="root"></main>'
  try {
    // Happy DOM constructs Text nodes through window.Text; browsers' native
    // createTextNode does not. Keep its constructor while using the actual
    // kit's Text component through the linker-provided module binding.
    const NativeText = window.Text
    window.eval(`(function(){${runtime}})();`)
    window.Text = NativeText
    // Disable only the 3-second polling timer; explicit context re-render is tested below.
    window.NekoUiKit.useEffect = () => {}
    window.eval(`(function(){${compiled}
      const props={...window.__NEKO_PAYLOAD,...window.NekoUiKit,
        api:{call:window.__fixtureCall,refresh:async()=>null}};
      window.__fixtureRender=()=>render(h(__Panel,props),document.getElementById('root'));
      window.__fixtureRender();})();`)
    const radio = window.document.querySelector('input[type=radio][value=manual]')
    assert.ok(radio, 'manual input is a peer of UID, not a hidden error fallback')
    radio.dispatchEvent(new window.Event('change', { bubbles: true }))
    await tick()
    assert.doesNotMatch(window.document.body.textContent, /Enka 本次查询失败/)
    const field = label => {
      const row = [...window.document.querySelectorAll('.neko-field')].find(node => node.textContent.includes(label))
      assert.ok(row, 'rendered field: ' + label)
      return row.querySelector('input,select')
    }
    const select = window.document.getElementById('build-character')
    select.focus()
    await tick()
    assert.ok(window.document.getElementById('build-character') === select, 'opening the popup must preserve the actual input node')
    assert.ok(window.document.activeElement === select, 'opening the popup must preserve real keyboard focus')
    const pearl = [...window.document.querySelectorAll('[role=option]')].find(node => node.textContent === '真珠')
    assert.ok(pearl)
    pearl.click()
    await tick()
    assert.equal(field('角色等级').value, '80')
    assert.equal(field('光锥等级').value, '80')
    const coneInput = window.document.getElementById('build-cone')
    coneInput.focus(); await tick()
    assert.ok(window.document.getElementById('build-cone') === coneInput, 'showing signature options must not replace the input node')
    assert.ok(window.document.activeElement === coneInput, 'signature note toggling must not steal focus')
    coneInput.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' })); await tick()
    const def = field('防御力')
    def.value = '3132'
    def.dispatchEvent(new window.Event('input', { bubbles: true }))
    await tick()
    window.__fixtureRender()
    await tick()
    assert.equal(field('防御力').value, '3132', 'ordinary refresh must retain unsaved input')
    const save = [...window.document.querySelectorAll('button')].find(node => node.textContent === '保存角色资料')
    assert.ok(save && !save.disabled)
    save.click()
    await tick(); await tick()
    assert.deepEqual(calls, [{ id: 'save_manual_build', args: { form_id: 'character_form.1503', changes: { panel: { def: '3132' } }, expected_revision: 'r1' } }])
    assert.match(window.document.body.textContent, /角色资料已保存/)
    assert.match(window.document.body.textContent, /真珠 · 本次保存结果/)
    assert.equal(field('防御力').value, '3132')
    // A saved card must expose the exact same persisted values and edit path.
    window.document.querySelector('input[type=radio][value=uid]').dispatchEvent(new window.Event('change', { bubbles: true }))
    await tick()
    assert.equal(window.document.querySelector('button[data-edit-role]'), null)
    assert.equal([...window.document.querySelectorAll('button')].some(node => node.textContent === '编辑真珠资料'), false, 'saved role details start collapsed')
    const savedList = [...window.document.querySelectorAll('.neko-accordion-trigger')].find(node => node.firstElementChild.textContent === messages['uid.savedDetails'])
    savedList.click(); await tick()
    const savedPearl = [...window.document.querySelectorAll('.neko-accordion-trigger')].find(node => node.firstElementChild.textContent === '真珠 · Lv.80 · 星魂0')
    savedPearl.click(); await tick()
    const edit = [...window.document.querySelectorAll('button')].find(node => node.textContent === '编辑真珠资料')
    assert.ok(edit)
    assert.match(edit.parentElement.textContent, /3132/)
    assert.match(edit.parentElement.textContent, /UID资料＋手动修改/)
    edit.click(); await tick()
    assert.equal(field('防御力').value, '3132')
    assert.equal(window.document.querySelector('input[type=radio][value=manual]').checked, true)
    assert.equal(calls.length, 1, 're-editing does not submit or query online')
    const fire = [...window.document.querySelectorAll('.neko-accordion-trigger')].find(node => node.firstElementChild.textContent === '火花 · Lv.70 · 星魂1')
    fire.click(); await tick()
    ;[...window.document.querySelectorAll('button')].find(node => node.textContent === '编辑火花资料').click(); await tick()
    assert.equal(field('防御力').value, '1800', 'a card edit updates an already-mounted editor, not a second copy of draft state')
    assert.equal(window.document.getElementById('build-character').value, '火花')
    assert.equal(calls.length, 1)
    assert.deepEqual(errors, [])
  } finally { await window.happyDOM.close() }
})
