import {
  Alert,
  Button,
  ButtonGroup,
  Card,
  Columns,
  EmptyState,
  Field,
  Inline,
  Input,
  List,
  NumberInput,
  Page,
  RefreshButton,
  Stack,
  StatCard,
  StatusBadge,
  Switch,
  Tabs,
  Text,
  Tip,
  useEffect,
  useForm,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

const OPENBILICLAW_EXTENSION_URL =
  "https://chromewebstore.google.com/detail/openbiliclaw/cdfjfkdjjhdaccbldipkjhpibnfbiamg"

function openExternalUrl(url: string): void {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      { type: "neko-hosted-surface-open-external", payload: { url } },
      "*",
    )
    return
  }
  window.open(url, "_blank", "noopener,noreferrer")
}

type RecommendationConfig = {
  enabled?: boolean
  shadow_mode?: boolean
  background_llm?: boolean
  daily_limit?: number
  min_interval_minutes?: number
  quiet_start?: string
  quiet_end?: string
  score_threshold?: number
  max_idle_seconds?: number
  min_user_silence_minutes?: number
  web_search?: boolean
  bilibili?: boolean
  openbiliclaw_enabled?: boolean
  openbiliclaw_port?: number
  openbiliclaw_backend_port?: number
}

type ProfilePreference = {
  domain?: string
  weight?: number
  specifics?: Array<{ name?: string; weight?: number }>
}

type OpenBiliClawProfile = {
  initialized?: boolean
  personality_portrait?: string
  current_phase?: string
  core_traits?: string[]
  deep_needs?: string[]
  values?: string[]
  motivational_drivers?: string[]
  cognitive_style?: string[]
  mbti?: { type?: string; confidence?: number }
  likes?: ProfilePreference[]
  dislikes?: ProfilePreference[]
}

type Candidate = {
  id?: string
  title?: string
  source?: string
  score?: number
  matched_interests?: string[]
}

type HistoryItem = Candidate & {
  candidate_id?: string
  timestamp?: number
  mode?: string
  outcome?: string
}

type DashboardState = {
  ready?: boolean
  store_enabled?: boolean
  config?: RecommendationConfig
  candidates?: Candidate[]
  history?: HistoryItem[]
  last_run?: {
    timestamp?: number
    messages_processed?: number
    discovered?: number
    delivery?: {
      reason?: string
      mode?: string
      submitted?: boolean
      candidate_title?: string
      candidate_source?: string
    }
  }
  metrics?: {
    profile_domain_count?: number
    local_interest_count?: number
    candidate_count?: number
    today_handoff_count?: number
    platform_event_count?: number
  }
  openbiliclaw?: {
    enabled?: boolean
    running?: boolean
    endpoint?: string
    backend_endpoint?: string
    connected_clients?: number
    last_error?: string
    cookie_ingest?: boolean
    compatibility_level?: string
    events?: {
      accepted?: number
      duplicate?: number
      rejected?: number
      by_platform?: Record<string, number>
      last_event_at?: number
    }
    recommendations?: {
      last_sync_at?: number
      last_error?: string
      last_fetched?: number
      total_imported?: number
      endpoint?: string
    }
    profile?: {
      last_sync_at?: number
      last_error?: string
      endpoint?: string
      data?: OpenBiliClawProfile
    }
  }
}

const defaultForm = {
  enabled: false,
  shadow_mode: true,
  background_llm: true,
  web_search: true,
  bilibili: false,
  openbiliclaw_enabled: false,
  openbiliclaw_port: 8421,
  openbiliclaw_backend_port: 8420,
  daily_limit: 2,
  min_interval_minutes: 240,
  min_user_silence_minutes: 20,
  max_idle_seconds: 900,
  score_threshold: 0.72,
  quiet_start: "23:00",
  quiet_end: "09:00",
}

type FormValues = typeof defaultForm

function actionById(actions: HostedAction[], id: string): HostedAction | undefined {
  return actions.find((action) => action.id === id || action.entry_id === id)
}

function formatScore(value?: number): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-"
}

function formatTimestamp(value: number | undefined, locale: string): string {
  if (!value) return "-"
  return new Date(value * 1000).toLocaleString(locale, {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export default function ProactiveRecommenderPanel(props: PluginSurfaceProps<DashboardState>) {
  const { t } = props
  const safeState = props.state || {}
  const config = safeState.config || {}
  const candidates = Array.isArray(safeState.candidates) ? safeState.candidates : []
  const history = Array.isArray(safeState.history) ? safeState.history : []
  const metrics = safeState.metrics || {}
  const lastRun = safeState.last_run || {}
  const compatibility = safeState.openbiliclaw || {}
  const platformEvents = compatibility.events || {}
  const recommendationSync = compatibility.recommendations || {}
  const profileSync = compatibility.profile || {}
  const profile = profileSync.data || {}
  const profileLikes = Array.isArray(profile.likes) ? profile.likes : []
  const coreTraits = Array.isArray(profile.core_traits) ? profile.core_traits : []
  const deepNeeds = Array.isArray(profile.deep_needs) ? profile.deep_needs : []
  const updateAction = actionById(props.actions || [], "update_recommendation_settings")
  const runAction = actionById(props.actions || [], "recommendation_run_once")
  const form = useForm<FormValues>(defaultForm)
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    form.setValues({
      enabled: !!config.enabled,
      shadow_mode: config.shadow_mode !== false,
      background_llm: config.background_llm !== false,
      web_search: config.web_search !== false,
      bilibili: !!config.bilibili,
      openbiliclaw_enabled: !!config.openbiliclaw_enabled,
      openbiliclaw_port: Number(config.openbiliclaw_port ?? 8421),
      openbiliclaw_backend_port: Number(config.openbiliclaw_backend_port ?? 8420),
      daily_limit: Number(config.daily_limit ?? 2),
      min_interval_minutes: Number(config.min_interval_minutes ?? 240),
      min_user_silence_minutes: Number(config.min_user_silence_minutes ?? 20),
      max_idle_seconds: Number(config.max_idle_seconds ?? 900),
      score_threshold: Number(config.score_threshold ?? 0.72),
      quiet_start: String(config.quiet_start || "23:00"),
      quiet_end: String(config.quiet_end || "09:00"),
    })
  }, [
    config.enabled,
    config.shadow_mode,
    config.background_llm,
    config.web_search,
    config.bilibili,
    config.openbiliclaw_enabled,
    config.openbiliclaw_port,
    config.openbiliclaw_backend_port,
    config.daily_limit,
    config.min_interval_minutes,
    config.min_user_silence_minutes,
    config.max_idle_seconds,
    config.score_threshold,
    config.quiet_start,
    config.quiet_end,
  ])

  async function saveSettings() {
    if (!updateAction || saving) return
    setSaving(true)
    try {
      await props.api.call("update_recommendation_settings", { ...form.values })
      await props.api.refresh()
      toast.success(t("panel.saved"))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSaving(false)
    }
  }

  async function runOnce() {
    if (!runAction || running) return
    setRunning(true)
    try {
      await props.api.call("recommendation_run_once", {})
      await props.api.refresh()
      toast.success(t("panel.runCompleted"))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setRunning(false)
    }
  }

  const modeTone = !config.enabled ? "warning" : config.shadow_mode ? "info" : "success"
  const modeLabel = !config.enabled
    ? t("panel.mode.disabled")
    : config.shadow_mode
      ? t("panel.mode.shadow")
      : t("panel.mode.live")
  const deliveryReason = String(lastRun.delivery?.reason || "never_run")
  const bridgeTone = !compatibility.enabled
    ? "warning"
    : recommendationSync.last_error
      ? "danger"
      : recommendationSync.last_sync_at
        ? "success"
        : compatibility.running
          ? "info"
          : "danger"
  const bridgeLabel = !compatibility.enabled
    ? t("panel.bridge.disabled")
    : recommendationSync.last_error
      ? t("panel.bridge.syncFailed")
      : recommendationSync.last_sync_at
        ? t("panel.bridge.running")
        : compatibility.running
          ? t("panel.bridge.waiting")
          : t("panel.bridge.failed")
  const platformSummary = Object.entries(platformEvents.by_platform || {})
    .sort((left, right) => right[1] - left[1])
    .map(([platform, count]) => `${platform} ${count}`)
    .join(" · ")
  const reasonKey = {
    never_run: "panel.reason.never",
    no_eligible_candidate: "panel.reason.noCandidate",
    shadow_mode: "panel.reason.shadow",
    handoff_submitted: "panel.reason.submitted",
    rejected: "panel.reason.rejected",
    global_proactive_disabled: "panel.reason.globalDisabled",
    quiet_hours: "panel.reason.quiet",
    private_foreground: "panel.reason.private",
    user_away: "panel.reason.away",
    recent_user_activity: "panel.reason.recent",
    daily_limit: "panel.reason.daily",
    minimum_interval: "panel.reason.interval",
    ignored_streak: "panel.reason.cooldown",
  }[deliveryReason] || "panel.reason.blocked"

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Columns cols={2} minColumnWidth={220} fluid>
        <Inline align="center">
          <StatusBadge tone={modeTone} label={modeLabel} />
        </Inline>
        <ButtonGroup>
          <RefreshButton label={t("panel.refresh")} tone="default" />
          <Button tone="primary" disabled={!runAction || running || !config.enabled} onClick={runOnce}>
            {running ? t("panel.running") : t("panel.runOnce")}
          </Button>
        </ButtonGroup>
      </Columns>

      {!config.enabled ? (
        <Alert tone="warning">{t("panel.alert.disabled")}</Alert>
      ) : config.shadow_mode ? (
        <Alert tone="info">{t("panel.alert.shadow")}</Alert>
      ) : (
        <Alert tone="success">{t("panel.alert.live")}</Alert>
      )}

      <Columns cols={4} minColumnWidth={150} fluid>
        <StatCard label={t("panel.stats.mode")} value={modeLabel} />
        <StatCard label={t("panel.stats.profileDomains")} value={metrics.profile_domain_count || 0} />
        <StatCard label={t("panel.stats.candidates")} value={metrics.candidate_count || 0} />
        <StatCard label={t("panel.stats.today")} value={metrics.today_handoff_count || 0} />
      </Columns>

      <Card title={t("panel.bridge.title")}>
        <Stack>
          <Columns cols={2} minColumnWidth={220} fluid>
            <Inline align="center">
              <StatusBadge tone={bridgeTone} label={bridgeLabel} />
            </Inline>
            <ButtonGroup>
              <Button tone="default" onClick={() => openExternalUrl(OPENBILICLAW_EXTENSION_URL)}>
                {t("panel.bridge.downloadExtension")}
              </Button>
            </ButtonGroup>
          </Columns>
          <Text>{`${t("panel.bridge.backend")}: ${String(compatibility.backend_endpoint || "http://127.0.0.1:8420")}`}</Text>
          <Text>{`${t("panel.bridge.ingress")}: ${String(compatibility.endpoint || "http://127.0.0.1:8421")}`}</Text>
          <Columns cols={4} minColumnWidth={150} fluid>
            <StatCard label={t("panel.bridge.fetched")} value={recommendationSync.last_fetched || 0} />
            <StatCard label={t("panel.bridge.imported")} value={recommendationSync.total_imported || 0} />
            <StatCard label={t("panel.bridge.events")} value={platformEvents.accepted || 0} />
            <StatCard label={t("panel.bridge.lastSync")} value={formatTimestamp(recommendationSync.last_sync_at, props.locale)} />
          </Columns>
          {platformSummary ? <Tip>{platformSummary}</Tip> : <Text>{t("panel.bridge.empty")}</Text>}
          {recommendationSync.last_error ? <Alert tone="danger">{String(recommendationSync.last_error)}</Alert> : null}
          {profileSync.last_error ? <Alert tone="danger">{`${t("panel.profile.syncFailed")}: ${String(profileSync.last_error)}`}</Alert> : null}
          {compatibility.last_error ? <Alert tone="danger">{String(compatibility.last_error)}</Alert> : null}
        </Stack>
      </Card>

      <Card title={t("panel.lastRun.title")}>
        <Stack>
          <Columns cols={4} minColumnWidth={150} fluid>
            <StatCard label={t("panel.lastRun.result")} value={t(reasonKey)} />
            <StatCard label={t("panel.lastRun.messages")} value={lastRun.messages_processed || 0} />
            <StatCard label={t("panel.lastRun.discovered")} value={lastRun.discovered || 0} />
            <StatCard label={t("panel.lastRun.time")} value={formatTimestamp(lastRun.timestamp, props.locale)} />
          </Columns>
          {lastRun.delivery?.candidate_title ? (
            <Tip>{`${lastRun.delivery.candidate_title} · ${lastRun.delivery.candidate_source || "-"}`}</Tip>
          ) : null}
        </Stack>
      </Card>

      <Card title={t("panel.inspection.title")}>
        <Tabs
          id="recommendation-inspection"
          items={[
            {
              id: "profile",
              label: t("panel.inspection.profile"),
              content: profile.initialized ? (
                <Stack>
                  <Alert tone="success">{t("panel.profile.synced")}</Alert>
                  <Columns cols={3} minColumnWidth={150} fluid>
                    <StatCard label={t("panel.profile.mbti")} value={profile.mbti?.type || "-"} />
                    <StatCard label={t("panel.profile.domains")} value={profileLikes.length} />
                    <StatCard label={t("panel.profile.traits")} value={coreTraits.length} />
                  </Columns>
                  {profile.personality_portrait ? (
                    <Stack gap={6}>
                      <Text>{t("panel.profile.portrait")}</Text>
                      <Tip>{profile.personality_portrait}</Tip>
                    </Stack>
                  ) : null}
                  {coreTraits.length ? (
                    <Stack gap={6}>
                      <Text>{t("panel.profile.coreTraits")}</Text>
                      <Text>{coreTraits.join(" · ")}</Text>
                    </Stack>
                  ) : null}
                  {profileLikes.length ? (
                    <Stack gap={6}>
                      <Text>{t("panel.profile.likes")}</Text>
                      <List
                        items={profileLikes}
                        render={(item) => (
                          <Stack gap={6}>
                            <Inline justify="space-between">
                              <span>{item.domain || "-"}</span>
                              <StatusBadge tone="success" label={formatScore(item.weight)} />
                            </Inline>
                            <Text>{(item.specifics || []).map((specific) => specific.name).filter(Boolean).join(" / ") || "-"}</Text>
                          </Stack>
                        )}
                      />
                    </Stack>
                  ) : null}
                  {profile.current_phase ? (
                    <Stack gap={6}>
                      <Text>{t("panel.profile.currentPhase")}</Text>
                      <Tip>{profile.current_phase}</Tip>
                    </Stack>
                  ) : null}
                  {deepNeeds.length ? (
                    <Stack gap={6}>
                      <Text>{t("panel.profile.deepNeeds")}</Text>
                      <List items={deepNeeds} render={(item) => <span>{item}</span>} />
                    </Stack>
                  ) : null}
                  <Text>{`${t("panel.profile.lastSync")}: ${formatTimestamp(profileSync.last_sync_at, props.locale)}`}</Text>
                </Stack>
              ) : (
                <Stack>
                  <EmptyState title={t("panel.empty.profile")} />
                  <Text>{t("panel.profile.emptyHelp")}</Text>
                </Stack>
              ),
            },
            {
              id: "candidates",
              label: t("panel.inspection.candidates"),
              content: candidates.length ? (
                <List
                  items={candidates}
                  render={(item) => (
                    <Stack gap={6}>
                      <Inline justify="space-between">
                        <span>{item.title || "-"}</span>
                        <StatusBadge tone="primary" label={formatScore(item.score)} />
                      </Inline>
                      <Text>{`${item.source || "-"} · ${(item.matched_interests || []).join(" / ") || "-"}`}</Text>
                    </Stack>
                  )}
                />
              ) : <EmptyState title={t("panel.empty.candidates")} />,
            },
            {
              id: "history",
              label: t("panel.inspection.history"),
              content: history.length ? (
                <List
                  items={[...history].reverse()}
                  render={(item) => (
                    <Stack gap={6}>
                      <Inline justify="space-between">
                        <span>{item.title || item.candidate_id || "-"}</span>
                        <StatusBadge
                          tone={item.outcome === "engaged" ? "success" : item.outcome === "rejected" ? "danger" : "info"}
                          label={
                            item.outcome === "handoff_submitted"
                              ? t("panel.reason.submitted")
                              : item.outcome === "shadow"
                                ? t("panel.reason.shadow")
                                : item.outcome || item.mode || "-"
                          }
                        />
                      </Inline>
                      <Text>{formatTimestamp(item.timestamp, props.locale)}</Text>
                    </Stack>
                  )}
                />
              ) : <EmptyState title={t("panel.empty.history")} />,
            },
          ]}
        />
      </Card>

      <Columns cols={2} minColumnWidth={300} fluid>
        <Card title={t("panel.config.behavior")}>
          <Stack>
            <Switch checked={form.values.enabled} label={t("panel.fields.enabled")} onChange={(value) => form.setField("enabled", value)} />
            <Switch checked={form.values.shadow_mode} label={t("panel.fields.shadow")} onChange={(value) => form.setField("shadow_mode", value)} />
            <Switch checked={form.values.background_llm} label={t("panel.fields.llm")} onChange={(value) => form.setField("background_llm", value)} />
            <Field label={t("panel.fields.sources")} help={t("panel.fields.sourcesHelp")}>
              <Stack gap={8}>
                <Switch checked={form.values.web_search} label={t("panel.fields.web")} onChange={(value) => form.setField("web_search", value)} />
                <Switch checked={form.values.bilibili} label={t("panel.fields.bilibili")} onChange={(value) => form.setField("bilibili", value)} />
              </Stack>
            </Field>
            <Switch checked={form.values.openbiliclaw_enabled} label={t("panel.bridge.enable")} onChange={(value) => form.setField("openbiliclaw_enabled", value)} />
            <Field label={t("panel.bridge.backendPort")} help={t("panel.bridge.backendPortHelp")}>
              <NumberInput value={form.values.openbiliclaw_backend_port} min={1024} max={65535} onChange={(value) => form.setField("openbiliclaw_backend_port", Number(value))} />
            </Field>
            <Field label={t("panel.bridge.ingressPort")} help={t("panel.bridge.ingressPortHelp")}>
              <NumberInput value={form.values.openbiliclaw_port} min={1024} max={65535} onChange={(value) => form.setField("openbiliclaw_port", Number(value))} />
            </Field>
            <Tip>{t("panel.config.safeTip")}</Tip>
          </Stack>
        </Card>

        <Card title={t("panel.config.gates")}>
          <Stack>
            <Columns cols={2} minColumnWidth={130} fluid>
              <Field label={t("panel.fields.dailyLimit")}>
                <NumberInput value={form.values.daily_limit} min={0} max={20} onChange={(value) => form.setField("daily_limit", Number(value))} />
              </Field>
              <Field label={t("panel.fields.interval")}>
                <NumberInput value={form.values.min_interval_minutes} min={0} max={1440} onChange={(value) => form.setField("min_interval_minutes", Number(value))} />
              </Field>
              <Field label={t("panel.fields.silence")}>
                <NumberInput value={form.values.min_user_silence_minutes} min={0} max={1440} onChange={(value) => form.setField("min_user_silence_minutes", Number(value))} />
              </Field>
              <Field label={t("panel.fields.idle")}>
                <NumberInput value={form.values.max_idle_seconds} min={0} max={86400} onChange={(value) => form.setField("max_idle_seconds", Number(value))} />
              </Field>
              <Field label={t("panel.fields.threshold")}>
                <NumberInput value={form.values.score_threshold} min={0} max={1} step={0.01} onChange={(value) => form.setField("score_threshold", Number(value))} />
              </Field>
            </Columns>
            <Columns cols={2} minColumnWidth={130} fluid>
              <Field label={t("panel.fields.quietStart")}>
                <Input value={form.values.quiet_start} placeholder="23:00" onChange={(value) => form.setField("quiet_start", value)} />
              </Field>
              <Field label={t("panel.fields.quietEnd")}>
                <Input value={form.values.quiet_end} placeholder="09:00" onChange={(value) => form.setField("quiet_end", value)} />
              </Field>
            </Columns>
            <Button tone="success" disabled={!updateAction || saving} onClick={saveSettings}>
              {saving ? t("panel.saving") : t("panel.save")}
            </Button>
          </Stack>
        </Card>
      </Columns>

      <Columns cols={2} minColumnWidth={300} fluid>
        <Card title={t("panel.data.title")}>
          <List
            items={[
              t("panel.data.recentConversation"),
              t("panel.data.browserEvents"),
              t("panel.data.profile"),
              t("panel.data.search"),
              t("panel.data.activity"),
              t("panel.data.feedback"),
            ]}
            render={(item, index) => <span>{`${index + 1}. ${item}`}</span>}
          />
        </Card>
        <Card title={t("panel.privacy.title")}>
          <Stack>
            <Alert tone="success">{t("panel.privacy.raw")}</Alert>
            <span>{t("panel.privacy.excluded")}</span>
            <span>{t("panel.privacy.browser")}</span>
            <span>{t("panel.privacy.boundary")}</span>
          </Stack>
        </Card>
      </Columns>
    </Page>
  )
}
