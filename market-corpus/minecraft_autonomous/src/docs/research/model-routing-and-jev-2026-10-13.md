# Neko 模型配置与 Jev 兼容核查

核查日期：2026-10-13。插件 core pin 仍为 `7889ce261a2309d94400b739c6198468d8271d83`；
上游 main 已 fetch 至 `40eb7eea951e5569a68864feb8febfa48052c996`，本次未切换核心或复制上游身体控制循环。

## 协议与来源

TypeSafe 官方接口是 `POST https://api.typesafe.ai/v1/systemone`，以 state、model 和 typed questions
交换 structured answers；不是 Chat Completions。choice 提供选项、完整概率分布和独立 confidence。
不能拿所选概率直接替代 confidence。[官方 API](https://docs.typesafe.ai/api)、
[confidence 语义](https://docs.typesafe.ai/confidence)、[发布说明](https://typesafe.ai/blog/introducing-system-one-models-and-jev)。

用户仓库的 `patches/decider/README.md` 指向开源 Mapika/Decider 固定提交 `a59466d`，
包含模型结构/校准补丁约束；这与「官方 Jev 开源权重」是不同结论。
`docs/SYSTEM-ONE-OFFICIAL-JEV.md` 记录后续切到官方服务，本地端保留给其他用途。
本插件接受显式远程 HTTPS 或 loopback HTTP 兼容端点，不部署/下载权重，不探测历史端口，
不读取该仓库私有 API key。来源：[用户仓库](https://github.com/jcs130/minecraft-ai-friend/tree/40eb7eea951e5569a68864feb8febfa48052c996)。

## Neko 公共边界

本机 Neko 的 `docs/design/plugin-model-slots.md`、`docs/plugins/hosted-ui.md`、
`plugin/sdk/shared/core/models.py` 和 `plugin/server/domain/model_config.py` 表明：
公开用途槽支持 OpenAI Chat/Anthropic Messages，能力绑定由宿主检查；没有 System One 协议槽。
插件通过公共 `ctx.models.get_client()` 调用别名，不能把 Jev 专用接口伪装成普通 chat 模型。

因此内嵌规划使用 decision/vision/fast 用途槽；专用 System One 采用插件自己的显式、可关闭适配器。
凭据只存环境变量，配置保存变量名。只返回经过校验的 advisory focus；物理动作、队列、反射和
原生账本仍属于 core。普通聊天模型的 fast 置信度只是自报数值，不能当成 Jev 校准效果。

## 本次验证和未验证范围

- 离线：别名路由、能力/绑定错误、图像时效与尾部放置、格式错误、概率/置信度差异、规则回退、
  有界审计、无副作用设置保存和独立框架导入；测试禁止真实模型、游戏或进程启动。
- 在线：通过实际插件 Hosted action → 公共 SDK gateway → 本机配置模型，验证文字工具调用、
  缺少 vision 时文字回退、同一模型绑定 vision 后的合成色块识别和结构化输出。未发送游戏动作。
- 当前游戏会话使用外部独立配置，快选择为 rules。未调用付费 Jev，未启动本地 Decider。
- Hosted context/settings API 和 TSX 类型检查已核对。工具当前无可连接浏览器，页面视觉验收待确认。
- 自动游戏解说、长期生存、现代 3D 画面与当前身体的绑定仍是独立验收项；色块识别不能替代它们。

## 重启后的现场记录

3080 Ti 移除后，本地 VoxCPM2 与 IndexTTS 均保持 disabled，WSL 未启动。
官方 Lanlan free 在独立队列产生 328680 字节 PCM；桌面自然语音另有 11 帧、823360 字节的
delivered/voice_play_end 同一语音回执。此记录不含音色 ID、鉴权或个人音频。

1.21.1 测试服保持独立，模型仍在 RTX 3090。发现规划器收到空响应却报告成功，且提示压缩
遗漏了侦察字段的角度与体积语义。新增拒绝空响应、保留数值字段短说明、重要生存事实优先，
并将脚本/制品完整 schema 改为按需读取。原始说明与回执仍可查询。
实际会话恢复了有效射线与方块侦察；自然语言插入上岸目标后，位置由河流 (-52,62,282)
变为森林干地 (-61,63,291)。这证明插入目标与实际位移，不等同于长期自主生存通过。
随后库存实际出现橡木原木、橡木木板、木镐和木锹；插入目标退出后恢复了长期生存目标。
