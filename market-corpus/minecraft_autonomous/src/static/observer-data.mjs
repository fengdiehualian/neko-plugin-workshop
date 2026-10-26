// Presentation helpers only: no network, model calls or game commands.
export function safeViewerUrl(value) {
    if (
        typeof value !== "string" ||
        !/^https?:\/\//i.test(value) ||
        /[\s\\]/.test(value)
    )
        return "";
    try {
        const url = new URL(value);
        if (
            !["http:", "https:"].includes(url.protocol) ||
            !url.hostname ||
            url.username ||
            url.password ||
            url.search ||
            url.hash
        )
            return "";
        return url.href;
    } catch {
        return "";
    }
}

const rows = (value) =>
    Array.isArray(value)
        ? value.filter((row) => row && typeof row === "object")
        : [];

export function observationView(state = {}, now = Date.now()) {
    const runtime = state.runtime || {};
    const facts = rows(runtime.facts).slice(0, 24);
    const body = facts.find((row) => row.key === "body")?.text || "";
    const vital = (label) => {
        const match = String(body).match(
            new RegExp(
                `${label}\\s*[:：]?\\s*(\\d+(?:\\.\\d+)?)\\s*[/／]\\s*20`,
            ),
        );
        return match ? Math.min(20, Number(match[1])) : null;
    };
    const timestamp =
        typeof runtime.updated_at === "string"
            ? Date.parse(runtime.updated_at)
            : NaN;
    const age = now - timestamp;
    return {
        runtime,
        facts,
        timestamp,
        fresh:
            !state.error &&
            Number.isFinite(age) &&
            age >= -30000 &&
            age <= 30000,
        health: vital("生命"),
        food: vital("饥饿"),
        place: String(
            facts.find((row) => row.key === "place")?.text || "尚无位置观测",
        ),
        pending: rows(runtime.pending).slice(0, 16),
        decisions: rows(state.audit?.decisions).slice(-20),
        receipts: rows(state.audit?.receipts).slice(-20),
        viewerUrl: safeViewerUrl(state.viewer_url),
    };
}

export function sparkline(values) {
    const samples = values.filter(
        (value) =>
            typeof value === "number" && Number.isFinite(value) && value >= 0,
    );
    if (!samples.length) return "";
    const max = Math.max(1, ...samples);
    return samples
        .map(
            (value, i) =>
                `${samples.length === 1 ? 130 : Math.round((i * 260) / (samples.length - 1))},${Math.round(56 - (value / max) * 56)}`,
        )
        .join(" ");
}
