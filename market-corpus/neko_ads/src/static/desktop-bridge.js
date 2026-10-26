/* Local renderer bridge. Game control and global exits belong to the plugin. */
(function () {
  'use strict';
  if (window.__nekoAdsBridgeLoaded) return;
  window.__nekoAdsBridgeLoaded = true;
  const base = new URL('../', document.currentScript.src).href.replace(/\/$/, '');
  const main = !/\/(?:chat|chat_full|subtitle)(?:\/|$)/.test(location.pathname) && !window.nekoChatWindow;
  const client = crypto.randomUUID(), storage = 'neko-ads-custom-exit-shortcut', roundStorage = 'neko-ads-current-round';
  const retired = new Set();
  const bridgeVersion = '0.2.0';
  // The backend advertises the same policy. Its lease leaves room for an
  // in-flight reply after the bounded local reconnect window.
  let pollTimeout = 6000, connectionGrace = 20000, activePollInterval = 1500, idlePollInterval = 3000;
  let game = null, rid = null, pending = false, revision = 0, status = null, loading = null, wasReady = false;
  let connectionTimer = null, reconnecting = false;
  let failureSince = null, connectionExpired = false, outageGeneration = 0;
  const stopping = new Map();
  let safetyTimer = null, safetyAbort = null;
  let checkingPlugin = false, connectionGeneration = 0;
  let available = false, controlMessage = '';
  let selected = localStorage.getItem(storage) || 'Ctrl+Shift+1', recording = false;
  let audio = null, player = null, speechAbort = null, speechTicket = 0, bubbleTimer = null, viewPending = false;
  let reactions = null, changingVoice = false;
  let localVoiceMute = false;
  let marketBase = 'https://market.project-neko.cn', catalog = [];
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('neko_ads_exit') : null;
  const $ = id => document.getElementById(id);
  const character = () => String(window.lanlan_config && window.lanlan_config.lanlan_name || window.appState && window.appState.lanlan_name || '');
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }
  function toast(text, error = false) {
    const node = el('div', text, 'neko-ads-toast');
    node.setAttribute('role', error ? 'alert' : 'status');
    document.body.append(node); setTimeout(() => node.remove(), error ? 6500 : 3500);
  }
  function controls() {
    if (!main) return;
    const active = !!rid || !!(status && status.active);
    const phase = reconnecting ? 'reconnecting' : pending ? 'starting' : !available ? 'unavailable' : !status.ready ? 'disabled' : active ? 'playing' : 'standby';
    $('neko-ads-dialog').dataset.phase = phase;
    $('neko-ads-start').disabled = active || !available || !status.ready || !character();
    $('neko-ads-stop').disabled = !rid;
    $('neko-ads-start').textContent = pending ? '杂鱼♡准备好挨广告吧……' : '杂鱼♡让本喵占领桌面！';
    $('neko-ads-stop').textContent = pending ? '杂鱼♡这就反悔啦？' : '杂鱼♡这就认输啦？';
    $('neko-ads-stop').setAttribute('aria-label', pending ? '取消开局：杂鱼♡这就反悔啦？' : '结束本局：杂鱼♡这就认输啦？');
    $('neko-ads-key').disabled = pending || recording;
    $('neko-ads-voice').disabled = pending || changingVoice;
    updateVoiceStatus();
    $('neko-ads-launcher').textContent = '猫娘广告部 · ' + ({reconnecting:'正在重连', starting:'正在开局', unavailable:'连接中断', disabled:'未启用', playing:'游戏中', standby:'待机中'})[phase];
    $('neko-ads-state').textContent = controlMessage || ({
      reconnecting: '连接暂时中断，正在重试。安全词和退出快捷键仍可使用。',
      starting: '正在开局，正在确认退出快捷键……',
      unavailable: '插件连接不可用，请在插件管理中检查是否已启动。',
      disabled: '插件未启用，请先在插件管理中启动插件。',
      playing: '游戏进行中，当前猫娘：' + character(),
      standby: '插件已启用 · 待机中，当前猫娘：' + character(),
    })[phase];
  }
  async function rpc(command, payload = {}, signal, timeout = 6000) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
    const abort = () => controller.abort();
    if (signal) { if (signal.aborted) abort(); else signal.addEventListener('abort', abort, {once: true}); }
    try {
      const response = await fetch(base + '/hosted-ui/action/desktop_command', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal,
        body: JSON.stringify({kind: 'panel', surface_id: 'main', args: {command, payload}}),
        cache: 'no-store', keepalive: command === 'stop',
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || '广告插件连接失败');
      const result = body.result || {};
      if (result.success === false || result.status === 'error' || result.error) throw new Error(result.error && (result.error.message || result.error) || '广告操作失败');
      return result.data || result.value || result;
    } finally { clearTimeout(timer); if (signal) signal.removeEventListener('abort', abort); }
  }
  async function pollState() {
    const next = await rpc('poll', {client_id: client, round_id:rid, character: character(), bridge_version:bridgeVersion, busy:!!(window.appState && window.appState.isPlaying)}, undefined, pollTimeout);
    if (!next || typeof next.active !== 'boolean' || typeof next.ready !== 'boolean' ||
        next.active && (typeof next.round_id !== 'string' || typeof next.owner !== 'string')) {
      throw new Error('广告插件状态无效');
    }
    return next;
  }
  function connectionPolicy(next) {
    const policy = next.connection_policy;
    if (!policy) return;
    const values = ['poll_timeout_ms','reconnect_grace_ms','active_poll_ms','idle_poll_ms','lease_seconds'];
    if (!values.every(key => Number.isInteger(policy[key])) ||
        policy.poll_timeout_ms < 1000 || policy.poll_timeout_ms > 10000 ||
        policy.reconnect_grace_ms < policy.poll_timeout_ms * 2 || policy.reconnect_grace_ms > 30000 ||
        policy.active_poll_ms < 1000 || policy.active_poll_ms > 3000 ||
        policy.idle_poll_ms < policy.active_poll_ms || policy.idle_poll_ms > 5000 ||
        policy.lease_seconds * 1000 < policy.reconnect_grace_ms + policy.poll_timeout_ms) return;
    pollTimeout = policy.poll_timeout_ms; connectionGrace = policy.reconnect_grace_ms;
    activePollInterval = policy.active_poll_ms; idlePollInterval = policy.idle_poll_ms;
  }
  async function pluginStopped() {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 2000);
    try {
      const response = await fetch(base.replace(/\/plugin\/neko_ads$/, '') + '/plugin/status?plugin_id=neko_ads', {signal: controller.signal, cache:'no-store'});
      if (!response.ok) return false;
      const data = await response.json();
      return ['stopped', 'disabled', 'error', 'crashed', 'frozen'].includes(data.status && data.status.status);
    } catch (_) { return false; }
    finally { clearTimeout(timer); }
  }
  function checkPluginStopped(version, id) {
    if (checkingPlugin) return;
    checkingPlugin = true;
    const generation = connectionGeneration;
    // A status probe must not delay the next heartbeat or stack up during a
    // host stall. Only a confirmed stop bypasses the reconnect deadline.
    void pluginStopped().then(stopped => {
      if (!stopped || version !== revision || id !== rid || generation !== connectionGeneration) return;
      status = {...status, ready:false, active:false}; available = true; wasReady = false;
      if (rid) stop();
      controlMessage = '广告部下班啦，杂鱼♡'; $('neko-ads-launcher').hidden = true; controls();
    }).finally(() => { setTimeout(() => { checkingPlugin = false; }, 5000); });
  }
  function cancelAudio() {
    const sequence = speechTicket++;
    if (speechAbort) { speechAbort.abort(); speechAbort = null; }
    if (rid) rpc('cancel_speech', {round_id: rid, sequence, preserve:false}).catch(() => {});
    if (player) player.stop();
  }
  function stopAudio() { if (reactions) reactions.clear(); else cancelAudio(); }
  function sharedRound() {
    try { return JSON.parse(localStorage.getItem(roundStorage) || 'null'); } catch (_) { return null; }
  }
  function publishRound(id) {
    try {
      if (id) localStorage.setItem(roundStorage, JSON.stringify({round_id:id, owner:client}));
      else {
        const old = sharedRound();
        if (old && old.owner === client) localStorage.removeItem(roundStorage);
      }
    } catch (_) {}
  }
  function requestStop(id, epoch) {
    const key = id || epoch;
    if (!key || stopping.has(key)) return;
    const version = revision, generation = connectionGeneration;
    const job = {id, epoch, deadline:performance.now() + connectionGrace, abort:new AbortController(), done:false, timer:null}; stopping.set(key, job);
    job.confirm = () => { job.done = true; clearTimeout(job.timer); stopping.delete(key); job.abort.abort(); };
    async function attempt() {
      if (job.done) return;
      const remaining = job.deadline - performance.now();
      if (remaining <= 0) {
        stopping.delete(key);
        if (!rid) { controlMessage = '本地已退出，后台停止尚未确认；正在等待后台租约结束。'; controls(); }
        return;
      }
      try {
        const result = await rpc('stop', id ? {round_id:id} : {stop_epoch:epoch}, job.abort.signal, Math.min(pollTimeout, remaining));
        if (job.done) return;
        if (id ? result.stopped_round_id !== id : !result.stop_ignored && result.stop_epoch === epoch) throw new Error('后台停止尚未确认');
        if (result.stopped_round_id) retired.add(result.stopped_round_id);
        if (!rid && version === revision && generation === connectionGeneration && result.epoch) status = result;
        job.confirm();
      } catch (_) {
        if (!job.done) job.timer = setTimeout(attempt, Math.min(1000, Math.max(0, job.deadline - performance.now())));
      }
    }
    void attempt();
  }
  function cancelSafetyWatch() {
    clearTimeout(safetyTimer); safetyTimer = null;
    if (safetyAbort) { safetyAbort.abort(); safetyAbort = null; }
  }
  function watchSafety() {
    cancelSafetyWatch();
    if (!rid) return;
    const id = rid, version = revision, epoch = status && status.epoch;
    async function check() {
      const controller = new AbortController(); safetyAbort = controller;
      const timer = setTimeout(() => controller.abort(), 800);
      try {
        // The SDK's read-only context is independent of a hung action response.
        // It never renews the round lease or adopts a round. In particular it
        // carries a native global-key exit to the renderer while poll is stuck.
        const response = await fetch(base + '/hosted-ui/context?kind=panel&id=main', {signal:controller.signal, cache:'no-store'});
        if (!response.ok) return;
        const body = await response.json(), next = body.state;
        if (version !== revision || rid !== id || !next) return;
        if (Array.isArray(next.retired_round_ids) && next.retired_round_ids.includes(id) ||
            typeof next.epoch === 'string' && epoch && next.epoch !== epoch || next.ready === false) stop();
      } catch (_) { /* Only ordinary validated polls decide connection health. */ }
      finally {
        clearTimeout(timer);
        if (safetyAbort === controller) safetyAbort = null;
        if (version === revision && rid === id) safetyTimer = setTimeout(check, 500);
      }
    }
    void check();
  }
  function finish() {
    const old = rid; if (old) retired.add(old);
    stopAudio(); rid = null; pending = false; viewPending = false; revision++;
    cancelSafetyWatch(); publishRound(null); connectionGeneration++;
    clearTimeout(bubbleTimer);
    if (main) {
      if (status && status.round_id === old) status = {...status, active: false};
      $('neko-ads-layer').replaceChildren(); $('neko-ads-feedback').hidden = true;
      controlMessage = '这就认输啦，杂鱼♡'; controls();
    }
    if (old) requestStop(old);
  }
  function stop() { if (main && game && game.active) game.stop(); else finish(); }
  function connected() {
    clearTimeout(connectionTimer); connectionTimer = null; reconnecting = false;
    failureSince = null; connectionExpired = false; outageGeneration++;
  }
  function connectionIssue(failedQuery = true) {
    if (failedQuery && failureSince === null) {
      failureSince = performance.now();
      const generation = ++outageGeneration, deadline = failureSince + connectionGrace;
      function expire() {
        if (generation !== outageGeneration || failureSince === null) return;
        const remaining = deadline - performance.now();
        if (remaining > 0) { connectionTimer = setTimeout(expire, remaining); return; }
        connectionTimer = null; connectionExpired = true; reconnecting = false; available = false;
        const playing = !!rid;
        if (playing) stop();
        controlMessage = playing ? '广告插件持续失联，本局已结束。请重新确认开局。' : '广告插件持续失联，请检查插件运行状态。'; controls();
        if (playing) toast('广告插件持续失联，本局已结束。', true);
      }
      connectionTimer = setTimeout(expire, connectionGrace);
    }
    reconnecting = !connectionExpired; available = false; controlMessage = '';
    $('neko-ads-launcher').hidden = false;
    controls();
  }
  function safe(text) {
    if (String(text || '').normalize('NFKC').replace(/\s/g, '').includes('广告下班')) {
      // A newly opened chat window may receive input before its first poll.
      // Explicit safety-word input always ends the current round, including
      // an in-flight start; it must not depend on a cached chat snapshot.
      const old = rid || (sharedRound() || {}).round_id;
      stopAudio();
      if (!old) {
        let epoch = status && status.stop_epoch;
        try { epoch = epoch || localStorage.getItem('neko-ads-stop-epoch'); } catch (_) {}
        if (main) finish();
        requestStop(null, epoch);
        if (channel && epoch) channel.postMessage({type:'stop-epoch', stop_epoch:epoch});
        return;
      }
      retired.add(old);
      if (main && rid === old) stop(); else requestStop(old);
      if (channel) channel.postMessage({type:'stop', round_id:old});
    }
  }
  function anchor() {
    const bubble = $('neko-ads-feedback');
    const api = window.avatarReactionBubble;
    const at = api && api.getActiveAvatarBubbleAnchor && api.getActiveAvatarBubbleAnchor();
    const rect = at && at.bounds;
    bubble.style.left = Math.max(8, Math.min(innerWidth - bubble.offsetWidth - 8, rect ? (rect.left + rect.right - bubble.offsetWidth) / 2 : innerWidth * .6)) + 'px';
    bubble.style.top = Math.max(8, Math.min(innerHeight - bubble.offsetHeight - 8, rect ? rect.top - bubble.offsetHeight - 12 : innerHeight * .35)) + 'px';
  }
  async function feedback(_line, speak, event) {
    const line = status && status.feedback && status.feedback[event];
    if (!line || !rid || !game.active || game.paused) return;
    const bubble = $('neko-ads-feedback');
    bubble.textContent = character() + '：' + line.text; bubble.hidden = false; anchor();
    clearTimeout(bubbleTimer); bubbleTimer = setTimeout(() => { bubble.hidden = true; }, 5500);
    if (reconnecting) return;
    const chatting = window.appState && window.appState.isPlaying;
    if (chatting) { stopAudio(); return; }
    if (typeof window.applyEmotion === 'function') window.applyEmotion(line.emotion);
    if (!$('neko-ads-voice').checked || !audio) return;
    reactions = reactions || new NekoAds.ReactionQueue({
      play: playSpeech, cancel: cancelAudio,
      valid: () => !!rid && game && game.active && !game.paused && !reconnecting && $('neko-ads-voice').checked && !(window.appState && window.appState.isPlaying),
    });
    reactions.request(event);
  }
  async function playSpeech(event, valid) {
    const ticket = ++speechTicket, id = rid;
    const abort = new AbortController(); speechAbort = abort;
    const parts = []; let size = 0;
    try {
      const clips = status && status.voice_preparation && status.voice_preparation.clips;
      if (clips && !clips[event]) return;
      const job = clips ? {audio_url:clips[event]} : await rpc('speech', {round_id: id, event, sequence: ticket}, abort.signal);
      if (!valid()) return;
      if (job.audio_url && /^\/ui\/voice-cache\/[a-f0-9-]+_[a-z]+\.(?:wav|ogg)$/.test(job.audio_url)) {
        const response = await fetch(base + job.audio_url, {signal:abort.signal,cache:'no-store'});
        if (!response.ok) return;
        const buffer = await audio.decodeAudioData(await response.arrayBuffer());
        if (!valid() || abort.signal.aborted || rid !== id || ticket !== speechTicket) return;
        player = player || new NekoAds.SpeechPlayer(audio); player.enqueue(buffer);
        await player.idle(); return;
      }
      if (!job.job_id) return;
      const deadline = Date.now() + 14000;
      while (!abort.signal.aborted && rid === id && ticket === speechTicket && Date.now() < deadline) {
        const result = await rpc('speech_result', {job_id: job.job_id}, abort.signal);
        if (!valid() || abort.signal.aborted || rid !== id || ticket !== speechTicket) return;
        player = player || new NekoAds.SpeechPlayer(audio);
        if (result.pcm) {
          player.pcm(Uint8Array.from(atob(result.pcm), c => c.charCodeAt(0)), result.sample_rate || 48000);
        }
        if (result.audio_part) {
          const part = Uint8Array.from(atob(result.audio_part), c => c.charCodeAt(0));
          size += part.length;
          if (size > 4000000) { cancelAudio(); return; }
          parts.push(part);
        }
        if (result.audio || result.ready && parts.length) {
          const bytes = result.audio ? Uint8Array.from(atob(result.audio), c => c.charCodeAt(0)) : new Uint8Array(size);
          if (!result.audio) { let at = 0; for (const part of parts) { bytes.set(part, at); at += part.length; } }
          const buffer = await audio.decodeAudioData(bytes.buffer);
          if (!valid() || abort.signal.aborted || rid !== id || ticket !== speechTicket) return;
          player.enqueue(buffer);
        }
        if (result.ready) { if (player) await player.idle(); return; }
        await new Promise(resolve => setTimeout(resolve, 80));
      }
      if (rid === id && ticket === speechTicket) cancelAudio();
    } catch (_) { /* Text and expressions remain usable without TTS. */ }
  }
  function updateVoiceStatus() {
    const preparation = status && status.voice_preparation, enabled = !localVoiceMute && (!status || status.voice_enabled !== false);
    const node = $('neko-ads-voice-status'), retry = $('neko-ads-voice-retry');
    if (!node) return;
    const count = preparation ? preparation.ready + '/' + preparation.total : '0/9';
    node.textContent = !enabled ? '语音已关闭' : !preparation || preparation.state === 'waiting' ? '等待当前猫娘，语音会静默准备。' :
      preparation.state === 'ready' ? '当前猫娘语音已就绪 · ' + count : preparation.state === 'paused' ? '语音准备已暂停 · ' + count :
      preparation.state === 'partial' || preparation.state === 'unavailable' ? '部分语音暂未就绪 · ' + count + '，可重试。' : '正在静默准备语音 · ' + count;
    retry.disabled = !enabled || !available || !preparation || !['paused','partial','unavailable'].includes(preparation.state);
  }
  function render() {
    const focused = document.activeElement;
    const focusedCard = focused && focused.closest && focused.closest('[data-card-id]');
    const focus = focusedCard && {id: focusedCard.dataset.cardId, close: focused.dataset.adClose, tab: focused.dataset.adTab};
    const layer = $('neko-ads-layer'); layer.replaceChildren();
    if (!game) return;
    game.cards.forEach(card => {
      // Asset paths belong to the plugin server, not the host desktop page.
      const artworkURL = card.image ? new URL(card.image, base + '/ui/').href : '';
      const open = (e, link) => {
        const id = rid;
        void NekoAds.openAdLink(e, {
          url: link.href, shell: window.electronShell,
          electron: /Electron/i.test(navigator.userAgent || ''),
          onOpening: () => { viewPending = true; pause(); },
          onError: message => {
            if (id !== rid) return;
            viewPending = false; resume();
            $('neko-ads-state').textContent = message; toast(message, true);
          },
        });
      };
      const modern = window.NekoAdTemplates.create(document, artworkURL ? {...card, image: artworkURL} : card, {
        assets: base + '/ui', links: {project: 'https://project-neko.online/', market: marketBase + '/#/plugins'},
        close: target => game.close(card.id, target), open, change: render,
      });
      let box, bar;
      if (modern) ({box, bar} = modern);
      else {
        box = el('article', undefined, 'neko-ad-placeholder neko-ad-legacy'); box.dataset.cardId = card.id;
        bar = el('header'); bar.append(el('span', card.subtitle));
        const close = el('button', '×'); close.type = 'button'; close.dataset.adClose = 'frame';
        close.setAttribute('aria-label', '关闭广告：' + card.title);
        close.onpointerenter = e => { if (e.pointerType !== 'touch') game.hover(card.id); };
        close.onclick = e => { e.stopPropagation(); game.close(card.id); }; bar.append(close);
        const link = el('a', undefined, 'neko-ad-promotion');
        link.href = card.pluginId ? marketBase + '/#/plugin/' + encodeURIComponent(card.pluginId) : card.kind === 'market' ? marketBase + '/#/plugins' : card.kind === 'nearby' ? 'https://github.com/Project-N-E-K-O/N.E.K.O#readme' : card.kind === 'scan' ? 'https://project-neko.online/zh-CN/' : 'https://project-neko.online/';
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        const media = el('div', artworkURL ? undefined : '广告图片占位', 'neko-ad-image-placeholder');
        if (artworkURL) {
          const image = el('img', undefined, 'neko-ad-legacy-art');
          media.className += ' neko-ad-has-image';
          image.src = artworkURL; image.alt = card.imageAlt || ''; image.draggable = false;
          image.onerror = () => { media.className = 'neko-ad-image-placeholder'; media.textContent = '广告图片占位'; };
          media.append(image);
        }
        if (!card.textOnly) link.append(media);
        link.append(el('h3', card.title), el('p', card.body), el('strong', card.action));
        link.onclick = e => open(e, link); link.onauxclick = e => open(e, link);
        const later = el('button', '稍后提醒'); later.type = 'button'; later.onclick = () => game.postpone(card.id);
        box.append(bar, link, later);
      }
      layer.append(box);
      box.style.left = Math.round(card.x * Math.max(0, innerWidth - box.offsetWidth - 20)) + 'px';
      box.style.top = Math.round(card.y * Math.max(0, innerHeight - box.offsetHeight - 120)) + 'px';
      avoidInput(box);
      if (focus && focus.id === String(card.id)) {
        const selector = focus.close ? '[data-ad-close="' + focus.close + '"]' : focus.tab ? '[data-ad-tab="' + focus.tab + '"]' : null;
        const node = selector && box.querySelector(selector); if (node) node.focus({preventScroll: true});
      }
      bar.onpointerdown = e => {
        if (e.button !== 0 || e.target.closest('button,a')) return;
        const x = box.offsetLeft, y = box.offsetTop, sx = e.clientX, sy = e.clientY; bar.setPointerCapture(e.pointerId);
        bar.onpointermove = move => {
          box.style.left = Math.max(0, Math.min(innerWidth - box.offsetWidth, x + move.clientX - sx)) + 'px';
          box.style.top = Math.max(0, Math.min(innerHeight - box.offsetHeight - 80, y + move.clientY - sy)) + 'px';
        };
        bar.onpointerup = () => { card.x = box.offsetLeft / Math.max(1, innerWidth - box.offsetWidth - 20); card.y = box.offsetTop / Math.max(1, innerHeight - box.offsetHeight - 120); bar.onpointermove = null; };
      };
    });
  }
  function avoidInput(box) {
    const inputs = [];
    document.querySelectorAll('textarea,[contenteditable="true"]').forEach(input => {
      const r = input.getBoundingClientRect(), b = box.getBoundingClientRect();
      if (r.width < 30 || r.height < 16) return;
      inputs.push(r);
      if (b.left < r.right && b.right > r.left && b.top < r.bottom && b.bottom > r.top) {
        box.style.top = Math.max(8, r.top - box.offsetHeight - 16) + 'px';
      }
    });
    const launcher = $('neko-ads-launcher');
    if (!launcher || launcher.hidden) return;
    const r = launcher.getBoundingClientRect(), b = box.getBoundingClientRect();
    // On a narrow host the fixed launcher otherwise covers a candidate X.
    if (b.left < r.right && b.right > r.left && b.top < r.bottom && b.top + 90 > r.top) {
      const top = r.bottom + 10;
      const bottom = Math.min(innerHeight - 80, ...inputs.filter(input => input.top > top).map(input => input.top - 16));
      if (bottom - top >= 96) {
        box.style.maxHeight = Math.min(box.offsetHeight, bottom - top) + 'px';
        box.style.top = top + 'px';
      }
    }
  }
  function loadEngine() {
    if (window.NekoAds && window.NekoAdTemplates) return Promise.resolve();
    const load = name => new Promise((resolve, reject) => {
      const script = document.createElement('script'); script.crossOrigin = 'anonymous'; script.src = base + '/ui/' + name + '?v=' + bridgeVersion;
      script.onload = resolve; script.onerror = () => reject(new Error('广告资源加载失败')); document.head.append(script);
    });
    if (!loading) loading = (async () => {
      if (!window.NekoAds) await load('engine.js');
      if (!window.NekoAdTemplates) await load('ad-templates.js');
    })().catch(error => { loading = null; throw error; });
    return loading;
  }
  function unlockAudio() { try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume().catch(() => {}); } catch (_) {} }
  async function adopt(next) {
    status = next;
    if (!next.active || next.owner !== client || retired.has(next.round_id)) { if (rid && !pending) stop(); return; }
    if (game && game.active && rid === next.round_id) {
      selected = next.shortcut; game.shortcut = selected; updateNotice(); return;
    }
    const version = revision;
    if (!window.NekoAds || !window.NekoAdTemplates) {
      await loadEngine();
      // Validate against the next scheduled heartbeat after assets arrive.
      // An immediate duplicate query adds two host IPC trips and can leave
      // adoption stuck retrying despite a healthy preceding heartbeat.
      return;
    }
    if (version !== revision || retired.has(next.round_id)) return;
    status = next;
    rid = next.round_id; selected = next.shortcut; pending = false;
    $('neko-ads-voice').checked = !localVoiceMute && next.voice_enabled !== false;
    unlockAudio();
    game = new NekoAds.Game({shortcut: selected, onChange: render, onFeedback: feedback, onStop: finish});
    game.catalog = NekoAds.normalizeCatalog({items: catalog});
    $('neko-ads-dialog').close(); controlMessage = ''; updateNotice(); game.start(); controls();
    publishRound(rid); watchSafety();
  }
  async function start() {
    if (pending || rid || status && status.active || !available || !status.ready) return;
    pending = true; controlMessage = ''; const version = ++revision; rid = crypto.randomUUID(); const id = rid; publishRound(id); watchSafety(); unlockAudio(); controls();
    let next;
    try {
      next = await rpc('start', {round_id:id, epoch:status.epoch, stop_epoch:status.stop_epoch, owner:client, confirmed:true, shortcut:selected, voice_enabled:$('neko-ads-voice').checked});
    } catch (error) {
      if (version === revision) { finish(); controlMessage = error.message; controls(); }
      return;
    }
    if (version !== revision) { requestStop(id); return; }
    pending = false;
    try { await adopt(next); }
    catch (_) { if (version === revision && rid === id) connectionIssue(); }
  }
  function updateNotice() {
    $('neko-ads-key').value = selected;
    $('neko-ads-notice').textContent = '输入安全词「广告下班」或按 ' + selected + ' 可以立即结束。';
  }
  async function recordKey(e) {
    e.preventDefault();
    if (pending || recording || ['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return;
    const mods = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift'].filter(Boolean);
    const key = /^Digit\d$/.test(e.code) ? e.code.slice(5) : /^Key[A-Z]$/.test(e.code) ? e.code.slice(3) : /^F(?:[1-9]|10|11)$/.test(e.code) ? e.code : '';
    if (!mods.length || !key || e.metaKey) { $('neko-ads-state').textContent = '请使用修饰键与字母、数字或 F1–F11 组合'; return; }
    const next = [...mods, key].join('+'); recording = true; controls();
    try {
      if (rid) await rpc('shortcut', {round_id: rid, shortcut: next});
      selected = next; if (game) game.shortcut = next; localStorage.setItem(storage, next); updateNotice();
    } catch (error) { controlMessage = error.message; }
    finally { recording = false; controls(); }
  }
  function exitKey(e) {
    const parts = selected.split('+'), key = parts.pop();
    return !e.metaKey && !!e.ctrlKey === parts.includes('Ctrl') && !!e.altKey === parts.includes('Alt') && !!e.shiftKey === parts.includes('Shift') &&
      (e.code === (/^\d$/.test(key) ? 'Digit' + key : /^F\d+$/.test(key) ? key : 'Key' + key));
  }
  function pause() { if (game && game.active) { game.pause(); stopAudio(); $('neko-ads-feedback').hidden = true; } }
  function resume() { if (game && game.active && !document.hidden) { game.resume(); if (viewPending) { viewPending = false; game.feedback('view'); } } }
  function init() {
    document.addEventListener('input', e => { if (e.target.type !== 'password') safe(e.target.value || e.target.textContent); }, true);
    document.addEventListener('compositionend', e => safe(e.data), true);
    window.addEventListener('neko:user-voice-content-received', e => safe(e.detail && e.detail.text));
    if (channel) channel.onmessage = e => {
      const id = e.data && e.data.round_id;
      if (main && e.data && e.data.type === 'stop-epoch' && status && e.data.stop_epoch === status.stop_epoch) {
        const epoch = status.stop_epoch; stop(); requestStop(null, epoch);
      }
      if (e.data && e.data.type === 'stop' && typeof id === 'string') {
        retired.add(id);
        if (main && rid === id) stop();
      }
    };
    // Chat windows only forward explicit safety input. They do not own the
    // round, and a cached round ID is not needed for the global safety stop.
    if (!main) return;
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.crossOrigin = 'anonymous'; css.href = base + '/ui/desktop.css?v=' + bridgeVersion; document.head.append(css);
    const launcher = el('button', '猫娘广告部 · 待机中', 'neko-ads-launcher'); launcher.id = 'neko-ads-launcher'; launcher.hidden = true;
    const dialog = el('dialog'); dialog.id = 'neko-ads-dialog';
    const notice = el('p'); notice.id = 'neko-ads-notice';
    const label = el('label', '退出快捷键（点击后按组合键）'), key = el('input'); key.id = 'neko-ads-key'; key.readOnly = true; key.onkeydown = recordKey; label.append(key);
    const voiceLabel = el('label', '当前猫娘语音反馈 '), voice = el('input'); voice.type = 'checkbox'; voice.checked = true; voice.id = 'neko-ads-voice'; voiceLabel.append(voice);
    voice.onchange = async () => {
      const enabled = voice.checked, version = revision; changingVoice = true;
      localVoiceMute = !enabled;
      if (!enabled) stopAudio();
      try { const next = await rpc('voice', {enabled}); if (version === revision) status = next; }
      catch (error) { controlMessage = error.message; }
      finally { changingVoice = false; controls(); }
    };
    const voiceState = el('p'); voiceState.id = 'neko-ads-voice-status'; voiceState.setAttribute('role','status');
    const voiceRetry = el('button','继续／重试语音准备'); voiceRetry.id = 'neko-ads-voice-retry';
    voiceRetry.onclick = async () => { try { status = await rpc('prepare_voice'); updateVoiceStatus(); } catch (_) {} };
    const state = el('p'); state.id = 'neko-ads-state'; state.setAttribute('role','status');
    const begin = el('button', '杂鱼♡让本喵占领桌面！'); begin.id = 'neko-ads-start'; begin.onclick = start;
    begin.setAttribute('aria-label', '开始广告游戏：杂鱼♡让本喵占领桌面！');
    const end = el('button', '杂鱼♡这就认输啦？'); end.id = 'neko-ads-stop'; end.onclick = stop;
    const back = el('button', '杂鱼♡先放你回去（返回主页面）'); back.id = 'neko-ads-back'; back.onclick = () => dialog.close();
    dialog.append(el('h2','猫娘广告部：杂鱼桌面占领计划'), notice, label, voiceLabel, voiceState, voiceRetry, state, begin, end, back);
    const layer = el('div'); layer.id = 'neko-ads-layer';
    const bubble = el('div'); bubble.id = 'neko-ads-feedback'; bubble.hidden = true; bubble.setAttribute('role', 'status');
    document.body.append(launcher, dialog, layer, bubble); updateNotice(); controls();
    launcher.onclick = () => { controls(); dialog.showModal(); };
    document.addEventListener('pointerdown', () => { if (rid) unlockAudio(); }, true);
    document.addEventListener('keydown', e => {
      if (!rid || e.target === key) return;
      if (game && game.active ? game.key(e) : exitKey(e) && (stop(), true)) { e.preventDefault(); e.stopImmediatePropagation(); }
    }, true);
    window.addEventListener('blur', pause); window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', () => document.hidden ? pause() : resume());
    window.addEventListener('offline', () => connectionIssue(false)); window.addEventListener('pagehide', () => { if (rid) stop(); });
    window.addEventListener('resize', render);
    window.addEventListener('neko-assistant-speech-start', () => { stopAudio(); bubble.hidden = true; });
    async function poll() {
      const version = revision, id = rid;
      let delay = rid ? activePollInterval : idlePollInterval;
      try {
        const name = character();
        if (!name) { delay = 500; return; }
        const next = await pollState();
        if (version !== revision || id !== rid) return;
        connectionGeneration++;
        for (const job of stopping.values()) {
          if (job.id ? Array.isArray(next.retired_round_ids) && next.retired_round_ids.includes(job.id) : next.stop_epoch && next.stop_epoch !== job.epoch) job.confirm();
        }
        if (!next.active && controlMessage.startsWith('本地已退出，后台停止尚未确认')) controlMessage = '后台已确认结束，插件继续待机。';
        const recovered = reconnecting || connectionExpired;
        if (recovered) controlMessage = '';
        connected();
        connectionPolicy(next);
        delay = idlePollInterval;
        if (next.voice_enabled === false || status && status.voice_preparation && next.voice_preparation && status.voice_preparation.token !== next.voice_preparation.token) stopAudio();
        if (!changingVoice) voice.checked = !localVoiceMute && next.voice_enabled !== false;
        if (next.ready && status && !status.ready) controlMessage = '';
        status = next; available = true; launcher.hidden = !next.ready && !rid;
        try { if (next.stop_epoch) localStorage.setItem('neko-ads-stop-epoch', next.stop_epoch); } catch (_) {}
        if (next.ready && !wasReady) {
          toast('猫娘广告部已启用，正在待机。点击左上角入口开始游戏。');
        }
        wasReady = !!next.ready;
        if (!pending) await adopt(next);
        if (version !== revision) return;
        if (rid && !pending) {
          anchor(); document.querySelectorAll('.neko-ad-placeholder').forEach(avoidInput);
          if (recovered) controlMessage = '连接已恢复，广告部继续营业。';
        }
        controls();
      } catch (_) {
        if (version !== revision || id !== rid) return;
        connectionIssue();
        checkPluginStopped(version, id);
      } finally { setTimeout(poll, rid ? activePollInterval : delay); }
    }
    poll();
    Promise.all([fetch(base.replace(/\/plugin\/neko_ads$/, '') + '/market/catalog/api/v1/plugins?page=1&page_size=12').then(r => r.json()), fetch(base.replace(/\/plugin\/neko_ads$/, '') + '/market/status').then(r => r.json())])
      .then(([items, market]) => { const url = new URL(market.market_web_url || market.market_url); if (['http:','https:'].includes(url.protocol) && !url.username && !url.password) { marketBase = url.origin + url.pathname.replace(/\/$/, ''); catalog = items.items || []; } }).catch(() => {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true}); else init();
})();
