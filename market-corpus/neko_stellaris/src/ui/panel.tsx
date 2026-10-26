/**
 * Hosted UI panel for the Stellaris plugin.
 *
 * Read-only by design: every number here comes from `@ui.context(id="dashboard")`,
 * which is built from the same helpers the plugin entries use, and every button is
 * a plain call to an existing entry. Nothing is recomputed in the panel, so the
 * panel cannot disagree with `stellaris_status` or with the CLI.
 */
import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Tip,
  Warning,
  Alert,
  StatCard,
  StatusBadge,
  KeyValue,
  Divider,
  Button,
  Toolbar,
  ToolbarGroup,
  RefreshButton,
  Progress,
  EmptyState,
  useEffect,
  useToast,
  useConfirm,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type InstallView = {
  kind?: string
  path?: string
  remote_id?: string
  version?: string
  files?: number
  size_mib?: number
  clips?: number
  registered?: boolean
  summary?: string
}

type FreshnessView = {
  state?: string
  summary?: string
  needs_rebuild?: boolean
  clips_to_generate?: number
}

type JobView = {
  id?: string
  state?: string
  percent?: number
  phase?: string
}

type LogHealthView = {
  available?: boolean
  clean?: boolean
  size_kib?: number
  counts?: Record<string, number>
}

type DuplicateView = {
  both_present?: boolean
  both_enabled?: boolean
  enabled?: string[]
}

type DashboardState = {
  mod_id?: string
  version?: string
  user_dir?: string
  install?: InstallView
  freshness?: FreshnessView
  published?: { remote_id?: string; path?: string }
  duplicate?: DuplicateView
  clips?: { total?: number; cached?: number; missing?: number; title?: string }
  tts?: { url?: string; voice_id?: string; source?: string; key_set?: boolean }
  live?: { state?: string }
  jobs?: JobView[]
  running_job?: JobView | null
  log_health?: LogHealthView
  errors?: string[]
  error?: string
}

/** Entry ids the panel drives. They are ordinary `@plugin_entry`s. */
const ENTRY = {
  build: "stellaris_build_voice",
  check: "stellaris_voice_check",
  probe: "stellaris_tts_probe",
  uninstall: "stellaris_uninstall",
}

function findAction(actions: HostedAction[] | undefined, entryId: string): HostedAction | undefined {
  return (actions || []).find((action) => action.id === entryId || action.entry_id === entryId)
}

/** Entries answer with an envelope; the payload of interest may sit in `.result`. */
function unwrap(envelope: unknown): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    const view = envelope as Record<string, any>
    if (view.result && typeof view.result === "object") return view.result
    return view
  }
  return {}
}

function succeeded(result: Record<string, any>): boolean {
  if (!result || Object.keys(result).length === 0) return true
  if (result.ok === false) return false
  if (typeof result.error === "string" && result.error.length > 0) return false
  return true
}

export default function NekoStellarisPanel(props: PluginSurfaceProps<DashboardState>) {
  const { state, actions, t } = props
  const toast = useToast()
  const confirm = useConfirm()

  const view = state || {}
  const install = view.install || {}
  const fresh = view.freshness || {}
  const dup = view.duplicate || {}
  const clips = view.clips || {}
  const tts = view.tts || {}
  const log = view.log_health || {}
  const running = view.running_job || null
  const busy = !!running

  const buildAction = findAction(actions, ENTRY.build)
  const checkAction = findAction(actions, ENTRY.check)
  const probeAction = findAction(actions, ENTRY.probe)
  const uninstallAction = findAction(actions, ENTRY.uninstall)

  // A build is a background job; the panel has to re-read the context to see it move.
  useEffect(() => {
    if (!busy) return
    const timer = setInterval(() => {
      Promise.resolve(props.api.refresh()).catch(() => undefined)
    }, 2000)
    return () => clearInterval(timer)
  }, [busy])

  async function callEntry(entryId: string, payload: Record<string, any>, doneKey: string) {
    try {
      const result = unwrap(await props.api.call(entryId, payload))
      if (!succeeded(result)) {
        toast.error(String(result.error || result.message || t("panel.toast.failed")))
        return
      }
      toast.success(t(doneKey))
      await props.api.refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("panel.toast.failed"))
    }
  }

  async function rebuild() {
    await callEntry(ENTRY.build, { install: true }, "panel.toast.rebuildStarted")
  }

  async function check() {
    await callEntry(ENTRY.check, { action: "check" }, "panel.toast.checked")
  }

  async function probe() {
    await callEntry(ENTRY.probe, {}, "panel.toast.probed")
  }

  async function uninstall() {
    const agreed = await confirm({
      title: t("panel.confirm.uninstall.title"),
      message: t("panel.confirm.uninstall.body"),
      tone: "danger",
      confirmLabel: t("panel.action.uninstall"),
    })
    if (!agreed) return
    await callEntry(ENTRY.uninstall, { voice: true, bridge: true }, "panel.toast.uninstalled")
  }

  const installKind =
    install.kind === "workshop"
      ? t("panel.install.kind.workshop")
      : install.kind === "local"
        ? t("panel.install.kind.local")
        : t("panel.value.notInstalled")

  const freshnessTone = fresh.needs_rebuild ? "warning" : fresh.state === "fresh" ? "success" : "default"

  if (view.error) {
    return (
      <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Alert tone="danger">{view.error}</Alert>
        <Toolbar>
          <ToolbarGroup>
            <RefreshButton label={t("panel.action.refresh")} />
          </ToolbarGroup>
        </Toolbar>
      </Page>
    )
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      {dup.both_enabled ? (
        <Warning>
          <Stack>
            <Text>{t("panel.duplicate.bothEnabled.title")}</Text>
            <Text>{t("panel.duplicate.bothEnabled.body")}</Text>
          </Stack>
        </Warning>
      ) : dup.both_present ? (
        <Warning>
          <Stack>
            <Text>{t("panel.duplicate.bothPresent.title")}</Text>
            <Text>{t("panel.duplicate.bothPresent.body")}</Text>
          </Stack>
        </Warning>
      ) : null}

      <Grid cols={4}>
        <StatCard label={t("panel.stat.install")} value={installKind} />
        <StatCard label={t("panel.stat.version")} value={view.version || "-"} />
        <StatCard label={t("panel.stat.clips")} value={clips.total ?? 0} />
        <StatCard
          label={t("panel.stat.freshness")}
          value={
            <StatusBadge
              tone={freshnessTone}
              label={fresh.needs_rebuild ? t("panel.value.stale") : t("panel.value.fresh")}
            />
          }
        />
      </Grid>
      <Tip>{t("panel.stat.cachedHint", { cached: clips.cached ?? 0, missing: clips.missing ?? 0 })}</Tip>

      {busy ? (
        <Card title={t("panel.card.build")}>
          <Stack>
            <Progress
              label={running?.phase || t("panel.jobs.running")}
              value={Math.round(Number(running?.percent || 0))}
            />
            <Tip>{t("panel.card.buildHint")}</Tip>
          </Stack>
        </Card>
      ) : null}

      <Card title={t("panel.card.freshness")}>
        <Stack>
          <Text>{fresh.summary || t("panel.value.unknown")}</Text>
          <Toolbar>
            <ToolbarGroup>
              <Button disabled={!buildAction || busy} tone="success" onClick={rebuild}>
                {t("panel.action.rebuild")}
              </Button>
              <Button disabled={!checkAction} onClick={check}>
                {t("panel.action.check")}
              </Button>
            </ToolbarGroup>
          </Toolbar>
          <Tip>{t("panel.action.rebuildHint")}</Tip>
        </Stack>
      </Card>

      <Card title={t("panel.card.install")}>
        {install.kind ? (
          <KeyValue
            items={[
              { key: "kind", label: t("panel.install.kind"), value: installKind },
              { key: "version", label: t("panel.install.version"), value: install.version || "-" },
              { key: "files", label: t("panel.install.files"), value: install.files ?? 0 },
              { key: "size", label: t("panel.install.size"), value: `${install.size_mib ?? 0} MiB` },
              { key: "clips", label: t("panel.install.clips"), value: install.clips ?? 0 },
              {
                key: "registered",
                label: t("panel.install.registered"),
                value: install.registered ? t("panel.value.yes") : t("panel.value.no"),
              },
              { key: "path", label: t("panel.install.path"), value: install.path || "-" },
              ...(install.remote_id
                ? [{ key: "remote", label: t("panel.install.remoteId"), value: install.remote_id }]
                : []),
            ]}
          />
        ) : (
          <EmptyState title={t("panel.empty.noMod.title")} description={t("panel.empty.noMod.body")} />
        )}
      </Card>

      <Card title={t("panel.card.voice")}>
        <KeyValue
          items={[
            { key: "voice", label: t("panel.voice.voiceId"), value: tts.voice_id || "-" },
            { key: "source", label: t("panel.voice.source"), value: tts.source || "-" },
            { key: "url", label: t("panel.voice.url"), value: tts.url || "-" },
            {
              key: "key",
              label: t("panel.voice.keySet"),
              value: tts.key_set ? t("panel.value.yes") : t("panel.value.no"),
            },
          ]}
        />
        <Divider />
        <Toolbar>
          <ToolbarGroup>
            <Button disabled={!probeAction} onClick={probe}>
              {t("panel.action.probe")}
            </Button>
          </ToolbarGroup>
        </Toolbar>
        <Tip>{t("panel.action.probeHint")}</Tip>
      </Card>

      <Card title={t("panel.card.log")}>
        {log.available ? (
          <Stack>
            <StatusBadge
              tone={log.clean ? "success" : "warning"}
              label={log.clean ? t("panel.log.clean") : t("panel.log.dirty")}
            />
            <KeyValue
              items={[
                {
                  key: "bridge",
                  label: t("panel.log.bridgeEvents"),
                  value: log.counts?.bridge_events ?? 0,
                },
                {
                  key: "switch",
                  label: t("panel.log.contextSwitch"),
                  value: log.counts?.context_switch ?? 0,
                },
                {
                  key: "sound",
                  label: t("panel.log.missingSound"),
                  value: log.counts?.missing_sound ?? 0,
                },
                {
                  key: "scope",
                  label: t("panel.log.unsupportedScope"),
                  value: log.counts?.unsupported_scope ?? 0,
                },
                { key: "size", label: t("panel.log.size"), value: `${log.size_kib ?? 0} KiB` },
              ]}
            />
            <Tip>{t("panel.card.logHint")}</Tip>
          </Stack>
        ) : (
          <EmptyState title={t("panel.log.unavailable.title")} description={t("panel.log.unavailable.body")} />
        )}
      </Card>

      <Card title={t("panel.card.danger")}>
        <Toolbar>
          <ToolbarGroup>
            <Button tone="danger" disabled={!uninstallAction} onClick={uninstall}>
              {t("panel.action.uninstall")}
            </Button>
            <RefreshButton label={t("panel.action.refresh")} />
          </ToolbarGroup>
        </Toolbar>
        <Tip>{t("panel.action.uninstallHint")}</Tip>
      </Card>
    </Page>
  )
}
