"""海龟汤小游戏 —— 单人情境猜谜（AI 出题，猫娘裁判）。

玩法：AI 出「汤面」，玩家靠「是/否」提问推理出「汤底」。
指令（cmd 子串匹配）：
  海龟汤 [类型]    —— 开始新一局（可选：悬疑/校园/日常…）
  提问 xxx         —— 提出推理问题，裁判答「是/不是/无关」
  猜汤底 xxx       —— 提交最终猜测
  结束 / 放弃      —— 放弃并揭晓汤底
  状态             —— 查看问答记录 + 累计积分
  （帮助走主插件 show_help）

LLM 走主插件已接好的桥（self.call_llm）；未配置时出题降级到本地题库，
裁判/判猜无法降级则给出明确提示。人设与情感由主插件（brain 的 persona/emotion）负责。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

from ...core.contracts import GameAdapter, build_fact

_SOUPS_PATH = os.path.join(os.path.dirname(__file__), "soups.json")
_SOUPS: Optional[List[Dict[str, str]]] = None

_IDLE_HINT = "还没有开始的海龟汤喵，说「海龟汤」来一锅吧~"


def _load_soups() -> List[Dict[str, str]]:
    """惰性加载本地题库（LLM 不可用时的降级）。"""
    global _SOUPS
    if _SOUPS is None:
        try:
            with open(_SOUPS_PATH, encoding="utf-8") as f:
                _SOUPS = json.load(f)
        except (OSError, json.JSONDecodeError):
            _SOUPS = []
    return _SOUPS


class SoupBubbleGame(GameAdapter):
    id = "soupbubble"
    name = "海龟汤"
    description = "AI 出题的悬疑猜谜，猫娘当裁判，主人来猜汤底"
    icon = "🍲"
    version = "0.1.0"

    # ── 指令路由 ──────────────────────────

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        c = (cmd or "").strip()
        sess = await self._load_session(user_id)
        playing = sess.get("state") == "playing"

        # 开场 / 重开
        if any(k in c for k in ("海龟汤", "开始", "发起", "来一锅")):
            if playing:
                return {"message": "已经有一锅汤在煮啦喵~想换新的先「结束」这锅",
                        "outcome": "remind", "facts": [build_fact("remind")]}
            return await self._start(user_id, sess, c)

        # 猜汤底（显式）
        if "猜汤底" in c or "猜底" in c:
            if not playing:
                return {"message": _IDLE_HINT, "outcome": "idle", "facts": [build_fact("idle")]}
            guess = self._strip_kw(c, "猜汤底", "猜底")
            if not guess:
                return {"message": "猜什么呀？说「猜汤底 你的答案」喵",
                        "outcome": "remind", "facts": [build_fact("remind")]}
            return await self._guess(user_id, sess, guess)

        # 结束 / 放弃 / 揭晓（含裸「汤底」= 想知道答案）
        if any(k in c for k in ("结束", "放弃", "公布", "揭晓", "不玩", "不猜", "汤底")):
            if not playing:
                return {"message": _IDLE_HINT, "outcome": "idle", "facts": [build_fact("idle")]}
            return await self._reveal(user_id, sess)

        # 状态
        if "状态" in c or "进度" in c:
            return await self._status(user_id, sess)

        # 提问（显式）
        if "提问" in c:
            if not playing:
                return {"message": _IDLE_HINT, "outcome": "idle", "facts": [build_fact("idle")]}
            q = self._strip_kw(c, "提问")
            if not q:
                return {"message": "想问什么呀？说「提问 你的问题」喵",
                        "outcome": "remind", "facts": [build_fact("remind")]}
            return await self._ask(user_id, sess, q)

        # 未开局：其余任何话都提示开局
        if not playing:
            return {"message": _IDLE_HINT, "outcome": "idle", "facts": [build_fact("idle")]}

        # 游戏中：自由文本 → 像提问就自动裁判，否则温和提醒
        if self._looks_like_question(c):
            return await self._ask(user_id, sess, c)
        return {"message": "还在猜这锅汤哦~想推理就「提问 ...」，有答案就「猜汤底 ...」，不猜了说「结束」喵",
                "outcome": "remind", "facts": [build_fact("remind")]}

    # ── 各指令实现 ──────────────────────────

    async def _start(self, user_id: str, sess: Dict[str, Any], c: str) -> Dict[str, Any]:
        category = self._category(c)
        puzzle = await self._generate_puzzle(category)
        if not puzzle:
            return {"message": "现在没有可用的 AI 出题，也找不到本地题库，海龟汤玩不了喵……",
                    "outcome": "no_llm", "facts": [build_fact("no_llm")]}
        stats = sess.get("stats", {}) or {}
        sess = {
            "state": "playing",
            "soup": puzzle["soup"],
            "bottom": puzzle["bottom"],
            "difficulty": puzzle.get("difficulty", ""),
            "qa": [],
            "guesses": [],
            "started_at": time.time(),
            "last_activity_at": time.time(),
            "nudged": False,
            "stats": {"solved": stats.get("solved", 0), "played": stats.get("played", 0) + 1},
        }
        await self._save_session(user_id, sess)
        lines = ["🎲 海龟汤开始！这局我当裁判，主人来猜~", f"汤面：{puzzle['soup']}"]
        if puzzle.get("difficulty"):
            lines.append(f"难度：{puzzle['difficulty']}")
        lines.append("主人想推理就说「提问 问题」，有把握就说「猜汤底 答案」，我来判断对错喵")
        return {"message": "\n".join(lines), "outcome": "start",
                "facts": [build_fact("start", soup=puzzle["soup"], difficulty=puzzle.get("difficulty", ""))]}

    async def _ask(self, user_id: str, sess: Dict[str, Any], question: str) -> Dict[str, Any]:
        question = question.strip()
        if len(question) < 2:
            return {"message": "问题太短啦，说清楚点喵", "outcome": "remind", "facts": [build_fact("remind")]}
        if len(question) > 200:
            return {"message": "问题太长啦，控制在 200 字内喵", "outcome": "remind", "facts": [build_fact("remind")]}

        judge_sys = self._cfg("prompts", {}).get("judge_system", "")
        prompt = (
            f"{judge_sys}\n\n汤面：{sess['soup']}\n汤底：{sess['bottom']}\n"
            f"玩家提问：{question}\n"
            "请判断答案是「是」「不是」还是「无关」。只返回 JSON 对象，字段："
            "answer（取值：是/不是/无关）、hint（可选一句话提示）。不要解释。"
        )
        text = await self.call_llm(prompt)
        if not text:
            return {"message": "裁判开小差了，稍等再问一次喵……",
                    "outcome": "judge_fail", "facts": [build_fact("judge_fail")]}
        answer, hint = self._parse_answer(text)

        sess["qa"].append({"q": question, "a": answer, "hint": hint})
        sess["last_activity_at"] = time.time()
        sess["nudged"] = False
        await self._save_session(user_id, sess)

        msg = f"主人问「{question}」→ 我的判断：{answer}"
        if hint:
            msg += f"（提示：{hint}）"
        return {"message": msg, "outcome": "question",
                "facts": [build_fact("question", answer=answer, hint=hint, question=question)]}

    async def _guess(self, user_id: str, sess: Dict[str, Any], guess: str) -> Dict[str, Any]:
        guess = guess.strip()
        if len(guess) < 2:
            return {"message": "猜什么呀？说「猜汤底 你的答案」喵", "outcome": "remind", "facts": [build_fact("remind")]}
        if len(guess) > 500:
            return {"message": "猜测太长啦，控制在 500 字内喵", "outcome": "remind", "facts": [build_fact("remind")]}

        guess_sys = self._cfg("prompts", {}).get("guess_system", "")
        prompt = (
            f"{guess_sys}\n\n汤底：{sess['bottom']}\n玩家的猜测：{guess}\n"
            "请判断这个猜测是否正确。只返回 JSON 对象，字段：correct（true/false）、"
            "comment（简短的评判说明）。不要解释。"
        )
        text = await self.call_llm(prompt)
        if not text:
            return {"message": "裁判开小差了，稍等再猜一次喵……",
                    "outcome": "judge_fail", "facts": [build_fact("judge_fail")]}
        obj = self._parse_json(text)
        correct = bool(obj.get("correct")) if obj else False
        comment = str(obj.get("comment", "") or "").strip() if obj else ""

        sess["guesses"].append({"guess": guess, "correct": correct})
        if correct:
            sess["state"] = "ended"
            sess["stats"]["solved"] = sess["stats"].get("solved", 0) + 1
            await self._save_session(user_id, sess)
            solved = sess["stats"]["solved"]
            played = sess["stats"].get("played", 0)
            # 用 big_win 触发高光 → 主插件出卡片 + LLM 兴奋叙述
            return {"message": f"🎉 猜对啦！主人好厉害！汤底就是：{sess['bottom']}", "outcome": "big_win",
                    "facts": [build_fact("win", guess=guess, solved=solved),
                              build_fact("stats", played=played, solved=solved)]}

        sess["last_activity_at"] = time.time()
        sess["nudged"] = False
        await self._save_session(user_id, sess)
        msg = "还没猜对哦"
        if comment:
            msg += f"（{comment}）"
        msg += "，主人再想想喵"
        return {"message": msg, "outcome": "guess_wrong",
                "facts": [build_fact("guess_wrong", guess=guess, comment=comment)]}

    async def _reveal(self, user_id: str, sess: Dict[str, Any]) -> Dict[str, Any]:
        sess["state"] = "ended"
        await self._save_session(user_id, sess)
        return {"message": f"汤底揭晓：{sess['bottom']}（这锅汤就是这样喵）", "outcome": "reveal",
                "facts": [build_fact("reveal", soup=sess["soup"])]}

    async def _status(self, user_id: str, sess: Dict[str, Any]) -> Dict[str, Any]:
        stats = sess.get("stats", {}) or {}
        lines = [f"🍲 海龟汤 · 已玩 {stats.get('played', 0)} 局 · 猜中 {stats.get('solved', 0)} 次"]
        if sess.get("state") == "playing":
            lines.append(f"当前汤面：{sess['soup']}")
            qa = sess.get("qa", [])
            if qa:
                lines.append("问答记录：")
                for item in qa[-10:]:
                    h = f"（提示：{item['hint']}）" if item.get("hint") else ""
                    lines.append(f"  问：{item['q']} → {item['a']}{h}")
            else:
                lines.append("还没有提问，快用「提问」来推理喵")
        else:
            lines.append("现在没有进行中的汤，说「海龟汤」来一锅~")
        return {"message": "\n".join(lines), "outcome": "status",
                "facts": [build_fact("status")]}

    # ── 每秒钩子：不活跃提醒 / 超时揭晓 ─────

    async def on_tick(self, user_id: str) -> None:
        sess = await self._load_session(user_id)
        if sess.get("state") != "playing":
            return
        now = time.time()
        last = float(sess.get("last_activity_at") or sess.get("started_at") or now)
        game_timeout = float(self._cfg("game_timeout", 600))
        nudge_after = float(self._cfg("nudge_after", 300))
        if now - last >= game_timeout:
            sess["state"] = "ended"
            await self._save_session(user_id, sess)
            return await self._reveal_line(sess)      # 推送交本体(brain.tick)
        if not sess.get("nudged") and now - last >= nudge_after:
            sess["nudged"] = True
            await self._save_session(user_id, sess)
            return await self._nudge_line(sess)       # 同上

    # ── LLM 桥接 ─────────────────────────

    async def _generate_puzzle(self, category: str) -> Optional[Dict[str, str]]:
        source = str(self._cfg("puzzle_source", "mix"))
        prompts = self._cfg("prompts", {})
        if source in ("ai", "mix"):
            prompt = str(prompts.get("puzzle_system", ""))
            user_text = str(prompts.get("puzzle_user", ""))
            if category:
                user_text += f"\n\n本局指定谜题类型/范围为「{category}」，请围绕此类型生成。"
            prompt += f"\n\n{user_text}\n\n只返回 JSON 对象，字段：soup（汤面）、bottom（汤底）、difficulty（简单/中等/困难）。不要解释。"
            text = await self.call_llm(prompt, scene="soup.puzzle")
            if text:
                obj = self._parse_json(text)
                if obj and obj.get("soup") and obj.get("bottom"):
                    return {"soup": str(obj["soup"]).strip(), "bottom": str(obj["bottom"]).strip(),
                            "difficulty": str(obj.get("difficulty", "") or "").strip()}
        if source in ("mix", "local"):
            soups = _load_soups()
            if soups:
                return dict(random.choice(soups))
        return None

    async def _nudge_line(self, sess: Dict[str, Any]) -> str:
        prompt = str(self._cfg("prompts", {}).get("nudge", ""))
        if prompt:
            text = await self.call_llm(
                f"{prompt}\n\n汤面：{sess['soup']}\n请用一句话问主人要不要公布答案。",
                scene="soup.nudge")
            if text and text.strip():
                return text.strip()
        return str(self._cfg("nudge_fallback", "都猜这么久了，要公布答案吗喵？"))

    async def _reveal_line(self, sess: Dict[str, Any]) -> str:
        prompt = str(self._cfg("prompts", {}).get("reveal", ""))
        if prompt:
            text = await self.call_llm(
                f"{prompt}\n\n汤面：{sess['soup']}\n汤底：{sess['bottom']}\n请用一句话公布答案。",
                scene="soup.reveal")
            if text and text.strip():
                return text.strip()
        return f"好久没动静啦，汤底公布~\n汤底：{sess['bottom']}"

    # ── 工具 ─────────────────────────────

    def _cfg(self, key: str, default: Any = None) -> Any:
        return (getattr(self, "_config", None) or {}).get(key, default)

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\{[\s\S]*?\}", text)
            if m:
                try:
                    return json.loads(m.group(0))
                except (json.JSONDecodeError, ValueError):
                    return None
            return None

    @staticmethod
    def _parse_answer(text: str) -> tuple:
        obj = SoupBubbleGame._parse_json(text)
        if obj and obj.get("answer") in ("是", "不是", "无关"):
            return obj["answer"], str(obj.get("hint", "") or "").strip()
        # 解析失败：从原文里找答案
        for k in ("不是", "无关", "是"):
            if k in (text or ""):
                return k, ""
        return "无关", ""

    @staticmethod
    def _strip_kw(text: str, *keywords: str) -> str:
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in text:
                return text.split(kw, 1)[1].strip()
        return text.strip()

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        if any(k in text for k in ("?", "？", "吗", "呢", "么", "吧")):
            return True
        if any(text.startswith(k) for k in ("为什么", "怎么", "是不是", "有没有", "是否",
                                            "谁", "什么", "哪里", "如何", "几个", "多少", "何时")):
            return True
        return False

    @staticmethod
    def _category(c: str) -> str:
        for kw in ("发起海龟汤", "开始海龟汤", "海龟汤", "发起", "开始", "来一锅"):
            if kw in c:
                rest = c.split(kw, 1)[1].strip(" \t,，。.!！?？~")
                if not rest or len(rest) > 30:
                    return ""
                if rest.lower() in ("跳过", "默认", "不指定", "skip", "no", "无", "一局", "一锅", "玩"):
                    return ""
                return rest
        return ""

    # ── 状态（面板轮询） ─────────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        sess = await self._load_session(user_id)
        stats = sess.get("stats", {}) or {}
        return {"state": sess.get("state", "idle"), "difficulty": sess.get("difficulty", ""),
                "solved": stats.get("solved", 0), "played": stats.get("played", 0)}

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        oc = (outcome or "").lower()
        if "win" in oc:
            return "highlight"
        if "reveal" in oc or "lose" in oc:
            return "lowlight"
        return "routine"

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        return "win" in (outcome or "").lower()

    def format_fact_for_card(self, fact: Dict[str, Any]) -> tuple:
        kind = fact.get("kind", "")
        if kind == "win":
            return "🎉 猜对汤底！", ""
        if kind == "stats":
            return f"已玩 {fact.get('played', 0)} 局 · 猜中 {fact.get('solved', 0)} 次", ""
        return "", ""

    def support_panel(self) -> Optional[Dict[str, Any]]:
        """面板配置 schema（锅巴风格，供面板零前端成本生成表单）。"""
        return {
            "schemas": [
                {"label": "对局设置", "component": "Group"},
                {"field": "puzzle_source", "label": "题目来源", "component": "Select",
                 "props": {"options": [{"label": "混合（推荐）", "value": "mix"},
                                       {"label": "仅 LLM 出题", "value": "llm"},
                                       {"label": "仅本地题库", "value": "local"}]},
                 "help": "优先 LLM 出题，失败自动回退本地题库"},
                {"field": "game_timeout", "label": "超时秒数", "component": "InputNumber",
                 "props": {"min": 60, "max": 3600}, "help": "多久不猜就自动揭晓"},
                {"field": "nudge_after", "label": "提醒秒数", "component": "InputNumber",
                 "props": {"min": 30, "max": 600}, "help": "多久不活跃就提醒一次"},
                {"label": "提示语", "component": "Group"},
                {"field": "nudge_fallback", "label": "提醒话术", "component": "InputTextArea"},
                {"field": "reveal_fallback", "label": "揭晓话术", "component": "InputTextArea"},
            ]
        }

    # ── 存档 ─────────────────────────────

    def _new_session(self) -> Dict[str, Any]:
        return {"state": "idle", "soup": "", "bottom": "", "difficulty": "",
                "qa": [], "guesses": [], "started_at": 0, "last_activity_at": 0,
                "nudged": False, "stats": {"solved": 0, "played": 0}}

    async def _load_session(self, user_id: str) -> Dict[str, Any]:
        data = await self.get_user_data(user_id)
        if not isinstance(data, dict) or not data:
            data = self._new_session()
            await self.save_user_data(user_id, data)
        return data

    async def _save_session(self, user_id: str, sess: Dict[str, Any]) -> None:
        await self.save_user_data(user_id, sess)
