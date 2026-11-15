"""发布链路的 TLS 口径(issue #11)。

以前为了绕本机 schannel 的「吊销服务器脱机」(0x80092013),直接把证书校验整段关掉
(`ssl.CERT_NONE` + git `http.sslVerify=false`),而这些请求**都带着 GitHub/码云的 token**。
中间人于是可以顺手把 token 拿走 —— 拿到 token 就等于拿到自更新的发布渠道,
和 #6 的更新完整性是同一件事的两端,所以不能继续这么干。

现在的口径:
- 默认**开启**证书校验,CA 优先用 certifi(有就用,没有退回系统默认);
- git 只关掉「吊销检查」这一项(`http.schannelCheckRevoke=false`),证书链照常校验 —— 这正是本机原来报错的原因;
- 真出问题时用环境变量显式降级,并且**每次都把风险打印出来**,不静默放行:
    NEKO_INSECURE_TLS=1  →  Python 侧 CERT_NONE(会明确警告 token 有泄露风险)
    NEKO_INSECURE_GIT=1  →  git 侧 sslVerify=false

`git_proxy_args()` 顺带把「不走失效代理」这件事集中在一处,别再各脚本自己拼一遍。
"""
import os
import shutil
import ssl
import subprocess
import sys

_warned = {"py": False, "git": False}


def _ca_bundle():
    try:
        import certifi  # 有就用它(系统根证书不全的机器上靠它)
        return certifi.where()
    except Exception:
        return os.environ.get("SSL_CERT_FILE") or ""


def insecure_py():
    return os.environ.get("NEKO_INSECURE_TLS", "") in ("1", "true", "yes")


def insecure_git():
    return os.environ.get("NEKO_INSECURE_GIT", "") in ("1", "true", "yes")


def make_context(verbose=True):
    """默认安全:校验域名与证书链。只有显式设了 NEKO_INSECURE_TLS 才降级,且一定打印警告。"""
    if insecure_py():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if verbose and not _warned["py"]:
            _warned["py"] = True
            print("!! NEKO_INSECURE_TLS=1:已关闭证书校验,而请求携带 GitHub/码云 token——"
                  "公网/代理环境下有凭据泄露风险,只在确认链路可信时这么用", file=sys.stderr)
        return ctx
    ctx = ssl.create_default_context(cafile=_ca_bundle() or None)
    return ctx


def find_ca_bundle():
    """按可靠性顺序找一个 CA bundle,给 git 的 openssl 后端用。"""
    p = os.environ.get("NEKO_CA_BUNDLE") or ""
    if p and os.path.isfile(p):
        return p
    home = os.path.expanduser("~")
    for cand in (
        os.path.join(home, ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0",
                     "mingw64", "etc", "ssl", "certs", "ca-bundle.crt"),
        r"C:\Program Files\Git\mingw64\etc\ssl\certs\ca-bundle.crt",
    ):
        if os.path.isfile(cand):
            return cand
    try:
        import certifi
        p = certifi.where()
        if os.path.isfile(p):
            return p
    except Exception:
        pass
    return ""


def git_tls_args(verbose=True):
    """给 git 的 -c 参数:**保持证书校验**。

    实测本机(PortableGit 2.55.windows.3,schannel 后端)的网络够不到吊销服务器,
    一律报 CRYPT_E_REVOCATION_OFFLINE(0x80092013);而 `http.schannelCheckRevoke=false`
    在这个构建上不生效(照样报同一个错)。以前的做法是顺手 sslVerify=false —— 可这些请求带着
    GitHub/码云的 token,等于把凭据在不可验证的线路上发出去。
    正解是换 OpenSSL 后端 + CA bundle(证书链照常校验,OpenSSL 默认不做 CRL 拉取),
    实测双端在完整校验下都能通。找不到 CA bundle 才退回系统默认,仍不关校验。
    """
    args = ["-c", "http.proxy=", "-c", "http.sslVerify=true"]
    if insecure_git():
        if not _warned["git"]:
            _warned["git"] = True
            print("!! NEKO_INSECURE_GIT=1:git 证书校验被关闭,而 push 会带上凭据——只在完全可信的网络里这么用", file=sys.stderr)
        return args + ["-c", "http.sslVerify=false"]
    ca = find_ca_bundle()
    if ca:
        args += ["-c", "http.sslBackend=openssl", "-c", "http.sslCAInfo=" + ca]
        if verbose and not _warned["git"]:
            _warned["git"] = True
            print("   git TLS:OpenSSL 后端 + CA bundle(证书照常校验):" + ca, file=sys.stderr)
    elif verbose and not _warned["git"]:
        _warned["git"] = True
        print("   git TLS:没找到 CA bundle,沿用系统默认校验(若报 0x80092013,设 NEKO_CA_BUNDLE)", file=sys.stderr)
    return args


_GIT_CACHE = {}


def resolve_git():
    """找 git 可执行文件:PATH → $GIT_BIN → 本机 PortableGit 已知位置。
    issue #13 说「工具脚本里写死本机路径」——写死本身不是问题,散在各脚本里、
    换台机器直接 FileNotFoundError 才是问题;收敛到这一处并且允许环境变量覆盖。"""
    if _GIT_CACHE.get("p"):
        return _GIT_CACHE["p"]
    p = shutil.which("git") or os.environ.get("GIT_BIN") or ""
    if not p:
        home = os.path.expanduser("~")
        for cand in (
            os.path.join(home, ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0", "cmd", "git.exe"),
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe",
        ):
            if os.path.isfile(cand):
                p = cand
                break
    _GIT_CACHE["p"] = p
    if not p:
        print("!! 找不到 git(设 GIT_BIN 或把 git 加进 PATH)", file=sys.stderr)
    return p


def run_git(args, cwd=None, env=None, **kw):
    exe = resolve_git()
    if not exe:
        raise RuntimeError("git 不可用")
    return subprocess.run([exe, *git_tls_args(), *args], cwd=cwd or os.getcwd(),
                          env=env or os.environ, capture_output=True, text=True, encoding="latin1", **kw)


def git_nocred(args, cwd=None, **kw):
    """不带 TLS 参数的 git 调用(本地操作:archive/tag/rev-parse),走 PATH 兜底。"""
    exe = resolve_git()
    if not exe:
        raise RuntimeError("git 不可用")
    return subprocess.run([exe, *args], cwd=cwd or os.getcwd(),
                          capture_output=True, text=True, encoding="latin1", **kw)


def resolve_node():
    """找 node:PATH → $NODE_BIN → 本机 dev\\tools\\node24。发布链路要调 node 签清单(issue #12),
    这里同样只做一处集中解析,别在各脚本里各写一遍绝对路径(issue #13)。"""
    p = os.environ.get("NODE_BIN") or shutil.which("node") or ""
    if p and os.path.isfile(p):
        return p
    home = os.path.expanduser("~")
    for cand in (
        os.path.join(home, "dev", "tools", "node24", "node-v24.14.0-win-x64", "node.exe"),
        os.path.join(home, "dev", "tools", "node24", "node-v24.11.0-win-x64", "node.exe"),
        r"C:\Program Files\nodejs\node.exe",
    ):
        if os.path.isfile(cand):
            return cand
    return ""


def cred(host, cwd=None):
    """从 git 的凭据库里取 token——三个发布脚本各写一份的日子结束了(issue #13:去重复、去写死)。
    凭据只在这里取一次,不回显、不写日志。"""
    exe = resolve_git()
    if not exe:
        return ""
    p = subprocess.run([exe, "credential", "fill"], input="protocol=https\nhost=%s\n\n" % host,
                       capture_output=True, text=True, encoding="latin1", cwd=cwd or os.getcwd(), timeout=120)
    for line in (p.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def curl_no_revoke():
    """排查用的 curl 参数(与 git 同口径:吊销脱机不该变成整段不校验)"""
    return ["--ssl-no-revoke"] if os.name == "nt" else []
