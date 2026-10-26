// One explicitly selected VB-CABLE endpoint, one turn in memory. Never save PCM.
export function isCableDevice(device) {
  return device?.kind === 'audioinput' && typeof device.deviceId === 'string' &&
    !['default', 'communications', ''].includes(device.deviceId) &&
    /^CABLE(?:-[A-D])? Output\b.*VB-Audio/i.test(device.label || '');
}

export class CableBuffer {
  constructor({now = Date.now, quietMs = 2500, waitMs = 35000, maxSamples = 48000 * 30} = {}) {
    Object.assign(this, {now, quietMs, waitMs, maxSamples});
    this.ready = false; this.peak = 0; this.frames = 0; this.lastFrame = 0; this.active = null; this.lastError = null;
  }
  status() {
    this.tick();
    return {ready: this.ready, peak: this.now() - this.lastFrame < 500 ? this.peak : 0,
      frames: this.frames, recording: !!this.active && !this.active.done,
      speech_detected: !!this.active?.heard, error: this.lastError};
  }
  setReady(value) {this.ready = value; if (!value) this.fail('NEKO_CABLE_DEVICE_LOST');}
  begin(signal) {
    signal.throwIfAborted();
    if (!this.ready || this.now() - this.lastFrame > 2000) throw new Error('NEKO_CABLE_NOT_READY');
    if (this.active) throw new Error('NEKO_CABLE_BUSY');
    const row = {chunks: [], size: 0, started: this.now(), heard: false, lastSound: 0, done: false, transcript: false};
    this.active = row; this.lastError = null;
    const abort = () => {row.chunks = []; row.done = true; row.error = 'NEKO_SPEECH_CANCELLED';};
    signal.addEventListener('abort', abort, {once: true});
    return {
      progress: text => {
        if (text && !row.transcript) {row.transcript = true; row.transcriptAt = this.now();}
        this.tick(); return {done: row.done, ...(row.error ? {error: row.error} : {})};
      },
      samples: () => {
        if (row.error) throw new Error(row.error);
        if (!row.done || !row.transcript || !row.size) throw new Error('NEKO_CABLE_INCOMPLETE');
        const pcm = new Int16Array(row.size); let offset = 0;
        for (const chunk of row.chunks) {pcm.set(chunk, offset); offset += chunk.length;}
        return pcm;
      },
      stop: () => {signal.removeEventListener('abort', abort); row.chunks = []; if (this.active === row) this.active = null;},
    };
  }
  fail(code) {this.lastError = code; if (this.active && !this.active.done) {this.active.done = true; this.active.error = code; this.active.chunks = [];}}
  tick() {
    const row = this.active, now = this.now();
    if (!row || row.done) return;
    if (!this.ready || now - this.lastFrame > 2000) return this.fail('NEKO_CABLE_DEVICE_LOST');
    if (row.transcript && row.heard && now - row.lastSound >= this.quietMs) {row.done = true; return;}
    if (now - (row.transcriptAt ?? row.started) > (row.transcript ? this.waitMs : 60000)) this.fail(row.heard ? 'NEKO_CABLE_TURN_TIMEOUT' : 'NEKO_CABLE_NO_AUDIO');
  }
  feed(samples) {
    if (!(samples instanceof Int16Array) || samples.length !== 2048) return;
    this.frames++; this.lastFrame = this.now(); this.peak = 0;
    let square = 0; for (const v of samples) {this.peak = Math.max(this.peak, Math.abs(v)); square += v * v;}
    const row = this.active;
    if (!row || row.done) return;
    // Keep 0.5 s before onset; only the selected cable's PCM enters this buffer.
    const voiced = Math.sqrt(square / samples.length) >= 80;
    if (voiced) {row.heard = true; row.lastSound = this.now();}
    row.chunks.push(samples.slice()); row.size += samples.length;
    if (!row.heard) while (row.size > 24000) row.size -= row.chunks.shift().length;
    if (row.size > this.maxSamples) return this.fail('NEKO_CABLE_AUDIO_TOO_LONG');
    this.tick();
  }
}
