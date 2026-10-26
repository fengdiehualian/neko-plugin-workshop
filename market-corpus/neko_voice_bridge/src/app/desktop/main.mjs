import {app, BrowserWindow, ipcMain, dialog, protocol, session, clipboard, shell} from 'electron';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes} from 'node:crypto';
import {BridgeEngine} from '../bridge/engine.mjs';
import {HostSpeechProviders} from '../bridge/host-speech.mjs';
import {createServer} from '../bridge/server.mjs';
import {BrowserAdapter} from './browser-adapter.mjs';
import {installVoiceObserver} from './voice-observer.mjs';
import {demo} from '../bridge/demo.mjs';
import {DiagnosticsLog} from './diagnostics.mjs';
import {ensureVirtualMicrophoneConsent} from './virtual-microphone-consent.mjs';
import {FirstUseNotice} from './first-use-notice.mjs';
import {CableCapture} from './cable-capture.mjs';
import {APP_VERSION} from '../bridge/release.mjs';
import {setupStatus} from './setup-guide.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const loginSmoke = process.argv.includes('--login-smoke');
const selfTest = process.argv.includes('--self-test') || loginSmoke;
if (selfTest) app.setPath('userData', path.join(process.env.TMPDIR || '/tmp', 'neko-voice-desktop-test'));
app.setName('Neko Voice Bridge');
if (process.platform === 'win32') app.setAppUserModelId('NekoVoiceBridge.Desktop');
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
// The offline RTC fixture has no hardware-capture permission and no mDNS
// responder. Expose local ICE candidates only inside this local self-test.
if (selfTest && !loginSmoke) app.commandLine.appendSwitch('disable-features', 'WebRtcHideLocalIpsWithMdns');
if (selfTest && !loginSmoke) app.commandLine.appendSwitch('use-fake-device-for-media-stream');
protocol.registerSchemesAsPrivileged([{scheme: 'nvb', privileges: {standard: true, secure: true, supportFetchAPI: true}}]);
const single = app.requestSingleInstanceLock();
if (!single) app.quit();
let dashboard, account, adapter, engine, server, tick, cfg, quitting = false;
let diagnostics;
let cable, cableChanging = false, cableTesting = false;
let wakeChanging = false;
let guideOpen = false;
let lastProblem = '';
const dataDir = app.getPath('userData'), configFile = path.join(dataDir, 'settings.json');
const isKook = value => {try {const u = new URL(value); return u.protocol === 'https:' && ['www.kookapp.cn', 'kookapp.cn'].includes(u.hostname);} catch {return false;}};
async function resourceHandler(request) {
  const name = new URL(request.url).pathname.slice(1);
  if (!['index.html', 'ui.js', 'style.css', 'test.html', 'cable.html', 'cable-renderer.mjs', 'cable-buffer.mjs'].includes(name)) return new Response('Not found', {status: 404});
  const body = name === 'test.html' ? '<!doctype html><title>Local audio test</title>' : await readFile(path.join(root, 'desktop', name));
  const type = name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript';
  return new Response(body, {headers: {'Content-Type': type + '; charset=utf-8'}});
}
function initialConfig() {
  return {mode: 'kook', port: 18765, bridge_token: randomBytes(32).toString('hex'),
    kook: {}, speech_mode: 'neko'};
}
async function saveConfig(value = cfg) {
  const disk = structuredClone(value);
  await mkdir(dataDir, {recursive: true});
  await writeFile(configFile + '.tmp', JSON.stringify(disk, null, 2), {mode: 0o600});
  const {rename} = await import('node:fs/promises'); await rename(configFile + '.tmp', configFile);
}
async function loadConfig() {
  try {
    cfg = JSON.parse(await readFile(configFile, 'utf8'));
    // Migrate old desktop settings without decrypting or using provider keys.
    delete cfg.asr; delete cfg.tts; delete cfg.wake_words; cfg.speech_mode = 'neko';
    await saveConfig();
  } catch (error) {
    if (error.code !== 'ENOENT') throw new Error('配置读取失败，请保留 settings.json 并检查文件格式');
    cfg = initialConfig(); await saveConfig();
  }
}
async function execute(method, ...args) {
  if (!account || account.isDestroyed()) throw new Error('ACCOUNT_WINDOW_CLOSED');
  if (!['enable', 'cancel', 'play', 'status'].includes(method)) throw new Error('UNKNOWN_BROWSER_METHOD');
  return account.webContents.executeJavaScript(`globalThis.__nekoVoice?.[${JSON.stringify(method)}](...${JSON.stringify(args)})`, true);
}
async function instrument(window, testMode = false) {
  if (!window.webContents.getURL()) await window.loadURL('about:blank');
  const debuggerApi = window.webContents.debugger;
  const binding = '__nvb_' + randomBytes(12).toString('hex'), contexts = new Set();
  debuggerApi.attach('1.3');
  debuggerApi.on('message', (_event, method, params) => {
    if (method === 'Runtime.executionContextCreated') {
      const c = params.context;
      if (c.auxData?.isDefault && (isKook(c.origin) || (testMode && c.origin.startsWith('nvb://')))) contexts.add(c.id);
    } else if (method === 'Runtime.executionContextDestroyed') contexts.delete(params.executionContextId);
    else if (method === 'Runtime.executionContextsCleared') contexts.clear();
    else if (method === 'Runtime.bindingCalled' && params.name === binding && contexts.has(params.executionContextId)) {
      if (params.payload.length > 18000) return;
      try {adapter.receive(JSON.parse(params.payload));} catch {lastProblem = '音频事件格式异常';}
    }
  });
  await debuggerApi.sendCommand('Runtime.enable');
  await debuggerApi.sendCommand('Page.enable');
  await debuggerApi.sendCommand('Runtime.addBinding', {name: binding});
  await debuggerApi.sendCommand('Page.addScriptToEvaluateOnNewDocument', {source: `(${installVoiceObserver.toString()})(${JSON.stringify(binding)}, ${testMode})`});
  window.webContents.on('render-process-gone', () => {adapter.reset(true); lastProblem = 'KOOK 窗口已退出，请重新打开并连接';});
  window.webContents.on('did-start-navigation', (_event, _url, inPlace, mainFrame) => {if (mainFrame && !inPlace) adapter.reset(true);});
  window.on('closed', () => {adapter.reset(true); account = null;});
}
async function openAccount() {
  if (account && !account.isDestroyed()) {account.show(); return;}
  if (!selfTest && !await ensureVirtualMicrophoneConsent(cfg, saveConfig, options => dialog.showMessageBox(dashboard, options))) throw new Error('VIRTUAL_MICROPHONE_CONSENT_REQUIRED');
  const ses = session.fromPartition('persist:kook-account');
  // Native hardware capture stays denied. The consented page-world facade
  // exposes only generated audio and virtual-device permission state.
  ses.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
  account = new BrowserWindow({width: 1180, height: 800, title: '猫娘专用 KOOK 账号',
    webPreferences: {partition: 'persist:kook-account', nodeIntegration: false, contextIsolation: true, sandbox: true, backgroundThrottling: false}});
  account.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  account.webContents.on('will-navigate', (event, url) => {if (!isKook(url)) event.preventDefault();});
  account.webContents.setAudioMuted(true);
  await instrument(account);
  const ua = account.webContents.getUserAgent().replace(/Electron\/[\d.]+\s*/g, '');
  try {await account.loadURL('https://www.kookapp.cn/app/login', {userAgent: ua}); lastProblem = '';}
  catch {account?.destroy(); throw new Error('KOOK 页面加载失败，请检查网络后重新打开');}
}
function state() {
  const bridge = engine.status(), browser = {...adapter.stats, connected: adapter.connected, mapped_tracks: adapter.mapped.size};
  return {bridge, browser, version: APP_VERSION,
    setup: setupStatus(cfg, bridge, browser, cable, engine.events, guideOpen),
    cable: {...cable.status(), mode: cfg.output_transport === 'cable' ? 'cable' : 'websocket', testing: cableTesting},
    problem: lastProblem, errors: diagnostics?.errors.slice(-5) || [], diagnostic_save_failed: diagnostics?.failed || false,
    events: engine.events.filter(e => ['transcript.final', 'reply.skipped', 'reply.accepted', 'playback.started', 'playback.finished', 'playback.failed', 'error'].includes(e.type)).slice(-12),
    experimental: true, platform_account_tested: false};
}
function copyConnectionCode() {
  const data = {bridge_url: `http://127.0.0.1:${cfg.port}`, bridge_token: cfg.bridge_token};
  clipboard.writeText(data.bridge_url + '/#token=' + encodeURIComponent(data.bridge_token));
  return {copied: true, message: '连接链接已复制。请打开猫娘插件的“语音频道连接”面板，粘贴并连接。'};
}
async function command(action, data) {
  if (action === 'state') return state();
  if (action === 'setup-open') {guideOpen = true; return {};}
  if (action === 'setup-close') {guideOpen = false; return {};}
  if (action === 'setup-output') {
    if (cfg.output_transport === 'cable' && !cable.ready) throw new Error('NEKO_CABLE_NOT_READY');
    await saveConfig({...cfg, setup_output_confirmed: true}); cfg.setup_output_confirmed = true;
    return {message: '输出线路已确认，下一步登录 KOOK 并加入频道。'};
  }
  if (action === 'setup-finish') {
    if (!state().setup.can_finish || !cfg.setup_output_confirmed) throw new Error('请先完成连接、输出线路确认和频道试音，再确认另一账号已听到声音');
    await saveConfig({...cfg, setup_completed: 1}); cfg.setup_completed = 1; guideOpen = false;
    return {message: '首次配置已完成。以后可随时点击“配置向导”重新检查。'};
  }
  if (action === 'wake-filter') {
    if (typeof data?.enabled !== 'boolean') throw new Error('BOOLEAN_REQUIRED');
    if (wakeChanging || cableTesting) throw new Error('请等待当前设置或线路测试完成');
    wakeChanging = true;
    try {
      await saveConfig({...cfg, wake_filter_enabled: data.enabled});
      engine.setWakeFilter(data.enabled);
      return {message: data.enabled ? '已开启称呼过滤：新发言需包含当前猫娘称呼。' : '已关闭称呼过滤：新发言无需称呼即可回复；原有待处理对话已取消。'};
    } finally {wakeChanging = false;}
  }
  if (action === 'cable-guide') {await shell.openExternal('https://vb-audio.com/Cable/'); return {message: '已打开 VB-CABLE 官网。安装驱动后重启 Windows，再刷新设备。'};}
  if (action === 'cable-devices') return {devices: await cable.devices()};
  if (action === 'cable-start' || action === 'cable-off') {
    if (cableChanging || cableTesting || engine.state !== 'READY' || engine.providers.jobs.size) throw new Error('请先停止接收，再切换输出线路');
    cableChanging = true; engine.outputChanging = true;
    try {
      if (action === 'cable-start') {
        if (typeof data?.deviceId !== 'string' || data.deviceId.length > 256) throw new Error('请先刷新并选择虚拟音频线');
        await cable.start(data.deviceId);
        cfg.output_transport = 'cable'; engine.providers.cable = cable;
      } else {
        await cable.stop(); cfg.output_transport = 'websocket'; engine.providers.cable = null;
      }
      await saveConfig();
      return {message: action === 'cable-start' ? '虚拟音频线已启用。请将猫娘输出设为 CABLE Input，再开始接收。' : '已切回插件音频模式；如需本机播放，请在猫娘中恢复原输出设备。'};
    } finally {cableChanging = false; engine.outputChanging = false;}
  }
  if (action === 'cable-test') {
    if (cableTesting || engine.state !== 'ACTIVE' || engine.replyBusy || engine.asrRunning) throw new Error('请开始接收，并等当前对话结束再测试');
    const wasListening = engine.listening; engine.setListening(false); cableTesting = true;
    const signal = engine.controller.signal;
    let recording;
    try {
      recording = cable.begin(signal);
      while (true) {
        signal.throwIfAborted();
        const status = recording.progress('manual-output-test');
        if (status.error) throw new Error(status.error);
        if (status.done) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      const pcm = recording.samples(); recording.stop(); recording = null;
      await engine.testOutput(false, pcm);
      return {message: '虚拟音频线已收到声音，并检测到 KOOK 发出音频包。请用另一个账号确认声音。'};
    } finally {recording?.stop(); cableTesting = false; engine.setListening(wasListening);}
  }
  if (action === 'account') {await openAccount(); return {message: '请登录专用账号，并在 KOOK 窗口加入语音频道、开启麦克风。麦克风由猫娘语音提供。'};}
  if (action === 'start') {
    if (cableTesting) throw new Error('请等待线路测试结束后再开始接收');
    if (cableChanging || (cfg.output_transport === 'cable' && !cable.ready)) throw new Error('NEKO_CABLE_NOT_READY');
    if (!engine.status().plugin.connected) throw new Error('请先在猫娘插件的连接面板中完成连接');
    await engine.join(); return {message: '开始接收。只有已对应昵称的音轨才会交给猫娘转写。'};
  }
  if (action === 'test-output' || action === 'test-tone') {
    await engine.testOutput(action === 'test-tone');
    return {message: '已检测到音频数据包发出，请用另一个 KOOK 账号确认是否听到。'};
  }
  if (action === 'export-diagnostics') {
    const target = await dialog.showSaveDialog(dashboard, {title: '保存语音桥诊断', defaultPath: 'neko-voice-diagnostics.json', filters: [{name: '诊断文件', extensions: ['json']}]});
    if (target.canceled || !target.filePath) return {message: '已取消导出'};
    await diagnostics.pending;
    const report = {version: '0.7.1', at_ms: Date.now(), bridge_state: engine.state,
      plugin_connected: engine.status().plugin.connected, speech_error: engine.providers.status?.().error,
      browser: adapter.stats, wake_filter_enabled: cfg.wake_filter_enabled !== false,
      cable: {...cable.status(), mode: cfg.output_transport || 'websocket'}, rtc_connected: adapter.connected, records: diagnostics.records, errors: diagnostics.errors};
    await writeFile(target.filePath, JSON.stringify(report, null, 2), {mode: 0o600});
    return {message: '诊断已保存，不含账号凭据、昵称、发言原文或录音。'};
  }
  if (action === 'stop') {await engine.leave(); return {message: '接收已停止；关闭 KOOK 窗口可退出频道。'};}
  if (action === 'leave') {if (account) account.close(); await engine.leave(); return {message: '已关闭账号窗口并停止接收。'};}
  if (action === 'demo') return {demo: await demo(), message: '双人模拟演示通过；未调用真实模型或语音服务。'};
  if (action === 'copy-code') return copyConnectionCode();
  throw new Error('未知操作');
}
async function launch() {
  if (selfTest) {console.log('SELF_TEST: launch'); setTimeout(() => {console.error('SELF_TEST: launch watchdog'); app.exit(1);}, 65000).unref();}
  await app.whenReady();
  if (selfTest) console.log('SELF_TEST: app ready');
  protocol.handle('nvb', resourceHandler);
  await loadConfig();
  guideOpen = cfg.setup_completed !== 1;
  cfg.require_plugin_version = true;
  if (selfTest) console.log('SELF_TEST: config loaded');
  diagnostics = new DiagnosticsLog(path.join(dataDir, 'diagnostics.json')); await diagnostics.load();
  engine = new BridgeEngine(cfg, new HostSpeechProviders());
  cable = new CableCapture(root, resourceHandler);
  if (cfg.output_transport === 'cable') engine.providers.cable = cable;
  const emit = engine.emit.bind(engine);
  engine.emit = (type, payload) => {const event = emit(type, payload); diagnostics.record(event); return event;};
  adapter = new BrowserAdapter(engine, execute); engine.attach(adapter);
  if (!selfTest) {
    server = createServer(engine, cfg.bridge_token);
    await new Promise((resolve, reject) => {server.once('error', reject); server.listen(cfg.port, '127.0.0.1', resolve);});
  }
  tick = setInterval(() => {engine.segmenter.tick(); engine.prune(); void engine.checkPluginLink().catch(() => {});}, 100);
  dashboard = new BrowserWindow({width: 1080, height: 820, minWidth: 850, minHeight: 650, title: '猫娘语音桥', show: !selfTest,
    icon: path.join(root, 'desktop/assets/icon.png'),
    webPreferences: {preload: path.join(root, 'desktop/preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true}});
  dashboard.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  dashboard.webContents.on('will-navigate', event => event.preventDefault());
  ipcMain.handle('nvb-command', async (event, action, data) => {
    if (event.sender !== dashboard.webContents || event.senderFrame !== dashboard.webContents.mainFrame || !event.senderFrame.url.startsWith('nvb://app/')) throw new Error('INVALID_SENDER');
    try {return {ok: true, data: await command(action, data)};}
    catch (e) {
      diagnostics.record({type: 'command.failed', action, code: /^[A-Z0-9_]+$/.test(e.message) ? e.message : 'OPERATION_FAILED'});
      return {ok: false, message: /^[A-Z0-9_]+$/.test(e.message) || /[\u4e00-\u9fff]/.test(e.message) ? e.message : '操作未完成，请检查本地配置或网络'};
    }
  });
  await dashboard.loadURL('nvb://app/index.html');
  const notice = new FirstUseNotice(cfg, saveConfig, options => dialog.showMessageBox(dashboard, options));
  let readingOutput = false;
  const outputTimer = setInterval(async () => {
    if (readingOutput || !account || account.isDestroyed()) return;
    readingOutput = true;
    try {const value = await execute('status'); if (value) adapter.receive({type: 'output.status', ...value});}
    catch {} finally {readingOutput = false;}
  }, 1200);
  const noticeTimer = !selfTest && setInterval(() => {
    const plugin = engine.status().plugin;
    if (plugin.connected) void notice.tick(plugin.character?.name).catch(() => {lastProblem = '首次提示保存失败，下次会再次显示';});
  }, 1200);
  dashboard.on('closed', () => {clearInterval(outputTimer); if (noticeTimer) clearInterval(noticeTimer); if (!quitting) app.quit();});
  if (selfTest) console.log('SELF_TEST: dashboard loaded');
  if (selfTest) {
    if (loginSmoke) {
      await openAccount();
      await new Promise(resolve => setTimeout(resolve, 4000));
      const page = await account.webContents.executeJavaScript('({title:document.title, observer_ready:!!globalThis.__nekoVoice, text:document.body.innerText.slice(0,1200)})');
      await writeFile(path.join(dataDir, 'kook-login.png'), (await account.webContents.capturePage()).toPNG());
      console.log('LOGIN_SMOKE', JSON.stringify(page));
      app.quit(); return;
    }
    const {runSelfTest} = await import('./self-test.mjs');
    account = new BrowserWindow({show: false, webPreferences: {nodeIntegration: false, contextIsolation: true, sandbox: true, backgroundThrottling: false}});
    account.webContents.session.setPermissionCheckHandler((_c, permission) => ['local-network', 'loopback-network', 'local-network-access'].includes(permission));
    account.webContents.session.setPermissionRequestHandler((_c, permission, callback) => callback(['local-network', 'loopback-network', 'local-network-access'].includes(permission)));
    account.webContents.setAudioMuted(true);
    await instrument(account, true);
    console.log('SELF_TEST: instrumented');
    await account.loadURL('nvb://app/test.html');
    console.log('SELF_TEST: fixture loaded');
    let timeout;
    try {await Promise.race([runSelfTest({account, dashboard, engine, adapter, cable, dataDir}), new Promise((_, reject) => {timeout = setTimeout(() => reject(new Error('SELF_TEST_TIMEOUT')), 50000);})]);}
    finally {clearTimeout(timeout);}
    app.quit();
  }
}
app.on('second-instance', () => {dashboard?.show(); dashboard?.focus();});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (quitting) return; event.preventDefault(); quitting = true; clearInterval(tick);
  server?.close(); server?.closeAllConnections();
  void (async () => {
    let timer;
    try {await Promise.race([engine?.leave(), new Promise(resolve => {timer = setTimeout(resolve, 2000);})]);}
    catch {}
    finally {clearTimeout(timer); cable?.destroy(); if (account && !account.isDestroyed()) account.destroy(); app.quit();}
  })();
});
if (single) launch().catch(error => {
  if (selfTest) {console.error('SELF_TEST_FAILED', error.message); app.exit(1); return;}
  else dialog.showErrorBox('猫娘语音桥未能启动', error.code === 'EADDRINUSE' ? '端口 18765 已被占用，请关闭另一份语音桥后重试。' : '启动失败，请检查程序文件是否完整、配置能否读取。');
  process.exitCode = 1; app.quit();
});
