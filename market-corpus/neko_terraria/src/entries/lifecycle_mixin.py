"""生命周期：启动 Agent + 自主大脑 + 交互引擎，关闭时清理。

v2.0 改进：
- 交互引擎 InteractionEngine 由 AutonomousBrain 内部管理，brain.start() 自动拉起
- LLMIntentParser 由 TaskCoordinator 内部管理，通过 _wire_llm_integration() 注入 LLM 调用
- executor 回调钩子由 brain.start() 注册，连通 task_done/step_done/interrupted → 交互引擎
- 交互相关配置项追加到 _load_config 白名单
- v2.1: 交互引擎通过 agent._neko_interaction 暴露给 service，service 紧急事件走引擎管道
- v2.1: LLM 同时注入 service（EMERGENCY_PROMPT 危险评估）
- v2.1: _try_load_host_persona() 从主项目 characters.json 加载角色人设
"""

import asyncio
import json
import os
from typing import Any, Dict, Optional

from plugin.sdk.plugin import lifecycle


class LifecycleMixin:
    _agent: Any
    _autonomous_brain: Any
    _service: Any
    _config: Dict[str, Any]

    def _init_core_services(self) -> None:
        from ..autonomous.brain import AutonomousBrain
        from ..bridge.agent import TerrariaAgent
        from ..core.config_store import load_user_config
        from ..core.service import TerrariaService

        # 所有默认值统一由 config_store.DEFAULTS 管理
        # load_user_config() 读取 user_config.json，缺失项用 DEFAULTS 补齐
        self._config = load_user_config()
        self._agent = TerrariaAgent(self)
        self._autonomous_brain = AutonomousBrain(self)
        self._service = TerrariaService(self, push_message=self.push_message)

    async def _load_config(self) -> None:
        # plugin.toml [neko_terraria] 段覆盖默认配置。
        # ★ 宿主的 config.dump 是同步阻塞且会**永久锁死线程**（timeout 参数无效，
        #   线程无法取消）——曾导致 _boot 卡死 11 分钟、感知/情感/大脑全不启动。
        #   彻底修复：不再调用 dump（宿主的 dump 线程锁无法安全绕过）。
        #   _config 已在 __init__ 由 config_store.load_user_config() 填好
        #   （DEFAULTS + 用户配置），plugin.toml 段覆盖属可选增强，跳过不影响功能。
        try:
            # 尝试非阻塞读取宿主配置（若提供）；绝不碰 dump（会锁线程）
            get_cfg = getattr(self.config, "get", None)
            if get_cfg is None:
                return
            neko = get_cfg("neko_terraria") or {}
            if isinstance(neko, dict):
                for key in ("mod_host", "mod_port", "server_host", "server_port",
                            "server_password", "game_path", "character_name",
                            "state_tick_interval_seconds",
                            "fast_think_interval_seconds", "deep_think_min_seconds",
                            "deep_think_max_seconds",
                            "context_push_interval_seconds", "context_deep_push_interval_seconds",
                            "llm_autonomous_enabled", "llm_think_min_seconds",
                            "llm_think_max_seconds",
                            "follow_trigger_dist", "follow_stop_dist",
                            "auto_return_after_respawn",
                            # v2.0: 交互引擎配置项
                            "interaction_tick",
                            "interaction_enabled",
                            "llm_intent_enabled",
                            "vision_analysis_enabled", "vision_min_interval",
                            "recovery_ask_interval",
                            "owner_track_window",):
                    if key in neko:
                        self._config[key] = neko[key]
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("读取配置失败，使用默认值: {}", exc)

    # ── LLM 注入 ──────────────────────────────────────

    async def _wire_llm_integration(self) -> None:
        """双 LLM 架构：主 LLM（对话/人格）+ 意图 LLM（结构化推理）。

        支持三种模式：
        1. 宿主注入（__call_llm/__call_intent_llm）- 优先使用
        2. 插件配置（config 中的 LLM 设置）- 自建客户端
        3. 单 LLM 回退（未配置意图 LLM 时使用主 LLM）
        """
        from ..llm.throttle import get_throttle
        get_throttle(self._config)

        main_llm = await self._resolve_llm_call()
        intent_llm = await self._resolve_intent_llm()

        if not main_llm and not intent_llm:
            self.logger.info("LLM 未配置，将使用正则 fallback 解析指令")
            return

        coordinator = getattr(self._agent, "coordinator", None)
        if coordinator and intent_llm:
            coordinator.set_llm_call(intent_llm)
            model_info = "（独立意图模型）" if intent_llm != main_llm else "（共享主模型）"
            self.logger.info(f"已注入意图解析 LLM {model_info}")

        # 注：service.set_llm_call 当前无消费者（紧急事件评估已由交互引擎
        # 直推宿主，不走 service 内 LLM）——保留注入便于未来启用，不再打
        # "已注入紧急评估 LLM"误导日志
        if self._service and main_llm:
            self._service.set_llm_call(main_llm)

    async def _resolve_llm_call(self):
        """主 LLM：对话、人格、汇报。优先使用宿主注入，否则从配置创建。"""
        raw = getattr(self, "__call_llm", None)
        if raw:
            if asyncio.iscoroutinefunction(raw):
                async def wrapper(prompt: str) -> str:
                    return await raw(prompt)
                return wrapper
            else:
                async def wrapper(prompt: str) -> str:
                    return raw(prompt)
                return wrapper

        call_fn = getattr(self, "call_llm", None)
        if call_fn:
            async def wrapper(prompt: str) -> str:
                return await call_fn(prompt)
            return wrapper

        return await self._create_llm_from_config("llm_main")

    async def _resolve_intent_llm(self):
        """意图 LLM：结构化推理、任务规划。优先宿主注入，否则配置创建，最后回退主 LLM。"""
        raw = getattr(self, "__call_intent_llm", None)
        if raw:
            if asyncio.iscoroutinefunction(raw):
                async def wrapper(prompt: str) -> str:
                    return await raw(prompt)
                return wrapper
            else:
                async def wrapper(prompt: str) -> str:
                    return raw(prompt)
                return wrapper

        intent_from_config = await self._create_llm_from_config("llm_intent")
        if intent_from_config:
            return intent_from_config

        return await self._resolve_llm_call()

    async def _create_llm_from_config(self, prefix: str):
        """从插件配置创建 LLM 客户端（prefix: "llm_main" 或 "llm_intent"）。"""
        from ..llm.unified_client import create_llm_client

        client = create_llm_client(self._config, prefix)
        if not client:
            return None

        async def wrapper(prompt: str) -> str:
            try:
                return await client.call(prompt)
            except Exception as e:
                self.logger.warning(f"LLM 调用失败 ({prefix}): {e}")
                raise

        return wrapper

    async def _wire_vision_llm(self) -> None:
        """注入 LLM Vision 能力到 VisualPerception。

        需要插件安装了支持视觉的 LLM 后端（如 Gemini Vision / GPT-4V）。"""
        try:
            # 检查配置开关
            if not self._config.get("vision_analysis_enabled", True):
                return

            vision = getattr(self._agent, "vision", None)
            if not vision or not hasattr(vision, "perception"):
                return

            vp = vision.perception  # VisualPerception 实例

            # 宿主是否提供 vision LLM？（v3.0: 统一走宿主，无自带客户端）
            vision_llm = getattr(self, "call_vision", None)
            if vision_llm:
                vp.set_llm_vision(vision_llm)
                self.logger.info("已注入 LLM Vision 感知 → 猫娘能看游戏画面了")
        except Exception:
            pass  # vision 是可选的，静默跳过

    # ── v2.1: 主项目角色人设加载 ────────────────────

    async def _try_load_host_persona(self) -> Optional[Dict[str, Any]]:
        """尝试从 N.E.K.O 主项目的 characters.json 加载当前角色的性格人设。

        返回 {traits: [...], description: str, habits: {...}} 或 None。
        如果主项目不可达或文件不存在，静默返回 None。
        """
        try:
            # 推导主项目 config 路径：__file__ = .../neko_terraria/entries/lifecycle_mixin.py
            # 上 2 级 → neko_terraria；上 4 级 → 主程序根（plugin/plugins/neko_terraria
            # 的开发布局与 resources/bin/plugin/plugins/neko_terraria 的生产布局同构）
            fdir = os.path.dirname(os.path.abspath(__file__))
            host_root = fdir
            for _ in range(4):
                host_root = os.path.dirname(host_root)

            # 优先 characters.json（旧布局），否则 characters/{locale}.json（新布局）
            candidates = [
                os.path.join(host_root, "config", "characters.json"),
                os.path.join(host_root, "config", "characters", "zh-CN.json"),
                os.path.join(host_root, "config", "characters", "zh_CN.json"),
                os.path.join(host_root, "config", "characters", "en.json"),
            ]
            config_path = next((p for p in candidates if os.path.exists(p)), None)
            if not config_path:
                self.logger.debug(f"未找到主项目人设（尝试过: {candidates[0]} 等）")
                return None

            with open(config_path, "r", encoding="utf-8") as f:
                characters = json.load(f)

            # 获取当前活跃角色的名称
            char_name = self._config.get("character_name", "")
            if not char_name:
                char_name = os.environ.get("NEKO_CHARACTER", "")

            # 中文布局：{"主人": {...}, "猫娘": {"YUI": {...}}, ...}；
            # 英文布局：{角色名: {...}} 或 list
            persona = None
            if isinstance(characters, dict):
                cats = characters.get("猫娘", None)
                if isinstance(cats, dict):
                    if char_name and char_name in cats:
                        persona = cats[char_name]
                    elif "default" in cats:
                        persona = cats["default"]
                    else:
                        persona = next(iter(cats.values()), None)
                else:
                    if char_name and char_name in characters:
                        persona = characters[char_name]
                    elif "default" in characters:
                        persona = characters["default"]
                    else:
                        persona = next(iter(characters.values()), None)
            elif isinstance(characters, list) and characters:
                for c in characters:
                    if isinstance(c, dict) and c.get("name", "").lower() == char_name.lower():
                        persona = c
                        break
                if persona is None:
                    persona = characters[0]

            if not persona:
                return None

            # 提取人设信息（兼容中英文键名）
            traits = (persona.get("核心特质", []) or persona.get("traits", [])
                      or persona.get("core_traits", []))
            description = (persona.get("一句话台词", "") or persona.get("description", "")
                           or persona.get("prompt", ""))
            habits = (persona.get("行为特点", {}) or persona.get("习惯", {})
                      or persona.get("habits", {}) or persona.get("speech_habits", {}))

            result = {
                "traits": traits if isinstance(traits, list) else [traits],
                "description": description if isinstance(description, str) else "",
                "habits": habits if isinstance(habits, dict) else {},
            }
            self.logger.info(f"已加载主项目角色人设: {config_path} "
                             f"(traits={len(result['traits'])})")
            return result
        except Exception as exc:
            self.logger.debug(f"加载主项目人设失败（将使用默认人设）: {exc}")
            return None

    # ── 生命周期 ──────────────────────────────────────

    @lifecycle(id="startup")
    async def on_load(self) -> None:
        self.logger.info("neko_terraria 加载")
        # 静态 UI 必须在 on_load 同步段注册，不能在后台 task，否则宿主路由报 no static directory
        ui_registered = self.register_static_ui(
            "static",
            index_file="index.html",
            cache_control="no-cache, no-store, must-revalidate",
        )
        if ui_registered:
            self.logger.info("已注册泰拉瑞亚控制面板: /plugin/neko_terraria/ui/")
        else:
            self.logger.warning("注册静态 UI 失败，请检查 static/index.html 是否存在")

        # 对齐 vr 插件：注册列表入口动作，确保宿主识别到插件入口点
        self.set_list_actions([{
            "id": "open_ui",
            "label": "打开控制面板",
            "kind": "ui",
            "target": f"/plugin/{self.plugin_id}/ui/",
            "open_in": "new_tab",
        }])

        # ★ 启动不在 on_load 里 create_task：宿主的 lifecycle.startup 在临时
        #   asyncio.run() 循环中执行（plugin/core/host.py:1162），create_task
        #   的后台任务随循环结束被取消，boot 永远跑不完 → brain/交互/感知全不
        #   启动（"几乎全部交互功能都有问题"的根因之一）。改为
        #   _on_command_loop_start 在宿主持久命令循环上调度，见该钩子 docstring。

    async def _on_command_loop_start(self) -> None:
        """在宿主持久命令循环上调度 boot。

        宿主在 _async_command_loop 开头调用本钩子
        （plugin/core/host.py:1611-1621），它所在的 asyncio loop 就是之后所有
        @plugin_entry/@llm_tool/@message 处理器运行的同一个长生命周期循环。
        在这里 create_task 的后台任务能存活；且 create_task 立即返回，不阻塞
        downlink_ready 的标记（host 在钩子返回后才 set）。
        """
        task = getattr(self, "_boot_task", None)
        if task is not None:
            if not task.done():
                return
            if not getattr(self, "_boot_failed", False):
                return
            self._boot_task = None
            self._boot_failed = False
        self._boot_task = asyncio.create_task(self._boot())

    async def _boot(self) -> None:
        self._boot_failed = False
        try:
            self.logger.info("[boot] 开始启动 neko_terraria...")
            await self._load_config()
            self.logger.info("[boot] 配置加载完成")

            # v2.1: 加载主项目角色人设（注入到 config 中供 habits/context 使用）
            persona = await self._try_load_host_persona()
            if persona:
                self._config["_host_persona"] = persona
                self.logger.info("[boot] 角色人设已注入")

            # v2.0: LLM 集成（agent 启动前注入，确保首次 handle() 就能走 LLM）
            await self._wire_llm_integration()
            self.logger.info("[boot] LLM 集成完成")

            # ★ 不自动启动 AI 客户端：插件启动只加载大脑/交互待命，不拉起
            #   tModLoader 游戏进程（曾 boot → agent.start() → launcher.launch()
            #   自动弹游戏窗口，用户没在玩也占资源）。
            #   由面板「连接游戏」(nt_connect → agent.start()) 手动拉起。
            #   agent.running=False 期间 brain/service 空转（见 brain 的
            #   running 门 + service 的 joined 延迟），连上后自动恢复。
            if getattr(self._agent, "running", False):
                self.logger.info("[boot] AI 客户端已在运行，跳过自动启动")
            else:
                self.logger.info("[boot] 未自动启动游戏（点面板『连接游戏』才会拉起 AI 客户端）")

            # v2.0: brain.start() 内部会拉起交互引擎 + 注册 executor 回调
            try:
                await asyncio.wait_for(self._autonomous_brain.start(), timeout=20.0)
            except asyncio.TimeoutError:
                self.logger.warning("[boot] 自治大脑启动超时，清理半初始化状态后重试")
                try:
                    await self._autonomous_brain.stop()
                    await self._autonomous_brain.start()
                except Exception:
                    pass
            except Exception as e:
                self.logger.warning(f"[boot] 自治大脑启动异常: {e}")
                try:
                    await self._autonomous_brain.stop()
                except Exception:
                    pass
            self.logger.info("[boot] 自治大脑启动完成")

            # v2.1: 交互引擎暴露给 agent → service/coordinator 可通过 agent._neko_interaction 访问
            self._agent._neko_interaction = self._autonomous_brain.interaction
            self._service.interaction = self._autonomous_brain.interaction

            # v3.0: 绑定游戏事件发射器到交互引擎
            self._service.bind_emitter_to_interaction()

            await self._service.start()
            self.logger.info("[boot] 游戏状态服务启动完成")

            # v2.0: vision LLM 后注入（vision 在 agent.start() 后才初始化）
            await self._wire_vision_llm()

            # v2.1: 启动视觉管线。截图源（v0.7）：主玩家窗口截图优先（猫娘"看"
            # 主人画面，陪伴感核心），失败时降级到 mod 截图（猫娘自己视角）。
            vp = getattr(self._agent, "vision", None)
            if vp:
                try:
                    from ..perception.owner_screen import owner_window_frame
                    mod_shot = self._agent.mod.screenshot

                    async def _frame_source():
                        try:
                            f = await owner_window_frame()
                            if f:
                                return f
                        except Exception:
                            pass
                        return await mod_shot()

                    vp.set_frame_source(_frame_source)
                except Exception:
                    vp.set_frame_source(self._agent.mod.screenshot)
                await vp.start()
                self.logger.info("[boot] 视觉感知管线已启动（主人画面优先）")

            # v0.7: 常驻场景上下文注入（read 模式不打扰，供主 LLM 人设感知）
            try:
                # SDK push_message 是同步方法，不能 await
                self.push_message(
                    parts=[{"type": "text", "text": (
                        "[场景] 猫娘在泰拉瑞亚世界里陪主人玩：她在游戏里有一个身体"
                        "（会疼、会累、会害怕、会冷），能看到主人的画面动态，"
                        "对主人有长期依恋情感。用角色人设自然地互动，"
                        "该场景贯穿本次会话。")}],
                    visibility=[], ai_behavior="read")
            except Exception:
                pass

            self.logger.info("猫娘已进入泰拉瑞亚世界（v2.1 交互引擎 + 紧急事件已接通）")
        except Exception as e:
            self._boot_failed = True
            import traceback
            self.logger.error(f"[boot] 启动过程中抛出未捕获异常: {e}")
            self.logger.error(f"[boot] 堆栈跟踪:\n{traceback.format_exc()}")
            # 如果是 asyncio 任务内部的异常，也要让宿主感知
            try:
                self.push_message(
                    parts=[{"type": "text",
                            "text": f"[系统] neko_terraria 启动失败: {e}"}],
                    visibility=["hud"], ai_behavior="blind")
            except Exception:
                pass

    @lifecycle(id="shutdown")
    async def on_unload(self) -> None:
        # 取消还在跑的 boot 任务（可能卡在 agent.start() 45s 超时中），
        # 避免它与下面的 stop 序列竞态（agent 启动一半被 stop，残留子进程）
        boot_task = getattr(self, "_boot_task", None)
        if boot_task is not None and not boot_task.done():
            boot_task.cancel()
            try:
                await boot_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._boot_task = None
        # v3.0: 关闭记忆存储（SQLite）
        closer = getattr(self, "_close_memory", None)
        if closer:
            try:
                closer()
            except Exception:
                pass
        vp = getattr(self._agent, "vision", None)
        if vp:
            await vp.stop()
        await self._service.stop()
        await self._autonomous_brain.stop()
        await self._agent.stop()
        self.logger.info("neko_terraria 卸载")
