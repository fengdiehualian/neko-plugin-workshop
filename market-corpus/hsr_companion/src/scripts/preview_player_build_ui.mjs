// Disposable source preview using the real Hosted linker/UI kit. No host API,
// Enka request, real account, installation, or persistent player write.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'

const root = resolve(import.meta.dirname, '..'), repo = resolve(root, '../../..')
const require = createRequire(resolve(repo, 'frontend/plugin-manager/package.json'))
const { transform } = require('sucrase')
const { bundleHostedTsxSource } = await import(pathToFileURL(resolve(repo, 'frontend/plugin-manager/src/components/plugin/hosted/hostedTsxModule.mjs')))
const paths = ['ui/panel_v09.tsx', 'ui/player_build_editor.tsx', 'ui/build_picker.tsx']
const files = paths.map(path => ({ path, source: readFileSync(resolve(root, path), 'utf8') }))
const compiled = transform(bundleHostedTsxSource(files[0].source, files.slice(1), paths[0]), {
  transforms: ['typescript', 'jsx'], jsxPragma: 'h', jsxFragmentPragma: 'Fragment', production: true,
}).code.replace(/\bexport\s+default\s+function\s+(\w+)?\s*\(/, (_, name) => `const __Panel = function ${name || ''}(`)
const kitRoot = resolve(repo, 'frontend/plugin-manager/src/components/plugin/hosted/ui-kit')
const runtime = readFileSync(resolve(kitRoot, 'runtime.js'), 'utf8')
const style = readFileSync(resolve(kitRoot, 'styles.css'), 'utf8')
const messages = JSON.parse(readFileSync(resolve(root, 'i18n/zh-CN.json'), 'utf8'))
const state = {
  uid_import: { status: 'saved', roster: { characters: [] } },
  player_builds: { new_character_revision: 'preview-new', characters: [
    { form_id: 'character_form.1503', name: '真珠', level: 80, eidolon: 0, edit_revision: 'preview-pearl',
      fetched_at: '2026-10-19T02:09:04Z', manual_updated_at: '2026-10-19T03:00:00Z', manual_fields: ['panel'],
      light_cone: { id: 'light_cone.23055', name: '献给明日的色彩', level: 80, superimposition: 1 },
      relic_sets: ['应天涉远的卜者'], sets: { outer: [{ id: 'relic_set.130', name: '应天涉远的卜者', pieces: 4 }], inner: null },
      panel: { state: 'provided', values: { hp: '3311', atk: '1754', def: '3132', spd: '143', crit_rate: '8.2', crit_dmg: '80.4' } } },
    { form_id: 'character_form.1506', name: '银狼LV.999', level: 80, eidolon: 0, edit_revision: 'preview-wolf',
      manual_fields: ['panel', 'light_cone'], panel: { values: { def: '2206', spd: '167' } },
      light_cone: { id: 'light_cone.23057', name: '欢迎来到银河城', level: 80, superimposition: 1 } },
  ] },
  build_options: {
    characters: [{ id: 'character_form.1503', name: '真珠', label: '真珠 · 欢愉' }, { id: 'character_form.1506', name: '银狼LV.999', label: '银狼LV.999 · 欢愉' }],
    light_cones: [
      { id: 'light_cone.23055', name: '献给明日的色彩', signature_for: ['真珠'], signature_form_ids: ['character_form.1503'] },
      { id: 'light_cone.23057', name: '欢迎来到银河城' }, { id: 'light_cone.20000', name: '锋镝', path_name: '巡猎' }],
    outer_sets: [{ id: 'relic_set.130', name: '应天涉远的卜者', effects: ['速度提高6%。', '首次施放欢愉技时，获得相应的队伍增益。此处仅为界面测试说明。'] }],
    inner_sets: [{ id: 'relic_set.301', name: '太空封印站', effects: ['此处为隔离测试说明，不是攻略资料。'] }],
  },
}
const payload = { plugin: { id: 'hsr_companion', name: '星铁游戏搭子' }, state, locale: 'zh-CN',
  i18n: { locale: 'zh-CN', default_locale: 'zh-CN', messages: { 'zh-CN': messages } },
  actions: [{ id: 'save_manual_build' }] }
const escapeScript = text => text.replace(/<\/script/gi, '<\\/script')
const boot = `
const props={...window.__NEKO_PAYLOAD,...window.NekoUiKit};
props.api={refresh:async()=>null,call:async(id,args)=>{
  if(id!=='save_manual_build') throw new Error('源码预览不连接真实服务');
  const rows=props.state.player_builds.characters;
  const old=rows.find(r=>r.form_id===args.form_id);
  if(old && old.edit_revision!==args.expected_revision) return {player_build_save:{status:'error',error_code:'stale_edit'}};
  const row={...old,...args.changes,form_id:args.form_id,edit_revision:'preview-'+Date.now(),manual_fields:Object.keys(args.changes),manual_updated_at:new Date().toISOString()};
  if(args.changes.panel) row.panel={values:args.changes.panel,state:'partial'};
  const index=rows.findIndex(r=>r.form_id===args.form_id); if(index<0) rows.push(row);else rows[index]=row;
  return {player_build_save:{status:'saved',form_id:args.form_id,readback_state:'confirmed',build:row}};
}};
window.fetch=async()=>{throw new Error('此预览禁用网络请求')};
render(h(__Panel,props),document.getElementById('root'));
`
const output = resolve(root, 'dist/player-ui-preview-20261019')
mkdirSync(output, { recursive: true })
writeFileSync(resolve(output, 'index.html'), `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>玩家资料面板 · 隔离源码预览</title><style>${style}body{max-width:780px;margin:24px auto;padding:0 16px}.fixture-banner{padding:12px;background:var(--surface-strong);border:1px solid var(--border);margin-bottom:16px}</style></head><body><aside class="fixture-banner">源码预览 · 测试资料 · 尚未安装。保存仅影响此页内存，不连接猫娘或现用玩家库。</aside><main id="root"></main><script>window.__NEKO_PAYLOAD=${escapeScript(JSON.stringify(payload))};${escapeScript(runtime)};(function(){${escapeScript(compiled)}\n${escapeScript(boot)}})();</script></body></html>`)
writeFileSync(resolve(output, 'source-hashes.json'), JSON.stringify(Object.fromEntries(files.map(file => [file.path, createHash('sha256').update(file.source).digest('hex')])), null, 2))
console.log(JSON.stringify({ preview: resolve(output, 'index.html'), network: false, persistent_writes: false }))
