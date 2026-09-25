"""QQ 开放平台机器人的「扫码创建 / 绑定已有」流程 —— 免去手动建应用、手抄 AppID/Secret。

用户不必去开发者后台点什么"创建应用"再抄两串凭据（抄错、选错机器人时**毫无提示**，
是"插件显示已连接、平台却显示离线"这类问题的常见成因）。扫码后由平台下发凭据并加密
回传，本地用一个自己生成的 AES-256 密钥解开。

流程（q.qq.com 的 lite 接口）。字段名标注为「实测」的在 2026-09-12 真机跑通过：

    ① 本地生成 bind_key（32 字节 AES-256，base64）
    ② POST /lite/create_bind_task  {key: bind_key}      → data.task_id（实测）
    ③ 二维码内容 = https://q.qq.com/qqbot/openclaw/connect.html?task_id=<task_id>
    ④ 手机 QQ 扫码 —— 连接页会让用户**自己选**「创建新的机器人」还是「选择已有的机器人」
    ⑤ 轮询 POST /lite/poll_bind_result  {task_id}       → data.status（实测）0/1/2/3
    ⑥ COMPLETED（status=2）→ data.bot_appid / data.bot_encrypt_secret /
       data.user_openid（三者均实测）→ 本地用 bind_key 解出 secret

**密钥不经第三方**：``bind_key`` 本地生成、随创建任务上传，平台用它加密回传的 secret，
所以只有本地能解开。

**字段名陷阱**：凭据里的 appid 在回包里叫 ``bot_appid``，**不是** ``appid``。真机实测前
代码只认 ``appid``，于是一次**成功**的绑定（status=2、凭据齐全）被判成
"平台返回的凭据不完整"，凭据被直接丢弃。两个键都认，别再退回单一字段名。

**注意**：新建还是绑定已有，由**用户在连接页上选**，不由本模块决定。实测选「已有的机器人」
时平台回的就是那台已有机器的 ``bot_appid``，不会新建。所谓"每扫一次就多一个"只发生在
用户主动选「创建新的机器人」时 —— 那道闸门在平台页面上，不在这里。（「创建新的」这条
路径本身尚未实测，只观测了「选已有」。）:func:`pick_reusable_bot` 管的是另一条更省事的
路：本地账本里已有记录时直接复用，连扫都不必扫。
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

#: 绑定接口的宿主。用 lite 接口（不是 openapi），无需 AppID 鉴权 —— 这正是它能
#: "从零创建"的原因。
BIND_HOST = "q.qq.com"
CREATE_PATH = "/lite/create_bind_task"
POLL_PATH = "/lite/poll_bind_result"
CONNECT_PATH = "/qqbot/openclaw/connect.html"

#: 二维码轮询间隔（秒）；平台侧建议 2 秒。
QR_POLL_INTERVAL = 2
#: 单次请求超时（毫秒）。
API_TIMEOUT_MS = 10_000

#: 平台返回的绑定状态码。
STATUS_NONE = 0
STATUS_PENDING = 1
STATUS_COMPLETED = 2
STATUS_EXPIRED = 3

_STATUS_TEXT = {
    STATUS_NONE: "none",
    STATUS_PENDING: "pending",
    STATUS_COMPLETED: "completed",
    STATUS_EXPIRED: "expired",
}


@dataclass
class BindSession:
    """一次绑定任务。``qrcode`` 是给用户扫的**链接内容**（前端渲染成码）。"""

    task_id: str
    bind_key: str
    qrcode: str
    interval: int = QR_POLL_INTERVAL


def _crypto():
    """延迟取 AESGCM —— 缺依赖时给出可操作的报错，而不是 ImportError 堆栈。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "缺少 cryptography 库，无法解密平台返回的机器人密钥。"
            "请让运行环境安装 cryptography（或将插件 lib/ 目录 vendoring 一份）。"
        ) from e
    return AESGCM


# ── 密钥 ────────────────────────────────────────────────────

def generate_bind_key() -> str:
    """生成 base64 的 32 字节 AES-256 密钥。"""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def decrypt_bot_secret(encrypted_secret: str, bind_key: str) -> str:
    """解开平台回传的机器人密钥。

    载荷是 base64(12 字节 nonce + 密文 + 16 字节 GCM tag)，密钥就是创建任务时上传的
    ``bind_key``。解不开一律抛 ``ValueError`` —— 拿不到可用密钥就该停在这里，
    往下走只会拿一个空 secret 去连网关，报出来的错和真因毫无关系。
    """
    try:
        key = base64.b64decode(bind_key)
        raw = base64.b64decode(encrypted_secret)
    except Exception as exc:
        raise ValueError("机器人凭据解码失败") from exc
    if len(key) != 32 or len(raw) <= 28:
        raise ValueError("机器人凭据密文格式异常")

    nonce, tag, ciphertext = raw[:12], raw[-16:], raw[12:-16]
    aesgcm = _crypto()(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext + tag, None).decode("utf-8")
    except Exception as exc:
        raise ValueError("机器人凭据解密失败") from exc


# ── 凭据校验 ────────────────────────────────────────────────

#: 用 appid + clientSecret 换 access_token 的接口。**凭据到底能不能用以这里为准** ——
#: 绑定回执说 ``completed`` 只代表平台记下了这次绑定，不代表这对凭据现在就能换到 token。
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

#: 绑定会**轮换** AppSecret（2026-09-12 实测：绑定之后，原先可用的那串立刻变成
#: 100016）。新值本身有没有"生效延迟"**没有验证过** —— 只观测到一次"绑定后立刻校验
#: 失败、稍后再试通过"，而那次平台上凭据整体处于坏状态，不能归因于延迟。
#: 这里仍留几次重试：代价很小，但把偶发失败说成"凭据无效"会让人跑去后台白重置一遍。
VERIFY_ATTEMPTS = 3
VERIFY_DELAY_SECONDS = 3.0


async def verify_credentials(appid: str, secret: str, *,
                             attempts: int = VERIFY_ATTEMPTS,
                             delay: float = VERIFY_DELAY_SECONDS) -> dict[str, Any]:
    """确认这对凭据真的能换到 access_token。

    返回 ``{"ok", "attempts", "expires_in", "error"}``。只有拿到 token 才算 ok；其余
    （含网络异常）都带最后一次的错误说明返回 ``ok=False``，由调用方决定怎么讲给用户。
    """
    appid = str(appid or "").strip()
    secret = str(secret or "").strip()
    if not appid or not secret:
        return {"ok": False, "attempts": 0, "expires_in": 0, "error": "缺 appid 或 secret"}

    last = "未知错误"
    for i in range(1, max(1, attempts) + 1):
        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT_MS / 1000) as client:
                resp = await client.post(
                    TOKEN_URL, json={"appId": appid, "clientSecret": secret})
                data = resp.json()
            token = str((data or {}).get("access_token") or "")
            if token:
                return {"ok": True, "attempts": i,
                        "expires_in": int(data.get("expires_in") or 0), "error": ""}
            last = f"HTTP {resp.status_code} {str(data)[:200]}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if i < attempts:
            await asyncio.sleep(delay)
    return {"ok": False, "attempts": attempts, "expires_in": 0, "error": last}


# ── 流程 ────────────────────────────────────────────────────

def qr_url(task_id: str, *, host: str = BIND_HOST) -> str:
    """二维码内容。前端把它渲染成码，用户用手机 QQ 扫。"""
    return f"https://{host}{CONNECT_PATH}?task_id={task_id}"


async def _post_json(url: str, payload: dict[str, Any],
                      *, timeout_ms: int = API_TIMEOUT_MS) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, dict) else {}


async def create_bind_task(*, host: str = BIND_HOST) -> BindSession:
    """创建一次绑定任务，返回可展示的二维码。"""
    bind_key = generate_bind_key()
    data = await _post_json(f"https://{host}{CREATE_PATH}", {"key": bind_key})
    payload = data.get("data")
    if not isinstance(payload, dict):
        payload = {}
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("绑定任务响应缺少 task_id")
    return BindSession(task_id=task_id, bind_key=bind_key,
                       qrcode=qr_url(task_id, host=host))


async def poll_bind_result(session: BindSession, *, host: str = BIND_HOST) -> dict[str, Any]:
    """轮询一次绑定结果。

    返回 ``{"status": "pending"|"completed"|"expired"|"none", ...}``；``completed`` 时
    额外带 ``appid``、已解密的 ``secret``，以及 **``user_openid``**（扫码者本人的
    openid —— 调用方用它把扫码者设成管理员）。
    """
    data = await _post_json(f"https://{host}{POLL_PATH}", {"task_id": session.task_id})
    payload = data.get("data")
    if not isinstance(payload, dict):
        payload = {}

    raw_status = payload.get("status")
    try:
        raw_status = int(raw_status)
    except (TypeError, ValueError):
        raw_status = STATUS_NONE
    status = _STATUS_TEXT.get(raw_status, "none")
    out: dict[str, Any] = {"status": status, "raw_status": raw_status}
    if status != "completed":
        return out

    # 平台回的键是 ``bot_appid``（2026-09-12 真机实测）；``appid`` 只作兜底 ——
    # 「创建新的机器人」那条路径没实测过，不能赌它用的是同一个名字。
    appid = str(payload.get("bot_appid") or payload.get("appid") or "").strip()
    encrypted = str(payload.get("bot_encrypt_secret") or "").strip()
    if not appid or not encrypted:
        out["status"] = "failed"
        out["error"] = "平台返回的凭据不完整（缺 appid 或加密密钥）"
        return out
    out["appid"] = appid
    out["secret"] = decrypt_bot_secret(encrypted, session.bind_key)
    # 扫码者本人的 openid。开放平台拿不到 QQ 号、只给 openid，而这是唯一一个
    # "扫码的人是谁"由平台背书的时刻 —— 调用方拿它把扫码者设成管理员。
    out["user_openid"] = str(payload.get("user_openid") or "").strip()
    return out


# ── 二维码渲染 ──────────────────────────────────────────────

def render_qr_png(data: str, dest: "Path | str") -> bool:
    """把二维码内容渲染成 PNG 落到 ``dest``，供前端 ``<img>`` 取。

    服务端渲染而不是前端画：前端画要引一个 QR 库（CDN 或 vendoring），而运行环境里
    已经有 ``qrcode`` + ``PIL`` —— 走静态目录和 NapCat 那个登录二维码是同一条路，
    不引入新的前端依赖。渲染失败返回 False，由调用方决定怎么降级。
    """
    import pathlib

    try:
        import qrcode

        path = pathlib.Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        qrcode.make(str(data or "")).save(path)
        return True
    except Exception:
        return False


# ── 机器人账本（避免每扫一次就多一个）─────────────────────

#: 设置里存机器人的键。每条是 ``{appid, secret, note, created_at}``。
BOTS_KEY = "qq_open_bots"
#: 当前使用的机器人 appid。
ACTIVE_KEY = "qq_open_active_bot"


def list_bots(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """已有机器人列表（账本）。字段缺失/类型不对时返回空表，不抛。"""
    bots = settings.get(BOTS_KEY)
    if not isinstance(bots, list):
        return []
    return [b for b in bots if isinstance(b, dict) and str(b.get("appid") or "").strip()]


def find_bot(settings: dict[str, Any], appid: str) -> dict[str, Any] | None:
    target = str(appid or "").strip()
    for bot in list_bots(settings):
        if str(bot.get("appid") or "").strip() == target:
            return bot
    return None


def pick_reusable_bot(settings: dict[str, Any]) -> dict[str, Any] | None:
    """要复用哪个机器人 —— **这是不无限建新机器人的关键**。

    优先当前选中的那个；它没了就退到最近创建的一个。返回 ``None`` 表示账本为空、
    确实需要走一次扫码创建。
    """
    active = str(settings.get(ACTIVE_KEY) or "").strip()
    if active:
        found = find_bot(settings, active)
        if found:
            return found
    bots = list_bots(settings)
    if not bots:
        return None
    return max(bots, key=lambda b: float(b.get("created_at") or 0))


def remember_bot(settings: dict[str, Any], *, appid: str, secret: str,
                 note: str = "") -> dict[str, Any]:
    """把一个机器人记进账本并设为当前使用。就地改 ``settings``，返回那条记录。

    同一个 appid 重复记只更新（不会堆重复项）；``created_at`` 保持首次的时间 ——
    它标的是"这个机器人是何时建的"，不是"何时被改过"。
    """
    appid = str(appid or "").strip()
    existing = find_bot(settings, appid)
    if existing is not None:
        existing["secret"] = str(secret or "")
        if note:
            existing["note"] = note
        settings[ACTIVE_KEY] = appid
        return existing

    record = {
        "appid": appid,
        "secret": str(secret or ""),
        "note": str(note or ""),
        "created_at": time.time(),
    }
    settings.setdefault(BOTS_KEY, [])
    if not isinstance(settings[BOTS_KEY], list):
        settings[BOTS_KEY] = []
    settings[BOTS_KEY].append(record)
    settings[ACTIVE_KEY] = appid
    return record
