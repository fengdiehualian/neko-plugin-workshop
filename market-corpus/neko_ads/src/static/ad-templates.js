/* Data-driven desktop templates. Round ownership, voice and exits stay in the bridge. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NekoAdTemplates = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function create(document, card, {assets, links, close, open, change}) {
    if (!['news', 'webgame'].includes(card.kind)) return null;
    const el = (tag, text, cls) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (cls) node.className = cls;
      return node;
    };
    const icon = name => {
      const node = el('img', undefined, 'neko-ad-icon');
      node.src = assets + '/assets/icons/' + name + '.svg'; node.alt = ''; node.draggable = false;
      return node;
    };
    const text = (node, content) => {
      for (const part of Array.isArray(content) ? content : [content]) {
        const cls = part && typeof part === 'object' && ['accent', 'highlight'].includes(part.tone) ? 'neko-ad-' + part.tone : '';
        node.append(el('span', typeof part === 'string' ? part : part && part.text || '', cls));
      }
      return node;
    };
    const link = (destination, label, cls) => {
      const node = el('a', undefined, cls);
      node.href = destination === 'market' ? links.market : links.project;
      node.target = '_blank'; node.rel = 'noopener noreferrer';
      if (label) text(node, label);
      node.onclick = event => open(event, node);
      node.onauxclick = event => open(event, node);
      return node;
    };
    const closeButton = target => {
      const node = el('button', undefined, 'neko-ad-close neko-ad-close-' + target);
      node.type = 'button'; node.dataset.adClose = target;
      node.setAttribute('aria-label', '关闭广告：' + card.title);
      node.append(icon('x'));
      node.onclick = event => { event.preventDefault(); event.stopPropagation(); close(target); };
      return node;
    };
    const box = el('article', undefined, 'neko-ad-placeholder neko-ad-modern neko-ad-' + card.kind);
    box.dataset.cardId = card.id; box.setAttribute('aria-label', card.subtitle);
    const bar = el('header', undefined, 'neko-ad-caption');
    const mark = card.kind === 'news' ? el('img', undefined, 'neko-ad-brand') : icon('megaphone-simple');
    if (card.kind === 'news') { mark.src = assets + '/assets/neko-logo.png'; mark.alt = ''; mark.draggable = false; }
    bar.append(mark, el('span', card.subtitle, 'neko-ad-caption-title'), closeButton('frame'));
    box.append(bar);
    if (card.kind === 'webgame') {
      const strip = el('div', undefined, 'neko-ad-game-strip');
      strip.append(closeButton('strip'), el('span', card.title));
      const stage = el('div', undefined, 'neko-ad-game-stage');
      const art = link(card.destination, null, 'neko-ad-art-link');
      art.setAttribute('aria-label', '查看猫娘计划宣传页');
      // Artwork is a complete clickable poster. Its controls live below it
      // so neither the CTA nor the third X covers the printed headline.
      if (card.image) {
        const image = el('img', undefined, 'neko-ad-art');
        image.src = card.image; image.alt = card.imageAlt || ''; image.draggable = false;
        image.onerror = () => image.remove(); art.append(image);
      }
      const cta = link(card.destination, card.action, 'neko-ad-game-cta'); cta.append(icon('arrow-right'));
      const actions = el('div', undefined, 'neko-ad-game-actions');
      actions.append(closeButton('corner'), cta);
      stage.append(art); box.append(strip, stage, actions);
    } else {
      const tabs = el('nav', undefined, 'neko-ad-news-tabs'); tabs.setAttribute('aria-label', '资讯分类');
      const category = card.category || 'all';
      for (const tab of card.tabs) {
        const node = el('button', tab.text, 'neko-ad-news-tab'); node.type = 'button';
        node.dataset.adTab = tab.category;
        node.setAttribute('aria-pressed', String(category === tab.category));
        node.onclick = () => { card.category = tab.category; change(); }; tabs.append(node);
      }
      const content = el('div', undefined, 'neko-ad-news-content');
      const heading = text(el('h3', undefined, 'neko-ad-news-headline'), card.headline);
      content.append(heading, el('p', card.body, 'neko-ad-news-summary'));
      const columns = el('div', undefined, 'neko-ad-news-columns');
      const stories = el('div', undefined, 'neko-ad-news-stories');
      for (const story of card.stories.filter(item => category === 'all' || item.destination === category)) {
        const entry = link(story.destination, null, 'neko-ad-news-story');
        entry.append(text(el('h4'), story.title), el('p', story.body)); stories.append(entry);
      }
      const more = link(card.destination, card.action, 'neko-ad-news-more'); more.append(icon('arrow-right')); stories.append(more);
      const sidebar = el('aside', undefined, 'neko-ad-news-sidebar');
      sidebar.append(el('h4', card.sidebarTitle));
      for (const item of card.recommendations) {
        const entry = link(item.destination, null, 'neko-ad-news-recommendation');
        entry.append(el('h4', item.title), el('p', item.body)); sidebar.append(entry);
      }
      columns.append(stories, sidebar); content.append(columns); box.append(tabs, content);
    }
    return {box, bar};
  }
  return {create};
});
