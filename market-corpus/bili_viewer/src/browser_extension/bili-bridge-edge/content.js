// v0.2.17：B站 直播弹幕采集（DOM），实时转发给后台 → 本机插件。
// 解决 YUI"看不见弹幕、被@不知道"：弹幕区文本实时推送，插件缓冲 + @检测。
(function () {
  if (window.__biliDanmakuBridge) return;
  window.__biliDanmakuBridge = true;

  const seen = new Map(); // key: sender+text -> ts，2s 内去重

  function pickRoom() {
    const m = location.pathname.match(/^\/(\d+)$/);
    return m ? m[1] : "";
  }

  function pushItem(sender, text) {
    const room = pickRoom();
    if (!text) return;
    // 兜底：部分 UI 把发送者与内容合并在一个文本节点（"昵称 : 内容"）
    if (!sender) {
      const m = text.match(/^([^\s:：]{1,16})\s*[:：]\s*(.+)$/);
      if (m) { sender = m[1]; text = m[2]; }
    }
    const now = Date.now();
    const key = sender + ":" + text;
    const last = seen.get(key) || 0;
    if (now - last < 2000) return;
    seen.set(key, now);
    if (seen.size > 300) {
      const it = seen.keys().next().value;
      seen.delete(it);
    }
    try {
      chrome.runtime.sendMessage({
        type: "danmaku-item",
        room: room,
        sender: sender,
        text: text,
        at: now,
      });
    } catch (e) { /* 扩展重载瞬间忽略 */ }
  }

  function scan() {
    // 选择器与 background.js readBiliDom 已验证一致（ext dom dms=N 有效）
    const nodes = document.querySelectorAll(
      ".chat-history-panel .chat-item, .chat-script-panel .chat-item, .chat-history-panel .dm-item, .chat-history-panel .chat-bar-item"
    );
    for (const n of nodes) {
      const nameEl = n.querySelector(".chat-uname, .dm-user, .chat-name");
      const textEl = n.querySelector(".chat-content, .dm-text, .chat-msg");
      const text = ((textEl || n).textContent || "").trim();
      const sender = (nameEl ? nameEl.textContent : "").trim() || "";
      if (text) pushItem(sender, text);
    }
  }

  let timer = null;
  function start() {
    scan();
    if (timer) return;
    timer = setInterval(scan, 1500);
    try {
      const obs = new MutationObserver(function () { scan(); });
      const root = document.querySelector(
        ".chat-history-panel, .chat-script-panel, .chat-history-list, .chat-danmaku-wrap"
      );
      obs.observe(root || document.body, { childList: true, subtree: true });
      window.__biliDanmakuObs = obs;
    } catch (e) { /* 不支持则仅轮询 */ }
  }
  start();
})();
