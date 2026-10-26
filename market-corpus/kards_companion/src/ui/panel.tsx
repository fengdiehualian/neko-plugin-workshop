// KARDS 猫娘陪玩 —— 控制台面板（hosted-tsx，沙箱 iframe 内运行）
// 只能 import 相对路径与 "@neko/plugin-ui"；数据全部走 props.api.call。
// 宿主动作回执可能是 {result: ...} / {data: ...} / Ok 包装 / 错误 envelope，
// 这里一律先解包再判断 ok/status，不把“响应为真”当作成功。
// local state 使用宿主惯例 props.useLocalState（不是包导出）。
// 采集健康、reading、诊断时间线都是可选字段：宿主未提供时不猜测、不补造。
// 事件识别（recent_events）与宿主投递（delivery/dispatcher timeline）是两件事：
// - submitted 只表示 SDK 本地提交给宿主，不保证 LLM 生成或 TTS 播放；
// - simulated 只表示 dry-run 记录，未推送；
// - dropped/expired/rejected 等是明确的未输出；
// - unconfirmed/unknown 是回执未确认，不能当作已播报；
// - 没有同一 identity 的时间线时不推断“已播报”。
// Copyright (C) 2026 MIKU — SPDX-License-Identifier: AGPL-3.0-only
import {
  Button,
  ButtonGroup,
  Card,
  DataTable,
  Divider,
  Field,
  Grid,
  KeyValue,
  NumberInput,
  Page,
  Select,
  Stack,
  StatusBadge,
  Switch,
  Text,
  Tip,
  useToast,
} from "@neko/plugin-ui";
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui";

type BadgeTone = "success" | "warning" | "danger" | "neutral";

type ActionOutcome = {
  data: Record<string, any>;
  error: string;
};

type EventOutputKey =
  | "accepted"
  | "simulated"
  | "unoutput"
  | "pending"
  | "unconfirmed"
  | "unmatched"
  | "other";

type EventOutput = {
  key: EventOutputKey;
  label: string;
  reason: string;
  tone: BadgeTone;
  inferred: boolean;
};

type EventRow = {
  id: string;
  time: string;
  kind: string;
  priority: string;
  summary: string;
  frameId: string;
  outputKey: EventOutputKey;
  outputLabel: string;
  outputReason: string;
};

type DiagRow = {
  key: string;
  title: string;
  status: string;
  detail: string;
  suggestion: string;
  tone: BadgeTone;
};

function asRecord(value: any): Record<string, any> {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

// Hosted actions may return:
//   { result: {...} } | { data: {...} } | { value: {...} } | {...payload}
//   error envelopes: { ok: false, error } / { error } / { is_error: true }
function unwrapActionResult(envelope: any): ActionOutcome {
  const outer = asRecord(envelope);
  const raw = outer.result ?? outer.data ?? outer.value ?? outer;
  const result = asRecord(raw);
  const nestedError = result.error ?? (result.ok === false ? result.error : undefined);
  const errorText =
    outer.error ??
    (outer.ok === false ? nestedError ?? outer.message : undefined) ??
    (result.is_error === true ? (result.error ?? "action_error") : undefined) ??
    (result.ok === false ? (result.error ?? "action_error") : undefined);
  return {
    data: result,
    error: errorText === undefined || errorText === null ? "" : String(errorText),
  };
}

function pickBoolean(...values: any[]): boolean | undefined {
  for (const value of values) {
    if (typeof value === "boolean") return value;
  }
  return undefined;
}

function formatFrameAge(value: any): string {
  if (value === undefined || value === null || value === "") return "";
  const ms = Number(value);
  if (!Number.isFinite(ms)) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function formatClock(value: any): string {
  const raw = Number(value);
  if (!Number.isFinite(raw) || raw <= 0) return "";
  try {
    const date = new Date(raw > 1e12 ? raw : raw * 1000);
    return date.toLocaleTimeString();
  } catch {
    return "";
  }
}

function formatNumber(value: any): string {
  if (value === undefined || value === null || value === "") return "未知";
  const number = Number(value);
  return Number.isFinite(number) ? String(Math.trunc(number)) : "未知";
}

function formatSeconds(value: any): string {
  if (value === undefined || value === null || value === "") return "未知";
  const number = Number(value);
  if (!Number.isFinite(number)) return "未知";
  if (number <= 0) return "0 s";
  return number >= 10 ? `${Math.round(number)} s` : `${number.toFixed(1)} s`;
}

function boolText(value: any): string {
  return value === true ? "是" : value === false ? "否" : "未知";
}

function frameIdOf(event: any): string {
  const details = asRecord(event?.details);
  const evidence = asRecord(details.evidence);
  const raw = evidence.frame_id ?? details.frame_id ?? details.capture_frame_id;
  if (raw === undefined || raw === null || raw === "") return "—";
  return String(raw);
}

function firstNumber(...values: any[]): number | undefined {
  for (const value of values) {
    if (value === undefined || value === null || value === "") continue;
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return undefined;
}

function clip(value: any, max = 80): string {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, Math.max(0, max - 1))}…` : text;
}

function asEntries(value: any): any[] {
  if (Array.isArray(value)) return value;
  const record = asRecord(value);
  if (Array.isArray(record.recent)) return record.recent;
  if (Array.isArray(record.entries)) return record.entries;
  return [];
}

function actionById(actions: HostedAction[] | undefined, id: string): boolean {
  if (!Array.isArray(actions) || actions.length === 0) return true;
  return actions.some((action) => action.id === id || action.entry_id === id);
}

const HEALTH_LABELS: Record<string, string> = {
  disabled: "采集已禁用",
  process_not_found: "未找到 KARDS 进程",
  window_not_found: "未找到游戏窗口",
  minimized: "窗口已最小化",
  frame_unavailable: "当前无可用画面",
  stale_frame: "画面已过期",
  capturing: "采集中",
  degraded: "降级采集中",
  stopped: "采集已停止",
};

const HEALTH_SUGGESTIONS: Record<string, string> = {
  disabled: "采集已在宿主中禁用；请检查宿主设置。",
  process_not_found: "启动 KARDS 并确认游戏进程存在。",
  window_not_found: "打开 KARDS 游戏窗口。",
  minimized: "从任务栏还原游戏窗口。",
  frame_unavailable: "将游戏窗口置于前台，等待新的可读帧。",
  stale_frame: "等待新帧；检查窗口是否被遮挡或最小化。",
  capturing: "",
  degraded: "采集处于降级后端；画面卡顿时检查窗口模式或采集后端。",
  stopped: "点击“开始监控”。",
};

const REASON_LABELS: Record<string, string> = {
  monitoring_stopped: "监控已停止",
  capture_not_started: "采集未启动",
  user_pause: "用户暂停解说",
  user_paused: "用户暂停解说",
  no_trusted_snapshot: "尚无可信快照",
  snapshot_not_trusted: "快照不可信",
  context_stale: "上下文过期",
  scene_not_match: "不在对局场景",
  scenario_context_invalid: "场景上下文失效",
  session_changed: "对局会话已切换",
  capture_unavailable: "采集不可用",
  frame_unavailable: "无可用画面",
  process_not_found: "未找到游戏进程",
  window_not_found: "未找到游戏窗口",
  minimized: "窗口已最小化",
  stale_frame: "画面已过期",
  dry_run: "dry-run 模拟",
  consent_required: "需要数据同意",
  stopped: "已停止",
  stopping: "正在停止",
  paused: "已暂停",
  ttl_expired: "事件 TTL 过期",
  quiet_window: "安静窗",
  route_unavailable: "路由不可用",
  host_rejected: "宿主拒绝",
  receipt_unknown: "回执未知",
  legacy_no_receipt: "旧 SDK 无回执",
  sdk_local_submitted: "SDK 本地提交",
  push_confirmed: "SDK 本地提交已确认",
  prompt_error: "本地构造失败",
  safety_circuit_open: "发送熔断打开",
  safety_paused: "输出安全门暂停",
};

const CONTEXT_SUGGESTIONS: Record<string, string> = {
  monitoring_stopped: "点击“开始监控”。",
  user_paused: "点击“恢复解说”。",
  no_trusted_snapshot: "等待采集产生第一帧可信快照。",
  snapshot_not_trusted: "等待下一帧；不要用旧帧推断对局。",
  context_stale: "保持游戏窗口在前台以刷新上下文。",
  scene_not_match: "进入对局后识别与解说才会运行。",
  scenario_context_invalid: "等待场景重新确认。",
  session_changed: "新对局已建立，等待上下文重建。",
};

const STAGE_LABELS: Record<string, string> = {
  capture: "采集",
  pipeline: "流水线",
  detector: "检测器",
  dispatcher: "投递",
  read: "只读通道",
  chat: "聊天",
  scenario: "场景",
};

const OUTCOME_LABELS: Record<string, string> = {
  queued: "已入队",
  submitted: "已提交给宿主",
  simulated: "模拟记录",
  dropped: "已丢弃",
  expired: "已过期",
  replaced: "已替换",
  purged: "已清除",
  deferred: "延后",
  waiting: "等待",
  retry: "重试",
  quiet_window: "安静窗等待",
  failed: "发送失败",
  suppressed: "已抑制",
  unconfirmed: "回执未确认",
  unknown: "回执未知",
  stale_epoch: "旧上下文丢弃",
  route_unavailable: "路由不可用",
  passive_context: "仅被动上下文（read）",
  rejected: "宿主拒绝",
  read_submitted: "read 已提交",
  read_coalesced: "read 合并",
  read_failed: "read 失败",
  read_suppressed: "read 抑制",
  started: "开始",
  closed: "关闭",
  unclean_stop: "清理未完成",
};

const FINAL_OUTCOMES = [
  "submitted",
  "simulated",
  "dropped",
  "expired",
  "replaced",
  "purged",
  "failed",
  "suppressed",
  "stale_epoch",
  "route_unavailable",
  "unconfirmed",
  "unknown",
  "rejected",
  "passive_context",
];

const PENDING_OUTCOMES = ["queued", "retry", "deferred", "quiet_window", "waiting"];

const UNOUTPUT_OUTCOMES = [
  "dropped",
  "expired",
  "purged",
  "replaced",
  "failed",
  "suppressed",
  "stale_epoch",
  "route_unavailable",
  "rejected",
];

// 顺序固定为：采集 / 变化检测 / 界面识别 / 卡牌识别（capture/change/unit/card）。
const PERCEPTION_PRESET_OPTIONS = [
  { value: "low_cost", label: "低负载（15 / 8 / 2 / 3）" },
  { value: "balanced", label: "平衡（20 / 10 / 4 / 6）" },
  { value: "quality", label: "质量（30 / 12 / 6 / 10）" },
  { value: "custom", label: "自定义" },
];

const FPS_FIELDS = [
  { key: "capture", configKey: "capture_fps", label: "采集", fallbackMin: 1, fallbackCap: 30 },
  { key: "change", configKey: "change_fps", label: "变化检测", fallbackMin: 0.5, fallbackCap: 15 },
  { key: "unit", configKey: "unit_fps", label: "界面识别", fallbackMin: 0.5, fallbackCap: 12 },
  { key: "card", configKey: "card_fps", label: "卡牌识别", fallbackMin: 0.5, fallbackCap: 12 },
];

// ---------------------------------------------------------------------------
// 识别模型 / 当前识别 / 实测频率 —— 新增文案常量（集中放置，便于统一审定）
// ---------------------------------------------------------------------------
// 概览「识别模型」卡
const MODEL_CARD_TITLE = "识别模型";
const MODEL_UI_DET_TITLE = "界面检测";
const MODEL_CARD_CLS_TITLE = "卡牌分类";
const MODEL_LOADED_TEXT = "文件已加载";
const MODEL_NOT_LOADED_TEXT = "未加载（功能停用）";
const MODEL_UNAVAILABLE_TEXT = "不可用";
const MODEL_UNKNOWN_TEXT = "状态未知";
const MODEL_ROW_CLASS_COUNT = "类别数";
const MODEL_ROW_CLASS_MAP = "类别表版本";
const MODEL_ROW_TRUSTED_COUNT = "可信卡牌数";
const MODEL_ROW_DIAGNOSTIC = "诊断";
const MODEL_TRUSTED_HINT =
  "第一版为候选版。模型加载不代表识别通过；只启用已验证类别，未通过的卡牌保持未知。";

// 概览「当前识别」卡
const RECOGNIZED_CARD_TITLE = "最近卡面观测";
const RECOGNIZED_EMPTY_TEXT = "暂无已确认卡牌";
const RECOGNIZED_UNKNOWN_NAME = "未识别";
const RECOGNIZED_TABLE_COLUMNS = {
  instance: "观测",
  name: "卡牌",
  region: "区域",
  conf: "置信度",
};
const RECOGNIZED_HINT =
  "这里只记录看到的卡面，不据此判断出牌，也不自动分配给棋盘单位。";
const CARD_REGION_LABELS: Record<string, string> = {
  card_hand: "手牌",
  played_card: "放大牌面",
  board: "棋盘",
};
const UI_CLASS_LABELS: Record<string, string> = {
  unit_board: "棋盘单位",
  card_hand: "手牌区域",
  card_back: "卡背",
  played_card: "放大牌面",
  hq: "总部位置",
  frontline: "前线",
  end_turn_button: "回合按钮位置",
  end_turn_button_active: "我方回合状态",
  screen_victory: "胜利画面",
  screen_defeat: "失败画面",
};

// 实测频率（fps_measured 与目标并列）
const FPS_MEASURED_NONE = "—";
const FPS_MEASURED_TARGET = "目标";
const FPS_MEASURED_ACTUAL = "实测";
const FPS_ROW_LABELS: Record<string, string> = {
  capture: "采集频率",
  change: "变化检测频率",
  unit: "界面识别频率",
  card: "卡牌识别频率",
};

function formatHz(value: number): string {
  return value >= 10 ? value.toFixed(1).replace(/\.0$/, "") : value.toFixed(1);
}

// “目标 10 Hz / 实测 8.3 Hz”；无实测显示“—”，目标未知显示“未知”。
function measuredFpsText(target: number | undefined, measured: any): string {
  const targetText = target === undefined ? "未知" : `${formatHz(target)} Hz`;
  const raw = Number(measured);
  const actualText =
    measured === null ||
      measured === undefined ||
      !Number.isFinite(raw) ||
      raw <= 0
      ? FPS_MEASURED_NONE
      : `${formatHz(raw)} Hz`;
  return `${FPS_MEASURED_TARGET} ${targetText} / ${FPS_MEASURED_ACTUAL} ${actualText}`;
}

function formatConfidence(value: any): string {
  const raw = Number(value);
  return Number.isFinite(raw) && raw > 0 ? `${(raw * 100).toFixed(0)}%` : "—";
}

function classMapVersionText(value: any): string {
  if (value === undefined || value === null || value === "") return "未知";
  return String(value);
}

type ModelBadge = { label: string; tone: BadgeTone; diagnostic: string };

// loaded 是唯一事实来源：true 才显示已加载；false 时按 state 区分
// 不可用 / 未加载（功能停用），诊断信息原样透出，不伪装可用。
function modelBadge(model: Record<string, any>): ModelBadge {
  const loaded = pickBoolean(model.loaded);
  const state = String(model.state ?? "");
  const diagnostic = String(model.diagnostics ?? "").trim();
  if (loaded === undefined) {
    return { label: MODEL_UNKNOWN_TEXT, tone: "neutral", diagnostic };
  }
  if (loaded) return { label: MODEL_LOADED_TEXT, tone: "success", diagnostic };
  if (state === "unavailable") {
    return { label: MODEL_UNAVAILABLE_TEXT, tone: "warning", diagnostic };
  }
  return { label: MODEL_NOT_LOADED_TEXT, tone: "warning", diagnostic };
}

// 与后端 runtime/contracts.py 的 event_identity/event_edge 保持同一口径。
// 仅用于把 recent_events 与 dispatcher timeline 的 detail.identity 精确关联；
// 关联不上时宁可显示“未匹配”，也不推断已播报。
const EDGE_VALUES = ["enter", "upgrade", "recovery", "steady"];

function hasOwn(record: Record<string, any>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function identityComponent(value: any, fallback = "-"): string {
  if (value === undefined || value === null) return fallback;
  let text = typeof value === "boolean" ? (value ? "True" : "False") : String(value);
  text = text.replace(/\u0000/g, " ");
  text = text.split(/\s+/).join(" ");
  text = text.replace(/\|/g, "_").slice(0, 64).trim();
  return text || fallback;
}

function eventEdge(event: any): string {
  const details = asRecord(event?.details);
  const explicit = String(details.edge ?? "").trim().toLowerCase();
  if (EDGE_VALUES.indexOf(explicit) >= 0) return explicit;
  const kind = String(event?.kind ?? "").trim();
  const side = String(details.side ?? "").trim().toLowerCase();
  if (kind === "match_start") return "enter";
  if (kind === "hq_critical") {
    if (side !== "mine" && side !== "enemy") return "steady";
    const rawHp = Number(details.hp);
    const hp = Number.isFinite(rawHp) ? Math.trunc(rawHp) : null;
    if (hp !== null && hp > 5) return "recovery";
    const rawBand = Number(details.band ?? 0);
    const band = Number.isFinite(rawBand) ? Math.trunc(rawBand) : 0;
    if (band >= 2) return "upgrade";
    return "enter";
  }
  if (kind === "unit_played") return "enter";
  if (kind === "unit_advanced") return "upgrade";
  if (kind === "hq_damage" || kind === "unit_died") return "steady";
  const rawBand = Number(details.band ?? 0);
  const band = Number.isFinite(rawBand) ? Math.trunc(rawBand) : 0;
  if (band >= 2) return "upgrade";
  if (band === 1) return "enter";
  return "steady";
}

function eventIdentity(event: any): string {
  const details = asRecord(event?.details);
  let entity = "-";
  for (const key of ["entity", "entity_id", "unit_id", "instance_id"]) {
    const value = details[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      entity = identityComponent(value);
      break;
    }
  }
  const game = hasOwn(details, "game")
    ? details.game
    : hasOwn(details, "game_number")
      ? details.game_number
      : 0;
  const turn = hasOwn(details, "turn") ? details.turn : details.turn_number;
  const band = hasOwn(details, "band") ? details.band : details.crisis_band;
  const occurrence = hasOwn(details, "occurrence") ? details.occurrence : details.seq;
  const episode = hasOwn(details, "episode")
    ? details.episode
    : details.condition_episode;
  return [
    identityComponent(details.session_id),
    identityComponent(game, "0"),
    identityComponent(event?.kind),
    identityComponent(details.side),
    entity,
    identityComponent(turn),
    identityComponent(band),
    identityComponent(eventEdge(event)),
    identityComponent(occurrence),
    identityComponent(episode),
  ].join("|");
}

function identityMatches(entry: any, identity: string): boolean {
  const record = asRecord(entry);
  const detail = asRecord(record.detail);
  const candidate = String(detail.identity ?? "");
  if (!candidate) return false;
  if (candidate === identity) return true;
  // 后端把 identity 截到 160 字符；其余部分只可能是截断后的前缀。
  return candidate.length >= 160 && identity.startsWith(candidate);
}

function latestFinalEntry(entries: any[]): any {
  const sorted = entries
    .slice()
    .sort((left, right) => Number(left?.ts ?? 0) - Number(right?.ts ?? 0));
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    if (FINAL_OUTCOMES.indexOf(String(sorted[index]?.outcome ?? "")) >= 0) {
      return sorted[index];
    }
  }
  return null;
}

function classifyOutcome(
  status: string,
  reason: string,
  inferred: boolean,
): EventOutput {
  const suffix = inferred ? "（时间邻近推断）" : "";
  if (status === "submitted") {
    return {
      key: "accepted",
      label: `已提交给宿主${suffix}`,
      reason: "SDK 本地提交；不保证 LLM 生成或 TTS 播放",
      tone: "success",
      inferred,
    };
  }
  if (status === "simulated") {
    return {
      key: "simulated",
      label: `模拟/已记录${suffix}`,
      reason: reasonText(reason) || "dry-run：仅写记录，不推送",
      tone: "warning",
      inferred,
    };
  }
  if (PENDING_OUTCOMES.indexOf(status) >= 0) {
    return {
      key: "pending",
      label: `等待中：${OUTCOME_LABELS[status] ?? status}${suffix}`,
      reason: reasonText(reason),
      tone: "neutral",
      inferred,
    };
  }
  if (status === "unconfirmed" || status === "unknown" || status === "legacy_unconfirmed") {
    const outcomeLabel = OUTCOME_LABELS[status] ?? status;
    return {
      key: "unconfirmed",
      label:
        outcomeLabel === "回执未确认"
          ? `回执未确认${suffix}`
          : `回执未确认：${outcomeLabel}${suffix}`,
      reason: reasonText(reason) || "不能推断是否已生成或已播放",
      tone: "warning",
      inferred,
    };
  }
  if (UNOUTPUT_OUTCOMES.indexOf(status) >= 0) {
    return {
      key: "unoutput",
      label: `未输出：${OUTCOME_LABELS[status] ?? status}${suffix}`,
      reason: reasonText(reason),
      tone: "danger",
      inferred,
    };
  }
  if (status === "passive_context") {
    return {
      key: "other",
      label: `仅被动上下文：read 通道${suffix}`,
      reason: reasonText(reason) || "未走 respond 输出",
      tone: "neutral",
      inferred,
    };
  }
  return {
    key: "other",
    label: `其他状态：${status || "—"}${suffix}`,
    reason: reasonText(reason),
    tone: "neutral",
    inferred,
  };
}

function eventOutputFor(
  identity: string,
  identityCount: number,
  dispatcherEntries: any[],
): EventOutput {
  if (identityCount > 1) {
    return {
      key: "unmatched",
      label: "未匹配输出记录",
      reason: "同一事件 identity 有多条，无法唯一归属；请对照下方 dispatcher 时间线",
      tone: "neutral",
      inferred: false,
    };
  }
  const exact = dispatcherEntries.filter((entry) => identityMatches(entry, identity));
  const chosen = exact.length > 0
    ? latestFinalEntry(exact) ?? exact[exact.length - 1]
    : null;
  if (!chosen) {
    // Never infer acceptance from timing or kind alone.  No exact dispatcher
    // identity means the output state is unknown.
    return {
      key: "unmatched",
      label: "输出状态未知",
      reason: "没有同一事件 identity 的 dispatcher 记录；不推断已接受或已播报",
      tone: "neutral",
      inferred: false,
    };
  }
  const record = asRecord(chosen);
  return classifyOutcome(
    String(record.outcome ?? ""),
    String(record.reason ?? ""),
    false,
  );
}

function reasonText(reason: any): string {
  const raw = String(reason ?? "").trim();
  if (!raw) return "";
  const label = REASON_LABELS[raw];
  return label && label !== raw ? `${label}（${raw}）` : raw;
}

function healthTone(status: string): BadgeTone {
  if (status === "capturing") return "success";
  if (status === "degraded" || status === "stale_frame") return "warning";
  if (
    status === "frame_unavailable" ||
    status === "window_not_found" ||
    status === "process_not_found"
  ) {
    return "danger";
  }
  return "neutral";
}

// 兼容主程序可能暴露的几种形状：capture_health 字符串/对象、capture 对象、
// health 对象，或旧的 capture_status 字符串。全部缺失时 present=false。
function captureOf(ctx: any): { present: boolean; value: Record<string, any> } {
  const raw = ctx.capture_health ?? ctx.capture ?? ctx.health;
  if (typeof raw === "string" && raw.trim() !== "") {
    return { present: true, value: { status: raw.trim() } };
  }
  const value = asRecord(raw);
  if (Object.keys(value).length > 0) return { present: true, value };
  if (typeof ctx.capture_status === "string" && ctx.capture_status !== "") {
    return { present: true, value: { status: ctx.capture_status } };
  }
  return { present: false, value: {} };
}

export default function Panel(props: PluginSurfaceProps) {
  const toast = useToast();
  const ctx = props.state?.status ?? {};
  const [diagResult, setDiagResult] = props.useLocalState<any>("diagResult", null);
  const [tab, setTab] = props.useLocalState<string>("panelTab", "overview");
  const [eventFilter, setEventFilter] = props.useLocalState<string>(
    "eventFilter",
    "all",
  );
  // Drafts stay null until the user edits; that keeps the panel aligned with
  // a context that may arrive one refresh later.
  const [presetDraft, setPresetDraft] = props.useLocalState<string>(
    "perceptionPresetDraft",
    "",
  );
  const [fpsDraft, setFpsDraft] = props.useLocalState<any>(
    "perceptionFpsDraft",
    null,
  );
  const [eventPage, setEventPage] = props.useLocalState<number>("eventPage", 0);
  const [lifecyclePage, setLifecyclePage] = props.useLocalState<number>(
    "lifecyclePage",
    0,
  );
  const [diagAdvanced, setDiagAdvanced] = props.useLocalState<boolean>(
    "diagAdvanced",
    false,
  );

  const can = (id: string) => actionById(props.actions, id);

  const call = async (id: string, args?: Record<string, unknown>): Promise<ActionOutcome> => {
    try {
      const envelope = await props.api.call(id, args ?? {});
      const outcome = unwrapActionResult(envelope);
      if (outcome.error) {
        toast.error(`操作失败：${outcome.error}`);
      }
      await props.api.refresh();
      return outcome;
    } catch (error: any) {
      const message = String(error?.message ?? error);
      toast.error(`操作失败：${message}`);
      return { data: {}, error: message };
    }
  };

  const pipeline = asRecord(ctx.pipeline);
  const capture = captureOf(ctx);
  const captureValue = capture.value;
  const detectors = asRecord(ctx.detectors);
  const delivery = asRecord(ctx.delivery);
  const dispatcherStats = asRecord(delivery.stats);
  const gate = asRecord(delivery.gate);
  const gateConfig = asRecord(gate.config);
  const breaker = asRecord(delivery.breaker);
  const quietWindow = asRecord(delivery.quiet_window);
  const config = asRecord(ctx.config);
  const scenario = asRecord(ctx.scenario);
  const diag = asRecord(diagResult);
  // Overview uses only the live status snapshot.  A manually pulled
  // diagnostic snapshot is never presented as current in overview.
  const lastSnapshot = asRecord(ctx.last_snapshot);
  const snapshotCapture = asRecord(lastSnapshot.capture);
  const snapshotFrame = asRecord(lastSnapshot.frame);
  const snapshotState = asRecord(lastSnapshot.state);
  const diagSnapshot = asRecord(diag.last_snapshot);
  const diagSnapshotView = Object.keys(diagSnapshot).length > 0
    ? diagSnapshot
    : lastSnapshot;
  const diagCapture = asRecord(diagSnapshotView.capture);
  const diagFrame = asRecord(diagSnapshotView.frame);
  const diagState = asRecord(diagSnapshotView.state);
  const gateRecords = asEntries(diag.gate_records);
  const diagSimulatedRecords = asEntries(diag.simulated_records);
  const simulatedRecords =
    diagSimulatedRecords.length > 0
      ? diagSimulatedRecords
      : asEntries(delivery.simulated_records);

  const monitoring = Boolean(ctx.monitoring ?? pipeline.monitoring);
  const modeValue = ctx.mode ?? pipeline.mode ?? config.mode;
  const mode =
    modeValue === undefined || modeValue === null ? "" : String(modeValue);
  const dryRunValue = pickBoolean(
    ctx.dry_run,
    pipeline.dry_run,
    config.dry_run,
    gateConfig.dry_run,
  );
  const dryRun = dryRunValue === true;
  const dryRunKnown = dryRunValue !== undefined;
  const dryRunBadgeTone: BadgeTone =
    dryRunKnown && !dryRun ? "neutral" : "warning";
  const dryRunBadgeLabel = !dryRunKnown
    ? "dry-run：状态未知"
    : dryRun
      ? "dry-run：模拟不推送"
      : "推送开启";
  const userPaused = Boolean(
    pipeline.user_paused ?? captureValue.user_paused ?? ctx.user_paused,
  );
  const pipelinePaused = Boolean(pipeline.paused);
  const pauseReason = String(pipeline.pause_reason ?? "");
  const startError = String(pipeline.start_error ?? "");
  const scene = String(ctx.scene ?? "unknown");
  const scenarioText = String(scenario.scenario ?? scene ?? "unknown");
  const scenarioReason = String(scenario.reason ?? "");
  const sessionId = String(pipeline.session_id ?? ctx.session_id ?? "");
  const gameNumber = Number(ctx.game_number ?? detectors.match_tracker?.game_number ?? 0);
  const inMatch = pickBoolean(ctx.in_match, detectors.match_tracker?.in_match);
  const detectorLoadedValue = pickBoolean(ctx.detector_loaded);
  const detectorLoaded = detectorLoadedValue === true;
  const detectorLoadedKnown = detectorLoadedValue !== undefined;
  const recentEvents: any[] = Array.isArray(ctx.recent_events) ? ctx.recent_events : [];

  const healthStatus = String(captureValue.status ?? "").trim();
  const healthLabel = healthStatus
    ? HEALTH_LABELS[healthStatus] ?? healthStatus
    : "状态未知";
  const reading = pickBoolean(captureValue.reading, ctx.reading);
  const contextValid = pickBoolean(
    captureValue.context_valid,
    pipeline.context_valid,
    ctx.context_valid,
  );
  const contextReason = String(
    pipeline.context_reason ?? captureValue.context_reason ?? "",
  );
  const classificationValid = pickBoolean(
    pipeline.classification_valid,
    ctx.classification_valid,
    captureValue.classification_valid,
  );
  const hasCapture = capture.present || reading !== undefined;
  const windowFound = pickBoolean(ctx.window_found);
  const foreground = pickBoolean(ctx.foreground, captureValue.foreground);
  const visible = pickBoolean(captureValue.visible);
  const minimized = pickBoolean(captureValue.minimized);
  const consent = pickBoolean(gateConfig.consent, config.llm_data_consent);

  const liveTimelineSource =
    ctx.timeline ?? ctx.diagnostic_timeline ?? ctx.diagnostics;
  const liveDiagnostics = asEntries(liveTimelineSource);
  const diagTimeline = diag.timeline;
  const diagDiagnostics = asEntries(diagTimeline);
  // 高级诊断优先展示手动拉取的更长快照；记录页优先使用实时 status timeline。
  const timelineSource =
    diagDiagnostics.length > 0 ? diagTimeline : liveTimelineSource;
  const timelineRecord = asRecord(timelineSource);
  const diagnostics = diagDiagnostics.length > 0 ? diagDiagnostics : liveDiagnostics;
  const droppedEntries = Number(timelineRecord.dropped ?? 0) || 0;
  const liveDispatcherEntries = liveDiagnostics.filter(
    (entry) => String(asRecord(entry).stage ?? "") === "dispatcher",
  );
  const dispatcherEntries =
    liveDispatcherEntries.length > 0
      ? liveDispatcherEntries
      : diagnostics.filter(
          (entry) => String(asRecord(entry).stage ?? "") === "dispatcher",
        );

  const perception = asRecord(ctx.perception_settings);
  const perceptionFps = asRecord(perception.fps);
  const perceptionCaps = asRecord(perception.caps);
  const perceptionMins = asRecord(perception.mins);
  const presetCatalog = asRecord(perception.presets);
  const settingsActionAvailable = can("perception_settings_set");
  const presetRaw =
    perception.perception_preset ?? perception.preset ?? config.perception_preset;
  const presetValue = presetDraft ||
    (presetRaw === undefined || presetRaw === null ? "" : String(presetRaw));
  const presetKnown =
    presetValue === "custom" || PERCEPTION_PRESET_OPTIONS.some(
      (item) => item.value === presetValue,
    );
  const fallbackFps = (...values: any[]): number | "" => {
    const number = firstNumber(...values);
    return number === undefined ? "" : number;
  };
  const fpsFallback: Record<string, number | ""> = {
    capture: fallbackFps(
      perception.capture_fps, perceptionFps.capture_fps, config.capture_fps),
    change: fallbackFps(
      perception.change_fps, perceptionFps.change_fps, config.change_fps),
    unit: fallbackFps(
      perception.unit_fps, perceptionFps.unit_fps, config.unit_fps),
    card: fallbackFps(
      perception.card_fps, perceptionFps.card_fps, config.card_fps),
  };
  const fpsValue: Record<string, number | ""> = {};
  for (const field of FPS_FIELDS) {
    const raw = fpsDraft?.[field.key];
    fpsValue[field.key] =
      raw === undefined || raw === null
        ? fpsFallback[field.key]
        : raw === ""
          ? ""
          : Number(raw);
  }
  const capOf = (field: (typeof FPS_FIELDS)[number]) =>
    firstNumber(perceptionCaps[field.configKey], field.fallbackCap) ??
    field.fallbackCap;
  const minOf = (field: (typeof FPS_FIELDS)[number]) =>
    firstNumber(perceptionMins[field.configKey], field.fallbackMin) ??
    field.fallbackMin;
  const fpsValid = FPS_FIELDS.every((field) => {
    if (fpsValue[field.key] === "") return false;
    const value = Number(fpsValue[field.key]);
    return Number.isFinite(value) && value >= minOf(field) && value <= capOf(field);
  });
  const snapshotTrusted =
    contextValid !== false &&
    pickBoolean(
      lastSnapshot.trusted,
      snapshotCapture.reading === true && snapshotFrame.valid === true,
    ) === true;
  const trustedState = snapshotTrusted ? snapshotState : {};
  const trustedInMatch = snapshotTrusted && trustedState.in_match === true;
  const unitTracker = asRecord(detectors.unit_tracker);
  const unitLast = asRecord(detectors.unit_last);
  const schedulerTasks = asRecord(asRecord(perception.scheduler).tasks);

  // 状态面 models 段：双模型加载态 + 类别表版本 + 可信卡数（kards_status_v2）。
  const models = asRecord(ctx.models);
  const uiDet = asRecord(models.ui_det);
  const cardCls = asRecord(models.card_cls);
  const classMapVersions = asRecord(models.class_map_versions);
  const uiDetBadge = modelBadge(uiDet);
  const cardClsBadge = modelBadge(cardCls);
  const trustedCardCountText = formatNumber(models.trusted_card_count);
  const supportedUiClasses: string[] = Array.isArray(uiDet.supported_classes)
    ? uiDet.supported_classes.map(String)
    : [];
  const unsupportedUiClasses: string[] = Array.isArray(uiDet.unsupported_classes)
    ? uiDet.unsupported_classes.map(String)
    : [];
  const uiCapabilityKnown = Array.isArray(uiDet.supported_classes);
  const uiClassNames = (names: string[]) => names
    .map((name) => UI_CLASS_LABELS[name] ?? name).join("、");
  const modelRows = (
    title: string,
    model: Record<string, any>,
    badge: ModelBadge,
    versionValue: any,
  ): Array<[string, string]> => {
    const rows: Array<[string, string]> = [
      [`${title} · 加载状态`, badge.label],
      [`${title} · ${MODEL_ROW_CLASS_COUNT}`, formatNumber(model.nc)],
      [`${title} · ${MODEL_ROW_CLASS_MAP}`, classMapVersionText(versionValue)],
    ];
    if (badge.diagnostic) {
      rows.push([`${title} · ${MODEL_ROW_DIAGNOSTIC}`, clip(badge.diagnostic, 60)]);
    }
    return rows;
  };
  const uiDetRows = modelRows(
    MODEL_UI_DET_TITLE, uiDet, uiDetBadge, classMapVersions.ui_classes);
  const cardClsRows = modelRows(
    MODEL_CARD_CLS_TITLE, cardCls, cardClsBadge, classMapVersions.card_classes);

  // 状态面 recognized_cards：分类确认的卡牌实例（最多保留 20 条）。
  const recognizedCards: any[] = Array.isArray(ctx.recognized_cards)
    ? ctx.recognized_cards
    : [];

  // 状态面 fps_measured：与目标值并列展示。
  const fpsMeasured = asRecord(ctx.fps_measured);
  const fpsTarget: Record<string, number | undefined> = {};
  for (const field of FPS_FIELDS) {
    fpsTarget[field.key] = firstNumber(
      perception[field.configKey],
      perceptionFps[field.configKey],
      config[field.configKey],
    );
  }
  const fpsMeasuredRows: Array<[string, string]> = FPS_FIELDS.map((field) => [
    FPS_ROW_LABELS[field.key] ?? field.label,
    measuredFpsText(fpsTarget[field.key], fpsMeasured[field.key]),
  ]);

  const captureRows: Array<[string, string]> = [];
  if (captureValue.backend) captureRows.push(["后端", String(captureValue.backend)]);
  if (captureValue.reason) captureRows.push(["原因", reasonText(captureValue.reason)]);
  const frameAge = formatFrameAge(captureValue.frame_age_ms);
  if (frameAge) captureRows.push(["帧龄", frameAge]);
  if (captureValue.width || captureValue.height) {
    captureRows.push([
      "分辨率",
      `${captureValue.width ?? "?"}×${captureValue.height ?? "?"}`,
    ]);
  }
  if (captureValue.process_name) {
    captureRows.push(["进程", String(captureValue.process_name)]);
  }
  if (captureValue.window_title) {
    captureRows.push(["窗口标题", clip(captureValue.window_title, 60)]);
  }
  if (typeof captureValue.foreground === "boolean") {
    captureRows.push(["前台", captureValue.foreground ? "是" : "否"]);
  }
  if (typeof captureValue.visible === "boolean") {
    captureRows.push(["窗口可见", captureValue.visible ? "是" : "否"]);
  }
  if (typeof captureValue.minimized === "boolean") {
    captureRows.push(["最小化", captureValue.minimized ? "是" : "否"]);
  }
  if (captureValue.occlusion_detection === "unavailable") {
    captureRows.push(["遮挡检测", "不可用（不推断为未遮挡）"]);
  }
  if (typeof captureValue.trusted === "boolean") {
    captureRows.push(["可信帧", captureValue.trusted ? "是" : "否"]);
  }
  if (contextValid !== undefined) {
    captureRows.push(["上下文有效", contextValid ? "是" : "否"]);
  }
  if (contextReason) {
    captureRows.push(["上下文原因", reasonText(contextReason)]);
  }
  const contextAge = formatFrameAge(captureValue.context_age_ms);
  if (contextAge) {
    captureRows.push(["上下文龄", contextAge]);
  }
  if (captureValue.snapshot_seq !== undefined && captureValue.snapshot_seq !== null) {
    captureRows.push(["快照序号", formatNumber(captureValue.snapshot_seq)]);
  }
  if (classificationValid !== undefined) {
    captureRows.push(["分类可信", classificationValid ? "是" : "否"]);
  }

  // 事件行：先用 identity 精确关联 dispatcher 时间线，关联不上就单列 unknown。
  const eventIdentities = recentEvents.map((event) => {
    const details = asRecord(asRecord(event).details);
    return String(details.identity || eventIdentity(event));
  });
  const identityCounts: Record<string, number> = {};
  for (const identity of eventIdentities) {
    identityCounts[identity] = (identityCounts[identity] ?? 0) + 1;
  }
  const eventRows: EventRow[] = recentEvents
    .map((raw, index) => {
      const event = asRecord(raw);
      const identity = eventIdentities[index];
      const output = eventOutputFor(
        identity,
        identityCounts[identity] ?? 0,
        dispatcherEntries,
      );
      return {
        id: `${String(event.fp ?? index)}-${index}`,
        time: formatClock(event.ts) || "—",
        kind: String(event.kind ?? "—"),
        priority: String(event.priority ?? "—"),
        summary: String(event.summary ?? ""),
        frameId: frameIdOf(event),
        outputKey: output.key,
        outputLabel: output.label,
        outputReason: output.reason,
      };
    })
    .reverse();

  const eventCounts = {
    accepted: eventRows.filter((row) => row.outputKey === "accepted").length,
    simulated: eventRows.filter((row) => row.outputKey === "simulated").length,
    unoutput: eventRows.filter((row) => row.outputKey === "unoutput").length,
    pending: eventRows.filter((row) => row.outputKey === "pending").length,
    unconfirmed: eventRows.filter((row) => row.outputKey === "unconfirmed").length,
    unmatched: eventRows.filter((row) => row.outputKey === "unmatched").length,
    other: eventRows.filter((row) => row.outputKey === "other").length,
  };

  const EVENT_FILTERS = [
    { value: "all", label: "全部事件" },
    { value: "accepted", label: "已提交给宿主" },
    { value: "simulated", label: "模拟/已记录" },
    { value: "unoutput", label: "未输出（丢弃/过期/失败/拒绝等）" },
    { value: "pending", label: "等待中" },
    { value: "unconfirmed", label: "回执未确认" },
    { value: "unmatched", label: "输出状态未知" },
    { value: "other", label: "其他/未识别状态（单独列出）" },
  ];
  const activeFilter = EVENT_FILTERS.some((item) => item.value === eventFilter)
    ? eventFilter
    : "all";
  const filteredEventRows =
    activeFilter === "all"
      ? eventRows
      : eventRows.filter((row) => row.outputKey === activeFilter);

  const EVENT_PAGE_SIZE = 6;
  const eventPageCount = Math.max(
    1,
    Math.ceil(filteredEventRows.length / EVENT_PAGE_SIZE),
  );
  const eventPageIndex = Math.min(
    Math.max(Number.isFinite(Number(eventPage)) ? Math.trunc(Number(eventPage)) : 0, 0),
    eventPageCount - 1,
  );
  const pagedEventRows = filteredEventRows.slice(
    eventPageIndex * EVENT_PAGE_SIZE,
    (eventPageIndex + 1) * EVENT_PAGE_SIZE,
  );

  const lifecycleRows = dispatcherEntries
    .slice()
    .reverse()
    .map((entry, index) => {
      const record = asRecord(entry);
      const detail = asRecord(record.detail);
      const status = String(record.outcome ?? "");
      return {
        id: `${String(record.ts ?? index)}-${index}`,
        time: formatClock(record.ts) || "—",
        kind: String(detail.kind ?? "—"),
        priority: detail.priority !== undefined ? String(detail.priority) : "—",
        status: `${(OUTCOME_LABELS[status] ?? status) || "—"}${status && OUTCOME_LABELS[status] ? `（${status}）` : ""}`,
        reason: String(record.reason ?? "") || "—",
        attempts: detail.attempts !== undefined ? String(detail.attempts) : "—",
      };
    });

  const LIFECYCLE_PAGE_SIZE = 8;
  const lifecyclePageCount = Math.max(
    1,
    Math.ceil(lifecycleRows.length / LIFECYCLE_PAGE_SIZE),
  );
  const lifecyclePageIndex = Math.min(
    Math.max(
      Number.isFinite(Number(lifecyclePage)) ? Math.trunc(Number(lifecyclePage)) : 0,
      0,
    ),
    lifecyclePageCount - 1,
  );
  const pagedLifecycleRows = lifecycleRows.slice(
    lifecyclePageIndex * LIFECYCLE_PAGE_SIZE,
    (lifecyclePageIndex + 1) * LIFECYCLE_PAGE_SIZE,
  );

  const droppedCount = Number(dispatcherStats.dropped ?? 0) || 0;
  const expiredCount = Number(dispatcherStats.expired ?? 0) || 0;
  const failedCount = Number(dispatcherStats.failed ?? 0) || 0;
  const unconfirmedCount = Number(dispatcherStats.unconfirmed ?? 0) || 0;
  const suppressedCount = Number(dispatcherStats.suppressed ?? 0) || 0;
  const replacedCount = Number(dispatcherStats.replaced ?? 0) || 0;
  const purgedCount = Number(dispatcherStats.purged ?? 0) || 0;
  const pendingSlots = Number(delivery.pending ?? dispatcherStats.pending ?? 0) || 0;
  const breakerState = String(breaker.state ?? "");

  const readonlySettings: Array<[string, string]> = [
    [
      "数据同意",
      consent === undefined
        ? "状态未知 · 当前由 N.E.K.O 管理"
        : consent
          ? "已允许 · 当前由 N.E.K.O 管理"
          : "已关闭 · 当前由 N.E.K.O 管理",
    ],
    [
      "免打扰",
      config.do_not_disturb === undefined
        ? "状态未知 · 当前由 N.E.K.O 管理"
        : config.do_not_disturb
          ? "开启 · 当前由 N.E.K.O 管理"
          : "关闭 · 当前由 N.E.K.O 管理",
    ],
    ["闲聊间隔", formatSeconds(config.chatter_interval_seconds)],
    ["普通播报冷却", formatSeconds(config.cooldown_seconds)],
    ["关键播报冷却", formatSeconds(config.critical_cooldown_seconds)],
    ["最小事件优先级", formatNumber(config.min_priority)],
    ["用户聊天安静窗", formatSeconds(config.user_chat_quiet_window_seconds)],
    ["战斗输出安静窗", formatSeconds(config.battle_output_quiet_window_seconds)],
    ["输出背压窗", formatSeconds(config.battle_output_backpressure_seconds)],
    ["短回复上限", `${formatNumber(config.reply_max_chars)} 字`],
    ["事件 TTL", formatSeconds(config.battle_event_ttl_seconds)],
    ["性能档", String(config.perf_mode ?? "未知")],
    [
      "感知周期",
      formatFrameAge(Number(pipeline.tick_seconds) * 1000) || "未知",
    ],
  ];

  const modeOptions = [
    { value: "companion", label: "陪伴（闲聊搭子）" },
    { value: "coach", label: "教练（附思路提示）" },
    { value: "silent", label: "静默" },
  ];
  const modeSelectOptions = modeOptions.some((item) => item.value === mode)
    ? modeOptions
    : [{ value: mode, label: mode ? `未知：${mode}` : "未提供" }, ...modeOptions];

  const detectorRegistry = Array.isArray(detectors.registry)
    ? detectors.registry.map((item: any) => String(item))
    : [];
  const detectorLast = Array.isArray(detectors.last)
    ? detectors.last.map((item: any) => asRecord(item))
    : [];
  const detectorErrors = detectorLast.filter(
    (item) => String(item.outcome ?? "") === "error",
  );
  const matchTracker = asRecord(detectors.match_tracker);

  const diagRows: DiagRow[] = [];

  // 1. 游戏窗口
  let windowRow: DiagRow = {
    key: "window",
    title: "游戏窗口",
    status: "未监控",
    detail: startError ? `启动错误：${startError}` : "监控未启动，未读取游戏窗口。",
    suggestion: "点击“开始监控”。",
    tone: "neutral",
  };
  if (monitoring) {
    if (healthStatus === "process_not_found") {
      windowRow = {
        key: "window",
        title: "游戏窗口",
        status: "未找到游戏进程",
        detail: `采集原因：${reasonText(captureValue.reason) || "process_not_found"}`,
        suggestion: HEALTH_SUGGESTIONS.process_not_found,
        tone: "danger",
      };
    } else if (healthStatus === "window_not_found") {
      windowRow = {
        key: "window",
        title: "游戏窗口",
        status: "未找到游戏窗口",
        detail: `采集原因：${reasonText(captureValue.reason) || "window_not_found"}`,
        suggestion: HEALTH_SUGGESTIONS.window_not_found,
        tone: "danger",
      };
    } else if (minimized === true || healthStatus === "minimized") {
      windowRow = {
        key: "window",
        title: "游戏窗口",
        status: "窗口已最小化",
        detail: "采集报告窗口最小化；不读取旧帧。",
        suggestion: HEALTH_SUGGESTIONS.minimized,
        tone: "warning",
      };
    } else if (visible === false) {
      windowRow = {
        key: "window",
        title: "游戏窗口",
        status: "窗口不可见",
        detail: "窗口存在但当前不可见；识别可能暂停。",
        suggestion: "将游戏窗口切到前台或取消遮挡。",
        tone: "warning",
      };
    } else if (windowFound === true || captureValue.process_name || captureValue.window_title) {
      const parts = [
        captureValue.process_name ? `进程 ${captureValue.process_name}` : "",
        foreground === true ? "前台" : foreground === false ? "非前台" : "",
        visible === true ? "可见" : visible === false ? "不可见" : "",
      ].filter(Boolean);
      windowRow = {
        key: "window",
        title: "游戏窗口",
        status: "窗口已找到",
        detail: parts.length > 0 ? parts.join(" · ") : "宿主报告窗口存在。",
        suggestion:
          foreground === false ? "将游戏窗口切到前台，避免识别暂停。" : "",
        tone: "success",
      };
    } else if (hasCapture) {
      windowRow = {
        key: "window",
        title: "游戏窗口",
        status: "窗口状态未知",
        detail: `采集状态：${healthLabel}`,
        suggestion: "刷新状态或拉取诊断。",
        tone: "neutral",
      };
    } else {
      windowRow = {
        key: "window",
        title: "游戏窗口",
        status: "状态未知",
        detail: "宿主未提供窗口或采集字段。",
        suggestion: "刷新状态或拉取诊断。",
        tone: "neutral",
      };
    }
  }
  diagRows.push(windowRow);

  // 2. 画面获取
  let captureRow: DiagRow = {
    key: "capture",
    title: "画面获取",
    status: "未监控",
    detail: "监控未启动，未请求画面。",
    suggestion: "点击“开始监控”。",
    tone: "neutral",
  };
  if (monitoring) {
    if (reading === true) {
      const parts = [
        captureValue.backend ? `后端 ${captureValue.backend}` : "",
        captureValue.width || captureValue.height
          ? `${captureValue.width ?? "?"}×${captureValue.height ?? "?"}`
          : "",
        frameAge ? `帧龄 ${frameAge}` : "",
      ].filter(Boolean);
      captureRow = {
        key: "capture",
        title: "画面获取",
        status: healthStatus === "degraded" ? "降级采集中" : "可读帧",
        detail: parts.length > 0 ? parts.join(" · ") : "宿主报告 reading=true。",
        suggestion: HEALTH_SUGGESTIONS[healthStatus] ?? "",
        tone: healthStatus === "degraded" ? "warning" : "success",
      };
    } else if (reading === false) {
      captureRow = {
        key: "capture",
        title: "画面获取",
        status: healthLabel,
        detail: `原因：${reasonText(captureValue.reason) || "未提供"}`,
        suggestion: HEALTH_SUGGESTIONS[healthStatus] ?? "检查采集窗口与前台状态。",
        tone: healthTone(healthStatus),
      };
    } else {
      captureRow = {
        key: "capture",
        title: "画面获取",
        status: "状态未知",
        detail: "宿主未提供 reading 字段。",
        suggestion: "刷新状态或拉取诊断。",
        tone: "neutral",
      };
    }
  }
  diagRows.push(captureRow);

  // 3. 对局状态
  let matchRow: DiagRow = {
    key: "match",
    title: "对局状态",
    status: "未监控",
    detail: "监控未启动，未建立对局上下文。",
    suggestion: "点击“开始监控”。",
    tone: "neutral",
  };
  if (monitoring) {
    if (userPaused) {
      matchRow = {
        key: "match",
        title: "对局状态",
        status: "用户暂停解说",
        detail: "采集与监控保持运行；识别与推送暂停。",
        suggestion: "在设置中点击“恢复解说”。",
        tone: "warning",
      };
    } else if (contextValid === false) {
      matchRow = {
        key: "match",
        title: "对局状态",
        status: "上下文失效",
        detail: `原因：${reasonText(contextReason) || "未提供"}`,
        suggestion: CONTEXT_SUGGESTIONS[contextReason] ?? "等待可信帧与场景重新确认。",
        tone: "warning",
      };
    } else if (classificationValid === false) {
      matchRow = {
        key: "match",
        title: "对局状态",
        status: "分类不可信",
        detail: `场景 ${scenarioText}${scenarioReason ? ` · ${scenarioReason}` : ""}`,
        suggestion: "等待画面稳定/场景重新确认；不使用旧帧推断。",
        tone: "warning",
      };
    } else if (pipelinePaused) {
      matchRow = {
        key: "match",
        title: "对局状态",
        status: "识别暂停",
        detail: `原因：${reasonText(pauseReason) || "未提供"}`,
        suggestion: CONTEXT_SUGGESTIONS[pauseReason] ?? "等待采集与上下文恢复。",
        tone: "warning",
      };
    } else if (inMatch === true) {
      matchRow = {
        key: "match",
        title: "对局状态",
        status: "对局识别中",
        detail: [
          `场景 ${scenarioText}`,
          gameNumber > 0 ? `第 ${gameNumber} 局` : "",
          sessionId ? `会话 ${clip(sessionId, 18)}` : "",
        ]
          .filter(Boolean)
          .join(" · "),
        suggestion: "",
        tone: "success",
      };
    } else if (contextValid === true) {
      matchRow = {
        key: "match",
        title: "对局状态",
        status: "不在对局",
        detail: `场景 ${scenarioText}${scenarioReason ? ` · ${scenarioReason}` : ""}`,
        suggestion: "进入对局后识别与解说才会运行。",
        tone: "neutral",
      };
    } else {
      matchRow = {
        key: "match",
        title: "对局状态",
        status: "状态未知",
        detail: "宿主未提供对局或上下文状态。",
        suggestion: "刷新状态或拉取诊断。",
        tone: "neutral",
      };
    }
  }
  diagRows.push(matchRow);

  // 4. 事件识别
  let detectorRow: DiagRow;
  if (!detectorLoadedKnown) {
    detectorRow = {
      key: "detector",
      title: "事件识别",
      status: "检测器状态未知",
      detail: "宿主未提供 detector_loaded，不推断检测器是否可用。",
      suggestion: "刷新状态或拉取诊断。",
      tone: "neutral",
    };
  } else if (!detectorLoaded) {
    detectorRow = {
      key: "detector",
      title: "事件识别",
      status: "检测器未加载",
      detail: "可选模型缺失；未加载时不猜测单位或事件。",
      suggestion: "检查可选检测模型是否随宿主安装。",
      tone: "warning",
    };
  } else if (detectorErrors.length > 0) {
    const firstError = String(
      detectorErrors[0].error ?? detectorErrors[0].outcome ?? "error",
    );
    detectorRow = {
      key: "detector",
      title: "事件识别",
      status: "检测器异常",
      detail: `${detectorErrors.length} 项错误 · ${clip(firstError, 60)}`,
      suggestion: "展开高级细节查看 detector 诊断。",
      tone: "danger",
    };
  } else if (detectors.paused === true) {
    detectorRow = {
      key: "detector",
      title: "事件识别",
      status: "识别暂停",
      detail: `原因：${reasonText(contextReason || pauseReason) || "上下文不可用"}`,
      suggestion:
        CONTEXT_SUGGESTIONS[contextReason || pauseReason] ??
        "等待可信帧与上下文恢复。",
      tone: "warning",
    };
  } else if (uiCapabilityKnown && supportedUiClasses.length < Number(uiDet.nc)) {
    detectorRow = {
      key: "detector",
      title: "事件识别",
      status: "仅启用已验证类别",
      detail: uiClassNames(supportedUiClasses) || "暂无可用类别",
      suggestion: "卡牌、单位移动和胜负识别仍未通过验证。",
      tone: "warning",
    };
  } else if (detectorLast.length > 0) {
    detectorRow = {
      key: "detector",
      title: "事件识别",
      status: "检测器就绪",
      detail: `已注册 ${detectorRegistry.length} 个检测器 · 最近 ${detectorLast.length} 条诊断`,
      suggestion: "",
      tone: "success",
    };
  } else {
    detectorRow = {
      key: "detector",
      title: "事件识别",
      status: "检测器已加载",
      detail: `已注册 ${detectorRegistry.length} 个检测器，等待下一帧。`,
      suggestion: "",
      tone: "success",
    };
  }
  diagRows.push(detectorRow);

  // 5. 消息发送
  const deliveryDetail = `已提交 ${formatNumber(dispatcherStats.submitted)} · 模拟 ${formatNumber(dispatcherStats.simulated)} · 丢弃 ${formatNumber(dispatcherStats.dropped)} · 过期 ${formatNumber(dispatcherStats.expired)} · 失败 ${formatNumber(dispatcherStats.failed)} · 未确认 ${formatNumber(dispatcherStats.unconfirmed)} · 待处理 ${formatNumber(pendingSlots)}`;
  let deliveryRow: DiagRow;
  if (!monitoring) {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "未监控",
      detail: `输出通道随监控停止。${dryRun ? "当前为 dry-run 模式。" : ""}`,
      suggestion: "开始监控后再观察发送。",
      tone: "neutral",
    };
  } else if (breakerState === "open") {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "发送熔断",
      detail: `连续失败 ${formatNumber(breaker.failures)} 次 · ${deliveryDetail}`,
      suggestion: "等待熔断恢复或检查宿主推送通道。",
      tone: "danger",
    };
  } else if (breakerState === "half_open") {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "发送熔断恢复中",
      detail: deliveryDetail,
      suggestion: "保持宿主通道可用，等待下一次探测。",
      tone: "warning",
    };
  } else if (gate.phase && String(gate.phase) !== "running") {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "宿主通道未运行",
      detail: `gate：${String(gate.phase)} · ${deliveryDetail}`,
      suggestion: "检查监控与宿主通道状态，必要时重新开始监控。",
      tone: "warning",
    };
  } else if (gate.paused === true) {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "宿主通道暂停",
      detail: deliveryDetail,
      suggestion: "恢复监控/宿主通道后再观察发送。",
      tone: "warning",
    };
  } else if (dryRun) {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "dry-run 模拟",
      detail: `完整仲裁/安全链已运行，最终不推送；只记录 events.jsonl。${deliveryDetail}`,
      suggestion: "需要真实输出时在设置关闭 dry-run。",
      tone: "warning",
    };
  } else if (!dryRunKnown) {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "dry-run 状态未知",
      detail: `宿主未提供 dry_run；不推断是否真实推送。${deliveryDetail}`,
      suggestion: "刷新状态或检查宿主配置。",
      tone: "warning",
    };
  } else if (!gate.phase) {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "宿主通道状态未知",
      detail: `宿主未提供 gate phase；不推断通道是否就绪。${deliveryDetail}`,
      suggestion: "刷新状态或拉取诊断。",
      tone: "warning",
    };
  } else if (consent === false) {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "数据同意关闭",
      detail: `llm_data_consent=false；真实推送会被拒绝。${deliveryDetail}`,
      suggestion: "在宿主设置中开启数据同意。",
      tone: "warning",
    };
  } else if (consent === undefined) {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "数据同意未知",
      detail: `宿主未提供 consent；不推断真实推送可用。${deliveryDetail}`,
      suggestion: "刷新状态或检查宿主设置。",
      tone: "warning",
    };
  } else {
    deliveryRow = {
      key: "delivery",
      title: "消息发送",
      status: "通道就绪",
      detail: deliveryDetail,
      suggestion: "",
      tone: "success",
    };
  }
  diagRows.push(deliveryRow);

  const tabId =
    tab === "history" || tab === "diagnostics" || tab === "settings"
      ? tab
      : tab === "records"
        ? "history"
        : "overview";

  const pullDiagnostics = async () => {
    const outcome = await call("monitor_diagnostics");
    if (!outcome.error) {
      setDiagResult(outcome.data);
      toast.success("诊断已拉取");
    }
  };

  const savePerceptionSettings = async () => {
    if (!settingsActionAvailable) return;
    if (!presetKnown || !fpsValid) {
      toast.error("请先修正采样值");
      return;
    }
    const patch: Record<string, unknown> = {
      perception_preset: presetValue,
    };
    // Send individual rates only when the user actually edited them.  A named
    // preset alone is resolved server-side, so an unloaded context cannot turn
    // a preset click into an accidental custom tuple.
    if (fpsDraft !== null) {
      patch.capture_fps = Number(fpsValue.capture);
      patch.change_fps = Number(fpsValue.change);
      patch.unit_fps = Number(fpsValue.unit);
      patch.card_fps = Number(fpsValue.card);
    }
    const outcome = await call("perception_settings_set", patch);
    if (!outcome.error) {
      setPresetDraft("");
      setFpsDraft(null);
      toast.success("设置已保存");
    }
  };

  const applyPresetDraft = (value: string) => {
    setPresetDraft(value);
    if (value === "custom") {
      setFpsDraft({ ...fpsValue });
      return;
    }
    const preset = asRecord(presetCatalog[value]);
    const mapped: Record<string, number | ""> = {};
    for (const field of FPS_FIELDS) {
      const number = Number(preset[field.configKey]);
      if (Number.isFinite(number)) mapped[field.key] = number;
    }
    if (Object.keys(mapped).length === FPS_FIELDS.length) {
      setFpsDraft(mapped);
    }
  };

  const renderHeader = () => (
    <ButtonGroup>
      <Button
        variant={tabId === "overview" ? "primary" : "ghost"}
        onClick={() => setTab("overview")}
      >
        概览
      </Button>
      <Button
        variant={tabId === "history" ? "primary" : "ghost"}
        onClick={() => setTab("history")}
      >
        记录
      </Button>
      <Button
        variant={tabId === "diagnostics" ? "primary" : "ghost"}
        onClick={() => setTab("diagnostics")}
      >
        诊断
      </Button>
      <Button
        variant={tabId === "settings" ? "primary" : "ghost"}
        onClick={() => setTab("settings")}
      >
        设置
      </Button>
    </ButtonGroup>
  );

  const renderSettings = () => {
    const presetOptions = presetKnown
      ? PERCEPTION_PRESET_OPTIONS
      : [
          { value: presetValue, label: presetValue ? `未知：${presetValue}` : "未知" },
          ...PERCEPTION_PRESET_OPTIONS,
        ];
    const disabled = !settingsActionAvailable;
    return (
      <Stack gap={12}>
        <Card title="采样">
          <Stack gap={10}>
            <Field label="预设">
              <Select
                value={presetValue}
                disabled={disabled}
                options={presetOptions}
                onChange={applyPresetDraft}
              />
            </Field>
            <Grid columns={2} gap={10}>
              {FPS_FIELDS.map((field) => (
                <Field
                  key={field.key}
                  label={`${field.label} FPS（${minOf(field)}..${capOf(field)}）`}
                >
                  <NumberInput
                    value={fpsValue[field.key]}
                    min={minOf(field)}
                    max={capOf(field)}
                    step={1}
                    disabled={disabled}
                    onChange={(value) => {
                      setFpsDraft({
                        ...fpsValue,
                        [field.key]: value === "" ? "" : Number(value),
                      });
                      setPresetDraft("custom");
                    }}
                  />
                </Field>
              ))}
            </Grid>
            <Text dim>
              顺序为采集 / 变化检测 / 界面识别 / 卡牌识别。修改单项会切换为自定义；
              服务端按 30 / 15 / 12 / 12 的硬上限复核，超出会被拒绝。
            </Text>
          </Stack>
        </Card>

        <Card title="保存">
          <Stack gap={10}>
            <ButtonGroup>
              <Button
                variant="primary"
                disabled={disabled || !presetKnown || !fpsValid}
                onClick={() => {
                  void savePerceptionSettings();
                }}
              >
                保存设置
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setPresetDraft("");
                  setFpsDraft(null);
                }}
              >
                恢复当前值
              </Button>
            </ButtonGroup>
            <Text dim>
              采样值点击“保存设置”后生效。
              {!settingsActionAvailable
                ? " 宿主未提供 perception_settings_set，当前仅显示只读配置。"
                : ""}
            </Text>
          </Stack>
        </Card>

        <Card title="由 N.E.K.O 管理（只读）">
          <Stack gap={10}>
            <Text dim>
              数据同意、免打扰、节奏和冷却由宿主设置管理；这里只显示实际值，不提供假开关。
            </Text>
            <Grid columns={2} gap={12}>
              <KeyValue rows={readonlySettings.slice(0, 7)} />
              <KeyValue rows={readonlySettings.slice(7)} />
            </Grid>
          </Stack>
        </Card>
      </Stack>
    );
  };

  const renderOverview = () => {
    const caption = !hasCapture
      ? "宿主未提供采集状态"
      : reading === undefined
        ? "采集出帧状态未知"
        : reading
          ? `${healthLabel} · 可读帧`
          : `${healthLabel} · 无有效帧`;
    return (
      <Stack gap={12}>
        <Card title="状态">
          <Stack gap={8}>
            <Stack direction="row" gap={8}>
              <StatusBadge
                tone={monitoring ? (userPaused ? "warning" : "success") : "neutral"}
              >
                {monitoring
                  ? userPaused
                    ? "监控运行中 · 解说暂停"
                    : "监控运行中"
                  : "监控已停止"}
              </StatusBadge>
              {pipelinePaused && !userPaused && (
                <StatusBadge tone="warning">
                  识别暂停：{reasonText(pauseReason) || "原因未知"}
                </StatusBadge>
              )}
              <StatusBadge tone={dryRunBadgeTone}>{dryRunBadgeLabel}</StatusBadge>
            </Stack>
            <Stack direction="row" gap={8}>
              <StatusBadge tone={healthTone(healthStatus)}>{healthLabel}</StatusBadge>
              {contextValid !== undefined && (
                <StatusBadge tone={contextValid ? "success" : "warning"}>
                  {contextValid ? "上下文有效" : "上下文失效"}
                </StatusBadge>
              )}
              {mode && (
                <StatusBadge tone="neutral">
                  模式：{mode === "coach" ? "教练" : mode === "silent" ? "静默" : mode === "companion" ? "陪伴" : `未知（${mode}）`}
                </StatusBadge>
              )}
            </Stack>
            <Text dim>{caption}</Text>
          </Stack>
        </Card>

        <Card title="控制">
          <Stack gap={10}>
            <Grid columns={2} gap={10}>
              <Field label="模式">
                <Select
                  value={mode}
                  disabled={!can("mode_set")}
                  options={modeSelectOptions}
                  onChange={(value: string) => {
                    if (!modeOptions.some((item) => item.value === value)) return;
                    void call("mode_set", { mode: value });
                  }}
                />
              </Field>
              <Field label="输出">
                <Switch
                  checked={dryRun}
                  disabled={!can("dry_run_set")}
                  label={
                    dryRunKnown
                      ? dryRun
                        ? "dry-run（仅模拟，不推送）"
                        : "真实推送"
                      : "dry-run 状态未知"
                  }
                  onChange={(checked: boolean) => {
                    void call("dry_run_set", { enabled: checked });
                  }}
                />
              </Field>
            </Grid>
            <Stack gap={8}>
              <ButtonGroup>
                <Button
                  variant="primary"
                  disabled={monitoring || !can("monitor_start")}
                  onClick={() => call("monitor_start")}
                >
                  开始监控
                </Button>
                <Button
                  variant="ghost"
                  disabled={!monitoring || userPaused || !can("monitor_pause")}
                  onClick={() => call("monitor_pause")}
                >
                  暂停解说
                </Button>
                <Button
                  variant="ghost"
                  disabled={!monitoring || !userPaused || !can("monitor_resume")}
                  onClick={() => call("monitor_resume")}
                >
                  恢复解说
                </Button>
              </ButtonGroup>
              <ButtonGroup>
                <Button
                  variant="ghost"
                  disabled={!monitoring || !can("monitor_stop")}
                  onClick={() => call("monitor_stop")}
                >
                  停止监控
                </Button>
                <Button
                  variant="ghost"
                  disabled={!can("monitor_status")}
                  onClick={() => call("monitor_status")}
                >
                  刷新
                </Button>
              </ButtonGroup>
            </Stack>
            {startError && <Text dim>启动错误：{startError}</Text>}
            <Text dim>
              暂停只停识别与推送，采集保持运行；停止监控会停止采集。
            </Text>
          </Stack>
        </Card>

        <Card title="采集与对局">
          <Stack gap={8}>
            <Stack direction="row" gap={8}>
              <StatusBadge tone={healthTone(healthStatus)}>{healthLabel}</StatusBadge>
              {reading !== undefined && (
                <StatusBadge tone={reading ? "success" : "warning"}>
                  {reading ? "可读帧" : "无有效帧"}
                </StatusBadge>
              )}
              {minimized === true && <StatusBadge tone="warning">已最小化</StatusBadge>}
              {userPaused && <StatusBadge tone="warning">解说已暂停</StatusBadge>}
            </Stack>
            {captureRows.length > 0 && <KeyValue rows={captureRows} />}
            <KeyValue rows={fpsMeasuredRows} />
            <Divider />
            {snapshotTrusted ? (
              <KeyValue
                rows={[
                  ["场景", scenarioText],
                  ["对局中", trustedInMatch ? "是" : "否"],
                  ["回合", formatNumber(trustedState.turn)],
                  ["行动方", trustedInMatch && typeof trustedState.my_turn === "boolean"
                    ? trustedState.my_turn ? "我方" : "对方" : "未知"],
                  ["我方 HQ", trustedInMatch ? formatNumber(trustedState.my_hq) : "未知"],
                  ["敌方 HQ", trustedInMatch ? formatNumber(trustedState.enemy_hq) : "未知"],
                  ["手牌", trustedInMatch ? formatNumber(trustedState.hand) : "未知"],
                  ["快照帧", formatNumber(snapshotFrame.frame_id)],
                ]}
              />
            ) : (
              <Text dim>
                暂无可信画面，不展示血量、回合或行动方；不用旧帧推断对局。
              </Text>
            )}
            {scenarioReason && <Text dim>场景原因：{reasonText(scenarioReason)}</Text>}
            <Text dim>
              无有效帧、未知/轮抓分类或用户暂停时，识别与解说安全暂停。
              面板只展示后端报告，不保证绕过遮挡或最小化。
            </Text>
          </Stack>
        </Card>

        <Card title={MODEL_CARD_TITLE}>
          <Stack gap={8}>
            <Stack direction="row" gap={8}>
              <StatusBadge tone="neutral">{MODEL_UI_DET_TITLE}</StatusBadge>
              <StatusBadge tone={uiDetBadge.tone}>{uiDetBadge.label}</StatusBadge>
            </Stack>
            <KeyValue rows={uiDetRows} />
            <KeyValue rows={[
              ["已启用识别", uiCapabilityKnown
                ? uiClassNames(supportedUiClasses) || "暂无" : "未知"],
              ["未通过验证", uiCapabilityKnown
                ? uiClassNames(unsupportedUiClasses) || "无" : "未知"],
            ]} />
            <Divider />
            <Stack direction="row" gap={8}>
              <StatusBadge tone="neutral">{MODEL_CARD_CLS_TITLE}</StatusBadge>
              <StatusBadge tone={cardClsBadge.tone}>{cardClsBadge.label}</StatusBadge>
            </Stack>
            <KeyValue rows={cardClsRows} />
            <KeyValue
              rows={[[MODEL_ROW_TRUSTED_COUNT, trustedCardCountText]]}
            />
            {models.trusted_card_count === 0 && (
              <Text dim>尚无卡牌通过真实画面验收，当前不会报告具体卡名。</Text>
            )}
            <Text dim>{MODEL_TRUSTED_HINT}</Text>
          </Stack>
        </Card>

        <Card title={RECOGNIZED_CARD_TITLE}>
          <Stack gap={8}>
            {recognizedCards.length === 0 ? (
              <Text dim>{RECOGNIZED_EMPTY_TEXT}</Text>
            ) : (
              <DataTable
                columns={[
                  { key: "instance", title: RECOGNIZED_TABLE_COLUMNS.instance, width: 64 },
                  { key: "name", title: RECOGNIZED_TABLE_COLUMNS.name },
                  { key: "region", title: RECOGNIZED_TABLE_COLUMNS.region, width: 96 },
                  { key: "conf", title: RECOGNIZED_TABLE_COLUMNS.conf, width: 80 },
                ]}
                rows={recognizedCards
                  .slice(-8)
                  .reverse()
                  .map((raw) => {
                    const card = asRecord(raw);
                    return {
                      instance: String(card.observation_id ?? card.frame_id ?? "—"),
                      name: String(card.name ?? "").trim() !== ""
                        ? clip(String(card.name), 40)
                        : RECOGNIZED_UNKNOWN_NAME,
                      region: CARD_REGION_LABELS[String(card.region ?? "")] ??
                        (String(card.region ?? "") || "—"),
                      conf: formatConfidence(card.conf),
                    };
                  })}
              />
            )}
            <Text dim>{RECOGNIZED_HINT}</Text>
          </Stack>
        </Card>

        <Card title="最近事件与输出">
          <Stack gap={8}>
            <Grid columns={2} gap={8}>
              <Stack direction="row" gap={8}>
                <StatusBadge tone="success">{eventCounts.accepted} 已提交给宿主</StatusBadge>
                <StatusBadge tone="warning">{eventCounts.simulated} 模拟</StatusBadge>
                <StatusBadge tone="danger">{eventCounts.unoutput} 未输出</StatusBadge>
              </Stack>
              <Stack direction="row" gap={8}>
                <StatusBadge tone="neutral">{eventCounts.pending} 等待</StatusBadge>
                <StatusBadge tone="neutral">{eventCounts.unconfirmed} 未确认</StatusBadge>
                <StatusBadge tone="neutral">{eventCounts.unmatched} 状态未知</StatusBadge>
                {eventCounts.other > 0 && (
                  <StatusBadge tone="neutral">{eventCounts.other} 其他</StatusBadge>
                )}
              </Stack>
            </Grid>
            {recentEvents.length === 0 ? (
              <Text dim>
                暂无事件。开一局（或用 dry-run 对照）后这里会滚动显示。
              </Text>
            ) : (
              <DataTable
                columns={[
                  { key: "time", title: "时间", width: 90 },
                  { key: "kind", title: "事件", width: 120 },
                  { key: "summary", title: "摘要" },
                  { key: "output", title: "输出（时间线）", width: 190 },
                ]}
                rows={eventRows.slice(0, 5).map((row) => ({
                  time: row.time,
                  kind: row.kind,
                  summary: clip(row.summary, 72) || "—",
                  output: row.outputLabel,
                }))}
              />
            )}
            <Button variant="ghost" onClick={() => setTab("history")}>
              查看全部记录
            </Button>
            <Text dim>
              “已提交给宿主”只表示 SDK 本地提交，不保证 LLM 生成或 TTS 播放；
              没有同一 identity 的回执时显示“状态未知”，不推断已播报。
            </Text>
          </Stack>
        </Card>
      </Stack>
    );
  };

  const renderRecords = () => (
    <Stack gap={12}>
      <Card title="事件识别（recent_events）">
        <Stack gap={8}>
        <Grid columns={2} gap={8}>
          <Stack direction="row" gap={8}>
            <StatusBadge tone="neutral">事件 {eventRows.length}</StatusBadge>
            <StatusBadge tone="success">已提交 {eventCounts.accepted}</StatusBadge>
            <StatusBadge tone="warning">模拟 {eventCounts.simulated}</StatusBadge>
            <StatusBadge tone="danger">未输出 {eventCounts.unoutput}</StatusBadge>
          </Stack>
          <Stack direction="row" gap={8}>
            <StatusBadge tone="neutral">等待 {eventCounts.pending}</StatusBadge>
            <StatusBadge tone="neutral">未确认 {eventCounts.unconfirmed}</StatusBadge>
            <StatusBadge tone="neutral">状态未知 {eventCounts.unmatched}</StatusBadge>
            {eventCounts.other > 0 && (
              <StatusBadge tone="neutral">其他 {eventCounts.other}</StatusBadge>
            )}
          </Stack>
        </Grid>
        <Stack gap={8}>
          <Stack direction="row" gap={8}>
            <Field label="筛选">
              <Select
                value={activeFilter}
                onChange={(value: string) => {
                  setEventFilter(value);
                  setEventPage(0);
                }}
                options={EVENT_FILTERS}
              />
            </Field>
          </Stack>
          {pagedEventRows.length === 0 ? (
            <Text dim>当前筛选下没有事件。</Text>
          ) : (
            <DataTable
              columns={[
                { key: "time", title: "时间", width: 90 },
                { key: "kind", title: "事件", width: 120 },
                { key: "priority", title: "优先级", width: 66 },
                { key: "frame", title: "帧", width: 66 },
                { key: "summary", title: "摘要" },
                { key: "output", title: "输出（时间线）", width: 190 },
                { key: "reason", title: "依据/原因", width: 220 },
              ]}
              rows={pagedEventRows.map((row) => ({
                time: row.time,
                kind: row.kind,
                priority: row.priority,
                frame: row.frameId,
                summary: clip(row.summary, 76) || "—",
                output: row.outputLabel,
                reason: clip(row.outputReason, 64) || "—",
              }))}
            />
          )}
          <Stack direction="row" gap={8}>
            <Button
              variant="ghost"
              disabled={eventPageIndex <= 0}
              onClick={() => setEventPage(Math.max(0, eventPageIndex - 1))}
            >
              上一页
            </Button>
            <Text dim>
              第 {eventPageIndex + 1}/{eventPageCount} 页 · {filteredEventRows.length} 条
            </Text>
            <Button
              variant="ghost"
              disabled={eventPageIndex >= eventPageCount - 1}
              onClick={() =>
                setEventPage(Math.min(eventPageCount - 1, eventPageIndex + 1))
              }
            >
              下一页
            </Button>
          </Stack>
          <Text dim>
            输出状态只按事件的 identity 与 dispatcher 时间线精确关联；无法唯一归属时显示“状态未知”，不做时间邻近推断。
            recent_events 窗口最多 10 条；下方 dispatcher 统计与时间线覆盖更长的生命周期。
          </Text>
          <Tip>
            “已提交给宿主”仅代表 SDK 本地提交，不保证 LLM 生成或 TTS 播放；“模拟/已记录”只存在于 dry-run；“未输出”只包含丢弃、过期、清除、替换、失败、抑制或拒绝。
          </Tip>
        </Stack>
        </Stack>
      </Card>

      <Card title="发送生命周期（dispatcher）">
        <Stack gap={10}>
          <Stack direction="row" gap={8}>
            <StatusBadge tone="success">
              已提交 {formatNumber(dispatcherStats.submitted)}
            </StatusBadge>
            <StatusBadge tone="warning">
              模拟 {formatNumber(dispatcherStats.simulated)}
            </StatusBadge>
            <StatusBadge tone="danger">
              未输出 {formatNumber(
                droppedCount + expiredCount + failedCount + suppressedCount +
                  replacedCount + purgedCount +
                  (Number(dispatcherStats.route_unavailable ?? 0) || 0),
              )}
            </StatusBadge>
            <StatusBadge tone="neutral">
              未确认 {formatNumber(unconfirmedCount)}
            </StatusBadge>
            <StatusBadge tone="neutral">
              待处理槽 {formatNumber(pendingSlots)}
            </StatusBadge>
          </Stack>

          <Grid columns={2} gap={12}>
            <KeyValue
              rows={[
                ["已提交给宿主", formatNumber(dispatcherStats.submitted)],
                ["模拟（dry-run）", formatNumber(dispatcherStats.simulated)],
                ["丢弃", formatNumber(dispatcherStats.dropped)],
                ["过期", formatNumber(dispatcherStats.expired)],
                ["被替换", formatNumber(dispatcherStats.replaced)],
                ["已清除", formatNumber(dispatcherStats.purged)],
                ["发送失败", formatNumber(dispatcherStats.failed)],
                ["已抑制", formatNumber(dispatcherStats.suppressed)],
                ["未确认", formatNumber(dispatcherStats.unconfirmed)],
                ["路由不可用", formatNumber(dispatcherStats.route_unavailable)],
              ]}
            />
            <KeyValue
              rows={[
                ["入队", formatNumber(dispatcherStats.queued)],
                ["待处理槽", formatNumber(pendingSlots)],
                ["等待槽", formatNumber(dispatcherStats.waiting)],
                ["延后", formatNumber(dispatcherStats.deferred)],
                ["重试", formatNumber(dispatcherStats.retried)],
                ["安静窗", formatNumber(dispatcherStats.quiet_window)],
                ["read 已提交", formatNumber(dispatcherStats.read_submitted)],
                ["read 合并", formatNumber(dispatcherStats.read_coalesced)],
                ["read 失败", formatNumber(dispatcherStats.read_failed)],
                ["read 抑制", formatNumber(dispatcherStats.read_suppressed)],
              ]}
            />
          </Grid>

          {lifecycleRows.length === 0 ? (
            <Text dim>
              暂无 dispatcher 时间线；开一局或点“诊断”里的“拉取诊断”后查看。
            </Text>
          ) : (
            <DataTable
              columns={[
                { key: "time", title: "时间", width: 90 },
                { key: "kind", title: "事件", width: 120 },
                { key: "priority", title: "优先级", width: 66 },
                { key: "status", title: "生命周期", width: 150 },
                { key: "reason", title: "原因", width: 180 },
                { key: "attempts", title: "尝试", width: 56 },
              ]}
              rows={pagedLifecycleRows.map((row) => ({
                time: row.time,
                kind: row.kind,
                priority: row.priority,
                status: row.status,
                reason: clip(row.reason, 56) || "—",
                attempts: row.attempts,
              }))}
            />
          )}
          <Stack direction="row" gap={8}>
            <Button
              variant="ghost"
              disabled={lifecyclePageIndex <= 0}
              onClick={() =>
                setLifecyclePage(Math.max(0, lifecyclePageIndex - 1))
              }
            >
              上一页
            </Button>
            <Text dim>
              第 {lifecyclePageIndex + 1}/{lifecyclePageCount} 页 · {lifecycleRows.length} 条
            </Text>
            <Button
              variant="ghost"
              disabled={lifecyclePageIndex >= lifecyclePageCount - 1}
              onClick={() =>
                setLifecyclePage(
                  Math.min(lifecyclePageCount - 1, lifecyclePageIndex + 1),
                )
              }
            >
              下一页
            </Button>
          </Stack>

          <Text dim>
            dispatcher 只保留一个 pending 槽；queued/等待/重试不是成功。submitted 才是宿主回执中的接受，且仍是 SDK 本地提交；failed/expired/dropped/replaced/purged 不等于输出。
          </Text>
        </Stack>
      </Card>

      <Card title="dry-run 模拟记录">
        {simulatedRecords.length === 0 ? (
          <Text dim>暂无模拟记录。dry-run 下产生的事件会记录在此，不会推送。</Text>
        ) : (
          <DataTable
            columns={[
              { key: "time", title: "时间", width: 90 },
              { key: "kind", title: "事件", width: 120 },
              { key: "status", title: "状态", width: 90 },
              { key: "reason", title: "原因" },
            ]}
            rows={simulatedRecords
              .slice()
              .reverse()
              .map((raw) => {
                const record = asRecord(raw);
                return {
                  time: formatClock(record.timestamp) || "—",
                  kind: String(record.kind ?? "—"),
                  status: String(record.status ?? "—"),
                  reason: String(record.reason ?? "—"),
                };
              })}
          />
        )}
      </Card>
    </Stack>
  );

  const renderDiagnostics = () => (
    <Stack gap={12}>
      <Stack direction="row" gap={8}>
        <Button
          variant="primary"
          onClick={() => {
            void pullDiagnostics();
          }}
        >
          拉取诊断
        </Button>
        <StatusBadge tone="neutral">
          {diagResult ? "五项检查：实时 status · 高级细节：诊断快照" : "五项检查：实时 status"}
        </StatusBadge>
      </Stack>

      <Card title="五项检查">
        <Stack gap={12}>
          {diagRows.map((row) => (
            <Stack key={row.key} gap={4}>
              <Stack direction="row" gap={8}>
                <StatusBadge tone={row.tone}>{row.status}</StatusBadge>
                <Text>{row.title}</Text>
              </Stack>
              <Text dim>{row.detail}</Text>
              {row.suggestion && <Text dim>建议：{row.suggestion}</Text>}
            </Stack>
          ))}
        </Stack>
      </Card>

      <Text dim>
        检查项只展示后端报告的状态与原因；遮挡、生成和 TTS 播放不在面板断言范围内。
      </Text>

      <Button
        variant="ghost"
        onClick={() => setDiagAdvanced(!diagAdvanced)}
      >
        {diagAdvanced
          ? "收起高级细节"
          : "展开高级细节（timeline / detector / delivery / gate / last_snapshot）"}
      </Button>

      {diagAdvanced && (
        <Stack gap={12}>
          <Card title="timeline（原始时间线）">
            <Stack gap={8}>
              {droppedEntries > 0 && (
                <Text dim>较早的 {droppedEntries} 条记录已被环形缓冲丢弃。</Text>
              )}
              {diagnostics.length === 0 ? (
                <Text dim>
                  暂无时间线；点击“拉取诊断”可获取最多 50 条后端诊断记录。
                </Text>
              ) : (
                <DataTable
                  columns={[
                    { key: "time", title: "时间", width: 90 },
                    { key: "stage", title: "阶段", width: 90 },
                    { key: "outcome", title: "结果", width: 130 },
                    { key: "kind", title: "事件", width: 120 },
                    { key: "frame", title: "帧", width: 60 },
                    { key: "reason", title: "原因" },
                    { key: "attempts", title: "尝试", width: 56 },
                  ]}
                  rows={diagnostics
                    .slice()
                    .reverse()
                    .map((raw) => {
                      const record = asRecord(raw);
                      const detail = asRecord(record.detail);
                      const stage = String(record.stage ?? "");
                      const outcome = String(record.outcome ?? "");
                      return {
                        time: formatClock(record.ts) || "—",
                        stage: `${(STAGE_LABELS[stage] ?? stage) || "—"}${stage && STAGE_LABELS[stage] ? `（${stage}）` : ""}`,
                        outcome: `${(OUTCOME_LABELS[outcome] ?? outcome) || "—"}${outcome && OUTCOME_LABELS[outcome] ? `（${outcome}）` : ""}`,
                        kind: String(detail.kind ?? "—"),
                        frame: formatNumber(detail.frame_id),
                        reason: String(record.reason ?? "") || "—",
                        attempts:
                          detail.attempts !== undefined
                            ? String(detail.attempts)
                            : "—",
                      };
                    })}
                />
              )}
            </Stack>
          </Card>

          <Card title="detector（检测器）">
            <Stack gap={8}>
              <Stack direction="row" gap={6}>
                {detectorRegistry.length === 0 ? (
                  <Text dim>未注册检测器。</Text>
                ) : (
                  detectorRegistry.map((name) => (
                    <StatusBadge key={name} tone="neutral">
                      {name}
                    </StatusBadge>
                  ))
                )}
              </Stack>
              {detectorLast.length === 0 ? (
                <Text dim>暂无检测器诊断。</Text>
              ) : (
                <DataTable
                  columns={[
                    { key: "name", title: "检测器", width: 160 },
                    { key: "duration", title: "耗时", width: 80 },
                    { key: "outcome", title: "结果", width: 100 },
                    { key: "error", title: "错误" },
                  ]}
                  rows={detectorLast.map((record, index) => ({
                    name: String(record.name ?? `#${index + 1}`),
                    duration: formatFrameAge(record.duration_ms) || "—",
                    outcome: String(record.outcome ?? "—"),
                    error: String(record.error ?? "") || "—",
                  }))}
                />
              )}
              <KeyValue
                rows={[
                  ["paused", boolText(detectors.paused)],
                  ["对局中", boolText(matchTracker.in_match)],
                  ["局号", formatNumber(matchTracker.game_number)],
                  ["会话", matchTracker.session_id ? clip(matchTracker.session_id, 24) : "—"],
                  ["已投递", formatNumber(matchTracker.delivered)],
                  ["等待重基线", boolText(matchTracker.rebase_pending)],
                ]}
              />
            </Stack>
          </Card>

          <Card title="delivery（dispatcher / gate）">
            <Stack gap={10}>
              <Grid columns={2} gap={12}>
                <KeyValue
                  rows={[
                    ["submitted", formatNumber(dispatcherStats.submitted)],
                    ["simulated", formatNumber(dispatcherStats.simulated)],
                    ["dropped", formatNumber(dispatcherStats.dropped)],
                    ["expired", formatNumber(dispatcherStats.expired)],
                    ["failed", formatNumber(dispatcherStats.failed)],
                    ["unconfirmed", formatNumber(dispatcherStats.unconfirmed)],
                    ["suppressed", formatNumber(dispatcherStats.suppressed)],
                    ["replaced", formatNumber(dispatcherStats.replaced)],
                  ]}
                />
                <KeyValue
                  rows={[
                    ["queued", formatNumber(dispatcherStats.queued)],
                    ["pending", formatNumber(pendingSlots)],
                    ["waiting", formatNumber(dispatcherStats.waiting)],
                    ["deferred", formatNumber(dispatcherStats.deferred)],
                    ["retried", formatNumber(dispatcherStats.retried)],
                    ["quiet_window", formatNumber(dispatcherStats.quiet_window)],
                    ["read_submitted", formatNumber(dispatcherStats.read_submitted)],
                    ["read_failed", formatNumber(dispatcherStats.read_failed)],
                  ]}
                />
              </Grid>
              <Divider />
              <KeyValue
                rows={[
                  ["breaker", breakerState || "—"],
                  ["breaker failures", formatNumber(breaker.failures)],
                  ["gate phase", String(gate.phase ?? "—")],
                  ["gate epoch", formatNumber(gate.epoch)],
                  ["gate simulating", boolText(gate.simulating)],
                  ["gate paused", boolText(gate.paused)],
                  ["gate consent", consent === undefined ? "—" : consent ? "是" : "否"],
                  [
                    "用户聊天安静窗",
                    formatSeconds(
                      quietWindow.user_chat_seconds ??
                        config.user_chat_quiet_window_seconds,
                    ),
                  ],
                  [
                    "战斗输出安静窗",
                    formatSeconds(
                      quietWindow.battle_output_seconds ??
                        config.battle_output_quiet_window_seconds,
                    ),
                  ],
                  [
                    "输出背压窗",
                    formatSeconds(
                      quietWindow.backpressure_seconds ??
                        config.battle_output_backpressure_seconds,
                    ),
                  ],
                ]}
              />
              {simulatedRecords.length > 0 && (
                <Stack gap={6}>
                  <Text dim>dry-run 模拟记录（仅本地，不推送）</Text>
                  <DataTable
                    columns={[
                      { key: "time", title: "时间", width: 90 },
                      { key: "kind", title: "事件", width: 120 },
                      { key: "status", title: "状态", width: 90 },
                      { key: "reason", title: "原因" },
                    ]}
                    rows={simulatedRecords
                      .slice()
                      .reverse()
                      .map((raw) => {
                        const record = asRecord(raw);
                        return {
                          time: formatClock(record.timestamp) || "—",
                          kind: String(record.kind ?? "—"),
                          status: String(record.status ?? "—"),
                          reason: String(record.reason ?? "—"),
                        };
                      })}
                  />
                </Stack>
              )}
            </Stack>
          </Card>

          <Card title="gate（回执记录）">
            <Stack gap={8}>
              <Text dim>
                gate 的 accepted/submitted 只表示 SDK 本地提交给宿主；回执不保证 LLM 生成或 TTS 播放。
              </Text>
              {gateRecords.length === 0 ? (
                <Text dim>
                  暂无 gate 回执；点击“拉取诊断”获取最多 30 条记录。
                </Text>
              ) : (
                <DataTable
                  columns={[
                    { key: "seq", title: "序号", width: 56 },
                    { key: "time", title: "时间", width: 90 },
                    { key: "intent", title: "意图", width: 90 },
                    { key: "status", title: "状态", width: 130 },
                    { key: "reason", title: "原因", width: 170 },
                    { key: "flags", title: "标记" },
                  ]}
                  rows={gateRecords
                    .slice()
                    .reverse()
                    .map((raw) => {
                      const record = asRecord(raw);
                      const flags = [
                        record.simulated ? "simulated" : "",
                        record.submitted ? "submitted" : "",
                        record.critical ? "critical" : "",
                        record.terminal ? "terminal" : "",
                      ].filter(Boolean);
                      return {
                        seq: formatNumber(record.seq),
                        time: formatClock(record.ts) || "—",
                        intent: String(record.intent ?? "—"),
                        status: String(record.status ?? "—"),
                        reason: String(record.reason ?? "") || "—",
                        flags: flags.join(" · ") || "—",
                      };
                    })}
                />
              )}
            </Stack>
          </Card>

          <Card title="last_snapshot（最后快照）">
            {Object.keys(diagSnapshotView).length === 0 ? (
              <Text dim>暂无最后快照。</Text>
            ) : (
              <KeyValue
                rows={[
                  ["序号", formatNumber(diagSnapshotView.seq)],
                  ["观测时间", formatClock(diagSnapshotView.observed_at) || "—"],
                  [
                    "采集状态",
                    String(diagCapture.status ?? "—"),
                  ],
                  [
                    "采集原因",
                    reasonText(diagCapture.reason) || "—",
                  ],
                  [
                    "帧有效",
                    boolText(diagFrame.valid),
                  ],
                  [
                    "分辨率",
                    `${formatNumber(diagFrame.width)}×${formatNumber(diagFrame.height)}`,
                  ],
                  [
                    "对局中",
                    boolText(diagState.in_match),
                  ],
                  ["回合", formatNumber(diagState.turn)],
                  ["我方 HQ", formatNumber(diagState.my_hq)],
                  ["敌方 HQ", formatNumber(diagState.enemy_hq)],
                  ["手牌", formatNumber(diagState.hand)],
                  ["单位数", `${asEntries(diagState.mine).length} / ${asEntries(diagState.enemy).length}`],
                ]}
              />
            )}
          </Card>

          <Card title="perception / models">
            <Grid columns={2} gap={12}>
              <KeyValue
                rows={[
                  ["采样预设", String(perception.perception_preset ?? perception.preset ?? "未知")],
                  ...fpsMeasuredRows,
                  ["帧证据", boolText(perception.evidence_enabled)],
                  ["调度 change", `${formatNumber(schedulerTasks.change?.actual)} / ${formatNumber(schedulerTasks.change?.missed)}`],
                  ["调度 unit", `${formatNumber(schedulerTasks.unit?.actual)} / ${formatNumber(schedulerTasks.unit?.missed)}`],
                  ["调度 card", `${formatNumber(schedulerTasks.card?.actual)} / ${formatNumber(schedulerTasks.card?.missed)}`],
                ]}
              />
              <KeyValue
                rows={[
                  [`${MODEL_UI_DET_TITLE} · 加载状态`, uiDetBadge.label],
                  [`${MODEL_UI_DET_TITLE} · ${MODEL_ROW_CLASS_COUNT}`, formatNumber(uiDet.nc)],
                  [`${MODEL_UI_DET_TITLE} · ${MODEL_ROW_CLASS_MAP}`, classMapVersionText(classMapVersions.ui_classes)],
                  [`${MODEL_CARD_CLS_TITLE} · 加载状态`, cardClsBadge.label],
                  [`${MODEL_CARD_CLS_TITLE} · ${MODEL_ROW_CLASS_COUNT}`, formatNumber(cardCls.nc)],
                  [`${MODEL_CARD_CLS_TITLE} · ${MODEL_ROW_CLASS_MAP}`, classMapVersionText(classMapVersions.card_classes)],
                  [MODEL_ROW_TRUSTED_COUNT, trustedCardCountText],
                  [`${MODEL_UI_DET_TITLE} · ${MODEL_ROW_DIAGNOSTIC}`, uiDetBadge.diagnostic ? clip(uiDetBadge.diagnostic, 60) : "—"],
                  [`${MODEL_CARD_CLS_TITLE} · ${MODEL_ROW_DIAGNOSTIC}`, cardClsBadge.diagnostic ? clip(cardClsBadge.diagnostic, 60) : "—"],
                  ["已确认卡牌数", String(recognizedCards.length)],
                  ["单位状态", String(unitLast.status ?? "未知")],
                  ["单位存活/确认", `${formatNumber(unitTracker.alive)} / ${formatNumber(unitTracker.confirmed)}`],
                  ["位置帧", formatNumber(unitTracker.frame_id)],
                ]}
              />
            </Grid>
          </Card>
        </Stack>
      )}
    </Stack>
  );

  return (
    <Page title="KARDS 陪玩">
      <Stack gap={12}>
        {renderHeader()}
        {tabId === "overview" && renderOverview()}
        {tabId === "history" && renderRecords()}
        {tabId === "diagnostics" && renderDiagnostics()}
        {tabId === "settings" && renderSettings()}
      </Stack>
    </Page>
  );
}
