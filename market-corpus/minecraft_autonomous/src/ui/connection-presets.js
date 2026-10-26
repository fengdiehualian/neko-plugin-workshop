// Optional connection data only. Selecting a preset never saves or connects.
export function applyConnectionPreset(draft, preset) {
  if (preset !== 'qiandeng') throw new Error('Unknown connection preset');
  return {...draft, server: {...draft.server, connection_type: 'custom', host: 'micro.kangqiang.site',
    port: 25702, version: '1.21.1'}};
}
