// 拟股喵喵 · 市场面板（P8 清丽重构版）
//
// Hosted TSX 规则（.agent/skills/neko-plugin/references/hosted-ui-authoring.md）：
//   * 只从 `@neko/plugin-ui` 导入；不引 npm 包；不引外部裸模块；
//   * **禁**动态 `import()`、**禁** class 组件、**禁** Context / portals /
//     Suspense / dangerouslySetInnerHTML；
//   * 相对依赖必须留在插件根目录内，且 ≤32 文件 / ≤512 KiB（本面板是单文件）；
//   * 运行时是**轻量 React-like 渲染器**（不是 React），JSX 工厂是 `h`；
//   * iframe sandbox = allow-scripts，不要依赖普通页面全局。
//
// 三条**渲染器限制**直接决定了本文件的写法：
//   1. 宿主用 `document.createElement()` 建元素 → **画不了真 SVG**，
//      所以 K 线是用 div 拼出来的（见 `Kline`），小折线用 `<canvas>` 画
//      （宿主渲染器支持 `canvas`，且 `ref` 会挂到真实 DOM 节点上）。
//   2. `CommonProps` 里**没有** `style` → 需要内联样式的地方直接用
//      导出的 `h()`（它的 props 是 `any`）。
//   3. `style` 传的是**对象**（运行时逐键写 `dom.style[key]`），
//      **不是** CSS 字符串；CSS 自定义属性（`--x`）用 `setProperty` 也支持。
//
// 0.8.1 起的**硬规矩**（Steam 宿主 0.9.0 上 0.8.0 把面板 UI 整个搞崩了）。
// P8.0 把视觉整个翻面成「清丽轻科技风」，但下面这些**崩溃防线一条都没破**：
//   * 零 ``position``（absolute / fixed）、零 ``inset``、零 ``z-index``；
//   * 零 ``filter``（**真毛玻璃做不了** —— 它靠 ``backdrop-filter``，属于
//     filter 家族，会给祖先建立 containing block 把宿主布局搞歪；这里用
//     「半透明白 + 淡阴影 + 16px 圆角」做质感近似）；
//   * 零 ``opacity``（半透明一律用 ``rgba`` 表达）；
//   * 零伪元素、零 ``@keyframes``；唯一的 ``animation`` 是 ``animation: none``；
//   * 渐变**只允许纵向**（180deg）—— 0.8.0 的巨大 X 线条是
//     ``position:absolute + inset:0 + 对角渐变`` 做的，这里既没有定位也没有
//     对角渐变；
//   * 卡片与布局容器之间**不许有自定义 div 包裹层**（卡片必须是布局容器的
//     **直接**子元素）—— 0.8.0 的第四条根因；
//   * 「病危」只有两种表现：面板顶部一条红色 `Alert` + 关键数字染红。
//
// P7.2 的**极简重构**（先做减法，再塞抽象文案）：
//   * K 线搬到**面板最上方**，占满全宽，当主视觉；
//   * 行情表删掉成本价 / 可卖 / 占比 / 标签，只留「名称 + 走势小折线 +
//     现价 + 超大红绿涨跌」；
//   * 持仓表删掉数量 / 均价 / 可卖 / 占比 / 标签，只留「名称 + 当前市值 +
//     大号红绿盈亏」；
//   * 操作区改成**一键梭哈 / 一键割肉**（输入框、下拉框全部收掉）；
//   * 猫娘弹幕变成**常驻在面板底部**的固定框（文档流，不悬浮）。
//
// P8.0 的**清丽轻科技风**（详见 RRM_CSS 的注释）：
//   * 主题靠**重新定义宿主 CSS 变量**换皮 —— 宿主所有组件（卡片 / 按钮 /
//     徽标 / 文本）一次性变成浅色系，不用逐个组件覆写；
//   * 行情表换成**方块卡片网格**（宿主 `Columns` 的 fluid 模式，窗口宽就是
//     4 列、窄就是 2 列）；
//   * 操作台换成**两个并排的胶囊大按钮**（各占一半宽）；
//   * 群友（社区名册）在名字旁边挂一个**小圆头像**（`ui/assets/` 的内联 PNG，
//     加载失败退回专属 Emoji，绝不破图）。
//
// 局中隐藏（P4 §14 / P5 方案 B）：本面板**不显示** heat、反转日、新闻真假。
// 载荷里没有的东西，面板也拿不到 —— 面板只渲染 `state` 里的白名单字段。
import {
  ActionButton,
  Alert,
  Button,
  Card,
  Columns,
  Divider,
  Field,
  Grid,
  Inline,
  Input,
  KeyValue,
  List,
  Modal,
  Page,
  Select,
  Slider,
  Stack,
  StatusBadge,
  Step,
  Steps,
  Text,
  h,
  showToast,
  useEffect,
  useLocalState,
  useMemo,
  useRef,
  useScrollIntoView,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps, Tone } from "@neko/plugin-ui"

import { AVATARS, MEOW_LOAN } from "./assets/avatars"

type KlinePoint = [number, number]

/**
 * 宿主传进来的翻译函数（``PluginSurfaceProps["t"]``）。
 *
 * P8.0：方块卡片是**独立组件**（不 Panel 的闭包里），所以要显式把
 * ``t`` 传下去 —— **绝不在组件里写裸中文字面量**（P8 红线：所有新增
 * 文字必须进 i18n 词典）。
 */
type Translate = (key: string, params?: Record<string, any>) => string

/**
 * 把后端的**内部枚举**翻译成界面文案。
 *
 * P8.1 事故：结算页把 ``starved`` / ``quick`` 这种英文枚举直接怼在玩家脸上，
 * 连 ``{mode}`` 占位符都原样漏了出来。这里统一走 i18n 词典，
 * **翻不到就显示破折号** —— 宁可空着，也不能让玩家看见内部 key。
 */
function enumText(t: Translate, prefix: string, raw: unknown): string {
  const key = String(raw || "")
  if (!key) return "—"
  // 宿主 t() 在 key 缺失时返回 key 本身；这里用 defaultValue="" 把它变空串。
  const text = t(`${prefix}.${key}`, { defaultValue: "" })
  return text || "—"
}

/**
 * P8.1：空闲态的猫娘**开场嘲讽**候选键。
 *
 * 面板每 3 秒轮询一次，所以这个随机值必须**在组件挂载时定一次**，
 * 不能每次渲染都摇 —— 否则玩家会眼睁睁看着话术自己变。
 */
const IDLE_TAUNT_KEYS = [
  "panel.idle_taunt_1",
  "panel.idle_taunt_2",
  "panel.idle_taunt_3",
  "panel.idle_taunt_4",
  "panel.idle_taunt_5",
  "panel.idle_taunt_6",
]

type Stock = {
  id: string
  name: string
  /** P7.2：名册群友在面板上的显示名（「星辰·定海神针股」）。 */
  display_name?: string
  roster_alias?: string
  roster_title?: string
  price: number
  prev_price: number
  change: number
  tag: string
  tag_label: string
  tag_color: string
  alive: boolean
  kline?: KlinePoint[]
  /** P7.2：走势小折线的数据（降采样过）。 */
  spark?: KlinePoint[]
}

type Position = {
  stock_id: string
  name: string
  display_name?: string
  amount: number
  avg_cost: number
  price: number
  market_value: number
  unrealized_pnl: number
  sellable: number
  weight: number
  tag: string
  tag_label: string
  alive: boolean
}

type Catgirl = {
  alive: boolean
  cash: number
  positions_value: number
  position_count: number
  days_survived: number
  today_action: string
}

type NewsItem = {
  id: string
  kind: string
  kind_label: string
}

type PendingAction = {
  kind?: string
  stock_ids?: string[]
  amount?: number
  price?: number
  reason?: string
}

type AnalysisResult = {
  stock_id: string
  name: string
  price: number
  tag_label: string
  rating: string
  chasing: boolean
  position_weight: number
  market_note: string
  analyses_left: number
  note: string
}

/** P7：猫娘弹幕。 */
type Danmaku = {
  id?: number
  kind?: string
  text?: string
  at?: number
}

/** P7：社区名册里的群友。 */
type RosterMember = {
  stock_id?: string
  alias?: string
  ticker?: string
  title?: string
  persona?: string
  price?: number
  display_name?: string
}

/** P7.2：导演台的「赛博弹窗轰炸」。 */
type Boom = {
  active?: boolean
  count?: number
  seconds_left?: number
  until?: number
  /** P7.3：本次轰炸**本来该持续多久**（秒）—— 前端据此算自己的硬上限。 */
  duration?: number
}

/** P7：视觉降级三档 + 弹幕 + 导演台 + 名册（**纯表现层**）。 */
type Visual = {
  stage?: "rich" | "broke" | "dying"
  critical?: boolean
  critical_emoji?: string
  stage_thresholds?: {
    broke_day?: number
    dying_assets?: number
    panic_distance?: number
  }
  emoji_sea_size?: number
  danmaku?: Danmaku[]
  director?: { enabled?: boolean; queued?: number }
  boom?: Boom
  roster?: RosterMember[]
}

/**
 * P10：节奏（游戏速度 + 自动/手动推进）。
 *
 * **纯表现**：这里只有"现实里等多久"的换算，一个游戏数值都没有。
 */
type Pace = {
  speed?: "slow" | "normal" | "fast"
  auto?: boolean
  base_interval_seconds?: number
  interval_seconds?: number
  ticks_per_day?: number
  day_seconds?: number
  multiplier?: number
}

/** P10：新手引导状态（已读标记记在 player_id 上）。 */
type Onboarding = {
  seen?: boolean
  auto_open?: boolean
  player_id?: string
}

/**
 * P11：猫娘主动搭话（外置交互引导）的状态。
 *
 * **纯表现**：这里只有"猫娘有没有开口"和"兜底弹窗有没有新消息"，
 * 一个游戏数值都没有 —— 主动搭话**不参与**任何游戏规则。
 */
type Meow = {
  /** 首次加载引导已经打过招呼了（全局一次）。 */
  greeted?: boolean
  /** 现在能不能呼叫（防连点冷却中就是 false）。 */
  can_call?: boolean
  /** 还要等几秒才能再呼叫。 */
  call_cooldown?: number
  /** 兜底弹窗的序号 —— 变了就说明"有一条新的猫娘消息"。 */
  fallback_seq?: number
  /** 兜底弹窗的台词（宿主接不住主动搭话时才有）。 */
  fallback_line?: string
  /** 兜底弹窗的原因（``push_failed`` 等）。 */
  fallback_reason?: string
}

/**
 * P12：手续费率（**只读** ``engine_config.fees``）。
 *
 * 面板要算"预计买入多少 / 预计花费多少 / 剩余现金"，用的是**引擎那一套
 * 费率**（``buy_fee`` / ``sell_fee``）—— 抄进载荷只是为了让面板**本地**
 * 算，拖滑块时零往返。**不改任何费率**（读，不是写）。
 */
type Fees = {
  buy_rate?: number
  sell_rate?: number
  stamp_tax_rate?: number
}

/**
 * P12：剧情状态（四幕叙事 + 外置 Yui）。
 *
 * **纯表现**：面板只显示"这一幕的文字"和（降级时的）选项按钮 ——
 * 猫娘在外置气泡里说，**面板不画立绘、不画对话框**。
 */
type StoryChoice = {
  id?: string
  label?: string
  hint?: string
}

type Story = {
  /** 每演一幕 +1 —— 面板靠它判断"这一幕是不是新的"。 */
  seq?: number
  /** 当前待选的章节 id（选完就清空）。 */
  pending?: string
  title?: string
  /** 纯文本场景（**不含任何立绘**）。 */
  scene?: string
  /** 聊天卡片投递成功了吗？失败时选项由面板接管。 */
  card_ok?: boolean
  /** 只有 ``card_ok === false`` 时才有内容。 */
  choices?: StoryChoice[]
  tags?: string[]
  debt?: { amount?: number; due_day?: number; paid?: number } | null
}

type State = {
  stage?: string
  day?: number
  tick?: number
  phase?: string
  ticks_per_day?: number
  ticks_to_settle?: number
  mode?: string
  mode_name?: string
  /**
   * P20.0：玩家自报的名字（**戏名**，不是宿主 session 里的真名）。
   *
   * 空闲态用它回填输入框；局中与结算页用它显示"这是谁"。
   */
  player_name?: string
  /**
   * P20.0：命中的名字彩蛋。
   *
   * **只有** `key` / `category` / `label_key` —— 没有倍率、没有数值。
   * `label_key` 指向 i18n 里的名号文案（面板自己查表显示徽标）。
   */
  name_egg?: { key?: string; category?: string; label_key?: string } | null
  alive?: boolean
  cash?: number
  total_assets?: number
  /** P7.2：玩家自己的开局钱（用来算「到现在赚/亏了多少」）。 */
  initial_cash?: number
  living_cost?: number
  next_bill?: number
  bill_gap?: number
  missed_bills?: number
  distance_to_kill?: number | null
  concentration?: number
  positions?: Position[]
  stocks?: Stock[]
  kline_stock_ids?: string[]
  catgirl?: Catgirl | null
  news_today?: NewsItem[]
  pending_action?: PendingAction | null
  analyses_left?: number
  settlement?: Record<string, any> | null
  achievements?: Achievements | null
  notice?: string
  /** P7：视觉降级 / 弹幕 / 导演台 / 名册。 */
  visual?: Visual
  /** P10：节奏（游戏速度 / 自动·手动）。 */
  pace?: Pace
  /** P10：新手引导（要不要自动弹《玩法说明》）。 */
  onboarding?: Onboarding
  /** P11：猫娘主动搭话的状态。 */
  meow?: Meow
  /** P12：手续费率（只读）。 */
  fees?: Fees
  /** P12：剧情状态。 */
  story?: Story
  /** P16.0：连击（**纯展示**；不含任何游戏数值）。 */
  combo?: { streak?: number; best?: number; enabled?: boolean }
  /**
   * P18.0：动态教程。
   *
   * **只有功能性 UI 文案的 i18n key** —— 猫娘的台词**不在这里**
   * （它在面板外的外置气泡里，由 AI 生成）。
   */
  tutorial?: {
    active?: boolean
    phase?: string
    tier?: string
    index?: number
    total?: number
    waiting_gate?: boolean
    gate_wait_key?: string
    step?: { key?: string; anchor?: string; title_key?: string; body_key?: string }
  }
  /**
   * P19.0：导演段的「进度」（``[director] show_progress``）。
   *
   * **这个键只在开关打开时才会出现在载荷里** —— 开关关闭时是
   * ``undefined``，面板也**完全不渲染**。
   *
   * ⚠️ 这里的 ``hype`` 就是引擎局中隐藏的那个量。面板**不许**说出
   * 它的引擎字段名（P5 契约 §4.1 的静态 lint：面板源码里不许出现
   * ``heat`` / ``seed`` / ``reversal``），所以后端换了键名 ——
   * 面板只是显示一个后端决定给的数字，**闸门在后端**。
   */
  director_progress?: {
    hype?: number
    chapter?: number
    chapter_total?: number
    chapter_title?: string
    seen?: number
  }
}

/** P5.5 改动 4：天桥虚惊一场的三屏。 */
type DeathScene = {
  screen1?: string
  screen2?: string
  screen3?: string
}

/** P5.5 改动 5/7：称号图鉴。 */
type Achievements = {
  unlocked?: { title?: string; epitaph?: string; identity_epitaph?: string }[]
  locked?: { title?: string; hint?: string }[]
  total?: number
  unlocked_count?: number
}

type AchievementRow = {
  unlocked: boolean
  title: string
  text: string
}

const RATING_TONE: Record<string, Tone> = {
  green: "success",
  yellow: "warning",
  red: "danger",
}

const PHASE_TONE: Record<string, Tone> = {
  honeymoon: "success",
  trap: "warning",
  slaughter: "danger",
}

/**
 * 涨绿跌红 —— P8.0 换成**柔和**配色：涨是薄荷绿，跌是珊瑚粉。
 *
 * 这两个值和 RRM_CSS 里的 ``--success`` / ``--danger`` 是**同一套**：
 * canvas 画不出来 CSS 变量（canvas 只认字面色值），所以这里写死同样的
 * 十六进制，两边必须同步改。
 */
const TREND_UP = "#35BFA0"
const TREND_DOWN = "#F2677F"

/**
 * P8.0：**清丽轻科技风**（从暗色调赛博朋克彻底翻面）。
 *
 * 做法（很重要，别写成逐组件覆写）：
 * 宿主的主题是靠 **CSS 变量**驱动的（`--text` / `--muted` / `--primary` /
 * `--success` / `--danger` / `--border` / `--surface` / `--surface-strong` /
 * `--radius-md`）。所以只要在 `.rrm-root` 上**重新定义这些变量**，宿主
 * 所有组件（卡片 / 按钮 / 徽标 / 文本 / 分隔线）会**一次性**换成浅色系 ——
 * 既不用碰宿主源码，也不用逐个组件写覆写。
 *
 * 剩下的几条硬规矩**一条都没破**（0.8.0 崩 UI 的五条根因全部规避）：
 *
 *   * 零 ``position``（absolute / fixed）、零 ``inset``、零 ``z-index``；
 *   * 零 ``filter``（所以**真毛玻璃做不了** —— 毛玻璃靠 ``backdrop-filter``，
 *     属于 filter 家族。这里用「半透明白 + 淡阴影 + 16px 圆角」做**质感近似**，
 *     视觉上是清透的，但绝不会给祖先建立 containing block）；
 *   * 零 ``opacity``（半透明一律用 ``rgba`` 表达）；
 *   * 零伪元素（``content:`` 一律不写）；
 *   * 唯一的 ``animation`` 是 ``animation: none !important``（关掉宿主入场淡入）；
 *   * 渐变**只允许纵向**（180deg）—— 0.8.0 的巨大 X 线条是 ``position:absolute
 *     + inset:0 + 对角渐变`` 做的，这里既没有定位也没有对角渐变。
 */
const RRM_CSS = `
*, *::before, *::after { box-sizing: border-box; }
.rrm-root, .rrm-root *, .rrm-root *::before, .rrm-root *::after { animation: none !important; }
.rrm-root .neko-stack > *, .rrm-root .neko-card-body > * { min-width: 0; }
.rrm-root .neko-key-value-value { overflow-wrap: anywhere; }

/* ── ① 主题变量覆写（P9.0：轻盈透明的二次元科技风；P10 微调）──────────────
   色值取自设计图：背景 #EAF4FE → #F6FBFF，卡片 #F0F7FF（近白蓝），
   绿胶囊 #E7F8F0、粉胶囊 #FBE6EA，强调蓝 #5B8FF9，正文深蓝 #1F2D3D。
   毛玻璃用「半透明白 + 柔光投影」近似（宿主不支持背景模糊效果）。

   P10 微调（视觉任务三）：底色再**通透**一档 —— 顶部那片天蓝压得更透，
   中段快速褪成近白，卡片因此像是「浮」在浅蓝空气里而不是贴在色块上。 */
.rrm-root {
  --text: #1f2d3d;
  --muted: #7b8ca6;
  --primary: #5b8ff9;
  --success: #17916f;
  --danger: #d6486a;
  --border: rgba(142, 171, 219, 0.22);
  --surface: rgba(255, 255, 255, 0.85);
  --surface-strong: #ffffff;
  --bg: #e4efff;
  --radius-md: 20px;
  /* P21.0：全屏沉浸 —— 底色**叠两层**：
     ① 顶部一层很淡的蓝晕（180deg，往下化开）；
     ② 原来的主渐变（浅蓝 → 近白）。
     两层都只用纵向渐变（斜向的那种 0.8.0 炸过一次），
     background-size: cover 让它在大屏下铺满而不是被拉扁。 */
  background:
    linear-gradient(180deg, rgba(91, 143, 249, 0.12) 0%, rgba(91, 143, 249, 0) 38%),
    linear-gradient(180deg, #e4efff 0%, #f4faff 48%, #fbfdff 100%);
  background-size: cover;
  background-repeat: no-repeat;
  color: var(--text);
  /* P13：让**原生控件**跟着浅色主题走 —— 滑块没填满的那截轨道原本是
     浏览器深色模式的默认黑，在浅色面板上非常刺眼。
     （P12 滑块默认停在 100%，看不出来；P13 停在真实仓位上就露出来了。） */
  color-scheme: light;
}

/* ── ② 毛玻璃卡片：半透明白底 + 20px 大圆角 + **柔而大**的蓝影 ──────────
   P10 微调：阴影从「10px 30px / α0.15」改成「18px 46px / α0.15」——
   位移更大、模糊更大、透明度不变 ⇒ 影子更散、更软，卡片于是有「浮起来」的轻盈感。 */
.rrm-root .neko-card {
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 20px;
  box-shadow: 0 18px 46px rgba(133, 163, 214, 0.15);
}
.rrm-root .neko-card-header { padding: 16px 18px 0; }
.rrm-root .neko-card-body { padding: 14px 18px 18px; gap: 16px; }
.rrm-root .neko-card-title { color: #1f2d3d; font-size: 16px; font-weight: 700; letter-spacing: 0.5px; }
.rrm-root .neko-heading { color: #1f2d3d; }
.rrm-root .neko-text { color: #6b7a90; }

/* ── ③ 页面标题：24px 加粗深蓝（设计图的标题规格）────────────────── */
.rrm-root .neko-page { gap: 16px; }
.rrm-root .neko-page-title { font-size: 24px; font-weight: 700; color: #1f2d3d; letter-spacing: 0.5px; }
.rrm-root .neko-page-subtitle { color: #a8bacd; font-size: 13px; }

/* ── ④ 状态徽标 = 浅蓝胶囊 Tag（像游戏里的标签）───────────────────── */
.rrm-root .neko-badge {
  background: #e4efff;
  border: 1px solid rgba(91, 143, 249, 0.16);
  color: #5b8ff9;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}
.rrm-root .neko-badge[data-tone="success"] { background: #e7f8f0; color: #17916f; border-color: rgba(23, 145, 111, 0.24); }
.rrm-root .neko-badge[data-tone="warning"] { background: #fff3de; color: #b3760f; border-color: rgba(240, 178, 74, 0.28); }
.rrm-root .neko-badge[data-tone="danger"] { background: #fde9ed; color: #d6486a; border-color: rgba(214, 72, 106, 0.26); }

/* ── ⑤ 大数字统计块（赚亏 / 现钱 / 身家）────────────────────────── */
.rrm-root .neko-stat {
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.65);
  border-radius: 18px;
  box-shadow: 0 14px 36px rgba(133, 163, 214, 0.14);
}
.rrm-root .neko-stat-label { color: #9fb2c6; font-size: 12px; }

/* ── ⑥ 行情方块卡：两层结构（名字 + 涨跌 / 走势 + 现价）──────────── */
.rrm-root .neko-card.rrm-tile {
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 18px;
  box-shadow: 0 14px 34px rgba(133, 163, 214, 0.15);
  padding: 12px 14px;
}
.rrm-root .neko-card.rrm-tile .neko-card-body { padding: 0; gap: 8px; }
.rrm-root .rrm-tile-head { display: flex; align-items: center; gap: 8px; width: 100%; }
.rrm-root .rrm-tile-foot { display: flex; align-items: center; gap: 10px; width: 100%; }
/* 迷你走势要**撑满**卡片宽度 —— 否则走势和现价之间会空一大片。 */
.rrm-root canvas.rrm-spark,
.rrm-root div.rrm-spark {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  height: 30px;
}
.rrm-root .rrm-stock-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  font-weight: 700;
  font-size: 13px;
  color: #3d5575;
}
.rrm-root .rrm-tile-name-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rrm-root .rrm-price {
  margin-left: auto;
  font-size: 15px;
  font-weight: 700;
  color: #3a5478;
  white-space: nowrap;
}
.rrm-root .rrm-change-pill {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 16px;
  font-weight: 800;
  line-height: 1.4;
  white-space: nowrap;
}
.rrm-root .rrm-change-up { background: #e7f8f0; color: #17916f; }
.rrm-root .rrm-change-down { background: #fde9ed; color: #d6486a; }
.rrm-root .rrm-change-flat { background: #eef3fa; color: #7b8ca6; }

/* ── ⑦ K 线：柱子带纵向渐变（设计图的「光影」），底下垫一层网格 ─────── */
.rrm-root .rrm-kline-wrap {
  background-image: repeating-linear-gradient(180deg, rgba(142, 171, 219, 0.10) 0px, rgba(142, 171, 219, 0.10) 1px, rgba(255, 255, 255, 0) 1px, rgba(255, 255, 255, 0) 40px);
}
.rrm-root .rrm-kline-bar-up { background: linear-gradient(180deg, #6fd8bd 0%, rgba(90, 200, 168, 0.22) 100%); }
.rrm-root .rrm-kline-bar-down { background: linear-gradient(180deg, #ff8fa4 0%, rgba(255, 107, 129, 0.22) 100%); }

/* P21.0：新手兜底的「幽灵提示」—— 从下面浮上来一点点。
   只用 transform + transition；那几样会让宿主布局崩掉的东西一个没用。 */
.rrm-root .rrm-ghost-hint {
  transform: translateY(5px);
  transition: transform 240ms ease-out;
}
.rrm-root .rrm-ghost-hint.rrm-ghost-hint-in { transform: translateY(0); }

/* ── ⑧ 操作台的两个大胶囊按钮：渐变质感 + 56px 高 + 18px 粗体 ─────── */
/* P21.0：全屏时按钮跟着面板一起变大（vh = iframe 视口高度，
   vw = iframe 宽度）—— 直播大屏上「掏钱买 / 割肉卖」要一眼就看见。
   clamp() 给上下限，窄窗口不至于挤爆。
   注意：这个注释在 CSS 模板字符串里，**一个反引号都不许出现**
   （P11.1 与 P12.0 各踩过一次：当场截断模板 ⇒ 面板白屏）。 */
.rrm-root .neko-button.rrm-pill {
  flex: 1 1 0;
  min-width: 0;
  height: clamp(56px, 7.5vh, 92px);
  padding: 0 16px;
  font-size: clamp(18px, 1.5vw, 28px);
  font-weight: 800;
  letter-spacing: 1px;
  border-radius: clamp(28px, 4vh, 46px);
  border: none;
  /* 真机教训：按钮被压窄时中文会**竖着排**（一字一行），难看且读不出来。
     锁死不换行，宁可两行空间也要横着排。 */
  white-space: nowrap;
}
.rrm-root .neko-button.rrm-pill-buy {
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  color: #16305c;
  box-shadow: 0 10px 24px rgba(123, 188, 255, 0.45);
}
.rrm-root .neko-button.rrm-pill-sell {
  background: linear-gradient(90deg, #ffc5d3 0%, #ff96b2 100%);
  color: #8a1e3c;
  box-shadow: 0 10px 24px rgba(255, 150, 178, 0.45);
}
/* 按钮下面那行小字（设计图里的 All In / Stop Loss 副标题） */
.rrm-root .rrm-pill-captions { display: flex; gap: 8px; width: 100%; }
.rrm-root .rrm-pill-caption {
  width: 48%;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.rrm-root .rrm-pill-caption-buy { color: #2f6fbf; }
.rrm-root .rrm-pill-caption-sell { color: #d2496e; }

/* ActionButton 会把按钮包进一个 div（class 名 neko-action-control），
   而那个壳在宿主里是 inline-grid（收缩到内容宽）—— 不改它的话，
   网格里的列会被撑歪，两个按钮就不是各占一半。
   这里把壳改成 grid（撑满列），里面的按钮再 width: 100%。
   注意：这段 CSS 在反引号模板字符串里，**注释里绝不能出现反引号**。 */
.rrm-root .neko-action-control {
  display: grid;
  align-items: stretch;
  min-width: 0;
}
.rrm-root .neko-action-control > .neko-button.rrm-pill { width: 100%; }

/* P8.1：两个胶囊按钮**紧凑并排**，各占 48% 宽（各留 2% + 间距在中间），
   整排**居中** —— 0.9.0 的 1fr 两列看着太散，像两张不相干的卡片。 */
.rrm-root .neko-grid.rrm-trade-row {
  grid-template-columns: repeat(2, minmax(0, 48%));
  justify-content: center;
}

/* ── P13：仓位控制（连续滑块 + 直输框）+ 实时预览 ──────────────────
   滑块是宿主**原生**的 input[type=range]（.neko-slider）—— 我们只调宽度，
   **不加任何定位、层叠、滤镜、透明**（0.8.0 的崩溃红线一条没破）。
   P13 把步进从 5% 改成 **1%**，并且**删掉了 25/50/75/100 那四档快捷键** ——
   仓位不再被量化成四档，玩家可以精确拖到 33% 或 67%。
   直输框也是宿主**原生**的 input（.neko-input），我们只调宽度与字号。 */
.rrm-root .neko-slider { width: 100%; }
/* P13：滑块轨道上色。
   宿主 UI Kit 用 accent-color 给**已填充**那段上色，但**没填充**那段
   走的是浏览器默认 —— 在这台机器的 Edge 上画出来是**纯黑**，放在浅色
   面板上非常刺眼（P12 滑块默认停在 100%，看不出来；P13 停在真实仓位上
   就露出来了）。
   ⇒ 这里用 CSS 变量 --rrm-trade-fill（由面板按当前比例写入）
   画一条 90 度渐变：左边是主色，右边是淡蓝。**没有**引入任何危险属性。 */
.rrm-root .rrm-trade-slider .neko-slider-input {
  /* **必须**先把原生外观去掉，作者写的 track / thumb 伪元素才会生效
     （否则 Chrome 用自己的内部绘制，作者的轨道色根本不认）。 */
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  height: 16px;
}
.rrm-root .rrm-trade-slider .neko-slider-input::-webkit-slider-runnable-track {
  height: 6px;
  border-radius: 3px;
  background: linear-gradient(
    90deg,
    var(--primary) 0%,
    var(--primary) var(--rrm-trade-fill, 0%),
    rgba(91, 143, 249, 0.2) var(--rrm-trade-fill, 0%),
    rgba(91, 143, 249, 0.2) 100%
  );
}
.rrm-root .rrm-trade-slider .neko-slider-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px;
  height: 16px;
  margin-top: -5px;
  border-radius: 50%;
  background: var(--primary);
  border: 2px solid #ffffff;
}
.rrm-root .neko-grid.rrm-trade-previews {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.rrm-root .neko-grid.rrm-trade-previews .neko-text {
  font-size: 12px;
  line-height: 1.5;
  color: #7b8ca6;
}
/* 直输框：两个框（股数 / 金额）平分一行，单位小字跟在框后。 */
.rrm-root .rrm-trade-inputs {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.rrm-root .neko-input.rrm-trade-input {
  flex: 1 1 0;
  min-width: 0;
  height: 28px;
  padding: 0 6px;
  font-size: 12px;
  border-radius: 8px;
}
.rrm-root .rrm-trade-input-unit {
  flex: 0 0 auto;
  font-size: 11px;
  color: #7b8ca6;
  white-space: nowrap;
}

/* ── P12：剧情沉浸模式（宿主原生 Modal size="full"）─────────────────
   宿主 Modal 的遮罩本来就是固定定位铺满整屏（**宿主自己的**），
   所以全面板覆盖**零危险 CSS**。这里只有**纯文本场景** ——
   猫娘在外置气泡里说，面板不画立绘、不画对话框。

   下面这两条只改**高度**（宿主那个 full 尺寸只撑宽、不撑高）：
   把弹窗拉满 92vh，body 自己滚动 —— 这就是"沉浸模式"该有的样子。
   （只用了 display / flex / height，**没有**定位、层叠、滤镜、透明。） */
.rrm-root .neko-modal[data-size="full"] {
  height: 92vh;
  display: flex;
  flex-direction: column;
}
.rrm-root .neko-modal[data-size="full"] .neko-modal-body {
  flex: 1 1 auto;
  max-height: none;
}
.rrm-root .rrm-story-scene {
  white-space: pre-wrap;
  font-size: 16px;
  line-height: 2;
}
.rrm-root .rrm-story-choices { display: grid; gap: 8px; }
.rrm-root .rrm-story-fallback-hint {
  font-size: 12px;
  color: #7b8ca6;
}
.rrm-root .neko-button.rrm-story-choice {
  width: 100%;
  height: 48px;
  padding: 0 14px;
  font-size: 15px;
  font-weight: 700;
  text-align: left;
}

/* P8.1：标的下拉框别孤零零横跨整屏 —— 限宽 + 居中。 */
.rrm-root .neko-inline.rrm-select-row { justify-content: center; }
.rrm-root .neko-inline.rrm-select-row .neko-select { max-width: 360px; }

/* P8.1：K 线卡的标的框同样限宽。它这行左边顶着卡片标题、右边跟着
   「拉这只的K线」按钮和行情文案，所以**不居中**（居中会和左对齐的卡片
   标题脱节），只把满宽的下拉框收成 360px。 */
.rrm-root .neko-inline.rrm-kline-row .neko-select { max-width: 360px; }

/* P8.1：K 线数据不足时的提示语 —— 浅灰、居中、不抢戏。 */
.rrm-root .rrm-kline-warming {
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

/* ── ⑨ 浅蓝胶囊 Tag（标题栏那一行）──────────────────────────────── */
.rrm-root .rrm-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 999px;
  background: #e4efff;
  border: 1px solid rgba(91, 143, 249, 0.16);
  color: #5b8ff9;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

/* ── ⑩ 破产配图：CSS 拼的扁平空钱包（不再用那张黄色土豆图）────────── */
.rrm-root .rrm-wallet-scene {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 4px 0 2px;
}
.rrm-root .rrm-wallet {
  display: flex;
  align-items: stretch;
  width: 200px;
  height: 124px;
  border-radius: 18px;
  overflow: hidden;
  background: linear-gradient(180deg, #eaf2ff 0%, #cfdefb 100%);
  border: 1px solid rgba(255, 255, 255, 0.75);
  box-shadow: 0 12px 28px rgba(142, 171, 219, 0.28);
}
.rrm-root .rrm-wallet-body {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.rrm-root .rrm-wallet-slot {
  width: 62px;
  height: 5px;
  border-radius: 999px;
  background: rgba(142, 171, 219, 0.35);
}
.rrm-root .rrm-wallet-tear { font-size: 26px; line-height: 1; }
.rrm-root .rrm-wallet-flap {
  width: 58px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #9cc0f5 0%, #7ba8f0 100%);
  border-left: 1px solid rgba(255, 255, 255, 0.55);
}
.rrm-root .rrm-wallet-clasp {
  width: 14px;
  height: 14px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 2px 6px rgba(60, 96, 160, 0.35);
}
.rrm-root .rrm-wallet-spark { font-size: 42px; line-height: 1; }

/* ── ⑪ 辅助按钮：缩小、变轻 ─────────────────────────────────────── */
.rrm-root .neko-button.rrm-mini {
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 650;
  border-radius: 999px;
}

/* ── ⑫ 群友头像（方块卡里的小圆头像）──────────────────────────── */
.rrm-root .rrm-avatar {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.9);
  vertical-align: middle;
  flex: 0 0 auto;
}

/* ── ⑬ P7.2 传下来的三档字体（只改字体，不改布局）──────────────────── */
.rrm-font-rich { font-family: "YouYuan", "Yuanti SC", "Hiragino Maru Gothic ProN", "Microsoft YaHei UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
.rrm-font-broke { font-weight: 300; }
.rrm-font-dying { font-weight: 500; }
.rrm-root .neko-button.rrm-escape { font-size: 11px; padding: 2px 8px; color: var(--muted); border-radius: 999px; }

/* ── ⑭ P10：玩法说明大按钮（空闲态最显眼的那一个）─────────────────────
   「怎么玩」必须比「开局」还要醒目 —— 新手第一眼看到的就是它。 */
.rrm-root .neko-button.rrm-guide-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: 100%;
  min-height: 62px;
  padding: 12px 22px;
  border-radius: 31px;
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  color: #16305c;
  box-shadow: 0 16px 38px rgba(123, 188, 255, 0.42);
}
.rrm-root .rrm-guide-btn-main { font-size: 18px; font-weight: 800; letter-spacing: 1px; }
.rrm-root .rrm-guide-btn-sub { font-size: 12px; font-weight: 600; letter-spacing: 0.3px; }
/* 标题栏里那个小链接（局中随时翻说明）。 */
.rrm-root .neko-button.rrm-guide-link {
  font-size: 11px;
  padding: 3px 11px;
  border-radius: 999px;
  color: var(--muted);
  font-weight: 650;
}

/* ── ⑯ P11：呼叫猫娘 + 拟态外置对话 ─────────────────────────────────── */
/* 标题栏里的【呼叫猫娘】小按钮（比《玩法说明》链接显眼一点点）。 */
.rrm-root .neko-button.rrm-meow-call {
  font-size: 11px;
  padding: 3px 11px;
  border-radius: 999px;
  font-weight: 700;
  color: #2b6ca8;
  background: rgba(164, 229, 255, 0.42);
  border: 1px solid rgba(123, 188, 255, 0.5);
}
/* 兜底弹窗里那条"消息"：左头像 + 右气泡。 */
.rrm-root .rrm-meow-thread {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.rrm-root .rrm-meow-avatar {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  line-height: 1;
  background: linear-gradient(180deg, #d8f0ff 0%, #bfe2ff 100%);
  border: 1px solid rgba(123, 188, 255, 0.45);
}
.rrm-root .rrm-meow-bubble {
  flex: 1 1 auto;
  padding: 11px 14px;
  border-radius: 16px;
  background: #eaf6ff;
  border: 1px solid rgba(123, 188, 255, 0.38);
  color: #1f2d3d;
  font-size: 13px;
  line-height: 1.6;
  box-shadow: 0 8px 22px rgba(133, 163, 214, 0.14);
}

/* ── ⑮ P10：玩法说明弹窗里的三步图文 ──────────────────────────────── */
/* P11.1：三步的**骨架**已经换回宿主的原生 Steps / Step 组件
   （neko-step / neko-step-index / neko-step-title 由宿主 styles.css 自己
   样式化），所以这里只剩下两行**正文**排版。
   注意：这段 CSS 整体是一个反引号模板字符串，注释里**绝对不能出现反引号**。 */
.rrm-guide-step-body { font-size: 13px; line-height: 1.8; color: #5b6b82; }
.rrm-guide-step-tip { font-size: 12px; line-height: 1.7; color: #8ba0bd; }
.rrm-guide-footer {
  font-size: 12px;
  color: #8ba0bd;
  text-align: center;
  letter-spacing: 0.5px;
}

/* ── ⑯ P10：节奏卡（游戏速度 + 自动/手动）─────────────────────────── */
.rrm-pace-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rrm-pace-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 13px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1px;
}
.rrm-pace-badge-auto {
  background: #e7f8f0;
  border: 1px solid rgba(23, 145, 111, 0.2);
  color: #17916f;
}
.rrm-pace-badge-manual {
  background: #fdf0e4;
  border: 1px solid rgba(185, 112, 42, 0.22);
  color: #b9702a;
}
.rrm-pace-hint { font-size: 13px; color: #5b6b82; }
.rrm-root .neko-button.rrm-speed-tag {
  width: 100%;
  padding: 9px 8px;
  border-radius: 999px;
  border: 1px solid rgba(142, 171, 219, 0.3);
  background: rgba(255, 255, 255, 0.78);
  color: #6b7a90;
  font-size: 13px;
  font-weight: 750;
  letter-spacing: 0.5px;
}
.rrm-root .neko-button.rrm-speed-tag-on {
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  border-color: rgba(255, 255, 255, 0.78);
  color: #16305c;
  box-shadow: 0 10px 24px rgba(123, 188, 255, 0.38);
}
.rrm-pace-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #8ba0bd;
}
.rrm-pace-actions { display: flex; gap: 10px; align-items: center; }
.rrm-root .neko-button.rrm-pace-toggle {
  flex: 0 0 auto;
  padding: 9px 18px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 750;
}
.rrm-root .neko-button.rrm-pace-advance {
  flex: 1 1 auto;
  min-height: 52px;
  border-radius: 26px;
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  border: 1px solid rgba(255, 255, 255, 0.72);
  color: #16305c;
  font-size: 17px;
  font-weight: 800;
  letter-spacing: 1px;
  box-shadow: 0 14px 32px rgba(123, 188, 255, 0.42);
}
.rrm-pace-note { font-size: 12px; line-height: 1.7; color: #8ba0bd; }

/* ── P18.0：动态聚焦引导（**零危险 CSS**）────────────────────────────
   圈 = outline + box-shadow（两个都不在禁词表里）；
   遮罩用**宿主自带的** Modal（宿主自己的全屏遮罩，不是我们写的）。
   这一段里**不出现**定位 / 层叠 / 滤镜 / 裁剪 / 淡入淡出 / 关键帧。── */
.rrm-tutorial-target {
  outline: 2px solid #7bbcff;
  outline-offset: 3px;
  border-radius: 14px;
  box-shadow: 0 0 0 5px rgba(123, 188, 255, 0.24);
}
.rrm-tutorial-coach {
  border-color: rgba(123, 188, 255, 0.55);
  box-shadow: 0 10px 28px rgba(123, 188, 255, 0.16);
}
.rrm-tutorial-steps .neko-step-index {
  background: linear-gradient(90deg, #a4e5ff 0%, #7bbcff 100%);
  color: #16305c;
}
`

function money(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  return Number(value).toFixed(2)
}

/**
 * P7.3：网贷弹窗的**本地超时**（秒）。
 *
 * 0.8.2 真机事故：弹窗唯一能关掉的路径是按钮的 `onResult`，而宿主
 * `ActionButton` 把 `onResult` 挡在 `await props.api.refresh()` 后面 ——
 * 刷新一失败，回调永远不来，弹窗永远关不掉，玩家被锁死在面板里。
 *
 * 现在有三道保险，任何一道都能把玩家放出来：
 *   1. **同步关窗**（点按钮的那一瞬间就把弹窗关掉，不等任何回调）；
 *   2. **本地倒计时**（本常量）：30 秒没人操作就自己关窗；
 *   3. **服务端看门狗**：30 秒没人操作，服务端自动结算
 *      （无限模式强制借入 / 经典模式直接破产）。
 */
const LOAN_PROMPT_TIMEOUT_SECONDS = 30

/**
 * P7.3：赛博弹窗轰炸的**客户端硬上限**（毫秒）。
 *
 * 0.8.2 用服务端的 `until` 时间戳减**客户端时钟**算剩余时间 —— 时钟偏了、
 * 或者面板载荷停止刷新，`boom.active` 就会永远为 true ⇒ 永久锁屏。
 *
 * 现在改成：从**第一次看见它**起，本地记一个截止时刻，到点无条件关窗。
 * 服务端说该持续多久就持续多久（`boom.duration`），但**只认第一次**，
 * 之后载荷再怎么刷都不会把这个上限往后推。
 */
const BOOM_HARD_LIMIT_MS = 5000

/**
 * P7.4：面板**自己轮询**上下文的节奏（毫秒）。
 *
 * 宿主**从不轮询** Hosted 面板的上下文 —— 它只在「插件启停 / 重载」和
 * 「面板自己发起动作之后」刷新。对一个每秒 tick 的实时游戏面板来说，这带来
 * 两个真问题：
 *
 * 1. **tick 是死的**：面板看着不动，玩家不知道行情在走；
 * 2. **面板会变陈旧**：这一局结束（或被别人结束）之后，面板还停在局中界面，
 *    玩家点「一键梭哈」只会拿到后端的 `not_joined`，而宿主把**任何**动作失败
 *    都当成「面板运行时错误」上报 —— 屏幕顶部弹一个黄色的
 *    「插件界面控件错误」覆盖层（0.8.3 真机事故）。
 *
 * 所以这里自己按固定节奏拉一次上下文。宿主的 `render` 走的是 reconcile
 * （组件实例不重建），本地状态（弹窗、倒计时、逃生确认）不会被打断。
 */
const PANEL_POLL_INTERVAL_MS = 3000

/**
 * P21.0：**三档 UI 密度**（渐进式简化 —— 把复杂藏起来，别在新手村吓人）。
 *
 * P16.0 只有「简单 / 完整」两档，前两关一起走简单面板。
 * P21.0 拆成三档，因为**入门档和街机档的诉求不一样**：
 *
 * | 档位 | 密度 | 诉求 |
 * | :-- | :-- | :-- |
 * | 街机 | ``minimal`` | 纯爽局：只留核心玩法区块，别吓跑人 |
 * | 入门 | ``guarded`` | 开始教生存：恢复水位条，但还不玩多标的计划 |
 * | 进阶 / 地狱 / 无尽 | ``full`` | 火力全开：压迫感该在那里出现 |
 *
 * **只影响面板的显示**（条件渲染），**不影响任何底层计算** ——
 * 引擎层与模式层一行没动，500 局基线照样逐字节全等。
 */
type UiLevel = "minimal" | "guarded" | "full"

/** 街机（纯爽局）：核心玩法之外什么都不给看。 */
const MINIMAL_UI_MODES = ["classic_arcade"]
/** 入门（断供前夕）：恢复生存提示，但仍不玩多标的计划。 */
const GUARDED_UI_MODES = ["classic_beginner"]
/** 前两关的持仓台账只铺前 N 只（其余折成一行字）。 */
const SIMPLE_POSITION_LIMIT = 3

/** 档位 ⇒ UI 密度（认不出来的档位一律按 ``full`` —— 宁可多给，不给空）。 */
function uiLevelFor(mode: string): UiLevel {
  if (MINIMAL_UI_MODES.indexOf(mode) >= 0) return "minimal"
  if (GUARDED_UI_MODES.indexOf(mode) >= 0) return "guarded"
  return "full"
}

function percent(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  return `${(Number(value) * 100).toFixed(1)}%`
}

/** 涨跌数字：带正负号，交给样式表上色。 */
function changeText(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—"
  const pct = Number(value) * 100
  const sign = pct > 0 ? "+" : ""
  return `${sign}${pct.toFixed(2)}%`
}

/**
 * 涨绿跌红（P9.0）。
 *
 * 用的是设计图那一档**柔和**的薄荷绿 / 珊瑚粉 —— 它们要落在浅色胶囊底上，
 * 不能用纯饱和色（浅绿底 + 亮绿字会糊成一片）。
 */
function changeColor(value: number | undefined | null): string {
  const pct = Number(value || 0) * 100
  if (pct > 0.001) return "#17916f"
  if (pct < -0.001) return "#d6486a"
  return "#8ba0bd"
}

/** 把 #RRGGBB 变成 rgba(r,g,b,a) —— canvas 画渐变面积时要用。 */
function hexAlpha(hex: string, alpha: number): string {
  const value = String(hex || "").replace("#", "")
  if (value.length !== 6) return hex
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/**
 * P7.2：**超大号**的统计数字。
 *
 * 宿主 `StatCard` **不透传 style**（见 runtime.js），所以要自己拼一个
 * `.neko-stat`（沿用宿主自己的类，样式还是宿主的）。
 */
function BigStat(props: {
  label: string
  value: string
  hint?: string
  color?: string
  /**
   * 字号：数字（px）或 **CSS 长度表达式**（P21.0）。
   *
   * P21.0：全屏时希望核心数字跟着面板一起变大 ⇒ 传
   * ``clamp(30px, 3.4vw, 56px)`` 这样的值（``vw`` = iframe 宽度）。
   */
  size?: number | string
  /**
   * P16.0：跳动时的**强调**（缩一下）。
   *
   * 只切 `transform` —— **不用 CSS 动画**（0.8.0 的崩溃根因之一）。
   * 不跳的时候给 `undefined`（不留 `scale(1)`）—— 免得凭空建一个
   * containing block。
   */
  pop?: boolean
}) {
  return h(
    "div",
    { className: "neko-stat" },
    h("span", { className: "neko-stat-label" }, props.label),
    h(
      "strong",
      {
        className: "neko-stat-value",
        style: {
          fontSize:
            typeof props.size === "string" ? props.size : `${props.size || 34}px`,
          color: props.color,
          lineHeight: "1.15",
          transform: props.pop ? "scale(1.12)" : undefined,
        },
      },
      props.value
    ),
    props.hint
      ? h("span", { style: { fontSize: "12px", color: "var(--muted)" } }, props.hint)
      : null
  )
}

/**
 * P16.0：**连击徽章** —— 街机档的「爽感」配件。
 *
 * 连续买中上涨的股票时，这块徽章会亮起来并**弹一下**。
 *
 * **不用 CSS 动画**（0.8.0 的崩溃根因之一）：弹跳是 **JS 定时器**
 * 切 `transform` 做的，跟 P7.2 的濒临破产闪烁同款思路。
 *
 * 徽章**只展示**连击数 —— 不含任何游戏数值，也不参与任何判定。
 */
function ComboBadge(props: { streak: number; best: number; t: Translate }) {
  const streak = Math.max(0, Number(props.streak || 0))
  const best = Math.max(0, Number(props.best || 0))
  const t = props.t
  // 连击断了 ⇒ 徽章收起来（只留一行「最长连击」）。
  const on = streak >= 2
  const [pop, setPop] = useState(false)
  useEffect(() => {
    if (!on) {
      setPop(false)
      return
    }
    setPop(true)
    const timer = setTimeout(() => setPop(false), 260)
    return () => clearTimeout(timer)
  }, [streak, on])
  if (!on) {
    return best >= 2
      ? h(
          "div",
          { style: { fontSize: "12px", color: "var(--muted)" } },
          t("panel.combo_best", { count: best })
        )
      : null
  }
  return h(
    "div",
    {
      className: "rrm-combo",
      style: {
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "6px 12px",
        borderRadius: "12px",
        background: "rgba(91, 143, 249, 0.12)",
        border: "1px solid rgba(91, 143, 249, 0.35)",
        transform: pop ? "scale(1.06)" : undefined,
      },
    },
    h("span", { style: { fontSize: "18px" } }, "🔥"),
    h(
      "strong",
      { style: { fontSize: "20px", color: "var(--primary)" } },
      t("panel.combo_badge", { count: streak })
    ),
    h(
      "span",
      { style: { fontSize: "12px", color: "var(--muted)" } },
      t("panel.combo_hint")
    )
  )
}

/**
 * P14.0：**资金水位条** —— 把「明天的生活费」画成一条看得见的红线。
 *
 * **为什么要有它**：现金以前只是一个数字，玩家看不见「还剩多少能撑」。
 * 水位条把两件事摆到同一根尺子上：
 *
 * * **红区**（长度固定 = 明天要交的生活费）—— 掉进去就是断供；
 * * **青蓝区**（生活费之上的那部分）—— 还能花的余量。
 *
 * **危机态**（现金 < 明天的生活费）：整条变红并**轻微闪烁**，
 * 现金数字也一起变红闪烁 —— 极强的警示。
 *
 * 闪烁是 **JS 定时器切颜色**做的（跟 P7.2 的濒临破产数字同款），
 * **不用 CSS 动画**（0.8.0 的崩溃根因之一）。
 *
 * 样式全部走**内联 style**：P14 的纪律是「极度克制地使用 CSS」，
 * 所以这一版**一行新 CSS 都不加**。
 */
function CashGauge(props: {
  cash: number
  bill: number
  crisis: boolean
  alarmOn: boolean
  t: Translate
  /**
   * P16.0：**紧凑模式**（前两关用）。
   *
   * 前两关的诉求是「闭着眼睛瞎买也能赚钱」—— 复杂的水位条在那里是
   * **干扰**。紧凑模式把它压成**一行字**：红线多少、还差多少 / 还能花多少。
   */
  compact?: boolean
}) {
  const cash = Math.max(0, Number(props.cash || 0))
  const bill = Math.max(0, Number(props.bill || 0))
  const crisis = Boolean(props.crisis)
  // 尺子：至少要装得下「现金」和「生活费」两个数。
  const scale = Math.max(cash, bill, 1)
  // 红区长度固定 = 生活费（不随现金变）；青蓝区 = 超出生活费的那部分。
  const redW = crisis ? (cash / scale) * 100 : (Math.min(bill, scale) / scale) * 100
  // 危机态：红区里没填满的那一段 = 「还差多少」。
  const holeW = crisis ? Math.max(0, ((bill - cash) / scale) * 100) : 0
  const cyanW = crisis ? 0 : Math.max(0, ((cash - bill) / scale) * 100)

  // 闪烁 = 两个红色之间来回切（同一支红，只是深浅不同）。
  const redStrong = props.alarmOn ? "rgba(214, 72, 106, 0.42)" : "var(--danger)"
  const redSolid = "var(--danger)"
  const redZone = crisis ? redStrong : redSolid
  const valueColor = crisis
    ? props.alarmOn
      ? "rgba(214, 72, 106, 0.45)"
      : "var(--danger)"
    : "var(--text)"

  // ── P16.0：紧凑模式（前两关）—— 水位条**压成一行** ──────────────────
  // 复杂图形在前两关是**干扰**：玩家不需要研究它，只需要知道自己要交多少。
  if (props.compact) {
    return h(
      "div",
      {
        // P19.0：**紧凑版也要带锚点 class** —— 前两关（街机 / 入门）的
        // 教程第 4 步就是「看水位」，锚点叫 ``gauge``。
        // 之前紧凑分支漏了这个 class ⇒ 教程圈不到任何东西。
        className: "rrm-gauge rrm-anchor-gauge",
        style: {
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "8px",
          fontSize: "12px",
          color: "var(--muted)",
        },
      },
      h("span", null, props.t("panel.gauge_bill", { amount: money(bill) })),
      crisis
        ? h(
            "span",
            { style: { color: "var(--danger)", fontWeight: "700" } },
            props.t("panel.gauge_crisis", { gap: money(bill - cash) })
          )
        : h("span", null, props.t("panel.gauge_free", { amount: money(cash - bill) }))
    )
  }

  return h(
    "div",
    {
      className: "rrm-gauge rrm-anchor-gauge",
      style: { display: "flex", flexDirection: "column", gap: "6px" },
    },
    h(
      "div",
      {
        style: {
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "8px",
        },
      },
      h(
        "span",
        { style: { fontSize: "12px", color: "var(--muted)" } },
        props.t("panel.gauge_title")
      ),
      h(
        "span",
        { style: { fontSize: "13px", fontWeight: "700", color: valueColor } },
        props.t("panel.gauge_bill_amount", { amount: money(bill) })
      )
    ),
    h(
      "div",
      {
        className: "rrm-gauge-track",
        style: {
          display: "flex",
          width: "100%",
          height: "14px",
          borderRadius: "7px",
          overflow: "hidden",
          background: "rgba(142, 171, 219, 0.16)",
          border: crisis
            ? `1px solid ${props.alarmOn ? "rgba(214, 72, 106, 0.42)" : "var(--danger)"}`
            : "1px solid rgba(142, 171, 219, 0.18)",
        },
      },
      redW > 0
        ? h("div", {
            className: "rrm-gauge-red",
            style: { width: `${redW}%`, height: "100%", background: redZone },
          })
        : null,
      holeW > 0
        ? h("div", {
            className: "rrm-gauge-hole",
            style: {
              width: `${holeW}%`,
              height: "100%",
              background: "rgba(214, 72, 106, 0.26)",
            },
          })
        : null,
      cyanW > 0
        ? h("div", {
            className: "rrm-gauge-cyan",
            style: { width: `${cyanW}%`, height: "100%", background: "var(--primary)" },
          })
        : null
    ),
    h(
      "div",
      {
        style: {
          display: "flex",
          justifyContent: "space-between",
          gap: "8px",
          fontSize: "11px",
          color: "var(--muted)",
        },
      },
      h("span", null, props.t("panel.gauge_bill", { amount: money(bill) })),
      crisis
        ? h(
            "span",
            { style: { color: "var(--danger)", fontWeight: "700" } },
            props.t("panel.gauge_crisis", { gap: money(bill - cash) })
          )
        : h("span", null, props.t("panel.gauge_free", { amount: money(cash - bill) }))
    )
  )
}

/**
 * P7.2：走势小折线（Sparkline）。
 *
 * 宿主渲染器画不了真 SVG，但 **canvas 是可以用的**（`document.createElement`
 * 建出来的是真 canvas，`ref` 也会挂到真实 DOM 节点上）。所以这里用 canvas
 * 画一条折线；万一宿主环境拿不到 2D 上下文，就**降级成柱子**（纯 div，
 * 不依赖任何定位 / 动画），绝不抛错、绝不崩面板。
 */
function Sparkline(props: {
  points: number[]
  up: boolean
  title?: string
  width?: number
  height?: number
}) {
  const canvasRef = useRef<any>(null)
  const [broken, setBroken] = useState(false)
  const values = props.points || []
  const signature = values.join(",")

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) {
      setBroken(true)
      return
    }
    let ctx: any = null
    try {
      ctx = canvas.getContext("2d")
    } catch (error) {
      ctx = null
    }
    if (!ctx) {
      setBroken(true)
      return
    }
    try {
      const width = Number(canvas.width) || 84
      const height = Number(canvas.height) || 26
      ctx.clearRect(0, 0, width, height)
      if (values.length === 0) return
      let max = values[0]
      let min = values[0]
      for (const value of values) {
        if (value > max) max = value
        if (value < min) min = value
      }
      const span = max - min || 1
      const pad = 3
      const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0
      const color = props.up ? TREND_UP : TREND_DOWN
      if (values.length < 2) {
        // 刚开盘只有一两个点 —— 画一条**两端淡出的平直基线**。
        // （孤零零一个小点看着像渲染坏了，不像「还没数据」。）
        const base = ctx.createLinearGradient(0, 0, width, 0)
        base.addColorStop(0, hexAlpha(color, 0))
        base.addColorStop(0.5, hexAlpha(color, 0.55))
        base.addColorStop(1, hexAlpha(color, 0))
        ctx.strokeStyle = base
        ctx.lineWidth = 2
        ctx.lineCap = "round"
        ctx.beginPath()
        ctx.moveTo(1, height / 2)
        ctx.lineTo(width - 1, height / 2)
        ctx.stroke()
        return
      }
      const coords = values.map((value, index) => ({
        x: pad + step * index,
        y: height - pad - ((value - min) / span) * (height - pad * 2),
      }))
      // P9.0：折线底下垫一层**渐变面积** —— 设计图里那条「会发光的走势带」。
      try {
        const area = ctx.createLinearGradient(0, 0, 0, height)
        area.addColorStop(0, hexAlpha(color, 0.34))
        area.addColorStop(1, hexAlpha(color, 0))
        ctx.beginPath()
        ctx.moveTo(coords[0].x, height)
        coords.forEach((point) => ctx.lineTo(point.x, point.y))
        ctx.lineTo(coords[coords.length - 1].x, height)
        ctx.closePath()
        ctx.fillStyle = area
        ctx.fill()
      } catch (error) {
        // 老浏览器没有渐变 API —— 少一层面积而已，不影响读数。
      }
      ctx.strokeStyle = color
      ctx.lineWidth = 1.8
      ctx.lineJoin = "round"
      ctx.lineCap = "round"
      ctx.beginPath()
      coords.forEach((point, index) => {
        if (index === 0) ctx.moveTo(point.x, point.y)
        else ctx.lineTo(point.x, point.y)
      })
      ctx.stroke()
      // 末端点一个小圆点（设计图的折线末端有个实心点）。
      const tail = coords[coords.length - 1]
      ctx.beginPath()
      ctx.arc(tail.x, tail.y, 2, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()
    } catch (error) {
      setBroken(true)
    }
  }, [signature, props.up])

  if (broken) {
    // 兜底：画不了 canvas 就用柱子（纯 div，无背景无定位）。
    const last = values.slice(-12)
    const max = last.length ? Math.max.apply(null, last) : 0
    const min = last.length ? Math.min.apply(null, last) : 0
    const span = max - min || 1
    const bars = last.map((value, index) => {
      const previous = index > 0 ? last[index - 1] : value
      return h("div", {
        key: index,
        style: {
          width: "3px",
          height: `${Math.max(2, Math.round(((value - min) / span) * 18))}px`,
          marginRight: "1px",
          background: value >= previous ? TREND_UP : TREND_DOWN,
        },
      })
    })
    return h(
      "div",
      {
        title: props.title,
        className: "rrm-spark",
        style: {
          display: "flex",
          alignItems: "flex-end",
          height: "20px",
        },
      },
      ...bars
    )
  }

  return h("canvas", {
    ref: canvasRef,
    className: "rrm-spark",
    width: props.width || 84,
    height: props.height || 26,
    title: props.title,
    style: { display: "block" },
  })
}

/**
 * P8.0：**群友头像**。
 *
 * 社区名册里的群友（星辰 / Wehos / 老王 / 小明 / 熬夜怪 / 潜水员）在行情
 * 方块卡里挂一个小圆头像 —— 头像是 ``ui/assets/`` 里内联的 PNG（data URI）。
 *
 * 极端情况下（图片解码失败）**退回专属 Emoji**，绝不出现破图。
 */
function RosterAvatar(props: { alias?: string }) {
  const alias = props.alias || ""
  const entry = AVATARS[alias]
  const [broken, setBroken] = useState(false)
  if (!entry || broken) {
    return h(
      "span",
      { style: { fontSize: "14px", lineHeight: "1" } },
      entry ? entry.fallback : "🐱"
    )
  }
  return h("img", {
    src: entry.src,
    alt: alias,
    className: "rrm-avatar",
    onError: () => setBroken(true),
  })
}

/**
 * P8.0：**行情方块卡片**（网格单元）。
 *
 * 一格 = 股票名（+ 群友头像）+ 微型折线 + **巨大**涨跌百分比 + 现价。
 *
 * 用宿主 `Card` 组件 ⇒ 它是网格容器的**直接**子元素（0.8.0 的第四条根因
 * 就是自定义 div 包裹层把卡片挤出 flex 流），布局由宿主负责，不会挤成一团。
 *
 * 三个信息维度（走势 / 现价 / 涨跌）的标签走 i18n，用原生 ``title`` 属性
 * 当悬浮提示 —— 不加任何 CSS，也不破坏方块卡的紧凑排版。
 */
function StockTile(props: { stock: Stock; t: Translate }) {
  const stock = props.stock
  const t = props.t
  const up = Number(stock.change || 0) >= 0
  const change = Number(stock.change || 0)
  const tone = change > 0.001 ? "up" : change < -0.001 ? "down" : "flat"
  // P9.0：卡片改成**设计图的两层结构** ——
  //   上层：股票名（+ 群友头像）  ····  右侧是涨跌胶囊
  //   下层：迷你走势（带渐变面积）  ····  右侧是现价
  return h(
    Card,
    { className: "rrm-tile" },
    h(
      "div",
      { className: "rrm-tile-head" },
      h(
        "span",
        { className: "rrm-stock-name" },
        h(RosterAvatar, { alias: stock.roster_alias }),
        h(
          "span",
          { className: "rrm-tile-name-text" },
          stock.display_name || stock.name
        )
      ),
      h(
        "span",
        {
          className: `rrm-change-pill rrm-change-${tone}`,
          title: t("visual.change_title"),
          style: { color: changeColor(stock.change) },
        },
        changeText(stock.change)
      )
    ),
    h(
      "div",
      { className: "rrm-tile-foot" },
      h(Sparkline, {
        points: (stock.spark || []).map((point) => point[1]),
        up,
        title: t("visual.trend_title"),
        width: 200,
        height: 30,
      }),
      h(
        "span",
        { className: "rrm-price", title: t("visual.price_title") },
        money(stock.price)
      )
    )
  )
}

/**
 * P8.0：**持仓方块卡片**（网格单元）。
 *
 * 和行情方块卡同一套视觉：名字 + 大号盈亏 + 当前市值。
 */
function PositionTile(props: { position: Position; t: Translate }) {
  const position = props.position
  const t = props.t
  const pnl = Number(position.unrealized_pnl || 0)
  const tone = pnl > 0.001 ? "up" : pnl < -0.001 ? "down" : "flat"
  // P9.0：和行情卡同一套两层结构（名字 + 盈亏胶囊 / 市值）。
  return h(
    Card,
    { className: "rrm-tile" },
    h(
      "div",
      { className: "rrm-tile-head" },
      h(
        "span",
        { className: "rrm-stock-name" },
        h("span", { className: "rrm-tile-name-text" }, position.display_name || position.name)
      ),
      h(
        "span",
        {
          className: `rrm-change-pill rrm-change-${tone}`,
          title: t("visual.pnl_title"),
          style: { color: changeColor(pnl) },
        },
        `${pnl > 0 ? "+" : ""}${money(pnl)}`
      )
    ),
    h(
      "div",
      { className: "rrm-tile-foot" },
      // P14.0：这只占了**总仓位的百分之多少** —— 玩家得看得见钱压在哪儿。
      h(
        "span",
        {
          style: { fontSize: "12px", fontWeight: "600", color: "var(--muted)" },
          title: t("panel.weight"),
        },
        `${t("panel.weight")} ${percent(Number(position.weight || 0))}`
      ),
      h("span", { className: "rrm-price" }, money(position.market_value))
    )
  )
}

/**
 * K 线：用 div 拼出来的迷你蜡烛图（宿主画不了 SVG）。
 *
 * P8.1：**刚开盘不再画一根顶天立地的柱子。**
 *
 * 0.9.0 的 K 线高度是按「价格区间」归一化的（min→2px，max→满格）。
 * 点数少的时候区间极窄，于是**最后一根柱子永远是满格** ——
 * 开局第一眼就是一根又高又孤立的绿柱，极其突兀。
 *
 * 现在点数不足 ``MIN_KLINE_POINTS`` 时**不画柱子**，改成在图表区正中
 * 摆一句浅灰提示（「市场刚开盘，积累数据中……」），等点数攒够了自然接管。
 */
const MIN_KLINE_POINTS = 5

/**
 * P21.0：K 线高度随**宿主 iframe 的高度**自适应。
 *
 * ``vh`` 在 iframe 里指的是 **iframe 自己的视口高度** —— 也就是插件面板
 * 的高度。全屏时它变高 ⇒ K 线跟着长高，正好填满上半部分，
 * 不会在大屏下留一大片白底。
 *
 * ``clamp()`` 给上下限：窄窗口不至于挤成一条线，大窗口也不会长到失控。
 *
 * **只用长度单位**（不碰 position / filter / opacity）——
 * 0.8.0 崩 UI 的根因就是那几样东西。
 */
const KLINE_HEIGHT_CSS = "clamp(232px, 34vh, 520px)"

function Kline(props: {
  points: KlinePoint[]
  emptyText: string
  warmingText: string
}) {
  const points = props.points || []
  // P8.0：K 线是**上半区的主视觉** —— 更高、更抢眼。
  // P21.0：高度改成 CSS 值（跟着 iframe 高度走），柱子的高度改用**百分比** ——
  // 百分比是相对容器算的，所以容器长高时柱子自动跟着长高（不需要 JS 测量）。
  const height = KLINE_HEIGHT_CSS
  if (points.length === 0) {
    return h("div", null, h("span", null, props.emptyText))
  }
  if (points.length < MIN_KLINE_POINTS) {
    return h(
      "div",
      {
        className: "rrm-kline-warming",
        style: {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height,
        },
      },
      h("span", null, props.warmingText)
    )
  }
  const prices = points.map((point) => point[1])
  const max = Math.max.apply(null, prices)
  const min = Math.min.apply(null, prices)
  const span = max - min || 1
  const bars = points.map((point, index) => {
    const tick = point[0]
    const price = point[1]
    // 柱子高度用**百分比**（相对容器），所以容器自适应高度时柱子跟着自适应。
    const ratio = (price - min) / span
    const previous = index > 0 ? points[index - 1][1] : price
    // P9.0：柱子改成**纵向渐变**（顶上实、底下化开），底下再垫一层网格线 ——
    // 这就是设计图里那条 K 线区的「光影」。
    return h("div", {
      key: tick,
      title: `tick ${tick} · ${price.toFixed(4)}`,
      className: price >= previous ? "rrm-kline-bar-up" : "rrm-kline-bar-down",
      style: {
        flex: "1 1 0",
        minWidth: "3px",
        maxWidth: "16px",
        height: `${(ratio * 100).toFixed(2)}%`,
        // 贴底的那根也要看得见（百分比算出来可能是 0）。
        minHeight: "2px",
        marginRight: "2px",
        borderRadius: "3px",
      },
    })
  })
  return h(
    "div",
    {
      className: "rrm-kline-wrap",
      style: {
        display: "flex",
        alignItems: "flex-end",
        height,
        padding: "6px 0",
        overflowX: "auto",
      },
    },
    ...bars
  )
}

/**
 * P9.0：结算页的**扁平空钱包**插画 —— 纯 CSS 拼的，不依赖任何素材文件。
 *
 * 0.9.1 那张「破产猫猫」是 PIL 画的：一只黄色猫瘫在灰色钱包上，远看就是个
 * 土豆，玩家直呼辣眼睛。这次彻底删掉那张图，改成设计图同款的扁平线性风格：
 * 浅蓝渐变的钱包身 + 深蓝搭扣 + 一滴泪 + 飞出去的钞票。
 *
 * 没用 ``position`` / ``filter``，纯 flex 布局 ⇒ 不会踩 0.8.0 那四个雷。
 */
function BankruptArt(props: { alt: string }) {
  return h(
    "div",
    {
      className: "rrm-wallet-scene",
      role: "img",
      title: props.alt,
      "aria-label": props.alt,
    },
    h("span", { className: "rrm-wallet-spark" }, "💸"),
    h(
      "div",
      { className: "rrm-wallet" },
      h(
        "div",
        { className: "rrm-wallet-body" },
        h("div", { className: "rrm-wallet-slot" }),
        h("div", { className: "rrm-wallet-tear" }, "💧")
      ),
      h(
        "div",
        { className: "rrm-wallet-flap" },
        h("div", { className: "rrm-wallet-clasp" })
      )
    )
  )
}

/**
 * P18.0：**动态聚焦引导** —— 分档次教程的面板侧实现。
 *
 * 三条硬约束：
 *
 * 1. **零危险 CSS** —— 遮罩用**宿主自带的** ``Modal``（宿主自己的
 *    全屏遮罩，不是我们写的）；高亮圈只用 ``outline`` + ``box-shadow``；
 *    **不碰**定位 / 层叠 / 滤镜 / 裁剪 / 淡入淡出 / 关键帧。
 * 2. **台词不在面板里** —— 面板只显示**功能性 UI 文案**（步骤标题、正文、
 *    按钮名），全部走 i18n；猫娘说的话在**面板外**的外置气泡里。
 * 3. **上课时绝不挡路** —— 上课中的那一步用**教练条**（不遮挡），
 *    这样玩家**能点到被圈出来的真实元素**（真实操作才能推进教程）。
 *    只有开场与结课用全屏 ``Modal``（那两个时刻玩家只需要读 + 确认）。
 */
function TutorialOverlay(props: {
  tutorial: NonNullable<State["tutorial"]>
  t: Translate
  onBegin: () => void
  onAdvance: () => void
  onSkip: () => void
}) {
  const tutorial = props.tutorial || {}
  const phase = String(tutorial.phase || "idle")
  const t = props.t
  const index = Number(tutorial.index || 0)
  const total = Number(tutorial.total || 0)
  const step = tutorial.step || null
  const stepTitle = step && step.title_key ? t(step.title_key) : ""
  const stepBody = step && step.body_key ? t(step.body_key) : ""

  if (phase === "greet") {
    return h(
      Modal,
      {
        open: true,
        title: t("tutorial.greet.title"),
        onClose: props.onSkip,
        closeOnBackdrop: false,
        closeOnEscape: false,
        size: "md",
        children: h(
          Stack,
          { gap: 12 },
          h(Text, null, t("tutorial.greet.body")),
          h(
            "div",
            { className: "rrm-tutorial-steps" },
            h(Steps, null, h(Step, { index: 1, title: t("tutorial.greet.step_title") }, null))
          )
        ),
        footer: h(
          "div",
          { className: "neko-button-group" },
          h(Button, { onClick: props.onBegin, children: t("tutorial.greet.begin") }),
          h(Button, { tone: "default", onClick: props.onSkip, children: t("tutorial.step.skip") })
        ),
      }
    )
  }

  if (phase === "step") {
    // 教练条：**不遮挡** —— 玩家要能点到被圈出来的那个真实元素。
    return h(
      Card,
      {
        className: "rrm-tutorial-coach",
        title: t("tutorial.step.progress", { index: index + 1, total }),
      },
      h(
        Stack,
        { gap: 8 },
        h(
          "div",
          { className: "rrm-tutorial-steps" },
          h(Steps, null, h(Step, { index: index + 1, title: stepTitle }, h(Text, null, stepBody)))
        ),
        tutorial.waiting_gate
          ? h(Alert, {
              tone: "info",
              message: t(String(tutorial.gate_wait_key || "tutorial.gate.wait")),
            })
          : null,
        h(
          "div",
          { className: "neko-button-group" },
          h(Button, { onClick: props.onAdvance, children: t("tutorial.step.next") }),
          h(Button, { tone: "default", onClick: props.onSkip, children: t("tutorial.step.skip") })
        )
      )
    )
  }

  if (phase === "epilogue") {
    return h(
      Modal,
      {
        open: true,
        title: t("tutorial.epilogue.title"),
        onClose: props.onSkip,
        closeOnBackdrop: false,
        closeOnEscape: false,
        size: "md",
        children: h(
          Stack,
          { gap: 12 },
          h(Text, null, t("tutorial.epilogue.body")),
          // 静态合规实体（**兜底**）：台词由 AI 说，这一条是**写死的声明**。
          h(Alert, { tone: "info", message: t("tutorial.epilogue.risk_note") })
        ),
        footer: h(
          "div",
          { className: "neko-button-group" },
          h(Button, { onClick: props.onSkip, children: t("tutorial.epilogue.close") })
        ),
      }
    )
  }

  return null
}

/**
 * P18.0：把被指代的元素**圈出来**并滚进视野。
 *
 * 零危险 CSS：圈是 ``.rrm-tutorial-target``（``outline`` + ``box-shadow``）。
 * 滚动用**宿主自带的** ``useScrollIntoView``。
 *
 * 锚点是 **className 约定**（宿主组件不透传 ``data-*``）：
 * ``market_grid`` ⇒ ``.rrm-anchor-market-grid``。
 */
function useTutorialHighlight(anchor: string | null) {
  const targetRef = useRef<any>(null)
  const scrollToTarget = useScrollIntoView(targetRef)
  useEffect(() => {
    if (!anchor) {
      targetRef.current = null
      return
    }
    const selector = ".rrm-anchor-" + String(anchor).replace(/_/g, "-")
    const node = document.querySelector(selector)
    if (!node) {
      targetRef.current = null
      return
    }
    targetRef.current = node
    node.classList.add("rrm-tutorial-target")
    scrollToTarget({ block: "center", inline: "nearest", behavior: "smooth" })
    return () => {
      node.classList.remove("rrm-tutorial-target")
      targetRef.current = null
    }
  }, [anchor])
}

/**
 * P10：玩法说明弹窗（三步图文）。
 *
 * 用**宿主自带的** ``Modal`` + ``Alert`` + ``Steps`` / ``Step``
 * （官方推荐做法之一）；宿主组件几乎不透传 ``style``，所以视觉一律走
 * ``className`` + 注入的 ``RRM_CSS``。
 *
 * **P11.1：用回原生的 ``Steps`` / ``Step``** —— 之前那三步是手搓的
 * ``.rrm-guide-step`` / ``.rrm-guide-num`` / ``.rrm-guide-step-title``，
 * 而宿主 UI Kit 里本来就有 ``Steps``（竖排步骤条）和 ``Step``
 * （序号 + 标题 + 正文），渲染出来的结构一模一样
 * （``neko-step`` / ``neko-step-index`` / ``neko-step-title``）。
 * 换回原生组件 ⇒ 少一堆自定义 CSS，也更像"官方原生样式"。
 *
 * 三步分别讲：① 零花钱/家底/救命钱 + 每日生活费；② 一键梭哈/一键割肉 + 看涨跌图；
 * ③ 猫娘会实时嘴臭、接受不了别玩。
 */
function GuideModal(props: {
  open: boolean
  t: Translate
  onClose: () => void
}) {
  const { open, t, onClose } = props
  const step = (index: number, title: string, body: string, tip: string) =>
    h(
      Step,
      { index: index, title: title, key: title },
      h("div", { className: "rrm-guide-step-body" }, body),
      tip ? h("div", { className: "rrm-guide-step-tip" }, tip) : null
    )
  return h(Modal, {
    open: open,
    title: t("guide.modal_title"),
    onClose: onClose,
    closeOnBackdrop: true,
    closeOnEscape: true,
    size: "lg",
    children: h(
      Stack,
      { gap: 12 },
      h(
        Steps,
        null,
        step(1, t("guide.step1_title"), t("guide.step1_body"), t("guide.step1_tip")),
        step(2, t("guide.step2_title"), t("guide.step2_body"), t("guide.step2_tip")),
        step(3, t("guide.step3_title"), t("guide.step3_body"), "")
      ),
      h(Alert, { tone: "warning", message: t("guide.step3_alert") }),
      h("div", { className: "rrm-guide-footer" }, t("guide.footer"))
    ),
    footer: h(
      "div",
      { className: "neko-button-group" },
      h(Button, {
        tone: "primary",
        onClick: onClose,
        children: t("guide.ack"),
      })
    ),
  })
}

/**
 * P11：拟态外置对话弹窗（**兜底**）。
 *
 * **这是降级路径，不是主路径。**
 *
 * 主路径是宿主 SDK 的 ``push_message(ai_behavior="respond")``：插件把一段提示
 * 喂给 LLM 并立刻触发一个 AI 回合，**外置猫娘**（桌宠）自己开口说话。
 *
 * 只有当宿主**接不住**这次主动搭话（老版本宿主 / 推送被拒）时，才会退到这
 * 个弹窗 —— 用"猫娘发来一条微信消息"的样式，在**面板内部**演一段台词。
 * 所以弹窗里必须写清楚这是模拟，不能让玩家以为桌宠真的没说话。
 *
 * 视觉一律走 ``className`` + 注入的 ``RRM_CSS``（宿主组件几乎不透传 style）。
 */
function MeowChatModal(props: {
  open: boolean
  t: Translate
  line: string
  reason: string
  onClose: () => void
}) {
  const { open, t, line, reason, onClose } = props
  return h(
    Modal,
    {
      open: open,
      title: t("meow.modal_title"),
      onClose: onClose,
      size: "sm",
      children: h(
        Stack,
        { gap: 12 },
        h(
          "div",
          { className: "rrm-meow-thread" },
          h("span", { className: "rrm-meow-avatar" }, "🐱"),
          h("div", { className: "rrm-meow-bubble" }, line || t("meow.fallback_empty"))
        ),
        h(Alert, {
          tone: "info",
          message: reason === "push_failed" ? t("meow.fallback_push") : t("meow.fallback_generic"),
        })
      ),
      footer: h(
        "div",
        { className: "neko-button-group" },
        h(Button, {
          tone: "primary",
          onClick: onClose,
          children: t("meow.ack"),
        })
      ),
    }
  )
}

/**
 * P10：节奏卡（游戏速度 + 自动/手动推进）。
 *
 * **只改"现实里等多久"**，一个游戏数值都不碰：
 *   * 速度三档（慢 / 标准 / 快）＝ 心跳间隔的倍率；
 *   * 自动 / 手动 ＝ 心跳要不要自己往前跳；
 *   * 手动模式下才出现【推进一拍】—— 自动模式里给这个按钮会让"自动"变成谎话。
 */
function PaceControl(props: {
  pace: Pace
  t: Translate
  ticksToSettle?: number
  /** P11.1：有 action 在飞 ⇒ 按钮禁用 + 显示"推进中"。 */
  busy?: boolean
  onSpeed: (speed: string) => void
  onToggleAuto: () => void
  onAdvance: () => void
}) {
  const { pace, t, ticksToSettle, busy, onSpeed, onToggleAuto, onAdvance } = props
  const speed = pace.speed || "normal"
  const auto = pace.auto !== false
  const interval = pace.interval_seconds || 0
  const minutes = Math.max(1, Math.round((pace.day_seconds || 0) / 60))
  const speeds = [
    { id: "slow", label: t("panel.pace_speed_slow") },
    { id: "normal", label: t("panel.pace_speed_normal") },
    { id: "fast", label: t("panel.pace_speed_fast") },
  ]
  return h(
    Card,
    { title: t("panel.pace_title") },
    h(
      Stack,
      { gap: 12 },
      h(
        "div",
        { className: "rrm-pace-head" },
        h(
          "span",
          {
            className: auto
              ? "rrm-pace-badge rrm-pace-badge-auto"
              : "rrm-pace-badge rrm-pace-badge-manual",
          },
          auto ? t("panel.pace_auto_badge") : t("panel.pace_manual_badge")
        ),
        h(
          "span",
          { className: "rrm-pace-hint" },
          auto
            ? interval > 0
              ? t("panel.pace_auto_hint", { seconds: interval })
              : t("panel.pace_auto_hint_unknown")
            : t("panel.pace_manual_hint")
        )
      ),
      h(
        Grid,
        { cols: 3, gap: 8 },
        speeds.map((item) =>
          h(Button, {
            key: item.id,
            className:
              item.id === speed
                ? "rrm-speed-tag rrm-speed-tag-on"
                : "rrm-speed-tag",
            disabled: busy,
            onClick: () => onSpeed(item.id),
            children: item.label,
          })
        )
      ),
      h(
        "div",
        { className: "rrm-pace-meta" },
        h(
          "span",
          null,
          minutes > 0
            ? t("panel.pace_day_estimate", { minutes: minutes })
            : t("panel.pace_day_estimate_unknown")
        ),
        ticksToSettle === undefined || ticksToSettle === null
          ? null
          : h(
              "span",
              null,
              t("panel.pace_to_settle", { ticks: ticksToSettle })
            )
      ),
      h(
        "div",
        { className: "rrm-pace-actions" },
        h(Button, {
          className: "rrm-pace-toggle",
          tone: "default",
          disabled: busy,
          onClick: onToggleAuto,
          children: auto
            ? t("panel.pace_switch_manual")
            : t("panel.pace_switch_auto"),
        }),
        auto
          ? null
          : h(Button, {
              className: "rrm-pace-advance",
              // ── P11.1：请求在飞时禁用 + 显示"推进中" ──────────────
              // 狂点会被这条禁用态挡掉 ⇒ 宿主不会再收到并发的 action ⇒
              // 不会再有 `Failed to query plugin UI action context`。
              disabled: busy,
              onClick: onAdvance,
              children: busy ? t("panel.pace_advance_busy") : t("panel.pace_advance"),
            })
      ),
      h(
        "div",
        { className: "rrm-pace-note" },
        auto ? t("panel.pace_auto_note") : t("panel.pace_manual_note")
      )
    )
  )
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, actions } = props

  const [selectedStock, setSelectedStock] = useLocalState<string>("rrm.selected_stock", "")
  const [sellStock, setSellStock] = useLocalState<string>("rrm.sell_stock", "")
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null)
  const [newsDetail, setNewsDetail] = useState<Record<string, any> | null>(null)
  const [dismissedProposal, setDismissedProposal] = useState<string>("")
  // ── P8.1：空闲态的猫娘**开场嘲讽** ──────────────────────────────
  // 面板每 3 秒轮询一次，所以随机值必须**在挂载时摇一次**并锁住 ——
  // 否则玩家会眼睁睁看着话术每 3 秒自己换一句（比没有还瘆人）。
  const [idleTauntKey] = useState<string>(() => {
    const index = Math.floor(Math.random() * IDLE_TAUNT_KEYS.length)
    return IDLE_TAUNT_KEYS[index] || IDLE_TAUNT_KEYS[0]
  })
  const idleTaunt = t(idleTauntKey)
  // ── P6 档 2：按需拉到的 K 线（局中载荷只带前 20 只，其余现拉）────
  const [klineDetail, setKlineDetail] = useState<Record<string, any> | null>(null)
  // ── P5.5 改动 4：天桥三屏（1 → 2 → 3，间隔自动推进）─────────────
  const [sceneScreen, setSceneScreen] = useState<number>(1)
  // ── P5.5 改动 7：称号图鉴的折叠状态（持久化）────────────────────
  const [achievementsOpen, setAchievementsOpen] = useLocalState<string>(
    "rrm.achievements_open",
    "0"
  )
  // ── P7：导演台（长按标题栏）、网贷弹窗 ──────────────────────────
  const [directorOpen, setDirectorOpen] = useState(false)
  const [holdHint, setHoldHint] = useState(false)
  const [loanModalOpen, setLoanModalOpen] = useState(false)
  const [loanModalDay, setLoanModalDay] = useState<number>(-1)
  // ── P7.3：网贷弹窗的本地倒计时（秒）+ 标题栏逃生按钮的两步确认 ──────
  const [loanLeft, setLoanLeft] = useState<number>(0)
  const [escapeArmed, setEscapeArmed] = useState(false)
  const escapeTimer = useRef<any>(null)
  // P7.3：网贷弹窗的**本地截止时刻**（epoch 毫秒）与轰炸的**硬上限**。
  const loanDeadline = useRef<number>(0)
  const boomDeadline = useRef<number>(0)
  // ── P7.4：面板自己轮询上下文（宿主不轮询）────────────────────────
  useEffect(() => {
    let cancelled = false
    const timer = setInterval(() => {
      if (cancelled) return
      try {
        const pending = props.api.refresh()
        if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
      } catch (_error) {
        // 插件正在重启：拉不到就算了，下一个周期再说。
      }
    }, PANEL_POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [props.api])
  // ── P7.2：濒临破产档「数字偶尔闪红」（**JS 定时器**，不用 CSS 动画）──
  const [flashOn, setFlashOn] = useState(false)
  // ── P7.2：弹窗轰炸的倒计时心跳（用来把 5 秒倒计时画出来）─────────
  const [, setBoomTick] = useState(0)
  const holdTimer = useRef<any>(null)

  const visual = state.visual || {}
  const stage = visual.stage || "rich"
  const critical = Boolean(visual.critical)
  const directorEnabled = Boolean((visual.director || {}).enabled)
  const danmakuList = visual.danmaku || []
  const rosterList = visual.roster || []
  const boom = visual.boom || {}

  // ── P10：节奏（游戏速度 / 自动·手动）与新手引导 ────────────────────
  // 宿主偶尔会返回**空状态**（宿主自己忙，5 秒预算用光）。这时候宁可显示
  // 上一次知道的节奏，也不要闪回"标准 0 秒/拍"吓人 —— 所以留个兜底缓存。
  const paceRef = useRef<Pace>({})
  const paceRaw = (state.pace || {}) as Pace
  const pace: Pace =
    Object.keys(paceRaw).length > 0 ? paceRaw : paceRef.current
  if (Object.keys(paceRaw).length > 0) {
    paceRef.current = paceRaw
  }
  const onboarding = state.onboarding || {}
  const [guideOpen, setGuideOpen] = useState(false)
  // ── P18.0：分档次动态教程 ──────────────────────────────────────
  // 载荷里只有**功能性 UI 文案的 i18n key**；台词由 Yui 在面板外说。
  const tutorial = (state.tutorial || {}) as NonNullable<State["tutorial"]>
  const tutorialPhase = String(tutorial.phase || "idle")
  const tutorialAnchor = tutorial.step ? String(tutorial.step.anchor || "") : ""
  const tutorialWaiting = Boolean(tutorial.waiting_gate)
  // 上课时才圈目标（开场 / 结课那两个全屏时刻不需要圈）。
  //
  // **等门槛事件的那一步不圈** —— 课还没开课，先圈会误导玩家
  // （而且门槛打开前后锚点相同，不这样处理的话高亮压根不会重新生效）。
  useTutorialHighlight(
    tutorialPhase === "step" && !tutorialWaiting ? tutorialAnchor : null
  )
  // P14.0：【命运挑战】的二级档位选择（入门 / 进阶 / 地狱）。
  const [challengeOpen, setChallengeOpen] = useState(false)
  // 「只自动弹一次」：**本次挂载**只试一次（面板每 3 秒轮询，不能反复弹）。
  const guideAutoTried = useRef(false)
  useEffect(() => {
    if (guideAutoTried.current) return
    if (!onboarding.auto_open) return
    guideAutoTried.current = true
    setGuideOpen(true)
  }, [onboarding.auto_open])

  // ── P20.0：玩家自报名号（**戏名**）───────────────────────────────
  //
  // 后端把玩家上次的自报名号放进 `state.player_name`（空闲态回填）。
  // 用户改过之后就不再被后端覆盖（`nameTouched`）。
  //
  // **防呆在前端先做一遍**（去换行、限长 12），后端**还会再洗一遍** ——
  // 面板永远不可信。
  const [nameDraft, setNameDraft] = useState<string>("")
  const [nameTouched, setNameTouched] = useState(false)
  useEffect(() => {
    if (nameTouched) return
    const incoming = String(state.player_name || "")
    if (incoming) setNameDraft(incoming)
  }, [state.player_name, nameTouched])
  const onNameInput = (raw: string) => {
    setNameTouched(true)
    const cleaned = String(raw === undefined || raw === null ? "" : raw)
      .replace(/[\r\n\t]/g, " ")
      // P21.0：限 **6 个字** —— 太长会撑爆面板上的标题栏。
      // 后端还会再洗一遍（``name_easter_eggs.MAX_NAME_LENGTH``）。
      .slice(0, 6)
    setNameDraft(cleaned)
  }
  // ── P11：猫娘外置交互引导（主动搭话）───────────────────────────
  //
  // **主路径**：插件调 `push_message(ai_behavior="respond")`，宿主让**外置猫娘**
  // 真的开口。面板这一侧只做两件事：
  //   ① 挂载时调一次 `meow_greet`（首次加载引导，全局只说一次）；
  //   ② 盯住 `meow.fallback_seq` —— 序号变了就说明"宿主接不住，弹兜底对话"。
  const meow = (state.meow || {}) as Meow
  const [meowFallback, setMeowFallback] = useState<{ line: string; reason: string } | null>(
    null
  )
  // 「只招呼一次」：**本次挂载**只试一次。
  //
  // `userInitiated: false` 让宿主在插件还没起来时**自动重试**这次调用 ——
  // 玩家刚打开面板时插件可能正在启动，等它跑起来再补一句招呼。
  const meowGreetTried = useRef(false)
  useEffect(() => {
    if (meowGreetTried.current) return
    meowGreetTried.current = true
    try {
      const pending = props.api.call("meow_greet", {}, { userInitiated: false })
      if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
    } catch (_error) {
      // 呼不上就算了：绝不能因为一句引导语把面板搞崩。
    }
  }, [props.api])
  // 兜底弹窗：序号变了 ⇒ 有一条新的猫娘消息（宿主接不住主动搭话）。
  const meowFallbackSeq = useRef(-1)
  useEffect(() => {
    const seq = Number(meow.fallback_seq || 0)
    if (!seq || seq === meowFallbackSeq.current) return
    meowFallbackSeq.current = seq
    setMeowFallback({
      line: String(meow.fallback_line || ""),
      reason: String(meow.fallback_reason || ""),
    })
  }, [meow.fallback_seq, meow.fallback_line, meow.fallback_reason])

  // ── P12：剧情沉浸模式（宿主原生 ``Modal size="full"``）──────────────
  //
  // **猫娘在外置气泡里说，面板这里只放纯文本场景** —— 面板**不画立绘、
  // 不画对话框**（外置已经有 Yui 了）。
  //
  // 正常路径：**选项按钮在聊天卡片上**（贴着 Yui 气泡），面板只展示这一幕
  // 的场景文字；只有**卡片投递失败**时，面板才接管选项按钮（优雅降级）。
  const story = state.story || {}
  const [storyOpen, setStoryOpen] = useState(false)
  // 用**章节 id**做键（而不是 ``seq``）—— ``seq`` 是内存计数，插件重启就归零，
  // 那样"重启前就开着、还没选完"的那一幕就再也弹不出来了。
  const storyPendingRef = useRef("")
  useEffect(() => {
    const pending = String(story.pending || "")
    if (!pending) {
      // 这一幕演完了（玩家选完）⇒ 收起，并允许下一幕再弹。
      storyPendingRef.current = ""
      setStoryOpen(false)
      return
    }
    if (pending === storyPendingRef.current) return
    storyPendingRef.current = pending
    setStoryOpen(true)
  }, [story.pending, story.seq])

  // ── P11.1：全局请求锁 ────────────────────────────────────────────
  //
  // 宿主执行任何 UI action **之前**都会先 `get_ui_context()`（**5 秒预算**），
  // 而插件是**单进程单事件循环**：并发请求会让插件里的 `threading.Lock`
  // 抢不到就**阻塞整个事件循环**，宿主那次上下文查询随之超时 ⇒
  // 红色 `Failed to query plugin UI action context`（500）。
  //
  // 所以面板侧**只允许一个 action 在飞**：点下去立刻置 busy，
  // 等接口返回 **且** 面板刷新完，才允许下一次点击。
  const busyRef = useRef(false)
  const [busy, setBusy] = useState(false)

  /**
   * 走锁的 action 调用。
   *
   * * 已经有请求在飞 ⇒ **静默丢弃**（只弹一条温和的 info 提示，
   *   **绝不**红色警告制造恐慌）；
   * * 返回 action 的载荷（``Ok({...})`` 里的字典），拿不到就 ``null``。
   */
  const callAction = async (
    action: string,
    args: Record<string, any>,
    options?: { userInitiated?: boolean }
  ): Promise<any> => {
    if (busyRef.current) {
      showToast(t("panel.action_busy"), "info")
      return null
    }
    busyRef.current = true
    setBusy(true)
    try {
      const result = await props.api.call(action, args, {
        userInitiated: options?.userInitiated !== false,
      })
      await props.api.refresh()
      return result
    } catch (error: any) {
      showToast(String((error && error.message) || error), "danger")
      // 失败**也要**把上下文拉回来 —— 面板不能停在旧状态
      // （比如这一局已经结束了，屏幕上的按钮全是死的）。见 P7.4 的 resync。
      try {
        await props.api.refresh()
      } catch (_error) {
        // 拉不到就算了。
      }
      return null
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  /** 【呼叫猫娘】：玩家按一下，外置猫娘回一句。 */
  const callMeow = async () => {
    const result = await callAction("meow_call", {})
    if (result === null) return
    const spoken = Boolean(result.spoken)
    showToast(
      spoken ? t("panel.meow_called") : t("panel.meow_fallback_toast"),
      spoken ? "info" : "warning"
    )
  }

  /**
   * 【执行买入】：按滑块（或直输框反推出来的）仓位买入。
   *
   * 走 ``callAction`` ⇒ **纳入 P11.1 的全局请求锁**（狂点只会被静默丢弃，
   * 不会并发打爆宿主那次 ``get_ui_context``）。
   */
  const executeBuy = async () => {
    if (buyAmount <= 0) {
      showToast(t("visual.no_money"), "warning")
      return
    }
    // P21.0：第一次成交 ⇒ 飘出新手兜底提示（没动滑块就直接买也算）。
    triggerGhostHint()
    await callAction("buy", { stock_id: effectiveStockId, amount: buyAmount })
    clearTradeDrafts()
    // P14：这只已经买过了 ⇒ 它那份"计划"作废（不然台账会重复计算）。
    // 别的标的的计划留着 —— 现金变少之后它们会**自动回缩**。
    setPlanBudgetMap((prev: Record<string, number>) => {
      const next = { ...(prev || {}) }
      delete next[effectiveStockId]
      return next
    })
  }

  /** 【执行卖出】：按滑块（或直输框反推出来的）仓位卖出。 */
  const executeSell = async () => {
    if (sellAmount <= 0) {
      showToast(t("visual.nothing_to_sell"), "warning")
      return
    }
    // P21.0：第一次成交 ⇒ 飘出新手兜底提示（没动滑块就直接卖也算）。
    triggerGhostHint()
    await callAction("sell", { stock_id: effectiveSellStockId, amount: sellAmount })
    clearTradeDrafts()
  }
  // ── P18.0：教程的三个动作（开始 / 下一步 / 跳过）──────────────────
  // **同步关界面**：后端成不成功都不该把玩家卡住。
  const tutorialCall = (action: string) => {
    try {
      props.api
        .call(action, {}, { userInitiated: true })
        .then(() => {
          try {
            props.api.refresh()
          } catch (_error) {
            // 刷新失败没关系：面板每 3 秒自己会轮询一次。
          }
        })
        .catch(() => undefined)
    } catch (_error) {
      // 连调用都抛出来了：教程只是引导，失败也不能挡住玩家。
    }
  }

  /** 关掉《玩法说明》＝"玩家看过了"：把已读标记记回 player_id。 */
  const closeGuide = () => {
    setGuideOpen(false)
    try {
      const pending = props.api.call("onboarding_ack", {}, { userInitiated: true })
      if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
    } catch (_error) {
      // 记不上就记不上：弹窗已经关了，绝不能因此挡住玩家。
    }
  }
  /**
   * 调一个 P10 的偏好入口（写完立刻刷新上下文）。
   *
   * **P11.1**：走全局请求锁；后端命中节流时（``throttled``）只给一条温和提示。
   */
  const callPace = async (action: string, args: Record<string, any>) => {
    const result = await callAction(action, args)
    if (result && result.throttled) {
      showToast(t("panel.action_throttled"), "info")
    }
  }

  // 三屏自动推进：第一屏停 3 秒（黑屏），之后每屏 2 秒。
  const deathScene = (state.settlement && state.settlement.death_scene) || null
  useEffect(() => {
    if (!deathScene || sceneScreen >= 3) return
    const delay = sceneScreen === 1 ? 3000 : 2000
    const timer = setTimeout(() => setSceneScreen((current) => current + 1), delay)
    return () => clearTimeout(timer)
  }, [sceneScreen, deathScene])

  // ── P7：荒诞弹窗 —— 交不起饭钱时弹一次「赛博网贷广告」───────────
  useEffect(() => {
    if (state.stage !== "playing") return
    if ((state.bill_gap || 0) <= 0) return
    const day = state.day || 0
    if (loanModalDay === day) return
    setLoanModalDay(day)
    setLoanModalOpen(true)
    // P7.3：告诉服务端「弹窗开了」——服务端据此启动 30 秒超时看门狗。
    // （服务端在渲染载荷时也会自己登记一遍，这里是双保险。）
    try {
      props.api
        .call("loan_prompt", { choice: "open" }, { userInitiated: true })
        .catch(() => undefined)
    } catch (_error) {
      // 登记失败也不要紧：服务端还有载荷侧登记 + 前端本地倒计时兜底。
    }
  }, [state.bill_gap, state.day, state.stage, loanModalDay, props.api])

  // ── P7.3：网贷弹窗的**本地倒计时**（前端兜底）────────────────────
  // 30 秒内没人操作 → 本地自己把弹窗关掉（服务端看门狗同时会自动结算）。
  // 计时与关窗**写在同一个定时器里**，避免两个 effect 抢同一个状态。
  useEffect(() => {
    if (!loanModalOpen) {
      loanDeadline.current = 0
      setLoanLeft(0)
      return undefined
    }
    if (loanDeadline.current <= 0) {
      loanDeadline.current = Date.now() + LOAN_PROMPT_TIMEOUT_SECONDS * 1000
    }
    setLoanLeft(LOAN_PROMPT_TIMEOUT_SECONDS)
    const timer = setInterval(() => {
      const left = Math.max(
        0,
        Math.ceil((loanDeadline.current - Date.now()) / 1000)
      )
      setLoanLeft(left)
      if (left <= 0) {
        // 到点了：本地关窗（**不动** loanModalDay —— 今天不再自动重开）。
        setLoanModalOpen(false)
      }
    }, 500)
    return () => clearInterval(timer)
  }, [loanModalOpen])

  // ── P7：长按标题栏 → 露出导演台（只在特制版）────────────────────
  const startHold = () => {
    if (!directorEnabled) return
    if (holdTimer.current) return
    setHoldHint(true)
    holdTimer.current = setTimeout(() => {
      holdTimer.current = null
      setHoldHint(false)
      setDirectorOpen(true)
    }, 1200)
  }
  const endHold = () => {
    if (holdTimer.current) {
      clearTimeout(holdTimer.current)
      holdTimer.current = null
    }
    setHoldHint(false)
  }
  useEffect(() => () => endHold(), [])

  // ── P7.2：濒临破产档「数字偶尔闪红」 ────────────────────────────────
  // **不用 CSS 动画**（0.8.0 的崩溃根因之一）：用 JS 定时器把颜色切一下。
  useEffect(() => {
    if (stage !== "dying") {
      setFlashOn(false)
      return
    }
    let hideTimer: any = 0
    const timer = setInterval(() => {
      setFlashOn(true)
      hideTimer = setTimeout(() => setFlashOn(false), 320)
    }, 2600)
    return () => {
      clearInterval(timer)
      clearTimeout(hideTimer)
    }
  }, [stage])

  // ── P14.0：资金水位条的**危机闪烁** ──────────────────────────────
  // 现金比明天的生活费还少 ⇒ 整根水位条变红并轻微闪烁。
  // 同样**不用 CSS 动画**：JS 定时器切颜色（跟上面濒临破产数字同款）。
  const nextBill = Math.max(0, Number(state.next_bill || 0))
  const cashNow = Math.max(0, Number(state.cash || 0))
  const cashCrisis = nextBill > 0 && cashNow < nextBill
  const [gaugeAlarm, setGaugeAlarm] = useState(false)
  useEffect(() => {
    if (!cashCrisis) {
      setGaugeAlarm(false)
      return
    }
    let hideTimer: any = 0
    const timer = setInterval(() => {
      setGaugeAlarm(true)
      hideTimer = setTimeout(() => setGaugeAlarm(false), 320)
    }, 900)
    return () => {
      clearInterval(timer)
      clearTimeout(hideTimer)
    }
  }, [cashCrisis])

  // ── P7.3：弹窗轰炸的**客户端硬上限**（不依赖载荷刷新）───────────
  // 0.8.2 用服务端的 `until` 时间戳减**客户端时钟**算剩余时间：时钟一偏、
  // 或者面板载荷停止刷新，`boom.active` 就会永远为 true ⇒ 永久锁屏。
  // 现在改成：**第一次看见它**的时候本地记一个截止时刻，到点无条件关窗。
  const boomPayloadActive = Boolean(boom.active)
  const boomWindow = Math.min(30, Math.max(1, Number(boom.duration || 5) || 5))
  useEffect(() => {
    if (!boomPayloadActive) {
      boomDeadline.current = 0
      return
    }
    if (boomDeadline.current <= 0) {
      boomDeadline.current = Date.now() + boomWindow * 1000
    }
  }, [boomPayloadActive, boomWindow])
  useEffect(() => {
    if (!boomPayloadActive) return
    const timer = setInterval(() => setBoomTick((count) => count + 1), 300)
    return () => clearInterval(timer)
  }, [boomPayloadActive])
  const boomLeft = Math.max(0, (boomDeadline.current - Date.now()) / 1000)
  const boomActive = boomPayloadActive && boomLeft > 0

  const stocks = state.stocks || []
  const positions = state.positions || []
  // P8.0：行情网格最多铺 12 张方块卡（再多是视觉噪音，不是信息）。
  const visibleStocks = stocks.slice(0, 12)
  const hiddenStockCount = Math.max(0, stocks.length - visibleStocks.length)
  // ── P21.0：三档 UI 密度（渐进式简化）──────────────────────────────
  //
  // 只影响**显示**：底层的计算、费率、判定一个字都没动。
  const uiLevel = uiLevelFor(String(state.mode || ""))
  const fullUi = uiLevel === "full"
  const guardedUi = uiLevel === "guarded"
  const minimalUi = uiLevel === "minimal"
  // 前两关的持仓只铺前 3 只（其余折成一行字）—— P16.0 的既有口径。
  const simpleUi = !fullUi
  // ── 教程例外：**教程点到哪，那块就必须看得见** ───────────────────
  //
  // 街机档的教程第 4 步就是「看水位」（锚点 ``gauge``），而街机档
  // 默认**不显示**水位条 —— 不处理的话，教程圈的就是空气
  // （P18.0 踩过一次同样的坑：锚点 class 漏了，圈了半天没圈到东西）。
  //
  // 所以：**教程正在圈的那一块，强制显示**。规则一句话：
  // 「教程点到哪，哪块就必须可见」。
  const tutorialNeeds = (anchor: string): boolean => tutorialAnchor === anchor
  // 水位条：入门起恢复（开始教生存了）；街机默认不显示。
  const showGauge = fullUi || guardedUi || tutorialNeeds("gauge")
  // 连击徽章：街机与入门都不给（纯爽局配件，前两关先教活下来）。
  const showCombo = fullUi
  // 集中度警告 / 计划台账：只有后面三档才玩。
  const showConcentration = fullUi
  const showPlanLedger = fullUi
  // 家道徽标（丰盈 / 断供 / 濒临破产）与「距离强制平仓线」：
  // 街机档没有账单，这两样**恒定没意义**，纯噪音 ⇒ 藏起来。
  const showFamilyBadge = !minimalUi
  const showKillLine = !minimalUi
  // 明日账单提示：街机档压根没有账单 ⇒ 不显示。
  const showBillAlert = !minimalUi
  // 台账按市值从大到小排；前两关只铺前 3 只，其余折成一行字。
  const rankedPositions = positions
    .slice()
    .sort((a, b) => Number(b.market_value || 0) - Number(a.market_value || 0))
  const shownPositions = simpleUi
    ? rankedPositions.slice(0, SIMPLE_POSITION_LIMIT)
    : rankedPositions
  const hiddenPositionCount = Math.max(0, rankedPositions.length - shownPositions.length)
  const news = state.news_today || []
  const catgirl = state.catgirl || null
  const pending = state.pending_action || null
  const klineIds = state.kline_stock_ids || []

  const actionById = (id: string): HostedAction | undefined =>
    (actions || []).find((item) => item.id === id)

  // ── P7.4：动作失败后把上下文拉回来 ────────────────────────────
  // 宿主的上下文不轮询，不主动拉的话面板会一直停在旧状态
  // （比如这一局已经结束了，屏幕上的按钮全是死的）。
  const resync = () => {
    try {
      const pending = props.api.refresh()
      if (pending && typeof pending.catch === "function") pending.catch(() => undefined)
    } catch (_error) {
      // 拉不到就算了。
    }
  }

  // 动作失败：先给人一句人话，再把上下文拉回来。
  const onActionError = (error: any) => {
    showToast(String((error && error.message) || error), "danger")
    resync()
  }

  const stockOptions = stocks
    .filter((stock) => stock.alive)
    .map((stock) => ({
      value: stock.id,
      label: stock.display_name || stock.name,
    }))

  const positionOptions = positions.map((position) => ({
    value: position.stock_id,
    label: position.display_name || position.name,
  }))

  // 没选过就默认第一只活着的标的。
  const effectiveStockId =
    selectedStock && stockOptions.some((option) => option.value === selectedStock)
      ? selectedStock
      : stockOptions.length > 0
        ? stockOptions[0].value
        : ""
  const effectiveSellStockId =
    sellStock && positionOptions.some((option) => option.value === sellStock)
      ? sellStock
      : positionOptions.length > 0
        ? positionOptions[0].value
        : ""

  // K 线标的：局中载荷只带前 20 只（限流），其余用「按需拉取」入口补。
  const klineStockId =
    selectedStock && stockOptions.some((option) => option.value === selectedStock)
      ? selectedStock
      : klineIds.length > 0
        ? klineIds[0]
        : effectiveStockId
  const klineStock = stocks.find((stock) => stock.id === klineStockId)
  const bundledKline = (klineStock && klineStock.kline) || []
  const fetchedKline =
    klineDetail && klineDetail.stock_id === klineStockId ? klineDetail.kline || [] : []
  const klinePoints = bundledKline.length > 0 ? bundledKline : fetchedKline

  const proposalKey = pending
    ? `${pending.kind || ""}:${(pending.stock_ids || []).join(",")}:${pending.amount || 0}`
    : ""
  const showPawnbroker =
    Boolean(pending) &&
    pending!.kind === "pawnbroker_sell" &&
    proposalKey !== "" &&
    proposalKey !== dismissedProposal

  // ── P7：三档的"资金"叫法 ────────────────────────────────────────
  const cashLabel =
    stage === "dying"
      ? t("visual.cash_dying")
      : stage === "broke"
        ? t("visual.cash_broke")
        : t("visual.cash_rich")
  // P7.2：总资产按档位换名字（零花钱 / 家底 / 救命钱）。
  const assetsLabel =
    stage === "dying"
      ? t("visual.assets_dying")
      : stage === "broke"
        ? t("visual.assets_broke")
        : t("visual.assets_rich")
  // P7.2：档位提示语（老爸给的钱 / 要交房租了 / 下顿饭的钱）。
  const stageHint =
    stage === "dying"
      ? t("visual.hint_dying")
      : stage === "broke"
        ? t("visual.hint_broke")
        : t("visual.hint_rich")
  const stageLabel =
    stage === "dying"
      ? t("visual.stage_dying")
      : stage === "broke"
        ? t("visual.stage_broke")
        : t("visual.stage_rich")
  // ── P20.0：玩家自报名号 + 命中的名号（**局中与结算页显示**）────────
  //
  // 空闲态**不显示**（那儿有输入框，名字已经看得见了）。
  // `label_key` 由后端给（`panel.name_egg_*`），面板自己查表显示徽标 ——
  // 面板**不解释**这个名号意味着什么（那是彩蛋引擎的事）。
  const playerName = String(state.player_name || "")
  const nameEgg = state.name_egg || null
  const nameEggLabel = nameEgg && nameEgg.label_key ? t(String(nameEgg.label_key)) : ""
  const showNameBadge = Boolean(playerName) && state.stage !== "idle"
  // P7.2：字体档位（只改字体，不改布局）。
  const stageClass =
    stage === "dying" ? "rrm-font-dying" : stage === "broke" ? "rrm-font-broke" : "rrm-font-rich"

  // ── P7.2：傻子版的第一屏数字 ────────────────────────────────────
  const totalAssets = Number(state.total_assets || 0)
  const initialCash = Number(state.initial_cash || 0)
  const pnl = totalAssets - initialCash
  const pnlColor = pnl > 0.001 ? "var(--success)" : pnl < -0.001 ? "var(--danger)" : "var(--muted)"
  const pnlHint =
    pnl > 0.001 ? t("visual.pnl_up") : pnl < -0.001 ? t("visual.pnl_down") : t("visual.pnl_flat")
  // 濒临破产档：数字**偶尔闪红**（用 JS 定时器切颜色，不用 CSS 动画）。
  const flashColor = flashOn ? "var(--danger)" : undefined

  // ── P16.0：盈利数字「跳动」────────────────────────────────────────
  // 街机档专用：**不用 CSS 动画**（0.8.0 的崩溃根因），用 JS 定时器把数字
  // 从旧值**滚**到新值，滚完再补一下弹跳 —— 让「赚到钱」这件事被看见。
  const combo = state.combo || null
  const comboEnabled = Boolean(combo && combo.enabled)
  const comboStreak = comboEnabled ? Math.max(0, Number(combo.streak || 0)) : 0
  const comboBest = comboEnabled ? Math.max(0, Number(combo.best || 0)) : 0
  const pnlShownRef = useRef(pnl)
  const prevPnlRef = useRef(pnl)
  const [pnlShown, setPnlShown] = useState(pnl)
  const [pnlPop, setPnlPop] = useState(false)
  useEffect(() => {
    const target = pnl
    const from = pnlShownRef.current
    if (!comboEnabled || Math.abs(target - from) < 0.005) {
      pnlShownRef.current = target
      setPnlShown(target)
      return
    }
    const started = Date.now()
    const duration = 520
    const timer = setInterval(() => {
      const ratio = Math.min(1, (Date.now() - started) / duration)
      const eased = 1 - Math.pow(1 - ratio, 3)
      const next = from + (target - from) * eased
      pnlShownRef.current = next
      setPnlShown(next)
      if (ratio >= 1) {
        clearInterval(timer)
        pnlShownRef.current = target
        setPnlShown(target)
      }
    }, 40)
    return () => clearInterval(timer)
  }, [pnl, comboEnabled])
  useEffect(() => {
    const previous = prevPnlRef.current
    prevPnlRef.current = pnl
    if (!comboEnabled || pnl <= previous + 0.005) return
    setPnlPop(true)
    const timer = setTimeout(() => setPnlPop(false), 300)
    return () => clearTimeout(timer)
  }, [pnl, comboEnabled])

  // ── P13：仓位控制（连续滑块 + 直输框）+ 实时预览 ──────────────────
  //
  // **为什么要彻底去量子化**：仓位被量化成 25/50/75/100 四档之后，玩家
  // 能做的只有"选一档"，"再压一点点""就差这一股"这种真实存在的动作
  // 做不出来。现在滑块是 **1% 步进的连续** 的，另外可以**直接输金额或
  // 股数** —— 系统反推仓位比例。"把最后几块钱也砸进去"应该做得到。
  //
  // **预览怎么算准**：手续费率是**引擎那一套**（``state.fees``，只读），
  // 所以"预计花费"和插件真正扣的钱**一致**（不是 0.995 那种经验折算）。
  //
  // **唯一的事实来源是仓位比例**：直输框只是**反推**出比例，真正成交的
  // 数量永远由比例算出来 ⇒ 拖滑块和手输不会打架、不会各算各的。
  // ── P14.0：计划买入台账（多标的）+ 买入/卖出**两条**独立仓位 ─────────
  //
  // **为什么要拆成两条**：P13 只有一个"仓位"，买入和卖出共用它 ——
  // 拖一下就同时改了"买多少"和"卖多少"，本来就该分开。
  // 更要紧的是**买入**那条要多一层约束：玩家可以给**好几只**标的排计划，
  // 一旦计划总额超过可用现金，**后拉的那条滑条必须被截断** ——
  // 那股"拉扯"的阻力感，就是在告诉玩家他的极限在哪。
  //
  // 台账的键是**标的 id**，值是**打算投入的绝对金额（元）**。
  // 用绝对金额而不是百分比：现金变少时这些金额**相对变重**，
  // 滑块会**自动回缩** —— 玩家看到的极限永远是真的。
  const cash = Math.max(0, Number(state.cash || 0))
  const [planBudgetMap, setPlanBudgetMap] = useLocalState<Record<string, number>>(
    "rrm.plan_budget",
    {}
  )
  const [sellPositionPct, setSellPositionPct] = useLocalState<number>(
    "rrm.sell_position_pct",
    100
  )
  // ── P21.0：新手兜底的「幽灵提示」──────────────────────────────────
  //
  // 很多主播会直接跳过教程开干 ⇒ 他第一次动手时，两眼一抹黑。
  // 所以：他**第一次动滑块 / 第一次成交**时，操作区旁边飘出一行小字
  //（「不会玩可以点上面的【呼叫猫娘】」）。
  //
  // **这是静态 UI 文案，不是 Yui 台词** —— 台词仍然 100% 由她生成
  // （P13 契约）。这里只负责**指路**：把人送到能问人的地方去。
  const [ghostHintOn, setGhostHintOn] = useState(false)
  const [ghostHintIn, setGhostHintIn] = useState(false)
  const ghostHintUsed = useRef(false)
  // 教程正在教 ⇒ 有人盯着，不需要这行提示。
  const tutorialCoaching = Boolean(tutorial.active)
  const triggerGhostHint = () => {
    if (ghostHintUsed.current || tutorialCoaching) return
    ghostHintUsed.current = true
    setGhostHintOn(true)
  }
  // 「飘出」= 先低一点点，再抬上来（**JS 定时器 + transform**，
  // 不用 CSS 动画 —— 0.8.0 的崩溃根因之一）。
  useEffect(() => {
    if (!ghostHintOn) {
      setGhostHintIn(false)
      return
    }
    const rise = setTimeout(() => setGhostHintIn(true), 30)
    const fade = setTimeout(() => setGhostHintOn(false), 9000)
    return () => {
      clearTimeout(rise)
      clearTimeout(fade)
    }
  }, [ghostHintOn])
  const planBudget = planBudgetMap || {}
  // 其它标的已经占用的额度（不含当前标的）。
  //
  // ── P21.0：前两关**不玩多标的计划** ⇒ 这里恒为 0 ──────────────────
  //
  // 台账藏起来了，台账占掉的额度也必须一起消失 ——
  // 否则玩家会撞上一个「滑条莫名其妙拉不满」的鬼现象，
  // 而他根本看不到原因（台账不在面板上）。
  //
  // 换句话说：前两关的买入滑条上限 = **全部现金**，就是一个纯粹的
  // 「这回掏多少」。
  const othersBudget = showPlanLedger
    ? Object.keys(planBudget).reduce((sum, key) => {
        if (key === effectiveStockId) return sum
        return sum + Math.max(0, Number(planBudget[key] || 0))
      }, 0)
    : 0
  // **本轮上限**：现金 − 别人占掉的 ⇒ 后拉动的滑条被它截断。
  const capBudget = Math.max(0, cash - othersBudget)
  const capPct = cash > 0 ? Math.min(100, (capBudget / cash) * 100) : 0
  // 当前标的的**有效**计划（超出上限的部分直接砍掉）。
  const currentPlanBudget = Math.max(
    0,
    Math.min(capBudget, Number(planBudget[effectiveStockId] || 0))
  )
  // 比例**允许是小数**：直输"买 10 股"折出来的比例可能是 33.33%，
  // 取整会让玩家要的 10 股变成 9 股 —— 悄悄改玩家的单子，最糟。
  const buyPositionPct = cash > 0 ? Math.min(100, (currentPlanBudget / cash) * 100) : 0
  const sellPct = Math.max(0, Math.min(100, Number(sellPositionPct || 0)))
  // 轨道填充比例：满格 = 本轮**上限**（上限被别的标的占掉时，轨道会变短）。
  const buyFillPct =
    capPct > 0 ? Math.max(0, Math.min(100, (buyPositionPct / capPct) * 100)) : 0
  // 买入仓位 ⇒ 写进当前标的的**计划买入**，并受全局上限**截断**。
  const setPositionPct = (value: number) => {
    const wanted = Math.max(0, Number(value) || 0)
    const budget = Math.min((cash * wanted) / 100, capBudget)
    setPlanBudgetMap((prev: Record<string, number>) => ({
      ...(prev || {}),
      [effectiveStockId]: budget,
    }))
    // P21.0：第一次动买入滑块 ⇒ 飘出新手兜底提示。
    triggerGhostHint()
  }
  const buyRate = Number(state.fees?.buy_rate || 0)
  const sellRate = Number(state.fees?.sell_rate || 0)
  const stampRate = Number(state.fees?.stamp_tax_rate || 0)

  // 浮点保险：比例 → 数量 的来回换算会带上 1e-14 量级的误差，直接
  // Math.floor 会把"明明是 1 股"算成 0 股。落在整数附近的就按整数算。
  const robustFloor = (value: number) => {
    if (!Number.isFinite(value)) return 0
    const nearest = Math.round(value)
    if (Math.abs(value - nearest) < 1e-6) return nearest
    return Math.floor(value)
  }

  const selectedStockRow = stocks.find((stock) => stock.id === effectiveStockId)
  const buyPrice = selectedStockRow ? Number(selectedStockRow.price || 0) : 0
  // 买入：预算 = 台账里给这只标的排的额度（**已被全局上限截断**）；
  // 按 ``notional × (1 + buy_rate) <= 预算`` 倒推数量。
  const buyBudget = currentPlanBudget
  const buyNotional = buyRate > -1 ? buyBudget / (1 + buyRate) : 0
  const buyAmount = buyPrice > 0 ? robustFloor(buyNotional / buyPrice) : 0
  const buyCost = buyAmount * buyPrice * (1 + buyRate)
  const buyCashAfter = Math.max(0, cash - buyCost)
  // 买完这一笔，可用现金会不会**掉到生活费红线以下**？
  const buyWarningAlarm = nextBill > 0 && buyCashAfter < nextBill

  const selectedPosition = positions.find((item) => item.stock_id === effectiveSellStockId)
  const sellAllAmount = selectedPosition ? Number(selectedPosition.sellable || 0) : 0
  const sellPrice = selectedPosition ? Number(selectedPosition.price || 0) : 0
  // 卖出：回款 = 数量 × 价 × (1 - 卖出手续费 - 印花税)（印花税单边收）。
  const sellAmount = robustFloor((sellAllAmount * sellPct) / 100)
  const sellIncome = sellAmount * sellPrice * (1 - sellRate - stampRate)
  const sellCashAfter = cash + sellIncome

  // ── P14.0：计划买入台账（多标的）──────────────────────────────────
  // 台账里排了计划的标的 —— **当前标的也在内**（它是第一个被排的）。
  // 「计划总额」是玩家**打算**花掉的钱；「还能花」是现金减去这个总额 ——
  // 两者的差就是玩家还能再往别的标的上塞多少。
  const planRows = Object.keys(planBudget)
    .filter((key) => Number(planBudget[key] || 0) > 0.005)
    .map((key) => {
      const row = stocks.find((stock) => stock.id === key)
      return {
        id: key,
        name: row ? String(row.display_name || row.name || key) : key,
        budget: Math.max(0, Number(planBudget[key] || 0)),
        current: key === effectiveStockId,
      }
    })
  const planTotal = planRows.reduce((sum, row) => sum + row.budget, 0)
  const dropPlan = (key: string) => {
    setPlanBudgetMap((prev: Record<string, number>) => {
      const next = { ...(prev || {}) }
      delete next[key]
      return next
    })
  }
  // 一局结束 / 换局 ⇒ 计划台账作废（上一局的"打算投多少"跟这一局没关系）。
  useEffect(() => {
    if (state.stage !== "playing") setPlanBudgetMap({})
  }, [state.stage])

  // ── P7.2：一键梭哈 / 一键割肉的数量 ─────────────────────────────
  const buyAllAmount = buyAmount

  // ── P13：直输框（股数 / 金额）⇒ 反推仓位比例 ──────────────────────
  //
  // 输入框显示**玩家正在敲的字**（草稿）；草稿是 ``null`` 时显示由比例
  // 算出来的值。**空串是"玩家把框清空了"**，和"没有草稿"是两回事 ——
  // 要是混为一谈，玩家一按退格，框里就立刻被派生值填回去，永远清不掉。
  // 同一侧只留一个草稿：改了这一个，另一个就让位。
  const [buySharesDraft, setBuySharesDraft] = useState<string | null>(null)
  const [buyMoneyDraft, setBuyMoneyDraft] = useState<string | null>(null)
  const [sellSharesDraft, setSellSharesDraft] = useState<string | null>(null)
  const [sellMoneyDraft, setSellMoneyDraft] = useState<string | null>(null)

  const clearTradeDrafts = () => {
    setBuySharesDraft(null)
    setBuyMoneyDraft(null)
    setSellSharesDraft(null)
    setSellMoneyDraft(null)
  }
  // 行情/现金变了 ⇒ 草稿作废（否则输入框里还留着上一次的数字）。
  const tradeSignature = [
    effectiveStockId,
    buyPrice,
    cash,
    effectiveSellStockId,
    sellPrice,
    sellAllAmount,
  ].join("|")
  useEffect(() => {
    clearTradeDrafts()
  }, [tradeSignature])

  // 买入「多少股」：这些股要花多少钱（含手续费）⇒ 除以现金 = 比例。
  const pctForBuyShares = (raw: number) => {
    const shares = Math.max(0, Math.floor(Number(raw) || 0))
    if (shares <= 0 || buyPrice <= 0 || cash <= 0) return 0
    const cost = shares * buyPrice * (1 + buyRate)
    return Math.max(0, Math.min(100, (cost / cash) * 100))
  }
  // 买入「多少钱」：先按费率**向下取整**能买多少股再折算比例 ——
  // 这样"输 100 块"就真的是 100 块上下，不会因为多出一分钱就少买一股。
  const pctForBuyMoney = (raw: number) => {
    const money = Math.max(0, Number(raw) || 0)
    if (money <= 0 || buyPrice <= 0 || cash <= 0) return 0
    const shares = Math.floor(money / (buyPrice * (1 + buyRate)))
    if (shares <= 0) return 0
    return pctForBuyShares(shares)
  }
  // 卖出「多少股」：占可卖数量的比例。
  const pctForSellShares = (raw: number) => {
    const shares = Math.max(0, Math.floor(Number(raw) || 0))
    if (shares <= 0 || sellAllAmount <= 0) return 0
    return Math.max(0, Math.min(100, (shares / sellAllAmount) * 100))
  }
  // 卖出「多少钱」：那是**期望回款**，先按费率倒推能卖多少股。
  const pctForSellMoney = (raw: number) => {
    const money = Math.max(0, Number(raw) || 0)
    if (money <= 0 || sellPrice <= 0 || sellAllAmount <= 0) return 0
    const per = sellPrice * (1 - sellRate - stampRate)
    if (per <= 0) return 0
    const shares = Math.floor(money / per)
    if (shares <= 0) return 0
    return pctForSellShares(shares)
  }

  const onBuySharesInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setBuyMoneyDraft(null)
    setBuySharesDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setPositionPct(pctForBuyShares(parsed))
  }
  const onBuyMoneyInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setBuySharesDraft(null)
    setBuyMoneyDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setPositionPct(pctForBuyMoney(parsed))
  }
  const onSellSharesInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setSellMoneyDraft(null)
    setSellSharesDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setSellPositionPct(pctForSellShares(parsed))
  }
  const onSellMoneyInput = (raw: string) => {
    const text = raw === "" ? "" : String(raw)
    setSellSharesDraft(null)
    setSellMoneyDraft(text)
    if (text === "") return
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) return
    setSellPositionPct(pctForSellMoney(parsed))
  }

  const buySharesShown = buySharesDraft === null ? String(buyAmount) : buySharesDraft
  const buyMoneyShown = buyMoneyDraft === null ? buyCost.toFixed(2) : buyMoneyDraft
  const sellSharesShown = sellSharesDraft === null ? String(sellAmount) : sellSharesDraft
  const sellMoneyShown = sellMoneyDraft === null ? sellIncome.toFixed(2) : sellMoneyDraft

  // ── P7：终局 Emoji 海 ───────────────────────────────────────────
  const emojiSea = useMemo(() => {
    const size = Math.max(50, Number(visual.emoji_sea_size || 60))
    const items: string[] = []
    for (let index = 0; index < size; index += 1) {
      items.push(index % 2 === 0 ? "🤡" : "🥣")
    }
    return items
  }, [visual.emoji_sea_size])

  // ── P7.2：赛博弹窗轰炸（关不掉，5 秒）───────────────────────────
  const boomModal = boomActive
    ? h(Modal, {
        open: true,
        title: t("visual.boom_title"),
        // **点遮罩 / 按 ESC 都关不掉** —— 只能等它自己倒计时结束。
        closeOnBackdrop: false,
        closeOnEscape: false,
        size: "lg",
        children: h(
          Stack,
          { gap: 8 },
          h(Text, null, t("visual.boom_body", { count: boom.count || 50 })),
          h(Alert, {
            tone: "warning",
            message: t("visual.boom_countdown", { seconds: Math.ceil(boomLeft) }),
          }),
          h(
            Grid,
            { cols: 10, gap: 2 },
            Array.from({ length: Number(boom.count || 50) }, (_item, index) =>
              h(
                "span",
                { key: index, style: { fontSize: "22px", lineHeight: "1.1" } },
                "🤡"
              )
            )
          )
        ),
      })
    : null

  // ── P7.3：标题栏右上角的**紧急逃生**（两步内联确认）────────────
  // 用途：处理所有不可预见的 UI 卡死（弹窗锁死、载荷不刷新、按钮点不动）。
  // 刻意**不用 Modal** 做二次确认 —— 万一面板已经卡死，再弹一个 Modal
  // 只会更糟；两步点确认（3 秒内再点一次）足够防误触。
  const escapeGame = () => {
    try {
      props.api
        .call("declare_bankrupt", {}, { userInitiated: true })
        .then(async () => {
          await props.api.refresh()
        })
        .catch((error: any) => {
          showToast(String((error && error.message) || error), "danger")
        })
    } catch (_error) {
      // 连调用都抛出来了：面板已经彻底坏了 —— 玩家只能手动重启插件。
    }
  }

  const armEscape = () => {
    if (escapeArmed) {
      if (escapeTimer.current) {
        clearTimeout(escapeTimer.current)
        escapeTimer.current = null
      }
      setEscapeArmed(false)
      escapeGame()
      return
    }
    setEscapeArmed(true)
    if (escapeTimer.current) clearTimeout(escapeTimer.current)
    escapeTimer.current = setTimeout(() => setEscapeArmed(false), 3000)
  }
  useEffect(
    () => () => {
      if (escapeTimer.current) clearTimeout(escapeTimer.current)
    },
    []
  )

  // ── P7：标题栏（长按露出导演台）─────────────────────────────────
  // 0.8.1：**只保留布局**，不再有背景 / 边框 / 圆角 —— 那层深色半透明底
  // 在宿主深浅色主题下都会让整块面板显脏。
  const titleBar = h(
    "div",
    {
      style: {
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "8px",
        flexWrap: "wrap",
      },
      onPointerDown: startHold,
      onPointerUp: endHold,
      onPointerLeave: endHold,
      onPointerCancel: endHold,
      onMouseDown: startHold,
      onMouseUp: endHold,
      onMouseLeave: endHold,
      onContextMenu: (event: any) => {
        if (event && typeof event.preventDefault === "function") event.preventDefault()
      },
    },
    h(
      "span",
      { className: "rrm-tag" },
      `${t("panel.title")} · ${stageLabel}`,
      // ── P20.0：名字 + 名号徽标（塞在标题里，**不占新的布局行**）──
      showNameBadge
        ? h(
            "span",
            {
              style: {
                marginLeft: "8px",
                padding: "1px 8px",
                borderRadius: "999px",
                border: "1px solid rgba(142, 171, 219, 0.35)",
                background: "rgba(142, 171, 219, 0.12)",
                fontSize: "12px",
                fontWeight: "700",
                color: "var(--fg)",
              },
            },
            playerName,
            nameEggLabel
              ? h(
                  "span",
                  {
                    style: {
                      marginLeft: "6px",
                      padding: "0 6px",
                      borderRadius: "999px",
                      background: "rgba(214, 72, 106, 0.16)",
                      color: "var(--danger)",
                    },
                  },
                  nameEggLabel
                )
              : null
          )
        : null
    ),
    // ── P10：自动/手动徽标 —— 一眼看出市场是自己走，还是等玩家推一拍 ────
    h(
      "span",
      {
        className:
          pace.auto === false
            ? "rrm-pace-badge rrm-pace-badge-manual"
            : "rrm-pace-badge rrm-pace-badge-auto",
      },
      pace.auto === false
        ? t("panel.pace_manual_badge")
        : t("panel.pace_auto_badge")
    ),
    // ── P10：局中也能随时翻《玩法说明》（不用回空闲态）─────────────────
    h(Button, {
      className: "rrm-guide-link",
      tone: "default",
      onClick: (event: any) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation()
        }
        setGuideOpen(true)
      },
      children: t("panel.guide_button"),
    }),
    // ── P11：【呼叫猫娘】—— 让**外置**猫娘（桌宠）开口说话 ────────────
    // 空闲态与局中都在标题栏里：一个位置，两个状态都够得着。
    h(Button, {
      className: "rrm-meow-call",
      tone: "default",
      // P11.1：有 action 在飞时禁用（全局请求锁）。
      disabled: busy,
      onClick: (event: any) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation()
        }
        callMeow()
      },
      children: t("panel.meow_call"),
    }),
    // 开源纯净版**不剧透**导演台：连提示都不给（入口本身插件层还会再挡一次）。
    directorEnabled
      ? h(
          "span",
          { style: { fontSize: "12px", opacity: holdHint ? 0.9 : 0.6 } },
          holdHint ? t("visual.director_hold") : t("visual.director_hint")
        )
      : null,
    // ── P7.3：紧急逃生（极小、不显眼；点两下才真退出）────────────
    state.stage === "playing"
      ? h(
          Button,
          {
            className: "rrm-escape",
            tone: "default",
            onClick: (event: any) => {
              if (event && typeof event.stopPropagation === "function") {
                event.stopPropagation()
              }
              armEscape()
            },
            onPointerDown: (event: any) => {
              if (event && typeof event.stopPropagation === "function") {
                event.stopPropagation()
              }
            },
            children: escapeArmed
              ? t("visual.escape_confirm")
              : t("visual.escape_exit"),
          }
        )
      : null
  )

  // ── P7：导演台（只在特制版；入口本身也会在插件层再挡一次）──────
  const directorConsole = directorOpen
    ? h(
        Card,
        { title: t("visual.director_title") },
        h(
          Stack,
          { gap: 8 },
          h(Alert, {
            tone: "warning",
            message: t("visual.director_locked"),
          }),
          h(
            Inline,
            { gap: 8, wrap: true, align: "center" },
            h(
              Select,
              {
                value: effectiveStockId,
                options: stockOptions,
                onChange: (value: any) => setSelectedStock(String(value)),
              }
            ),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_crash"),
              tone: "danger",
              values: { op: "crash", stock_id: effectiveStockId },
              onError: onActionError,
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_loan"),
              tone: "danger",
              values: { op: "loan" },
              onError: onActionError,
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_welfare"),
              tone: "danger",
              values: { op: "welfare" },
              onError: onActionError,
            }),
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_kill"),
              tone: "danger",
              values: { op: "kill" },
              onError: onActionError,
            }),
            // P7.2：赛博弹窗轰炸（关不掉的 Emoji 海 + 封锁操作 5 秒）。
            h(ActionButton, {
              action: actionById("director"),
              label: t("visual.director_boom"),
              tone: "danger",
              values: { op: "boom" },
              onError: onActionError,
            }),
            h(Button, {
              onClick: () => setDirectorOpen(false),
              children: [t("visual.director_closed")],
            })
          )
        )
      )
    : null

  // ── P7.2：猫娘实时弹幕框（**常驻面板底部**，文档流、不悬浮）─────
  const danmakuCard = h(
    Card,
    { title: t("visual.danmaku_title") },
    h(
      Stack,
      { gap: 6 },
      h(Text, null, t("visual.danmaku_hint")),
      danmakuList.length === 0
        ? h(Text, null, state.stage === "idle" ? idleTaunt : t("visual.danmaku_empty"))
        : h(
            Stack,
            { gap: 4 },
            danmakuList
              .slice(-3)
              .reverse()
              .map((item) =>
                h(
                  "span",
                  { key: item.id, style: { fontSize: "13px", lineHeight: "1.6" } },
                  item.text || ""
                )
              )
          )
    )
  )

  // ── P10：节奏卡（游戏速度 + 自动/手动）─────────────────────────────
  const paceCard = h(PaceControl, {
    pace: pace,
    t: t,
    ticksToSettle: state.stage === "playing" ? state.ticks_to_settle : undefined,
    // ── P11.1：把全局请求锁的 busy 态传下去（按钮禁用 + "推进中"）──
    busy: busy,
    onSpeed: (next: string) => callPace("set_game_speed", { speed: next }),
    onToggleAuto: () => callPace("set_auto_tick", { enabled: pace.auto === false }),
    onAdvance: () => callPace("advance_tick", {}),
  })

  // ── P10：《玩法说明》弹窗（三步图文）───────────────────────────────
  const guideModal = h(GuideModal, {
    open: guideOpen,
    t: t,
    onClose: closeGuide,
  })

  // ── P11：拟态外置对话弹窗（**兜底**）──────────────────────────────
  // 只有宿主接不住主动搭话时才会出现（``meowFallback`` 非空即弹）。
  const meowModal = h(MeowChatModal, {
    open: meowFallback !== null,
    t: t,
    line: meowFallback ? meowFallback.line : "",
    reason: meowFallback ? meowFallback.reason : "",
    onClose: () => setMeowFallback(null),
  })

  /** P12：选了某个剧情选项（**只有卡片投递失败时面板才会接管**）。 */
  const chooseStory = async (choiceId: string) => {
    const result = await callAction("story_choice", {
      chapter_id: String(story.pending || ""),
      choice_id: choiceId,
    })
    if (result === null) return
    showToast(t("panel.story_choice_done"), "info")
  }

  // ── P12：剧情沉浸模式（宿主原生 ``Modal size="full"``）──────────────
  //
  // **为什么不用自己写全屏**：宿主原生 ``Modal`` 的遮罩本来就是
  // ``position: fixed; inset: 0`` ⇒ 它**天然**铺满整个面板视口。
  // 我们**一个危险 CSS 都不用写**（``position`` / ``inset`` / ``z-index``
  // 全是宿主自己的）。
  //
  // **面板里画什么**：只有**纯文本场景**。猫娘在外置气泡里说，
  // 面板**不画立绘、不画对话框**；正常路径的选项按钮在**聊天卡片**上，
  // 只有卡片投递失败时这里才接管选项（优雅降级）。
  const storyChoices = (story.choices || []) as StoryChoice[]
  const storyCardOk = story.card_ok !== false
  const storyDebt = story.debt || null
  const storyModal = h(
    Modal,
    {
      open: storyOpen,
      title: String(story.title || t("panel.story_modal_title")),
      onClose: () => setStoryOpen(false),
      size: "full",
      children: h(
        Stack,
        { gap: 12 },
        h("div", { className: "rrm-story-scene" }, String(story.scene || "")),
        storyDebt
          ? h(Alert, {
              tone: "warning",
              message: t("panel.story_debt", {
                amount: Number(storyDebt.amount || 0).toFixed(2),
                days: Math.max(
                  0,
                  Number(storyDebt.due_day || 0) - Number(state.day || 0)
                ),
              }),
            })
          : null,
        storyCardOk
          ? h(Alert, { tone: "info", message: t("panel.story_modal_hint") })
          : h(
              "div",
              { className: "rrm-story-choices" },
              h("div", { className: "rrm-story-fallback-hint" }, t("panel.story_choose_here")),
              storyChoices.map((choice) =>
                h(
                  Button,
                  {
                    key: String(choice.id || ""),
                    className: "rrm-story-choice",
                    disabled: busy,
                    onClick: () => {
                      void chooseStory(String(choice.id || ""))
                    },
                  },
                  String(choice.label || "")
                )
              )
            )
      ),
    }
  )

  // ── 局终：展示归档的结算报告 ─────────────────────────────────────
  if (state.stage === "finished" && state.settlement) {
    const report = state.settlement
    const catgirlResult = report.catgirl_result || null
    const scene = (report.death_scene || null) as DeathScene | null
    const achievements =
      (state.achievements || report.achievements || null) as Achievements | null
    // P8.1：结算页也要能「再来一局」。老版本插件没把 join 挂成 UI action 时
    // 就**不渲染**这个按钮 —— 绝不能拿一个空 action 去调。
    const joinAction = actionById("join")

    // ── P5.5 改动 4：天桥虚惊一场（分三屏，第三屏是结算数据）──────
    if (scene && sceneScreen < 3) {
      const current =
        sceneScreen === 1 ? scene.screen1 : sceneScreen === 2 ? scene.screen2 : scene.screen3
      return (
        <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
          <Stack gap={12}>
            {h("style", null, RRM_CSS)}
            {titleBar}
            <Card title={t("panel.death_scene")}>
              <Stack gap={12}>
                {h("div", { style: { whiteSpace: "pre-wrap", lineHeight: "1.8" } }, current || "")}
                <Inline gap={8}>
                  <Button
                    onClick={() => {
                      setSceneScreen(3)
                    }}
                  >
                    {/* P8.1：嘲讽版「跳过」。 */}
                    {t("panel.death_scene_skip_mock")}
                  </Button>
                </Inline>
              </Stack>
            </Card>
            {guideModal}
            {meowModal}
            {storyModal}
            {danmakuCard}
          </Stack>
        </Page>
      )
    }

    // ── P5.5 改动 7：称号图鉴（第 12 区块）──────────────────────
    const rows: AchievementRow[] = []
    const unlockedList = (achievements && achievements.unlocked) || []
    const lockedList = (achievements && achievements.locked) || []
    for (const item of unlockedList) {
      rows.push({
        unlocked: true,
        title: item.title || "",
        text: item.epitaph || "",
      })
    }
    for (const item of lockedList) {
      rows.push({
        unlocked: false,
        title: item.title || "？？？",
        text: item.hint || "",
      })
    }

    // ── P8.1：结算页的三处「内部枚举不许见光」──────────────────────
    //   ① 标签用了带占位符的 "panel.mode"（"模式：{mode}"），而这里
    //      t() **没有传参** ⇒ 玩家看到的就是字面量「{mode}」。
    //      结算页的模式是**一行里的一个值**，所以标签单独给一个"模式"。
    //   ② 破产原因 / 结局 / 入场身份之前直接显示 starved / quick 这种英文枚举。
    const deadReport = Boolean(report.death_reason)
    const modeText = String(report.mode || "")
    const modeValue = modeText === "infinite"
      ? t("panel.mode_infinite")
      : modeText
        ? `${t("panel.mode_classic")} · ${report.mode_name || modeText}`
        : "—"

    return (
      <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          {/* P8.1：破产了就把标题换成嘲讽版（活下来也有话说）。 */}
          <Card
            title={
              deadReport ? t("panel.settlement_title_dead") : t("panel.settlement_title_alive")
            }
          >
            <Stack gap={12}>
              {/* P9.0：结算页配图换成**纯 CSS 拼的扁平空钱包**。
                  0.9.1 那张「破产猫」是 PIL 画的黄色土豆，看着像廉价表情包 ——
                  这次直接废掉这张图（素材文件也一并删掉），改成设计图同款的
                  扁平线性风格钱包。 */}
              {h(BankruptArt, { alt: t("panel.settlement_image_alt") })}
              <Alert tone="info" message={t("panel.settlement_notice")} />
              {/* P18.0：**静态合规实体**（兜底）。
                  台词由 Yui 在结算时自己说；这一条是写死的声明 ——
                  万一模型漏了，合规实体也必须落地。 */}
              <Alert tone="primary" message={t("panel.settlement_risk_note")} />
              <KeyValue
                items={[
                  { key: "mode", label: t("panel.mode_label"), value: modeValue },
                  { key: "days", label: t("panel.days_survived"), value: report.days_survived },
                  { key: "trades", label: t("panel.trades"), value: report.trade_count },
                  {
                    key: "reason",
                    label: t("panel.death_reason"),
                    value: enumText(t, "death_reason", report.death_reason),
                  },
                  { key: "title", label: t("panel.death_title"), value: report.death_title || "—" },
                  {
                    key: "ending",
                    label: t("panel.ending"),
                    value: enumText(t, "ending", report.ending),
                  },
                  {
                    key: "identity",
                    label: t("panel.identity"),
                    value: enumText(t, "identity.label", report.identity),
                  },
                ]}
              />
              {/* P5.5 改动 2：身份呼应句 —— 只在破产结算页出现 */}
              {report.identity_echo ? <Text>{report.identity_echo}</Text> : null}
              {/* P5.5 改动 1：「无为而治」的打工叙事 */}
              {report.inaction_worker ? <Text>{report.inaction_worker}</Text> : null}
              {/* 猫娘播报 */}
              {catgirlResult ? (
                <KeyValue
                  items={[
                    {
                      key: "catgirl_alive",
                      label: t("catgirl.alive"),
                      value: catgirlResult.alive ? t("catgirl.alive") : t("catgirl.dead"),
                    },
                    {
                      key: "catgirl_days",
                      label: t("catgirl.survived_days"),
                      value: catgirlResult.days_survived,
                    },
                    {
                      key: "catgirl_cash",
                      label: t("catgirl.cash"),
                      value: money(catgirlResult.final_cash),
                    },
                    {
                      key: "catgirl_trades",
                      label: t("panel.trades"),
                      value: catgirlResult.trade_count,
                    },
                    {
                      key: "catgirl_reason",
                      label: t("panel.death_reason"),
                      value: enumText(t, "death_reason", catgirlResult.death_reason),
                    },
                  ]}
                />
              ) : null}
              {/* P5.5 改动 3：猫娘沉默旁白（在猫娘播报之后） */}
              {report.catgirl_silence ? <Text>{report.catgirl_silence}</Text> : null}
              <Text>{report.anti_fraud_notice || ""}</Text>
              {/* P8.1：结算完别让玩家干瞪眼 —— 给一个**带嘲讽**的再来一局。 */}
              {joinAction ? (
                <Inline gap={8} align="center">
                  <ActionButton
                    action={joinAction}
                    label={t("panel.settlement_again")}
                    tone="primary"
                    values={{ mode: "classic_arcade" }}
                    onError={onActionError}
                  />
                </Inline>
              ) : null}
            </Stack>
          </Card>

          {/* ── P7 模块二 ④：Emoji 海结算（至少 50 个 🤡 / 🥣）── */}
          <Card title={t("visual.emoji_sea_title")}>
            <Stack gap={8}>
              <Text>{t("visual.emoji_sea_caption")}</Text>
              <Grid cols={10} gap={2}>
                {emojiSea.map((emoji, index) => (
                  <span key={index}>{emoji}</span>
                ))}
              </Grid>
            </Stack>
          </Card>

          {/* ⑫ 称号图鉴（P5.5 改动 7）：可折叠 */}
          {achievements ? (
            <Card title={t("panel.achievements")}>
              <Stack gap={8}>
                <Inline gap={8} align="center" wrap>
                  <StatusBadge
                    tone="default"
                    label={t("panel.achievements_count", {
                      unlocked: achievements.unlocked_count || 0,
                      total: achievements.total || 0,
                    })}
                  />
                  <Button
                    onClick={() => {
                      setAchievementsOpen(achievementsOpen === "1" ? "0" : "1")
                    }}
                  >
                    {achievementsOpen === "1"
                      ? t("panel.achievements_collapse")
                      : t("panel.achievements_expand")}
                  </Button>
                </Inline>
                {achievementsOpen === "1" ? (
                  <List<AchievementRow>
                    items={rows}
                    render={(row) => (
                      <Inline gap={8} align="center" wrap>
                        <StatusBadge
                          tone={row.unlocked ? "success" : "default"}
                          label={row.unlocked ? row.title : t("panel.achievements_locked")}
                        />
                        <Text>{row.text}</Text>
                      </Inline>
                    )}
                  />
                ) : null}
              </Stack>
            </Card>
          ) : null}
          {guideModal}
          {meowModal}
          {storyModal}
          {danmakuCard}
        </Stack>
      </Page>
    )
  }

  // ── 没开局 ───────────────────────────────────────────────────────
  if (state.stage !== "playing") {
    const joinAction = actionById("join")
    const joinActionId = joinAction
      ? String(joinAction.entry_id || joinAction.id || "join")
      : ""
    /** 按档位开局：走 P11.1 的全局请求锁（狂点只会被静默丢弃，不会打爆宿主）。 */
    const joinMode = (mode: string) => {
      if (!joinActionId) return
      // P20.0：把玩家自报名号一起带过去（后端会再洗一遍）。
      void callAction(joinActionId, { mode, player_name: nameDraft.trim() })
    }
    /**
     * P14.0：一块**档位卡**。
     *
     * 身份色写在顶部的渐变色带上，文字压在浅底（无尽那张是深底）上 ——
     * 颜色分得开，字也还读得清。
     */
    const modeCard = (opts: {
      title: string
      tagline: string
      hint: string
      band: string
      bg: string
      titleColor: string
      tagColor: string
      hintColor: string
      onClick: () => void
    }) =>
      h(
        "button",
        {
          type: "button",
          className: "rrm-mode-card",
          style: {
            display: "block",
            width: "100%",
            textAlign: "left",
            padding: "0",
            border: "1px solid rgba(142, 171, 219, 0.26)",
            borderRadius: "12px",
            overflow: "hidden",
            background: opts.bg,
            cursor: "pointer",
            fontFamily: "inherit",
          },
          onClick: opts.onClick,
        },
        h("div", { style: { height: "10px", background: opts.band } }),
        h(
          "div",
          {
            style: {
              padding: "12px 14px",
              display: "flex",
              flexDirection: "column",
              gap: "3px",
            },
          },
          h(
            "span",
            {
              style: {
                fontSize: "17px",
                fontWeight: "800",
                letterSpacing: "0.5px",
                color: opts.titleColor,
              },
            },
            opts.title
          ),
          h(
            "span",
            { style: { fontSize: "12px", fontWeight: "700", color: opts.tagColor } },
            opts.tagline
          ),
          h("span", { style: { fontSize: "12px", color: opts.hintColor } }, opts.hint)
        )
      )
    // ── P14.0：【命运挑战】的二级档位（入门 / 进阶 / 地狱）────────────
    const challengeLevels = [
      {
        mode: "classic_beginner",
        title: t("panel.mode_level_beginner"),
        tag: t("panel.mode_level_beginner_tag"),
        hint: t("panel.mode_level_beginner_hint"),
        accent: "#0f6b4c",
        band: "linear-gradient(90deg, #8fe0bf 0%, #17916f 100%)",
        bg: "linear-gradient(180deg, #effaf4 0%, #ddf3e7 100%)",
      },
      {
        mode: "classic_advanced",
        title: t("panel.mode_level_advanced"),
        tag: t("panel.mode_level_advanced_tag"),
        hint: t("panel.mode_level_advanced_hint"),
        accent: "#9c2b45",
        band: "linear-gradient(90deg, #f5c451 0%, #d6486a 100%)",
        bg: "linear-gradient(180deg, #fff6e2 0%, #ffe3e6 100%)",
      },
      {
        mode: "classic_hell",
        title: t("panel.mode_level_hell"),
        tag: t("panel.mode_level_hell_tag"),
        hint: t("panel.mode_level_hell_hint"),
        accent: "#e7e0ff",
        band: "linear-gradient(90deg, #7a3a63 0%, #d6486a 100%)",
        bg: "linear-gradient(180deg, #2b2650 0%, #16122f 100%)",
      },
    ]
    const challengeModal = h(Modal, {
      open: challengeOpen,
      title: t("panel.mode_challenge_modal_title"),
      closeOnBackdrop: true,
      closeOnEscape: true,
      size: "md",
      children: h(
        Stack,
        { gap: 10 },
        h(Text, null, t("panel.mode_challenge_modal_hint")),
        challengeLevels.map((level) =>
          h(
            "button",
            {
              type: "button",
              className: "rrm-mode-card",
              style: {
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "0",
                border: "1px solid rgba(142, 171, 219, 0.26)",
                borderRadius: "12px",
                overflow: "hidden",
                background: level.bg,
                cursor: "pointer",
                fontFamily: "inherit",
              },
              onClick: () => {
                setChallengeOpen(false)
                joinMode(level.mode)
              },
            },
            h("div", { style: { height: "8px", background: level.band } }),
            h(
              "div",
              {
                style: {
                  padding: "10px 12px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "2px",
                },
              },
              h(
                "span",
                {
                  style: {
                    fontSize: "15px",
                    fontWeight: "800",
                    color: level.accent,
                  },
                },
                level.title
              ),
              h(
                "span",
                { style: { fontSize: "12px", fontWeight: "700", color: level.accent } },
                level.tag
              ),
              h(
                "span",
                { style: { fontSize: "12px", color: level.accent } },
                level.hint
              )
            )
          )
        )
      ),
    })
    return (
      <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Stack gap={12}>
          {h("style", null, RRM_CSS)}
          {titleBar}
          {boomModal}
          <Card title={t("panel.idle_title")}>
            <Stack gap={12}>
              <Alert tone="info" message={state.notice || t("panel.idle")} />
              <Text>{t("panel.idle_hint")}</Text>
              {/* ── P20.0：名字输入框（在档位卡**上方**）──────────────
                  玩家先报个名，再挑档位。

                  提示语写得欠一点是故意的（"别用真名，丢人"）——
                  这是直播向的沙盘，认真取名反而出戏。

                  防呆在前端先做一遍（去换行、限长 12）；**后端还会再洗一遍**
                  —— 面板永远不可信。 */}
              {h(
                Field,
                {
                  label: t("panel.name_input_label"),
                  // P21.0：两行提示 —— 一行劝退，一行**吊胃口**
                  // （「名字太嚣张，容易触发特殊彩蛋」）。
                  help: h(
                    "span",
                    null,
                    t("panel.name_input_hint"),
                    h("br", null),
                    t("panel.name_input_hint2")
                  ),
                },
                h(Input, {
                  value: nameDraft,
                  placeholder: t("panel.name_input_placeholder"),
                  onChange: onNameInput,
                })
              )}
              {/* ── P7.4：真正的开局入口 ──────────────────────────────
                  0.8.3 事故：空闲态只有一行冰冷的文字，玩家**没法开局** ——
                  要么去聊天框打「加入一局」，要么就卡在这儿。这里给两个大按钮，
                  点一下直接开一局。

                  注意 ``joinAction`` 可能不存在（老版本插件没有把 join 挂成
                  UI action）—— 那就不渲染按钮，绝不能拿一个空 action 去调，
                  否则宿主会把它当成面板运行时错误上报。 */}
              {/* ── P14.0：档位卡（街机 / 命运挑战 / 无尽）──────────────
                  三档必须**一眼分得开**：
                  街机 = 纯爽局（浅绿，「老爸给的零花钱」）；
                  命运挑战 = 要动真格的（黄→深红，断供 / 杠杆 / 生存压力），
                  点开是**二级选择**（入门 / 进阶 / 地狱）；
                  无尽 = 深渊（深蓝紫 + 血红，「最后的筹码」）。

                  颜色全部走**内联渐变**（P14 的纪律：极度克制地使用 CSS），
                  一行新 CSS 都不加。 */}
              {joinAction ? (
                h(
                  "div",
                  {
                    style: {
                      display: "flex",
                      flexDirection: "column",
                      gap: "10px",
                    },
                  },
                  modeCard({
                    title: t("panel.mode_arcade_title"),
                    tagline: t("panel.mode_arcade_tag"),
                    hint: t("panel.mode_arcade_hint"),
                    band: "linear-gradient(90deg, #8fe0bf 0%, #17916f 100%)",
                    bg: "linear-gradient(180deg, #effaf4 0%, #ddf3e7 100%)",
                    titleColor: "#0f6b4c",
                    tagColor: "#17916f",
                    hintColor: "#5d7d70",
                    onClick: () => joinMode("classic_arcade"),
                  }),
                  modeCard({
                    title: t("panel.mode_challenge_title"),
                    tagline: t("panel.mode_challenge_tag"),
                    hint: t("panel.mode_challenge_hint"),
                    band: "linear-gradient(90deg, #f5c451 0%, #d6486a 100%)",
                    bg: "linear-gradient(180deg, #fff6e2 0%, #ffe3e6 100%)",
                    titleColor: "#9c2b45",
                    tagColor: "#c0455f",
                    hintColor: "#8a6a70",
                    onClick: () => setChallengeOpen(true),
                  }),
                  modeCard({
                    title: t("panel.mode_infinite_title"),
                    tagline: t("panel.mode_infinite_tag"),
                    hint: t("panel.mode_infinite_hint"),
                    band: "linear-gradient(90deg, #7a3a63 0%, #d6486a 100%)",
                    bg: "linear-gradient(180deg, #2b2650 0%, #16122f 100%)",
                    titleColor: "#e7e0ff",
                    tagColor: "#b9a8f0",
                    hintColor: "#8d86b8",
                    onClick: () => joinMode("infinite"),
                  })
                )
              ) : null}
              <Text>{t("panel.idle_modes_hint")}</Text>
              {/* ── P10：大号《玩法说明》按钮 ──────────────────────────
                  上手门槛太高是这一版最致命的问题：玩家打开面板完全不知道
                  该干嘛。所以「怎么玩」必须比「开局」还醒目 —— 一个占满
                  整行的大胶囊按钮，点了弹出三步图文说明。 */}
              <Button
                className="rrm-guide-btn"
                tone="primary"
                onClick={() => {
                  setGuideOpen(true)
                }}
              >
                {[
                  h(
                    "span",
                    { className: "rrm-guide-btn-main", key: "main" },
                    t("panel.guide_button")
                  ),
                  h(
                    "span",
                    { className: "rrm-guide-btn-sub", key: "sub" },
                    t("panel.guide_button_sub")
                  ),
                ]}
              </Button>
            </Stack>
          </Card>
          {paceCard}
          {challengeModal}
          {guideModal}
          {meowModal}
          {storyModal}
          {danmakuCard}
        </Stack>
      </Page>
    )
  }

  // ── P7 模块二 ②：荒诞弹窗（赛博网贷广告）─────────────────────
  // P7.3：**点按钮 = 同步关窗**，然后才异步调后端。
  //
  // 0.8.2 的死锁根因：这里用的是宿主 `ActionButton`，而它的 `onResult` 被
  // `await props.api.refresh()` 挡在后面 —— 刷新一失败，回调永远不来，弹窗就永远
  // 关不掉；无限模式下这个弹窗**又只有那一个按钮**，于是玩家被彻底锁死，
  // 连「宣布破产」都点不了（它被遮罩挡住了）。
  //
  // 现在有三道保险，任何一道都能把玩家放出来：
  //   ① 不用 `ActionButton`，直接宿主 `Button` + `props.api.call`，**点了立刻关**；
  //   ② **永远**给一个「我不借了，把我清出去吧」的逃生按钮；
  //   ③ 30 秒本地倒计时，到点自己关（服务端看门狗同时自动结算）。
  const loanChoice = (choice: string) => {
    // ── 同步关窗：不管后端成不成功，玩家都不会被卡住 ──
    setLoanModalOpen(false)
    try {
      props.api
        .call("loan_prompt", { choice }, { userInitiated: true })
        .then(async (result: any) => {
          await props.api.refresh()
          const note = result && result.note ? String(result.note) : ""
          if (note) {
            showToast(note, result && result.failed ? "danger" : "info")
          }
        })
        .catch((error: any) => {
          showToast(String((error && error.message) || error), "danger")
        })
    } catch (_error) {
      // 连调用都抛出来了：弹窗已经关了，至少玩家还能继续操作别的。
    }
  }

  const loanCanBorrow = state.mode === "infinite"

  const loanModal = h(Modal, {
    open: loanModalOpen,
    title: t("visual.loan_modal_title"),
    // 点遮罩 / 按 ESC 依然关不掉（这是广告的排面）——
    // 但**按钮永远在**，而且 30 秒后自动收工，玩家绝不会被困住。
    closeOnBackdrop: false,
    closeOnEscape: false,
    size: "md",
    children: h(
      Stack,
      { gap: 12 },
      // P8.0：弹窗配图（猫爪按着合同）—— 最基础的 <img>，不带 onError
      // 也无所谓：它是**内联 data URI**，不存在加载失败这回事。
      h("img", {
        src: MEOW_LOAN,
        alt: t("visual.loan_modal_image_alt"),
        style: { width: "160px", height: "120px", borderRadius: "12px" },
      }),
      h(Text, null, t("visual.loan_modal_body")),
      h(Alert, { tone: "danger", message: t("visual.loan_modal_note") }),
      // P7.3：把逃生通道**写在脸上**（玩家不用猜有没有出路）。
      h(Alert, { tone: "info", message: t("visual.loan_modal_escape_hint") }),
      // P7.3：倒计时（玩家看得见自己还剩多久就要被自动结算）。
      h(Alert, {
        tone: "warning",
        message: t("visual.loan_modal_countdown", { seconds: loanLeft }),
      })
    ),
    footer: h(
      "div",
      { className: "neko-button-group" },
      // ── 「借走 500 万」（经典模式没有高利贷，就不显示）─────────
      loanCanBorrow
        ? h(Button, {
            tone: "danger",
            onClick: () => {
              loanChoice("borrow")
            },
            children: t("visual.loan_modal_confirm"),
          })
        : null,
      // ── 逃生通道：**永远存在**，无限模式也有 ──────────────────
      h(Button, {
        tone: "default",
        onClick: () => {
          loanChoice("refuse")
        },
        children: t("visual.loan_modal_refuse"),
      })
    ),
  })

  return (
    <Page className="rrm-root" title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Stack className={stageClass} gap={16}>
        {h("style", null, RRM_CSS)}
        {/* ── P18.0：动态聚焦引导（开场 / 上课 / 结课）───────────────── */}
        {h(TutorialOverlay, {
          tutorial: tutorial,
          t: t,
          onBegin: () => tutorialCall("tutorial_begin"),
          onAdvance: () => tutorialCall("tutorial_advance"),
          onSkip: () => tutorialCall("tutorial_skip"),
        })}
        {/* ── 0.8.1 病危表现：**只有**一条红色 Alert 警告条（文档流内）。
            绝不用闪烁 / 半透明 / 遮罩 —— 那是 0.8.0 崩 UI 的根因。───── */}
        {critical ? (
          <Alert
            tone="danger"
            message={`${visual.critical_emoji || "💸"} ${t("visual.critical")}`}
          />
        ) : null}
        {titleBar}
        {directorConsole}
        {boomModal}
        {guideModal}
        {meowModal}
        {storyModal}

        {/* ── P7.2 ⓪：状态条（压缩成一行徽标，别占地方）────────────── */}
        <Inline className="rrm-anchor-status-bar" gap={8} wrap align="center">
          <StatusBadge
            tone={PHASE_TONE[state.phase || ""] || "default"}
            label={t(`phase.${state.phase || "honeymoon"}`)}
          />
          <StatusBadge tone="default" label={t("panel.day", { day: state.day })} />
          <StatusBadge
            tone="default"
            label={t("panel.mode", { mode: state.mode_name || state.mode || "" })}
          />
          <StatusBadge
            tone="default"
            label={t("panel.to_settle", { ticks: state.ticks_to_settle })}
          />
          <StatusBadge tone="default" label={t("panel.tick", { tick: state.tick })} />
          {/* ── P21.0：家道徽标（丰盈 / 断供 / 濒临破产）──────────────
              街机档没有账单 ⇒ 家道**恒定**是「丰盈」⇒ 纯噪音，藏起来。 */}
          {showFamilyBadge ? <StatusBadge tone="default" label={stageLabel} /> : null}
          {/* ── P21.0：「距离强制平仓线」—— 街机档没有线可距 ⇒ 不显示。
              （这行字在街机档永远是「—」，而且「强制平仓」四个字本身就是
                新手最不需要看到的词。） */}
          {showKillLine ? (
            <Text>
              {t("panel.distance_to_kill")}:{" "}
              {state.distance_to_kill === null || state.distance_to_kill === undefined
                ? "—"
                : money(state.distance_to_kill)}
            </Text>
          ) : null}
        </Inline>

        {/* ── P19.0：导演段的「进度」（**只在开关打开时才渲染**）────────
            直播特制版的开关 ``[director] show_progress = true``。
            开关关闭时 ``state.director_progress`` 是 undefined ⇒ **整块不渲染**，
            面板与以前逐像素一样。 */}
        {state.director_progress ? (
          <Inline gap={8} wrap align="center" className="rrm-director-progress">
            <StatusBadge
              tone={(state.director_progress.hype || 0) > 0.6 ? "danger" : "default"}
              label={t("director.progress.hype", {
                value: (state.director_progress.hype || 0).toFixed(2),
              })}
            />
            <StatusBadge
              tone="default"
              label={t("director.progress.chapter", {
                current: state.director_progress.chapter || 0,
                total: state.director_progress.chapter_total || 0,
              })}
            />
            {state.director_progress.chapter_title ? (
              <StatusBadge
                tone="default"
                label={state.director_progress.chapter_title}
              />
            ) : null}
          </Inline>
        ) : null}

        {/* ── P10：节奏卡（游戏速度 + 自动/手动）─────────────────────
            放在状态条正下方：自动/手动必须**一眼可见** —— 玩家抱怨的
            "Tick 快得让人窒息"，靠的就是这一张卡来解。 */}
        {paceCard}

        {/* ── P7.2 ①：K 线 —— 面板最上方的**主视觉**，占满全宽 ─────── */}
        <Card title={t("panel.kline")}>
          <Stack gap={8}>
            {stocks.length === 0 ? (
              <Text>{t("panel.kline_empty")}</Text>
            ) : (
              <>
                <Inline gap={8} wrap align="center" className="rrm-kline-row">
                  <Select
                    value={klineStockId}
                    options={stockOptions}
                    onChange={(value: any) => setSelectedStock(String(value))}
                  />
                  <ActionButton
                    action={actionById("kline")}
                    label={t("panel.kline_fetch")}
                    values={{ stock_id: klineStockId }}
                    onResult={(result: any) => {
                      if (result) setKlineDetail(result as Record<string, any>)
                    }}
                    onError={(error: any) =>
                      showToast(String((error && error.message) || error), "danger")
                    }
                  />
                  {klineStock ? (
                    <Text>
                      {t("panel.kline_hint", {
                        name: klineStock.display_name || klineStock.name,
                        price: money(klineStock.price),
                      })}
                    </Text>
                  ) : null}
                </Inline>
                <Kline
                  points={klinePoints}
                  emptyText={t("panel.kline_empty")}
                  warmingText={t("panel.kline_warming")}
                />
              </>
            )}
          </Stack>
        </Card>

        {/* ── P8.0 ②：家道三档的第一屏数字 ──────────────────────────
            顺序改成「盈亏 | 现钱 | **身家**」—— 身家那张卡落在**右上角**，
            它的名字随 stage 变：零花钱（富二代）/ 家底（断供）/ 救命钱（濒临破产）。 */}
        <Grid cols={3} gap={12}>
          {/* P21.0：字号改成 **CSS 表达式** ⇒ 全屏（面板更宽）时数字跟着变大，
              窄窗口又不会缩成一团。``vw`` 指的是 iframe 自己的宽度。 */}
          <BigStat
            label={t("visual.pnl_title")}
            value={`${pnlShown > 0 ? "+" : ""}${money(pnlShown)}`}
            hint={`${t("visual.total_pnl")} · ${pnlHint}`}
            color={pnlColor}
            size="clamp(30px, 3.4vw, 58px)"
            pop={pnlPop}
          />
          <BigStat
            label={cashLabel}
            value={money(state.cash)}
            hint={stageHint}
            // P14.0：现金跌破生活费红线 ⇒ 数字**变红并轻微闪烁**。
            color={
              cashCrisis
                ? gaugeAlarm
                  ? "rgba(214, 72, 106, 0.45)"
                  : "var(--danger)"
                : undefined
            }
            size="clamp(26px, 2.8vw, 46px)"
          />
          <BigStat
            label={assetsLabel}
            value={money(totalAssets)}
            hint={t("visual.assets_hint", { stage: stageLabel })}
            color={flashColor}
            size="clamp(30px, 3.4vw, 58px)"
          />
        </Grid>

        {/* ── P16.0：连击徽章（P21.0：只有后面三档才给看）────────────── */}
        {showCombo ? h(ComboBadge, { streak: comboStreak, best: comboBest, t: t }) : null}

        {/* ── P14.0：资金水位条（P21.0：入门起恢复；街机默认藏起来）────
            **教程圈到它的时候强制显示** —— 教程点到哪，哪块就必须可见。 */}
        {showGauge
          ? h(CashGauge, {
              cash: cash,
              bill: nextBill,
              crisis: cashCrisis,
              alarmOn: gaugeAlarm,
              t: t,
              // 前两关压成**一行字**（完整的水位条在那里是干扰）。
              compact: simpleUi,
            })
          : null}

        {/* ── P7.2 ③：明日账单（交不起就是红色警告）──────────────────
            P21.0：街机档没有账单 ⇒ 整块不渲染（恒定的「明天要交 0」是噪音）。 */}
        {showBillAlert ? (
          (state.bill_gap || 0) > 0 ? (
            <Alert
              tone="danger"
              message={t("panel.bill_warn", {
                gap: money(state.bill_gap),
                next: money(state.next_bill),
                cash: money(state.cash),
                missed: state.missed_bills || 0,
              })}
            />
          ) : (
            <Alert
              tone="success"
              message={t("panel.bill_ok", { next: money(state.next_bill), cash: money(state.cash) })}
            />
          )
        ) : null}

        {/* ── P8.0 ③：中半区 —— 行情**方块卡片网格** ──────────────────
            用宿主 `Columns` 的 fluid 模式：面板宽就是 4 列，窄就是 2 列。
            每张方块卡 = 股票名（+ 群友头像）+ 微型折线 + 巨大涨跌百分比。
            卡片是 `Columns` 的**直接**子元素 —— 中间没有任何 div 包裹层。 */}
        <Card className="rrm-anchor-market-grid" title={t("visual.market_title")}>
          {stocks.length === 0 ? (
            <Text>{t("panel.no_stocks")}</Text>
          ) : (
            <Stack gap={10}>
              <Columns fluid minWidth={228} gap={16}>
                {visibleStocks.map((stock) => h(StockTile, { key: stock.id, stock, t }))}
              </Columns>
              {hiddenStockCount > 0 ? (
                <Text>{t("panel.more_stocks", { count: hiddenStockCount })}</Text>
              ) : null}
            </Stack>
          )}
        </Card>

        {/* ── P7.2 ⑤：持仓列表（只留名称 / 市值 / 大号盈亏）───────── */}
        <Card className="rrm-anchor-position-grid" title={t("visual.holdings_title")}>
          <Stack gap={12}>
            {positions.length === 0 ? (
              <Text>{t("panel.no_positions")}</Text>
            ) : (
              <Stack gap={12}>
                <Columns fluid minWidth={196} gap={16}>
                  {shownPositions.map((position) =>
                    h(PositionTile, { key: position.stock_id, position, t })
                  )}
                </Columns>
                {hiddenPositionCount > 0 ? (
                  <Text>{t("panel.positions_more", { count: hiddenPositionCount })}</Text>
                ) : null}
              </Stack>
            )}
            {/* ── P14.0：集中度警告（P21.0：只有后面三档才给看）────────
                前两关的诉求是「闭着眼睛瞎买也能赚钱」——
                「你的钱全压在一只上」这种警告在这里是**干扰**。 */}
            {showConcentration && (state.concentration || 0) > 0.6 ? (
              <Alert
                tone="warning"
                message={t("visual.concentration_warn", {
                  value: percent(state.concentration),
                })}
              />
            ) : null}
          </Stack>
        </Card>

        {/* ── P13：操作台 · 连续仓位 + 直输框 + 执行 ───────────────
            P12 把「一键梭哈 / 一键割肉」换成了仓位滑块，但滑块只有
            25/50/75/100 四档 —— 仓位还是被**量化**的。P13 把它拆干净：

            * 滑块是宿主**原生** ``Slider``，步进改成 **1%**（连续）；
            * **删掉** 25/50/75/100 那四档快捷键（那才是"去量子化"的
              拦路虎）；
            * 预览区多两个**直输框**（股数 / 金额），输入后系统**反推**
              仓位比例并同步滑块；
            * 预览用**引擎那一套费率**（``state.fees``），所以"预计花费"
              和真扣的钱**一致**；
            * 执行按钮走 ``callAction`` ⇒ **纳入 P11.1 的全局请求锁**
              （``ActionButton`` 不支持 ``disabled``，也进不了这把锁）。 */}
        <Card className="rrm-anchor-trade-console" title={t("panel.trade")}>
          <Stack gap={12}>
            {/* ── P14.0：买 / 卖**两条**独立仓位 ───────────────────────
                P13 只有一个"仓位"，买和卖共用它 —— 拖一下就同时改了
                "买多少"和"卖多少"，本来就该分开。P14 拆成两条，
                左右各看各的，标的也各自带在顶上。

                买入那条还多一层**全局上限**：台账里别的标的占掉的额度
                会把它**截断**（后拉动的滑条只能拉到上限为止）——
                那股"拉扯"的阻力感，就是在告诉玩家他的极限在哪。 */}
            <Grid cols={2} gap={12} className="rrm-trade-sides">
              {/* ── 买入这一侧 ─────────────────────────────────── */}
              <Stack gap={6} className="rrm-trade-side">
                <Inline gap={10} wrap={false} align="center" className="rrm-select-row">
                  <Select
                    value={effectiveStockId}
                    options={stockOptions}
                    onChange={(value: any) => setSelectedStock(String(value))}
                  />
                </Inline>
                <Text>{t("visual.buy_all_hint")}</Text>
                <Inline gap={8} align="center" justify="space-between">
                  <Text>{t("panel.trade_buy_side")}</Text>
                  <Text>{t("panel.trade_pct", { pct: Math.round(buyPositionPct) })}</Text>
                </Inline>
                {h(
                  "div",
                  {
                    className: "rrm-trade-slider",
                    // 当前比例写进 CSS 变量 ⇒ 轨道渐变能画出"已填充到哪"。
                    // 轨道满格 = 本轮**上限**（不是 100%）—— 上限被占满时
                    // 整条填满，玩家一眼看见自己已经顶到天花板。
                    style: { "--rrm-trade-fill": `${Math.round(buyFillPct * 10) / 10}%` },
                  },
                  h(Slider, {
                    min: 0,
                    // **上限不是 100**：别的标的占掉的额度会把这条滑条截断。
                    max: Math.max(1, Math.floor(capPct)),
                    step: 1,
                    value: buyPositionPct,
                    showValue: false,
                    // 上限被吃光了 ⇒ 这条滑条**锁死**（连 1% 都分不到）。
                    disabled: busy || capPct <= 0,
                    onChange: (value: any) => {
                      setPositionPct(Number(value))
                      clearTradeDrafts()
                    },
                  })
                )}
                <Text>{t("panel.trade_position_hint")}</Text>
                <Text>
                  {t("panel.trade_cap_hint", {
                    cap: Math.max(0, Math.floor(capPct)),
                    used: othersBudget.toFixed(2),
                  })}
                </Text>
              </Stack>

              {/* ── 卖出这一侧 ─────────────────────────────────── */}
              <Stack gap={6} className="rrm-trade-side">
                <Inline gap={10} wrap={false} align="center" className="rrm-select-row">
                  <Select
                    value={effectiveSellStockId}
                    options={positionOptions}
                    onChange={(value: any) => setSellStock(String(value))}
                  />
                </Inline>
                <Text>{t("visual.sell_all_hint")}</Text>
                <Inline gap={8} align="center" justify="space-between">
                  <Text>{t("panel.trade_sell_side")}</Text>
                  <Text>{t("panel.trade_pct", { pct: Math.round(sellPct) })}</Text>
                </Inline>
                {h(
                  "div",
                  {
                    className: "rrm-trade-slider",
                    style: { "--rrm-trade-fill": `${Math.round(sellPct * 10) / 10}%` },
                  },
                  h(Slider, {
                    min: 0,
                    max: 100,
                    step: 1,
                    value: sellPct,
                    showValue: false,
                    onChange: (value: any) => {
                      setSellPositionPct(Number(value))
                      clearTradeDrafts()
                      // P21.0：第一次动卖出滑块 ⇒ 飘出新手兜底提示。
                      triggerGhostHint()
                    },
                  })
                )}
                <Text>{t("panel.trade_sell_hint", { amount: sellAllAmount })}</Text>
              </Stack>
            </Grid>

            {/* ── P14.0：买入后的**水位预警** ─────────────────────────
                玩家拉满滑块的时候，得让他看见"买完还剩多少"，
                以及"这一笔会不会把生活费吃掉"—— 低于红线就变红。 */}
            {h(
              "div",
              {
                className: "rrm-trade-warning",
                style: {
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "8px",
                  flexWrap: "wrap",
                  padding: "8px 10px",
                  borderRadius: "8px",
                  fontSize: "12px",
                  lineHeight: "1.5",
                  background: buyWarningAlarm
                    ? "rgba(214, 72, 106, 0.12)"
                    : "rgba(91, 143, 249, 0.08)",
                  border: buyWarningAlarm
                    ? "1px solid rgba(214, 72, 106, 0.5)"
                    : "1px solid rgba(91, 143, 249, 0.22)",
                  color: buyWarningAlarm ? "var(--danger)" : "var(--muted)",
                },
              },
              h(
                "span",
                null,
                t("panel.trade_after_buy", { cash: buyCashAfter.toFixed(2) })
              ),
              buyWarningAlarm
                ? h(
                    "span",
                    { style: { fontWeight: "700" } },
                    t("panel.trade_below_bill", { bill: nextBill.toFixed(2) })
                  )
                : null
            )}

            {/* ── P21.0：新手兜底的「幽灵提示」──────────────────────
                跳过教程直接开干的人，第一次动手时旁边飘出这一行。
                静态 UI 文案（**不是** Yui 台词）—— 只负责指路。 */}
            {ghostHintOn
              ? h(
                  "div",
                  {
                    className: ghostHintIn
                      ? "rrm-ghost-hint rrm-ghost-hint-in"
                      : "rrm-ghost-hint",
                    style: {
                      fontSize: "12px",
                      lineHeight: "1.6",
                      color: "var(--muted)",
                      fontStyle: "italic",
                      padding: "2px 2px 0",
                    },
                  },
                  t("panel.ghost_hint")
                )
              : null}

            {/* ── P8.0 ④：两个胶囊大按钮**并排**独占一行 ──────────────
                真机教训（0.9.0 首轮）：它们原本和 `Select` 挤在同一行，
                `Select` 抢走了几乎全部宽度 ⇒ 两个大按钮被压成竖排细条、
                「一键割肉」还被卡片裁掉。
                真机教训（0.9.0 二轮）：改用 `Inline` 还是不均分 ——
                `ActionButton` 会把按钮包进 `<div class="neko-action-control">`，
                而那个壳是 `inline-grid`（收缩到内容宽），`flex: 1 1 0` 落不到
                真正的 flex 子项上。
                所以这里用 **`Grid cols={2}`** —— 网格列天生等分，不管子项
                是裸 `<button>` 还是带壳的 `ActionButton`。

                P12：两个按钮从 `ActionButton` 换成 **`Button` + `callAction`** ——
                `ActionButton` 既不支持 `disabled`，也进不了 P11.1 的全局
                请求锁（狂点会绕过"点击即禁用"）。 */}
            <Grid cols={2} gap={8} className="rrm-trade-row">
              <Button
                className="rrm-pill rrm-pill-buy"
                disabled={busy}
                onClick={() => {
                  void executeBuy()
                }}
              >
                {t("panel.trade_execute_buy")}
              </Button>
              <Button
                className="rrm-pill rrm-pill-sell"
                disabled={busy}
                onClick={() => {
                  void executeSell()
                }}
              >
                {t("panel.trade_execute_sell")}
              </Button>
            </Grid>

            {/* ── P9.0：两个大按钮下面各有一行小字（主副标题的层次）────
                P12：文案跟着按钮一起改了 ——「All In / Stop Loss」不再是事实
                （现在是按仓位买），所以换成「按上面的仓位买 / 卖」。 */}
            <Inline gap={8} className="rrm-pill-captions">
              <span className="rrm-pill-caption rrm-pill-caption-buy">
                {t("panel.trade_execute_buy_sub")}
              </span>
              <span className="rrm-pill-caption rrm-pill-caption-sell">
                {t("panel.trade_execute_sell_sub")}
              </span>
            </Inline>

            {/* ── 实时预览 + 直输框（两列，跟上面的按钮对齐）─────────
                P13：预览下面各挂两个输入框 —— **股数** 与 **金额**。
                玩家可以直接说"我要买 10 股"或"我要买 100 块"，系统
                **反推**出仓位比例并同步滑块（所以滑块永远只有一个
                事实来源：比例）。

                输入框用宿主**原生** ``Input``（自己造 input 就要自己
                写样式 = 自己写危险 CSS）。金额那一栏用文本型：数字型
                输入框会把"84."这种中间态吃掉，玩家根本打不出小数。 */}
            <Grid cols={2} gap={8} className="rrm-trade-previews">
              <Stack gap={4} className="rrm-trade-preview-col">
                <Text>
                  {t("panel.trade_buy_preview", {
                    amount: buyAmount,
                    cost: buyCost.toFixed(2),
                    cash: buyCashAfter.toFixed(2),
                  })}
                </Text>
                {h(
                  "div",
                  {
                    className: "rrm-trade-inputs",
                    // 离开输入框 ⇒ 草稿作废，框里显示**实际**成交的那一组数。
                    // （"我要买 20 块"但一股就 11.77 ⇒ 真正花的是 11.77。）
                    //
                    // **用 focusout 不用 blur** —— 宿主 UI Kit 是**自研迷你运行时**
                    // （onX → addEventListener(x)），而 `blur` **不冒泡**：
                    // 挂在容器上的 onBlur 永远等不到子输入框的失焦。
                    // `focusout` 会冒泡，才是这里该用的那个。
                    onFocusout: (event: any) => {
                      const next = event && event.relatedTarget
                      if (next && event.currentTarget && event.currentTarget.contains(next)) {
                        return
                      }
                      setBuySharesDraft(null)
                      setBuyMoneyDraft(null)
                    },
                  },
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "number",
                      min: 0,
                      step: 1,
                      value: buySharesShown,
                      placeholder: t("panel.trade_input_shares"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onBuySharesInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_shares")),
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "text",
                      value: buyMoneyShown,
                      placeholder: t("panel.trade_input_money"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onBuyMoneyInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_money"))
                )}
              </Stack>
              <Stack gap={4} className="rrm-trade-preview-col">
                <Text>
                  {t("panel.trade_sell_preview", {
                    amount: sellAmount,
                    income: sellIncome.toFixed(2),
                    cash: sellCashAfter.toFixed(2),
                  })}
                </Text>
                {h(
                  "div",
                  {
                    className: "rrm-trade-inputs",
                    // 同上：离开输入框 ⇒ 吸附到**实际**回款（focusout 会冒泡）。
                    onFocusout: (event: any) => {
                      const next = event && event.relatedTarget
                      if (next && event.currentTarget && event.currentTarget.contains(next)) {
                        return
                      }
                      setSellSharesDraft(null)
                      setSellMoneyDraft(null)
                    },
                  },
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "number",
                      min: 0,
                      step: 1,
                      value: sellSharesShown,
                      placeholder: t("panel.trade_input_shares"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onSellSharesInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_shares")),
                  h(
                    Input,
                    {
                      className: "rrm-trade-input",
                      type: "text",
                      value: sellMoneyShown,
                      placeholder: t("panel.trade_input_money"),
                      disabled: busy,
                      onChange: (value: any) => {
                        onSellMoneyInput(value)
                      },
                    }
                  ),
                  h("span", { className: "rrm-trade-input-unit" }, t("panel.trade_unit_money"))
                )}
              </Stack>
            </Grid>

            {/* ── P14.0：计划买入台账（多标的）────────────────────────
                玩家可以给**好几只**标的排计划。这一栏把"打算花多少"
                摊开给玩家看：每只排了多少、总额多少。
                别的标的占掉的额度会**截断**买入滑条 ——
                拉扯的阻力感，就是极限的提示。

                P21.0：前两关**不玩**这一套 ⇒ 整块不渲染
                （而且台账占掉的额度也一起归零，见 othersBudget）——
                藏起来的东西不能还在暗中起作用。 */}
            {showPlanLedger ? (
            <Stack gap={6} className="rrm-plan">
              <Inline gap={8} align="center" justify="space-between">
                <Text>{t("panel.plan_title")}</Text>
                <Text>
                  {t("panel.plan_total", {
                    total: planTotal.toFixed(2),
                    cash: cash.toFixed(2),
                  })}
                </Text>
              </Inline>
              {planRows.length > 0
                ? h(
                    "div",
                    {
                      style: {
                        display: "flex",
                        flexDirection: "column",
                        gap: "4px",
                      },
                    },
                    planRows.map((row) =>
                      h(
                        "div",
                        {
                          className: "rrm-plan-row",
                          style: {
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: "8px",
                            fontSize: "12px",
                          },
                        },
                        h("span", { style: { color: "var(--text)" } }, row.name),
                        h(
                          "span",
                          { style: { color: "var(--muted)" } },
                          `${row.budget.toFixed(2)} ${t("panel.trade_unit_money")}`
                        ),
                        h(
                          "button",
                          {
                            type: "button",
                            className: "rrm-plan-drop",
                            style: {
                              border: "1px solid rgba(142, 171, 219, 0.35)",
                              background: "transparent",
                              color: "var(--muted)",
                              borderRadius: "6px",
                              padding: "0 7px",
                              cursor: "pointer",
                              fontSize: "12px",
                              lineHeight: "1.5",
                            },
                            onClick: () => dropPlan(row.id),
                          },
                          "×"
                        )
                      )
                    )
                  )
                : h(Text, null, t("panel.plan_empty"))}
            </Stack>
            ) : null}

            <Divider />

            <Inline gap={8} wrap align="center">
              <ActionButton
                className="rrm-mini"
                action={actionById("analyze")}
                label={t("action.analyze")}
                values={{ stock_id: effectiveStockId }}
                onResult={(result: any) => {
                  if (result) setAnalysis(result as AnalysisResult)
                }}
                onError={onActionError}
              />
              <Text>{t("panel.analyses_left", { count: state.analyses_left || 0 })}</Text>
            </Inline>

            <Inline gap={8} wrap align="center">
              <ActionButton
                className="rrm-mini"
                action={actionById("daily_claim")}
                label={t("daily.claim")}
                onError={onActionError}
              />
              <ActionButton
                className="rrm-mini rrm-anchor-loan-button"
                action={actionById("daily_loan")}
                label={t("daily.loan")}
                tone="danger"
                onError={onActionError}
              />
              <ActionButton
                className="rrm-mini"
                action={actionById("daily_reject")}
                label={t("panel.reject")}
                onError={onActionError}
              />
              {/* P5.5 改动 6：跳过本日（无限模式专用，经典模式入口会报错） */}
              <ActionButton
                className="rrm-mini"
                action={actionById("skip_day")}
                label={t("panel.skip_day")}
                onError={onActionError}
              />
            </Inline>

            {analysis ? (
              <Card title={t("panel.analysis_result")}>
                <Stack gap={8}>
                  <Inline gap={8} wrap align="center">
                    <StatusBadge
                      tone={RATING_TONE[analysis.rating] || "default"}
                      label={t(`analysis.${analysis.rating}`)}
                    />
                    <Text>
                      {analysis.name} · {money(analysis.price)}
                    </Text>
                  </Inline>
                  <KeyValue
                    items={[
                      { key: "chasing", label: t("panel.chasing"), value: analysis.chasing ? t("common.yes") : t("common.no") },
                      { key: "weight", label: t("panel.weight"), value: percent(analysis.position_weight) },
                      { key: "left", label: t("panel.analyses_left", { count: analysis.analyses_left }) },
                    ]}
                  />
                  {analysis.market_note ? (
                    <Alert tone="warning" message={analysis.market_note} />
                  ) : null}
                  <Text>{analysis.note}</Text>
                </Stack>
              </Card>
            ) : null}
          </Stack>
        </Card>

        {/* ── P7.2 ⑦：社区名册（群友的专属标的）──────────────────── */}
        <Card title={t("visual.roster_title")}>
          {rosterList.length === 0 ? (
            <Text>{t("visual.roster_empty")}</Text>
          ) : (
            <Stack gap={8}>
              {rosterList.map((member) => (
                <Inline key={member.stock_id} gap={8} align="center" wrap>
                  <StatusBadge tone="default" label={member.alias || ""} />
                  <Text>{member.title || ""}</Text>
                  <Text>{money(member.price)}</Text>
                  <Text>{member.persona || ""}</Text>
                </Inline>
              ))}
            </Stack>
          )}
        </Card>

        {/* ── P7.2 ⑧：当天新闻（局中只有中性分类，标题正文要点开才给）─ */}
        <Card title={t("panel.news")}>
          <Stack gap={8}>
            {news.length === 0 ? (
              <Text>{t("panel.news_empty")}</Text>
            ) : (
              <List<NewsItem>
                items={news}
                render={(item) => (
                  <Inline gap={8} align="center" wrap>
                    <StatusBadge tone="default" label={item.kind_label} />
                    <Text>{t("panel.news_hint")}</Text>
                    <ActionButton
                      action={actionById("news_detail")}
                      label={t("panel.news_detail")}
                      values={{ news_id: item.id }}
                      onResult={(result: any) => {
                        if (result) setNewsDetail(result as Record<string, any>)
                      }}
                    />
                  </Inline>
                )}
              />
            )}
            {newsDetail ? (
              <Card title={t("panel.news_detail")}>
                <Stack gap={8}>
                  <Inline gap={8} align="center">
                    <StatusBadge tone="default" label={newsDetail.kind_label} />
                    <Text>{newsDetail.headline}</Text>
                  </Inline>
                  {newsDetail.roster_alias ? (
                    <Inline gap={8} align="center" wrap>
                      <StatusBadge tone="default" label={newsDetail.roster_alias} />
                      <Text>{newsDetail.roster_title || ""}</Text>
                    </Inline>
                  ) : null}
                  <Text>{newsDetail.content}</Text>
                  <Text>{t("news.truth_note")}</Text>
                </Stack>
              </Card>
            ) : null}
          </Stack>
        </Card>

        {/* ── ⑨ 猫娘公开状态 ─────────────────────────────────────── */}
        {catgirl ? (
          <Card title={t("panel.catgirl")}>
            <KeyValue
              items={[
                {
                  key: "alive",
                  label: t("catgirl.alive"),
                  value: catgirl.alive ? t("catgirl.alive") : t("catgirl.dead"),
                },
                { key: "cash", label: t("catgirl.cash"), value: money(catgirl.cash) },
                {
                  key: "holdings",
                  label: t("catgirl.holdings"),
                  value: t("catgirl.holdings_value", {
                    value: money(catgirl.positions_value),
                    count: catgirl.position_count,
                  }),
                },
                {
                  key: "days",
                  label: t("catgirl.survived_days"),
                  value: catgirl.days_survived,
                },
                { key: "action", label: t("panel.today_action"), value: catgirl.today_action },
              ]}
            />
          </Card>
        ) : null}

        {/* ── ⑩ 二道贩子弹窗（方案 A：引擎零改动，面板弹窗征求同意）─ */}
        {showPawnbroker && pending ? (
          <Card title={t("panel.pawnbroker")}>
            <Stack gap={8}>
              <Inline gap={8} align="center" wrap>
                <StatusBadge tone="danger" label={t("visual.force_liquid")} />
                <Text>
                  {t("panel.pawnbroker_hint", {
                    amount: pending.amount || 0,
                    reason: pending.reason || "",
                  })}
                </Text>
              </Inline>
              <Inline gap={8}>
                <ActionButton
                  action={actionById("daily_reject")}
                  label={t("panel.pawnbroker_reject")}
                  tone="danger"
                  onResult={() => setDismissedProposal(proposalKey)}
                />
                {/* 「接受」不需要调任何入口 —— 不撤销就是默认接受，
                    强制卖出照常执行（P5 契约 §3.4 方案 A：引擎零改动）。 */}
                <Button
                  onClick={() => {
                    setDismissedProposal(proposalKey)
                  }}
                >
                  {t("panel.pawnbroker_accept")}
                </Button>
              </Inline>
            </Stack>
          </Card>
        ) : null}

        {/* ── P7.2 ⑪：猫娘实时弹幕框（**永远在面板最后**）────────── */}
        {danmakuCard}
        {loanModal}
      </Stack>
    </Page>
  )
}
