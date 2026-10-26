#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neko —— 让**任意 Agent CLI** 调用 N.E.K.O. 的命令行客户端。

设计目标：把 N.E.K.O. 变成一个"可被别的 Agent 软件调用"的能力面，
所以整个文件**只用标准库**（urllib / json / socket），任何装了 Python 3.9+ 的
Agent 环境（Claude Code / Codex / dsh / omp / EvoX / OpenCode / OpenClaw /
AtomCode / QwenPaw …）都能直接跑，不需要装依赖、不需要联网安装。

子命令
------
    neko.py doctor                # 端口发现与连通性自检
    neko.py plugins [--json]      # N.E.K.O. 里装了哪些插件、在不在跑
    neko.py entries [--plugin ID] # 某个插件有哪些入口（含 input_schema）
    neko.py run <entry> [...]     # 调一个入口（POST /runs → 轮询 → /export）
    neko.py messages [--limit N]  # 最近进入对话的推送（宿主 messages 存储）
    neko.py say <text>            # 往 N.E.K.O. 对话里推一条文本（自动挑推送入口）
    neko.py mcp                   # 以 MCP stdio server 方式跑（给支持 MCP 的 Agent 用）

端口发现顺序
------------
1. ``--plugin-port`` / ``--main-port``；
2. 环境变量 ``NEKO_PLUGIN_PORT`` / ``NEKO_MAIN_PORT``；
3. 常用默认值 48916（插件服务器）/ 48911（主服务）；
4. 在 127.0.0.1:48900-48999 里按特征探测（插件服务器会返回 ``{"plugins": [...]}``）。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

__version__ = "0.1.0"

PLUGIN_PORT_DEFAULT = 48916
MAIN_PORT_DEFAULT = 48911
SCAN_RANGE = range(48900, 49000)
HTTP_TIMEOUT = 15.0
TERMINAL_STATUSES = ("succeeded", "failed", "error", "cancelled")

#: 「推给 N.E.K.O. 对话」的**白名单插件**。默认为空 = 不许自动挑，必须显式 ``--entry``。
#: 为什么默认空：真机上一个按关键词瞎挑的例子会命中 ``bilibili_danmaku:ask_neko_bili_send_message``，
#: 那是**B 站私信/弹幕发送器** —— 一次「帮我推条消息」就能把内容发到站外去。宁可让调用方多写一个
#: ``--entry``，也不能让自动挑选碰到外部平台。
SAFE_PUSH_PLUGINS: tuple[str, ...] = ()

#: 命中这些字样的插件/入口：自动挑选**永不**选中；显式调用则必须带 ``--allow-external``。
EXTERNAL_HINTS = (
    "bilibili", "bili", "danmaku", "wechat", "weixin", "wecom", "qq", "discord", "telegram",
    "twitter", "mastodon", "mail", "email", "smtp", "sms", "publish", "post", "comment",
    "dynamic", "tweet", "share", "upload",
)

#: 候选入口里，这些参数名可以承载正文。
TEXT_ARG_NAMES = ("text", "content", "message", "msg", "prompt", "body", "title")


class NekoError(RuntimeError):
    """面向调用方的一句话错误（Agent 直接读它决定下一步）。"""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _request(base: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    url = f"{base}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise NekoError(f"{method} {path} → HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise NekoError(f"{method} {path} 连不上（{base}）：{exc}") from exc
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", "replace")


def _port_open(port: int, timeout: float = 0.15) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _openapi_paths(port: int, timeout: float = 3.0) -> set[str]:
    """拿一个端口的 openapi 路由表（快：本地几十毫秒，几百 KB 以内）。探测失败返回空集。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — 探测失败就是"不是"
        return set()
    paths = payload.get("paths")
    return set(paths) if isinstance(paths, dict) else set()


def _looks_like_plugin_server(port: int) -> bool:
    """插件服务器的特征：路由表里有 ``/runs`` 与 ``/plugin/{plugin_id}/reload``。

    注意**不要**拿 ``/plugins`` 当探针：那个接口要返回近 1MB 的插件清单、真机要 5s 左右，
    用它会把自己探测成"没在跑"（踩过）。
    """
    paths = _openapi_paths(port)
    return "/runs" in paths and any("plugin" in path for path in paths) and "/api/proactive/mode" not in paths


def _looks_like_main_server(port: int) -> bool:
    """主服务的特征：路由表里有 ``/api/proactive/mode`` 且**没有** ``/runs``。

    只认路由，不认标题：真机上主服务的 openapi 标题就是 "FastAPI"（踩过：
    按标题判定会一直判否，然后掉进 100 个端口的扫描，白等 20 多秒）。
    """
    paths = _openapi_paths(port)
    return "/api/proactive/mode" in paths and "/runs" not in paths


def discover(args: argparse.Namespace) -> tuple[str, str, list[str]]:
    """返回 ``(plugin_base, main_base, notes)``；发现过程写进 notes 供 doctor 展示。"""
    notes: list[str] = []
    plugin_port = getattr(args, "plugin_port", None) or _env_int("NEKO_PLUGIN_PORT") or PLUGIN_PORT_DEFAULT
    main_port = getattr(args, "main_port", None) or _env_int("NEKO_MAIN_PORT") or MAIN_PORT_DEFAULT

    if not _looks_like_plugin_server(plugin_port):
        notes.append(f"插件服务器 {plugin_port} 没响应，开始扫描 48900-48999")
        found = next(
            (port for port in SCAN_RANGE if _port_open(port) and _looks_like_plugin_server(port)), None
        )
        if found is None:
            raise NekoError("找不到 N.E.K.O. 插件服务器（48900-48999 都没特征响应）。N.E.K.O. 在跑吗？")
        plugin_port = found
        notes.append(f"插件服务器发现于 {plugin_port}")
    if not _looks_like_main_server(main_port):
        found = next((port for port in SCAN_RANGE if _port_open(port) and _looks_like_main_server(port)), None)
        if found is not None:
            main_port = found
            notes.append(f"主服务发现于 {main_port}")
        else:
            notes.append(f"主服务（{main_port}）没找到；只影响 /api/* 相关能力，插件能力照常")
    return f"http://127.0.0.1:{plugin_port}", f"http://127.0.0.1:{main_port}", notes


def _env_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if raw.isdigit():
        return int(raw)
    return None


# --------------------------------------------------------------------------- #
# 本机 Agent 派发主干（从 agent_bridge/bridge/registry.py + runner.py 移植的 stdlib 版）
#
# 这一半**不依赖 N.E.K.O.**：只要有本机装了编码 Agent CLI，就能在任意 Agent 环境里
# 「发现 + 派活」，所以它是 skill 的主干；N.E.K.O. 那些动词是附加族。
# 登记表与 agent_bridge 的 AGENT_SPECS 同源（改动请两边同步）。
# --------------------------------------------------------------------------- #

#: ``id -> (label, binary, mode, fixed_args, prompt_flag, model_flag, json_args, notes)``
AGENT_SPECS: dict[str, tuple[str, str, str, tuple[str, ...], str | None, str | None, tuple[str, ...], str]] = {
    "codebuddy": ("CodeBuddy Code（腾讯）", "codebuddy", "flag", (), "-p", "--model", ("--output-format", "json"),
                  "凭据挂在 WorkBuddy 桌面端；未登录时退出码 1 且无输出。"),
    "claude": ("Claude Code（Anthropic）", "claude", "flag", (), "-p", "--model", ("--output-format", "json"),
               "部分沙箱环境下启动时调 reg.exe 会命中程序黑名单。"),
    "dsh": ("DeepSeek Harness", "dsh", "positional", ("--profile", "headless"), None, None, (),
            "任务描述作为位置参数放在 --profile 之后。"),
    "omp": ("Oh My Pi", "omp", "flag", (), "-p", "--model", ("--mode", "json"),
            "同 monorepo 另有 mnemopi（记忆）与 omp-stats（用量）。"),
    "evox": ("EvoX", "evox", "flag", ("--evox-cli",), "-p", "--model", (),
             "evox.exe 需要 --evox-cli 才进入 CLI 模式；PATH 上是 evox.cmd 包装器。"),
    "opencode": ("OpenCode", "opencode", "positional", ("run",), None, "--model", ("--format", "json"),
                 "非交互式走独立的 run 子命令。"),
    "openclaw": ("OpenClaw", "openclaw", "positional", ("agent", "exec"), None, "--model", ("--json",),
                 "agent exec 为一次性隔离执行，不需要常驻 Gateway。"),
    "atomcode": ("AtomCode", "atomcode", "flag", (), "-p", None, ("--output-format", "jsonl"),
                 "登录走 AtomGit OAuth；支持 --prompt-file 与 --ephemeral。"),
    "qwenpaw": ("QwenPaw", "qwenpaw", "flag", ("task",), "-i", "-m", (),
                "内置跨 Agent 协作工具；PATH 上是 .cmd 包装器。"),
}

AGENT_ALIASES: dict[str, str] = {
    "cbc": "codebuddy", "codebuddy-code": "codebuddy", "oh-my-pi": "omp", "pi": "omp",
    "oc": "opencode", "claw": "openclaw", "atom": "atomcode", "qwen": "qwenpaw",
}

#: 子进程里必须清掉的环境变量：N.E.K.O. 用嵌入式 Python，PYTHONHOME/PYTHONPATH 会指向
#: 宿主自己的 bin，导致 Python 实现的 Agent（如 qwenpaw）直接起不来。
ENV_STRIP = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP")


def canonical_agent(name: str) -> str:
    key = (name or "").strip().lower()
    if key in AGENT_SPECS:
        return key
    if key in AGENT_ALIASES:
        return AGENT_ALIASES[key]
    raise NekoError(f"不认识的 Agent：{name!r}（可选：{', '.join(sorted(AGENT_SPECS))}）")


def _expand_windows_vars(value: str, variables: dict[str, str]) -> str:
    """展开 ``%NAME%``（先在 shim 自己的 ``set`` 变量里找，再落到进程环境）。"""
    import re as _re

    def replace(match: "object") -> str:
        name = match.group(1)  # type: ignore[attr-defined]
        return variables.get(name.upper()) or os.environ.get(name, match.group(0))  # type: ignore[attr-defined]

    return _re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", replace, value)


def parse_cmd_shim(path: str) -> tuple[str, list[str]] | None:
    """从 ``.cmd``/``.bat`` 包装器里还原真实 exe 与其前缀旗标；解析不出返回 None。

    为什么值得做：真机上 ``qwenpaw`` 的 shim 带 **UTF-8 BOM**，走 ``cmd /c`` 会把第一行
    读成 `'﻿@echo' 不是内部或外部命令`（我复现过）；而且真身 ``.exe`` 直接调用更稳
    （少一层 cmd 的引号/转义规则）。evox 的 shim 还会内联 ``--evox-cli``。
    """
    import re as _re

    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # shim 自己的 set 赋值（例如 qwenpaw 的 REAL_BIN / QWENPAW_HOME）
    variables: dict[str, str] = {}
    for match in _re.finditer(r'set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?', raw):
        variables[match.group(1).upper()] = match.group(2)
    for _ in range(2):  # 嵌套展开（%QWENPAW_HOME% 里还可能含 %USERPROFILE%）
        for key in list(variables):
            variables[key] = _expand_windows_vars(variables[key], variables)
    variables = {key: _expand_windows_vars(value, variables) for key, value in variables.items()}

    forwarding = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith(("rem", "::", "@echo")):
            continue
        if "%*" in line:
            forwarding = line
    if not forwarding:
        return None
    head = forwarding.partition("%*")[0]

    exe, flags = "", []
    # 先看引号里的第一个 token：它可能是字面 .exe，也可能是 %REAL_BIN% 这样的变量
    # （qwenpaw 的转发行就是 `"%REAL_BIN%" %*`）——所以**先展开再判断**，别要求字面 .exe。
    quoted = _re.search(r'"([^"]+)"', head)
    if quoted:
        candidate = _expand_windows_vars(quoted.group(1), variables).strip().strip('"')
        if candidate.lower().endswith((".exe", ".bat", ".cmd")):
            exe = candidate
            flags = _re.findall(r'(?<!["\w])--?[\w-]+', head[quoted.end():])
    if not exe:
        bare = _re.search(r'([^\s"]+\.exe)', head, _re.IGNORECASE)
        if bare:
            exe, flags = bare.group(1), _re.findall(r'(?<!["\w])--?[\w-]+', head[bare.end():])

    # `REAL_BIN=%QWENPAW_HOME%\venv\Scripts\qwenpaw.exe` 这种：取等号右边
    if "=" in exe:
        exe = exe.split("=", 1)[-1]
    exe = _expand_windows_vars(exe, variables).strip('"')
    if not exe or not Path(exe).exists():
        return None
    return exe, flags


def resolve_agent(spec_id: str) -> tuple[list[str], str, str]:
    """把 Agent 解析成 ``(argv_prefix, source, detail)``。

    顺序：PATH → ``.cmd/.bat`` 则**先解析 shim 找真身 exe**（带旗标）→ 解不出再退回
    ``cmd.exe /d /s /c``。
    """
    import shutil

    _label, binary, *_rest = AGENT_SPECS[spec_id]
    found = shutil.which(binary)
    if not found:
        return [], "missing", "未在 PATH 中找到"
    if found.lower().endswith((".cmd", ".bat")):
        parsed = parse_cmd_shim(found)
        if parsed is not None:
            exe, flags = parsed
            return [exe, *flags], "cmd-shim", f"{Path(found).name} -> {Path(exe).name}"
        comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
        return [comspec, "/d", "/s", "/c", found], "cmd-shim", f"{Path(found).name}（未解析出 exe，走 cmd 兜底）"
    return [found], "path", ""


def build_agent_argv(
    spec_id: str, argv_prefix: list[str], task: str, *, model: str = "", json_output: bool = False,
    extra: list[str] | None = None,
) -> list[str]:
    _label, _binary, mode, fixed, prompt_flag, model_flag, json_args, _notes = AGENT_SPECS[spec_id]
    # shim 里可能已经内联了同样的旗标（例如 evox.cmd 的 --evox-cli 与 spec.fixed_args 重复），去重
    deduped_fixed = [item for item in fixed if item not in argv_prefix]
    argv = [*argv_prefix, *deduped_fixed]
    if mode == "flag":
        if not prompt_flag:
            raise NekoError(f"agent {spec_id} 的 mode=flag 但没定义 prompt_flag")
        argv += [prompt_flag, task]
    elif mode == "positional":
        argv.append(task)
    else:
        raise NekoError(f"agent {spec_id} 的 mode 未知：{mode!r}")
    if json_output and json_args:
        argv += list(json_args)
    if model and model_flag:
        argv += [model_flag, model]
    argv += [str(item) for item in (extra or [])]
    return argv


def kill_process_tree(process: Any) -> None:
    """结束进程**树**（不是只杀直接子进程）。

    为什么必须这样：``.cmd/.bat`` 形态的 Agent 被包成 ``cmd.exe /d /s /c <shim>``，
    真正的 CLI 是 cmd.exe 的**孙**进程 —— 超时只 kill 直接子进程的话，那个 CLI 会继续跑
    （占额度、改文件），而调用方以为已经停了。（上游 ``agent_bridge/bridge/runner.py``
    也有同样的 ``_kill_tree_sync``。）
    """
    import signal
    import subprocess

    pid = getattr(process, "pid", None)
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        return
    # POSIX：子进程用 start_new_session 起，所以能整组杀（连孙进程）
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass


def run_subprocess(
    argv: list[str], *, cwd: str | None, timeout: float, max_bytes: int = 400_000
) -> dict[str, Any]:
    """跑一个子进程并返回结构化结果（超时按**进程树**清理，截断如实标注）。"""
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ENV_STRIP}
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd or None,
        "env": env,
    }
    if os.name != "nt":
        # 单独进程组，超时才能整组杀（Windows 用 taskkill /T，不需要这个）
        popen_kwargs["start_new_session"] = True

    started = time.time()
    try:
        process = subprocess.Popen(argv, **popen_kwargs)
    except OSError as exc:
        return {
            "ok": False, "timed_out": False, "exit_code": None,
            "duration_ms": int((time.time() - started) * 1000),
            "stdout": "", "stderr": "", "error": f"{type(exc).__name__}: {exc}", "argv": argv,
        }

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(process)
        try:
            # 树杀掉之后管道写端才会关，communicate 才可能返回；给 10s 宽限。
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            stdout, stderr = "", ""

    stdout, stderr = stdout or "", stderr or ""
    truncated = len(stdout) > max_bytes
    return {
        "ok": (not timed_out) and process.returncode == 0,
        "timed_out": timed_out,
        "exit_code": process.returncode,
        "duration_ms": int((time.time() - started) * 1000),
        "stdout": stdout[:max_bytes],
        "stderr": stderr[-4000:],
        "truncated": truncated,
        "error": "超时（已按进程树清理）" if timed_out else ("" if process.returncode == 0 else f"退出码 {process.returncode}"),
        "argv": argv,
    }


def _version_cache_path() -> Path:
    base = os.environ.get("NEKO_BRIDGE_STATE") or os.environ.get("TEMP") or "/tmp"
    return Path(base) / "neko-bridge-agents.json"


def _load_version_cache() -> dict[str, Any]:
    path = _version_cache_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _store_version_cache(cache: dict[str, Any]) -> None:
    try:
        _version_cache_path().write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def agent_report(spec_id: str, *, want_version: bool, fresh: bool = False) -> dict[str, Any]:
    label, binary, mode, fixed, prompt_flag, model_flag, json_args, notes = AGENT_SPECS[spec_id]
    argv_prefix, source, detail = resolve_agent(spec_id)
    report: dict[str, Any] = {
        "agent": spec_id,
        "label": label,
        "binary": binary,
        "installed": bool(argv_prefix),
        "source": source,
        "detail": detail,
        "mode": mode,
        "fixed_args": list(fixed),
        "prompt_flag": prompt_flag,
        "model_flag": model_flag,
        "json_args": list(json_args),
        "notes": notes,
        "version": "",
    }
    if not argv_prefix or not want_version:
        return report
    cache = _load_version_cache()
    cached = cache.get(spec_id) or {}
    if not fresh and cached.get("version") and (time.time() - float(cached.get("at") or 0)) < 86_400:
        report["version"] = cached["version"]
        report["version_cached"] = True
        return report
    import re as _re

    def _pick(text: str) -> str:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        # 优先挑"像版本号"的那一行：CLI 常把 INFO 日志打在前面（qwenpaw 就是）
        noise = ("info", "usage", "try ", "error", "no such option", "unknown option", "traceback")
        for line in lines:
            if _re.search(r"\d+\.\d+", line) and not line.lower().startswith(noise):
                return line[:80]
        for line in lines:
            if not line.lower().startswith(noise):
                return line[:80]
        return ""

    result = run_subprocess([*argv_prefix, *fixed, "--version"], cwd=None, timeout=20)
    report["version"] = _pick(result["stdout"] or result["stderr"])
    if not report["version"]:
        # 有些 CLI 的子命令不接受 --version（qwenpaw `task --version` 只打 usage）
        retry = run_subprocess([*argv_prefix, "--version"], cwd=None, timeout=20)
        report["version"] = _pick(retry["stdout"] or retry["stderr"])
    if report["version"]:
        cache[spec_id] = {"version": report["version"], "at": time.time()}
        _store_version_cache(cache)
    return report


def dispatch_agent(
    agent: str, task: str, *, model: str = "", cwd: str = "", timeout: float = 600.0,
    json_output: bool = False, extra: list[str] | None = None, dry_run: bool = False,
) -> dict[str, Any]:
    spec_id = canonical_agent(agent)
    argv_prefix, source, detail = resolve_agent(spec_id)
    if not argv_prefix:
        raise NekoError(f"{spec_id} 没装（{detail}）。用 `neko.py agents` 看本机可用清单。")
    argv = build_agent_argv(spec_id, argv_prefix, task, model=model, json_output=json_output, extra=extra)
    if dry_run:
        return {"dry_run": True, "agent": spec_id, "argv": argv, "cwd": cwd or os.getcwd()}
    result = run_subprocess(argv, cwd=cwd or None, timeout=timeout)
    result.update({"agent": spec_id, "label": AGENT_SPECS[spec_id][0], "source": source, "task": task})
    # 不管有没有要求 --json-output，只要 stdout 本身就是 JSON 就顺手解析出来
    # （qwenpaw 的 headless 输出天然是 JSON：{"status","response","elapsed_seconds"}）。
    text = (result.get("stdout") or "").strip()
    if text.startswith(("{", "[")):
        try:
            result["parsed"] = json.loads(text)
        except json.JSONDecodeError:
            pass
    return result


# --------------------------------------------------------------------------- #
# N.E.K.O. 能力
# --------------------------------------------------------------------------- #


_PLUGIN_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
PLUGIN_CACHE_TTL = 60.0


def list_plugins(plugin_base: str, *, fresh: bool = False) -> list[dict[str, Any]]:
    """插件清单。``/plugins`` 真机上约 953KB / 5s，所以进程内缓存 60s（MCP 模式收益最大）。

    环境变量 ``NEKO_NO_CACHE=1`` 可禁用缓存。
    """
    if not fresh and not os.environ.get("NEKO_NO_CACHE"):
        cached = _PLUGIN_CACHE.get(plugin_base)
        if cached and (time.time() - cached[0]) < PLUGIN_CACHE_TTL:
            return cached[1]
    payload = _request(plugin_base, "GET", "/plugins")
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    if not isinstance(plugins, list):
        raise NekoError("插件列表格式不对（期望 {'plugins': [...]}）")
    rows = [row for row in plugins if isinstance(row, dict)]
    _PLUGIN_CACHE[plugin_base] = (time.time(), rows)
    return rows


def plugin_entries(plugin_base: str, plugin_id: str) -> list[dict[str, Any]]:
    for row in list_plugins(plugin_base):
        if str(row.get("id")) == plugin_id:
            entries = row.get("entries_preview") or row.get("entries") or []
            return [item for item in entries if isinstance(item, dict)]
    raise NekoError(f"没有这个插件：{plugin_id}（用 `neko.py plugins` 看清单）")


def all_entries(plugin_base: str) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for row in list_plugins(plugin_base):
        plugin_id = str(row.get("id"))
        for entry in row.get("entries_preview") or row.get("entries") or []:
            if isinstance(entry, dict):
                result.append((plugin_id, entry))
    return result


def run_entry(
    plugin_base: str,
    plugin_id: str,
    entry_id: str,
    args: dict[str, Any],
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """走官方运行接口调一个入口：``POST /runs`` → 轮询 → ``GET /runs/{id}/export``。"""
    created = _request(
        plugin_base,
        "POST",
        "/runs",
        {"plugin_id": plugin_id, "entry_id": entry_id, "args": args},
    )
    run_id = str((created or {}).get("run_id") or "")
    if not run_id:
        raise NekoError(f"POST /runs 没有返回 run_id：{json.dumps(created, ensure_ascii=False)[:200]}")
    record: dict[str, Any] = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = _request(plugin_base, "GET", f"/runs/{run_id}") or {}
        if str(record.get("status")) in TERMINAL_STATUSES:
            break
        time.sleep(0.4)
    payload: dict[str, Any] = {}
    exported: Any = {}
    try:
        exported = _request(plugin_base, "GET", f"/runs/{run_id}/export")
    except NekoError as exc:
        exported = {"error": str(exc)}
    if isinstance(exported, dict):
        for item in exported.get("items") or exported.get("entries") or []:
            if not isinstance(item, dict) or item.get("label") != "trigger_response":
                continue
            body = item.get("json")
            if isinstance(body, dict) and isinstance(body.get("data"), dict):
                payload = body["data"]
            elif isinstance(item.get("data"), dict):
                payload = item["data"]
            break
        payload = payload or exported.get("data") or {}
    return {
        "ok": str(record.get("status")) == "succeeded",
        "status": record.get("status"),
        "run_id": run_id,
        "plugin_id": plugin_id,
        "entry_id": entry_id,
        "result": payload,
        "raw": record if not payload else {},
    }


def recent_pushes(plugin_base: str, limit: int = 10) -> list[dict[str, Any]]:
    """最近进入对话的消息。

    主路径是插件服务器**已验证**的读端点 ``GET /plugin/messages``；
    拿不到（老版本/权限）再回退到 ``bot_bridge:bridge_status`` 的 ``recent_pushes``。
    """
    try:
        payload = _request(plugin_base, "GET", f"/plugin/messages?limit={int(limit)}")
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list) and payload["messages"]:
            return [item for item in payload["messages"] if isinstance(item, dict)][-int(limit):]
    except NekoError:
        pass
    result = run_entry(plugin_base, "bot_bridge", "bridge_status", {}, timeout=60.0)
    payload = result.get("result") or {}
    pushes = payload.get("recent_pushes")
    if not isinstance(pushes, list):
        raise NekoError(
            "读不到推送历史：需要装并运行 bot_bridge 插件（它提供 bridge_status.recent_pushes）"
        )
    return [item for item in pushes if isinstance(item, dict)][-int(limit) :]


def _is_external(plugin_id: str, entry_id: str, entry: dict[str, Any]) -> bool:
    blob = f"{plugin_id} {entry_id} {entry.get('name') or ''} {entry.get('description') or ''}".lower()
    return any(hint in blob for hint in EXTERNAL_HINTS)


def pick_push_entry(plugin_base: str, text: str) -> tuple[str, str, dict[str, Any]]:
    """挑一个"把文本推进 N.E.K.O. 对话"的入口。

    **默认不许自动挑**（``SAFE_PUSH_PLUGINS`` 为空）：白名单之外一律拒绝，并把"看起来像外部
    平台发送器"的候选点名报出来 —— 免得一次「推条消息」变成往 B 站/微信发私信。
    """
    skipped_external: list[str] = []
    candidates: list[str] = []
    for plugin_id, entry in all_entries(plugin_base):
        entry_id = str(entry.get("id") or "")
        if entry_id.startswith("__") or entry_id.startswith("bot_cmd_"):
            continue
        if _is_external(plugin_id, entry_id, entry):
            skipped_external.append(f"{plugin_id}:{entry_id}")
            continue
        if plugin_id not in SAFE_PUSH_PLUGINS:
            continue
        schema = entry.get("input_schema") or {}
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict):
            continue
        text_arg = next((name for name in TEXT_ARG_NAMES if name in properties), "")
        if not text_arg:
            candidates.append(f"{plugin_id}:{entry_id}（没有文本参数）")
            continue
        return plugin_id, entry_id, {text_arg: text}

    detail = "；".join(candidates) if candidates else "（白名单为空，未做任何挑选）"
    avoided = f"；已跳过 {len(skipped_external)} 个外部平台发送器：{'、'.join(skipped_external[:4])}" if skipped_external else ""
    raise NekoError(
        "say 需要显式指定推送入口（默认不做自动挑选，避免误发到外部平台）："
        "`neko.py say <text> --entry 插件:入口 --yes`。"
        f"候选提示：{detail}{avoided}"
    )


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


def emit(payload: Any, *, as_json: bool, text: str = "") -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(text or json.dumps(payload, ensure_ascii=False))


def _i18n_text(value: Any) -> str:
    """入口描述可能是 ``{"$i18n": ..., "default": "..."}``，取可读的那份。"""
    if isinstance(value, dict):
        for key in ("default", "zh-CN", "en"):
            if isinstance(value.get(key), str):
                return value[key]
        return ""
    return str(value or "")


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #


def cmd_agents(args: argparse.Namespace) -> int:
    """列出本机 Agent 与它们的安装/版本（主干能力：不依赖 N.E.K.O.）。"""
    wanted = [item.strip() for item in (args.only or "").split(",") if item.strip()]
    spec_ids = [canonical_agent(item) for item in wanted] if wanted else sorted(AGENT_SPECS)
    rows = [
        agent_report(spec_id, want_version=not args.no_version, fresh=args.fresh)
        for spec_id in spec_ids
    ]
    if args.installed_only:
        rows = [row for row in rows if row["installed"]]
    if args.json:
        emit(rows, as_json=True)
    else:
        for row in rows:
            mark = "✓" if row["installed"] else "✗"
            version = row["version"] or row["detail"] or ""
            print(f"{mark} {row['agent']:11} {row['label'][:22]:24} {row['source']:10} {version[:60]}")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    """把一条任务派给本机某个 Agent CLI（主干能力：不依赖 N.E.K.O.）。"""
    result = dispatch_agent(
        args.agent,
        args.task,
        model=args.model or "",
        cwd=args.cwd or "",
        timeout=args.timeout,
        json_output=args.json_output,
        extra=args.extra or [],
        dry_run=args.dry_run,
    )
    if args.json:
        emit(result, as_json=True)
    elif result.get("dry_run"):
        print(" ".join(result["argv"]))
    else:
        print(f"[{result['agent']}] 退出码={result['exit_code']} 耗时={result['duration_ms']}ms"
              f"{' 超时' if result.get('timed_out') else ''}{' 截断' if result.get('truncated') else ''}")
        if result.get("error"):
            print(f"错误：{result['error']}", file=sys.stderr)
        # 命令行只回显 argv 形态（任务正文可能很长，截断展示）
        print(" ".join(token if len(token) < 60 else token[:57] + "…" for token in (result.get("argv") or [])))
        if result.get("stdout"):
            print("--- stdout ---")
            print(result["stdout"])
        if result.get("stderr"):
            print("--- stderr ---", file=sys.stderr)
            print(result["stderr"], file=sys.stderr)
    return 0 if result.get("ok") else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    plugin_base, main_base, notes = discover(args)
    plugins = list_plugins(plugin_base)
    running = [row for row in plugins if str(row.get("status")) == "running"]
    payload = {
        "plugin_base": plugin_base,
        "main_base": main_base,
        "note_plugins_call": "首次 /plugins 约 5s（近 1MB），进程内缓存 60s",
        "plugins_total": len(plugins),
        "plugins_running": len(running),
        "notes": notes,
        "neko_cli": __version__,
    }
    emit(payload, as_json=args.json, text="\n".join(f"{k}: {v}" for k, v in payload.items()))
    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    plugin_base, _main, _notes = discover(args)
    rows = [
        {
            "id": row.get("id"),
            "status": row.get("status"),
            "version": row.get("version"),
            "type": row.get("type"),
            "plugin_type": row.get("plugin_type"),
            "description": _i18n_text(row.get("description") or row.get("short_description"))[:120],
        }
        for row in list_plugins(plugin_base)
    ]
    if args.running:
        rows = [row for row in rows if row["status"] == "running"]
    if args.json:
        emit(rows, as_json=True)
    else:
        for row in rows:
            print(f"{str(row['status'] or '?'):9} {row['id']:28} {row['version'] or '':8} {row['description'][:70]}")
    return 0


def cmd_entries(args: argparse.Namespace) -> int:
    plugin_base, _main, _notes = discover(args)
    if args.plugin:
        pairs = [(args.plugin, entry) for entry in plugin_entries(plugin_base, args.plugin)]
    else:
        pairs = all_entries(plugin_base)
    rows = [
        {
            "plugin_id": plugin_id,
            "entry_id": entry.get("id"),
            "name": _i18n_text(entry.get("name")),
            "description": _i18n_text(entry.get("description"))[:160],
            "timeout": entry.get("timeout"),
            "input_schema": entry.get("input_schema") or {},
        }
        for plugin_id, entry in pairs
        if not str(entry.get("id") or "").startswith("__")
    ]
    if args.json:
        emit(rows, as_json=True)
    else:
        for row in rows:
            print(f"{row['plugin_id']:22} {str(row['entry_id']):34} {row['name'][:24]:24} {row['description'][:60]}")
    return 0


def _parse_args(raw: list[str] | None, inline: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if inline:
        try:
            loaded = json.loads(inline)
        except json.JSONDecodeError as exc:
            raise NekoError(f"--args 不是合法 JSON：{exc}") from exc
        if not isinstance(loaded, dict):
            raise NekoError("--args 必须是 JSON 对象")
        payload.update(loaded)
    for item in raw or []:
        if "=" not in item:
            raise NekoError(f"--arg 需要 key=value 形式：{item!r}")
        key, value = item.split("=", 1)
        try:
            payload[key] = json.loads(value)
        except json.JSONDecodeError:
            payload[key] = value
    return payload


def cmd_run(args: argparse.Namespace) -> int:
    plugin_base, _main, _notes = discover(args)
    plugin_id = args.plugin
    entry_id = args.entry
    if ":" in entry_id and not plugin_id:
        plugin_id, entry_id = entry_id.split(":", 1)
    if not plugin_id:
        matches = [pid for pid, entry in all_entries(plugin_base) if str(entry.get("id")) == entry_id]
        if len(matches) != 1:
            raise NekoError(f"入口 {entry_id!r} 归属不明（命中插件：{matches or '无'}）；请用 --plugin 或 plugin:entry")
        plugin_id = matches[0]
    result = run_entry(
        plugin_base, plugin_id, entry_id, _parse_args(args.arg, args.args), timeout=args.timeout
    )
    emit(result, as_json=args.json or not args.text, text=json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_messages(args: argparse.Namespace) -> int:
    plugin_base, _main, _notes = discover(args)
    rows = recent_pushes(plugin_base, args.limit)
    if args.json:
        emit(rows, as_json=True)
    elif not rows:
        print("（宿主消息存储里还没有记录；等一次推送进来再看 —— 推送用 run 调具体插件的入口）")
    else:
        for item in rows:
            print(f"{item.get('source', '?'):26} {item.get('kind', ''):14} {str(item.get('preview', ''))[:70]}")
    return 0


def _resolve_say_target(plugin_base: str, args: argparse.Namespace) -> tuple[str, str, dict[str, Any], bool]:
    if args.entry:
        if ":" not in args.entry:
            raise NekoError("--entry 请用 插件:入口 形式，例如 --entry bot_bridge:bridge_status")
        plugin_id, entry_id = args.entry.split(":", 1)
        entries = {str(item.get("id")): item for item in plugin_entries(plugin_base, plugin_id)}
        if entry_id not in entries:
            raise NekoError(f"{plugin_id} 里没有入口 {entry_id}（用 `neko.py entries --plugin {plugin_id}` 看）")
        payload = _parse_args(args.arg, args.args) or {"text": args.text}
        return plugin_id, entry_id, payload, _is_external(plugin_id, entry_id, entries[entry_id])
    plugin_id, entry_id, payload = pick_push_entry(plugin_base, args.text)
    return plugin_id, entry_id, payload, False


def cmd_say(args: argparse.Namespace) -> int:
    """往 N.E.K.O. 对话推文本。

    默认**只做 dry-run**（打印将调用的插件/入口/参数）；真要执行请加 ``--yes``。
    命中外部平台（B 站/微信/QQ/邮件…）的入口会被拒绝，除非显式 ``--allow-external``。
    """
    plugin_base, _main, _notes = discover(args)
    plugin_id, entry_id, payload, external = _resolve_say_target(plugin_base, args)
    plan = {"plugin_id": plugin_id, "entry_id": entry_id, "args": payload, "external": external}

    if external and not args.allow_external:
        raise NekoError(
            f"拒绝调用外部平台发送器 {plugin_id}:{entry_id}（它会把内容发到站外）。"
            "确认要发就加 --allow-external，或换一个对话推送入口。"
        )
    if not args.yes:
        emit({"dry_run": True, **plan, "hint": "确认无误后加 --yes 执行"}, as_json=True)
        return 0
    result = run_entry(plugin_base, plugin_id, entry_id, payload, timeout=args.timeout)
    emit(result, as_json=True)
    return 0 if result.get("ok") else 1


# --------------------------------------------------------------------------- #
# MCP（stdio）：让支持 MCP 的 Agent（Claude Code / Codex / Cursor / …）直接调用
# --------------------------------------------------------------------------- #

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "neko_agents",
        "description": (
            "列出本机装了哪些编码 Agent CLI（codebuddy/claude/dsh/omp/evox/opencode/openclaw/atomcode/qwenpaw），"
            "含安装状态与版本。这一族不依赖 N.E.K.O.。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"installed_only": {"type": "boolean"}, "with_version": {"type": "boolean"}},
        },
    },
    {
        "name": "neko_dispatch",
        "description": (
            "把一条非交互式任务派给本机某个 Agent CLI，返回它的 stdout/退出码/耗时。"
            "它会真的执行那个 Agent（可能读写文件），只在用户要求时调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "task": {"type": "string"},
                "model": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number"},
                "dry_run": {"type": "boolean", "description": "只回显将执行的命令行"},
            },
            "required": ["agent", "task"],
        },
    },
    {
        "name": "neko_status",
        "description": "N.E.K.O. 是否在跑、插件服务器与主服务在哪、装了多少插件。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "neko_plugins",
        "description": "列出 N.E.K.O. 里装了哪些插件（id / 状态 / 版本）。",
        "inputSchema": {"type": "object", "properties": {"running_only": {"type": "boolean"}}},
    },
    {
        "name": "neko_entries",
        "description": "列出可调用的入口（可选按插件过滤），含参数 schema。",
        "inputSchema": {"type": "object", "properties": {"plugin_id": {"type": "string"}}},
    },
    {
        "name": "neko_run",
        "description": "调用 N.E.K.O. 的某个入口（plugin_id + entry_id + args）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plugin_id": {"type": "string"},
                "entry_id": {"type": "string"},
                "args": {"type": "object"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["plugin_id", "entry_id"],
        },
    },
    {
        "name": "neko_messages",
        "description": "最近进入 N.E.K.O. 对话的消息（读插件服务器已验证的 GET /plugin/messages）。",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "neko_say",
        "description": (
            "调用一个**推送类入口**把文本送进 N.E.K.O.（必须显式给 entry；本机目前没有已验证的通用"
            "对话推送入口，所以这条通常用不了——优先用 neko_run 调具体插件）。外部平台发送器"
            "（B 站/微信/QQ/邮件等）默认拒绝，确认要发才传 allow_external=true。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "entry": {"type": "string", "description": "插件:入口，例如 bot_bridge:bridge_status"},
                "allow_external": {"type": "boolean", "description": "允许调用外部平台发送器（默认 false）"},
            },
            "required": ["text", "entry"],
        },
    },
]


def _mcp_call(name: str, arguments: dict[str, Any]) -> Any:
    # 主干（Agent 派发）不碰 N.E.K.O.，所以先处理、别去 discover 端口。
    if name == "neko_agents":
        rows = [
            agent_report(spec_id, want_version=bool(arguments.get("with_version", True)))
            for spec_id in sorted(AGENT_SPECS)
        ]
        if arguments.get("installed_only"):
            rows = [row for row in rows if row["installed"]]
        return rows
    if name == "neko_dispatch":
        return dispatch_agent(
            str(arguments.get("agent") or ""),
            str(arguments.get("task") or ""),
            model=str(arguments.get("model") or ""),
            cwd=str(arguments.get("cwd") or ""),
            timeout=float(arguments.get("timeout_seconds") or 600.0),
            dry_run=bool(arguments.get("dry_run")),
        )
    plugin_base, main_base, notes = discover(argparse.Namespace(plugin_port=None, main_port=None))
    if name == "neko_status":
        plugins = list_plugins(plugin_base)
        return {
            "plugin_base": plugin_base,
            "main_base": main_base,
            "plugins_total": len(plugins),
            "plugins_running": len([row for row in plugins if row.get("status") == "running"]),
            "notes": notes,
        }
    if name == "neko_plugins":
        rows = list_plugins(plugin_base)
        if arguments.get("running_only"):
            rows = [row for row in rows if row.get("status") == "running"]
        return [{"id": row.get("id"), "status": row.get("status"), "version": row.get("version")} for row in rows]
    if name == "neko_entries":
        plugin_id = str(arguments.get("plugin_id") or "")
        pairs = (
            [(plugin_id, entry) for entry in plugin_entries(plugin_base, plugin_id)]
            if plugin_id
            else all_entries(plugin_base)
        )
        return [
            {
                "plugin_id": pid,
                "entry_id": entry.get("id"),
                "description": _i18n_text(entry.get("description"))[:160],
                "input_schema": entry.get("input_schema") or {},
            }
            for pid, entry in pairs
            if not str(entry.get("id") or "").startswith("__")
        ]
    if name == "neko_run":
        return run_entry(
            plugin_base,
            str(arguments.get("plugin_id")),
            str(arguments.get("entry_id")),
            dict(arguments.get("args") or {}),
            timeout=float(arguments.get("timeout_seconds") or 120.0),
        )
    if name == "neko_messages":
        return recent_pushes(plugin_base, int(arguments.get("limit") or 10))
    if name == "neko_say":
        text = str(arguments.get("text") or "")
        entry = str(arguments.get("entry") or "")
        allow_external = bool(arguments.get("allow_external"))
        if not entry or ":" not in entry:
            raise NekoError(
                "neko_say 需要显式 entry（插件:入口）——不允许自动挑选，避免误发到 B 站/微信等外部平台。"
                "先用 neko_entries 找对话推送入口，或改用 neko_run。"
            )
        plugin_id, entry_id = entry.split(":", 1)
        entries = {str(item.get("id")): item for item in plugin_entries(plugin_base, plugin_id)}
        if entry_id not in entries:
            raise NekoError(f"{plugin_id} 里没有入口 {entry_id}")
        if _is_external(plugin_id, entry_id, entries[entry_id]) and not allow_external:
            raise NekoError(f"{plugin_id}:{entry_id} 是外部平台发送器；确认要发请传 allow_external=true")
        return run_entry(plugin_base, plugin_id, entry_id, {"text": text}, timeout=120.0)
    raise NekoError(f"未知工具：{name}")


def _mcp_write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cmd_mcp(args: argparse.Namespace) -> int:
    """极简 MCP stdio server：initialize / tools/list / tools/call，够 Claude Code 等直接用。"""
    _ = args
    # 用 readline() 而不是 `for line in sys.stdin`：迭代器带预读缓冲，stdio 服务端在这种
    # 交互式场景下会有可感知的延迟（也更容易被"半行"卡住）。
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = str(message.get("method") or "")
        message_id = message.get("id")
        if method == "initialize":
            _mcp_write(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "neko-bridge", "version": __version__},
                    },
                }
            )
        elif method == "tools/list":
            _mcp_write({"jsonrpc": "2.0", "id": message_id, "result": {"tools": MCP_TOOLS}})
        elif method == "tools/call":
            params = message.get("params") or {}
            name = str(params.get("name") or "")
            try:
                result = _mcp_call(name, dict(params.get("arguments") or {}))
                text = json.dumps(result, ensure_ascii=False, indent=2)
                _mcp_write(
                    {"jsonrpc": "2.0", "id": message_id, "result": {"content": [{"type": "text", "text": text}]}}
                )
            except NekoError as exc:
                _mcp_write(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": {"content": [{"type": "text", "text": f"调用失败：{exc}"}], "isError": True},
                    }
                )
        elif method.startswith("notifications/"):
            continue
        elif message_id is not None:
            _mcp_write(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neko",
        description="让任意 Agent CLI 调用 N.E.K.O.（标准库实现，无依赖）",
    )
    parser.add_argument("--version", action="version", version=f"neko {__version__}")
    parser.add_argument("--plugin-port", type=int, help="N.E.K.O. 插件服务器端口（默认自动发现）")
    parser.add_argument("--main-port", type=int, help="N.E.K.O. 主服务端口（默认自动发现）")
    sub = parser.add_subparsers(dest="command", required=True)

    agents = sub.add_parser("agents", help="列出本机 Agent CLI（主干，不依赖 N.E.K.O.）")
    agents.add_argument("--json", action="store_true")
    agents.add_argument("--installed-only", action="store_true")
    agents.add_argument("--no-version", action="store_true", help="不探测版本（快）")
    agents.add_argument("--fresh", action="store_true", help="忽略版本缓存重新探测")
    agents.add_argument("--only", default="", help="只看这些 Agent（逗号分隔，例：qwenpaw,evox）")
    agents.set_defaults(func=cmd_agents)

    dispatch = sub.add_parser("dispatch", help="把任务派给本机某个 Agent CLI（主干）")
    dispatch.add_argument("agent", help=f"{'/'.join(sorted(AGENT_SPECS))}（支持别名）")
    dispatch.add_argument("task", help="任务描述（非交互式一次性执行）")
    dispatch.add_argument("--model", default="", help="透传给支持 --model 的 Agent")
    dispatch.add_argument("--cwd", default="", help="工作目录（默认当前目录）")
    dispatch.add_argument("--timeout", type=float, default=600.0)
    dispatch.add_argument("--json-output", action="store_true", help="要求 Agent 输出 JSON（若它支持）")
    dispatch.add_argument("--extra", action="append", help="追加的原始 argv（可重复）")
    dispatch.add_argument("--dry-run", action="store_true", help="只打印将执行的命令行")
    dispatch.add_argument("--json", action="store_true", help="以 JSON 打印结果")
    dispatch.set_defaults(func=cmd_dispatch)

    doctor = sub.add_parser("doctor", help="自检：N.E.K.O. 端口发现与连通性")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    plugins = sub.add_parser("plugins", help="列出 N.E.K.O. 插件")
    plugins.add_argument("--running", action="store_true", help="只看在跑的")
    plugins.add_argument("--json", action="store_true")
    plugins.set_defaults(func=cmd_plugins)

    entries = sub.add_parser("entries", help="列出入口（含参数 schema）")
    entries.add_argument("--plugin", default="", help="只看这个插件")
    entries.add_argument("--json", action="store_true")
    entries.set_defaults(func=cmd_entries)

    run = sub.add_parser("run", help="调用一个入口")
    run.add_argument("entry", help="入口 id，或 插件:入口")
    run.add_argument("--plugin", default="", help="插件 id（入口 id 有歧义时必填）")
    run.add_argument("--args", default="", help="JSON 对象形式的参数")
    run.add_argument("--arg", action="append", help="单个参数 key=value（可重复）")
    run.add_argument("--timeout", type=float, default=120.0, help="等待秒数")
    run.add_argument("--json", action="store_true")
    run.add_argument("--text", action="store_true", help="只输出结果（不打印 JSON 包装）")
    run.set_defaults(func=cmd_run)

    messages = sub.add_parser("messages", help="最近进入对话的推送")
    messages.add_argument("--limit", type=int, default=10)
    messages.add_argument("--json", action="store_true")
    messages.set_defaults(func=cmd_messages)

    say = sub.add_parser("say", help="往 N.E.K.O. 对话推一条文本")
    say.add_argument("text")
    say.add_argument("--entry", default="", help="显式指定 插件:入口（不填则拒绝执行，避免误发）")
    say.add_argument("--yes", action="store_true", help="真的执行（默认只 dry-run）")
    say.add_argument("--allow-external", action="store_true", help="允许调用外部平台发送器（默认拒绝）")
    say.add_argument("--args", default="", help="覆盖默认参数（JSON）")
    say.add_argument("--arg", action="append")
    say.add_argument("--timeout", type=float, default=120.0)
    say.add_argument("--json", action="store_true")
    say.set_defaults(func=cmd_say)

    mcp = sub.add_parser("mcp", help="以 MCP stdio server 运行")
    mcp.set_defaults(func=cmd_mcp)
    return parser


def _configure_stdio() -> None:
    """把 stdout/stderr 固定成 UTF-8。

    两个理由，都是踩过的：
    * Windows 默认按控制台码页（cp936 之类）编码，插件描述里的中文/符号会直接把
      ``print`` 打成 ``OSError: [Errno 22] Invalid argument``（被别的东西管道抓走时尤其明显）；
    * MCP 的 stdio 传输**要求** UTF-8 JSON-RPC。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — 重配失败就按原样跑，不能因此崩掉
                pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except NekoError as exc:
        print(f"neko: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
