const panel=document.getElementById('accountPanel');
panel.innerHTML=`<header><div><small>YUI · 猫粮</small><h2>猫粮额度设置</h2></div><button id="closeAccount" type="button" aria-label="返回人物">×</button></header>
<p class="note">选择自动跟随，或固定一种猫粮喵～</p>
<section id="trackingSettings" aria-label="猫粮监测方式"><h3>监测方式</h3><label>监测方式<select id="displayMode"><option value="auto">自动跟随前台应用 / 页面</option><option value="fixed">始终监测指定猫粮</option></select></label><label id="fixedProfileLabel">指定猫粮<select id="fixedProfile"></select></label><p id="trackingHint" role="status" class="note"></p><p class="note">按平台自动显示余额、积分或 Token；有真实余额时优先显示余额。</p><details class="note"><summary>额度来源说明</summary><p>各平台余额独立，不合并。自动识别后沿用对应平台的查询来源；手动记录不会变成实时余额。浏览器只读取已适配的官方余额字段，不读取对话或输入框。</p></details></section>
<label>查找模型或平台<input id="profileSearch" type="search" placeholder="例如 WorkBuddy、TRAE、Kimi、Qwen"></label><div id="quotaCards"></div>
<section id="accountEditor" hidden><form id="accountForm"><div class="accountEditorHeader"><h3 id="editingName">账户设置</h3><button id="closeEditor" type="button">收起设置</button></div><p id="sourceHint" class="note"></p><button id="accountTopup" type="button">补充猫粮 ↗</button>
<label>模型备注<input id="model" maxlength="100" placeholder="可填写具体模型或套餐名称"></label>
<label id="keyLabel">查询密钥（留空保留此平台的密钥）<input id="accountKey" type="password" autocomplete="new-password"></label>
<div id="bridgeFields"><p class="note">在 DeepSeek Harness 安装并启用「猫粮余额桥接」后，保持 Harness 运行，即可读取当前登录账户的充值余额和赠金。无需复制 API Key 或登录凭据。应用内充值由 Harness 的原生窗口处理。</p><p id="bridgeWallets" class="note"></p></div>
<div id="desktopFields"><label>积分来源<select id="quota_mode"><option value="auto">自动读取本机用量页（macOS）</option><option value="manual">手动校正积分</option></select></label><p id="desktopHint" class="note">在对应应用打开积分用量页后刷新。自动读取需要系统辅助功能授权给猫粮额度读取组件，只读取额度面板，不自动打开应用或签到。面板关闭、应用未运行或权限不足时保留上次读数，不当成 0。WorkBuddy 的面板兼容性尚待真实客户端验证。</p><label id="desktopTotalLabel">猫粮参考总积分（可选）<input id="desktop_total" type="number" min="0" max="1000000000000" step="any"><small>总积分由你设定，用于脸色与追逐比例；不把历史已过期积分计为本期总额。</small></label></div>
<div id="aliyunFields"><label>阿里云 CLI 配置名（可选）<input id="aliyun_profile" maxlength="80" placeholder="留空使用默认配置"></label><p class="note">需要已安装并登录阿里云官方 CLI，RAM 身份仅需 bss:DescribeAcccount 查询权限。此处显示账户共享可用额度（可能含授信），并非 Qwen 聊天积分或百炼某模型免费 Token；不需要填写 DashScope 模型 Key。</p></div>
<div id="customFields"><label>余额接口（HTTPS GET）<input id="endpoint" type="url"></label><label>金额字段路径<input id="value_path" placeholder="data.balance"></label><p class="note">密钥仅以 Bearer 认证发给此地址，请确认服务商。</p></div>
<div id="quotaFields"><p class="note">此来源尚未接入自动额度查询。按平台页面填写；不会自动扣减或重置。填写 Token 总额度或自设预算后，YUI 会在剩余 75%、50%、25% 时逐渐阴沉喵～</p><label>剩余额度<input id="quota_remaining" type="number" min="0" max="1000000000000" step="any"></label><label>猫粮总额度／预算（可选，同单位）<input id="quota_total" type="number" min="0" max="1000000000000" step="any"></label><label>单位<select id="quota_unit"><option>次</option><option>token</option><option>%</option></select></label><label>重置时间备注（可选）<input id="quota_reset" maxlength="80" placeholder="例如：周五 20:00"></label></div>
<label id="balanceTotalLabel">猫粮参考总额（可选，同币种）<input id="balance_total" type="number" min="0" max="1000000000000" step="any"><small>用于计算剩余比例和 YUI 脸色，不会自动扣减。</small></label>
<div id="budgetFields"><label>预算金额<input id="budget" type="number" min="0" max="1000000000" step="any"></label><label id="dateLabel">预算起始日期（UTC）<input id="budget_start" type="date"></label></div>
<label id="currencyLabel">币种<select id="currency"><option>CNY</option><option>USD</option></select></label>
<details id="estimateFields"><summary>估算与提醒</summary><label>每百万 token 综合价格（选填）<input id="price_per_million" type="number" min="0.000001" step="any"></label><label>低余额提醒阈值<input id="low_balance" type="number" min="0" step="any"></label><label><input id="alerts" type="checkbox"> 低余额提醒喵～</label></details><label id="intervalLabel">查询间隔（秒）<input id="interval" type="number" min="60" max="86400"></label>
<button id="saveAccount" class="primary" type="submit">保存设置</button><p id="accountFeedback" role="status"></p><p class="note">密钥保存在本机。API 预算、订阅额度和账户余额是不同指标；共享账户金额不代表某个模型的独立额度。</p></form></section>`;
const field=id=>document.getElementById(id);
const creditOption=document.createElement('option');creditOption.textContent='积分';field('quota_unit').append(creditOption);
// The per-account reminder switch also applies to points and manual quota sources.
field('estimateFields').after(field('alerts').closest('label'));
const advanced=document.createElement('details');advanced.id='accountAdvanced';
const advancedTitle=document.createElement('summary');advancedTitle.textContent='高级设置与数据说明';advanced.append(advancedTitle);
for(const node of [field('sourceHint'),field('model').closest('label'),field('customFields'),field('estimateFields'),field('intervalLabel')])advanced.append(node);
field('saveAccount').before(advanced);
field('closeEditor').onclick=()=>{field('accountEditor').hidden=true;panel.classList.remove('editing-account');panel.scrollTop=0;};
const configFields=['model','endpoint','value_path','budget','balance_total','budget_start','currency','price_per_million','low_balance','interval','quota_remaining','quota_total','quota_unit','quota_reset','quota_mode','aliyun_profile'];
let accountCsrf='',profileRows=[],editingProfile='',cardsKey='';
const openSourceDetails=new Set();
async function setTracking(values){
 const response=await fetch('/api/tracking',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':accountCsrf},body:JSON.stringify(values)});
 const data=await response.json();if(!response.ok)throw Error(data.error||'监测方式保存失败');drawCards(data);
 if(typeof update==='function')await update();return data;
}
field('displayMode').onchange=async()=>{try{await setTracking({mode:field('displayMode').value,profile:field('fixedProfile').value});}catch(e){field('trackingHint').textContent=e.message;}};
field('fixedProfile').onchange=async()=>{try{await setTracking({mode:'fixed',profile:field('fixedProfile').value});}catch(e){field('trackingHint').textContent=e.message;}};
function desktopVisibility(){const row=profileRows.find(r=>r.id===editingProfile),desktop=row?.source==='desktop_quota';field('quotaFields').hidden=desktop?field('quota_mode').value!=='manual':row?.source!=='manual_quota';field('desktopTotalLabel').hidden=!desktop||field('quota_mode').value==='manual';}
field('quota_mode').onchange=()=>{if(field('quota_mode').value==='manual')field('quota_total').value=field('desktop_total').value;else field('desktop_total').value=field('quota_total').value;desktopVisibility();};
field('accountTopup').onclick=()=>window.CatfoodTopUp.open(editingProfile);
function quotaSummary(s){return window.CatfoodQuotaDisplay.present(s).amount;}
function fillProfile(id){
 const row=profileRows.find(r=>r.id===id);if(!row)return;editingProfile=id;const c=row.snapshot.config,s=c.source;
 field('editingName').textContent=row.name+' · 设置';field('sourceHint').textContent=row.hint+({openai:'。需要组织 Admin Key；非 ChatGPT 订阅额度。',anthropic:'。需要组织费用查询权限；不含 Priority Tier。',codex:'。读取本机已登录的 Codex，无需密钥。',openrouter:'。需要 Management Key；不是普通聊天 Key。',jev:'。仅本机 JEV 桥接上报，不代表完整账户用量。'}[s]||'');
 for(const id of configFields)field(id).value=c[id]??'';field('alerts').checked=c.alerts;field('accountKey').value='';
 field('keyLabel').hidden=['codex','jev','manual','manual_quota','desktop_quota','aliyun','app_bridge'].includes(s);field('customFields').hidden=s!=='custom';field('quotaFields').hidden=s!=='manual_quota';field('budgetFields').hidden=!['openai','anthropic','manual'].includes(s);field('dateLabel').hidden=s==='manual';field('balanceTotalLabel').hidden=['codex','jev','manual_quota','desktop_quota','openai','anthropic'].includes(s);
 field('bridgeFields').hidden=s!=='app_bridge';field('bridgeWallets').textContent=Object.entries(row.snapshot.wallet_breakdown||{}).map(([currency,w])=>`${currency} · 充值余额 ${Number(w.normal).toLocaleString('zh-CN',{maximumFractionDigits:8})} · 赠金 ${Number(w.bonus).toLocaleString('zh-CN',{maximumFractionDigits:8})}`).join(' ｜ ');
 field('desktopFields').hidden=s!=='desktop_quota';field('desktopHint').textContent=row.id==='trae'?'打开或切回已登录的 TRAE 后自动读取积分；会短暂进入用量页，读取后返回原任务，无需一直打开。需要猫粮额度读取与导航组件的辅助功能权限；不会签到或操作支付。应用未运行或读取失败时保留上次数据。':'在 WorkBuddy 中打开积分用量页后刷新，需要辅助功能权限。面板关闭或读取失败时保留上次数据；真实客户端兼容性尚待验证。';field('aliyunFields').hidden=s!=='aliyun';field('desktop_total').value=c.quota_total??'';field('quota_mode').value=c.quota_mode||'auto';desktopVisibility();
 const nonMoney=['codex','jev','manual_quota','desktop_quota'].includes(s);field('currencyLabel').hidden=nonMoney;field('estimateFields').hidden=nonMoney;field('intervalLabel').hidden=['jev','manual','manual_quota'].includes(s);
 field('currency').disabled=['openai','anthropic','openrouter','siliconflow','moonshot_cn','moonshot_global'].includes(s);if(['openai','anthropic','openrouter','moonshot_global'].includes(s))field('currency').value='USD';if(['siliconflow','moonshot_cn'].includes(s))field('currency').value='CNY';
 if(['moonshot_cn','moonshot_global'].includes(s))field('sourceHint').textContent+='。使用对应站点开放平台 API Key，会员和 Kimi Code Key 不通用。';
 field('accountFeedback').textContent='';advanced.open=s==='custom';
 field('accountTopup').disabled=!row.topup?.entries?.length;field('accountTopup').title=row.topup?.entries?.length?'打开此平台的官方充值或套餐入口':row.topup?.hint||'尚无已核验的官方入口';
}
function drawCards(state){
 if(state.csrf)accountCsrf=state.csrf;
 profileRows=state.profiles||[];const container=field('quotaCards');
 const tracking=state.tracking||{mode:'fixed',fixed_profile:state.profile};
 field('displayMode').value=tracking.mode;field('fixedProfileLabel').hidden=tracking.mode!=='fixed';field('trackingHint').textContent=tracking.hint||'';
 const choices=field('fixedProfile'),signature=profileRows.map(row=>row.id+':'+row.enabled).join('|');if(choices.dataset.signature!==signature){choices.replaceChildren();for(const row of profileRows){const option=document.createElement('option');option.value=row.id;option.textContent=row.name+(row.enabled?'':' · 未接入');choices.append(option);}choices.dataset.signature=signature;}choices.value=tracking.fixed_profile;
 const nextKey=JSON.stringify([profileRows,state.profile,tracking.mode,state.reported_usage]);if(nextKey===cardsKey)return;cardsKey=nextKey;container.replaceChildren();
 for(const row of [...profileRows].sort((a,b)=>(b.id===state.profile)-(a.id===state.profile)||Number(b.enabled)-Number(a.enabled))){
  if(row.source==='jev')row.snapshot.reported_usage=state.reported_usage;
  const card=document.createElement('article');card.className='quota-card';card.classList.toggle('selected',state.profile===row.id);
  const name=document.createElement('strong');name.textContent=row.name;
  const amount=document.createElement('b');amount.textContent=row.enabled?quotaSummary(row.snapshot):'未接入';
  const note=document.createElement('small');const manual=row.source==='manual_quota'||row.source==='desktop_quota'&&row.snapshot.config.quota_mode==='manual';note.textContent=(row.snapshot.page_observation?(row.snapshot.stale?'上次页面余额 · 充值与赠送合计':'当前页面余额 · 充值与赠送合计'):row.source==='desktop_quota'?(manual?'手动校正积分 · 不自动扣减':row.id==='trae'?'打开 TRAE 自动读取 · 读后返回任务':'本机用量页读取 · macOS · 需保持额度页可读取'):row.hint)+(row.enabled&&row.snapshot.error?' · 查询失败，保留旧数据':row.enabled&&row.snapshot.stale&&!manual&&row.source!=='jev'?' · 等待更新':'');
  const stamp=document.createElement('small');stamp.textContent=row.snapshot.updated_at?'更新于 '+new Date(row.snapshot.updated_at*1000).toLocaleString('zh-CN'):'';
  const edit=document.createElement('button');edit.type='button';edit.textContent=row.enabled?'设置':'接入 / 记录';edit.onclick=()=>{fillProfile(row.id);field('accountEditor').hidden=false;panel.classList.add('editing-account');panel.scrollTop=0;};
  card.append(name,amount);
  const snap=row.snapshot,c=snap.config;
  const q=window.CatfoodQuotaMood.quota(snap);
  const percentages=c.source==='codex'?(snap.allowance?.windows||snap.codex_quota?.windows||[]).filter(w=>!snap.allowance||w.status==='active').map(w=>({label:w.label,value:Number(w.remaining),reset:'重置 '+new Date(w.resets_at*1000).toLocaleString('zh-CN')})):q?[{label:manual?'手动剩余':q.basis,value:q.percent,reset:c.quota_reset?'重置备注 '+c.quota_reset:''}]:[];
  if(row.enabled)for(const q of percentages){const label=document.createElement('small');label.textContent=q.label+' · '+q.value.toFixed(1)+'%';const bar=document.createElement('progress');bar.max=100;bar.value=q.value;bar.setAttribute('aria-label',q.label+'剩余百分比');card.append(label,bar);if(q.reset){const reset=document.createElement('small');reset.textContent=q.reset;card.append(reset);}}
  const sourceInfo=document.createElement('details'),sourceTitle=document.createElement('summary');sourceTitle.textContent='来源与更新时间';sourceInfo.open=openSourceDetails.has(row.id);sourceInfo.ontoggle=()=>{if(sourceInfo.open)openSourceDetails.add(row.id);else openSourceDetails.delete(row.id);};sourceInfo.append(sourceTitle,note,stamp);appendAllowanceDetails(sourceInfo,row);card.append(sourceInfo,edit);
  const topup=document.createElement('button');topup.type='button';topup.textContent='补充猫粮 ↗';topup.disabled=!row.topup?.entries?.length;topup.title=topup.disabled?(row.topup?.hint||'尚无已核验的官方入口'):'官方充值或套餐入口';topup.onclick=()=>window.CatfoodTopUp.open(row.id,row.name);card.append(topup);
  const show=document.createElement('button');show.type='button';show.textContent=tracking.mode==='fixed'&&state.profile===row.id?'已固定监测':'固定监测';show.onclick=async()=>{try{await setTracking({mode:'fixed',profile:row.id});}catch(e){field('accountFeedback').textContent=e.message;}};card.append(show);
  card.dataset.search=(row.name+' '+row.id).toLowerCase();card.hidden=!card.dataset.search.includes(field('profileSearch').value.trim().toLowerCase());container.append(card);
 }
}
function appendAllowanceDetails(details,row){
 const a=row.snapshot.allowance;if(a?.schema_version!==1)return;
 const facts=[a.status_label+' · '+a.source_label,a.account_scope.label+' · '+a.account_scope.id.slice(0,8),a.basis,
  a.observed_at?'观测于 '+new Date(a.observed_at*1000).toLocaleString('zh-CN'):'观测时间未知',
  a.checked_at?'最近尝试 '+new Date(a.checked_at*1000).toLocaleString('zh-CN'):'',
  a.percent_basis==='configured_total'?'比例相对用户自设总量':a.percent_basis==='configured_budget'?'比例相对用户自设预算':'',
  a.alternatives?.some(v=>v.decision==='separate_account_scope')?'其他来源未核验为同一账户，未合并或替换':'',a.audit_error||''];
 for(const text of facts.filter(Boolean)){const p=document.createElement('small');p.textContent=text;details.append(p);}
 const button=document.createElement('button');button.type='button';button.textContent='查看读数记录';
 const records=document.createElement('div');records.className='allowanceAudit';records.hidden=true;
 button.onclick=async()=>{
  if(!records.hidden){records.hidden=true;button.textContent='查看读数记录';return;}
  button.disabled=true;
  try{
   const r=await fetch('/api/allowance?profile='+encodeURIComponent(row.id));if(!r.ok)throw Error('读数记录暂不可用');const data=await r.json();records.replaceChildren();
   const intro=document.createElement('p');intro.textContent='仅显示当前来源档案最近 30 条记录；不是消费流水。';records.append(intro);
   if(!data.observations?.length){const p=document.createElement('p');p.textContent='尚无有效读数记录';records.append(p);}
   for(const item of data.observations||[]){const p=document.createElement('p');p.textContent=(item.observed_at?new Date(item.observed_at*1000).toLocaleString('zh-CN'):'时间未知')+' · '+item.value+' '+item.unit+' · '+item.source_label+(item.metric==='usage_total'?' · 已用量':'');records.append(p);}
   const decisions=(data.decisions||[]).filter(v=>v.stage==='selection').slice(0,5);
   if(decisions.length){const p=document.createElement('p');p.textContent='最近数据状态：'+decisions.map(v=>({current:'当前',stale:'上次',expired:'过期',unknown:'未知',manual:'手动',conflict:'冲突',invalid:'无效'}[v.status]||v.status)).join(' ← ');records.append(p);}
   records.hidden=false;button.textContent='收起读数记录';
  }catch(e){records.textContent=e.message;records.hidden=false;}finally{button.disabled=false;}
 };details.append(button,records);
}
field('profileSearch').oninput=()=>{for(const card of field('quotaCards').children)card.hidden=!card.dataset.search.includes(field('profileSearch').value.trim().toLowerCase());};
window.addEventListener('catfood:statusrefresh',e=>drawCards(e.detail));
window.openAccount=async()=>{if(typeof nativeHideTimer!=='undefined'){clearTimeout(nativeHideTimer);nativeHideTimer=null;}panel.hidden=false;field('accountEditor').hidden=true;panel.classList.remove('editing-account');try{const r=await fetch('/api/status');if(!r.ok)throw Error();const s=await r.json();accountCsrf=s.csrf;drawCards(s);fillProfile(s.profile);}catch{field('accountFeedback').textContent='插件未连接，请稍后重试喵～';}};
field('closeAccount').onclick=()=>{panel.hidden=true;if(typeof native==='function'&&typeof recordingActive!=='undefined'&&!recordingActive)native('hide');};
field('accountForm').onsubmit=async e=>{e.preventDefault();field('saveAccount').disabled=true;try{const body={profile:editingProfile,api_key:field('accountKey').value,alerts:field('alerts').checked};for(const id of configFields)body[id]=field(id).value;if(profileRows.find(r=>r.id===editingProfile)?.source==='desktop_quota'&&body.quota_mode==='auto')body.quota_total=field('desktop_total').value;const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-Neko-CSRF':accountCsrf},body:JSON.stringify(body)});const s=await r.json();if(!r.ok)throw Error(s.error||'保存失败');field('accountKey').value='';drawCards(s);const edited=s.profiles?.find(p=>p.id===editingProfile)?.snapshot;field('accountFeedback').textContent=edited?.error?'已保存；'+edited.error:'已保存；'+(s.tracking?.mode==='auto'?'继续自动跟随前台喵～':'已固定监测喵～');if(typeof update==='function')update();}catch(e){field('accountFeedback').textContent=e.message;}finally{field('saveAccount').disabled=false;}};
setInterval(async()=>{if(panel.hidden||!accountCsrf)return;try{const r=await fetch('/api/status');if(r.ok)drawCards(await r.json());}catch{}},2500);
