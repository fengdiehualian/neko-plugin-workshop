// Run against a fresh, isolated copy of the real host, never a user's profile.
// NEKO_RECEIPT_TEST_BASE_URL=http://127.0.0.1:PORT
// NEKO_RECEIPT_TEST_OUTPUT=ABSOLUTE_EVIDENCE_DIRECTORY
import assert from "node:assert/strict"
import fs from "node:fs/promises"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const host = fileURLToPath(new URL("../../../../", import.meta.url))
const { chromium } = await import(pathToFileURL(path.join(host, "frontend/plugin-manager/node_modules/playwright/index.mjs")))
const base = process.env.NEKO_RECEIPT_TEST_BASE_URL
const output = process.env.NEKO_RECEIPT_TEST_OUTPUT
assert.ok(base && /^http:\/\/127\.0\.0\.1:\d+$/.test(base), "An explicit loopback test host is required")
assert.ok(output && path.isAbsolute(output), "An absolute evidence directory is required")
await fs.mkdir(output, { recursive: true })
const result = { checks: [], pageErrors: [], savePosts: 0, consultPosts: 0 }
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, locale: "zh-CN" })
page.setDefaultTimeout(15000)
page.on("pageerror", error => result.pageErrors.push(error.message))
page.on("request", request => {
  if (request.method() === "POST" && request.url().endsWith("/begin_settings_save")) result.savePosts++
  if (request.method() === "POST" && request.url().endsWith("/rimworld_consult")) result.consultPosts++
})
function check(name, passed, details = {}) {
  result.checks.push({ name, passed: !!passed, ...details })
  console.log(name, !!passed)
  assert.ok(passed, name)
}
async function context() {
  const response = await page.request.get(base + "/plugin/neko_rimworld/hosted-ui/context")
  assert.ok(response.ok())
  return (await response.json()).state
}
async function settings() {
  await page.goto(base + "/ui/plugins/neko_rimworld", { waitUntil: "domcontentloaded" })
  await page.getByRole("tab", { name: "面板", exact: true }).click()
  const frame = page.frameLocator("iframe")
  await frame.getByRole("tab", { name: "设置", exact: true }).click()
  return frame
}
async function submit(frame) {
  const response = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/begin_settings_save"))
  await frame.getByRole("button", { name: "保存模型设置", exact: true }).click()
  const accepted = await (await response).json()
  assert.match(accepted.result.operation_id, /^[a-f0-9]{32}$/)
  return accepted.result.operation_id
}
async function receipt(id) {
  for (let i = 0; i < 80; i++) {
    const value = (await context()).settings_saves?.find(value => value.operation_id === id)
    if (value && value.state !== "saving") return value
    await page.waitForTimeout(150)
  }
  throw new Error("The isolated save did not finish")
}
try {
  // Refuse to replace a real user's configured model or credential.
  const initial = await context()
  assert.ok(!initial.configuration?.agent_model && !initial.configuration?.agent_api_key_configured,
    "Acceptance requires a fresh isolated plugin profile")
  let frame = await settings()
  await frame.getByRole("textbox", { name: /服务地址/ }).fill(base + "/unused-model/v1")
  await frame.getByRole("textbox", { name: /模型名称/ }).fill("receipt-acceptance-v1")
  await frame.locator('input[type="password"]').nth(1).fill("isolated-acceptance-key")
  const savedId = await submit(frame)
  await page.goto(base + "/ui/plugins", { waitUntil: "domcontentloaded" })
  const saved = await receipt(savedId)
  check("save_completes_with_panel_closed", saved.state === "saved" && saved.persisted === true && saved.applied === true, { operation_id: savedId })
  frame = await settings()
  await frame.getByText("设置已保存并应用，对下一次咨询生效。", { exact: true }).waitFor()
  check("complete_iframe_reopen_restores_success", await frame.getByRole("textbox", { name: /模型名称/ }).inputValue() === "receipt-acceptance-v1")
  check("reopen_does_not_restore_secret_input", await frame.locator('input[type="password"]').nth(1).inputValue() === "")
  check("reopen_does_not_repeat_save", result.savePosts === 1)
  await page.screenshot({ path: path.join(output, "reopened-success.png"), fullPage: true })

  // The UI accepts non-empty input; the real plugin rejects this invalid URL.
  await frame.getByRole("textbox", { name: /服务地址/ }).fill("not-a-url")
  await frame.getByRole("textbox", { name: /模型名称/ }).fill("must-not-be-applied")
  const failedId = await submit(frame)
  await page.goto(base + "/ui/plugins", { waitUntil: "domcontentloaded" })
  const failed = await receipt(failedId)
  check("invalid_save_has_definite_failed_receipt", failed.state === "failed" && failed.persisted === false && failed.applied === false, { operation_id: failedId })
  frame = await settings()
  await frame.getByText("这次设置没有应用。请检查下面的提示后再试。", { exact: true }).waitFor()
  check("complete_iframe_reopen_restores_failure", await frame.getByText("模型设置未通过检查，请核对地址、名称和高级设置。", { exact: true }).isVisible())
  check("failure_keeps_previous_configuration", await frame.getByRole("textbox", { name: /模型名称/ }).inputValue() === "receipt-acceptance-v1" && (await context()).configuration.agent_model === "receipt-acceptance-v1")
  check("failure_reopen_does_not_repeat_save", result.savePosts === 2)
  await page.screenshot({ path: path.join(output, "reopened-failure.png"), fullPage: true })

  // Complete another save, then restart the real SDK subprocess.
  await frame.getByRole("textbox", { name: /模型名称/ }).fill("receipt-acceptance-v2")
  const restartId = await submit(frame)
  await receipt(restartId)
  await frame.getByText("设置已保存并应用，对下一次咨询生效。", { exact: true }).waitFor()
  await frame.locator('input[type="password"]').nth(1).fill("unsaved-draft-after-save")
  const oldNonce = (await context()).action_nonce
  const csrf = (await (await page.request.get(base + "/security/csrf-token")).json()).csrf_token
  const restarted = await page.request.post(base + "/plugin/neko_rimworld/reload", { headers: { "X-CSRF-Token": csrf, Origin: base }, timeout: 45000 })
  assert.ok(restarted.ok())
  const afterRestart = await context()
  check("real_backend_restart_discards_receipts_and_keeps_config", afterRestart.action_nonce !== oldNonce && afterRestart.settings_saves.length === 0 && afterRestart.configuration.agent_model === "receipt-acceptance-v2")
  await frame.getByRole("button", { name: "更新连接状态", exact: true }).click()
  await frame.getByText("尚未确认保存结果，不能确定是否已经写入。", { exact: true }).waitFor()
  check("restart_does_not_reuse_old_success", await frame.getByText("设置已保存并应用，对下一次咨询生效。", { exact: true }).count() === 0)
  check("restart_keeps_new_unsaved_secret_draft", await frame.locator('input[type="password"]').nth(1).inputValue() === "unsaved-draft-after-save")
  await frame.getByRole("button", { name: "核对这次保存状态", exact: true }).click()
  await frame.getByText("尚未确认保存结果，不能确定是否已经写入。", { exact: true }).waitFor()
  check("checking_unavailable_receipt_never_replays_save", result.savePosts === 3)
  await page.screenshot({ path: path.join(output, "backend-restarted.png"), fullPage: true })
  frame = await settings()
  check("fresh_iframe_after_restart_reads_persisted_config", await frame.getByRole("textbox", { name: /模型名称/ }).inputValue() === "receipt-acceptance-v2")
  check("fresh_iframe_does_not_invent_a_save_result", await frame.getByText("设置已保存并应用，对下一次咨询生效。", { exact: true }).count() === 0 && await frame.getByText("这次设置没有应用。请检查下面的提示后再试。", { exact: true }).count() === 0)
  check("no_repeated_write_or_consultation", result.savePosts === 3 && result.consultPosts === 0 && result.pageErrors.length === 0)
} catch (error) {
  result.error = { name: error.name, message: error.message }
  await page.screenshot({ path: path.join(output, "error.png"), fullPage: true }).catch(() => {})
  console.error(error.message)
  process.exitCode = 1
} finally {
  await browser.close()
  result.browserClosed = true
  await fs.writeFile(path.join(output, "ACCEPTANCE.json"), JSON.stringify(result, null, 2))
}
