/* Account quota only: context length and locally observed usage are not balances. */
(function(root){
 'use strict';
 const number=value=>value!==null&&value!==''&&typeof value!=='boolean'&&Number.isFinite(Number(value))?Number(value):null;
 function quota(state){
  const a=state?.allowance;
  if(a?.schema_version===1){
   const p=number(a.percent);
   return p!==null&&a.value!==null?{percent:p,basis:a.basis,stale:!a.can_alert}:null;
  }
  const c=state?.config||{};
  if(c.source==='codex'){
   const windows=(state.codex_quota?.windows||[]).filter(w=>number(w.remaining)!==null&&number(w.remaining)>=0&&number(w.remaining)<=100);
   const w=[...windows].sort((a,b)=>a.remaining-b.remaining)[0];
   return w?{percent:Number(w.remaining),basis:'Codex '+w.label+'剩余',stale:!!state.stale}:null;
  }
  if(c.source==='desktop_quota'&&c.quota_mode!=='manual'){
   const remaining=number(state.account_quota?.remaining),total=number(c.quota_total);
   return remaining!==null&&remaining>=0&&total>0?{percent:Math.min(100,remaining/total*100),basis:'本机用量页 · 相对自设总积分',stale:!!state.stale}:null;
  }
  if(c.source==='manual_quota'||c.source==='desktop_quota'){
   const remaining=number(c.quota_remaining),total=c.quota_unit==='%'?100:number(c.quota_total);
   return remaining!==null&&remaining>=0&&total>0?{percent:Math.min(100,remaining/total*100),basis:'手动记录',stale:false}:null;
  }
  if(c.source==='jev')return null;
  const balance=number(state?.selected?.balance);
  const total=number(['openai','anthropic'].includes(c.source)?c.budget:c.balance_total);
  return balance!==null&&total>0?{percent:Math.max(0,Math.min(100,balance/total*100)),basis:['openai','anthropic'].includes(c.source)?'预算剩余':'相对自设猫粮总额',stale:!!state.stale}:null;
 }
 function tier(percent){
  const n=number(percent);
  return n===null?'unknown':n<=25?'low':n<=50?'half':n<=75?'watch':'normal';
 }
 const labels={unknown:'未设置总额度',normal:'猫粮充足',watch:'有点担心猫粮',half:'猫粮只剩一半左右',low:'猫粮快没了喵…'};
 const api={quota,tier,labels};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.CatfoodQuotaMood=api;
})(typeof window==='object'?window:globalThis);
