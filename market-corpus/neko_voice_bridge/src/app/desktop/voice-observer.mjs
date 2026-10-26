// Serialized into Chromium's page world before the account client runs.
// No tokens, HTTP headers, passwords, cookies or arbitrary messages leave it.
export function installVoiceObserver(binding, testMode = false) {
  if (globalThis.__nekoVoice) return;
  // Only the explicitly consented account page gets a virtual-device facade.
  if (!testMode && (globalThis !== globalThis.top || location.protocol !== 'https:' || !['www.kookapp.cn', 'kookapp.cn'].includes(location.hostname))) return;
  let enabled = false, context, microphone, nextSocket = 0;
  const consumers = new Map(), peers = new Map(), tracks = new Map(), players = new Map();
  let deviceEnumerations = 0, microphonePermissionChecks = 0;
  const joins = new Set();
  const connections = new Set();
  const emit = (type, data = {}) => {
    try {globalThis[binding](JSON.stringify({type, ...data}));} catch {}
  };
  const short = x => typeof x === 'string' && x.length <= 160 ? x : '';
  const name = p => short(p?.nickname) || short(p?.displayName) || short(p?.display_name);
  function audioContext() {
    if (!context) context = new AudioContext({sampleRate: 48000});
    return context;
  }
  async function virtualMicrophone() {
    const ctx = audioContext();
    await ctx.resume();
    microphone ||= ctx.createMediaStreamDestination();
    return new MediaStream(microphone.stream.getAudioTracks().map(t => t.clone()));
  }
  // getUserMedia is virtual-only. KOOK may process that track before publishing,
  // so inspect its actual RTP senders instead of guessing from the source id.
  function audioSenders() {
    return [...connections].filter(pc => pc.connectionState === 'connected').flatMap(pc =>
      pc.getTransceivers().filter(t => ['sendrecv', 'sendonly'].includes(t.currentDirection) && t.sender.track?.kind === 'audio')
        .map(t => t.sender));
  }
  function senderEnabled(sender) {
    const track = sender.track, encodings = sender.getParameters().encodings;
    return track?.readyState === 'live' && track.enabled && !track.muted &&
      (!encodings?.length || encodings.some(e => e.active !== false));
  }
  function outputStatus() {
    const senders = audioSenders();
    return {virtual_microphone: !!microphone, virtual_permission: true, device_enumerations: deviceEnumerations, permission_checks: microphonePermissionChecks, audio_senders: senders.length,
      enabled_senders: senders.filter(senderEnabled).length,
      microphone_state: !microphone ? 'missing' : !senders.length ? 'unpublished' : senders.some(senderEnabled) ? 'ready' : 'muted'};
  }
  async function outputCounters(senders) {
    let bytes = 0, packets = 0, energy = 0, hasEnergy = false;
    for (const sender of senders) {
      const report = await sender.getStats();
      for (const row of report.values()) {
        if (row.type !== 'outbound-rtp' || (row.kind || row.mediaType) !== 'audio' || row.isRemote) continue;
        bytes += row.bytesSent || 0; packets += row.packetsSent || 0;
        const source = report.get(row.mediaSourceId);
        if (Number.isFinite(source?.totalAudioEnergy)) {energy += source.totalAudioEnergy; hasEnergy = true;}
      }
    }
    return {bytes, packets, energy, hasEnergy};
  }
  function match(trackId) {
    // mediasoup uses Consumer.id as the remote SDP trackId. Never guess by timing or mid alone.
    const matches = [...consumers.values()].filter(c => c.id === trackId && c.name);
    return matches.length === 1 ? matches[0] : null;
  }
  function stopTrack(id) {
    const t = tracks.get(id);
    if (!t) return;
    t.processor.onaudioprocess = null;
    t.playout.pause(); t.playout.srcObject = null;
    t.source.disconnect(); t.processor.disconnect(); t.mute.disconnect();
    tracks.delete(id); emit('track.closed', {track_id: id});
  }
  function watchTrack(track, connection) {
    if (track.kind !== 'audio' || tracks.has(track.id)) return;
    const ctx = audioContext(), source = ctx.createMediaStreamSource(new MediaStream([track]));
    // Chromium's received WebRTC audio can remain undecoded until a media element
    // starts playout, even when a WebAudio source is connected. Keep an inaudible sink.
    const playout = new Audio(); playout.srcObject = new MediaStream([track]); playout.volume = 0;
    void playout.play().catch(() => emit('audio.playout_error'));
    // ScriptProcessor keeps this early compatibility probe independent of the site's worklet CSP.
    const processor = ctx.createScriptProcessor(1024, 1, 1), mute = ctx.createGain(); mute.gain.value = 0;
    source.connect(processor); processor.connect(mute); mute.connect(ctx.destination);
    const row = {source, processor, mute, playout, connection, announced: ''}; tracks.set(track.id, row);
    track.addEventListener('ended', () => stopTrack(track.id), {once: true});
    processor.onaudioprocess = event => {
      if (!enabled) return;
      const c = match(track.id);
      if (!c) return;
      const signature = c.scope + ':' + c.peer_id + ':' + c.name;
      if (row.announced !== signature) {row.announced = signature; emit('track.mapped', {...c, track_id: track.id});}
      const values = event.inputBuffer.getChannelData(0);
      const samples = Array.from(values, x => Math.max(-32768, Math.min(32767, Math.round(x * 32767))));
      emit('audio', {scope: c.scope, peer_id: c.peer_id, track_id: track.id, sample_rate: ctx.sampleRate, samples});
    };
    emit('track.seen', {track_id: track.id});
  }
  function signal(scope, message, outgoing = false) {
    if (!message || typeof message !== 'object') return;
    const requestId = scope + ':' + String(message.id);
    if (outgoing) {
      if (message.request === true && ['join', 'quickJoin', 'initSpeaking'].includes(message.method) && joins.size < 64) joins.add(requestId);
      return;
    }
    const d = message.data;
    if (!d || typeof d !== 'object') return;
    if (message.response === true && joins.delete(requestId)) {
      if (Array.isArray(d.peers)) for (const peer of d.peers.slice(0, 256)) {
        const id = short(peer.id); if (!id || !name(peer)) continue;
        peers.set(scope + ':' + id, {id, nickname: name(peer)});
        for (const c of consumers.values()) if (c.scope === scope && c.peer_id === id) c.name = name(peer);
      }
      return;
    }
    if (message.method === 'newConsumer' && d.kind === 'audio') {
      const id = short(d.id), peer = short(d.peerId);
      if (!id || !peer || consumers.size >= 64) return;
      const p = d.peerInfo || d.appData?.peerInfo || peers.get(scope + ':' + peer);
      consumers.set(scope + ':' + id, {id, peer_id: peer, name: name(p), scope});
      emit('consumer.seen', {consumer_id: id, has_name: !!name(p)});
    } else if (['newPeer', 'peerInfoChanged'].includes(message.method)) {
      const id = short(d.peerId) || short(d.id);
      if (!id || !name(d)) return;
      peers.set(scope + ':' + id, d);
      for (const c of consumers.values()) if (c.scope === scope && c.peer_id === id) c.name = name(d);
    } else if (message.method === 'consumerClosed') {
      const key = scope + ':' + short(d.consumerId), c = consumers.get(key);
      if (c) {consumers.delete(key); stopTrack(c.id);}
    } else if (message.method === 'peerClosed') {
      const id = short(d.peerId) || short(d.id); peers.delete(scope + ':' + id);
      for (const [key, c] of consumers) if (c.scope === scope && c.peer_id === id) {consumers.delete(key); stopTrack(c.id);}
      emit('peer.left', {peer_id: id, scope});
    }
  }
  const OriginalSocket = globalThis.WebSocket;
  globalThis.WebSocket = class extends OriginalSocket {
    constructor(...args) {
      super(...args);
      const scope = 'rtc-' + (++nextSocket);
      const originalSend = this.send;
      this.send = function(data) {
        if (typeof data === 'string' && data.length < 1000000) {
          try {signal(scope, JSON.parse(data), true);} catch {}
        }
        return originalSend.call(this, data);
      };
      this.addEventListener('message', event => {
        if (typeof event.data !== 'string' || event.data.length > 1000000) return;
        try {signal(scope, JSON.parse(event.data));} catch {}
      });
      this.addEventListener('close', () => {
        let used = false;
        for (const [key, c] of consumers) if (c.scope === scope) {used = true; consumers.delete(key); stopTrack(c.id);}
        if (used) emit('session.closed', {scope});
        for (const key of peers.keys()) if (key.startsWith(scope + ':')) peers.delete(key);
        for (const key of joins) if (key.startsWith(scope + ':')) joins.delete(key);
      });
    }
  };
  const OriginalPeer = globalThis.RTCPeerConnection;
  globalThis.RTCPeerConnection = class extends OriginalPeer {
    constructor(...args) {
      super(...args); connections.add(this);
      this.addEventListener('track', e => watchTrack(e.track, this));
      this.addEventListener('connectionstatechange', () => {
        if (['closed', 'failed'].includes(this.connectionState)) {
          for (const [id, row] of tracks) if (row.connection === this) stopTrack(id);
          connections.delete(this);
        }
        emit('rtc.state', {connected: [...connections].some(c => c.connectionState === 'connected')});
      });
    }
    close() {
      for (const [id, row] of tracks) if (row.connection === this) stopTrack(id);
      connections.delete(this); super.close();
      emit('rtc.state', {connected: [...connections].some(c => c.connectionState === 'connected')});
    }
  };
  // A synthetic getUserMedia stream alone is insufficient: clients first
  // query microphone permission and enumerate input devices. These three
  // surfaces must describe the SAME consented virtual device. Native Electron
  // capture remains denied, including cameras, if this facade is unavailable.
  const virtualDevice = Object.freeze({deviceId: 'default', groupId: 'neko-voice-bridge',
    kind: 'audioinput', label: '猫娘语音桥（内置虚拟麦克风）',
    toJSON() {return {deviceId: this.deviceId, groupId: this.groupId, kind: this.kind, label: this.label};}});
  const nativeEnumerate = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
  navigator.mediaDevices.enumerateDevices = async () => {
    deviceEnumerations++;
    let outputs = [];
    try {outputs = (await nativeEnumerate()).filter(d => d.kind === 'audiooutput');} catch {}
    // The default output remains valid even when denied hardware capture makes
    // Chromium omit physical devices and labels. Never expose physical inputs.
    const defaultOutput = Object.freeze({deviceId: 'default', groupId: '', kind: 'audiooutput', label: '系统默认输出',
      toJSON() {return {deviceId: this.deviceId, groupId: this.groupId, kind: this.kind, label: this.label};}});
    return [virtualDevice, defaultOutput, ...outputs.filter(d => d.deviceId !== 'default')];
  };
  if (navigator.permissions?.query) {
    const nativeQuery = navigator.permissions.query.bind(navigator.permissions);
    const permission = new EventTarget();
    Object.defineProperties(permission, {state: {value: 'granted'}, name: {value: 'microphone'}, onchange: {value: null, writable: true}});
    navigator.permissions.query = async descriptor => {
      if (descriptor?.name !== 'microphone') return nativeQuery(descriptor);
      microphonePermissionChecks++;
      return permission;
    };
  }
  // This account's microphone is intentionally virtual. Never fall back to the user's physical microphone.
  navigator.mediaDevices.getUserMedia = async constraints => {
    if (!constraints?.audio || constraints.video) throw new DOMException('Voice-only account', 'NotAllowedError');
    const stream = await virtualMicrophone();
    for (const track of stream.getAudioTracks()) {
      const settings = track.getSettings.bind(track);
      track.getSettings = () => ({...settings(), deviceId: virtualDevice.deviceId, groupId: virtualDevice.groupId});
      Object.defineProperty(track, 'label', {value: virtualDevice.label});
    }
    return stream;
  };
  const legacyCapture = (constraints, success, failure) => {navigator.mediaDevices.getUserMedia(constraints).then(success, failure);};
  navigator.getUserMedia = legacyCapture;
  navigator.webkitGetUserMedia = legacyCapture;
  globalThis.__nekoVoice = Object.freeze({
    async enable(value) {enabled = value === true; if (enabled) {for (const row of tracks.values()) row.announced = ''; await audioContext().resume();}},
    async play(id, samples) {
      const progress = {input_peak: samples.reduce((peak, sample) => Math.max(peak, Math.abs(sample)), 0)};
      const fail = code => emit('playback.failed', {id, code, ...progress});
      if (!progress.input_peak) {fail('REPLY_AUDIO_SILENT'); return;}
      if (!enabled || !microphone) {fail('VIRTUAL_MICROPHONE_NOT_READY'); return;}
      const senders = audioSenders().filter(senderEnabled);
      if (!senders.length) {fail(audioSenders().length ? 'KOOK_MICROPHONE_MUTED' : 'KOOK_MICROPHONE_NOT_PUBLISHED'); return;}
      // Register before awaiting stats so cancellation also covers preparation.
      const player = {source: null, probes: []}; players.set(id, player);
      const disposeProbes = () => {for (const p of player.probes) {p.node.onaudioprocess = null; p.source.disconnect(); p.node.disconnect(); p.mute.disconnect();} player.probes = [];};
      player.dispose = disposeProbes;
      try {
        const before = await outputCounters(senders);
        const ctx = audioContext(); await ctx.resume();
        if (!players.has(id)) return;
        // Inspect the actual published tracks AFTER KOOK's gain/processing.
        // RTP packets can contain silence, and sender.getStats() does not
        // expose media-source energy on every Chromium build.
        for (const sender of senders) {
          const row = {source: ctx.createMediaStreamSource(new MediaStream([sender.track])),
            node: ctx.createScriptProcessor(1024, 2, 1), mute: ctx.createGain(), frames: 0, energy: 0};
          player.probes.push(row); row.mute.gain.value = 0;
          row.node.onaudioprocess = event => {
            row.frames++;
            for (let ch = 0; ch < event.inputBuffer.numberOfChannels; ch++) {
              for (const sample of event.inputBuffer.getChannelData(ch)) row.energy += sample * sample;
            }
          };
          row.source.connect(row.node); row.node.connect(row.mute); row.mute.connect(ctx.destination);
        }
        const buffer = ctx.createBuffer(1, samples.length, 48000);
        buffer.copyToChannel(Float32Array.from(samples, x => x / 32768), 0);
        const source = player.source = ctx.createBufferSource(); source.buffer = buffer; source.connect(microphone);
        source.onended = async () => {
          source.disconnect();
          try {
            // Allow the encoder to drain its final buffered frames.
            await new Promise(resolve => setTimeout(resolve, 200));
            if (!players.has(id)) return;
            const after = await outputCounters(senders);
            if (!players.has(id)) return;
            const probeFrames = player.probes.reduce((n, p) => n + p.frames, 0);
            const probeEnergy = player.probes.reduce((n, p) => n + p.energy, 0);
            Object.assign(progress, {probe_frames: probeFrames, probe_energy: probeEnergy});
            disposeProbes(); players.delete(id);
            if (!senders.every(s => audioSenders().includes(s) && senderEnabled(s))) {fail('KOOK_MICROPHONE_CHANGED'); return;}
            const bytes = after.bytes - before.bytes, packets = after.packets - before.packets;
            Object.assign(progress, {bytes_sent: bytes, packets_sent: packets, source_energy: after.energy - before.energy, energy_available: before.hasEnergy && after.hasEnergy});
            if (bytes <= 0 || packets <= 0) {fail('KOOK_RTP_NOT_SENDING'); return;}
            if (!probeFrames) {fail('KOOK_OUTPUT_PROBE_UNAVAILABLE'); return;}
            if (probeEnergy <= 1e-10) {fail('KOOK_OUTPUT_SILENT'); return;}
            // This is an outbound RTP receipt, not a remote playback receipt.
            emit('playback.done', {id, rtp_verified: true, ...progress});
          } catch {disposeProbes(); if (players.delete(id)) fail('KOOK_OUTPUT_STATS_FAILED');}
        };
        source.start();
      } catch {disposeProbes(); if (players.delete(id)) fail('KOOK_OUTPUT_STATS_FAILED');}
    },
    cancel() {for (const {source, dispose} of players.values()) {dispose(); if (source) {source.onended = null; source.stop(); source.disconnect();}} players.clear();},
    status() {return {tracks: tracks.size, consumers: consumers.size, mapped: [...tracks.keys()].filter(id => match(id)).length, ...outputStatus()};},
  });
  if (testMode) {globalThis.__nekoVoiceTestSignal = signal; globalThis.__nekoVoiceTestPeer = OriginalPeer;}
  addEventListener('pagehide', () => {enabled = false; globalThis.__nekoVoice.cancel(); for (const id of [...tracks.keys()]) stopTrack(id); void context?.close();});
  emit('hook.ready');
}
