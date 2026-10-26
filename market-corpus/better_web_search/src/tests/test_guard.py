"""Offline SSRF guard tests, including synthetic DNS answers."""

from __future__ import annotations

import socket

import conftest
import pytest

guard = conftest.load("_guard")


FAKE_IP = "198.18.1.7"
FAKE_RANGE = "198.18.0.0/15"


def fake_getaddrinfo(*args, **kwargs):
    """Return a Clash/mihomo-style synthetic IPv4 DNS answer."""
    port = args[1] if len(args) > 1 else 443
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (FAKE_IP, port))]


def test_domain_fake_ip_is_allowed_only_with_configured_range(monkeypatch) -> None:
    monkeypatch.setattr(guard.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(guard.UnsafeUrlError):
        guard.normalize_http_url("https://example.com/")
    assert guard.normalize_http_url("https://example.com/", allow_ranges=[FAKE_RANGE]) == "https://example.com/"
    assert guard.is_http_url("https://example.com/", allow_ranges=[FAKE_RANGE])


def test_explain_decision_reports_fake_ip_and_matching_range(monkeypatch) -> None:
    monkeypatch.setattr(guard.socket, "getaddrinfo", fake_getaddrinfo)

    decision = guard.explain_decision("https://example.com/", allow_ranges=[FAKE_RANGE])

    assert decision == {
        "ok": True,
        "reason": "allowed",
        "matched_range": FAKE_RANGE,
        "ip": FAKE_IP,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/",
        "http://[fd00::1]/",
        "http://x.local/",
        "http://10.1.2.3/",
        "http://192.168.1.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "javascript:alert(1)",
        "ftp://x/",
        "https://example.com/" + "a" * 4096,
        "http://user:password@example.com/",
        "http://[::1]/",
    ],
)
def test_local_private_and_malformed_targets_remain_blocked(url: str) -> None:
    with pytest.raises(guard.UnsafeUrlError):
        guard.normalize_http_url(url, allow_ranges=[FAKE_RANGE])


def test_literal_fake_ip_is_never_allowed_by_dns_exception() -> None:
    with pytest.raises(guard.UnsafeUrlError):
        guard.normalize_http_url("http://198.18.1.7/", allow_ranges=[FAKE_RANGE])


def test_invalid_allow_ranges_are_ignored(monkeypatch) -> None:
    monkeypatch.setattr(guard.socket, "getaddrinfo", fake_getaddrinfo)

    assert guard.normalize_http_url(
        "https://example.com/",
        allow_ranges=["not-a-cidr", "", FAKE_RANGE],
    ) == "https://example.com/"
    with pytest.raises(guard.UnsafeUrlError):
        guard.normalize_http_url("https://example.com/", allow_ranges=["not-a-cidr", ""])
