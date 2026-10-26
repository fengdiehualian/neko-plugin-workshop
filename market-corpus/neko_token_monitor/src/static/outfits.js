/* YUI keeps her identity; model families select local costume assets. */
(function(root){
 'use strict';
 const outfits={
  original:{name:'YUI 原装',style:'蓝白外套',image:'/yui-chibi.png',color:'#5db5ed'},
  gpt:{name:'GPT / Codex',style:'白绿程序员外套',image:'/outfits/gpt.png',color:'#229d98'},
  claude:{name:'Claude',style:'陶土色卫衣',image:'/outfits/claude.png',color:'#cb7954'},
  deepseek:{name:'DeepSeek',style:'鲸鱼女仆装',image:'/outfits/deepseek.png',color:'#466cb6'},
  gemini:{name:'Gemini',style:'星光水手服',image:'/outfits/gemini.png',color:'#9470d5'},
  glm:{name:'GLM',style:'黑金科技女仆装',image:'/outfits/glm.png',color:'#b38a38'},
  kimi:{name:'Kimi',style:'月光航行夹克',image:'/outfits/kimi.png',color:'#586b9f'},
  qwen:{name:'通义千问',style:'紫色盘扣外套',image:'/outfits/qwen.png',color:'#9566d4'},
  doubao:{name:'豆包',style:'薄荷橘色围裙',image:'/outfits/doubao.png',color:'#7cab8c'},
  jev:{name:'JEV',style:'红白开发者夹克',image:'/outfits/jev.png',color:'#bb5362'}
 };
 function family(value){
  if(typeof value!=='string')return null;
  const s=value.toLowerCase();
  if(/deepseek|深度求索/.test(s))return 'deepseek';
  if(/claude|anthropic/.test(s))return 'claude';
  if(/gemini|antigravity/.test(s))return 'gemini';
  if(/(?:^|[\s/_.-])(?:chat)?glm(?:[\s/_.\d-]|$)|zhipu|智谱|z\.ai|zai-/.test(s))return 'glm';
  if(/kimi|moonshot/.test(s))return 'kimi';
  if(/qwen|tongyi|通义|千问/.test(s))return 'qwen';
  if(/doubao|豆包|bytedance|(?:^|[\s/_.-])seed(?:[\s/_.\d-]|$)/.test(s))return 'doubao';
  if(/jev/.test(s))return 'jev';
  if(/gpt|codex|openai|chatgpt|(?:^|[\s/_.-])o[134](?:[\s/_.\d-]|$)/.test(s))return 'gpt';
  return null;
 }
 function resolve(state,agents,mode='auto',fixed='original'){
  if(mode==='fixed')return {key:outfits[fixed]?fixed:'original',reason:'固定服装'};
  if(mode==='auto'&&agents?.status==='busy'){
   const rows=[...(agents.active_agents||[])].sort((a,b)=>(b.at||0)-(a.at||0));
   for(const agent of rows){
    const key=family(agent.model)||family(agent.id)||family(agent.name);
    if(key)return {key,reason:'跟随 '+agent.name,model:agent.model||null};
   }
  }
  const key=family(state?.config?.model)||family(state?.profile)||family(state?.config?.source)||'original';
  return {key,reason:key==='original'?'未识别模型，穿回原装':'跟随气泡模型'};
 }
 const api={outfits,family,resolve};
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.CatfoodWardrobe=api;
})(typeof window==='object'?window:globalThis);
