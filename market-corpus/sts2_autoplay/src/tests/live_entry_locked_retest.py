from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from plugin.plugins.sts2_autoplay.tests.live_entry_smoke import LiveEntryPlugin
from plugin.sdk.shared.models.result import Err, Ok


def _resolve_neko_root() -> Path:
    """NEKO 根目录（提供 config/ 与 plugin/sdk）。见 tests/live_entry_smoke.py 的同名实现。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "plugin" / "sdk").is_dir() and (parent / "config" / "__init__.py").is_file():
            return parent
    for entry in sys.path:
        candidate = Path(entry or ".")
        if (candidate / "plugin" / "sdk").is_dir() and (candidate / "config" / "__init__.py").is_file():
            return candidate
    parents = Path(__file__).resolve().parents
    return parents[4] if len(parents) > 4 else parents[1]


PROJECT_ROOT = _resolve_neko_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

config_init = PROJECT_ROOT / "config" / "__init__.py"
if "config" not in sys.modules and config_init.exists():
    spec = importlib.util.spec_from_file_location("config", config_init)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["config"] = module
        spec.loader.exec_module(module)


async def unwrap(result: Any) -> dict[str, Any]:
    if isinstance(result, Ok):
        return {"kind": "ok", "payload": result.value}
    if isinstance(result, Err):
        return {"kind": "err", "error": str(result.error)}
    if isinstance(result, dict):
        return {"kind": "dict", "payload": result}
    return {"kind": type(result).__name__, "payload": repr(result)}


async def main() -> None:
    plugin = LiveEntryPlugin()
    startup = await plugin.startup()
    if isinstance(startup, Err):
        print(json.dumps({"startup": str(startup.error)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    before = await plugin._service.get_snapshot()
    planned = await plugin.sts2_get_planned_operation()
    after_plan = await plugin._service.get_snapshot()
    executed = await plugin.sts2_execute_planned_operation()
    after_exec = await plugin._service.get_snapshot()
    await plugin.shutdown()

    output = {
        "before_screen": before.get("snapshot", {}).get("screen"),
        "before_actions": before.get("snapshot", {}).get("available_actions"),
        "planned": await unwrap(planned),
        "after_plan_screen": after_plan.get("snapshot", {}).get("screen"),
        "after_plan_actions": after_plan.get("snapshot", {}).get("available_actions"),
        "executed": await unwrap(executed),
        "after_exec_screen": after_exec.get("snapshot", {}).get("screen"),
        "after_exec_actions": after_exec.get("snapshot", {}).get("available_actions"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
