"""数据文件读取：**优先读同名 `.gz`，回退明文**。

为什么：包体里最大的东西不是代码，是数据 —— 例如人生重开的
`resources/data/age.json`(1.88MB) 与 `events.json`(0.36MB) 两份就占整个包的 60%。
JSON 压缩率极高(minify+gzip 后 age.json 只剩 0.07MB, 省 97%)，而宿主打包不支持
自定义构建步骤，所以做法是：**仓库里既留明文(开发/再生成)，也提交 `.json.gz`；
打包时用 `exclude_files` 排除明文，只发布 `.gz`**；运行期由本模块透明读取。

用法：
    from plugin.plugins.neko_arcade.core.datafile import read_json
    data = read_json(path)          # path 可以是 .json；会自动找 <path>.gz
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any


def gz_path(path: Any) -> Path:
    """`<path>.gz`（约定：压缩版就是在原名后加 .gz）。"""
    return Path(f"{path}.gz")


def read_bytes(path: Any) -> bytes:
    """读原始字节：优先 `.gz`（自动解压），没有就读明文。"""
    p = Path(path)
    gz = gz_path(p)
    if gz.is_file():
        with gzip.open(gz, "rb") as f:
            return f.read()
    return p.read_bytes()


def read_json(path: Any, default: Any = None) -> Any:
    """读 JSON：优先 `.gz`，回退明文；读不到/坏数据返回 default。"""
    p = Path(path)
    gz = gz_path(p)
    try:
        if gz.is_file():
            with gzip.open(gz, "rt", encoding="utf-8") as f:
                return json.load(f)
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default
