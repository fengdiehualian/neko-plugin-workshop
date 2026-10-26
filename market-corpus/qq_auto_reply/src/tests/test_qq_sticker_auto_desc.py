"""上传表情包后自动用 VLM 解析描述：走既有那条看图路径，失败必须能兜底。

**为什么要有这条**：`desc` 是后端必填、也是猫娘挑图的唯一依据。以前只能手填或退回
文件名（`cat-nod` 这种），描述没意义 → 挑出来的表情包就是错的。

自动描述有两个"必须"：

1. **必须复用插件既有的看图路径**（`_vlm_describe_locator`，引用回复的图也走它）。
   再抄一份的话，两处的模型配置迟早漂移 —— 所以这里连"委托关系"一起钉住。
2. **VLM 失败必须能兜底**：模型没配、超时、返回空，都只能退回原来的 desc/文件名，
   **不能**因此把图丢掉或把描述写成空。上传是一次性的用户动作，失败要留个可用结果。

另外 `describe_sticker` 单独一条：对**以前传的**表情包补描述（那些的描述多半还是
文件名）。它解析不出来时要**报错**，不能静默保留旧值 —— 界面上点了"重新解析"却
什么都没变，用户会以为是自己没点到。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import pathlib
import re
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import STICKER_VLM_PROMPT, QQAutoReplyPlugin

BASE = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (BASE / "__init__.py").read_text(encoding="utf-8")

#: 一张最小的合法 PNG（1x1）
PNG_B64 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100" "05fe02fea7" "0000000049454e44ae426082"
)).decode()


def _plugin(tmp_path: pathlib.Path, vlm_result: str) -> SimpleNamespace:
    """假插件：只需 data_path / logger / 目录缓存，外加一个 VLM 桩。"""
    ns = SimpleNamespace(
        data_path=lambda name: tmp_path / name,
        logger=logging.getLogger("qq.test"),
        session_instruction_service=SimpleNamespace(_sticker_catalog_cache="cached"),
    )
    ns.vlm_calls = []

    async def _describe(locator, *, prompt, max_tokens=60):
        ns.vlm_calls.append({"locator": str(locator), "prompt": prompt, "max_tokens": max_tokens})
        return vlm_result

    ns._vlm_describe_locator = _describe
    return ns


def _upload(plugin, **kw):
    args = {"filename": "cat.png", "data_base64": f"data:image/png;base64,{PNG_B64}", "desc": "手填的描述"}
    args.update(kw)
    return asyncio.run(QQAutoReplyPlugin._asset_upload_sticker(plugin, args))


def _describe(plugin, **kw):
    return asyncio.run(QQAutoReplyPlugin._asset_describe_sticker(plugin, kw))


def _registered(tmp_path: pathlib.Path) -> dict:
    return json.loads((tmp_path / "sticker.json").read_text(encoding="utf-8"))


def test_auto_desc_overwrites_the_typed_description(tmp_path):
    plugin = _plugin(tmp_path, "一只黑猫在摇头，表示无奈")

    res = _upload(plugin, auto_desc=True)

    assert res.is_ok, res
    assert res.value["vlm_used"] is True
    assert res.value["desc"] == "一只黑猫在摇头，表示无奈"
    assert _registered(tmp_path)["1"]["desc"] == "一只黑猫在摇头，表示无奈", "描述没有落盘"


def test_auto_desc_uses_the_sticker_prompt_not_the_generic_one(tmp_path):
    """表情包的描述是**给模型以后挑图用的**，提示词和"描述这张图"不是一回事。"""
    plugin = _plugin(tmp_path, "在笑")

    _upload(plugin, auto_desc=True)

    assert len(plugin.vlm_calls) == 1
    call = plugin.vlm_calls[0]
    assert call["prompt"] == STICKER_VLM_PROMPT, "用的不是表情包那条提示词"
    assert "表情包" in call["prompt"], "提示词里应当说明这是表情包、要怎么描述"
    assert call["locator"].endswith("cat.png"), f"喂给 VLM 的不是刚存下的那张图: {call['locator']}"


def test_no_auto_desc_means_no_model_call(tmp_path):
    """不勾就不该花这次调用 —— 模型调用是要花钱和时间的。"""
    plugin = _plugin(tmp_path, "在笑")

    res = _upload(plugin)  # 不传 auto_desc

    assert res.is_ok, res
    assert res.value["vlm_used"] is False
    assert res.value["desc"] == "手填的描述"
    assert plugin.vlm_calls == [], "没勾自动解析却调了 VLM"


def test_vlm_failure_keeps_the_fallback_description_and_the_image(tmp_path):
    """模型没配 / 超时 / 返回空 —— 不能因此丢图，也不能把描述写成空。"""
    plugin = _plugin(tmp_path, "")  # 桩模拟"解析不出来"

    res = _upload(plugin, auto_desc=True)

    assert res.is_ok, res
    assert res.value["vlm_used"] is False
    assert res.value["desc"] == "手填的描述", "解析失败时应沿用传入的描述"
    assert _registered(tmp_path)["1"]["desc"] == "手填的描述"
    # 图必须还在：上传是一次性动作，失败也留个可用结果
    assert (tmp_path / "sticker" / "cat.png").is_file(), "解析失败把图也弄丢了"


def test_auto_desc_is_advertised_in_the_schema(tmp_path):
    """`additionalProperties: False`：schema 里没写 auto_desc，前端传了会被参数校验拦掉。"""
    assert '"auto_desc": {"type": "boolean"' in SOURCE, "schema 里没有 auto_desc"
    assert '"describe_sticker"' in SOURCE, "schema / dispatch 里没有 describe_sticker"


def test_the_reply_image_path_delegates_to_the_same_helper():
    """引用回复的图也走 `_vlm_describe_locator` —— 两处模型配置不许各拉一份。"""
    body = SOURCE[SOURCE.index("async def _describe_reply_image"):]
    body = body[: body.index("async def _describe_sticker") if "async def _describe_sticker" in body else 800]
    assert "_vlm_describe_locator" in body, "引用回复的图没有走共用助手，配置会漂移"


def test_describe_sticker_reparses_an_existing_entry(tmp_path):
    """给以前传的表情包补描述（那些的描述多半还是文件名）。"""
    plugin = _plugin(tmp_path, "一只猫在摇头")
    _upload(plugin)  # 先不解析，desc 是手填的
    plugin.vlm_calls.clear()

    res = _describe(plugin, id="1")

    assert res.is_ok, res
    assert res.value["desc"] == "一只猫在摇头"
    assert res.value["previous"] == "手填的描述"
    assert _registered(tmp_path)["1"]["desc"] == "一只猫在摇头"
    assert plugin.session_instruction_service._sticker_catalog_cache == "", "没有清目录缓存"


def test_describe_sticker_reports_failure_instead_of_silently_keeping_the_old_desc(tmp_path):
    """解析不出来要报错：界面上点了"重新解析"却什么都没变，用户会以为没点到。"""
    plugin = _plugin(tmp_path, "")
    _upload(plugin)

    res = _describe(plugin, id="1")

    assert res.is_err, res
    assert "VLM_FAILED" in str(res.error)
    assert _registered(tmp_path)["1"]["desc"] == "手填的描述", "报错时不该改描述"


def test_describe_sticker_validates_id_and_file(tmp_path):
    plugin = _plugin(tmp_path, "随便")

    assert _describe(plugin, id="  ").is_err, "空 id 应当报错"
    assert _describe(plugin, id="99").is_err, "不存在的 id 应当报错"

    # 登记在、文件没了 → 报错而不是拿空路径去喂模型
    _upload(plugin)
    (tmp_path / "sticker" / "cat.png").unlink()
    res = _describe(plugin, id="1")
    assert res.is_err, res
    assert "NOT_FOUND" in str(res.error)


# ── 用哪套模型配置：**必须还是 conversation**（使用者明确要求的回退） ──────
#
# 这中间我一度改成"优先本体的 vision 槽"（本体给图片分析专门留了 VISION_MODEL，
# 它自己的 utils/screenshot_utils.py 用的就是这个槽），理由是聊天模型不一定多模态。
# **使用者要求回退**：只把新功能接到既有分析上，不动既有路径用的模型 ——
# 而这两者共用 _vlm_describe_locator，换槽会连带改变引用回复图片描述的行为。
#
# 下面几条就是把这个决定钉住，免得以后有人又"顺手"改回 vision。


class _FakeCM:
    """假的 config manager：记录被问了哪些槽，并返回预设配置。"""

    def __init__(self, mapping: dict[str, dict]) -> None:
        self.mapping = mapping
        self.asked: list[str] = []

    def get_model_api_config(self, slot: str) -> dict:
        self.asked.append(slot)
        if slot not in self.mapping:
            raise KeyError(slot)
        return self.mapping[slot]


def _install_cm(monkeypatch, mapping: dict[str, dict]) -> _FakeCM:
    cm = _FakeCM(mapping)
    import utils.config_manager as cm_mod

    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: cm)
    return cm


_VISION = {"model": "free-vision-model", "base_url": "https://x/v1", "api_key": "k"}
_CONV = {"model": "free-model", "base_url": "https://x/v1", "api_key": "k"}


def test_vlm_uses_the_conversation_slot(monkeypatch):
    """回退后的行为：和插件改动前一致（conversation），而且**不会去问 vision**。"""
    cm = _install_cm(monkeypatch, {"vision": _VISION, "conversation": _CONV})

    cfg = QQAutoReplyPlugin._pick_vlm_config(SimpleNamespace())

    assert cfg is not None
    assert cfg["_slot"] == "conversation", f"应当用 conversation，实际 {cfg['_slot']}"
    assert cfg["model"] == "free-model"
    assert "vision" not in cm.asked, (
        "不该去问 vision 槽 —— 换槽会连带改变引用回复图片描述的行为，使用者要求不动它"
    )


def test_vision_slot_is_never_consulted_even_when_conversation_is_unconfigured(monkeypatch):
    """即使 conversation 没配也不许悄悄改用 vision：那等于又把既有行为改了。"""
    cm = _install_cm(monkeypatch, {"vision": _VISION, "conversation": {"model": "", "base_url": ""}})

    assert QQAutoReplyPlugin._pick_vlm_config(SimpleNamespace()) is None
    assert "vision" not in cm.asked


def test_the_helper_does_not_mention_the_vision_slot():
    """源码级兜底：这个函数里不该出现 vision 槽。

    （`_pick_vlm_config` 的注释里提到 vision 是在解释"为什么没用它"，所以只查
    `get_model_api_config(...)` 的实参。）
    """
    body = SOURCE[SOURCE.index("def _pick_vlm_config"):]
    body = body[: body.index("async def _describe_reply_image")]
    # `\b` 是必须的：注释里引用了本体那个 `aget_model_api_config('vision')`，
    # 不加词边界会把 `aget_…` 里的子串也匹配进来（第一版就误报成"问了 vision"）。
    asked = re.findall(r"\bget_model_api_config\(\s*['\"](\w+)['\"]", body)
    assert asked == ["conversation"], f"这个函数只该问 conversation，实际问了 {asked}"


def test_a_missing_vlm_config_is_logged_not_silent(monkeypatch, caplog):
    """静默返回空会让用户以为是自己没点到 —— 实际原因要留在日志里。

    （这是回退时**保留**的部分：模型槽不动，但失败要有迹可循。）
    """
    _install_cm(monkeypatch, {})

    plugin = SimpleNamespace(logger=logging.getLogger("qq.test"))
    # 拿裸 SimpleNamespace 当 self 调未绑定方法，所以要把真方法挂上去
    plugin._pick_vlm_config = lambda: QQAutoReplyPlugin._pick_vlm_config(plugin)
    with caplog.at_level(logging.INFO, logger="qq.test"):
        out = asyncio.run(QQAutoReplyPlugin._vlm_describe_locator(
            plugin, "/nonexistent.png", prompt="x"))

    assert out == ""
    assert any("没有可用的看图模型配置" in r.message for r in caplog.records), (
        "没有配置时应当留下一条日志说明原因"
    )
