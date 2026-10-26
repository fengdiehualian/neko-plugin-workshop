"""五子棋规则 + 猫娘 AI(纯逻辑, 无 IO, 可直接单测)。

坐标约定(与棋盘图一致): 列 A..O 从左到右, 行 1..15 从上到下 ——
所以 "H8" = 第 8 列第 8 行。内部用 (r, c): r 是行号-1(0 在最上), c 是列号-1(0 在最左)。
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple

SIZE = 15
EMPTY, BLACK, WHITE = 0, 1, 2
COL_LETTERS = "ABCDEFGHIJKLMNO"

#: 棋盘上的星位(五子棋惯例的 5 个点)
STAR_POINTS = ((3, 3), (3, 11), (7, 7), (11, 3), (11, 11))

#: 棋型分值: 串里 1=我方 2=对方/墙 0=空
_PATTERNS: Tuple[Tuple[str, int], ...] = (
    ("11111", 500000),
    ("011110", 50000),
    ("01111", 9000), ("11110", 9000), ("11011", 9000),
    ("10111", 9000), ("11101", 9000),
    ("01110", 3000),
    ("010110", 1500), ("011010", 1500),
    ("00110", 400), ("01100", 400), ("01010", 300),
    ("0010", 60), ("0100", 60),
)


def coord_to_rc(text: str, size: int = SIZE) -> Optional[Tuple[int, int]]:
    """把 "H8" / "h8" / "8H" / "落子 H8" 解析成 (r, c); 解析不了返回 None。"""
    # 只认 ASCII: 中文的 isalnum()/isalpha() 也是 True, 会把「落子 H8」解析坏
    raw = "".join(ch for ch in str(text or "").upper()
                  if ch.isascii() and ch.isalnum())
    letters = "".join(ch for ch in raw if ch.isalpha())
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not letters or not digits:
        return None
    col = letters[0]
    if col not in COL_LETTERS[:size]:
        return None
    try:
        row = int(digits)
    except ValueError:
        return None
    if not 1 <= row <= size:
        return None
    return (row - 1, COL_LETTERS.index(col))


def rc_to_coord(r: int, c: int) -> str:
    return f"{COL_LETTERS[c]}{r + 1}"


class Board:
    """一盘五子棋(自由规则, 无禁手)。"""

    def __init__(self, size: int = SIZE) -> None:
        self.size = size
        self.grid: List[List[int]] = [[EMPTY] * size for _ in range(size)]
        self.moves: List[Tuple[int, int, int]] = []      # (r, c, 谁下的)
        self.winner = EMPTY                              # EMPTY / BLACK / WHITE
        self.draw = False

    # ── 基本操作 ─────────────────────────────
    def legal(self, r: int, c: int) -> bool:
        return (0 <= r < self.size and 0 <= c < self.size
                and self.grid[r][c] == EMPTY and not self.winner and not self.draw)

    def place(self, r: int, c: int, who: int) -> bool:
        if not self.legal(r, c):
            return False
        self.grid[r][c] = who
        self.moves.append((r, c, who))
        if self._five(r, c, who):
            self.winner = who
        elif len(self.moves) >= self.size * self.size:
            self.draw = True
        return True

    def undo(self, count: int = 1) -> int:
        """悔 count 手(按落子顺序倒着撤), 返回实际撤掉的步数。"""
        done = 0
        for _ in range(max(0, int(count))):
            if not self.moves:
                break
            r, c, _who = self.moves.pop()
            self.grid[r][c] = EMPTY
            done += 1
        self.winner = EMPTY
        self.draw = False
        return done

    def last(self) -> Optional[Tuple[int, int, int]]:
        return self.moves[-1] if self.moves else None

    def is_full(self) -> bool:
        return all(cell != EMPTY for row in self.grid for cell in row)

    def count(self, who: int) -> int:
        return sum(1 for m in self.moves if m[2] == who)

    # ── 判胜 ────────────────────────────────
    def _five(self, r: int, c: int, who: int) -> bool:
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            run = 1
            for sign in (1, -1):
                rr, cc = r + dr * sign, c + dc * sign
                while (0 <= rr < self.size and 0 <= cc < self.size
                       and self.grid[rr][cc] == who):
                    run += 1
                    rr += dr * sign
                    cc += dc * sign
            if run >= 5:
                return True
        return False

    # ── 给 AI 用 ────────────────────────────
    def _line(self, r: int, c: int, dr: int, dc: int, who: int, radius: int = 4) -> str:
        """以 (r,c) 为中心取一串: 1=who, 2=对手/墙, 0=空(先把 (r,c) 当成 who)。"""
        chars = []
        for step in range(-radius, radius + 1):
            rr, cc = r + dr * step, c + dc * step
            if not (0 <= rr < self.size and 0 <= cc < self.size):
                chars.append("2")
            elif step == 0:
                chars.append("1")
            else:
                cell = self.grid[rr][cc]
                chars.append("0" if cell == EMPTY else ("1" if cell == who else "2"))
        return "".join(chars)

    def score_at(self, r: int, c: int, who: int) -> int:
        """假设 who 下在 (r,c) 后, 他自己的棋型总分(用来找自己的好点)。"""
        return sum(_line_score(self._line(r, c, dr, dc, who)) for dr, dc in
                   ((0, 1), (1, 0), (1, 1), (1, -1)))

    def candidates(self, radius: int = 2) -> List[Tuple[int, int]]:
        """已有棋子附近的空点(全空盘就返回天元)。"""
        if not self.moves:
            mid = self.size // 2
            return [(mid, mid)]
        out = set()
        for r, c, _who in self.moves:
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    rr, cc = r + dr, c + dc
                    if self.legal(rr, cc):
                        out.add((rr, cc))
        return sorted(out)


def _line_score(line: str) -> int:
    total = 0
    for pattern, score in _PATTERNS:
        start = 0
        while True:
            idx = line.find(pattern, start)
            if idx < 0:
                break
            total += score
            start = idx + 1
    return total


def winning_move(board: Board, who: int) -> Optional[Tuple[int, int]]:
    """who 下一手能立刻成五的点(没有则 None)。"""
    for r, c in board.candidates():
        if board._five(r, c, who) or board.score_at(r, c, who) >= 500000:
            return (r, c)
    return None


def cat_move(board: Board, level: int = 2, rng: Optional[random.Random] = None) -> Tuple[int, int]:
    """猫娘选点: 1=随手 2=正常(1 层攻防) 3=认真(带一步预判)。"""
    rng = rng or random.Random()
    size = board.size
    cands = board.candidates()
    if not cands:
        mid = size // 2
        return (mid, mid)

    me, foe = WHITE, BLACK
    win = winning_move(board, me)
    if win:
        return win                                  # 自己能成五 → 直接赢
    block = winning_move(board, foe)
    if block:
        return block                                # 对方要成五 → 必须堵

    scored: List[Tuple[float, Tuple[int, int]]] = []
    for r, c in cands:
        attack = board.score_at(r, c, me)
        defend = board.score_at(r, c, foe)
        value = attack + defend * 0.85
        if level >= 3:
            # 预判一步: 我下这里之后, 对方有没有立刻成五的点
            trial = Board(size)
            trial.grid = [row[:] for row in board.grid]
            trial.moves = list(board.moves)
            trial.place(r, c, me)
            if winning_move(trial, foe):
                value *= 0.25
        scored.append((value, (r, c)))
    scored.sort(key=lambda item: item[0], reverse=True)

    if level <= 1:                                  # 随手: 从靠前的一批里随机
        pool = [p for _v, p in scored[:8]] or [p for _v, p in scored]
        return rng.choice(pool)
    best = scored[0][0]
    if level == 2 and rng.random() < 0.12:          # 正常: 偶尔不选最优, 别太机械
        near = [p for v, p in scored if v >= best * 0.9][:4]
        return rng.choice(near or [scored[0][1]])
    return scored[0][1]


def board_block(board: Board, *, level_label: str = "") -> Dict[str, object]:
    """把棋局转成渲染桥的棋盘块数据(交叉点摆法 + A..O/1..15 坐标 + 最后一手)。"""
    glyph = {BLACK: "●", WHITE: "○"}
    rows: List[List[object]] = []
    for r in range(board.size):
        row: List[object] = []
        for c in range(board.size):
            cell = board.grid[r][c]
            if cell == EMPTY:
                row.append("")
            else:
                row.append({"g": glyph.get(cell, "●"),
                            "c": "black" if cell == BLACK else "white"})
        rows.append(row)
    marks: Dict[str, str] = {}
    for r, c in STAR_POINTS:
        if 0 <= r < board.size and 0 <= c < board.size and board.grid[r][c] == EMPTY:
            marks[f"{r},{c}"] = "star"
    last = board.last()
    if last:
        marks[f"{last[0]},{last[1]}"] = "last"
    return {
        "type": "board",
        "title": "棋盘" + (f"（{level_label}）" if level_label else ""),
        "rows": rows,
        "placement": "cross",
        "col_labels": list(COL_LETTERS[:board.size]),
        "row_labels": [str(i + 1) for i in range(board.size)],
        "marks": marks,
    }


# ── 着法评估 / 威胁感知(给猫娘"看盘说话"用) ──────────────

WIN_SCORE = 500000


def _foe(who: int) -> int:
    return WHITE if who == BLACK else BLACK


def _clone(board: Board) -> Board:
    new = Board(board.size)
    new.grid = [row[:] for row in board.grid]
    new.moves = list(board.moves)
    new.winner = board.winner
    new.draw = board.draw
    return new


def move_value(board: Board, r: int, c: int, who: int, foe: Optional[int] = None) -> int:
    """在**当前**局面下, 把 who 下到 (r,c) 的价值 = 自己成型 + 0.85×破坏对方。

    加上防守项很重要: 否则"没去堵对方的四"也会被算成好手。
    """
    foe = foe or _foe(who)
    return board.score_at(r, c, who) + int(board.score_at(r, c, foe) * 0.85)


def best_score(board: Board, who: int) -> Tuple[int, Optional[Tuple[int, int]]]:
    foe = _foe(who)
    best, at = 0, None
    for r, c in board.candidates():
        value = move_value(board, r, c, who, foe)
        if value > best:
            best, at = value, (r, c)
    return best, at


def analyse_move(board_before: Board, r: int, c: int, who: int) -> Dict[str, object]:
    """评价 who 刚落下的这一手(传**落子前**的局面, 内部会模拟一手)。

    quality: win(成五) / best(接近最佳) / ok(还行) / loose(有点松) / blunder(送)
    另外标出: 是否本来有成五点却错过了。
    """
    foe = _foe(who)
    missing_win = None
    win_now = winning_move(board_before, who)
    if win_now and (r, c) != win_now:
        missing_win = win_now
    best, best_at = best_score(board_before, who)
    trial = _clone(board_before)
    trial.place(r, c, who)
    if trial.winner == who:
        quality = "win"
    else:
        played = move_value(board_before, r, c, who, foe)
        ratio = (played / best) if best > 0 else 1.0
        if missing_win is not None:
            quality = "blunder"
        elif ratio >= 0.85:
            quality = "best"
        elif ratio >= 0.30:
            quality = "ok"
        elif ratio >= 0.08:
            quality = "loose"
        else:
            quality = "blunder"
    return {"quality": quality, "value": move_value(board_before, r, c, who, foe) if not trial.winner else WIN_SCORE,
            "best": best, "best_move": best_at, "missing_win": missing_win}


def threats(board: Board, who: int) -> Dict[str, int]:
    """整线扫描 who 的成型威胁(活四/冲四/活三/眠三)。给"她感觉到危险/得意"用。"""
    size = board.size
    counts = {"open4": 0, "four": 0, "open3": 0, "three": 0}
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        for r in range(size):
            for c in range(size):
                pr, pc = r - dr, c - dc          # 只从每条线的起点扫一次
                if 0 <= pr < size and 0 <= pc < size:
                    continue
                cells = []
                rr, cc = r, c
                while 0 <= rr < size and 0 <= cc < size:
                    cell = board.grid[rr][cc]
                    cells.append("1" if cell == who else ("2" if cell != EMPTY else "0"))
                    rr += dr
                    cc += dc
                line = "2" + "".join(cells) + "2"   # 两端补墙, 边缘棋型才认得出来
                if "1" not in line:
                    continue
                if "011110" in line:
                    counts["open4"] += 1
                elif any(p in line for p in ("01111", "11110", "11011", "10111", "11101")):
                    counts["four"] += 1
                elif "01110" in line:
                    counts["open3"] += 1
                elif any(p in line for p in ("0110", "01010", "21110", "01112")):
                    counts["three"] += 1        # 含眠三(一边被堵)
    return counts


def describe_threats(board: Board, who: int) -> str:
    """把威胁翻成人话(喂提示词/台词)。"""
    t = threats(board, who)
    if t["open4"]:
        return "已经有活四, 下一手就成五"
    if t["four"]:
        return "有四连威胁"
    if t["open3"]:
        return "有活三, 快连起来了"
    if t["three"]:
        return "有个三连苗头"
    return "暂时没有成型威胁"


#: 引擎里的 cat_move 先存一份别名 —— 下面协议层要用同名函数, 不能互相盖
_engine_cat_move = cat_move

# ── 协议适配层(boards/base.py): 只做数据搬运, 规则仍在本模块上面 ──

NAME = "五子棋"
BLACK_MARK, WHITE_MARK = 1, 2


def new_state(**cfg: Any) -> Dict[str, Any]:
    size = max(9, min(19, int(cfg.get("size") or SIZE)))
    return {"size": size, "moves": [], "turn": BLACK_MARK, "winner": 0,
            "draw": False, "level": int(cfg.get("level") or 2)}


def _board_of(state: Dict[str, Any]) -> Board:
    board = Board(int(state.get("size") or SIZE))
    for mv in state.get("moves") or []:
        try:
            board.place(int(mv[0]), int(mv[1]), int(mv[2]))
        except (TypeError, ValueError, IndexError):
            continue
    return board


def _write(state: Dict[str, Any], board: Board) -> None:
    state["moves"] = [list(m) for m in board.moves]
    state["winner"] = board.winner
    state["draw"] = board.draw
    state["turn"] = BLACK_MARK if len(board.moves) % 2 == 0 else WHITE_MARK


def parse_move(text: str, state: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    return coord_to_rc(text, int(state.get("size") or SIZE))


def legal_moves(state: Dict[str, Any]) -> List[Tuple[int, int]]:
    board = _board_of(state)
    return board.candidates() if not board.winner else []


def apply_move(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    board = _board_of(state)
    who = int(state.get("turn") or BLACK_MARK)
    before = _board_of(state)
    r, c = int(move[0]), int(move[1])
    if not board.place(r, c, who):
        return {"ok": False, "reason": "illegal"}
    coord_txt = rc_to_coord(r, c)
    event, facts = "cat_move", [{"kind": "cat_move", "coord": coord_txt, "value": coord_txt}]
    if who == BLACK_MARK:
        review = analyse_move(before, r, c, BLACK_MARK)
        quality = str(review.get("quality") or "ok")
        event = {"best": "player_best", "loose": "player_loose",
                 "blunder": "player_blunder", "win": "player_move"}.get(quality, "player_move")
        facts = [{"kind": event, "coord": coord_txt, "value": coord_txt, "quality": quality}]
    _write(state, board)
    over = ""
    if board.winner == BLACK_MARK:
        over = "red"
    elif board.winner == WHITE_MARK:
        over = "black"
    elif board.draw:
        over = "draw"
    if over:
        state["turn"] = 0
        return {"ok": True, "facts": facts, "over": over, "event": event,
                "coord": coord_txt}
    if who == WHITE_MARK and winning_move(before, BLACK_MARK):
        facts.append({"kind": "cat_block", "coord": coord_txt, "value": coord_txt})
        event = "cat_block"
    return {"ok": True, "facts": facts, "event": event, "coord": coord_txt}


def review(state: Dict[str, Any], move: Any) -> Dict[str, Any]:
    return analyse_move(_board_of(state), int(move[0]), int(move[1]), BLACK_MARK)


def situation(state: Dict[str, Any]) -> str:
    board = _board_of(state)
    return (f"黑 {board.count(BLACK_MARK)} 手 · 白 {board.count(WHITE_MARK)} 手"
            f"；黑{describe_threats(board, BLACK_MARK)}，白{describe_threats(board, WHITE_MARK)}")


def text_board(state: Dict[str, Any]) -> str:
    board = _board_of(state)
    rows = ["   " + " ".join(list(COL_LETTERS[:board.size]))]
    for r in range(board.size):
        rows.append(f"{r + 1:>2} " + " ".join(
            "." if board.grid[r][c] == EMPTY else
            ("X" if board.grid[r][c] == BLACK_MARK else "O") for c in range(board.size)))
    return "\n".join(rows)


def pending(state: Dict[str, Any]) -> bool:
    return not state.get("winner") and not state.get("draw") \
        and state.get("turn") == BLACK_MARK


def cat_move(state: Dict[str, Any], level: int = 2, rng: Any = None) -> Optional[Tuple[int, int]]:
    return _engine_cat_move(_board_of(state), level, rng)


#: 引擎的 board_block 收 Board 对象; 协议层要收 state 字典 —— 同名会互相盖,
#: 先存别名, 再定义协议版(出图时踩过 'dict' object has no attribute 'size')
_engine_board_block = board_block


def board_block(state: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    return _engine_board_block(_board_of(state), **kw)
