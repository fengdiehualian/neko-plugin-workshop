"""fail-to-pass 证据：把"上传后自动解析描述"的必要条件逐个拆掉，确认看门狗会红。

拆的四种情况，每一种都对应一个真实的坏结果：

1. VLM 返回空时也照样覆盖 desc → 描述被写成空，猫娘挑图的依据没了
2. `describe_sticker` 解析不出来却不报错 → 界面上点了"重新解析"什么都没变
3. 上传时用的不是表情包那条提示词 → 描述变成客观转写，不适合当挑图依据
4. 前端不传 `auto_desc` → 开关是摆设，自动解析永远不会发生

铁律（沿用 verify_page_structure_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程（`pytest.main` 同进程不会重新导入
模块，改文件对已 import 的模块无效）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_sticker_auto_desc_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_sticker_auto_desc.py"),
    str(TESTS / "test_qq_sticker_desc_wiring.py"),
]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "__init__.py",
        "VLM 返回空时也照样覆盖 desc（描述被写成空）",
        """            if vlm_desc:
                data[sid] = {"desc": vlm_desc, "path": dest_name}""",
        """            if True:
                data[sid] = {"desc": vlm_desc, "path": dest_name}""",
    ),
    (
        "__init__.py",
        "describe_sticker 解析不出来却不报错",
        """        desc = await self._vlm_describe_locator(full_path, prompt=STICKER_VLM_PROMPT, max_tokens=80)
        if not desc:""",
        """        desc = await self._vlm_describe_locator(full_path, prompt=STICKER_VLM_PROMPT, max_tokens=80)
        if False:""",
    ),
    (
        "__init__.py",
        "上传时用的不是表情包那条提示词",
        "str(dest_path), prompt=STICKER_VLM_PROMPT, max_tokens=80,",
        'str(dest_path), prompt="描述这张图片", max_tokens=80,',
    ),
    (
        "static/status.html",
        "前端不传 auto_desc（开关成摆设）",
        "desc: desc, auto_desc: autoDesc });",
        "desc: desc });",
    ),
    (
        "__init__.py",
        "把看图改成 vision 槽（会连带改变引用回复图片描述 —— 使用者要求不动它）",
        'cfg = get_config_manager().get_model_api_config("conversation")',
        'cfg = get_config_manager().get_model_api_config("vision")',
    ),
    (
        "__init__.py",
        "没有看图模型配置时静默返回空（用户无从判断原因）",
        """            self.logger.info(
                "[VLM] 没有可用的看图模型配置（conversation 缺 model 或 base_url）")
            return \"\"""",
        """            return \"\"""",
    ),
]


def _run_pytest() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TEST_FILES, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    results: list[tuple[str, bool]] = []
    sources = {rel: (PLUGIN / rel).read_text(encoding="utf-8")
               for rel, _l, _o, _w in MUTATIONS}

    for rel, label, old, new in MUTATIONS:
        source = sources[rel]
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((f"{rel}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((f"{rel}: {label}", False))
            continue

        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = code == 1 and restored
        results.append((f"{rel}: {label}", ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: exit={code}（期望 1）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（修复在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 自动解析描述的每处必要条件都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
