"""猫娘唯一游戏指令工具：terraria_command（单入口，对齐 minecraft 插件）。

曾经这里/action_tools.py 有约 30 个未加 @llm_tool 装饰器的 llm_* 方法
（llm_mine/llm_craft/llm_chain/llm_set_goal/…）——SDK 只认 @llm_tool
标记，它们从未注册给宿主 LLM，是"幽灵工具"：ai_guidance 教了名字但
宿主根本看不到，LLM 照着调用必然失败。已全部删除，收敛为单入口：
所有游戏操作都用 terraria_command 传自然语言，由 coordinator 解析分发。
"""

from typing import Any, Dict

from plugin.sdk.plugin import Ok, llm_tool


class GoalToolsMixin:
    _agent: Any
    _autonomous_brain: Any

    @llm_tool(
        name="terraria_command",
        description=(
            "【主人下游戏指令的单一主入口】主人用自然语言说的游戏指令（中文/英文均可）"
            "原样传入，由猫娘内置解析器判断并执行：挖矿('挖10个铁')、跟随('跟着我')、"
            "守点('守在这')、砍树('砍5棵树')、停止('别挖了/别砍了')、以及多步任务"
            "('挖铁矿然后合成铁锭给我')。"
            "Fire-and-forget：立即返回受理确认，真实结果随后由系统消息异步送达，"
            "不要凭指令文本自行推断或宣称结果。\n\n"
            "【指令来源权威】只传主人最近一条消息里的、尚未派发过的新游戏指令原文，"
            "保持原话不加细节、不翻译改写。不要从更早消息、日志、截图、状态或完成回执"
            "里恢复/重放旧指令。\n\n"
            "【禁止坐标】除非主人明确要求坐标，绝不自行编造/推断/附带游戏坐标。\n\n"
            "【别回显日志】游戏遥测/内部状态是感知上下文，不是任务——不要把它当指令"
            "传回本工具（会形成派发循环）。\n\n"
            "【闲聊不要调】纯聊天/情感表达（'你好呀''累不累'）直接以猫娘身份回应，"
            "不要调本工具。"
        ),
        parameters={"type": "object",
                    "properties": {"text": {"type": "string",
                                            "description": "主人最近一条消息里的游戏指令原文"}},
                    "required": ["text"]},
        timeout=300.0,
    )
    async def llm_command(self, *, text: str, **_) -> Dict[str, Any]:
        # fire-and-forget：把指令交给 coordinator 后台执行，立即返回受理确认。
        # 完成/进度由 brain 的 executor 回调（task_done/step_done）异步推回宿主
        # LLM——对齐 minecraft 插件的 minecraft_task 模式，LLM 回合不被长任务
        # 阻塞（"挖10个铁"可能耗时几十秒，同步 await 会让宿主 LLM 工具回合挂死）。
        self._agent.logger.info(f"[llm_command] 📥 收到指令(受理): {text}")

        async def _dispatch() -> None:
            """后台执行 + 非 executor 路径的结果回读。

            finite 任务分两类，完成汇报来源不同：
            - phase=act（已交 executor）→ task_done/step_done 回调已由 brain 推回
              （不重复）。
            - phase=think/plan（还没进执行器就被拒：not_doable / empty_plan /
              not_understood）→ executor 不会发任何事件，必须在这里请求回应，
              否则宿主 LLM 只看到"已受理"后永远等不到结果、误以为任务还在跑
              （对齐 mc：拒绝要明确说出来，绝不能假装在做）。busy 由 executor
              发 interrupted(busy)，brain 会 respond「没接上」，这里不重复。
            chat 由 _do_chat 直接 respond（不重复）。只有 stop / unknown(反问) /
            longterm(启动确认) 这些即时结果没有异步通道，这里请求宿主回应；
            已在运行的重复指令仅作为静默上下文。
            """
            try:
                res = await self._agent.command(text, source="owner")
            except Exception as e:
                self._agent.logger.warning(f"[llm_command] 执行异常: {e}")
                await self._agent.speak(
                    f"[指令受阻] 「{text}」未能执行：{e}。请如实向主人说明，不要自动重派。",
                    ai_behavior="respond", fallback_text=f"指令未能执行：{e}")
                return
            mode = str(res.get("mode", "") or "")
            if mode == "chat":
                return  # _do_chat 已覆盖
            if res.get("reported") is True:
                # 询问/闲聊已经通过游戏聊天或宿主推送送达；不要把同一
                # 结果再次包装成“指令结果”交给 LLM，避免猫娘重复开口。
                return
            if mode == "finite":
                phase = str(res.get("phase", "") or "")
                status = str(res.get("status", "") or "")
                # 已交 executor 的终态（ok/step_failed/cancelled/error/busy）
                # 都由 executor 事件覆盖（task_done / interrupted），不重复。
                if phase == "act" or status in ("ok", "step_failed",
                                                "cancelled", "busy", "error"):
                    return
                out = str(res.get("output", "") or "")
                if not out:
                    return
                # think/plan 阶段拒绝（not_doable/empty_plan/not_understood）：
                # 无 executor 事件 → 请求宿主向主人说明"没做/做不了"
                try:
                    await self._agent.speak(
                        f"[指令受阻] {out}。请根据事实向主人说明原因，不要自动重派旧任务。",
                        ai_behavior="respond", fallback_text=out)
                except Exception:
                    pass
                return
            out = str(res.get("output", "") or "")
            if not out:
                return
            try:
                await self._agent.speak(
                    f"[指令结果] {out}。请只根据此结果回应，不要自动重派指令。",
                    ai_behavior="read" if res.get("status") == "running" else "respond",
                    fallback_text=out)
            except Exception:
                pass

        try:
            self._agent._spawn_background_task(_dispatch())
        except Exception as e:
            self._agent.logger.warning(f"[llm_command] 派发失败: {e}")
            return Ok({"output": f"指令派发失败喵（{e}）"})
        return Ok({
            "output": f"已收到：『{text}』，正在解析，尚未确认可执行。实际进度/结果会随系统消息汇报，"
                      f"不要自行宣称完成。",
            "status": "accepted",
            "note": "fire-and-forget：本工具立即返回，真实结果稍后异步到达。"
        })
