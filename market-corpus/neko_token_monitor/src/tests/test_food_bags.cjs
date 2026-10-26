const assert=require('node:assert/strict');
const {plan,brand,faceRect,contains}=require('../static/food-bags.js');
const codex=(percent,extra={})=>({profile:'codex',config:{source:'codex'},codex_quota:{windows:[{label:'5 小时',remaining:percent},{label:'每周',remaining:100}]},...extra});
for(const [percent,count] of [[100,3],[75.001,3],[75,3],[74.999,2],[50.001,2],[50,2],[49.999,1],[25.001,1],[25,1],[24.999,1],[0,1]]){
 const value=plan(codex(percent));assert.equal(value.count,count);assert.equal(value.key,'codex');
}
assert.equal(plan({...codex(90),codex_quota:{windows:[{remaining:90},{remaining:49}]}}).count,1);
for(const state of [{...codex(90),codex_quota:{windows:[]}},null,
 {config:{source:'deepseek'},selected:{balance:50}},
 {config:{source:'manual_quota',quota_remaining:300,quota_unit:'token'}},
 {config:{source:'jev'},reported_usage:{input_tokens:500}}]){
 assert.equal(plan(state).count,1);assert.equal(plan(state).percent,null);
}
assert.equal(plan({profile:'qwen',config:{source:'manual_quota',quota_remaining:250000,quota_total:1000000,quota_unit:'token'}}).count,1);
assert.equal(plan({profile:'trae',config:{source:'desktop_quota',quota_total:1000},account_quota:{remaining:750}}).count,3);
assert.equal(plan(codex(80,{stale:true})).stale,true);
assert.equal(plan(codex(80,{error:'offline'})).stale,true);
for(const [profile,key] of [['codex','codex'],['chatgpt','gpt'],['openai','gpt'],['claude','claude'],['anthropic','claude'],['deepseek_harness','deepseek'],['kimi_api_cn','kimi'],['qwen_api','qwen'],['trae','trae'],['workbuddy','workbuddy'],['openrouter','openrouter'],['siliconflow','siliconflow']])assert.equal(brand({profile,config:{model:'Claude'},active_agents:[{model:'GPT'}]}).key,key);
assert.equal(brand({profile:'manual',config:{source:'manual',model:'Codex'}}).key,'codex');
assert.equal(brand({profile:'custom',config:{model:'<script>bad</script>'}}).key,'other');
const face=faceRect({left:100,top:200,width:205,height:207});
assert.equal(contains(face,207,330),true);assert.equal(contains(face,207,210),false);assert.equal(contains(face,100,400),false);
assert.equal(contains(face,NaN,330),false);assert.equal(contains(null,207,330),false);
const scaled=faceRect({left:600,top:50,width:102.5,height:103.5});
assert.equal(contains(scaled,650,110),true);assert.equal(contains(scaled,207,330),false);
assert.equal(contains(faceRect({left:0,top:0,width:70,height:70},true),35,20),true);
console.log('PASS: minimum one bag, percentage boundaries, unknown is not zero, monitored-provider branding, and face drop bounds after scaling/movement.');
