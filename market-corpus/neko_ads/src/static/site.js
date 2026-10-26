/* Showroom interactions stay local to the page; only main.js controls rounds. */
(function () {
  'use strict';
  function hostShell() {
    if (window.electronShell) return window.electronShell;
    try { return window.parent.electronShell; } catch (_) { return null; }
  }
  const status = document.getElementById('link-status');
  document.querySelectorAll('a[data-external]').forEach(link => {
    const open = event => {
      const shell = hostShell();
      return window.NekoAds.openAdLink(event, {
        url: link.href, shell, electron: /Electron/i.test(navigator.userAgent) || !!shell,
        onOpening: () => { status.textContent = ''; },
        onError: message => { status.textContent = message; status.scrollIntoView({block:'nearest'}); },
      });
    };
    link.addEventListener('click', open);
    link.addEventListener('auxclick', open);
  });
  document.querySelectorAll('[data-demo]').forEach(sample => {
    const content = sample.querySelector('.sample-content');
    const empty = sample.querySelector('.sample-empty');
    const response = sample.querySelector('.demo-response');
    const decoys = sample.querySelectorAll('[data-demo-decoy]');
    sample.querySelector('[data-demo-close]').addEventListener('click', () => {
      content.hidden = true; empty.hidden = false;
      decoys.forEach(button => { button.disabled = true; });
      sample.querySelector('[data-demo-reset]').focus();
    });
    sample.querySelector('[data-demo-reset]').addEventListener('click', () => {
      content.hidden = false; empty.hidden = true;
      decoys.forEach(button => { button.disabled = false; });
      if (response) response.textContent = '杂鱼♡ 猜猜哪一个能关掉？';
      sample.querySelector('[data-demo-close]').focus();
    });
    decoys.forEach(button => button.addEventListener('click', () => {
      response.textContent = 'ざぁこ♡ 你关掉的，是关闭按钮的认真模式。';
    }));
  });
})();
