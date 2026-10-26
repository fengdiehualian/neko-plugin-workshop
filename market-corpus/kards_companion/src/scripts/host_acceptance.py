#!/usr/bin/env python3
"""Read-only N.E.K.O host acceptance probe for kards_companion.

Probes processes, loopback ports, OpenAPI, installed metadata, and GET /plugins.
Never starts KARDS/Steam, sends game input, or calls an unknown action.  --diagnostics
allows only monitor_status/monitor_diagnostics via an OpenAPI-discovered route.
Only --launch-host starts the verified exe (wait <= 60s).  The bridge token is used
in memory for one 401/403 retry and is never reported or persisted.  Python 3.11+.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOST_EXE = Path(r"E:\Steam\steamapps\common\n.e.k.o\N.E.K.O.exe")
HOST_BACKEND = HOST_EXE.parent / "resources" / "bin" / "projectneko_server.exe"
LOCAL = Path(os.environ.get("LOCALAPPDATA", "")) / "N.E.K.O"
ROAMING = Path(os.environ.get("APPDATA", "")) / "N.E.K.O"
PLUGIN_ROOT = LOCAL / ".neko-plugin-installations" / "plugins"
OVERRIDES = LOCAL / "config" / "plugin_runtime_overrides.json"
PORT_CONFIG = ROAMING / "port_config.json"
CORE_CONFIG = LOCAL / "config" / "core_config.json"
BRIDGE_FILE = Path.home() / ".neko" / "bridge.json"
CANDIDATES = ("kards_companion_dev", "kards_companion")
READ_ONLY_ACTIONS = ("monitor_status", "monitor_diagnostics")
HOSTED_UI_ROUTE = "/plugin/{plugin_id}/hosted-ui/action/{action_id}"
SENSITIVE = re.compile(r"(^|_)(token|secret|password|passwd|api_?key|authorization|cookie|"
                       r"prompt|chat|conversation|message|content|text|raw|screenshot|image)(_|$)", re.I)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
OPENER = urllib.request.build_opener(NoRedirect)


def jread(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def clean(value, depth=0):
    """Redact secret/chat/prompt keys and cap report size."""
    if depth > 6:
        return "<depth-limit>"
    if isinstance(value, dict):
        return {str(k): clean(v, depth + 1) for k, v in list(value.items())[:80]
                if not SENSITIVE.search(str(k))}
    if isinstance(value, list):
        return [clean(v, depth + 1) for v in value[:40]]
    if isinstance(value, str):
        return value[:300]
    return value if isinstance(value, (int, float, bool)) or value is None else f"<{type(value).__name__}>"


def bridge_token():
    data = jread(BRIDGE_FILE)
    return data.get("token") if data and isinstance(data.get("token"), str) else None


def processes():
    if os.name != "nt":
        return {"supported": False, "host_exe_running": False, "backend_running": False, "running": False}
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or b""
        text = out.decode("utf-8", errors="ignore").lower()
    except (OSError, subprocess.SubprocessError):
        text = ""
    host, backend = "n.e.k.o.exe" in text, "projectneko_server.exe" in text
    return {"supported": True, "host_exe_running": host, "backend_running": backend, "running": host or backend}


def ports():
    data, source = jread(PORT_CONFIG), "port_config.json"
    if not data:
        data = (jread(CORE_CONFIG) or {}).get("customPorts")
        data, source = data if isinstance(data, dict) else {}, "core_config.json:customPorts"
    used = []
    def pick(default, *names):
        for name in names:
            if isinstance(data.get(name), int):
                return data[name]
        used.append(names[0])
        return default
    main = pick(48911, "MAIN_SERVER_PORT", "MAIN_SERVER")
    plugin = pick(48916, "USER_PLUGIN_SERVER_PORT", "USER_PLUGIN_SERVER")
    return {"source": source if data else "default", "main": main, "plugin": plugin, "defaults_used": used}


def tcp_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def http_json(method, url, timeout, auth, body=None):
    """Loopback-only JSON request; bridge token is used for one 401/403 retry."""
    if urllib.parse.urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        return {"status": None, "json": None, "reason": "non_loopback_refused"}
    headers = {"Accept": "application/json", "User-Agent": "neko-host-acceptance/1"}

    def once(hdrs):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        with OPENER.open(req, timeout=timeout) as resp:
            raw, status = resp.read(2_000_000), resp.status
        try:
            return status, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return status, None

    status = payload = None
    for attempt in (1, 2):
        try:
            status, payload = once(headers)
            break
        except urllib.error.HTTPError as exc:
            token = bridge_token() if attempt == 1 and exc.code in (401, 403) else None
            if not token:
                return {"status": exc.code, "json": None, "reason": f"http_{exc.code}"}
            auth["used"] = True
            headers["Authorization"] = f"Bearer {token}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"status": None, "json": None, "reason": type(exc).__name__}
    if status is None:
        return {"status": None, "json": None, "reason": "retry_exhausted"}
    return {"status": status, "json": payload, "reason": None if payload is not None else "not_json"}


def get_openapi(port, timeout, auth):
    result = http_json("GET", f"http://127.0.0.1:{port}/openapi.json", timeout, auth)
    spec = result.get("json")
    paths = spec.get("paths") if result.get("status") == 200 and isinstance(spec, dict) else None
    if not isinstance(paths, dict):
        return {"available": False, "status": result.get("status"),
                "reason": result.get("reason") or "openapi_shape_unrecognized"}, None
    action_like = sorted(p[:160] for p in paths
                         if any(k in p.lower() for k in ("action", "invoke", "entr")) and "llm-tools" not in p.lower())
    hosted_ui = any(p.rstrip("/").lower() == HOSTED_UI_ROUTE for p in paths)
    return {"available": True, "path_count": len(paths), "action_like_paths": action_like[:25],
            "hosted_ui_path_observed": hosted_ui}, spec


def invoke_readonly(base, spec, plugin_id, action, timeout, auth):
    """Only the observed Hosted UI template is accepted; no fuzzy route matching."""
    if action not in READ_ONLY_ACTIONS:
        return {"status": "refused", "reason": "action_not_allowlisted"}
    paths = spec.get("paths", {})
    route = next((p for p in paths if p.rstrip("/").lower() == HOSTED_UI_ROUTE), None)
    ops = paths.get(route) if route else None
    if not isinstance(ops, dict):
        return {"status": "blocked", "reason": "hosted_ui_route_not_observed"}
    for method in ("get", "post"):
        op = ops.get(method)
        if not isinstance(op, dict):
            continue
        docs = " ".join(str(op.get(k) or "") for k in ("summary", "description", "operationId"))
        docs += " " + " ".join(str(x) for x in (op.get("tags") or []))
        if docs.strip() and "hosted" not in docs.lower():
            continue  # docs disagree with the Hosted UI contract
        required = any(isinstance(p, dict) and p.get("required") and p.get("in") != "path"
                       for p in op.get("parameters", []))
        if required or (method == "post" and op.get("requestBody")):
            continue  # query/body shape not confirmed by this probe
        candidate = route.replace("{plugin_id}", urllib.parse.quote(plugin_id, safe=""))
        candidate = candidate.replace("{action_id}", urllib.parse.quote(action, safe=""))
        if "{" in candidate:
            continue
        result = http_json(method.upper(), base + candidate, timeout, auth, b"{}" if method == "post" else None)
        if result.get("status") == 200 and isinstance(result.get("json"), dict):
            return {"status": "completed", "route": route, "payload": result["json"]}
    return {"status": "unconfirmed", "reason": "hosted_ui_contract_not_confirmed"}


def summarize(payload, diagnostics=False):
    node = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(node, dict):
        return {"shape": type(node).__name__}
    out = {k: clean(node[k]) for k in ("monitoring", "status", "mode", "dry_run", "reading", "capture",
                                       "capture_health", "last_error", "error", "reason", "counters", "timeline")
           if k in node}
    if diagnostics and isinstance(node.get("timeline"), list):
        out["timeline"] = [{k: clean(v) for k, v in item.items()
                            if k in ("stage", "result", "reason", "status", "ts", "frame_age_ms")}
                           for item in node["timeline"][:40] if isinstance(item, dict)]
    return out


def inspect_entry(root, entry):
    module = entry.split(":", 1)[0]
    guesses = [root / "__init__.py"]
    if module.startswith("plugin.plugins."):
        rel = module[len("plugin.plugins."):].replace(".", "/")
        guesses += [root / f"{rel}.py", root / rel / "__init__.py"]
    guesses.append(root / f"{module.rsplit('.', 1)[-1]}.py")
    source = next((p for p in guesses if p.is_file()), None)
    if source is None:
        return {"source_file": None, "entries": [], "ui_actions": [], "llm_tools": [], "reason": "entry_file_not_found"}
    text = source.read_text(encoding="utf-8", errors="replace")

    def grab(pattern: str) -> list[str]:
        return sorted(set(re.findall(pattern, text)))

    return {"source_file": str(source), "source_sha256_16": hashlib.sha256(source.read_bytes()).hexdigest()[:16],
            "entries": grab(r'@plugin_entry\(\s*id\s*=\s*"([^"]+)"'),
            "ui_actions": grab(r'@ui\.action\(\s*id\s*=\s*"([^"]+)"'),
            "llm_tools": grab(r'@llm_tool\(\s*name\s*=\s*"([^"]+)"')}


def scan_plugins(overrides):
    result = []
    for dirname in CANDIDATES:
        root = PLUGIN_ROOT / dirname
        if not root.is_dir():
            result.append({"id": dirname, "installed": False})
            continue
        try:
            plugin = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8")).get("plugin", {})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            result.append({"id": dirname, "installed": True, "error": f"plugin_toml:{type(exc).__name__}"})
            continue
        plugin_id = str(plugin.get("id") or dirname)
        override = overrides.get(plugin_id) or overrides.get(dirname) or {}
        result.append({"id": plugin_id, "directory": dirname, "installed": True, "version": plugin.get("version"),
                       "enabled": override.get("enabled"), "auto_start": override.get("auto_start"),
                       **inspect_entry(root, str(plugin.get("entry") or ""))})
    return result


def runtime_plugins(port, timeout, auth):
    result = http_json("GET", f"http://127.0.0.1:{port}/plugins", timeout, auth)
    payload = result.get("json")
    items = payload.get("plugins") if isinstance(payload, dict) else payload
    items = list(items.values()) if isinstance(items, dict) else items
    if result.get("status") != 200 or not isinstance(items, list):
        return {"available": False, "status": result.get("status"),
                "reason": result.get("reason") or "plugins_shape_unrecognized"}
    out = []
    for item in items[:40]:
        if not isinstance(item, dict):
            continue
        rec = {k: item.get(k) for k in ("plugin_id", "id", "name", "version", "status", "enabled") if item.get(k) is not None}
        for key in ("entries", "actions", "tools"):
            value = item.get(key)
            if isinstance(value, list):
                rec[key] = [str(x.get("id") or x.get("name")) if isinstance(x, dict) else str(x) for x in value[:40]]
            elif isinstance(value, dict):
                rec[key] = list(value.keys())[:40]
        out.append(clean(rec))
    return {"available": True, "count": len(items), "plugins": out}


def launch_host(plugin_port):
    if os.name != "nt":
        return False, {"reason": "windows_only"}
    if not HOST_EXE.is_file():
        return False, {"reason": "verified_host_exe_missing"}
    if processes()["running"] or tcp_open(plugin_port):
        return False, {"reason": "already_running"}
    try:
        proc = subprocess.Popen([str(HOST_EXE)], cwd=str(HOST_EXE.parent),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, {"reason": f"launch_error:{type(exc).__name__}"}
    started = time.monotonic()
    while time.monotonic() - started < 60:
        if processes()["running"] and tcp_open(plugin_port):
            return True, {"pid": proc.pid, "waited_seconds": round(time.monotonic() - started, 1)}
        if proc.poll() not in (None, 0):
            return False, {"reason": "host_exited_early", "exit_code": proc.poll()}
        time.sleep(1)
    return False, {"reason": "launch_timeout_60s", "pid": proc.pid}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only N.E.K.O host acceptance probe (kards_companion)")
    ap.add_argument("--probe", action="store_true", help="read-only probe (default)")
    ap.add_argument("--launch-host", action="store_true", help="launch verified host exe, wait <= 60s")
    ap.add_argument("--diagnostics", action="store_true", help="call only monitor_status/monitor_diagnostics")
    ap.add_argument("--plugin-id", default=None)
    ap.add_argument("--timeout", type=float, default=2.0)
    args = ap.parse_args(argv)
    auth, port_info, launch = {"used": False}, ports(), {"requested": False, "launched": False}
    exit_code = 0
    if args.launch_host:
        launch["requested"] = True
        ok, detail = launch_host(port_info["plugin"])
        launch.update(detail, launched=ok)
        exit_code = 0 if ok else 4
    state = processes()
    main_open, plugin_open = tcp_open(port_info["main"]), tcp_open(port_info["plugin"])
    openapi, specs = {}, []
    for label, port, is_open in (("plugin_server", port_info["plugin"], plugin_open),
                                 ("main_server", port_info["main"], main_open)):
        if is_open:
            openapi[label], spec = get_openapi(port, args.timeout, auth)
            if spec:
                specs.append((f"http://127.0.0.1:{port}", spec))
    scanned = scan_plugins(jread(OVERRIDES) or {})
    enabled = [p["id"] for p in scanned if p.get("installed") and p.get("enabled") is True]
    plugin_id = args.plugin_id or (enabled[0] if enabled else "kards_companion_dev")
    runtime = runtime_plugins(port_info["plugin"], args.timeout, auth) if plugin_open \
        else {"available": False, "reason": "plugin_port_closed"}
    selected = next((p for p in scanned if p.get("id") == plugin_id), {})
    diagnostics = {"requested": bool(args.diagnostics), "attempted": False, "status": "not_requested"}
    if args.diagnostics:
        if not state["running"]:
            diagnostics.update(status="blocked", reason="host_not_running")
        elif not specs:
            diagnostics.update(status="blocked", reason="openapi_unavailable")
        else:
            diagnostics["attempted"] = True
            results, failures = {}, []
            for base, spec in specs:
                for action in READ_ONLY_ACTIONS:
                    if action not in results:
                        outcome = invoke_readonly(base, spec, plugin_id, action, args.timeout, auth)
                        if outcome.get("status") == "completed":
                            results[action] = {"route": outcome["route"],
                                               "summary": summarize(outcome["payload"], action == "monitor_diagnostics")}
                        else:
                            failures.append({"action": action, "reason": outcome.get("reason")})
            diagnostics.update(results=clean(results), failures=clean(failures))
            diagnostics["status"] = "completed" if len(results) == len(READ_ONLY_ACTIONS) else "unconfirmed"
            if diagnostics["status"] != "completed":
                diagnostics["reason"] = "read_only_action_route_unconfirmed"
                if failures and all(str(f.get("reason", "")).startswith("hosted_ui") for f in failures):
                    diagnostics["status"] = "blocked"
                exit_code = exit_code or 3
    reasons = []
    if not state["running"]:
        reasons.append("host_not_running")
    if not plugin_open:
        reasons.append("plugin_port_closed")
    if len(enabled) > 1:
        reasons.append("multiple_candidates_enabled")
    if not any(p.get("installed") for p in scanned):
        reasons.append("plugin_not_installed")
    if any(p.get("installed") for p in scanned) and not enabled:
        reasons.append("no_candidate_enabled")
    if state["running"] and plugin_open and not any(s.get("available") for s in openapi.values()) and not runtime.get("available"):
        reasons.append("runtime_api_unverified")
    if exit_code == 0 and (not state["running"] or not plugin_open):
        exit_code = 2
    if exit_code == 0 and any(r in reasons for r in ("runtime_api_unverified", "plugin_not_installed", "no_candidate_enabled")):
        exit_code = 3
    report = {
        "schema": "neko-host-acceptance/1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "launch+probe" if args.launch_host else "probe", "read_only": not args.launch_host,
        "policy": {"external_network": False, "game_input": False, "launched_host": launch["launched"],
                   "bridge_auth_used": auth["used"], "bridge_auth_persisted": False},
        "host": {"verified_exe": {"path": str(HOST_EXE), "exists": HOST_EXE.is_file()},
                 "backend_exists": HOST_BACKEND.is_file(), "processes": state, "launch": launch},
        "ports": {"source": port_info["source"], "main": port_info["main"], "plugin": port_info["plugin"],
                  "defaults_used": port_info["defaults_used"], "main_open": main_open, "plugin_open": plugin_open},
        "openapi": openapi or {"discovered": False},
        "plugins": {"scan_root": str(PLUGIN_ROOT), "enabled_state_source": str(OVERRIDES),
                    "candidates": scanned, "enabled_candidates": enabled, "selected_plugin_id": plugin_id,
                    "selected_registration": {**{k: selected.get(k) for k in
                                                 ("version", "ui_actions", "entries", "llm_tools", "source_sha256_16")},
                                              "registration_source": "installed_static_source"},
                    "runtime": runtime},
        "diagnostics": diagnostics, "reasons": reasons,
        "manual_actions": [
            "Confirm the live action route in /openapi.json before invoking beyond monitor_status/monitor_diagnostics.",
            "Test-say is future integration work; proposed monitor_test_say may change with the real action contract.",
            "A submitted test-say is local submission evidence only; never report it as played/audible.",
            "Enable only one of kards_companion / kards_companion_dev to avoid duplicate entries.",
            "Do not start KARDS/Steam or synthesize game input during host acceptance."],
        "limitations": [
            "Only --launch-host starts the host; KARDS/Steam and game input are never started.",
            "Only observed GET /openapi.json and GET /plugins are assumed; action routes are discovered dynamically.",
            "Entry/tool/UI registration is a static decorator scan of the installed source.",
            "Raw config, bridge credentials, screenshots, user chat, and prompts are omitted.",
            "Legacy Documents directories and old v5 keys are not read."],
        "ok": exit_code == 0, "exit_code": exit_code,
    }
    print(json.dumps(clean(report), ensure_ascii=False, indent=2))
    print(f"[host-acceptance] host={'running' if state['running'] else 'stopped'} "
          f"plugin_port={port_info['plugin']} open={plugin_open} runtime_plugins={runtime.get('available')} "
          f"diagnostics={diagnostics['status']} exit={exit_code}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
