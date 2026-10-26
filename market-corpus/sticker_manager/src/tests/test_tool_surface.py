# pyright: reportMissingImports=false
"""v0.17.0「常驻目录面」的门。

来由是主人实机的原话：「不咋爱发表情包，老是得手动点注入她才去发」。参考侧走查完
定性——不是提醒得不够勤，是**载体不对**：

- 它把 `分类 - 用途` 全表每轮缝进常驻面（`main.py:871-884` + `_wrap/_strip_meme_prompt`），
  而且那张面上**只有许可没有闸**（概率 100%、条数不限，全是模型看不见的事后过滤）；
- 我们的目录原本只活在存在感注入里，而 `ai_behavior="read"` 是**排干即弃的一次性 cue**
  （文字模式等下一个用户话轮，语音模式要等下一次自然热切换——宿主
  `main_logic/core/lifecycle.py:2749-2778`）；
- `sticker_send` 的工具描述才是每轮重发的那根面，且重挂即推活会话（宿主
  `main_logic/core/tool_calling.py:67` 的 `register_tool_and_sync` → `session.update`）。

钉五件事：目录进描述、描述里不许有禁令、只报激活区、库变了会自动重挂、检查点排在描述最前（v0.20.0）。
"""

from __future__ import annotations

from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings
from sticker_manager.core.eagerness import REPLY_FIRST_NOTE, TOOL_NOTE
from sticker_manager.core.tool_surface import build_send_tool_description, catalog_for_tool
from sticker_manager.services.library import Library

# 本轮从描述里清出去的禁令措辞。闸一个都没拆（仍在 `services/sender.py` 硬拦），
# 只是不再常驻告诉她——她每次撞闸当轮由 `_SEND_HINTS` 就地说明。
# 钉这一族是因为"顺手把限制讲清楚"是这里的历史本能：v0.16.x 的正文通篇是这个。
PROHIBITIONS = ("会拒", "别连试", "只有主人点名", "force", "挡下", "最近不重复", "冷却")

_ONE_CATEGORY = "・困与睡（9 张） — 深夜、犯困、准备睡了时用。"


def _st(sid: str, *, group: str = "", desc: str = "", disabled: bool = False) -> Sticker:
    return Sticker(id=sid, file=f"{sid}.png", desc=desc or sid, group=group, disabled=disabled)


def _plugin(tmp_path, *, eagerness: str = "natural"):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True, "send": {"eagerness": eagerness}}}),
    )
    plugin, _host = build_plugin(host)
    plugin._settings = StickerManagerSettings(
        enabled=True,
        send=SendSettings(eagerness=eagerness),
        storage=StorageSettings(),
    )
    return plugin


def _image(tag: str) -> bytes:
    # 内容指纹查重吃字节，所以每张图要有自己的字节，不然第二张直接被拒 duplicate。
    return PNG_BYTES + f"|{tag}".encode("utf-8")


class TestCatalogForTool:
    def test_name_count_and_description_all_land(self):
        stickers = [_st("a", group="困与睡"), _st("b", group="困与睡")]
        text = catalog_for_tool(stickers, {"困与睡": "深夜犯困、撑着不想起时用。"})
        assert "困与睡" in text and "2 张" in text and "撑着不想起" in text

    def test_empty_category_never_reaches_her(self):
        # 陷阱 21 的常驻面版：在册但零张的分类进了描述，她选中就是一发 group_not_found。
        text = catalog_for_tool([_st("a", group="有图类")], {"有图类": "甲", "空类": "乙"})
        assert "有图类" in text and "空类" not in text

    def test_category_whose_only_image_is_disabled_is_gone(self):
        assert catalog_for_tool([_st("a", group="困与睡", disabled=True)], {"困与睡": "深夜"}) == ""

    def test_ungrouped_line_is_stripped(self):
        # 「未分组」是面板侧的记账词，不是她能填进 group 的值（填了必 group_not_found）。
        text = catalog_for_tool([_st("a"), _st("b", group="困与睡")], {})
        assert "未分组" not in text and "困与睡" in text


class TestStandingDescription:
    def test_carries_the_catalog(self):
        text = build_send_tool_description(catalog_for_tool([_st("a", group="呆住与宕机")], {"呆住与宕机": "听懵了、脑子转不动时用。"}), "natural")
        assert "呆住与宕机" in text and "脑子转不动" in text
        assert "group" in text  # 名字就是 group 要填的值，这句得说白

    def test_carries_no_prohibitions_in_any_tier(self):
        for tier in TOOL_NOTE:
            text = build_send_tool_description(_ONE_CATEGORY, tier)
            for word in PROHIBITIONS:
                assert word not in text, f"{tier} 档的描述里又写回禁令了：{word}"

    def test_checkpoint_leads_the_description_in_every_tier(self):
        # The first instruction must not put two model round trips before speech.
        for tier in TOOL_NOTE:
            text = build_send_tool_description(_ONE_CATEGORY, tier)
            assert text.startswith(REPLY_FIRST_NOTE)
            assert "开口前" not in text and "开口之前" not in text
            assert text.index("判断") < text.index("发一张你收藏间"), f"{tier} 档的检查点没排在最前"

    def test_checkpoint_still_grants_a_text_only_exit(self):
        # 检查点要求的是"想过一次"，不是"必须发"。丢掉这个出口就是逼她滥发。
        for tier in TOOL_NOTE:
            text = build_send_tool_description(_ONE_CATEGORY, tier)
            assert "你必须配图" not in text and "一定要发" not in text
            assert "文字回" in text, f"{tier} 档的检查点没留下纯文字的出口"

    def test_says_all_three_forms_are_still_allowed(self):
        # 摆齐三种形态这条没被检查点顶掉：两者共存。
        text = build_send_tool_description("", "natural")
        assert "纯文字" in text and "只发图" in text

    def test_normal_reply_and_optional_caption_use_separate_surfaces(self):
        for tier in TOOL_NOTE:
            text = build_send_tool_description(_ONE_CATEGORY, tier)
            assert "sticker_list(query=正向意图短词)" in text
            assert "sticker_list(group=分类名)" in text
            assert "sticker_send(sticker_id=候选ID)" in text
            assert "正常文字回复照常说" in text and "图片独立发送" in text
            assert "text 留空即可" in text
            assert "只有额外配文才填 text" in text
            assert "配文与图片分条发送" in text
            assert "配文不要在正文重复" in text
            assert "图文一条" not in text and "一起出去" not in text

    def test_empty_catalog_says_so_instead_of_encouraging(self):
        # 没有可发的分类还鼓励她发，只会换来一堆失败调用。
        assert "暂时还没有可发的分类" in build_send_tool_description("", "eager")

    def test_tier_note_still_appended_and_tiers_differ(self):
        texts = {tier: build_send_tool_description(_ONE_CATEGORY, tier) for tier in TOOL_NOTE}
        assert len(set(texts.values())) == len(TOOL_NOTE)
        for tier, note in TOOL_NOTE.items():
            assert texts[tier].endswith(note), f"{tier} 档丢了许可句"
        assert _ONE_CATEGORY in texts["natural"]


class TestZoneScopedCatalog:
    def test_only_the_active_zone_shows_up(self, tmp_path):
        # 陷阱 23 的常驻面版：她的可选面只能从 active_pool() 拿，不许各自拼过滤。
        plugin = _plugin(tmp_path)
        library = plugin._library
        library.load(force=True)
        home = library.active_zone()
        other, error = library.create_zone("另一个区", "")
        assert error == "" and other
        assert library.create_group("本区类", "本区的说明", "")[1] == ""
        assert library.create_group("那区类", "那区的说明", other)[1] == ""
        assert library.add(data=_image("home"), desc="本区图", tags=[], group="本区类")[1] == ""
        assert library.add(data=_image("away"), desc="那区图", tags=[], group="那区类", zone=other)[1] == ""
        text = build_send_tool_description(plugin._send_tool_catalog(), "natural")
        assert "本区类" in text and "那区类" not in text
        # 换区即换目录：同一把尺，两处一起动
        assert library.activate_zone(other)[1] == ""
        text = build_send_tool_description(plugin._send_tool_catalog(), "natural")
        assert "那区类" in text and "本区类" not in text
        assert library.activate_zone(home)[1] == ""


class TestRideOnLibraryChange:
    def test_save_marks_dirty_and_the_sweep_applies_once(self, tmp_path):
        plugin = _plugin(tmp_path)
        applied: list[int] = []
        plugin._apply_send_tool_surface = lambda: (applied.append(1) or True)
        # 构造时脏标是亮的（开机第一拍要把描述对齐一次），先把它消费掉再测"不脏不动"。
        assert plugin._refresh_send_tool_surface(force=False) is True
        assert applied == [1]
        assert plugin._refresh_send_tool_surface(force=False) is True
        assert applied == [1], "不脏就不该动"
        plugin._mark_surface_dirty()
        assert plugin._refresh_send_tool_surface(force=False) is True
        assert applied == [1, 1]
        assert plugin._refresh_send_tool_surface(force=False) is True
        assert applied == [1, 1], "成功后必须清标，否则每拍都白重挂一次"

    def test_failure_keeps_the_flag_so_the_next_tick_retries(self, tmp_path):
        # 重挂失败不许把脏标吞掉：吞了就永远停在旧目录上，且没有任何线索。
        plugin = _plugin(tmp_path)
        plugin._apply_send_tool_surface = lambda: False
        plugin._mark_surface_dirty()
        assert plugin._refresh_send_tool_surface(force=False) is False
        assert plugin._surface_dirty is True

    def test_force_always_applies(self, tmp_path):
        # 启动 / 改配置 / 面板切档走的是 force：那里不读脏标，直接对齐。
        plugin = _plugin(tmp_path)
        applied: list[int] = []
        plugin._apply_send_tool_surface = lambda: (applied.append(1) or True)
        assert plugin._refresh_send_tool_surface() is True
        assert applied == [1]

    def test_library_save_fires_the_hook(self, tmp_path):
        notes: list[int] = []
        library = Library(tmp_path / "lib", on_saved=lambda: notes.append(len(notes) + 1))
        library.load(force=True)
        assert library.save().ok is True
        assert notes == [1]
        assert library.add(data=_image("甲"), desc="甲", tags=[])[1] == ""
        assert notes == [1, 2], "入库也要算一次"

    def test_hook_crash_does_not_fail_the_save(self, tmp_path):
        # 库已经落盘了，不许被下游的打标动作反咬成"保存失败"。
        def boom() -> None:
            raise RuntimeError("surface down")

        library = Library(tmp_path / "lib", on_saved=boom)
        library.load(force=True)
        assert library.save().ok is True
