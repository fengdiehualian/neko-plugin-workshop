"""免费线上的 LLM 请求必须带本体人设 —— 不带就被免费文字端 400 掉。

**为什么要有这条**：插件里**自己拼消息**的 LLM 调用在免费线上一直是死的，但
**死得很安静**：请求发出去了、模型也在，只是服务端回 400
`Invalid request: you are not using Lanlan. STOP ABUSE THE API.`，插件记一条
`[VLM] … 看图失败`（或 `except Exception: pass` 什么都不记）就返回空串。
用户看到的是"点了没反应"，而真正的原因在远端。

实测（2026-09-25，`D:\\NekoClaw\\N.E.K.O`）的分界线**不是**进程、不是模型槽、
不是 streaming / key / UA / 是否多模态，而是请求里**带不带本体人设**：

    同一张图、同一个 `free-model`、
    system = 本体人设（`lanlan_prompt_map[her_name]`，3.1k 字符）→ 200
    同一请求去掉 system                                            → 400
    只留人设里的标志句 "…periodically sends some useful information" → 200
    连标志句都不完整                                                → 400

本体的聊天轮本来就带人设，所以聊天一直没事。中招的是四处自己拼消息的调用：

| 调用点 | 症状 |
|---|---|
| `_vlm_describe_locator`（表情包自动描述 / 引用图描述） | 描述永远出不来 |
| `reply_postprocess_node._repair_xml` | XML 修复永远失败（`except: pass`） |
| `reply_buffer_service._generate_ack` | "我在听"那条闸永远不响 |
| `reply_buffer_service._summarize_buffered` | 连发多条后的总结回复永远出不来 |

两条钉住的设计：

1. **免费线才加人设**：自配 API（付费 provider / 本地端点）没有这道门，
   多塞 3k 字符是按 token 付费。所以判据是 base_url 命中免费域名。
2. **逐字复用本体人设，不硬编码那句英文标志句**：人设是本体的资产、随本体升级，
   标志句变了插件跟着变。往插件里抄一句"咒语"既会腐化，也像是绕过校验。
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService
from plugin.plugins.qq_auto_reply.reply_postprocess_node import QQReplyPostprocessNode

BASE = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (BASE / "__init__.py").read_text(encoding="utf-8")
BUFFER_SOURCE = (BASE / "reply_buffer_service.py").read_text(encoding="utf-8")
POSTPROCESS_SOURCE = (BASE / "reply_postprocess_node.py").read_text(encoding="utf-8")

PERSONA = "A fictional character named {LANLAN_NAME} … {MASTER_NAME} is her family."
FREE_URL = "https://www.lanlan.tech/text/v1"
PAID_URL = "https://api.example.com/v1"

CONV_CONFIG = {"model": "free-model", "base_url": FREE_URL, "api_key": "free-access"}


class _FakeCM:
    """假的 config manager：只回答 conversation 槽与角色数据。"""

    def __init__(self, *, base_url: str = FREE_URL, persona: str = PERSONA,
                 character_data=None) -> None:
        self.base_url = base_url
        self.persona = persona
        self.character_data = character_data

    def get_model_api_config(self, slot: str) -> dict:
        assert slot == "conversation", f"看图只该问 conversation，实际问了 {slot}"
        return dict(CONV_CONFIG, base_url=self.base_url)

    def get_character_data(self):
        if self.character_data is not None:
            return self.character_data
        return ("宅久", "宅久皖萱", None, {}, None, {"宅久皖萱": self.persona}, None, None, None)


class _CapturingLLM:
    def __init__(self, sink: list, result: str = "一只黑猫在摇头，表示无奈") -> None:
        self.sink = sink
        self.result = result

    async def ainvoke(self, messages):
        self.sink.append(messages)
        return SimpleNamespace(content=self.result)

    async def aclose(self) -> None:
        return None


def _preload_host_modules() -> None:
    """先把 `main_logic.core` 拉起来再打桩。

    `_apply_role_placeholders` 是惰性导入（见 `session_instruction_service` 的注释），
    而 `main_logic.core` 的导入图里**模块级**就会碰 config manager。若在替换了
    `get_config_manager` 之后才首次导入，那段模块级代码会打在假对象上，测的就不是
    插件逻辑了 —— 所以这里先把导入做完。

    用 `import_module` 而不是 `import main_logic.core`：后者的模块名没有绑定到任何
    变量、只靠副作用，F401 只能靠 noqa 压住；而 CI 上有一条 `--ignore-noqa` 的检查
    （suppression 在那条 gate 下不作数）。`import_module` 是一次**调用**，本身即是"使用"。
    """
    from importlib import import_module

    import_module("main_logic.core")


def _run(monkeypatch, cm: _FakeCM) -> list:
    """跑一次真实的 `_vlm_describe_locator`，返回它发给模型的 messages。"""
    import utils.config_manager as cm_mod
    import utils.llm_client as llm_mod

    _preload_host_modules()
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: cm)
    sent: list = []

    async def _make(**kwargs):
        return _CapturingLLM(sent)

    monkeypatch.setattr(llm_mod, "create_chat_llm_async", _make)

    plugin = SimpleNamespace(
        logger=logging.getLogger("qq.test"),
        _prepare_attachment_image_b64=_fake_b64,
    )
    # 未绑定方法：把真实现挂到裸 namespace 上
    plugin._pick_vlm_config = lambda: QQAutoReplyPlugin._pick_vlm_config(plugin)
    plugin._free_route_system_prompt = (
        lambda cfg: QQAutoReplyPlugin._free_route_system_prompt(plugin, cfg)
    )
    out = asyncio.run(QQAutoReplyPlugin._vlm_describe_locator(
        plugin, "/tmp/x.png", prompt="用一句话描述这张图"))
    assert out == "一只黑猫在摇头，表示无奈"
    assert len(sent) == 1
    return sent[0]


async def _fake_b64(_attachment) -> str:
    return "aGVsbG8="


def test_free_route_sends_the_host_persona_as_system(monkeypatch):
    """免费线上的请求必须带 system，且就是本体那份人设（占位符已替换）。"""
    messages = _run(monkeypatch, _FakeCM())

    assert messages[0]["role"] == "system", f"第一条不是 system：{messages[0]}"
    system = str(messages[0]["content"])
    assert "A fictional character named 宅久皖萱" in system, system[:120]
    assert "{LANLAN_NAME}" not in system, "占位符没替换，模型会看到字面量"
    assert "宅久 is her family" in system, "主人名占位符没替换"


def test_free_route_keeps_the_image_and_prompt_after_the_system(monkeypatch):
    """加 system 不能挤掉图或提示词 —— 顺序与内容都要还在。"""
    messages = _run(monkeypatch, _FakeCM())

    assert [m["role"] for m in messages] == ["system", "user"]
    parts = messages[1]["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[1] == {"type": "text", "text": "用一句话描述这张图"}


def test_paid_route_gets_no_persona(monkeypatch):
    """自配 API 没有这道门：不许白塞 3k 字符人设（按 token 付费）。"""
    messages = _run(monkeypatch, _FakeCM(base_url=PAID_URL))

    assert [m["role"] for m in messages] == ["user"], (
        f"付费/自配线不该加 system，实际 {[m['role'] for m in messages]}"
    )


def test_local_route_gets_no_persona(monkeypatch):
    """本地端点同理（host 里没有 lanlan 字样就不加）。"""
    messages = _run(monkeypatch, _FakeCM(base_url="http://127.0.0.1:8000/v1"))

    assert [m["role"] for m in messages] == ["user"]


def test_missing_persona_still_sends_the_request(monkeypatch):
    """人设取不到时不能连图都不发：请求照发，失败由既有的 [VLM] 日志说话。"""
    messages = _run(monkeypatch, _FakeCM(persona=""))

    assert [m["role"] for m in messages] == ["user"]
    assert messages[0]["content"][0]["type"] == "image_url"


def test_character_data_failure_is_swallowed(monkeypatch):
    """`get_character_data()` 抛异常也不能把整个描述炸掉。"""
    cm = _FakeCM(character_data=("boom",))
    messages = _run(monkeypatch, cm)

    assert [m["role"] for m in messages] == ["user"]


def test_persona_lookup_failure_is_logged(monkeypatch, caplog):
    """取不到人设要留痕：否则下次 400 又只能靠猜。"""
    cm = _FakeCM(character_data=("boom",))
    import utils.config_manager as cm_mod
    import utils.llm_client as llm_mod

    _preload_host_modules()
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: cm)
    monkeypatch.setattr(llm_mod, "create_chat_llm_async", _make_llm)

    plugin = SimpleNamespace(
        logger=logging.getLogger("qq.test"),
        _prepare_attachment_image_b64=_fake_b64,
    )
    plugin._pick_vlm_config = lambda: QQAutoReplyPlugin._pick_vlm_config(plugin)
    plugin._free_route_system_prompt = (
        lambda cfg: QQAutoReplyPlugin._free_route_system_prompt(plugin, cfg)
    )
    with caplog.at_level(logging.INFO, logger="qq.test"):
        out = asyncio.run(QQAutoReplyPlugin._vlm_describe_locator(
            plugin, "/tmp/x.png", prompt="x"))

    assert out == "一只黑猫在摇头，表示无奈"
    assert any("人设文本取不到" in r.message for r in caplog.records), (
        "取不到人设应当留一条日志"
    )


async def _make_llm(**_kwargs):
    return _CapturingLLM([])


# ── 源码级设计约束 ────────────────────────────────────────────────

def test_the_magic_marker_sentence_is_not_hardcoded():
    """不许把免费端那句英文标志句抄进插件。

    抄进去就是"用咒语过校验"：本体一改人设，插件就开始 400，而且没人会想到
    来看这里。正确做法是复用本体人设文本（上面几条钉的就是它）。
    """
    lowered = SOURCE.lower()
    assert "periodically sends some useful information" not in lowered, (
        "标志句被硬编码进插件了 —— 应当复用本体人设文本"
    )


def test_persona_comes_from_the_host_not_from_a_new_prompt(monkeypatch):
    """人设必须来自本体（`lanlan_prompt_map[her_name]`），不是插件另写一份。"""
    body = SOURCE[SOURCE.index("def _free_route_system_prompt"):]
    body = body[: body.index("async def _vlm_describe_locator")]
    assert "get_character_data()" in body, "没从本体取人设"
    assert "data[5]" in body, "人设桶的位置变了：本体的 get_character_data 第 6 项"
    assert "data[1]" in body, "her_name 的位置变了：本体的 get_character_data 第 2 项"
    assert "_apply_role_placeholders" in body, "占位符没替换，模型会看到 {LANLAN_NAME}"


# ── 另外三处"自己拼消息"的调用点 ──────────────────────────────────
#
# 同一个根因、同一份人设：这三处以前也是 `except: pass` 静默失败，用户看到的是
# "功能点了没反应"。它们各自的服务类只依赖 `self.plugin`，所以可以直接实例化。

class _NullLogger:
    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def error(self, *a, **k):
        return None

    def debug(self, *a, **k):
        return None


def _service_plugin() -> SimpleNamespace:
    plugin = SimpleNamespace(logger=_NullLogger(), _emit_log=lambda *a, **k: None)
    plugin._free_route_system_prompt = (
        lambda cfg: QQAutoReplyPlugin._free_route_system_prompt(plugin, cfg)
    )
    return plugin


def _capture_llm(monkeypatch, cm: _FakeCM, result: str = "一只黑猫在摇头，表示无奈") -> list:
    """打桩 config manager 与 LLM 工厂，返回被发出去的 messages 列表。"""
    import utils.config_manager as cm_mod
    import utils.llm_client as llm_mod
    import utils.token_tracker as tt_mod

    _preload_host_modules()
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: cm)
    sent: list = []

    async def _make(**_kwargs):
        return _CapturingLLM(sent, result)

    monkeypatch.setattr(llm_mod, "create_chat_llm_async", _make)
    monkeypatch.setattr(tt_mod, "set_call_type", lambda *_a, **_k: None, raising=False)
    return sent


def test_repair_xml_sends_the_persona_on_the_free_route(monkeypatch):
    """XML 修复：免费线的请求要带人设，否则这条修复路径永远失败。"""
    cm = _FakeCM()
    sent = _capture_llm(monkeypatch, cm, result="<msg>修好了</msg>")
    node = QQReplyPostprocessNode(_service_plugin())

    fixed = asyncio.run(node._repair_xml("<msg>坏掉的XML"))

    assert fixed == "<msg>修好了</msg>"
    assert [m["role"] for m in sent[0]] == ["system", "user"]
    assert "A fictional character named 宅久皖萱" in str(sent[0][0]["content"])


def test_repair_xml_has_no_persona_off_the_free_route(monkeypatch):
    """自配线上不加人设（token 成本），但请求本身照发。"""
    cm = _FakeCM(base_url=PAID_URL)
    sent = _capture_llm(monkeypatch, cm, result="<msg>修好了</msg>")
    node = QQReplyPostprocessNode(_service_plugin())

    asyncio.run(node._repair_xml("<msg>坏掉的XML"))

    assert [m["role"] for m in sent[0]] == ["user"]


def test_generate_ack_sends_the_persona_on_the_free_route(monkeypatch):
    """「我在听」那条闸：免费线必须带人设，否则它永远不响。"""
    cm = _FakeCM()
    sent = _capture_llm(monkeypatch, cm)
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = _service_plugin()

    ack = asyncio.run(service._generate_ack(["在吗", "看这个", "喂"]))

    assert ack == "一只黑猫在摇头，表示无奈"
    assert [m["role"] for m in sent[0]] == ["system", "user"]
    assert "A fictional character named 宅久皖萱" in str(sent[0][0]["content"])


def test_generate_ack_has_no_persona_off_the_free_route(monkeypatch):
    cm = _FakeCM(base_url=PAID_URL)
    sent = _capture_llm(monkeypatch, cm)
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = _service_plugin()

    asyncio.run(service._generate_ack(["在吗"]))

    assert [m["role"] for m in sent[0]] == ["user"]


class _FakeSessionClient:
    """假的 OmniOfflineClient：记下 connect 收到的 instructions。"""

    last: "_FakeSessionClient | None" = None

    def __init__(self, **_kwargs) -> None:
        self.instructions: str | None = None
        self.prompts: list[str] = []
        self._on_text = _kwargs.get("on_text_delta")
        _FakeSessionClient.last = self

    async def connect(self, instructions: str, native_audio: bool = False) -> None:
        self.instructions = instructions

    async def stream_text(self, text: str, **_kwargs) -> None:
        self.prompts.append(text)
        if self._on_text:
            await self._on_text("总结：他们说了三件事", False)


def _patch_session_client(monkeypatch, cm: _FakeCM) -> None:
    import main_logic.omni_offline_client as omni_mod

    _preload_host_modules()
    monkeypatch.setattr(omni_mod, "OmniOfflineClient", _FakeSessionClient)
    monkeypatch.setattr(cm, "aensure_region_resolved", _noop_async, raising=False)


async def _noop_async(*_a, **_k) -> None:
    return None


def test_summarize_buffered_connects_with_the_persona_on_the_free_route(monkeypatch):
    """连发多条后的总结：人设只能经 `connect(instructions=…)` 进这条路。"""
    cm = _FakeCM()
    import utils.config_manager as cm_mod

    _preload_host_modules()
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: cm)
    monkeypatch.setattr(cm, "aensure_region_resolved", _noop_async, raising=False)
    import main_logic.omni_offline_client as omni_mod

    monkeypatch.setattr(omni_mod, "OmniOfflineClient", _FakeSessionClient)

    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = _service_plugin()

    out = asyncio.run(service._summarize_buffered(["a", "b", "c"], True))

    assert out == "总结：他们说了三件事"
    client = _FakeSessionClient.last
    assert client is not None
    assert client.instructions and "A fictional character named 宅久皖萱" in client.instructions, (
        "免费线的总结请求没带人设 —— 这条请求以前必然 400"
    )
    assert "对方连续发了 3 条消息" in client.prompts[0]


def test_summarize_buffered_does_not_connect_off_the_free_route(monkeypatch):
    """自配线上维持原行为：不 connect（避免凭空多一条 system 消息）。"""
    cm = _FakeCM(base_url=PAID_URL)
    import utils.config_manager as cm_mod

    _preload_host_modules()
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: cm)
    monkeypatch.setattr(cm, "aensure_region_resolved", _noop_async, raising=False)
    import main_logic.omni_offline_client as omni_mod

    monkeypatch.setattr(omni_mod, "OmniOfflineClient", _FakeSessionClient)

    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = _service_plugin()

    out = asyncio.run(service._summarize_buffered(["a"], False))

    assert out == "总结：他们说了三件事"
    assert _FakeSessionClient.last.instructions is None


def test_every_self_built_request_asks_the_shared_helper():
    """源码级兜底：这三处必须走同一个助手，不许各写各的判据。"""
    for name, source in (
        ("reply_postprocess_node.py", POSTPROCESS_SOURCE),
        ("reply_buffer_service.py", BUFFER_SOURCE),
    ):
        assert "_free_route_system_prompt" in source, (
            f"{name} 里的自拼请求没走 `_free_route_system_prompt`"
        )
    ack = BUFFER_SOURCE[BUFFER_SOURCE.index("async def _generate_ack"):]
    ack = ack[: ack.index("async def _summarize_buffered")]
    assert ack.count("_free_route_system_prompt") == 1, "「我在听」那条路没带人设"
    summary = BUFFER_SOURCE[BUFFER_SOURCE.index("async def _summarize_buffered"):]
    assert summary.count("_free_route_system_prompt") == 2, (
        "总结的主路与 raw LLM 兜底都必须带人设"
    )
    repair = POSTPROCESS_SOURCE[POSTPROCESS_SOURCE.index("async def _repair_xml"):]
    assert repair.count("_free_route_system_prompt") == 1, "XML 修复那条路没带人设"
