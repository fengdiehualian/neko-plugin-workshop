import {
  Page,
  Card,
  Stack,
  Text,
  StatusBadge,
  KeyValue,
  Select,
  Field,
  Input,
  Alert,
  Divider,
  EmptyState,
  ActionButton,
  RefreshButton,
  useEffect,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type MaidInfo = {
  id: string
  name: string
  health: number
  max_health: number
  is_sitting: boolean
  is_following: boolean
  owner: string
}

type State = {
  connected: boolean
  ws_url: string
  ws_port: string
  maids: MaidInfo[]
  assigned_maid_id: string
  assigned_maid_name: string
  command_execution_enabled: boolean
  companion_mode: string
  companion_settings: Record<string, number>
  plan_state: PlanState
  plan_summary: PlanSummary
  last_diagnostic?: DiagnosticResult | null
  last_refresh_status?: RefreshStatus | null
}

type PlanStep = {
  text: string
  done: boolean
}

type PlanState = {
  title: string
  steps: PlanStep[]
  updated_at: number
}

type PlanSummary = {
  title: string
  total_steps: number
  completed_steps: number
  pending_steps: number
  plan: string
}

type DiagnosticCheck = {
  status: string
  title: string
  detail: string
  suggestion?: string
}

type DiagnosticResult = {
  status: string
  summary: string
  checks: DiagnosticCheck[]
}

type RefreshStatus = {
  status: string
  message: string
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, actions, useLocalState } = props

  const connected = state?.connected ?? false
  const wsUrl = state?.ws_url ?? ""
  const wsPort = state?.ws_port ?? ""
  const maids = state?.maids ?? []
  const assignedId = state?.assigned_maid_id ?? ""
  const assignedName = state?.assigned_maid_name ?? ""
  const commandExecutionEnabled = state?.command_execution_enabled ?? false
  const companionMode = state?.companion_mode ?? "standard"
  const companionSettings = state?.companion_settings ?? {}
  const planState = state?.plan_state ?? { title: "", steps: [], updated_at: 0 }
  const planSummary = state?.plan_summary ?? { title: "", total_steps: 0, completed_steps: 0, pending_steps: 0, plan: "" }
  const diagnostic = state?.last_diagnostic ?? null
  const refreshStatus = state?.last_refresh_status ?? null

  const [selectedMaidId, setSelectedMaidId] = useLocalState<string>("selectedMaidId", "")
  const [connectionPort, setConnectionPort] = useState<string>(wsPort)
  const [planTitle, setPlanTitle] = useState<string>(planState.title || "")
  const [planAppendStep, setPlanAppendStep] = useState<string>("")
  const [selectedPlanStep, setSelectedPlanStep] = useState<string>("")
  const settingText = (key: string) => String(companionSettings[key] ?? "")
  const [selectedCompanionMode, setSelectedCompanionMode] = useState<string>(companionMode)
  const [customQuietStableSeconds, setCustomQuietStableSeconds] = useState<string>(settingText("playmate_quiet_stable_seconds"))
  const [customQuietCooldown, setCustomQuietCooldown] = useState<string>(settingText("playmate_quiet_cooldown"))
  const [customSuggestionCooldown, setCustomSuggestionCooldown] = useState<string>(settingText("playmate_suggestion_cooldown"))

  useEffect(() => {
    setConnectionPort(wsPort)
  }, [wsPort])

  useEffect(() => {
    setSelectedCompanionMode(companionMode)
  }, [companionMode])

  useEffect(() => {
    setCustomQuietStableSeconds(settingText("playmate_quiet_stable_seconds"))
    setCustomQuietCooldown(settingText("playmate_quiet_cooldown"))
    setCustomSuggestionCooldown(settingText("playmate_suggestion_cooldown"))
  }, [
    companionMode,
    companionSettings.playmate_quiet_stable_seconds,
    companionSettings.playmate_quiet_cooldown,
    companionSettings.playmate_suggestion_cooldown,
  ])

  useEffect(() => {
    setPlanTitle(planState.title || "")
    setSelectedPlanStep("")
  }, [planState.title, planState.steps.length, planState.updated_at])

  const assignAction = actions.find((a) => a.id === "assign_maid") as HostedAction | undefined
  const refreshAction = actions.find((a) => a.id === "refresh_maid_status") as HostedAction | undefined
  const diagnoseAction = actions.find((a) => a.id === "diagnose_bridge") as HostedAction | undefined
  const setConnectionPortAction = actions.find((a) => a.id === "set_connection_port" || a.entry_id === "set_connection_port") as HostedAction | undefined
  const applySpeechPresetAction = actions.find((a) => a.id === "apply_speech_frequency_preset" || a.entry_id === "apply_speech_frequency_preset") as HostedAction | undefined
  const setPlanBoardAction = actions.find((a) => a.id === "set_plan_board" || a.entry_id === "set_plan_board") as HostedAction | undefined

  const companionModeOptions = ["quiet", "standard", "active", "custom"].map((mode) => ({
    value: mode,
    label: t(`companionMode.${mode}`),
  }))

  const effectiveCompanionMode = selectedCompanionMode || companionMode
  const customValue = (key: string, draft: string) => draft || String(companionSettings[key] ?? "")
  const customCompanionValues = {
    playmate_quiet_stable_seconds: customValue("playmate_quiet_stable_seconds", customQuietStableSeconds),
    playmate_quiet_cooldown: customValue("playmate_quiet_cooldown", customQuietCooldown),
    playmate_suggestion_cooldown: customValue("playmate_suggestion_cooldown", customSuggestionCooldown),
  }

  const maidOptions = [
    { value: "", label: t("maid.selectPlaceholder") },
    ...maids.map((m) => ({
      value: m.id,
      label: `${m.name} (${m.id.substring(0, 8)}...)`,
    })),
  ]

  const assignedMaid = maids.find((m) => m.id === assignedId)
  const selectedMaid = maids.find((m) => m.id === selectedMaidId)
  const planStepOptions = [
    { value: "", label: t("plan.selectStep") },
    ...planState.steps.map((step, index) => ({
      value: String(index + 1),
      label: `${index + 1}. ${step.done ? t("plan.done") : t("plan.pending")} ${step.text}`,
    })),
  ]

  if (state == null) {
    return (
      <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
        <Card title={t("connection.title")}>
          <Stack>
            <StatusBadge tone="error">{t("connection.pluginNotEnabled")}</StatusBadge>
            <Text>{t("connection.pluginNotEnabledHint")}</Text>
            <RefreshButton />
          </Stack>
        </Card>
      </Page>
    )
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Card title={t("connection.title")}>
        <Stack>
          <StatusBadge tone={connected ? "success" : "error"}>
            {connected ? t("connection.connected") : t("connection.disconnected")}
          </StatusBadge>
          <KeyValue
            items={[
              { key: t("connection.wsUrl"), value: wsUrl || "-" },
              { key: t("connection.companionMode"), value: t(`companionMode.${companionMode}`) },
            ]}
          />
          <div
            style={{
              display: "grid",
              gridTemplateColumns: setConnectionPortAction ? "minmax(180px, 2fr) minmax(140px, 1fr)" : "1fr",
              gap: "12px",
              alignItems: "start",
            }}
          >
            <Field label={t("connection.port")} help={t("connection.portHelp")}>
              <Input value={connectionPort} onChange={setConnectionPort} />
            </Field>
            {setConnectionPortAction && (
              <div style={{ paddingTop: "24px" }}>
                <ActionButton
                  action={setConnectionPortAction}
                  values={{ port: connectionPort.trim() }}
                >
                  {t("actions.setConnectionPort")}
                </ActionButton>
              </div>
            )}
          </div>
          <Stack direction="horizontal">
            <RefreshButton />
            {refreshAction && (
              <ActionButton action={refreshAction}>{t("actions.refresh")}</ActionButton>
            )}
            {diagnoseAction && (
              <ActionButton action={diagnoseAction}>{t("actions.diagnose")}</ActionButton>
            )}
          </Stack>
          {refreshStatus && (
            <Alert tone={refreshStatus.status === "success" ? "success" : refreshStatus.status === "error" ? "error" : "warning"}>
              {refreshStatus.message}
            </Alert>
          )}
        </Stack>
      </Card>

      <Card title={t("companion.title")}>
        <Stack>
          <Text>{t("companion.description")}</Text>
          <Select
            options={companionModeOptions}
            value={effectiveCompanionMode}
            onChange={setSelectedCompanionMode}
          />
          <Text>{t(`companion.modeSummary.${effectiveCompanionMode}`)}</Text>
          {effectiveCompanionMode === "custom" && (
            <Stack>
              <Alert tone="info">{t("companion.customHint")}</Alert>
              <Field label={t("companion.fields.quietStableSeconds")} help={t("companion.fields.quietStableSecondsHelp")}>
                <Input value={customCompanionValues.playmate_quiet_stable_seconds} onChange={setCustomQuietStableSeconds} />
              </Field>
              <Field label={t("companion.fields.quietCooldown")} help={t("companion.fields.quietCooldownHelp")}>
                <Input value={customCompanionValues.playmate_quiet_cooldown} onChange={setCustomQuietCooldown} />
              </Field>
              <Field label={t("companion.fields.suggestionCooldown")} help={t("companion.fields.suggestionCooldownHelp")}>
                <Input value={customCompanionValues.playmate_suggestion_cooldown} onChange={setCustomSuggestionCooldown} />
              </Field>
            </Stack>
          )}
          {applySpeechPresetAction && (
            <ActionButton
              action={applySpeechPresetAction}
              values={{
                mode: effectiveCompanionMode,
                ...(effectiveCompanionMode === "custom" ? customCompanionValues : {}),
              }}
            >
              {t("actions.applySpeechPreset")}
            </ActionButton>
          )}
        </Stack>
      </Card>

      {diagnostic && (
        <Card title={t("diagnostic.title")}>
          <Stack>
            <Alert tone={diagnostic.status === "ok" ? "success" : diagnostic.status === "error" ? "error" : "warning"}>
              {diagnostic.summary}
            </Alert>
            <KeyValue
              items={diagnostic.checks.map((check) => ({
                key: `${t(`diagnostic.status.${check.status}`)} · ${check.title}`,
                value: check.suggestion ? `${check.detail} ${check.suggestion}` : check.detail,
              }))}
            />
          </Stack>
        </Card>
      )}

      <Card title={t("plan.title")}>
        <Stack>
          {planSummary.total_steps > 0 || planState.title ? (
            <Stack>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) auto",
                  gap: "12px",
                  alignItems: "start",
                }}
              >
                <KeyValue
                  items={[
                    { key: t("plan.boardTitle"), value: planState.title || "-" },
                    { key: t("plan.progress"), value: `${planSummary.completed_steps}/${planSummary.total_steps}` },
                  ]}
                />
                {setPlanBoardAction && (
                  <ActionButton
                    action={setPlanBoardAction}
                    values={{ plan: "" }}
                  >
                    {t("actions.clearPlan")}
                  </ActionButton>
                )}
              </div>
              {planState.steps.length > 0 && (
                <div style={{ display: "grid", gap: "8px" }}>
                  {planState.steps.map((step, index) => (
                    <div
                      key={`${index}-${step.text}`}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "96px minmax(0, 1fr)",
                        gap: "10px",
                        alignItems: "center",
                      }}
                    >
                      <StatusBadge tone={step.done ? "success" : "warning"}>
                        {`${index + 1}. ${step.done ? t("plan.done") : t("plan.pending")}`}
                      </StatusBadge>
                      <Text>{step.text}</Text>
                    </div>
                  ))}
                </div>
              )}
            </Stack>
          ) : (
            <EmptyState title={t("plan.emptyTitle")} description={t("plan.emptyDescription")} />
          )}

          {setPlanBoardAction && (
            <Stack>
              <Divider />
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) minmax(120px, 160px)",
                  gap: "12px",
                  alignItems: "start",
                }}
              >
                <Field label={t("plan.fields.title")}>
                  <Input value={planTitle} onChange={setPlanTitle} />
                </Field>
                <div style={{ paddingTop: "24px" }}>
                  <ActionButton
                    action={setPlanBoardAction}
                    values={{ title: planTitle }}
                  >
                    {t("actions.updatePlanTitle")}
                  </ActionButton>
                </div>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) minmax(120px, 160px)",
                  gap: "12px",
                  alignItems: "start",
                }}
              >
                <Field label={t("plan.fields.appendStep")}>
                  <Input value={planAppendStep} onChange={setPlanAppendStep} />
                </Field>
                <div style={{ paddingTop: "24px" }}>
                  {planAppendStep.trim() && (
                    <ActionButton
                      action={setPlanBoardAction}
                      values={{ append_step: planAppendStep }}
                      onResult={() => setPlanAppendStep("")}
                    >
                      {t("actions.appendPlanStep")}
                    </ActionButton>
                  )}
                </div>
              </div>

              {planState.steps.length > 0 && (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0, 1fr) minmax(120px, 160px) minmax(120px, 160px)",
                    gap: "12px",
                    alignItems: "start",
                  }}
                >
                  <Field label={t("plan.fields.step")}>
                    <Select
                      options={planStepOptions}
                      value={selectedPlanStep}
                      onChange={setSelectedPlanStep}
                    />
                  </Field>
                  <div style={{ paddingTop: "24px" }}>
                    {selectedPlanStep && (
                      <ActionButton
                        action={setPlanBoardAction}
                        values={{ completed_step: selectedPlanStep }}
                      >
                        {t("actions.completePlanStep")}
                      </ActionButton>
                    )}
                  </div>
                  <div style={{ paddingTop: "24px" }}>
                    {selectedPlanStep && (
                      <ActionButton
                        action={setPlanBoardAction}
                        values={{ uncompleted_step: selectedPlanStep }}
                      >
                        {t("actions.reopenPlanStep")}
                      </ActionButton>
                    )}
                  </div>
                </div>
              )}
            </Stack>
          )}
        </Stack>
      </Card>

      <Card title={t("command.title")}>
        <Stack>
          <Alert tone={commandExecutionEnabled ? "success" : "warning"}>
            {commandExecutionEnabled ? t("command.enabled") : t("command.disabled")}
          </Alert>
          <Text>{t("command.description")}</Text>
        </Stack>
      </Card>

      <Card title={t("maid.title")}>
        <Stack>
          {assignedId && assignedName ? (
            <Alert tone="success">{t("maid.assigned", { name: assignedName })}</Alert>
          ) : (
            <Alert tone="warning">{t("maid.notAssigned")}</Alert>
          )}

          {assignedMaid && (
            <KeyValue
              items={[
                { key: t("maid.name"), value: assignedMaid.name },
                { key: t("maid.health"), value: `${assignedMaid.health}/${assignedMaid.max_health}` },
                { key: t("maid.sitting"), value: assignedMaid.is_sitting ? t("yes") : t("no") },
                { key: t("maid.following"), value: assignedMaid.is_following ? t("yes") : t("no") },
                { key: t("maid.owner"), value: assignedMaid.owner },
              ]}
            />
          )}

          <Divider />

          {maids.length > 0 ? (
            <Stack>
              <Text>{t("maid.selectHint")}</Text>
              <Select
                options={maidOptions}
                value={selectedMaidId}
                onChange={setSelectedMaidId}
              />
              {assignAction && selectedMaidId && (
                <ActionButton
                  action={assignAction}
                  values={{ maid_id: selectedMaidId, maid_name: selectedMaid?.name ?? "" }}
                >
                  {t("actions.assignMaid")}
                </ActionButton>
              )}
            </Stack>
          ) : connected ? (
            <EmptyState title={t("maid.noMaids")} description={t("maid.noMaidsHint")} />
          ) : (
            <EmptyState title={t("maid.connectFirst")} description={t("maid.connectFirstHint")} />
          )}
        </Stack>
      </Card>
    </Page>
  )
}
