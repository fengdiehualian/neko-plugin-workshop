import { useEffect, useRef, useState } from "@neko/plugin-ui"
import type { TFunc } from "./types"
import type { IntroFlowNode } from "./capintro"
import { demoStory, demoUi } from "./capdemo_data"
import type { DemoStory } from "./capdemo_data"

type CapPrincipleDemoProps = { t: TFunc; id: string; nodes: IntroFlowNode[] }
type DemoSceneProps = {
  t: TFunc
  id: string
  story: DemoStory
  after: boolean
  phase: number
  privacy: boolean
  choice: string
  onPrivacy: () => void
  onChoice: (choice: string) => void
}
type DemoIconProps = { name: string }
type DemoNoteProps = { label: string; text: string; tone?: string; className?: string }
type DemoControlProps = { label: string; name: string; className: string; disabled?: boolean; onClick: () => void }

function nodeText(t: TFunc, node: IntroFlowNode): string {
  const value = node.label
  if (typeof value === "string") return value
  if (!value) return ""
  return value.$i18n ? t(value.$i18n, { defaultValue: value.default || "" }) : value.default || ""
}

function beatFor(step: number, count: number): number {
  return step === 0 ? 0 : step >= count - 1 ? 2 : 1
}

// The host links JSX exports safely only when every exported symbol precedes JSX helpers.
export function CapPrincipleDemo(props: CapPrincipleDemoProps) {
  const { t, id, nodes } = props
  const story = demoStory(t, id)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const clock = useRef(0)
  const currentStep = useRef(0)
  const [mode, setMode] = useState("after")
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [activePlaying, setActivePlaying] = useState(true)
  const [reduced, setReduced] = useState(false)
  const [epoch, setEpoch] = useState(0)
  const [privacy, setPrivacy] = useState(false)
  const [choice, setChoice] = useState("")
  const count = Math.max(3, nodes.length)
  const after = mode === "after"
  const phase = beatFor(step, count)

  useEffect(() => {
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null
    const update = () => {
      const next = !!media?.matches
      setReduced(next)
      if (next) {
        currentStep.current = count - 1
        clock.current = 8000
        setStep(count - 1)
        setPlaying(false)
      }
    }
    update()
    media?.addEventListener("change", update)
    return () => media?.removeEventListener("change", update)
  }, [count])

  useEffect(() => {
    const prefers = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    clock.current = prefers ? 8000 : 0
    currentStep.current = prefers ? count - 1 : 0
    setStep(currentStep.current)
    setMode("after")
    setPrivacy(false)
    setChoice("")
    setPlaying(!prefers)
  }, [id])

  useEffect(() => {
    const root = rootRef.current
    if (!root || !story) return
    let frame = 0
    let active = true
    let visible = !document.hidden
    let intersects = true
    let lastTime = 0
    const paint = () => {
      root.style.setProperty("--tm-demo-progress", String(Math.min(1, clock.current / 8000)))
      root.dataset.progress = (Math.min(1, clock.current / 8000)).toFixed(4)
    }
    const canRun = () => active && playing && !reduced && visible && intersects && clock.current < 8000
    const tick = (time: number) => {
      frame = 0
      if (!canRun()) return
      if (lastTime) clock.current = Math.min(8000, clock.current + Math.max(0, time - lastTime))
      lastTime = time
      const next = Math.min(count - 1, Math.floor(clock.current / (5700 / (count - 1))))
      if (next !== currentStep.current) {
        currentStep.current = next
        setStep(next)
      }
      paint()
      if (clock.current >= 8000) {
        root.dataset.playing = "false"
        setActivePlaying(false)
        setPlaying(false)
      } else frame = window.requestAnimationFrame(tick)
    }
    const sync = () => {
      lastTime = 0
      if (frame) window.cancelAnimationFrame(frame)
      frame = 0
      root.dataset.playing = canRun() ? "true" : "false"
      setActivePlaying(canRun())
      if (canRun()) frame = window.requestAnimationFrame(tick)
    }
    const onVisibility = () => {
      visible = !document.hidden
      sync()
    }
    const observer = typeof IntersectionObserver === "function" ? new IntersectionObserver((entries) => {
      intersects = !!entries[0]?.isIntersecting
      sync()
    }, { threshold: 0.05 }) : null
    observer?.observe(root)
    document.addEventListener("visibilitychange", onVisibility)
    paint()
    sync()
    return () => {
      active = false
      if (frame) window.cancelAnimationFrame(frame)
      observer?.disconnect()
      document.removeEventListener("visibilitychange", onVisibility)
      root.dataset.playing = "false"
    }
  }, [id, mode, playing, reduced, epoch, count])

  function restart(nextMode: string) {
    clock.current = reduced ? 8000 : 0
    currentStep.current = reduced ? count - 1 : 0
    setStep(currentStep.current)
    setMode(nextMode)
    setPrivacy(false)
    setChoice("")
    setPlaying(!reduced)
    setEpoch((value) => value + 1)
  }

  function goTo(next: number) {
    const target = Math.max(0, Math.min(count - 1, next))
    currentStep.current = target
    clock.current = target === count - 1 ? 8000 : target * (5700 / (count - 1))
    setStep(target)
    setPlaying(false)
    setEpoch((value) => value + 1)
  }

  function togglePlayback() {
    if (reduced) return
    if (!playing && clock.current >= 8000) restart(mode)
    else setPlaying(!playing)
  }

  if (!story) {
    return (
      <div className="tm-demo tm-demo-unavailable">
        <p>{demoUi(t, "unavailable")}</p>
        <div className="tm-demo-flow" aria-label={demoUi(t, "process")}>
          {nodes.map((node, index) => (
            <span key={index} className="tm-ci-node" data-kind={node.kind || "proc"}>{nodeText(t, node)}</span>
          ))}
        </div>
      </div>
    )
  }

  const blockedActivity = after && story.scene === "activity" && privacy
  const undecidedJournal = after && story.scene === "journal" && choice !== "write"
  const caption = !after ? story.before
    : blockedActivity ? demoUi(t, "privacyNotice")
      : undecidedJournal ? (choice === "later" ? demoUi(t, "notSaved") : demoUi(t, "choose"))
        : story.after
  const currentLabel = nodes[step] ? nodeText(t, nodes[step]) : demoUi(t, phase === 0 ? "input" : phase === 1 ? "process" : "output")
  const u = (key: string) => demoUi(t, key)

  return (
    <div className="tm-demo" ref={rootRef} data-mode={mode} data-step={step} data-phase={phase}
      data-scene={story.scene} data-feature={id} data-playing={activePlaying && playing && !reduced ? "true" : "false"}
      data-progress={(Math.min(1, clock.current / 8000)).toFixed(4)}
      data-reduced={reduced ? "true" : "false"} data-private={privacy ? "true" : "false"} data-choice={choice || "pending"}>
      <h4 className="tm-demo-heading">{t("panel.capintro.flowTitle", { defaultValue: "原理演示" })}</h4>
      <div className="tm-demo-toolbar">
        <div className="tm-demo-modes" role="group" aria-label={u("sample")}>
          <button type="button" className="tm-demo-mode-before" aria-pressed={!after ? "true" : "false"} onClick={() => restart("before")}>{u("before")}</button>
          <button type="button" className="tm-demo-mode-after" aria-pressed={after ? "true" : "false"} onClick={() => restart("after")}>{u("after")}</button>
        </div>
        <span className="tm-demo-sample"><DemoIcon name="flask" />{u("sample")}</span>
        <div className="tm-demo-controls">
          <DemoControl label={u("previous")} name="previous" className="tm-demo-prev" disabled={step === 0} onClick={() => goTo(step - 1)} />
          <DemoControl label={u(playing ? "pause" : "play")} name={playing ? "pause" : "play"} className="tm-demo-play" disabled={reduced} onClick={togglePlayback} />
          <DemoControl label={u("next")} name="next" className="tm-demo-next" disabled={step >= count - 1} onClick={() => goTo(step + 1)} />
          <DemoControl label={u("replay")} name="replay" className="tm-demo-replay" onClick={() => restart(mode)} />
        </div>
      </div>
      <div className="tm-demo-stage" role="figure" aria-label={`${u("sample")} · ${caption}`}>
        <div className="tm-demo-beatline" aria-hidden="true">
          <span data-active={phase === 0}>{u("input")}</span><span data-active={phase === 1}>{u("process")}</span><span data-active={phase === 2}>{u("result")}</span>
        </div>
        <div className="tm-demo-scene" key={`${id}-${mode}-${epoch}`}>
          <DemoScene t={t} id={id} story={story} after={after} phase={phase} privacy={privacy} choice={choice}
            onPrivacy={() => setPrivacy(!privacy)} onChoice={setChoice} />
        </div>
        <div className="tm-demo-caption">
          <span className="tm-demo-caption-mark" aria-hidden="true"><DemoIcon name={after && !blockedActivity && !undecidedJournal ? "check" : "info"} /></span>
          <p>{caption}</p>
        </div>
      </div>
      <div className="tm-demo-progress" role="progressbar" aria-label={u("process")} aria-valuemin={1}
        aria-valuemax={count} aria-valuenow={step + 1} aria-valuetext={currentLabel}><span /></div>
      <div className="tm-demo-flow" aria-label={u("process")}>
        {nodes.map((node, index) => (
          <button key={index} type="button" className="tm-ci-node tm-demo-flow-node" data-kind={node.kind || "proc"}
            data-active={index === step ? "true" : "false"} data-done={index < step ? "true" : "false"}
            aria-current={index === step ? "step" : undefined} onClick={() => goTo(index)}>
            <span className="tm-demo-step-number" aria-hidden="true">{index + 1}</span>
            <span className="tm-ci-node-label">{nodeText(t, node)}</span>
          </button>
        ))}
      </div>
      <div className="tm-demo-detail"><span>{story.detail}</span><span className="tm-demo-notice">{u("sampleNotice")}</span></div>
    </div>
  )
}

function DemoControl(props: DemoControlProps) {
  return (
    <button type="button" className={`tm-demo-icon-button ${props.className}`} title={props.label}
      aria-label={props.label} disabled={props.disabled} onClick={props.onClick}><DemoIcon name={props.name} /></button>
  )
}

function DemoIcon(props: DemoIconProps) {
  return <span className="tm-demo-icon" data-icon={props.name} aria-hidden="true" />
}

function DemoNote(props: DemoNoteProps) {
  return (
    <div className={`tm-demo-note ${props.className || ""}`} data-tone={props.tone || "neutral"}>
      <span className="tm-demo-label">{props.label}</span><p>{props.text}</p>
    </div>
  )
}

function DemoScene(props: DemoSceneProps) {
  if (props.story.scene === "calendar") return <CalendarScene {...props} />
  if (props.story.scene === "activity") return <ActivityScene {...props} />
  if (props.story.scene === "mood") return <MoodScene {...props} />
  if (props.story.scene === "tone") return <ToneScene {...props} />
  if (props.story.scene === "fragments") return <FragmentsScene {...props} />
  if (props.story.scene === "journal") return <JournalScene {...props} />
  if (props.story.scene === "review") return <ReviewScene {...props} />
  return <ChatScene {...props} />
}

function ChatScene(props: DemoSceneProps) {
  const { t, story, after } = props
  const u = (key: string) => demoUi(t, key)
  return (
    <div className="tm-demo-chat-layout">
      <div className="tm-demo-conversation">
        <span className="tm-demo-lane-title"><DemoIcon name="chat" />{u("chat")}</span>
        <div className="tm-demo-bubble tm-demo-incoming"><span className="tm-demo-label">{u("input")}</span><p>{story.input}</p></div>
        <div className="tm-demo-context" data-open={after}>
          <span className="tm-demo-label"><DemoIcon name="layers" />{after ? u("background") : u("noHint")}</span>
          <p>{after ? story.process : u("continues")}</p>
          <span className="tm-demo-context-slip" aria-hidden="true" />
        </div>
        <div className="tm-demo-bubble tm-demo-answer"><span className="tm-demo-label">{u("character")}</span><p>{after ? story.example : u("continues")}</p></div>
      </div>
      <div className="tm-demo-state-column">
        <span className="tm-demo-state-index" aria-hidden="true">01<span />02<span />03</span>
        <DemoNote label={u("process")} text={story.process} tone="neutral" className="tm-demo-process-note" />
        <span className="tm-demo-route" aria-hidden="true"><DemoIcon name="arrow" /></span>
        <p className="tm-demo-scene-footnote">{after ? story.output : u("noHint")}</p>
      </div>
    </div>
  )
}

function CalendarScene(props: DemoSceneProps) {
  const { t, id, story, after } = props
  const u = (key: string) => demoUi(t, key)
  const birthday = id === "birthday"
  const phase = id === "phase_openers"
  const highlight = phase ? 15 : birthday ? 18 : 20
  return (
    <div className="tm-demo-calendar-layout">
      <div className="tm-demo-calendar">
        <div className="tm-demo-calendar-top"><DemoIcon name="calendar" /><span>{story.input}</span></div>
        <div className="tm-demo-calendar-grid" aria-hidden="true">
          {Array.from({ length: 28 }, (_, index) => (
            <span key={index} data-target={index + 1 === highlight} data-passed={index + 1 < highlight}>
              {index + 1}
              {index + 1 === highlight ? <i><DemoIcon name={birthday ? "gift" : phase ? "spark" : "flag"} /></i> : null}
            </span>
          ))}
        </div>
        <div className="tm-demo-calendar-meta">{phase ? u("process") : u("once")}<span>{birthday ? u("noAge") : phase ? u("character") : "30 / 60 / 90 / 365"}</span></div>
      </div>
      <div className="tm-demo-calendar-delivery">
        <div className="tm-demo-date-gate" data-enabled={after}><DemoIcon name="check" /><p>{after ? story.process : u("noHint")}</p></div>
        <div className="tm-demo-envelope" data-open={after}>
          <span className="tm-demo-envelope-flap" aria-hidden="true" />
          <div className="tm-demo-letter"><span className="tm-demo-label">{u("background")}</span><p>{after ? story.example : u("continues")}</p></div>
          <span className="tm-demo-envelope-front" aria-hidden="true" />
        </div>
        <p className="tm-demo-scene-footnote">{after ? story.output : u("continues")}</p>
        {birthday && after ? <span className="tm-demo-small-tag"><DemoIcon name="book" />{u("optional")}</span> : null}
      </div>
    </div>
  )
}

function ActivityScene(props: DemoSceneProps) {
  const { t, story, after, privacy, onPrivacy } = props
  const u = (key: string) => demoUi(t, key)
  const permitted = after && !privacy
  return (
    <div className="tm-demo-activity-layout">
      <div className="tm-demo-host">
        <div className="tm-demo-monitor"><div className="tm-demo-monitor-screen"><DemoIcon name="monitor" /><p>{story.input}</p><div className="tm-demo-signal-bars" aria-hidden="true"><i /><i /><i /><i /><i /></div></div><span className="tm-demo-monitor-base" /></div>
        <span className="tm-demo-label">{u("host")}</span>
        <span className="tm-demo-small-tag"><DemoIcon name="shield" />{u("privacyNotice")}</span>
      </div>
      <div className="tm-demo-privacy-gate" data-closed={!permitted} data-route={permitted ? "open" : "closed"}>
        <DemoIcon name={privacy ? "lock" : "shield"} />
        <label className="tm-demo-privacy-control"><input className="tm-demo-privacy" type="checkbox" checked={privacy} onChange={onPrivacy} /><span>{u("privacy")}</span></label>
        <span className="tm-demo-gate-track" aria-hidden="true"><i /><i /><i /></span>
        <p>{privacy ? u("private") : after ? story.process : u("noHint")}</p>
      </div>
      <div className="tm-demo-activity-output" data-enabled={permitted}>
        <DemoIcon name={permitted ? "chat" : "lock"} />
        <span className="tm-demo-label">{u("background")}</span><p>{permitted ? story.output : u("noHint")}</p>
        <span className="tm-demo-small-tag">{permitted ? story.example : u("continues")}</span>
      </div>
    </div>
  )
}

function MoodScene(props: DemoSceneProps) {
  const { t, story, after } = props
  const u = (key: string) => demoUi(t, key)
  return (
    <div className="tm-demo-mood-layout">
      <div className="tm-demo-mood-in">
        <div className="tm-demo-bubble tm-demo-incoming"><span className="tm-demo-label">{u("character")}</span><p>{story.input}</p></div>
        <div className="tm-demo-tool-call" data-enabled={after}><DemoIcon name="wrench" /><span>{after ? story.process : u("continues")}</span></div>
      </div>
      <div className="tm-demo-mood-instrument" data-enabled={after}>
        <div className="tm-demo-mood-dial" aria-hidden="true"><div className="tm-demo-mood-needle" /><span /><span /><span /><span /><span /></div>
        <div className="tm-demo-mood-center"><DemoIcon name="heart" /><p>{after ? story.example : u("continues")}</p></div>
        <div className="tm-demo-mood-history" aria-hidden="true">{[18, 27, 21, 37, 44, 39, 55, 49, 61].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div>
        <span className="tm-demo-label">{story.output}</span>
      </div>
      <div className="tm-demo-mood-out">
        <DemoNote label={u("result")} text={after ? story.output : u("continues")} tone={after ? "success" : "neutral"} />
        <span className="tm-demo-clock"><DemoIcon name="clock" /><i /><span>{u("process")}</span></span>
      </div>
    </div>
  )
}

function ToneScene(props: DemoSceneProps) {
  const { t, story, after } = props
  const u = (key: string) => demoUi(t, key)
  return (
    <div className="tm-demo-tone-layout">
      <div className="tm-demo-fast-lane"><span className="tm-demo-lane-title"><DemoIcon name="chat" />{u("chat")}</span><div className="tm-demo-inline-message"><span>{story.input}</span><DemoIcon name="arrow" /><span>{u("continues")}</span><DemoIcon name="check" /></div></div>
      <div className="tm-demo-parallel-split" aria-hidden="true"><span /><i /><span /></div>
      <div className="tm-demo-analysis-lane" data-enabled={after}>
        <span className="tm-demo-lane-title"><DemoIcon name="scan" />{u("background")}</span>
        <div className="tm-demo-tone-scan"><div className="tm-demo-wave" aria-hidden="true">{[12, 26, 19, 32, 17, 37, 22, 13, 25, 15, 29, 18, 10, 20, 12].map((height, index) => <i key={index} style={{ height: `${height}px` }} />)}<span /></div><p>{after ? story.process : u("noHint")}</p></div>
        <div className="tm-demo-analysis-result"><span className="tm-demo-label">{u("character")}</span><p>{after ? story.output : u("continues")}</p></div>
      </div>
      <p className="tm-demo-scene-footnote tm-demo-tone-footnote">{after ? story.example : u("noHint")}</p>
    </div>
  )
}

function FragmentsScene(props: DemoSceneProps) {
  const { t, story, after } = props
  const u = (key: string) => demoUi(t, key)
  return (
    <div className="tm-demo-fragments-layout">
      <div className="tm-demo-quote-stack"><span className="tm-demo-label">{u("chat")}</span>
        <blockquote className="tm-demo-quote"><span aria-hidden="true">“</span><p>{story.input}</p><span className="tm-demo-highlight" aria-hidden="true" /></blockquote>
        <div className="tm-demo-text-line" aria-hidden="true" /><div className="tm-demo-text-line" aria-hidden="true" />
      </div>
      <div className="tm-demo-fragment-filter" data-enabled={after}><DemoIcon name="filter" /><p>{after ? story.process : u("noHint")}</p><span className="tm-demo-filter-slits" aria-hidden="true"><i /><i /><i /></span></div>
      <div className="tm-demo-fragment-book" data-enabled={after}>
        <div className="tm-demo-book-tab" aria-hidden="true" />
        <span className="tm-demo-label"><DemoIcon name="quote" />{u("notebook")}</span>
        <p>{after ? story.example : u("saved")}</p>
        <span className="tm-demo-original-quote">{after ? story.input : u("noCapture")}</span>
        <span className="tm-demo-book-lines" aria-hidden="true"><i /><i /></span>
      </div>
      <p className="tm-demo-scene-footnote">{after ? story.output : u("continues")}</p>
    </div>
  )
}

function JournalScene(props: DemoSceneProps) {
  const { t, story, after, phase, choice, onChoice } = props
  const u = (key: string) => demoUi(t, key)
  const written = after && choice === "write" && phase === 2
  return (
    <div className="tm-demo-journal-layout">
      <div className="tm-demo-invitation">
        <span className="tm-demo-label"><DemoIcon name="mail" />{after ? u("invitation") : u("noInvite")}</span><p>{after ? story.process : u("noInvite")}</p>
        <div className="tm-demo-materials" aria-hidden="true"><span /><span /><span /></div>
        <span className="tm-demo-invitation-time"><DemoIcon name="clock" />{story.input}</span>
      </div>
      <div className="tm-demo-journal-choice">
        <span className="tm-demo-choice-line" aria-hidden="true" />
        <span className="tm-demo-label">{u("character")}</span>
        <p>{after ? u("choose") : u("noInvite")}</p>
        <div className="tm-demo-choice-buttons">
          <button type="button" className="tm-demo-journal-write" aria-pressed={choice === "write"} disabled={!after || phase === 0} onClick={() => onChoice("write")}><DemoIcon name="pen" />{u("write")}</button>
          <button type="button" className="tm-demo-journal-later" aria-pressed={choice === "later"} disabled={!after || phase === 0} onClick={() => onChoice("later")}><DemoIcon name="clock" />{u("later")}</button>
        </div>
      </div>
      <div className="tm-demo-journal-page" data-written={written}>
        <span className="tm-demo-label"><DemoIcon name="book" />{u("notebook")}</span><p>{written ? story.example : after ? u("notSaved") : u("saved")}</p>
        <div className="tm-demo-writing-lines" aria-hidden="true"><i /><i /><i /><i /></div>
        <span className="tm-demo-page-status">{written ? `${u("sample")} · ${u("complete")}` : !after ? u("noInvite") : choice === "later" ? u("later") : u("pending")}</span>
      </div>
      <p className="tm-demo-scene-footnote">{written ? story.output : u("notSaved")}</p>
    </div>
  )
}

function ReviewScene(props: DemoSceneProps) {
  const { t, story, after } = props
  const u = (key: string) => demoUi(t, key)
  return (
    <div className="tm-demo-review-layout">
      <div className="tm-demo-review-sources">
        <span className="tm-demo-label">{story.input}</span>
        <div className="tm-demo-source-row"><DemoIcon name="chat" /><span>{u("chat")}</span><i /><i /><i /><i /></div>
        <div className="tm-demo-source-row"><DemoIcon name="heart" /><span>{u("local")}</span><div className="tm-demo-mini-chart"><i /><i /><i /><i /><i /></div></div>
        <div className="tm-demo-source-row"><DemoIcon name="quote" /><span>{u("notebook")}</span><span className="tm-demo-mini-sheets" aria-hidden="true"><i /><i /><i /></span></div>
      </div>
      <div className="tm-demo-review-compiler" data-enabled={after}><DemoIcon name="layers" /><p>{after ? story.process : u("noReview")}</p><span className="tm-demo-compiler-packets" aria-hidden="true"><i /><i /><i /></span></div>
      <div className="tm-demo-review-page" data-enabled={after}>
        <span className="tm-demo-label"><DemoIcon name="book" />{u("background")}</span><p>{after ? story.example : u("noReview")}</p>
        <span className="tm-demo-review-rule" /><p className="tm-demo-review-destination">{after ? story.output : u("continues")}</p>
        <span className="tm-demo-small-tag"><DemoIcon name="monitor" />{u("local")}</span>
      </div>
    </div>
  )
}
