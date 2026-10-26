# LOCAL-PATCH: 4302a9ea 本文件是**插件自撰**，不是上游副本（上游没有对应模块）。
# 它必须跟着 _vendor/connection_onebot/ 一起留下来：副本里的 qq_open_plat.py 用
# `from . import qq_open_platform_media` 引它，删了会让整个副本包 import 失败。
# 明细与同步顺序见同目录 PROVENANCE.md。
#
# 标记写在 docstring **之前**：`tests/test_qq_connector_seam.py` 的守卫只认文件头 40 行，
# 而这段 docstring 有 45 行 —— 标记跟在后面就等于没标。

"""QQ Open Platform rich media: image upload and image sending.

Image sending on the Open Platform is not one request but two: upload the bytes to
get a ``file_info``, then send a ``msg_type=7`` message carrying it. Both halves are
QQ-platform-specific, so they live here rather than in the platform-neutral
``base`` layer.

Two upload protocols coexist on this platform:

1. **Legacy direct upload** — ``POST /v2/{scope}/{id}/files`` with
   ``file_type``/``file_name``/``file_size``/``mime_type``, which answers with an
   ``upload_url`` to ``PUT`` the bytes to. This is what the transport class used
   before; the current platform docs no longer list those request fields, and a live
   run on 2026-09-26 showed the group flow failing on it.
2. **Documented upload** — either a **URL upload** (hand the platform an http(s)
   address and let it fetch), or a **chunked upload**
   (``upload_prepare`` → per-part ``PUT`` + ``upload_part_finish`` → merge).
   A local file cannot use the URL flow.

So for local files both are attempted, legacy first: that keeps the previously
working deployment working, and whichever succeeds is named in the log, which is how
"which protocol is still alive" gets answered without guessing.

Shape
-----

The actions sit on :class:`QQOpenPlatformMediaMixin`, the same way NapCat /
go-cqhttp extensions sit on ``NapCatActionsMixin``: the transport class stays about
the protocol, and a platform's extras stay in one place next to it. Mixing it into
``QQOpenPlatformConnection`` overrides ``send_group_image`` (same name, and the
transport's own implementation is the stale legacy path described above) and adds
``upload_image`` and ``send_private_image``.

The consuming plugin cannot add bases to the host's class, and it resolves whichever
connector the host provides -- the host's, or its own vendored copy. The three
operations therefore also exist as module-level wrappers taking any connection
object. Each wrapper binds the mixin over that object (:class:`_MediaAdapter`: mixin
methods win, everything else comes from the connection) and calls one mixin method,
so the flow lives in exactly one place and there is no second implementation to
drift. On the day the host class composes the mixin, the adapter steps aside.

Dependencies
------------

Only these members of the connection object, all present on the resolved connector
(``tests/test_qq_open_platform_media.py`` guards them): ``_http``, ``_API_BASE``,
``_ensure_token()``, ``_auth_headers()``, ``logger``, and ``record_sent_message_id()``
for the send half.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
from typing import Any

#: Platform media type for an image.
FILE_TYPE_IMAGE = 1

#: Soft image limit. Past this the platform stores the upload as a "file" instead of
#: an image; this module refuses rather than silently changing what it sends.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: ``upload_prepare`` wants ``md5_10m``: the MD5 of the first 10002432 bytes.
_MD5_10M_BYTES = 10_002_432


def is_open_platform(conn: Any) -> bool:
    """Is this connection the QQ Open Platform?

    Reads ``CHANNEL`` first (``"open"``, the same value domain as
    ``OneBotClient.CHANNEL``), then falls back to ``mode``. Both already exist; no
    protocol member is added for this.
    """
    if str(getattr(conn, "CHANNEL", "") or "").strip() == "open":
        return True
    return str(getattr(conn, "mode", "") or "").strip() == "open_platform"


def _local_path(source: str) -> str:
    text = str(source or "").strip()
    if text.startswith("file://"):
        text = text[7:]
    return text


def _read_source(source: str) -> tuple[bytes, str]:
    """Read a local file -> ``(bytes, file_name)``; unreadable is ``(b"", "")``.

    Blocking on purpose: callers run it off the event loop (``asyncio.to_thread``) after
    checking the size, so a slow or huge file never stalls the loop or gets read just to
    be rejected. Kept identical to the host module's copy.
    """
    path = _local_path(source)
    if not path or not os.path.isfile(path):
        return b"", ""
    with open(path, "rb") as handle:
        return handle.read(), os.path.basename(path)


def _digests(payload: bytes) -> dict[str, str]:
    return {
        "md5": hashlib.md5(payload).hexdigest(),
        "sha1": hashlib.sha1(payload).hexdigest(),
        "md5_10m": hashlib.md5(payload[:_MD5_10M_BYTES]).hexdigest(),
    }


def _media_error(data: dict[str, Any]) -> str:
    """The platform's error envelope, when the answer carries one.

    Success answers either hold the field the caller wants (``file_info`` / ``id``) or
    nothing at all, so only a **non-zero** ``code`` / ``err_code`` counts as an error:
    a missing code is not one. Returns "" when the answer looks fine.

    Kept identical to the host module's copy -- the two have to behave the same, since
    which one runs depends only on whether the host ships the media mixin.
    """
    for key in ("code", "err_code"):
        if key not in data:
            continue
        value = data.get(key)
        if str(value).strip() not in ("", "0", "None"):
            return str(data.get("message") or data.get("msg") or f"{key}={value}")
    return ""


class QQOpenPlatformMediaMixin:
    """Rich-media actions for ``QQOpenPlatformConnection``.

    Every method is written against ``self`` as the connection object, so the mixin
    works on any class that provides the members listed in the module docstring.
    Failures return empty/``None`` instead of raising: the caller decides how to
    degrade (the delivery layer falls back to a ``[图片]`` text line).
    """

    # ── transport plumbing ─────────────────────────────────────────────

    def _media_log(self, level: str, message: str) -> None:
        logger = getattr(self, "logger", None)
        if logger is None:
            return
        try:
            getattr(logger, level, logger.info)(f"[QQOpenPlatform] {message}")
        except Exception:
            pass

    def _media_api_base(self) -> str:
        return str(getattr(self, "_API_BASE", "") or "").rstrip("/")

    async def _media_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Authenticated POST returning parsed JSON; a non-dict answer is ``{}``.

        A non-2xx answer **raises** rather than looking like an empty result. For an
        upload step that difference is the whole ballgame: "the part was accepted" and
        "the part was rejected" decide whether merging the chunks is allowed to run,
        and a merge over a rejected part stores a truncated file while the platform
        still answers with a normal-looking ``file_info``.
        """
        response = await self._http.post(
            f"{self._media_api_base()}{path}", json=body, headers=self._auth_headers(),
        )
        response.raise_for_status()
        try:
            data = response.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    # ── upload protocols ───────────────────────────────────────────────

    async def _media_upload_by_url(
        self, *, scope: str, owner_id: str, url: str, file_type: int,
    ) -> str:
        """Documented URL upload: the platform fetches and stores the address."""
        data = await self._media_post(
            f"/v2/{scope}/{owner_id}/files",
            {"file_type": file_type, "url": url, "srv_send_msg": False},
        )
        return str(data.get("file_info") or "")

    async def _media_upload_chunked(
        self, *, scope: str, owner_id: str, payload: bytes, file_name: str, file_type: int,
    ) -> str:
        """Documented chunked upload: prepare -> per-part PUT + finish -> merge."""
        digests = _digests(payload)
        prepare = await self._media_post(
            f"/v2/{scope}/{owner_id}/upload_prepare",
            {
                "file_type": file_type,
                "file_size": str(len(payload)),
                "file_name": file_name,
                **digests,
            },
        )
        upload_id = str(prepare.get("upload_id") or "")
        parts = prepare.get("parts")
        if not upload_id or not isinstance(parts, list) or not parts:
            return ""

        mime_type = mimetypes.guess_type(file_name)[0] or "image/png"
        ordered = sorted(
            (p for p in parts if isinstance(p, dict)),
            key=lambda p: int(p.get("index") or 0),
        )
        offset = 0
        for part in ordered:
            try:
                index = int(part.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            try:
                size = int(part.get("block_size") or 0)
            except (TypeError, ValueError):
                size = 0
            chunk = payload[offset:offset + size] if size > 0 else payload[offset:]
            presigned = str(part.get("presigned_url") or "")
            if not chunk or not presigned:
                return ""

            # Every part has to be **confirmed** before the merge is allowed to run: a
            # rejected PUT or a rejected finish would otherwise leave a hole in the file,
            # and the merge below still answers with a normal-looking ``file_info``.
            try:
                response = await self._http.put(
                    presigned, content=chunk, headers={"Content-Type": mime_type},
                )
                response.raise_for_status()
            except Exception as exc:
                self._media_log(
                    "warning", f"分片第 {index} 片上传失败（{len(chunk)} 字节），放弃合并: {exc}",
                )
                return ""
            try:
                finished = await self._media_post(
                    f"/v2/{scope}/{owner_id}/upload_part_finish",
                    {
                        "upload_id": upload_id,
                        "part_index": index,
                        "block_size": str(len(chunk)),
                        "md5": hashlib.md5(chunk).hexdigest(),
                    },
                )
            except Exception as exc:
                self._media_log("warning", f"分片第 {index} 片收尾失败，放弃合并: {exc}")
                return ""
            problem = _media_error(finished)
            if problem:
                self._media_log("warning", f"分片第 {index} 片被平台拒绝，放弃合并: {problem}")
                return ""

            offset += len(chunk)

        if offset != len(payload):
            # The part list did not cover the whole file: merging would store a
            # truncated file and the platform will not flag it. Skipping this send is
            # better than uploading a broken image and reporting success.
            self._media_log(
                "warning", f"分片只覆盖 {offset}/{len(payload)} 字节，放弃合并",
            )
            return ""

        merged = await self._media_post(
            f"/v2/{scope}/{owner_id}/files",
            {"file_type": file_type, "upload_id": upload_id, "srv_send_msg": False, "file_name": file_name},
        )
        problem = _media_error(merged)
        if problem:
            self._media_log("warning", f"合并分片失败: {problem}")
            return ""
        return str(merged.get("file_info") or "")

    async def _media_upload_legacy(
        self, *, scope: str, owner_id: str, payload: bytes, file_name: str, file_type: int,
    ) -> str:
        """Legacy direct upload: apply for an ``upload_url``, then PUT."""
        mime_type = mimetypes.guess_type(file_name)[0] or "image/png"
        data = await self._media_post(
            f"/v2/{scope}/{owner_id}/files",
            {
                "file_type": file_type,
                "file_name": file_name,
                "file_size": len(payload),
                "mime_type": mime_type,
            },
        )
        upload_url = str(data.get("upload_url") or "")
        if not upload_url:
            return ""
        response = await self._http.put(
            upload_url, content=payload, headers={"Content-Type": mime_type},
        )
        response.raise_for_status()
        file_info = ""
        try:
            file_info = str((response.json() or {}).get("file_info") or "")
        except Exception:
            file_info = ""
        return file_info or str(data.get("file_info") or "")

    # ── public operations ──────────────────────────────────────────────

    async def upload_image(self, *, scope: str, owner_id: str, source: str) -> str:
        """Upload one image into ``scope`` (``"groups"`` / ``"users"``), return ``file_info``.

        ``scope`` is the platform's own isolation: an upload made through the private
        interface can only be sent privately, and the other way round, so callers must
        pass the one matching where the image goes. Failure returns ``""`` and leaves
        the degradation choice to the caller.
        """
        url = str(source or "").strip()
        if not url:
            return ""
        if url.startswith(("http://", "https://")):
            try:
                await self._ensure_token()
                file_info = await self._media_upload_by_url(
                    scope=scope, owner_id=owner_id, url=url, file_type=FILE_TYPE_IMAGE,
                )
            except Exception as exc:
                self._media_log("warning", f"图片 URL 上传异常: {exc}")
                return ""
            if file_info:
                self._media_log("info", f"图片上传成功(url): {file_info[:24]}")
            else:
                self._media_log("warning", "图片 URL 上传失败")
            return file_info

        # Size first, bytes later: the soft limit has to be able to reject a file
        # *without* pulling it into memory, and the read itself must not run on the
        # event loop (a local image can be arbitrarily large or on a slow volume).
        path = _local_path(url)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size <= 0:
            self._media_log("warning", f"图片文件不存在或为空: {path}")
            return ""
        if size > MAX_IMAGE_BYTES:
            self._media_log(
                "warning",
                f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 软限制，放弃上传: {size} 字节",
            )
            return ""

        try:
            payload, file_name = await asyncio.to_thread(_read_source, url)
        except Exception as exc:
            self._media_log("warning", f"图片读取失败: {exc}")
            return ""
        if not payload:
            self._media_log("warning", f"图片文件不存在或为空: {path}")
            return ""
        if len(payload) > MAX_IMAGE_BYTES:
            # Second line of defence: the file can grow (or be swapped) between the stat
            # above and the read.
            self._media_log(
                "warning",
                f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 软限制，放弃上传: {len(payload)} 字节",
            )
            return ""

        try:
            await self._ensure_token()
        except Exception as exc:
            self._media_log("warning", f"取 token 失败，无法上传图片: {exc}")
            return ""

        # Both protocols, legacy first: never worse than the behaviour that shipped,
        # and the log says which one worked.
        for label, attempt in (
            ("直传", self._media_upload_legacy),
            ("分片", self._media_upload_chunked),
        ):
            try:
                file_info = await attempt(
                    scope=scope, owner_id=owner_id,
                    payload=payload, file_name=file_name, file_type=FILE_TYPE_IMAGE,
                )
            except Exception as exc:
                self._media_log("warning", f"图片{label}上传异常: {exc}")
                continue
            if file_info:
                self._media_log("info", f"图片上传成功({label}): {file_info[:24]}")
                return file_info
            self._media_log("warning", f"图片{label}上传未拿到 file_info")
        return ""

    async def send_private_image(
        self, user_id: str, source: str, *,
        content: str = "", reply_message_id: str = "", record_sent: bool = True,
    ) -> str | None:
        """Send one image to a private chat (``msg_type=7`` + ``media.file_info``).

        Returns the message id, or ``None`` at any failure for the caller to degrade.
        """
        target = str(user_id or "").strip()
        if not target:
            return None
        file_info = await self.upload_image(scope="users", owner_id=target, source=source)
        if not file_info:
            return None
        body: dict[str, Any] = {"msg_type": 7, "media": {"file_info": file_info}}
        text = str(content or "").strip()
        if text:
            body["content"] = text
        reply_id = str(reply_message_id or "").strip()
        if reply_id:
            body["msg_id"] = reply_id
        try:
            data = await self._media_post(f"/v2/users/{target}/messages", body)
        except Exception as exc:
            self._media_log("warning", f"发送单聊图片失败: {exc}")
            return None
        message_id = str(data.get("id") or "")
        if message_id and record_sent:
            try:
                self.record_sent_message_id(message_id)
            except Exception:
                pass
        return message_id or None

    async def send_group_image(
        self, group_id: str, source: str, *,
        content: str = "", reply_message_id: str = "", at_user_id: str = "",
        sub_type: str = "", record_sent: bool = False,
    ) -> str | None:
        """Send one image to a group chat (``msg_type=7`` + ``media.file_info``).

        Overrides the connection's own ``send_group_image`` when mixed in, which is
        deliberate: that one only implemented the legacy direct upload, and a live run
        on 2026-09-26 showed it failing on the Open Platform -- the group sticker path
        had been silently degrading to a ``[图片]`` text line, while the private path
        only worked because it went through this module's chunked fallback. Both
        directions now take the same route.

        ``sub_type`` is the transport's own image flavour (e.g. OneBot's flash image);
        the Open Platform has no equivalent, so an override with the same signature
        accepts and ignores it instead of breaking its callers with a ``TypeError``.
        """
        target = str(group_id or "").strip()
        if not target:
            return None
        file_info = await self.upload_image(scope="groups", owner_id=target, source=source)
        if not file_info:
            return None
        body: dict[str, Any] = {"msg_type": 7, "media": {"file_info": file_info}}
        at = str(at_user_id or "").strip()
        text = str(content or "").strip()
        if at or text:
            body["content"] = (f"<@!{at}>" if at else "") + text
        reply_id = str(reply_message_id or "").strip()
        if reply_id:
            body["msg_id"] = reply_id
        try:
            data = await self._media_post(f"/v2/groups/{target}/messages", body)
        except Exception as exc:
            self._media_log("warning", f"发送群聊图片失败: {exc}")
            return None
        message_id = str(data.get("id") or "")
        if message_id and record_sent:
            try:
                self.record_sent_message_id(message_id)
            except Exception:
                pass
        return message_id or None


# ── compatibility surface ──────────────────────────────────────────────
#
# The same three operations, callable with any connection object. This is what the
# plugin's delivery layer and the vendored ``qq_open_plat`` patches use: the host's
# connection class cannot be given new bases from here, and the resolved connector may
# be either implementation. The wrappers are thin -- the flow is in the mixin above.


class _MediaAdapter(QQOpenPlatformMediaMixin):
    """Bind the mixin over a connection object that does not inherit it.

    Attribute lookup finds the mixin's own methods first (including the private
    ``_media_*`` plumbing) and delegates everything else to the wrapped connection,
    which is where ``_http`` / ``_API_BASE`` / ``_ensure_token`` / ``_auth_headers`` /
    ``logger`` / ``record_sent_message_id`` come from. The connection object itself is
    left untouched: no attribute is set on it, so a class with ``__slots__`` or a
    read-only connection works the same.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _adapter(conn: Any) -> Any:
    """A connection ready for the mixin methods; already-mixed connections pass through."""
    return conn if isinstance(conn, QQOpenPlatformMediaMixin) else _MediaAdapter(conn)


async def upload_image(conn: Any, *, scope: str, owner_id: str, source: str) -> str:
    return await QQOpenPlatformMediaMixin.upload_image(
        _adapter(conn), scope=scope, owner_id=owner_id, source=source,
    )


async def send_private_image(
    conn: Any, user_id: str, source: str, *,
    content: str = "", reply_message_id: str = "", record_sent: bool = True,
) -> str | None:
    return await QQOpenPlatformMediaMixin.send_private_image(
        _adapter(conn), user_id, source,
        content=content, reply_message_id=reply_message_id, record_sent=record_sent,
    )


async def send_group_image(
    conn: Any, group_id: str, source: str, *,
    content: str = "", reply_message_id: str = "", at_user_id: str = "",
    sub_type: str = "", record_sent: bool = False,
) -> str | None:
    return await QQOpenPlatformMediaMixin.send_group_image(
        _adapter(conn), group_id, source,
        content=content, reply_message_id=reply_message_id,
        at_user_id=at_user_id, sub_type=sub_type, record_sent=record_sent,
    )
