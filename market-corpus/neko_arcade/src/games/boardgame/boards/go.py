"""围棋 —— 按 boards/base.py 协议写的子玩法(默认 9 路, 中国规则数子)。

只写规则: 落子/提子/禁着点(劫 + 自杀)/停一手/终局数子 + 棋盘数据。
台词、出图、心情、推送全归本体。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

NAME = "围棋"
FILES = "abcdefghi"
SIZE = 9
DIRS = ((0, 1), (0, -1), (1, 0), (-1, 0))
PASS_WORDS = ("过", "停一手", "pass", "虚手", "跳过")


def _grid(state: Dict[str, Any]) -> List[List[str]]:
    rows = state.get("board") or []
    size = int(state.get("size") or SIZE)
    return [list(r) for r in rows] if rows else [list("." * size) for _ in range(size)]


def _dump(grid: List[List[str]]) -> List[str]:
    return ["".join(r) for r in grid]


def new_state(**cfg: Any) -> Dict[str, Any]:
    size = int(cfg.get("size") or SIZE)
    size = max(5, min(19, size))
    return {"board": _dump([list("." * size) for _ in range(size)]), "size": size,
            "turn": "b", "ko": "", "passes": 0, "caps": {"b": 0, "w": 0},
            "over": "", "level": int(cfg.get("level") or 2), "moves": []}


def coord(f: int, r: int) -> str:
    return f"{FILES[f]}{r + 1}"


def parse_move(text: str, state: Dict[str, Any]) -> Optional[Any]:
    raw = str(text or "").strip().lower()
    if any(w in raw for w in PASS_WORDS):
        return "pass"
    clean = "".join(ch for ch in raw if ch.isascii() and ch.isalnum())
    if len(clean) < 2 or clean[0] not in FILES:
        return None
    digits = "".join(ch for ch in clean[1:] if ch.isdigit())
    size = int(state.get("size") or SIZE)
    if not digits:
        return None
    rank = int(digits)
    if not 1 <= rank <= size:
        return None
    f = FILES.index(clean[0])
    if f >= size:
        return None
    return (f, rank - 1)


def _neigh(f: int, r: int, size: int):
    for df, dr in DIRS:
        nf, nr = f + df, r + dr
        if 0 <= nf < size and 0 <= nr < size:
            yield nf, nr


def _group(grid: List[List[str]], f: int, r: int, size: int) -> Tuple[set, set]:
    """返回 (同色连块, 它的气)。"""
    colour = grid[r][f]
    stones, seen, libs = {(f, r)}, {(f, r)}, set()
    stack = [(f, r)]
    while stack:
        cf, cr = stack.pop()
        for nf, nr in _neigh(cf, cr, size):
            cell = grid[nr][nf]
            if cell == ".":
                libs.add((nf, nr))
            elif cell == colour and (nf, nr) not in seen:
                seen.add((nf, nr))
                stones.add((nf, nr))
                stack.append((nf, nr))
    return stones, libs


def _play(grid: List[List[str]], f: int, r: int, who: str, size: int) -> Optional[Dict[str, Any]]:
    """落子并提子; 返回 {captured, ko} 或 None(非法)。"""
    if grid[r][f] != ".":
        return None
    foe = "w" if who == "b" else "b"
    grid[r][f] = who
    captured: List[Tuple[int, int]] = []
    for nf, nr in _neigh(f, r, size):
        if grid[nr][nf] == foe:
            stones, libs = _group(grid, nf, nr, size)
            if not libs:
                for sf, sr in stones:
                    grid[sr][sf] = "."
                captured.extend(stones)
    mine, my_libs = _group(grid, f, r, size)
    if not my_libs:
        grid[r][f] = "."                       # 自杀 → 撤销
        for sf, sr in captured:
            pass
        return None
    ko = ""
    if len(captured) == 1 and len(mine) == 1 and len(my_libs) == 1:
        ko = coord(*captured[0])               # 单子提单子 → 劫
    return {"captured": captured, "ko": ko}


def legal_moves(state: Dict[str, Any]) -> List[Any]:
    grid = _grid(state)
    size = int(state.get("size") or SIZE)
    who = state.get("turn") or "b"
    out: List[Any] = []
    for r in range(size):
        for f in range(size):
            if grid[r][f] != "." or coord(f, r) == (state.get("ko") or ""):
                continue
            trial = [row[:] for row in grid]
            if _play(trial, f, r, who, size):
                out.append((f, r))
    out.append("pass")                          # 随时可以停一手
    return out


def apply_move(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    grid = _grid(state)
    size = int(state.get("size") or SIZE)
    who = state.get("turn") or "b"
    if move == "pass":
        state["passes"] = int(state.get("passes") or 0) + 1
        state["turn"] = "w" if who == "b" else "b"
        state["ko"] = ""
        state["moves"] = list(state.get("moves") or []) + [["pass"]]
        facts = [{"kind": "pass", "value": f"{who} 停一手"}]
        over = ""
        if state["passes"] >= 2:
            black, white = _score(grid, size)
            over = "draw" if black == white else ("red" if black > white else "black")
            state.update({"over": over, "score": [black, white]})
            facts.append({"kind": "score", "value": f"黑 {black} 目 · 白 {white} 目"})
        return {"ok": True, "facts": facts, "over": over, "event": "pass"}
    f, r = int(move[0]), int(move[1])
    if coord(f, r) == (state.get("ko") or ""):
        return {"ok": False, "reason": "ko"}
    result = _play(grid, f, r, who, size)
    if result is None:
        return {"ok": False, "reason": "illegal"}
    caps = state.setdefault("caps", {"b": 0, "w": 0})
    caps[who] = int(caps.get(who) or 0) + len(result["captured"])
    facts: List[Dict[str, Any]] = [{"kind": "move", "coord": coord(f, r), "value": coord(f, r)}]
    event = "player_move" if who == "b" else "cat_move"
    if result["captured"]:
        event = "player_capture" if who == "b" else "cat_capture"
        facts.append({"kind": event, "count": len(result["captured"]),
                      "value": f"提 {len(result['captured'])} 子"})
    state.update({"board": _dump(grid), "ko": result["ko"], "passes": 0,
                  "turn": "w" if who == "b" else "b",
                  "moves": list(state.get("moves") or []) + [[f, r]]})
    return {"ok": True, "facts": facts, "event": event, "coord": coord(f, r)}


def _score(grid: List[List[str]], size: int) -> Tuple[int, int]:
    """中国规则数子: 己方活子 + 只被自己包围的空点。"""
    black = sum(1 for r in range(size) for f in range(size) if grid[r][f] == "b")
    white = sum(1 for r in range(size) for f in range(size) if grid[r][f] == "w")
    seen = set()
    for r in range(size):
        for f in range(size):
            if grid[r][f] != "." or (f, r) in seen:
                continue
            region, borders, stack = {(f, r)}, set(), [(f, r)]
            seen.add((f, r))
            while stack:
                cf, cr = stack.pop()
                for nf, nr in _neigh(cf, cr, size):
                    cell = grid[nr][nf]
                    if cell == ".":
                        if (nf, nr) not in region:
                            region.add((nf, nr))
                            seen.add((nf, nr))
                            stack.append((nf, nr))
                    else:
                        borders.add(cell)
            if borders == {"b"}:
                black += len(region)
            elif borders == {"w"}:
                white += len(region)
    return black, white


def board_block(state: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    grid = _grid(state)
    size = int(state.get("size") or SIZE)
    rows: List[List[object]] = []
    for r in range(size - 1, -1, -1):
        rows.append(["" if grid[r][f] == "." else
                     {"g": "●" if grid[r][f] == "b" else "○",
                      "c": "black" if grid[r][f] == "b" else "white"}
                     for f in range(size)])
    marks = {}
    last = (state.get("moves") or [None])[-1]
    if isinstance(last, list) and len(last) == 2:
        marks[f"{size - 1 - int(last[1])},{int(last[0])}"] = "last"
    caps = state.get("caps") or {}
    return {"type": "board",
            "title": kw.get("title") or f"围棋 {size}路（提子 你 {caps.get('b', 0)} : {caps.get('w', 0)} 她）",
            "rows": rows, "placement": "cross", "col_labels": list(FILES[:size]),
            "row_labels": [str(i) for i in range(size, 0, -1)], "marks": marks}


def situation(state: Dict[str, Any]) -> str:
    grid = _grid(state)
    size = int(state.get("size") or SIZE)
    caps = state.get("caps") or {}
    black = sum(1 for r in range(size) for f in range(size) if grid[r][f] == "b")
    white = sum(1 for r in range(size) for f in range(size) if grid[r][f] == "w")
    who = "你" if state.get("turn") == "b" else "猫娘"
    tail = ""
    if state.get("over"):
        sc = state.get("score") or [0, 0]
        tail = f"，数子 黑 {sc[0]} : 白 {sc[1]}"
    return (f"你(黑) {black} 子 提{caps.get('b', 0)} · 猫娘(白) {white} 子 提{caps.get('w', 0)}"
            f"，轮到{who}{tail}")


def text_board(state: Dict[str, Any]) -> str:
    grid = _grid(state)
    size = int(state.get("size") or SIZE)
    rows = ["   " + " ".join(list(FILES[:size]))]
    for r in range(size - 1, -1, -1):
        rows.append(f"{r + 1:>2} " + " ".join(
            "." if grid[r][f] == "." else ("X" if grid[r][f] == "b" else "O")
            for f in range(size)))
    return "\n".join(rows)


def pending(state: Dict[str, Any]) -> bool:
    return not state.get("over") and state.get("turn") == "b"


def cat_move(state: Dict[str, Any], level: int = 2, rng: Any = None) -> Any:
    """猫娘选点: 先看能否提子; 否则挑"贴着已有子且有气"的点; 实在没有就停一手。"""
    import random
    rng = rng or random.Random()
    grid = _grid(state)
    size = int(state.get("size") or SIZE)
    cands = [m for m in legal_moves(state) if m != "pass"]
    if not cands:
        return "pass"
    scored = []
    for f, r in cands:
        trial = [row[:] for row in grid]
        res = _play(trial, f, r, "w", size)
        caps = len(res["captured"]) if res else 0
        near = sum(1 for nf, nr in _neigh(f, r, size) if grid[nr][nf] != ".")
        _, libs = _group(trial, f, r, size)
        score = caps * 12 + near * 2 + min(len(libs), 4)
        scored.append((score, (f, r)))
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][0]
    if best <= 4 and int(state.get("moves") and len(state["moves"]) or 0) > 20:
        return "pass"                            # 局面平淡且已下很多 → 收手
    if level <= 1:
        return rng.choice([m for _s, m in scored[:5]])
    if level == 2 and rng.random() < 0.15 and len(scored) > 1:
        return rng.choice([m for _s, m in scored[:3]])
    return scored[0][1]
