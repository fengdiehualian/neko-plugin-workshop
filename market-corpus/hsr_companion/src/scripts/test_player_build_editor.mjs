// Source UI behavior only. No network, real account, host mutation or install.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import vm from 'node:vm'
import { test } from 'node:test'

const ts = createRequire(resolve('package.json'))('typescript')
const copy = JSON.parse(readFileSync(new URL('../i18n/zh-CN.json', import.meta.url), 'utf8'))
const source = readFileSync(new URL('../ui/player_build_editor.tsx', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020,
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, jsxFactory: 'h' } }).outputText
const options = {
  characters: [{ id: 'character_form.1503', name: '真珠' }, { id: 'character_form.1501', name: '火花' }, { id: 'character_form.1306', name: '花火' }],
  light_cones: [{ id: 'light_cone.23055', name: '献给明日的色彩' }, { id: 'light_cone.20000', name: '锋镝' }],
  outer_sets: [{ id: 'relic_set.133', name: '戏梦点星的伶人' }, { id: 'relic_set.132', name: '应天涉远的卜者' }],
  inner_sets: [{ id: 'relic_set.301', name: '太空封印站' }],
}
const uidRow = { form_id: 'character_form.1503', name: '真珠', level: 80, eidolon: 0, edit_revision: 'revision-1',
  light_cone: { id: 'light_cone.23055', name: '献给明日的色彩', level: 80, superimposition: 1 },
  sets: { outer: [{ id: 'relic_set.133', name: '戏梦点星的伶人', pieces: 4 }], inner: { id: 'relic_set.301', name: '太空封印站' } } }

function fixture(rows = [structuredClone(uidRow)]) {
  const local = new Map(), calls = []
  let answer = { status: 'saved' }, failRefresh = false, busy = ''
  const kit = Object.fromEntries(['Stack','Text','Tip','Alert','Button','Field','Input','Select','Grid','Accordion'].map(name => [name, name]))
  kit.useConfirm = () => async () => true
  kit.useEffect = () => {}
  const pickerSource = readFileSync(new URL('../ui/build_picker.tsx', import.meta.url), 'utf8')
  const pickerContext = vm.createContext({ exports: {}, require: () => kit })
  vm.runInContext(ts.transpileModule(pickerSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, jsxFactory: 'h' } }).outputText, pickerContext)
  const context = vm.createContext({ exports: {}, require: id => id === './build_picker' ? pickerContext.exports : kit,
    h: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }) })
  vm.runInContext(js, context)
  const surface = {
    state: { player_builds: { characters: rows }, build_options: options },
    t: (key, params = {}) => Object.entries(params).reduce((text, [key, value]) => text.replace('{' + key + '}', value), copy[key] || key),
    useLocalState: (key, initial) => {
      if (!local.has(key)) local.set(key, initial)
      return [local.get(key), value => local.set(key, typeof value === 'function' ? value(local.get(key)) : value)]
    },
    api: { call: async (id, args) => { calls.push({ id, args }); return { result: { player_build_save: answer.status !== 'saved' ? answer : {
      ...answer, readback_state: 'confirmed', build: { ...(rows.find(r => r.form_id === args.form_id) || {}),
        form_id: args.form_id, name: '真珠', edit_revision: 'revision-saved', panel: { values: args.changes.panel || {} },
        manual_fields: ['panel'], manual_updated_at: '2026-10-19T03:00:00Z' },
    } } } },
      refresh: async () => { if (failRefresh) throw new Error('synthetic refresh failure') } },
  }
  const render = () => context.exports.PlayerBuildEditor({ surface, busy, onBusy: value => { busy = value }, editor: context.exports.usePlayerBuildDraft(surface, busy) })
  const nodes = tree => [tree, ...(tree?.children || []).filter(child => child && typeof child === 'object').flatMap(nodes)]
  const text = tree => typeof tree === 'string' ? tree : (tree?.children || []).map(text).join('')
  const field = label => {
    const row = nodes(render()).find(node => (node.type === 'Field' || node.type?.name === 'BuildPicker') && node.props.label === copy[label])
    assert.ok(row, 'field exists: ' + label)
    return row.type?.name === 'BuildPicker' ? row : nodes(row).find(node => ['Input','Select'].includes(node.type))
  }
  const button = label => nodes(render()).find(node => node.type === 'Button' && text(node) === copy[label])
  const select = async id => field('build.character').props.onChange(id)
  return { render, nodes, text, field, button, select, calls, surface,
    reject: code => { answer = { status: 'error', error_code: code } },
    failRefresh: () => { failRefresh = true } }
}

test('UID prefill needs no online call, and forms preserve identity', async () => {
  const f = fixture()
  await f.select('character_form.1503')
  assert.equal(f.field('build.level').props.value, '80')
  assert.equal(f.field('build.cone').props.value, 'light_cone.23055')
  assert.equal(f.field('build.outerA').props.value, 'relic_set.133')
  assert.equal(f.button('build.save').props.disabled, true)
  assert.deepEqual(f.calls, [])
})

test('role/cone/set choices use one search-select control, not duplicated inputs', async () => {
  const f = fixture()
  assert.equal(f.nodes(f.render()).some(node => node.props?.label === copy['build.roleSearch']), false)
  assert.deepEqual(Array.from(f.field('build.character').props.items, r => r.id), ['character_form.1503', 'character_form.1501', 'character_form.1306'])
  await f.select('character_form.1503')
  assert.equal(f.nodes(f.render()).some(node => node.props?.label === copy['build.coneSearch']), false)
  assert.equal(f.field('build.cone').props.value, 'light_cone.23055')
})

test('only edited panel is submitted, preserving UID cone, levels and full relics', async () => {
  const f = fixture()
  await f.select('character_form.1503')
  f.field('build.stat.def').props.onChange('3132')
  await f.button('build.save').props.onClick()
  const args = JSON.parse(JSON.stringify(f.calls[0].args))
  assert.deepEqual(args, { form_id: 'character_form.1503', changes: { panel: { def: '3132' } }, expected_revision: 'revision-1' })
  assert.equal(f.calls[0].id, 'save_manual_build')
  assert.match(f.text(f.render()), /已保存/)
  assert.equal(f.field('build.stat.def').props.value, '3132', 'a confirmed save keeps the actual saved values visible')
})

test('new role can be saved offline without UID, without assuming unknown level/eidolon', async () => {
  const f = fixture([])
  await f.select('character_form.1501')
  assert.equal(f.field('build.level').props.value, '')
  assert.equal(f.field('build.eidolon').props.value, '')
  f.field('build.stat.spd').props.onChange('143')
  await f.button('build.save').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].args.changes)), { panel: { spd: '143' } })
})

test('a refreshed UID cannot silently clobber a dirty draft', async () => {
  const f = fixture()
  await f.select('character_form.1503')
  f.field('build.stat.def').props.onChange('3132')
  f.surface.state.player_builds.characters[0].edit_revision = 'revision-2'
  assert.equal(f.field('build.stat.def').props.value, '3132')
  assert.equal(f.button('build.save').props.disabled, true)
  assert.match(f.text(f.render()), /你的输入仍保留/)
  assert.deepEqual(f.calls, [])
})

test('validation failure keeps the entered values and explains the field', async () => {
  const f = fixture()
  await f.select('character_form.1503')
  f.field('build.stat.crit_rate').props.onChange('8.2%')
  f.reject('invalid_panel')
  await f.button('build.save').props.onClick()
  assert.equal(f.field('build.stat.crit_rate').props.value, '8.2%')
  assert.match(f.text(f.render()), /不带百分号/)
})

test('successful save followed by refresh failure is not reported as save failure', async () => {
  const f = fixture()
  await f.select('character_form.1503')
  f.field('build.stat.def').props.onChange('3132')
  f.failRefresh()
  await f.button('build.save').props.onClick()
  assert.match(f.text(f.render()), /已经保存.*无需重复保存/)
  assert.equal(f.calls.length, 1)
})

test('two-plus-two set selection sends set counts, not invented item stats', async () => {
  const f = fixture()
  await f.select('character_form.1503')
  f.field('build.outerMode').props.onChange('two_two')
  f.field('build.outerB').props.onChange('relic_set.132')
  await f.button('build.save').props.onClick()
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].args.changes)), { sets: {
    outer: [{ id: 'relic_set.133', pieces: 2 }, { id: 'relic_set.132', pieces: 2 }], inner: 'relic_set.301' } })
})
