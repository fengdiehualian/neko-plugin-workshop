"""棋类对弈 —— 一个游戏入口, 内部挂多种棋(五子棋/黑白棋/象棋/四子棋)。

游戏只给数据: 规则在 boards/<棋种>.py, 台词/心情/闲聊归本体 core/companion.py,
出图走渲染桥, 推送归 brain。本文件只做「模式路由 + 状态搬运 + 调本体陪伴」。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional

from ...core.contracts import GameAdapter
from .boards import chess, connect4, go, gomoku, othello, xiangqi

log = logging.getLogger("neko_arcade.boardgame")

MODES = {"gomoku": gomoku, "othello": othello, "xiangqi": xiangqi,
         "connect4": connect4, "go": go, "chess": chess}
MODE_WORDS = {"国际象棋": "chess", "西洋棋": "chess",     # 必须排在「象棋」之前
              "围棋": "go", "下围棋": "go",
              "五子棋": "gomoku", "五连": "gomoku",
              "黑白棋": "othello", "翻转棋": "othello", "奥赛罗": "othello",
              "象棋": "xiangqi", "中国象棋": "xiangqi",
              "四子棋": "connect4", "四连棋": "connect4"}
LEVELS = {1: "随手", 2: "正常", 3: "认真"}
_HOWTO = {"gomoku": "报坐标落子，例如 H8", "othello": "报坐标落子，例如 d3",
          "xiangqi": "报「起点 终点」，例如 b3 b7", "connect4": "报一列，例如 d（或写 4）",
          "go": "报坐标落子，例如 d4；这手不想下就说「过」",
          "chess": "报「起点 终点」，例如 e2 e4"}
_UNDO = ("悔棋", "撤回", "退一步")
_RESIGN = ("认输", "投降", "弃权", "不下了")
_BOARD = ("棋盘", "看盘", "局面")
_RESTART = ("重开", "再来一局", "重新开始", "新的一局")
_SWITCH = ("换棋种", "换一种", "下别的", "换游戏")


class BoardGame(GameAdapter):
    """棋类对弈: 你说下什么棋, 猫娘就陪你下那一盘。"""

    id = "boardgame"
    name = "棋类对弈"
    description = "和猫娘下棋：五子棋 / 黑白棋 / 中国象棋 / 四子棋，一边下一边聊"
    version = "0.1.0"
    icon = "🎴"

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._rng = random.Random()
        self._comp: Any = None

    # ── 配置 / 模式 ──────────────────────────
    def _cfg(self, key: str, default: Any = None) -> Any:
        cur: Any = getattr(self, "_config", None) or {}
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    #: 出图档位: always=每步 / key=关键节点(默认) / never=只看文字
    #: board=显式说"棋盘"; 这些时刻一定出图
    _KEY_OUTCOMES = ("start", "board", "win", "lose", "draw", "finished", "resign")
    _KEY_SUBSTR = ("capture", "check", "promote", "castle", "hang")

    def _image_mode(self) -> str:
        """出图档位。兼容老配置: true→always, false→never, 缺省→key。"""
        raw = self._cfg("show_image", "key")
        if raw is True:
            return "always"
        if raw is False or raw is None:
            return "never"
        text = str(raw).strip().lower()
        return text if text in ("always", "key", "never") else "key"

    def _should_render(self, state: Dict[str, Any], outcome: str) -> bool:
        mode = self._image_mode()
        if mode == "never":
            return False
        if mode == "always":
            return True
        if outcome in self._KEY_OUTCOMES or any(k in outcome for k in self._KEY_SUBSTR):
            return True
        moves = len(state.get("moves") or [])
        return moves > 0 and moves % 5 == 0          # 每 5 步给一张, 免得刷屏

    def _level(self) -> int:
        try:
            return max(1, min(3, int(self._cfg("level", 2))))
        except (TypeError, ValueError):
            return 2

    def _mode_cfg(self, mode_id: str) -> Dict[str, Any]:
        base: Dict[str, Any] = {"level": self._level()}
        got = (self._cfg("modes", {}) or {}).get(mode_id)
        if isinstance(got, dict):
            base.update(got)
        return base

    def _companion(self) -> Any:
        """陪伴层: 优先用本体注入的那份(带宿主猫娘人格); 没有才自己兜底。"""
        injected = getattr(self, "_companion_svc", None)
        if injected is not None:
            return injected
        if self._comp is None:
            try:
                from ...core.companion import Companion
                rt = getattr(self.plugin, "rt", None)
                cm = getattr(rt, "cfg_mgr", None)
                self._comp = Companion(
                    persona=getattr(getattr(rt, "brain", None), "persona", None),
                    llm=getattr(rt, "llm", None), config_manager=cm,
                    cfg={"enable_llm_lines": self._cfg("enable_llm_lines", True),
                         "persona_hint": self._cfg("persona_hint", "")})
            except Exception as exc:
                log.warning("companion 初始化失败: %s", exc)
                self._comp = None
        return self._comp

    async def _say(self, event: str, *, ctx: Optional[Dict[str, Any]] = None,
                   user_text: str = "", situation: str = "", cache: bool = True) -> str:
        comp = self._companion()
        if comp is None:
            return ""
        try:
            return await comp.line(self.id, event, ctx=ctx, user_text=user_text,
                                   situation=situation, cache=cache)
        except Exception as exc:
            log.warning("台词生成失败(%s): %s", event, exc)
            return ""

    # ── 指令路由 ────────────────────────────
    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        c = (cmd or "").strip()
        data = await self._load(user_id)
        state = data.get("game")
        if isinstance(state, dict):
            state["last_ts"] = time.time()
        mode_id = str((state or {}).get("mode") or data.get("mode") or "gomoku")
        try:
            limit = max(0, int(self._cfg("undo_limit", 3)))
        except (TypeError, ValueError):
            limit = 3
        self._undo_left = max(0, limit - int(data.get("undo_used") or 0))

        if any(w in c for w in _SWITCH) or ("下棋" in c and not state):
            return await self._catalog(data, user_id)
        for word, mid in MODE_WORDS.items():
            if word in c:
                if not isinstance(state, dict) or state.get("over") or mid != mode_id:
                    return await self._new_game(data, user_id, mid)
                return await self._pack(state, await self._say("resume", situation=self._situation(state)),
                                  outcome="board")
        if isinstance(state, dict) and not state.get("over"):
            if any(w in c for w in _RESIGN):
                return await self._finish(data, user_id, state, result="black", event="resign")
            if any(w in c for w in _UNDO):
                return await self._undo(data, user_id, state)
            if any(w in c for w in _BOARD):
                return await self._pack(state, await self._say("board", situation=self._situation(state)),
                                  outcome="board")
            mode = MODES.get(mode_id)
            move = mode.parse_move(c, state) if mode else None
            if move is not None:
                return await self._play(data, user_id, state, mode, move)
            reply = await self._say("chat", user_text=c, situation=self._situation(state),
                                    cache=False)
            return await self._pack(state, reply or "嗯嗯，本喵听着喵（轮到你走）",
                              outcome="chat", facts=[{"kind": "chat", "value": c[:40]}])
        if isinstance(state, dict) and state.get("over"):
            # 已经结束的对局: 认输/悔棋都要给"这局结束了"的准话, 不能报"没有对决"
            if any(w in c for w in _RESIGN) or any(w in c for w in _UNDO):
                line = await self._say("board", situation=self._situation(state))
                record = self._record_line(data)
                return await self._pack(state, f"{line}\n这局已经结束啦，{record}｜说「重开」再来一盘",
                                        outcome="finished")
        if any(w in c for w in _RESTART) or "下棋" in c:
            return await self._new_game(data, user_id, mode_id)
        if any(w in c for w in _RESIGN + _UNDO + _BOARD):
            return {"message": "现在没在下棋喵～说「下棋」看看有哪几种棋，或者直接说棋种名开局",
                    "facts": [{"kind": "hint"}], "outcome": "idle"}
        return {"message": "想下哪种棋喵？五子棋 / 黑白棋 / 中国象棋 / 四子棋 / 围棋 / 国际象棋",
                "facts": [{"kind": "hint"}], "outcome": "idle"}

    async def _catalog(self, data: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        lines = ["🎴 想下哪种棋喵？"]
        lines += [f"· {m.NAME}" for m in MODES.values()]
        lines.append("直接说「五子棋」「象棋」「黑白棋」「四子棋」就开局")
        return {"message": "\n".join(lines), "facts": [{"kind": "hint"}], "outcome": "hint"}

    async def _new_game(self, data: Dict[str, Any], user_id: str, mode_id: str) -> Dict[str, Any]:
        old_state = data.get("game")
        if isinstance(old_state, dict) and not old_state.get("over") \
                and (old_state.get("moves") or []):
            stats = data.setdefault("stats", self._new_stats())
            stats["aborted"] = int(stats.get("aborted") or 0) + 1   # 中途换棋种/重开
            # (对话记忆归 core/companion, 适配器不再自存 talk 缓冲)
        mode = MODES.get(mode_id) or gomoku
        state = dict(mode.new_state(**self._mode_cfg(mode_id)))
        state.update({"mode": mode_id, "started": time.time(), "over": "",
                      "result": "", "talk": [], "nudges": 0, "last_nudge": 0,
                      "last_ts": time.time()})
        data["game"] = state
        data["mode"] = mode_id
        data["undo_used"] = 0
        await self._save(user_id, data)
        record = self._record_line(data)
        last = self._last_result_text(data, mode_id)
        line = await self._say("start",
                               ctx={"event_desc": f"开局一盘{mode.NAME}；对手战绩：{record}；{last}"},
                               situation=f"{self._situation(state)}；{record}；{last}")
        head = "\n".join(x for x in (line, f"（{record}{'；' + last if last else ''}）",
                                      _HOWTO.get(mode_id, "")) if x)
        return await self._pack(state, head, outcome="start")

    async def _play(self, data: Dict[str, Any], user_id: str, state: Dict[str, Any],
                    mode: Any, move: Any) -> Dict[str, Any]:
        out = mode.apply_move(state, move)
        if not out.get("ok"):
            line = await self._say("illegal", situation=self._situation(state))
            return await self._pack(state, line or "这步走不了喵", outcome="illegal",
                              facts=[{"kind": "illegal"}])
        facts = list(out.get("facts") or [])
        event = str(out.get("event") or "player_move")
        if out.get("over"):
            return await self._finish(data, user_id, state, result=str(out["over"]),
                                      event=event, facts=facts)
        # ⚠️ pending() 的含义是"轮到玩家"; 玩家走完就不是他的回合了,
        #    该猫娘动 —— 之前写成 if pending(...) 导致她永远不应手(踩过)。
        if not mode.pending(state):
            her = mode.cat_move(state, level=int(state.get("level") or self._level()),
                                rng=self._rng)
            if her is not None:
                her_out = mode.apply_move(state, her)
                if her_out.get("ok"):
                    facts.extend(her_out.get("facts") or [])
                    if her_out.get("over"):
                        data["game"] = state
                        await self._save(user_id, data)
                        return await self._finish(data, user_id, state,
                                                  result=str(her_out["over"]),
                                                  event=str(her_out.get("event") or "lose"),
                                                  facts=facts)
        line = await self._say(event, ctx={"move": str(out.get("coord") or out.get("move") or ""),
                                           "piece": next((f.get("piece") for f in facts
                                                          if f.get("piece")), "")},
                               situation=self._situation(state))
        data["game"] = state
        await self._save(user_id, data)
        return await self._pack(state, line, outcome=event, facts=facts)

    async def _undo(self, data: Dict[str, Any], user_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            limit = max(0, int(self._cfg("undo_limit", 3)))
        except (TypeError, ValueError):
            limit = 3
        moves = list(state.get("moves") or [])
        if int(data.get("undo_used") or 0) >= limit:
            line = await self._say("undo", situation=self._situation(state))
            return await self._pack(state, f"{line}（本局 {limit} 次悔棋都用完了喵）",
                                    outcome="undo")
        if len(moves) < 2:
            return await self._pack(state, "还没得悔喵～至少下两手之后再说",
                                    outcome="undo")
        del moves[-2:]
        state["moves"] = moves
        state["over"] = ""
        state["result"] = ""
        data["undo_used"] = int(data.get("undo_used") or 0) + 1
        self._undo_left = max(0, limit - int(data["undo_used"]))   # 撤完要重算余量
        data["game"] = state
        await self._save(user_id, data)
        line = await self._say("undo", situation=self._situation(state))
        return await self._pack(state, line, outcome="undo", facts=[{"kind": "undo"}])

    async def _finish(self, data: Dict[str, Any], user_id: str, state: Dict[str, Any],
                      *, result: str, event: str,
                      facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        mode = MODES.get(str(state.get("mode") or "gomoku")) or gomoku
        stats = data.setdefault("stats", self._new_stats())
        stats["games"] = int(stats.get("games") or 0) + 1
        stats["moves"] = int(stats.get("moves") or 0) + len(state.get("moves") or [])
        if result == "draw":
            stats["draws"] = int(stats.get("draws") or 0) + 1
        elif result == "red":
            stats["wins"] = int(stats.get("wins") or 0) + 1
            stats["streak"] = int(stats.get("streak") or 0) + 1
            stats["best_streak"] = max(int(stats.get("best_streak") or 0), stats["streak"])
        else:
            stats["losses"] = int(stats.get("losses") or 0) + 1
            stats["streak"] = 0
        recent = data.setdefault("recent_results", [])
        recent.append({"mode": str(state.get("mode") or "gomoku"), "result": result,
                       "moves": len(state.get("moves") or []), "ts": time.time()})
        del recent[:-5]
        per = stats.setdefault("by_mode", {})
        key_mode = str(state.get("mode") or "gomoku")
        per[key_mode] = int(per.get(key_mode) or 0) + 1
        state["over"] = True
        state["result"] = result
        data["game"] = state
        data["undo_used"] = 0
        await self._save(user_id, data)
        key = "win" if result == "red" else ("lose" if result == "black" else "draw")
        line = await self._say(key, situation=self._situation(state))
        record = (f"{mode.NAME}战绩：{stats['wins']} 胜 {stats['losses']} 负"
                  + (f" {stats['draws']} 平" if stats.get("draws") else ""))
        out_facts = list(facts or [])
        out_facts.append({"kind": key, "value": record})
        msg = f"{line}\n\n{self._situation(state)}\n{record}"
        return await self._pack(state, msg, outcome=key, facts=out_facts)

    # ── 打包 ────────────────────────────────
    def _last_result_text(self, data: Dict[str, Any], mode_id: str) -> str:
        """上一同棋种的战绩(她开局会提): 「上次你赢了」/「上次本喵赢了」。"""
        for item in reversed(data.get("recent_results") or []):
            if str(item.get("mode")) != mode_id:
                continue
            if item.get("result") == "red":
                return "上次是你赢了"
            if item.get("result") == "black":
                return "上次是本喵赢了"
            return "上次是平局"
        return ""

    def _record_line(self, data: Dict[str, Any]) -> str:
        """一句话战绩(给结束/无对局的提示用)。"""
        st = data.get("stats") or {}
        streak = int(st.get("streak") or 0)
        return (f"{int(st.get('wins') or 0)} 胜 {int(st.get('losses') or 0)} 负"
                + (f" {int(st['draws'])} 平" if st.get("draws") else "")
                + (f"，{streak} 连胜" if streak >= 2 else "")
                + (f"，{int(st['aborted'])} 次中途换棋" if st.get("aborted") else ""))

    def _situation(self, state: Dict[str, Any]) -> str:
        mode = MODES.get(str(state.get("mode") or "gomoku"))
        if mode is None:
            return ""
        try:
            return mode.situation(state)
        except Exception:
            return ""

    async def _pack(self, state: Dict[str, Any], line: str, *, outcome: str,
                    facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """打包结果: 每步都给棋盘图(渲染桥); 出图不可用才退回文字棋盘。

        ⚠️ 合并棋类时这里漏过出图, 结果线上"只有文字没有棋盘图"(用户实测反馈),
        所以这里把出图放回唯一出口 —— 所有落子/开局都走它。
        """
        mode = MODES.get(str(state.get("mode") or "gomoku"))
        lv = LEVELS.get(int(state.get("level") or 2), "正常")
        head = f"🎴 {mode.NAME}（难度 {lv}）" if mode else "🎴 棋类对弈"
        undo_txt = f"｜悔棋剩 {getattr(self, '_undo_left', 0)} 次"
        msg = "\n".join(x for x in (line, f"{head}｜{self._situation(state)}{undo_txt}") if x)
        result: Dict[str, Any] = {"message": msg,
                                  "facts": list(facts or [{"kind": outcome}]),
                                  "outcome": outcome}
        if mode is None:
            return result
        if not self._should_render(state, outcome):
            # 控刷屏: 不出的那几步只给一句状态, 想看盘随时说「棋盘」
            result["message"] = f"{msg}\n（发「棋盘」可以随时出图）"
            return result
        png = await self._board_png(mode, state)
        if png:
            result["images"] = [self.build_image("", png)]
        else:
            try:                                    # 渲染不可用 → 文字棋盘兜底
                result["message"] = f"{msg}\n\n{mode.text_board(state)}"
            except Exception:
                pass
        return result

    async def _board_png(self, mode: Any, state: Dict[str, Any]) -> Optional[bytes]:
        """交给渲染桥出图(游戏只给棋盘数据块)。"""
        try:
            block = mode.board_block(state)
        except Exception as exc:
            log.warning("棋盘块生成失败: %s", exc)
            return None
        mode_id = str(state.get("mode") or "gomoku")
        try:
            return await self.render_page(
                title=f"{mode.NAME} · 猫娘对弈",
                subtitle=self._situation(state),
                blocks=[block],
                rows=[["玩法", f"{mode.NAME}（难度 {LEVELS.get(int(state.get('level') or 2), '正常')}）"],
                      ["落子", _HOWTO.get(mode_id, "")]],
                commands=[["棋盘", "重新出图"], ["悔棋", "撤回你和猫娘各一手"],
                          ["认输", "结束这一局"]],
                tip=f"轮到你了喵：{_HOWTO.get(mode_id, '')}", theme="light")
        except Exception as exc:
            log.warning("棋盘出图失败(退回文字棋盘): %s", exc)
            return None

    # ── 生命周期 / 面板 ─────────────────────
    async def on_tick(self, user_id: str) -> None:
        """局中静默提醒: 阈值/节流策略在本体 companion.should_nudge。"""
        try:
            data = await self._load(user_id)
            state = data.get("game")
            if not isinstance(state, dict) or state.get("over"):
                return
            mode = MODES.get(str(state.get("mode") or "gomoku"))
            if mode is None or not mode.pending(state):
                return
            comp = self._companion()
            if comp is None or not comp.should_nudge(
                    float(state.get("last_ts") or 0), nudges=int(state.get("nudges") or 0)):
                return
            state["nudges"] = int(state.get("nudges") or 0) + 1
            state["last_ts"] = time.time()
            data["game"] = state
            await self._save(user_id, data)
            line = await self._say("nudge", situation=self._situation(state), cache=False)
            return line                     # 推送由本体 brain 负责(游戏不自己推)
        except Exception as exc:
            log.warning("boardgame on_tick 异常: %s", exc)

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        try:
            data = await self._load(user_id)
        except Exception:
            return {}
        state = data.get("game") or {}
        stats = data.get("stats") or {}
        mode = MODES.get(str(state.get("mode") or "gomoku"))
        return {"in_game": bool(state) and not state.get("over"),
                "mode": mode.NAME if mode else "",
                "moves": len(state.get("moves") or []),
                "level": LEVELS.get(int(state.get("level") or 2), "正常"),
                "wins": int(stats.get("wins") or 0),
                "losses": int(stats.get("losses") or 0),
                "draws": int(stats.get("draws") or 0),
                "streak": int(stats.get("streak") or 0)}

    def support_panel(self) -> Dict[str, Any]:
        return {"schemas": [
            {"label": "对局", "component": "Group"},
            {"field": "level", "label": "猫娘棋力(全局)", "component": "Select",
             "props": {"options": [{"label": "1 随手", "value": 1},
                                   {"label": "2 正常", "value": 2},
                                   {"label": "3 认真", "value": 3}]}},
            {"field": "undo_limit", "label": "每局可悔棋次数", "component": "InputNumber",
             "props": {"min": 0, "max": 20}},
            {"field": "modes.gomoku.size", "label": "五子棋棋盘大小",
             "component": "InputNumber", "props": {"min": 9, "max": 19}},
            {"field": "modes.othello.level", "label": "黑白棋棋力", "component": "Select",
             "props": {"options": [{"label": "1", "value": 1}, {"label": "2", "value": 2},
                                   {"label": "3", "value": 3}]}},
            {"field": "modes.xiangqi.level", "label": "象棋棋力", "component": "Select",
             "props": {"options": [{"label": "1", "value": 1}, {"label": "2", "value": 2},
                                   {"label": "3", "value": 3}]}},
            {"label": "说话与出图", "component": "Group"},
            {"field": "enable_llm_lines", "label": "用 LLM 生成台词", "component": "Switch"},
            {"field": "show_image", "label": "棋盘出图时机", "component": "Select",
             "props": {"options": [{"label": "关键节点(推荐)", "value": "key"},
                                   {"label": "每步都出", "value": "always"},
                                   {"label": "只看文字", "value": "never"}]},
             "help": "关键节点=开局/吃子/将军/胜负，另外每 5 步补一张；说「棋盘」随时出图"},
            {"field": "persona_hint", "label": "台词人设提示", "component": "InputTextArea"},
        ]}

    @staticmethod
    def _new_stats() -> Dict[str, Any]:
        return {"games": 0, "wins": 0, "losses": 0, "draws": 0, "aborted": 0,
                "moves": 0, "streak": 0, "best_streak": 0, "by_mode": {}}

    def _new_player(self) -> Dict[str, Any]:
        return {"stats": self._new_stats(), "game": None, "mode": "gomoku", "undo_used": 0}

    async def _load(self, user_id: str) -> Dict[str, Any]:
        data = await self.get_user_data(user_id)
        if not isinstance(data, dict):
            return self._new_player()
        base = self._new_player()
        stats = data.get("stats")
        if not isinstance(stats, dict):
            stats = base["stats"]
        for key, value in base["stats"].items():
            stats.setdefault(key, value)
        data["stats"] = stats
        return data

    async def _save(self, user_id: str, data: Dict[str, Any]) -> None:
        await self.save_user_data(user_id, data)
