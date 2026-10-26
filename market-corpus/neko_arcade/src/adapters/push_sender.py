"""推送适配：通过主项目 push_message 发送文字/图片/音频。

图片显示策略（重要）：
- 旧宿主（未合并 #2835）：没有 ctx.images，聊天窗前端 ReactMarkdown 只开
  remark-gfm/rehype-katex，**不渲染 HTML 标签**——`<img>` 会原样显示成代码。
  唯一用户可见图片方式是**标准 markdown 图片语法 `![alt](url)`**。
- 新宿主（#2835 合并后，0.9.0.x）：`parts` + `visibility=["chat"]` 原生图片气泡。
  宿主聊天投影（app/main_server/character_runtime.py::_build_plugin_chat_blocks）
  对两种 part 的处理不同：
    * `{"type":"image","data":bytes,"mime":"image/png"}` → 内联 data: URL，
      **保留插件上传的原始分辨率、不重编码**；每推 ≤8 张、内联合计 ≤8 MiB；
    * `ctx.images.upload()` → 归一化成 JPEG q88、最长边 ≤2048px，走 /media/<id>
      URL（不吃内联预算，但文字边缘有压缩痕迹）。
  因此文字密集的帮助图**优先内联原始字节**；不可用时退回上传路径；再退回
  markdown（兼容旧宿主）。
  注意：同时带 url 和字节的 image part 会被宿主直接丢弃，别混用。

注意：宿主不支持 audio/video parts(见 text_with_audio 注释)。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, List, Optional

DEFAULT_PLUGIN_SERVER_PORT = "48916"


class PushSender:
    """封装主项目 push_message，支持文本、图片、音频混合推送。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.source = "neko_arcade"

    # ── 图片落盘 → markdown URL ─────────────────────────

    def _static_dir(self) -> Optional[Path]:
        """插件 static 目录(宿主静态 UI 服务 /plugin/neko_arcade/ui/ 的根)。"""
        try:
            cfg_dir = getattr(self.plugin, "config_dir", None)
            if cfg_dir is None:
                return None
            return Path(cfg_dir) / "static"
        except Exception:
            return None

    def _base_url(self) -> str:
        """宿主插件 server 地址(USER_PLUGIN_SERVER_PORT, 默认 48916)。"""
        port = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "") or \
               os.getenv("USER_PLUGIN_SERVER_PORT", "") or DEFAULT_PLUGIN_SERVER_PORT
        return f"http://127.0.0.1:{port}/plugin/neko_arcade/ui/"

    async def save_image(self, image_bytes: bytes, mime: str = "image/png") -> Optional[str]:
        """把图片写入 static/cards/，返回可访问的 http URL；失败返回 None。

        文件名带毫秒时间戳 + 内容哈希，避免缓存与冲突。
        旧宿主 markdown 通道的图片没有 CSS 宽度限制, 落盘前先缩放
        (最长边 ≤ 720px, 保持纵横比), 防止 720px 帮助图/大图在窄聊天窗溢出。
        """
        static = self._static_dir()
        if not static:
            return None
        try:
            # 缩放(仅静态图; 失败则原样落盘)。PNG 缩放后转 JPEG, 扩展名跟着变。
            scaled = self._resize_for_markdown(image_bytes)
            data = scaled if scaled is not None else image_bytes
            cards = static / "cards"
            cards.mkdir(parents=True, exist_ok=True)
            if scaled is not None:
                ext = ".jpg"  # 缩放输出恒为 JPEG
            elif "jpeg" in mime or "jpg" in mime:
                ext = ".jpg"
            elif "gif" in mime:
                ext = ".gif"
            else:
                ext = ".png"
            digest = hashlib.md5(data[:4096]).hexdigest()[:8]
            name = f"{int(time.time() * 1000)}_{digest}{ext}"
            (cards / name).write_bytes(data)
            return f"{self._base_url()}cards/{name}"
        except Exception:
            return None

    @staticmethod
    def _resize_for_markdown(image_bytes: bytes, max_side: int = 720) -> Optional[bytes]:
        """把图片缩放到最长边 ≤ max_side(默认 720px), 保持纵横比。

        旧宿主 markdown 图片无 CSS 限制, 大图会溢出聊天窗; 这里在落盘前
        缩放。PNG 转 JPEG 压缩体积(RGBA 需转 RGB)。失败返回 None(调用方原样落盘)。
        """
        try:
            import io

            from PIL import Image
            buf = io.BytesIO(image_bytes)
            img = Image.open(buf)
            w, h = img.size
            longest = max(w, h)
            if longest <= max_side:
                return None  # 不需要缩放
            ratio = max_side / longest
            new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85)
            return out.getvalue()
        except Exception:
            return None

    # ── 推送 ──────────────────────────────────────────

    async def text(self, text: str, visibility: Optional[List[str]] = None,
                   ai_behavior: str = "blind") -> None:
        """推一条纯文本消息。"""
        await self._push([{"type": "text", "text": text}], visibility, ai_behavior)

    async def text_with_image(self, text: str, image_bytes: bytes,
                              mime: str = "image/png",
                              ai_behavior: str = "blind") -> None:
        """推文本 + 图片。

        ⚠️ ai_behavior 默认 "blind"（宿主侧 = silent，跳过 LLM 注入）。
        只有 visibility 决定"在聊天窗显示"，所以 blind 一样能出图；
        而 "read" 会把这条推送注入模型上下文并让宿主多起一轮——
        LLM 复述用户已见内容、还带着旧输入回调 play_game，用户看到的就是
        "双重回复"（实测 2026-09-27 五子棋对局：正常落子后 8 秒又收到同一句，
        第二次落子被判非法 → 两份状态卡）。需要模型真的"看图"时才显式传 "read"。

        优先走原生图片通道(#2835): 内联原始字节 part + visibility=["chat"]
        → 聊天窗原生图片气泡(原始分辨率、不重编码)；内联不可用时退回
        ctx.images.upload()(JPEG 归一化)。SDK 不支持(旧宿主)时回退 markdown
        图片语法 `![alt](url)`——旧宿主前端不渲染 <img> HTML 标签
        (ReactMarkdown 无 rehype-raw), 必须用标准 markdown 图片语法。
        """
        if await self._push_native_image(text, image_bytes, mime=mime,
                                         ai_behavior=ai_behavior):
            return
        # 回退: 旧宿主 markdown 图片语法(![alt](url), 前端 ReactMarkdown 内置支持)
        url = await self.save_image(image_bytes, mime)
        if url:
            content = f"{text}\n\n![游戏图片]({url})"
        else:
            content = text
        await self._push([{"type": "text", "text": content}], ai_behavior=ai_behavior)

    async def text_with_image_url(self, text: str, url: str,
                                  ai_behavior: str = "blind") -> None:
        """推文本 + 图片 URL（ai_behavior 语义同 text_with_image，默认 blind）。

        优先原生通道(URL 已由 ctx.images.upload() 产生时); 否则 markdown
        图片语法 `![alt](url)`(旧宿主前端不渲染 <img> HTML)。
        """
        if await self._push_native_image(text, url=url, ai_behavior=ai_behavior):
            return
        content = f"{text}\n\n![游戏图片]({url})" if url else text
        await self._push([{"type": "text", "text": content}], ai_behavior=ai_behavior)

    async def _push_parts(self, parts: List[dict], ai_behavior: str) -> bool:
        """按 v2 契约(parts + visibility)推送；失败返回 False 由调用方回退。"""
        try:
            receipt = self.plugin.push_message(
                source=self.source,
                parts=parts,
                visibility=["chat"],
                ai_behavior=ai_behavior,
                target_lanlan=self._resolve_target_lanlan() or None,
            )
            # ⚠️ 宿主对超限的内联图**不抛异常**，只回一条 rejected 回执：
            #    `push_message rejected: reason=payload_too_large ...`
            # 不检查回执就会误判成"推送成功"，既不回退也不换通道，
            # 用户那边就是"没有图"。这里把 rejected 回执当失败。
            if isinstance(receipt, dict):
                if receipt.get("ok") is False or receipt.get("accepted") is False:
                    return False
                reason = str(receipt.get("reason") or receipt.get("error") or "")
                if "reject" in reason.lower() or "too_large" in reason.lower() \
                        or "payload" in reason.lower():
                    return False
            return True
        except Exception:
            return False

    async def _push_native_image(self, text: str, image_bytes: Optional[bytes] = None,
                                 url: Optional[str] = None,
                                 ai_behavior: str = "blind",
                                 mime: str = "image/png") -> bool:
        """原生图片通道：内联原始字节优先 → SDK 上传 → 都不行返回 False。

        返回 True 表示已推送；False 表示这条通道不可用，调用方应回退 markdown。
        visibility=["chat"] 让图片在用户聊天窗可见；ai_behavior="blind" 表示
        只给用户看、不喂 LLM（帮助文档图 / 对局状态图 / 照片都用它）——
        别改成 "read"：宿主会把内容注入模型上下文，多起一轮就变成"双重回复"。

        为什么内联优先：宿主聊天投影对 data 内联图**不缩放、不重编码**（官方
        注释: 帮助/文档/代码图正是读者要放大看的材料），而 ctx.images.upload
        会先把 PNG 压成 JPEG q88。帮助图是文字密集图，原始 PNG 明显更清晰。
        """
        images_api = getattr(getattr(self.plugin, "ctx", None), "images", None)
        if images_api is None or not hasattr(self.plugin, "push_message"):
            return False
        if url is None and not image_bytes:
            return False
        head = [{"type": "text", "text": text}] if text else []
        if url is not None:
            # 已是宿主侧 URL(原生媒体/static) → 直接按 url part 推
            return await self._push_parts(head + [{"type": "image", "url": url}], ai_behavior)
        # 1) 内联原始字节：原始分辨率 + 不重编码
        #    ⚠️ 宿主内联预算：单条 part 原始字节上限约 393045B(524061B / 1.33)。
        #    帮助图动辄 500KB+，内联必被 rejected —— 大图直接跳过内联走上传通道。
        inline_max = 360_000
        if image_bytes and len(image_bytes) <= inline_max:
            if await self._push_parts(
                head + [{"type": "image", "data": image_bytes, "mime": mime}], ai_behavior
            ):
                return True
        # 2) 退回 SDK 归一化上传(JPEG, ≤2048px) → url part
        upload = getattr(images_api, "upload", None)
        if not callable(upload):
            return False
        try:
            part = await upload(image_bytes or b"", timeout=8.0)
        except Exception:
            return False
        if not isinstance(part, dict):
            return False
        return await self._push_parts(head + [part], ai_behavior)

    def static_url(self, relative_path: str) -> str:
        """把 static 下的相对路径转成可访问的 http URL。

        relative_path 形如 "img/neko/可爱/xx.png"(相对 static 目录)。
        """
        return f"{self._base_url()}{relative_path.lstrip('/')}"

    # 注意: 宿主不支持 audio/video parts —— character_runtime 对 type != "image"
    # 的 part 直接 warning 后 drop(stream_audio 是实时麦克风 PCM 管线, 非通用
    # 文件注入器)。因此不要尝试 push audio part 给用户。
    # 语音播报的正确姿势: 宿主会自动把 chat 通道文字 TTS 播放(官方 short_tts_line
    # 契约), 插件只需保证文本是 TTS-friendly 短句(见 TTSClient), 无需推音频数据。

    async def help_doc(self, title: str, image_bytes: bytes,
                       text: Optional[str] = None) -> None:
        """推帮助文档图片。

        优先原生通道: 内联 PNG(原始分辨率, 帮助图文字密集, 不做 JPEG 重编码)
        + visibility=["chat"] + ai_behavior="blind"(只给用户看, 不喂 LLM)；
        内联不可用 → ctx.images.upload()；SDK 不支持(旧宿主) → markdown
        图片语法 `![alt](url)`(前端 ReactMarkdown 内置支持)。
        """
        caption = text or title
        if await self._push_native_image(caption, image_bytes, ai_behavior="blind"):
            return
        # 回退: 旧宿主 markdown 图片语法
        url = await self.save_image(image_bytes, "image/png")
        lines = []
        if text:
            lines.append(text)
        if url:
            lines.append(f"![{title}]({url})")
        if not lines:
            return
        await self._push([{"type": "text", "text": "\n".join(lines)}])

    async def _push(self, parts: List[dict],
                    visibility: Optional[List[str]] = None,
                    ai_behavior: str = "blind") -> None:
        # 注意：SDK 的 push_message 是同步方法（返回回执对象，不是协程），不能 await。
        # 之前写成 await self.plugin.push_message(...) 会抛
        # "TypeError: object dict can't be used in 'await' expression"。
        #
        # target_lanlan：多猫娘(多角色)环境下不带目标角色名, 宿主 _get_session_manager("")
        # 拿不到会话, 多会话时 fallback 也为空 → 推送会被宿主直接丢弃(lifekit/neko_live
        # 都显式带 target_lanlan)。解析优先级: ctx 当前角色 → 环境变量 → 无(单角色兜底)。
        target = self._resolve_target_lanlan()
        self.plugin.push_message(
            source=self.source,
            parts=parts,
            visibility=visibility or ["chat", "hud"],
            ai_behavior=ai_behavior,
            target_lanlan=target or None,
        )

    def _resolve_target_lanlan(self) -> str:
        """解析推送目标角色名(猫娘名)。

        顺序: ctx._current_lanlan → ctx._host_ctx._current_lanlan →
        环境变量(NEKO_TARGET_LANLAN / NEKO_LANLAN_NAME / NEKO_HER_NAME)。
        拿不到返回空串(宿主单角色 fallback 可兜住)。
        """
        ctx = getattr(self.plugin, "ctx", None)
        host_ctx = getattr(ctx, "_host_ctx", None) if ctx is not None else None
        for source in (
            getattr(ctx, "_current_lanlan", None) if ctx is not None else None,
            getattr(host_ctx, "_current_lanlan", None) if host_ctx is not None else None,
            os.getenv("NEKO_TARGET_LANLAN", ""),
            os.getenv("NEKO_LANLAN_NAME", ""),
            os.getenv("NEKO_HER_NAME", ""),
        ):
            if isinstance(source, str) and source.strip():
                return source.strip()
        return ""
