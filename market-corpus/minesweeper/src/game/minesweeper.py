import json
import math
import os
import random
import socket
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox

try:
    import minesweeper_plugin
except Exception:
    minesweeper_plugin = None


def neko_notify(event):
    if minesweeper_plugin is None:
        return
    try:
        minesweeper_plugin.notify(event)
    except Exception:
        pass


BG = "#c0c0c0"
LIGHT = "#ffffff"
DARK = "#808080"
LED_OFF = "#400000"
LED_ON = "#ff0000"

NUMBER_COLORS = {
    1: "#0000ff",
    2: "#008000",
    3: "#ff0000",
    4: "#000080",
    5: "#800000",
    6: "#008080",
    7: "#000000",
    8: "#808080",
}

ARROW_SHAPE = [
    (0, 0), (0, 14), (3.5, 10.5), (6.5, 16.5), (8.5, 15.6), (5.5, 9.8), (10, 9.8),
]

AI_MOVE_MIN_MS = 550
AI_MOVE_MAX_MS = 1000
AI_RETURN_MS = 750
AI_FRAME_MS = 16
AI_THINK_MS = 350
AI_AIM_MS = 150
AI_PRESS_MS = 500
AI_SETTLE_MS = 250
AI_DOUBT_BASE = 0.25
AI_DOUBT_MAX = 0.60
AI_DOUBT_BADFLAG = 0.10
AI_WONDER_MS = 900
AI_QUESTION_MS = 600

# ---------- N.E.K.O. 桥接阈值（按需调整） ----------
NEKO_PORT = 39001               # 与 minesweeper_plugin.py / plugin.toml 保持一致
NEKO_OPEN_MIN = 2               # 一次翻开 >= 此格数就写进记事
NEKO_RISKY_NUMBER = 3           # 翻开数字 >= 此值算“高风险区域”
NEKO_STREAK_MIN = 2             # 连续安全格 >= 此值才写进记事
NEKO_ENDGAME_SAFE_LEFT = 3      # 剩余未翻开安全格 <= 此值时提示残局
NEKO_IDLE_SECONDS = 45          # 玩家无操作多少秒后提醒

# 窗口位置记录文件（与本文件同级，四个窗口各自一格）
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "window.json")

SEGMENTS = {
    "0": set("abcdef"),
    "1": set("bc"),
    "2": set("abged"),
    "3": set("abgcd"),
    "4": set("fgbc"),
    "5": set("afgcd"),
    "6": set("afgecd"),
    "7": set("abc"),
    "8": set("abcdefg"),
    "9": set("abfgcd"),
    "-": set("g"),
    " ": set(),
}


class Minesweeper:
    BORDER = 4
    MARGIN = 6
    PANEL_H = 34
    CELL = 24
    LED_W = 13
    LED_H = 23
    LED_GAP = 2

    def __init__(self, root, rows=9, cols=9, mines=10):
        self.root = root
        self.rows = rows
        self.cols = cols
        self.mines = mines

        self.mine_positions = set()
        self.revealed = set()
        self.flags = set()
        self.started = False
        self.game_over = False
        self.seconds = 0
        self.smiley = "smile"
        self._pressing = False
        self._timer_id = None
        self._running = False

        self.turn = "human"
        self.ai_pressed = False
        self.ai_flagging = False
        self.ai_question = False
        self.ai_flagged = set()
        self._last_doubt_cell = None
        self._doubted_last_turn = False
        self._ai_last_reason = "guess"
        self._neko_safe_streak = 0
        self._neko_endgame_sent = False
        self._neko_idle_ticks = 0
        self._neko_opening = True
        self._ai_after = None
        self._wonder_after = None
        self._last_cancel = None
        self._cancel_log = deque(maxlen=8)
        self._dev_window = None
        self._dev_vars = {}

        self._scores = []
        self._rank_window = None
        self._rank_vars = {}
        self._rank_cols = []
        self._rank_rows = None
        self._help_window = None
        self._help_status_var = None
        self._help_after = None
        self._game_recorded = False
        self._settings = self._load_settings()

        board_w = cols * self.CELL
        board_h = rows * self.CELL
        self.cw = board_w + 2 * self.MARGIN
        self.ch = self.PANEL_H + self.MARGIN + board_h + 2 * self.MARGIN
        w = self.cw + 2 * self.BORDER + 4
        h = self.ch + 2 * self.BORDER + 4

        self.root.title("扫雷")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(
            root, width=w, height=h, bg=BG, highlightthickness=0
        )
        self.canvas.pack()

        self._build_menu()

        self.cx1 = self.BORDER + 2
        self.cy1 = self.BORDER + 2
        self.cx2 = self.cx1 + self.cw
        self.cy2 = self.cy1 + self.ch

        self.px1 = self.cx1 + self.MARGIN
        self.py1 = self.cy1 + self.MARGIN
        self.px2 = self.px1 + board_w
        self.py2 = self.py1 + self.PANEL_H

        self.bx = self.px1
        self.by = self.py2 + self.MARGIN

        smiley_size = 26
        scx = (self.px1 + self.px2) / 2
        scy = (self.py1 + self.py2) / 2
        self.smiley_box = (
            scx - smiley_size / 2,
            scy - smiley_size / 2,
            scx + smiley_size / 2,
            scy + smiley_size / 2,
        )
        self.smiley_center = (scx, scy)

        self.ai_rest = (w - 12, h - 18)
        self.ai_pos = list(self.ai_rest)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._right_click)

        self.reset()

        self._restore_geometry(self.root, "main")
        self.root.protocol(
            "WM_DELETE_WINDOW", lambda: self._close_window(self.root, "main")
        )

    # ---------- window position persistence ----------

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_settings(self):
        try:
            tmp = SETTINGS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._settings, f)
            os.replace(tmp, SETTINGS_PATH)
        except Exception:
            pass

    def _restore_geometry(self, window, key):
        data = self._settings.get(key)
        if not isinstance(data, dict):
            return
        x, y = data.get("x"), data.get("y")
        if isinstance(x, int) and isinstance(y, int) and abs(x) < 100000 and abs(y) < 100000:
            try:
                window.geometry("+%d+%d" % (x, y))
            except Exception:
                pass

    def _save_geometry(self, window, key):
        try:
            if window.winfo_exists() and window.state() == "normal":
                self._settings[key] = {
                    "x": int(window.winfo_x()),
                    "y": int(window.winfo_y()),
                }
                self._write_settings()
        except Exception:
            pass

    def _close_window(self, window, key):
        self._save_geometry(window, key)
        if window is self.root:
            # 主窗口关闭时，把仍开着的子窗口也存一遍
            for child_key, attr in (
                ("rank", "_rank_window"),
                ("help", "_help_window"),
                ("dev", "_dev_window"),
            ):
                child = getattr(self, attr, None)
                if child is not None:
                    self._save_geometry(child, child_key)
        try:
            window.destroy()
        except Exception:
            pass

    # ---------- drawing primitives ----------

    def _bevel(self, x1, y1, x2, y2, raised=True, t=2, fill=BG, tag="base"):
        c = self.canvas
        tl = LIGHT if raised else DARK
        br = DARK if raised else LIGHT
        c.create_rectangle(x1, y1, x2, y2, fill=fill, outline="", tags=tag)
        c.create_polygon(
            x1, y1, x2, y1, x2 - t, y1 + t, x1 + t, y1 + t,
            fill=tl, outline="", tags=tag,
        )
        c.create_polygon(
            x1, y1, x1 + t, y1 + t, x1 + t, y2 - t, x1, y2,
            fill=tl, outline="", tags=tag,
        )
        c.create_polygon(
            x1, y2, x1 + t, y2 - t, x2 - t, y2 - t, x2, y2,
            fill=br, outline="", tags=tag,
        )
        c.create_polygon(
            x2, y1, x2, y2, x2 - t, y2 - t, x2 - t, y1 + t,
            fill=br, outline="", tags=tag,
        )

    def _hseg(self, x, y, length, t, color):
        self.canvas.create_polygon(
            x, y + t / 2, x + t / 2, y, x + length - t / 2, y,
            x + length, y + t / 2, x + length - t / 2, y + t,
            x + t / 2, y + t,
            fill=color, outline="", tags="panel",
        )

    def _vseg(self, x, y, length, t, color):
        self.canvas.create_polygon(
            x + t / 2, y, x + t, y + t / 2, x + t, y + length - t / 2,
            x + t / 2, y + length, x, y + length - t / 2, x, y + t / 2,
            fill=color, outline="", tags="panel",
        )

    def _draw_digit(self, x, y, ch):
        w, h, t = self.LED_W, self.LED_H, 3
        segs = SEGMENTS.get(ch, set())
        h2 = h / 2

        def draw(name, on):
            col = LED_ON if on else LED_OFF
            if name == "a":
                self._hseg(x, y, w, t, col)
            elif name == "d":
                self._hseg(x, y + h - t, w, t, col)
            elif name == "g":
                self._hseg(x, y + h2 - t / 2, w, t, col)
            elif name == "f":
                self._vseg(x, y, h2, t, col)
            elif name == "b":
                self._vseg(x + w - t, y, h2, t, col)
            elif name == "e":
                self._vseg(x, y + h2, h2, t, col)
            elif name == "c":
                self._vseg(x + w - t, y + h2, h2, t, col)

        for name in "abcdefg":
            draw(name, name in segs)

    def _draw_counter(self, x, y, value):
        w, h = self.LED_W, self.LED_H
        total = 3 * w + 2 * self.LED_GAP
        self.canvas.create_rectangle(
            x - 2, y - 2, x + total + 2, y + h + 2,
            fill="#000000", outline=DARK, tags="panel",
        )
        if value < 0:
            text = "-" + f"{min(-value, 99):02d}"
        else:
            text = f"{min(value, 999):03d}"
        for i, ch in enumerate(text):
            self._draw_digit(x + i * (w + self.LED_GAP), y, ch)

    def _draw_smiley(self):
        c = self.canvas
        x1, y1, x2, y2 = self.smiley_box
        self._bevel(x1, y1, x2, y2, raised=True, t=2, fill=BG, tag="panel")
        cx, cy = self.smiley_center
        r = 10
        c.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill="#ffff00", outline="#000000", width=1, tags="panel",
        )
        eye_dy = -3
        if self.smiley == "dead":
            for dx in (-4, 4):
                c.create_line(
                    cx + dx - 2, cy + eye_dy - 2, cx + dx + 2, cy + eye_dy + 2,
                    fill="#000000", width=2, tags="panel",
                )
                c.create_line(
                    cx + dx - 2, cy + eye_dy + 2, cx + dx + 2, cy + eye_dy - 2,
                    fill="#000000", width=2, tags="panel",
                )
            c.create_arc(
                cx - 7, cy - 2, cx + 7, cy + 12, start=20, extent=140,
                style="arc", outline="#000000", width=2, tags="panel",
            )
        elif self.smiley == "cool":
            c.create_rectangle(
                cx - 8, cy + eye_dy - 2, cx - 1, cy + eye_dy + 2,
                fill="#000000", outline="#000000", tags="panel",
            )
            c.create_rectangle(
                cx + 1, cy + eye_dy - 2, cx + 8, cy + eye_dy + 2,
                fill="#000000", outline="#000000", tags="panel",
            )
            c.create_line(
                cx - 1, cy + eye_dy, cx + 1, cy + eye_dy,
                fill="#000000", width=2, tags="panel",
            )
            c.create_arc(
                cx - 6, cy - 4, cx + 6, cy + 7, start=200, extent=140,
                style="arc", outline="#000000", width=2, tags="panel",
            )
        else:
            for dx in (-4, 4):
                c.create_oval(
                    cx + dx - 1.5, cy + eye_dy - 1.5, cx + dx + 1.5, cy + eye_dy + 1.5,
                    fill="#000000", outline="", tags="panel",
                )
            if self.smiley == "press":
                c.create_oval(
                    cx - 3, cy + 2, cx + 3, cy + 7,
                    outline="#000000", width=2, tags="panel",
                )
            else:
                c.create_arc(
                    cx - 6, cy - 5, cx + 6, cy + 6, start=200, extent=140,
                    style="arc", outline="#000000", width=2, tags="panel",
                )

    def _draw_panel(self):
        self.canvas.delete("panel")
        self._bevel(
            self.px1, self.py1, self.px2, self.py2,
            raised=False, t=2, fill=BG, tag="panel",
        )
        led_x = self.px1 + 6
        led_y = (self.py1 + self.py2) / 2 - self.LED_H / 2
        self._draw_counter(led_x, led_y, self.mines - len(self.flags))
        total = 3 * self.LED_W + 2 * self.LED_GAP
        self._draw_counter(self.px2 - 6 - total, led_y, self.seconds)
        self._draw_smiley()
        self._raise_ai()

    # ---------- board drawing ----------

    def _cell_coords(self, r, c):
        x1 = self.bx + c * self.CELL
        y1 = self.by + r * self.CELL
        return x1, y1, x1 + self.CELL, y1 + self.CELL

    def _draw_unopened(self, r, c):
        x1, y1, x2, y2 = self._cell_coords(r, c)
        self._bevel(x1, y1, x2, y2, raised=True, t=2, fill=BG, tag="board")
        self._raise_ai()

    def _draw_revealed(self, r, c):
        x1, y1, x2, y2 = self._cell_coords(r, c)
        self.canvas.create_rectangle(
            x1, y1, x2, y2, fill=BG, outline=DARK, width=1, tags="board",
        )
        count = self._count_adjacent(r, c)
        if count > 0:
            self.canvas.create_text(
                x1 + self.CELL / 2, y1 + self.CELL / 2,
                text=str(count), fill=NUMBER_COLORS[count],
                font=("Consolas", int(self.CELL * 0.62), "bold"), tags="board",
            )
        self._raise_ai()

    def _draw_flag(self, r, c):
        x1, y1, x2, y2 = self._cell_coords(r, c)
        cx = x1 + self.CELL / 2
        cy = y1 + self.CELL / 2
        self.canvas.create_rectangle(
            cx - 5, cy + 6, cx + 6, cy + 9, fill="#000000", outline="", tags="board",
        )
        self.canvas.create_line(
            cx + 3, cy - 8, cx + 3, cy + 7, fill="#000000", width=2, tags="board",
        )
        self.canvas.create_polygon(
            cx + 3, cy - 8, cx - 6, cy - 2, cx + 3, cy + 3,
            fill="#ff0000", outline="#000000", tags="board",
        )
        self._raise_ai()

    def _draw_mine(self, r, c):
        x1, y1, x2, y2 = self._cell_coords(r, c)
        cx = x1 + self.CELL / 2
        cy = y1 + self.CELL / 2
        rad = 6
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)):
            self.canvas.create_line(
                cx + dx * rad, cy + dy * rad,
                cx + dx * (rad + 4), cy + dy * (rad + 4),
                fill="#000000", width=2, tags="board",
            )
        self.canvas.create_oval(
            cx - rad, cy - rad, cx + rad, cy + rad,
            fill="#000000", outline="#000000", tags="board",
        )
        self.canvas.create_rectangle(
            cx - 4, cy - 4, cx - 1, cy - 1, fill="#ffffff", outline="", tags="board",
        )

    def _draw_wrong(self, r, c):
        x1, y1, x2, y2 = self._cell_coords(r, c)
        self.canvas.create_line(
            x1 + 5, y1 + 5, x2 - 5, y2 - 5, fill="#ff0000", width=2, tags="board",
        )
        self.canvas.create_line(
            x1 + 5, y2 - 5, x2 - 5, y1 + 5, fill="#ff0000", width=2, tags="board",
        )

    def _build_all(self):
        self.canvas.delete("all")
        self._bevel(
            2, 2, self.canvas.winfo_reqwidth() - 2, self.canvas.winfo_reqheight() - 2,
            raised=True, t=3, fill=BG,
        )
        self.canvas.create_rectangle(
            self.cx1, self.cy1, self.cx2, self.cy2, fill=BG, outline="",
        )
        self._draw_board()
        self._draw_panel()
        self._draw_ai_pointer()

    def _draw_board(self):
        self.canvas.delete("board")
        self._bevel(
            self.bx - 2, self.by - 2,
            self.bx + self.cols * self.CELL + 2,
            self.by + self.rows * self.CELL + 2,
            raised=False, t=3, fill=BG, tag="board",
        )
        for r in range(self.rows):
            for c in range(self.cols):
                self._draw_unopened(r, c)

    # ---------- game logic ----------

    def _neighbors(self, r, c):
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if 0 <= nr < self.rows and 0 <= nc < self.cols:
                    yield nr, nc

    def _count_adjacent(self, r, c):
        return sum(1 for n in self._neighbors(r, c) if n in self.mine_positions)

    def _place_mines(self, safe_r, safe_c):
        safe = {(safe_r, safe_c)} | set(self._neighbors(safe_r, safe_c))
        candidates = [
            (r, c)
            for r in range(self.rows)
            for c in range(self.cols)
            if (r, c) not in safe
        ]
        count = min(self.mines, len(candidates))
        self.mine_positions = set(random.sample(candidates, count))

    def _start_timer(self):
        if not self._running:
            self._running = True
            self._tick()

    def _tick(self):
        if not self._running:
            return
        self.seconds = min(self.seconds + 1, 999)
        self._neko_tick_idle()
        self._draw_panel()
        self._timer_id = self.root.after(1000, self._tick)

    def _left_click(self, event):
        if self.game_over:
            return
        if self._over_smiley(event):
            return
        c = (event.x - self.bx) // self.CELL
        r = (event.y - self.by) // self.CELL
        if not (0 <= r < self.rows and 0 <= c < self.cols):
            return
        if (r, c) in self.flags or (r, c) in self.revealed:
            return
        if not self.started:
            self.started = True
            self._place_mines(r, c)
            self._start_timer()
            self._neko_new_game()
        self._neko_idle_ticks = 0
        _before = set(self.revealed)
        self._reveal(r, c)
        self._neko_after_reveal("player", _before)
        if not self.game_over:
            self._check_win()
        if not self.game_over:
            self._ai_turn()

    def _reveal(self, r, c):
        stack = [(r, c)]
        while stack and not self.game_over:
            cr, cc = stack.pop()
            if (cr, cc) in self.revealed or (cr, cc) in self.flags:
                continue
            if (cr, cc) in self.mine_positions:
                self.revealed.add((cr, cc))
                self._lose()
                return
            self.revealed.add((cr, cc))
            self._draw_revealed(cr, cc)
            if self._count_adjacent(cr, cc) == 0:
                for n in self._neighbors(cr, cc):
                    if n not in self.revealed:
                        stack.append(n)

    def _right_click(self, event):
        if self.game_over or self.turn != "human":
            return
        c = (event.x - self.bx) // self.CELL
        r = (event.y - self.by) // self.CELL
        if not (0 <= r < self.rows and 0 <= c < self.cols):
            return
        self._toggle_flag(r, c)

    def _over_smiley(self, event):
        cx, cy = self.smiley_center
        return abs(event.x - cx) <= 13 and abs(event.y - cy) <= 13

    def _on_press(self, event):
        if self._over_smiley(event):
            self._pressing = True
            self.smiley = "press"
            self._draw_panel()
        elif self.turn == "human" and not self.game_over:
            self._left_click(event)

    def _on_release(self, event):
        if self._pressing:
            self._pressing = False
            if self._over_smiley(event):
                self.reset()
            else:
                self.smiley = "smile"
                self._draw_panel()

    # ---------- AI pointer & turn ----------

    def _draw_ai_pointer(self):
        self.canvas.delete("aiptr")
        x, y = self.ai_pos
        if self.ai_question:
            c = self.canvas
            c.create_oval(
                x, y + 2, x + 10, y + 14,
                fill="#ffff00", outline="#000000", width=1, tags="aiptr",
            )
            c.create_text(
                x + 5, y + 8, text="?", fill="#000000",
                font=("Arial", 9, "bold"), tags="aiptr",
            )
        elif self.ai_flagging:
            c = self.canvas
            c.create_rectangle(
                x, y + 13, x + 5, y + 16, fill="#000000", outline="", tags="aiptr",
            )
            c.create_line(
                x + 2.5, y, x + 2.5, y + 14, fill="#000000", width=2, tags="aiptr",
            )
            c.create_polygon(
                x + 2.5, y, x + 9, y + 4, x + 2.5, y + 8,
                fill="#ff0000", outline="#000000", tags="aiptr",
            )
        else:
            pts = []
            for dx, dy in ARROW_SHAPE:
                pts.extend((x + dx, y + dy))
            fill = "#ff0000" if self.ai_pressed else "#ffffff"
            self.canvas.create_polygon(
                *pts, fill=fill, outline="#000000", width=1, tags="aiptr",
            )
        self.canvas.tag_raise("aiptr")

    def _raise_ai(self):
        self.canvas.tag_raise("aiptr")

    def _cell_center(self, r, c):
        x1, y1, x2, y2 = self._cell_coords(r, c)
        return (x1 + x2) / 2, (y1 + y2) / 2

    def _move_ai_pointer(self, tx, ty, done, duration_ms=None):
        sx, sy = self.ai_pos
        if duration_ms is None:
            dist = math.hypot(tx - sx, ty - sy)
            reach = math.hypot(self.cols * self.CELL, self.rows * self.CELL)
            ratio = min(dist / reach, 1.0) if reach else 0.0
            duration_ms = AI_MOVE_MIN_MS + (AI_MOVE_MAX_MS - AI_MOVE_MIN_MS) * ratio
        duration_ms = max(AI_FRAME_MS, duration_ms)
        start = time.monotonic()

        def step():
            t = min((time.monotonic() - start) * 1000.0 / duration_ms, 1.0)
            e = t * t * t * (t * (t * 6 - 15) + 10)
            self.ai_pos = [sx + (tx - sx) * e, sy + (ty - sy) * e]
            self._draw_ai_pointer()
            if t < 1.0:
                self._ai_after = self.root.after(AI_FRAME_MS, step)
            else:
                done()

        step()

    def _set_title(self):
        if self.game_over:
            label = "扫雷"
        elif self.turn == "ai":
            label = "扫雷 - AI 思考中..."
        else:
            label = "扫雷 - 你的回合"
        self.root.title(label)

    def _ai_scan(self):
        safe = set()
        mines = set()
        for (r, c) in self.revealed:
            if (r, c) in self.mine_positions:
                continue
            num = self._count_adjacent(r, c)
            if num == 0:
                continue
            hidden = [
                n for n in self._neighbors(r, c)
                if n not in self.revealed and n not in self.flags
            ]
            if not hidden:
                continue
            flagged = sum(1 for n in self._neighbors(r, c) if n in self.flags)
            if num == flagged:
                safe.update(hidden)
            elif num == flagged + len(hidden):
                mines.update(hidden)
        return safe, mines

    def _ai_candidates(self):
        return [
            (r, c)
            for r in range(self.rows)
            for c in range(self.cols)
            if (r, c) not in self.revealed and (r, c) not in self.flags
        ]

    def _ai_decide(self):
        safe, mines = self._ai_scan()
        if safe:
            return ("reveal",) + random.choice(sorted(safe))
        if mines:
            return ("flag",) + random.choice(sorted(mines))
        candidates = self._ai_candidates()
        if not candidates:
            return None
        return ("reveal",) + random.choice(candidates)

    def _ai_bad_flags(self):
        bad = set()
        for (r, c) in self.revealed:
            if (r, c) in self.mine_positions:
                continue
            num = self._count_adjacent(r, c)
            if num == 0:
                continue
            nbr_flags = [n for n in self._neighbors(r, c) if n in self.flags]
            if len(nbr_flags) > num:
                bad.update(n for n in nbr_flags if n not in self.ai_flagged)
        return bad

    def _ai_danger(self):
        if not self._ai_bad_flags():
            return False
        candidates = set(self._ai_candidates())
        if not candidates:
            return False
        _, mines = self._ai_scan()
        return candidates <= mines

    def _ai_doubt_chance(self):
        if self._ai_danger():
            return 1.0
        total = self.rows * self.cols
        ratio = len(self.revealed) / total if total else 0.0
        p = (
            AI_DOUBT_BASE
            + (AI_DOUBT_MAX - AI_DOUBT_BASE) * ratio
            + AI_DOUBT_BADFLAG * len(self._ai_bad_flags())
        )
        return min(p, AI_DOUBT_MAX)

    def _ai_wonder(self, r, c):
        if self.game_over or self.turn != "human":
            return False
        if self._wonder_after:
            self.root.after_cancel(self._wonder_after)
        self.ai_question = True
        self._draw_ai_pointer()
        self._wonder_after = self.root.after(AI_WONDER_MS, self._ai_wonder_end)
        self._dev_refresh()
        return True

    def _ai_wonder_end(self):
        self._wonder_after = None
        self.ai_question = False
        self._draw_ai_pointer()
        self._dev_refresh()

    def _clear_wonder(self):
        if self._wonder_after:
            self.root.after_cancel(self._wonder_after)
            self._wonder_after = None
        if self.ai_question:
            self.ai_question = False
            self._draw_ai_pointer()

    def _ai_turn(self):
        if self.game_over or self.turn == "ai":
            return
        self._clear_wonder()
        self.turn = "ai"
        self._neko_send_state()
        self._set_title()
        self._dev_refresh()
        self._ai_after = self.root.after(AI_THINK_MS, self._ai_begin)

    def _ai_begin(self):
        if self.game_over:
            self._finish_ai_turn()
            return
        action = self._ai_decide()
        if action is None:
            self._finish_ai_turn()
            return
        self._ai_last_reason = self._classify_ai_action(action)
        doubt = self._ai_doubt()
        self._doubted_last_turn = doubt is not None
        if doubt is not None:
            self._last_doubt_cell = doubt
            self._neko_ai_doubt(doubt)
            dx, dy = self._cell_center(*doubt)
            self._move_ai_pointer(dx, dy, lambda: self._ai_aim(doubt, action))
        else:
            self._ai_go(action)

    def _ai_doubt(self):
        chance = self._ai_doubt_chance()
        if self._doubted_last_turn and chance < AI_DOUBT_MAX:
            return None
        suspects = set()
        for (r, c) in self.revealed:
            if (r, c) in self.mine_positions:
                continue
            num = self._count_adjacent(r, c)
            if num == 0:
                continue
            nbr_flags = [n for n in self._neighbors(r, c) if n in self.flags]
            if len(nbr_flags) > num:
                suspects.update(n for n in nbr_flags if n not in self.ai_flagged)
        suspects.discard(self._last_doubt_cell)
        if not suspects:
            return None
        if random.random() >= chance:
            return None
        return random.choice(sorted(suspects))

    def _ai_aim(self, doubt, action):
        if self.game_over:
            self._finish_ai_turn()
            return
        if doubt is None:
            self._ai_after = self.root.after(
                AI_AIM_MS, lambda: self._ai_press(*action)
            )
        else:
            self._ai_after = self.root.after(
                AI_AIM_MS, lambda: self._ai_question(action)
            )

    def _ai_question(self, action):
        if self.game_over:
            self._finish_ai_turn()
            return
        self.ai_question = True
        self._draw_ai_pointer()
        self._ai_after = self.root.after(
            AI_QUESTION_MS, lambda: self._ai_resume(action)
        )

    def _ai_resume(self, action):
        self.ai_question = False
        self._draw_ai_pointer()
        self._ai_go(action)

    def _ai_go(self, action):
        if self.game_over:
            self._finish_ai_turn()
            return
        tx, ty = self._cell_center(action[1], action[2])
        self._move_ai_pointer(tx, ty, lambda: self._ai_aim(None, action))

    def _ai_press(self, kind, r, c):
        if self.game_over:
            self._finish_ai_turn()
            return
        if kind == "reveal":
            self.ai_pressed = True
        else:
            self.ai_flagging = True
        self._draw_ai_pointer()
        self._ai_after = self.root.after(
            AI_PRESS_MS, lambda: self._ai_act(kind, r, c)
        )

    def _ai_act(self, kind, r, c):
        if self.game_over:
            self._finish_ai_turn()
            return
        if kind == "reveal":
            if not self.started:
                self.started = True
                self._place_mines(r, c)
                self._start_timer()
                self._neko_new_game()
            self._neko_idle_ticks = 0
            _before = set(self.revealed)
            self._reveal(r, c)
            self._neko_after_reveal("ai", _before)
            if not self.game_over:
                self._check_win()
        else:
            self._toggle_flag(r, c, by_ai=True)
        self.ai_pressed = False
        self.ai_flagging = False
        self.ai_question = False
        self._draw_ai_pointer()
        self._dev_refresh()
        if self.game_over:
            self._finish_ai_turn()
        else:
            self._ai_after = self.root.after(AI_SETTLE_MS, self._finish_ai_turn)

    def _finish_ai_turn(self):
        def back():
            if not self.game_over:
                self.turn = "human"
            self._set_title()

        self._move_ai_pointer(
            self.ai_rest[0], self.ai_rest[1], back, duration_ms=AI_RETURN_MS
        )

    # ---------- N.E.K.O. bridge ----------

    def _neko_new_game(self):
        neko_notify({
            "type": "new_game",
            "board": "%dx%d" % (self.rows, self.cols),
            "mines": self.mines,
        })

    def _classify_ai_action(self, action):
        try:
            safe, mines = self._ai_scan()
        except Exception:
            return "guess"
        cell = (action[1], action[2])
        if cell in safe:
            return "logic_safe"
        if cell in mines:
            return "logic_mine"
        return "guess"

    def _neko_board_text(self):
        safe_total = self.rows * self.cols - len(self.mine_positions)
        safe_found = sum(1 for x in self.revealed if x not in self.mine_positions)
        safe_left = max(safe_total - safe_found, 0)
        mines_unmarked = max(len(self.mine_positions) - len(self.flags), 0)
        rows = []
        for r in range(self.rows):
            tokens = []
            for c in range(self.cols):
                cell = (r, c)
                if cell in self.revealed:
                    tokens.append("*" if cell in self.mine_positions
                                  else str(self._count_adjacent(r, c)))
                elif cell in self.flags:
                    tokens.append("F")
                else:
                    tokens.append(".")
            rows.append(" ".join(tokens))
        header = "%dx%d  雷%d  剩余安全格 %d  未标记雷位 %d" % (
            self.rows, self.cols, self.mines, safe_left, mines_unmarked)
        return header + "\n" + "\n".join(rows)

    def _neko_send_state(self):
        neko_notify({"type": "state", "board": self._neko_board_text()})

    def _neko_after_reveal(self, actor, before):
        if self.game_over:
            self._neko_safe_streak = 0
            return
        new_cells = self.revealed - before
        if not new_cells:
            return
        count = len(new_cells)

        if self._neko_opening:
            # 开局第一手单独作为 opening，不与普通连锁混为一谈
            self._neko_opening = False
            cell = min(new_cells)
            neko_notify({
                "type": "opening",
                "actor": actor,
                "cell": [int(cell[0]), int(cell[1])],
                "count": count,
            })
            self._neko_check_endgame()
            return

        if count >= NEKO_OPEN_MIN:
            cell = min(new_cells)
            neko_notify({
                "type": "big_open",
                "actor": actor,
                "cell": [int(cell[0]), int(cell[1])],
                "count": count,
            })
        max_num = -1
        max_cell = None
        for cell in new_cells:
            if cell in self.mine_positions:
                continue
            num = self._count_adjacent(cell[0], cell[1])
            if num > max_num:
                max_num, max_cell = num, cell
        if max_cell is not None and max_num >= NEKO_RISKY_NUMBER:
            neko_notify({
                "type": "risky_open",
                "actor": actor,
                "cell": [int(max_cell[0]), int(max_cell[1])],
                "number": int(max_num),
            })
        self._neko_safe_streak += 1
        if self._neko_safe_streak >= NEKO_STREAK_MIN:
            neko_notify({
                "type": "streak",
                "actor": actor,
                "count": self._neko_safe_streak,
            })
        self._neko_check_endgame()

    def _neko_check_endgame(self):
        if self._neko_endgame_sent or self.game_over:
            return
        safe_total = self.rows * self.cols - len(self.mine_positions)
        safe_found = sum(1 for x in self.revealed if x not in self.mine_positions)
        safe_left = safe_total - safe_found
        if 0 < safe_left <= NEKO_ENDGAME_SAFE_LEFT:
            self._neko_endgame_sent = True
            mines_unmarked = max(len(self.mine_positions) - len(self.flags), 0)
            neko_notify({
                "type": "endgame",
                "safe_left": int(safe_left),
                "mines_left": int(mines_unmarked),
            })

    def _neko_tick_idle(self):
        if not self.started or self.game_over or self.turn != "human":
            self._neko_idle_ticks = 0
            return
        self._neko_idle_ticks += 1
        if self._neko_idle_ticks == NEKO_IDLE_SECONDS:
            neko_notify({"type": "idle", "seconds": NEKO_IDLE_SECONDS})

    def _neko_ai_doubt(self, cell):
        neko_notify({"type": "ai_doubt", "cell": [int(cell[0]), int(cell[1])]})

    # ---------- developer mode ----------

    def _build_menu(self):
        menubar = tk.Menu(self.root, tearoff=0)
        options = tk.Menu(menubar, tearoff=0)
        options.add_command(label="开发者模式", command=self._open_dev_window)
        menubar.add_cascade(label="选项", menu=options)

        menubar.add_command(label="排行", command=self._open_rank_window)

        menubar.add_command(label="帮助", command=self._open_help_window)

        self.root.config(menu=menubar)

    def _open_dev_window(self):
        if self._dev_window is not None:
            self._dev_window.deiconify()
            self._dev_window.lift()
            self._dev_refresh()
            return
        top = tk.Toplevel(self.root)
        top.title("开发者模式")
        top.configure(bg=BG)
        top.resizable(False, False)
        top.transient(self.root)
        top.bind("<Destroy>", self._on_dev_destroy)
        self._dev_window = top
        self._restore_geometry(top, "dev")
        top.protocol("WM_DELETE_WINDOW", lambda: self._close_window(top, "dev"))

        tk.Label(
            top, text="开发者模式 - 扫雷", bg=BG, fg="#000080",
            font=("Consolas", 12, "bold"),
        ).pack(padx=10, pady=(10, 6))

        self._dev_vars = {}

        def section(title_text, keys):
            box = tk.Frame(top, bg=BG, bd=2, relief="sunken")
            box.pack(fill="x", padx=10, pady=4)
            tk.Label(
                box, text=title_text, bg=BG, font=("Consolas", 10, "bold"),
                anchor="w",
            ).pack(fill="x", padx=6, pady=(6, 2))
            for key in keys:
                var = tk.StringVar()
                self._dev_vars[key] = var
                tk.Label(
                    box, textvariable=var, bg=BG, font=("Consolas", 9),
                    justify="left", anchor="w",
                ).pack(fill="x", padx=6, pady=1)

        section(
            "① 取消 AI 旗 → 是否触发？",
            ["a_code", "a_last", "a_log", "a_state"],
        )
        section(
            "② AI 疑惑玩家插的旗 → 目前触发概率",
            ["b_chance", "b_parts", "b_cool"],
        )

        tk.Button(
            top, text="关闭", command=lambda: self._close_window(top, "dev"),
            font=("Consolas", 9),
        ).pack(pady=(6, 10))

        self._dev_vars["a_code"].set(
            "removed_ai = (r, c) in ai_flagged\n"
            "if removed_ai and not by_ai and turn == \"human\":\n"
            "    _ai_wonder(r, c)   # 100% 原地显示 \"?\""
        )
        self._dev_refresh()

    def _on_dev_destroy(self, event):
        if event.widget is self._dev_window:
            self._dev_window = None

    def _record_cancel(self, r, c, triggered):
        entry = {
            "cell": (r, c),
            "triggered": bool(triggered),
            "time": time.strftime("%H:%M:%S"),
        }
        self._last_cancel = entry
        self._cancel_log.append(entry)

    def _dev_refresh(self):
        if self._dev_window is None or not self._dev_vars:
            return
        v = self._dev_vars
        last = self._last_cancel
        if last is None:
            v["a_last"].set("最近一次取消 AI 旗：（无）")
        else:
            v["a_last"].set(
                "最近一次取消 AI 旗：%s  触发=%s  %s"
                % (str(last["cell"]), last["triggered"], last["time"])
            )
        if self._cancel_log:
            v["a_log"].set(
                "取消 AI 旗日志：\n"
                + "\n".join(
                    "  %s  %-8s  触发=%s"
                    % (e["time"], str(e["cell"]), e["triggered"])
                    for e in self._cancel_log
                )
            )
        else:
            v["a_log"].set("取消 AI 旗日志：（暂无记录）")
        v["a_state"].set(
            "ai_flagged=%s\nai_question=%s   _wonder_after=%s"
            % (sorted(self.ai_flagged), self.ai_question,
               "挂起" if self._wonder_after else "无")
        )

        total = self.rows * self.cols
        ratio = len(self.revealed) / total if total else 0.0
        bad = len(self._ai_bad_flags())
        danger = self._ai_danger()
        chance = self._ai_doubt_chance()
        v["b_chance"].set("当前触发概率：%.3f" % chance)
        v["b_parts"].set(
            "基础 %.2f + (%.2f-%.2f)×已翻开比例(%.3f) + %.2f×错旗数(%d)\n危险局面=%s"
            % (AI_DOUBT_BASE, AI_DOUBT_MAX, AI_DOUBT_BASE, ratio,
               AI_DOUBT_BADFLAG, bad, danger)
        )
        v["b_cool"].set(
            "冷却：上回合已怀疑=%s  当前%s"
            % (self._doubted_last_turn,
               "关闭（概率已达上限，可连续触发）" if chance >= AI_DOUBT_MAX else "生效")
        )

    # ---------- ranking ----------

    def _record_score(self):
        if self._game_recorded:
            return
        self._game_recorded = True
        prev_best = min(
            (e["seconds"] for e in self._scores if e["score"] == self.mines),
            default=None,
        )
        correct = len(self.flags & self.mine_positions)
        self._scores.append({
            "score": correct,
            "mines": self.mines,
            "seconds": self.seconds,
            "board": "%dx%d / %d 雷" % (self.rows, self.cols, self.mines),
            "time": time.strftime("%H:%M:%S"),
        })
        self._rank_refresh()
        if self.smiley == "cool" and correct == self.mines:
            if prev_best is None or self.seconds < prev_best:
                neko_notify({
                    "type": "new_record",
                    "seconds": int(self.seconds),
                    "previous": (int(prev_best) if prev_best is not None else None),
                })

    def _sorted_scores(self):
        return sorted(self._scores, key=lambda e: (-e["score"], e["seconds"]))

    def _open_rank_window(self):
        if self._rank_window is not None:
            self._rank_window.deiconify()
            self._rank_window.lift()
            self._rank_refresh()
            return
        top = tk.Toplevel(self.root)
        top.title("排行榜")
        top.configure(bg=BG)
        top.resizable(False, False)
        top.transient(self.root)
        top.bind("<Destroy>", self._on_rank_destroy)
        self._rank_window = top
        self._restore_geometry(top, "rank")
        top.protocol("WM_DELETE_WINDOW", lambda: self._close_window(top, "rank"))

        toolbar = tk.Frame(top, bg=BG)
        toolbar.pack(fill="x", padx=10, pady=(8, 0))
        tk.Button(
            toolbar, text="清空排行", font=("Consolas", 9),
            command=self._clear_scores,
        ).pack(side="left")

        tk.Label(
            top, text="排行榜（本次运行）", bg=BG, fg="#000080",
            font=("Consolas", 12, "bold"),
        ).pack(padx=10, pady=(4, 6))

        box = tk.Frame(top, bg=BG, bd=2, relief="sunken")
        box.pack(fill="x", padx=10, pady=4)

        self._rank_vars = {}
        self._rank_cols = [("名次", 6), ("标记正确", 10), ("用时", 7),
                           ("棋盘", 14), ("时间", 9)]
        header = tk.Frame(box, bg=BG)
        header.pack(fill="x", padx=6, pady=(6, 2))
        for col, (title, width) in enumerate(self._rank_cols):
            tk.Label(
                header, text=title, width=width, anchor="w", bg=BG,
                font=("Consolas", 9, "bold"),
            ).grid(row=0, column=col, sticky="w")

        self._rank_rows = tk.Frame(box, bg=BG)
        self._rank_rows.pack(fill="x", padx=6, pady=(0, 6))

        var = tk.StringVar()
        self._rank_vars["count"] = var
        tk.Label(top, textvariable=var, bg=BG, font=("Consolas", 9)).pack(pady=(0, 2))

        tk.Button(
            top, text="关闭", font=("Consolas", 9),
            command=lambda: self._close_window(top, "rank"),
        ).pack(pady=(4, 10))

        self._rank_refresh()

    def _on_rank_destroy(self, event):
        if event.widget is self._rank_window:
            self._rank_window = None

    def _clear_scores(self):
        self._scores = []
        self._rank_refresh()

    # ---------- help ----------

    def _open_help_window(self):
        if self._help_window is not None:
            self._help_window.deiconify()
            self._help_window.lift()
            return
        top = tk.Toplevel(self.root)
        top.title("帮助")
        top.configure(bg=BG)
        top.resizable(False, False)
        top.transient(self.root)
        top.bind("<Destroy>", self._on_help_destroy)
        self._help_window = top
        self._restore_geometry(top, "help")
        top.protocol("WM_DELETE_WINDOW", lambda: self._close_window(top, "help"))

        tk.Label(
            top, text="扫雷 × N.E.K.O.", bg=BG, fg="#000080",
            font=("Microsoft YaHei", 12, "bold"),
        ).pack(padx=16, pady=(10, 6))

        body = tk.Frame(top, bg=BG)
        body.pack(padx=16, pady=(0, 4), anchor="w")
        for line in (
            "· YUI 会在一旁协助你",
            "· YUI 偶尔会开口说点什么？",
            "· YUI 可以了解当前的局面",
        ):
            tk.Label(
                body, text=line, bg=BG, anchor="w",
                font=("Microsoft YaHei", 10),
            ).pack(anchor="w", pady=1)

        # 分隔线（细灰线）
        tk.Frame(top, height=1, bd=0, bg="#000000").pack(
            fill="x", padx=16, pady=(6, 4))

        tk.Label(
            top, text="· 可能看错||||", bg=BG, fg="#333333", anchor="w",
            font=("Microsoft YaHei", 9),
        ).pack(padx=16, pady=(0, 1), anchor="w")
        tk.Label(
            top, text="· 不要压力YUI哦！", bg=BG, fg="#333333", anchor="w",
            font=("Microsoft YaHei", 9),
        ).pack(padx=16, pady=(0, 2), anchor="w")

        self._help_status_var = tk.StringVar()
        tk.Label(
            top, textvariable=self._help_status_var, bg=BG, fg="#000080",
            font=("Microsoft YaHei", 9),
        ).pack(padx=16, pady=(2, 4))

        tk.Button(
            top, text="关闭", font=("Consolas", 9),
            command=lambda: self._close_window(top, "help"),
        ).pack(pady=(4, 10))

        self._help_tick()

    def _on_help_destroy(self, event):
        if event.widget is self._help_window:
            self._help_window = None
            if self._help_after:
                try:
                    self.root.after_cancel(self._help_after)
                except Exception:
                    pass
                self._help_after = None

    def _neko_connected(self):
        try:
            with socket.create_connection(("127.0.0.1", NEKO_PORT), timeout=0.05):
                return True
        except OSError:
            return False
        except Exception:
            return False

    def _help_tick(self):
        if self._help_window is None or self._help_status_var is None:
            return
        state = "已连接" if self._neko_connected() else "未连接"
        self._help_status_var.set("插件状态：%s" % state)
        self._help_after = self.root.after(2000, self._help_tick)

    def _rank_refresh(self):
        if self._rank_window is None or self._rank_rows is None:
            return
        for child in self._rank_rows.winfo_children():
            child.destroy()
        rows = self._sorted_scores()
        shown = rows[:15]
        if not shown:
            tk.Label(
                self._rank_rows, text="暂无记录", bg=BG, anchor="w",
                font=("Consolas", 9),
            ).grid(row=0, column=0, columnspan=len(self._rank_cols), sticky="w")
        else:
            for i, e in enumerate(shown, 1):
                passed = e["score"] >= e.get("mines", self.mines)
                cells = [
                    (str(i), "#000000"),
                    ("通关" if passed else str(e["score"]),
                     "#008000" if passed else "#000000"),
                    ("%d:%02d" % divmod(e["seconds"], 60), "#000000"),
                    (e["board"], "#000000"),
                    (e["time"], "#000000"),
                ]
                for col, (text, fg) in enumerate(cells):
                    tk.Label(
                        self._rank_rows, text=text,
                        width=self._rank_cols[col][1], anchor="w",
                        bg=BG, fg=fg, font=("Consolas", 9),
                    ).grid(row=i - 1, column=col, sticky="w")
        count = "共 %d 局" % len(rows)
        if len(rows) > 15:
            count += "（仅显示前 15）"
        self._rank_vars["count"].set(count)

    # ---------- shared actions ----------

    def _toggle_flag(self, r, c, by_ai=False):
        if (r, c) in self.revealed:
            return
        was_flagged = (r, c) in self.flags
        removed_ai_flag = False
        if was_flagged:
            removed_ai_flag = (r, c) in self.ai_flagged
            self.flags.discard((r, c))
            self.ai_flagged.discard((r, c))
            self._draw_unopened(r, c)
        else:
            if len(self.flags) >= self.mines:
                return
            self.flags.add((r, c))
            if by_ai:
                self.ai_flagged.add((r, c))
            self._draw_flag(r, c)
        self._draw_panel()
        self._raise_ai()
        if not by_ai:
            self._neko_idle_ticks = 0
            if removed_ai_flag:
                triggered = self._ai_wonder(r, c)
                self._record_cancel(r, c, triggered)
                neko_notify({"type": "ai_unflag", "cell": [int(r), int(c)]})
            elif not was_flagged:
                neko_notify({"type": "player_flag", "cell": [int(r), int(c)]})
        self._dev_refresh()

    def _lose(self):
        self.game_over = True
        self._running = False
        self._clear_wonder()
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None
        self.smiley = "dead"
        for (r, c) in self.mine_positions:
            if (r, c) not in self.flags:
                self._draw_mine(r, c)
        for (r, c) in self.flags:
            if (r, c) not in self.mine_positions:
                self._draw_wrong(r, c)
        self._draw_panel()
        self._set_title()
        self._record_score()
        neko_notify({"type": "mine_hit", "actor": "ai" if self.turn == "ai" else "player"})
        neko_notify({
            "type": "lose",
            "correct": len(self.flags & self.mine_positions),
            "mines": self.mines,
        })
        messagebox.showinfo("扫雷", "很遗憾，踩到地雷了！")

    def _check_win(self):
        if len(self.revealed) == self.rows * self.cols - len(self.mine_positions):
            self.game_over = True
            self._running = False
            self._clear_wonder()
            if self._timer_id:
                self.root.after_cancel(self._timer_id)
                self._timer_id = None
            self.smiley = "cool"
            for (r, c) in self.mine_positions:
                if (r, c) not in self.flags:
                    self.flags.add((r, c))
                    self._draw_flag(r, c)
            self._draw_panel()
            self._set_title()
            self._record_score()
            neko_notify({"type": "win", "seconds": self.seconds, "mines": self.mines})
            messagebox.showinfo("扫雷", "恭喜你，排雷成功！")

    def reset(self):
        self.mine_positions = set()
        self.revealed = set()
        self.flags = set()
        self.started = False
        self.game_over = False
        self.seconds = 0
        self.smiley = "smile"
        self._running = False
        self.turn = "human"
        self.ai_pressed = False
        self.ai_flagging = False
        self.ai_question = False
        self.ai_flagged = set()
        self._last_doubt_cell = None
        self._doubted_last_turn = False
        self._ai_last_reason = "guess"
        self._neko_safe_streak = 0
        self._neko_endgame_sent = False
        self._neko_idle_ticks = 0
        self._neko_opening = True
        self._last_cancel = None
        self._cancel_log.clear()
        self._game_recorded = False
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None
        if self._ai_after:
            self.root.after_cancel(self._ai_after)
            self._ai_after = None
        if self._wonder_after:
            self.root.after_cancel(self._wonder_after)
            self._wonder_after = None
        self.ai_pos = list(self.ai_rest)
        self._build_all()
        self._set_title()
        self._dev_refresh()


if __name__ == "__main__":
    root = tk.Tk()
    Minesweeper(root, rows=9, cols=9, mines=10)
    root.mainloop()
