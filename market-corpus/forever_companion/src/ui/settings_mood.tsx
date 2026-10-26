// 情绪页：只读选择与事件记录；参数仍通过页面保存入口提交。
import { Button, Card, Field, NumberInput } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { FormValues, Mood, MoodEvent, TFunc } from "./types"
import { arousalColor, arousalScore, moodDotColor, moodWordOf, stripEmoji, valenceBarColor, valenceScore } from "./utils"

export function MoodSettingsCard(props: {
  key?: string
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
  mood: Mood
  enabled: boolean
  canEnd: boolean
  ending: boolean
  onEnd: () => void
}) {
  const { t, form, updateForm } = props

  return (
    <>
    <MoodExperience {...props} />
    <Card title={t("panel.settings.sectionMood", { defaultValue: "情绪系统" })}>
      <div className="tm-derived">
        {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
      </div>
      <TmSwitch
        checked={form.mood_enabled}
        label={t("panel.settings.moodEnabled", { defaultValue: "启用情绪系统（实验性）" })}
        onChange={(value: boolean) => updateForm({ mood_enabled: value })}
      />
      {form.mood_enabled ? (
        <Field className="tm-reserve-help" label={t("panel.settings.actionMinutes", { defaultValue: "限时情绪动作默认时长（分钟）" })}>
          <NumberInput value={form.default_action_minutes} min={1} max={720} step={1} onChange={(v: number | string) => updateForm({ default_action_minutes: typeof v === "number" ? v : 20 })} />
        </Field>
      ) : null}
    </Card>
    </>
  )
}

function MoodExperience(props: {
  t: TFunc; mood: Mood; enabled: boolean; canEnd: boolean; ending: boolean
  onEnd: () => void
}) {
  const { t, mood, enabled, canEnd, ending, onEnd } = props
  const systemOn = enabled && mood.system_enabled !== false
  const active = systemOn && !!mood.active
  const events = Array.isArray(mood.history) ? mood.history : []
  const recent = events.slice(0, 5)
  const earlier = events.slice(5)
  const v = Math.max(-1, Math.min(1, Number(mood.affect?.valence) || 0))
  const a = Math.max(0, Math.min(1, Number(mood.affect?.arousal) || 0))
  const label = active ? actionName(mood.action || "", t) : t("panel.mood.noChoice", { defaultValue: "暂无生效的工具选择" })
  const word = systemOn ? t(moodWordOf(v, a), { defaultValue: "平静" }) : "—"
  const remaining = active && mood.expires_at
    ? t("panel.mood.recordUntil", { time: eventTime(mood.expires_at), defaultValue: "记录有效至 {time}" }) : ""
  return (
    <>
      <Card title={t("panel.mood.currentChoice", { defaultValue: "当前选择" })}>
        <div className="tm-mood-current" aria-live="polite">
          <div className="tm-mood-choice">
            <span className={active ? "tm-mood-choice-dot tm-mood-choice-dot-active" : "tm-mood-choice-dot"} />
            <strong>{systemOn ? label : t("panel.off", { defaultValue: "已关闭" })}</strong>
            {active ? <span className="tm-mood-source">{sourceName(mood.origin || "", t)}</span> : null}
          </div>
          {remaining ? <div className="tm-derived">{remaining}</div> : null}
        </div>
        {active && mood.reason ? (
          <details className="tm-mood-reason">
            <summary>{t("panel.mood.recordedReason", { defaultValue: "记录的原因" })}</summary>
            <p>{mood.reason}</p>
          </details>
        ) : null}
        <div className="tm-mood-observation">
          <div className="tm-mood-observation-title">
            <span>{t("panel.mood.observation", { defaultValue: "心情观察" })}</span>
            <span style={{ color: moodDotColor(systemOn ? v : 0) }}>{word}</span>
          </div>
          <div className="tm-mood-observation-bars">
            <ObservationBar label={t("panel.mood.gauge.valence", { defaultValue: "愉悦度" })}
              score={systemOn ? valenceScore(v) : null} color={valenceBarColor(v)} />
            <ObservationBar label={t("panel.mood.gauge.arousal", { defaultValue: "活跃度" })}
              score={systemOn ? arousalScore(a) : null} color={arousalColor(a)} />
          </div>
        </div>
        {active && canEnd ? (
          <div className="tm-mood-actions">
            <Button disabled={ending} onClick={onEnd}>{t("actions.liftMood.label", { defaultValue: "结束当前状态" })}</Button>
          </div>
        ) : null}
      </Card>
      <Card title={t("panel.mood.historyTitle", { defaultValue: "最近变化" })}>
        {events.length ? (
          <>
            <ol className="tm-mood-history">
              {recent.map((event, i) => <EventRow key={`${event.ts}:${i}`} event={event} t={t} />)}
            </ol>
            {earlier.length ? (
              <details className="tm-mood-earlier">
                <summary>{t("panel.mood.historyMore", { n: earlier.length, defaultValue: "更早的记录 · {n} 条" })}</summary>
                <ol className="tm-mood-history">
                  {earlier.map((event, i) => <EventRow key={`${event.ts}:${i}`} event={event} t={t} />)}
                </ol>
              </details>
            ) : null}
          </>
        ) : <div className="tm-derived tm-mood-empty">{t("panel.mood.historyEmpty", { defaultValue: "还没有情绪工具变化记录" })}</div>}
      </Card>
    </>
  )
}

function ObservationBar(props: { label: string; score: number | null; color: string }) {
  return (
    <div className="tm-mood-observation-bar">
      <span>{props.label}</span>
      <div className="tm-mood-observation-track" aria-hidden="true">
        <div style={{ width: `${props.score === null ? 0 : props.score}%`, background: props.color }} />
      </div>
      <span className="tm-mood-observation-value">{props.score === null ? "—" : props.score}</span>
    </div>
  )
}

function EventRow(props: { key?: string; event: MoodEvent; t: TFunc }) {
  const { event, t } = props
  const action = actionName(event.action, t)
  const title = event.kind === "switch"
    ? t("panel.mood.eventSwitch", { previous: actionName(event.previous_action || "", t), action, defaultValue: "{previous} → {action}" })
    : event.kind === "release"
      ? t("panel.mood.eventRelease", { action, defaultValue: "结束了「{action}」" })
      : event.kind === "expire"
        ? t("panel.mood.eventExpire", { action, defaultValue: "「{action}」记录到期" })
        : t("panel.mood.eventChoose", { action, defaultValue: "选择了「{action}」" })
  return (
    <li className="tm-mood-event">
      <time>{eventTime(event.ts)}</time>
      <div className="tm-mood-event-body">
        <div>{title}</div>
        <span className="tm-mood-source">{sourceName(event.origin, t)}</span>
        {event.reason ? (
          <details className="tm-mood-reason">
            <summary>{t("panel.mood.recordedReason", { defaultValue: "记录的原因" })}</summary>
            <p>{event.reason}</p>
          </details>
        ) : null}
      </div>
    </li>
  )
}

function sourceName(origin: string, t: TFunc): string {
  if (origin === "self") return t("panel.mood.sourceSelf", { defaultValue: "角色自主选择" })
  if (origin === "user") return t("panel.mood.sourceUser", { defaultValue: "用户明确要求" })
  if (origin === "timer") return t("panel.mood.sourceTimer", { defaultValue: "记录到期" })
  return t("panel.mood.sourceUnknown", { defaultValue: "来源未记录" })
}

function actionName(action: string, t: TFunc): string {
  return stripEmoji(t(`mood.${action}`, { defaultValue: action }))
}

function eventTime(ts: number): string {
  const date = new Date(ts * 1000)
  if (!Number.isFinite(date.getTime())) return "—"
  return date.toLocaleString(undefined, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  })
}
