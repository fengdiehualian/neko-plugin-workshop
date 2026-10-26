"""QQ 开放平台富媒体：图片上传（两条协议）与单聊发图。

这轮补的三件事之一。以前开放平台**只有群聊能发图**，单聊那条路把图写成
`[图片]` 三个字发出去 —— 而官方 v2 明确有「单聊富媒体上传」
（`POST /v2/users/{user_openid}/files`），也就是"两边能力差一截"纯粹是本仓库
只实现了群那一半。

两条协议并存这件事是**官方文档现状**逼出来的：

* 本仓库群聊用的是**旧式直传**（`{file_type, file_name, file_size, mime_type}`
  → `upload_url` → PUT → `file_info`）；
* 当前 wiki 只列 **URL 上传**（给平台一个 http 地址）与 **分片上传**
  （`upload_prepare` → 逐片 PUT + `upload_part_finish` → 带 `upload_id` 合并）。

旧式那条到底还算不算数，本地没有开放平台凭据**验证不了**，所以实现是"两条都试、
谁成谁赢、日志写清哪条成的"。这些测试钉的就是这个顺序与各条请求的**形状**：
形状错了，真机上只会得到一句"上传未拿到 file_info"，而那是静默降级的开始。
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import connector_seam, media_seam
from plugin.plugins.qq_auto_reply._vendor.connection_onebot import (
    qq_open_platform_media as MEDIA,
)


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        """照 httpx 的行为：4xx/5xx 抛异常，而不是被当成一个能用的响应。"""
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHTTP:
    """按 ``responder(method, url, body)`` 出响应，并记录每一次调用。

    responder 可以直接返回 ``_Response`` —— 需要造非 2xx 时（片 PUT 被拒、
    ``upload_part_finish`` 失败）用得上。
    """

    def __init__(self, responder):
        self._responder = responder
        self.calls: list[tuple[str, str, object]] = []

    def _answer(self, method, url, body) -> _Response:
        raw = self._responder(method, url, body)
        return raw if isinstance(raw, _Response) else _Response(raw)

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json))
        return self._answer("POST", url, json)

    async def put(self, url, content=None, headers=None):
        self.calls.append(("PUT", url, content))
        return self._answer("PUT", url, content)

    def posts(self):
        return [(url, body) for method, url, body in self.calls if method == "POST"]

    def puts(self):
        return [(url, body) for method, url, body in self.calls if method == "PUT"]


class _Conn:
    """只带富媒体函数需要的那几个成员（与漂移守卫测的集合一致）。"""

    CHANNEL = "open"
    mode = "open_platform"

    def __init__(self, responder):
        self._http = _FakeHTTP(responder)
        self._API_BASE = "https://api.example"
        self.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None,
        )
        self.token_calls = 0
        self.recorded: list[str] = []

    async def _ensure_token(self):
        self.token_calls += 1

    def _auth_headers(self):
        return {"Authorization": "QQBot fake"}

    def record_sent_message_id(self, mid):
        self.recorded.append(str(mid))


def _legacy_ok(**_):
    """旧式直传成功。"""
    return {"upload_url": "https://cos.example/put/1", "file_info": "FI-legacy"}


def _legacy_then_nothing(method, url, body):
    """旧式直传拿不到 upload_url；分片那条一切正常（两片，覆盖整个 16 字节文件）。"""
    if method == "PUT":
        return {"file_info": "FI-legacy"}
    if url.endswith("/upload_prepare"):
        return {
            "upload_id": "upload_1",
            "block_size": "8",
            "parts": [
                {"index": 0, "presigned_url": "https://cos.example/part/0", "block_size": "8"},
                {"index": 1, "presigned_url": "https://cos.example/part/1", "block_size": "8"},
            ],
        }
    if url.endswith("/upload_part_finish"):
        return {}
    if url.endswith("/files") and body.get("upload_id"):
        return {"file_info": "FI-chunked"}
    if url.endswith("/files"):
        return {}  # 旧式直传：不吐 upload_url
    return {}


def _documented_chunked(method, url, body):
    if method == "POST" and url.endswith("/upload_prepare"):
        return {
            "upload_id": "upload_1",
            "block_size": "8",
            "parts": [{"index": 0, "presigned_url": "https://cos.example/part/0", "block_size": "8"}],
        }
    if method == "POST" and url.endswith("/upload_part_finish"):
        return {}
    if method == "PUT":
        return {}
    if method == "POST" and url.endswith("/files"):
        assert body.get("upload_id") == "upload_1", "合并那一步必须带 upload_id"
        return {"file_info": "FI-chunked"}
    return {}


# ── URL 上传：平台自己去下载，本地不碰字节 ──────────────────────────────

def test_a_remote_url_goes_through_the_documented_url_upload():
    def responder(method, url, body):
        if url.endswith("/messages"):
            return {"id": "MID-1"}
        return {"file_info": "FI-url"}

    conn = _Conn(responder)

    message_id = _run(MEDIA.send_private_image(
        conn, "USER1", "https://cdn.example/a.png", content="给你看",
    ))

    posts = conn._http.posts()
    assert posts[0] == (
        "https://api.example/v2/users/USER1/files",
        {"file_type": 1, "url": "https://cdn.example/a.png", "srv_send_msg": False},
    ), f"URL 上传的请求体不对: {posts[0]!r}"
    assert posts[1][0] == "https://api.example/v2/users/USER1/messages"
    assert posts[1][1] == {"msg_type": 7, "media": {"file_info": "FI-url"}, "content": "给你看"}
    # 没有字节要传：URL 上传不该出现 PUT
    assert conn._http.puts() == []
    assert message_id == "MID-1"
    # 回执要记账：上层用 record_sent_message_id 认"这条是我发的"（引用/反应靠它）
    assert conn.recorded == ["MID-1"]


def test_the_scope_is_users_for_private_and_groups_for_group():
    """平台把两个上传入口分开：单聊接口传的只能发单聊（文档原话），反之亦然。"""
    private_conn = _Conn(lambda method, url, body: {"file_info": "FI"})
    _run(MEDIA.send_private_image(private_conn, "U1", "https://cdn.example/a.png"))
    assert private_conn._http.posts()[0][0].startswith("https://api.example/v2/users/U1/")

    group_conn = _Conn(lambda method, url, body: {"file_info": "FI"})
    _run(MEDIA.upload_image(group_conn, scope="groups", owner_id="G1", source="https://cdn.example/a.png"))
    assert group_conn._http.posts()[0][0].startswith("https://api.example/v2/groups/G1/")


# ── 本地文件：旧式直传优先，失败再走文档的分片 ──────────────────────────

def test_a_local_file_tries_the_legacy_direct_upload_first(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"x" * 32)
    conn = _Conn(lambda method, url, body: _legacy_ok() if method == "POST" else {"file_info": "FI-legacy"})

    file_info = _run(MEDIA.upload_image(conn, scope="groups", owner_id="G1", source=str(sticker)))

    assert file_info == "FI-legacy"
    urls = [url for url, _ in conn._http.posts()]
    assert urls[0].endswith("/v2/groups/G1/files")
    assert not any(u.endswith("/upload_prepare") for u in urls), "旧式直传成功时不该再试分片"
    assert conn._http.puts()[0][0] == "https://cos.example/put/1"


def test_a_local_file_falls_back_to_the_documented_chunked_upload(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)
    conn = _Conn(_legacy_then_nothing)

    file_info = _run(MEDIA.upload_image(conn, scope="users", owner_id="U1", source=str(sticker)))

    assert file_info == "FI-chunked", "旧式直传拿不到 file_info 时必须走分片那条"
    urls = [url for url, _ in conn._http.posts()]
    assert urls[0].endswith("/v2/users/U1/files")           # 旧式直传
    assert urls[1].endswith("/v2/users/U1/upload_prepare")  # 分片第一步
    assert urls[2].endswith("/v2/users/U1/upload_part_finish")
    assert urls[3].endswith("/v2/users/U1/upload_part_finish")
    assert urls[4].endswith("/v2/users/U1/files")           # 带 upload_id 合并
    # 预上传要的四个校验值都在（文档列的是 file_size/md5/sha1/md5_10m）
    prepare = conn._http.posts()[1][1]
    assert set(prepare) == {"file_type", "file_size", "file_name", "md5", "sha1", "md5_10m"}
    assert prepare["file_size"] == "16"
    # 每片 PUT 的字节数要与 part_finish 报的 block_size 一致（服务端按这个校验），
    # 且两片加满整个文件 —— 少传一片就是"上传了一个残缺的文件还说成功"。
    put_sizes = [len(body) for _, body in conn._http.puts()]
    finish_sizes = [
        body["block_size"] for url, body in conn._http.posts() if url.endswith("/upload_part_finish")
    ]
    assert put_sizes == [8, 8], f"分片大小不对: {put_sizes!r}"
    assert finish_sizes == ["8", "8"] == [str(n) for n in put_sizes]


def test_a_partial_part_list_is_refused_instead_of_merging_a_truncated_file(tmp_path):
    """分片列表没覆盖完整个文件时**不许合并**。

    服务端的 ``parts`` 是它按我们报的 ``file_size`` 算的，正常不会缺片；但一旦缺了，
    合并上去就是一个残缺文件，而返回的 ``file_info`` 看起来完全正常 —— 静默数据损坏。
    宁可这次不发图。
    """
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)
    conn = _Conn(lambda method, url, body: _truncated_parts(method, url, body))

    assert _run(MEDIA.upload_image(conn, scope="users", owner_id="U1", source=str(sticker))) == ""
    assert not any(
        url.endswith("/files") and body.get("upload_id") for url, body in conn._http.posts()
    ), "缺片时不该走到合并那一步"


def _truncated_parts(method, url, body):
    if method == "PUT":
        return {"file_info": "FI-legacy"}
    if url.endswith("/upload_prepare"):
        return {
            "upload_id": "upload_1",
            "block_size": "8",
            "parts": [{"index": 0, "presigned_url": "https://cos.example/part/0", "block_size": "8"}],
        }
    if url.endswith("/upload_part_finish"):
        return {}
    return {}


# ── 每一片都必须确认成功，否则不许合并 ──────────────────────────────────
#
# httpx 自己不会因为 4xx/5xx 抛异常，所以"这片传失败了"以前和"传成功了"长得一模一样：
# 循环照旧往下走、覆盖检查照样通过、合并还会返回一个看起来正常的 file_info ——
# 一张残缺的图被当成发出去了。Greptile 在宿主那份 PR（#3210）上把这条点出来了。

def _no_merge(conn) -> bool:
    return not any(
        url.endswith("/files") and body.get("upload_id") for url, body in conn._http.posts()
    )


def test_a_rejected_part_upload_is_refused_before_merging(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)

    def responder(method, url, body):
        if method == "PUT":
            return _Response({}, status_code=403)      # 预签名 URL 过期/被拒
        return _legacy_then_nothing(method, url, body)

    conn = _Conn(responder)

    assert _run(MEDIA.upload_image(conn, scope="users", owner_id="U1", source=str(sticker))) == ""
    assert _no_merge(conn), "片 PUT 被拒时不该走到合并那一步"


def test_a_failing_part_finish_is_refused_before_merging(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)

    def responder(method, url, body):
        if method == "POST" and url.endswith("/upload_part_finish"):
            return _Response({}, status_code=500)
        return _legacy_then_nothing(method, url, body)

    conn = _Conn(responder)

    assert _run(MEDIA.upload_image(conn, scope="users", owner_id="U1", source=str(sticker))) == ""
    assert _no_merge(conn), "upload_part_finish 失败时不该走到合并那一步"


def test_a_part_finish_error_envelope_is_refused_before_merging(tmp_path):
    """200 但带着平台的错误信封（``{"code": 500, …}``）同样算失败。"""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)

    def responder(method, url, body):
        if method == "POST" and url.endswith("/upload_part_finish"):
            return {"code": 500, "message": "part rejected"}
        return _legacy_then_nothing(method, url, body)

    conn = _Conn(responder)

    assert _run(MEDIA.upload_image(conn, scope="users", owner_id="U1", source=str(sticker))) == ""
    assert _no_merge(conn), "错误信封不该走到合并那一步"


def test_a_rejected_legacy_put_is_not_reported_as_success(tmp_path):
    """旧式那条：PUT 失败时不许拿"申请上传"那一步的 file_info 冒充成功。"""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"x" * 32)

    def responder(method, url, body):
        if method == "PUT":
            return _Response({}, status_code=403)
        return {"upload_url": "https://cos.example/put/1", "file_info": "FI-upfront"}

    conn = _Conn(responder)

    assert _run(MEDIA.upload_image(conn, scope="groups", owner_id="G1", source=str(sticker))) == ""


# ── 失败与拒绝：一律返回空，让调用方降级 ────────────────────────────────

def test_upload_failure_returns_none_so_the_caller_can_fall_back_to_text(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 8)
    conn = _Conn(lambda method, url, body: {})

    assert _run(MEDIA.send_private_image(conn, "U1", str(sticker))) is None
    # 上传没成，就不该往 /messages 发那条 msg_type=7（否则会发出一条空消息）
    assert not any(url.endswith("/messages") for url, _ in conn._http.posts())


def test_a_missing_local_file_never_touches_the_network(tmp_path):
    conn = _Conn(lambda method, url, body: {"file_info": "FI"})

    assert _run(MEDIA.send_private_image(conn, "U1", str(tmp_path / "nope.png"))) is None
    assert conn._http.calls == []
    assert conn.token_calls == 0


def test_an_oversized_image_is_refused_before_uploading(tmp_path, monkeypatch):
    """超过图片软限制就不传：平台会把"图片"降级成"文件"类型，那是偷偷改语义。"""
    sticker = tmp_path / "big.png"
    sticker.write_bytes(b"b" * 64)
    monkeypatch.setattr(MEDIA, "MAX_IMAGE_BYTES", 32)
    conn = _Conn(lambda method, url, body: {"file_info": "FI"})

    assert _run(MEDIA.upload_image(conn, scope="groups", owner_id="G1", source=str(sticker))) == ""
    assert conn._http.calls == []


# ── 大小先看、读在别的线程（Greptile P2，宿主 PR #3210）─────────────────
#
# 原来 `_read_source` 在异步路径上**整份同步读**，20 MB 上限是读完之后才判的：
# 一个几百 MB 的文件会先占满内存、还堵着事件循环，最后才被拒。

def _spy_on_read(monkeypatch, record):
    """把模块里的读取函数换成记账版（`upload_image` 是运行时按名字取的，能拦到）。"""
    real = MEDIA._read_source

    def spy(source):
        record.append(source)
        return real(source)

    monkeypatch.setattr(MEDIA, "_read_source", spy)
    return spy


def test_an_oversized_file_is_refused_without_reading_it(tmp_path, monkeypatch):
    sticker = tmp_path / "huge.png"
    sticker.write_bytes(b"b" * 4096)
    monkeypatch.setattr(MEDIA, "MAX_IMAGE_BYTES", 64)
    read: list[str] = []
    _spy_on_read(monkeypatch, read)
    conn = _Conn(lambda method, url, body: {"file_info": "FI"})

    assert _run(MEDIA.upload_image(conn, scope="groups", owner_id="G1", source=str(sticker))) == ""
    assert read == [], "超限的文件根本不该被读进来"
    assert conn._http.calls == []


def test_a_missing_file_is_refused_without_reading_it(tmp_path, monkeypatch):
    read: list[str] = []
    _spy_on_read(monkeypatch, read)
    conn = _Conn(lambda method, url, body: {"file_info": "FI"})

    assert _run(MEDIA.upload_image(conn, scope="users", owner_id="U1", source=str(tmp_path / "nope.png"))) == ""
    assert read == [], "不存在的文件不该被读"
    assert conn._http.calls == []


def test_a_local_file_is_read_off_the_event_loop(tmp_path, monkeypatch):
    """整份文件 I/O 不许压在事件循环上：大文件或慢盘会把整条管线拖住。"""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"x" * 8)
    threads: list[bool] = []
    real = MEDIA._read_source

    def spy(source):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(source)

    monkeypatch.setattr(MEDIA, "_read_source", spy)
    conn = _Conn(lambda method, url, body: _legacy_ok() if method == "POST" else {"file_info": "FI-legacy"})

    assert _run(MEDIA.upload_image(conn, scope="groups", owner_id="G1", source=str(sticker))) == "FI-legacy"
    assert threads == [False], "本地文件必须在工作线程里读，不能直接堵在事件循环上"


def test_a_token_failure_does_not_raise(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"c" * 8)

    class _Boom(_Conn):
        async def _ensure_token(self):
            raise RuntimeError("token 服务不可用")

    conn = _Boom(lambda method, url, body: {"file_info": "FI"})
    assert _run(MEDIA.upload_image(conn, scope="groups", owner_id="G1", source=str(sticker))) == ""


# ── 群聊发图：与单聊同一条富媒体流程 ────────────────────────────────────

def test_group_image_uploads_with_the_group_scope_and_sends_msg_type_7():
    def responder(method, url, body):
        if url.endswith("/messages"):
            return {"id": "MID-G"}
        return {"file_info": "FI-group"}

    conn = _Conn(responder)
    message_id = _run(MEDIA.send_group_image(
        conn, "G-openid", "https://cdn.example/a.png", at_user_id="MEMBER1",
    ))

    posts = conn._http.posts()
    assert posts[0] == (
        "https://api.example/v2/groups/G-openid/files",
        {"file_type": 1, "url": "https://cdn.example/a.png", "srv_send_msg": False},
    ), f"群聊上传入口或请求体不对: {posts[0]!r}"
    assert posts[1] == (
        "https://api.example/v2/groups/G-openid/messages",
        {"msg_type": 7, "media": {"file_info": "FI-group"}, "content": "<@!MEMBER1>"},
    ), f"群聊发图载荷不对: {posts[1]!r}"
    assert message_id == "MID-G"


def test_group_image_failure_returns_none_and_does_not_send(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"q" * 8)
    conn = _Conn(lambda method, url, body: {})

    assert _run(MEDIA.send_group_image(conn, "G1", str(sticker))) is None
    assert not any(url.endswith("/messages") for url, _ in conn._http.posts())


def test_group_image_without_content_sends_no_content_field():
    """没有文字/@ 时不要塞一个空 content —— 平台对空串的处理没必要去赌。"""
    def responder(method, url, body):
        return {"id": "MID"} if url.endswith("/messages") else {"file_info": "FI"}

    conn = _Conn(responder)
    _run(MEDIA.send_group_image(conn, "G1", "https://cdn.example/a.png"))

    sent = [body for url, body in conn._http.posts() if url.endswith("/messages")]
    assert sent == [{"msg_type": 7, "media": {"file_info": "FI"}}]


# ── 漂移守卫：这些自由函数依赖的连接成员 ────────────────────────────────

def test_the_media_helpers_members_exist_on_the_resolved_connector():
    """富媒体流程只依赖这几个成员，而它要同时服务宿主副本与插件副本。

    宿主改了名字（或某一版没有这些成员）时，这里必须红 —— 否则症状是
    "开放平台突然发不出图了"，而日志里只有一句上传失败。

    ``_http`` 是**实例属性**（``__init__`` 里赋值），所以只查类属性会漏掉它 ——
    这里连实例一起查。构造一个真实例不发任何网络请求。
    """
    required = ("_http", "_API_BASE", "_ensure_token", "_auth_headers", "record_sent_message_id")
    cls = connector_seam.QQOpenPlatformConnection
    instance = cls(app_id="a", client_secret="b")
    missing = [name for name in required if not hasattr(instance, name)]
    assert not missing, f"{connector_seam.CONNECTOR_MODULE} 的连接缺了这些成员: {missing}"


def test_the_media_module_is_importable_and_has_the_entry_points():
    """副本模块本身（回退路径）的入口点还在。

    注意：**它不再由 ``connector_seam`` 解析** —— 富媒体的解析搬到了 ``media_seam``，
    判据也从"连接器来源"换成了"连接对象有没有媒体能力"（见 ``tests/test_qq_media_seam.py``）。
    这条只保证副本自己仍能被直接导入、仍提供那两个入口。
    """
    assert hasattr(MEDIA, "send_private_image") and hasattr(MEDIA, "upload_image")
    assert hasattr(MEDIA, "is_open_platform"), "副本仍要能被单独使用（宿主没有 mixin 时它就是全部）"


def test_the_channel_check_lives_in_the_plugin_now():
    """分流判据是插件自己的，不再从副本借。

    只认开放平台：OneBot 连接不能被这条流程接管。这段原本写在副本模块里，现在在
    ``media_seam`` —— 因为退役时副本整个要删，判据不能跟着走。
    """
    assert media_seam.is_open_platform(SimpleNamespace(CHANNEL="open"))
    assert media_seam.is_open_platform(SimpleNamespace(mode="open_platform"))
    assert not media_seam.is_open_platform(SimpleNamespace(CHANNEL="onebot", mode="napcat"))
    assert not media_seam.is_open_platform(object())


# ── 适配层形状：mixin 是主体，包装函数只是"绑上去" ──────────────────────
#
# 这块守的是**形状**而不是行为，因为形状错了真机上不会立刻报错：宿主连接类加不了基类，
# 所以运行时走的是模块级包装函数；包装函数哪天被写成"第二份实现"，两份就会各自漂移，
# 而症状只是某一边发不出图。

def test_the_mixin_works_on_any_class_that_has_the_members():
    """mixin 直接混进一个只有那几个成员的类就能跑 —— 不要求宿主基类、不要求继承。"""
    class _Mixed(MEDIA.QQOpenPlatformMediaMixin, _Conn):
        pass

    conn = _Mixed(lambda method, url, body: {"id": "MID"} if url.endswith("/messages") else {"file_info": "FI"})

    assert _run(conn.send_private_image("U1", "https://cdn.example/a.png")) == "MID"
    assert _run(conn.upload_image(scope="groups", owner_id="G1", source="https://cdn.example/a.png")) == "FI"


def test_the_mixin_overrides_the_transports_own_group_image():
    """同名方法必须由 mixin 盖掉：宿主/副本类自带的那份是旧的直传实现。

    MRO 上 mixin 在前 —— 这条断了，混入后群图会静默退回"上传失败→发 [图片] 三个字"。
    """
    class _Transport:
        async def send_group_image(self, group_id, image_data, *, reply_message_id="", at_user_id="", sub_type=""):
            return "transport-legacy"

    class _Mixed(MEDIA.QQOpenPlatformMediaMixin, _Transport, _Conn):
        pass

    assert _Mixed.send_group_image is MEDIA.QQOpenPlatformMediaMixin.send_group_image


def test_the_mixin_adds_exactly_three_public_members():
    """混进宿主类时只多这三个公开方法 —— 别的都走 ``_media_`` 私有前缀。

    宿主侧有兼容面契约测试（``tests/unit/test_connection_compat_surface.py``）钉着连接类的
    成员集合，这里是插件这一侧的镜像：新加公开成员必须是**有意**的。
    """
    public = {
        name for name in vars(MEDIA.QQOpenPlatformMediaMixin)
        if not name.startswith("_")
    }
    assert public == {"upload_image", "send_private_image", "send_group_image"}, public


def test_the_media_override_accepts_the_transport_signature():
    """覆盖方法的签名要能吃下被覆盖者的调用形状，包括 ``sub_type``。

    ``send_group_image(group_id, image_data, *, reply_message_id, at_user_id, sub_type)``
    是连接类原有的形状；比它窄的覆盖会在混入那天以 ``TypeError`` 炸掉调用方。
    """
    import inspect

    params = inspect.signature(MEDIA.QQOpenPlatformMediaMixin.send_group_image).parameters
    transport_style = {"reply_message_id", "at_user_id", "sub_type"}
    assert transport_style <= set(params), f"覆盖方法缺参数: {transport_style - set(params)}"


def test_the_wrappers_are_thin_forwards_over_the_mixin():
    """三个包装函数体内只准有"绑上适配器 + 调 mixin 那一个方法"。"""
    import ast
    import inspect
    import textwrap

    for name in ("upload_image", "send_private_image", "send_group_image"):
        wrapper = getattr(MEDIA, name)
        tree = ast.parse(textwrap.dedent(inspect.getsource(wrapper)))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        targets = {
            ast.unparse(node.func) for node in calls
            if ast.unparse(node.func).startswith("QQOpenPlatformMediaMixin.")
        }
        assert targets == {f"QQOpenPlatformMediaMixin.{name}"}, f"{name} 的包装体应只调 mixin 一次: {targets}"
        assert not any(
            "._http" in ast.unparse(node.func) for node in calls
        ), f"{name} 的包装体里出现了直接网络调用 —— 那是第二份实现"


def test_the_adapter_leaves_the_connection_object_alone():
    """适配器不给连接对象挂任何属性：只读连接、``__slots__`` 连接都得能用。"""
    class _Slotted:
        CHANNEL = "open"
        __slots__ = ("_http", "_API_BASE", "logger", "token_calls", "recorded")

        def __init__(self, responder):
            self._http = _FakeHTTP(responder)
            self._API_BASE = "https://api.example"
            self.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
            self.token_calls = 0
            self.recorded = []

        async def _ensure_token(self):
            self.token_calls += 1

        def _auth_headers(self):
            return {}

        def record_sent_message_id(self, mid):
            self.recorded.append(str(mid))

    conn = _Slotted(lambda method, url, body: {"file_info": "FI"})
    assert _run(MEDIA.upload_image(conn, scope="users", owner_id="U1", source="https://cdn.example/a.png")) == "FI"
    assert not hasattr(conn, "_media_post"), "适配器不该把 mixin 方法挂到连接对象上"


def test_an_already_mixed_connection_is_not_wrapped_again():
    """已经混过 mixin 的连接原样通过 —— 宿主将来自己混入时不需要改这里。"""
    class _Mixed(MEDIA.QQOpenPlatformMediaMixin, _Conn):
        pass

    conn = _Mixed(lambda method, url, body: {})
    assert MEDIA._adapter(conn) is conn


def _run(coro):
    """跑一遍协程；所有 ``{...}/messages`` 的 POST 都回一个消息 id。"""
    import asyncio

    return asyncio.run(coro)
