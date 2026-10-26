"""中国象棋规则 + 猫娘 AI(纯逻辑, 无 IO, 可单测)。

坐标: 列 a..i(左→右), 行 1..10(1 在红方底线, 10 在黑方底线) —— 渲染时会把
行序翻过来画(红在下), 所以图上的坐标标签也是 10..1 从上到下。
内部 (f, r): f=0..8, r=0..9(r=0 是红方底线)。
颜色: "red" / "black"; 红先行。
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple

RED, BLACK = "red", "black"
FILES = "abcdefghi"
WIDTH, HEIGHT = 9, 10

#: 棋子种类: K帅/将 A仕/士 B相/象 N马 R车 C炮 P兵/卒
KINDS = "KABNRCP"
NAMES = {
    "red": {"K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵"},
    "black": {"K": "将", "A": "士", "B": "象", "N": "马", "R": "车", "C": "砲", "P": "卒"},
}
VALUE = {"K": 10000, "R": 900, "C": 450, "N": 400, "B": 200, "A": 200, "P": 100}

#: 初始局面(标准摆法)
START: Dict[Tuple[int, int], Tuple[str, str]] = {}
for _f, _k in zip(range(9), "RNBAKABNR"):
    START[(_f, 0)] = (RED, _k)
    START[(_f, 9)] = (BLACK, _k)
START[(1, 2)] = (RED, "C")
START[(7, 2)] = (RED, "C")
START[(1, 7)] = (BLACK, "C")
START[(7, 7)] = (BLACK, "C")
for _f in (0, 2, 4, 6, 8):
    START[(_f, 3)] = (RED, "P")
    START[(_f, 6)] = (BLACK, "P")

#: 花名(给台词用)
def in_board(f: int, r: int) -> bool:
    return 0 <= f < WIDTH and 0 <= r < HEIGHT


def in_palace(color: str, f: int, r: int) -> bool:
    if not (3 <= f <= 5):
        return False
    return (0 <= r <= 2) if color == RED else (7 <= r <= 9)


def own_half(color: str, r: int) -> bool:
    """没过多河的半边(相/象不能过河)。"""
    return r <= 4 if color == RED else r >= 5


def coord(f: int, r: int) -> str:
    return f"{FILES[f]}{r + 1}"


def parse_coord(text: str) -> Optional[Tuple[int, int]]:
    raw = "".join(ch for ch in str(text or "").lower() if ch.isascii() and ch.isalnum())
    if len(raw) < 2:
        return None
    letter = raw[0]
    digits = "".join(ch for ch in raw[1:] if ch.isdigit())
    if letter not in FILES or not digits:
        return None
    rank = int(digits)
    if not 1 <= rank <= 10:
        return None
    return (FILES.index(letter), rank - 1)


class Position:
    """一个象棋局面。board: {(f, r): (color, kind)}。"""

    def __init__(self, board: Optional[Dict] = None, turn: str = RED) -> None:
        self.board: Dict[Tuple[int, int], Tuple[str, str]] = dict(board or START)
        self.turn = turn
        self.moves: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []

    def clone(self) -> "Position":
        new = Position(self.board, self.turn)
        new.moves = list(self.moves)
        return new

    def find_king(self, color: str) -> Optional[Tuple[int, int]]:
        for pos, (c, k) in self.board.items():
            if c == color and k == "K":
                return pos
        return None

    # ── 走子生成 ────────────────────────────
    def _slide(self, f: int, r: int, color: str, out: List) -> None:
        for df, dr in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nf, nr = f + df, r + dr
            while in_board(nf, nr):
                target = self.board.get((nf, nr))
                if target is None:
                    out.append(((f, r), (nf, nr)))
                else:
                    if target[0] != color:
                        out.append(((f, r), (nf, nr)))
                    break
                nf += df
                nr += dr

    def _cannon(self, f: int, r: int, color: str, out: List) -> None:
        for df, dr in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nf, nr = f + df, r + dr
            screen = False
            while in_board(nf, nr):
                target = self.board.get((nf, nr))
                if not screen:
                    if target is None:
                        out.append(((f, r), (nf, nr)))
                    else:
                        screen = True          # 找到炮架, 接着找第一个敌子
                else:
                    if target is not None:
                        if target[0] != color:
                            out.append(((f, r), (nf, nr)))
                        break
                nf += df
                nr += dr

    def _horse(self, f: int, r: int, color: str, out: List) -> None:
        for df, dr, lf, lr in ((1, 2, 0, 1), (-1, 2, 0, 1), (1, -2, 0, -1), (-1, -2, 0, -1),
                               (2, 1, 1, 0), (2, -1, 1, 0), (-2, 1, -1, 0), (-2, -1, -1, 0)):
            nf, nr = f + df, r + dr
            if not in_board(nf, nr):
                continue
            if (f + lf, r + lr) in self.board:      # 蹩马腿
                continue
            target = self.board.get((nf, nr))
            if target is None or target[0] != color:
                out.append(((f, r), (nf, nr)))

    def _elephant(self, f: int, r: int, color: str, out: List) -> None:
        for df, dr in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
            nf, nr = f + df, r + dr
            if not in_board(nf, nr) or not own_half(color, nr):
                continue
            if (f + df // 2, r + dr // 2) in self.board:   # 塞象眼
                continue
            target = self.board.get((nf, nr))
            if target is None or target[0] != color:
                out.append(((f, r), (nf, nr)))

    def _advisor(self, f: int, r: int, color: str, out: List) -> None:
        for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            nf, nr = f + df, r + dr
            if not in_palace(color, nf, nr):
                continue
            target = self.board.get((nf, nr))
            if target is None or target[0] != color:
                out.append(((f, r), (nf, nr)))

    def _king(self, f: int, r: int, color: str, out: List) -> None:
        for df, dr in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nf, nr = f + df, r + dr
            if not in_palace(color, nf, nr):
                continue
            target = self.board.get((nf, nr))
            if target is None or target[0] != color:
                out.append(((f, r), (nf, nr)))

    def _pawn(self, f: int, r: int, color: str, out: List) -> None:
        forward = 1 if color == RED else -1
        steps = [(0, forward)]
        crossed = (r >= 5) if color == RED else (r <= 4)
        if crossed:
            steps += [(1, 0), (-1, 0)]
        for df, dr in steps:
            nf, nr = f + df, r + dr
            if not in_board(nf, nr):
                continue
            target = self.board.get((nf, nr))
            if target is None or target[0] != color:
                out.append(((f, r), (nf, nr)))

    def pseudo_moves(self, color: Optional[str] = None) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
        color = color or self.turn
        out: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        for (f, r), (c, kind) in list(self.board.items()):
            if c != color:
                continue
            if kind == "R":
                self._slide(f, r, color, out)
            elif kind == "C":
                self._cannon(f, r, color, out)
            elif kind == "N":
                self._horse(f, r, color, out)
            elif kind == "B":
                self._elephant(f, r, color, out)
            elif kind == "A":
                self._advisor(f, r, color, out)
            elif kind == "K":
                self._king(f, r, color, out)
            elif kind == "P":
                self._pawn(f, r, color, out)
        return out

    # ── 规则检查 ────────────────────────────
    def kings_face(self) -> bool:
        rk, bk = self.find_king(RED), self.find_king(BLACK)
        if not rk or not bk or rk[0] != bk[0]:
            return False
        f = rk[0]
        lo, hi = sorted((rk[1], bk[1]))
        return all((f, r) not in self.board for r in range(lo + 1, hi))

    def in_check(self, color: str) -> bool:
        king = self.find_king(color)
        if not king:
            return True
        foe = BLACK if color == RED else RED
        for (src, dst) in self.pseudo_moves(foe):
            if dst == king:
                return True
        return self.kings_face()

    def apply(self, src: Tuple[int, int], dst: Tuple[int, int]) -> Optional[Tuple[str, str]]:
        """走一步(不做合法性校验, 调用方负责), 返回被吃的子。"""
        captured = self.board.pop(dst, None)
        piece = self.board.pop(src)
        self.board[dst] = piece
        self.turn = BLACK if self.turn == RED else RED
        self.moves.append((src, dst))
        return captured

    def legal_moves(self, color: Optional[str] = None) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
        color = color or self.turn
        out = []
        for src, dst in self.pseudo_moves(color):
            trial = self.clone()
            trial.turn = color
            trial.apply(src, dst)
            if not trial.in_check(color):
                out.append((src, dst))
        return out

    def game_over(self) -> Optional[str]:
        """没有合法着法的一方判负(象棋里困毙也算输)。返回输家颜色或 None。"""
        if not self.legal_moves(self.turn):
            return self.turn
        return None

    # ── 给 AI / 台词用 ───────────────────────
    def material(self, color: str) -> int:
        return sum(VALUE[k] for (c, k) in self.board.values() if c == color)

    def evaluate(self, color: str) -> int:
        """从 color 视角打分(材料 + 机动性 + 过河兵加分)。"""
        foe = BLACK if color == RED else RED
        score = self.material(color) - self.material(foe)
        score += 2 * (len(self.pseudo_moves(color)) - len(self.pseudo_moves(foe)))
        for (f, r), (c, k) in self.board.items():
            if k == "P":
                if c == RED and r >= 5:
                    score += 40
                elif c == BLACK and r <= 4:
                    score -= 40
        return score

    def checkers(self, color: str) -> int:
        """正在将军 color 的棋子数(0 = 没被将)。"""
        king = self.find_king(color)
        if not king:
            return 0
        foe = BLACK if color == RED else RED
        return sum(1 for (_s, d) in self.pseudo_moves(foe) if d == king)


def search_move(pos: Position, color: str, level: int = 2,
                rng: Optional[random.Random] = None,
                node_cap: int = 60000) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """猫娘选着: 1=随手(偏吃子) 2=两层搜索 3=三层搜索(带节点上限)。"""
    rng = rng or random.Random()
    moves = pos.legal_moves(color)
    if not moves:
        return None
    if level <= 1:
        caps = [(s, d) for (s, d) in moves if d in pos.board]
        if caps and rng.random() < 0.75:
            return rng.choice(caps)
        return rng.choice(moves)

    depth = 2 if level == 2 else 3
    foe = BLACK if color == RED else RED
    budget = [node_cap]
    # ⚠️ 光靠节点数不够: 局面复杂时 3 层能跑到几秒(实测 2.7s), 会卡住插件的
    #    每一次落子。这里加硬时限, 超时就用手上最好的着法回退。
    deadline = time.monotonic() + (1.0 if level >= 3 else 0.5)

    def negamax(p: Position, side: str, d: int, alpha: int, beta: int) -> int:
        if budget[0] <= 0 or time.monotonic() > deadline:
            return p.evaluate(color)
        budget[0] -= 1
        if d == 0:
            return p.evaluate(color)
        best = -10 ** 9
        children = p.legal_moves(side)
        if not children:
            return -9000 if side == color else 9000      # 无着可走 = 输
        for s, dst in children:
            trial = p.clone()
            trial.apply(s, dst)
            value = -negamax(trial, RED if side == BLACK else BLACK, d - 1, -beta, -alpha)
            if value > best:
                best = value
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
        return best

    scored = []
    for s, dst in moves:
        trial = pos.clone()
        trial.apply(s, dst)
        value = -negamax(trial, foe, depth - 1, -10 ** 9, 10 ** 9)
        scored.append((value, (s, dst)))
    scored.sort(key=lambda item: item[0], reverse=True)
    if level == 2 and rng.random() < 0.10 and len(scored) > 1:
        return rng.choice([m for v, m in scored[:3]])
    return scored[0][1]


def move_text(pos: Position, src: Tuple[int, int], dst: Tuple[int, int],
              captured: Optional[Tuple[str, str]] = None) -> str:
    """一步棋的中文描述: 「红 车 a1→a4」/「… 吃 卒 a4」/「… 将军!」。"""
    piece = pos.board.get(dst) or (pos.turn, "P")
    color = piece[0]
    name = NAMES[color].get(piece[1], "子")
    txt = f"{'红' if color == RED else '黑'} {name} {coord(*src)}→{coord(*dst)}"
    if captured:
        txt += f" 吃 {NAMES[captured[0]].get(captured[1], '子')}"
    return txt


def board_block(pos: Position, *, last: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None,
                title: str = "棋盘") -> Dict[str, object]:
    """渲染桥的棋盘块: 交叉点摆法 + 红黑棋子 + a..i/1..10 坐标 + 最后一手高亮。"""
    rows: List[List[object]] = []
    for r in range(HEIGHT - 1, -1, -1):          # 行序翻过来(红方在下方)
        row: List[object] = []
        for f in range(WIDTH):
            piece = pos.board.get((f, r))
            if not piece:
                row.append("")
            else:
                color, kind = piece
                row.append({"g": NAMES[color].get(kind, "?"), "c": color})
        rows.append(row)
    marks: Dict[str, str] = {}
    if last:
        (sf, sr), (df, dr) = last
        marks[f"{HEIGHT - 1 - dr},{df}"] = "last"
    return {
        "type": "board",
        "title": title,
        "rows": rows,
        "placement": "cross",
        "col_labels": list(FILES),
        "row_labels": [str(r) for r in range(HEIGHT, 0, -1)],
        "marks": marks,
    }


def describe_side(pos: "Position", color: str) -> str:
    """一句话描述某方的处境(喂台词提示词)。"""
    foe = BLACK if color == RED else RED
    diff = pos.material(color) - pos.material(foe)
    parts = []
    if diff >= 200:
        parts.append("子力占优")
    elif diff <= -200:
        parts.append("少子")
    if pos.in_check(color):
        parts.append("正被将军")
    if pos.checkers(foe):
        parts.append("正在将军对方")
    pending = 0
    for src, dst in pos.pseudo_moves(color):
        if dst in pos.board and pos.board[dst][1] in ("R", "C", "N"):
            pending += 1
    if pending:
        parts.append(f"有 {pending} 个吃子点")
    return "、".join(parts) if parts else "暂时平稳"


# ── 协议适配层(boards/base.py): 只做数据搬运, 规则仍在本模块上面 ──

NAME = "中国象棋"


def new_state(**cfg: Any) -> Dict[str, Any]:
    return {"moves": [], "turn": RED, "result": "", "level": int(cfg.get("level") or 2)}


def _pos_of(state: Dict[str, Any]) -> "Position":
    pos = Position()
    for mv in state.get("moves") or []:
        try:
            src = (int(mv[0]), int(mv[1]))
            dst = (int(mv[2]), int(mv[3]))
        except (TypeError, ValueError, IndexError):
            continue
        if src in pos.board and (src, dst) in pos.pseudo_moves(pos.turn):
            pos.apply(src, dst)
    return pos


def _write(state: Dict[str, Any], pos: "Position") -> None:
    state["moves"] = [[s[0], s[1], d[0], d[1]] for s, d in pos.moves]
    state["turn"] = pos.turn


def parse_move(text: str, state: Dict[str, Any]) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    import re
    found = re.findall(r"([a-iA-I])\s*(10|[1-9])", str(text or ""))
    if len(found) < 2:
        return None
    src = parse_coord("".join(found[0]))
    dst = parse_coord("".join(found[1]))
    return (src, dst) if src and dst else None


def legal_moves(state: Dict[str, Any]) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    pos = _pos_of(state)
    if state.get("result"):
        return []
    return pos.legal_moves(pos.turn)


def apply_move(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    pos = _pos_of(state)
    src, dst = move
    who = pos.turn
    if (src, dst) not in pos.legal_moves(who):
        return {"ok": False, "reason": "illegal"}
    captured = pos.board.get(dst)
    txt = move_text(pos, src, dst, captured)
    pos.apply(src, dst)
    _write(state, pos)
    facts: List[Dict[str, Any]] = [{"kind": "move", "move": txt, "value": txt}]
    event = "cat_move" if who == BLACK else "player_move"
    if captured:
        event = "cat_capture" if who == BLACK else "player_capture"
        facts.append({"kind": event, "move": txt, "value": txt,
                      "piece": NAMES[captured[0]].get(captured[1], "子")})
    if pos.in_check(BLACK if who == RED else RED):
        event = "cat_check" if who == BLACK else "player_check"
        facts.append({"kind": event, "move": txt, "value": txt})
    elif who == RED and not captured and _hangs(pos, dst):
        event = "player_hang"
    over = ""
    if pos.game_over():
        over = "red" if who == RED else "black"
        state["result"] = over
    return {"ok": True, "facts": facts, "event": event, "over": over, "move": txt}


def _hangs(pos: "Position", dst: Tuple[int, int]) -> bool:
    """刚走的子白送? (对方能用更小的子吃掉它)"""
    piece = pos.board.get(dst)
    if not piece:
        return False
    mine = VALUE.get(piece[1], 0)
    for s, d in pos.legal_moves(BLACK):
        if d == dst:
            eater = pos.board.get(s)
            if eater and VALUE.get(eater[1], 0) < mine:
                return True
    return False


def review(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    _pos_of(state)
    _, dst = move
    trial = _pos_of(state)
    src, d = move
    if (src, d) not in trial.legal_moves(trial.turn):
        return {"quality": "illegal"}
    trial.apply(src, d)
    if trial.in_check(BLACK):
        return {"quality": "best", "note": "将军"}
    if _hangs(trial, dst):
        return {"quality": "loose", "note": "送子"}
    return {"quality": "ok"}


def situation(state: Dict[str, Any]) -> str:
    pos = _pos_of(state)
    return (f"{len(state.get('moves') or [])} 手｜你(红)：{describe_side(pos, RED)}"
            f"｜猫娘(黑)：{describe_side(pos, BLACK)}")


def text_board(state: Dict[str, Any]) -> str:
    pos = _pos_of(state)
    rows = ["   " + " ".join(list(FILES))]
    for r in range(9, -1, -1):
        rows.append(f"{r + 1:>2} " + " ".join(
            NAMES[pos.board[(f, r)][0]].get(pos.board[(f, r)][1], "?")
            if (f, r) in pos.board else "." for f in range(9)))
    return "\n".join(rows)


def pending(state: Dict[str, Any]) -> bool:
    return not state.get("result") and len(state.get("moves") or []) % 2 == 0


def cat_move(state: Dict[str, Any], level: int = 2, rng: Any = None):
    return search_move(_pos_of(state), BLACK, level, rng)


#: 同上: 引擎的 board_block 收 Position, 协议层收 state
_engine_board_block = board_block


def board_block(state: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    pos = _pos_of(state)
    last = pos.moves[-1] if pos.moves else None
    return _engine_board_block(pos, last=last, **kw)
