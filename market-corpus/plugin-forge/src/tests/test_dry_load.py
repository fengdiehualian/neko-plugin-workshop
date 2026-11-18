# -*- coding: utf-8 -*-
"""dry_load 的回归：坏样例必须失败，好样例必须通过，降级必须明说。

这一组用例的意义在于「执行层」：以前所有门禁都不运行插件代码，
agent 自报「测过了」没法证伪。

夹具的入口写法全用上游真能装的包写法（`plugin.plugins.<目录名>:符号`，类在 __init__.py）。
2026-11-17 对上游实测：裸模块名（`main:符号`）在 source_only 两种取值下都装不进，
所以以前那套 `main:DemoPlugin` 夹具是个假绿——它只在降级腿下才算好。
"""
import io
import os

import pytest

TOML_OK = ("[plugin]\n"
           'id = "demo_plugin"\n'
           'version = "1.0.0"\n'
           'entry = "plugin.plugins.demo_plugin:DemoPlugin"\n'
           'author = "t"\n'
           'name = "n"\n'
           'description = "d"\n'
           "\n[plugin_runtime]\n"
           "auto_start = true\n")
SRC_OK = """from plugin.sdk.plugin import NekoPluginBase, neko_plugin, llm_tool


@neko_plugin
class DemoPlugin(NekoPluginBase):
    async def on_load(self):
        return 1

    @llm_tool(name='t')
    async def t(self):
        return 2
"""

# 假上游：照 host.py:557-582、656-683 的规矩解析包写法，别用 sys.path 兜底糊过去
FAKE_HOST = (
    "import importlib.util, os, sys\n\n\n"
    "def _import_plugin_module(module_path, config_path, logger, *, source_only=False):\n"
    "    parts = [x for x in str(module_path).split('.') if x]\n"
    "    if parts[:1] == ['plugin']:\n"
    "        parts = parts[1:]\n"
    "    directory = os.path.dirname(str(config_path))\n"
    "    if len(parts) < 2 or parts[0] != 'plugins' or parts[1] != os.path.basename(directory):\n"
    "        if source_only:\n"
    "            raise ValueError('Source-only plugin module must match its source directory')\n"
    "        import importlib as _il\n"
    "        return _il.import_module(module_path)\n"
    "    name = 'forge_probe_' + parts[1]\n"
    "    spec = importlib.util.spec_from_file_location(\n"
    "        name, os.path.join(directory, '__init__.py'), submodule_search_locations=[directory])\n"
    "    if spec is None or spec.loader is None:\n"
    "        raise ImportError('no spec for ' + name)\n"
    "    mod = importlib.util.module_from_spec(spec)\n"
    "    sys.modules[name] = mod\n"
    "    spec.loader.exec_module(mod)\n"
    "    return mod\n")


def _pkg(root, name, toml, files):
    d = os.path.join(str(root), name)
    os.makedirs(d, exist_ok=True)
    io.open(os.path.join(d, "plugin.toml"), "w", encoding="utf-8", newline="\n").write(toml)
    for fn, body in files.items():
        p = os.path.join(d, fn.replace("/", os.sep))
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        io.open(p, "w", encoding="utf-8", newline="\n").write(body)
    return d


def _fake_repo(base):
    """搭一个最小假上游：plugin.sdk.plugin 与 plugin.core.host 两个件够用。"""
    sdk = os.path.join(base, "plugin", "sdk", "plugin")
    core = os.path.join(base, "plugin", "core")
    os.makedirs(sdk)
    os.makedirs(core)
    for p in (os.path.join(base, "plugin", "__init__.py"),
              os.path.join(base, "plugin", "sdk", "__init__.py"),
              os.path.join(core, "__init__.py")):
        io.open(p, "w", encoding="utf-8").write("")
    io.open(os.path.join(sdk, "__init__.py"), "w", encoding="utf-8", newline="\n").write(
        "class NekoPluginBase(object):\n    pass\n\n\n"
        "def neko_plugin(cls=None, **k):\n"
        "    return cls if cls is not None else (lambda c: c)\n\n\n"
        "def plugin_entry(*a, **k):\n    return lambda c: c\n\n\n"
        "def llm_tool(*a, **k):\n    return lambda f: f\n")
    io.open(os.path.join(core, "host.py"), "w", encoding="utf-8", newline="\n").write(FAKE_HOST)
    return base


def test_好样例必须退_0(dry_load, tmp_path):
    d = _pkg(tmp_path, "demo_plugin", TOML_OK, {"__init__.py": SRC_OK})
    code, rep = dry_load.run(d)
    assert code == 0, rep["problems"]
    assert rep["mode"] == "ast-fallback"
    assert rep.get("note"), "降级模式必须明写它证明不了能 import"


def test_仓内对照样例保持一好一坏(dry_load, repo_root):
    base = os.path.join(repo_root, "tests", "fixtures", "plugins")
    ok_code, _r1 = dry_load.run(os.path.join(base, "demo_ok"))
    bad_code, r2 = dry_load.run(os.path.join(base, "demo_broken"))
    assert ok_code == 0
    assert bad_code == 1
    assert any("GhostPlugin" in x for x in r2["problems"]), r2


@pytest.mark.parametrize("kind", [
    "syntax", "no_symbol", "not_async", "bad_entry_shape", "type_removed",
    "host_section", "id_mismatch", "missing_toml", "relative_import",
    "bare_module_entry", "pkg_prefix_mismatch",
])
def test_坏形状各自必须失败(dry_load, tmp_path, kind):
    if kind == "syntax":
        d = _pkg(tmp_path, "demo_plugin", TOML_OK, {"__init__.py": "def broken(:\n"})
    elif kind == "no_symbol":
        d = _pkg(tmp_path, "demo_plugin", TOML_OK,
                 {"__init__.py": SRC_OK.replace("class DemoPlugin", "class ElsePlugin")})
    elif kind == "not_async":
        d = _pkg(tmp_path, "demo_plugin", TOML_OK,
                 {"__init__.py": SRC_OK.replace("    async def t(self)", "    def t(self)")})
    elif kind == "bad_entry_shape":
        d = _pkg(tmp_path, "demo_plugin",
                 TOML_OK.replace('entry = "plugin.plugins.demo_plugin:DemoPlugin"',
                                 'entry = "plugin.plugins.demo_plugin.DemoPlugin"'),
                 {"__init__.py": SRC_OK})
    elif kind == "type_removed":
        d = _pkg(tmp_path, "demo_plugin",
                 TOML_OK.replace('version = "1.0.0"', 'version = "1.0.0"\ntype = "extension"'),
                 {"__init__.py": SRC_OK})
    elif kind == "host_section":
        d = _pkg(tmp_path, "demo_plugin", TOML_OK + "\n[plugin.host]\nfoo = 1\n",
                 {"__init__.py": SRC_OK})
    elif kind == "id_mismatch":
        d = _pkg(tmp_path, "other_dir", TOML_OK, {"__init__.py": SRC_OK})
    elif kind == "relative_import":
        d = _pkg(tmp_path, "demo_plugin", TOML_OK,
                 {"__init__.py": SRC_OK + "\nfrom . import ghost\n"})
    elif kind == "bare_module_entry":
        # 裸模块名：schema 的正则放得下，宿主两种装载模式都装不进（2026-11-17 对上游实测）
        d = _pkg(tmp_path, "demo_plugin",
                 TOML_OK.replace('entry = "plugin.plugins.demo_plugin:DemoPlugin"',
                                 'entry = "main:DemoPlugin"'),
                 {"main.py": SRC_OK, "__init__.py": SRC_OK})
    elif kind == "pkg_prefix_mismatch":
        d = _pkg(tmp_path, "demo_plugin",
                 TOML_OK.replace("plugin.plugins.demo_plugin", "plugin.plugins.other_name"),
                 {"__init__.py": SRC_OK})
    else:  # missing_toml
        d = str(tmp_path / "empty_dir")
        os.makedirs(d, exist_ok=True)
        io.open(os.path.join(d, "__init__.py"), "w", encoding="utf-8").write("x = 1\n")
    code, rep = dry_load.run(d)
    assert code == 1, (kind, code, rep.get("problems"))
    assert rep["problems"], kind
    if kind == "bare_module_entry":
        assert any("装不进" in x for x in rep["problems"]), rep["problems"]
    if kind == "pkg_prefix_mismatch":
        assert any("host.py:664" in x or "目录名" in x for x in rep["problems"]), rep["problems"]


def test_目录不存在是用法错误不是发现问题(dry_load, tmp_path):
    code, rep = dry_load.run(os.path.join(str(tmp_path), "nope"))
    assert code == 2
    assert rep["mode"] == "usage"


def test_安装态前缀也合法(dry_load, tmp_path):
    """plugins.<id> 是归一化后的前缀，上游两种模式都进得来（本轮实测）。"""
    toml = TOML_OK.replace("plugin.plugins.demo_plugin", "plugins.demo_plugin")
    d = _pkg(tmp_path, "demo_plugin", toml, {"__init__.py": SRC_OK})
    code, rep = dry_load.run(d)
    assert code == 0, rep["problems"]


def test_真装载那条腿在_CI_上确实跑到(dry_load, tmp_path):
    """假上游里放同签名且同规矩的 _import_plugin_module，真装载分支就有覆盖。"""
    import subprocess
    import sys

    fake = _fake_repo(str(tmp_path / "repo"))
    pkg_root = os.path.join(fake, "pkgs", "demo_plugin")
    os.makedirs(pkg_root, exist_ok=True)
    io.open(os.path.join(pkg_root, "plugin.toml"), "w", encoding="utf-8", newline="\n").write(TOML_OK)
    entry_py = os.path.join(pkg_root, "__init__.py")
    io.open(entry_py, "w", encoding="utf-8", newline="\n").write(SRC_OK)
    code, rep = dry_load.run(pkg_root, py=sys.executable, repo=fake)
    assert rep["mode"] == "real-import", rep
    assert code == 0, rep["problems"]
    # 入口符号改名：AST 层就该拦住
    io.open(entry_py, "w", encoding="utf-8", newline="\n").write(
        SRC_OK.replace("class DemoPlugin", "class GonePlugin"))
    code2, rep2 = dry_load.run(pkg_root, py=sys.executable, repo=fake)
    assert code2 == 1, rep2


def test_真装载对裸模块名写法必须报装不进(dry_load, tmp_path):
    """降级腿以前会放这种写法过去；强腿接上上游函数以后它必然失败。"""
    import sys

    fake = _fake_repo(str(tmp_path / "repo"))
    pkg_root = os.path.join(fake, "pkgs", "demo_plugin")
    os.makedirs(pkg_root, exist_ok=True)
    io.open(os.path.join(pkg_root, "plugin.toml"), "w", encoding="utf-8", newline="\n").write(
        TOML_OK.replace('entry = "plugin.plugins.demo_plugin:DemoPlugin"', 'entry = "main:DemoPlugin"'))
    io.open(os.path.join(pkg_root, "main.py"), "w", encoding="utf-8", newline="\n").write(SRC_OK)
    io.open(os.path.join(pkg_root, "__init__.py"), "w", encoding="utf-8", newline="\n").write(SRC_OK)
    code, rep = dry_load.run(pkg_root, py=sys.executable, repo=fake)
    assert code == 1, rep
    assert any("装不进" in x for x in rep["problems"]), rep["problems"]


def test_拿不到凭据判据必须停跑(dry_load, tmp_path, monkeypatch):
    """A791 第 2 条③：sandbox import 坏掉时，旧版只打一行警告就照跑。

    那时子进程继承宿主全部环境变量，凭据过滤整个跳过，结论还照打「通过」。
    现在 fail-closed：不跑真装载，退出码 2，结论行写「环境阻断」。
    """
    import contextlib
    import io as _io
    import sys

    # 让 `from sandbox import env_is_secret` 立刻 ImportError：
    # sys.modules 里放 None 是 Python 自己的「该模块不可用」语义，不用改文件系统。
    monkeypatch.setitem(sys.modules, "sandbox", None)
    d = _pkg(tmp_path, "demo_plugin", TOML_OK, {"__init__.py": SRC_OK})
    fake = _fake_repo(str(tmp_path / "repo"))
    code, rep = dry_load.run(d, py=sys.executable, repo=fake)

    assert code == 2, rep
    assert rep["mode"] == "blocked"
    assert any("凭据过滤" in x for x in rep["problems"]), rep["problems"]

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        dry_load._print(rep, code)
    out = buf.getvalue()
    assert "环境阻断" in out and "没核过" in out, out
    assert "结论：通过" not in out and "结论：降级通过" not in out, out


def test_凭据判据在就直接按沙箱口径过滤(dry_load, tmp_path):
    """反向对照：判据拿得到时，宿主的凭据变量必须不进子进程环境。"""
    os.environ["FORGE_PROBE_API_KEY"] = "sk-" + "probe0123456789012345678901"
    try:
        env = dry_load._isolated_env(str(tmp_path / "home"))
    finally:
        os.environ.pop("FORGE_PROBE_API_KEY", None)
    assert not [k for k in env if "FORGE_PROBE_API_KEY" in k], \
        "宿主凭据变量漏进了子进程环境"


def test_降级结论必须写明降级(dry_load, tmp_path):
    """A791 第 2 条②：不给沙箱根必然降级，结论行不许写成「通过」。"""
    import contextlib
    import io as _io

    d = _pkg(tmp_path, "demo_plugin", TOML_OK, {"__init__.py": SRC_OK})
    code, rep = dry_load.run(d)
    assert (code, rep["mode"]) == (0, "ast-fallback"), rep

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        dry_load._print(rep, code)
    out = buf.getvalue()
    assert "结论：降级通过" in out, out
    assert "结论：通过" not in out.replace("结论：降级通过", ""), out
    assert "降级模式证明不了能不能真的 import" in out, out


def test_真装载超时单列失败不降级(dry_load, tmp_path, monkeypatch):
    """A791 第 2 条②：TimeoutExpired 是 SubprocessError 的子类。

    旧版被归成「环境缺」→ 降级 → 退出码 0，import 期死循环的插件拿到「通过」。
    现在超时是真装载失败：mode 保持 real-import，退出码 1。
    """
    import subprocess as _sp
    import sys

    def _boom(*a, **k):
        raise _sp.TimeoutExpired(cmd="dryload", timeout=180)

    monkeypatch.setattr(dry_load.subprocess, "run", _boom)
    d = _pkg(tmp_path, "demo_plugin", TOML_OK, {"__init__.py": SRC_OK})
    fake = _fake_repo(str(tmp_path / "repo"))
    code, rep = dry_load.run(d, py=sys.executable, repo=fake)

    assert rep["mode"] == "real-import", rep
    assert code == 1, rep
    assert any("超时" in x for x in rep["problems"]), rep["problems"]
