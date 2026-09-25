// 功能管理页（1.2.7 能力中心；1.2.7 起每行带「功能介绍」按钮）：
// 全部功能能力的开关总览。
// 数据源 = dashboard 轮询同源下发的 capabilities（按宿主当前角色解析）；
// 开关经 set_capability 写"否决集"——关闭即否决、打开回落既有配置默认（不会
// 强行点亮配置里关着的项）。
// 介绍子页（1.2.7 二轮修订）：点按钮拉一次 get_capability_intro（前端缓存、
// 不轮询），在功能页内切换到介绍子页展示作用/场景/依赖/限制/原理图（文案源
// core/intros.py，渲染与排版全在 ui/capintro.tsx）。不再是居中 Modal：
// 子页直接吃面板内容区自然宽度，刚好贴合外框、随窗口伸缩。
// 高级选项 hide_disabled_tools 全局一份：开启后，对所有角色都不生效的能力，
// 其 LLM 工具从模型可见面摘除（面板保存后经 props 上的 api.refresh 拉回最新状态）。
import { Card, Field, StatusBadge, Alert } from "@neko/plugin-ui"
import { useState } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { CapabilitiesPayload, CapItem, TFunc } from "./types"
import { CapIntroView, LLM_BADGES } from "./capintro"
import type { CapIntroPayload } from "./capintro"

const GROUP_ORDER = ["rhythm", "mood", "diary"]

export function FeaturesPane(props: {
  t: TFunc
  caps: CapabilitiesPayload | null
  loading?: boolean
  busyId?: string
  onToggleCap: (id: string, enabled: boolean) => any
  onToggleHideTools: (value: boolean) => any
  onLoadIntro: (id: string) => Promise<CapIntroPayload>
}) {
  const { t, caps, loading, busyId, onToggleCap, onToggleHideTools, onLoadIntro } = props
  const items = (caps && caps.capabilities) || []
  const masterOn = !!caps && caps.master_enabled !== false

  // 介绍子页：introId 空串 = 列表视图；非空 = 整页切到介绍子页（前端缓存，
  // 返回列表不重复拉取；文案静态，开关态/依赖态由子页头部现场叠展示）
  const [introId, setIntroId] = useState("")
  const [introCache, setIntroCache] = useState<Record<string, CapIntroPayload>>({})
  const [introLoading, setIntroLoading] = useState(false)
  const [introError, setIntroError] = useState("")

  function openIntro(item: CapItem) {
    setIntroId(item.id)
    setIntroError("")
    if (introCache[item.id]) return
    setIntroLoading(true)
    Promise.resolve(onLoadIntro(item.id))
      .then((payload) => {
        if (payload) setIntroCache({ ...introCache, [item.id]: payload })
      })
      .catch(() => {
        setIntroError(t("panel.capintro.loadError", { defaultValue: "介绍加载失败，请返回列表重试；若反复失败，重启插件服务后即可生效" }))
      })
      .finally(() => setIntroLoading(false))
  }

  function capLabel(id: string): string {
    return t(`panel.features.cap.${id}.label`, { defaultValue: id })
  }

  function groupLabel(group: string): string {
    return t(`panel.features.group.${group}`, { defaultValue: group })
  }

  function offHint(item: CapItem): string {
    if (item.source === "master_off") {
      return t("panel.features.hint.masterOff", { defaultValue: "该角色的总开关未开启" })
    }
    if (item.source === "config_off") {
      return t("panel.features.hint.configOff", { defaultValue: "功能配置里已关闭（到对应设置页打开）" })
    }
    if (item.source === "upstream_off") {
      const deps = (item.blocked_by || []).map(capLabel).join("、")
      return t("panel.features.hint.upstream", { defaultValue: `依赖的功能未生效：${deps}` })
    }
    if (item.source === "user_off") {
      return t("panel.features.hint.userOff", { defaultValue: "已在功能管理里关闭" })
    }
    return ""
  }

  const introItem = introId ? items.find((item) => item.id === introId) : null

  // 页内子页导航：已进入介绍态就整页切换（不叠弹窗），返回即回列表；
  // 能力被卸下线（理论上不会：introId 来自当前 items）时自动回落列表
  if (introItem) {
    return (
      <CapIntroView
        t={t}
        item={introItem}
        capLabel={capLabel}
        statusHint={offHint(introItem)}
        intro={introCache[introItem.id] || null}
        loading={introLoading}
        error={introError}
        onBack={() => setIntroId("")}
      />
    )
  }

  return (
    <div className="tm-pane">
      {!masterOn ? (
        <Alert
          tone="warning"
          message={t("panel.features.masterBanner", {
            defaultValue: "该角色的总开关未开启：下面所有功能都不会生效（这里改的开关仍会记住）。",
          })}
        />
      ) : null}

      {/* 单页目标（1.2.7 用户反馈）：三组卡横向并排，总高≈最高的身体节律列；
          窄窗口由 CSS 媒体查询回落双栏/单栏 */}
      <div className="tm-feat-cols">
      {GROUP_ORDER.map((group) => {
        const inGroup = items.filter((item) => item.group === group)
        if (!inGroup.length) return null
        return (
          <Card key={group} title={groupLabel(group)}>
            <div className="tm-feat-list">
              {inGroup.map((item) => {
                const badge = LLM_BADGES[item.llm] || LLM_BADGES.none
                const busy = busyId === item.id
                return (
                  <div key={item.id} className="tm-feat-row">
                    <Field
                      label={(
                        <span className="tm-feat-label">
                          {capLabel(item.id)}
                          <button
                            type="button"
                            className="tm-ci-open"
                            onClick={() => openIntro(item)}
                          >
                            {t("panel.capintro.open", { defaultValue: "功能介绍" })}
                          </button>
                        </span>
                      )}
                      help={
                        (item.enabled ? "" : offHint(item)) ||
                        t(`panel.features.cap.${item.id}.desc`, { defaultValue: "" })
                      }
                    >
                      <span className="tm-feat-side">
                        <StatusBadge tone={badge.tone} label={t(badge.key, { defaultValue: badge.def })} />
                        <TmSwitch
                          checked={!!item.enabled}
                          disabled={busy || loading}
                          onChange={(value: boolean) => onToggleCap(item.id, value)}
                        />
                      </span>
                      </Field>
                  </div>
                )
              })}
            </div>
          </Card>
        )
      })}
      </div>

      {/* 高级选项压成单行窄条（配合横排后一屏放得下全部开关）：
          长说明挂 title 悬停可见，不占版面 */}
      <div className="tm-feat-adv">
        <span className="tm-feat-adv-label">{t("panel.features.advanced", { defaultValue: "高级选项" })}</span>
        <span
          className="tm-feat-adv-switch"
          title={t("panel.features.hideToolsHelp", {
            defaultValue: "默认温和模式：关闭的功能其工具仍在位、调用时被拒绝。开启后，对所有角色都不生效的功能，其工具会真正对模型隐藏（省上下文）；恢复生效自动重挂。",
          })}
        >
          <TmSwitch
            checked={!!(caps && caps.hide_disabled_tools)}
            disabled={loading}
            label={t("panel.features.hideTools", { defaultValue: "关闭的功能从模型可见面摘除工具" })}
            onChange={(value: boolean) => onToggleHideTools(value)}
          />
        </span>
        <span className="tm-feat-adv-help">
          {caps && caps.hide_disabled_tools
            ? t("panel.features.hideToolsOn", { defaultValue: "已开启：不生效功能的工具会摘除" })
            : t("panel.features.hideToolsOff", { defaultValue: "温和模式：工具在位、调用时才拒" })}
        </span>
      </div>
    </div>
  )
}
