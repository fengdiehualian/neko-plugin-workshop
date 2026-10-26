import {randomUUID} from 'node:crypto';
import {resample} from './audio.mjs';

// Speech services run in the N.E.K.O plugin. No model credentials cross this bridge.
export class HostSpeechProviders {
  constructor() {this.jobs = new Map(); this.lastPoll = 0; this.lastError = null; this.cable = null;}
  status() {return {mode: 'neko', output_transport: this.cable ? 'cable' : 'websocket', cable_ready: !!this.cable?.ready,
    connected: Date.now() - this.lastPoll < 4000, pending: this.jobs.size, error: this.lastError};}
  request(kind, payload, signal, capture = null) {
    signal.throwIfAborted();
    if (!this.status().connected) return Promise.reject(new Error('NEKO_PLUGIN_NOT_CONNECTED'));
    if (this.jobs.size >= 4) return Promise.reject(new Error('NEKO_SPEECH_BUSY'));
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      const finish = (error, result) => {
        clearTimeout(timer); signal.removeEventListener('abort', abort); this.jobs.delete(id);
        this.lastError = error?.message || null;
        if (error) reject(error); else resolve(result);
      };
      const abort = () => finish(new Error('NEKO_SPEECH_CANCELLED'));
      const timer = setTimeout(() => finish(new Error('NEKO_SPEECH_TIMEOUT')), 85000);
      this.jobs.set(id, {id, kind, payload, finish, claimed: false, capture});
      signal.addEventListener('abort', abort, {once: true});
    });
  }
  next(activeId) {
    this.lastPoll = Date.now();
    if (activeId) return {active: this.jobs.has(activeId), job: null};
    const row = [...this.jobs.values()].find(x => !x.claimed);
    if (!row) return {job: null};
    row.claimed = true;
    return {job: {id: row.id, kind: row.kind, ...row.payload}};
  }
  disconnect() {
    for (const row of [...this.jobs.values()]) row.finish(new Error('NEKO_PLUGIN_DISCONNECTED'));
    this.lastPoll = 0;
    return {disconnected: true};
  }
  progress(data) {
    const row = this.jobs.get(data.id);
    if (!row?.claimed || !row.capture) throw new Error('UNKNOWN_CABLE_JOB');
    if (data.text !== undefined && (typeof data.text !== 'string' || data.text.length > 4000)) throw new Error('INVALID_ASR_RESPONSE');
    return row.capture.progress(data.text?.trim());
  }
  complete(data) {
    const row = this.jobs.get(data.id);
    if (!row?.claimed) throw new Error('UNKNOWN_SPEECH_JOB');
    if (data.error) {
      const code = /^NEKO_[A-Z0-9_]{1,65}$/.test(data.error) ? data.error : 'NEKO_SPEECH_FAILED';
      row.finish(new Error(code)); return {accepted: true};
    }
    if (row.kind === 'asr') {
      if (typeof data.text !== 'string' || data.text.length > 4000) throw new Error('INVALID_ASR_RESPONSE');
      if (row.capture) {
        if (data.output_transport === 'host_pcm' && data.host_reply) {
          const reply = data.host_reply;
          try {
            if (typeof reply.text !== 'string' || reply.text.length > 1000 || !data.text.trim()) throw new Error('NEKO_HOST_INVALID_REPLY');
            row.finish(null, {text: data.text.trim(), host_reply: {text: reply.text, pcm: decodePcm(reply)}, output_transport: 'host_pcm'});
          } catch {row.finish(new Error('NEKO_HOST_INVALID_REPLY'));}
          return {accepted: true};
        }
        if (data.output_transport !== 'cable' || !data.text.trim()) {
          row.finish(new Error('NEKO_CABLE_UPDATE_PLUGIN')); return {accepted: true};
        }
        try {row.finish(null, {text: data.text.trim(), host_reply: {text: '', pcm: row.capture.samples()}, output_transport: 'cable'});}
        catch (error) {row.finish(error);}
        return {accepted: true};
      }
      if (data.host_reply !== undefined) {
        const reply = data.host_reply;
        if (!reply || typeof reply.text !== 'string' || reply.text.length > 1000) throw new Error('INVALID_HOST_REPLY');
        row.finish(null, {text: data.text.trim(), host_reply: {text: reply.text, pcm: decodePcm(reply)}});
      } else row.finish(null, data.text.trim());
    } else {
      row.finish(null, decodePcm(data));
    }
    return {accepted: true};
  }
  async transcribe(segment, signal) {
    const samples = resample(segment.samples, segment.sampleRate, 16000);
    const bytes = Buffer.alloc(samples.length * 2);
    samples.forEach((x, i) => bytes.writeInt16LE(x, i * 2));
    const capture = this.cable?.begin(signal);
    try {
      return await this.request('asr', {pcm: bytes.toString('base64'), sample_rate: 16000, host_voice_protocol: 1,
        ...(capture ? {output_transport: 'cable'} : {}), speaker: segment.metadata?.speaker || {}}, signal, capture);
    } finally {capture?.stop();}
  }
  synthesize(text, signal) {return this.request('tts', {text}, signal);}
}

function decodePcm(data) {
  if (data.sample_rate !== 48000 || typeof data.pcm !== 'string' || data.pcm.length > 3840000) throw new Error('INVALID_TTS_AUDIO');
  const bytes = Buffer.from(data.pcm, 'base64');
  if (!bytes.length || bytes.length % 2 || bytes.toString('base64') !== data.pcm) throw new Error('INVALID_TTS_AUDIO');
  const pcm = new Int16Array(bytes.length / 2);
  for (let i = 0; i < pcm.length; i++) pcm[i] = bytes.readInt16LE(i * 2);
  return pcm;
}
