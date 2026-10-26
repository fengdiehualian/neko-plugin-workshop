const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(require.resolve('../static/widget.js'),'utf8');
const code=source.slice(source.indexOf('function native(action){'),source.indexOf('\nlet agentState'));
for(const host of ['windows','mac']){
 const calls=[],bridge={postMessage:x=>calls.push(x)},window=host==='windows'?{chrome:{webview:bridge}}:{webkit:{messageHandlers:{nekoWidget:bridge}}};
 const c=vm.createContext({window,recordingState:{id:'current-session'}});vm.runInContext(code,c);
 vm.runInContext("native('recordStart');native('recordEnd');native('hide');",c);
 assert.deepEqual(JSON.parse(JSON.stringify(calls)),[{action:'recordStart',session_id:'current-session'},{action:'recordEnd',session_id:'current-session'},'hide']);
}
console.log('PASS: Windows and macOS preserve session IDs across native start/end messages');
