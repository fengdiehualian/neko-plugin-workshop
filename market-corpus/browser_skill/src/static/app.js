const PLUGIN='browser_skill', RUNS='/runs';
const $=id=>document.getElementById(id);
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const controls=['refreshBtn','daemonBtn','closeBtn','saveBtn'];
let uiBusy=false, refreshInFlight=false, pluginReachable=true;
let configuredBskValue='', resolvedBundledPath='';

function unwrap(value){
  let raw=value;
  for(let i=0;i<6&&raw&&typeof raw==='object';i++){
    if(raw.success===true&&raw.data&&typeof raw.data==='object'){raw=raw.data;continue;}
    if(raw.data&&typeof raw.data==='object'&&Object.keys(raw).length<=3){raw=raw.data;continue;}
    break;
  }
  return raw||{};
}

async function call(entry,args={}){
  const started=await fetch(RUNS,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plugin_id:PLUGIN,entry_id:entry,args})});
  if(!started.ok)throw new Error(`启动失败 HTTP ${started.status}`);
  const info=await started.json(), id=info.run_id||info.id;
  if(!id)throw new Error('宿主没有返回 run_id');
  const deadline=Date.now()+50000;
  while(Date.now()<deadline){
    const poll=await fetch(`${RUNS}/${id}`);
    if(poll.ok){
      const state=await poll.json();
      if(state.status==='succeeded'){
        const exported=await fetch(`${RUNS}/${id}/export`);
        if(!exported.ok)throw new Error('无法读取插件返回值');
        const body=await exported.json(), items=body.items||[];
        const item=items.find(x=>x.type==='json'&&x.json)||items[0];
        return unwrap(item?.json||{});
      }
      if(['failed','canceled','timeout'].includes(state.status))throw new Error(state.error?.message||state.message||state.status);
    }
    await wait(300);
  }
  throw new Error('插件操作超时');
}

async function readContext(){
  const url=`/plugin/${PLUGIN}/hosted-ui/context?kind=panel&id=main&_=${Date.now()}`;
  const response=await fetch(url,{cache:'no-store'});
  if(!response.ok)throw new Error(`读取状态失败 HTTP ${response.status}`);
  const body=await response.json();
  return unwrap(body.state||{});
}

function busy(on){uiBusy=on;controls.forEach(id=>$(id).disabled=on||!pluginReachable);}
function notice(message,good=true){const el=$('notice');el.hidden=false;el.className=`notice ${good?'good':'bad'}`;el.textContent=message;}
function markPluginOffline(){
  pluginReachable=false;
  controls.forEach(id=>$(id).disabled=true);
  $('readyBadge').textContent='插件未启动';
  $('readyBadge').className='badge bad';
  $('browserState').textContent='等待插件启动';
  $('effectiveRoute').textContent='—';
  notice('BrowserSkill 插件未启动或正在重启。启动插件后，本页面会自动恢复连接。',false);
}
function routeText(mode){return {auto:'自动',native:'原生工具',fallback:'后备路由',hybrid:'双通道'}[mode]||mode||'—';}
function setValue(id,value){const el=$(id);if(el.type==='checkbox')el.checked=Boolean(value);else el.value=value??'';}

function render(data,{applySettings=true}={}){
  if(!pluginReachable){$('notice').hidden=true;}
  pluginReachable=true;
  controls.forEach(id=>$(id).disabled=uiBusy);
  const availability=data.availability||{}, settings=data.settings||{}, routing=data.routing||{}, cli=data.cli||{}, browsers=availability.browsers||[];
  $('readyBadge').textContent=availability.ready?'已连接':'需要设置';
  $('readyBadge').className=`badge ${availability.ready?'good':'bad'}`;
  $('cliVersion').textContent=availability.version
    ? `bsk CLI ${availability.version}${cli.bundled_selected?' · 内置':''}`
    : cli.bundled_selected
      ? `bsk CLI ${cli.bundled_version||'0.1.10'} · 内置`
      : '未检测到';
  $('browserState').textContent=browsers.length?`${browsers.length} 个在线`:(availability.reasons||[]).join(', ')||'未连接';
  $('effectiveRoute').textContent=routeText(routing.effective);
  const owner=data.tasks?.control_owner;
  $('taskCount').textContent=`${String(data.tasks?.background_count||0)}${owner?` · ${owner==='user'?'用户控制':'Agent 控制'}`:''}`;
  const usage=data.tasks?.token_usage||{}, total=Number(usage.total_tokens||0), calls=Number(usage.calls||0), estimated=Number(usage.estimated_calls||0);
  $('tokenCount').textContent=total?`${total} · ${calls} 轮${estimated?` · ${estimated} 轮估算`:''}`:'—';
  $('modelInfo').textContent=`主对话模型：${routing.conversation_model||'未配置'} · ${routing.conversation_endpoint_host||'未知端点'} · 原生能力判定：${routing.native_route_supported?'支持':'不确定'}`;
  $('routeHint').textContent=routing.requested==='auto'
    ? `自动模式当前采用“${routeText(routing.effective)}”。不确定线路会同时提供原生和后备入口，插件会去重，只有一条浏览队列。`
    : `手动模式“${routeText(routing.requested)}”已生效。原生工具：${routing.native_tool_registered?'开':'关'}；后备入口：${routing.fallback_registered?'开':'关'}。`;
  const map={bskExecutable:'bsk_executable',browserLabel:'browser_label',routingMode:'routing_mode',autoStartDaemon:'auto_start_daemon',sessionScope:'session_scope',reuseWindow:'reuse_existing_window',releaseIdleControl:'release_control_when_idle',allowExtraTabs:'allow_additional_agent_tabs',maxSteps:'max_steps',activeTimeout:'active_timeout_seconds',duplicateSeconds:'duplicate_suppression_seconds',keepaliveSeconds:'session_keepalive_seconds',plannerTokens:'planner_max_completion_tokens',correctionTokens:'planner_correction_max_completion_tokens',snapshotTokens:'snapshot_max_tokens',scrollMaxPages:'scroll_max_pages',scrollTokens:'scroll_snapshot_max_tokens',scrollSettleMs:'scroll_settle_ms',livePageChars:'live_page_max_chars',allowTabBorrow:'allow_tab_borrow',visionFallback:'enable_vision_fallback',keepMedia:'keep_session_for_media',debugLogging:'debug_logging'};
  if(applySettings){
    Object.entries(map).forEach(([id,key])=>setValue(id,settings[key]));
    configuredBskValue=String(settings.bsk_executable??'');
    resolvedBundledPath=String(cli.resolved_path||'');
    if(cli.bundled_selected)setValue('bskExecutable',resolvedBundledPath);
  }
  const list=$('browserList');list.replaceChildren();
  if(!browsers.length){const p=document.createElement('p');p.className='muted';p.textContent='暂无浏览器信息';list.append(p);}
  browsers.forEach(browser=>{const row=document.createElement('div');row.className='browser-card';const left=document.createElement('div');const strong=document.createElement('strong');strong.textContent=`${browser.browser_name||'browser'} ${browser.browser_version||''}`;const small=document.createElement('small');small.textContent=`${browser.label||browser.instance_id||'未命名'} · 扩展 ${browser.extension_version||'未知'}`;left.append(strong,document.createElement('br'),small);const right=document.createElement('small');right.textContent=browser.version_skew?'版本不匹配':`${browser.session_count||0} 个会话`;row.append(left,right);list.append(row);});
  const debug=data.debug||{}, events=Array.isArray(debug.events)?debug.events:[];
  $('debugEvents').textContent=debug.enabled
    ? (events.length?events.map(item=>`[${item.time||'--:--:--'}] ${item.message||''}`).join('\n'):'尚无 Debug 事件；执行或查询一次浏览器任务后会显示。')
    : 'Debug 日志已关闭。';
}

function bskSettingValue(){
  const displayed=$('bskExecutable').value.trim();
  return configuredBskValue.toLowerCase()==='bundled'&&displayed===resolvedBundledPath?'bundled':displayed;
}
function formSettings(){return {
  bsk_executable:bskSettingValue(),browser_label:$('browserLabel').value.trim(),routing_mode:$('routingMode').value,auto_start_daemon:$('autoStartDaemon').checked,session_scope:$('sessionScope').value,reuse_existing_window:$('reuseWindow').checked,release_control_when_idle:$('releaseIdleControl').checked,allow_additional_agent_tabs:$('allowExtraTabs').checked,
  max_steps:Number($('maxSteps').value),active_timeout_seconds:Number($('activeTimeout').value),duplicate_suppression_seconds:Number($('duplicateSeconds').value),session_keepalive_seconds:Number($('keepaliveSeconds').value),planner_max_completion_tokens:Number($('plannerTokens').value),planner_correction_max_completion_tokens:Number($('correctionTokens').value),snapshot_max_tokens:Number($('snapshotTokens').value),scroll_max_pages:Number($('scrollMaxPages').value),scroll_snapshot_max_tokens:Number($('scrollTokens').value),scroll_settle_ms:Number($('scrollSettleMs').value),live_page_max_chars:Number($('livePageChars').value),
  allow_tab_borrow:$('allowTabBorrow').checked,enable_vision_fallback:$('visionFallback').checked,keep_session_for_media:$('keepMedia').checked,debug_logging:$('debugLogging').checked
};}

async function run(entry,args,message){busy(true);try{const data=await call(entry,args);if(data.success===false)throw new Error(data.error?.message||'操作失败');render(data);notice(data.message||message,true);}catch(error){notice(error.message||String(error),false);}finally{busy(false);}}
async function refreshSilently(){
  if(uiBusy||refreshInFlight)return;
  refreshInFlight=true;
  try{
    const data=await readContext();
    if(data.success!==false)render(data,{applySettings:false});
  }catch(_error){
    markPluginOffline();
  }finally{refreshInFlight=false;}
}
$('refreshBtn').onclick=()=>run('browser_skill_control',{action:'refresh'},'状态已刷新');
$('daemonBtn').onclick=()=>run('browser_skill_control',{action:'start_daemon'},'daemon 已启动');
$('closeBtn').onclick=()=>run('browser_skill_control',{action:'close_sessions'},'任务与会话已关闭');
$('saveBtn').onclick=()=>run('save_browser_skill_settings',{settings:formSettings()},'设置已保存');
readContext().then(data=>render(data)).catch(()=>markPluginOffline());
setInterval(refreshSilently,1000);
