"""core/datafile.py 单测：优先读 `.gz`、回退明文、坏了返回默认值。

背景：包里最大的东西是数据（age.json 1.88MB 占整包 60%），打包不支持自定义
构建步骤 → 仓库存明文 + `.gz`，发布时排除明文（pyproject exclude_files），
运行时由 datafile 透明读取。所以"两种文件都在"和"只有明文"都必须能跑。
"""
from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

from plugin.plugins.neko_arcade.core.datafile import gz_path, read_bytes, read_json

_TMP = Path(__file__).resolve().parent.parent / ".tmp_datafile_test"


def _tmp_dir(name: str) -> Path:
    d = _TMP / name
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_prefers_gz_over_plain():
    """两个文件都在时 → 用 .gz（包里正是这种状态：只有 .gz 会被发布）。"""
    d = _tmp_dir("prefer")
    plain = d / "age.json"
    plain.write_text(json.dumps({"from": "plain"}), encoding="utf-8")
    gz_path(plain).write_bytes(
        gzip.compress(json.dumps({"from": "gz"}).encode("utf-8")))

    assert read_json(plain) == {"from": "gz"}
    assert json.loads(read_bytes(plain)) == {"from": "gz"}


def test_falls_back_to_plain_when_no_gz():
    """只有明文（开发环境）也能读。"""
    d = _tmp_dir("plain")
    plain = d / "events.json"
    plain.write_text(json.dumps({"from": "plain"}), encoding="utf-8")
    assert read_json(plain) == {"from": "plain"}


def test_reads_only_gz():
    """发布包里的状态：明文被排除，只剩 .gz。"""
    d = _tmp_dir("onlygz")
    plain = d / "items.json"
    gz_path(plain).write_bytes(
        gzip.compress(json.dumps([1, 2, 3]).encode("utf-8")))
    assert read_json(plain) == [1, 2, 3]


def test_missing_or_broken_returns_default():
    """缺文件/坏 JSON 一律返回默认值，不抛异常（游戏不该因数据缺失崩）。"""
    d = _tmp_dir("broken")
    assert read_json(d / "nope.json", []) == []

    bad = d / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert read_json(bad, {}) == {}

    bad_gz = d / "badgz.json"
    gz_path(bad_gz).write_bytes(b"not gzip at all")
    assert read_json(bad_gz, "fallback") == "fallback"


def test_gz_path_convention():
    """约定：压缩版就是原文件名后加 .gz（工具与读取器同一约定）。"""
    assert gz_path("a/b/c.json").as_posix().endswith("c.json.gz")
