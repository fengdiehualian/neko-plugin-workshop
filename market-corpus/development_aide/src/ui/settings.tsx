/**
 * Development-Aide 设置面板
 *
 * 三块功能：
 *  1. 设置：工作区、读取上限、语气、功能开关（保存后持久化）
 *  2. Skill：扫描本机技能目录 → 下拉选择 → 导入
 *  3. 自检：直接读取一个文件 / 运行分析，立即看到结果，无需先问猫娘
 */
import {
  Page,
  Card,
  Stack,
  Field,
  Input,
  Select,
  Switch,
  ActionButton,
  Button,
  Text,
  Tip,
  Alert,
  StatusBadge,
  JsonView,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"
import { useLocalState } from "@neko/plugin-ui"

type FeaturesEnabled = {
  code_review: boolean
  error_fix: boolean
  project_summary: boolean
  multi_file_summary: boolean
}

type State = {
  config: {
    skill_path: string
    workspace_root: string
    read_only: boolean
    max_chars: number
    analysis_tone: string
    default_file_extensions: string[]
    enable_code_review: boolean
    enable_error_fix: boolean
    enable_project_summary: boolean
    enable_multi_file_summary: boolean
  }
  status: {
    ready: boolean
    mode: string
    read_only: boolean
    skill_path: string
    skill_loaded: boolean
    workspace_root: string
    workspace_configured: boolean
    workspace_exists: boolean
    features_enabled: FeaturesEnabled
  }
}

type SkillCandidate = { path: string; name?: string; marker?: string }

const TONE_OPTIONS = [
  { label: "专业", value: "professional" },
  { label: "猫娘", value: "catgirl" },
]

// Keep in sync with MAX_CHARS_HARD_LIMIT in the Python plugin.
const MAX_CHARS_HARD_LIMIT = 1_000_000

function isAbsolutePath(value: string): boolean {
  return /^(\/|[A-Za-z]:[\\/])/.test(value)
}

/** The host may hand back the raw payload or an {ok, value} envelope. */
function unwrapResult(result: any): any {
  if (result && typeof result === "object" && !("result" in result) && result.value && typeof result.value === "object") {
    return result.value
  }
  return result
}

function resultOk(result: any): boolean {
  if (result === null || result === undefined) return false
  if (typeof result !== "object") return true
  const data = unwrapResult(result)
  if (result.ok === false || result.error) return false
  if (typeof data?.result === "string") return data.result === "success"
  return true
}

function resultMessage(result: any): string {
  if (!result || typeof result !== "object") return String(result ?? "")
  const error = result.error
  if (typeof error === "string") return error
  if (error && typeof error === "object") return String(error.message ?? JSON.stringify(error))
  const data = unwrapResult(result)
  return String(data?.message ?? result.message ?? "")
}

export default function SettingsPanel(props: PluginSurfaceProps<State>) {
  const { state, actions, api } = props

  // Actions can be empty on the first render; cache them so buttons stay put.
  const [cachedActions, setCachedActions] = useLocalState("da-ca", () => [] as HostedAction[])
  if (actions.length > 0 && actions.length !== cachedActions.length) {
    setCachedActions(actions)
  }
  const effectiveActions = cachedActions.length > 0 ? cachedActions : actions

  const [skillPath, setSkillPath] = useLocalState("da-sp", () => state.config?.skill_path ?? "")
  const [workspaceRoot, setWorkspaceRoot] = useLocalState("da-wr", () => state.config?.workspace_root ?? "")
  const [readOnly, setReadOnly] = useLocalState("da-ro", () => state.config?.read_only ?? true)
  const [maxCharsText, setMaxCharsText] = useLocalState("da-mc", () => String(state.config?.max_chars ?? 4000))
  const [analysisTone, setAnalysisTone] = useLocalState("da-at", () => state.config?.analysis_tone ?? "professional")
  const [enableCodeReview, setEnableCodeReview] = useLocalState("da-ecr", () => state.config?.enable_code_review ?? true)
  const [enableErrorFix, setEnableErrorFix] = useLocalState("da-eef", () => state.config?.enable_error_fix ?? true)
  const [enableProjectSummary, setEnableProjectSummary] = useLocalState(
    "da-eps",
    () => state.config?.enable_project_summary ?? true
  )
  const [enableMultiFileSummary, setEnableMultiFileSummary] = useLocalState(
    "da-emf",
    () => state.config?.enable_multi_file_summary ?? true
  )

  // Skill scan / import
  const [candidates, setCandidates] = useLocalState("da-cand", () => [] as SkillCandidate[])
  const [chosenSkill, setChosenSkill] = useLocalState("da-cs", () => "")
  const [skillMessage, setSkillMessage] = useLocalState("da-sm", () => "")
  const [skillError, setSkillError] = useLocalState("da-se", () => false)
  const [scanning, setScanning] = useLocalState("da-scn", () => false)

  // Self-check: read a file directly from the panel
  const [readPath, setReadPath] = useLocalState("da-rp", () => "")
  const [lastResult, setLastResult] = useLocalState("da-lr", () => null as any)
  const [lastOk, setLastOk] = useLocalState("da-lo", () => true)

  const saveAction = effectiveActions.find((a: HostedAction) => a.id === "save_settings")
  const codeReviewAction = effectiveActions.find((a: HostedAction) => a.id === "code_review")
  const errorFixAction = effectiveActions.find((a: HostedAction) => a.id === "error_fix")
  const projectSummaryAction = effectiveActions.find((a: HostedAction) => a.id === "project_summary")
  const multiSummaryAction = effectiveActions.find((a: HostedAction) => a.id === "multi_file_summary")
  const quickAuditAction = effectiveActions.find((a: HostedAction) => a.id === "quick_audit")
  const readFileAction = effectiveActions.find((a: HostedAction) => a.id === "read_project_file")

  // ---- validation -------------------------------------------------------
  const trimmedRoot = workspaceRoot.trim()
  const digits = maxCharsText.trim()
  const maxCharsValue = Number(digits)
  const maxCharsValid = /^\d+$/.test(digits) && maxCharsValue >= 1 && maxCharsValue <= MAX_CHARS_HARD_LIMIT
  const workspaceValid = trimmedRoot === "" || isAbsolutePath(trimmedRoot)
  const toneValid = analysisTone === "professional" || analysisTone === "catgirl"
  const formValid = maxCharsValid && workspaceValid && toneValid

  const workspaceConfigured = Boolean(trimmedRoot)
  const workspaceExists = state.status?.workspace_exists === true
  const workspaceLooksMissing = workspaceConfigured && !workspaceExists

  const skillLoaded = state.status?.skill_loaded === true
  const quickAuditEnabled = enableCodeReview || enableErrorFix || enableMultiFileSummary

  function buildConfig(): Record<string, unknown> {
    return {
      skill_path: skillPath.trim(),
      workspace_root: trimmedRoot,
      read_only: readOnly,
      max_chars: maxCharsValue,
      analysis_tone: analysisTone,
      enable_code_review: enableCodeReview,
      enable_error_fix: enableErrorFix,
      enable_project_summary: enableProjectSummary,
      enable_multi_file_summary: enableMultiFileSummary,
    }
  }

  function remember(result: any) {
    setLastOk(resultOk(result))
    setLastResult(unwrapResult(result))
  }

  function handleScanSkills() {
    setScanning(true)
    setSkillMessage("正在扫描…")
    setSkillError(false)
    api
      .call("scan_skill_dirs")
      .then((result: any) => {
        remember(result)
        if (!resultOk(result)) {
          setSkillError(true)
          setSkillMessage(resultMessage(result) || "扫描失败，请查看下方最近结果。")
          setCandidates([])
          return
        }
        const list: SkillCandidate[] = Array.isArray(unwrapResult(result)?.candidates)
          ? unwrapResult(result).candidates
          : []
        setCandidates(list)
        setChosenSkill(list.length > 0 ? list[0].path : "")
        setSkillError(list.length === 0)
        setSkillMessage(list.length > 0 ? `扫描到 ${list.length} 个技能目录，请选择后导入。` : "没有扫描到技能目录。")
      })
      .catch((error: any) => {
        setSkillError(true)
        setSkillMessage(String(error?.message ?? error))
      })
      .then(() => setScanning(false))
  }

  function handleImportSkill() {
    const target = chosenSkill || skillPath.trim()
    if (!target) {
      setSkillError(true)
      setSkillMessage("请先扫描并选择一个技能目录，或直接填写技能目录的绝对路径。")
      return
    }
    setSkillMessage("正在导入…")
    setSkillError(false)
    api
      .call("import_skill", { skill_path: target })
      .then((result: any) => {
        remember(result)
        const data = unwrapResult(result)
        const ok = resultOk(result)
        setSkillError(!ok)
        setSkillMessage(
          ok
            ? `已导入：${data?.skill_path ?? target}（标记文件 ${data?.skill_marker ?? "已检测"}）`
            : resultMessage(result) || "导入失败，请查看下方最近结果。"
        )
        if (ok && typeof data?.skill_path === "string") {
          setSkillPath(data.skill_path)
        }
      })
      .catch((error: any) => {
        setSkillError(true)
        setSkillMessage(String(error?.message ?? error))
      })
  }

  const candidateOptions = candidates.map((item: SkillCandidate) => ({
    value: item.path,
    label: item.name ? `${item.name} — ${item.path}` : item.path,
  }))

  const workspaceStatusText = workspaceLooksMissing
    ? `工作区目录不存在：${trimmedRoot}`
    : workspaceExists
      ? `工作区已就绪：${state.status?.workspace_root ?? trimmedRoot}`
      : "尚未配置工作区目录，读取文件会失败"

  return (
    <Page title="Development-Aide">
      <Card title="开发辅助设置">
        <Stack>
          {!formValid ? (
            <Alert
              tone="danger"
              message={
                !maxCharsValid
                  ? `单文件最大读取字符数必须是 1 ~ ${MAX_CHARS_HARD_LIMIT} 之间的正整数。`
                  : !workspaceValid
                    ? "工作区路径必须留空或填写绝对路径（如 D:\\projects\\demo）。"
                    : "语气风格只能是「专业」或「猫娘」。"
              }
            />
          ) : null}

          {workspaceLooksMissing ? (
            <Alert
              tone="warning"
              message="已保存的工作区目录不存在，读取文件一定会失败；请修正路径后重新保存设置。"
            />
          ) : null}

          <Field label="项目工作区路径（绝对路径，留空则不启用读取）">
            <Input
              value={workspaceRoot}
              placeholder="例如 D:\\projects\\my-project 或 /home/you/my-project"
              onChange={(value: string) => setWorkspaceRoot(value)}
            />
          </Field>
          <Stack>
            <StatusBadge tone={workspaceExists ? "success" : "warning"} label={workspaceStatusText} />
            <Tip>
              把工作区设成项目根目录即可。之后读取文件时可以给相对路径（src/main.py）、工作区内的绝对路径，或只给文件名
              （main.py），插件会在工作区内自动查找。
            </Tip>
          </Stack>

          <Field label="语言/语气风格">
            <Select
              value={analysisTone}
              options={TONE_OPTIONS}
              onChange={(value: string) => setAnalysisTone(value)}
            />
          </Field>

          <Field label="只读模式（强烈建议保持开启）">
            <Switch checked={readOnly} onChange={(value: boolean) => setReadOnly(value)} />
          </Field>

          <Field label="单文件最大读取字符数">
            <Input
              value={maxCharsText}
              placeholder="4000"
              onChange={(value: string) => setMaxCharsText(value)}
            />
          </Field>

          <Card title="功能开关">
            <Stack>
              <Field label="代码审查">
                <Switch checked={enableCodeReview} onChange={(value: boolean) => setEnableCodeReview(value)} />
              </Field>
              <Field label="错误定位与修复建议">
                <Switch checked={enableErrorFix} onChange={(value: boolean) => setEnableErrorFix(value)} />
              </Field>
              <Field label="项目结构摘要">
                <Switch
                  checked={enableProjectSummary}
                  onChange={(value: boolean) => setEnableProjectSummary(value)}
                />
              </Field>
              <Field label="多文件汇总">
                <Switch
                  checked={enableMultiFileSummary}
                  onChange={(value: boolean) => setEnableMultiFileSummary(value)}
                />
              </Field>
            </Stack>
          </Card>

          {saveAction && formValid ? (
            <ActionButton
              action={saveAction}
              values={{ config: buildConfig() }}
              onResult={(result: any) => {
                remember(result)
                const data = unwrapResult(result)
                if (resultOk(result) && data?.workspace_configured && !data?.workspace_exists) {
                  setLastOk(false)
                }
              }}
            >
              保存设置
            </ActionButton>
          ) : (
            <Tip>{formValid ? "保存按钮加载中…" : "请先修正上方设置错误，再保存。"}</Tip>
          )}
        </Stack>
      </Card>

      <Card title="Skill 技能包">
        <Stack>
          <Stack>
            <StatusBadge
              tone={skillLoaded ? "success" : "warning"}
              label={skillLoaded ? `已导入：${state.status?.skill_path ?? skillPath}` : "尚未导入技能"}
            />
          </Stack>
          <Button tone="info" disabled={scanning} onClick={handleScanSkills}>
            {scanning ? "正在扫描…" : "扫描技能目录"}
          </Button>
          {candidateOptions.length > 0 ? (
            <Field label="扫描到的技能目录">
              <Select
                value={chosenSkill}
                options={candidateOptions}
                onChange={(value: string) => setChosenSkill(value)}
              />
            </Field>
          ) : null}
          <Field label="技能目录绝对路径（也可手动填写或粘贴）">
            <Input
              value={skillPath}
              placeholder="例如 C:\\Users\\you\\.trae-cn\\skills\\my-skill"
              onChange={(value: string) => setSkillPath(value)}
            />
          </Field>
          <Button tone="primary" onClick={handleImportSkill}>
            导入所选技能
          </Button>
          {skillMessage ? <Alert tone={skillError ? "danger" : "success"} message={skillMessage} /> : null}
          <Tip>
            面板无法弹出系统文件选择框，所以先「扫描技能目录」再下拉选择；也可以直接把技能目录的绝对路径粘到上面，
            或者对猫娘说「把 D:\\...\\skills\\xxx 导入」。
          </Tip>
        </Stack>
      </Card>

      <Card title="读取文件（自检）">
        <Stack>
          <Field label="要读取的文件：相对路径 / 工作区内绝对路径 / 文件名">
            <Input
              value={readPath}
              placeholder="例如 src/main.py 或 main.py"
              onChange={(value: string) => setReadPath(value)}
            />
          </Field>
          {readFileAction ? (
            <ActionButton
              action={readFileAction}
              values={{ relative_path: readPath.trim() }}
              onResult={remember}
            >
              读取这个文件
            </ActionButton>
          ) : (
            <Tip>读取入口加载中…</Tip>
          )}
          <Tip>先用这里确认读取是否正常；如果这里失败，猫娘那边也会读到同样的错误原因。</Tip>
        </Stack>
      </Card>

      <Card title="分析操作">
        <Stack>
          {codeReviewAction && enableCodeReview ? (
            <ActionButton action={codeReviewAction} values={{}} onResult={remember}>
              代码审查
            </ActionButton>
          ) : (
            <Tip>代码审查（已在功能开关中关闭）</Tip>
          )}
          {errorFixAction && enableErrorFix ? (
            <ActionButton action={errorFixAction} values={{}} onResult={remember}>
              修复建议
            </ActionButton>
          ) : (
            <Tip>修复建议（已在功能开关中关闭）</Tip>
          )}
          {projectSummaryAction && enableProjectSummary ? (
            <ActionButton action={projectSummaryAction} values={{}} onResult={remember}>
              结构摘要
            </ActionButton>
          ) : (
            <Tip>结构摘要（已在功能开关中关闭）</Tip>
          )}
          {multiSummaryAction && enableMultiFileSummary ? (
            <ActionButton action={multiSummaryAction} values={{}} onResult={remember}>
              多文件汇总
            </ActionButton>
          ) : (
            <Tip>多文件汇总（已在功能开关中关闭）</Tip>
          )}
          {quickAuditAction && quickAuditEnabled ? (
            <ActionButton action={quickAuditAction} values={{}} onResult={remember}>
              一键开发审查
            </ActionButton>
          ) : (
            <Tip>一键开发审查（审查 / 修复 / 多文件汇总均已关闭）</Tip>
          )}
        </Stack>
      </Card>

      {lastResult ? (
        <Card title="最近一次结果">
          <Stack>
            <StatusBadge tone={lastOk ? "success" : "danger"} label={lastOk ? "成功" : "失败（含失败原因）"} />
            {!lastOk ? <Text>失败原因见下方 details / error 字段。</Text> : null}
            <JsonView data={lastResult} />
          </Stack>
        </Card>
      ) : null}
    </Page>
  )
}
