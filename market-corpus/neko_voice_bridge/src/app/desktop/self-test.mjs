import {readFile, writeFile} from 'node:fs/promises';
import {clipboard} from 'electron';
import path from 'node:path';
import {APP_VERSION} from '../bridge/release.mjs';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function runSelfTest({account, dashboard, engine, adapter, cable, dataDir}) {
  const previousConfig = JSON.parse(await readFile(path.join(dataDir,'settings.json'),'utf8'));
  const prematureFinish = await dashboard.webContents.executeJavaScript("nekoBridge.command('setup-finish')");
  if (prematureFinish.ok) throw new Error('SETUP_ACCEPTED_WITHOUT_CONNECTION');
  const originalWrite = clipboard.writeText;
  let code = '';
  try {
    clipboard.writeText = text => {code = text;};
    const result = await dashboard.webContents.executeJavaScript('nekoBridge.command("copy-code")');
    const pairing = {bridge_token: new URLSearchParams(new URL(code).hash.slice(1)).get('token')};
    if (!result.ok || !result.data.copied || !code.startsWith('http://127.0.0.1:') || pairing.bridge_token !== engine.config.bridge_token ||
        'wake_words' in pairing || 'asr' in pairing || 'tts' in pairing) throw new Error('PAIRING_CODE_FAILED');
  } finally {clipboard.writeText = originalWrite;}
  for (const enabled of [false, true]) {
    const setting = await dashboard.webContents.executeJavaScript(`nekoBridge.command('wake-filter',{enabled:${enabled}})`);
    if (!setting.ok || engine.status().wake_filter_enabled !== enabled || JSON.parse(await readFile(path.join(dataDir,'settings.json'),'utf8')).wake_filter_enabled !== enabled) throw new Error('WAKE_FILTER_PERSISTENCE_FAILED');
  }
  console.log('SELF_TEST: starting local WebRTC pairs');
  // The audio fixture has no real plugin. Compatibility is tested separately.
  engine.config.require_plugin_version = false;
  engine.providers = {transcribe: async () => '猫娘你好〔本机合成音频〕',
    synthesize: async () => Int16Array.from({length: 24000}, (_, i) => Math.round(Math.sin(i * 2 * Math.PI * 660 / 48000) * 8000))};
  await account.webContents.executeJavaScript(`(async () => {
    const permission = await navigator.permissions.query({name:'microphone'});
    const camera = await navigator.permissions.query({name:'camera'});
    const devices = await navigator.mediaDevices.enumerateDevices();
    if (permission.state !== 'granted' || camera.state !== 'denied') throw new Error('VIRTUAL_PERMISSION_SCOPE_FAILED');
    const inputs = devices.filter(d => d.kind === 'audioinput');
    if (inputs.length !== 1 || !inputs[0].label.includes('内置虚拟麦克风')) throw new Error('VIRTUAL_DEVICE_ENUMERATION_FAILED');
    const probeMic = await navigator.mediaDevices.getUserMedia({audio:{deviceId:{exact:inputs[0].deviceId}}});
    if (probeMic.getAudioTracks()[0].getSettings().deviceId !== inputs[0].deviceId) throw new Error('VIRTUAL_DEVICE_SELECTION_FAILED');
    probeMic.getTracks().forEach(t=>t.stop());
    const peers = [], contexts = [], iceFailures = [], candidateKinds = [];
    function loopbackCandidate(candidate) {
      // Both peers live in this process; do not route fixture packets through
      // the current VPN/default interface or depend on LAN multicast.
      const value = candidate.toJSON(), fields = value.candidate.split(' ');
      fields[4] = '127.0.0.1'; value.candidate = fields.join(' ');
      return value;
    }
    async function connect(stream, observedSender = false) {
      const SenderPeer = observedSender ? RTCPeerConnection : __nekoVoiceTestPeer;
      const a = new SenderPeer({iceServers: []}), b = new RTCPeerConnection({iceServers: []});
      peers.push(a, b);
      a.onicecandidate = e => {if(e.candidate) {candidateKinds.push({type:e.candidate.type, protocol:e.candidate.protocol, mdns: e.candidate.address?.endsWith('.local')}); b.addIceCandidate(loopbackCandidate(e.candidate)).catch(e=>iceFailures.push(e.name));}};
      b.onicecandidate = e => {if(e.candidate) {candidateKinds.push({type:e.candidate.type, protocol:e.candidate.protocol, mdns: e.candidate.address?.endsWith('.local')}); a.addIceCandidate(loopbackCandidate(e.candidate)).catch(e=>iceFailures.push(e.name));}};
      const remote = new Promise(resolve => b.addEventListener('track', e => resolve(e.track), {once:true}));
      stream.getTracks().forEach(t => a.addTrack(t, stream));
      await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
      await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
      return remote;
    }
    const ctx = new AudioContext({sampleRate:48000}); contexts.push(ctx); await ctx.resume();
    const output = ctx.createMediaStreamDestination(), oscillator = ctx.createOscillator();
    oscillator.frequency.value = 440; oscillator.connect(output);
    const sourceTrack = output.stream.getAudioTracks()[0];
    globalThis.__nekoVoiceTestSignal('fixture-scope', {request:true,id:1,method:'join',data:{}}, true);
    globalThis.__nekoVoiceTestSignal('fixture-scope', {response:true,id:1,data:{peers:[{id:'fixture-person',displayName:'小明'}]}});
    globalThis.__nekoVoiceTestSignal('fixture-scope', {method:'newConsumer', data:{id:sourceTrack.id, peerId:'fixture-person', kind:'audio'}});
    const remoteSource = await connect(output.stream);
    const mic = await navigator.mediaDevices.getUserMedia({audio:true});
    // KOOK web adds a separate WebAudio source/gain/destination before RTP.
    const micNode = ctx.createMediaStreamSource(mic), micGain = ctx.createGain(), processedMic = ctx.createMediaStreamDestination();
    micNode.connect(micGain); micGain.connect(processedMic);
    const remoteMic = await connect(processedMic.stream, true);
    const node = ctx.createMediaStreamSource(new MediaStream([remoteMic]));
    const probe = ctx.createScriptProcessor(1024,1,1), quiet = ctx.createGain();quiet.gain.value=0;
    globalThis.__outputEnergy = 0;
    probe.onaudioprocess = e => {for(const s of e.inputBuffer.getChannelData(0)) globalThis.__outputEnergy += s*s;};
    node.connect(probe);probe.connect(quiet);quiet.connect(ctx.destination);
    globalThis.__fixture = {gain(value){micGain.gain.value=value;}, mute(value){processedMic.stream.getAudioTracks()[0].enabled = !value;}, async status(){const stats=[];for(const p of peers){for(const r of (await p.getStats()).values())if(['media-source','outbound-rtp','inbound-rtp'].includes(r.type)&&r.kind==='audio')stats.push({type:r.type,energy:r.totalAudioEnergy,samples:r.totalSamplesReceived,packets:r.packetsSent||r.packetsReceived,audio:r.audioLevel});}return {iceFailures, candidateKinds, connections: peers.map(p=>({state:p.connectionState,ice:p.iceConnectionState})), context_state:ctx.state, current_time:ctx.currentTime, source:{enabled:sourceTrack.enabled, muted:sourceTrack.muted},remote:{enabled:remoteSource.enabled,muted:remoteSource.muted},stats};}, speak(){oscillator.start();setTimeout(()=>oscillator.stop(),1800);}, async close(){probe.onaudioprocess=null;node.disconnect();probe.disconnect();quiet.disconnect();peers.forEach(p=>p.close());micNode.disconnect();micGain.disconnect();processedMic.stream.getTracks().forEach(t=>t.stop());mic.getTracks().forEach(t=>t.stop());output.stream.getTracks().forEach(t=>t.stop());await ctx.close();}};
  })()`, true);
  console.log('SELF_TEST: tracks negotiated');
  for (let i = 0; i < 60 && !adapter.connected; i++) await delay(100);
  if (!adapter.connected) {console.log('SELF_TEST_RTC', JSON.stringify(await account.webContents.executeJavaScript('__fixture.status()'))); throw new Error('CHROMIUM_WEBRTC_NOT_CONNECTED');}
  await engine.join();
  await account.webContents.executeJavaScript('__fixture.speak()', true);
  let transcript;
  for (let i = 0; i < 100; i++) {
    transcript = engine.events.find(e => e.type === 'transcript.final');
    if (transcript) break;
    await delay(100);
  }
  if (!transcript || transcript.speaker.display_name !== '小明' || !transcript.speaker.audio_source_verified) {
    console.log('SELF_TEST_DIAGNOSTICS', JSON.stringify({stats: adapter.stats, mapped: [...adapter.mapped], events: engine.events.slice(-8), browser: await account.webContents.executeJavaScript('(async()=>({observer:__nekoVoice.status(),fixture:await __fixture.status()}))()')}));
    throw new Error('REAL_CHROMIUM_AUDIO_MAPPING_FAILED');
  }
  await engine.reply({utterance_id: transcript.utterance_id, epoch: transcript.epoch, text: '本机测试回复'});
  await delay(300);
  const energy = await account.webContents.executeJavaScript('__outputEnergy');
  if (!(energy > 1)) throw new Error('VIRTUAL_MIC_WEBRTC_OUTPUT_FAILED');
  await account.webContents.executeJavaScript('__fixture.mute(true)', true);
  let mutedRejected = false;
  try {await engine.testOutput(true);} catch (e) {mutedRejected = e.message === 'KOOK_MICROPHONE_MUTED';}
  if (!mutedRejected) throw new Error('MUTED_SENDER_NOT_DETECTED');
  await account.webContents.executeJavaScript('__fixture.mute(false)', true);
  await engine.testOutput(true);
  if (!adapter.stats.last_output?.rtp_verified) throw new Error('OUTBOUND_RTP_NOT_VERIFIED');
  const previousPlugin = engine.plugin;
  engine.plugin = {version:APP_VERSION, character:{name:'猫娘',revision:'fixture'}, seen_at:Date.now(), worker_ready:true};
  engine.providers.status = () => ({connected:true});
  try {
    for (const action of ['setup-output','setup-finish']) {
      const response = await dashboard.webContents.executeJavaScript(`nekoBridge.command(${JSON.stringify(action)})`);
      if (!response.ok) throw new Error('SETUP_COMPLETION_FAILED');
    }
    const saved = JSON.parse(await readFile(path.join(dataDir,'settings.json'),'utf8'));
    if (saved.setup_completed !== 1 || saved.bridge_token !== previousConfig.bridge_token) throw new Error('SETUP_DAMAGED_EXISTING_SETTINGS');
  } finally {engine.plugin=previousPlugin;delete engine.providers.status;}
  await account.webContents.executeJavaScript('__fixture.gain(0)', true);
  let gainRejected = false;
  try {await engine.testOutput(true);} catch (e) {gainRejected = e.message === 'KOOK_OUTPUT_SILENT';}
  if (!gainRejected || !(adapter.stats.last_output?.input_peak > 0) || adapter.stats.last_output?.probe_energy > 1e-10) {console.log('SILENT_PROCESSING_DIAG', JSON.stringify(adapter.stats.last_output)); throw new Error('SILENT_PROCESSING_NOT_DETECTED');}
  await account.webContents.executeJavaScript('__fixture.gain(1)', true);
  await engine.testOutput(true);
  let silenceRejected = false;
  try {await adapter.play(new Int16Array(4800), new AbortController().signal);} catch (e) {silenceRejected = e.message === 'REPLY_AUDIO_SILENT';}
  if (!silenceRejected) throw new Error('SILENT_REPLY_NOT_DETECTED');
  console.log('SELF_TEST: cable renderer / IPC / buffered RTC output');
  await cable.open();
  await cable.window.webContents.executeJavaScript(`(async () => {
    const mic=await navigator.permissions.query({name:'microphone'}), camera=await navigator.permissions.query({name:'camera'});
    if(mic.state!=='granted'||camera.state!=='denied') throw new Error('CABLE_NATIVE_PERMISSION_SCOPE');
    const inputs=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='audioinput'&&d.label.includes('Fake'));
    if(!inputs.length) throw new Error('CABLE_NATIVE_DEVICE_LABELS_HIDDEN');
    // --self-test forces Chromium fake capture. Never probe a real microphone.
    const probe=await navigator.mediaDevices.getUserMedia({video:false,audio:{deviceId:{exact:inputs[0].deviceId}}});
    probe.getTracks().forEach(t=>t.stop());
  })()`);
  await cable.window.webContents.executeJavaScript(`(() => {
    navigator.mediaDevices.enumerateDevices = async () => [
      {kind:'audioinput', deviceId:'physical-test', label:'Physical microphone'},
      {kind:'audioinput', deviceId:'fixture-cable', label:'CABLE Output (VB-Audio Virtual Cable)'}];
    navigator.mediaDevices.getUserMedia = async opts => {
      if(opts.audio?.deviceId?.exact !== 'fixture-cable' || opts.video !== false) throw new Error('PHYSICAL_CAPTURE_REQUESTED');
      const ctx = new AudioContext({sampleRate:48000}); await ctx.resume();
      const dest=ctx.createMediaStreamDestination(), tone=ctx.createOscillator(), gain=ctx.createGain();
      gain.gain.value=0; tone.connect(gain);gain.connect(dest);tone.start();
      const track=dest.stream.getAudioTracks()[0];
      Object.defineProperty(track,'getSettings',{value:()=>({deviceId:'fixture-cable'})});
      window.cableFixture={speak(){gain.gain.value=.25;setTimeout(()=>gain.gain.value=0,600);},close(){tone.stop();return ctx.close();}};
      return dest.stream;
    };
  })()`);
  const inputs = await cable.devices();
  if (inputs.length !== 1 || inputs[0].deviceId !== 'fixture-cable') throw new Error('CABLE_INPUT_FILTER_FAILED');
  let physicalRejected = false;
  try {await cable.start('physical-test');} catch (error) {physicalRejected = error.message === 'NEKO_CABLE_DEVICE_NOT_FOUND';}
  if (!physicalRejected) throw new Error('CABLE_PHYSICAL_DEVICE_ALLOWED');
  await cable.start('fixture-cable');
  for(let i=0;i<30 && !cable.frames;i++) await delay(100);
  const captured = cable.begin(new AbortController().signal);
  captured.progress('猫娘你好');
  await cable.window.webContents.executeJavaScript('cableFixture.speak()');
  for(let i=0;i<60 && !captured.progress().done;i++) await delay(100);
  const pcm = captured.samples(); captured.stop();
  const energyBeforeCable = await account.webContents.executeJavaScript('__outputEnergy');
  await engine.testOutput(false, pcm);
  await delay(200);
  if ((await account.webContents.executeJavaScript('__outputEnergy')) <= energyBeforeCable + 1 || !adapter.stats.last_output?.rtp_verified) throw new Error('CABLE_RTC_OUTPUT_FAILED');
  await cable.stop();
  const cableFrames = cable.frames; await delay(150);
  if (cable.frames !== cableFrames || cable.ready) throw new Error('CABLE_CAPTURE_NOT_STOPPED');
  await cable.window.webContents.executeJavaScript('cableFixture.close()');
  await engine.leave();
  await account.webContents.executeJavaScript('__fixture.close()', true);
  await account.webContents.executeJavaScript('for(let i=0;i<100;i++){const p=new RTCPeerConnection({iceServers:[]});p.close();}', true);
  await delay(1500);
  await dashboard.webContents.executeJavaScript("nekoBridge.command('setup-open')");
  await delay(1400);
  const screenshot = path.join(dataDir, 'dashboard.png');
  await writeFile(screenshot, (await dashboard.webContents.capturePage()).toPNG());
  await dashboard.webContents.executeJavaScript('document.querySelector("[data-action=cable-guide]").closest("section").scrollIntoView()');
  await delay(150);
  await writeFile(path.join(dataDir, 'dashboard-cable.png'), (await dashboard.webContents.capturePage()).toPNG());
  await dashboard.webContents.executeJavaScript('scrollTo(0, document.body.scrollHeight)');
  await delay(150);
  await writeFile(path.join(dataDir, 'dashboard-bottom.png'), (await dashboard.webContents.capturePage()).toPNG());
  const report = {plugin_pairing_code: true, chromium_rtc_receive: true, exact_track_name_mapping: true, virtual_mic_rtc_output: true, processed_mic_rtc_output: true, zero_gain_detected: true, silent_reply_detected: true,
    wake_filter_persistence: true,
    setup_wizard_gates_and_persistence: true, existing_pairing_preserved: true,
    simulated_cable_capture_to_rtc: true, native_capture_permission_with_fake_device: true, cable_physical_device_rejected: true, cable_stop_releases_capture: true, real_windows_cable_tested: false,
    virtual_permission_consistent: true, physical_capture_denied: true, virtual_device_selection: true, muted_sender_rejected: true, outbound_rtp_verified: true, local_test_tone: true, repeated_peer_cleanup: 100, frames: adapter.stats.frames, output_energy: Math.round(energy),
    real_kook_account: false, real_asr_llm_tts: false, screenshot};
  await writeFile(path.join(dataDir, 'self-test.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
