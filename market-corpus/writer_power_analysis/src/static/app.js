// ── Shared state & API for writer_power_analysis static UI ──
const PLUGIN_ID = 'writer_power_analysis';
const RUNS_URL = '/runs';

// ── API ────────────────────────────────────────────────────
async function callPlugin(entry, args = {}, timeoutMs = 120000) {
  const body = JSON.stringify({ plugin_id: PLUGIN_ID, entry_id: entry, args });
  let resp;
  try {
    resp = await fetch(RUNS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
  } catch (e) {
    console.error('[callPlugin] fetch POST failed:', e);
    throw new Error('网络请求失败: ' + e.message);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    console.error('[callPlugin] POST /runs', resp.status, text);
    throw new Error(`服务器返回 ${resp.status}`);
  }
  const json = await resp.json();
  console.log('[callPlugin] POST /runs for', entry, ':', json);
  const runId = json.run_id || json.id;
  if (!runId) {
    console.warn('[callPlugin] no run_id for', entry, ', treating as sync:', json);
    return unwrapResult(json);
  }

  const deadline = Date.now() + timeoutMs;
  let delay = 300;
  let pollCount = 0;
  while (Date.now() < deadline) {
    pollCount++;
    await sleep(delay);
    const poll = await fetch(`${RUNS_URL}/${runId}`).catch(() => null);
    if (!poll || !poll.ok) continue;
    const rec = await poll.json().catch(() => null);
    if (!rec) continue;
    if (['succeeded', 'failed', 'canceled', 'timeout'].includes(rec.status)) {
      console.log('[callPlugin] run', runId, 'status:', rec.status, 'entry:', entry, 'polls:', pollCount);
      if (rec.status !== 'succeeded') {
        throw new Error(rec.error?.message || rec.message || rec.status);
      }
      const exp = await fetch(`${RUNS_URL}/${runId}/export`).catch(() => null);
      if (!exp || !exp.ok) { console.warn('[callPlugin] export fetch failed for', entry); return {}; }
      const exported = await exp.json().catch(() => ({}));
      console.log('[callPlugin] export for', entry, ':', JSON.stringify(exported).substring(0, 300));
      const { items = [] } = exported;
      const item = items.find(i => i.type === 'json' && i.json) || items[0];
      if (!item) { console.warn('[callPlugin] no export items for', entry); return {}; }
      return unwrapResult(item.json || {});
    }
    delay = Math.min(delay * 1.5, 5000);
  }
  throw new Error('调用超时');
}

function unwrapResult(raw) {
  console.log('[callPlugin] unwrap input:', JSON.stringify(raw).substring(0, 300));
  // unwrap outer data nesting: {success: true, data: {...}} or {data: {success: true, ...}}
  while (raw && raw.data && typeof raw.data === 'object' && ('success' in raw.data || 'error' in raw.data || 'task_id' in raw.data)) {
    raw = raw.data;
  }
  // unwrap Ok/Err envelope: {success: true, result: {...}}
  if (raw && typeof raw === 'object' && raw.success === true && 'result' in raw) {
    console.log('[callPlugin] unwrap: Ok envelope, result keys:', Object.keys(raw.result || {}));
    return raw.result;
  }
  // unwrap {success: true, data: {...}} (no result field)
  if (raw && typeof raw === 'object' && raw.success === true && 'data' in raw) {
    console.log('[callPlugin] unwrap: Ok+data envelope, data keys:', Object.keys(raw.data || {}));
    return raw.data;
  }
  if (raw && typeof raw === 'object' && raw.success === false && raw.error) {
    throw new Error(raw.error);
  }
  // sometimes result is direct { result: {...} } without success wrapper
  // BUT only if the object has NO other metadata fields (task_id, status, etc.)
  if (raw && typeof raw === 'object' && 'result' in raw && !('success' in raw)
      && !('task_id' in raw) && !('status' in raw) && !('source' in raw)) {
    return raw.result;
  }
  return raw;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── State (localStorage-backed) ────────────────────────────
const LS = {
  get(key, fallback) {
    try { const v = localStorage.getItem('wpa_' + key); return v !== null ? JSON.parse(v) : fallback; }
    catch (_) { return fallback; }
  },
  set(key, val) { localStorage.setItem('wpa_' + key, JSON.stringify(val)); }
};

// ── Helpers ────────────────────────────────────────────────
function $q(sel, parent) { return (parent || document).querySelector(sel); }
function $qa(sel, parent) { return (parent || document).querySelectorAll(sel); }
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function fmtDate(ts) { return new Date(ts).toLocaleString(); }

function scoreTone(score) {
  if (typeof score !== 'number') return 'default';
  if (score >= 80) return 'success';
  if (score >= 60) return 'primary';
  if (score >= 40) return 'warning';
  return 'danger';
}

const SCORE_CLASS = { success: 'badge-green', primary: 'badge-blue', warning: 'badge-yellow', danger: 'badge-red', default: 'badge-gray' };

// ── Report visualizations ─────────────────────────────────
function dimensionMax(name) {
  const text = String(name || '');
  if (text.includes('经典')) return 2;
  if (text.includes('新锐')) return 1.5;
  return 5;
}

function cleanDimensionName(name) {
  return String(name || '未命名')
    .replace(/^[^\u4e00-\u9fa5A-Za-z0-9]+/, '')
    .replace(/（.*?）/g, '')
    .replace(/\s+/g, '')
    .slice(0, 6);
}

function findDimension(dimensions, keywords) {
  return (dimensions || []).find(d => keywords.some(k => String(d?.name || '').includes(k)));
}

function normalizedDimensionScore(dimension) {
  if (!dimension || typeof dimension.score !== 'number') return 0;
  return Math.max(0, Math.min(1, dimension.score / dimensionMax(dimension.name)));
}

function avgNormalized(dimensions, keywordGroups) {
  const values = keywordGroups
    .map(group => normalizedDimensionScore(findDimension(dimensions, group)))
    .filter(v => v > 0);
  if (!values.length) return 0;
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

function wrapSvgText(text, maxChars) {
  const raw = String(text || '');
  if (raw.length <= maxChars) return [raw];
  const chunks = [];
  for (let i = 0; i < raw.length; i += maxChars) chunks.push(raw.slice(i, i + maxChars));
  return chunks.slice(0, 3);
}

function renderRadarChart(dimensions) {
  const base = (dimensions || []).filter(d => d && d.name && !String(d.name).includes('经典') && !String(d.name).includes('新锐')).slice(0, 14);
  if (base.length < 3) return '<p class="muted">维度数据不足，无法生成雷达图。</p>';

  const size = 420;
  const cx = size / 2;
  const cy = size / 2;
  const radius = 132;
  const levels = [0.2, 0.4, 0.6, 0.8, 1];
  const n = base.length;
  const pointAt = (index, scale) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index / n);
    return [cx + Math.cos(angle) * radius * scale, cy + Math.sin(angle) * radius * scale];
  };
  const polygon = (scale) => base.map((_, i) => pointAt(i, scale).map(v => v.toFixed(1)).join(',')).join(' ');
  const dataPoints = base.map((d, i) => pointAt(i, normalizedDimensionScore(d)).map(v => v.toFixed(1)).join(',')).join(' ');

  const grid = levels.map(level => `<polygon points="${polygon(level)}" class="radar-grid" />`).join('');
  const axes = base.map((_, i) => {
    const [x, y] = pointAt(i, 1);
    return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="radar-axis" />`;
  }).join('');
  const labels = base.map((d, i) => {
    const [x, y] = pointAt(i, 1.19);
    const lines = wrapSvgText(cleanDimensionName(d.name), 4);
    const anchor = Math.abs(x - cx) < 12 ? 'middle' : (x > cx ? 'start' : 'end');
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${anchor}" class="radar-label">${lines.map((line, idx) => `<tspan x="${x.toFixed(1)}" dy="${idx === 0 ? 0 : 13}">${esc(line)}</tspan>`).join('')}</text>`;
  }).join('');

  return `<div class="radar-wrap">
    <svg class="radar-svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="维度雷达图">
      ${grid}${axes}
      <polygon points="${dataPoints}" class="radar-area" />
      <polyline points="${dataPoints} ${dataPoints.split(' ')[0]}" class="radar-line" />
      ${base.map((d, i) => {
        const [x, y] = pointAt(i, normalizedDimensionScore(d));
        return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.2" class="radar-point"><title>${esc(d.name)}: ${esc(String(d.score ?? '-'))}/${dimensionMax(d.name)}</title></circle>`;
      }).join('')}
      ${labels}
    </svg>
  </div>`;
}

function buildStyleProfile(dimensions) {
  return [
    { label: '语言辨识度', value: avgNormalized(dimensions, [['文体'], ['语言原创']]), note: '文体魅力与语言原创性的综合表现' },
    { label: '叙事组织力', value: avgNormalized(dimensions, [['结构复杂'], ['稳定性']]), note: '结构复杂度与完成度的合力' },
    { label: '情绪穿透力', value: avgNormalized(dimensions, [['情感穿透']]), note: '文本调动共情和余韵的能力' },
    { label: '类型推进力', value: avgNormalized(dimensions, [['情节反转'], ['谜团操控']]), note: '悬念、转折与阅读牵引' },
    { label: '思想密度', value: avgNormalized(dimensions, [['主题深度'], ['文化底蕴'], ['引用张力']]), note: '主题、文化和互文层面的厚度' },
    { label: '实验锋芒', value: avgNormalized(dimensions, [['先锋性'], ['新锐']]), note: '形式突破和新鲜感' },
  ];
}

function listItems(value, limit = 8) {
  return Array.isArray(value) ? value.map(item => String(item || '').trim()).filter(Boolean).slice(0, limit) : [];
}

function hasArticleStyleProfile(profile) {
  return profile && typeof profile === 'object' && (
    profile.styleLabel || profile.summary || profile.storyContent || profile.coreExpression ||
    profile.genreType || listItems(profile.keywords).length
  );
}

function renderListChips(items, emptyText) {
  const list = listItems(items, 12);
  if (!list.length) return `<span class="muted">${esc(emptyText || '暂无')}</span>`;
  return `<div class="tag-row">${list.map(item => `<span class="tag">${esc(item)}</span>`).join('')}</div>`;
}

function renderProfileDetails(items, emptyText) {
  const list = listItems(items, 8);
  if (!list.length) return `<span class="muted">${esc(emptyText || '暂无')}</span>`;
  return `<ul class="style-detail-list">${list.map(item => `<li>${esc(item)}</li>`).join('')}</ul>`;
}

function renderArticleStyleProfile(profile, fallbackTags) {
  const keywords = listItems(profile.keywords, 12);
  const tags = keywords.length ? keywords : listItems(fallbackTags, 12);
  return `<div class="portrait-summary portrait-profile">
    <div class="style-profile-head">
      <span class="style-label">${esc(profile.styleLabel || '未命名文风')}</span>
      <span class="style-genre">当前作品画像</span>
    </div>
    ${profile.summary ? `<p>${esc(profile.summary)}</p>` : ''}
    <div class="style-profile-grid">
      <div><b>体裁/类型</b><span>${esc(profile.genreType || '-')}</span></div>
      <div><b>故事内容</b><span>${esc(profile.storyContent || '-')}</span></div>
      <div><b>核心表达</b><span>${esc(profile.coreExpression || '-')}</span></div>
      <div><b>表达节奏</b><span>${esc(profile.expressionRhythm || '-')}</span></div>
      <div><b>语言习惯</b>${renderProfileDetails(profile.languageHabits, '暂无语言习惯')}</div>
      <div><b>句式结构</b>${renderProfileDetails(profile.sentenceStructures, '暂无句式结构')}</div>
      <div><b>意象偏好</b>${renderProfileDetails(profile.imageryPreferences, '暂无意象偏好')}</div>
    </div>
    ${tags.length ? `<div class="style-keywords">${renderListChips(tags, '暂无关键词')}</div>` : ''}
  </div>`;
}

function collectAuthorStyleProfiles(extraProfile) {
  const history = LS.get('history', []);
  const profiles = [];
  const seen = new Set();
  const profileKey = (profile) => [
    profile.styleLabel,
    profile.genreType,
    profile.summary,
    profile.storyContent,
    profile.coreExpression,
    listItems(profile.keywords, 6).join('|')
  ].join('::');
  const addProfile = (profile) => {
    if (!hasArticleStyleProfile(profile)) return;
    const finalKey = profileKey(profile);
    if (seen.has(finalKey)) return;
    seen.add(finalKey);
    profiles.push(profile);
  };
  if (Array.isArray(history)) {
    history.forEach(entry => addProfile(entry?.analysis?.articleStyleProfile));
  }
  addProfile(extraProfile);
  return profiles;
}

function countTerms(items, maxItems = 10) {
  const counts = new Map();
  const firstSeen = new Map();
  let order = 0;
  (items || []).forEach(item => {
    const text = String(item || '').trim();
    if (!text || text === '-') return;
    if (!firstSeen.has(text)) firstSeen.set(text, order++);
    counts.set(text, (counts.get(text) || 0) + 1);
  });
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || firstSeen.get(a[0]) - firstSeen.get(b[0]))
    .slice(0, maxItems)
    .map(([text, count]) => ({ text, count }));
}

function collectProfileField(profiles, field) {
  return profiles.flatMap(profile => listItems(profile?.[field], 12));
}

function collectTextField(profiles, field) {
  return profiles.map(profile => String(profile?.[field] || '').trim()).filter(Boolean);
}

function buildAuthorStyleProfile(currentProfile) {
  const profiles = collectAuthorStyleProfiles(currentProfile);
  if (!profiles.length) return null;
  const styleLabels = countTerms(collectTextField(profiles, 'styleLabel'), 6);
  const genres = countTerms(collectTextField(profiles, 'genreType'), 6);
  const languageHabits = countTerms(collectProfileField(profiles, 'languageHabits'), 10);
  const sentenceStructures = countTerms(collectProfileField(profiles, 'sentenceStructures'), 10);
  const imageryPreferences = countTerms(collectProfileField(profiles, 'imageryPreferences'), 10);
  const keywords = countTerms(collectProfileField(profiles, 'keywords'), 12);
  const rhythms = countTerms(collectTextField(profiles, 'expressionRhythm'), 5);
  const coreExpressions = countTerms(collectTextField(profiles, 'coreExpression'), 5);
  const dominantStyle = styleLabels[0]?.text || '尚未形成稳定标签';
  const dominantGenre = genres[0]?.text || '题材样本不足';
  return {
    sampleCount: profiles.length,
    source: 'local',
    dominantStyle,
    dominantGenre,
    styleLabels,
    genres,
    languageHabits,
    sentenceStructures,
    imageryPreferences,
    keywords,
    rhythms,
    coreExpressions,
  };
}

function authorStyleSignatureFromProfiles(profiles) {
  return (profiles || []).map(profile => [
    profile.styleLabel,
    profile.genreType,
    profile.summary,
    profile.storyContent,
    profile.coreExpression,
    listItems(profile.keywords, 8).join('|')
  ].join('::')).join('##');
}

function renderTermChips(terms, emptyText) {
  const list = Array.isArray(terms) ? terms.filter(item => item && item.text) : [];
  if (!list.length) return `<span class="muted">${esc(emptyText || '暂无')}</span>`;
  return `<div class="tag-row">${list.map(item => {
    const label = item.count > 1 ? `${item.text} ×${item.count}` : item.text;
    return `<span class="tag">${esc(label)}</span>`;
  }).join('')}</div>`;
}

function renderAuthorStyleProfile(currentProfile) {
  const profile = buildAuthorStyleProfile(currentProfile);
  if (!profile) return '';
  return renderAuthorStyleProfileData(profile);
}

function renderAuthorStyleProfileData(profile, statusText = '') {
  if (!profile) return '';
  const sourceLabel = statusText || (profile.source === 'model' ? '模型统合' : '本地统计兜底');
  return `<div class="author-profile">
    <div class="author-profile-head">
      <div>
        <div class="section-title">作者文风画像</div>
        <p>基于 ${profile.sampleCount} 篇历史作品画像统合，提炼稳定出现的语言、句式、意象和命题倾向。</p>
      </div>
      <span class="style-genre">${esc(sourceLabel)} · ${esc(profile.sampleCount)} 篇样本</span>
    </div>
    <div class="style-profile-head">
      <span class="style-label">${esc(profile.dominantStyle)}</span>
      <span class="style-genre">${esc(profile.dominantGenre)}</span>
    </div>
    ${profile.summary ? `<p>${esc(profile.summary)}</p>` : ''}
    <div class="author-profile-grid">
      <div><b>稳定风格标签</b>${renderTermChips(profile.styleLabels, '暂无稳定标签')}</div>
      <div><b>常写题材类型</b>${renderTermChips(profile.genres, '暂无题材样本')}</div>
      <div><b>语言习惯</b>${renderTermChips(profile.languageHabits, '暂无语言习惯')}</div>
      <div><b>句式结构</b>${renderTermChips(profile.sentenceStructures, '暂无句式结构')}</div>
      <div><b>意象偏好</b>${renderTermChips(profile.imageryPreferences, '暂无意象偏好')}</div>
      <div><b>关键词网络</b>${renderTermChips(profile.keywords, '暂无关键词')}</div>
      <div><b>主题偏好</b>${renderListChips(profile.topicPreferences, '暂无主题偏好')}</div>
      <div><b>叙事倾向</b>${renderListChips(profile.narrativeTendencies, '暂无叙事倾向')}</div>
      <div><b>稳定优势</b>${renderListChips(profile.strengths, '暂无稳定优势')}</div>
      <div><b>惯性风险</b>${renderListChips(profile.risks, '暂无惯性风险')}</div>
      <div><b>升级方向</b>${renderListChips(profile.evolutionAdvice, '暂无升级方向')}</div>
      <div><b>置信度</b><span>${typeof profile.confidence === 'number' ? `${Math.round(profile.confidence * 100)}%` : '-'}</span></div>
    </div>
    ${profile.rhythms.length ? `<div class="author-profile-note"><b>常见表达节奏</b><span>${esc(profile.rhythms.map(item => item.text).join(' / '))}</span></div>` : ''}
    ${profile.coreExpressions.length ? `<div class="author-profile-note"><b>核心表达倾向</b><span>${esc(profile.coreExpressions.map(item => item.text).join(' / '))}</span></div>` : ''}
  </div>`;
}

function renderAuthorStyleProfileShell(profileHtml) {
  return `<div id="authorStyleProfileBox">${profileHtml}</div>`;
}

async function synthesizeAuthorStyleProfile(currentProfile, requestOptions = {}) {
  const profiles = collectAuthorStyleProfiles(currentProfile);
  if (!profiles.length) return null;
  const signature = authorStyleSignatureFromProfiles(profiles);
  const cached = LS.get('modelAuthorStyleProfile', null);
  if (cached && cached.signature === signature && cached.profile) return cached.profile;
  const profile = await callPlugin('synthesize_author_style_profile', {
    profiles,
    model: requestOptions.model || '',
    api_key: requestOptions.api_key || '',
    base_url: requestOptions.base_url || '',
    model_list_path: requestOptions.model_list_path || '/v1/models',
    use_neko_model: Boolean(requestOptions.use_neko_model),
  }, requestOptions.timeoutMs || 120000);
  const normalized = { ...profile, source: profile?.source || 'model' };
  LS.set('modelAuthorStyleProfile', { signature, profile: normalized });
  return normalized;
}

function renderStylePortrait(analysis) {
  const dimensions = Array.isArray(analysis?.dimensions) ? analysis.dimensions : [];
  const profile = buildStyleProfile(dimensions);
  const sorted = [...profile].sort((a, b) => b.value - a.value);
  const strongest = sorted[0];
  const weakest = sorted[sorted.length - 1];
  const tags = Array.isArray(analysis?.tags) ? analysis.tags : [];
  const hasDimensionData = dimensions.some(d => typeof d?.score === 'number');
  const articleProfile = hasArticleStyleProfile(analysis?.articleStyleProfile) ? analysis.articleStyleProfile : null;
  return `<div class="visual-grid">
    <div>
      <div class="section-title">雷达图可视化报告</div>
      ${renderRadarChart(dimensions)}
    </div>
    <div>
      <div class="section-title">当前作品文风画像</div>
      ${articleProfile ? renderArticleStyleProfile(articleProfile, tags) : `<div class="portrait-summary">
        <p>${hasDimensionData && strongest ? `当前最突出的能力是「${esc(strongest.label)}」，相对薄弱处是「${esc(weakest.label)}」。` : '维度数据不足，暂无法生成稳定画像。'}</p>
        ${tags.length ? `<div class="tag-row">${tags.slice(0, 12).map(tag => `<span class="tag">${esc(tag)}</span>`).join('')}</div>` : ''}
      </div>`}
      <div class="profile-bars">
        ${profile.map(item => {
          const pct = Math.round(item.value * 100);
          return `<div class="profile-row">
            <div class="profile-head"><span>${esc(item.label)}</span><span>${pct}%</span></div>
            <div class="profile-track"><div class="profile-fill" style="width:${pct}%;"></div></div>
            <div class="profile-note">${esc(item.note)}</div>
          </div>`;
        }).join('')}
      </div>
    </div>
  </div>`;
}

function parseMermaidDiagram(code) {
  const raw = String(code || '').replace(/\\"/g, '"').replace(/\n/g, ';');
  const statements = raw.split(';').map(s => s.trim()).filter(Boolean);
  const first = statements[0] || 'flowchart TD';
  const direction = /\bLR\b/.test(first) ? 'LR' : 'TD';
  const nodes = new Map();
  const edges = [];
  const ensure = (id, label) => {
    if (!id) return;
    if (!nodes.has(id)) nodes.set(id, label || id);
    else if (label && nodes.get(id) === id) nodes.set(id, label);
  };
  const nodeRe = /([A-Za-z][A-Za-z0-9]*)\s*\[\s*"([^"]+)"\s*\]/g;
  for (const statement of statements.slice(1)) {
    if (/^(subgraph|end)\b/.test(statement)) continue;
    let match;
    while ((match = nodeRe.exec(statement))) ensure(match[1], match[2]);
    const edgeMatch = statement.match(/([A-Za-z][A-Za-z0-9]*)(?:\s*\[[^\]]+\])?\s*[-=.]+>\s*(?:\|"([^"]+)"\|\s*)?([A-Za-z][A-Za-z0-9]*)/);
    if (edgeMatch) {
      ensure(edgeMatch[1]);
      ensure(edgeMatch[3]);
      edges.push({ from: edgeMatch[1], to: edgeMatch[3], label: edgeMatch[2] || '' });
    }
  }
  return { direction, nodes: [...nodes.entries()].map(([id, label]) => ({ id, label })), edges };
}

function layoutGraph(parsed) {
  const ids = parsed.nodes.map(n => n.id);
  const indegree = Object.fromEntries(ids.map(id => [id, 0]));
  const level = Object.fromEntries(ids.map(id => [id, 0]));
  const outgoing = Object.fromEntries(ids.map(id => [id, []]));
  parsed.edges.forEach(edge => {
    if (edge.from in outgoing) outgoing[edge.from].push(edge.to);
    if (edge.to in indegree) indegree[edge.to] += 1;
  });
  const queue = ids.filter(id => indegree[id] === 0);
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i];
    (outgoing[id] || []).forEach(next => {
      level[next] = Math.max(level[next], level[id] + 1);
      indegree[next] -= 1;
      if (indegree[next] === 0) queue.push(next);
    });
  }
  const groups = {};
  ids.forEach(id => {
    const key = level[id] || 0;
    groups[key] = groups[key] || [];
    groups[key].push(id);
  });
  const maxLevel = Math.max(0, ...Object.keys(groups).map(Number));
  const width = parsed.direction === 'LR' ? Math.max(640, 190 * (maxLevel + 1)) : 760;
  const height = parsed.direction === 'LR'
    ? Math.max(240, 92 * Math.max(...Object.values(groups).map(g => g.length)))
    : Math.max(280, 105 * (maxLevel + 1));
  const positions = {};
  Object.entries(groups).forEach(([levelKey, group]) => {
    const levelNo = Number(levelKey);
    group.forEach((id, idx) => {
      if (parsed.direction === 'LR') {
        positions[id] = { x: 90 + levelNo * 185, y: height / (group.length + 1) * (idx + 1) };
      } else {
        positions[id] = { x: width / (group.length + 1) * (idx + 1), y: 58 + levelNo * 98 };
      }
    });
  });
  return { width, height, positions };
}

function renderGraphSvg(diagram) {
  const parsed = parseMermaidDiagram(diagram.code);
  if (parsed.nodes.length < 2) {
    return `<pre class="diagram-code">${esc(diagram.code || '')}</pre>`;
  }
  const layout = layoutGraph(parsed);
  const byId = Object.fromEntries(parsed.nodes.map(n => [n.id, n]));
  const edges = parsed.edges.map(edge => {
    const a = layout.positions[edge.from];
    const b = layout.positions[edge.to];
    if (!a || !b) return '';
    const labelX = (a.x + b.x) / 2;
    const labelY = (a.y + b.y) / 2 - 8;
    return `<g>
      <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" class="graph-edge" marker-end="url(#arrow)" />
      ${edge.label ? `<text x="${labelX}" y="${labelY}" text-anchor="middle" class="graph-edge-label">${esc(edge.label)}</text>` : ''}
    </g>`;
  }).join('');
  const nodes = parsed.nodes.map(node => {
    const p = layout.positions[node.id];
    if (!p) return '';
    const lines = wrapSvgText(node.label, 9);
    const h = Math.max(42, 24 + lines.length * 14);
    return `<g>
      <rect x="${p.x - 58}" y="${p.y - h / 2}" width="116" height="${h}" rx="8" class="graph-node" />
      <text x="${p.x}" y="${p.y - (lines.length - 1) * 7}" text-anchor="middle" class="graph-node-label">
        ${lines.map((line, idx) => `<tspan x="${p.x}" dy="${idx === 0 ? 0 : 14}">${esc(line)}</tspan>`).join('')}
      </text>
      <title>${esc(byId[node.id]?.label || node.id)}</title>
    </g>`;
  }).join('');
  return `<svg class="graph-svg" viewBox="0 0 ${layout.width} ${layout.height}" role="img" aria-label="${esc(diagram.title || '分析图表')}">
    <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#64748b"></path></marker></defs>
    ${edges}${nodes}
  </svg>`;
}

function fallbackStructureDiagram(analysis) {
  return {
    title: '结构分析图表',
    code: 'flowchart TD;A["开篇情境"] --> B["核心冲突"];B --> C["关系推进"];C --> D["转折压力"];D --> E["阶段收束"]'
  };
}

function safeDiagramLabel(value, fallback) {
  return String(value || fallback || '未命名')
    .replace(/["\[\]{}|<>#&@]/g, '')
    .slice(0, 10) || fallback || '未命名';
}

function fallbackThemeDiagram(analysis) {
  const tags = Array.isArray(analysis?.tags) && analysis.tags.length ? analysis.tags.slice(0, 4) : ['核心欲望', '关系变化', '价值选择', '读者余韵'];
  const labels = tags.map((tag, index) => safeDiagramLabel(tag, `命题${index + 1}`));
  while (labels.length < 3) labels.push(`命题${labels.length + 1}`);
  return {
    title: '主题逻辑与命题网络图',
    code: `graph LR;A["核心命题"] --> B["${labels[0]}"];A --> C["${labels[1]}"];A --> D["${labels[2]}"];B --> E["情感回响"];C --> E;D --> F["意义收束"]`
  };
}

function renderDiagramSection(analysis, kind) {
  const diagrams = Array.isArray(analysis?.mermaid_diagrams) ? analysis.mermaid_diagrams : [];
  const matcher = kind === 'theme' ? /(主题|命题|思想|逻辑)/ : /(结构|情节|人物|关系|叙事)/;
  let selected = diagrams.find(d => matcher.test(String(d?.title || '') + String(d?.code || '')));
  if (!selected) selected = kind === 'theme' ? fallbackThemeDiagram(analysis) : fallbackStructureDiagram(analysis);
  return `<div class="detail-section">
    <h3>${kind === 'theme' ? '主题逻辑与命题网络图' : '结构分析图表'}</h3>
    <div class="diagram-card">
      <div class="diagram-title">${esc(selected.title || (kind === 'theme' ? '主题逻辑与命题网络图' : '结构分析图表'))}</div>
      ${renderGraphSvg(selected)}
    </div>
  </div>`;
}

function renderVisualReport(analysis) {
  if (!analysis || typeof analysis !== 'object') return '';
  const authorProfile = renderAuthorStyleProfileShell(renderAuthorStyleProfile(analysis.articleStyleProfile));
  return `<div class="detail-section">
    <h3>可视化报告</h3>
    ${renderStylePortrait(analysis)}
    ${authorProfile}
  </div>
  ${renderDiagramSection(analysis, 'structure')}
  ${renderDiagramSection(analysis, 'theme')}`;
}

// ── Notification ───────────────────────────────────────────
function notify(msg, type = 'info') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'toast toast-' + type + ' show';
  setTimeout(() => el.className = 'toast', 2500);
}
