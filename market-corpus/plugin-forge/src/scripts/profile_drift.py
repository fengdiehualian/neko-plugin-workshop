#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""profile 锚点漂移探针（零第三方依赖，Python 3.8+；网络走 GitHub REST 只读接口，
用 urllib 直连，不依赖 gh——CI 里没有交互式登录也能跑，见 api()）

用途
  profile-neko.md 的锚点写法不统一：`(1050 行，读于 a222e50)`、`（id 212、name 213）`、
  `plugins.py:28`、`runs.py:35` 都有。行号会随上游漂移，而这份 profile 是团队的契约来源。
  本脚本把「路径 + 紧跟该路径的行号」抽出来，对**当前 main**逐条核对：记录的那一行上是否还
  出现该行附近的标识符。不在了，就给出该标识符现在在哪。

用法
  python scripts/profile_drift.py [--profile 路径] [--json] [--strict] [--fresh]
  python scripts/profile_drift.py --self-test

  不带 --profile 时一次核对 PROFILES 里的两份文档（profile-neko.md 与沙箱 SKILL.md）。
  默认只报告：不改文件、不因漂移失败（退出码 0）。--strict 时-blocking，CI 就跑这一条。
  --fresh 忽略缓存重拉。

缓存
  拉到的上游文件放在 scripts/.drift-cache/（已加进 .gitignore），避免一次跑打上百次 API。
  当前 main 的 sha 也存那里，超过 24 小时自动重取，否则「当前 main」这句话站不住。

匿名限额是一小时 60 次。CI 里给这一步传 GITHUB_TOKEN，否则跑不了几条就限流。
限流与断网不算漂移：那些锚点报 unreachable，脚本退 2，不冒充「上游删了文件」的结论。

退出码：0 报告完成；--strict 且有漂移、有锚点缺符号可比对（weak）、或有锚点只写了文件名
（写法核不了）时退 1；有锚点取不到上游时 2（包括根本取不到 sha）。
weak 与「写法核不了」都算失败，因为这两句都是我们自己的写法问题：探针看不见的那一条，
才是行号写错还能过关的那一条（2026-11-17 的外部审阅 A734 就是这么进来的）。
"""
import argparse
import base64
import contextlib
import http.client
import json
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_PROFILE = os.path.join(REPO, "skills", "plugin-forge", "references", "profile-neko.md")
# 门禁覆盖面。沙箱 SKILL.md 的契约表也逐条指向上游，以前它不在任何自动核对里，
# 所以我把 config/network.py 的行号写错都没人报（A734 的 N3）。
PROFILES = [DEFAULT_PROFILE,
            os.path.join(REPO, "skills", "neko-plugin-sandbox", "SKILL.md")]
CACHE_DIR = os.path.join(HERE, ".drift-cache")
HEAD_TTL = 24 * 3600
UPSTREAM = "Project-N-E-K-O/N.E.K.O"
NL = chr(10)

# 前缀白名单。2026-11-17 补 utils / brain：profile 早就在引
# utils/config_manager/storage_roots.py:367-373 这类锚点，白名单不含 utils 时探针直接看不见它，
# 于是「71 条全对」这句话漏掉了真在用的锚点。
# 不要加 scripts/：那是我们自己的仓的路径，上游没有对应文件，加进去会让探针把
# 本文提到的 `scripts/profile_drift.py` 当成上游锚点，报出一条假 404。
PATH_RE = re.compile(r"(?<![\w/.\-])((?:plugin|app|config|docs|nekoclaw|memory|launcher_core|"
                     r"main_logic|\.agent|utils|brain)/[\w./\-]*\.(?:py|md|toml|json|js|ts))")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
# 认这六种形状：NNN 行、NNN-NNN 行（区间也要，旧版因为 lookbehind 挡了连字符，
# 「实测为 105-113 行」整条抽不出来，那一行的锚点完全不在门禁里）、:NNN、（NNN 或（NNN/NNN、
# 带标签的「id 212、name 213」，以及「行号 64、95、182」这种带「行号」标签的顿号列。
# issue 号带 #、章节号带 §，都不会被吃进来。
# 第五种必须跟在短标签后并以顿号或括号收尾，否则「见 4.2 与自查 5、6、8」这种句子编号会被当成行号——
# 第一版探针就在这里报了 8 条假漂移。第六种必须有「行号」两字打头，同一句话里光秃秃的数字不吃。
NUM_PATTERNS = (re.compile(r"(?<![\w.\-])(\d{1,5})(?=\s*行)"),
                re.compile(r"(?<![\w.])(\d{1,5})\s*[-–]\s*(\d{1,5})(?=\s*行)"),
                re.compile(r":(\d{1,5})\s*[-–]\s*(\d{1,5})(?![\d%])"),
                re.compile(r":(\d{1,5})(?![\d%])"),
                re.compile(r"[（(]\s*(\d{1,5}(?:\s*[/、,]\s*\d{1,5})*)"),
                re.compile(r"(?<=[A-Za-z_] )(\d{1,5})(?=[、，,)）])"),
                re.compile(r"行号\s*((?:\d{1,5}\s*[、，,]\s*)*\d{1,5})(?![\d\-–])"))
STOPWORDS = {"plugin", "read", "line", "sha", "fact", "def", "main", "code", "true", "false",
             "none", "str", "int", "bool", "list", "dict", "self", "return", "import", "from",
             "https", "http", "www", "md", "py", "toml", "json", "get", "post", "put", "delete",
             "api", "ctx", "sdk", "app", "pinned", "readme", "guide", "this", "that", "with"}


def classify(status):
    """把 HTTP 状态分成三类，因为它们的含义完全不同。

    missing  = 404，上游真的没有这个文件，这是有效结论（算漂移）。
    limited  = 403 / 429，匿名限额一小时 60 次，这不是结论。
    net      = 其他状态、超时、断连，同样不是结论。
    旧版把三者一律压成 None，限流就被报成「上游取不到该文件」，读起来像上游删了代码。
    """
    if status == 404:
        return "missing"
    if status in (403, 429):
        return "limited"
    return "net"


def api(url):
    """直连 GitHub REST，不依赖 gh：CI 里没有交互式登录也能跑。有 GITHUB_TOKEN 就带上。

    返回 (数据, 故障)。故障取 classify 的三种值，None 表示成功。
    """
    import urllib.error
    import urllib.request  # forge-declared: SEC-4 理由=只读拉取 GitHub API 的 UA 头 出处=AUDIT.md#sec-declared 复审=2026-12-15
    req = urllib.request.Request(url, headers={  # forge-declared: SEC-4 理由=只读拉取 api.github.com 出处=AUDIT.md#sec-declared 复审=2026-12-15
        "Accept": "application/vnd.github+json",
        "User-Agent": "plugin-forge-profile-drift-probe",
    })
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # forge-declared: SEC-4 理由=只读拉取 api.github.com 出处=AUDIT.md#sec-declared 复审=2026-12-15
            return json.loads(r.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, classify(e.code)
    except (ValueError, TimeoutError, OSError, http.client.HTTPException):
        # IncompleteRead 等 http.client 异常既不是 OSError 也不是 ValueError：
        # 读响应体中途被截断时旧版会抛 traceback，退出码 1，被读成「有漂移」（A734 的 O1）。
        return None, "net"


def resolve_head(use_cache=True, max_age=HEAD_TTL, cache_dir=None, get=None):
    """取上游当前 main 的 sha。缓存超过 max_age 秒就重取，否则「当前 main」这句话不成立。

    cache_dir 与 get 可注入：这一段以前只能靠真网络演，缓存命中、过期重取、
    取不到 sha 三条分支在 CI 里一次都没跑过（终检 sweep 第 14 条）。
    """
    import time
    get = get or api
    cache_dir = cache_dir or CACHE_DIR
    mark = os.path.join(cache_dir, "head.txt")
    if use_cache and os.path.isfile(mark) and time.time() - os.path.getmtime(mark) <= max_age:
        with open(mark, encoding="utf-8") as f:
            sha = f.read().strip()
        if sha:
            return sha, True
    meta, err = get("https://api.github.com/repos/" + UPSTREAM)
    branch = (meta or {}).get("default_branch") or "main"
    data, err2 = get("https://api.github.com/repos/%s/commits/%s" % (UPSTREAM, branch))
    sha = (data or {}).get("sha", "")
    if sha:
        os.makedirs(cache_dir, exist_ok=True)
        with open(mark, "w", encoding="utf-8") as f:
            f.write(sha)
        return sha, False
    return "", err2 or err or "net"


def body_state(data):
    """区分「上游没有这个文件」与「文件太大、API 不给正文」。

    contents API 对 1MB–50MB 的文件返 HTTP 200 但不带 content。旧版把它算成 404，
    于是探针会说「该文件已改名或已删除」——一个不存在的产品结论。
    """
    if data is None:
        return "missing"
    return None if data.get("content") else "big"


def api_bytes(url):
    """取原始正文。Contents API 对 1MB 以上的文件不给正文，这类文件只能走 raw 端点。

    2026-11-17 加：CI 里这一步已经改成阻塞，`plugin/settings.py`（1.1MB）以前会算成
    unreachable 并退 2，等于探针天天红却什么都没说。raw 端点没有大小门槛。
    """
    import urllib.error
    import urllib.request  # forge-declared: SEC-4 理由=只读拉取 raw.githubusercontent.com 出处=AUDIT.md#sec-declared 复审=2026-12-15
    req = urllib.request.Request(url, headers={  # forge-declared: SEC-4 理由=只读拉取 raw.githubusercontent.com 出处=AUDIT.md#sec-declared 复审=2026-12-15
        "Accept": "text/plain",
        "User-Agent": "plugin-forge-profile-drift-probe",
    })
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # forge-declared: SEC-4 理由=只读拉取 raw.githubusercontent.com 出处=AUDIT.md#sec-declared 复审=2026-12-15
            return r.read(), None
    except urllib.error.HTTPError as e:
        return None, classify(e.code)
    except (ValueError, TimeoutError, OSError, http.client.HTTPException):
        # IncompleteRead 等 http.client 异常既不是 OSError 也不是 ValueError：
        # 读响应体中途被截断时旧版会抛 traceback，退出码 1，被读成「有漂移」（A734 的 O1）。
        return None, "net"


def upstream_lines(path, head, get=None, get_raw=None, cache_dir=None):
    """返回 (行列表, 故障)。故障非 None 时不能据此说上游没有该文件。

    cache_dir 可注入，自检用它把夹具写进临时目录，不污染仓库外的 .drift-cache。
    """
    get = get or api
    get_raw = get_raw or api_bytes
    cache_dir = cache_dir or CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    fn = os.path.join(cache_dir, "%s.%s.txt" % (head[:8], path.replace("/", "__")))
    if os.path.isfile(fn):
        with open(fn, encoding="utf-8", errors="replace") as f:
            return f.read().split(NL), None
    data, err = get("https://api.github.com/repos/%s/contents/%s?ref=%s" % (UPSTREAM, path, head))
    if err:
        return None, err
    state = body_state(data)
    if state == "big":
        raw, rerr = get_raw("https://raw.githubusercontent.com/%s/%s/%s" % (UPSTREAM, head, path))
        if rerr:
            return None, rerr
        if raw is None:
            return None, "big"
        text = raw.decode("utf-8", "replace")
        with open(fn, "w", encoding="utf-8", newline=NL) as f:
            f.write(text)
        return text.split(NL), None
    if state:
        return None, state
    try:
        text = base64.b64decode(data["content"]).decode("utf-8", "replace")
    except Exception:
        return None, "net"
    with open(fn, "w", encoding="utf-8", newline=NL) as f:
        f.write(text)
    return text.split(NL), None


def where_symbol(lines, token):
    return [i for i, ln in enumerate(lines, 1) if token in ln][:6]


def window_nums(seg):
    """从一段文字里抽行号。每个匹配的所有捕获组都要，第二个模式是「105-113 行」这种区间。

    不拆 "-" 的全局切分：文档里有一句专门列「已失效的旧行号 1055-1150、1693-2049」，
    那种历史数字一旦被当成锚点，探针就会天天报假漂移。
    """
    out = []
    for pat in NUM_PATTERNS:
        for mtch in pat.finditer(seg):
            for group in mtch.groups():
                if group is None:
                    continue
                for piece in re.split(r"[/、,]", group):
                    piece = piece.strip()
                    if piece.isdigit() and 1 <= int(piece) <= 40000:
                        out.append(int(piece))
    return sorted(set(out))


DEFINITION_RE_CACHE = {}


def _definition_pattern(name):
    """名字要以定义的身份出现：def/class/const 等关键字后、`NAME =`，或 `NAME:`。

    2026-11-17 按 A734 的 N4 加。旧判据只问「这名字在不在整个文件里」，注释与字符串里提一句
    也算过，`LLM`、`images`、`_brief` 这类短名几乎必过，那 18 条符号锚的「全对」就等于 grep 命中。
    """
    rx = DEFINITION_RE_CACHE.get(name)
    if rx is None:
        n = re.escape(name)
        rx = re.compile(
            r"(?:^|\s)(?:def|class|function|const|let|var|type|interface|enum)\s+" + n + r"\b"
            + r"|\b" + n + r"\s*=(?!=)"
            + r"|\b" + n + r"\s*:"
            # __all__ 里的导出写法："NAME",
            + r"|[\"']" + n + r"[\"']"
            # markdown 文件里符号就是标题或粗体／行内代码，没有 def 这一说
            + r"|^#{1,6}[^\n]*" + n
            + r"|^\s*(?:\*\*|`)[^\n]*" + n,
            re.M)
        DEFINITION_RE_CACHE[name] = rx
    return rx



def _route_literals(seg):
    """把反引号里的路由字面量去掉，只留下真正的代码符号。

    `GET /plugin/metrics*` 这种写法里 IDENT_RE 会抓到 metrics，而它不是代码符号，是路由名。
    2026-11-17 实测：一行里连写四个 `xxx.py:NN` 时，这种假符号会被安到前一个路径头上，
    探针报「记录行上没有本行任何标识符」的假漂移。带 / 的那段整个摘掉；不带 / 的
    `health()`、`PluginPageMutationGuardedRoute` 这些照旧留下。
    """
    return re.sub(r"`[^`]*?/[^`]*?`", " ", seg)


def _anchor_symbol_span(seg):
    """只认文档自己的两种写法，别的散文一律不看。

    第一版这里用「整段里像代码的词」，结果四条假漂移：文档写 `@llm_tool` 不在该文件、
    写某个类「不叫 PluginManifestSchema」、把 latest_user_request 挂在另一条链上——
    那些都是散文，不是锚点。profile 第 26 行的规矩本来就规定了写法：
    「文件:符号名」。那就只按这个形状取符号。
    """
    m1 = re.match(r"\s*[:：]\s*(?:`([^`]{3,60})`|([A-Za-z_][\w.]{2,60}))", seg)
    if m1:
        raw = m1.group(1) or m1.group(2)
    else:
        m2 = re.match(r"\s*的\s*`([A-Za-z_][\w.]{2,60})`", seg)
        if not m2:
            return []
        raw = m2.group(1)
    out = []
    for piece in re.split(r"[、+，,]", raw):
        piece = piece.strip().strip("`").split("(")[0].split(" ")[0].strip()
        if re.fullmatch(r"[A-Za-z_][\w.]{2,60}", piece or "") and piece.lower() not in STOPWORDS:
            out.append(piece)
    return out[:6]


# 文件名本体。行号在不在、是什么形状，统一交给 window_nums 判——与 collect() 同一张形状表。
# 旧版这里自带 lookahead，只认「:NNN」与「NNN 行」两种紧挨着的形状；裸名用括号区间、顿号列、
# id 标签列时 unresolvable 与 collect 都不看，双向隐身（W10.3）。
LOOSE_RE = re.compile(r"(?<![\w/.\-])([\w.\-/]+\.(?:py|md|toml|json|js|ts))")
LOCAL_NAMES = None
# 上游同名出现 2 份以上的文件名，与本仓文件名求交集（脚本 forge-scratch/wf-out/up_tree.py，
# 抓于 2026-11-18：SKILL.md 36 份、README.md 28 份、plugin.toml 16 份）。这些名字即使撞
# 本仓文件名也不豁免：裸名进不了 collect，行号写错 CI 不响（W10.2）。
# 刷新义务：上游新增歧义名后重跑交集；未刷新前清单只是下限。
AMBIG_UPSTREAM = frozenset({"SKILL.md", "README.md", "plugin.toml", "main.py", "conftest.py"})
# 这些说法与标记只豁免含它的那一个小句（边界见 CLAUSE_SEPS），不再整行静音（W10.1）。
# 「drift-ignore」是给讲解坏写法的例句用的显式标记：反例句不在声明上游行号，
# 门禁不该逼作者删例句（W10.4）。上游锚点照样由 collect() 抽，这里只是不再要求写全目录。
SKIP_MARKS = ("未核验", "未核实", "本机", "drift-ignore")
CLAUSE_SEPS = ("。", "；", ";", "|", "！", "？", "，", ",")


def _local_names():
    """本仓库自己的文件名。文档里 `sandbox.py:120`、`session_capability_refresh.py:88`
    指的是我们自己的脚本，不是上游，探针不该管，也不该报「核不了」。
    """
    global LOCAL_NAMES
    if LOCAL_NAMES is None:
        LOCAL_NAMES = set()
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".drift-cache")]
            LOCAL_NAMES.update(files)
    return LOCAL_NAMES


def unresolvable(text):
    """锚点写法自己把门禁挡住了：行号前面只写了文件名或半截目录。

    2026-11-17 A734 的 N3。上游有 5 个 messages.py、6 个 query_service.py，`messages.py:22`
    这种写法探针不可能猜对，以前 PATH_RE 匹配不到就整条跳过——上一轮 `config/network.py`
    的行号被我改成 194 就是这么溜过去的。这里不猜映射表（猜错会报「对得上」，比不检更糟），
    只把写法报出来，要人把目录写全。
    2026-11-18 W10 四条一起改：①豁免按小句生效，不按整行——一行表格里「本机」不再静音
    同行另一句的上游裸名锚点；②AMBIG_UPSTREAM 里的名字即使撞本仓文件名也照报，指认本仓
    文件请写仓内相对路径，路径存在即豁免；③有没有行号统一问 window_nums，与 collect()
    共用一张形状表；④讲解坏写法的例句在句内写 drift-ignore。
    """
    out = []
    for no, line in enumerate(text.split(NL), 1):
        # 反引号先抹平：行内代码写法 `cli.py` 105-113 行 里，那个反引号正好挡在
        # 文件名与数字之间，不抹就看不见这一处锚点。路径串本身不含反引号，抹掉不改变取值。
        plain = line.replace("`", " ")
        full = set(m.group(1) for m in PATH_RE.finditer(plain))
        for m in LOOSE_RE.finditer(plain):
            p = m.group(1)
            base = p.rsplit("/", 1)[-1]
            # 白名单全路径、仓内相对路径（必须带目录）、以 / ~ . 开头的本机路径都不归这里管。
            if (p in full or p.startswith(("/", "~", "."))
                    or ("/" in p and os.path.isfile(os.path.join(REPO, *p.split("/"))))
                    or (base in _local_names() and base not in AMBIG_UPSTREAM)):
                continue
            # 形状对齐：文件名之后、下一个文件名之前，凡 window_nums 认的行号都算锚点，
            # 与 collect() 判全路径时用的是同一个函数、同一张形状表。
            nxt = LOOSE_RE.search(plain, m.end())
            if not window_nums(plain[m.end():nxt.start()] if nxt else plain[m.end():]):
                continue
            # 小句级豁免：标记词只管含它那一句，不再静音同行其他句。
            cut = max([plain.rfind(s, 0, m.start()) for s in CLAUSE_SEPS] + [-1])
            nxtb = [i for i in (plain.find(s, m.end()) for s in CLAUSE_SEPS) if i >= 0]
            if any(k in plain[cut + 1:min(nxtb + [len(plain)])] for k in SKIP_MARKS):
                continue
            out.append({"line": no, "path": p, "text": line.strip()[:120]})
    return out


PAIR_LOOKBACK = 46
PAIR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]{3,})[^A-Za-z0-9_]{0,12}?(?=\d)")


def _pair_nums(seg, nums):
    """给每个行号找紧邻它的标识符，找不到就不配（判据回退到整条的 token 列表）。

    取法：在段内定位这个数字，往回看 PAIR_LOOKBACK 个字符，取最后一个像标识符的词。
    `（def 156）` → def 是停用词，跳过；往前拿到 timer_interval。
    """
    out = {}
    for n in nums:
        idx = seg.find(":%d" % n)
        if idx < 0:
            idx = seg.find(str(n))
        if idx < 0:
            continue
        back = seg[:idx][-PAIR_LOOKBACK:]
        cands = [w for w in IDENT_RE.findall(back)
                 if w.lower() not in STOPWORDS and not re.fullmatch(r"[0-9a-fA-F]{7,}", w)]
        if cands:
            out[n] = cands[-1]
    return out


def collect(text):
    """每个路径只看它自己之后、下一个路径之前的那段文字。

    段内没有标识符时不要退回整行。整行里有路径本身，`manifest.ts:129` 会抽出
    `manifest`，而上游正文里几乎到处都写着 manifest。那种锚点自己命中自己，
    代码搬走了也报「对得上」。这类锚点标 weak，交给 check 单独报「核不了」。
    """
    anchors = []
    for no, line in enumerate(text.split(NL), 1):
        found = list(PATH_RE.finditer(line))
        if not found:
            continue
        for i, m in enumerate(found):
            stop = found[i + 1].start() if i + 1 < len(found) else len(line)
            seg = line[m.end():stop]
            nums = window_nums(seg)
            if not nums:
                # 2026-11-17 补：路径后面一个行号都没有的那些锚点。profile 第 26 行的规矩是
                # 「每条能力面结论带 文件:符号名」，所以这类写法是故意的，可探针以前一条都不抽，
                # 于是约六十处声明完全在门禁之外。现在按符号存在性核：符号还在不在那个文件里。
                syms = _anchor_symbol_span(seg)
                if not syms:
                    continue
                anchors.append({"line": no, "path": m.group(1), "nums": [],
                                "tokens": [], "weak": False, "syms": list(dict.fromkeys(syms))[:6],
                                "symbol_only": True,
                                "text": line.strip()[:120]})
                continue
            toks = [x for x in IDENT_RE.findall(_route_literals(seg))
                    if x.lower() not in STOPWORDS and not re.fullmatch(r"[0-9a-fA-F]{7,}", x)]
            nums = nums[:10]
            uniq_toks = list(dict.fromkeys(toks))
            anchors.append({"line": no, "path": m.group(1), "nums": nums,
                            "tokens": uniq_toks[:6],
                            # 每个行号配一个紧邻它的标识符：`timer_interval（def 156）` 里
                            # 156 就该拿 timer_interval 核，而不是拿同一段里排在前面的别的符号。
                            # 以前整条锚点共用一个 token 列表，一条写 8 个符号时只比前 6 个，
                            # 第 7 个以后要么误报漂移、要么被前面的命中侥幸放过（本轮两处都踩到）。
                            "pairs": _pair_nums(seg, nums),
                            "weak": not toks,
                            "text": line.strip()[:120]})
    seen, uniq = set(), []
    for an in anchors:
        # 行号也进 key：同一个 (路径, 行号) 被两行重复引用时，两行都要算被核过。
        # 以前只留第一条，第二条静默消失，「这行有没有被门禁看见」这个问题的答案就是错的。
        # 抓取成本不变：check() 对同一个文件本来就有缓存。
        k = (an["line"], an["path"], tuple(an["nums"]), an["weak"], an.get("symbol_only"),
             tuple(an.get("syms", [])), tuple(sorted((an.get("pairs") or {}).items())))
        if k not in seen:
            seen.add(k)
            uniq.append(an)
    return uniq


def enclosing(lines, token, n):
    """记录行是否落在该标识符的定义域内。装饰器行不含函数名、区间落在函数体内，都属正常。

    第一版只认 Python 的 def/class 与 Field/Literal 赋值。TS 与 JS 文件里的锚点
    因此永远进不了定义体判定：函数搬走、改名，探针仍说对得上。这里补 TS/JS 的
    顶层定义形状，同时保留 Python 那两条。
    """
    starts = [i for i, ln in enumerate(lines, 1) if token in ln]
    if not starts:
        return False
    top = re.compile(r"^(?:async\s+)?(?:def|class)\s+\w|^\w.*=\s*(?:Field|Literal)\b"
                     r"|^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                     r"(?:function|class|interface|type|enum|namespace|module|abstract\s+class)\s+\w"
                     r"|^(?:export\s+)?(?:const|let|var)\s+\w"
                     r"|^\w[\w$]*\s*\(.*\)\s*\{\s*$"
                     r"|^\w[\w$]*\s*(?:\([^)]*\))?\s*[:=]\s*(?:async\s*)?\(")
    for s in starts:
        if s > n:
            continue
        blocked = False
        for k in range(s + 1, n + 1):
            if k <= len(lines) and top.match(lines[k - 1]) and token not in lines[k - 1]:
                blocked = True
                break
        if not blocked:
            return True
    return False


def _all_blank(lines, present):
    """记录的那些行号是不是全是空行。全空就不能说「这行写着那个标识符」。"""
    return bool(present) and all(not lines[n - 1].strip() for n in present)


def check(anchors, head, get=None, get_raw=None, cache_dir=None):
    """逐条核对锚点。状态五种：ok 对得上，drift 行号漂了，gone 上游 404（真结论），
    unreachable 取不到（限流或断网，不是结论），weak 这句话没有符号可比对（探针管不了，
    也不算核过）。把 gone 与 unreachable 混在一起，一次限流就会被读成「上游把文件删了」。
    把 weak 算成 ok，就回到旧版的假绿：锚点只写了路径与行号时，谁都没在核它。

    get / get_raw / cache_dir 透传给 upstream_lines，自检因此能离线跑满 check 的判据本身
    （2026-11-17：以前自检只测 _all_blank 这个纯函数，接线断了也照样绿）。
    """
    file_cache, blob_cache, rows = {}, {}, []
    for an in anchors:
        path = an["path"]
        if path not in file_cache:
            file_cache[path] = upstream_lines(path, head, get=get, get_raw=get_raw,
                                              cache_dir=cache_dir)
        lines, err = file_cache[path]
        if err == "missing":
            rows.append(dict(an, status="gone", detail="上游 404：该文件已改名或已删除"))
            continue
        if lines is None:
            why = {"limited": "限流（403/429），带 GITHUB_TOKEN 再跑",
                   "big": "文件超过 contents API 的正文上限（1MB），需改用 git blob API 才能核"}.get(err, "传输失败（超时或断网）")
            rows.append(dict(an, status="unreachable", detail=why))
            continue
        if an.get("symbol_only"):
            # 只有符号名没有行号的锚点：核符号还在不在整个文件里。符号没了就是改名或搬走，
            # 那种声明会一路误导排障，以前这类完全不在门禁之内。
            # 带点的写法（TaskAgentManager._brief）上游是「类里定义 def _brief」，正文里没有
            # 那串点号，所以按每一段分别核：类名与方法名都要在该文件里出现。
            blob = NL.join(lines)
            missing = []
            for s in an["syms"]:
                parts = [p for p in s.split(".") if p]
                # 点号写法逐段核定义：TaskAgentManager._brief 要类名与方法名各自以定义身份出现
                if all(_definition_pattern(p).search(blob) for p in parts):
                    continue
                missing.append(s)
            if missing:
                rows.append(dict(an, status="drift",
                                 detail="上游该文件里已经找不到这些符号：%s（多半改了名或搬走，"
                                        "去上游搜一遍再改文档）" % ", ".join(missing)))
            else:
                rows.append(dict(an, status="ok", detail="符号仍在（无行号）"))
            continue
        if an.get("weak"):
            rows.append(dict(an, status="weak",
                             detail="这句话只有路径与行号，没有可比对的符号。上游怎么改都验不出来，"
                                    "请在 profile 里补一个符号名（函数、字段或类）"))
            continue
        # 整份文件的原文拼一次，判「配上的词是不是真符号」要用
        if path not in blob_cache:
            blob_cache[path] = NL.join(lines)
        blob = blob_cache[path]
        drift, present = [], []
        for n in an["nums"]:
            if n > len(lines):
                drift.append("%d 超出该文件行数(%d)" % (n, len(lines)))
            else:
                present.append(n)
        # 命中判据（2026-11-17 按 A734 的 N4 收紧）：逐个行号各判各的。
        # 以前把这些行拼成一大段，整段里只要出现任意一个标识符就算全对。一条锚点挂 4 个 logs
        # 路由、另一条挂 8 个 plugins 函数，其中 3 个漂了也不会报。
        # 容差保留：记录行本身，或紧邻的下一行（装饰器在 def 之前），或落在该标识符的定义体内。
        blank_hit = bool(an["tokens"]) and _all_blank(lines, present)
        if blank_hit:
            moved = []
            for t in an["tokens"][:3]:
                w = where_symbol(lines, t)
                moved.append("%s→%s" % (t, "/".join(map(str, w)) if w else "未找到"))
            drift.append("记录行在上游全是空行，锚点没落在写代码的那一行；" + ", ".join(moved))
        else:
            for n in present:
                win = lines[n - 1]
                if n < len(lines):
                    win += "\n" + lines[n]
                # 判据用「整条的 token 列表 + 这一号配上的那个符号」。
                # 只加不减：散文里紧邻的词未必是作者指的符号（`docstring`、`oauth` 这类），
                # 拿它单独判会报一片假漂移；但它非空就把被 6 个上限挤掉的第 7 个符号请回来。
                paired = (an.get("pairs") or {}).get(n)
                # 配上的词要同时满足两条才算「这一号的符号」：在文件里真出现过、
                # 且以定义形状出现。散文里挨着数字的普通词（docstring、grep、rollback）
                # 两条都不满足，退回整条 tokens；而「两个号互换」这种真错误两条都满足，跑不掉。
                hard = (bool(paired) and bool(where_symbol(lines, paired))
                        and bool(_definition_pattern(paired).search(blob)))
                cand = [paired] if hard else an["tokens"]
                if any(t in win for t in cand):
                    continue
                if any(enclosing(lines, t, n) for t in cand):
                    continue
                moved = []
                for t in cand[:3]:
                    w = where_symbol(lines, t)
                    moved.append("%s→%s" % (t, "/".join(map(str, w)) if w else "未找到"))
                tag = "本锚点为这一号配上的符号" if hard else "本锚点的标识符"
                drift.append("%d 行上没有%s，也不在其定义体内；%s"
                             % (n, tag, ", ".join(moved)))
        rows.append(dict(an, status="drift" if drift else "ok", detail="; ".join(drift)))
    return rows


def self_test():
    """钉住两处真出过错的形状：状态分类，以及从一句话里抓行号。

    2026-11-15 又钉两处假绿：段内没有符号时退回整行拿路径片段当 token，
    以及 enclosing 只认 Python 定义形状。这两种都会让「代码已搬走」读成「仍对得上」。
    """
    def anchor(text):
        got = collect(text)
        return got[0] if got else {}

    cases = [
        (classify(404), "missing"), (classify(403), "limited"), (classify(429), "limited"),
        (classify(500), "net"), (classify(301), "net"),
        # 200 但无正文 = 文件过大，不是上游 404
        (body_state(None), "missing"), (body_state({"content": ""}), "big"),
        (body_state({"content": "x"}), None),
        # issue 号带 #、章节号带 §，都不该被当成行号；「NNN 行」「:NNN」「（NNN」三种形状要抓到
        (window_nums("读于 :35 与 1050 行"), [35, 1050]),
        (window_nums("issue #3683 §4.2 自查 5、6、8"), []),
        (window_nums("（id 212、name 213）"), [212, 213]),
        (window_nums("覆盖率 95%"), []),
        # 只有路径与行号的一句话：标 weak，token 必须为空（不许拿路径片段凑）
        (anchor("见 plugin/app/x.ts:129 那一处。").get("weak"), True),
        (anchor("见 plugin/app/x.ts:129 那一处。").get("tokens"), []),
        # 句子里有符号就不 weak
        (anchor("plugin/app/x.py:12 里是 handler 函数").get("weak"), False),
        (anchor("plugin/app/x.py:12 里是 handler 函数").get("tokens"), ["handler"]),
        # enclosing 认 TS/JS 顶层定义：下一个定义把作用域关掉时，记录行不算在域内。
        # 这一条是真的对照：只认 Python 形状时，第 4 行的 export const 不算边界，会错判 True。
        (enclosing(["function handler() {", "  return 1", "}",
                    "export const WORLD = {", "  k: 1"], "handler", 5), False),
        # Python 的 def 边界照旧算边界
        (enclosing(["def handler():", "    return 1", "def other():", "    pass"],
                   "handler", 4), False),
        # 同一个函数体内部的行算在域内（装饰器与区间锚属这种）
        (enclosing(["def handler():", "    x = 1", "    return x"], "handler", 3), True),
        (enclosing(["export class Panel extends Base {", "  onMount() {", "    const x = 1"],
                   "Panel", 3), True),
        # 2026-11-17 收紧：行号撞上空行时必须算漂，哪怕它落在某个定义体内
        (_all_blank(["def handler():", "", "    pass"], [2]), True),
        (_all_blank(["def handler():", "    x = 1"], [2]), False),
        (_all_blank(["def handler():", "    x = 1"], []), False),
        (_all_blank(["a", "b", "c"], [2, 3]), False),
        # 2026-11-17 补：区间形状的行号以前抽不出来。「实测为 105-113 行」整条锚点在门禁之外。
        (window_nums("实测为 105-113 行"), [105, 113]),
        # 冒号区间（外部审 40 号 FG-5）：Cortico/TS 那一侧正文就写 `:14-36`、`:500-503`，
        # 旧形状表只认头不认尾，区间尾号写错永远是绿。头尾两个号都要进号组。
        (window_nums("src/core/blobs.ts:14-36、:85"), [14, 36, 85]),
        (window_nums("src/core/core.ts:500-503 里重写 ts"), [500, 503]),
        (window_nums("docs/worlds.md:60-61"), [60, 61]),
        (window_nums("src/bot.ts:253-256 与 :1050"), [253, 256, 1050]),
        (window_nums("旧行号 1055-1150、1693-2049 已失效"), []),
        (window_nums("行号 64、95、182，读于 e14f61963"), [64, 95, 182]),
        (window_nums("`kind: EntryKind` 那一行，行号 263、302、391"), [263, 302, 391]),
        (window_nums("这三处是 12、34"), []),
        # 同一句声明被两行重复引用时，两行都要算被门禁看见（key 里带行号）
        (len(collect("见 plugin/x.py 的 `handler_a`:12" + NL + "见 plugin/x.py 的 `handler_a`:12")), 2),
        (window_nums("def :150、调用 :180、回写 :184"), [150, 180, 184]),
        # 符号锚只认文档规定的「文件:符号名」两种写法，散文里的词不算锚点
        (_anchor_symbol_span(" : MESSAGE_PLANE_PAYLOAD_MAX_BYTES 常量块"),
         ["MESSAGE_PLANE_PAYLOAD_MAX_BYTES"]),
        (_anchor_symbol_span(" 的 `build_plugin_server_app` 形参"), ["build_plugin_server_app"]),
        (_anchor_symbol_span(": _definition；无对话历史 = x"), ["_definition"]),
        (_anchor_symbol_span("（下行起的 13 个行号读于 db81c1be0）"), []),
        (_anchor_symbol_span(" 里是 handler 函数"), []),
        (_anchor_symbol_span(": TaskAgentManager._brief；另一处 = RpcDispatcher.submit"),
         ["TaskAgentManager._brief"]),
    ]
    # 2026-11-17 补：1MB 以上的文件必须走 raw 端点拿正文。Contents API 对这种文件返回 200
    # 但不带 content，旧版把它算成「取不到」，探针会天天退 2 却不给出任何结论。
    import shutil
    import tempfile
    cache = tempfile.mkdtemp(prefix="drift-self-")
    seen = []
    try:
        def contents_big(url):
            seen.append(("contents", url))
            return {"content": None, "size": 1100000}, None       # 200 无正文 = 文件过大

        def raw_ok(url):
            seen.append(("raw", url))
            return ("handler: int = 1" + NL + "value = 2").encode("utf-8"), None

        want_lines = ["handler: int = 1", "value = 2"]
        lines, err = upstream_lines("plugin/settings.py", "deadbeefdeadbeef",
                                    get=contents_big, get_raw=raw_ok, cache_dir=cache)
        cases.append(((err, lines), (None, want_lines)))
        cases.append((seen[1][1],
                      "https://raw.githubusercontent.com/%s/deadbeefdeadbeef/plugin/settings.py"
                      % UPSTREAM))
        # 第二次应当命中缓存：再取就一个网络调用都不发
        calls = []
        lines2, err2 = upstream_lines("plugin/settings.py", "deadbeefdeadbeef",
                                      get=lambda u: calls.append(u) or ({}, None),
                                      get_raw=lambda u: calls.append(u) or (None, None),
                                      cache_dir=cache)
        cases.append(((len(calls), err2, lines2), (0, None, want_lines)))
        # raw 自己失败时按故障类型分账：断网不算「上游删了文件」，404 才算
        lines3, err3 = upstream_lines("plugin/big.py", "cafe1234cafe1234", get=contents_big,
                                      get_raw=lambda u: (None, "net"), cache_dir=cache)
        cases.append(((err3, lines3), ("net", None)))
        lines4, err4 = upstream_lines("plugin/gone.py", "cafe1234cafe1234", get=contents_big,
                                      get_raw=lambda u: (None, "missing"), cache_dir=cache)
        cases.append(((err4, lines4), ("missing", None)))
        # check() 层的判据。光测 _all_blank 这个纯函数不够：接线断了照样绿。
        fake_src = ("def handler():" + NL + "    x = 1" + NL + NL + "    return x" + NL)
        b64 = base64.b64encode(fake_src.encode("utf-8")).decode("ascii")

        def get_inline(url):
            return {"content": b64}, None

        def an(n):
            return {"path": "plugin/a.py", "line": 1, "nums": [n],
                    "tokens": ["handler"], "weak": False}

        cache2 = tempfile.mkdtemp(prefix="drift-check-")
        try:
            for n, want_status in ((3, "drift"), (1, "ok"), (2, "ok")):
                got_status = check([an(n)], "f00df00df00d",
                                   get=get_inline, cache_dir=cache2)[0]["status"]
                cases.append((got_status, want_status))
            # 大文件也要能过 check：Contents 不给正文时由 raw 补上，判据照旧
            def get_big_no_body(url):
                return {"content": None}, None

            got_big = check([an(1)], "beefbeefbeef", get=get_big_no_body,
                            get_raw=lambda u: (fake_src.encode("utf-8"), None),
                            cache_dir=tempfile.mkdtemp(prefix="drift-big-"))[0]["status"]
            cases.append((got_big, "ok"))
            # 符号锚（只有「文件:符号名」没有行号）也要过 check：上游删了符号就得报漂移。
            src_with = ("class TaskAgentManager:" + NL + "    def _brief(self):" + NL +
                        "        return 1" + NL)
            src_wo = "class Other:" + NL + "    def _brief(self):" + NL + "        pass" + NL

            def an_sym(syms):
                return {"path": "plugin/a.py", "line": 1, "nums": [], "tokens": [],
                        "weak": False, "syms": syms, "symbol_only": True}

            def get_body(url, b64s=None):
                return {"content": b64s}, None

            b_with = base64.b64encode(src_with.encode("utf-8")).decode("ascii")
            b_wo = base64.b64encode(src_wo.encode("utf-8")).decode("ascii")
            c3 = tempfile.mkdtemp(prefix="drift-sym-")
            st_dot = check([an_sym(["TaskAgentManager._brief"])], "aaaa1111aaaa",
                           get=lambda u: get_body(u, b_with), cache_dir=c3)[0]["status"]
            cases.append((st_dot, "ok"))
            st_gone = check([an_sym(["RpcDispatcher.submit"])], "bbbb2222bbbb",
                            get=lambda u: get_body(u, b_with), cache_dir=c3)[0]["status"]
            cases.append((st_gone, "drift"))
            st_plain = check([an_sym(["_brief"])], "cccc3333cccc",
                             get=lambda u: get_body(u, b_wo), cache_dir=c3)[0]["status"]
            cases.append((st_plain, "ok"))
            # 这条专防「拼成一大段再找」：第 6 行与 handler 无关，但第 1 行有。
            # 拼段判据会说对得上，逐行判据必须报漂移。
            far_src = ("def handler():" + NL + "    x = 1" + NL + "    return x" + NL + NL
                       + NL + "class Unrelated:" + NL)
            far_b64 = base64.b64encode(far_src.encode("utf-8")).decode("ascii")
            far_anchor = {"path": "plugin/a.py", "line": 1, "nums": [1, 7],
                          "tokens": ["handler"], "weak": False}
            far_status = check([far_anchor], "cafe0001cafe",
                               get=lambda u: ({"content": far_b64}, None),
                               cache_dir=tempfile.mkdtemp(prefix="drift-far-"))[0]["status"]
            cases.append((far_status, "drift"))
            md_case = _definition_pattern("LLM Result Field Filtering").search(
                "# 顶部\n\n### LLM Result Field Filtering\n正文")
            cases.append((bool(md_case), True))
            cases.append((bool(_definition_pattern("Ok").search('__all__ = [\n    "Ok",\n]')), True))
            cases.append((bool(_definition_pattern("Ok").search("正文里顺口提到 Ok 一句话")), False))
            cases.append((bool(_definition_pattern("Ok").search(
                "## Ok 与 Err" + NL + NL + "正文")), True))
            # 不变量：每条锚点都必须产出一行结果。上面那次批量替换把 rows.append 吞掉时，
            # 101 条锚点只剩 17 行结果，而每条结果各自仍是「对得上」——整体会读成绿灯。
            _many = [an(n) for n in (1, 2, 3)] + [an_sym(["handler"]), an_sym(["nope_xyz"])]
            _rows = check(_many, "f00df00df00d", get=get_inline, cache_dir=cache2)
            cases.append((len(_rows) == len(_many) and len({id(r) for r in _rows}) == len(_many), True))
            shutil.rmtree(c3, ignore_errors=True)
            # collect() 必须真的把「文件:符号名」这种写法抽成锚点，否则上面这些都是死代码
            cases.append((bool([a for a in collect("见 plugin/x.py: handler_def 那一处")
                                if a.get("symbol_only")]), True))
            cases.append((bool([a for a in collect("见 plugin/x.py（读于 sha）")
                                if a.get("symbol_only")]), False))
            # unresolvable()：只写文件名的锚点必须被报出来。上游 5 个 messages.py、6 个
            # query_service.py，猜映射表会猜错，所以这里只报写法，让人把目录写全。
            def un(s):
                return [u["path"] for u in unresolvable(s.replace("\n", NL))]
            cases.append((un("见 `health.py:20`"), ["health.py"]))
            cases.append((un("见 `plugin/server/routes/health.py:20`"), []))
            cases.append((un("见 `routes/plugins.py:42`"), ["routes/plugins.py"]))
            cases.append((un("见 `config/network.py:193`"), []))
            cases.append((un("见 `docs/plugins/cli.md:5`"), []))
            cases.append((un("见 `scripts/profile_drift.py:13`"), []))
            cases.append((un("未核验：`pyproject.toml:7` 的动态版本"), []))
            cases.append((un("本机 `~/.workbuddy/settings.json:12`"), []))
            cases.append((un("见 `plugin/core/registry.py:1` 与 `registry.py:2`"), ["registry.py"]))
            cases.append((un("见 `plugin/a.py` 的 `handler`"), []))
            # W10 四条例据：撤对应判据行，这几条必须红。
            # ①小句级豁免：同行另一句的「本机」不静音上游裸名锚点
            cases.append((un("| 6 | 起 | 本机旧拷贝；上游 `registry.py` 第 42 行 |"),
                          ["registry.py"]))
            # ②上游歧义名撞本仓文件名仍须报；写全仓内相对路径才豁免
            cases.append((un("见 `SKILL.md` 第 9999 行"), ["SKILL.md"]))
            cases.append((un("见 `skills/neko-plugin-sandbox/SKILL.md` 第 9999 行"), []))
            # ③形状对齐：collect 认的括号区间、冒号、顿号列、行号列，裸名同样要报
            cases.append((un("见 `messages.py`:22"), ["messages.py"]))
            cases.append((un("见 `messages.py`（22）"), ["messages.py"]))
            cases.append((un("见 `schema.py`: PluginRuntimeSchema（264-282 读于 db81c1be0）"),
                          ["schema.py"]))
            cases.append((un("见 `runs.py` 行号 64、95"), ["runs.py"]))
            # ④讲解坏写法的例句显式豁免
            cases.append((un("`messages.py:22` 这种写法探针猜不对（drift-ignore）"), []))
            # 本机绝对/相对路径照旧豁免；裸 main.py 属上游歧义名，须报
            cases.append((un("见 `./tools/mine.py:3` 与本仓 `main.py`:8"), ["main.py"]))
            # 「NNN-NNN 行」紧跟着文件名时也是锚点，反引号要抹平才看得见
            cases.append((un("`cli.py` 105-113 行"), ["cli.py"]))
            cases.append((un("`plugin/neko_plugin_cli/cli.py` 105-113 行"), []))
            # W10.3 对齐后本句是锚点：全径同句 collect 本来就抓 [105, 113]，裸名不许缺一边。
            cases.append((un("`cli.py` 实测为 105-113 行"), ["cli.py"]))
            cases.append((un("本机蒸馏文档 `sdk-core.md` 第 20 行"), []))
            # 行号与符号的配对：一条锚点里多个符号时，每个号各问各的
            seg_two = ": timer_interval（def 156）、lifecycle（127）"
            pr = _pair_nums(seg_two, [156, 127])
            cases.append((pr.get(156), "timer_interval"))
            cases.append((pr.get(127), "lifecycle"))
            cases.append((_pair_nums("（读于 db81c1be0）", [81]), {}))
            # 一条锚点里 7 个符号：tokens 只留前 6 个，第 7 个（omega）靠配对回到判据里。
            # 没有配对时它会被挤掉，omega 那号就只能靠别的符号侥幸命中。
            many_src = ("def alpha():" + NL + "def beta():" + NL + "def gamma():" + NL
                        + "def delta():" + NL + "def epsilon():" + NL + "def zeta():" + NL
                        + "def omega():" + NL)
            many_b64 = base64.b64encode(many_src.encode("utf-8")).decode("ascii")
            many_text = ("见 `plugin/a.py`: alpha（1）、beta（2）、gamma（3）、delta（4）、"
                         "epsilon（5）、zeta（6）与 omega（7）`")
            many_anchors = collect(many_text)
            many_a = [x for x in many_anchors if x["path"] == "plugin/a.py"][0]
            cases.append((len(many_a["tokens"]), 6))
            cases.append((many_a["pairs"].get(7), "omega"))
            cases.append((check([dict(many_a)], "ee11ee11ee11",
                                get=lambda u: ({"content": many_b64}, None),
                                cache_dir=tempfile.mkdtemp(prefix="drift-many-"))[0]["status"],
                         "ok"))
            # 配对只加不减：散文里紧邻的是 `docstring` 这种废词，也不该因此报漂移
            doc_src = "def handler():" + NL + "    pass" + NL
            doc_b64 = base64.b64encode(doc_src.encode("utf-8")).decode("ascii")
            doc_an = {"path": "plugin/a.py", "line": 1, "nums": [1],
                      "tokens": ["handler"], "weak": False, "pairs": {1: "docstring"}}
            swap_src = ("def timer_interval():" + NL + "    pass" + NL + NL
                        + "def lifecycle():" + NL + "    pass" + NL)
            swap_b64 = base64.b64encode(swap_src.encode("utf-8")).decode("ascii")
            swap_an = {"path": "plugin/a.py", "line": 1, "nums": [1, 4],
                       "tokens": ["timer_interval", "lifecycle"], "weak": False,
                       "pairs": {1: "lifecycle", 4: "timer_interval"}}
            swap_rc = check([swap_an], "ee33ee33ee33",
                            get=lambda u: ({"content": swap_b64}, None),
                            cache_dir=tempfile.mkdtemp(prefix="drift-swap-"))[0]
            cases.append((swap_rc["status"], "drift"))
            # 真符号配对：各号命中自己的符号，必须通过
            good_an = {"path": "plugin/a.py", "line": 1, "nums": [1, 4],
                       "tokens": ["timer_interval", "lifecycle"], "weak": False,
                       "pairs": {1: "timer_interval", 4: "lifecycle"}}
            cases.append((check([good_an], "ee44ee44ee44",
                                get=lambda u: ({"content": swap_b64}, None),
                                cache_dir=tempfile.mkdtemp(prefix="drift-good-"))[0]["status"],
                         "ok"))
            # 配上的词在文件里不是定义（散文词）→ 退回 tokens，不许误杀
            prose_an = {"path": "plugin/a.py", "line": 1, "nums": [1],
                        "tokens": ["timer_interval"], "weak": False,
                        "pairs": {1: "docstring"}}
            cases.append((check([prose_an], "ee55ee55ee55",
                                get=lambda u: ({"content": swap_b64}, None),
                                cache_dir=tempfile.mkdtemp(prefix="drift-prose-"))[0]["status"],
                         "ok"))
            cases.append((check([doc_an], "ee22ee22ee22",
                                get=lambda u: ({"content": doc_b64}, None),
                                cache_dir=tempfile.mkdtemp(prefix="drift-doc-"))[0]["status"],
                         "ok"))
            # 区间尾号也要核（外部审 40 号 FG-5）：`:2-5` 的 5 落在文件外，必须报漂移。
            # 旧形状表只认头，整条锚点写错尾号永远是绿。
            rng_src = "def alpha():" + NL + "    pass" + NL
            rng_b64 = base64.b64encode(rng_src.encode("utf-8")).decode("ascii")
            rng_an = [x for x in collect("见 `plugin/a.py`: alpha:2-5 `")
                      if x["path"] == "plugin/a.py"][0]
            cases.append((rng_an["nums"], [2, 5]))
            cases.append((check([dict(rng_an)], "ee66ee66ee66",
                                get=lambda u: ({"content": rng_b64}, None),
                                cache_dir=tempfile.mkdtemp(prefix="drift-rng-"))[0]["status"],
                         "drift"))
            # 同号组的两条锚点都要留在账上（外部审 40 号 FG-4）：去重键不含符号时，
            # 第二条（往往是编出来的那个符号）静默消失，锚点总数不变、门禁全绿。
            dup_text = ("见 `plugin/a.py`: alpha（1）`" + NL
                        + "见 `plugin/a.py`: ghost（1）`")
            cases.append((len([x for x in collect(dup_text) if x["path"] == "plugin/a.py"]), 2))
            # resolve_head 的三条分支：命中缓存、过期重取、取不到 sha。以前这段只能靠真网络演，
            # 「当前 main」这句话到底从哪来，门禁里没有一个用例说过（终检 sweep 第 14 条）。
            hc = tempfile.mkdtemp(prefix="drift-head-")
            try:
                calls = []

                def get_head(url):
                    calls.append(url)
                    return {"sha": "cafe0000cafe", "default_branch": "trunk"}, None

                def get_never(url):
                    raise AssertionError("缓存还新鲜时不该发请求：" + url)
                with open(os.path.join(hc, "head.txt"), "w", encoding="utf-8") as f:
                    f.write("deadbeefdead\n")
                cases.append((resolve_head(True, 3600, cache_dir=hc, get=get_never),
                              ("deadbeefdead", True)))
                got_fresh = resolve_head(True, 3600, cache_dir=tempfile.mkdtemp(prefix="drift-head2-"),
                                         get=get_head)
                cases.append((got_fresh, ("cafe0000cafe", False)))
                cases.append((any("/commits/trunk" in u for u in calls), True))
                # 缓存过期（max_age=-1）→ 必须重取，不能拿旧 sha 说「当前 main」
                got_old = resolve_head(True, -1, cache_dir=hc, get=get_head)
                cases.append((got_old, ("cafe0000cafe", False)))
                got_none = resolve_head(False, 3600, cache_dir=tempfile.mkdtemp(prefix="drift-head3-"),
                                        get=lambda u: (None, "limited"))
                cases.append((got_none[0], ""))
                cases.append((got_none[1], "limited"))
                shutil.rmtree(hc, ignore_errors=True)
            except OSError as e:
                cases.append((str(e), ""))
            # main() 的退出码接线也要演：argv 现在可注入。api 与 CACHE_DIR 临时换掉，
            # 自检照旧不碰网络，也不往仓库缓存里写东西。
            _real_api, _real_cache = api, CACHE_DIR
            mp = tempfile.mkdtemp(prefix="drift-main-")
            try:
                globals()["CACHE_DIR"] = mp
                src_b64 = base64.b64encode(("def handler():" + NL + "    x = 1" + NL).encode(
                    "utf-8")).decode("ascii")

                def get_main(url):
                    if "/contents/" in url:
                        return {"content": src_b64}, None
                    return {"sha": "cafe0000cafe", "default_branch": "main"}, None
                globals()["api"] = get_main
                prof = os.path.join(mp, "p.md")

                def run(text, strict=True):
                    with open(prof, "w", encoding="utf-8") as f:
                        f.write(text)
                    # 吞掉报告：自检的输出只要一行结论，main() 的表格别混进来
                    with contextlib.redirect_stdout(io.StringIO()):
                        return main(["--profile", prof] + (["--strict"] if strict else []))
                cases.append((run("见 `plugin/a.py:1` 的 `handler`" + NL), 0))
                # 第 2 行落在 handler 的定义体内，那是合规写法；第 9 行超出文件长度才是漂移
                cases.append((run("见 `plugin/a.py:2` 的 `handler`" + NL), 0))
                cases.append((run("见 `plugin/a.py:9` 的 `handler`" + NL), 1))
                cases.append((run("见 `health.py:20`" + NL), 1))
                cases.append((run("见 `health.py:20`" + NL, strict=False), 0))
                globals()["api"] = lambda u: (None, "limited")
                # 缓存也要一起换掉：上一例已经把 head 与正文写进 mp，不换就命中缓存、测不到退 2
                globals()["CACHE_DIR"] = tempfile.mkdtemp(prefix="drift-cold-")
                cases.append((run("见 `plugin/a.py:1` 的 `handler`" + NL), 2))
            except OSError as e:
                cases.append((str(e), ""))
            finally:
                globals()["api"], globals()["CACHE_DIR"] = _real_api, _real_cache
                shutil.rmtree(mp, ignore_errors=True)
        finally:
            shutil.rmtree(cache2, ignore_errors=True)
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    bad = [(i, got, want) for i, (got, want) in enumerate(cases) if got != want]
    for i, got, want in bad:
        print("  自检失败 #%d: 得到 %r 期望 %r" % (i, got, want))
    print("探针自检 %d 例：%s" % (len(cases), "全部通过" if not bad else "%d 例失败" % len(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="profile 锚点漂移探针")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="有漂移或有 weak 锚点就退出 1（默认只报告）")
    ap.add_argument("--fresh", action="store_true", help="忽略缓存重拉")
    ap.add_argument("--self-test", action="store_true", help="只跑自检，不碰网络")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    paths = PROFILES if a.profile == DEFAULT_PROFILE else [a.profile]
    for p in paths:
        if not os.path.isfile(p):
            print("用法错误: 找不到 profile:", p)
            return 2
    if a.fresh and os.path.isdir(CACHE_DIR):
        for fn in os.listdir(CACHE_DIR):
            os.remove(os.path.join(CACHE_DIR, fn))

    anchors, unres = [], []
    for p in paths:
        text = open(p, encoding="utf-8-sig").read().replace("\r\n", NL)
        src = os.path.relpath(p, REPO).replace("\\", "/")
        anchors += [dict(x, src=src) for x in collect(text)]
        unres += [dict(x, src=src) for x in unresolvable(text)]

    head, from_cache = resolve_head(use_cache=not a.fresh)
    if not head:
        print("探针取不到上游 %s 的当前 sha（匿名限额、离线或凭据问题），本次不作判定。" % UPSTREAM)
        return 2

    rows = check(anchors, head)
    unreachable = [r for r in rows if r["status"] == "unreachable"]
    weak = [r for r in rows if r["status"] == "weak"]
    drift = [r for r in rows if r["status"] in ("drift", "gone")]
    ok = len(rows) - len(drift) - len(unreachable) - len(weak)
    if a.json:
        print(json.dumps({"upstream": UPSTREAM, "head": head, "head_from_cache": from_cache,
                          "anchors": len(rows), "ok": ok, "weak": len(weak),
                          "unreachable": len(unreachable), "drift": drift,
                          "unresolvable": unres,
                          "weak_rows": [{"line": r["line"], "path": r["path"],
                                         "nums": r["nums"]} for r in weak]},
                         ensure_ascii=False, indent=1))
    else:
        print("上游 %s，当前 main = %s%s" % (UPSTREAM, head[:9],
              "（取自 %d 小时内的缓存，--fresh 可强制重取）" % (HEAD_TTL // 3600) if from_cache else ""))
        print("核对 %s：锚点 %d 条，对得上 %d，漂移 %d，核不了 %d，取不到 %d；写法核不了 %d 条"
              % ("、".join(os.path.basename(p) for p in paths), len(rows), ok, len(drift),
                 len(weak), len(unreachable), len(unres)))
        for r in (drift + unreachable + weak)[:30]:
            print("  [%s] %s 第 %d 行  %s  记录号 %s\n        %s"
                  % (r["status"], r.get("src", "profile"), r["line"], r["path"],
                     "/".join(map(str, r["nums"][:6])), r["detail"]))
        for u in unres[:20]:
            print("  [写法核不了] %s 第 %d 行  %s:NN——行号前要把上游目录写全，"
                  "同名文件上游不止一个" % (u["src"], u["line"], u["path"]))
        if len(drift) + len(unreachable) + len(weak) > 30:
            print("  ...其余 %d 条用 --json 查看" % (len(drift) + len(unreachable) + len(weak) - 30))
        if unreachable:
            print("结论：%d 条锚点本次无法判定（取不到上游，不算漂移）。" % len(unreachable))
        elif unres:
            print("结论：%d 处锚点只写了文件名。上游同名文件不止一个，探针不知道是哪一个，"
                  "这几条完全在门禁之外。把目录写全（`plugin/server/routes/plugins.py:42`）。"
                  % len(unres))
        elif weak:
            print("结论：%d 条锚点没有可比对的符号，只有路径与行号，这几条不算核过。"
                  "把符号名补进 profile 那句里，探针才管得住它。" % len(weak))
        else:
            print("结论：" + ("全部锚点在当前 main 上仍对得上。" if not drift else
                            "行号已漂。按 detail 更新行号，或把锚点降成「只写标识符 + 读于 <sha>」。"))
    if unreachable:
        return 2
    # 2026-11-17：CI 这一步已从「只报」改成阻塞。weak 也要算失败——它的意思是我们自己的锚点没写符号，
    # 上游怎么变都验不出来。让它静默通过等于在门禁里留一格假绿。
    # unres（写法核不了）同理：探针看不见的那一条，才是行号写错还能过关的那一条。
    return 1 if (a.strict and (drift or weak or unres)) else 0


if __name__ == "__main__":
    sys.exit(main())
