"""心情精力状态插件 (Mood State)

为猫娘注入持久的心情/精力双维度内在状态（移植自 Open-LLM-VTuber）：

- 状态持久化在 data/state.json（{enabled, mood, energy, updated_at, eval_note}）
- 按 0~100 分每 10 分一档，从 prompts/state_prompts.json 选取档位文案，
  通过 push_message(ai_behavior="read", visibility=[]) 静默注入对话上下文
- LLM 可通过 llm_tool 查询(get_my_current_state)与自评自调(update_my_state)
- 主人可通过插件入口手动加减分（对应原项目的 gauge 按钮）
- 定时任务：向基线缓慢漂移 + 周期性重注入，防止状态被上下文遗忘

与原项目的映射：
    memory_store/state.json        → data/state.json (data_path)
    prompts/state_prompts.yaml     → prompts/state_prompts.json
    state_prompt.build_state_prompt→ core.render_state_block + push_message
    启动器 LLM 评估                → evaluate_now 启发式评估 + LLM 自评工具
    gauge ±1/±5 手动调整           → adjust_state 入口
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

from .core import (
    DIMENSIONS,
    clamp,
    clamp_delta,
    drift_toward,
    heuristic_eval,
    load_prompts_file,
    load_state_file,
    normalize_state,
    pick,
    refresh_state_entry,
    render_state_block,
    save_state_file,
)
from .gui import start_gui_server

_DEFAULT_CFG: Dict[str, Any] = {
    "enabled": True,
    "initial_mood": 50,
    "initial_energy": 50,
    "baseline_mood": 50,
    "baseline_energy": 50,
    "drift_per_hour": 2.0,
    "push_refresh_minutes": 30,
    "push_on_change": True,
    "main_server_url": "http://127.0.0.1:48911",
    "gui_enabled": True,
    "gui_port": 48930,
    "gui_auto_open": True,
    "gui_python": "",
}


@neko_plugin
class MoodStatePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._lock = threading.Lock()
        self._cfg: Dict[str, Any] = dict(_DEFAULT_CFG)
        self._prompts_cache: tuple = (0.0, {})
        self._last_push_ts: float = 0.0
        self._loop = None
        self._gui = None
        self._gui_proc = None

    # ---------- 内部工具 ----------

    def _state_path(self) -> Path:
        return self.data_path("state.json")

    def _prompts_path(self) -> Path:
        # Steam 版 SDK 只有 config_dir；源码新版才有 plugin_dir；最后用 __file__ 兜底
        base = getattr(self, "config_dir", None) or getattr(self, "plugin_dir", None)
        if base is None:
            base = Path(__file__).resolve().parent
        return Path(base) / "prompts" / "state_prompts.json"

    def _load_prompts(self) -> Dict:
        path = self._prompts_path()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if mtime != self._prompts_cache[0]:
            self._prompts_cache = (mtime, load_prompts_file(path))
        return self._prompts_cache[1]

    def _load_state(self) -> Dict:
        return normalize_state(
            load_state_file(self._state_path()),
            {
                "enabled": bool(self._cfg.get("enabled", True)),
                "mood": self._cfg.get("initial_mood", 50),
                "energy": self._cfg.get("initial_energy", 50),
            },
        )

    def _save_state(self, state: Dict) -> Dict:
        return save_state_file(self._state_path(), state)

    def _compose_text(self, state: Dict) -> str:
        """渲染状态块全文；未启用时返回空串。"""
        text = render_state_block(state, self._load_prompts())
        if not text:
            return ""
        note = state.get("eval_note")
        if note:
            text += f"\n- 状态备注:{note}"
        text += "\n（若对话中发生明显影响你情绪或精力的事,可调用 update_my_state 工具自行调整状态。）"
        return text

    def _push_state(self, state: Dict, priority: int = 3) -> bool:
        """把状态块静默推入当前会话（AI 可读，不进聊天窗），立即生效。"""
        text = self._compose_text(state)
        if not text:
            return False
        try:
            result = self.push_message(
                source="mood_state",
                visibility=[],
                ai_behavior="read",
                parts=[{"type": "text", "text": text}],
                priority=priority,
                metadata={
                    "event_type": "mood_state_update",
                    "mood": state.get("mood"),
                    "energy": state.get("energy"),
                },
            )
            submitted = (
                bool(result.get("submitted")) if isinstance(result, dict) else False
            )
            if submitted:
                self._last_push_ts = time.time()
            else:
                self.logger.warning("mood_state push rejected: {}", result)
            return submitted
        except Exception as exc:
            self.logger.warning("mood_state push failed: {}", exc)
            return False

    # ---------- 近期记忆注入（每轮对话上下文渲染） ----------

    def _http_json(
        self, url: str, body: Dict | None = None, timeout: float = 5.0
    ) -> Any:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def _inject_recent(self, state: Dict) -> bool:
        """把状态块伪装成最新的系统记忆写入 recent.json。

        走主服务官方 /api/memory/recent_file/save 路径（记忆浏览器同款），
        recent 层会被渲染进每轮新对话上下文，实现"每次发送对话都带上状态"。
        """
        base = str(self._cfg.get("main_server_url", "")).rstrip("/")
        if not base:
            return False
        text = self._compose_text(state)

        def _do() -> bool:
            try:
                files = self._http_json(f"{base}/api/memory/recent_files").get(
                    "files", []
                )
            except Exception as exc:
                self.logger.warning("recent_files list failed: {}", exc)
                return False
            ok_any = False
            for fname in files:
                for attempt in range(2):  # 409 冲突时重读重试一次
                    try:
                        q = urllib.parse.quote(fname)
                        got = self._http_json(
                            f"{base}/api/memory/recent_file?filename={q}"
                        )
                        chat = json.loads(got.get("content") or "[]")
                        new_chat = refresh_state_entry(chat, text)
                        if new_chat == chat and not text:
                            ok_any = True
                            break  # 无旧条目且停用，无需写
                        self._http_json(
                            f"{base}/api/memory/recent_file/save",
                            {
                                "filename": fname,
                                "fingerprint": got.get("fingerprint"),
                                "identity_token": got.get("identity_token"),
                                "chat": new_chat,
                            },
                        )
                        ok_any = True
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code == 409 and attempt == 0:
                            continue
                        self.logger.warning("recent inject {} failed: {}", fname, exc)
                        break
                    except Exception as exc:
                        self.logger.warning("recent inject {} failed: {}", fname, exc)
                        break
            return ok_any

        try:
            return await asyncio.to_thread(_do)
        except Exception as exc:
            self.logger.warning("recent inject failed: {}", exc)
            return False

    # ---------- GUI（本地网页）回调 ----------

    def _schedule_inject(self, state: Dict) -> None:
        """GUI 线程里把记忆注入调度回插件主事件循环。"""
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._inject_recent(state), loop)

    def gui_state_snapshot(self) -> Dict:
        state = self._load_state()
        prompts = self._load_prompts()
        return {
            "enabled": state.get("enabled", False),
            "mood": state.get("mood", 50),
            "energy": state.get("energy", 50),
            "mood_text": pick(prompts, "mood", state.get("mood", 50)),
            "energy_text": pick(prompts, "energy", state.get("energy", 50)),
            "eval_note": state.get("eval_note", ""),
        }

    def gui_adjust(self, dimension: str, delta: int) -> Dict:
        state = self._apply_delta(
            **{f"{dimension}_delta": clamp_delta(delta)}, note="手动调整(GUI)"
        )
        self._schedule_inject(state)
        return self.gui_state_snapshot()

    def gui_set_enabled(self, enabled: bool) -> Dict:
        with self._lock:
            state = self._load_state()
            state["enabled"] = enabled
            state = self._save_state(state)
        if enabled:
            self._push_state(state)
        self._schedule_inject(state)
        return self.gui_state_snapshot()

    # ---------- 原生窗口子进程 ----------

    def _probe_pyside(self, py: str) -> bool:
        try:
            r = subprocess.run(
                [py, "-c", "import PySide6.QtWidgets"],
                capture_output=True,
                timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _gui_python(self) -> str | None:
        """解析能跑 PySide6 窗口的解释器：配置 > 非冻结自身 > PATH，逐个探测。"""
        cfg_py = str(self._cfg.get("gui_python", "") or "").strip()
        candidates: list = []
        if cfg_py and Path(cfg_py).exists():
            candidates.append(cfg_py)
        if not getattr(sys, "frozen", False):
            candidates.append(sys.executable)
        for name in ("pythonw.exe", "pythonw", "python.exe", "python"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        results = {}
        for py in dict.fromkeys(candidates):
            results[py] = self._probe_pyside(py)
        self.logger.info("GUI python candidates: {}", results)
        for py, ok in results.items():
            if ok:
                return py
        return None

    def _launch_gui(self) -> None:
        if self._gui is None:
            return
        if self._gui_proc is not None and self._gui_proc.poll() is None:
            return  # 已有一个活着的窗口
        py = self._gui_python()
        if not py:
            self.logger.warning(
                "no python with PySide6 available for native GUI "
                "(set [mood_state] gui_python in plugin config)"
            )
            return
        script = Path(__file__).resolve().parent / "gui_window.py"
        try:
            logf = open(self.data_path("gui_window.log"), "ab")
            self._gui_proc = subprocess.Popen(
                [py, str(script), "--url", self._gui.url],
                stdout=logf,
                stderr=subprocess.STDOUT,
            )
            self.logger.info("native GUI launched pid={} py={}", self._gui_proc.pid, py)
        except Exception as exc:
            self.logger.warning("native GUI launch failed: {}", exc)

    def _apply_delta(
        self,
        mood_delta: int = 0,
        energy_delta: int = 0,
        note: str | None = None,
        push: bool = True,
    ) -> Dict:
        with self._lock:
            state = self._load_state()
            state["mood"] = clamp(state.get("mood", 50) + clamp_delta(mood_delta))
            state["energy"] = clamp(state.get("energy", 50) + clamp_delta(energy_delta))
            if note:
                state["eval_note"] = note
            state = self._save_state(state)
        if push and state.get("enabled") and self._cfg.get("push_on_change", True):
            self._push_state(state)
        return state

    async def _read_business_cfg(self) -> Dict[str, Any]:
        try:
            cfg = await self.config.dump(timeout=5.0)
        except Exception as exc:
            self.logger.warning("config dump failed, using defaults: {}", exc)
            cfg = {}
        cfg = cfg if isinstance(cfg, dict) else {}
        biz = cfg.get("mood_state") if isinstance(cfg.get("mood_state"), dict) else {}
        merged = dict(_DEFAULT_CFG)
        for k in merged:
            if k in biz:
                merged[k] = biz[k]
        return merged

    # ---------- 生命周期 ----------

    @lifecycle(id="startup")
    async def startup(self, **_):
        self._cfg = await self._read_business_cfg()
        self._loop = asyncio.get_running_loop()
        if self._cfg.get("gui_enabled", True) and self._gui is None:
            try:
                self._gui = start_gui_server(
                    self, int(self._cfg.get("gui_port", 48930))
                )
                self.logger.info("mood_state GUI API at {}", self._gui.url)
                if self._cfg.get("gui_auto_open", True):
                    self._launch_gui()
            except Exception as exc:
                self.logger.warning("GUI start failed: {}", exc)
        with self._lock:
            existing = load_state_file(self._state_path())
            if existing is None:
                state = self._save_state(self._load_state())
                self.logger.info("mood_state initialized: {}", state)
            else:
                state = normalize_state(existing)
        if state.get("enabled") and self._cfg.get("enabled", True):
            self._push_state(state)
            await self._inject_recent(state)
        return Ok({"status": "running", "state": state})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        if self._gui_proc is not None and self._gui_proc.poll() is None:
            self._gui_proc.terminate()
        self._gui_proc = None
        if self._gui is not None:
            self._gui.stop()
            self._gui = None
        self.logger.info("mood_state shutdown")
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        self._cfg = await self._read_business_cfg()
        return Ok({"status": "config_reloaded"})

    # ---------- 定时漂移 + 上下文刷新 ----------

    @timer_interval(
        id="state_drift", seconds=1800, name="状态漂移与上下文刷新"
    )
    async def state_drift(self, **_):
        try:
            cfg = self._cfg
            if not cfg.get("enabled", True):
                return Ok({"skipped": "plugin disabled"})
            with self._lock:
                state = self._load_state()
                if not state.get("enabled"):
                    return Ok({"skipped": "state disabled"})
                new_state, changed = drift_toward(
                    state,
                    float(cfg.get("baseline_mood", 50)),
                    float(cfg.get("baseline_energy", 50)),
                    float(cfg.get("drift_per_hour", 0)),
                )
                if changed:
                    new_state = self._save_state(new_state)
            refresh_due = (
                time.time() - self._last_push_ts
                >= float(cfg.get("push_refresh_minutes", 30)) * 60
            )
            if changed or refresh_due:
                self._push_state(new_state)
                await self._inject_recent(new_state)
            return Ok({"drifted": changed, "pushed": changed or refresh_due})
        except Exception as exc:
            self.logger.warning("state_drift tick failed: {}", exc)
            return Ok({"skipped": "error"})

    # ---------- LLM 工具（对话中自评自调） ----------

    @llm_tool(
        name="get_my_current_state",
        description=(
            "查询你当前的心情与精力状态(0~100)。"
            "当你不确定自己当前状态、或需要让语气贴合状态时调用。"
        ),
        parameters={"type": "object", "properties": {}},
    )
    async def get_my_current_state(self):
        state = self._load_state()
        prompts = self._load_prompts()
        return {
            "enabled": state.get("enabled", False),
            "mood": state.get("mood", 50),
            "energy": state.get("energy", 50),
            "mood_text": pick(prompts, "mood", state.get("mood", 50)),
            "energy_text": pick(prompts, "energy", state.get("energy", 50)),
            "eval_note": state.get("eval_note", ""),
        }

    @llm_tool(
        name="update_my_state",
        description=(
            "调整你自己的心情/精力状态(增量 -100~100,会自动夹取到 0~100)。"
            "当对话中发生明显影响你情绪或精力的事时调用,"
            "例如:被夸奖/聊得开心→mood_delta 为正;被骂/听到难过的事→mood_delta 为负;"
            "长时间高强度聊天→energy_delta 为负;兴奋的事→energy_delta 为正。"
            "调整后你的语气应自然贴合新状态,不需要向用户复述数值。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "mood_delta": {
                    "type": "integer",
                    "description": "心情变化量,正数变好,负数变差,建议幅度 2~15",
                },
                "energy_delta": {
                    "type": "integer",
                    "description": "精力变化量,正数更来劲,负数更疲惫,建议幅度 2~15",
                },
                "reason": {
                    "type": "string",
                    "description": "一句话说明为什么调整,例如'被主人夸了'",
                },
            },
        },
    )
    async def update_my_state(
        self, *, mood_delta: int = 0, energy_delta: int = 0, reason: str = ""
    ):
        state = self._apply_delta(
            mood_delta=mood_delta,
            energy_delta=energy_delta,
            note=(reason or None),
        )
        await self._inject_recent(state)
        return {
            "mood": state.get("mood"),
            "energy": state.get("energy"),
            "reason": reason or "",
        }

    # ---------- 插件入口（插件管理器 / Agent 调度） ----------

    @plugin_entry(
        id="get_state",
        name="查看心情精力状态",
        description="查看猫娘当前的心情与精力分数、档位文案与备注。",
    )
    async def get_state(self, **_):
        state = self._load_state()
        prompts = self._load_prompts()
        return Ok(
            {
                "state": state,
                "mood_text": pick(prompts, "mood", state.get("mood", 50)),
                "energy_text": pick(prompts, "energy", state.get("energy", 50)),
            }
        )

    @plugin_entry(
        id="adjust_state",
        name="手动调整心情/精力",
        description=(
            "手动加减心情或精力分数(对应原项目 gauge 的 ±1/±5 按钮)。"
            "dimension 取值 'mood' 或 'energy';delta 为增量,如 +1 / -5。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "enum": list(DIMENSIONS),
                    "description": "要调整的维度:mood=心情,energy=精力",
                },
                "delta": {
                    "type": "integer",
                    "description": "增量(-100~100),如 1、-5",
                },
            },
            "required": ["dimension", "delta"],
        },
    )
    async def adjust_state(self, dimension: str, delta: int, **_):
        if dimension not in DIMENSIONS:
            return Err(
                SdkError(f"dimension must be one of {DIMENSIONS}, got {dimension!r}")
            )
        state = self._apply_delta(
            **{f"{dimension}_delta": clamp_delta(delta)},
            note="手动调整",
        )
        await self._inject_recent(state)
        return Ok({"state": state})

    @plugin_entry(
        id="set_enabled",
        name="启用/停用状态系统",
        description="启用或停用心情精力状态注入。停用后不再向对话注入状态块。",
        input_schema={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
    )
    async def set_enabled(self, enabled: bool, **_):
        with self._lock:
            state = self._load_state()
            state["enabled"] = bool(enabled)
            state = self._save_state(state)
        if state["enabled"]:
            self._push_state(state)
        await self._inject_recent(state)  # 停用时负责剔除旧状态块
        return Ok({"state": state})

    @plugin_entry(
        id="open_gui",
        name="打开状态窗口",
        description="重新打开被关闭的心情/精力原生调整窗口。",
    )
    async def open_gui(self, **_):
        self._launch_gui()
        return Ok({"opened": self._gui_proc is not None})

    @plugin_entry(
        id="evaluate_now",
        name="立刻重新评估状态",
        description=(
            "基于最近一小时的对话内容做一次启发式评估并更新心情/精力"
            "（正面情绪提升心情、负面情绪拉低心情、兴奋点提升精力、长消息消耗精力）。"
        ),
    )
    async def evaluate_now(self, **_):
        texts = []
        try:
            records = await self.bus.memory.get(bucket_id="default", limit=20)
            for r in records or []:
                if isinstance(r, dict):
                    t = r.get("text") or r.get("content") or r.get("utterance") or ""
                else:
                    t = str(r)
                if t:
                    texts.append(t)
        except Exception as exc:
            self.logger.warning("bus.memory read failed: {}", exc)
        with self._lock:
            state = self._load_state()
        mood_delta, energy_delta, note = heuristic_eval(texts, state)
        new_state = self._apply_delta(mood_delta, energy_delta, note=note)
        await self._inject_recent(new_state)
        return Ok(
            {
                "evaluated_messages": len(texts),
                "mood_delta": mood_delta,
                "energy_delta": energy_delta,
                "note": note,
                "state": new_state,
            }
        )
