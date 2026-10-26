"""High-level public Neko tools; execution stays in the independent runtime."""

from plugin.sdk.plugin import Err, llm_tool


def unwrap(result):
    return {"is_error": True, "error": result.error} if isinstance(result, Err) else result.value


class NekoAutonomyTools:
    @llm_tool(
        name="minecraft_autonomy_status",
        description="读取独立 Minecraft 框架的最近状态供内部高层决策。读数不是朗读稿，不播报背包清单或查询过程；用户主动提问时简洁回答重点。先核对采样时间，不把受理或模型报告说成成功。可按 fact_key 和 offset 分页读取完整事实，或用 request_id 查意图回执；此工具不连接游戏、不调用规划模型。",
        parameters={"type": "object", "properties": {
            "fact_key": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
            "request_id": {"type": "string"}}, "additionalProperties": False},
        timeout=10,
    )
    async def minecraft_autonomy_status(self, fact_key="", offset=0, request_id="", **_):
        if not isinstance(fact_key, str) or type(offset) is not int or offset < 0:
            return {"is_error": True, "error": "invalid_fact_page"}
        result = unwrap(await self.status())
        if result.get("is_error"):
            return result
        snapshot = result.get("observation", result)
        facts = {part["key"]: part for part in snapshot.get("facts", {}).get("parts", [])}
        if fact_key:
            if fact_key not in facts:
                return {"is_error": True, "error": "fact_not_observed", "available_keys": list(facts)}
            part = facts[fact_key]
            text = part.get("text", "")
            end = min(len(text), offset + 1200)
            return {"key": fact_key, "text": text[offset:end], "original": part.get("original"),
                    "updated_at": snapshot.get("updated_at"), "source": "last_observed_snapshot",
                    "next_offset": end, "has_more": end < len(text)}
        summary = {key: snapshot.get(key) for key in (
            "phase", "game_connected", "autonomy_running", "updated_at", "source", "live_verified", "error")}
        summary["standing_goal"] = str(snapshot.get("standing_goal", ""))[:700]
        active = snapshot.get("current_goal")
        summary["current_goal"] = {key: active.get(key) for key in ("id", "mode", "goal", "origin", "parent_id")} if active else None
        summary["pending"] = [{key: item.get(key) for key in ("kind", "id", "phase", "terminal")}
                              for item in snapshot.get("pending", [])[:16]]
        summary["facts"] = [{"key": key, "text": part.get("text", "")[:240],
                             "has_more": len(part.get("text", "")) > 240, "original": part.get("original")}
                            for key, part in facts.items() if key in {
                                "place", "body", "gear", "equip", "life", "players", "foodReserve", "queue", "lastTask"}]
        summary["available_fact_keys"] = list(facts)
        if request_id:
            receipt = unwrap(await self.task_receipt(request_id=request_id))
            command = receipt.get("command") or {}
            report = command.get("planner_report") or {}
            summary["command"] = {key: command.get(key) for key in ("request_id", "kind", "delivery")}
            summary["command"]["planner_report"] = {
                "outcome": report.get("outcome"), "verified": False,
                "evidence": str(report.get("evidence", ""))[:1200]}
        return summary

    @llm_tool(
        name="minecraft_autonomy_intent",
        description="仅转达用户本轮明确提出的新游戏目标。append 排队；insert 在安全检查点插入后恢复；replace 明确停止旧动作改做新目标。自主心跳中的高层策略必须用minecraft_autonomy_review，先inspect再continue/insert/revise；不要通过此工具重发旧目标或过期实体编号。解说不投递任务。必须提供 UUID request_id；确认不明时查询或复用同一个 ID，不能生成新 ID 重放。",
        parameters={"type": "object", "properties": {
            "goal": {"type": "string", "minLength": 1, "maxLength": 4000},
            "mode": {"enum": ["append", "insert", "replace"]},
            "request_id": {"type": "string", "format": "uuid"}},
            "required": ["goal", "mode", "request_id"], "additionalProperties": False},
        timeout=10,
    )
    async def minecraft_autonomy_intent(self, goal, mode, request_id, **_):
        if mode == "append":
            return unwrap(await self.submit_goal(goal=goal, request_id=request_id))
        if mode in {"insert", "replace"}:
            return unwrap(await self.feedback(goal=goal, mode=mode, request_id=request_id))
        return {"is_error": True, "error": "explicit_intent_mode_required"}

    @llm_tool(
        name="minecraft_autonomy_stop",
        description="用户明确要求停止自主游玩时调用。停止独立框架及其游戏连接，保留账本。不是临时插入任务，也不关闭游戏服务器、模型或桌面；queued/delivered 只表示停止指令受理，仍需查状态确认断开。重试复用同一 UUID。",
        parameters={"type": "object", "properties": {"request_id": {"type": "string", "format": "uuid"}},
                    "required": ["request_id"], "additionalProperties": False},
        timeout=30,
    )
    async def minecraft_autonomy_stop(self, request_id, **_):
        return unwrap(await self.stop(request_id=request_id))
