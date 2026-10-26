import {findPlayerWindow,isPlayerReady,isMainPlayerWindow} from './player-window.mjs?v=0.1.0-origin1';

const query = new URLSearchParams(location.search);
const parentOrigin = query.get('parent_origin');
const allowed = ['http://localhost:48916','http://127.0.0.1:48916',
  'http://localhost:48911','http://127.0.0.1:48911'];
if (window.parent !== window && allowed.includes(parentOrigin)) {
  const ready = Boolean(findPlayerWindow(window,candidate=>isPlayerReady(candidate,location.origin)));
  const available = ready || Boolean(findPlayerWindow(window,candidate=>isMainPlayerWindow(candidate,location.origin)));
  window.parent.postMessage({type:'neko-drive-player-origin',nonce:query.get('nonce'),ready,available},parentOrigin);
}
