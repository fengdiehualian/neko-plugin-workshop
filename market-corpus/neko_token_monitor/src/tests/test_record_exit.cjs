const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/widget.js'),'utf8');
const code=source.slice(source.indexOf('function pendingClicks(){'),source.indexOf('let nativeTimer=null;'));
function page(storage,server,mode='online'){
 const calls=[],native=[],timers=[],elements=new Map(),context=vm.createContext({
  browserPreview:false,recordingActive:server.active,recordingState:{...server},recordQueue:Promise.resolve(),exiting:false,csrf:'test-csrf',timer:null,
  crypto:require('node:crypto'),AbortController,Event:class{constructor(type){this.type=type;}},
  localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
  el:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
  window:{dispatchEvent(){}},document:{body:{classList:{add(){}}}},closeContextMenus(){},say(){},
  native:action=>native.push(action),setTimeout:(callback,delay)=>{if(delay===3200)timers.push(callback);return 1;},clearTimeout(){},
  renderRecording:value=>{context.recordingState=value;context.recordingActive=value.active;},
  fetch:async(_url,options)=>{
   const body=JSON.parse(options.body);calls.push(body);
   if(mode==='offline'||mode==='click-fails'&&body.action==='pet')throw Error('fixture transport offline');
   let ended=null;
   if(body.action==='end'){ended=body.session_id;if(ended===server.id)server.active=false;}
   if(body.action==='pet'&&!server.seen.has(body.event_id)){server.seen.add(body.event_id);server.pet++;}
   if(body.action==='start'){server.id='new-session';server.active=true;}
   return {ok:true,json:async()=>({...server,summary:server.active?null:`摸头 ${server.pet} 次喵～`,ended_session_id:ended})};
  },
 });
 vm.runInContext(code,context);return {context,calls,native,close:()=>timers.forEach(callback=>callback())};
}
(async()=>{
 for(let round=0;round<5;round++)for(const mode of ['click-fails','offline']){
  const storage=new Map(),server={id:'old-session',active:true,pet:0,seen:new Set()};
  storage.set('catfood-pending-clicks',JSON.stringify([{action:'pet',event_id:'pet-1',session_id:server.id}]));
  const first=page(storage,server,mode);await vm.runInContext('finishRecording()',first.context);first.close();
  assert.equal(first.calls[0].action,'end','close must precede an unsent click');
  assert.ok(first.native.includes('recordEnd')&&first.native.includes('hide'));
  assert.equal(JSON.parse(storage.get('catfood-pending-clicks')).length,1);
  assert.equal(JSON.parse(storage.get('catfood-pending-ends')).length,mode==='offline'?1:0);
  const reopened=page(storage,server);
  const state=await vm.runInContext("recordAction('status')",reopened.context);
  assert.equal(state.active,false,'reopen reconciles old end before considering takeover');
  assert.equal(server.pet,1);assert.equal(JSON.parse(storage.get('catfood-pending-ends')).length,0);
  assert.equal(reopened.calls.filter(body=>body.action==='start').length,0);
  await vm.runInContext("recordAction('status')",reopened.context);assert.equal(server.pet,1,'no double replay');
  await vm.runInContext("recordAction('start')",reopened.context);assert.equal(server.active,true);
  await vm.runInContext("recordAction('end',{session_id:'old-session'})",reopened.context);
  assert.equal(server.active,true,'replaying an old end cannot close the new session');
 }
 console.log('PASS: 10 offline/click-failure exits, durable reload recovery, original-session replay, no duplicate counts and no stale end closing a new session.');
})().catch(error=>{console.error(error);process.exitCode=1;});
