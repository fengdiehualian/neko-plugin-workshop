/** Account credentials stay inside Harness. Only UI-safe wallets cross loopback. */
import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

export function defaultDirectory() {
  const base=process.platform==='darwin'?path.join(os.homedir(),'Library/Application Support'):process.platform==='win32'?(process.env.LOCALAPPDATA||path.join(os.homedir(),'AppData/Local')):path.join(os.homedir(),'.local/share');
  return path.join(base,'NekoCatfood','bridges','deepseek_harness');
}
export function projectWallets(value) {
  if(!value||value.status!=='ready')throw Error('balance_unavailable');
  const project=rows=>{
    if(!Array.isArray(rows)||rows.length>100)throw Error('invalid_balance');
    return rows.map(row=>{
      if(!row||!['CNY','USD'].includes(row.currency)||typeof row.balance!=='string'||row.balance.length>80||!(/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(row.balance)))throw Error('invalid_balance');
      return {currency:row.currency,balance:row.balance};
    });
  };
  return {normal:project(value.value),bonus:project(value.bonusWallets)};
}
export function createBridge({account,directory,port=0,version='0.1.0'}) {
  const token=crypto.randomBytes(32).toString('hex');
  let boundPort=port,closed=false,started=false,uiSeen=0,native=false,request=null,pending=null;
  const file=path.join(directory,'connection.json');
  const clientConfig=()=>({protocol:1,url:`http://127.0.0.1:${boundPort}`,token,version});
  const authorized=header=>{
    const candidate=typeof header==='string'?Buffer.from(header):Buffer.alloc(0),expected=Buffer.from('Bearer '+token);
    return candidate.length===expected.length&&crypto.timingSafeEqual(candidate,expected);
  };
  const json=(res,status,value,origin)=>{
    res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...(origin?{'Access-Control-Allow-Origin':origin,'Vary':'Origin'}:{})});
    res.end(JSON.stringify(value));
  };
  const body=async req=>{
    let raw='';for await(const chunk of req){raw+=chunk;if(raw.length>2048)throw Error('invalid_request');}
    const value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))throw Error('invalid_request');return value;
  };
  const balance=async()=>{
    if(pending)return pending;
    pending=(async()=>{
      const state=await account.getState();
      if(state.status!=='credential-stored')return {ok:false,error:'signed_out'};
      const details=await account.getBalance({version,locale:'zh-CN',timezoneOffsetSeconds:-new Date().getTimezoneOffset()*60});
      return {ok:true,protocol:1,provider:'deepseek_harness',wallets:projectWallets(details),observed_at:Date.now()/1000};
    })();
    try{return await pending;}finally{pending=null;}
  };
  const server=http.createServer(async(req,res)=>{
    const origin=req.headers.origin;
    if(req.headers.host!==`127.0.0.1:${boundPort}`||origin&&origin!=='dsh-app://app')return json(res,403,{ok:false,error:'forbidden'});
    if(req.method==='OPTIONS'&&origin==='dsh-app://app'){
      res.writeHead(204,{'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Methods':'GET,POST,OPTIONS','Access-Control-Allow-Headers':'Authorization,Content-Type','Access-Control-Max-Age':'60','Vary':'Origin'});return res.end();
    }
    if(!authorized(req.headers.authorization))return json(res,403,{ok:false,error:'forbidden'},origin);
    try {
      const url=req.url;
      if(req.method==='GET'&&url==='/v1/status')return json(res,200,{ok:true,protocol:1,provider:'deepseek_harness',native:native&&Date.now()-uiSeen<10000},origin);
      if(req.method==='GET'&&url==='/v1/balance'){
        // The service owns account query cancellation; never expose raw exceptions.
        let timer;const result=await Promise.race([balance(),new Promise(resolve=>{timer=setTimeout(()=>resolve({ok:false,error:'timeout'}),8000);})]);clearTimeout(timer);
        return json(res,result.ok?200:503,result,origin);
      }
      if(req.method==='POST'&&url==='/v1/open'){
        const value=await body(req);
        if(Object.keys(value).length!==1||value.page!=='top-up')throw Error('invalid_request');
        if(!native||Date.now()-uiSeen>=10000)return json(res,503,{ok:false,error:'ui_unavailable'},origin);
        if(request&&['queued','loading','opened'].includes(request.state)&&Date.now()-request.created<120000)return json(res,202,{ok:true,id:request.id,payment_confirmed:false},origin);
        request={id:crypto.randomUUID(),page:'top-up',state:'queued',created:Date.now()};
        return json(res,202,{ok:true,id:request.id,payment_confirmed:false},origin);
      }
      if(req.method==='POST'&&url==='/v1/ui/poll'){
        const value=await body(req);
        if(typeof value.client!=='string'||!(/^[0-9a-f-]{36}$/.test(value.client)))throw Error('invalid_request');
        if(value.native===true){uiSeen=Date.now();native=true;}
        if(request?.owner===value.client&&request.state==='opened'&&value.active!==request.id)request.state='failed';
        const eligible=value.native===true&&request&&['queued','loading'].includes(request.state)&&Date.now()-request.created<45000;
        if(eligible&&!request.owner)request.owner=value.client;
        return json(res,200,{ok:true,command:eligible&&request.owner===value.client?{id:request.id,page:'top-up'}:null},origin);
      }
      if(req.method==='POST'&&url==='/v1/ui/result'){
        const value=await body(req);
        if(!request||value.id!==request.id||value.client!==request.owner||!['loading','opened','returned','failed'].includes(value.state))throw Error('invalid_request');
        const transitions={queued:['loading','failed'],loading:['opened','returned','failed'],opened:['returned','failed'],returned:[],failed:[]};
        if(request.state!==value.state&&!transitions[request.state].includes(value.state))throw Error('invalid_request');
        request.state=value.state;return json(res,200,{ok:true,payment_confirmed:false},origin);
      }
      if(req.method==='GET'&&/^\/v1\/requests\/[0-9a-f-]{36}$/.test(url))return json(res,request&&url.endsWith(request.id)?200:404,request&&url.endsWith(request.id)?{ok:true,state:request.state,payment_confirmed:false}:{ok:false,error:'not_found'},origin);
      return json(res,404,{ok:false,error:'not_found'},origin);
    }catch{return json(res,503,{ok:false,error:'unavailable'},origin);}
  });
  return {clientConfig,balance,
    async start(){
      if(closed)return;
      await fs.mkdir(directory,{recursive:true,mode:0o700});
      const stat=await fs.lstat(directory);if(stat.isSymbolicLink()||!stat.isDirectory()||process.getuid&&stat.uid!==process.getuid())throw Error('unsafe_directory');
      await fs.chmod(directory,0o700);
      await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
      started=true;boundPort=server.address().port;
      if(closed)return this.close();
      const temporary=file+'.'+crypto.randomUUID()+'.tmp';
      await fs.writeFile(temporary,JSON.stringify({...clientConfig(),pid:process.pid}),{mode:0o600,flag:'wx'});await fs.rename(temporary,file);
    },
    async close(){
      closed=true;if(started)await new Promise(resolve=>{server.close(resolve);server.closeAllConnections?.();});
      try{const value=JSON.parse(await fs.readFile(file,'utf8'));if(value.token===token)await fs.unlink(file);}catch{}
    },
  };
}
