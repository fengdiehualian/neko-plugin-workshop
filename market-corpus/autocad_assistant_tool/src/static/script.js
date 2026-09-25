// CAD辅助绘图插件 — 静态面板逻辑
// Hosted UI 内部调用必须走 /runs 异步 API（发起→轮询→取结果）
const PLUGIN_ID = "autocad_assistant_tool";
const RUNS_URL = "/runs";
const POLL_INTERVAL = 300;
const POLL_TIMEOUT = 60000;

const $ = (id) => document.getElementById(id);

// --- 核心: 通过 /runs 调用插件入口点 ---
async function callPlugin(entryId, args = {}) {
  const resp = await fetch(RUNS_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plugin_id: PLUGIN_ID, entry_id: entryId, args }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const { run_id, id } = await resp.json();
  const runId = run_id || id;
  if (!runId) throw new Error("未获取到 run_id");

  // 轮询状态直到完成
  const deadline = Date.now() + POLL_TIMEOUT;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL));
    const poll = await fetch(`${RUNS_URL}/${runId}`);
    if (!poll.ok) continue;
    const rec = await poll.json();
    if (rec.status === "succeeded") {
      // 取导出结果
      const exp = await fetch(`${RUNS_URL}/${runId}/export`);
      if (!exp.ok) return {};
      const exportData = await exp.json();
      const items = exportData.items || [];
      const resultItem = items.find((i) => i.type === "json" && i.json) || items[0];
      if (!resultItem) return {};
      const pluginResponse = resultItem.json || {};
      // 解包 N.E.K.O SDK 包装格式: {success: true, data: {...}} 或 {success: false, error: "..."}
      if (pluginResponse.success === false || pluginResponse.error) {
        const errMsg = pluginResponse.error?.message || pluginResponse.error || "插件执行失败";
        throw new Error(typeof errMsg === "string" ? errMsg : JSON.stringify(errMsg));
      }
      return pluginResponse.data || pluginResponse;
    }
    if (["failed", "canceled", "timeout"].includes(rec.status)) {
      const errMsg = rec.error?.message || rec.message || rec.status;
      throw new Error(errMsg);
    }
  }
  throw new Error("调用超时");
}

// --- UI 辅助 ---
function setPill(connected) {
  const p = $("conn-pill");
  p.textContent = connected ? "已连接" : "未连接";
  p.className = "pill " + (connected ? "on" : "off");
}

function renderStatus(data) {
  const out = $("status");
  if (!data) { out.textContent = "（无响应）"; return; }
  if (data.error) { out.textContent = "错误: " + data.error; setPill(false); return; }
  if (data.connected) {
    setPill(true);
    out.textContent =
      "已连接 CAD\n实体数量: " + (data.entity_count ?? 0) +
      (data.layers ? "\n图层: " + data.layers.map(l => l.name).join(", ") : "");
  } else {
    setPill(false);
    out.textContent = data.message || "未连接 CAD，请先调用 connect。";
  }
}

// --- 按钮绑定 ---
$("btn-connect").onclick = async () => {
  $("status").textContent = "正在连接CAD（最多等待20秒）...";
  $("btn-connect").disabled = true;
  try {
    const r = await callPlugin("connect", { allow_launch: true });
    renderStatus(r);
  } catch (e) {
    $("status").textContent = "连接失败: " + e.message;
    setPill(false);
  } finally {
    $("btn-connect").disabled = false;
  }
};

$("btn-status").onclick = async () => {
  try { renderStatus(await callPlugin("get_status", {})); }
  catch (e) { $("status").textContent = "请求失败: " + e.message; }
};

$("btn-caps").onclick = async () => {
  const card = $("caps-card");
  const body = $("caps-body");
  body.innerHTML = "";
  try {
    const r = await callPlugin("get_capabilities", {});
    const cmds = r.commands || {};
    Object.keys(cmds).sort().forEach((k) => {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><code>" + k + "</code></td><td>" + cmds[k] + "</td>";
      body.appendChild(tr);
    });
    card.style.display = "block";
  } catch (e) {
    $("status").textContent = "请求失败: " + e.message;
  }
};

// 初始加载状态
window.addEventListener("load", () => {
  try { $("btn-status").click(); } catch (e) { /* 面板首次打开可能还没就绪 */ }
});
