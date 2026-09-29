import { describe, expect, test } from "bun:test"
import { loadPacks, renderProject, renderTemplate } from "../src/index"

describe("packs registry", () => {
  test("neko-plugin pack loads with 3 templates", async () => {
    const packs = await loadPacks()
    const neko = packs.find((p) => p.meta.id === "neko-plugin")
    expect(neko).toBeDefined()
    expect(neko!.templates.sort()).toEqual(["hello_world", "notes", "reminder"])
    expect((neko!.rules.x_hard_rules as string[]).length).toBe(12)
  })

  test("hello_world template renders placeholders", async () => {
    const files = await renderTemplate("neko-plugin", "hello_world", {
      PLUGIN_ID: "demo_cat",
      PLUGIN_NAME: "演示猫",
      CLASS_NAME: "DemoCat",
    })
    expect(files["plugin.toml"]).toContain('id = "demo_cat"')
    expect(files["plugin.toml"]).toContain('type = "plugin"')
    expect(files["plugin.toml"]).toContain("DemoCatPlugin")
    expect(files["pyproject.toml"]).toContain('name = "demo_cat"')
    expect(files["__init__.py"]).toContain("class DemoCatPlugin(NekoPluginBase)")
    expect(files["__init__.py"]).toContain('return Ok(')
  })

  test("all templates are async + Ok-returning (R3 spot check)", async () => {
    for (const t of ["hello_world", "reminder", "notes"]) {
      const files = await renderTemplate("neko-plugin", t, {
        PLUGIN_ID: "x",
        PLUGIN_NAME: "X",
        CLASS_NAME: "X",
      })
      const py = files["__init__.py"]
      expect(py).toContain("async def")
      expect(py).toContain("Ok(")
      expect(py).not.toMatch(/plugin\._types\.result|plugin\.sdk\.extension/)
      expect(files["tests/test_smoke.py"]).not.toContain("pytest.mark.asyncio")
    }
  })

  test("every generated project embeds anti-forgetting rules (AGENTS.md + SKILL.md)", async () => {
    const files = await renderProject("neko-plugin", "hello_world", {
      PLUGIN_ID: "demo",
      PLUGIN_NAME: "演示",
      CLASS_NAME: "Demo",
    })
    expect(files["AGENTS.md"]).toContain("自主闭环")
    expect(files["AGENTS.md"]).toContain("R1")
    expect(files[".opencode/skill/neko-plugin-dev/SKILL.md"]).toContain("plugin.toml")
    expect(files[".opencode/skill/neko-plugin-dev/SKILL.md"]).toContain("已移除 API")
  })
})
