const el=id=>document.getElementById(id);let state=null,csrf='',talk=false,timer=null,quipIndex=0,bubbleToggleBusy=false;
const browserPreview=!window.webkit?.messageHandlers?.nekoWidget&&!window.chrome?.webview;
if(browserPreview)document.body.classList.add('browser-preview');
let companionPosition=null;
let launcherLayoutKey='';
function reportLauncherLayout(){
 const bridge=window.webkit?.messageHandlers?.nekoWidget||window.chrome?.webview;if(!bridge)return;
 const rect=el('thought').getBoundingClientRect();if(!rect.width||!rect.height)return;
 const bounds={x:rect.x,y:rect.y,width:rect.width,height:rect.height},key=JSON.stringify(bounds);
 if(key===launcherLayoutKey)return;launcherLayoutKey=key;bridge.postMessage({action:'launcherLayout',rect:bounds});
}
if(browserPreview){try{const saved=JSON.parse(localStorage.getItem('catfood-companion-position'));if(saved&&Number.isFinite(saved.x)&&Number.isFinite(saved.y))companionPosition={x:Math.max(0,Math.min(1,saved.x)),y:Math.max(0,Math.min(1,saved.y))};}catch{}}
function placeCompanion(x,y){
 const group=el('companion'),scale=Number(document.documentElement.style.getPropertyValue('--companion-scale'))||1;
 const halfWidth=group.offsetWidth*scale/2,halfHeight=group.offsetHeight*scale/2;
 const cx=Math.max(halfWidth+8,Math.min(window.innerWidth-halfWidth-8,x));
 const cy=Math.max(halfHeight+8,Math.min(window.innerHeight-halfHeight-8,y));
 group.style.left=cx+'px';group.style.top=cy+'px';
 return {x:cx/window.innerWidth,y:cy/window.innerHeight};
}
function fitCompanion(){const height=Math.max(450,382+el('recordBar').offsetHeight+8);el('companion').style.height=height+'px';const available=Math.max(1,window.innerHeight-(browserPreview?16:0));document.documentElement.style.setProperty('--companion-scale',String(Math.min(1,available/height)));if(browserPreview)placeCompanion((companionPosition?.x??.5)*window.innerWidth,(companionPosition?.y??.5)*window.innerHeight);reportLauncherLayout();}
fitCompanion();window.addEventListener('resize',fitCompanion);
if(window.ResizeObserver)new ResizeObserver(fitCompanion).observe(el('recordBar'));
let recordBarVisible=false;
try{recordBarVisible=localStorage.getItem('catfood-record-bar-visible')==='true';}catch{}
function setRecordBarVisible(visible,persist=true){
 recordBarVisible=visible;el('recordBar').hidden=!visible;
 const toggle=el('recordVisibilityToggle');toggle.setAttribute('aria-checked',String(visible));
 if(!visible)el('historyPanel').hidden=true;
 if(persist){try{localStorage.setItem('catfood-record-bar-visible',String(visible));}catch{}}
 fitCompanion();
}
el('recordVisibilityToggle').onclick=()=>setRecordBarVisible(!recordBarVisible);
setRecordBarVisible(recordBarVisible,false);
function native(action){const bridge=window.webkit?.messageHandlers?.nekoWidget||window.chrome?.webview;if(!bridge)return false;bridge.postMessage(['recordStart','recordEnd'].includes(action)?{action,session_id:recordingState?.id}:action);return true;}
let agentState='unknown',agentData=null,idleCard=false;
const wardrobe=window.CatfoodWardrobe;
let outfitMode='auto',fixedOutfit='original',requestedOutfit=null,outfitRevision=0;
try{const saved=JSON.parse(localStorage.getItem('catfood-wardrobe'));if(saved){if(['auto','profile','fixed'].includes(saved.mode))outfitMode=saved.mode;if(wardrobe.outfits[saved.fixed])fixedOutfit=saved.fixed;}}catch{}
el('outfitFollow').value=outfitMode;
function saveOutfitPreference(){try{localStorage.setItem('catfood-wardrobe',JSON.stringify({mode:outfitMode,fixed:fixedOutfit}));}catch{}}
function updateCostume(){
 const choice=wardrobe.resolve(state,agentData,outfitMode,fixedOutfit),outfit=wardrobe.outfits[choice.key];
 el('outfitContext').textContent=choice.reason+(choice.model?' · '+choice.model:'')+' · '+outfit.style;
 for(const button of el('outfitChoices').children){const selected=button.dataset.outfit===choice.key;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));}
 if(requestedOutfit===choice.key)return;
 requestedOutfit=choice.key;const revision=++outfitRevision,image=el('character').querySelector('img'),preload=new Image();
 preload.onload=()=>{if(revision!==outfitRevision)return;image.src=outfit.image;image.alt='YUI 穿着 '+outfit.name+' 的'+outfit.style;el('character').dataset.outfit=choice.key;el('character').dataset.outfitChangedAt=String(Date.now());};
 preload.onerror=()=>{if(revision!==outfitRevision)return;image.src='/yui-chibi.png';image.alt='YUI 原装';el('character').dataset.outfit='original';el('outfitContext').textContent='服装暂未载入，已恢复 YUI 原装';requestedOutfit=null;};
 preload.src=outfit.image;
}
for(const [key,outfit] of Object.entries(wardrobe.outfits)){
 const button=document.createElement('button');button.type='button';button.dataset.outfit=key;button.setAttribute('aria-label','穿上 '+outfit.name+' · '+outfit.style);
 const image=document.createElement('img');image.src=outfit.image;image.alt='';image.loading='lazy';image.draggable=false;
 const label=document.createElement('strong');label.textContent=outfit.name;const style=document.createElement('small');style.textContent=outfit.style;
 button.append(image,label,style);button.onclick=()=>{fixedOutfit=key;outfitMode='fixed';el('outfitFollow').value=outfitMode;saveOutfitPreference();updateCostume();};el('outfitChoices').append(button);
}
el('outfitToggle').onclick=()=>{closeContextMenus();el('outfitPanel').hidden=false;el('historyPanel').hidden=true;updateCostume();};
el('closeOutfits').onclick=()=>el('outfitPanel').hidden=true;
el('outfitFollow').onchange=()=>{outfitMode=el('outfitFollow').value;saveOutfitPreference();updateCostume();};
const quotaMood=window.CatfoodQuotaMood;
const percentText=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1}).format(value)+'%';
function renderQuotaMood(){
 const q=quotaMood.quota(state),tier=quotaMood.tier(q?.percent),character=el('character');
 window.catfoodRefreshBags?.(state);
 character.dataset.mood=tier;
 if(q)character.dataset.remainingPercent=String(q.percent);else delete character.dataset.remainingPercent;
 const description=q?(q.stale?'上次数据 · ':'')+'猫粮剩余 '+percentText(q.percent)+' · '+quotaMood.labels[tier]:state?.allowance?.value===null?state.allowance.status_label:quotaMood.labels.unknown;
 character.title=description+' · 右键打开人物设置';character.setAttribute('aria-label','摸摸猫娘，按住拖动，右键打开人物设置。'+description);
 window.catfoodQuotaState=q;window.dispatchEvent(new CustomEvent('catfood:quota',{detail:q}));
 return q;
}
function render(){
 if(!state)return;
 const view=window.CatfoodQuotaDisplay.present(state);
 el('heading').textContent='猫粮余额';el('amount').classList.remove('idle-label');
 el('amount').classList.toggle('token-amount',view.token);el('amount').textContent=view.amount;el('amount').title=view.title;
 const signature=(state.profile||'')+'|'+view.amount+'|'+view.detail;
 if(el('readout').dataset.signature!==signature){el('readout').dataset.signature=signature;el('readout').classList.remove('quota-updated');void el('readout').offsetWidth;el('readout').classList.add('quota-updated');}
 el('detail').textContent=view.detail;el('connection').textContent=view.connection;
 window.catfoodRefreshPeek?.(state);
}
function syncBubble(){
 const visible=state?.tracking?.bubble_visible!==false,wasHidden=el('thought').hidden;
 el('thought').hidden=!visible;document.body.classList.toggle('bubble-hidden',!visible);
 if(visible&&wasHidden)reportLauncherLayout();
 const toggle=el('bubbleVisibilityToggle');toggle.setAttribute('aria-checked',String(visible));toggle.disabled=bubbleToggleBusy||!state;
 if(!visible&&contextMenuOwner===el('thought'))closeContextMenus();
}
el('bubbleVisibilityToggle').onclick=async()=>{
 if(bubbleToggleBusy||!state||!csrf)return;
 const toggle=el('bubbleVisibilityToggle'),visible=state.tracking?.bubble_visible!==false;
 bubbleToggleBusy=true;syncBubble();
 try{
  const response=await fetch('/api/tracking',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({bubble_visible:!visible})});
  const data=await response.json();if(!response.ok)throw Error(data.error||'气泡开关保存失败');
  state=data;csrf=data.csrf||csrf;toggle.textContent='显示猫粮气泡';toggle.title='';renderAgentCard();
 }catch(error){toggle.textContent='气泡保存失败，点此重试';toggle.title=error.message;}
 finally{bubbleToggleBusy=false;syncBubble();}
};
function renderAgentCard(){
 syncBubble();updateCostume();renderQuotaMood();if(talk||!state)return;
 if(idleCard){
  const names=(agentData?.active_agents||[]).map(a=>a.name);
  el('heading').textContent='当前 Agent：';el('amount').textContent=agentState==='busy'?(names.length===1?names[0]:names.length+' 个在工作'):agentState==='idle'?'空闲时段':'状态未连接';
  el('detail').textContent=agentState==='busy'?(names.join('、')+' · 正在工作'):agentState==='idle'?'Agent 没在忙，本喵陪你喵～':'暂时读不到 Agent 状态';
  el('amount').classList.toggle('idle-label',agentState==='idle');el('amount').classList.remove('token-amount');el('amount').title='';
 }else{render();}
 if(state.tracking){
  el('thought').title=state.tracking.hint+' · 右键打开猫粮额度设置';

 }
}
function say(text){talk=true;el('readout').hidden=true;el('quip').hidden=false;el('quip').textContent=text;el('quip').classList.remove('fade');void el('quip').offsetWidth;el('quip').classList.add('fade');clearTimeout(timer);timer=setTimeout(restore,4200);}
function restore(){clearTimeout(timer);talk=false;el('quip').hidden=true;el('readout').hidden=false;renderAgentCard();}
const quips=['哼，本喵在呢。\n碳基生物，安心忙吧喵～','猫粮本喵盯着，\n你记得按时吃饭喵～','才不是特意等你。\n只是这里待着舒服喵～','累了就歇一会。\n本喵又不会跑掉喵～','有事就叫本喵。\n别一个人硬撑喵～','又偷看本喵？\n好啦，陪你一会喵～'];
quips.push('哦喵喵…','深眠……喵～','坏了…用户彻底怒了喵～！','好猫娘…喵～↓');

const clickAnimations=new WeakMap();
function bounce(id){
 const target=el(id);clickAnimations.get(target)?.cancel();
 if(window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 const character=id==='character';
 const frames=character?[
  {transform:'translateY(0) scale(1,1)',offset:0},
  {transform:'translateY(1px) scale(1.035,.94)',offset:.16},
  {transform:'translateY(-15px) scale(.985,1.025)',offset:.43},
  {transform:'translateY(0) scale(1.02,.97)',offset:.7},
  {transform:'translateY(-4px) scale(1,1)',offset:.85},
  {transform:'translateY(0) scale(1,1)',offset:1}
 ]:[
  {transform:'translateY(0) scale(1)',offset:0},
  {transform:'translateY(2px) scale(.965)',offset:.2},
  {transform:'translateY(-4px) scale(1.012)',offset:.5},
  {transform:'translateY(0) scale(.994)',offset:.77},
  {transform:'translateY(0) scale(1)',offset:1}
 ];
 clickAnimations.set(target,target.animate(frames,{duration:character?500:380,easing:'ease-out'}));
}

el('thought').onclick=e=>{if(e.button!==0||e.ctrlKey)return;recordAction('ask');bounce('thought');idleCard=!idleCard;restore();};
const petLines=['突、突然摸头干嘛……\n只许再摸一下喵～','哼，头发都被你揉乱了。\n……也没说不让你摸喵～','好啦，本喵在这里。\n今天也辛苦你了喵～','碳基生物，轻一点。\n本喵又不会跑喵～'];let petIndex=0;
function pet(){recordAction("pet");bounce('character');bounce('thought');const lines=[...quips,...petLines];const line=lines[petIndex++%lines.length];say(line);if(!browserPreview)requestSpokenLine(line);}
el('character').onclick=e=>{if(e.button===0&&!e.ctrlKey)pet();};
let companionDrag=null,suppressDragClick=false;
el('companion').addEventListener('pointerdown',e=>{
 if(!e.isPrimary||e.button!==0||e.ctrlKey||companionDrag)return;
 const handle=e.target.closest('#character,#thought,#recordBar,#historyPanel header,#outfitPanel header');
 const control=e.target.closest('button,input,select,textarea,a');
 if(control&&!['character','thought'].includes(control.id))return;
 if(!handle&&e.target!==el('companion')&&e.target!==el('pet')&&!e.target.classList.contains('dot'))return;
 const capture=handle||el('companion'),box=el('companion').getBoundingClientRect();
 companionDrag={id:e.pointerId,capture,x:e.clientX,y:e.clientY,screenX:e.screenX,screenY:e.screenY,cx:box.left+box.width/2,cy:box.top+box.height/2,moved:false};
 suppressDragClick=false;capture.setPointerCapture(e.pointerId);
 if(!browserPreview)native('dragStart');
});
el('companion').addEventListener('pointermove',e=>{
 const d=companionDrag;if(!d||e.pointerId!==d.id)return;
 const dx=(browserPreview?e.clientX:e.screenX)-(browserPreview?d.x:d.screenX),dy=(browserPreview?e.clientY:e.screenY)-(browserPreview?d.y:d.screenY);
 if(!d.moved&&Math.hypot(dx,dy)<=4)return;
 if(!d.moved){d.moved=true;el('companion').classList.add('dragging');clickAnimations.get(el('character'))?.cancel();clickAnimations.get(el('thought'))?.cancel();}
 e.preventDefault();
 if(browserPreview)companionPosition=placeCompanion(d.cx+dx,d.cy+dy);else native('dragMove');
});
function endCompanionDrag(e){
 const d=companionDrag;if(!d||e.pointerId!==d.id)return;
 companionDrag=null;el('companion').classList.remove('dragging');
 if(!browserPreview)native('dragEnd');
 if(d.capture.hasPointerCapture(d.id))d.capture.releasePointerCapture(d.id);
 if(d.moved){suppressDragClick=true;setTimeout(()=>{suppressDragClick=false;},0);if(browserPreview){try{localStorage.setItem('catfood-companion-position',JSON.stringify(companionPosition));}catch{}}}
}
for(const type of ['pointerup','pointercancel','lostpointercapture'])el('companion').addEventListener(type,endCompanionDrag);
el('companion').addEventListener('click',e=>{if(suppressDragClick){e.preventDefault();e.stopImmediatePropagation();}},true);
let contextMenuOwner=null;
function closeContextMenus(restoreFocus=false){
 for(const id of ['menu','quotaMenu'])el(id).hidden=true;
 for(const id of ['character','thought'])el(id).setAttribute('aria-expanded','false');
 if(restoreFocus)contextMenuOwner?.focus({preventScroll:true});
}
function openContextMenu(owner,event){
 event?.preventDefault();if(document.body.classList.contains('record-finished'))return;
 closeContextMenus();contextMenuOwner=owner;
 const menu=el(owner.id==='thought'?'quotaMenu':'menu');menu.hidden=false;owner.setAttribute('aria-expanded','true');
 const box=owner.getBoundingClientRect(),x=event?.clientX||box.left+box.width/2,y=event?.clientY||box.top+box.height/2;
 menu.style.left=Math.max(8,Math.min(innerWidth-menu.offsetWidth-8,x))+'px';
 menu.style.top=Math.max(8,Math.min(innerHeight-menu.offsetHeight-8,y))+'px';
 menu.querySelector('button:not(:disabled)')?.focus({preventScroll:true});
}
for(const id of ['thought','character']){
 const owner=el(id);owner.addEventListener('contextmenu',e=>openContextMenu(owner,e));
 owner.addEventListener('keydown',e=>{if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10')){e.preventDefault();openContextMenu(owner);}});
}
document.addEventListener('pointerdown',e=>{if(!e.target.closest('.contextMenu'))closeContextMenus();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeContextMenus(true);});
window.addEventListener('blur',()=>closeContextMenus());window.addEventListener('resize',()=>closeContextMenus());
for(const id of ['menu','quotaMenu']){
 const menu=el(id);menu.addEventListener('click',e=>{if(e.target.closest('button'))closeContextMenus();});
 menu.addEventListener('keydown',e=>{
  const buttons=[...menu.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
   e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next]?.focus();
  }else if(e.key==='Tab')closeContextMenus();
 });
}
for(const id of ['settings','characterSettings'])el(id).onclick=()=>{closeContextMenus();openAccount();};
el('topupOpen').onclick=()=>{closeContextMenus();if(state?.profile)window.CatfoodTopUp.open(state.profile);};
let updateBusy=false,refreshPending=false;
async function update(refresh=false){if(updateBusy){refreshPending=refreshPending||refresh;return;}updateBusy=true;try{const r=await fetch(refresh?'/api/refresh':'/api/status',refresh?{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:'{}'}:{});if(!r.ok)throw Error();state=await r.json();window.catfoodQuotaOnline=true;window.catfoodQuotaReadAt=Date.now();csrf=state.csrf||csrf;renderAgentCard();}catch{window.catfoodQuotaOnline=false;el('connection').textContent='插件连接中断';if(state){state.stale=true;state.estimated_tokens=null;renderQuotaMood();render();el('connection').textContent='插件连接中断';}}finally{updateBusy=false;if(refreshPending){refreshPending=false;update(true);}}}
el('refreshNow').onclick=()=>{closeContextMenus();update(true);};update();setInterval(()=>update(),1500);

let agentPollTimer=null,agentPollBusy=false,agentFingerprint='';
function scheduleAgentPoll(){clearTimeout(agentPollTimer);agentPollTimer=setTimeout(pollAgent,document.hidden?5000:500);}
async function pollAgent(){
 if(agentPollBusy)return;
 clearTimeout(agentPollTimer);agentPollBusy=true;
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),1500);
 try{const r=await fetch('/api/agent',{signal:controller.signal});if(!r.ok)throw Error();agentData=await r.json();agentState=['idle','busy'].includes(agentData.status)?agentData.status:'unknown';}
 catch{agentState='unknown';agentData=null;}
 finally{clearTimeout(timeout);agentPollBusy=false;}
 const fingerprint=JSON.stringify({status:agentState,agents:agentData?.active_agents||[]});
 if(fingerprint!==agentFingerprint){
  agentFingerprint=fingerprint;const names=(agentData?.active_agents||[]).map(a=>a.name);
  el('agentStatus').textContent=agentState==='idle'?'空闲时段 · 喵～':agentState==='busy'?(names.join('、')+' 工作中 · 喵～'):'Agent 状态未连接';
  el('agentStatus').classList.toggle('idle',agentState==='idle');renderAgentCard();
 }
 scheduleAgentPoll();
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)scheduleAgentPoll();else pollAgent();});
pollAgent();

async function requestSpokenLine(line){if(!csrf)return;try{const r=await fetch('/api/reply',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({line})});const result=await r.json();el('connection').textContent=r.ok&&result.submitted?'已请求原软件回应\n出声取决于语音状态':r.status===429?'语音稍等一下喵～':result.error||'原软件语音暂不可用';}catch{el('connection').textContent='原软件语音未连接';}}

let recordingActive=false, recordingState=null, recordQueue=Promise.resolve(), exiting=false,takeoverConfirmed=false,nativeHideTimer=null;
const lastStatusNotes=new Map();
function noteStatus(kind,message){
 if(!recordingActive||exiting||!message||lastStatusNotes.get(kind)===message)return;
 lastStatusNotes.set(kind,message);
 recordAction('note',{kind,message:message.slice(0,240)}).catch(()=>{});
}
for(const [id,kind] of [['agentStatus','agent'],['connection','connection']]){
 new MutationObserver(()=>noteStatus(kind,el(id).textContent)).observe(el(id),{childList:true,characterData:true,subtree:true});
}
function renderRecording(d){
 if(recordingActive&&!d.active){window.dispatchEvent(new Event('catfood:recordend'));native('recordEnd');document.body.classList.add('record-inactive');}
 const newlyActive=!recordingActive&&!!d.active;
 recordingState=d;recordingActive=!!d.active;
 window.dispatchEvent(new CustomEvent('catfood:recordsnapshot',{detail:d}));
 if(newlyActive){clearTimeout(nativeHideTimer);nativeHideTimer=null;takeoverConfirmed=false;lastStatusNotes.clear();document.body.classList.remove('record-finished');el('recordCounts').title='正在确认人物替换';restore();noteStatus('agent',el('agentStatus').textContent);noteStatus('connection',el('connection').textContent);}
 el('exitRecord').hidden=browserPreview||window.catfoodHasLauncher===true;
 el('exitRecord').textContent=recordingActive?'退出猫粮监控':'开启猫粮监控';
 el('exitRecord').disabled=browserPreview;
 el('recordCounts').textContent=d.error||`本次摸头 ${d.pet||0} · 询问 ${d.ask||0} ｜累计摸头 ${d.totals?.pet||0} · 询问 ${d.totals?.ask||0}`;
 el('historyTotals').textContent=`共 ${d.totals?.sessions||0} 段记录，摸头 ${d.totals?.pet||0} 次，询问 ${d.totals?.ask||0} 次喵～`;
 el('memoryStatus').textContent=d.pending_memory?`${d.pending_memory} 段已保存在本机，等待写入 YUI 记忆；后台会重试喵～`:'已完成记录的记忆写入均已确认喵～';
 window.catfoodRefreshDiary?.();
}
function pendingClicks(){try{return JSON.parse(localStorage.getItem('catfood-pending-clicks')||'[]');}catch{return [];}}
function savePending(items){localStorage.setItem('catfood-pending-clicks',JSON.stringify(items));}
function pendingEnds(){try{const rows=JSON.parse(localStorage.getItem('catfood-pending-ends')||'[]');return Array.isArray(rows)?rows.filter(x=>x&&typeof x.session_id==='string'&&x.session_id.length>0&&x.session_id.length<=100):[];}catch{return [];}}
function savePendingEnds(items){localStorage.setItem('catfood-pending-ends',JSON.stringify(items));}
async function sendRecord(body){
 if(!csrf)throw Error('插件尚未连接喵～');
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
 try{const r=await fetch('/api/recording',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify(body),signal:controller.signal});if(!r.ok)throw Error('记录未保存喵～');const d=await r.json();if(d.error)throw Error(d.error);renderRecording(d);return d;}finally{clearTimeout(timeout);}
}
async function flushClicks(){for(const item of pendingClicks()){await sendRecord(item);savePending(pendingClicks().filter(x=>x.event_id!==item.event_id));}}
async function flushEnds(){for(const item of pendingEnds()){const d=await sendRecord(item);if(d.ended_session_id!==item.session_id)throw Error('结束记录仍待同步喵～');savePendingEnds(pendingEnds().filter(x=>x.session_id!==item.session_id));}}
function recordAction(action,details={}){
 if(browserPreview&&action!=='status')return Promise.resolve(null);
 if(['pet','ask','note','game'].includes(action)){
  if(!recordingActive||exiting)return Promise.resolve(null);
  const item={action,event_id:crypto.randomUUID(),session_id:recordingState.id,...(['pet','ask'].includes(action)?{at:new Date().toISOString()}:{}),...details};savePending([...pendingClicks(),item]);
 }
 if(action==='end'){
  const id=details.session_id||recordingState?.id;
  if(id&&!pendingEnds().some(x=>x.session_id===id))savePendingEnds([...pendingEnds(),{action:'end',session_id:id}]);
 }
 const work=async()=>{
  if(!browserPreview){
   // Close sessions before replaying clicks; old clicks retain their original session ID.
   // A failed close remains durable and blocks start/status from resuming that session.
   await flushEnds();
   if(action==='end'){try{await flushClicks();}catch{}return recordingState;}
   await flushClicks();
  }
  return ['pet','ask','note','game'].includes(action)?recordingState:sendRecord({action});
 };
 const result=recordQueue.then(work);recordQueue=result.catch(()=>{el('recordCounts').textContent='记录暂未同步，点击已暂存在本机，将自动重试喵～';});return result;
}
async function finishRecording(){
 if(browserPreview)return;
 if(exiting)return;exiting=true;el('exitRecord').disabled=true;
 // Restore the original immediately even when the loopback server is unavailable.
 window.dispatchEvent(new Event('catfood:recordend'));native('recordEnd');document.body.classList.add('record-inactive','record-finished');el('historyPanel').hidden=true;el('outfitPanel').hidden=true;closeContextMenus();
 try{const d=await recordAction('end');if(d?.summary){say(pendingClicks().length?'已结束记录，互动次数会在同步后更新喵～':d.summary);clearTimeout(timer);el('connection').textContent=d.memory_saved?'已写入 YUI 记忆喵～':'互动已保存，记忆在后台同步喵～';}return d;}
 catch(e){say('已恢复 NEKO。\n结束记录已暂存，恢复连接后自动同步喵～');clearTimeout(timer);}
 finally{exiting=false;el('exitRecord').disabled=false;nativeHideTimer=setTimeout(()=>native('hide'),3200);}
}
let nativeTimer=null;
function requestNativeTakeover(){
 if(takeoverConfirmed)return true;
 clearTimeout(nativeTimer);const session=recordingState?.id;
 nativeTimer=setTimeout(()=>{if(session===recordingState?.id&&recordingActive&&!takeoverConfirmed)window.catfoodNativeState({active:true,ok:false,reason:'timeout',session_id:session});},38000);
 if(native('recordStart'))return true;
 clearTimeout(nativeTimer);return false;
}
async function reportTakeover(d){
 const session=d.session_id||recordingState?.id;if(!csrf||!session)return false;
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),3000);
 try{const response=await fetch('/api/takeover',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({session_id:session,ok:!!d.ok,reason:d.reason||null}),signal:controller.signal});return response.ok&&(await response.json()).accepted===true;}
 catch{return false;}finally{clearTimeout(timeout);}
}
window.catfoodNativeState=d=>{
 if(d.session_id&&d.session_id!==recordingState?.id)return;
 clearTimeout(nativeTimer);
 if(!d.active||!recordingActive||exiting)return;
 const session=recordingState.id,report=reportTakeover(d);
 if(d.ok){if(!takeoverConfirmed)restore();takeoverConfirmed=true;document.body.classList.remove('record-inactive');el('recordCounts').title=d.paused?'原模型已暂停，原程序窗口已隐藏':'原程序窗口已隐藏';}
 else{takeoverConfirmed=false;document.body.classList.add('record-inactive');el('recordCounts').title='人物未替换，原版已保留';native('recordEnd');report.then(()=>recordAction('end',{session_id:session})).catch(()=>{});say(d.reason==='controls_unavailable'?'原版工具栏暂不可用，已取消接管。\n保留原版 YUI 陪你喵～':d.reason==='accessibility'?'请先在系统设置授予猫粮辅助功能权限，\n再重新打开挂件喵～':'监控模式未启动，人物未替换。\n已保留原版，请重试喵～');clearTimeout(timer);el('connection').textContent=d.reason==='accessibility'?'辅助功能权限不可用':d.reason==='hide_unconfirmed'?'原版窗口隐藏未确认':d.reason==='controls_unavailable'?'原版工具栏暂不可用，接管已取消':'原版人物暂停未确认';}
};
async function startRecording(){
 if(browserPreview)return;
 try{const d=await recordAction('start');if(!d?.active)return;
 document.body.classList.add('record-inactive');restore();
 if(!requestNativeTakeover()){await recordAction('end');say('请从 NEKO 打开桌面挂件喵～');clearTimeout(timer);return;}
 }catch(e){say(e.message);}
}
el('exitRecord').onclick=()=>{el('menu').hidden=true;return recordingActive?finishRecording():startRecording();};
let launcherToggleBusy=false;
window.catfoodToggleMonitoring=async()=>{
 if(browserPreview||launcherToggleBusy||exiting)return;
 launcherToggleBusy=true;
 try{if(!csrf)throw Error('正在连接猫粮，请稍后再试喵～');return await(recordingActive?finishRecording():startRecording());}
 finally{launcherToggleBusy=false;native('toggleSettled');}
};
el('historyToggle').onclick=()=>{closeContextMenus();el('outfitPanel').hidden=true;el('historyPanel').hidden=false;window.catfoodRefreshDiary?.(true);recordAction('status').catch(()=>{});};el('closeHistory').onclick=()=>el('historyPanel').hidden=true;
async function retryMemory(){const d=await recordAction('retry');if(d)say(d.pending_memory?'互动已保存，正在重试记忆同步喵～':'已写入 YUI 的记忆喵～');}
el('retryMemory').onclick=()=>{el('menu').hidden=true;retryMemory().catch(()=>{});};el('historyRetry').onclick=()=>retryMemory().catch(()=>{});
document.body.classList.add('record-inactive');
const beginRecording=setInterval(async()=>{if(!csrf)return;clearInterval(beginRecording);try{const d=await recordAction('status');if(browserPreview)return;if(d?.active){requestNativeTakeover();}else if(!d?.initialized&&!window.catfoodLauncherOnly)await startRecording();}catch{}},200);
let recordPollBusy=false;setInterval(async()=>{if(!csrf||exiting||recordPollBusy)return;recordPollBusy=true;try{const wasActive=recordingActive;const d=await recordAction('status');if(!browserPreview&&d?.active&&!wasActive){document.body.classList.add('record-inactive');if(!requestNativeTakeover())await recordAction('end');}}catch{}finally{recordPollBusy=false;}},1000);
