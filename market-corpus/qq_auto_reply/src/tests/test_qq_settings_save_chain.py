"""设置保存链路看门狗：前端 → dashboard → settings_schema 三层键名必须完全对齐。

**为什么要有这条**（2026-09-23 的真实生产事故）：

    2026-09-23 04:21:46 ERROR - Unexpected error executing 'config'
    TypeError: QQDashboardService.save_settings() got an unexpected keyword argument
               'reply_burst_window_seconds'

前端 `doSave()` 提交了 50 个键，`dashboard_service.save_settings` 只认其中一部分 ——
多出来的一个就让**整个设置保存**崩掉（不是那一个键失效，是全部失效）。当时是靠人工
"补回 17 个具名参数"（提交 `8dd3848`）修的。这类断裂在测试里是零成本的，在生产里
是用户点一次保存就报错，所以这里钉死三条不变量：

1. 前端提交的每个键，`dashboard_service.save_settings` 都必须有形参
   —— 否则生产环境 TypeError，整次保存失败。
2. `dashboard_service` 的每个形参，必须是 `settings_schema` 声明的键（或登记过的别名）
   —— 否则 `_apply_plain_settings` 遍历 `SETTINGS` 时会**静默丢弃**它：
   界面显示"已保存"，重启就回退。
3. `settings_schema` 里每个 `saveable=True` 的键都要在 dashboard 签名里
   —— 否则这个键用户永远改不了（想改成只读就该标成 `saveable=False`）。

这三条各自都便宜，合起来覆盖了"加了键但没接通"的全部三个方向。
"""

from __future__ import annotations

import inspect
import pathlib
import re

from plugin.plugins.qq_auto_reply import settings_schema
from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService

_PLUGIN_DIR = pathlib.Path(settings_schema.__file__).resolve().parent

#: 前端用的历史别名 → settings_schema 的 canonical 名。
#: 见 `settings_service._save_settings_locked` 里两个名字都收的那段注释。
_ALIASES = {
}

#: 认定一个 `args={...}` 对象是"设置保存载荷"的门槛：至少提到这么多个已知
#: dashboard 形参。用来把 backlog 标签编辑之类的小载荷排除掉。
_PAYLOAD_MIN_KNOWN_KEYS = 5


def _balanced_object(text: str, open_brace: int) -> str:
    """返回与 `open_brace` 处 `{` 配对的括号内文本（跳过模板字符串）。"""
    depth = 0
    i = open_brace
    while i < len(text):
        ch = text[i]
        if ch == "`":
            i += 1
            while i < len(text) and text[i] != "`":
                i += 1
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1: i]
        i += 1
    raise AssertionError("括号不配对，解析器需要跟着改")


def _top_level_keys(body: str) -> set[str]:
    """对象字面量的顶层键（过滤掉嵌套对象里的键）。"""
    depth = 0
    top: list[str] = []
    for ch in body:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if depth == 0:
            top.append(ch)
    return set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", "".join(top)))


def _frontend_settings_payloads() -> dict[str, set[str]]:
    """{文件名: 提交的键集合}，只取设置保存载荷。"""
    dashboard_params = set(
        inspect.signature(QQDashboardService.save_settings).parameters
    )
    found: dict[str, set[str]] = {}
    for path in sorted((_PLUGIN_DIR / "static").glob("*.html")) + sorted(
        (_PLUGIN_DIR / "static").glob("*.js")
    ):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\bargs\s*=\s*\{", text):
            body = _balanced_object(text, match.end() - 1)
            keys = _top_level_keys(body)
            if len(keys & dashboard_params) < _PAYLOAD_MIN_KNOWN_KEYS:
                continue
            found.setdefault(path.name, set()).update(keys)
    return found


def _dashboard_params() -> set[str]:
    return {
        name
        for name in inspect.signature(QQDashboardService.save_settings).parameters
        if name != "self"
    }


def _declared_keys() -> set[str]:
    return {spec.key for spec in settings_schema.SETTINGS}


def _saveable_keys() -> set[str]:
    return {spec.key for spec in settings_schema.SETTINGS if spec.saveable}


def test_the_parser_actually_finds_the_save_payload():
    """先证明检查本身在干活 —— 解析器静默失配会让下面三条全部空过。"""
    payloads = _frontend_settings_payloads()
    assert payloads, "没解析出任何设置保存载荷，说明解析器失配了"
    biggest = max(payloads.values(), key=len)
    # 下限只是「证明解析器真找到了载荷」的防呆值，不是「载荷应有这么多键」。
    # 面板在 D 档收缩后从 22 个注意力控件降到 7 个，因此下限跟着下调。
    assert len(biggest) >= 25, (
        f"最大的载荷只有 {len(biggest)} 个键，前端保存表单应该远不止这些"
    )
    assert "onebot_url" in biggest or "reply_mode" in biggest, (
        "解析出来的不像是设置保存载荷"
    )


def test_every_frontend_key_has_a_dashboard_parameter():
    """不变量 1：前端提交的键必须都有形参 —— 否则生产环境整次保存 TypeError。"""
    params = _dashboard_params()
    problems: list[str] = []
    for name, keys in _frontend_settings_payloads().items():
        missing = keys - params
        if missing:
            problems.append(
                f"{name} 提交了 dashboard 签名里没有的键 {sorted(missing)}"
                f"（2026-09-23 的 'reply_burst_window_seconds' TypeError 就是这一类）"
            )
    assert not problems, "\n".join(problems)


def test_every_dashboard_parameter_is_a_declared_setting():
    """不变量 2：形参必须有对应声明 —— 否则 `_apply_plain_settings` 静默丢弃它。"""
    declared = _declared_keys()
    unknown = sorted(_dashboard_params() - declared - set(_ALIASES))
    assert not unknown, (
        f"dashboard 收了这些参数但 settings_schema 没声明: {unknown}。"
        f"`_apply_plain_settings` 只遍历 SETTINGS，这些键会被静默丢弃 —— "
        f"界面显示已保存、重启就回退"
    )


def test_every_alias_points_at_a_real_key():
    """别名表本身不能漂移。"""
    declared = _declared_keys()
    for alias, canonical in _ALIASES.items():
        assert canonical in declared, f"别名 {alias} 指向了不存在的键 {canonical}"
        assert alias not in declared, f"{alias} 已经是声明键了，不该再当别名"


def test_json_settings_field_is_validated_before_the_payload_is_built():
    """`attention_emotion_multipliers`（文本域里手写 JSON）必须先校验再提交。

    原来的写法把解析失败吞成 `undefined`：

        attention_emotion_multipliers:(function(){try{return JSON.parse(v)}
            catch(e){return undefined}})()

    而 `JSON.stringify` 会**整体省略**值为 `undefined` 的属性，后端于是收不到这个键
    （`if ... is not None` 不成立 → 不写盘），其它 47 个键照常保存、接口返回成功 →
    前端弹「设置已保存」并把文本框回填成**旧值**。

    用户视角：手改情绪倍率时打错一个逗号 → 改动没了 → 还被告知保存成功。
    所以这里钉住两条：不得再把失败吞成 `undefined`；校验必须在构造载荷**之前**，
    失败时要中止整次保存（否则"部分保存 + 假成功"依旧成立）。
    """
    text = (_PLUGIN_DIR / "static" / "napcat.html").read_text(encoding="utf-8")
    start = text.find("async function doSave()")
    assert start != -1, "找不到 doSave()，测试需要跟着改"
    # doSave 到下一个顶层函数为止
    end = text.find("\nasync function ", start + 10)
    body = text[start: end if end != -1 else start + 12000]

    assert "catch(e){return undefined}" not in body, (
        "JSON 解析失败又被吞成 undefined 了 —— 该键会被静默丢弃却提示保存成功"
    )

    parse_at = body.find("JSON.parse(")
    payload_at = body.find("let args={")
    assert parse_at != -1, "doSave 里找不到 JSON.parse，测试需要跟着改"
    assert payload_at != -1, "doSave 里找不到载荷构造，测试需要跟着改"
    assert parse_at < payload_at, (
        "JSON 校验发生在构造载荷之后 —— 失败时已经没法中止整次保存了"
    )

    # 载荷必须引用校验结果，而不是内联再解析一次（内联就没有中止的机会）
    payload = body[payload_at:]
    assert "attention_emotion_multipliers:emoParsed" in payload, (
        "载荷没有引用校验过的解析结果，等于又回到「解析失败静默丢键」"
    )

    # 失败路径必须中止：同一分支里要同时出现 toast 和 return
    guard = body[parse_at:payload_at]
    assert "catch(e){" in guard and "return}" in guard, (
        "JSON 不合法时没有中止保存 —— 仍会出现「部分保存 + 提示已保存」"
    )


# ── 未知键必须留痕，不能纯静默丢弃 ───────────────────────────────────

def _config_save(payload: dict):
    """直接调未绑定的 `_config_save`，用最小桩替换插件。"""
    import asyncio
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    logs: list[tuple[str, str]] = []
    forwarded: list[dict] = []

    async def _fake_save_settings(**kw):
        forwarded.append(kw)
        return {"persisted": True}

    stub = SimpleNamespace(
        _CONFIG_SAVE_KEYS={"onebot_url", "reply_mode"},
        _emit_log=lambda level, msg: logs.append((level, msg)),
        dashboard_service=SimpleNamespace(save_settings=_fake_save_settings),
    )
    result = asyncio.run(QQAutoReplyPlugin._config_save(stub, payload))
    return result, logs, forwarded


def test_unknown_save_keys_are_dropped_but_logged():
    """认不出的键必须丢弃（不能撞服务层），但**必须留一条日志**。

    纯静默丢弃是本插件反复出现的故障形态：调用方以为保存成功，整键消失且没有
    任何痕迹。`proactive_topics` 就是这样 —— 它有专用入口 `save_topics`，走通用
    `save` 时会被无声吃掉。
    """
    _result, logs, forwarded = _config_save(
        {"onebot_url": "ws://0.0.0.0:6199", "proactive_topics": ["a"], "action": "save"}
    )

    assert forwarded, "白名单里的键没有被转发下去"
    assert "proactive_topics" not in forwarded[0], "未知键不该被转发"
    assert forwarded[0].get("onebot_url") == "ws://0.0.0.0:6199"

    warnings = [msg for level, msg in logs if level == "WARNING"]
    assert warnings, "丢弃未知键时没有留下任何日志 —— 用户查不出改动为什么没生效"
    assert "proactive_topics" in warnings[0], (
        f"日志没列出被丢弃的键名: {warnings[0]!r}"
    )


def test_all_unknown_keys_still_error_and_name_them():
    """全是未知键时仍返回错误（既有行为），但错误里要带上键名。"""
    result, _logs, forwarded = _config_save({"proactive_topics": ["a"]})
    assert not forwarded, "没有可识别键时不该调保存"
    text = str(result)
    assert "proactive_topics" in text, (
        f"错误信息没有说明是哪个键不认: {text!r}"
    )


# ── 宿主注入的信封键（`_ctx`）不是"手滑" ─────────────────────────────
#
# 真机 17:59:37 那条：`[Config] save 丢弃了 1 个不可识别的键: ['_ctx']`。
# `_ctx` 是**宿主自己塞的**（两条路径：hosted UI action 加 run_id；agent 派发加
# lanlan_name/conversation_id/latest_user_request/entry_timeout）。把它算成"不可识别"
# 会让每次保存都报一条警告 —— 噪音正好毁掉这条日志存在的意义。

def test_host_envelope_keys_are_not_reported_as_unrecognized():
    _result, logs, forwarded = _config_save(
        {"action": "save", "_ctx": {"run_id": "x", "lanlan_name": "皖萱"},
         "onebot_url": "ws://0.0.0.0:6199"}
    )

    assert forwarded and forwarded[0].get("onebot_url") == "ws://0.0.0.0:6199", "正常键没存下去"
    assert "_ctx" not in forwarded[0], "信封键不该被转发给服务层"
    assert not [msg for level, msg in logs if level == "WARNING"], (
        f"宿主信封被当成手滑键报警了: {logs!r}"
    )


def test_a_real_typo_still_warns_even_alongside_an_envelope():
    """信封要和真手滑**分开**：报警时只点真手滑那个，别把 `_ctx` 也列进去。"""
    _result, logs, forwarded = _config_save(
        {"action": "save", "_ctx": {"run_id": "x"},
         "onebot_url": "ws://0.0.0.0:6199", "proactive_topics": ["a"]}
    )

    assert forwarded, "白名单键没被转发"
    warnings = [msg for level, msg in logs if level == "WARNING"]
    assert warnings, "真手滑键反而没报警"
    assert "proactive_topics" in warnings[0]
    assert "_ctx" not in warnings[0], f"报警里混进了宿主的信封键: {warnings[0]!r}"


def test_a_save_with_only_an_envelope_still_errors_and_names_it():
    """只有信封、没有任何设置项时仍要报错（不能静默成功），且错误里带上键名。"""
    result, _logs, forwarded = _config_save({"action": "save", "_ctx": {"run_id": "x"}})

    assert not forwarded
    assert "_ctx" in str(result), f"错误没说清收到了什么: {result!r}"


def test_the_dropped_key_warning_is_written_to_the_file_log_too():
    """这条警告必须**双写** —— 使用者就是从界面日志里看到它的，而文件日志里查不到
    （`_emit_log` 只进 ring，ring 在重载时清空）。"""
    import asyncio
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    emitted: list[tuple[str, str]] = []
    logged: list[str] = []

    async def _fake_save_settings(**kw):
        return {"persisted": True}

    stub = SimpleNamespace(
        _CONFIG_SAVE_KEYS={"onebot_url"},
        _emit_log=lambda level, msg: emitted.append((level, msg)),
        logger=SimpleNamespace(
            info=lambda msg, *a, **k: logged.append(str(msg)),
            warning=lambda msg, *a, **k: logged.append(str(msg)),
        ),
        dashboard_service=SimpleNamespace(save_settings=_fake_save_settings),
    )
    asyncio.run(QQAutoReplyPlugin._config_save(
        stub, {"action": "save", "onebot_url": "ws://x", "proactive_topics": ["a"]},
    ))

    assert emitted, "界面日志（ring）里没有这条警告"
    assert any("proactive_topics" in line for line in logged), "文件日志里没有这条警告"


def test_locale_is_connected_end_to_end():
    """界面语言偏好必须真的存得下来 —— 三条链缺一条它就只有 localStorage 生效。

    前端一直在提交（`onLangChange`）并在快照里读回（`s.locale`），但 `locale`
    以前不在真源里，于是：

    * 入口按白名单过滤 → 整键丢弃，`payload` 为空 → 返回 Err（前端 `catch(e){}` 吞掉）
    * `settings_service` 里那段写盘代码的上游签名没有这个参数 → **不可达**
    * 快照只遍历 saveable spec → 不回显

    净效果：换浏览器 / 清 localStorage 就回到默认语言。这条钉住"三条链都在"。
    """
    from plugin.plugins.qq_auto_reply import settings_schema

    assert "locale" in settings_schema.SAVEABLE_KEYS, (
        "locale 不在保存白名单里 —— 入口会把前端提交的它整键丢掉"
    )
    assert "locale" in _dashboard_params(), (
        "locale 不在 dashboard 签名里 —— 前端提交会 TypeError（且写盘代码不可达）"
    )

    html = (_PLUGIN_DIR / "static" / "napcat.html").read_text(encoding="utf-8")
    assert "action:'save', locale:locale" in html, (
        "前端不再提交 locale 了，测试需要跟着改"
    )
    assert "s.locale" in html, (
        "前端不再从快照读回 locale 了，测试需要跟着改"
    )



def test_every_saveable_setting_is_reachable_from_the_frontend():
    """不变量 3：`saveable=True` 的键必须能保存 —— 否则它应该改标 `saveable=False`。"""
    params = _dashboard_params()
    unreachable = sorted(_saveable_keys() - params)
    assert not unreachable, (
        f"这些键标了 saveable=True 但 dashboard 签名里没有，用户永远改不了: "
        f"{unreachable}"
    )
