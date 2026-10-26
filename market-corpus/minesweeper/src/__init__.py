"""
### ！！！
### 注意： 该文件中的文本/注释（除了4个功能介绍的文本是我写的） 完全由ai编写，请注意辨别
### ！！！

合作扫雷 (minesweeper) — 把扫雷的局面与事件转述给陪伴角色。

游戏侧（minesweeper_plugin.py）通过本机回环 TCP 发送一行一条 JSON；
本插件在**后台线程**里监听端口，用本地缓存 + push_message / llm_tool 让角色知晓战况。

为什么用线程而不是 asyncio 任务：插件进程只在生命周期握手时运行 event loop，
握手结束后 loop 关闭，asyncio 后台任务会被取消。官方插件（memo_reminder）同样
使用 threading 线程承载后台工作。

**省上下文设计**
- 平时**不推送**任何静默消息；把最近 **2 步**（每步 = 一个 COALESCE_WINDOW 窗口的
  事实短句）与最新**棋盘**缓存在本进程内存里（新开一局会清空缓存，避免串局）。
- **白名单事件**（开局 / 起疑 / 拔旗 / 残局 / 胜负）触发时，只发那一句 `respond`，
  并带 `coalesce_key="ms_speak"`（队列中旧的被最新覆盖，避免堆积）。
- 战况汇总**不主动推送**：她可用 `@llm_tool minesweeper_status` **按需**拉取。
  （汇总推送的实现 `_push_summary` 仍在，只是被注释停用，需要时可恢复。）
- **不读取对话内容**（本插件不监听聊天，只接收游戏事件）。
- 推送带 `target_lanlan`（从调用上下文捕获的角色名；未捕获到则为 None）。
- 所有推送文本统一加 `EVENT_PREFIX`（`[扫雷] `）前缀，声明这是"外部事件通知"，
  弱化被模型当成情绪刺激（含 `test_push`，保证测试与真实一致）。

**内置游戏 + 指令启动**
- 游戏本体随包放在 `game/`（`minesweeper.py` + `minesweeper_plugin.py`）。
- 另随包内置一份**便携 Python**（`vendor/python/`，含 tkinter/tcl），用户无需自装 Python。
- 用户对角色说“启动扫雷”等 → 角色调用 `@llm_tool start_minesweeper` →
  插件用 `subprocess` 拉起 `vendor/python/python.exe game/minesweeper.py`（**不在宿主进程里创建 Tk**，
  宿主的冻结运行时缺 Tcl 脚本库，直接建 Tk 会让插件进程崩溃）。
- 事件仍走 `127.0.0.1:39001` 回环（游戏是客户端、本插件是服务端）。
- 插件停止/重载时终止该子进程。

视角约定：游戏里的 AI 队友 = 角色本人（「你」），另一方默认称「人类」
（称呼由 HUMAN_LABEL 控制，可按不同猫娘改成「对方」「玩家」「主人」等）。

事件格式（一行一个 JSON 对象）：
    {"type": "state", "board": "..."}
    {"type": "new_game", "board": "9x9", "mines": 10}
    {"type": "opening", "actor": "player"|"ai", "cell": [r, c], "count": 22}
    {"type": "big_open", "actor": "player"|"ai", "cell": [r, c], "count": 6}
    {"type": "risky_open", "actor": "player"|"ai", "cell": [r, c], "number": 3}
    {"type": "streak", "actor": "player"|"ai", "count": 10}
    {"type": "player_flag", "cell": [r, c]}
    {"type": "ai_doubt", "cell": [r, c]}
    {"type": "ai_unflag", "cell": [r, c]}
    {"type": "endgame", "safe_left": 3, "mines_left": 2}
    {"type": "idle", "seconds": 45}
    {"type": "mine_hit", "actor": "player"|"ai"}
    {"type": "win", "seconds": 42, "mines": 10}
    {"type": "lose", "correct": 7, "mines": 10}
    {"type": "new_record", "seconds": 42, "previous": 55}
"""

import asyncio
import json
import os
import random
import socket
import subprocess
import threading
import time
from collections import deque

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

DEFAULT_PORT = 39001

# 插件根目录
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
# 随包内置的游戏源码目录（minesweeper.py / minesweeper_plugin.py）
GAME_DIR = os.path.join(PLUGIN_DIR, "game")
# 随包内置的第三方依赖目录（便携 Python 等）；官方打包会随包发布，CI 的 Ruff 会跳过
VENDOR_DIR = os.path.join(PLUGIN_DIR, "vendor")
# 随包内置的便携 Python 与游戏脚本（用 subprocess 启动，避免在宿主进程里跑 Tk）
PYTHON_EXE = os.path.join(VENDOR_DIR, "python", "python.exe")
GAME_SCRIPT = os.path.join(GAME_DIR, "minesweeper.py")

# 对人类一方的称呼。不同猫娘可按需改成「对方」「玩家」「主人」等。
HUMAN_LABEL = "人类"

# 推送文本统一加的事件标记（声明这是"外部事件通知"，弱化被当成情绪刺激）。
EVENT_PREFIX = "[扫雷] "

# 同一操作的事件合并窗口（秒）；一个窗口 = 缓存里的“一步”。
COALESCE_WINDOW = 0.35

# 缓存保留的步数（最近 N 步）。
CACHE_STEPS = 2

# 汇总与上一条完全相同时，在此秒数内不重复发送。
SUMMARY_DEDUP_SECONDS = 120.0

# 说话白名单：只有这些事件会让她开口。
_SPEAK = {"ai_unflag", "win", "lose", "new_game", "ai_doubt", "endgame"}

# push_message 的优先级。
_PRIORITY = {
    "state": 2,
    "summary": 3,
    "new_game": 4,
    "big_open": 4,
    "risky_open": 4,
    "streak": 4,
    "player_flag": 4,
    "ai_doubt": 5,
    "ai_unflag": 5,
    "endgame": 5,
    "idle": 4,
    "mine_hit": 5,
    "win": 6,
    "lose": 6,
    "new_record": 7,
    "test_push": 4,
}

# 同一窗口内谁当"说话主事件"（数值越大越重要）。
_RANK = {
    "win": 100,
    "lose": 100,
    "new_game": 95,
    "mine_hit": 90,
    "new_record": 80,
    "endgame": 70,
    "ai_unflag": 60,
    "ai_doubt": 55,
    "big_open": 50,
    "opening": 45,
    "risky_open": 40,
    "streak": 30,
    "player_flag": 25,
    "idle": 20,
    "test_push": 10,
}

# 说话类事件的措辞池（客观说法，随机轮换，减少重复感）。
_DOUBT_POOL = [
    "你在怀疑{cell}的旗。",
    "你对{cell}那面旗起了疑心。",
]
_UNFLAG_POOL = [
    "{human}把你插在{cell}的旗拔掉了。",
    "{human}把你在{cell}插的旗拔掉了。",
]
_ENDGAME_POOL = [
    "还剩 {safe_left} 格未翻开，其中 {mines_left} 个是雷。",
    "快到收尾阶段了，剩 {safe_left} 格没开。",
]


def _fmt_cell(cell):
    try:
        r, c = int(cell[0]) + 1, int(cell[1]) + 1
        return "第%d行第%d列" % (r, c)
    except (TypeError, ValueError, IndexError):
        return "某个格子"


def _who(actor):
    # 视角约定：AI 一方 = 角色本人（「你」），另一方 = HUMAN_LABEL
    return "你" if actor == "ai" else HUMAN_LABEL


@neko_plugin
class MinesweeperPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._sock = None
        self._thread = None
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._pending = []
        self._pending_deadline = 0.0
        self._steps = deque(maxlen=CACHE_STEPS)
        self._board = ""
        self._last_summary_text = ""
        self._last_summary_at = 0.0
        self._role_pending = False
        self._port = DEFAULT_PORT
        self._last_event = None
        self._game_proc = None
        self._game_error = None
        self._lanlan = None  # 当前角色名（从调用上下文捕获，用于 target_lanlan）

    # ---------- target lanlan ----------

    @staticmethod
    def _resolve_target_lanlan(kwargs):
        """从本次调用上下文解析目标角色名（官方推荐做法）。

        注意：不使用 ctx._current_lanlan —— 官方注释指出它是"上一次调用留下的"，
        可能把消息串到别的角色。
        """
        if not isinstance(kwargs, dict):
            return None
        explicit = kwargs.get("target_lanlan")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        ctx_obj = kwargs.get("_ctx")
        if isinstance(ctx_obj, dict):
            name = ctx_obj.get("lanlan_name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        return None

    def _remember_target(self, kwargs):
        name = self._resolve_target_lanlan(kwargs)
        if name:
            self._lanlan = name
        return name

    # ---------- lifecycle ----------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._stop_event.clear()
        self._ready.clear()
        self._port = await self._resolve_port()
        self._thread = threading.Thread(
            target=self._serve_blocking,
            args=(self._port,),
            daemon=True,
            name="ms-bridge",
        )
        self._thread.start()
        self._ready.wait(timeout=2.0)
        listening = self._sock is not None
        if listening:
            self.logger.info("minesweeper bridge listening on 127.0.0.1:%d", self._port)
        else:
            self.logger.error("minesweeper bridge failed to listen on 127.0.0.1:%d", self._port)
        return Ok({"status": "ready" if listening else "bind_failed", "port": self._port})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._stop_game()
        self._stop_event.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        self.logger.info("minesweeper bridge shutdown")
        return Ok({"status": "stopped"})

    # ---------- bundled game (start/stop) ----------

    def _read_stderr_tail(self, max_chars=800):
        path = os.path.join(GAME_DIR, "game_stderr.log")
        try:
            with open(path, "rb") as f:
                data = f.read()
            text = data.decode("utf-8", "replace").strip()
            return text[-max_chars:] if text else ""
        except Exception:
            return ""

    def _start_game(self):
        proc = self._game_proc
        if proc is not None and proc.poll() is None:
            return False  # 已经在运行
        self._game_error = None
        if not os.path.isfile(PYTHON_EXE):
            self._game_error = "未找到内置 Python：%s" % PYTHON_EXE
            self.logger.error("game: %s", self._game_error)
            return True
        stderr_path = os.path.join(GAME_DIR, "game_stderr.log")
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with open(stderr_path, "wb") as errf:
                self._game_proc = subprocess.Popen(
                    [PYTHON_EXE, GAME_SCRIPT],
                    cwd=GAME_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=errf,
                    creationflags=creationflags,
                )
            self.logger.info("game: launched pid=%s", self._game_proc.pid)
        except Exception as e:
            self._game_error = "%s: %s" % (type(e).__name__, e)
            self._game_proc = None
            self.logger.error("game: launch failed: %s", self._game_error)
        return True

    def _stop_game(self):
        proc = self._game_proc
        self._game_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    proc.kill()
            except Exception:
                pass

    def _game_running(self):
        proc = self._game_proc
        return bool(proc is not None and proc.poll() is None)

    async def _resolve_port(self):
        try:
            cfg = await self.config.dump(timeout=5.0)
            section = cfg.get("minesweeper") or {}
            return int(section.get("port", DEFAULT_PORT))
        except Exception as e:
            self.logger.warning("read port config failed, using default: %s", e)
            return DEFAULT_PORT

    # ---------- background TCP server (runs in its own thread) ----------

    def _serve_blocking(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(8)
            sock.settimeout(0.1)
        except OSError as e:
            self.logger.error("cannot bind 127.0.0.1:%d: %s", port, e)
            try:
                sock.close()
            except Exception:
                pass
            self._ready.set()
            return

        self._sock = sock
        self._ready.set()
        self.logger.info("minesweeper bridge listening on 127.0.0.1:%d", port)
        try:
            while not self._stop_event.is_set():
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    self._flush_pending()
                    continue
                except OSError:
                    break
                try:
                    self._handle_conn(conn)
                except Exception:
                    self.logger.exception("client handler error")
                self._flush_pending()
        finally:
            self._flush_pending(force=True)
            try:
                sock.close()
            except Exception:
                pass
            self._sock = None
            self.logger.info("minesweeper bridge listener stopped")

    def _handle_conn(self, conn):
        conn.settimeout(2.0)
        buf = b""
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    self._dispatch(event)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ---------- dispatch / cache ----------

    def _dispatch(self, event):
        if not isinstance(event, dict):
            return
        self._last_event = event
        etype = event.get("type")
        if not isinstance(etype, str):
            return
        if etype == "state":
            text = self._render("state", event)
            if text:
                with self._lock:
                    self._board = text
            return
        if etype == "new_game":
            # 新开一局：清空上一局残留（棋盘随后会被同批的 state 覆盖为新盘面）
            with self._lock:
                self._steps.clear()
                self._board = ""
                self._last_summary_text = ""
                self._last_summary_at = 0.0
                self._role_pending = True
        with self._lock:
            self._pending.append(event)
            self._pending_deadline = time.monotonic() + COALESCE_WINDOW

    def _flush_pending(self, force=False):
        with self._lock:
            if not self._pending:
                return
            if not force and time.monotonic() < self._pending_deadline:
                return
            events = self._pending
            self._pending = []
            self._pending_deadline = 0.0

        items = self._coalesce(events)
        if not items:
            return

        speak_items = [it for it in items if it[0] in _SPEAK]
        silent_items = [it for it in items if it[0] not in _SPEAK]

        # 静默事件：只写进缓存（一步），不推送
        if silent_items:
            silent_items.sort(key=lambda it: it[2], reverse=True)
            step = "；".join(it[1].rstrip("。") for it in silent_items)
            with self._lock:
                self._steps.append(step)

        # 白名单说话事件
        if speak_items:
            primary = max(speak_items, key=lambda it: it[2])
            # 【汇总推送已停用】平时不再推送战况汇总（她可按需调用 minesweeper_status 拉取）。
            # 如需恢复：取消下一行注释即可（_push_summary / _build_summary 均保留）。
            # self._push_summary()
            self._push(primary[0], primary[1], behavior="respond")

    def _build_summary(self, consume_role=False):
        with self._lock:
            steps = list(self._steps)
            board = self._board
            role = self._role_pending
            if consume_role and role:
                self._role_pending = False
        parts = []
        if role:
            parts.append("（本局：你和人类合作排雷）")
        if len(steps) >= 2:
            parts.append("上一步：%s" % steps[-2])
            parts.append("当前：%s" % steps[-1])
        elif steps:
            parts.append("当前：%s" % steps[-1])
        if board:
            parts.append(board)
        return "\n".join(parts)

    def _push_summary(self):
        text = self._build_summary(consume_role=True)
        if not text:
            return
        now = time.monotonic()
        with self._lock:
            if text == self._last_summary_text and (now - self._last_summary_at) < SUMMARY_DEDUP_SECONDS:
                return
            self._last_summary_text = text
            self._last_summary_at = now
        self._push("summary", text, behavior="read")

    def _coalesce(self, events):
        types = {e.get("type") for e in events}
        used = set()
        items = []

        if "mine_hit" in types and "lose" in types:
            hit = next(e for e in events if e.get("type") == "mine_hit")
            lose = next(e for e in events if e.get("type") == "lose")
            used.add(id(hit))
            used.add(id(lose))
            items.append(("lose", self._render_loss_merged(hit, lose), _RANK["lose"]))

        if "win" in types and "new_record" in types:
            win = next(e for e in events if e.get("type") == "win")
            rec = next(e for e in events if e.get("type") == "new_record")
            used.add(id(win))
            used.add(id(rec))
            items.append(("win", self._render_win_merged(win, rec), _RANK["win"]))

        for e in events:
            if id(e) in used:
                continue
            etype = e.get("type")
            text = self._render(etype, e)
            if text:
                items.append((etype, text, _RANK.get(etype, 0)))
        return items

    # ---------- rendering ----------

    def _render(self, etype, event):
        cell = _fmt_cell(event.get("cell"))
        actor = event.get("actor")

        if etype == "state":
            board = event.get("board")
            return "当前棋盘：\n" + board if isinstance(board, str) else None

        # ---- 说话类 ----
        if etype == "new_game":
            return "新的一局扫雷开始了，你和人类一起排雷：%s，共 %s 颗雷。" % (
                event.get("board", "未知棋盘"),
                event.get("mines", "?"),
            )
        if etype == "ai_doubt":
            return random.choice(_DOUBT_POOL).format(cell=cell)
        if etype == "ai_unflag":
            return random.choice(_UNFLAG_POOL).format(human=HUMAN_LABEL, cell=cell)
        if etype == "endgame":
            return random.choice(_ENDGAME_POOL).format(
                safe_left=event.get("safe_left", "?"),
                mines_left=event.get("mines_left", "?"),
            )
        if etype == "win":
            return "胜利，用时 %s 秒。" % event.get("seconds", "?")
        if etype == "lose":
            return "本局结束，标记正确 %s 个雷。" % event.get("correct", "?")

        # ---- 静默记事类（只报事实） ----
        if etype == "opening":
            return "%s开局翻开了 %s 格" % (_who(actor), event.get("count", "?"))
        if etype == "big_open":
            return "%s翻开了 %s 格" % (_who(actor), event.get("count", "?"))
        if etype == "risky_open":
            return "%s翻出了数字 %s" % (_who(actor), event.get("number", "?"))
        if etype == "streak":
            return "连续安全格 %s" % event.get("count", "?")
        if etype == "player_flag":
            return "%s在%s插旗" % (HUMAN_LABEL, cell)
        if etype == "idle":
            return "已 %s 秒无操作" % event.get("seconds", "?")
        if etype == "mine_hit":
            return "你踩雷" if actor == "ai" else "%s踩雷" % HUMAN_LABEL
        if etype == "new_record":
            previous = event.get("previous")
            if previous is None:
                return "完成用时 %s 秒" % event.get("seconds", "?")
            return "刷新最佳记录（原 %s 秒）" % previous

        return None

    def _render_loss_merged(self, hit, lose):
        who = "你自己" if hit.get("actor") == "ai" else HUMAN_LABEL
        correct = lose.get("correct", "?")
        template = random.choice(
            [
                "{who}踩到雷了，本局结束，标记正确 {correct} 个雷。",
                "这局结束了：{who}踩到雷，标记正确 {correct} 个雷。",
            ]
        )
        return template.format(who=who, correct=correct)

    def _render_win_merged(self, win, rec):
        seconds = win.get("seconds", "?")
        previous = rec.get("previous")
        if previous is None:
            template = random.choice(
                [
                    "胜利，用时 {seconds} 秒，完成了这一局。",
                    "这一局赢了，用时 {seconds} 秒。",
                ]
            )
            return template.format(seconds=seconds)
        template = random.choice(
            [
                "胜利，用时 {seconds} 秒，刷新了最佳记录（原 {previous} 秒）。",
                "这一局赢了，用时 {seconds} 秒，还刷新了最佳记录（原 {previous} 秒）。",
            ]
        )
        return template.format(seconds=seconds, previous=previous)

    # ---------- push ----------

    def _push(self, etype, text, behavior="read"):
        # 本方法在工作线程里被调用；push_message 是同步 ZMQ 发送，直接调用即可。
        if not text.startswith(EVENT_PREFIX):
            text = EVENT_PREFIX + text
        try:
            receipt = self.push_message(
                source="minesweeper",
                visibility=[],
                ai_behavior=behavior,
                parts=[{"type": "text", "text": text}],
                priority=_PRIORITY.get(etype, 3),
                # 队列中同 key 的消息"最新覆盖旧的"，避免堆积（官方同款做法）
                coalesce_key="ms_speak",
                # 目标角色：从调用上下文捕获；拿不到则为 None（由宿主决定）
                target_lanlan=self._lanlan or None,
            )
        except Exception:
            self.logger.exception("push_message raised")
            return
        if isinstance(receipt, dict) and receipt.get("submitted") is False:
            self.logger.warning("push rejected: %s", receipt.get("reason"))

    # ---------- LLM tool (on-demand pull) ----------

    @llm_tool(
        name="minesweeper_status",
        description=("当yui想看当前扫雷战况时，会调用（yui用）"),
        parameters={"type": "object", "properties": {}},
    )
    async def minesweeper_status(self, **kwargs):
        self._remember_target(kwargs)
        text = self._build_summary()
        return {"output": {"summary": text or "目前还没有可用的棋局信息。"}}

    @llm_tool(
        name="start_minesweeper",
        description=("点击启动扫雷。或者对yui说：“玩扫雷”，“开一局”，“扫雷启动！”（（"),
        parameters={"type": "object", "properties": {}},
        timeout=15.0,
    )
    async def start_minesweeper(self, **kwargs):
        self._remember_target(kwargs)
        if not self._start_game():
            return {"output": {"started": False, "already_running": True}}
        await asyncio.sleep(1.0)
        proc = self._game_proc
        if proc is not None and proc.poll() is not None:
            # 进程启动后很快退出 => 失败，带上 stderr 便于定位
            self._game_error = self._read_stderr_tail() or ("进程已退出（返回码 %s）" % proc.returncode)
            self.logger.error("game: start failed: %s", self._game_error)
            return {
                "output": {"started": False, "exit_code": proc.returncode},
                "is_error": True,
                "error": "启动扫雷失败：" + self._game_error,
            }
        if self._game_error:
            return {
                "output": {"started": False},
                "is_error": True,
                "error": "启动扫雷失败：" + self._game_error,
            }
        return {
            "output": {
                "started": True,
                "pid": (proc.pid if proc is not None else None),
                "message": "扫雷窗口已打开",
            }
        }

    # ---------- entries (manual testing) ----------

    @plugin_entry(
        id="test_push",
        name="Test Push",
        description="向yui发送一条测试消息，验证桥接是否可用。（测试用）",
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要发送给角色的文本",
                }
            },
            "required": ["text"],
        },
        llm_result_fields=["sent", "text"],
    )
    async def test_push(self, text="我在玩扫雷哦，我会留意棋局的。", **kwargs):
        self._remember_target(kwargs)
        self._push("test_push", text, behavior="respond")
        return Ok({"sent": True, "text": text})

    @plugin_entry(
        id="bridge_status",
        name="Bridge Status",
        description="查看桥接端口、缓存战况、最近一次事件与游戏状态。（测试用）",
        llm_result_fields=[
            "port",
            "listening",
            "summary",
            "last_event",
            "game_running",
            "game_pid",
            "game_error",
        ],
    )
    async def bridge_status(self, **kwargs):
        self._remember_target(kwargs)
        proc = self._game_proc
        if proc is not None and proc.poll() is None:
            running, pid = True, proc.pid
        else:
            running, pid = False, None
        return Ok(
            {
                "port": self._port,
                "listening": self._sock is not None,
                "summary": self._build_summary(),
                "last_event": self._last_event,
                "game_running": running,
                "game_pid": pid,
                "game_error": self._game_error,
            }
        )
