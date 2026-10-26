# -*- coding: utf-8 -*-
"""统一帮助系统契约测试: 新旧格式归一化、三层寻址、别名与兜底。

不依赖浏览器与网络: 只测数据层(core/help/contract.py)。
"""
from __future__ import annotations

import json
from pathlib import Path

from plugin.plugins.neko_arcade.core.help import (
    auto_groups,
    normalize_help,
    resolve_topic,
    split_help_intent,
)

OLD_FORMAT = {
    "text": "钓鱼说明",
    "commands": [["钓鱼", "抛竿钓鱼"], ["鱼缸", "查看收藏的鱼"], ["鱼市", "出售鱼获"]],
}

NEW_FORMAT = {
    "text": "钓鱼说明",
    "commands": [["钓鱼", "抛竿钓鱼"]],
    "title": "钓鱼",
    "subtitle": "每日抛竿",
    "groups": [
        {"id": "fish", "name": "钓鱼玩法", "icon": "fishing",
         "aliases": ["抛竿", "钓"],
         "commands": [
             ["钓鱼", "抛竿钓鱼"],
             {"cmd": "钓鱼 3", "desc": "连抛 3 次", "params": ["次数"],
              "aliases": ["连钓"]},
         ]},
        {"id": "tank", "name": "鱼缸与鱼市", "icon": "coin", "aliases": ["鱼市", "卖鱼"],
         "commands": [["鱼缸", "查看收藏的鱼"], ["鱼市", "出售鱼获"]]},
    ],
}


# ── 归一化 ──────────────────────────────────────────────
def test_old_format_becomes_flat_doc() -> None:
    doc = normalize_help(OLD_FORMAT, "fishing", "钓鱼")
    assert doc.groups == []
    assert not doc.grouped
    assert doc.command_count == 3
    assert [c.cmd for c in doc.flat] == ["钓鱼", "鱼缸", "鱼市"]
    # 老格式没有 groups → 仍然渲染"扁平单页", 与升级前行为一致
    assert resolve_topic(doc, "").kind == "flat"
    assert resolve_topic(doc, "鱼市").kind == "flat"


def test_new_format_parses_groups_aliases_and_object_commands() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    assert doc.grouped and len(doc.groups) == 2
    assert doc.title == "钓鱼" and doc.subtitle == "每日抛竿"
    fish = doc.groups[0]
    assert fish.aliases == ["抛竿", "钓"]
    assert [c.cmd for c in fish.commands] == ["钓鱼", "钓鱼 3"]
    assert fish.commands[1].params == ["次数"]
    assert fish.commands[1].aliases == ["连钓"]
    assert doc.command_count == 4


def test_view_and_action_kind_detection() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    plain = doc.groups[1].commands[0]
    with_param = doc.groups[0].commands[1]
    assert plain.is_view           # 无参数占位符 = 查看类
    assert not with_param.is_view  # 带 params = 动作类


# ── 三层寻址 ────────────────────────────────────────────
def test_resolve_empty_topic_is_catalog() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    page = resolve_topic(doc, "")
    assert page.kind == "catalog" and page.title == "钓鱼"


def test_resolve_group_by_name_and_alias() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    assert resolve_topic(doc, "鱼缸与鱼市").kind == "group"
    by_alias = resolve_topic(doc, "卖鱼")
    assert by_alias.kind == "group" and by_alias.group.id == "tank"


def test_resolve_command_beats_group() -> None:
    """指令级优先于分组级: 「钓鱼」既像分组别名又是指令名, 必须命中指令页。"""
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    page = resolve_topic(doc, "钓鱼")
    assert page.kind == "command"
    assert page.command.cmd == "钓鱼"


def test_resolve_command_alias_and_param_form() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    assert resolve_topic(doc, "连钓").command.cmd == "钓鱼 3"
    assert resolve_topic(doc, "钓鱼 3").kind == "command"


def test_resolve_miss_returns_catalog_with_suggestions() -> None:
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    page = resolve_topic(doc, "完全不存在的东西")
    assert page.kind == "catalog"
    assert page.miss and page.suggestions
    assert page.topic == "完全不存在的东西"


# ── 零配置兜底 ──────────────────────────────────────────
def test_auto_groups_cluster_flat_commands() -> None:
    flat = normalize_help(OLD_FORMAT, "fishing", "钓鱼").flat
    groups = auto_groups(flat)
    assert groups and sum(len(g.commands) for g in groups) == len(flat)
    assert all(g.name and g.icon for g in groups)


def test_group_nesting_is_supported_one_level() -> None:
    """指令多的游戏必须能分组嵌套: 一级分组 + 子分组(子分组同样挂指令/版式块)。"""
    raw = {
        "groups": [{
            "id": "outer", "name": "装备与道具", "icon": "bag", "aliases": ["纳戒"],
            "commands": [["我的装备", "查看装备栏"]],
            "groups": [{"id": "inner", "name": "背包", "icon": "bag",
                        "commands": [["我的背包", "查看背包"]]}],
        }]
    }
    doc = normalize_help(raw, "g", "G")
    outer = doc.groups[0]
    assert len(outer.groups) == 1 and outer.groups[0].name == "背包"
    # 条数/指令统计要算上子分组
    assert doc.command_count == 2
    assert [c.cmd for c in outer.all_commands()] == ["我的装备", "我的背包"]
    # 子分组名也要能被寻址(用户直接说「背包」)
    page = resolve_topic(doc, "背包")
    assert page.kind == "group" and page.group.name == "背包"
    # 父分组别名照样命中父分组
    assert resolve_topic(doc, "纳戒").group.name == "装备与道具"


def test_command_level_blocks_give_own_page() -> None:
    """「我的纳戒」这类查看指令可以带专属版式块 → 命中时出它自己的图。"""
    raw = {"groups": [{
        "id": "bag", "name": "装备与道具", "icon": "bag", "aliases": ["纳戒"],
        "commands": [
            {"cmd": "我的纳戒", "desc": "查看背包", "kind": "view",
             "blocks": [{"type": "bag", "title": "纳戒背包", "cols": 8, "cells": 16,
                         "filled": [0]}]},
            ["装备 X", "穿戴装备"],
        ]}]}
    doc = normalize_help(raw, "g", "G")
    bag = doc.groups[0].commands[0]
    assert bag.has_own_page and bag.blocks[0]["type"] == "bag"
    assert not doc.groups[0].commands[1].has_own_page
    page = resolve_topic(doc, "我的纳戒")
    assert page.kind == "command" and page.command.has_own_page


def test_auto_groups_opt_in_only() -> None:
    """结构由游戏决定: 没写 groups 也没请求 auto_groups → 平铺(插件不擅自改结构)。"""
    assert normalize_help(OLD_FORMAT, "fishing", "钓鱼").groups == []
    many = {"commands": [[f"指令{i}", f"说明{i}"] for i in range(12)]}
    assert normalize_help(many, "g", "G").groups == []          # 指令多也不擅自分组
    opted = normalize_help({**many, "auto_groups": True}, "g", "G")
    assert opted.groups                                          # 游戏明确请求才分组


def test_game_declared_groups_are_respected() -> None:
    """游戏写了 groups → 插件照做, 分组名/别名/条数都按游戏写的来。"""
    doc = normalize_help(NEW_FORMAT, "fishing", "钓鱼")
    assert [g.name for g in doc.groups] == ["钓鱼玩法", "鱼缸与鱼市"]
    assert [len(g.commands) for g in doc.groups] == [2, 2]


def test_auto_group_matches_name_before_desc() -> None:
    """代劳分组时看**指令名**优先: 说明里出现"猫娘"不该把看图指令拐进伙伴分组。"""
    raw = {"auto_groups": True,
           "commands": [["发图", "猫娘随机发一张图"], ["照片", "看猫娘的自拍"],
                        ["图库", "查看分类"], ["停止", "结束会话"],
                        ["领养小猫", "养一只猫娘"], ["猫猫背包", "查看背包"]]}
    doc = normalize_help(raw, "g", "G")
    names = {g.name for g in doc.groups}
    assert "看图与晒图" in names
    paotu = next(g for g in doc.groups if g.name == "看图与晒图")
    assert {c.cmd for c in paotu.commands} == {"发图", "照片"}


def test_auto_group_skips_help_command() -> None:
    """代劳分组时指令名叫「帮助」的由插件统一拦截, 不进帮助列表。"""
    raw = {"auto_groups": True,
           "commands": [["帮助", "显示玩法帮助"], ["钓鱼", "抛竿"],
                        ["鱼缸", "看鱼"], ["鱼市", "卖鱼"], ["购买 竿", "买竿"],
                        ["换竿 竿", "换竿"]]}
    doc = normalize_help(raw, "fishing", "钓鱼")
    cmds = [c.cmd for g in doc.groups for c in g.commands]
    assert "帮助" not in cmds and len(cmds) == 5


def test_all_games_help_configs_are_addressable() -> None:
    """10 个游戏的 help.json 都能出图: 每条指令都能被寻址到, 不因配置形状失败。"""
    root = Path(__file__).resolve().parents[1]
    cfg_root = root / "data" / "config"
    seen_games = 0
    for gdir in sorted(p for p in cfg_root.iterdir() if p.is_dir()):
        hp = gdir / "help.json"
        if not hp.exists():
            continue
        seen_games += 1
        raw = json.loads(hp.read_text(encoding="utf-8"))
        doc = normalize_help(raw, gdir.name, gdir.name)
        assert doc.title, gdir.name
        commands = doc.all_commands()
        assert commands, gdir.name
        for c in commands:
            page = resolve_topic(doc, c.cmd)
            # 扁平游戏(指令少)整页就是全部指令; 分组游戏应命中指令页或分组页
            assert page.kind in ("command", "group", "flat"), \
                f"{gdir.name}/{c.cmd} → {page.kind}"
            if page.kind == "group":
                # 指令名与分组名重名时落到分组页也算可达(内容一致)
                assert page.group is not None
    assert seen_games >= 10                       # 10 个游戏都覆盖到


# ── 帮助意图识别 ────────────────────────────────────────
def test_split_help_intent() -> None:
    assert split_help_intent("帮助") == ""
    assert split_help_intent("修仙帮助", strip_words=("修仙",)) == ""
    assert split_help_intent("修仙帮助 战斗", strip_words=("修仙",)) == "战斗"
    # 游戏名/触发词由调用方传入剥离(brain 传游戏名 + keywords.json);
    # 不传时保留原文, 由调用方自行处理
    assert split_help_intent("修仙帮助") == "修仙"
    assert split_help_intent("修仙纳戒帮助", strip_words=("修仙",)) == "纳戒"
    assert split_help_intent("怎么玩") == ""
    assert split_help_intent("help") == ""
    assert split_help_intent("抛竿") is None
    assert split_help_intent("") is None


# ── 真实配置: xiuxian 11 个分组 ─────────────────────────
def _xiuxian_doc():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "data" / "config" / "xiuxian" / "help.json")
                     .read_text(encoding="utf-8"))
    return normalize_help(raw, "xiuxian", "诸天修仙")


def test_xiuxian_help_covers_all_commands() -> None:
    doc = _xiuxian_doc()
    assert doc.grouped
    assert len(doc.groups) == 11                     # 11 个功能分组直接可见
    assert sum(len(g.groups) for g in doc.groups) == 0    # 修仙不需要再分二级
    assert doc.command_count == 61
    # 老字段保留(面板 get_game_config 仍读 commands/text)
    assert doc.flat and doc.text


def test_xiuxian_topics_resolve() -> None:
    doc = _xiuxian_doc()
    # 功能分组(含别名)
    bag = resolve_topic(doc, "纳戒")
    assert bag.kind == "group" and bag.group.name == "装备与道具"
    assert bag.group.blocks                          # 分组页带版式块(装备栏/背包格)
    assert resolve_topic(doc, "战斗").group.name == "战斗挑战"
    assert resolve_topic(doc, "生活").group.name == "生活职业"
    # 指令级: 「我的纳戒」有专属图 → 命中指令而不是分组
    cmd = resolve_topic(doc, "我的纳戒")
    assert cmd.kind == "command" and cmd.command.cmd == "我的纳戒"
    assert cmd.command.has_own_page
    assert cmd.command.blocks[0]["type"] == "bag"
    assert resolve_topic(doc, "我的装备").command.has_own_page
    # 带参数指令仍可寻址
    param = resolve_topic(doc, "装备 X")
    assert param.kind == "command" and param.command.cmd == "装备 X"


def test_xiuxian_parent_aliases_do_not_shadow_children() -> None:
    """父分组的别名/名字不能和子分组重名, 否则用户永远只能命中父分组。"""
    doc = _xiuxian_doc()
    for parent in doc.groups:
        parent_words = {_norm_test(w) for w in parent.names()}
        for sub in parent.groups:
            child_words = {_norm_test(w) for w in sub.names()}
            assert not (parent_words & child_words), \
                f"{parent.name} 与子分组 {sub.name} 抢词: {parent_words & child_words}"


def _norm_test(text: str) -> str:
    import re
    return re.sub(r"[\s\u3000]+", "", str(text or "")).lower()
