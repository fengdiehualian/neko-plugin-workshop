const EVENT = 'neko-game-window-state-change';
const CLASS = 'neko-sister-drive-companion';
const AVATARS = ['live2d','vrm','mmd','pngtuber'];

// Scoped to the connected plugin's player. The stock main page still owns
// route notifications and every other game; no host listener is replaced.
export function keepCompanionVisible(player) {
  const doc = player.document;
  if (!doc?.body) return () => {};
  const style = doc.createElement('style');
  let visible = [], active = false, releasedGameClass = false;
  const snapshot = () => {
    visible = AVATARS.filter(name => {
      const node = doc.getElementById(`${name}-container`);
      return node && !node.classList.contains('hidden') && node.style.display !== 'none'
        && (doc.body.classList.contains('neko-game-active') || player.getComputedStyle(node).display !== 'none');
    });
  };
  const clear = (restore = true) => {
    if (restore && active && releasedGameClass) doc.body.classList.add('neko-game-active');
    active = false; releasedGameClass = false;
    doc.body.classList.remove(CLASS); style.remove();
  };
  const capture = event => {
    const own = event.detail?.action === 'opened' && event.detail?.gameType === 'teach-sister-drive';
    if (own && !active) snapshot();
    if (!own) clear(event.detail?.action !== 'closed');
  };
  const apply = event => {
    if (event.detail?.action !== 'opened' || event.detail?.gameType !== 'teach-sister-drive') return;
    active = true;
    const scope = `body.${CLASS}`;
    const overlays = ['#chat-container','#status-toast','#subtitle-display','#react-chat-window-overlay',
      '#chat-avatar-preview-popup','.home-avatar-floating-guide-player'];
    style.textContent = overlays.map(selector=>`${scope} ${selector}`).join(',')
      + ' { display:none !important; pointer-events:none !important; }';
    doc.head.append(style); doc.body.classList.add(CLASS);
    // Electron uses this stock CSS flag to force the *whole native window*
    // into click-through mode, bypassing model and button hit tests. This game
    // keeps an interactive companion, so use our scoped overlay flag instead.
    // appState/game-route ownership is unchanged; native pixel hit testing,
    // model locks and transparent-area click-through keep their normal policy.
    if (doc.body.classList.contains('neko-game-active')) {
      releasedGameClass = true; doc.body.classList.remove('neko-game-active');
    }
    for (const name of visible) {
      const manager = player[`${name}Manager`];
      if (name === 'pngtuber') manager?.show?.();
      else manager?.resumeRendering?.();
    }
  };
  snapshot();
  player.addEventListener(EVENT,capture,true);
  player.addEventListener(EVENT,apply);
  if (player.appState?.gameRouteActive && player.appState.gameRouteGameType === 'teach-sister-drive')
    apply({detail:{action:'opened',gameType:'teach-sister-drive'}});
  return () => {
    player.removeEventListener(EVENT,capture,true); player.removeEventListener(EVENT,apply);
    if (active && (releasedGameClass || doc.body.classList.contains('neko-game-active'))) {
      for (const name of visible) {
        const manager = player[`${name}Manager`];
        if (name === 'pngtuber') manager?.hide?.();
        else manager?.pauseRendering?.();
      }
    }
    clear();
  };
}

// A local RTC data channel keeps a connected real-time panel out of Chromium's
// intensive background timer throttling. No remote peer, media or STUN server.
export async function keepPanelResponsive(page = window) {
  if (!page.RTCPeerConnection) return () => {};
  const first = new page.RTCPeerConnection({iceServers:[]});
  const second = new page.RTCPeerConnection({iceServers:[]});
  const close = () => { first.close(); second.close(); };
  first.onicecandidate = ({candidate}) => { if (candidate) void second.addIceCandidate(candidate).catch(()=>{}); };
  second.onicecandidate = ({candidate}) => { if (candidate) void first.addIceCandidate(candidate).catch(()=>{}); };
  first.createDataChannel('neko-driving-lifecycle');
  try {
    await first.setLocalDescription(await first.createOffer());
    await second.setRemoteDescription(first.localDescription);
    await second.setLocalDescription(await second.createAnswer());
    await first.setRemoteDescription(second.localDescription);
    return close;
  } catch (error) { close(); throw error; }
}
