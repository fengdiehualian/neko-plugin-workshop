// 永远的陪伴面板入口：context 轮询、动作处理、Tab 导航与各页组装
// 顶级页签：日历 / 周期 / 注入 / 情绪（情绪系统+语气感知）/ 手记 / 管理（危险区+角色名单）
import {
  Alert,
  Page,
} from "@neko/plugin-ui"
import { useConfirm, useEffect, useLocalState, useRef, useState, useToast } from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"
import type { Appearance, FormValues, GalleryItem, MediaStorage, Settings, State } from "./types"
import {
  DATE_RE,
  appearanceEquals,
  appearanceVars,
  bgLayerStyle,
  compressImageDataUrl,
  normAppearance,
  settingsToForm,
} from "./utils"
import { PANEL_STYLES } from "./styles"
import { CAPDEMO_STYLES } from "./styles_capdemo"
import { DEFAULT_THEME_COLORS, readWallpaperTheme, themeColorVars } from "./theme"
import type { ThemeColors } from "./theme"
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
import { BackupPanel } from "./backup"
import { FeaturesPane } from "./features"
import { OnboardingWizard } from "./onboarding"
import { DiaryPane } from "./diary"
import { MomentPane } from "./moment"
import { AppearanceCard } from "./appearance"
import {
  BackgroundVideo,
  cancelMediaTask,
  downloadGalleryVideo,
  readMediaStorage,
  uploadVideoFile,
  MEDIA_CLEANUP_TIMEOUT_MS,
  MEDIA_REQUEST_TIMEOUT_MS,
  VIDEO_MAX_BYTES,
  VIDEO_HARD_MAX_BYTES,
} from "./media"
import type { GalleryMedia, MediaTask } from "./media"
import type { DiaryItem, JournalPage, Heatmap, MonthReport, ReviewEntry, ReviewProgress } from "./types"
import { unwrapCallResult, errorText, diaryWriteError } from "./utils"

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
  const [backupRevision, setBackupRevision] = useState(0)
  const [recordsRevision, setRecordsRevision] = useState(0)
  const resetLock = useRef(false)
  const [resetting, setResetting] = useState(false)
  const statsGeneration = useRef(0)
  const [form, setForm] = useState<FormValues>(settingsToForm({}))
  // 面板外观（1.2.0）：图库索引/外观参数（saved=生效、draft=实时预览）按需拉取；
  // 图片本体逐条缓存（imgCache），绝不进 5s 轮询载荷
  const [galleryItems, setGalleryItems] = useState<GalleryItem[]>([])
  const [mediaStorage, setMediaStorage] = useState<MediaStorage | null>(null)
  const galleryItemsRef = useRef(galleryItems)
  galleryItemsRef.current = galleryItems
  const [savedAp, setSavedAp] = useState<Appearance>(normAppearance(null))
  const [draftAp, setDraftAp] = useState<Appearance>(normAppearance(null))
  const [imgCache, setImgCache] = useState<Record<string, string>>({})
  const [readyBg, setReadyBg] = useState<{ id: string; dataUrl: string; theme: ThemeColors | null; media?: GalleryMedia }>({ id: "", dataUrl: "", theme: null })
  const readyBgRef = useRef(readyBg)
  const previousReadyBg = useRef<typeof readyBg | null>(null)
  readyBgRef.current = readyBg
  const videoCache = useRef<Record<string, GalleryMedia>>({})
  const pendingUploadedVideo = useRef("")
  const selectedBgId = useRef(draftAp.bg_id)
  selectedBgId.current = draftAp.bg_id
  const [reducedMotion, setReducedMotion] = useState(() => typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches)
  const playbackRequested = useRef(true)
  playbackRequested.current = draftAp.motion && !reducedMotion
  const [backgroundError, setBackgroundError] = useState("")
  const [backgroundProgress, setBackgroundProgress] = useState<{ id: string; value: number; stage: string } | null>(null)
  const [videoProgress, setVideoProgress] = useState<{ value: number; stage: string } | null>(null)
  const uploadTask = useRef<MediaTask | null>(null)
  const uploadFinished = useRef<Promise<void> | null>(null)
  const mediaClearing = useRef(false)
  const panelAlive = useRef(true)
  const themeCache = useRef<Record<string, { dataUrl: string; theme: ThemeColors }>>({})
  const imgInflight = useRef<Record<string, MediaTask>>({})
  const thumbTried = useRef<Record<string, boolean>>({})
  const mediaGeneration = useRef(0)
  const gallerySnapshotRevision = useRef(0)
  const [mediaReset, setMediaReset] = useState(0)
  const [apBusy, setApBusy] = useState(false)
  // 设置页那枚「保存设置」在飞标记：它一趟按两摊（设置 + 外观），连点会重复写盘
  const [savingAll, setSavingAll] = useState(false)
  const [journalSubmitting, setJournalSubmitting] = useState(false)
  const [reviewSubmitting, setReviewSubmitting] = useState(false)
  const [moodEnding, setMoodEnding] = useState(false)
  const moodEndLock = useRef(false)
  const currentMoodRef = useRef({ lanlan: state.lanlan, startedAt: mood.started_at })
  currentMoodRef.current = { lanlan: state.lanlan, startedAt: mood.started_at }
  const journalSubmitLock = useRef(false)
  const reviewSubmitLock = useRef(false)
  // 时光页：相处统计的热力图/月报数据量大，进页时按需拉取（不随 5s 轮询）；
  // 切角色时清掉旧数据等下次进页重拉
  const [heatData, setHeatData] = useState<Heatmap | null>(null)
  const [monthData, setMonthData] = useState<MonthReport | null>(null)
  const [monthList, setMonthList] = useState<string[]>([])
  const [statsLoading, setStatsLoading] = useState(false)
  // 功能管理页（1.2.7）：能力清单随 dashboard 5s 轮询下发（state.capabilities），
  // 与总开关/其它页设置同帧一致；本页只留"操作在飞"禁用态防连点
  const [capsBusyId, setCapsBusyId] = useState("")

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    const query = window.matchMedia("(prefers-reduced-motion: reduce)")
    const change = () => setReducedMotion(query.matches)
    if (query.addEventListener) query.addEventListener("change", change)
    else query.addListener(change)
    return () => {
      if (query.removeEventListener) query.removeEventListener("change", change)
      else query.removeListener(change)
    }
  }, [])

  function updateForm(patch: Partial<FormValues>) {
    setForm((prev) => ({ ...prev, ...patch }))
  }

  // 已落盘基线（1.3.3 修订轮）：保存时只提交与它不同的字段。旧做法是把整张表单回写
  // `update_settings`，于是用户从没碰过的下拉也会进 Store 覆盖层——而覆盖层优先于
  // plugin.toml（mixins/shards.py 的"面板保存过的全局字段优先于 toml 默认"），此后
  // 插件再怎么改出厂默认都追不到这份存档上。2026-09-22 实机就卡在这：碎片出厂默认
  // 从 host 改成 custom，老存档里躺着的那份 host 把它压了一整天。
  const formBaseline = useRef<FormValues>(settingsToForm({}))

  // 表单与服务端快照同步的唯一入口：回填表单的同时把基线挪到同一份值上，
  // 两者任何时候都同源，"改没改过"才算得出真话
  function syncForm(next: Settings) {
    const values = settingsToForm(next)
    formBaseline.current = values
    setForm(values)
  }

  useEffect(() => {
    syncForm(settings)
  }, [
    backupRevision,
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
    settings.tone_min_interval_sec,
    settings.tone_check_rate,
    settings.tone_phase_sensitivity_enabled,
    settings.tone_phase_sensitivity,
    settings.tone_slot,
    settings.fragments_enabled,
    settings.fragments_slot,
    settings.fragments_mode,
    settings.review_enabled,
    settings.review_slot,
    settings.review_mode,
    settings.review_turns_threshold,
    settings.review_days_threshold,
    settings.agent_daily_budget,
    (settings as Record<string, any>).anniversary_inject,
  ])

  // 面板打开期间周期性同步 context：宿主切换角色后自动跟上，情绪状态/阶段等外部变化
  // （入口点触发、tick 到期、对话中的 LLM 工具调用）无需重进面板即可看到
  useEffect(() => {
    let alive = true
    let refreshing = false
    const refresh = () => {
      if (!alive || document.hidden || refreshing) return
      refreshing = true
      Promise.resolve().then(() => props.api.refresh()).catch(() => {}).finally(() => { refreshing = false })
    }
    const timer = setInterval(refresh, 5000)
    document.addEventListener("visibilitychange", refresh)
    return () => {
      alive = false
      clearInterval(timer)
      document.removeEventListener("visibilitychange", refresh)
    }
  }, [])

  // 外观（1.2.0）：面板打开时拉一次图库索引 + 已存参数（图片本体不进轮询）；
  // 旧版单图背景在后端入口内自动迁移进图库。拉取失败按默认外观处理，不弹错
  useEffect(() => {
    let alive = true
    panelAlive.current = true
    const generation = mediaGeneration.current
    const revision = gallerySnapshotRevision.current
    const controller = new AbortController()
    Promise.resolve(props.api.call("get_panel_gallery", {
      include_background: true, prefer_direct: !reducedMotion,
    }, {
      timeoutMs: MEDIA_REQUEST_TIMEOUT_MS, signal: controller.signal,
    }))
      .then((payload) => {
        if (!alive || mediaGeneration.current !== generation || gallerySnapshotRevision.current !== revision) return
        const r = (unwrapCallResult(payload) || {}) as Record<string, any>
        const items = Array.isArray(r.items) ? (r.items as GalleryItem[]) : []
        const ap = normAppearance(r.appearance)
        setGalleryItems(items)
        try {
          setMediaStorage(readMediaStorage(r.media_storage))
        } catch {
          setMediaStorage(null)
        }
        setSavedAp(ap)
        setDraftAp(ap)
        playbackRequested.current = ap.motion && !reducedMotion
        if (ap.bg_id) {
          loadImage(ap.bg_id, false, r.background?.id === ap.bg_id ? r.background.media : undefined)
        }
      })
      .catch(() => {
        if (alive && mediaGeneration.current === generation && gallerySnapshotRevision.current === revision) console.warn("[forever_companion] load panel gallery failed")
      })
    return () => {
      alive = false
      controller.abort()
      panelAlive.current = false
      if (uploadTask.current) cancelMediaTask(uploadTask.current)
      mediaGeneration.current += 1
      for (const id of Object.keys(imgInflight.current)) cancelMediaTask(imgInflight.current[id])
      imgInflight.current = {}
      for (const id of Object.keys(videoCache.current)) {
        if (videoCache.current[id].url) URL.revokeObjectURL(videoCache.current[id].url)
      }
      videoCache.current = {}
      pendingUploadedVideo.current = ""
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backupRevision])

  // 图片本体按需拉取（带在途去重）：选中某张壁纸时若没缓存就拉
  function loadImage(id: string, forceBlob = false, initialMedia?: Record<string, any>) {
    if (!id) return
    if (imgInflight.current[id]) return
    const cachedVideo = videoCache.current[id]
    if (!forceBlob && imgCache[id] && (!cachedVideo || cachedVideo.url || !playbackRequested.current)) return
    const request: MediaTask = { cancelled: false, controller: new AbortController() }
    const generation = mediaGeneration.current
    imgInflight.current[id] = request
    setBackgroundError("")
    setBackgroundProgress({ id, value: 0, stage: "preparing" })
    Promise.resolve(initialMedia || props.api.call(
      "get_gallery_image",
      { item_id: id, prefer_direct: !forceBlob && playbackRequested.current && !!props.host?.origin },
      { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS, signal: request.controller?.signal },
    ))
      .then(async (payload) => {
        if (imgInflight.current[id] !== request) return
        if (mediaGeneration.current !== generation) return
        const r = (unwrapCallResult(payload) || {}) as Record<string, any>
        if (r.kind === "video") {
          if (typeof r.poster !== "string" || r.poster.length > 1000000
            || !/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/.test(r.poster)) throw new Error("video_read_failed")
          let media: GalleryMedia = { kind: "video", url: "", poster: String(r.poster || "") }
          videoCache.current[id] = { ...media, loading: playbackRequested.current }
          if (!readyBgRef.current.dataUrl || readyBgRef.current.id === id) {
            setImgCache((prev) => ({ ...prev, [id]: media.poster }))
          }
          if (playbackRequested.current) {
            try {
              media = await downloadGalleryVideo(props.api.call, id, r, request, (value) => {
                if (!request.cancelled && mediaGeneration.current === generation && imgInflight.current[id] === request) {
                  setBackgroundProgress({ id, value, stage: "downloading" })
                }
              }, forceBlob ? undefined : props.host?.origin)
            } catch (error) {
              if (request.cancelled) return
              if (readyBgRef.current.dataUrl && readyBgRef.current.id !== id) throw error
              media.error = errorText(error, t)
            }
          }
          if (request.cancelled || mediaGeneration.current !== generation || imgInflight.current[id] !== request) {
            if (media.url) URL.revokeObjectURL(media.url)
            return
          }
          const previous = videoCache.current[id]
          if (previous?.url && previous.url !== media.url) URL.revokeObjectURL(previous.url)
          videoCache.current[id] = media
          if (media.url) setBackgroundProgress({ id, value: 100, stage: "preparing" })
          else {
            setBackgroundError(media.error || "")
            setBackgroundProgress((current) => current?.id === id ? null : current)
            setReadyBg((current) => current.id === id ? { ...current, media } : current)
          }
          setImgCache((prev) => ({ ...prev, [id]: media.url || media.poster }))
          return
        }
        if (r && typeof r.data_url === "string" && r.data_url) {
          setBackgroundProgress((current) => current?.id === id ? null : current)
          setImgCache((prev) => ({ ...prev, [id]: r.data_url }))
        }
      })
      .catch((err) => {
        if (request.cancelled || mediaGeneration.current !== generation || imgInflight.current[id] !== request) return
        const message = errorText(err, t)
        const item = galleryItemsRef.current.find((entry) => entry.id === id)
        const thumb = String(item?.thumb || "")
        if (!readyBgRef.current.dataUrl && item?.kind === "video" && thumb.length <= 400000
          && /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/.test(thumb)) {
          videoCache.current[id] = { kind: "video", url: "", poster: thumb, error: message }
          setImgCache((prev) => ({ ...prev, [id]: thumb }))
        }
        setBackgroundError(message)
        setBackgroundProgress((current) => current?.id === id ? null : current)
      })
      .finally(() => {
        if (imgInflight.current[id] === request) delete imgInflight.current[id]
        if (request.cancelled) setBackgroundProgress((current) => current?.id === id ? null : current)
      })
  }

  // draft 指向的图若未缓存则补拉（切换/新加入库时）
  useEffect(() => {
    // Switching cancels the old download before the next chunk is requested.
    if (readyBgRef.current.media?.loading && previousReadyBg.current && readyBgRef.current.id !== draftAp.bg_id) {
      setReadyBg(previousReadyBg.current)
      previousReadyBg.current = null
    }
    for (const id of Object.keys(imgInflight.current)) {
      if (id !== draftAp.bg_id || (!playbackRequested.current && galleryItems.some((item) => item.id === id && item.kind === "video"))) {
        cancelMediaTask(imgInflight.current[id])
        delete imgInflight.current[id]
      }
    }
    loadImage(draftAp.bg_id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftAp.bg_id, draftAp.motion, reducedMotion])

  // 新壁纸准备好才替换；切换期间保留旧图，取消/删除会使迟到的解码失效。
  const requestedBgUrl = draftAp.bg_id ? (imgCache[draftAp.bg_id] || "") : ""
  useEffect(() => {
    if (!draftAp.bg_id) {
      previousReadyBg.current = null
      setReadyBg({ id: "", dataUrl: "", theme: null })
      setBackgroundError("")
      setBackgroundProgress(null)
      return
    }
    if (!requestedBgUrl) return
    let alive = true
    const generation = mediaGeneration.current
    const media = videoCache.current[draftAp.bg_id]
    const themeSource = media ? media.poster : requestedBgUrl
    const image = new Image()
    const ready = () => {
      if (!alive || mediaGeneration.current !== generation) return
      const latestMedia = videoCache.current[draftAp.bg_id]
      const currentMedia = latestMedia && latestMedia.url === media?.url ? latestMedia : media
      const cached = themeCache.current[draftAp.bg_id]
      const theme = cached?.dataUrl === themeSource ? cached.theme : readWallpaperTheme(image)
      themeCache.current[draftAp.bg_id] = { dataUrl: themeSource, theme }
      const previous = readyBgRef.current
      if (currentMedia?.url && previous.dataUrl && previous.id !== draftAp.bg_id && !previous.media?.loading) {
        previousReadyBg.current = previous
      } else if (!currentMedia?.loading && !currentMedia?.url) previousReadyBg.current = null
      // 壁纸和配色同帧切换，草稿还原和迟到解码共享同一个生命周期。
      setReadyBg({ id: draftAp.bg_id, dataUrl: themeSource, theme,
        media: currentMedia?.url ? { ...currentMedia, loading: true } : currentMedia })
      setBackgroundError(currentMedia?.error || "")
      if (currentMedia?.url) setBackgroundProgress({ id: draftAp.bg_id, value: 100, stage: "preparing" })
      else if (!currentMedia?.loading) setBackgroundProgress((current) => current?.id === draftAp.bg_id ? null : current)
    }
    image.onload = () => {
      const prepare = async () => {
        if (image.decode) await image.decode()
        if (!alive || mediaGeneration.current !== generation) return
        ready()
      }
      prepare().catch((err) => {
        if (!alive || mediaGeneration.current !== generation) return
        setBackgroundError(errorText(media ? err : "wallpaper_image_decode_failed", t))
        setBackgroundProgress((current) => current?.id === draftAp.bg_id ? null : current)
        // A usable poster remains a truthful fallback if playback fails.
        if (media && !readyBgRef.current.dataUrl) {
          setReadyBg({ id: draftAp.bg_id, dataUrl: themeSource, theme: readWallpaperTheme(image), media: { ...media, url: "" } })
        }
      })
    }
    image.onerror = () => {
      if (alive && mediaGeneration.current === generation) {
        setBackgroundError(errorText("wallpaper_image_decode_failed", t))
        setBackgroundProgress((current) => current?.id === draftAp.bg_id ? null : current)
      }
    }
    if (themeCache.current[draftAp.bg_id]?.dataUrl === themeSource) ready()
    else image.src = themeSource
    return () => {
      alive = false
      image.onload = image.onerror = null
    }
  }, [draftAp.bg_id, requestedBgUrl])

  function backgroundPlaybackError(id: string, media: GalleryMedia) {
    if (!panelAlive.current || readyBgRef.current.id !== id || readyBgRef.current.media?.url !== media.url) return
    if (media.direct && selectedBgId.current === id) {
      // A cache eviction or an older host can fail after the initial probe.
      // Recover once via the established Blob path for this displayed item.
      setReadyBg((current) => current.id === id ? { ...current, media: { ...media, url: "", direct: false, loading: true } } : current)
      loadImage(id, true)
    } else {
      const previous = previousReadyBg.current
      previousReadyBg.current = null
      if (previous && previous.id !== id && galleryItemsRef.current.some((item) => item.id === previous.id)) {
        setReadyBg(previous)
      } else if (media.loading) setReadyBg((current) => current.id === id ? { ...current, media: { ...media, url: "", loading: false } } : current)
      setBackgroundProgress((current) => current?.id === id ? null : current)
      setBackgroundError(errorText("wallpaper_video_play_failed", t))
    }
  }

  function backgroundPlaybackReady(id: string, media: GalleryMedia) {
    if (!panelAlive.current || mediaClearing.current || readyBgRef.current.id !== id || readyBgRef.current.media?.url !== media.url) return
    previousReadyBg.current = null
    setReadyBg((current) => current.id === id && current.media?.url === media.url
      ? { ...current, media: { ...media, loading: false } } : current)
    setBackgroundProgress((current) => current?.id === id ? null : current)
  }

  // Keep at most the displayed video and the pending selection in memory.
  useEffect(() => {
    for (const id of Object.keys(videoCache.current)) {
      if (id === readyBg.id || id === draftAp.bg_id || id === previousReadyBg.current?.id || id === pendingUploadedVideo.current) continue
      if (videoCache.current[id].url) URL.revokeObjectURL(videoCache.current[id].url)
      delete videoCache.current[id]
      setImgCache((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    }
  }, [readyBg.id, readyBg.media?.loading, draftAp.bg_id])

  // 缺缩略图的在用图（如旧版迁移来的 legacy）：拿到本体后本地画一张回填
  useEffect(() => {
    const current = savedAp.bg_id
    const item = galleryItems.find((it) => (it.id || "") === current)
    if (!current || !item || item.kind === "video" || item.thumb || thumbTried.current[current]) return
    const dataUrl = imgCache[current]
    if (!dataUrl) return
    thumbTried.current[current] = true
    const generation = mediaGeneration.current
    compressImageDataUrl(dataUrl, item.mime || "image/png", "raw")
      .then((res) => {
        if (res.thumb && panelAlive.current && mediaGeneration.current === generation) {
          props.api.call(
            "gallery_set_thumb",
            { item_id: current, thumb: res.thumb },
            { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS },
          )
            .then(() => { if (panelAlive.current && mediaGeneration.current === generation) setGalleryItems((prev) => prev.map((it) => ((it.id || "") === current ? { ...it, thumb: res.thumb } : it))) })
            .catch(() => {})
        }
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [galleryItems, imgCache, savedAp.bg_id])

  function onDraftChange(patch: Record<string, string | number | boolean>) {
    if (typeof patch.bg_id === "string" && pendingUploadedVideo.current) {
      const pending = pendingUploadedVideo.current
      pendingUploadedVideo.current = ""
      if (patch.bg_id !== pending && pending !== readyBgRef.current.id && pending !== selectedBgId.current) {
        if (videoCache.current[pending]?.url) URL.revokeObjectURL(videoCache.current[pending].url)
        delete videoCache.current[pending]
        delete themeCache.current[pending]
        setImgCache((prev) => {
          const next = { ...prev }
          delete next[pending]
          return next
        })
      }
    }
    setDraftAp((prev) => normAppearance(Object.assign({}, prev, patch)))
  }

  // 外观落盘：整包 draft 参数发后端（悬空 bg_id 会被后端静默解除），回填归一结果。
  // 只发不弹——成功与否由调用方（设置页的统一保存）汇成一条反馈
  async function postAppearanceDraft() {
    const next = draftAp
    const payload = unwrapCallResult(await props.api.call("set_panel_appearance", {
      bg_id: next.bg_id, fill: next.fill, position: next.position,
      blur: next.blur, dim: next.dim, brightness: next.brightness,
      saturate: next.saturate, contrast: next.contrast, glass: next.glass,
      card_alpha: next.card_alpha, text_weight: next.text_weight,
      motion: next.motion,
    }))
    const r = (payload || {}) as Record<string, any>
    const ap = normAppearance(r.appearance || next)
    setSavedAp(ap)
    setDraftAp(ap)
  }

  // 导入：面板侧已压好并生成缩略图；入册即时生效但不自动选为壁纸（选图走 draft+保存）
  async function addImage(dataUrl: string, thumb: string, name: string) {
    if (apBusy || mediaClearing.current) return false
    setApBusy(true)
    const generation = mediaGeneration.current
    try {
      const payload = unwrapCallResult(await props.api.call(
        "gallery_add",
        { data_url: dataUrl, thumb, name },
        { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS },
      ))
      const r = (payload || {}) as Record<string, any>
      if (!panelAlive.current || mediaGeneration.current !== generation) return false
      gallerySnapshotRevision.current += 1
      const items = Array.isArray(r.items) ? (r.items as GalleryItem[]) : []
      setGalleryItems(items)
      const newId = String(r.id || "")
      if (newId) {
        setImgCache((prev) => ({ ...prev, [newId]: dataUrl }))
      }
      toast.success(t("panel.appearance.added", { defaultValue: "已加入图库" }))
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    } finally {
      if (!mediaClearing.current) setApBusy(false)
    }
  }

  async function addVideo(file: File, name: string): Promise<boolean> {
    if (apBusy || uploadTask.current || mediaClearing.current) return false
    const task: MediaTask = { cancelled: false }
    const generation = mediaGeneration.current
    let finishUpload = () => {}
    uploadFinished.current = new Promise<void>((resolve) => { finishUpload = resolve })
    uploadTask.current = task
    setApBusy(true)
    try {
      const result = await uploadVideoFile(props.api.call, file, name, task, (value, stage) => {
        if (panelAlive.current && uploadTask.current === task) setVideoProgress({ value, stage })
      }, mediaStorage?.single_limit_bytes || VIDEO_MAX_BYTES)
      if (!panelAlive.current || mediaGeneration.current !== generation) return false
      const newId = String(result.id || "")
      if (!newId) throw new Error("video_save_failed")
      const previousPending = pendingUploadedVideo.current
      if (previousPending && previousPending !== readyBgRef.current.id && previousPending !== selectedBgId.current) {
        if (videoCache.current[previousPending]?.url) URL.revokeObjectURL(videoCache.current[previousPending].url)
        delete videoCache.current[previousPending]
        delete themeCache.current[previousPending]
      }
      const localUrl = URL.createObjectURL(file)
      videoCache.current[newId] = { kind: "video", url: localUrl, poster: result.localFrames.poster }
      pendingUploadedVideo.current = newId
      setImgCache((prev) => {
        const next = { ...prev, [newId]: localUrl }
        if (previousPending && previousPending !== readyBgRef.current.id && previousPending !== selectedBgId.current) delete next[previousPending]
        return next
      })
      gallerySnapshotRevision.current += 1
      setGalleryItems(Array.isArray(result.items) ? result.items as GalleryItem[] : [])
      try { setMediaStorage(readMediaStorage(result.media_storage)) } catch {}
      toast.success(t("panel.appearance.added", { defaultValue: "已加入图库" }))
      return true
    } catch (err) {
      if (panelAlive.current && !task.cancelled) toast.error(errorText(err, t))
      return false
    } finally {
      if (uploadTask.current === task) uploadTask.current = null
      finishUpload()
      uploadFinished.current = null
      if (panelAlive.current) {
        if (!mediaClearing.current) setApBusy(false)
        setVideoProgress(null)
      }
    }
  }

  // 删除图库图（danger 确认）：后端会顺带解除在用的 bg_id，回填 appearance
  async function removeImage(id: string) {
    if (!id || mediaClearing.current) return
    const ok = await confirmDialog({
      title: t("actions.gallery_remove.label", { defaultValue: "从图库删除壁纸" }),
      message: t("actions.gallery_remove.confirm", { defaultValue: "确定从图库删除这项壁纸吗？如果正在使用，也会取消壁纸。删除后无法恢复。" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok || mediaClearing.current) return
    setApBusy(true)
    gallerySnapshotRevision.current += 1
    mediaGeneration.current += 1
    for (const current of Object.keys(imgInflight.current)) cancelMediaTask(imgInflight.current[current])
    imgInflight.current = {}
    if (videoCache.current[id]?.url) URL.revokeObjectURL(videoCache.current[id].url)
    delete videoCache.current[id]
    if (pendingUploadedVideo.current === id) pendingUploadedVideo.current = ""
    delete themeCache.current[id]
    setImgCache((prev) => {
      const cache = { ...prev }
      delete cache[id]
      return cache
    })
    try {
      const payload = unwrapCallResult(await props.api.call(
        "gallery_remove",
        { item_id: id },
        { timeoutMs: MEDIA_CLEANUP_TIMEOUT_MS },
      ))
      const r = (payload || {}) as Record<string, any>
      const items = Array.isArray(r.items) ? (r.items as GalleryItem[]) : []
      setGalleryItems(items)
      const ap = normAppearance(r.appearance)
      setSavedAp(ap)
      setDraftAp(ap)
      try { setMediaStorage(readMediaStorage(r.media_storage)) } catch {}
      loadImage(ap.bg_id)
      toast.success(t("panel.diary.deleted", { defaultValue: "已删除" }))
    } catch (err) {
      toast.error(errorText(err, t))
      try {
        const ap = await refreshMediaSnapshot()
        if (ap) loadImage(ap.bg_id)
      } catch {}
    } finally {
      if (!mediaClearing.current) setApBusy(false)
    }
  }

  async function updateVideoPolicy(singleLimit: number, totalLimit: number): Promise<boolean> {
    const single = Math.max(32, Math.min(512, Math.round(Number(singleLimit) || 0)))
    const total = Math.max(64, Math.min(16384, Math.round(Number(totalLimit) || 0)))
    try {
      const payload = unwrapCallResult(await props.api.call(
        "gallery_add",
        { op: "video_policy", single_limit_mib: single, total_limit_mib: total },
        { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS },
      ))
      const next = readMediaStorage(payload?.media_storage)
      if (!next) throw new Error("video_storage_invalid")
      setMediaStorage(next)
      toast.success(t("panel.appearance.policySaved", { defaultValue: "容量设置已更新" }))
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    }
  }

  async function refreshMediaSnapshot() {
    const generation = mediaGeneration.current
    const revision = gallerySnapshotRevision.current
    const result = unwrapCallResult(await props.api.call(
      "get_panel_gallery",
      {},
      { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS },
    ))
    if (!panelAlive.current || mediaGeneration.current !== generation || gallerySnapshotRevision.current !== revision) return
    const ap = normAppearance(result.appearance)
    setGalleryItems(Array.isArray(result.items) ? result.items as GalleryItem[] : [])
    setSavedAp(ap)
    setDraftAp(ap)
    setMediaStorage(readMediaStorage(result.media_storage))
    return ap
  }

  function invalidateRecords() {
    statsGeneration.current += 1
    setStatsLoading(false)
    setHeatData(null)
    setMonthData(null)
    setMonthList([])
    setRecordsRevision((value) => value + 1)
  }

  async function onBackupRestored() {
    invalidateRecords()
    mediaGeneration.current += 1
    gallerySnapshotRevision.current += 1
    themeCache.current = {}
    thumbTried.current = {}
    previousReadyBg.current = null
    setImgCache({})
    setReadyBg({ id: "", dataUrl: "", theme: null })
    setBackgroundError("")
    setBackgroundProgress(null)
    setMediaReset((value) => value + 1)
    setBackupRevision((value) => value + 1)
    toast.success(t("panel.backup.restored"))
    try { await props.api.refresh() }
    catch (err) { toast.error(errorText(err, t)) }
  }

  async function clearMedia(): Promise<boolean> {
    if (mediaClearing.current) return false
    const ok = await confirmDialog({
      title: t("panel.appearance.clearMedia", { defaultValue: "清空壁纸与缓存" }),
      message: t("panel.appearance.clearMediaConfirm", { defaultValue: "清空本插件的所有壁纸、封面、上传暂存和面板媒体缓存，无法恢复。日记、相处统计和其他设置不受影响。" }),
      tone: "danger",
      ...confirmLabels,
    })
    if (!ok || mediaClearing.current) return false
    mediaClearing.current = true
    try {
      setApBusy(true)
      gallerySnapshotRevision.current += 1
      mediaGeneration.current += 1
      setMediaReset((value) => value + 1)
      if (uploadTask.current) cancelMediaTask(uploadTask.current)
      for (const id of Object.keys(imgInflight.current)) cancelMediaTask(imgInflight.current[id])
      imgInflight.current = {}
      for (const id of Object.keys(videoCache.current)) {
        if (videoCache.current[id].url) URL.revokeObjectURL(videoCache.current[id].url)
      }
      videoCache.current = {}
      pendingUploadedVideo.current = ""
      themeCache.current = {}
      thumbTried.current = {}
      setImgCache({})
      const emptyBackground = { id: "", dataUrl: "", theme: null }
      readyBgRef.current = emptyBackground
      setReadyBg(emptyBackground)
      setBackgroundError("")
      setBackgroundProgress(null)
      if (uploadFinished.current) await uploadFinished.current
      const payload = unwrapCallResult(await props.api.call(
        "gallery_remove",
        { op: "clear_media", confirm: true },
        { timeoutMs: MEDIA_CLEANUP_TIMEOUT_MS },
      ))
      if (payload?.cleared !== true) throw new Error("video_cleanup_incomplete")
      setGalleryItems(Array.isArray(payload.items) ? payload.items as GalleryItem[] : [])
      setSavedAp(normAppearance(payload.appearance))
      setDraftAp(normAppearance(payload.appearance))
      setMediaStorage(readMediaStorage(payload.media_storage))
      if (payload.cleanup_pending) {
        toast.error(t("panel.appearance.cleanupPending", { defaultValue: "仍有媒体数据等待清理，请关闭占用文件的程序后重试。" }))
        return false
      }
      if (payload.storage_reclaim_pending) {
        toast.info(t("panel.appearance.storageReclaimPending", { defaultValue: "媒体条目已清除；数据库文件大小可能暂时不变。" }))
      }
      toast.success(t("panel.appearance.mediaCleared", { defaultValue: "壁纸与缓存已清空" }))
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      try { await refreshMediaSnapshot() } catch {}
      return false
    } finally {
      mediaClearing.current = false
      setApBusy(false)
    }
  }

  const updateSettingsAction = actions.find((a) => a.id === "update_settings") as HostedAction | undefined
  const setAnchor = actions.find((a) => a.id === "set_anchor") as HostedAction | undefined
  const advance = actions.find((a) => a.id === "advance_days") as HostedAction | undefined
  const toggle = actions.find((a) => a.id === "toggle") as HostedAction | undefined
  const resetAll = actions.find((a) => a.id === "reset_all") as HostedAction | undefined
  const liftMoodAction = actions.find((a) => a.id === "lift_mood") as HostedAction | undefined
  const pruneLanlan = actions.find((a) => a.id === "prune_lanlan") as HostedAction | undefined

  // kit 的 useConfirm 默认按钮文案是英文 Confirm/Cancel，必须显式传本地化文案
  const confirmLabels = {
    confirmLabel: t("panel.confirm", { defaultValue: "确认" }),
    cancelLabel: t("panel.cancel", { defaultValue: "取消" }),
  }

  // 设置落盘（周期/注入/情绪/日记/通用同一入口）：锚点未设与动作缺失都当场抛错，
  // 由调用方翻成 toast
  async function postSettings() {
    if (!updateSettingsAction) {
      throw new Error(t("panel.errors.actionUnavailable", { defaultValue: "操作不可用（插件可能未运行）" }))
    }
    if (!DATE_RE.test(String(state.anchor_date || ""))) {
      throw new Error(t("panel.errors.setAnchorFirst", { defaultValue: "请先设置周期起点，再开启模拟" }))
    }
    const base = formBaseline.current as Record<string, any>
    const current = form as Record<string, any>
    const dirty: Record<string, any> = {}
    for (const key of Object.keys(current)) {
      const now = current[key]
      const was = base[key]
      // 数组/对象按序列化比（phase_openers 这类是列表），标量按值比
      const same = now === was || (typeof now === "object" && JSON.stringify(now) === JSON.stringify(was))
      if (!same) dirty[key] = now
    }
    // 一个都没改：不发这一趟，更不写存档（写了就等于替用户把出厂默认钉死）
    if (Object.keys(dirty).length === 0) return
    const result = await props.api.call("update_settings", dirty)
    // 用入口返回的最新快照立即回填表单，不依赖 context 重取
    const snap = (result && typeof result === "object" && (result as Record<string, any>).enabled !== undefined)
      ? (result as Partial<Settings>)
      : null
    if (snap) {
      syncForm({ ...settings, ...snap })
    }
  }

  async function saveSettings() {
    try {
      await postSettings()
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

  // 设置页的统一保存（1.3.2）：这页既能改通用设置、又能改面板外观，
  // 而外观卡不再有自家保存钮——一趟把两摊待存内容都落盘。
  // 两半各自独立成败：设置被锚点闸挡下时外观照样存住（卡内读数随之转"已同步"）；
  // 外观没动过就不发第二趟，省一次无谓写盘。失败只报第一条（两半同因居多），
  // 没存住的那半由各自的可见状态兜底：表单未回填、外观读数仍亮着"有未保存的调整"
  async function saveSettingsPage() {
    if (savingAll) return
    setSavingAll(true)
    const apDirty = !appearanceEquals(draftAp, savedAp)
    let failure: unknown = null
    try {
      await postSettings()
    } catch (err) {
      failure = err
    }
    if (apDirty) {
      try {
        await postAppearanceDraft()
      } catch (err) {
        if (!failure) failure = err
      }
    }
    try {
      await props.api.refresh()
    } catch {
      // 回读失败不影响落盘结论
    }
    if (failure) {
      toast.error(errorText(failure, t))
    } else {
      toast.success(t("panel.messages.saved", { defaultValue: "设置已保存" }))
    }
    setSavingAll(false)
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
      message: t("actions.toggle.confirm", { defaultValue: "确定切换模拟总开关吗？" }),
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

  async function onDeleteFragment(ts: string) {
    const ok = await confirmDialog({
      title: t("actions.delete_diary_item.label", { defaultValue: "删除这条片段" }),
      message: t("actions.delete_diary_item.confirm", { defaultValue: "确定删除这条对话片段吗？删除后无法恢复。" }),
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
    if (journalSubmitLock.current) return
    journalSubmitLock.current = true
    setJournalSubmitting(true)
    try {
      const payload = unwrapCallResult(await props.api.call("invite_journal", {}))
      await props.api.refresh()
      const r = (payload || {}) as Record<string, any>
      if (r.accepted) {
        toast.info(t("panel.review.accepted", { defaultValue: "已加入生成队列，完成后会显示在这里" }))
      } else if (r.reason === "already_writing") {
        toast.info(t("panel.review.alreadyWriting", { defaultValue: "已有内容正在生成，请等待完成" }))
      } else if (r.reason === "not_enough_material") {
        toast.info(t("panel.journal.notEnoughMaterial", { defaultValue: "暂无可写素材，请先聊一轮，或积累新的时光日记记录。" }))
      } else if (r.accepted === false) {
        toast.error(diaryWriteError(r, t))
      } else if (r.invited === false && r.mode === "failed") {
        toast.error(t("panel.journal.inviteFailed", { defaultValue: "邀请发送失败，消息通道可能正忙或不可用。请稍后重试。" }))
      } else if (r.invited === false) {
        toast.info(t("panel.journal.inviteOff", { defaultValue: "个人日记开关未开启，邀请未发送" }))
      } else if (r.mode === "respond") {
        toast.success(t("panel.journal.invitedRespond", { defaultValue: "写作请求已发送，是否写作由角色决定" }))
      } else {
        toast.success(t("panel.journal.invitedQuiet", { defaultValue: "最近已发送过邀请，本次改为后台提醒" }))
      }
    } catch (err) {
      toast.error(errorText(err, t))
    } finally {
      journalSubmitLock.current = false
      setJournalSubmitting(false)
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
    if (reviewSubmitLock.current) return
    reviewSubmitLock.current = true
    setReviewSubmitting(true)
    try {
      // 受理式入口（1.2.3）：后端秒回"已开始写"，真正的成文在后台一拍内起跑；
      // 成败结论经 dashboard review_brief.last_result 由下方 effect 弹完成 toast。
      // i18n 契约（1.3.0 第九轮）：后端只回 reason 码与数据字段，文案全在本地按码分支
      const payload = unwrapCallResult(await props.api.call("write_review_now", {}))
      await props.api.refresh()
      const r = (payload || {}) as Record<string, any>
      if (r.accepted) {
        toast.info(t("panel.review.accepted", { defaultValue: "已加入生成队列，完成后会显示在这里" }))
      } else if (r.reason === "already_writing") {
        toast.info(t("panel.review.alreadyWriting", { defaultValue: "已有内容正在生成，请等待完成" }))
      } else if (r.reason === "not_enough_material") {
        // 数字走插值不进文案（后端只回 turns/min_turns 字段）
        toast.info(t("panel.review.notEnoughMaterial", {
          defaultValue: "素材不足：目前 {turns} 轮，至少需要 {min_turns} 轮互动",
          turns: String(r.turns ?? 0),
          min_turns: String(r.min_turns ?? 10),
        }))
      } else {
        toast.error(diaryWriteError(r, t))
      }
    } catch (err) {
      toast.error(errorText(err, t))
    } finally {
      reviewSubmitLock.current = false
      setReviewSubmitting(false)
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
      toast.success(t("panel.review.written", { defaultValue: "记录已生成" }))
    } else {
      toast.error(diaryWriteError(result, t))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.review_brief && state.review_brief.last_result && state.review_brief.last_result.ts, state.lanlan])

  const journalResultSeen = useRef<number>(0)
  const journalResultRole = useRef<string | null>(null)
  useEffect(() => {
    const result = state.journal_write && state.journal_write.last_result
    const role = state.lanlan || ""
    if (journalResultRole.current !== role) {
      journalResultRole.current = role
      journalResultSeen.current = (result && result.ts) || 0
      return
    }
    if (!result || !result.ts || journalResultSeen.current === result.ts) return
    journalResultSeen.current = result.ts
    if (result.written) toast.success(t("panel.review.written", { defaultValue: "记录已生成" }))
    else toast.error(diaryWriteError(result, t))
  }, [state.journal_write && state.journal_write.last_result && state.journal_write.last_result.ts, state.lanlan])

  async function onEndMood() {
    if (moodEndLock.current || !mood.active || !liftMoodAction) return
    moodEndLock.current = true
    const expected = { ...currentMoodRef.current }
    try {
      const ok = await confirmDialog({
        title: t("actions.liftMood.label", { defaultValue: "结束当前状态" }),
        message: t("actions.liftMood.confirm", { defaultValue: "结束当前角色的情绪工具状态？心情观察与变化记录会保留，这不代表角色已经释怀。" }),
        ...confirmLabels,
      })
      if (!ok) return
      if (expected.lanlan !== currentMoodRef.current.lanlan || expected.startedAt !== currentMoodRef.current.startedAt) {
        toast.error(t("panel.errors.moodChanged", { defaultValue: "角色或情绪状态已变化，请查看最新状态后重试。" }))
        return
      }
      setMoodEnding(true)
      await props.api.call("lift_mood", {
        expected_lanlan: expected.lanlan,
        expected_started_at: expected.startedAt,
      })
      await props.api.refresh()
    } catch (err) {
      toast.error(errorText(err, t))
    } finally {
      setMoodEnding(false)
      moodEndLock.current = false
    }
  }

  // 确认文案与后端 action 共用翻译，错误码经 errorText 展示。
  async function onResetAll() {
    if (resetLock.current) return
    resetLock.current = true
    const expectedLanlan = state.lanlan || ""
    try {
      const ok = await confirmDialog({
        title: t("actions.reset.label", { defaultValue: "重置状态与记录" }),
        message: t("actions.reset.confirm", { defaultValue: "确定清空当前角色的状态与记录吗？快进天数、情绪状态、三本日记（时光日记、个人日记、我的日记）、全部归档、累计素材和相处统计都会清空，待写任务会取消，且无法恢复。模拟开关恢复默认设置；周期起点、参数、全局设置、壁纸和其他角色的数据保留。" }),
        tone: "danger",
        ...confirmLabels,
      })
      if (!ok) return
      setResetting(true)
      unwrapCallResult(await props.api.call("reset_all", { expected_lanlan: expectedLanlan }))
      invalidateRecords()
      await props.api.refresh()
      toast.success(t("panel.settings.resetDone"))
    } catch (err) {
      toast.error(errorText(err, t))
    } finally {
      setResetting(false)
      resetLock.current = false
    }
  }

  async function onLoadStats(month?: string, year?: string) {
    const generation = ++statsGeneration.current
    setStatsLoading(true)
    try {
      const args: Record<string, string> = {}
      if (month) args.month = month
      if (year) args.year = year
      const payload = unwrapCallResult(await props.api.call("get_stats", args))
      if (!panelAlive.current || generation !== statsGeneration.current) return
      const r = (payload || {}) as Record<string, any>
      setHeatData((r.heatmap || {}) as Heatmap)
      setMonthData((r.month || null) as MonthReport | null)
      setMonthList(Array.isArray(r.month_available) ? (r.month_available as string[]) : [])
    } catch (err) {
      console.warn("[forever_companion] load stats failed:", err)
    } finally {
      if (generation === statsGeneration.current) setStatsLoading(false)
    }
  }

  // 切角色时旧统计立即失效：清到空态，避免上一个角色的热力图残留展示
  useEffect(() => {
    statsGeneration.current += 1
    setStatsLoading(false)
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
        toast.info(t("panel.features.reverted", { defaultValue: "此功能在设置中已关闭，或所需功能尚未开启。请先完成相关设置。" }))
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
          ? t("panel.features.hideToolsOn", { defaultValue: "隐藏对所有角色都不生效的工具" })
          : t("panel.features.hideToolsOff", { defaultValue: "保留工具，已关闭的功能不执行" }),
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
      message: t("actions.prune_lanlan.confirm", { defaultValue: "确定清除这个角色的残留数据吗？周期、情绪、日记和旧版周记都会删除，且无法恢复。" }),
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
      title: t("actions.set_anchor.label", { defaultValue: "设置周期起点" }),
      message: t("actions.set_anchor.confirm", { defaultValue: "确定设置新的周期起点吗？快进天数会清零，并重新计算当前阶段。" }),
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
        toast.success(t("panel.birthday.saved", { defaultValue: "生日已保存" }))
      } else {
        toast.success(t("panel.birthday.cleared", { defaultValue: "已清除生日设置" }))
      }
      return true
    } catch (err) {
      toast.error(errorText(err, t))
      return false
    }
  }

  const VALID_TABS = ["overview", "calendar", "diary", "moment", "mood", "features", "cycle", "settings"]
  const activeTab = VALID_TABS.indexOf(tab) >= 0 ? tab : "overview"

  // 面板始终跟随宿主当前角色（5 秒轮询自动跟上切卡），lanlan_list 提供全部已知角色的状态摘要
  const lanlanKnown = (state.lanlan_list || []).filter((item) => item && item.name)

  // 页签图标用纯 CSS 线框图标（hosted 运行时不支持 SVG，也不用 emoji）
  const tabs = [
    { id: "overview", label: t("panel.tab.overview", { defaultValue: "总览" }) },
    { id: "calendar", label: t("panel.tab.calendar", { defaultValue: "日历" }) },
    { id: "diary", label: t("panel.tab.diary", { defaultValue: "日记" }) },
    { id: "moment", label: t("panel.tab.moment", { defaultValue: "时光" }) },
    { id: "mood", label: t("panel.tab.mood", { defaultValue: "情绪" }) },
    { id: "features", label: t("panel.tab.features", { defaultValue: "功能" }) },
    { id: "cycle", label: t("panel.tab.cycle", { defaultValue: "周期" }) },
    { id: "settings", label: t("panel.tab.settings", { defaultValue: "设置" }) },
  ]

  // 参数保持 draft 实时预览；图片准备期间只暂留仍在图库中的旧图。
  const previousBgExists = galleryItems.some((item) => String(item.id || "") === readyBg.id)
  const displayedBg = readyBg.media?.loading && previousReadyBg.current ? previousReadyBg.current : readyBg
  const bgDataUrl = draftAp.bg_id && (readyBg.id === draftAp.bg_id || previousBgExists)
    ? displayedBg.dataUrl
    : ""
  const themeColors = bgDataUrl && displayedBg.theme ? displayedBg.theme : DEFAULT_THEME_COLORS
  // 背景材质只在壁纸就绪时应用，无壁纸保持默认中性卡片。
  const surfaceAppearance = bgDataUrl ? draftAp : { ...draftAp, glass: 0, card_alpha: 100 }
  const rootStyle = Object.assign({ display: "contents" }, appearanceVars(surfaceAppearance), themeColorVars(themeColors)) as Record<string, string>

  if (state.backup_recovery_required) return (
    <Page>
      <div className="tm-settings">
        <style>{PANEL_STYLES}</style>
        <BackupPanel t={t} api={props.api} available={true} disabled={false} recoveryRequired={true} onRestored={onBackupRestored} />
      </div>
    </Page>
  )

  return (
    <Page className={bgDataUrl ? "tm-has-bg" : ""}>
      <div key="root" className="tm-appearance-root" style={rootStyle}>
      <style key="styles">{PANEL_STYLES}</style>
      <style key="demo-styles">{CAPDEMO_STYLES}</style>

      {bgDataUrl ? (
        <div key="bg" className="tm-bg" aria-hidden="true">
          <div className="tm-bg-image" style={bgLayerStyle(readyBg.media?.kind === "video" && draftAp.fill === "repeat"
            ? { ...draftAp, fill: "cover" } : draftAp, bgDataUrl)} />
          {readyBg.media?.kind === "video" && readyBg.media.url ? (
            <BackgroundVideo media={readyBg.media} appearance={draftAp}
              onReady={() => backgroundPlaybackReady(readyBg.id, readyBg.media!)}
              onError={() => backgroundPlaybackError(readyBg.id, readyBg.media!)} />
          ) : null}
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
                <Alert tone="warning" message={t("panel.errors.setAnchorFirst", { defaultValue: "请先设置周期起点，再开启模拟" })} />
              ) : null}
              <CycleSettingsCard t={t} settings={settings} form={form} updateForm={updateForm} lanlan={state.lanlan} />
              <InjectSettingsCard t={t} form={form} updateForm={updateForm} />
              <SaveBar t={t} canSave={!!updateSettingsAction} onSave={saveSettings} />
            </div>
          ) : null}
          {activeTab === "mood" ? (
            <div className="tm-pane">
              <MoodSettingsCard
                key={state.lanlan || ""}
                t={t} form={form} updateForm={updateForm}
                mood={mood} enabled={status.enabled !== false}
                canEnd={!!liftMoodAction} ending={moodEnding}
                onEnd={onEndMood}
              />
              <EmotionSenseSettingsCard t={t} form={form} updateForm={updateForm} />
              <ChannelSettingsCard
                t={t}
                form={form}
                updateForm={updateForm}
                toneSlotOptions={state.tone_slot_options}
                channelStatus={state.channel_status}
                agentUsage={state.agent_tier_usage}
              />
              <SaveBar t={t} canSave={!!updateSettingsAction} onSave={saveSettings} />
            </div>
          ) : null}
          {activeTab === "diary" ? (
            <DiaryPane
              key={`${state.lanlan || ""}:${recordsRevision}`}
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
              onDeleteFragment={onDeleteFragment}
              onLoadJournal={onLoadJournal}
              onLoadJournalArchive={onLoadJournalArchive}

              onLoadMoreDiary={onLoadMoreDiary}
              onInviteJournal={onInviteJournal}
              journalWrite={state.journal_write}
              journalSubmitting={journalSubmitting}
              reviewSubmitting={reviewSubmitting}
              onLoadReview={onLoadReview}
              onLoadReviewArchive={onLoadReviewArchive}
              onWriteReviewNow={onWriteReviewNow}
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
              resetting={resetting}
              lanlanList={lanlanKnown}
              lanlan={state.lanlan}
              canPrune={!!pruneLanlan}
              onPruneLanlan={onPruneLanlan}
              onReopenGuide={reopenGuide}
              api={props.api}
              canBackup={actions.some((action) => action.id === "data_backup")}
              backupDisabled={savingAll || apBusy || journalSubmitting || reviewSubmitting || resetting}
              onRestored={onBackupRestored}
              footer={<SaveBar t={t} canSave={!!updateSettingsAction} saving={savingAll} onSave={saveSettingsPage} />}
            >
              <AppearanceCard
                key={`appearance-${mediaReset}`}
                t={t}
                items={galleryItems}
                draft={draftAp}
                saved={savedAp}
                theme={themeColors}
                themeFromWallpaper={!!bgDataUrl && readyBg.theme !== DEFAULT_THEME_COLORS}
                uploading={apBusy}
                videoProgress={videoProgress}
                backgroundError={backgroundError}
                backgroundProgress={backgroundProgress?.id === draftAp.bg_id ? backgroundProgress : null}
                onCancelUpload={() => { if (uploadTask.current) cancelMediaTask(uploadTask.current) }}
                onDraft={onDraftChange}
                onRevert={() => { setDraftAp(savedAp) }}
                onAdd={addImage}
                onAddVideo={addVideo}
                onAskRemove={(item: GalleryItem) => removeImage(String(item.id || ""))}
                mediaStorage={mediaStorage}
                onUpdateVideoPolicy={updateVideoPolicy}
                onClearMedia={clearMedia}
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
