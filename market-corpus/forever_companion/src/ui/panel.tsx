// 永远的陪伴面板入口：context 轮询、动作处理、Tab 导航与各页组装
// 顶级页签：日历 / 周期 / 注入 / 情绪（情绪系统+语气感知）/ 手记 / 管理（危险区+角色名单）
import {
  Alert,
  Page,
} from "@neko/plugin-ui"
import { useConfirm, useEffect, useLocalState, useRef, useState, useToast } from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"
import type { Appearance, FormValues, GalleryItem, Settings, State } from "./types"
import {
  DATE_RE,
  appearanceVars,
  bgLayerStyle,
  compressImageDataUrl,
  normAppearance,
  settingsToForm,
} from "./utils"
import { PANEL_STYLES } from "./styles"
import { StatusBar } from "./statusbar"
import { OverviewPane } from "./overview"
import { CalendarPane } from "./calendar"
import { CycleSettingsCard } from "./settings_cycle"
import { InjectSettingsCard } from "./settings_inject"
import { MoodSettingsCard } from "./settings_mood"
import { EmotionSenseSettingsCard } from "./settings_emotion"
import { ChannelSettingsCard } from "./settings_tone"
import { DiarySettingsCard } from "./settings_diary"
import { SaveBar } from "./savebar"
import { ManagePane } from "./manage"
import { FeaturesPane } from "./features"
import { OnboardingWizard } from "./onboarding"
import { DiaryPane } from "./diary"
import { MomentPane } from "./moment"
import { AppearanceCard } from "./appearance"
import type { DiaryItem, JournalPage, Heatmap, MonthReport, ReviewEntry, ReviewProgress } from "./types"
import { unwrapCallResult, errorText } from "./utils"

export default function Panel(props: PluginSurfaceProps<State>) {
  const { actions, state, t } = props
  const status = state.status || {}
  const mood = state.mood || {}
  const settings = state.settings || {}
  const toast = useToast()
  const confirmDialog = useConfirm()
  // 默认页签 = 总览；useLocalState 的旧持久化值（0.9.0 前可能是 calendar 等）
  // 会被 VALID_TABS 校验兜回 overview，但初值本身也要指向 overview
  const [tab, setTab] = useLocalState<string>("tide.tab", "overview")
  // 内容区（右侧主页面）滚动条静默化（1.2.9）：默认完全透明看不见，仅两种
  // 时刻浮现——① 鼠标落入右缘滚动条位（:hover 命不中槽区，onPointerMove
  // 判距 ≤16px 切 hot）；② 正在滚动（onScroll 续命，停 900ms 后归静）。
  // 拖动滑块时 pointermove 不进元素、hot 会掉，但拖动即滚动，onScroll 接棒。
  const [contentHot, setContentHot] = useState(false)
  const [contentScrolling, setContentScrolling] = useState(false)
  const scrollIdleRef = useRef<any>(null)
  const markContentScrolling = () => {
    setContentScrolling(true)
    if (scrollIdleRef.current) clearTimeout(scrollIdleRef.current)
    scrollIdleRef.current = setTimeout(() => setContentScrolling(false), 900)
  }
  useEffect(
    () => () => {
      if (scrollIdleRef.current) clearTimeout(scrollIdleRef.current)
    },
    []
  )
  // 首次向导（1.2.6）：本会话关闭闸门——服务端 wizard_pending 要等下一次 5s
  // 轮询才翻转，关闭后到翻转前的窗口全靠这个本地闸不重现
  const [wizardClosed, setWizardClosed] = useState(false)
  const [form, setForm] = useState<FormValues>(settingsToForm({}))
  // 面板外观（1.2.0）：图库索引/外观参数（saved=生效、draft=实时预览）按需拉取；
  // 图片本体逐条缓存（imgCache），绝不进 5s 轮询载荷
  const [galleryItems, setGalleryItems] = useState<GalleryItem[]>([])
  const [savedAp, setSavedAp] = useState<Appearance>(normAppearance(null))
  const [draftAp, setDraftAp] = useState<Appearance>(normAppearance(null))
  const [imgCache, setImgCache] = useState<Record<string, string>>({})
  const imgInflight = useRef<Record<string, boolean>>({})
  const thumbTried = useRef<Record<string, boolean>>({})
  const [apSaving, setApSaving] = useState(false)
  const [apBusy, setApBusy] = useState(false)
  // 时光页：相处统计的热力图/月报数据量大，进页时按需拉取（不随 5s 轮询）；
  // 切角色时清掉旧数据等下次进页重拉
  const [heatData, setHeatData] = useState<Heatmap | null>(null)
  const [monthData, setMonthData] = useState<MonthReport | null>(null)
  const [monthList, setMonthList] = useState<string[]>([])
  const [statsLoading, setStatsLoading] = useState(false)
  // 功能管理页（1.2.7）：能力清单随 dashboard 5s 轮询下发（state.capabilities），
  // 与总开关/其它页设置同帧一致；本页只留"操作在飞"禁用态防连点
  const [capsBusyId, setCapsBusyId] = useState("")

  function updateForm(patch: Partial<FormValues>) {
    setForm((prev) => ({ ...prev, ...patch }))
  }

  useEffect(() => {
    setForm(settingsToForm(settings))
  }, [
    settings.auto_derive,
    settings.cycle_length,
    settings.period_length,
    settings.ovulation_day,
    settings.ovulation_window,
    settings.inject_mode,
    settings.inject_interval_n,
    settings.phase_openers,
    settings.timezone,
    settings.mood_enabled,
    settings.default_action_minutes,
    settings.emotion_sense_enabled,
    settings.tone_check_rate,
    settings.tone_phase_sensitivity_enabled,
    settings.tone_phase_sensitivity,
    settings.tone_slot,
    settings.fragments_enabled,
    settings.fragments_slot,
    settings.review_enabled,
    settings.review_slot,
    settings.review_turns_threshold,
    settings.review_days_threshold,
    (settings as Record<string, any>).anniversary_inject,
  ])

  // 面板打开期间周期性同步 context：宿主切换角色后自动跟上，情绪状态/阶段等外部变化
  // （入口点触发、tick 到期、对话中的 LLM 工具调用）无需重进面板即可看到
  useEffect(() => {
    const timer = setInterval(() => {
      try {
        props.api.refresh()
      } catch {
        // 轮询失败静默忽略，下个周期重试
      }
    }, 5000)
    return () => clearInterval(timer)
  }, [])

  // 外观（1.2.0）：面板打开时拉一次图库索引 + 已存参数（图片本体不进轮询）；
  // 旧版单图背景在后端入口内自动迁移进图库。拉取失败按默认外观处理，不弹错
  useEffect(() => {
    let alive = true
    Promise.resolve(props.api.call("get_panel_gallery", {}))
      .then((payload) => {
        if (!alive) return
        const r = (unwrapCallResult(payload) || {}) as Record<string, any>
        const items = Array.isArray(r.items) ? (r.items as GalleryItem[]) : []
        const ap = normAppearance(r.appearance)
        setGalleryItems(items)
        setSavedAp(ap)
        setDraftAp(ap)
        if (ap.bg_id) {
          loadImage(ap.bg_id)
        }
      })
      .catch(() => {
        console.warn("[forever_companion] load panel gallery failed")
      })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 图片本体按需拉取（带在途去重）：选中某张壁纸时若没缓存就拉
  function loadImage(id: string) {
    if (!id) return
    if (imgCache[id] || imgInflight.current[id]) return
    imgInflight.current[id] = true
    Promise.resolve(props.api.call("get_gallery_image", { item_id: id }))
      .then((payload) => {
        const r = (unwrapCallResult(payload) || {}) as Record<string, any>
        if (r && typeof r.data_url === "string" && r.data_url) {
          setImgCache((prev) => ({ ...prev, [id]: r.data_url }))
        }
      })
      .catch(() => { console.warn("[forever_companion] load gallery image failed") })
      .finally(() => { imgInflight.current[id] = false })
  }

  // draft 指向的图若未缓存则补拉（切换/新加入库时）
  useEffect(() => {
    loadImage(draftAp.bg_id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftAp.bg_id])

  // 缺缩略图的在用图（如旧版迁移来的 legacy）：拿到本体后本地画一张回填
  useEffect(() => {
    const current = savedAp.bg_id
    const item = galleryItems.find((it) => (it.id || "") === current)
    if (!current || !item || item.thumb || thumbTried.current[current]) return
    const dataUrl = imgCache[current]
    if (!dataUrl) return
    thumbTried.current[current] = true
    compressImageDataUrl(dataUrl, item.mime || "image/png", "raw")
      .then((res) => {
        if (res.thumb) {
          props.api.call("gallery_set_thumb", { item_id: current, thumb: res.thumb })
            .then(() => { setGalleryItems((prev) => prev.map((it) => ((it.id || "") === current ? { ...it, thumb: res.thumb } : it))) })
            .catch(() => {})
        }
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [galleryItems, imgCache, savedAp.bg_id])

  function onDraftChange(patch: Record<string, string | number>) {
    setDraftAp((prev) => normAppearance(Object.assign({}, prev, patch)))
  }

  // 外观保存：整包 draft 参数发后端（悬空 bg_id 会被后端静默解除），回填归一结果
  async function saveAppearance() {
    const next = draftAp
    setApSaving(true)
    try {
      const payload = unwrapCallResult(await props.api.call("set_panel_appearance", {
        bg_id: next.bg_id, fill: next.fill, position: next.position,
        blur: next.blur, dim: next.dim, brightness: next.brightness,
        saturate: next.saturate, contrast: next.contrast, glass: next.glass,
        card_alpha: next.card_alpha, text_weight: next.text_weight,
      }))
      const r = (payload || {}) as Record<string, any>
      const ap = normAppearance(r.appearance || next)
      setSavedAp(ap)
      setDraftAp(ap)
      toast.success(t("panel.appearance.applied", { defaultValue: "背景已更新" }))
    } catch (err) {
      toast.error(errorText(err, t))
    } finally {
      setApSaving(false)
    }
  }

  // 导入：面板侧已压好并生成缩略图；入册即时生效但不自动选为壁纸（选图走 draft+保存）
  async function addImage(dataUrl: string, thumb: string, name: string) {
    setApBusy(true)
    try {
      const payload = unwrapCallResult(await props.api.call("gallery_add", { data_url: dataUrl, thumb, name }))
      const r = (payload || {}) as Record<string, any>
      const items = Array.isArray(r.items) ? (r.items as GalleryItem[]) : []
      setGalleryItems(items)
      const newId = String(r.id || "")
      if (newId) {
        setImgCache((prev) => ({ ...prev, [newId]: dataUrl }))
      }
      toast.success(t("panel.appearance.added", { defaultValue: "已加入图库" }))
    } catch (err) {
      toast.error(errorText(err, t))
    } finally {
      setApBusy(false)
    }
  }

  // 删除图库图（danger 确认）：后端会顺带解除在用的 bg_id，回填 appearance
  async function removeImage(id: string) {
    if (!id) return
    const ok = await confirmDialog({
      title: t("actions.gallery_remove.label", { defaultValue: "从图库删除图片" }),
      message: t("actions.gallery_remove.confirm", { defaultValue: "将从图库删除这张图片（若正在使用会一并停用背景），不可恢复，确认？" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok) return
    setApBusy(true)
    try {
      const payload = unwrapCallResult(await props.api.call("gallery_remove", { item_id: id }))
      const r = (payload || {}) as Record<string, any>
      const items = Array.isArray(r.items) ? (r.items as GalleryItem[]) : []
      setGalleryItems(items)
      const ap = normAppearance(r.appearance)
      setSavedAp(ap)
      setDraftAp(ap)
      const cache = { ...imgCache }
      delete cache[id]
      setImgCache(cache)
      toast.success(t("panel.diary.deleted", { defaultValue: "已删除" }))
    } catch (err) {
      toast.error(errorText(err, t))
    } finally {
      setApBusy(false)
    }
  }

  const updateSettingsAction = actions.find((a) => a.id === "update_settings") as HostedAction | undefined
  const setAnchor = actions.find((a) => a.id === "set_anchor") as HostedAction | undefined
  const advance = actions.find((a) => a.id === "advance_days") as HostedAction | undefined
  const toggle = actions.find((a) => a.id === "toggle") as HostedAction | undefined
  const resetAll = actions.find((a) => a.id === "reset_all") as HostedAction | undefined
  const pruneLanlan = actions.find((a) => a.id === "prune_lanlan") as HostedAction | undefined

  // kit 的 useConfirm 默认按钮文案是英文 Confirm/Cancel，必须显式传本地化文案
  const confirmLabels = {
    confirmLabel: t("panel.confirm", { defaultValue: "确认" }),
    cancelLabel: t("panel.cancel", { defaultValue: "取消" }),
  }

  async function saveSettings() {
    if (!updateSettingsAction) {
      toast.error(t("panel.errors.actionUnavailable", { defaultValue: "操作不可用（插件可能未运行）" }))
      return
    }
    try {
      if (!DATE_RE.test(String(state.anchor_date || ""))) {
        throw new Error(t("panel.errors.setAnchorFirst", { defaultValue: "请先设置潮汐首日锚点，再开启模拟" }))
      }
      const result = await props.api.call("update_settings", { ...form })
      // 用入口返回的最新快照立即回填表单，不依赖 context 重取
      const snap = (result && typeof result === "object" && (result as Record<string, any>).enabled !== undefined)
        ? (result as Partial<Settings>)
        : null
      if (snap) {
        setForm(settingsToForm({ ...settings, ...snap }))
      }
      toast.success(t("panel.messages.saved", { defaultValue: "设置已保存" }))
      try {
        await props.api.refresh()
      } catch {
        // context 刷新失败不影响"已保存"结论，表单已本地回填
      }
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  // 总开关切换：返回值供 TmSwitch 乐观回滚（false = 取消/失败，开关动画回落）
  async function onToggle() {
    if (!toggle) {
      toast.error(t("panel.errors.actionUnavailable", { defaultValue: "操作不可用（插件可能未运行）" }))
      return false
    }
    const turningOff = status.enabled !== false
    const ok = await confirmDialog({
      title: turningOff ? t("panel.turnOff", { defaultValue: "关闭模拟" }) : t("panel.turnOn", { defaultValue: "开启模拟" }),
      message: t("actions.toggle.confirm", { defaultValue: "切换身体节律模拟的总开关，确认？" }),
      tone: turningOff ? "warning" : "primary",
      ...confirmLabels,
    })
    if (!ok) return false
    try {
      await props.api.call("toggle")
      await props.api.refresh()
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    }
  }

  // 向导收尾（1.2.6）：done/skip 都先过本地闸再写盘——写失败不重新拦向导
  // （服务端内存位已改，本会话不再弹）；错误如实弹，不谎报"引导已保存"
  async function closeWizard(action: "done" | "skip") {
    setWizardClosed(true)
    try {
      await props.api.call("set_onboarding", { action })
      await props.api.refresh()
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  // 设置页"再看一次新手引导"：reopen 只清引导记录，不动任何配置
  async function reopenGuide() {
    try {
      await props.api.call("set_onboarding", { action: "reopen" })
      setWizardClosed(false)
      await props.api.refresh()
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  async function onClearDiary() {
    const ok = await confirmDialog({
      title: t("actions.clear_diary.label", { defaultValue: "清空时光日记" }),
      message: t("actions.clear_diary.confirm", { defaultValue: "将删除全部时光日记（她的手记与自动碎片），不可恢复，确认？" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok) return
    try {
      await props.api.call("clear_diary")
      await props.api.refresh()
      toast.success(t("panel.diary.cleared", { defaultValue: "时光日记已清空" }))
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  async function onDeleteFragment(ts: string) {
    const ok = await confirmDialog({
      title: t("actions.delete_diary_item.label", { defaultValue: "删除这条碎片" }),
      message: t("actions.delete_diary_item.confirm", { defaultValue: "将删除这条自动记录的碎片，不可恢复，确认？" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok) return
    try {
      await props.api.call("delete_diary_item", { ts })
      await props.api.refresh()
      toast.success(t("panel.diary.deleted", { defaultValue: "已删除" }))
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  async function onLoadJournal() {
    try {
      const payload = unwrapCallResult(await props.api.call("get_journal", {}))
      const pages = (payload as Record<string, any>)?.pages
      return Array.isArray(pages) ? (pages as JournalPage[]) : []
    } catch (err) {
      // 宿主不可达/动作被拒：按空日记本处理，面板不报错；console 留痕便于排查
      console.warn("[forever_companion] load journal failed:", err)
      return []
    }
  }

  // 藏书阁（1.3.0）：合订本全量按需拉取（淘汰低频，点开那一次拉齐即可）。
  // 走 get_journal(scope=archive) 而非新入口 get_journal_archive：宿主运行中
  // 覆盖导入不重扫静态入口白名单，新入口要整启宿主才可达（实机 403 踩坑）；
  // 两通道同数据，新入口保留给 API/跨插件与重启后的规范调用
  async function onLoadJournalArchive() {
    try {
      const payload = unwrapCallResult(await props.api.call("get_journal", { scope: "archive" }))
      const pages = (payload as Record<string, any>)?.pages
      return Array.isArray(pages) ? (pages as JournalPage[]) : []
    } catch (err) {
      console.warn("[forever_companion] load journal archive failed:", err)
      return null
    }
  }

  async function onLoadMoreDiary(offset: number) {
    try {
      const payload = unwrapCallResult(await props.api.call("get_diary", { limit: 50, offset }))
      const r = (payload || {}) as Record<string, any>
      return {
        items: Array.isArray(r.items) ? (r.items as DiaryItem[]) : [],
        hasMore: !!r.has_more,
      }
    } catch (err) {
      console.warn("[forever_companion] load more diary failed:", err)
      return { items: [] as DiaryItem[], hasMore: false }
    }
  }

  async function onInviteJournal() {
    try {
      const payload = unwrapCallResult(await props.api.call("invite_journal", {}))
      await props.api.refresh()
      const r = (payload || {}) as Record<string, any>
      // 四态文案全在前端按 invited/mode 码翻译（i18n 契约 1.3.0 第九轮）：
      // 后端不再回人类可读 note，中文裸串不会短路 t()。传输拒收是真错误，
      // 不能与"开关未开启"共用一条 info 软提示（1.2.4 语义不变）；
      // respond（当面递到）/ read（冷却内悄悄补递）分文案（1.2.3 既定双态）
      if (r.invited === false && r.mode === "failed") {
        toast.error(t("panel.journal.inviteFailed", { defaultValue: "邀请没能送到她手上（消息通道正忙或不可用），再按一次试试" }))
      } else if (r.invited === false) {
        toast.info(t("panel.journal.inviteOff", { defaultValue: "个人日记开关未开启，邀请未发送" }))
      } else if (r.mode === "respond") {
        toast.success(t("panel.journal.invitedRespond", { defaultValue: "邀请已当面递到她手上，她这会儿正想着呢——写不写由她自己决定" }))
      } else {
        toast.success(t("panel.journal.invitedQuiet", { defaultValue: "她刚收到过邀请，这次改成悄悄提醒——给她留点考虑的空间" }))
      }
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  async function onLoadReview() {
    try {
      const payload = unwrapCallResult(await props.api.call("get_review", {}))
      const r = (payload || {}) as Record<string, any>
      return {
        entries: Array.isArray(r.entries) ? (r.entries as ReviewEntry[]) : [],
        progress: (r.progress || {}) as ReviewProgress,
      }
    } catch (err) {
      console.warn("[forever_companion] load review failed:", err)
      return { entries: [] as ReviewEntry[], progress: {} as ReviewProgress }
    }
  }

  // 档案室（1.3.0）：旧卷宗全量按需拉取（淘汰低频，点开那一次拉齐即可）。
  // 走 get_review(scope=archive) 而非新入口 get_review_archive：宿主运行中
  // 覆盖导入不重扫静态入口白名单，新入口要整启宿主才可达（藏书阁同款防御）；
  // 失败返回 null 由面板就地弹错，不再"点了没反应"
  async function onLoadReviewArchive() {
    try {
      const payload = unwrapCallResult(await props.api.call("get_review", { scope: "archive" }))
      const r = (payload || {}) as Record<string, any>
      return Array.isArray(r.entries) ? (r.entries as ReviewEntry[]) : []
    } catch (err) {
      console.warn("[forever_companion] load review archive failed:", err)
      return null
    }
  }

  async function onWriteReviewNow() {
    try {
      // 受理式入口（1.2.3）：后端秒回"已开始写"，真正的成文在后台一拍内起跑；
      // 成败结论经 dashboard review_brief.last_result 由下方 effect 弹完成 toast。
      // i18n 契约（1.3.0 第九轮）：后端只回 reason 码与数据字段，文案全在本地按码分支
      const payload = unwrapCallResult(await props.api.call("write_review_now", {}))
      await props.api.refresh()
      const r = (payload || {}) as Record<string, any>
      if (r.accepted) {
        toast.info(t("panel.review.accepted", { defaultValue: "已开始写这一篇，写完会自动出现在这里，不用守着" }))
      } else if (r.reason === "already_writing") {
        toast.info(t("panel.review.alreadyWriting", { defaultValue: "上一篇还在写，写完会自动出现在这里，稍等一下" }))
      } else if (r.reason === "not_enough_material") {
        // 数字走插值不进文案（后端只回 turns/min_turns 字段）
        toast.info(t("panel.review.notEnoughMaterial", {
          defaultValue: "素材还不够（目前 {turns} 轮，至少 {min_turns} 轮才值得写一篇），再聊聊吧",
          turns: String(r.turns ?? 0),
          min_turns: String(r.min_turns ?? 10),
        }))
      } else if (r.reason === "slot_unresolved") {
        // 休眠原因复用新手向导通道卡的既有文案（零新增 key，1.md 既定）
        toast.info(r.dormant_reason === "free_route"
          ? t("onboarding.channels.freeRoute", { defaultValue: "休眠 · 宿主免费端点限制" })
          : t("onboarding.channels.noModel", { defaultValue: "休眠 · 槽位没配模型" }))
      } else {
        toast.info(t("panel.review.writeFailed", { defaultValue: "这一篇还没写成，稍后再试" }))
      }
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  // 队列成文的完成反馈（1.2.3）：last_result 的 ts 变化 = 刚有一篇写完/写失败。
  // 角色切换按"挂载"处理（1.2.4）：last_result 是按角色存的内存位。
  // 1.3.0 第十一轮修序：旧版把 `!result return` 挡在认领之前——挂载时没有旧结论，
  // 认领永远不发生；真正落地的**第一条**成败结论反而被当成"挂载期旧值"吞掉
  // （面板首次"立即写一篇"的失败反馈必消失，用户端只见按钮弹回、列表无变化）。
  // 现在认领看"角色首帧"而不看"首条结果"：无结果帧也完成认领（seen 记 0），
  // 随后任何 ts>0 的结果都是新事——弹。`seen===0` 旧吞币分支同步删除
  // （ts 来自 time.time()，恒 >0，0 只会是"还没认领到真结果"的哨兵）。
  // 复位必须写在本 effect 内：面板的 [state.lanlan] effect 声明在之后，
  // 同一轮 render 里它跑到时本 effect 已经把 toast 弹出去了。
  // 面板关着错过的失败另有常驻兜底：diary.tsx 的 lastFailed 内联警示行
  const reviewResultSeen = useRef<number>(0)
  const reviewResultRole = useRef<string | null>(null)
  useEffect(() => {
    const result = state.review_brief && state.review_brief.last_result
    const role = state.lanlan || ""
    if (reviewResultRole.current !== role) {
      reviewResultRole.current = role
      reviewResultSeen.current = (result && result.ts) || 0
      return
    }
    if (!result || !result.ts) return
    if (reviewResultSeen.current === result.ts) return
    reviewResultSeen.current = result.ts
    if (result.written) {
      toast.success(t("panel.review.written", { defaultValue: "这一篇已经写好了，翻开看看吧" }))
    } else if (result.reason === "persist_failed") {
      toast.error(t("panel.review.persistFailed", { defaultValue: "这一篇写成了但没存住，素材还留着，可以再点一次" }))
    } else {
      toast.info(t("panel.review.writeFailed", { defaultValue: "这一篇还没写成，稍后再试" }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.review_brief && state.review_brief.last_result && state.review_brief.last_result.ts, state.lanlan])

  // 重置全部数据（危险区）：原走 kit ActionButton，它的错误展示是内联裸串
  // （会把我们的稳定码直喷给用户），改受控 Button + errorText 翻译——确认文案
  // 取 actions.reset.*（与后端 @ui.action 的 label/confirm 同一对键，两端同源）
  async function onResetAll() {
    const ok = await confirmDialog({
      title: t("actions.reset.label", { defaultValue: "重置全部数据" }),
      message: t("actions.reset.confirm", { defaultValue: "将清空当前角色的周期偏移、情绪状态和心情手记，确认？" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok) return
    try {
      await props.api.call("reset_all", {})
      await props.api.refresh()
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  async function onClearReview() {
    const ok = await confirmDialog({
      title: t("actions.clear_review.label", { defaultValue: "清空我的日记" }),
      message: t("actions.clear_review.confirm", { defaultValue: "将删除全部「我的日记」评价（累计素材一并清零），不可恢复，确认？" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok) return
    try {
      await props.api.call("clear_review", {})
      await props.api.refresh()
      toast.success(t("panel.review.cleared", { defaultValue: "我的日记已清空" }))
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  async function onLoadStats(month?: string, year?: string) {
    setStatsLoading(true)
    try {
      const args: Record<string, string> = {}
      if (month) args.month = month
      if (year) args.year = year
      const payload = unwrapCallResult(await props.api.call("get_stats", args))
      const r = (payload || {}) as Record<string, any>
      setHeatData((r.heatmap || {}) as Heatmap)
      setMonthData((r.month || null) as MonthReport | null)
      setMonthList(Array.isArray(r.month_available) ? (r.month_available as string[]) : [])
    } catch (err) {
      console.warn("[forever_companion] load stats failed:", err)
    } finally {
      setStatsLoading(false)
    }
  }

  // 切角色时旧统计立即失效：清到空态，避免上一个角色的热力图残留展示
  useEffect(() => {
    setHeatData(null)
    setMonthData(null)
    setMonthList([])
  }, [state.lanlan])

  // 进时光页时按需拉取一次热力图/月报（徽章与摘要在 5s 轮询里）；
  // 已有数据不重拉，翻月由 onPickMonth 显式触发
  useEffect(() => {
    if (tab === "moment" && !heatData && !statsLoading) {
      onLoadStats()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, heatData])

  // 功能管理（1.2.7）：清单数据随 dashboard 轮询到达（后端 _cap_view）；
  // 开关操作后 await props.api.refresh() 拉回最新帧——总开关在状态条拨了，
  // 功能页横幅与行开关 ≤ 下一帧自动跟上，不再有"拉一次就定格"的陈旧窗口

  // 功能介绍卡（1.2.7）：按需拉一次 get_capability_intro（不进 5s 轮询），
  // 缓存与刷新策略在 FeaturesPane 内部；这里只提供拉取通道
  async function onLoadCapIntro(id: string) {
    return unwrapCallResult(await props.api.call("get_capability_intro", { capability_id: id }))
  }

  // 能力开关：返回值供 TmSwitch 乐观回滚——被否决（reverted）或报错时回落；
  // persist_error 是"内存已生效、盘未落"的既定契约，开关保持新态不回落
  async function onToggleCap(id: string, enabled: boolean) {
    setCapsBusyId(id)
    try {
      const payload = unwrapCallResult(await props.api.call("set_capability", { capability_id: id, enabled }))
      const r = (payload || {}) as Record<string, any>
      if (r.note === "reverted_to_default") {
        toast.info(t("panel.features.reverted", { defaultValue: "该功能在功能配置里是关着的（或被依赖的功能挡住），这里无法强行点亮" }))
        return false
      } else if (r.persist_error) {
        // 既定契约：内存当场生效、盘上保持原样，如实报错可重试；
        // 后端回稳定码（i18n 契约第九轮），经 errorText 翻译而非英文裸串直出
        toast.error(errorText(r.persist_error, t))
      }
      await props.api.refresh()
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    } finally {
      setCapsBusyId("")
    }
  }

  // 返回值供 TmSwitch 乐观回滚（false = 失败回落）
  async function onToggleHideTools(value: boolean) {
    setCapsBusyId("__flags__")
    try {
      const payload = unwrapCallResult(await props.api.call("set_capability_flags", { hide_disabled_tools: value }))
      const r = (payload || {}) as Record<string, any>
      toast.success(
        r.hide_disabled_tools
          ? t("panel.features.hideToolsOn", { defaultValue: "已开启：不再生效的功能会把她看不见的工具一并摘除" })
          : t("panel.features.hideToolsOff", { defaultValue: "已回到温和模式：工具始终在位，调用时才拒绝" }),
      )
      await props.api.refresh()
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    } finally {
      setCapsBusyId("")
    }
  }

  async function onPruneLanlan(name: string) {
    if (!pruneLanlan) {
      toast.error(t("panel.errors.actionUnavailable", { defaultValue: "操作不可用（插件可能未运行）" }))
      return
    }
    const ok = await confirmDialog({
      title: t("actions.prune_lanlan.label", { defaultValue: "清除残留数据" }),
      message: t("actions.prune_lanlan.confirm", { defaultValue: "将删除该角色残留的周期、情绪与手记数据，不可恢复，确认？" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok) return
    try {
      await props.api.call("prune_lanlan", { lanlan: name })
      await props.api.refresh()
      toast.success(t("panel.lanlan.pruned", { defaultValue: "残留数据已清除" }))
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  async function onSetAnchor(date: string): Promise<boolean> {
    if (!setAnchor) return false
    const ok = await confirmDialog({
      title: t("actions.set_anchor.label", { defaultValue: "设置潮汐首日" }),
      message: t("actions.set_anchor.confirm", { defaultValue: "将重设周期锚点并重新计算阶段，确认？" }),
      tone: "warning",
      ...confirmLabels,
    })
    if (!ok) return false
    try {
      await props.api.call("set_anchor", { date })
      await props.api.refresh()
      toast.success(t("panel.messages.anchorSet", { defaultValue: "潮汐首日已更新" }))
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    }
  }

  async function onAdvance() {
    if (!advance) return
    try {
      await props.api.call("advance_days", { days: 1 })
      await props.api.refresh()
      toast.success(t("panel.messages.advanced", { defaultValue: "已快进一天" }))
    } catch (err) {
      toast.error(errorText(err, t))
    }
  }

  // 生日设置卡保存（1.3.1）：复用 update_settings 定向写生日字段（空串 = 清除休眠）；
  // 非法日期由后端发码、errorText 翻译
  async function onSetBirthday(date: string, keepDiary: boolean): Promise<boolean> {
    try {
      await props.api.call("update_settings", { birthday_date: date, birthday_keep_diary: keepDiary })
      await props.api.refresh()
      if (date) {
        toast.success(t("panel.birthday.saved", { defaultValue: "已记下主人的生日" }))
      } else {
        toast.success(t("panel.birthday.cleared", { defaultValue: "已清除生日设置" }))
      }
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    }
  }

  const VALID_TABS = ["overview", "calendar", "diary", "moment", "features", "cycle", "mood", "settings"]
  const activeTab = VALID_TABS.indexOf(tab) >= 0 ? tab : "overview"

  // 面板始终跟随宿主当前角色（5 秒轮询自动跟上切卡），lanlan_list 提供全部已知角色的状态摘要
  const lanlanKnown = (state.lanlan_list || []).filter((item) => item && item.name)

  // 页签图标用纯 CSS 线框图标（hosted 运行时不支持 SVG，也不用 emoji）
  const tabs = [
    { id: "overview", label: t("panel.tab.overview", { defaultValue: "总览" }) },
    { id: "calendar", label: t("panel.tab.calendar", { defaultValue: "日历" }) },
    { id: "diary", label: t("panel.tab.diary", { defaultValue: "日记" }) },
    { id: "moment", label: t("panel.tab.moment", { defaultValue: "时光" }) },
    { id: "features", label: t("panel.tab.features", { defaultValue: "功能" }) },
    { id: "cycle", label: t("panel.tab.cycle", { defaultValue: "周期" }) },
    { id: "mood", label: t("panel.tab.mood", { defaultValue: "情绪" }) },
    { id: "settings", label: t("panel.tab.settings", { defaultValue: "设置" }) },
  ]

  // 实时预览语义：背景层按 draft 渲染（图未拉到时留空闪一次可接受）；
  // 参数经 display:contents 包装层落成 CSS 变量供 styles.ts 的 var() 消费
  const bgDataUrl = draftAp.bg_id ? (imgCache[draftAp.bg_id] || "") : ""
  const rootStyle = Object.assign({ display: "contents" }, appearanceVars(draftAp)) as Record<string, string>

  return (
    <Page className={bgDataUrl ? "tm-has-bg" : ""}>
      <div key="root" className="tm-appearance-root" style={rootStyle}>
      <style key="styles">{PANEL_STYLES}</style>

      {bgDataUrl ? (
        <div key="bg" className="tm-bg" style={bgLayerStyle(draftAp, bgDataUrl)}>
          <div className="tm-bg-dim" style={{ opacity: String(draftAp.dim) }} />
        </div>
      ) : null}

      <StatusBar
        t={t}
        status={status}
        mood={mood}
        lanlan={state.lanlan}
        canToggle={!!toggle}
        onToggle={onToggle}
        onGotoOverview={() => setTab("overview")}
        showOffHint={status.enabled === false}
        showMoodHint={mood.system_enabled === false}
        onGotoMood={() => setTab("mood")}
      />

      {status.error ? <div key="warn-error" className="tm-warnstrip"><Alert tone="danger" message={status.error} /></div> : null}

      <div key="body" className="tm-body">
        <nav className="tm-tabs">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              className={activeTab === item.id ? "tm-tab tm-tab-active" : "tm-tab"}
              onClick={() => setTab(item.id)}
              title={item.label}
            >
              <span className={`tm-ico tm-ico-${item.id}`} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        <div
          className={contentHot || contentScrolling ? "tm-content tm-content-hot" : "tm-content"}
          key={activeTab}
          onScroll={markContentScrolling}
          onPointerMove={(e: any) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const near = rect.right - e.clientX <= 16
            if (near !== contentHot) setContentHot(near)
          }}
          onPointerLeave={() => setContentHot(false)}
        >
          {activeTab === "overview" ? (
            <OverviewPane
              t={t}
              status={status}
              mood={mood}
              settings={settings}
              lanlan={state.lanlan}
              diaryTotal={state.diary_total || 0}
              fragmentTotal={state.fragment_total || 0}
              journalIndex={state.journal_index || []}
              reviewBrief={state.review_brief}
              weekActivity={state.week_activity}
              onboarding={state.onboarding}
              canEnable={!!toggle}
              onEnable={onToggle}
              onGoto={(id: string) => setTab(id)}
            />
          ) : null}
          {activeTab === "calendar" ? (
            <CalendarPane
              key={state.lanlan || ""}
              t={t}
              calendar={state.calendar}
              anchorDate={state.anchor_date}
              advanceDays={state.advance_days}
              lanlan={state.lanlan}
              canSetAnchor={!!setAnchor}
              canAdvance={!!advance}
              onSetAnchor={onSetAnchor}
              onAdvance={onAdvance}
            />
          ) : null}
          {activeTab === "cycle" ? (
            <div className="tm-pane">
              {!state.anchor_date ? (
                <Alert tone="warning" message={t("panel.errors.setAnchorFirst", { defaultValue: "请先设置潮汐首日锚点，再开启模拟" })} />
              ) : null}
              <CycleSettingsCard t={t} settings={settings} form={form} updateForm={updateForm} lanlan={state.lanlan} />
              <InjectSettingsCard t={t} form={form} updateForm={updateForm} />
              <SaveBar t={t} canSave={!!updateSettingsAction} onSave={saveSettings} />
            </div>
          ) : null}
          {activeTab === "mood" ? (
            <div className="tm-pane">
              <MoodSettingsCard t={t} form={form} updateForm={updateForm} />
              <EmotionSenseSettingsCard t={t} form={form} updateForm={updateForm} />
              <ChannelSettingsCard
                t={t}
                form={form}
                updateForm={updateForm}
                toneSlotOptions={state.tone_slot_options}
                channelStatus={state.channel_status}
              />
              <SaveBar t={t} canSave={!!updateSettingsAction} onSave={saveSettings} />
            </div>
          ) : null}
          {activeTab === "diary" ? (
            <DiaryPane
              t={t}
              diary={state.diary_recent || []}
              diaryTotal={state.diary_total || 0}
              fragmentTotal={state.fragment_total || 0}
              journalIndex={state.journal_index || []}
              journalArchiveBrief={state.journal_archive_brief}
              invitePending={!!state.journal_invite_pending}
              lanlan={state.lanlan}
              reviewBrief={state.review_brief}
              reviewArchiveBrief={state.review_archive_brief}
              onClearDiary={onClearDiary}
              onDeleteFragment={onDeleteFragment}
              onLoadJournal={onLoadJournal}
              onLoadJournalArchive={onLoadJournalArchive}

              onLoadMoreDiary={onLoadMoreDiary}
              onInviteJournal={onInviteJournal}
              onLoadReview={onLoadReview}
              onLoadReviewArchive={onLoadReviewArchive}
              onWriteReviewNow={onWriteReviewNow}
              onClearReview={onClearReview}
              settingsChildren={(
                <>
                  <DiarySettingsCard t={t} form={form} updateForm={updateForm} />
                  <SaveBar t={t} canSave={!!updateSettingsAction} onSave={saveSettings} />
                </>
              )}
            />
          ) : null}
          {activeTab === "moment" ? (
            <MomentPane
              t={t}
              lanlan={state.lanlan}
              summary={state.stats_summary ? state.stats_summary.summary : undefined}
              badges={state.stats_summary ? state.stats_summary.badges : undefined}
              heatmap={heatData}
              month={monthData}
              monthAvailable={monthList}
              monthLoading={statsLoading}
              birthday={state.birthday}
              canSaveBirthday={!!updateSettingsAction}
              onSetBirthday={onSetBirthday}
              onPickMonth={(month: string) => onLoadStats(month)}
              // 翻年份时保住当前正在看的月份，月报不跟着跳回当月
              onPickYear={(year: string) =>
                onLoadStats(String((monthData && monthData.month) || "") || undefined, year)
              }
            />
          ) : null}
          {activeTab === "features" ? (
            <FeaturesPane
              t={t}
              caps={state.capabilities}
              loading={!state.capabilities}
              busyId={capsBusyId}
              onToggleCap={onToggleCap}
              onToggleHideTools={onToggleHideTools}
              onLoadIntro={onLoadCapIntro}
            />
          ) : null}
          {activeTab === "settings" ? (
            <ManagePane
              t={t}
              form={form}
              updateForm={updateForm}
              resetAll={resetAll}
              onResetAll={onResetAll}
              lanlanList={lanlanKnown}
              lanlan={state.lanlan}
              canPrune={!!pruneLanlan}
              onPruneLanlan={onPruneLanlan}
              onReopenGuide={reopenGuide}
            >
              <AppearanceCard
                t={t}
                items={galleryItems}
                draft={draftAp}
                saved={savedAp}
                saving={apSaving}
                uploading={apBusy}
                onDraft={onDraftChange}
                onSave={saveAppearance}
                onRevert={() => { setDraftAp(savedAp) }}
                onAdd={addImage}
                onAskRemove={(item: GalleryItem) => removeImage(String(item.id || ""))}
              />
            </ManagePane>
          ) : null}
        </div>
      </div>

      <OnboardingWizard
        t={t}
        open={!!(state.onboarding && state.onboarding.wizard_pending) && !wizardClosed}
        status={status}
        channelStatus={state.channel_status}
        canToggle={!!toggle}
        onEnableRhythm={() => { onToggle() }}
        birthday={state.birthday}
        canSaveBirthday={!!updateSettingsAction}
        onSetBirthday={onSetBirthday}
        onGotoAppearance={() => {
          // 外观页只做引导跳转：向导收尾（记 done）并把面板落到设置页——
          // 真实的外观卡原地可用，向导里不复刻那份逻辑
          void closeWizard("done")
          setTab("settings")
        }}
        onFinish={(action: "done" | "skip") => { closeWizard(action) }}
      />
      </div>
    </Page>
  )
}
