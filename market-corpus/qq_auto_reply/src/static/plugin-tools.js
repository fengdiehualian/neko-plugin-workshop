/* 插件工具桥（「插件」页）—— napcat 面板与开放平台面板**共用这一份**实现与标记。
 *
 * 为什么是共用文件而不是各抄一份：这两个面板本来就是同一套东西的两个视图，
 * 而这一页在 2026-10-02 之后对**所有 QQ 通道**都生效（`plugin_tool_service.
 * channel_allows_plugin_tools`）。抄两份的话，档位按钮、拖拽、自动刷新、通道提示
 * 这些细节迟早只在一份里被修。两个页面各自只留一个空容器：
 *
 *     <div class="page" id="page-plugintools"></div>
 *
 * 本文件的 MARKUP 会注入进去；页面自己的 switchPage 只需要调
 * `loadPluginTools()` + `ptScheduleRefresh()`（离开时 `ptStopRefresh()`）。
 *
 * 依赖页面全局：`t / tf / call / toast / escapeHtml / escapeOnclick / currentPage`。
 * 所以脚本标签放在页面主 inline script **之后**（两页都是这么放的）。
 */
(function () {
  'use strict';

  const MARKUP = `
    <div class="card">
        <div class="card-title" data-i18n="ui.openplat.plugintools.title">插件工具</div>
        <p style="color:var(--text-secondary);font-size:12px;margin-bottom:8px" data-i18n="ui.openplat.plugintools.hint">让她可以调用本体的其它插件。所有非 QQ 插件都会列出来（没启动的也能先配好，只是那一轮挂不上）；点卡片上的「所有人 / 仅管理员」按钮或用拖拽决定谁能用。</p>
        <div class="form-hint" id="plugin-tools-channel" style="margin-bottom:10px"></div>
        <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
            <!-- 左：可添加（长列表 → 自己的滚动区 + 搜索框，不把右侧分区顶出屏幕） -->
            <div style="flex:1 1 320px;min-width:280px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                    <span class="form-label" style="margin:0" data-i18n="ui.openplat.plugintools.available">可添加的插件</span>
                    <span id="pt-count-available" class="tag tag-offline"></span>
                </div>
                <input class="form-input" id="pt-search" style="margin-bottom:8px" data-i18n-placeholder="ui.openplat.plugintools.search_placeholder" placeholder="搜索插件名 / id" oninput="renderPluginTools()">
                <div id="pt-zone-available" class="drop-zone" data-tier=""
                     style="min-height:120px;max-height:46vh;overflow-y:auto;text-align:left;padding:10px;font-size:13px"></div>
            </div>
            <!-- 右：两个档位（sticky，滚动时始终停在视野里；卡片少，拖拽才有意义） -->
            <div style="flex:1 1 320px;min-width:280px;position:sticky;top:12px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                    <span class="form-label" style="margin:0" data-i18n="ui.openplat.plugintools.tier_all">所有人都可以用</span>
                    <span id="pt-count-all" class="tag tag-running"></span>
                </div>
                <div id="pt-zone-all" class="drop-zone" data-tier="all"
                     style="min-height:110px;text-align:left;padding:10px;font-size:13px"></div>
                <div style="display:flex;align-items:center;gap:8px;margin:12px 0 6px">
                    <span class="form-label" style="margin:0" data-i18n="ui.openplat.plugintools.tier_admin">只有管理员可以用</span>
                    <span id="pt-count-admin" class="tag tag-running"></span>
                </div>
                <div id="pt-zone-admin" class="drop-zone" data-tier="admin"
                     style="min-height:110px;text-align:left;padding:10px;font-size:13px"></div>
            </div>
        </div>
        <div class="form-hint" style="margin-top:10px" data-i18n="ui.openplat.plugintools.drag_hint">点按钮或拖拽都能改档位；拖回左边（或点「撤销」）即移除。改动立刻保存。</div>
        <div id="plugin-tools-note" style="font-size:12px;color:var(--muted);margin-top:6px"></div>
        <!-- 异步任务的结果回投：她说"结果出来我告诉你"时，后台替她盯着那个任务，
             到点后由她主动回一条到同一个会话。这是插件里唯一一条"她主动说话"的路，
             所以给一个能一键关掉的开关，并把"还在等什么"显示出来。 -->
        <div class="form-group" style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px">
            <label><input id="pt-followup-enabled" type="checkbox" onchange="ptSetFollowup(this.checked)"> <span data-i18n="ui.openplat.plugintools.followup_enabled">结果出来时允许她主动回一条（异步任务的后续）</span></label>
            <div class="form-hint" data-i18n="ui.openplat.plugintools.followup_hint">她答应"结果出来告诉你"时，后台会替你盯着那个任务；出结果后她把结论主动发回同一个会话。关掉之后她就不会再承诺这件事。</div>
            <div class="form-hint" id="pt-followups" style="margin-top:6px"></div>
        </div>
    </div>`;

  /* 候选与"配了但此刻没启动"分开展示（后端分好字段）：后者挂不上去，界面必须如实
     说出来，否则使用者会以为配好了就能用。拖拽落点就是保存动作 —— 拖完还要再找
     一次"保存"按钮是更差的交互。 */
  let _pluginTools = {tiers: {}, candidates: [], configured_not_running: [], max: 8, enabled_on_this_channel: false, connection_mode: ''};
  let PT_REFRESH_MS = 10000, _ptTimer = null;

  function ptMount() {
    const host = document.getElementById('page-plugintools');
    if (!host || document.getElementById('pt-zone-available')) return;
    host.innerHTML = MARKUP;
    // 注入的节点要自己再翻一次：i18n.js 初始化时扫的是**当时**的 DOM。
    if (window.I18n && typeof window.I18n.scanDOM === 'function') window.I18n.scanDOM(host);
    ['pt-zone-available', 'pt-zone-all', 'pt-zone-admin'].forEach(id => {
      const z = document.getElementById(id);
      if (!z) return;
      ['dragenter', 'dragover'].forEach(ev => z.addEventListener(ev, e => {e.preventDefault(); z.classList.add('drag-over')}));
      ['dragleave', 'drop'].forEach(ev => z.addEventListener(ev, e => {e.preventDefault(); z.classList.remove('drag-over')}));
      z.addEventListener('drop', e => {
        const pid = e.dataTransfer.getData('text/plain');
        if (pid) ptSetTier(pid, z.dataset.tier || null);
      });
    });
  }

  function _ptCard(c, tier) {
    /* 加插件**不看有没有启动**（使用者定的）：全量出卡片、先分档位；在不在跑只影响
       "这一轮挂不挂得上"，所以这里只打个标记，不禁用、也不隐藏。
       **卡片颜色就是状态**（使用者 17:3x 定）：在跑 = 绿色卡片，没在跑 = 灰色卡片 ——
       一眼扫过去就知道哪些挂了工具、哪些只是先配好档位。
       **每个卡片自己带两个档位按钮**：一多起来（十几张卡）纯拖拽就没法用了 ——
       列表底部的卡片根本拖不到右侧分区去。按钮是可靠路径，拖拽只是顺手的加速器。 */
    let stopped = c.running === false;
    let stateTag = stopped
      ? `<span class="tag tag-offline">${t('ui.openplat.plugintools.not_running', '未启动')}</span>`
      : `<span class="tag tag-online">${t('ui.openplat.plugintools.running', '运行中')}</span>`;
    let skin = stopped
      ? 'background:var(--overlay);border:1px solid var(--border)'
      : 'background:var(--success-tint-strong);border:1px solid var(--success-border);border-left:3px solid var(--success)';
    let entries = (c.entries || []).length;
    let count = entries ? ` · ${t('ui.openplat.plugintools.entry_count', '可用功能 ')}${entries}` : '';
    let pid = escapeOnclick(c.plugin_id);
    let btn = (want, label) => `<button class="btn ${tier === want ? 'btn-primary' : 'btn-ghost'} btn-sm" onclick="ptSetTier('${pid}','${want}')">${label}</button>`;
    let actions = tier
      ? `${btn('all', t('ui.openplat.plugintools.tier_all_short', '所有人'))} ${btn('admin', t('ui.openplat.plugintools.tier_admin_short', '仅管理员'))} <button class="btn btn-ghost btn-sm" onclick="ptSetTier('${pid}',null)">${t('ui.openplat.plugintools.btn_remove', '撤销')}</button>`
      : `${btn('all', t('ui.openplat.plugintools.tier_all_short', '所有人'))} ${btn('admin', t('ui.openplat.plugintools.tier_admin_short', '仅管理员'))}`;
    return `<div class="pt-card" draggable="true" data-pid="${escapeHtml(c.plugin_id)}" data-tier="${escapeHtml(tier || '')}" data-running="${stopped ? '0' : '1'}"
      style="${skin};border-radius:var(--radius-md);padding:8px 10px;margin-bottom:6px;cursor:grab">
      <div style="font-weight:600;font-size:13px">${escapeHtml(c.name || c.plugin_id)} ${stateTag}</div>
      <div style="font-size:11px;color:var(--text-secondary);word-break:break-all">${escapeHtml(c.plugin_id)}${count}</div>
      <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">${actions}</div>
    </div>`;
  }

  function renderPluginTools() {
    let tiers = _pluginTools.tiers || {}, cands = _pluginTools.candidates || [];
    let byId = {}; cands.forEach(c => {byId[c.plugin_id] = c});
    /* 配了但目录里现在没有（插件被卸了 / id 对不上）：也要给一张卡，否则没法撤销它。 */
    let missing = (_pluginTools.configured_not_running || [])
      .filter(pid => !byId[pid])
      .map(pid => ({plugin_id: pid, name: pid, entries: [], running: false}));
    let all = missing.concat(cands);
    let q = (document.getElementById('pt-search') || {}).value || ''; q = q.trim().toLowerCase();
    let match = c => !q || String(c.name || '').toLowerCase().includes(q) || String(c.plugin_id || '').toLowerCase().includes(q);
    let avail = all.filter(c => !tiers[c.plugin_id] && match(c));
    let zAll = all.filter(c => tiers[c.plugin_id] === 'all');
    let zAdmin = all.filter(c => tiers[c.plugin_id] === 'admin');
    let zone = (id, rows, hint) => {
      let el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = rows.length ? rows.map(c => _ptCard(c, tiers[c.plugin_id] || '')).join('')
        : `<span style="color:var(--text-secondary);font-size:12px">${hint}</span>`;
    };
    zone('pt-zone-available', avail, t('ui.openplat.plugintools.empty_available', '没有可添加的插件（非 QQ 插件都会列在这里）'));
    zone('pt-zone-all', zAll, t('ui.openplat.plugintools.drop_hint', '把卡片拖到这里，或点左边卡片上的「所有人」'));
    zone('pt-zone-admin', zAdmin, t('ui.openplat.plugintools.drop_hint', '把卡片拖到这里，或点左边卡片上的「仅管理员」'));
    let counter = (id, n) => {let el = document.getElementById(id); if (el) el.textContent = String(n)};
    counter('pt-count-available', all.filter(c => !tiers[c.plugin_id]).length);
    counter('pt-count-all', zAll.length);
    counter('pt-count-admin', zAdmin.length);
    document.querySelectorAll('#page-plugintools .pt-card').forEach(card => {
      card.addEventListener('dragstart', ev => {ev.dataTransfer.setData('text/plain', card.dataset.pid); ev.dataTransfer.effectAllowed = 'move'});
    });
    // 通道提示：闸对所有 QQ 通道都开（开放平台 + OneBot），所以这句按**当前通道**说，
    // 不再写死"只在开放平台生效"。
    let ch = document.getElementById('plugin-tools-channel');
    if (ch) {
      let modeTxt = _pluginTools.connection_mode || '?';
      let cap = _pluginTools.max_plugins || _pluginTools.max || 8;
      ch.textContent = _pluginTools.enabled_on_this_channel
        ? tf('ui.openplat.plugintools.channel_ok', '当前通道是 {mode}，配置生效中（最多 {n} 个）', {mode: modeTxt, n: cap})
        : tf('ui.openplat.plugintools.channel_unsupported', '当前通道是 {mode}，不支持插件工具', {mode: modeTxt});
      ch.style.color = _pluginTools.enabled_on_this_channel ? 'var(--success)' : 'var(--warning)';
    }
    let note = document.getElementById('plugin-tools-note');
    if (note) {
      let miss = _pluginTools.configured_not_running || [];
      note.textContent = miss.length
        ? tf('ui.openplat.plugintools.missing_note', '这些已配置但当前没启动，挂不上去：{ids}', {ids: miss.join('、')})
        : '';
    }
    let fuBox = document.getElementById('pt-followup-enabled');
    if (fuBox) fuBox.checked = _pluginTools.followups_enabled !== false;
    let fu = document.getElementById('pt-followups');
    if (fu) {
      let rows = _pluginTools.pending_followups || [];
      fu.textContent = rows.length
        ? tf('ui.openplat.plugintools.followup_pending', '正在等结果：{items}', {
            items: rows.map(fr => fr.conversation + ' · ' + fr.plugin_id + ' · '
              + tf('ui.openplat.plugintools.followup_waited', '已等 {s}s', {s: Math.round(fr.age_seconds)})
              + (fr.status && fr.status !== 'queued' && fr.status !== 'running' ? '（' + fr.status + '）' : '')).join('；')
          })
        : '';
      fu.style.color = rows.length ? 'var(--accent)' : 'var(--muted)';
    }
  }

  async function loadPluginTools() {
    let box = document.getElementById('pt-zone-available');
    if (!box) return;
    try {
      let d = await call('query', {action: 'plugin_tools'});
      _pluginTools = Object.assign(_pluginTools, d || {});
      renderPluginTools();
    } catch (e) {
      toast(t('ui.openplat.plugintools.load_failed', '加载插件列表失败: ') + e.message);
    }
  }

  /* 这一页要**自己会更新**：启停状态是在别处（插件管理）改的，而卡片以前只在进页时读一次 ——
     于是就出现"插件已经启动了，卡片还写着未启动"。每 PT_REFRESH_MS 重读一次（界面那条读
     路径是真的去问宿主，见 plugin_tool_service.refresh_for_ui），离开这页就停掉。 */
  function ptScheduleRefresh() {
    ptStopRefresh();
    _ptTimer = setInterval(() => {
      if (currentPage === 'plugintools' && !document.hidden) loadPluginTools();
      else ptStopRefresh();
    }, PT_REFRESH_MS);
  }

  function ptStopRefresh() {
    if (_ptTimer) {clearInterval(_ptTimer); _ptTimer = null}
  }

  async function ptSetFollowup(on) {
    try {
      await call('config', {action: 'save', qq_open_plugin_followup_enabled: !!on});
      _pluginTools.followups_enabled = !!on;
      toast(t('ui.openplat.plugintools.saved', '分级已保存'), true);
    } catch (e) {
      toast(t('ui.openplat.plugintools.save_failed', '保存失败: ') + e.message);
    }
  }

  async function ptSetTier(pid, tier) {
    let t2 = Object.assign({}, _pluginTools.tiers || {});
    if (tier) t2[pid] = tier; else delete t2[pid];
    try {
      await call('config', {action: 'save', qq_open_plugin_tools: t2});
      _pluginTools.tiers = t2;
      renderPluginTools();
      toast(t('ui.openplat.plugintools.saved', '分级已保存'), true);
    } catch (e) {
      toast(t('ui.openplat.plugintools.save_failed', '保存失败: ') + e.message);
    }
  }

  // 页面与内联 handler 只认这几个名字（拖拽区与卡片按钮都是内联 onclick/oninput，
  // 挂在 window 上才叫得到）。其余（候选表、定时器、卡片渲染）留在本作用域里。
  window.loadPluginTools = loadPluginTools;
  window.renderPluginTools = renderPluginTools;
  window.ptSetTier = ptSetTier;
  window.ptSetFollowup = ptSetFollowup;
  window.ptScheduleRefresh = ptScheduleRefresh;
  window.ptStopRefresh = ptStopRefresh;
  window.ptMountPluginTools = ptMount;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ptMount);
  else ptMount();
})();
