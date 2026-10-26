const assert=require('node:assert/strict');
const {present}=require('../static/quota-display.js');
const {quota}=require('../static/quota-mood.js');
const {plan}=require('../static/food-bags.js');
const base={schema_version:1,value:'4.24',metric:'money_balance',unit:'CNY',percent:21.2,status:'current',status_label:'当前读数',basis:'官方余额接口',can_alert:true};
const state={profile:'deepseek',config:{source:'deepseek',model:'DeepSeek'},selected:{balance:'999',currency:'CNY'},allowance:base};
assert.equal(present(state).amount,'¥ 4.24');
assert.equal(quota(state).percent,21.2);
assert.equal(plan(state).count,1);
for(const status of ['expired','invalid','unknown']){
 const s={...state,allowance:{...base,status,value:null,percent:null,can_alert:false}};
 assert.notEqual(present(s).amount,'¥ 999.00');assert.equal(quota(s),null);assert.equal(plan(s).percent,null);assert.equal(plan(s).count,1);
}
const stale={...state,allowance:{...base,status:'stale',can_alert:false}};
assert.equal(present(stale).amount,'¥ 4.24');assert.equal(quota(stale).stale,true);
const conflict={...state,allowance:{...base,status:'conflict',can_alert:false}};
assert.equal(quota(conflict).stale,true);
const used={...state,allowance:{...base,value:'12000',metric:'usage_total',unit:'token',percent:null,can_alert:false}};
assert.equal(present(used).amount,'1.2万 token');assert.match(present(used).detail,/已上报用量/);assert.equal(plan(used).percent,null);assert.equal(plan(used).count,1);
assert.equal(present({...state,allowance:{...base,metric:'budget_remaining',unit:'USD'}}).detail,'DeepSeek 预算剩余');
assert.equal(present({...state,allowance:{...base,value:'0.000000000000000001'}}).amount,'¥ 0.000000000000000001');
console.log('PASS: canonical facts override legacy values; expired/invalid/missing are not zero; stale/conflict suppress reactions; reported usage is not remaining; budget semantics and tiny money precision.');
