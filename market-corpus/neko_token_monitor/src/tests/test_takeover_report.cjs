const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/widget.js'),'utf8');
const code=source.slice(source.indexOf('let nativeTimer=null;'),source.indexOf('async function startRecording(){'));
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function page(){
 const calls=[],timers=new Map(),elements=new Map();let answer;
 const context=vm.createContext({
  recordingState:{id:'first',active:true},recordingActive:true,takeoverConfirmed:false,exiting:false,csrf:'fixture',timer:null,
  AbortController,window:{},document:{body:{classList:{add(){},remove(){}}}},restore(){},say(){},
  el:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
  native:action=>calls.push({native:action}),recordAction:async(action,details)=>calls.push({action,...details}),
  setTimeout:(fn,delay)=>{const id=timers.size+1;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
  fetch:(_url,options)=>new Promise((resolve,reject)=>{
   calls.push({report:JSON.parse(options.body)});answer={resolve,reject};
   options.signal.addEventListener('abort',()=>reject(Error('fixture timeout')));
  }),
 });
 vm.runInContext(code,context);
 return {context,calls,timers,reply:value=>answer.resolve({ok:true,json:async()=>value}),fail:()=>answer.reject(Error('fixture offline'))};
}
(async()=>{
 for(const mode of ['accepted','offline','timeout']){
  const p=page();p.context.window.catfoodNativeState({active:true,ok:false,reason:'pause_unconfirmed',session_id:'first'});
  assert.ok(p.calls.some(x=>x.native==='recordEnd'),'native restoration must start immediately');
  assert.equal(p.calls.filter(x=>x.action==='end').length,0,'HTTP end must await report completion');
  assert.equal(p.calls.find(x=>x.report).report.reason,'pause_unconfirmed');
  p.context.recordingState={id:'second',active:true};
  if(mode==='accepted')p.reply({accepted:true});
  else if(mode==='offline')p.fail();
  else [...p.timers.values()].find(x=>x.delay===3000).fn();
  await settle();await settle();
  const ends=p.calls.filter(x=>x.action==='end');assert.equal(ends.length,1);
  assert.equal(ends[0].session_id,'first','late report must never close a newly started session');
 }
 const stale=page();stale.context.window.catfoodNativeState({active:true,ok:false,session_id:'old'});
 assert.equal(stale.calls.length,0,'a stale native callback must not report or restore the current session');
 console.log('PASS: immediate native restoration, acknowledgement ordering, offline and bounded timeout cleanup, session isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
