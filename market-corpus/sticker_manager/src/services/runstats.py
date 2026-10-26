"""本次运行的四把读数（v0.17.1，纯观测轮）。

**为什么要它**：v0.17.0 把目录搬进常驻面之后，主人反馈"还是很少发"。我去查的时候
发现三件事日志里全没有，只能手读宿主的 `GET /api/tools` 才拼得出来：

1. 那份常驻描述**到底挂上了没有**（成功路径不记日志）；
2. 她**调没调** `sticker_send`——更要紧的是"她没调"与"她调了但被闸拦下"在日志里
   长得一模一样（拒因不留痕），这两种病的处置完全相反；
3. 分母是多少（她今天开了几轮）。

没有这三行，任何"改了有没有用"的判断都是猜。本模块只做**计数与形态留痕**：
一条决策都不参与，一个行为都不改——这是"零行为变更"的观测轮，不是半轮功能。

口径纪律（反面就是"仪器坏了但读数正常"那个病）：

- **计数是内存态、进程重启归零**。所以对外一律叫「本次运行」，绝不允许写成「今日」
  ——那会让人拿半天的数当一天的数。要持久化的账本已有正主：`usage.json`。
- **只记形状，不记内容**：她的检索词只记长度，落盘日志里不许出现原话。
  分类名是我们自己的目录键（主人写的），可以记——"她挑了哪个分类、挑空了几次"
  正是本轮要看的东西。
"""

from __future__ import annotations

import time
from typing import Any


def gave_shape(**values: object) -> str:
    """把"她给了哪几种参数"压成 `id+group` 这样的短形状（什么都没给 = `none`）。

    空串与纯空白算"没给"——她经常三个都不填（那是空枪，会被拒），形状要能看出来。
    布尔按真值算（`force=False` 不算给了 force）。
    """
    keys = []
    for name, value in values.items():
        if isinstance(value, bool):
            ok = value
        else:
            ok = bool(str(value or "").strip())
        if ok:
            keys.append(name)
    return "+".join(keys) if keys else "none"


class RunStats:
    """本次运行内的：她调用工具 / 发出成功 / 被拦下。话轮数归 `TurnWatcher` 记。"""

    def __init__(self, *, now: Any = None) -> None:
        self._now = now if callable(now) else time.monotonic
        self.started_at = time.time()
        self.tool_calls = 0
        self.sticker_send_calls = 0
        self.agent_send_calls = 0
        self.sent = 0
        self.refused = 0
        # 最近一次拒因与调用形状：面板上摆一行，比四个 0 更能说明"断在哪一环"。
        self.last_reason = ""
        self.last_call = ""
        # 常驻面最后一次真换描述时的形状（未换则不动，见 `surface_applied`）。
        self.surface_chars = 0
        self.surface_categories = 0
        self.surface_tier = ""
        self.surface_at = 0.0

    # --- 记账点 ---------------------------------------------------------------

    def note_tool_call(
        self,
        *,
        name: str,
        gave: str,
        result: dict[str, Any],
        group: str = "",
        query_len: int = 0,
        count_delivery: bool = True,
    ) -> str:
        """记一次她发起的工具调用。返回拒因（成功时空串），供调用方决定日志级别。

        成功判定**只认 `ok=True` 且有 `sent`**——`sticker_list` 那种"查到了但没发"
        既不是发出也不是拦下，两类都不能虚增，否则读数就成了噪声。
        """
        self.tool_calls += 1
        if name == "sticker_send":
            self.sticker_send_calls += 1
        elif name == "agent_send":
            self.agent_send_calls += 1
        reason = "" if result.get("ok") else str(result.get("reason") or "unknown")
        shape = f"{name}({gave}" + (f" group={group}" if group else "") + (f" q_len={query_len}" if query_len else "") + ")"
        self.last_call = shape
        if reason:
            if count_delivery:
                self.refused += 1
            self.last_reason = reason
        elif count_delivery and name in {"sticker_send", "agent_send"} and result.get("sent"):
            self.sent += 1
        return reason

    def surface_applied(self, *, chars: int, categories: int, tier: str) -> None:
        """常驻面**真的换了**一次（未变时不该走到这里）。"""
        self.surface_chars = chars
        self.surface_categories = categories
        self.surface_tier = tier
        self.surface_at = time.time()

    def note_delivery(self, *, ok: bool, reason: str = "") -> None:
        """A queued tool receipt is not a send; count its eventual outcome once."""
        if ok:
            self.sent += 1
        else:
            self.refused += 1
            self.last_reason = reason

    # --- 读数 -----------------------------------------------------------------

    def snapshot(self, *, turns: int, running_sec: float | None = None) -> dict[str, Any]:
        """给面板的只读块。字段名与面板一一对上，别在这里做展示逻辑。"""
        elapsed = running_sec
        if elapsed is None:
            elapsed = max(0.0, time.time() - self.started_at)
        return {
            "turns": turns,
            "tool_calls": self.tool_calls,
            "sticker_send_calls": self.sticker_send_calls,
            "agent_send_calls": self.agent_send_calls,
            "sent": self.sent,
            "refused": self.refused,
            "last_call": self.last_call,
            "last_reason": self.last_reason,
            "running_sec": round(elapsed, 1),
            # 常驻面：0 表示这次运行还没换过描述（开机那次没记，见 `surface_at`）。
            "surface_chars": self.surface_chars,
            "surface_categories": self.surface_categories,
            "surface_tier": self.surface_tier,
        }
