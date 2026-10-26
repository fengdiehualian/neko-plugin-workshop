import { observationView, sparkline } from "./observer-data.mjs";

const byId = (id) => document.getElementById(id);
const text = (id, value) => {
    byId(id).textContent = value;
};
const number = (value) =>
    typeof value === "number" && Number.isFinite(value)
        ? value.toLocaleString("zh-CN")
        : "—";
const node = (tag, content, className) => {
    const element = document.createElement(tag);
    element.textContent = content;
    if (className) element.className = className;
    return element;
};
let savedState = null,
    configuredUrl = "",
    activeUrl = "",
    failedRead = false;
let timer,
    clockTimer,
    request,
    frameTimer,
    closed = false;
let onScreen = true;
const visible = () => !closed && !document.hidden && onScreen;

function freshness() {
    const view = observationView(savedState || {});
    const fresh = view.fresh && !failedRead;
    const status = byId("sample-status");
    status.textContent = fresh ? "近期观测" : "观测待更新";
    status.className = `badge ${fresh ? "good" : "stale"}`;
    text(
        "sample-time",
        Number.isFinite(view.timestamp)
            ? `样本时间 ${new Date(view.timestamp).toLocaleString("zh-CN", { hour12: false })}`
            : "尚无样本",
    );
    const recorded =
        view.runtime.game_connected === true
            ? "游戏已连接"
            : view.runtime.game_connected === false
              ? "游戏未连接"
              : "连接状态未知";
    text("connection", `${fresh ? "最近观测" : "历史记录"}：${recorded}`);
    byId("connection").className = `connection${fresh ? "" : " muted"}`;
}

function unloadViewer() {
    clearTimeout(frameTimer);
    byId("frame-slot").replaceChildren();
    byId("frame-slot").hidden = true;
    byId("viewer-empty").hidden = false;
    byId("unload-viewer").hidden = true;
    activeUrl = "";
}

function viewer(view) {
    if (configuredUrl !== view.viewerUrl) {
        unloadViewer();
        configuredUrl = view.viewerUrl;
    }
    byId("load-viewer").disabled = !configuredUrl;
    const link = byId("open-viewer");
    link.hidden = !configuredUrl;
    if (configuredUrl) link.href = configuredUrl;
    else link.removeAttribute("href");
    text(
        "viewer-empty-title",
        configuredUrl ? "观战页面已就绪" : "尚未设置现代观战地址",
    );
    text(
        "viewer-empty-detail",
        configuredUrl
            ? "点击后连接你配置的可视化页面。请核对账号、位置与右侧最新观测是否一致。"
            : "在「配置与控制」中填写与当前角色配套的可视化页面。右侧与下方可直接查看框架的实际观测。",
    );
    if (!activeUrl) {
        text("viewer-badge", configuredUrl ? "等待载入" : "未配置");
        text(
            "viewer-note",
            configuredUrl
                ? "已配置观战地址；画面与当前角色的绑定尚未核验。"
                : "尚无画面连接，未核验角色绑定。",
        );
    }
}

byId("load-viewer").addEventListener("click", () => {
    if (!configuredUrl || activeUrl) return;
    activeUrl = configuredUrl;
    const frame = document.createElement("iframe");
    frame.title = "Minecraft 现代观战页面";
    frame.referrerPolicy = "no-referrer";
    frame.setAttribute(
        "sandbox",
        "allow-scripts allow-same-origin allow-pointer-lock",
    );
    frame.setAttribute("allow", "fullscreen");
    frame.src = activeUrl;
    frame.addEventListener("load", () => {
        if (!frame.isConnected) return;
        text("viewer-badge", "已请求载入");
        text(
            "viewer-note",
            "请核对画面中的账号与位置；页面载入不代表角色绑定已验证。空白时可单独打开。",
        );
    });
    byId("frame-slot").replaceChildren(frame);
    byId("frame-slot").hidden = false;
    byId("viewer-empty").hidden = true;
    byId("unload-viewer").hidden = false;
    text("viewer-badge", "正在载入");
    text("viewer-note", "正在打开配置的观战页面，尚未核验角色绑定。");
    frameTimer = setTimeout(() => {
        if (frame.isConnected)
            text(
                "viewer-note",
                "若画面空白，请单独打开并检查观战服务是否允许嵌入；当前角色绑定仍需核对。",
            );
    }, 15000);
});
byId("unload-viewer").addEventListener("click", () => {
    unloadViewer();
    viewer(observationView(savedState || {}));
});

function records(id, rows, heading, detail, empty) {
    const container = byId(id);
    container.replaceChildren();
    if (!rows.length) {
        container.append(node("p", empty, "muted"));
        return;
    }
    for (const row of rows) {
        const item = node("div", "", "record");
        const head = node("div", "", "record-head");
        const [title, badge] = heading(row);
        head.append(
            node("strong", String(title || "未记录")),
            node("span", String(badge || "未知"), "badge"),
        );
        item.append(head);
        if (detail) item.append(node("p", detail(row)));
        container.append(item);
    }
}

function render(state) {
    const view = observationView(state);
    const runtime = view.runtime;
    byId("runtime-error").hidden = runtime.phase !== "failed";
    text(
        "runtime-mode",
        state.settings?.runtime?.mode === "external"
            ? "独立框架 · Neko 观察入口"
            : "Neko 嵌入式框架",
    );
    for (const [id, value] of [
        ["health", view.health],
        ["food", view.food],
    ]) {
        text(id, value === null ? "尚无数据" : `${value} / 20`);
        byId(`${id}-meter`).hidden = value === null;
        if (value !== null) byId(`${id}-meter`).value = value;
    }
    text("place", view.place);
    text(
        "goal",
        runtime.current_goal?.goal || runtime.standing_goal || "尚无目标",
    );
    text("turns", number(runtime.decisions));
    text(
        "pending-count",
        Array.isArray(runtime.pending) ? String(view.pending.length) : "—",
    );
    text("failed-count", number(state.audit?.sample_failed_receipts));
    text(
        "token-count",
        state.audit?.usage_recorded
            ? number(state.audit.sample_tokens)
            : "未记录",
    );
    text("phase", runtime.phase || "尚无状态");
    records(
        "queue",
        view.pending,
        (row) => [row.id, row.phase],
        (row) => row.kind || "",
        "没有记录中的待结算动作",
    );
    records(
        "receipts",
        [...view.receipts].reverse(),
        (row) => [
            row.tool,
            row.failed ? "失败回执" : row.execution?.phase || "已返回",
        ],
        (row) => row.text || "无文本回执",
        "尚无原生回执",
    );
    const samples = view.decisions
        .map((row) => row.latency_ms)
        .filter(
            (value) =>
                typeof value === "number" &&
                Number.isFinite(value) &&
                value >= 0,
        );
    byId("latency-line").setAttribute("points", sparkline(samples));
    byId("chart-empty").hidden = samples.length > 0;
    text(
        "latest-latency",
        samples.length ? `${number(samples.at(-1))} ms` : "—",
    );
    text(
        "latency-caption",
        samples.length
            ? `${samples.length} 条实际耗时样本 · 峰值 ${number(Math.max(...samples))} ms`
            : "仅展示有实际耗时的样本；不补零。",
    );
    byId("decisions").replaceChildren();
    byId("decisions-empty").hidden = view.decisions.length > 0;
    for (const row of [...view.decisions].reverse()) {
        const tr = document.createElement("tr");
        for (const value of [
            row.route || "未记录",
            row.status || "未记录",
            row.tools || "—",
            typeof row.latency_ms === "number"
                ? `${number(row.latency_ms)} ms`
                : "—",
            row.reply || "—",
        ])
            tr.append(node("td", value));
        byId("decisions").append(tr);
    }
    byId("facts").replaceChildren();
    for (const row of view.facts) {
        const fact = node("article", "", "fact");
        fact.append(
            node("h3", row.key || "观测"),
            node("p", row.text || "无文本"),
        );
        if (row.has_more)
            fact.append(
                node("small", "长内容已截取，完整原文保留在框架中。", "muted"),
            );
        byId("facts").append(fact);
    }
    if (!view.facts.length)
        byId("facts").append(node("p", "等待框架观测", "muted"));
    viewer(view);
    freshness();
}

async function refresh() {
    if (request || !visible()) return;
    clearTimeout(timer);
    request = new AbortController();
    const timeout = setTimeout(() => request?.abort(), 15000);
    byId("refresh").disabled = true;
    try {
        const response = await fetch(
            "../hosted-ui/context?kind=panel&id=viewer",
            {
                signal: request.signal,
                credentials: "same-origin",
                cache: "no-store",
            },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const context = await response.json();
        if (
            !context.state ||
            typeof context.state !== "object" ||
            context.state.error ||
            !context.state.runtime
        )
            throw new Error("observation_unavailable");
        savedState = context.state;
        failedRead = false;
        byId("read-error").hidden = true;
        render(savedState);
    } catch {
        failedRead = true;
        byId("read-error").hidden = false;
        text(
            "read-error",
            "未能读取最新观测。下面保留上次样本；请检查插件状态，或稍后刷新。",
        );
        freshness();
    } finally {
        clearTimeout(timeout);
        request = null;
        byId("refresh").disabled = false;
        if (visible()) timer = setTimeout(refresh, 5000);
    }
}
byId("refresh").addEventListener("click", refresh);
function resume() {
    if (!visible()) return;
    freshness();
    clearInterval(clockTimer);
    clockTimer = setInterval(freshness, 1000);
    refresh();
}
document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    clearInterval(clockTimer);
    if (!document.hidden) resume();
});
// Browser visibility alone does not cover a Neko panel hidden by its parent tab.
const intersection = new IntersectionObserver((entries) => {
    const next = entries.some((entry) => entry.isIntersecting);
    if (next === onScreen) return;
    onScreen = next;
    clearTimeout(timer);
    clearInterval(clockTimer);
    if (next) resume();
});
intersection.observe(document.documentElement);
window.addEventListener("pagehide", () => {
    closed = true;
    clearTimeout(timer);
    clearInterval(clockTimer);
    clearTimeout(frameTimer);
    request?.abort();
});
window.addEventListener("pageshow", () => {
    if (closed) {
        closed = false;
        resume();
    }
});
resume();
