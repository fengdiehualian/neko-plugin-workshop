"""NapCat 包下载的**续传**与**复用**。

镜像实测会掉速并中途断流 —— ``gh-proxy.com`` 同一天里从 16 MB/s 掉到 0.23 MB/s，
并且真的在下到 20MB 处断掉过一次（28MB 的包）。原实现里任何失败都 ``unlink`` 掉
``.part``、从零重来，在那个速率下等于永远下不完。

三条约定：
- 断流**保留** ``.part``，换个镜像接着下（带 Range）；
- **只有校验不过**才丢弃 —— 那说明攒起来的字节本身就是错的；
- 已有一份校验通过的正式包就直接用，不打网络（镜像不可用时这是唯一的路）。
"""
from __future__ import annotations

import hashlib

import pytest
from plugin.plugins.qq_auto_reply import napcat_install as ni

ASSET = "Fake.Shell.zip"
BODY = b"N" * 4096           # 假的"完整包"
HEAD = BODY[:1024]           # 下到 1/4 的样子
JUNK = b"j" * 4096           # 长度对、内容错


@pytest.fixture(autouse=True)
def _pin_fake_asset(monkeypatch):
    monkeypatch.setitem(
        ni.PINNED_ASSETS, ASSET, (len(BODY), hashlib.sha256(BODY).hexdigest()))


def _paths(tmp_path):
    return tmp_path / ASSET, tmp_path / (ASSET + ".part")


async def _no_network(*a, **k):
    raise AssertionError("不该打网络 —— 本地已经有可用的包了")


# ── 复用已下好的包 ──────────────────────────────────────────

async def test_reuses_an_already_verified_package(tmp_path, monkeypatch):
    final, _ = _paths(tmp_path)
    final.write_bytes(BODY)
    monkeypatch.setattr(ni, "_fetch", _no_network)

    assert await ni.download_asset(ASSET, tmp_path) == final


async def test_a_corrupt_existing_package_is_discarded(tmp_path, monkeypatch):
    """坏包不能留在原地反复被校验 —— 而且必须能重新下。"""
    final, _ = _paths(tmp_path)
    final.write_bytes(JUNK)
    monkeypatch.setattr(ni, "_fetch", _write_body)

    await ni.download_asset(ASSET, tmp_path)

    assert final.read_bytes() == BODY


# ── 续传 ────────────────────────────────────────────────────

async def test_resumes_from_the_partial_file(tmp_path, monkeypatch):
    """断点续传的核心：把已经下到的字节数交给 _fetch 当 Range 起点。"""
    _, part = _paths(tmp_path)
    part.write_bytes(HEAD)
    seen: list[int] = []

    async def _spy(url, dest, *, progress=None, timeout=0, fallback_total=0, resume_from=0):
        seen.append(resume_from)
        with open(dest, "ab") as f:
            f.write(BODY[resume_from:])

    monkeypatch.setattr(ni, "_fetch", _spy)

    final = await ni.download_asset(ASSET, tmp_path)

    assert seen == [len(HEAD)], "没有从已下到的位置接着下"
    assert final.read_bytes() == BODY


async def test_a_failed_mirror_keeps_the_partial_file(tmp_path, monkeypatch):
    """断流**不能**丢掉已经下到的那部分 —— 丢了就等于从零重来。"""
    final, part = _paths(tmp_path)
    part.write_bytes(HEAD)

    async def _drop(*a, **k):
        raise ConnectionError("代理断流")

    monkeypatch.setattr(ni, "_fetch", _drop)

    with pytest.raises(RuntimeError):
        await ni.download_asset(ASSET, tmp_path, mirrors=[""])

    assert part.read_bytes() == HEAD, "断流把已下的部分丢了"
    assert not final.exists()


async def test_the_next_mirror_continues_where_the_last_one_died(tmp_path, monkeypatch):
    """第一个镜像断流后，第二个镜像要**接着**下，而不是从 0 开始。"""
    _, part = _paths(tmp_path)
    part.write_bytes(HEAD)
    seen: list[tuple[str, int]] = []

    async def _first_dies(url, dest, *, progress=None, timeout=0, fallback_total=0, resume_from=0):
        seen.append((url, resume_from))
        raise ConnectionError("断流")

    monkeypatch.setattr(ni, "_fetch", _first_dies)

    with pytest.raises(RuntimeError):
        await ni.download_asset(ASSET, tmp_path, mirrors=["https://a.example/", "https://b.example/"])

    # candidate_urls 无论如何都会把官方源追加成最后一道，所以这里是 3 个候选
    assert len(seen) >= 2, "只试了一个源就不试了"
    assert all(r == len(HEAD) for _, r in seen), (
        f"有镜像从零重来了 —— 每次尝试都应该接着已下到的位置：{seen}")


async def test_checksum_failure_discards_the_partial_file(tmp_path, monkeypatch):
    """攒起来的字节本身是错的 —— 这时只能重来，留着它永远也验不过。"""
    _, part = _paths(tmp_path)
    part.write_bytes(HEAD)
    monkeypatch.setattr(ni, "_fetch", _write_junk)

    with pytest.raises(RuntimeError):
        await ni.download_asset(ASSET, tmp_path, mirrors=[""])

    assert not part.exists(), "坏数据该被丢掉"


# ── .part 已经攒满 ──────────────────────────────────────────

async def test_a_complete_part_is_promoted_without_network(tmp_path, monkeypatch):
    """上一轮可能已经下满、只是没走到提升 —— 别再打一遍网络。"""
    final, part = _paths(tmp_path)
    part.write_bytes(BODY)
    monkeypatch.setattr(ni, "_fetch", _no_network)

    assert await ni.download_asset(ASSET, tmp_path) == final
    assert not part.exists()


async def test_an_oversized_part_is_discarded(tmp_path, monkeypatch):
    """比钉死长度还长（换过版本？）—— 攒着也没用，只能重来。"""
    final, part = _paths(tmp_path)
    part.write_bytes(BODY + b"extra")
    monkeypatch.setattr(ni, "_fetch", _write_body)

    await ni.download_asset(ASSET, tmp_path)

    assert final.read_bytes() == BODY


# ── _fetch 自己的 Range 语义 ────────────────────────────────

class _Resp:
    def __init__(self, status, headers, chunks):
        self.status_code, self.headers, self._chunks = status, headers, chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size=0):
        for c in self._chunks:
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_client(monkeypatch, resp, seen):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, headers=None):
            seen["headers"] = headers
            seen["url"] = url
            return resp

    monkeypatch.setattr(ni.httpx, "AsyncClient", lambda **k: _Client())


async def test_fetch_sends_a_range_header_when_resuming(tmp_path, monkeypatch):
    dest = tmp_path / "out.bin"
    dest.write_bytes(HEAD)
    seen: dict = {}
    resp = _Resp(206, {"content-length": str(len(BODY) - len(HEAD))}, [BODY[len(HEAD):]])
    _fake_client(monkeypatch, resp, seen)

    await ni._fetch("https://m.example/x.zip", dest, progress=None, timeout=5,
                    fallback_total=len(BODY), resume_from=len(HEAD))

    assert seen["headers"] == {"Range": f"bytes={len(HEAD)}-"}
    assert dest.read_bytes() == BODY, "续传没有接在原文件后面"


async def test_fetch_truncates_when_the_server_ignores_range(tmp_path, monkeypatch):
    """服务端回 200（不认 Range）时必须**从头写**。

    追加的话会攒出一份"长度碰巧对得上、内容全错"的包 —— 那种文件最后卡在校验上，
    而且每次重试都重演。
    """
    dest = tmp_path / "out.bin"
    dest.write_bytes(HEAD)
    seen: dict = {}
    resp = _Resp(200, {"content-length": str(len(BODY))}, [BODY])
    _fake_client(monkeypatch, resp, seen)

    await ni._fetch("https://m.example/x.zip", dest, progress=None, timeout=5,
                    fallback_total=len(BODY), resume_from=len(HEAD))

    assert dest.read_bytes() == BODY, "把整包追加到半截文件后面了"


# ── 打桩用的小工具 ──────────────────────────────────────────

async def _write_body(url, dest, *, progress=None, timeout=0, fallback_total=0, resume_from=0):
    with open(dest, "wb") as f:
        f.write(BODY)


async def _write_junk(url, dest, *, progress=None, timeout=0, fallback_total=0, resume_from=0):
    with open(dest, "wb") as f:
        f.write(JUNK)
