"""steam_manager — Steam 管理 + 游戏助手。

安全边界：只读本地库元数据；启动游戏需用户明确指令；截图与行为跟踪
仅在用户开启监控后运行；键鼠接管（YUI 操作游戏）默认关闭，必须由
用户显式授权（take_control），且仅作用于当前前台游戏窗口；不读取
账号、凭据、云存档等敏感数据。
"""

from __future__ import annotations

import os
import time
import webbrowser

from plugin.sdk.plugin import (
    NekoPluginBase,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from . import control, steam


def _to_png(bmp_path: str) -> str:
    """把 BMP 截图转成 PNG 再返回（YUI 视觉通道只认 PNG/JPG，BMP 会读不出画面）。

    转换失败时原样返回 BMP 路径（避免截图丢失），输出文本会如实标注。
    """
    if not bmp_path or not bmp_path.lower().endswith(".bmp"):
        return bmp_path
    try:
        from PIL import Image

        png_path = bmp_path[:-4] + ".png"
        Image.open(bmp_path).convert("RGB").save(png_path, "PNG")
        return png_path
    except Exception:
        return bmp_path


def _image_entry(image_path: str) -> list | None:
    """把截图文件读成 N.E.K.O ``images`` 通道条目 [{data_b64, mime}]。

    关键：N.E.K.O 只会把工具返回 dict 的 ``images`` 数组喂给模型视觉——
    ``screenshot`` 字段只是文本路径，YUI 永远收不到图（此前'看不到画面'的真正根因）。
    超 2MB base64 上限时用 PIL 缩图存 JPEG（N.E.K.O 交付侧还会再压缩到 ≤500KB）。
    """
    if not image_path:
        return None
    try:
        import base64

        with open(image_path, "rb") as fh:
            raw = fh.read()
        b64 = base64.b64encode(raw).decode("ascii")
        if len(b64) <= 2 * 1024 * 1024:
            return [{"data_b64": b64, "mime": "image/png"}]
        from io import BytesIO

        from PIL import Image

        im = Image.open(image_path).convert("RGB")
        w, h = im.size
        scale = min(1.0, 1200.0 / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=82)
        return [{"data_b64": base64.b64encode(buf.getvalue()).decode("ascii"), "mime": "image/jpeg"}]
    except Exception:
        return None


@neko_plugin
class SteamManagerPlugin(NekoPluginBase):
    """N.E.K.O. 插件：Steam 游戏库管理与游戏助手。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._steam_path: str | None = None
        self._state = control.ControlState()
        self._watcher = control.FrameWatcher(self._state)

    # ══════════ 跨插件优先级：civ6 运营锁让位 ══════════
    # civ6_buddy 运营托管期间写 `%LOCALAPPDATA%\N.E.K.O\config\civ6_ops_active`（带时间戳），
    # 见新鲜锁（<5 分钟）即让位：本插件所有操作拒绝执行，避免抢占 YUI 运营注意力。
    _CIV6_OPS_LOCK_TTL = 300.0

    def _civ6_ops_yield(self) -> dict | None:
        p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "N.E.K.O", "config", "civ6_ops_active")
        if not p or not os.path.isfile(p):
            return None
        try:
            fresh = (time.time() - os.path.getmtime(p)) < self._CIV6_OPS_LOCK_TTL
        except OSError:
            return None
        if not fresh:
            return None
        return {
            "output": (
                "YUI 正在托管运营《文明6》（检测到 civ6_buddy 运营锁，Steam 管理已让位暂停）。"
                "为避免抢占运营注意力，本轮不执行 Steam 操作；如确需操作，请先让 YUI "
                "调用 civ6_ops_handoff 交还文明6 控制权后再来。"
            ),
            "is_error": True,
            "error": "CIV6_OPS_ACTIVE_YIELD",
        }

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._steam_path = steam.find_steam_path()
        return {"status": "ready", "plugin": "steam_manager"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._watcher.stop()
        self._state.disable_watch()
        self._state.disable_control()
        return {"status": "shutdown"}

    # ══════════ Steam 基础工具 ══════════

    @llm_tool(
        name="steam_status",
        description=(
            "查询 Steam 客户端状态。何时用：用户问'Steam 开着吗/Steam 装在哪/检测一下 Steam'等。"
            "返回 Steam 是否运行、安装路径（未安装则说明如何配置）。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=15.0,
    )
    async def steam_status(self, **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        path = self._steam_path or steam.find_steam_path()
        if not path:
            return {"output": "未检测到 Steam 安装目录。请确认已安装 Steam 客户端（常见路径 C:\\Program Files (x86)\\Steam）。", "is_error": True, "error": "STEAM_NOT_FOUND"}
        running = steam.steam_running()
        return {
            "output": f"Steam 安装路径：{path}\n客户端运行中：{'是' if running else '否'}",
            "is_error": False,
        }

    @llm_tool(
        name="list_games",
        description=(
            "列出 Steam 本地游戏库中的游戏。何时用：用户问'我 Steam 里有什么游戏/列出游戏/查看游戏库'时。"
            "参数 keyword：可选，按名称模糊过滤（如 'cs' 匹配 Counter-Strike）；limit：可选，返回条数上限（默认 50，最大 200）。"
            "返回游戏的 AppID、名称、占用空间与安装状态。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "可选：按名称模糊过滤"},
                "limit": {"type": "integer", "description": "可选：条数上限，默认 50"},
            },
        },
        timeout=30.0,
    )
    async def list_games(self, *, keyword: str = "", limit: int = 0, **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        keyword = (keyword or params.get("keyword") or "").strip()
        try:
            limit = int(limit or params.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        path = self._steam_path or steam.find_steam_path()
        if not path:
            return {"output": "未检测到 Steam 安装目录，无法读取游戏库。", "is_error": True, "error": "STEAM_NOT_FOUND"}
        games = steam.find_game(path, keyword, limit=limit)
        installed = [g for g in games if g["installed"]]
        if not installed:
            return {"output": f"未找到匹配的已安装游戏（关键词：{keyword or '全部'}）。", "is_error": True, "error": "NO_MATCH"}
        shown = installed[:limit]
        lines = [
            f"{g['name']}（AppID {g['app_id']}，{g['size_mb']} MB，{g['state']}"
            + (f"，中文名：{steam._GAME_CN_NAMES.get(g['app_id'], '')}" if steam._GAME_CN_NAMES.get(g['app_id']) else "")
            + "）"
            for g in shown
        ]
        head = f"共找到 {len(installed)} 个已安装游戏，显示前 {len(shown)} 个："
        return {"output": head + "\n" + "\n".join(lines), "is_error": False, "games": shown}

    @llm_tool(
        name="launch_game",
        description=(
            "启动 Steam 游戏。何时用：用户明确要求'打开/启动/玩 <游戏名或AppID>'时。"
            "参数 game：游戏名称（支持模糊匹配）或 AppID 数字。"
            "限制：仅当 Steam 已安装且游戏已下载时才能启动；名称匹配到多个游戏时会列出候选让用户确认。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "游戏名称或 AppID"},
            },
            "required": ["game"],
        },
        timeout=30.0,
    )
    async def launch_game(self, *, game: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        game = (game or params.get("game") or "").strip()
        if not game:
            return {"output": "请告诉我你想启动哪个游戏（名称或 AppID）。", "is_error": True, "error": "NO_GAME"}
        path = self._steam_path or steam.find_steam_path()
        if not path:
            return {"output": "未检测到 Steam 安装目录，无法启动游戏。", "is_error": True, "error": "STEAM_NOT_FOUND"}
        matches = steam.find_game(path, game)
        installed = [g for g in matches if g["installed"]]
        if not installed:
            return {"output": f"未找到已安装的游戏「{game}」。可先用 list_games 查看游戏库。", "is_error": True, "error": "NO_MATCH"}
        if len(installed) > 1:
            candidates = "、".join(f"{g['name']}（{g['app_id']}）" for g in installed[:10])
            return {"output": f"「{game}」匹配到多个游戏，请指定精确名称或 AppID：{candidates}", "is_error": True, "error": "AMBIGUOUS"}
        target = installed[0]
        result = steam.launch_game(path, target["app_id"])
        if not result.get("ok"):
            return {"output": result.get("output", "启动失败。"), "is_error": True, "error": result.get("error", "LAUNCH_FAILED")}
        return {"output": f"正在启动「{target['name']}」（AppID {target['app_id']}）。", "is_error": False}

    # ══════════ 账号 / 好友 / 价格 / 促销（v0.3.0） ══════════

    def _steam_id(self, steam_id: str) -> str:
        """解析 steam_id：显式参数优先，否则读本地最近登录账号。"""
        steam_id = (steam_id or "").strip()
        if steam_id:
            return steam_id
        path = self._steam_path or steam.find_steam_path()
        local = steam.read_local_account(path)
        return (local or {}).get("steam_id") or ""

    @llm_tool(
        name="steam_account",
        description=(
            "读取 Steam 账户信息。何时用：用户问'我的 Steam 账号是什么/账户信息/我是谁/看看我的 Steam 资料'时。"
            "默认读取本地 Steam 最近登录账号（昵称 + SteamID）；若配置了 STEAM_API_KEY（环境变量或插件目录 steam_api_key.txt），"
            "额外返回在线状态/国家/资料页。参数 steam_id：可选，指定要查询的 64 位 SteamID。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "steam_id": {"type": "string", "description": "可选：64 位 SteamID（默认取本地最近登录账号）"},
            },
        },
        timeout=20.0,
    )
    async def steam_account(self, *, steam_id: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        steam_id = (steam_id or params.get("steam_id") or "").strip()
        path = self._steam_path or steam.find_steam_path()
        local = steam.read_local_account(path)
        key = steam.api_key()
        if not local and not steam_id and not key:
            return {
                "output": "未读取到 Steam 本地账号（未检测到 Steam 安装目录，或 config/loginusers.vdf 不存在），也未配置 STEAM_API_KEY。\n"
                "可用方式：安装并登录 Steam 客户端，或配置 STEAM_API_KEY（环境变量 / 插件目录 steam_api_key.txt）。",
                "is_error": True,
                "error": "NO_ACCOUNT",
            }
        sid = steam_id or (local or {}).get("steam_id") or ""
        lines = []
        if local and (not steam_id or steam_id == local["steam_id"]):
            lines.append(f"本地账号：{local['name'] or '未知'}（SteamID {local['steam_id']}）")
        elif local:
            lines.append(f"本地最近登录账号：{local['name'] or '未知'}（SteamID {local['steam_id']}）")
        if key and sid:
            try:
                info = steam.account_info(key, sid)
                if info:
                    lines.append(f"账户名：{info['name']}")
                    lines.append(f"在线状态：{info['state']}")
                    lines.append(f"国家/地区：{info['country'] or '未知'}")
                    lines.append(f"资料页：{info['url']}")
                    return {"output": "\n".join(lines), "is_error": False, "account": info}
            except (OSError, ValueError) as exc:
                lines.append(f"在线账户信息查询失败：{exc}")
        elif key and not sid:
            lines.append("已配置 STEAM_API_KEY，但未读取到本地 SteamID；请传 steam_id 参数或登录 Steam 客户端。")
        return {"output": "\n".join(lines), "is_error": False}

    @llm_tool(
        name="steam_friends",
        description=(
            "读取 Steam 好友列表及好友信息。何时用：用户问'我的 Steam 好友有哪些/好友列表/XX 在不在线/看看好友'时。"
            "需要配置 STEAM_API_KEY（环境变量或插件目录 steam_api_key.txt）。"
            "参数 steam_id：可选，默认查询本地最近登录账号；name_filter：可选，按昵称过滤好友。"
            "返回每个好友的昵称、SteamID、在线状态。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "steam_id": {"type": "string", "description": "可选：64 位 SteamID（默认取本地最近登录账号）"},
                "name_filter": {"type": "string", "description": "可选：按昵称过滤好友"},
            },
        },
        timeout=30.0,
    )
    async def steam_friends(self, *, steam_id: str = "", name_filter: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        steam_id = (steam_id or params.get("steam_id") or "").strip()
        name_filter = (name_filter or params.get("name_filter") or "").strip()
        key = steam.api_key()
        if not key:
            return {"output": "查询好友列表需要 Steam Web API Key。请配置 STEAM_API_KEY（环境变量）或把 Key 写入插件目录的 steam_api_key.txt。", "is_error": True, "error": "NO_API_KEY"}
        sid = self._steam_id(steam_id)
        if not sid:
            return {"output": "未读取到本地 SteamID。请登录 Steam 客户端或传入 steam_id 参数。", "is_error": True, "error": "NO_STEAM_ID"}
        try:
            friends = steam.friend_list(key, sid)
        except (OSError, ValueError) as exc:
            return {"output": f"好友列表查询失败：{exc}", "is_error": True, "error": "FRIEND_LIST_FAILED"}
        if not friends:
            return {"output": "未获取到好友列表（可能是 API Key 无效、隐私设置或没有好友）。", "is_error": True, "error": "NO_FRIENDS"}
        if name_filter:
            friends = [f for f in friends if name_filter.lower() in f["name"].lower()]
            if not friends:
                return {"output": f"没有昵称匹配「{name_filter}」的好友。", "is_error": True, "error": "NO_MATCH"}
        lines = [f"{f['name']}（{f['state']}，SteamID {f['steam_id']}）" for f in friends]
        return {"output": f"共 {len(friends)} 位好友：\n" + "\n".join(lines), "is_error": False, "friends": friends}

    @llm_tool(
        name="steam_reply_friend",
        description=(
            "给 Steam 好友发送一条消息（网页聊天）。何时用：用户明确要求'给 XX 发消息/回复好友/跟 XX 说一句'时。"
            "需要：① STEAM_API_KEY（解析好友名→SteamID）；② steam_cookie.txt（steamcommunity 登录 cookie，含 sessionid）。"
            "参数 friend：好友昵称或 64 位 SteamID；text：消息内容（最长 500 字）。"
            "安全：这是对外写操作，仅在用户明确要求发送时调用；发送成功后原样回复'已发送。'，不复述消息内容。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "friend": {"type": "string", "description": "好友昵称或 64 位 SteamID"},
                "text": {"type": "string", "description": "消息内容，最长 500 字"},
            },
            "required": ["friend", "text"],
        },
        timeout=30.0,
    )
    async def steam_reply_friend(self, *, friend: str = "", text: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        friend = (friend or params.get("friend") or "").strip()
        text = (text or params.get("text") or "").strip()
        if not friend or not text:
            return {"output": "请提供好友（昵称或 SteamID）和消息内容。", "is_error": True, "error": "EMPTY"}
        key = steam.api_key()
        cookie = steam.community_cookie()
        if not cookie:
            return {"output": "回复好友需要 steamcommunity 登录 cookie。请把登录后浏览器 Cookie 写入插件目录 steam_cookie.txt（需含 sessionid）。", "is_error": True, "error": "NO_COOKIE"}
        sid = self._steam_id("")
        if not sid:
            return {"output": "未读取到本地 SteamID（需先登录 Steam 客户端，用于确认发消息人身份）。", "is_error": True, "error": "NO_STEAM_ID"}
        friend_sid = friend if friend.isdigit() else ""
        if not friend_sid:
            if not key:
                return {"output": "要用好友昵称发消息需要 STEAM_API_KEY（用于把昵称解析为 SteamID）。", "is_error": True, "error": "NO_API_KEY"}
            try:
                friends = steam.friend_list(key, sid)
            except (OSError, ValueError) as exc:
                return {"output": f"好友列表查询失败：{exc}", "is_error": True, "error": "FRIEND_LIST_FAILED"}
            matches = [f for f in friends if friend.lower() in f["name"].lower()]
            if not matches:
                return {"output": f"在好友列表中没有找到「{friend}」。可先用 steam_friends 查看好友。", "is_error": True, "error": "NO_FRIEND"}
            if len(matches) > 1:
                cand = "、".join(f"{f['name']}（{f['steam_id']}）" for f in matches[:8])
                return {"output": f"「{friend}」匹配到多个好友，请指定 SteamID：{cand}", "is_error": True, "error": "AMBIGUOUS"}
            friend_sid = matches[0]["steam_id"]
        try:
            result = steam.send_friend_message(cookie, sid, friend_sid, text)
        except ValueError as exc:
            return {"output": str(exc), "is_error": True, "error": "SEND_FAILED"}
        return {"output": "已发送。", "is_error": False, "result": result}

    @llm_tool(
        name="steam_game_price",
        description=(
            "查询 Steam 游戏价格（国区，人民币）。何时用：用户问'XX 游戏多少钱/价格/贵不贵/值不值/打不打折'时。"
            "参数 game：游戏名称或 AppID。返回当前价、原价与折扣（0=无折扣）。无需 API Key。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "游戏名称或 AppID"},
            },
            "required": ["game"],
        },
        timeout=30.0,
    )
    async def steam_game_price(self, *, game: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        game = (game or params.get("game") or "").strip()
        if not game:
            return {"output": "请提供要查价格的游戏名称或 AppID。", "is_error": True, "error": "NO_GAME"}
        try:
            if game.isdigit():
                info = steam.app_price(game)
                if not info:
                    return {"output": f"未找到 AppID {game} 的价格信息（可能不存在或商店接口无数据）。", "is_error": True, "error": "NOT_FOUND"}
                price = "免费" if info["free"] else f"{info['price_cny']:.2f} 元"
                original = "" if info["free"] else f"（原价 {info['original_cny']:.2f} 元）" if info["original_cny"] else ""
                disc = f"，折扣 {info['discount']}%" if info["discount"] else ""
                return {"output": f"{info['name']}（AppID {info['app_id']}）：{price}{original}{disc}", "is_error": False, "price": info}
            results = steam.search_store(game, limit=3)
            if not results:
                return {"output": f"在 Steam 商店未搜到「{game}」。", "is_error": True, "error": "NOT_FOUND"}
            lines = []
            for r in results:
                if r["price_cny"] is None:
                    lines.append(f"{r['name']}（AppID {r['app_id']}）：免费" if r["type"] in ("game", "dlc", "application") else f"{r['name']}：无价格")
                else:
                    orig = f"（原价 {r['original_cny']:.2f} 元）" if r["original_cny"] else ""
                    disc = f"，折扣 {r['discount']}%" if r["discount"] else ""
                    lines.append(f"{r['name']}（AppID {r['app_id']}）：{r['price_cny']:.2f} 元{orig}{disc}")
            return {"output": "\n".join(lines), "is_error": False, "results": results}
        except (OSError, ValueError) as exc:
            return {"output": f"价格查询失败：{exc}", "is_error": True, "error": "PRICE_FAILED"}

    @llm_tool(
        name="steam_promotions",
        description=(
            "查询 Steam 商店当前促销/特惠游戏（国区首页特色分类）。何时用：用户问'Steam 有什么打折/促销/特惠/薅羊毛'时。"
            "参数 limit：可选，返回条数上限（默认 15，最大 30）。返回游戏名、现价、原价与折扣。无需 API Key。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "可选：条数上限，默认 15"},
            },
        },
        timeout=30.0,
    )
    async def steam_promotions(self, *, limit: int = 0, **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        try:
            limit = int(limit or params.get("limit") or 15)
        except (TypeError, ValueError):
            limit = 15
        limit = max(1, min(limit, 30))
        try:
            items = steam.store_promotions(limit=limit)
        except (OSError, ValueError) as exc:
            return {"output": f"促销查询失败：{exc}", "is_error": True, "error": "PROMO_FAILED"}
        if not items:
            return {"output": "当前没有拉取到特惠游戏（可能接口无数据）。", "is_error": True, "error": "NO_PROMO"}
        lines = []
        for it in items:
            price = "免费" if it["price_cny"] is None else f"{it['price_cny']:.2f} 元"
            orig = f"（原价 {it['original_cny']:.2f} 元）" if it["original_cny"] else ""
            lines.append(f"{it['name']}：{price}{orig}，-{it['discount']}%（AppID {it['app_id']}）")
        return {"output": f"Steam 当前特惠（{len(items)} 个）：\n" + "\n".join(lines), "is_error": False, "promotions": items}

    @llm_tool(
        name="steam_open_app",
        description=(
            "打开任意 Steam 游戏/应用（含其他开发者制作、未安装的游戏）。何时用：用户说'打开/启动 XX 游戏/应用'，"
            "且该游戏不在本地库或想打开商店页时。参数 game：游戏名称或 AppID。"
            "已安装 → 直接启动；未安装 → 打开 Steam 商店页（用户可点击安装）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "游戏名称或 AppID"},
            },
            "required": ["game"],
        },
        timeout=30.0,
    )
    async def steam_open_app(self, *, game: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        game = (game or params.get("game") or "").strip()
        if not game:
            return {"output": "请提供要打开的游戏名称或 AppID。", "is_error": True, "error": "NO_GAME"}
        path = self._steam_path or steam.find_steam_path()
        app_id = game if game.isdigit() else ""
        installed = False
        if path:
            matches = steam.find_game(path, game) if not app_id else [g for g in steam.scan_games(path, limit=200) if g["app_id"] == app_id]
            installed_matches = [g for g in matches if g["installed"]]
            if installed_matches:
                if len(installed_matches) > 1:
                    cand = "、".join(f"{g['name']}（{g['app_id']}）" for g in installed_matches[:10])
                    return {"output": f"「{game}」匹配到多个已安装游戏，请指定精确名称或 AppID：{cand}", "is_error": True, "error": "AMBIGUOUS"}
                installed = True
                app_id = installed_matches[0]["app_id"]
                return await self.launch_game(game=app_id, parameters={})
            if app_id:
                installed = False
            else:
                try:
                    results = steam.search_store(game, limit=1)
                except (OSError, ValueError) as exc:
                    return {"output": f"搜索「{game}」失败：{exc}", "is_error": True, "error": "SEARCH_FAILED"}
                if not results:
                    return {"output": f"在 Steam 商店未搜到「{game}」，无法打开。", "is_error": True, "error": "NOT_FOUND"}
                app_id = results[0]["app_id"]
        elif not app_id:
            try:
                results = steam.search_store(game, limit=1)
            except (OSError, ValueError) as exc:
                return {"output": f"搜索「{game}」失败：{exc}", "is_error": True, "error": "SEARCH_FAILED"}
            if not results:
                return {"output": f"在 Steam 商店未搜到「{game}」。", "is_error": True, "error": "NOT_FOUND"}
            app_id = results[0]["app_id"]
        if not app_id:
            return {"output": "未能确定 AppID。", "is_error": True, "error": "NO_APP_ID"}
        result = steam.open_app(path, app_id, installed)
        if not result.get("ok"):
            return {"output": result.get("output", "打开失败。"), "is_error": True, "error": result.get("error", "OPEN_FAILED")}
        return {"output": result.get("output", "已打开。"), "is_error": False}

    # ══════════ 凭证自动配置（v0.3.2） ══════════

    @llm_tool(
        name="steam_credentials",
        description=(
            "配置 Steam 扩展能力所需凭证（STEAM_API_KEY 与 steam_cookie.txt）。何时用：用户问'配置 Steam/"
            "给 Steam 配 API Key/好友功能用不了/回复好友用不了/自动配置一下'或插件提示缺少凭证时。"
            "三种模式（mode）：skip=不配置（保留本地库/查价/促销能力，好友/发消息不可用）；"
            "yui=拿到凭证由 YUI 配置：把 API Key 传 api_key、cookie 传 cookie 参数，写入插件目录并立即验证；"
            "auto=全自动：有凭证同样写入验证；没有任何凭证时先自动探测本机 Chrome/Edge 里的 steamcommunity 登录 Cookie"
            "（找到即写入并验证），探测不到再打开官方获取页并给出指引（验证由助手协助）。验证通过才报告配置成功；"
            "验证失败必须如实说明原因，不得声称配置完成。"
            "安全：Key/Cookie 只写入本地插件目录并验证有效性，不回显内容。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["skip", "yui", "auto"], "description": "skip=不配置 / yui=提供凭证由YUI配置 / auto=全自动"},
                "api_key": {"type": "string", "description": "Steam Web API Key（yui/auto 时提供）"},
                "cookie": {"type": "string", "description": "steamcommunity 登录 Cookie（yui/auto 时提供，需含 sessionid）"},
            },
        },
        timeout=30.0,
    )
    async def steam_credentials(self, *, mode: str = "", api_key: str = "", cookie: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        mode = (mode or params.get("mode") or "auto").strip().lower()
        api_key = (api_key or params.get("api_key") or "").strip()
        cookie = (cookie or params.get("cookie") or "").strip()
        status = steam.credentials_status()
        if mode == "skip":
            return {
                "output": "已选择不配置 Steam 凭证。本地游戏库/启动游戏/查价格/看促销功能不受影响；"
                "好友列表与回复好友功能保持不可用。随时可再次调用本工具配置。",
                "is_error": False,
                "result": {"mode": "skip", **status},
            }
        saved: list[str] = []
        problems: list[str] = []
        if api_key:
            try:
                steam.save_api_key(api_key)
                ok, msg = steam.validate_api_key(api_key)
                saved.append(f"API Key：{'已写入并验证通过' if ok else f'已写入但验证未通过（{msg}）'}")
                if not ok:
                    problems.append(msg)
            except ValueError as exc:
                problems.append(str(exc))
        if cookie:
            try:
                steam.save_community_cookie(cookie)
                ok, msg = steam.validate_community_cookie(cookie)
                saved.append(f"Cookie：{'已写入并验证通过' if ok else f'已写入但验证未通过（{msg}）'}")
                if not ok:
                    problems.append(msg)
            except ValueError as exc:
                problems.append(str(exc))
        if not api_key and not cookie:
            if mode == "yui":
                return {
                    "output": "未收到凭证（mode=yui 需要你提供 api_key 或 cookie 参数）。可调用 auto 模式自动探测或打开获取页。",
                    "is_error": True,
                    "error": "NO_CREDENTIAL",
                }
            # auto：先自动探测浏览器里的 steamcommunity 登录 cookie
            probe = steam.probe_browser_cookies()
            if probe["found"]:
                try:
                    steam.save_community_cookie(
                        "sessionid=" + probe["sessionid"] + "; steamLoginSecure=" + probe["steam_login_secure"]
                        if probe["steam_login_secure"]
                        else "sessionid=" + probe["sessionid"] + "; steamLogin=" + probe["steam_login"]
                    )
                    ok, msg = steam.validate_community_cookie(
                        "sessionid=" + probe["sessionid"] + "; steamLoginSecure=" + probe["steam_login_secure"]
                        if probe["steam_login_secure"]
                        else "sessionid=" + probe["sessionid"] + "; steamLogin=" + probe["steam_login"]
                    )
                    if ok:
                        return {
                            "output": "已从浏览器自动检测到 Steam 登录 Cookie（来源：" + probe["browser"] + "）并写入验证通过：" + msg + "\n" + _cred_status_line(steam.credentials_status()),
                            "is_error": False,
                            "result": {"mode": "auto", "source": "browser_probe", **steam.credentials_status()},
                        }
                    return {
                        "output": "检测到 Cookie 但验证未通过（" + msg + "），未写入。可重新在浏览器登录后重试。",
                        "is_error": True,
                        "error": "CREDENTIAL_INVALID",
                        "result": {"mode": "auto", "source": "browser_probe", **steam.credentials_status()},
                    }
                except ValueError as exc:
                    return {
                        "output": "检测到 Cookie 但写入失败：" + str(exc),
                        "is_error": True,
                        "error": "CREDENTIAL_INVALID",
                        "result": {"mode": "auto", **steam.credentials_status()},
                    }
            # 探测不到：打开官方获取页 + 指引
            try:
                webbrowser.open("https://steamcommunity.com/dev/apikey")
                opened = "已为你打开 Steam API Key 获取页（https://steamcommunity.com/dev/apikey）。\n"
            except OSError:
                opened = "请打开 https://steamcommunity.com/dev/apikey 获取 API Key。\n"
            probe_note = f"浏览器 Cookie 自动探测未找到登录态（{probe.get('error') or '浏览器库中无 steamcommunity cookie'}）。\n" if not probe["found"] else ""
            return {
                "output": (
                    opened
                    + probe_note
                    + "步骤：1) 在页面登录你的 Steam 账号，域名填 localhost 即可点创建；"
                    "2) 把生成的 Key 复制给我（说'配置 Steam，Key 是……'），我会写入并验证；"
                    "3) 回复好友还需 steamcommunity 登录态：登录 steamcommunity.com 后重新调用本工具（auto 会再次自动探测 Cookie）。"
                    + _cred_status_line(status)
                ),
                "is_error": False,
                "result": {"mode": "auto", **status},
            }
        new_status = steam.credentials_status()
        summary = "\n".join(saved) if saved else "本次未写入任何凭证。"
        if problems:
            return {
                "output": summary + "\n\n⚠ 以下验证未通过，请修正后重试：\n" + "\n".join(problems) + "\n" + _cred_status_line(new_status),
                "is_error": True,
                "error": "CREDENTIAL_INVALID",
                "result": {"mode": mode, **new_status},
            }
        return {
            "output": summary + "\n" + _cred_status_line(new_status),
            "is_error": False,
            "result": {"mode": mode, **new_status},
        }

    # ══════════ 游戏助手工具 ══════════

    @llm_tool(
        name="game_capture",
        description=(
            "截取当前前台游戏窗口的画面。何时用：用户问'现在游戏里是什么情况/截个图看看/这局怎么样了'，"
            "或你需要了解当前游戏画面时。返回窗口标题、进程、截图文件路径与时间；"
            "截图是 BMP 文件，路径可直接提供给视觉能力解读或让用户查看。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30.0,
    )
    async def game_capture(self, **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        import logging

        _log = logging.getLogger("N.E.K.O.Plugin_steam_manager")
        info = control.get_foreground_window_info()
        civ = control.find_civ6_window()
        _log.info("game_capture: fg=%r civ6_window=%r",
                  info and info.get("title"), civ and civ.get("title"))
        # 增强（v0.3.4）：前台窗口不是文明6 但文明6 窗口存在时，改用 PrintWindow
        # 直抓游戏窗口本体——前台被聊天/终端窗口遮挡时，屏幕区域截图只会截到遮挡层，
        # PrintWindow 向游戏窗口请求绘制，拿到的永远是游戏画面。
        fg_is_civ = info is not None and "civilization vi" in (info.get("title") or "").casefold()
        if civ is not None and not fg_is_civ:
            path = control.capture_window_printwindow(civ)
            _log.info("game_capture: printwindow=%s", bool(path))
            if path:
                self._state.note_frame(civ, path)
                self._state.note_event("capture", f"{civ['title']} / {civ['process']} (printwindow)")
                width = civ["rect"][2] - civ["rect"][0]
                height = civ["rect"][3] - civ["rect"][1]
                view_path = _to_png(path)
                fmt_note = "（PNG，视觉可直接读取）" if view_path.endswith(".png") else "（BMP，视觉可能读不出，请让 YUI 改用 civ6_ops_peek）"
                return {
                    "output": (
                        f"已截取文明6 游戏窗口画面（PrintWindow 直抓，被聊天/终端窗口遮挡也能拿到游戏本体）：\n"
                        f"窗口：{civ['title']}（进程 {civ['process']}，PID {civ['pid']}）\n"
                        f"尺寸：{width}×{height}\n截图路径：{view_path}{fmt_note}\n时间：{civ['ts']:.0f}"
                    ),
                    "is_error": False,
                    "screenshot": view_path,
                    "images": _image_entry(view_path) or [],
                }
            # PrintWindow 直抓失败（运行环境限制）：如实说明并引导改用 civ6_buddy 的 peek
            return {
                "output": (
                    f"检测到文明6 窗口存在（{civ['title']}）但 PrintWindow 直抓失败"
                    f"（进程环境限制，前台被遮挡时本工具截不到游戏本体）。\n"
                    f"请改用 civ6_buddy 插件的 civ6_ops_peek 工具确认文明6 画面——"
                    f"它会用同一条 PrintWindow 通道直抓游戏本体（被遮挡也能拿到），"
                    f"或让玩家把游戏切到前台后我再截一次。"
                ),
                "is_error": True,
                "error": "PRINTWINDOW_FAIL",
            }
        _log.info("game_capture: branch=bitblt fg=%r", info and info.get("title"))
        if not info:
            return {"output": "没有检测到前台窗口（可能窗口已最小化）。请确认游戏窗口在前台。", "is_error": True, "error": "NO_WINDOW"}
        path = control.capture_window_bmp(info)
        if not path:
            return {"output": "截图失败：无法读取屏幕区域。", "is_error": True, "error": "CAPTURE_FAILED"}
        self._state.note_frame(info, path)
        self._state.note_event("capture", f"{info['title']} / {info['process']}")
        width = info["rect"][2] - info["rect"][0]
        height = info["rect"][3] - info["rect"][1]
        view_path = _to_png(path)
        fmt_note = "（PNG，视觉可直接读取）" if view_path.endswith(".png") else "（BMP，视觉可能读不出，请让 YUI 改用 civ6_ops_peek）"
        return {
            "output": (
                f"已截取前台窗口画面：\n窗口：{info['title']}（进程 {info['process']}，PID {info['pid']}）\n"
                f"尺寸：{width}×{height}\n截图路径：{view_path}{fmt_note}\n时间：{info['ts']:.0f}"
            ),
            "is_error": False,
            "screenshot": view_path,
            "images": _image_entry(view_path) or [],
        }

    @llm_tool(
        name="watch_game",
        description=(
            "开启或关闭后台画面监控。何时用：用户要求'帮我盯着游戏/实时跟踪/看他打到哪了'，"
            "或需要持续了解游戏进展时。参数 on：true 开启、false 关闭；interval：可选，截帧间隔秒数（默认 3，最小 1）。"
            "开启后插件会周期性截取前台窗口画面并记录行为时间线，供 game_state_summary 查询。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "on": {"type": "boolean", "description": "true 开启监控，false 关闭"},
                "interval": {"type": "integer", "description": "可选：截帧间隔秒数，默认 3"},
            },
        },
        timeout=20.0,
    )
    async def watch_game(self, *, on: bool = False, interval: int = 0, **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        on = bool(params.get("on", on))
        try:
            interval = int(interval or params.get("interval") or 3)
        except (TypeError, ValueError):
            interval = 3
        if on:
            # v0.3.1 修复：重复开启时先停旧 watcher，避免多线程并存疯狂截图
            self._watcher.stop()
            self._watcher = control.FrameWatcher(self._state, interval=interval)
            self._watcher.start()
            self._state.enable_watch()
            return {"output": f"后台画面监控已开启（每 {max(1, interval)} 秒截一帧）。用 game_state_summary 查看进展，用 watch_game(on=false) 关闭。", "is_error": False}
        self._watcher.stop()
        self._state.disable_watch()
        return {"output": "后台画面监控已关闭。", "is_error": False}

    @llm_tool(
        name="game_state_summary",
        description=(
            "汇总游戏当前状态与近期行为时间线。何时用：用户问'现在什么局面/他刚才做了什么/这局进展如何'，"
            "或你需要基于画面与操作历史给出建议时。返回监控/接管状态、当前窗口、最近截图与行为记录。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=20.0,
    )
    async def game_state_summary(self, **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        summary = self._state.summary()
        lines = []
        lines.append(f"监控状态：{'开启' if summary['watch_enabled'] else '关闭'} | 接管状态：{'YUI 已接管' if summary['control_enabled'] else '未接管'}")
        win = summary.get("last_window")
        if win:
            lines.append(f"当前窗口：{win.get('title', '')}（进程 {win.get('process', '')}，PID {win.get('pid', 0)}）")
        else:
            lines.append("当前窗口：无最近记录（可先 game_capture 截一帧）")
        frames = summary.get("frames", [])
        if frames:
            latest = frames[-1]
            lines.append(f"最近截图：{latest.get('path', '')}（{latest.get('ts', 0):.0f}）")
            lines.append(f"已记录画面帧数：{len(frames)}")
        timeline = summary.get("timeline", [])
        if timeline:
            recent = timeline[-8:]
            lines.append("近期行为记录：")
            for item in recent:
                lines.append(f"  [{item.get('ts', 0):.0f}] {item.get('event', '')} {item.get('detail', '')}")
        return {"output": "\n".join(lines), "is_error": False, "summary": summary}

    @llm_tool(
        name="take_control",
        description=(
            "授权或交还 YUI 对游戏窗口的键鼠控制。何时用：用户明确说'让 YUI 接管/你来玩/帮我操作'时开启，"
            "用户说'停止接管/交还控制/我自己来'时关闭。"
            "安全：开启前必须先有最近的窗口画面（先 game_capture 或开启 watch_game）；"
            "接管仅作用于当前前台游戏窗口；用户随时可用本工具或对话直接交还。"
            "参数 on：true 开启、false 交还。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "on": {"type": "boolean", "description": "true 开启接管，false 交还"},
            },
        },
        timeout=20.0,
    )
    async def take_control(self, *, on: bool = False, **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        on = bool(params.get("on", on))
        if not on:
            self._state.disable_control()
            return {"output": "已交还控制，YUI 不再模拟键鼠操作游戏。", "is_error": False}
        if not self._state.enable_control():
            return {"output": "无法开启接管：没有最近的游戏窗口画面（先 game_capture 截一帧，或开启 watch_game 监控）。", "is_error": True, "error": "NO_WINDOW_EVIDENCE"}
        return {
            "output": "接管已开启：YUI 现在可以模拟键鼠操作当前游戏窗口。玩家随时说「停止接管」即可交还。",
            "is_error": False,
        }

    @llm_tool(
        name="game_action",
        description=(
            "在已授权的游戏窗口上执行一次键鼠动作。何时用：YUI 已获得接管授权（take_control on）后，"
            "需要点击画面或按键操作游戏时。"
            "参数 action：click（点击，需 x/y，窗口内相对坐标 0-1000）、key（按键，key 为单字母或 1-255 的虚拟键码）、"
            "text（输入文本，text 为 ASCII 内容）；x/y：点击坐标（窗口相对千分比）。"
            "安全：仅当接管开启且窗口仍在场时可用；每次动作都会记录到行为时间线。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["click", "key", "text"], "description": "动作类型"},
                "x": {"type": "integer", "description": "click 的窗口相对 X（0-1000）"},
                "y": {"type": "integer", "description": "click 的窗口相对 Y（0-1000）"},
                "key": {"type": "string", "description": "key 的单字母或虚拟键码（1-255）"},
                "text": {"type": "string", "description": "text 的 ASCII 内容"},
            },
            "required": ["action"],
        },
        timeout=20.0,
    )
    async def game_action(self, *, action: str = "", x: int = 0, y: int = 0, key: str = "", text: str = "", **_):
        _y = self._civ6_ops_yield()
        if _y:
            return _y
        params = _.get("parameters") or {}
        action = (action or params.get("action") or "").strip().lower()
        try:
            x = int(x if x else params.get("x") or 0)
            y = int(y if y else params.get("y") or 0)
        except (TypeError, ValueError):
            x, y = 0, 0
        key = (key or params.get("key") or "").strip()
        text = (text or params.get("text") or "").strip()
        if not self._state.control_enabled:
            return {"output": "接管未开启，无法执行操作。请先让用户明确授权：take_control(on=true)。", "is_error": True, "error": "CONTROL_DISABLED"}
        if not self._state.recent_window_active(within_seconds=15):
            return {"output": "最近 15 秒内没有新的窗口画面，可能游戏不在前台或已切换。请先 game_capture 确认窗口在场。", "is_error": True, "error": "WINDOW_MISSING"}
        win = self._state.last_window
        if not win:
            return {"output": "缺少窗口信息，无法执行操作。请先 game_capture。", "is_error": True, "error": "NO_WINDOW"}
        if action == "click":
            sx, sy = control.window_client_to_screen(win, max(0, min(1000, x)), max(0, min(1000, y)))
            control.mouse_click_abs(sx, sy)
            self._state.note_event("action", f"click ({x},{y}) → 屏幕 ({sx},{sy}) @ {win.get('title', '')}")
            return {"output": f"已点击窗口内 ({x},{y})（屏幕 {sx},{sy}）。", "is_error": False}
        if action == "key":
            vk = _parse_vk(key)
            if vk is None:
                return {"output": "key 参数无效：请输入单字母（如 'a'）或 1-255 的虚拟键码。", "is_error": True, "error": "BAD_KEY"}
            control.press_key(vk)
            self._state.note_event("action", f"key {key}")
            return {"output": f"已按键：{key}。", "is_error": False}
        if action == "text":
            if len(text) > 100:
                return {"output": "文本过长（上限 100 字符）。", "is_error": True, "error": "TEXT_TOO_LONG"}
            control.type_text(text)
            self._state.note_event("action", f"text {text}")
            return {"output": f"已输入文本：{text}。", "is_error": False}
        return {"output": "action 无效：可选 click / key / text。", "is_error": True, "error": "BAD_ACTION"}

    # ══════════ 自动路由入口（plugin_entry：TaskExecutor 评估可见） ══════════
    # v0.2.0 只注册了 llm_tool、无 plugin_entry，导致市场面板"入口点: 0"、
    # 评估侧无法路由到本插件（Steam 管理与游戏助手功能实际不可用）。
    # v0.2.1 注册 run 通用入口修复（方法名不能以下划线开头，bili_viewer v0.6.22 教训）。

    @plugin_entry(
        id="run",
        name="Steam管理",
        description=(
            "Steam管理（steam_manager）：管理本地 Steam 客户端与游戏库，并充当游戏助手；"
            "也可读取账户/好友信息、回复好友、查价格与促销、打开任意游戏。"
            "用户说'Steam 开着吗/我 Steam 里有什么游戏/打开/启动 <游戏>/现在游戏什么情况/截个图看看/帮我盯着游戏/"
            "让 YUI 接管/你来玩/停止接管/我的 Steam 账号/好友列表/给好友发消息/XX 多少钱/有什么打折/打开其他游戏'等请求时使用。"
            "操作：status（查客户端状态与路径）/ list（列游戏库，可按名称过滤）/ launch（启动已安装游戏）/ "
            "account（读账户信息，需 STEAM_API_KEY 可增强）/ friends（好友列表，需 STEAM_API_KEY）/ "
            "reply（给好友发消息，需 STEAM_API_KEY + steam_cookie.txt）/ "
            "price（查游戏价格，无需凭证）/ promo（查商店促销，无需凭证）/ "
            "open（打开任意游戏：已装启动、未装开商店页）/ "
            "setup（凭证自动配置：skip 不配置 / yui 提供凭证写入 / auto 全自动打开获取页并指引）/ "
            "capture（截取当前游戏画面）/ watch（开启/关闭后台画面监控）/ summary（汇总局面与行为时间线）/ "
            "control（开启/交还键鼠接管，需用户显式授权且仅作用于前台游戏窗口）/ action（click/key/text 操作，需先授权）。"
            "安全边界：只读本地库元数据；键鼠接管默认关闭、必须用户明确授权，仅作用于当前前台游戏窗口；"
            "API Key / cookie 只读取不输出；回复好友仅在用户明确要求时发送。"
            "回复纪律：直接给出查询结果或操作确认，不加多余话术，不编造游戏状态。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型：status / list / launch / account / friends / reply / price / promo / open / capture / watch / summary / control / action",
                },
                "game": {"type": "string", "description": "启动/查价/打开的游戏名称或 AppID（launch/price/open 时用）"},
                "keyword": {"type": "string", "description": "列表过滤关键词（action=list 时用）"},
                "limit": {"type": "integer", "description": "列表条数上限（list/promo 时用）"},
                "steam_id": {"type": "string", "description": "64 位 SteamID（account/friends 时用，可选）"},
                "name_filter": {"type": "string", "description": "好友昵称过滤（action=friends 时用）"},
                "friend": {"type": "string", "description": "好友昵称或 SteamID（action=reply 时用）"},
                "text": {"type": "string", "description": "发给好友的消息内容（action=reply 时用）"},
                "on": {"type": "boolean", "description": "watch / control 的开关：true 开，false 关"},
                "interval": {"type": "integer", "description": "watch 的截帧间隔秒数"},
                "click_x": {"type": "integer", "description": "action=click 的窗口相对 X（0-1000）"},
                "click_y": {"type": "integer", "description": "action=click 的窗口相对 Y（0-1000）"},
                "key": {"type": "string", "description": "action=key 的单字母或虚拟键码"},
                "sub_action": {"type": "string", "description": "action=action 时的子操作：click / key / text（v0.3.1 修复入口可达性）"},
                "mode": {"type": "string", "description": "action=setup 的凭证配置模式：skip / yui / auto"},
                "api_key": {"type": "string", "description": "action=setup 时要写入并验证的 Steam Web API Key"},
                "cookie": {"type": "string", "description": "action=setup 时要写入并验证的 steamcommunity Cookie"},
            },
        },
        timeout=30.0,
    )
    async def entry_run(self, **kwargs):
        params = kwargs.get("parameters") or {}
        action = str(kwargs.get("action") or params.get("action") or "").strip().lower()
        game = str(kwargs.get("game") or params.get("game") or "")
        keyword = str(kwargs.get("keyword") or params.get("keyword") or "")
        try:
            limit = int(kwargs.get("limit") or params.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        on = bool(kwargs.get("on", params.get("on", False)))
        try:
            interval = int(kwargs.get("interval") or params.get("interval") or 0)
        except (TypeError, ValueError):
            interval = 0
        try:
            click_x = int(kwargs.get("click_x") or params.get("click_x") or 0)
            click_y = int(kwargs.get("click_y") or params.get("click_y") or 0)
        except (TypeError, ValueError):
            click_x, click_y = 0, 0
        key = str(kwargs.get("key") or params.get("key") or "")
        sub_action = str(kwargs.get("sub_action") or params.get("sub_action") or params.get("act") or "")
        steam_id = str(kwargs.get("steam_id") or params.get("steam_id") or "")
        name_filter = str(kwargs.get("name_filter") or params.get("name_filter") or "")
        friend = str(kwargs.get("friend") or params.get("friend") or "")
        text = str(kwargs.get("text") or params.get("text") or "")
        if action in ("status", ""):
            return await self.steam_status()
        if action in ("list", "games"):
            return await self.list_games(keyword=keyword, limit=limit, parameters={})
        if action in ("launch", "start"):
            return await self.launch_game(game=game, parameters={})
        if action in ("account", "me", "profile"):
            return await self.steam_account(steam_id=steam_id, parameters={})
        if action in ("friends", "friendlist"):
            return await self.steam_friends(steam_id=steam_id, name_filter=name_filter, parameters={})
        if action in ("reply", "message"):
            return await self.steam_reply_friend(friend=friend, text=text, parameters={})
        if action in ("price", "pricing"):
            return await self.steam_game_price(game=game, parameters={})
        if action in ("setup", "credentials", "config"):
            return await self.steam_credentials(
                mode=str(kwargs.get("mode") or params.get("mode") or "auto"),
                api_key=str(kwargs.get("api_key") or params.get("api_key") or ""),
                cookie=str(kwargs.get("cookie") or params.get("cookie") or ""),
                parameters={},
            )
        if action in ("promo", "promotions", "sale"):
            return await self.steam_promotions(limit=limit, parameters={})
        if action in ("open", "open_app"):
            return await self.steam_open_app(game=game, parameters={})
        if action in ("capture", "screenshot"):
            return await self.game_capture()
        if action in ("watch", "monitor"):
            return await self.watch_game(on=on, interval=interval, parameters={})
        if action in ("summary", "state"):
            return await self.game_state_summary()
        if action in ("control", "take_control"):
            return await self.take_control(on=on, parameters={})
        if action in ("action", "operate"):
            return await self.game_action(
                action=sub_action,
                x=click_x, y=click_y, key=key, text=text, parameters={},
            )
        return {
            "output": "请指定操作：status/list/launch/account/friends/reply/price/promo/open/capture/watch/summary/control/action。",
            "is_error": True,
            "error": "NO_ACTION",
        }


def _cred_status_line(status: dict) -> str:
    return (
        f"当前配置状态：API Key{'已配置' if status['api_key_configured'] else '未配置'} | "
        f"Cookie{'已配置' if status['cookie_configured'] else '未配置'} | "
        f"好友功能：{'可用' if status['can_use_friends'] else '不可用'} | "
        f"回复好友：{'可用' if status['can_reply_friend'] else '不可用'}"
    )


def _parse_vk(key: str) -> int | None:
    """把按键名解析为虚拟键码：单字母/数字用 ASCII 码，多位数字用 VK 码。"""
    if not key:
        return None
    if len(key) == 1:
        code = ord(key.upper())
        if 48 <= code <= 90:  # 数字或字母
            return code
    if key.isdigit():
        vk = int(key)
        return vk if 1 <= vk <= 255 else None
    return None
