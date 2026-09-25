# -*- coding: utf-8 -*-
"""合并后的 LLM 工具层（v0.4.0 → v0.5.0）。

设计：52 个 @llm_tool 合并为 32 个，按"动作族"组织（action/kind/op 枚举派发）。
旧的 48+4 个工具方法保留为普通方法（面板按钮、入口点、内部调用不受影响），
本 Mixin 只做参数校验与派发，业务逻辑全部复用原有实现。

v0.5.0 新增：screen_advanced（像素/帧差异/多尺度匹配/GIF）、gamepad（手柄）、
process（进程管理）、network（HTTP/下载/Ping）、notify（系统通知）、
trigger（条件触发器）、macro（宏录制/回放）。
"""
from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, SdkError, llm_tool


def _bad(msg: str):
    return Err(SdkError(msg))


class ConsolidatedToolsMixin:
    """25 个合并后的 LLM 工具。"""

    # ── 目标窗口管理 ─────────────────────────────────────────────
    @llm_tool(
        name="keyboard_target",
        description=(
            "目标窗口管理（action）：set 锁定目标窗口（之后所有注入都作用于它，"
            "pid 优先、query 为标题/进程名关键字）；get 查看当前目标；"
            "clear 解除锁定（也用于退出全屏模式）；fullscreen 进入全屏模式"
            "（不锁窗口直接对整屏注入，适合桌面/多窗口/全屏游戏）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "get", "clear", "fullscreen"],
                           "description": "对目标窗口执行的操作"},
                "pid": {"type": "integer", "description": "action=set 时：目标进程 pid（优先）"},
                "query": {"type": "string", "description": "action=set 时：窗口标题/进程名关键字"},
            },
            "required": ["action"],
        },
        timeout=20.0,
    )
    async def tool_target(self, action: str, pid: int | None = None, query: str = "", **_) -> Any:
        action = str(action or "").strip().lower()
        if action == "set":
            return await self.set_target(pid=pid, query=query)
        if action == "get":
            return await self.get_target()
        if action == "clear":
            return await self.clear_target()
        if action == "fullscreen":
            return await self.set_fullscreen()
        return _bad(f"未知 action：{action}（可选 set/get/clear/fullscreen）")

    @llm_tool(
        name="keyboard_find_windows",
        description=(
            "按窗口标题或进程名关键字搜索可见窗口，返回 pid、标题、进程名。"
            "配合 keyboard_target(action='set') 锁定目标。query 留空列出全部。"
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "关键字，可留空"}},
        },
        timeout=15.0,
    )
    async def tool_find_windows(self, query: str = "", **_) -> Any:
        return await self.find_windows(query=query)

    # ── 键盘输入 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_type",
        description=(
            "向目标窗口输入一段文本（Unicode 支持中文）。短文本逐字输入，"
            "长文本自动改用剪贴板粘贴。须先 keyboard_target(action='set')。"
        ),
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要输入的文本"}},
            "required": ["text"],
        },
        timeout=30.0,
    )
    async def tool_type(self, text: str, **_) -> Any:
        return await self.type_text(text=text)

    @llm_tool(
        name="keyboard_press",
        description=(
            "向目标窗口注入按键/组合键（'+' 连接，如 'ctrl+c'、'alt+f4'）。"
            "hold_seconds>0 时为长按（按住指定秒数再松开，用于蓄力/持续触发）。"
            "count 为重复次数。注入前自动聚焦目标窗口。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "按键或组合键，如 'space' / 'ctrl+c'"},
                "count": {"type": "integer", "description": "重复次数（默认 1）"},
                "hold_seconds": {"type": "number", "description": ">0 时长按秒数（默认不长按）"},
            },
            "required": ["keys"],
        },
        timeout=30.0,
    )
    async def tool_press(self, keys: str, count: int = 1, hold_seconds: float = 0, **_) -> Any:
        if hold_seconds and float(hold_seconds) > 0:
            return await self.hold_key(keys=keys, seconds=float(hold_seconds))
        return await self.press_keys(keys=keys, count=int(count or 1))

    @llm_tool(
        name="keyboard_sequence",
        description=(
            "向目标窗口依次执行一串混合操作：每步可为 {'keys': 组合键, count/delay/hold}、"
            "{'text': 文本}、{'action': 'click'|'move'|'drag'|'wheel'|'wait', x/y/x2/y2/button/"
            "delta/steps/seconds} 或直接字符串。用于连招、多步宏等一次性编排。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "array",
                    "description": "步骤列表，如 [{'keys': 'ctrl+c'}, {'keys': 'enter'}]",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "keys": {"type": "string"},
                                    "count": {"type": "integer"},
                                    "delay": {"type": "number"},
                                    "hold": {"type": "number"},
                                    "text": {"type": "string"},
                                    "action": {"type": "string",
                                               "enum": ["click", "move", "drag", "wheel", "wait"]},
                                    "seconds": {"type": "number"},
                                    "x": {"type": "integer"},
                                    "y": {"type": "integer"},
                                    "x2": {"type": "integer"},
                                    "y2": {"type": "integer"},
                                    "button": {"type": "string"},
                                    "delta": {"type": "integer"},
                                    "steps": {"type": "integer"},
                                },
                            },
                        ]
                    },
                },
            },
            "required": ["sequence"],
        },
        timeout=60.0,
    )
    async def tool_sequence(self, sequence: Any, **_) -> Any:
        return await self.press_sequence(sequence=sequence)

    # ── 鼠标输入 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_mouse",
        description=(
            "鼠标控制（action，屏幕绝对坐标，须先设目标）：move 移动光标到 x,y；"
            "click 在 x,y 点击（button 默认 left，clicks=2 双击）；"
            "drag 从 x,y 按住拖到 x2,y2（steps 插值步数默认 20）；"
            "wheel 在 x,y 滚动滚轮（delta 正=上/负=下，一格约 120）；"
            "hold 在 x,y（留空=当前光标）按住 button 键 hold_seconds 秒。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["move", "click", "drag", "wheel", "hold"],
                           "description": "鼠标动作"},
                "x": {"type": "integer", "description": "屏幕 x（drag 为起点 x1）"},
                "y": {"type": "integer", "description": "屏幕 y（drag 为起点 y1）"},
                "x2": {"type": "integer", "description": "action=drag：终点 x"},
                "y2": {"type": "integer", "description": "action=drag：终点 y"},
                "button": {"type": "string", "enum": ["left", "right", "middle"],
                           "description": "鼠标键（默认 left）"},
                "clicks": {"type": "integer", "description": "点击次数（默认 1，2=双击）"},
                "delta": {"type": "integer", "description": "action=wheel：滚动量（默认 120）"},
                "hold_seconds": {"type": "number", "description": "action=hold：按住秒数（默认 1）"},
                "steps": {"type": "integer", "description": "action=drag：插值步数（默认 20）"},
            },
            "required": ["action"],
        },
        timeout=30.0,
    )
    async def tool_mouse(self, action: str, x: int | None = None, y: int | None = None,
                         x2: int | None = None, y2: int | None = None,
                         button: str = "left", clicks: int = 1, delta: int = 120,
                         hold_seconds: float = 1.0, steps: int = 20, **_) -> Any:
        action = str(action or "").strip().lower()
        if action == "move":
            if x is None or y is None:
                return _bad("action=move 需要 x, y")
            return await self.mouse_move(x=int(x), y=int(y))
        if action == "click":
            if x is None or y is None:
                return _bad("action=click 需要 x, y")
            return await self.mouse_click(x=int(x), y=int(y), button=button, clicks=int(clicks))
        if action == "drag":
            if None in (x, y, x2, y2):
                return _bad("action=drag 需要 x, y, x2, y2")
            return await self.mouse_drag(x1=int(x), y1=int(y), x2=int(x2), y2=int(y2),
                                         button=button, steps=int(steps))
        if action == "wheel":
            if x is None or y is None:
                return _bad("action=wheel 需要 x, y")
            return await self.mouse_wheel(x=int(x), y=int(y), delta=int(delta))
        if action == "hold":
            return await self.hold_mouse(button=button, seconds=float(hold_seconds),
                                         x=None if x is None else int(x),
                                         y=None if y is None else int(y))
        return _bad(f"未知 action：{action}（可选 move/click/drag/wheel/hold）")

    @llm_tool(
        name="keyboard_click_window",
        description=(
            "在目标窗口**内部相对坐标** (x, y) 点击（0,0=客户区左上角），窗口移动也不点错。"
            "与 keyboard_mouse 的屏幕绝对坐标不同。坐标范围可先用 keyboard_window(action='rect') 查。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "窗口内 x"},
                "y": {"type": "integer", "description": "窗口内 y"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "默认 left"},
                "clicks": {"type": "integer", "description": "默认 1，2=双击"},
            },
            "required": ["x", "y"],
        },
        timeout=20.0,
    )
    async def tool_click_window(self, x: int, y: int, button: str = "left", clicks: int = 1, **_) -> Any:
        return await self.click_in_window(x=int(x), y=int(y), button=button, clicks=int(clicks))

    # ── 查找与点击（OCR/模板） ───────────────────────────────────
    @llm_tool(
        name="keyboard_click",
        description=(
            "查找并直接点击（method）：text 按文字点击（OCR 子串匹配 query）；"
            "image 按模板图点击（template_path，适合图形按钮）；"
            "control 按名点控件（name 子串，UIA Invoke 后台触发无需焦点）。"
            "mode='target' 目标窗口内找/'fullscreen' 全屏；index 选第几个匹配；"
            "min_score 仅 image；region 'x,y,w,h' 限定区域。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["text", "image", "control"], "description": "定位方式"},
                "query": {"type": "string", "description": "method=text：要点击的文字"},
                "template_path": {"type": "string", "description": "method=image：模板图片路径"},
                "name": {"type": "string", "description": "method=control：控件文本子串"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "description": "默认 target"},
                "index": {"type": "integer", "description": "第几个匹配（默认 0）"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "默认 left"},
                "clicks": {"type": "integer", "description": "默认 1，2=双击"},
                "min_score": {"type": "number", "description": "method=image：阈值（默认 0.75）"},
                "region": {"type": "string", "description": "限定区域 'x,y,w,h'"},
            },
            "required": ["method"],
        },
        timeout=90.0,
    )
    async def tool_click(self, method: str, query: str = "", template_path: str = "",
                         name: str = "", mode: str = "target", index: int = 0,
                         button: str = "left", clicks: int = 1, min_score: float = 0.75,
                         region: str = "", **_) -> Any:
        method = str(method or "").strip().lower()
        if method == "text":
            return await self.click_text(query=query, mode=mode, index=int(index),
                                         button=button, clicks=int(clicks), region=region)
        if method == "image":
            return await self.click_image(template_path=template_path, mode=mode,
                                          min_score=float(min_score), index=int(index),
                                          button=button, clicks=int(clicks))
        if method == "control":
            return await self.click_control(name=name, index=int(index))
        return _bad(f"未知 method：{method}（可选 text/image/control）")

    @llm_tool(
        name="keyboard_find",
        description=(
            "在屏幕上查找并返回坐标（不点击）：kind=text 按文字（OCR，query）；"
            "kind=image 按模板图（template_path，min_score 阈值）。"
            "mode='target' 目标窗口内/'fullscreen' 全屏；max_results 限制返回数；"
            "region 限定区域。返回 text/中心坐标 + 置信度，供 keyboard_click_window 等使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["text", "image"], "description": "查找类型"},
                "query": {"type": "string", "description": "kind=text：要找的文字"},
                "template_path": {"type": "string", "description": "kind=image：模板图片路径"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "description": "默认 target"},
                "max_results": {"type": "integer", "description": "最多返回几个（默认 5）"},
                "min_score": {"type": "number", "description": "kind=image：阈值（默认 0.75）"},
                "region": {"type": "string", "description": "限定区域 'x,y,w,h'"},
            },
            "required": ["kind"],
        },
        timeout=60.0,
    )
    async def tool_find(self, kind: str, query: str = "", template_path: str = "",
                        mode: str = "target", max_results: int = 5,
                        min_score: float = 0.75, region: str = "", **_) -> Any:
        kind = str(kind or "").strip().lower()
        if kind == "text":
            return await self.find_text(query=query, mode=mode,
                                        max_results=int(max_results), region=region)
        if kind == "image":
            return await self.find_image(template_path=template_path, mode=mode,
                                         min_score=float(min_score),
                                         max_results=int(max_results), region=region)
        return _bad(f"未知 kind：{kind}（可选 text/image）")

    # ── 截屏感知 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_screen",
        description=(
            "屏幕感知（op）：ocr 截图并 OCR 返回文字（include_boxes=true 附文字块坐标）；"
            "see 把真实截图推给多模态模型直接看图（token 消耗大，按需使用）。"
            "mode='target' 截目标窗口（须先设目标）/'fullscreen' 截全屏。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["ocr", "see"], "description": "ocr=文字识别 / see=视觉看屏"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "description": "默认 target"},
                "include_boxes": {"type": "boolean", "description": "op=ocr：返回文字块坐标（默认 false）"},
                "region": {"type": "string", "description": "op=ocr：限定 OCR 区域 'x,y,w,h'"},
            },
            "required": ["op"],
        },
        timeout=60.0,
    )
    async def tool_screen(self, op: str, mode: str = "target",
                          include_boxes: bool = False, region: str = "", **_) -> Any:
        op = str(op or "").strip().lower()
        if op == "ocr":
            return await self.capture_screen(mode=mode, include_boxes=include_boxes, region=region)
        if op == "see":
            return await self.see_screen(mode=mode)
        return _bad(f"未知 op：{op}（可选 ocr/see）")

    # ── 等待族 ───────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_wait_for",
        description=(
            "轮询等待某条件成立（kind，超时返回 found/stable=false 不报错）："
            "window 等窗口出现/消失（query，disappear=true 等消失）；"
            "text 等屏幕文字出现（query，OCR）；image 等模板图出现（template_path，min_score）；"
            "idle 等画面稳定（threshold 灰度差阈值，stable_count 连续稳定次数）。"
            "text/image/idle 支持 mode=target|fullscreen；timeout 最长秒数；interval 轮询间隔。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["window", "text", "image", "idle"],
                         "description": "等待条件类型"},
                "query": {"type": "string", "description": "kind=window|text：关键字/文字"},
                "template_path": {"type": "string", "description": "kind=image：模板图片路径"},
                "disappear": {"type": "boolean", "description": "kind=window：true=等消失"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "description": "默认 target"},
                "index": {"type": "integer", "description": "kind=text|image：第几个匹配"},
                "min_score": {"type": "number", "description": "kind=image：阈值（默认 0.75）"},
                "timeout": {"type": "number", "description": "最长等待秒数（默认 10~15，≤120）"},
                "interval": {"type": "number", "description": "轮询间隔秒数（默认 0.5）"},
                "threshold": {"type": "number", "description": "kind=idle：灰度差阈值（默认 3）"},
                "stable_count": {"type": "integer", "description": "kind=idle：连续稳定次数（默认 2）"},
            },
            "required": ["kind"],
        },
        timeout=150.0,
    )
    async def tool_wait_for(self, kind: str, query: str = "", template_path: str = "",
                            disappear: bool = False, mode: str = "target", index: int = 0,
                            min_score: float = 0.75, timeout: float = 0,
                            interval: float = 0.5, threshold: float = 3.0,
                            stable_count: int = 2, **_) -> Any:
        kind = str(kind or "").strip().lower()
        if kind == "window":
            return await self.wait_for_window(query=query, disappear=disappear,
                                              timeout=float(timeout or 15.0), interval=float(interval))
        if kind == "text":
            return await self.wait_for_text(query=query, mode=mode, index=int(index),
                                            timeout=float(timeout or 10.0), interval=float(interval))
        if kind == "image":
            return await self.wait_for_image(template_path=template_path, mode=mode,
                                             min_score=float(min_score), index=int(index),
                                             timeout=float(timeout or 10.0), interval=float(interval))
        if kind == "idle":
            return await self.wait_for_idle(mode=mode, timeout=float(timeout or 15.0),
                                            interval=float(interval), threshold=float(threshold),
                                            stable_count=int(stable_count))
        return _bad(f"未知 kind：{kind}（可选 window/text/image/idle）")

    # ── 窗口控制 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_window",
        description=(
            "控制目标窗口（action）：minimize/maximize/restore/close/pin 置顶/unpin 取消置顶；"
            "move 移动或缩放窗口（x,y 必填，width/height 可选，屏幕像素）；"
            "rect 返回窗口位置大小与客户区原点（供窗口内相对坐标点击用）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["minimize", "maximize", "restore", "close", "pin", "unpin",
                                    "move", "rect"],
                           "description": "窗口操作"},
                "x": {"type": "integer", "description": "action=move：新位置 x"},
                "y": {"type": "integer", "description": "action=move：新位置 y"},
                "width": {"type": "integer", "description": "action=move：新宽度"},
                "height": {"type": "integer", "description": "action=move：新高度"},
            },
            "required": ["action"],
        },
        timeout=15.0,
    )
    async def tool_window(self, action: str, x: int | None = None, y: int | None = None,
                          width: int | None = None, height: int | None = None, **_) -> Any:
        action = str(action or "").strip().lower()
        if action in ("minimize", "maximize", "restore", "close", "pin", "unpin"):
            return await self.control_window(action=action)
        if action == "move":
            if x is None or y is None:
                return _bad("action=move 需要 x, y")
            return await self.move_window(x=int(x), y=int(y),
                                          width=None if width is None else int(width),
                                          height=None if height is None else int(height))
        if action == "rect":
            return await self.get_window_rect()
        return _bad(f"未知 action：{action}")

    # ── UIA 控件 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_inspect_controls",
        description=(
            "枚举目标窗口内控件（类名/文本/中心坐标），供 keyboard_click_window 精准点击。"
            "backend='auto' 先 UIA（覆盖 Win32/WPF/UWP/Qt/浏览器）后回退 Win32 枚举。"
            "max_results 限制数量（默认 30）。须先设目标。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "include_empty": {"type": "boolean", "description": "仅 win32 回退路径生效"},
                "max_results": {"type": "integer", "description": "默认 30（≤100）"},
                "backend": {"type": "string", "enum": ["auto", "uia", "win32"], "description": "默认 auto"},
            },
        },
        timeout=90.0,
    )
    async def tool_inspect_controls(self, include_empty: bool = False, max_results: int = 30,
                                    backend: str = "auto", **_) -> Any:
        return await self.inspect_controls(include_empty=include_empty,
                                           max_results=int(max_results), backend=backend)

    @llm_tool(
        name="keyboard_control_action",
        description=(
            "对目标窗口内按名匹配的控件执行操作（op，UIA，无需焦点后台执行）："
            "click Invoke 点击；get_value 读输入框当前值；set_text 写入输入框（text 参数，"
            "不触发输入法）；expand 展开下拉；collapse 收起；select 选中列表项；"
            "scrollinto 滚动到可见。name 为控件文本/AutomationId 子串，index 同名多选一。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string",
                       "enum": ["click", "get_value", "set_text", "expand", "collapse",
                                "select", "scrollinto"],
                       "description": "控件操作"},
                "name": {"type": "string", "description": "控件文本子串"},
                "text": {"type": "string", "description": "op=set_text：要写入的内容"},
                "index": {"type": "integer", "description": "同名第几个（默认 0）"},
            },
            "required": ["op", "name"],
        },
        timeout=90.0,
    )
    async def tool_control_action(self, op: str, name: str, text: str = "",
                                  index: int = 0, **_) -> Any:
        op = str(op or "").strip().lower()
        if op == "click":
            return await self.click_control(name=name, index=int(index))
        if op == "get_value":
            return await self.get_control_value(name=name, index=int(index))
        if op == "set_text":
            return await self.set_control_text(name=name, text=text, index=int(index))
        if op in ("expand", "collapse", "select", "scrollinto"):
            return await self.control_pattern(name=name, action=op, index=int(index))
        return _bad(f"未知 op：{op}")

    # ── 剪贴板 ───────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_clipboard",
        description=(
            "剪贴板读写（op）：get 读取当前文本（配合 press 'ctrl+a'+'ctrl+c' 可精确提取"
            " OCR 拿不准的原文）；set 写入文本 text（配合 'ctrl+v' 粘贴长文本）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["get", "set"], "description": "读或写"},
                "text": {"type": "string", "description": "op=set：要写入的文本"},
            },
            "required": ["op"],
        },
        timeout=10.0,
    )
    async def tool_clipboard(self, op: str, text: str = "", **_) -> Any:
        op = str(op or "").strip().lower()
        if op == "get":
            return await self.get_clipboard()
        if op == "set":
            return await self.set_clipboard(text=text)
        return _bad(f"未知 op：{op}（可选 get/set）")

    # ── 文件 ─────────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_file",
        description=(
            "工作区文件操作（op，工作区默认 用户\\Documents）：list 列目录（path 留空=根）；"
            "read 读文本（自动 utf-8/gbk，start_line/line_count 分段）；"
            "write 写文件（path+content，append=true 追加否则覆盖）。"
            "配合 keyboard_run_command 实现 vibe-coding 迭代。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["list", "read", "write"], "description": "文件操作"},
                "path": {"type": "string", "description": "相对工作区或工作区内绝对路径"},
                "content": {"type": "string", "description": "op=write：完整文本内容"},
                "append": {"type": "boolean", "description": "op=write：true=追加（默认覆盖）"},
                "start_line": {"type": "integer", "description": "op=read：起始行（默认 1）"},
                "line_count": {"type": "integer", "description": "op=read：行数（0=读到末尾）"},
            },
            "required": ["op", "path"],
        },
        timeout=20.0,
    )
    async def tool_file(self, op: str, path: str = "", content: str = "",
                        append: bool = False, start_line: int = 1,
                        line_count: int = 0, **_) -> Any:
        op = str(op or "").strip().lower()
        if op == "list":
            return await self.list_files(path=path)
        if op == "read":
            return await self.read_file(path=path, start_line=int(start_line),
                                        line_count=int(line_count))
        if op == "write":
            return await self.write_file(path=path, content=content, append=append)
        return _bad(f"未知 op：{op}（可选 list/read/write）")

    # ── 命令执行 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_run_command",
        description=(
            "执行一条 shell 命令并返回输出。shell 可选 auto/cmd/powershell；cwd 指定工作目录。"
            "有超时保护与输出截断。确认模式开启时不会立即执行，请提示用户到面板确认。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "shell": {"type": "string", "enum": ["auto", "cmd", "powershell"], "description": "默认 auto"},
                "cwd": {"type": "string", "description": "工作目录，留空=默认"},
            },
            "required": ["command"],
        },
        timeout=60.0,
    )
    async def tool_run_command(self, command: str, shell: str = "auto", cwd: str = "", **_) -> Any:
        return await self.run_command(command=command, shell=shell, cwd=cwd)

    @llm_tool(
        name="keyboard_set_command_whitelist",
        description=(
            "设置命令自动放行前缀白名单（逗号分隔，如 'dir,ls,git status'，空串清空）："
            "匹配前缀的命令跳过用户确认直接执行。仅影响 keyboard_run_command。"
        ),
        parameters={
            "type": "object",
            "properties": {"prefixes": {"type": "string", "description": "逗号分隔前缀；空串=清空"}},
            "required": ["prefixes"],
        },
        timeout=10.0,
    )
    async def tool_set_command_whitelist(self, prefixes: str, **_) -> Any:
        return await self.set_command_whitelist(prefixes=prefixes)

    # ── 启动应用 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_launch_app",
        description=(
            "启动应用并自动设为操作目标（启动→等窗口→set_target 一步完成）。"
            "command 如 'notepad'、'calc' 或完整路径；window_query 为窗口关键字（留空=命令首词）。"
            "直接执行不走确认队列，仅用于可信应用；配置 launch_app_enabled=false 可禁用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "启动命令"},
                "window_query": {"type": "string", "description": "窗口关键字，留空=命令首词"},
                "timeout": {"type": "number", "description": "最长等待秒数（默认 15，≤60）"},
            },
            "required": ["command"],
        },
        timeout=90.0,
    )
    async def tool_launch_app(self, command: str, window_query: str = "",
                              timeout: float = 15.0, **_) -> Any:
        return await self.launch_app(command=command, window_query=window_query,
                                     timeout=float(timeout))

    # ── 任务状态与上下文 ─────────────────────────────────────────
    @llm_tool(
        name="keyboard_task_state",
        description=(
            "持久化任务状态（外部工作记忆，action）：set 更新 goal/status/next_step"
            "（只传需更新字段，clear=true 清空）；get 读取。长任务每完成一步更新一次，"
            "上下文被压缩后用它恢复进度。每字段上限 500 字符。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set"], "description": "读或写"},
                "goal": {"type": "string", "description": "action=set：总目标"},
                "status": {"type": "string", "description": "action=set：当前进展"},
                "next_step": {"type": "string", "description": "action=set：下一步"},
                "clear": {"type": "boolean", "description": "action=set：true=清空"},
            },
            "required": ["action"],
        },
        timeout=10.0,
    )
    async def tool_task_state(self, action: str, goal: str = "", status: str = "",
                              next_step: str = "", clear: bool = False, **_) -> Any:
        action = str(action or "").strip().lower()
        if action == "get":
            return await self.get_task_state()
        if action == "set":
            return await self.set_task_state(goal=goal, status=status,
                                             next_step=next_step, clear=clear)
        return _bad(f"未知 action：{action}（可选 get/set）")

    @llm_tool(
        name="keyboard_context",
        description=(
            "操作态势与历史（kind）：brief 一览（目标窗口/全屏、聚焦、安全开关、能力可用性、"
            "今日统计与最近动作）——开始任务或上下文丢失时调一次即可重新定向；"
            "recent 最近 n 条操作事件（跨轮次恢复'刚做了什么'）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["brief", "recent"], "description": "brief=态势 / recent=历史"},
                "n": {"type": "integer", "description": "kind=recent：条数（默认 10，≤50）"},
            },
            "required": ["kind"],
        },
        timeout=20.0,
    )
    async def tool_context(self, kind: str, n: int = 10, **_) -> Any:
        kind = str(kind or "").strip().lower()
        if kind == "brief":
            return await self.agent_brief()
        if kind == "recent":
            return await self.recent_actions(n=int(n))
        return _bad(f"未知 kind：{kind}（可选 brief/recent）")

    # ── 配置类 ───────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_set_input_speed",
        description=(
            "切换全局输入速度档位并持久化：slow 慢（老游戏/远程桌面）、normal 标准、"
            "fast 快、turbo 极速（连招）。影响按键间隔与打字速度。"
        ),
        parameters={
            "type": "object",
            "properties": {"profile": {"type": "string", "enum": ["slow", "normal", "fast", "turbo"],
                                       "description": "速度档位"}},
            "required": ["profile"],
        },
        timeout=10.0,
    )
    async def tool_set_input_speed(self, profile: str, **_) -> Any:
        return await self.set_input_speed(profile=profile)

    @llm_tool(
        name="keyboard_set_window_watch",
        description=(
            "设置窗口监视名单（反应式，逗号分隔标题/进程名关键字，空串清空）："
            "名单内窗口出现/关闭时自动推送通知进上下文。适合'用户打开某应用后提醒我'。"
        ),
        parameters={
            "type": "object",
            "properties": {"titles": {"type": "string", "description": "逗号分隔关键字；空串=清空"}},
            "required": ["titles"],
        },
        timeout=10.0,
    )
    async def tool_set_window_watch(self, titles: str, **_) -> Any:
        return await self.set_window_watch(titles=titles)

    @llm_tool(
        name="keyboard_analyze_audio",
        description=(
            "监听电脑正在播放的声音并分析频谱（音量 dB/频谱质心/低中高频占比/最集中频率/"
            "音调性 + 人话解读），让非视觉模型'听到'主机在响什么。duration 秒（默认 4，≤15）。"
        ),
        parameters={
            "type": "object",
            "properties": {"duration": {"type": "number", "description": "监听秒数（默认 4，≤15）"}},
        },
        timeout=30.0,
    )
    async def tool_analyze_audio(self, duration: float = 0, **_) -> Any:
        return await self.analyze_audio(duration=float(duration or 0))

    # ── 日记 ─────────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_diary",
        description=(
            "操作日记（action）：status 查看今日日记状态与事件计数；write 立即把今天操作"
            "整理成 Markdown 落盘；read 读某天日记（date=YYYY-MM-DD，留空今天）；"
            "note 往今天日记追加一条随笔（detail 正文）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "write", "read", "note"],
                           "description": "日记操作"},
                "date": {"type": "string", "description": "action=read：日期 YYYY-MM-DD，留空=今天"},
                "detail": {"type": "string", "description": "action=note：随笔正文"},
            },
            "required": ["action"],
        },
        timeout=20.0,
    )
    async def tool_diary(self, action: str, date: str = "", detail: str = "", **_) -> Any:
        action = str(action or "").strip().lower()
        if action == "status":
            return await self.diary_status()
        if action == "write":
            return await self.diary_write_now()
        if action == "read":
            return await self.diary_read(date=date)
        if action == "note":
            return await self.diary_note(detail=detail)
        return _bad(f"未知 action：{action}（可选 status/write/read/note）")

    # ── 屏幕高级感知 ─────────────────────────────────────────────
    @llm_tool(
        name="keyboard_screen_advanced",
        description=(
            "屏幕高级感知（op）：color 检测像素色值/搜索色块（x,y 单点或 target RGB+tolerance 搜索）；"
            "diff 比较两帧返回变化区域（需先截两帧）；multi_match 多尺度模板匹配（scales 缩放比列表）；"
            "gif 录屏控制（action=start/stop/status）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string",
                       "enum": ["color", "diff", "multi_match", "gif"],
                       "description": "操作类型"},
                # color
                "x": {"type": "integer", "description": "op=color：像素 x"},
                "y": {"type": "integer", "description": "op=color：像素 y"},
                "target": {"type": "array", "items": {"type": "integer"},
                           "description": "op=color：目标色 [R,G,B]"},
                "tolerance": {"type": "integer", "description": "op=color：容差（默认 8）"},
                "max_results": {"type": "integer", "description": "op=color：最多返回"},
                "region": {"type": "string", "description": "限定区域 'x,y,w,h'"},
                # diff
                "threshold": {"type": "integer", "description": "op=diff：灰度差阈值（默认 16）"},
                "min_area": {"type": "integer", "description": "op=diff：最小变化面积（默认 64）"},
                # multi_match
                "template_path": {"type": "string", "description": "op=multi_match：模板图路径"},
                "scales": {"type": "array", "items": {"type": "number"},
                           "description": "op=multi_match：缩放比列表"},
                "min_score": {"type": "number", "description": "op=multi_match：阈值（默认 0.75）"},
                # gif
                "gif_action": {"type": "string", "enum": ["start", "stop", "status"],
                               "description": "op=gif：录屏控制"},
                "fps": {"type": "number", "description": "op=gif：帧率（默认 10）"},
                "save_path": {"type": "string", "description": "op=gif：保存路径"},
            },
            "required": ["op"],
        },
        timeout=60.0,
    )
    async def tool_screen_advanced(self, op: str, x: int | None = None, y: int | None = None,
                                   target: list | None = None, tolerance: int = 8,
                                   max_results: int = 50, region: str = "",
                                   threshold: int = 16, min_area: int = 64,
                                   template_path: str = "", scales: list | None = None,
                                   min_score: float = 0.75,
                                   gif_action: str = "status", fps: float = 10.0,
                                   save_path: str = "", **_) -> Any:
        op = str(op or "").strip().lower()
        if op == "color":
            return await self._screen_color(x=x, y=y, target=target, tolerance=tolerance,
                                            max_results=max_results, region=region)
        if op == "diff":
            return await self._screen_diff(threshold=threshold, min_area=min_area)
        if op == "multi_match":
            return await self._screen_multi_match(template_path=template_path, scales=scales,
                                                  min_score=min_score)
        if op == "gif":
            return await self._screen_gif(action=gif_action, fps=fps, save_path=save_path)
        return _bad(f"未知 op：{op}（可选 color/diff/multi_match/gif）")

    # ── 手柄模拟 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_gamepad",
        description=(
            "虚拟手柄控制（op，Windows）：button 按下/长按手柄按钮（a/b/x/y/dpad/start/back/"
            "shoulder/thumb，hold_seconds 长按）；stick 设置摇杆位置（left_x/left_y/right_x/right_y，"
            "-1~1）；trigger 设置扳机（left/right 0~1）。list 列出支持的按钮。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["button", "stick", "trigger", "list", "status"],
                       "description": "操作"},
                "button": {"type": "string", "description": "op=button：按钮名"},
                "hold_seconds": {"type": "number", "description": "op=button：长按秒数（默认 0.1）"},
                "left_x": {"type": "number", "description": "op=stick：左摇杆 x（-1~1）"},
                "left_y": {"type": "number", "description": "op=stick：左摇杆 y（-1~1）"},
                "right_x": {"type": "number", "description": "op=stick：右摇杆 x（-1~1）"},
                "right_y": {"type": "number", "description": "op=stick：右摇杆 y（-1~1）"},
                "left": {"type": "number", "description": "op=trigger：左扳机 0~1"},
                "right": {"type": "number", "description": "op=trigger：右扳机 0~1"},
            },
            "required": ["op"],
        },
        timeout=15.0,
    )
    async def tool_gamepad(self, op: str, button: str = "", hold_seconds: float = 0.1,
                           left_x: float = 0, left_y: float = 0,
                           right_x: float = 0, right_y: float = 0,
                           left: float = 0, right: float = 0, **_) -> Any:
        return await self._gamepad_dispatch(op=op, button=button, hold_seconds=hold_seconds,
                                            left_x=left_x, left_y=left_y,
                                            right_x=right_x, right_y=right_y,
                                            left=left, right=right)

    # ── 进程管理 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_process",
        description=(
            "进程管理（op）：list 列出进程（query 关键字过滤，max_results 上限）；"
            "info 查某 PID 详情（名称/内存/命令行/窗口标题）；"
            "kill 终止进程（force=true 强制）；"
            "wait 等待进程退出（timeout 秒）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["list", "info", "kill", "wait"],
                       "description": "操作"},
                "query": {"type": "string", "description": "op=list|info：进程名关键字"},
                "pid": {"type": "integer", "description": "op=info|kill|wait：进程 PID"},
                "force": {"type": "boolean", "description": "op=kill：true=强制终止"},
                "timeout": {"type": "number", "description": "op=wait：最长等待秒数（默认 30）"},
                "max_results": {"type": "integer", "description": "op=list：最多返回（默认 50）"},
            },
            "required": ["op"],
        },
        timeout=30.0,
    )
    async def tool_process(self, op: str, query: str = "", pid: int | None = None,
                           force: bool = False, timeout: float = 30.0,
                           max_results: int = 50, **_) -> Any:
        return await self._process_dispatch(op=op, query=query, pid=pid, force=force,
                                            timeout=timeout, max_results=max_results)

    # ── 网络请求 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_network",
        description=(
            "网络请求（op）：http 发送 HTTP 请求（method=GET/POST/HEAD，可选 headers/body/json，"
            "返回状态码/响应头/正文截断）；download 下载文件到工作区 path；ping 测试主机连通性。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["http", "download", "ping"],
                       "description": "操作"},
                "url": {"type": "string", "description": "op=http|download：URL"},
                "method": {"type": "string", "enum": ["GET", "POST", "HEAD", "PUT", "DELETE", "PATCH"],
                           "description": "op=http：HTTP 方法"},
                "headers": {"type": "object", "description": "op=http：额外请求头"},
                "body": {"type": "string", "description": "op=http：请求正文"},
                "json_body": {"type": "object", "description": "op=http：JSON 请求体"},
                "path": {"type": "string", "description": "op=download：保存路径"},
                "host": {"type": "string", "description": "op=ping：主机名或 IP"},
                "count": {"type": "integer", "description": "op=ping：发包数（默认 4）"},
                "timeout": {"type": "number", "description": "超时秒数（默认 15）"},
            },
            "required": ["op"],
        },
        timeout=60.0,
    )
    async def tool_network(self, op: str, url: str = "", method: str = "GET",
                           headers: dict | None = None, body: str = "",
                           json_body: Any = None, path: str = "",
                           host: str = "", count: int = 4,
                           timeout: float = 15.0, **_) -> Any:
        return await self._network_dispatch(op=op, url=url, method=method,
                                            headers=headers, body=body,
                                            json_body=json_body, path=path,
                                            host=host, count=count, timeout=timeout)

    # ── 系统通知 ─────────────────────────────────────────────────
    @llm_tool(
        name="keyboard_notify",
        description=(
            "弹出系统通知（Windows Toast / Linux notify-send）。title 标题 + body 正文；"
            "icon 可选 info/warning/error。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "通知标题"},
                "body": {"type": "string", "description": "通知正文"},
                "icon": {"type": "string", "enum": ["info", "warning", "error"],
                         "description": "图标类型"},
            },
            "required": ["title"],
        },
        timeout=10.0,
    )
    async def tool_notify(self, title: str, body: str = "", icon: str = "info", **_) -> Any:
        return await self._show_notification(title=title, body=body, icon=icon)

    # ── 条件触发器 ───────────────────────────────────────────────
    @llm_tool(
        name="keyboard_trigger",
        description=(
            "条件触发器管理（action）：add 创建触发器（condition=color/change/text + action=press/"
            "click/notify/run_command/sequence + 对应参数）；remove 删除；list 列出全部；"
            "enable/disable 开关；clear 清空。触发器在后台轮询，条件满足时自动执行动作。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["add", "remove", "list", "enable", "disable", "clear"],
                           "description": "触发器操作"},
                "trigger_id": {"type": "string", "description": "触发器 ID"},
                "condition": {"type": "string", "enum": ["color", "change"],
                              "description": "action=add：条件类型"},
                "cond_params": {"type": "object", "description": "action=add：条件参数"},
                "act": {"type": "string", "description": "action=add：执行动作类型"},
                "act_params": {"type": "object", "description": "action=add：动作参数"},
                "cooldown_s": {"type": "number", "description": "action=add：冷却秒数"},
                "max_fires": {"type": "integer", "description": "action=add：最大触发次数"},
            },
            "required": ["action"],
        },
        timeout=15.0,
    )
    async def tool_trigger(self, action: str, trigger_id: str = "",
                           condition: str = "", cond_params: dict | None = None,
                           act: str = "", act_params: dict | None = None,
                           cooldown_s: float = 0, max_fires: int = 0, **_) -> Any:
        return await self._trigger_dispatch(action=action, trigger_id=trigger_id,
                                            condition=condition, cond_params=cond_params,
                                            act=act, act_params=act_params,
                                            cooldown_s=cooldown_s, max_fires=max_fires)

    # ── 宏录制与回放 ─────────────────────────────────────────────
    @llm_tool(
        name="keyboard_macro",
        description=(
            "操作宏录制与回放（action）：record_start 开始录制（name 宏名）；"
            "record_step 记录一步操作（type=press/type/mouse_click/keyboard_sequence 等，"
            "params 参数，delay 该步后等待秒数）；record_stop 停止并返回宏 JSON；"
            "record_cancel 取消录制；play 回放宏文件（path 宏 JSON 路径）或直接传 macro JSON；"
            "list 列出宏模板（templates）；template 使用模板创建宏（template_name + params）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["record_start", "record_step", "record_stop", "record_cancel",
                                    "play", "list", "template"],
                           "description": "宏操作"},
                "name": {"type": "string", "description": "宏名称"},
                "step_type": {"type": "string", "description": "action=record_step：步骤类型"},
                "step_params": {"type": "object", "description": "action=record_step：步骤参数"},
                "delay": {"type": "number", "description": "action=record_step：该步后等待秒数"},
                "path": {"type": "string", "description": "action=play：宏 JSON 文件路径"},
                "macro_json": {"type": "object", "description": "action=play：直接传宏 JSON"},
                "template_name": {"type": "string", "description": "action=template：模板名"},
                "template_params": {"type": "object", "description": "action=template：模板参数"},
                "repeat": {"type": "integer", "description": "action=play：循环次数"},
                "repeat_delay": {"type": "number", "description": "action=play：循环间延迟"},
            },
            "required": ["action"],
        },
        timeout=60.0,
    )
    async def tool_macro(self, action: str, name: str = "",
                         step_type: str = "", step_params: dict | None = None,
                         delay: float = 0, path: str = "",
                         macro_json: dict | None = None,
                         template_name: str = "", template_params: dict | None = None,
                         repeat: int = 1, repeat_delay: float = 0.5, **_) -> Any:
        return await self._macro_dispatch(action=action, name=name,
                                          step_type=step_type, step_params=step_params,
                                          delay=delay, path=path, macro_json=macro_json,
                                          template_name=template_name,
                                          template_params=template_params,
                                          repeat=repeat, repeat_delay=repeat_delay)
