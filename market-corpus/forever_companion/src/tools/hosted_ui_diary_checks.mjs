import assert from "node:assert/strict"
import { join } from "node:path"

export async function diaryChecks(browser, sources, origin, output, { loadPage, inspectLayout }) {
  const checks = []
  const screenshots = []
  const check = async (name, operation) => {
    try {
      await operation()
      checks.push({ name, passed: true })
      console.log(`PASS ${name}`)
    } catch (error) {
      checks.push({ name, passed: false, error: error.stack })
      console.log(`FAIL ${name}: ${error.message}`)
    }
  }
  for (const locale of Object.keys(sources.messages)) {
    for (const width of [1280, 390]) {
      const loaded = await loadPage(browser, origin, { locale, tab: "diary", wallpaper: "none", diaryCase: "headings" }, width, 900, "light")
      const { page, frame, context, errors } = loaded
      const text = sources.messages[locale]
      const refresh = async () => {
        const payload = await page.evaluate(() => window.qaFixture.context)
        await frame.evaluate(next => window.__NekoRefreshHostedPayload(next), payload)
      }
      try {
        for (const kind of ["journal", "review"]) {
          await check(`${kind}-${locale}-${width}-fixed-date-above-title`, async () => {
            await frame.getByRole("button", { name: text[kind === "journal" ? "panel.journal.tabJournal" : "panel.review.tabReview"], exact: true }).click()
            const cards = frame.locator(kind === "journal" ? ".tmb-spine:not(.tmb-spine--file)" : ".tmb-spine--file")
            await cards.first().waitFor()
            const items = await page.evaluate(kind => window.qaFixture[kind], kind)
            const stored = JSON.stringify(items)
            const expected = ["卡片约定小记", text["panel.journal.untitled"], text["panel.journal.untitled"], "1999-12-31·跨年的约定", items[4].title, "一起等日落"]
            for (let index = 0; index < expected.length; index++) {
              const card = cards.nth(index)
              const recorded = items[index].started_at || items[index].ts
              assert.equal(await card.locator("time").count(), 1)
              assert.equal(await card.locator(".tmb-record-date").innerText(), recorded.slice(0, 10))
              assert.equal(await card.locator(".tmb-record-date").getAttribute("datetime"), recorded)
              assert.equal(await card.locator(".tmb-cover-title").innerText(), expected[index])
              assert(!/\d{4}-\d{2}-\d{2}/.test(await card.locator(".tmb-cover-bottom").innerText()))
              const geometry = await card.evaluate(element => {
                const date = element.querySelector(".tmb-record-date")
                const title = element.querySelector(".tmb-cover-title")
                const excerpt = element.querySelector(".tmb-cover-excerpt")
                return {
                  dateBottom: date.getBoundingClientRect().bottom, titleTop: title.getBoundingClientRect().top,
                  titleBottom: title.getBoundingClientRect().bottom, excerptTop: excerpt.getBoundingClientRect().top,
                  dateSize: parseFloat(getComputedStyle(date).fontSize), titleSize: parseFloat(getComputedStyle(title).fontSize),
                }
              })
              assert(geometry.dateBottom < geometry.titleTop && geometry.titleBottom < geometry.excerptTop, JSON.stringify(geometry))
              assert(geometry.titleSize > geometry.dateSize, JSON.stringify(geometry))
            }
            const id = `${kind}-headings-${locale}-${width}`
            const path = join(output, `${id}.png`)
            await page.screenshot({ path, animations: "disabled" })
            const layout = await inspectLayout(frame)
            assert.equal(layout.documentOverflow, 0)
            assert.equal(layout.contentOverflow, 0)
            assert.deepEqual(layout.issues, [])
            screenshots.push({ id, path, layout, errors: [...errors], diagnostics: [] })
            for (const index of [0, 1, 2, 3]) {
              await cards.nth(index).click()
              assert.equal(await frame.locator(".tmb-reading-title").innerText(), expected[index])
              assert.equal(await frame.locator(".tmb-current-title").innerText(), expected[index])
              await frame.locator(".tmb-foot .tmb-btn").first().click()
            }
            assert.equal(await page.evaluate(kind => JSON.stringify(window.qaFixture[kind]), kind), stored)
            assert.deepEqual(errors, [])
          })
          await check(`manual-${kind}-${locale}-${width}-queue-progress-and-specific-failure`, async () => {
            const action = kind === "journal" ? "invite_journal" : "write_review_now"
            const stateKey = kind === "journal" ? "journal_write" : "review_brief"
            await frame.getByRole("button", { name: text[kind === "journal" ? "panel.journal.tabJournal" : "panel.review.tabReview"], exact: true }).click()
            await page.evaluate(({ action, stateKey }) => {
              window.qaResults[action] = { accepted: true, queued: true }
              window.qaFixture.context.state[stateKey] = { writing: false, last_result: null }
            }, { action, stateKey })
            const label = text[kind === "journal" ? "panel.journal.invite" : "panel.review.writeNow"]
            await frame.getByRole("button", { name: label, exact: true }).click()
            await frame.waitForFunction(expected =>
              [...document.querySelectorAll(".neko-toast")].some(item => item.textContent.includes(expected)), text["panel.review.accepted"])
            await page.evaluate(stateKey => {
              window.qaFixture.context.state[stateKey] = { writing: true, last_result: null }
            }, stateKey)
            await refresh()
            await frame.getByRole("button", { name: text["panel.review.writing"], exact: true }).waitFor()
            assert(await frame.getByRole("button", { name: text["panel.review.writing"], exact: true }).isDisabled())
            const progressKey = kind === "journal" ? "panel.journal.writingHint" : "panel.review.writingHint"
            assert((await frame.locator(".tm-content").innerText()).includes(text[progressKey]))
            for (const [reason, key] of [
              ["request_failed", "panel.writing.requestFailed"],
              ["request_timeout", "panel.writing.requestTimeout"],
              ["invalid_reply", "panel.writing.invalidReply"],
              ["budget_exhausted", "panel.writing.budget"],
              ["persist_failed", "panel.review.persistFailed"],
            ]) {
              await page.evaluate(({ stateKey, reason }) => {
                window.qaFixture.context.state[stateKey] = { writing: false, last_result: { ts: Date.now(), written: false, reason } }
              }, { stateKey, reason })
              await refresh()
              await frame.waitForFunction(({ key, expected }) =>
                [...document.querySelectorAll(".tm-derived")].some(item => item.textContent.includes(expected)), { key, expected: text[key] })
              assert(await frame.getByRole("button", { name: label, exact: true }).isEnabled())
            }
            await page.evaluate(stateKey => {
              window.qaFixture.context.state[stateKey] = { writing: false, last_result: { ts: Date.now(), written: true, reason: "written" } }
            }, stateKey)
            await refresh()
            await frame.locator(".neko-toast").first().waitFor({ state: "detached", timeout: 6000 })
            const id = `manual-${kind}-${locale}-${width}`
            const path = join(output, `${id}.png`)
            await page.screenshot({ path, animations: "disabled" })
            const layout = await inspectLayout(frame)
            assert.equal(layout.documentOverflow, 0)
            assert.equal(layout.contentOverflow, 0)
            assert.deepEqual(layout.issues, [])
            screenshots.push({ id, path, layout, errors: [...errors], diagnostics: [] })
            assert.deepEqual(errors, [])
          })
        }
      } finally { await context.close() }
    }
  }
  for (const [width, theme, locale] of [[1000, "light", "zh-CN"], [1000, "dark", "en"], [390, "light", "zh-CN"], [390, "dark", "en"]]) {
    const loaded = await loadPage(browser, origin, { locale, tab: "diary", wallpaper: "none", diaryCase: "many" }, width, 900, theme)
    const { page, frame, context, errors } = loaded
    const label = key => sources.messages[locale][key]
    const id = `${width}-${theme}-${locale}`
    const capture = async name => {
      await frame.locator(".neko-toast").first().waitFor({ state: "detached", timeout: 6000 })
      const path = join(output, `${id}-${name}.png`)
      await page.screenshot({ path, animations: "disabled" })
      screenshots.push({ id: `${id}-${name}`, path, layout: await inspectLayout(frame), errors: [...errors], diagnostics: await page.evaluate(() => window.qaDiagnostics.filter(item => item.type === "neko-hosted-surface-error")) })
    }
    try {
      const stored = await page.evaluate(() => JSON.stringify([window.qaFixture.journal, window.qaFixture.review]))
      for (const kind of ["journal", "review"]) {
        const selector = kind === "journal" ? ".tmb-spine:not(.tmb-spine--file)" : ".tmb-spine--file"
        await frame.getByRole("button", { name: label(kind === "journal" ? "panel.journal.tabJournal" : "panel.review.tabReview"), exact: true }).click()
        await frame.waitForSelector(selector)
        const catalogLabel = number => label("panel.journal.catalogPage").replace("{n}", String(number)).replace("{total}", "3")
        const assertReviewIdentity = async entry => {
          const title = entry.title || label("panel.journal.untitled")
          const composedAt = entry.ts.replace("T", " ").slice(0, 16)
          assert.equal(await frame.locator(".tmb-reading-title").innerText(), title)
          assert.equal(await frame.locator(".tmb-current-title").innerText(), title)
          assert.equal(await frame.locator(".tmb-head-no").innerText(), composedAt)
          assert.equal(await frame.locator(".tmb-foot .tmb-ind").innerText(), composedAt)
          assert.equal(await frame.locator(".tmb-leaf-ind").count(), 0)
        }
        if (kind === "journal") await check(`${id}-journal-title-display-search-and-fallback`, async () => {
          const titles = await page.evaluate(() => window.qaFixture.journal.map(item => item.title || ""))
          assert.equal(await frame.locator(".tmb-cover-title").first().innerText(), titles[0])
          assert.equal(await frame.locator(".tmb-cover-title").nth(1).innerText(), label("panel.journal.untitled"))
          await frame.locator(".tmb-search").fill("onlyintitle")
          assert.equal(await frame.locator(selector).count(), 1)
          await frame.locator(selector).first().click()
          assert.equal(await frame.locator(".tmb-reading-title").innerText(), "OnlyInTitle")
          assert(!(await frame.locator(".tmb-page-body").innerText()).includes("OnlyInTitle"))
          await frame.locator(".tmb-foot .tmb-btn").first().click()
          await frame.locator(".tmb-search").fill("")
        })
        if (kind === "review") await check(`${id}-review-title-display-search-and-fallback`, async () => {
          const titles = await page.evaluate(() => window.qaFixture.review.map(item => item.title || ""))
          assert.equal(await frame.locator(".tmb-cover-meta").first().locator("time").count(), 1)
          assert.equal(await frame.locator(".tmb-cover-title").first().innerText(), titles[0])
          assert.equal(await frame.locator(".tmb-cover-title").nth(1).innerText(), label("panel.journal.untitled"))
          assert.equal(await frame.locator(".tmb-search").getAttribute("placeholder"), label("panel.review.search"))
          await frame.locator(".tmb-search").fill("reviewonlyintitle")
          assert.equal(await frame.locator(selector).count(), 1)
          await frame.locator(selector).first().click()
          assert.equal(await frame.locator(".tmb-reading-title").innerText(), "ReviewOnlyInTitle")
          assert(!(await frame.locator(".tmb-file-main").innerText()).includes("ReviewOnlyInTitle"))
          await frame.locator(".tmb-foot .tmb-btn").first().click()
          assert.equal(await frame.locator(".tmb-search").inputValue(), "reviewonlyintitle")
          await frame.locator(".tmb-search").fill("")
        })
        if (kind === "journal") await check(`${id}-journal-identity-and-continuation-boundaries`, async () => {
          assert.equal(await frame.locator(".tmb-cover-meta").first().locator("time").count(), 1)
          const diary = await page.evaluate(() => window.qaFixture.journal[0])
          const diaryLabel = label("panel.journal.diaryNo").replace("{n}", String(diary.page_no))
          assert.equal(diary.page_no, 53)
          await frame.locator(selector).first().click()
          assert.equal(await frame.locator(".tmb-head-no").innerText(), diaryLabel)
          assert.equal(await frame.locator(".tmb-foot .tmb-ind").innerText(), diaryLabel)
          assert.equal(await frame.locator(".tmb-current-title").innerText(), diary.title)
          const centers = await frame.locator(".tmb-foot-mid > *").evaluateAll(elements => elements.map(element => {
            const rect = element.getBoundingClientRect()
            return (rect.top + rect.bottom) / 2
          }))
          assert(Math.max(...centers) - Math.min(...centers) <= 1, `Diary navigation wrapped: ${JSON.stringify(centers)}`)
          assert.equal(await frame.locator(".tmb-leaf-ind").count(), 0)
          assert.deepEqual(await frame.locator(".tmb-entry-kind").allInnerTexts(), [label("panel.journal.firstWritten"), label("panel.journal.continued")])
          assert.deepEqual(await frame.locator(".tmb-entry-when").allInnerTexts(), diary.entries.map(item => item.ts.replace("T", " ").slice(0, 16)))
          assert((await frame.locator(".tmb-page-body").innerText()).includes("journal-1-continuation-tail"))
          await frame.getByRole("button", { name: label("panel.journal.nextDiary"), exact: true }).click()
          assert.equal(await frame.locator(".tmb-foot .tmb-ind").innerText(), label("panel.journal.diaryNo").replace("{n}", "54"))
          assert.deepEqual(await frame.locator(".tmb-entry-kind").allInnerTexts(), [label("panel.journal.firstWritten")])
          assert(!(await frame.locator(".tmb-page-body").innerText()).includes("journal-1-continuation-tail"))
          assert.equal((await frame.locator(".tmb-current-title").innerText()).replace(/\s+/g, " "), (await frame.locator(".tmb-reading-title").innerText()).replace(/\s+/g, " "))
          await frame.getByRole("button", { name: label("panel.journal.prevDiary"), exact: true }).click()
          assert.equal(await frame.locator(".tmb-foot .tmb-ind").innerText(), diaryLabel)
          await frame.locator(".tmb-foot .tmb-btn").first().click()
        })
        await check(`${id}-${kind}-catalog-pagination`, async () => {
          assert.equal(await frame.locator(selector).count(), 8)
          assert.equal(await frame.locator(".tmb-catalog-nav .tmb-ind").innerText(), catalogLabel(1))
          assert(await frame.locator(".tmb-cover-excerpt").first().innerText())
          assert(!/【/.test(await frame.locator(".tmb-cover-excerpt").first().innerText()))
          await capture(`${kind}-catalog`)
          await frame.locator(".tmb-catalog-btn").last().click()
          assert.equal(await frame.locator(".tmb-catalog-nav .tmb-ind").innerText(), catalogLabel(2))
          await frame.locator(".tmb-catalog-btn").last().click()
          assert.equal(await frame.locator(selector).count(), kind === "journal" ? 5 : 1)
          assert.equal(await frame.locator(".tmb-catalog-btn").last().isDisabled(), true)
        })
        await check(`${id}-${kind}-search-full-text-and-return`, async () => {
          await frame.locator(".tmb-search").fill(`${kind}-11-tail`)
          assert.equal(await frame.locator(selector).count(), 1)
          await frame.locator(selector).first().click()
          const content = await frame.locator(kind === "journal" ? ".tmb-page-body" : ".tmb-file-main").innerText()
          assert(content.includes(`${kind}-11-tail`))
          await frame.locator(".tmb-foot .tmb-btn").first().click()
          assert.equal(await frame.locator(".tmb-search").inputValue(), `${kind}-11-tail`)
          assert.equal(await frame.locator(selector).count(), 1)
          await frame.locator(".tmb-search").fill("no-matching-diary")
          assert.equal(await frame.locator(selector).count(), 0)
          assert.equal(await frame.locator(".tmb-no-matches").innerText(), label("panel.journal.noMatches"))
          assert.equal(await frame.locator(".tmb-stack").isVisible(), true)
          await frame.locator(".tmb-search").fill("")
          assert.equal(await frame.locator(selector).count(), 8)
        })
        await check(`${id}-${kind}-reader-navigation-and-original-content`, async () => {
          await frame.locator(selector).first().click()
          const body = frame.locator(kind === "journal" ? ".tmb-page-body" : ".tmb-file-main")
          assert((await body.innerText()).includes(`${kind}-1-tail`))
          if (kind === "journal") {
            const title = await page.evaluate(() => window.qaFixture.journal[0].title)
            assert.equal(await frame.locator(".tmb-reading-title").innerText(), title)
          }
          if (kind === "review") {
            await assertReviewIdentity(await page.evaluate(() => window.qaFixture.review[0]))
            assert.equal(await frame.locator(".tmb-reference").getAttribute("open"), null)
            assert.equal(await frame.locator(".tmb-evidence").isVisible(), false)
            assert(!(await frame.locator(".tmb-reading-meta").innerText()).includes(label("panel.review.fieldTurns")))
          }
          assert.equal(await frame.locator(".tmb-foot .tmb-btn").nth(1).isDisabled(), true)
          await frame.locator(".tm-content").evaluate(element => { element.scrollTop = 180 })
          await frame.locator(".tmb-foot .tmb-btn").last().click()
          assert((await body.innerText()).includes(`${kind}-2-tail`))
          if (kind === "journal") assert.equal(await frame.locator(".tmb-reading-title").innerText(), label("panel.journal.untitled"))
          if (kind === "review") await assertReviewIdentity(await page.evaluate(() => window.qaFixture.review[1]))
          assert.equal(await frame.locator(".tm-content").evaluate(element => element.scrollTop), 0)
          await frame.locator(".tmb-foot .tmb-btn").nth(1).click()
          assert((await body.innerText()).includes(`${kind}-1-tail`))
          if (kind === "review") {
            await assertReviewIdentity(await page.evaluate(() => window.qaFixture.review[0]))
            assert.equal(await frame.locator(".tmb-reference").getAttribute("open"), null)
            await frame.locator(".tmb-reference summary").click()
            assert.equal(await frame.locator(".tmb-quote").count(), 1)
            assert((await frame.locator(".tmb-evidence").innerText()).includes("16"))
            const turns = frame.locator(".tmb-evidence-line").filter({ hasText: label("panel.review.fieldTurns") })
            assert.equal(await turns.locator(".tmb-evidence-value").innerText(), String(await page.evaluate(() => window.qaFixture.review[0].turns)))
          }
          await frame.locator(".tm-content").evaluate(element => { element.scrollTop = 0 })
          await capture(`${kind}-reader`)
          await frame.locator(".tmb-foot .tmb-btn").first().click()
        })
        await check(`${id}-${kind}-archive-failure-retry-and-navigation`, async () => {
          await page.evaluate(action => { window.qaFailNext = action }, kind === "journal" ? "get_journal" : "get_review")
          await frame.locator(".tmb-stack").click()
          await frame.waitForTimeout(150)
          assert((await frame.locator(".neko-toast").innerText()).includes(label(kind === "journal" ? "panel.journal.archiveLoadError" : "panel.review.archiveLoadError")))
          assert.equal(await frame.locator(".tmb-page").count(), 0)
          assert.equal(await frame.locator(".tmb-stack").isEnabled(), true)
          await frame.locator(".tmb-stack").click()
          await frame.waitForSelector(".tmb-archive-badge")
          if (kind === "journal") {
            const title = await page.evaluate(() => window.qaFixture.journalArchive[1].title)
            assert.equal(await frame.locator(".tmb-reading-title").innerText(), title)
          }
          if (kind === "review") await assertReviewIdentity(await page.evaluate(() => window.qaFixture.reviewArchive[0]))
          else assert.equal(await frame.locator(".tmb-foot .tmb-ind").innerText(), label("panel.journal.diaryNo").replace("{n}", "2"))
          assert.equal(await frame.locator(".tmb-foot .tmb-btn").nth(1).isDisabled(), true)
          await frame.locator(".tmb-foot .tmb-btn").last().click()
          if (kind === "review") await assertReviewIdentity(await page.evaluate(() => window.qaFixture.reviewArchive[1]))
          else assert.equal(await frame.locator(".tmb-foot .tmb-ind").innerText(), label("panel.journal.diaryNo").replace("{n}", "1"))
          assert.equal(await frame.locator(".tmb-foot .tmb-btn").last().isDisabled(), true)
          await frame.locator(".tm-content").evaluate(element => { element.scrollTop = 0 })
          await capture(`${kind}-archive`)
          await frame.locator(".tmb-foot .tmb-btn").first().click()
        })
      }
      await check(`${id}-display-never-modifies-stored-diaries`, async () => {
        assert.equal(await page.evaluate(() => JSON.stringify([window.qaFixture.journal, window.qaFixture.review])), stored)
        assert.deepEqual(errors, [])
      })
    } finally { await context.close() }
  }
  for (const diaryCase of ["archives-only", "legacy", "empty"]) {
    const loaded = await loadPage(browser, origin, { locale: "en", tab: "diary", wallpaper: "none", diaryCase, empty: diaryCase === "empty" ? "true" : "false" }, 390, 900, "light")
    try {
      await check(`${diaryCase}-old-fields-and-empty-states`, async () => {
        for (const kind of ["journal", "review"]) {
          await loaded.frame.getByRole("button", { name: kind === "journal" ? "Personal Journal" : "My Diary", exact: true }).click()
          if (diaryCase === "legacy") {
            if (kind === "review") {
              await loaded.frame.locator(".tmb-spine--file").first().click()
              assert.equal(await loaded.frame.locator(".tmb-reference").count(), 0)
              assert(!/undefined|NaN/.test(await loaded.frame.locator(".tmb-page").innerText()))
              assert(!(await loaded.frame.locator(".tmb-reading-meta").innerText()).includes("0 turns"))
            }
          } else {
            assert.equal(await loaded.frame.locator(".tmb-spine").count(), 0)
            assert.equal(await loaded.frame.locator(".neko-empty").count(), 1)
            if (kind === "review") assert.equal(await loaded.frame.locator(".tmb-meter").count(), 1)
            if (diaryCase === "archives-only") {
              await loaded.frame.locator(".tmb-stack").click()
              await loaded.frame.waitForSelector(".tmb-archive-badge")
              await loaded.frame.locator(".tmb-foot .tmb-btn").first().click()
            } else assert.equal(await loaded.frame.locator(".tmb-stack").count(), 0)
          }
        }
        assert.deepEqual(loaded.errors, [])
      })
    } finally { await loaded.context.close() }
  }
  const continued = await loadPage(browser, origin, { locale: "en", tab: "diary", wallpaper: "none", diaryCase: "many" }, 390, 600, "light")
  try {
    await check("journal-live-continuation-preserves-identity-and-reading-position", async () => {
      const { frame, page } = continued
      await frame.getByRole("button", { name: "Personal Journal", exact: true }).click()
      await frame.locator(".tmb-spine").first().click()
      await frame.locator(".tm-content").evaluate(element => { element.scrollTop = 160 })
      const scrollBefore = await frame.locator(".tm-content").evaluate(element => element.scrollTop)
      const payload = await page.evaluate(() => {
        const diary = window.qaFixture.journal[0]
        diary.entries.push({ ts: "2026-10-13T10:30:00+08:00", text: "\u3010\u60f3\u8bf4\u7684\u3011live-continuation" })
        diary.entry_count = diary.entries.length
        diary.last_ts = diary.entries.at(-1).ts
        window.qaFixture.context.state.journal_index = window.qaFixture.journal.map(({ entries, ...header }) => header)
        return window.qaFixture.context
      })
      const stored = await page.evaluate(() => JSON.stringify(window.qaFixture.journal))
      await frame.evaluate(next => window.__NekoRefreshHostedPayload(next), payload)
      await frame.locator(".tmb-page-body").getByText("live-continuation", { exact: true }).waitFor()
      assert.equal(await frame.locator(".tmb-foot .tmb-ind").innerText(), "Diary #53")
      assert.equal(await frame.locator(".tmb-current-title").innerText(), await frame.locator(".tmb-reading-title").innerText())
      assert.deepEqual(await frame.locator(".tmb-entry-kind").allInnerTexts(), ["First entry", "Continuation", "Continuation"])
      assert.equal(await frame.locator(".tm-content").evaluate(element => element.scrollTop), scrollBefore)
      await frame.locator(".tmb-foot .tmb-btn").first().click()
      assert.equal(await frame.locator(".tmb-record-date").first().innerText(), "2026-09-01")
      assert.equal(await page.evaluate(() => JSON.stringify(window.qaFixture.journal)), stored)
      assert.deepEqual(continued.errors, [])
    })
  } finally { await continued.context.close() }
  const reviewRefreshed = await loadPage(browser, origin, { locale: "en", tab: "diary", wallpaper: "none", diaryCase: "many" }, 390, 600, "light")
  try {
    await check("review-new-title-refreshes-when-shelf-count-is-unchanged", async () => {
      const { frame, page } = reviewRefreshed
      await frame.getByRole("button", { name: "My Diary", exact: true }).click()
      await frame.waitForSelector(".tmb-spine--file")
      const count = await page.evaluate(() => window.qaFixture.review.length)
      const payload = await page.evaluate(() => {
        const fixture = window.qaFixture
        const entry = { ...fixture.review[0], ts: "2026-10-13T10:30:00+08:00", title: "ReviewFreshTitle", text: "new-composed-review-body" }
        fixture.review.unshift(entry)
        fixture.review.pop()
        fixture.context.state.review_brief.last_ts = entry.ts
        return fixture.context
      })
      const stored = await page.evaluate(() => JSON.stringify(window.qaFixture.review))
      await frame.evaluate(next => window.__NekoRefreshHostedPayload(next), payload)
      await frame.locator(".tmb-cover-title").getByText("ReviewFreshTitle", { exact: true }).waitFor()
      assert.equal(await page.evaluate(() => window.qaFixture.review.length), count)
      await frame.locator(".tmb-spine--file").first().click()
      assert.equal(await frame.locator(".tmb-reading-title").innerText(), "ReviewFreshTitle")
      assert.equal(await frame.locator(".tmb-file-main").innerText(), "new-composed-review-body")
      assert.equal(await page.evaluate(() => JSON.stringify(window.qaFixture.review)), stored)
      assert.deepEqual(reviewRefreshed.errors, [])
    })
  } finally { await reviewRefreshed.context.close() }
  return { checks, screenshots }
}
