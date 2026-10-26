from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .shared_memory import NativeBridgeError, PROTOCOL_VERSION, SharedMemoryBridge


STATUS_MESSAGES = {
    1: "accepted",
    -1: "control is disabled",
    -2: "network or unsupported game mode",
    -3: "harvester was not found or is not controllable",
    -4: "target cell is invalid",
    -5: "action is invalid",
    -6: "target object was not found or is not visible",
    -7: "the selected unit cannot perform this action on that target",
}


class RealBridgeState:
    def __init__(self, native: SharedMemoryBridge) -> None:
        self.native = native
        self.lock = threading.Lock()
        self.emergency_latched = False


class RealBridgeHandler(BaseHTTPRequestHandler):
    server_version = "MOBridge/0.1"

    @property
    def bridge(self) -> RealBridgeState:
        return self.server.bridge_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        if self.path == "/v1/health":
            health = self.bridge.native.health()
            self._json(
                200,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "bridge_version": "0.1.0",
                    "game_connected": health.connected,
                    "native_protocol_version": health.native_protocol,
                    "control_enabled": health.control_active,
                    "last_command_status": health.last_status,
                    "last_error": health.last_error,
                    "capabilities": {
                        "telemetry": health.connected,
                        "commands": health.connected,
                        "network_commands": health.connected and health.native_protocol == 10,
                        "entity_query": health.connected,
                        "batch_group": health.connected,
                        "deploy": health.connected and health.native_protocol in {9, 10},
                        "explore": health.connected and health.native_protocol in {9, 10},
                        "auto_attack": health.connected and health.native_protocol in {9, 10},
                        "hunt": health.connected and health.native_protocol in {9, 10},
                        "economy_flow": health.connected,
                        "power": health.connected and health.power_telemetry,
                        "production": health.connected and health.production_telemetry,
                        "building_placement": health.connected and health.native_protocol == 10,
                        "support_powers": health.connected
                        and health.production_telemetry
                        and health.native_protocol == 10,
                    },
                },
            )
            return
        if self.path == "/v1/state":
            try:
                state = self.bridge.native.state()
            except NativeBridgeError as exc:
                self._json(503, self._error(str(exc)))
                return
            self._json(200, state)
            return
        self._json(404, self._error("not found"))

    def do_POST(self) -> None:
        if self.path == "/v1/entities/query":
            try:
                payload = self._read_json()
                self._json(200, self.bridge.native.query_entities(payload))
            except (ValueError, NativeBridgeError, TypeError) as exc:
                self._json(409, self._error(str(exc)))
            return

        if self.path == "/v1/session/control":
            try:
                payload = self._read_json()
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise NativeBridgeError("enabled must be a boolean")
                network_opt_in = payload.get("network_opt_in", False)
                if not isinstance(network_opt_in, bool):
                    raise NativeBridgeError("network_opt_in must be a boolean")
                try:
                    active = self.bridge.native.set_control(
                        enabled, network_opt_in=network_opt_in
                    )
                except TypeError:
                    # Keep development/test bridge doubles compatible while the
                    # native bridge is upgraded to understand the opt-in value.
                    active = self.bridge.native.set_control(enabled)
                if enabled and not active:
                    raise NativeBridgeError("native bridge refused control in this mode")
                with self.bridge.lock:
                    if enabled:
                        self.bridge.emergency_latched = False
                self._json(
                    200,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "control_enabled": active,
                        "network_opt_in": network_opt_in,
                    },
                )
            except (ValueError, NativeBridgeError) as exc:
                self._json(409, self._error(str(exc)))
            return

        if self.path in {"/v1/actions/harvester", "/v1/actions/unit"}:
            try:
                payload = self._read_json()
                with self.bridge.lock:
                    if self.bridge.emergency_latched:
                        raise NativeBridgeError("emergency stop is latched")
                status = self.bridge.native.command(payload)
                if status != 1:
                    raise NativeBridgeError(STATUS_MESSAGES.get(status, f"native status {status}"))
                self._json(
                    202,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "accepted": True,
                        "request_id": payload.get("request_id"),
                    },
                )
            except (ValueError, NativeBridgeError) as exc:
                self._json(409, self._error(str(exc)))
            return

        if self.path == "/v1/actions/group":
            try:
                payload = self._read_json()
                with self.bridge.lock:
                    if self.bridge.emergency_latched:
                        raise NativeBridgeError("emergency stop is latched")
                result = self.bridge.native.command_group(payload)
                status = result["status"]
                response = {
                    "protocol_version": PROTOCOL_VERSION,
                    "accepted": status == 1,
                    "request_id": payload.get("request_id"),
                    **result,
                }
                if status != 1:
                    response["error"] = STATUS_MESSAGES.get(
                        status, f"native status {status}"
                    )
                self._json(202 if status == 1 else 200, response)
            except (ValueError, NativeBridgeError) as exc:
                self._json(409, self._error(str(exc)))
            return

        if self.path == "/v1/actions/production":
            try:
                payload = self._read_json()
                with self.bridge.lock:
                    if self.bridge.emergency_latched:
                        raise NativeBridgeError("emergency stop is latched")
                result = self.bridge.native.command_production(payload)
                status = result["status"]
                response = {
                    "protocol_version": PROTOCOL_VERSION,
                    "accepted": status == 1 and result["accepted_count"] > 0,
                    "request_id": payload.get("request_id"),
                    "type_id": payload.get("type_id"),
                    "requested_count": payload.get("quantity"),
                    **result,
                }
                if status != 1:
                    response["error"] = STATUS_MESSAGES.get(
                        status, f"native status {status}"
                    )
                self._json(202 if response["accepted"] else 200, response)
            except (ValueError, NativeBridgeError) as exc:
                self._json(409, self._error(str(exc)))
            return

        if self.path == "/v1/actions/building-placement":
            try:
                payload = self._read_json()
                with self.bridge.lock:
                    if self.bridge.emergency_latched:
                        raise NativeBridgeError("emergency stop is latched")
                result = self.bridge.native.command_building_placement(payload)
                status = result["status"]
                response = {
                    "protocol_version": PROTOCOL_VERSION,
                    "accepted": status == 1 and result["accepted_count"] > 0,
                    "request_id": payload.get("request_id"),
                    "type_id": payload.get("type_id"),
                    "target_cell": payload.get("target_cell"),
                    **result,
                }
                if status != 1:
                    response["error"] = result.get("last_error") or STATUS_MESSAGES.get(
                        status, f"native status {status}"
                    )
                self._json(202 if response["accepted"] else 200, response)
            except (ValueError, NativeBridgeError) as exc:
                self._json(409, self._error(str(exc)))
            return

        if self.path == "/v1/actions/support-power":
            try:
                payload = self._read_json()
                with self.bridge.lock:
                    if self.bridge.emergency_latched:
                        raise NativeBridgeError("emergency stop is latched")
                result = self.bridge.native.command_support_power(payload)
                status = result["status"]
                response = {
                    "protocol_version": PROTOCOL_VERSION,
                    "accepted": status == 1 and result["accepted_count"] > 0,
                    "request_id": payload.get("request_id"),
                    "type_id": payload.get("type_id"),
                    "target_cell": payload.get("target_cell"),
                    **result,
                }
                if status != 1:
                    response["error"] = result.get("last_error") or STATUS_MESSAGES.get(
                        status, f"native status {status}"
                    )
                self._json(202 if response["accepted"] else 200, response)
            except (ValueError, NativeBridgeError) as exc:
                self._json(409, self._error(str(exc)))
            return

        if self.path == "/v1/emergency-stop":
            with self.bridge.lock:
                self.bridge.emergency_latched = True
            warning = None
            try:
                self.bridge.native.set_control(False)
            except NativeBridgeError as exc:
                warning = str(exc)
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "stopped": True,
            }
            if warning:
                response["warning"] = warning
            self._json(200, response)
            return

        self._json(404, self._error("not found"))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0 or length > 65536:
            raise ValueError("invalid request body size")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request must be a JSON object")
        return value

    def _json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"protocol_version": PROTOCOL_VERSION, "error": message}


def create_server(
    host: str = "127.0.0.1",
    port: int = 48970,
    native: SharedMemoryBridge | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), RealBridgeHandler)
    server.bridge_state = RealBridgeState(native or SharedMemoryBridge())  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real MO bridge HTTP adapter")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=48970, type=int)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("bridge may only bind to localhost")
    server = create_server(args.host, args.port)
    print(f"MO bridge listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.bridge_state.native.close()  # type: ignore[attr-defined]
        server.server_close()


if __name__ == "__main__":
    main()
