"""Lanlan (character session) target resolution for host pushes.

The host routes a push to one character session through ``target_lanlan``.
With a single connected session an empty target is fine (the host falls back),
but with several sessions an untargeted push may be dropped.  Resolution is a
pure, SDK-free helper so both the read and respond channels share exactly the
same priority order.

Resolution order (first non-empty wins)
---------------------------------------
1. an explicit target carried by the *event* (``details.target_lanlan`` /
   ``details.lanlan_name`` or a trusted ``details._ctx``);
2. the persistent plugin configuration (``target_lanlan`` / ``lanlan_name``,
   flattened or under the ``kards`` section);
3. an explicit invocation context (``invocation._current_lanlan`` /
   ``lanlan_name``) - this is the *current* call and therefore beats the
   plugin-wide legacy ``ctx._current_lanlan``;
4. an injected host resolver (pure callable supplied by the integration);
5. environment variables (``KARDS_TARGET``, ``NEKO_TARGET``,
   ``NEKO_LANLAN_NAME``, ``NEKO_HER_NAME``);
6. a bounded, exception-safe ``config_manager`` read fallback;
7. legacy ``plugin.ctx._current_lanlan`` **only** when the caller explicitly
   opts in with ``allow_legacy_context=True``.  Background ticks must not
   blindly broadcast to whatever the global context last mentioned.

An empty result is intentional: callers must omit ``target_lanlan`` instead of
inventing a character name.  Game/OCR text (for example an opponent name) is
never used as a target.
"""

from __future__ import annotations

import inspect
import os
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

TARGET_LANLAN_ENV = "KARDS_TARGET_LANLAN"
TARGET_ENV_VARS: tuple[str, ...] = (
    "KARDS_TARGET",
    "KARDS_TARGET_LANLAN",   # legacy spelling kept for compatibility
    "NEKO_TARGET",
    "NEKO_LANLAN_NAME",
    "NEKO_HER_NAME",
)
MAX_TARGET_LANLAN_CHARS = 80

# Keys in event details that may carry a trusted target.  ``lanlan_name`` is
# accepted for parity with the host callback metadata.
_EVENT_TARGET_KEYS: tuple[str, ...] = ("target_lanlan", "lanlan_name")
_CONTEXT_TARGET_KEYS: tuple[str, ...] = ("_current_lanlan", "lanlan_name",
                                         "target_lanlan")
_CONFIG_TARGET_KEYS: tuple[str, ...] = ("target_lanlan", "lanlan_name")


def clean_target_lanlan(value: Any) -> str:
    """Normalize a host target without inventing or expanding anything.

    Control characters are replaced with spaces, whitespace is collapsed and
    the result is capped at :data:`MAX_TARGET_LANLAN_CHARS`.  An empty result
    is returned for non-string / blank values.
    """
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:
        return ""
    chars: list[str] = []
    for char in text:
        if unicodedata.category(char).startswith("C"):
            chars.append(" ")
        else:
            chars.append(char)
    return " ".join("".join(chars).split())[:MAX_TARGET_LANLAN_CHARS]


def _get(mapping: Mapping[str, Any], key: str) -> Any:
    try:
        return mapping.get(key)
    except Exception:
        return None


def _from_mapping(value: Any, keys: Sequence[str]) -> str:
    if not isinstance(value, Mapping):
        return ""
    for key in keys:
        target = clean_target_lanlan(_get(value, key))
        if target:
            return target
    return ""


def _from_object(value: Any, keys: Sequence[str]) -> str:
    if value is None:
        return ""
    for key in keys:
        try:
            candidate = getattr(value, key, None)
        except Exception:
            continue
        if callable(candidate):
            try:
                candidate = candidate()
            except Exception:
                continue
        if inspect.isawaitable(candidate):
            _discard_awaitable(candidate)
            continue
        if not isinstance(candidate, (str, int)):
            continue
        target = clean_target_lanlan(candidate)
        if target:
            return target
    return ""


def _discard_awaitable(value: Any) -> None:
    """Never stringify a coroutine from an async config manager."""
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _context_target(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return _from_mapping(value, _CONTEXT_TARGET_KEYS)
    return _from_object(value, _CONTEXT_TARGET_KEYS)


def _event_target(event: Any) -> str:
    """Return only an explicitly trusted per-event target.

    This intentionally ignores names extracted from the game screen (opponent
    or deck names); they must never become a character routing target.
    """
    if event is None:
        return ""
    if isinstance(event, Mapping):
        details = event.get("details")
    else:
        details = getattr(event, "details", None)
    if not isinstance(details, Mapping):
        return ""
    target = _from_mapping(details, _EVENT_TARGET_KEYS)
    if target:
        return target
    return _context_target(details.get("_ctx"))


def _config_section(plugin: Any) -> tuple[Any, Any]:
    cfg = getattr(plugin, "_cfg", None)
    if not isinstance(cfg, Mapping):
        return None, None
    nested = cfg.get("kards")
    return cfg, nested


def _config_target(plugin: Any) -> str:
    if plugin is None:
        return ""
    cfg, nested = _config_section(plugin)
    if isinstance(nested, Mapping):
        target = _from_mapping(nested, _CONFIG_TARGET_KEYS)
        if target:
            return target
    if isinstance(cfg, Mapping):
        target = _from_mapping(cfg, _CONFIG_TARGET_KEYS)
        if target:
            return target
    # Declarative settings objects (PluginSettings) expose the field directly,
    # either on the object or on an inner section.
    for attribute in ("settings", "cfg", "config"):
        section = getattr(plugin, attribute, None)
        if section is None or isinstance(section, Mapping):
            continue
        target = _from_object(section, _CONFIG_TARGET_KEYS)
        if target:
            return target
        try:
            inner = getattr(section, "kards", None)
        except Exception:
            inner = None
        target = _from_object(inner, _CONFIG_TARGET_KEYS)
        if target:
            return target
    return _from_object(plugin, _CONFIG_TARGET_KEYS) if not isinstance(plugin, Mapping) else ""


def _config_manager(plugin: Any) -> Any:
    if plugin is None:
        return None
    for attribute in ("config_manager", "_config_manager", "cfg_manager",
                      "settings_manager", "config"):
        try:
            manager = getattr(plugin, attribute, None)
        except Exception:
            continue
        if manager is not None:
            return manager
    return None


def _manager_target(plugin: Any) -> str:
    """Exception-safe read of a small, fixed set of config keys only."""
    manager = _config_manager(plugin)
    if manager is None:
        return ""
    if isinstance(manager, Mapping):
        target = _from_mapping(manager, _CONFIG_TARGET_KEYS)
        if target:
            return target
        nested = _get(manager, "kards")
        return _from_mapping(nested, _CONFIG_TARGET_KEYS)
    for method_name in ("get", "get_setting", "read_setting", "read"):
        method = getattr(manager, method_name, None)
        if not callable(method):
            continue
        for key in _CONFIG_TARGET_KEYS:
            try:
                value = method(key)
            except TypeError:
                try:
                    value = method("kards", key)
                except Exception:
                    continue
            except Exception:
                continue
            if inspect.isawaitable(value):
                _discard_awaitable(value)
                continue
            if not isinstance(value, (str, int)):
                continue
            target = clean_target_lanlan(value)
            if target:
                return target
    return _from_object(manager, _CONFIG_TARGET_KEYS)


def _env_target(environ: Mapping[str, str] | None) -> str:
    source = os.environ if environ is None else environ
    for name in TARGET_ENV_VARS:
        target = clean_target_lanlan(source.get(name, ""))
        if target:
            return target
    return ""


def _call_host_resolver(host_resolver: Any, event: Any) -> str:
    if host_resolver is None:
        return ""
    if not callable(host_resolver):
        return clean_target_lanlan(host_resolver)
    try:
        value = host_resolver(event)
    except TypeError:
        try:
            value = host_resolver()
        except Exception:
            return ""
    except Exception:
        return ""
    if inspect.isawaitable(value):
        _discard_awaitable(value)
        return ""
    # A host resolver may expose an explicit resolution object.
    if not isinstance(value, (str, type(None), Mapping)):
        resolved = getattr(value, "target", None)
        if resolved is not None:
            value = resolved
    if not isinstance(value, (str, int)):
        return ""
    return clean_target_lanlan(value)


def _normalize_known(known_targets: Any) -> tuple[str, ...]:
    if known_targets is None:
        return ()
    if callable(known_targets):
        try:
            known_targets = known_targets()
        except Exception:
            return ()
    if isinstance(known_targets, str):
        known_targets = (known_targets,)
    try:
        values = list(known_targets)  # type: ignore[arg-type]
    except TypeError:
        return ()
    seen: list[str] = []
    for value in values:
        target = clean_target_lanlan(value)
        if target and target not in seen:
            seen.append(target)
    return tuple(seen)


@dataclass(frozen=True)
class TargetResolution:
    """Result of target resolution, including why it is empty.

    ``ambiguous`` means several known sessions exist and no explicit target
    selected one; callers must report ``route_unavailable`` instead of
    guessing a character.
    """

    target: str = ""
    reason: str = "empty"
    known_targets: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.ambiguous

    @property
    def ambiguous(self) -> bool:
        return self.reason == "ambiguous" or (
            not self.target and len(self.known_targets) > 1)

    @property
    def route_unavailable(self) -> bool:
        return self.ambiguous

    def to_dict(self) -> dict[str, object]:
        return {
            "target_present": bool(self.target),
            "reason": self.reason,
            "ambiguous": self.ambiguous,
            "known_targets": len(self.known_targets),
        }


def resolve_target(
    plugin: Any = None,
    event: Any = None,
    *,
    invocation: Any = None,
    host_resolver: Any = None,
    environ: Mapping[str, str] | None = None,
    allow_legacy_context: bool = False,
    known_targets: Any = None,
    config_manager: Any = None,
) -> TargetResolution:
    """Resolve the host target and expose the reason for diagnostics.

    ``event`` is a per-item override and never mutates global state.  Passing
    ``invocation`` is an explicit statement about *this* call; the plugin-wide
    legacy ``ctx._current_lanlan`` is deliberately ignored unless
    ``allow_legacy_context=True``.
    """
    target = _event_target(event)
    if target:
        return TargetResolution(target, "explicit_event")

    target = _config_target(plugin)
    if target:
        return TargetResolution(target, "config")

    target = _context_target(invocation)
    if target:
        return TargetResolution(target, "invocation")

    target = _call_host_resolver(host_resolver, event)
    if target:
        return TargetResolution(target, "host_resolver")

    target = _env_target(environ)
    if target:
        return TargetResolution(target, "environment")

    if config_manager is not None:
        target = _from_mapping(config_manager, _CONFIG_TARGET_KEYS) \
            if isinstance(config_manager, Mapping) else ""
        if not target:
            target = _from_object(config_manager, _CONFIG_TARGET_KEYS)
        if not target:
            # The manager may live on the plugin instead.
            target = _manager_target(plugin)
    else:
        # A manager attached to the plugin is the documented fallback.
        target = _manager_target(plugin)
    if target:
        return TargetResolution(target, "config_manager")

    known = _normalize_known(known_targets)
    if len(known) == 1:
        return TargetResolution(known[0], "single_known_target", known)
    if len(known) > 1:
        return TargetResolution("", "ambiguous", known)

    if allow_legacy_context:
        ctx = getattr(plugin, "ctx", None) if plugin is not None else None
        target = _context_target(ctx)
        if target:
            return TargetResolution(target, "legacy_context")
    return TargetResolution("", "empty", known)


def resolve_target_lanlan(
    plugin: Any = None,
    event: Any = None,
    *,
    invocation: Any = None,
    host_resolver: Any = None,
    environ: Mapping[str, str] | None = None,
    allow_legacy_context: bool = False,
    known_targets: Any = None,
) -> str:
    """Backward-compatible string form; empty means no target is known."""
    return resolve_target(
        plugin,
        event,
        invocation=invocation,
        host_resolver=host_resolver,
        environ=environ,
        allow_legacy_context=allow_legacy_context,
        known_targets=known_targets,
    ).target


__all__ = [
    "MAX_TARGET_LANLAN_CHARS",
    "TARGET_ENV_VARS",
    "TARGET_LANLAN_ENV",
    "TargetResolution",
    "clean_target_lanlan",
    "resolve_target",
    "resolve_target_lanlan",
]
