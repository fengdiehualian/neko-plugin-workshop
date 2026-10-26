/* 斗地主 · 卡牌大师 —— 浏览器面板前端（Hosted UI） */
const pluginId = 'card_master';
const RUNS_URL = '/runs';
const RUN_POLL_DELAY_MS = 400;
const CSRF_BOOTSTRAP_TIMEOUT_MS = 5000;
const CSRF_BOOTSTRAP_RETRY_TIMEOUT_MS = 30000;

let csrfTokenPromise = null;

async function mutationHeaders(timeoutMs = CSRF_BOOTSTRAP_TIMEOUT_MS) {
  if (!csrfTokenPromise) {
    const bootstrapAbort = new AbortController();
    const bootstrapTimer = setTimeout(() => bootstrapAbort.abort(), timeoutMs);
    csrfTokenPromise = fetch('/security/csrf-token', { credentials: 'same-origin', signal: bootstrapAbort.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`CSRF token bootstrap failed: HTTP ${response.status}`);
        const data = await response.json();
        if (!data || typeof data.csrf_token !== 'string' || !data.csrf_token) {
          throw new Error('CSRF token bootstrap returned an invalid token');
        }
        return data.csrf_token;
      })
      .finally(() => clearTimeout(bootstrapTimer));
  }
  const pending = csrfTokenPromise;
  let waitTimer;
  const wait = new Promise((resolve) => {
    waitTimer = setTimeout(resolve, timeoutMs);
  });
  await wait;
  clearTimeout(waitTimer);
  if (!pending) return {};
  try {
    const token = await pending;
    if (token) return { 'X-CSRF-Token': token };
    console.warn('CSRF token not ready; sending without it');
    return {};
  } catch (error) {
    if (csrfTokenPromise === pending) csrfTokenPromise = null;
    console.warn('CSRF token unavailable; sending without it:', error);
    return {};
  }
}

async function isCsrfTokenFailure(response) {
  if (response.headers.get('X-CSRF-Failure') === 'token') return true;
  try {
    const data = await response.clone().json();
    return Boolean(data && data.detail && data.detail.csrf_failure === 'token');
  } catch (e) { /* not json */ }
  return false;
}

async function postWithCsrfRetry(url, body) {
  for (let attempt = 0; ; attempt += 1) {
    const headers = await mutationHeaders(attempt === 0 ? CSRF_BOOTSTRAP_TIMEOUT_MS : CSRF_BOOTSTRAP_RETRY_TIMEOUT_MS);
    headers['Content-Type'] = 'application/json';
    const resp = await fetch(url, { method: 'POST', headers, body, credentials: 'same-origin' });
    if (attempt > 0 || resp.status !== 403 || !(await isCsrfTokenFailure(resp))) return resp;
    csrfTokenPromise = null;
  }
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function callPlugin(entry, args = {}, tolerateErrors = []) {
  const resp = await postWithCsrfRetry(RUNS_URL, JSON.stringify({ plugin_id: pluginId, entry_id: entry, args }));
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const { run_id, id } = await resp.json();
  const runId = run_id || id;
  if (!runId) throw new Error('未获取到 run_id');

  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    const poll = await fetch(`${RUNS_URL}/${runId}`);
    if (!poll.ok) { await delay(RUN_POLL_DELAY_MS); continue; }
    const rec = await poll.json();
    if (rec.status === 'succeeded') {
      const exp = await fetch(`${RUNS_URL}/${runId}/export`);
      if (!exp.ok) return {};
      const { items = [] } = await exp.json();
      let item = null;
      for (const it of items) {
        if (it && typeof it === 'object' && (it.json || it.json_data)) { item = it; break; }
      }
      if (!item) return {};
      let raw = item.json || item.json_data || {};
      /* ok()/fail() 信封：外层含 success/error/time 键，data 才是 action 返回值 */
      if (raw && typeof raw === 'object' && 'success' in raw && 'error' in raw) {
        if (raw.data && typeof raw.data === 'object') {
          raw = raw.data;
        } else if (raw.success === false && raw.error) {
          throw new Error((raw.error && raw.error.message) || '插件调用失败');
        }
      }
      /* 兼容旧式嵌套（data 内含 success/error 标记） */
      while (raw && raw.data && typeof raw.data === 'object' && ('success' in raw.data || 'error' in raw.data)) {
        raw = raw.data;
      }
      if (raw && raw.error) {
        if (tolerateErrors.includes(String(raw.error))) return raw;
        throw new Error(String(raw.error) === 'NO_GAME' ? '还没有牌局，请先点「再来一局」' : (raw.output || '插件调用失败'));
      }
      return raw;
    }
    if (['failed', 'canceled', 'timeout'].includes(rec.status)) {
      throw new Error((rec.error && rec.error.message) || rec.message || rec.status);
    }
    await delay(RUN_POLL_DELAY_MS);
  }
  throw new Error('调用超时');
}

/* ── 牌面渲染 ── */
const SUITS = ['♠', '♥', '♦', '♣'];

function parseCard(cardStr) {
  const s = String(cardStr || '').trim();
  if (s === '小王' || s === '大王') return { rank: s, suit: '', joker: true };
  for (const suit of SUITS) {
    if (s.endsWith(suit)) return { rank: s.slice(0, -suit.length), suit, joker: false };
  }
  return { rank: s, suit: '', joker: false };
}

function isRed(card) {
  return card.joker || card.suit === '♥' || card.suit === '♦';
}

function cardEl(cardStr, extraClass = '') {
  const c = parseCard(cardStr);
  const el = document.createElement('span');
  el.className = `card ${isRed(c) ? 'red' : 'black'}${c.joker ? ' joker' : ''} ${extraClass}`.trim();
  const rank = document.createElement('span');
  rank.className = 'rank';
  rank.textContent = c.rank;
  const suit = document.createElement('span');
  suit.className = 'suit';
  suit.textContent = c.suit;
  el.appendChild(rank);
  el.appendChild(suit);
  const cornerText = c.rank + c.suit;
  for (const pos of ['tl', 'br']) {
    const corner = document.createElement('span');
    corner.className = `corner ${pos}`;
    corner.textContent = cornerText;
    el.appendChild(corner);
  }
  return el;
}

function miniEl(cardStr) {
  const c = parseCard(cardStr);
  const el = document.createElement('span');
  el.className = `mini ${isRed(c) ? 'red' : 'black'}`;
  el.textContent = c.rank + c.suit;
  return el;
}

/* ── 状态渲染 ── */
let currentState = { stage: 'none' };
const selected = new Set();

const $ = (id) => document.getElementById(id);

function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function renderState(state) {
  currentState = state || { stage: 'none' };
  selected.clear();

  const stats = state.stats || { wins: 0, losses: 0 };
  $('stats').textContent = `胜 ${stats.wins} / 负 ${stats.losses}`;

  /* 叫地主区域 */
  const bid = state.bid_state || { bidding: false, current: -1, current_name: '', bid_history: [] };
  const bidding = !!bid.bidding;
  $('bid-area').classList.toggle('hidden', !bidding);
  const ctrl = $('controls');
  if (ctrl) ctrl.style.display = bidding ? 'none' : 'flex';
  if (bidding) {
    const logBox = $('bid-log');
    clearChildren(logBox);
    (bid.bid_history || []).forEach((h) => {
      const span = document.createElement('span');
      span.className = 'bid-item';
      span.textContent = `${h.player}：${h.bid === 0 ? '不叫' : h.bid + '分'}`;
      logBox.appendChild(span);
    });
    const myTurn = bid.current === 0;
    [$('btn-bid0'), $('btn-bid1'), $('btn-bid2'), $('btn-bid3')].forEach((b) => { if (b) b.disabled = !myTurn; });
  }

  /* 底牌 */
  const bottomBox = $('bottom-cards');
  clearChildren(bottomBox);
  bottomBox.classList.remove('reveal');
  if (state.stage && state.stage !== 'none') {
    if (state.stage === 'bidding') {
      for (let i = 0; i < 3; i += 1) {
        const b = document.createElement('span');
        b.className = 'mini back';
        bottomBox.appendChild(b);
      }
    } else {
      (state.bottom || []).forEach((c) => bottomBox.appendChild(miniEl(c)));
    }
    if (state.stage === 'playing') bottomBox.classList.add('reveal');
  }

  /* AI 面板 */
  const players = state.players || [];
  const left = players.find((p) => p.name === 'YUI_左') || { name: 'YUI_左', role: '', count: 0 };
  const right = players.find((p) => p.name === 'YUI_右') || { name: 'YUI_右', role: '', count: 0 };
  $('ai-left-name').textContent = left.name;
  $('ai-left-role').textContent = bidding ? '叫分中' : (left.role || '');
  $('ai-left-role').className = 'ai-role' + (left.role === '地主' ? ' lord' : left.role === '农民' ? ' farmer' : '');
  $('ai-left-count').textContent = left.count;
  $('ai-right-name').textContent = right.name;
  $('ai-right-role').textContent = bidding ? '叫分中' : (right.role || '');
  $('ai-right-role').className = 'ai-role' + (right.role === '地主' ? ' lord' : right.role === '农民' ? ' farmer' : '');
  $('ai-right-count').textContent = right.count;

  /* 玩家信息 */
  $('player-role').textContent = state.user_role ? `你是${state.user_role}` : '';
  $('player-must-hint').textContent = state.stage === 'none' ? '还没有牌局，先开一局' : '';

  /* 上一手 */
  const lastPlayBox = $('last-play');
  clearChildren(lastPlayBox);
  if (state.last_play && state.last_play.length) {
    state.last_play.forEach((c) => lastPlayBox.appendChild(miniEl(c)));
    $('last-player').textContent = `${state.last_player} 出了：`;
  } else if (state.stage === 'playing') {
    $('last-player').textContent = '新的一轮，先手出牌';
  } else if (state.stage === 'over') {
    $('last-player').textContent = '本局结束';
  } else {
    $('last-player').textContent = '';
  }

  /* 轮次提示 */
  const hint = $('turn-hint');
  if (state.stage === 'over') {
    const won = state.winner === '你';
    hint.textContent = won ? '🎉 你赢了！' : `😔 你输了（${state.winner || ''}获胜）`;
  } else if (state.stage === 'none') {
    hint.textContent = '';
  } else if (bidding) {
    hint.textContent = bid.current === 0 ? '轮到你叫地主 👑' : `${bid.current_name} 思考叫分中…`;
  } else if (state.stage !== 'playing') {
    hint.textContent = '等待开始…';
  } else if (state.current === '你') {
    hint.textContent = state.must_play ? '轮到你出牌（先手，必须出）' : '轮到你出牌';
  } else {
    hint.textContent = `${state.current} 思考中…`;
  }

  /* 手牌 */
  const handBox = $('player-hand');
  clearChildren(handBox);
  (state.user_hand || []).forEach((cardStr, idx) => {
    const el = cardEl(cardStr);
    el.dataset.idx = String(idx);
    el.dataset.card = cardStr;
    el.addEventListener('click', () => {
      if (state.stage !== 'playing' || state.current !== '你') return;
      if (selected.has(idx)) { selected.delete(idx); el.classList.remove('selected'); }
      else { selected.add(idx); el.classList.add('selected'); }
      updateButtons();
    });
    handBox.appendChild(el);
  });

  updateButtons();
}

function updateButtons() {
  const playing = currentState.stage === 'playing' && currentState.current === '你';
  $('btn-play').disabled = !playing || selected.size === 0;
  $('btn-pass').disabled = !playing || !!currentState.must_play;
  $('btn-new').disabled = false;
}

/* ── 动作 ── */
let busy = false;

async function runAction(entry, args = {}, showOutput = true) {
  if (busy) return;
  busy = true;
  try {
    const raw = await callPlugin(entry, args);
    if (raw && raw.state) renderState(raw.state);
    else renderState({ stage: 'none' });
    if (showOutput && raw && raw.output) toast(raw.output);
  } catch (e) {
    toast(e.message || '操作失败');
    try { const s = await callPlugin('dd_ui_state', {}, ['NO_GAME']); if (s && s.state) renderState(s.state); } catch (e2) { /* ignore */ }
  } finally {
    busy = false;
  }
}

function toast(msg) {
  const t = $('msg-toast');
  t.textContent = String(msg).replace(/^\s+|\s+$/g, '');
  t.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.remove('show'), 2600);
}

/* ── 事件绑定 ── */
$('btn-play').addEventListener('click', () => {
  const cards = Array.from(selected).sort((a, b) => a - b).map((idx) => {
    const el = document.querySelector(`#player-hand .card[data-idx="${idx}"]`);
    return el ? el.dataset.card : '';
  }).filter(Boolean);
  if (!cards.length) return;
  runAction('dd_ui_play', { cards });
});

$('btn-pass').addEventListener('click', () => runAction('dd_ui_pass', {}));
$('btn-new').addEventListener('click', () => runAction('dd_ui_new_game', {}));

$('btn-bid0').addEventListener('click', () => runAction('dd_ui_bid', { score: 0 }));
$('btn-bid1').addEventListener('click', () => runAction('dd_ui_bid', { score: 1 }));
$('btn-bid2').addEventListener('click', () => runAction('dd_ui_bid', { score: 2 }));
$('btn-bid3').addEventListener('click', () => runAction('dd_ui_bid', { score: 3 }));

/* 初始加载 */
(async function init() {
  try {
    const s = await callPlugin('dd_ui_state', {}, ['NO_GAME']);
    if (s && s.state) renderState(s.state);
    else renderState({ stage: 'none' });
  } catch (e) {
    renderState({ stage: 'none' });
    toast('面板初始化失败：' + (e.message || '未知错误'));
  }
})();
