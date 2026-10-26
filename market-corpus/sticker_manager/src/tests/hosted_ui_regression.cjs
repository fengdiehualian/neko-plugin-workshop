// Run with: node tests/hosted_ui_regression.cjs --node-modules <dev node_modules>
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const moduleFlag = process.argv.indexOf("--node-modules");
const moduleRoot = moduleFlag >= 0 ? process.argv[moduleFlag + 1] : null;
const devRequire = moduleRoot
  ? createRequire(path.join(path.resolve(moduleRoot), "__hosted_ui_tests__.cjs"))
  : require;
const ts = devRequire("typescript");

function environment(options = {}) {
  const frames = new Map();
  const scrollCalls = [];
  let frameId = 0;
  let activeRunner = null;
  const scroll = { scrollLeft: 0, scrollTop: 0 };
  const document = {
    scrollingElement: scroll,
    documentElement: scroll,
    getElementById: (id) => ({
      scrollIntoView: (settings) => scrollCalls.push({ anchor: id, settings }),
      focus: (settings) => scrollCalls.push({ focus: id, settings }),
      querySelector: () => ({
        focus: (settings) => scrollCalls.push({ focus: `${id}/button`, settings }),
      }),
    }),
  };
  const window = {
    innerWidth: options.width || 1280,
    scrollX: 0,
    scrollY: 0,
    scrollTo(x, y) {
      this.scrollX = scroll.scrollLeft = x;
      this.scrollY = scroll.scrollTop = y;
      scrollCalls.push({ x, y });
    },
    requestAnimationFrame(callback) {
      const id = ++frameId;
      frames.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) {
      frames.delete(id);
    },
  };
  const kit = {
    useState(initial) {
      const runner = activeRunner;
      const index = runner.cursor++;
      if (!runner.hooks[index]) {
        runner.hooks[index] = { value: typeof initial === "function" ? initial() : initial };
      }
      const hook = runner.hooks[index];
      return [
        hook.value,
        (next) => {
          const value = typeof next === "function" ? next(hook.value) : next;
          if (!Object.is(value, hook.value)) {
            hook.value = value;
            runner.dirty = true;
          }
          return value;
        },
      ];
    },
    useRef(initial) {
      return kit.useState(() => ({ current: initial }))[0];
    },
    useEffect(effect, dependencies) {
      const runner = activeRunner;
      const index = runner.cursor++;
      const previous = runner.hooks[index];
      const changed = !previous || !dependencies ||
        dependencies.some((value, slot) => !Object.is(value, previous.dependencies[slot]));
      if (!changed) return;
      const hook = { dependencies, cleanup: previous?.cleanup };
      runner.hooks[index] = hook;
      runner.effects.push(() => {
        if (hook.cleanup) hook.cleanup();
        hook.cleanup = effect();
      });
    },
    useConfirm: () => options.confirm || (async () => true),
    useElementSize: () => ({ width: options.width || 1280, height: 900 }),
  };
  for (const name of [
    "Accordion", "Alert", "Button", "Card", "Checkbox", "Divider", "EmptyState",
    "Field", "Inline", "Input", "KeyValue", "Page", "Progress", "Section",
    "SegmentedControl", "Select", "Slider", "Stack", "StatusBadge", "Switch",
    "Tabs", "Text", "Textarea",
  ]) {
    kit[name] = function UiComponent() {};
    Object.defineProperty(kit[name], "name", { value: name });
  }
  const h = (type, props, ...children) => ({
    type,
    props: props || {},
    children: children.flat(Infinity).filter((value) => value !== null && value !== false),
  });
  const preview = {
    useStickerPreview: () => ({
      preview: "data:image/png;base64,cHJldmlldw==",
      loading: false,
      boxRef: { current: null },
      retry: () => {},
      onError: () => {},
    }),
  };
  const sandbox = {
    console: { ...console, warn: () => {} },
    Error,
    window,
    document,
    setInterval,
    clearInterval,
    FileReader: class {
      readAsDataURL() {
        this.result = "data:application/octet-stream;base64,cHJldmlldw==";
        this.onload();
      }
    },
    h,
    Fragment: "fragment",
  };
  const context = vm.createContext(sandbox);
  const modules = new Map();
  const load = (relative) => {
    let filename = path.resolve(root, relative);
    if (!path.extname(filename)) {
      filename = fs.existsSync(`${filename}.tsx`) ? `${filename}.tsx` : `${filename}.ts`;
    }
    if (modules.has(filename)) return modules.get(filename).exports;
    const module = { exports: {} };
    modules.set(filename, module);
    const source = fs.readFileSync(filename, "utf8");
    const compiled = ts.transpileModule(source, {
      fileName: filename,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
        jsx: ts.JsxEmit.React,
        jsxFactory: "h",
        jsxFragmentFactory: "Fragment",
      },
    }).outputText;
    const requireModule = (specifier) => {
      if (specifier === "@neko/plugin-ui") return kit;
      const resolved = path.resolve(path.dirname(filename), specifier);
      if (resolved === path.resolve(root, "ui/preview") && !options.realPreview) return preview;
      return load(resolved);
    };
    vm.runInContext(`(function(require, module, exports) { ${compiled}\n})`, context, {
      filename,
    })(requireModule, module, module.exports);
    return module.exports;
  };
  return {
    kit,
    load,
    window,
    scrollCalls,
    sandbox,
    runner(render) {
      const runner = { hooks: [], cursor: 0, effects: [], dirty: false, value: null };
      runner.render = () => {
        for (let attempt = 0; attempt < 20; attempt++) {
          activeRunner = runner;
          runner.cursor = 0;
          runner.dirty = false;
          runner.value = render();
          activeRunner = null;
          for (const effect of runner.effects.splice(0)) effect();
          if (!runner.dirty) return runner.value;
        }
        throw new Error("Hook mock did not settle");
      };
      runner.unmount = () => {
        for (const hook of runner.hooks) if (hook?.cleanup) hook.cleanup();
      };
      return runner;
    },
    flushFrames() {
      for (let guard = 0; frames.size && guard < 10; guard++) {
        const callbacks = [...frames.values()];
        frames.clear();
        callbacks.forEach((callback) => callback());
      }
      assert.equal(frames.size, 0);
    },
  };
}

function walk(node, predicate) {
  if (!node || typeof node !== "object") return [];
  const found = predicate(node) ? [node] : [];
  return found.concat((node.children || []).flatMap((child) => walk(child, predicate)));
}

function text(node) {
  if (typeof node === "string") return node;
  return node && typeof node === "object" ? (node.children || []).map(text).join("") : "";
}

function button(node, label) {
  const result = walk(node, (item) =>
    (item.type === "button" || item.type.name === "Button") && text(item) === label,
  )[0];
  assert.ok(result, `Missing button: ${label}`);
  return result;
}

function surface(call = async () => ({ result: {} })) {
  return {
    state: {
      active_zone: "z1",
      zones: [{ id: "z1", name: "One" }, { id: "z2", name: "Two" }],
      stickers: [
        { id: "s1", zone: "z1", group: "g1", desc: "Original", tags: ["tag"] },
        { id: "s2", zone: "z2", group: "g2", desc: "Other" },
      ],
      groups: [
        { name: "g1", zone: "z1", count: 1 },
        { name: "g2", zone: "z2", count: 1 },
      ],
    },
    api: { call, refresh: async () => {} },
    t: (_key, params = {}) => (params.defaultValue || _key).replace(
      /\{([^}]+)\}/g, (match, key) => params[key] === undefined ? match : String(params[key]),
    ),
  };
}

async function settle() {
  for (let step = 0; step < 20; step++) await Promise.resolve();
}

function largeSurface(call, count = 201) {
  const props = surface(call);
  props.state.stickers = Array.from({ length: count }, (_, index) => ({
    id: `item-${index}`,
    zone: "z1",
    group: "g1",
    desc: `Item ${index}`,
    tags: [],
  }));
  props.state.groups[0].count = count;
  return props;
}

test("detail replaces the gallery and returning restores its exact scroll", () => {
  const env = environment();
  const Panel = env.load("ui/panel.tsx").default;
  const props = surface();
  const runner = env.runner(() => Panel(props));
  let node = runner.render();
  env.flushFrames();
  env.window.scrollTo(11, 987);
  walk(node, (item) => item.type.name === "CategorySection")[0].props.onOpen("s1");
  node = runner.render();
  env.flushFrames();
  assert.equal(walk(node, (item) => item.type.name === "CategorySection").length, 0);
  assert.equal(walk(node, (item) => item.type.name === "AwarenessCard").length, 0);
  const detail = walk(node, (item) => item.type.name === "FocusCard")[0];
  assert.equal(detail.props.row.id, "s1");
  assert.equal(env.window.scrollY, 0);
  detail.props.onExit();
  node = runner.render();
  env.flushFrames();
  assert.equal(walk(node, (item) => item.type.name === "FocusCard").length, 0);
  assert.equal(walk(node, (item) => item.type.name === "CategorySection").length, 1);
  assert.equal(env.window.scrollX, 11);
  assert.equal(env.window.scrollY, 987);
  assert.ok(env.scrollCalls.some((call) => call.anchor === "sticker-tile-s1"));
  assert.ok(env.scrollCalls.some((call) => call.focus === "sticker-open-s1"));
});

test("detail preserves search and selection; changing zones clears old focus and selection", () => {
  const env = environment();
  const { useLibraryModel } = env.load("ui/library_model.ts");
  const runner = env.runner(() => useLibraryModel(surface()));
  let model = runner.render();
  model.setQuery("Original");
  model.toggleSelected("s1");
  model.openFocus("s1");
  model = runner.render();
  model.closeFocus();
  model = runner.render();
  assert.equal(model.query, "Original");
  assert.deepEqual(Array.from(model.selected), ["s1"]);
  model.openFocus("s1");
  model = runner.render();
  model.setViewZone("z2");
  model = runner.render();
  assert.equal(model.view, "z2");
  assert.equal(model.focus, "");
  assert.deepEqual(Array.from(model.selected), []);
});

test("removing the focused row returns to the gallery without a ghost detail", () => {
  const env = environment();
  const Panel = env.load("ui/panel.tsx").default;
  const props = surface();
  const runner = env.runner(() => Panel(props));
  let node = runner.render();
  walk(node, (item) => item.type.name === "CategorySection")[0].props.onOpen("s1");
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "FocusCard").length, 1);
  props.state = { ...props.state, stickers: props.state.stickers.filter((row) => row.id !== "s1") };
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "FocusCard").length, 0);
});

test("failed saves keep the draft; cancel and reopening reload the current row", async () => {
  const env = environment();
  const { FocusCard } = env.load("ui/components/focus_card.tsx");
  const props = surface(async () => { throw new Error("update_failed"); });
  const detailProps = { surface: props, row: props.state.stickers[0], onExit: () => {} };
  const runner = env.runner(() => FocusCard(detailProps));
  let node = runner.render();
  button(node, "编辑").props.onClick();
  node = runner.render();
  const input = walk(node, (item) => item.type.name === "Input" && item.props.value === "Original")[0];
  input.props.onChange("Unsubmitted draft");
  node = runner.render();
  await button(node, "保存").props.onClick();
  node = runner.render();
  assert.ok(walk(node, (item) => item.type.name === "Input")
    .some((item) => item.props.value === "Unsubmitted draft"));
  assert.equal(walk(node, (item) => item.type.name === "Alert").length, 1);
  button(node, "取消").props.onClick();
  node = runner.render();
  detailProps.row = { ...detailProps.row, desc: "Server value" };
  node = runner.render();
  button(node, "编辑").props.onClick();
  node = runner.render();
  assert.ok(walk(node, (item) => item.type.name === "Input")
    .some((item) => item.props.value === "Server value"));
});

test("failed deletes do not close detail; pending actions block duplicate requests", async () => {
  let exits = 0;
  let calls = 0;
  let rejectRequest;
  const env = environment();
  const { FocusCard } = env.load("ui/components/focus_card.tsx");
  const props = surface(() => {
    calls++;
    return new Promise((_resolve, reject) => { rejectRequest = reject; });
  });
  const runner = env.runner(() => FocusCard({
    surface: props,
    row: props.state.stickers[0],
    onExit: () => exits++,
  }));
  let node = runner.render();
  const remove = button(node, "删除").props.onClick;
  const pending = remove();
  await Promise.resolve();
  node = runner.render();
  assert.ok(walk(node, (item) => item.type.name === "Button").every((item) => item.props.disabled));
  await remove();
  assert.equal(calls, 1);
  rejectRequest(new Error("remove_failed"));
  await pending;
  node = runner.render();
  assert.equal(exits, 0);
  assert.equal(walk(node, (item) => item.type.name === "Alert").length, 1);
  assert.equal(button(node, "删除").props.disabled, false);
});

test("raw DOM length values use explicit CSS units rather than React-only numeric pixels", () => {
  const lengths = new Set([
    "width", "height", "minWidth", "maxWidth", "minHeight", "maxHeight", "gap",
    "top", "right", "bottom", "left", "borderRadius", "fontSize", "padding",
    "paddingTop", "paddingBottom", "margin", "marginTop", "marginBottom",
  ]);
  const filenames = [
    "ui/components/focus_card.tsx",
    "ui/components/sticker_tile.tsx",
    "ui/components/category_section.tsx",
  ];
  for (const filename of filenames) {
    const source = ts.createSourceFile(
      filename, fs.readFileSync(path.join(root, filename), "utf8"), ts.ScriptTarget.Latest, true,
    );
    const check = (node) => {
      if (ts.isPropertyAssignment(node) && lengths.has(node.name.getText(source))) {
        const value = node.initializer;
        if (ts.isNumericLiteral(value)) {
          assert.equal(Number(value.text), 0, `${filename}: ${node.getText(source)} lacks CSS units`);
        }
      }
      ts.forEachChild(node, check);
    };
    check(source);
  }
});

test("the selected original image jumps ahead of queued thumbnails and stale tasks are skipped", async () => {
  const calls = [];
  const pending = new Map();
  const env = environment({ realPreview: true });
  const { useStickerPreview } = env.load("ui/preview.ts");
  const props = surface((_action, args) => {
    calls.push(args.id);
    return new Promise((resolve) => pending.set(args.id, resolve));
  });
  const previewRunner = (id, auto, kind) => {
    const runner = env.runner(() => useStickerPreview(props, id, auto, kind));
    runner.render();
    return runner;
  };
  const first = previewRunner("first", true, "thumb");
  const second = previewRunner("second", true, "thumb");
  const stale = previewRunner("stale", true, "thumb");
  previewRunner("last", true, "thumb");
  previewRunner("original", true, "full");
  stale.unmount();
  assert.deepEqual(calls, ["first", "second"]);
  const finish = (id) => pending.get(id)({ result: {
    mime: "image/png", chunk_base64: "cHJldmlldw==", done: true,
  } });
  const settle = async () => {
    for (let step = 0; step < 12; step++) await Promise.resolve();
  };
  finish("first");
  await settle();
  assert.equal(calls[2], "original");
  finish("second");
  await settle();
  assert.equal(calls[3], "last");
  finish("original");
  finish("last");
  await settle();
  assert.equal(calls.includes("stale"), false);
  first.unmount();
  second.unmount();
});

test("category creation and ZIP upload freeze the browsed target zone", async () => {
  const calls = [];
  let releaseChunk;
  const props = surface(async (action, args) => {
    calls.push({ action, args });
    if (action === "import_upload_start") return { result: { session: "test", chunk_bytes: 1 } };
    if (action === "import_upload_chunk") await new Promise((resolve) => { releaseChunk = resolve; });
    return { result: { imported: 1 } };
  });
  const env = environment();
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  const runner = env.runner(() => useModel(props));
  let model = runner.render();
  model.setViewZone("z2");
  model.setNewName("New category");
  model = runner.render();
  await model.createCategory();
  assert.equal(calls.find((call) => call.action === "group_create").args.zone, "z2");
  model = runner.render();
  const upload = model.importZip({ name: "test.zip", size: 1, slice: () => ({}) });
  await settle();
  model.setViewZone("z1");
  runner.render();
  releaseChunk();
  await upload;
  assert.equal(calls.find((call) => call.action === "import_upload_finish").args.zone, "z2");
  assert.match(runner.render().libraryNote, /Two/);
});

test("multi-image collection freezes the zone even if the browsing view changes", async () => {
  const calls = [];
  const env = environment();
  let runner;
  const props = surface(async (action, args) => {
    calls.push({ action, args });
    if (action === "add" && calls.length === 1) {
      runner.render().setViewZone("z2");
      runner.render();
    }
    return { result: { note: "sticker_added" } };
  });
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  runner = env.runner(() => useModel(props));
  let model = runner.render();
  model.collectInto("g1");
  model = runner.render();
  await model.chooseCollectedFiles([{ size: 1 }, { size: 1 }]);
  assert.deepEqual(calls.map((call) => call.args.zone), ["z1", "z1"]);
});

test("large batch updates and deletes send every ID in chunks of at most 200", async () => {
  const calls = [];
  const props = largeSurface(async (action, args) => {
    calls.push({ action, ids: Array.from(args.ids) });
    return { result: { updated: args.ids.length, removed: args.ids.length, missing: [] } };
  });
  const env = environment();
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  const runner = env.runner(() => useModel(props));
  let model = runner.render();
  props.state.stickers.forEach((row) => model.toggleSelected(row.id));
  model = runner.render();
  await model.runBatch({ disabled: true });
  assert.deepEqual(calls.map((call) => call.ids.length), [200, 1]);
  assert.equal(new Set(calls.flatMap((call) => call.ids)).size, 201);
  calls.length = 0;
  model = runner.render();
  await model.batchDelete();
  assert.deepEqual(calls.map((call) => call.ids.length), [200, 1]);
  assert.equal(runner.render().selected.length, 0);
});

test("a partial batch failure preserves unconfirmed and failed deletion selections", async () => {
  let callCount = 0;
  const props = largeSurface(async (_action, args) => {
    callCount++;
    if (callCount === 2) throw new Error("temporary_timeout");
    return { result: { removed: args.ids.length - 1, missing: [args.ids[0]] } };
  });
  const env = environment();
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  const runner = env.runner(() => useModel(props));
  let model = runner.render();
  props.state.stickers.forEach((row) => model.toggleSelected(row.id));
  model = runner.render();
  await model.batchDelete();
  model = runner.render();
  assert.deepEqual(Array.from(model.selected), ["item-0", "item-200"]);
  assert.match(model.libraryNote, /199/);
  assert.match(model.libraryNote, /temporary_timeout/);
});

test("management locking is synchronous and refresh retries never repeat a write", async () => {
  let calls = 0;
  let release;
  const props = surface(() => {
    calls++;
    return new Promise((resolve) => { release = () => resolve({ result: {} }); });
  });
  const env = environment();
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  const runner = env.runner(() => useModel(props));
  let model = runner.render();
  const exporting = model.exportPack();
  await model.exportPack();
  await model.repair();
  assert.equal(calls, 1);
  assert.equal(runner.render().pending, "export_pack");
  release();
  await exporting;
  model = runner.render();
  props.api.call = async () => { calls++; return { result: {} }; };
  props.api.refresh = async () => { throw new Error("refresh_timeout"); };
  model.setNewName("Created once");
  model = runner.render();
  await model.createCategory();
  model = runner.render();
  assert.equal(model.refreshFailed, true);
  assert.equal(model.creating, false);
  assert.match(model.libraryNote, /刷新失败/);
  const writes = calls;
  await model.repair();
  assert.equal(calls, writes);
  props.api.refresh = async () => {};
  await model.retryRefresh();
  assert.equal(calls, writes);
  assert.equal(runner.render().refreshFailed, false);
});

test("hidden selections can be cleared and destructive confirmation names hidden items", async () => {
  let confirmation;
  const env = environment({ confirm: async (settings) => { confirmation = settings; return false; } });
  const props = largeSurface(async () => ({ result: {} }), 3);
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  const runner = env.runner(() => useModel(props));
  let model = runner.render();
  props.state.stickers.forEach((row) => model.toggleSelected(row.id));
  model = runner.render();
  await model.batchDelete(2);
  assert.match(confirmation.message, /3/);
  assert.match(confirmation.message, /2.*筛选/);
  model.clearHiddenSelection(["item-1"]);
  assert.deepEqual(Array.from(runner.render().selected), ["item-1"]);
});

test("failed zone description saves keep the editor and its draft", async () => {
  const env = environment();
  const ZoneBar = env.load("ui/components/zone_bar.tsx").ZoneBar;
  const props = surface();
  const zoneProps = {
    surface: props, zones: props.state.zones, view: "z1", activeZone: "z1",
    onSetDesc: async () => false, onRename: async () => false,
  };
  const runner = env.runner(() => ZoneBar(zoneProps));
  let node = runner.render();
  button(node, "编辑区说明").props.onClick();
  node = runner.render();
  walk(node, (item) => item.type.name === "Input")[0].props.onChange("Keep this draft");
  node = runner.render();
  await button(node, "保存").props.onClick();
  node = runner.render();
  assert.ok(walk(node, (item) => item.type.name === "Input")
    .some((item) => item.props.value === "Keep this draft"));
  assert.ok(button(node, "取消"));
});

test("zone management and category descriptions expand without hiding the image grid", () => {
  const env = environment();
  const ZoneBar = env.load("ui/components/zone_bar.tsx").ZoneBar;
  const CategorySection = env.load("ui/components/category_section.tsx").CategorySection;
  const props = surface();
  const zoneNode = env.runner(() => ZoneBar({
    surface: props, zones: props.state.zones, view: "z2", activeZone: "z1",
  })).render();
  const zoneMenu = walk(zoneNode, (item) => item.type === "details")[0];
  assert.equal(zoneMenu.props.open, undefined);
  assert.ok(button(zoneMenu, "新建区"));
  assert.ok(button(zoneMenu, "编辑区说明"));
  assert.equal(walk(zoneMenu, (item) => item.type.name === "Button")
    .some((item) => text(item).includes("让她改用这个区")), false);
  assert.ok(button(zoneNode, "让她改用这个区"));
  const categoryNode = CategorySection({
    surface: props,
    section: { name: "One", group: "g1", desc: "Long category description", editable: false,
      total: 1, rows: [props.state.stickers[0]] },
    selected: [], descEditing: "", collectBusy: false,
  });
  const categoryMenu = walk(categoryNode, (item) => item.type === "details")[0];
  assert.equal(categoryMenu.props.open, undefined);
  assert.match(text(categoryMenu), /Long category description/);
  assert.equal(walk(categoryMenu, (item) => item.type.name === "StickerTile").length, 0);
  assert.equal(walk(categoryNode, (item) => item.type.name === "StickerTile").length, 1);
});

test("preview retry is explicit, protects double clicks and recovers decode failures", async () => {
  let calls = 0;
  const props = surface(async () => {
    calls++;
    if (calls === 1) throw new Error("temporary_timeout");
    return { result: { mime: "image/png", chunk_base64: "cHJldmlldw==", done: true } };
  });
  const env = environment({ realPreview: true });
  const hook = env.load("ui/preview.ts").useStickerPreview;
  const runner = env.runner(() => hook(props, "retry-image", true));
  runner.render();
  await settle();
  let model = runner.render();
  assert.equal(model.preview, "");
  assert.equal(calls, 1);
  model.retry();
  model.retry();
  runner.render();
  await settle();
  model = runner.render();
  assert.equal(calls, 2);
  assert.match(model.preview, /^data:image\/png/);
  model.onError();
  model = runner.render();
  assert.equal(model.preview, "");
  model.retry();
  runner.render();
  await settle();
  assert.equal(calls, 3);
  assert.match(runner.render().preview, /^data:image\/png/);
  runner.unmount();
});

test("reopening a detail joins the same unfinished original-image request", async () => {
  let calls = 0;
  let finish;
  const props = surface(() => {
    calls++;
    return new Promise((resolve) => { finish = resolve; });
  });
  const env = environment({ realPreview: true });
  const hook = env.load("ui/preview.ts").useStickerPreview;
  const first = env.runner(() => hook(props, "shared-image", true));
  first.render();
  first.unmount();
  const reopened = env.runner(() => hook(props, "shared-image", true));
  reopened.render();
  assert.equal(calls, 1);
  finish({ result: { mime: "image/png", chunk_base64: "cHJldmlldw==", done: true } });
  await settle();
  assert.match(reopened.render().preview, /^data:image\/png/);
  reopened.unmount();
});

test("a successful detail save exits edit mode even when refresh fails", async () => {
  let writes = 0;
  let reports = 0;
  const props = surface(async () => { writes++; return { result: {} }; });
  props.api.refresh = async () => { throw new Error("refresh_timeout"); };
  const env = environment();
  const FocusCard = env.load("ui/components/focus_card.tsx").FocusCard;
  const runner = env.runner(() => FocusCard({
    surface: props, row: props.state.stickers[0], onExit: () => {},
    onRefreshFailed: () => reports++,
  }));
  let node = runner.render();
  button(node, "编辑").props.onClick();
  node = runner.render();
  await button(node, "保存").props.onClick();
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "Input").length, 0);
  assert.equal(reports, 1);
  assert.equal(button(node, "编辑").props.disabled, true);
  props.api.refresh = async () => {};
  await button(node, "重新刷新").props.onClick();
  assert.equal(writes, 1);
  assert.equal(button(runner.render(), "编辑").props.disabled, false);
});

test("image buttons and named checkboxes are siblings with guarded disabled handlers", () => {
  const env = environment();
  const StickerTile = env.load("ui/components/sticker_tile.tsx").StickerTile;
  let opens = 0;
  let selections = 0;
  const props = surface();
  const tileProps = {
    surface: props, row: props.state.stickers[0], disabled: true,
    onOpen: () => opens++, onToggleSelect: () => selections++,
  };
  const runner = env.runner(() => StickerTile(tileProps));
  const node = runner.render();
  const imageButton = walk(node, (item) => item.type === "button")[0];
  const checkbox = walk(node, (item) => item.type === "input")[0];
  assert.equal(imageButton.props.id, "sticker-open-s1");
  assert.match(imageButton.props["aria-label"], /Original/);
  assert.match(checkbox.props["aria-label"], /Original/);
  assert.equal(walk(imageButton, (item) => item.type === "input").length, 0);
  imageButton.props.onClick();
  checkbox.props.onChange();
  assert.equal(opens, 0);
  assert.equal(selections, 0);
});

test("library is the default view with only library and settings navigation", () => {
  const env = environment();
  const Panel = env.load("ui/panel.tsx").default;
  const props = surface();
  const runner = env.runner(() => Panel(props));
  let node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "AwarenessCard").length, 0);
  assert.equal(walk(node, (item) => item.type.name === "Card").length, 0);
  const navigation = walk(node, (item) => item.type.name === "SegmentedControl")[0];
  assert.equal(navigation.props.value, "library");
  assert.deepEqual(Array.from(navigation.props.options, (option) => option.value), ["library", "settings"]);
  navigation.props.onChange("settings");
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "CategorySection").length, 0);
  assert.equal(walk(node, (item) => item.type.name === "AwarenessCard")[0].props.mode, "settings");
  walk(node, (item) => item.type.name === "SegmentedControl")[0].props.onChange("library");
  assert.equal(walk(runner.render(), (item) => item.type.name === "CategorySection").length, 1);
});

test("category navigation survives detail return and reports selections hidden by a filter", () => {
  const env = environment();
  const Panel = env.load("ui/panel.tsx").default;
  const props = surface();
  props.state.groups.push({ name: "g3", zone: "z1", count: 1 });
  props.state.stickers.push({ id: "s3", zone: "z1", group: "g3", desc: "Third" });
  const runner = env.runner(() => Panel(props));
  let node = runner.render();
  const categories = walk(node, (item) => item.type.name === "CategorySection");
  categories[0].props.onToggleSelect("s1");
  categories[1].props.onToggleSelect("s3");
  node = runner.render();
  walk(node, (item) => item.type.name === "LibraryToolbar")[0].props.setCategoryFilter("group:g1");
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "CategorySection").length, 1);
  assert.equal(walk(node, (item) => item.type.name === "BatchBar")[0].props.hiddenCount, 1);
  walk(node, (item) => item.type.name === "CategorySection")[0].props.onOpen("s1");
  node = runner.render();
  walk(node, (item) => item.type.name === "FocusCard")[0].props.onExit();
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "LibraryToolbar")[0].props.categoryFilter, "group:g1");
  assert.equal(walk(node, (item) => item.type.name === "BatchBar")[0].props.hiddenCount, 1);
});

test("malformed batch acknowledgements preserve every unconfirmed selection", async () => {
  const props = largeSurface(async () => ({ result: { removed: 200, missing: [] } }), 201);
  const env = environment();
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  const runner = env.runner(() => useModel(props));
  let model = runner.render();
  props.state.stickers.forEach((row) => model.toggleSelected(row.id));
  model = runner.render();
  await model.batchDelete();
  model = runner.render();
  assert.deepEqual(Array.from(model.selected), ["item-200"]);
  assert.match(model.libraryNote, /invalid_batch_result/);
});

test("send cooldown saves numeric zero independently and blocks duplicate saves while pending", async () => {
  const calls = [];
  let finish;
  const props = surface((action, args) => {
    calls.push({ action, args: { ...args } });
    return new Promise((resolve) => { finish = resolve; });
  });
  props.state.config = { cooldown_sec: 20 };
  props.state.awareness = { min_interval_sec: 0 };
  const env = environment();
  const AwarenessCard = env.load("ui/components/awareness_card.tsx").AwarenessCard;
  const runner = env.runner(() => AwarenessCard({ surface: props, mode: "settings" }));
  let node = runner.render();
  const input = walk(node, (item) => item.type.name === "Input")[0];
  assert.equal(input.props.type, "number");
  assert.equal(input.props.value, "20");
  assert.equal(input.props.max, 3600);
  assert.equal(button(node, "保存发送冷却").props.disabled, true);
  input.props.onChange("0");
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "Slider")[0].props.value, 0);
  assert.match(text(node), /当前发送冷却：20 秒/);
  const save = button(node, "保存发送冷却");
  save.props.onClick();
  save.props.onClick();
  node = runner.render();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].action, "set_send_cooldown");
  assert.deepEqual(calls[0].args, { cooldown_sec: 0 });
  assert.equal(walk(node, (item) => item.type.name === "Input")[0].props.disabled, true);
  props.state.config.cooldown_sec = 0;
  finish({ result: { cooldown_sec: 0, note: "send_cooldown_set" } });
  await settle();
  node = runner.render();
  assert.match(text(node), /关闭发送冷却/);
  assert.match(walk(node, (item) => item.type.name === "Alert")[0].props.message, /发送冷却已保存/);
  assert.equal(button(node, "保存发送冷却").props.disabled, true);
});

test("invalid cooldown drafts never invoke an action and an external update refreshes the input", () => {
  const calls = [];
  const props = surface(async (...args) => { calls.push(args); return { result: {} }; });
  props.state.config = { cooldown_sec: 12.5 };
  const env = environment();
  const AwarenessCard = env.load("ui/components/awareness_card.tsx").AwarenessCard;
  const runner = env.runner(() => AwarenessCard({ surface: props, mode: "settings" }));
  let node = runner.render();
  for (const value of ["", "-1", "3601", "NaN", "Infinity"]) {
    walk(node, (item) => item.type.name === "Input")[0].props.onChange(value);
    node = runner.render();
    const save = button(node, "保存发送冷却");
    assert.equal(save.props.disabled, true);
    save.props.onClick();
    assert.equal(calls.length, 0);
  }
  props.state.config.cooldown_sec = 60;
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "Input")[0].props.value, "60");
  assert.equal(button(node, "保存发送冷却").props.disabled, true);
});

test("failed cooldown save keeps the draft and current setting and allows retry", async () => {
  const props = surface(async () => { throw new Error("config_unavailable"); });
  props.state.config = { cooldown_sec: 20 };
  const env = environment();
  const AwarenessCard = env.load("ui/components/awareness_card.tsx").AwarenessCard;
  const runner = env.runner(() => AwarenessCard({ surface: props, mode: "settings" }));
  let node = runner.render();
  walk(node, (item) => item.type.name === "Input")[0].props.onChange("0");
  button(runner.render(), "保存发送冷却").props.onClick();
  await settle();
  node = runner.render();
  assert.equal(walk(node, (item) => item.type.name === "Input")[0].props.value, "0");
  assert.match(text(node), /当前发送冷却：20 秒/);
  assert.equal(walk(node, (item) => item.type.name === "Alert")[0].props.tone, "danger");
  assert.equal(button(node, "保存发送冷却").props.disabled, false);
});

test("eager selection slider saves zero once, locks controls and follows external updates", async () => {
  const calls = [];
  let finish;
  const props = surface((action, args) => {
    calls.push({ action, args: { ...args } });
    return new Promise((resolve) => { finish = resolve; });
  });
  props.state.eagerness = "eager";
  props.state.config = { eager_attachment_turns: 2 };
  const env = environment();
  const AwarenessCard = env.load("ui/components/awareness_card.tsx").AwarenessCard;
  const runner = env.runner(() => AwarenessCard({ surface: props, mode: "settings" }));
  const slider = (node) => walk(node, (item) => item.type.name === "Slider" && item.props.max === 10)[0];
  let node = runner.render();
  assert.equal(slider(node).props.value, 2);
  assert.equal(slider(node).props.min, 0);
  assert.equal(slider(node).props.step, 1);
  assert.equal(button(node, "保存补图轮数").props.disabled, true);
  slider(node).props.onChange(0);
  node = runner.render();
  assert.match(text(node), /每轮帮选/);
  const save = button(node, "保存补图轮数");
  save.props.onClick();
  save.props.onClick();
  node = runner.render();
  assert.deepEqual(calls, [{ action: "set_eager_attachment_turns", args: { turns: 0 } }]);
  assert.equal(slider(node).props.disabled, true);
  props.state.config.eager_attachment_turns = 0;
  finish({ result: { turns: 0 } });
  await settle();
  node = runner.render();
  assert.equal(button(node, "保存补图轮数").props.disabled, true);
  assert.equal(walk(node, (item) => item.type.name === "Alert")[0].props.message, "补图轮数已保存");
  props.state.config.eager_attachment_turns = 10;
  node = runner.render();
  assert.equal(slider(node).props.value, 10);
  assert.match(text(node), /连续 10 轮未配图后帮选/);
  props.state.eagerness = "natural";
  assert.equal(slider(runner.render()), undefined);
  props.state.eagerness = "reserved";
  assert.equal(slider(runner.render()), undefined);
});

test("failed eager selection save keeps the draft and allows retry", async () => {
  const props = surface(async () => { throw new Error("config_unavailable"); });
  props.state.eagerness = "eager";
  const env = environment();
  const AwarenessCard = env.load("ui/components/awareness_card.tsx").AwarenessCard;
  const runner = env.runner(() => AwarenessCard({ surface: props, mode: "settings" }));
  let node = runner.render();
  const slider = (tree) => walk(tree, (item) => item.type.name === "Slider" && item.props.max === 10)[0];
  assert.equal(slider(node).props.value, 1);
  slider(node).props.onChange(10);
  button(runner.render(), "保存补图轮数").props.onClick();
  await settle();
  node = runner.render();
  assert.equal(slider(node).props.value, 10);
  assert.equal(button(node, "保存补图轮数").props.disabled, false);
  assert.equal(walk(node, (item) => item.type.name === "Alert")[0].props.tone, "danger");
});

test("advanced reminder and diagnostics stay collapsed without hiding ordinary send controls", () => {
  const env = environment();
  const AwarenessCard = env.load("ui/components/awareness_card.tsx").AwarenessCard;
  const node = env.runner(() => AwarenessCard({ surface: surface(), mode: "settings" })).render();
  const panels = walk(node, (item) => item.type === "details");
  assert.equal(panels.length, 2);
  assert.ok(panels.every((item) => !item.props.open));
  assert.equal(walk(panels[0], (item) => item.type.name === "Slider").length, 1);
  assert.equal(walk(panels[1], (item) => item.type === "dl").length, 3);
  assert.equal(walk(panels[0], (item) => item.type.name === "Input").length, 0);
  assert.equal(walk(node, (item) => item.type.name === "SegmentedControl").length, 1);
  assert.equal(walk(node, (item) => item.type.name === "Input").length, 1);
});

test("library management expands explicitly and category creation keeps the form visible", () => {
  const env = environment();
  const Toolbar = env.load("ui/components/library_toolbar.tsx").LibraryToolbar;
  let creates = 0;
  const filters = [];
  const props = {
    surface: surface(), query: "", categories: [], categoryFilter: "",
    zipInputRef: { current: null }, imgInputRef: { current: null },
    onToggleCreating: () => creates++,
    setCategoryFilter: (value) => filters.push(value),
  };
  const runner = env.runner(() => Toolbar(props));
  let node = runner.render();
  const categorySelect = walk(node, (item) => item.type.name === "Select")[0];
  assert.equal(categorySelect.props.value, "all");
  assert.equal(categorySelect.props.options[0].value, "all");
  categorySelect.props.onChange("group:g1");
  categorySelect.props.onChange("all");
  assert.deepEqual(filters, ["group:g1", ""]);
  const menu = () => walk(node, (item) => item.props.id === "sticker-library-management")[0];
  const more = () => walk(node, (item) => item.props.className === "sticker-more")[0];
  assert.equal(menu().props.hidden, true);
  more().props.onClick();
  node = runner.render();
  assert.equal(menu().props.hidden, false);
  assert.equal(more().props["aria-expanded"], "true");
  button(node, "新建分类").props.onClick();
  node = runner.render();
  assert.equal(creates, 1);
  assert.equal(menu().props.hidden, true);
  props.creating = true;
  node = runner.render();
  assert.ok(button(node, "创建分类"));
});

test("empty batch tags disable actions and stale clicks never dispatch a request", () => {
  const env = environment();
  const BatchBar = env.load("ui/components/batch_bar.tsx").BatchBar;
  const calls = [];
  const props = {
    surface: surface(), selected: ["s1"], hiddenCount: 0, zone: "z1",
    batchTags: "", batchGroup: "", onRun: (patch) => calls.push(patch),
  };
  for (const draft of ["", "  ", ",，、", "x".repeat(25)]) {
    props.batchTags = draft;
    const node = BatchBar(props);
    for (const name of ["加标签", "删标签"]) {
      const action = button(node, name);
      assert.equal(action.props.disabled, true);
      action.props.onClick();
    }
  }
  assert.equal(calls.length, 0);
  props.batchTags = "喜欢， 温柔";
  let node = BatchBar(props);
  assert.equal(button(node, "删标签").props.disabled, false);
  button(node, "删标签").props.onClick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].tags_remove, props.batchTags);
  props.disabled = true;
  node = BatchBar(props);
  button(node, "加标签").props.onClick();
  assert.equal(calls.length, 1);
});

test("batch model rejects empty tag edits while allowing valid edits and empty-category moves", async () => {
  const calls = [];
  const props = surface(async (action, args) => {
    calls.push({ action, args });
    return { result: { updated: args.ids.length, missing: [] } };
  });
  const env = environment();
  const useModel = env.load("ui/library_model.ts").useLibraryModel;
  const runner = env.runner(() => useModel(props));
  let model = runner.render();
  model.toggleSelected("s1");
  model = runner.render();
  for (const patch of [{}, { tags_add: " " }, { tags_remove: "，、" }, { tags_add: "x".repeat(25) }]) {
    await model.runBatch(patch);
  }
  model = runner.render();
  assert.equal(calls.length, 0);
  assert.match(model.libraryNote, /请先填写有效标签/);
  assert.deepEqual(Array.from(model.selected), ["s1"]);
  await model.runBatch({ tags_add: "温柔，温柔, 喜欢" });
  assert.equal(calls[0].args.tags_add, "温柔,喜欢");
  await model.runBatch({ group: "", tags_remove: "" });
  assert.equal(calls[1].args.group, "");
  assert.equal("tags_remove" in calls[1].args, false);
});
