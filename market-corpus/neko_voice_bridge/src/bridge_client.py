"""Small authenticated loopback client; no SDK internals or extra dependencies."""
import asyncio
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BridgeClient:
    def __init__(self, config):
        self.url = config["bridge_url"].rstrip("/")
        parsed = urlsplit(self.url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1")
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("BRIDGE_MUST_BE_LOOPBACK")
        self.token = config["bridge_token"]
        if not isinstance(self.token, str) or len(self.token) < 32:
            raise ValueError("BRIDGE_TOKEN_TOO_SHORT")

    def _request(self, path, data):
        req = Request(self.url + path, headers={"Authorization": "Bearer " + self.token,
                      "Content-Type": "application/json"},
                      data=None if data is None else json.dumps(data).encode())
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=50 if path == "/v1/reply" else 3) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("BRIDGE_RESPONSE_TOO_LARGE")
                return json.loads(raw)
        except HTTPError as exc:
            raise RuntimeError(f"BRIDGE_HTTP_{exc.code}") from None
        except (OSError, ValueError):
            raise RuntimeError("BRIDGE_UNAVAILABLE_OR_INVALID_RESPONSE") from None

    async def request(self, path, data=None):
        return await asyncio.to_thread(self._request, path, data)
