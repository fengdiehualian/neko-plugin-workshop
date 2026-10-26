window.__ModuleLoader__.load({
 id:'@local/catfood-harness-bridge',
 factory(require){
  const React=require('react'),{createPortal}=require('react-dom'),h=React.createElement;
  function BridgeView(){
   // Electron contextBridge globals can yield fresh proxy identities on reads.
   // A render after loading must never dispose the native view and reopen it.
   const cfg=React.useRef(window.__CATFOOD_HARNESS_BRIDGE__).current,platform=React.useRef(window.dshPlatform).current;
   const client=React.useRef(crypto.randomUUID()),active=React.useRef(null),viewport=React.useRef(null),closing=React.useRef(false);
   const [command,setCommand]=React.useState(null),[failed,setFailed]=React.useState(false),[loaded,setLoaded]=React.useState(false);
   const send=React.useCallback(async(route,body)=>{
    if(!cfg?.token)throw Error();
    const response=await fetch(cfg.url+route,{method:'POST',credentials:'omit',cache:'no-store',headers:{'Content-Type':'application/json',Authorization:'Bearer '+cfg.token},body:JSON.stringify({...body,client:client.current})});
    if(!response.ok)throw Error();return response.json();
   },[cfg]);
   React.useEffect(()=>{
    if(!cfg?.token)return;
    let stopped=false,busy=false;
    const poll=async()=>{
     if(stopped||busy)return;busy=true;
     try{
      const otherModal=document.querySelector('[role="dialog"][aria-modal="true"]');
      const available=!!platform&&document.visibilityState==='visible'&&(!otherModal||active.current);
      const result=await send('/v1/ui/poll',{native:!!available,active:active.current});
      if(!stopped&&result.command&&!active.current){active.current=result.command.id;setFailed(false);setLoaded(false);setCommand(result.command);}
     }catch{}finally{busy=false;}
    };
    void poll();const timer=setInterval(poll,1000);
    return()=>{stopped=true;clearInterval(timer);};
   },[cfg,platform,send]);
   React.useLayoutEffect(()=>{
    if(!command)return;
    const own=viewport.current?.parentElement,previous=document.activeElement;
    const background=[...document.body.children].filter(e=>e!==own).map(e=>({element:e,inert:e.inert}));
    for(const {element} of background)element.inert=true;
    own?.querySelector('button')?.focus();
    return()=>{for(const {element,inert} of background)element.inert=inert;if(previous?.isConnected)previous.focus?.();};
   },[command]);
   React.useEffect(()=>{
    if(!command||!platform||!viewport.current)return;
    let disposed=false;
    const bounds=()=>{const r=viewport.current.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height};};
    void (async()=>{
     try{
      await send('/v1/ui/result',{id:command.id,state:'loading'});
      await platform.open('top-up',bounds());
      if(!disposed){setLoaded(true);await send('/v1/ui/result',{id:command.id,state:'opened'});}
     }catch{if(!disposed){setFailed(true);void send('/v1/ui/result',{id:command.id,state:'failed'}).catch(()=>{});}}
    })();
    const observer=new ResizeObserver(()=>{void platform.setBounds(bounds()).catch(()=>{});});observer.observe(viewport.current);
    return()=>{disposed=true;observer.disconnect();void platform.close().catch(()=>{});};
   },[command,platform,send]);
   if(!command)return null;
   const close=async()=>{
    if(closing.current)return;closing.current=true;
    try{await send('/v1/ui/result',{id:command.id,state:'returned'});}catch{}
    finally{active.current=null;setCommand(null);closing.current=false;}
   };
   return createPortal(h('section',{role:'dialog','aria-modal':true,'aria-label':'猫粮 · Harness 充值',style:{position:'fixed',inset:0,zIndex:2147483000,background:'var(--background-color,#f7fafb)',color:'var(--text-color,#25354a)'}},
    h('header',{style:{height:56,boxSizing:'border-box',display:'flex',alignItems:'center',padding:'8px 16px',gap:16}},
     h('button',{type:'button',onClick:close,style:{marginLeft:80,padding:'7px 12px',borderRadius:8}},'返回 DeepSeek Harness'),
     h('span',null,'补充猫粮 · 请在官方页面自行完成支付')),
    h('div',{ref:viewport,style:{position:'absolute',top:56,left:0,right:0,bottom:0}},
     !loaded&&h('p',{role:'status',style:{padding:24}},failed?'充值页面加载失败，请返回后使用官网入口。':'正在打开官方充值页面…'))),document.body);
  }
  return{inject:['slots'],apply(ctx){
   ctx.slots.inject('shell.overlay',()=>ctx.slots.register({name:'shell.overlay',id:'catfood-harness-topup',order:100},BridgeView));
  }};
 }
});
