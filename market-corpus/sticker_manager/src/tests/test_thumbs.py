# pyright: reportMissingImports=false
"""v0.17.2「缩略图轮」的门。

来由是主人点的两件事：日志刷屏 + 加载图片超时。查下来是同一个根因——表情墙一格一图，
而 `preview` 回的是**原图字节**：官方区 190 张 ≈34.7MB（base64 后 ≈46MB）过控制通道、
并发 2，排在后面的格子等过面板默认 30s 就是超时，每张一行宿主 `TRIGGER entry='preview'`
就是刷屏（当天 363 行里 313 行）。

钉的是一条条具体的尺，尤其第一条——它反过来就是"本轮白做"的形状：

1. **命中缓存时绝不读原图**（用"删掉原图文件仍要出图"来反向验）；
2. 任何一步失败都只许降级回原图，不许让格子空白；
3. 降级日志整轮只吼一次（否则刷屏修好了又造一个新的）；
4. 不带 `kind` 的请求形状一字不变。

真 PIL 那条路**不在这里测**：本仓 `.venv` 里没有 Pillow，写成 "没 PIL 就 skip"
会是一道永远不会变红的门（陷阱 5）。`TestRenderThumbWithoutPillow` 反过来钉的是
"PIL 缺席时诚实降级"这个真事实，真渲染另跑一次性实测并把数字记进 CHANGELOG。
"""

from __future__ import annotations

from typing import Any

from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.thumbs import (
    THUMB_EDGE,
    THUMB_MAX_BYTES,
    THUMB_QUALITY,
    build_thumb,
    render_thumb,
    thumb_filename,
)
from sticker_manager.services.library import Library

_FAKE_JPEG = b"\xff\xd8\xff" + b"j" * 40


def _calls_of(record: list[dict[str, Any]]):
    """给 `build_thumb` 用的假渲染器（记每次调用用的档位）。"""

    def render(data: bytes, *, edge: int, quality: int) -> bytes:
        record.append({"edge": edge, "quality": quality, "len": len(data)})
        return _FAKE_JPEG

    return render


def _builder(record: list[dict[str, Any]], blob: bytes | None = _FAKE_JPEG):
    """给 `Library.thumb_for` 用的假**编排器**：`build(data) -> bytes|None`。

    `thumb_for` 的注入 seam 是"整条渲染编排"而不是"一次 PIL 调用"——档位降试那套逻辑
    归 `build_thumb`（上面已单独钉过），这里只关心缓存盘行为。
    """

    def build(data: bytes) -> bytes | None:
        record.append({"len": len(data)})
        return blob

    return build


class TestThumbFilename:
    def test_carries_digest_version_and_edge(self):
        name = thumb_filename("abc123")
        assert name.startswith("abc123-") and name.endswith(".jpg")
        assert str(THUMB_EDGE) in name

    def test_missing_digest_still_named(self):
        assert thumb_filename("").startswith("no-digest")


class TestBuildThumbOrchestration:
    def test_first_pass_hit_is_the_only_render(self):
        calls: list[dict[str, Any]] = []
        assert build_thumb(b"raw", render=_calls_of(calls)) == _FAKE_JPEG
        assert len(calls) == 1 and calls[0]["edge"] == THUMB_EDGE
        assert calls[0]["quality"] == THUMB_QUALITY

    def test_oversized_result_renders_again_smaller(self):
        # 反向样本：降档那一步被删掉的话，这里会拿到 None（大响应糊回通道）。
        tries: list[bytes] = []

        def render(data: bytes, *, edge: int, quality: int) -> bytes:
            tries.append(b"x" * (THUMB_MAX_BYTES + 1) if len(tries) == 0 else _FAKE_JPEG)
            return tries[-1]

        assert build_thumb(b"raw", render=render) == _FAKE_JPEG
        assert len(tries) == 2

    def test_still_oversized_after_fallback_degrades(self):
        def render(data: bytes, **_: Any) -> bytes:
            return b"x" * (THUMB_MAX_BYTES * 3)

        assert build_thumb(b"raw", render=render) is None

    def test_renderer_crash_is_not_raised(self):
        def boom(data: bytes, **_: Any) -> bytes:
            raise RuntimeError("decoder missing")

        assert build_thumb(b"raw", render=boom) is None

    def test_empty_input_renders_nothing(self):
        assert build_thumb(b"", render=_calls_of([])) is None


class TestRenderThumbWithoutPillow:
    def test_absent_pillow_degrades_honestly(self):
        # 本仓 `.venv` 里没有 Pillow（真事实，不是跳过的借口）：`render_thumb` 自己不许吞异常
        # ——吞了就没有"降级"这回事，只有"永远没有缩略图"；而 `build_thumb` 必须把它接成 None。
        # 用 find_spec 探而不是 `import PIL`：后者在这道门里是 F401，而本仓的 ruff 跑
        # `--ignore-noqa`（陷阱 10），noqa 注释救不回来。
        import importlib.util

        if importlib.util.find_spec("PIL") is None:
            raised = False
            try:
                render_thumb(PNG_BYTES, edge=THUMB_EDGE, quality=THUMB_QUALITY)
            except Exception:
                raised = True
            assert raised, "PIL 缺席时 render_thumb 必须抛，别把没图说成有图"
            assert build_thumb(PNG_BYTES) is None
        else:  # 装了 Pillow 的环境（宿主 venv 跑真机验证时）：改验"真能出小 JPEG"。
            from io import BytesIO

            from PIL import Image

            canvas = Image.new("RGB", (900, 700))
            buf = BytesIO()
            canvas.save(buf, format="PNG")
            blob = render_thumb(buf.getvalue(), edge=THUMB_EDGE, quality=THUMB_QUALITY)
            assert blob[:2] == b"\xff\xd8", "缩略图必须是 JPEG 字节头"
            assert len(blob) <= THUMB_MAX_BYTES
            with Image.open(BytesIO(blob)) as made:
                assert max(made.size) <= THUMB_EDGE


class TestLibraryThumbCache:
    def _library(self, tmp_path):
        library = Library(tmp_path / "lib")
        library.load(force=True)
        sticker, error = library.add(data=PNG_BYTES + b"|a", desc="哈欠", tags=[])
        assert error == ""
        return library, sticker

    def test_cache_hit_never_reads_the_original(self, tmp_path):
        """本轮的整条理由都钉在这上面：命中缓存还去读原图 = 白做。

        反向验法：把原图文件**删掉**，只要缓存还在就该照样给得出图。
        """
        library, sticker = self._library(tmp_path)
        calls: list[dict[str, Any]] = []
        first = library.thumb_for(sticker, build=_builder(calls))
        assert first == _FAKE_JPEG and len(calls) == 1
        library.image_path(sticker).unlink()
        again = library.thumb_for(sticker, build=_builder(calls))
        assert again == _FAKE_JPEG
        assert len(calls) == 1, "第二次不许重渲"

    def test_cache_file_is_named_by_content_digest(self, tmp_path):
        library, sticker = self._library(tmp_path)
        library.thumb_for(sticker, build=_builder([]))
        names = [p.name for p in library.thumbs_dir.iterdir()]
        assert names == [thumb_filename(sticker.sha256)]

    def test_failure_leaves_no_file_and_returns_none(self, tmp_path):
        library, sticker = self._library(tmp_path)
        assert library.thumb_for(sticker, build=lambda data: None) is None
        # 一次都没落盘：没有缩略图就别留半个文件（目录可以不存在）。
        assert library.thumbs_dir.is_dir() is False or list(library.thumbs_dir.iterdir()) == []

    def test_missing_digest_gets_one_at_render_time(self, tmp_path):
        library, sticker = self._library(tmp_path)
        loose = sticker.with_sha256("") if hasattr(sticker, "with_sha256") else sticker
        assert library.thumb_for(loose, build=_builder([])) == _FAKE_JPEG


class TestRepairPurgesThumbs:
    def test_orphan_thumb_goes_but_foreign_files_stay(self, tmp_path):
        library = Library(tmp_path / "lib")
        library.load(force=True)
        sticker, error = library.add(data=PNG_BYTES + b"|a", desc="甲", tags=[])
        assert error == ""
        library.thumb_for(sticker, build=_builder([]))
        keep = library.thumbs_dir / "someone-else.txt"
        keep.write_text("not ours", encoding="utf-8")
        counts = library.repair()
        assert counts["purged_files"] == 0, "条目还在，它的缩略图不算孤儿"
        assert (library.thumbs_dir / thumb_filename(sticker.sha256)).is_file(), "在用的缩略图不许被当孤儿删"
        assert keep.is_file(), "体检只认自己的命名，别的文件一律不碰"
        # 条目没了之后，它的缩略图才算孤儿。（remove 回的是错误码字符串，"" = 成功。）
        assert library.remove(sticker.id) == ""
        after = library.repair()
        assert after["purged_files"] >= 1, "失主的缩略图要跟着回收，否则换机后目录只涨不落"
        assert not (library.thumbs_dir / thumb_filename(sticker.sha256)).is_file()
        assert keep.is_file()


class TestPreviewEntryKind:
    def _plugin(self, tmp_path, *, build: Any = None):
        host = FakeHostContext(
            data_root=tmp_path,
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
        )
        plugin, host = build_plugin(host)
        plugin._library.load(force=True)
        sticker, error = plugin._library.add(data=PNG_BYTES + b"|a", desc="哈欠", tags=[])
        assert error == ""
        if build is not None:
            original = plugin._library.thumb_for

            def fake(sticker_arg, *, build=build):  # 只替渲染尺，路径逻辑照走
                return original(sticker_arg, build=build)

            plugin._library.thumb_for = fake
        return plugin, sticker

    def test_thumb_is_a_single_done_response(self, tmp_path, run_async):
        plugin, sticker = self._plugin(tmp_path, build=_builder([]))
        result = run_async(plugin.preview_entry(id=sticker.id, kind="thumb"))
        payload = result.value
        assert payload["done"] is True and payload["kind"] == "thumb"
        assert payload["mime"] == "image/jpeg"
        assert payload["next_offset"] == payload["size"]

    def test_no_kind_keeps_the_old_shape(self, tmp_path, run_async):
        # 反向样本：老调用点（聚焦卡、任何外部脚本）不带 kind，形状若被改了就是回归。
        plugin, sticker = self._plugin(tmp_path, build=_builder([]))
        payload = run_async(plugin.preview_entry(id=sticker.id)).value
        assert "kind" not in payload
        assert payload["done"] is True and payload["offset"] == 0
        assert payload["size"] > 0 and payload["next_offset"] == payload["size"]

    def test_thumb_failure_falls_back_to_original_bytes(self, tmp_path, run_async):
        plugin, sticker = self._plugin(tmp_path, build=lambda data, **_: None)
        payload = run_async(plugin.preview_entry(id=sticker.id, kind="thumb")).value
        assert payload.get("kind") is None
        assert payload["size"] == len(PNG_BYTES) + 2  # 原图字节，一格都不许空

    def test_fallback_warns_once_not_once_per_tile(self, tmp_path, run_async):
        # 刷屏本身就是本轮要修的东西；降级若每张吼一次就是换了个地方刷屏。
        recorder = []

        class _Log:
            def info(self, message, **_k):
                recorder.append(str(message))

            def warning(self, message, **_k):
                recorder.append(str(message))

            def error(self, message, **_k):
                recorder.append(str(message))

            def debug(self, message, **_k):
                recorder.append(str(message))

        plugin, sticker = self._plugin(tmp_path, build=lambda data, **_: None)
        plugin.logger = _Log()
        for _ in range(5):
            run_async(plugin.preview_entry(id=sticker.id, kind="thumb"))
        assert sum(1 for x in recorder if "falling back" in x) == 1
