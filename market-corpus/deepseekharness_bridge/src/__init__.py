"""DeepSeek Harness bridge plugin for N.E.K.O.

Drives a local DeepSeek Harness (``dsh``) process so the catgirl can hand it
long, code-shaped tasks and read the result back into the conversation.

The harness is spawned through its Python SDK (``deepseek_harness``) when that
package is importable, and falls back to invoking the ``dsh`` CLI directly when
it is not. Both paths write into the same run log, so a failed SDK install is
visible rather than silent.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

# Model catalog the running DeepSeek Harness ships, read from its own source
# (@deepseek-ai/dsh-llm-deepseek DEFAULT_MODELS in 0.2.1-rc.2). The headless
# profile disables the account provider, so the online /models listing is not
# available and this built-in catalog is the source of truth.
#
# Keep in sync when DSH ships new models; an unknown id fails at request time
# with MODEL_UNSUPPORTED.
_MODEL_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "deepseek-flash",
        "name": "DeepSeek-V4.1-Flash",
        "description": "默认选择：快且支持图像输入，适合大多数任务。",
    },
    {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek-V4-Pro",
        "description": "更强的编码与推理，适合复杂任务，成本更高。",
    },
)

_DEFAULT_MODEL = "deepseek-flash"

# Model ids the catalog offers, for config validation and the panel dropdown.
_KNOWN_MODELS = tuple(entry["id"] for entry in _MODEL_CATALOG)

# Reasoning effort levels DSH accepts on agent-default-model. The Messages path
# (dsh-llm-deepseek) accepts ONLY off / low / high / max — "minimal" and
# "medium" are not valid and the run dies with UNSUPPORTED_REASONING_EFFORT,
# which is exactly the v0.2.x log line. Keep this list in that exact set.
_EFFORT_LEVELS: tuple[dict[str, str], ...] = (
    {"id": "off", "name": "关闭", "description": "不推理，最快最省，质量最低。"},
    {"id": "low", "name": "低", "description": "轻量推理，适合简单任务。"},
    {"id": "high", "name": "高", "description": "更深入推理，耗时更长。"},
    {"id": "max", "name": "最高", "description": "默认，最强推理，最慢最贵。"},
)

_DEFAULT_EFFORT = "max"

_KNOWN_EFFORTS = tuple(entry["id"] for entry in _EFFORT_LEVELS)

# DSH sandbox modes (SANDBOX_MODES in @deepseek-ai/dsh-sandbox-policy).
# `workspace-write` is the default here: it lets a task create and edit files
# under the workspace root, which is what most tasks need. `danger-full-access`
# removes the boundary entirely but a one-shot CLI run has no approval channel
# to service, so it is not a safe default.
_SANDBOX_MODES: tuple[dict[str, str], ...] = (
    {
        "id": "read-only",
        "name": "只读",
        "description": "不能改任何文件，只能查看和分析。",
    },
    {
        "id": "workspace-write",
        "name": "工作区可写",
        "description": "默认。可读写工作区目录内的文件，其他位置仍只读。",
    },
    {
        "id": "danger-full-access",
        "name": "完全访问",
        "description": "不限制文件位置。CLI 一次性运行没有审批通道，可能直接失败。",
    },
)

_DEFAULT_SANDBOX = "workspace-write"

_KNOWN_SANDBOXES = tuple(entry["id"] for entry in _SANDBOX_MODES)


def _truncate(text: str, limit: int = 6000) -> str:
    """Keep tool payloads small enough for a prompt without hiding that they were cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text) - limit} chars omitted]"


@neko_plugin
class DeepseekharnessBridgePlugin(NekoPluginBase):
    """Expose a local DeepSeek Harness to N.E.K.O. as LLM tools."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._sdk_available: bool | None = None
        self._dsh_executable: str | None = None
        # Set when the CLI is the one bundled inside the DSH desktop app:
        # (exe, cli.js) — that pair is how it must be launched.
        self._dsh_desktop_launcher: tuple[str, str] | None = None
        self._runs: dict[str, dict[str, Any]] = {}
        self._dsh_bridge_state: str = "unknown"
        # Set when the user picks a model or reasoning effort in the panel. Takes
        # precedence over config so the choice applies immediately rather than
        # whenever the config read next happens to catch up.
        self._model_override: str | None = None
        self._effort_override: str | None = None
        self._sandbox_override: str | None = None
        # Warnings already emitted, so a re-read of the same stale config does
        # not repeat the line on every panel refresh.
        self._warned: set[str] = set()
        self._lock = asyncio.Lock()

    # --- lifecycle -----------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        # Resolve the harness once at startup so the first tool call is not
        # the thing that discovers a broken install.
        self._sdk_available = self._probe_sdk()
        self._dsh_executable = self._resolve_dsh_executable()

        # Bootstrap the other direction: make sure the DSH side of the bridge
        # is installed, so starting this plugin is the only step a user needs.
        dsh_state = await self._ensure_dsh_bridge_installed()

        self.logger.info(
            "deepseekharness_bridge ready (sdk=%s, dsh=%s, dsh_bridge=%s)",
            "available" if self._sdk_available else "missing",
            self._dsh_executable or "not found",
            dsh_state,
        )
        return Ok({
            "status": "ready",
            "sdk": self._sdk_available,
            "dsh_bridge": dsh_state,
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self.logger.info(
            "deepseekharness_bridge stopping after %d run(s)", len(self._runs)
        )
        return Ok({"status": "stopped", "runs": len(self._runs)})

    # --- hosted UI -----------------------------------------------------

    @ui.context(id="dashboard")
    async def dashboard(self):
        """面板状态：harness 可用性、当前配置摘要、最近运行记录。

        只回传元数据（会话 ID、长度、耗时、模式），不回传任务正文或模型
        回复——面板可能被围观，日志同理。
        """
        # Newest first: the panel lists every task with its status, text and
        # output, and the most recent run is what a user looks for first.
        runs = [
            {
                "session_id": record["session_id"],
                "task": record.get("task", ""),
                "task_chars": record.get("task_chars", 0),
                "status": record.get("status", "ok" if record.get("ok") else "failed"),
                "elapsed_seconds": record.get("elapsed_seconds", 0),
                "mode": record.get("mode", "cli"),
                "model": record.get("model", ""),
                "output": record.get("output", ""),
                "error": record.get("error", ""),
            }
            for record in reversed(list(self._runs.values()))
        ]

        return {
            "harness": {
                "sdk_available": self._sdk_available,
                "dsh_executable": self._dsh_executable,
                "ready": bool(self._sdk_available or self._dsh_executable),
            },
            "dsh_bridge": self._dsh_bridge_state,
            "settings": {
                "model": str(await self._cfg("model", _DEFAULT_MODEL)),
                "profile": str(await self._cfg("profile", "headless")),
                "reasoning_effort": str(await self._cfg("reasoning_effort", _DEFAULT_EFFORT)),
                "sandbox_mode": str(await self._cfg("sandbox_mode", _DEFAULT_SANDBOX)),
                "task_timeout": float(await self._cfg("task_timeout", 600)),
                "workspace": str(await self._workspace()),
                "repo_path": str(await self._cfg("repo_path", "")),
            },
            # The panel renders this as a dropdown, so a user picks a model
            # instead of typing an id that may not exist.
            "models": [dict(entry) for entry in _MODEL_CATALOG],
            "efforts": [dict(entry) for entry in _EFFORT_LEVELS],
            "sandboxes": [dict(entry) for entry in _SANDBOX_MODES],
            "runs": runs,
            "runs_total": len(self._runs),
            "running_count": sum(1 for r in runs if r["status"] == "running"),
        }

    @ui.action(refresh_context=True)
    @plugin_entry(
        id="panel_refresh",
        name="刷新面板",
        description="重新读取 harness 可用性与运行记录。",
    )
    async def panel_refresh(self, **_):
        # 状态由 @ui.context 现取，这里只需要一个成功的信号让面板重渲染。
        return Ok({"status": "refreshed"})

    @ui.action(refresh_context=True)
    @plugin_entry(
        id="panel_run_task",
        name="运行任务",
        description="把任务交给本地 DeepSeek Harness 执行。",
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要 harness 执行的任务，写清楚目标和验收标准",
                }
            },
            "required": ["task"],
        },
        timeout=900.0,
    )
    async def panel_run_task(self, task: str, **_):
        """面板专用入口：等任务跑完，直接把结果返回。

        Awaiting the run is the point: the operator asked for the result, not a
        ticket number. The entry carries a generous timeout so a long task does
        not get cut off, and the run is still recorded through its lifecycle so
        the task list shows what happened.
        """
        if not task or not task.strip():
            return Err(SdkError("任务不能为空"))

        session = f"neko-{int(time.time())}"
        task_text = task.strip()

        try:
            outcome = await self._invoke_harness(task_text, session)
        except Exception as exc:
            # The record is already marked failed with the reason by
            # _invoke_harness; surface it rather than an opaque transport error.
            self.logger.warning("run %s failed: %s", session, exc)
            return Err(SdkError(str(exc)))

        return Ok({
            "ok": True,
            "session_id": session,
            "status": outcome.get("status", "ok"),
            "elapsed_seconds": outcome.get("elapsed_seconds"),
            "response": outcome.get("output", ""),
        })

    @ui.action(refresh_context=True)
    @plugin_entry(
        id="panel_set_model",
        name="切换模型",
        description="选择 DeepSeek Harness 执行任务时使用的模型。",
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "模型 ID",
                    "enum": list(_KNOWN_MODELS),
                }
            },
            "required": ["model"],
        },
    )
    async def panel_set_model(self, model: str, **_):
        """Persist the chosen model so later runs use it.

        Also updates the in-process value right away: the config write is
        asynchronous and the dashboard context re-reads it on refresh, so
        without this the button looks like it did nothing until something else
        happens to re-read the config.
        """
        if model not in _KNOWN_MODELS:
            return Err(SdkError(
                f"未知模型 {model}；可用：{', '.join(_KNOWN_MODELS)}"
            ))

        try:
            await self.config.update({"deepseekharness_bridge.model": model})
        except Exception as exc:
            # The in-process value still changes, so the choice takes effect
            # for this session even if persistence failed.
            self.logger.warning("cannot persist model choice: %s", exc)

        self._model_override = model
        self.logger.info("model switched to %s", model)
        return Ok({"ok": True, "model": model})

    @ui.action(refresh_context=True)
    @plugin_entry(
        id="panel_set_effort",
        name="思考强度",
        description="选择 DeepSeek Harness 的推理投入程度。",
        input_schema={
            "type": "object",
            "properties": {
                "effort": {
                    "type": "string",
                    "description": "推理投入程度",
                    "enum": list(_KNOWN_EFFORTS),
                }
            },
            "required": ["effort"],
        },
    )
    async def panel_set_effort(self, effort: str, **_):
        """Persist the reasoning effort for later runs.

        Applied through the same patch that carries the model: the headless CLI
        has no flag for it, so the profile's agent-default-model row is the only
        place it can be set.
        """
        if effort not in _KNOWN_EFFORTS:
            return Err(SdkError(
                f"未知思考强度 {effort}；可用：{', '.join(_KNOWN_EFFORTS)}"
            ))

        try:
            await self.config.update(
                {"deepseekharness_bridge.reasoning_effort": effort}
            )
        except Exception as exc:
            self.logger.warning("cannot persist reasoning effort: %s", exc)

        self._effort_override = effort
        self.logger.info("reasoning effort switched to %s", effort)
        return Ok({"ok": True, "effort": effort})

    @ui.action(refresh_context=True)
    @plugin_entry(
        id="panel_set_sandbox",
        name="文件权限",
        description="选择 harness 能读写哪些文件。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "沙箱模式",
                    "enum": list(_KNOWN_SANDBOXES),
                }
            },
            "required": ["mode"],
        },
    )
    async def panel_set_sandbox(self, mode: str, **_):
        """Persist the sandbox mode for later runs.

        This is what lets a task create files at all: headless inherits
        dsh-base's read-only default, which denies every write — including
        inside the workspace. ``workspace-write`` is the useful middle ground,
        and it needs no approval channel, which a one-shot CLI run cannot
        service anyway.
        """
        if mode not in _KNOWN_SANDBOXES:
            return Err(SdkError(
                f"未知沙箱模式 {mode}；可用：{', '.join(_KNOWN_SANDBOXES)}"
            ))

        try:
            await self.config.update(
                {"deepseekharness_bridge.sandbox_mode": mode}
            )
        except Exception as exc:
            self.logger.warning("cannot persist sandbox mode: %s", exc)

        self._sandbox_override = mode
        self.logger.info("sandbox mode switched to %s", mode)
        return Ok({"ok": True, "mode": mode})

    # --- DSH side bootstrap --------------------------------------------

    async def _ensure_dsh_bridge_installed(self) -> str:
        """Make sure the DSH side of the bridge is ready to load.

        The N.E.K.O. plugin and the dsh bundle ship together, so starting this
        plugin is the only step a user should need. This checks the DSH desktop
        profile and installs the bundle when it is missing or incomplete.

        Returns a short state string for logs and the panel: ``installed``,
        ``repaired``, ``installed-fresh``, or a reason it was skipped
        (``no-dsh``, ``failed``).
        """
        try:
            return await asyncio.to_thread(self._ensure_dsh_bridge_installed_sync)
        except Exception as exc:
            # A failure here must never stop the plugin from loading: the
            # N.E.K.O. -> DSH direction works without the DSH-side bundle.
            self.logger.warning("dsh bridge bootstrap failed: %s", exc)
            return "failed"

    def _find_dsh_install(self) -> Path | None:
        """Locate a DeepSeek Harness desktop installation."""
        # 1. Explicit config wins.
        configured = self.metadata.get("dsh_install_path") if self.metadata else None
        if configured:
            candidate = Path(str(configured)).expanduser()
            if (candidate / "DeepSeek Harness.exe").exists():
                return candidate

        # 2. Standard install locations.
        roots = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DeepSeek Harness",
            Path("D:/DeepSeek Harness"),
            Path(os.environ.get("PROGRAMFILES", "")) / "DeepSeek Harness",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "DeepSeek Harness",
        ]
        for root in roots:
            try:
                if (root / "DeepSeek Harness.exe").exists():
                    return root
            except OSError:
                continue

        # 3. Fall back to the running process, if any.
        exe = self._find_dsh_process_exe()
        if exe:
            return exe.parent

        return None

    def _find_dsh_process_exe(self) -> Path | None:
        """Find the DSH desktop exe from the process table (Windows only)."""
        if sys.platform != "win32":
            return None
        try:
            import subprocess

            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq DeepSeek Harness.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if "DeepSeek Harness.exe" not in out.stdout:
                return None
        except Exception:
            return None

        # tasklist gives the image name, not the path. Ask WMI instead, which
        # does report ExecutablePath.
        try:
            import subprocess

            ps = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-Process -Name 'DeepSeek Harness' | Select-Object -First 1).Path",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            line = ps.stdout.strip().splitlines()[0] if ps.stdout.strip() else ""
            if line and Path(line).exists():
                return Path(line)
        except Exception:
            pass
        return None

    def _dsh_bundle_source(self) -> Path | None:
        """Where the dsh-plugin-neko bundle lives.

        The embedded copy under ``bundled/dsh-plugin-neko`` is the primary
        source: it ships inside this plugin, so the bootstrap works no matter
        where the user installed the N.E.K.O. plugin from. A sibling checkout
        is checked as a fallback for source-tree development.
        """
        candidates = [
            self.plugin_dir / "bundled" / "dsh-plugin-neko",
            self.plugin_dir.parent.parent.parent / "dsh-plugin-neko",
            self.plugin_dir.parent.parent.parent.parent / "dsh-plugin-neko",
        ]
        for candidate in candidates:
            if (candidate / "package.json").exists() and (candidate / "lib" / "index.js").exists():
                return candidate
        return None

    def _ensure_dsh_bridge_installed_sync(self) -> str:
        """Synchronous body of the DSH-side bootstrap."""
        dsh = self._find_dsh_install()
        if dsh is None:
            self._dsh_bridge_state = "no-dsh"
            self.logger.info("no DeepSeek Harness installation found; DSH-side bridge skipped")
            return "no-dsh"

        source = self._dsh_bundle_source()
        if source is None:
            self._dsh_bridge_state = "no-bundle"
            self.logger.warning(
                "dsh-plugin-neko bundle not found beside the plugin; DSH-side bridge skipped"
            )
            return "no-bundle"

        home = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
        profile_dir = home / "profiles" / "desktop"
        target = profile_dir / "node_modules" / "dsh-plugin-neko"

        manifest_ok = (target / "package.json").exists()
        entry_ok = (target / "lib" / "index.js").exists()
        registered = self._profile_lists_bundle(profile_dir)

        if manifest_ok and entry_ok and registered:
            self._dsh_bridge_state = "installed"
            return "installed"

        # Copy the bundle into the profile. A copy (not a symlink) keeps the
        # install self-contained: the profile survives the source tree moving.
        if not (manifest_ok and entry_ok):
            target.mkdir(parents=True, exist_ok=True)
            for name in ("package.json", "cordis.patch.yml", "README.md"):
                src = source / name
                if src.exists():
                    shutil.copyfile(src, target / name)
            lib_src = source / "lib"
            if lib_src.exists():
                shutil.copytree(lib_src, target / "lib", dirs_exist_ok=True)
            self.logger.info("copied dsh-plugin-neko into %s", target)

        if not registered:
            self._register_bundle_in_profile(profile_dir)

        # Something was already there: this run fixed it up rather than doing
        # a clean install.
        state = "repaired" if (manifest_ok or entry_ok or registered) else "installed-fresh"
        self._dsh_bridge_state = state
        return state

    def _profile_lists_bundle(self, profile_dir: Path) -> bool:
        """Whether the profile manifest already lists the bundle."""
        import json

        manifest = profile_dir / "package.json"
        if not manifest.exists():
            return False
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        bundles = data.get("dsh", {}).get("profile", {}).get("bundles", [])
        return "dsh-plugin-neko" in bundles

    def _register_bundle_in_profile(self, profile_dir: Path) -> None:
        """Add the bundle to the profile manifest and its dependency list."""
        import json

        manifest = profile_dir / "package.json"
        if not manifest.exists():
            self.logger.warning("DSH desktop profile manifest not found at %s", manifest)
            return

        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.logger.warning("cannot read DSH profile manifest: %s", exc)
            return

        dsh_section = data.setdefault("dsh", {}).setdefault("profile", {})
        bundles = dsh_section.setdefault("bundles", [])
        if "dsh-plugin-neko" not in bundles:
            bundles.append("dsh-plugin-neko")

        deps = data.setdefault("dependencies", {})
        deps["dsh-plugin-neko"] = "file:" + str(
            (profile_dir / "node_modules" / "dsh-plugin-neko").as_posix()
        )

        manifest.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.logger.info("registered dsh-plugin-neko in %s", manifest)

    # --- configuration -------------------------------------------------
    async def _cfg(self, key: str, default: Any = None) -> Any:
        """Read one dotted config value, tolerating an unreadable host config.

        Stale values from earlier versions are corrected on read. The runtime
        config outlives plugin upgrades, so a value that was valid once keeps
        shadowing the manifest default — which is how a run ended up with
        `profile=sdk-minimal` and `model=deepseek-v4-flash`, neither of which
        the headless CLI accepts. Correcting here fixes an existing install
        without the user having to edit config by hand.

        An in-process override (set from the panel) wins over everything, so a
        choice applies on the very next read.
        """
        if key == "model" and self._model_override:
            return self._model_override
        if key == "reasoning_effort" and self._effort_override:
            return self._effort_override
        if key == "sandbox_mode" and self._sandbox_override:
            return self._sandbox_override

        try:
            value = await self.config.get(f"deepseekharness_bridge.{key}", default=default)
        except Exception as exc:  # host config unavailable — keep defaults
            self.logger.warning("config read failed for %s: %s", key, exc)
            return default

        if key == "profile" and value == "sdk-minimal":
            self._warn_once(
                "profile-stale",
                "profile 'sdk-minimal' is no longer supported; using 'headless'",
            )
            return "headless"

        if key == "model" and value == "deepseek-v4-flash":
            self._warn_once(
                "model-stale",
                "model 'deepseek-v4-flash' does not exist; using 'deepseek-flash'",
            )
            return _DEFAULT_MODEL

        return value

    def _warn_once(self, key: str, message: str) -> None:
        """Log a warning the first time only.

        The dashboard context re-reads config on every panel refresh, so an
        unconditional warning here repeats on each refresh and drowns the log.
        """
        if key in self._warned:
            return
        self._warned.add(key)
        self.logger.warning(message)

    async def _log_dir(self) -> Path:
        configured = await self._cfg("log_dir", "")
        if configured:
            path = Path(str(configured)).expanduser()
        else:
            path = self.storage_dir / "runs"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.logger.warning("cannot create run log dir %s: %s", path, exc)
        return path

    async def _workspace(self) -> Path:
        """Where the harness runs.

        The plugin's own storage directory by default. This is also the
        ``workspace-write`` sandbox root, so a task can create and edit files
        here without touching anything outside it — set ``workspace`` explicitly
        to point somewhere else, and the sandbox root follows.
        """
        configured = await self._cfg("workspace", "")
        if configured:
            path = Path(str(configured)).expanduser()
        else:
            path = self.storage_dir / "workspace"

        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.logger.warning("cannot create workspace %s: %s", path, exc)
        return path

    # --- harness resolution -------------------------------------------

    def _probe_sdk(self) -> bool:
        """Whether the DeepSeek Harness Python SDK is installed here.

        Uses ``find_spec`` rather than importing: probing should not execute
        the SDK's import-time side effects, and the market's ruff config runs
        with ``--ignore-noqa``, so an ``# noqa: F401`` on a probe import would
        not suppress the unused-import warning.
        """
        try:
            return importlib.util.find_spec("deepseek_harness") is not None
        except (ImportError, ValueError):
            return False

    def _resolve_dsh_executable(self) -> str | None:
        """Find a usable ``dsh`` command, preferring the configured repo checkout."""
        configured = self.metadata.get("dsh_executable") if self.metadata else None
        if configured:
            candidate = Path(str(configured)).expanduser()
            if candidate.exists():
                return str(candidate)
            self.logger.warning("configured dsh_executable missing: %s", candidate)

        found = shutil.which("dsh")
        if found:
            return found

        # A source checkout exposes dsh through pnpm; look for the repo root.
        repo = self.metadata.get("repo_path") if self.metadata else None
        if repo:
            root = Path(str(repo)).expanduser()
            for candidate in (
                root / "node_modules" / ".bin" / "dsh.cmd",
                root / "node_modules" / ".bin" / "dsh",
            ):
                if candidate.exists():
                    return str(candidate)

        # The desktop app puts no `dsh` on PATH, but it ships a working CLI
        # inside its own asar. This is the common install, so check it before
        # giving up.
        desktop = self._find_dsh_install()
        if desktop is not None:
            exe = desktop / "DeepSeek Harness.exe"
            asar = desktop / "resources" / "app.asar"
            # cli.js lives *inside* app.asar, a single archive file, so
            # Path.exists() on the full path is always False — only the app's
            # own Electron runtime resolves asar entries. Probe the archive
            # itself and let the CLI report a missing entry point if the layout
            # ever changes.
            cli = (
                asar / "dsh" / "node_modules"
                / "@deepseek-ai" / "dsh-desktop-host" / "lib" / "cli.js"
            )
            if exe.exists() and asar.exists():
                # That pair is how the desktop CLI must be launched: the exe
                # doubles as a node runtime under ELECTRON_RUN_AS_NODE.
                self._dsh_desktop_launcher = (str(exe), str(cli))
                self.logger.info("using the DSH desktop CLI at %s", cli)
                return str(cli)

        return None

    # --- entry points --------------------------------------------------

    @plugin_entry(
        id="run_task",
        name="让 DeepSeek Harness 执行任务",
        description=(
            "把一个较长或偏代码的任务交给本地 DeepSeek Harness 执行，"
            "例如读写文件、跑测试、分析仓库，然后返回它的最终回复。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要 harness 执行的任务描述"},
                "session_id": {
                    "type": "string",
                    "description": "可选的会话 ID，用于在多次调用间延续同一个会话",
                },
            },
            "required": ["task"],
        },
        timeout=900.0,
    )
    async def run_task(self, task: str, session_id: str = "", **_):
        if not task or not task.strip():
            return Err(SdkError("task 不能为空"))

        session = (session_id or "").strip() or f"neko-{int(time.time())}"
        self.logger.info("dispatching harness task (session=%s, len=%d)", session, len(task))

        try:
            outcome = await self._invoke_harness(task, session)
        except Exception as exc:
            self.logger.exception("harness run failed")
            return Err(SdkError(f"DeepSeek Harness 执行失败：{exc}"))

        return Ok(outcome)

    @plugin_entry(
        id="status",
        name="Harness 状态",
        description="检查本地 DeepSeek Harness 是否可用，以及本插件的运行记录。",
    )
    async def status(self, **_):
        return Ok({
            "sdk_available": self._sdk_available,
            "dsh_executable": self._dsh_executable,
            "python": sys.version.split()[0],
            "runs_recorded": len(self._runs),
        })

    # --- LLM tools -----------------------------------------------------

    @llm_tool(
        name="dsh_run_task",
        description=(
            "把代码、文件、测试或仓库分析类的长任务交给本地 DeepSeek Harness 执行，"
            "并取回它的最终回复。适合需要读写文件、运行命令或多步骤推理的任务；"
            "不适合闲聊或一句话就能答完的问题。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要 DeepSeek Harness 执行的任务，写清楚目标和验收标准",
                },
                "session_id": {
                    "type": "string",
                    "description": "可选，用于延续同一个 harness 会话",
                },
            },
            "required": ["task"],
        },
        timeout=900.0,
    )
    async def dsh_run_task(self, *, task: str, session_id: str = ""):
        outcome = await self.run_task(task, session_id=session_id)
        if isinstance(outcome, Ok):
            return outcome.value
        return {"ok": False, "error": str(getattr(outcome, "error", outcome))}

    # --- harness invocation --------------------------------------------

    async def _invoke_harness(self, task: str, session: str) -> dict[str, Any]:
        """Run one harness task, preferring the SDK and falling back to the CLI.

        Every run is recorded with its full lifecycle — running, then ok or
        failed — so the panel can show all tasks with their status, task text
        and output. The task body and the model's answer are kept in memory
        only; the on-disk run log stays metadata-only.
        """
        started = time.time()
        workspace = await self._workspace()
        model = str(await self._cfg("model", _DEFAULT_MODEL))
        profile = str(await self._cfg("profile", "headless"))
        timeout = float(await self._cfg("task_timeout", 600))
        repo = str(await self._cfg("repo_path", ""))
        mode = "sdk" if self._sdk_available else "cli"

        # The resolved values, not the manifest defaults: a runtime-config
        # override is invisible otherwise and profile is the one that decides
        # which CLI arguments are legal.
        self.logger.info(
            "run %s: model=%s profile=%s mode=%s", session, model, profile, mode
        )

        record: dict[str, Any] = {
            "session_id": session,
            "task": task,
            "task_chars": len(task),
            "status": "running",
            "elapsed_seconds": 0.0,
            "mode": mode,
            "model": model,
            "started_at": started,
            "output": "",
            "error": "",
        }
        self._runs[session] = record

        try:
            if self._sdk_available:
                result = await self._run_via_sdk(task, session, workspace, model, profile, repo, timeout)
            elif self._dsh_executable:
                result = await self._run_via_cli(task, session, workspace, model, profile, self._dsh_executable, timeout)
            else:
                raise RuntimeError(
                    "未找到 DeepSeek Harness。请在插件配置中指定 repo_path，"
                    "或安装桌面版 / Python SDK。"
                )
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = _truncate(str(exc), 1500)
            record["elapsed_seconds"] = round(time.time() - started, 2)
            await self._append_run_log(record)
            raise

        record["status"] = "ok"
        record["elapsed_seconds"] = round(time.time() - started, 2)
        record["output"] = _truncate(str(result))
        await self._append_run_log(record)

        return {
            "ok": True,
            "session_id": session,
            "response": record["output"],
            "elapsed_seconds": record["elapsed_seconds"],
            "mode": mode,
        }

    async def _run_via_sdk(
        self,
        task: str,
        session: str,
        workspace: Path,
        model: str,
        profile: str,
        repo: str,
        timeout: float,
    ) -> str:
        """Run through the DeepSeek Harness Python SDK."""
        from deepseek_harness import DeepSeekHarness

        kwargs: dict[str, Any] = {
            "provider": "deepseek-official",
            "model": model,
            "cwd": str(workspace),
            "profile": profile,
        }
        if repo:
            # The SDK expects an isolated home; the repo checkout is not one, so
            # only pass it when the user explicitly configured a checkout.
            kwargs["dsh_home"] = str(Path(repo).expanduser() / ".dsh-home")

        def _blocking_run() -> str:
            with DeepSeekHarness(**kwargs) as harness:
                result = harness.run(task, session_id=session)
                return str(getattr(result, "final_response", result))

        return await asyncio.wait_for(
            asyncio.to_thread(_blocking_run), timeout=timeout
        )

    async def _run_via_cli(
        self,
        task: str,
        session: str,
        workspace: Path,
        model: str,
        profile: str,
        executable: str,
        timeout: float,
    ) -> str:
        """Run through the ``dsh`` CLI as a one-shot subprocess.

        Two launch shapes: a real ``dsh`` binary (source checkout or PATH), and
        the CLI bundled inside the DSH desktop app, which has to be started
        through that app's exe with ELECTRON_RUN_AS_NODE=1.

        The headless profile takes the task as a positional argument and prints
        the final answer to stdout — there is no `run` subcommand and no
        ``--prompt-file``. The task therefore does land in argv; the trade-off
        is accepted because this is a local, single-user process and the CLI
        offers no alternative.
        """
        repo = str(await self._cfg("repo_path", ""))
        launcher = self._dsh_desktop_launcher

        if launcher is not None and executable == launcher[1]:
            argv = [launcher[0], "--expose-internals", launcher[1]]
            cwd = str(workspace)
        elif repo and "node_modules" in executable:
            argv = [executable]
            cwd = repo
        else:
            argv = [executable]
            cwd = str(workspace)

        # headless disables the account stack by design (credentials,
        # authorization, deepseek-account are all `disabled: true` in its
        # bundle). Re-enabling it through a later patch layer is what lets a
        # signed-in DeepSeek account work without an API key, and it makes the
        # provider fetch the live /models listing.
        #
        # No --model: the headless app takes only the task, --session-id and
        # --json, so an unknown flag would be swallowed as a positional. The
        # model comes from the profile's agent-default-model row instead.
        patch = await self._write_account_patch(workspace, model)

        # --patch is a launcher flag, so it must precede --profile. Putting it
        # after makes the launcher treat it as a positional and the run fails
        # with "too many arguments".
        if patch is not None:
            argv += ["--patch", str(patch)]

        argv += ["--profile", profile]
        argv.append(task)

        env = dict(os.environ)
        env.setdefault("DSH_WORKSPACE", str(workspace))
        if launcher is not None:
            # Makes the exe behave as a node runtime rather than opening the GUI.
            env["ELECTRON_RUN_AS_NODE"] = "1"

        # Logged before the run so a CLI-level failure can be diagnosed from the
        # plugin log alone: the argv is the part the user cannot see otherwise.
        self.logger.info("dsh argv: %s", " ".join(argv))
        self.logger.info("dsh cwd: %s", cwd)

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        finally:
            if proc.returncode is None:
                proc.kill()
            if patch is not None:
                try:
                    patch.unlink()
                except OSError:
                    pass

        text = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            # Warning, not debug: the default log level is INFO, so a debug
            # entry hides the one message that explains a failed run.
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                self.logger.warning("dsh stderr: %s", _truncate(err, 2000))
            if not text:
                raise RuntimeError(f"dsh 退出码 {proc.returncode}: {_truncate(err, 1500)}")
        return text

    async def _write_account_patch(self, workspace: Path, model: str) -> Path | None:
        """Write the patch that re-enables the DeepSeek account stack.

        The headless profile disables credentials, authorization and
        deepseek-account. A later patch layer can turn them back on, which is
        what makes an existing DSH sign-in usable — no API key required — and
        makes the provider fetch the live /models listing.

        The patch also carries the chosen model and reasoning effort, because
        the CLI has no flag for either: the profile's agent-default-model row is
        the only place they can be set.

        Returns the patch path, or None when the caller already asked for the
        API-key route (``provider: deepseek-official``), where re-enabling the
        account stack would only add latency.
        """
        provider = str(await self._cfg("provider", "deepseek-account"))
        if provider == "deepseek-official":
            return None

        effort = str(await self._cfg("reasoning_effort", "max"))
        if effort not in _KNOWN_EFFORTS:
            self._warn_once(
                "effort-unknown",
                f"unknown reasoning effort {effort!r}; using 'max'",
            )
            effort = "max"

        # Honour the user's sandbox selection verbatim. read-only is now a real
        # preset (declared below), so picking it genuinely confines the harness
        # instead of being silently remapped to workspace-write. workspace-write
        # is the default because most tasks need to create or edit files, and a
        # one-shot CLI run has no approval channel to service escalations.
        sandbox = str(await self._cfg("sandbox_mode", "workspace-write"))
        if sandbox not in _KNOWN_SANDBOXES:
            self._warn_once(
                "sandbox-unknown",
                f"unknown sandbox mode {sandbox!r}; using 'workspace-write'",
            )
            sandbox = "workspace-write"

        path = workspace / ".neko-account.patch.yml"
        try:
            path.write_text(
                "\n".join(
                    [
                        "# Generated by the N.E.K.O. deepseekharness_bridge plugin.",
                        "# headless disables the account stack; re-enable it so an",
                        "# existing DeepSeek sign-in works without an API key.",
                        "- id: credentials",
                        "  disabled: false",
                        "- id: authorization",
                        "  disabled: false",
                        "- id: deepseek-account",
                        "  disabled: false",
                        "- id: agent-default-model",
                        "  config:",
                        f"    provider: {provider}",
                        f"    model: {model}",
                        f"    reasoningEffort: {effort}",
                        # headless/dsh-base mounts a read-only bash executor
                        # (shell.sandboxMode = "read-only") and the default preset
                        # table only knows workspace-write(ask) and
                        # danger-full-access(never). With no read-only entry,
                        # derive(EMPTY_KNOBS) falls through to "custom" and the
                        # service throws on boot. Declare all three modes as real
                        # presets so the user's selection — read-only included —
                        # actually applies instead of being silently remapped.
                        #
                        # approval is "never" on every preset because headless has
                        # no answerer: "ask" fails closed (every escalation is
                        # denied), so "never" is the only policy that lets a
                        # one-shot run proceed. It auto-approves; the sandbox
                        # mode still enforces what the harness may touch.
                        "- id: permission-presets",
                        "  config:",
                        "    presets:",
                        "      read-only:",
                        "        sandbox: read-only",
                        "        approval: never",
                        "        name: 只读",
                        "        description: 只能查看和分析，不能改动任何文件。",
                        "      workspace-write:",
                        "        sandbox: workspace-write",
                        "        approval: never",
                        "        name: 工作区可写",
                        "        description: 可读写工作区目录内的文件，其余位置只读。",
                        "      danger-full-access:",
                        "        sandbox: danger-full-access",
                        "        approval: never",
                        "        name: 完全访问",
                        "        description: 不限制文件位置。",
                        f"    defaultPreset: {sandbox}",
                        # Sandbox policy is the composition-level default mode;
                        # the preset above pins the per-session sandbox through
                        # setSandboxMode. Keep both in agreement with the chosen
                        # mode so the selection is honoured end to end.
                        "- id: sandbox-policy",
                        "  config:",
                        f"    mode: {sandbox}",
                        f"    workspaceRoot: {workspace.as_posix()}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            self.logger.warning("cannot write the account patch: %s", exc)
            return None

        return path

    # --- run log -------------------------------------------------------

    async def _append_run_log(self, record: dict[str, Any]) -> None:
        """Append one JSON line per run so operators can audit usage.

        Metadata only. The task text and the model's answer stay in memory for
        the panel and are deliberately kept out of the on-disk log.
        """
        import json

        safe = {
            key: record[key]
            for key in (
                "session_id",
                "task_chars",
                "status",
                "elapsed_seconds",
                "mode",
                "model",
            )
            if key in record
        }

        log_dir = await self._log_dir()
        path = log_dir / "runs.jsonl"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.logger.warning("cannot append run log: %s", exc)
