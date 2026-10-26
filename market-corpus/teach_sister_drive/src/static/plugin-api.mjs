let token = null;
async function csrfHeaders() {
  if (token) return {'X-CSRF-Token':token};
  const response = await fetch('/security/csrf-token',{credentials:'same-origin',signal:AbortSignal.timeout(5000)});
  if (response.status === 404) throw new Error('此插件需要新版 N.E.K.O，当前 Steam 版不受支持。');
  if (!response.ok) throw new Error('插件服务暂时不可用，请稍后重试。');
  const data = await response.json();
  if (!data.csrf_token) throw new Error('未取得插件操作凭据，请刷新面板。');
  token = data.csrf_token;
  return {'X-CSRF-Token':token};
}

async function postJson(url,body) {
  let response;
  for (let attempt=0;attempt<2;attempt++) {
    response = await fetch(url,{method:'POST',credentials:'same-origin',signal:AbortSignal.timeout(15000),
      headers:{'Content-Type':'application/json',...await csrfHeaders()},
      body:JSON.stringify(body)});
    if (response.status !== 403 || attempt) break;
    const problem=await response.clone().json().catch(()=>null);
    if (response.headers.get('X-CSRF-Failure') !== 'token' && problem?.detail?.csrf_failure !== 'token') break;
    token=null;
  }
  return response;
}

export async function callPlugin(entry,args={},timeoutMs=125000,started=false) {
  const response=await postJson('/runs',{plugin_id:'teach_sister_drive',entry_id:entry,args});
  if (!response.ok) throw new Error(`无法调用插件（HTTP ${response.status}），请确认插件已启用。`);
  const created=await response.json(),id=created.run_id || created.id;
  if (!id) throw new Error('插件没有返回任务编号，请重试。');
  const deadline=Date.now()+timeoutMs;
  while(Date.now()<deadline) {
    await new Promise(resolve=>setTimeout(resolve,250));
    const status=await fetch(`/runs/${encodeURIComponent(id)}`,{signal:AbortSignal.timeout(10000)});
    if (!status.ok) throw new Error('暂时无法读取安装任务，请稍后重新检测游戏状态。');
    const run=await status.json();
    if (run.status === 'succeeded') {
      const exported=await fetch(`/runs/${encodeURIComponent(id)}/export`,{signal:AbortSignal.timeout(10000)});
      if (!exported.ok) throw new Error('任务已结束，但结果尚未取得；请重新检测游戏状态。');
      const result=await exported.json();
      const item=(result.items || []).find(item=>item.type==='json' && item.json);
      const payload=item?.json;
      if (!payload) throw new Error('插件任务没有返回安装结果，请重新检测游戏状态。');
      if (payload.success === false || payload.error) throw new Error(payload.error?.message || payload.error || '插件操作失败');
      return payload.data || {};
    }
    if (run.status==='failed' && run.error?.code==='NOT_READY' && !started) {
      // NOT_READY proves the entry never ran, so starting our own plugin and
      // retrying once cannot duplicate an installation. No other task retries.
      const start=await postJson('/plugin/teach_sister_drive/start',{});
      if(!start.ok)throw new Error('无法启动安装服务，请在插件管理器中启用「教妹妹开车」。');
      const result=await start.json();
      if(result.success===false || result.ok===false)throw new Error(result.message || '安装服务启动失败，请在插件管理器查看状态。');
      return callPlugin(entry,args,timeoutMs,true);
    }
    if (['failed','canceled','cancelled','timeout'].includes(run.status))
      throw new Error(run.error?.message || run.message || '插件任务未完成，请重新检测游戏状态。');
  }
  throw new Error('等待插件任务超时；请重新检测游戏状态后再决定是否重试。');
}
