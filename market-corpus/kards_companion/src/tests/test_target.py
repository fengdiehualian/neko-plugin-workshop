"""Target resolution is a pure routing helper; never invent a name."""

from kards_companion.adapters.target import (
    MAX_TARGET_LANLAN_CHARS,
    TARGET_ENV_VARS,
    TargetResolution,
    clean_target_lanlan,
    resolve_target,
    resolve_target_lanlan,
)


class Ctx:
    def __init__(self, value):
        self._current_lanlan = value


class Plugin:
    def __init__(self, cfg=None, ctx=None, settings=None, manager=None):
        self._cfg = cfg or {}
        self.ctx = ctx
        if settings is not None:
            self.settings = settings
        if manager is not None:
            self.config_manager = manager


def ev(**details):
    return {"kind": "hq_damage", "details": details}


def test_clean_target_strips_controls_collapses_and_caps() -> None:
    assert clean_target_lanlan("  lanlan\x00\t ") == "lanlan"
    assert clean_target_lanlan("a\nb\r\nc") == "a b c"
    assert clean_target_lanlan("x" * 200) == "x" * MAX_TARGET_LANLAN_CHARS
    assert clean_target_lanlan(None) == ""
    assert clean_target_lanlan(123) == "123"
    # No padding / invented fallback.
    assert clean_target_lanlan("   ") == ""


def test_event_explicit_target_beats_everything() -> None:
    plugin = Plugin(cfg={"target_lanlan": "cfg"}, ctx=Ctx("legacy"))
    assert resolve_target(plugin, ev(target_lanlan="event"), environ={
        "KARDS_TARGET": "env"}).target == "event"
    assert resolve_target_lanlan(plugin, ev(lanlan_name="named"), environ={
        "KARDS_TARGET": "env"}) == "named"
    assert resolve_target_lanlan(plugin, ev(_ctx={"_current_lanlan": "ctx-ev"}),
                                 environ={"KARDS_TARGET": "env"}) == "ctx-ev"


def test_config_nested_and_alias_then_invocation_beats_legacy_context() -> None:
    nested = Plugin(cfg={"kards": {"lanlan_name": "  nested\x00 "}})
    assert resolve_target_lanlan(nested, environ={}) == "nested"

    plugin = Plugin(cfg={}, ctx=Ctx("stale-global"))
    invocation = {"_current_lanlan": "current-call"}
    # Legacy global context is ignored unless explicitly opted in.
    assert resolve_target_lanlan(plugin, environ={}) == ""
    assert resolve_target_lanlan(plugin, environ={},
                                 allow_legacy_context=True) == "stale-global"
    assert resolve_target_lanlan(plugin, invocation=invocation,
                                 environ={}) == "current-call"
    assert resolve_target_lanlan(plugin, invocation=invocation, environ={},
                                 allow_legacy_context=True) == "current-call"


def test_env_order_and_config_manager_safe_fallback(monkeypatch) -> None:
    for name in TARGET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    env = {TARGET_ENV_VARS[0]: "first", TARGET_ENV_VARS[1]: "second"}
    assert resolve_target_lanlan(None, environ=env) == "first"

    class Manager:
        def get(self, key, *args):
            if key == "target_lanlan":
                raise RuntimeError("config unavailable")
            if key == "lanlan_name":
                return "manager-name"
            raise KeyError(key)

    plugin = Plugin(manager=Manager())
    assert resolve_target_lanlan(plugin, environ={}) == "manager-name"

    # A throwing manager must never break routing.
    class Broken:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    assert resolve_target_lanlan(Plugin(manager=Broken()), environ={}) == ""


def test_injected_host_resolver_and_empty_is_never_invented() -> None:
    calls = []

    def resolver(event):
        calls.append(event)
        return " host-name \x00"

    assert resolve_target_lanlan(None, ev(), host_resolver=resolver,
                                 environ={}) == "host-name"
    assert calls and calls[0]["kind"] == "hq_damage"
    assert resolve_target_lanlan(None, None, environ={}) == ""
    assert resolve_target(None, None, environ={}) == TargetResolution("", "empty")


def test_multiple_known_targets_are_ambiguous_not_guessed() -> None:
    result = resolve_target(None, ev(), known_targets=("alpha", "beta"), environ={})
    assert result.target == ""
    assert result.ambiguous and result.route_unavailable
    assert result.reason == "ambiguous"
    assert resolve_target_lanlan(None, ev(), known_targets=("alpha", "beta"),
                                 environ={}) == ""

    single = resolve_target(None, ev(), known_targets=("only",), environ={})
    assert single.target == "only" and single.ok


def test_game_screen_names_are_never_used_as_target() -> None:
    # Explicit non-target game fields must not leak into routing.
    assert resolve_target_lanlan(
        None, ev(game_name="Opponent_OCR", opponent="Someone", deck="Deck"),
        environ={}) == ""
