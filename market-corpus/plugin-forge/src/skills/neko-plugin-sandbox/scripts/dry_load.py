#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin-forge · 干装载检查器（零第三方依赖，Python 3.8+）

它回答一个问题：这个插件目录能不能被真的装载一次。
以前 agent 说「测过了」没法证伪——本仓所有门禁都只查格式，没有一个环节执行过插件代码。

用法
  python dry_load.py <插件目录> [--root 沙箱根] [--json] [--quiet] [--self-test]

两种模式（输出里必须写明用的是哪一种，不许混着说）
  真装载：沙箱 venv 与官方 checkout 都在 → 起子进程 `importlib.import_module(入口模块)`
          并取 entry 符号。这一步会执行插件的模块级代码，所以只在沙箱里跑，
          数据目录指向临时根，宿主凭据不进子进程。
  降级检查：没有 venv 或没有 checkout 时，只做 AST 级检查——语法能编译、
          入口符号真在顶层、入口函数是 async、包内相对导入指向的文件真在、
          清单形状合法。降级模式**证明不了能 import**，输出会明写这点。

判据出处（都读过上原文，钉在 N.E.K.O main e14f6196395a346ff422e0eb852c5eae3befc1cf）
  - `[plugin].entry` 形状 `^[a-zA-Z0-9_.]+:[a-zA-Z0-9_]+$`（plugin/config/schema.py:214）
  - `type` 只允许 plugin（默认）/ adapter，其他历史类型已移除（profile-neko §2）
  - `[plugin.host]` 已移除，写了直接报错（plugin/config/schema.py:18、233-235）
  - 没写 `[plugin_runtime] auto_start` 时按 false 处理（plugin/core/registry.py:1293）
  - 插件目录名必须等于 `[plugin].id`，否则扫描不到（沙箱 SKILL.md 失败判定表）

退出码：0 通过（含降级通过，会明写降级）；1 发现问题；2 用法或环境错误。
凭据过滤判据（sandbox.env_is_secret）拿不到时也是 2：那时子进程会继承宿主全部环境，
这一趟没核过，不许算通过。
"""
import argparse
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ENTRY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TYPES_OK = {"plugin", "adapter"}
# 加载入口允许的装饰器与基类名（上游 decorators 面的子集，读于 e14f61963）
ENTRY_DECORATORS = {"plugin_entry", "neko_plugin"}
RUNTIME_DECORATORS = {"llm_tool", "lifecycle", "message", "timer_interval", "custom_event",
                      "before_entry", "after_entry", "around_entry", "replace_entry",
                      "quick_action", "hook"}
REQUIRED_MARK = "降级模式证明不了能不能真的 import"


class EnvBlocked(RuntimeError):
    """真装载的前置条件不成立，这一趟不许跑，也不许降级成「通过」。"""


def toml_section_value(text, section, key):
    """不依赖 tomllib（3.11+）读某个 [section] 下的标量值。

    与沙箱脚本同一口径：只认段头到下一个段头之间的 `key = value`，
    子段（[plugin.ui]）的键不算顶层，行尾注释要去掉。
    """
    cur = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        # 段头可以带行尾注释（[plugin]  # 说明）。先剥注释再判，与
        # security_audit.plugin_toml_keys 同口径：两处判据不一致时会出假红。
        # 只对以 [ 开头的行剥注释，值行（key = "a#b"）不进这个分支，行为不变。
        head = line.split("#")[0].strip() if line.startswith("[") else line
        if head.startswith("[") and head.endswith("]"):
            cur = head[1:-1].strip()
            continue
        if cur != section or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != key:
            continue
        # 只在引号外面剥行尾注释。TOML 里 `id = "a#b"` 的 # 属于值，
        # 无条件 split("#") 会把它截成 "a"，与上游 tomllib 的读法不一致（自检抓到过一次）。
        v = v.strip()
        inq, cut = "", None
        for idx, ch in enumerate(v):
            if inq:
                if ch == inq:
                    inq = ""
            elif ch in "\"'":
                inq = ch
            elif ch == "#":
                cut = idx
                break
        if cut is not None:
            v = v[:cut].strip()
        return v.strip('"').strip("'") if v else None
    return None


def toml_has_section(text, section):
    # 缩进的段头与带行尾注释的段头都要认到，与 toml_section_value 同一口径（A791 第 3 条）。
    # 旧写法是子串匹配，缩进行与首行段头都漏；行首锚定后也不再误命中值串里的同名文本。
    pat = r"(?m)^[ \t]*\[%s\][ \t]*(#.*)?$" % re.escape(section)
    return bool(re.search(pat, text.replace("\r\n", "\n")))


def plugin_package_matches_directory(package, directory_name):
    """包名与目录名是否算对上。照上游 plugin/core/entry_points.py:47-62（main e14f61963）。

    单向等价：目录可以叫 kebab（my-cool-plugin），包名必须叫 snake（my_cool_plugin），
    因为包名是 Python 真要 import 的那一侧。反过来不成立，真对不上照旧报错。
    """
    return package == directory_name or package == directory_name.replace("-", "_")


def entry_module_file(pkg_dir, mod):
    """入口里的 module.path 对应到插件目录里的哪个文件。返回 (路径, 问题)。

    只有包写法能用，2026-11-17 对上游两种装载模式实测：
      - `plugin.plugins.<id>[.sub]`：内置件与 CLI 构建件（build.py 走 source_only=True）；
      - `plugins.<id>[.sub]`：安装态归一化后的命名空间，两种模式都进得来。
    上游 host.py:664 要求 `plugins.` 后第一段等于目录名，对不上就 import 不到，这里照同一条规则报。
    裸模块名（`main`、`pkg.mod`）在这里只用于定位文件，check_manifest 已把它判成问题。
    """
    name = os.path.basename(os.path.normpath(pkg_dir))
    parts = [p for p in mod.split(".") if p]
    if parts[:1] == ["plugin"]:
        parts = parts[1:]
    if parts[:1] == ["plugins"]:
        if len(parts) < 2:
            return None, "入口包路径 %s 不完整：至少要写到 plugins.<目录名>" % mod
        if not plugin_package_matches_directory(parts[1], name):
            return None, ("入口写的是 %s，插件目录却叫 %s：上游按 plugins.<目录名> 解析"
                          "（plugin/core/host.py:664 + entry_points.py:47-62），对不上就 import 不到"
                          % (mod, name))
        parts = parts[2:]
    base = os.path.join(pkg_dir, *parts) if parts else pkg_dir
    for cand in (base + ".py", os.path.join(base, "__init__.py")):
        if os.path.isfile(cand):
            return cand, None
    return None, ("入口模块 %s 对应的文件不在插件目录里：%s.py 或 %s%s__init__.py 都不存在"
                  % (mod, base, base, os.sep))


def check_manifest(pkg_dir):
    """清单层的问题列表。返回 (problems, info)。"""
    problems, info = [], {}
    toml_path = os.path.join(pkg_dir, "plugin.toml")
    if not os.path.isfile(toml_path):
        return ["目录里没有 plugin.toml，不是插件包"], info
    text = io.open(toml_path, encoding="utf-8", errors="replace").read()
    pid = toml_section_value(text, "plugin", "id")
    entry = toml_section_value(text, "plugin", "entry")
    ptype = (toml_section_value(text, "plugin", "type") or "plugin").lower()
    auto = toml_section_value(text, "plugin_runtime", "auto_start")
    info.update({"id": pid, "entry": entry, "type": ptype, "auto_start": auto})
    if not pid:
        problems.append("plugin.toml 里没解析到 [plugin] id")
    elif not ID_RE.match(pid):
        problems.append("[plugin] id「%s」不合法：只允许 ^[A-Za-z_][A-Za-z0-9_]*$" % pid)
    elif not plugin_package_matches_directory(pid, os.path.basename(os.path.normpath(pkg_dir))):
        problems.append("目录名「%s」与 [plugin] id「%s」对不上：上游按目录名扫描，"
                        "id 是包名那一侧，只允许目录用 kebab、id 用 snake 这一种等价"
                        "（plugin/core/host.py:533 + entry_points.py:47-62）"
                        % (os.path.basename(os.path.normpath(pkg_dir)), pid))
    if ptype not in TYPES_OK:
        problems.append("[plugin] type「%s」不支持：上游只留 plugin / adapter，"
                        "历史类型已移除" % ptype)
    if toml_has_section(text, "plugin.host"):
        problems.append("写了 [plugin.host]：上游已移除该节，宿主 import 期就报错"
                        "（plugin/config/schema.py:18、233-235）")
    if not entry:
        problems.append("[plugin] entry 缺失：宿主没有加载入口")
    elif not ENTRY_RE.match(entry):
        problems.append("[plugin] entry「%s」形状不对：必须是 module.path:Symbol"
                        "（schema 的正则是 ^[a-zA-Z0-9_.]+:[a-zA-Z0-9_]+$）" % entry)
    else:
        mod = entry.split(":", 1)[0]
        head = mod.split(".")[0]
        if head not in ("plugin", "plugins"):
            # 实测（2026-11-17，上游 host.py:649 两种模式各跑一遍）：裸模块名装不进。
            # source_only=False 给 ModuleNotFoundError，source_only=True 给
            # ValueError「Source-only plugin module must match its source directory」。
            # schema 的正则放得下这种写法，宿主不放行：形状对不等于装得进。
            problems.append(
                "[plugin] entry「%s」上游装不进：模块路径要以 plugin.plugins.<目录名> 或 "
                "plugins.<目录名> 开头。裸模块名（如 main）在两种装载模式下都失败"
                "（plugin/core/host.py:649、664；2026-11-17 对当前上游实测）" % entry)
    if auto is None:
        problems.append("没写 [plugin_runtime] auto_start：上游按 false 处理"
                        "（plugin/core/registry.py:1293），插件会被注册但不会 autostart")
    return problems, info


def _decor_names(node):
    out = []
    for dec in getattr(node, "decorator_list", []) or []:
        if isinstance(dec, ast.Call):
            tgt = dec.func
        else:
            tgt = dec
        if isinstance(tgt, ast.Attribute):
            out.append(tgt.attr)
        elif isinstance(tgt, ast.Name):
            out.append(tgt.id)
    return out


def check_source(pkg_dir, entry):
    """AST 层：入口符号、async、装饰器、包内导入。"""
    problems = []
    if not entry or ":" not in entry:
        return ["entry 形状不对，没法定位源码"], None
    mod, _, sym = entry.partition(":")
    path, err = entry_module_file(pkg_dir, mod)
    if err:
        return [err], None
    src = io.open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return ["入口模块语法错误：%s（第 %s 行）" % (e.msg, e.lineno)], None
    found = None
    for node in tree.body:                      # 只看顶层：入口符号必须是模块级
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == sym:
            found = node
            break
    if found is None:
        problems.append("入口符号 %s 不在 %s 的顶层定义里" % (sym, mod))
    else:
        decs = set(_decor_names(found))
        if isinstance(found, ast.AsyncFunctionDef):
            problems.append("入口 %s 是 async 函数而不是类：加载入口要指向 NekoPluginBase 子类"
                            "或模块级装饰过的入口" % sym)
        elif isinstance(found, ast.FunctionDef):
            problems.append("入口函数 %s 不是 async def（上游要求入口协程化）" % sym)
        elif isinstance(found, ast.ClassDef):
            bases = []
            for b in found.bases:
                bases.append(b.attr if isinstance(b, ast.Attribute)
                             else getattr(b, "id", ""))
            if "NekoPluginBase" not in bases and "PluginRouter" in bases:
                problems.append("类 %s 直接拿 PluginRouter 当加载入口：上游按 NekoPluginBase 子类解析"
                                % sym)
            if not decs & ENTRY_DECORATORS and "neko_plugin" not in {d.lower() for d in bases}:
                problems.append("类 %s 没有 @neko_plugin 装饰：宿主按它登记插件类"
                                "（本机 neko_host 实测 11 个内置件全部带该装饰）" % sym)
        if isinstance(found, ast.ClassDef):
            for sub in found.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sd = set(_decor_names(sub)) & RUNTIME_DECORATORS
                    if sd and not isinstance(sub, ast.AsyncFunctionDef):
                        problems.append("方法 %s.%s 带 %s 却不是 async def（R3：入口函数必须协程）"
                                        % (sym, sub.name, sorted(sd)[0]))
    # 包内相对导入指向的文件必须真在
    pkg_names = set()
    for root, dirs, files in os.walk(pkg_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for f in files:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(root, f), pkg_dir)
                pkg_names.add(rel[:-3].replace(os.sep, ".").replace(".", "/"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = (node.module or "").replace(".", "/")
            # `from . import ghost` 没有 module，只有名字：每个名字都是一个子模块，
            # 之前只核 module 的写法会把这一类整批放过
            wants = [base] if base else [a.name for a in node.names if a.name != "*"]
            for want in wants:
                if not want:
                    continue
                if not any(w == want or w.startswith(want + "/") or
                            w.endswith("/" + want) for w in pkg_names):
                    problems.append("入口模块里 %s 的相对导入找不到对应文件：%s"
                                    % ("." * node.level, want))
    return problems, path


def _isolated_env(root_tmp):
    """真装载子进程的环境：数据根指到临时目录，宿主凭据不进子进程。"""
    env = os.environ.copy()
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"):
        env[key] = os.path.join(root_tmp, key.lower())
    # 上游已无读取点，留一个路径值只会注入假的「嵌入式」身份。与 sandbox.py 同口径，清掉。
    env.pop("NEKO_PLUGIN_HOSTED_BY_AGENT", None)
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sandbox import env_is_secret          # 同一份判据，不在这里重写
        for k in [k for k, v in env.items() if env_is_secret(k, v)]:
            env.pop(k, None)
    except Exception as e:
        # 判据来自 sandbox.py。拿不到它就是没了凭据过滤：子进程会继承宿主全部环境。
        # 旧版只打一行警告然后照跑（fail-open），等于「过滤失败 = 不设防」。
        # 现在 fail-closed：不跑真装载，退出码 2，输出里说明这一趟没核过（A791 第 2 条③）。
        # 出声之前先把 stderr 转 UTF-8：main() 里那句 reconfigure 在这条路上没跑过
        # （模块被 import 后直接调本函数时），Windows 上默认是本机代码页 GBK，
        # 上层按 UTF-8 解出来是乱码，等于警告丢了（反证 34 实测）。
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        raise EnvBlocked(
            "凭据过滤判据拿不到（sandbox.env_is_secret：%s）——拒绝继续真装载："
            "子进程会继承宿主全部环境变量。请把 sandbox.py 与 dry_load.py 一起拉取。"
            % type(e).__name__)
    for sub in ("home", "localappdata", "appdata", "temp", "tmp"):
        try:
            os.makedirs(os.path.join(root_tmp, sub), exist_ok=True)
        except OSError:
            pass
    return env


def real_load(pkg_dir, repo_root, py, info):
    """用上游自己的导入函数真装载一次。

    返回 (kind, problems, 输出尾巴)，kind 三取一：
      "ok"   真装载跑了，成败看 problems
      "env"  宿主侧取不到 _import_plugin_module，交回调用方降级并写原因
    判据不自己模拟：上游 plugin/core/host.py:649 的 `_import_plugin_module` 里有
    模块树清理、`plugins.<id>` 命名规则与 cwd 兜底。自己 importlib 一遍会放行一些
    宿主其实装不进的插件，那正是这条检查要防的假绿。
    """
    entry = info.get("entry") or ""
    if ":" not in entry:
        return "env", ["entry 不可用，没法定位模块"], ""
    mod, _, sym = entry.partition(":")
    # 宿主自己分两档装载：CLI 构建与内置件走 source_only=True（host.py:656-683），
    # 普通命名空间导入走 False。判据要覆盖两档，只问一档会把真装得进的写法报成失败。
    modes = [True, False] if mod.split(".")[0] in ("plugin", "plugins") else [False]
    toml_path = os.path.abspath(os.path.join(pkg_dir, "plugin.toml"))
    tmp = tempfile.mkdtemp(prefix="dryload-home-")
    try:
        child_env = _isolated_env(tmp)
    except EnvBlocked as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return "blocked", [str(e)], ""
    script = (
        "import json, sys, pathlib\n"
        "sys.path.insert(0, %r)\n"
        "try:\n"
        "    from plugin.core.host import _import_plugin_module\n"
        "except BaseException as e:\n"
        "    print('FORGE-HOST-UNAVAILABLE ' + type(e).__name__ + ' ' + str(e)[:200])\n"
        "    raise SystemExit(97)\n"
        "class _L:\n"
        "    def __getattr__(self, name):\n"
        "        def _noop(*a, **k):\n"
        "            return None\n"
        "        return _noop\n"
        "try:\n"
        "    m = _import_plugin_module(%r, pathlib.Path(%r), _L(), "
        "source_only=__SOURCE_ONLY__)\n"
        "except BaseException as e:\n"
        "    print('FORGE-IMPORT-FAIL ' + type(e).__name__ + ' ' + str(e)[:300])\n"
        "    raise SystemExit(96)\n"
        "obj = getattr(m, %r, None)\n"
        "print('FORGE-RESULT ' + json.dumps({'module_file': getattr(m, '__file__', ''),\n"
        "                                     'symbol_found': obj is not None,\n"
        "                                     'symbol_type': type(obj).__name__,\n"
        "                                     'is_class': isinstance(obj, type)},\n"
        "                                    ensure_ascii=False))\n"
        % (repo_root, mod, toml_path, sym)
    )
    outs, codes = [], []
    try:
        for so in modes:
            r = subprocess.run([py, "-c", script.replace("__SOURCE_ONLY__", repr(so))],
                               capture_output=True, text=True,
                               timeout=180, env=child_env, cwd=tmp)
            outs.append(((r.stdout or "") + (r.stderr or "")).strip())
            codes.append(r.returncode)
            if r.returncode == 0:      # 有一档装得进就用那一档的结果，别把成功读成两份
                break
        out = outs[-1]
    except subprocess.TimeoutExpired:
        # 超时不是「环境缺」而是「插件 import 期卡住」。宿主装载会卡在同一个地方，
        # 所以单独算失败，不许降级成「真装载没问题」（工单 A791 第 2 条）。
        # TimeoutExpired 必须排在 SubprocessError 前面，它是那个类的子类。
        return "ok", ["真装载超时：子进程 180 秒内没返回，插件 import 期可能死循环或长阻塞"], ""
    except (OSError, subprocess.SubprocessError) as e:
        return "env", ["子进程没跑起来：%s（%s）" % (type(e).__name__, str(e)[:80])], ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines = out.splitlines()
    # 结果行靠前缀认，不靠位置：插件在 import 期会自己打印（内置 web_search 就打
    # 「Creating app directory: …」），拿最后一行当结果会把一次成功读成失败。
    head = ""
    for line in reversed(lines):
        if line.startswith("FORGE-RESULT ") or line.startswith("FORGE-IMPORT-FAIL") \
                or line.startswith("FORGE-HOST-UNAVAILABLE"):
            head = line
            break
    if not head:
        return "ok", ["真装载没打出结果行（子进程退出码 %s）" % r.returncode], out[-1200:]
    if head.startswith("FORGE-HOST-UNAVAILABLE") or r.returncode == 97:
        return "env", [head[:200] or "宿主侧不可用"], out[-800:]
    if head.startswith("FORGE-IMPORT-FAIL") or r.returncode == 96:
        return "ok", ["真装载失败：%s（上游在 import 期就报错，宿主会把插件记为 failed）"
                      % head[len("FORGE-IMPORT-FAIL "):][:220]], out[-1200:]
    if r.returncode != 0:
        return "ok", ["真装载子进程异常退出（码 %s）" % r.returncode], out[-1200:]
    try:
        got = json.loads(head[len("FORGE-RESULT "):])
    except ValueError:
        return "ok", ["真装载结果行不是 JSON：%r" % head[:140]], out[-400:]
    problems = []
    if not got.get("symbol_found"):
        problems.append("模块 import 成功，但入口符号 %s 取不到：宿主会在 getattr 处报 "
                        "Class not found in module" % sym)
    return "ok", problems, head


def run(pkg_dir, sandbox_root=None, quiet=False, py=None, repo=None):
    """跑全套检查，返回 (退出码, 报告 dict)。

    --python / --neko-repo 是给手工核对与自检用的显式覆盖：外部 checkout 模式下 venv
    常常不在沙箱根里（本机实测 `~/forge-sandbox-serve` 就没有 .venv），
    没有这两个口子就只能永远走降级模式，真装载那条腿没人跑到过。
    """
    report = {"plugin_dir": pkg_dir, "mode": "", "problems": [], "info": {}}
    if not os.path.isdir(pkg_dir):
        report["problems"] = ["插件目录不存在：%s" % pkg_dir]
        report["mode"] = "usage"
        return 2, report
    problems, info = check_manifest(pkg_dir)
    report["info"] = info
    src_problems, _ = check_source(pkg_dir, info.get("entry"))
    problems += src_problems

    repo_root = repo
    if sandbox_root and not (py and repo_root):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from sandbox import repo_dir, venv_python
            root = __import__("pathlib").Path(sandbox_root).expanduser()
            cand_repo, cand_py = repo_dir(root), venv_python(root)
            if cand_repo.is_dir() and cand_py.is_file():
                repo_root, py = str(cand_repo), str(cand_py)
        except Exception as e:
            problems.append("沙箱配置读不到，走降级模式：%s（%s）" % (type(e).__name__, str(e)[:60]))
    if py and not os.path.isfile(str(py)):
        problems.append("指定的解释器不存在：%s" % py)
        py = None
    if repo_root and not os.path.isdir(str(repo_root)):
        problems.append("指定的官方源码目录不存在：%s" % repo_root)
        py = None
    if py and repo_root and not problems:
        kind, rp, tail = real_load(pkg_dir, repo_root, py, info)
        if kind == "ok":
            report["mode"] = "real-import"
            report["real_load_output"] = tail
            problems += rp
        elif kind == "blocked":
            # 凭据过滤判据缺席：既不是插件的问题，也不是可以降级的情形。
            # 单列 blocked，退出码 2，输出里不许出现「通过」（A791 第 2 条③）。
            report["mode"] = "blocked"
            report["degrade_reason"] = (rp[0] if rp else "凭据过滤判据不可用")[:220]
            problems += rp
        else:
            report["mode"] = "ast-fallback"
            report["note"] = REQUIRED_MARK
            # 宿主侧不可用不是插件的问题，不许记进 problems；只记原因，让人看见这条没核过
            report["degrade_reason"] = ("；".join(x for x in rp if x) or "宿主侧不可用")[:220]
    else:
        report["mode"] = "ast-fallback"
        report["note"] = REQUIRED_MARK
    report["problems"] = problems
    if report.get("mode") == "blocked":
        return 2, report
    return (1 if problems else 0), report


def _print(report, code):
    print("dry_load：插件目录 %s" % report["plugin_dir"])
    reason = ("｜降级原因：%s" % report["degrade_reason"]
              if report.get("degrade_reason") else "")
    print("模式：%s%s%s" % (report.get("mode", ""),
                          "（%s）" % report["note"] if report.get("note") else "", reason))
    info = report.get("info") or {}
    print("清单：id=%s entry=%s type=%s auto_start=%s"
          % (info.get("id"), info.get("entry"), info.get("type"), info.get("auto_start")))
    if report.get("real_load_output"):
        print("真装载输出尾巴：%s" % report["real_load_output"][-300:])
    for p in report.get("problems", []):
        print("  [问题] %s" % p)
    if code == 0:
        # 键取 mode，不取 degrade_reason：没沙箱的常规降级没有 degrade_reason，
        # 也不能汇报成「通过」（模块 docstring 第 26 行早就承诺会明写）。
        verdict = "通过" if report.get("mode") == "real-import" else "降级通过"
    elif code == 1:
        verdict = "发现问题 %d 处" % len(report.get("problems", []))
    elif report.get("mode") == "blocked":
        verdict = "环境阻断：这一趟没核过，不算通过"
    else:
        verdict = "用法或环境错误"
    print("结论：%s" % verdict)


def _self_test():
    """自检在临时目录里造好例与坏例插件，逐条核退出码。"""
    bad, n = [], 0
    root = tempfile.mkdtemp(prefix="dryload-self-")

    def pkg(name, toml, files):
        d = os.path.join(root, name)
        # 同名夹具会复用目录：先清空，否则上一例残留的 .py 会替本例「补齐」缺失的导入
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
        io.open(os.path.join(d, "plugin.toml"), "w", encoding="utf-8", newline="\n").write(toml)
        for rel, body in files.items():
            p = os.path.join(d, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w", encoding="utf-8", newline="\n").write(body)
        return d

    good_toml = ('[plugin]\nid = "demo_plugin"\nversion = "1.0.0"\n'
                 'entry = "plugin.plugins.demo_plugin:DemoPlugin"\n'
                 '\n[plugin_runtime]\nauto_start = true\n')
    good_src = ("from plugin.sdk.plugin import NekoPluginBase, llm_tool, neko_plugin, plugin_entry\n\n\n"
                "@neko_plugin\nclass DemoPlugin(NekoPluginBase):\n"
                "    async def on_load(self):\n        return 1\n\n"
                "    @llm_tool(name=\"t\")\n    async def t(self):\n        return 2\n")
    # 默认夹具用上游真能装的包写法：入口类放在插件目录的 __init__.py 里。
    # 上一版用 main:DemoPlugin，那是宿主两种模式都装不进的写法（见 check_manifest 那条），
    # 夹具跟着判据改，不然「好例子」只在降级腿下才是好的。
    # A791 第 1/3 条各钉一条：段头带注释、缩进段头、值行里的 # 不算注释
    hdr_id = toml_section_value("[plugin]" + chr(32) + " # 说明" + chr(10)
                                + 'id = "demo_plugin"' + chr(10), "plugin", "id")
    n += 1
    if hdr_id != "demo_plugin":
        bad.append("段头带行尾注释时没认到 id，实得 %r" % (hdr_id,))
    n += 1
    if not toml_has_section(chr(10) + "  [plugin.host]" + chr(32) + " # c" + chr(10),
                            "plugin.host"):
        bad.append("缩进且带注释的段头没认到（toml_has_section）")
    n += 1
    val_hash = toml_section_value("[plugin]" + chr(10) + 'id = "a#b"' + chr(10),
                                  "plugin", "id")
    if val_hash != "a#b":
        bad.append("值行里的 # 被当注释剥掉了，实得 %r" % (val_hash,))
    cases = [
        ("好例子（降级模式）", dict(toml=good_toml, files={"__init__.py": good_src}), 0),
        ("目录名不等于 id", dict(name="other_dir", toml=good_toml, files={"__init__.py": good_src}), 1),
        ("没写 auto_start", dict(toml=('[plugin]\nid = "demo_plugin"\n'
                                       'entry = "plugin.plugins.demo_plugin:DemoPlugin"\n'),
                                 files={"__init__.py": good_src}), 1),
        ("写了已移除的 [plugin.host]",
         dict(toml=good_toml + "\n[plugin.host]\nfoo = 1\n", files={"__init__.py": good_src}), 1),
        ("type 是历史值 extension",
         dict(toml=good_toml.replace('version = "1.0.0"', 'version = "1.0.0"\ntype = "extension"'),
              files={"__init__.py": good_src}), 1),
        ("入口符号不在顶层", dict(toml=good_toml, files={"__init__.py": "class Nope:\n    pass\n"}), 1),
        ("入口模块文件缺失", dict(toml=good_toml, files={"other.py": good_src}), 1),
        ("语法错误", dict(toml=good_toml, files={"__init__.py": "def broken(:\n"}), 1),
        ("入口函数不是 async",
         dict(toml=good_toml.replace("DemoPlugin", "demo_entry"),
              files={"__init__.py": ("from plugin.sdk.plugin import plugin_entry\n\n\n"
                                     "@plugin_entry(id=\"demo\")\ndef demo_entry():\n    return 1\n")}), 1),
        ("方法带 @llm_tool 却不是 async",
         dict(toml=good_toml, files={"__init__.py": good_src.replace("    async def t(self)",
                                                                     "    def t(self)")}), 1),
        ("相对导入指向不存在的模块",
         dict(toml=good_toml, files={"__init__.py": good_src + "\nfrom . import ghost\n"}), 1),
        ("entry 形状不对", dict(toml=good_toml.replace("plugin.plugins.demo_plugin:DemoPlugin",
                                                      "plugin.plugins.demo_plugin.DemoPlugin"),
                                files={"__init__.py": good_src}), 1),
        # 裸模块名：schema 的正则放得下，宿主两种装载模式都装不进（2026-11-17 实测）
        ("裸模块名写法上游装不进",
         dict(toml=good_toml.replace("plugin.plugins.demo_plugin:DemoPlugin", "main:DemoPlugin"),
              files={"main.py": good_src, "__init__.py": good_src}), 1),
        # 内置件与 CLI 构建件的写法：plugin.plugins.<目录名>:符号
        ("包写法合法（plugins.<id>，安装态归一化后的前缀）",
         dict(toml=good_toml.replace("plugin.plugins.demo_plugin:DemoPlugin",
                                     "plugins.demo_plugin:DemoPlugin"),
              files={"__init__.py": good_src}), 0),
        # 包名与目录名的等价是单向的（上游 entry_points.py:47-62，2026-11-18 对 main 实读）：
        # 目录可以 kebab、包必须 snake；反过来不成立
        ("目录名 kebab 与包名 snake 等价（单向）",
         dict(name="my-cool-plugin",
              toml=good_toml.replace('id = "demo_plugin"', 'id = "my_cool_plugin"')
                           .replace("plugin.plugins.demo_plugin", "plugin.plugins.my_cool_plugin"),
              files={"__init__.py": good_src}), 0),
        ("包名写成 kebab 必须失败",
         dict(name="my_cool_plugin",
              toml=good_toml.replace('id = "demo_plugin"', 'id = "my_cool_plugin"')
                           .replace("plugin.plugins.demo_plugin", "plugin.plugins.my-cool-plugin"),
              files={"__init__.py": good_src}), 1),
        ("包写法与目录名对不上",
         dict(toml=good_toml.replace("plugin.plugins.demo_plugin:DemoPlugin",
                                     "plugin.plugins.other_dir:DemoPlugin"),
              files={"__init__.py": good_src}), 1),
        ("包写法指到子模块",
         dict(toml=good_toml.replace("plugin.plugins.demo_plugin:DemoPlugin",
                                     "plugin.plugins.demo_plugin.entry:DemoPlugin"),
              files={"__init__.py": "", "entry.py": good_src}), 0),
    ]
    for i, case in enumerate(cases):
        name, spec, want = case
        # 夹具目录名默认等于 id：否则「目录名≠id」那条判据会把每个用例都绊倒，
        # 报出来的失败就不是本来的原因了。真要测目录名的用例自己带 name。
        d = pkg(spec.get("name") or "demo_plugin", spec["toml"], spec["files"])
        code, _rep = run(d, sandbox_root=None)
        n += 1
        if code != want:
            bad.append("%s：退出码 %s，期望 %s（问题=%s）"
                       % (name, code, want, (_rep.get("problems") or ["无"])[:2]))
    # 包名-目录名等价的方向性要单独钉住：上面两条端到端用例分不清是形状拦的还是等价拦的
    n += 1
    if (plugin_package_matches_directory("my_cool_plugin", "my-cool-plugin"),
            plugin_package_matches_directory("my-cool-plugin", "my_cool_plugin"),
            plugin_package_matches_directory("demo_plugin", "demo_plugin"),
            plugin_package_matches_directory("other_dir", "demo_plugin")) != (True, False, True, False):
        bad.append("包名与目录名的等价不是单向的（上游 entry_points.py:47-62 只允许"
                   "目录 kebab ↔ 包 snake 这一个方向）")
    # 目录不存在必须是用法错误 2
    code, _rep = run(os.path.join(root, "nope-dir"))
    n += 1
    if code != 2:
        bad.append("插件目录不存在应退 2，实得 %s" % code)
    # 降级模式必须在输出里明说
    d = pkg("marker", good_toml, {"main.py": good_src})
    code, rep = run(d)
    n += 1
    if REQUIRED_MARK not in (rep.get("note") or ""):
        bad.append("降级模式没写明「证明不了能 import」")
    n += 1
    if rep.get("mode") != "ast-fallback":
        bad.append("没有沙箱时模式应是 ast-fallback，实得 %s" % rep.get("mode"))
    # A791 第 2 条：降级时结论行必须写「降级通过」，不许写「通过」。
    # 键取 mode，不取 degrade_reason：没沙箱的常规降级没有 degrade_reason，也一样不许冒充真装载过。
    buf = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(buf):
        _print(rep, 0)
    n += 1
    if "降级通过" not in buf.getvalue():
        bad.append("降级模式的结论行仍写「通过」，实得 %r" % (buf.getvalue()[-90:],))
    # 真装载那条腿：造一个假 SDK 包与一个入口能 import 的插件，用当前解释器跑
    fake = tempfile.mkdtemp(prefix="dryload-real-")
    try:
        sdk = os.path.join(fake, "plugin", "sdk", "plugin")
        os.makedirs(sdk, exist_ok=True)
        io.open(os.path.join(fake, "plugin", "__init__.py"), "w", encoding="utf-8").write("")
        io.open(os.path.join(fake, "plugin", "sdk", "__init__.py"), "w", encoding="utf-8").write("")
        io.open(os.path.join(sdk, "__init__.py"), "w", encoding="utf-8", newline=chr(10)).write(
            "class NekoPluginBase(object):\n    pass\n\n\n"
            "def plugin_entry(*a, **k):\n    return lambda c: c\n\n\n"
            "def llm_tool(*a, **k):\n    return lambda f: f\n\n\n"
            "def neko_plugin(cls=None, **k):\n"
            "    if cls is None:\n"
            "        return lambda c: c\n"
            "    return cls\n")
        core = os.path.join(fake, "plugin", "core")
        os.makedirs(core, exist_ok=True)
        io.open(os.path.join(core, "__init__.py"), "w", encoding="utf-8", newline=chr(10)).write("")
        io.open(os.path.join(core, "host.py"), "w", encoding="utf-8", newline=chr(10)).write(
            "import importlib.util, os, sys\n\n\n"
            "def _import_plugin_module(module_path, config_path, logger, *, source_only=False):\n"
            "    # 与上游同签名、同规矩（host.py:649、656-683、557-582）：\n"
            "    # 包写法要拆掉 plugin. 前缀，plugins. 后第一段必须等于插件目录名，\n"
            "    # 对得上就从插件目录的 __init__.py 载入。裸模块名在这种规矩下必然失败。\n"
            "    parts = [x for x in str(module_path).split('.') if x]\n"
            "    if parts[:1] == ['plugin']:\n"
            "        parts = parts[1:]\n"
            "    directory = os.path.dirname(str(config_path))\n"
            "    if len(parts) < 2 or parts[0] != 'plugins' or parts[1] != os.path.basename(directory):\n"
            "        if source_only:\n"
            "            raise ValueError('Source-only plugin module must match its source directory')\n"
            "        import importlib as _il\n"
            "        return _il.import_module(module_path)\n"
            "    name = 'forge_probe_' + parts[1]\n"
            "    spec = importlib.util.spec_from_file_location(\n"
            "        name, os.path.join(directory, '__init__.py'),\n"
            "        submodule_search_locations=[directory])\n"
            "    if spec is None or spec.loader is None:\n"
            "        raise ImportError('no spec for ' + name)\n"
            "    mod = importlib.util.module_from_spec(spec)\n"
            "    sys.modules[name] = mod\n"
            "    spec.loader.exec_module(mod)\n"
            "    return mod\n")
        rpkg = os.path.join(fake, "pkgs", "demo_plugin")
        os.makedirs(rpkg, exist_ok=True)
        io.open(os.path.join(rpkg, "plugin.toml"), "w", encoding="utf-8", newline=chr(10)).write(
            good_toml)
        io.open(os.path.join(rpkg, "__init__.py"), "w", encoding="utf-8",
                newline=chr(10)).write(good_src)
        code, rep_ = run(rpkg, py=sys.executable, repo=fake)
        n += 1
        if rep_.get("mode") != "real-import":
            bad.append("真装载那条腿没跑到：mode=%s，降级原因=%s"
                       % (rep_.get("mode"), rep_.get("degrade_reason")))
        elif code != 0:
            bad.append("真装载应通过却退 %s：%s" % (code, (rep_.get("problems") or [])[:2]))
        mpath = os.path.join(rpkg, "__init__.py")

        def _write_main(lines):
            io.open(mpath, "w", encoding="utf-8", newline=chr(10)).write(
                chr(10).join(lines) + chr(10))

        # 噪音走 stderr：stdout 与 stderr 拼起来时噪音常压在结果行后面（内置 web_search 实测）。
        # 「取最后一行当结果」在这里就会把一次成功读成失败，所以结果行按 FORGE- 前缀认。
        _write_main(['import sys as _s', "print('Creating app directory: 插件自己的噪音', file=_s.stderr)", '', 'from plugin.sdk.plugin import NekoPluginBase, neko_plugin, llm_tool', '', '', '@neko_plugin', 'class DemoPlugin(NekoPluginBase):', '    async def on_load(self):', '        return 1', '', "    @llm_tool(name='t')", '    async def t(self):', '        return 2'])
        code2, rep2 = run(rpkg, py=sys.executable, repo=fake)
        n += 1
        if code2 != 0 or rep2.get("mode") != "real-import":
            bad.append("插件往 stderr 打噪音时判错：rc=%s mode=%s 问题=%s"
                       % (code2, rep2.get("mode"), (rep2.get("problems") or [])[:2]))

        # AST 层干净、宿主 import 期抛异常：这一类只有真装载抓得到
        _write_main(['from plugin.sdk.plugin import NekoPluginBase, neko_plugin', '', '', '@neko_plugin', 'class DemoPlugin(NekoPluginBase):', '    async def on_load(self):', '        return 1', '', "raise RuntimeError('import 期就炸')"])
        code3, rep3 = run(rpkg, py=sys.executable, repo=fake)
        n += 1
        if code3 == 0 or rep3.get("mode") != "real-import":
            bad.append("import 期抛异常的插件没被真装载抓住：rc=%s mode=%s 问题=%s"
                       % (code3, rep3.get("mode"), (rep3.get("problems") or [])[:2]))

        # 入口符号改名：AST 层就该抓住，不必等真装载
        _write_main(['from plugin.sdk.plugin import NekoPluginBase, neko_plugin', '', '', '@neko_plugin', 'class RenamedAway(NekoPluginBase):', '    async def on_load(self):', '        return 1'])
        code4, rep4 = run(rpkg, py=sys.executable, repo=fake)
        n += 1
        if code4 == 0:
            bad.append("入口符号改名后仍退 0：%s" % (rep4.get("problems") or [])[:2])
        # _isolated_env 自己的两条：凭据形状要删掉，运行变量要留下
        keep_names = ("PATH", "SYSTEMROOT", "SSH_AUTH_SOCK")
        plant = "ZZ_DRYLOAD_SELFTEST_TOKEN"
        saved = {k: os.environ.get(k) for k in keep_names + (plant,)}
        try:
            os.environ[plant] = "Sup3rS3cretValue"
            os.environ.setdefault("SYSTEMROOT", "C:" + chr(92) + "Windows")
            os.environ.setdefault("SSH_AUTH_SOCK", "/tmp/ssh-x")
            env = _isolated_env(tempfile.mkdtemp(prefix="dryload-env-"))
            n += 1
            if plant in env:
                bad.append("_isolated_env 没删掉凭据形状变量：" + plant)
            n += 1
            for k in ("PATH", "SSH_AUTH_SOCK"):
                if k in saved and k not in env:
                    bad.append("_isolated_env 把该留的运行变量删了：" + k)
            n += 1
            if env.get("LOCALAPPDATA") == os.environ.get("LOCALAPPDATA"):
                bad.append("_isolated_env 没重定向 LOCALAPPDATA：会读到用户真实数据根")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    finally:
        shutil.rmtree(fake, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)
    for m in bad:
        print("SELF-TEST FAIL:", m)
    print("SELF-TEST:", "PASS（%d 例）" % n if not bad else "FAIL（%d/%d 没通过）" % (len(bad), n))
    return 0 if not bad else 1


def _fixtures():
    """仓内样例插件目录：<仓>/tests/fixtures/plugins/{demo_ok,demo_broken}。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(root, "tests", "fixtures", "plugins"), root


def _demo_check(as_json=False):
    good_dir, broken_dir = None, None
    base, _repo = _fixtures()
    good_dir = os.path.join(base, "demo_ok")
    broken_dir = os.path.join(base, "demo_broken")
    reports, codes = [], []
    for label, path in (("好样例 demo_ok", good_dir), ("坏样例 demo_broken", broken_dir)):
        if not os.path.isdir(path):
            print("SELF-TEST FAIL: 夹具缺失 %s（%s）" % (label, path))
            return 1
        code, rep_ = run(path)
        codes.append(code)
        reports.append((label, code, rep_))
        if not as_json:
            print("--- %s：%s" % (label, path))
            _print(rep_, code)
    ok = codes[0] == 0 and codes[1] == 1
    if as_json:
        print(json.dumps({"codes": codes, "ok": ok,
                          "problems": [r[2].get("problems") for r in reports]},
                         ensure_ascii=False, indent=1))
    return 0 if ok else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        # stderr 也要转。fail-open 的警告就印在 stderr：Windows 上子进程默认用本机代码页（GBK），
        # 上层按 UTF-8 解码会整段丢掉，看起来就像「警告没出声」（反证 34 实测踩过）。
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--self-test" in (argv or sys.argv[1:]):
        return _self_test()
    ap = argparse.ArgumentParser(description="N.E.K.O 插件干装载检查器")
    ap.add_argument("plugin_dir", nargs="?", help="插件目录（含 plugin.toml）")
    ap.add_argument("--root", help="沙箱根（给了才有真装载模式）")
    ap.add_argument("--python", dest="py", help="真装载用的解释器（覆盖沙箱 venv 探测）")
    ap.add_argument("--neko-repo", dest="repo", help="官方源码目录（覆盖沙箱 config）")
    ap.add_argument("--demo-check", action="store_true",
                    help="跑仓内样例插件：好样例必须退 0，坏样例必须退 1（CI 用）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.demo_check:
        return _demo_check(bool(a.json))
    if not a.plugin_dir:
        print("用法错误: 要给插件目录")
        return 2
    code, report = run(os.path.abspath(a.plugin_dir), a.root, py=a.py, repo=a.repo)
    if a.json:
        print(json.dumps(dict(report, exit_code=code), ensure_ascii=False, indent=1))
    elif not a.quiet:
        _print(report, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
