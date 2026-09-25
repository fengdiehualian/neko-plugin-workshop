"""面板外观（1.2.0 图库 + 可调背景）的单测。

纯函数（校验/归一/索引操作/旧图迁移）直接走模块级再导出；
六个面板入口链路用 plugin_factory_full 的 FakeStore 走
迁移 → 加图 → 选图保存 → 改缩略图 → 删图 → 悬空引用清理。
运行方式：uv run python -m pytest tests -q
"""

import asyncio

PNG_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
SVG_URL = "data:image/svg+xml;base64,PHN2Zy8+"
THUMB_URL = "data:image/webp;base64,SGVsbG8="


def run(p, entry: str, **args):
    return asyncio.run(getattr(p, entry)(**args))


# ---------- parse_image_data_url：合法性校验 ----------


def test_parse_accepts_png_and_svg(tm) -> None:
    mime, size = tm.parse_image_data_url(PNG_URL)
    assert mime == "image/png" and size == len(PNG_URL)
    mime, _ = tm.parse_image_data_url(SVG_URL)
    assert mime == "image/svg+xml"


def test_parse_mime_case_insensitive(tm) -> None:
    mime, _ = tm.parse_image_data_url("data:image/PNG;base64,AAA")
    assert mime == "image/png"


def test_parse_rejects_non_data_url(tm) -> None:
    for bad in ("", "https://example.com/a.png", "file:///tmp/a.png", None):
        try:
            tm.parse_image_data_url(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"should reject: {bad!r}")


def test_parse_rejects_unsupported_mime(tm) -> None:
    for bad in ("data:text/plain;base64,AAA", "data:image/tiff;base64,AAA", "data:;base64,AAA"):
        try:
            tm.parse_image_data_url(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"should reject: {bad!r}")


def test_parse_rejects_oversized(tm) -> None:
    big = "data:image/png;base64," + "A" * (tm._PANEL_BG_MAX_CHARS + 1)
    try:
        tm.parse_image_data_url(big)
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("should reject oversized")


def test_parse_custom_max_for_thumb(tm) -> None:
    big_thumb = "data:image/webp;base64," + "A" * (tm._GALLERY_THUMB_MAX_CHARS + 1)
    try:
        tm.parse_image_data_url(big_thumb, tm._GALLERY_THUMB_MAX_CHARS)
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("should reject oversized thumb")


def test_parse_rejects_malformed_no_payload(tm) -> None:
    try:
        tm.parse_image_data_url("data:image/png;base64")
    except ValueError:
        pass
    else:
        raise AssertionError("should reject malformed")


# ---------- clamp_appearance：参数归一 ----------


def test_clamp_defaults_reproduce_legacy_look(tm) -> None:
    out = tm.clamp_appearance(None)
    assert out == tm.appearance_defaults()
    # 1.1.x 观感：cover/center/无滤镜/glass=16/dim=0.3/底色与文字强度满格
    assert out["fill"] == "cover"
    assert out["position"] == "center"
    assert out["blur"] == 0.0
    assert out["brightness"] == 100.0
    assert out["saturate"] == 100.0
    assert out["contrast"] == 100.0
    assert out["glass"] == 16.0
    assert out["dim"] == 0.3
    assert out["card_alpha"] == 100.0
    assert out["text_weight"] == 100.0


def test_clamp_numeric_bounds_and_junk(tm) -> None:
    out = tm.clamp_appearance({
        "blur": 999,
        "dim": -5,
        "brightness": "abc",
        "glass": 22.5,
        "text_weight": 10,
    })
    assert out["blur"] == 30.0
    assert out["dim"] == 0.0
    assert out["brightness"] == 100.0  # 坏值回退默认
    assert out["glass"] == 22.5
    assert out["text_weight"] == 40.0  # 下限 40


def test_clamp_enums(tm) -> None:
    out = tm.clamp_appearance({"fill": "COVER", "position": "  center   top ", "bg_id": "g1"})
    assert out["fill"] == "cover"
    assert out["position"] == "center top"
    assert out["bg_id"] == "g1"
    bad = tm.clamp_appearance({"fill": "nowhere", "position": "top top", "bg_id": "x" * 200})
    assert bad["fill"] == "cover"
    assert bad["position"] == "center"
    assert bad["bg_id"] == ""


# ---------- 图库索引纯操作 ----------


def test_gallery_index_normalize_and_ops(tm) -> None:
    raw = {
        "items": [
            {"id": "g1", "name": "a.png", "thumb": THUMB_URL},
            "not-a-dict",
            {"id": ""},
            {"id": "g2", "thumb": "x" * (tm._GALLERY_THUMB_MAX_CHARS + 1)},
        ],
        "next": "bad",
    }
    index = tm.gallery_normalize_index(raw)
    assert [item["id"] for item in index["items"]] == ["g1", "g2"]
    assert index["items"][1]["thumb"] == ""  # 超长缩略图降级为空
    assert index["next"] == 3
    gid = tm.gallery_next_id(index)
    assert gid == "g3" and index["next"] == 4
    assert tm.gallery_remove_item(index, "g1") is True
    assert tm.gallery_remove_item(index, "g1") is False
    assert tm.gallery_normalize_index("junk") == {"items": [], "next": 1}


def test_gallery_add_respects_limit(tm) -> None:
    index = tm.gallery_normalize_index({
        "items": [{"id": f"g{i}"} for i in range(tm._GALLERY_MAX_ITEMS)],
        "next": 99,
    })
    index, added = tm.gallery_add_item(index, {"id": "overflow"})
    assert added is False
    assert len(index["items"]) == tm._GALLERY_MAX_ITEMS


# ---------- legacy_to_gallery：旧版单图迁移 ----------


def test_legacy_migration_carries_dim(tm) -> None:
    migrated = tm.legacy_to_gallery({"data_url": PNG_URL, "mime": "image/png", "size": 42, "dim": 0.55})
    assert migrated is not None
    gid, item, image, appearance = migrated
    assert gid == tm._LEGACY_BG_ID
    assert item["id"] == tm._LEGACY_BG_ID and item["thumb"] == ""
    assert image["data_url"] == PNG_URL
    assert appearance["bg_id"] == tm._LEGACY_BG_ID
    assert appearance["dim"] == 0.55


def test_legacy_migration_rejects_junk(tm) -> None:
    assert tm.legacy_to_gallery(None) is None
    assert tm.legacy_to_gallery({}) is None
    assert tm.legacy_to_gallery({"data_url": "https://x/y.png"}) is None
    # 坏 dim 回退默认而不是拒迁
    migrated = tm.legacy_to_gallery({"data_url": PNG_URL, "dim": "nope"})
    assert migrated is not None and migrated[3]["dim"] == 0.3


# ---------- 入口链路 ----------


def test_get_gallery_fresh_defaults(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    res = run(p, "get_panel_gallery")
    assert isinstance(res, tm.Ok)
    assert res.value["items"] == []
    assert res.value["migrated"] is False
    assert res.value["appearance"]["bg_id"] == ""
    # 无旧图时不写外观记录（首次真保存才落盘）
    assert p.store.data.get("panel_appearance") is None


def test_legacy_migration_flow(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(store_initial={
        "panel_bg": {"data_url": PNG_URL, "mime": "image/png", "size": 42, "dim": 0.6},
    })
    res = run(p, "get_panel_gallery")
    assert isinstance(res, tm.Ok)
    assert res.value["migrated"] is True
    assert [item["id"] for item in res.value["items"]] == ["legacy"]
    assert res.value["appearance"]["bg_id"] == "legacy"
    assert res.value["appearance"]["dim"] == 0.6
    # 原图与参数已落盘，旧 panel_bg key 保留作备份
    assert p.store.data["gallery_img/legacy"]["data_url"] == PNG_URL
    assert p.store.data["panel_bg"]["data_url"] == PNG_URL
    # 再拉一次不重复迁移
    again = run(p, "get_panel_gallery")
    assert again.value["migrated"] is False
    assert [item["id"] for item in again.value["items"]] == ["legacy"]


def test_gallery_add_remove_roundtrip(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    added = run(p, "gallery_add", data_url=PNG_URL, thumb=THUMB_URL, name="wallpaper.png")
    assert isinstance(added, tm.Ok)
    gid = added.value["id"]
    assert gid == "g1"
    assert added.value["items"][0]["thumb"] == THUMB_URL
    saved = run(p, "set_panel_appearance", bg_id=gid, blur=8, dim=0.4)
    assert isinstance(saved, tm.Ok) and saved.value["appearance"]["bg_id"] == gid
    fetched = run(p, "get_gallery_image", item_id=gid)
    assert isinstance(fetched, tm.Ok) and fetched.value["data_url"] == PNG_URL
    # 删除在用图：本体 key 清掉、索引移除、外观 bg_id 解除并落盘
    removed = run(p, "gallery_remove", item_id=gid)
    assert isinstance(removed, tm.Ok)
    assert removed.value["items"] == []
    assert removed.value["appearance"]["bg_id"] == ""
    assert "gallery_img/g1" not in p.store.data
    assert p.store.data["panel_appearance"]["bg_id"] == ""
    assert isinstance(run(p, "get_gallery_image", item_id=gid), tm.Err)


def test_gallery_add_validation(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    assert isinstance(run(p, "gallery_add", data_url="https://x/a.png"), tm.Err)
    assert isinstance(run(p, "gallery_add", data_url="data:image/tiff;base64,AAA"), tm.Err)
    big = "data:image/png;base64," + "A" * (tm._PANEL_BG_MAX_CHARS + 1)
    assert isinstance(run(p, "gallery_add", data_url=big), tm.Err)
    huge_thumb = "data:image/webp;base64," + "A" * (tm._GALLERY_THUMB_MAX_CHARS + 1)
    assert isinstance(run(p, "gallery_add", data_url=PNG_URL, thumb=huge_thumb), tm.Err)
    # 校验失败不写任何图片本体 key（索引可为空或缺失）
    assert not [k for k in p.store.data if str(k).startswith("gallery_img/")]


def test_set_appearance_normalizes_and_clears_dangling(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    added = run(p, "gallery_add", data_url=PNG_URL)
    gid = added.value["id"]
    saved = run(p, "set_panel_appearance", bg_id=gid, fill="repeat", position="right bottom",
                blur=50, brightness=20, glass=25, card_alpha=70, text_weight=55)
    assert isinstance(saved, tm.Ok)
    out = saved.value["appearance"]
    assert out == {
        "bg_id": gid, "fill": "repeat", "position": "right bottom",
        "blur": 30.0, "dim": 0.3, "brightness": 30.0, "saturate": 100.0,
        "contrast": 100.0, "glass": 25.0, "card_alpha": 70.0, "text_weight": 55.0,
    }
    # 悬空 bg_id（指向不存在条目）：静默解除后照常保存（整包替换：其余字段回默认）
    dangling = run(p, "set_panel_appearance", bg_id="nope")
    assert isinstance(dangling, tm.Ok) and dangling.value["appearance"]["bg_id"] == ""
    assert dangling.value["appearance"]["fill"] == "cover"
    # 重新整包保存 → 归一参数落盘，get 回来的与保存结果一致
    run(p, "set_panel_appearance", **out)
    got = run(p, "get_panel_gallery")
    assert got.value["appearance"] == out


def test_gallery_set_thumb(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    added = run(p, "gallery_add", data_url=PNG_URL)  # 无 thumb
    gid = added.value["id"]
    res = run(p, "gallery_set_thumb", item_id=gid, thumb=THUMB_URL)
    assert isinstance(res, tm.Ok)
    assert res.value["items"][0]["thumb"] == THUMB_URL
    assert isinstance(run(p, "gallery_set_thumb", item_id="ghost", thumb=THUMB_URL), tm.Err)
    assert isinstance(run(p, "gallery_set_thumb", item_id=gid, thumb="https://x/a.webp"), tm.Err)
