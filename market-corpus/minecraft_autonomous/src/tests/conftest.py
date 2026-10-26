"""Load the real public SDK without starting a N.E.K.O host."""

import asyncio
import importlib.util
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
if not (VENDOR / "agent_mc_autonomy/__init__.py").is_file():
    raise pytest.UsageError("Run the real SDK's neko-plugin sync for this plugin before testing.")
# SDK release checks launch plain pytest, outside the runtime dependency loader.
sys.path.insert(0, str(VENDOR))
SDK_ROOTS = [*([Path(os.environ["NEKO_ROOT"])] if os.environ.get("NEKO_ROOT") else []), *ROOT.parents]
for candidate in SDK_ROOTS:
    if (candidate / "plugin/sdk/plugin/__init__.py").is_file():
        SDK_ROOT = candidate.resolve()
        sys.path.insert(0, str(SDK_ROOT))
        break
else:
    raise pytest.UsageError("Set NEKO_ROOT to a N.E.K.O source checkout containing plugin/sdk/plugin.")


@pytest.fixture
def sdk_root():
    return SDK_ROOT


@pytest.fixture
def run_async():
    # Windows creates an internal socket pair when the loop is initialized.
    # Initialize it before forbidding I/O, then reuse it for guarded plugin calls.
    with asyncio.Runner() as runner:
        yield runner.run


@pytest.fixture
def plugin_module(monkeypatch):
    module_name = "minecraft_autonomous_under_test"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin_instance(monkeypatch, tmp_path, run_async, plugin_module):
    def forbidden_io(*args, **kwargs):
        raise AssertionError("Unit tests must not connect, launch processes, or send messages.")

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_io)
    monkeypatch.setattr(subprocess, "Popen", forbidden_io)

    async def get_own_config(**_):
        return {}

    ctx = SimpleNamespace(
        plugin_id="minecraft_autonomous",
        config_path=ROOT / "plugin.toml",
        metadata={},
        logger=logging.getLogger("minecraft_autonomous.tests"),
        bus=None,
        images=None,
        models=None,
        push_message=forbidden_io,
        _effective_config={},
        get_own_config=get_own_config,
    )
    return plugin_module.MinecraftAutonomousPlugin(ctx)
