// Offline preview with the actual host renderer and bundled images. No live host API.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");

const root = path.resolve(__dirname, "..");
const arg = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index < 0 ? fallback : process.argv[index + 1];
};
const host = path.resolve(arg("--host-root", path.join(root, "../N.E.K.O")));
const output = path.resolve(arg("--out", path.join(root, ".tmpgate/ui-preview")));
const modules = path.resolve(arg("--node-modules", path.join(host, "frontend/plugin-manager/node_modules")));
const tsModules = path.resolve(arg("--typescript-modules", modules));
const python = arg("--python", path.join(root, ".venv/Scripts/python.exe"));
const ts = createRequire(path.join(tsModules, "__visual__.cjs"))("typescript");
const { chromium } = createRequire(path.join(modules, "__visual__.cjs"))("playwright");
const kit = path.join(host, "frontend/plugin-manager/src/components/plugin/hosted/ui-kit");
const fixture = JSON.parse(execFileSync(python, ["-c", `
import base64, importlib.util, io, json
from pathlib import Path
from PIL import Image
spec = importlib.util.spec_from_file_location("bundled", "services/bundled_pack.py")
bundled = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundled)
rows, images, groups = [], {}, []
with bundled.open_pack(Path("official/official_pack.zip")) as archive:
    manifest = json.loads(archive.read("manifest.json"))
    for group in manifest["groups"]:
        selected = [row for row in manifest["stickers"] if row["group"] == group["name"]][:8]
        if group["name"] not in ["开心与庆祝", "比心与撒娇", "打招呼与冒泡"]:
            continue
        groups.append({**group, "zone": "z1", "count": len(selected)})
        for row in selected:
            image = Image.open(io.BytesIO(archive.read("stickers/" + row["file"]))).convert("RGBA")
            image.thumbnail((256, 256))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            sticker_id = row["asset_id"]
            images[sticker_id] = base64.b64encode(buffer.getvalue()).decode("ascii")
            rows.append({**row, "id": sticker_id, "zone": "z1"})
print(json.dumps({"images": images, "state": {
    "enabled": True, "lanlan": "YUI", "eagerness": "natural", "active_zone": "z1",
    "zones": [{"id": "z1", "name": "官方收藏", "active": True, "builtin": True, "total": len(rows)},
              {"id": "z2", "name": "我的收藏", "total": 0}],
    "stickers": rows, "groups": groups,
    "counts": {"total": len(rows), "enabled": len(rows), "groups": len(groups), "sent_total": 12},
    "config": {"cooldown_sec": 20, "eager_attachment_turns": 1},
    "awareness": {"status": "waiting", "target": "YUI", "min_interval_sec": 60,
                  "inject_interval_n": 6, "turns_since": 2, "driver": "bus"},
    "run": {"turns": 29, "tool_calls": 8, "sent": 7, "refused": 0, "surface_categories": 3}
}}, ensure_ascii=False))
`], { cwd: root, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONIOENCODING: "utf-8" },
  encoding: "utf8", maxBuffer: 16 * 1024 * 1024 }));

const compiled = {};
function collect(relative) {
  const filename = path.resolve(root, relative);
  const key = path.relative(root, filename).replaceAll("\\", "/");
  if (compiled[key]) return;
  const source = fs.readFileSync(filename, "utf8");
  compiled[key] = ts.transpileModule(source, {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.React, jsxFactory: "h", jsxFragmentFactory: "Fragment" },
  }).outputText;
  for (const match of source.matchAll(/from\s+["'](\.[^"']+)["']/g)) {
    const base = path.resolve(path.dirname(filename), match[1]);
    collect(fs.existsSync(`${base}.tsx`) ? `${base}.tsx` : `${base}.ts`);
  }
}
collect("ui/panel.tsx");
const factories = Object.entries(compiled).map(([key, value]) =>
  `${JSON.stringify(key)}: function(require,module,exports) {${value}\n}`,
).join(",\n");
const locales = Object.fromEntries(["zh-CN", "en"].map((locale) => [
  locale, JSON.parse(fs.readFileSync(path.join(root, `i18n/${locale}.json`), "utf8")),
]));
const safeJson = (value) => JSON.stringify(value).replaceAll("<", "\\u003c");
const html = `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sticker Manager - Offline UI Preview</title>
<style>${fs.readFileSync(path.join(kit, "styles.css"), "utf8")}</style></head><body>
<main id="root"></main><script>
window.__NEKO_PAYLOAD = {locale:"zh-CN",i18n:{locale:"zh-CN",default_locale:"zh-CN",messages:${safeJson(locales)}}};
${fs.readFileSync(path.join(kit, "runtime.js"), "utf8")}
const fixture = ${safeJson(fixture)};
const factories = {${factories}};
const cache = {};
function load(name) {
  if (cache[name]) return cache[name].exports;
  const module = cache[name] = {exports:{}};
  factories[name](function(specifier) {
    if (specifier === "@neko/plugin-ui") return window.NekoUiKit;
    const parts = name.split("/").slice(0,-1);
    specifier.split("/").forEach(part => part === ".." ? parts.pop() : part !== "." && parts.push(part));
    const base = parts.join("/");
    return load(factories[base+".tsx"] ? base+".tsx" : base+".ts");
  },module,module.exports);
  return module.exports;
}
window.previewCalls = [];
const surface = {
  state: fixture.state, t: window.NekoUiKit.t,
  api: {
    async call(action,args) {
      window.previewCalls.push({action,args});
      if (action === "preview") return {result:{mime:"image/png",
        chunk_base64:fixture.images[args.id],done:true,next_offset:0}};
      if (action === "set_send_cooldown") surface.state.config.cooldown_sec = args.cooldown_sec;
      if (action === "set_eager_attachment_turns") surface.state.config.eager_attachment_turns = args.turns;
      if (action === "set_reminder_interval") surface.state.awareness.min_interval_sec = args.min_interval_sec;
      if (action === "set_eagerness") surface.state.eagerness = args.eagerness;
      if (action === "switch") surface.state.enabled = args.enabled;
      if (action === "update") Object.assign(surface.state.stickers.find(row => row.id === args.id), args);
      return {result: action === "set_send_cooldown" ? {cooldown_sec:args.cooldown_sec}
        : action === "set_eager_attachment_turns" ? {turns:args.turns} : {}};
    },
    async refresh() { draw(); }
  }
};
const Panel = load("ui/panel.tsx").default;
function draw() { window.NekoUiKit.render(window.NekoUiKit.h(Panel,surface),document.getElementById("root")); }
window.previewLocale = function(locale) {
  __NEKO_PAYLOAD.locale = locale;
  __NEKO_PAYLOAD.i18n.locale = locale;
  draw();
};
window.previewAttachment = function(status) {
  surface.state.eager_attachment = {status, attempts:3, sent:1};
  draw();
};
draw();
</script></body></html>`;
fs.mkdirSync(output, { recursive: true });
const previewFile = path.join(output, "index.html");
fs.writeFileSync(previewFile, html);

async function layout(page) {
  const result = await page.evaluate(() => {
    const visible = (element) => element.getClientRects().length > 0;
    const outside = [...document.querySelectorAll("button,summary,input,select,img,h2")]
      .filter(visible).filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.left < -1 || rect.right > innerWidth + 1;
      }).map((element) => element.outerHTML.slice(0, 200));
    return { width: innerWidth, scroll: document.documentElement.scrollWidth, outside };
  });
  assert.ok(result.scroll <= result.width + 1, JSON.stringify(result));
  assert.deepEqual(result.outside, []);
}

async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];
  try {
    for (const [name, width, height, scheme] of [
      ["desktop", 1100, 900, "light"], ["mobile", 390, 844, "light"],
      ["narrow", 320, 740, "light"], ["dark", 900, 900, "dark"],
    ]) {
      const page = await browser.newPage({ viewport: { width, height }, colorScheme: scheme });
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(pathToFileURL(previewFile).href);
      await page.locator(".sticker-grid img").first().waitFor();
      for (const tile of await page.locator(".sticker-tile").all()) {
        await tile.scrollIntoViewIfNeeded();
        await tile.locator("img").waitFor();
        await tile.locator("img").evaluate((image) => image.decode());
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      await layout(page);
      assert.ok(await page.locator(".sticker-grid img").first().evaluate((image) =>
        image.complete && image.naturalWidth > 0));
      await page.screenshot({ path: path.join(output, `${name}-library.png`), fullPage: true });
      const filter = page.locator(".sticker-search select");
      const secondGroup = await filter.locator("option").nth(2).getAttribute("value");
      await page.locator('.sticker-tile input[type="checkbox"]').first().check();
      const batch = page.locator(".sticker-batch");
      await batch.locator("summary").click();
      assert.equal(await batch.getByRole("button", { name: "加标签", exact: true }).isDisabled(), true);
      assert.equal(await batch.getByRole("button", { name: "删标签", exact: true }).isDisabled(), true);
      await batch.locator("input").fill("，、");
      assert.equal(await batch.getByRole("button", { name: "删标签", exact: true }).isDisabled(), true);
      await batch.locator("input").fill("温柔");
      assert.equal(await batch.getByRole("button", { name: "删标签", exact: true }).isEnabled(), true);
      await layout(page);
      await filter.selectOption(secondGroup);
      await page.getByRole("button", { name: "取消隐藏选择", exact: true }).waitFor();
      await page.getByRole("button", { name: "取消隐藏选择", exact: true }).click();
      await filter.selectOption("all");
      await page.getByRole("button", { name: "图库管理", exact: true }).click();
      await page.getByRole("button", { name: "新建分类", exact: true }).waitFor();
      await layout(page);
      await page.getByRole("button", { name: "新建分类", exact: true }).click();
      await page.getByRole("button", { name: "创建分类", exact: true }).waitFor();
      await page.getByRole("button", { name: "取消", exact: true }).click();
      await page.getByRole("tab", { name: "设置", exact: true }).click();
      await layout(page);
      assert.equal(await page.locator(".sticker-advanced[open]").count(), 0);
      await page.screenshot({ path: path.join(output, `${name}-settings.png`), fullPage: true });
      await page.getByRole("tab", { name: "爱发", exact: true }).click();
      assert.equal(await page.evaluate(() => window.previewCalls.filter(call =>
        call.action === "set_eagerness" && call.args.eagerness === "eager").length), 1);
      const selectionSlider = page.locator('input[type="range"][max="10"]');
      await selectionSlider.fill("0");
      await page.getByText("每轮帮选", { exact: true }).waitFor();
      await page.getByRole("button", { name: "保存补图轮数", exact: true }).click();
      await page.getByText("补图轮数已保存", { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.previewCalls.filter(call =>
        call.action === "set_eager_attachment_turns" && call.args.turns === 0).length), 1);
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-eager-every-reply.png`), fullPage: true });
      await selectionSlider.fill("10");
      await page.getByText("连续 10 轮未配图后帮选", { exact: true }).waitFor();
      await page.getByRole("button", { name: "保存补图轮数", exact: true }).click();
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-eager-ten-replies.png`), fullPage: true });
      await page.evaluate(() => window.previewAttachment("model_unbound"));
      await page.getByText("请在插件模型配置中为“爱发补图选图”绑定文字模型。", { exact: true }).waitFor();
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-eager-unbound.png`), fullPage: true });
      await page.evaluate(() => window.previewAttachment("selecting"));
      await page.getByText("正在为本轮回复选图…", { exact: true }).waitFor();
      await layout(page);
      await page.locator('input[type="number"]').fill("0");
      await page.getByRole("button", { name: "保存发送冷却", exact: true }).click();
      await page.getByText("关闭发送冷却", { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.previewCalls.filter(call =>
        call.action === "set_send_cooldown" && call.args.cooldown_sec === 0).length), 1);
      await page.locator(".sticker-advanced > summary").first().click();
      await page.locator(".sticker-advanced > summary").last().click();
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-advanced.png`), fullPage: true });
      await page.getByRole("tab", { name: "图库", exact: true }).click();
      await page.locator(".sticker-tile button").first().click();
      await page.getByRole("button", { name: "返回图库", exact: true }).waitFor();
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-detail.png`), fullPage: true });
      await page.getByRole("button", { name: "返回图库", exact: true }).click();
      if (name === "mobile") {
        await page.evaluate(() => window.previewLocale("en"));
        await layout(page);
        await page.getByRole("tab", { name: "Settings", exact: true }).click();
        await layout(page);
        await page.screenshot({ path: path.join(output, "mobile-settings-en.png"), fullPage: true });
      }
      await page.close();
    }
    assert.deepEqual(errors, []);
    console.log(`Visual and browser checks passed. Preview: ${previewFile}`);
  } finally {
    await browser.close();
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
