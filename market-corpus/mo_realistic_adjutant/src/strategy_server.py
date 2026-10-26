from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .strategy_library import StrategyLibrary
from .strategy_blocks import BlockCompileError, compile_program
from .unit_threat import classify_unit_threat
from .block_registry import block_registry


_TARGET_CATALOG_CACHE: list[dict[str, Any]] | None = None


def _target_catalog() -> list[dict[str, Any]]:
    """Expose a compact Chinese target picker sourced from the unit registry."""
    global _TARGET_CATALOG_CACHE
    if _TARGET_CATALOG_CACHE is not None:
        return _TARGET_CATALOG_CACHE
    owners = {
        "UnitedStates": "美国", "Europeans": "欧洲联盟", "Pacific": "太平洋阵线",
        "USSR": "苏俄", "Latin": "拉丁同盟", "Chinese": "中国",
        "PsiCorps": "心灵军团", "Headquaters": "厄普西隆总部", "ScorpionCell": "天蝎组织",
        "Guild1": "焚风子阵营一", "Guild2": "焚风子阵营二", "Guild3": "焚风子阵营三",
    }
    source = Path(__file__).with_name("unit_catalog.json")
    aliases_path = Path(__file__).with_name("player_aliases.json")
    try:
        aliases_payload = json.loads(aliases_path.read_text(encoding="utf-8"))
        aliases = aliases_payload.get("aliases") if isinstance(aliases_payload, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        aliases = {}
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    result: list[dict[str, Any]] = []
    for unit in (raw.get("units") or {}).values():
        if not isinstance(unit, dict) or not unit.get("type_id"):
            continue
        kind = str(unit.get("kind") or "")
        movement = str(unit.get("movement_zone") or "")
        if kind == "building":
            group = "建筑"
        elif kind == "infantry":
            group = "步兵"
        elif kind == "aircraft":
            group = "飞机"
        elif kind == "naval" or "Water" in movement:
            group = "海军"
        elif kind == "vehicle":
            group = "装甲"
        else:
            continue
        owner_values = unit.get("owners") if isinstance(unit.get("owners"), list) else []
        threat = classify_unit_threat(unit)
        raw_name = str(unit.get("display_name") or "")
        alias_values = aliases.get(str(unit["type_id"]).upper(), []) if isinstance(aliases, dict) else []
        name_zh = raw_name if raw_name and "�" not in raw_name else (str(alias_values[0]) if alias_values else str(unit.get("internal_name") or unit["type_id"]))
        result.append({
            "id": str(unit["type_id"]),
            "name_zh": name_zh,
            "group": group,
            "factions": [owners.get(str(owner), str(owner)) for owner in owner_values],
            "tech_level": int(unit.get("tech_level") or 0),
            "role": str(unit.get("role") or ""),
            "building_class": str(unit.get("building_class") or ""),
            "battlefield_class": str(threat.get("battlefield_class") or ""),
            "threat_level": str(threat.get("threat_level") or ""),
            "threat_tags": list(threat.get("threat_tags") or []),
            "is_plug": bool(unit.get("is_plug")),
            "powers_up_building": str(unit.get("powers_up_building") or ""),
            "deploys_into": str(unit.get("deploys_into") or ""),
            "passengers": unit.get("passengers"),
        })
    result.sort(key=lambda item: (item["group"], item["name_zh"], item["id"]))
    _TARGET_CATALOG_CACHE = result
    return result


class StrategyLibraryServer:
    """Local-only editor/API server for the strategy library."""

    def __init__(self, library: StrategyLibrary, host: str = "127.0.0.1", port: int = 8765):
        self.library = library
        self.host = host
        self.port = int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        if self._server is not None:
            return self.port
        library = self.library
        # The editor may be opened either from the local server or directly as
        # a file in the installed plugin directory. This server is loopback-only.
        cors_origin = "*"

        class Handler(BaseHTTPRequestHandler):
            server_version = "MO-Strategy-Library/1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, payload: Any, content_type: str = "application/json; charset=utf-8") -> None:
                body = payload if isinstance(payload, bytes) else (
                    json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    if content_type.startswith("application/json") else str(payload).encode("utf-8")
                )
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.end_headers()
                self.wfile.write(body)

            def _json_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 2_000_000:
                    raise ValueError("request body is too large")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("request body must be an object")
                return value

            def do_OPTIONS(self) -> None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = unquote(parsed.path)
                try:
                    if path == "/api/health":
                        self._send(HTTPStatus.OK, {"ok": True, "port": self.server.server_address[1]})
                        return
                    if path == "/api/categories":
                        self._send(HTTPStatus.OK, {"categories": library.categories()})
                        return
                    if path == "/api/target-catalog":
                        self._send(HTTPStatus.OK, {"units": _target_catalog()})
                        return
                    if path == "/api/block-registry":
                        self._send(HTTPStatus.OK, block_registry())
                        return
                    if path == "/api/snapshot":
                        self._send(HTTPStatus.OK, library.runtime_snapshot())
                        return
                    if path == "/api/strategies":
                        query = parse_qs(parsed.query)
                        value = library.list_strategies(
                            scope=(query.get("scope") or [""])[0],
                            status=(query.get("status") or [""])[0],
                            category=(query.get("category") or [""])[0],
                            search=(query.get("search") or [""])[0],
                            limit=int((query.get("limit") or [200])[0]),
                            offset=int((query.get("offset") or [0])[0]),
                        )
                        self._send(HTTPStatus.OK, value)
                        return
                    prefix = "/api/strategies/"
                    if path.startswith(prefix):
                        strategy = library.get_strategy(path[len(prefix):])
                        if strategy is None:
                            self._send(HTTPStatus.NOT_FOUND, {"error": "strategy_not_found"})
                        else:
                            self._send(HTTPStatus.OK, strategy)
                        return
                    if path in {"/", "/策略条款编辑器.html"}:
                        html = Path(__file__).with_name("策略条款编辑器.html").read_bytes()
                        self._send(HTTPStatus.OK, html, "text/html; charset=utf-8")
                        return
                    if path == "/策略积木编辑器.css":
                        css = Path(__file__).with_name("策略积木编辑器.css").read_bytes()
                        self._send(HTTPStatus.OK, css, "text/css; charset=utf-8")
                        return
                    if path == "/策略积木编辑器.js":
                        script = Path(__file__).with_name("策略积木编辑器.js").read_bytes()
                        self._send(HTTPStatus.OK, script, "text/javascript; charset=utf-8")
                        return
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = unquote(parsed.path)
                try:
                    body = self._json_body()
                    if path == "/api/blocks/compile":
                        self._send(HTTPStatus.OK, compile_program(body))
                        return
                    if path == "/api/strategies":
                        self._send(HTTPStatus.OK, library.save_strategy(body))
                        return
                    if path.endswith("/promote"):
                        identifier = path[len("/api/strategies/"):-len("/promote")].strip("/")
                        self._send(HTTPStatus.OK, library.promote(identifier, body.get("category_path")))
                        return
                    if path.endswith("/enable"):
                        identifier = path[len("/api/strategies/"):-len("/enable")].strip("/")
                        self._send(HTTPStatus.OK, library.set_enabled(
                            identifier, bool(body.get("enabled", True)), body.get("usable_in_match")
                        ))
                        return
                    if path.endswith("/result"):
                        identifier = path[len("/api/strategies/"):-len("/result")].strip("/")
                        library.record_result(
                            identifier, str(body.get("outcome") or "unknown"),
                            failure_layer=str(body.get("failure_layer") or ""),
                            metrics=body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
                            context=body.get("context") if isinstance(body.get("context"), dict) else {},
                        )
                        self._send(HTTPStatus.OK, {"ok": True})
                        return
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            def do_PUT(self) -> None:
                path = unquote(urlparse(self.path).path)
                prefix = "/api/strategies/"
                if not path.startswith(prefix):
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                try:
                    self._send(HTTPStatus.OK, library.save_strategy(self._json_body(), path[len(prefix):]))
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            def do_DELETE(self) -> None:
                path = unquote(urlparse(self.path).path)
                prefix = "/api/strategies/"
                if not path.startswith(prefix):
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                try:
                    library.delete(path[len(prefix):])
                    self._send(HTTPStatus.OK, {"ok": True})
                except KeyError as exc:
                    self._send(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                except Exception as exc:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(target=server.serve_forever, name="mo-strategy-library", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
