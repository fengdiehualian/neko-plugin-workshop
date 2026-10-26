import { createBridge, defaultDirectory } from './bridge.js';

export const inject = ['deepseekAccount'];
export function apply(ctx) {
  const bridge = createBridge({account:ctx.deepseekAccount, directory:defaultDirectory(),port:48929,version:'0.1.0'});
  ctx.on('webserver/index-inject', table => {
    table.push({kind:'global',name:'__CATFOOD_HARNESS_BRIDGE__',value:bridge.clientConfig()});
  });
  ctx.effect(() => {
    void bridge.start().catch(() => console.error('[catfood] bridge could not start'));
    return () => { void bridge.close(); };
  });
}
