const test=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('../../../../frontend/plugin-manager/node_modules/@playwright/test');
const {serveLocalStaticAssets}=require('./browser-assets.cjs');

test('plugin keeps a local RTC lifecycle channel and closes both peers on disconnect',async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();await serveLocalStaticAssets(page.context());
    await page.goto('http://localhost:48911/static/game/teach-sister-drive/index.html');
    const result=await page.evaluate(async()=>{
      const peers=[], Native=window.RTCPeerConnection;
      window.RTCPeerConnection=class extends Native {constructor(options){super(options);peers.push(this);}};
      const {keepPanelResponsive}=await import('./companion.mjs');
      const release=await keepPanelResponsive();
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>reject(new Error('local RTC failed to connect')),4000);
        const check=()=>{if(peers.every(p=>p.connectionState==='connected')){clearTimeout(timer);resolve();}};
        peers.forEach(p=>p.onconnectionstatechange=check);check();
      });
      const connected=peers.every(p=>p.connectionState==='connected');
      release();return {connected,closed:peers.every(p=>p.connectionState==='closed'),count:peers.length};
    });
    assert.deepEqual(result,{connected:true,closed:true,count:2});
  } finally {await browser.close();}
});
