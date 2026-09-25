import time


class MiniGameCompanion:
    def __init__(self, cooldown_seconds=90, max_context_chars=90):
        self._cooldown_seconds = max(0, float(cooldown_seconds or 0))
        self._max_context_chars = max(20, int(max_context_chars or 90))
        self._last_feedback_at = {}
        self.current_goal = ""

    def record(self, event_data, text, priority, side_effects, memory):
        event_type = event_data.get("event_type", "minigame")
        game_type = event_data.get("game_type", "unknown")
        chess_event_type = side_effects.get("chess_event_type", event_type)
        self.current_goal = self._goal_text(event_data, chess_event_type)
        memory_text = self._memory_text(event_data, text, chess_event_type)
        memory.remember("minigame", memory_text, priority=priority)
        context_text = self._context_text(event_data, chess_event_type)
        if not context_text:
            return None
        if chess_event_type == "chess_game_start":
            return self._trim(context_text)
        if chess_event_type == "chess_game_end":
            # 棋局结束后清理对应的冷却记录，避免字典无限增长
            self._cleanup_feedback(game_type)
            return self._trim(context_text)
        if not self._can_feedback(game_type, chess_event_type):
            return None
        return self._trim(context_text)

    def _can_feedback(self, game_type, chess_event_type):
        now = time.time()
        key = f"{game_type}:{chess_event_type}"
        global_key = f"{game_type}:*"
        last_at = max(self._last_feedback_at.get(key, 0), self._last_feedback_at.get(global_key, 0))
        if now - last_at < self._cooldown_seconds:
            return False
        self._last_feedback_at[key] = now
        self._last_feedback_at[global_key] = now
        return True

    def _cleanup_feedback(self, game_type):
        """棋局结束后清理对应 game_type 的冷却记录"""
        prefix = f"{game_type}:"
        keys_to_remove = [k for k in self._last_feedback_at if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._last_feedback_at[k]

    def _memory_text(self, event_data, text, chess_event_type):
        game_name = self._game_name(event_data)
        opponent = event_data.get("opponent", "伙伴")
        move_count = event_data.get("move_count", 0)
        result = event_data.get("result", "")
        if chess_event_type == "chess_game_start":
            return f"和{opponent}开始下{game_name}"
        if chess_event_type == "chess_mid_game":
            turn = "轮到我" if event_data.get("is_maid_turn", False) else "轮到玩家"
            move_text = f"第{move_count}步" if move_count else "中盘"
            return f"{game_name}{move_text}，{turn}"
        if chess_event_type == "chess_game_end":
            result_text = {"win": "我赢了", "lose": "玩家赢了", "draw": "平局"}.get(result, "结束了")
            move_text = f"，共{move_count}步" if move_count else ""
            return f"和{opponent}的{game_name}{result_text}{move_text}"
        return text

    def _context_text(self, event_data, chess_event_type):
        game_name = self._game_name(event_data)
        move_count = event_data.get("move_count", 0)
        result = event_data.get("result", "")
        if chess_event_type == "chess_game_start":
            return f"开始陪玩家下{game_name}。用一句轻松、有期待感的开局回应就好，不要分析太长。"
        if chess_event_type == "chess_mid_game":
            if event_data.get("is_maid_turn", False):
                return f"{game_name}轮到我走了。可以短短思考、嘴硬或吐槽一下，像真的在陪玩，不要讲大段策略。"
            move_text = f"第{move_count}步" if move_count else "中盘"
            return f"{game_name}{move_text}。陪玩家看一眼局势，只回应一句简短陪玩感想，可以轻轻紧张或加油。"
        if chess_event_type == "chess_game_end":
            if result == "win":
                return f"{game_name}我赢了。用一句俏皮但不炫耀的话收尾，可以带一点得意。"
            if result == "lose":
                return f"{game_name}玩家赢了。用一句不超过二十字的祝贺或撒娇式不服收尾。"
            if result == "draw":
                return f"{game_name}平局了，用一句轻松的话夸双方势均力敌。"
            return f"{game_name}结束了，用一句简短自然的话收尾。"
        return ""

    def _goal_text(self, event_data, chess_event_type):
        game_name = self._game_name(event_data)
        opponent = event_data.get("opponent", "伙伴")
        move_count = event_data.get("move_count", 0)
        if chess_event_type == "chess_game_start":
            return f"和{opponent}一起下{game_name}"
        if chess_event_type == "chess_mid_game":
            move_text = f"第{move_count}步" if move_count else "中盘"
            return f"正在下{game_name}，{move_text}"
        if chess_event_type == "chess_game_end":
            return f"刚下完{game_name}，可以轻松收尾"
        return ""

    def _game_name(self, event_data):
        names = {"gomoku": "五子棋", "wchess": "国际象棋", "cchess": "中国象棋"}
        game_type = event_data.get("game_type", "unknown")
        return names.get(game_type, game_type)

    def _trim(self, text):
        text = str(text or "").strip()
        if len(text) > self._max_context_chars:
            return text[:self._max_context_chars - 1].rstrip() + "…"
        return text
