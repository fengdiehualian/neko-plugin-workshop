import { Alert, Button, Card, Field, Grid, InlineError, Input, NumberInput, PasswordInput, Stack, TextBlock, useI18n } from "@neko/plugin-ui"
import type { SettingsController } from "./useSettings"
import { BUDGET_FIELDS } from "../types"
import { budgetError } from "../format"
import { SaveStatus } from "./save-status"

export function SettingsPage({ c }: { c: SettingsController }) {
  const { t } = useI18n()
  const busy = c.savingBridge || c.savingAgent
  const invalidBudget = budgetError(c.budgets, BUDGET_FIELDS, t)
  return <div style={{ maxWidth: "1000px", marginInline: "auto" }}><Stack>
    <TextBlock text={t("settings.intro")} />
    <Card title={t("settings.connection")}><Stack>
      <TextBlock text={t("settings.pairHelp")} />
      <Grid cols={2}>
        <Field label={t("settings.port")} required><NumberInput value={c.port} min={1} max={65535} step={1} disabled={busy} onChange={value => c.changePort(value === "" ? "" : Number(value))} /></Field>
        <Field label={t("settings.token")} help={t(c.state.configuration?.token_configured ? "settings.keepToken" : "settings.needToken")}>
          <PasswordInput value={c.pairingToken} disabled={busy} onChange={c.changeToken} />
        </Field>
      </Grid>
      {c.bridgeError ? <InlineError title={t("settings.checkFields")} error={c.bridgeError} /> : null}
      <Button tone="primary" disabled={busy || !c.bridgeDirty} onClick={c.saveBridge}>{t(c.savingBridge ? "settings.saving" : "settings.saveBridge")}</Button>
      <SaveStatus receipt={c.bridgeReceipt} check={c.checkBridge} checking={c.checking} dirty={c.bridgeDirty} />
    </Stack></Card>
    <Card title={t("settings.model")}><Stack>
      <TextBlock text={t("settings.modelHelp")} />
      <Grid cols={2}>
        <Field label={t("settings.url")} help={t("settings.urlHelp")} required><Input value={c.baseUrl} disabled={busy} onChange={c.changeBaseUrl} placeholder="https://example.com/v1" /></Field>
        <Field label={t("settings.modelName")} required><Input value={c.model} disabled={busy} onChange={c.changeModel} /></Field>
      </Grid>
      <Field label={t("settings.apiKey")} help={t(c.state.configuration?.agent_api_key_configured ? "settings.keepKey" : "settings.needKey")}>
        <PasswordInput value={c.apiKey} disabled={busy} onChange={c.changeApiKey} />
      </Field>
      {c.agentError ? <InlineError title={t("settings.checkFields")} error={c.agentError} /> : null}
      <Button tone="primary" disabled={busy || !c.agentDirty || !!invalidBudget || !c.baseUrl.trim() || !c.model.trim()} onClick={c.saveAgent}>{t(c.savingAgent ? "settings.saving" : "settings.saveModel")}</Button>
      <SaveStatus receipt={c.agentReceipt} check={c.checkAgent} checking={c.checking} dirty={c.agentDirty} />
      <details><summary style={{ cursor: "pointer", paddingBlock: "8px" }}>{t("settings.advanced")}</summary><Stack>
        <TextBlock text={t("settings.budgetHelp")} />
        <TextBlock text={t("settings.retryHelp")} />
        <Grid cols={2}>{BUDGET_FIELDS.map(field => <Field key={field.key} label={t(`settings.budget.${field.key}`)} help={t(field.key === "max_output_tokens" ? "settings.tokenRange" : "settings.secondRange", { min: field.min, max: field.max })} required>
          <NumberInput value={c.budgets[field.key]} min={field.min} max={field.max} step={1} disabled={busy} onChange={value => c.changeBudget(field.key, value === "" ? "" : Number(value))} />
        </Field>)}</Grid>
        {invalidBudget ? <Alert tone="warning">{invalidBudget}</Alert> : null}
      </Stack></details>
    </Stack></Card>
    <TextBlock text={t("settings.draftHelp")} />
  </Stack></div>
}
