"""fail-to-pass 证据：副本本地改动的「标记」是必要条件，拆掉守卫必须红。

背景：2026-09-26 的 `4302a9ea` 改了 `_vendor/connection_onebot/qq_open_plat.py`（5 个
hunk）并新增 `qq_open_platform_media.py`，**却没更新 PROVENANCE.md** —— 文档里"副本不是
逐字一致"只剩 lint 那一半。重新同步上游时这些改动会**静默**回退：单聊发图退回只发
`[图片]`、群图上传退回只试旧式直传；漏拷 media 模块更会让整个副本包 import 失败
（`connector_seam` 解析直接抛，启动自动回复就失败）。

2026-09-27 补：差异表还要写清**真机生效面** —— 运行时连的是宿主那份连接器，6 处改动里
只有 `qq_open_platform_media` 在真机路径上（`reply_delivery_node._send_sticker` 直调它），
其余几处只在回退部署里跑。不写清楚，下一个人"清理用不上的代码"就会把唯一在役的那部分删掉。

六处变异，覆盖四组守卫：
1. 抹掉 `qq_open_plat.py` 文件头的 LOCAL-PATCH 标记；
2. 抹掉一处本地接线（群图上传转发）；
3. 从 PROVENANCE 的文件名清单里删掉自撰的 media 模块；
4. 从 PROVENANCE 里删掉出处 commit；
5. 差异表不再写「真机是否生效」；
6. 抹掉真机证据（分片上传成功那行）。

铁律（沿用 verify_runtime_transition_lock_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿**；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_vendored_patch_marker_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_connector_seam.py")
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "_vendor/connection_onebot/qq_open_plat.py",
        "文件头的 LOCAL-PATCH 标记被抹掉",
        """# LOCAL-PATCH: 4302a9ea 开放平台富媒体接线（单聊发图 / 群图上传转发 / 附件文件名）""",
        """# （标记被抹掉，模拟某次重新同步上游只拷了文件）""",
    ),
    (
        "_vendor/connection_onebot/qq_open_plat.py",
        "群图上传那处本地接线被上游版本覆盖回去",
        """        return await qq_open_platform_media.upload_image(""",
        """        return await qq_open_platform_media.upload_image_v2(""",
    ),
    (
        "_vendor/connection_onebot/PROVENANCE.md",
        "文件名清单里删掉自撰的 media 模块（当初就是漏了它）",
        # 对齐填充 58 个空格是从源文件实测的（`←` 落在第 83 列）
        "qq_open_platform_media.py" + " " * 58 + "← 插件自撰，上游**没有**这个文件",
        "（清单里少了 media 模块）",
    ),
    (
        "_vendor/connection_onebot/PROVENANCE.md",
        "出处 commit 被抹掉（改动又变成无痕）",
        "`4302a9ea6280954929b644fe9404adebc69f10a2`",
        "`某次未记录的 commit`",
    ),
    (
        "_vendor/connection_onebot/PROVENANCE.md",
        "差异表不再写「真机是否生效」（读者会以为副本那些转发是活的）",
        "| 位置（副本） | 上游 | 副本里改成了什么 | 真机是否生效 |",
        "| 位置（副本） | 上游 | 副本里改成了什么 |",
    ),
    (
        "_vendor/connection_onebot/PROVENANCE.md",
        "真机证据（分片上传成功）被抹掉",
        "图片上传成功(分片)",
        "图片上传成功",
    ),
]


def _adapt(source: str, anchor: str) -> str:
    """把按 LF 写的锚点适配到目标文件的换行风格（本仓库 CRLF 居多）。"""
    if "\r\n" in source and "\r\n" not in anchor:
        return anchor.replace("\n", "\r\n")
    return anchor


def _run(*test_files: str) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *test_files, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    results: list[tuple[str, bool]] = []
    sources = {rel: (PLUGIN / rel).read_text(encoding="utf-8")
               for rel, _l, _o, _w in MUTATIONS}

    baseline = _run(TARGET_FILE)
    if baseline != 0:
        print(f"[FAIL] 目标用例在干净树上就是红的（exit={baseline}）—— 本次取证无效，先修它")
        return 1
    print(f"[OK  ] 目标用例在干净树上绿（exit={baseline}）")

    for rel, label, old_raw, new_raw in MUTATIONS:
        source = sources[rel]
        old, new = _adapt(source, old_raw), _adapt(source, new_raw)
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old_raw[:60]!r}")
            results.append((f"{rel}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((f"{rel}: {label}", False))
            continue

        target_code = control_code = -1
        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            target_code = _run(TARGET_FILE)
            control_code = _run(CONTROL_FILE)
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = target_code != 0 and control_code == 0 and restored
        results.append((f"{rel}: {label}", ok))
        print(
            f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: "
            f"目标 exit={target_code}（期望非 0）；控制组 exit={control_code}（期望 0）；已恢复={restored}"
        )

    code_clean = _run(TARGET_FILE)
    control_ok = code_clean == 0
    results.append(("对照（标记与接线都在必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 标记、接线、清单、出处、真机生效面都是必要条件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
