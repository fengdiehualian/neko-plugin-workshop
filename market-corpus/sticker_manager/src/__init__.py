"""表情包管理 (sticker_manager) —— 让她收藏、送出自己的表情包。

架构（一段话）：`core/` 是零 SDK 依赖的纯函数层（条目形状 / 格式嗅探 / 检索 /
目录文案 / 配置视图），`services/` 是有状态层（`data/` 下的 JSON 目录 + 图片文件、
发送链路与冷却台账），本模块只做装配与对外契约面（entry / ui.context / ui.action /
llm_tool）。

v0.1.0「能收能发」：主人在面板里收藏表情包（上传、写描述、打标签、禁用、删除），
她通过 `sticker_list` / `sticker_send` 两个工具在对话里自主挑一张发出去；
发送走 `push_message` 的 image part（≤ 内联预算直发原字节，大图换 URL part），
每次成败都记进使用台账，面板能看到"她最近爱用什么"。

v0.20.14「不再静默缺席」：工具注册恢复（services/tool_watch）——main_server 重启或
晚于插件启动时，她的 `sticker_list` / `sticker_send` 可能静默缺席；巡检器每 10 秒
逐角色核对，明确缺席时补挂，并在后续查询确认恢复。

v0.2.0「她得记得自己有表情」：三件事——① 存在感注入（services/awareness，挂在
60s watch 拍上的低频静默提示，治"她想不起来有表情"）；② 套图分组（Sticker.group，
检索/目录/面板全贯通）；③ 库的导出与套图包导入（manifest + zip，收件箱认包）。

三条硬约束（源码核实，见 DESIGN.md「已知陷阱」）：
1. `ctx.images.upload()` 会把图**归一成 JPEG**——gif 动图会被压平，所以动图只走
   内联通道，内联预算装不下就如实拒绝，不上报假成功。
2. 角色归属只取本次调用注入的 `_ctx["lanlan_name"]`，绝不读 `ctx._current_lanlan`
   （脏值会把 A 角色的发送记到 B 角色头上）。
3. `push_message` 的 `submitted=True` 只代表已交给传输；发送冷却只在内存里，
   重启即清零（刻意，见 services/sender 模块 docstring）。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import math
import time
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (  # pyright: ignore[reportMissingImports] — 独立仓无宿主包树；挂载态可解析，测试由 conftest 桩接管
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
    ui,
)

from .core import (
    CAPTION_MAX_CHARS,
    EAGERNESS_DEFAULT,
    EAGERNESS_LEVELS,
    GROUP_DESC_MAX_CHARS,
    INTERNAL_SELECTION_NOTE,
    MAX_STICKER_BYTES,
    NEXT_STEP_NOTE,
    OFFICIAL_PACK_RELPATH,
    PREVIEW_CHUNK_BYTES,
    SELECTION_HINT,
    SEND_CANDIDATES_MAX,
    SEND_TEXT_MAX_CHARS,
    UPLOAD_CHUNK_BYTES,
    VISIBLE_TEXT_MAX_CHARS,
    Sticker,
    StickerManagerSettings,
    build_send_tool_description,
    catalog_for_tool,
    format_catalog_for_model,
    format_group_overview,
    normalize_group,
    normalize_tags,
    normalize_zone_name,
    parse_tags_field,
    search_intent_candidates,
    search_stickers,
    validate_desc,
    validate_desc_optional,
    validate_optional_text,
)
from .core.quick_candidates import quick_candidates_for_tool
from .core.tool_surface import build_list_tool_description
from .services import Awareness, LanlanResolver, Library, RunStats, Sender, ToolWatch, gave_shape
from .services.bundled_pack import pack_exists
from .services.eager_attachment import EagerAttachment
from .services.reply_diagnostics import reply_context_fields
from .services.turn_end import TurnEndLogs, log_directories

__all__ = ["StickerManagerPlugin"]

# 上传解码前的字节上限提示（真正的 8 MiB 判定在 add 入口）。
_MAX_BASE64_CHARS = 12 * 1024 * 1024
# 直传单块的 base64 尺寸上限：3 MiB 原始恰好多不出 4 MiB 字符；给 JSON 封套留余量，
# 超过就是故意的帧溢出尝试，在解码前拦下（同预览方向的 4,784,128 字节尺）。
_UPLOAD_B64_MAX_CHARS = 4 * 1024 * 1024

# sticker_send 的工具描述（v0.17.0 起是**常驻目录面**）：能力句 + 读表指引 + 激活区
# 分类全表 + 档位许可句，拼装尺在 `core/tool_surface.py`。
# 为什么目录放这儿：工具列表每轮都随请求喂给模型，且重挂即推活会话；而存在感注入是
# 排干即弃的一次性 cue（语音模式还要等自然热切换）。v0.14/v0.15/v0.16 三轮都在调
# "多久提醒她一次"，实机账本（44 轮 / 12 提醒 / 3 发图）说明错的不是频率，是**载体**。
# 装饰器用的是空目录形状：万一重挂不可用，她看到的仍是与当前行为一致的那句。
_SEND_TOOL_NAME = "sticker_send"
_SEND_TOOL_DESCRIPTION_DEFAULT = build_send_tool_description("", EAGERNESS_DEFAULT)


# 工具层拒发的二段指引（面向模型，同 multi_candidates 的 hint 一样走硬编码中文：
# 消费方是模型不是面板，不走 i18n；reason 码本身才是给面板/日志的稳态契约）。
_SEND_HINTS: dict[str, str] = {
    "recent_repeat": "这张最近发过了——换一张或干脆用文字回；只有主人点名要再看这张时才 force=true 重发。",
    "probability_declined": "这一轮不配图，别重试、也别换一张再试；正文已说完就结束。",
    "send_cooldown": "刚发过一张，她在冷却；正文已说完就结束。",
}

# 运行账本的日志节流（v0.20.0）：读数原来只在面板上，判"今晚这次改动有没有用"得开着
# WebUI 盯——而这条链路的证据必须在日志里离线可读。节流是照陷阱 31 加的（那天 363 行
# 日志里 313 行是同一个入口的重复留痕）：指纹没变不吼，变了也最快两分钟一行。
_LEDGER_LOG_MIN_SEC = 120.0


def _private_selection_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the reply boundary present on success, empty results and refusals."""
    private = {**result, "hint": str(result.get("hint") or "") + INTERNAL_SELECTION_NOTE}
    if not result.get("candidate_ids") and (
        result.get("count") == 0 or result.get("reason") in {"no_match", "group_not_found", "id_or_query_required"}
    ):
        private["note"] = "本结果仅供内部判断；若已说完就结束，仅续接未说完的话。"
        private["hint"] = INTERNAL_SELECTION_NOTE
    return private


def _clean_id_list(value: Any, *, cap: int = 200) -> list[str]:
    """批量入口的 ids 参数尺：只收非空字符串、去重保序、限量。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        sid = item.strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
        if len(out) >= cap:
            break
    return out


def _match_group_names(names: list[str], term: str) -> list[str]:
    """组名解析：精确 > 子串（casefold）；多命中全回，让工具面决定“拍板还是回列表”。"""
    if term in names:
        return [term]
    folded = term.casefold()
    return [n for n in names if folded in n.casefold()]


@neko_plugin
class StickerManagerPlugin(NekoPluginBase):
    """表情包收藏与发送：面板管理库，她在对话里自主发图。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._settings = StickerManagerSettings.defaults()
        # v0.17.0：库一落盘就把"她的工具描述该重挂了"打个标——收口在 `Library.save()`，
        # 所以新增入口只要走 save() 就自动带上，不必在每个调用点各记一遍。
        # 这里只打标不发 IPC：批量导入会连着 save() 上百次。真正重挂在 10s 的 turns 拍。
        self._surface_dirty = True
        # 缩略图降级整轮只吼一次（v0.17.2）：一墙 190 张各自降级就是 190 行日志，
        # 而"刷屏"本身就是本轮要修的两件事之一。
        self._thumb_fallback_logged = False
        self._library = Library(
            self.data_path("library"),
            logger=self.logger,
            on_saved=self._mark_surface_dirty,
        )
        self._sender = Sender(
            self, self._library, logger=self.logger,
            turn_end=TurnEndLogs(self, log_directories(self._library.root)),
        )
        # 工具注册心跳（v0.1.4）：@llm_tool 只在启动时发一次 IPC，main_server 没就绪
        # 或重启后她的两个工具会静默缺席（见 services/tool_watch.py 模块 docstring）。
        # 与总开关无关：注册韧性是宿主层面的在场性，不随业务冻结而应冻结。
        self._tool_watch = ToolWatch(self, logger=self.logger)
        # 目标解析（v0.14.1）：她在跟谁说话。面板动作派发的 `_ctx` 里只有 run_id，
        # 普通话轮又不写 conversations 存储——只读 ctx/总线会让面板按钮全报 no_target。
        self._lanlan = LanlanResolver(self, logger=self.logger)
        # 提醒前核对目标角色的注册表；未知状态只记账，不改变提醒资格。
        self._awareness = Awareness(
            self, self._library, logger=self.logger, resolver=self._lanlan, tool_watch=self._tool_watch
        )
        # 本次运行读数（v0.17.1 观测轮）：只记账不参与任何决策，进程重启归零。
        self._runstats = RunStats()
        self._eager_attachment = EagerAttachment(self)
        # 运行账本的日志面（v0.20.0）：上一次报出去的指纹 + 时刻。见 `_maybe_log_run_ledger`。
        self._ledger_fingerprint: tuple[Any, ...] = ()
        self._ledger_logged_at = 0.0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._reload_settings()
        loaded = self._library.load(force=True)
        seed_note = "absent"
        try:
            # J-2 官方区内置（P2A）：首启把随包官方收藏收进「官方」区——尺在 Library.seed_official
            # （只播一次/同名收编/空库才默认激活）。播种炸了不许拦 startup：它是锦上添花，
            # 库的管理面不因此受伤；台账未盖，下次启动自动重试（幂等吃指纹查重）。
            pack = self._official_pack_path()
            if pack_exists(pack):
                seed = self._library.seed_official(pack)
                seed_note = str(seed.get("status", "?") if seed.get("status") != "io" else f"io:{seed.get('error')}")
        except Exception:  # noqa: BLE001 - 播种异常只进日志，不拖死启动
            self.logger.warning("sticker_manager official seed leaked", exc_info=True)
            seed_note = "leaked"
        # v0.17.0：她的分类目录 + 配图意愿都装进常驻面（工具描述），不再只活在一次性注入里。
        self._refresh_send_tool_surface()
        self.logger.info(
            "sticker_manager ready: enabled={} stickers={} io_ok={} official_seed={}",
            self._settings.enabled,
            self._library.count(),
            loaded.ok,
            seed_note,
        )
        return Ok(
            {
                "status": "ready",
                "enabled": self._settings.enabled,
                "stickers": self._library.count(),
                "official_seed": seed_note,
            }
        )

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._reload_settings()
        self._refresh_send_tool_surface()
        return Ok({"status": "config_updated", "enabled": self._settings.enabled})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._eager_attachment.invalidate(closing=True)
        self._note_deferred_outcomes(self._sender.cancel_pending(settings=self._settings, closing=True))
        self._note_deferred_outcomes(await self._sender.cancel_host_pending(settings=self._settings))
        # Observe retained turns without injecting a new cue during shutdown.
        try:
            await self._awareness.observe_pending(settings=self._settings)
        except Exception:
            self.logger.warning("final turn poll failed", exc_info=True)
        try:
            self._maybe_log_run_ledger(now=time.time(), final=True)
        except Exception:
            self.logger.warning("final run ledger failed", exc_info=True)
        # 本插件的写路径全部是同步落盘（写穿式 JSON + 原子替换），没有待 flush 的
        # 内存态；shutdown 只留一个收尾日志与"目录还能读"的健康探针。
        probe = self._library.load(force=True)
        self.logger.info(
            "sticker_manager stopped: stickers={} catalog_ok={}",
            self._library.count(),
            probe.ok,
        )
        return Ok({"status": "stopped", "stickers": self._library.count()})

    # ------------------------------------------------------------------
    # 工具注册心跳（v0.1.4）
    # ------------------------------------------------------------------

    @timer_interval(id="eager_attachment", seconds=1, name="sticker_manager eager attachment")
    async def on_eager_attachment(self, **_):
        return Ok(await self._eager_attachment.poll())

    @timer_interval(id="sticker_tail", seconds=1, name="sticker_manager reply tail")
    async def on_sticker_tail(self, **_):
        try:
            outcomes = await self._sender.drain(settings=self._settings)
            self._note_deferred_outcomes(outcomes)
            return Ok({"completed": len(outcomes)})
        except Exception:
            self.logger.warning("sticker_manager reply tail failed", exc_info=True)
            return Ok({"status": "failed"})

    def _note_deferred_outcomes(self, outcomes):
        for _source, result in outcomes:
            if result.detail == "host_reply_tail_duplicate" or getattr(result, "delivery_recorded", False):
                continue
            self._runstats.note_delivery(ok=result.ok, reason=result.code)
            result.delivery_recorded = True

    @timer_interval(id="watch", seconds=10, name="sticker_manager watch")
    async def on_watch(self, **_):
        # 独立巡检每 10s 一拍，提醒关闭时也能恢复重建后丢失的工具。
        # timer 每拍跑在新 event loop 且无 watchdog：异常必须自己兜住（陷阱 §3）。
        try:
            watch = await self._tool_watch.maybe_run(now=time.time())
        except Exception:  # noqa: BLE001 - timer 无 watchdog，异常漏出去会停不了但也静默
            self.logger.warning("sticker_manager tool watch leaked", exc_info=True)
            watch = {"status": "leaked"}
        try:
            # 运行账本一行（v0.20.0）：读数原来只在面板上，判"改了有没有用"得开 WebUI。
            self._maybe_log_run_ledger(now=time.time())
        except Exception:  # noqa: BLE001 - 观测面坏掉不许影响心跳本体
            self.logger.warning("sticker_manager run ledger leaked", exc_info=True)
        return Ok({"tool_watch": watch})

    @timer_interval(id="turns", seconds=10, name="sticker_manager turns")
    async def on_turns(self, **_):
        """存在感注入的拍（v0.16.0）：10s 问一次总线"她开新一轮了吗"。

        为什么单独一拍而不蹭 60s watch：注入的触发源从挂钟换成了话轮，60s 的粒度
        会把连发的几条用户轮并成一轮看见（同门 forever_companion 同样用 10s）。
        为什么必须自己兜异常：这一拍是整条存在感链路唯一的驱动，炸出去就是静默失声——
        而那正是本轮要修的症状。

        v0.17.0 顺手当常驻目录面的扫尾：库一变（`Library.save()` 的收口回调）就打标，
        这一拍最多 10 秒后把她的工具描述重挂一次。为什么不在回调里直接发 IPC：导入
        官方包会连着 save() 190 次，那就是 190 个 `session.update` 打在一条线上。
        两件事各兜各的异常——蹭在一起就是一个坏了把另一个的表也标黄（陷阱 16 同族）。
        """
        try:
            awareness = await self._awareness.maybe_run(settings=self._settings, now=time.time())
        except Exception:  # noqa: BLE001 - 注入坏掉不许带走 timer 线程
            self.logger.warning("sticker_manager awareness leaked", exc_info=True)
            awareness = {"status": "leaked"}
        try:
            surface = self._refresh_send_tool_surface(force=False)
        except Exception:  # noqa: BLE001 - 重挂坏掉不许带走这一拍（下拍还会再试）
            self.logger.warning("sticker_manager tool surface leaked", exc_info=True)
            surface = False
        return Ok({"awareness": awareness, "tool_surface": surface})

    # ------------------------------------------------------------------
    # 管理入口（面板 + 命令面板共用）
    # ------------------------------------------------------------------

    @ui.action(
        id="add",
        label=tr("actions.add.label", default="Add"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="add",
        metadata={"agent_auto": False},
        name=tr("entries.add.name", default="收藏一张表情包"),
        description=tr(
            "entries.add.description",
            default="把一张图片存进表情库：data_base64 是图片本体（不含 data: 前缀）。轮 F 起逐图文本全部可选——"
            "不写也能收，她靠分组说明选图；caption（梗义）仍是选图最准的一把尺，顺手就写",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "data_base64": {
                    "type": "string",
                    "description": tr("fields.data_base64", default="图片 base64（png/jpg/gif/webp，≤8MiB）"),
                },
                "desc": {
                    "type": "string",
                    "description": tr("fields.desc", default="一句话描述图里在干什么（可选，≤200字）"),
                },
                "tags": {
                    "type": "string",
                    "description": tr("fields.tags", default="标签，逗号分隔（可选）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，如：猫猫日常）"),
                },
                "zone": {
                    "type": "string",
                    "description": tr("fields.zone_target", default="目标区 id（留空=落在分类所在的区/激活区）"),
                },
                "caption": {
                    "type": "string",
                    "description": tr(
                        "fields.caption",
                        default="梗义：这张图在回复什么、什么上一句会触发发它（可选，≤300字）",
                    ),
                },
                "visible_text": {
                    "type": "string",
                    "description": tr("fields.visible_text", default="图里清晰可见的原文字（可选，≤200字）"),
                },
            },
            "required": ["data_base64"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id", "desc"],
        timeout=30.0,
    )
    async def add_entry(
        self,
        data_base64: str = "",
        desc: str = "",
        tags: str = "",
        group: str = "",
        zone: str = "",
        caption: str = "",
        visible_text: str = "",
        **_,
    ):
        # 轮 F：desc 不再是入库门槛（学外部系统：逐图零文本也能用）；只拦超限。
        text_desc, desc_error = validate_desc_optional(desc)
        if desc_error:
            return Err(SdkError("desc_too_long"))
        text_caption, caption_error = validate_optional_text(caption, limit=CAPTION_MAX_CHARS)
        if caption_error:
            return Err(SdkError("caption_too_long"))
        text_visible, visible_error = validate_optional_text(visible_text, limit=VISIBLE_TEXT_MAX_CHARS)
        if visible_error:
            return Err(SdkError("visible_text_too_long"))
        if not isinstance(data_base64, str) or not data_base64:
            return Err(SdkError("image_required"))
        if len(data_base64) > _MAX_BASE64_CHARS:
            return Err(SdkError("image_too_large"))
        try:
            payload = base64.b64decode(data_base64, validate=False)
        except (binascii.Error, ValueError):
            return Err(SdkError("image_undecodable"))
        if len(payload) > MAX_STICKER_BYTES:
            return Err(SdkError("image_too_large"))
        sticker, error = self._library.add(
            data=payload,
            desc=text_desc,
            tags=parse_tags_field(tags),
            group=normalize_group(group),
            zone=zone if isinstance(zone, str) else "",
            caption=text_caption,
            visible_text=text_visible,
            now=time.time(),
        )
        if sticker is None:
            return Err(SdkError(error or "image_rejected"))
        return Ok({"note": "sticker_added", "id": sticker.id, "desc": sticker.desc})

    @ui.action(
        id="update",
        label=tr("actions.update.label", default="Edit"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="update",
        metadata={"agent_auto": False},
        name=tr("entries.update.name", default="修改表情包"),
        description=tr(
            "entries.update.description",
            default="改描述/梗义/图内文字/标签/禁用状态。梗义决定她会不会选中这张图（空串=清除标注），禁用=从她的可选面里摘掉",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
                "desc": {"type": "string", "description": tr("fields.desc", default="新描述（留空=不改）")},
                "tags": {"type": "string", "description": tr("fields.tags", default="新标签（留空=不改）")},
                "disabled": {
                    "type": "boolean",
                    "description": tr("fields.disabled", default="是否禁用（缺省=不改）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，如：猫猫日常）"),
                },
                "caption": {
                    "type": "string",
                    "description": tr(
                        "fields.caption",
                        default="梗义：这张图在回复什么、什么上一句会触发发它（可选，≤300字）",
                    ),
                },
                "visible_text": {
                    "type": "string",
                    "description": tr("fields.visible_text", default="图里清晰可见的原文字（可选，≤200字）"),
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id"],
    )
    async def update_entry(
        self,
        id: str = "",  # noqa: A002 - 入口参数名就是 id（面板与模型看到的形状）
        desc: str | None = None,
        tags: Any = None,
        disabled: bool | None = None,
        group: Any = None,
        caption: Any = None,
        visible_text: Any = None,
        **_,
    ):
        if not isinstance(id, str) or not id:
            return Err(SdkError("sticker_not_found"))
        new_desc: str | None = None
        if isinstance(desc, str):
            # 轮 F：空串是合法意图（清掉逐图描述，靠分组说明顶上）；只有 None（缺席）才是不改。
            if not desc.strip():
                new_desc = ""
            else:
                text_desc, desc_error = validate_desc(desc)
                if desc_error:
                    return Err(SdkError(f"desc_{desc_error}"))
                new_desc = text_desc
        new_tags: list[str] | None = None
        if tags is not None and not (isinstance(tags, str) and not tags.strip()):
            new_tags = parse_tags_field(tags)
        if disabled is not None and not isinstance(disabled, bool):
            return Err(SdkError("invalid_value"))
        # group/caption/visible_text 与 tags 的语义不同：**空串是合法意图**（"移出分组"/"清掉标注"），
        # 只有 None（参数缺席）才表示"不改"。
        new_group: str | None = None
        if isinstance(group, str):
            new_group = normalize_group(group)
        new_caption: str | None = None
        if isinstance(caption, str):
            new_caption, caption_error = validate_optional_text(caption, limit=CAPTION_MAX_CHARS)
            if caption_error:
                return Err(SdkError("caption_too_long"))
        new_visible: str | None = None
        if isinstance(visible_text, str):
            new_visible, visible_error = validate_optional_text(visible_text, limit=VISIBLE_TEXT_MAX_CHARS)
            if visible_error:
                return Err(SdkError("visible_text_too_long"))
        sticker, error = self._library.update(
            id,
            desc=new_desc,
            tags=new_tags,
            disabled=disabled,
            group=new_group,
            caption=new_caption,
            visible_text=new_visible,
        )
        if sticker is None:
            return Err(SdkError(error or "sticker_not_found"))
        return Ok({"note": "sticker_updated", "id": sticker.id})

    @ui.action(
        id="remove",
        label=tr("actions.remove.label", default="Delete"),
        tone="danger",
        refresh_context=True,
        confirm=True,
    )
    @plugin_entry(
        id="remove",
        metadata={"agent_auto": False},
        name=tr("entries.remove.name", default="删除表情包"),
        description=tr("entries.remove.description", default="从库里删掉这张图和它的记录（不可恢复）"),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id"],
    )
    async def remove_entry(self, id: str = "", **_):  # noqa: A002
        if not isinstance(id, str) or not id:
            return Err(SdkError("sticker_not_found"))
        error = self._library.remove(id)
        if error:
            return Err(SdkError(error))
        return Ok({"note": "sticker_removed", "id": id})

    # ------------------------------------------------------------------
    # 轮 F：分组说明与批量整理（对齐外部系统“描述挂分类、整理靠批量”的管理面）
    # ------------------------------------------------------------------

    @ui.action(
        id="group_set_desc",
        label=tr("actions.group_set_desc.label", default="Group note"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="group_set_desc",
        metadata={"agent_auto": False},
        name=tr("entries.group_set_desc.name", default="给套图分类写一句说明"),
        description=tr(
            "entries.group_set_desc.description",
            default="分类说明是她选图时看到的“分类目录”正文（一句“什么时候用这一组”，≤300字）；空串=清掉这句话（分类本身不删，删分类用 group_remove）",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": tr("fields.group_name", default="分类名（须已在册：建过分类或有图在用）"),
                },
                "desc": {
                    "type": "string",
                    "description": tr("fields.group_desc", default="什么时候用这一组（≤300字）"),
                },
            },
            "required": ["group"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "group"],
    )
    async def group_set_desc_entry(self, group: str = "", desc: str = "", **_):
        name = normalize_group(group)
        if not name:
            return Err(SdkError("group_required"))
        text, error = validate_optional_text(desc, limit=GROUP_DESC_MAX_CHARS)
        if error:
            return Err(SdkError("group_desc_too_long"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        known = self._library.group_names()
        if name not in known:
            # 只能给“在册”的分类写说明。轮 I 之后分类可以先建后用（group_create），
            # 但依旧不认“什么都没见过”的名字：打错一个字不许静默造出一个新分区。
            return Err(SdkError("group_not_found"))
        ok, error = self._library.set_group_desc(name, text)
        if not ok:
            return Err(SdkError(error or "group_io_error"))
        return Ok({"note": "group_desc_set", "group": name, "cleared": not text})

    # ---------------------------------------------------------------
    # 轮 I：分类优先的管理面（先立分类 → 往分类里收图 → 整间拆掉）
    # ---------------------------------------------------------------

    @ui.action(
        id="group_create",
        label=tr("actions.group_create.label", default="New category"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="group_create",
        metadata={"agent_auto": False},
        name=tr("entries.group_create.name", default="新建一个表情分类"),
        description=tr(
            "entries.group_create.description",
            default="先立分类再收图：名字必填，说明可留空（她选图时看到的分类正文就是这句话）。空分类对她是不可见的：不进目录、不能被选去发",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": tr("fields.group_name_new", default="分类名字")},
                "desc": {
                    "type": "string",
                    "description": tr("fields.group_desc", default="什么时候用这一组（≤300字）"),
                },
                "zone": {
                    "type": "string",
                    "description": tr("fields.zone_target", default="目标区 id（留空=落在分类所在的区/激活区）"),
                },
            },
            "required": ["group"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "group"],
    )
    async def group_create_entry(self, group: str = "", desc: str = "", zone: str = "", **_):
        name = normalize_group(group)
        if not name:
            return Err(SdkError("group_required"))
        text, error = validate_optional_text(desc, limit=GROUP_DESC_MAX_CHARS)
        if error:
            return Err(SdkError("group_desc_too_long"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, create_error = self._library.create_group(name, text, zone if isinstance(zone, str) else "")
        if not ok:
            return Err(SdkError(create_error or "group_io_error"))
        return Ok({"note": "group_created", "group": name})

    @ui.action(
        id="group_remove",
        label=tr("actions.group_remove.label", default="Delete category"),
        tone="danger",
        refresh_context=True,
    )
    @plugin_entry(
        id="group_remove",
        metadata={"agent_auto": False},
        name=tr("entries.group_remove.name", default="删掉一个分类（连带删它里的图）"),
        description=tr(
            "entries.group_remove.description",
            default="拆掉整个分区：这个分类下的每一张图与它们的文件一起删，不可恢复（面板先把精确张数摊开再问）",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": tr("fields.group_name", default="分类名（须已在册：建过分类或有图在用）"),
                },
            },
            "required": ["group"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "group", "removed"],
        # 陷阱 19 那两把尺：面板长任务走 LONG_CALL=120s，服务端同尺寸，
        # 否则一个大分类（几百张）删到一半先被服务端抢断，面板报“操作失败”而盘上已删。
        timeout=120.0,
    )
    async def group_remove_entry(self, group: str = "", **_):
        name = normalize_group(group)
        if not name:
            return Err(SdkError("group_required"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, error, removed = self._library.remove_group(name)
        if not ok:
            return Err(SdkError(error or "group_io_error"))
        return Ok({"note": "group_removed", "group": name, "removed": removed})

    # ---------------------------------------------------------------
    # J-1（v0.11.0）：区——分类的上层。她只感知激活区，其余区整体隐形。
    # 区用内部 id 引用（改名白送）；面板 tab 就是区的脸面。
    # ---------------------------------------------------------------

    @ui.action(
        id="zone_create",
        label=tr("actions.zone_create.label", default="New zone"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="zone_create",
        metadata={"agent_auto": False},
        name=tr("entries.zone_create.name", default="新建一个上层区"),
        description=tr(
            "entries.zone_create.description",
            default="区是分类的上层（比如官方自带/自己的主题收藏）：名字必填，说明可留空。新区不自动激活",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "zone": {"type": "string", "description": tr("fields.zone_name_new", default="区名字")},
                "desc": {
                    "type": "string",
                    "description": tr("fields.zone_desc", default="这个区是干什么的（可选，≤300字）"),
                },
            },
            "required": ["zone"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "zone_id"],
    )
    async def zone_create_entry(self, zone: str = "", desc: str = "", **_):
        name = normalize_zone_name(zone)
        if not name:
            return Err(SdkError("zone_required"))
        text, error = validate_optional_text(desc, limit=GROUP_DESC_MAX_CHARS)
        if error:
            return Err(SdkError("group_desc_too_long"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        zone_id, create_error = self._library.create_zone(name, text)
        if create_error:
            return Err(SdkError(create_error))
        return Ok({"note": "zone_created", "zone_id": zone_id})

    @ui.action(
        id="zone_rename",
        label=tr("actions.zone_rename.label", default="Rename zone"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="zone_rename",
        metadata={"agent_auto": False},
        name=tr("entries.zone_rename.name", default="改区名"),
        description=tr(
            "entries.zone_rename.description",
            default="区用内部 id 引用，改名不扯动任何归属——想改就改（分类改名仍是另一回事）",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "zone_id": {"type": "string", "description": tr("fields.zone_id", default="区 id")},
                "zone": {"type": "string", "description": tr("fields.zone_name_new", default="新名字")},
            },
            "required": ["zone_id", "zone"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "zone_id"],
    )
    async def zone_rename_entry(self, zone_id: str = "", zone: str = "", **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, error = self._library.rename_zone(zone_id, zone)
        if not ok:
            return Err(SdkError(error or "zone_io_error"))
        return Ok({"note": "zone_renamed", "zone_id": zone_id})

    @ui.action(
        id="zone_set_desc",
        label=tr("actions.zone_set_desc.label", default="Zone note"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="zone_set_desc",
        metadata={"agent_auto": False},
        name=tr("entries.zone_set_desc.name", default="给区写一句说明"),
        description=tr(
            "entries.zone_set_desc.description",
            default="区的说明只给主人看（面板 tab 下的提示语），不进她的目录；空串=清空",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "zone_id": {"type": "string", "description": tr("fields.zone_id", default="区 id")},
                "desc": {
                    "type": "string",
                    "description": tr("fields.zone_desc", default="这个区是干什么的（可选，≤300字）"),
                },
            },
            "required": ["zone_id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "zone_id"],
    )
    async def zone_set_desc_entry(self, zone_id: str = "", desc: str = "", **_):
        text, error = validate_optional_text(desc, limit=GROUP_DESC_MAX_CHARS)
        if error:
            return Err(SdkError("group_desc_too_long"))
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, set_error = self._library.set_zone_desc(zone_id, text)
        if not ok:
            return Err(SdkError(set_error or "zone_io_error"))
        return Ok({"note": "zone_desc_set", "zone_id": zone_id})

    @ui.action(
        id="zone_activate",
        label=tr("actions.zone_activate.label", default="Use this zone"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="zone_activate",
        metadata={"agent_auto": False},
        name=tr("entries.zone_activate.name", default="激活一个区"),
        description=tr(
            "entries.zone_activate.description",
            default="切她的世界：只有激活区的分类与图对她可见可选；其余区整体隐形",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "zone_id": {"type": "string", "description": tr("fields.zone_id", default="区 id")},
            },
            "required": ["zone_id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "zone_id"],
    )
    async def zone_activate_entry(self, zone_id: str = "", **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, error = self._library.activate_zone(zone_id)
        if not ok:
            return Err(SdkError(error or "zone_io_error"))
        return Ok({"note": "zone_activated", "zone_id": zone_id})

    @ui.action(
        id="zone_remove",
        label=tr("actions.zone_remove.label", default="Delete zone"),
        tone="danger",
        refresh_context=True,
    )
    @plugin_entry(
        id="zone_remove",
        metadata={"agent_auto": False},
        name=tr("entries.zone_remove.name", default="拆掉一个区（连带拆它全部分类与图）"),
        description=tr(
            "entries.zone_remove.description",
            default="拆区是删分类的放大版：区内每一张图、每一个分类连同文件一起消失，不可恢复；最后一个区不许拆。面板先把精确张数摊开再等 3 秒确认",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "zone_id": {"type": "string", "description": tr("fields.zone_id", default="区 id")},
            },
            "required": ["zone_id"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "zone_id", "removed"],
        timeout=120.0,
    )
    async def zone_remove_entry(self, zone_id: str = "", **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        ok, error, removed = self._library.remove_zone(zone_id)
        if not ok:
            return Err(SdkError(error or "zone_io_error"))
        return Ok({"note": "zone_removed", "zone_id": zone_id, "removed": removed})

    def _official_pack_path(self) -> Path:
        """内置官方包的定位尺（代码根只读区，J-2 P2A）：打包、首启播种、恢复按钮三处同源。

        测试把同名实例属性换成假包路径即可（入口/快照/启动都走这把尺）。
        """
        return Path(str(self.plugin_dir)).joinpath(*OFFICIAL_PACK_RELPATH)

    @ui.action(
        id="zone_restore_official",
        label=tr("actions.zone_restore_official.label", default="Restore official"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="zone_restore_official",
        metadata={"agent_auto": False},
        name=tr("entries.zone_restore_official.name", default="恢复官方收藏区"),
        description=tr(
            "entries.zone_restore_official.description",
            default="官方区被拆掉后的补救入口：把随包的官方收藏重新收进「官方」区（内容指纹查重，不会重入重图）；不会抢走她当前正在用的区，除非库本来就是空的",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["note", "zone", "imported", "duplicates"],
        timeout=120.0,
    )
    async def zone_restore_official_entry(self, **_):
        pack = self._official_pack_path()
        if not pack_exists(pack):
            return Err(SdkError("official_pack_missing"))
        seed = self._library.seed_official(pack, force=True)
        if seed.get("status") in {"io", "invalid_assets"}:
            return Err(SdkError(str(seed.get("error") or "library_io_error")))
        return Ok({"note": "official_restored", **seed})

    @ui.action(
        id="batch_update",
        label=tr("actions.batch_update.label", default="Batch edit"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="batch_update",
        metadata={"agent_auto": False},
        name=tr("entries.batch_update.name", default="批量编辑一批表情包"),
        description=tr(
            "entries.batch_update.description",
            default="对一批 id 统一：加标签/删标签/移到分组/启停。只改传来的项，缺席不改；逐张走同一把 update 尺",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("fields.ids", default="表情 id 列表（≤200）"),
                },
                "tags_add": {"type": "string", "description": tr("fields.tags_add", default="要加的标签，逗号分隔")},
                "tags_remove": {
                    "type": "string",
                    "description": tr("fields.tags_remove", default="要删的标签，逗号分隔"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，整批共用；包里自带的优先）"),
                },
                "disabled": {"type": "boolean", "description": tr("fields.disabled", default="禁用/启用")},
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "updated"],
    )
    async def batch_update_entry(
        self,
        ids: Any = None,
        tags_add: Any = None,
        tags_remove: Any = None,
        group: Any = None,
        disabled: Any = None,
        **_,
    ):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        id_list = _clean_id_list(ids)
        if not id_list:
            return Err(SdkError("batch_empty"))
        add_tags = parse_tags_field(tags_add) if isinstance(tags_add, (str, list)) else None
        add_tags = add_tags or None
        remove_tags = (
            {t.casefold() for t in parse_tags_field(tags_remove)} if isinstance(tags_remove, (str, list)) else set()
        )
        new_group = normalize_group(group) if isinstance(group, str) else None
        if add_tags is None and not remove_tags and new_group is None and disabled is None:
            return Err(SdkError("batch_noop"))
        if disabled is not None and not isinstance(disabled, bool):
            return Err(SdkError("invalid_value"))
        updated = 0
        missing: list[str] = []
        for sid in id_list:
            sticker = self._library.get(sid)
            if sticker is None:
                missing.append(sid)
                continue
            merged_tags: list[str] | None = None
            if add_tags is not None or remove_tags:
                merged = [t for t in sticker.tags if t.casefold() not in remove_tags]
                for tag in add_tags or []:
                    if tag.casefold() not in {m.casefold() for m in merged}:
                        merged.append(tag)
                merged_tags = normalize_tags(merged)
            result, error = self._library.update(
                sid, tags=merged_tags, group=new_group, disabled=disabled if isinstance(disabled, bool) else None
            )
            if result is None:
                missing.append(sid if sid else (error or "?"))
            else:
                updated += 1
        return Ok({"note": "library_batch_updated", "updated": updated, "missing": missing})

    @ui.action(
        id="batch_remove",
        label=tr("actions.batch_remove.label", default="Batch delete"),
        tone="danger",
        refresh_context=True,
        confirm=True,
    )
    @plugin_entry(
        id="batch_remove",
        metadata={"agent_auto": False},
        name=tr("entries.batch_remove.name", default="批量删除表情包"),
        description=tr(
            "entries.batch_remove.description",
            default="按 id 列表逐张删除（图与记录，不可恢复）；面板侧负责先把精确张数摊在确认里",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("fields.ids", default="表情 id 列表（≤200）"),
                },
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "removed"],
    )
    async def batch_remove_entry(self, ids: Any = None, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        id_list = _clean_id_list(ids)
        if not id_list:
            return Err(SdkError("batch_empty"))
        removed = 0
        missing: list[str] = []
        for sid in id_list:
            error = self._library.remove(sid)
            if error:
                missing.append(sid)
            else:
                removed += 1
        return Ok({"note": "library_batch_removed", "removed": removed, "missing": missing})

    async def _resolve_lanlan(self, kwargs: dict[str, Any]) -> str:
        """她在跟谁说话：入口 `_ctx` 带了就用，没带走解析链（见 services/lanlan.py）。

        四个调用点（面板试发 / 面板注入 / 面板快照 / 她的工具）都过这里——只读 `_ctx`
        是 v0.14.0 实机「awareness_no_target」的根因：宿主给面板动作的 `_ctx` 只有 run_id
        （`ui_query_service.py:1757-1762`），普通话轮又不写 conversations 存储。
        """
        return await self._lanlan.resolve(_lanlan_from_kwargs(kwargs))

    def _note_tool_call(
        self,
        *,
        name: str,
        result: dict[str, Any],
        gave: str,
        group: str = "",
        query_len: int = 0,
        elapsed_ms: float | None = None,
    ) -> None:
        """她发起的每一次工具调用留一痕（v0.17.1 观测轮）：**只读结果，不改结果**。

        为什么值得单独一痕：主人那句"还是很少发"分不出三种病——她没想起来调 /
        调了但被闸拦下 / 拦都没拦、图没出去。以前只有成功那一支有日志（`sticker sent`），
        于是"0 次调用"和"0 次成功"在日志里长得一模一样。
        隐私纪律：她的检索词只落长度；分类名是主人自己写的目录键，落原名——
        "她挑了哪个分类、挑空了几次"正是本轮要看的东西。
        """
        delivery_recorded = result.pop("_delivery_recorded", False)
        reason = self._runstats.note_tool_call(
            name=name, gave=gave, result=result, group=group, query_len=query_len,
            **({"count_delivery": False} if delivery_recorded else {}),
        )
        if reason:
            outcome = f"refused reason={reason}"
        elif result.get("sent"):
            outcome = "sent id=" + str(result["sent"])
        elif result.get("queued"):
            outcome = "queued id=" + str(result["queued"])
        elif result.get("candidate_ids"):
            outcome = "candidates ids=" + ",".join(result["candidate_ids"])
        else:
            outcome = f"ok rows={result.get('count', '?')}" if name == "sticker_list" else "ok"
        self.logger.info(
            f"tool call: name={name} gave={gave}"
            + (f" group={group}" if group else "")
            + (f" q_len={query_len}" if query_len else "")
            + f" outcome={outcome}"
            + (f" elapsed_ms={elapsed_ms:.1f}" if elapsed_ms is not None else "")
        )

    def _maybe_log_run_ledger(self, *, now: float, final: bool = False) -> None:
        """把本次运行的四把数落到日志一行（v0.20.0：纯观测，零行为变更）。

        为什么非但面板要有、日志也要有一行：`RunStats` 那四把数（话轮数 / 她调用数 /
        发出成功数 / 被拦下数）此前只有开 WebUI 才读得到，而"她到底调没调"是判断任何
        一次措辞改动有没有用的第一手证据。

        更要紧的是它把**插件看不见的两个洞**变得可推断：宿主 realtime 侧有 4 次/15 秒的
        工具熔断（宿主 `omni_realtime_client/_tools.py:810-833`，直接回她"停止调用任何
        工具"且**不执行**），以及用户抢话时在飞 tool task 被整批取消（同文件 `:356-381`）。
        这两种情况一次都不会进到本插件的计数里——日志上就是"话轮在涨、调用为 0"。
        所以这一行必须把 turns 与 calls 并排打：只看 sent=0 分不清"她没想起来"、
        "被闸拦了"和"被宿主吞了"，三种病的处置完全相反。
        """
        stats = self._runstats.snapshot(turns=self._awareness.turns_seen)
        triggers = dict(self._awareness.trigger_counts)
        # v0.20.1 两组新读数，各自钉一种"读数正常但仪器坏了"的病：
        # · 信号面：`pointer=0/N` 分不清"这句没情绪"与"`turn.text` 读成空串"（总线形状变了），
        #   所以把有文本/空文本/命中三个次数并排打（只记次数，原话永不落盘）。
        # · 工具面：她**根本没有** `sticker_send` 的窗口由 `ToolWatch` 巡检发现，
        #   缺失只是查询时的注册表观测；gaps=0 不保证所有话轮都有工具。
        #   秒数带 `_max` 语义，真实缺席起点不可知，不许当精确时长读。
        aware = self._awareness
        gaps = getattr(self._tool_watch, "gaps", 0)
        gap_seconds_max = float(getattr(self._tool_watch, "gap_seconds_max", 0.0) or 0.0)
        unreachable = getattr(self._tool_watch, "unreachable", 0)
        tool_state = self._tool_watch.snapshot(now=now)
        missing_roles = tuple(sorted(tool_state.get("missing_by_role", {})))
        fingerprint = (
            stats["turns"],
            stats["tool_calls"],
            stats["sent"],
            stats["refused"],
            tuple(sorted(triggers.items())),
            aware.turn_texts,
            aware.turn_empty,
            aware.signal_hits,
            gaps,
            unreachable,
            tool_state.get("status"),
            missing_roles,
        )
        if not final and fingerprint == self._ledger_fingerprint:
            return
        if not final and now - self._ledger_logged_at < _LEDGER_LOG_MIN_SEC:
            # 节流期内不更新指纹：下一拍报的是最新值，而不是把中间值攒成一条过期的。
            return
        self._ledger_fingerprint = fingerprint
        self._ledger_logged_at = now
        self.logger.info(
            f"run ledger: turns={stats['turns']} calls={stats['tool_calls']}"
            f" sent={stats['sent']} refused={stats['refused']}"
            f" sticker_send_calls={stats['sticker_send_calls']} agent_send_calls={stats['agent_send_calls']}"
            f" last_reason={stats['last_reason'] or '-'}"
            f" last_call={stats['last_call'] or '-'}"
            f" pointer={triggers.get('signal', 0)}/{triggers.get('count', 0)}"
            f" sig_text={aware.turn_texts}/{aware.turn_empty} hits={aware.signal_hits}"
            f" tool_gaps={gaps}(≤{gap_seconds_max:.0f}s) unreachable={unreachable}"
            f" tool_registry={tool_state.get('status', 'unknown')}"
            f" registry_checked_at={tool_state.get('checked_at')}"
            f" registry_age_sec={tool_state.get('age_sec')}"
            f" missing_roles={len(missing_roles)}"
            f" reissues={tool_state.get('reissues', 0)} confirmations={tool_state.get('confirmations', 0)}"
            f" surface_cats={stats['surface_categories']} tier={stats['surface_tier'] or '-'}"
            + (" final=True" if final else "")
        )

    def _mark_surface_dirty(self) -> None:
        """库落盘了 → 她那份常驻目录该重挂。只打标，不在这里发 IPC。"""
        self._surface_dirty = True

    def _send_tool_catalog(self) -> str:
        """她该看见的分类全表：**激活区**里有未禁用图的那些分类（陷阱 23 那把尺）。"""
        return catalog_for_tool(self._library.active_pool(), self._library.group_descs())

    def _send_tool_candidates(self) -> str:
        # Shared metadata rotates past the latest successful images across roles.
        # Per-role lookup and send gates still use their own authoritative history.
        recent: set[str] = set()
        count = self._settings.send.recent_dedup_count
        if count > 0:
            for row in self._library.read_usage(limit=self._settings.storage.usage_history_keep):
                sid = str(row.get("id") or "")
                if row.get("ok") and sid:
                    recent.add(sid)
                    if len(recent) >= count:
                        break
        return quick_candidates_for_tool(
            self._library.active_pool(), self._library.group_descs(),
            limit=self._settings.storage.catalog_limit_for_model,
            recent_ids=recent,
            eager=self._settings.send.eagerness in ("natural", "eager"),
        )

    def _refresh_send_tool_surface(self, *, force: bool = True) -> bool:
        """需要时把 `sticker_send` 的描述按当前库重挂一遍。

        `force=False` 用在拍子上：只有脏了才动，且成功后清标。描述算出来与在册的一样
        就不碰 IPC（v0.15.0 的纪律：换描述是重活，不该每拍都白干一次）。
        """
        if not force and not self._surface_dirty:
            return True
        ok = self._apply_send_tool_surface()
        self._surface_dirty = not ok
        return ok

    def _apply_send_tool_surface(self) -> bool:
        """把「能力句 + 激活区分类全表 + 档位许可句」装进 `sticker_send` 的工具描述。

        这是 v0.17.0 那根换了载体的杠杆：目录原本只活在存在感注入里（一次性 cue），
        现在挪到每轮都在场的工具描述上。SDK 的 `register_llm_tool` 明写就是给
        "描述随状态变"用的；名字已在册时重注册会撞 EntryConflictError，所以先 unregister
        再 register。宿主侧 `register_tool_and_sync` 会把新快照直接推到活的 realtime
        会话上，所以换区/建类不用等她下次开新会话。

        三条底线（v0.15.0 定，本轮一字未改）：注册面缺席（桩/宿主改名）只记日志不动手；
        描述没变就不折腾 IPC；重注册失败**先把原描述装回去**——宁可她读到旧目录，
        也不能因为重挂失败没了这个工具。
        """
        unregister = getattr(self, "unregister_llm_tool", None)
        register = getattr(self, "register_llm_tool", None)
        lister = getattr(self, "list_llm_tools", None)
        if not (callable(unregister) and callable(register) and callable(lister)):
            self.logger.info("sticker_send surface skipped: SDK 注册面不可用")
            return False
        try:
            metas = {str(meta.get("name")): meta for meta in lister()}
        except Exception:  # noqa: BLE001 - 读不到自己的工具表就什么都不做
            self.logger.warning("sticker_send surface skipped: list_llm_tools 失败", exc_info=True)
            return False
        previous = metas.get(_SEND_TOOL_NAME)
        if not isinstance(previous, dict):
            self.logger.warning("sticker_send surface skipped: 工具不在册")
            return False
        catalog = self._send_tool_catalog()
        quick_candidates = self._send_tool_candidates()
        description = build_send_tool_description(
            catalog, self._settings.send.eagerness, quick_candidates=quick_candidates,
        )
        tail_supported = "reply_tail" in inspect.signature(register).parameters
        if not self._apply_list_tool_surface(
            metas.get("sticker_list"), register, unregister, tail_supported=tail_supported,
        ):
            return False
        if (
            str(previous.get("description") or "") == description
            and (not tail_supported or previous.get("reply_tail") is True)
        ):
            return True
        params = {
            "name": _SEND_TOOL_NAME,
            "description": description,
            "parameters": previous.get("parameters"),
            "handler": self.tool_sticker_send,
            "timeout": float(previous.get("timeout_seconds") or 20.0),
            "role": previous.get("role"),
            **({"reply_tail": True} if tail_supported else {}),
        }
        try:
            unregister(_SEND_TOOL_NAME)
            applied = bool(register(**params))
            if applied:
                # v0.17.1：到场证据必须自己落在日志里。今晚判断"目录到底挂上没有"
                # 靠的是手读宿主 GET /api/tools——一个需要人现场去问的读数等于没有读数。
                categories = sum(1 for line in catalog.splitlines() if line.startswith("・"))
                self._runstats.surface_applied(
                    chars=len(description), categories=categories, tier=self._settings.send.eagerness
                )
                self.logger.info(
                    f"sticker_send surface applied: chars={len(description)}"
                    f" categories={categories} tier={self._settings.send.eagerness}"
                    f" quick_chars={len(quick_candidates)}"
                )
            return applied
        except Exception:  # noqa: BLE001 - 换档失败不许带走她的工具
            self.logger.warning("sticker_send surface re-register failed", exc_info=True)
            try:
                register(
                    **{
                        **params,
                        "description": str(previous.get("description") or _SEND_TOOL_DESCRIPTION_DEFAULT),
                    }
                )
            except Exception:  # noqa: BLE001 - 回滚也失败：等下一次工具心跳前她没有这个工具
                self.logger.error("sticker_send 回滚注册也失败，等工具心跳补挂", exc_info=True)
            return False

    def _apply_list_tool_surface(self, previous, register, unregister, *, tail_supported: bool) -> bool:
        """Keep lookup willingness in sync with send, retaining original role context."""
        if not isinstance(previous, dict):
            return True
        description = build_list_tool_description(self._settings.send.eagerness)
        if (
            previous.get("description") == description
            and (not tail_supported or previous.get("reply_tail") is True)
        ):
            return True
        params = {
            "name": "sticker_list",
            "description": description,
            "parameters": previous.get("parameters"),
            "handler": self.tool_sticker_list,
            "timeout": float(previous.get("timeout_seconds") or 10.0),
            "role": previous.get("role"),
            **({"reply_tail": True} if tail_supported else {}),
        }
        try:
            unregister("sticker_list")
            if register(**params):
                self.logger.info(
                    f"sticker_list surface applied: chars={len(description)}"
                    f" tier={self._settings.send.eagerness}"
                )
                return True
        except Exception:
            self.logger.warning("sticker_list surface registration failed", exc_info=True)
        try:
            unregister("sticker_list")
            restore = {**params, "description": str(previous.get("description") or "")}
            if tail_supported:
                restore["reply_tail"] = bool(previous.get("reply_tail"))
            register(**restore)
        except Exception:
            self.logger.error("sticker_list surface restore failed", exc_info=True)
        return False

    @ui.action(
        id="send",
        label=tr("actions.send.label", default="Send"),
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="send",
        metadata={"agent_auto": False},
        name=tr("entries.send.name", default="把这张表情发到聊天"),
        description=tr(
            "entries.send.description",
            default="按 id 发送具体表情包。仅给 group、query 或不填参数时返回候选，不发送；"
            "先阅读逐图说明，按回复意图、对象和语气选中后再给 id。没合适的就纯文字。"
            "面板仍可用 id 试发指定图片。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
                "group": {"type": "string", "description": "要发送的表情分类（可选）"},
                "query": {"type": "string", "description": "想表达的情绪或场景关键词（可选）"},
                "text": {
                    "type": "string",
                    "maxLength": SEND_TEXT_MAX_CHARS,
                    "description": (
                        "额外配文（可选）：先单独发文字，再单独发图片。"
                        "正常文字回复时可用 sticker_send(sticker_id=候选ID) 配一张独立表情包，text 无需填写；"
                        "只有额外配文才填 text，配文不要在正文重复"
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        llm_result_fields=["note", "id", "reason", "candidates", "hint", "text_submitted"],
        timeout=30.0,
    )
    async def send_entry(
        self, id: str = "", group: str = "", query: str = "", text: str = "", **kwargs: Any
    ):  # noqa: A002
        if not id or group or query or text:
            result = await self._sticker_send_body(
                sticker_id=id, group=group, query=query, text=text, _source="agent", _browse_all=True, **kwargs
            )
            result = _private_selection_result(result)
            self._note_tool_call(
                name="agent_send",
                result=result,
                group=group if isinstance(group, str) else "",
                query_len=len(query) if isinstance(query, str) else 0,
                gave=gave_shape(sticker_id=id, query=query, group=group, text=text, force=False),
            )
            if not result.get("ok"):
                return Err(SdkError(str(result.get("reason") or "send_failed"), details=result))
            return Ok({**result, "id": result.get("queued") or result.get("sent", "")})
        lanlan = await self._resolve_lanlan(kwargs)
        sticker, failure = self._pick_for_send(id, lanlan)
        if failure is not None or sticker is None:
            return failure if failure is not None else Err(SdkError("sticker_not_found"))
        result = await self._sender.send(
            sticker, lanlan=lanlan, settings=self._settings, source="panel", now=time.time()
        )
        if not result.ok:
            return Err(SdkError(result.code))
        return Ok({"note": "sticker_sent", "id": sticker.id, "desc": sticker.desc})

    @ui.action(
        id="list",
        label=tr("actions.list.label", default="Search"),
        tone="info",
        refresh_context=False,
    )
    @plugin_entry(
        id="list",
        metadata={"agent_auto": False},
        name=tr("entries.list.name", default="搜索表情包"),
        description=tr("entries.list.description", default="按关键词在库里搜（描述/标签/id 子串匹配），返回条目列表"),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": tr("fields.query", default="关键词，留空=全部")},
                "include_disabled": {
                    "type": "boolean",
                    "description": tr("fields.include_disabled", default="是否包含被禁用的"),
                },
            },
        },
        llm_result_fields=["note", "count"],
    )
    async def list_entry(self, query: str = "", include_disabled: bool = False, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        pool = search_stickers(self._library.all(), query if isinstance(query, str) else "", include_disabled=True)
        rows = [s.as_dict() for s in pool if include_disabled or not s.disabled]
        return Ok({"note": "library_listed", "count": len(rows), "stickers": rows})

    @ui.action(
        id="preview",
        label=tr("actions.preview.label", default="Preview"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="preview",
        metadata={"agent_auto": False},
        name=tr("entries.preview.name", default="取一张表情包的预览"),
        description=tr(
            "entries.preview.description",
            default="按段返回图片字节（base64）：面板逐段拉取拼回 dataUrl。分段是为了躲宿主控制通道单帧上限"
            "（实测 3.95MB 图整张回包会被拒发导致超时）。kind=thumb 时改回一张 256px 缩略图"
            "（一次到底、不分段），墙上的格子都走这一档；缩略图拿不到时自动回退原图",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": tr("fields.id", default="表情包 id")},
                "offset": {
                    "type": "integer",
                    "description": tr("fields.offset", default="从第几字节取（0 = 从头）"),
                },
                # v0.17.2：墙上格子只要一张 256px 小图。空或 "full" = 原图分段（老形状一字不变）。
                "kind": {
                    "type": "string",
                    "description": tr("fields.preview_kind", default="thumb = 取缩略图；留空 = 原图"),
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        timeout=15.0,
    )
    async def preview_entry(self, id: str = "", offset: int = 0, kind: str = "", **_):  # noqa: A002
        sticker = self._library.get(id) if isinstance(id, str) else None
        if sticker is None:
            return Err(SdkError("sticker_not_found"))
        # 缩略图先走缓存盘（v0.17.2）：命中时连原图字节都不读——本轮省的就是那一步。
        if isinstance(kind, str) and kind.strip() == "thumb":
            blob = await asyncio.to_thread(self._library.thumb_for, sticker)
            if blob:
                return Ok(
                    {
                        "note": "preview",
                        "id": sticker.id,
                        "mime": "image/jpeg",
                        "size": len(blob),
                        "offset": 0,
                        "next_offset": len(blob),
                        "chunk_base64": base64.b64encode(blob).decode("ascii"),
                        "done": True,
                        "kind": "thumb",
                    }
                )
            # 降级不静默（ PIL 缺席/坏图/超字节都会走到这里），但**整轮只吼一次**：
            # 一墙 190 张各自 fallback 就是 190 行日志，正是本轮要修的那个刷屏。
            if not self._thumb_fallback_logged:
                self._thumb_fallback_logged = True
                self.logger.warning("thumb unavailable; falling back to original bytes (logged once)")
        try:
            data = self._library.image_path(sticker).read_bytes()
        except FileNotFoundError:
            return Err(SdkError("sticker_file_missing"))
        except Exception:
            return Err(SdkError("sticker_image_unreadable"))
        mime = _mime_for_file(sticker.file)
        if mime is None:
            return Err(SdkError("sticker_image_unreadable"))
        start = offset if isinstance(offset, int) and not isinstance(offset, bool) and offset > 0 else 0
        start = min(start, len(data))
        end = min(start + PREVIEW_CHUNK_BYTES, len(data))
        chunk = base64.b64encode(data[start:end]).decode("ascii")
        return Ok(
            {
                "note": "preview",
                "id": sticker.id,
                "mime": mime,
                "size": len(data),
                "offset": start,
                "next_offset": end,
                "chunk_base64": chunk,
                "done": end >= len(data),
            }
        )

    @ui.action(
        id="history",
        label=tr("actions.history.label", default="History"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="history",
        metadata={"agent_auto": False},
        name=tr("entries.history.name", default="最近的表情包使用记录"),
        description=tr(
            "entries.history.description", default="返回发送台账尾部若干条（时刻/用了哪张/谁/成败），不含对话原文"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": tr("fields.limit", default="取几条")},
            },
        },
        llm_result_fields=["note", "count"],
    )
    async def history_entry(self, limit: int = 30, **_):
        take = limit if isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit <= 200 else 30
        return Ok({"note": "usage_loaded", "count": take, "usage": self._library.read_usage(limit=take)})

    @ui.action(
        id="switch",
        label=tr("actions.switch.label", default="Toggle"),
        tone="warning",
        refresh_context=True,
    )
    @plugin_entry(
        id="switch",
        metadata={"agent_auto": False},
        name=tr("entries.switch.name", default="开关表情包管理"),
        description=tr(
            "entries.switch.description",
            default="fail-closed 总开关：关闭后她不发图、看不到目录；库本身的增删改仍可用",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": tr("fields.enabled", default="是否开启")},
            },
            "required": ["enabled"],
        },
        llm_result_fields=["note", "enabled"],
    )
    async def switch_entry(self, enabled: bool = False, **_):
        if not isinstance(enabled, bool):
            return Err(SdkError("invalid_value"))
        try:
            await self.config.set("sticker_manager.enabled", bool(enabled))
        except Exception:
            self.logger.warning("failed to persist [sticker_manager].enabled", exc_info=True)
            return Err(SdkError("config_unavailable"))
        await self._reload_settings()
        return Ok({"note": "enabled" if enabled else "disabled", "enabled": self._settings.enabled})

    @ui.action(
        id="set_eagerness",
        label=tr("actions.set_eagerness.label", default="Set eagerness"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="set_eagerness",
        metadata={"agent_auto": False},
        name=tr("entries.set_eagerness.name", default="设置配表情积极度"),
        description=tr(
            "entries.set_eagerness.description",
            default="三档（矜持/自然/爱发）只管她有多想配图；冷却、最近不重复、概率闸一律不动",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "eagerness": {
                    "type": "string",
                    "enum": list(EAGERNESS_LEVELS),
                    "description": tr("fields.eagerness", default="档位"),
                },
            },
            "required": ["eagerness"],
        },
        llm_result_fields=["note", "eagerness"],
    )
    async def set_eagerness_entry(self, eagerness: str = "", **_):
        # 只认在册档位：写错值不当"顺手兑成默认档"处理——那是主人的决定被静默改掉。
        if eagerness not in EAGERNESS_LEVELS:
            return Err(SdkError("invalid_value"))
        try:
            await self.config.set("sticker_manager.send.eagerness", str(eagerness))
        except Exception:
            self.logger.warning("failed to persist [sticker_manager.send].eagerness", exc_info=True)
            return Err(SdkError("config_unavailable"))
        await self._reload_settings()
        # 换档当场把工具描述也换掉：那才是每轮都在场的那句话（v0.15.0）。
        self._refresh_send_tool_surface()
        return Ok({"note": "eagerness_set", "eagerness": self._settings.send.eagerness})

    @ui.action(
        id="set_send_cooldown",
        label=tr("actions.set_send_cooldown.label", default="Save send cooldown"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="set_send_cooldown",
        metadata={"agent_auto": False},
        name=tr("entries.set_send_cooldown.name", default="设置发送冷却"),
        description=tr(
            "entries.set_send_cooldown.description",
            default="设置同一角色两次发图的最小间隔，0 到 3600 秒；0 关闭发送冷却",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "cooldown_sec": {
                    "type": "number", "minimum": 0, "maximum": 3600,
                    "description": tr("fields.cooldown_sec", default="发送冷却（秒）"),
                },
            },
            "required": ["cooldown_sec"],
        },
        llm_result_fields=["note", "cooldown_sec"],
    )
    async def set_send_cooldown_entry(self, cooldown_sec: float = 20.0, **_):
        if (
            isinstance(cooldown_sec, bool)
            or not isinstance(cooldown_sec, (int, float))
            or not 0 <= cooldown_sec <= 3600
            or not math.isfinite(cooldown_sec)
        ):
            return Err(SdkError("invalid_value"))
        try:
            await self.config.set("sticker_manager.send.cooldown_sec", float(cooldown_sec))
        except Exception:
            self.logger.warning("failed to persist [sticker_manager.send].cooldown_sec", exc_info=True)
            return Err(SdkError("config_unavailable"))
        await self._reload_settings()
        if self._settings.send.cooldown_sec != cooldown_sec:
            self.logger.warning("send cooldown saved but effective value differs from requested value")
            return Err(SdkError("config_unavailable"))
        return Ok({"note": "send_cooldown_set", "cooldown_sec": self._settings.send.cooldown_sec})

    @ui.action(
        id="set_eager_attachment_turns",
        label=tr("actions.set_eager_attachment_turns.label", default="保存补图轮数"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="set_eager_attachment_turns",
        metadata={"agent_auto": False},
        name=tr("entries.set_eager_attachment_turns.name", default="设置补图触发轮数"),
        description=tr(
            "entries.set_eager_attachment_turns.description",
            default="爱发档连续多少轮完整回复未配图后独立选图，整数 0 到 10；0 每轮帮选",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "turns": {
                    "type": "integer", "minimum": 0, "maximum": 10,
                    "description": tr("fields.eager_attachment_turns", default="补图触发轮数"),
                },
            },
            "required": ["turns"],
        },
        llm_result_fields=["note", "turns"],
    )
    async def set_eager_attachment_turns_entry(self, turns: int = 1, **_):
        if isinstance(turns, bool) or not isinstance(turns, int) or not 0 <= turns <= 10:
            return Err(SdkError("invalid_value"))
        try:
            await self.config.set("sticker_manager.send.eager_attachment_turns", turns)
        except Exception:
            self.logger.warning("failed to persist [sticker_manager.send].eager_attachment_turns", exc_info=True)
            return Err(SdkError("config_unavailable"))
        await self._reload_settings()
        if self._settings.send.eager_attachment_turns != turns:
            self.logger.warning("eager attachment turns saved but effective value differs from requested value")
            return Err(SdkError("config_unavailable"))
        return Ok({"note": "eager_attachment_turns_set", "turns": self._settings.send.eager_attachment_turns})

    @ui.action(
        id="set_reminder_interval",
        label=tr("actions.set_reminder_interval.label", default="Save reminder interval"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="set_reminder_interval",
        metadata={"agent_auto": False},
        name=tr("entries.set_reminder_interval.name", default="设置提醒最小间隔"),
        description=tr(
            "entries.set_reminder_interval.description",
            default="提醒间隔可设为 0 到 60 秒；0 关闭时间限制，不改变轮数门槛与发送规则",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "min_interval_sec": {
                    "type": "number", "minimum": 0, "maximum": 60,
                    "description": tr("fields.min_interval_sec", default="提醒最小间隔（秒）"),
                },
            },
            "required": ["min_interval_sec"],
        },
        llm_result_fields=["note", "min_interval_sec"],
    )
    async def set_reminder_interval_entry(self, min_interval_sec: float = 60.0, **_):
        if (
            isinstance(min_interval_sec, bool)
            or not isinstance(min_interval_sec, (int, float))
            or not 0 <= min_interval_sec <= 60
            or not math.isfinite(min_interval_sec)
        ):
            return Err(SdkError("invalid_value"))
        try:
            await self.config.set("sticker_manager.awareness.min_interval_sec", float(min_interval_sec))
        except Exception:
            self.logger.warning("failed to persist [sticker_manager.awareness].min_interval_sec", exc_info=True)
            return Err(SdkError("config_unavailable"))
        await self._reload_settings()
        return Ok({
            "note": "reminder_interval_set",
            "min_interval_sec": self._settings.awareness.min_interval_sec,
        })

    @ui.action(
        id="repair",
        label=tr("actions.repair.label", default="Repair"),
        tone="warning",
        refresh_context=True,
    )
    @plugin_entry(
        id="repair",
        metadata={"agent_auto": False},
        name=tr("entries.repair.name", default="库体检与自修复"),
        description=tr(
            "entries.repair.description",
            default="清掉图片文件已丢失的条目、删掉没人引用的孤儿文件、补齐旧条目的内容指纹；返回各项计数",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["note", "removed_entries", "purged_files", "backfilled_hashes"],
        timeout=30.0,
    )
    async def repair_entry(self, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        counts = self._library.repair()
        return Ok({"note": "library_repaired", **counts})

    @ui.action(
        id="import_inbox",
        label=tr("actions.import_inbox.label", default="Import"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="import_inbox",
        metadata={"agent_auto": False},
        name=tr("entries.import_inbox.name", default="导入收件箱里的图片"),
        description=tr(
            "entries.import_inbox.description",
            default="把收件箱目录里的图片逐张收进库（描述取自文件名，重复自动跳过）；成功与重复的源文件会被删掉，超限/坏图保留原地可重试",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tags": {
                    "type": "string",
                    "description": tr("fields.tags", default="标签，逗号分隔（可选，整批共用）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，整批共用；包里自带的优先）"),
                },
                "zone": {
                    "type": "string",
                    "description": tr("fields.zone_target", default="目标区 id（留空=落在分类所在的区/激活区）"),
                },
            },
        },
        llm_result_fields=["note", "imported", "duplicates", "rejected", "failed"],
        timeout=120.0,
    )
    async def import_inbox_entry(self, tags: str = "", group: str = "", zone: str = "", **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        summary = self._library.ingest_inbox(
            tags=parse_tags_field(tags),
            group=normalize_group(group),
            zone=zone if isinstance(zone, str) else "",
        )
        return Ok({"note": "inbox_imported", **summary})

    # ------------------------------------------------------------------
    # 面板选择文件直传（v0.6.0）：zip 分块上传会话三步曲。
    # 为什么不走单 entry 整块 base64：控制面板的 ZeroMQ 帧上限呰不下套图包
    # （见 core.catalog UPLOAD_CHUNK_BYTES 注释）；为什么不只修面板：服务层
    # 需要逐块把关总量/乱序/会话寿命，这些纪律只能长在服务端。
    # 模型不需要这三个入口（它发图走 sticker_send，不搬运文件），但 entry 面
    # 就是面板/命令面板的 RPC 面，照旧双装饰。
    # ------------------------------------------------------------------

    @ui.action(
        id="import_upload_start",
        label=tr("actions.import_upload_start.label", default="Upload start"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="import_upload_start",
        metadata={"agent_auto": False},
        name=tr("entries.import_upload_start.name", default="开始上传套图包"),
        description=tr(
            "entries.import_upload_start.description",
            default="面板选择文件直传第一步：开一个 .zip 上传会话，返回 session 与每块原始字节上限；面板专用通道，模型不需要调",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": tr("fields.upload_name", default="文件名（须以 .zip 结尾）")},
                "size": {
                    "type": "integer",
                    "description": tr("fields.upload_size", default="总字节数（仅用于提前拒绝明显超限）"),
                },
            },
            "required": ["name"],
        },
        llm_result_fields=["note", "session", "chunk_bytes"],
        timeout=30.0,
    )
    async def import_upload_start_entry(self, name: str = "", size: Any = None, **_):
        sid, error = self._library.upload_start(name, size=size)
        if error:
            return Err(SdkError(error))
        return Ok({"note": "upload_opened", "session": sid, "chunk_bytes": UPLOAD_CHUNK_BYTES})

    @ui.action(
        id="import_upload_chunk",
        label=tr("actions.import_upload_chunk.label", default="Upload chunk"),
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="import_upload_chunk",
        metadata={"agent_auto": False},
        name=tr("entries.import_upload_chunk.name", default="上传一个分块"),
        description=tr(
            "entries.import_upload_chunk.description",
            default="把文件切块 base64 后按 seq 从 0 连续追加到上传会话；乱序/超限/会话已死会如实报错并作废会话",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": tr("fields.upload_session", default="上传会话 id")},
                "seq": {"type": "integer", "description": tr("fields.upload_seq", default="分块序号（从 0 连续）")},
                "data_base64": {
                    "type": "string",
                    "description": tr("fields.upload_chunk_base64", default="本块 base64（原始体≤chunk_bytes）"),
                },
            },
            "required": ["session", "seq", "data_base64"],
        },
        llm_result_fields=["note", "received"],
        timeout=30.0,
    )
    async def import_upload_chunk_entry(self, session: str = "", seq: int = -1, data_base64: str = "", **_):
        if not isinstance(session, str) or not session:
            return Err(SdkError("upload_session_unknown"))
        if not isinstance(seq, int) or isinstance(seq, bool):
            return Err(SdkError("upload_seq_gap"))
        if not isinstance(data_base64, str) or not data_base64:
            return Err(SdkError("upload_chunk_bad"))
        if len(data_base64) > _UPLOAD_B64_MAX_CHARS:
            return Err(SdkError("upload_too_large"))
        try:
            payload = base64.b64decode(data_base64, validate=False)
        except (binascii.Error, ValueError):
            return Err(SdkError("upload_chunk_bad"))
        if not payload:
            return Err(SdkError("upload_chunk_bad"))
        error = self._library.upload_append(session, seq, payload)
        if error:
            return Err(SdkError(error))
        return Ok({"note": "upload_chunked", "received": seq + 1})

    @ui.action(
        id="import_upload_finish",
        label=tr("actions.import_upload_finish.label", default="Upload finish"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="import_upload_finish",
        metadata={"agent_auto": False},
        name=tr("entries.import_upload_finish.name", default="完成上传并导入套图包"),
        description=tr(
            "entries.import_upload_finish.description",
            default="关闭上传会话，把暂存的 zip 按收件箱认包同一把尺整批收进库（manifest v2 协议同款），返回四类计数；会话随即作废、暂存体删除",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": tr("fields.upload_session", default="上传会话 id")},
                "tags": {
                    "type": "string",
                    "description": tr("fields.tags", default="标签，逗号分隔（可选，整批共用）"),
                },
                "group": {
                    "type": "string",
                    "description": tr("fields.group", default="套图分组（可选，整批共用；包里自带的优先）"),
                },
                "zone": {
                    "type": "string",
                    "description": tr("fields.zone_target", default="目标区 id（留空=落在分类所在的区/激活区）"),
                },
            },
            "required": ["session"],
        },
        llm_result_fields=["note", "imported", "duplicates", "rejected", "failed"],
        timeout=120.0,
    )
    async def import_upload_finish_entry(self, session: str = "", tags: str = "", group: str = "", zone: str = "", **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        summary, error = self._library.upload_finish(
            session,
            tags=parse_tags_field(tags),
            group=normalize_group(group),
            zone=zone if isinstance(zone, str) else "",
        )
        if error:
            return Err(SdkError(error))
        return Ok({"note": "pack_uploaded", **summary})

    @ui.action(
        id="export_pack",
        label=tr("actions.export_pack.label", default="Export"),
        tone="info",
        refresh_context=False,
    )
    @plugin_entry(
        id="export_pack",
        metadata={"agent_auto": False},
        name=tr("entries.export_pack.name", default="导出套图包"),
        description=tr(
            "entries.export_pack.description",
            default="把整本表情库打成一个套图 zip（图 + manifest）写到 exports 目录，返回文件路径；导入方放回收件箱即可整套收进",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["note", "exported", "skipped", "file"],
        timeout=120.0,
    )
    async def export_pack_entry(self, **_):
        loaded = self._library.load()
        if not loaded.ok:
            return Err(SdkError(loaded.code))
        result, error = self._library.export_pack(now=time.time())
        if error:
            return Err(SdkError(error))
        return Ok({"note": "pack_exported", **result})

    @ui.action(
        id="awareness_now",
        label=tr("actions.awareness_now.label", default="Ping"),
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="awareness_now",
        metadata={"agent_auto": False},
        name=tr("entries.awareness_now.name", default="立刻注一条存在感"),
        description=tr(
            "entries.awareness_now.description",
            default="调试用：绕过间隔闸，立刻给当前角色卡注一条'你有表情包'的静默提示（用户看不见）",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["note", "status"],
        timeout=15.0,
    )
    async def awareness_now_entry(self, **kwargs):
        lanlan = await self._resolve_lanlan(kwargs)
        result = await self._awareness.inject_now(settings=self._settings, lanlan=lanlan, now=time.time())
        status = str(result.get("status", ""))
        if status in {"disabled", "no_target", "empty_library"}:
            return Err(SdkError(f"awareness_{status}"))
        return Ok({"note": "awareness", "status": status})

    # ------------------------------------------------------------------
    # 面板上下文
    # ------------------------------------------------------------------

    @ui.context(id="dashboard", title=tr("panel.title", default="表情包管理"))
    async def dashboard_context(self, **kwargs: Any) -> dict[str, Any]:
        loaded = self._library.load()
        settings = self._settings
        lanlan = await self._resolve_lanlan(kwargs)
        stickers = self._library.all()
        group_descs = self._library.group_descs()
        group_counts: dict[str, int] = {}
        for s in stickers:
            if s.group:
                group_counts[s.group] = group_counts.get(s.group, 0) + 1
        groups = [
            {
                "name": n,
                "count": group_counts.get(n, 0),
                "desc": group_descs.get(n, ""),
                "zone": self._library.zone_of_group(n) or self._library.active_zone(),
            }
            for n in sorted(set(group_counts) | set(group_descs))
        ]
        payload: dict[str, Any] = {
            "enabled": settings.enabled,
            # v0.14.0：面板的「配表情积极度」Select 读这一键，写走 set_eagerness 入口。
            "eagerness": settings.send.eagerness,
            "lanlan": lanlan,
            "counts": {
                "total": len(stickers),
                "enabled": sum(1 for s in stickers if not s.disabled),
                "sent_total": sum(s.use_count for s in stickers),
                "groups": len(groups),
            },
            "stickers": [s.as_dict() for s in stickers],
            "groups": groups,
            "group_descs": group_descs,
            # J-1：区的脸面（tab 序=插入序）+ 她的世界窗口在哪个区。
            "zones": self._library.zones(),
            "active_zone": self._library.active_zone(),
            # J-2：官方区的脸面——pack=随包官方装在不在（恢复按钮的前提），zone=它在哪个区
            # （空串=不在册，面板 tab 尾出「恢复官方收藏」），seeded=播种台账。
            # J-3：pack_version=已应用的官方包内容版本（0=从没刷过标签）——排障用，面板不读。
            "official": {
                "pack": pack_exists(self._official_pack_path()),
                "zone": self._library.official_zone(),
                "seeded": self._library.official_seeded(),
                "pack_version": self._library.official_pack_version(),
            },
            "usage": self._library.read_usage(limit=12),
            "inbox": {
                "pending": len(self._library.inbox_files()),
                "path": str(self._library.inbox_dir),
            },
            "awareness": self._awareness.snapshot(settings=settings, now=time.time()),
            # v0.17.1 观测轮：本次运行的四把读数（内存态，重启归零——面板必须标"本次运行"）。
            # 摆在这里是为了回答"到底断在哪一环"：话轮有多少 → 她调了几次 → 成了几次 → 拦了几次。
            "run": self._runstats.snapshot(turns=self._awareness.turns_seen),
            "eager_attachment": self._eager_attachment.snapshot(),
            "config": {
                "cooldown_sec": settings.send.cooldown_sec,
                "eager_attachment_turns": settings.send.eager_attachment_turns,
                "inline_max_bytes": settings.send.inline_max_bytes,
                "catalog_limit_for_model": settings.storage.catalog_limit_for_model,
                "recent_dedup_count": settings.send.recent_dedup_count,
                "probability": settings.send.probability,
                "probability_reuse_sec": settings.send.probability_reuse_sec,
                "awareness_enabled": settings.awareness.enabled,
                "awareness_interval_sec": settings.awareness.interval_sec,
            },
        }
        if not loaded.ok:
            payload["error_code"] = loaded.code
        return payload

    # ------------------------------------------------------------------
    # LLM 工具：她可以自主调用
    # ------------------------------------------------------------------

    @llm_tool(
        name="sticker_list",
        description=build_list_tool_description(EAGERNESS_DEFAULT),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "正向意图短词，空格或逗号分隔，如「自己被摸头 舒服」「给对方泡茶 已完成 开心」；"
                        "等待用「等待收到 期待」「等待来访 期待」，已发生用「收到礼物 开心」「见面问候 开心」；"
                        "写具体动作、对象及阶段，不只写话题或情绪。明确动作至少匹配一项，可不填"
                    ),
                },
                "group": {"type": "string", "description": "只看这个套图分组的全部图（组名从分类行里拿）"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    async def tool_sticker_list(self, query: str = "", group: str = "", **kwargs: Any) -> dict[str, Any]:
        # 薄壳只干一件事：把"她调了这个工具、结果如何"记成一痕（v0.17.1 观测轮）。
        # 判定逻辑一律留在 `_sticker_list_body` 里——本轮零行为变更，日志不参与决策。
        started = time.perf_counter()
        self.logger.info("reply tool enter: name=sticker_list " + reply_context_fields(kwargs))
        result = await self._sticker_list_body(query=query, group=group, **kwargs)
        result = _private_selection_result(result)
        self._note_tool_call(
            name="sticker_list",
            result=result,
            group=group if isinstance(group, str) else "",
            query_len=len(query) if isinstance(query, str) else 0,
            gave=gave_shape(query=query, group=group),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        self.logger.info("reply tool return: name=sticker_list " + reply_context_fields(kwargs))
        return result

    def _find_candidates(
        self, pool: list[Sticker], query: str, *, recent: set[str],
    ) -> tuple[list[Sticker], list[Sticker]]:
        diagnostics: dict[str, int] = {}
        available = search_intent_candidates(pool, query, diagnostics=diagnostics)
        selectable = [s for s in available if s.id not in recent]
        diagnostics["recent_filtered"] = len(available) - len(selectable)
        diagnostics["selectable"] = len(selectable)
        self.logger.info(
            "candidate lookup: " + " ".join(f"{key}={value}" for key, value in diagnostics.items())
        )
        return available, selectable

    def _note_candidate_scope_miss(self, pool: list[Sticker], recent: set[str]) -> None:
        self.logger.info(
            f"candidate lookup: scope_unavailable=1 active={len(pool)}"
            f" disabled={sum(s.disabled for s in pool)}"
            f" recent_filtered={sum(not s.disabled and s.id in recent for s in pool)}"
        )

    async def _sticker_list_body(self, query: str = "", group: str = "", **kwargs: Any) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"ok": False, "reason": "not_enabled"}
        loaded = self._library.load()
        if not loaded.ok:
            return {"ok": False, "reason": loaded.code}
        query = query if isinstance(query, str) else ""
        limit = self._settings.storage.catalog_limit_for_model
        # J-1：目录只报激活区——她看不到的区，连名字都不该出现在分类行里。
        lanlan = await self._resolve_lanlan(kwargs)
        recent = self._sender.recent_sent_ids(lanlan, self._settings)
        active = self._library.active_pool()
        all_stickers = [s for s in active if not s.disabled and s.id not in recent]
        descs = self._library.group_descs()
        gname = normalize_group(group)
        if gname:
            names = sorted({s.group for s in all_stickers if s.group})
            matched = _match_group_names(names, gname)
            if len(matched) > 1:
                overview = format_group_overview(
                    [s for s in all_stickers if s.group in matched], {n: descs.get(n, "") for n in matched}
                )
                return {
                    "ok": True,
                    "count": len(matched),
                    "catalog": overview,
                    "note": "group 撞了多个组名——更准的名字再来一次",
                }
            if not matched:
                self._note_candidate_scope_miss(active, recent)
                return {
                    "ok": True,
                    "count": 0,
                    "catalog": "",
                    "note": "该分类没有可选候选，未发送；没有合适图片就纯文字。",
                }
            _, pool = self._find_candidates(
                [s for s in active if s.group == matched[0]], query, recent=recent,
            )
            if query.strip():
                limit = min(limit, SEND_CANDIDATES_MAX)
            catalog = format_catalog_for_model(pool, limit, descs)
            header = f"「{matched[0]}」" + (f"：{descs[matched[0]]}" if descs.get(matched[0]) else "")
            return {
                "ok": True,
                "count": pool and len(pool) or 0,
                "catalog": f"{header}\n{catalog}" if catalog else "",
                "candidate_ids": [s.id for s in pool[:limit]],
                "shown": min(len(pool), limit),
                "hint": SELECTION_HINT,
            }
        _, pool = self._find_candidates(active, query, recent=recent)
        if isinstance(query, str) and query.strip():
            limit = min(limit, SEND_CANDIDATES_MAX)
        catalog = format_catalog_for_model(pool, limit, descs)
        if not catalog:
            return {
                "ok": True, "count": 0, "catalog": "",
                "note": "没有可用或匹配的候选，未发送；没有合适图片就纯文字。",
            }
        if not (isinstance(query, str) and query.strip()):
            # 轮 F 两级目录：无搜索词时先给分类行再给条目——她先看“有哪些套图”，
            # 下钻用 group 参数（对齐外部系统“分类即 prompt”的心智，只是我们在工具结果里给）。
            overview = format_group_overview(all_stickers, descs)
            if overview:
                catalog = f"【套图分类】\n{overview}\n【条目】\n{catalog}"
        return {
            "ok": True, "count": len(pool), "catalog": catalog,
            "candidate_ids": [s.id for s in pool[:limit]], "hint": SELECTION_HINT,
            "shown": min(len(pool), limit),
        }

    @llm_tool(
        name="sticker_send",
        description=_SEND_TOOL_DESCRIPTION_DEFAULT,
        parameters={
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": "分类名；只给分类时返回逐图候选，不会自动发图。",
                },
                "sticker_id": {"type": "string", "description": "阅读候选说明并确认贴合回复后，填具体图片 ID 才会发送。"},
                "query": {
                    "type": "string",
                    "description": (
                        "空格或逗号分隔的正向意图短词，写清动作、对象及阶段，不只写话题或情绪；"
                        "明确动作至少匹配一项，仅查候选，可用 group 限定分类。"
                    ),
                },
                "force": {
                    "type": "boolean",
                    "description": "仅当主人明确点名要再看/再发这张时置 true：跳过最近不重复与概率闸（冷却仍生效）",
                },
                "text": {
                    "type": "string",
                    "description": (
                        f"额外配文（可选，≤{SEND_TEXT_MAX_CHARS} 字）。普通回复照常说，此项留空只发图；"
                        "非空时先单独发配文，再单独发图片，配文不要在正文重复。"
                    ),
                },
            },
            "required": [],
        },
        timeout=20.0,
    )
    async def tool_sticker_send(
        self,
        sticker_id: str = "",
        query: str = "",
        group: str = "",
        force: bool = False,
        text: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        # 同 `tool_sticker_list`：薄壳只留痕，判定一律在 `_sticker_send_body`。
        # 这一痕就是本轮的要害——"她没调"与"她调了被闸拦下"以前在日志里长得一模一样。
        started = time.perf_counter()
        self.logger.info(
            "reply tool enter: name=sticker_send " + reply_context_fields(kwargs)
            + f" caption_chars={len(text) if isinstance(text, str) else 0}"
        )
        result = await self._sticker_send_body(
            sticker_id=sticker_id, query=query, group=group, force=force, text=text, **kwargs
        )
        result = _private_selection_result(result)
        self._note_tool_call(
            name="sticker_send",
            result=result,
            group=group if isinstance(group, str) else "",
            query_len=len(query) if isinstance(query, str) else 0,
            gave=gave_shape(sticker_id=sticker_id, query=query, group=group, force=force, text=text),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        self.logger.info("reply tool return: name=sticker_send " + reply_context_fields(kwargs))
        return result

    async def _sticker_send_body(
        self,
        sticker_id: str = "",
        query: str = "",
        group: str = "",
        force: bool = False,
        text: str = "",
        _source: str = "tool",
        _browse_all: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"ok": False, "reason": "not_enabled"}
        lanlan = await self._resolve_lanlan(kwargs)
        loaded = self._library.load()
        if not loaded.ok:
            return {"ok": False, "reason": loaded.code}
        settings = self._settings
        bypass = bool(force)
        # 去重（轮 D①）：query 候选池剔除"她最近发过的 N 张"（同一角色卡）。
        # 显式 id 不在这拦——那是 sender.send 里和冷却/概率闸一起过的同一把尺，
        # 保证 tool 以外没有第二条能绕开的通道。
        recent = (
            self._sender.recent_sent_ids(lanlan, settings)
            if (not bypass and settings.send.recent_dedup_count > 0)
            else set()
        )
        caption = text.strip() if isinstance(text, str) else ""
        if len(caption) > SEND_TEXT_MAX_CHARS:
            return {
                "ok": False, "reason": "text_too_long",
                "hint": f"配文最多 {SEND_TEXT_MAX_CHARS} 字，普通回复照常说即可。",
            }
        sticker = None
        has_id = isinstance(sticker_id, str) and bool(sticker_id.strip())
        has_query = isinstance(query, str) and bool(query.strip())
        has_group = isinstance(group, str) and bool(normalize_group(group))
        # J-1：她的候选池 = 激活区。非激活区的图与分类对她不存在（id 点到了也算没找到）。
        pool = self._library.active_pool()
        known_ids = {s.id for s in pool}
        if has_id:
            raw_sticker = self._library.get(sticker_id.strip())
            sticker = raw_sticker if raw_sticker is not None and raw_sticker.id in known_ids else None
            if sticker is not None and sticker.disabled:
                sticker = None
            if sticker is None:
                return {"ok": False, "reason": "no_match", "hint": "该图片不可用，未发送；没有合适图片就纯文字。"}
        candidates: list[Sticker] = []
        if not has_id and has_group:
            gname = normalize_group(group)
            names = sorted({s.group for s in pool if s.group})
            matched = _match_group_names(names, gname)
            if len(matched) > 1:
                return {
                    "ok": True,
                    "sent": "",
                    "note": "group_candidates",
                    "count": len(matched),
                    "candidates": "\n".join(f"・{n}" for n in matched),
                    "hint": "组名撞了多个——用更准的组名或组内某张的 id 再发一次。",
                }
            if not matched:
                self._note_candidate_scope_miss(pool, recent)
                return {
                    "ok": False,
                    "reason": "group_not_found",
                    "groups": names,
                }
            pool = [s for s in pool if s.group == matched[0]]
            if not any(not s.disabled for s in pool):
                self._note_candidate_scope_miss(pool, recent)
                return {"ok": False, "reason": "group_empty", "hint": "这一分类没有可用图，继续纯文字回复。"}
        if not has_id and (has_group or has_query or _browse_all):
            available, selectable = self._find_candidates(pool, query if has_query else "", recent=recent)
            if available and not selectable:
                return {
                    "ok": False,
                    "reason": "recent_repeat",
                    "hint": "贴切候选最近已发过，继续文字回复，不必找不相关的图。",
                }
            limit = self._settings.storage.catalog_limit_for_model
            candidates = selectable[: min(limit, SEND_CANDIDATES_MAX) if has_query else limit]
        if candidates:
            catalog = format_catalog_for_model(candidates, len(candidates), self._library.group_descs())
            return {
                "ok": True,
                "sent": "",
                "note": "multi_candidates",
                "count": len(candidates),
                "candidates": catalog,
                "candidate_ids": [s.id for s in candidates],
                "hint": SELECTION_HINT,
            }
        if sticker is None:
            return {
                "ok": False,
                "reason": "no_match" if (has_id or has_query or has_group or _browse_all) else "id_or_query_required",
                "hint": "没有贴切候选，未发送；继续纯文字回复，不必换分类凑图。",
            }
        host_reply = _host_reply_from_kwargs(kwargs) if _source == "tool" else None
        result = await self._sender.send(
            sticker,
            lanlan=lanlan,
            settings=settings,
            source=_source,
            now=time.time(),
            force=bypass,
            text=caption,
            **({"host_reply": host_reply} if host_reply is not None else {}),
        )
        if not result.ok:
            if not getattr(result, "failure_recorded", False):
                self._sender.note_attempt_failed(
                    lanlan=lanlan,
                    sticker_id=sticker.id,
                    code=result.code,
                    settings=self._settings,
                    now=time.time(),
                    source=_source,
                    **({"text_submitted": True} if result.text_submitted else {}),
                )
            out: dict[str, Any] = {"ok": False, "reason": result.code, "tried": sticker.id}
            if getattr(result, "delivery_recorded", False):
                out["_delivery_recorded"] = True
            hint = _SEND_HINTS.get(result.code)
            if hint:
                out["hint"] = hint
            if result.text_submitted:
                out["text_submitted"] = True
                out["hint"] = "配文已提交，但图片未提交成功。不要重复发送这段配文，继续正常回复。"
            return out
        if result.queued:
            if result.detail.startswith("host_reply_tail"):
                tail_note = (
                    "表情包登记结果暂未确认；如宿主接受，将排在本轮全部文字之后。"
                    if result.detail == "host_reply_tail_uncertain"
                    else "表情包已登记，等待本轮全部文字显示后再投递。"
                )
                return {
                    "ok": True,
                    "sent": "",
                    "queued": sticker.id,
                    "desc": sticker.catalog_body(self._library.group_descs()),
                    "note": (
                        tail_note
                        + "无需等待或重复提交这张图。"
                        + NEXT_STEP_NOTE.removeprefix("表情包已提交到聊天，")
                    ),
                }
            return {
                "ok": True,
                "sent": "",
                "queued": sticker.id,
                "desc": sticker.catalog_body(self._library.group_descs()),
                "note": (
                    "表情包已排队，会在本轮回复与显示缓冲结束后发出。继续正常回复，无需等待；"
                    "仅同一轮同一张图不重复提交。"
                    + NEXT_STEP_NOTE.removeprefix("表情包已提交到聊天，")
                ),
            }
        if result.detail == "host_reply_tail_duplicate":
            return {
                "ok": True, "sent": "",
                "note": "同一轮这张图已登记，不必再次发送。"
                + NEXT_STEP_NOTE.removeprefix("表情包已提交到聊天，"),
            }
        return {
            "ok": True,
            "sent": sticker.id,
            "desc": sticker.catalog_body(self._library.group_descs()),
            # v0.19.0：同门每个情绪工具成功后都用 note 指挥下一步，我们以前只回三个字段——
            # 她做完一件事后拿不到任何"接下来呢"，那一面是白扔的。不提任何闸（陷阱 29）。
            "note": NEXT_STEP_NOTE,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _reload_settings(self) -> StickerManagerSettings:
        try:
            config = await self.config.dump()
        except Exception:
            self.logger.warning("config dump failed; keeping previous settings", exc_info=True)
            return self._settings
        with self._eager_attachment.control_lock:
            self._eager_attachment.invalidate()
            self._settings = StickerManagerSettings.from_config(config if isinstance(config, dict) else {})
        self.logger.info(
            f"sticker_manager settings applied: send_cooldown_sec={self._settings.send.cooldown_sec}"
            f" reminder_min_interval_sec={self._settings.awareness.min_interval_sec}"
        )
        if not self._settings.enabled:
            self._note_deferred_outcomes(self._sender.cancel_pending(settings=self._settings))
            self._note_deferred_outcomes(await self._sender.cancel_host_pending(settings=self._settings))
        return self._settings

    def _pick_for_send(self, sticker_id: Any, lanlan: str) -> tuple[Sticker | None, Any | None]:
        loaded = self._library.load()
        if not loaded.ok:
            return None, Err(SdkError(loaded.code))
        if not isinstance(sticker_id, str) or not sticker_id.strip():
            return None, Err(SdkError("sticker_not_found"))
        sticker = self._library.get(sticker_id.strip())
        if sticker is None:
            return None, Err(SdkError("sticker_not_found"))
        _ = lanlan
        return sticker, None


def _host_reply_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    ctx_obj = kwargs.get("_ctx")
    value = ctx_obj.get("host_reply") if isinstance(ctx_obj, dict) else None
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        return None
    required = ("reply_id", "request_id", "role", "call_id", "source", "token")
    if not all(isinstance(value.get(key), str) and value[key].strip() for key in required):
        return None
    return dict(value)


def _lanlan_from_kwargs(kwargs: dict[str, Any]) -> str:
    """Use the original host reply's role, then the legacy entry hint."""
    host_reply = _host_reply_from_kwargs(kwargs)
    if host_reply is not None:
        return host_reply["role"]
    ctx_obj = kwargs.get("_ctx") if isinstance(kwargs, dict) else None
    if isinstance(ctx_obj, dict):
        name = ctx_obj.get("lanlan_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def _mime_for_file(file_name: str) -> str | None:
    lowered = file_name.lower()
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".gif"):
        return "image/gif"
    if lowered.endswith(".webp"):
        return "image/webp"
    return None
