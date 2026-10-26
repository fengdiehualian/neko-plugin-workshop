from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from typing import Any

from . import media_seam
from .pipeline_models import (
    QQDeliveryPlan,
    QQDeliveryResult,
    QQMessageBlock,
    backlog_sender_label,
)


class QQReplyDeliveryNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def deliver(
        self, plan: QQDeliveryPlan | None, *,
        consent_gate: Callable[[], bool] | None = None,
    ) -> QQDeliveryResult | None:
        """consent_gate 返回 True 表示本轮回复依赖的记忆授权已被撤销。

        多块计划的块间有 2-5 秒拟人停顿，撤销可能落在任意一段停顿里——
        闸只放在发送前，后面几块照样会在 opt-out 之后发出去。"""
        if not plan or not plan.blocks:
            return None

        blocks = plan.blocks
        first_text = ""
        # 判据分两条线：承载正文的块（文本/语音/按钮降级/卡片）决定
        # delivered——记忆里存的那一行就是正文；装饰块（poke/表情包）发不
        # 出去不该把已经送达的正文判成未投递（模板本来就让 poke 单独成块
        # 跟在正文块前后，冷却期跳过会让活跃群每隔一条就丢一次记忆）。
        # 纯装饰计划没有正文可判，就如实反映"到底有没有东西发出去"。
        content_attempted = False
        content_sent = True
        decoration_sent = False
        for i, block in enumerate(blocks):
            if i > 0:
                # 块间延迟：模拟真人打字间隔
                await asyncio.sleep(random.uniform(2.0, 5.0))

            if consent_gate is not None and consent_gate():
                # 撤销落在块间停顿里：剩余块不再发出，并且整条按未投递
                # 处理——已发出的部分同样带着被撤销的记忆内容，历史里那
                # 一行不该再进提取。
                self.plugin.logger.warning(
                    "记忆授权在分块投递途中被撤销，剩余消息块不再发送"
                )
                content_attempted = True
                content_sent = False
                break

            if block.poke:
                if await self._send_poke(plan, block):
                    decoration_sent = True
                continue

            if block.record:
                # record 与 text 同块时**两者都要发**：提示词明说 `<record>` 可以
                # 和 `<text>` 组合，而这里以前直接 `continue`，那段文字永远发不出去
                # ——用户听到语音、看不到文字，历史行里存的却是文字。
                # 顺序与 `<sticker>` 那条「先发文字、表情包跟发」一致：先文字后语音。
                #
                # `keyboard` 仍不传给文本分支：按钮在语音块里没有意义，把它的文案
                # 并进正文等于凭空多送一段用户没要求的文字（`delivered_blocks_text`
                # 对 record 块的 keyboard 排除逻辑与此保持一致）。
                text = self._compose_text(block)
                if text:
                    if i == 0:
                        first_text = text
                    content_attempted = True
                    if not await self._send_text(plan, block, text, keyboard=""):
                        content_sent = False
                content_attempted = True
                if not await self._send_record(plan, block):
                    content_sent = False
                continue

            if block.sticker:
                if await self._send_sticker(plan, block):
                    decoration_sent = True
                continue

            # 文本块（可含 emoji + at + reply + keyboard）
            text = self._compose_text(block)
            if not text and block.keyboard and plan.target_type != "group":
                # 官方按钮只有群聊承载：私聊的 keyboard-only 块无处安放，
                # 必须按未投递处理——与 ark 同一类判据，不能"什么都没发"
                # 却清掉未投递标、把 mention 记进 scoped 提取。
                content_attempted = True
                content_sent = False
                self.plugin.logger.warning(
                    "keyboard-only 块不支持私聊投递，未发送（记忆按未投递处理）"
                )
                continue
            if not text and block.keyboard and plan.target_type == "group":
                # keyboard-only 块必须真发出点什么，否则既没送出去又被算成
                # 已投递。官方按钮只有开放平台能渲染（NapCat/OneBot 协议
                # 无此字段，其 send_group_message_segments 收下 keyword 但
                # 不读）——NapCat 侧把按钮文案降级成可读文本，别只发一个
                # 空格。
                content_attempted = True
                labels = " / ".join(
                    part.strip()
                    for part in str(block.keyboard).split("|")
                    if part.strip()
                ) or str(block.keyboard)
                if self._supports_keyboard():
                    # 内容不能是空白：开放平台 sender 会 strip 后判空直接
                    # 返回 None，连带按钮 payload 都不构造——既没按钮也没
                    # 文本，还被当成发送失败。用选项文案当正文。
                    sent = await self.plugin.qq_client.send_group_message_segments(
                        plan.target_id,
                        [{"type": "text", "data": {"text": labels}}],
                        keyboard=block.keyboard,
                    )
                else:
                    sent = await self.plugin.qq_client.send_group_message(
                        plan.target_id, labels,
                    )
                if not self._confirm_platform_result(sent):
                    content_sent = False
                continue
            if not text:
                if block.ark:
                    # Ark 卡片目前没有投递实现（_send_ark 自 #2429 起无
                    # 调用方，属本 PR 之外的既有缺陷）：这里只保证记忆侧
                    # 不把"什么都没发"记成已投递——草稿保持排除、不记
                    # mention。真正的卡片发送要另行接回。
                    content_attempted = True
                    content_sent = False
                    self.plugin.logger.warning(
                        "Ark 卡片块没有投递实现，未发送（记忆按未投递处理）"
                    )
                continue
            if i == 0:
                first_text = text
            content_attempted = True
            if not await self._send_text(plan, block, text, keyboard=block.keyboard):
                content_sent = False

        # 开放平台单条发送失败返回 None（不抛异常）：只要有正文块未确认
        # 就不得报 delivered=True——buffer 会据此清未投递标并记 mention，
        # 而排除名单是整行粒度的，部分未发出的内容也会进 scoped 提取。
        delivered = content_sent if content_attempted else decoration_sent
        # 发送成功**必须留一行文件日志**（2026-09-27 真机验收的缺口）：
        # 这条路径原来只在失败/跳过时写日志，成功时只进 `_emit_log` 的内存环形
        # 缓冲（UI 面板）——于是"她今天到底发出去几条"在日志里查不到，
        # 只能靠"生成了几轮"间接推断。
        if delivered:
            try:
                self.plugin.logger.info(
                    f"[Send] {plan.target_type} {plan.target_id} 已发送"
                    f"（{self._describe_blocks(blocks)}, blocks={len(blocks)}"
                    f"{', 问句结尾' if self._ends_with_question(blocks) else ''}）"
                )
            except Exception:  # noqa: BLE001 —— 日志失败不该影响投递结论
                pass
        else:
            # **未投递那一半**（与上面那条对称，2026-09-27 补）。
            #
            # 缺口实证：14:41:48 群 1048307485 生成了一条 24 字回复，日志里
            # **一条投递痕迹都没有** —— 没有 `已发送`（对，它确实没发出去），
            # 也没有任何"为什么没发"的记录。`delivered = content_sent if
            # content_attempted else decoration_sent` 为 False 时此前是完全静默的：
            # 用户看到的是"她生成了却不说话"，而日志里查不到原因。
            # 这一行把三个分量都写出来，下次直接能判是哪一类：
            #   有正文块=False        → 计划里根本没有正文（纯装饰/被过滤光）
            #   正文确认=False        → 真发了但平台没确认（NapCat 超时/开放平台吞异常）
            #   装饰=False            → poke/表情包没发出去（冷却期等）
            try:
                self.plugin.logger.warning(
                    f"[Send] {plan.target_type} {plan.target_id} **未投递**"
                    f"（blocks={len(blocks)}, 有正文块={content_attempted}, "
                    f"正文确认={content_sent}, 装饰={decoration_sent}）"
                )
            except Exception:  # noqa: BLE001
                pass
        return QQDeliveryResult(
            delivered=delivered,
            target_type=plan.target_type,
            target_id=plan.target_id,
            reply_text=first_text,
        )

    @staticmethod
    def _compose_text(block: QQMessageBlock) -> str:
        """组合文字 + emoji + at + reply 为最终文本。

        **只有修饰（``<reply>`` / ``<at>``）而没有正文时返回空串**（2026-09-27）。
        否则这一段会被当成"有东西可发"：composed 出来是
        ``"[CQ:reply,id=…]"`` 这种非空字符串，``if not text`` 拦不住它，
        于是 QQ 上出现一个**只有引用块、没有正文的空消息**（使用者实测截图）。
        判据与 ``reply_postprocess_node.block_has_content`` 必须一致 ——
        两处漂移的代价已经在 ``<msg></msg>`` 上付过一次了。
        """
        import re as _re

        body = str(getattr(block, "text", "") or "").strip()
        face = str(getattr(block, "emoji", "") or "").strip()
        if not body and not face:
            return ""
        parts: list[str] = []
        if block.reply_to:
            parts.append(f"[CQ:reply,id={block.reply_to}]")
        if block.at_user:
            parts.append(f"[CQ:at,qq={block.at_user}]")
        if block.text:
            # 兜底：清除 prompt 模板 XML 标签残留（模型偶有输出未进 msg 包裹的裸标签，
            # 后处理器移除后仍可能因格式变体留下漏网之鱼）
            #
            # `ark` 也在这里，而且它是**内容泄漏**而不是内容丢失：Ark 卡片没有投递
            # 实现（下面 `if block.ark:` 分支只记一条 warning），于是块外/旧式写法里的
            # `<ark title="…" desc="…">正文</ark>` 会原样发给用户。开放平台格式段明文
            # 教模型用这个标签，用户就会在群里看到一整段 XML。剥掉标签壳、留下正文
            # 是这里唯一合理的降级。
            clean = block.text
            clean = _re.sub(r"</?(?:reply|msg|at|poke|sticker|record|keyboard|text|emoji|think|feeling|forward|mark|ark)(?:\s[^>]*)?\s*/?>", "", clean, flags=_re.IGNORECASE)
            parts.append(clean.strip())
        if block.emoji:
            parts.append(f"[CQ:face,id={block.emoji}]")
        return "".join(parts)

    async def _send_text(
        self, plan: QQDeliveryPlan, block: QQMessageBlock, text: str,
        *, keyboard: str = "",
    ) -> bool:
        """Returns True only when the send came back confirmed.

        Both platforms report one: the Open Platform client returns the
        message id (None when it swallowed a failure), and the NapCat
        client returns the id from the echo round-trip (None on timeout).
        None means the message was not confirmed delivered."""
        if not text:
            return False
        # 出站守门（内容安全 + 重复过滤）：**群发**的最后一道闸，语音分支也算
        # （它发的是同一段文本的 TTS）。被拦 = 当作未投递，不写 mention、不清
        # 未投递标 —— 与"她本来就没说"在记账上一致。
        if plan.target_type == "group":
            guard = getattr(self.plugin, "outbound_guard_service", None)
            if guard is not None:
                verdict = guard.check(group_id=plan.target_id, text=text)
                if verdict.blocked:
                    reason = f"内容安全：命中黑名单词 {verdict.detail!r}" if verdict.reason == "blacklist" \
                        else f"重复过滤：{verdict.detail}"
                    self.plugin.logger.warning(f"出站内容被拦截（{reason}）: {text[:40]!r}")
                    self.plugin._emit_log("WARN", f"[Outbound] 拦截（{reason}）: {text[:20]}")
                    return False
        mode = self.plugin._get_reply_mode()
        if mode == "voice":
            # voice-only 模式：走 TTS 发送语音——确认结果一路传播（开放
            # 平台失败吞异常返回 None，语音回复也不得凭空算已投递）。
            # 按钮无法在语音里交互，但选项文案要念出来，否则用户听到的
            # 回复缺了它在问的那几个选项。
            voice_text = text
            if keyboard:
                labels = " / ".join(
                    part.strip() for part in str(keyboard).split("|")
                    if part.strip()
                )
                if labels:
                    voice_text = voice_text + "\n" + labels
            if plan.target_type == "group":
                return bool(await self.plugin._deliver_group_reply(plan.target_id, voice_text, fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure))
            return bool(await self.plugin._deliver_private_reply(plan.target_id, voice_text, fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure))
        if plan.target_type != "group" and keyboard:
            # 官方按钮只有群聊承载：私聊带 keyboard 的文本块若原样发出，
            # "想看哪个？" 会到达用户手里却一个选项都没有。和 NapCat 群
            # 路径同样处理——把选项文案降级成可读正文。
            labels = " / ".join(
                part.strip() for part in str(keyboard).split("|")
                if part.strip()
            )
            if labels:
                text = text + "\n" + labels
        if plan.target_type == "group":
            if keyboard and not self._supports_keyboard():
                # NapCat 渲染不了官方按钮：把选项文案追加进正文，别让
                # "要看看哪个？<keyboard>A|B|C</keyboard>" 变成一句没有
                # 任何可选项的话。
                labels = " / ".join(
                    part.strip() for part in str(keyboard).split("|")
                    if part.strip()
                )
                if labels:
                    text = text + "\n" + labels
            if keyboard and self._supports_keyboard():
                # keyboard 只有开放平台的 segments 接口承载：带按钮的文本
                # 块走它。NapCat 不支持按钮，走普通文本（内容照发，按钮
                # 能力缺失是协议限制，不是静默丢弃逻辑）。
                result = await self.plugin.qq_client.send_group_message_segments(
                    plan.target_id,
                    [{"type": "text", "data": {"text": text}}],
                    keyboard=keyboard,
                )
            else:
                result = await self.plugin.qq_client.send_group_message(plan.target_id, text)
        else:
            result = await self.plugin.qq_client.send_message(plan.target_id, text)
        # 两个平台的文本发送现在都有回执：开放平台失败吞异常返回 None，
        # NapCat 走 echo 往返（超时返回 None）。显式 None = 未确认送达。
        return result is not None

    @staticmethod
    def _ends_with_question(blocks: list[QQMessageBlock]) -> bool:
        """这条回复是不是**以问句收尾**（用于把"问句率"变成日志里可数的数字）。

        由来（2026-09-27）：使用者问「群里有人说了她不知道的东西，她很容易去询问为什么」。
        查下来根因是提示词里没人管"要不要用提问回应"，于是给她加了问句预算
        （`DETAIL_CONSTRAINTS_SECTION`）。**加了约束就必须能验证** ——
        宿主记忆库里只有私聊/本体对话（实测 1888 条她的发言里 43% 以问号结尾），
        群聊里她的话**不落盘**，所以群侧的量只能在这里留痕：
        `grep -c '问句结尾' / grep -c '已发送'` 就是群聊的问句率。
        """
        text = "".join(str(getattr(b, "text", "") or "").strip() for b in blocks)
        if not text:
            return False
        import re as _re
        stripped = _re.sub(r"<[^>]+>", "", text).strip()
        return bool(stripped) and stripped[-1] in "？?"

    @staticmethod
    def _describe_blocks(blocks: list[QQMessageBlock]) -> str:
        """把"这条到底发了什么"写成日志里的一小段（`3 字` / `语音` / `表情/戳一戳`）。

        为什么不用 `len(first_text)`：真机 15:00:54 出现过
        `[Send] group 1048307485 已发送（0 字, blocks=1）` —— 读日志的人会以为她发了
        **一条空消息**，而实际是语音（`reply_mode=both` 时会走 record 块）或表情/poke
        这类**没有正文**的投递。以「首块正文字数」当摘要，遇到"首块是装饰、正文在后面"
        或"压根没有正文"都会误报成 0 字。这里按块里**真正有什么**逐类列出来。
        """
        text_chars = sum(len(str(getattr(b, "text", "") or "").strip()) for b in blocks)
        kinds: list[str] = []
        if text_chars:
            kinds.append(f"{text_chars} 字")
        if any(getattr(b, "record", None) for b in blocks):
            kinds.append("语音")
        if any(getattr(b, "sticker", None) for b in blocks):
            kinds.append("表情")
        if any(getattr(b, "poke", None) for b in blocks):
            kinds.append("戳一戳")
        if any(getattr(b, "emoji", None) for b in blocks):
            kinds.append("表情回应")
        if any(getattr(b, "ark", None) for b in blocks):
            kinds.append("卡片")
        if any(getattr(b, "keyboard", None) for b in blocks):
            kinds.append("按钮")
        return "/".join(kinds) or "无内容"

    async def _send_sticker(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> bool:
        """表情包投递。**两条通道走法不同，不能合并**：

        * **开放平台**：一律走 `media_seam` —— 它优先用**连接对象自带的宿主适配器**
          （`upload_image` / `send_private_image`，群图走 `send_group_image`），宿主还没带
          那个 mixin 时才回退内置副本。判据是能力（`upload_image`），不是类名或版本号。
          这条路当初必须自己实现，是因为宿主那份连接器
          ① 没有单聊富媒体方法（插件改不了宿主的文件）；
          ② 群聊图只实现旧式直传，而 2026-09-26/27 真机实测证明旧式直传在开放平台已经失效
          （「图片直传上传未拿到 file_info」→ 分片才成功）—— 不自己发，群聊表情包只会静默
          降级成「[图片]」三个字。宿主新包把这两件事都补上了，插件这边只留"优先用它的"。
        * **OneBot（NapCat 等）**：`send_group_image` / `send_private_msg` 本来就认
          image 段，用现成接口。私聊刻意**不**走连接器上那个 `send_private_image()`：
          它固定 `record_sent=True`，而表情包这条一直是 `record_sent=False` ——
          "她发出去的消息 id 缓存"里不该多出表情包。

        私聊以前第一行就是 `return False`（表情包是装饰，不发就不发）；"群里能发、私聊
        静默不发"并不是谁定的规矩，只是这条路没写。
        """
        sticker_path = self.plugin._resolve_sticker_path(block.sticker)
        if not sticker_path:
            return False
        client = self.plugin.qq_client
        is_open_platform = media_seam.is_open_platform(client)

        if plan.target_type == "group":
            if is_open_platform:
                return self._confirm_platform_result(
                    await media_seam.send_group_image(
                        client, plan.target_id, sticker_path,
                    ),
                )
            return self._confirm_platform_result(
                await client.send_group_image(
                    plan.target_id, sticker_path, sub_type="1",
                ),
            )

        if is_open_platform:
            message_id = await media_seam.send_private_image(
                client, plan.target_id, sticker_path, record_sent=False,
            )
            return self._confirm_platform_result(message_id)
        return self._confirm_platform_result(
            await client.send_private_message_segments(
                plan.target_id, [{"type": "image", "data": {"file": sticker_path}}],
                record_sent=False,
            ),
        )

    async def _send_poke(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> bool:
        """Returns True only when a poke actually reached the group.

        A skipped poke (private target, cooldown) reports False like a
        failed one — pokes are decoration, so deliver() only consults this
        when the plan carries no text at all."""
        if plan.target_type != "group" or not block.poke:
            return False
        # 冷却：同一群每 30 秒最多戳一次，避免刷屏
        now = __import__("time").time()
        key = f"poke_out:{plan.target_id}"
        last = getattr(self, "_last_poke_out", {}).get(key, 0)
        if now - last < 30:
            self.plugin._emit_log("INFO", f"戳一戳冷却中，跳过 (群{plan.target_id})")
            return False
        if not hasattr(self, "_last_poke_out"):
            self._last_poke_out = {}
        self._last_poke_out[key] = now
        return self._confirm_platform_result(
            await self.plugin.qq_client.send_group_poke(plan.target_id, block.poke),
        )

    @staticmethod
    def build_forward_nodes(
        messages: list[dict[str, Any]], *, summary: str = "",
        bot_name: str = "", bot_uin: str = "",
    ) -> list[dict[str, Any]]:
        """把「标记之后的多人多句」组装成合并转发节点。

        节点格式是 OneBot v11 的 `node`：每个节点带 `name`/`uin`（显示成谁说的）
        和 `content`（消息段数组）。**第一句是模型写的那句总结**，用机器人自己的
        身份发（提示词：「写一句总结，系统自动附上标记之后的对话原文」）。
        """
        nodes: list[dict[str, Any]] = []

        def _node(name: str, uin: str, text: str) -> dict[str, Any]:
            return {
                "type": "node",
                "data": {
                    "name": str(name or "群友"),
                    "uin": str(uin or "0"),
                    "content": [{"type": "text", "data": {"text": str(text)}}],
                },
            }

        text = str(summary or "").strip()
        if text:
            nodes.append(_node(bot_name or "猫娘", bot_uin or "0", text))
        for item in messages or []:
            body = str(item.get("text") or item.get("message_text") or "").strip()
            if not body:
                continue
            nodes.append(
                _node(
                    backlog_sender_label(item),
                    str(item.get("sender_id") or "0"),
                    body,
                )
            )
        return nodes

    async def send_forward(
        self, *, target_type: str, target_id: str, nodes: list[dict[str, Any]],
    ) -> bool:
        """发一条合并转发。仅 NapCat 支持（开放平台是返回 `{}` 的空桩，判成未确认）。"""
        target_id = str(target_id or "").strip()
        if not target_id or not nodes:
            self.plugin.logger.info("合并转发缺少目标或内容，跳过")
            return False
        client = self.plugin.qq_client
        if not client:
            return False
        try:
            if target_type == "group":
                result = await client.send_group_forward_msg(target_id, nodes)
            else:
                result = await client.send_private_forward_msg(target_id, nodes)
        except Exception:
            self.plugin.logger.warning("合并转发发送失败", exc_info=True)
            return False
        if not self._confirm_platform_result(result):
            self.plugin.logger.info(f"合并转发未被确认（target={target_id}）")
            return False
        return True

    def _supports_keyboard(self) -> bool:
        """Only the Open Platform renders official keyboard buttons.

        NapCat/OneBot has no such field — its send_group_message_segments
        accepts the kwarg for interface parity but never reads it."""
        client = self.plugin.qq_client
        return bool(client and not client.needs_attention)

    async def send_emoji_reaction(self, message_id: str, emoji_id: str) -> bool:
        """给某条消息**贴表情**（反应），不是把表情当消息发出去。

        这是 `<emoji>` 块外标签的唯一正确投递方式：提示词承诺的就是「对消息的表情回复
        （贴表情到对方消息上）」。以前这个字段解析出来却**没有任何消费方**，模型照提示词
        做也零效果。

        仅 NapCat 支持：开放平台客户端里 `set_msg_emoji_like` 是返回 `{}` 的空桩，
        `{}` 是假值，所以这里天然会被判成未确认 —— 不需要额外分支，但也不能因为
        它而让整轮回复失败（反应是装饰，失败只降级成日志）。
        """
        message_id = str(message_id or "").strip()
        emoji_id = str(emoji_id or "").strip()
        if not message_id or not emoji_id:
            # 合成轮（回溯补回/破冰）没有对应的具体消息，没有可以贴的对象。
            self.plugin.logger.info("表情反应缺少目标消息 id，跳过")
            return False
        client = self.plugin.qq_client
        if not client or not hasattr(client, "set_msg_emoji_like"):
            self.plugin.logger.info("当前连接不支持表情反应，跳过")
            return False
        try:
            result = await client.set_msg_emoji_like(message_id, emoji_id)
        except Exception:
            self.plugin.logger.warning("表情反应发送失败", exc_info=True)
            return False
        if not self._confirm_platform_result(result):
            self.plugin.logger.info(
                f"表情反应未被确认（message_id={message_id} emoji={emoji_id}）",
            )
            return False
        return True

    @staticmethod
    def _confirm_platform_result(result) -> bool:
        """Falsy result == the send was never confirmed.

        Every sender now reports one: the Open Platform returns the message
        id (None when it swallowed a failure), and the NapCat client does an
        echo round-trip for messages / returns a bool for pokes. Keeping a
        per-call "does this one have a receipt?" flag is what let text
        fallbacks silently count as delivered."""
        return bool(result)

    async def _send_record(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> bool:
        if not block.record:
            return False
        try:
            file_uri, _ = await self.plugin.voice_reply_service.synthesize_reply_voice_file(block.record)
            if plan.target_type == "group":
                result = await self.plugin.qq_client.send_group_record(plan.target_id, file_uri)
            else:
                result = await self.plugin.qq_client.send_private_record(plan.target_id, file_uri)
            if self._confirm_platform_result(result):
                # 语音走 segments 接口（群/私聊都带 echo 回执）：超时返回
                # None 是"没确认送达"，不能当 fire-and-forget 放行，否则
                # 用户没听到的回复既不回退文本、还被记成已投递。
                return True
            if plan.fallback_to_text_on_voice_failure:
                # 未确认（开放平台吞异常返回 None）与异常同等对待：按请求
                # 回退文本，而不是直接判未投递。
                if plan.target_type == "group":
                    fb = await self.plugin.qq_client.send_group_message(plan.target_id, block.record)
                else:
                    fb = await self.plugin.qq_client.send_message(plan.target_id, block.record)
                return self._confirm_platform_result(fb)
            return False
        except Exception as exc:
            # 语音发不出去**多数是配置性的**（角色没配音色、线路不认识这个音色、本地那条
            # ws 没端点）—— 这类原因一句话就够，整段 traceback 只会淹掉它；真正的意外
            # （不是 RuntimeError）照旧带栈，免得把 bug 也降噪掉。
            if isinstance(exc, RuntimeError):
                self.plugin.logger.warning(f"语音未发送：{exc}")
            else:
                self.plugin.logger.warning("语音发送失败", exc_info=True)
            if plan.fallback_to_text_on_voice_failure and block.record:
                text = block.record
                if plan.target_type == "group":
                    result = await self.plugin.qq_client.send_group_message(plan.target_id, text)
                else:
                    result = await self.plugin.qq_client.send_message(plan.target_id, text)
                if self._confirm_platform_result(result):
                    # 这一行是为了对上 `[Send] … 已发送（语音）`：那个标签按**块里有什么**
                    # 命名，语音回退成文字时不会变，不说明一句读日志的人会以为语音发出去了。
                    self.plugin.logger.info("语音这条已改发文字（上面那条是原因）")
                    return True
                return False
            return False
