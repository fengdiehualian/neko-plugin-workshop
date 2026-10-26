"""code_sandbox 安全与功能测试。

注意：与 card_master/media_generator 测试一致，插件模块在函数内导入，
避免 isort first-party 判定在本地/CI 间分歧。
"""

from __future__ import annotations

import os

import pytest

# ══════════ audit_code ══════════


def test_audit_allows_pure_calculation() -> None:
    from plugin.plugins.code_sandbox.sandbox import audit_code

    code = (
        "import math, random, json\n"
        "def f(x):\n"
        "    return x * 2\n"
        "print(f(21), math.pi, random.randint(1, 2))\n"
    )
    audit_code(code)  # 不应抛异常


def test_audit_blocks_os_import() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="os"):
        audit_code("import os\nprint(os.getcwd())\n")


def test_audit_blocks_subprocess_import() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="subprocess"):
        audit_code("from subprocess import run\n")


def test_audit_blocks_socket_import() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="socket"):
        audit_code("import socket\n")


def test_audit_blocks_pathlib() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="pathlib"):
        audit_code("from pathlib import Path\n")


def test_audit_blocks_eval_exec() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="eval"):
        audit_code("eval('1+1')\n")
    with pytest.raises(SandboxViolation, match="exec"):
        audit_code("exec('x=1')\n")


def test_audit_blocks_open_call() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="open"):
        audit_code("f = open('x.txt', 'w')\n")


def test_audit_blocks_builtins_attribute_open() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="open"):
        audit_code("__builtins__.open('evil.txt', 'w')\n")


def test_audit_blocks_dangerous_attribute() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="system"):
        audit_code("import os\nos.system('dir')\n")


def test_audit_blocks_getattr() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="getattr"):
        audit_code("getattr(__builtins__, 'open')\n")


def test_audit_rejects_syntax_error() -> None:
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="语法错误"):
        audit_code("def broken(:\n")


# ══════════ run_code ══════════


def test_run_code_success() -> None:
    from plugin.plugins.code_sandbox.sandbox import run_code

    result = run_code("import math\nprint(sum(n for n in range(1, 101)))\n")
    assert result["is_error"] is False
    assert "5050" in result["output"]


def test_run_code_empty() -> None:
    from plugin.plugins.code_sandbox.sandbox import run_code

    result = run_code("   ")
    assert result["is_error"] is True
    assert "NO_CODE" in result["error"]


def test_run_code_violation_is_rejected() -> None:
    from plugin.plugins.code_sandbox.sandbox import run_code

    result = run_code("import os\nprint(os.getcwd())\n")
    assert result["is_error"] is True
    assert result["error"] == "SANDBOX_VIOLATION"
    assert "安全拦截" in result["output"]


def test_run_code_runtime_error() -> None:
    from plugin.plugins.code_sandbox.sandbox import run_code

    result = run_code("1 / 0\n")
    assert result["is_error"] is True
    assert result["error"] == "RUNTIME_ERROR"


def test_run_code_timeout_kills() -> None:
    from plugin.plugins.code_sandbox.sandbox import run_code

    result = run_code("while True:\n    pass\n", timeout=1)
    assert result["is_error"] is True
    assert result["error"] == "TIMEOUT"


# ══════════ 环境与截断 ══════════


def test_sanitized_env_strips_api_keys() -> None:
    from plugin.plugins.code_sandbox.sandbox import _sanitized_env

    os.environ["DASHSCOPE_API_KEY"] = "test-secret-123"
    os.environ["KLING_SECRET_KEY"] = "kling-secret"
    try:
        env = _sanitized_env()
    finally:
        os.environ.pop("DASHSCOPE_API_KEY", None)
        os.environ.pop("KLING_SECRET_KEY", None)
    assert "DASHSCOPE_API_KEY" not in env
    assert "KLING_SECRET_KEY" not in env
    # 普通变量保留
    os.environ["TMP"] = os.environ.get("TMP", "C:\\Temp")
    env2 = _sanitized_env()
    assert "TMP" in env2


def test_truncation_limit_exists() -> None:
    from plugin.plugins.code_sandbox.sandbox import MAX_OUTPUT_CHARS

    assert MAX_OUTPUT_CHARS == 50000


# ══════════ v0.1.1：危险根限定（去误伤）+ 逃逸防护 + 入口注册 ══════════


def test_audit_allows_pure_data_methods() -> None:
    """纯计算数据结构的同名属性不得误伤：list.remove / dict.update / str.join 放行（v0.1.1）。"""
    from plugin.plugins.code_sandbox.sandbox import audit_code

    audit_code(
        "a = [1, 2, 3]\n"
        "a.remove(1)\n"
        "d = {'x': 1}\n"
        "d.update({'y': 2})\n"
        "print(a, d, '|'.join(['a', 'b']))\n"
    )


def test_audit_blocks_os_remove() -> None:
    """危险根对象的同名属性仍拦截：os.remove（v0.1.1 危险根限定后不绕过）。"""
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="remove"):
        audit_code("import os\nos.remove('x.txt')\n")


def test_audit_blocks_tempfile_import() -> None:
    """tempfile 加入禁导入清单，防 mkdtemp/mkstemp 写文件系统（v0.1.1 审计发现）。"""
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="tempfile"):
        audit_code("import tempfile\n")


def test_audit_blocks_escape_chain() -> None:
    """Python 沙箱经典逃逸链属性一律拦截（v0.1.1 加固）。"""
    from plugin.plugins.code_sandbox.sandbox import SandboxViolation, audit_code

    with pytest.raises(SandboxViolation, match="__class__"):
        audit_code("().__class__.__mro__\n")
    with pytest.raises(SandboxViolation, match="__subclasses__"):
        audit_code("().__class__.__base__.__subclasses__()\n")


def test_entry_registered() -> None:
    """plugin_entry run 入口可被宿主收集（入口点 > 0，v0.1.1 修复）。"""
    from plugin.plugins.code_sandbox import CodeSandboxPlugin

    metas = []
    for name in dir(CodeSandboxPlugin):
        if name.startswith("_"):
            continue
        meta = getattr(getattr(CodeSandboxPlugin, name, None), "__neko_event_meta__", None)
        if meta is not None and getattr(meta, "event_type", None) == "plugin_entry":
            metas.append(meta)
    assert any(m.id == "run" for m in metas), f"缺少入口 run，现有：{[m.id for m in metas]}"


def test_entry_run_executes() -> None:
    """入口 run：真实执行纯计算代码（无 Key 依赖，走子进程）。"""
    import asyncio

    from plugin.plugins.code_sandbox import CodeSandboxPlugin

    inst = CodeSandboxPlugin.__new__(CodeSandboxPlugin)
    result = asyncio.run(inst.entry_run(code="print(6 * 7)\n", parameters={}))
    assert result["is_error"] is False
    assert "42" in result["output"]


def test_entry_run_empty_code() -> None:
    import asyncio

    from plugin.plugins.code_sandbox import CodeSandboxPlugin

    inst = CodeSandboxPlugin.__new__(CodeSandboxPlugin)
    result = asyncio.run(inst.entry_run(code="", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_CODE"
