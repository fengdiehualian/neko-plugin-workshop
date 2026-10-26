/* The website opens outside the panel, through the existing browser opener. */
(function () {
  'use strict';
  const link = document.getElementById('open-website');
  const status = document.getElementById('website-status');
  function hostShell() {
    if (window.electronShell) return window.electronShell;
    try { return window.parent.electronShell; } catch (_) { return null; }
  }
  function open(event) {
    const shell = hostShell();
    return window.NekoAds.openAdLink(event, {
      url: link.href, shell, electron: /Electron/i.test(navigator.userAgent) || !!shell,
      onOpening: () => { status.textContent = ''; },
      onError: message => { status.textContent = message; },
    });
  }
  link.addEventListener('click', open);
  link.addEventListener('auxclick', open);
})();
