"""离线自检:用最小桩替代 N.E.K.O SDK / pydantic,直接跑安装器插件的纯逻辑。

用途:CI 之外、本机没装 SDK 依赖(甚至 .venv 被删)时,也能验证
分卷选择、拼接校验、文件头识别这些最容易写错的地方。
用法:python tools/offline_check.py
"""

import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path

TMP = Path(os.environ.get("TEMP", r"C:\tmp")) / "wb-review"
TMP.mkdir(parents=True, exist_ok=True)

# ---- pydantic 桩 ----
pyd = types.ModuleType("pydantic")


class _BaseModel:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _Field(default=None, **_):
    return default


pyd.BaseModel = _BaseModel
pyd.Field = _Field
sys.modules["pydantic"] = pyd

# ---- plugin.sdk.plugin 桩 ----
sdk = types.ModuleType("plugin")
sdkp = types.ModuleType("plugin.sdk")
sdkpl = types.ModuleType("plugin.sdk.plugin")


class _Base:
    def __init__(self, ctx=None):
        self.ctx = ctx


def _deco(*a, **k):
    # 支持 @foo 与 @foo(...) 两种写法:直接装饰时第一个位置参数就是被装饰对象
    if len(a) == 1 and callable(a[0]) and not k:
        return a[0]

    def wrap(fn):
        return fn

    return wrap


class _Ok:
    def __init__(self, v):
        self.v = v


class _Err:
    def __init__(self, e):
        self.e = e


class SdkError(Exception):
    pass


sdkpl.NekoPluginBase = _Base
sdkpl.neko_plugin = _deco
sdkpl.llm_tool = _deco
sdkpl.plugin_entry = _deco
sdkpl.lifecycle = _deco
sdkpl.Ok = _Ok
sdkpl.Err = _Err
sdkpl.SdkError = SdkError
sys.modules["plugin"] = sdk
sys.modules["plugin.sdk"] = sdkp
sys.modules["plugin.sdk.plugin"] = sdkpl

IMPL = r"C:\Users\Administrator\dev\n.e.k.o_plugin_workbench_installer\plugin\plugins\workbench_installer\__init__.py"
spec = importlib.util.spec_from_file_location("wb_impl", IMPL)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails.append(name)


print("== _pick_mirror_assets ==")
single = [
    {"name": "N.E.K.O.PluginWorkshop-Payload.7z", "browser_download_url": "u7"},
    {"name": "N.E.K.O.PluginWorkshop-Setup.exe", "browser_download_url": "ux"},
]
check("单文件优先取 7z", mod._pick_mirror_assets(single) == ["u7"])

parts = [
    {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part2", "browser_download_url": "u2"},
    {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part1", "browser_download_url": "u1"},
]
check("分卷按编号排序", mod._pick_mirror_assets(parts) == ["u1", "u2"])

gap = [
    {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part1", "browser_download_url": "u1"},
    {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part3", "browser_download_url": "u3"},
]
check("缺卷直接拒绝", mod._pick_mirror_assets(gap) == [])

mixed = [
    {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.14.exe.part1", "browser_download_url": "a1"},
    {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part1", "browser_download_url": "b1"},
    {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part2", "browser_download_url": "b2"},
]
check("跨版本混排不误判", mod._pick_mirror_assets(mixed) == [])

nourl = [{"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part1", "browser_download_url": ""},
         {"name": "N.E.K.O.PluginWorkshop-Setup-v1.2.15.exe.part2", "browser_download_url": "u2"}]
check("空 URL 不参与拼卷", mod._pick_mirror_assets(nourl) == [])

check("空清单返回空", mod._pick_mirror_assets([]) == [] and mod._pick_mirror_assets(None) == [])

print("== _archive_kind / _looks_like_exe ==")
for name, data, want in [
    ("exe", b"MZ\x90\x00\x03", "exe"),
    ("7z", b"7z\xbc\xaf\x27\x1cxx", "7z"),
    ("xz", b"\xfd7zXZ\x00rest", "xz"),
    ("unknown", b"plain text", "unknown"),
]:
    p = TMP / f"probe_{name}.bin"
    p.write_bytes(data)
    check(f"识别 {name}", mod._archive_kind(str(p)) == want, mod._archive_kind(str(p)))

p = TMP / "mz.bin"
p.write_bytes(b"MZ--")
check("_looks_like_exe 认 MZ", mod._looks_like_exe(str(p)) is True)
check("_looks_like_exe 缺文件返回 False", mod._looks_like_exe(str(TMP / "nope.bin")) is False)

print("== _download_split ==")


class FakePlugin(mod.WorkbenchInstallerPlugin):
    def __init__(self, chunks, fail_on=None):
        self.chunks = chunks
        self.fail_on = fail_on
        self.calls = []

    async def _download(self, url, dest, read_timeout=None):
        self.calls.append(url)
        if self.fail_on and url == self.fail_on:
            return {"ok": False, "kind": "http", "status": 500, "error": "boom"}
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(self.chunks[url])
        return {"ok": True, "path": dest, "bytes": len(self.chunks[url]), "via": "direct"}


dest = str(TMP / "out" / "Setup.exe")
fx = FakePlugin({"https://g/1": b"MZ-AAA", "https://g/2": b"BBB"})
got = asyncio.run(fx._download_split(["https://g/1", "https://g/2"], dest))
check("两卷拼接成功", got.get("ok") is True, got)
check("拼接内容正确", Path(dest).read_bytes() == b"MZ-AAABBB")
check("标记 fallback", got.get("fallback") is True)
check("临时分卷已清理", not list((TMP / "out").glob("*.part*")))
check("via 标注卷数", "分卷 2" in got.get("via", ""), got.get("via"))

bad = FakePlugin({"https://g/1": b"NOTMZ", "https://g/2": b"BBB"})
got = asyncio.run(bad._download_split(["https://g/1", "https://g/2"], str(TMP / "out2" / "s.exe")))
check("非 MZ 拒绝", got.get("ok") is False and "MZ" in got.get("error", ""), got)

failing = FakePlugin({"https://g/1": b"MZ-AAA", "https://g/2": b"BBB"}, fail_on="https://g/2")
got = asyncio.run(failing._download_split(["https://g/1", "https://g/2"], str(TMP / "out3" / "s.exe")))
check("任一卷失败整体失败", got.get("ok") is False and "分卷 2" in got.get("error", ""), got)
check("失败后不留残卷", not list((TMP / "out3").glob("*.part*")))

print()
print("失败项:", fails if fails else "无")
sys.exit(1 if fails else 0)
