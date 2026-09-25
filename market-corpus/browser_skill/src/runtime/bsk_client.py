"""Safe asyncio wrapper around the BrowserSkill ``bsk`` CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .models import Availability, BrowserInfo

_SECRET_RE = re.compile(
    r"(?ixs)"
    r"(?P<prefix>"
    r"(?P<key_quote>[\"']?)"
    r"(?:password|passwd|passcode|token|secret|api[_-]?key|authorization|cookie)"
    r"(?P=key_quote)\s*[:=]\s*"
    r")"
    r"(?:"
    r"(?P<value_quote>[\"'])(?P<quoted_value>.*?)(?P=value_quote)"
    r"|(?P<bare_value>[^,;}&\]\r\n]+)"
    r")"
)

BUNDLED_BSK_SELECTOR = "bundled"
BUNDLED_BSK_VERSION = "0.1.10"

_BUNDLED_BSK_ASSETS: dict[str, dict[str, str]] = {
    "darwin-arm64": {
        "path": "darwin-arm64/bsk",
        "sha256": "357452c2d9e15f3b24a088767eb4447dc56134ee0e32bf89c815e7b543ba987e",
    },
    "darwin-x64": {
        "path": "darwin-x64/bsk",
        "sha256": "ce96809704657e9d18cb51a80d856bc49e41a22767cb3177f6d27e10a1ab275a",
    },
    "linux-arm64": {
        "path": "linux-arm64/bsk",
        "sha256": "e4839a89b68ea49f96612da19f7869c2e298f5c7517f70d9dd85f57559325cbc",
    },
    "linux-x64": {
        "path": "linux-x64/bsk",
        "sha256": "7d94b5cabb82a5fc36d7af2032e7672cf41d6e625541dd4ce242ed80c5056f4d",
    },
    "windows-x64": {
        "path": "windows-x64/bsk.exe",
        "sha256": "e24090da00c9523eef484ef60ff932e8281183ab59b90ec95d6b22b9ee5a3e37",
    },
}


def bundled_platform_key(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> str | None:
    """Return the release platform key used by BrowserSkill's manifest."""
    system_id = str(system or platform.system()).strip().casefold()
    machine_id = str(machine or platform.machine()).strip().casefold()
    os_id = {
        "darwin": "darwin",
        "linux": "linux",
        "windows": "windows",
    }.get(system_id)
    arch_id = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x64",
        "x86_64": "x64",
    }.get(machine_id)
    if not os_id or not arch_id:
        return None
    key = f"{os_id}-{arch_id}"
    return key if key in _BUNDLED_BSK_ASSETS else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _make_executable(path: Path) -> None:
    if os.name == "nt":
        return
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@lru_cache(maxsize=8)
def resolve_bundled_executable(platform_key: str | None = None) -> str | None:
    """Select and verify the bundled CLI for the current supported platform."""
    key = platform_key or bundled_platform_key()
    asset = _BUNDLED_BSK_ASSETS.get(str(key or ""))
    if asset is None:
        return None

    plugin_root = Path(__file__).resolve().parent.parent
    executable = plugin_root / "bin" / Path(asset["path"])
    if not executable.is_file():
        raise RuntimeError(f"bundled BrowserSkill executable is missing: {asset['path']}")
    if not secrets.compare_digest(_sha256_file(executable), asset["sha256"]):
        raise RuntimeError(f"bundled BrowserSkill executable failed verification: {asset['path']}")
    _make_executable(executable)
    return str(executable.resolve())


def redact_text(value: str, *, limit: int = 2000) -> str:
    def replace_secret(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        quote = match.group("value_quote") or ""
        return f"{prefix}{quote}<redacted>{quote}"

    text = _SECRET_RE.sub(replace_secret, str(value or ""))
    return text[:limit]


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _parse_json_output(text: str) -> Any:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        return value
    return None


@dataclass(slots=True)
class BskCommandResult:
    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    data: Any = None


class BskCommandError(RuntimeError):
    def __init__(self, result: BskCommandResult) -> None:
        payload = result.data if isinstance(result.data, dict) else {}
        self.exit_code = result.exit_code
        self.code = str(payload.get("code") or _exit_code_name(result.exit_code))
        self.hint = redact_text(str(payload.get("hint") or ""))
        self.data = payload.get("data")
        self.retryable = result.exit_code in {2, 3, 4}
        message = str(payload.get("message") or result.stderr or result.stdout or "bsk command failed")
        super().__init__(redact_text(message))

    @property
    def is_stale_ref(self) -> bool:
        haystack = f"{self.code} {self} {self.data}".lower()
        return "ref_not_found" in haystack or ("snapshot ref" in haystack and "not found" in haystack)

    @property
    def is_session_busy(self) -> bool:
        haystack = f"{self.code} {self} {self.data}".lower()
        return "session_busy" in haystack or "unfinished command" in haystack


def _exit_code_name(exit_code: int) -> str:
    return {
        1: "BSK_USER_ERROR",
        2: "BSK_TRANSPORT_ERROR",
        3: "BSK_BROWSER_ERROR",
        4: "BSK_TIMEOUT",
        5: "BSK_VERSION_SKEW",
    }.get(exit_code, "COMMAND_FAILED")


class BskClient:
    """Executes only typed argument arrays; never invokes a shell."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        logger: Any = None,
        debug_enabled: bool = True,
        direct_request_help: bool = False,
    ) -> None:
        self._explicit_executable = executable
        self._executable_error = ""
        self._logger = logger
        self._debug_enabled = debug_enabled
        self._direct_request_help = direct_request_help
        self._active_process: asyncio.subprocess.Process | None = None
        self._active_rpc: tuple[str, str] | None = None
        self._process_guard = asyncio.Lock()
        self._version_guard = asyncio.Lock()
        self._version_cache = ""

    @property
    def executable(self) -> str | None:
        if self._explicit_executable:
            configured = str(self._explicit_executable).strip()
            normalized = configured.replace("\\", "/").casefold()
            if normalized in {BUNDLED_BSK_SELECTOR, "bin/bsk.exe"}:
                try:
                    resolved = resolve_bundled_executable()
                except (OSError, RuntimeError) as exc:
                    self._executable_error = redact_text(str(exc), limit=500)
                    return None
                if resolved:
                    self._executable_error = ""
                    return resolved
                self._executable_error = (
                    f"no bundled bsk build for {platform.system()} {platform.machine()}"
                )
                return None
            candidate = Path(os.path.expandvars(configured)).expanduser()
            if not candidate.is_absolute():
                candidate = Path(__file__).resolve().parent.parent / candidate
            if candidate.is_file():
                self._executable_error = ""
                return str(candidate.resolve())
            self._executable_error = ""
            return None
        self._executable_error = ""
        return shutil.which("bsk")

    def spawn_peer(self) -> "BskClient":
        """Create an independent CLI channel for long-running human handoff.

        ``request-help`` blocks until control is returned.  It therefore must
        not occupy the same process guard as ordinary agent commands.
        """
        return BskClient(
            executable=self._explicit_executable,
            logger=self._logger,
            debug_enabled=self._debug_enabled,
            # Windows console signals are not reliable from a windowless
            # plugin process. A peer used for idle handoff talks to the local
            # daemon pipe with a known RPC id, so cancellation is explicit.
            direct_request_help=os.name == "nt",
        )

    async def run(
        self,
        args: Iterable[str],
        *,
        session_id: str | None = None,
        timeout: float = 45.0,
        json_output: bool = True,
        quiet: bool = True,
    ) -> BskCommandResult:
        executable = self.executable
        if not executable:
            raise FileNotFoundError("bsk")
        argv = [str(item) for item in args]
        if session_id:
            argv.extend(["--session", session_id])
        if json_output:
            argv.append("--json")
        if quiet:
            argv.append("--quiet")

        command = argv[0] if argv else "unknown"
        if command in {"session", "tab", "daemon"} and len(argv) > 1:
            command = f"{command}.{argv[1]}"
        session_tag = (
            hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:10]
            if session_id
            else "none"
        )
        started_at = time.monotonic()
        # Successful version/status probes are high-frequency diagnostics,
        # not browser actions. Logging four lines per probe makes the useful
        # action/error trace unreadable while adding no debugging value.
        log_success = command not in {"--version", "status"}
        if self._logger is not None and self._debug_enabled and log_success:
            self._logger.debug(
                "BrowserSkill CLI start command={} session={} argc={} timeout={}",
                command,
                session_tag,
                len(argv),
                round(float(timeout), 2),
            )

        creationflags = 0
        if os.name == "nt":
            # A separate process group lets cancellation deliver CTRL_BREAK.
            # BrowserSkill handles that signal by cancelling the in-flight
            # daemon request, instead of leaving request-help queued forever.
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        async with self._process_guard:
            process = await asyncio.create_subprocess_exec(
                executable,
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
            self._active_process = process
            try:
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    await self._terminate(process)
                    result = BskCommandResult(tuple(argv), 4, "", "bsk command timed out")
                    raise BskCommandError(result)
                except asyncio.CancelledError:
                    await self._terminate(process)
                    raise
            finally:
                if self._active_process is process:
                    self._active_process = None

        stdout = _decode(stdout_b)
        stderr = _decode(stderr_b)
        data = _parse_json_output(stdout)
        result = BskCommandResult(tuple(argv), int(process.returncode or 0), stdout, stderr, data)
        if self._logger is not None and self._debug_enabled and log_success:
            self._logger.debug(
                "BrowserSkill CLI finish command={} session={} exit={} duration_ms={} stdout_chars={} stderr_chars={}",
                command,
                session_tag,
                result.exit_code,
                max(0, int((time.monotonic() - started_at) * 1000)),
                len(stdout),
                len(stderr),
            )
        if result.exit_code != 0:
            if self._logger is not None and self._debug_enabled and not log_success:
                self._logger.debug(
                    "BrowserSkill CLI diagnostic failed command={} exit={} duration_ms={}",
                    command,
                    result.exit_code,
                    max(0, int((time.monotonic() - started_at) * 1000)),
                )
            raise BskCommandError(result)
        return result

    async def cancel_active(self) -> None:
        active_rpc = self._active_rpc
        if active_rpc is not None:
            await asyncio.to_thread(self._named_pipe_cancel, *active_rpc)
        process = self._active_process
        if process is not None and process.returncode is None:
            await self._terminate(process)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        send_signal = getattr(process, "send_signal", None)
        if callable(send_signal):
            try:
                send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                await asyncio.wait_for(process.wait(), timeout=1.5)
                return
            except ProcessLookupError:
                return
            except (AttributeError, OSError, ValueError):
                pass
            except asyncio.TimeoutError:
                pass
        try:
            process.terminate()
        except OSError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except OSError:
                return
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                return

    async def version(self) -> str:
        if self._version_cache:
            return self._version_cache
        async with self._version_guard:
            if self._version_cache:
                return self._version_cache
            result = await self.run(
                ["--version"],
                timeout=10.0,
                json_output=False,
                quiet=False,
            )
            match = re.search(
                r"(?:bsk\s+)?(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)",
                result.stdout,
            )
            self._version_cache = match.group(1) if match else result.stdout[:100]
            return self._version_cache

    async def start_daemon(self) -> None:
        """Start the local daemon without installing or downloading anything."""
        await self.run(
            ["daemon", "start"],
            timeout=20.0,
            json_output=False,
            quiet=False,
        )

    async def preflight(
        self,
        *,
        browser_label: str = "",
        auto_start_daemon: bool = False,
    ) -> Availability:
        if not self.executable:
            return Availability(
                ready=False,
                reasons=["BSK_BUNDLE_ERROR" if self._executable_error else "BSK_NOT_INSTALLED"],
            )
        try:
            version = await self.version()
            try:
                status_result = await self.run(["status"], timeout=20.0)
            except BskCommandError as status_exc:
                if not auto_start_daemon or status_exc.exit_code != 2:
                    raise
                await self.start_daemon()
                status_result = await self.run(["status"], timeout=20.0)
        except BskCommandError as exc:
            code = "BSK_VERSION_SKEW" if exc.exit_code == 5 else "BSK_EXTENSION_OFFLINE"
            return Availability(ready=False, reasons=[code], version="")

        status = status_result.data if isinstance(status_result.data, dict) else {}
        raw_browsers = status.get("browsers") if isinstance(status.get("browsers"), list) else []
        browsers: list[BrowserInfo] = []
        for raw in raw_browsers:
            if not isinstance(raw, dict):
                continue
            try:
                browsers.append(BrowserInfo.model_validate(raw))
            except Exception:
                continue
        if not browsers:
            return Availability(
                ready=False,
                reasons=["BROWSER_NOT_CONNECTED"],
                version=version,
            )

        selected: BrowserInfo | None = None
        requested = browser_label.strip().casefold()
        if requested:
            matches = [
                browser
                for browser in browsers
                if browser.instance_id.casefold() == requested or browser.label.casefold() == requested
            ]
            if len(matches) != 1:
                return Availability(
                    ready=False,
                    reasons=["MULTIPLE_BROWSERS" if len(matches) > 1 else "BROWSER_NOT_CONNECTED"],
                    version=version,
                    browsers=[browser.model_dump(mode="json") for browser in browsers],
                )
            selected = matches[0]
        elif len(browsers) == 1:
            selected = browsers[0]
        else:
            return Availability(
                ready=False,
                reasons=["MULTIPLE_BROWSERS"],
                version=version,
                browsers=[browser.model_dump(mode="json") for browser in browsers],
            )

        skew_ids = {
            str(item.get("instance_id"))
            for item in status.get("version_skew_browsers", [])
            if isinstance(item, dict)
        }
        if selected.version_skew or selected.instance_id in skew_ids:
            return Availability(
                ready=False,
                reasons=["BSK_VERSION_SKEW"],
                version=version,
                browsers=[browser.model_dump(mode="json") for browser in browsers],
                selected_browser=selected.instance_id,
            )
        return Availability(
            ready=True,
            reasons=[],
            version=version,
            browsers=[browser.model_dump(mode="json") for browser in browsers],
            selected_browser=selected.instance_id,
        )

    async def status(self) -> dict[str, Any]:
        result = await self.run(["status"], timeout=20.0)
        return result.data if isinstance(result.data, dict) else {}

    async def start_session(self, browser_id: str) -> dict[str, Any]:
        result = await self.run(
            ["session", "start", "--browser", browser_id],
            timeout=45.0,
        )
        if not isinstance(result.data, dict) or not result.data.get("session_id"):
            raise RuntimeError("bsk session start returned no session_id")
        return result.data

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        result = await self.run(["session", "stop", session_id], timeout=45.0)
        return result.data if isinstance(result.data, dict) else {}

    async def snapshot(self, session_id: str, *, max_depth: int, max_tokens: int) -> dict[str, Any]:
        result = await self.run(
            ["snapshot", "--max-depth", str(max_depth), "--max-tokens", str(max_tokens)],
            session_id=session_id,
        )
        return result.data if isinstance(result.data, dict) else {}

    async def observe(self, session_id: str, *, max_depth: int, max_tokens: int) -> dict[str, Any]:
        result = await self.run(
            ["observe", "--max-depth", str(max_depth), "--max-tokens", str(max_tokens)],
            session_id=session_id,
        )
        return result.data if isinstance(result.data, dict) else {}

    async def get_html(self, session_id: str, *, ref: str | None, max_bytes: int) -> dict[str, Any]:
        args = ["get-html", "--max-bytes", str(max_bytes)]
        if ref:
            args.extend(["--ref", ref])
        result = await self.run(args, session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def screenshot(self, session_id: str, *, out: Path, ref: str | None = None) -> dict[str, Any]:
        args = ["screenshot", "--out", str(out)]
        if ref:
            args.extend(["--ref", ref])
        result = await self.run(args, session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def navigate(
        self,
        session_id: str,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
    ) -> dict[str, Any]:
        result = await self.run(
            ["navigate", url, "--wait-until", wait_until, "--timeout", "15s"],
            session_id=session_id,
            timeout=25.0,
        )
        return result.data if isinstance(result.data, dict) else {}

    async def navigate_history(self, session_id: str, direction: str) -> dict[str, Any]:
        command = "navigate-back" if direction == "back" else "navigate-forward"
        result = await self.run([command], session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def reload(self, session_id: str, *, hard: bool = False) -> dict[str, Any]:
        args = ["reload"]
        if hard:
            args.append("--hard")
        result = await self.run(args, session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def click(
        self,
        session_id: str,
        target: str,
        *,
        button: str = "left",
        click_count: int = 1,
    ) -> dict[str, Any]:
        result = await self.run(
            ["click", target, "--button", button, "--click-count", str(click_count)],
            session_id=session_id,
        )
        return result.data if isinstance(result.data, dict) else {}

    async def fill(self, session_id: str, target: str, value: str) -> dict[str, Any]:
        result = await self.run(
            ["fill", target, "--value", value],
            session_id=session_id,
        )
        return result.data if isinstance(result.data, dict) else {}

    async def select(self, session_id: str, target: str, values: list[str]) -> dict[str, Any]:
        args = ["select", target]
        for value in values:
            args.extend(["--value", value])
        result = await self.run(args, session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def press(self, session_id: str, key: str, *, target: str | None = None) -> dict[str, Any]:
        args = ["press", key]
        if target:
            if re.fullmatch(r"@?e\d+", target):
                args.extend(["--ref", target])
            else:
                args.extend(["--selector", target])
        result = await self.run(args, session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def wait_for_navigation(
        self,
        session_id: str,
        *,
        wait_until: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        result = await self.run(
            ["wait-for-navigation", "--wait-until", wait_until, "--timeout", f"{timeout_seconds}s"],
            session_id=session_id,
            timeout=float(timeout_seconds + 10),
        )
        return result.data if isinstance(result.data, dict) else {}

    async def tab_list(self, session_id: str, *, scope: str) -> dict[str, Any]:
        result = await self.run(["tab", "list", "--scope", scope], session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def tab_create(self, session_id: str, *, url: str | None = None) -> dict[str, Any]:
        args = ["tab", "create"]
        if url:
            args.extend(["--url", url])
        result = await self.run(args, session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def tab_select(self, session_id: str, tab_id: int) -> dict[str, Any]:
        result = await self.run(["tab", "select", str(tab_id)], session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def tab_borrow(self, session_id: str, tab_id: int) -> dict[str, Any]:
        # The plugin has already obtained a task-scoped confirmation through
        # request-help. Avoid a second CLI confirmation prompt while preserving
        # BrowserSkill's requirement that borrowing is always explicit.
        result = await self.run(
            ["tab", "borrow", str(tab_id), "--no-confirm"],
            session_id=session_id,
        )
        return result.data if isinstance(result.data, dict) else {}

    async def tab_return(self, session_id: str, tab_id: int) -> dict[str, Any]:
        result = await self.run(["tab", "return", str(tab_id)], session_id=session_id)
        return result.data if isinstance(result.data, dict) else {}

    async def request_help(
        self,
        session_id: str,
        *,
        prompt: str,
        title: str,
        targets: list[str],
        timeout_seconds: int,
        completion_criteria: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._direct_request_help and os.name == "nt":
            return await self._request_help_via_named_pipe(
                session_id,
                prompt=prompt,
                title=title,
                targets=targets,
                timeout_seconds=timeout_seconds,
                completion_criteria=completion_criteria,
            )
        args = [
            "request-help",
            "--prompt",
            prompt,
            "--title",
            title,
            "--timeout",
            f"{timeout_seconds}s",
        ]
        for target in targets:
            args.extend(["--target", target])
        if completion_criteria:
            args.extend(
                [
                    "--completion-criteria",
                    json.dumps(completion_criteria, ensure_ascii=False, separators=(",", ":")),
                ]
            )
        result = await self.run(
            args,
            session_id=session_id,
            timeout=float(timeout_seconds + 20),
        )
        return result.data if isinstance(result.data, dict) else {}

    async def _request_help_via_named_pipe(
        self,
        session_id: str,
        *,
        prompt: str,
        title: str,
        targets: list[str],
        timeout_seconds: int,
        completion_criteria: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Run cancellable request-help without relying on Windows signals."""
        status = await self.status()
        pipe_name = str(status.get("sock_path") or "")
        if not pipe_name.startswith(r"\\.\pipe\bsk-daemon-"):
            raise BskCommandError(
                BskCommandResult(
                    ("request-help",),
                    2,
                    "",
                    "BrowserSkill daemon did not return a trusted named pipe",
                )
            )
        rpc_id = f"browser-skill-help-{secrets.token_hex(6)}"
        resolved_targets = [
            ({"ref": value} if value.startswith("@") else {"selector": value})
            for value in targets
        ]
        params: dict[str, Any] = {
            "session_id": session_id,
            "prompt": prompt,
            "title": title,
            "timeout_ms": max(1, int(timeout_seconds * 1000)),
        }
        if resolved_targets:
            params["targets"] = resolved_targets
        if completion_criteria:
            params["completion_criteria"] = completion_criteria
        frame = {
            "id": rpc_id,
            "method": "tool.request_help",
            "params": params,
        }
        self._active_rpc = (pipe_name, rpc_id)
        rpc_task = asyncio.create_task(
            asyncio.to_thread(self._named_pipe_rpc, pipe_name, frame),
            name=f"browser-skill-pipe-help-{rpc_id}",
        )
        try:
            try:
                response = await asyncio.shield(rpc_task)
            except asyncio.CancelledError:
                # Use the known daemon correlation id. This both aborts the
                # extension overlay and releases the per-session command slot.
                await asyncio.shield(
                    asyncio.to_thread(self._named_pipe_cancel, pipe_name, rpc_id)
                )
                try:
                    await asyncio.wait_for(asyncio.shield(rpc_task), timeout=4.0)
                except (asyncio.TimeoutError, OSError):
                    pass
                raise
        finally:
            if self._active_rpc == (pipe_name, rpc_id):
                self._active_rpc = None
        if not isinstance(response, dict):
            return {}
        error = response.get("error")
        if isinstance(error, dict):
            raise BskCommandError(
                BskCommandResult(
                    ("request-help",),
                    2,
                    "",
                    str(error.get("message") or "BrowserSkill request-help failed"),
                    error,
                )
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _open_named_pipe(pipe_name: str):
        deadline = time.monotonic() + 5.0
        while True:
            try:
                return open(pipe_name, "r+b", buffering=0)  # noqa: SIM115
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    @classmethod
    def _named_pipe_rpc(cls, pipe_name: str, frame: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with cls._open_named_pipe(pipe_name) as pipe:
            pipe.write(payload + b"\n")
            line = pipe.readline()
        decoded = json.loads(line.decode("utf-8", errors="replace"))
        if not isinstance(decoded, dict) or decoded.get("id") != frame.get("id"):
            raise RuntimeError("BrowserSkill named-pipe response id mismatch")
        return decoded

    @classmethod
    def _named_pipe_cancel(cls, pipe_name: str, rpc_id: str) -> dict[str, Any]:
        cancel_id = f"browser-skill-cancel-{secrets.token_hex(6)}"
        return cls._named_pipe_rpc(
            pipe_name,
            {
                "id": cancel_id,
                "method": "cancel",
                "params": {"rpc_id": rpc_id},
            },
        )
