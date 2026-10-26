import { Alert, Button, Stack, TextBlock, useI18n } from "@neko/plugin-ui"
import type { SettingsReceipt } from "../types"

export function SaveStatus({ receipt, check, checking, dirty }: { receipt: SettingsReceipt | null; check: () => void; checking: boolean; dirty: boolean }) {
  const { t } = useI18n()
  if (!receipt) return null
  const state = receipt.state
  return <Stack>
    <div role="status" aria-live="polite"><Alert tone={state === "saved" ? "success" : state === "saving" ? "info" : "warning"}>
      {t(state === "saved" && dirty ? "settings.save.savedWithDraft" : `settings.save.${state}`)}
    </Alert></div>
    {state === "unconfirmed" ? <TextBlock text={t("settings.save.unconfirmedHelp")} /> : null}
    {state === "failed" ? <TextBlock text={t(`settings.error.${receipt.error_code}`, { defaultValue: t("settings.save.failedHelp") })} /> : null}
    {state !== "saved" ? <Button disabled={checking} onClick={check}>{t(checking ? "settings.save.checking" : "settings.save.check")}</Button> : null}
  </Stack>
}
