"""A minimal stand-in for ``plugin.sdk.plugin``, used only by the local smoke test.

It mirrors the documented surface we rely on (decorators, ``Ok/Err``,
``NekoPluginBase`` plumbing) so the entry module can be imported and exercised
without a N.E.K.O checkout. It is **not** shipped and must never be imported by
plugin code. Real integration is verified by loading the plugin in the host.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class SdkError(Exception):
    pass


class Result:
    def __init__(self, value: Any = None, error: Any = None) -> None:
        self.value = value
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self.value!r})"


class Ok(Result):
    def __init__(self, value: Any = None) -> None:
        super().__init__(value=value)


class Err(Result):
    def __init__(self, error: Any = None) -> None:
        super().__init__(error=error)


def neko_plugin(cls):
    return cls


@dataclass
class EntryMeta:
    kind: str
    id: str
    options: dict[str, Any] = field(default_factory=dict)


def _decorate(kind: str, fn: Callable, options: dict[str, Any]) -> Callable:
    existing = getattr(fn, "__neko_kind__", None)
    if existing is None:
        metas = list(getattr(fn, "__neko_meta__", []))
        metas.append(EntryMeta(kind=kind, id=str(options.get("id") or fn.__name__), options=options))
        setattr(fn, "__neko_meta__", metas)
        setattr(fn, "__neko_kind__", kind)
    return fn


def plugin_entry(**options):
    return lambda fn: _decorate("entry", fn, options)


def lifecycle(**options):
    return lambda fn: _decorate("lifecycle", fn, options)


def timer_interval(**options):
    return lambda fn: _decorate("timer", fn, options)


def llm_tool(**options):
    return lambda fn: _decorate("llm_tool", fn, options)


class NekoPluginBase:
    def __init__(self, ctx: "FakeContext") -> None:
        self.ctx = ctx
        self.logger = ctx.logger
        self.config = ctx.config
        self.plugin_dir = Path(ctx.plugin_dir)
        self.entries: dict[str, Callable] = {}
        self.llm_tools: dict[str, Callable] = {}
        self.lifecycles: dict[str, Callable] = {}
        self.timers: dict[str, Callable] = {}
        self._collect()

    def _collect(self) -> None:
        for name in dir(type(self)):
            fn = getattr(type(self), name, None)
            if not callable(fn):
                continue
            for meta in getattr(fn, "__neko_meta__", []):
                bound = getattr(self, name)
                if meta.kind == "entry":
                    self.entries[meta.id] = bound
                elif meta.kind == "llm_tool":
                    self.llm_tools[str(meta.options.get("name") or meta.id)] = bound
                elif meta.kind == "lifecycle":
                    self.lifecycles[meta.id] = bound
                elif meta.kind == "timer":
                    self.timers[meta.id] = bound

    # -- capabilities used by the plugin ----------------------------------

    def data_path(self, *parts: str) -> Path:
        base = Path(self.ctx.data_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base.joinpath(*parts)

    def cache_path(self, *parts: str) -> Path:
        base = Path(self.ctx.cache_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base.joinpath(*parts)

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        self.ctx.pushed.append(kwargs)
        return {"submitted": True}

    def report_status(self, status: dict[str, Any]) -> None:
        self.ctx.status.append(status)

    def register_llm_tool(self, **kwargs: Any) -> None:  # pragma: no cover - unused
        self.llm_tools[str(kwargs.get("name"))] = kwargs.get("handler")

    def register_dynamic_entry(self, entry_id: str, handler, **kwargs) -> bool:
        self.entries[entry_id] = handler
        return True

    def list_entries(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        return [{"id": key} for key in self.entries]

    def list_llm_tools(self) -> list[dict[str, Any]]:
        return [{"name": key} for key in self.llm_tools]


class _Logger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _log(self, level: str, message: Any, *args: Any, **kwargs: Any) -> None:
        text = str(message)
        if args:
            text = text.format(*args) if "{" in text else (text % args)
        self.records.append((level, text))

    def debug(self, message, *args, **kwargs):
        self._log("debug", message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        self._log("info", message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        self._log("warning", message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self._log("error", message, *args, **kwargs)

    def exception(self, message, *args, **kwargs):
        self._log("exception", message, *args, **kwargs)


class _Config:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    async def dump(self) -> dict[str, Any]:
        return dict(self._data)

    async def get(self, key: str, default: Any = None) -> Any:
        current: Any = self._data
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    async def get_int(self, key: str, default: int = 0) -> int:
        value = await self.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


class FakeContext:
    def __init__(self, config: dict[str, Any], root: Path) -> None:
        self.config = _Config(config)
        self.logger = _Logger()
        self.plugin_id = "neko_morning_brief"
        self.plugin_dir = str(root)
        self.data_dir = str(root / "data")
        self.cache_dir = str(root / "cache")
        self.pushed: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.status: list[dict[str, Any]] = []

    async def create_card(self, **kwargs: Any):
        self.cards.append(kwargs)
        return types.SimpleNamespace(id="card-1")


def install() -> None:
    """Register the stub as ``plugin.sdk.plugin``.

    Tests always run against this stub, even inside the N.E.K.O source tree:
    the fake context cannot satisfy the real base class, and the stub keeps the
    contract test deterministic across local and CI runs.
    """

    module = types.ModuleType(__name__)
    for name, value in list(globals().items()):
        if not name.startswith("_"):
            setattr(module, name, value)
    module.__all__ = [
        "Err",
        "FakeContext",
        "NekoPluginBase",
        "Ok",
        "SdkError",
        "install",
        "lifecycle",
        "llm_tool",
        "neko_plugin",
        "plugin_entry",
        "timer_interval",
    ]

    import importlib
    import importlib.util
    from pathlib import Path

    try:
        has_real_plugin = importlib.util.find_spec("plugin") is not None
    except (ImportError, ValueError):
        has_real_plugin = False

    if has_real_plugin:
        # Inside the N.E.K.O source tree the real package exists; never shadow
        # it, or pytest's ``plugin.plugins...`` collection breaks.
        importlib.import_module("plugin")
    else:
        # Not importable from sys.path (pytest may run with the plugin dir as
        # cwd). Build a namespace package that still points at the real tree so
        # ``plugin.plugins`` keeps resolving.
        real_root = Path(__file__).resolve().parents[3]
        plugin_pkg = types.ModuleType("plugin")
        plugin_pkg.__path__ = (  # type: ignore[attr-defined]
            [str(real_root)] if (real_root / "plugins").is_dir() else []
        )
        sys.modules["plugin"] = plugin_pkg
        if plugin_pkg.__path__:
            try:
                importlib.import_module("plugin.plugins")
            except Exception:  # noqa: BLE001 - best effort
                pass

    sdk_pkg = sys.modules.get("plugin.sdk") or types.ModuleType("plugin.sdk")
    sdk_pkg.__path__ = getattr(sdk_pkg, "__path__", [])  # type: ignore[attr-defined]
    sys.modules.setdefault("plugin.sdk", sdk_pkg)
    sys.modules["plugin.sdk.plugin"] = module
