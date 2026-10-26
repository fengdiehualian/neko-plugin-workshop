"""fail-to-pass 证据：这轮三件事的**必要条件**逐个拆掉，看门狗必须红。

三件事（使用者确认"3 个都做"）：

1. **私聊发图**：开放平台单聊也能发图（`/v2/users/{openid}/files` + `msg_type=7`）。
   拆掉投递分流 → 私聊表情包又变回"静默不发"；
2. **不白烧 TTS**：通道 `supports_voice=False` 时不该先合成一遍。拆掉那道闸 →
   合成函数又被调用（每次白跑一次 TTS）；
3. **入站非图片附件**：接到文件渲染链路。拆掉派发那一段（或让它永远返回空）→
   附件内容又不进 prompt。

铁律（沿用 verify_session_handoff_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_open_platform_media_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_open_platform_media.py"),
    str(TESTS / "test_qq_private_image_delivery.py"),
    str(TESTS / "test_qq_voice_channel_gate.py"),
    str(TESTS / "test_qq_attachment_files.py"),
]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "reply_delivery_node.py",
        "私聊表情包退回「静默不发」（这轮补的那条路）",
        """        if is_open_platform:
            message_id = await media_seam.send_private_image(
                client, plan.target_id, sticker_path, record_sent=False,
            )
            return self._confirm_platform_result(message_id)
        return self._confirm_platform_result(
            await client.send_private_message_segments(
                plan.target_id, [{"type": "image", "data": {"file": sticker_path}}],
                record_sent=False,
            ),
        )""",
        """        return False
        if is_open_platform:
            message_id = await media_seam.send_private_image(
                client, plan.target_id, sticker_path, record_sent=False,
            )
            return self._confirm_platform_result(message_id)""",
    ),
    (
        "reply_delivery_node.py",
        "群聊表情包退回只实现旧式直传的连接器那条（真机上会静默降级成「[图片]」）",
        """        if plan.target_type == "group":
            if is_open_platform:""",
        """        if plan.target_type == "group":
            if False:""",
    ),
    (
        "voice_reply_service.py",
        "私聊那条语音能力闸拆掉（合成一遍再判失败）",
        """        if not self._client_supports_voice():
            # 这个通道发不出语音：直接按上面那套判据落文字，不做那次注定要丢的合成。
            return await self._private_text_fallback(
                target_qq, normalized_text, mode=mode, fallback=fallback_to_text_on_voice_failure,
            )""",
        """        if False:
            return await self._private_text_fallback(
                target_qq, normalized_text, mode=mode, fallback=fallback_to_text_on_voice_failure,
            )""",
    ),
    (
        "voice_reply_service.py",
        "群聊那条语音能力闸拆掉",
        """        if not self._client_supports_voice():
            # 与私聊那条同源：通道发不出语音时，别先合成一遍再判失败。
            return await self._group_text_fallback(""",
        """        if False:
            return await self._group_text_fallback(""",
    ),
    (
        "message_dispatcher.py",
        "派发层不再处理开放平台的文件附件",
        """            if await self.enrich_open_platform_attachments(
                message, label_defs=label_defs, raw_content=raw_content,
            ):""",
        """            if False:""",
    ),
    (
        "enrichment.py",
        "附件取用永远返回空（等于没接上）",
        """        files: list[dict] = []
        for attachment in message.get("attachments") or []:""",
        """        files: list[dict] = []
        for attachment in []:""",
    ),
    (
        "message_dispatcher.py",
        "派发层不再把「其实是图片的文件附件」提升成图片（真机上就是「这个文件打不开欸」）",
        """        if hasattr(enricher, "promote_image_attachments"):""",
        """        if False:""",
    ),
    (
        "enrichment.py",
        "文件渲染不再按内容认图（扩展名认不出就报二进制）",
        """                                if looks_like_image_bytes(payload):""",
        """                                if False:""",
    ),
    (
        "enrichment.py",
        "按内容认图的判据恒为否（等于没做）",
        """    head = bytes(payload[:_IMAGE_SNIFF_BYTES])""",
        """    return False
    head = bytes(payload[:_IMAGE_SNIFF_BYTES])""",
    ),
    (
        "_vendor/connection_onebot/qq_open_platform_media.py",
        "分片缺片也照合并（上传残缺文件还说成功）",
        """        if offset != len(payload):""",
        """        if False:""",
    ),
    (
        "_vendor/connection_onebot/qq_open_platform_media.py",
        "单聊图片误用群聊的上传入口（平台语义上不能跨用）",
        """        file_info = await self.upload_image(scope="users", owner_id=target, source=source)""",
        """        file_info = await self.upload_image(scope="groups", owner_id=target, source=source)""",
    ),
    (
        "_vendor/connection_onebot/qq_open_platform_media.py",
        "本地文件先走文档的分片、后走旧式直传（顺序反了）",
        """        for label, attempt in (
            ("直传", self._media_upload_legacy),
            ("分片", self._media_upload_chunked),
        ):""",
        """        for label, attempt in (
            ("分片", self._media_upload_chunked),
            ("直传", self._media_upload_legacy),
        ):""",
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

    code, _ = 0, None
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
        ok = code != 0 and restored
        results.append((f"{rel}: {label}", ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: exit={code}（期望非 0）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（三件事都在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 三件事的每处必要条件都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
