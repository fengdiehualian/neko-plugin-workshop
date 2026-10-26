import assert from "node:assert/strict"
import { join } from "node:path"

export async function heatmapChecks(browser, sources, origin, output, { loadPage, inspectLayout }) {
  const checks = []
  const screenshots = []
  const cases = [
    ["screenshot", 1000, "light", "zh-CN"],
    ["screenshot", 1000, "dark", "zh-CN"],
    ["screenshot", 1000, "light", "zh-CN", "bright"],
    ["screenshot", 390, "dark", "en", "dark"],
    ["year", 1000, "light", "zh-CN"],
    ["year", 600, "dark", "en"],
    ["year", 390, "light", "zh-CN"],
    ["leap", 390, "dark", "ja"],
    ["short", 390, "light", "zh-CN"],
    ["boundary", 390, "light", "en"],
    ["uniform", 1000, "light", "zh-CN"],
    ["outlier", 1000, "light", "zh-CN"],
    ["empty", 390, "dark", "zh-CN"],
  ]
  for (const [heat, width, theme, locale, wallpaper = "none"] of cases) {
    const id = `${heat}-${width}-${theme}-${locale}-${wallpaper}`
    const loaded = await loadPage(browser, origin, { heat, locale, tab: "moment", wallpaper }, width, 900, theme)
    const { context, page, frame, errors } = loaded
    try {
      const expected = await page.evaluate(() => window.qaFixture.stats.heatmap)
      if (!expected.start) {
        assert.equal(await frame.locator(".tm-gh").count(), 0)
        assert.equal(await frame.locator(".tm-heat-card .neko-empty-state").count() > 0 ||
          (await frame.locator(".tm-heat-card").innerText()).includes("\u8fd8\u6ca1\u6709"), true)
      } else {
        const geometry = await frame.locator(".tm-gh").evaluate(grid => {
          const scroll = grid.closest(".tm-heat-scroll")
          const cells = [...grid.querySelectorAll("[data-date]")]
          const months = [...grid.querySelectorAll(".tm-gh-month")].map(node => {
            const r = node.getBoundingClientRect()
            return { text: node.textContent, left: r.left, right: r.right }
          })
          return {
            dates: cells.map(node => node.dataset.date),
            levels: cells.map(node => node.className.match(/tm-heat-lv(\d)/)[1]),
            sizes: cells.map(node => { const r = node.getBoundingClientRect(); return [r.width, r.height] }),
            colors: [...grid.querySelectorAll("[data-date]")].map(node => getComputedStyle(node).backgroundColor),
            gap: parseFloat(getComputedStyle(grid.querySelector(".tm-gh-cols")).columnGap),
            columns: grid.querySelectorAll(".tm-gh-col").length,
            scroll: { width: scroll.clientWidth, total: scroll.scrollWidth, mode: getComputedStyle(scroll).overflowX },
            monthLeft: grid.querySelector(".tm-gh-monthslot").getBoundingClientRect().left,
            cellLeft: grid.querySelector(".tm-gh-col").getBoundingClientRect().left,
            months,
          }
        })
        const days = (Date.parse(expected.end) - Date.parse(expected.start)) / 86400000 + 1
        assert.equal(geometry.dates.length, days, "Every window date must be rendered exactly once")
        assert.equal(new Set(geometry.dates).size, days)
        assert.equal(geometry.dates[0], expected.start, "Do not fill days before first interaction")
        assert.equal(geometry.dates.at(-1), expected.end)
        assert(geometry.sizes.every(([w, h]) => w >= 10.9 && w <= 15.1 && Math.abs(w - h) < 0.1))
        assert.equal(geometry.gap, 3)
        assert(Math.abs(geometry.monthLeft - geometry.cellLeft) < 0.5, "Month labels must align with week columns")
        for (let index = 1; index < geometry.months.length; index++) {
          assert(geometry.months[index - 1].right <= geometry.months[index].left + 0.5, "Month names must not overlap")
        }
        if (heat === "year" || heat === "leap") assert.equal(geometry.columns, 53)
        if ((heat === "year" || heat === "leap") && width <= 600) {
          assert(geometry.scroll.total > geometry.scroll.width)
          assert.equal(geometry.scroll.mode, "auto")
          await frame.locator(".tm-heat-scroll").evaluate(node => { node.scrollLeft = node.scrollWidth })
          const lastVisible = await frame.locator(`[data-date="${expected.end}"]`).evaluate(node => {
            const chart = node.closest(".tm-heat-scroll").getBoundingClientRect()
            const rect = node.getBoundingClientRect()
            return rect.left >= chart.left && rect.right <= chart.right + 1
          })
          assert(lastVisible, "December must remain reachable in narrow panels")
          await frame.locator(".tm-heat-scroll").evaluate(node => { node.scrollLeft = 0 })
        }
        const countsToLevels = new Map()
        for (let i = 0; i < expected.days.length; i++) {
          const n = expected.days[i].turns
          const level = geometry.levels[i]
          if (countsToLevels.has(n)) assert.equal(countsToLevels.get(n), level, "Equal counts must share a color")
          countsToLevels.set(n, level)
        }
        if (heat === "uniform") assert.equal(countsToLevels.size, 1)
        if (heat === "outlier" || heat === "screenshot") {
          assert.equal(new Set(geometry.levels).size, 5, "Typical counts must remain differentiated")
          assert.equal(new Set(geometry.colors).size, 5)
        }
        const firstCell = frame.locator(`[data-date="${expected.start}"]`)
        await firstCell.hover()
        assert((await frame.locator(".tm-tip").innerText()).includes(expected.start))
        const before = await firstCell.boundingBox()
        await frame.locator(".tm-heat-nav").hover()
        const after = await firstCell.boundingBox()
        assert(Math.abs(before.width - after.width) < 0.1, "Hover must not resize the date cell")
      }
      const layout = await inspectLayout(frame)
      assert.equal(layout.documentOverflow, 0)
      assert.equal(layout.contentOverflow, 0)
      assert.deepEqual(layout.issues, [])
      assert.deepEqual(errors, [])
      const diagnostics = await page.evaluate(() => window.qaDiagnostics.filter(item => item.type === "neko-hosted-surface-error"))
      assert.deepEqual(diagnostics, [])
      const path = join(output, `heatmap-${id}.png`)
      await page.screenshot({ path, animations: "disabled" })
      screenshots.push({ id, path, layout, errors: [...errors], diagnostics })
      if (heat === "year" && width === 1000) {
        await frame.locator(".tm-heat-nav button").first().click()
        await frame.waitForSelector('[data-date="2024-02-29"]')
        assert.equal(await frame.locator(".tm-gh [data-date]").count(), 366)
        await frame.locator(".tm-heat-nav button").last().click()
        await frame.waitForSelector('[data-date="2025-12-31"]')
        assert.equal(await frame.locator(".tm-gh [data-date]").count(), 365)
      }
      checks.push({ name: id, passed: true })
      console.log(`PASS heatmap-${id}`)
    } catch (error) {
      checks.push({ name: id, passed: false, error: error.stack })
      console.log(`FAIL heatmap-${id}: ${error.message}`)
    } finally { await context.close() }
  }
  return { checks, screenshots }
}
