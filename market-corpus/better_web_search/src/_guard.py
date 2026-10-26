"""URL validation for the user-supplied ``fetch`` entry.

``fetch`` dereferences a URL that the model chose, so it must not be allowed to
probe the local network, cloud metadata endpoints, or the user's own machine.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
from collections.abc import Sequence

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_URL_LENGTH = 4096

_LOCAL_HOSTS = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
})
_BLOCKED_HOST_SUFFIXES = (
    ".local",
    ".internal",
    ".localdomain",
    ".home.arpa",
    ".localhost",
)
_DATA_LIKE_HOSTS = frozenset({
    "0.0.0.0",
    "169.254.169.254",
    "metadata.google.internal",
})

# Hosts that only look external but resolve into private space by convention.
_BLOCKED_EXACT = frozenset({"0", "0.0.0.0", "127.0.0.1", "::", "::1"})


class UnsafeUrlError(ValueError):
    """The URL is malformed or points somewhere ``fetch`` must not reach."""


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = host.strip().strip("[]")
    if not candidate:
        return None
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    # Decimal / octal / hexadecimal integer forms of IPv4 loopback, e.g.
    # http://2130706433/ or http://0x7f000001/ must not slip past the check.
    if re.fullmatch(r"[0-9]+", candidate):
        try:
            value = int(candidate)
        except ValueError:
            return None
        if 0 <= value <= 0xFFFFFFFF:
            return ipaddress.ip_address(value)
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", candidate):
        try:
            return ipaddress.ip_address(int(candidate, 16))
        except ValueError:
            return None
    return None


def _is_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _parse_allow_ranges(
    allow_ranges: Sequence[str],
) -> tuple[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str], ...]:
    """Parse configured DNS-result exceptions, ignoring malformed entries."""
    parsed_ranges = []
    for item in allow_ranges:
        try:
            network = ipaddress.ip_network(item, strict=False)
        except (TypeError, ValueError):
            continue
        parsed_ranges.append((network, str(network)))
    return tuple(parsed_ranges)


def _matching_allow_range(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    allow_ranges: tuple[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str], ...],
) -> str:
    for network, label in allow_ranges:
        try:
            if address in network:
                return label
        except TypeError:
            # IPv4 and IPv6 networks are both valid configuration, but a
            # network of the other family cannot contain this address.
            continue
    return ""


def _validate_http_url(
    raw: object,
    *,
    allow_ranges: Sequence[str] = (),
) -> tuple[str, str, str]:
    """Validate a URL and return ``(normalised, matched_range, resolved_ip)``."""
    text = str(raw or "").strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise UnsafeUrlError("URL 为空或过长")
    if any(ord(char) < 0x21 or char.isspace() for char in text):
        raise UnsafeUrlError("URL 含非法字符")

    if "//" not in text and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text):
        text = "https://" + text

    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError as error:
        raise UnsafeUrlError("URL 无法解析") from error

    scheme = parsed.scheme.casefold()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"只允许 http/https，收到 {scheme or '空协议'}")
    if parsed.password:
        raise UnsafeUrlError("URL 不允许携带密码")

    host = (parsed.hostname or "").strip().rstrip(".").casefold()
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")
    if host in _LOCAL_HOSTS or host in _DATA_LIKE_HOSTS or host in _BLOCKED_EXACT:
        raise UnsafeUrlError("目标是本地或元数据地址")
    if host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeUrlError("目标是内部域名")
    if re.fullmatch(r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}", host):
        raise UnsafeUrlError("目标是内网地址")

    literal = _literal_ip(host)
    if literal is not None:
        # Explicit IP targets never consult allow_ranges.  This keeps a
        # configured fake-IP exception limited to DNS answers only.
        if _is_blocked_ip(literal):
            raise UnsafeUrlError("目标 IP 属于本地/内网/保留地址段")
        resolved_ip = str(literal)
        matched_range = ""
    else:
        # Resolve before connecting: a public-looking hostname may point at a
        # private address (DNS rebinding against the fetch entry).
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            detail = getattr(error, "strerror", "")
            if not detail:
                detail = "未知原因"
            raise UnsafeUrlError(f"域名解析失败: {detail}") from error

        configured_ranges = _parse_allow_ranges(allow_ranges)
        resolved_ip = ""
        matched_range = ""
        for info in infos:
            address_text = str(info[4][0]).split("%")[0]
            try:
                address = ipaddress.ip_address(address_text)
            except ValueError:
                continue
            if not resolved_ip:
                resolved_ip = address_text
            if _is_blocked_ip(address):
                matched = _matching_allow_range(address, configured_ranges)
                if matched:
                    matched_range = matched_range or matched
                    continue
                raise UnsafeUrlError("域名解析到了本地/内网地址")

    rebuilt = parsed._replace(scheme=scheme)
    return urllib.parse.urlunsplit(rebuilt), matched_range, resolved_ip


def normalize_http_url(raw: object, *, allow_ranges: Sequence[str] = ()) -> str:
    """Validate ``raw`` and return an absolute http(s) URL string."""
    normalized, _, _ = _validate_http_url(raw, allow_ranges=allow_ranges)
    return normalized


def is_http_url(raw: object, *, allow_ranges: Sequence[str] = ()) -> bool:
    try:
        normalize_http_url(raw, allow_ranges=allow_ranges)
    except UnsafeUrlError:
        return False
    return True


def explain_decision(raw: object, *, allow_ranges: Sequence[str] = ()) -> dict[str, object]:
    """Return a stable, side-effect-free explanation of URL validation."""
    try:
        _, matched_range, resolved_ip = _validate_http_url(raw, allow_ranges=allow_ranges)
    except UnsafeUrlError as error:
        return {"ok": False, "reason": str(error), "matched_range": "", "ip": ""}
    return {"ok": True, "reason": "allowed", "matched_range": matched_range, "ip": resolved_ip}
