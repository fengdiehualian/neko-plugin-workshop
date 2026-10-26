"""LLM 意图解析器：替换正则 intent.py，一次 LLM 调用产出意图+对话+打断级别。

设计原则：
1. 优先走 LLM 解析（自然语言理解，不靠词表）
2. LLM 不可用时自动 fallback 到旧 intent.parse()
3. 解析结果包含 pre_reply（解析完后先说一句）和 interrupt_level（打断级别）
4. 支持"闲聊"类意图（chat），让猫娘直接回应不给 coordinator 做任务

与旧 intent.py 的关系：
- 保留 intent.py 作为 fallback
- LLM 解析成功 → 用 LLM 结果
- LLM 挂了/超时 → 走 intent.parse() 兜底
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ..bridge import intent as fallback_intent
from ..core.context import build_user_context as _build_ctx

log = logging.getLogger(__name__)


def _safe_int(value: Any, default: int = 0, minimum: Optional[int] = None) -> int:
    """将 LLM 的 null/空串/中文数字安全转换为整数。"""
    if value is None or value == "":
        result = default
    else:
        try:
            result = int(float(str(value).strip()))
        except (TypeError, ValueError):
            digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2,
                      "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
                      "八": 8, "九": 9, "十": 10}
            text = str(value).strip()
            if text in digits:
                result = digits[text]
            elif text.startswith("十") and text[1:] in digits:
                result = 10 + digits[text[1:]]
            elif text.endswith("十") and text[:-1] in digits:
                result = digits[text[:-1]] * 10
            else:
                result = default
    return max(minimum, result) if minimum is not None else result


def _safe_float(value: Any, default: float = 0.0) -> float:
    """将 LLM 的 null/空串安全转换为有限浮点数。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default

# ── LLM 意图解析的 prompt 模板（v2：思维链 + few-shot + 上下文）──

INTENT_PARSE_PROMPT = """你是{name}，在泰拉瑞亚世界里帮主人做事的猫娘冒险者。

【游戏状态】
{game_context}

【你的状态】
{task_status}

【最近对话】
{recent_context}

【主人说】「{text}」

【分析步骤】请先思考：
1. 主人这句话是什么类型？（任务指令/闲聊/停止命令/危险预警）
2. 如果是任务，有明确终点吗？（"挖10个" vs "一直挖"）
3. 需要打断当前任务吗？紧急程度？
4. 如果是多步任务，该怎么拆解？（先A后B）
5. 如果有省略/指代（"继续"、"再来点"），参考最近对话理解

【输出格式】严格 JSON，不要任何多余文字：
{{
    "mode": "longterm|finite|stop|chat|unknown",
    "kind": "follow|mine|guard|explore|craft|fetch|goto|give|wait|combat|fish|chop|gather|...",
    "target": "目标物/地点（如 铁矿、地下、这里）",
    "amount": 数量(整数, 0=不限量),
    "pre_reply": "你的第一句回应（猫娘语气，10字内）",
    "interrupt_level": 0-3,
    "reason": "你理解的任务要点（1句话）",
    "confidence": 0.0-1.0 (你的把握度),
    "steps": [
        {{"action": "explore|mine|craft|fetch|goto|give|gather|wait|combat|climb|fish|chop", "item": "目标", "amount": 数量}},
        ...
    ]
}}

【mode 判断规则】
- longterm: 无终点持续性任务（"跟着我"、"一直挖铁"、"守在这"）
- finite: 有明确终点（"挖10个铁"、"做把镐子"、"去地下"）
- stop: 明确停止指令（"别跟了"、"停下"）
- chat: 纯闲聊/情感（"你好呀"、"累不累"、"你在干嘛"）
- unknown: 真的不懂（confidence < 0.5 时用）

【跟随模式区分（重要）】
- "跟着我"/"跟上我" → kind=follow, target=""
- "跟在我身边"/"贴身跟着"/"别离开我"/"黏着我" → kind=follow, target="stick"（贴身跟随，一直黏在主人旁边）

【interrupt_level 判断】
- 0: 闲聊，不打断（"你在干嘛呀"）
- 1: 新任务，先说话再切（"帮我挖点铁"）
- 2: 紧急关心，立即回应（"小心后面！"）
- 3: 危险，立刻停下（"快跑！"、"别动！"）

【action 类型】
- explore: 探索（"去地下看看"、"帮我找铁矿"）
- mine: 挖矿/采集（"挖铁矿"）
- craft: 合成（"做把镐子"）
- fetch: 从箱子取物（"拿点木头"）
- goto: 移动到某处（"去那边"）
- give: 给玩家物品（"给我"）
- gather: 收集掉落物（"捡起来"）
- wait: 等待（"等我一下"）
- combat: 战斗（"打史莱姆"）
- climb: 爬升（"上去"）

【steps 拆解示例】
"做10个火把给我" → [
    {{"action":"fetch", "item":"木材", "amount":10}},
    {{"action":"fetch", "item":"凝胶", "amount":10}},
    {{"action":"craft", "item":"火把", "amount":10}},
    {{"action":"give", "item":"火把", "amount":10}}
]

"去地下挖铁矿" → [
    {{"action":"goto", "item":"地下", "amount":1}},
    {{"action":"mine", "item":"铁矿", "amount":20}}
]

"帮我找找铁矿" → [
    {{"action":"explore", "item":"铁矿", "amount":1}}
]

"去钓鱼/钓点鱼" → [
    {{"action":"fish", "item":"鱼", "amount":3}}
]

"把背包里的铜做成铜镐" → [
    {{"action":"craft", "item":"铜镐", "amount":1}}
]

【上下文理解】
- 如果主人说"继续"，参考最近对话，继续上一个任务
- 如果主人说"再来点"，参考最近对话，重复上次的采集目标
- 如果主人说"换个地方"，参考最近对话，去别处做同样的事

现在开始分析并输出 JSON："""


def _parse_item_amount(rest: str):
    """从"10个铁矿""铁矿"等文本提取 (item, amount)。"""
    rest = (rest or "").strip("点些个块颗组 的附近")
    m = re.match(r"(\d+)\s*(个|块|颗|组)?\s*(.+)", rest)
    if m:
        return m.group(3).strip() or "目标", max(1, int(m.group(1)))
    if rest:
        return rest, 1
    return "", 1


@dataclass
class IntentResult:
    """LLM 解析产出的完整意图结果。"""

    mode: str = "unknown"          # longterm / finite / stop / chat / unknown
    kind: str = ""                 # follow / mine / guard / chat
    target: str = ""
    amount: int = 0
    pre_reply: str = ""            # 解析完后先说的一句
    interrupt_level: int = 0       # 打断级别 0-3
    reason: str = ""
    raw: str = ""
    steps: list = field(default_factory=list)
    source: str = "llm"            # llm / fallback / semantic
    confidence: float = 1.0        # 解析置信度 0.0-1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode, "kind": self.kind,
            "target": self.target, "amount": self.amount,
            "pre_reply": self.pre_reply, "interrupt_level": self.interrupt_level,
            "reason": self.reason, "raw": self.raw,
            "steps": self.steps, "source": self.source,
            "confidence": self.confidence,
        }

    def snapshot(self) -> Dict[str, Any]:
        """兼容旧 Intent.snapshot() 接口。"""
        return self.to_dict()


class LLMIntentParser:
    """LLM 意图解析器。

    外部提供 llm_call(text) → str 函数来调 LLM。
    如果不提供，始终走 fallback 正则解析。
    """

    def __init__(self, agent, llm_call: Optional[Callable] = None,
                 name: str = "neko") -> None:
        self.agent = agent
        self._llm_call = llm_call                  # async (prompt: str) -> str
        self._timeout = 3.0                         # LLM 调用超时
        self._last_result: Optional[IntentResult] = None
        self._name: str = name
        # 上下文记忆：最近对话
        self._recent_tasks: list = []              # [(text, intent_result), ...]
        # 历史成功指令库（用于语义相似度匹配）
        self._success_patterns: list = []          # [(text, intent_result), ...]

    def _get_task_status(self) -> str:
        """生成当前任务状态文本，注入 LLM 上下文。"""
        ex = getattr(self.agent, "executor", None)
        lt = getattr(self.agent, "longterm", None)
        parts = []
        if ex:
            cur = ex.current()
            if cur:
                parts.append(f"正在做：{cur.get('name', '未知任务')}")
        if lt:
            active = lt.active()
            if active:
                names = ", ".join(a.get("name", "") for a in active)
                parts.append(f"长期任务：{names}")
        return "\n".join(parts) if parts else "当前空闲，没有在做任务"

    def _get_game_context(self) -> str:
        """获取游戏状态上下文文本。"""
        try:
            return _build_ctx(self.agent)
        except Exception:
            return ""

    def _get_recent_context(self) -> str:
        """获取最近对话上下文（用于"继续"/"再来点"等指代理解）。"""
        if not self._recent_tasks:
            return "无"
        lines = []
        for i, (txt, res) in enumerate(self._recent_tasks[-3:]):
            lines.append(f"{i+1}. 主人说『{txt}』→ 理解为：{res.reason}")
        return "\n".join(lines)

    def record_success(self, text: str, result: IntentResult) -> None:
        """记录成功执行的指令（用于语义相似度匹配）。"""
        self._recent_tasks.append((text, result))
        if len(self._recent_tasks) > 10:
            self._recent_tasks.pop(0)

        # 只记录 mode 不是 unknown 的成功指令
        if result.mode != "unknown":
            self._success_patterns.append((text, result))
            if len(self._success_patterns) > 50:
                self._success_patterns.pop(0)

    async def parse(self, text: str) -> IntentResult:
        """主入口：解析主人的一句话。

        四层 fallback：停止词规则 → LLM → 正则 → 语义相似度 → unknown
        """
        text = (text or "").strip()
        if not text:
            return self._empty_result()

        # 0. 确定性停止词兜底：LLM 曾把"别跟了/停下"误判为 follow 长期任务，
        #    导致任务关不掉（stop 被"换新重启"）。停止是可逆动作，宁可多停。
        rule_result = self._rule_stop(text)
        if rule_result:
            return rule_result

        # 1. 尝试 LLM 解析
        if self._llm_call:
            try:
                llm_result = await self._llm_parse(text)
                if llm_result and llm_result.mode != "unknown":
                    return llm_result
                # LLM 返回 unknown 但有低置信度 → 继续尝试 fallback
            except asyncio.TimeoutError:
                log.warning("LLM 意图解析超时 (%.1fs), 降级 fallback", self._timeout)
            except Exception:
                log.warning("LLM 意图解析异常, 降级 fallback", exc_info=True)

        # 2. Fallback：正则解析
        fallback_result = self._fallback_parse(text)
        if fallback_result.mode != "unknown":
            return fallback_result

        # 3. 语义相似度匹配（在历史成功指令中找相似的）
        semantic_result = self._semantic_match(text)
        if semantic_result:
            return semantic_result

        # 4. 真的不懂 → 返回 unknown
        return self._empty_result()

    def _rule_stop(self, text: str) -> Optional[IntentResult]:
        """确定性停止词识别（规则层，优先于 LLM）。

        命中明确停止词 → 直接返回 stop 意图。kind 从文本推断任务类型
        （"别跟了"→follow），没指明任务 → kind=""（coordinator 全停）。
        """
        t = text.lower()
        stop_kws = ("别跟了", "不跟了", "别跟着", "别跟", "不用跟", "别追了",
                    "停下", "停止", "停一下", "别动", "住手",
                    "别做了", "别弄了", "别挖了", "别采了", "别打了", "别守了", "不用守",
                    "别砍了", "别砍", "不用砍", "stop", "cancel", "abort",
                    "don't follow", "do not follow", "stop following",
                    "stop mining", "stop chopping", "stop fighting", "stop guarding")
        for kw in stop_kws:
            if kw not in t:
                continue
            kind = ""
            if any(word in t for word in ("跟", "追", "follow")):
                kind = "follow"
            elif any(word in t for word in ("砍", "chop", "wood")):
                kind = "chop"  # 长期砍树任务注册为 chop（mine+木材归一化）
            elif any(word in t for word in ("挖", "采", "mine")):
                kind = "mine"
            elif any(word in t for word in ("守", "guard")):
                kind = "guard"
            elif any(word in t for word in ("打", "战斗", "fight", "combat")):
                kind = "combat"
            return IntentResult(
                mode="stop", kind=kind, target="",
                pre_reply="好的，我不做了~",
                reason=f"停止词规则命中:{kw}",
                source="rule",
            )
        return None

    async def _llm_parse(self, text: str) -> Optional[IntentResult]:
        """调用意图 LLM 做结构化解析（快速模型）。"""
        from .throttle import get_throttle
        throttle = get_throttle()
        if not throttle.acquire(source="intent_parser", priority="high"):
            log.warning("意图解析被限流，降级到 fallback")
            return None

        prompt = INTENT_PARSE_PROMPT.format(
            name=self._name,
            game_context=self._get_game_context(),
            task_status=self._get_task_status(),
            recent_context=self._get_recent_context(),
            text=text,
        )

        raw_response = await asyncio.wait_for(
            self._llm_call(prompt), timeout=self._timeout)

        if not raw_response:
            return None

        return self._parse_llm_response(raw_response, text)

    def _parse_llm_response(self, raw: str, original_text: str) -> Optional[IntentResult]:
        """解析 LLM 返回的 JSON。"""
        # 清洗：去掉可能的 markdown 代码块标记
        cleaned = raw.strip()
        for fence in ("```json", "```"):
            cleaned = cleaned.replace(fence, "").strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试找到第一个 { 到最后一个 }
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(cleaned[start:end + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None

        mode = str(data.get("mode", "unknown")).strip()
        if mode not in ("longterm", "finite", "stop", "chat", "unknown"):
            mode = "unknown"

        steps_raw = data.get("steps", [])
        steps = self._normalize_steps(steps_raw)

        # 提取置信度（如果 LLM 返回了）
        confidence = _safe_float(data.get("confidence", 1.0), 1.0)
        confidence = max(0.0, min(1.0, confidence))

        result = IntentResult(
            mode=mode,
            kind=str(data.get("kind", "") or "").strip(),
            target=str(data.get("target", "") or "").strip(),
            amount=_safe_int(data.get("amount", 0), 0, minimum=0),
            pre_reply=str(data.get("pre_reply", "") or "").strip(),
            interrupt_level=max(0, min(3, _safe_int(data.get("interrupt_level", 0)))),
            reason=str(data.get("reason", "") or "").strip(),
            raw=original_text,
            steps=steps,
            source="llm",
            confidence=confidence,
        )
        self._last_result = result
        return result

    @staticmethod
    def _normalize_steps(steps_raw) -> list:
        """把 LLM 产出的步骤统一成 dict 结构（可能给字符串，需兜底转换）。

        dict: 保留原样；str: 启发式转成 {"action", "item", "amount"}。
        """
        if not isinstance(steps_raw, list):
            return []
        out = []
        for s in steps_raw:
            if isinstance(s, dict):
                step = {k: v for k, v in s.items()}
                # Missing/invalid actions must stay visible to TaskBrain so it
                # can stop the chain honestly.  Defaulting to ``goto`` turned
                # malformed LLM JSON into an unintended navigation command.
                step["action"] = str(step.get("action", "") or "").strip()
                step["item"] = str(step.get("item", "") or "目标").strip()
                step["amount"] = _safe_int(step.get("amount", 1), 1, minimum=0)
                out.append(step)
            elif isinstance(s, str):
                t = s.strip()
                action, item, amount = "", "", 1
                if "挖" in t:
                    action = "mine"
                    item, amount = _parse_item_amount(t.split("挖")[-1])
                elif "合" in t or "做" in t:
                    action = "craft"
                    rest = t.split("合")[-1].split("做")[-1]
                    if rest.startswith("成"):
                        rest = rest[1:]  # "合成镐子" → "镐子"
                    item, amount = _parse_item_amount(rest)
                elif "打" in t or "杀" in t or "fight" in t.lower() or "kill" in t.lower() or "combat" in t.lower():
                    action = "combat"
                    rest = re.sub(r"(?i)^.*?\b(?:fight|kill|combat)\b", "", t)
                    rest = rest.split("打")[-1].split("杀")[-1]
                    item, amount = _parse_item_amount(rest)
                elif "捡" in t or "收" in t or "采" in t:
                    action = "gather"
                    item, amount = _parse_item_amount(
                        t.split("捡")[-1].split("收")[-1].split("采")[-1])
                elif "去" in t or "走到" in t:
                    action = "goto"
                    item, amount = _parse_item_amount(t.split("去")[-1].split("走到")[-1])
                out.append({"action": action,
                            "item": item or "目标", "amount": amount})
            else:
                # Preserve malformed entries as blockers; dropping one step
                # would let a partial chain be reported as the whole task.
                out.append({"action": "", "item": "目标", "amount": 1})
        return out

    def _fallback_parse(self, text: str) -> IntentResult:
        """降级到旧的 intent.py 正则解析。"""
        it = fallback_intent.parse(text)
        result = IntentResult(
            mode=it.mode,
            kind=it.kind,
            target=it.target,
            amount=it.amount,
            pre_reply="",
            interrupt_level=0,
            reason=it.reason,
            raw=it.raw,
            steps=it.steps,
            source="fallback",
            confidence=0.7,  # 正则匹配给固定置信度
        )
        self._last_result = result
        return result

    def _semantic_match(self, text: str) -> Optional[IntentResult]:
        """语义相似度匹配：在历史成功指令中找最相似的。"""
        if not self._success_patterns:
            return None

        # 简单相似度：SequenceMatcher（编辑距离）
        try:
            from difflib import SequenceMatcher
        except ImportError:
            return None

        best_match = None
        best_score = 0.0
        best_text = ""

        for hist_text, hist_result in self._success_patterns:
            score = SequenceMatcher(None, text.lower(), hist_text.lower()).ratio()
            if score > best_score:
                best_score = score
                best_match = hist_result
                best_text = hist_text

        # 相似度阈值 70%
        if best_score >= 0.7:
            log.info(f"语义匹配: '{text}' ≈ '{best_text}' (相似度={best_score:.2f})")
            # 复制结果但标记为 semantic 来源
            result = IntentResult(
                mode=best_match.mode,
                kind=best_match.kind,
                target=best_match.target,
                amount=best_match.amount,
                pre_reply="",  # 不复用旧的 pre_reply
                interrupt_level=best_match.interrupt_level,
                reason=f"参考之前的『{best_text}』",
                raw=text,
                steps=list(best_match.steps),
                source="semantic",
                confidence=best_score,
            )
            return result

        return None

    @staticmethod
    def _empty_result() -> IntentResult:
        return IntentResult(mode="unknown", source="fallback", confidence=0.0)

    @property
    def last_result(self) -> Optional[IntentResult]:
        return self._last_result
