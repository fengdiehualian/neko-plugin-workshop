import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const repo = fileURLToPath(new URL("../../../../", import.meta.url));
const plugin = fileURLToPath(new URL("../", import.meta.url));
const front = path.join(repo, "frontend/plugin-manager");
const { Window } = await import(pathToFileURL(path.join(front, "node_modules/happy-dom/lib/index.js")).href);
const { transform } = await import(pathToFileURL(path.join(front, "node_modules/sucrase/dist/index.js")).href);
const { bundleHostedTsxSource } = await import(pathToFileURL(path.join(front, "src/components/plugin/hosted/hostedTsxModule.mjs")).href);
const runtime = await fs.readFile(path.join(front, "src/components/plugin/hosted/ui-kit/runtime.js"), "utf8");
const source = await fs.readFile(path.join(plugin, "ui/panel.tsx"), "utf8");
const messages = Object.fromEntries(await Promise.all(["zh-CN", "en"].map(async locale => [locale, JSON.parse(await fs.readFile(path.join(plugin, "locales", locale + ".json"), "utf8"))])));
const dependencies = [];
async function collect(dir) {
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) await collect(absolute);
    else if (/\.tsx?$/.test(entry.name) && entry.name !== "panel.tsx") dependencies.push({ path: path.relative(plugin, absolute).replaceAll("\\", "/"), source: await fs.readFile(absolute, "utf8") });
  }
}
await collect(path.join(plugin, "ui"));
const compiled = transform(bundleHostedTsxSource(source, dependencies, "ui/panel.tsx"), { transforms:["typescript","jsx"], jsxPragma:"h", jsxFragmentPragma:"Fragment", production:true }).code.replace(/export\s+default\s+function\s+([\w$]*)\s*\(/, "const __Panel = function $1(");
const guideEntry = "ui/guide/guide-page.tsx";
const guideSource = await fs.readFile(path.join(plugin,guideEntry),"utf8");
const compiledGuide = transform(bundleHostedTsxSource(guideSource, dependencies.filter(item=>item.path!==guideEntry), guideEntry), { transforms:["typescript","jsx"], jsxPragma:"h", jsxFragmentPragma:"Fragment", production:true }).code.replace(/export\s+default\s+function\s+([\w$]*)\s*\(/, "const __Panel = function $1(");
const flush = async () => { for (let i=0;i<8;i++) await Promise.resolve(); await new Promise(resolve => setTimeout(resolve, 1)); };
function setup(locale = "zh-CN", initialState = {}, surface = "panel") {
  const window = new Window({ url:"http://localhost/ui" });
  window.__NEKO_PAYLOAD={ locale, host:{origin:"http://localhost"}, i18n:{messages,default_locale:"en"} };
  const NativeText = window.Text;
  window.eval(runtime);
  window.Text = NativeText; // happy-dom constructs text through window.Text; browsers use their native DOM implementation.
  window.eval((surface === "guide" ? compiledGuide : compiled)+"\nwindow.TestPanel=__Panel;");
  const ui=window.NekoUiKit;
  const root=window.document.createElement("main");window.document.body.append(root);
  const state={ action_nonce:"test-nonce", status:"ready", port:48919, configuration:{token_configured:true,agent_configured:true,agent_api_key_configured:true,agent_base_url:"https://example.com/v1",agent_model:"test"}, agent:{active_count:0,recent_count:0,active_runs:[],recent_runs:[],latest_run:null} };
  Object.assign(state, initialState);
  const calls=[];
  let refreshes=0;
  let invoke=async (id,args) => ({ result:id === "workspace_view" ? {workspaces:[],total:0,next_offset:null} : id === "begin_settings_save" ? {operation_id:args.operation_id,kind:args.kind,state:"saved",persisted:true,applied:true} : {status:"ok"} });
  const props={ state, plugin:{id:"neko_rimworld",name:"RimWorld"}, actions:["workspace_view","rimworld_report","get_agent_run_details","rimworld_consult","probe_game_overview","save_agent_settings","save_bridge_settings","begin_settings_save","get_settings_save"].map(id=>({id,entry_id:id})), warnings:[], locale, api:{call:async(id,args)=>{calls.push({id,args});return invoke(id,args)}, refresh:async()=>{refreshes++;render()}} };
  function render(){ui.render(ui.h(window.TestPanel,props),root)}
  function button(label, selector="button") { const found=[...root.querySelectorAll(selector)].find(x=>x.textContent===label);assert.ok(found,`Missing ${label}: ${root.textContent}`);return found; }
  async function click(label,selector="button"){button(label,selector).click();await flush()}
  async function tab(label){
    if(label === "调试" || label === "主页" || label === "会话与事件") {
      const advanced=root.querySelector('#rimworld-main-nav-tab-diagnostics');
      assert.ok(advanced,"调试 must always be present in the top navigation");advanced.click();
      await flush();await click(label === "调试" ? "咨询测试" : "执行记录",'[role="tab"]');return;
    }
    await click(label,'[role="tab"]')
  }
  async function type(input,value){assert.ok(input);input.value=value;input.dispatchEvent(new window.Event("input",{bubbles:true}));await flush()}
  render();
  return {window,root,props,state,calls,render,button,click,tab,type,refreshes:()=>refreshes,setInvoke(fn){invoke=fn},async close(){ui.render(null,root);await window.happyDOM.abort();window.close()}};
}

function summary(id,question){return {run_id:id,question,state:"completed",result_status:"ok",result_summary:question+" summary",event_count:61,revision:61,terminal:true,stage:"completed",latest_event:{event_seq:61,event:"run_completed",cycle:1,elapsed_ms:1000},latest_event_summary:"Run completed.",model_calls:1,tool_calls:1,duration_ms:1000}}
function detail(id,question,offset=0){return {...summary(id,question),events:[{event_seq:offset+1,event:"model_response_received",elapsed_ms:100,data:{content:question}}],events_page:{offset,limit:50,total:61,has_more:offset===0},result:{status:"ok",answer:question+" full",summary:question+" summary",observations:[]}}}

test("the guide translates every chapter and offers public downloads without running actions",async()=>{
  for(const locale of ["zh-CN","en","zh-TW","fr"]){
    const h=setup(locale,{},"guide");try{
      await flush();
      assert.equal(h.root.querySelector('#rimworld-main-nav'),null);
      const chapters=[...h.root.querySelectorAll('section[id^="rimworld-guide-"]')];
      assert.equal(chapters.length,8);
      assert.deepEqual(h.root.textContent.match(/\bguide\.(?:title|intro|(?:prepare|mod|plugin|pair|model|chat|answers|help|link|build)\.)/g),null,locale);
      const chinese=locale.startsWith("zh");
      assert.match(h.root.textContent,chinese?/从下载安装到第一次咨询/:/From download to your first colony question/);
      assert.match(h.root.textContent,chinese?/用户插件/:/User Plugin/);
      assert.match(h.root.textContent,/127\.0\.0\.1/);
      assert.match(h.root.textContent,/48919/);
      const links=[...h.root.querySelectorAll('.neko-download')];
      assert.equal(links.length,7);
      const external=[];h.window.parent.postMessage=message=>{if(message.type==='neko-hosted-surface-open-external')external.push(message.payload.url)};
      for(const link of links)link.click();
      assert.equal(external.length,7,"sandboxed downloads must go through the host external-link handler");
      assert.ok(external.includes("https://github.com/wislap/NekoRimWorld/releases/download/v3.2.0/NekoRimWorld-3.2.0.zip"));
      assert.ok(external.includes("https://github.com/wislap/n.e.k.o_plugin_neko_rimworld/releases"));
      assert.ok(external.every(url=>url.startsWith("https://")));
      for(const detail of h.root.querySelectorAll('details'))detail.querySelector('summary').click();
      let jumped=false;chapters[4].scrollIntoView=()=>{jumped=true};
      h.root.querySelectorAll('nav button')[4].click();await flush();
      assert.ok(jumped);assert.equal(h.window.document.activeElement,chapters[4]);
      assert.equal(h.calls.length,0,"reading and jumping through the guide must not call the backend");
    }finally{await h.close()}
  }
});

test("the guide is separate from the four panel pages and never disturbs panel drafts",async()=>{
  const h=setup();const guide=setup("zh-CN",{},"guide");try{
    await flush();
    assert.equal(h.root.querySelectorAll('#rimworld-main-nav-tab-guide').length,0);
    assert.equal(h.root.querySelectorAll('[role="tablist"] [role="tab"]').length,4);
    await h.tab("设置");
    await h.type(h.root.querySelector('input[placeholder="https://example.com/v1"]'),"https://draft.example/v1");
    await h.type(h.root.querySelectorAll('input[type="password"]')[1],"draft-key");
    assert.equal(guide.root.querySelectorAll('section[id^="rimworld-guide-"]').length,8);
    h.render();await flush();
    assert.equal(h.root.querySelector('input[placeholder="https://example.com/v1"]').value,"https://draft.example/v1");
    assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"draft-key");
    assert.equal(guide.calls.length,0);
    assert.ok(h.calls.every(call=>call.id==='workspace_view'));
  }finally{await guide.close();await h.close()}
});

test("primary pages preserve drafts across navigation and server refresh",async()=>{
  const h=setup();try{
    await flush();assert.equal(h.root.querySelectorAll('input[type="password"]').length,0);
    await h.tab("设置");
    await h.type(h.root.querySelector('input[placeholder="https://example.com/v1"]'),"https://draft.example/v1");
    await h.type(h.root.querySelectorAll('input[type="password"]')[1],"temporary-test-secret");
    await h.tab("调试");await h.type(h.root.querySelector("textarea"),"unfinished question");
    await h.tab("主页");h.state.configuration.agent_base_url="https://server-change.example/v1";h.render();await flush();
    await h.tab("设置 · 未保存");
    assert.equal(h.root.querySelector('input[placeholder="https://example.com/v1"]').value,"https://draft.example/v1");
    assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"temporary-test-secret");
    await h.tab("调试");assert.equal(h.root.querySelector("textarea").value,"unfinished question");
    assert.ok(h.calls.every(x=>x.id === "workspace_view"));
  }finally{await h.close()}
});

test("running consultation survives navigation and prevents duplicate submit",async()=>{
  const h=setup();try{
    let finish;
    h.setInvoke(async(id,args)=>{
      if(id==="rimworld_consult")return await new Promise(resolve=>{finish=resolve});
      if(id==="get_agent_run_details")return {result:detail(args.run_id,"completed question",args.event_offset)};
      return {result:{status:"ok"}};
    });
    await h.tab("调试");const run=h.button("运行咨询");run.click();run.click();await flush();
    assert.equal(h.calls.filter(x=>x.id==="rimworld_consult").length,1);
    await h.tab("主页");assert.match(h.root.textContent,/正在分析/);
    finish({result:{status:"degraded",summary:"A limited result",audit:{run_id:"run-new"}}});await flush();
    await h.tab("调试");assert.match(h.root.textContent,/A limited result/);
    await h.click("打开本次会话详情");await flush();assert.match(h.root.textContent,/completed question full/);
    assert.equal(h.root.querySelector('#rimworld-main-nav-tab-diagnostics').getAttribute("aria-selected"),"true");
    assert.equal(h.calls.filter(x=>x.id==="get_agent_run_details").length,1);
  }finally{await h.close()}
});

test("degraded runs explain the provider failure and next action without opening raw events",async()=>{
  const h=setup();try{
    const failed={...summary("failed-run","Colony status"),result_status:"degraded",model_calls:4,model_failures:3,tool_calls:2,failure_summary:{
      error_code:"model_request_deadline",phase:"first_response",attempts:4,failed_attempts:3,retries:2,provider_failures:2,
      provider_error:{type:"upstream_error",message:"Our servers are currently overloaded. Please try again later."}
    }};
    h.state.agent.recent_runs=[failed];h.state.agent.latest_run=failed;h.render();await flush();
    await h.tab("会话与事件");
    assert.match(h.root.textContent,/模型服务过载/);
    assert.match(h.root.textContent,/已自动重试 2 次/);
    assert.match(h.root.textContent,/稍后重试或更换模型服务/);
    h.setInvoke(async()=>({result:{...detail("failed-run","Colony status"),...failed,result:{status:"degraded",answer:"fallback",summary:"fallback",observations:[{}]}}}));
    await h.click("查看本次详情");await flush();
    assert.match(h.root.textContent,/已取得 1 份游戏观察/);
    assert.match(h.root.textContent,/Our servers are currently overloaded/);
  }finally{await h.close()}
});

test("active runs use lightweight progress and fetch full detail once on completion",async()=>{
  const h=setup();try{
    const active={...summary("active-run","Live progress"),state:"model_requesting",result_status:null,result_summary:null,terminal:false,revision:4,event_count:4,stage:"model_requesting",latest_event:{event_seq:4,event:"model_retry_scheduled",cycle:1,elapsed_ms:2500},latest_event_summary:"Retrying the model in 0.5 seconds.",duration_ms:2500};
    h.state.agent.active_count=1;h.state.agent.active_runs=[active];h.state.agent.recent_runs=[];h.state.agent.latest_run=active;h.render();await flush();
    await h.tab("会话与事件");
    assert.match(h.root.textContent,/模型暂时没有响应，正在重试/);
    await h.tab("会话与事件");await flush();
    assert.match(h.root.textContent,/模型暂时没有响应，正在重试/);
    assert.match(h.root.textContent,/进度版本.*4/);
    assert.equal(h.calls.filter(x=>x.id==="get_agent_run_details").length,0);

    const completed={...summary("active-run","Live progress"),revision:9,event_count:9};
    h.setInvoke(async(id,args)=>id==="get_agent_run_details"?{result:{...detail(args.run_id,"Live progress",args.event_offset),revision:9,terminal:true}}:{result:{status:"ok"}});
    h.state.agent.active_count=0;h.state.agent.active_runs=[];h.state.agent.recent_runs=[completed];h.state.agent.latest_run=completed;h.render();await flush();
    assert.match(h.root.textContent,/Live progress full/);
    assert.equal(h.calls.filter(x=>x.id==="get_agent_run_details").length,1);
    h.render();await flush();
    assert.equal(h.calls.filter(x=>x.id==="get_agent_run_details").length,1);
  }finally{await h.close()}
});

test("detail refresh failure preserves the last successful detail",async()=>{
  const h=setup();try{
    const run=summary("stable-run","Stable detail");h.state.agent.recent_runs=[run];h.state.agent.latest_run=run;
    h.setInvoke(async()=>({result:detail("stable-run","Stable detail")}));h.render();await flush();await h.tab("会话与事件");await flush();
    assert.match(h.root.textContent,/Stable detail full/);
    h.setInvoke(async()=>{throw new Error("Hosted surface request timed out.")});
    await h.click("刷新详情");
    assert.match(h.root.textContent,/Stable detail full/);
    assert.match(h.root.textContent,/详情刷新暂时失败/);
    assert.match(h.root.textContent,/已保留上次成功读取的内容/);
  }finally{await h.close()}
});

test("selected historical run, event pages and expiry never switch to latest",async()=>{
  const h=setup();try{
    const first=summary("r1","Older question");const second=summary("r2","Newer question");
    h.state.agent.recent_runs=[second,first];h.state.agent.latest_run=second;h.render();await flush();
    let expired=false;
    h.setInvoke(async(id,args)=>{
      if(expired)throw new Error("This run is no longer retained");
      return {result:detail(args.run_id,args.run_id==="r1"?"Older question":"Newer question",args.event_offset)};
    });
    await h.tab("会话与事件");
    const row=[...h.root.querySelectorAll("tr")].find(x=>x.textContent.includes("Older question"));assert.ok(row);row.click();await flush();
    assert.match(h.root.textContent,/Older question full/);
    h.state.agent.latest_run=summary("r3","Newest");h.render();await flush();assert.match(h.root.textContent,/Older question full/);
    await h.tab("事件");await h.click("下一页");assert.equal(h.calls.at(-1).args.event_offset,50);
    expired=true;h.state.agent.recent_runs=[h.state.agent.latest_run];h.render();await flush();
    await h.click("刷新详情");assert.match(h.root.textContent,/no longer retained/);
    assert.doesNotMatch(h.root.textContent,/Newest full/);
  }finally{await h.close()}
});

test("budget edits are included in save and secret inputs clear only after success",async()=>{
  const h=setup();try{
    await h.tab("设置");const numbers=h.root.querySelectorAll('input[type="number"]');await h.type(numbers[4],"40");
    await h.type(h.root.querySelectorAll('input[type="password"]')[1],"temporary-key");
    await h.click("保存模型设置");
    const saved=h.calls.find(x=>x.id==="begin_settings_save");assert.equal(saved.args.budgets.request_timeout_seconds,40);
    assert.equal(saved.args.ui_nonce,"test-nonce");assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"");
    assert.match(h.root.textContent,/已保存/);
  }finally{await h.close()}
});


test("lost bridge save reply checks the same operation without another write",async()=>{
  const h=setup();try{
    h.setInvoke(async(id,args)=>{
      if(id==="begin_settings_save"){h.state.status="error";throw new Error("Hosted request timed out")}
      if(id==="get_settings_save")return {result:{operation_id:args.operation_id,kind:"bridge",state:"unconfirmed"}};
      return {result:{status:"ok"}};
    });
    await h.tab("设置");
    const port=h.root.querySelector('input[type="number"]');await h.type(port,"48920");
    await h.click("保存并应用连接设置");
    assert.equal(h.refreshes(),1);assert.equal(port.value,"48920");
    assert.match(h.root.textContent,/尚未确认保存结果/);
    assert.match(h.root.textContent,/游戏连接需要检查/);
    assert.equal(h.calls.filter(x=>x.id==="begin_settings_save").length,1);
    assert.equal(h.calls.find(x=>x.id==="get_settings_save").args.operation_id,h.calls.find(x=>x.id==="begin_settings_save").args.operation_id);
  }finally{await h.close()}
});

test("late run detail replies cannot replace a newer selection",async()=>{
  const h=setup();try{
    const first=summary("r1","First");const second=summary("r2","Second");
    h.state.agent.recent_runs=[first,second];h.state.agent.latest_run=first;h.render();await flush();
    const pending={};
    h.setInvoke((id,args)=>new Promise(resolve=>{pending[args.run_id]=resolve}));
    await h.tab("会话与事件");
    const row=[...h.root.querySelectorAll("tr")].find(x=>x.textContent.includes("Second"));row.click();await flush();
    pending.r2({result:detail("r2","Second")});await flush();
    pending.r1({result:detail("r1","First")});await flush();
    assert.match(h.root.textContent,/Second full/);assert.doesNotMatch(h.root.textContent,/First full/);
  }finally{await h.close()}
});

test("top navigation supports keyboard selection",async()=>{
  const h=setup();try{
    const tab=h.root.querySelector("#rimworld-main-nav-tab-work");tab.focus();
    tab.dispatchEvent(new h.window.KeyboardEvent("keydown",{key:"End",bubbles:true}));await flush();
    assert.equal(h.root.querySelector("#rimworld-main-nav-tab-settings").getAttribute("aria-selected"),"true");
    assert.equal(h.window.document.activeElement,h.root.querySelector("#rimworld-main-nav-tab-settings"));
    assert.ok(h.root.querySelector('input[type="password"]'));
  }finally{await h.close()}
});

test("debug remains a top-level destination from advice, history and settings",async()=>{
  const h=setup();try{
    await flush();
    for(const label of ["最近建议","历史记录","设置"]){
      await h.click(label,'[role="tab"]');
      const tab=h.root.querySelector('#rimworld-main-nav-tab-diagnostics');
      assert.equal(tab.textContent,"调试");
      assert.equal(tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]').length,4);
    }
    await h.type(h.root.querySelector('input[placeholder="https://example.com/v1"]'),"https://unsaved.example/v1");
    await h.click("调试",'[role="tab"]');
    assert.equal(h.root.querySelector('#rimworld-main-nav-tab-diagnostics').getAttribute("aria-selected"),"true");
    assert.ok(h.button("执行记录",'[role="tab"]'));
    assert.ok(h.button("原始读取记录",'[role="tab"]'));
    await h.click("咨询测试",'[role="tab"]');
    assert.ok(h.root.querySelector('textarea'));
    await h.click("设置 · 未保存",'[role="tab"]');
    assert.equal(h.root.querySelector('input[placeholder="https://example.com/v1"]').value,"https://unsaved.example/v1");
    assert.ok(h.calls.every(call=>call.id === "workspace_view"));
  }finally{await h.close()}
});

test("output budget is editable and length errors display actual limits",async()=>{
  const h=setup();try{
    await h.tab("设置");
    const input=h.root.querySelectorAll('input[type="number"]')[7];
    assert.equal(input.value,"32768");await h.type(input,"65536");
    await h.click("保存模型设置");
    assert.equal(h.calls.at(-1).args.budgets.max_output_tokens,65536);
    const run={...summary("length-run","Budget failure"),result_status:"degraded",failure_summary:{error_code:"model_output_limit",finish_reason:"length",max_output_tokens:32768,usage:{completion_tokens:32768,reasoning_tokens:32000}}};
    h.state.agent.latest_run=run;h.state.agent.recent_runs=[run];h.render();await flush();
    h.setInvoke(async()=>({result:{...detail("length-run","Budget failure"),...run,result:{status:"degraded"}}}));
    await h.tab("主页");await h.click("查看本次详情");await flush();
    assert.match(h.root.textContent,/输出预算不足/);assert.match(h.root.textContent,/32768 \/ 32000/);
    assert.match(h.root.textContent,/length/);
  }finally{await h.close()}
});


test("saved reports show full body, preserve draft status and explicitly attach followups",async()=>{
  const h=setup();try{
    const id="a".repeat(32), full="完整正文。".repeat(500)+"报告末尾保留";
    h.setInvoke(async(entry,args)=>{
      if(entry==="rimworld_report" && args.action==="list")return {result:{reports:[{report_id:id,state:"draft",question:"历史调查",created_at:"2026-09-05"}],total:1,next_offset:null}};
      if(entry==="rimworld_report")return {result:{report:{report_id:id,state:"draft",question:"历史调查",full_text:full,observation_count:2}}};
      return {result:{status:"ok",summary:"followup done"}};
    });
    await h.tab("会话与事件");await h.tab("完整报告");
    const row=[...h.root.querySelectorAll("tr")].find(x=>x.textContent.includes("历史调查"));assert.ok(row);row.click();await flush();
    assert.ok(h.root.textContent.includes(id));assert.match(h.root.textContent,/报告末尾保留/);assert.match(h.root.textContent,/不能当作正式交付/);
    await h.click("复制完整正文");assert.equal(await h.window.navigator.clipboard.readText(),full);
    await h.click("基于这份报告追问");await h.type(h.root.querySelector("textarea"),"解释原报告的细节");await h.click("运行咨询");
    assert.deepEqual(JSON.parse(JSON.stringify(h.calls.find(x=>x.id==="rimworld_consult").args)),{question:`解释原报告的细节\n请分析保存的历史报告 ${id}。`});
    await h.click("取消报告引用");await h.click("运行咨询");
    assert.deepEqual(JSON.parse(JSON.stringify(h.calls.filter(x=>x.id==="rimworld_consult").at(-1).args)),{question:"解释原报告的细节"});
  }finally{await h.close()}
});

test("late report reply cannot replace a newly selected report",async()=>{
  const h=setup();try{
    const first="a".repeat(32),second="b".repeat(32),pending={};
    h.setInvoke(async(entry,args)=>{
      if(args.action==="list")return {result:{reports:[{report_id:first,question:"报告一"},{report_id:second,question:"报告二"}],total:2}};
      return await new Promise(resolve=>{pending[args.report_id]=resolve});
    });
    await h.tab("会话与事件");await h.tab("完整报告");
    [...h.root.querySelectorAll("tr")].find(x=>x.textContent.includes("报告一")).click();await flush();
    [...h.root.querySelectorAll("tr")].find(x=>x.textContent.includes("报告二")).click();await flush();
    pending[second]({result:{report:{report_id:second,full_text:"第二份正文",state:"complete"}}});await flush();
    pending[first]({result:{report:{report_id:first,full_text:"第一份正文",state:"complete"}}});await flush();
    assert.match(h.root.textContent,/第二份正文/);assert.doesNotMatch(h.root.textContent,/第一份正文/);
  }finally{await h.close()}
});

function workFixture(id, owner, question) {
  const turn = `${id}-turn`
  const workspace = {workspace_id:id,owner,colony_id:`${id}-colony`,work_state:"waiting",last_question:question,turn_count:1}
  return {workspace, history:{workspace_id:id,workspace,turns:[{turn_id:turn,sequence:1,question,status:"ok"}],total:1,next_offset:null},
    exchange:{workspace_id:id,turn_id:turn,question,status:"ok",history_saved:true,audit_available:true,
      result:{status:"ok",answer:question+" 完整结果末尾",agent_text:"旧内核摘要"},handoff:{summary:"最终宿主摘要",prepared_at:"2026-10-03"},
      evidence:[{observation_id:"source",sha256:"original-hash"}]}}
}

test("persistent work remains visible offline with no volatile runs or consultation calls",async()=>{
 const h=setup();try{
  const a=workFixture("work-a","小天","患者历史调查")
  h.setInvoke(async(id,args)=>{
   assert.equal(id,"workspace_view")
   if(args.action==="list")return {result:{workspaces:[a.workspace],total:1,next_offset:null}}
   if(args.action==="work")return {result:a.history}
   if(args.action==="turn")return {result:a.exchange}
   if(args.action==="observation")return {result:{workspace_id:"work-a",turn_id:"work-a-turn",observation_id:"source",sha256:"original-hash",observation:{response:{payload:{data:{flag:false,value:null}}}}}}
  })
  await h.tab("历史记录");await flush()
  assert.match(h.root.textContent,/游戏离线/)
  assert.match(h.root.textContent,/患者历史调查 完整结果末尾/)
  assert.match(h.root.textContent,/最终宿主摘要/)
  assert.doesNotMatch(h.root.textContent,/旧内核摘要/)
  await h.click("查看这次分析的高级诊断")
  await h.click("1. 游戏读取记录 1")
  assert.match(h.root.textContent,/original-hash/)
  assert.match(h.root.textContent,/false/)
  assert.ok(h.calls.every(c=>c.id==="workspace_view"))
 }finally{await h.close()}
})

test("late work response cannot replace another role's selected work",async()=>{
 const h=setup();try{
  const a=workFixture("a","角色甲","甲的调查"), b=workFixture("b","角色乙","乙的调查")
  let finishA
  h.setInvoke(async(id,args)=>{
   if(args.action==="list")return {result:{workspaces:[a.workspace,b.workspace],total:2,next_offset:null}}
   if(args.action==="work" && args.workspace_id==="a")return await new Promise(resolve=>{finishA=resolve})
   if(args.action==="work")return {result:b.history}
   return {result:b.exchange}
  })
  await h.tab("历史记录");await flush()
  const row=[...h.root.querySelectorAll("tr")].find(x=>x.textContent.includes("角色乙"));row.click();await flush()
  assert.match(h.root.textContent,/乙的调查 完整结果末尾/)
  finishA({result:a.history});await flush()
  assert.doesNotMatch(h.root.textContent,/甲的调查 完整结果末尾/)
  assert.match(h.root.textContent,/乙的调查 完整结果末尾/)
 }finally{await h.close()}
})

test("saved work audit opens after volatile run history has been cleared",async()=>{
 const h=setup();try{
  const a=workFixture("a","小天","持久调查")
  h.setInvoke(async(id,args)=>{
   if(args.action==="list")return {result:{workspaces:[a.workspace],total:1,next_offset:null}}
   if(args.action==="work")return {result:a.history}
   if(args.action==="turn")return {result:a.exchange}
   if(args.action==="audit")return {result:{...detail("saved-run","保存的执行审计"),workspace_id:"a",turn_id:"a-turn"}}
   throw new Error("Unexpected call")
  })
  await h.tab("历史记录");await flush()
  await h.click("查看这次分析的高级诊断")
  await h.click("查看保存的执行诊断")
  assert.match(h.root.textContent,/保存的执行审计 full/)
  assert.match(h.root.textContent,/工作中保存的执行审计/)
  assert.equal(h.calls.filter(c=>c.id==="get_agent_run_details").length,0)
 }finally{await h.close()}
})

test("work refresh failure preserves the selected historical answer and save uncertainty",async()=>{
 const h=setup();try{
  const a=workFixture("a","小天","完整但未保存")
  a.exchange.history_saved=false
  h.setInvoke(async(id,args)=>({result:args.action==="list"?{workspaces:[a.workspace],total:1,next_offset:null}:args.action==="work"?a.history:a.exchange}))
  await h.tab("历史记录");await flush()
  assert.match(h.root.textContent,/保存尚未确认/)
  h.setInvoke(async()=>{throw new Error("Offline read failure")})
  await h.click("刷新记录")
  assert.match(h.root.textContent,/完整但未保存 完整结果末尾/)
  assert.match(h.root.textContent,/历史记录暂时无法读取/)
  assert.ok(h.calls.every(c=>c.id==="workspace_view"))
 }finally{await h.close()}
})

test("advice defaults to the current colony and keeps full text and raw data out of the first view",async()=>{
 const h=setup();try{
  const old=workFixture("older","小天","另一殖民地的问题"),current=workFixture("current","小天","当前殖民地的问题")
  h.state.session={colony_id:"current…ony",load_epoch:"load…1"}
  current.workspace.current_colony=true
  current.exchange.evidence[0].request={operation:"inspect_resources",arguments:{}}
  current.exchange.evidence[0].received_at="2026-10-17T08:00:00Z"
  h.setInvoke(async(id,args)=>({result:args.action==="list"?{workspaces:[old.workspace,current.workspace],total:2,next_offset:null}:args.action==="work"?current.history:args.action==="turn"?current.exchange:{...current.exchange,observation_id:"source",sha256:"original-hash",observation:{flag:false,value:null}}}))
  h.render();await flush()
  assert.match(h.root.textContent,/当前殖民地的问题/)
  assert.doesNotMatch(h.root.textContent,/另一殖民地的问题|source|original-hash|准备返回宿主|原始证据/)
  assert.equal(h.root.querySelectorAll('table').length,0)
  assert.equal(h.root.querySelector('details').open,false)
  assert.match(h.root.textContent,/最终宿主摘要/)
  assert.equal(h.calls.filter(c=>c.args.action==="observation").length,0)
  h.root.querySelector('summary').click();await flush()
  assert.equal(h.root.querySelector('details').open,true)
  await h.click("查看这次分析的高级诊断")
  assert.match(h.root.textContent,/物资来源与数量/)
  await h.click("1. 物资来源与数量")
  assert.match(h.root.textContent,/original-hash/)
  assert.match(h.root.textContent,/false/)
  assert.ok(h.calls.every(c=>c.id==="workspace_view"))
 }finally{await h.close()}
})

test("a new loaded colony cannot display the previous colony as its current advice",async()=>{
 const h=setup();try{
  const a=workFixture("a","角色甲","旧殖民地建议")
  h.state.session={colony_id:"a-colony",load_epoch:"first"}
  h.setInvoke(async(id,args)=>({result:args.action==="list"?{workspaces:[a.workspace],total:1,next_offset:null}:args.action==="work"?a.history:a.exchange}))
  h.render();await flush();assert.match(h.root.textContent,/旧殖民地建议/)
  h.state.session={colony_id:"new-colony",load_epoch:"second"};h.render();await flush()
  assert.doesNotMatch(h.root.textContent,/旧殖民地建议/)
  assert.match(h.root.textContent,/当前殖民地还没有讨论记录/)
 }finally{await h.close()}
})

test("operational handoffs are not shown as advice and the available full answer stays readable",async()=>{
 const h=setup();try{
  const a=workFixture("a","角色甲","未完整完成的分析")
  a.exchange.handoff={summary:"Internal operational notice",delivery:{submission_mode:"operational_fallback"}}
  a.exchange.result={status:"degraded",answer:"保留的作者正文",summary:"Internal fallback",delivery:{submission_mode:"operational_fallback"}}
  h.state.session={colony_id:"a-colony",load_epoch:"first"}
  h.setInvoke(async(id,args)=>({result:args.action==="list"?{workspaces:[a.workspace],total:1,next_offset:null}:args.action==="work"?a.history:a.exchange}))
  h.render();await flush()
  assert.doesNotMatch(h.root.textContent,/Internal operational|Internal fallback/)
  assert.match(h.root.textContent,/保留的作者正文/)
  assert.equal(h.root.querySelector('details').open,true)
  assert.match(h.root.textContent,/分析未完整完成/)
 }finally{await h.close()}
})

test("a failed historical answer read keeps its bound question list available for recovery",async()=>{
 const h=setup();try{
  const a=workFixture("a","角色甲","可恢复的历史问题")
  h.setInvoke(async(id,args)=>{
   if(args.action==="list")return {result:{workspaces:[a.workspace],total:1,next_offset:null}}
   if(args.action==="work")return {result:a.history}
   throw new Error("Temporary history transport failure")
  })
  await h.tab("历史记录");await flush()
  assert.match(h.root.textContent,/讨论过的问题/)
  assert.match(h.root.textContent,/可恢复的历史问题/)
  assert.match(h.root.textContent,/历史记录暂时无法读取/)
  assert.doesNotMatch(h.root.textContent,/最终宿主摘要/)
  h.setInvoke(async(id,args)=>({result:args.action==="list"?{workspaces:[a.workspace],total:1,next_offset:null}:args.action==="work"?a.history:a.exchange}))
  await h.click("刷新记录");await flush()
  assert.match(h.root.textContent,/最终宿主摘要/)
 }finally{await h.close()}
})

test("first use explains pairing, model setup and where to ask without calling either service",async()=>{
 const h=setup();try{
  h.state.configuration={token_configured:false,agent_configured:false};h.state.status="stopped";h.render();await flush()
  assert.match(h.root.textContent,/开始使用 RimWorld 建议/)
  assert.match(h.root.textContent,/配对码/);assert.match(h.root.textContent,/独立模型服务/)
  assert.match(h.root.textContent,/不会调用模型/)
  assert.ok(h.calls.every(call=>call.id==="workspace_view"))
  await h.click("打开设置");assert.ok(h.root.querySelector('input[placeholder="https://example.com/v1"]'))
  assert.equal(h.root.querySelector("details").open,false)
 }finally{await h.close()}
})

test("pending save disables duplicate writes and a manual receipt check clears a confirmed secret",async()=>{
 const h=setup();try{
  let saved=false
  h.setInvoke(async(id,args)=>({result:id==="begin_settings_save" || id==="get_settings_save" ? {operation_id:args.operation_id,kind:"agent",state:saved?"saved":"saving",persisted:saved?true:null,applied:saved} : {workspaces:[],total:0,next_offset:null}}))
  await h.tab("设置");await h.type(h.root.querySelectorAll('input[type="password"]')[1],"private-ui-key")
  h.button("保存模型设置").click();await flush()
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"private-ui-key")
  assert.match(h.root.textContent,/正在保存并确认/)
  assert.equal(h.button("正在保存，请稍候…").disabled,true)
  saved=true;await h.click("核对这次保存状态")
  assert.match(h.root.textContent,/设置已保存并应用/)
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"")
  assert.equal(h.calls.filter(call=>call.id==="begin_settings_save").length,1)
  assert.doesNotMatch(JSON.stringify(Array.from({length:h.window.localStorage.length},(_,i)=>h.window.localStorage.getItem(h.window.localStorage.key(i)))),/private-ui-key/)
 }finally{await h.close()}
})

test("a late successful receipt cannot erase a newer unsaved draft",async()=>{
 const h=setup();try{
  let pending
  h.setInvoke(async(id,args)=>{
   if(id==="begin_settings_save"){pending={operation_id:args.operation_id,kind:"agent"};return {result:{...pending,state:"unconfirmed"}}}
   if(id==="get_settings_save")return {result:{...pending,state:"saved",persisted:true,applied:true}}
   return {result:{workspaces:[],total:0,next_offset:null}}
  })
  await h.tab("设置");await h.type(h.root.querySelectorAll('input[type="password"]')[1],"first-key")
  await h.click("保存模型设置")
  await h.type(h.root.querySelector('input[placeholder="https://example.com/v1"]'),"https://new-draft.example/v1")
  await h.type(h.root.querySelectorAll('input[type="password"]')[1],"newer-key")
  h.state.settings_saves=[{...pending,state:"saved",persisted:true,applied:true}];h.render();await flush()
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"newer-key")
  assert.equal(h.root.querySelector('input[placeholder="https://example.com/v1"]').value,"https://new-draft.example/v1")
  assert.match(h.root.textContent,/上次提交的设置已保存/)
  assert.ok(h.button("设置 · 未保存",'[role="tab"]'))
 }finally{await h.close()}
})

test("remounting within the same iframe recovers a pending receipt without repeating the save",async()=>{
 const h=setup();try{
  let pending,finished=false
  h.setInvoke(async(id,args)=>{
   if(id==="begin_settings_save"){pending={operation_id:args.operation_id,kind:"agent"};return {result:{...pending,state:"saving"}}}
   if(id==="get_settings_save")return {result:{...pending,state:finished?"saved":"saving",persisted:finished?true:null,applied:finished}}
   return {result:{workspaces:[],total:0,next_offset:null}}
  })
  await h.tab("设置");await h.type(h.root.querySelectorAll('input[type="password"]')[1],"do-not-store-this-key")
  await h.click("保存模型设置")
  h.window.NekoUiKit.render(null,h.root)
  finished=true;h.render();await flush()
  assert.match(h.root.textContent,/设置已保存并应用/)
  assert.equal(h.calls.filter(call=>call.id==="begin_settings_save").length,1)
  assert.equal(h.calls.filter(call=>call.id==="get_settings_save").at(-1).args.operation_id,pending.operation_id)
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"")
 }finally{await h.close()}
})

test("a completely new iframe restores the latest save for each kind from the backend",async()=>{
 let receipt
 const first=setup();try{
  first.setInvoke(async(id,args)=>{
   if(id==="begin_settings_save") {receipt={operation_id:args.operation_id,kind:args.kind,state:"saving"};return {result:receipt}}
   return {result:{workspaces:[],total:0,next_offset:null}}
  })
  await first.tab("设置");await first.type(first.root.querySelectorAll('input[type="password"]')[1],"not-a-persistent-secret")
  await first.click("保存模型设置")
  assert.equal(first.calls.filter(c=>c.id==="begin_settings_save").length,1)
 }finally{await first.close()}
 const saved={...receipt,state:"saved",persisted:true,applied:true}
 const bridge={operation_id:"b".repeat(32),kind:"bridge",state:"failed",error_code:"bridge_start_error",persisted:false,applied:false}
 const reopened=setup("zh-CN",{settings_saves:[{...saved,operation_id:"a".repeat(32),state:"failed"},bridge,saved]});try{
  await reopened.tab("设置")
  assert.match(reopened.root.textContent,/设置已保存并应用/)
  assert.match(reopened.root.textContent,/未能使用这个端口建立连接/)
  assert.equal(reopened.root.querySelectorAll('input[type="password"]')[1].value,"")
  assert.ok(reopened.calls.every(c=>c.id!=="begin_settings_save"))
  assert.equal(reopened.window.localStorage.length,0)
  assert.equal(reopened.window.sessionStorage.length,0)
 }finally{await reopened.close()}
})

test("a new iframe follows an unfinished save and a failed save never becomes success",async()=>{
 const pending={operation_id:"c".repeat(32),kind:"agent",state:"saving",persisted:null,applied:null}
 const h=setup("zh-CN",{settings_saves:[pending]});try{
  h.setInvoke(async(id,args)=>({result:id==="get_settings_save"?{...pending,state:"failed",persisted:false,applied:false,error_code:"invalid_agent_settings"}:{workspaces:[],total:0,next_offset:null}}))
  await h.tab("设置");assert.match(h.root.textContent,/正在保存并确认/)
  await h.click("核对这次保存状态")
  assert.match(h.root.textContent,/这次设置没有应用/)
  assert.match(h.root.textContent,/模型设置未通过检查/)
  assert.doesNotMatch(h.root.textContent,/设置已保存并应用/)
  assert.equal(h.calls.find(c=>c.id==="get_settings_save").args.operation_id,pending.operation_id)
  assert.ok(h.calls.every(c=>c.id!=="begin_settings_save"))
 }finally{await h.close()}
})

test("receipts arriving after a fresh iframe mounts do not erase a new draft",async()=>{
 const h=setup();try{
  await h.tab("设置")
  await h.type(h.root.querySelector('input[placeholder="https://example.com/v1"]'),"https://new-draft.example/v1")
  await h.type(h.root.querySelectorAll('input[type="password"]')[1],"new-draft-key")
  h.state.settings_saves=[{operation_id:"d".repeat(32),kind:"agent",state:"saved",persisted:true,applied:true}]
  h.render();await flush()
  assert.match(h.root.textContent,/上次提交的设置已保存/)
  assert.equal(h.root.querySelector('input[placeholder="https://example.com/v1"]').value,"https://new-draft.example/v1")
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"new-draft-key")
  assert.ok(h.button("设置 · 未保存",'[role="tab"]'))
  assert.ok(h.calls.every(c=>c.id!=="begin_settings_save"))
 }finally{await h.close()}
})

test("a backend restart invalidates old success without losing current settings or replaying a write",async()=>{
 const saved={operation_id:"e".repeat(32),kind:"agent",state:"saved",persisted:true,applied:true}
 const h=setup("zh-CN",{settings_saves:[saved]});try{
  await h.tab("设置");assert.match(h.root.textContent,/设置已保存并应用/)
  await h.type(h.root.querySelectorAll('input[type="password"]')[1],"keep-new-draft")
  h.setInvoke(async(id)=>{if(id==="get_settings_save")throw new Error("settings_receipt_unavailable");return {result:{workspaces:[],total:0,next_offset:null}}})
  h.state.action_nonce="restarted-backend";h.state.settings_saves=[];h.state.configuration.agent_model="persisted-model"
  h.render();await flush()
  assert.match(h.root.textContent,/尚未确认保存结果/)
  assert.match(h.root.textContent,/插件重启后，上次保存记录可能无法恢复/)
  assert.doesNotMatch(h.root.textContent,/设置已保存并应用|上次提交的设置已保存/)
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"keep-new-draft")
  await h.click("核对这次保存状态")
  assert.ok(h.calls.every(c=>c.id!=="begin_settings_save"))
 }finally{await h.close()}
 const reopened=setup("zh-CN",{action_nonce:"restarted-backend",settings_saves:[],configuration:{agent_model:"persisted-model",agent_base_url:"https://example.com/v1"}});try{
  await reopened.tab("设置")
  assert.ok([...reopened.root.querySelectorAll("input")].some(input=>input.value==="persisted-model"))
  assert.doesNotMatch(reopened.root.textContent,/设置已保存并应用|这次设置没有应用/)
  assert.ok(reopened.calls.every(c=>c.id!=="begin_settings_save"))
 }finally{await reopened.close()}
})

test("a receipt reply from the previous backend cannot restore stale success after restart",async()=>{
 const pending={operation_id:"f".repeat(32),kind:"agent",state:"saving",persisted:null,applied:null}
 const h=setup("zh-CN",{settings_saves:[pending]});try{
  let finishOld
  h.setInvoke(async(id)=>id==="get_settings_save"?await new Promise(resolve=>{finishOld=resolve}):{result:{workspaces:[],total:0,next_offset:null}})
  await h.tab("设置");h.button("核对这次保存状态").click();await flush()
  assert.equal(typeof finishOld,"function")
  h.setInvoke(async(id)=>{if(id==="get_settings_save")throw new Error("settings_receipt_unavailable");return {result:{workspaces:[],total:0,next_offset:null}}})
  h.state.action_nonce="next-process";h.state.settings_saves=[];h.render();await flush()
  assert.match(h.root.textContent,/尚未确认保存结果/)
  finishOld({result:{...pending,state:"saved",persisted:true,applied:true}});await flush()
  assert.match(h.root.textContent,/尚未确认保存结果/)
  assert.doesNotMatch(h.root.textContent,/设置已保存并应用/)
  assert.ok(h.calls.every(c=>c.id!=="begin_settings_save"))
 }finally{await h.close()}
})

test("an initial save reply from the previous backend cannot replace the restart warning",async()=>{
 const h=setup();try{
  let finishSave,submitted
  h.setInvoke(async(id,args)=>{
   if(id==="begin_settings_save") {submitted=args;return await new Promise(resolve=>{finishSave=resolve})}
   if(id==="get_settings_save")throw new Error("settings_receipt_unavailable")
   return {result:{workspaces:[],total:0,next_offset:null}}
  })
  await h.tab("设置");await h.type(h.root.querySelectorAll('input[type="password"]')[1],"keep-this-key")
  h.button("保存模型设置").click();await flush()
  h.state.action_nonce="another-process";h.state.settings_saves=[];h.render();await flush()
  assert.match(h.root.textContent,/尚未确认保存结果/)
  finishSave({result:{operation_id:submitted.operation_id,kind:"agent",state:"saved",persisted:true,applied:true}});await flush()
  assert.match(h.root.textContent,/尚未确认保存结果/)
  assert.doesNotMatch(h.root.textContent,/设置已保存并应用/)
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"keep-this-key")
  assert.equal(h.calls.filter(c=>c.id==="begin_settings_save").length,1)
 }finally{await h.close()}
})

test("a saved label without persistence and activation confirmation cannot clear a key",async()=>{
 const h=setup();try{
  h.setInvoke(async(id,args)=>({result:{operation_id:args.operation_id,kind:"agent",state:"saved",persisted:null,applied:false}}))
  await h.tab("设置");await h.type(h.root.querySelectorAll('input[type="password"]')[1],"keep-this-draft")
  await h.click("保存模型设置")
  assert.match(h.root.textContent,/尚未确认保存结果/)
  assert.equal(h.root.querySelectorAll('input[type="password"]')[1].value,"keep-this-draft")
  assert.doesNotMatch(h.root.textContent,/设置已保存并应用/)
  assert.equal(h.calls.filter(call=>call.id==="begin_settings_save").length,1)
 }finally{await h.close()}
})

test("normal advice explains an incomplete answer and lets the user copy it without another consultation",async()=>{
 const h=setup();try{
  const a=workFixture("a","YUI","当前问题")
  a.exchange.result={status:"degraded",answer:"仍可阅读的完整保留文本",agent_text:"部分结论",audit:{error_code:"agent_run_budget"},report:{export:{status:"unavailable",reason:"export_failed"}}}
  a.exchange.handoff=null;a.exchange.history_saved=false
  h.state.session={colony_id:"a-colony",load_epoch:"one"}
  h.setInvoke(async(id,args)=>({result:args.action==="list"?{workspaces:[a.workspace],total:1,next_offset:null}:args.action==="work"?a.history:a.exchange}))
  h.render();await flush()
  assert.match(h.root.textContent,/未能在等待时间内完成/)
  assert.match(h.root.textContent,/保存尚未确认/)
  assert.match(h.root.textContent,/没有生成可下载文件/)
  await h.click("复制完整回答");assert.equal(await h.window.navigator.clipboard.readText(),"仍可阅读的完整保留文本")
  assert.ok(h.calls.every(call=>call.id==="workspace_view"))
 }finally{await h.close()}
})

test("history searches send a bounded saved-data filter and colony choices are readable",async()=>{
 const h=setup();try{
  const a=workFixture("a","YUI","历史问题")
  h.state.session={colony_id:"a-colony",load_epoch:"one"}
  h.setInvoke(async(id,args)=>({result:args.action==="list"?{workspaces:[a.workspace],total:1,next_offset:null}:args.action==="work"?a.history:a.exchange}))
  await h.tab("历史记录")
  await h.type(h.root.querySelector('input[placeholder="输入问题中的词语或角色名"]'),"old question")
  await new Promise(resolve=>setTimeout(resolve,300));await flush()
  assert.equal(h.calls.filter(call=>call.args.action==="list").at(-1).args.search,"old question")
  await h.click("只看当前殖民地")
  assert.equal(h.calls.filter(call=>call.args.action==="list").at(-1).args.colony_only,true)
  assert.ok(h.calls.every(call=>call.id==="workspace_view"))
  assert.ok([...h.root.querySelectorAll("table button")].length>0)
 }finally{await h.close()}
})

test("history question buttons select once without bubbling into a second row selection",async()=>{
 const h=setup();try{
  const a=workFixture("a","Alpha","First discussion"),b=workFixture("b","Beta","Second discussion")
  h.setInvoke(async(id,args)=>{
   if(args.action==="list")return {result:{workspaces:[a.workspace,b.workspace],total:2,next_offset:null}}
   const selected=args.workspace_id==="b"?b:a
   return {result:args.action==="work"?selected.history:selected.exchange}
  })
  await h.tab("历史记录");await flush()
  await h.click("Second discussion")
  assert.match(h.root.textContent,/Second discussion 完整结果末尾/)
  assert.doesNotMatch(h.root.textContent,/First discussion 完整结果末尾/)
  assert.equal(h.calls.filter(call=>call.args.action==="turn"&&call.args.workspace_id==="b").length,1)
 }finally{await h.close()}
})

test("English and fallback locales render translated settings, guidance and troubleshooting",async()=>{
 for(const locale of ["en","fr-FR"]){
  const h=setup(locale);try{
   await flush();assert.match(h.root.textContent,/Get started with RimWorld advice/)
   await h.click("Settings",'[role="tab"]');assert.match(h.root.textContent,/Save model settings/)
   assert.match(h.root.textContent,/Advanced: time and output limits/)
   assert.doesNotMatch(h.root.textContent,/[\u4e00-\u9fff]|settings\.[a-z]|debug\.[a-z]/)
   await h.click("Troubleshooting",'[role="tab"]');assert.match(h.root.textContent,/Original readings/)
   assert.doesNotMatch(h.root.textContent,/[\u4e00-\u9fff]|debug\.[a-z]/)
  }finally{await h.close()}
 }
})
