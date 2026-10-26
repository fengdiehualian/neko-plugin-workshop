"""删除表情包：登记的摘除、图片文件的删除、以及**不能删错东西**。

**为什么要有这条**：删除是本插件里少见的**真删磁盘文件**的操作，而且它的输入
（`sticker.json` 里的 `path`）是一个本地文件里的字符串 —— 理论上可能被手改过。
所以除了"正常能删掉"，更要钉住几种"删错就出事"的情况：

* 同一个图片文件被登记了两次 → 删掉其中一条时**不能**把文件删走（另一条还在用它）；
* `path` 里带 `../` → 只能按 basename 落到 `data/sticker/` 里，**不能删到目录外面**；
* id 不存在 / 为空 → 报错，且**不能**顺手把别的登记搞坏。

另外 `asset` 入口的 schema 是 `additionalProperties: False`：**dispatch 加了
`delete_sticker` 却忘了往 schema 里加 `id`，前端一调就会被参数校验拦掉** ——
所以 schema 也对一遍（源码级断言，和 `test_qq_deploy_extract_thread.py` 同一个路子）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

BASE = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (BASE / "__init__.py").read_text(encoding="utf-8")


def _plugin(tmp_path: pathlib.Path) -> SimpleNamespace:
    """只需 data_path / logger / session_instruction_service —— 直接调那个方法。"""
    return SimpleNamespace(
        data_path=lambda name: tmp_path / name,
        logger=logging.getLogger("qq.test"),
        session_instruction_service=SimpleNamespace(_sticker_catalog_cache="cached"),
    )


def _seed(tmp_path: pathlib.Path, mapping: dict) -> None:
    (tmp_path / "sticker").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sticker.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )
    for info in mapping.values():
        (tmp_path / "sticker" / pathlib.PurePosixPath(info["path"]).name).write_bytes(b"img")


def _registered(tmp_path: pathlib.Path) -> dict:
    return json.loads((tmp_path / "sticker.json").read_text(encoding="utf-8"))


def _delete(plugin, **kw):
    return asyncio.run(QQAutoReplyPlugin._asset_delete_sticker(plugin, kw))


def test_it_removes_the_entry_and_the_file(tmp_path):
    plugin = _plugin(tmp_path)
    _seed(tmp_path, {"1": {"desc": "猫", "path": "cat.png"}, "2": {"desc": "狗", "path": "dog.png"}})

    res = _delete(plugin, id="1")

    assert res.is_ok, res
    assert res.value["removed_file"] is True
    assert res.value["total"] == 1
    left = _registered(tmp_path)
    assert "1" not in left, "登记没有摘掉"
    assert "2" in left, "误删了别的表情包"
    assert not (tmp_path / "sticker" / "cat.png").exists(), "图片文件没有被删掉"
    assert (tmp_path / "sticker" / "dog.png").exists(), "误删了别的图片文件"


def test_it_invalidates_the_sticker_prompt_cache(tmp_path):
    """表情包目录进了 system prompt，有缓存；删完不清缓存，猫娘还会引用已删的图。"""
    plugin = _plugin(tmp_path)
    _seed(tmp_path, {"1": {"desc": "猫", "path": "cat.png"}})

    _delete(plugin, id="1")

    assert plugin.session_instruction_service._sticker_catalog_cache == "", "没有清掉目录缓存"


def test_unknown_id_is_an_error_and_changes_nothing(tmp_path):
    plugin = _plugin(tmp_path)
    _seed(tmp_path, {"1": {"desc": "猫", "path": "cat.png"}})

    res = _delete(plugin, id="99")

    assert res.is_err, res
    assert "NOT_FOUND" in str(res.error)
    assert _registered(tmp_path) == {"1": {"desc": "猫", "path": "cat.png"}}, "报错时不该动登记"
    assert (tmp_path / "sticker" / "cat.png").exists(), "报错时不该删文件"


def test_empty_id_is_rejected(tmp_path):
    plugin = _plugin(tmp_path)
    _seed(tmp_path, {"1": {"desc": "猫", "path": "cat.png"}})

    res = _delete(plugin, id="   ")

    assert res.is_err, res
    assert "INVALID_INPUT" in str(res.error)
    assert "1" in _registered(tmp_path), "报错时不该动登记"


def test_a_shared_file_is_kept_while_another_entry_uses_it(tmp_path):
    """register_sticker 允许把同一个文件登记两次；删掉其中一条不能把文件删走。"""
    plugin = _plugin(tmp_path)
    _seed(tmp_path, {"1": {"desc": "猫 A", "path": "cat.png"}, "2": {"desc": "猫 B", "path": "cat.png"}})

    res = _delete(plugin, id="1")

    assert res.is_ok, res
    assert res.value["removed_file"] is False, "还有人引用这个文件，不该删"
    assert (tmp_path / "sticker" / "cat.png").exists(), "文件被删了，另一条登记就成了死链"
    assert "2" in _registered(tmp_path)


def test_a_path_with_dotdot_cannot_escape_the_sticker_dir(tmp_path):
    """`path` 来自本地 json，可能被手改过 —— 只能落到 data/sticker/ 里面。"""
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"do not delete me")
    plugin = _plugin(tmp_path)
    (tmp_path / "sticker").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sticker.json").write_text(
        json.dumps({"1": {"desc": "坏路径", "path": "../../outside.png"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    res = _delete(plugin, id="1")

    assert res.is_ok, res
    assert res.value["removed_file"] is False
    assert outside.exists(), "path 里的 ../ 逃出了表情包目录，删到了外面的文件"


def test_the_entry_schema_advertises_delete_sticker_and_id():
    """`additionalProperties: False`：schema 里少了 id，前端的调用会被参数校验拦掉。

    这是源码级断言（schema 写在装饰器里，跑起来才拿得到）—— dispatch 加了动作、
    schema 忘了加参数，是这类入口最典型的半截改动。
    """
    assert "delete_sticker" in SOURCE, "asset 的 schema / dispatch 里没有 delete_sticker"
    assert '"delete_sticker", "attention"' in SOURCE or '"delete_sticker"' in SOURCE, (
        "action 的 enum 里没有 delete_sticker —— 前端传这个 action 会被拦掉"
    )
    assert '"id": {"type": "string"' in SOURCE, (
        "schema 的 properties 里没有 id —— additionalProperties:False 会把这个参数判为非法"
    )
