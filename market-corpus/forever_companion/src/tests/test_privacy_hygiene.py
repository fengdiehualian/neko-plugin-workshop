"""隐私卫生门（1.3.0 第十轮，问题清单 §2.2 清账）：明文 key 与对话内容不得进日志。

宿主规范（docs/zh-CN/plugins/best-practices.md）：「不要把原始对话、用户输入的密钥
或其他隐私敏感 payload 写进日志或进程输出。正常诊断优先记录脱敏后的长度、ID 和
错误类型」；发布检查清单同款（日志和进程输出均不包含原始对话、密钥或私有 payload）。

本文件三道门：

**门 1/2（行为）**：`_post_chat_completion` 两条 warning 路径——
  失败留痕旧版直记裸 `exc`：urllib 家族的异常 message 可能携带完整 URL
  （malformed-URL ValueError、部分传输库的 reason 串），自定义端点若以
  `?api-key=…` 查询参数或 `user:pass@` userinfo 形态带凭据，会原样落进插件
  日志文件；宿主 logging_config 的 REDACT_PATTERNS 不覆盖连字符 `api-key`
  与裸 `sk-` 形态，兜不住。现在只准记脱敏形态（异常类型名 + 若有 code 则
  状态码）。
  无可用内容留痕旧版记 `payload head=<前80字符>`：网关错误页/回显型上游会
  把对话内容映进响应，等于原始对话进日志。现在只准记结构摘要（类型/顶层
  键名/条数/字节长度，零值输出）。

**门 3（静态，AST + 字面量）**：出货代码里 logger 调用不得把凭据/对话变量
  （api_key / prompt / quote / user_text / her_text / raw / core_cfg / resolved /
  text / body）或其下标/属性子树当实参递出去（白名单：`payload.get("<字面量>")`
  形态的宿主信封字段访问——那是 error 码位而非正文）；并钉死三族已淘汰的
  内容打印写法（`payload head`、`str(payload)[:`、`str(raw)[:`）不得复活。

反向对照：三条门各自备有"塞回违规写法必红"的对照测试（门 3 用改过的源码
字符串过扫描器，不碰真文件）。豁免面与 test_i18n_contract 同一口径：
debug_entries 的 Ok 回显 = 开发者面（默认关、本地面板），不是日志通路。
"""

from __future__ import annotations

import ast
import io
import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class _CaptureLogger:
    """收集 logger.* 的全部格式化输出（loguru 风格 "{}" 占位 + 真文件可能写的 repr）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _emit(self, fmt, *args, **_kwargs):
        try:
            rendered = str(fmt).format(*args)
        except (IndexError, KeyError, ValueError):
            rendered = str(fmt) + " " + repr(args)
        self.lines.append(rendered)

    debug = info = warning = error = exception = _emit


# ---------------------------------------------------------------------------
# 门 1/2：_post_chat_completion 的行为契约
# ---------------------------------------------------------------------------

_BASE_URL = "https://gw.example.net/v1"
_KEY = "sk-SUPERSUPERSECRET-9d3f"
_PROMPT_MARK = "CONVOMARK-conversation-text-must-not-appear"


def _boom(exc: BaseException):
    def _raise(*_args, **_kwargs):
        raise exc

    return _raise


def test_failure_log_never_leaks_key_url_or_prompt(tm, monkeypatch) -> None:
    """异常 message 携带端点/凭据/对话回显 → 日志一条都不准出现，但错误类型可诊断。"""
    tone_slot = tm.services.tone_slot
    boom = ValueError(
        f"unknown url type: '{_BASE_URL}/chat/completions?api-key={_KEY}' "
        f"while analyzing: {_PROMPT_MARK}"
    )
    monkeypatch.setattr("urllib.request.urlopen", _boom(boom))
    logger = _CaptureLogger()
    out = tone_slot._post_chat_completion(
        _BASE_URL, _KEY, "some-model", f"prompt contains {_PROMPT_MARK}", logger=logger
    )
    assert out is None
    assert logger.lines, "失败留痕丢了：面板'详见插件日志'会查无可查"
    joined = "\n".join(logger.lines)
    for secret in (_KEY, _BASE_URL, _PROMPT_MARK):
        assert secret not in joined, f"日志泄漏 {secret!r} 同族内容:\n{joined}"
    # 诊断性：类型名必须在（不能为了隐私把线索全擦掉）
    assert "ValueError" in joined


def test_http_error_keeps_status_code_but_not_message(tm, monkeypatch) -> None:
    """带 .code 的 HTTPError 形态：状态码保留（区分 401/429/5xx），message 主体不进日志。"""
    tone_slot = tm.services.tone_slot

    class _HttpErrorLike(Exception):
        code = 429

    monkeypatch.setattr("urllib.request.urlopen", _boom(_HttpErrorLike("too many requests")))
    logger = _CaptureLogger()
    assert tone_slot._post_chat_completion(
        _BASE_URL, _KEY, "m", "p", logger=logger
    ) is None
    joined = "\n".join(logger.lines)
    assert "code=429" in joined and "_HttpErrorLike" in joined


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_unusable_content_log_is_structural_only(tm, monkeypatch) -> None:
    """HTTP 200 但响应非 OpenAI 形态（且值里回显对话）→ 只准记结构，零值输出。"""
    tone_slot = tm.services.tone_slot
    body = json.dumps(
        {
            "error": {"message": f"gateway echoed: {_PROMPT_MARK}"},
            "choices": f"echo {_PROMPT_MARK}",
        }
    ).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(body))
    logger = _CaptureLogger()
    out = tone_slot._post_chat_completion(_BASE_URL, _KEY, "m", "p", logger=logger)
    assert out is None
    assert logger.lines, "无可用内容留痕丢了"
    joined = "\n".join(logger.lines)
    assert _PROMPT_MARK not in joined, f"响应值回显进日志了:\n{joined}"
    # 结构信息：顶层键名与字节长度在（能定位"是错误信封还是坏形态"）
    assert "keys=" in joined and "bytes=" in joined and "error" in joined


def test_valid_response_does_not_warn(tm, monkeypatch) -> None:
    """正路不回归：可用回复照常返回 content、零 warning。"""
    tone_slot = tm.services.tone_slot
    body = json.dumps(
        {"choices": [{"message": {"content": '{"emotion":"warm","confidence":0.8}'}}]}
    ).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(body))
    logger = _CaptureLogger()
    got = tone_slot._post_chat_completion(_BASE_URL, _KEY, "m", "p", logger=logger)
    assert got and "warm" in got
    assert logger.lines == []


# ---------------------------------------------------------------------------
# 门 3：静态门（AST 实参黑名单 + 已淘汰写法字面量钉死）
# ---------------------------------------------------------------------------

_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})
# 凭据/对话/配置整包形态的名字：出现在 logger 实参的子树里即违规
_PRIVACY_NAMES = frozenset(
    {
        "api_key",
        "core_cfg",
        "her_text",
        "prompt",
        "quote",
        "raw",
        "resolved",
        "text",
        "user_text",
    }
)
# 已淘汰的内容打印写法（复活即红，整文件文本扫描）。注意：本仓源码（含注释）
# 不得出现这三串字面量，历史说明里提到旧写法时改用不含此串的措辞。
_BANNED_LITERALS = ("payload head", "str(payload)[:", "str(raw)[:")


def _shipped_sources() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
        parts = set(path.parts)
        if {".venv", "tests", "vendor"} & parts:
            continue
        out.append((path.read_text(encoding="utf-8"), path))
    return out


def _logger_chain_name(node: ast.AST) -> bool:
    """logger.x() / self.logger.x() / self._logger().x() 形态：接收者表达式里
    出现任何含 logger 的名字段（Name.id / Attribute.attr）即算。"""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in _LOG_METHODS:
        return False
    for sub in ast.walk(node.func.value):
        if isinstance(sub, ast.Name) and "logger" in sub.id.lower():
            return True
        if isinstance(sub, ast.Attribute) and "logger" in sub.attr.lower():
            return True
    return False


def _arg_root(arg: ast.AST) -> str | None:
    """数据实参的根名：api_key / resolved["api_key"] / user_text[:200] → 根 Name；
    len(x) / _shape(x) 这类函数调用 → None（脱敏发生在被调用侧，不审内部）。"""
    if isinstance(arg, ast.Call):
        return None
    cur = arg
    while isinstance(cur, (ast.Subscript, ast.Attribute)):
        cur = cur.value
    if isinstance(cur, ast.Starred):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else None


def _violations(source: str) -> list[str]:
    """对一份源码文本跑门 3 的两条规则，返回违规描述（空 = 干净）。

    规则 A（AST，只审**数据实参**，去掉首参格式串）：实参本身是凭据/对话
    变量的名字/下标/切片/属性链即红；经 helper 包一层的（len()、
    _exc_shape() 等）根是 Call，天然豁免。格式串里出现 text/prompt 只是
    文案，不是数据外泄。
    规则 B（整文件文本）：已淘汰的内容打印写法字面量复活即红（含注释——
    历史说明用不含该串的措辞，防复制粘贴复活）。
    """
    found: list[str] = [
        f"源码复活已禁写法 {banned!r}" for banned in _BANNED_LITERALS if banned in source
    ]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not _logger_chain_name(node):
            continue
        data_args = list(node.args[1:]) + [kw.value for kw in node.keywords]
        for arg in data_args:
            root = _arg_root(arg)
            if root is not None and root in _PRIVACY_NAMES:
                found.append(f"logger 实参直出 {root}")
    return found


def test_shipped_logger_calls_are_privacy_clean() -> None:
    dirty: list[str] = []
    for source, path in _shipped_sources():
        dirty.extend(f"{path.name}: {v}" for v in _violations(source))
    assert not dirty, "隐私静态门：\n" + "\n".join(dirty)


def test_static_gate_reverse_control() -> None:
    """反向对照：违规写法塞回去，扫描器必须每类都抓到（门不是空转）。"""
    assert _violations('self.logger.warning("x {}", api_key)')
    assert _violations('self.logger.info("sub {}", resolved["api_key"])')
    assert _violations('logger.warning("preview {}", prompt)')
    assert _violations('logger.error("echo {}", user_text[:200])')  # 截断也是直出
    assert _violations('logger.warning("p {}", shape)  # str(payload)[:80] 写法复活')  # 规则 B
    # 合法形态不误报：helper 脱敏、信封字段访问、本地长度变量
    assert _violations('logger.warning("failed: {}", _exc_shape(exc))') == []
    assert _violations('logger.warning("dormant: {}", payload.get("error"))') == []
    assert _violations('logger.info("captured: kind={} quote_len={}", kind, len(quote))') == []
    assert _violations(
        'logger.warning("slot {} unresolved ({})", slot, _slot_dormancy_hint(core_cfg, slot))'
    ) == []


def test_quote_and_detail_logs_use_lengths_not_content() -> None:
    """两处已修点的确切形态钉桩：碎片日志记 quote_len、成文失败日志记 shape=。"""
    senses = (PLUGIN_ROOT / "mixins" / "senses.py").read_text(encoding="utf-8")
    assert re.search(r"fragment captured: lanlan=\{\} kind=\{\} quote_len=\{\}", senses), (
        "碎片日志的脱敏形态被改动：确认新写法仍零内容输出后同步本钉桩"
    )
    assert "head={str(raw)" not in senses
    assert 'quote={!r}' not in senses
