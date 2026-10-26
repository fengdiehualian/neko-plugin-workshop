/** Passive, host-neutral Qiandengji presentation for the caller's own bot.
 * No planner, task queue, login or second Minecraft connection. Hosts can
 * explicitly enable bounded read-only status refresh after attested detection.
 * Ordinary servers stay inactive. State expires and is cleared on disconnect.
 */
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const ID=/^[a-z0-9_:.-]{1,80}$/;
const finite=(v,lo,hi)=>typeof v==='number'&&Number.isFinite(v)&&v>=lo&&v<=hi;
const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const text=(v,n)=>typeof v==='string'&&v.length>0&&v.length<=n?v:null;
const channels=['mcviewer:state','mcagent:state'];
export const itemAppearanceCatalog={fileUrl:new URL('./data-packs/qiandengji/viewer-items/catalog.json',import.meta.url).href,
  sha256:'bc79c58108deb78accf2f281de59e149ab4116ecb1131485ae4c9ee51c4012c8'};
export const presentationOptions=Object.freeze({stateRefreshMs:30_000});
// This immutable texture identifies an appearance, not server permissions or
// live skills/vitals. Reconnects need not repeat MC_GUIDE to render the actual
// same-player item. Never select it by its generic player_head ID or name.
function observedBackpackAppearance(bot) {
  const slots=bot.inventory?.slots;
  if(bot._client?.state!=='play'||!bot.entity||!Array.isArray(slots)||slots.length>128)return false;
  return slots.some(item=>{
    if(item?.name!=='player_head'||!Number.isSafeInteger(item.count)||item.count<1||!Array.isArray(item.components)||item.components.length>128||
      !Array.isArray(item.removedComponents)||item.removedComponents.some(row=>(typeof row==='string'?row:row?.type)==='profile'))return false;
    const profiles=item.components.filter(row=>row?.type==='profile');
    const properties=profiles.length===1?profiles[0].data?.properties:null;
    if(!Array.isArray(properties)||properties.length>16)return false;
    const textures=properties.filter(row=>row?.name==='textures');
    const value=textures.length===1?textures[0].value:null;
    if(typeof value!=='string'||value.length>16384||!/^[A-Za-z0-9+/]+={0,2}$/.test(value))return false;
    try{
      const url=JSON.parse(Buffer.from(value,'base64').toString('utf8'))?.textures?.SKIN?.url;
      return url==='https://textures.minecraft.net/texture/8dcc6eb40f3bada41e4339888d6d207437598bdbd175c2e731191d5a9a42d3c8'||
        url==='http://textures.minecraft.net/texture/8dcc6eb40f3bada41e4339888d6d207437598bdbd175c2e731191d5a9a42d3c8';
    }catch{return false;}
  });
}
function skills(raw, observedAt, channel) {
  if(!record(raw)||raw.schemaVersion!==1||!Array.isArray(raw.skills??[])||!Array.isArray(raw.abilities??[])
    ||(raw.skills?.length??0)>64||(raw.abilities?.length??0)>64)return null;
  let mana=null;
  if(raw.mana!=null){if(!record(raw.mana)||!finite(raw.mana.current,0,1e6)||!finite(raw.mana.max,1,1e6)||raw.mana.current>raw.mana.max)return null;
    mana={current:raw.mana.current,max:raw.mana.max};}
  const rows=[]; for(const row of raw.skills??[]){
    if(!record(row)||!ID.test(row.id??'')||!text(row.name,80)||!finite(row.level,0,1e5))return null;
    rows.push({id:row.id,name:row.name,level:row.level,xp:finite(row.xp,0,1e9)?row.xp:null,requiredXp:finite(row.requiredXp,0,1e9)?row.requiredXp:null});
  }
  const abilities=[];for(const row of raw.abilities??[]){
    if(!record(row)||!ID.test(row.id??'')||!text(row.name,80))return null;
    abilities.push({id:row.id,name:row.name,level:finite(row.level,0,1e5)?row.level:null,
      manaCost:finite(row.manaCost,0,1e6)?row.manaCost:null,
      cooldownMs:channel==='mcviewer:state'?null:finite(row.cooldownMs,0,864e5)?row.cooldownMs:null,
      ...(channel==='mcviewer:state'?{cooldownRemainingMs:finite(row.cooldownMs,0,864e5)?row.cooldownMs:null}:{}),
      ...(text(row.icon,128)&&/^[a-z0-9_:./-]+$/.test(row.icon)?{icon:row.icon}:{})});
  }
  return {schemaVersion:1,mana,skills:rows,abilities,source:'plugin',observedAt};
}
function vitals(raw, playerUuid) {
  if(!record(raw)||!finite(raw.health,0,1024)||!finite(raw.maxHealth,1,1024)||raw.health>raw.maxHealth)return null;
  const result={playerUuid,health:raw.health,maxHealth:raw.maxHealth};
  for(const [key,max] of [['food',20],['armor',1024],['absorption',1024],['oxygen',20],['experienceLevel',2147483647],['experienceProgress',1]])
    if(finite(raw[key],0,max))result[key]=raw[key];
  if(typeof raw.inWater==='boolean')result.inWater=raw.inWater;
  return result;
}
export function createPresentationObserver(bot,{now=Date.now,staleMs=12_000,autoDetect=true,stateRefreshMs=0}={}) {
  if(bot.version!=='1.21.1')throw Error('QIANDENGJI_PRESENTATION_VERSION_MISMATCH');
  let current=null,closed=false,ended=false,serverSession=null,lastSequence=0,detected=!autoDetect;
  if(!Number.isInteger(stateRefreshMs)||stateRefreshMs<0||stateRefreshMs>300_000||stateRefreshMs>0&&stateRefreshMs<30_000)throw Error('QIANDENGJI_REFRESH_INTERVAL_INVALID');
  const freshness=Math.max(staleMs,stateRefreshMs*1.5);
  let lastRefresh=-Infinity,refreshError=null;
  const registered=[];
  const own=()=>{const value=bot._client?.uuid;return UUID.test(value??'')?value.toLowerCase():null;};
  const bind=(value,source,at)=>{current={...value,profile:'qiandengji_1.21.1',source,receivedAt:at,entityId:bot.entity?.id};};
  const payload=packet=>{
    if(closed||ended||!detected||!channels.includes(packet?.channel)||!Buffer.isBuffer(packet.data)||packet.data.length>65536)return;
    let raw;try{raw=JSON.parse(packet.data.toString('utf8'));}catch{return;}
    const id=own(),session=raw?.viewerSession;
    // The same socket is insufficient when a server can send spectator data.
    if(!id||!record(session)||session.mode!=='self'||session.attached!==true||
      session.recipientUuid?.toLowerCase()!==id||session.playerUuid?.toLowerCase()!==id||session.entityId!==bot.entity?.id)return;
    const at=now(),state=skills(raw,at,packet.channel),self=vitals(raw.vitals,id);
    if(!state||!self)return;
    bind({schemaVersion:1,playerUuid:id,self,skills:state},'qiandengji_same_player_plugin_state',at);
  };
  const message=(value,position,sender)=>{
    if(closed||ended||position!=='system'||sender!=null)return;
    let line;try{line=value.toString();}catch{return;}
    if(typeof line!=='string'||Buffer.byteLength(line)>32768)return;
    const match=/^(?:\[MC 系统\] )?(MC_GUIDE|MC_STATUS) (\{.*\})$/s.exec(line);if(!match)return;
    let raw;try{raw=JSON.parse(match[2]);}catch{return;}
    const id=own(),at=now();
    if(!id||!record(raw)||raw.schemaVersion!==1||raw.scope!=='self'||raw.readOnly!==true||
      raw.playerUuid?.toLowerCase()!==id||!UUID.test(raw.sessionId??'')||!Number.isSafeInteger(raw.sequence)||raw.sequence<1||
      !finite(raw.observedAt,0,Number.MAX_SAFE_INTEGER)||Math.abs(at-raw.observedAt)>30000)return;
    if(match[1]==='MC_GUIDE'){
      if(raw.kind!=='guidance'||!text(raw.guideVersion,80)||raw.stateCommand!=='/mycli status'||raw.capabilitiesCommand!=='/mycli capabilities')return;
      if(serverSession===raw.sessionId&&raw.sequence<=lastSequence)return;
      serverSession=raw.sessionId;lastSequence=raw.sequence;detected=true;if(bot._client.state==='play')register();return;
    }
    if(raw.kind!=='state'||raw.sessionId!==serverSession||raw.sequence<=lastSequence)return;
    const self=vitals(raw,id);if(!self)return;
    let mana=null;if(raw.mana!=null||raw.maxMana!=null){
      if(!finite(raw.mana,0,1e6)||!finite(raw.maxMana,1,1e6)||raw.mana>raw.maxMana)return;
      mana={current:raw.mana,max:raw.maxMana};}
    const prepared=raw.profession?.available===true&&Array.isArray(raw.profession.prepared)?raw.profession.prepared:[];
    if(prepared.length>64||prepared.some(id=>typeof id!=='string'||!ID.test(id)))return;
    lastSequence=raw.sequence;
    // A fresh richer plugin reading takes precedence; guide text never creates
    // fake levels, cooldowns, costs or permission to execute a spell.
    if(current?.source==='qiandengji_same_player_plugin_state'&&at-current.receivedAt<=staleMs)return;
    bind({schemaVersion:1,playerUuid:id,self,skills:{schemaVersion:1,mana,skills:[],
      abilities:prepared.map(id=>({id,name:id,level:null,cooldownMs:null,manaCost:null})),source:'plugin',observedAt:raw.observedAt}},
      'qiandengji_bound_system_status',at);
  };
  function clearBody(){current=null;}
  function clear(){clearBody();serverSession=null;lastSequence=0;}
  function refresh(){
    const at=now();
    if(!stateRefreshMs||closed||ended||!detected||!serverSession||!own()||bot._client.state!=='play'||!bot.entity||bot.health===0
      ||at-lastRefresh<stateRefreshMs||current&&at-current.receivedAt<stateRefreshMs)return false;
    lastRefresh=at;
    try{bot.chat('/mycli status');refreshError=null;return true;}catch{refreshError='QIANDENGJI_STATUS_REFRESH_FAILED';return false;}
  }
  // Check deadlines cheaply; querying on a coarse interval can miss a reply's
  // exact 30s deadline by another whole interval and let the 45s state expire.
  // refresh() still enforces the read-only query rate, including retries.
  const refreshTimer=stateRefreshMs?setInterval(refresh,1_000):null;refreshTimer?.unref?.();
  const end=()=>{ended=true;clear();clearInterval(refreshTimer);};
  // Death invalidates body readings, not the identity-attested connection guide.
  bot._client.on('custom_payload',payload);bot.on('message',message);bot.on('end',end);bot.on('death',clearBody);
  // Register an existing server protocol only. This sends channel metadata,
  // never a gameplay command. Paper then sends fresh own-player state itself.
  const register=()=>{if(closed||!detected||registered.length||typeof bot._client.registerChannel!=='function')return;
    for(const channel of channels){bot._client.registerChannel(channel,['restBuffer'],true);registered.push(channel);}};
  if(bot._client.state==='play')register();else bot.once('login',register);
  const snapshot=()=>{
    if(closed||ended||!own())return null;
    if(!current||current.playerUuid!==own()||current.entityId!==bot.entity?.id||now()-current.receivedAt<0||now()-current.receivedAt>freshness)
      return observedBackpackAppearance(bot)?{schemaVersion:1,playerUuid:own(),source:'same_player_observed_item_profile',
        serverItemPackId:'qiandengji-items-1.21.1'}:null;
    return {...structuredClone(current),serverItemPackId:'qiandengji-items-1.21.1'};
  };
  return {snapshot,capabilities:()=>{const value=snapshot();return {active:!!value,id:'qiandengji',minecraftVersion:bot.version,
    source:value?.source??null,observedAt:value?.skills?.observedAt??null,skills:!!value?.skills,mana:!!value?.skills?.mana,refreshError};},refresh,
    close(){if(closed)return;closed=true;clear();clearInterval(refreshTimer);bot._client.off('custom_payload',payload);bot.off('message',message);bot.off('end',end);bot.off('death',clearBody);bot.off('login',register);
      // Do not unregister a protocol that another explicit extension may share.
      // The connection owns registration; this observer owns only its listeners.
    }};
}
