"""core/catalog.py 的纯函数测试：格式嗅探、id、标签、检索、目录文案。"""

from __future__ import annotations

import time

from conftest import GIF_BYTES, JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES, WEBP_BYTES
from sticker_manager.core.catalog import (  # pyright: ignore[reportMissingImports] — 独立仓无 sticker_manager 目录名；测试态由 conftest 的 importlib 别名接管（pytest 实测可解），静态侧不可见，同 __init__.py 的 SDK 导入先例
    CAPTION_MAX_CHARS,
    DESC_MAX_CHARS,
    VISIBLE_TEXT_MAX_CHARS,
    Sticker,
    desc_from_filename,
    detect_image_format,
    format_catalog_for_model,
    is_animated_gif,
    new_sticker_id,
    normalize_optional_text,
    normalize_tags,
    parse_tags_field,
    search_stickers,
    validate_desc,
    validate_optional_text,
)


class TestDetectImageFormat:
    def test_png_jpg_gif_webp_recognized(self):
        assert detect_image_format(PNG_BYTES) == ("png", "image/png")
        assert detect_image_format(JPEG_BYTES) == ("jpg", "image/jpeg")
        assert detect_image_format(GIF_BYTES) == ("gif", "image/gif")
        assert detect_image_format(WEBP_BYTES) == ("webp", "image/webp")

    def test_non_image_rejected(self):
        assert detect_image_format(NOT_AN_IMAGE) is None
        assert detect_image_format(b"") is None

    def test_webp_requires_both_riff_and_webp_markers(self):
        # RIFF 头但非 WEBP 签名（如 AVI）必须被拒
        fake_avi = b"RIFF" + (70).to_bytes(4, "little") + b"AVI " + b"\x00" * 62
        assert detect_image_format(fake_avi) is None

    def test_extension_never_trumps_magic(self):
        # 嗅探只看文件头：给了 .png 名字的内容嗅探结果不变（入库口就是唯一判据）
        assert detect_image_format(NOT_AN_IMAGE) is None


class TestAnimatedGif:
    def test_single_frame_not_animated(self):
        assert is_animated_gif(GIF_BYTES) is False

    def test_netscape_loop_detected(self):
        data = GIF_BYTES[:6] + b"NETSCAPE2.0" + GIF_BYTES[17:]
        assert is_animated_gif(data) is True

    def test_non_gif_always_false(self):
        assert is_animated_gif(PNG_BYTES) is False


class TestIdsAndFields:
    def test_new_id_unique_against_existing(self):
        existing = {"a1b2c3d4e5"}
        sid = new_sticker_id(existing)
        assert len(sid) == 10
        assert sid not in existing

    def test_normalize_tags_dedupes_and_caps(self):
        tags = normalize_tags([" 开心 ", "开心", "KIRA", "x" * 40, 123, "cat", " "])
        assert tags == ["开心", "KIRA", "cat"]

    def test_normalize_tags_casefold_dedupe(self):
        assert normalize_tags(["Cat", "cat"]) == ["Cat"]

    def test_parse_tags_field_accepts_both_shapes(self):
        assert parse_tags_field("a，b、c,a") == ["a", "b", "c"]
        assert parse_tags_field(["a", "b"]) == ["a", "b"]
        assert parse_tags_field(None) == []
        assert parse_tags_field(42) == []

    def test_validate_desc(self):
        assert validate_desc(" 猫笑着挥手 ") == ("猫笑着挥手", "")
        assert validate_desc("") == ("", "empty")
        assert validate_desc("   ") == ("", "empty")
        assert validate_desc("x" * 201)[1] == "too_long"
        assert validate_desc(None) == ("", "empty")


class TestStickerRecord:
    def test_roundtrip(self):
        sticker = Sticker(id="abc", file="abc.png", desc="笑", tags=["开心"], added_at=1.0)
        restored = Sticker.from_dict(sticker.as_dict())
        assert restored == sticker

    def test_from_dict_drops_bad_shapes(self):
        assert Sticker.from_dict(None) is None
        assert Sticker.from_dict("x") is None
        assert Sticker.from_dict({"id": "", "file": "a.png"}) is None
        assert Sticker.from_dict({"id": "a", "file": ""}) is None
        # desc 缺失不炸：宽松还原成空串
        loose = Sticker.from_dict({"id": "a", "file": "a.png"})
        assert loose is not None and loose.desc == ""

    def test_from_dict_survives_poisoned_numbers(self):
        # v0.6.0 承诺：手改坏的时间戳/计数只能丢自己的值（回 0），不能把整本库打成不可加载
        # （load() 的条目循环没有逐条 try——宽松尺必须在 from_dict 内兑现）。
        poisoned = Sticker.from_dict(
            {
                "id": "a",
                "file": "a.png",
                "desc": "笑",
                "added_at": "abc",
                "use_count": [3],
                "last_used_at": "nan",
                "sha256": None,
            }
        )
        assert poisoned is not None
        assert poisoned.added_at == 0.0 and poisoned.use_count == 0
        assert poisoned.last_used_at == 0.0 and poisoned.sha256 == ""
        # inf 字符串 / 越界 float / 巨整数字面量（合法 Python int，异常捕不到）全部回 0
        floats = Sticker.from_dict({"id": "a", "file": "a.png", "added_at": "inf", "last_used_at": 1e400})
        assert floats is not None and floats.added_at == 0.0 and floats.last_used_at == 0.0
        big = Sticker.from_dict({"id": "a", "file": "a.png", "use_count": 10**400})
        assert big is not None and big.use_count == 0
        # 正常值不受牵连：好数字照常还原
        good = Sticker.from_dict({"id": "a", "file": "a.png", "added_at": 1.5, "use_count": 7})
        assert good is not None and good.added_at == 1.5 and good.use_count == 7

    def test_with_touch_increments(self):
        sticker = Sticker(id="abc", file="abc.png", desc="笑", use_count=3, last_used_at=1.0)
        touched = sticker.with_touch(now=99.0)
        assert touched.use_count == 4
        assert touched.last_used_at == 99.0
        # 原对象不动（frozen dataclass 的复制语义）
        assert sticker.use_count == 3


def _s(sid: str, desc: str, tags=(), disabled=False, uses=0, last=0.0) -> Sticker:
    return Sticker(
        id=sid,
        file=f"{sid}.png",
        desc=desc,
        tags=list(tags),
        disabled=disabled,
        added_at=time.time(),
        use_count=uses,
        last_used_at=last,
    )


class TestSearch:
    def setup_method(self):
        self.pool = [
            _s("id001", "猫咪开心挥手", ["开心", "猫"], uses=5, last=100.0),
            _s("id002", "无语凝噎", ["无语"], uses=9, last=90.0),
            _s("id003", "开心到飞起", ["开心"], disabled=True, uses=1, last=80.0),
            _s("id004", "猫猫思考", ["猫"], uses=2, last=95.0),
        ]

    def test_empty_query_sorted_by_use_count_then_recency(self):
        # 排序键：use_count 降序（9,5,2），同次数再按最近使用
        result = search_stickers(self.pool, "")
        assert [s.id for s in result] == ["id002", "id001", "id004"]

    def test_disabled_excluded_by_default(self):
        ids = [s.id for s in search_stickers(self.pool, "开心")]
        assert "id003" not in ids
        assert "id001" in ids

    def test_include_disabled(self):
        ids = [s.id for s in search_stickers(self.pool, "开心", include_disabled=True)]
        assert "id003" in ids

    def test_exact_id_beats_desc_match(self):
        result = search_stickers(self.pool, "id002")
        assert result[0].id == "id002"

    def test_tag_match_ranks_below_desc_match(self):
        result = search_stickers(self.pool, "开心")
        assert [s.id for s in result][0] == "id001"  # desc 命中

    def test_case_insensitive_query(self):
        pool = [_s("id010", "Cool cat", ["CUTE"])]
        assert search_stickers(pool, "cute")[0].id == "id010"

    def test_no_match_returns_empty(self):
        assert search_stickers(self.pool, "不存在的词") == []


def _sc(sid: str, desc: str, *, caption="", group="", tags=(), visible="") -> Sticker:
    return Sticker(
        id=sid,
        file=f"{sid}.png",
        desc=desc,
        caption=caption,
        group=group,
        tags=list(tags),
        visible_text=visible,
    )


class TestSearchSemanticFields:
    """打分序：desc 命中 > caption 命中 > 套图名 > 标签 > 图内原文 > 文件名。"""

    def setup_method(self):
        self.pool = [
            _sc("d1", "开心挥手"),
            _sc("c1", "猫图", caption="开心到飞起后的自得"),
            _sc("g1", "某图", group="开心"),  # 套图名**精确**命中（58=子串命中会排标签后，不同层）
            _sc("t1", "另一图", tags=["开心"]),
            _sc("v1", "静图", visible="看了就开心"),
        ]

    def test_desc_beats_caption(self):
        result = search_stickers(self.pool, "开心")
        assert result[0].id == "d1"
        assert result[1].id == "c1"

    def test_caption_beats_group(self):
        ids = [s.id for s in search_stickers(self.pool, "开心")]
        assert ids.index("c1") < ids.index("g1")

    def test_group_beats_tag(self):
        ids = [s.id for s in search_stickers(self.pool, "开心")]
        assert ids.index("g1") < ids.index("t1")

    def test_visible_text_is_a_search_hit(self):
        result = search_stickers(self.pool, "看了就开心")
        assert result and result[0].id == "v1"

    def test_catalog_row_uses_caption_and_hides_visible_text(self):
        text = format_catalog_for_model(self.pool, 10)
        assert "[c1] 开心到飞起后的自得" in text
        assert "[d1] 开心挥手" in text  # 无 caption 回落 desc
        assert "看了就开心" not in text  # 图内原文不上目录


class TestCatalogForModel:
    def test_line_shape(self):
        pool = [_s("id001", "猫咪开心挥手", ["开心", "猫"])]
        text = format_catalog_for_model(pool, 10)
        assert text == "[id001] 猫咪开心挥手（标签：开心/猫）"

    def test_disabled_and_limit(self):
        pool = [_s(f"id{i}", f"表情{i}", disabled=(i == 1)) for i in range(5)]
        text = format_catalog_for_model(pool, 3)
        lines = text.splitlines()
        assert len(lines) == 3
        assert all("表情1" not in line for line in lines)

    def test_empty_library_is_empty_string(self):
        assert format_catalog_for_model([], 10) == ""


class TestDescFromFilename:
    def test_strips_extension_and_separators(self):
        assert desc_from_filename("happy_cat-01.png") == "happy cat 01"

    def test_takes_basename_of_path(self):
        assert desc_from_filename(r"C:\pics\歪头.webp") == "歪头"

    def test_collapses_whitespace(self):
        assert desc_from_filename("a +.. b.gif") == "a b"

    def test_empty_stem_falls_back(self):
        assert desc_from_filename("...png") == "sticker"
        assert desc_from_filename("") == "sticker"

    def test_capped_at_desc_max(self):
        assert len(desc_from_filename("x" * 500)) == DESC_MAX_CHARS


class TestSemanticFields:
    """v0.3.0 轮 A：caption（梗义）/ visible_text（图内原文）的数据层契约。"""

    def test_roundtrip_with_caption(self):
        sticker = Sticker(id="abc", file="abc.png", desc="笑", caption="被催很久终于交差", visible_text="就这？")
        assert Sticker.from_dict(sticker.as_dict()) == sticker

    def test_legacy_dict_without_new_keys(self):
        # 老库宽松兼容：无 caption/visible_text 键 → 空串，不炸不需迁移
        legacy = {"id": "a", "file": "a.png", "desc": "笑"}
        restored = Sticker.from_dict(legacy)
        assert restored is not None
        assert restored.caption == "" and restored.visible_text == ""

    def test_from_dict_caps_oversized_values(self):
        raw = {
            "id": "a",
            "file": "a.png",
            "desc": "笑",
            "caption": "x" * (CAPTION_MAX_CHARS + 99),
            "visible_text": "y" * (VISIBLE_TEXT_MAX_CHARS + 99),
        }
        restored = Sticker.from_dict(raw)
        assert restored is not None
        assert len(restored.caption) == CAPTION_MAX_CHARS
        assert len(restored.visible_text) == VISIBLE_TEXT_MAX_CHARS

    def test_catalog_body_prefers_caption(self):
        with_caption = Sticker(id="a", file="a.png", desc="短名", caption="梗义正文")
        without = Sticker(id="b", file="b.png", desc="短名")
        assert with_caption.catalog_body() == "梗义正文"
        assert without.catalog_body() == "短名"

    def test_validate_optional_text(self):
        assert validate_optional_text("  ", limit=10) == ("", "")  # 空是合法意图
        assert validate_optional_text(None, limit=10) == ("", "")
        assert validate_optional_text(" 好 ", limit=10) == ("好", "")
        assert validate_optional_text("x" * 11, limit=10) == ("", "too_long")

    def test_normalize_optional_text_truncates(self):
        assert normalize_optional_text("  abc  ", limit=10) == "abc"
        assert normalize_optional_text("x" * 50, limit=10) == "x" * 10
        assert normalize_optional_text(123, limit=10) == ""

    def test_with_touch_and_sha_carry_new_fields(self):
        # 防回归：拷贝重建若漏字段，caption 会在一次使用后静默丢（sha256 回归同款病）
        sticker = Sticker(id="a", file="a.png", desc="笑", caption="梗义", visible_text="原文", sha256="d")
        touched = sticker.with_touch(now=1.0)
        assert touched.caption == "梗义" and touched.visible_text == "原文"
        assert touched.sha256 == "d"
        rehashed = sticker.with_sha256("new")
        assert rehashed.caption == "梗义" and rehashed.visible_text == "原文"
