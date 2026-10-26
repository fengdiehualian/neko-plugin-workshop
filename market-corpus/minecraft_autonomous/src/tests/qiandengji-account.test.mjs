import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {runAgentAccess,parseInviteLink} from '../assets/server-extensions/qiandengji/access/agent-register.mjs';

const origin='https://mc.kangqiang.site';
const helper=new URL('../assets/server-extensions/qiandengji/access/agent-register.mjs',import.meta.url);
test('official helper is byte pinned and invitation is a fragment, never a query token',async()=>{
  assert.equal(createHash('sha256').update(await fs.readFile(helper)).digest('hex'),'cfa9733c093a27093100b3c08b21de0727af24f486561a07bf5a7868d49ca5cd');
  const token='qdi_'+'i'.repeat(43);
  assert.deepEqual(parseInviteLink(origin+'/join#invite='+token),{origin,token});
  assert.throws(()=>parseInviteLink(origin+'/join?invite='+token));
});

test('official account flow persists secrets before exchange and queries uncertain activation without replay',async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'neko-qdi-'));
  const file=path.join(directory,'identity.json'),invite=path.join(directory,'invite.private');
  const token='qdi_'+'i'.repeat(43),session='qda_'+'a'.repeat(43);
  await fs.writeFile(invite,origin+'/join#invite='+token,{mode:0o600});
  const priorFetch=globalThis.fetch, outputs=[],requests=[];
  let currentState='not_created',uncertain=false;
  globalThis.fetch=async(url,options)=>{
    const saved=JSON.parse(await fs.readFile(file,'utf8'));
    requests.push({url,method:options.method});
    assert.equal(options.redirect,'error');
    assert(!url.includes(token));
    assert(saved.gamePassword&&saved.gameRequestId&&saved.clientId);
    const body=options.body&&JSON.parse(options.body);
    let result;
    if(url.endsWith('/register')){
      assert.equal(options.headers.Authorization,'Bearer '+token);
      assert.equal(body.requestId,saved.clientId);
      assert.equal(body.clientSecret,saved.clientSecret);
      assert(!('gamePassword' in body));
      result={state:'registered',username:saved.username,clientId:saved.clientId};
    }else if(url.endsWith('/sessions')){
      result={token:session,clientId:saved.clientId,username:saved.username,expiresAt:new Date(Date.now()+3600000).toISOString()};
    }else if(url.endsWith('/activate')){
      assert.equal(body.requestId,saved.gameRequestId);
      assert.equal(body.gamePassword,saved.gamePassword);
      if(uncertain)throw Error('simulated transport lost');
      currentState='pending'; result={state:currentState,username:saved.username,gameAccountCreated:false};
    }else{
      assert.equal(options.method,'GET');
      result={state:currentState,username:saved.username,gameAccountCreated:currentState==='ready',game:{ready:true}};
    }
    return {ok:true,status:200,json:async()=>result};
  };
  try{
    await runAgentAccess(['register','--username','ag_Test','--invite-url-file',invite,'--identity-file',file],v=>outputs.push(v));
    const identity=JSON.parse(await fs.readFile(file,'utf8'));
    await runAgentAccess(['activate','--identity-file',file],v=>outputs.push(v));
    assert.equal(requests.filter(r=>r.url.endsWith('/activate')).length,1);
    for(const state of ['pending','unknown','ready']){
      currentState=state;
      await runAgentAccess(['activate','--identity-file',file],v=>outputs.push(v));
    }
    assert.equal(requests.filter(r=>r.url.endsWith('/activate')).length,1);
    currentState='not_created';uncertain=true;
    await assert.rejects(runAgentAccess(['activate','--identity-file',file],()=>{}),/RESULT_UNKNOWN_KEEP_IDENTITY_AND_QUERY_STATUS/);
    currentState='unknown';
    await runAgentAccess(['status','--identity-file',file],v=>outputs.push(v));
    assert.equal(requests.filter(r=>r.url.endsWith('/activate')).length,2);
    const after=JSON.parse(await fs.readFile(file,'utf8'));
    assert.equal(after.clientId,identity.clientId);
    assert.equal(after.gameRequestId,identity.gameRequestId);
    assert.equal(after.gamePassword,identity.gamePassword);
    const text=outputs.join('\n');
    for(const secret of [token,session,identity.clientSecret,identity.gamePassword])assert(!text.includes(secret));
  }finally{
    globalThis.fetch=priorFetch;
    await fs.rm(directory,{recursive:true,force:true});
  }
});
