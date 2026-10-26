import test from 'node:test';
import assert from 'node:assert/strict';
import {executeCommand} from '../ui/command-client.mjs';

const base = {requestId:'one',wait:async()=>{},isCurrent:()=>true};
test('lost POST response reconciles the original executed ID without reissuing', async () => {
  let sent=0;
  const result = await executeCommand({...base,post:async()=>{sent++;throw new Error('response lost');},
    lookup:async id=>({ok:true,status:'executed',request_id:id})});
  assert.equal(result.status,'executed');
  assert.equal(sent,1);
});
test('pending receipt is polled until execution is confirmed', async () => {
  let lookups=0;
  const result=await executeCommand({...base,post:async()=>({ok:false,status:'queued',request_id:'one'}),
    lookup:async()=>({status:++lookups===2?'executed':'executing',request_id:'one'})});
  assert.equal(result.status,'executed');assert.equal(lookups,2);
});
test('definitive rejection stays rejection without reissuing', async () => {
  let lookups=0;
  const result=await executeCommand({...base,post:async()=>{throw Object.assign(new Error('reject'),
    {receipt:{status:'rejected',request_id:'one'}});},lookup:async()=>{lookups++;}});
  assert.equal(result.status,'rejected');assert.equal(lookups,0);
});
test('unknown outcome is not reported as a confirmed failure', async () => {
  await assert.rejects(executeCommand({...base,attempts:2,post:async()=>{throw new Error('offline');},
    lookup:async()=>{throw new Error('unknown_request_id');}}),error=>error.uncertain===true);
});
test('another request receipt cannot confirm this operation', async () => {
  await assert.rejects(executeCommand({...base,post:async()=>({status:'executed',request_id:'other'}),
    lookup:async()=>{throw new Error('must not poll wrong ID');}}),error=>error.uncertain===true);
});
test('scene replacement stops receipt polling', async () => {
  let lookups=0;
  await assert.rejects(executeCommand({...base,isCurrent:()=>false,post:async()=>({status:'queued',request_id:'one'}),
    lookup:async()=>{lookups++;}}),error=>error.cancelled===true);
  assert.equal(lookups,0);
});
