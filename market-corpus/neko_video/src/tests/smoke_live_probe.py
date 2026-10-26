"""端到端 smoke：真实网络，验证整条链能产出可用的摘要。

验证：窗口标题 → 反查 BV → 视频信息 → 弹幕 → 热评 → 摘要文本
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from _bili import danmaku, hot_comments, resolve_bv_by_title, video_info
from _digest import build_digest, content_fingerprint
from _window import foreground_title, visible_titles


def main() -> int:
    print("=" * 72)
    print("① 窗口标题")
    print("=" * 72)
    fg = foreground_title()
    print(f"  前台: {fg!r}")
    titles = visible_titles()
    print(f"  可见窗口 {len(titles)} 个")
    bili = [t for t in titles if "哔哩哔哩" in t or "bilibili" in t.lower()]
    print(f"  B站窗口 {len(bili)} 个: {bili[:3]}")

    # 真实标题优先；没有就用测过的样本标题
    sample = bili[0] if bili else "机械迷城_哔哩哔哩_bilibili"
    print(f"\n  使用标题: {sample!r}")

    print()
    print("=" * 72)
    print("② 标题 → BV 反查")
    print("=" * 72)
    t0 = time.time()
    match = resolve_bv_by_title(sample)
    if not match:
        print("  ✗ 未匹配到（相似度不足）")
        return 1
    print(f"  ✓ {match['bvid']} 《{match['title']}》 UP={match['author']} 匹配度={match['score']}")
    print(f"  耗时 {int((time.time()-t0)*1000)}ms")

    print()
    print("=" * 72)
    print("③ 视频信息 + 弹幕 + 热评")
    print("=" * 72)
    info = video_info(match["bvid"])
    print(f"  《{info['title']}》 {info['tname']} {info['duration']}s")

    t0 = time.time()
    dm = danmaku(info["cid"])
    cm = hot_comments(info["aid"])
    print(f"  弹幕 {len(dm)} 条，热评 {len(cm)} 条，耗时 {int((time.time()-t0)*1000)}ms")
    if dm:
        print(f"  弹幕样本: {[d['text'] for d in dm[:3]]}")

    print()
    print("=" * 72)
    print("④ 生成的摘要（这就是要推给猫娘的内容）")
    print("=" * 72)
    digest = build_digest(info, dm, cm)
    print(digest)
    print(f"\n  摘要长度: {len(digest)} 字")
    print(f"  内容指纹: {content_fingerprint(info, dm, cm)}")

    print()
    print("=" * 72)
    print("结论")
    print("=" * 72)
    ok = bool(dm) or bool(cm)
    print(f"  整条链可用: {'✅' if ok else '⚠ 无弹幕/评论，仅标题+简介'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())