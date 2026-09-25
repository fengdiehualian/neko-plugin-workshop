/**
 * Web Searching · 搜索记录 UI
 * 前端逻辑：搜索、历史记录展示、统计、樱花动画
 * 支持：即时答案、百科摘要、网页搜索结果
 */

const pluginId = 'web_searching';
const RUNS_URL = '/runs';

// ---------------------------------------------------------------------------
// 樱花飘落动画
// ---------------------------------------------------------------------------
function initPetals() {
    const container = document.getElementById('petals');
    if (!container) return;
    const count = 18;
    for (let i = 0; i < count; i++) {
        const petal = document.createElement('div');
        petal.className = 'petal';
        petal.style.left = Math.random() * 100 + '%';
        petal.style.animationDuration = (8 + Math.random() * 12) + 's';
        petal.style.animationDelay = (Math.random() * 10) + 's';
        const size = 8 + Math.random() * 12;
        petal.style.width = size + 'px';
        petal.style.height = size + 'px';
        petal.style.opacity = (0.3 + Math.random() * 0.4).toString();
        container.appendChild(petal);
    }
}

// ---------------------------------------------------------------------------
// 调用插件 entry
// ---------------------------------------------------------------------------
async function callPlugin(entry, args = {}) {
    const resp = await fetch(RUNS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId, entry_id: entry, args }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const { run_id, id } = await resp.json();
    const runId = run_id || id;
    if (!runId) throw new Error('未获取到 run_id');

    const deadline = Date.now() + 30000;
    let delay = 300;
    while (Date.now() < deadline) {
        await new Promise(r => setTimeout(r, delay));
        delay = Math.min(delay * 1.3, 1000);
        const poll = await fetch(`${RUNS_URL}/${runId}`);
        if (!poll.ok) continue;
        const rec = await poll.json();
        if (rec.status === 'succeeded') {
            const exp = await fetch(`${RUNS_URL}/${runId}/export`);
            if (!exp.ok) return {};
            const { items = [] } = await exp.json();
            const item = items.find(i => i.type === 'json' && i.json) || items[0];
            if (!item) return {};
            let raw = item.json || {};
            while (raw && raw.data && typeof raw.data === 'object'
                   && ('success' in raw.data || 'history' in raw.data || 'total' in raw.data)) {
                raw = raw.data;
            }
            return raw;
        }
        if (['failed', 'canceled', 'timeout'].includes(rec.status)) {
            throw new Error(rec.error?.message || rec.message || rec.status);
        }
    }
    throw new Error('调用超时');
}

// ---------------------------------------------------------------------------
// Toast / Loading
// ---------------------------------------------------------------------------
function showToast(msg, type = 'info') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => {
        t.style.opacity = '0';
        t.style.transform = 'translateY(-20px)';
        t.style.transition = 'all 0.3s';
        setTimeout(() => t.remove(), 300);
    }, 2500);
}

function showLoading(text = '正在搜索中...') {
    const o = document.getElementById('loadingOverlay');
    o.querySelector('.loading-text').textContent = text;
    o.classList.add('show');
}
function hideLoading() {
    document.getElementById('loadingOverlay').classList.remove('show');
}

// ---------------------------------------------------------------------------
// 格式化
// ---------------------------------------------------------------------------
function timeAgo(str, ts) {
    const now = Date.now();
    const t = ts ? ts * 1000 : new Date(str).getTime();
    const d = now - t;
    if (d < 60000) return '刚刚';
    if (d < 3600000) return Math.floor(d / 60000) + '分钟前';
    if (d < 86400000) return Math.floor(d / 3600000) + '小时前';
    return str || '';
}

// ---------------------------------------------------------------------------
// 渲染
// ---------------------------------------------------------------------------
function renderStats(data) {
    document.getElementById('totalSearches').textContent = data.total || 0;
    const strategies = data.strategies || {};
    const keys = Object.keys(strategies);
    document.getElementById('currentEngine').textContent =
        keys.length > 0 ? keys.join('/') : '--';
    let totalResults = 0;
    if (data.history) {
        data.history.forEach(h => { totalResults += h.result_count || 0; });
    }
    document.getElementById('totalResults').textContent = totalResults;
}

function escapeHtml(s) {
    if (!s) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}

function renderHistory(history) {
    const list = document.getElementById('historyList');
    const empty = document.getElementById('emptyState');
    if (!history || history.length === 0) {
        list.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';
    list.innerHTML = '';

    history.forEach((item, idx) => {
        const card = document.createElement('div');
        card.className = 'history-card';
        card.style.animationDelay = (idx * 0.05) + 's';

        // 构建预览文本
        let preview = '';
        if (item.instant_answer) {
            preview = item.instant_answer.substring(0, 120);
        } else if (item.wiki_summary) {
            preview = item.wiki_summary.substring(0, 120);
        } else if (item.results && item.results.length > 0) {
            preview = item.results[0].snippet || item.results[0].title || '';
        } else {
            preview = '未找到结果';
        }

        card.innerHTML = `
            <div class="history-card-header">
                <div class="history-query">${escapeHtml(item.query)}</div>
                <span class="history-badge">${escapeHtml(item.strategy || 'search')}</span>
            </div>
            <div class="history-meta">
                <span class="history-time">🕐 ${timeAgo(item.searched_at_str, item.searched_at)}</span>
                <span class="history-count">📄 ${item.result_count || 0} 条</span>
            </div>
            <div class="history-preview">${escapeHtml(preview)}</div>
        `;
        card.addEventListener('click', () => showHistoryDetail(item));
        list.appendChild(card);
    });
}

function showHistoryDetail(item) {
    const section = document.getElementById('resultsSection');
    const list = document.getElementById('resultsList');
    section.style.display = 'block';
    list.innerHTML = '';

    // 即时答案
    if (item.instant_answer) {
        const el = document.createElement('div');
        el.className = 'result-item result-ia';
        el.innerHTML = `
            <h3 class="result-title">💡 即时答案</h3>
            <p class="result-snippet" style="white-space: pre-wrap;">${escapeHtml(item.instant_answer)}</p>
        `;
        list.appendChild(el);
    }

    // 百科摘要
    if (item.wiki_summary) {
        const el = document.createElement('div');
        el.className = 'result-item result-wiki';
        el.innerHTML = `
            <h3 class="result-title">📚 百科知识</h3>
            <p class="result-snippet" style="white-space: pre-wrap;">${escapeHtml(item.wiki_summary.substring(0, 500))}...</p>
        `;
        list.appendChild(el);
    }

    // Web 搜索结果
    if (item.results && item.results.length > 0) {
        item.results.forEach((r, i) => {
            const el = document.createElement('div');
            el.className = 'result-item';
            el.style.animationDelay = (i * 0.05) + 's';
            el.innerHTML = `
                <h3 class="result-title">${escapeHtml(r.title || '')}</h3>
                ${r.snippet ? `<p class="result-snippet">${escapeHtml(r.snippet)}</p>` : ''}
                ${r.url ? `<a class="result-url" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.url)}</a>` : ''}
            `;
            list.appendChild(el);
        });
    }

    if (!item.instant_answer && !item.wiki_summary && (!item.results || item.results.length === 0)) {
        list.innerHTML = '<p style="text-align:center;color:var(--text-sub);padding:20px;">没有找到相关结果 🌧️</p>';
    }

    section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function showSearchResults(data) {
    const section = document.getElementById('resultsSection');
    const list = document.getElementById('resultsList');
    section.style.display = 'block';
    list.innerHTML = '';

    if (data.instant_answer) {
        const el = document.createElement('div');
        el.className = 'result-item result-ia';
        el.innerHTML = `
            <h3 class="result-title">💡 即时答案</h3>
            <p class="result-snippet" style="white-space: pre-wrap;">${escapeHtml(data.instant_answer)}</p>
        `;
        list.appendChild(el);
    }

    if (data.wiki_summary) {
        const el = document.createElement('div');
        el.className = 'result-item result-wiki';
        el.innerHTML = `
            <h3 class="result-title">📚 百科知识</h3>
            <p class="result-snippet" style="white-space: pre-wrap;">${escapeHtml(data.wiki_summary.substring(0, 500))}</p>
        `;
        list.appendChild(el);
    }

    if (data.results && data.results.length > 0) {
        data.results.forEach((r, i) => {
            const el = document.createElement('div');
            el.className = 'result-item';
            el.style.animationDelay = (i * 0.05) + 's';
            el.innerHTML = `
                <h3 class="result-title">${escapeHtml(r.title || '')}</h3>
                ${r.snippet ? `<p class="result-snippet">${escapeHtml(r.snippet)}</p>` : ''}
                ${r.url ? `<a class="result-url" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.url)}</a>` : ''}
            `;
            list.appendChild(el);
        });
    }

    if (!data.instant_answer && !data.wiki_summary && (!data.results || data.results.length === 0)) {
        list.innerHTML = '<p style="text-align:center;color:var(--text-sub);padding:20px;">没有找到相关结果 🌧️</p>';
    }

    section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------
async function loadHistory() {
    try {
        const data = await callPlugin('get_history', { limit: 50, offset: 0 });
        if (data && data.history) {
            renderStats({
                total: data.total || data.history.length,
                strategies: data.stats?.strategies || {},
                history: data.history,
            });
            renderHistory(data.history);
        } else {
            renderStats({ total: 0 });
            renderHistory([]);
        }
    } catch (e) {
        console.error('加载历史失败:', e);
        renderStats({ total: 0 });
        renderHistory([]);
        showToast('加载历史记录失败', 'error');
    }
}

async function doSearch(query) {
    if (!query || !query.trim()) {
        showToast('请输入搜索关键词', 'error');
        return;
    }
    const btn = document.getElementById('searchBtn');
    const input = document.getElementById('searchInput');
    btn.disabled = true;
    input.disabled = true;
    showLoading('正在搜索中...');

    try {
        const result = await callPlugin('search', { query: query.trim(), max_results: 8 });
        if (result) {
            showSearchResults(result);
            const parts = [];
            if (result.instant_answer) parts.push('即时答案');
            if (result.wiki_summary) parts.push('百科');
            parts.push(`${result.count || (result.results||[]).length} 条网页结果`);
            showToast(`搜索完成：${parts.join(' + ')} ✨`, 'success');
            await loadHistory();
        }
    } catch (e) {
        console.error('搜索失败:', e);
        showToast('搜索失败: ' + (e.message || ''), 'error');
    } finally {
        hideLoading();
        btn.disabled = false;
        input.disabled = false;
    }
}

async function clearHistory() {
    if (!confirm('确定要清空所有搜索记录吗？')) return;
    try {
        await callPlugin('clear_history', {});
        showToast('搜索记录已清空 🌸', 'success');
        await loadHistory();
    } catch (e) {
        showToast('清空失败: ' + (e.message || ''), 'error');
    }
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    initPetals();

    document.getElementById('searchBtn').addEventListener('click', () => {
        doSearch(document.getElementById('searchInput').value);
    });
    document.getElementById('searchInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doSearch(e.target.value);
    });
    document.getElementById('refreshBtn').addEventListener('click', function() {
        this.style.transform = 'rotate(360deg)';
        this.style.transition = 'transform 0.6s';
        setTimeout(() => { this.style.transform = ''; this.style.transition = ''; }, 600);
        loadHistory();
    });
    document.getElementById('clearBtn').addEventListener('click', clearHistory);
    document.getElementById('closeResultsBtn').addEventListener('click', () => {
        document.getElementById('resultsSection').style.display = 'none';
    });

    loadHistory();
});
