import { deflateSync } from "node:zlib"

const TODAY = "2026-10-02"
const SAMPLE = "\u6d4b\u8bd5\u8bb0\u5f55\uff1a\u4eca\u5929\u7684\u76f8\u5904\u5f88\u5b89\u9759\u3002\u665a\u4e0a\u4e00\u8d77\u804a\u4e86\u4e00\u4f1a\u513f\uff0c\u8bb0\u4f4f\u4e86\u4e00\u4e9b\u5c0f\u4e8b\u3002"
const ACTIONS = [
  "toggle", "update_settings", "set_anchor", "advance_days", "reset_all",
  "get_diary", "clear_diary", "delete_diary_item", "get_journal",
  "invite_journal", "get_review", "write_review_now", "clear_review",
  "get_stats", "clear_stats", "get_panel_gallery", "get_gallery_image",
  "set_panel_appearance", "gallery_add", "gallery_remove", "gallery_set_thumb",
  "set_capability", "set_capability_flags", "get_capability_intro",
  "set_onboarding", "prune_lanlan", "lift_mood", "data_backup",
]

function crc32(bytes) {
  let value = 0xffffffff
  for (const byte of bytes) {
    value ^= byte
    for (let i = 0; i < 8; i += 1) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0)
  }
  return (value ^ 0xffffffff) >>> 0
}

function pngChunk(name, data) {
  const type = Buffer.from(name)
  const size = Buffer.alloc(4)
  size.writeUInt32BE(data.length)
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(Buffer.concat([type, data])))
  return Buffer.concat([size, type, data, crc])
}

// Deterministic test pixels only; this asset never enters the runtime plugin.
export function makeWallpaper(dark = false, variant = 0, palette = null) {
  const width = 960
  const height = 640
  const pixels = Buffer.alloc((width * 3 + 1) * height)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = y * (width * 3 + 1) + 1 + x * 3
      const ridge = 340 + Math.sin(x / 125 + variant) * 62 + Math.cos(x / 240) * 28
      const foreground = y > 465 + Math.sin(x / 160) * 27
      let color = dark ? [35, 55, 78] : [155, 207, 227]
      if (y > ridge) color = dark ? [47, 92, 98] : [81, 160, 153]
      if (foreground) color = dark ? [27, 62, 57] : [36, 119, 109]
      if ((x - 740) ** 2 + (y - 124) ** 2 < 62 ** 2) color = dark ? [232, 220, 183] : [254, 237, 161]
      if (variant > 1) color = [color[1], color[2], color[0]]
      if (palette) color = palette[y < height * 0.72 ? 0 : 1]
      pixels[offset] = color[0]
      pixels[offset + 1] = color[1]
      pixels[offset + 2] = color[2]
    }
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width)
  header.writeUInt32BE(height, 4)
  header[8] = 8
  header[9] = 2
  const png = Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", header), pngChunk("IDAT", deflateSync(pixels)), pngChunk("IEND", Buffer.alloc(0)),
  ])
  return `data:image/png;base64,${png.toString("base64")}`
}

function calendarMonth(year, month) {
  const count = new Date(Date.UTC(year, month, 0)).getUTCDate()
  const offset = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7
  const cells = Array.from({ length: offset }, () => ({ in_month: false }))
  for (let day = 1; day <= count; day += 1) {
    const phase = day <= 5 ? "menstrual" : day <= 11 ? "follicular" : day <= 17 ? "ovulatory" : "luteal"
    const date = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`
    cells.push({ day, in_month: true, phase, phase_label: phase, is_tide: day <= 5, cycle_day: day, is_today: date === TODAY, is_future: date > TODAY })
  }
  while (cells.length % 7) cells.push({ in_month: false })
  return { year, month, label: `${year} / ${String(month).padStart(2, "0")}`, cells }
}

export function makeFixture(messages, { locale = "zh-CN", wallpaper = "none", empty = false, wizard = false, intros = {}, video = null, heat = "", diaryCase = "", moodCase = "", rpcDelay = 0, legacyGallery = false } = {}) {
  const appearance = { bg_id: wallpaper === "none" ? "" : wallpaper === "video" ? "qa-video" : "qa-wallpaper", fill: "cover", position: "center", blur: 0, dim: 0.4, brightness: 100, saturate: 100, contrast: 100, glass: 0, card_alpha: 0, text_weight: 100, motion: true }
  const palettes = {
    olive: [[191, 185, 125], [125, 167, 191]],
    rose: [[201, 128, 157], [128, 167, 201]],
    gray: [[128, 128, 128], [192, 192, 192]],
    white: [[255, 255, 255], [255, 255, 255]],
  }
  const image = makeWallpaper(wallpaper === "dark", 0, palettes[wallpaper] || null)
  const images = { "qa-wallpaper": image, "qa-wallpaper-b": makeWallpaper(true, 1), "qa-wallpaper-c": makeWallpaper(false, 2) }
  const diary = empty ? [] : Array.from({ length: 12 }, (_, index) => ({
    ts: `2026-09-${String(30 - index).padStart(2, "0")}T19:30:00+08:00`,
    source: index % 3 ? "self" : "auto", kind: index % 3 ? "" : "like", mood: "warm",
    entry: SAMPLE.repeat(index % 3 + 1), quote: "\u6211\u559c\u6b22\u8fd9\u6837\u5b89\u9759\u7684\u665a\u4e0a\u3002", note: SAMPLE,
  }))
  const journal = empty || diaryCase === "archives-only" ? [] : Array.from({ length: diaryCase === "many" ? 21 : 8 }, (_, index) => ({
    page_no: index + 1, started_at: `2026-09-${String(diaryCase === "many" ? index + 1 : index * 3 + 1).padStart(2, "0")}T19:00:00+08:00`,
    last_ts: `2026-09-${String(diaryCase === "many" ? index + 2 : index * 3 + 2).padStart(2, "0")}T19:00:00+08:00`, entry_count: 1, mood_avg: 0.24,
    entries: [{ ts: "2026-09-28T19:00:00+08:00", text: `\u3010\u8fd9\u6bb5\u65f6\u95f4\u3011\n${SAMPLE.repeat(4)}\n\u3010\u6211\u5728\u60f3\u3011\n${SAMPLE.repeat(3)}\n\u3010\u5bf9\u4ed6\u7684\u611f\u89c9\u3011\n${SAMPLE.repeat(3)}` }],
  }))
  const review = empty || diaryCase === "archives-only" ? [] : Array.from({ length: diaryCase === "many" ? 17 : 6 }, (_, index) => ({
    ts: `2026-09-${String(29 - (diaryCase === "many" ? index : index * 3)).padStart(2, "0")}T20:00:00+08:00`, turns: 58, span: "2026-09-21~2026-09-29", self_action_count: 2,
    tone: { happy: 16, neutral: 8, sad: 2 }, mood_avg: 0.24, quotes: [{ kind: "like", quote: SAMPLE.slice(0, 30) }], text: SAMPLE.repeat(20),
  }))
  const journalArchive = empty ? [] : [1, 2].map(page_no => ({
    page_no, started_at: "2026-01-01", last_ts: "2026-02-28", entry_count: 1,
    entries: [{ ts: "2026-02-28T19:00:00+08:00", text: SAMPLE }],
  }))
  const reviewArchive = empty ? [] : [2, 1].map(day => ({
    ts: `2026-02-0${day}T20:00:00+08:00`, text: SAMPLE, turns: 11,
  }))
  if (diaryCase === "many") {
    journal.forEach((page, index) => {
      // Retired older diaries must not renumber the surviving identities.
      page.page_no += 52
      page.entries[0].ts = page.started_at
      page.entries[0].text += `\njournal-${index + 1}-tail`
      if (index === 0) {
        page.entries.push({ ts: page.last_ts, text: `\u3010\u60f3\u8bf4\u7684\u3011${SAMPLE}\njournal-1-continuation-tail` })
        page.entry_count = page.entries.length
      }
      if (index !== 1) page.title = [
        "\u665a\u98ce\u91cc\u7684\u5c0f\u4e8b",
        "\u4e00\u8d77\u7b49\u65e5\u843d",
        "\u628a\u90a3\u4e9b\u8fd8\u6ca1\u6765\u5f97\u53ca\u8bf4\u51fa\u53e3\u7684\u5c0f\u4e8b\u7559\u5728\u8fd9\u4e00\u9875\uff0c\u7b49\u4e0b\u4e00\u6b21\u60f3\u8d77\u4f60\u7684\u65f6\u5019\u518d\u6162\u6162\u63a5\u7740\u5199",
        "\u6709\u4f60\u7684\u666e\u901a\u4e00\u5929",
      ][index % 4]
      if (index === 10) page.title = "OnlyInTitle"
    })
    journalArchive[1].title = "\u65e9\u6625\u7684\u8bb0\u5fc6"
    review.forEach((entry, index) => {
      entry.text += `\nreview-${index + 1}-tail`
      if (index !== 1) entry.title = [
        "\u5b89\u9759\u76f8\u5904\u7684\u4e00\u5468",
        "\u5206\u4eab\u65e5\u5e38\u7684\u65f6\u523b",
        "\u5728\u90a3\u4e9b\u5e73\u5e38\u7684\u591c\u665a\u91cc\u6162\u6162\u628a\u8bdd\u8bf4\u5b8c\uff0c\u4e5f\u5b66\u7740\u8ba4\u771f\u542c\u89c1\u5bf9\u65b9\u7684\u60f3\u6cd5",
        "\u6e29\u67d4\u56de\u5e94\u7684\u65e5\u5b50",
      ][index % 4]
      if (index === 10) entry.title = "ReviewOnlyInTitle"
    })
    reviewArchive[0].title = "\u6625\u65e5\u76f8\u5904\u5c0f\u8bb0"
  }
  if (diaryCase === "legacy") {
    review.splice(0, review.length, { ts: "2026-01-01T19:00:00", text: SAMPLE })
  }
  if (diaryCase === "headings") {
    for (const items of [journal, review]) {
      items.forEach((item, index) => {
        const recorded = (item.started_at || item.ts).slice(0, 10)
        if (index === 0) item.title = `${recorded.replaceAll("-", ".")}·卡片约定小记`
        if (index === 2) item.title = recorded
        if (index === 3) item.title = "1999-12-31·跨年的约定"
        if (index === 4) item.title = "把那些还没来得及说出口的小事留在这一页，等下次想起你的时候再慢慢接着写"
        if (index === 5) item.title = `${recorded.replaceAll("-", "/")} : 一起等日落`
      })
    }
  }
  const capabilities = [
    ["whisper", "rhythm", "injection"], ["phase_openers", "rhythm", "injection"],
    ["activity_sense", "rhythm", "none"], ["anniversary", "rhythm", "injection"],
    ["birthday", "rhythm", "injection"], ["mood_engine", "mood", "tool"],
    ["tone_sense", "mood", "host_http"], ["fragments", "diary", "direct"],
    ["journal", "diary", "injection"], ["review", "diary", "direct"],
  ].map(([id, group, llm]) => ({ id, group, llm, enabled: !empty, source: empty ? "master_off" : "on", tools: intros[id]?.tools || [], depends: intros[id]?.deps || [], user_off: false }))
  const heatDays = empty ? [] : Array.from({ length: 184 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 3, 1 + index)).toISOString().slice(0, 10)
    return { date, turns: index % 5 === 0 ? 0 : index % 30 + 2, tone: "happy", valence: 0.25 }
  })
  function heatWindow(start, end) {
    const counts = [0, 2, 4, 6, 8, 10, 14, 18, 24, 32, 48, 80]
    const days = []
    for (let stamp = Date.parse(`${start}T00:00:00Z`), index = 0; stamp <= Date.parse(`${end}T00:00:00Z`); stamp += 86400000, index++) {
      const date = new Date(stamp).toISOString().slice(0, 10)
      const turns = heat === "uniform" ? 12 : heat === "outlier" && index === 80 ? 100000 : counts[(Math.imul(index + 19, 2654435761) >>> 16) % counts.length]
      days.push({ date, turns })
    }
    return { days, start, end, year: Number(start.slice(0, 4)), years: [2026, 2025, 2024], months: [] }
  }
  const heatmaps = heat ? {
    2026: heatWindow("2026-06-13", "2026-10-11"),
    2025: heatWindow("2025-01-01", "2025-12-31"),
    2024: heatWindow("2024-01-01", "2024-12-31"),
  } : null
  let selectedHeat = heatmaps && heatmaps[2026]
  if (heat === "year") selectedHeat = heatmaps[2025]
  if (heat === "leap") selectedHeat = heatmaps[2024]
  if (heat === "short") selectedHeat = heatWindow("2026-01-01", "2026-01-02")
  if (heat === "boundary") selectedHeat = heatWindow("2026-10-30", "2026-11-03")
  if (heat === "empty") selectedHeat = { days: [], start: "", end: "", years: [], year: 2026, months: [] }
  const state = {
    status: { enabled: !empty, phase: "follicular", phase_label: "\u56de\u5347\u671f", tone: "success", cycle_day: 9, day_ratio: 9 / 28, days_until_next_period: 20 },
    anchor_date: "2026-09-24", advance_days: 0, lanlan: "\u6d4b\u8bd5\u89d2\u8272",
    mood: { active: false, system_enabled: true, affect: { valence: 0.25, arousal: 0.45, arousal_baseline: 0.35 } },
    settings: { enabled: !empty, auto_derive: true, cycle_length: 28, period_length: 5, ovulation_day: 14, ovulation_window: 3, inject_mode: "interval_n", inject_interval_n: 3, phase_openers: true, timezone: "auto", mood_enabled: true, default_action_minutes: 10, emotion_sense_enabled: true, tone_min_interval_sec: 20, tone_check_rate: 1, tone_phase_sensitivity_enabled: true, tone_phase_sensitivity: 0.15, tone_slot: "", fragments_enabled: true, fragments_slot: "agent", fragments_mode: "host", review_enabled: true, review_slot: "agent", review_mode: "host", review_turns_threshold: 50, review_days_threshold: 7, agent_daily_budget: 20, anniversary_inject: true, debug_mode: false },
    calendar: { months: [calendarMonth(2026, 9), calendarMonth(2026, 10), calendarMonth(2026, 11)] },
    diary_recent: diary, diary_total: diary.length, fragment_total: empty ? 0 : 4,
    journal_index: journal.map(({ entries, ...header }) => header),
    journal_archive_brief: { pages: empty ? 0 : 2, first_ts: "2026-01-01", last_ts: "2026-02-28" },
    review_archive_brief: { entries: empty ? 0 : 2, first_ts: "2026-01-01", last_ts: "2026-02-28" },
    journal_invite_pending: false,
    journal_write: { writing: false, last_result: null },
    lanlan_list: [{ name: "\u6d4b\u8bd5\u89d2\u8272", enabled: true, phase: "follicular" }, { name: "\u53e6\u4e00\u4f4d\u6d4b\u8bd5\u89d2\u8272", enabled: false, phase: "luteal" }, { name: "\u5df2\u5220\u9664\u7684\u6d4b\u8bd5\u89d2\u8272", orphan: true }],
    tone_slot_options: ["", "conversation", "summary", "agent", "correction", "vision"].map(value => ({ value, model: value ? `qa-${value}-model` : "qa-emotion-model" })),
    review_brief: { enabled: true, entries: review.length, last_ts: review[0]?.ts || "", progress_turns: 32, turns_threshold: 50, writing: false, last_result: null },
    channel_status: { tone: { enabled: true, dormant_reason: "", transport: "host" }, fragments: { enabled: true, dormant_reason: "", transport: "host" }, review: { enabled: true, dormant_reason: "", transport: "host" } },
    agent_tier_usage: { used: 3, budget: 20 }, week_activity: empty ? 0 : 7,
    stats_summary: { summary: empty ? {} : { days_together: 184, total_turns: 2835, active_days: 142, cold_wars: 3, made_ups: 3, warm_moments: 26, longest_streak: 21, current_streak: 12, next_anniversary_in: 26 }, badges: [7, 30, 100, 365, 730].map(days => ({ id: `days_${days}`, days, unlocked: !empty && days <= 184, date: "2026-09-02" })) },
    onboarding: { wizard_pending: wizard, guide: { wizard: "done", version: "qa" }, readiness: { items: [], must_ok: true, all_ok: true, pending: 0 } },
    capabilities: { master_enabled: !empty, hide_disabled_tools: false, capabilities },
    birthday: { date: "2000-10-02", set: true, keep_diary: true },
  }
  if (moodCase) {
    const started = Date.parse("2026-10-13T15:00:00Z") / 1000
    state.mood = {
      ...state.mood, active: true, action: "ebb_tide", started_at: started,
      origin: "self", expires_at: started + 600,
      reason: "<img src=x onerror=alert(1)>\n" + "LongUnbrokenReason".repeat(25),
      history: Array.from({ length: 12 }, (_, index) => ({
        ts: started - index * 3600, action: "ripple",
        kind: ["choose", "switch", "release", "expire"][index % 4],
        origin: ["self", "user", "self", "timer"][index % 4],
        previous_action: index % 4 === 1 ? "shallow_reef" : "",
        reason: `event-${index}-private`,
      })),
    }
    if (moodCase === "legacy") { state.mood.origin = ""; state.mood.history = [] }
    if (moodCase === "empty") { state.mood.active = false; state.mood.history = [] }
    if (moodCase === "disabled") state.mood.system_enabled = false
  }
  if (selectedHeat?.start) {
    const summary = state.stats_summary.summary
    summary.days_together = (Date.parse(selectedHeat.end) - Date.parse(selectedHeat.start)) / 86400000 + 2
    summary.total_turns = selectedHeat.days.reduce((sum, day) => sum + day.turns, 0)
    summary.active_days = selectedHeat.days.filter(day => day.turns > 0).length
  }
  const translate = key => messages[locale]?.[key] || messages.en?.[key] || key
  const gallery = Object.entries(images).map(([id, data], index) => ({ id, name: `qa-landscape-${"ABC"[index]}.png`, mime: "image/png", size: data.length, added_at: TODAY, thumb: data }))
  const videos = {}
  if (video) {
    videos["qa-video"] = { ...video, name: "qa-motion.webm" }
    gallery.push({ id: "qa-video", kind: "video", name: "qa-motion.webm", mime: video.mime, size: video.size, added_at: TODAY, thumb: video.thumb })
  }
  const mediaStorage = {
    schema_version: 1, backend: "files",
    single_limit_bytes: 256 * 1024 * 1024,
    total_limit_bytes: 2 * 1024 * 1024 * 1024,
    hard_single_limit_bytes: 512 * 1024 * 1024,
    chunk_bytes: 768 * 1024,
    video_bytes: 0, pending_bytes: 0, reclaim_bytes: 0, occupied_bytes: 0,
    available_bytes: 32 * 1024 * 1024 * 1024,
    storage_reclaim_pending: false, cleanup_pending: false,
  }
  return {
    context: {
      plugin: { id: "forever_companion", name: "\u6c38\u8fdc\u7684\u966a\u4f34" },
      surface: { id: "main", kind: "panel", mode: "hosted-tsx", entry: "ui/panel.tsx", permissions: ["state:read", "config:read", "action:call"] },
      state, actions: ACTIONS.map(id => ({ id, entry_id: id, label: translate(id === "lift_mood" ? "actions.liftMood.label" : `actions.${id}.label`), refresh_context: true, confirm: id === "reset_all" ? translate("actions.reset.confirm") : false })),
      entries: [], config: { schema: { type: "object", properties: {} }, value: {}, readonly: true }, warnings: [],
      locale, i18n: { locale, default_locale: "zh-CN", messages },
    },
    appearance, image, images, gallery, videos, uploads: {}, videoSerial: 0,
    intros, journal, review, journalArchive, reviewArchive, diary, reviewProgress: { turns: 32, turns_threshold: 50, days: 4, days_threshold: 7, span: "2026-09-28~2026-10-02", due: false },
    mediaStorage, heatmaps, rpcDelay, legacyGallery,
    stats: { heatmap: selectedHeat || { days: heatDays, start: "2026-04-01", end: "2026-10-01", years: [2026], year: 2026, months: [] }, months: ["2026-10", "2026-09", "2026-08"], month: { month: "2026-10", turns: 124, active_days: 2, busiest_day: TODAY, busiest_turns: 68, longest_streak: 2, cold_wars: 0, made_ups: 0, warm_moments: 3, tone: { happy: 72, neutral: 33, sad: 4 }, valence_avg: 0.24, voice: { ts: TODAY, mood: "warm", entry: SAMPLE.repeat(3) }, sealed: false } },
  }
}

// Runs only in the isolated parent page. No real backend requests are made.
export function installFixtureBridge(fixture) {
  window.qaFixture = fixture
  window.qaMessages = []
  window.qaDiagnostics = []
  window.qaRefreshes = 0
  window.qaFailNext = ""
  window.qaResults = {}
  window.qaBackupCalls = []
  window.qaBackupFailure = ""
  window.qaOpenedPaths = []
  window.qaDelays = { get_panel_gallery: Number(fixture.rpcDelay), get_gallery_image: Number(fixture.rpcDelay) }
  window.qaLegacyGallery = fixture.legacyGallery === "true"
  window.qaResolved = []
  window.qaFailVideoOp = ""
  window.qaFailVideoChunk = -1
  window.qaFailVideoReadChunk = -1
  window.qaVideoCalls = []
  window.qaVideoAckOverrides = {}
  window.qaVideoWrites = []
  window.qaDroppedReplies = {}
  window.qaCancelledRequests = []
  window.qaHoldReplies = {}
  window.qaHeldReplies = {}
  window.qaReleaseHeldReplies = key => {
    window.qaHoldReplies[key] = false
    const replies = window.qaHeldReplies[key] || []
    delete window.qaHeldReplies[key]
    replies.forEach(reply => reply())
  }
  const clone = value => JSON.parse(JSON.stringify(value))
  const chunkBytes = 768 * 1024
  const videoBytes = () => fixture.gallery.reduce((sum, item) => sum + (item.kind === "video" ? item.size : 0), 0)
  const mediaSummary = () => {
    fixture.mediaStorage.video_bytes = videoBytes()
    fixture.mediaStorage.occupied_bytes = videoBytes()
    return clone(fixture.mediaStorage)
  }
  const validPoster = (value, maximum) => {
    if (typeof value !== "string" || !value || value.length > maximum || !/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/.test(value)) return false
    const [header, encoded] = value.split(",")
    try {
      const bytes = atob(encoded)
      if (btoa(bytes) !== encoded) return false
      if (header === "data:image/png;base64") return bytes.startsWith("\x89PNG\r\n\x1a\n")
      if (header === "data:image/jpeg;base64") return bytes.startsWith("\xff\xd8\xff")
      return bytes.startsWith("RIFF") && bytes.slice(8, 12) === "WEBP"
    } catch { return false }
  }
  const decodeChunk = value => {
    if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4) throw new Error("video_chunk_invalid")
    try {
      const bytes = atob(value)
      if (btoa(bytes) !== value) throw new Error("video_chunk_invalid")
      return bytes
    } catch { throw new Error("video_chunk_invalid") }
  }
  const dispatchVideo = args => {
    const op = args.op
    window.qaVideoCalls.push(clone(args))
    if (window.qaFailVideoOp === op && (window.qaFailVideoChunk < 0 || args.chunk_index === window.qaFailVideoChunk)) {
      window.qaFailVideoOp = ""
      window.qaFailVideoChunk = -1
      throw new Error("video_save_failed")
    }
    if (op === "video_begin") {
      if (!["video/mp4", "video/webm"].includes(args.mime)) throw new Error("video_type_unsupported")
      if (!Number.isInteger(args.size) || args.size < 1) throw new Error("video_size_invalid")
      if (args.size > fixture.mediaStorage.single_limit_bytes || args.size > fixture.mediaStorage.hard_single_limit_bytes) throw new Error("video_too_large")
      if (fixture.gallery.length >= 24) throw new Error("gallery_full")
      if (videoBytes() + args.size > fixture.mediaStorage.total_limit_bytes) throw new Error("video_storage_full")
      if (!validPoster(args.thumb, 400000) || !validPoster(args.poster, 1000000)) throw new Error("video_poster_invalid")
      const uploadId = `qa-session-${++fixture.videoSerial}`
      fixture.uploads[uploadId] = { name: args.name, mime: args.mime, size: args.size, thumb: args.thumb, poster: args.poster, chunks: [], written: 0 }
      return { upload_id: uploadId, chunk_bytes: chunkBytes, chunk_receipts: true }
    }
    const session = fixture.uploads[args.upload_id]
    if (op === "video_abort") {
      delete fixture.uploads[args.upload_id]
      return { aborted: true }
    }
    if (!session) throw new Error("video_upload_not_found")
    if (op === "video_status") {
      if (!Number.isInteger(args.chunk_index) || args.chunk_index < 0
        || args.chunk_index >= Math.ceil(session.size / chunkBytes)) throw new Error("video_chunk_invalid")
      return { chunk_index: args.chunk_index, accepted: args.chunk_index < session.chunks.length }
    }
    if (op === "video_chunk") {
      if (!Number.isInteger(args.chunk_index) || args.chunk_index < 0) throw new Error("video_chunk_invalid")
      if (args.chunk_index < session.chunks.length) {
        if (session.chunks[args.chunk_index] !== args.data_b64) throw new Error("video_chunk_invalid")
        return { chunk_index: args.chunk_index, accepted: true }
      }
      if (args.chunk_index !== session.chunks.length) throw new Error("video_chunk_out_of_order")
      const bytes = decodeChunk(args.data_b64)
      if (bytes.length !== Math.min(chunkBytes, session.size - session.written)) throw new Error("video_chunk_invalid")
      session.chunks.push(args.data_b64)
      session.written += bytes.length
      window.qaVideoWrites.push({ upload_id: args.upload_id, chunk_index: args.chunk_index, bytes: bytes.length })
      return Object.hasOwn(window.qaVideoAckOverrides, args.chunk_index)
        ? clone(window.qaVideoAckOverrides[args.chunk_index])
        : { chunk_index: args.chunk_index, accepted: true }
    }
    if (op === "video_commit") {
      if (session.written !== session.size) throw new Error("video_upload_incomplete")
      const head = atob(session.chunks[0]).slice(0, 4096)
      if (session.mime === "video/webm" && (!head.startsWith("\x1a\x45\xdf\xa3") || !head.includes("webm"))) throw new Error("video_signature_invalid")
      if (session.mime === "video/mp4") {
        const boxSize = [...head.slice(0, 4)].reduce((value, byte) => value * 256 + byte.charCodeAt(0), 0)
        if (head.length < 16 || head.slice(4, 8) !== "ftyp" || boxSize < 16 || boxSize > session.size || head.slice(8, 12) === "\0\0\0\0") throw new Error("video_signature_invalid")
      }
      if (fixture.gallery.length >= 24) throw new Error("gallery_full")
      if (videoBytes() + session.size > fixture.mediaStorage.total_limit_bytes) throw new Error("video_storage_full")
      const id = `qa-video-upload-${++fixture.videoSerial}`
      fixture.videos[id] = { ...clone(session), chunk_bytes: chunkBytes }
      fixture.gallery.push({ id, kind: "video", name: session.name, mime: session.mime, size: session.size, added_at: "2026-10-04", thumb: session.thumb })
      delete fixture.uploads[args.upload_id]
      return { id, items: clone(fixture.gallery), media_storage: mediaSummary() }
    }
    throw new Error("video_operation_invalid")
  }
  const dispatch = (action, args) => {
    const state = fixture.context.state
    if (window.qaFailNext === action) {
      window.qaFailNext = ""
      throw new Error("persist_failed")
    }
    if (Object.hasOwn(window.qaResults, action)) return clone(window.qaResults[action])
    if (action === "reset_all") {
      if (args.expected_lanlan && args.expected_lanlan !== state.lanlan) throw new Error("mood_changed")
      state.advance_days = 0
      state.status.enabled = false
      state.mood = { active: false, history: [], valence: 0, arousal: 0 }
      fixture.diary = []
      fixture.journal = []
      fixture.journalArchive = []
      fixture.review = []
      fixture.reviewArchive = []
      fixture.reviewProgress = { turns: 0 }
      fixture.stats = { heatmap: { days: [], year: 2026 }, month: null, month_available: [] }
      fixture.heatmaps = {}
      Object.assign(state, {
        diary_recent: [], diary_total: 0, fragment_total: 0, journal_index: [],
        journal_archive_brief: { pages: 0 }, review_archive_brief: { entries: 0 },
        journal_write: { writing: false, last_result: null }, journal_invite_pending: false,
        review_brief: { entries: 0, writing: false, last_result: null, progress_turns: 0 },
        stats_summary: { summary: {}, badges: [] },
      })
      return { reset: true, lanlan: state.lanlan }
    }
    if (action === "data_backup") {
      window.qaBackupCalls.push(clone(args))
      if (window.qaBackupFailure) throw new Error(window.qaBackupFailure)
      const summary = { roles: ["Alice", "Old @ role"], diary: 93, journal: 58, review: 41, images: 1 }
      const manifest = { created_at: "2026-10-16T10:00:00+00:00", plugin_version: "1.4.0", summary }
      const saved = { path: "D:\\Plugin Data\\forever_companion\\backups\\forever-companion-backup-20261016.fc-backup", directory: "D:\\Plugin Data\\forever_companion\\backups" }
      if (args.op === "export") return saved
      if (args.op === "begin") {
        fixture.backupUpload = { id: "a".repeat(32), size: args.size, received: 0 }
        return { upload_id: fixture.backupUpload.id, chunk_bytes: 393216 }
      }
      if (args.op === "recover") {
        if (fixture.backupHealthyState) fixture.context.state = fixture.backupHealthyState
        else delete state.backup_recovery_required
        return { recovered: true }
      }
      if (!fixture.backupUpload || fixture.backupUpload.id !== args.upload_id) throw new Error("backup_upload_invalid")
      if (args.op === "abort") { fixture.backupUpload = null; return { aborted: true } }
      if (args.op === "chunk") {
        if (args.offset !== fixture.backupUpload.received) throw new Error("backup_upload_invalid")
        fixture.backupUpload.received += atob(args.data_b64).length
        return { received: fixture.backupUpload.received }
      }
      if (args.op === "preview") return { manifest, current: { ...summary, roles: ["Alice"] }, token: "test-digest" }
      if (args.op === "apply") {
        if (args.confirm !== true || args.token !== "test-digest") throw new Error("backup_confirmation_required")
        state.settings.timezone = "Europe/London"
        fixture.appearance.bg_id = ""
        fixture.backupUpload = null
        return { restored: true, previous: saved, summary }
      }
      throw new Error("backup_invalid")
    }
    if (action === "lift_mood") {
      if (window.qaChangeMoodBeforeLift) {
        window.qaChangeMoodBeforeLift = false
        state.mood.started_at += 1
      }
      if (args.expected_lanlan !== state.lanlan || args.expected_started_at !== state.mood.started_at) throw new Error("mood_changed")
      state.mood.history.unshift({ ts: Date.now() / 1000, kind: "release", origin: "user", action: state.mood.action })
      state.mood.active = false
      state.mood.action = state.mood.origin = state.mood.reason = ""
      state.mood.started_at = 0
      return { cleared: true }
    }
    if (action === "get_panel_gallery") {
      const result = { items: fixture.gallery, appearance: fixture.appearance, media_storage: mediaSummary() }
      if (args.include_background && fixture.appearance.bg_id && !window.qaLegacyGallery) {
        const id = fixture.appearance.bg_id
        result.background = { id, media: dispatch("get_gallery_image", {
          item_id: id, prefer_direct: args.prefer_direct && fixture.appearance.motion,
        }) }
      }
      return result
    }
    if (action === "get_gallery_image") {
      const video = fixture.videos[args.item_id]
      if (video) {
        const index = args.chunk_index ?? -1
        if (index === -1) return { kind: "video", mime: video.mime, size: video.size, name: video.name, chunk_count: video.chunks.length, chunk_bytes: chunkBytes, poster: video.poster,
          ...(args.prefer_direct && video.playback_path ? { playback_path: video.playback_path } : {}) }
        const requested = args.chunk_count ?? 1
        if (window.qaFailVideoReadChunk >= index && window.qaFailVideoReadChunk < index + requested) {
          window.qaFailVideoReadChunk = -1
          throw new Error("video_read_failed")
        }
        if (!Number.isInteger(index) || index < 0 || index >= video.chunks.length) throw new Error("video_chunk_invalid")
        if (!Number.isInteger(requested) || requested < 1 || requested > 4) throw new Error("video_chunk_invalid")
        if (requested > 1) {
          return {
            chunks: video.chunks.slice(index, index + requested).map((data_b64, offset) => ({
              chunk_index: index + offset,
              data_b64,
            })),
          }
        }
        return { chunk_index: index, data_b64: video.chunks[index] }
      }
      if (!fixture.images[args.item_id]) throw new Error("image_not_found")
      return { data_url: fixture.images[args.item_id] }
    }
    if (action === "get_journal") return { pages: args.scope === "archive" ? fixture.journalArchive : fixture.journal }
    if (action === "get_review") return { entries: args.scope === "archive" ? fixture.reviewArchive : fixture.review, progress: fixture.reviewProgress }
    if (action === "get_diary") return { items: fixture.diary.slice(Number(args.offset || 0)), has_more: false }
    if (action === "get_stats") return { ...fixture.stats, heatmap: fixture.heatmaps?.[args.year] || fixture.stats.heatmap, month: fixture.stats.month ? { ...fixture.stats.month, month: args.month || fixture.stats.month.month } : null }
    if (action === "get_capability_intro") return clone(fixture.intros[args.capability_id] || { found: false, id: args.capability_id })
    if (action === "update_settings") {
      Object.assign(state.settings, args)
      if ("birthday_date" in args) state.birthday.date = args.birthday_date
      return clone(state.settings)
    }
    if (action === "set_panel_appearance") { Object.assign(fixture.appearance, args); return { appearance: clone(fixture.appearance) } }
    if (action === "gallery_add") {
      if (args.op === "video_policy") {
        if (!Number.isInteger(args.single_limit_mib) || args.single_limit_mib < 32 || args.single_limit_mib > 512
          || !Number.isInteger(args.total_limit_mib) || args.total_limit_mib < 64 || args.total_limit_mib > 16384) throw new Error("video_policy_invalid")
        fixture.mediaStorage.single_limit_bytes = args.single_limit_mib * 1024 * 1024
        fixture.mediaStorage.total_limit_bytes = args.total_limit_mib * 1024 * 1024
        return { media_storage: mediaSummary() }
      }
      if (args.op) return dispatchVideo(args)
      const id = `qa-upload-${fixture.gallery.length}`
      fixture.images[id] = args.data_url
      fixture.gallery.push({ id, name: args.name || "qa-upload.png", mime: "image/png", size: args.data_url.length, added_at: "2026-10-02", thumb: args.thumb })
      return { id, items: clone(fixture.gallery) }
    }
    if (action === "gallery_remove") {
      if (args.op === "clear_media") {
        if (args.confirm !== true) throw new Error("video_clear_confirmation_required")
        fixture.gallery = []
        fixture.images = {}
        fixture.videos = {}
        fixture.uploads = {}
        fixture.appearance.bg_id = ""
        fixture.mediaStorage.pending_bytes = 0
        fixture.mediaStorage.reclaim_bytes = 0
        return { cleared: true, items: [], appearance: clone(fixture.appearance), media_storage: mediaSummary(), storage_reclaim_pending: false, cleanup_pending: false }
      }
      fixture.gallery = fixture.gallery.filter(item => item.id !== args.item_id)
      delete fixture.images[args.item_id]
      delete fixture.videos[args.item_id]
      if (fixture.appearance.bg_id === args.item_id) fixture.appearance.bg_id = ""
      return { items: clone(fixture.gallery), appearance: clone(fixture.appearance) }
    }
    if (action === "toggle") {
      const enabled = !state.status.enabled
      state.status.enabled = enabled
      state.settings.enabled = enabled
      state.capabilities.master_enabled = enabled
      for (const cap of state.capabilities.capabilities) {
        if (cap.source === "master_off" || cap.source === "on") {
          cap.enabled = enabled
          cap.source = enabled ? "on" : "master_off"
        }
      }
      return { enabled }
    }
    if (action === "set_capability") {
      const cap = state.capabilities.capabilities.find(item => item.id === args.capability_id)
      if (cap) { cap.enabled = args.enabled; cap.source = args.enabled ? "on" : "user_off" }
      return { capabilities: state.capabilities, note: "reverted_to_default" }
    }
    if (action === "set_capability_flags") { state.capabilities.hide_disabled_tools = args.hide_disabled_tools; return {} }
    if (action === "set_onboarding") { state.onboarding.wizard_pending = args.action === "reopen"; return {} }
    if (action === "set_anchor") { state.anchor_date = args.date; return {} }
    if (action === "advance_days") { state.advance_days += Number(args.days || 1); return {} }
    return { accepted: true }
  }
  window.addEventListener("message", event => {
    const message = event.data
    const frame = document.getElementById("hosted")
    if (event.source !== frame?.contentWindow || !message || typeof message !== "object") return
    if (message.type === "neko-hosted-surface-open-path") { window.qaOpenedPaths.push(message.payload.path); return }
    if (message.type === "neko-hosted-surface-error" || message.type === "neko-hosted-surface-console") window.qaDiagnostics.push(message)
    if (message.type === "neko-hosted-surface-cancel") {
      window.qaCancelledRequests.push({ requestId: message.requestId, at: performance.now() })
      return
    }
    if (message.type !== "neko-hosted-surface-request") return
    window.qaMessages.push({ ...clone(message), receivedAt: performance.now() })
    try {
      let result
      if (message.method === "refresh") { window.qaRefreshes += 1; result = clone(fixture.context) }
      else if (message.method === "call") {
        const actionId = message.payload.actionId
        result = { plugin_id: "forever_companion", action_id: actionId, result: clone(dispatch(actionId, message.payload.args || {})) }
      } else throw new Error(`Unsupported fixture method: ${message.method}`)
      const actionId = message.method === "call" ? message.payload.actionId : message.method
      const imageId = actionId === "get_gallery_image" ? message.payload.args.item_id : ""
      const args = message.payload.args || {}
      const delayKey = args.op ? `${actionId}:${args.op}:${args.chunk_index ?? ""}`
        : imageId && Number.isInteger(args.chunk_index) ? `${actionId}:${imageId}:${args.chunk_index}`
        : imageId || actionId
      const delay = Number(window.qaDelays[delayKey] ?? window.qaDelays[imageId] ?? window.qaDelays[actionId] ?? 0)
      if (window.qaDroppedReplies[delayKey]) return
      const reply = () => {
        const target = frame.contentWindow
        if (!target) return
        window.qaResolved.push({ action: actionId, imageId, args: clone(args), requestId: message.requestId, delayKey, at: performance.now() })
        target.postMessage({ type: "neko-hosted-surface-response", requestId: message.requestId, ok: true, result }, "*")
      }
      if (window.qaHoldReplies[delayKey]) {
        const replies = window.qaHeldReplies[delayKey] ||= []
        replies.push(reply)
      } else setTimeout(reply, delay)
    } catch (error) {
      frame.contentWindow.postMessage({ type: "neko-hosted-surface-response", requestId: message.requestId, ok: false, error: error.message }, "*")
    }
  })
}
