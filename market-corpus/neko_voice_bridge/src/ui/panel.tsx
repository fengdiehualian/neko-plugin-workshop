import {Alert, Button, Card, Field, FileDownload, Inline, Page, Stack, StatusBadge, Text, Textarea, useEffect, useRef, useState, useToast} from "@neko/plugin-ui"
import type {PluginSurfaceProps} from "@neko/plugin-ui"

type State = {
  phase?: string; connected?: boolean; paired?: boolean; character_name?: string;
  rtc_connected?: boolean; members?: number; processed?: number;
  output_transport?: string; cable_ready?: boolean; wake_filter_enabled?: boolean;
  error?: string; speech_error?: string; action_token?: string; plugin_version?: string;
  speech_error_history?: {at_ms: number; code: string; stage: string; exception: string}[];
  speech_diagnostic?: {stage?: string; exception?: string; missing_symbol?: string; http_status?: number; ws_rcvd_code?: number; ws_sent_code?: number};
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const [code, setCode] = useState("")
  const [busy, setBusy] = useState(false)
  const [stale, setStale] = useState(false)
  const toast = useToast()
  const refresh = useRef(props.api.refresh)
  refresh.current = props.api.refresh
  useEffect(() => {
    let alive = true, pending = false
    const timer = setInterval(async () => {
      if (pending) return
      pending = true
      try { await refresh.current(); if (alive) setStale(false) }
      catch { if (alive) setStale(true) }
      finally { pending = false }
    }, 1000)
    return () => { alive = false; clearInterval(timer) }
  }, [])
  const state = props.state || {}
  const phase = stale ? "reconnecting" : state.phase || "disconnected"
  const connected = !stale && !!state.connected
  const available = !!state.action_token && (props.actions || []).some(a => (a.id === "ui_pair" || a.entry_id === "ui_pair"))
  const t = props.t

  async function act(action: string) {
    if (action === "pair" && !code.trim()) {toast.error(t("panel.empty_code")); return}
    if (!available) {toast.error(t("panel.unavailable")); return}
    setBusy(true)
    try {
      if (action === "pair") {
        await props.api.call("ui_pair", {connection_code: code.trim(), ui_token: state.action_token}, {userInitiated: true})
        setCode("")
      } else {
        await props.api.call("ui_control", {action, ui_token: state.action_token}, {userInitiated: true})
      }
      await props.api.refresh()
      toast.success(t("panel.updated"))
    } catch (error) {toast.error(error instanceof Error ? error.message : t("errors.control"))}
    finally {setBusy(false)}
  }
  return <Page title={t("panel.title")} subtitle={t("panel.subtitle")}><Stack>
    <Card title={t("guide.download_title")}><Stack>
      <Text>{t("guide.download_help")}</Text>
      <FileDownload href="https://github.com/qiguang113/n.e.k.o_plugin_neko_voice_bridge/releases" label={t("guide.download_button")} openExternal />
      <Text>{t("guide.download_match")}</Text>
      <Text>{t("guide.connect_step")}</Text>
    </Stack></Card>
    <Card title={t("panel.pair_title")}><Stack>
      <Text>{t("panel.pair_help")}</Text>
      <Field label={t("panel.code")}><Textarea value={code} onChange={setCode} placeholder="http://127.0.0.1:18765/#token=…" /></Field>
      <Button tone="primary" disabled={busy} onClick={() => act("pair")}>{t("actions.pair")}</Button>
      <Text>{t("panel.private")}</Text>
      <Text>{t("panel.transport")}</Text>
    </Stack></Card>
    <Card title={t("panel.connection")}><Stack>
      <Inline justify="space-between" align="center"><Text>{t("panel.bridge")}</Text><StatusBadge tone={connected ? "success" : "warning"} label={t("phase." + phase)} /></Inline>
      {!available ? <Alert tone="warning">{t("panel.unavailable")}</Alert> : null}
      <Text>{t("panel.version")} {state.plugin_version || "—"}</Text>
      <Text>{t(state.output_transport === "cable" ? state.cable_ready ? "panel.cable_ready" : "panel.cable_wait" : "panel.websocket_output")}</Text>
      <Text>{t("panel.character")} {state.character_name || t("panel.wait_character")}</Text>
      <Text>{t("panel.automatic_name")}</Text>
      <Alert tone="warning">{t(state.wake_filter_enabled === false ? "panel.wake_disabled" : "panel.host_voice_note")}</Alert>
      <Text>{t("panel.host_voice_identity")}</Text>
      <Text>{t("panel.channel")} {state.rtc_connected ? t("panel.in_channel") : t("panel.not_in_channel")} · {t("panel.members")} {state.members || 0}</Text>
      {state.error ? <Alert tone="warning">{t(connected ? "panel.reply_error" : "panel.check_connection")} {state.error}</Alert> : null}
      {state.speech_error ? <Alert tone="warning">{t(connected ? "panel.speech_error_connected" : "panel.speech_error")} {state.speech_error}{["NEKO_HOST_VOICE_UPDATE_EXE", "NEKO_HOST_AUDIO_FORMAT_UNSUPPORTED", "NEKO_HOST_NO_TRANSCRIPT", "NEKO_HOST_NO_REPLY", "NEKO_HOST_NO_REPLY_AUDIO", "NEKO_HOST_AUDIO_INCOMPLETE", "NEKO_HOST_TURN_NOT_FINISHED"].includes(state.speech_error) ? " · " + t("panel.error." + state.speech_error) : ""}</Alert> : null}
      {state.speech_diagnostic ? <Alert tone="warning">{t("panel.diagnostic")} {state.speech_diagnostic.stage} · {state.speech_diagnostic.exception} {state.speech_diagnostic.missing_symbol || ""} {state.speech_diagnostic.http_status || ""} {state.speech_diagnostic.ws_rcvd_code ? t("panel.ws_close") + " " + state.speech_diagnostic.ws_rcvd_code : ""}</Alert> : null}
      {(state.speech_error_history || []).length ? <Card title="本次连接的错误记录（恢复后仍保留）"><Stack>
        {(state.speech_error_history || []).slice(-5).reverse().map((item, index) => <Text key={String(item.at_ms) + index}>{new Date(item.at_ms).toLocaleTimeString()} · {item.code} · {item.stage} · {item.exception}</Text>)}
        <Text>插件日志中保留完整记录；语音桥 EXE 的“导出诊断”可保存频道发送状态。</Text>
      </Stack></Card> : null}
      <Button disabled={busy} onClick={async () => {try {await props.api.refresh()} catch {toast.error(t("panel.unavailable"))}}}>{t("panel.refresh")}</Button>
      <Inline>
        <Button disabled={busy || !connected || !state.rtc_connected || phase === "receiving"} tone="primary" onClick={() => act("start")}>{t("actions.start")}</Button>
        <Button disabled={busy || !connected || phase !== "receiving"} onClick={() => act("stop")}>{t("actions.stop")}</Button>
        <Button disabled={busy || !available || !state.paired} onClick={() => act("reconnect")}>{t("actions.reconnect")}</Button>
        <Button disabled={busy || !available || !state.paired || phase === "disconnected"} onClick={() => act("disconnect")}>{t("actions.disconnect")}</Button>
      </Inline>
    </Stack></Card>

  </Stack></Page>
}
