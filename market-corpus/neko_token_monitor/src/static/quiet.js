/* Passive local notices: no voice request, activation, or inferred task completion. */
(()=>{
 const bridge=window.webkit?.messageHandlers?.nekoWidget;
 const standalone=document.getElementById('standaloneNotice');
 let token='',pollBusy=false,activeId='',busyUntil=0;
 let seen=[];try{const stored=JSON.parse(localStorage.getItem('catfood-quiet-seen')||'[]');if(Array.isArray(stored))seen=stored.filter(id=>typeof id==='string').slice(-300);}catch{}
 if(Array.isArray(window.catfoodNativeQuietSeenIds))seen=[...new Set([...seen,...window.catfoodNativeQuietSeenIds])].slice(-300);
 function markSeen(id){seen=[...seen.filter(value=>value!==id),id].slice(-300);try{localStorage.setItem('catfood-quiet-seen',JSON.stringify(seen));}catch{}}
 window.catfoodNativeQuietSeen=markSeen;
 function closeWindow(id){if(bridge)bridge.postMessage({action:'quietNoticeClose',notice_id:id});else if(standalone)standalone.hidden=true;}
 async function respond(action,id){
  if(!token){const r=await fetch('/api/status');if(!r.ok)throw Error('连接暂时中断，请稍后重试喵～');token=(await r.json()).csrf;}
  const r=await fetch('/api/care',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':token},body:JSON.stringify({action,notice_id:id})});
  const d=await r.json();if(!r.ok)throw Error(d.error||'选择暂未保存，请重试喵～');return d;
 }
 function card(notice,onClose){
  const node=document.createElement('section');node.className='quietCard';node.setAttribute('aria-label',notice.kind==='low_food'?'YUI 的猫粮提醒':'任务完成小牌子');
  node.innerHTML='<header><small>YUI 的小纸条</small><button class="quietClose" type="button" aria-label="收起这条小纸条">×</button></header><div class="quietBody"><img src="/yui-chibi.png" alt="YUI" draggable="false"><div><strong></strong><p class="quietFact"></p></div></div><div class="quietActions"></div><p class="quietHint"></p><p class="quietFeedback" role="status"></p>';
  node.querySelector('strong').textContent=notice.line;
  node.querySelector('strong').title=notice.line;
  const amount=notice.quota?.remaining,unit=notice.quota?.unit;
  node.querySelector('.quietFact').textContent=typeof amount==='number'?('剩余 '+amount.toLocaleString('zh-CN',{maximumFractionDigits:2})+' '+(unit||'')):notice.kind==='task_completed'?'N.E.K.O 已明确报告完成':'';
  node.querySelector('.quietHint').textContent=notice.kind==='low_food'?'同一档只提醒一次；额度进一步下降仍会提醒。':'';
  const act=async(action)=>{
   const buttons=[...node.querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
   try{const d=await respond(action,notice.id);onClose?.(d);node.remove();}
   catch(error){node.querySelector('.quietFeedback').textContent=error.message;buttons.forEach(button=>button.disabled=false);}
  };
  node.querySelector('.quietClose').onclick=()=>act('dismiss');
  if(notice.kind==='low_food')for(const [action,label] of [['snooze','半小时后再提醒'],['today','今天知道啦']]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>act(action);node.querySelector('.quietActions').append(button);}
  return node;
 }
 function renderList(container,data){
  if(!container)return;container.replaceChildren();
  const rows=(data.notices||[]).filter(notice=>notice.kind==='low_food');
  for(const notice of rows)container.append(card(notice,next=>renderList(container,next)));
  if(!rows.length){const text=document.createElement('p');text.className='carePolicy';text.textContent='目前没有待处理的猫粮提醒喵～';container.append(text);}
 }
 window.CatfoodQuiet={renderList};
 if(standalone){
  const requested=new URLSearchParams(location.search).get('id');let rendered='';
  async function load(){
   if(pollBusy)return;pollBusy=true;
   try{
    if(!token){const r=await fetch('/api/status');if(!r.ok)throw Error();token=(await r.json()).csrf;}
    const r=await fetch('/api/care');if(!r.ok)throw Error();const d=await r.json(),notice=(d.notices||[]).find(n=>n.id===requested);
    if(!notice){closeWindow(requested);return;}
    if(rendered!==notice.id){rendered=notice.id;standalone.replaceChildren(card(notice,()=>closeWindow(notice.id)));}
   }catch{if(!rendered)standalone.textContent='小纸条暂时未连接喵～';}
   finally{pollBusy=false;}
  }
  load();setInterval(load,1500);return;
 }
 const companion=document.getElementById('companion');if(!companion)return;
 const board=document.createElement('div');board.id='taskBoard';board.hidden=true;board.setAttribute('role','status');board.innerHTML='<span class="boardPaw left" aria-hidden="true"></span><strong></strong><span class="boardPaw right" aria-hidden="true"></span>';document.getElementById('pet').append(board);
 const inline=document.createElement('aside');inline.id='quietInline';inline.hidden=true;document.body.append(inline);
 let boardTimer=null,inlineTimer=null;
 window.catfoodNoticeClosed=id=>{if(activeId===id){activeId='';busyUntil=0;}};
 function present(notice){
  markSeen(notice.id);activeId=notice.id;const duration=notice.kind==='low_food'?22000:6000;busyUntil=Date.now()+duration;
  const characterVisible=typeof browserPreview!=='undefined'&&(browserPreview||typeof recordingActive!=='undefined'&&recordingActive&&typeof takeoverConfirmed!=='undefined'&&takeoverConfirmed&&!document.body.classList.contains('record-inactive'))&&!document.body.classList.contains('record-finished');
  if(notice.kind==='task_completed'&&characterVisible){
   if(bridge)bridge.postMessage({action:'quietNoticeSeen',notice_id:notice.id});
   clearTimeout(boardTimer);board.querySelector('strong').textContent=notice.line;board.hidden=false;
   boardTimer=setTimeout(()=>{board.hidden=true;window.catfoodNoticeClosed(notice.id);},duration);return;
  }
  if(bridge){bridge.postMessage({action:'showQuietNotice',notice_id:notice.id,kind:notice.kind});return;}
  clearTimeout(inlineTimer);inline.replaceChildren(card(notice,()=>{inline.hidden=true;window.catfoodNoticeClosed(notice.id);}));inline.hidden=false;
  inlineTimer=setTimeout(()=>{inline.hidden=true;window.catfoodNoticeClosed(notice.id);},duration);
 }
 async function poll(){
  if(pollBusy||typeof csrf==='undefined'||!csrf)return;pollBusy=true;
  try{token=csrf;const r=await fetch('/api/care');if(!r.ok)return;const d=await r.json();
   if(activeId&&!(d.notices||[]).some(notice=>notice.id===activeId)){inline.hidden=true;board.hidden=true;clearTimeout(boardTimer);clearTimeout(inlineTimer);window.catfoodNoticeClosed(activeId);}
   if(Date.now()>=busyUntil){const pending=(d.notices||[]).filter(notice=>!seen.includes(notice.id)).sort((a,b)=>(b.kind==='low_food')-(a.kind==='low_food')||a.at-b.at);if(pending[0])present(pending[0]);}
  }catch{}
  finally{pollBusy=false;}
 }
 setInterval(poll,1500);poll();
 window.addEventListener('catfood:recordend',()=>{board.hidden=true;clearTimeout(boardTimer);});
 const thought=document.getElementById('thought'),peek=document.getElementById('quotaPeek'),stale=document.getElementById('staleMark');let peekTimer=null;
 function hidePeek(){clearTimeout(peekTimer);peek.hidden=true;}
 function showPeek(){clearTimeout(peekTimer);peekTimer=setTimeout(()=>{if(!thought.hidden)peek.hidden=false;},450);}
 thought.addEventListener('pointerenter',showPeek);thought.addEventListener('focus',showPeek);thought.addEventListener('pointerleave',hidePeek);thought.addEventListener('blur',hidePeek);thought.addEventListener('pointerdown',hidePeek);
 function refreshPeek(s){
  const a=s.allowance;
  if(a?.schema_version===1){
   const old=['stale','expired','invalid','conflict'].includes(a.status);
   stale.hidden=!old;stale.textContent=a.status==='conflict'?'!':'◷';stale.setAttribute('aria-label',a.status_label);
   thought.classList.toggle('old-quota',old);if(thought.hidden)hidePeek();
   const rows=[a.status_label+' · '+a.source_label,a.observed_at?'观测于 '+new Date(a.observed_at*1000).toLocaleString('zh-CN'):'观测时间未知',a.basis,a.account_scope.label];
   if(a.status==='conflict')rows.push('可比来源读数不一致，暂不触发低粮提醒');
   if(a.alternatives?.some(r=>r.decision==='separate_account_scope'))rows.push('另一个来源未核验为同一账户，保持各自独立');
   if(a.audit_error)rows.push(a.audit_error);
   peek.replaceChildren();for(const text of rows.filter(Boolean)){const p=document.createElement('p');p.textContent=text;peek.append(p);}return;
  }
  const config=s.config||{},manual=['manual','manual_quota','jev'].includes(config.source)||config.source==='desktop_quota'&&config.quota_mode==='manual';
  const old=!!(s.error||s.stale&&!manual),stamp=Number(s.updated_at);stale.hidden=!(old&&Number.isFinite(stamp)&&stamp>0);
  thought.classList.toggle('old-quota',!stale.hidden);if(thought.hidden)hidePeek();
  const rows=[old&&!manual?'上次读数，等待更新':manual?'手动记录 / 本机上报':'最近读数',Number.isFinite(stamp)&&stamp>0?'更新于 '+new Date(stamp*1000).toLocaleString('zh-CN'):'尚无更新时间',s.basis||'额度来源请在设置中查看'];
  const windows=(s.codex_quota?.windows||[]).filter(row=>typeof row.resets_at==='number'&&Number.isFinite(row.resets_at));
  for(const row of windows)rows.push(row.label+' · 重置于 '+new Date(row.resets_at*1000).toLocaleString('zh-CN'));
  if(config.quota_reset)rows.push('重置备注：'+config.quota_reset);
  peek.replaceChildren();for(const text of rows){const p=document.createElement('p');p.textContent=text;peek.append(p);}
 }
 window.catfoodRefreshPeek=refreshPeek;if(typeof state!=='undefined'&&state)refreshPeek(state);
})();
