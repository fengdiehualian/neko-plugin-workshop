from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from typing import Any, Iterable

import pytest
from plugin.plugins.browser_skill.runtime.bsk_client import (
    BUNDLED_BSK_SELECTOR,
    BUNDLED_BSK_VERSION,
    BskClient,
    BskCommandError,
    BskCommandResult,
    bundled_platform_key,
    redact_text,
    resolve_bundled_executable,
)


class RecordingClient(BskClient):
    def __init__(self) -> None:
        super().__init__(executable=__file__)
        self.calls: list[tuple[list[str], str | None]] = []

    async def run(
        self,
        args: Iterable[str],
        *,
        session_id: str | None = None,
        timeout: float = 45.0,
        json_output: bool = True,
        quiet: bool = True,
    ) -> BskCommandResult:
        argv = list(args)
        self.calls.append((argv, session_id))
        data: dict[str, Any] = {"ok": True}
        if argv[:2] == ["session", "start"]:
            data = {"session_id": "s-1", "browser_instance_id": "browser-1"}
        return BskCommandResult(tuple(argv), 0, "{}", "", data)


class PreflightClient(BskClient):
    def __init__(self, browsers: list[dict[str, Any]], *, skew: list[dict[str, Any]] | None = None) -> None:
        super().__init__(executable=__file__)
        self.browsers = browsers
        self.skew = skew or []

    async def version(self) -> str:
        return "0.1.10"

    async def run(self, args: Iterable[str], **kwargs: Any) -> BskCommandResult:
        data = {
            "daemon_version": "0.1.10",
            "protocol_version": "1.0",
            "browsers": self.browsers,
            "sessions": [],
            "version_skew_browsers": self.skew,
        }
        return BskCommandResult(tuple(args), 0, "{}", "", data)


class DaemonRecoveryClient(PreflightClient):
    def __init__(self, browsers: list[dict[str, Any]]) -> None:
        super().__init__(browsers)
        self.status_attempts = 0
        self.daemon_starts = 0

    async def run(self, args: Iterable[str], **kwargs: Any) -> BskCommandResult:
        argv = list(args)
        if argv == ["status"]:
            self.status_attempts += 1
            if self.status_attempts == 1:
                result = BskCommandResult(tuple(argv), 2, "", "daemon not running", None)
                raise BskCommandError(result)
        return await super().run(argv, **kwargs)

    async def start_daemon(self) -> None:
        self.daemon_starts += 1


@pytest.mark.asyncio
async def test_typed_commands_bind_session_and_never_use_shell_strings() -> None:
    client = RecordingClient()
    await client.snapshot("s-1", max_depth=12, max_tokens=4000)
    await client.fill("s-1", "@e4", "a value; $(not-a-shell)")
    await client.screenshot("s-1", out=Path("fake-shot.png"))
    await client.tab_borrow("s-1", 42)
    await client.navigate("s-1", "https://www.bing.com/search?q=neko")

    assert all(session_id == "s-1" for _, session_id in client.calls)
    assert client.calls[1][0] == ["fill", "@e4", "--value", "a value; $(not-a-shell)"]
    assert client.calls[3][0] == ["tab", "borrow", "42", "--no-confirm"]
    assert client.calls[4][0] == [
        "navigate",
        "https://www.bing.com/search?q=neko",
        "--wait-until",
        "domcontentloaded",
        "--timeout",
        "15s",
    ]


@pytest.mark.asyncio
async def test_diagnostics_and_session_lifecycle_have_expected_binding() -> None:
    client = RecordingClient()
    await client.start_session("browser-1")
    await client.stop_session("s-1")
    assert client.calls == [
        (["session", "start", "--browser", "browser-1"], None),
        (["session", "stop", "s-1"], None),
    ]


@pytest.mark.asyncio
async def test_daemon_start_uses_explicit_command() -> None:
    client = RecordingClient()
    await client.start_daemon()
    assert client.calls == [(["daemon", "start"], None)]


@pytest.mark.asyncio
async def test_cli_version_is_cached_for_repeated_preflight_checks() -> None:
    client = RecordingClient()

    first = await client.version()
    second = await client.version()

    assert first == second == "{}"
    assert client.calls == [(["--version"], None)]


def browser_entry(instance_id: str, *, label: str = "Personal") -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "browser_name": "Chrome",
        "browser_version": "140.0",
        "extension_version": "0.1.10",
        "label": label,
        "session_count": 0,
        "connected_at_ms": 1_700_000_000_000,
        "version_skew": False,
        "extension_protocol_version": "1.0",
        "future_informational_field": "accepted",
    }


@pytest.mark.asyncio
async def test_preflight_accepts_real_status_shape_and_selects_one_browser() -> None:
    result = await PreflightClient([browser_entry("chrome-1")]).preflight()
    assert result.ready
    assert result.selected_browser == "chrome-1"
    assert result.browsers[0]["connected_at_ms"] == 1_700_000_000_000


@pytest.mark.asyncio
async def test_preflight_requires_explicit_choice_for_multiple_browsers() -> None:
    client = PreflightClient(
        [browser_entry("chrome-1", label="Personal"), browser_entry("edge-1", label="Work")]
    )
    ambiguous = await client.preflight()
    selected = await client.preflight(browser_label="Work")
    assert ambiguous.reasons == ["MULTIPLE_BROWSERS"]
    assert selected.ready and selected.selected_browser == "edge-1"


@pytest.mark.asyncio
async def test_preflight_blocks_version_skew() -> None:
    browser = browser_entry("chrome-1")
    browser["version_skew"] = True
    result = await PreflightClient([browser]).preflight()
    assert not result.ready
    assert result.reasons == ["BSK_VERSION_SKEW"]


@pytest.mark.asyncio
async def test_preflight_can_start_daemon_once_then_retry_status() -> None:
    client = DaemonRecoveryClient([browser_entry("edge-1")])
    result = await client.preflight(auto_start_daemon=True)
    assert result.ready
    assert client.daemon_starts == 1
    assert client.status_attempts == 2


def test_error_mapping_and_redaction() -> None:
    result = BskCommandResult(
        ("status",),
        5,
        '{"code":"VERSION_SKEW","message":"token=abc123 failed","hint":"password=hunter2"}',
        "",
        {
            "code": "VERSION_SKEW",
            "message": "token=abc123 failed",
            "hint": "password=hunter2",
        },
    )
    error = BskCommandError(result)
    assert error.exit_code == 5
    assert "abc123" not in str(error)
    assert "hunter2" not in error.hint
    assert redact_text("cookie=session-value") == "cookie=<redacted>"
    assert redact_text('{"token":"json-secret"}') == '{"token":"<redacted>"}'
    assert redact_text('password: "secret with spaces"') == 'password: "<redacted>"'
    assert redact_text("Authorization: Bearer bearer-secret") == "Authorization: <redacted>"

    busy = BskCommandError(
        BskCommandResult(
            ("tab", "list"),
            4,
            "",
            "session already has an unfinished command",
            {"code": "timeout", "data": {"reason": "session_busy"}},
        )
    )
    assert busy.is_session_busy


def test_relative_executable_resolves_from_plugin_root() -> None:
    client = BskClient(executable="bin/LICENSE.BrowserSkill")
    executable = Path(str(client.executable))
    assert executable.name == "LICENSE.BrowserSkill"
    assert executable.parent.name == "bin"
    assert executable.is_file()


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "darwin-arm64"),
        ("Darwin", "x86_64", "darwin-x64"),
        ("Linux", "aarch64", "linux-arm64"),
        ("Linux", "amd64", "linux-x64"),
        ("Windows", "AMD64", "windows-x64"),
        ("Windows", "ARM64", None),
        ("FreeBSD", "x86_64", None),
    ],
)
def test_bundled_platform_detection(
    system: str,
    machine: str,
    expected: str | None,
) -> None:
    assert bundled_platform_key(system=system, machine=machine) == expected


@pytest.mark.parametrize(
    "platform_key",
    ["darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64", "windows-x64"],
)
def test_each_bundled_platform_resolves_direct_executable(platform_key: str) -> None:
    executable = Path(str(resolve_bundled_executable(platform_key)))
    assert executable.is_file()
    assert executable.is_relative_to(Path(__file__).resolve().parent.parent / "bin")


@pytest.mark.asyncio
async def test_bundled_cli_for_current_platform_reports_expected_version() -> None:
    if bundled_platform_key() is None:
        pytest.skip("current platform has no BrowserSkill release asset")
    client = BskClient(executable=BUNDLED_BSK_SELECTOR)
    executable = Path(str(client.executable))
    assert executable.is_file()
    plugin_bin = Path(__file__).resolve().parent.parent / "bin"
    assert executable.is_relative_to(plugin_bin)
    assert ".runtime" not in executable.parts
    assert await client.version() == BUNDLED_BSK_VERSION


@pytest.mark.asyncio
async def test_preflight_distinguishes_missing_custom_path_from_broken_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = await BskClient(executable="missing/bsk").preflight()
    assert missing.reasons == ["BSK_NOT_INSTALLED"]

    def broken_bundle(_platform_key: str | None = None) -> str:
        raise RuntimeError("checksum mismatch")

    module = sys.modules[BskClient.__module__]
    monkeypatch.setattr(module, "resolve_bundled_executable", broken_bundle)
    broken = await BskClient(executable=BUNDLED_BSK_SELECTOR).preflight()
    assert broken.reasons == ["BSK_BUNDLE_ERROR"]


class HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.Future()
        raise AssertionError("unreachable")

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            await asyncio.Future()
        return int(self.returncode)


@pytest.mark.asyncio
async def test_timeout_terminates_then_kills(monkeypatch: pytest.MonkeyPatch) -> None:
    process = HangingProcess()

    async def fake_create(*args: Any, **kwargs: Any) -> HangingProcess:
        assert kwargs.get("stdout") == asyncio.subprocess.PIPE
        assert kwargs.get("stderr") == asyncio.subprocess.PIPE
        return process

    original_wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable: Any, timeout: float) -> Any:
        return await original_wait_for(awaitable, timeout=min(timeout, 0.01))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)
    client = BskClient(executable=__file__)
    with pytest.raises(BskCommandError) as caught:
        await client.run(["status"], timeout=0.01)
    assert caught.value.exit_code == 4
    assert process.terminated
    assert process.killed


@pytest.mark.asyncio
async def test_cancel_uses_graceful_signal_before_force_termination() -> None:
    class GracefulProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.signals: list[int] = []
            self.terminated = False

        def send_signal(self, value: int) -> None:
            self.signals.append(value)
            self.returncode = 0

        async def wait(self) -> int:
            return int(self.returncode or 0)

        def terminate(self) -> None:
            self.terminated = True

    process = GracefulProcess()
    await BskClient._terminate(process)  # type: ignore[arg-type]
    assert process.signals
    assert process.terminated is False


@pytest.mark.asyncio
async def test_direct_request_help_cancels_known_daemon_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BskClient(executable=__file__, direct_request_help=True)
    rpc_started = threading.Event()
    rpc_released = threading.Event()
    cancelled_ids: list[str] = []

    async def fake_status() -> dict[str, Any]:
        return {"sock_path": r"\\.\pipe\bsk-daemon-test"}

    def fake_rpc(pipe_name: str, frame: dict[str, Any]) -> dict[str, Any]:
        assert pipe_name.endswith("bsk-daemon-test")
        rpc_started.set()
        assert rpc_released.wait(timeout=2)
        return {
            "id": frame["id"],
            "error": {"code": "cancelled", "message": "request_help aborted"},
        }

    def fake_cancel(pipe_name: str, rpc_id: str) -> dict[str, Any]:
        cancelled_ids.append(rpc_id)
        rpc_released.set()
        return {"id": "cancel-test", "result": {"cancelled": True}}

    monkeypatch.setattr(client, "status", fake_status)
    monkeypatch.setattr(client, "_named_pipe_rpc", fake_rpc)
    monkeypatch.setattr(client, "_named_pipe_cancel", fake_cancel)

    task = asyncio.create_task(
        client._request_help_via_named_pipe(
            "s-1",
            prompt="take control",
            title="idle",
            targets=[],
            timeout_seconds=60,
            completion_criteria=None,
        )
    )
    await asyncio.wait_for(asyncio.to_thread(rpc_started.wait), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(cancelled_ids) == 1
    assert cancelled_ids[0].startswith("browser-skill-help-")


@pytest.mark.asyncio
async def test_debug_logging_never_records_command_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CompleteProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b'{"ok":true}', b""

    class DebugLogger:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def debug(self, *args: Any) -> None:
            self.calls.append(args)

    async def fake_create(*_args: Any, **_kwargs: Any) -> CompleteProcess:
        return CompleteProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    logger = DebugLogger()
    client = BskClient(executable=__file__, logger=logger)

    await client.run(
        ["fill", "@e7", "--value", "do-not-log-this"],
        session_id="private-session-id",
    )

    rendered = repr(logger.calls)
    assert "do-not-log-this" not in rendered
    assert "private-session-id" not in rendered
    assert "fill" in rendered

    action_log_count = len(logger.calls)
    await client.run(["status"])
    await client.run(["--version"], json_output=False, quiet=False)
    assert len(logger.calls) == action_log_count
