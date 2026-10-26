// 面板外观：分类壁纸库、导入、背景调节与媒体存储。
// 图库（入册/删除/缩略图）由 panel.tsx 经 gallery_* 入口即时落盘；
// "用哪张 + 十项调节"是 draft 参数——实时预览，落盘交给设置页底部那枚「保存设置」
// （1.3.2：卡内不再自设保存钮，一页两个"保存"会让人以为外观改丢了）。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前；不支持 <svg>（九宫格/占位图纯 CSS）
import { Alert, Button, Field, ImageUpload, Progress, SegmentedControl, Slider } from "@neko/plugin-ui"
import { useEffect, useRef, useState } from "@neko/plugin-ui"
import type { Appearance, GalleryItem, MediaStorage, TFunc } from "./types"
import { APPEARANCE_POSITIONS, appearanceEquals, compressImageDataUrl, errorText, navigateTabs } from "./utils"
import { readFileDataUrl, VIDEO_MAX_BYTES } from "./media"
import { scanWallpaperFiles } from "./wallpaper_import"
import type { WallpaperCandidate, WallpaperScan } from "./wallpaper_import"

// 单图原始字节上限（原画质档）：base64 后 ≈5.9M 字符，卡在后端 6,000,000 字符闸内
const RAW_MAX_BYTES = 4400000
// 自动压缩档上传闸：反正会重编码，放宽到 20MB 让大图也能进（失败自动回退原样）
const AUTO_MAX_BYTES = 20000000

export function AppearanceCard(props: {
  key?: string
  t: TFunc
  items: GalleryItem[]
  draft: Appearance
  saved: Appearance
  theme?: { primary: string; secondary: string }
  themeFromWallpaper?: boolean
  uploading: boolean
  videoProgress: { value: number; stage: string } | null
  backgroundError: string
  backgroundProgress?: { value: number; stage: string } | null
  onCancelUpload: () => void
  onDraft: (patch: Record<string, string | number | boolean>) => void
  onRevert: () => void
  onAdd: (dataUrl: string, thumb: string, name: string) => Promise<boolean>
  onAddVideo: (file: File, name: string) => Promise<boolean>
  onAskRemove: (item: GalleryItem) => void
  mediaStorage: MediaStorage | null
  onUpdateVideoPolicy: (singleLimit: number, totalLimit: number) => Promise<boolean>
  onClearMedia: () => Promise<boolean>
}) {
  const { t, items, draft, saved, theme, themeFromWallpaper, uploading, onDraft, onRevert, onAdd, onAskRemove, mediaStorage } = props
  // 压缩档只影响"怎么入册"，不是面板状态，留在卡内；上传错误本地化同前
  const [quality, setQuality] = useState<string>("auto")
  const [mediaKind, setMediaKind] = useState("image")
  const [importKind, setImportKind] = useState("image")
  const initialFilter = useRef(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [scan, setScan] = useState<WallpaperScan | null>(null)
  const [scanning, setScanning] = useState(false)
  const [localBusy, setLocalBusy] = useState(false)
  const [singleLimit, setSingleLimit] = useState(256)
  const [totalLimit, setTotalLimit] = useState(2048)
  const [imported, setImported] = useState<Record<string, boolean>>({})
  const scanToken = useRef<object | null>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false; scanToken.current = null }, [])
  const dirty = !appearanceEquals(draft, saved)
  const hasBg = draft.bg_id !== ""
  const videoSelected = items.some((item) => item.id === draft.bg_id && item.kind === "video")
  const busy = uploading || localBusy
  const imageCount = items.filter((item) => item.kind !== "video").length
  const videoCount = items.length - imageCount
  const visibleItems = items.filter((item) => (item.kind === "video" ? "video" : "image") === mediaKind)
  const noImage = imageCount === 0
  const mediaTabs = [
    { id: "image", label: t("panel.appearance.images", { defaultValue: "图片" }), count: imageCount },
    { id: "video", label: t("panel.appearance.videos", { defaultValue: "视频" }), count: videoCount },
  ]
  useEffect(() => {
    if (initialFilter.current || !items.length) return
    initialFilter.current = true
    setMediaKind(videoSelected ? "video" : "image")
  }, [items, draft.bg_id])
  useEffect(() => {
    if (!mediaStorage) return
    setSingleLimit(Math.round(mediaStorage.single_limit_bytes / 1048576))
    setTotalLimit(Math.round(mediaStorage.total_limit_bytes / 1048576))
  }, [mediaStorage?.single_limit_bytes, mediaStorage?.total_limit_bytes])
  const noWallpaperLabel = t("panel.appearance.noWallpaper", { defaultValue: "不用壁纸" })
  const appliedLabel = t("panel.appearance.inUse", { defaultValue: "已应用" })
  const previewingLabel = t("panel.appearance.previewing", { defaultValue: "预览中" })
  const deleteLabel = t("panel.appearance.deleteTile", { defaultValue: "从图库删除" })
  const noneApplied = saved.bg_id === ""
  const noneClass = "tm-gallery-tile tm-gallery-none"
    + (!hasBg ? " tm-gallery-active" : "")
    + (noneApplied ? " tm-gallery-applied" : "")
    + (!hasBg && !noneApplied ? " tm-gallery-preview" : "")

  function fillOptions() {
    const options = [
      { value: "cover", label: t("panel.appearance.fill.cover", { defaultValue: "铺满裁剪" }) },
      { value: "contain", label: t("panel.appearance.fill.contain", { defaultValue: "完整显示" }) },
      { value: "repeat", label: t("panel.appearance.fill.repeat", { defaultValue: "平铺" }) },
      { value: "stretch", label: t("panel.appearance.fill.stretch", { defaultValue: "拉伸" }) },
    ]
    return videoSelected ? options.filter((option) => option.value !== "repeat") : options
  }

  function posLabel(value: string): string {
    const key = "panel.appearance.pos." + value.replace(" ", "-")
    return t(key, { defaultValue: value })
  }

  // 九宫格小圆点的摆放：字符串里含 left/right/top/bottom 即对应贴边
  function posCellStyle(value: string): Record<string, string> {
    const style: Record<string, string> = {}
    style.justifySelf = value.indexOf("left") >= 0 ? "start" : value.indexOf("right") >= 0 ? "end" : "center"
    style.alignSelf = value.indexOf("top") >= 0 ? "start" : value.indexOf("bottom") >= 0 ? "end" : "center"
    return style
  }

  async function handleUpload(artifact: any): Promise<boolean> {
    if (busy) return false
    const url = String((artifact && artifact.dataUrl) || "")
    const mime = String((artifact && artifact.mime) || "")
    const name = String((artifact && (artifact.filename || artifact.name)) || "")
    setUploadError(null)
    if (!url) return false
    setLocalBusy(true)
    try {
      const res = await compressImageDataUrl(url, mime, quality)
      if (res.dataUrl.length > 6000000) {
        setUploadError(t("panel.appearance.errorTooLargeShort", { defaultValue: "图片超过大小限制，请压缩到 4MB 以内" }))
        return false
      }
      if (!alive.current) return false
      const success = await onAdd(res.dataUrl, res.thumb, name)
      if (alive.current && success) {
        initialFilter.current = true
        setMediaKind("image")
      }
      return success
    } catch (error) {
      if (alive.current && mime === "image/svg+xml") {
        const success = await onAdd(url, "", name)
        if (alive.current && success) { initialFilter.current = true; setMediaKind("image") }
        return success
      }
      if (alive.current) setUploadError(errorText("wallpaper_image_decode_failed", t))
      return false
    } finally {
      if (alive.current) setLocalBusy(false)
    }
  }

  async function chooseVideo(event: any) {
    const file = event.currentTarget.files?.[0] as File | undefined
    event.currentTarget.value = ""
    if (!file || busy) return
    setUploadError(null)
    const success = await props.onAddVideo(file, file.name)
    if (alive.current && success) { initialFilter.current = true; setMediaKind("video") }
  }

  async function chooseDirectory(event: any) {
    const files = Array.from(event.currentTarget.files || []) as File[]
    event.currentTarget.value = ""
    if (!files.length || busy) return
    const token = {}
    scanToken.current = token
    setScanning(true)
    setUploadError(null)
    setScan(null)
    setImported({})
    try {
      const result = await scanWallpaperFiles(files, () => scanToken.current !== token || !alive.current)
      if (scanToken.current === token && alive.current) setScan(result)
    } catch (error) {
      if (scanToken.current === token && alive.current) setUploadError(errorText(error, t))
    } finally {
      if (scanToken.current === token && alive.current) setScanning(false)
    }
  }

  async function importCandidate(candidate: WallpaperCandidate) {
    if (busy || imported[candidate.key]) return
    setUploadError(null)
    let success = false
    if (candidate.kind === "video") {
      success = await props.onAddVideo(candidate.file, candidate.title)
    } else {
      if (candidate.file.size > (quality === "raw" ? RAW_MAX_BYTES : AUTO_MAX_BYTES)) {
        setUploadError(t("panel.appearance.errorTooLargeShort", { defaultValue: "图片超过大小限制，请压缩到 4MB 以内" }))
        return
      }
      try {
        const dataUrl = await readFileDataUrl(candidate.file)
        if (!alive.current) return
        const ext = candidate.file.name.split(".").pop()?.toLowerCase()
        const mime = candidate.file.type || (ext === "jpg" || ext === "jpeg" ? "image/jpeg" : `image/${ext}`)
        success = await handleUpload({ dataUrl, mime, name: candidate.title })
      } catch (error) {
        if (alive.current) setUploadError(errorText(error, t))
      }
    }
    if (alive.current && success) {
      setImported((previous) => ({ ...previous, [candidate.key]: true }))
      initialFilter.current = true
      setMediaKind(candidate.kind)
    }
  }

  return (
    <section className="tm-settings-section">
      <h3>{t("panel.appearance.title", { defaultValue: "面板外观" })}</h3>
      <div className="tm-appearance">
        <div className="tm-wallpaper-current">
          <span>{t("panel.appearance.current", { defaultValue: "当前壁纸" })}</span>
          <strong>{items.find((item) => item.id === draft.bg_id)?.name || (hasBg
            ? t("panel.appearance.legacyName", { defaultValue: "旧的背景图" }) : noWallpaperLabel)}</strong>
        </div>

        {/* —— 图库：网格选择 + 导入 + 删除（即时落盘，不经保存钮） —— */}
        <div className="tm-media-tabs" role="tablist" aria-label={t("panel.appearance.library", { defaultValue: "壁纸库" })}>
          {mediaTabs.map((item) => (
            <button key={item.id} type="button" role="tab" className="tm-media-tab"
              id={`tm-media-tab-${item.id}`} aria-selected={mediaKind === item.id ? "true" : "false"}
              aria-controls="tm-media-gallery" tabIndex={mediaKind === item.id ? 0 : -1}
              onKeyDown={navigateTabs} onClick={() => { initialFilter.current = true; setMediaKind(item.id) }}>
              <span>{item.label}</span><span className="tm-media-count">{item.count}</span>
            </button>
          ))}
        </div>
        <div id="tm-media-gallery" role="tabpanel" aria-labelledby={`tm-media-tab-${mediaKind}`} tabIndex={0}>
          <div className="tm-gallery">
            <div
              className={noneClass}
            >
              <button
                type="button"
                className="tm-gallery-pick"
                title={noWallpaperLabel}
                aria-label={noWallpaperLabel}
                aria-pressed={!hasBg ? "true" : "false"}
                onClick={() => { onDraft({ bg_id: "" }) }}
              >
                <span className="tm-gallery-none-mark" aria-hidden="true">×</span>
                <span className="tm-gallery-name">{noWallpaperLabel}</span>
                {!hasBg && !noneApplied ? <span className="tm-gallery-use">{previewingLabel}</span> : noneApplied ? <span className="tm-gallery-use">{appliedLabel}</span> : null}
              </button>
            </div>
            {visibleItems.map((item) => {
              const id = String(item.id || "")
              const active = id !== "" && id === draft.bg_id
              const applied = id !== "" && id === saved.bg_id
              const previewing = active && !applied
              const thumb = String(item.thumb || "")
              const tileClass = "tm-gallery-tile"
                + (active ? " tm-gallery-active" : "")
                + (applied ? " tm-gallery-applied" : "")
                + (previewing ? " tm-gallery-preview" : "")
              const label = String(item.name || "") || t("panel.appearance.legacyName", { defaultValue: "旧的背景图" })
              const deleteTitle = `${deleteLabel}: ${label}`
              return (
                <div
                  key={id}
                  data-item-id={id}
                  data-media-kind={item.kind === "video" ? "video" : "image"}
                  className={thumb ? tileClass : `${tileClass} tm-gallery-thumbless`}
                >
                  <button
                    type="button"
                    className="tm-gallery-pick"
                    style={thumb ? { backgroundImage: `url("${thumb}")` } : undefined}
                    title={label}
                    aria-label={label}
                    aria-pressed={active ? "true" : "false"}
                    onClick={() => { onDraft({ bg_id: id }) }}
                  >
                    <span className="tm-gallery-name">{label}</span>
                    {item.kind === "video" ? <span className="tm-gallery-video-mark" title={t("panel.appearance.videoBadge")} aria-label={t("panel.appearance.videoBadge")} /> : null}
                    {previewing ? <span className="tm-gallery-use">{previewingLabel}</span> : applied ? <span className="tm-gallery-use">{appliedLabel}</span> : null}
                  </button>
                  <button
                    type="button"
                    className="tm-gallery-del"
                    title={deleteTitle}
                    aria-label={deleteTitle}
                    onClick={() => { onAskRemove(item) }}
                  ><span aria-hidden="true">×</span></button>
                </div>
              )
            })}
          </div>
          {visibleItems.length === 0 ? <div className="tm-gallery-empty" role="status">
            {mediaKind === "video"
              ? t("panel.appearance.emptyVideos", { defaultValue: "还没有视频壁纸" })
              : t("panel.appearance.emptyImages", { defaultValue: "还没有图片壁纸" })}
          </div> : null}
        </div>

        {props.backgroundError ? <Alert tone="warning" message={props.backgroundError} /> : null}
        {uploadError ? <Alert tone="danger" message={uploadError} /> : null}
        <details className="tm-settings-disclosure tm-media-add">
          <summary>{t("panel.appearance.addMedia", { defaultValue: "导入壁纸" })}</summary>
          <div className="tm-settings-disclosure-body">
          <SegmentedControl value={importKind} options={[
            { value: "image", label: t("panel.appearance.images", { defaultValue: "图片" }) },
            { value: "video", label: t("panel.appearance.videos", { defaultValue: "视频" }) },
            { value: "directory", label: "Wallpaper Engine" },
          ]} onChange={(value: any) => setImportKind(String(value))} />
        <div className={busy ? "tm-media-import tm-media-import-busy" : "tm-media-import"}>
        <div className="tm-media-import-fields" hidden={importKind !== "image"}>
        <Field
          label={t("panel.appearance.modeLabel", { defaultValue: "导入方式" })}
          help={t("panel.appearance.modeHelp", { defaultValue: "自动压缩将图片长边缩至最多 2560 像素，并重新编码。GIF / SVG 保留原文件。" })}
        >
          <SegmentedControl
            value={quality}
            options={[
              { value: "auto", label: t("panel.appearance.modeAuto", { defaultValue: "自动压缩（推荐）" }) },
              { value: "raw", label: t("panel.appearance.modeRaw", { defaultValue: "原画质" }) },
            ]}
            onChange={(v: any) => { setQuality(String(v)) }}
          />
        </Field>
        <Field
          label={t("panel.appearance.upload", { defaultValue: "导入图片" })}
          help={noImage ? t("panel.appearance.galleryEmptyHint", { defaultValue: "暂无图片" }) : t("panel.appearance.uploadHelp", { defaultValue: "支持 PNG / JPG / WebP / GIF / SVG。原画质文件不能超过 4MB。" })}
        >
          <ImageUpload
            value={undefined}
            placeholder={t("panel.appearance.uploadPlaceholder", { defaultValue: "点击或拖拽图片到这里" })}
            accept="image/*"
            maxBytes={quality === "raw" ? RAW_MAX_BYTES : AUTO_MAX_BYTES}
            onChange={handleUpload}
            onError={(error) => { setUploadError(localizeUploadError(t, error)) }}
          />
        </Field>
        </div>
        <div hidden={importKind !== "video"}>
        <Field label={t("panel.appearance.videoImport")} help={t("panel.appearance.videoLimit", {
          n: mediaStorage ? Math.round(mediaStorage.single_limit_bytes / 1048576) : VIDEO_MAX_BYTES / 1048576,
          total: mediaStorage ? Math.round(mediaStorage.total_limit_bytes / 1048576) : 128,
        })}>
          <input type="file" className="tm-wallpaper-video-input" accept=".mp4,.webm,video/mp4,video/webm"
            disabled={busy || scanning} aria-label={t("panel.appearance.videoImport")} onChange={chooseVideo} />
        </Field>
        </div>
        <div hidden={importKind !== "directory"}>
        <Field label={t("panel.appearance.directoryImport")} help={t("panel.appearance.directoryLimit")}>
          <input type="file" className="tm-wallpaper-directory-input" webkitdirectory="" multiple
            disabled={busy || scanning} aria-label={t("panel.appearance.directoryImport")} onChange={chooseDirectory} />
        </Field>
        </div>
        </div>
        </div>
        </details>
        {props.videoProgress ? (
          <div className="tm-video-progress" role="status" aria-live="polite">
            <Progress value={props.videoProgress.value}
              label={t(`panel.appearance.videoStage.${props.videoProgress.stage}`, { n: props.videoProgress.value })} />
            <Button disabled={props.videoProgress.stage === "saving" || props.videoProgress.stage === "done"}
              onClick={props.onCancelUpload}>{t("panel.cancel", { defaultValue: "取消" })}</Button>
          </div>
        ) : uploading || localBusy ? (
          <div className="tm-derived">{t("panel.appearance.adding", { defaultValue: "处理图片中…" })}</div>
        ) : null}
        {scanning ? <div className="tm-derived" role="status">{t("panel.appearance.directoryScanning")}</div> : null}
        {props.backgroundProgress ? (
          <div className="tm-video-loading" role="status" aria-live="polite">
            <Progress value={props.backgroundProgress.value}
              label={props.backgroundProgress.stage === "downloading"
                ? t("panel.appearance.videoLoading", { n: props.backgroundProgress.value })
                : t("panel.appearance.videoPreparing")} />
          </div>
        ) : null}
        {scan ? (
          <div className="tm-wallpaper-candidates">
            <div className="tm-derived" role="status">
              {t("panel.appearance.directorySummary", { n: scan.candidates.length, skipped: scan.unsupported, invalid: scan.invalid })}
            </div>
            {scan.candidates.map((candidate) => (
              <div key={candidate.key} className="tm-wallpaper-candidate">
                <div className="tm-wallpaper-candidate-name" title={candidate.key}>
                  <strong>{candidate.title}</strong>
                  <span>{candidate.file.name}</span>
                </div>
                <Button disabled={busy || !!imported[candidate.key]} onClick={() => { void importCandidate(candidate) }}>
                  {imported[candidate.key] ? t("panel.appearance.directoryImported") : t("panel.appearance.directoryAdd")}
                </Button>
              </div>
            ))}
          </div>
        ) : null}

        {theme ? (
          <Field label={t("panel.appearance.themeColors", { defaultValue: "主题色联动" })}>
            <div className="tm-theme-colors" data-source={themeFromWallpaper ? "wallpaper" : "default"}>
              <span className="tm-theme-source">{themeFromWallpaper
                ? t("panel.appearance.themeWallpaper", { defaultValue: "壁纸配色" })
                : t("panel.appearance.themeDefault", { defaultValue: "默认浅蓝" })}</span>
              <span className="tm-theme-color">
                <span className="tm-theme-swatch" style={{ backgroundColor: theme.primary }} aria-hidden="true" />
                <span>{t("panel.appearance.themePrimary", { defaultValue: "主色" })}</span>
                <code>{theme.primary}</code>
              </span>
              <span className="tm-theme-color">
                <span className="tm-theme-swatch" style={{ backgroundColor: theme.secondary }} aria-hidden="true" />
                <span>{t("panel.appearance.themeSecondary", { defaultValue: "辅色" })}</span>
                <code>{theme.secondary}</code>
              </span>
            </div>
          </Field>
        ) : null}

        <details className="tm-appearance-adjust">
          <summary className="tm-appearance-adjust-summary">
            {t("panel.appearance.adjustSection", { defaultValue: "背景调节" })}
          </summary>
          <div className="tm-appearance-adjust-body">
            {!hasBg ? (
              <div className="tm-derived">{t("panel.appearance.noBgHint", { defaultValue: "选择图片后可预览壁纸效果。选择「不用壁纸」可恢复默认主题。" })}</div>
            ) : null}
            <div className={hasBg ? "tm-adjust-grid" : "tm-adjust-grid tm-adjust-off"}>
              <Field label={t("panel.appearance.fillLabel", { defaultValue: "填充方式" })}>
                <SegmentedControl
                  value={videoSelected && draft.fill === "repeat" ? "cover" : draft.fill}
                  options={fillOptions()}
                  onChange={(v: any) => { onDraft({ fill: String(v) }) }}
                />
              </Field>
              {videoSelected ? (
                <Field label={t("panel.appearance.motionLabel")} help={t("panel.appearance.motionHelp")}>
                  <label className="tm-wallpaper-motion-label">
                    <input type="checkbox" className="tm-wallpaper-motion" checked={draft.motion}
                      onChange={(event: any) => onDraft({ motion: !!event.currentTarget.checked })} />
                    <span>{t("panel.appearance.motionToggle")}</span>
                  </label>
                  {draft.fill === "repeat" ? <div className="tm-derived">{t("panel.appearance.videoRepeatFallback")}</div> : null}
                </Field>
              ) : null}
              <Field label={t("panel.appearance.posLabel", { defaultValue: "背景位置" })}>
                <div className="tm-pos-grid">
                  {APPEARANCE_POSITIONS.map((value) => {
                    const cellClass = value === draft.position ? "tm-pos-cell tm-pos-active" : "tm-pos-cell"
                    return (
                      <button
                        key={value}
                        type="button"
                        className={cellClass}
                        title={posLabel(value)}
                        aria-label={posLabel(value)}
                        aria-pressed={value === draft.position ? "true" : "false"}
                        onClick={() => { onDraft({ position: value }) }}
                      >
                        <span className="tm-pos-dot" style={posCellStyle(value)} />
                      </button>
                    )
                  })}
                </div>
              </Field>
              <Field label={t("panel.appearance.blurLabel", { defaultValue: "背景模糊" })} help={t("panel.appearance.blurHelp", { defaultValue: "虚化壁纸（px），0=清晰" })}>
                <Slider value={draft.blur} min={0} max={30} step={1} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ blur: Number(v) }) }} />
              </Field>
              <Field label={t("panel.appearance.dim", { defaultValue: "遮罩强度" })} help={t("panel.appearance.dimHelp", { defaultValue: "越高越暗；背景偏亮时调高一些，文字更清晰" })}>
                <Slider value={draft.dim} min={0} max={0.85} step={0.05} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ dim: Number(v) }) }} />
              </Field>
              <Field label={t("panel.appearance.brightnessLabel", { defaultValue: "背景亮度" })}>
                <Slider value={draft.brightness} min={30} max={150} step={5} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ brightness: Number(v) }) }} />
              </Field>
              <Field label={t("panel.appearance.saturateLabel", { defaultValue: "背景饱和度" })}>
                <Slider value={draft.saturate} min={0} max={200} step={5} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ saturate: Number(v) }) }} />
              </Field>
              <Field label={t("panel.appearance.contrastLabel", { defaultValue: "背景对比度" })}>
                <Slider value={draft.contrast} min={50} max={200} step={5} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ contrast: Number(v) }) }} />
              </Field>
              <Field label={t("panel.appearance.glassLabel", { defaultValue: "卡片毛玻璃强度" })} help={t("panel.appearance.glassHelp", { defaultValue: "卡片背后虚化的半径（px）" })}>
                <Slider value={draft.glass} min={0} max={40} step={1} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ glass: Number(v) }) }} />
              </Field>
              <Field label={t("panel.appearance.cardAlphaLabel", { defaultValue: "卡片底色强度" })} help={t("panel.appearance.cardAlphaHelp", { defaultValue: "卡片自身底色的不透明度（%），调低更透" })}>
                <Slider value={draft.card_alpha} min={0} max={100} step={5} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ card_alpha: Number(v) }) }} />
              </Field>
              <Field label={t("panel.appearance.textWeightLabel", { defaultValue: "整体字体显示强度" })} help={t("panel.appearance.textWeightHelp", { defaultValue: "调节文字深浅，数值越高越清晰" })}>
                <Slider value={draft.text_weight} min={40} max={100} step={5} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ text_weight: Number(v) }) }} />
              </Field>
            </div>
          </div>
        </details>

        <details className="tm-settings-disclosure tm-media-storage-disclosure">
          <summary>{t("panel.appearance.storageTitle", { defaultValue: "壁纸存储" })}</summary>
          <div className="tm-settings-disclosure-body tm-media-storage">
            <div className="tm-derived">
              {mediaStorage
                ? t("panel.appearance.storageUsage", { defaultValue: "已占用 {used} MiB / {total} MiB", used: Math.ceil(mediaStorage.occupied_bytes / 1048576), total: Math.ceil(mediaStorage.total_limit_bytes / 1048576) })
                : t("panel.appearance.storageLegacy", { defaultValue: "当前宿主未提供可调整的壁纸容量信息。" })}
            </div>
            {mediaStorage ? (
              <>
                <div className="tm-derived">
                  {t("panel.appearance.storagePending", { defaultValue: "暂存 {pending} MiB", pending: Math.ceil((mediaStorage.pending_bytes + mediaStorage.reclaim_bytes) / 1048576) })}
                  {mediaStorage.available_bytes !== null
                    ? ` · ${t("panel.appearance.storageFree", { defaultValue: "磁盘可用 {free} MiB", free: Math.ceil(mediaStorage.available_bytes / 1048576) })}`
                    : ""}
                </div>
                {mediaStorage.storage_reclaim_pending || mediaStorage.cleanup_pending
                  ? <Alert tone={mediaStorage.cleanup_pending ? "warning" : "info"} message={mediaStorage.cleanup_pending
                    ? t("panel.appearance.cleanupPending", { defaultValue: "仍有媒体数据等待清理，请关闭占用文件的程序后重试。" })
                    : t("panel.appearance.storageReclaimPending", { defaultValue: "媒体条目已清除；数据库文件大小可能暂时不变。" })} />
                  : null}
                <div className="tm-media-policy">
                  <label>
                    <span>{t("panel.appearance.singleLimit", { defaultValue: "单个视频上限（MiB）" })}</span>
                    <input type="number" min={32} max={512} step={1} value={singleLimit}
                      disabled={busy} onChange={(event: any) => setSingleLimit(Number(event.currentTarget.value))} />
                  </label>
                  <label>
                    <span>{t("panel.appearance.totalLimit", { defaultValue: "视频库预算（MiB）" })}</span>
                    <input type="number" min={64} max={16384} step={1} value={totalLimit}
                      disabled={busy} onChange={(event: any) => setTotalLimit(Number(event.currentTarget.value))} />
                  </label>
                  <Button disabled={busy || (singleLimit === Math.round(mediaStorage.single_limit_bytes / 1048576) && totalLimit === Math.round(mediaStorage.total_limit_bytes / 1048576))}
                    onClick={() => { void props.onUpdateVideoPolicy(singleLimit, totalLimit) }}>
                    {t("panel.appearance.policySave", { defaultValue: "更新容量" })}
                  </Button>
                </div>
                <Button tone="danger" disabled={busy} onClick={() => { void props.onClearMedia() }}>
                  {t("panel.appearance.clearMedia", { defaultValue: "清空壁纸与缓存" })}
                </Button>
              </>
            ) : null}
          </div>
        </details>

        {/* 待存读数 + 还原：真正的落盘在页面底部那条「保存设置」上 */}
        <div className="tm-appearance-save">
          <span className="tm-save-hint">
            {dirty
              ? t("panel.appearance.unsavedHint", { defaultValue: "有未保存的外观调整" })
              : t("panel.appearance.savedHint", { defaultValue: "外观已保存" })}
          </span>
          <Button tone="default" disabled={!dirty} onClick={() => {
            initialFilter.current = true
            setMediaKind(items.some((item) => item.id === saved.bg_id && item.kind === "video") ? "video" : "image")
            onRevert()
          }}>
            {t("panel.appearance.revert", { defaultValue: "还原" })}
          </Button>
        </div>
      </div>
    </section>
  )
}

// 宿主 ImageUpload 抛的运行时错误是英文（"File is too large (x MB)"等），
// 这里映射成中文给用户看；未知错误保留原文不吞信息
function localizeUploadError(t: TFunc, error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error || "")
  const tooLarge = raw.match(/File is too large \((\d+) MB\)/)
  if (tooLarge) {
    return t("panel.appearance.errorTooLarge", { defaultValue: "图片超过大小限制（{n} MB）。请使用更小的图片，或选择「自动压缩」。" })
      .replace("{n}", tooLarge[1])
  }
  if (/too large/i.test(raw)) {
    return t("panel.appearance.errorTooLargeShort", { defaultValue: "图片超过大小限制，请压缩到 4MB 以内" })
  }
  return raw
}
