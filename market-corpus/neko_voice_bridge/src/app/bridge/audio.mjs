// PCM handling is independent of the platform SDK; no microphone is opened here.
export function monoPcm(samples, channels = 1) {
  if (!(samples instanceof Int16Array) || ![1, 2].includes(channels) || samples.length % channels) {
    throw new Error('INVALID_PCM');
  }
  const result = new Int16Array(samples.length / channels);
  for (let i = 0; i < result.length; i++) {
    result[i] = channels === 1 ? samples[i] : Math.round((samples[i * 2] + samples[i * 2 + 1]) / 2);
  }
  return result;
}

export function wavEncode(samples, sampleRate) {
  const out = Buffer.alloc(44 + samples.length * 2);
  out.write('RIFF'); out.writeUInt32LE(out.length - 8, 4); out.write('WAVEfmt ', 8);
  out.writeUInt32LE(16, 16); out.writeUInt16LE(1, 20); out.writeUInt16LE(1, 22);
  out.writeUInt32LE(sampleRate, 24); out.writeUInt32LE(sampleRate * 2, 28);
  out.writeUInt16LE(2, 32); out.writeUInt16LE(16, 34); out.write('data', 36);
  out.writeUInt32LE(samples.length * 2, 40);
  samples.forEach((v, i) => out.writeInt16LE(v, 44 + i * 2));
  return out;
}

export function wavDecode(buffer) {
  if (buffer.length < 44 || buffer.toString('ascii', 0, 4) !== 'RIFF' || buffer.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('TTS_REQUIRES_PCM16_WAV');
  }
  let fmt, data;
  for (let p = 12; p + 8 <= buffer.length;) {
    const size = buffer.readUInt32LE(p + 4), name = buffer.toString('ascii', p, p + 4);
    if (p + 8 + size > buffer.length) throw new Error('TRUNCATED_WAV');
    if (name === 'fmt ' && size >= 16) fmt = buffer.subarray(p + 8, p + 8 + size);
    if (name === 'data') data = buffer.subarray(p + 8, p + 8 + size);
    p += 8 + size + (size % 2);
  }
  if (!fmt || !data || fmt.readUInt16LE(0) !== 1 || fmt.readUInt16LE(14) !== 16 || data.length % 2) {
    throw new Error('TTS_REQUIRES_PCM16_WAV');
  }
  const rate = fmt.readUInt32LE(4), channels = fmt.readUInt16LE(2);
  if (rate < 8000 || rate > 96000) throw new Error('INVALID_SAMPLE_RATE');
  const samples = Int16Array.from({length: data.length / 2}, (_, i) => data.readInt16LE(i * 2));
  return {samples: monoPcm(samples, channels), sampleRate: rate};
}

export function resample(samples, inputRate, outputRate = 48000) {
  if (inputRate === outputRate) return samples;
  const n = Math.floor(samples.length * outputRate / inputRate);
  return Int16Array.from({length: n}, (_, i) => {
    const x = i * inputRate / outputRate, a = Math.floor(x), b = Math.min(a + 1, samples.length - 1);
    return Math.round(samples[a] * (1 - (x - a)) + samples[b] * (x - a));
  });
}

// Energy gate for POC; this is not a trained speech/noise classifier.
export class Segmenter {
  constructor(onSegment, {threshold = 500, silenceMs = 700, minSpeechMs = 240, maxMs = 15000, preRollMs = 200} = {}) {
    Object.assign(this, {onSegment, threshold, silenceMs, minSpeechMs, maxMs, preRollMs});
    this.tracks = new Map();
  }
  feed(key, frame, metadata, now = Date.now()) {
    const rate = frame.sampleRate;
    if (!Number.isInteger(rate) || rate < 8000 || rate > 96000 || frame.bitsPerSample !== 16) throw new Error('INVALID_PCM_FORMAT');
    const samples = monoPcm(frame.samples, frame.channelCount);
    if (!samples.length || samples.length > rate * 0.12) throw new Error('INVALID_FRAME_SIZE');
    let t = this.tracks.get(key);
    if (t && t.rate !== rate) { this.tracks.delete(key); t = null; }
    if (!t) {
      if (this.tracks.size >= 24) throw new Error('TOO_MANY_TRACKS');
      t = {rate, meta: structuredClone(metadata), chunks: [], pre: [], preCount: 0, count: 0, speech: 0, silent: 0, last: now, active: false};
      this.tracks.set(key, t);
    }
    t.last = now;
    let energy = 0; for (const s of samples) energy += s * s;
    const voice = Math.sqrt(energy / samples.length) >= this.threshold;
    if (!t.active && !voice) {
      t.pre.push(samples); t.preCount += samples.length;
      while (t.preCount > rate * this.preRollMs / 1000 && t.pre.length > 1) t.preCount -= t.pre.shift().length;
      return;
    }
    if (!t.active) {
      t.active = true; t.chunks = t.pre; t.count = t.preCount;
      t.started = now - (t.preCount + samples.length) * 1000 / rate;
    }
    t.chunks.push(samples); t.count += samples.length;
    if (voice) {t.speech += samples.length; t.silent = 0;} else t.silent += samples.length;
    if (t.silent * 1000 / rate >= this.silenceMs || t.count * 1000 / rate >= this.maxMs) this.flush(key, now);
  }
  flush(key, now = Date.now()) {
    const t = this.tracks.get(key); this.tracks.delete(key);
    if (!t || t.speech * 1000 / t.rate < this.minSpeechMs) return;
    const samples = new Int16Array(t.count); let p = 0;
    for (const c of t.chunks) {samples.set(c, p); p += c.length;}
    this.onSegment({samples, sampleRate: t.rate, metadata: t.meta, startedAt: t.started, endedAt: now});
  }
  tick(now = Date.now()) {
    for (const [key, t] of this.tracks) if (now - t.last >= this.silenceMs) this.flush(key, t.last);
  }
  drop(key) { this.tracks.delete(key); }
  clear() { this.tracks.clear(); }
}
