(function () {
  'use strict';
  let context = null;
  let enabled = true;
  function unlock() {
    if (!enabled) return;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    try {
      if (!context) context = new Audio();
      if (context.state === 'suspended') context.resume().catch(() => {});
    } catch { /* Sound is optional; unsupported audio must not block play. */ }
  }
  function play(kind, strength = 1) {
    if (!enabled || !context || context.state !== 'running') return;
    const tones = { launch: [380, 180], hit: [260, 110], guard: [780, 490], result: [520, 780], retreat: [650, 140], respawn: [220, 460] };
    const [start, end] = tones[kind] || tones.hit;
    const now = context.currentTime;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(start, now);
    oscillator.frequency.exponentialRampToValueAtTime(end, now + 0.12);
    gain.gain.setValueAtTime(0.035 * Math.max(0.15, Math.min(1, strength)), now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.16);
    oscillator.connect(gain); gain.connect(context.destination);
    oscillator.start(now); oscillator.stop(now + 0.17);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
  }
  window.MiaoSound = {
    unlock, play,
    get enabled() { return enabled; },
    toggle() { enabled = !enabled; if (enabled) unlock(); return enabled; },
    destroy() { if (context) context.close().catch(() => {}); context = null; },
  };
})();
