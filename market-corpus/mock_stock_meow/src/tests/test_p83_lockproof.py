# -*- coding: utf-8 -*-
"""P7.3 的守卫：**弹窗防死锁**（0.8.2 真机面板锁死事故的回归防线）。

0.8.2 真机上，「赛博网贷 · 魔爪契约」弹窗和导演台的「赛博弹窗轰炸」
把面板**彻底锁死**了 —— 玩家点任何东西都没反应，连「宣布破产」都点不了。
根因有三条，全部用源码扫描 + 行为断言锁死：

1. **唯一的关窗路径被刷新挡住了**。宿主 ``ActionButton`` 的 ``onResult``
   排在 ``await api.refresh()`` **后面** —— 刷新一失败（超时 / 断网 /
   状态冲突），回调就永远不来，弹窗就永远关不掉。而无限模式下那个弹窗
   **只有「借走 500 万」一个按钮**，于是玩家被锁死。
2. **没有第二条路**。弹窗里没有「我不借了」的按钮，超时也不会自动收工。
3. **轰炸的 5 秒不是硬保证**。旧写法拿**服务端时间戳**减**客户端时钟**
   算剩余时间，时钟一偏、或者载荷停止刷新，遮罩层就永远不撤。

这一阶段的硬要求：

* 借按钮**点了就关窗**（同步关，不等任何回调）；
* 弹窗里**永远**有一个破产逃生按钮；
* 弹窗 30 秒没人操作 → 前端自动关窗 + 服务端自动结算；
* 轰炸 5 秒后**无条件**撤销遮罩（客户端硬截止，不依赖载荷刷新）；
* 面板标题栏有一个**紧急逃生**按钮（两步内联确认，不用 Modal）；
* 引擎层与模式层一个字节都不许动。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_PLUGIN_TOML = _PLUGIN_ROOT / "plugin.toml"


# ---------------------------------------------------------------- 工具


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _panel_code() -> str:
    """面板源码去掉注释与 CSS 模板字符串后的正文。"""
    source = _panel_source()
    kept: list[str] = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            continue
        kept.append(line)
    code = "\n".join(kept)
    marker = "const RRM_CSS = `"
    if marker in code:
        head, _, rest = code.partition(marker)
        _, _, tail = rest.partition("`")
        code = head + "\n/* <RRM_CSS> */\n" + tail
    return code


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def _value(result: Any) -> dict[str, Any]:
    """Ok 结果拆包（``market_context()`` 直接返回 dict，别的入口返回 ``Ok``）。"""
    if isinstance(result, dict):
        return result
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _block(code: str, marker: str) -> str:
    """从 ``marker`` 起，取到下一个**顶格**收尾（``})`` / ``}`` / ``)``）为止的源码。

    取的是「**最早**出现的那一种收尾」，而不是「第一种出现过的收尾」——
    否则前面一段函数里的 ``  }`` 会把后面弹窗的 ``  })`` 挡在取块范围之外。
    """
    assert marker in code, f"源码里找不到 {marker}"
    rest = code.split(marker, 1)[1]
    cut = len(rest)
    for end in ("\n  })\n", "\n  }\n", "\n  )\n"):
        index = rest.find(end)
        if index != -1:
            cut = min(cut, index)
    return rest[:cut]


def _plugin_source() -> str:
    return (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 面板源码


def test_loan_modal_must_not_use_action_button() -> None:
    """**死锁根因**：弹窗里不许再用宿主 ``ActionButton``。

    ``ActionButton`` 的 ``onResult`` 排在 ``await api.refresh()`` 后面 ——
    刷新一失败，回调永远不来，弹窗永远关不掉。弹窗里的按钮必须自己
    ``props.api.call`` + **同步关窗**。
    """
    code = _panel_code()
    block = _block(code, "const loanModal = h(Modal")
    assert "ActionButton" not in block, "网贷弹窗里不许再用 ActionButton（它的 onResult 会被 refresh 挡住）"
    choice_block = _block(code, "const loanChoice = (choice: string) => {")
    assert "props.api" in choice_block, "弹窗按钮必须自己调 props.api.call"


def test_loan_modal_always_has_a_refuse_button() -> None:
    """**逃生通道**：弹窗里永远有一个「我不借了，把我埋了吧」。

    无限模式也必须有 —— 0.8.2 恰恰是无限模式只有「借走 500 万」一个
    按钮，借不到就永远锁死。
    """
    code = _panel_code()
    block = _block(code, "const loanModal = h(Modal")
    assert 'loanChoice("refuse")' in block, "网贷弹窗必须有一个破产逃生按钮"
    assert 'loanChoice("borrow")' in block, "网贷弹窗必须有「借走 500 万」"
    # 逃生按钮必须**无条件**渲染（不在 loanCanBorrow 的三元里）。
    assert "visual.loan_modal_refuse" in block


def test_loan_button_closes_the_modal_synchronously() -> None:
    """点了按钮 → **同步**关窗，不等任何后端回调。"""
    code = _panel_code()
    block = _block(code, "const loanChoice = (choice: string) => {")
    assert "setLoanModalOpen(false)" in block, "点按钮必须同步关窗"
    # 关窗必须在调后端**之前**（否则又是死锁）。
    assert block.index("setLoanModalOpen(false)") < block.index("props.api")


def test_loan_modal_has_a_local_countdown() -> None:
    """30 秒本地倒计时：到点自己关窗。"""
    code = _panel_code()
    assert "LOAN_PROMPT_TIMEOUT_SECONDS = 30" in code
    assert "loanLeft" in code
    # 倒计时归零 → 关窗。
    assert "setLoanModalOpen(false)" in code
    assert "visual.loan_modal_countdown" in code


def test_boom_modal_has_a_client_hard_deadline() -> None:
    """轰炸必须有**客户端硬截止**：不再拿服务端时间戳减客户端时钟。"""
    code = _panel_code()
    assert "BOOM_HARD_LIMIT_MS" in code
    assert "boomDeadline" in code
    # 旧写法（客户端时钟减服务端 until）不许再出现。
    assert "boomUntil" not in code, "轰炸的剩余时间不许再靠客户端时钟算"
    assert "boom.duration" in code or "duration" in code


def test_title_bar_has_an_emergency_escape_button() -> None:
    """标题栏右上角：一个极小的紧急逃生按钮（两步内联确认）。"""
    code = _panel_code()
    assert "rrm-escape" in code, "标题栏必须有紧急逃生按钮"
    assert "escapeArmed" in code, "紧急逃生必须两步确认，防误触"
    escape_block = _block(code, "const escapeGame = () => {")
    assert '"declare_bankrupt"' in escape_block, "紧急逃生必须真的结束这一局"
    assert ".call(" in escape_block, "紧急逃生必须走 props.api.call"


def test_escape_button_does_not_use_a_modal() -> None:
    """紧急逃生**不许**用 Modal 做二次确认。

    万一面板已经卡死，再弹一个 Modal 只会更糟 —— 必须是纯文档流的
    两步内联确认。
    """
    code = _panel_code()
    block = _block(code, "const armEscape = () => {")
    assert "Modal" not in block, "紧急逃生不许再弹 Modal"


def test_panel_still_has_no_layout_breaking_constructs() -> None:
    """P7.3 不许把 0.8.0 那套把面板搞崩的写法加回来。"""
    code = _panel_code()
    for banned in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
        "<div className=",
    ):
        assert banned not in code, f"面板代码里不许再出现 {banned}"

    # P14.0：内联的**线性渐变**解禁（档位卡 / 资金水位条要用它）。
    # 但角度仍然锁死：0.8.0 那条 X 线条 = 绝对定位 + **对角**渐变，
    # 定位已经被禁死，这里再禁一次对角 ⇒ 三重保险，X 线条不可能复活。
    for match in re.finditer(r"([a-z]+)-gradient\(([^)]*)\)", code):
        kind, body = match.group(1), match.group(2)
        assert kind == "linear", f"只用 linear-gradient，发现 {kind}-gradient"
        angle = re.search(r"(\d+(?:\.\d+)?)deg", body)
        if angle:
            assert float(angle.group(1)) in (0.0, 90.0, 180.0), (
                f"内联渐变只允许纵向 / 横向（0/90/180deg），发现 {angle.group(1)}deg"
            )


# ---------------------------------------------------------------- 后端


def test_loan_prompt_action_exists() -> None:
    """``loan_prompt`` 入口必须在（弹窗的唯一出口）。"""
    source = _plugin_source()
    assert 'id="loan_prompt"' in source
    assert "async def loan_prompt(" in source
    for choice in ("open", "borrow", "refuse"):
        assert f'"{choice}"' in source, f"loan_prompt 必须支持 {choice}"


def test_loan_prompt_borrow_never_returns_an_error(plugin) -> None:
    """**借不到也返回 Ok** —— 借不到只是「这次没借到」，不是操作失败。

    返回 Err 会让前端走 catch 分支；前端虽然也会关窗，但契约上
    「借不到」根本不该是错误。
    """
    _value(_run(plugin.join(mode="classic_arcade", seed=9305)))
    result = _run(plugin.loan_prompt(choice="borrow"))
    assert result.is_ok(), "经典模式借不到高利贷也必须返回 Ok"
    payload = _value(result)
    assert payload["granted"] == 0.0
    assert payload["note"], "必须给玩家一句说明"


def test_loan_prompt_borrow_grants_the_loan_in_infinite_mode(plugin) -> None:
    """无限模式点「借走 500 万」→ 真的借到（引擎既有参数）。"""
    _value(_run(plugin.join(mode="infinite", seed=9305)))
    payload = _value(_run(plugin.loan_prompt(choice="borrow")))
    assert payload["granted"] > 0.0, "无限模式必须真的借到钱"
    assert payload["loan_debt"] > 0.0


def test_loan_prompt_borrow_twice_still_returns_ok(plugin) -> None:
    """当天借过一次还点「借走 500 万」→ **必须还是 Ok**（不许 Err）。

    这正是 0.8.2 死锁的触发条件：``loan_already_taken`` 是 Err，
    而弹窗唯一的关窗路径就挂在这个 Err 上。
    """
    _value(_run(plugin.join(mode="infinite", seed=9305)))
    _value(_run(plugin.loan_prompt(choice="borrow")))
    result = _run(plugin.loan_prompt(choice="borrow"))
    assert result.is_ok(), "重复借款必须返回 Ok（failed=True），不是 Err"
    payload = _value(result)
    assert payload["granted"] == 0.0
    assert payload["failed"] is True


def test_loan_prompt_refuse_kills_the_player(plugin) -> None:
    """「我不借了，把我清出去吧」→ 当场结束这一局（引擎既有破产原因）。"""
    _value(_run(plugin.join(mode="infinite", seed=9305)))
    _run(plugin.loan_prompt(choice="refuse"))
    payload = _value(_run(plugin.market_context()))
    assert payload.get("stage") != "playing", "破产之后必须离开对局"


def test_loan_prompt_open_registers_the_deadline(plugin) -> None:
    """``choice=open`` 登记弹窗已打开，并给出 30 秒截止时刻。"""
    _value(_run(plugin.join(mode="classic_beginner", seed=9305)))
    payload = _value(_run(plugin.loan_prompt(choice="open")))
    assert payload["timeout_seconds"] == 30
    assert payload["deadline"] > 0.0
    assert plugin._loan_prompt_at > 0.0, "服务端必须记住「弹窗开着」"


def test_loan_prompt_watchdog_auto_borrows_in_infinite_mode(plugin) -> None:
    """**服务端兜底**：30 秒没人操作 → 无限模式自动强制借入。"""
    _value(_run(plugin.join(mode="infinite", seed=9305)))
    from plugins.mock_stock_meow.storage.session import load_session

    session = _run(
        load_session(plugin._repo, plugin.state, engine_config=_run(plugin._engine_config()))
    )
    assert session is not None
    # 人为把「弹窗打开」的时刻推到 100 秒前。
    plugin._loan_prompt_at = time.time() - 100.0
    plugin._loan_prompt_day = int(session.player.day)
    _run(plugin._loan_prompt_watchdog())

    session = _run(
        load_session(plugin._repo, plugin.state, engine_config=_run(plugin._engine_config()))
    )
    assert session is not None
    assert session.player.loan_debt > 0.0, "无限模式超时必须自动借入"
    assert plugin._loan_prompt_at == 0.0, "超时处理完必须清掉待办"


def test_loan_prompt_watchdog_auto_bankrupts_in_classic_mode(plugin) -> None:
    """经典模式没有高利贷 → 超时只能「拒绝并破产」。"""
    _value(_run(plugin.join(mode="classic_beginner", seed=9305)))
    from plugins.mock_stock_meow.storage.session import load_session

    session = _run(
        load_session(plugin._repo, plugin.state, engine_config=_run(plugin._engine_config()))
    )
    assert session is not None
    plugin._loan_prompt_at = time.time() - 100.0
    plugin._loan_prompt_day = int(session.player.day)
    _run(plugin._loan_prompt_watchdog())

    # 破产之后这一局就没了（``market_state.player_id`` 被清空）。
    payload = _value(_run(plugin.market_context()))
    assert payload.get("stage") != "playing", "经典模式超时必须判定破产"


def test_watchdog_is_wired_into_the_heartbeat() -> None:
    """看门狗必须挂在 1 秒心跳上（前端冻住时的唯一指望）。"""
    source = _plugin_source()
    assert "async def _loan_prompt_watchdog(" in source
    heartbeat = source.split("async def market_tick(", 1)[-1]
    heartbeat = heartbeat.split("\n    async def ", 1)[0]
    assert "_loan_prompt_watchdog()" in heartbeat, "心跳必须调用看门狗"


def test_declare_bankrupt_does_not_nest_the_plugin_lock() -> None:
    """``declare_bankrupt`` 必须委托给**无锁**实现。

    ``_locked`` 不可重入：``loan_prompt`` 自己持锁，不能再去调一个同样
    ``@_locked`` 的方法（会死锁）。
    """
    source = _plugin_source()
    assert "async def _do_declare_bankrupt(" in source
    block = source.split("async def declare_bankrupt(", 1)[-1]
    block = block.split("\n    async def ", 1)[0]
    assert "_do_declare_bankrupt(session, engine_config)" in block


def test_panel_payload_carries_the_loan_prompt_countdown(plugin) -> None:
    """面板载荷带上弹窗的倒计时信息（前端据此显示「N 秒后自动结算」）。"""
    _value(_run(plugin.join(mode="classic_beginner", seed=9305)))
    payload = _value(_run(plugin.market_context()))
    block = payload["visual"]["loan_prompt"]
    assert block["timeout_seconds"] == 30
    assert "pending" in block
    assert "can_borrow" in block


def test_boom_payload_carries_the_duration(plugin) -> None:
    """轰炸载荷带上「本来该持续多久」（前端据此算硬上限）。"""
    _value(_run(plugin.join(mode="classic_arcade", seed=9305)))
    payload = _value(_run(plugin.market_context()))
    assert "duration" in payload["visual"]["boom"]


# ---------------------------------------------------------------- 文案 / 版本


def test_new_i18n_keys_exist() -> None:
    table = _i18n()
    for key in (
        "visual.loan_modal_refuse",
        "visual.loan_modal_escape_hint",
        "visual.loan_modal_countdown",
        "visual.escape_exit",
        "visual.escape_confirm",
        "loan.prompt.classic_no_loan",
        "taunt.loan.timeout",
    ):
        assert key in table, f"i18n 缺少 {key}"


def test_panel_i18n_keys_all_exist() -> None:
    """面板里每一个**静态** ``t("...")`` 键在 i18n 里都有条目。"""
    table = _i18n()
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", _panel_code()))
    missing = sorted(key for key in used if key not in table)
    assert not missing, f"i18n 缺少面板用到的键：{missing}"


def test_version_is_bumped() -> None:
    raw = _PLUGIN_TOML.read_text(encoding="utf-8")
    assert 'version = "0.10.5"' in raw


def test_engine_and_mode_layers_are_still_untouched() -> None:
    """P7.3 **不许**碰引擎层与模式层（数值与公式零改动）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"
