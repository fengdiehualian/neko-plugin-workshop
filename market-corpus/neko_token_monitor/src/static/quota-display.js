/* Select the provider's native unit; estimates never replace a real balance. */
(function(root){
 'use strict';
 const number=value=>typeof value==='number'?Number.isFinite(value)?value:null:typeof value==='string'&&value.trim()!==''&&Number.isFinite(Number(value))?Number(value):null;
 const compact=value=>new Intl.NumberFormat('zh-CN',{notation:Math.abs(value)>=10000?'compact':'standard',maximumFractionDigits:1}).format(value);
 const percent=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1}).format(value)+'%';
 function present(state={}){
  const c=state.config||{},model=state.profiles?.find(p=>p.id===state.profile)?.name||c.model||'猫粮';
  const view={amount:'未连接',detail:model+' 额度剩余',title:'',token:false,connection:state.error?'查询失败，保留上次结果':state.stale?'上次数据，等待更新':''};
  const a=state.allowance;
  if(a?.schema_version===1){
   const v=number(a.value),old=['stale','expired','invalid','conflict'].includes(a.status);
   view.connection=old?a.status_label:'';view.title=a.basis||'';
   if(v===null)return {...view,amount:a.status==='expired'?'待更新':a.status==='invalid'?'待读取':state.configured?'待查询':'未连接'};
   if(a.metric==='usage_total')return {...view,amount:compact(v)+' token',detail:model+' 已上报用量',token:true};
   if(['money_balance','budget_remaining'].includes(a.metric)){
    const prefix=a.unit==='USD'?'$':a.unit==='CNY'?'¥':a.unit;
    return {...view,amount:prefix+' '+v.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:v!==0&&Math.abs(v)<.01?Math.max(2,Math.min(18,(String(a.value).split('.')[1]||'').length)):2}),detail:model+(a.metric==='budget_remaining'?' 预算剩余':' 账户余额')};
   }
   return {...view,amount:a.unit==='%'?percent(v):(a.unit==='token'?compact(v):v.toLocaleString('zh-CN',{maximumFractionDigits:2}))+' '+a.unit,detail:c.source==='codex'?a.basis:view.detail,token:a.unit==='token'};
  }
  if(c.source==='desktop_quota'&&c.quota_mode!=='manual'){
   const value=number(state.account_quota?.remaining);
   return {...view,amount:value!==null?value.toLocaleString('zh-CN',{maximumFractionDigits:2})+' 积分':'待读取'};
  }
  if(c.source==='manual_quota'||c.source==='desktop_quota'){
   const value=number(c.quota_remaining),unit=c.quota_unit||'次',token=unit.toLowerCase()==='token';
   return {...view,amount:value!==null?(token?compact(value):value.toLocaleString('zh-CN'))+' '+unit:'未填写',token,title:value!==null?value.toLocaleString('zh-CN')+' '+unit:''};
  }
  if(c.source==='codex'){
   const windows=(state.codex_quota?.windows||[]).filter(w=>number(w.remaining)!==null&&number(w.remaining)>=0&&number(w.remaining)<=100);
   const w=[...windows].sort((a,b)=>number(a.remaining)-number(b.remaining))[0];
   return {...view,amount:w?percent(number(w.remaining)):'待查询',detail:w?model+' '+w.label+'剩余':view.detail};
  }
  if(c.source==='jev'){
   const u=state.reported_usage||{},input=number(u.input_tokens),output=number(u.output_tokens);
   const known=input!==null||output!==null,value=(input||0)+(output||0);
   return {...view,amount:known?compact(value)+' token':'未提供',detail:model+' 已上报用量',token:known,title:known?value.toLocaleString('zh-CN')+' token 已用；不是账户剩余':''};
  }
  const row=state.selected,balance=number(row?.balance);
  if(balance!==null){
   const currency=row.currency||c.currency||'CNY',prefix=currency==='USD'?'$':currency==='CNY'?'¥':currency;
   return {...view,amount:prefix+' '+balance.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:balance!==0&&Math.abs(balance)<0.01?8:2}),detail:model+(['openai','anthropic','manual'].includes(c.source)?' 额度剩余':' 账户余额')};
  }
  const estimate=number(state.estimated_tokens);
  if(estimate!==null&&estimate>=0&&!state.stale)return {...view,amount:'≈ '+compact(estimate)+' token',detail:model+' 预计 Token 剩余',title:'估算 '+estimate.toLocaleString('zh-CN')+' token',token:true};
  return {...view,amount:state.configured?'待查询':'未连接'};
 }
 const api={present};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.CatfoodQuotaDisplay=api;
})(typeof window==='object'?window:globalThis);
