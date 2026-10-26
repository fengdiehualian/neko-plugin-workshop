(function () {
  'use strict';
  const supported = ['en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'ru', 'es', 'pt'];
  let messages = {};
  let english = {};
  let locale = 'en';
  const host = location.pathname.match(/^(.*\/plugin\/miao_miao_bump)\/ui(?:\/|$)/);

  function normalize(value) {
    const code = String(value || '').toLowerCase().replaceAll('_', '-');
    if (code === 'zh-tw' || code === 'zh-hk' || code === 'zh-hant' || code.startsWith('zh-hant-')) return 'zh-TW';
    if (code.startsWith('zh')) return 'zh-CN';
    return supported.find(item => item.toLowerCase() === code.split('-')[0]) || 'en';
  }

  async function bundle(code) {
    const url = host ? `${host[1]}/ui-api/i18n/${code}.json` : new URL(`../i18n/${code}.json`, document.baseURI);
    const response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) throw new Error('Translation bundle unavailable');
    return response.json();
  }

  async function load(code) {
    const next = normalize(code);
    if (!Object.keys(english).length) english = await bundle('en');
    messages = next === 'en' ? english : await bundle(next);
    locale = next;
    document.documentElement.lang = locale;
    document.title = t('plugin.name');
    document.querySelectorAll('[data-t]').forEach(element => { element.textContent = t(element.dataset.t); });
    document.querySelectorAll('[data-aria]').forEach(element => { element.setAttribute('aria-label', t(element.dataset.aria)); });
    document.getElementById('language').value = locale;
  }

  function t(key) {
    const message = messages[key] ?? english[key] ?? key;
    return message.replaceAll('{opponent}', messages['opponent.name'] ?? english['opponent.name'] ?? 'Opponent');
  }

  async function init() {
    let desired = new URLSearchParams(location.search).get('locale');
    if (!desired && host) {
      try {
        const response = await fetch(`${host[1]}/ui-api/locale`);
        if (response.ok) desired = (await response.json()).locale;
      } catch { /* Standalone browser language remains available. */ }
    }
    await load(desired || navigator.language || 'en');
  }

  window.MiaoI18n = { init, load, t, normalize, get locale() { return locale; } };
})();
