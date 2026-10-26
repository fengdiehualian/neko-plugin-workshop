// Run from a workspace with TypeScript installed (e.g. frontend/plugin-manager).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import vm from 'node:vm'
import { test } from 'node:test'

const ts = createRequire(resolve('package.json'))('typescript')
const source = readFileSync(new URL('../ui/panel_v09.tsx', import.meta.url), 'utf8')
const parsed = ts.createSourceFile('panel.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
assert.equal(parsed.parseDiagnostics.length, 0, 'panel TSX must parse before behavioral tests')
const names = ['friendlyError', 'runPanelAction', 'copyDiagnosticText']
const functions = parsed.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
assert.equal(functions.length, names.length)
const isolated = functions.map(node => node.getText(parsed)).join('\n')
const js = ts.transpileModule(isolated, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText
const sandbox = vm.createContext({})
vm.runInContext(js, sandbox)

test('missing button registration explains the operation, not a missing game', () => {
  const message = sandbox.friendlyError({ code: 'PLUGIN_UI_ACTION_NOT_FOUND', message: "UI action 'restart_background_perception' is not a plugin entry" }, '重新检查连接')
  assert.match(message, /重新检查连接/)
  assert.match(message, /未登记/)
  assert.doesNotMatch(message, /没有找到游戏|操作没有完成，请查看面板/)
})

test('unknown 404 is not misrepresented as a missing game window', () => {
  assert.match(sandbox.friendlyError({ message: 'resource not found', status: 404 }, '下载资料'), /resource not found/)
})

test('cross-realm Error retains the actual backend explanation', () => {
  assert.match(sandbox.friendlyError(new Error('测试错误原因'), '下载资料'), /测试错误原因/)
})

test('action failure is not reported as success', async () => {
  let refreshed = false
  await assert.rejects(sandbox.runPanelAction(async () => { throw new Error('action rejected') }, async () => { refreshed = true }, '已完成'), /action rejected/)
  assert.equal(refreshed, false)
})

test('success followed by refresh failure does not request duplicate operation', async () => {
  const result = { result: { last_operation: { message: '助手已安装，无需重复下载。' } } }
  const feedback = await sandbox.runPanelAction(async () => result, async () => { throw new Error('refresh unavailable') }, '下载完成')
  assert.equal(feedback.tone, 'info')
  assert.equal(feedback.result, result)
  assert.match(feedback.message, /无需重复下载/)
  assert.match(feedback.message, /无需重复刚才的操作/)
})

test('successful action uses the backend result envelope', async () => {
  const feedback = await sandbox.runPanelAction(async () => ({ result: { last_operation: { message: '连接已经恢复。' } } }), async () => ({}), '默认消息')
  assert.equal(feedback.tone, 'success')
  assert.equal(feedback.message, '连接已经恢复。')
})

const copy = JSON.parse(readFileSync(new URL('../i18n/zh-CN.json', import.meta.url), 'utf8'))
const compiled = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS,
  jsx: ts.JsxEmit.React, jsxFactory: 'h', jsxFragmentFactory: 'Fragment',
} }).outputText

function panelFixture(state = {}) {
  const local = new Map()
  const calls = []
  const copied = []
  const notices = []
  let refreshes = 0
  let copySucceeds = true
  let exportSucceeds = true
  let refreshSucceeds = true
  let uidReply = null
  const callArguments = []
  let report = { checked_at: '2026-10-11T12:00:00+00:00', checks: [], copy_text: 'SAFE_REPORT' }
  const names = ['Page', 'Card', 'Stack', 'Text', 'Tip', 'Alert', 'StatusBadge', 'Button', 'ButtonGroup', 'Accordion', 'Textarea', 'Input', 'Switch', 'Field', 'RadioGroup', 'ConfirmDialog', 'FileDownload', 'SavedPlayerBuilds']
  const kit = Object.fromEntries(names.map(name => [name, name]))
  kit.useEffect = () => {} // The polling timer is not part of a button invocation.
  kit.usePlayerBuildDraft = () => ({})
  kit.useToast = () => ({ success: message => notices.push(message), error: message => notices.push(message) })
  kit.useClipboard = () => ({ write: async text => { copied.push(text); return copySucceeds } })
  const context = vm.createContext({
    exports: {}, require: () => kit,
    navigator: { clipboard: { writeText: async text => {
      copied.push(text)
      if (!copySucceeds) throw new Error('Clipboard API blocked by permissions policy')
    } } },
    h: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
  })
  vm.runInContext(compiled, context)
  const props = {
    state, actions: ['diagnose_companion', 'export_companion_diagnostic', 'set_proactive_companionship', 'start_auto_observation', 'stop_auto_observation',
      'preview_uid_import', 'confirm_uid_import', 'discard_uid_import', 'clear_uid_import'].map(id => ({ id })),
    t: (key, params = {}) => Object.entries(params).reduce((text, [name, value]) => text.replace('{' + name + '}', value), copy[key] || key),
    api: {
      call: async (id, args) => {
        calls.push(id)
        callArguments.push({ id, args })
        if (id.endsWith('_uid_import')) return { result: { uid_import: uidReply } }
        if (id === 'export_companion_diagnostic') {
          if (!exportSucceeds) throw new Error('诊断导出失败：磁盘空间不足')
          return { result: { diagnostic: report, export: {
            path: 'C:\\plugin-data\\diagnostics\\report.json', filename: 'report.json', checked_at: report.checked_at,
          } } }
        }
        return { result: { diagnostic: report } }
      },
      refresh: async () => {
        refreshes += 1
        if (!refreshSucceeds) throw new Error('refresh unavailable')
        if (uidReply) props.state.uid_import = uidReply
      },
    },
    useLocalState: (key, initial) => {
      if (!local.has(key)) local.set(key, initial)
      return [local.get(key), value => local.set(key, value)]
    },
  }
  const render = () => context.exports.default(props)
  const nodes = tree => [tree, ...(tree?.children || []).filter(child => child && typeof child === 'object').flatMap(nodes)]
  const text = tree => (typeof tree === 'string' ? tree : (tree?.children || []).map(text).join(''))
  const button = (tree, label) => nodes(tree).find(node => node.type === 'Button' && text(node) === label)
  return { render, nodes, text, button, calls, copied, notices, callArguments,
    refreshes: () => refreshes, setReport: value => { report = value }, failCopy: () => { copySucceeds = false },
    failExport: () => { exportSucceeds = false }, setUidReply: value => { uidReply = value },
    failRefresh: () => { refreshSucceeds = false } }
}

test('pending current state keeps history visibly separate and never marks it ready', () => {
  const fixture = panelFixture({
    observer: { enabled: true, state: 'observing' },
    vision_assistant: { connected: true, game: { running: true } },
    observation_display: {
      current: { fresh: false, scene: { verified: false }, screen_facts: { facts: [] } },
      last_successful: { fresh: false, observed_at: '2026-10-15T08:00:00+00:00',
        scene: { verified: true, label: '无名勋礼' },
        screen_facts: { facts: [{ label: '星琼', value: '1600' }] } },
    },
  })
  const tree = fixture.render()
  assert.match(fixture.text(tree), /上次读取结果|读取时间/)
  assert.match(fixture.text(tree), /不代表现在的画面/)
  assert.match(fixture.text(tree), /星琼：1600/)
  const status = fixture.nodes(tree).find(node => node.type === 'StatusBadge')
  assert.notEqual(status.props.tone, 'success')
})

test('export works without a prior diagnosis or clipboard and shows the actual saved path', async () => {
  const fixture = panelFixture()
  fixture.failCopy()
  await fixture.button(fixture.render(), '导出诊断').props.onClick()
  assert.deepEqual(fixture.calls, ['export_companion_diagnostic'])
  assert.deepEqual(fixture.copied, [])
  assert.equal(fixture.refreshes(), 0)
  const tree = fixture.render()
  assert.match(fixture.text(tree), /诊断文件已保存/)
  assert.match(fixture.text(tree), /C:\\plugin-data\\diagnostics\\report.json/)
  const opener = fixture.nodes(tree).find(node => node.type === 'FileDownload')
  assert.equal(opener.props.path, 'C:\\plugin-data\\diagnostics\\report.json')
  assert.equal(opener.props.label, '打开导出文件')
})

test('failed export preserves diagnosis but clears the old saved-file success indicator', async () => {
  const fixture = panelFixture()
  await fixture.button(fixture.render(), '导出诊断').props.onClick()
  fixture.failExport()
  await fixture.button(fixture.render(), '导出诊断').props.onClick()
  const tree = fixture.render()
  assert.match(fixture.text(tree), /诊断导出失败/)
  assert.doesNotMatch(fixture.text(tree), /诊断文件已保存/)
  assert.equal(fixture.nodes(tree).some(node => node.type === 'FileDownload'), false)
  assert.ok(fixture.nodes(tree).some(node => node.type === 'Textarea' && node.props.value === 'SAFE_REPORT'))
  assert.equal(fixture.notices.filter(message => message === '诊断文件已保存').length, 1)
  assert.equal(fixture.refreshes(), 0)
})

test('diagnose and copy are separate; diagnosis does not refresh or speak', async () => {
  const fixture = panelFixture()
  assert.equal(fixture.button(fixture.render(), '复制诊断结果'), undefined)
  await fixture.button(fixture.render(), '一键诊断').props.onClick()
  assert.deepEqual(fixture.calls, ['diagnose_companion'])
  assert.deepEqual(fixture.copied, [])
  assert.equal(fixture.refreshes(), 0)
  await fixture.button(fixture.render(), '复制诊断结果').props.onClick()
  assert.deepEqual(fixture.calls, ['diagnose_companion'])
  assert.deepEqual(fixture.copied, ['SAFE_REPORT'])
})

test('clipboard failure preserves a visible manual-copy report', async () => {
  const fixture = panelFixture()
  await fixture.button(fixture.render(), '一键诊断').props.onClick()
  fixture.failCopy()
  await fixture.button(fixture.render(), '复制诊断结果').props.onClick()
  assert.match(fixture.notices.at(-1), /手动选择复制/)
  assert.ok(fixture.nodes(fixture.render()).some(node => node.type === 'Textarea' && node.props.value === 'SAFE_REPORT'))
})

test('clipboard API rejection tries legacy copy and cleans up without changing permissions', async () => {
  let removed = false
  let focused = false
  let selected = false
  let copied = ''
  const field = { style: {}, focus: () => {}, select: () => { selected = true }, remove: () => { removed = true } }
  sandbox.navigator = { clipboard: { writeText: async () => { throw new Error('blocked by policy') } } }
  sandbox.document = { activeElement: { focus: () => { focused = true } }, createElement: () => field,
    body: { appendChild: () => {} }, execCommand: command => { copied = field.value; return command === 'copy' } }
  assert.equal(await sandbox.copyDiagnosticText('SAFE_REPORT'), true)
  assert.equal(copied, 'SAFE_REPORT')
  assert.ok(removed && focused && selected)
})

test('both clipboard paths blocked returns failure and leaves no temporary field', async () => {
  let removed = false
  sandbox.document = { activeElement: null, createElement: () => ({ style: {}, focus: () => {}, select: () => {}, remove: () => { removed = true } }),
    body: { appendChild: () => {} }, execCommand: () => { throw new Error('blocked') } }
  assert.equal(await sandbox.copyDiagnosticText('SAFE_REPORT'), false)
  assert.equal(removed, true)
})

test('failed repeat diagnosis keeps the previous timestamped snapshot', async () => {
  const fixture = panelFixture()
  await fixture.button(fixture.render(), '一键诊断').props.onClick()
  fixture.setReport({})
  await fixture.button(fixture.render(), '一键诊断').props.onClick()
  assert.match(fixture.text(fixture.render()), /诊断未返回有效结果/)
  assert.match(fixture.text(fixture.render()), /结果是当时的快照/)
  assert.ok(fixture.nodes(fixture.render()).some(node => node.type === 'Textarea' && node.props.value === 'SAFE_REPORT'))
})

test('daily panel has no experimental screenshot, roster-import or speech-test buttons', () => {
  const fixture = panelFixture({ observer: { enabled: true }, proactive_companionship: { enabled: true } })
  const tree = fixture.render()
  const buttons = fixture.nodes(tree).filter(node => node.type === 'Button').map(fixture.text)
  assert.ok(buttons.includes('关闭主动搭话'))
  assert.ok(buttons.includes('暂停陪玩'))
  assert.ok(!buttons.some(label => /测试搭话|识别这张画面|识别角色列表|保存角色档案/.test(label)))
  const proactive = fixture.nodes(tree).find(node => node.type === 'Card' && node.props.title === '主动搭话')
  assert.ok(fixture.button(proactive, '关闭主动搭话'))
  const help = fixture.nodes(tree).find(node => node.type === 'Accordion' && node.props.title === '使用说明')
  assert.match(fixture.text(help), /无需开启语音或视频通话/)
})

test('paused panel cannot display cached current roles, location or controlled character', () => {
  const fixture = panelFixture({ observer: { enabled: false }, current_visual_evidence: {
    fresh: true, characters: [{ name: 'OLD_ROLE' }], controlled_character: { name: 'OLD_CONTROLLED' },
    screen_facts: { facts: [{ kind: 'current_location', label: '位置', value: 'OLD_LOCATION' }] },
  } })
  assert.doesNotMatch(fixture.text(fixture.render()), /OLD_ROLE|OLD_CONTROLLED|OLD_LOCATION/)
})

function consentToUid(fixture) {
  fixture.nodes(fixture.render()).find(n => n.type === 'Input').props.onChange('100000001')
  fixture.nodes(fixture.render()).find(n => n.type === 'Switch').props.onChange(true)
}

const uidPreview = {
  status: 'preview', uid: '100000001',
  preview: { preview_id: 'preview-1', fetched_at: '2026-10-18T13:00:00Z', can_confirm: true,
    characters: [{ form_id: 'character_form.1503', name: '真珠', level: 80, eidolon: 0,
      light_cone: { name: '献给明日的色彩', level: 80, superimposition: 1 },
      relics: [{ slot: '头部', set_name: '应天涉远的卜者', level: 15, complete: true,
        main_stat: { label: '生命值', display: '705' },
        sub_stats: [{ label: '攻击力', display: '19' }, { label: '防御力', display: '35' },
          { label: '速度', display: '6' }, { label: '暴击率', display: '5.8%' }] }] }] },
  roster: { characters: [] },
}

test('UID requires explicit consent, and rendering/polling never query the provider', () => {
  const fixture = panelFixture()
  assert.equal(fixture.button(fixture.render(), '读取展示角色').props.disabled, true)
  consentToUid(fixture)
  assert.equal(fixture.button(fixture.render(), '读取展示角色').props.disabled, false)
  for (let i = 0; i < 5; i++) fixture.render()
  assert.deepEqual(fixture.calls, [])
})

test('UID preview shows all returned substats and requires a separate save click', async () => {
  const fixture = panelFixture()
  consentToUid(fixture)
  fixture.setUidReply(structuredClone(uidPreview))
  await fixture.button(fixture.render(), '读取展示角色').props.onClick()
  const tree = fixture.render()
  assert.deepEqual(fixture.calls, ['preview_uid_import'])
  assert.equal(fixture.callArguments[0].args.consent, true)
  assert.match(fixture.text(tree), /攻击力 19/)
  assert.match(fixture.text(tree), /防御力 35/)
  assert.match(fixture.text(tree), /速度 6/)
  assert.match(fixture.text(tree), /暴击率 5.8%/)
  assert.ok(fixture.button(tree, '确认保存这批角色'))
  assert.match(fixture.text(tree), /还没有保存角色资料/)
  assert.match(fixture.text(tree), /没有直接提供最终属性面板/)
  assert.equal(fixture.calls.includes('start_auto_observation'), false)
})

test('UID preview remains usable when host refresh fails after successful read', async () => {
  const fixture = panelFixture()
  consentToUid(fixture)
  fixture.setUidReply(structuredClone(uidPreview))
  fixture.failRefresh()
  await fixture.button(fixture.render(), '读取展示角色').props.onClick()
  assert.match(fixture.text(fixture.render()), /真珠|读取到 1 个角色/)
  assert.ok(fixture.button(fixture.render(), '确认保存这批角色'))
  assert.deepEqual(fixture.calls, ['preview_uid_import'])
})

test('UID save targets the exact preview and does not imply visual recognition', async () => {
  const fixture = panelFixture({ uid_import: structuredClone(uidPreview), observer: { enabled: false } })
  fixture.setUidReply({ status: 'saved', uid: '100000001', roster: { characters: uidPreview.preview.characters } })
  await fixture.button(fixture.render(), '确认保存这批角色').props.onClick()
  assert.equal(fixture.callArguments[0].args.preview_id, 'preview-1')
  assert.ok(fixture.nodes(fixture.render()).some(n => n.type === 'StatusBadge' && n.props.label === '已保存 1 个角色配装'))
  const observation = fixture.nodes(fixture.render()).find(n => n.type === 'Card' && n.props.title === '陪玩状态')
  assert.doesNotMatch(fixture.text(observation), /真珠/)
})

test('UID cache cooldown disables repeat requests and network failures are explained', () => {
  const fixture = panelFixture({ uid_import: { status: 'error', error_code: 'network_unavailable',
    next_query_at: new Date(Date.now() + 60000).toISOString(), roster: { characters: [] } } })
  consentToUid(fixture)
  const tree = fixture.render()
  assert.equal(fixture.button(tree, '读取展示角色').props.disabled, true)
  assert.match(fixture.text(tree), /上游有缓存/)
  assert.match(fixture.text(tree), /无法连接 Enka.Network/)
  assert.deepEqual(fixture.calls, [])
})

test('retained preview displays its own UID, not a later failed query account', () => {
  const pending = structuredClone(uidPreview)
  pending.uid = '200000001'
  pending.preview.uid = '100000001'
  pending.status = 'error'
  pending.error_code = 'network_unavailable'
  const fixture = panelFixture({ uid_import: pending })
  assert.match(fixture.text(fixture.render()), /这批展示来自 UID：100000001/)
  assert.doesNotMatch(fixture.text(fixture.render()), /这批展示来自 UID：200000001/)
})

test('existing roles and maintenance are collapsed, but connection status remains visible', () => {
  const fixture = panelFixture({ uid_import: { status: 'saved', uid: '100000001',
    roster: { characters: uidPreview.preview.characters } } })
  const tree = fixture.render()
  for (const id of ['uid-query-settings-v2', 'maintenance-v2', 'correction-v2', 'help-v2', 'data-management-v2']) {
    const accordion = fixture.nodes(tree).find(n => n.type === 'Accordion' && n.props.id === id)
    assert.equal(accordion.props.open, false, id)
  }
  assert.equal(fixture.nodes(tree).find(n => n.type === 'SavedPlayerBuilds').props.rows.length, 1)
  assert.ok(fixture.nodes(tree).some(n => n.type === 'StatusBadge' && n.props.label === '已保存 1 个角色配装'))
  assert.equal(fixture.button(tree, '刷新展示配装').props.disabled, false)
})

test('delta preview highlights the new role and folds unchanged role details', () => {
  const pending = structuredClone(uidPreview)
  pending.preview.characters[0].change = 'unchanged'
  pending.preview.characters.push({ ...structuredClone(pending.preview.characters[0]),
    form_id: 'character_form.1501', name: '火花', change: 'added' })
  pending.preview.summary = { added: 1, updated: 0, unchanged: 1, retained: 1, total_after: 3 }
  const fixture = panelFixture({ uid_import: pending })
  const tree = fixture.render()
  assert.match(fixture.text(tree), /本次新增 1 位 · 配装更新 0 位 · 未变化 1 位/)
  assert.match(fixture.text(tree), /确认后共记录 3 位/)
  assert.equal(fixture.nodes(tree).find(n => n.props.id === 'uid-unchanged-v2').props.open, false)
  assert.ok(fixture.nodes(tree).some(n => n.type === 'Accordion' && /新增 · 火花/.test(n.props.title)))
  assert.deepEqual(fixture.calls, [])
})

test('proxy requires a deliberate selection and only accompanies the Enka read action', async () => {
  const fixture = panelFixture()
  consentToUid(fixture)
  fixture.setUidReply(structuredClone(uidPreview))
  assert.equal(fixture.nodes(fixture.render()).find(n => n.type === 'Switch' && n.props.label === copy['uid.systemProxy']).props.checked, false)
  fixture.nodes(fixture.render()).find(n => n.type === 'Switch' && n.props.label === copy['uid.systemProxy']).props.onChange(true)
  await fixture.button(fixture.render(), '读取展示角色').props.onClick()
  assert.equal(fixture.callArguments[0].args.use_system_proxy, true)
  assert.equal(fixture.calls.includes('restart_background_perception'), false)
})

test('server errors show safe HTTP status and do not duplicate the UID error alert', () => {
  const fixture = panelFixture({ uid_import: { status: 'error', error_code: 'provider_unavailable',
    error_info: { http_status: 500, network_mode: 'inherit' }, roster: { characters: [] } } })
  const tree = fixture.render()
  assert.match(fixture.text(tree), /HTTP 500/)
  assert.equal(fixture.nodes(tree).filter(n => n.type === 'Alert' && /Enka 本次返回服务器错误/.test(fixture.text(n))).length, 1)
})
