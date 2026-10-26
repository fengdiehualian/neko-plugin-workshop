"""小游戏标准契约：所有小游戏必须实现的适配接口。"""

from __future__ import annotations

import abc
import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("neko_arcade.contracts")


class GameAdapter(abc.ABC):
    """小游戏适配基类。子类必须实现 id/name/description 和 handle_action。

    游戏适配约定:
    - 基础属性: id/name/description/icon/version(类属性声明)
    - 关键词/别名: 写在 data/config/{id}/keywords.json(主插件统一读取,
      用于路由和 LLM 工具描述), 不在代码里声明。
    """

    id: str = ""
    name: str = ""
    description: str = ""
    version: str = "0.1.0"
    icon: str = "🎮"

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.enabled = True
        self._push: Any = None
        self._img: Any = None
        self._tts: Any = None
        self._llm: Any = None
        self._photo: Any = None
        self._render: Any = None
        self._assets: Any = None

    def bind_services(self, push=None, img=None, tts=None, llm=None,
                      photo=None, render=None, companion=None,
                      llm_gateway=None, assets=None) -> None:
        """绑定插件服务（由注册表在注册时调用），游戏可通过 self 调用。

        photo: PhotoBridge 实例(插件主体通用发图桥接), 游戏可用 self.send_photo
        自主发图, 无需自己实现图库扫描/分类/推送。
        render: RenderBridge 实例(插件主体通用**渲染桥接**), 游戏可用
        self.render_help / self.render_page / self.send_page 出图,
        **无需自己写 HTML/CSS/主题**——渲染完全由插件主体负责。

        传 None 表示"该项不动"(便于只补绑一个服务), 注册表会一次性全量绑定。
        """
        if push is not None:
            self._push = push
        if img is not None:
            self._img = img
        if tts is not None:
            self._tts = tts
        if llm is not None:
            self._llm = llm
        if photo is not None:
            self._photo = photo
        if render is not None:
            self._render = render
        if companion is not None:
            #: 本体统一的陪伴层(带宿主猫娘人格) —— 游戏台词一律用它
            self._companion_svc = companion
        if llm_gateway is not None:
            #: 本体统一的 LLM 入口(场景缓存/限流/统计), 由 call_llm 内部使用
            self._llm_gateway = llm_gateway
        if assets is not None:
            #: 外部素材库(首次运行从 Git 源下载大素材并缓存) —— 游戏用 self.asset_path()
            self._assets = assets

    async def asset_path(self, rel: str) -> Optional[Any]:
        """取外部素材的本地路径（首次调用会下载）。

        素材（如塔罗牌面 251MB）不进插件包：包内只有 manifest，
        首次运行按需下载到缓存目录并校验 sha256。
        返回 None 表示暂时拿不到（离线/未启用）——调用方应回退占位图，不要报错。
        """
        if not self._assets:
            return None
        try:
            return await self._assets.ensure(rel)
        except Exception:  # noqa: BLE001 - 取素材失败绝不能影响对局
            return None

    # ── 发图桥接(插件主体通用能力) ─────────

    async def send_photo(self, user_id: str, category: Optional[str] = None,
                         caption: str = "", auto: bool = False) -> Dict[str, Any]:
        """通过插件主体 PhotoBridge 自主发一张图到聊天框。

        任何游戏适配后都可直接调用: 图库扫描/分类/推送/上传由桥接统一处理。
        返回 {ok, style, category, rarity, summary, caption, photo}。
        """
        if not self._photo:
            return {"ok": False, "summary": "发图桥接未就绪喵", "error": "no_bridge"}
        return await self._photo.send_photo(user_id, category=category,
                                            caption=caption, auto=auto)

    async def pick_photo_for_delivery(self, category: Optional[str] = None,
                                      caption: str = "") -> Dict[str, Any]:
        """通过桥接取一张图(不推送), 供 handle_action 返回 images 交 brain 推送。

        「游戏适配插件」输出契约: 游戏不直接 push, 只取图数据返回 images,
        brain 统一编排推送。返回 {ok, image: {text, bytes, mime, url}, ...}。
        """
        if not self._photo:
            return {"ok": False, "summary": "发图桥接未就绪喵", "error": "no_bridge"}
        return await self._photo.pick_for_delivery(category=category, caption=caption)

    async def send_auto_photo(self, user_id: str) -> Dict[str, Any]:
        """后台自动发图(桥接): 只发本地图库, 配文随机。"""
        if not self._photo:
            return {"ok": False, "summary": "发图桥接未就绪喵", "error": "no_bridge"}
        return await self._photo.send_auto(user_id)

    def photo_categories(self) -> List[str]:
        """图库分类列表(桥接)。"""
        return self._photo.get_categories() if self._photo else []

    async def upload_photo(self, user_id: str, name: str,
                           data_b64: str = "", data_bytes: bytes = b"",
                           category: str = "默认") -> Dict[str, Any]:
        """用户上传图片到图库(桥接)。"""
        if not self._photo:
            return {"ok": False, "message": "发图桥接未就绪喵"}
        return await self._photo.upload_photo(user_id, name=name,
                                              data_b64=data_b64,
                                              data_bytes=data_bytes,
                                              category=category)

    # ── 输出契约(游戏适配插件) ─────────────
    #
    # 游戏 handle_action 只返回结构化结果, 不参与任何推送:
    #   return {"facts": [...], "outcome": "win", "message": "结算文本",
    #           "images": [{"text": "配文", "bytes": img_bytes, "mime": "image/png"}]}
    # brain 统一负责: 推 message、推 images、渲染高光卡片、生成 summary。
    #
    # 以下 push_* 方法已废弃(仅兼容历史游戏/on_tick 后台提醒使用):
    # 新游戏 handle_action 内不得调用, 应返回 images 数据交给 brain 推送。

    @staticmethod
    def build_image(text: str = "", image_bytes: bytes = b"",
                    mime: str = "image/png", url: str = "") -> Dict[str, Any]:
        """构造 handle_action 返回的 images 元素(由 brain 统一推送)。"""
        img: Dict[str, Any] = {"text": text}
        if url:
            img["url"] = url
        else:
            img["bytes"] = image_bytes
            img["mime"] = mime
        return img

    async def push_text(self, text: str) -> None:
        """推送文本到聊天框(已废弃: 仅 on_tick 后台提醒/历史兼容使用)。"""
        if self._push:
            await self._push.text(text)

    async def push_text_image(self, text: str, image_bytes: bytes,
                              mime: str = "image/png") -> None:
        """推送文本+图片到聊天框(已废弃: 仅历史兼容, 新游戏用 images 返回)。"""
        if self._push:
            await self._push.text_with_image(text, image_bytes, mime)

    async def push_text_image_url(self, text: str, url: str) -> None:
        """推送文本 + 图片 URL(已废弃: 仅历史兼容, 新游戏用 images 返回)。"""
        if self._push:
            await self._push.text_with_image_url(text, url)

    async def push_help(self, title: str, image_bytes: bytes,
                        text: str = "") -> None:
        """推送帮助文档(已废弃: 帮助由 brain.show_help 统一处理)。"""
        if self._push:
            await self._push.help_doc(title, image_bytes, text)

    async def render_card(self, game_name: str, title: str,
                          lines: List[Tuple[str, str]],
                          subtitle: str = "", mood: str = "calm") -> Optional[bytes]:
        """渲染结果卡片图片。"""
        if self._img:
            return await self._img.render_card(game_name, title, lines, subtitle, mood)
        return None

    async def render_help_img(self, game_name: str,
                              commands: List[Tuple[str, str]],
                              footer: str = "") -> Optional[List[bytes]]:
        """渲染帮助文档图片(多页时返回每页 PNG bytes 列表)。"""
        if self._img:
            return await self._img.render_help(game_name, commands, footer)
        return None

    # ── 渲染桥接(插件主体通用能力: 游戏只给数据) ─────
    #
    # 「游戏适配插件」的核心约定: 图片渲染(帮助图/面板图/结果卡)全部由插件主体
    # 负责——官方 UI Kit 视觉、主题、版式块、宿主浏览器都在插件侧。
    # 游戏**不要**写 HTML/CSS, 也不要自己调 PIL/浏览器, 只调用下面的接口传数据。

    async def render_help(self, topic: str = "") -> List[bytes]:
        """渲染本游戏的帮助图(支持功能主题, 如「纳戒」「战斗」)。

        数据来自 data/config/<id>/help.json; 渲染由插件统一完成。
        返回每页 PNG bytes 列表; 渲染不可用返回空列表(游戏可降级为文本)。
        """
        if not self._render:
            return []
        return await self._render.help(self.id, game_name=self.name,
                                       raw_help=self._help_raw(), topic=topic)

    async def render_page(self, title: str, subtitle: str = "", blocks: Any = None,
                          rows: Any = None, commands: Any = None, chips: Any = None,
                          tip: str = "", theme: str = "") -> Optional[bytes]:
        """渲染一个自定义页面(官方组件 + 版式块), 游戏只给数据。

        blocks: [{"type": "slots|bag|table|steps|flow|chips|text", ...}, ...]
        rows:   [["左", "右"], ...]  简单两列表格
        commands: [["指令", "说明"], ...] 或对象形式
        返回 PNG bytes; 不可用返回 None。
        """
        if not self._render:
            return None
        pages = await self._render.page(title, subtitle=subtitle, blocks=blocks,
                                        rows=rows, commands=commands, chips=chips,
                                        tip=tip, theme=theme, game_id=self.id)
        return pages[0] if pages else None

    async def send_page(self, title: str, subtitle: str = "", blocks: Any = None,
                        rows: Any = None, commands: Any = None, chips: Any = None,
                        tip: str = "", text: str = "") -> Dict[str, Any]:
        """渲染并推送一条图片消息(渲染+推送都由插件主体完成)。"""
        if not self._render:
            return {"ok": False, "summary": "渲染桥接未就绪喵"}
        return await self._render.send(title, subtitle=subtitle, blocks=blocks,
                                       rows=rows, commands=commands, chips=chips,
                                       tip=tip, text=text, game_id=self.id)

    def _help_raw(self) -> Dict[str, Any]:
        """本游戏的原始 help.json 数据(注册表注入; 没有则空)。"""
        return getattr(self, "_help_data", None) or {}

    # ── 插件内部/历史渲染接口(游戏不应再使用) ─────────────

    async def render_html(self, html: str, css: str = "", width: int = 720,
                          height: int = 600, game_name: str = "",
                          selector: str = "body", brand_footer: bool = False) -> Optional[bytes]:
        """【已废弃·插件内部】渲染 HTML 为 PNG。

        保留仅为兼容历史游戏。按「游戏适配插件」约定, 游戏不参与渲染:
        请改用 render_page(数据) 或 render_help —— 主题、版式、浏览器都在插件侧。
        """
        log.warning("游戏 %s 调用了已废弃的 render_html, 请改用 render_page", self.id)
        if not self._img:
            return None
        renderer = getattr(self._img, "render_html", None)
        if not renderer:
            return None
        return await renderer(html, css=css, width=width, height=height,
                              game_name=game_name or self.name,
                              selector=selector, brand_footer=brand_footer)

    async def render_avatar(self, mood: str = "calm", size: int = 128) -> Optional[bytes]:
        """渲染猫娘表情头像。"""
        if self._img:
            return self._img.render_neko_avatar(mood, size)
        return None

    def tts_note(self, text: str) -> None:
        """记录 TTS 语音行。"""
        if self._tts:
            self._tts.note_tts_line(text)

    async def call_llm(self, prompt: str, scene: str = "") -> Optional[str]:
        """统一的 LLM 入口(游戏只调这里, 不碰 provider)。

        内部走 `core/llm_gateway.py`: 场景标签 + 按 key 缓存 + 限流 + token 统计。
        传 scene 便于统计里分清用途(如 "soup.puzzle"/"xiuxian.story"),
        不传就用 "<游戏id>.llm" 兜底。
        """
        gateway = getattr(self, "_llm_gateway", None)
        if gateway is not None:
            return await gateway.scene(scene or f"{self.id or 'game'}.llm", prompt) or None
        if self._llm:
            return await self._llm.call(prompt)
        return None

    # ── 生命周期（可选覆写） ─────────────────

    async def on_register(self) -> None:
        """注册时初始化（存档迁移等）。"""

    async def on_unload(self) -> None:
        """卸载时清理资源。"""

    async def on_start(self, user_id: str) -> None:
        """会话开始。"""

    async def on_stop(self, user_id: str) -> None:
        """会话结束。"""

    async def on_tick(self, user_id: str) -> None:
        """由大脑每秒 tick 调用（可选覆写，用于超时/不活跃检测等）。"""

    # ── 核心接口（必须实现） ─────────────────

    @abc.abstractmethod
    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """处理玩家行为，返回结构化结果。

        facts: [{"kind": "catch", "name": "鲲", "rarity": "legendary"}, ...]
        outcome: "caught_legendary" / "caught" / "trash" / "win" / "lose" / ...
        message: 游戏自己的结算文本（可选）
        """

    # ── 插件适配接口（可选覆写，默认从 data/config 加载） ─────

    def get_keywords(self) -> List[str]:
        """返回触发关键词列表，用于插件匹配用户输入。
        默认使用游戏名和 id，可在 data/config/{id}/keywords.json 中配置。"""
        if hasattr(self, '_keywords') and self._keywords:
            return self._keywords
        return [self.name, self.id]

    def get_emotion_templates(self) -> Dict[str, List[str]]:
        """返回情感模板字典，键为 outcome/fact kind，值为模板句子列表。
        默认从 data/config/{id}/emotion.json 加载。"""
        if hasattr(self, '_emotion_templates') and self._emotion_templates:
            return self._emotion_templates
        return {}

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        """返回事件分类：'highlight' / 'lowlight' / 'routine'。
        默认从 outcome 和 facts 中推断。"""
        oc = (outcome or "").lower()
        if "legendary" in oc or "highlight" in oc or "big_win" in oc:
            return "highlight"
        if "lose" in oc or "lowlight" in oc or "air" in oc:
            return "lowlight"
        for f in facts:
            r = str(f.get("rarity", "")).lower()
            if r in ("legendary", "？"):
                return "highlight"
            if r in ("epic", "rare"):
                return "highlight"
        return "routine"

    async def on_milestone(self, outcome: str, facts: List[Dict[str, Any]],
                           memory: Any) -> None:
        """处理里程碑事件。默认处理稀有度相关里程碑。"""
        for f in facts:
            if str(f.get("rarity", "")).lower() in ("legendary", "？"):
                await memory.bump_stat(self.id, "legendary_caught")

    def format_fact_for_card(self, fact: Dict[str, Any]) -> Tuple[str, str]:
        """格式化事实用于卡片显示，返回 (显示文本, 稀有度标签)。"""
        name = fact.get("name", "") or fact.get("item", "") or str(fact.get("kind", ""))
        rarity = fact.get("rarity", "")
        if fact.get("size"):
            detail = f"{name}（{rarity}）{fact['size']}cm {fact.get('weight', '')}kg"
        else:
            detail = name
        return detail, rarity

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        """高亮事件是否要生成卡片。默认有 facts 时生成。"""
        return bool(facts)

    # ── 状态（可选覆写） ─────────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        """面板轮询的游戏状态。"""

    def get_meta(self) -> Dict[str, Any]:
        """游戏卡片信息。"""
        return {"id": self.id, "name": self.name,
                "description": self.description, "version": self.version,
                "icon": self.icon, "enabled": self.enabled}

    def support_panel(self) -> Optional[Dict[str, Any]]:
        """面板配置 schema（可选覆写）。返回 None 表示该游戏无配置面板。

        参考锅巴(Guoba)的 schema 风格，声明式描述 config.json 里的字段：
            {
              "schemas": [
                {"label": "超时设置", "component": "Group"},
                {"field": "game_timeout", "label": "超时秒数", "component": "InputNumber",
                 "props": {"min": 60, "max": 3600}, "help": "多久不猜就自动揭晓"},
                {"field": "puzzle_source", "label": "题目来源", "component": "Select",
                 "props": {"options": ["mix", "llm", "local"]}},
                {"field": "enable_x", "label": "开关项", "component": "Switch"},
              ]
            }

        组件类型：Switch / Input / InputNumber / Select / InputTextArea / Group。
        field 对应 config.json 的键（点路径 a.b 表示嵌套）；props 透传给前端控件。
        """
        return None

    # ── 存档工具 ─────────────────────────────

    async def get_user_data(self, user_id: str, default: Any = None) -> Any:
        """读取玩家存档。"""
        return await self.plugin.store_get_user(self.id, user_id, default)

    async def save_user_data(self, user_id: str, data: Any) -> None:
        """写入玩家存档。"""
        await self.plugin.store_save_user(self.id, user_id, data)


def build_fact(kind: str, **fields: Any) -> Dict[str, Any]:
    """构造一条结构化事实。"""
    fact: Dict[str, Any] = {"kind": kind}
    fact.update(fields)
    return fact
