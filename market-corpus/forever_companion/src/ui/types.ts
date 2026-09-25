// 面板共享类型：宿主 context 快照、设置表单、以及各模块间传递的回调签名

// 宿主桥的 t() 第二参数同时承担 defaultValue 与 {{n}}/{n} 插值参数（见 ui-kit 运行时
// interpolateI18n），索引签名放行任意插值键；1.2.7 介绍卡 toolsCount 用到 {n}
export type TFunc = (key: string, opts?: { defaultValue?: string; [param: string]: any }) => string

export type Status = {
  enabled?: boolean
  phase?: string
  phase_label?: string
  tone?: string
  cycle_day?: number
  day_ratio?: number
  days_until_next_period?: number
  error?: string
}

export type Mood = {
  active?: boolean
  system_enabled?: boolean
  action?: string
  action_label?: string
  reason?: string
  expires_at?: number | null
  affect?: { valence: number; arousal: number; arousal_baseline?: number }
}

export type Settings = {
  enabled?: boolean
  auto_derive?: boolean
  cycle_length?: number
  period_length?: number
  ovulation_day?: number
  ovulation_window?: number
  inject_mode?: string
  inject_interval_n?: number
  phase_openers?: boolean
  timezone?: string
  mood_enabled?: boolean
  default_action_minutes?: number
  emotion_sense_enabled?: boolean
  tone_check_rate?: number
  tone_phase_sensitivity_enabled?: boolean
  tone_phase_sensitivity?: number
  tone_slot?: string
  fragments_enabled?: boolean
  fragments_slot?: string
  review_enabled?: boolean
  review_slot?: string
  review_turns_threshold?: number
  review_days_threshold?: number
  debug_mode?: boolean
}

// 时光日记时间线条目：source=self 为她手写的（mood/entry），source=auto 为自动碎片
//（kind/quote/note）；旧数据（0.6.x）缺 source 按 self 处理
export type DiaryItem = {
  ts?: string
  source?: string
  kind?: string
  mood?: string
  entry?: string
  quote?: string
  note?: string
}

// 个人日记页眉统计（journal_index 用）；entries 仅 journal_current / get_journal 返回
export type JournalPageHeader = {
  page_no?: number
  started_at?: string
  last_ts?: string
  entry_count?: number
  mood_avg?: number | null
  legacy?: boolean
}

// 藏书阁概览（1.3.0，journal_archive_brief 用）：合订本极轻量摘要——
// 只显存储里现成的事实（本数 + 时段），不派生会随淘汰平移的序号
export type JournalArchiveBrief = {
  pages?: number
  first_ts?: string
  last_ts?: string
}

// 个人日记页内的单段续写记录
export type JournalEntry = { ts?: string; text?: string; affect?: number }

export type JournalPage = JournalPageHeader & { entries?: JournalEntry[] }

export type CalCell = {
  day?: number
  in_month?: boolean
  phase?: string
  phase_label?: string
  is_tide?: boolean
  cycle_day?: number
  is_today?: boolean
  is_future?: boolean
}

export type CalMonth = { year?: number; month?: number; label?: string; cells?: CalCell[] }

export type Calendar = { months?: CalMonth[]; error?: string }

export type LanlanItem = {
  name?: string
  enabled?: boolean
  phase?: string
  mood_active?: boolean
  mood_action?: string
  orphan?: boolean
}

// 语气分析模型槽位下拉项：value 为槽位 id（"" = 宿主默认情感模型槽），model 为该槽当前模型名（空串 = 未知）
export type ToneSlotOption = { value?: string; model?: string }

// 面板外观（1.2.0）：图库索引条目——不含原图本体（走 get_gallery_image 按需拉取）；
// thumb 为面板生成并回填的小缩略图 data URL（旧图迁移后短暂为空，随后补齐）
export type GalleryItem = {
  id?: string
  name?: string
  mime?: string
  size?: number
  added_at?: string
  thumb?: string
}

// 面板外观参数（与后端 panel_appearance 记录同构）：draft=编辑中实时预览，
// saved=已保存生效；除 bg_id 外的数值/枚举字段都有明确取值域（见 utils.normAppearance）
export type Appearance = {
  bg_id: string
  fill: string
  position: string
  blur: number
  dim: number
  brightness: number
  saturate: number
  contrast: number
  glass: number
  card_alpha: number
  text_weight: number
}

// 我的日记素材快照（1.3.0）：成文时随正文固化进篇目的「本卷依据」——
// tone 按计数降序、mood_avg 可空（无采样）、quotes 至多 6 条单条≤40 字
export type ReviewQuote = { kind?: string; quote?: string }

// 我的日记：已成文的一篇评价（get_review 返回，时间倒序）
export type ReviewEntry = {
  ts?: string
  turns?: number
  span?: string
  self_action_count?: number
  // 1.3.0 素材快照：旧篇目缺字段容忍（卷宗页对应栏自动隐藏）
  tone?: Record<string, number>
  mood_avg?: number | null
  quotes?: ReviewQuote[]
  demo?: boolean
  text?: string
}

// 我的日记档案室概览（1.3.0，review_archive_brief 用）：与藏书阁 brief
// 同款纪律——只显存储里现成的事实（卷数 + 时段），不派生会平移的序号
export type ReviewArchiveBrief = {
  entries?: number
  first_ts?: string
  last_ts?: string
}

// 我的日记素材进度（get_review 返回 / dashboard review_brief 轻量版）：
// 双门槛都可见（1.3.0）——turns 与 days 任先到先写，due/due_reason 供面板提示
export type ReviewProgress = {
  turns?: number
  turns_threshold?: number
  days?: number
  days_threshold?: number
  span?: string
  due?: boolean
  due_reason?: string
}

export type ReviewBrief = {
  enabled?: boolean
  entries?: number
  progress_turns?: number
  turns_threshold?: number
  // 「立即写一篇」排队成文（1.2.3）：writing=排队中/在飞；last_result 是队列
  // 成文的成败结论（内存即弃、只留最近一次），面板按 ts 去重弹一次完成 toast
  writing?: boolean
  last_result?: { ts?: number; written?: boolean; reason?: string } | null
}

// 模型通道状态灯（dashboard channel_status）：dormant_reason ∈
// ""(ok) / free_route / no_model / disabled
export type ChannelStatusItem = { enabled?: boolean; dormant_reason?: string }
export type ChannelStatus = { tone?: ChannelStatusItem; fragments?: ChannelStatusItem; review?: ChannelStatusItem }

// ---- 相处统计（1.1.0）----
// 数字摘要（dashboard stats_summary.summary，纯本地即时计算）
export type StatsSummary = {
  days_together?: number
  next_anniversary_in?: number
  total_turns?: number
  active_days?: number
  cold_wars?: number
  made_ups?: number
  warm_moments?: number
  longest_streak?: number
  current_streak?: number
}

// 徽章（dashboard stats_summary.badges）：相伴天数类带 days，事件类（first_*）只看 unlocked
export type StatsBadge = {
  id?: string
  days?: number
  unlocked?: boolean
  date?: string
}

// 热力图单日（get_stats heatmap.days）：turns=0 也是有记录的天（面板按档染色）
export type HeatDay = {
  date?: string
  turns?: number
  tone?: string
  valence?: number | null
}

// 热力图（get_stats heatmap）：GitHub 式日历年视图。start/end = 本年视图网格边界
// （起点已被首条互动日截断，start > end 表示还没有可展示的日子）；
// years = 可选择的年份（当年降序回退到最早明细年）；year = 当前视图年份
export type Heatmap = {
  months?: string[]
  days?: HeatDay[]
  years?: number[]
  year?: number
  start?: string
  end?: string
}

// 月报（get_stats month）：voice = 本月声音（她当月写过的最长一条手记摘录）
export type MonthVoice = { ts?: string; mood?: string; entry?: string }

export type MonthReport = {
  month?: string
  turns?: number
  active_days?: number
  busiest_day?: string
  busiest_turns?: number
  longest_streak?: number
  cold_wars?: number
  made_ups?: number
  warm_moments?: number
  tone?: Record<string, number>
  valence_avg?: number | null
  voice?: MonthVoice | null
  sealed?: boolean
}

export type StatsState = {
  summary?: StatsSummary
  badges?: StatsBadge[]
}

export type State = {
  status?: Status
  anchor_date?: string
  advance_days?: number
  mood?: Mood
  settings?: Settings
  calendar?: Calendar
  diary_recent?: DiaryItem[]
  diary_total?: number
  fragment_total?: number
  journal_index?: JournalPageHeader[]
  // 藏书阁（1.3.0）：合订本概览；全量翻阅走 get_journal_archive 按需拉取
  journal_archive_brief?: JournalArchiveBrief
  // 我的日记档案室（1.3.0）：旧卷宗合档概览，面板据此决定画不画那只档案盒
  review_archive_brief?: ReviewArchiveBrief
  // 个人日记邀请挂起态（1.2.3）：递过邀请、她还没落笔
  journal_invite_pending?: boolean
  lanlan?: string
  lanlan_list?: LanlanItem[]
  tone_slot_options?: ToneSlotOption[]
  review_brief?: ReviewBrief
  channel_status?: ChannelStatus
  week_activity?: number
  stats_summary?: StatsState
  // 新手引导 + 就绪清单（1.2.6）
  onboarding?: Onboarding
  // 能力中心总览（1.2.7）：功能页数据源随 5s 轮询下发（纯内存小载荷）
  capabilities?: CapabilitiesPayload
  // 生日轻语（1.3.1）：dashboard 的生日设置卡视图（纯本地，零 IO）
  birthday?: BirthdayView
}

// ---- 生日轻语（1.3.1）----
// set = 填了合法日期；日期是全局配置（各角色同值）。
// 1.3.1 改版：只喂「时光」页生日设置卡，倒数/当天视图字段随总览生日卡一同退役
export type BirthdayView = {
  date?: string
  set?: boolean
  keep_diary?: boolean
}

// ---- 能力中心（1.2.7）----
// source：on / user_off（功能页否决）/ config_off（设置里关）/
// upstream_off（被依赖挡住，blocked_by 给上游 id）/ master_off（总开关）
export type CapItem = {
  id: string
  group: string
  llm: string
  tools?: string[]
  enabled: boolean
  source: string
  blocked_by?: string[]
  user_off?: boolean
}

export type CapabilitiesPayload = {
  lanlan?: string
  master_enabled?: boolean
  hide_disabled_tools?: boolean
  capabilities?: CapItem[]
}

// ---- 新手引导与配置引导（1.2.6）----
// 就绪清单项：level = must（必办）/ suggest（建议）；tab = 直达页签（"" = 无动作项）
export type GuideItem = { id?: string; ok?: boolean; level?: string; tab?: string }
export type Readiness = { items?: GuideItem[]; must_ok?: boolean; all_ok?: boolean; pending?: number }
export type Onboarding = {
  wizard_pending?: boolean
  guide?: { wizard?: string; at?: string; version?: string }
  readiness?: Readiness
}

export type FormValues = {
  auto_derive: boolean
  cycle_length: number
  period_length: number
  ovulation_day: number
  ovulation_window: number
  inject_mode: string
  inject_interval_n: number
  phase_openers: boolean
  timezone: string
  mood_enabled: boolean
  default_action_minutes: number
  emotion_sense_enabled: boolean
  tone_check_rate: number
  tone_phase_sensitivity_enabled: boolean
  tone_phase_sensitivity: number
  tone_slot: string
  fragments_enabled: boolean
  fragments_slot: string
  review_enabled: boolean
  review_slot: string
  review_turns_threshold: number
  review_days_threshold: number
  anniversary_inject: boolean
  debug_mode: boolean
}
