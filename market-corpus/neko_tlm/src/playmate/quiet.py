import time


class QuietCompanionTrigger:
    def __init__(self, stable_seconds=90, cooldown_seconds=300):
        self._stable_seconds = max(0, int(stable_seconds or 0))
        self._cooldown_seconds = max(0, int(cooldown_seconds or 0))
        self._state = "unknown"
        self._state_since = 0
        self._last_trigger = {}

    def observe(self, state, label, recent_push_count=0, now=None):
        now = now or time.time()
        state = state or "unknown"
        if state != self._state:
            self._state = state
            self._state_since = now
            return None
        if state in ("combat", "away", "gathering", "organizing"):
            return None
        if recent_push_count > 2:
            return None
        if now - self._state_since < self._stable_seconds:
            return None
        if now - self._last_trigger.get(state, 0) < self._cooldown_seconds:
            return None
        self._last_trigger[state] = now
        return self._text_for_state(state, label)

    def _text_for_state(self, state, label):
        texts = {
            "mining": "玩家已经持续挖矿一段时间。请用一句很短的陪玩语气陪着下矿，重点是安全、照明或一起撑一会儿，不要像系统提醒。",
            "underground_exploring": "玩家已经持续地下探索一段时间。请用一句很短、带一点紧张但愿意陪着的语气回应，不要催促。",
            "base_building": "玩家已经持续建家/布置一段时间。请用一句很短的陪玩语气夸一点进度、吐槽材料或表示想帮忙，不要评价太长。",
            "fishing": "玩家已经持续钓鱼一段时间。请用一句轻松、低打扰的陪等语气回应，可以小声加油或吐槽等待，不要催鱼上钩。",
            "traveling": "玩家已经持续赶路/跑图一段时间。请用一句很短的陪伴语气回应，表达会跟着走或一起看看前面有什么。",
            "idle": "玩家安静了一段时间。请用一句很轻、自然的陪伴语气搭话，可以撒娇或问一句接下来想做什么，不要连环提问。",
            "redstone_engineering": "玩家已经持续搞红石/自动装置一段时间。请用一句很短的陪玩语气回应，可以好奇地问在做什么装置或表示看不懂但觉得厉害，不要评价设计。",
            "nether_exploring": "玩家在下界探索。请用一句很短、带一点紧张但愿意陪着的语气回应，注意提醒岩浆和恶魂，不要催促。",
            "end_exploring": "玩家在末地探索。请用一句很短的陪玩语气回应，可以感叹末地很空旷或提醒末影人，不要打断节奏。",
            "trading": "玩家在和村民交易。请用一句很短的轻松语气回应，可以吐槽村民的定价或好奇在买什么，不要打断交易节奏。",
        }
        return texts.get(state, f"玩家已经持续处于{label}一段时间。请主动用一句很短、自然、低打扰的陪玩语气回应，可以轻轻吐槽、鼓励或提出一个很小的建议；不要长篇解释，也不要像系统提醒。")
