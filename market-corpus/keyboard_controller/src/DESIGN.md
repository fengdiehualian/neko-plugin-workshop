# keyboard_controller 设计笔记

> 平台契约要求的设计文档。记录架构决策、安全模型与演进路线。

## 身份

- plugin_id: `keyboard_controller`
- 概念：给 LLM 猫娘装上"手、眼、耳、记忆"——键鼠注入 + 屏幕感知 + 命令/文件执行 + 音频监听 + 上下文管理
- 入口：`KeyboardControllerPlugin`（`plugin.plugins.keyboard_controller:__init__`）

## 架构

```
__init__.py            工具编排层（~90 方法，@llm_tool/@ui.action/@plugin_entry）
 ├─ _input_backend.py  平台后端选择器
 │   ├─ _win32_input.py   Windows: SendInput / Win32 / WASAPI
 │   └─ _linux_input.py   Linux X11: XTest / EWMH / xclip|wl-clipboard
 ├─ _screen_capture.py 截图（PrintWindow 后台优先 → mss 回退）+ OCR 调度
 │                       （RapidOCR 全功能 / Windows.Media.Ocr 快速通道）
 ├─ _template_match.py numpy NCC 模板匹配（无 OpenCV）
 ├─ _command_exec.py   shell 执行（PS EncodedCommand / bash）
 ├─ _file_ops.py       工作区沙箱读写
 ├─ _audio_analysis.py Win WASAPI loopback / Linux parec monitor → FFT 特征
 └─ _diary.py          日志 → Markdown 日记
ui/panel.tsx           dashboard 控制面板
```

### 关键决策记录

| 决策 | 理由 |
|---|---|
| 后端同构接口 + 引用名兼容（`win32.*`） | 工具层零改动接入 Linux；parity 测试防符号漂移 |
| UIA 经 PowerShell/.NET 而非裸 COM | vtable 手写有宿主崩溃风险；PS 零依赖且可独立冒烟 |
| UIA 只走两级遍历 + 坐标异常过滤 | 实测内容岛深层元素返回异空间坐标 |
| llm_tool 返回 JSON 字符串，图像走 push_message | 宿主把工具结果压成字符串；push 支持多模态 parts |
| 安全开关不注册 @llm_tool | 猫娘不能自己关护栏；仅面板/UI 动作可控 |
| 输入强度上限在工具层收口 | 防 LLM 参数失控占用真实键鼠 |
| 异步体内 win32 一律 to_thread | EnumWindows/聚焦重试可达秒级，阻塞事件循环会冻结局域插件进程 |

## 安全模型

四道闸门（均可独立开关，默认全开）：目标锁定 → 反作弊黑名单 → 提权隔离 → 焦点校验。
命令确认队列（token/TTL/上限）+ 白名单免确认（支持 `re:` 正则）。
全流程日记留痕。

## 已知限制 / 演进路线

1. **Router 重构**：主文件超 4000 行（契约建议 >1k 拆分）。按域拆 routers/，
   纯机械但需完整回归窗口期。
2. **UIA 事件订阅**：现为 10s 轮询监视（reactive_window_watch）；真·事件订阅
   需 AddAutomationEventHandler 生命周期管理，收益待验证。
3. **多窗口会话**：单 target 锁定改为命名集合，涉及全部工具签名，动架构。
4. **Linux 音频**：parec 单声道 44.1k 已够用；PipeWire 原生 API 待评估。
5. **面板卡片**：速度档/白名单/任务状态尚无 UI 卡片（工具已就绪），前端联调环境就绪后补。
