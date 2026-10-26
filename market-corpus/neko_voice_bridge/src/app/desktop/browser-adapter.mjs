import {randomUUID} from 'node:crypto';

export class BrowserAdapter {
  constructor(engine, execute) {
    this.engine = engine; this.execute = execute; this.connected = false; this.armed = false;
    this.scope = null; this.mapped = new Map(); this.pending = new Map();
    this.generation = 0;
    this.stats = {hook_ready: false, frames: 0, tracks_seen: 0, consumers_seen: 0, peak: 0};
  }
  async join() {
    if (!this.connected) throw new Error('JOIN_KOOK_CHANNEL_FIRST');
    const generation = ++this.generation;
    this.mapped.clear(); this.scope = null;
    await this.execute('enable', true);
    if (generation !== this.generation) throw new Error('SESSION_CHANGED');
    this.armed = true;
  }
  async close() {this.generation++; this.armed = false; this.cancel(); this.mapped.clear(); this.scope = null; await this.execute('enable', false).catch(() => {});}
  cancel() {
    for (const row of this.pending.values()) row.reject(new Error('PLAYBACK_CANCELLED'));
    this.pending.clear(); void this.execute('cancel').catch(() => {});
  }
  async play(samples, signal) {
    signal.throwIfAborted();
    if (!this.armed || !this.connected) throw new Error('NOT_CONNECTED');
    if (!(samples instanceof Int16Array) || samples.length > 48000 * 30) throw new Error('PLAYBACK_TOO_LONG');
    const id = randomUUID();
    let abort;
    try {
      return await new Promise((resolve, reject) => {
        this.pending.set(id, {resolve, reject});
        abort = () => {this.pending.delete(id); reject(new Error('PLAYBACK_CANCELLED')); void this.execute('cancel').catch(() => {});};
        signal.addEventListener('abort', abort, {once: true});
        void this.execute('play', id, Array.from(samples)).catch(() => {this.pending.delete(id); reject(new Error('BROWSER_PLAYBACK_FAILED'));});
      });
    } finally {signal.removeEventListener('abort', abort); this.pending.delete(id);}
  }
  reset(pageGone = false) {
    this.stats.microphone_state = 'missing'; this.stats.last_output = null;
    this.connected = false; if (pageGone) {this.stats.hook_ready = false; this.stats.virtual_permission = false;} this.cancel(); this.mapped.clear(); this.scope = null;
    if (this.engine.state !== 'READY') void this.engine.leave();
  }
  receive(e) {
    if (!e || typeof e.type !== 'string') return;
    if (e.type === 'output.status') {
      this.stats.virtual_permission = e.virtual_permission === true;
      for (const key of ['device_enumerations', 'permission_checks', 'audio_senders', 'enabled_senders']) if (Number.isSafeInteger(e[key]) && e[key] >= 0) this.stats[key] = e[key];
      if (['missing', 'unpublished', 'muted', 'ready'].includes(e.microphone_state)) this.stats.microphone_state = e.microphone_state;
      return;
    }
    if (e.type === 'hook.ready') {this.stats.hook_ready = true; return;}
    if (e.type === 'rtc.state') {this.connected = e.connected === true; if (!this.connected && this.armed) this.reset(); return;}
    if (e.type === 'track.seen') {this.stats.tracks_seen++; return;}
    if (e.type === 'consumer.seen') {this.stats.consumers_seen++; return;}
    if (e.type === 'playback.done' || e.type === 'playback.failed') {
      const pending = this.pending.get(e.id); this.pending.delete(e.id);
      if (e.type === 'playback.done') {
        const receipt = {rtp_verified: e.rtp_verified === true,
          bytes_sent: Number.isSafeInteger(e.bytes_sent) ? e.bytes_sent : 0,
          packets_sent: Number.isSafeInteger(e.packets_sent) ? e.packets_sent : 0};
        if (pending) {
          this.stats.last_output = receipt;
          for (const key of ['input_peak', 'source_energy', 'probe_frames', 'probe_energy']) if (Number.isFinite(e[key]) && e[key] >= 0) this.stats.last_output[key] = e[key];
          if (typeof e.energy_available === 'boolean') this.stats.last_output.energy_available = e.energy_available;
        }
        pending?.resolve(receipt);
      } else {
        if (pending) {
          this.stats.last_output = {rtp_verified: false, code: typeof e.code === 'string' && /^[A-Z_]+$/.test(e.code) ? e.code : 'BROWSER_PLAYBACK_FAILED'};
          for (const key of ['input_peak', 'bytes_sent', 'packets_sent', 'source_energy', 'probe_frames', 'probe_energy']) if (Number.isFinite(e[key]) && e[key] >= 0) this.stats.last_output[key] = e[key];
          this.stats.last_output.energy_available = e.energy_available === true;
        }
        const codes = ['REPLY_AUDIO_SILENT', 'VIRTUAL_MICROPHONE_NOT_READY', 'KOOK_MICROPHONE_MUTED', 'KOOK_MICROPHONE_NOT_PUBLISHED',
          'KOOK_OUTPUT_PROBE_UNAVAILABLE', 'KOOK_MICROPHONE_CHANGED', 'KOOK_RTP_NOT_SENDING', 'KOOK_OUTPUT_SILENT', 'KOOK_OUTPUT_STATS_FAILED'];
        pending?.reject(new Error(codes.includes(e.code) ? e.code : 'BROWSER_PLAYBACK_FAILED'));
      }
      return;
    }
    if (!this.armed) return;
    if (e.type === 'session.closed' && e.scope === this.scope) {this.reset(); return;}
    if (e.type === 'track.closed') {
      const removed = this.mapped.get(e.track_id); this.mapped.delete(e.track_id); this.engine.trackClosed(e.track_id);
      if (removed && ![...this.mapped.values()].some(p => p.peer === removed.peer)) this.engine.removePeer(removed.peer);
      return;
    }
    if (e.type === 'peer.left' && e.scope === this.scope) {this.engine.removePeer(e.peer_id); return;}
    if (e.type === 'track.mapped') {
      if (![e.track_id, e.peer_id, e.scope, e.name].every(x => typeof x === 'string' && x.length > 0 && x.length <= 160)) return;
      if (e.id !== e.track_id) return;
      if (this.scope && this.scope !== e.scope) {this.reset(); return;}
      this.scope = e.scope;
      this.mapped.set(e.track_id, {peer: e.peer_id, scope: e.scope});
      this.engine.peer(e.peer_id, e.name.slice(0, 80));
      this.engine.identity.rtc(e.peer_id, e.name.slice(0, 80));
      this.engine.emit('member.updated', {speaker: this.engine.identity.resolve(e.peer_id)});
    } else if (e.type === 'audio') {
      const mapped = this.mapped.get(e.track_id);
      if (!mapped || mapped.peer !== e.peer_id || mapped.scope !== e.scope) return;
      if (!Array.isArray(e.samples) || e.samples.length !== 1024 || !e.samples.every(x => Number.isInteger(x) && x >= -32768 && x <= 32767)) return;
      this.stats.frames++;
      this.stats.peak = Math.max(this.stats.peak, ...e.samples.map(Math.abs));
      this.engine.audio(e.peer_id, e.track_id, {samples: Int16Array.from(e.samples), sampleRate: e.sample_rate, bitsPerSample: 16, channelCount: 1});
    }
  }
}
