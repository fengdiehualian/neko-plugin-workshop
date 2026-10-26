const $ = selector => document.querySelector(selector);
async function call(action, data) {
  const result = await window.nekoBridge.command(action, data);
  if (!result.ok) throw new Error(result.message);
  return result.data;
}
const errors = {
  PLUGIN_VERSION_MISMATCH: '插件与程序版本不匹配，请前往猫娘计划插件市场更新“猫娘语音桥”，然后重启插件。若市场暂未提供对应版本，可导入随程序提供的 .neko-plugin。',
  NEKO_CABLE_NOT_READY: '虚拟音频线未就绪。停止接收后，刷新设备并启用 CABLE Output。',
  NEKO_CABLE_DEVICE_LOST: '虚拟音频线采集中断，请停止接收并重新启用设备。',
  NEKO_CABLE_NO_AUDIO: '没有收到虚拟音频线声音。这发生在称呼过滤之前；请检查本体输出线路，并确认插件和 EXE 均已更新。',
  NEKO_CABLE_UPDATE_PLUGIN: '虚拟音频线模式需要新版插件，请同时更新 .neko-plugin。',
  NEKO_CABLE_PERMISSION_DENIED: 'Windows 拒绝录音权限。请允许桌面应用访问麦克风；程序只采集所选 CABLE Output。',
  NEKO_CABLE_CAPTURE_FAILED: '无法打开虚拟音频线。请确认驱动已安装并重启电脑。',
  NEKO_CABLE_DEVICE_NOT_FOUND: '没有找到所选 VB-CABLE 录音端，请刷新设备重新选择。',
  NEKO_CABLE_AUDIO_TOO_LONG: '输出超过 30 秒，本次未发送。请先用一句简短回复测试。',
  NEKO_CABLE_TURN_TIMEOUT: '音频线持续有声音或没有收到转写，本次未发送。请检查是否有其他程序输出到此设备。',
  KOOK_OUTPUT_PROBE_UNAVAILABLE: '无法读取 KOOK 实际发送音轨，请重新入房后测试。',
  REPLY_AUDIO_SILENT: '本次回复音频本身是静音，请用“测试频道提示音”单独检查 KOOK。',
  VIRTUAL_MICROPHONE_CONSENT_REQUIRED: '尚未允许使用内置虚拟麦克风。请再次点击“打开 KOOK 登录”并选择允许。',
  NEKO_HOST_NO_TRANSCRIPT: '猫娘未向插件返回识别或回复。可能存在本体输出连接切换，请导出诊断。',
  NEKO_HOST_NO_REPLY_AUDIO: '收到文字回复，但没有收到猫娘的语音。',
  VIRTUAL_MICROPHONE_NOT_READY: '虚拟麦克风尚未创建，请在程序的 KOOK 窗口开启麦克风。',
  KOOK_MICROPHONE_NOT_PUBLISHED: 'KOOK 尚未发布麦克风音轨，请开启麦克风并确认有频道发言权限。',
  KOOK_MICROPHONE_MUTED: 'KOOK 麦克风已静音或被禁用，请解除静音；按键发言模式需按住发言键。',
  KOOK_MICROPHONE_CHANGED: '发送期间麦克风状态改变，请重新测试。',
  KOOK_RTP_NOT_SENDING: '虚拟麦克风已播放，但 KOOK 没有发出音频数据包。',
  KOOK_OUTPUT_SILENT: 'KOOK 发送端仍为静音，请检查静音、发言模式和音频处理设置。',
  KOOK_OUTPUT_STATS_FAILED: '无法读取 KOOK 发送结果，请重新入房后测试。',
  CANCELLED_OR_TIMEOUT: '语音操作取消或超时；可先用“测试频道提示音”检查频道发送。',
};
const explain = code => errors[code] ? `${errors[code]}（${code}）` : code;
function message(text, error = false) {$('#message').textContent = explain(text); $('#message').className = error ? 'error show' : 'show';}
async function refresh() {
  try {
    const s = await call('state');
    $('#cable-mode').textContent = s.cable.mode === 'cable' ? '虚拟音频线模式' : '插件音频模式';
    $('#cable-status').textContent = s.cable.error ? explain(s.cable.error) : !s.cable.ready ? '采集未启用；设备选择只显示 VB-CABLE 录音端' : s.cable.recording ? (s.cable.speech_detected ? '已收到本体声音，等待一句结束' : '正在等待本体输出声音') : '采集已就绪；让本体说话时，音量条应有变化';
    $('#cable-level').value = s.cable.peak;
    for (const action of ['cable-start', 'cable-off']) $('[data-action="'+action+'"]').disabled = s.bridge.state !== 'READY' || s.cable.testing;
    $('[data-action="cable-test"]').disabled = !s.cable.ready || s.bridge.state !== 'ACTIVE' || s.bridge.reply_busy || s.cable.testing;
    const compat = s.bridge.plugin?.compatibility;
    $('#version-status').textContent = !s.bridge.plugin?.connected ? `程序 ${s.version} · 等待插件连接后检查版本。` : compat?.ok ? `程序 ${s.version} · 插件 ${compat.actual} · 版本匹配` : `需要更新：程序 ${s.version}，插件 ${compat?.actual || '未报告版本（旧版插件）'}。请前往猫娘计划插件市场，将“猫娘语音桥”更新至 ${compat?.expected || s.version}，然后重启插件；无需重新登录 KOOK。若市场暂未提供该版本，可导入随程序提供的 .neko-plugin。`;
    $('#setup-guide').hidden = !s.setup.open;
    s.setup.steps.forEach((done, i) => {$('#setup-step-'+i).textContent = done ? '✓' : String(i+1);});
    $('[data-action="setup-finish"]').disabled = !s.setup.can_finish;
    $('#setup-summary').textContent = s.setup.can_finish ? '程序已检测到音频包发出；请亲自确认另一个账号能听到，再完成设置。' : '已完成 '+s.setup.steps.filter(Boolean).length+' / 4 步。音频包发出后仍需另一账号试听确认。';
    $('#bridge-state').textContent = !s.bridge.plugin?.connected ? '未连接猫娘' : s.bridge.state === 'ACTIVE' ? '正在接收' : '已连接，待接收';
    $('#rtc-state').textContent = s.browser.connected ? '语音已连接' : s.browser.hook_ready ? '等待加入频道' : '等待登录';
    $('#rtc-detail').textContent = s.problem || '账号登录与频道切换在 KOOK 窗口操作';
    $('#track-count').textContent = s.browser.mapped_tracks;
    $('#microphone-state').textContent = !s.browser.connected ? '虚拟麦克风：等待加入 KOOK 语音频道' : '虚拟麦克风：' + ({missing: '尚未创建，请在 KOOK 开启麦克风', unpublished: '已创建，KOOK 尚未发布音轨', muted: 'KOOK 发送音轨已静音', ready: '已发布可用音轨，可测试频道提示音'}[s.browser.microphone_state] || '等待 KOOK 窗口');
    $('#errors').replaceChildren(...s.errors.slice().reverse().map(e => {const row = document.createElement('div'); row.textContent = new Date(e.at_ms).toLocaleTimeString() + ' · ' + explain(e.code); return row;}));
    if (!s.errors.length) $('#errors').textContent = '暂无错误';
    if (s.diagnostic_save_failed) $('#errors').append(' 诊断文件保存失败，请检查磁盘权限。');
    $('#frames').textContent = `已收到 ${s.browser.frames} 帧 · 频道显示名`;
    $('#members').replaceChildren(...s.bridge.members.map(p => {const span = document.createElement('span'); span.className = 'member'; span.textContent = p.display_name + ' · 频道显示名'; return span;}));
    if (!s.bridge.members.length) $('#members').textContent = '尚未发现可关联昵称的音轨';
    $('#events').replaceChildren(...s.events.map(e => {const row = document.createElement('div'); row.textContent = e.type === 'transcript.final' ? `${e.speaker.display_name}：${e.text}` :
      e.type === 'reply.skipped' ? ({wake_name: '未播回 KOOK：发言未包含当前猫娘称呼', muted: '未播回 KOOK：输出已静音', identity: '未播回 KOOK：音轨来源尚未确认'}[e.reason] || '未播回 KOOK') :
      e.type === 'reply.accepted' ? '已接收回复，准备发送到 KOOK' : e.type === 'playback.started' ? '正在向 KOOK 发送语音' :
      e.type === 'playback.finished' ? (e.meaning === 'outbound_rtp_verified' ? '已检测到 KOOK 音频数据包发出（请在另一账号试听）' : '本地播放完成，尚未验证频道接收') : `处理状态：${explain(e.code || e.type)}`; return row;}));
    if (!s.events.length) $('#events').textContent = '还没有发言。双人模拟不会调用真实语音或模型服务。';
    $('#speech-state').textContent = s.bridge.plugin?.connected ? '猫娘插件已连接' : '等待猫娘插件连接';
    $('#speech-error').textContent = s.bridge.speech?.error ? `猫娘语音状态：${s.bridge.speech.error}；请检查猫娘内的语音设置和连接面板。` : '';
    $('#character-name').textContent = s.bridge.plugin?.character?.name || '等待猫娘同步';
    const wakeEnabled = s.bridge.wake_filter_enabled !== false;
    const wakeButton = $('[data-action="wake-filter"]');
    wakeButton.textContent = '仅回应称呼：' + (wakeEnabled ? '开启' : '关闭');
    wakeButton.setAttribute('aria-checked', String(wakeEnabled));
    $('#reply-rule').textContent = !wakeEnabled ? '称呼过滤已关闭：无需先叫名字。仍检查发言来源、频道状态和静音。' : s.bridge.plugin?.character?.name ? `回复规则：发言需包含“${s.bridge.plugin.character.name}”，才会播回 KOOK；这是语音桥规则，不是本体要求。` : '回复规则：等待猫娘称呼同步。';
    $('[data-action="start"]').disabled = !s.bridge.plugin?.connected || !compat?.ok || !s.browser.connected || s.bridge.state !== 'READY';
    $('[data-action="stop"]').disabled = s.bridge.state !== 'ACTIVE';
    $('[data-action="test-tone"]').disabled = s.bridge.state !== 'ACTIVE' || s.bridge.reply_busy;
    $('[data-action="test-output"]').disabled = s.cable.mode === 'cable' || !s.bridge.plugin?.connected || s.bridge.state !== 'ACTIVE' || s.bridge.reply_busy;
  } catch (e) {message(e.message, true);}
}
for (const button of document.querySelectorAll('[data-action]')) button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    if (button.dataset.action === 'cable-test') message('正在等待本体说话：请在猫娘本体让她说一句简短的话。静音 2.5 秒后会发送到 KOOK；测试期间暂停接收频道发言。');
    const result = await call(button.dataset.action, button.dataset.action === 'cable-start' ? {deviceId: $('#cable-device').value} : button.dataset.action === 'wake-filter' ? {enabled: button.getAttribute('aria-checked') !== 'true'} : undefined);
    if (result.devices) {
      $('#cable-device').replaceChildren(...result.devices.map(d => {const o = document.createElement('option'); o.value = d.deviceId; o.textContent = d.label; return o;}));
      if (!result.devices.length) message('未发现 VB-CABLE 录音端。请先安装驱动并重启电脑，再刷新设备。', true);
      else message('已找到虚拟音频线，请选择并启用。');
    }
    if (result.message) message(result.message);
  }
  catch (e) {message(e.message, true);} finally {button.disabled = false; await refresh();}
});
for (const button of document.querySelectorAll('[data-nav]')) button.addEventListener('click', () => document.getElementById(button.dataset.nav)?.scrollIntoView({behavior:'smooth',block:'start'}));
void refresh(); setInterval(refresh, 1200);
