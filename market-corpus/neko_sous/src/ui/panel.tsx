import { Button, Modal, Page, StatusBadge, useEffect, useRef, useState } from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type Problem = { id: string; code: string; detail?: string }
type State = {
  address?: string; game_state?: string; connection_state?: string; desired?: boolean;
  session_id?: string; server_version?: string; retry_in?: number; last_error?: Problem | null;
  mode?: string; mode_supported?: boolean; pending_mode?: string | null;
  events_supported?: boolean; events_subscribed?: boolean; state_supported?: boolean; kitchen_supported?: boolean; mixing_supported?: boolean;
  subscription_pending?: boolean; settings_pending?: boolean;
  settings?: { events_enabled: boolean; chatter_enabled: boolean; frequency: string; delivery_enabled?: boolean; expiry_enabled?: boolean; failure_enabled?: boolean; combo_enabled?: boolean; fire_enabled?: boolean; ready_enabled?: boolean; overcooking_enabled?: boolean; mixing_ready_enabled?: boolean; mixing_warning_enabled?: boolean; mixing_ruined_enabled?: boolean };
}

const styles = `
.sous{--accent:#19866b;--soft:rgba(25,134,107,.09);max-width:850px;margin:auto;padding:6px 0 14px}
.sous *{box-sizing:border-box}.sous-header{display:flex;align-items:center;gap:16px;margin-bottom:28px}
.sous-mark{width:54px;height:54px;display:grid;place-items:center;border-radius:18px;background:var(--soft);color:var(--accent);font-size:28px}
.sous-eyebrow{font-size:10px;letter-spacing:.18em;color:var(--accent);font-weight:750;margin-bottom:6px}
.sous-title{font-size:26px;letter-spacing:-.03em;font-weight:750;margin:0}.sous-subtitle{opacity:.65;font-size:13px;line-height:1.6;margin:7px 0 0}
.sous-box{border:1px solid rgba(128,148,143,.22);border-radius:22px;overflow:hidden;background:rgba(128,148,143,.025)}
.sous-top{padding:23px 25px;background:linear-gradient(115deg,var(--soft),transparent);display:flex;align-items:center;justify-content:space-between;gap:12px}
.sous-top strong{font-size:15px}.sous-tag{font-size:10px;letter-spacing:.13em;opacity:.55}
.sous-row{padding:22px 25px;display:flex;justify-content:space-between;align-items:center;gap:18px;border-top:1px solid rgba(128,148,143,.16)}
.sous-label{font-size:14px;font-weight:650}.sous-caption{font-size:12px;opacity:.55;margin-top:6px;line-height:1.5}
.sous-pill{border-radius:999px!important;padding:6px 12px!important;white-space:nowrap;font-size:12px!important;display:inline-flex;gap:7px;align-items:center}
.sous-footer{padding:23px 25px;border-top:1px solid rgba(128,148,143,.16);display:flex;align-items:center;justify-content:space-between;gap:18px}
.sous-hint{font-size:12px;opacity:.65;line-height:1.7;max-width:440px}.sous-buttons{display:flex;gap:9px;flex-shrink:0}
.sous-meta{margin:18px 4px 0;font-size:11px;opacity:.5;display:flex;gap:16px;flex-wrap:wrap;overflow-wrap:anywhere}
.sous-code{font-family:ui-monospace,monospace;font-size:12px}.sous-notice{margin:16px 0 0;border-radius:14px;padding:14px 17px;background:rgba(200,135,40,.09);font-size:12px;line-height:1.7}
.sous-dialog p{line-height:1.75}.sous-dialog small{display:block;opacity:.65;line-height:1.7}
.sous-modes{padding:22px 25px;border-top:1px solid rgba(128,148,143,.16)}
.sous-mode-options{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}
.sous-mode-button{font:inherit;color:inherit;text-align:left;padding:14px;border:1px solid rgba(128,148,143,.25);border-radius:13px;background:transparent;cursor:pointer}
.sous-mode-button[aria-checked="true"]{border-color:var(--accent);background:var(--soft)}
.sous-mode-button:disabled{cursor:default;opacity:.55}.sous-mode-button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.sous-mode-button strong{font-size:13px;display:block}.sous-mode-button span{display:block;font-size:11px;opacity:.65;line-height:1.6;margin-top:5px}
.sous-settings{padding:23px 25px;border-top:1px solid rgba(128,148,143,.16)}
.sous-setting-row{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 0}
.sous-setting-row+.sous-setting-row{border-top:1px solid rgba(128,148,143,.12)}
.sous-switch{border:0;padding:3px;width:42px;height:25px;border-radius:20px;flex-shrink:0;background:rgba(128,148,143,.4);cursor:pointer}
.sous-switch[aria-checked="true"]{background:var(--accent)}
.sous-switch span{display:block;width:19px;height:19px;background:white;border-radius:50%;transition:transform .15s}
.sous-switch[aria-checked="true"] span{transform:translateX(17px)}
.sous-switch:disabled{opacity:.45;cursor:default}.sous-switch:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.sous-frequency{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:12px}
@media(max-width:550px){.sous-settings{padding:19px}.sous-frequency{grid-template-columns:1fr}.sous-setting-row{gap:12px}}
@media(max-width:550px){.sous-top,.sous-row,.sous-footer{padding:19px}.sous-footer{align-items:stretch;flex-direction:column}.sous-buttons{justify-content:flex-end}.sous-title{font-size:23px}.sous-row{gap:12px}.sous-code{font-size:11px}}
`

export default function NekoSousPanel(props: PluginSurfaceProps<State>) {
  const { t } = props
  const state = props.state || {}
  const latest = useRef(props)
  latest.current = props
  const seen = useRef("")
  const working = useRef(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const connection = state.connection_state || "disconnected"
  const game = state.game_state || "unknown"
  const desired = !!state.desired
  const canConnect = props.actions.some(action => (action.entry_id || action.id) === "connect")
  const canDisconnect = props.actions.some(action => (action.entry_id || action.id) === "disconnect")
  const canSetMode = props.actions.some(action => (action.entry_id || action.id) === "set_mode")
  const mode = state.mode || "companion"
  const modeDisabled = busy || !!state.pending_mode || connection !== "connected" || !state.mode_supported || !canSetMode
  const settings = state.settings || { events_enabled: true, chatter_enabled: true, frequency: "balanced" }
  const canSettings = props.actions.some(action => (action.entry_id || action.id) === "update_settings")
  const settingsBusy = busy || !!state.settings_pending || !!state.subscription_pending || !canSettings
  const subscriptionDisabled = settingsBusy || (desired && connection !== "connected")
    || (connection === "connected" && !state.events_supported)

  function displayError(error: Problem) {
    if (seen.current === error.id) return
    seen.current = error.id
    setProblem(error)
  }

  function bridgeError(error: any, id: string): Problem {
    console.error("[NekoSous] Hosted UI request failed", { code: error?.code, message: error?.message })
    const code = error?.code === "PLUGIN_UI_ACTION_NOT_FOUND" ? "ui_action_missing"
      : error?.code === "PLUGIN_NOT_RUNNING" ? "plugin_not_running" : "ui_unavailable"
    return { id, code }
  }

  useEffect(() => {
    if (state.last_error) displayError(state.last_error)
  }, [state.last_error?.id])

  useEffect(() => {
    let active = true
    let refreshing = false
    const timer = setInterval(async () => {
      if (!active || refreshing) return
      refreshing = true
      try {
        await latest.current.api.refresh()
      } catch (error) {
        if (active && seen.current !== "context-unavailable") displayError(bridgeError(error, "context-unavailable"))
      } finally {
        refreshing = false
      }
    }, 2000)
    return () => { active = false; clearInterval(timer) }
  }, [])

  async function act(action: string, args: Record<string, any> = {}) {
    if (working.current) return
    working.current = true
    setBusy(true)
    try {
      const envelope = await props.api.call(action, args, { timeoutMs: 15000 })
      const result = envelope?.result || envelope?.data || envelope || {}
      if (result.last_error) displayError(result.last_error)
      await props.api.refresh()
    } catch (error) {
      displayError(bridgeError(error, `action-${Date.now()}`))
    } finally {
      working.current = false
      setBusy(false)
    }
  }

  const tone = connection === "connected" ? "success" : desired ? "warning" : "default"
  return <Page>
    <style>{styles}</style>
    <div className="sous">
      <div className="sous-header">
        <div className="sous-mark" aria-hidden="true">♨</div>
        <div>
          <div className="sous-eyebrow">{t("panel.eyebrow")}</div>
          <h1 className="sous-title">{t("panel.title")}</h1>
          <p className="sous-subtitle">{t("panel.subtitle")}</p>
        </div>
      </div>
      <div className="sous-box">
        <div className="sous-top"><strong>{t("panel.connection")}</strong><span className="sous-tag">{t("panel.local")}</span></div>
        <div className="sous-row">
          <div><div className="sous-label">{t("panel.game")}</div><div className="sous-caption">{t("panel.gameName")}</div></div>
          <StatusBadge tone={game === "running" ? "success" : game === "unknown" ? "warning" : "default"} className="sous-pill">
            {t(`game.${game}`)}
          </StatusBadge>
        </div>
        <div className="sous-row">
          <div><div className="sous-label">{t("panel.server")}</div><div className="sous-caption sous-code">{state.address || "ws://127.0.0.1:48930/neko"}</div></div>
          <StatusBadge tone={tone} className="sous-pill">{t(`connection.${connection}`)}</StatusBadge>
        </div>
        <div className="sous-modes">
          <div className="sous-label">{t("panel.mode")}</div>
          <div className="sous-mode-options" role="radiogroup" aria-label={t("panel.mode")}>
            {["companion", "playmate"].map(value => <button type="button" role="radio"
              className="sous-mode-button" aria-checked={mode === value ? "true" : "false"} disabled={modeDisabled}
              onClick={() => { if (value !== mode) return act("set_mode", { mode: value }) }}>
              <strong>{t(`mode.${value}`)}</strong><span>{t(`mode.${value}Description`)}</span>
            </button>)}
          </div>
          <div className="sous-caption">{t(state.pending_mode ? "mode.pending" : connection !== "connected"
            ? "mode.connectFirst" : !state.mode_supported ? "mode.upgrade" : "mode.confirmed", { mode: t(`mode.${mode}`) })}</div>
          <div className="sous-caption">{t("mode.phaseOne")}</div>
        </div>
        <div className="sous-settings">
          <div className="sous-label">{t("settings.title")}</div>
          <div className="sous-setting-row">
            <div><div className="sous-label">{t("settings.events")}</div>
              <div className="sous-caption">{t("settings.eventsHint")}</div>
              <div className="sous-caption">{t(state.subscription_pending ? "settings.syncing"
                : connection !== "connected" ? "settings.offline" : !state.events_supported
                  ? "settings.upgradeEvents" : state.events_subscribed ? "settings.subscribed" : "settings.unsubscribed")}</div>
            </div>
            <button type="button" role="switch" aria-label={t("settings.events")} className="sous-switch"
              aria-checked={settings.events_enabled ? "true" : "false"} disabled={subscriptionDisabled}
              onClick={() => act("update_settings", { events_enabled: !settings.events_enabled })}><span /></button>
          </div>
          <div className="sous-label">{t("settings.categories")}</div>
          {["delivery", "expiry", "failure", "combo", "fire", "ready", "overcooking", "mixing_ready", "mixing_warning", "mixing_ruined"].map(key => {
            const setting = `${key}_enabled` as "delivery_enabled" | "expiry_enabled" | "failure_enabled" | "combo_enabled" | "fire_enabled" | "ready_enabled" | "overcooking_enabled" | "mixing_ready_enabled" | "mixing_warning_enabled" | "mixing_ruined_enabled"
            const enabled = settings[setting] !== false
            const unsupported = connection === "connected" && ((["fire", "ready", "overcooking"].includes(key) && !state.kitchen_supported)
              || (key.startsWith("mixing_") && !state.mixing_supported))
            return <div className="sous-setting-row" key={key}>
              <div><div className="sous-label">{t(`settings.${key}`)}</div>
                <div className="sous-caption">{t(`settings.${key}Hint`)}</div></div>
              <button type="button" role="switch" aria-label={t(`settings.${key}`)} className="sous-switch"
                aria-checked={enabled ? "true" : "false"} disabled={settingsBusy || !settings.events_enabled || unsupported}
                onClick={() => act("update_settings", { [setting]: !enabled })}><span /></button>
            </div>
          })}
          {connection === "connected" && !state.kitchen_supported && <div className="sous-caption">{t("settings.upgradeKitchen")}</div>}
          {connection === "connected" && !state.mixing_supported && <div className="sous-caption">{t("settings.upgradeMixing")}</div>}
          <div className="sous-label">{t("settings.conversation")}</div>
          <div className="sous-setting-row">
            <div><div className="sous-label">{t("settings.chatter")}</div><div className="sous-caption">{t("settings.chatterHint")}</div></div>
            <button type="button" role="switch" aria-label={t("settings.chatter")} className="sous-switch"
              aria-checked={settings.chatter_enabled ? "true" : "false"} disabled={settingsBusy || !settings.events_enabled}
              onClick={() => act("update_settings", { chatter_enabled: !settings.chatter_enabled })}><span /></button>
          </div>
          <div className="sous-label">{t("settings.frequency")}</div>
          <div className="sous-frequency" role="radiogroup" aria-label={t("settings.frequency")}>
            {["quiet", "balanced", "lively"].map(value => <button type="button" role="radio"
              className="sous-mode-button" aria-checked={settings.frequency === value ? "true" : "false"}
              disabled={settingsBusy || !settings.events_enabled || !settings.chatter_enabled}
              onClick={() => { if (value !== settings.frequency) return act("update_settings", { frequency: value }) }}>
              <strong>{t(`frequency.${value}`)}</strong><span>{t(`frequency.${value}Hint`)}</span>
            </button>)}
          </div>
          <div className="sous-caption">{t("settings.pacingHint")}</div>
          {connection === "connected" && settings.events_enabled && !state.state_supported
            && <div className="sous-caption">{t("settings.upgradeState")}</div>}
        </div>
        <div className="sous-footer">
          <div className="sous-hint">{t(desired ? "panel.retryHint" : "panel.manualHint")}</div>
          <div className="sous-buttons">
            <Button tone={desired ? "default" : "primary"} disabled={busy || (desired ? !canDisconnect : !canConnect)} onClick={() => act(desired ? "disconnect" : "connect")}>
              {t(busy ? "actions.working" : desired ? "actions.disconnect" : "actions.connect")}
            </Button>
          </div>
        </div>
      </div>
      {!canConnect && <div className="sous-notice">{t("panel.startPlugin")}</div>}
      {state.last_error && <div className="sous-notice">{t(`errors.${state.last_error.code}`)} {t("errors.logs")}</div>}
      <div className="sous-meta">
        <span>{t("panel.refreshHint")}</span>
        {state.server_version && <span>{t("panel.version", { version: state.server_version })}</span>}
        {state.session_id && <span>{t("panel.session", { id: state.session_id.slice(0, 8) })}</span>}
      </div>
      <Modal open={!!problem} title={t(problem?.code === "game_not_running" ? "errors.gameTitle" : "errors.title")}
        onClose={() => { setProblem(null) }} footer={<Button tone="primary" onClick={() => { setProblem(null) }}>{t("actions.ok")}</Button>}>
        <div className="sous-dialog"><p>{problem ? t(`errors.${problem.code}`) : ""}</p><small>{t("errors.logs")}</small></div>
      </Modal>
    </div>
  </Page>
}
