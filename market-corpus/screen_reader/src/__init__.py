"""screen_reader — 通用屏幕阅读器（Windows 桌面）。

让 YUI「看得见」电脑屏幕：全屏/区域/窗口截屏（PNG，路径供视觉能力解读）、
前台窗口识别、可选 OCR 文字识别（需 Tesseract-OCR 引擎）。

安全边界：只在用户会话内按需截屏，不静默持续录制；不上传截图到第三方；
不采集输入框密码/验证码等敏感输入画面。
"""

from __future__ import annotations

import base64
import logging

from plugin.sdk.plugin import (
    NekoPluginBase,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from . import capture

_log = logging.getLogger("N.E.K.O.Plugin_screen_reader")


def _image_entry(image_path: str) -> list | None:
    """把截图文件读成 N.E.K.O ``images`` 通道条目 [{data_b64, mime}]。

    关键：N.E.K.O 只把工具返回 dict 的 ``images`` 数组喂给模型视觉——
    ``screenshot`` 字段只是文本路径，YUI 永远收不到图（'看不到画面'的根因之一）。
    超 2MB base64 上限时用 PIL 缩图存 JPEG（N.E.K.O 交付侧还会压缩到 ≤500KB）。
    """
    if not image_path:
        return None
    try:
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
class ScreenReaderPlugin(NekoPluginBase):
    """N.E.K.O. 插件：通用屏幕阅读器。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._save_dir: str | None = None

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._save_dir = capture.init_save_dir()
        return {"status": "ready", "plugin": "screen_reader"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        return {"status": "shutdown"}

    # ══════════ 工具 ══════════

    @llm_tool(
        name="screen_capture",
        description=(
            "截取电脑屏幕画面。何时用：①用户明确问'看看我屏幕/截个屏/屏幕上是什么/帮我看看这个界面'；"
            "②用户说'一起看电脑/一起看屏幕/我们一起看/你也看看/你看看我在看什么'等共享观看、需要你了解用户"
            "当前正在看什么内容（直播/视频/网页/界面）时，必须调用本工具截屏查看用户屏幕，再基于画面内容继续互动。"
            "参数 scope：full（全屏，默认）/ window（前台窗口）；region：可选，'x,y,w,h' 像素区域（scope=full 时生效）。"
            "返回 PNG 截图文件路径，可直接交给视觉能力解读。"
            "限制：仅截取用户当前会话内的屏幕，不做静默持续录制。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "full（全屏）或 window（前台窗口）"},
                "region": {"type": "string", "description": "可选：x,y,w,h 像素区域（scope=full 时生效）"},
            },
        },
        timeout=30.0,
    )
    async def screen_capture(self, *, scope: str = "full", region: str = "", **_):
        params = _.get("parameters") or {}
        scope = (scope or params.get("scope") or "full").strip().lower()
        region = region or params.get("region") or ""
        save_dir = self._save_dir or capture.init_save_dir()

        if scope in ("window", "前台窗口", "窗口"):
            info = capture.get_foreground_window()
            if not info:
                return {"output": "没有检测到前台窗口（可能桌面焦点不在任何窗口）。请切到目标窗口后重试，或改用全屏截图。",
                        "is_error": True, "error": "NO_WINDOW"}
            path = capture.capture_window(info, save_dir)
            if not path:
                return {"output": "窗口截图失败：无法读取该窗口画面。", "is_error": True, "error": "CAPTURE_FAILED"}
            return {
                "output": f"已截取前台窗口画面：\n窗口：{info['title']}（进程 {info['process']}，PID {info['pid']}）\n"
                          f"尺寸：{info['width']}×{info['height']}\n截图路径：{path}",
                "is_error": False,
                "screenshot": path,
                "images": _image_entry(path) or [],
                "window": info,
            }

        path = capture.capture_full(save_dir, region=region)
        if not path:
            return {"output": "全屏截图失败。", "is_error": True, "error": "CAPTURE_FAILED"}
        return {
            "output": f"已截取屏幕画面：\n尺寸：{capture.last_size()}\n截图路径：{path}\n"
                      "需要时可用视觉能力直接查看该图片。",
            "is_error": False,
            "screenshot": path,
            "images": _image_entry(path) or [],
        }

    @llm_tool(
        name="screen_ocr",
        description=(
            "识别屏幕画面中的文字（OCR）。何时用：用户问'屏幕上这段字是什么/把这个弹窗文字读出来/代码/聊天内容'"
            "等需要提取屏幕文字时。参数 scope：full（全屏，默认）/ window（前台窗口）。返回识别出的文本行。"
            "限制：需要已安装 Tesseract-OCR 引擎；未安装时返回提示，可用 screen_capture 截图后由视觉能力直接查看。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "full（全屏）或 window（前台窗口）"},
            },
        },
        timeout=60.0,
    )
    async def screen_ocr(self, *, scope: str = "full", **_):
        params = _.get("parameters") or {}
        scope = (scope or params.get("scope") or "full").strip().lower()
        save_dir = self._save_dir or capture.init_save_dir()

        path = None
        if scope in ("window", "前台窗口", "窗口"):
            info = capture.get_foreground_window()
            if not info:
                return {"output": "没有检测到前台窗口。请切到目标窗口后重试，或改用全屏识别。",
                        "is_error": True, "error": "NO_WINDOW"}
            path = capture.capture_window(info, save_dir)
        else:
            path = capture.capture_full(save_dir)

        if not path:
            return {"output": "截图失败，无法识别。", "is_error": True, "error": "CAPTURE_FAILED"}

        text = capture.ocr_image(path)
        if text is None:
            return {
                "output": "OCR 不可用：未检测到 Tesseract-OCR 引擎。已保留截图路径，可直接用视觉能力查看：\n" + path,
                "is_error": False,
                "screenshot": path,
                "images": _image_entry(path) or [],
            }
        if not text.strip():
            return {"output": "识别完成：画面中没有检测到文字。", "is_error": False, "text": ""}
        return {"output": "识别到的文字：\n" + text[:4000], "is_error": False, "text": text[:4000]}

    # ══════════ 自动路由入口（plugin_entry：TaskExecutor 评估可见） ══════════

    @plugin_entry(
        id="run",
        name="屏幕阅读器",
        description=(
            "屏幕阅读器（screen_reader）：让 YUI 看见电脑屏幕。"
            "用户说'看看我屏幕/截个屏/屏幕上是什么/帮我看看这个界面/把这个弹窗文字读出来/识别屏幕上这段字'，"
            "或用户说'一起看电脑/一起看屏幕/我们一起看/你也看看/你看看我在看什么'等共享观看、需要了解用户当前"
            "在看什么内容（直播/视频/网页/界面）时使用。"
            "操作：screen（全屏截图）/ window（前台窗口截图）/ ocr（屏幕文字识别，需 Tesseract-OCR 引擎）/ "
            "foreground（当前前台窗口信息）。截图以 PNG 文件路径返回，可直接交给视觉能力查看。"
            "安全边界：只在用户会话内按需截屏，不静默持续录制，不采集密码/验证码等敏感输入画面。"
            "回复纪律：直接给出截图路径、窗口信息或识别文字，不加多余话术，不编造屏幕内容。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "screen（全屏截图）/ window（前台窗口截图）/ ocr（文字识别）/ foreground（窗口信息）",
                },
                "region": {"type": "string", "description": "可选：x,y,w,h 像素区域（screen 用）"},
            },
        },
        timeout=60.0,
    )
    async def entry_run(self, **kwargs):
        params = kwargs.get("parameters") or {}
        action = str(kwargs.get("action") or params.get("action") or "").strip().lower()
        region = str(kwargs.get("region") or params.get("region") or "")
        if action in ("ocr", "识别", "read"):
            return await self.screen_ocr(scope=params.get("scope", "full"), parameters={})
        if action in ("window", "前台", "窗口"):
            return await self.screen_capture(scope="window", parameters={})
        if action in ("foreground", "前台窗口", "info"):
            return await self._foreground_info()
        if action in ("screen", "full", "截屏", ""):
            return await self.screen_capture(scope="full", region=region, parameters={})
        return {
            "output": "请指定操作：screen（全屏截图）/ window（前台窗口截图）/ ocr（文字识别）/ foreground（窗口信息）。",
            "is_error": True,
            "error": "NO_ACTION",
        }

    async def _foreground_info(self, **_):
        info = capture.get_foreground_window()
        if not info:
            return {"output": "没有检测到前台窗口（可能焦点在桌面）。", "is_error": True, "error": "NO_WINDOW"}
        return {
            "output": f"当前前台窗口：{info['title']}（进程 {info['process']}，PID {info['pid']}，"
                      f"{info['width']}×{info['height']}）",
            "is_error": False,
            "window": info,
        }
