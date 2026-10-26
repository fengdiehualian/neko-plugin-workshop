(function () {
  'use strict';
  const base = '/plugin/neko_ads/hosted-ui', storage = 'neko-ads-custom-exit-shortcut';
  const $ = id => document.getElementById(id);
  let state = null, available = false, selected = localStorage.getItem(storage) || 'Ctrl+Shift+1';
  let pending = null, stopping = false, switching = null, runtime = 'unknown', recording = false, revision = 0, message = '';
  let runtimeConfirmation = null;
  let changingVoice = false;
  function metadata(plugin) {
    const label = $('plugin-version');
    if (!label) return;
    const version = plugin && typeof plugin.version === 'string' ? plugin.version.trim() : '';
    label.textContent = version ? (/^v/i.test(version) ? version : 'v' + version) : '版本暂不可用';
  }
  function notice() { $('shortcut').value = selected; $('notice').textContent = '输入安全词「广告下班」或按 ' + selected + ' 可以立即结束。'; }
  function render() {
    const active = !!(state && state.active), ready = !!(available && state && state.ready);
    if (runtimeConfirmation === 'start' && ready) {
      runtimeConfirmation = null; message = active ? '' : '插件已启动，正在待机。想玩时再点击下方开局按钮。';
    } else if (runtimeConfirmation === 'stop' && available && runtime === 'stopped') {
      runtimeConfirmation = null; message = '插件已关闭，广告与声音已停止。';
    }
    const running = ready || runtime === 'running';
    const phase = switching ? switching === 'stop' ? 'disabling' : 'enabling' : stopping ? 'stopping' : pending ? 'starting' : !available ? (active ? 'reconnecting' : 'unavailable') : !ready ? 'disabled' : active ? 'playing' : state.desktop_ready ? 'standby' : 'waiting';
    document.body.dataset.phase = phase;
    const phaseLabel = $('phase-label');
    if (phaseLabel) phaseLabel.textContent = ({enabling:'正在启动', disabling:'正在关闭', starting:'正在开局', stopping:'正在结束', reconnecting:'正在重连', unavailable:'等待连接', disabled:'未启用', playing:'游戏进行中', standby:'已启用 · 待命', waiting:'等待主界面'})[phase];
    $('enable').disabled = !!switching || !['running', 'stopped'].includes(runtime);
    $('enable').textContent = switching ? switching === 'stop' ? '正在关闭插件……' : '正在启动插件……' : running ? '一键关闭插件' : '一键启动插件';
    $('start').disabled = !ready || !state.desktop_ready || active || !!pending || stopping || !!switching;
    $('stop').disabled = (!active && !pending) || stopping || !!switching;
    $('start').textContent = pending ? '杂鱼♡准备好挨广告吧……' : '杂鱼♡让本喵占领桌面！';
    if (phaseLabel && !pending) $('start').textContent = '杂鱼♡让本喵\n占领桌面！';
    $('stop').textContent = stopping ? '杂鱼♡认输处理中……' : pending ? '杂鱼♡这就反悔啦？' : '杂鱼♡这就认输啦？';
    $('stop').setAttribute('aria-label', pending ? '取消开局：杂鱼♡这就反悔啦？' : '结束本局：杂鱼♡这就认输啦？');
    $('shortcut').disabled = !!pending || stopping || !!switching;
    $('voice').disabled = !ready || !!pending || stopping || !!switching || changingVoice;
    const preparation = state && state.voice_preparation;
    const count = preparation ? preparation.ready + '/' + preparation.total : '0/9';
    $('voice-status').textContent = !ready ? '插件启动后静默准备语音。' : state.voice_enabled === false ? '语音已关闭' : !preparation || preparation.state === 'waiting' ? '等待当前猫娘，语音会静默准备。' :
      preparation.state === 'ready' ? '当前猫娘语音已就绪 · ' + count : preparation.state === 'paused' ? '语音准备已暂停 · ' + count :
      ['partial','unavailable'].includes(preparation.state) ? '部分语音暂未就绪 · ' + count + '，可重试。' : '正在静默准备语音 · ' + count;
    if (phaseLabel && ready && state.voice_enabled !== false && preparation && preparation.state === 'ready') $('voice-status').textContent = '语音已就绪 · ' + count;
    $('voice-retry').disabled = !ready || !state.voice_enabled || !preparation || !['paused','partial','unavailable'].includes(preparation.state);
    $('status').textContent = ({
      enabling: '正在启动插件，完成后进入待机……',
      disabling: '正在关闭插件，本局广告与声音一并结束……',
      starting: '正在开局，正在确认主界面和退出快捷键……', stopping: '正在结束本局……',
      reconnecting: '连接暂时中断，正在重连。可在猫娘主界面输入安全词或按退出快捷键。',
      unavailable: runtime === 'running' ? '插件正在运行，等待控制页连接恢复。' : '插件服务连接不可用，请检查宿主是否正在运行。',
      disabled: '插件未启动，点击上方“一键启动插件”进入待机。',
      playing: '游戏进行中', standby: '插件已启用 · 待机中', waiting: '插件已启用 · 等待猫娘主界面连接',
    })[phase];
    const name = state && (active ? state.character : state.desktop && state.desktop.character);
    $('connection').textContent = name ? '当前猫娘：' + name : ready ? '请打开或重新加载猫娘主界面后再开局。' : '';
    $('message').textContent = message;
  }
  async function request(path, options = {}, timeout = 6000) {
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), timeout);
    try {
      const response = await fetch(path.startsWith('/plugin/') ? path : base + path, {...options, signal: abort.signal, cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '插件连接不可用');
      return data;
    } finally { clearTimeout(timer); }
  }
  async function command(command, payload) {
    const data = await request('/action/desktop_command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({kind:'panel',surface_id:'main',args:{command,payload}})});
    const result = data.result || {};
    if (result.success === false || result.status === 'error' || result.error) throw new Error(result.error && (result.error.message || result.error) || '广告操作失败');
    return result.data || result.value || result;
  }
  async function context() {
    const data = await request('/context?kind=panel&id=main');
    // Host metadata comes from this installation's manifest, even while stopped.
    // Both surfaces reuse this request; version display never adds another poll.
    metadata(data.plugin);
    if (!data.state || typeof data.state.ready !== 'boolean' || typeof data.state.active !== 'boolean') throw new Error('无效状态');
    return data.state;
  }
  async function update() {
    const version = revision;
    try {
      const next = await context();
      if (version !== revision) return;
      state = next; available = true; runtime = state.ready ? 'running' : 'stopped';
      if (!changingVoice) $('voice').checked = state.voice_enabled !== false;
      if (state.active && !recording) { selected = state.shortcut; notice(); }
    } catch (_) {
      if (version !== revision) return;
      available = false;
      try {
        // The static page remains available when the plugin process is stopped.
        const host = await request('/plugin/status?plugin_id=neko_ads');
        if (version !== revision) return;
        const value = host.status && host.status.status;
        if (value === 'running') runtime = 'running';
        else if (['stopped', 'disabled', 'error', 'crashed', 'frozen'].includes(value)) {
          runtime = 'stopped'; available = true; state = {...state, ready: false, active: false};
        } else runtime = 'unknown';
      } catch (_) { if (version === revision) runtime = 'unknown'; }
    }
    finally { if (version === revision) render(); setTimeout(update, 2000); }
  }
  $('enable').onclick = async () => {
    if ($('enable').disabled || switching) return;
    const action = runtime === 'running' ? 'stop' : 'start';
    const old = pending || state && state.active && state.round_id, version = ++revision;
    switching = action; runtimeConfirmation = null; pending = null; stopping = false; message = ''; render();
    let completed = false;
    try {
      if (action === 'stop' && old) {
        // Retire an active or in-flight round before stopping its process.
        try { const next = await command('stop', {round_id:old}); if (version === revision) state = next; } catch (_) {}
      }
      const result = await request('/plugin/neko_ads/' + action, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'}, 20000);
      if (result.success !== true) throw new Error(action === 'stop' ? '插件关闭未成功，请查看插件日志。' : '插件启动未成功，请查看插件日志。');
      if (version !== revision) return;
      completed = true;
      if (action === 'stop') {
        runtime = 'stopped'; available = true; state = {...state, ready:false, active:false, round_id:null};
        message = '插件已关闭，广告与声音已停止。'; return;
      }
      runtime = 'running';
      const next = await context();
      if (version !== revision) return;
      state = next; available = true;
      message = next.active ? '' : '插件已启动，正在待机。想玩时再点击下方开局按钮。';
    } catch (error) {
      if (version === revision) {
        if (completed) available = false;
        if (completed || error.name === 'AbortError') runtimeConfirmation = action;
        message = completed ? '插件已启动，正在等待控制页连接。' : error.name === 'AbortError' ? '操作等待超时，正在检查插件状态。' : error.message;
      }
    } finally { if (version === revision) revision++; switching = null; render(); }
  };
  $('start').onclick = async () => {
    if ($('start').disabled || pending || stopping || !state || !state.desktop) return;
    const id = crypto.randomUUID(), version = ++revision;
    pending = id; message = ''; render();
    try { localStorage.setItem('neko-ads-current-round', JSON.stringify({round_id:id, owner:state.desktop.client_id})); } catch (_) {}
    try {
      const next = await command('start', {round_id:id,epoch:state.epoch,stop_epoch:state.stop_epoch,owner:state.desktop.client_id,confirmed:true,shortcut:selected,voice_enabled:$('voice').checked});
      if (version !== revision || pending !== id) { await command('stop', {round_id:id}); return; }
      if (!next.active || next.round_id !== id) throw new Error('本次开局未完成，请重新点击开始。');
      state = next; available = true;
    } catch (e) { if (version === revision) { message = e.message; void command('stop', {round_id:id}).catch(() => {}); } }
    finally { if (version === revision) { revision++; pending = null; render(); } }
  };
  $('stop').onclick = async () => {
    if ($('stop').disabled) return;
    const id = pending || state && state.round_id, version = ++revision;
    if (id && typeof BroadcastChannel === 'function') {
      const exitChannel = new BroadcastChannel('neko_ads_exit');
      exitChannel.postMessage({type:'stop', round_id:id}); exitChannel.close();
    }
    pending = null; stopping = true; message = ''; render();
    try {
      const next = await command('stop', id ? {round_id:id} : {stop_epoch:state && state.stop_epoch});
      if (version !== revision) return;
      state = next; available = true; message = '本局已结束，插件继续待机。';
    } catch (_) { if (version === revision) { available = false; message = '结束请求未确认，请在猫娘主界面输入安全词或按全局退出快捷键。'; } }
    finally { if (version === revision) { revision++; stopping = false; render(); } }
  };
  $('voice').onchange = async () => {
    const enabled = $('voice').checked, version = revision; changingVoice = true; render();
    try { const next = await command('voice', {enabled}); if (version === revision) { state = next; available = true; } }
    catch (error) { if (version === revision) message = error.message; }
    finally { changingVoice = false; render(); }
  };
  $('voice-retry').onclick = async () => {
    if ($('voice-retry').disabled) return;
    try { state = await command('prepare_voice', {}); render(); } catch (error) { message = error.message; render(); }
  };
  $('shortcut').onkeydown = async e => {
    e.preventDefault(); if (recording || pending || stopping || switching || ['Control','Shift','Alt','Meta'].includes(e.key)) return;
    const mods = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift'].filter(Boolean);
    const key = /^Digit\d$/.test(e.code) ? e.code.slice(5) : /^Key[A-Z]$/.test(e.code) ? e.code.slice(3) : /^F(?:[1-9]|10|11)$/.test(e.code) ? e.code : '';
    if (!mods.length || !key || e.metaKey) { message = '请使用修饰键与字母、数字或 F1–F11 组合。'; render(); return; }
    const next = [...mods,key].join('+'), version = revision; recording = true;
    try {
      if (state && state.active) await command('shortcut',{round_id:state.round_id,shortcut:next});
      if (version !== revision) return;
      revision++; selected=next; message=''; localStorage.setItem(storage,next); notice();
    } catch(e) { if (version === revision) message=e.message; }
    finally { recording=false; render(); }
  };
  notice(); render(); update();
})();
