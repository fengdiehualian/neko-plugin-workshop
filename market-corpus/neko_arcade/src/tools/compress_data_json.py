"""给大 JSON 生成 `.json.gz`（minify + gzip），供打包时"只发布压缩版"。

为什么：宿主打包没有自定义构建步骤，所以改用"仓库里存两份"：
  · `<name>.json`     —— 开发/审阅/再生成用（**不打包**，见 pyproject exclude_files）
  · `<name>.json.gz`  —— minify+gzip 后发布（运行时由 core/datafile.py 透明读取）
实测收益：age.json 1.88MB → 0.07MB、events.json 0.36MB → 0.06MB。

用法：
    python tools/compress_data_json.py            # 生成/更新所有 .gz
    python tools/compress_data_json.py --check    # 只报告，不写文件
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: 需要压缩的数据文件/目录（目录会递归处理其中所有 .json）
TARGETS = [
    "games/remake/resources/data",
    "games/xiuxian/data/items",
    "games/xiuxian/data/levels",
    "games/fishing/fishdata.json",
]


def iter_targets() -> list[Path]:
    files: list[Path] = []
    for rel in TARGETS:
        p = REPO / rel
        if p.is_dir():
            files += sorted(p.rglob("*.json"))
        elif p.is_file():
            files.append(p)
    return sorted(set(files))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告, 不写文件")
    args = ap.parse_args()

    total_raw = total_gz = 0
    for src in iter_targets():
        raw = src.read_bytes()
        try:
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            print(f"跳过（JSON 解析失败）{src.relative_to(REPO)}: {exc}")
            continue
        blob = gzip.compress(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)
        dst = Path(f"{src}.gz")
        if not args.check:
            dst.write_bytes(blob)
        total_raw += len(raw)
        total_gz += len(blob)
        print(f"  {src.relative_to(REPO)}  {len(raw) / 1024:8.1f} KB → "
              f"{len(blob) / 1024:7.1f} KB  (省 {100 - len(blob) * 100 // max(len(raw), 1)}%)")

    print(f"\n合计 {total_raw / 1024 / 1024:.2f} MB → {total_gz / 1024 / 1024:.2f} MB"
          f"  (省 {100 - total_gz * 100 // max(total_raw, 1)}%)"
          + ("  [--check 未写文件]" if args.check else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
