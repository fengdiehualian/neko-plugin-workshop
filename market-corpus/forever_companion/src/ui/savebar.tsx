// 吸底保存条：各设置页共用，滚动到底仍可见；未运行（无保存动作）时不渲染
// saving 只禁用按钮不改文案：连点会发两趟写盘，落盘在飞时必须挡掉
import { Button, useEffect, useRef } from "@neko/plugin-ui"
import type { TFunc } from "./types"

export function SaveBar(props: { t: TFunc; canSave: boolean; onSave: () => void; saving?: boolean }) {
  const { t, canSave, onSave, saving } = props
  const barRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!canSave) return
    const bar = barRef.current
    const content = bar?.closest(".tm-content") as HTMLElement | null
    if (!bar || !content) return
    let active = true
    // 其它吸底控件避让实测保存条，随语言换行和内容区内边距更新。
    const update = () => {
      if (!active) return
      const padding = parseFloat(window.getComputedStyle(content).paddingBottom) || 0
      const height = bar.getBoundingClientRect().height
      content.style.setProperty("--tm-save-clearance", `${Math.ceil(Math.max(0, height + padding))}px`)
    }
    update()
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null
    observer?.observe(bar)
    observer?.observe(content)
    window.addEventListener("resize", update)
    return () => {
      active = false
      observer?.disconnect()
      window.removeEventListener("resize", update)
      content.style.removeProperty("--tm-save-clearance")
    }
  }, [canSave])
  if (!canSave) return null
  return (
    <div className="tm-save" ref={barRef}>
      <Button tone="primary" disabled={!!saving} onClick={onSave}>
        {t("panel.settings.save", { defaultValue: "保存设置" })}
      </Button>
    </div>
  )
}
