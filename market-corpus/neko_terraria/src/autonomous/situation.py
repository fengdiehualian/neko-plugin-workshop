"""处境融合层（v0.7）：身体感 × 画面感 × 记忆 → 心情/台词/行为倾向。

猫娘的第一人称处境叙事——不是"血低了"这种数据，而是
"我在雪地洞穴里，血只有 40%，有点冷，主人在地表盖房子，离我好远……"
→ mood（情绪）/ thought（独白）/ say（想说的话）/ behavior（行为倾向）

- 每 SITUATION_INTERVAL 秒聚合一次（默认 20s，避开视觉管线的 15s 低频）
- 融合 prompt 用意图 LLM（快速结构化，独立于主 LLM 人设）
- say → interaction.push_speech(respond) 由主 LLM 以人设演绎（画面情感）
- behavior → 行为倾向（run_to_owner / stick / stay），空闲时生效
"""

import asyncio
import json
from typing import Any, Dict, Optional

SITUATION_INTERVAL = 20.0

SITUATION_PROMPT = """你是{name}，一只在泰拉瑞亚世界里陪主人玩的猫娘。
这是你此刻的处境（来自你的身体感知、你看到的画面、你的记忆）：

【身体】我在{biome}，血量{hp}/{max}，状态效果{buffs}，正在{movement}，附近有{npcs}，离主人{dist}格
【画面】主人{owner_action}（{summary}）
【记忆】{memory}
【上次】{last_situation}

用 JSON 输出你此刻的真实感受：
{{
    "mood": "lonely|afraid|cozy|curious|hurt|proud|happy|bored",
    "thought": "内心独白（猫娘语气，20字内）",
    "say": "想对主人说的话（可为空字符串，15字内，猫娘语气）",
    "behavior": "run_to_owner|stick|stay|explore|report|none"
}}

只输出 JSON，不要废话。"""

_MOOD_TO_ARC = {
    "lonely": "fear", "afraid": "fear", "hurt": "fear",
    "cozy": "excitement", "happy": "excitement",
    "curious": "curiosity", "proud": "proud", "bored": "tired",
}


class SituationEngine:
    """处境融合循环。llm_call 为空时只做行为倾向（不生成说话）。"""

    def __init__(self, agent, brain, llm_call=None) -> None:
        self.agent = agent
        self.brain = brain
        self._llm_call = llm_call
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.last_situation = ""

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while self.running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(SITUATION_INTERVAL)

    # ---------------- 聚合 ----------------

    def _owner_dist(self, state: Dict[str, Any]) -> int:
        """离主人（最近玩家）的距离（格）。"""
        me = (state.get("tile_x", 0), state.get("tile_y", 0))
        best = -1
        for pl in (state.get("nearby_players") or []):
            dx = int(pl.get("tile_x", 0) or 0) - me[0]
            dy = int(pl.get("tile_y", 0) or 0) - me[1]
            d = (dx * dx + dy * dy) ** 0.5
            if best < 0 or d < best:
                best = d
        return int(best) if best >= 0 else -1

    def _memory_hint(self) -> str:
        """从插件记忆取一条最近互动（主人行为模式的轻量提示）。"""
        try:
            store = getattr(self.agent, "memory", None) or getattr(self.agent, "_memory", None)
            if store is None:
                plugin = getattr(self.agent, "plugin", None)
                getter = getattr(plugin, "_memory_store", None)
                if getter:
                    store = getter()
            if store and hasattr(store, "recall"):
                hits = store.recall("主人", limit=1) if callable(store.recall) else None
                if hits:
                    hit = hits[0]
                    if isinstance(hit, dict):
                        return f"{hit.get('key', '')}: {hit.get('value', '')}"[:60]
                    return str(hit)[:60]
        except Exception:
            pass
        return ""

    # ---------------- 融合 ----------------

    async def _tick(self) -> None:
        brain = self.brain
        # #94: 只在前台任务忙时让路——长期任务（跟随/挖矿）不阻塞处境层，
        # 否则猫娘一跟随就永久失去处境感知/主动说话（陪伴感核心）。
        if brain:
            ex = getattr(self.agent, "executor", None)
            if ex and ex.busy():
                return

        state = {}
        try:
            state = self.agent.get_state()
        except Exception:
            pass
        if (not state or not getattr(self.agent, "running", False)
                or state.get("alive") is False or int(state.get("hp", 0) or 0) <= 0):
            return

        report = {}
        vision = getattr(self.agent, "vision", None)
        if vision:
            try:
                report = vision.perception.last_report or {}
            except Exception:
                pass

        # 行为倾向在无 LLM 时也可用规则兜底
        if not self._llm_call:
            await self._apply_behavior(self._rule_behavior(state, report), state)
            return

        try:
            my_name = "Neko"
            try:
                my_name = self.agent._character_name()
            except Exception:
                pass
            prompt = SITUATION_PROMPT.format(
                name=my_name,
                biome=state.get("biome", "未知"),
                hp=state.get("hp", 0),
                max=state.get("max_life", 100),
                buffs="、".join((state.get("buffs") or [])[:3]) or "无",
                movement=state.get("movement_state", "grounded"),
                npcs="、".join(str(n.get("name", "")) for n in (state.get("nearby_npcs") or [])[:3]) or "无",
                dist=self._owner_dist(state),
                owner_action=report.get("owner_action", "unknown"),
                summary=report.get("summary", ""),
                memory=self._memory_hint() or "无",
                last_situation=self.last_situation or "无",
            )
            raw = await self._llm_call(prompt)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

        parsed = self._parse(raw)
        if not parsed:
            return

        thought = (parsed.get("thought") or "").strip()
        if thought:
            self.last_situation = thought

        # mood → 情绪弧线（交互引擎）
        emo = _MOOD_TO_ARC.get((parsed.get("mood") or "").strip())
        if emo and brain and getattr(brain, "interaction", None):
            try:
                brain.interaction.mood.trigger(emo, 0.35)
            except Exception:
                pass

        # say → 说话（respond 走主 LLM 人设演绎）
        say = (parsed.get("say") or "").strip()
        if say and brain and getattr(brain, "interaction", None):
            try:
                await brain.interaction.push_speech(
                    f"[处境心情] {say}\n用猫娘语气说出这句话（不超过15字，保持原意）",
                    behavior="respond")
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        # behavior → 行为倾向
        await self._apply_behavior((parsed.get("behavior") or "none").strip(), state)

    @staticmethod
    def _parse(raw: str) -> Optional[Dict[str, Any]]:
        try:
            cleaned = raw.strip()
            for fence in ("```json", "```"):
                cleaned = cleaned.replace(fence, "").strip()
            return json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return None

    def _rule_behavior(self, state: Dict[str, Any], report: Dict[str, Any]) -> str:
        """无 LLM 时的规则兜底行为倾向。"""
        hp = state.get("hp", 100)
        max_hp = state.get("max_life", 100) or 100
        dist = self._owner_dist(state)
        if hp < max_hp * 0.35 and 0 <= dist < 30:
            return "run_to_owner"  # 血低 + 主人在附近 → 找主人
        if report.get("owner_action") == "fight":
            return "run_to_owner"  # 主人在战斗 → 过去帮忙/看
        if dist < 0:
            return "explore"       # 主人不在 → 自己转转
        return "none"

    # ---------------- 行为接线 ----------------

    async def _apply_behavior(self, behavior: str, state: Dict[str, Any]) -> None:
        if behavior not in ("run_to_owner", "stick", "stay"):
            return
        agent = self.agent
        if not self.running or not getattr(agent, "running", False):
            return
        ex = getattr(agent, "executor", None)
        if ((ex and ex.busy()) or getattr(agent, "_in_combat", False)
                or getattr(self.brain, "_busy", False)):
            return
        allow = getattr(agent, "autonomy_allowed", None)
        if allow and not allow("follow"):
            return
        lt = getattr(agent, "longterm", None)
        if lt is None:
            return
        # 已有跟随任务：交给 follow_loop 的距离分层，不重复干预
        if lt.get("follow") is not None:
            return
        # 处境层只负责陪伴意图，不能在主人已下达挖矿/砍树/守点时抢占长期槽。
        if lt.busy_kinds():
            return

        if behavior in ("run_to_owner", "stick"):
            # 主人不在视野内（dist<0）→ 无从追起
            if self._owner_dist(state) < 0:
                return
            try:
                await agent.start_longterm(
                    "follow", target="stick" if behavior == "stick" else "",
                    reason="处境感知：想去找主人")
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        elif behavior == "stay":
            try:
                await agent.stop_longterm("follow", why="想安静待一会儿")
            except Exception:
                pass
