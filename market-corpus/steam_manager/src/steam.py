"""steam_manager — Steam 客户端与游戏库管理（Windows）。

功能：
- 检测 Steam 客户端安装路径与运行状态
- 解析本地游戏库（libraryfolders.vdf + appmanifest_*.acf）
- 通过 steam.exe -applaunch 启动已安装游戏
- 读取本地最近登录账号元数据（config/loginusers.vdf，仅昵称/SteamID）
- 经 Steam Web API 查询账户信息、好友列表（需 STEAM_API_KEY）
- 经 steamcommunity 网页聊天接口回复好友（需 steam_cookie.txt）
- 经 Steam Store 公开 API 查询游戏价格与促销（无需凭证）
- 打开任意 Steam 应用：已安装直接启动，未安装打开商店页

安全边界：只读本地库元数据 + 按用户明确指令启动游戏；
账号/好友/发消息仅在使用户授权的场景执行，凭据（API Key / cookie）
只读取不输出；不读取密码、令牌、云存档等敏感数据。
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

_PERSONA_STATE = {0: "离线", 1: "在线", 2: "离开", 3: "忙碌", 4: "打盹", 5: "想交易", 6: "想玩"}

# 常见 Steam 安装路径（注册表失败时的兜底）
_FALLBACK_PATHS = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
)

# StateFlags 位含义（Steam 官方）
_FLAG_FULLY_INSTALLED = 4  # 已完整安装
_FLAG_UPDATE_REQUIRED = 2  # 需要更新


def _state_flags_text(flags: int) -> str:
    """将 StateFlags 数字转成可读状态描述。"""
    if flags & _FLAG_FULLY_INSTALLED:
        return "已安装"
    if flags & _FLAG_UPDATE_REQUIRED:
        return "需要更新"
    if flags == 0:
        return "未安装"
    return f"状态码 {flags}"


# ══════════ VDF/ACF 解析（纯函数） ══════════

_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """解析 Valve VDF/ACF 文本为嵌套 dict。

    兼容 libraryfolders.vdf 与 appmanifest_*.acf 的通用结构：
    "key" "value"、嵌套 "block" { ... }，支持转义引号。
    """
    root: dict = {}
    stack: list[dict] = [root]
    pending_key: str | None = None

    for match in _TOKEN_RE.finditer(text or ""):
        value, brace = match.group(1), match.group(2)
        if value is not None:
            value = value.replace('\\"', '"').replace("\\\\", "\\")
            if pending_key is None:
                pending_key = value
            else:
                stack[-1][pending_key] = value
                pending_key = None
        elif brace == "{":
            block: dict = {}
            if pending_key is not None:
                stack[-1][pending_key] = block
                pending_key = None
            stack.append(block)
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
    return root


# ══════════ Steam 路径与状态 ══════════


def find_steam_path() -> str | None:
    """定位 Steam 安装目录；找不到返回 None。"""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value = winreg.QueryValueEx(key, "SteamPath")[0]
                if value and os.path.isdir(value):
                    return value
        except OSError:
            pass
    for candidate in _FALLBACK_PATHS:
        if os.path.isdir(candidate):
            return candidate
    return None


def steam_running() -> bool:
    """检测 Steam 客户端进程是否在运行。"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
            capture_output=True, text=True, timeout=10.0,
            creationflags=_CREATE_NO_WINDOW,
        ).stdout
        return "steam.exe" in (out or "")
    except (OSError, subprocess.SubprocessError):
        return False


# ══════════ 游戏库扫描 ══════════


def _collect_library_roots(steam_path: str) -> list[str]:
    """收集主库与全部扩展库根目录（去重）。"""
    roots = [steam_path]
    vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    if not os.path.isfile(vdf):
        return roots
    try:
        with open(vdf, encoding="utf-8", errors="replace") as fh:
            data = parse_vdf(fh.read())
    except OSError:
        return roots
    libs = data.get("libraryfolders", {})
    for key, value in libs.items():
        if isinstance(value, dict):
            path = value.get("path")
            if isinstance(path, str) and os.path.isdir(path):
                roots.append(path)
        elif isinstance(value, str) and key.isdigit() and os.path.isdir(value):
            roots.append(value)
    return roots


def scan_games(steam_path: str, limit: int = 100) -> list[dict]:
    """扫描所有库中的 appmanifest，返回已安装游戏列表。"""
    games: list[dict] = []
    seen: set[str] = set()
    for root in _collect_library_roots(steam_path):
        apps_dir = os.path.join(root, "steamapps")
        if not os.path.isdir(apps_dir):
            continue
        for filename in os.listdir(apps_dir):
            if not (filename.startswith("appmanifest_") and filename.endswith(".acf")):
                continue
            app_id = filename[len("appmanifest_"):-len(".acf")]
            if not app_id.isdigit() or app_id in seen:
                continue
            seen.add(app_id)
            acf_path = os.path.join(apps_dir, filename)
            try:
                with open(acf_path, encoding="utf-8", errors="replace") as fh:
                    state = parse_vdf(fh.read()).get("AppState", {})
            except OSError:
                continue
            name = str(state.get("name") or filename)
            size = 0
            try:
                size = int(state.get("SizeOnDisk") or 0)
            except (TypeError, ValueError):
                size = 0
            flags = 0
            try:
                flags = int(state.get("StateFlags") or 0)
            except (TypeError, ValueError):
                flags = 0
            games.append(
                {
                    "app_id": app_id,
                    "name": name,
                    "size_mb": round(size / 1048576, 1) if size else 0,
                    "installed": bool(flags & _FLAG_FULLY_INSTALLED),
                    "state": _state_flags_text(flags),
                }
            )
            if limit > 0 and len(games) >= limit:
                return games
    return games


def find_game(steam_path: str, query: str, limit: int = 100) -> list[dict]:
    """按名称模糊匹配游戏（先全量扫描、后过滤，再按 limit 截断；返回全部匹配项，供调用方决策）。

    v0.3.1：修复 limit 截断顺序——此前先截断后过滤，游戏库超 limit 时
    关键词匹配项可能被截掉导致误报"未找到"。
    """
    query = _alias_expand(query)
    games = scan_games(steam_path, limit=0)
    if query:
        if query.isdigit():
            games = [g for g in games if g["app_id"] == query]
        else:
            games = [g for g in games if query in g["name"].lower()]
    return games[:limit] if limit > 0 else games


# ══════════ 启动游戏 ══════════


# 常见游戏中文别名/简写 → 英文名匹配关键词（中文玩家习惯用中文名搜）。
# 命中后按英文关键词做子串匹配，避免"文明6"搜不到 Civilization VI 这类误报"未安装"。
_GAME_ALIASES = {
    "文明": "civilization",
    "文明6": "civilization vi",
    "文明六": "civilization vi",
    "文明vi": "civilization vi",
    "civ6": "civilization vi",
    "civ": "civilization",
    "席德梅尔的文明": "civilization",
    "钢铁雄心": "hearts of iron",
    "钢4": "hearts of iron",
    "hoi4": "hearts of iron",
    "群星": "stellaris",
    "欧陆风云": "europa universalis",
    "维多利亚": "victoria",
    "十字军之王": "crusader kings",
    "ck3": "crusader kings",
    "gmod": "garry",
    "盖瑞模组": "garry",
    "garrys mod": "garry",
    "骑砍": "mount & blade",
    "文明6开发": "development assets",
}

# 常见游戏 AppID → 中文显示名（list_games 输出附加，方便 LLM 识别"文明6"就是 Civilization VI）。
_GAME_CN_NAMES = {
    "289070": "文明6",
    "597260": "文明6开发工具(SDK)",
    "394360": "钢铁雄心4",
    "4000": "Garry's Mod",
    "945360": "VRChat",
}


def _alias_expand(query: str) -> str:
    """把中文别名/简写展开成英文匹配关键词；无别名时原样返回（小写）。"""
    q = (query or "").strip().lower()
    if q in _GAME_ALIASES and _GAME_ALIASES[q]:
        return _GAME_ALIASES[q]
    return q


def launch_game(steam_path: str, app_id: str) -> dict:
    """通过 steam.exe -applaunch 启动指定 AppID 的游戏。

    返回 {"ok": bool, ...} 信封；steam.exe 不存在时给出明确错误。
    """
    app_id = (app_id or "").strip()
    if not app_id.isdigit():
        return {"ok": False, "error": "INVALID_APP_ID", "output": "AppID 必须是数字。"}
    exe = os.path.join(steam_path, "steam.exe")
    if not os.path.isfile(exe):
        return {
            "ok": False,
            "error": "STEAM_EXE_NOT_FOUND",
            "output": f"在 {steam_path} 下未找到 steam.exe，无法启动游戏。",
        }
    try:
        subprocess.Popen(
            [exe, "-applaunch", app_id],
            creationflags=_CREATE_NO_WINDOW,
        )
        return {
            "ok": True,
            "output": f"已通过 Steam 启动游戏（AppID {app_id}）。如未登录 Steam 或游戏未安装，请先完成相应操作。",
        }
    except OSError as exc:
        return {"ok": False, "error": "LAUNCH_FAILED", "output": f"启动游戏失败：{exc}"}


# ══════════ 可选凭证（只读，不输出内容） ══════════


def api_key() -> str:
    """读取 Steam Web API Key：环境变量 STEAM_API_KEY 或插件目录 steam_api_key.txt。"""
    key = os.environ.get("STEAM_API_KEY", "").strip()
    if key:
        return key
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "steam_api_key.txt"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def community_cookie() -> str:
    """读取 steamcommunity 登录 cookie：环境变量 STEAM_COOKIE 或插件目录 steam_cookie.txt。

    需包含 sessionid（发消息必需）与 steamLoginSecure/steamLogin（身份）。
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "steam_cookie.txt"), encoding="utf-8") as fh:
            cookie = fh.read().strip()
        if cookie:
            return cookie
    except OSError:
        pass
    return os.environ.get("STEAM_COOKIE", "").strip()


def _cookie_value(cookie_text: str, name: str) -> str:
    """从 Cookie 文本中提取指定字段值。"""
    for part in (cookie_text or "").split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:].strip().strip('"')
    return ""


# ══════════ 凭证自动配置（v0.3.2：skip / yui / auto 三模式 + 验证） ══════════


def _credential_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def save_api_key(key: str, path: str | None = None) -> str:
    """把 Steam Web API Key 写入插件目录 steam_api_key.txt，返回写入路径。"""
    key = (key or "").strip()
    if not key:
        raise ValueError("API Key 为空")
    target = path or os.path.join(_credential_dir(), "steam_api_key.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(key)
    return target


def save_community_cookie(cookie: str, path: str | None = None) -> str:
    """把 steamcommunity 登录 Cookie 写入插件目录 steam_cookie.txt，返回写入路径。"""
    cookie = (cookie or "").strip()
    if not cookie:
        raise ValueError("Cookie 为空")
    target = path or os.path.join(_credential_dir(), "steam_cookie.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(cookie)
    return target


def validate_api_key(key: str) -> tuple[bool, str]:
    """验证 API Key：用 Valve 官方账号 steamid 调 GetPlayerSummaries 探测。

    返回 (是否有效, 说明)。成功响应无 code 字段（players 非空即有效）；
    Steam Web API 对无效 Key 通常返回 HTTP 403（实测非 code=101），
    一律归类为"Key 无效"。
    """
    key = (key or "").strip()
    if not key:
        return False, "Key 为空"
    try:
        data = _http_get_json(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
            f"?key={urllib.parse.quote(key)}&steamids=76561197960287930"
        )
    except OSError as exc:
        if "HTTP 403" in str(exc):
            return False, "Key 无效（接口返回 403，通常为 Key 错误或被禁用），请检查是否复制完整"
        return False, f"验证请求失败：{exc}"
    except ValueError as exc:
        return False, f"验证请求失败：{exc}"
    players = ((data.get("response") or {}).get("players"))
    if players:
        return True, "Key 有效（已通过官方接口验证）"
    if data.get("code") == 101:
        return False, "Key 无效（InvalidKey），请检查是否复制完整"
    return False, f"Key 无效（code={data.get('code')}）"


def _probe_cookie_page(cookie: str) -> tuple[str, str]:
    """带 Cookie 请求 steamcommunity /profiles/me，返回 (最终URL, 页面文本)。"""
    req = urllib.request.Request(
        "https://steamcommunity.com/profiles/me",
        headers={"User-Agent": _UA, "Cookie": cookie},
    )
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        return resp.geturl(), resp.read(65536).decode("utf-8", errors="replace")


def validate_community_cookie(cookie: str) -> tuple[bool, str]:
    """验证 steamcommunity Cookie：跳转到个人资料页或页面含身份信息 = 有效。

    需含 sessionid（发消息必需）。返回 (是否有效, 说明)。
    """
    cookie = (cookie or "").strip()
    if not cookie:
        return False, "Cookie 为空"
    if not _cookie_value(cookie, "sessionid"):
        return False, "Cookie 缺少 sessionid（发消息必需，请重新登录后复制完整 Cookie）"
    try:
        final_url, body = _probe_cookie_page(cookie)
    except urllib.error.HTTPError as exc:
        return False, f"验证请求失败（HTTP {exc.code}）"
    except OSError as exc:
        return False, f"验证请求失败：{exc}"
    if "/profiles/" in final_url or "personaName" in body or '"steamid"' in body:
        return True, "Cookie 有效（已通过 steamcommunity 登录态验证）"
    return False, "Cookie 无效或已过期（未登录到个人资料页）"


def credentials_status() -> dict:
    """返回当前凭证配置状态（只判断已配置与否，不输出内容）。"""
    key = api_key()
    cookie = community_cookie()
    return {
        "api_key_configured": bool(key),
        "cookie_configured": bool(cookie),
        "can_use_friends": bool(key),
        "can_reply_friend": bool(key and cookie),
    }


# ══════════ 浏览器 Cookie 自动探测（v0.3.2：解密 Chrome/Edge 本地库） ══════════
# 仅读取本机自己浏览器的 steamcommunity 登录 cookie，用于配置插件，不输出任何明文到对话。

def _dpapi_unprotect(data: bytes) -> bytes:
    """用当前 Windows 用户 DPAPI 解密（CryptUnprotectData）。"""
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    blob_in = _DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("DPAPI 解密失败（CryptUnprotectData）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _chrome_aes_key(local_state_path: str) -> bytes:
    """从 Local State 提取 AES-GCM 主密钥（DPAPI 解密 encrypted_key）。"""
    import json

    with open(local_state_path, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    enc = state.get("os_crypt", {}).get("encrypted_key", "")
    raw = base64.b64decode(enc)
    if raw.startswith(b"DPAPI"):
        return _dpapi_unprotect(raw[5:])
    return raw  # v20：直接明文 AES key（少见）


def _decrypt_cookie_value(encrypted_value: bytes, aes_key: bytes) -> str:
    """解密 Chrome v10 cookie：3 字节 'v10' 前缀 + nonce(12) + AES-GCM 密文。"""
    if encrypted_value.startswith(b"v10"):
        nonce, ciphertext = encrypted_value[3:15], encrypted_value[15:]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(aes_key).decrypt(nonce, ciphertext, None).decode("utf-8", errors="replace")
    return encrypted_value.decode("utf-8", errors="replace")


def _copy_cookie_db(src: str, dst: str) -> None:
    """复制 Cookie 库到临时文件（浏览器运行中 SQLite 可能被锁）。"""
    import shutil

    shutil.copyfile(src, dst)


def _iter_browser_cookie_dbs() -> list[tuple[str, str]]:
    """枚举 Chrome/Edge 各 profile 的 Cookies 数据库与 Local State，返回 [(db, local_state)]。"""
    candidates: list[tuple[str, str]] = []
    for app in ("Google\\Chrome", "Microsoft\\Edge"):
        root = os.path.join(os.environ.get("LOCALAPPDATA", ""), app, "User Data")
        if not os.path.isdir(root):
            continue
        local_state = os.path.join(root, "Local State")
        if not os.path.isfile(local_state):
            continue
        for entry in os.scandir(root):
            if not entry.is_dir():
                continue
            db = os.path.join(entry.path, "Network", "Cookies")
            if os.path.isfile(db):
                candidates.append((db, local_state))
    return candidates


def probe_browser_cookies() -> dict:
    """自动探测本机浏览器里 steamcommunity.com 的登录 cookie。

    返回 {"found": bool, "browser": str, "sessionid": str, "steam_login_secure": str,
          "steam_login": str, "error": str}；值仅供插件写入使用，调用方不得回显。
    """
    import sqlite3
    import tempfile

    wanted = {"sessionid", "steamLoginSecure", "steamLogin", "steamCountry"}
    found: dict[str, str] = {}
    used_browser = ""
    last_error = ""
    for db_path, local_state in _iter_browser_cookie_dbs():
        try:
            aes_key = _chrome_aes_key(local_state)
        except Exception as exc:  # noqa: BLE001 - 单个 profile 失败不中断
            last_error = f"{os.path.basename(os.path.dirname(os.path.dirname(db_path)))} 密钥解密失败：{exc}"
            continue
        tmp = os.path.join(tempfile.gettempdir(), f"cookies_probe_{os.getpid()}_{len(found)}.db")
        try:
            try:
                _copy_cookie_db(db_path, tmp)
            except OSError as exc:
                last_error = f"复制 Cookie 库失败：{exc}"
                continue
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT name, value, encrypted_value FROM cookies WHERE host_key LIKE '%steamcommunity.com%'"
                ).fetchall()
            finally:
                con.close()
            for name, value, enc in rows:
                plain = value or _decrypt_cookie_value(enc, aes_key)
                if name in wanted and name not in found:
                    found[name] = plain
            used_browser = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(db_path)))) or used_browser
            if found.get("sessionid") and (found.get("steamLoginSecure") or found.get("steamLogin")):
                break
        except Exception as exc:  # noqa: BLE001
            last_error = f"读取 Cookie 库失败：{exc}"
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    result = {
        "found": bool(found.get("sessionid") and (found.get("steamLoginSecure") or found.get("steamLogin"))),
        "browser": used_browser,
        "sessionid": found.get("sessionid", ""),
        "steam_login_secure": found.get("steamLoginSecure", ""),
        "steam_login": found.get("steamLogin", ""),
        "steam_country": found.get("steamCountry", ""),
        "error": last_error if not found.get("sessionid") else "",
    }
    return result


# ══════════ 本地账号（config/loginusers.vdf） ══════════


def read_local_account(steam_path: str | None) -> dict | None:
    """读取本地最近登录的 Steam 账号（SteamID/昵称/最近登录时间）。

    仅读取昵称与 SteamID 等非敏感元数据；不读取密码、令牌、cookies。
    """
    if not steam_path:
        return None
    vdf = os.path.join(steam_path, "config", "loginusers.vdf")
    if not os.path.isfile(vdf):
        return None
    try:
        with open(vdf, encoding="utf-8", errors="replace") as fh:
            data = parse_vdf(fh.read())
    except OSError:
        return None
    users = data.get("users", {})
    best: dict | None = None
    for sid, meta in users.items():
        if not isinstance(meta, dict):
            continue
        rec = {
            "steam_id": str(sid),
            "name": str(meta.get("PersonaName") or ""),
            "most_recent": 0,
        }
        try:
            rec["most_recent"] = int(meta.get("mostrecent") or 0)
        except (TypeError, ValueError):
            rec["most_recent"] = 0
        if best is None or rec["most_recent"] > best["most_recent"]:
            best = rec
    return best


# ══════════ HTTP 工具 ══════════


def _http_get_json(url: str, referer: str | None = None, timeout: float = 15.0) -> dict:
    """GET 并解析 JSON。网络或解析失败抛 OSError/ValueError。"""
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise OSError(f"HTTP {exc.code}") from exc
    return json.loads(raw)


# ══════════ 账户信息（Steam Web API，需 Key） ══════════


def account_info(api_key: str, steam_id: str) -> dict | None:
    """查询指定 SteamID 的账户概要（昵称/状态/头像/国家）。

    返回 None 表示账号不存在或接口失败。需 Steam Web API Key。
    """
    steam_id = (steam_id or "").strip()
    if not api_key or not steam_id:
        return None
    url = (
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        f"?key={urllib.parse.quote(api_key)}&steamids={urllib.parse.quote(steam_id)}"
    )
    data = _http_get_json(url)
    players = (data.get("response") or {}).get("players") or []
    if not players:
        return None
    p = players[0]
    state = 0
    try:
        state = int(p.get("personastate", 0) or 0)
    except (TypeError, ValueError):
        state = 0
    return {
        "steam_id": str(p.get("steamid") or steam_id),
        "name": str(p.get("personaname") or ""),
        "state": _PERSONA_STATE.get(state, str(state)),
        "avatar": str(p.get("avatarfull") or ""),
        "country": str(p.get("loccountrycode") or ""),
        "url": str(p.get("profileurl") or ""),
    }


# ══════════ 好友列表（Steam Web API，需 Key） ══════════


def _summaries_in_chunks(api_key: str, ids: list[str], chunk: int = 100) -> dict[str, dict]:
    """GetPlayerSummaries 每次最多 100 个 steamid，超过必须分批（v0.3.1 修复）。"""
    info_map: dict[str, dict] = {}
    for start in range(0, len(ids), chunk):
        batch = ids[start:start + chunk]
        try:
            sdata = _http_get_json(
                "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
                f"?key={urllib.parse.quote(api_key)}&steamids={urllib.parse.quote(','.join(batch))}"
            )
            for p in (sdata.get("response") or {}).get("players") or []:
                info_map[str(p.get("steamid"))] = p
        except (OSError, ValueError):
            pass
    return info_map


def friend_list(api_key: str, steam_id: str, limit: int = 200) -> list[dict]:
    """查询指定账号的好友列表，合并每个好友的昵称/状态/头像。

    需 Steam Web API Key；好友超 100 人时自动分批获取概要（v0.3.1）。
    """
    steam_id = (steam_id or "").strip()
    if not api_key or not steam_id:
        return []
    url = (
        "https://api.steampowered.com/ISteamUser/GetFriendList/v1/"
        f"?key={urllib.parse.quote(api_key)}&steamid={urllib.parse.quote(steam_id)}&relationship=friend"
    )
    data = _http_get_json(url)
    friends = ((data.get("friendslist") or {}).get("friends")) or []
    ids = [str(f.get("steamid") or "") for f in friends if f.get("steamid")]
    info_map = _summaries_in_chunks(api_key, ids)
    out: list[dict] = []
    for f in friends[:limit]:
        sid = str(f.get("steamid") or "")
        if not sid:
            continue
        p = info_map.get(sid) or {}
        state = 0
        try:
            state = int(p.get("personastate", 0) or 0)
        except (TypeError, ValueError):
            state = 0
        out.append(
            {
                "steam_id": sid,
                "name": str(p.get("personaname") or ""),
                "state": _PERSONA_STATE.get(state, str(state)),
                "avatar": str(p.get("avatarfull") or ""),
            }
        )
    return out


# ══════════ 回复好友（steamcommunity 网页聊天，需 cookie） ══════════


def send_friend_message(cookie: str, my_steam_id: str, friend_steam_id: str, text: str) -> dict:
    """通过 steamcommunity 网页聊天接口给好友发一条消息。

    cookie 需包含 sessionid（与 steamLoginSecure/steamLogin）。消息最长 500 字。
    返回 {"ok": True, ...} 信封；失败抛 ValueError。
    """
    text = (text or "").strip()
    my_steam_id = (my_steam_id or "").strip()
    friend_steam_id = (friend_steam_id or "").strip()
    if not text:
        raise ValueError("消息内容为空")
    if len(text) > 500:
        raise ValueError("消息最长 500 个字符")
    if not my_steam_id or not friend_steam_id:
        raise ValueError("缺少自己的或对方的好友 SteamID")
    session = _cookie_value(cookie, "sessionid")
    if not session:
        raise ValueError("cookie 缺少 sessionid（请重新登录 steamcommunity 后更新 steam_cookie.txt）")
    body = urllib.parse.urlencode(
        {
            "steamid_first": friend_steam_id,
            "steamid": my_steam_id,
            "body": text,
        }
    ).encode()
    req = urllib.request.Request(
        "https://steamcommunity.com/chat/sendmessage",
        data=body,
        headers={
            "User-Agent": _UA,
            "Referer": "https://steamcommunity.com/chat/",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://steamcommunity.com",
            "Cookie": cookie,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"发送失败（HTTP {exc.code}）") from exc
    stripped = raw.strip()
    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
            if arr and str(arr[0]) == "1":  # 官方成功返回 [1, ...]
                return {"ok": True, "to": friend_steam_id, "text": text}
        except ValueError:
            pass
    raise ValueError(f"发送失败：{stripped[:120]}")


# ══════════ 价格与促销（Steam Store 公开 API，无需凭证） ══════════


def _final_fen(price: dict | None) -> int | None:
    """取 price_overview/价格对象的 final（单位分）；无效返回 None。"""
    if not price or not isinstance(price, dict):
        return None
    final = price.get("final")
    try:
        return int(final)
    except (TypeError, ValueError):
        return None


def search_store(query: str, limit: int = 5) -> list[dict]:
    """按名称在 Steam 商店搜索游戏（国区，简体中文）。返回 app_id/名称/价格/折扣。"""
    query = (query or "").strip()
    if not query:
        return []
    url = (
        "https://store.steampowered.com/api/storesearch/"
        f"?term={urllib.parse.quote(query)}&cc=cn&l=schinese"
    )
    data = _http_get_json(url, referer="https://store.steampowered.com/")
    items = data.get("items") or []
    out: list[dict] = []
    for it in items[:limit]:
        price = it.get("price") or {}
        final = _final_fen(price)
        original = None
        try:
            original = int(price.get("original")) if price.get("original") is not None else None
        except (TypeError, ValueError):
            original = None
        out.append(
            {
                "app_id": str(it.get("id") or ""),
                "name": str(it.get("name") or ""),
                "type": str(it.get("type") or ""),
                "price_cny": (final / 100) if final is not None else None,
                "original_cny": (original / 100) if original is not None else None,
                "discount": int(price.get("discount_percent") or 0) if price.get("discount_percent") is not None else 0,
            }
        )
    return out


def app_price(appid: str) -> dict | None:
    """查询单个 AppID 的国区价格与折扣。返回 None 表示不存在或不可用。"""
    appid = (appid or "").strip()
    if not appid.isdigit():
        return None
    url = (
        "https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&cc=cn&l=schinese&filters=basic,price_overview"
    )
    data = _http_get_json(url, referer="https://store.steampowered.com/")
    entry = data.get(appid) or {}
    if not entry.get("success"):
        return None
    d = entry.get("data") or {}
    po = d.get("price_overview") or {}
    final = _final_fen(po)
    original = None
    try:
        original = int(po.get("original")) if po.get("original") is not None else None
    except (TypeError, ValueError):
        original = None
    free = bool(po.get("is_free")) or final is None
    return {
        "app_id": appid,
        "name": str(d.get("name") or ""),
        "price_cny": (final / 100) if final is not None else None,
        "original_cny": (original / 100) if original is not None else None,
        "discount": int(po.get("discount_percent") or 0) if po.get("discount_percent") is not None else 0,
        "free": free,
    }


def store_promotions(limit: int = 15) -> list[dict]:
    """查询 Steam 商店当前特惠/促销游戏（国区首页特色分类）。"""
    url = "https://store.steampowered.com/api/featuredcategories?cc=cn&l=schinese"
    data = _http_get_json(url, referer="https://store.steampowered.com/")
    specials = ((data.get("specials") or {}).get("items")) or []
    out: list[dict] = []
    for it in specials[:limit]:
        final = _final_fen({"final": it.get("final_price")})
        original = None
        try:
            original = int(it.get("original_price")) if it.get("original_price") is not None else None
        except (TypeError, ValueError):
            original = None
        out.append(
            {
                "app_id": str(it.get("id") or ""),
                "name": str(it.get("name") or ""),
                "price_cny": (final / 100) if final is not None else None,
                "original_cny": (original / 100) if original is not None else None,
                "discount": int(it.get("discount_percent") or 0) if it.get("discount_percent") is not None else 0,
            }
        )
    return out


# ══════════ 打开任意 Steam 应用 ══════════


def open_app(steam_path: str | None, app_id: str, installed: bool) -> dict:
    """打开任意 Steam 应用：已安装直接启动；未安装打开商店页。

    用于打开其他开发者制作/未安装的游戏或应用。
    """
    app_id = (app_id or "").strip()
    if not app_id.isdigit():
        return {"ok": False, "error": "INVALID_APP_ID", "output": "AppID 必须是数字。"}
    if installed:
        if not steam_path:
            return {"ok": False, "error": "STEAM_NOT_FOUND", "output": "未检测到 Steam 安装目录，无法启动。"}
        return launch_game(steam_path, app_id)
    try:
        webbrowser.open(f"steam://openurl/https://store.steampowered.com/app/{app_id}")
    except OSError as exc:
        return {"ok": False, "error": "OPEN_FAILED", "output": f"打开商店页失败：{exc}"}
    return {
        "ok": True,
        "output": f"游戏（AppID {app_id}）未安装，已打开 Steam 商店页；在页面点击「安装」即可下载后游玩。",
    }
