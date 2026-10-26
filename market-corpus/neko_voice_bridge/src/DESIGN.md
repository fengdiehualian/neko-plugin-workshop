# 插件设计约束

- 身份：目录/id `neko_voice_bridge`；名称“猫娘语音频道桥”；入口 `plugin.plugins.neko_voice_bridge:NekoVoiceBridgePlugin`。
- 类型：普通 Plugin，自动启动后台、Hosted TSX 连接面板；平台连接属于独立 Node 进程。
- 边界：`bridge_client.py` 只访问鉴权回环 HTTP；`worker.py` 筛选事件与顺序回复；`neko_backend.py` 承担对话兼容；`neko_speech.py` 分流免费和独立服务；免费由 `host_voice.py` 接入主服务前端 WebSocket，自备 API 仍复用宿主 ASR/Realtime/TTS 调度；`speech_worker.py` 独立拉取语音任务，密钥不离开宿主。
- 会话：每个桥进程只配置一间房；epoch 变化或事件丢失时重建会话，不将旧回复送回新连接。
- 身份：保留 peer/presence/平台 ID（如人工提供）/显示名/可信状态；昵称不是权限依据。
- 控制：当前猫娘称呼用于显示和频道回复过滤，首次弹窗说明；未知来源不回复；人工映射需单独开关。
- 限制：尚无真实平台或完整宿主语音验收，OOPZ 未实现；原生 ASR/TTS 接口按固定上游版本适配。

- 同步：`connection.py` 监督连接，每秒心跳；桥分配进程实例 ID，重启后重建 worker；称呼/人格/音色配置修订后取消旧会话。面板与 EXE 读取同一状态。
- 面板：`ui_pair/ui_control` 是 SDK 内部动作，通过面板随机令牌保护，不提供手动 start/status/stop 使用流程。

- 0.5.0 免费路由：本体完整会话拥有角色、鉴权、模型及 TTS。插件不生成/伪装免费服务首包，不直连免费模型。新版 EXE 接收原始 host_reply，标记 host_handled，禁止再调用独立对话/TTS。
- 免费会话边界：共用桌面角色会话、记忆和工具设置；使用当前猫娘称呼控制频道播放；首次连接弹窗告知用户。SDK read 上下文只确认提交，不视为昵称与音频关联成功。界面明确说明，与自备 API 的隔离链路区别。
- 收发界限：匹配请求的 session_started 后发 PCM；同时收到本轮转写、turn end、匹配 speech_id 的 audio_done 后才能返回。切换/取消关闭本地 socket，禁止全局 end_session。拒绝 Ogg、多 speech_id 混合和超长输出。

- 0.6.0 虚拟音频线：EXE 显式选择系统 VB-CABLE 录音端，仅在独立可信采集窗口授权音频；KOOK 网页原生麦克风仍拒绝。新任务带 output_transport=cable，插件通过带鉴权的 /v1/speech/progress 发送转写并轮询采集结果，维持本体会话到采集完成，不等 WebSocket 回复音频。EXE 缓冲结果仍走原有音轨身份/称呼/静音/epoch 过滤再发频道，禁止另调 LLM/TTS。采集错误和停止会丢弃缓冲；旧模式保持不变。
- 系统线无角色/句子 ID，只供当前猫娘独占；2.5 秒静音分句、30 秒上限可能截断长停顿，UI 和说明书明确标注，真实 Windows 仍待验证。

- 0.6.1：EXE 持久化 wake_filter_enabled，事件携带同一规则，worker 对非 host_handled 事件使用该值；关闭只取消称呼要求，不取消身份/静音/会话约束。cable 模式保留并竞争同轮完整原生 PCM，返回 output_transport=host_pcm 或 cable，单次结果防止重复播放。默认仍开启过滤。

- 0.7.0 安装版：插件标准包继续独立交付，心跳携带 plugin_version，供 EXE 在启动接收前检查版本。未修改猫娘宿主、语音或权限接口。

- 0.7.1：多人频道规则见 channel_context.py，按显示昵称提交当前说话人，要求回复其他成员先叫昵称。本人称呼沿用人类档案，不新增账号 ID 或绑定功能。SDK coalesce_key 合并未消费提示，完成/取消后提交结束提示；不冒充消费确认，不宣称本体记忆隔离。
