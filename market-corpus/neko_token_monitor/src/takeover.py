"""Transient acknowledgement for a native character takeover attempt."""

import threading


def companion_result(outcome):
    """Opening the window is separate from confirming character replacement."""
    if outcome and outcome.get("ok"):
        return {"shown": True, "window_opened": True, "monitor_active": True,
                "takeover": "confirmed", "message": "原版人物已暂停并隐藏，猫粮 YUI 图片已接管桌面喵～"}
    reason = (outcome or {}).get("reason") or ("pause_unconfirmed" if outcome else "pending")
    detail = {
        "accessibility": "辅助功能权限不可用，请在系统设置中允许猫粮",
        "host_not_found": "没有找到运行中的 N.E.K.O 原版窗口",
        "pause_unconfirmed": "未确认原版人物已经暂停",
        "controls_unavailable": "原版工具栏暂不可用，已取消接管并保留原版人物",
        "hide_unconfirmed": "未确认原版窗口已经隐藏",
        "timeout": "等待人物替换确认超时",
        "session_ended": "本次记录已经结束，原版人物已恢复",
        "pending": "仍在等待原版人物暂停与窗口隐藏的确认",
    }.get(reason, "没有收到原版人物已暂停并隐藏的确认")
    return {"shown": False, "window_opened": True, "monitor_active": False,
            "takeover": "failed" if outcome else "pending", "reason": reason,
            "message": f"监控模式尚未启动，人物未替换。{detail}喵～"}


class TakeoverState:
    def __init__(self):
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.session_id = None
        self.result = None

    def begin(self, session_id):
        with self.lock:
            if self.session_id != session_id:
                self.session_id = session_id
                self.result = None
                self.event.clear()

    def report(self, session_id, ok, reason=None):
        with self.lock:
            if session_id != self.session_id or self.result is not None:
                return False
            self.result = {"ok": ok, "reason": reason}
            self.event.set()
            return True

    def wait(self, session_id, timeout):
        self.event.wait(timeout)
        with self.lock:
            return self.result if self.session_id == session_id else None
