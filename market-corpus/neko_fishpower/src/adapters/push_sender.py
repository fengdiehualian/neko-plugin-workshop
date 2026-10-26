"""推送调度：respond（人设归宿主 LLM）与 blind（直出气泡）双通道。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any


class PushSender:
    """封装宿主推送通道。

    respond 模式（默认）：只投事实行，宿主按当前人设润色并触发语音；
    blind 模式：直出气泡，不触发语音。
    target_lanlan：多角色会话必须带（缺了宿主 _get_session_manager 返回 None，
    推送被丢弃）；单角色 fallback 兜住。
    """

    def __init__(self, plugin: Any, dry_run: bool = True) -> None:
        self.plugin = plugin
        self.dry_run = dry_run

    def _resolve_target_lanlan(self) -> str:
        """解析当前角色：payload → ctx → env → 宿主 config_manager（照 neko_warthunder）。"""
        try:
            ctx = getattr(self.plugin, "ctx", None)
            if ctx is not None:
                v = getattr(ctx, "_current_lanlan", None)
                if v:
                    return str(v)
                host_ctx = getattr(ctx, "_host_ctx", None)
                if host_ctx is not None:
                    v = getattr(host_ctx, "_current_lanlan", None)
                    if v:
                        return str(v)
        except Exception:
            pass
        import os
        for env in ("NEKO_TARGET_LANLAN", "NEKO_LANLAN_NAME", "NEKO_HER_NAME"):
            v = os.environ.get(env, "")
            if v:
                return v
        # 宿主配置管理器兜底：后台线程 ctx 拿不到 _current_lanlan 时用角色名
        try:
            from utils.config_manager import get_config_manager
            character_data = get_config_manager().get_character_data()
            if isinstance(character_data, tuple) and len(character_data) >= 2:
                v = str(character_data[1]).strip()[:80]
                if v:
                    return v
        except Exception:
            pass
        return ""

    async def push_fact(self, fact: str, target_lanlan: str = "") -> bool:
        """respond 模式：事实交给宿主，由当前人设决定措辞。"""
        if self.dry_run:
            self.plugin.logger.info("[dry_run] respond: %s", fact)
            return True
        try:
            result = self.plugin.push_message(
                source="neko_fishpower",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": fact}],
                priority=5,
                target_lanlan=target_lanlan or self._resolve_target_lanlan() or None,
            )
            ok = bool(result and result.get("submitted"))
            if not ok:
                self.plugin.logger.warning("respond 推送未提交: %s", str(result)[:120])
            return ok
        except Exception as exc:
            self.plugin.logger.warning("respond push failed: %s", exc)
            return False

    async def push_direct(self, text: str) -> bool:
        """blind 模式：直出聊天气泡，不触发语音。"""
        if self.dry_run:
            self.plugin.logger.info("[dry_run] blind: %s", text)
            return True
        try:
            result = self.plugin.push_message(
                source="neko_fishpower",
                visibility=["chat"],
                ai_behavior="blind",
                parts=[{"type": "text", "text": text}],
                priority=5,
                target_lanlan=self._resolve_target_lanlan() or None,
            )
            ok = bool(result and result.get("submitted"))
            if not ok:
                self.plugin.logger.warning("blind 推送未提交: %s", str(result)[:120])
            return ok
        except Exception as exc:
            self.plugin.logger.warning("blind push failed: %s", exc)
            return False

    async def push_image(self, image_bytes: bytes, caption: str = "") -> bool:
        """落盘图片到 data/static/cards/ 并以 markdown 直出。

        注意：当前无调用点；URL 依赖宿主静态托管映射，启用前需实测。
        """
        if self.dry_run:
            self.plugin.logger.info("[dry_run] image: %s", caption)
            return True
        try:
            cards = Path(self.plugin.data_path("static", "cards"))
            cards.mkdir(parents=True, exist_ok=True)
            name = f"card_{int(time.time() * 1000)}.png"
            (cards / name).write_bytes(image_bytes)
            url = f"/plugin/neko_fishpower/ui/static/cards/{name}"
            md = f"![img]({url})" + (f"\n{caption}" if caption else "")
            return await self.push_direct(md)
        except Exception:
            return await self.push_direct(caption or "（图片推送失败）")
