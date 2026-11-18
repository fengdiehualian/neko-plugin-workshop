#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin-forge · 安全审计扫描器（零第三方依赖，Python 3.8+）

用法
  python security_audit.py <repo路径> [--allow 相对路径前缀,...] [--allow 另一个] [--include-docs]
                           [--json] [--quiet] [--fail-unclear] [--strict-placeholder]
                           [--declared-list 审核方清单.json]

四项计分检查 + 一项不计分提示（细则与评分规则见 references/security-checklist.md）
  SEC-1 路径范围：写/删操作指向项目外、敏感家目录，或残留个人绝对路径。
  SEC-2 明文密钥：token 形态、私钥块、password 类赋值。任一命中 = 直接阻断（一票否决）。
  SEC-3 任意执行：eval/exec、把子进程的 shell 开关打开、os.system、拼进 shell 的 -c 参数、
  管道喂给解释器、不安全反序列化。
  SEC-4 静默下载：网络取回与包安装点位；每处须对应"已声明用途"，未声明即静默。
  SEC-5 清单形状（不计分）：安装期钩子、依赖直连远端包、plugin.toml/package.json 缺必填字段。
  盲区另记两本账：skips（设计内不读：构建目录、文档、--allow 豁免、素材归档）
  与 unclear（真盲区：该读却没读到，或扩展名不在任何名单里、疑似压缩混淆）。
  「扩展名未分类」记在真盲区那本账上（工单 W05）：名单漏了一个后缀时，报告要响，
  不许静默当成放行。判池前先剥一层备份后缀，settings.py.bak 按 .py 读。

默认只扫代码与配置文件（文档不参与评分）；--include-docs 把 md/txt/rst 也纳入
（.html 与 .htm 属代码池，不用这个旗标：宿主真加载的页，门禁也当受保护文件）
（SEC-1/SEC-2 文档残留检查）。--allow 用于外部仓库的整份豁免，前缀匹配。
本仓自己不用 --allow：豁免走点位声明，写在行尾注释里，四段必填
（SEC-n、理由=、出处=含 AUDIT.md 的锚点、复审=YYYY-MM-DD），缺一段或过期就失效。
**行尾声明只在本仓自扫时生效。** 审外部仓库时它是被审方自己能写的东西，一律不算豁免：
要在外部仓上豁免，只能用审核方持有的 `--declared-list` 清单（仓库外 JSON，
每条用 SHA-256 绑住那一行正文，带 reviewer 与到期日）。被审方改一行，声明立刻失效。
--strict-declared 把畸形标记直接判成问题。这是分诊器，不是裁判：命中仍要逐条人工判定。

退出码：0 放行（≥90）；1 修完再放（50–89）；2 阻断（SEC-2 有发现或 <50）；
  3 不确定（加 --fail-unclear 且存在盲区时）。

设计参照：清单形状检查与「盲区不得当作放行」两点取自 jusspound-netizen/dsh-plugin-scanner
（bb8dc7cd，2026-11-14；该仓库无 LICENSE 文件，本文件为独立实现，未复制其代码）。
"""
import sys, os, re, json, argparse, tempfile, shutil, subprocess, hashlib

# 上游源码镜像（漂移探针的缓存）。这些文件是别人仓库的正文，扫它们等于把上游的
# 示例 token 记到本仓库头上：--include-docs 一开就被 314 个镜像文件淹没。
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", ".pytest_cache", ".mypy_cache", ".tox", ".drift-cache"}
# 这份扩展名池定义「什么算代码」，只服务于内容扫描。契约门禁另有一份 CODE_EXTS，
# 那份的用途是「什么算受保护文件」。两处用途不同，成员也不等价：门禁还管无扩展名文件、
# shebang 文件、hooks.json 与构建文件，本池还管 .ipynb 与各类脚本。
# 2026-11-17 起 .svg .htm .m .jl 两边都有。以前只有门禁认它们，扫描器不读内容，
# 那四类就成了「受保护却不被检查」的缝。改任何一份都要另核一遍这条缝有没有重新张开。
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
             ".rs", ".go", ".java", ".kt", ".kts", ".scala", ".swift", ".c", ".h",
             ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".rb", ".php", ".lua", ".pl",
             ".ex", ".exs", ".erl", ".dart", ".sh", ".bash", ".zsh", ".fish",
             ".ps1", ".psm1", ".bat", ".cmd", ".sql", ".ipynb",
             # 2026-11-17 补：这四类门禁早就当受保护文件，扫描器却不读它们的内容，
             # 密钥与 eval 写在里面就等于不存在（A571 第 1 条那个反向缺口的另一半）。
             # .svg 可带 <script>，.htm 是 .html 的另一半，.m 是 Objective-C，.jl 是 Julia。
             # 2026-11-17 再补：.html 以前只在 DOC_EXTS 里，于是门禁当它是受保护代码、
             # 扫描器却不读内容，宿主真加载的面板页成了两边都不管的缝（终检 sweep 第 12 条）。
             # .vbs 与 .ksh 与 .mk 同理：门禁名单里没有，扩展名判据一条都盖不住。
             ".htm", ".html", ".svg", ".m", ".jl", ".vbs", ".ksh", ".mk",
             # 工单 W05：Windows 脚本宿主（.wsf/.wsh/.vbe）、网页脚本（.asp/.jsp/.cfm）与
             # MSBuild/配置载体（.xml/.csproj）以前两边名单都没有。一条 ghp_ 写进 report.wsf
             # 就是 SEC-2=0、--fail-unclear 不响。这些都是宿主或浏览器真加载执行的形态。
             ".wsf", ".wsh", ".vbe", ".asp", ".aspx", ".jsp", ".jspx", ".cfm", ".xml",
             ".csproj"}
CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
               ".properties",
               # 工单 W05：这些是纯文本载体，行级规则读得动，以前却因为不在池里整个不读。
               # uv.lock / pnpm-lock 的摘要、导出的 .csv、误提交的 .log 都在这一类。
               ".csv", ".tsv", ".lock", ".sum", ".log", ".diff", ".patch"}
# .html 已进 CODE_EXTS（宿主真加载的页），这里不再列它：一个名字只归一处。
DOC_EXTS = {".md", ".markdown", ".txt", ".rst", ".llm"}
# 备份与临时副本的后缀：真扩展名藏在它们前面。settings.py.bak 要按 .py 判。
BACKUP_EXTS = {".bak", ".backup", ".orig", ".rej", ".new", ".old", ".save", ".swp",
               ".tmp", ".test", ".disabled", ".sample", ".example"}
# 素材与归档：内容不是文本，行级规则无从下手。这是设计内排除，不是漏检，
# 所以措辞里要带 BY_DESIGN_KEYS 的词；未分类的那一类反过来必须记成盲区。
ASSET_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".avif",
              ".tiff", ".mp3", ".mp4", ".webm", ".wav", ".ogg", ".flac", ".m4a",
              ".mov", ".avi", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".icns",
              ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
              ".whl", ".jar", ".war", ".nupkg", ".exe", ".dll", ".so", ".dylib",
              ".bin", ".class", ".pyc", ".pyo", ".o", ".a", ".lib", ".pdb",
              ".sqlite", ".sqlite3", ".db", ".pack", ".idx"}


def _pool_ext(fn):
    """判池用的扩展名：先剥一层备份/临时后缀，再取真扩展名。

    shortcut: 只剥一层（settings.py.bak 认，a.py.bak.bak 不认）；出现双层再升级。
    """
    name = fn[:-1] if len(fn) > 1 and fn.endswith("~") else fn
    ext = os.path.splitext(name)[1].lower()
    if ext in BACKUP_EXTS:
        ext = os.path.splitext(os.path.splitext(name)[0])[1].lower()
    return ext
MAX_BYTES = 2 * 1024 * 1024
CHECKS = ("SEC-1", "SEC-2", "SEC-3", "SEC-4")
# 点位级声明：
#   写法：在行尾注释里写 forge-declared 标记，四段依次是 SEC-n、理由=、出处=（含 AUDIT.md 的锚点）、
# 四段全部必填：类别、理由、出处、复审日期。缺任一段就不算豁免（fail-closed），
# 且 --strict-declared 把畸形标记直接判成问题。日期一过声明自动失效，命中回到账上。
DECLARE_RE = re.compile(r"forge-declared:\s*(SEC-[1-5])\s+理由=(\S.*?)\s+"
                        r"出处=(\S*AUDIT\.md\S*)\s+复审=(\d{4}-\d{2}-\d{2})")
# 只认注释位置上的标记：正则定义、报错文案、夹具里的字样都不算声明尝试。
# 两种注释语法：`#`（脚本与 yml）与 HTML 注释 `<!-- -->`（md 与 html）。
# 以前只有 `#` 一种，等于文档没有点位声明这条路——而文档里的教学示例正需要它：
# 历史条目不许为了过门禁改字（那是造假），改措辞又会丢掉原来的形状（工单 W14）。
DECLARE_BAD_RE = re.compile(r"(?:#|<!--)\s*forge-declared:[^\n]*(?:理由=|复审=)")

WRITE_RE = r"(?:open|rmtree|move|remove|unlink|rename|replace|write_text|write_bytes|mkdir|rmdir)\s*[({]"
ABS = r"['\"](?:[A-Za-z]:[\\\\/]|/(?:etc|usr|var|home|root|proc|Users)/)"

SEC1 = [
    (WRITE_RE + r"[^#]*" + ABS, "写/删目标含硬编码绝对路径"),
    (r"expanduser\s*\(\s*['\"]~/(?:\.ssh|\.aws|\.gnupg|\.azure|\.config|\.codex|\.zcode|\.codebuddy)", "指向敏感家目录"),
    (r"[A-Za-z]:[/\\\\]+Users[/\\\\]+(?!(?:Public|Default|All Users|<|\{|\.)[A-Za-z0-9_.<>{} -]*)[A-Za-z0-9_.-]{2,}", "Windows 用户名路径残留"),
    (r"(?:/Users/|/home/)(?!(?:user|Users|home|example|name|<|\{)[\"'/])([A-Za-z0-9_.-]{3,})/", "个人 home 路径残留"),
    (WRITE_RE + r"[^#]*\.\.[/\\\\]", "写/删经 ../ 越出当前目录"),
]
GH = r"\bgh[pousr]_[A-Za-z0-9]{16,}\b|\bgithub_pat_[A-Za-z0-9_]{24,}\b"
SEC2 = [
    (GH, "GitHub token"),
    (r"\bsk-[A-Za-z0-9_\-]{20,}\b", "sk- 形态 API key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "Slack token"),
    (r"-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----", "私钥块"),
    # 明文赋值改由 secret_assignments() 按标识符拆词判定：一条正则盖不住
    # snake / 前缀 / 中置 / 驼峰四种命名风格，且 \b 在下划线处根本不成立。
    (r"://[^/\s:@]*:(?![\"']?[<$])([^\s@\"'<>{}$]{8,})@", "URL 内嵌凭据"),
]
# 值为「代码引用」而不是字面量时的形态：dotted 路径、全大写常量、类型标注、纯数字、
# 与键同名、普通 http URL。这些都是核对报告点名的误报来源，一票否决项不能被它们触发。
CODE_VALUE_RE = re.compile(r"""^(?:
    [A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+      # args.password / self.password
  | [A-Z][A-Z0-9_]{3,}                    # MAX_TOKENS 之类的全大写常量
  | [A-Za-z_]\w*\[[^\]]*\]                # Optional[str] 这类类型标注（必须带方括号）
  | \d+                                   # 1234567890
  | (?:https?|ftp|ws|wss)://\S+           # 普通 URL，凭据另由 URL 内嵌凭据 判定
  | (?i:true|false|null|none|undefined)   # 字面量关键字
)$""", re.X)
IGNORE_VAL = re.compile(r"(?i)^(?:your[_-]|(?:changeme|placeholder|example|dummy|sample|test|fake|redacted|masked|todo|noreply|none|null|empty)(?![A-Za-z])|x{3,}|\*{3,}|<|\$\{|\{|\[)")
# 严格档只改一处：占位词后面跟数字也算真值。默认档会把 example1234567、test123456、
# fake9876543 当占位符吃掉，而这类写法在真实泄漏里不少见。代价是文档示例值会开始报，
# 所以默认不开，由 --strict-placeholder 打开。
IGNORE_VAL_STRICT = re.compile(r"(?i)^(?:your[_-]|(?:changeme|placeholder|example|dummy|sample|test|fake|redacted|masked|todo|noreply|none|null|empty)(?![A-Za-z0-9])|x{3,}|\*{3,}|<|\$\{|\{|\[)")
STRICT_PLACEHOLDER = False

  # forge-declared: SEC-3 理由=规则表里的说明文字本身 出处=AUDIT.md#sec-declared 复审=2026-12-15
def is_placeholder(val):
    """占位符判定，严格档由 --strict-placeholder 切换。"""
    pat = IGNORE_VAL_STRICT if STRICT_PLACEHOLDER else IGNORE_VAL
    return bool(pat.match(val))
SEC3 = [
    (r"(?<![\w.])(?:eval|exec)\s*\(", "eval/exec 动态执行"),  # forge-declared: SEC-3 理由=规则表里的正则字面量本身 出处=AUDIT.md#sec-declared 复审=2026-12-15
    (r"shell\s*=\s*True", "子进程 shell 开关被打开"),
    (r"os\.system\s*\(", "os.system"),
    (r"execSync\s*\(|child_process\.exec\(", "Node child_process.exec"),
    (r"new\s+Function\s*\(", "new Function 动态执行"),
    (r"['\"](?:bash|sh|zsh|cmd|powershell)(?:\.exe)?\s+[-/]c[\s'\\]", "shell -c 执行串"),
    (r"(?:curl|wget)[^'\"]*\|\s*(?:ba)?sh", "管道给 shell"),
    (r"Invoke-Expression|\biex\s", "PowerShell 动态执行"),  # forge-declared: SEC-3 理由=检测规则的正则字面量本身 出处=AUDIT.md#sec-declared 复审=2026-12-15
    (r"pickle\.loads?\s*\(", "pickle 反序列化"),
    (r"(?<![\w.])marshal\.loads?\s*\(", "marshal 反序列化（与 pickle 同级风险）"),
    (r"(?<![\w.])yaml\.load\s*\((?:(?!Loader|safe).)*\)", "unsafe yaml.load"),
    # 2026-11-15 补：这三条形状以前整批漏，实测喂进临时仓库报 SEC-3 0 处、100/100、放行
    (r"(?<![\w.])yaml\.unsafe_load\s*\(", "yaml.unsafe_load 全量反序列化"),  # forge-declared: SEC-4 理由=规则表里的正则字面量本身 出处=AUDIT.md#sec-declared 复审=2026-12-15
    (r"(?<![\w.])os\.popen\s*\(", "os.popen 起 shell"),
    (r"(?<![\w.])subprocess\.get(output|statusoutput)\s*\(", "subprocess.getoutput 走 shell 取回显"),
]
SEC4 = [
    (r"urlopen\s*\(|urlretrieve\s*\(|urllib\.request", "urllib 网络取回"),  # forge-declared: SEC-4 理由=规则表里的说明文字本身 出处=AUDIT.md#sec-declared 复审=2026-12-15
    (r"requests\.(?:get|post|put|patch)\s*\(|httpx\.(?:get|post|Client|AsyncClient)|aiohttp\.", "HTTP 客户端"),
    (r"\bfetch\s*\(|XMLHttpRequest|Invoke-WebRequest|Invoke-RestMethod", "前端/PS 网络取回"),  # forge-declared: SEC-4 理由=检测规则的正则字面量本身 出处=AUDIT.md#sec-declared 复审=2026-12-15
    (r"\b(?:curl|wget)\s+\S", "命令行下载"),
    (r"(?i)\b(?:pip|uv)\s+(?:pip\s+)?(?:install|add)\b", "包安装（外联 PyPI 等）"),
    (r"(?i)\b(?:npm|yarn|pnpm)\s+(?:i|install)\b|\bcargo\s+install\b|\bgo\s+get\b", "包安装（其他源）"),
    (r"git\s+(?:clone|pull|fetch)\b", "git 外联"),
    (r"(?i)download", "download 字样（核对是否声明）"),  # forge-declared: SEC-4 理由=检测规则的正则字面量本身 出处=AUDIT.md#sec-declared 复审=2026-12-15
]
TABLES = [(CHECKS[0], SEC1), (CHECKS[1], SEC2), (CHECKS[2], SEC3), (CHECKS[3], SEC4)]


# 键名拆词后命中这些整词，或整名含这些高区分度子串，才算明文赋值嫌疑。
KEY_WORDS = {"password", "passwd", "pwd", "passphrase", "secret", "token", "tokens",
             "credential", "credentials", "apikey", "api_key", "accesskey", "access_key",
             "accesstoken", "access_token", "authtoken", "auth_token", "refreshtoken",
             "refresh_token", "bearertoken", "bearer_token", "clientsecret", "client_secret",
             "secretkey", "secret_key", "privatetoken", "private_token", "sessionkey",
             "session_key", "encryptionkey", "encryption_key", "pass", "pwd"}
KEY_SUBSTRINGS = ("password", "passwd", "secret", "credential", "privatekey", "private_key",
                  "apikey", "api_key", "access_key", "accesskey", "auth_token", "authtoken",
                  "refresh_token", "client_secret")
MIN_SECRET_LEN = 8       # 再短会被示例值淹没；短于此的口令是已知盲区，写进 checklist


def secret_assignments(line):
    """返回 [(列, 键名, 值)]。值是代码引用时跳过，见 CODE_VALUE_RE。

    `@?` 是给 Objective-C 与 C# 的 @"..." 字面量留的口子。没有它，`NSString *token = @"口令"`
    整个不匹配：值字符类在 `"` 前就撞上 `@`，取到的值只剩一个字符。`.m` 进池之后实测漏的就是这条。
    """
    out = []
    # 值字符类要认散文的边界：反引号、中日韩标点与全角字符（、。「」（）…）一律当截断点。
    # 旧版只排 ASCII 空白与 ,;{}()，于是文档里「`max_tokens=MAX_TOKENS`）、类型标注」
    # 一路吃到下一个 ASCII 空格，取到的值带着反引号与汉字，CODE_VALUE_RE 的全大写规则
    # 当场失效；同一行相邻的 `OPENAI_API_KEY=`、`DB_PASSWORD=` 也被当成一个长值吞掉
    # （工单 W14：--include-docs 下 6 处 SEC-2 全是这个形状，没有一处是真凭据）。
    pat = (r'([A-Za-z0-9_.$-]{2,})[\'"]?\s*[:=]\s*@?[\'"]?'
           r'([^\'"\s,;{}()`　-〿一-鿿＀-￯'
           r'‐-‧“”‘’]{%d,})' % MIN_SECRET_LEN)
    for m in re.finditer(pat, line):
        key, val = m.group(1), m.group(2)
        if line[m.end(2):m.end(2) + 1] == "(":
            continue          # 右侧是函数调用，不是字面量（本文件自己就有一行）
        if is_placeholder(val) or CODE_VALUE_RE.match(val):
            continue
        vlow, klow = val.lower(), key.lower()
        if vlow == klow or vlow == klow.rsplit(".", 1)[-1]:
            continue          # self.password = password 是形参传递，不是硬编码
        low = key.lower()
        parts = set(re.split(r'[_.$-]+', low)) | {low}
        parts |= {p.lower() for p in re.findall(r'[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+', key)}
        if (parts & KEY_WORDS) or any(s in low for s in KEY_SUBSTRINGS) \
                or ("key" in parts and "api" in parts):
            # 最后一条管 x-api-key / api-key：拆词后是 x、api、key，词表里没有单独的 key。
            out.append((m.start(2), key, val))
    return out


def looks_minified(lines):
    """压缩或混淆时，行级规则等于没扫。单行 bundle 是最典型的情形，必须先于行数判断。"""
    if any(len(ln) > 500 for ln in lines):
        return True
    n = len(lines)
    if n <= 5:
        return False
    return sum(len(ln) for ln in lines) / float(n) > 200


SEC5 = [
    (r"(?i)(?:^|[{,\s])[\"']?(preinstall|postinstall|prepare|install)[\"']?\s*[:=]",
     "安装期钩子：装包即执行代码"),
    (r"(?i)(git\+(?:https?|ssh)://|(?:https?|ftp)://\S+\.(?:tar\.gz|whl|zip))",
     "依赖直连远端包（不经索引与锁定）"),
    (r"(?i)\bcmdclass\b\s*=|\bsetup_requires\b\s*=", "构建期自定义执行"),
]
MANIFEST_NAMES = {"plugin.toml", "pyproject.toml", "package.json", "setup.py",
                  "requirements.txt"}
# 盲区分两本账：设计内排除（构建目录、不参与评分的文档、--allow 豁免、扩展名不在池）
# 与真盲区（该读却没读到：超大、二进制、读失败、疑似压缩）。只有后者能触发退出码 3，
# 否则任何有 .git 或 README 的仓库都会恒为「不确定」。
BY_DESIGN_KEYS = ("构建/缓存/VCS 目录", "不在扫描池", "--allow 豁免")
# 密钥材料文件：内容不读（里面本来就该是私钥，把明文再抄进报告是第二次泄露）。
# 但它不能记成「设计内排除」：一个把 id_rsa 提交进仓库的项目，在盲区账上必须看得见。
# 旧版只按扩展名过滤，.pem 落进「不在扫描池」，于是满是私钥的仓库照样报「真盲区 0」。
SECRET_FILE_EXTS = {".pem", ".key", ".pfx", ".p12", ".kdbx", ".ppk", ".jks", ".keystore"}
SECRET_FILE_NAMES = {".netrc", "_netrc", ".npmrc", ".pypirc",
                     "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
# 名字本身就等于泄露的那一类：不看内容也计 SEC-2（一票否决）。故意不含 .pem 与 .key——
# 前者常是公钥证书，后者在 Next.js 里是本地化文件，按名字阻断会误伤；它们留在盲区账里。
# 收窄后的名单（外部审 A472 第 4 条）：一票否决只留给名字本身即泄露的形态。
# 旧版把 .npmrc / .jks / .keystore / server.key 也算进来，与「误报代价比漏报大」相违：
# .npmrc 常常只写 registry=，Android 示例仓库常带调试 keystore，Next.js 的本地化文件就叫 *.key。
SECRET_FILE_HARD = {".pfx", ".p12", ".kdbx", ".ppk"}
SECRET_FILE_HARD_NAMES = {".netrc", "_netrc", "private_key"}
# 前缀匹配：id_rsa.bak、id_ed25519.old 这类改名也要命中。
SECRET_KEY_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
# 这两个配置文件按内容判：只有真的写了凭据才计 SEC-2，报告里只出标记名、不出值。
AUTH_GATED_NAMES = {".npmrc", ".pypirc"}
AUTH_MARKER_RE = re.compile(r"(_auth\s*=|_authToken\s*=|password\s*=|token\s*=)")
# 值写成「环境变量引用」时不是凭据：//registry.npmjs.org/:_authToken=${NPM_TOKEN} 是 npm
# 官方推荐的写法，以前的判据只看标记名在不在，于是照它配的 .npmrc 被一票否决（A571 第 4 条）。
ENV_REF_RE = re.compile(r"^\$\{")
# 公钥后缀：前缀匹配 id_rsa 会连 id_rsa.pub、id_ed25519.pub 一起命中。公钥常出现在 ansible、
# 部署文档与测试夹具里，一票否决项不能误伤（AUDIT 原则：误报代价比漏报大，A571 第 3 条）。
PUBLIC_KEY_SUFFIX = ".pub"


def is_public_key(low_name):
    return low_name.endswith(PUBLIC_KEY_SUFFIX)


def norm_allow(allow):
    """把 --allow 的值统一成「仓库根相对、正斜杠、去 ./ 前缀、去尾斜杠」。

    内容扫描与文件名扫描必须同一口径。旧版只在 iter_files 里归一化，`--allow tests/fixtures/`
    与反斜杠写法豁免得了内容、豁免不了夹具里的 id_rsa，于是假阻断（A571 第 5 条）。
    """
    out = []
    for a in allow:
        a = a.replace("\\", "/")
        if a.startswith("./"):
            a = a[2:]
        out.append(a.rstrip("/"))
    return out


def is_allowed(rel, allow):
    return any(rel == a or rel.startswith(a + "/") for a in allow)


def secret_name_hits(root, allow):
    """按文件名找私钥材料。只走目录，不读内容：内容里就是私钥，不该被抄进报告。

    默认口径以前把这类文件只记成盲区，一个入库 id_rsa 的仓库仍然 100 分退 0。
    """
    out = []
    allow = norm_allow(allow)
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for fn in sorted(fns):
            low = fn.lower()
            if is_public_key(low):
                continue
            ext = os.path.splitext(low)[1]
            if (ext not in SECRET_FILE_HARD and low not in SECRET_FILE_HARD_NAMES
                    and not low.startswith(SECRET_KEY_PREFIXES) and low not in AUTH_GATED_NAMES):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/")
            full = os.path.join(dp, fn)
            if is_allowed(rel, allow):
                continue
            hard = (ext in SECRET_FILE_HARD or low in SECRET_FILE_HARD_NAMES
                    or low.startswith(SECRET_KEY_PREFIXES))
            if not hard and low in AUTH_GATED_NAMES:
                hard = bool(auth_marker(full))
            if not hard:
                continue
            note = "私钥材料文件入库（按文件名判定，未读内容）"
            if low in AUTH_GATED_NAMES:
                note = "凭据配置文件含明文凭据（只报标记名，不报值）：" + auth_marker(full)
            out.append({"file": rel, "line": 1, "note": note, "snippet": fn})
    return out


def auth_marker(full):
    """读凭据配置文件，返回第一个「真凭据形态」的标记名；否则空串。只回标记，不回值。

    跳过三种：值为空、值是环境变量引用（`${NPM_TOKEN}`）、值是占位符（`<your-token>`）。
    这三种都是官方推荐写法或示例，不是泄露。
    """
    try:
        with open(full, "rb") as f:
            head = f.read(4096).decode("utf-8", "replace")
    except OSError:
        return ""
    for line in head.splitlines():
        m = AUTH_MARKER_RE.search(line)
        if not m:
            continue
        val = line.split("=", 1)[1].strip().strip("\"'")
        if not val or ENV_REF_RE.match(val) or is_placeholder(val):
            continue
        return m.group(1).split("=")[0].strip()
    return ""


def blind_kind(entry):
    return "excluded" if any(k in entry for k in BY_DESIGN_KEYS) else "unread"


def manifest_gaps(rel, text):
    """清单必填形状检查。不计分，只提示：清单缺字段是发布前置条件，不是漏洞本身。"""
    out = []
    name = os.path.basename(rel)
    if name == "package.json":
        try:
            data = json.loads(text)
        except Exception:
            return [("package.json 解析失败", 1)]
        for field in ("license", "repository"):
            if isinstance(data, dict) and field not in data:
                out.append(("package.json 缺 %s 字段" % field, 1))
    elif name == "plugin.toml":
        have = plugin_toml_keys(text)
        for key in ("id", "name", "version", "entry"):
            if key not in have:
                out.append(("plugin.toml 的 [plugin] 段缺必填 %s" % key, 1))
    return out


def plugin_toml_keys(text):
    """只取 [plugin] 段顶层的键。全文扫键会两头出错：`[plugin_runtime]` 里的同名键会
    掩盖顶层缺项，别的段里的键也会被当成已填。不引第三方 TOML，只做本节需要的分行扫描。
    """
    keys, inside = set(), False
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("["):
            if s.startswith("[["):
                inside = False          # 数组表 [[x]] 不是 [x] 表，键不能算进顶层
            else:
                m = re.match(r"^\[\s*([^\[\]]+)\s*\]", s)
                name_t = m.group(1).strip() if m else ""
                inside = name_t.strip("'\"").lower() == "plugin"
            continue
        if inside:
            m = re.match(r"([A-Za-z0-9_-]+)\s*=", s)
            if m:
                keys.add(m.group(1).lower())
    return keys


def _utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def iter_files(root, include_docs, allow, skips=None, unclear=None):
    """产出 (rel, full)。skips 收集被排除的条目和原因：审计报告要能说明自己没看什么，
    而不是让"0 发现"读起来像全覆盖。

    unclear 是另一本账：真盲区。扩展名不在任何名单里的文件记到这里，不记 skips——
    名单漏一个后缀时报告要响（工单 W05）。
    """
    if skips is None:
        skips = []
    if unclear is None:
        unclear = []
    allow = norm_allow(allow)
    def _walk_error(err):
        # os.walk 默认吞掉不可读目录，那样「扫完了」是假的。
        skips.append("（目录不可读）" + str(getattr(err, "filename", err)))

    for dirpath, dirnames, filenames in os.walk(root, onerror=_walk_error):
        kept = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS:
                rel = os.path.relpath(os.path.join(dirpath, d), root).replace("\\", "/")
                skips.append(rel + "/*（构建/缓存/VCS 目录，整目录跳过）")
            else:
                kept.append(d)
        dirnames[:] = kept
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if is_allowed(rel, allow):
                skips.append(rel + "（--allow 豁免）")
                continue
            ext = os.path.splitext(fn)[1].lower()
            scan_ext = _pool_ext(fn)
            pool = CODE_EXTS | CONFIG_EXTS | (DOC_EXTS if include_docs else set())
            # 无扩展名文件（Dockerfile / Makefile / 无后缀脚本）和 .env* 也进池：明文密钥最常
            # 落在这两类里，旧版只按扩展名过滤，等于对它们失明。
            low = fn.lower()
            # 密钥材料：不读内容，但要记在真盲区那本账上，所以措辞里不能出现 BY_DESIGN_KEYS 的词。
            if scan_ext in SECRET_FILE_EXTS or low in SECRET_FILE_NAMES:
                skips.append(rel + "（密钥材料文件 " + (ext or low) + "，内容不读）")
                continue
            # 清单文件无条件进池：requirements.txt 属 DOC_EXTS，不加 --include-docs 就读不到，
            # 而 git+ 直连依赖最常写在这个文件里。
            # 工单 W18.1：docs/ 下的 .html 与同目录 .md 同口径——默认不参与评分，
            # --include-docs 才读。理由：宿主真加载的面板页不当素材放过（security_audit.py
            # 文件头的设计注释仍然成立），但文档导出页长得像代码不等于它就是运行面。
            # 真凭据仍会被拦：CI 的文档面门禁那一步带 --include-docs，SEC-2 一票否决照判。
            if (not include_docs and scan_ext in (".html", ".htm")
                    and "/docs/" in ("/" + rel.lower())):
                skips.append(rel + "（docs 下的 " + scan_ext + " 按文档形态排除，不在扫描池）")
                continue
            if (scan_ext and scan_ext not in pool and fn not in MANIFEST_NAMES
                    and not low.startswith(".env") and not low.endswith(".env")):
                if scan_ext in ASSET_EXTS or scan_ext in DOC_EXTS:
                    skips.append(rel + "（扩展名 " + scan_ext + " 不在扫描池）")
                else:
                    # 工单 W05：白名单永远可以漏下一个后缀。未分类不能再记成设计内排除，
                    # 否则真盲区恒 0，--fail-unclear 对「藏在没见过的扩展名里」形同无物。
                    # 默认变响：漏一个后缀的代价是 CI 红一次，不是凭据被藏住。
                    unclear.append(rel + "（扩展名 " + scan_ext + " 未分类：这类文件内容没读）")
                continue
            try:
                if os.path.getsize(full) > MAX_BYTES:
                    skips.append(rel + "（超过 2 MiB，未读）")
                    continue
                with open(full, "rb") as f:
                    head = f.read(4096)
                if b"\x00" in head:
                    skips.append(rel + "（含 NUL，按二进制跳过）")
                    continue
            except OSError as e:
                skips.append(rel + "（stat 或 open 失败：" + type(e).__name__ + "）")
                continue
            yield rel, full


def _declaration(line, today=None):
    """这一行有没有有效的点位声明。返回 (ok, note)。

    ok=False 的两种情况分开：没有标记 / 标记不合规或已过期。后者要让调用方知道，
    否则「写了标记但写错格式」会静默回到无豁免状态，人会以为已经豁免过了。
    """
    if not DECLARE_BAD_RE.search(line):
        return None, ""
    m = DECLARE_RE.search(line)
    if not m:
        return False, ("声明格式不合：要 `forge-declared: SEC-n 理由=<非空> 出处=<含 AUDIT.md 的锚点> 复审=YYYY-MM-DD`")
    cid, reason, review = m.group(1), m.group(2).strip(), m.group(4)
    if today is None:
        import datetime
        today = datetime.date.today().isoformat()
    if review < today:                      # ISO 日期可以按字符串比大小
        return False, "声明已过复审期（复审=%s），命中回到账上" % review
    return True, "%s 理由=%s 复审=%s" % (cid, reason, review)


def citation_ok(root, line, cache=None):
    """声明里的「出处」必须能在被审仓库里查到那条记录。查不到就当没有声明。

    外部审 A791 的 CRITICAL，本会话实测复现过：拿扫描器审一个外部插件仓时，
    被审方自己在真 token 那行末尾写一条格式完全合法的声明
   （出处只写 `AUDIT.md#x`），旧判据就把 SEC-2 的一票否决抹成 rc=0 放行——
    因为「出处」以前只校验字符串里含 AUDIT.md 这个文件名，没核对那本账里真写着这件事。
    本仓自扫不受影响：37 条声明都写 `AUDIT.md#sec-declared`，锚点在 AUDIT.md 里真存在。
    fail-closed 是这里的方向：宁可多报，不许被被审对象自己的注释免掉。
    """
    m = DECLARE_RE.search(line)
    if not m:
        return False
    src = m.group(3)
    fname, _, anchor = src.partition("#")
    fname = fname.replace("\\", "/").lstrip("./") or "AUDIT.md"
    cache = declared_cache if declared_cache is not None else {}
    if fname not in cache:
        try:
            with open(os.path.join(root, fname), encoding="utf-8-sig",
                     errors="replace") as f:
                cache[fname] = f.read()
        except OSError:
            cache[fname] = None
    text = cache[fname]
    if text is None:
        return False
    if not anchor:
        # 「出处=AUDIT.md」不带锚点，等于说「账本里某处写过」。这不接受：
        # 被审方只要放一本非空 AUDIT.md 就能免掉命中（A791 CRITICAL 的残留 R1）。
        # 实测本仓 51 处、姊妹仓 32 处声明全都带 #锚点，收紧零回归。
        return False
    return re.search(r"(?<![\w-])" + re.escape(anchor) + r"(?![\w-])", text) is not None


def _covers(cid, line):
    """声明里的 SEC-n 要正好是被命中那一类才管用。

    写 SEC-1 的声明不能顺手豁免 SEC-4：不然一处含糊的标记会把相邻风险一起带走。
    """
    m = DECLARE_RE.search(line)
    return bool(m) and m.group(1) == cid


def _is_self_repo(root):
    """root 是不是本仓库自己。豁免信道按「谁在审谁」分（工单 A791 第 1 条）。

    本仓自扫时审核方 = 被审方，行尾声明照旧生效，51 处声明零回归。
    审外部仓时行尾注释是被审方自己能写的东西——被审对象不能给自己发通行证。
    判据不看路径长相，看被审仓里那份扫描器是不是正在跑的这个文件：
    外部仓就算抄一份同名脚本进来，realpath 也不会等于自己这一份。
    """
    own = os.path.realpath(os.path.abspath(__file__))
    for base in (root, os.path.join(root, "skills", "plugin-forge")):
        if os.path.realpath(os.path.join(base, "scripts", "security_audit.py")) == own:
            return True
    return False


def load_declared_list(path):
    """读审核方控制的点位声明清单（仓库外 JSON）。

    清单不在被审仓库里，被审方写不进去；每条用 SHA-256 绑住那一行的正文，
    改一个字符就失效。少 reviewer / 到期日 / 完整 sha256 的条目直接丢弃，
    不猜、不降级：清单本身坏掉时宁可报「没有豁免」。
    """
    import datetime
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("清单缺 entries 数组")
    today = datetime.date.today().isoformat()
    table = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        rel = str(e.get("file", "")).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        sec = str(e.get("sec", ""))
        digest = str(e.get("sha256", "")).lower()
        who = str(e.get("reviewer", "")).strip()
        expires = str(e.get("expires", ""))
        if not rel or sec not in CHECKS or len(digest) != 64 or not who:
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", expires) or expires < today:
            continue
        table[(rel, sec, digest)] = who
    return table


def _decl_verdict(root, rel, line, honor_inline, declared_list):
    """这一行的声明能不能豁免，返回 (ok, note)。

    信道优先级：审核方清单 > 本仓行尾声明。外部仓没有清单 = 没有豁免。
    """
    ok, note = _declaration(line)
    if not ok:
        return ok, note
    m = DECLARE_RE.search(line)
    cid = m.group(1)
    digest = hashlib.sha256(line.strip().encode("utf-8")).hexdigest()
    if declared_list and (rel, cid, digest) in declared_list:
        return True, ("%s 理由=%s 复审=%s（审核方清单，审核人 %s）"
                      % (cid, m.group(2).strip(), m.group(4),
                         declared_list[(rel, cid, digest)]))
    if not honor_inline:
        return False, ("外部仓库的行尾声明不生效（%s）：豁免只能来自审核方 "
                       "--declared-list 清单" % cid)
    if not citation_ok(root, line):
        return False, "出处在被审仓库里查无记录（%s）" % m.group(3)
    return True, note


declared_cache = None


def scan(root, include_docs, allow, declared=None, honor_inline=None, declared_list=None):
    findings = {c: [] for c in CHECKS}
    seen = set()
    scanned = 0
    skips = []
    unclear = []
    adv = []
    global declared_cache
    declared_cache = {}          # 每轮扫描重新计，免得跨仓复用出处文本
    if honor_inline is None:
        honor_inline = _is_self_repo(root)
    for rel, full in iter_files(root, include_docs, allow, skips, unclear):
        scanned += 1
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            skips.append(rel + "（读取失败：" + type(e).__name__ + "）")
            continue
        if looks_minified(lines):
            unclear.append(rel + "（疑似压缩/混淆：行级规则读不动）")
        if os.path.basename(full) in MANIFEST_NAMES:
            for i2, l2 in enumerate(lines, 1):
                for pat5, note5 in SEC5:
                    if re.search(pat5, l2):
                        adv.append({"file": rel, "line": i2, "note": note5,
                                    "snippet": l2.strip()[:140]})
            for note6, i3 in manifest_gaps(rel, chr(10).join(lines)):
                adv.append({"file": rel, "line": i3, "note": note6, "snippet": ""})
        for i, line in enumerate(lines, 1):
            ok_dec, note_dec = _decl_verdict(root, rel, line, honor_inline, declared_list)
            if ok_dec is False and declared is not None:
                declared["bad"].append({"file": rel, "line": i, "why": note_dec,
                                        "snippet": line.strip()[:140]})
            # 不再把行截断到 500 字符：压缩过的 JS 里，密钥与 eval 往往就在行尾。
            for cid, table in TABLES:
                for pat, note in table:
                    for mo in re.finditer(pat, line):
                        if declared is not None and ok_dec and _covers(cid, line):
                            declared.setdefault("ok", []).append(
                                {"file": rel, "line": i, "note": note_dec,
                                 "hit": note})
                            continue
                        if cid == "SEC-2" and is_placeholder(mo.group(1) if mo.groups() else mo.group(0)):
                            continue
                        key = (rel, i, cid, note)
                        if key in seen:
                            continue
                        seen.add(key)
                        findings[cid].append({"file": rel, "line": i, "note": note,
                                              "snippet": line.strip()[:140]})
            for _col, skey, sval in secret_assignments(line):
                note2 = "password/secret 明文赋值（" + skey + "）"
                # 同一条声明在这一行对所有 SEC-2 路子都要管用：赋值判定与表判定是两条道，
                # 只豁免一条道会让人看见「声明写了却没生效」。
                if ok_dec and _covers("SEC-2", line):
                    if declared is not None:
                        declared.setdefault("ok", []).append(
                            {"file": rel, "line": i, "note": note_dec, "hit": note2})
                    continue
                k2 = (rel, i, "SEC-2", note2)
                if k2 in seen:
                    continue
                seen.add(k2)
                findings["SEC-2"].append({"file": rel, "line": i, "note": note2,
                                          "snippet": line.strip()[:140]})
    for h in secret_name_hits(root, allow):
        findings["SEC-2"].append(h)
    return scanned, findings, skips, unclear, adv


def score(findings):
    s = {}
    s["SEC-2"] = 25 if len(findings["SEC-2"]) == 0 else (10 if len(findings["SEC-2"]) <= 2 else 0)
    for c in ("SEC-1", "SEC-3", "SEC-4"):
        n = len(findings[c])
        s[c] = 25 if n == 0 else 20 if n <= 3 else 10 if n <= 10 else 0
    total = sum(s.values())
    if findings["SEC-2"] or total < 50:
        verdict, code = "阻断", 2
    elif total < 90:
        verdict, code = "修完再放", 1
    else:
        verdict, code = "放行", 0
    return s, total, verdict, code


def _self_test():
    """正例反例各一条，钉住 SEC-1/SEC-2 的实际匹配面（CI 跑，见 sync-check.yml）。"""
    B = chr(92)

    # 直接由 TABLES 推出来。手写第二份 id→表 的映射会在新增一档时静默漏掉那一档。
    TABLES_BY_ID = dict(TABLES)

    def hit(cid, line):
        for pat, _note in TABLES_BY_ID[cid]:
            for mo in re.finditer(pat, line):
                if cid != "SEC-2":
                    return True
                v = mo.group(1) if mo.groups() else mo.group(0)
                if not is_placeholder(v):
                    return True
        if cid == "SEC-2" and secret_assignments(line):
            return True
        return False  # forge-declared: SEC-3 理由=自检夹具的假 shell 调用 出处=AUDIT.md#sec-declared 复审=2026-12-15

    # 夹具一律拆写：本文件会被自己扫，字面量会让 SEC-2 在一所干净的仓库上把自己否决。
    K = "api" + "_key"
    TK = "ACCESS" + "_TOKEN"
    V = "abcdefgh" + "ijkl12"
    USR = "Users"
    C1 = "C:" + B + USR + B + "Xiao8"
    C2 = "C:" + B + B + USR + B + B + "Bob"
    C3 = "C:" + "/" + USR + "/" + "alice"
    cases = [  # forge-declared: SEC-3 理由=自检夹具的假 shell 调用 出处=AUDIT.md#sec-declared 复审=2026-12-15
        ("SEC-1", 'p = r"' + C1 + B + 'proj"', True),
        ("SEC-1", 'x = "' + C2 + '"', True),  # forge-declared: SEC-4 理由=自检夹具的假下载命令 出处=AUDIT.md#sec-declared 复审=2026-12-15
        ("SEC-1", 'p = "' + C3 + '/x"', True),
        ("SEC-1", 'p = r"C:' + B + USR + B + '<user>' + B + 'x"', False),
        ("SEC-1", 'p = r"C:' + B + USR + B + 'Public' + B + 'x"', False),
        ("SEC-1", 'p = r"C:' + B + USR + B + 'Default' + B + 'x"', False),
        ("SEC-1", 'p = r"C:' + B + USR + B + 'All Users' + B + 'x"', False),
        ("SEC-2", '{' + '"' + K + '"' + ': ' + '"' + V + '"' + '}', True),
        ("SEC-2", K + "=" + V, True),
        ("SEC-2", K.upper() + ' = "' + V + '"', True),
        ("SEC-2", K.upper() + ' = "your_' + "secret" + '_here1"', False),
        ("SEC-2", K + ' = "' + "changeme" + '"', False),
        ("SEC-2", TK + ' = "' + "x" * 15 + '"', False),
        # Objective-C / C# 的 @"..." 字面量。.m 进池后实测到这条漏网：值前多一个 @，旧模式整个不匹配。
        ("SEC-2", "NSString *token = @\"" + V + '";', True),
        ("SEC-2", "NSString *token = @\"sh" + '";', False),
        ("SEC-2", "the pass" + "word field is documented only", False),
        # 驼峰、粘连、裸 token、URL 内嵌凭据：旧的万能正则四种全漏。
        ("SEC-2", 'user' + 'Password = "' + V + '"', True),
        ("SEC-2", 'openai' + 'ApiKey = "' + V + '"', True),
        ("SEC-2", 'db' + 'password = "' + V + '"', True),
        ("SEC-2", "BOT" + "_" + "TOKEN=" + V, True),
        ("SEC-2", "refresh" + "_token: " + chr(39) + V + chr(39), True),
        ("SEC-2", 'u = "postgres://' + "user:" + V + '@db/x"', True),
        # 右侧是代码表达式而不是密钥：本文件自己就有一行这样的。
        ("SEC-2", 'SECRET' + "_ENV_RE = re.com" + "pile(", False),
        ("SEC-2", 'api' + "_key = " + chr(39) + "abcdefghij" + chr(39), True),
        ("SEC-2", 'api' + "_key = " + chr(39) + "abc" + chr(39), False),
        # 前缀名：下划线算单词字符，旧的 \b 会在 OPENAI_ / DB_ / AWS_ 这里失配。
        ("SEC-2", "OPENAI" + "_" + "API" + "_KEY=" + V, True),
        ("SEC-2", "DB" + "_" + "PASSWORD" + "=" + V, True),
        ("SEC-2", "AWS" + "_" + "SECRET" + "_ACCESS" + "_" + "KEY" + " = '" + V + "'", True),
        ("SEC-2", "MY" + "_" + "SECRET=" + V, True),
        ("SEC-2", "pass" + "phrase_length = \"" + V + "\"", True),   # 拆词后 passphrase 算密钥名
        ("SEC-3", "subprocess.run(cmd, " + "shel" + "l=True)", True),
        # 2026-11-15 补：这五种形状以前漏，仓库里真 unsafe 的代码会被打满分放行
        ("SEC-3", "out = os." + "popen(cmd).read()", True),
        ("SEC-3", "out = subprocess." + "getoutput(cmd)", True),
        ("SEC-3", "rc, out = subprocess." + "getstatusoutput(cmd)", True),
        ("SEC-3", "obj = marshal." + "loads(blob)", True),
        ("SEC-3", "data = yaml." + "unsafe_load(text)", True),
        # 反例：显式 safe_load 与显式 Popen 列表调用不算
        ("SEC-3", "data = yaml." + "safe_load(text)", False),
        ("SEC-3", "subprocess." + "Popen([cmd, arg])", False),
        ("SEC-3", "comment mentions shel" + "l=True only", True),
        ("SEC-3", "we do not shell=" + "\" out here", False),
        ("SEC-4", "cur" + "l -sS https://x.sh | " + "sh", True),
        ("SEC-4", "普通说明文字，没有取回动作", False),
    ]
    sec5_cases = [
        ('  "post' + 'install": "node gyp rebuild"', True),
        ("install" + "_requires = " + chr(34) + "git+https://x/y.git" + chr(34), True),
        ("normal" + "_script = " + chr(34) + "build" + chr(34), False),
    ]
    s5_bad = [(l, w) for l, w in sec5_cases
              if any(re.search(p, l) for p, _n in SEC5) != w]
    for l, w in s5_bad:
        print("SELF-TEST FAIL: SEC-5 want=%s %r" % (w, l))
    # 核对报告点名的误报与漏报，逐条钉住
    extra = [
        ("SEC-2", 'password = args.' + 'password', False),          # 属性路径
        ("SEC-2", 'self.password = ' + 'password', False),          # 自引用
        ("SEC-2", 'max_' + 'tokens=MAX_TOKENS', False),             # 全大写常量
        ("SEC-2", 'token_' + 'count = 1234567890', False),          # 纯数字
        ("SEC-2", 'token_' + 'url = "https://x.example/token"', False),
        ("SEC-2", 'def f(pass' + 'word: Optional[str] = None)', False),
        ("SEC-2", 'x-api-' + 'key: abcdefghijkl12', True),          # 连字符形态
        ("SEC-2", 'DB_PA' + 'SS=abcdefghijkl12', True),             # 缩写 pass
        ("SEC-2", 'ALIYUN_ACCESS_' + 'KEY=abcdefghijkl12', True),   # 前缀 + 中置
        ("SEC-2", 'p = "redis://:abcdefgh' + 'ijkl1@host"', True),  # 无用户名
        ("SEC-2", 'p = "postgres://u:${DB_' + 'PASSWORD}@db"', False),
        ("SEC-2", 'p = "postgres://u:<' + 'password>@db"', False),  # 模板占位不是密钥
        # 工单 W14：右值字符类要认散文的边界。这三条钉住修的两侧——
        # 文档示例不再被误报，但真字面量混在中文句子里仍要抓到。
        ("SEC-2", '反引号写法 `max_' + 'tokens=MAX_TOKENS`）、类型标注', False),
        ("SEC-2", 'DB_PA' + 'SS=`、`AWS_SECRET_ACCESS_KEY=`、`MY_SECRET=`', False),
        ("SEC-2", '说明文字 password = "' + 'abcdefghijkl12" 这样', True),
        ("SEC-2", '`self.pass' + 'word = password` 属于形参', False),
    ]
    e_bad = [(l, w) for _cid, l, w in extra if hit("SEC-2", l) != w]
    for l, w in e_bad:
        print("SELF-TEST FAIL: SEC-2 want=%s got=%s %r" % (w, hit("SEC-2", l), l))

    helper_bad = []
    if not looks_minified(["var a=1;" + "b" * 600]):
        helper_bad.append("单行压缩 bundle 没被判为盲区")
    if looks_minified(["a=1", "b=2"]):
        helper_bad.append("普通短文件被误判为压缩盲区")
    if blind_kind("x.js/*（构建/缓存/VCS 目录，整目录跳过）") != "excluded":
        helper_bad.append("构建目录应算设计内排除")
    if blind_kind("big.json（超过 2 MiB，未读）") != "unread":
        helper_bad.append("超大文件应算真盲区")
    if blind_kind("srv.pem（密钥材料文件 .pem，内容不读）") != "unread":
        helper_bad.append("密钥材料文件必须算真盲区，不能算设计内排除")
    if ".pem" in CODE_EXTS | CONFIG_EXTS | DOC_EXTS:
        helper_bad.append(".pem 不该进可读池：里面的私钥会被抄进报告")
    # 端到端：真造一个目录跑 iter_files / scan，而不是只喂字面量给 blind_kind。
    # 夹具里的密钥形状一律拆写（见 :400 那条规则）——这个文件会被自己扫，
    # 完整的 "-----BEGIN … PRIVATE KEY-----" 或 "password = …" 字面量会让自己被一票否决。
    e2e = tempfile.mkdtemp(prefix="forge-audit-e2e-")
    def _w(name, text):
        d = os.path.dirname(os.path.join(e2e, name))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(e2e, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    _w("id_ed25519.bak", "renamed key material\n")
    _w("id_rsa.pub", "ssh-rsa AAAAB3NzaC1yc2 public half\n")
    _w("app.jks", "binary-ish\n")
    _w(".npmrc", "registry=https://registry.npmjs.org\n")
    _w(".pypirc", "[distutils]" + chr(10) + "pass" + "word = hunter2" + chr(10))
    # 同一个 .npmrc 标记名，三种值：官方推荐的引用写法、占位符、真令牌。只有第三种该阻断。
    _w("cfg_env/.npmrc", "//registry.npmjs.org/:" + "_auth" + "Token=" + "${NPM_" + "TOKEN}" + chr(10))
    _w("cfg_ph/.npmrc", "//registry.npmjs.org/:" + "_auth" + "Token=" + "<your-token>" + chr(10))
    _w("cfg_real/.npmrc", "//registry.npmjs.org/:" + "_auth" + "Token=" + "abcdefgh" + "ijkl1234" + chr(10))
    with open(os.path.join(e2e, "srv.pem"), "w", encoding="utf-8") as f:
        f.write("-----BEGIN " + "RSA PRIVATE KEY-----" + chr(10) + "MIIB" + chr(10))
    with open(os.path.join(e2e, "id_rsa"), "w", encoding="utf-8") as f:
        f.write("-----BEGIN " + "OPENSSH PRIVATE KEY-----" + chr(10))
    try:
        _sc, _fd, _sk, _un, _ad = scan(e2e, False, [])
    finally:
        shutil.rmtree(e2e, ignore_errors=True)   # 自检不留一堆带私钥的临时目录
    if os.path.exists(e2e):
        helper_bad.append("自检临时目录没清掉：" + e2e)
    sec2 = {h["file"] for h in _fd["SEC-2"]}
    if "id_ed25519.bak" not in sec2:
        helper_bad.append("改名后的 id_ed25519.bak 没被前缀匹配命中")
    if ".pypirc" not in sec2:
        helper_bad.append(".pypirc 写了 password 却没计 SEC-2")
    if ".npmrc" in sec2:
        helper_bad.append("只含 registry= 的 .npmrc 被误计成 SEC-2（一票否决项不该误报）")
    if "app.jks" in sec2:
        helper_bad.append(".jks 已退回盲区，不该再计 SEC-2")
    # A571 第 3 条：前缀匹配不能把公钥一起否决
    if "id_rsa.pub" in sec2:
        helper_bad.append("id_rsa.pub 是公钥，不该按私钥一票否决")
    if not any(h["file"] == "id_ed25519.bak" for h in _fd["SEC-2"]):
        helper_bad.append("排除 .pub 之后，id_ed25519.bak 仍要被前缀匹配命中")
    # A571 第 4 条：同一个 _authToken 标记，值形态不同结论就不同
    if "cfg_env/.npmrc" in sec2:
        helper_bad.append("_authToken=${NPM_TOKEN} 是 npm 官方推荐写法，不该计 SEC-2")
    if "cfg_ph/.npmrc" in sec2:
        helper_bad.append("_authToken=<your-token> 是占位符，不该计 SEC-2")
    if "cfg_real/.npmrc" not in sec2:
        helper_bad.append(".npmrc 里写了真令牌却没计 SEC-2（占位符过滤不能把真值也吃掉）")
    if blind_kind("srv.pem（密钥材料文件 .pem，内容不读）") == "excluded" \
            or not any("密钥材料" in s for s in _sk):
        helper_bad.append("iter_files 没把 .pem 记成密钥材料盲区：%s" % _sk)
    if not any(h["file"] == "id_rsa" for h in _fd["SEC-2"]):
        helper_bad.append("id_rsa 按文件名没计进 SEC-2，入库私钥仍会被放行")
    # A571 第 5 条：--allow 的三种写法在文件名那一遍必须同样生效。旧版只在 iter_files 里
    # 归一化，./ 前缀、尾斜杠、反斜杠都豁免得了内容、豁免不了夹具里的 id_rsa。
    fx = tempfile.mkdtemp(prefix="forge-audit-allow-")
    try:
        os.makedirs(os.path.join(fx, "tests", "fixtures"), exist_ok=True)
        with open(os.path.join(fx, "tests", "fixtures", "id_rsa"), "w", encoding="utf-8") as f:
            f.write("-----BEGIN " + "OPENSSH PRIVATE KEY-----" + chr(10))
        for spelling in ("./tests/fixtures/", "tests" + B + "fixtures",
                         "tests" + B + "fixtures" + B, "tests/fixtures"):
            _s3, _f3, _k3, _u3, _a3 = scan(fx, False, [spelling])
            if _f3["SEC-2"]:
                helper_bad.append("--allow %r 没豁免掉夹具里的 id_rsa：%s"
                                  % (spelling, [x["file"] for x in _f3["SEC-2"]]))
        _s4, _f4, _k4, _u4, _a4 = scan(fx, False, ["tests/other"])
        if not any(x["file"] == "tests/fixtures/id_rsa" for x in _f4["SEC-2"]):
            helper_bad.append("豁免写错目录时，夹具里的 id_rsa 应计 SEC-2 却没有：%s" % _f4["SEC-2"])
    except OSError as e:
        helper_bad.append("--allow 归一化自检没跑成：" + type(e).__name__)
    finally:
        shutil.rmtree(fx, ignore_errors=True)
    # 自扫护栏：把本文件复制进临时目录扫一遍。夹具里若留了未拆写的密钥字面量，
    # 本文件会被自己一票否决（A472 第 1 条就是这么被抓的，当时只有手工扫才发现）。
    own = tempfile.mkdtemp(prefix="forge-audit-own-")
    try:
        shutil.copy(__file__, os.path.join(own, "security_audit.py"))
        _s2, _f2, _k2, _u2, _a2 = scan(own, False, [])
        if _f2["SEC-2"]:
            helper_bad.append("本文件出现会被自己判 SEC-2 的字面量（夹具要拆写）：%s"
                              % [(x["line"], x["note"]) for x in _f2["SEC-2"]][:4])
    except OSError as e:
        helper_bad.append("自扫护栏没跑成：" + type(e).__name__)
    finally:
        shutil.rmtree(own, ignore_errors=True)
    if len(manifest_gaps("package.json", '{"name":"x"}')) < 2:
        helper_bad.append("package.json 缺 license/repository 没报出来")
    # 池子覆盖面：门禁当受保护文件的那四类扩展名，扫描器必须真读它们的内容。
    # 以前只门禁认、扫描器不读，密钥与动态执行写进 .svg/.htm/.m/.jl 就等于不存在。
    # 触发词一律拆写：本文件既要自扫，也要被仓库扫描扫到，整串会把自己判进去（A472 第 1 条口径）。
    px = tempfile.mkdtemp(prefix="forge-audit-pool-")
    try:
        xf = {os.path.join("assets", "icon.svg"): '<svg><script>fet' + 'ch("/api/market", {method:"POST"});</script></svg>\n',
              "page.htm": "var h = ev" + "al(location.hash);\n",
              os.path.join("src", "run.jl"): "run(`cur" + "l -s http://x`)\nconst val PASS = \"Sup3rS3" + "cretPass\"\n",
              os.path.join("src", "App.m"): "#import <Foundation/Foundation.h>\n@implementation Demo\n@end\n",
              # 2026-11-17 终检 sweep 第 12/13 条：这四类以前两边都不读
              "page.html": "var h = ev" + "al(location.hash);\n",
              os.path.join("tools", "legacy.vbs"): "h = ev" + "al(input)\n",
              os.path.join("sh", "install.ksh"): "ev" + "al \"$1\"\n",
              os.path.join("mkdir", "rules.mk"): "all:\n\tev" + "al $(SRC)\n"}
        for rel, body in xf.items():
            p = os.path.join(px, rel)
            os.makedirs(os.path.dirname(p) or px, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
        _s5, _f5, _k5, _u5, _a5 = scan(px, False, [])
        if _s5 != len(xf):
            helper_bad.append("八类扩展名应读进 %d 个文件，实得 %d（池子里缺哪一类？）"
                              % (len(xf), _s5))
        for cid, fname in (("SEC-4", "assets/icon.svg"), ("SEC-3", "page.htm"),
                           ("SEC-2", "src" + "/" + "run.jl"), ("SEC-3", "page.html")):
            if not any(x["file"] == fname for x in _f5[cid]):
                helper_bad.append("%s 没在 %s 上报出来，那类文件在池子里读不到内容" % (cid, fname))
        if ".html" in DOC_EXTS:
            helper_bad.append(".html 同时在两个池子里：一个名字要归一处，否则读没读说不清")
    except OSError as e:
        helper_bad.append("池子覆盖面用例没跑成：" + type(e).__name__)
    finally:
        shutil.rmtree(px, ignore_errors=True)
    # 探针的上游源码镜像必须整目录跳过：那些文件是别人仓库的正文，
    # 里面的示例 token 会记到本仓库头上（终检 sweep 第 21 条）。
    pd_ = tempfile.mkdtemp(prefix="forge-audit-cache-")
    try:
        for rel, body in ((os.path.join("scripts", ".drift-cache", "settings.py"),
                           "var h = ev" + "al(upstream)\n"),
                          ("leak.html", "var h = ev" + "al(mine)\n")):
            p = os.path.join(pd_, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
        _s6, _f6, _k6, _u6, _a6 = scan(pd_, False, [])
        if _s6 != 1:
            helper_bad.append(".drift-cache 没被整目录跳过：应只读 1 个文件，实读 %d" % _s6)
        if not all(not x["file"].startswith("scripts/") for x in _f6["SEC-3"]):
            helper_bad.append(".drift-cache 里的内容还是被报了")
    except OSError as e:
        helper_bad.append(".drift-cache 用例没跑成：" + type(e).__name__)
    finally:
        shutil.rmtree(pd_, ignore_errors=True)
    # plugin.toml 必填要按节判定：子段里的同名键不能顶掉顶层缺项
    nested = "[plugin]\nid = \"a\"\nname = \"n\"\nversion = \"1.0.0\"\n\n[plugin_runtime]\nentry = 1\n"
    if not any("entry" in m for m, _l in manifest_gaps("plugin.toml", nested)):
        helper_bad.append("[plugin] 缺 entry 被子段同名键掩盖，没报出来")
    complete = "[plugin]\nid = \"a\"\nname = \"n\"\nversion = \"1.0.0\"\nentry = \"m:C\"\n"
    if manifest_gaps("plugin.toml", complete):
        helper_bad.append("字段齐全的 plugin.toml 被误报")
    # 段头的四种刁钻写法：数组表、点号子段、行尾注释、带引号段名
    if plugin_toml_keys("[[plugin]]\nid = \"a\"\n"):
        helper_bad.append("数组表 [[plugin]] 被当成 [plugin] 表收键")
    if "name" in plugin_toml_keys("[plugin]\nid = \"a\"\n\n[plugin.sub]\nname = \"n\"\n"):
        helper_bad.append("点号子段 [plugin.sub] 的键被算进顶层")
    if "entry" in plugin_toml_keys("[[users]]\nentry = 1\n"):
        helper_bad.append("[[users]] 段头解析出错，键漏进了判定")
    if plugin_toml_keys("[plugin]  # 段说明\nid = \"a\"  # 必填\nname = \"n\"\n"
                        "version = \"1.0.0\"\nentry = \"m:C\"\n") != {"id", "name", "version", "entry"}:
        helper_bad.append("行尾注释让 [plugin] 段解析失效")
    if "id" not in plugin_toml_keys("[\"plugin\"]\nid = \"a\"\n"):
        helper_bad.append("带引号的段名 [\"plugin\"] 没认出来")
    # 占位符两档：默认吃掉 example1234567，严格档要放行
    global STRICT_PLACEHOLDER
    STRICT_PLACEHOLDER = False
    if not is_placeholder("exam" + "ple1234567"):
        helper_bad.append("默认档应把 example1234567 当占位符")
    STRICT_PLACEHOLDER = True
    if is_placeholder("exam" + "ple1234567"):
        helper_bad.append("严格档不应再把 example1234567 当占位符")
    if not is_placeholder("exam" + "ple_key"):
        helper_bad.append("严格档仍应把 example_key 这类明显示例当占位符")
    STRICT_PLACEHOLDER = False
    if manifest_gaps("package.json", '{"license":"MIT","repository":{}}'):
        helper_bad.append("字段齐全却被 SEC-5 误报")
    # 评分档与退出码：以前自检从不跑 score()，而清单里那张表就是从 score() 抄的。
    # 2026-11-17 终检 sweep 第 2/5 条——文档写的 25/0 两档其实还有中间的 10 分档。
    def _f(sec2=0, sec1=0, sec3=0, sec4=0):
        return {c: [("x", i) for i in range(n)] for c, n in
                (("SEC-1", sec1), ("SEC-2", sec2), ("SEC-3", sec3), ("SEC-4", sec4))}
    for n2, want2, want_total, want_code in ((0, 25, 100, 0), (1, 10, 85, 2), (2, 10, 85, 2),
                                             (3, 0, 75, 2)):
        s2, t2, v2, c2 = score(_f(sec2=n2))
        if (s2["SEC-2"], t2, c2) != (want2, want_total, want_code):
            helper_bad.append("SEC-2 %d 处应得 (%s, %s, 退出 %s)，实得 (%s, %s, %s)"
                              % (n2, want2, want_total, want_code, s2["SEC-2"], t2, c2))
    for n1, want1 in ((0, 25), (3, 20), (4, 10), (10, 10), (11, 0)):
        s3 = score(_f(sec1=n1))[0]
        if s3["SEC-1"] != want1:
            helper_bad.append("SEC-1 %d 处应得 %d 分，实得 %d" % (n1, want1, s3["SEC-1"]))
    # 50–89 这一段是「修完再放」（退出 1）：SEC-1 与 SEC-3 各 5 处 → 10+10+25+25=70
    mid = score(_f(sec1=5, sec3=5))
    if (mid[1], mid[3]) != (70, 1):
        helper_bad.append("总分 70 应判「修完再放」退 1，实得 %s/%s" % (mid[1], mid[3]))
    # 退出码走真进程：main() 的映射（空扫描=3、--fail-unclear=3）不在 score() 里，
    # 只测 score() 就还会有一格假绿。密钥形状在夹具里拼写，本文件不自扫自阻断。
    def _run(extra, target):
        r = subprocess.run([sys.executable, os.path.abspath(__file__), target, "--json"] + extra,
                           capture_output=True, text=True, timeout=180)
        try:
            return r.returncode, json.loads(r.stdout)
        except ValueError:
            return r.returncode, {}
    for mk in ("empty", "secret", "blind", "pool", "html-doc"):
        d = tempfile.mkdtemp(prefix="forge-audit-exit-" + mk + "-")
        try:
            if mk == "secret":
                with open(os.path.join(d, "leak.py"), "w", encoding="utf-8") as f:
                    f.write('KEY = "' + "ghp_" + "A" * 36 + '"' + chr(10))
                rc, body = _run([], d)
                hits = body.get("findings", {}).get("SEC-2") or []
                if rc != 2 or not hits:
                    helper_bad.append("夹具里放一条 GitHub token 形状：应 SEC-2 阻断退 2，实得 rc=%s 命中 %d"
                                      % (rc, len(hits)))
            elif mk == "html-doc":
                # 工单 W18.1：同一段纯文档教程，改后缀不许改变结论。
                # .md 与 docs/*.html 在默认口径都不参与评分；带 --include-docs 都要读，
                # 真凭据在两种后缀下一票否决。
                L = chr(10)
                text = ("# 教程" + L + L + "示例密钥 " + "AKIA" + "IOSFODNN7EXAMPLE"
                        + " 与讲解代码 ev" + "al(x) 的写法。" + L)
                os.makedirs(os.path.join(d, "docs"), exist_ok=True)
                open(os.path.join(d, "app.py"), "w", encoding="utf-8", newline=L).write(
                    "print(1)" + L)
                open(os.path.join(d, "docs", "tutor.md"), "w", encoding="utf-8",
                        newline=L).write(text)
                open(os.path.join(d, "docs", "tutor.html"), "w", encoding="utf-8",
                        newline=L).write(text.replace("# 教程", "<h1>教程</h1>"))
                rc_m, body_m = _run([], d)
                rc_f, body_f = _run(["--include-docs"], d)
                if rc_m != 0:
                    helper_bad.append("docs 下的 .html 在默认口径仍被评分：rc=%s counts=%s"
                                      % (rc_m, body_m.get("counts")))
                if body_f.get("counts", {}).get("SEC-2") != 2:
                    helper_bad.append("--include-docs 下两种后缀的示例密钥数应一致（2 处），实得 %s"
                                      % (body_f.get("counts"),))
                # 非 docs 目录的面板页仍要当代码：设计注释那句不许被这次豁免冲掉
                open(os.path.join(d, "ui"), "w") if False else os.makedirs(
                    os.path.join(d, "ui"), exist_ok=True)
                open(os.path.join(d, "ui", "panel.html"), "w", encoding="utf-8",
                        newline=L).write(text.replace("# 教程", "<h1>面板</h1>"))
                rc_u, body_u = _run([], d)
                if rc_u == 0:
                    helper_bad.append("ui/panel.html（宿主真加载的页）被 docs 豁免顺带放走了")
            elif mk == "blind":
                with open(os.path.join(d, "ok.py"), "w", encoding="utf-8") as f:
                    f.write("print(1)" + chr(10))
                with open(os.path.join(d, "nul.py"), "wb") as f:
                    f.write(b"x = 1" + bytes([0]) + b"y = 2")
                rc, body = _run([], d)
                rc_f, body_f = _run(["--fail-unclear"], d)
                if rc != 0 or rc_f != 3:
                    helper_bad.append("含 NUL 的 .py：默认应退 0，--fail-unclear 应退 3，实得 %s/%s"
                                      % (rc, rc_f))
                ledger = " ".join(body_f.get("skips", []) + body_f.get("unclear", [])
                                  + [x.get("file", "") if isinstance(x, dict) else str(x)
                                     for x in body_f.get("unclear", [])])
                if "nul.py" not in ledger:
                    helper_bad.append("没读动的文件没在账上点名：%s" % ledger[:120])
                # 超过 2 MiB 的文件同一条规矩：没读就要点名，不许只报一个总数
                with open(os.path.join(d, "huge.py"), "wb") as f:
                    f.write(b"# pad\n" * 400000)          # 约 3.2 MiB，过 MAX_BYTES
                rc_h, body_h = _run(["--fail-unclear"], d)
                h_ledger = " ".join(body_h.get("skips", []) + list(body_h.get("unread_files", [])))
                if "huge.py" not in h_ledger:
                    helper_bad.append("超大文件没进未读账：%s" % h_ledger[:160])
                if rc_h != 3:
                    helper_bad.append("有真盲区时 --fail-unclear 应退 3，实得 %s" % rc_h)
            elif mk == "pool":
                # 工单 W05 的四种形状：每种都要有自己的判据，缺一条就回到静默放行
                with open(os.path.join(d, "ok.py"), "w", encoding="utf-8") as f:
                    f.write("print(1)" + chr(10))
                tok = "ghp_" + "B" * 36
                # (1) 备份后缀：真扩展名在里面，必须按 .py 读到内容
                with open(os.path.join(d, "settings.py.bak"), "w", encoding="utf-8") as f:
                    f.write('API_KEY = "' + tok + '"' + chr(10))
                rc, body = _run([], d)
                hits = body.get("findings", {}).get("SEC-2") or []
                if rc != 2 or not any("settings.py.bak" in h.get("file", "")
                                      for h in hits if isinstance(h, dict)):
                    helper_bad.append("备份后缀里的 token 没被 SEC-2 抓到：rc=%s 命中 %s"
                                      % (rc, str(hits)[:90]))
                os.remove(os.path.join(d, "settings.py.bak"))
                # (2) Windows 脚本宿主：进代码池，同一条 token 要抓到
                with open(os.path.join(d, "report.wsf"), "w", encoding="utf-8") as f:
                    f.write('<job><script language="VBScript">' + chr(10)
                            + 'API_KEY = "' + tok + '"' + chr(10) + '</script></job>' + chr(10))
                rc, body = _run([], d)
                hits = body.get("findings", {}).get("SEC-2") or []
                if rc != 2 or not any("report.wsf" in h.get("file", "")
                                      for h in hits if isinstance(h, dict)):
                    helper_bad.append("名单外脚本家族的 token 没被 SEC-2 抓到：rc=%s 命中 %s"
                                      % (rc, str(hits)[:90]))
                os.remove(os.path.join(d, "report.wsf"))
                # (3) 未分类后缀：内容读不了就得记真盲区，--fail-unclear 要响
                with open(os.path.join(d, "notes.qqq"), "w", encoding="utf-8") as f:
                    f.write("nothing to see" + chr(10))
                rc, body = _run([], d)
                rc_f, body_f = _run(["--fail-unclear"], d)
                ledger = " ".join([str(x) for x in (body_f.get("unclear") or [])])
                if rc_f != 3 or "notes.qqq" not in ledger:
                    helper_bad.append("未分类扩展名没记成真盲区（默认 rc=%s，--fail-unclear rc=%s，"
                                      "账上：%s）" % (rc, rc_f, ledger[:120]))
                os.remove(os.path.join(d, "notes.qqq"))
                # (4) 素材与归档：设计内排除，不许把上面那条判据顶成常红
                os.makedirs(os.path.join(d, "assets"), exist_ok=True)
                with open(os.path.join(d, "assets", "icon.png"), "wb") as f:
                    f.write(bytes([137, 80, 78, 71]) + bytes(range(256)) * 2)
                rc_a, body_a = _run(["--fail-unclear"], d)
                if rc_a != 0:
                    helper_bad.append("素材文件被当成真盲区（应设计内排除）：rc=%s %s"
                                      % (rc_a, str(body_a.get("unclear"))[:120]))
            else:
                rc, body = _run([], d)
                if (rc, body.get("scanned_files")) != (3, 0):
                    helper_bad.append("空目录应报「空扫描」退 3，实得 rc=%s scanned=%s"
                                      % (rc, body.get("scanned_files")))
        except OSError as e:
            helper_bad.append("退出码用例（%s）没跑成：" % mk + type(e).__name__)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    # 点位声明：格式、复审期、类别三段都得对，错一段就回到无豁免状态
    dec_lines = [
        ("x = 1  # forge-" + "declared: SEC-4 理由=只读拉取 出处=AUDIT.md#s 复审=2099-01-01",
         True, ""),
        ("x = 1  # forge-" + "declared: SEC-4 理由=只读拉取 出处=AUDIT.md#s",
         False, "格式不合"),
        ("x = 1  # forge-" + "declared: SEC-4 理由= 出处=AUDIT.md#s 复审=2099-01-01",
         False, "格式不合"),
        ("x = 1  # forge-" + "declared: SEC-4 理由=只读拉取 出处=AUDIT.md#s 复审=2020-01-01",
         False, "已过复审期"),
        ("x = 1  # forge-" + "declared: SEC-2 理由=示例口令 出处=AUDIT.md#s 复审=2099-01-01",
         True, ""),
        # 文档没有 # 注释语法，点位声明必须认 HTML 注释这一种写法（工单 W14：
        # 历史条目与教学示例不许为过门禁改写文字，声明是唯一的正路）
        ("password = \"abcdefghijkl12\" <!-- forge-" + "declared: SEC-2 理由=教学示例 出处=AUDIT.md#s 复审=2099-01-01 -->",
         True, ""),
        ("password = \"abcdefghijkl12\" <!-- forge-" + "declared: SEC-2 理由=缺出处 复审=2099-01-01 -->",
         False, "格式不合"),
    ]
    for line, want_ok, want_why in dec_lines:
        ok_d, note_d = _declaration(line)
        if (ok_d is True) != want_ok:
            helper_bad.append("点位声明判定错：%r 期望 ok=%s，实得 %s（%s）"
                             % (line[-46:], want_ok, ok_d, note_d))
        if want_why and want_why not in note_d:
            helper_bad.append("点位声明的失效原因不对：%r → %s" % (line[-40:], note_d))
    ok_np, note_np = _declaration("x = 1  # forge-" + "declared: SEC-4 理由=只读 复审=2099-01-01")
    if ok_np is not False or "格式不合" not in note_np:
        helper_bad.append("缺出处段的声明被判成有效：%r" % (note_np,))
    # 讲这条语法本身的说明文字不许被当成声明尝试：文档一定要写出标记样子，
    # 旧写法（只看注释位置）会把每篇文档都判成畸形声明（工单 W14 落地时实测 2 处）
    if _declaration("- 注释语法两种都认：脚本与 yml 用 `# forge-" + "declared: …`")[0] is not None:
        helper_bad.append("讲语法的说明文字被当成了声明尝试")
    if _declaration("x = 1  <!-- forge-" + "declared: SEC-2 理由=缺出处 复审=2099-01-01 -->")[0] is not False:
        helper_bad.append("HTML 注释里的畸形声明没被判成无效（说明文字例外不能放掉真尝试）")
    if _covers("SEC-4", "x  # forge-" + "declared: SEC-2 理由=a 出处=AUDIT.md#s 复审=2099-01-01"):
        helper_bad.append("SEC-2 的声明能豁免 SEC-4 的命中")
    if not _covers("SEC-4", "x  # forge-" + "declared: SEC-4 理由=a 出处=AUDIT.md#s 复审=2099-01-01"):
        helper_bad.append("同类声明没能豁免同类命中")

    # 端到端：一个带有效声明的文件不计分，一个声明过期的文件计分
    dd = tempfile.mkdtemp(prefix="forge-audit-declared-")
    try:
        # 声明现在要核对「出处在被审仓库里真查得到」（A791 的 CRITICAL），
        # 所以夹具仓库也得带上那本账，锚点与声明里写的一致
        open(os.path.join(dd, "AUDIT.md"), "w", encoding="utf-8", newline=chr(10)).write(
            "### s · 自检夹具的点位声明登记" + chr(10) + chr(10))
        open(os.path.join(dd, "ok_net.py"), "w", encoding="utf-8", newline="\n").write(
            "import urllib.request  # forge-" + "declared: SEC-4 理由=只读探测 "  # forge-declared: SEC-4 理由=自检夹具里的网络字面量 出处=AUDIT.md#sec-declared 复审=2026-12-15
            "出处=AUDIT.md#s 复审=2099-01-01\n"
            "urllib.request.urlopen(q)  # forge-" + "declared: SEC-4 理由=只读探测 "  # forge-declared: SEC-4 理由=自检夹具里的网络字面量 出处=AUDIT.md#sec-declared 复审=2026-12-15
            "出处=AUDIT.md#s 复审=2099-01-01\n")
        open(os.path.join(dd, "stale_net.py"), "w", encoding="utf-8", newline="\n").write(
            "import urllib.request  # forge-" + "declared: SEC-4 理由=只读探测 "  # forge-declared: SEC-4 理由=自检夹具里的网络字面量 出处=AUDIT.md#sec-declared 复审=2026-12-15
            "出处=AUDIT.md#s 复审=2001-01-01\n"
            "urllib.request.urlopen(q)  # forge-" + "declared: SEC-4 理由=只读探测 "  # forge-declared: SEC-4 理由=自检夹具里的网络字面量 出处=AUDIT.md#sec-declared 复审=2026-12-15
            "出处=AUDIT.md#s 复审=2001-01-01\n")
        box = {"ok": [], "bad": []}
        _sd, _fd, _kd, _ud, _ad = scan(dd, False, [], box, honor_inline=True)
        files_hit = sorted({h["file"] for h in _fd["SEC-4"]})
        if files_hit != ["stale_net.py"]:
            helper_bad.append("SEC-4 计数没有只留下过期声明那份：实得 %s" % files_hit)
        ok_files = sorted({h["file"] for h in box["ok"]})
        if ok_files != ["ok_net.py"]:
            helper_bad.append("有效声明没记进账：%s" % (ok_files,))
        bad_files = sorted({h["file"] for h in box["bad"]})
        if bad_files != ["stale_net.py"]:
            helper_bad.append("过期声明没记进失效账：%s" % (bad_files,))
        # SEC-2 的赋值判定是另一条道（secret_assignments），声明在这条道上也要管用，
        # 不然会出现「标记写了、命中照计」这种谁也解释不了的状态。
        # 触发词拆写（本仓夹具口径）：这一行自己不能被判成 SEC-2，
        # 不然「扫自己」这一关就先红，测不到声明机制。
        pw_key = "MY_API" + "_PASSWORD"
        pw_val = '"Sup3rS3cret' + 'Value"'
        pw_mark = "# forge-" + "declared: SEC-2 理由=自检夹具的假口令 "                   "出处=AUDIT.md#s 复审=2099-01-01"
        open(os.path.join(dd, "declared_pw.py"), "w", encoding="utf-8",
             newline=chr(10)).write(pw_key + " = " + pw_val + "  " + pw_mark + chr(10))
        box2 = {"ok": [], "bad": []}
        _s2, _f2, _k2, _u2, _a2 = scan(dd, False, [], box2, honor_inline=True)
        pw_hits = [h for h in _f2["SEC-2"] if h["file"] == "declared_pw.py"]
        # 反向：把账本里的锚点抹掉，同一条声明必须失效，命中回到账上。
        # 这条钉住 A791 的 CRITICAL：被审方自己写一行注释就免掉一票否决。
        open(os.path.join(dd, "AUDIT.md"), "w", encoding="utf-8", newline=chr(10)).write(
            "### other · 与声明无关的标题" + chr(10))
        box2b, _sk2b, _uc2b, _f2b = [], [], [], {}
        _s2b, _f2b, _sk2b, _uc2b, _a2b = scan(dd, False, [], {"ok": [], "bad": []}, honor_inline=True)
        if not [h for h in _f2b["SEC-2"] if h["file"] == "declared_pw.py"]:
            helper_bad.append("出处查无记录时声明仍然生效（A791 CRITICAL 没修住）")
        if not [h for h in _f2b["SEC-4"] if h["file"] == "ok_net.py"]:
            helper_bad.append("出处查无记录时 SEC-4 仍被免掉：fail-closed 的方向写反了")
        # R1：`出处=AUDIT.md` 不带锚点不算数。否则放一本非空 AUDIT.md 就能自封豁免。
        open(os.path.join(dd, "AUDIT.md"), "w", encoding="utf-8", newline=chr(10)).write(
            "### 任意标题" + chr(10))
        open(os.path.join(dd, "bare_cite.py"), "w", encoding="utf-8", newline=chr(10)).write(
            "MY_API_PASSWORD = \"Sup3rS3cretValue\"  # forge-" + "declared: SEC-2 "
            "理由=无锚点声明 出处=AUDIT.md 复审=2099-01-01" + chr(10))
        _s2c, _f2c, _sk2c, _uc2c, _a2c = scan(dd, False, [], {"ok": [], "bad": []}, honor_inline=True)
        if not [h for h in _f2c["SEC-2"] if h["file"] == "bare_cite.py"]:
            helper_bad.append("不带锚点的出处仍然豁免了 SEC-2（A791 R1 没修住）")
        # 外部仓自带一本 AUDIT.md、写上锚点、再挂一条格式合法的行尾声明：仍不许自免。
        # 这是 A791 CRITICAL 的正解——被审对象不能给自己发通行证。
        open(os.path.join(dd, "AUDIT.md"), "w", encoding="utf-8", newline=chr(10)).write(
            "### s" + chr(10) + "- 教学示例口令" + chr(10))
        ext_box = {"ok": [], "bad": []}
        _sx, _fx, _kx, _ux, _ax = scan(dd, False, [], ext_box, honor_inline=False)
        if not [h for h in _fx["SEC-2"] if h["file"] == "declared_pw.py"]:
            helper_bad.append("外部仓的行尾声明仍然豁免了 SEC-2（A791 CRITICAL 没堵住）")
        if ext_box["ok"]:
            helper_bad.append("外部仓的行尾声明被记进了豁免账：%s" % (ext_box["ok"][:1],))
        # 审核方清单：同一行、同一类、行正文哈希对上才生效
        pw_line = open(os.path.join(dd, "declared_pw.py"), encoding="utf-8").read().strip()
        good_hash = hashlib.sha256(pw_line.encode("utf-8")).hexdigest()
        lst_ok = {("declared_pw.py", "SEC-2", good_hash): "审核人甲"}
        _sl, _fl, _kl, _ul, _al = scan(dd, False, [], {"ok": [], "bad": []},
                                       honor_inline=False, declared_list=lst_ok)
        if [h for h in _fl["SEC-2"] if h["file"] == "declared_pw.py"]:
            helper_bad.append("审核方清单命中了却没生效（--declared-list 白给）")
        lst_stale = {("declared_pw.py", "SEC-2", "0" * 64): "审核人甲"}
        _st, _ft, _kt, _ut, _at = scan(dd, False, [], {"ok": [], "bad": []},
                                       honor_inline=False, declared_list=lst_stale)
        if not [h for h in _ft["SEC-2"] if h["file"] == "declared_pw.py"]:
            helper_bad.append("清单里哈希对不上也放行了：sha256 绑定形同虚设")
        if pw_hits:
            helper_bad.append("SEC-2 的赋值判定没认点位声明：%s" % (pw_hits[:1],))
        if not any(h["file"] == "declared_pw.py" for h in box2["ok"]):
            helper_bad.append("SEC-2 的有效声明没记进 declared_ok 账")
        # --strict-declared：畸形标记默认只失效，开了旗标才算问题
        open(os.path.join(dd, "broken_mark.py"), "w", encoding="utf-8", newline=chr(10)).write(
            "x = 1  # forge-" + "declared: SEC-4 理由= 出处=AUDIT.md#s 复审=2099-01-01" + chr(10))
        rc_soft, body_soft = _run([], dd)
        rc_strict, body_strict = _run(["--strict-declared"], dd)
        soft_sec3 = [h for h in body_soft.get("findings", {}).get("SEC-3", [])
                     if h["file"] == "broken_mark.py"]
        strict_sec3 = [h for h in body_strict.get("findings", {}).get("SEC-3", [])
                       if h["file"] == "broken_mark.py"]
        if soft_sec3:
            helper_bad.append("默认档把畸形声明当成了问题（应当只失效不报）：%s" % (soft_sec3[:1],))
        if not strict_sec3:
            helper_bad.append("--strict-declared 没把畸形声明判成问题")
        if body_strict.get("declared_bad") == []:
            helper_bad.append("畸形声明没进 declared_bad 账")
    except OSError as e:
        helper_bad.append("点位声明端到端没跑成：" + type(e).__name__)
    finally:
        shutil.rmtree(dd, ignore_errors=True)

    # 文档与代码同步：清单里用反引号点名的每个常量都必须真在本文件里定义。
    # 「判据分散在六处常量」这句话已经错过两次（实为八处、九处），所以不再让它数数，
    # 改成让脚本自己核对名字。
    try:
        doc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                                "references", "security-checklist.md"), encoding="utf-8").read()
        src_txt = open(os.path.abspath(__file__), encoding="utf-8").read()
        cited = set(re.findall(r"`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`", doc))
        defined = set(re.findall(r"^([A-Z][A-Z0-9]*_[A-Z0-9_]+)\s*=", src_txt, re.M))
        for name in sorted(cited - defined):
            helper_bad.append("清单点名了 %s，本文件里没有这个常量" % name)
        if not cited:
            helper_bad.append("清单里一个常量名都没点名，核对用例空转")
    except OSError as e:
        helper_bad.append("清单引用核对没跑成：" + type(e).__name__)
    for msg in helper_bad:
        print("SELF-TEST FAIL:", msg)

    bad = [c for c in cases if hit(c[0], c[1]) != c[2]]
    for cid, line, want in bad:
        print("SELF-TEST FAIL: %s want=%s got=%s %r" % (cid, want, hit(cid, line), line))
    # 例数实算：写死的常量已经两次落后于实际检查数（11→16→18）。
    try:
        src = open(__file__, encoding="utf-8").read()
        helpers = src.count("helper_bad." + "append(")
    except OSError:
        helpers = 0
    n = len(cases) + len(sec5_cases) + len(extra) + helpers
    ok = not (bad or s5_bad or e_bad or helper_bad)
    print("SEC-1/2/5 + 边界自检: %s（%d 例）" % ("PASS" if ok else "FAIL", n))
    return 0 if ok else 1


def main():
    _utf8()
    ap = argparse.ArgumentParser(description="四项安全检查，输出 0–100 分与门禁结论")
    ap.add_argument("repo", nargs="?", default=".", help="目标仓库路径")
    ap.add_argument("--allow", action="append", default=[],
                    help="豁免的相对路径前缀。可重复传，也可逗号分隔（依据须记入 AUDIT.md）。"
                         "旧版是单值，重复传只认最后一个，会静默少豁免并报假阻断")
    ap.add_argument("--include-docs", action="store_true", help="把 md/txt/rst 也纳入扫描（.html 已在代码池）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    ap.add_argument("--self-test", action="store_true", help="内置自检（CI 用）")
    ap.add_argument("--fail-unclear", action="store_true",
                    help="有盲区（未读文件或疑似压缩）时把放行改成退出码 3=不确定，供 CI 用")
    ap.add_argument("--strict-declared", action="store_true",
                    help="畸形或已过复审期的点位声明直接算问题（CI 用；默认只让声明失效，不额外报）")
    ap.add_argument("--strict-placeholder", action="store_true",
                    help="占位词后跟数字也算真值：example1234567 一类不再被吃掉，文档示例会开始报")
    ap.add_argument("--declared-list", default=None,
                    help="审核方控制的点位声明清单（仓库外 JSON）。行尾声明只在被审仓就是本仓时生效；"
                         "审外部仓库时豁免只认这份清单，每条按行正文的 SHA-256 绑定")
    args = ap.parse_args()
    global STRICT_PLACEHOLDER
    STRICT_PLACEHOLDER = args.strict_placeholder
    if args.self_test:
        return _self_test()
    root = os.path.abspath(args.repo)
    if not os.path.isdir(root):
        print("用法错误: 不是目录:", root)
        return 2
    allow = [x.strip() for chunk in args.allow for x in chunk.split(",") if x.strip()]
    honor_inline = _is_self_repo(root)
    declared_list = None
    if args.declared_list:
        try:
            declared_list = load_declared_list(args.declared_list)
        except (OSError, ValueError) as e:
            print("用法错误: 声明清单读不了: %s（%s）" % (args.declared_list, e))
            return 2
    declared = {"ok": [], "bad": []}
    scanned, findings, skips, unclear, adv = scan(root, args.include_docs, allow, declared,
                                                  honor_inline=honor_inline,
                                                  declared_list=declared_list)
    if args.strict_declared:
        for b in declared["bad"]:
            findings["SEC-3"].append({"file": b["file"], "line": b["line"],
                                      "note": "畸形点位声明：" + b["why"]})
    s, total, verdict, code = score(findings)
    unread = [sk for sk in skips if blind_kind(sk) == "unread"]
    excluded = [sk for sk in skips if blind_kind(sk) == "excluded"]
    blind = len(unread) + len(unclear)
    if scanned == 0:
        verdict, code = "空扫描（没有读到任何文件，先核对路径与扩展名池）", 3
    elif args.fail_unclear and blind and code == 0:
        verdict, code = "不确定（有 %d 处真盲区）" % blind, 3
    if args.json:
        print(json.dumps({"repo": root.replace("\\", "/"), "scanned_files": scanned,
                          "skipped_files": len(skips), "skips": skips,
                          "unread_files": unread, "by_design_excluded": len(excluded),
                          "unclear_files": len(unclear), "unclear": unclear,
                          "advisory_sec5": len(adv), "sec5": adv,
                          "declared_ok": len(declared["ok"]),
                          "declared_ok_list": declared["ok"],
                          "declared_bad": declared["bad"],
                          "blind_spots": blind,
                          "allow": allow,
                          "inline_declarations_honored": honor_inline,
                          "declared_list": args.declared_list,
                          "counts": {c: len(findings[c]) for c in CHECKS},
                          "findings": findings, "score": s, "total": total,
                          "verdict": verdict}, ensure_ascii=False, indent=1))
        return code
    print("仓库: %s | 扫描 %d 个文件 | 豁免 %s" % (root.replace("\\", "/"), scanned,
                                                  ",".join(allow) or "无"))
    print("豁免信道: 行尾声明%s | 审核方清单 %s"
          % ("生效（本仓自扫）" if honor_inline else "不生效（外部仓库，被审方不能自己发通行证）",
             args.declared_list or "未提供"))
    if skips:
        from collections import Counter
        kinds = Counter(x.rsplit("（", 1)[-1].rstrip("）") for x in skips)
        print("未读 %d 项（设计内排除 %d、真盲区 %d）：%s"
              % (len(skips), len(excluded), len(unread),
                 "；".join("%s %d" % (k, v) for k, v in kinds.most_common())))
        print("   完整清单用 --json 查看（skips / unread_files 字段）——这些位置本次没有证据。")
    for c in CHECKS:
        n = len(findings[c])
        print("== %s：%d 处（%d 分）" % (c, n, s[c]))
        if args.quiet:
            continue
        for f in findings[c][:20]:
            print("   %s:%d [%s] %s" % (f["file"], f["line"], f["note"], f["snippet"]))
        if n > 20:
            print("   ...其余 %d 处用 --json 查看" % (n - 20))
    if adv:
        print("== SEC-5 清单与安装钩子（不计分，仅提示）：%d 处" % len(adv))
        if not args.quiet:
            for f in adv[:20]:
                print("   %s:%d [%s] %s" % (f["file"], f["line"], f["note"], f["snippet"]))
    print("得分: SEC1=%d SEC2=%d SEC3=%d SEC4=%d 总分=%d/100" %
          (s["SEC-1"], s["SEC-2"], s["SEC-3"], s["SEC-4"], total))
    if unclear:
        print("盲区 %d 个文件疑似压缩/混淆：%s"
              % (len(unclear), "; ".join(x.split("（")[0] for x in unclear[:5])
                 + ("…" if len(unclear) > 5 else "")))
    print("结论: %s（SEC-2 有发现或总分<50 阻断；50-89 修完再放；>=90 放行；"
          "加 --fail-unclear 后有盲区判 3）" % verdict)
    dec_n = len(declared["ok"]) if declared is not None else 0
    dec_b = len(declared["bad"]) if declared is not None else 0
    if dec_n or dec_b:
        print("提醒: 本轮 %d 处点位声明不计分、%d 处声明畸形或已过期；逐条看 --json 的 "
              "declared_ok_list 与 declared_bad。" % (dec_n, dec_b))
    print("提醒: 这是分诊器。命中要逐条人工判定。%s"
          "整份文件的 --allow 只留给外部仓库与工具自身，每条仍要在审计记录里给依据。"
          % ("豁免优先走行尾点位声明"
             "（`forge-declared: SEC-n 理由=… 出处=<审计记录>… 复审=YYYY-MM-DD`）；"
             if honor_inline else
             "外部仓库的行尾声明不计豁免，只能由审核方用 --declared-list 提供清单；"))
    return code


if __name__ == "__main__":
    sys.exit(main())
