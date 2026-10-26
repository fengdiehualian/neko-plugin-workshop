"""国际象棋 —— 按 boards/base.py 协议写的子玩法。

覆盖: 常规走子、吃子、将军/将死/逼和、王车易位、吃过路兵、兵升变(默认升后)。
未做: 三次重复、50 步和棋(可在局面描述里提示, 不影响对局)。

坐标列 a..h, 行 1..8(1 在白方底线)。棋子码 = 颜色 + 种类: wP/bK ...
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

NAME = "国际象棋"
FILES = "abcdefgh"
BACK = "rnbqkbnr"
VALUE = {"P": 100, "N": 300, "B": 320, "R": 500, "Q": 900, "K": 20000}
#: 中心/推进加成(极简位置表): 越靠中心越值钱
CENTER = ((0, 1, 2, 3, 3, 2, 1, 0), (1, 2, 3, 4, 4, 3, 2, 1), (2, 3, 4, 5, 5, 4, 3, 2),
          (3, 4, 5, 6, 6, 5, 4, 3), (3, 4, 5, 6, 6, 5, 4, 3), (2, 3, 4, 5, 5, 4, 3, 2),
          (1, 2, 3, 4, 4, 3, 2, 1), (0, 1, 2, 3, 3, 2, 1, 0))


def _empty() -> List[List[str]]:
    return [[""] * 8 for _ in range(8)]


def new_state(**cfg: Any) -> Dict[str, Any]:
    board = _empty()
    for f, piece in enumerate(BACK):
        board[0][f] = "w" + piece.upper()
        board[7][f] = "b" + piece.upper()
    for f in range(8):
        board[1][f] = "wP"
        board[6][f] = "bP"
    return {"board": [row[:] for row in board], "turn": "w", "ep": "", "castling": "KQkq",
            "halfmove": 0, "over": "", "result": "", "level": int(cfg.get("level") or 2),
            "moves": []}


def _board(state: Dict[str, Any]) -> List[List[str]]:
    rows = state.get("board")
    return [list(r) for r in rows] if rows else _empty()


def coord(f: int, r: int) -> str:
    return f"{FILES[f]}{r + 1}"


def parse_move(text: str, state: Dict[str, Any]) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    import re
    found = re.findall(r"([a-hA-H])\s*([1-8])", str(text or ""))
    if len(found) < 2:
        return None
    pts = [(FILES.index(a.lower()), int(b) - 1) for a, b in found[:2]]
    return (pts[0], pts[1])


def _colour(piece: str) -> str:
    return piece[0] if piece else ""


def _kind(piece: str) -> str:
    return piece[1] if piece else ""


def _on(f: int, r: int) -> bool:
    return 0 <= f < 8 and 0 <= r < 8


def _pseudo(board: List[List[str]], f: int, r: int, ep: str,
            castling: str) -> List[Tuple[Tuple[int, int], Tuple[int, int], str]]:
    """伪合法着法: (from, to, 特殊标记)。标记: '' / 'ep' / 'castle' / 'promote'"""
    piece = board[r][f]
    if not piece:
        return []
    colour, kind = _colour(piece), _kind(piece)
    foe = "w" if colour == "b" else "b"
    out: List[Tuple[Tuple[int, int], Tuple[int, int], str]] = []
    fwd = 1 if colour == "w" else -1

    def push(nf: int, nr: int, flag: str = "") -> None:
        if _on(nf, nr) and _colour(board[nr][nf]) != colour:
            out.append(((f, r), (nf, nr), flag))

    if kind == "P":
        if _on(f, r + fwd) and not board[r + fwd][f]:
            push(f, r + fwd, "promote" if (r + fwd) in (0, 7) else "")
            start = 1 if colour == "w" else 6
            if r == start and not board[r + 2 * fwd][f]:
                push(f, r + 2 * fwd)
        for df in (-1, 1):
            nf, nr = f + df, r + fwd
            if not _on(nf, nr):
                continue
            target = board[nr][nf]
            if target and _colour(target) == foe:
                push(nf, nr, "promote" if nr in (0, 7) else "")
            elif ep and coord(nf, nr) == ep:
                out.append(((f, r), (nf, nr), "ep"))
    elif kind == "N":
        for df, dr in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
            push(f + df, r + dr)
    elif kind == "K":
        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                if df or dr:
                    push(f + df, r + dr)
        rank = 0 if colour == "w" else 7
        if r == rank and f == 4 and not _attacked(board, 4, rank, foe):
            rights = "KQ" if colour == "w" else "kq"
            if rights[0] in castling and not board[rank][5] and not board[rank][6] \
                    and not _attacked(board, 5, rank, foe):
                out.append(((f, r), (6, rank), "castle"))
            if rights[1] in castling and not board[rank][3] and not board[rank][2] \
                    and not board[rank][1] and not _attacked(board, 3, rank, foe):
                out.append(((f, r), (2, rank), "castle"))
    else:
        dirs = {"B": ((1, 1), (1, -1), (-1, 1), (-1, -1)),
                "R": ((0, 1), (0, -1), (1, 0), (-1, 0)),
                "Q": ((1, 1), (1, -1), (-1, 1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0))}
        for df, dr in dirs.get(kind, ()):
            nf, nr = f + df, r + dr
            while _on(nf, nr):
                target = board[nr][nf]
                if not target:
                    out.append(((f, r), (nf, nr), ""))
                else:
                    if _colour(target) == foe:
                        out.append(((f, r), (nf, nr), ""))
                    break
                nf += df
                nr += dr
    return out


def _attacked(board: List[List[str]], f: int, r: int, by: str) -> bool:
    """by 方是否攻击 (f,r)。⚠️ 必须**不递归**调用 _pseudo —— 易位合法性要查
    "路过的格子是否被攻击", 而 _pseudo 又用它, 会无限递归(踩过)。这里直接按
    攻击模式反查。"""
    # 兵: by 方兵向上(白 +1 / 黑 -1)斜攻, 所以攻击者在其反方向
    pawn = 1 if by == "w" else -1
    for df in (-1, 1):
        nf, nr = f - df, r - pawn
        if _on(nf, nr) and board[nr][nf] == by + "P":
            return True
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df or dr:
                nf, nr = f + df, r + dr
                if _on(nf, nr):
                    piece = board[nr][nf]
                    if piece == by + "K":
                        return True
    for df, dr in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
        nf, nr = f + df, r + dr
        if _on(nf, nr) and board[nr][nf] == by + "N":
            return True
    for df, dr, kinds in ((0, 1, "RQ"), (0, -1, "RQ"), (1, 0, "RQ"), (-1, 0, "RQ"),
                          (1, 1, "BQ"), (1, -1, "BQ"), (-1, 1, "BQ"), (-1, -1, "BQ")):
        nf, nr = f + df, r + dr
        while _on(nf, nr):
            piece = board[nr][nf]
            if piece:
                if _colour(piece) == by and _kind(piece) in kinds:
                    return True
                break
            nf += df
            nr += dr
    return False


def _king(board: List[List[str]], colour: str) -> Optional[Tuple[int, int]]:
    for r in range(8):
        for f in range(8):
            if board[r][f] == colour + "K":
                return (f, r)
    return None


def in_check(board: List[List[str]], colour: str) -> bool:
    king = _king(board, colour)
    if not king:
        return True
    return _attacked(board, king[0], king[1], "w" if colour == "b" else "b")


def _apply(board: List[List[str]], src, dst, flag: str) -> Dict[str, Any]:
    f, r = src
    nf, nr = dst
    piece = board[r][f]
    captured = board[nr][nf]
    board[nr][nf] = piece
    board[r][f] = ""
    extra: Dict[str, Any] = {"ep": ""}
    if flag == "ep":
        board[r][nf] = ""                       # 吃过路兵: 吃掉旁边那枚
        captured = ("w" if _colour(piece) == "b" else "b") + "P"
    if flag == "promote":
        board[nr][nf] = _colour(piece) + "Q"    # 默认升后
    if flag == "castle":
        rank = nr
        if nf == 6:                             # 短易位: 车从 h→f
            board[rank][5] = board[rank][7]
            board[rank][7] = ""
        else:                                   # 长易位: 车从 a→d
            board[rank][3] = board[rank][0]
            board[rank][0] = ""
    if _kind(piece) == "P" and abs(nr - r) == 2:
        extra["ep"] = coord(f, (r + nr) // 2)
    return {"captured": captured, "extra": extra}


def legal_moves(state: Dict[str, Any]) -> List[Any]:
    board = _board(state)
    who = state.get("turn") or "b"
    if state.get("over"):
        return []
    out: List[Any] = []
    for r in range(8):
        for f in range(8):
            if _colour(board[r][f]) != who:
                continue
            for src, dst, flag in _pseudo(board, f, r, state.get("ep") or "", state.get("castling") or ""):
                trial = [row[:] for row in board]
                _apply(trial, src, dst, flag)
                if not in_check(trial, who):
                    out.append((src, dst))
    return out


def apply_move(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    board = _board(state)
    who = state.get("turn") or "b"
    src, dst = move
    legal = legal_moves({**state, "board": [row[:] for row in board]})
    if (src, dst) not in legal:
        return {"ok": False, "reason": "illegal"}
    f, r = src
    nf, nr = dst
    flag = ""
    for s, d, fl in _pseudo(board, f, r, state.get("ep") or "", state.get("castling") or ""):
        if s == src and d == dst:
            flag = fl
            break
    piece = board[r][f]
    out = _apply(board, src, dst, flag)
    captured = out["captured"]
    # 易位权: 动了王/车, 或车被吃 → 相应撤销
    rights = list(state.get("castling") or "")
    if _kind(piece) == "K":
        rights = [x for x in rights if x.lower() not in ("k", "q") or (_colour(piece) == "w") != x.isupper()]
    for ch in ("K", "Q", "k", "q"):
        square = {"K": (7, 0), "Q": (0, 0), "k": (7, 7), "q": (0, 7)}[ch]
        if ch in rights and (src == square or dst == square):
            rights.remove(ch)
    state.update({"board": board, "turn": "w" if who == "b" else "b",
                  "ep": out["extra"].get("ep", ""), "castling": "".join(rights),
                  "moves": list(state.get("moves") or []) + [[src[0], src[1], dst[0], dst[1]]]})
    facts: List[Dict[str, Any]] = [{"kind": "move",
                                    "coord": f"{coord(*src)}→{coord(*dst)}",
                                    "value": f"{coord(*src)}→{coord(*dst)}"}]
    event = "player_move" if who == "b" else "cat_move"
    if captured:
        event = "player_capture" if who == "b" else "cat_capture"
        facts.append({"kind": event, "piece": _kind(captured), "value": "吃子"})
    if flag == "castle":
        facts.append({"kind": "castle", "value": "易位"})
    if flag == "ep":
        facts.append({"kind": "ep", "value": "吃过路兵"})
    if _kind(piece) == "P" and nr in (0, 7):
        facts.append({"kind": "promote", "value": "升变"})
    foe = "w" if who == "b" else "b"
    if in_check(board, foe):
        event = "cat_check" if who == "b" else "player_check"
        facts.append({"kind": event, "value": "将军"})
    over = ""
    nxt = state.get("turn") or ("w" if who == "b" else "b")
    if not legal_moves(state):
        # ⚠️ 判的是"轮到的对方": 对方无着且被将 = 走子方将死他; 无着但没被将 = 逼和
        over = ("black" if who == "b" else "red") if in_check(board, nxt) else "draw"
        state["over"] = over
        state["result"] = over
    return {"ok": True, "facts": facts, "event": event, "over": over,
            "coord": f"{coord(*src)}→{coord(*dst)}"}


def board_block(state: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    board = _board(state)
    glyph = {"P": "♟", "N": "♞", "B": "♝", "R": "♜", "Q": "♛", "K": "♚"}
    rows: List[List[object]] = []
    for r in range(7, -1, -1):
        row: List[object] = []
        for f in range(8):
            piece = board[r][f]
            if not piece:
                row.append("")
            else:
                mark = glyph.get(_kind(piece), "?")
                if _colour(piece) == "w":
                    mark = {"P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔"}[_kind(piece)]
                row.append({"g": mark, "c": "white" if _colour(piece) == "w" else "black"})
        rows.append(row)
    marks = {}
    last = (state.get("moves") or [None])[-1]
    if last:
        marks[f"{7 - int(last[3])},{int(last[2])}"] = "last"
    return {"type": "board", "title": kw.get("title") or "国际象棋（你执白）",
            "rows": rows, "placement": "cell", "col_labels": list(FILES),
            "row_labels": [str(i) for i in range(8, 0, -1)], "marks": marks}


def situation(state: Dict[str, Any]) -> str:
    board = _board(state)
    who = "你" if state.get("turn") == "b" else "猫娘"
    tail = ""
    if in_check(board, state.get("turn") or "b"):
        tail = "，正被将军"
    if state.get("over"):
        tail = "，将死" if state["over"] == "red" else ("，逼和" if state["over"] == "draw" else "，猫娘将死你")
    return f"{len(state.get('moves') or [])} 手，轮到{who}{tail}"


def text_board(state: Dict[str, Any]) -> str:
    board = _board(state)
    rows = ["   " + " ".join(list(FILES))]
    for r in range(7, -1, -1):
        rows.append(f"{r + 1:>2} " + " ".join(
            (board[r][f] if board[r][f] else "..")[-2:] for f in range(8)))
    return "\n".join(rows)


def pending(state: Dict[str, Any]) -> bool:
    return not state.get("over") and state.get("turn") == "w"


def _material(board: List[List[str]], colour: str) -> int:
    total = 0
    for r in range(8):
        for f in range(8):
            piece = board[r][f]
            if _colour(piece) == colour:
                total += VALUE.get(_kind(piece), 0) + CENTER[r][f]
    return total


def cat_move(state: Dict[str, Any], level: int = 2, rng: Any = None):
    """猫娘选着: 材料 + 中心加成, 2 层搜索(带时限); 能将军/吃子优先。"""
    import random
    import time
    rng = rng or random.Random()
    moves = legal_moves(state)
    if not moves:
        return None
    if level <= 1:
        caps = [m for m in moves if _board(state)[m[1][1]][m[1][0]]]
        return rng.choice(caps or moves)
    board = _board(state)
    deadline = time.monotonic() + 1.5

    def score(b: List[List[str]]) -> int:
        return _material(b, "b") - _material(b, "w")

    best, best_move = -10 ** 9, moves[0]
    for src, dst in moves:
        trial = [row[:] for row in board]
        flag = ""
        for s, d, fl in _pseudo(board, src[0], src[1], state.get("ep") or "", state.get("castling") or ""):
            if s == src and d == dst:
                flag = fl
                break
        _apply(trial, src, dst, flag)
        value = score(trial)
        if level >= 3 and time.monotonic() < deadline:
            foe_moves = []
            for rr in range(8):
                for ff in range(8):
                    if _colour(trial[rr][ff]) == "w":
                        foe_moves += _pseudo(trial, ff, rr, "", "")
            for _s2, d2, fl2 in foe_moves[:24]:
                t2 = [row[:] for row in trial]
                _apply(t2, _s2, d2, fl2)
                value = max(value, score(t2))
        if value > best:
            best, best_move = value, (src, dst)
    return best_move
