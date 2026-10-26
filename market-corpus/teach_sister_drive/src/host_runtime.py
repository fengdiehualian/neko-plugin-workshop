"""Opt-in source-host integration, loaded only by the plugin's launcher.

The stock plugin worker is a different process and cannot intercept a main
server ASR callback. This explicit bootstrap installs a scoped runtime adapter;
it does not edit host files or auto-inject into an already running host.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path

GAME = "teach-sister-drive"


def make_input_router(host, original):
    async def route(lanlan_name, state, text, *, source, mode, kind, request_id=None):
        if state.get("game_type") != GAME:
            return await original(lanlan_name, state, text, source=source, mode=mode, kind=kind, request_id=request_id)
        text = str(text or "").strip()
        if not text or state.get("_exit_flow_started") or state.get("game_route_active") is False:
            return True
        if host._get_active_game_route_state(lanlan_name, GAME) is not state:
            return True
        now = time.time()
        if kind == "user-voice":
            seen = state.setdefault("_sister_voice_seen", OrderedDict())
            while seen and next(iter(seen.values())) < now - 60:
                seen.popitem(last=False)
            key = str(request_id or "") or f"__no_id__:{text}"
            previous = seen.get(key)
            if previous is not None and (request_id or now - previous < 1):
                return True
            while len(seen) >= 128:
                seen.popitem(last=False)
            seen[key] = now
        mgr = host.get_session_manager().get(lanlan_name)
        if mgr and hasattr(mgr, "mirror_user_input"):
            meta = host.build_mirror_meta(
                source=source, kind=GAME, session_id=str(state.get("session_id") or ""), event={"memory_enabled": False}
            )
            if state.get("_sdk_route_instance_id"):
                meta["sdk_route_instance_id"] = state["_sdk_route_instance_id"]
            await mgr.mirror_user_input(
                text,
                metadata=meta,
                request_id=request_id,
                input_type=(
                    host.MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE
                    if kind == "user-voice"
                    else host.MIRROR_USER_TEXT_INPUT_TYPE
                ),
                send_to_frontend=kind == "user-voice",
            )
        # Mirroring awaits a socket; never deliver to a replacement instance.
        if host._get_active_game_route_state(lanlan_name, GAME) is not state:
            return True
        if state.get("_exit_flow_started") or state.get("game_route_active") is False:
            return True
        event = (
            host._build_external_voice_event(state, text)
            if kind == "user-voice"
            else host._build_external_text_event(state, text)
        )
        host._append_game_dialog(
            state, {"type": "user", "source": source, "text": text, "request_id": request_id or ""}
        )
        host._append_game_output(
            state,
            {
                "type": "game_external_input",
                "source": source,
                "request_id": request_id or "",
                "ts": now,
                "input_ts": now,
                "event": event,
                "meta": {
                    "kind": kind,
                    "inputText": text,
                    "inputTs": now,
                    "priority": 8,
                    "itemCount": 1,
                    "hasUserSpeech": kind == "user-voice",
                    "hasUserText": kind == "user-text",
                    "gameMemoryEnabled": False,
                    "game_memory_enabled": False,
                    "gameMemoryPlayerInteractionEnabled": False,
                    "game_memory_player_interaction_enabled": False,
                },
            },
        )
        # The page owns interruption, model reasoning and action validation.
        return True

    route.__sister_plugin_adapter__ = True
    return route


def install(app):
    from main_routers.game_router import runtime
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    if getattr(runtime._route_external_transcript_to_game, "__sister_plugin_adapter__", False):
        return
    required = (
        "_get_active_game_route_state",
        "get_session_manager",
        "build_mirror_meta",
        "_build_external_voice_event",
        "_build_external_text_event",
        "_append_game_dialog",
        "_append_game_output",
        "MIRROR_USER_VOICE_TRANSCRIPT_INPUT_TYPE",
        "MIRROR_USER_TEXT_INPUT_TYPE",
    )
    if any(not hasattr(runtime, name) for name in required):
        raise RuntimeError("This N.E.K.O host version does not support the driving plugin adapter")
    # Precede the stock /static mount. Assets stay in the plugin directory;
    # the existing same-origin SDK/player path needs no copied host assets.
    app.router.routes.insert(
        0,
        Mount(f"/static/game/{GAME}", app=StaticFiles(directory=Path(__file__).parent / "ui"), name="sister-plugin-ui"),
    )

    async def status(request):
        return JSONResponse(
            {
                "ok": True,
                "input_policy": "plugin",
                "assets": "plugin",
                "source_launcher": True,
            }
        )

    app.router.routes.insert(0, Route(f"/api/{GAME}/plugin-health", status, methods=["GET"]))
    runtime._route_external_transcript_to_game = make_input_router(runtime, runtime._route_external_transcript_to_game)


def run_main_server(*args):
    from launcher_core import runtime as launcher

    launcher._apply_child_process_signal_policy()
    launcher._reload_runtime_config_from_env()
    from app import main_server

    install(main_server.app)
    return launcher.run_main_server(*args)
