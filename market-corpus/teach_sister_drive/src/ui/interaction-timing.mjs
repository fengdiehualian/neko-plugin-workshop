// Bounded, in-memory measurements. Callers cannot attach dialogue or metadata.
const stages = new Set(['received','turn_accepted','state_ready','memory_ready','decision_ready',
  'action_confirmed','queue_entered','speech_adapted','speech_registered','speech_requested',
  'preparing','preparation_slow','started','playback_slow','resumed','awaiting_confirmation','feedback_pending','ended','failed','interrupted']);
const sources = new Set(['voice','text','scripted','recovery']);
const outcomes = new Set(['ended','failed','interrupted']);
const reasons = new Set(['first_audio_timeout','playback_stalled','playback_timeout','completion_confirmation_timeout',
  'feedback_failed','audio_unavailable','speech_request_failed','manual_retry','decision_failed']);

export class InteractionTimings {
  constructor({now = () => performance.now(), limit = 32} = {}) {
    this.now = now;
    this.limit = limit;
    this.sequence = 0;
    this.entries = new Map();
  }
  begin(source = 'text') {
    const id = `interaction-${++this.sequence}`;
    this.entries.set(id, {id,source:sources.has(source)?source:'text',startedAt:this.now(),events:[],outcome:''});
    while (this.entries.size > this.limit) this.entries.delete(this.entries.keys().next().value);
    this.mark(id,'received');
    return id;
  }
  mark(id, stage, reason = '') {
    const entry = this.entries.get(id);
    if (!entry || entry.outcome || !stages.has(stage) || entry.events.some(event=>event.stage===stage)) return;
    entry.events.push({stage,ms:Math.max(0,Math.round(this.now()-entry.startedAt)),
      ...(reasons.has(reason)?{reason}:{})});
  }
  finish(id, outcome, reason = '') {
    const entry = this.entries.get(id);
    if (!entry || entry.outcome || !outcomes.has(outcome)) return;
    this.mark(id,outcome,reason);
    entry.outcome = outcome;
  }
  get(id) {
    const entry = this.entries.get(id);
    return entry ? {id:entry.id,source:entry.source,outcome:entry.outcome,events:entry.events.map(event=>({...event}))} : null;
  }
  snapshot() {
    return {schema:1,unit:'ms',asrMeasured:false,entries:[...this.entries.keys()].map(id=>this.get(id))};
  }
}
