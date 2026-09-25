"""功能介绍（1.2.7）测试：文案声明表 / payload 组装 / 面板入口 / i18n 对账。

覆盖：
- 声明表完整性：每个 managed 能力都有介绍条目、无孤儿条目；结构合规
  （flow kind 词表、条数上下限、字段非空、流程链首尾形态）；
- build_intro_payload：key 派生规则（前端与 i18n 文件都按此对账，改不得）、
  deps/config_keys/llm/tools 与声明表同源、未登记能力 found=False；
- get_capability_intro 入口：未知 id 拒绝；已知 id 返回完整 payload
  （conftest 的 tr 桩直接吐中文，模拟 zh 用户视角）；
- i18n 对账（防漂移护栏）：core/intros.py 的每条中文原文必须与
  i18n/zh-CN.json 对应 key 的值逐字相等，且 i18n/en.json 同 key 有英文——
  只改 Python 文案不跑生成脚本 / 手抖改错 key，测试当场红。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import json
import pathlib
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _fake_ref(key, default):
    """生产形态的引用构造（SDK tr 返回同构 dict，宿主/前端按 $i18n 解析）。"""
    return {"$i18n": key, "default": default}


# ---------- 声明表完整性 ----------


def test_every_managed_cap_has_intro():
    from forever_companion.core.capabilities import CAPABILITY_SPECS
    from forever_companion.core.intros import CAP_INTROS

    managed = {s.id for s in CAPABILITY_SPECS.values() if s.managed}
    assert managed == set(CAP_INTROS), "功能声明表与介绍声明表不同步"


def test_intro_structure_sane():
    from forever_companion.core.intros import CAP_INTROS, FLOW_KINDS

    for cap_id, intro in CAP_INTROS.items():
        assert intro.purpose and len(intro.purpose) >= 20, cap_id
        assert 2 <= len(intro.scenarios) <= 4, cap_id
        assert 2 <= len(intro.limits) <= 5, cap_id
        assert 3 <= len(intro.flow) <= 6, cap_id
        for node in intro.flow:
            assert node.kind in FLOW_KINDS, (cap_id, node.kind)
            assert node.label, cap_id
        for text in (*intro.scenarios, *intro.limits, *[n.label for n in intro.flow]):
            assert '"' not in text, f"{cap_id}: 正文禁止 ASCII 引号（会截断字符串/污染文案）"


def test_intro_deps_point_to_real_caps():
    """介绍里不该出现依赖声明之外的信息——deps 直接来自声明表，这里核对声明本身。"""
    from forever_companion.core.capabilities import CAPABILITY_SPECS

    for spec in CAPABILITY_SPECS.values():
        for dep in spec.depends:
            assert dep in CAPABILITY_SPECS, (spec.id, dep)


# ---------- payload 组装 ----------


def test_build_intro_payload_key_derivation():
    from forever_companion.core.intros import build_intro_payload

    spec = SimpleNamespace(
        id="whisper", depends=(), config=None, llm="injection", tools=()
    )
    payload = build_intro_payload(spec, _fake_ref)
    assert payload["found"] is True
    assert payload["purpose"]["$i18n"] == "panel.capintro.whisper.purpose"
    assert payload["flow"][0]["label"]["$i18n"] == "panel.capintro.whisper.flow1"
    # 1.2.7 用户反馈去 emoji：payload 不得再携带 icon 字段（防回潮）
    assert all("icon" not in n for n in payload["flow"])
    assert payload["config_keys"] == []
    assert payload["deps"] == []
    assert payload["llm"] == "injection"


def test_build_intro_payload_structural_facts_from_spec():
    from forever_companion.core.capabilities import CAPABILITY_SPECS
    from forever_companion.core.intros import build_intro_payload

    # 依赖/配置键/触点/工具全部现场取自声明表：介绍与开关状态永同一口径
    payload = build_intro_payload(CAPABILITY_SPECS["tone_sense"], _fake_ref)
    assert payload["deps"] == ["mood_engine"]
    assert payload["config_keys"] == ["[emotion_sense].enabled"]
    assert payload["llm"] == "host_http"

    mood = build_intro_payload(CAPABILITY_SPECS["mood_engine"], _fake_ref)
    assert "mood_ebb_tide" in mood["tools"]
    assert mood["config_keys"] == ["[mood].enabled"]


def test_build_intro_payload_missing_intro_found_false():
    from forever_companion.core.intros import build_intro_payload

    spec = SimpleNamespace(id="no_such_cap", depends=(), config=None, llm="none", tools=())
    payload = build_intro_payload(spec, _fake_ref)
    assert payload == {"id": "no_such_cap", "found": False}


def test_flow_chains_have_source_and_output():
    """流程链约定首节点=输入(src)、尾节点=输出(out)：前端箭头语义靠这个不变量。"""
    from forever_companion.core.intros import CAP_INTROS

    for cap_id, intro in CAP_INTROS.items():
        assert intro.flow[0].kind == "src", cap_id
        assert intro.flow[-1].kind == "out", cap_id


# ---------- 面板入口 ----------


def run(coro):
    import asyncio

    return asyncio.run(coro)


def test_get_capability_intro_entry(plugin_factory_full):
    from forever_companion import Ok

    p = plugin_factory_full()
    res = run(p.get_capability_intro(capability_id="mood_engine"))
    assert isinstance(res, Ok)
    data = res.value
    assert data["found"] is True
    # conftest 的 tr 桩直出中文（= zh 用户视角）：正文非空、结构齐
    assert isinstance(data["purpose"], str) and len(data["purpose"]) > 20
    assert data["deps"] == []
    assert data["config_keys"] == ["[mood].enabled"]
    assert data["scenarios"] and data["limits"]
    assert all(isinstance(n["kind"], str) for n in data["flow"])


def test_intro_entry_adapts_keyword_only_tr(plugin_factory_full, monkeypatch):
    """生产签名回归锁：SDK tr 的 default 是 keyword-only。

    1.2.7 实机 bug：入口把 tr 直接交给 build_intro_payload 当 (key, zh)
    双位置参调用 → "tr() takes 1 positional argument but 2 were given"，
    点开介绍卡报 Unexpected error。这里用与生产同构的 keyword-only
    假 tr 验证适配层（lambda k, d: tr(k, default=d)）存在且生效。
    """
    import forever_companion.mixins.capabilities as cap_mod
    from forever_companion import Ok

    calls = []

    def prod_like_tr(key, *, default="", **params):
        # 与 plugin.sdk.shared.i18n.tr 同构：default 不可位置传
        calls.append(key)
        return {"$i18n": key, "default": default}

    monkeypatch.setattr(cap_mod, "tr", prod_like_tr)
    p = plugin_factory_full()
    res = run(p.get_capability_intro(capability_id="whisper"))
    assert isinstance(res, Ok)
    payload = res.value
    # 生产形态的引用 dict 原样透传（前端 resolveText 按 $i18n 展开）
    assert payload["purpose"]["$i18n"] == "panel.capintro.whisper.purpose"
    assert payload["flow"] and payload["flow"][0]["label"]["$i18n"].endswith("flow1")
    assert calls and all(c.startswith("panel.capintro.") for c in calls)


def test_get_capability_intro_unknown_rejected(plugin_factory_full):
    from forever_companion import Err

    p = plugin_factory_full()
    res = run(p.get_capability_intro(capability_id="nope"))
    assert isinstance(res, Err)


def test_get_capability_intro_json_serializable(plugin_factory_full):
    """payload 必须原样可 JSON 序列化（ZMQ msgpack→bridge→前端全靠这个）。"""
    from forever_companion.core.capabilities import CAPABILITY_SPECS

    p = plugin_factory_full()
    for cap_id in CAPABILITY_SPECS:
        data = run(p.get_capability_intro(capability_id=cap_id)).value
        json.dumps(data, ensure_ascii=False)


# ---------- i18n 对账（防漂移护栏）----------


@pytest.mark.parametrize("locale_file", ["zh-CN.json", "en.json"])
def test_i18n_files_cover_intro_keys(locale_file):
    from forever_companion.core.capabilities import CAPABILITY_SPECS
    from forever_companion.core.intros import (
        CAP_INTROS,
        flow_key,
        limit_key,
        purpose_key,
        scenario_key,
    )

    messages = json.loads((ROOT / "i18n" / locale_file).read_text(encoding="utf-8"))
    for cap_id, intro in CAP_INTROS.items():
        required = [purpose_key(cap_id)]
        required += [scenario_key(cap_id, i) for i in range(1, len(intro.scenarios) + 1)]
        required += [limit_key(cap_id, i) for i in range(1, len(intro.limits) + 1)]
        required += [flow_key(cap_id, i) for i in range(1, len(intro.flow) + 1)]
        missing = [k for k in required if not messages.get(k)]
        assert not missing, f"{locale_file} 缺 key: {missing[:3]} ({cap_id})"

    if locale_file == "zh-CN.json":
        # 中文值必须与 Python 事实源逐字一致（改文案必须走生成脚本重新登记）
        for cap_id, intro in CAP_INTROS.items():
            assert messages[purpose_key(cap_id)] == intro.purpose, cap_id
            for i, s in enumerate(intro.scenarios, 1):
                assert messages[scenario_key(cap_id, i)] == s, (cap_id, i)
            for i, s in enumerate(intro.limits, 1):
                assert messages[limit_key(cap_id, i)] == s, (cap_id, i)
            for i, n in enumerate(intro.flow, 1):
                assert messages[flow_key(cap_id, i)] == n.label, (cap_id, i)
        # 声明表没有的能力不应留孤儿 key
        orphan = [
            k for k in messages
            if k.startswith("panel.capintro.")
            and k.split(".")[2] not in set(CAPABILITY_SPECS)
            and k.split(".")[2] not in {
                "open", "purpose", "scenarios", "limits", "deps", "flowTitle",
                "flowEmpty", "stateOn", "stateOff", "depsNone", "depUpstream",
                "toolsCount", "loading", "loadError", "notFound", "close", "back",
            }
        ]
        assert not orphan, f"孤儿 capintro key: {orphan[:3]}"
