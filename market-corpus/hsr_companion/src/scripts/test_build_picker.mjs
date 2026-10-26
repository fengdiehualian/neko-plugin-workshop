// One input, real hosted event handling, ID-only selection. No network.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import vm from 'node:vm'
import { test } from 'node:test'

const ts = createRequire(resolve('package.json'))('typescript')
const source = readFileSync(new URL('../ui/build_picker.tsx', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, jsxFactory: 'h' } }).outputText
function fixture() {
  const local = new Map(), calls = []
  const kit = { useLocalState(key, initial) {
    if (!local.has(key)) local.set(key, initial)
    return [local.get(key), value => local.set(key, value)]
  } }
  const context = vm.createContext({ exports: {}, require: () => kit,
    h: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }) })
  vm.runInContext(js, context)
  const props = { id: 'test-picker', label: '光锥', value: 'b', placeholder: '搜索', emptyLabel: '未填写',
    items: [{ id: 'a', name: '献给明日的色彩', note: '真珠专属光锥' }, { id: 'b', name: '锋镝' }],
    t: key => key, onChange: value => { calls.push(value); props.value = value } }
  const render = () => context.exports.BuildPicker(props)
  const nodes = tree => [tree, ...(tree?.children || []).filter(x => x && typeof x === 'object').flatMap(nodes)]
  const input = () => nodes(render()).find(x => x.type === 'input')
  const options = () => nodes(render()).filter(x => x.props.role === 'option')
  const key = (name, extra = {}) => input().props.onKeyDown({ key: name, preventDefault() {}, ...extra })
  const type = value => input().props.onInput({ target: { value } })
  return { render, nodes, input, options, calls, props, key, type }
}

test('one textbox searches names and signature notes; typing is not saving an ID', () => {
  const f = fixture()
  assert.equal(f.nodes(f.render()).filter(x => x.type === 'input').length, 1)
  f.input().props.onFocus()
  f.type('真珠')
  assert.equal(f.options().length, 1)
  assert.deepEqual(f.calls, [])
  f.key('Enter')
  assert.deepEqual(f.calls, ['a'])
  assert.equal(f.input().props.value, '献给明日的色彩')
})

test('unknown input and Escape/blur preserve the selected equipment', () => {
  const f = fixture()
  f.type('没有这个光锥')
  assert.equal(f.options().length, 0)
  f.key('Enter')
  assert.deepEqual(f.calls, [])
  f.key('Escape')
  assert.equal(f.input().props.value, '锋镝')
  f.type('真珠')
  f.input().props.onBlur({ currentTarget: { parentElement: { contains: () => false } }, relatedTarget: null })
  assert.equal(f.input().props.value, '锋镝')
  assert.deepEqual(f.calls, [])
})

test('keyboard can navigate, but IME Enter never commits a choice', () => {
  const f = fixture()
  f.key('ArrowDown')
  f.key('ArrowDown')
  f.key('Enter', { isComposing: true })
  assert.deepEqual(f.calls, [])
  f.key('Enter')
  assert.deepEqual(f.calls, ['a'])
})

test('busy controls reject input selection without changing stored ID', () => {
  const f = fixture()
  f.props.disabled = true
  f.key('ArrowDown')
  assert.equal(f.input().props.disabled, true)
  assert.deepEqual(f.calls, [])
})
