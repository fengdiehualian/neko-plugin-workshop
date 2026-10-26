import {readFile, writeFile, rename} from 'node:fs/promises';
const code = value => typeof value === 'string' && /^[A-Z][A-Z0-9_]{2,70}$/.test(value) ? value : undefined;
const types = new Set(['error', 'command.failed', 'reply.accepted', 'reply.skipped', 'playback.started', 'playback.finished', 'playback.failed', 'transcript.final', 'settings.changed']);
export class DiagnosticsLog {
  constructor(file) {this.file = file; this.records = []; this.errors = []; this.pending = Promise.resolve(); this.failed = false;}
  async load() {
    try {
      const data = JSON.parse(await readFile(this.file, 'utf8'));
      this.records = (data.records || []).slice(-300).map(row => this.clean(row)).filter(Boolean);
      this.errors = (data.errors || []).slice(-30).map(row => this.clean(row)).filter(Boolean);
    } catch (e) {if (e.code !== 'ENOENT') this.failed = true;}
  }
  clean(event) {
    if (!types.has(event?.type)) return null;
    const row = {type: event.type, at_ms: Number.isSafeInteger(event.at_ms) ? event.at_ms : Date.now()};
    if (code(event.code)) row.code = event.code;
    if (['wake_name', 'muted', 'identity'].includes(event.reason)) row.reason = event.reason;
    if (['outbound_rtp_verified', 'sender_queue_drained'].includes(event.meaning)) row.meaning = event.meaning;
    if (typeof event.diagnostic === 'boolean') row.diagnostic = event.diagnostic;
    if (typeof event.host_handled === 'boolean') row.host_handled = event.host_handled;
    if (typeof event.wake_filter_enabled === 'boolean') row.wake_filter_enabled = event.wake_filter_enabled;
    if (['host_pcm', 'cable'].includes(event.output_transport)) row.output_transport = event.output_transport;
    if (['test-output', 'test-tone', 'start', 'stop', 'account', 'leave'].includes(event.action)) row.action = event.action;
    // Never save transcripts, names, pairing links, tokens or arbitrary errors.
    return row;
  }
  record(event) {
    const row = this.clean(event); if (!row) return;
    this.records.push(row); this.records = this.records.slice(-300);
    if (row.code) {this.errors.push(row); this.errors = this.errors.slice(-30);}
    const body = JSON.stringify({records: this.records, errors: this.errors});
    this.pending = this.pending.catch(() => {}).then(async () => {
      await writeFile(this.file + '.tmp', body, {mode: 0o600});
      await rename(this.file + '.tmp', this.file); this.failed = false;
    }).catch(() => {this.failed = true;});
  }
}
