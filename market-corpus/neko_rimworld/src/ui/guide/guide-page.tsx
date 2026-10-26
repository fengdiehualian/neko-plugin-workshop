import { Alert, Button, Card, CodeBlock, FileDownload, Stack, TextBlock, useI18n } from "@neko/plugin-ui"
import { GUIDE_LINKS, GUIDE_SECTIONS, PLUGIN_BUILD_COMMANDS } from "./content"

export default function RimWorldSetupGuide() {
  const { t } = useI18n()
  const paragraph = (key: string) => <TextBlock text={t(`guide.${key}`)} />
  const steps = (section: string, count: number) => <ol style={{ margin: 0, paddingInlineStart: "24px" }}>
    {Array.from({ length: count }, (_, index) => <li key={index} style={{ marginBlock: "8px" }}>{t(`guide.${section}.step${index + 1}`)}</li>)}
  </ol>
  const link = (key: keyof typeof GUIDE_LINKS) => <FileDownload href={GUIDE_LINKS[key]} label={t(`guide.link.${key}`)} tone="default" />
  const jump = (id: string) => {
    const section = document.getElementById(`rimworld-guide-${id}`)
    section?.scrollIntoView({ block: "start" })
    section?.focus({ preventScroll: true })
  }
  const chapter = (id: typeof GUIDE_SECTIONS[number], children: any) => <section id={`rimworld-guide-${id}`} tabIndex={-1}
    aria-label={t(`guide.${id}.title`)} style={{ scrollMarginTop: "12px" }}>
    <Card title={t(`guide.${id}.title`)}><Stack>{children}</Stack></Card>
  </section>
  return <div className="rimworld-guide" style={{ maxWidth: "1000px", width: "100%", minWidth: 0, marginInline: "auto", lineHeight: 1.8, overflowWrap: "anywhere" }}>
    <style>{`
      .rimworld-guide .neko-stack { min-width: 0; }
      .rimworld-guide .neko-card-body { grid-template-columns: minmax(0, 1fr); }
      .rimworld-guide .neko-code { max-width: 100%; box-sizing: border-box; white-space: pre-wrap; overflow-wrap: anywhere; }
      .rimworld-guide .neko-button { max-width: 100%; white-space: normal; }
    `}</style><Stack>
    <h2 style={{ margin: 0 }}>{t("guide.title")}</h2>
    {paragraph("intro")}
    <nav aria-label={t("guide.contents")} style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
      {GUIDE_SECTIONS.map(id => <Button key={id} onClick={() => jump(id)}>{t(`guide.${id}.title`)}</Button>)}
    </nav>
    {chapter("prepare", <>
      {paragraph("prepare.intro")}
      <ul style={{ margin: 0, paddingInlineStart: "24px" }}>
        {["game", "host", "bridge", "model"].map(id => <li key={id}>{t(`guide.prepare.${id}`)}</li>)}
      </ul>
      <ul style={{ margin: 0, paddingInlineStart: "24px" }}>
        <li>{link("host")} · {link("hostHelp")}</li>
        <li>{link("harmony")}</li>
        <li>{link("mod")} · {link("modRelease")}</li>
        <li>{link("plugin")} · {link("pluginSource")}</li>
      </ul>
      <Alert tone="info">{t("guide.prepare.availability")}</Alert>
      {paragraph("prepare.hostStart")}
      {paragraph("prepare.scope")}
    </>)}
    {chapter("mod", <>
      {steps("mod", 5)}
      <CodeBlock>RimWorld/Mods/NekoRimWorld/About/About.xml</CodeBlock>
      {paragraph("mod.check")}
      {paragraph("mod.update")}
    </>)}
    {chapter("plugin", <>
      {paragraph("plugin.intro")}
      {steps("plugin", 4)}
      {paragraph("plugin.success")}
      <details><summary style={{ cursor: "pointer", paddingBlock: "8px" }}>{t("guide.build.title")}</summary><Stack>
        {paragraph("build.intro")}
        {paragraph("build.checkout")}
        <CodeBlock>git clone https://github.com/Project-N-E-K-O/N.E.K.O.git{`\n`}cd N.E.K.O</CodeBlock>
        {paragraph("build.commands")}
        <CodeBlock>{PLUGIN_BUILD_COMMANDS}</CodeBlock>
        {paragraph("build.result")}
      </Stack></details>
    </>)}
    {chapter("pair", <>
      {paragraph("pair.intro")}
      {steps("pair", 6)}
      {paragraph("pair.success")}
      {paragraph("pair.secret")}
    </>)}
    {chapter("model", <>
      {paragraph("model.intro")}
      {steps("model", 4)}
      <Alert tone="info">{t("guide.model.cost")}</Alert>
      {paragraph("model.saved")}
      {paragraph("model.receipt")}
    </>)}
    {chapter("chat", <>
      {steps("chat", 4)}
      <blockquote style={{ margin: 0, paddingInlineStart: "16px", borderInlineStart: "3px solid currentColor" }}>{t("guide.chat.question")}</blockquote>
      {paragraph("chat.progress")}
      {paragraph("chat.test")}
    </>)}
    {chapter("answers", <>
      {["latest", "history", "continue", "export", "next"].map(id => <TextBlock key={id} text={t(`guide.answers.${id}`)} />)}
    </>)}
    {chapter("help", <>
      {paragraph("help.intro")}
      {["missing", "connection", "world", "model", "chat", "save", "answer"].map(id => <details key={id}>
        <summary style={{ cursor: "pointer", paddingBlock: "8px" }}>{t(`guide.help.${id}.title`)}</summary>
        <TextBlock text={t(`guide.help.${id}.body`)} />
      </details>)}
      {paragraph("help.share")}
    </>)}
  </Stack></div>
}
