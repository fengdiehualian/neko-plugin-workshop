// 配置引导（1.2.6）：总览页顶部"准备清单"卡。就绪状态由后端 dashboard 即时
// 计算（纯本地零开销），每项带直达动作；must 欠账时卡片常驻，全部就绪整卡收起。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前
import { Button, Card } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { GuideItem, Readiness, TFunc } from "./types"

export function GuideCard(props: {
  t: TFunc
  readiness?: Readiness
  canEnable: boolean
  // 同状态条总开关：resolve false 时开关回落（确认取消/失败）
  onEnable: () => any
  onGoto: (tab: string) => void
}) {
  const { t, readiness, canEnable, onEnable, onGoto } = props
  const items = (readiness && readiness.items) || []
  // 全就绪（或后端没下发）→ 整卡收起，不打扰
  if (!readiness || readiness.all_ok || items.length === 0) return null
  const doneCount = items.filter((item) => item.ok === true).length

  return (
    <Card title={t("panel.guide.title", { defaultValue: "把她准备到最佳状态" })}>
      <div className="tm-guide-progress">
        <span className="tm-guide-bar">
          <span className="tm-guide-bar-fill" style={{ width: `${Math.round((doneCount / items.length) * 100)}%` }} />
        </span>
        <span className="tm-guide-count">{doneCount}/{items.length}</span>
      </div>
      <div className="tm-guide-list">
        {items.map((item) => (
          <div key={String(item.id)} className={item.ok ? "tm-guide-row tm-guide-row-ok" : "tm-guide-row"}>
            <span className={item.ok ? "tm-guide-dot tm-guide-dot-ok" : "tm-guide-dot"} />
            <span className="tm-guide-text">{guideItemLabel(t, item)}</span>
            {!item.ok && item.level === "must" ? (
              <span className="tm-guide-must">{t("panel.guide.must", { defaultValue: "必办" })}</span>
            ) : null}
            <span className="tm-guide-spacer" />
            {!item.ok && item.id === "rhythm" ? (
              <TmSwitch
                checked={false}
                small
                disabled={!canEnable}
                title={t("panel.guide.turnOn", { defaultValue: "开启" })}
                onChange={() => onEnable()}
              />
            ) : null}
            {!item.ok && item.id !== "rhythm" && item.tab ? (
              <Button onClick={() => onGoto(String(item.tab))}>
                {t("panel.guide.go", { defaultValue: "去设置" })}
              </Button>
            ) : null}
          </div>
        ))}
      </div>
      <div className="tm-guide-foot">
        {t("panel.guide.foot", { defaultValue: "「建议」项不点亮也能开始陪伴——它们决定的是她能记多深、读你多准" })}
      </div>
    </Card>
  )
}

// 非导出辅助：清单项文案（id → 本地化标签）。未知 id 原样显示（后端加项而
// 面板未更新时的容忍面，与全插件"坏数据宽容"纪律一致）
function guideItemLabel(t: TFunc, item: GuideItem): string {
  const id = String(item.id || "")
  if (id === "rhythm") return t("panel.guide.item.rhythm", { defaultValue: "开启她的身体节律（总开关）" })
  if (id === "anchor") return t("panel.guide.item.anchor", { defaultValue: "周期起点已确认（想改去「周期」页）" })
  if (id === "mood") return t("panel.guide.item.mood", { defaultValue: "情绪系统已开启" })
  if (id === "channels") return t("panel.guide.item.channels", { defaultValue: "至少一条模型通道在线（碎片/成文/语气）" })
  if (id === "together") return t("panel.guide.item.together", { defaultValue: "去和她聊聊天——相处记录从今天点亮" })
  return id
}
