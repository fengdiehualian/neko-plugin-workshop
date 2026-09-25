// 面板外观卡（1.2.0）：图片图库 + 可调背景。
// 图库（入册/删除/缩略图）由 panel.tsx 经 gallery_* 入口即时落盘；
// "用哪张 + 十项调节"是 draft 参数——实时预览，点「保存外观」才生效。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前；不支持 <svg>（九宫格/占位图纯 CSS）
import { Button, Card, Field, ImageUpload, SegmentedControl, Slider } from "@neko/plugin-ui"
import { useState } from "@neko/plugin-ui"
import type { Appearance, GalleryItem, TFunc } from "./types"
import { APPEARANCE_POSITIONS, appearanceEquals, compressImageDataUrl } from "./utils"

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
  saving: boolean
  uploading: boolean
  onDraft: (patch: Record<string, string | number>) => void
  onSave: () => void
  onRevert: () => void
  onAdd: (dataUrl: string, thumb: string, name: string) => void
  onAskRemove: (item: GalleryItem) => void
}) {
  const { t, items, draft, saved, saving, uploading, onDraft, onSave, onRevert, onAdd, onAskRemove } = props
  // 压缩档只影响"怎么入册"，不是面板状态，留在卡内；上传错误本地化同前
  const [quality, setQuality] = useState<string>("auto")
  const [uploadError, setUploadError] = useState<string | null>(null)
  const dirty = !appearanceEquals(draft, saved)
  const hasBg = draft.bg_id !== ""
  const noImage = items.length === 0

  function fillOptions() {
    return [
      { value: "cover", label: t("panel.appearance.fill.cover", { defaultValue: "铺满裁剪" }) },
      { value: "contain", label: t("panel.appearance.fill.contain", { defaultValue: "完整显示" }) },
      { value: "repeat", label: t("panel.appearance.fill.repeat", { defaultValue: "平铺" }) },
      { value: "stretch", label: t("panel.appearance.fill.stretch", { defaultValue: "拉伸" }) },
    ]
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

  function handleUpload(artifact: any) {
    const url = String((artifact && artifact.dataUrl) || "")
    const mime = String((artifact && artifact.mime) || "")
    const name = String((artifact && (artifact.filename || artifact.name)) || "")
    setUploadError(null)
    if (!url) return
    const result = compressImageDataUrl(url, mime, quality)
    result.then((res) => {
      if (res.dataUrl.length > 6000000) {
        setUploadError(t("panel.appearance.errorTooLargeShort", { defaultValue: "图片太大了，请压缩到 4MB 以内" }))
        return
      }
      onAdd(res.dataUrl, res.thumb, name)
    }).catch(() => {
      // 解码失败兜底：原样入册（后端仍会把关），压缩档用户看不到差异也无需知道
      onAdd(url, "", name)
    })
  }

  return (
    <Card title={t("panel.appearance.title", { defaultValue: "面板外观" })}>
      <div className="tm-appearance">
        <div className="tm-derived">
          {t("panel.appearance.hint", { defaultValue: "导入的图片会留在图库里，随时切换当壁纸；下方调节实时预览，点保存后生效。" })}
        </div>

        {/* —— 图库：网格选择 + 导入 + 删除（即时落盘，不经保存钮） —— */}
        <Field label={t("panel.appearance.gallery", { defaultValue: "图片图库" })}>
          <div className="tm-gallery">
            <div
              className={hasBg ? "tm-gallery-tile tm-gallery-none" : "tm-gallery-tile tm-gallery-none tm-gallery-active"}
              title={t("panel.appearance.noWallpaper", { defaultValue: "不用壁纸" })}
              onClick={() => { onDraft({ bg_id: "" }) }}
            >
              <span className="tm-gallery-none-mark">×</span>
            </div>
            {items.map((item) => {
              const id = String(item.id || "")
              const active = id !== "" && id === draft.bg_id
              const thumb = String(item.thumb || "")
              const tileClass = active ? "tm-gallery-tile tm-gallery-active" : "tm-gallery-tile"
              const label = String(item.name || "") || t("panel.appearance.legacyName", { defaultValue: "旧的背景图" })
              return (
                <div
                  key={id}
                  className={thumb ? tileClass : `${tileClass} tm-gallery-thumbless`}
                  style={thumb ? { backgroundImage: `url("${thumb}")` } : undefined}
                  title={label}
                  onClick={() => { onDraft({ bg_id: id }) }}
                >
                  {active ? <span className="tm-gallery-use">{t("panel.appearance.inUse", { defaultValue: "使用中" })}</span> : null}
                  <span
                    className="tm-gallery-del"
                    title={t("panel.appearance.deleteTile", { defaultValue: "从图库删除" })}
                    onClick={(event: any) => {
                      if (event && event.stopPropagation) event.stopPropagation()
                      onAskRemove(item)
                    }}
                  >×</span>
                </div>
              )
            })}
          </div>
        </Field>

        <Field
          label={t("panel.appearance.modeLabel", { defaultValue: "导入方式" })}
          help={t("panel.appearance.modeHelp", { defaultValue: "自动压缩会缩到长边 2560 并重编码，图库能放更多张；GIF / SVG 一律原样保留" })}
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
          help={noImage ? t("panel.appearance.galleryEmptyHint", { defaultValue: "图库还是空的——导入第一张试试吧" }) : t("panel.appearance.uploadHelp", { defaultValue: "支持 PNG / JPG / WebP / GIF / SVG，原画质档不超过 4MB" })}
          error={uploadError || undefined}
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
        {uploading ? (
          <div className="tm-derived">{t("panel.appearance.adding", { defaultValue: "处理图片中…" })}</div>
        ) : null}

        {/* —— 背景调节：全部作用于当前壁纸，实时预览、保存生效 —— */}
        <Field label={t("panel.appearance.adjustSection", { defaultValue: "背景调节" })}>
          {!hasBg ? (
            <div className="tm-derived">{t("panel.appearance.noBgHint", { defaultValue: "先在上方选一张壁纸（或用「不用壁纸」格取消），调节即实时预览" })}</div>
          ) : null}
          <div className={hasBg ? "tm-adjust-grid" : "tm-adjust-grid tm-adjust-off"}>
            <Field label={t("panel.appearance.fillLabel", { defaultValue: "填充方式" })}>
              <SegmentedControl
                value={draft.fill}
                options={fillOptions()}
                onChange={(v: any) => { onDraft({ fill: String(v) }) }}
              />
            </Field>
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
            <Field label={t("panel.appearance.textWeightLabel", { defaultValue: "整体字体显示强度" })} help={t("panel.appearance.textWeightHelp", { defaultValue: "正文文字的浓淡；调低变淡并自动加描边保证可读" })}>
              <Slider value={draft.text_weight} min={40} max={100} step={5} showValue disabled={!hasBg} onChange={(v: any) => { onDraft({ text_weight: Number(v) }) }} />
            </Field>
          </div>
        </Field>

        <div className="tm-appearance-save">
          <span className="tm-save-hint">
            {dirty
              ? t("panel.appearance.unsavedHint", { defaultValue: "有未保存的外观调整" })
              : t("panel.appearance.savedHint", { defaultValue: "外观已同步" })}
          </span>
          <Button tone="default" disabled={!dirty || saving} onClick={onRevert}>
            {t("panel.appearance.revert", { defaultValue: "还原" })}
          </Button>
          <Button tone="primary" disabled={!dirty || saving} onClick={onSave}>
            {saving
              ? t("panel.appearance.savingNow", { defaultValue: "保存中…" })
              : t("panel.appearance.saveBtn", { defaultValue: "保存外观" })}
          </Button>
        </div>
      </div>
    </Card>
  )
}

// 宿主 ImageUpload 抛的运行时错误是英文（"File is too large (x MB)"等），
// 这里映射成中文给用户看；未知错误保留原文不吞信息
function localizeUploadError(t: TFunc, error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error || "")
  const tooLarge = raw.match(/File is too large \((\d+) MB\)/)
  if (tooLarge) {
    return t("panel.appearance.errorTooLarge", { defaultValue: "图片太大了（{n} MB），请换小一点的图或选「自动压缩」" })
      .replace("{n}", tooLarge[1])
  }
  if (/too large/i.test(raw)) {
    return t("panel.appearance.errorTooLargeShort", { defaultValue: "图片太大了，请压缩到 4MB 以内" })
  }
  return raw
}
