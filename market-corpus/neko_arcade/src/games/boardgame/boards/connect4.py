"""四子棋(Connect Four) —— 按 boards/base.py 协议写的子玩法。

落子方式与棋类不同: 主人报一列(如 "d" 或 "4"), 子从顶上落到底。
棋盘 7 列 × 6 行, 列 a..g, 行 1 在最下。存档用 6 行字符串("." 空 / "r" 我 / "y" 猫娘)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

NAME = "四子棋"
FILES = "abcdefg"
COLS, ROWS, NEED = 7, 6, 4

WEIGHTS = (3, 4, 5, 7, 5, 4, 3)      # 中间列更值钱


def _grid(state: Dict[str, Any]) -> List[List[str]]:
    rows = state.get("board") or []
    return [list(r) for r in rows] if rows else [list("." * COLS) for _ in range(ROWS)]


def _dump(grid: List[List[str]]) -> List[str]:
    return ["".join(r) for r in grid]


def new_state(**cfg: Any) -> Dict[str, Any]:
    return {"board": _dump([list("." * COLS) for _ in range(ROWS)]),
            "turn": "r", "level": int(cfg.get("level") or 2), "over": "", "moves": []}


def parse_move(text: str, state: Dict[str, Any]) -> Optional[int]:
    """报列: "d" / "D" / "4" / "d 3"(取第一个有效列) → 列号 0..6。"""
    raw = "".join(ch for ch in str(text or "").lower() if ch.isascii() and ch.isalnum())
    for ch in raw:
        if ch in FILES:
            return FILES.index(ch)
        if ch.isdigit() and 1 <= int(ch) <= COLS:
            return int(ch) - 1
    return None


def coord(col: int) -> str:
    return FILES[col]


def _drop_row(grid: List[List[str]], col: int) -> int:
    for r in range(ROWS):                      # r=0 是最下行
        if grid[r][col] == ".":
            return r
    return -1


def legal_moves(state: Dict[str, Any]) -> List[int]:
    grid = _grid(state)
    return [c for c in range(COLS) if grid[ROWS - 1][c] == "."]


def _wins(grid: List[List[str]], col: int, row: int, who: str) -> bool:
    for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
        run = 1
        for sign in (1, -1):
            c, r = col + dc * sign, row + dr * sign
            while 0 <= c < COLS and 0 <= r < ROWS and grid[r][c] == who:
                run += 1
                c += dc * sign
                r += dr * sign
        if run >= NEED:
            return True
    return False


def apply_move(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    grid = _grid(state)
    who = state.get("turn") or "r"
    col = int(move)
    if not 0 <= col < COLS:
        return {"ok": False, "reason": "illegal"}
    row = _drop_row(grid, col)
    if row < 0:
        return {"ok": False, "reason": "illegal"}
    grid[row][col] = who
    state["moves"] = list(state.get("moves") or []) + [[col, row]]
    facts: List[Dict[str, Any]] = [{"kind": "drop", "coord": f"{FILES[col]}{row + 1}",
                                    "value": f"{FILES[col]} 列"}]
    if _wins(grid, col, row, who):
        over = "red" if who == "r" else "black"
        state.update({"board": _dump(grid), "over": over})
        return {"ok": True, "facts": facts, "over": over,
                "event": "win" if who == "r" else "lose"}
    if all(grid[ROWS - 1][c] != "." for c in range(COLS)):
        state.update({"board": _dump(grid), "over": "draw"})
        return {"ok": True, "facts": facts, "over": "draw", "event": "draw"}
    state.update({"board": _dump(grid), "turn": "y" if who == "r" else "r"})
    return {"ok": True, "facts": facts, "event": "drop", "coord": f"{FILES[col]}{row + 1}"}


def review(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    grid = _grid(state)
    col = int(move)
    row = _drop_row(grid, col)
    threat = 0
    if row >= 0:
        grid[row][col] = "r"
        threat = 1 if _wins(grid, col, row, "r") else 0
    return {"quality": "best" if threat else ("ok" if WEIGHTS[col] >= 5 else "loose"),
            "weight": WEIGHTS[col]}


def board_block(state: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    grid = _grid(state)
    rows: List[List[object]] = []
    for r in range(ROWS - 1, -1, -1):
        rows.append(["" if grid[r][c] == "." else
                     {"g": "●" if grid[r][c] == "r" else "○",
                      "c": "red" if grid[r][c] == "r" else "white"} for c in range(COLS)])
    marks = {}
    last = (state.get("moves") or [None])[-1]
    if last:
        marks[f"{ROWS - 1 - int(last[1])},{int(last[0])}"] = "last"
    return {"type": "board", "title": kw.get("title") or "四子棋（你先手 ●）",
            "rows": rows, "placement": "cell",
            "col_labels": list(FILES), "row_labels": [str(i) for i in range(ROWS, 0, -1)],
            "marks": marks}


def situation(state: Dict[str, Any]) -> str:
    grid = _grid(state)
    flat = "".join("".join(r) for r in grid)
    who = "你" if state.get("turn") == "r" else "猫娘"
    return f"你(●) 落了 {flat.count('r')} 子 · 猫娘(○) {flat.count('y')} 子，轮到{who}"


def text_board(state: Dict[str, Any]) -> str:
    grid = _grid(state)
    rows = ["   " + " ".join(FILES)]
    for r in range(ROWS - 1, -1, -1):
        rows.append(f"{r + 1:>2} " + " ".join(
            "·" if grid[r][c] == "." else ("●" if grid[r][c] == "r" else "○")
            for c in range(COLS)))
    return "\n".join(rows)


def pending(state: Dict[str, Any]) -> bool:
    return not state.get("over") and state.get("turn") == "r"


def cat_move(state: Dict[str, Any], level: int = 2, rng: Any = None) -> Optional[int]:
    """猫娘选列: 能赢就赢 → 堵你 → 否则按列权重+成三潜力挑。"""
    import random
    rng = rng or random.Random()
    grid = _grid(state)
    cands = [c for c in range(COLS) if grid[ROWS - 1][c] == "."]
    if not cands:
        return None

    def wins_if(col: int, who: str) -> bool:
        row = _drop_row(grid, col)
        if row < 0:
            return False
        grid[row][col] = who
        hit = _wins(grid, col, row, who)
        grid[row][col] = "."
        return hit

    for c in cands:
        if wins_if(c, "y"):
            return c
    for c in cands:
        if wins_if(c, "r"):
            return c
    scored = []
    for c in cands:
        row = _drop_row(grid, c)
        grid[row][c] = "y"
        score = WEIGHTS[c] * 2.0
        for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
            run = 1
            for sign in (1, -1):
                cc, rr = c + dc * sign, row + dr * sign
                while 0 <= cc < COLS and 0 <= rr < ROWS and grid[rr][cc] == "y":
                    run += 1
                    cc += dc * sign
                    rr += dr * sign
            score += run * run
        grid[row][c] = "."
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    if level <= 1:
        return rng.choice([c for _s, c in scored[:3]])
    return scored[0][1]
