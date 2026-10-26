import {randomUUID} from 'node:crypto';
import {Segmenter} from './audio.mjs';
import {IdentityDirectory} from './identity.mjs';
import {pluginCompatibility} from './release.mjs';

export function errorCode(error) {
  return /^[A-Z][A-Z0-9_]{2,70}$/.test(error?.message || '') ? error.message : 'OPERATION_FAILED';
}
export class BridgeEngine {
  constructor(config, providers) {
    this.config = config; this.providers = providers;
    this.identity = new IdentityDirectory(); this.sequence = 0; this.events = [];
    this.state = 'READY'; this.listening = true; this.muted = false;
    this.pending = new Map(); this.asrQueue = []; this.asrRunning = false;
    this.controller = new AbortController(); this.replyController = null; this.replyBusy = false;
    this.segmenter = new Segmenter(s => this.enqueueSegment(s), config.vad || {});
    this.mode = config.mode || 'kook'; this.trackOwners = new Map();
    this.instanceId = randomUUID(); this.plugin = null;
  }
  attach(adapter) {this.adapter = adapter;}
  emit(type, payload = {}) {
    const event = {protocol: 'NVB-POC/1', event_id: randomUUID(), sequence: ++this.sequence, epoch: this.identity.epoch, type, at_ms: Date.now(), mode: this.mode, ...payload};
    this.events.push(event); if (this.events.length > 200) this.events.shift();
    return event;
  }
  status() {
    const pluginFresh = !!this.plugin && Date.now() - this.plugin.seen_at < 4000;
    return {protocol: 'NVB-POC/1', instance_id: this.instanceId, state: this.state, mode: this.mode, epoch: this.identity.epoch, sequence: this.sequence,
      plugin: {connected: pluginFresh && this.plugin.worker_ready && !!this.providers.status?.().connected,
        character: pluginFresh ? this.plugin.character : null, error: pluginFresh ? this.plugin.error : null,
        compatibility: pluginCompatibility(pluginFresh ? this.plugin.version : null)},
      rtc_connected: !!this.adapter?.connected,
      room: {platform: this.mode, tenant_id: this.config.kook?.guild_id || '', channel_id: this.config.kook?.channel_id || 'demo'},
      listening: this.listening, muted: this.muted, reply_busy: this.replyBusy, asr_queue: this.asrQueue.length,
      wake_filter_enabled: this.config.wake_filter_enabled !== false,
      speech: this.providers.status?.() || {mode: 'external'},
      members: [...this.identity.peers.values()].map(p => this.identity.resolve(p.peer_id)),
      capabilities: {platform_identity_verified: false, receive_tested: false,
        asr_configured: !!this.providers.status?.().connected || !!this.config.asr?.url,
        tts_configured: !!this.providers.status?.().connected || !!this.config.tts?.url}};
  }
  readEvents(after) {
    if (!Number.isSafeInteger(after) || after < 0) throw new Error('INVALID_CURSOR');
    return {epoch: this.identity.epoch, cursor: this.sequence, gap: after > this.sequence || (this.events.length > 0 && after < this.events[0].sequence - 1), events: this.events.filter(e => e.sequence > after)};
  }
  heartbeat(data) {
    if (typeof data.session_id !== 'string' || data.session_id.length > 100 || !data.session_id ||
        typeof data.character?.name !== 'string' || !data.character.name || data.character.name.length > 160 ||
        typeof data.character?.revision !== 'string' || data.character.revision.length > 100) throw new Error('INVALID_PLUGIN_HEARTBEAT');
    if (this.plugin && (this.plugin.session_id !== data.session_id || this.plugin.character.revision !== data.character.revision)) {
      // Keep the channel mapping but discard all work for the previous character.
      this.controller.abort(); this.controller = new AbortController(); this.cancelReply();
      this.segmenter.clear(); this.asrQueue = []; this.pending.clear();
    }
    this.plugin = {session_id: data.session_id, seen_at: Date.now(), worker_ready: data.worker_ready === true,
      version: pluginCompatibility(data.plugin_version).actual,
      character: {name: data.character.name, revision: data.character.revision},
      error: typeof data.error === 'string' && /^[A-Z0-9_]{1,80}$/.test(data.error) ? data.error : null};
    return this.status();
  }
  async unlinkPlugin(sessionId) {
    if (this.plugin && this.plugin.session_id !== sessionId) throw new Error('STALE_PLUGIN_SESSION');
    this.plugin = null; this.providers.disconnect?.(); await this.leave(); return this.status();
  }
  async checkPluginLink() {
    if (this.plugin && Date.now() - this.plugin.seen_at >= 4000) {
      this.plugin = null; this.providers.disconnect?.(); await this.leave();
    }
  }
  rotate() {
    this.controller.abort(); this.controller = new AbortController(); this.cancelReply();
    this.identity.reset(); this.segmenter.clear(); this.trackOwners.clear(); this.pending.clear(); this.asrQueue = [];
  }
  async join() {
    if (this.state !== 'READY') throw new Error('NOT_READY');
    if (this.config.require_plugin_version && !pluginCompatibility(this.plugin?.version).ok) throw new Error('PLUGIN_VERSION_MISMATCH');
    if (this.outputChanging) throw new Error('OUTPUT_ROUTE_CHANGING');
    if (this.providers.cable && !this.providers.cable.ready) throw new Error('NEKO_CABLE_NOT_READY');
    this.rotate(); this.state = 'JOINING'; this.emit('connection.state', {state: this.state});
    const epoch = this.identity.epoch;
    let timer;
    try {
      await Promise.race([this.adapter.join(), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('JOIN_TIMEOUT')), 20000);
      })]);
      if (epoch !== this.identity.epoch) throw new Error('SESSION_CHANGED');
      this.state = 'ACTIVE'; this.emit('connection.state', {state: this.state});
    } catch (e) {
      if (epoch === this.identity.epoch) await this.leave();
      throw e;
    } finally {clearTimeout(timer);}
  }
  async leave() {
    this.state = 'LEAVING'; this.rotate();
    try {await this.adapter?.close();} finally {this.state = 'READY'; this.emit('connection.state', {state: 'READY'});}
  }
  peer(id, name) {
    if (!['ACTIVE', 'JOINING'].includes(this.state)) return;
    try {this.identity.upsert(id, name); this.emit('member.updated', {speaker: this.identity.resolve(id)});} catch {this.emit('error', {code: 'INVALID_PEER'});}
  }
  removePeer(id) {
    if (this.replyPeer === id) this.cancelReply();
    this.identity.remove(id);
    for (const [track, owner] of this.trackOwners) if (owner === id) this.trackClosed(track);
    this.asrQueue = this.asrQueue.filter(s => s.metadata.speaker.peer_id !== id);
    for (const [key, value] of this.pending) if (value.speaker.peer_id === id) this.pending.delete(key);
    this.emit('member.left', {peer_id: id});
  }
  trackClosed(id) {this.trackOwners.delete(id); this.segmenter.drop(id);}
  bind(peer, user, nickname, epoch) {
    this.identity.bind(peer, user, nickname, epoch);
    // Buffered or in-flight audio received before a binding must not change identity retroactively.
    for (const [track, owner] of this.trackOwners) if (owner === peer) this.segmenter.drop(track);
    this.emit('member.updated', {speaker: this.identity.resolve(peer)});
  }
  audio(peer, track, data) {
    if (this.state !== 'ACTIVE' || !this.listening) return;
    try {
      if (this.trackOwners.has(track) && this.trackOwners.get(track) !== peer) this.segmenter.drop(track);
      this.trackOwners.set(track, peer);
      if (!this.identity.peers.has(peer)) this.identity.upsert(peer);
      this.segmenter.feed(track, data, {epoch: this.identity.epoch, speaker: this.identity.resolve(peer), track});
    } catch (e) {this.emit('error', {code: errorCode(e)});}
  }
  enqueueSegment(segment) {
    if (this.state !== 'ACTIVE' || !this.listening) return;
    this.emit('utterance.ready', {speaker: segment.metadata.speaker, duration_ms: Math.round(segment.samples.length * 1000 / segment.sampleRate)});
    if (this.asrQueue.length >= 12) {this.emit('error', {code: 'ASR_BACKPRESSURE'}); return;}
    this.asrQueue.push(segment); void this.drainAsr();
  }
  segmentCurrent(s) {
    const m = s.metadata;
    return this.state === 'ACTIVE' && this.listening && m.epoch === this.identity.epoch && this.trackOwners.get(m.track) === m.speaker.peer_id && this.identity.peers.get(m.speaker.peer_id)?.presence_id === m.speaker.presence_id;
  }
  async drainAsr() {
    if (this.asrRunning) return;
    this.asrRunning = true;
    try {
      while (this.asrQueue.length) {
        const segment = this.asrQueue.shift(), signal = this.controller.signal;
        if (!this.segmentCurrent(segment) || Date.now() - segment.endedAt > 10000) continue;
        try {
          const result = await this.providers.transcribe(segment, signal);
          const native = typeof result === 'object' && result?.host_reply;
          const text = native ? result.text : result;
          if (signal.aborted || !text || !this.segmentCurrent(segment)) continue;
          const id = randomUUID(), now = Date.now(); this.prune(now);
          if (this.pending.size >= 50) {this.emit('error', {code: 'TURN_BACKPRESSURE'}); continue;}
          const payload = {utterance_id: id, speaker: segment.metadata.speaker, text, started_at_ms: Math.round(segment.startedAt), ended_at_ms: Math.round(segment.endedAt)};
          this.pending.set(id, {...payload, epoch: this.identity.epoch, expires: now + 60000, replied: false});
          this.emit('transcript.final', {...payload, host_handled: !!native, wake_filter_enabled: this.config.wake_filter_enabled !== false,
            ...(result?.output_transport ? {output_transport: result.output_transport} : {})});
          if (native) {
            const speaker = payload.speaker;
            const namedTrack = speaker.identity_verified || (speaker.identity_source === 'rtc_signaling' && speaker.audio_source_verified);
            const wake = this.plugin?.character?.name;
            if (namedTrack && (this.config.wake_filter_enabled === false || (wake && text.includes(wake))) && !this.muted) {
              await this.reply({utterance_id: id, epoch: this.identity.epoch, text: native.text || '语音回复'}, native.pcm);
            } else {
              this.emit('reply.skipped', {utterance_id: id, reason: !namedTrack ? 'identity' : this.muted ? 'muted' : 'wake_name'});
            }
          }
        } catch (e) {if (!signal.aborted) this.emit('error', {code: errorCode(e)});}
      }
    } finally {this.asrRunning = false;}
  }
  prune(now = Date.now()) {for (const [k, v] of this.pending) if (v.expires < now) this.pending.delete(k);}
  setListening(value) {
    if (typeof value !== 'boolean') throw new Error('BOOLEAN_REQUIRED');
    this.listening = value;
    if (!value) {this.controller.abort(); this.controller = new AbortController(); this.segmenter.clear(); this.asrQueue = []; this.pending.clear(); this.cancelReply();}
  }
  setMuted(value) {
    if (typeof value !== 'boolean') throw new Error('BOOLEAN_REQUIRED');
    this.muted = value; if (value) this.cancelReply();
  }
  setWakeFilter(value) {
    if (typeof value !== 'boolean') throw new Error('BOOLEAN_REQUIRED');
    this.config.wake_filter_enabled = value;
    // A rule change applies to new utterances, never releases an old reply.
    this.controller.abort(); this.controller = new AbortController(); this.cancelReply();
    this.segmenter.clear(); this.asrQueue = []; this.pending.clear();
    this.emit('settings.changed', {wake_filter_enabled: value});
  }
  cancelReply() {this.replyController?.abort(); this.adapter?.cancel();}
  async testOutput(localTone = false, capturedPcm = null) {
    if (this.state !== 'ACTIVE') throw new Error('NOT_READY');
    const id = randomUUID();
    this.pending.set(id, {epoch: this.identity.epoch, expires: Date.now() + 60000, replied: false,
      speaker: {peer_id: null}, diagnostic: true});
    try {
      const tone = capturedPcm || (localTone ? Int16Array.from({length: 48000}, (_, i) => {
        const envelope = Math.min(1, i / 480, (47999 - i) / 480);
        return Math.round(Math.sin(i * 2 * Math.PI * 660 / 48000) * 6000 * envelope);
      }) : null);
      return await this.reply({utterance_id: id, epoch: this.identity.epoch, text: '你好，这是一条 KOOK 频道语音测试。'}, tone);
    } finally {this.pending.delete(id);}
  }
  async reply({utterance_id, epoch, text}, hostPcm = null) {
    this.prune();
    if (this.state !== 'ACTIVE' || epoch !== this.identity.epoch) throw new Error('STALE_SESSION');
    const turn = this.pending.get(utterance_id);
    if (!turn || turn.epoch !== epoch) throw new Error('UNKNOWN_OR_EXPIRED_TURN');
    if (turn.replied) throw new Error('ALREADY_REPLIED');
    if (this.muted) throw new Error('OUTPUT_MUTED');
    if (this.replyBusy) throw new Error('REPLY_BUSY');
    if (typeof text !== 'string' || !text.trim() || text.length > 1000) throw new Error('INVALID_REPLY');
    turn.replied = true; this.replyBusy = true; this.replyPeer = turn.speaker.peer_id;
    const controller = this.replyController = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]);
    const replyId = randomUUID(); this.emit('reply.accepted', {reply_id: replyId, utterance_id, diagnostic: !!turn.diagnostic});
    try {
      const pcm = hostPcm || await this.providers.synthesize(text, signal);
      signal.throwIfAborted();
      if (epoch !== this.identity.epoch || this.state !== 'ACTIVE' || this.pending.get(utterance_id) !== turn) throw new Error('STALE_SESSION');
      this.emit('playback.started', {reply_id: replyId, diagnostic: !!turn.diagnostic});
      const receipt = await this.adapter.play(pcm, signal);
      signal.throwIfAborted();
      this.emit('playback.finished', {reply_id: replyId, diagnostic: !!turn.diagnostic, meaning: receipt?.rtp_verified ? 'outbound_rtp_verified' : 'sender_queue_drained'});
      return {reply_id: replyId};
    } catch (e) {
      this.emit('playback.failed', {reply_id: replyId, code: signal.aborted ? 'CANCELLED_OR_TIMEOUT' : errorCode(e)});
      throw new Error(signal.aborted ? 'CANCELLED_OR_TIMEOUT' : errorCode(e));
    } finally {this.replyBusy = false; this.replyController = null; this.replyPeer = null;}
  }
}
