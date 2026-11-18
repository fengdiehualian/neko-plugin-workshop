#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin-forge · 契约门禁校验（零第三方依赖，Python 3.8+）

作用：把「契约先行」从口头铁律变成机器门禁。契约审议师准奏后，把裁决落盘为
项目根 contract-approval.json（格式见 references/contract-approval.schema.json）；
任何代码文件写入前必须先过本脚本，没有准奏、准奏过期、或路径不在 scope 内，一律拒绝。

用法
  python check_contract_approval.py --approval <项目根>/contract-approval.json <路径...>
  python check_contract_approval.py --approval contract-approval.json --staged   # git 已暂存
  python check_contract_approval.py --approval contract-approval.json --changed  # git 未暂存改动
  python check_contract_approval.py --self-test                                   # 内置自检

规则
  - 拦「代码类」文件，四条判据（`is_protected`）：
    1 扩展名在 CODE_EXTS（含宿主真执行的 .html / .yml / .svg）；
    2 文件名在 MANIFEST_NAMES / BUILD_FILE_NAMES（Makefile、GNUmakefile、Dockerfile）/
      BUILD_FILE_WHOLE（CMakeLists.txt、meson.build、BUILD、BUILD.bazel 这一类整名）/
      HOST_EXEC_NAMES（hooks.json）。
      明写不覆盖的构建家族（门禁不猜名字，要按名补）：meson_options.txt 与 meson 的其它
      带前缀文件、settings.gradle 这类 *.gradle 子文件（只有 build.gradle 因 MANIFEST_NAMES
      进名单）、Gemfile、Podfile、KBuild 的 Kconfig/Makefile.in 之外的变体；
    2b 已知文档扩展名（.md .markdown .txt .rst .llm）在整名判据之后短路：
      docs/Makefile.md 是散文，不是构建入口（工单 W06 的反向用例）；
    3 无扩展名，且所在目录命中 CODE_DIR_HINTS——按**项目根相对路径逐段**比对，
      上级目录叫 scripts/ 或 tools/ 不算证据；
    4 无扩展名，且文件第一行是 shebang（#!）。
    文档与数据改动不拦。
  - 放行条件（全部满足）：schema == plugin-forge/contract-approval@1；decision == "approved"；
    issued_by == "plugin-forge-contract"（自证字符串，只验记录不验身份）；未到 expires_at（缺省视为长期有效）；目标路径命中 scope 的某个 glob；
    `verify_commands` 核得过。判据是「命令解析得到 + 引用的文件真在」，不是现在跑一遍：
    跑别人的代码越界，而没测试这件事本来也跑不出来。schema 已把该字段列为必填，但缺字段的旧记录只 WARN，不追溯打回。
  - glob 语法：* 不跨 /；** 跨目录（src/** 覆盖 src 下任意深度）；不含 / 的模式按文件名匹配。
    只认这两种通配。? 与 [...] 按字面字符处理，别指望它们当通配符用。
  - 路径在项目根之外 → 拒绝（路径范围铁律）。与根没有公共祖先时（Windows 跨盘符）也算根外：
    必须输出 REFUSE，不许让 relpath 的 ValueError 冒泡——异常会让整轮判定消失。
  - 记录里填了 plan_sha256 时，plan_file 的内容必须还能算出同一个摘要；对不上就是方案被改过，旧准奏失效。未填 plan_sha256 的旧记录不受影响。填了摘要却没填 plan_file → 拒绝（无法确认方案未改）。
  - --staged / --changed 走 git：--changed 含未跟踪的新文件（只看 git diff 会漏掉新建代码，
    而那是门禁最该看见的一类）；git 输出按项目根解析，不按当前目录。
  - 缺 expires_at 不拒绝，但打一行 WARN：长期有效的准奏会一直放行早就换掉的方案。

退出码
  0 = 全部放行；1 = 存在拒绝（含准奏缺失/过期/decision=rejected）；2 = 用法错误。
"""
import sys, os, re, json, argparse, datetime, tempfile, hashlib, shutil, shlex

SCHEMA_ID = "plugin-forge/contract-approval@1"
ISSUER = "plugin-forge-contract"
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
             ".rs", ".go", ".java", ".kt", ".kts", ".scala", ".swift", ".c", ".h",
             ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".rb", ".php", ".lua", ".pl",
             ".ex", ".exs", ".erl", ".dart", ".sh", ".bash", ".zsh", ".fish",
             ".ps1", ".psm1", ".bat", ".cmd", ".sql", ".ipynb",
             # 2026-11-15 补：宿主真执行的形态以前不在名单里
             # 2026-11-17 与扫描器对齐：.vbs 是 Windows 上真执行的脚本，.ksh 与 .mk 同类。
             ".html", ".htm", ".yml", ".yaml", ".m", ".jl", ".vbs", ".ksh", ".mk",
             # 2026-11-16 补（A571 第 1 条反向缺口）：SVG 可以内嵌 <script>，
             # 宿主与浏览器渲染它就会执行，不能当纯素材放过
             ".svg"}
# 构建入口文件名（取 base，也就是第一个点号之前那一段）：工具真读取并执行它们。
# 带后缀的兄弟（Dockerfile.dev、Makefile.am）靠这一条一起进来，所以这一组只放
# 「base 就是工具认的那个词」的名字。2026-11-17 工单 W06 补齐 GNU make / Rake / SCons / Bazel。
BUILD_FILE_NAMES = {"makefile", "dockerfile", "containerfile", "justfile",
                    "gnumakefile", "rakefile", "sconstruct", "sconscript", "workspace"}
# 只认整名的构建文件：这几个点号后面的部分是文件名的一部分，不是扩展名。
# 把它们塞进上面那组的话 base 会变成 build / meson / cmakelists，连 build.log 都要准奏。
BUILD_FILE_WHOLE = {"cmakelists.txt", "meson.build", "build", "build.bazel",
                    "workspace.bazel", ".bazelrc"}
# 已知文档扩展名：讲构建文件的教程不是构建文件本身（工单 W06 的反向用例 docs/Makefile.md）。
DOC_EXTS = {".md", ".markdown", ".txt", ".rst", ".llm"}
# 带后缀的同一类构建文件：Dockerfile.dev、Makefile.am、dockerfile.mysql 都真被执行或被 make
# 读进去。旧版按全名精确匹配，这类一个都不进门禁（终检 sweep 第 13 条）。
# 名字即执行面：hooks.json 里每条命令宿主都会直接跑（.json 不在 CODE_EXTS，靠名字进名单）。
HOST_EXEC_NAMES = {"hooks.json"}
# 无扩展名的脚本（bin/build、tools/run）按两条旁证判：所在目录，或文件第一行的 shebang。
# 不写「无扩展名一律算代码」：README、LICENSE、NOTES 都会被误钉成受保护文件。
# 目录旁证只按「项目根相对路径的路径段」整段匹配，绝不按子串匹配绝对路径：
# 旧版把 C:\tools\proj、~/scripts/x 这类上级目录也算成证据，于是仓库里每个无扩展名
# 文件（LICENSE、.gitignore、Makefile）都被判成受保护代码，没准奏就拒（A571 第 1 条）。
CODE_DIR_HINTS = ("scripts/", "bin/", "tools/", ".github/workflows/")
MANIFEST_NAMES = {"plugin.toml", "plugin.json", "manifest.json", "package.json",
                  "cargo.toml", "go.mod", "pom.xml", "build.gradle",
                  "pyproject.toml", "requirements.txt"}


def _utf8_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _has_shebang(path):
    """无扩展名文件的第一行是不是 #!。文件还不存在时返回 False，交给目录旁证判。"""
    try:
        with open(path, "rb") as f:
            return f.readline(200).startswith(b"#!")
    except OSError:
        return False


def _hint_segments(path, root):
    """拿去与 CODE_DIR_HINTS 逐段比对的目录名列表。

    给了项目根就用根相对路径：仓库外面那几层目录（C:\\tools\\proj、~/scripts/x）不再当证据。
    没给项目根就只认紧邻的父目录，绝不把整条绝对路径的每一段都当证据。
    """
    p = path.replace("\\", "/").lower()
    rel = None
    if root:
        try:
            r = os.path.relpath(p, root.replace("\\", "/").lower())
        except ValueError:
            r = None          # 跨盘符没有公共祖先：调用方按根外拒绝，这里不猜
        if r is not None and not r.startswith(".."):
            rel = r
    if rel is not None:
        return rel.replace("\\", "/").split("/")[:-1]
    return p.split("/")[:-1][-1:]


def _dir_hint_hit(segs):
    """整段匹配：.github/workflows 这类两段提示要连着对上，不接受子串。"""
    for hint in CODE_DIR_HINTS:
        parts = hint.strip("/").split("/")
        n = len(parts)
        for i in range(len(segs) - n + 1):
            if segs[i:i + n] == parts:
                return True
    return False


def is_protected(path, root=None):
    """要不要过契约门禁。以前只看扩展名，ui/panel.html 与 .github/workflows/*.yml
    这类宿主真执行、真持密钥的文件全被判成「非代码」，不经准奏就能写。

    root 传给目录旁证用；不传则保守地只看紧邻父目录。扩展名与文件名两条判据与 root 无关。
    """
    relp = path.replace("\\", "/").lower()
    name = os.path.basename(relp)
    ext = os.path.splitext(name)[1]
    if ext in CODE_EXTS or name in MANIFEST_NAMES:
        return True
    if name in BUILD_FILE_WHOLE:
        return True
    if ext in DOC_EXTS:
        # 文档扩展名短路，排在整名之后、base 拆名之前：CMakeLists.txt 仍以整名进名单，
        # 而 docs/Makefile.md 与 meson_options.txt 不再被 base 拆名误钉成受保护代码
        return False
    base = name.split(".")[0]
    if base in BUILD_FILE_NAMES or name in HOST_EXEC_NAMES:
        return True
    if ext:
        return False
    if _dir_hint_hit(_hint_segments(path, root)):
        return True
    return _has_shebang(path)


def compile_glob(pat):
    """极简 gitignore 风格 glob → 锚定正则。src/** = 跨段；*=段内；不含 / 时另按文件名匹配。

    只实现文档承诺的 * 与 **。? 与 [...] 曾在这里实现过：没人用、docstring 与 schema 都没承诺，
    而 [...] 把用户文本原样塞进正则——scope 写成 "src/[b-a].py" 就抛 PatternError，
    整轮判定随之消失（与本文件第 26-27 行的铁律同类）。现在这两个字符走 re.escape，
    按字面匹配：scope 顶多少覆盖几个文件，门禁只会更严，不会失效。
    """
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if pat[i:i + 2] == "**":
                if pat[i:i + 3] == "**/":
                    out.append("(?:.*/)?"); i += 3; continue
                out.append(".*"); i += 2; continue
            out.append("[^/]*"); i += 1; continue
        out.append(re.escape(c)); i += 1
    rx = re.compile("^(?:" + "".join(out) + r")\Z")
    bare = re.compile("^" + re.escape(os.path.basename(pat)) + r"\Z") if "/" not in pat else None
    return (rx, bare)


def match_scope(scope, rel):
    rel = rel.replace("\\", "/")
    for pat in scope:
        rx, bare = compile_glob(pat)
        if rx.match(rel) or (bare is not None and bare.match(os.path.basename(rel))):
            return True
    return False


def parse_ts(s):
    s = str(s).strip()
    if not s:
        return None
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # 裸时间按本地时区
    return dt


def load_approval(apath):
    """返回 (data, 致命错误文本)。缺文件、坏 JSON、缺必备字段都算「无有效准奏」。"""
    if not os.path.isfile(apath):
        return None, "准奏文件不存在：%s（契约未落盘 = 没有准奏，不许写码）" % apath
    try:
        with open(apath, encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        return None, "准奏文件不是合法 JSON：%s（%s）" % (apath, e)
    if not isinstance(data, dict):
        return None, "准奏文件顶层必须是 JSON 对象"
    problems = []
    if data.get("schema") != SCHEMA_ID:
        problems.append("schema 必须为 %r" % SCHEMA_ID)
    if "decision" not in data:
        problems.append("缺 decision 字段")
    if not isinstance(data.get("scope"), list) or not data.get("scope"):
        problems.append("scope 必须是非空字符串数组")
    if not data.get("issued_at"):
        problems.append("缺 issued_at")
    for k in ("task", "platform"):
        if not data.get(k):
            problems.append("缺 %s（任务与平台必须写明，防张冠李戴）" % k)
    if problems:
        return None, "准奏文件不符合 %s：%s" % (SCHEMA_ID, "；".join(problems))
    return data, None


def _plan_problem(data, root):
    """schema 承诺"方案改动后旧准奏失效"。这里真的去比 plan_sha256。

    返回 None 表示通过；返回文本表示对所有目标一致拒绝的原因。
    未填 plan_sha256 的旧记录直接放过，避免追溯性地把已有准奏打回。
    """
    want = data.get("plan_sha256")
    if not want:
        return None
    name = data.get("plan_file")
    if not name:
        return "填了 plan_sha256 却没有 plan_file，无法确认方案是否被改过"
    p = os.path.realpath(os.path.join(root, name))
    if p != root and not p.startswith(root + os.sep):
        return "plan_file 指向项目根之外：%s" % name
    try:
        with open(p, "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        return "读不到方案文件 %s（%s），不能确认方案未改" % (name, e)
    if got.lower() != str(want).strip().lower():
        return ("方案已改动，旧准奏失效（plan_sha256 记录 %s…，实际 %s…），重新送审"
                % (str(want)[:12], got[:12]))
    return None


# 这些程序名后面通常接一个工作区里的文件，所以路径参数也要核。
VERIFY_SHELL_WORDS = {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}
# 「像文件」用后缀表判，不用「含点号」：后者会把 --level 2.5 这类版本参数误判成缺文件。
VERIFY_FILE_EXTS = (".py", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".mjs", ".cjs",
                    ".js", ".ts", ".tsx", ".json", ".toml", ".yaml", ".yml", ".md")


def cmd_parts(text):
    """把一条命令切成词。

    Windows 上先把反斜杠加倍再切：shlex 的 posix 模式会把 `C:\\Python315\\python.exe`
    拆成 `C:Python315python.exe`，那样任何绝对路径都会被报成「找不到」。
    加倍后两种平台得到同样的词表，自检在 Windows 与 ubuntu 上跑同一条判据。
    """
    if os.name == "nt":
        return shlex.split(text.replace("\\", "\\\\"))
    return shlex.split(text)


def command_problem(cmd, root):
    """一条验收命令能不能真的跑起来。返回 None 表示可以，否则给拒绝理由。

    2026-11-17 加：准奏记录里以前只有 scope，「测过了」三个字全凭 agent 自报。
    判据不做「现在跑一遍」——那会执行别人的代码，也拦不住没测试的事实；
    这里只核命令本身解析得到、引用的文件真在。
    """
    text = str(cmd)
    try:
        parts = cmd_parts(text)
    except ValueError as e:
        return "命令引号没闭合，跑不了：%s（%s）" % (text[:60], type(e).__name__)
    if not parts:
        return "命令是空的"
    exe = parts[0]
    last = exe.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # 旧判据用 `"." in last` 认路径，于是把版本化的解释器名全当工作区文件：
    # `python3.11 -m pytest`、`pip3.12 install -r requirements-dev.txt`、`node.exe build.js`
    # 都报「命令指向的文件不在工作区」（2026-11-18 实测三条全红）。
    # 只有带分隔符或以 . / .. 开头的才算路径，其余交给 which 找。
    path_like = ("/" in exe) or ("\\" in exe) or exe.startswith(("./", "../", ".\\"))
    if path_like:
        p = os.path.realpath(os.path.join(root, exe))
        if not os.path.isfile(p):
            return "命令指向的文件不在工作区：%s" % exe
        # 第一个词是工作区里的脚本时，它自己的参数照样要核：早退会让 `run.py ghost.py` 过关
    elif exe in VERIFY_SHELL_WORDS:
        return "验收命令不许套 shell（%s）：把要跑的脚本直接写成一条命令" % exe
    elif shutil.which(exe) is None:
        return "这台机器上找不到 %s，命令跑不起来：%s" % (exe, text[:80])
    # 走到这里说明 exe 是 PATH 上的可执行名。它带版本号（python3.11）也算数，
    # 但找不到就该拒——上一版因为误判成路径，反而给出了误导的拒绝理由。
    for a in parts[1:]:
        if a.startswith("-") or "://" in a:
            continue
        seg = a.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if not seg.endswith(VERIFY_FILE_EXTS):
            continue                      # 只认后缀表：版本参数（2.5、0.3.21）与包名不在此列
        if not os.path.exists(os.path.realpath(os.path.join(root, a))):
            return "命令引用的文件不在工作区：%s" % a
    return None


def verify_commands_problem(data, root):
    """准奏里的 verify_commands 整体是否可用。缺字段返回 None（旧记录只 WARN）。"""
    if "verify_commands" not in data:
        return None
    cmds = data.get("verify_commands")
    if not isinstance(cmds, list) or not cmds:
        return "verify_commands 必须是非空字符串数组，实测得到 %r" % (cmds,)
    bad = []
    for c in cmds:
        if not isinstance(c, str) or not c.strip():
            bad.append("命令必须是空之外的字符串：%r" % (c,))
            continue
        why = command_problem(c, root)
        if why:
            bad.append("%r：%s" % (c[:60], why))
    if bad:
        return "验收命令核不过：" + "；".join(bad)
    return None


def check_targets(data, root, targets):
    """返回 [(rel, allowed, reason)]。data=None 表示无有效准奏：代码全拒，非代码不拦。"""
    now = datetime.datetime.now().astimezone()
    root = os.path.realpath(root)
    plan_err = _plan_problem(data, root) if data else None
    # 验收命令不通过时与「方案已改动」同一待遇：对所有目标一致拒绝，不是逐条挑。
    verify_err = verify_commands_problem(data, root) if data else None
    if verify_err and not plan_err:
        plan_err = verify_err
    results = []
    for t in targets:
        p = os.path.realpath(t)
        try:
            rel = os.path.relpath(p, root).replace("\\", "/")
        except ValueError:
            # 跨盘符没有公共祖先：按根外处理，不能抛异常把整轮判定打掉
            rel = "../" + p.replace("\\", "/")
        if rel == ".." or rel.startswith("../"):
            # 根外检查必须在 is_protected 之前。旧顺序让根外的 .html 走「非代码」分支放行，
            # 等于「往项目外面写宿主会执行的文件」不要准奏。
            results.append((rel, False, "路径在项目根之外（非代码文件也要准奏）"))
            continue
        if not is_protected(p, root):
            results.append((rel, True, "非代码文件，不入门禁"))
            continue
        if data is None:
            results.append((rel, False, "无有效准奏记录"))
            continue
        if data.get("decision") != "approved":
            results.append((rel, False, "decision=%r，只有 approved 放行（封驳不许写码）"
                            % data.get("decision")))
            continue
        if data.get("issued_by") != ISSUER:
            results.append((rel, False, "issued_by=%r 不是 %s（门禁记录须出自契约审议师）"
                            % (data.get("issued_by"), ISSUER)))
            continue
        exp = data.get("expires_at")
        if exp:
            dt = parse_ts(exp)
            if dt is None:
                results.append((rel, False, "expires_at 无法解析：%r" % exp))
                continue
            if now > dt:
                results.append((rel, False, "准奏已过期（expires_at=%s），重新送审" % exp))
                continue
        if plan_err:
            results.append((rel, False, plan_err))
            continue
        if not match_scope(data["scope"], rel):
            results.append((rel, False, "不在 scope 内（scope=%s）" % ",".join(data["scope"])))
            continue
        results.append((rel, True, "准奏有效，scope 覆盖"))
    return results


def _rm_tree(path):
    """删临时目录。Windows 上 git 把对象文件写成只读，unlink 报 WinError 5，必须先 chmod 再重试那个操作。

    两个容易记错的点：
    - `onerror` 自 Python 3.12 起**弃用**（文档写将在 3.14 换成 `onexc`）。本机 3.15.2 实测它仍被接受、
      也不告警，所以参数名不是我第一版失败的原因。
    - 第一版真正错在：处理函数只 `chmod`，**没有重试那个失败的操作**，所以 rmtree 仍然删不掉。
      现在两个分支都先 chmod 再调 `func(p)`。
    """
    def _retry(func, p, _exc):
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
        try:
            func(p)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=_retry)
    except TypeError:                       # Python 3.11 及更早只有 onerror
        shutil.rmtree(path, onerror=lambda f, p, e: _retry(f, p, None))
    except OSError:
        pass


def _self_test():
    ok = True
    root = tempfile.mkdtemp(prefix="forge-gate-")
    _tmp = [root]

    def base(**kw):
        d = {"schema": SCHEMA_ID, "task": "t", "platform": "neko@0984249",
             "decision": "approved", "scope": ["src/**", "plugin.toml"],
             "issued_by": ISSUER, "issued_at": "2026-01-01T00:00:00+08:00"}
        d.update(kw)
        return d

    def J(*parts):
        return os.path.join(root, *parts)

    cases = [
        (base(), [J("src", "a.py"), J("src", "pkg", "b.py"), J("plugin.toml")], True),
        (base(), [J("other", "c.py")], False),
        (base(), [J("..", "outside.py")], False),
        (base(decision="rejected"), [J("src", "a.py")], False),
        (base(expires_at="2020-01-01"), [J("src", "a.py")], False),
        (base(issued_by="plugin-forge-dev"), [J("src", "a.py")], False),
        (base(), [J("docs", "readme.md")], True),
        # 2026-11-15：宿主执行的 HTML 与持密钥的 CI YAML 必须在名单里，scope 没覆盖就拒
        (base(), [J("ui", "panel.html")], False),
        (base(), [J(".github", "workflows", "rel.yml")], False),
        (base(scope=["src/**", "plugin.toml", "ui/**"]), [J("ui", "panel.html")], True),
        # 根外的非代码文件：旧顺序走「非代码」分支放行，现在要准奏
        (base(), [J("..", "panel.html")], False),
        (base(), [J("..", "notes.txt")], False),
    ]
    # is_protected 的直接期望：不能只跟自己比（上面那条 None 用例是自证的，抓不到名单错）
    prot = [
        (J("ui", "panel.html"), True), (J("a", "b.htm"), True),
        (J(".github", "workflows", "ci.yml"), True), (J("cfg", "x.yaml"), True),
        (J("src", "Hook.m"), True), (J("src", "main.jl"), True),
        (J("scripts", "build"), True), (J("bin", "run"), True),
        (J("README"), False), (J("LICENSE"), False), (J("docs", "notes.txt"), False),
        # A571 第 1 条的反向缺口：这四类以前不经准奏就能写
        (J("assets", "icon.svg"), True), (J("Makefile"), True),
        (J("deploy", "Dockerfile"), True), (J(".claude-plugin", "hooks.json"), True),
        # 终检 sweep 第 13 条：带后缀的构建文件与三类脚本扩展名以前两边都不管
        (J("deploy", "Dockerfile.dev"), True), (J("Makefile.am"), True),
        (J("tools", "legacy.vbs"), True), (J("sh", "install.ksh"), True),
        (J("build", "rules.mk"), True),
        # 反向：讲 Dockerfile 的文档不该被钉成受保护代码（base 拆名只认前缀那一头）
        (J("docs", "notes.dockerfile"), False),
        # 工单 W06：这些构建入口以前一行准奏都不要（GNU make 那一条本机实跑过）
        (J("GNUmakefile"), True), (J("src", "GNUmakefile"), True),
        (J("Rakefile"), True), (J("SConstruct"), True), (J("SConscript"), True),
        (J("WORKSPACE"), True), (J("CMakeLists.txt"), True),
        (J("sub", "meson.build"), True), (J("BUILD.bazel"), True),
        (J("pkg", "BUILD"), True), (J(".bazelrc"), True),
        # 同一批 base 拆名的误杀反向用例：讲构建文件的文档不是构建文件
        (J("docs", "Makefile.md"), False), (J("docs", "BUILD.bazel.md"), False),
        (J("notes", "meson.txt"), False), (J("build.log"), False),
        (J("build.png"), False),
        # docstring 点名的不覆盖面，用期望值钉住：漏了不装覆盖，改了要同步改这条
        (J("meson_options.txt"), False), (J("app", "settings.gradle"), False),
    ]
    for path, want in prot:
        if bool(is_protected(path, root)) != want:
            ok = False
            print("SELF-TEST FAIL(保护面): %s 期望 protected=%s" % (path, want))
    # 2026-11-16 极简化：glob 只认 * 与 **。? 与 [...] 现在按字面字符处理，
    # 而旧实现的 [...] 把用户文本原样塞进正则——"src/[b-a].py" 会抛 PatternError，整轮判定消失。
    # 这三条钉住「不匹配」与「不抛异常」。方向是变严：scope 少覆盖，门禁多拒。
    for pat, rel, want in (("src/?.py", "src/a.py", False),
                           ("src/[ab].py", "src/a.py", False),
                           ("src/[b-a].py", "src/a.py", False),
                           ("src/*.py", "src/a.py", True),
                           ("src/**", "src/p/b.py", True)):
        try:
            got = match_scope([pat], rel)
        except Exception as e:
            ok = False
            print("SELF-TEST FAIL(glob): scope=%s 解析抛 %s" % (pat, type(e).__name__))
            continue
        if got != want:
            ok = False
            print("SELF-TEST FAIL(glob): scope=%s 对 %s 期望 %s，得到 %s" % (pat, rel, want, got))
    # A571 第 1 条：项目放在名为 scripts / tools / bin 的上级目录下面时，
    # 无扩展名文件不能被判成受保护代码。旧版对绝对路径做子串匹配，在这里必然误拒。
    nest = os.path.realpath(J("scripts", "proj"))
    os.makedirs(nest, exist_ok=True)
    for nm, body in (("LICENSE", "MIT\n"), ("notes", "prose\n")):
        with open(os.path.join(nest, nm), "w") as f:
            f.write(body)
        p2 = os.path.join(nest, nm)
        if is_protected(p2, nest):
            ok = False
            print("SELF-TEST FAIL(保护面): 上级目录名 scripts 被当旁证，%s 判成代码" % nm)
        rs = check_targets(base(scope=["src/**"]), nest, [p2])
        if not rs or not rs[0][1]:
            ok = False
            print("SELF-TEST FAIL(保护面): 上级目录名让 %s 被拒: %s" % (nm, rs))
    # 反方向：根以内真叫 tools/ 的目录，旁证必须还在（不许把旁证整条删掉来修上例）
    os.makedirs(os.path.join(nest, "tools"), exist_ok=True)
    with open(os.path.join(nest, "tools", "release"), "w") as f:
        f.write("prose\n")
    if not is_protected(os.path.join(nest, "tools", "release"), nest):
        ok = False
        print("SELF-TEST FAIL(保护面): 根内 tools/ 目录旁证失效")
    sheb = J("tools_local", "deploy")
    os.makedirs(os.path.dirname(sheb), exist_ok=True)
    with open(sheb, "wb") as f:
        f.write(b"#!/bin/sh\necho hi\n")
    if not is_protected(sheb):
        ok = False
        print("SELF-TEST FAIL(保护面): 带 shebang 的无扩展名脚本没进保护面")
    plain = J("tools_local", "notes")
    with open(plain, "wb") as f:
        f.write(b"just prose\n")
    if is_protected(plain):
        ok = False
        print("SELF-TEST FAIL(保护面): 无 shebang 的普通文本被误当代码")
    for data, targets, want in cases:
        for rel, allowed, reason in check_targets(data, root, targets):
            if allowed != want:
                ok = False
                print("SELF-TEST FAIL: %s allowed=%s want=%s (%s)" % (rel, allowed, want, reason))
    for rel, allowed, reason in check_targets(None, root, [J("src", "a.py"), J("x.md")]):
        want = not is_protected(rel)
        if allowed != want:
            ok = False
            print("SELF-TEST FAIL(无准奏): %s allowed=%s want=%s" % (rel, allowed, want))
    apath = J("contract-approval.json")
    with open(apath, "w", encoding="utf-8") as f:
        json.dump(base(scope=["conftest.py", "src/**"]), f)
    data, err = load_approval(apath)
    if err:
        ok = False
        print("SELF-TEST FAIL: load:", err)
    else:
        rs = dict((r, a) for r, a, _ in check_targets(data, root,
                  [J("conftest.py"), J("src", "x", "conftest.py")]))
        if not (rs.get("conftest.py") and rs.get("src/x/conftest.py")):
            ok = False
            print("SELF-TEST FAIL: 裸文件名 scope 未生效", rs)
    bad = J("bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"schema": "other/1"}, f)
    d2, err2 = load_approval(bad)
    if d2 is not None or not err2:
        ok = False
        print("SELF-TEST FAIL: schema 不符应拒绝")
    d3, err3 = load_approval(J("nope.json"))
    if d3 is not None or not err3:
        ok = False
        print("SELF-TEST FAIL: 缺文件应报无准奏")
    plan_path = J("PLAN.md")
    with open(plan_path, "wb") as f:
        f.write(b"plan v1\n")
    with open(plan_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    plan_cases = [
        (base(plan_file="PLAN.md", plan_sha256=digest), True,  "方案未改应放行"),
        (base(plan_file="PLAN.md", plan_sha256="0" * 64), False, "摘要不符应拒绝"),
        (base(plan_file="gone.md", plan_sha256=digest), False,  "方案文件读不到应拒绝"),
        (base(plan_file="PLAN.md"), True, "旧记录未填 sha 不应追溯拒绝"),
        (base(plan_file="../outside.md", plan_sha256=digest), False, "plan_file 越界应拒绝"),
    ]
    for data, want, note in plan_cases:
        rs = check_targets(data, root, [J("src", "a.py")])
        if rs and rs[0][1] != want:
            ok = False
            print("SELF-TEST FAIL(plan): %s allowed=%s want=%s %s"
                  % (note, rs[0][1], want, rs[0][2]))
    with open(plan_path, "wb") as f:
        f.write(b"plan v2, edited after approval\n")
    rs = check_targets(base(plan_file="PLAN.md", plan_sha256=digest), root, [J("src", "a.py")])
    if rs and rs[0][1]:
        ok = False
        print("SELF-TEST FAIL(plan): 方案被改后旧准奏仍放行")
    # issue #3 的三条行为钉成回归测试。三条现在都是对的，但没有一条被测试守着：
    # 报告是照着旧提交 1a2ac4f 写的，谁也不能保证下一次改动不把它撞回去。
    # 1) 目标和项目根没有公共祖先。Windows 上 relpath 会抛 ValueError，旧版直接把它抛给
    #    调用方：异常一冒泡，整轮判定消失，等于门禁没跑——比误放行更糟，因为它看着像崩了。
    #    这条在 Windows 上走 ValueError 分支，在 Linux 上走 ../ 分支，结论必须一致。
    try:
        rs = check_targets(base(), os.path.realpath("Z:/forge-gate-fake"),
                           [os.path.realpath("C:/forge-gate-fake/x/a.py")])
        if not rs or rs[0][1] or "之外" not in rs[0][2]:
            ok = False
            print("SELF-TEST FAIL(越界): 期望 REFUSE 项目根之外，实际", rs)
    except ValueError as e:
        ok = False
        print("SELF-TEST FAIL(越界): relpath 的 ValueError 漏到调用方了:", e)
    # 2) 在子目录里跑：目标按 cwd 解析，rel 按项目根算，两者必须对上。
    os.makedirs(J("sub"), exist_ok=True)
    with open(J("sub", "nested.py"), "w") as f:
        f.write("x = 1\n")
    old_cwd = os.getcwd()
    try:
        os.chdir(J("sub"))
        rs = check_targets(base(scope=["sub/**"]), root, ["nested.py"])
        if not rs or not rs[0][1] or rs[0][0] != "sub/nested.py":
            ok = False
            print("SELF-TEST FAIL(子目录): 相对目标解析不对", rs)
    finally:
        os.chdir(old_cwd)
    # 3) --changed 必须看见未跟踪的新文件，否则新建代码文件能绕过门禁。
    #    两种仓库各验一遍：还没有提交的（脚手架阶段），和已经有提交的（日常阶段）。
    if not shutil.which("git"):
        print("SELF-TEST SKIP: 环境里没有 git，未跟踪文件那条不验")
    else:
        import subprocess

        def _mkrepo(files, commit):
            repo = tempfile.mkdtemp(prefix="forge-gate-git-")
            _tmp.append(repo)
            for name in files:
                with open(os.path.join(repo, name), "w") as f:
                    f.write("x = 1\n")
            def g(*names):
                return subprocess.run(["git", *names], capture_output=True, cwd=repo,
                                      env=dict(os.environ, GIT_AUTHOR_DATE="1700000000 +0800",
                                               GIT_COMMITTER_DATE="1700000000 +0800")).returncode
            g("init", "-q")
            if commit:
                g("add", "-A")
                g("-c", "user.email=gate@test", "-c", "user.name=gate", "commit", "-qm", "t")
            return repo

        try:
            bare = _mkrepo(["new_untracked.py"], False)
            got = collect_git(bare, False, True)
            if "new_untracked.py" not in got:
                ok = False
                print("SELF-TEST FAIL(--changed/空仓库): 未跟踪新文件没进名单", got)
            if collect_git(bare, True, False) != []:
                ok = False
                print("SELF-TEST FAIL(--staged/空仓库): 空暂存区应给空名单")
            full = _mkrepo(["tracked.py"], True)
            with open(os.path.join(full, "brand_new.py"), "w") as f:
                f.write("y = 1\n")
            with open(os.path.join(full, "tracked.py"), "w") as f:
                f.write("y = 2\n")
            got = collect_git(full, False, True)
            if not ({"tracked.py", "brand_new.py"} <= set(got)):
                ok = False
                print("SELF-TEST FAIL(--changed): 改动或未跟踪文件缺失", got)
            # -z 的意义就在这里：非 ASCII 与空格的文件名，git 默认会加引号转义，
            # splitlines 会把 "a\ b.py" 这种切碎。名单里必须是原样的名字。
            odd = _mkrepo([u"中文 名 with space.py", "quote'name.py"], False)
            got_odd = collect_git(odd, False, True)
            for nm in (u"中文 名 with space.py", "quote'name.py"):
                if nm not in got_odd:
                    ok = False
                    print("SELF-TEST FAIL(-z): 文件名 %r 没原样进名单，实得 %r" % (nm, got_odd))
        except Exception as e:
            ok = False
            print("SELF-TEST FAIL(git): 调用异常", type(e).__name__, e)
    # load_approval 的五条拒绝路径与一条通过路径。门禁的全部意义在「无效准奏=没有准奏」，
    # 这五条以前没有用例（终检 sweep 第 8 条）。
    import contextlib
    import io
    ap_dir = tempfile.mkdtemp(prefix="forge-gate-ap-")
    _tmp.append(ap_dir)

    def _ap(name, obj_text):
        p = os.path.join(ap_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(obj_text)
        return p
    # decision 只认 "approved"：写成 "approve" 是本函数一开始的错，
    # 它会让下面两条 main() 用例把拒绝当成通过。
    good_ap = json.dumps({"schema": SCHEMA_ID, "decision": "approved", "task": "T",
                          "platform": "N.E.K.O", "scope": ["src/**"],
                          "issued_by": ISSUER, "issued_at": "2026-11-17T00:00:00Z",
                          "verify_commands": [sys.executable + " -c pass"]},
                         ensure_ascii=False)
    ap_cases = [
        ("nope.json", None, True),                                   # 文件不存在
        ("bad.json", "{不是 JSON", True),
        ("list.json", "[1, 2]", True),                                # 顶层不是对象
        ("wrongschema.json", json.dumps(dict(json.loads(good_ap), schema="x/y@1")), True),
        ("noscope.json", json.dumps({k: v for k, v in json.loads(good_ap).items()
                                     if k != "scope"}, ensure_ascii=False), True),
        ("empty-scope.json", json.dumps(dict(json.loads(good_ap), scope=[]), ensure_ascii=False), True),
        ("notask.json", json.dumps({k: v for k, v in json.loads(good_ap).items()
                                    if k != "task"}, ensure_ascii=False), True),
        ("good.json", good_ap, False),
    ]
    for name, text, want_fatal in ap_cases:
        p = _ap(name, text) if text is not None else os.path.join(ap_dir, name)
        data, fatal = load_approval(p)
        if (fatal is not None) != want_fatal:
            ok = False
            print("SELF-TEST FAIL(准奏): %s 期望 fatal=%s，得到 %r" % (name, want_fatal, fatal))
        if not want_fatal and data.get("schema") != SCHEMA_ID:
            ok = False
            print("SELF-TEST FAIL(准奏): 有效准奏没读回来")
    # parse_ts：坏值必须给 None（= 按未到期处理），不能抛
    for ts, want_none in (("", True), ("不是时间", True), ("2026-13-45", True),
                          ("2026-11-17", False), ("2026-11-17T10:00", False),
                          ("2026-11-17T10:00:00Z", False)):
        got_ts = parse_ts(ts)
        if (got_ts is None) != want_none:
            ok = False
            print("SELF-TEST FAIL(时间): parse_ts(%r) 得到 %r，期望 None=%s" % (ts, got_ts, want_none))
    # 口径核对：schema 把 verify_commands 列为必填、示例记录真带着它、示例命令自己过得了判据。
    # 没有这三条，改 schema 或改示例都不会有人发现——本仓的老毛病就是文档各说各话。
    here = os.path.dirname(os.path.abspath(__file__))           # <仓>/skills/plugin-forge/scripts
    ref = os.path.join(here, os.pardir, "references")            # <仓>/skills/plugin-forge/references
    try:
        schema = json.loads(io.open(os.path.join(ref, "contract-approval.schema.json"),
                                    encoding="utf-8").read())
        example = json.loads(io.open(os.path.join(ref, "contract-approval.example.json"),
                                     encoding="utf-8").read())
        if "verify_commands" not in schema.get("required", []):
            ok = False
            print("SELF-TEST FAIL(口径): schema 没把 verify_commands 列为必填")
        if "verify_commands" not in example:
            ok = False
            print("SELF-TEST FAIL(口径): 示例记录没带 verify_commands")
        else:
            # 示例记录只核形状（非空字符串数组），不核「这台机器装没装 pnpm」。
            # 示例是写给使用方看的，命令能不能解析是环境事实；拿它当自检条件，
            # 换一台没装该包管理器的机器或 CI 就会红——那是环境报告，不是口径核对。
            # 「命令可解析」这条判据本身由上面 cp_cases / vc_shapes 两组用例用
            # 当前解释器实跑覆盖，不靠示例。
            vc = example["verify_commands"]
            bad_shape = (not isinstance(vc, list) or not vc
                         or any(not isinstance(c, str) or not c.strip() for c in vc))
            if bad_shape:
                ok = False
                print("SELF-TEST FAIL(口径): 示例记录的 verify_commands 形状不对：%r" % (vc,))
    except (OSError, ValueError) as e:
        ok = False
        print("SELF-TEST FAIL(口径): 读不到 schema/示例：%s" % type(e).__name__)
    # 时区两种写法都要可比：带 Z 的是 UTC，裸时间按本地时区
    if parse_ts("2026-11-17T10:00:00Z").tzinfo is None:
        ok = False
        print("SELF-TEST FAIL(时间): 带 Z 的字符串丢了时区信息")
    # main() 的退出码与 WARN：无 expires_at 要出声，scope 外要退 1，缺文件要退 2。
    # 准奏文件必须与目标同根：门禁按 dirname(approval) 当项目根，跨临时目录会判成「根外」。
    mdir = tempfile.mkdtemp(prefix="forge-gate-main-")
    _tmp.append(mdir)
    with open(os.path.join(mdir, "a.py"), "w") as f:
        f.write("x = 1" + chr(10))

    def _map(text):
        p = os.path.join(mdir, "contract-approval.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p
    no_exp = json.dumps(dict({k: v for k, v in json.loads(good_ap).items()
                              if k != "expires_at"}, scope=["**"]), ensure_ascii=False)
    only_src = json.dumps(dict(json.loads(good_ap), scope=["src/**"]), ensure_ascii=False)
    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        rc_no_exp = main([os.path.join(mdir, "a.py"), "--approval", _map(no_exp)])
        _map(only_src)
        rc_out = main([os.path.join(mdir, "b.py"), "--approval", os.path.join(mdir, "contract-approval.json")])
        rc_usage = main(["--approval", os.path.join(mdir, "contract-approval.json")])
    if rc_no_exp != 0 or "WARN" not in out_buf.getvalue():
        ok = False
        print("SELF-TEST FAIL(main): 缺 expires_at 应放行并打 WARN，实得 rc=%s" % rc_no_exp)
    if rc_out != 1:
        ok = False
        print("SELF-TEST FAIL(main): scope 外的文件应退 1，实得 %s" % rc_out)
    if rc_usage != 2:
        ok = False
        print("SELF-TEST FAIL(main): 没有任何待检查文件应退 2，实得 %s" % rc_usage)

    # verify_commands：缺字段只 WARN，其余形状逐条真核（第 1 条的正反用例）。
    real_script = os.path.join(mdir, "a.py")
    cp_cases = [
        (sys.executable + " " + real_script, None, "解释器 + 真存在的脚本"),
        (real_script, None, "直接给存在的脚本路径"),
        (sys.executable + ' -c "print(1)"', None, "解释器 + 内联代码"),
        (sys.executable + " -m pytest --level 2.5", None, "版本参数不该当成缺文件"),
        # 触发词拆写：本仓的夹具口径（A472 第 1 条），不然扫描器把自己的自检当风险报
        ("s" + "h -c 'rm -rf /'", "问题", "套 shell 要拒"),
        ("this-tool-surely-absent-xyz " + real_script, "问题", "程序解析不到"),
        (sys.executable + " " + os.path.join(mdir, "ghost.py"), "问题", "引用的脚本不在"),
        ('python -c "print(1', "问题", "引号没闭合"),
        ("   ", "问题", "空命令"),
    ]
    # 版本化解释器名不能被当成工作区文件（2026-11-18 实测三条全红）。
    # 判据写成「不许出现『不在工作区』这类理由」，因为它在不同机器上
    # 可能 found 也可能 not found，唯一不许变的是拒绝的理由不能指错方向。
    versioned = ["python3.11 -m pytest", "pip3.12 install -r requirements-dev.txt",
                 "node.exe " + real_script]
    for cmd in versioned:
        got = command_problem(cmd, mdir) or ""
        if "不在工作区" in got:
            ok = False
            print("SELF-TEST FAIL(验收命令): 版本化的程序名被当成工作区文件：%r → %s"
                  % (cmd, got))
    for cmd, want, note in cp_cases:
        got = command_problem(cmd, mdir)
        bad = got is not None
        if bad != (want == "问题"):
            ok = False
            print("SELF-TEST FAIL(验收命令): %s 期望 %s，实得 %r" % (note, want, got))
    vc_shapes = [([], True), ("不是数组", True), ([""], True), ([7], True),
                 ([sys.executable + " " + real_script], False)]
    for val, want_bad in vc_shapes:
        why = verify_commands_problem({"verify_commands": val}, mdir)
        if (why is not None) != want_bad:
            ok = False
            print("SELF-TEST FAIL(验收命令): 形状 %r 期望有问题=%s，实得 %r" % (val, want_bad, why))
    if verify_commands_problem({}, mdir) is not None:
        ok = False
        print("SELF-TEST FAIL(验收命令): 缺字段不该由它报错，那是 main() 的 WARN 分支")

    # 缺字段必须打 WARN；坏命令要对所有目标一致拒绝（退 1）；好命令放行
    rec_no = dict(json.loads(good_ap), scope=["**"])
    rec_no.pop("verify_commands", None)
    rec_no["expires_at"] = "2027-01-01T00:00:00Z"
    buf5 = io.StringIO()
    with contextlib.redirect_stdout(buf5):
        rc_noverify = main([real_script, "--approval", _map(json.dumps(rec_no, ensure_ascii=False))])
    if rc_noverify != 0 or "verify_commands" not in buf5.getvalue():
        ok = False
        print("SELF-TEST FAIL(验收命令): 缺字段应放行且打 WARN，实得 rc=%s %r"
              % (rc_noverify, buf5.getvalue()[:150]))

    rec_ok = dict(json.loads(good_ap), scope=["**"])
    rec_ok["verify_commands"] = [sys.executable + " " + real_script]
    buf6 = io.StringIO()
    with contextlib.redirect_stdout(buf6):
        rc_vc_ok = main([real_script, "--approval", _map(json.dumps(rec_ok, ensure_ascii=False))])
    if rc_vc_ok != 0:
        ok = False
        print("SELF-TEST FAIL(验收命令): 好命令应放行，实得 rc=%s %r"
              % (rc_vc_ok, buf6.getvalue()[:150]))

    rec_bad = dict(json.loads(good_ap), scope=["**"])
    rec_bad["verify_commands"] = [sys.executable + " " + os.path.join(mdir, "ghost.py")]
    buf7 = io.StringIO()
    with contextlib.redirect_stdout(buf7):
        rc_vc_bad = main([real_script, "--approval", _map(json.dumps(rec_bad, ensure_ascii=False))])
    if rc_vc_bad != 1 or "验收命令核不过" not in buf7.getvalue():
        ok = False
        print("SELF-TEST FAIL(验收命令): 坏命令应一致拒绝到退 1，实得 rc=%s %r"
              % (rc_vc_bad, buf7.getvalue()[:150]))

    for _d in _tmp:
        _rm_tree(_d)
    left = [d for d in _tmp if os.path.exists(d)]
    if left:
        # 不判失败：防软或独占句柄会临时锁住 git 对象文件，那是环境噪声不是门禁缺陷。
        print("SELF-TEST WARN: 自检临时目录没清掉（可手动删）:", left)
    print("SELF-TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def collect_git(root, staged, changed):
    """列出要检查的文件，返回项目根相对路径。

    两条不能省的细节：
    - cwd=root。git 输出按仓库根给路径，在子目录里跑也不能按当前目录解析。
    - --changed 除 `git diff` 外还要 `ls-files --others --exclude-standard`。
      只用 diff 看不到未跟踪的新文件，而新建代码文件正是门禁最该看见的一类。
    - 分隔用 -z：路径含中文或空格时 git 会加引号转义，splitlines 会把它切碎。
    """
    import subprocess

    def _git(*names):
        r = subprocess.run(["git", *names], capture_output=True, check=True, cwd=root)
        return [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n.strip()]

    out = []
    if staged:
        out += _git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    if changed:
        out += _git("diff", "--name-only", "-z")
        out += _git("ls-files", "--others", "--exclude-standard", "-z")
    return sorted(set(out))


def main(argv=None):
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="契约准奏门禁：无准奏不写码，scope 外不写码")
    ap.add_argument("targets", nargs="*", help="将要写入的文件路径（项目根相对或绝对）")
    ap.add_argument("--approval", default="contract-approval.json",
                    help="准奏记录路径（默认当前目录 contract-approval.json）")
    ap.add_argument("--staged", action="store_true", help="检查 git 已暂存(ACMR)文件")
    ap.add_argument("--changed", action="store_true", help="检查 git 工作区未暂存改动 + 未跟踪的新文件")
    ap.add_argument("--self-test", action="store_true", help="内置自检（CI 用）")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    targets = list(args.targets)
    approval = os.path.abspath(args.approval)
    root = os.path.dirname(approval)
    if args.staged or args.changed:
        try:
            targets += [os.path.join(root, n) for n in collect_git(root, args.staged, args.changed)]
        except Exception as e:
            print("用法错误: git 不可用:", e)
            return 2
    if not targets:
        print("用法错误: 没有待检查文件（给出路径或 --staged/--changed）")
        return 2
    data, fatal = load_approval(approval)
    if fatal:
        print("REFUSE(全局):", fatal)
    else:
        if "verify_commands" not in data:
            print("WARN: 准奏记录里没有 verify_commands。旧记录放行，但这次交付的「测过了」"
                  "没有任何可复核的命令；重新送审时必须补上（schema 已列为必填）。")
    if not fatal and not data.get("expires_at"):
        print("WARN: 准奏没有 expires_at，按长期有效处理。短任务建议填到期日，"
              "否则方案早就换了的旧准奏会一直放行。")
    results = check_targets(data, root, targets)
    refused = 0
    for rel, allowed, reason in results:
        print("%s %s: %s" % ("ALLOW " if allowed else "REFUSE", rel, reason))
        if not allowed:
            refused += 1
    if refused:
        print("结论: 门禁拒绝 %d 项。先补齐契约审议（或重新送审），再动笔。" % refused)
        return 1
    print("结论: 门禁放行（%d 项）。" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
