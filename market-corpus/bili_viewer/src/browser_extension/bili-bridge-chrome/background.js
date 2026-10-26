// B站接入桥 (Chrome) v0.2.9 —— Google Chrome 专用版
// 1) 把 B站登录态直连推送给本机 bili_viewer 插件（POST 127.0.0.1:8765，不上外网）；
// 2) 把当前活动标签页（URL+标题）实时推送给插件，让 YUI 精确感知你在看什么；
// 3) 在 B站 直播/视频页注入脚本读取页面实况（直播弹幕列表/在线人数/视频进度），
//    让 YUI 能"看到"直播间与视频正在发生的内容（需 scripting 权限）；
// 4) 截取当前活动标签页画面（captureVisibleTab）推送给插件（需 activeTab 权限；
//    JPEG 压缩后仅推送 127.0.0.1，不上外网）；
// 5) v0.2.5 声音捕获：popup 一次性开启后，tabCapture 捕获 B站 标签页音频，
//    offscreen document 采集成 wav 分段推送插件（本地 ASR 转文字让 YUI 听到）；
//    开关状态记忆在 storage，浏览器/扩展重启后自动恢复（后台静默，不抢界面）。
// 本版与 Edge/360 版本相互独立：推送 source 标记为 bili-bridge-chrome-extension，
// 插件日志可区分来源，多浏览器共存互不干扰。
// 触发时机：安装/浏览器启动立即推送；cookie 变化（onChanged 防抖）；
// 标签页切换/URL 变化（tabs 监听防抖）；alarms 30 秒兜底（MV3 SW 休眠时由 alarm 唤醒）；
// 推送失败 4 秒快速重试（插件重启后 5 秒内即可恢复感知，不再等下一轮 alarm）。

const EXT_SOURCE = "bili-bridge-chrome-extension";
const PANEL_URL = "http://127.0.0.1:8765";
const AUDIO_STATE_KEY = "audio_capture_enabled";

// 与插件 cdp_cookie._KEEP_NAMES 保持一致的 B站凭证名
const KEEP_NAMES = new Set([
  "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
  "buvid3", "buvid4", "b_nut", "CURRENT_FNVAL",
  "bili_ticket", "bili_ticket_expires", "sid",
  "fingerprint", "fingerprintp", "buvid_fp",
]);

// 窗口焦点门控（v0.2.9）：仅当浏览器窗口在前台时推送页面实况（tab/dom/shot），
// 避免 Chrome/Edge/360 同时打开时互相覆盖导致 YUI 感知错位；
// cookie 同步与声音捕获不受焦点限制（后台也要持续同步、照常听到声音）。
let windowFocused = true;

let pushTimer = null;
let retryTimer = null;
let retryCount = 0;

function buildCookieString(cookies) {
  const seen = new Set();
  const items = [];
  for (const c of cookies) {
    if (!c.domain.includes("bilibili")) continue;
    if (!KEEP_NAMES.has(c.name) || !c.value || seen.has(c.name)) continue;
    seen.add(c.name);
    items.push(`${c.name}=${c.value}`);
  }
  return items.join("; ");
}

// 推送失败后 4 秒快速重试（最多约 80 秒），覆盖插件/面板重启的空窗期
function scheduleRetry() {
  clearTimeout(retryTimer);
  if (retryCount > 20) return;
  retryCount += 1;
  retryTimer = setTimeout(() => { pushCookie(); pushTab(); pushDom(); pushShot(); }, 4000);
}

async function postJson(path, body) {
  try {
    const resp = await fetch(`${PANEL_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp && resp.ok) retryCount = 0; // 任一路径成功即视为链路恢复
    return resp;
  } catch (e) {
    // 插件未运行/本地面板未启动：静默，快速重试兜底
    scheduleRetry();
    return null;
  }
}

async function pushCookie() {
  try {
    const cookies = await chrome.cookies.getAll({ domain: "bilibili.com" });
    const cookie = buildCookieString(cookies);
    await postJson("/api/extension-cookie", {
      cookie, at: Date.now(), source: EXT_SOURCE,
    });
  } catch (e) { scheduleRetry(); }
}

// 在 B站页面内执行的 DOM 读取（executeScript 序列化执行，无外部依赖）
function readBiliDom() {
  const out = { kind: null, title: "", uname: "", online: "", area: "", parent_area: "", progress: 0, duration: 0, danmaku: [] };
  const isLive = location.hostname.indexOf("live.bilibili.com") === 0 ||
    (/^\/(\d+)/.test(location.pathname) && location.hostname.indexOf("bilibili.com") >= 0);
  const grab = (selectors) => {
    for (const s of selectors) {
      const el = document.querySelector(s);
      if (el && el.textContent) {
        const t = el.textContent.trim();
        if (t) return t;
      }
    }
    return "";
  };
  if (isLive) {
    out.kind = "live";
    out.title = grab([".live-name", ".room-title", ".Title-1", ".room-info-title"]) ||
      document.title.replace(/\s*-\s*哔哩哔哩直播\s*$/, "");
    out.online = grab([".online-detail", ".online-num", ".web-room-online"]) || "";
    out.uname = grab([".uname", ".anchor-name", ".room-owner-username", ".name"]) || "";
    out.parent_area = grab([".parent-area-name", ".area-link:first-child"]) || "";
    out.area = grab([".area-name"]) || "";
    const items = document.querySelectorAll(
      ".chat-history-panel .chat-item, .chat-script-panel .chat-item, .chat-history-panel .dm-item, .chat-history-panel .chat-bar-item"
    );
    const dms = [];
    for (const el of Array.from(items)) {
      const user = (el.querySelector(".chat-uname, .dm-user, .chat-name") || {}).textContent || "";
      const text = (el.querySelector(".chat-content, .dm-text, .chat-msg") || el).textContent || "";
      const u = user.trim(), t = text.trim();
      if (t) dms.push({ user: u, text: t });
    }
    out.danmaku = dms.slice(-20);
  } else if (/\/video\/(BV|av)/.test(location.pathname)) {
    out.kind = "video";
    out.title = grab([".video-info-title", "h1"]) || document.title.replace(/\s*_\s*哔哩哔哩_bilibili\s*$/, "");
    out.uname = grab([".up-name", ".name", ".video-owner-name"]) || "";
    const v = document.querySelector("video");
    if (v) { out.progress = v.currentTime || 0; out.duration = v.duration || 0; }
    const items = document.querySelectorAll(".bui-danmaku-item, .bui-danmaku-text, .danmaku-item");
    const dms = [];
    for (const el of Array.from(items)) {
      const t = (el.textContent || "").trim();
      if (t) dms.push(t);
    }
    out.danmaku = dms.slice(-20);
  }
  return out;
}

async function pushDom() {
  try {
    const tabs = await chrome.tabs.query({ active: true }).catch(() => []);
    const tab = tabs && tabs[0];
    if (!tab || !tab.id || !tab.url) return;
    if (tab.url.indexOf("bilibili.com") < 0) return;
    const res = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: readBiliDom,
    });
    const data = res && res[0] && res[0].result;
    if (!data || !data.kind) return;
    await postJson("/api/extension-dom", {
      url: tab.url,
      kind: data.kind,
      title: data.title,
      uname: data.uname,
      online: data.online,
      area: data.area,
      parent_area: data.parent_area,
      progress: data.progress,
      duration: data.duration,
      danmaku: data.danmaku,
      source: EXT_SOURCE,
      at: Date.now(),
    });
  } catch (e) { /* 页面不可注入/无权限：静默，靠 API 兜底 */ }
}

async function pushShot() {
  try {
    const tabs = await chrome.tabs.query({ active: true }).catch(() => []);
    const tab = tabs && tabs[0];
    if (!tab || !tab.id || !tab.url) return;
    if (tab.url.startsWith("chrome://") || tab.url.startsWith("edge://") || tab.url.startsWith("chrome-extension://")) return;
    // captureVisibleTab 截当前活动标签页可见画面（JPEG 压缩，仅本地推送）
    const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: "jpeg", quality: 45 });
    if (!dataUrl || dataUrl.length < 1000) return;
    await postJson("/api/extension-shot", {
      image: dataUrl,
      url: tab.url,
      title: tab.title || "",
      source: EXT_SOURCE,
      at: Date.now(),
    });
  } catch (e) { /* 标签页不可截（后台/受保护页）：静默 */ }
}

async function pushTab() {
  try {
    const tabs = await chrome.tabs.query({ active: true }).catch(() => []);
    const tab = tabs && tabs[0];
    if (!tab || !tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("edge://")) return;
    await postJson("/api/extension-tab", {
      url: tab.url, title: tab.title || "", source: EXT_SOURCE, at: Date.now(),
    });
  } catch (e) { scheduleRetry(); }
}

function scheduleCookie() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => { pushCookie(); }, 800);
}

async function pushAudioState(extra) {
  try {
    const body = { enabled: Boolean(audioEnabled), at: Date.now() };
    if (extra) {
      if (extra.ctx_state) body.ctx_state = extra.ctx_state;
      if (extra.ctx_err) body.ctx_err = extra.ctx_err;
    }
    await postJson("/api/extension-audio-state", body);
  } catch (e) { /* 插件未就绪：下一轮心跳重试 */ }
}

function scheduleTab() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    pushTab(); pushDom(); pushShot();
    // 标签切换/URL 变化：音频捕获需重绑到当前标签（tabCapture 一次只捕获一个标签）
    if (audioEnabled && !audioStarting) startAudioCapture();
  }, 800);
}

chrome.cookies.onChanged.addListener((changeInfo) => {
  const c = changeInfo && changeInfo.cookie;
  if (c && c.domain && c.domain.includes("bilibili")) scheduleCookie();
});

// 标签页切换 / URL 变化：防抖后推送当前活动标签页与页面实况
chrome.tabs.onActivated.addListener(() => scheduleTab());
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.url || changeInfo.title) scheduleTab();
});

chrome.alarms.create("bili-bridge-push", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "bili-bridge-push") { pushCookie(); pushTab(); pushDom(); pushShot(); pushAudioState(); checkCommands(); }
  ensureAudioSession();
});

// v0.2.20：插件指令轮询——插件写入的"进入直播间"等命令在此拉取并执行（打开标签页驻场）
async function checkCommands() {
  try {
    const resp = await fetch(`${PANEL_URL}/api/pending-command`);
    if (!resp.ok) return;
    const cmd = await resp.json();
    if (!cmd || !cmd.id) return;
    if (cmd.type === "enter-live" && cmd.room) {
      // v0.2.23：指令可指定浏览器执行；非目标浏览器跳过（不执行不 ack，留给目标扩展）
      const want = (cmd.browser || "").toLowerCase();
      const mine = EXT_SOURCE.includes("-edge-") ? "edge" : EXT_SOURCE.includes("-360-") ? "360" : "chrome";
      if (want && want !== mine) return;
      try {
        const url = `https://live.bilibili.com/${cmd.room}`;
        const all = await chrome.tabs.query({});
        const has = all.some(t => t.url && t.url.startsWith(`https://live.bilibili.com/${cmd.room}`));
        if (!has) {
          const t = await chrome.tabs.create({ url, active: false });
          if (t && t.id) { try { await chrome.tabs.update(t.id, { active: false }); } catch (e) {} }
        }
      } catch (e) {}
    }
    await fetch(`${PANEL_URL}/api/pending-command`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ack: cmd.id }),
    }).catch(() => {});
  } catch (e) {}
}

chrome.runtime.onInstalled.addListener(() => {
  pushCookie();
  pushTab();
  pushDom();
  pushShot();
  pushAudioState();
});

// ─────────── 声音捕获（v0.2.5，tabCapture + offscreen） ───────────

let audioEnabled = false;
let audioStarting = false;
let lastOffscreenBeat = 0;

// SW 每次唤醒后从 storage 恢复声音开关记忆（v0.2.14：休眠重启后开关状态不丢）
chrome.storage.local.get({ [AUDIO_STATE_KEY]: false }, (r) => {
  audioEnabled = Boolean(r[AUDIO_STATE_KEY]);
  if (audioEnabled) ensureAudioSession();
});

async function ensureOffscreenDoc() {
  // 内核过旧（Chromium < 109）无 offscreen API 时声音能力不可用，静默跳过不报错
  if (typeof chrome.offscreen === "undefined") {
    audioEnabled = false;
    await chrome.storage.local.set({ [AUDIO_STATE_KEY]: false });
    throw new Error("offscreen-unsupported");
  }
  const existing = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (existing && existing.length > 0) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
    justification: "捕获 B站 直播/视频标签页声音并回放给用户，同时推送本机插件让 YUI 听到（仅 127.0.0.1 回环）",
  });
}

async function startAudioCapture() {
  if (audioStarting) return;
  audioStarting = true;
  try {
    await ensureOffscreenDoc();
    // v0.2.25：发送带重试——offscreen 文档正在重建/销毁时 sendMessage 会报
    // "message channel closed"（15:09:53 实测），短暂等待后重新取 streamId 再发
    let started = false;
    for (let attempt = 0; attempt < 3 && !started; attempt++) {
      try {
        // 用 active:true（所有窗口）而非 lastFocusedWindow：DevTools/多窗口时仍能定位到用户看的 B站 标签
        const tabs = await chrome.tabs.query({ active: true }).catch(() => []);
        const tab = (tabs && tabs.find((t) => t.url && t.url.indexOf("bilibili.com") >= 0)) || (tabs && tabs[0]);
        if (!tab || !tab.id || !tab.url || tab.url.indexOf("bilibili.com") < 0) {
          // 当前不在 B站 页：先记住开关意图（v0.2.8 修复"一直关闭"——
          // 旧版这里 throw 导致开关永远开不起来），等切到 B站 标签后由
          // 标签监听/下一轮 alarm 自动启动捕获。
          audioEnabled = true;
          await chrome.storage.local.set({ [AUDIO_STATE_KEY]: true });
          pushAudioState();
          return;
        }
        const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
        // v0.2.19：回放只在页面被 tabCapture 静音时才启用（否则页面原声 + 回放 = 双音乐/回声）
        const muted = !!(tab.mutedInfo && tab.mutedInfo.muted);
        await chrome.runtime.sendMessage({ type: "start-audio", streamId: streamId, muted: muted });
        started = true;
      } catch (e) {
        if (attempt < 2) await new Promise((r) => setTimeout(r, 1000));
        else throw e;
      }
    }
    audioEnabled = true;
    await chrome.storage.local.set({ [AUDIO_STATE_KEY]: true });
    pushAudioState();
  } catch (e) {
    // v0.2.14 诊断：捕获启动链路任何一步失败都上报插件日志（此前静默吞掉）
    try { pushAudioState({ ctx_err: "startAudioCapture:" + String((e && e.message) || e) }); } catch (_) {}
  } finally {
    audioStarting = false;
  }
}

async function stopAudioCapture() {
  try {
    await chrome.runtime.sendMessage({ type: "stop-audio" });
  } catch (e) { /* offscreen 不存在时静默 */ }
  const existing = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] }).catch(() => []);
  if (existing && existing.length > 0) {
    await chrome.offscreen.closeDocument().catch(() => {});
  }
  audioEnabled = false;
  await chrome.storage.local.set({ [AUDIO_STATE_KEY]: false });
  pushAudioState();
}

async function ensureAudioSession() {
  if (!audioEnabled || audioStarting) return;
  // SW 被唤醒（alarm/消息）时若 offscreen 已随 SW 关闭则重开；捕获持续到标签页切换。
  // v0.2.14 增补：offscreen 心跳过期（30s 无上报）视为死文档，先关再重建——
  // 避免 DevTools 检查视图等场景留下僵尸 offscreen 吞掉 start-audio 消息导致 0 推送。
  // v0.2.25：closeDocument 后等待旧文档真正销毁（800ms），否则 start-audio 会发到
  // 正在关闭的通道报 "message channel closed"，采集起不来。
  try {
    const existing = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] }).catch(() => []);
    const alive = existing && existing.length > 0;
    const beatStale = Date.now() - lastOffscreenBeat > 30000;
    if (!alive || beatStale) {
      if (alive) {
        chrome.offscreen.closeDocument().catch(() => {});
        await new Promise((r) => setTimeout(r, 800));
      }
      startAudioCapture();
    }
  } catch (e) {
    startAudioCapture();
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // v0.2.17：content script 采集的直播弹幕 → 转发本机插件（弹幕实时可见 + @检测）
  if (msg && msg.type === "danmaku-item") {
    postJson("/api/extension-danmaku", {
      room: String(msg.room || ""),
      sender: String(msg.sender || ""),
      text: String(msg.text || ""),
      at: Number(msg.at || 0),
    }).catch(() => {});
    return false;
  }
  // offscreen 上报采集状态（含 AudioContext 状态/错误诊断，v0.2.14）
  if (msg && msg.type === "audio-state") {
    if (msg.running) lastOffscreenBeat = Date.now();
    pushAudioState(msg);
    return false;
  }
  if (msg && msg.type === "start-audio-request") {
    startAudioCapture().then(() => sendResponse({ ok: true, enabled: audioEnabled }));
    return true;
  }
  if (msg && msg.type === "stop-audio-request") {
    stopAudioCapture().then(() => sendResponse({ ok: true, enabled: false }));
    return true;
  }
  if (msg && msg.type === "audio-state-query") {
    sendResponse({ enabled: audioEnabled });
    return false;
  }
  return undefined;
});

// 快捷键命令（v0.2.16）：Ctrl+Shift+Y 授权并开启声音采集。
// Chrome MV3 的 tabCapture 要求扩展先被用户手势"invoke"一次（点击 action 或触发命令），
// 之后 alarm 自动采集才被授权；用户按一次快捷键即完成授权并立即启动采集。
// 关闭声音请使用扩展弹窗（popup）里的开关，快捷键只负责开启。
chrome.commands.onCommand.addListener((command) => {
  if (command === "toggle-audio") {
    startAudioCapture();
  }
});

// 启动时恢复声音捕获开关（v0.2.8 永久开启：从未设置过=开，用户显式关闭过才保持关）
chrome.storage.local.get(AUDIO_STATE_KEY).then((saved) => {
  audioEnabled = saved[AUDIO_STATE_KEY] !== false;
  if (audioEnabled) startAudioCapture();
});

// 浏览器/扩展启动时立即推送一次
pushCookie();
pushTab();
pushDom();
pushShot();
pushAudioState();

// 窗口焦点监听（v0.2.9）：失焦即停推页面实况，多浏览器共存时只让用户
// 正在看的那一个说话；cookie / 声音不受影响。
chrome.windows.onFocusChanged.addListener((windowId) => {
  windowFocused = windowId !== chrome.windows.WINDOW_ID_NONE;
});
// 启动时校准一次真实焦点（SW 后台唤醒默认 true，这里纠正）
try {
  chrome.windows.getLastFocused({}, (win) => {
    if (chrome.runtime.lastError) return; // 无焦点窗口：保持当前值，不报 Unchecked runtime.lastError
    if (win) windowFocused = win.focused !== false;
  });
} catch (e) { /* 内核过旧无此 API：保持默认 true */ }

// v0.2.22：标签切换/地址变化时重建声音捕获——tabCapture 流会随标签失活断开，
// 旧版只靠 30s alarm 且 offscreen 未死时不重捕，切回直播标签后一直"听不到声音"。
chrome.tabs.onActivated.addListener(async () => {
  if (!audioEnabled) return;
  const tabs = await chrome.tabs.query({ active: true }).catch(() => []);
  const tab = (tabs.find((t) => t.url && t.url.indexOf("bilibili.com") >= 0)) || (tabs.length ? tabs[0] : null);
  if (!tab || !tab.url || tab.url.indexOf("bilibili.com") < 0) return;
  // v0.2.25：标签切换直接重捕（startCapture 内部会干净地停旧流再捕新标签），
  // 不再置零 lastOffscreenBeat/强关文档——旧逻辑会误判正常采集为僵尸，
  // 触发 offscreen 重建竞态导致 "message channel closed" 采集中断。
  if (!audioStarting) startAudioCapture();
});
chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
  if (!audioEnabled) return;
  if (changeInfo.url && changeInfo.url.indexOf("bilibili.com") >= 0) {
    if (!audioStarting) startAudioCapture();
  }
});