from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .pipeline_models import QQDeliveryPlan, QQMessageBlock, QQModelResult, QQReplyContext, QQReplyOutcome


class QQReplyPostprocessNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    @staticmethod
    def _clean_dynamic_prefix(text: str) -> str:
        """Remove the trailing opening fence from a literal prefix."""
        import re as _re

        # 前缀末尾的 opening fence 要拿掉，否则 ```xml 会被当成可见的 pre-tool 文本。
        cleaned = _re.sub(
            r"```(?:xml)?\s*$", "", text, flags=_re.IGNORECASE,
        )
        return cleaned.strip()

    @staticmethod
    def _split_dynamic_xml(text: str) -> tuple[str, str] | None:
        """Separate literal assistant text from the dynamic XML document."""
        import re as _re

        msg_start = _re.search(r"<msg(?:\s|>)", text)
        if msg_start is None:
            return None
        leading_raw = text[:msg_start.start()]
        xml_text = _re.sub(r"\s*```\s*$", "", text[msg_start.start():])
        return QQReplyPostprocessNode._clean_dynamic_prefix(leading_raw), xml_text

    @staticmethod
    def _parse_blocks(raw_text: str) -> list[QQMessageBlock]:
        """`<msg>` 块解析器。将 LLM 输出解析为消息块列表。

        容器形态（`<msg>` 挂子标签、块外内容不作为消息）借自 KiraAI；**标签集合是本插件
        自己的**：上游还有 `img/video/selfie/file` 与操作 message_id 的 `forward`，我们有
        `keyboard/ark`、块外 `<emoji>`、`<mark/>`、`<feeling>`、`<forward to=…>`；并且上游
        明令禁止 `<msg>` 内裸文本，我们显式接受（见 `_parse_blocks` 的裸文本分支）。

        支持格式:
          <msg><text>文字</text><emoji>277</emoji></msg>
          <msg><sticker>5</sticker></msg>
          <msg><poke>123456</poke></msg>
          <msg><record>语音文本</record></msg>
        纯文本（无 <msg> 标签）回退为单块纯文本。
        """
        text = (raw_text or "").strip()
        if not text:
            return []

        # 检查是否包含 <msg> 标签 → XML 解析
        if "<msg>" not in text and "<msg " not in text:
            # 检查是否使用旧式标签（<reply> <at> <sticker> <poke> <record> <keyboard>）
            if any(tag in text for tag in ("<reply>", "<at>", "<sticker>", "<poke>", "<record>", "<keyboard>")):
                return QQReplyPostprocessNode._parse_legacy_tags(text)
            # 纯文本当作一个块
            block = QQMessageBlock(text=text)
            return [block] if text else []

        # pre-tool 是 XML 文档外的普通文本，必须先切出来；若把它一起交给
        # ElementTree，模型自然输出的未转义 < / & 会让整个结构降级失败。
        import re as _re
        dynamic_parts = QQReplyPostprocessNode._split_dynamic_xml(text)
        if dynamic_parts is None:
            return [QQMessageBlock(text=text)]
        leading_text, xml_text = dynamic_parts

        # XML 解析
        try:
            root = ET.fromstring(f"<root>{xml_text}</root>")
        except ET.ParseError:
            # 解析失败 → 回退纯文本（去除 XML 标签）
            clean = _re.sub(r"<[^>]+>", "", xml_text).strip()
            blocks = []
            if leading_text:
                blocks.append(QQMessageBlock(text=leading_text))
            if clean:
                blocks.append(QQMessageBlock(text=clean))
            return blocks or [QQMessageBlock(text=text)]

        blocks: list[QQMessageBlock] = []
        if leading_text:
            blocks.append(QQMessageBlock(text=leading_text))

        for msg_el in root.findall("msg"):
            block = QQMessageBlock()

            # 裸文本：`<msg>文字</msg>` 的 `msg_el.text`，以及**每个子元素后面的 tail**
            # ——`<msg><reply>1252066434</reply>你好</msg>` 里的"你好"就是 `reply` 的
            # tail。旧写法只收 `msg_el.text`，于是这一整句正文被丢掉、块里只剩
            # `reply_to`，投递层拼出一个**只有引用、没有正文的空消息**（使用者实测
            # 截图就是它）。tail 必须一起收，否则"引用 + 正文"的常见写法必然丢正文。
            loose_parts: list[str] = []
            if msg_el.text and msg_el.text.strip():
                loose_parts.append(msg_el.text.strip())
            for child in msg_el:
                if child.tail and child.tail.strip():
                    loose_parts.append(child.tail.strip())

            # <text>
            text_el = msg_el.find("text")
            if text_el is not None and text_el.text:
                block.text = text_el.text.strip()

            # <emoji>
            emoji_el = msg_el.find("emoji")
            if emoji_el is not None and emoji_el.text:
                block.emoji = emoji_el.text.strip()

            # <at>
            at_el = msg_el.find("at")
            if at_el is not None and at_el.text:
                block.at_user = at_el.text.strip()

            # <reply>
            reply_el = msg_el.find("reply")
            if reply_el is not None and reply_el.text:
                block.reply_to = reply_el.text.strip()

            # <sticker>
            sticker_el = msg_el.find("sticker")
            if sticker_el is not None and sticker_el.text:
                block.sticker = sticker_el.text.strip()

            # <poke>
            poke_el = msg_el.find("poke")
            if poke_el is not None and poke_el.text:
                block.poke = poke_el.text.strip()

            # <record>
            record_el = msg_el.find("record")
            if record_el is not None and record_el.text:
                block.record = record_el.text.strip()

            # <keyboard>
            kb_el = msg_el.find("keyboard")
            if kb_el is not None and kb_el.text:
                block.keyboard = QQReplyPostprocessNode._normalize_keyboard(
                    kb_el.text
                )

            # <ark> with attrs
            ark_el = msg_el.find("ark")
            if ark_el is not None:
                for k, v in ark_el.attrib.items():
                    block.ark[k] = str(v)
                if ark_el.text:
                    block.ark["_body"] = ark_el.text.strip()

            # 裸文本并进正文（`<msg>text</msg>`、`<msg><reply>id</reply>text</msg>`、
            # `<msg><emoji>277</emoji>text</msg>` 都走这里）。
            #
            # 旧写法只在"块里完全没内容"时才用裸文本，于是
            # `<msg><emoji>277</emoji>裸文本</msg>` 会把那段文字丢掉 —— 判据本身
            # 也把 `reply_to` 当内容，两个小毛病叠一起就是"发了条空引用"。
            loose = "".join(loose_parts).strip()
            if loose:
                block.text = f"{block.text}{loose}" if block.text else loose

            blocks.append(block)

        # 空块列表 → 回退
        if not blocks:
            return [QQMessageBlock(text=text)]

        return blocks

    @staticmethod
    def block_has_content(block: QQMessageBlock) -> bool:
        """这个块里有没有**用户能看到的东西**。

        与投递层同口径：``reply_delivery_node._compose_text`` 把 text/emoji/at/reply
        组合成最终文本，poke/sticker/record/keyboard/ark 各有分支。任何一项非空都
        意味着"发得出去"。

        抽出来是因为这个判断以前散在两处（``_parse_blocks`` 的裸 ``<msg>`` 回退、
        投递分支），而**漏掉它的代价已经真实发生过**：``<msg></msg>`` 解析出一个
        全空块，投递层正确地跳过了它（什么都不发），但 ``outcome.reply_text`` 仍是
        ``"<msg></msg>"`` 这个真值字符串 —— 于是所有
        ``outcome.action == "reply" and outcome.reply_text`` 的调用点全部误判：
        注意力被当成"已回复"扣了一次、用户消息被标成已读、回溯/破冰记成"成功"。

        **2026-09-27 收紧**：``reply_to`` 与 ``at_user`` **不算内容** —— 它们是
        「修饰」（引用谁、@谁），必须依附在一句话上；只有它们、没有正文时，
        QQ 那边渲染出来是**一个只有引用块、没有正文的空消息**（使用者实测截图：
        引用条 + 空白正文）。原来的判据把 ``reply_to`` 当内容，于是
        ``<msg><reply>1252066434</reply></msg>`` 一路被当成真回复发出去，
        而它表达的意思恰恰是"我不想说话"。
        """
        return bool(
            str(getattr(block, "text", "") or "").strip()
            or str(getattr(block, "emoji", "") or "").strip()
            or str(getattr(block, "sticker", "") or "").strip()
            or str(getattr(block, "poke", "") or "").strip()
            or str(getattr(block, "record", "") or "").strip()
            or str(getattr(block, "keyboard", "") or "").strip()
            or getattr(block, "ark", None)
        )

    @staticmethod
    def _normalize_keyboard(raw: str | None) -> str:
        """Trim, drop empties, and cap at what the platform renders (4).

        Normalizing once at parse time keeps delivery and bookkeeping on
        the same value — the Open Platform sender builds at most four
        buttons, so a fifth option would be recorded but never shown."""
        options = [
            part.strip() for part in str(raw or "").split("|") if part.strip()
        ]
        return "|".join(options[:4])

    @classmethod
    def _parse_legacy_tags(cls, raw_text: str) -> list[QQMessageBlock]:
        """向后兼容：解析旧式散落标签（<reply> <at> <sticker> <poke> <record> <keyboard>）。
        将其转换为单个 QQMessageBlock（含标签属性）+ 可能的 sticker/poke 独立块。
        """
        import re
        text = raw_text

        reply_id = ""
        at_id = ""
        poke_user = ""
        sticker_id = ""
        voice_text = ""
        keyboard = ""

        m = re.search(r"<reply>(.*?)</reply>", text, re.IGNORECASE)
        if m:
            reply_id = m.group(1).strip()
            text = re.sub(r"<reply>.*?</reply>", "", text, flags=re.IGNORECASE)

        m = re.search(r"<at>(.*?)</at>", text, re.IGNORECASE)
        if m:
            at_id = m.group(1).strip()
            text = re.sub(r"<at>.*?</at>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<poke>(.*?)</poke>", text, re.IGNORECASE)
        if m:
            poke_user = m.group(1).strip()
            text = re.sub(r"<poke>.*?</poke>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<sticker>(.*?)</sticker>", text, re.IGNORECASE)
        if m:
            sticker_id = m.group(1).strip()
            text = re.sub(r"<sticker>.*?</sticker>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<record>(.*?)</record>", text, re.IGNORECASE)
        if m:
            voice_text = m.group(1).strip()
            text = re.sub(r"<record>.*?</record>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<keyboard>(.*?)</keyboard>", text, re.IGNORECASE)
        if m:
            # 旧式标签走同一个归一化：否则老输入能留下第五个按钮或空选项，
            # 而发送端只投前四个——投递与记忆记录又对不上了。
            keyboard = cls._normalize_keyboard(m.group(1))
            text = re.sub(r"<keyboard>.*?</keyboard>", "", text, count=1, flags=re.IGNORECASE)

        clean = text.strip()
        blocks: list[QQMessageBlock] = []

        if poke_user:
            blocks.append(QQMessageBlock(poke=poke_user))
        if clean or reply_id or at_id or voice_text or keyboard:
            blocks.append(QQMessageBlock(
                text=clean, reply_to=reply_id, at_user=at_id,
                record=voice_text, keyboard=keyboard,
            ))
        if sticker_id:
            blocks.append(QQMessageBlock(sticker=sticker_id))
        return blocks if blocks else [QQMessageBlock(text=raw_text)]

    async def _repair_xml(self, broken: str) -> str | None:
        """用 LLM 修复格式错误的 XML 输出（30秒超时，失败则放弃）。

        **必须带本体人设**：这条请求是自己拼的消息，免费端（lanlan）只认带人设的
        请求，不带就一律 400 —— 而这个方法是 `except Exception: pass` 的"尽力而为"
        路径，静默失败等于功能不存在（见 docs/SESSION-HANDOFF.md §4.0t）。
        """
        import asyncio
        try:
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            model_config = get_config_manager().get_model_api_config("conversation")
            if not model_config.get("base_url") or not model_config.get("model"):
                return None
            llm = await create_chat_llm_async(
                model=str(model_config["model"]),
                base_url=str(model_config["base_url"]),
                api_key=str(model_config.get("api_key", "")),
                max_completion_tokens=500,
                timeout=15.0,
                provider_type=model_config.get("provider_type"),
            )
            prompt = (
                "以下是一段格式错误的 XML，请修复它使其成为合法的 XML，不要改变任何内容和标签：\n\n"
                f"{broken}\n\n只返回修复后的 XML。"
            )
            messages: list[dict[str, Any]] = []
            free_system = self.plugin._free_route_system_prompt(model_config)
            if free_system:
                messages.append({"role": "system", "content": free_system})
            messages.append({"role": "user", "content": prompt})
            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=15.0,
            )
            fixed = str(getattr(response, "content", "") or "").strip()
            if fixed and ("<msg>" in fixed or "<msg " in fixed):
                self.plugin._emit_log("INFO", "[XML修复] 成功修复格式错误")
                return fixed
        except Exception:
            pass
        return None

    async def finalize(self, context: QQReplyContext, model_result: QQModelResult) -> QQReplyOutcome:
        raw_reply_text = model_result.reply_text or ""
        reply_text = self.plugin._sanitize_generated_reply(raw_reply_text)
        known_pre_tool = str(
            getattr(model_result, "pre_tool_text", "") or ""
        )
        structural_pre_tool = (
            known_pre_tool
            if known_pre_tool and reply_text.startswith(known_pre_tool)
            else ""
        )
        # 真实 tool 边界之后的最终段：buffer 用它判断"是否空回复"、并作为汇总的输入。
        post_tool_text = (
            reply_text[len(structural_pre_tool):]
            if structural_pre_tool
            else reply_text
        )
        if raw_reply_text and not reply_text:
            self.plugin._emit_log("INFO", f"[Sanitize] {len(raw_reply_text)}字被清除: {raw_reply_text[:100]}")

        blocks: list[QQMessageBlock] = []
        emoji_reaction_id = ""
        feeling = ""
        forward_content = ""
        forward_target = ""
        forward_count = 0
        mark_flag = False

        # 只要提示词教了标签就解析 —— 模式合并后这一点结构上成立：唯一策略
        # （neko_dynamic）与开放平台都教标签，neko_scene 这个「教了标签却不认」的
        # 组合已经不存在。
        if reply_text:
            import re
            # --- 提取独立标签（仅限 <msg> 之外的标签，不碰块内内容）---
            # 计算 <msg> 块区间，辅助判断标签是否在块外
            _msg_ranges = [(m.start(), m.end()) for m in re.finditer(r"<msg[\s>][\s\S]*?</msg>", reply_text, re.IGNORECASE)]
            def _outside_msg(pos: int) -> bool:
                return not any(start <= pos < end for start, end in _msg_ranges)
            # extract <emoji>ID</emoji> (standalone reaction tag)
            em = re.search(r"<emoji>(\d+)</emoji>", reply_text, re.IGNORECASE)
            if em and _outside_msg(em.start()):
                emoji_reaction_id = em.group(1).strip()
                reply_text = reply_text[:em.start()] + reply_text[em.end():]
                reply_text = reply_text.strip()
            # extract <mark/> (forward bookmark)
            mk = re.search(r"<mark\s*/>", reply_text, re.IGNORECASE)
            if mk and _outside_msg(mk.start()):
                reply_text = reply_text[:mk.start()] + reply_text[mk.end():]
                reply_text = reply_text.strip()
                mark_flag = True
            # extract <feeling>emotion</feeling> (mood tag)
            fm = re.search(r"<feeling>(\w+)</feeling>", reply_text, re.IGNORECASE)
            if fm and _outside_msg(fm.start()):
                feeling = fm.group(1).strip().lower()
                reply_text = reply_text[:fm.start()] + reply_text[fm.end():]
                reply_text = reply_text.strip()
            # extract <forward to="群号" count="30">content</forward>
            fw = re.search(r"<forward(\s+to\s*=\s*\"(\d+)\")?(\s+count\s*=\s*\"(\d+)\")?\s*>(.*?)</forward>", reply_text, re.DOTALL | re.IGNORECASE)
            if fw and _outside_msg(fw.start()):
                forward_content = fw.group(5).strip() if fw.group(5) else ""
                forward_target = fw.group(2) or ""
                forward_count = int(fw.group(4)) if fw.group(4) else 0
                reply_text = reply_text[:fw.start()] + reply_text[fw.end():]
                reply_text = reply_text.strip()

            # 刷新 post_tool_text：上面可能剥离了 <feeling>/<emoji>/<mark>/<forward>，
            # 用旧值会导致 buffer 把空 feeling 当成有待投递内容
            post_tool_text = reply_text

            # --- 处理 pre-tool 文本（core 在 tool-round start 捕获的模型文本）---
            explicit_prefix = ""
            parse_text = reply_text
            if structural_pre_tool:
                # 这是 core 在真实 tool-round start 捕获的模型文本，不是
                # dynamic XML 的格式前缀。完整 Markdown 围栏等字面内容都属于
                # 助手输出，不能再用启发式清理器裁剪。
                explicit_prefix = structural_pre_tool.strip()
                parse_text = reply_text[len(structural_pre_tool):]

            # --- XML 解析 ---
            dynamic_parts = self._split_dynamic_xml(parse_text)
            parse_failed = dynamic_parts is None and "</msg>" in parse_text
            broken_xml = parse_text if parse_failed else ""
            if dynamic_parts is not None:
                try:
                    ET.fromstring(f"<root>{dynamic_parts[1]}</root>")
                except ET.ParseError:
                    parse_failed = True
                    broken_xml = dynamic_parts[1]
            blocks = self._parse_blocks(parse_text)
            if explicit_prefix:
                blocks.insert(0, QQMessageBlock(text=explicit_prefix))
            # 解析失败必须显式进入修复；带 pre-tool 时 fallback 会产生两个
            # 纯文本块，不能再用 len(blocks) == 1 猜测解析状态。
            if parse_failed:
                leading_text = dynamic_parts[0] if dynamic_parts else ""
                repaired = await self._repair_xml(broken_xml)
                if repaired:
                    repaired_blocks = self._parse_blocks(repaired)
                    if repaired_blocks:
                        prefix_blocks = [
                            QQMessageBlock(text=value)
                            for value in (explicit_prefix, leading_text)
                            if value
                        ]
                        blocks = prefix_blocks + repaired_blocks
                        reply_text = repaired
            # 构建人类可读的 reply_text（首个块的文本）
            first_text = blocks[0].text if blocks else ""
            reply_text = first_text or reply_text

            # 全是空块（典型 `<msg></msg>`）⇒ 模型说了个空消息 = 它选择不说话。
            #
            # 必须在这里归零，不能留给投递层：投递层确实会跳过空块（什么都不发），
            # 但 `reply_text` 仍是 "<msg></msg>" 这个**真值字符串**，于是所有
            # `outcome.action == "reply" and outcome.reply_text` 的调用点全部误判 ——
            # 注意力被当成"已回复"扣一次、用户消息被标已读（再也不会被回溯补回）、
            # 回溯/破冰记成"成功"。实测见过：日志里
            # `[RetroReview] 回溯回复已发送: <msg></msg>`。
            #
            # 归零之后本方法自然走到下面的 `llm_skip` 分支（`reply_text=None`），
            # 语义正好是"模型被给了机会但没说话"，而且 `feeling` 照旧保留。
            if blocks and not any(
                QQReplyPostprocessNode.block_has_content(b) for b in blocks
            ):
                blocks = []
                reply_text = ""
            # 日志：LLM 使用的标签
            tags = []
            if emoji_reaction_id:
                tags.append(f"emoji={emoji_reaction_id}")
            if feeling:
                tags.append(f"feeling={feeling}")
            if forward_content:
                tags.append("forward")
            for b in (blocks or []):
                if b.reply_to:
                    tags.append(f"reply={b.reply_to}")
                if b.at_user:
                    tags.append(f"at={b.at_user}")
                if b.sticker:
                    tags.append(f"sticker={b.sticker}")
                if b.poke:
                    tags.append(f"poke={b.poke}")
                if b.record:
                    tags.append("record")
                if b.keyboard:
                    tags.append("keyboard")
            if tags:
                self.plugin._emit_log("INFO", f"[Tags] {' | '.join(tags)}")

        # `emoji_reaction_id` / `forward_content` 也算"有内容"：只贴一个表情、或只发一次
        # 合并转发，都是完全合理的回复（提示词把这两个都描述成独立的块外标签）。
        # 不算进来的话，这些输出会被判成 `llm_skip` —— 于是日志写成「决定不回复」
        # 而其实已经发了东西，注意力/频率的记账也与事实不符。
        if blocks or reply_text or emoji_reaction_id or forward_content:
            return QQReplyOutcome(
                action="reply",
                reply_text=reply_text,
                raw_reply_text=raw_reply_text,
                pre_tool_text=structural_pre_tool,
                post_tool_text=post_tool_text,
                postprocess_reason="reply_xml",
                blocks=blocks,
                used_fallback=bool(getattr(model_result, "used_fallback", False)),
                feeling=feeling,
                emoji_reaction_id=emoji_reaction_id,
                forward_content=forward_content,
                forward_target=forward_target,
                forward_count=forward_count,
                forward_mark=mark_flag,
            )
        if context.ephemeral_session:
            return QQReplyOutcome(
                action="reply",
                reply_text=None,
                raw_reply_text=raw_reply_text,
                pre_tool_text=structural_pre_tool,
                post_tool_text=post_tool_text,
                postprocess_reason="empty",
                used_fallback=bool(getattr(model_result, "used_fallback", False)),
                feeling=feeling,
                emoji_reaction_id=emoji_reaction_id,
                forward_content=forward_content,
                forward_target=forward_target,
                forward_count=forward_count,
                forward_mark=mark_flag,
            )
        is_forced = getattr(context, "force_reply", False) or context.permission_level == "admin"
        if not is_forced:
            return QQReplyOutcome(
                action="reply",
                reply_text=None,
                raw_reply_text=raw_reply_text,
                pre_tool_text=structural_pre_tool,
                post_tool_text=post_tool_text,
                postprocess_reason="llm_skip",
                used_fallback=bool(getattr(model_result, "used_fallback", False)),
                feeling=feeling,
                emoji_reaction_id=emoji_reaction_id,
                forward_content=forward_content,
                forward_target=forward_target,
                forward_count=forward_count,
                forward_mark=mark_flag,
            )
        # default/forced 回复同样没有本轮历史 ai 行：标记必须保留，
        # 否则 buffer 会把上一条已投递回复误记成未投递草稿。
        return QQReplyOutcome(
            action="reply",
            reply_text=self.plugin.i18n.t("messages.default_no_reply", default="嗯嗯~"),
            used_default_message=True,
            raw_reply_text=raw_reply_text,
            pre_tool_text=structural_pre_tool,
            post_tool_text=post_tool_text,
            postprocess_reason="default",
            used_fallback=bool(getattr(model_result, "used_fallback", False)),
            feeling=feeling,
            emoji_reaction_id=emoji_reaction_id,
            forward_content=forward_content,
            forward_target=forward_target,
            forward_count=forward_count,
        )

    def build_delivery_plan(self, request: Any, outcome: QQReplyOutcome) -> QQDeliveryPlan | None:
        if not outcome.blocks and not outcome.reply_text:
            return None
        # 如果没有 blocks（旧格式回退），用 reply_text 构造一个块
        blocks = outcome.blocks if outcome.blocks else [QQMessageBlock(text=outcome.reply_text or "")]
        if request.is_group:
            return QQDeliveryPlan(
                target_type="group",
                target_id=str(request.group_id or ""),
                blocks=blocks,
                fallback_to_text_on_voice_failure=request.fallback_to_text_on_voice_failure,
            )
        return QQDeliveryPlan(
            target_type="private",
            target_id=request.sender_id,
            blocks=blocks,
            fallback_to_text_on_voice_failure=request.fallback_to_text_on_voice_failure,
        )
