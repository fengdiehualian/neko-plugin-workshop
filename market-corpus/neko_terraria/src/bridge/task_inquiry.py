"""任务中途询问：猫娘在执行任务遇到决策点时主动问主人。

场景：
1. 多目标分支（前方分岔，不知去哪个方向）
2. 资源不足（应该打还是等？）
3. 目标模糊（主人说"挖矿"但没说哪种矿）
4. 主人指令含糊需要确认（"帮帮"→ 帮什么？）
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Inquiry:
    """一个待决的询问。"""

    id: str
    question: str                          # 对主人说的话
    options: List[str] = field(default_factory=list)  # 可选项（供 LLM 理解）
    context: Dict[str, Any] = field(default_factory=dict)  # 发起询问时的上下文
    asked_at: float = 0.0
    timeout: float = 60.0                  # 超时（秒），超时后自己决定
    resolved: bool = False
    answer: str = ""


class TaskInquiry:
    """任务询问管理器。

    在 executor 中遇到决策点时，推送一个询问给主人，
    等主人回应后继续执行。
    """

    def __init__(self, agent, max_pending: int = 3) -> None:
        self.agent = agent
        self.max_pending = max_pending
        self._pending: List[Inquiry] = []
        self._counter = 0

    def ask(self, question: str, options: Optional[List[str]] = None,
            context: Optional[Dict] = None, timeout: float = 60.0) -> Optional[Inquiry]:
        """创建一个新询问。如果待决数已达上限，返回 None。

        调用者（executor）应该检查返回值：
        - 返回 Inquiry → 发起询问并暂停任务
        - 返回 None → 队列满了，executor 自己做决策
        """
        self.check_timeouts()
        if len(self._pending) >= self.max_pending:
            return None
        self._counter += 1
        inquiry = Inquiry(
            id=f"ask_{self._counter}",
            question=question,
            options=options or [],
            context=context or {},
            asked_at=__import__("time").time(),
            timeout=timeout,
        )
        self._pending.append(inquiry)
        return inquiry

    def match_answer(self, user_text: str) -> Optional[Inquiry]:
        """尝试用主人回复匹配一个待决询问。

        规则：
        - 命中某询问 options 里的词（如"换个地方"）→ 该询问 answered
        - auto（继续/随便/你决定）→ 自动决策
        - hold（等等/先别）→ 挂起，等后续
        - deny（不是/不要/算了/先不做/不用）→ 否定回答
        - 都不像 → 返回 None 放行——这条消息是**新指令**而非对旧问题的回答

        曾默认吞掉任意文本当答案：主人询问等待期说"帮我挖矿"会被当回答
        消费，新指令永不进入 parse/派发（"说了不执行"）。
        """
        # An expired semantic confirmation must never authorize an old task.
        self.check_timeouts()
        if not self._pending:
            return None

        text = (user_text or "").strip()
        if not text:
            return None
        from .intent import parse
        if parse(text).mode == "stop":
            return None
        text = text.lower().strip(" 。！!，,？?")

        auto_words = {"继续", "可以", "好", "行", "随便", "你决定", "你来",
                       "go ahead", "ok", "yes", "sure", "continue",
                       "换个地方", "换地方"}
        hold_words = {"等等", "先别", "等一下", "等等先", "hold",
                       "wait", "别急", "等会"}
        deny_words = {"不是", "不要", "不用", "算了", "先不做", "不了", "不对",
                       "别做", "停手", "没有", "no", "nope"}

        for inquiry in list(self._pending):
            if inquiry.resolved:
                continue
            # Negative/hold answers keep their control meaning even when the
            # same words are offered as explicit options.  In particular,
            # the semantic-confirmation prompt offers "不是"; returning that
            # raw option made the coordinator treat it as approval.
            if text in deny_words or text in hold_words:
                inquiry.answer = ("deny（主人否定了）" if text in deny_words
                                  else "hold（主人让等一下）")
                inquiry.resolved = True
                self._pending.remove(inquiry)
                return inquiry
            # 先试询问自带 options（"换个地方"/"继续挖"这类精确回答）
            if inquiry.options:
                opt = next((o for o in inquiry.options
                            if o and o.lower() == text), None)
                if opt:
                    inquiry.answer = opt
                    inquiry.resolved = True
                    self._pending.remove(inquiry)
                    return inquiry
            if text in auto_words:
                inquiry.answer = "auto（主人让猫娘自己决定）"
                inquiry.resolved = True
                self._pending.remove(inquiry)
                return inquiry
        return None  # 都不像回答 → 放行，按新指令解析

    def cancel_all(self) -> None:
        for inquiry in self._pending:
            inquiry.answer = "cancelled"
            inquiry.resolved = True
        self._pending.clear()

    def check_timeouts(self) -> List[Inquiry]:
        """检查超时询问，自动决策并返回。"""
        now = __import__("time").time()
        timed_out = []
        for inquiry in list(self._pending):
            if not inquiry.resolved and now - inquiry.asked_at > inquiry.timeout:
                inquiry.answer = "timeout（猫娘自己判断了）"
                inquiry.resolved = True
                self._pending.remove(inquiry)
                timed_out.append(inquiry)
        return timed_out

    async def wait_answer(self, inquiry: Inquiry,
                          timeout: Optional[float] = None) -> str:
        """阻塞等待主人回答（或超时自动决策）。返回答案字符串。

        与 coordinator.handle() 的 match_answer 配合：
        等待期间主人发任意消息，命中匹配即立即返回。
        """
        deadline = __import__("time").time() + (timeout or inquiry.timeout)
        while __import__("time").time() < deadline:
            if inquiry.resolved:
                return inquiry.answer
            for t in self.check_timeouts():
                if t.id == inquiry.id:
                    return t.answer
            await asyncio.sleep(0.5)
        # 最终兜底：再查一次超时
        for t in self.check_timeouts():
            if t.id == inquiry.id:
                return t.answer
        return "timeout"

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def has_pending(self) -> bool:
        self.check_timeouts()
        return len(self._pending) > 0
