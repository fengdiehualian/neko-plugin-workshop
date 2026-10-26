"""agent_bridge 鉴权失败识别 + 结果分类的回归测试（pytest 可直接收集）。

    set PYTHONPATH=C:\\Users\\54301\\N.E.K.O && python -m pytest tests -q

历史问题：本文件以前是**脚本**（模块级 ``asyncio.run``），pytest 收集 **0 项** ——
测试看起来存在，实际从没被跑过。现已改写为真正的 test 函数。

背景：这些 Agent CLI 未登录时**退出码是 0**，只往 stderr 打一行
「Authentication required. Please use /login ...」。不特判的话，桥接层把这次调用
判成成功，那行登录提示被当成「任务输出」交给模型。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.config import BridgeConfig
from bridge.registry import AGENT_SPECS, ResolvedCommand
from bridge.runner import _detect_auth_failure, run_agent
from bridge.tools import format_agent_result

REAL_STDERR = (
    "Authentication required. Please use /login command to sign in to your account\n"
)
LIVE = os.environ.get("NEKO_TEST_LIVE_CLI") == "1"


# ---------------------------------------------------------------------------
# 1. 真实未登录输出必须被判为「需要登录」
# ---------------------------------------------------------------------------


def test_real_codebuddy_stderr_is_detected():
    assert _detect_auth_failure("", REAL_STDERR)


def test_detected_even_with_some_stdout_noise():
    """修复点：不再要求 stdout 为空。

    启动期真出问题时 CLI 会先往 stdout 打噪声（codebuddy 的 PowerShell 警告），
    旧实现用「stdout 非空就不判」当闸门，这类失败被报成 ok=True。
    """
    noisy = "WARNING: PowerShell profile failed to load\n\n"
    assert _detect_auth_failure(noisy, REAL_STDERR)


def test_substantive_stdout_is_not_flagged():
    """有真实正文时不算登录失败。"""
    assert _detect_auth_failure("int main(){return 0;}", REAL_STDERR) == ""


def test_overlong_stderr_is_not_flagged():
    assert _detect_auth_failure("", "x" * 900 + REAL_STDERR) == ""


def test_prompt_buried_late_is_not_flagged():
    assert _detect_auth_failure("", "x" * 700 + REAL_STDERR) == ""


def test_normal_output_is_not_flagged():
    assert _detect_auth_failure("", "compilation finished with 0 errors") == ""


# ---------------------------------------------------------------------------
# 2. 假阳性防护：任务正文里出现 login
# ---------------------------------------------------------------------------


def test_answer_mentioning_login_is_not_flagged():
    """让人写登录页 —— 输出里必然有 login / authentication 字样，但这是正常交付物。"""
    answer = (
        "Here is the login page implementation:\n"
        "function login(user, pass) { return authenticate(user, pass); }\n"
        "// authentication required for /admin routes\n" + "\n// more code\n" * 40
    )
    assert _detect_auth_failure(answer, "") == ""


# ---------------------------------------------------------------------------
# 3. 负控制：正常干完活的 Agent 不能被误伤（真跑一个假 CLI）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="用 cmd.exe 起假 CLI，Windows 专属")
async def test_healthy_agent_not_misclassified(tmp_path):
    """假 CLI：退出码 0 + stdout 有正文 → ok=True、needs_login=False。"""
    fake = tmp_path / "fakeagent.cmd"
    fake.write_text("@echo off\r\necho int main(){return 0;}\r\nexit /b 0\r\n", encoding="utf-8")

    config = BridgeConfig(default_timeout_seconds=30, max_timeout_seconds=60)
    resolved = ResolvedCommand(
        "cmd.exe", ("/d", "/s", "/c", str(fake)), source="test", exists=True
    )
    result = await run_agent(
        AGENT_SPECS["codebuddy"], resolved, "x", config=config, cwd=str(tmp_path), timeout=30
    )

    assert result.exit_code == 0
    assert result.ok is True, f"正常结果应 ok=True，实际 error={result.error!r}"
    assert result.needs_login is False
    assert "· OK ·" in format_agent_result(result)


@pytest.mark.asyncio
async def test_unresolvable_agent_refuses_to_dispatch(tmp_path):
    """cmd-fallback 解析不出真身时必须拒绝派发，不能把用户文本送进 shell。

    审计 PoC 里 7 个载荷有 4 个经 cmd.exe 逃逸并真的落地了文件。
    """
    from bridge.runner import CMD_FALLBACK

    fake = tmp_path / "fallback.cmd"
    fake.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    resolved = ResolvedCommand(
        "cmd.exe", ("/d", "/s", "/c", str(fake)), source=CMD_FALLBACK, exists=True
    )
    result = await run_agent(
        AGENT_SPECS["codebuddy"],
        resolved,
        'A" & echo PWNED>INJECTED.txt & echo "B',
        config=BridgeConfig(),
        cwd=str(tmp_path),
        timeout=10,
    )

    assert result.exit_code is None
    assert "AGENT_UNRESOLVED" in (result.error or "")
    assert not (tmp_path / "INJECTED.txt").exists()


# ---------------------------------------------------------------------------
# 4. 端到端：真跑一次没登录的 codebuddy（默认跳过，设 NEKO_TEST_LIVE_CLI=1 开启）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LIVE, reason="需要 NEKO_TEST_LIVE_CLI=1（真跑本机 CLI）")
@pytest.mark.asyncio
async def test_live_codebuddy_needs_login():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = BridgeConfig(default_timeout_seconds=90, max_timeout_seconds=120)
    result = await run_agent(
        AGENT_SPECS["codebuddy"],
        ResolvedCommand("codebuddy", source="test", exists=True),
        "reply with OK only",
        config=config,
        cwd=repo,
        timeout=90,
    )
    assert result.ok is False
    assert result.needs_login is True
    assert result.error.startswith("AGENT_NEEDS_LOGIN:")
    text = format_agent_result(result)
    assert "NEEDS_LOGIN" in text
    assert "换一个 ready 的 Agent" in text