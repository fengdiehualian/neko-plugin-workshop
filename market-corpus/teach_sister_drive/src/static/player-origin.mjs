export function mainOrigin(value) {
  try {
    const url = new URL(value);
    if (url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname)
      && url.port === '48911' && !url.username && !url.password) return url.origin;
  } catch { /* An absent or invalid hint is not a main-server origin. */ }
  return null;
}

function probePlayer(page, origin) {
  return new Promise(resolve => {
    const frame = page.document.createElement('iframe');
    const nonce = page.crypto.randomUUID();
    frame.hidden = true;
    frame.setAttribute('aria-hidden','true');
    const finish = result => {
      page.clearTimeout(timer);
      page.removeEventListener('message',receive);
      frame.remove();
      resolve({origin,...result});
    };
    const receive = event => {
      if (event.source !== frame.contentWindow || event.origin !== origin
        || event.data?.type !== 'neko-drive-player-origin' || event.data?.nonce !== nonce) return;
      finish({ready:event.data.ready === true,available:event.data.available === true});
    };
    const timer = page.setTimeout(()=>finish({ready:false,available:false}),2000);
    page.addEventListener('message',receive);
    const query = new URLSearchParams({parent_origin:page.location.origin,nonce});
    frame.src = `${origin}/static/game/teach-sister-drive/player-probe.html?${query}`;
    page.document.body.append(frame);
  });
}

// A tiny frame on each loopback origin can inspect the existing opener chain
// on its own origin. No voice session, model or visible player is created.
export async function resolvePlayerOrigin(page, preferred) {
  const fallback = mainOrigin(preferred) || `http://${page.location.hostname === 'localhost'?'localhost':'127.0.0.1'}:48911`;
  const origins = [...new Set([fallback,'http://localhost:48911','http://127.0.0.1:48911'])];
  const results = await Promise.all(origins.map(origin=>probePlayer(page,origin)));
  return (results.find(result=>result.ready) || results.find(result=>result.available))?.origin || fallback;
}
