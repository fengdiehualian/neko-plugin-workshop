"""D 节验收 + 入口/工具层面的诚实性与隐私断言。

这里用的是**真实插件实例**（真实 SDK 基类、真实 PluginStore、真实 i18n），
只有宿主 IPC（config.dump / push_message / logger）是桩。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import GENSHIN_ENTRY, WUWA_ENTRY, FakeLogger, make_config
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.sdk.plugin import Err

QUERY_MARKER = "MARKER9f3a"
GAME_MARKER = "MARKER7c21"

ALLOWED_PUSH_KWARGS = {
    "source",
    "visibility",
    "ai_behavior",
    "parts",
    "priority",
    "metadata",
    "coalesce_key",
}


def ok_value(result: Any) -> dict[str, Any]:
    assert not isinstance(result, Err), f"unexpected Err: {result}"
    return result.value


def err_message(result: Any) -> str:
    assert isinstance(result, Err), f"expected Err, got {result}"
    error = getattr(result, "error", None)
    return str(error) if error is not None else str(result)


async def declare(plugin: GenshinGameBuddyPlugin, name: str) -> dict[str, Any]:
    return ok_value(await plugin.set_game(game=name))


def context_blocks(plugin: GenshinGameBuddyPlugin) -> list[str]:
    return [part["text"] for pushed in plugin.ctx.pushed for part in pushed["parts"]]


# =============================================================================
# D-02 ~ D-07：验收流程
# =============================================================================


@pytest.mark.integration
async def test_acceptance__declare_genshin_then_context_block_contains_genshin_terms(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()

    payload = await declare(plugin, "我在玩原神")
    assert payload["game_id"] == "genshin"
    assert payload["context_injected"] is True
    assert payload["term_count"] > 0

    block = context_blocks(plugin)[-1]
    assert "原神" in block
    assert "元素反应" in block  # core 术语确实进了语境块
    assert "lookup_game_term" in block  # 拉取路径的提示也在


@pytest.mark.integration
async def test_acceptance__lookup_cross_game_is_isolated(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")

    result = await plugin.lookup_game_term(query="声骸")  # 声骸是鸣潮的
    assert result["is_error"] is False
    assert result["output"]["found"] is False
    assert result["output"]["game"] == "原神"
    assert "声骸" not in result["output"]["message"]  # 不回显查询原文


@pytest.mark.integration
async def test_acceptance__switch_game_unloads_old_and_loads_new(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    assert (await plugin.lookup_game_term(query="雷电将军"))["output"]["found"] is True

    await declare(plugin, "鸣潮")
    assert (await plugin.lookup_game_term(query="雷电将军"))["output"]["found"] is False
    assert (await plugin.lookup_game_term(query="声骸"))["output"]["found"] is True
    assert "已从" in context_blocks(plugin)[-1]  # 切换公告只出现在切换那一次


@pytest.mark.integration
async def test_acceptance__restart_restores_current_game(build_plugin) -> None:
    first = build_plugin()
    await first.startup()
    await declare(first, "原神")

    second = build_plugin()
    payload = ok_value(await second.startup())
    assert payload["game_id"] == "genshin"
    assert payload["persisted"] is True
    assert (await second.get_current_game()).value["game"] == "原神"


@pytest.mark.integration
async def test_acceptance__partial_override_merges_correctly(build_plugin) -> None:
    plugin = build_plugin()
    override = plugin.data_path() / "terms" / "genshin" / "core.toml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text('[terms."元素反应"]\nbrief = "覆盖层写的说明"\n', encoding="utf-8")

    await plugin.startup()
    await declare(plugin, "原神")
    block = context_blocks(plugin)[-1]
    assert "覆盖层写的说明" in block  # 覆盖生效
    assert "两种元素相互作用产生的额外效果" not in block  # 默认说明被替换
    # 只覆盖一条：同维度其他术语照旧来自默认层
    assert "深境螺旋" in block


@pytest.mark.integration
async def test_acceptance__malformed_file_degrades_gracefully(build_plugin) -> None:
    first = build_plugin()
    await first.startup()
    await declare(first, "原神")

    override = first.data_path() / "terms" / "genshin" / "characters.toml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text('[terms."坏掉的"\nbroken\n', encoding="utf-8")

    second = build_plugin()
    payload = ok_value(await second.startup())
    assert payload["game_id"] == "genshin"  # 坏文件不阻断启动
    assert any("characters" in note for note in payload["notes"])  # 降级被如实报出
    # 覆盖层坏掉只是跳过那一层：默认层仍然可用（C-12）
    assert (await second.lookup_game_term(query="雷电将军"))["output"]["found"] is True
    assert (await second.lookup_game_term(query="元素反应"))["output"]["found"] is True


@pytest.mark.integration
async def test_acceptance__block_within_budget(build_plugin) -> None:
    plugin = build_plugin(config=make_config(options={"max_context_chars": 120}))
    await plugin.startup()
    await declare(plugin, "原神")
    block = context_blocks(plugin)[-1]
    assert len(block) <= 120


@pytest.mark.integration
async def test_acceptance__push_failure_reports_context_injected_false(build_plugin) -> None:
    plugin = build_plugin(receipt={"submitted": False, "reason": "backpressure"})
    await plugin.startup()
    payload = await declare(plugin, "原神")
    assert payload["context_injected"] is False  # 不谎报"已注入"
    assert payload["term_count"] > 0  # 登记本身是成功的
    assert "语境" in payload["summary"]

    # refresh 走同一条诚实路径：注入失败必须 Err，而不是 Ok
    message = err_message(await plugin.refresh_game_context())
    assert message


# =============================================================================
# D-11 ~ D-14：push_message v2 契约
# =============================================================================


@pytest.mark.integration
async def test_push__only_parts_visibility_ai_behavior(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    assert plugin.ctx.pushed
    for pushed in plugin.ctx.pushed:
        assert set(pushed) <= ALLOWED_PUSH_KWARGS, sorted(pushed)


@pytest.mark.integration
async def test_push__injection_uses_read_not_respond(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    assert [pushed["ai_behavior"] for pushed in plugin.ctx.pushed] == ["read"]


@pytest.mark.integration
async def test_push__injection_visibility_is_empty(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    assert [pushed["visibility"] for pushed in plugin.ctx.pushed] == [[]]


@pytest.mark.integration
async def test_push__submitted_true_still_counts_as_injected(build_plugin) -> None:
    plugin = build_plugin(receipt={"submitted": True})
    await plugin.startup()
    assert (await declare(plugin, "原神"))["context_injected"] is True


@pytest.mark.integration
async def test_push__receipt_none_is_not_treated_as_failure(build_plugin) -> None:
    # 老版本 SDK 的同步提交成功返回 None；把它当失败会造成假阴性。
    plugin = build_plugin(receipt=None)
    await plugin.startup()
    assert (await declare(plugin, "原神"))["context_injected"] is True


# =============================================================================
# A-14 / A-24 ~ A-28 / A-32 / A-33：配置键"改了行为就变"
# =============================================================================


@pytest.mark.integration
async def test_session__store_disabled__entry_ok_but_persisted_false(build_plugin) -> None:
    plugin = build_plugin(config=make_config(store_enabled=False))
    await plugin.startup()
    payload = await declare(plugin, "原神")
    assert payload["persisted"] is False
    assert "不会被记住" in payload["summary"] or "记住" in payload["summary"]
    assert plugin.store.enabled is False


@pytest.mark.integration
async def test_context_pack__max_terms_zero_yields_block_without_term_lines(build_plugin) -> None:
    plugin = build_plugin(config=make_config(options={"context_inject_max_terms": 0}))
    await plugin.startup()
    await declare(plugin, "原神")
    block = context_blocks(plugin)[-1]
    assert block
    assert not any(line.startswith("- ") for line in block.splitlines())


@pytest.mark.integration
async def test_context_pack__max_terms_two_limits_lines(build_plugin) -> None:
    plugin = build_plugin(config=make_config(options={"context_inject_max_terms": 2}))
    await plugin.startup()
    await declare(plugin, "原神")
    block = context_blocks(plugin)[-1]
    assert sum(1 for line in block.splitlines() if line.startswith("- ")) == 2


@pytest.mark.integration
async def test_context_pack__core_layer_always_present_plus_context_terms(build_plugin) -> None:
    """核心层常驻 + context_terms 显式补充（拍板 2.0.56/2.0.57）。"""
    games = {"genshin": {**GENSHIN_ENTRY, "context_terms": ["深境螺旋"]}}
    plugin = build_plugin(config=make_config(games=games, options={"context_inject_max_terms": 40}))
    await plugin.startup()
    await declare(plugin, "原神")
    block = context_blocks(plugin)[-1]
    assert "深境螺旋" in block   # context_terms 显式补充
    assert "元素反应" in block    # 核心层（core 维度）常驻
    assert "雷电将军" not in block # characters 维度留给 lookup_game_term


@pytest.mark.integration
async def test_context_pack__unknown_context_terms_key_is_warned_not_fatal(build_plugin) -> None:
    logger = FakeLogger()
    games = {"genshin": {**GENSHIN_ENTRY, "context_terms": ["不存在的术语", "抽卡"]}}
    plugin = build_plugin(config=make_config(games=games), logger=logger)
    await plugin.startup()
    await declare(plugin, "原神")
    block = context_blocks(plugin)[-1]
    assert "抽卡" in block
    assert "不存在的术语" not in block
    assert "unknown key" in logger.text


@pytest.mark.integration
async def test_lookup__max_matches__caps_returned_entries(build_plugin) -> None:
    one = build_plugin(config=make_config(options={"lookup_max_matches": 1}))
    await one.startup()
    await declare(one, "原神")
    capped = await one.lookup_game_term(query="角色")
    assert capped["output"]["found"] is True
    assert capped["output"]["count"] == 1

    many = build_plugin(config=make_config(options={"lookup_max_matches": 3}))
    await many.startup()
    await declare(many, "原神")
    uncapped = await many.lookup_game_term(query="角色")
    assert uncapped["output"]["count"] == 3
    assert uncapped["output"]["count"] > capped["output"]["count"]


@pytest.mark.integration
async def test_lookup__max_chars_per_term__truncates_each_entry(build_plugin) -> None:
    plugin = build_plugin(config=make_config(options={"lookup_max_chars_per_term": 40}))
    await plugin.startup()
    await declare(plugin, "原神")
    result = await plugin.lookup_game_term(query="雷电将军")
    assert result["output"]["found"] is True
    for card in result["output"]["text"].split("\n\n")[1:]:
        assert len(card) <= 40


@pytest.mark.integration
async def test_reinject__zero_is_off_and_never_pushes(build_plugin) -> None:
    plugin = build_plugin(config=make_config(options={"reinject_every_n_messages": 0}))
    await plugin.startup()
    await declare(plugin, "原神")
    before = len(plugin.ctx.pushed)
    for _ in range(5):
        payload = ok_value(await plugin.on_chat_message(text="随便说点什么", sender=""))
        assert payload["reinjected"] is False
        assert payload["reason"] == "disabled"
    assert len(plugin.ctx.pushed) == before


@pytest.mark.integration
async def test_reinject__n_repushes_after_n_messages(build_plugin) -> None:
    plugin = build_plugin(config=make_config(options={"reinject_every_n_messages": 3}))
    await plugin.startup()
    await declare(plugin, "原神")
    before = len(plugin.ctx.pushed)

    outcomes = [ok_value(await plugin.on_chat_message(text="hi", sender="")) for _ in range(3)]
    assert [item["reinjected"] for item in outcomes] == [False, False, True]
    assert len(plugin.ctx.pushed) == before + 1
    # 重推不是切换：不该再报"已从 X 切换"
    assert "已从" not in context_blocks(plugin)[-1]


@pytest.mark.integration
async def test_reinject__without_current_game_is_a_noop(build_plugin) -> None:
    plugin = build_plugin(config=make_config(options={"reinject_every_n_messages": 1}))
    await plugin.startup()
    payload = ok_value(await plugin.on_chat_message(text="hi", sender=""))
    assert payload["reinjected"] is False
    assert payload["reason"] == "no_game"


@pytest.mark.integration
async def test_registry__enabled_false__excluded_from_list_and_rejected_on_declare(build_plugin) -> None:
    games = {
        "genshin": dict(GENSHIN_ENTRY),
        "wuthering_waves": {**WUWA_ENTRY, "enabled": False},
    }
    plugin = build_plugin(config=make_config(games=games))
    await plugin.startup()

    listing = ok_value(await plugin.list_games())
    assert [item["game_id"] for item in listing["games"]] == ["genshin"]
    assert "鸣潮" not in listing["summary"]

    message = err_message(await plugin.set_game(game="鸣潮"))
    assert "关闭" in message


@pytest.mark.integration
async def test_term_store__missing_terms_dir_fails_that_game_only(build_plugin) -> None:
    games = {
        "genshin": dict(GENSHIN_ENTRY),
        "wuwa": {"display_name": "鸣潮", "aliases": ["鸣潮"], "terms_dir": "terms/does_not_exist"},
    }
    plugin = build_plugin(config=make_config(games=games))
    await plugin.startup()

    message = err_message(await plugin.set_game(game="鸣潮"))
    assert "不存在" in message
    assert "does_not_exist" not in message
    assert ":\\" not in message

    # 其他游戏不受影响
    assert (await declare(plugin, "原神"))["game_id"] == "genshin"


@pytest.mark.integration
async def test_registry__unknown_game_reports_available_names(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    message = err_message(await plugin.set_game(game=GAME_MARKER))
    assert "原神" in message  # 给出可用列表
    assert GAME_MARKER not in message  # 不回显用户原文


@pytest.mark.integration
async def test_refresh_game_context__without_game_is_err(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    assert "登记" in err_message(await plugin.refresh_game_context())


@pytest.mark.integration
async def test_refresh_game_context__reuses_current_game(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    payload = ok_value(await plugin.refresh_game_context())
    assert payload["game_id"] == "genshin"
    assert len(plugin.ctx.pushed) == 2
    assert "已从" not in context_blocks(plugin)[-1]


@pytest.mark.integration
async def test_refresh_game_context__picks_up_edited_override_layer(build_plugin) -> None:
    """文档承诺"改完覆盖层 → 触发一次重新注入即可生效"，这条用例把它钉住。"""
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    assert "覆盖层改后的说明" not in context_blocks(plugin)[-1]

    override = plugin.data_path() / "terms" / "genshin" / "core.toml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text('[terms."元素反应"]\nbrief = "覆盖层改后的说明"\n', encoding="utf-8")

    ok_value(await plugin.refresh_game_context())
    assert "覆盖层改后的说明" in context_blocks(plugin)[-1]


@pytest.mark.integration
async def test_config_change__drops_game_removed_from_manifest(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")

    plugin.config.payload = make_config(games={"wuthering_waves": dict(WUWA_ENTRY)})
    payload = ok_value(await plugin.config_change())
    assert payload["outcome"] == "dropped"
    assert payload["game_id"] == ""
    assert (await plugin.get_current_game()).value["game"] == ""


@pytest.mark.integration
async def test_config_change__keeps_game_still_registered(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")

    plugin.config.payload = make_config(options={"context_inject_max_terms": 1})
    payload = ok_value(await plugin.config_change())
    assert payload["outcome"] == "reloaded"
    assert payload["game_id"] == "genshin"


@pytest.mark.integration
async def test_shutdown__unloads_state(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    assert ok_value(await plugin.shutdown())["status"] == "stopped"
    assert (await plugin.get_current_game()).value["game"] == ""


# =============================================================================
# LLM 工具返回形状
# =============================================================================


@pytest.mark.integration
async def test_llm_tool__set_current_game_success_shape(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    result = await plugin.set_current_game(game="Genshin")
    assert result["is_error"] is False
    assert result["output"]["registered"] is True
    assert result["output"]["game_id"] == "genshin"
    assert result["output"]["context_injected"] is True


@pytest.mark.integration
async def test_llm_tool__unknown_game_is_not_an_error_envelope(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    result = await plugin.set_current_game(game=GAME_MARKER)
    assert result["is_error"] is False  # 可自纠的正常情况
    assert result["output"]["registered"] is False
    assert result["output"]["reason"] == "unknown_game"
    assert "原神" in result["output"]["available"]
    assert GAME_MARKER not in str(result)


@pytest.mark.integration
async def test_llm_tool__lookup_without_game_is_not_an_error_envelope(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    result = await plugin.lookup_game_term(query="原神")
    assert result["is_error"] is False
    assert result["output"]["found"] is False
    assert result["output"]["reason"] == "no_game"


@pytest.mark.integration
async def test_llm_tool__lookup_hit_shape(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    result = await plugin.lookup_game_term(query="雷电将军")
    assert result["is_error"] is False
    assert result["output"]["found"] is True
    assert result["output"]["count"] == 1
    entry = result["output"]["entries"][0]
    assert entry["key"] == "雷电将军"
    assert "aliases" in entry
    # avoid 为可选字段，空时不写；有则必须是字符串
    if "avoid" in entry:
        assert isinstance(entry["avoid"], str)


# =============================================================================
# E-19 ~ E-22 / I-16：隐私
# =============================================================================


@pytest.mark.privacy
async def test_privacy__lookup_query_text_never_logged(build_plugin) -> None:
    logger = FakeLogger()
    plugin = build_plugin(logger=logger)
    await plugin.startup()
    await declare(plugin, "原神")
    await plugin.lookup_game_term(query=f"{QUERY_MARKER}雷神")
    assert QUERY_MARKER not in logger.text


@pytest.mark.privacy
async def test_privacy__lookup_logs_length_only(build_plugin) -> None:
    logger = FakeLogger()
    plugin = build_plugin(logger=logger)
    await plugin.startup()
    await declare(plugin, "原神")
    query = f"{QUERY_MARKER}雷神"
    await plugin.lookup_game_term(query=query)
    assert f"query_len={len(query)}" in logger.text


@pytest.mark.privacy
async def test_privacy__game_id_is_loggable(build_plugin) -> None:
    logger = FakeLogger()
    plugin = build_plugin(logger=logger)
    await plugin.startup()
    await declare(plugin, "原神")
    assert "genshin" in logger.text  # game_id 不是隐私，允许进日志便于排查


@pytest.mark.privacy
async def test_privacy__user_text_never_appears_in_returned_payloads(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    payloads = [
        await plugin.set_game(game=GAME_MARKER),
        await plugin.lookup_game_term(query=f"{QUERY_MARKER}声骸"),
        await plugin.set_current_game(game=GAME_MARKER),
        await plugin.lookup_game_term(query=f"{QUERY_MARKER}甲"),
        await plugin.get_current_game(),
        await plugin.list_games(),
    ]
    blob = "\n".join(str(getattr(item, "value", item)) for item in payloads)
    assert QUERY_MARKER not in blob
    assert GAME_MARKER not in blob


@pytest.mark.privacy
async def test_privacy__no_bus_reads(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    await plugin.lookup_game_term(query="雷电将军")
    await plugin.on_chat_message(text="hi", sender="")
    await plugin.refresh_game_context()
    await plugin.shutdown()
    assert plugin.ctx.bus.uses == 0


@pytest.mark.privacy
async def test_privacy__rendered_payloads_never_contain_absolute_paths(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    block = context_blocks(plugin)[-1]
    assert ":\\" not in block
    assert "/home/" not in block
    assert str(plugin.plugin_dir) not in block


# =============================================================================
# detect_game：OCR 文本的游戏归属判定（拍板 2.0.14 云游戏/多游戏）
# =============================================================================


@pytest.mark.integration
async def test_detect_game__genshin_terms_attribute_to_genshin(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    # 多条原神专有术语 + 场景信号 → 归属 genshin（阈值 2；单条 generic 词不够）
    assert await plugin.detect_game("元素反应 雷电将军 深境螺旋") == "genshin"


@pytest.mark.integration
async def test_detect_game__unrelated_text_yields_none(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    await declare(plugin, "原神")
    # 无任何游戏证据 → none（不硬归到当前游戏，对应 no_game_detected）
    assert await plugin.detect_game("今天天气真好 hello world") == "none"


@pytest.mark.integration
async def test_detect_game__empty_text_yields_none(build_plugin) -> None:
    plugin = build_plugin()
    await plugin.startup()
    assert await plugin.detect_game("") == "none"
