export const APP_VERSION = '0.7.1';
export function pluginCompatibility(version) {
  const actual = typeof version === 'string' && /^\d+\.\d+\.\d+$/.test(version) ? version : null;
  return {ok: actual === APP_VERSION, expected: APP_VERSION, actual,
    code: actual === APP_VERSION ? 'MATCHED' : actual ? 'PLUGIN_VERSION_MISMATCH' : 'PLUGIN_VERSION_UNKNOWN'};
}
