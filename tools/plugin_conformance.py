"""插件社区规范符合性检查(纯静态,不需要 N.E.K.O SDK/venv)。

依据:官方文档 plugin-development / plugin-toml / cli 的发布前置要求,以及 market-corpus 里 127 个在架插件的实测写法。
用法:
    python tools/plugin_conformance.py <plugin_dir> [more_dirs...] [--json]
退出码:0=全部通过(允许 warning),1=存在 error。
"""
import ast
import json
import os
import re
import sys
import tomllib

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
VER_RE = re.compile(r"^\d+\.\d+\.\d+([.\-+][0-9A-Za-z.\-+]*)?$")
RANGE_RE = re.compile(r"^(>=|<=|==|!=|~=|>|<)")
OK_CALL = re.compile(r"\b(Ok|Err)\s*\(")
TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")

errors, warnings = [], []


def err(rule, msg):
    errors.append((rule, msg))


def warn(rule, msg):
    warnings.append((rule, msg))


def find_entry_module(root, entry):
    """entry = 'module.path:ClassName' → 在插件目录里定位模块文件。"""
    mod = entry.split(":", 1)[0]
    parts = mod.split(".")
    # 常见三种写法:plugin.plugins.<id> / plugins.<id> / <id>
    if parts[:2] == ["plugin", "plugins"] and len(parts) >= 3:
        parts = parts[2:]
    elif parts and parts[0] == "plugins" and len(parts) >= 2:
        parts = parts[1:]
    candidate = os.path.join(root, *parts) + ".py"
    pkg = os.path.join(root, *parts, "__init__.py")
    for p in (candidate, pkg):
        if os.path.isfile(p):
            return p
    # 退一步:目录名就是插件 id 时,根 __init__.py
    init = os.path.join(root, "__init__.py")
    return init if os.path.isfile(init) else None


def check(root):
    root = os.path.abspath(root)
    name = os.path.basename(root.rstrip("\\/"))
    # 语料目录是 <slug>/src,规范比较对象是插件目录名(slug),不是 "src"
    if name.lower() in {"src", "plugin"}:
        name = os.path.basename(os.path.dirname(root.rstrip("\\/")))
    tp = os.path.join(root, "plugin.toml")
    if not os.path.isfile(tp):
        err("R1", "缺少 plugin.toml")
        return
    try:
        cfg = tomllib.load(open(tp, "rb"))
    except Exception as e:
        err("R1", f"plugin.toml 解析失败: {e}")
        return

    plug = cfg.get("plugin") or {}
    pid = str(plug.get("id") or "")
    entry = str(plug.get("entry") or "")

    # R2 必填字段
    for f in ("id", "name", "version", "entry"):
        if not plug.get(f):
            err("R2", f"[plugin].{f} 缺失(官方发布必填)")
    if pid and not ID_RE.match(pid):
        err("R2", f"id '{pid}' 不符合 ^[A-Za-z0-9_-]+$")
    if pid and pid != name:
        warn("R2", f"id '{pid}' 与目录名 '{name}' 不一致")
    ver = str(plug.get("version") or "")
    if ver and not VER_RE.match(ver):
        err("R2", f"version '{ver}' 不是 x.y.z 形式")
    if plug.get("type") == "extension":
        err("R2", "type=extension 已被移除")

    # R3 加载入口可解析
    if entry:
        if ":" not in entry:
            err("R3", "entry 必须是 module.path:ClassName")
        else:
            cls_name = entry.split(":", 1)[1]
            mod_path = find_entry_module(root, entry)
            if not mod_path:
                err("R3", f"entry 模块找不到: {entry}")
            else:
                src = open(mod_path, encoding="utf-8", errors="replace").read()
                try:
                    tree = ast.parse(src)
                except SyntaxError as e:
                    err("R3", f"入口模块语法错误: {e}")
                    tree = None
                if tree:
                    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name), None)
                    if cls is None:
                        err("R3", f"entry 指向的类 {cls_name} 不存在于 {os.path.basename(mod_path)}")
                    else:
                        bases = [getattr(b, "id", getattr(b, "attr", "")) for b in cls.bases]
                        if "NekoPluginBase" not in bases:
                            err("R3", f"类 {cls_name} 未继承 NekoPluginBase")
                        decos = [getattr(d, "id", getattr(d, "attr", "")) for d in cls.decorator_list]
                        has_neko = any(
                            isinstance(d, ast.Call) and getattr(d.func, "id", "") == "neko_plugin" for d in cls.decorator_list
                        ) or "neko_plugin" in decos
                        if not has_neko:
                            err("R3", f"类 {cls_name} 缺少 @neko_plugin")

    # R4 运行时入口
    init = os.path.join(root, "__init__.py")
    src = open(init, encoding="utf-8", errors="replace").read() if os.path.isfile(init) else ""
    if not src:
        err("R4", "缺少 __init__.py")
    else:
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            err("R4", f"__init__.py 语法错误: {e}")
            tree = None
        if tree:
            entries = 0
            for n in ast.walk(tree):
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                pe = next(
                    (d for d in n.decorator_list if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "plugin_entry"),
                    None,
                )
                if pe is None:
                    continue
                entries += 1
                kw = {k.arg: k.value for k in pe.keywords}
                if "id" not in kw:
                    err("R4", f"{n.name}: @plugin_entry 缺 id")
                if not isinstance(n, ast.AsyncFunctionDef):
                    err("R4", f"{n.name}: 入口必须 async def")
                if n.args.kwarg is None:
                    warn("R4", f"{n.name}: 建议用 ** _ 收尾(容忍平台多传参)")
            if entries == 0 and not plug.get("passive"):
                warn("R4", "没有任何 @plugin_entry(非 passive 插件会成为不可调用)")

    # R5 可发现性字段
    kwds = plug.get("keywords") or []
    if not isinstance(kwds, list) or not kwds:
        warn("R5", "keywords 为空,Agent 很难选中这个插件")
    desc = str(plug.get("description") or "")
    if len(desc) < 10:
        warn("R5", "description 太短,写清『做什么 + 什么时候用』")

    # R6 SDK 范围
    sdk = plug.get("sdk") or {}
    for f in ("recommended", "supported"):
        v = str(sdk.get(f) or "")
        if not v:
            warn("R6", f"[plugin.sdk].{f} 未声明")
        elif not RANGE_RE.match(v):
            err("R6", f"[plugin.sdk].{f}='{v}' 不是合法版本范围")

    # R7 测试
    tdir = os.path.join(root, "tests")
    if not os.path.isdir(tdir) or not any(f.startswith("test_") and f.endswith(".py") for f in os.listdir(tdir)):
        err("R7", "缺少 tests/test_*.py(发布检查会跑测试)")

    # R8 自定义段 → config.example.toml
    custom = [k for k in cfg if k not in {"plugin", "plugin_runtime", "plugin_store"} and not k.startswith("plugin_")]
    if custom and not os.path.isfile(os.path.join(root, "config.example.toml")):
        warn("R8", f"有自定义配置段 {custom},但缺 config.example.toml")

    # R9 发布工作流
    for wf in ("verify.yml", "release.yml"):
        if not os.path.isfile(os.path.join(root, ".github", "workflows", wf)):
            err("R9", f"缺少 .github/workflows/{wf}(neko-plugin publish 的前置条件)")

    # R10 ruff
    if not os.path.isfile(os.path.join(root, "ruff.toml")):
        warn("R10", "缺少 ruff.toml(官方 check 会按标准规则 lint)")

    # R11 i18n
    i18n = (plug.get("i18n") or {})
    if i18n:
        ld = str(i18n.get("locales_dir") or "i18n")
        default = str(i18n.get("default_locale") or "zh-CN")
        ddir = os.path.join(root, ld)
        if not os.path.isdir(ddir):
            err("R11", f"[plugin.i18n] 声明了 {ld}/,但目录不存在")
        else:
            f = os.path.join(ddir, f"{default}.json")
            if not os.path.isfile(f):
                err("R11", f"缺少默认语言文件 {ld}/{default}.json")
            else:
                try:
                    json.load(open(f, encoding="utf-8"))
                except Exception as e:
                    err("R11", f"{ld}/{default}.json 不是合法 JSON: {e}")

    # R12 UI 资源
    ui = plug.get("ui") or {}
    if ui.get("enabled"):
        for panel in ui.get("panel") or []:
            p = str(panel.get("entry") or "")
            if p and not os.path.isfile(os.path.join(root, p)):
                err("R12", f"UI 面板文件不存在: {p}")
        for guide in ui.get("guide") or []:
            g = str(guide.get("entry") or "")
            if g and not os.path.isfile(os.path.join(root, g)):
                err("R12", f"UI 指南文件不存在: {g}")

    # R13 卫生
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "vendor", "__pycache__", "node_modules"}]
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(cur, f)
            t = open(p, encoding="utf-8", errors="replace").read()
            if TODO_RE.search(t):
                warn("R13", f"{os.path.relpath(p, root)} 含 TODO/FIXME 占位")
            if "time.sleep(" in t:
                warn("R13", f"{os.path.relpath(p, root)} 用了 time.sleep(阻塞事件循环,建议 asyncio.sleep)")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        return 2
    summary = []
    for d in args:
        errors.clear()
        warnings.clear()
        check(d)
        summary.append((d, list(errors), list(warnings)))
    if as_json:
        print(json.dumps([{"dir": d, "errors": e, "warnings": w} for d, e, w in summary], ensure_ascii=False, indent=1))
    else:
        total_e = total_w = 0
        for d, es, ws in summary:
            total_e += len(es)
            total_w += len(ws)
            flag = "PASS" if not es else "FAIL"
            print(f"[{flag}] {os.path.basename(os.path.abspath(d))}  error={len(es)} warning={len(ws)}")
            for rule, msg in es:
                print(f"    ✗ {rule} {msg}")
            for rule, msg in ws:
                print(f"    ! {rule} {msg}")
        print(f"\n合计: {len(summary)} 个插件,error {total_e},warning {total_w}")
    return 1 if any(e for _, e, _ in summary) else 0


if __name__ == "__main__":
    sys.exit(main())
