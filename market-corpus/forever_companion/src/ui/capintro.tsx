// 功能介绍是功能页内的只读子页：先说明作用、场景、边界和使用条件，再展示
// 原理演示。配置键与模型工具名放在默认收起的技术信息中，不混入依赖标签。
//
// 数据契约（不变）：文案事实源在 core/intros.py（中文原文 + key 派生），经
// get_capability_intro 入口以 SDK 的 tr() 引用（{"$i18n","default"}）透传——
// action 返回值不经宿主 i18n 解析（只有 dashboard context 会），所以这里
// 用 resolveText() 手动展开：zh 走 default，en 等语言进 i18n/*.json 补同名
// key 即生效。依赖链/LLM 触点/配置键从声明表 payload 现场取，与开关同源。
//
// 原理演示独立于真实功能开关；具体场景、动画和分步查看在 capdemo.tsx。
import { StatusBadge } from "@neko/plugin-ui"
import type { CapItem, TFunc } from "./types"
import { CapPrincipleDemo } from "./capdemo"

// ---- 文案引用解析（$i18n 引用 / 裸字符串都吃得下）----
type I18nRef = { $i18n?: string; default?: string }
export type IntroText = string | I18nRef

// 签名纪律（1.2.7 白屏事故 + 二轮修订二进宫复盘，双向复现验证）：
// hosted 链接器的导出扫描器扫到带 JSX 的函数时（不分导出不导出！）会状态
// 漂移，把其后所有 `export function/const` 漏采——编译产物残留裸 export →
// iframe 语法错、整面白屏，本地 tsc 查不出。故：只导出跨文件引用的符号；
// 一切带 JSX 的函数一律排在全部导出之后；
// 确需多导出的函数用命名类型 + 单行签名，不要把带 JSX 的导出排在它们前面；
// 改版 ui/*.tsx 后必须过仓内 hosted 链接自检（能完整复现此坑，比肉眼可靠）。
export function resolveText(t: TFunc, v: IntroText | null | undefined): string {
  if (v == null) return ""
  if (typeof v === "string") return v
  const key = String(v.$i18n || "")
  const def = String(v.default || "")
  if (!key) return def
  return t(key, { defaultValue: def })
}

function listTexts(t: TFunc, list: IntroText[] | null | undefined): string[] {
  return (list || []).map((v) => resolveText(t, v)).filter((s) => !!s)
}

// ---- 介绍入口 payload（与 core/intros.py build_intro_payload 对齐）----
export type IntroFlowNode = { kind?: string; label?: IntroText }
export type CapIntroPayload = {
  id?: string
  found?: boolean
  purpose?: IntroText
  scenarios?: IntroText[]
  limits?: IntroText[]
  flow?: IntroFlowNode[]
  actions?: { id: string; label: IntroText; description: IntroText }[]
  deps?: string[]
  config_keys?: string[]
  llm?: string
  tools?: string[]
}

// LLM 触点徽标表（1.2.7 起从 features.tsx 迁移至此统一维护：
// 功能行与介绍视图共用一张表，features.tsx 反向 import，避免两处漂移）。
// 类型别名绕开校验器：export const 的注解里不能带泛型尖括号的逗号（见 utils.ts 同款注释）
type LlmBadge = { key: string; def: string; tone: "warning" | "success" | "default" }
type LlmBadgeMap = Record<string, LlmBadge>

export const LLM_BADGES: LlmBadgeMap = {
  tool: { key: "panel.features.llm.tool", def: "模型工具", tone: "warning" },
  direct: { key: "panel.features.llm.direct", def: "模型服务", tone: "warning" },
  host_http: { key: "panel.features.llm.host", def: "宿主模型", tone: "warning" },
  injection: { key: "panel.features.llm.injection", def: "后台提示", tone: "default" },
  none: { key: "panel.features.llm.local", def: "本地处理", tone: "success" },
}

type LlmNote = { key: string; def: string }
type LlmNoteMap = Record<string, LlmNote>
const LLM_NOTES: LlmNoteMap = {
  tool: {
    key: "panel.capintro.modelTool",
    def: "由角色模型决定是否调用工具，模型需支持工具调用。",
  },
  direct: {
    key: "panel.capintro.modelDirect",
    def: "后台请求模型生成结果；默认通过 N.E.K.O 的模型通道，也可按设置直连服务。",
  },
  host_http: {
    key: "panel.capintro.modelHost",
    def: "通过 N.E.K.O 的情感分析服务分析语气，也可按设置直连模型。",
  },
  injection: {
    key: "panel.capintro.modelInjection",
    def: "把提示加入对话上下文，由角色模型决定如何表达。",
  },
  none: {
    key: "panel.capintro.modelLocal",
    def: "在本地读取和整理状态，不单独调用模型；结果可随状态提示加入对话。",
  },
}

// props 命名类型（单行签名配套）
type CapIntroViewProps = {
  t: TFunc
  item: CapItem
  capLabel: (id: string) => string
  statusHint: string
  intro: CapIntroPayload | null
  loading?: boolean
  error?: string
  onBack: () => void
}

// 介绍子页本体（features.tsx 唯一引用的视图导出；带 JSX，必须排在
// 全部非 JSX 导出之后——见签名纪律注释）
export function CapIntroView(props: CapIntroViewProps) {
  const { t, item, capLabel, statusHint, intro, loading, error, onBack } = props

  const scenarios = listTexts(t, intro && intro.scenarios)
  const limits = listTexts(t, intro && intro.limits)
  const actions = (intro && intro.actions) || []
  const deps = (intro && intro.deps) || []
  const configKeys = (intro && intro.config_keys) || []
  const tools = (intro && intro.tools) || []
  const llm = (intro && intro.llm) || item.llm
  const badge = LLM_BADGES[llm] || LLM_BADGES.none
  const modelNote = LLM_NOTES[llm] || LLM_NOTES.none

  return (
    <div className="tm-pane tm-ci-page">
      <div className="tm-ci-head">
        <button type="button" className="tm-ci-back" onClick={onBack}>
          <span className="tm-ci-back-icon" aria-hidden="true" />
          {t("panel.capintro.back", { defaultValue: "返回功能列表" })}
        </button>
        <h3 className="tm-ci-title">{capLabel(item.id)}</h3>
        <span className="tm-ci-head-spacer" />
        <StatusBadge
          tone={item.enabled ? "success" : "warning"}
          label={item.enabled
            ? t("panel.capintro.stateOn", { defaultValue: "已启用" })
            : (statusHint || t("panel.capintro.stateOff", { defaultValue: "当前未生效" }))}
        />
      </div>

      {error ? (
        <div className="tm-ci-error">{error}</div>
      ) : null}
      {loading ? (
        <div className="tm-ci-loading">{t("panel.capintro.loading", { defaultValue: "介绍加载中…" })}</div>
      ) : null}
      {!loading && !error && intro && intro.found === false ? (
        <div className="tm-ci-empty">
          {t("panel.capintro.notFound", { defaultValue: "该功能暂无介绍内容" })}
        </div>
      ) : null}
      {!loading && !error && intro && intro.found !== false ? (
        <div className="tm-ci">
          <section className="tm-ci-sec tm-ci-summary" data-section="purpose">
            <h4 className="tm-ci-h">{t("panel.capintro.purpose", { defaultValue: "功能作用" })}</h4>
            <p className="tm-ci-purpose">{resolveText(t, intro.purpose)}</p>
          </section>
          {actions.length ? (
            <section className="tm-ci-sec tm-ci-actions" data-section="actions">
              <h4 className="tm-ci-h">{t("panel.capintro.mood_engine.actionsTitle", { defaultValue: "情绪状态与结束方式" })}</h4>
              <dl className="tm-ci-facts">
                {actions.map((action) => (
                  <div key={action.id} className="tm-ci-fact" data-action={action.id}>
                    <dt>{resolveText(t, action.label)}</dt>
                    <dd>{resolveText(t, action.description)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ) : null}
          <div className="tm-ci-grid">
            <section className="tm-ci-sec tm-ci-scenarios" data-section="scenarios">
              <h4 className="tm-ci-h">{t("panel.capintro.scenarios", { defaultValue: "主要场景" })}</h4>
              <ul className="tm-ci-list">
                {scenarios.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </section>
            <section className="tm-ci-sec tm-ci-limits" data-section="limits">
              <h4 className="tm-ci-h">{t("panel.capintro.limits", { defaultValue: "限制与注意事项" })}</h4>
              <ul className="tm-ci-list tm-ci-list-warn">
                {limits.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </section>
          </div>
          <section className="tm-ci-sec tm-ci-requirements" data-section="requirements">
            <h4 className="tm-ci-h">{t("panel.capintro.requirements", { defaultValue: "使用条件" })}</h4>
            <p className="tm-ci-prerequisite">
              {t("panel.capintro.masterRequired", { defaultValue: "需要开启当前角色的模拟总开关。" })}
            </p>
            <dl className="tm-ci-facts">
              <div className="tm-ci-fact" data-kind="deps">
                <dt>{t("panel.capintro.deps", { defaultValue: "所需功能" })}</dt>
                <dd>
                  {deps.length ? (
                    <ul className="tm-ci-deps">
                      {deps.map((d) => <li key={d}>{capLabel(d)}</li>)}
                    </ul>
                  ) : t("panel.capintro.depsNone", { defaultValue: "不需要开启其他功能" })}
                </dd>
              </div>
              <div className="tm-ci-fact" data-kind="model">
                <dt>{t("panel.capintro.model", { defaultValue: "模型交互" })}</dt>
                <dd className="tm-ci-model">
                  <StatusBadge tone={badge.tone} label={t(badge.key, { defaultValue: badge.def })} />
                  <p>{t(modelNote.key, { defaultValue: modelNote.def })}</p>
                </dd>
              </div>
            </dl>
          </section>
          <section className="tm-ci-sec tm-ci-demo-section" data-section="demo" aria-label={t("panel.capintro.flowTitle", { defaultValue: "原理演示" })}>
            <CapPrincipleDemo t={t} id={item.id} nodes={intro.flow || []} />
          </section>
          <details key={item.id} className="tm-ci-technical">
            <summary className="tm-ci-tech-summary">
              {t("panel.capintro.technical", { defaultValue: "技术信息" })}
            </summary>
            <dl className="tm-ci-facts">
              <div className="tm-ci-fact" data-kind="config">
                <dt>{t("panel.capintro.config", { defaultValue: "启用配置键" })}</dt>
                <dd>
                  {configKeys.length ? (
                    <ul className="tm-ci-code-list">
                      {configKeys.map((k) => <li key={k}><code className="tm-ci-mono">{k}</code></li>)}
                    </ul>
                  ) : t("panel.capintro.configNone", { defaultValue: "未绑定独立的启用配置键" })}
                </dd>
              </div>
              <div className="tm-ci-fact" data-kind="tools">
                <dt>{t("panel.capintro.tools", { defaultValue: "模型工具" })}</dt>
                <dd>
                  {tools.length ? (
                    <div className="tm-ci-tools">
                      <p className="tm-ci-tools-count">
                        {t("panel.capintro.toolsCount", { defaultValue: "提供 {n} 个模型工具", n: tools.length })}
                      </p>
                      <ul className="tm-ci-code-list">
                        {tools.map((tool) => <li key={tool}><code className="tm-ci-mono">{tool}</code></li>)}
                      </ul>
                    </div>
                  ) : t("panel.capintro.toolsNone", { defaultValue: "不提供模型工具" })}
                </dd>
              </div>
            </dl>
          </details>
        </div>
      ) : null}
    </div>
  )
}
