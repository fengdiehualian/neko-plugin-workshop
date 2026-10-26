// Correlates host player samples, never HTTP completion alone, with Unity lines.
export const PLAYBACK_LIMITS = Object.freeze({warningMs:5000,firstAudioMs:15000,stalledMs:12000,confirmationMs:15000,totalMs:90000});

export class PlaybackTracker {
  constructor({send, onError = () => {}, onProgress = () => {}, now = Date.now, limits = PLAYBACK_LIMITS,
    setTimer = (...args) => globalThis.setTimeout(...args),
    clearTimer = (...args) => globalThis.clearTimeout(...args)}) {
    Object.assign(this, {send, onError, onProgress, now, setTimer, clearTimer, limits});
    this.jobs = new Map();
    this.hostIds = new Map();
    this.completedTurns = new Set();
  }

  begin(id, scope = null) {
    if (this.jobs.has(id)) throw new Error('duplicate speech id');
    let resolve;
    const finished = new Promise(done => { resolve = done; });
    const job = {id, scope, started:false, generated:false, latest:null, terminal:false,
      resolve, finished, sends:Promise.resolve(), createdAt:this.now(), timers:new Set(), audioTime:-1};
    this.jobs.set(id, job);
    this.progress(job, 'preparing');
    this.timer(job, () => this.progress(job, 'preparation_slow'), this.limits.warningMs);
    job.firstAudioTimer = this.timer(job, () => this.finish(job, 'failed', true, 'first_audio_timeout'), this.limits.firstAudioMs);
    return finished;
  }

  timer(job, callback, ms) {
    const timer = this.setTimer(() => { job.timers.delete(timer); if (!job.terminal) callback(); }, ms);
    job.timers.add(timer);
    return timer;
  }

  cancelTimer(job, timer) { this.clearTimer(timer); job.timers.delete(timer); }

  progress(job, phase, reason = '') {
    // Structural diagnostics only; observers cannot affect playback ownership.
    try { this.onProgress({id:job.id, scope:job.scope, phase, reason, elapsedMs:Math.max(0,this.now()-job.createdAt)}); } catch { /* Observer only. */ }
  }

  armProgressDeadline(job) {
    this.cancelTimer(job,job.slowTimer);
    this.cancelTimer(job,job.stallTimer);
    job.slowTimer = this.timer(job, () => { job.slow = true; this.progress(job, 'playback_slow'); }, this.limits.warningMs);
    job.stallTimer = this.timer(job, () => this.finish(job, 'failed', true, 'playback_stalled'), this.limits.stalledMs);
  }

  queue(job, status, state = {}) {
    const mouth = state.audioContextState === 'running' && (state.ageMs || 0) <= 750
      ? Math.max(0, Math.min(1, Number(state.mouthFrame?.rms) || 0)) : 0;
    const body = {speech_id:job.id, status, mouth, remaining_seconds:Math.max(0, Number(state.remainingSeconds) || 0)};
    // Preserve started -> playing -> ended order even when HTTP acknowledgements
    // take longer than the 200 ms player heartbeat.
    job.sends = job.sends.then(() => { if (!job.deliveryFailed) return this.send(body, job.scope); }).catch(error => {
      job.deliveryFailed = true;
      this.onError(error, job.scope);
      if (!job.terminal) this.finish(job, 'failed', false, 'feedback_failed');
    });
  }

  generated(id, response) {
    const job = this.jobs.get(id);
    if (!job || job.terminal) return;
    if (response?.ok === false || response?.data?.ok === false || response?.data?.audio_sent === false)
      return this.finish(job, 'failed', true, 'audio_unavailable');
    job.generated = true;
    const hostId = response?.data?.speech_id || response?.data?.speechId;
    if (hostId) this.hostIds.set(hostId, id);
    this.considerEnd(job);
  }

  accept(state) {
    const id = state?.requestId || this.hostIds.get(state?.speechId);
    const job = this.jobs.get(id);
    if (!job || job.terminal || !Number.isFinite(state.updatedAt) || this.now() - state.updatedAt > 750) return;
    if (state.speechId) this.hostIds.set(state.speechId, id);
    job.latest = state;
    if (state.turnId) job.turnId = state.turnId;
    if (/cancel|clear_audio_queue|interrupt/.test(state.reason || '')) return this.finish(job, 'interrupted');
    const audible = !Number.isFinite(state.remainingSeconds) || state.remainingSeconds > 0.05;
    if (state.active && audible && state.audioContextState === 'running' && state.playbackTurnId === state.turnId
      && Number(state.playbackStartAudioTime) > 0
      && Number(state.audioContextTime) >= Number(state.playbackStartAudioTime)) {
      this.queue(job, job.started ? 'playing' : 'started', state);
      if (!job.started) {
        // The first-audio warning is no longer relevant after sound starts.
        for (const timer of job.timers) this.clearTimer(timer);
        job.timers.clear();
        this.timer(job, () => this.finish(job, 'failed', true, 'playback_timeout'), this.limits.totalMs);
        this.progress(job, 'started');
      }
      job.started = true;
      if (Number(state.audioContextTime) > job.audioTime && !this.completedTurns.has(job.turnId)) {
        if (job.slow) { job.slow = false; this.progress(job,'resumed'); }
        job.audioTime = Number(state.audioContextTime);
        this.armProgressDeadline(job);
      }
    }
    this.considerEnd(job);
  }

  considerEnd(job) {
    if (job.terminal || !job.started || !job.turnId || !this.completedTurns.has(job.turnId)) return;
    if (job.generated) return this.finish(job, 'ended');
    if (!job.streamEnded) {
      job.streamEnded = true;
      this.cancelTimer(job,job.slowTimer);
      this.cancelTimer(job,job.stallTimer);
      this.progress(job, 'awaiting_confirmation');
      this.timer(job, () => this.finish(job, 'failed', true, 'completion_confirmation_timeout'), this.limits.confirmationMs);
    }
  }

  // Called only by the same-origin player's native end event observer after
  // checking audio_done for the exact decoder epoch. Empty source queues alone
  // cannot distinguish an inter-chunk gap from the end of the stream.
  playerEnded(turnId, streamClosed) {
    if (!turnId || !streamClosed) return;
    this.completedTurns.add(turnId);
    while (this.completedTurns.size > 128) this.completedTurns.delete(this.completedTurns.values().next().value);
    for (const job of this.jobs.values()) this.considerEnd(job);
  }

  fail(id, status = 'failed', reason = '') { const job = this.jobs.get(id); if (job) this.finish(job, status, true, reason || (status === 'failed' ? 'speech_request_failed' : '')); }

  finish(job, status, notify = true, reason = '') {
    if (job.terminal) return;
    job.terminal = true;
    for (const timer of job.timers) this.clearTimer(timer);
    job.timers.clear();
    this.progress(job, 'feedback_pending', reason);
    if (notify) this.queue(job, status, job.latest || {});
    job.sends.finally(() => {
      this.jobs.delete(job.id);
      for (const [host, id] of this.hostIds) if (id === job.id) this.hostIds.delete(host);
      const outcome = job.deliveryFailed ? 'failed' : status;
      this.progress(job,outcome,job.deliveryFailed?'feedback_failed':reason);
      job.resolve(outcome);
    });
  }

  close({notify = true} = {}) { for (const job of this.jobs.values()) this.finish(job, 'interrupted', notify); }
}
