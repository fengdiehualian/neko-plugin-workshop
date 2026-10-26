const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/widget.js'),'utf8');
const code=source.slice(source.indexOf('let launcherToggleBusy=false;'),source.indexOf("el('historyToggle').onclick"));
function page(active=false){
 const calls=[];let resolve;
 const pending=new Promise(done=>resolve=done);
 const context=vm.createContext({browserPreview:false,exiting:false,recordingActive:active,csrf:'fixture',
  window:{},native:action=>calls.push(action),
  startRecording:()=>{calls.push('start');return pending;},
  finishRecording:()=>{calls.push('end');return pending;}});
 vm.runInContext(code,context);return {context,calls,resolve,toggle:context.window.catfoodToggleMonitoring};
}
(async()=>{
 for(const active of [false,true]){
  const p=page(active),first=p.toggle();await p.toggle();
  assert.deepEqual(p.calls,[active?'end':'start'],'a repeated click cannot start another transition');
  p.resolve();await first;assert.equal(p.calls.at(-1),'toggleSettled');
  p.context.recordingActive=!active;await p.toggle();
  assert.deepEqual(p.calls,[active?'end':'start','toggleSettled',active?'start':'end','toggleSettled']);
 }
 const p=page();p.context.csrf='';await assert.rejects(p.toggle(),/正在连接猫粮/);
 assert.deepEqual(p.calls,['toggleSettled'],'connection failures unlock the native button');
 p.context.csrf='fixture';const retry=p.toggle();p.resolve();await retry;assert.ok(p.calls.includes('start'));
 for(const flag of ['browserPreview','exiting']){
  const q=page();q.context[flag]=true;await q.toggle();assert.equal(q.calls.length,0);
 }
 console.log('PASS: monitor button start/exit, duplicate-click guard, failed connection recovery and preview/exit guards.');
})().catch(error=>{console.error(error);process.exitCode=1;});
