// popup 状态展示 + 声音捕获开关（v0.2.5）
const PANEL_URL = "http://127.0.0.1:8765";

function fmtUname(s) {
  return s || "—";
}

async function refresh() {
  const st = document.getElementById("st");
  const at = document.getElementById("at");
  const tab = document.getElementById("tab");
  const au = document.getElementById("au");
  const btn = document.getElementById("btn");
  try {
    const r = await fetch(`${PANEL_URL}/api/extension-status`);
    const s = await r.json();
    if (s.online) {
      st.textContent = "运行中（已连插件）";
      st.className = "ok";
      const d = s.last_seen ? new Date(s.last_seen).toLocaleTimeString() : "刚刚";
      at.textContent = d;
      if (s.current_tab && s.current_tab.url) {
        const t = s.current_tab.title || s.current_tab.url;
        tab.textContent = t.length > 26 ? t.slice(0, 25) + "…" : t;
        tab.title = s.current_tab.url;
      } else { tab.textContent = "—"; }
    } else {
      st.textContent = "插件未运行或未收到推送";
      st.className = "bad";
      at.textContent = s.last_seen ? new Date(s.last_seen).toLocaleTimeString() : "—";
    }
  } catch (e) {
    st.textContent = "插件面板未运行";
    st.className = "bad";
    at.textContent = "—";
  }
  // 声音状态（查询 background；失败时读本地 storage 兜底）
  let enabled = false;
  try {
    const resp = await chrome.runtime.sendMessage({ type: "audio-state-query" });
    enabled = Boolean(resp && resp.enabled);
  } catch (e) {
    const saved = await chrome.storage.local.get("audio_capture_enabled");
    enabled = saved.audio_capture_enabled !== false;  // 永久开启：未设置过=开
  }
  au.textContent = enabled ? "开启（后台静默）" : "关闭";
  au.className = enabled ? "ok" : "bad";
  btn.textContent = enabled ? "关闭声音" : "开启声音（让 YUI 听到直播/视频）";
  btn.classList.toggle("off", enabled);
}

document.getElementById("btn").addEventListener("click", async () => {
  const btn = document.getElementById("btn");
  btn.disabled = true;
  try {
    const resp = await chrome.runtime.sendMessage({
      type: document.getElementById("au").className === "ok" ? "stop-audio-request" : "start-audio-request",
    });
    if (resp && resp.ok) {
      // 稍等 background 完成 offscreen 初始化后刷新状态
      setTimeout(refresh, 800);
    }
  } catch (e) {
    btn.textContent = "操作失败，请重试";
  }
  btn.disabled = false;
});

refresh();
