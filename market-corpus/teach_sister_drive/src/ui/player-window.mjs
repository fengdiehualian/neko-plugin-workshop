// A panel can be nested under the static plugin frame and a manager window on
// another port. Cross-origin WindowProxy permits parent/opener traversal, but
// the caller only accepts a ready voice player on the adapter's own origin.
export function isPlayerReady(candidate, origin) {
  try { return Boolean(candidate && !candidate.closed && candidate.location.origin === origin
    && candidate.document.readyState !== 'loading' && candidate.appState && candidate.appAudioPlayback
    && candidate.document.querySelector('script[src*="/app-game-voice-control.js"]')); }
  catch { return false; }
}

export function isMainPlayerWindow(candidate, origin) {
  try { return Boolean(candidate && !candidate.closed && candidate.location.origin === origin
    && candidate.location.pathname === '/'); }
  catch { return false; }
}

export function pendingPlayerAudio(player, speechId) {
  const audio=player.appAudioPlayback;
  if(typeof audio?.pendingAudioCountForSpeech==='function') return audio.pendingAudioCountForSpeech(speechId);
  // A host without the native completion query cannot confirm playback end.
  return Infinity;
}

export function interruptPlayerAudio(player, speechId) {
  if(!speechId || player.NekoSpeechPlaybackState?.speechId!==speechId)return;
  const audio=player.appAudioPlayback;
  if(typeof audio?.clearAudioQueue==='function')void audio.clearAudioQueue();
}

export function findPlayerWindow(start, ready) {
  const visited = new Set();
  let frame = start;
  for (let depth = 0; frame && depth < 8 && !visited.has(frame); depth++) {
    visited.add(frame);
    try { if (ready(frame.opener)) return frame.opener; } catch { /* Closed opener. */ }
    if (frame !== start && ready(frame)) return frame;
    try {
      const parent = frame.parent;
      if (!parent || parent === frame) break;
      frame = parent;
    } catch { break; }
  }
  return null;
}

// Only coordination uses the player window. In particular, Response must stay
// in the SDK page's realm: the host buffers HTTP bodies into window.Response,
// and the public SDK rejects cross-realm JSON objects rather than coercing them.
export function createPlayerHostWindow(page, player) {
  const events = new Set(['addEventListener','removeEventListener','dispatchEvent']);
  const pageMethods = new Set(['setTimeout','clearTimeout','setInterval','clearInterval','fetch']);
  const channels = new Set(['BroadcastChannel','localStorage']);
  const methods = new Map();
  return new Proxy(page, {
    get(target, key) {
      if (events.has(key)) {
        if (!methods.has(key)) methods.set(key, (...args) => {
          const type = key === 'dispatchEvent' ? args[0]?.type : args[0];
          const owner = ['pagehide','beforeunload','unload'].includes(type) ? target : player;
          return owner[key](...args);
        });
        return methods.get(key);
      }
      if (pageMethods.has(key)) {
        if (!methods.has(key)) methods.set(key, target[key].bind(target));
        return methods.get(key);
      }
      if (channels.has(key)) return Reflect.get(player, key, player);
      return Reflect.get(target, key, target);
    },
  });
}
