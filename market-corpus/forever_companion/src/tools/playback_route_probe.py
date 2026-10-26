"""Serve only a QA directory through the host's unmodified static-file handler.

Extract the two pure route functions without importing the host's application,
configuration, Store or lifecycle. This server is never used by the plugin.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from starlette.routing import Route


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    source = args.host / "plugin/server/routes/plugin_ui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"plugin_ui_file", "_get_mime_type"}:
            node.decorator_list = []
            selected.append(node)
    if len(selected) != 2:
        raise RuntimeError("host static route contract changed")

    async def static_dir(plugin_id: str):
        return directory if plugin_id == "forever_companion" else None

    async def config(_plugin_id: str):
        return {"cache_control": "private, no-cache, max-age=0, must-revalidate"}

    namespace = {
        "Path": Path, "HTTPException": HTTPException, "FileResponse": FileResponse,
        "mimetypes": mimetypes, "_get_plugin_static_dir": static_dir,
        "_get_static_ui_config": config, "ServerDomainError": type("ServerDomainError", (Exception,), {}),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)

    async def endpoint(request):
        return await namespace["plugin_ui_file"](**request.path_params)

    app = Starlette(routes=[Route("/plugin/{plugin_id}/ui/{file_path:path}", endpoint)])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": unquote(urlsplit(self.path).path),
                "query_string": b"", "root_path": "",
                "headers": [(key.lower().encode("ascii"), value.encode("latin-1")) for key, value in self.headers.items()],
            }

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                if message["type"] == "http.response.start":
                    self.send_response(message["status"])
                    for key, value in message["headers"]:
                        self.send_header(key.decode("ascii"), value.decode("latin-1"))
                    self.end_headers()
                elif message["type"] == "http.response.body":
                    self.wfile.write(message.get("body", b""))

            try:
                asyncio.run(app(scope, receive, send))
            except (ConnectionError, BrokenPipeError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(json.dumps({"port": server.server_port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
