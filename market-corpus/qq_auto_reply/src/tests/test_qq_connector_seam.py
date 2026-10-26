"""``connector_seam`` 的解析行为与漂移守卫。

连接器在两种宿主上都要能跑：有 PR #2996 的宿主走 ``utils.connection.onebot``，
没有的走 ``_vendor`` 里的副本。这里盯住三件事：

1. 宿主真的提供了连接器时，**宿主优先**；
2. 宿主没提供（缺包，**或者**只剩一个空目录）时，回退副本；
3. 两边都在时，``OneBotConnector`` 协议的成员集合必须一致 —— 副本与宿主漂移了就要红。
   这是 PR 合并后拆掉 ``_vendor/`` 时的安全网。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
from types import ModuleType

import pytest
from plugin.plugins.qq_auto_reply import connector_seam

#: 保证导入不出来的宿主包名（没有对应的父包）。
_MISSING_HOST = "no_such_host_package_xyz.connection"


def _fake_connector_package() -> ModuleType:
    """一个"看起来像"宿主连接器包的假模块：9 个必需属性齐全。"""
    mod = ModuleType("fake_host_connector")
    for name in connector_seam._REQUIRED_ATTRS:
        setattr(mod, name, object())
    return mod


def test_host_is_preferred_when_it_really_provides_the_connector(monkeypatch):
    fake = _fake_connector_package()
    monkeypatch.setitem(sys.modules, "fake_host_pkg", fake)

    provider, source = connector_seam._load("fake_host_pkg")

    assert source == "host"
    assert provider is fake


def test_falls_back_when_host_package_is_missing():
    provider, source = connector_seam._load(_MISSING_HOST)

    assert source == "vendored"
    assert connector_seam._provides_connector(provider)
    assert provider.__name__ == connector_seam.VENDORED_PACKAGE


def test_falls_back_when_host_is_an_empty_namespace_package(monkeypatch):
    """空目录也算"宿主没提供"。

    真机上踩过：切分支会把 ``utils/connection/onebot/*.py`` 删掉、却留下 ``__pycache__``，
    那个目录于是变成一个**空命名空间包** —— ``import`` 成功、属性一个没有。
    只看导入成没成功的话这里会误判成宿主可用，回退根本没机会生效。
    """
    empty = ModuleType("empty_namespace_pkg")  # 无任何属性
    monkeypatch.setitem(sys.modules, "empty_host_pkg", empty)

    provider, source = connector_seam._load("empty_host_pkg")

    assert source == "vendored"
    assert connector_seam._provides_connector(provider)


def test_vendored_provider_builds_a_working_connection():
    """回退那条路必须真能用 —— 工厂造得出连接，且符合既有契约。"""
    conn = connector_seam.create_onebot_connection({"qq_connection_mode": "napcat"})

    assert hasattr(conn, "set_inbound_sink")
    assert conn.inbound_sink is None
    assert conn.mode == "napcat"
    assert conn.direction == "reverse"


def test_vendored_matches_host_protocol_surface():
    """副本与宿主的协议成员集合必须一致；宿主不在时本轮无标的，跳过。"""
    host, source = connector_seam._load()
    if source != "host":
        pytest.skip("宿主未提供 utils.connection.onebot，漂移守卫本轮无标的")

    vendored = importlib.import_module(connector_seam.VENDORED_PACKAGE)

    def _public_members(obj) -> set[str]:
        return {name for name in dir(obj) if not name.startswith("_")}

    assert _public_members(host.OneBotConnector) == _public_members(vendored.OneBotConnector), (
        "宿主与副本的 OneBotConnector 协议已经漂移 —— 对齐后（或合并后拆掉 _vendor/）再放行"
    )


# ── 副本里的功能性本地改动必须留痕 ─────────────────────────────────
#
# 2026-09-26 的 4302a9ea 改了副本里的 qq_open_plat.py（5 个 hunk）并新增
# qq_open_platform_media.py，但**没有更新 PROVENANCE.md** —— 于是"副本不是逐字一致"
# 在文档里只剩 lint 那一半，功能性差异零痕迹。重新同步上游时这些改动会**静默**消失
# （不报错）：单聊发图退回只发 `[图片]`、群图上传退回只试旧式直传；
# 而如果连 qq_open_platform_media.py 一起漏拷，`from . import qq_open_platform_media`
# 会让整个副本包 import 失败（宿主没带连接器时插件直接起不来）。
#
# 这三条守卫把"标记"钉成可执行的：标记在、接线在、文件名清单与 PROVENANCE 对得上。

_VENDOR_DIR = pathlib.Path(connector_seam.__file__).resolve().parent / "_vendor" / "connection_onebot"
_PROVENANCE = _VENDOR_DIR / "PROVENANCE.md"

#: 带功能性本地改动的文件 → (标记里必须出现的 commit 短号, 必须还在的接线片段)
LOCAL_PATCHES: dict[str, tuple[str, tuple[str, ...]]] = {
    "qq_open_plat.py": ("4302a9ea", (
        "from . import qq_open_platform_media",
        "qq_open_platform_media.send_private_image(",
        "qq_open_platform_media.upload_image(",
    )),
    "qq_open_platform_media.py": ("4302a9ea", (
        "def upload_image(",
        "def send_private_image(",
    )),
}

#: 标记只认文件头这一段：贴到文件末尾等于没标。
_MARKER_HEAD_LINES = 40


def _vendored_text(name: str) -> str:
    path = _VENDOR_DIR / name
    if not path.is_file():
        pytest.skip(f"_vendor/ 已按 PROVENANCE 的计划拆掉（缺 {name}），本组守卫随之退休")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(LOCAL_PATCHES))
def test_vendored_local_patches_are_marked_and_kept(name: str):
    commit, anchors = LOCAL_PATCHES[name]
    text = _vendored_text(name)

    head = "\n".join(text.splitlines()[:_MARKER_HEAD_LINES])
    assert "LOCAL-PATCH:" in head, (
        f"{name} 丢了文件头的 LOCAL-PATCH 标记 —— 重新同步上游时这些功能改动会被静默抹掉"
    )
    assert commit in head, f"{name} 的标记里缺 commit 短号 {commit}"

    for anchor in anchors:
        assert anchor in text, (
            f"{name} 里的本地接线不见了: {anchor!r} —— 上游副本覆盖回来时就是这样丢的"
        )


def _fenced_blocks(text: str) -> str:
    """把 ``` 围栏里的内容拼起来 —— 文件名清单写在这里面。"""
    inside = False
    collected: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            collected.append(line)
    return "\n".join(collected)


def test_provenance_lists_every_vendored_file():
    """文件名清单必须与实际一致（当初就是漏了 qq_open_platform_media.py）。

    只在正文里提一句不算：清单是**围栏代码块**里的那张表，核对的是那个。
    """
    text = _PROVENANCE.read_text(encoding="utf-8")
    actual = sorted(p.name for p in _VENDOR_DIR.glob("*.py"))
    listed = _fenced_blocks(text)

    assert actual, "副本目录里一个 .py 都没有 —— 路径解析错了"
    missing = [name for name in actual if name not in listed]
    assert not missing, f"PROVENANCE.md 的清单没登记这些副本文件: {missing}"


def test_provenance_documents_the_local_patch():
    text = _PROVENANCE.read_text(encoding="utf-8")

    assert "LOCAL-PATCH" in text, "PROVENANCE.md 里没有标记约定的说明"
    assert "4302a9ea" in text, "PROVENANCE.md 没记这次功能性改动的出处 commit"
    assert "qq_open_platform_media.py" in text, "PROVENANCE.md 没登记自撰的 media 模块"


def test_provenance_records_which_patches_are_live():
    """差异表必须写出「真机是否生效」+ 真机证据。

    为什么单钉这一条：副本里 6 处改动**只有 media 模块在真机路径上**（运行时连的是宿主
    那份连接器，`reply_delivery_node._send_sticker` 又刻意直调 media 自由函数）。不写清楚
    的话，下一个读这份文档的人会以为 `qq_open_plat.py` 里那些转发是活的，从而在
    "优化掉用不上的代码"时把唯一在役的那部分一起删掉。
    """
    text = _PROVENANCE.read_text(encoding="utf-8")

    assert "真机是否生效" in text, "PROVENANCE.md 的差异表缺少「真机是否生效」一列"
    assert "图片直传上传未拿到 file_info" in text, "没记真机证据：旧式直传已失效"
    assert "图片上传成功(分片)" in text, "没记真机证据：分片上传是活的"
    assert "connector_seam" in text and "host" in text, "没写清运行时装的是宿主那份连接器"
