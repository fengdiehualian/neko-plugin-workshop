import {
  ActionButton,
  Alert,
  Button,
  ButtonGroup,
  Card,
  CodeBlock,
  DataTable,
  Grid,
  InlineError,
  KeyValue,
  Page,
  Section,
  Stack,
  StatCard,
  StatusBadge,
  Text,
  Textarea,
  useCallback,
  useEffect,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

/**
 * deepseekharness_bridge 面板。
 *
 * 三段：harness 可用性、配置摘要、最近运行记录，加一个任务执行表单。
 * 业务逻辑都在 Python 的 @ui.context / @ui.action 里，这里只做呈现和本地
 * UI 状态。
 *
 * 文案直接用中文作 i18n key：`t` 的签名是 (key, params)，没有 defaultValue，
 * 取不到翻译时回退显示 key 本身，所以 key 写成可读文案就是可用的默认值。
 */

type HarnessState = {
  sdk_available?: boolean
  dsh_executable?: string | null
  ready?: boolean
}

type Settings = {
  model?: string
  profile?: string
  reasoning_effort?: string
  sandbox_mode?: string
  task_timeout?: number
  workspace?: string
  repo_path?: string
}

type ModelEntry = {
  id: string
  name: string
  description?: string
}

type EffortEntry = {
  id: string
  name: string
  description?: string
}

type SandboxEntry = {
  id: string
  name: string
  description?: string
}

type RunRecord = {
  session_id: string
  task?: string
  task_chars?: number
  /** "running" | "ok" | "failed" */
  status?: string
  elapsed_seconds?: number
  mode?: string
  model?: string
  output?: string
  error?: string
}

type State = {
  harness?: HarnessState
  settings?: Settings
  models?: ModelEntry[]
  efforts?: EffortEntry[]
  sandboxes?: SandboxEntry[]
  runs?: RunRecord[]
  runs_total?: number
  running_count?: number
  dsh_bridge?: string
}

function findAction(actions: HostedAction[], id: string): HostedAction | undefined {
  return actions.find((action) => action.id === id || action.entry_id === id)
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { actions, state, t } = props

  const harness = state.harness || {}
  const settings = state.settings || {}
  const runs = state.runs || []
  const bridge = state.dsh_bridge || "unknown"
  const runningCount = state.running_count ?? 0

  // Which task's detail to show. Local UI state, not plugin state: a refresh
  // reloads the task list and the selection survives as long as that task is
  // still in the list.
  const [selected, setSelected] = useState("")
  const detail = runs.find((r) => r.session_id === selected)

  // Auto-refresh while a task is running. A run can take minutes, and without
  // this the panel would stay frozen until the user clicked refresh — which is
  // exactly the "clicked run and saw no output" complaint.
  useEffect(() => {
    if (runningCount === 0) return
    const timer = setInterval(() => {
      void props.api.refresh()
    }, 2000)
    return () => clearInterval(timer)
  }, [runningCount, props.api])

  // Select the task the form just submitted, so its output is on screen
  // without the user having to find it in the list.
  const handleSubmitted = useCallback((result: any) => {
    const session = result?.session_id
    if (typeof session === "string" && session) setSelected(session)
  }, [])

  // Task composer. Textarea + Button + api.call rather than ActionForm: the
  // form helper rendered but never reached the entry, and this way any failure
  // is visible in the panel instead of being swallowed.
  const [draft, setDraft] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState("")

  const submitTask = useCallback(async () => {
    const text = draft.trim()
    if (!text) return

    setSubmitting(true)
    setSubmitError("")
    try {
      const result = await props.api.call("panel_run_task", { task: text })
      setDraft("")
      handleSubmitted(result)
      await props.api.refresh()
    } catch (err) {
      setSubmitError(String(err))
    } finally {
      setSubmitting(false)
    }
  }, [draft, props.api, handleSubmitted])

  const refresh = findAction(actions, "panel_refresh")
  const models = state.models || []
  const efforts = state.efforts || []
  const sandboxes = state.sandboxes || []

  // The DSH-side bundle is bootstrapped on startup, so this normally reads
  // "installed". Anything else explains what is missing.
  const bridgeLabel: Record<string, string> = {
    installed: "已就绪",
    repaired: "已自动修复",
    "installed-fresh": "已自动安装",
    "no-dsh": "未检测到 DSH",
    "no-bundle": "缺少内嵌 bundle",
    failed: "自动安装失败",
    unknown: "尚未检查",
  }
  const bridgeTone =
    bridge === "installed" || bridge === "repaired" || bridge === "installed-fresh"
      ? "success"
      : bridge === "unknown"
        ? "default"
        : "warning"

  return (
    <Page
      title={props.plugin.name}
      subtitle={t("驱动本地 DeepSeek Harness 执行长任务。")}
    >
      <Stack gap={16}>
        <Section>
          <Grid cols={2} gap={12}>
            <StatCard label={t("Harness 状态")} value={harness.ready ? t("可用") : t("未就绪")} />
            <StatCard label={t("进行中")} value={String(runningCount)} />
            <StatCard
              label={t("调用方式")}
              value={harness.sdk_available ? t("Python SDK") : t("dsh CLI")}
            />
            <Card title={t("DSH 侧桥接")}>
              <StatusBadge tone={bridgeTone} label={bridgeLabel[bridge] || bridge} />
            </Card>
          </Grid>
        </Section>

        {!harness.ready ? (
          <Alert tone="warning">
            {t(
              "未找到 DeepSeek Harness。请安装 Python SDK（pip install deepseek-harness-sdk），或在配置中指定 repo_path / dsh_executable。",
            )}
          </Alert>
        ) : null}

        <Card title={t("Harness")}>
          <Stack gap={12}>
            <KeyValue
              items={[
                {
                  label: t("Python SDK"),
                  value: (
                    <StatusBadge
                      tone={harness.sdk_available ? "success" : "default"}
                      status={harness.sdk_available ? "ok" : "missing"}
                    />
                  ),
                },
                {
                  label: t("dsh 可执行文件"),
                  value: harness.dsh_executable || "—",
                },
                { label: t("Profile"), value: settings.profile || "—" },
                {
                  label: t("任务超时（秒）"),
                  value: String(settings.task_timeout ?? "—"),
                },
                { label: t("工作目录"), value: settings.workspace || "—" },
              ]}
            />
            {refresh ? <ActionButton action={refresh} label={t("刷新")} /> : null}
          </Stack>
        </Card>

        {models.length > 0 ? (
          <Card title={t("模型")}>
            <Stack gap={8}>
              <Text>
                {models.find((m) => m.id === settings.model)?.description ||
                  t("切换后立即对之后的任务生效。")}
              </Text>
              <ButtonGroup>
                {models.map((m) => (
                  <Button
                    key={m.id}
                    tone={settings.model === m.id ? "primary" : "default"}
                    onClick={() => {
                      void props.api.call("panel_set_model", { model: m.id })
                    }}
                  >
                    {m.name}
                  </Button>
                ))}
              </ButtonGroup>
            </Stack>
          </Card>
        ) : null}

        {efforts.length > 0 ? (
          <Card title={t("思考强度")}>
            <Stack gap={8}>
              <Text>
                {efforts.find((e) => e.id === settings.reasoning_effort)?.description ||
                  t("思考越深，耗时和成本越高。")}
              </Text>
              <ButtonGroup>
                {efforts.map((e) => (
                  <Button
                    key={e.id}
                    tone={settings.reasoning_effort === e.id ? "primary" : "default"}
                    onClick={() => {
                      void props.api.call("panel_set_effort", { effort: e.id })
                    }}
                  >
                    {e.name}
                  </Button>
                ))}
              </ButtonGroup>
            </Stack>
          </Card>
        ) : null}

        {sandboxes.length > 0 ? (
          <Card title={t("文件权限")}>
            <Stack gap={8}>
              <Text>
                {sandboxes.find((s) => s.id === settings.sandbox_mode)?.description ||
                  t("决定 harness 能读写哪些文件。")}
              </Text>
              <ButtonGroup>
                {sandboxes.map((s) => (
                  <Button
                    key={s.id}
                    tone={settings.sandbox_mode === s.id ? "primary" : "default"}
                    onClick={() => {
                      void props.api.call("panel_set_sandbox", { mode: s.id })
                    }}
                  >
                    {s.name}
                  </Button>
                ))}
              </ButtonGroup>
              <Text>
                {t("工作区目录：") + (settings.workspace || "—")}
              </Text>
            </Stack>
          </Card>
        ) : null}

        <Card title={t("运行任务")}>
          <Stack gap={8}>
            <Field label={t("任务内容")} help={t("写清楚目标和验收标准。")}>
              <Textarea
                value={draft}
                placeholder={t("例如：总结当前工作区的结构")}
                onChange={(next) => setDraft(next)}
              />
            </Field>
            <Button
              tone="primary"
              disabled={submitting || !draft.trim()}
              onClick={() => {
                void submitTask()
              }}
            >
              {submitting ? t("提交中…") : t("交给 harness 执行")}
            </Button>
            {submitError ? (
              <InlineError title={t("提交失败")} message={submitError} />
            ) : null}
          </Stack>
        </Card>

        <Card title={t("任务")}>
          <Stack gap={12}>
            <Stack gap={4}>
              <Text>
                {runningCount > 0
                  ? t("进行中") + "：" + String(runningCount)
                  : t("全部任务，最新的在最上面。点击任意一行查看输出。")}
              </Text>
            </Stack>
            <DataTable<RunRecord>
              data={runs}
              rowKey="session_id"
              selectedKey={selected}
              onSelect={(row) => setSelected(row.session_id)}
              emptyText={t("还没有任务。在上方「运行任务」里提交一个。")}
              columns={[
                {
                  key: "status",
                  label: t("状态"),
                  render: (row) => (
                    <StatusBadge
                      tone={
                        row.status === "ok"
                          ? "success"
                          : row.status === "running"
                            ? "info"
                            : "danger"
                      }
                      status={
                        row.status === "ok"
                          ? "ok"
                          : row.status === "running"
                            ? "pending"
                            : "failed"
                      }
                      label={
                        row.status === "ok"
                          ? t("完成")
                          : row.status === "running"
                            ? t("进行中")
                            : t("失败")
                      }
                    />
                  ),
                },
                {
                  key: "task",
                  label: t("任务"),
                  render: (row) => {
                    const text = row.task || ""
                    return <Text>{text.length > 60 ? text.slice(0, 60) + "…" : text}</Text>
                  },
                },
                { key: "model", label: t("模型") },
                {
                  key: "elapsed_seconds",
                  label: t("耗时（秒）"),
                },
              ]}
            />

            {detail ? (
              <Card title={t("任务详情")}>
                <Stack gap={8}>
                  <KeyValue
                    items={[
                      { label: t("会话"), value: detail.session_id },
                      { label: t("状态"), value: detail.status },
                      { label: t("模型"), value: detail.model || "—" },
                      { label: t("调用方式"), value: detail.mode || "—" },
                      {
                        label: t("耗时（秒）"),
                        value: String(detail.elapsed_seconds ?? 0),
                      },
                    ]}
                  />
                  <Text>{t("任务内容")}</Text>
                  <CodeBlock>{detail.task || "—"}</CodeBlock>
                  {detail.error ? (
                    <>
                      <Text>{t("错误")}</Text>
                      <CodeBlock>{detail.error}</CodeBlock>
                    </>
                  ) : null}
                  <Text>{t("输出")}</Text>
                  <CodeBlock>{detail.output || t("（暂无输出）")}</CodeBlock>
                </Stack>
              </Card>
            ) : null}
          </Stack>
        </Card>
      </Stack>
    </Page>
  )
}
