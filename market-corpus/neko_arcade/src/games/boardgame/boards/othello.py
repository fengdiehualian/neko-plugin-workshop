"""黑白棋(Reversi) —— 按 games/boardgame/boards/base.py 协议写的子玩法。

只写规则: 合法性/翻子/终局/棋盘数据/局面描述。台词、出图、心情、推送全归本体。
坐标列 a..h, 行 1..8(1 在下)。棋盘用 8 行字符串存("." 空 / "b" 黑 / "w" 白), 便于存档。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

NAME = "黑白棋"
FILES = "abcdefgh"
SIZE = 8
DIRS = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))

#: 位置权重: 角最香, 角旁边(C/X 位)最亏, 边其次
WEIGHTS = (
    (100, -50, 10, 5, 5, 10, -50, 100),
    (-50, -60, -5, -5, -5, -5, -60, -50),
    (10, -5, 3, 2, 2, 3, -5, 10),
    (5, -5, 2, 1, 1, 2, -5, 5),
    (5, -5, 2, 1, 1, 2, -5, 5),
    (10, -5, 3, 2, 2, 3, -5, 10),
    (-50, -60, -5, -5, -5, -5, -60, -50),
    (100, -50, 10, 5, 5, 10, -50, 100),
)


def _grid(state: Dict[str, Any]) -> List[List[str]]:
    rows = state.get("board") or []
    return [list(r) for r in rows] if rows else [
        list("........") for _ in range(SIZE)]


def _dump(grid: List[List[str]]) -> List[str]:
    return ["".join(r) for r in grid]


def _other(who: str) -> str:
    return "w" if who == "b" else "b"


def _flips(grid: List[List[str]], f: int, r: int, who: str) -> List[Tuple[int, int]]:
    """在 (f,r) 落 who 能翻掉的所有子(合法性就看这个是否非空)。"""
    if not (0 <= f < SIZE and 0 <= r < SIZE) or grid[r][f] != ".":
        return []
    out: List[Tuple[int, int]] = []
    foe = _other(who)
    for df, dr in DIRS:
        line = []
        nf, nr = f + df, r + dr
        while 0 <= nf < SIZE and 0 <= nr < SIZE and grid[nr][nf] == foe:
            line.append((nf, nr))
            nf += df
            nr += dr
        if line and 0 <= nf < SIZE and 0 <= nr < SIZE and grid[nr][nf] == who:
            out.extend(line)
    return out


def new_state(**cfg: Any) -> Dict[str, Any]:
    grid = [list("........") for _ in range(SIZE)]
    grid[3][3] = "w"
    grid[3][4] = "b"          # d4=w e4=b
    grid[4][3] = "b"
    grid[4][4] = "w"          # d5=b e5=w
    return {"board": _dump(grid), "turn": "b", "level": int(cfg.get("level") or 2),
            "passes": 0, "over": "", "moves": []}


def parse_move(text: str, state: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    raw = "".join(ch for ch in str(text or "").lower() if ch.isascii() and ch.isalnum())
    if len(raw) < 2 or raw[0] not in FILES:
        return None
    digits = "".join(ch for ch in raw[1:] if ch.isdigit())
    if not digits:
        return None
    rank = int(digits)
    if not 1 <= rank <= SIZE:
        return None
    return (FILES.index(raw[0]), rank - 1)


def coord(f: int, r: int) -> str:
    return f"{FILES[f]}{r + 1}"


def legal_moves(state: Dict[str, Any]) -> List[Tuple[int, int]]:
    grid = _grid(state)
    who = state.get("turn") or "b"
    return [(f, r) for r in range(SIZE) for f in range(SIZE)
            if _flips(grid, f, r, who)]


def _count(grid: List[List[str]]) -> Dict[str, int]:
    flat = "".join("".join(r) for r in grid)
    return {"b": flat.count("b"), "w": flat.count("w")}


def apply_move(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    grid = _grid(state)
    who = state.get("turn") or "b"
    f, r = move
    flips = _flips(grid, f, r, who)
    if not flips:
        return {"ok": False, "reason": "illegal"}
    grid[r][f] = who
    for ff, rr in flips:
        grid[rr][ff] = who
    counts = _count(grid)
    facts: List[Dict[str, Any]] = [{"kind": "flip", "count": len(flips),
                                    "value": f"{who} 翻 {len(flips)} 子"}]
    nxt = _other(who)
    passes = 0
    if not any(_flips(grid, ff, rr, nxt) for rr in range(SIZE) for ff in range(SIZE)):
        nxt = who                                # 对方无处可下 → 跳过
        passes = 1
        facts.append({"kind": "pass", "value": "对方无子可落"})
        if not any(_flips(grid, ff, rr, nxt) for rr in range(SIZE) for ff in range(SIZE)):
            over = "draw" if counts["b"] == counts["w"] else (
                "black" if counts["b"] > counts["w"] else "red")
            state.update({"board": _dump(grid), "turn": nxt, "over": over})
            return {"ok": True, "facts": facts, "event": "win" if over == "red" else "lose",
                    "over": over}
    state.update({"board": _dump(grid), "turn": nxt, "passes": passes,
                  "moves": list(state.get("moves") or []) + [[f, r]]})
    return {"ok": True, "facts": facts, "event": "flip", "counts": counts,
            "coord": coord(f, r)}


def review(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    """着法质量: 占角=好, 送角(X/C 位)=亏。"""
    left = state.get("board") or []
    before = [list(x) for x in left] if left else []
    f, r = move
    grid = before if before else [list("........") for _ in range(SIZE)]
    gain = len(_flips(grid, f, r, "b"))
    weight = WEIGHTS[r][f]
    if weight >= 100:
        quality = "best"
    elif weight <= -50:
        quality = "blunder"
    elif weight < 0:
        quality = "loose"
    else:
        quality = "ok"
    return {"quality": quality, "flip": gain, "weight": weight}


def board_block(state: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    grid = _grid(state)
    rows: List[List[object]] = []
    for r in range(SIZE - 1, -1, -1):            # 行序翻转, 1 行在下
        row: List[object] = []
        for f in range(SIZE):
            cell = grid[r][f]
            row.append("" if cell == "." else
                       {"g": "●" if cell == "b" else "○",
                        "c": "black" if cell == "b" else "white"})
        rows.append(row)
    marks = {}
    last = (state.get("moves") or [None])[-1]
    if last:
        marks[f"{SIZE - 1 - int(last[1])},{int(last[0])}"] = "last"
    counts = _count(grid)
    return {"type": "board", "title": kw.get("title") or f"黑白棋（你 {counts['b']} : {counts['w']} 她）",
            "rows": rows, "placement": "cell",
            "col_labels": list(FILES),
            "row_labels": [str(i) for i in range(SIZE, 0, -1)],
            "marks": marks}


def situation(state: Dict[str, Any]) -> str:
    counts = _count(_grid(state))
    diff = counts["b"] - counts["w"]
    who = "你" if state.get("turn") == "b" else "猫娘"
    lead = "你领先" if diff > 0 else ("猫娘领先" if diff < 0 else "持平")
    return f"你(黑) {counts['b']} 子 · 猫娘(白) {counts['w']} 子，{lead}，轮到{who}"


def text_board(state: Dict[str, Any]) -> str:
    grid = _grid(state)
    rows = [f"   {' '.join(FILES)}"]
    for r in range(SIZE - 1, -1, -1):
        rows.append(f"{r + 1:>2} " + " ".join(
            "·" if grid[r][f] == "." else ("●" if grid[r][f] == "b" else "○")
            for f in range(SIZE)))
    return "\n".join(rows)


def pending(state: Dict[str, Any]) -> bool:
    return not state.get("over") and state.get("turn") == "b"


def cat_move(state: Dict[str, Any], level: int = 2, rng: Any = None) -> Optional[Tuple[int, int]]:
    """猫娘选点: 位置权重 + 机动性; 3 档加一步预判(避免送角)。"""
    import random
    rng = rng or random.Random()
    grid = _grid(state)
    cands = legal_moves({"board": _dump(grid), "turn": "w"})
    if not cands:
        return None
    scored: List[Tuple[float, Tuple[int, int]]] = []
    for f, r in cands:
        trial = [row[:] for row in grid]
        flips = _flips(grid, f, r, "w")
        trial[r][f] = "w"
        for ff, rr in flips:
            trial[rr][ff] = "w"
        score = float(WEIGHTS[r][f]) + len(flips) * 1.5
        if level >= 3:                            # 预判: 别把角送给她
            after = {"board": _dump(trial), "turn": "b"}
            for bf, br in legal_moves(after):
                score -= max(0, WEIGHTS[br][bf]) * 0.6
        scored.append((score, (f, r)))
    scored.sort(key=lambda x: x[0], reverse=True)
    if level <= 1:
        return rng.choice([c for _s, c in scored[:4]])
    if level == 2 and rng.random() < 0.12 and len(scored) > 1:
        return rng.choice([c for _s, c in scored[:2]])
    return scored[0][1]
