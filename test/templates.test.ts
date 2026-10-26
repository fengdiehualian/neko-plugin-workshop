import { describe, expect, test } from "bun:test"
import { renderTemplate } from "../src/index.ts"

const TEMPLATES = ["hello_world", "notes", "reminder"]
const REQUIRED = [
  "plugin.toml",
  "__init__.py",
  "README.md",
  "config.example.toml",
  "pyproject.toml",
  "ruff.toml",
  ".github/workflows/verify.yml",
  ".github/workflows/release.yml",
]

describe("脚手架模板:必须覆盖官方发布前置文件", () => {
  for (const tpl of TEMPLATES) {
    test(`${tpl} 渲染出全部必需文件`, async () => {
      const files = await renderTemplate("neko-plugin", tpl, {
        PLUGIN_ID: "demo_plugin",
        PLUGIN_NAME: "演示",
        PLUGIN_AUTHOR: "tester",
        CLASS_NAME: "Demo",
      })
      for (const f of REQUIRED) expect(Object.keys(files)).toContain(f)
      expect(Object.keys(files).some((k) => k.startsWith("tests/test_"))).toBe(true)
    })

    test(`${tpl} 的工作流与配置正确替换了插件 id`, async () => {
      const files = await renderTemplate("neko-plugin", tpl, {
        PLUGIN_ID: "demo_plugin",
        PLUGIN_NAME: "演示",
        PLUGIN_AUTHOR: "tester",
        CLASS_NAME: "Demo",
      })
      expect(files["plugin.toml"]).toContain('id = "demo_plugin"')
      expect(files[".github/workflows/verify.yml"]).toContain("plugin-id: demo_plugin")
      expect(files[".github/workflows/release.yml"]).toContain("plugin-id: demo_plugin")
      expect(files[".github/workflows/verify.yml"]).toContain("managed-template: plugin-market-actions-v1")
      expect(files["ruff.toml"]).toContain('known-third-party = ["plugin"]')
      // 不该残留未替换的占位符
      for (const [name, text] of Object.entries(files)) expect(text).not.toContain("{{PLUGIN_ID}}")
    })
  }
})
