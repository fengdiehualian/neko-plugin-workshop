"""Plugin-side message enrichment: expand reply/forward/voice/file + VLM.

Moved out of the connector (``utils/connection/onebot``): enrichment is business/LLM
pre-processing, so it lives in the plugin. The connector only does transport +
normalization + send primitives; it exposes the *data* API (``get_msg``,
``get_forward_msg``, ``get_record``, ``get_*_file_url``, ``get_*_member_info``,
``get_stranger_info``) that this enricher calls to pull quoted / forwarded /
voice / file content.
"""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime as _dt
from pathlib import Path as _Path
from typing import Any, Dict

import httpx

from .message_chain import (
    At,
    Emoji,
    File,
    Forward,
    Image,
    JsonCard,
    MessageChain,
    Record,
    Reply,
    Text,
)

#: Max depth for recursively expanding quote chains.
_MAX_REPLY_DEPTH = 3
#: text-file content injection cap (truncate and annotate beyond it, to avoid blowing up the context)
_FILE_TEXT_MAX_BYTES = 100 * 1024
#: file extensions treated as images and routed to the VLM path
_IMAGE_FILE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic", ".svg"}
)


class QQMessageEnricher:
    """Enhance a normalized QQ message for LLM consumption.

    ``client`` is the connector (a ``OneBotConnectionBase``); it supplies the *data*
    API used to pull quoted / forwarded / voice / file content. ``image_describer``
    and ``voice_transcriber`` are the optional VLM / STT callbacks (business-owned).
    """

    def __init__(
        self,
        client: Any,
        *,
        image_describer: Any = None,
        voice_transcriber: Any = None,
        logger: Any = None,
        emit_log: Any = None,
    ):
        self._client = client
        self._image_describer = image_describer
        self._voice_transcriber = voice_transcriber
        self.logger = logger
        self._emit_log = emit_log or (lambda level, msg: None)

    # ── segment extraction (what the pipeline should enrich) ────────────

    def _transcribe_record_segments(self, message: Dict[str, Any]) -> list[str]:
        """Extract voice-segment info, returning the file_ids to fetch asynchronously.
        Supports array segments, CQ-code strings, and raw_message."""
        segments = message.get("message")
        record_files: list[str] = []
        if isinstance(segments, list):
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                if seg.get("type") == "record":
                    f = str(seg.get("data", {}).get("file") or "").strip()
                    if f:
                        record_files.append(f)
        if isinstance(segments, str):
            for m in re.finditer(r"\[CQ:record,\s*file=([^,\]]+)", segments):
                f = m.group(1).strip()
                if f:
                    record_files.append(f)
        raw_msg = message.get("raw_message")
        if isinstance(raw_msg, str) and not record_files:
            for m in re.finditer(r"\[CQ:record,\s*file=([^,\]]+)", raw_msg):
                f = m.group(1).strip()
                if f:
                    record_files.append(f)
        if record_files:
            self._emit_log("DEBUG", f"[Voice] 检测到 {len(record_files)} 条语音 (file_id={record_files})")
        else:
            self._emit_log("DEBUG", f"[Voice] 未检测到语音段, msg_type={message.get('message_type')} segments_type={type(segments).__name__} segments={str(segments)[:100]}")
        return record_files

    def _collect_file_segments(self, message: Dict[str, Any]) -> list[dict]:
        """Extract file-segment info, returning ``[{file_id, name, url, busid}]``."""
        segments = message.get("message")
        files: list[dict] = []
        if isinstance(segments, list):
            for seg in segments:
                if not isinstance(seg, dict) or seg.get("type") != "file":
                    continue
                d = seg.get("data")
                if not isinstance(d, dict):
                    d = {}
                try:
                    busid = int(d.get("busid") or 0)
                except (TypeError, ValueError):
                    busid = 0
                files.append({
                    "file_id": str(d.get("file_id") or "").strip(),
                    "name": str(d.get("file_name") or d.get("name") or d.get("file") or "").strip(),
                    "url": str(d.get("url") or "").strip(),
                    "busid": busid,
                })
        if not files:
            raw = str(message.get("raw_message") or message.get("message") or "")
            for m in re.finditer(r"\[CQ:file,[^\]]*\]", raw):
                text = m.group(0)
                fid = re.search(r"file_id=([^,\]]+)", text)
                fname = re.search(r"file=([^,\]]+)", text)
                busid = re.search(r"busid=(\d+)", text)
                files.append({
                    "file_id": fid.group(1).strip() if fid else "",
                    "name": fname.group(1).strip() if fname else "",
                    "url": "",
                    "busid": int(busid.group(1)) if busid else 0,
                })
        return files

    def _expand_forward_segments(self, message: Dict[str, Any]) -> list[str]:
        """Expand forward segments, appending readable text to raw_message.
        Returns forward_ids that must be fetched via API."""
        segments = message.get("message")
        if not isinstance(segments, list):
            return []
        forward_texts: list[str] = []
        unresolved_ids: list[str] = []
        for seg in segments:
            if not isinstance(seg, dict) or seg.get("type") != "forward":
                continue
            data = seg.get("data") or {}
            forward_id = str(data.get("id") or "").strip()
            sub_msgs = data.get("messages") or []
            if isinstance(sub_msgs, list) and sub_msgs:
                for sub in sub_msgs:
                    if not isinstance(sub, dict):
                        continue
                    sender = sub.get("sender", {})
                    sender_name = (sender.get("card") or sender.get("nickname") or str(sub.get("user_id") or "")).strip()
                    sub_segments = sub.get("message") or []
                    sub_text = ""
                    if isinstance(sub_segments, list):
                        for s in sub_segments:
                            if isinstance(s, dict) and s.get("type") == "text":
                                sub_text += str(s.get("data", {}).get("text", ""))
                            elif isinstance(s, dict) and s.get("type") == "image":
                                sub_text += "[图片]"
                            elif isinstance(s, dict) and s.get("type") == "face":
                                sub_text += "[表情]"
                    elif isinstance(sub_segments, str):
                        sub_text = sub_segments
                    if sub_text.strip():
                        forward_texts.append(f"[转发] {sender_name}: {sub_text.strip()}")
            elif forward_id:
                unresolved_ids.append(forward_id)
        if forward_texts:
            raw = str(message.get("raw_message") or "").strip()
            expanded = "\n".join(forward_texts)
            message["raw_message"] = f"{raw}\n{expanded}" if raw else expanded
            if not message.get("content"):
                message["content"] = message["raw_message"]
            message["_forward_sub_count"] = len(forward_texts)
        return unresolved_ids

    def _expand_reply_segments(self, message: Dict[str, Any]) -> list[str]:
        """Collect quoted-reply message IDs, for async full-content fetch."""
        segments = message.get("message")
        if not isinstance(segments, list):
            return []
        reply_ids: list[str] = []
        for seg in segments:
            if isinstance(seg, dict) and seg.get("type") == "reply":
                rid = str((seg.get("data") or {}).get("id") or "").strip()
                if rid:
                    reply_ids.append(rid)
        return reply_ids

    # ── async content fetch ─────────────────────────────────────────────

    async def _fetch_forward_content(self, message: Dict[str, Any], unresolved_ids: list[str]) -> None:
        """Fetch forwarded message content via API, build MessageChain and append."""
        seen: set[str] = set()
        forward_texts: list[str] = []
        for fid in unresolved_ids:
            if fid in seen:
                continue
            seen.add(fid)
            try:
                data = await self._client.get_forward_msg(fid)
                chains = await self._build_forward_chains(data)
                for chain in chains:
                    sender = chain.sender_name or chain.sender_id or "未知用户"
                    ts_str = ""
                    if chain.timestamp:
                        ts_str = _dt.fromtimestamp(chain.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                    prefix = f"[转发] [{ts_str}] {sender}: " if ts_str else f"[转发] {sender}: "
                    forward_texts.append(prefix + chain.repr)
            except Exception:
                if self.logger:
                    self.logger.exception(f"Failed to fetch forward msg {fid}")
        if forward_texts:
            raw = str(message.get("raw_message") or "").strip()
            expanded = "\n".join(forward_texts)
            message["raw_message"] = f"{raw}\n{expanded}" if raw else expanded
            content = str(message.get("content") or "").strip()
            message["content"] = f"{content}\n{expanded}" if content else message["raw_message"]
            prev = message.get("_forward_sub_count", 0)
            message["_forward_sub_count"] = prev + len(forward_texts)

    async def _build_message_chain(self, msg_data: dict[str, Any], *, depth: int = 0, seen: set[str] | None = None) -> MessageChain:
        """Convert a message dict into a MessageChain, recursively expanding quote/forward."""
        if seen is None:
            seen = set()
        msg = msg_data.get("data") or msg_data
        chain = MessageChain(
            sender_name=self._resolve_reply_sender(msg),
            sender_id=str(msg.get("user_id") or ""),
            timestamp=int(msg.get("time") or 0),
            message_id=str(msg.get("message_id") or ""),
        )
        segments = msg.get("message") or []
        if not isinstance(segments, list):
            raw = str(msg.get("raw_message") or "")
            chain.add(Text(re.sub(r"\[CQ:[^]]+]", "", raw).strip()))
            return chain
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            data = seg.get("data") or {}
            st = seg.get("type") or ""
            if st == "text":
                chain.add(Text(str(data.get("text") or "")))
            elif st == "image":
                img_url = str(data.get("url") or data.get("file") or "")
                desc = ""
                if img_url and self._image_describer and depth <= _MAX_REPLY_DEPTH:
                    try:
                        desc = await asyncio.wait_for(self._image_describer(img_url), timeout=8.0)
                    except Exception:
                        pass
                if desc:
                    chain.add(Text(f"[Image {desc}]"))
                else:
                    chain.add(Image(url=img_url))
            elif st == "face":
                chain.add(Emoji(emoji_id=str(data.get("id") or "")))
            elif st == "at":
                qq = str(data.get("qq") or "")
                if not qq:
                    continue
                nickname = ""
                if qq != "all":
                    nickname = await self._resolve_at_nickname(qq, msg)
                chain.add(At(pid=qq, nickname=nickname))
            elif st == "record":
                chain.add(Record(file_id=str(data.get("file") or "")))
            elif st == "video":
                chain.add(Text("[视频]"))
            elif st == "json":
                chain.add(JsonCard(raw_json=str(data.get("data") or "")))
            elif st == "file":
                chain.add(await self._build_file_element(data, msg))
            elif st == "reply":
                rid = str(data.get("id") or "").strip()
                inner_chain = MessageChain.empty()
                if rid and depth < _MAX_REPLY_DEPTH and rid not in seen:
                    seen.add(rid)
                    try:
                        inner_data = await self._client.get_msg(rid)
                        inner_chain = await self._build_message_chain(inner_data, depth=depth + 1, seen=seen)
                    except Exception:
                        pass
                chain.add(Reply(message_id=rid, chain=inner_chain))
            elif st == "forward":
                fid = str(data.get("id") or "").strip()
                sub_chains: list[MessageChain] = []
                if fid and depth < _MAX_REPLY_DEPTH and fid not in seen:
                    seen.add(fid)
                    try:
                        forward_data = await self._client.get_forward_msg(fid)
                        sub_chains = await self._build_forward_chains(forward_data, depth=depth + 1, seen=seen)
                    except Exception:
                        pass
                chain.add(Forward(chains=sub_chains))
        return chain

    async def _build_file_element(self, data: dict[str, Any], msg: dict[str, Any]) -> File:
        """Turn a file segment into a File element, resolving the real URL."""
        raw_file = str(data.get("file") or "")
        url = str(data.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            name = str(data.get("file_name") or "") or str(data.get("name") or "") or raw_file or "file"
            return File(name=name, url=url)
        file_id = str(data.get("file_id") or "").strip()
        if file_id:
            msg_type = str(msg.get("message_type") or "").strip()
            group_id = str(msg.get("group_id") or "").strip()
            try:
                if msg_type == "group" and group_id:
                    try:
                        busid = int(data.get("busid") or 0)
                    except (TypeError, ValueError):
                        busid = 0
                    if busid:
                        ret = await self._client.get_group_file_url(group_id, file_id, busid=busid)
                    else:
                        ret = await self._client.get_file_by_id(file_id)
                elif msg_type == "private":
                    sender_id = str(msg.get("user_id") or "").strip()
                    ret = await self._client.get_private_file_url(sender_id, file_id) if sender_id else None
                else:
                    ret = None
                ret_url = str((ret or {}).get("url") or "").strip()
                if ret_url:
                    name = str(ret.get("file_name") or "") or str(ret.get("name") or "") or raw_file or "file"
                    return File(name=name, url=ret_url)
            except Exception:
                pass
        return File(name=raw_file)

    async def _resolve_at_nickname(self, qq: str, msg: dict[str, Any]) -> str:
        """Resolve the nickname of an @ target (injected into the prompt by At.repr)."""
        group_id = str(msg.get("group_id") or "").strip()
        if group_id:
            try:
                info = await self._client.get_group_member_info(group_id, qq, no_cache=False)
                nickname = str((info or {}).get("card") or "").strip()
                if nickname:
                    return nickname
            except Exception:
                pass
        try:
            info = await self._client.get_stranger_info(qq, no_cache=False)
            return str((info or {}).get("nick") or (info or {}).get("nickname") or "").strip()
        except Exception:
            return ""

    async def _build_forward_chains(self, forward_data: dict[str, Any], *, depth: int = 0, seen: set[str] | None = None) -> list[MessageChain]:
        """Build MessageChains from a get_forward_msg return; each sub-message carries a timestamp."""
        messages = forward_data.get("messages") or forward_data.get("data", {}).get("messages") or []
        if not isinstance(messages, list):
            return []
        chains: list[MessageChain] = []
        for sub in messages:
            if not isinstance(sub, dict):
                continue
            chain = await self._build_message_chain(sub, depth=depth, seen=seen)
            chains.append(chain)
        return chains

    async def _fetch_reply_content(self, message: Dict[str, Any], reply_ids: list[str]) -> None:
        """Recursively expand the quote chain: build nested MessageChains via the get_msg API."""
        seen: set[str] = set()
        chains: list[MessageChain] = []
        first_sender_id = ""
        for rid in reply_ids:
            if rid in seen:
                continue
            seen.add(rid)
            try:
                data = await self._client.get_msg(rid)
                chain = await self._build_message_chain(data, depth=0, seen=seen)
                if chain.elements:
                    chains.append(chain)
                    if not first_sender_id and chain.sender_id:
                        first_sender_id = chain.sender_id
            except Exception:
                if self.logger:
                    self.logger.exception(f"Failed to fetch reply msg {rid}")
        if first_sender_id:
            message["_cached_reply_sender_id"] = first_sender_id
        if chains:
            lines: list[str] = []
            for chain in chains:
                sender = chain.sender_name or chain.sender_id or "未知用户"
                ts = chain.timestamp
                time_str = ""
                if ts:
                    time_str = _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                header = f"[↑ {sender}"
                if time_str:
                    header += f" {time_str}"
                header += f": {chain.repr}]"
                lines.append(header)
            raw = str(message.get("raw_message") or "").strip()
            raw = re.sub(r"\[CQ:reply,\s*id=\d+[^\]]*\]", "", raw).strip()
            message["raw_message"] = raw if raw else str(message.get("raw_message") or "")
            if not message.get("content"):
                message["content"] = message["raw_message"]
            message["_reply_context"] = "\n".join(lines)
            message["_reply_chains"] = chains

    @staticmethod
    def _resolve_reply_sender(msg_data: dict[str, Any]) -> str:
        sender = msg_data.get("sender", {}) or {}
        name = sender.get("card") or sender.get("nickname") or ""
        if str(name).strip():
            return str(name).strip()
        uid = str(msg_data.get("user_id") or "").strip()
        return f"QQ用户{uid}" if uid else "未知用户"

    async def _fetch_record_content(self, message: Dict[str, Any], record_files: list[str]) -> None:
        """Asynchronously fetch voice files and inject the transcription into raw_message."""
        for file_id in record_files:
            try:
                self._emit_log("DEBUG", f"[Voice] 获取语音: file={file_id} has_transcriber={self._voice_transcriber is not None}")
                data = await self._client.get_record(file_id)
                url = str((data.get("data") or {}).get("url") or data.get("url") or "").strip()
                file_path = str((data.get("data") or {}).get("file") or data.get("file") or "").strip()
                record_bytes = b""
                if url:
                    try:
                        async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as cl:
                            resp = await cl.get(url)
                            if resp.status_code == 200:
                                record_bytes = resp.content
                    except Exception:
                        pass
                if not record_bytes and file_path:
                    _fp = _Path(file_path)
                    if _fp.is_file():
                        record_bytes = await asyncio.to_thread(_fp.read_bytes)
                record_b64 = base64.b64encode(record_bytes).decode() if record_bytes else ""
                self._emit_log("DEBUG", f"[Voice] get_record完成: b64_len={len(record_b64)} url_len={len(url)}")
                transcript = ""
                if self._voice_transcriber:
                    try:
                        if url:
                            transcript = await self._voice_transcriber(audio_url=url)
                        if not transcript and record_b64:
                            transcript = await self._voice_transcriber(record_b64)
                    except Exception:
                        if self.logger:
                            self.logger.exception("语音转文字失败")
                if transcript:
                    raw = str(message.get("raw_message") or "").strip()
                    message["raw_message"] = f"[语音] {transcript} {raw}".strip()
                    message["content"] = message["raw_message"]
                    self._emit_log("INFO", f"[Voice] 语音转文字完成: {transcript[:40]}")
                    continue
                existing = str(message.get("content") or message.get("raw_message") or "").strip()
                marker = "[语音]"
                if marker not in existing:
                    message["content"] = f"{marker} {existing}".strip() if existing else marker
            except Exception:
                if self.logger:
                    self.logger.exception(f"Failed to fetch record {file_id}")

    async def _fetch_file_content(self, message: Dict[str, Any], files: list[dict]) -> None:
        """Background-fetch file content: images go through VLM, decode text, mark binary/failed."""
        renders: list[str] = []
        msg_type = str(message.get("message_type") or "").strip()
        group_id = str(message.get("group_id") or "").strip()
        for f in files:
            file_id = str(f.get("file_id") or "").strip()
            name = str(f.get("name") or "").strip() or file_id or "文件"
            url = str(f.get("url") or "").strip()
            render: str | None = None
            try:
                if not url and file_id:
                    if msg_type == "group" and group_id:
                        try:
                            busid = int(f.get("busid") or 0)
                        except (TypeError, ValueError):
                            busid = 0
                        if busid:
                            ret = await self._client.get_group_file_url(group_id, file_id, busid=busid)
                        else:
                            ret = await self._client.get_file_by_id(file_id)
                        url = str((ret or {}).get("url") or "").strip()
                    elif msg_type == "private":
                        sender_id = str(message.get("user_id") or "").strip()
                        if sender_id:
                            ret = await self._client.get_private_file_url(sender_id, file_id)
                            url = str((ret or {}).get("url") or "").strip()
                if not url:
                    render = f"[文件 {name}]"
                elif _Path(name).suffix.lower() in _IMAGE_FILE_EXTENSIONS:
                    desc = ""
                    if self._image_describer:
                        try:
                            desc = await asyncio.wait_for(self._image_describer(url), timeout=15.0)
                        except Exception:
                            pass
                    render = f"[文件 {name} (图片)]"
                    if desc:
                        render = f"{render}: {desc}"
                else:
                    async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as cl:
                        read_limit = _FILE_TEXT_MAX_BYTES + 1
                        async with cl.stream("GET", url) as resp:
                            if resp.status_code == 200:
                                chunks: list[bytes] = []
                                total = 0
                                async for chunk in resp.aiter_bytes():
                                    chunks.append(chunk)
                                    total += len(chunk)
                                    if total >= read_limit:
                                        break
                                payload = b"".join(chunks)
                                if b"\x00" in payload[:512]:
                                    render = f"[文件 {name} (二进制,无法读取)]"
                                else:
                                    text = payload[:_FILE_TEXT_MAX_BYTES].decode("utf-8", errors="replace")
                                    truncated = total > _FILE_TEXT_MAX_BYTES
                                    tail = "\n…(内容过长已截断)" if truncated else ""
                                    render = f"[文件 {name}]\n{text}{tail}"
                            else:
                                render = f"[文件 {name} (下载失败)]"
            except Exception:
                render = f"[文件 {name}]"
            if render:
                renders.append(render)
        if not renders:
            return
        raw = str(message.get("raw_message") or message.get("content") or "")
        replaced = [False]
        it = iter(renders)

        def _repl(m: Any) -> str:
            replaced[0] = True
            return next(it, m.group(0))

        new_raw = re.sub(r"\[CQ:file,[^\]]*\]", _repl, raw)
        if not replaced[0]:
            extra = " ".join(renders)
            new_raw = (new_raw.strip() + " " + extra).strip() if new_raw.strip() else extra
        message["raw_message"] = new_raw
        message["content"] = new_raw

    async def _inject_image_descriptions(self, message: dict[str, Any]) -> None:
        """Call VLM to describe the images in the main message and inject into content."""
        raw_msg = message.get("raw") or {}
        segments = raw_msg.get("message") or message.get("message") or []
        if not isinstance(segments, list):
            return
        img_urls: list[str] = []
        for seg in segments:
            if isinstance(seg, dict) and seg.get("type") == "image":
                sd = seg.get("data") or {}
                u = str(sd.get("url") or sd.get("file") or "").strip()
                if u:
                    img_urls.append(u)
        if img_urls:
            self._emit_log("DEBUG", f"[VLM] 检测到 {len(img_urls)} 张主消息图片, 开始描述...")
        descriptions: list[str] = []
        for img_url in img_urls:
            try:
                desc = await asyncio.wait_for(self._image_describer(img_url), timeout=8.0)
                if desc:
                    descriptions.append(f"[Image {desc}]")
                    self._emit_log("INFO", f"[VLM] 图片描述: {desc[:40]}")
                else:
                    self._emit_log("DEBUG", "[VLM] 图片描述返回为空")
            except Exception as e:
                self._emit_log("DEBUG", f"[VLM] 图片描述失败: {type(e).__name__}")
        if not descriptions:
            return
        base = str(message.get("content") or message.get("raw_message") or "").strip()
        if base:
            _it = iter(descriptions)

            def _replace_image(match) -> str:
                return next(_it, match.group(0))

            new_text = re.sub(r"\[CQ:image,[^\]]*\]", _replace_image, base)
            leftover = list(_it)
            if leftover:
                new_text = f"{new_text} {' '.join(leftover)}".strip()
        else:
            new_text = " ".join(descriptions)
        message["raw_message"] = new_text
        message["content"] = new_text

    # ── entry point ─────────────────────────────────────────────────────

    async def enrich_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Expand quote/forward/voice/file content + inject VLM image descriptions.

        Consumes the ``_pending_*`` markers (stamped by the pipeline), called after
        the dispatcher's eligibility filter and before backlog / blacklist re-check.
        Returns the (possibly rewritten) message dict.
        """
        reply_ids = message.get("_pending_reply_ids")
        if isinstance(reply_ids, list) and reply_ids:
            await self._fetch_reply_content(message, reply_ids)
            message.pop("_pending_reply_ids", None)

        forward_ids = message.get("_pending_forward_ids")
        if isinstance(forward_ids, list) and forward_ids:
            await self._fetch_forward_content(message, forward_ids)
            message.pop("_pending_forward_ids", None)

        record_files = message.get("_pending_record_files")
        if isinstance(record_files, list) and record_files:
            try:
                await asyncio.wait_for(self._fetch_record_content(message, record_files), timeout=60.0)
            except (asyncio.TimeoutError, Exception) as e:
                self._emit_log("DEBUG", f"[Voice] 语音转录超时或失败: {type(e).__name__}")
            message.pop("_pending_record_files", None)

        file_ids = message.get("_pending_file_ids")
        if isinstance(file_ids, list) and file_ids:
            try:
                await asyncio.wait_for(self._fetch_file_content(message, file_ids), timeout=60.0)
            except (asyncio.TimeoutError, Exception) as e:
                self._emit_log("DEBUG", f"[File] 文件内容拉取超时或失败: {type(e).__name__}")
            message.pop("_pending_file_ids", None)

        if self._image_describer:
            await self._inject_image_descriptions(message)
        return message
