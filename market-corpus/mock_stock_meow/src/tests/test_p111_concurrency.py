"""P11.1 守卫：并发报错根治（上下文非阻塞 + 后端节流 + 前端请求锁）。

这一版修的是**"手动模式狂点【推进一拍】，面板顶部疯狂弹红色警告
``Failed to query plugin UI action context``"**。

根因链（官方源码钉死）
-----------------------
1. 宿主执行任何 UI action **之前**都会先 ``await host.get_ui_context()``
   （``plugin/core/host.py``，**timeout=5.0**）；
2. 失败就抛 ``PLUGIN_UI_CONTEXT_QUERY_FAILED`` / 500
   （``plugin/server/application/plugins/ui_query_service.py``）；
3. 面板把 500 显示成**红色警告**；
4. 插件的上下文处理器 ``market_context`` 原本带 ``@_locked``，
   与所有动作抢**同一把** ``threading.Lock``；
5. ``threading.Lock.acquire()`` 是**阻塞**调用 —— 在 asyncio 的单线程事件
   循环里，阻塞会把**整个事件循环冻住**；
6. 狂点 ⇒ N 个并发请求 ⇒ 第 2 个开始抢锁 ⇒ 事件循环冻住 ⇒
   宿主那次上下文查询排队 ⇒ **5 秒超时 ⇒ 500**。

三道防线
--------
* **插件层（根治）**：上下文处理器**非阻塞**拿锁，拿不到就返回**缓存快照**；
  tick 流水线也改成非阻塞拿锁。⇒ 事件循环**永远不会被冻住**。
* **插件层（节流）**：两次推进至少隔 1 秒，太快的**静默丢弃**
  （返回温和的 ``Ok``，**绝不**抛红色 Error）。
* **面板层（体验）**：全局请求锁 —— 请求在飞时按钮禁用 + "推进中"。

三层都要有：插件层的锁挡住"多标签页 / LLM 工具调用"，面板层的锁挡住"手抖"。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_TOML = _PLUGIN_ROOT / "plugin.toml"


# ---------------------------------------------------------------- 读文件的家伙


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _init_text() -> str:
    return _INIT.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
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
    return "\n".join(kept)


def _panel_code() -> str:
    source = _strip_comments(_panel_text())
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        body, _, tail = rest.partition("`")
        source = head + "\n/* <RRM_CSS> */\n" + tail
    return source


def _panel_css() -> str:
    source = _panel_text()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    # CSS 是一整块反引号模板字符串，它的**结束**反引号独占一行。
    # 用"独占一行"来找结尾 —— 注释里混进来的反引号（哪怕在行中间）骗不过它。
    # （P11.1 实测：分区取第一个反引号的话，注释里的反引号会**当分隔符**被
    #  吞掉，守卫反而测不出来 —— 那就等于没守卫。）
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号（它必须独占一行）"
    body = rest[:end]
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


def _method_body(source: str, name: str) -> str:
    """截出某个方法/函数的函数体（到下一个同级定义/注释块为止）。"""
    for marker in (f"    def {name}(", f"    async def {name}("):
        if marker in source:
            break
    else:
        raise AssertionError(f"源码里找不到 {name}")
    _, _, tail = source.partition(marker)
    kept: list[str] = []
    for line in tail.split("\n")[1:]:
        if (
            line.startswith("    def ")
            or line.startswith("    @")
            or line.startswith("    # ---")
            or line.startswith("    async def ")
        ):
            break
        kept.append(line)
    return "\n".join(kept)


def _tsx_block(source: str, marker: str) -> str:
    """截出 TSX 里某段代码的函数体（**按花括号配平**）。

    先把模板串与引号串抹掉 —— 否则字符串里的花括号会把配平算歪；
    但**返回的是原文**（抹过的版本只用来数花括号）。
    """
    at = source.index(marker)
    rest = source[at:]
    stripped = re.sub(r"`(?:[^`\\]|\\.)*`", "``", rest)
    stripped = re.sub(r"'(?:[^'\\]|\\.)*'", "''", stripped)
    stripped = re.sub(r'"(?:[^"\\]|\\.)*"', '""', stripped)
    depth = 0
    paren = 0
    started = False
    lines = rest.split("\n")
    stripped_lines = stripped.split("\n")
    for index, line in enumerate(stripped_lines):
        for ch in line:
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren -= 1
            elif paren == 0 and ch == "{":
                # **只在括号深度为 0 时**数花括号 —— TypeScript 的类型注解
                # （``{ userInitiated?: boolean }``）也带花括号，但它在
                # 圆括号里，不该被算成函数体。
                depth += 1
                started = True
            elif paren == 0 and ch == "}":
                depth -= 1
        if started and depth <= 0:
            return "\n".join(lines[: index + 1])
    return rest


# ══════════════════════════════════════════════════════════════ ① 根因：上下文不许阻塞


def test_market_context_is_not_decorated_with_locked() -> None:
    """上下文处理器**不许**再用 ``@_locked``（那把锁是阻塞的）。"""
    init = _init_text()
    block = init.split("async def market_context(", 1)[1]
    header = init[: init.index("async def market_context(")]
    # 装饰器就在 def 之前的几行里 —— 不能出现 ``@_locked``。
    decorators = [
        line.strip()
        for line in header.splitlines()[-6:]
        if line.strip().startswith("@")
    ]
    assert "@_locked" not in decorators, f"market_context 不能带 @_locked：{decorators}"
    assert "market_context" in block


def test_market_context_uses_a_non_blocking_lock() -> None:
    """上下文拿锁必须**非阻塞**（``blocking=False``）。"""
    body = _method_body(_init_text(), "market_context")
    assert "blocking=False" in body, "上下文拿锁必须是非阻塞的"
    assert "with self._lock" not in body, "上下文不许用 with self._lock（那是阻塞的）"


def test_market_context_falls_back_to_a_cached_snapshot() -> None:
    """锁忙时返回**上一次的快照**，而不是干等。"""
    body = _method_body(_init_text(), "market_context")
    assert "_context_cache" in body, "必须有上下文缓存兜底"
    init = _init_text()
    assert "self._context_cache" in init, "缓存字段必须存在"
    assert "self._context_cache: dict[str, Any] | None = None" in init


def test_tick_pipeline_uses_a_non_blocking_lock() -> None:
    """tick 流水线也必须非阻塞拿锁（心跳与推进一拍共用它）。"""
    body = _method_body(_init_text(), "_run_one_tick")
    assert "blocking=False" in body, "tick 拿锁必须是非阻塞的"
    assert "with self._lock" not in body


def test_run_one_tick_reports_whether_it_ran() -> None:
    """``_run_one_tick`` 要返回"这一拍真的跑了吗"。"""
    body = _method_body(_init_text(), "_run_one_tick")
    assert "-> bool" in _init_text().split("async def _run_one_tick(", 1)[1][:80]
    assert "return False" in body and "return True" in body


# ══════════════════════════════════════════════════════════════ ② 后端节流


def test_advance_throttle_is_at_least_one_second() -> None:
    """两次推进至少隔 1 秒。"""
    from plugins.mock_stock_meow import _ADVANCE_THROTTLE_SECONDS

    assert _ADVANCE_THROTTLE_SECONDS >= 1.0


def test_advance_tick_has_a_timestamp_gate() -> None:
    """``advance_tick`` 必须有时间戳闸门。"""
    body = _method_body(_init_text(), "advance_tick")
    assert "_last_advance_at" in body
    assert "_ADVANCE_THROTTLE_SECONDS" in body


def test_advance_tick_gate_runs_before_the_expensive_work() -> None:
    """闸门必须排在 ``_engine_config()`` / ``_session_or_error()`` **之前**。

    那两步要走 IPC + 查库，正是把宿主的 5 秒预算吃光的地方 ——
    闸门排在它们后面就白搭了。
    """
    body = _method_body(_init_text(), "advance_tick")
    gate = body.index("self._advance_throttled()")
    # 注意：要找**真正的调用**，不能找注释里提到的那几个字。
    assert gate < body.index("await self._engine_config()"), "闸门必须早于 _engine_config()"
    assert gate < body.index("await self._session_or_error("), "闸门必须早于 _session_or_error()"


def test_advance_tick_rechecks_the_gate_right_before_it_runs() -> None:
    """闸门必须判**两次**（真机 8 发齐射真抓到的坑）。

    只判一次的话：两个请求都用"进门时"的旧时间戳判闸门（都以为自己没被
    节流），各自 ``await`` 完再回来 —— 先醒的那个跑完一拍，后醒的那个
    **不会再判一次**闸门，于是接着又跑一拍，两拍只隔了不到 1 秒，
    "两次推进至少隔 1 秒"这条契约就破了。
    """
    body = _method_body(_init_text(), "advance_tick")
    assert body.count("self._advance_throttled()") >= 2, "闸门至少要判两次"
    late = body.rindex("self._advance_throttled()")
    assert late > body.index("await self._session_or_error("), "二次闸门必须在耗时操作之后"
    assert late < body.index("await self._run_one_tick()"), "二次闸门必须在真正推进之前"


def test_advance_tick_records_a_fresh_timestamp() -> None:
    """时间戳要记"**真正推进**的那一刻"，不能是进门那一刻。

    记旧时间戳 ⇒ 后面所有"距上次推进多久"的判断都会偏早，
    二次闸门也就白判了。
    """
    body = _method_body(_init_text(), "advance_tick")
    assert "self._last_advance_at = time.time()" in body, "要用此刻的时间戳"
    assert "self._last_advance_at = now" not in body, "不能用 await 之前抓的旧时间戳"


def test_the_throttle_helper_reads_the_current_time() -> None:
    """节流判定必须读"此刻"的时间（而不是进门那一刻抓的）。"""
    body = _method_body(_init_text(), "_advance_throttled")
    assert "time.time()" in body, "闸门必须用此刻的时间"
    assert "_ADVANCE_THROTTLE_SECONDS" in body
    assert "self._last_advance_at <= 0.0" in body, "没推进过时不该拦"


def test_advance_tick_never_raises_a_red_error_when_too_fast() -> None:
    """太快时**绝不**抛红色 Error（必须是温和的 Ok）。"""
    body = _method_body(_init_text(), "advance_tick")
    assert '_err("advance_tick_busy")' not in body, "太快时不许抛红色 Error"
    assert '"throttled": True' in body, "节流命中必须带 throttled 标记"


def test_skip_day_does_not_get_a_timestamp_gate() -> None:
    """【跳过本日】**刻意不加**时间戳闸门。

    跳过本日本来就每天只能一次（游戏自己的 ``skip_day_used`` 闸门），
    不存在"狂点刷屏"；而"连续快进 14 天"是**合法**用法 ——
    加个时间戳闸门反而会把连续快进打断（隐藏结局「无为而治」就靠它）。
    """
    body = _method_body(_init_text(), "skip_day")
    assert "_last_skip_day_at" not in body, "skip_day 不该有时间戳闸门"
    assert '"throttled": True' not in body, "skip_day 不该有节流分支"


def test_throttle_returns_ok_not_err() -> None:
    """节流命中时返回的是 ``Ok``（不是 ``Err``）。"""
    body = _method_body(_init_text(), "advance_tick")
    markers = [m.start() for m in re.finditer(r'"throttled": True', body)]
    assert markers, "advance_tick 里必须有 throttled 分支"
    for at in markers:
        window = body[max(0, at - 400) : at]
        # 离标记最近的 return 语句，必须是 ``return Ok(``。
        last_return = window.rfind("return ")
        assert last_return >= 0, "throttled 分支前面必须有 return"
        statement = window[last_return:].lstrip()
        assert statement.startswith("return Ok("), (
            f"节流命中必须返回 Ok（不是 Err）：{statement[:40]!r}"
        )


# ══════════════════════════════════════════════════════════════ ③ 面板侧全局请求锁


def test_panel_has_a_global_request_lock() -> None:
    """面板必须有**全局**请求锁（一个 ref + 一个 state）。"""
    code = _panel_code()
    assert "busyRef" in code, "必须有 busyRef（并发判据要用 ref，不能用 state）"
    assert "setBusy(true)" in code and "setBusy(false)" in code
    assert "busyRef.current = true" in code


def test_the_lock_is_released_only_after_the_refresh() -> None:
    """解锁必须发生在"接口返回 **且** 面板刷新完"之后。"""
    body = _tsx_block(_panel_code(), "const callAction")
    call_at = body.index("props.api.call(")
    refresh_at = body.index("props.api.refresh()")
    unlock_at = body.index("busyRef.current = false")
    assert call_at < refresh_at < unlock_at, "顺序必须是 调用 → 刷新 → 解锁"


def test_concurrent_clicks_are_dropped_silently() -> None:
    """已有请求在飞 ⇒ **静默丢弃**（只给温和提示，绝不红色警告）。"""
    body = _tsx_block(_panel_code(), "const callAction")
    assert "if (busyRef.current)" in body
    assert 't("panel.action_busy")' in body, "要有温和的 info 提示"
    dropped = body.split("if (busyRef.current)", 1)[1][:220]
    assert "danger" not in dropped, "并发时不许弹红色警告"


def test_the_advance_button_is_disabled_while_busy() -> None:
    """【推进一拍】在请求在飞时必须禁用 + 显示"推进中"。"""
    pace = _tsx_block(_panel_code(), "function PaceControl(")
    assert "disabled: busy" in pace, "推进按钮必须禁用"
    assert 't("panel.pace_advance_busy")' in pace, "要有 loading 文案"


def test_the_meow_button_is_disabled_while_busy() -> None:
    """【呼叫猫娘】也要跟着全局锁。"""
    code = _panel_code()
    meow_block = code.split('className: "rrm-meow-call"', 1)[1][:400]
    assert "disabled: busy" in meow_block


# ══════════════════════════════════════════════════════════════ ④ 原生组件重构


def test_guide_modal_uses_the_native_steps_components() -> None:
    """《玩法说明》改用宿主的原生 ``Steps`` / ``Step``。"""
    code = _panel_code()
    guide = _tsx_block(code, "function GuideModal(")
    assert "Steps," in guide, "必须用原生 Steps"
    assert "Step," in guide, "必须用原生 Step"
    assert "h(\n        Steps," in guide or "h(Steps," in guide


def test_the_hand_rolled_step_css_is_gone() -> None:
    """手搓的三步样式删干净了（骨架交给宿主原生组件）。"""
    css = _panel_css()
    for gone in (".rrm-guide-step {", ".rrm-guide-num", ".rrm-guide-step-main", ".rrm-guide-step-title"):
        assert gone not in css, f"自绘的步骤样式 {gone} 应当删掉（改用原生 Steps/Step）"


# ══════════════════════════════════════════════════════════════ ⑤ 真跑一遍


def test_context_does_not_block_when_the_lock_is_busy(plugin) -> None:
    """锁被占着时，上下文**立刻**返回缓存（不阻塞）。"""
    run(plugin.join(mode="classic_arcade", seed=8811))
    first = run(plugin.market_context())
    assert isinstance(first, dict)
    assert first.get("stage")

    # 模拟"有人正在跑 tick"：把锁按住不放。
    plugin._lock.acquire()
    try:
        started = time.perf_counter()
        cached = run(plugin.market_context())
        elapsed = time.perf_counter() - started
    finally:
        plugin._lock.release()
    assert isinstance(cached, dict), "锁忙时也要返回载荷，不能抛异常"
    assert elapsed < 1.0, f"上下文不该阻塞（实际 {elapsed:.3f}s）"
    assert cached.get("stage") == first.get("stage"), "锁忙时返回的是缓存快照"


def test_run_one_tick_skips_instead_of_blocking(plugin) -> None:
    """锁被占着时，tick **跳过**这一拍（而不是阻塞事件循环）。"""
    run(plugin.join(mode="classic_arcade", seed=8812))
    plugin._lock.acquire()
    try:
        started = time.perf_counter()
        ran = run(plugin._run_one_tick())
        elapsed = time.perf_counter() - started
    finally:
        plugin._lock.release()
    assert ran is False, "拿不到锁就不该跑这一拍"
    assert elapsed < 1.0, f"不该阻塞（实际 {elapsed:.3f}s）"


def test_rapid_advance_is_throttled_but_not_an_error(plugin) -> None:
    """狂点【推进一拍】：第一个真的走，第二个被**温和**拦下。"""
    run(plugin.join(mode="classic_arcade", seed=8813))
    run(plugin.set_auto_tick(enabled=False))

    first = run(plugin.advance_tick())
    assert first.is_ok(), "第一次推进必须成功"
    assert "throttled" not in first.unwrap()

    second = run(plugin.advance_tick())
    assert second.is_ok(), "第二次**绝不能**是错误（不许红色警告）"
    payload = second.unwrap()
    assert payload.get("throttled") is True
    assert payload.get("reason") == "advance_tick_busy"


def test_advance_works_again_after_the_cooldown(plugin) -> None:
    """等过冷却窗口，又能推进（闸门不是一次性锁死）。"""
    run(plugin.join(mode="classic_arcade", seed=8814))
    run(plugin.set_auto_tick(enabled=False))
    run(plugin.advance_tick())
    run(plugin.advance_tick())  # 被节流
    plugin._last_advance_at = time.time() - 5.0  # 假装冷却窗口已过
    again = run(plugin.advance_tick())
    assert again.is_ok()
    assert "throttled" not in again.unwrap()


def test_two_concurrent_advances_only_move_one_tick(plugin, monkeypatch) -> None:
    """两个**并发**的【推进一拍】：只能推进一拍（真机 8 发齐射抓到的坑）。

    复现路径（和真机一模一样）：

    1. 两个请求几乎同时进门 → 都用"进门时"的旧时间戳判了一次闸门
       （都以为自己没被节流）；
    2. 各自 ``await`` 查配置/查库；
    3. 先醒的那个跑完一拍；
    4. 后醒的那个醒来时 ``_tick_running`` 已经复位 —— **闸门如果不再判一次**，
       它就会接着再跑一拍，于是两拍只隔了不到 1 秒。

    这里用受控的 await 顺序把第 4 步**确定性地**造出来：
    第二个请求的 ``_session_or_error`` 故意慢半拍，等第一个把整拍跑完。
    """
    run(plugin.join(mode="classic_arcade", seed=8815))
    run(plugin.set_auto_tick(enabled=False))

    original = plugin._session_or_error
    calls = {"n": 0}

    async def staggered_session(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return await original(*args, **kwargs)
        # 第二个请求慢半拍 ⇒ 等第一个把整拍跑完，它醒来时 "_tick_running" 已复位
        await asyncio.sleep(0.2)
        return await original(*args, **kwargs)

    monkeypatch.setattr(plugin, "_session_or_error", staggered_session)

    async def _both():
        return await asyncio.gather(plugin.advance_tick(), plugin.advance_tick())

    first, second = run(_both())
    assert first.is_ok() and second.is_ok(), "并发推进**绝不能**抛红色 Error"
    payloads = [first.unwrap(), second.unwrap()]
    advanced = [p for p in payloads if not p.get("throttled")]
    throttled = [p for p in payloads if p.get("throttled")]
    assert len(advanced) == 1, f"并发推进只能走一拍（实际走了 {len(advanced)} 拍）"
    assert len(throttled) == 1, "另一个必须被**温和**节流"
    assert throttled[0].get("reason") == "advance_tick_busy"


def test_concurrent_advances_keep_the_one_second_gap(plugin, monkeypatch) -> None:
    """并发推进也不能把"两次推进至少隔 1 秒"这条契约踩破。

    即使两个请求**前后脚**都跑了（第一个跑完、第二个紧接着跑），
    第二次推进也必须等冷却窗口过去。
    """
    run(plugin.join(mode="classic_arcade", seed=8816))
    run(plugin.set_auto_tick(enabled=False))

    original = plugin._session_or_error
    calls = {"n": 0}

    async def staggered_session(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return await original(*args, **kwargs)

    monkeypatch.setattr(plugin, "_session_or_error", staggered_session)

    async def _both():
        return await asyncio.gather(plugin.advance_tick(), plugin.advance_tick())

    run(_both())
    # 第一个请求记下的时间戳 —— 第二个请求醒来时看到的那个
    stamp = plugin._last_advance_at
    assert stamp > 0.0, "推进过就应该记下时间戳"
    # 冷却窗口内再来一次 ⇒ 必须被节流
    again = run(plugin.advance_tick())
    assert again.is_ok()
    assert again.unwrap().get("throttled") is True, "冷却窗口内不许再推进"


def test_context_survives_a_busy_lock_from_the_start(plugin) -> None:
    """连一次快照都还没有时，锁忙也要返回载荷（不能让宿主 500）。"""
    plugin._lock.acquire()
    try:
        payload = run(plugin.market_context())
    finally:
        plugin._lock.release()
    assert isinstance(payload, dict)
    assert payload.get("stage") == "busy"


# ══════════════════════════════════════════════════════════════ ⑥ 红线


def test_engine_and_modes_are_untouched() -> None:
    """引擎层与模式层**零改动**。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


def test_dangerous_css_is_still_banned() -> None:
    """危险 CSS 继续禁用。"""
    css = _panel_css()
    for token in (
        "position: absolute",
        "position: fixed",
        "z-index",
        "filter:",
        "clip-path",
        "inset:",
        "opacity",
        "@keyframes",
        "radial-gradient",
    ):
        assert token not in css, f"注入的 CSS 里不许再出现 {token}"
    for match in re.finditer(r"(?<![\w-])content\s*:", css):
        raise AssertionError(f"注入的 CSS 里不许再出现 content: 属性（{match.group(0)}）")


def test_dangerous_inline_styles_are_still_banned() -> None:
    """面板代码里不许出现危险的内联样式。"""
    code = _panel_code()
    for token in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
    ):
        assert token not in code, f"面板代码里不许再出现 {token}"

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


def test_new_strings_are_in_the_dictionary() -> None:
    """P11.1 新增的文案必须进 i18n 词典。"""
    i18n = _i18n()
    for key in (
        "panel.pace_advance_busy",
        "panel.action_busy",
        "panel.action_throttled",
    ):
        assert key in i18n, f"i18n 里缺 {key}"
        assert i18n[key].strip(), f"{key} 是空的"


def test_panel_uses_only_dictionary_keys() -> None:
    """面板里用到的 ``t()`` 键都必须在词典里。"""
    i18n = _i18n()
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", _panel_code()))
    missing = sorted(key for key in used if key not in i18n)
    assert not missing, f"i18n 缺少面板用到的键：{missing}"


def test_version_is_0_9_5() -> None:
    """版本号 = 0.10.5。"""
    assert 'version = "0.10.5"' in _TOML.read_text(encoding="utf-8")
    assert 'PLUGIN_VERSION = "0.10.5"' in _init_text()
