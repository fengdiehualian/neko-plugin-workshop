/* Round state and link activation, shared by the renderer and regression tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NekoAds = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SAFE_WORD = '广告下班';
  const PROMO_ART = {
    takeover: 'assets/posters/project-takeover.png',
    companion: 'assets/posters/project-companion.png',
    play: 'assets/posters/project-play.png',
    market: 'assets/posters/plugin-market.png',
  };
  const FEEDBACK = {
    start: ['广告部，开——业！主人今天也要好好看广告哦♡', '広告の時間だよ。ちゃんと見てね♡', 'smug'],
    dodge: ['こっち、こっち～连一个叉都追不上？', 'こっち、こっち。遅いね、ざぁこ♡', 'smug'],
    fake: ['ざぁこ♡ 你关闭的是广告的广告哦。', 'ざぁこ。それは広告の広告だよ♡', 'smug'],
    split: ['关掉一张，赠送一张！猫娘广告部就是这么贴心～', 'もう一枚、プレゼントだよ♡', 'smug'],
    scan: ['体检完毕！主人缺的不是优化，是猫娘♡', '足りないのは、猫ちゃんだよ♡', 'smug'],
    later: ['稍后？本广告部的稍后，就是现在哦～', 'あとで？今すぐだよ♡', 'smug'],
    closed: ['哼，刚刚算你赢。可别得意忘形了！', '今回は負けてあげる。調子に乗らないでね。', 'pout'],
    streak: ['等、等等……你怎么越来越熟练了！不许笑！', 'ちょっと。上手くなりすぎ。笑わないで！', 'flustered'],
    view: ['嘴上嫌烦，点得倒是很诚实嘛，主人♡', 'ふふ、気になるんだね、ざぁこ♡', 'smug'],
  };
  const BUILTINS = [
    {kind: 'poster', title: '是主人就来养我！', subtitle: '猫娘计划 · 桌面陪伴', body: '开局一只猫，陪伴全靠聊。你的桌面，猫娘承包了！', badge: '今日热喵', action: '领取桌面猫娘', trick: 'fake', image: PROMO_ART.takeover, imageAlt: '桌面归本喵了！猫娘计划宣传海报'},
    {
      kind: 'news', title: '你的桌面，已经被猫娘盯上了', subtitle: '猫娘快讯', trick: 'plain',
      headline: ['你的桌面，已经被', {text: '猫娘', tone: 'accent'}, '盯上了'],
      body: '猫娘计划与插件推荐，都在这里。',
      tabs: [{text: '推荐', category: 'all'}, {text: '猫娘计划', category: 'project'}, {text: '插件发现', category: 'market'}],
      stories: [
        {title: ['桌面陪伴新消息：', {text: '本喵来了', tone: 'highlight'}], body: '更可爱的桌面体验，正在靠近你。', destination: 'project'},
        {title: '插件市场精选：总有一款拿捏你', body: '实用又有趣的插件，装点你的每一天。', destination: 'market'},
        {title: '杂鱼♡今天也逃不过本喵的推荐', body: '来看看本喵为你挑选的宝藏插件吧。', destination: 'market'},
      ],
      sidebarTitle: '插件热推',
      recommendations: [
        {title: '猫娘计划', body: '让桌面更有陪伴感。', destination: 'project'},
        {title: '插件市场', body: '发现更多有趣玩法。', destination: 'market'},
        {title: '今日发现', body: '每天都有新惊喜。', destination: 'market'},
      ],
      action: '更多推荐', destination: 'market',
    },
    {
      kind: 'webgame', title: '桌面占领计划', subtitle: '猫娘广告部 · 今日推荐',
      action: '去看看本喵', trick: 'multi-close', destination: 'project', image: PROMO_ART.play,
      imageAlt: '是猫娘，就来陪我玩！杂鱼~今天也别想偷偷溜走♡',
    },
    {kind: 'nearby', title: '你附近有一只猫娘', subtitle: '正在等你说话！', body: '不用跨服，不用排队。打开猫娘计划，和桌面上的她聊两句。', badge: '猫娘在线', action: '查看猫娘计划', trick: 'dodge', image: PROMO_ART.companion, imageAlt: '开局一只猫，陪伴全靠聊。猫娘计划宣传海报'},
    {kind: 'scan', title: '桌面含猫量严重不足！', subtitle: '猫娘广告部 · 一本正经体检', body: '建议补充：一只猫娘、一段闲聊，以及一点点对主人的关爱。', badge: '紧急补猫', action: '立即补猫', trick: 'scan', textOnly: true},
    {kind: 'market', title: '震惊！桌面还能这样玩？', subtitle: '猫娘计划 · 插件市场', body: '搜索、提醒、陪伴……去插件市场看看真实功能，猫娘广告部负责夸张宣传！', badge: '插件上新', action: '逛逛插件市场', trick: 'split', image: PROMO_ART.market, imageAlt: '猫娘的本事，还能加！猫娘计划 · 插件市场宣传海报'},
  ];
  function normalizeCatalog(payload) {
    const items = payload && Array.isArray(payload.items) ? payload.items : [];
    return items.filter(p => p && typeof p.name === 'string' && p.name.trim() && p.id != null)
      .slice(0, 12).map(p => ({
        kind: 'recommendation', title: `震惊！${p.name.trim().slice(0, 40)}登场！`,
        subtitle: '来自猫娘计划插件市场 · 真实推荐',
        body: String(p.short_description || p.description || '点击查看这个插件的真实功能。').slice(0, 180),
        badge: '主人必看', action: '查看插件介绍', trick: 'split',
        image: PROMO_ART.market, imageAlt: '猫娘的本事，还能加！猫娘计划 · 插件市场宣传海报',
        pluginId: String(p.id).slice(0, 100), pluginName: p.name.trim().slice(0, 40),
      }));
  }
  async function openAdLink(event, {url, shell, electron = false, onOpening = () => {}, onError = () => {}}) {
    if (event.type === 'auxclick' && event.button !== 1) return false;
    let target;
    try {
      target = new URL(url);
      if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) throw new Error();
    } catch (_) {
      event.preventDefault(); onError('广告链接无效，已取消跳转。'); return false;
    }
    if (!shell || typeof shell.openExternal !== 'function') {
      if (electron) {
        event.preventDefault();
        onError('无法调用系统默认浏览器，已取消跳转。');
        return false;
      }
      // An ordinary browser owns this anchor's normal, closable new tab.
      onOpening(); return true;
    }
    // Prevent Electron's generic child-window handler, including middle-click.
    // A rejected system-browser request must never fall back to window.open.
    event.preventDefault();
    onOpening();
    try {
      const result = await shell.openExternal(target.href);
      if (result === false || result && result.success === false) throw new Error();
      return true;
    } catch (_) {
      onError('未能打开系统默认浏览器，请稍后重试。');
      return false;
    }
  }
  class SpeechPlayer {
    constructor(context) {
      this.context = context; this.sources = new Set(); this.next = 0; this.tail = null; this.waiters = [];
    }
    enqueue(buffer) {
      const source = this.context.createBufferSource();
      source.buffer = buffer; source.connect(this.context.destination); this.sources.add(source);
      source.onended = () => { this.sources.delete(source); source.disconnect(); if (!this.sources.size) this.drain(); };
      const at = Math.max(this.next, this.context.currentTime + .035);
      this.next = at + buffer.duration; source.start(at);
    }
    pcm(bytes, sampleRate = 48000) {
      if (this.tail !== null) {
        const joined = new Uint8Array(bytes.length + 1); joined[0] = this.tail; joined.set(bytes, 1); bytes = joined;
      }
      const length = bytes.length - bytes.length % 2;
      this.tail = length < bytes.length ? bytes.at(-1) : null;
      if (!length) return;
      const buffer = this.context.createBuffer(1, length / 2, sampleRate), samples = buffer.getChannelData(0);
      const view = new DataView(bytes.buffer, bytes.byteOffset, length);
      for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
      this.enqueue(buffer);
    }
    stop() {
      for (const source of this.sources) { try { source.stop(); source.disconnect(); } catch (_) {} }
      this.sources.clear(); this.next = 0; this.tail = null;
      this.drain();
    }
    idle() { return this.sources.size ? new Promise(resolve => this.waiters.push(resolve)) : Promise.resolve(); }
    drain() { this.waiters.splice(0).forEach(resolve => resolve()); }
  }
  class ReactionQueue {
    constructor({play, cancel, valid = () => true, now = Date.now, schedule = (fn, ms) => setTimeout(fn, ms), unschedule = id => clearTimeout(id), gap = 2200, maxAge = 4500}) {
      Object.assign(this, {play, cancel, valid, now, schedule, unschedule, gap, maxAge});
      this.pending = null; this.running = false; this.timer = null; this.generation = 0; this.last = -Infinity;
    }
    request(event) {
      if (!this.valid()) return;
      this.pending = {event, at:this.now()}; this.pump();
    }
    async pump() {
      if (this.running || this.timer !== null || !this.pending || !this.valid()) return;
      if (this.now() - this.pending.at > this.maxAge) { this.pending = null; return; }
      const wait = this.last + this.gap - this.now();
      if (wait > 0) { this.timer = this.schedule(() => { this.timer = null; this.pump(); }, wait); return; }
      const item = this.pending, generation = this.generation;
      this.pending = null; this.running = true; this.last = this.now();
      const valid = () => generation === this.generation && this.valid();
      try { await this.play(item.event, valid); } catch (_) {}
      finally { if (generation === this.generation) { this.running = false; this.pump(); } }
    }
    clear() {
      this.generation++; this.pending = null; this.running = false; this.last = -Infinity;
      if (this.timer !== null) this.unschedule(this.timer);
      this.timer = null; this.cancel();
    }
  }
  class Game {
    constructor({schedule = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id), now = Date.now, random = Math.random, shortcut = 'Ctrl+Shift+1', onChange = () => {}, onFeedback = () => {}, onStop = () => {}} = {}) {
      Object.assign(this, {schedule, cancel, now, random, onChange, onFeedback, onStop});
      this.active = false;
      this.paused = false;
      this.shortcut = shortcut;
      this.generation = 0;
      this.timers = new Set();
      this.cards = [];
      this.catalog = [];
      this.nextId = 0;
      this.cursor = 0;
      this.closed = 0;
      this.attempts = 0;
      this.lastFeedback = -Infinity;
      this.successStreak = 0; this.lastStreak = -Infinity;
    }
    later(fn, ms) {
      const generation = this.generation;
      const id = this.schedule(() => {
        this.timers.delete(id);
        if (this.active && !this.paused && generation === this.generation) fn();
      }, ms);
      this.timers.add(id);
    }
    start() {
      if (this.active) return;
      this.generation++;
      this.active = true;
      this.paused = false;
      this.cards = [];
      this.cursor = this.closed = this.attempts = 0;
      this.lastFeedback = -Infinity;
      this.successStreak = 0; this.lastStreak = -Infinity;
      this.spawn();
      this.feedback('start');
      this.loop();
    }
    stop(reason = 'exit') {
      this.active = false;
      this.generation++;
      for (const id of this.timers) this.cancel(id);
      this.timers.clear();
      this.cards = [];
      this.onStop(reason);
      this.onChange();
    }
    loop() {
      this.later(() => { this.spawn(); this.loop(); }, 6500 + this.random() * 3500);
    }
    pause() {
      if (!this.active || this.paused) return;
      this.paused = true;
      for (const id of this.timers) this.cancel(id);
      this.timers.clear();
    }
    resume() {
      if (!this.active || !this.paused) return;
      this.paused = false;
      this.loop();
    }
    spawn() {
      if (!this.active || this.paused || this.cards.length >= 3) return;
      const pool = [...BUILTINS, ...this.catalog];
      const template = pool[this.cursor++ % pool.length];
      const card = {...template, id: ++this.nextId, tries: 0, dodges: 0, x: this.random(), y: this.random()};
      // Choose once per popup. Renders, fake clicks and focus changes never redraw it.
      if (card.trick === 'multi-close') card.closeTarget = ['frame', 'strip', 'corner'][Math.floor(this.random() * 3)];
      this.cards.push(card);
      this.onChange();
    }
    feedback(event) {
      if (!this.active || this.paused) return;
      this.onFeedback(FEEDBACK[event], true, event);
    }
    hover(id) {
      const card = this.cards.find(c => c.id === id);
      if (!this.active || !card || card.trick !== 'dodge' || card.dodges >= 2) return false;
      card.dodges++;
      this.successStreak = 0;
      card.x = this.random(); card.y = this.random();
      this.feedback('dodge'); this.onChange();
      return true;
    }
    close(id, target = 'frame') {
      const card = this.cards.find(c => c.id === id);
      if (!this.active || !card) return;
      this.attempts++;
      if (card.trick === 'multi-close' && target !== card.closeTarget) {
        this.successStreak = 0;
        card.tries++;
        this.feedback('fake'); return;
      }
      if (card.trick === 'fake' && card.tries++ === 0) {
        this.successStreak = 0;
        card.title = '您关闭的是广告的广告';
        card.subtitle = '真正的猫娘宣传，现在才开始！';
        this.feedback('fake'); this.onChange(); return;
      }
      if (card.trick === 'scan' && card.tries++ === 0) {
        this.successStreak = 0;
        card.title = '体检结果：主人需要补猫';
        card.body = '缺猫指数 99.9%（猫娘广告部编的）。处方：打开猫娘计划，陪她聊聊天。';
        this.feedback('scan'); this.onChange(); return;
      }
      this.cards = this.cards.filter(c => c !== card);
      this.closed++;
      this.successStreak++;
      let event = card.trick === 'split' ? 'split' : 'closed';
      if (event === 'closed' && this.successStreak >= 3 && this.now() - this.lastStreak >= 20000) {
        event = 'streak'; this.lastStreak = this.now(); this.successStreak = 0;
      }
      this.feedback(event);
      this.onChange();
      if (card.trick === 'split') this.later(() => this.spawn(), 700);
    }
    postpone(id) {
      if (!this.active || !this.cards.some(c => c.id === id)) return;
      this.successStreak = 0;
      this.feedback('later'); this.spawn();
    }
    input(value) {
      const text = String(value).normalize('NFKC').replace(/\s/g, '');
      if (text.includes(SAFE_WORD)) { this.stop('safe-word'); return true; }
      return false;
    }
    key(event) {
      const parts = this.shortcut.split('+');
      const key = parts.pop();
      const code = /^\d$/.test(key) ? 'Digit' + key : /^[A-Z]$/.test(key) ? 'Key' + key : key;
      if (!!event.ctrlKey === parts.includes('Ctrl') && !!event.shiftKey === parts.includes('Shift') &&
          !!event.altKey === parts.includes('Alt') && !event.metaKey && (event.code === code || String(event.key).toUpperCase() === key)) {
        this.stop('shortcut'); return true;
      }
      return false;
    }
  }
  return {Game, BUILTINS, FEEDBACK, SAFE_WORD, normalizeCatalog, openAdLink, SpeechPlayer, ReactionQueue};
});
