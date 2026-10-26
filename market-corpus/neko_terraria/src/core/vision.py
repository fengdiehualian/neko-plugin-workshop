"""截图采集 + 节流 + 推 LLM（ai_behavior=read）。

v2.1: VisionPipeline 容器 —— 截图源(可插拔) → VisionBridge 节流
      → VisualPerception 分析（LLM Vision + 游戏数据融合 + 交互引擎事件）。
"""

import asyncio
import time
from typing import Callable, Optional


class VisionBridge:
    def __init__(self, cfg: dict, push_message=None) -> None:
        self.cfg = cfg
        self.push = push_message
        # ★ 截图推宿主 LLM 的间隔：曾默认 6s——boot 后约 100 秒内推 17-18 张
        #   全屏截图进宿主 pending queue（read 模式），用户下一轮说话时 LLM 一次
        #   携带全部图片+文本 → 上下文爆炸、LLM 卡死数分钟、恢复后拼接刷屏、
        #   且 llm_tool 指令（terraria_command）随 LLM 卡住不执行。
        #   120s 一张：仍保留"猫娘看到主人画面"的陪伴感，同时单轮最多多 1 张图。
        self.min_interval = cfg.get("screenshot_stream_min_interval_seconds", 120.0)
        self._last = 0.0

    async def on_frame(self, b64: str, mime: str) -> None:
        now = time.time()
        if now - self._last < self.min_interval:
            return
        self._last = now
        if self.push:
            try:
                import base64
                img_bytes = base64.b64decode(b64)
                img_bytes = self._compress_for_wire(img_bytes)
                if not img_bytes:
                    return
                # SDK push_message 是同步方法，不能 await
                self.push(
                    parts=[{"type": "image", "data": img_bytes,
                            "mime": "image/jpeg"}],
                    ai_behavior="read")
            except Exception:
                pass

    def _compress_for_wire(self, img_bytes: bytes) -> Optional[bytes]:
        """压缩到宿主 payload 上限内（参照 Minecraft 插件做法）。

        - 原始 ≤100KB 直接返回（解码都省了）
        - 超过则用 PIL 阶梯降级：边缘 1024→512→256 × JPEG 质量 80→65→50→40→30，
          直到原始 JPEG ≤100KB（100KB ≈ 256KB 上限的 ~2.3x 反推安全余量）
        - PIL 不可用或压缩失败：超限返回 None（静默跳过），未超限原样返回
        """
        if len(img_bytes) <= 100 * 1024:
            return img_bytes
        try:
            from io import BytesIO

            from PIL import Image
            img = Image.open(BytesIO(img_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            for edge in (1024, 512, 256):
                w, h = img.size
                longest = max(w, h)
                if longest > edge:
                    scale = edge / longest
                    img = img.resize((max(1, int(w * scale)),
                                      max(1, int(h * scale))),
                                     Image.LANCZOS)
                for quality in (80, 65, 50, 40, 30):
                    buf = BytesIO()
                    img.save(buf, "JPEG", quality=quality)
                    out = buf.getvalue()
                    if len(out) <= 100 * 1024:
                        return out
            return None   # 256px/30q 仍超限：放弃（视觉是增强项，不阻塞）
        except Exception:
            # PIL 不可用等：未超限原样返回，超限丢弃
            return img_bytes if len(img_bytes) <= 256 * 1024 else None


class VisionPipeline:
    """截图管线容器：采集循环 + 节流桥 + 感知分析。

    - set_frame_source(fn)：注入截图源（async () -> (b64, mime) | None）。
      未注入或源返回 None 时循环静默跳过（截图能力未就绪的降级路径）。
    - 实例暴露为 agent.vision，lifecycle 的 _wire_vision_llm 通过
      agent.vision.perception 注入 LLM Vision 能力。
    """

    def __init__(self, cfg: dict, agent=None, push_message=None) -> None:
        from ..perception.vision import VisualPerception  # 延迟导入避免循环

        self.cfg = cfg
        self.agent = agent
        self.bridge = VisionBridge(cfg, push_message)
        self.perception = VisualPerception(self.bridge, agent)
        self._frame_source: Optional[Callable] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # 截图采集循环间隔：与推宿主 LLM 的间隔解耦（采集可快，推送受 bridge 节流）
        self._interval = cfg.get("vision_capture_interval_seconds", 6.0)

    def set_frame_source(self, fn: Callable) -> None:
        """注入截图源：async () -> (b64: str, mime: str) 或 None。"""
        self._frame_source = fn

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _capture_loop(self) -> None:
        while self._running:
            try:
                if self._frame_source:
                    frame = await self._frame_source()
                    if frame:
                        b64, mime = frame
                        await self.perception.feed(b64, mime)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger = getattr(self.agent, "logger", None)
                if logger:
                    logger.warning("视觉采集循环异常: %s", exc)
            await asyncio.sleep(self._interval)
