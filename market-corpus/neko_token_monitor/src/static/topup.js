/* Open a verified official entry; never collect payment details or change quota. */
(()=>{
 const sheet=document.createElement('section');sheet.id='topupPanel';sheet.hidden=true;
 sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');sheet.setAttribute('aria-labelledby','topupTitle');
 sheet.innerHTML='<header><h2 id="topupTitle">补充猫粮</h2><button id="topupClose" type="button" aria-label="关闭补充猫粮">×</button></header><section id="topupRoute"><strong id="topupRouteLabel"></strong><p id="topupRouteReason"></p><p id="topupRouteGuide"></p></section><p id="topupHint"></p><div id="topupLinks"></div><p id="topupStatus" role="status"></p><button id="topupRefresh" type="button">返回后刷新额度</button>';
 document.body.append(sheet);
 const el=id=>document.getElementById(id);
 let current=null,csrf='',owner=null,opening=false;
 const notice=document.createElement('p');notice.id='topupNotice';notice.hidden=true;notice.setAttribute('role','status');document.body.append(notice);
 let noticeTimer;
 function notify(message){clearTimeout(noticeTimer);notice.textContent=message;notice.hidden=false;noticeTimer=setTimeout(()=>notice.hidden=true,7000);}
 const native=()=>!!(window.webkit?.messageHandlers?.nekoWidget||window.chrome?.webview);
 function close(){sheet.hidden=true;owner?.focus?.();}
 el('topupClose').onclick=close;
 sheet.addEventListener('keydown',e=>{
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}
  if(e.key==='Tab'){
   const nodes=[...sheet.querySelectorAll('a[href],button:not(:disabled)')];
   const first=nodes[0],last=nodes[nodes.length-1];
   if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
   else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
  }
 });
 function render(info,name){
  current=info;
  el('topupTitle').textContent=(name?name+' · ':'')+'补充猫粮';el('topupHint').textContent=info.hint;
  const route=info.routing||{};
  el('topupRoute').hidden=!route.label;el('topupRouteLabel').textContent=route.label||'';
  el('topupRouteReason').textContent=route.reason||'';el('topupRouteGuide').textContent=route.guide||'';el('topupRouteGuide').hidden=!route.guide;
  el('topupStatus').textContent='';el('topupLinks').replaceChildren();
  const badge=(row,target)=>{
   if(route.target!==target)return;
   row.dataset.recommended='true';const label=document.createElement('small');label.className='topup-recommended';label.textContent='推荐入口';row.prepend(label);
  };
  if(info.application){
   const row=document.createElement('article'),button=document.createElement('button'),hint=document.createElement('p');
   button.id='topupApplication';button.type='button';button.textContent=info.application.label;hint.textContent=info.application.hint;
   button.onclick=async()=>{
    button.disabled=true;el('topupStatus').textContent='正在打开应用内增购页面，请稍候喵～';
    try{
     const r=await fetch('/api/topup',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({profile:info.profile,target:'application'})});
     const result=await r.json();
     if(current===info)el('topupStatus').textContent=result.message||(r.ok&&result.page_confirmed?'已打开应用内页面，请自行完成支付喵～':'未能确认应用内页面，请使用下方官网入口喵～');
    }catch{if(current===info)el('topupStatus').textContent='连接中断，未能确认应用内页面。请检查应用，或使用下方官网入口喵～';}
    finally{button.disabled=false;}
   };
   row.append(button,hint);badge(row,'application');el('topupLinks').append(row);
  }
  for(const item of info.entries||[]){
   const row=document.createElement('article'),a=document.createElement('a'),domain=document.createElement('small'),hint=document.createElement('p');
   a.textContent=item.label+' ↗';a.href=item.url;a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';
   domain.textContent=item.domain;hint.textContent=item.hint;
   a.onclick=async e=>{
    if(!native()){el('topupStatus').textContent='正在打开官方页面；支付与到账以官网为准喵～';return;}
    e.preventDefault();if(a.getAttribute('aria-disabled')==='true')return;
    a.setAttribute('aria-disabled','true');
    try{
     const r=await fetch('/api/topup',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({profile:info.profile,target:item.id})});
     const result=await r.json();if(!r.ok||!result.requested)throw Error();
     el('topupStatus').textContent='已请求在浏览器打开官方页面；支付与到账以官网为准喵～';
    }catch{el('topupStatus').textContent='未能打开，请稍后重试，或手动前往上方官方域名喵～';}
    finally{a.removeAttribute('aria-disabled');}
   };
   row.append(a,domain,hint);badge(row,item.id);el('topupLinks').append(row);
  }
  const items=[...el('topupLinks').children];items.sort((a,b)=>Number(b.dataset.recommended==='true')-Number(a.dataset.recommended==='true'));el('topupLinks').append(...items);
  sheet.hidden=false;el('topupClose').focus();
 }
 async function open(profile,name){
  if(opening)return;
  opening=true;
  owner=document.activeElement;
  try{
   const r=await fetch('/api/status');if(!r.ok)throw Error();const s=await r.json();csrf=s.csrf;
   const row=s.profiles?.find(p=>p.id===profile);
   const info=row?.topup||(s.profile===profile?s.topup:null);
   if(info&&['application','website'].includes(info.routing?.kind)&&info.routing.target){
    notify('正在打开补充猫粮入口喵～');
    const response=await fetch('/api/topup',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:JSON.stringify({profile,target:'default'})});
    const result=await response.json();
    if(response.ok&&result.requested){close();notify(result.message||'已打开官方充值页面喵～');return;}
    render(info,name||row?.name||profile);notice.hidden=true;
    el('topupStatus').textContent=result.message||'未能打开默认入口，请选择备用入口喵～';return;
   }
   render(info||{profile,entries:[],hint:'此来源尚无已核验的官方充值入口，请到实际服务商账户管理页查看。'},name||row?.name||profile);
  }catch{render({profile,entries:[],hint:'插件连接中断，暂时无法核对官方入口，请稍后重试喵～'},name);}
  finally{opening=false;}
 }
 el('topupRefresh').onclick=async()=>{
  const button=el('topupRefresh');button.disabled=true;
  try{
   const r=await fetch('/api/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':csrf},body:'{}'});
   if(!r.ok)throw Error();const s=await r.json();
   window.dispatchEvent(new CustomEvent('catfood:statusrefresh',{detail:s}));
   if(typeof update==='function')await update();
   el('topupStatus').textContent=s.error?'查询未成功，保留上次数据喵～':s.profile!==current?.profile?'已刷新当前展示的平台；请切换到刚充值的平台查看喵～':
    ['manual_quota','manual'].includes(s.config?.source)||s.config?.quota_mode==='manual'?'这是手动记录，请在账户设置中校正剩余额度喵～':'已请求刷新；以查询结果为准，到账可能需要时间喵～';
  }catch{el('topupStatus').textContent='刷新未成功，保留上次数据喵～';}
  finally{button.disabled=false;}
 };
 window.CatfoodTopUp={open};
})();
