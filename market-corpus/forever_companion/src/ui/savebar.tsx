// 吸底保存条：各设置页共用，滚动到底仍可见；未运行（无保存动作）时不渲染
import { Button } from "@neko/plugin-ui"
import type { TFunc } from "./types"

export function SaveBar(props: { t: TFunc; canSave: boolean; onSave: () => void }) {
  const { t, canSave, onSave } = props
  if (!canSave) return null
  return (
    <div className="tm-save">
      <Button tone="primary" onClick={onSave}>
        {t("panel.settings.save", { defaultValue: "保存设置" })}
      </Button>
    </div>
  )
}
