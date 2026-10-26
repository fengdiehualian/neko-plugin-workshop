"""把插件里的大素材压缩导出成「可直接上传 Gitee」的目录 + manifest.json。

**用法**（默认只处理塔罗；其它分类用 `--category` 追加）：

    python tools/build_asset_manifest.py                       # 塔罗(默认 948px 原尺寸)
    python tools/build_asset_manifest.py --max-side 720        # 再降一档, 体积更小
    python tools/build_asset_manifest.py --out D:\\assets       # 指定输出目录
    python tools/build_asset_manifest.py \
        --category tarot=games/tarot/data \
        --category arcade/cat_evolution=games/cat_evolution/assets

输出结构（分类 = Gitee 仓库里的一级目录，天然"分门别类"）：

    <out>/manifest.json                    # 全局清单: path/sha256/size
    <out>/tarot/<主题>/<子类>/<牌>.webp      # 一类
    <out>/arcade/cat_evolution/xxx.webp    # 又一类

插件侧只需 `self.asset_path("<分类>/<相对路径>")` 就能取到（首次下载 + 缓存 + 校验）。
换素材流程：改 → 跑本脚本 → 把 <out> 内容推到 Gitee → 完事（插件不用改代码，
远端 manifest 优先，插件内置的那份只是离线兜底）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def _mb(n: int) -> float:
    """字节 → MB（保留两位）。"""
    return round(n / 1024 / 1024, 2)


def repo_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                              capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def convert(src: Path, out: Path, quality: int, max_side: int) -> tuple[int, int]:
    """把 src 转成 WebP 写到 out，返回 (原字节, 新字节)。"""
    with Image.open(src) as im:
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        if max_side and max(im.size) > max_side:
            ratio = max_side / max(im.size)
            im = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=quality, method=6)
    blob = buf.getvalue()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return src.stat().st_size, len(blob)


def main() -> int:
    ap = argparse.ArgumentParser(description="导出素材 + 生成 manifest")
    ap.add_argument("--category", action="append", default=None,
                    metavar="分类=路径", help="可重复；默认 tarot=games/tarot/data")
    ap.add_argument("--out", default=r"E:\pythonxx\tkry\tarot-assets")
    ap.add_argument("--quality", type=int, default=82, help="WebP 质量(默认 82)")
    ap.add_argument("--max-side", type=int, default=0, help="最长边上限(0=保持原尺寸)")
    args = ap.parse_args()

    pairs = args.category or ["tarot=games/tarot/data"]
    categories: list[tuple[str, Path]] = []
    for item in pairs:
        name, _, rel = item.partition("=")
        src = (REPO / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
        if not src.is_dir():
            print(f"跳过（目录不存在）: {name} <- {src}")
            continue
        categories.append((name.strip("/"), src))
    if not categories:
        print("没有可处理的分类")
        return 1

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    total_src = total_out = 0

    for name, src in categories:
        files = sorted(p for p in src.rglob("*") if p.suffix.lower() in IMG_EXT)
        subtotal_src = subtotal_out = 0
        for p in files:
            rel = p.relative_to(src).with_suffix(".webp").as_posix()
            asset_path = f"{name}/{rel}"
            old, new = convert(p, out_root / asset_path, args.quality, args.max_side)
            subtotal_src += old
            subtotal_out += new
            entries.append({"path": asset_path,
                            "sha256": hashlib.sha256((out_root / asset_path).read_bytes()).hexdigest(),
                            "size": new})
        total_src += subtotal_src
        total_out += subtotal_out
        print(f"[{name}] {len(files)} 张  {_mb(subtotal_src)} MB → {_mb(subtotal_out)} MB")

    manifest = {
        "version": "1",
        "generated_from": repo_head(),
        "note": "插件素材：客户端首次运行按 path 下载到用户缓存并校验 sha256，缺图回退占位图。",
        "count": len(entries),
        "total_size": total_out,
        "files": entries,
    }
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"合计 {len(entries)} 张: {_mb(total_src)} MB → {_mb(total_out)} MB "
          f"(省 {100 - total_out * 100 // max(total_src, 1)}%)")
    print("输出目录:", out_root)
    print("分类:", ", ".join(n for n, _ in categories))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
