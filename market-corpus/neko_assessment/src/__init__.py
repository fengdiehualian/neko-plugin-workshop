"""心理测评室（neko_assessment）

心理测评 + 人格测评 + 趣味测评，集成在一个面板里：
- 人格/职业量表照搬**公开可复现**的开放工具原题（大五人格用 IPIP/Goldberg 公共领域题库、
  16 型用 OEJTS 1.2、职业兴趣用 O*NET Interest Profiler）；
- 情绪健康用公开筛查工具（PHQ-9、GAD-7、UCLA 简版）；
- 其余量表为本插件自编条目，已在来源里如实标注「自编」；
- 全部数据**只存本机 data/，不上传、不联网**；
- 只作自我了解与反思，**不是医学诊断**。
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional


def _mark(step: str) -> None:
    """每一步都往 %TEMP% 追加一行：进程被直接杀掉也能看出死在哪一步。"""
    import tempfile

    try:
        with open(Path(tempfile.gettempdir()) / "neko_assessment_step.txt", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {step}\n")
    except Exception:
        pass


def _dump_crash(stage: str) -> None:
    """宿主日志只有 start_plugin failed 时的救命稻草：把异常写进 %TEMP%。"""
    import tempfile
    import traceback

    try:
        crash = Path(tempfile.gettempdir()) / f"neko_assessment_crash_{stage}.txt"
        crash.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass

try:
    from plugin.sdk.plugin import (
        Err,
        NekoPluginBase,
        Ok,
        lifecycle,
        llm_tool,
        message,
        neko_plugin,
    )

    from . import _compat, _games, _tarot, _yui_link
    from ._engine import score
    from ._panel import PanelServer, find_open_port, guess_mime
    from ._scales import CATEGORIES, CRISIS_LINES, get_scale, list_scales
    from ._store import RecordStore, load_prefs, save_prefs
except Exception:
    _dump_crash("import")
    raise

_PLUGIN_ID = "neko_assessment"
_PANEL_PORT = 15800

# 她的记忆目录：留空 = 自动定位（问宿主 config_manager，再从安装位置上溯）。
# 别人装机位置各不相同，所以**不许写死绝对路径**。
_UI_DEFAULTS = {
    "yui_memory_dir": "",
}

_PREFS_DEFAULT = {
    "ui_font": "system",
    "ui_font_size": "m",
    "ui_trail": "on",
    "bg_mode": "default",
    "bg_dim": "medium",
}

# 默契测试：单条推送的硬上限（实测宿主的正文截断点约 200~300 字符，取保守值）
_COMPAT_ASK_MAX_CHARS = 150
_COMPAT_HINT = "回两个数字：①你自己选的 ②猜主人选的（编号从 1 开始）"
_COMPAT_HINT_SHORT = "回两个数字：①你自己 ②猜主人"


def _dedupe_texts(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """同一句话可能被多条通道读到；按原文去重，保持先后顺序。"""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for text, channel in pairs:
        if text and text not in seen:
            seen.add(text)
            out.append((text, channel))
    return out


_mark('before-class')


@neko_plugin
class AssessmentPlugin(NekoPluginBase):
    _mark('class-enter')
    def __init__(self, ctx):
        try:
            super().__init__(ctx)
            self.file_logger = self.enable_file_logging(log_level="INFO")
            self.logger = self.file_logger
            self.data_dir = Path(self.data_path())
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._panel_port: int = _PANEL_PORT
            self._panel_server: Optional[PanelServer] = None
            self._prefs: Optional[dict] = None
            self._store: Optional[RecordStore] = None
            # 塔罗：状态文件独立（data/tarot.json），解读任务在后台线程跑
            self._tarot_lock = threading.Lock()
            self._tarot_job: dict[str, Any] = {"status": "idle", "draw_id": "", "text": ""}
            # 解压小游戏：切水果记录 + 电子板本地音源（与测评记录完全分开）
            self._games: Optional[_games.GameStore] = None
            # 默契测试：回合状态机 + YUI 后台作答任务
            self._compat: Optional[_compat.CompatStore] = None
            self._compat_job: dict[str, Any] = {"status": "idle", "round_id": "", "source": ""}
            # 她逐题的原话（按回合存，揭晓时给面板看——这是"真的她在答"的证据）
            self._compat_reply: dict[str, dict] = {}
            # 逐题面谈的实时进度（面板要显示"她正在答第几题"，以及每题的原文/结果）
            self._compat_progress: dict[str, dict] = {}
            # 主读回通道（宿主实时总线 ctx.bus.memory.get_sync）
            self._compat_bus_cache: Any = None
            # 次通道（宿主记忆服务对话流）：None=还没建，False=建不起来
            self._compat_feed: Any = None
            # 备用通道：她的对话库；None=还没建，False=定位不到，否则是 YuiDialog
            self._yui_mem: Any = None
            self.yui_memory_dir: str = _UI_DEFAULTS["yui_memory_dir"]
            # 后台线程（逐题面谈）的停止信号；shutdown 时置位
            self._stop_event = threading.Event()
            # 收到过多少次 chat 事件（只记前 40 条日志，用来判断能不能收到她的话）
            self._chat_events: int = 0
            # 我们自己推出去的原文。**它绝不能当成她的回答**——实时总线里
            # `MESSAGE_PUSH` 这一类就是我们自己的推送，踩过：面板把她答的题显示成
            # 我推的题目原文，「解析成选项 3/4」其实是从我题目的选项编号里抠的。
            self._compat_pushed: set[str] = set()
        except Exception:
            _dump_crash("init")
            raise
    # ── 路径 ─────────────────────────────────────────────────
    def _data_dir(self) -> Path:
        return self.data_dir

    def _records_path(self) -> Path:
        return self._data_dir() / "records.json"

    def _prefs_path(self) -> Path:
        return self._data_dir() / "panel_prefs.json"

    def _bg_dir(self) -> Path:
        path = self._data_dir() / "backgrounds"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return path

    def _bg_file(self) -> Optional[Path]:
        saved = self._prefs if isinstance(self._prefs, dict) else {}
        name = str(saved.get("bg_file") or "").strip()
        if name:
            candidate = (self._bg_dir() / Path(name).name).resolve()
            if candidate.is_file():
                return candidate
        for suffix in (".png", ".jpg", ".webp", ".gif"):
            candidate = self._bg_dir() / f"custom{suffix}"
            if candidate.is_file():
                return candidate
        return None

    # ── 生命周期 ─────────────────────────────────────────────
    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            self._store = RecordStore(self._records_path(), logger=self.logger)
            self._games = _games.GameStore(self._data_dir() / "games.json", logger=self.logger)
            self._compat = _compat.CompatStore(self._data_dir() / "compat.json", logger=self.logger)
            self._prefs = load_prefs(self._prefs_path())
            if isinstance(self._prefs, dict):
                self.yui_memory_dir = str(self._prefs.get("yui_memory_dir") or "").strip()
            self._start_panel()
            count = len(list_scales())
            self.logger.info("[assessment] 心理测评室已就绪：{} 份量表", count)
            return Ok("started")
        except Exception:
            self.logger.warning("[assessment] startup 异常：{}", traceback.format_exc())
            _dump_crash("startup")
            return Err("startup failed")

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        if self._panel_server:
            self._panel_server.stop()
            self._panel_server = None
        try:
            self._stop_event.set()          # 让等她作答的后台线程退出
        except Exception:
            pass
        return Ok("stopped")

    # ── 面板 ─────────────────────────────────────────────────
    def _panel_html(self) -> str:
        page = Path(__file__).parent / "static" / "index.html"
        try:
            return page.read_text(encoding="utf-8")
        except Exception:
            return "<h1>面板页缺失（static/index.html）</h1>"

    def _start_panel(self) -> None:
        endpoints = {
            ("GET", "/api/scales"): self._api_scales,
            ("POST", "/api/scale"): self._api_scale,
            ("POST", "/api/submit"): self._api_submit,
            ("GET", "/api/records"): self._api_records,
            ("POST", "/api/records"): self._api_records,
            ("POST", "/api/records/delete"): self._api_delete,
            ("POST", "/api/records/clear"): self._api_clear,
            ("POST", "/api/history"): self._api_history,
            ("GET", "/api/prefs"): self._api_prefs,
            ("POST", "/api/prefs"): self._api_prefs,
            ("GET", "/api/background"): self._api_background,
            ("POST", "/api/background"): self._api_background,
            ("POST", "/api/export"): self._api_export,
            ("POST", "/api/card"): self._api_card,
            ("GET", "/api/tarot"): self._api_tarot_info,
            ("POST", "/api/tarot/draw"): self._api_tarot_draw,
            ("POST", "/api/tarot/interpret"): self._api_tarot_interpret,
            ("GET", "/api/game"): self._api_game,
            ("POST", "/api/game"): self._api_game,
            ("GET", "/api/compat"): self._api_compat,
            ("POST", "/api/compat"): self._api_compat,
        }
        port = find_open_port(self._panel_port)
        server = PanelServer(
            port,
            self._panel_html,
            endpoints,
            static_dir=Path(__file__).parent / "static",
            asset_provider=self._bg_asset_for,
        )
        if server.start():
            self._panel_server = server
            self._panel_port = port
            self.logger.info("[assessment] 面板已启动: http://127.0.0.1:{}", port)
            try:
                self.register_static_ui("static")
            except Exception as exc:
                self.logger.warning("[assessment] static UI 注册失败: {}", exc)
        else:
            self.logger.warning("[assessment] 面板启动失败")

    # ── 接口 ─────────────────────────────────────────────────
    def _api_scales(self, _body: dict) -> dict:
        counts = self._store.counts() if self._store else {}
        rows = list_scales()
        for row in rows:
            row["done"] = counts.get(row["id"], 0)
            scale = get_scale(row["id"]) or {}
            row["bank_size"] = len(scale.get("bank") or scale.get("items") or [])
            row["default_items"] = int(scale.get("default_items") or row["count"])
            row["min_items"] = int(scale.get("min_items") or row["count"])
            row["fixed"] = bool(scale.get("fixed"))
            row["official_count"] = int(scale.get("official_count") or 0)
        return {"ok": True, "scales": rows, "categories": CATEGORIES}

    def _api_scale(self, body: dict) -> dict:
        """取本次要答的题：从题库里随机抽 count 题（官方量表固定题量，不抽样）。"""
        payload = body or {}
        scale_id = str(payload.get("id") or "").strip()
        scale = get_scale(scale_id)
        if scale is None:
            return {"ok": False, "error": f"没有这份量表：{scale_id}"}

        bank = list(scale.get("bank") or scale["items"])
        total = len(bank)
        fixed = bool(scale.get("fixed"))
        try:
            want = int(payload.get("count") or 0)
        except Exception:
            want = 0
        official_count = int(scale.get("official_count") or 0)
        official = False
        if official_count and want == official_count:
            official = True                       # 官方题数：按原量表顺序出官方原题
            ids = list(range(official_count))
        elif want <= 0 or want >= total:
            ids = list(range(total))
        else:
            want = max(1, min(total, want))   # 用户自己定题数就得听用户的，只做上下界保护
            ids = sorted(random.sample(range(total), want))

        return {
            "ok": True,
            "scale": {
                "id": scale["id"],
                "name": scale["name"],
                "kind": scale.get("kind"),
                "intro": scale["intro"],
                "source": scale["source"],
                "options": scale.get("options"),
                "dimensions": scale.get("dimensions", []),
                "dichotomies": scale.get("dichotomies", []),
                "items": [bank[i] for i in ids],
                "ids": ids,
                "count": len(ids),
                "bank_size": total,
                "min_items": int(scale.get("min_items") or 6),
                "default_items": int(scale.get("default_items") or len(ids)),
                "fixed": fixed,
                "official_count": official_count,
                "official": official,
                "method": scale.get("method", ""),
            },
        }

    def _api_submit(self, body: dict) -> dict:
        payload = body or {}
        scale_id = str(payload.get("scale_id") or "").strip()
        answers = payload.get("answers") or []
        ids = payload.get("ids") or []
        if not isinstance(answers, list):
            return {"ok": False, "error": "答案格式不对。"}
        scale = get_scale(scale_id)
        items = None
        if scale and isinstance(ids, list) and ids:
            bank = list(scale.get("bank") or scale["items"])
            try:
                items = [bank[int(i)] for i in ids if 0 <= int(i) < len(bank)]
            except Exception:
                items = None
        official = False
        if scale and items is not None:
            official_count = int(scale.get("official_count") or 0)
            official = bool(official_count) and len(items) == official_count
        result = score(scale_id, answers, items=items, official=official) if items else score(scale_id, answers)
        if not result.get("ok"):
            return result
        scale = get_scale(scale_id) or {}
        record = None
        if self._store:
            record = self._store.add(
                scale_id, scale.get("name", scale_id), scale.get("kind", ""), result, answers
            )
        result["record"] = {k: v for k, v in (record or {}).items() if k != "answers"}
        result["crisis_lines"] = CRISIS_LINES if result.get("risk") else []
        return result

    def _api_records(self, body: dict) -> dict:
        if not self._store:
            return {"ok": True, "records": []}
        scale_id = str((body or {}).get("scale_id") or "").strip()
        rows = self._store.list()
        if scale_id:
            rows = [row for row in rows if row.get("scale_id") == scale_id]
        rows = sorted(rows, key=lambda row: row.get("ts", 0), reverse=True)
        return {"ok": True, "records": rows}

    def _api_delete(self, body: dict) -> dict:
        if not self._store:
            return {"ok": False, "error": "存储未就绪。"}
        record_id = str((body or {}).get("id") or "").strip()
        return {"ok": self._store.delete(record_id)}

    def _api_clear(self, _body: dict) -> dict:
        if not self._store:
            return {"ok": False, "error": "存储未就绪。"}
        return {"ok": True, "removed": self._store.clear()}

    def _api_history(self, body: dict) -> dict:
        if not self._store:
            return {"ok": True, "history": []}
        scale_id = str((body or {}).get("scale_id") or "").strip()
        return {"ok": True, "history": self._store.history_for(scale_id)}

    def _api_card(self, body: dict) -> dict:
        """把面板生成的分享卡（PNG base64）存到磁盘——内嵌 webview 里 <a download> 经常被吞掉。"""
        import base64
        import re as _re

        payload = body or {}
        raw = str(payload.get("image") or "").strip()
        if not raw:
            return {"ok": False, "error": "没有收到图片数据。"}
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        try:
            blob = base64.b64decode(raw)
        except Exception:
            return {"ok": False, "error": "图片数据解不开。"}
        if not blob:
            return {"ok": False, "error": "图片是空的。"}
        if len(blob) > 12 * 1024 * 1024:
            return {"ok": False, "error": "图片太大。"}

        name = _re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(payload.get("name") or "result"))[:40]
        stamp = time.strftime("%Y%m%d-%H%M%S")

        # 首选：用户「图片」文件夹（方便直接发出去）；不行就退回插件 data/
        saved_dir = None
        try:
            home = Path.home()
            for candidate in (home / "Pictures", home / "图片", home / "OneDrive" / "Pictures"):
                if candidate.is_dir():
                    saved_dir = candidate / "心理测评室"
                    saved_dir.mkdir(parents=True, exist_ok=True)
                    break
        except Exception:
            saved_dir = None
        if saved_dir is None:
            saved_dir = self._data_dir() / "share_cards"
            try:
                saved_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                return {"ok": False, "error": f"创建目录失败：{exc}"}

        target = saved_dir / f"{stamp}-{name}.png"
        try:
            target.write_bytes(blob)
        except Exception as exc:
            return {"ok": False, "error": f"写入失败：{exc}"}
        self.logger.info("[assessment] 分享卡已保存：{}", target)
        return {"ok": True, "path": str(target), "dir": str(saved_dir), "bytes": len(blob)}

    def _api_export(self, _body: dict) -> dict:
        """导出全部记录（纯文本，便于自己留存或交给专业人士看）。"""
        if not self._store:
            return {"ok": False, "error": "存储未就绪。"}
        rows = self._store.list()
        lines = ["# 心理测评室 · 记录导出", f"# 生成时间：{time.strftime('%Y-%m-%d %H:%M')}", ""]
        for row in rows:
            lines.append(f"[{row.get('time')}] {row.get('name')}")
            if row.get("level"):
                lines.append(f"  等级：{row['level']}（总分 {row.get('total')}）")
            if row.get("type") and isinstance(row["type"], dict):
                lines.append(f"  类型：{row['type'].get('name')}（{row['type'].get('code')}）")
            for dim in row.get("dims", []):
                lines.append(f"  - {dim.get('key')}: {dim.get('score')}（{dim.get('percent')}%）")
            lines.append("")
        lines.append("# 本结果仅供自我了解，不构成医学诊断。")
        return {"ok": True, "text": "\n".join(lines), "count": len(rows)}

    def _prefs_payload(self) -> dict:
        merged = dict(_PREFS_DEFAULT)
        saved = self._prefs if isinstance(self._prefs, dict) else {}
        for key in _PREFS_DEFAULT:
            value = str(saved.get(key) or "").strip()
            if value:
                merged[key] = value[:32]
        return merged

    def _api_prefs(self, body: dict) -> dict:
        prefs = self._prefs_payload()
        payload = body if isinstance(body, dict) else {}
        changed = False
        for key in _PREFS_DEFAULT:
            value = str(payload.get(key) or "").strip()
            if value and value != prefs[key]:
                prefs[key] = value[:32]
                changed = True
        if changed:
            raw = dict(self._prefs) if isinstance(self._prefs, dict) else {}
            raw.update(prefs)
            self._save_prefs(raw)
        # 她的记忆目录：可以填、也可以清空（留空 = 自动定位），
        # 所以不能走上面那套「非空且截断到 32 字」的通用逻辑
        if "yui_memory_dir" in payload:
            wanted = str(payload.get("yui_memory_dir") or "").strip()[:260]
            if wanted != self.yui_memory_dir:
                self.yui_memory_dir = wanted
                raw = dict(self._prefs) if isinstance(self._prefs, dict) else {}
                raw["yui_memory_dir"] = wanted
                self._save_prefs(raw)
                self._yui_mem = None          # 改了路径就重新定位
        background = self._background_state()
        return {"ok": True, "prefs": prefs, "background": background,
                "yui_memory_dir": self.yui_memory_dir, "memory": self._yui_stats()}

    def _background_state(self) -> dict:
        prefs = self._prefs_payload()
        mode = prefs.get("bg_mode", "default")
        if mode not in ("default", "custom", "plain"):
            mode = "default"
        custom = self._bg_file()
        if mode == "custom" and custom is None:
            mode = "default"          # 图没了就退回默认，别留一片空白
        return {
            "mode": mode,
            "dim": prefs.get("bg_dim", "medium"),
            "has_custom": custom is not None,
            "custom_bytes": custom.stat().st_size if custom is not None else 0,
            "custom_name": custom.name if custom is not None else "",
            "custom_path": "/bg/custom",
        }

    def _save_prefs(self, prefs: dict) -> None:
        self._prefs = prefs
        save_prefs(self._prefs_path(), prefs)

    def _bg_asset(self) -> Optional[tuple[bytes, str]]:
        path = self._bg_file()
        if path is None:
            return None
        try:
            return path.read_bytes(), guess_mime(path.name)
        except Exception:
            return None

    def _bg_asset_for(self, rel: str) -> Optional[tuple[bytes, str]]:
        rel = (rel or "").strip().lstrip("/").split("?", 1)[0]
        if rel in ("bg/custom", "bg/custom.jpg", "bg/custom.png"):
            return self._bg_asset()
        if rel.startswith("pad-audio/"):            # 用户自备的电子板音源（非商业自用）
            return _games.pad_audio_asset(self.data_dir, rel.split("/", 1)[1])
        return None

    _BG_MAX_BYTES = 8 * 1024 * 1024
    _BG_MIME_EXT = {
        "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/webp": ".webp", "image/gif": ".gif",
    }

    def _api_background(self, body: dict) -> dict:
        """上传 / 切换 / 恢复自定义背景。图存 data/backgrounds/，不进安装包。"""
        import base64

        payload = body if isinstance(body, dict) else {}
        action = str(payload.get("action") or "").strip() or "mode"
        prefs = dict(self._prefs) if isinstance(self._prefs, dict) else {}
        if action == "upload":
            raw = str(payload.get("image_base64") or payload.get("image") or "").strip()
            if not raw:
                return {"ok": False, "error": "没有收到图片数据。", "background": self._background_state()}
            if raw.startswith("data:"):
                head, _, encoded = raw.partition(",")
                mime = head[5:].split(";")[0].strip().lower()
            else:
                encoded, mime = raw, "image/png"
            ext = self._BG_MIME_EXT.get(mime)
            if not ext:
                return {"ok": False, "error": f"不支持的格式：{mime or '未知'}", "background": self._background_state()}
            try:
                blob = base64.b64decode(encoded or "", validate=False)
            except Exception:
                return {"ok": False, "error": "图片数据解不开。", "background": self._background_state()}
            if not blob:
                return {"ok": False, "error": "图片是空的。", "background": self._background_state()}
            if len(blob) > self._BG_MAX_BYTES:
                return {"ok": False, "error": f"图片超过 {self._BG_MAX_BYTES // 1048576}MB 限制。", "background": self._background_state()}
            for suffix in (".png", ".jpg", ".webp", ".gif"):
                stale = self._bg_dir() / f"custom{suffix}"
                if stale.is_file():
                    try:
                        stale.unlink()
                    except Exception:
                        pass
            target = self._bg_dir() / f"custom{ext}"
            try:
                target.write_bytes(blob)
            except Exception as exc:
                return {"ok": False, "error": str(exc), "background": self._background_state()}
            prefs["bg_mode"] = "custom"
            prefs["bg_file"] = target.name
            self._save_prefs(prefs)
            return {"ok": True, "message": "背景已换成你上传的图。", "background": self._background_state()}
        if action == "reset":
            prefs["bg_mode"] = "default"
            self._save_prefs(prefs)
            return {"ok": True, "message": "已恢复默认背景。", "background": self._background_state()}
        mode = str(payload.get("mode") or "").strip() or str(prefs.get("bg_mode") or "default")
        if mode not in ("default", "custom", "plain"):
            return {"ok": False, "error": f"未知模式：{mode}", "background": self._background_state()}
        if mode == "custom" and self._bg_file() is None:
            return {"ok": False, "error": "还没上传过背景图。", "background": self._background_state()}
        prefs["bg_mode"] = mode
        dim = str(payload.get("dim") or "").strip()
        if dim in ("light", "medium", "strong"):
            prefs["bg_dim"] = dim
        self._save_prefs(prefs)
        return {"ok": True, "background": self._background_state()}

    # ── 解压小游戏（从学习辅助猫娘移植；与测评记录完全分开）──────
    def _api_game(self, body: dict) -> dict:
        """切水果：取规则 / 取记录 / 交成绩；电子板：扫本地音源 / 导入音源。"""
        if self._games is None:
            return {"ok": False, "error": "游戏存储未就绪。"}
        payload = body if isinstance(body, dict) else {}
        action = str(payload.get("action") or "state").strip() or "state"
        game = str(payload.get("game") or _games.GAME_FRUIT).strip() or _games.GAME_FRUIT
        if action == "config":
            config = self._games.config()
            config["local_audio"] = _games.scan_pad_audio(self.data_dir)
            config["local_audio_dir"] = f"data/{_games.PAD_AUDIO_DIR}"
            return {"ok": True, "config": config, "state": self._games.state(game)}
        if action in ("import-audio", "import_audio"):
            files = payload.get("files")
            if not isinstance(files, list) or not files:
                return {"ok": False, "error": "没有收到音源文件。"}
            saved, skipped = self._import_pad_audio(files)
            return {
                "ok": True,
                "saved": saved,
                "skipped": skipped,
                "files": _games.scan_pad_audio(self.data_dir),
                "dir": f"data/{_games.PAD_AUDIO_DIR}",
            }
        if action in ("pad-audio", "pad_audio"):
            return {
                "ok": True,
                "files": _games.scan_pad_audio(self.data_dir),
                "dir": f"data/{_games.PAD_AUDIO_DIR}",
                "note": (
                    "把你自己有的音源（mp3/ogg/wav）放进插件 data/"
                    f"{_games.PAD_AUDIO_DIR}/ 就会自动用上；这些文件不会被打进安装包。"
                ),
            }
        if action == "submit":
            run = payload.get("run")
            if not isinstance(run, dict):
                return {"ok": False, "error": "没有收到成绩数据。", "state": self._games.state(game)}
            return self._games.submit(run, game)
        return {"ok": True, "state": self._games.state(game), "config": self._games.config()}

    _AUDIO_MAX_BYTES = 8 * 1024 * 1024

    def _import_pad_audio(self, files: list) -> tuple[int, int]:
        """把用户在面板里选的本机音源写进 data/mikutap_audio/。

        文件是用户自己在自己机器上选的，插件只负责存到自己的数据目录，
        发行包里不含任何音频。单文件上限 8MB、总数上限 120 个，避免误选整个音乐库。
        """
        import base64

        target_dir = _games.pad_audio_dir(self.data_dir)
        saved = 0
        skipped = 0
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.logger.warning("[assessment] 音源目录创建失败：{}", exc)
            return 0, len(files)
        for item in files[:120]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            name = Path(str(item.get("name") or "")).name
            if not name or Path(name).suffix.lower() not in _games.AUDIO_EXTS:
                skipped += 1
                continue
            raw = str(item.get("data") or item.get("data_base64") or "").strip()
            if not raw:
                skipped += 1
                continue
            _, _, encoded = raw.partition(",")
            try:
                blob = base64.b64decode(encoded or raw, validate=False)
            except Exception:
                skipped += 1
                continue
            if not blob or len(blob) > self._AUDIO_MAX_BYTES:
                skipped += 1
                continue
            try:
                (target_dir / name).write_bytes(blob)
                saved += 1
            except Exception:
                skipped += 1
        self.logger.info("[assessment] 本地音源导入：成功 {}，跳过 {}", saved, skipped)
        return saved, skipped

    # ── 默契测试：请她本人逐题作答，再把她的回答读回来 ────────────────
    #
    # 为什么不是「把她的记忆注入 prompt、让模型替她答」：
    # 那样答话的是**插件**，只是套了她的人设和记忆——她既不知道自己在答题，
    # 用的也不是她自己真实的对话上下文。所以走宿主本来就会的能力：
    #   插件把题目推进对话（ai_behavior="respond"）→ 宿主用**她本人**
    #   （人设 / 记忆 / 上下文）生成回复，屏幕上就是她在说话 → 插件读回来解析。
    #
    # ⚠ 一条血泪：**整卷一次推过去会被截断**
    # 宿主对单条 `push_message` 的正文有 token 上限（`AGENT_CALLBACK_TEXT_MAX_TOKENS`
    # 一族，值编译进主程序，磁盘上取不到）。实测把 700 字符的 10 题整卷推过去，
    # 她亲口说「第三道你选项都没写完」——只拿到 Q1、Q2 全文和 Q3 的题干。
    # 所以**改成一题一条**：每题一条 ≤ _COMPAT_ASK_MAX_CHARS 的短文，等她答完再发
    # 下一题。长度由构造保证，不去赌宿主的上限到底是多少。
    #
    # ⚠ 第二条血泪：她**不会**吐 JSON。别让她交 `{"own":[...]}`，让她用自己的话
    # 说两个编号，`_yui_link.parse_picks` 认数字/字母/选项原文；解析不出就算没答上。
    #
    # ⚠ 第三条血泪：读回**不能按时间戳**。`time_indexed.db` 是快照式写入，同一批
    # 新增的多行**共享同一个时间戳**，`timestamp > since` 会读到用户那条、读漏她那条。
    # 主通道改走宿主记忆服务的实时对话流，备用通道按自增 `id` 锚点读。
    _COMPAT_ASK_MAX_CHARS = _COMPAT_ASK_MAX_CHARS   # 单条推送的硬上限（见模块级注释）
    # ── 三个时间刻度，**故意分开**（合在一起会互相绑死）───────────────
    # ① 每题「还算在等她」的窗口：用户要求至少 300 秒。在这段时间内，面板不许
    #    把这一题写成"没答上"——她的回答可能只是还没落盘。
    _COMPAT_PER_Q_WAIT = 300.0
    # ② 读回通道通不通的**探针**：第一题只等这么久。通了之后每题就按 ① 给的
    #    300 秒走（真正实现"她答完才发下一题"）；不通就退化成 ③ 的快节奏，
    #    免得 300 秒 × 10 题 = 50 分钟白等。
    _COMPAT_PROBE_WAIT = 60.0
    # 实时总线（内存、不落盘）优先；它没给就退回对话流/对话库。
    _COMPAT_USE_BUS_FOR_ANSWERS = True
    # 对话流也是答案来源之一。规则很简单（用户定的）：
    #   **题目发出去之后她说的第一条，就是这一题的答案。**
    # 所以不挑通道、不比对时间戳、不看题号——哪条通道先把她那句话露出来就用哪条。
    _COMPAT_USE_FEED_FOR_ANSWERS = True
    # ③ 退化成快节奏后，问下一题的间隔（只用来避免"连续推送攒成一条"）。
    _COMPAT_GAP_SECONDS = 20.0
    # ③ 整轮窗口：问完之后一直收到这里为止。落盘可能晚好几分钟，也可能要等
    #    用户说句话才触发，所以给足一小时；期间面板随时可以揭晓。
    _COMPAT_ROUND_WINDOW_SECONDS = 3600.0
    _COMPAT_WATCH_POLL_SECONDS = 15.0
    _COMPAT_POLL_SECONDS = 1.5
    # 面板上手动点「补收」时后台跑多久
    _COMPAT_HARVEST_SECONDS = 300.0

    # ── 读她的两条通道 ────────────────────────────────────────
    def _char_name(self) -> str:
        """她叫什么——问宿主，问不到用兜底。"""
        try:
            name = _yui_link.host_character_name()
        except Exception:
            name = ""
        return name or "YUI"

    def _yui_feed(self) -> Any:
        """主通道：宿主记忆服务的实时对话流（懒建，连不上返回 None）。"""
        if self._compat_feed is None:
            try:
                self._compat_feed = _yui_link.YuiFeed(self._char_name())
            except Exception as exc:
                self.logger.warning("[assessment] 建对话流通道失败：{}", exc)
                self._compat_feed = False
        return self._compat_feed or None

    def _yui_dialog(self) -> Any:
        """备用通道：她那本对话库（按自增 id 锚点读）。"""
        if self._yui_mem is None:
            try:
                explicit = str(getattr(self, "yui_memory_dir", "") or "").strip()
                path, source = _yui_link.resolve_yui_dir(explicit, [str(self._data_dir())])
                self._yui_mem = _yui_link.YuiDialog(path, source)
            except Exception as exc:
                self.logger.warning("[assessment] 定位她的对话库失败：{}", exc)
                self._yui_mem = False
        return self._yui_mem or None

    def _yui_stats(self) -> dict:
        """面板用的诊断：三条通道各自能不能用，如实报，不美化。"""
        info: dict[str, Any] = {}
        bus = self._yui_bus()
        if bus is not None:
            try:
                info["bus"] = bus.stats()
            except Exception as exc:
                info["bus"] = {"available": False, "reason": str(exc)}
        else:
            info["bus"] = {"available": False, "reason": "通道没建起来"}
        feed = self._yui_feed()
        if feed is not None:
            try:
                info = dict(info, **feed.stats())
            except Exception as exc:
                info.update({"available": False, "reason": str(exc)})
        else:
            info.update({"available": False, "reason": "通道没建起来"})
        dlg = self._yui_dialog()
        if dlg is not None:
            try:
                db = dlg.stats()
            except Exception as exc:
                db = {"available": False, "reason": str(exc)}
        else:
            db = {"available": False, "reason": "没定位到她的记忆目录"}
        info["db"] = db
        info["db_available"] = bool(db.get("available"))
        for key in ("yui_dir", "db_path"):
            if db.get(key):
                info[key] = db[key]
        info["character"] = self._char_name()
        return info

    # ── 出题：一题一条短文，长度由构造保证 ────────────────────
    def _compat_answer_hint(self) -> str:
        return _COMPAT_HINT

    def _compat_ask_text(self, index: int, total: int, question_id: str,
                         limit: int | None = None) -> str:
        """第 ``index`` 题的推送正文。**构造上保证不超过上限**，超了就逐级压缩。

        ``limit`` 只留给测试显式指定；正常走类的 ``_COMPAT_ASK_MAX_CHARS``。
        """
        limit = int(limit or getattr(self, "_COMPAT_ASK_MAX_CHARS", 0)
                    or _COMPAT_ASK_MAX_CHARS)
        pub = _compat.question_public(question_id) or {}
        options = [str(o) for o in (pub.get("options") or [])]
        text = str(pub.get("text") or "")
        lead = "" if int(index) > 1 else "主人要和你玩默契测试，要你亲自答喵。\n"
        head = f"{lead}默契测试 {index}/{total}：{text}"
        labels_full = [f"{j + 1} {o}" for j, o in enumerate(options)]
        labels_short = [
            f"{j + 1} {(o[:6] + '…') if len(o) > 7 else o}" for j, o in enumerate(options)
        ]
        for labels, hint in (
            (labels_full, _COMPAT_HINT),
            (labels_short, _COMPAT_HINT),
            (labels_short, _COMPAT_HINT_SHORT),
        ):
            out = "\n".join([head, "　".join(labels), hint])
            if len(out) <= limit:
                return out
        tail = "\n".join(["", "　".join(labels_short), _COMPAT_HINT_SHORT])
        short_head = f"默契测试 {index}/{total}："
        keep = max(6, limit - len(tail) - len(short_head))
        return "\n".join([short_head + text[:keep], "　".join(labels_short), _COMPAT_HINT_SHORT])

    def _compat_push_text(self, text: str, description: str) -> None:
        """把一段话推进对话，请她本人回应。失败要抛出去，让面板如实显示。"""
        if len(text) > self._COMPAT_ASK_MAX_CHARS * 3:
            raise ValueError(f"推送正文过长（{len(text)} 字符），拒绝发送")
        try:
            self._compat_pushed.add(self._compat_norm(text))
        except Exception:
            pass
        result = self.ctx.push_message(
            source=_PLUGIN_ID,
            visibility=["chat"],
            ai_behavior="respond",
            parts=[{"type": "text", "text": text}],
            priority=5,
            metadata={"description": description},
        )
        if asyncio.iscoroutine(result):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(result)
            finally:
                loop.close()

    # ── 读回：实时总线 → 对话流 → 对话库 ──────────────────────
    def _yui_bus(self) -> Any:
        """实时总线：`ctx.bus.memory.get_sync`。

        **这是唯一不依赖落盘的通道**，官方插件读"用户刚说了什么"用的就是它。
        宿主的对话落盘是**懒触发**的（要等用户发言或空闲维护才快照），所以对话流
        和对话库都会滞后到没法用来"等她回答"——上一版就栽在这上面：她的回答
        20:36 就说了，而库里到 20:38 还是没有。
        """
        if self._compat_bus_cache is None:
            try:
                self._compat_bus_cache = _yui_link.YuiBus(self.ctx, self._char_name())
            except Exception as exc:
                self.logger.warning("[assessment] 建实时总线通道失败：{}", exc)
                self._compat_bus_cache = False
        return self._compat_bus_cache or None

    def _compat_cursors(self, *, mark_bus: bool = True) -> tuple[dict, int]:
        """推题**之前**记下游标。总线把存量吃掉；对话流/对话库各记一份。"""
        bus = self._yui_bus()
        if bus is not None and mark_bus:
            try:
                bus.mark_seen()
            except Exception:
                pass
        snap: dict = {}
        feed = self._yui_feed()
        if feed is not None:
            try:
                snap = feed.snapshot()
            except Exception:
                snap = {}
        mark = 0
        dlg = self._yui_dialog()
        if dlg is not None and dlg.available:
            try:
                mark = dlg.mark()
            except Exception:
                mark = 0
        return snap, mark

    @staticmethod
    def _compat_norm(text: str) -> str:
        return " ".join(str(text or "").split())

    def _compat_is_our_push(self, text: str) -> bool:
        """这段文字是不是**我们自己推出去的**。

        实时总线里有一类 `MESSAGE_PUSH`（往对话里推的消息流），我们自己的题目就在
        里面，而且题目正文自带「1 xxx 2 yyy 3 zzz 4 www」这种选项编号——解析器会
        从里面抠出"3 4"当成她的回答。所以两道闸门：原文比对 + 题目标记。
        """
        norm = self._compat_norm(text)
        if not norm:
            return True
        if norm in getattr(self, "_compat_pushed", ()):
            return True
        return "默契测试" in norm and ("/10" in norm or "/20" in norm)

    def _compat_new_texts(self, snap: dict, mark: int) -> list[tuple[str, str]]:
        """她相对游标新说的话：``[(原文, 通道)]``。**总线优先**，它不滞后。"""
        out: list[tuple[str, str]] = []
        out = self._compat_bus_texts() if self._COMPAT_USE_BUS_FOR_ANSWERS else []
        if not out and self._COMPAT_USE_FEED_FOR_ANSWERS:
            feed = self._yui_feed()
            if feed is not None:
                try:
                    out.extend((t, "对话流") for t in feed.new_her_turns(snap))
                except Exception:
                    pass
        if not out:
            dlg = self._yui_dialog()
            if dlg is not None and dlg.available:
                try:
                    out.extend((row["text"], "对话库") for row in dlg.new_replies(mark))
                except Exception:
                    pass
        return [(t, c) for t, c in _dedupe_texts(out) if not self._compat_is_our_push(t)]

    def _compat_bus_texts(self) -> list[tuple[str, str]]:
        """从实时总线拿她新说的话（不带任何时间过滤）。"""
        bus = self._yui_bus()
        if bus is None:
            return []
        try:
            recs = bus.new_records()
        except Exception:
            return []
        return [(rec.get("text") or "", "实时总线") for rec in recs]

    def _compat_all_new_texts(self, prog: dict) -> list[tuple[str, str]]:
        """**这一轮开始以来**她说的所有话（不只当前这一题），给"补收"用。"""
        out: list[tuple[str, str]] = []
        bus = self._yui_bus()
        if bus is not None:
            try:
                for rec in bus.round_records():
                    out.append((rec.get("text") or "", "实时总线"))
            except Exception:
                pass
        start = prog.get("start") or {}
        if self._COMPAT_USE_FEED_FOR_ANSWERS:
            feed = self._yui_feed()
            if feed is not None:
                try:
                    out.extend((t, "对话流")
                               for t in feed.new_her_turns(start.get("snap") or {}))
                except Exception:
                    pass
        dlg = self._yui_dialog()
        if dlg is not None and dlg.available:
            try:
                out.extend((row["text"], "对话库")
                           for row in dlg.new_replies(int(start.get("mark") or 0)))
            except Exception:
                pass
        return [(t, c) for t, c in _dedupe_texts(out) if not self._compat_is_our_push(t)]

    def _compat_log_poll(self, fresh: list[tuple[str, str]]) -> None:
        """把"总线这一轮到底给了什么"记一笔（限速 ~12 秒一次）。

        排查"她答了却读不到"时，这一行直接说明：她的新话到没到、什么时候到、
        走的哪条通道。没有它就只能猜——前面几轮全是这么浪费掉的。
        """
        now = time.time()
        if now - float(getattr(self, "_compat_poll_log_at", 0.0) or 0.0) < 12.0:
            return
        self._compat_poll_log_at = now
        bus = self._yui_bus()
        if bus is None:
            self.logger.info("[assessment] 总线轮询：通道没建起来；新话 {} 条", len(fresh))
            return
        try:
            recs = bus.records(40)
        except Exception as exc:
            self.logger.info("[assessment] 总线轮询失败：{}", exc)
            return
        by_space: dict[str, list[dict]] = {}
        for rec in recs:
            by_space.setdefault(rec.get("space") or "?", []).append(rec)
        parts = []
        for space, rows in list(by_space.items())[:4]:
            last = rows[-1]
            ts = _yui_link.parse_ts(last.get("ts"))
            when = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "无时间戳"
            parts.append("%s×%d 末条(%s,%s)「%s」" % (
                space, len(rows), last.get("kind") or last.get("role") or "?",
                when, str(last.get("text") or "")[:24].replace("\n", " ")))
        self.logger.info("[assessment] 总线轮询 → {}｜本轮新话 {} 条 {}",
                         "；".join(parts) or "（空）", len(fresh),
                         [t[:16] for t, _ in fresh[:2]])

    @staticmethod
    def _compat_pick_from(texts: list[str], n_options: int,
                          options: list[str]) -> tuple[list[int], str]:
        """从"她说的这几条话"里抠出两个编号，返回 ``(picks, 原文)``。

        分三层试：
        1. **第一条整条抠**——用户定的规则就是"题目发出后她说的第一条就是答案"，
           这条必须排在最前；
        2. **第一、二条拼起来**——提示语教她「①你自己选的 ②猜主人选的」，所以她
           常常把 ① 和 ② **分成两条消息**发。只在两条**各恰好一个合法编号**时才拼，
           否则宁可不收——不猜、不补；
        3. **她后续的话里第一条能抠出两个编号的**——第一条是闲聊、答案在第二条；
        4. 都不成就返回空。
        """
        if not texts:
            return [], ""
        picks = _yui_link.parse_picks(texts[0], n_options, 2, options)
        if picks:
            return picks, texts[0]
        if len(texts) >= 2:
            first, second = texts[0], texts[1]
            pa = _yui_link.single_pick(first, n_options)
            pb = _yui_link.single_pick(second, n_options)
            if len(pa) == 1 and len(pb) == 1:
                return [pa[0], pb[0]], first + "\n" + second
        for text in texts[1:]:
            picks = _yui_link.parse_picks(text, n_options, 2, options)
            if picks:
                return picks, text
        return [], ""

    def _compat_await_one(self, question_id: str, index: int, total: int,
                          deadline: float) -> tuple[list[int], str, str]:
        """把这一题发过去，等她交出两个编号。

        返回 ``(picks, 原文, 通道)``；没等到就是 ``([], 最后看到的话, 通道)``。
        **解析不出就是没答上**——绝不替她补一个。
        """
        pub = _compat.question_public(question_id) or {}
        options = [str(o) for o in (pub.get("options") or [])]
        n_options = max(2, len(options))
        snap, mark = self._compat_cursors(mark_bus=False)
        self._compat_push_text(
            self._compat_ask_text(index, total, question_id),
            f"🐾 默契测试 第 {index}/{total} 题",
        )
        last_text = ""
        last_channel = ""
        collected: list[tuple[str, str]] = []
        while time.time() < deadline:
            stop = getattr(self, "_stop_event", None)
            if stop is not None and stop.is_set():
                return [], last_text, last_channel
            fresh = self._compat_new_texts(snap, mark)
            self._compat_log_poll(fresh)
            if fresh:
                # **规则（用户定的）：题目发出去之后她说的第一条，就是这一题的答案。**
                # 但"第一条"里可能混进别的东西（宿主的系统通知、复述题目的系统回声），
                # 所以在她的话里按**先后顺序**找第一条**真能解析出两个编号**的——
                # 顺序不变、不挑通道、不比时间戳；一条都解析不出就如实算没答上，
                # 绝不替她补一个。
                collected.extend(fresh)
                texts = [t for t, _ in collected]
                picks, raw = self._compat_pick_from(texts, n_options, options)
                if picks:
                    return picks, raw, collected[0][1]
                last_text, last_channel = collected[-1]
            if stop is not None:
                stop.wait(self._COMPAT_POLL_SECONDS)
            else:
                time.sleep(self._COMPAT_POLL_SECONDS)
        return [], last_text, last_channel

    # ── 补收：她的回答晚一步才可读时，回头再收一次 ────────────
    def _compat_harvest(self, round_id: str, wait_seconds: float = 0.0) -> dict:
        """把「她后来说的话」按顺序补给还没答上的题。

        为什么非要有这一步：宿主的对话落盘是**懒触发**的（要等用户发言或空闲
        维护才快照）。实测她 20:36 就答了，而对话库到 20:38 还是最后一条 20:10，
        对话流也一样——三个通道里只有实时总线不滞后。所以宁可先把没接住的题标成
        "没答上"，过一会儿再回来收；**收回来的一律标「·补收」**，让人看得出
        是当场接住的还是事后补的。

        配对规则：新收到的、还没被用过的回答，按先后顺序补到还没答上的题上。
        """
        prog = self._compat_progress.get(round_id)
        if not isinstance(prog, dict):
            return {"filled": 0, "note": "没有这一轮的进度记录"}
        entry = self._compat.get(round_id)
        if entry is None:
            return {"filled": 0, "note": "没有找到这一轮"}
        if entry.get("status") == "revealed":
            return {"filled": 0, "note": "这一轮已经揭晓了，补收不会改分"}
        qids = [str(q) for q in (entry.get("question_ids") or [])]
        items = prog.get("items") or []
        deadline = time.time() + max(0.0, float(wait_seconds))
        total = 0
        while True:
            cands = self._compat_all_new_texts(prog)
            used = {it.get("raw") for it in items if it.get("raw")}
            filled_now = 0

            def options_of(idx: int) -> list[str]:
                pub = _compat.question_public(qids[idx]) or {}
                return [str(o) for o in (pub.get("options") or [])]

            def assign(idx: int, text: str, channel: str) -> None:
                picks = _yui_link.parse_picks(text, max(2, len(options_of(idx))), 2,
                                              options_of(idx))
                if not picks:
                    return False
                items[idx].update({"state": "ok", "picks": picks, "raw": text,
                                   "channel": "%s·补收" % channel})
                used.add(text)
                return True

            # ① 先按她自己写的题号认领（「8/10: …」「第4题…」）——一条消息答了几题
            #    时，只有按编号才放得对位置。
            for text, channel in cands:
                if text in used or len(text) > 400:
                    continue
                ref = _yui_link.referenced_question(text, len(qids))
                idx = ref - 1 if ref else -1
                if 0 <= idx < len(items) and not items[idx].get("picks"):
                    if assign(idx, text, channel):
                        filled_now += 1
            # ② 没写题号的，按顺序补给还没答上的题
            for i, item in enumerate(items):
                if item.get("picks") or i >= len(qids):
                    continue
                for text, channel in cands:
                    if text in used:
                        continue
                    if assign(i, text, channel):
                        filled_now += 1
                        break
            cont = prog.setdefault("candidates", [])
            cont[:] = [t for t, _ in cands][:20]
            total += filled_now
            prog["items"] = items
            prog["answered"] = sum(1 for it in items if len(it.get("picks") or []) == 2)
            if filled_now or time.time() >= deadline:
                break
            stop = getattr(self, "_stop_event", None)
            if stop is not None and stop.is_set():
                break
            prog["status"] = "harvesting"
            if stop is not None:
                stop.wait(self._COMPAT_POLL_SECONDS)
            else:
                time.sleep(self._COMPAT_POLL_SECONDS)
        if total:
            self.logger.info("[assessment] 默契补收回 {} 题（{}）", total, round_id)
        return {
            "filled": total,
            "answered": int(prog.get("answered") or 0),
            "note": ("补收回 %d 题" % total) if total else "没有收到新的回答",
        }

    def _compat_apply_pasted(self, round_id: str, text: str) -> dict:
        """把**她说的原话**（用户从对话框里复制过来的）解析成答案。

        为什么必须有这条路：实测在**没有实时读回通道**的情况下（见日志里
        `总线轮询 → messages×40 … 本轮新话 0 条`：只有"推题"那条通道是活的，
        她的回复任何通道都不给），插件不可能自动收到她的回答。与其一轮一轮
        赌通道，不如给她一条一定能收齐的路——她确实是自己答的，用户只是把
        她的话搬过来一次。

        解析规则：**先按她写的题号认领（「8/10」「第4题」），没写题号的按顺序补**。
        而她写的每一题常常是**两行**（① 她自己选的、② 猜主人选的），所以先把行
        按「槽位」归成块再解析——按行单抠永远凑不出两个编号。
        """
        prog = self._compat_progress.get(round_id)
        entry = self._compat.get(round_id)
        if not isinstance(prog, dict) or entry is None:
            return {"filled": 0, "note": "没有找到这一轮"}
        qids = [str(q) for q in (entry.get("question_ids") or [])]
        items = prog.get("items") or []
        n_questions = len(qids)

        def options_of(idx: int) -> list[str]:
            pub = _compat.question_public(qids[idx]) or {}
            return [str(o) for o in (pub.get("options") or [])]

        lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
        if not lines:
            return {"filled": 0, "note": "没看到内容"}
        used_raw = {it.get("raw") for it in items if it.get("raw")}
        filled = 0

        # ① 分组：带题号的行、或以 ① 开头的行 → 开一个新块；其余行并进当前块。
        #    她真写过的样子（N.E.K.O 日志 2026-10-09 21:53）：
        #      「①3，自然醒没人吵，周末就该睡到饱。」
        #      「②猜你选2，你就喜欢窝在家里。」
        blocks: list[list] = []          # [[题号(1-based,0=没写), [行, ...]], ...]
        for line in lines:
            head = line.lstrip("　 ")
            ref = _yui_link.referenced_question(line, n_questions)
            if ref or (head[:1] == "①" and blocks):
                blocks.append([ref, [line]])
            elif blocks:
                blocks[-1][1].append(line)
            else:
                blocks.append([0, [line]])

        def fill(idx: int, raw: str, picks: list[int]) -> None:
            items[idx].update({"state": "ok", "picks": picks, "raw": raw,
                               "channel": "你贴的"})

        # ② 带题号的块：按题号认领；没写题号的块先攒着，等会儿按顺序补
        plain: list[str] = []
        for ref, body_lines in blocks:
            idx = (ref - 1) if ref else -1
            if not (0 <= idx < len(items)) or items[idx].get("picks"):
                if not ref:
                    plain.append("\n".join(body_lines))
                continue                                  # 已答上的题不覆盖
            picks, raw = self._compat_pick_from(body_lines, max(2, len(options_of(idx))),
                                                options_of(idx))
            if picks:
                fill(idx, raw or "\n".join(body_lines), picks)
                used_raw.add(raw)
                filled += 1

        # ③ 没写题号的块：按顺序补给还没答上的题
        for raw in plain:
            if raw in used_raw:
                continue
            for i, item in enumerate(items):
                if item.get("picks") or i >= n_questions:
                    continue
                picks, got = self._compat_pick_from(
                    raw.splitlines(), max(2, len(options_of(i))), options_of(i))
                if picks:
                    fill(i, got or raw, picks)
                    used_raw.add(raw)
                    filled += 1
                    break

        prog["items"] = items
        prog["answered"] = sum(1 for it in items if len(it.get("picks") or []) == 2)
        if filled:
            self.logger.info("[assessment] 贴入她的回答：收下 {} 题（{}）", filled, round_id)
        self._compat_finish(round_id)
        return {"filled": filled, "answered": int(prog["answered"]),
                "note": ("收下 %d 题" % filled) if filled else "这些行里没解析出两个编号"}

    def _compat_harvest_worker(self, round_id: str, wait_seconds: float) -> None:
        """面板点「补收」时跑：后台收一会儿，边收边更新进度。"""
        try:
            self._compat_job = {"status": "harvesting", "round_id": round_id, "source": ""}
            got = self._compat_harvest(round_id, wait_seconds)
            self._compat_finish(round_id)
            if got.get("filled"):
                self.logger.info("[assessment] 补收完成：{} 题（{}）", got["filled"], round_id)
        except Exception as exc:
            self.logger.warning("[assessment] 补收线程异常：{}", traceback.format_exc())
            job = dict(self._compat_job)
            job["reason"] = str(exc)
            self._compat_job = job

    def _compat_watch_worker(self, round_id: str) -> None:
        """问完之后**一直盯着**，她什么时候落盘就什么时候收。

        实测宿主把她的回答写进对话库要等好几分钟（她 20:36 说的，20:40:58 才入库；
        20:53 说的，20:55 还没入库），而且没有可用的实时读回通道。所以"问完就判
        她没答上"是错的——只能一直等。收齐了立刻停；窗口走完才如实标"没答上"。
        """
        try:
            deadline = time.time() + self._COMPAT_ROUND_WINDOW_SECONDS
            while True:
                stop = getattr(self, "_stop_event", None)
                if stop is not None and stop.is_set():
                    break
                prog = self._compat_progress.get(round_id)
                if not isinstance(prog, dict):
                    return
                entry = self._compat.get(round_id)
                if entry is None or entry.get("status") == "revealed":
                    return
                self._compat_harvest(round_id, 0.0)
                missing = [it for it in (prog.get("items") or []) if not (it.get("picks") or [])]
                if not missing:
                    break
                # 收到了一些就先落一次库，好让面板随时能揭晓
                self._compat_finish(round_id, keep_watching=True)
                prog["status"] = "watching"
                self._compat_job = {"status": "watching", "round_id": round_id, "source": ""}
                if time.time() >= deadline:
                    break
                if stop is not None:
                    stop.wait(self._COMPAT_WATCH_POLL_SECONDS)
                else:
                    time.sleep(self._COMPAT_WATCH_POLL_SECONDS)
            self._compat_finish(round_id)
            prog = self._compat_progress.get(round_id)
            if isinstance(prog, dict):
                now = time.time()
                for it in (prog.get("items") or []):
                    if it.get("picks") or it.get("state") != "waiting":
                        continue
                    # 每题至少「还算在等她」满 _COMPAT_PER_Q_WAIT 秒，才允许写成没答上
                    ready_at = float(it.get("asked_at") or 0.0) + float(
                        it.get("wait_seconds") or self._COMPAT_PER_Q_WAIT)
                    if now >= ready_at:
                        it["state"] = "skipped"
                        it["reason"] = "她一直没在对话里落盘"
        except Exception:
            self.logger.warning("[assessment] 盯梢线程异常：{}", traceback.format_exc())

    # ── 面谈调度 ─────────────────────────────────────────────
    def _compat_finish(self, round_id: str, *, keep_watching: bool = False) -> None:
        """把这一轮的作答落库（没答上的题留 None，下标记进 yui_answered）。

        ``keep_watching``：盯梢线程每收一轮就调一次，好让面板随时能揭晓；这时
        别把状态写成"她没答上"——她可能只是还没落盘。
        """
        prog = self._compat_progress.get(round_id) or {}
        items = prog.get("items") or []
        entry = self._compat.get(round_id)
        if entry is None:
            return
        own: list[Any] = []
        guess: list[Any] = []
        answered: list[int] = []
        for i, item in enumerate(items):
            picks = item.get("picks") or []
            if len(picks) == 2:
                own.append(int(picks[0]))
                guess.append(int(picks[1]))
                answered.append(i)
            else:
                own.append(None)
                guess.append(None)
        if not answered:
            if keep_watching:
                prog["answered"] = 0
                return
            self._compat_job = {
                "status": "no_answer", "round_id": round_id, "source": "",
                "reason": "她的回答一直没出现在对话里，这一轮不算她的成绩",
            }
            prog["status"] = "no_answer"
            return
        self._compat.set_yui(round_id, {"own": own, "guess": guess}, "herself", answered)
        prog["answered"] = len(answered)
        if keep_watching:
            return
        self._compat_job = {"status": "done", "round_id": round_id, "source": "herself"}
        prog["status"] = "done"
        raw = [it.get("raw") or "" for it in items if it.get("raw")]
        self._compat_reply[round_id] = {
            "text": "\n\n".join(raw),
            "ts": time.strftime("%H:%M:%S"),
            "answered": len(answered),
            "total": len(items),
        }
        self.logger.info("[assessment] 她自己答完了默契测试：{}（{}/{} 题）",
                         round_id, len(answered), len(items))

    def _compat_interview(self, round_id: str, question_ids: list[str],
                          only: list[int] | None = None) -> None:
        """后台线程：**她答完这一题才发下一题**（用户要求的节奏）。

        读回通道通的时候每题几秒就收到答案，10 题一两分钟走完；通道不通时第一题
        就能探出来，随即退化成快节奏，不白等 50 分钟。
        """
        try:
            prog = self._compat_progress.get(round_id)
            if not isinstance(prog, dict):
                return
            items = prog.get("items") or []
            total = len(question_ids)
            todo = list(range(total)) if not only else [i for i in only if 0 <= i < total]
            reads_ok = 0
            adaptive = bool(prog.get("adaptive"))
            for i in todo:
                stop = getattr(self, "_stop_event", None)
                if stop is not None and stop.is_set():
                    prog["status"] = "stopped"
                    return
                qid = str(question_ids[i])
                while len(items) <= i:                     # 兜底，别让下标越界
                    items.append({"i": len(items) + 1, "state": "pending"})
                item = items[i]
                item["i"] = i + 1
                item["qid"] = qid
                item["state"] = "asking"
                prog["index"] = i + 1
                prog["status"] = "asking"
                if adaptive:
                    wait = self._COMPAT_GAP_SECONDS
                elif reads_ok == 0 and i == 0:
                    wait = self._COMPAT_PROBE_WAIT     # 先短探一下读回通道通不通
                else:
                    wait = self._COMPAT_PER_Q_WAIT     # 通了就按用户要求给足 300 秒
                picks, raw, channel = self._compat_await_one(
                    qid, i + 1, total, time.time() + wait)
                if picks:
                    reads_ok += 1
                    item["state"] = "ok"
                    item["picks"] = picks
                    prog["answered"] = int(prog.get("answered") or 0) + 1
                else:
                    item["state"] = "waiting"
                    item["picks"] = []
                    item["reason"] = "还没读到她的回答（宿主落盘慢）"
                    if i == 0 and reads_ok == 0 and not adaptive:
                        # 第一题探不通 → 读回通道没响应。再按 300 秒一题等下去就是
                        # 50 分钟白等，所以退化成"快节奏发完、之后一直替她收"。
                        adaptive = True
                        prog["adaptive"] = True
                        prog["reason"] = ("读回通道一时没响应，先按 %d 秒间隔把题发完，"
                                          "之后一直替她收" % int(self._COMPAT_GAP_SECONDS))
                        self.logger.warning(
                            "[assessment] 读回通道没响应，默契测试改用快节奏发题（{}）", round_id)
                item["asked_at"] = time.time()
                item["wait_seconds"] = self._COMPAT_PER_Q_WAIT
                item["raw"] = raw
                item["channel"] = channel
                items[i] = item
                prog["items"] = items
            prog["index"] = total
            # 问完只等于"问完"。宿主的落盘要好几分钟，所以**不**在这儿判她没答上，
            # 先把没接住的题标成"等她落盘"，交给盯梢线程慢慢收。
            for it in (prog.get("items") or []):
                if not (it.get("picks") or []):
                    it["state"] = "waiting"
                    it["reason"] = "还没读到她的回答（宿主落盘慢）"
            prog["status"] = "watching"
            self._compat_job = {"status": "watching", "round_id": round_id, "source": ""}
            threading.Thread(
                target=self._compat_watch_worker, args=(round_id,),
                daemon=True, name="neko-assess-watch",
            ).start()
        except Exception as exc:
            self.logger.warning("[assessment] 逐题面谈线程异常：{}", traceback.format_exc())
            prog = self._compat_progress.get(round_id)
            if isinstance(prog, dict):
                prog["status"] = "error"
                prog["reason"] = str(exc)
            self._compat_job = {"status": "error", "round_id": round_id, "source": str(exc)}

    def _compat_start_round(self, question_ids: list[str], *,
                            only: list[int] | None = None,
                            round_id: str = "") -> dict:
        if round_id:
            entry = self._compat.get(round_id) or {}
        else:
            round_id = uuid.uuid4().hex[:10]
            entry = self._compat.start_round(round_id, question_ids)
        prog = self._compat_progress.get(round_id)
        if not isinstance(prog, dict):
            prog = {
                "round_id": round_id,
                "total": len(question_ids),
                "index": 0,
                "status": "asking",
                "answered": 0,
                "items": [{"i": i + 1, "qid": str(q), "state": "pending"}
                          for i, q in enumerate(question_ids)],
                "reason": "",
            }
            self._compat_progress[round_id] = prog
        else:
            for i in (only or []):
                if 0 <= i < len(prog.get("items") or []):
                    prog["items"][i]["state"] = "pending"
                    prog["items"][i]["picks"] = []
            prog["status"] = "asking"
            prog["reason"] = ""
        # 回合起点游标：补收时要用它把「这一轮以来她说的所有话」捞全
        snap, mark = self._compat_cursors()
        prog["start"] = {"snap": snap, "mark": mark}
        prog["answered"] = sum(1 for it in prog["items"] if len(it.get("picks") or []) == 2)
        self._compat_log_channels()
        self._compat_job = {"status": "asking", "round_id": round_id, "source": "herself"}
        threading.Thread(
            target=self._compat_interview, args=(round_id, question_ids, only),
            daemon=True, name="neko-assess-compat",
        ).start()
        return entry

    def _compat_bus_surface(self) -> str:
        """把 `ctx.bus` 到底长什么样写进日志。

        宿主没有公开的插件事件 API 文档，官方插件里那处 `ctx.bus.memory.get_sync`
        也只是一句"有就更好、没有就返回 unknown"的可选提示。唯一可靠的办法是把
        对象摊开看一眼——把类名和成员名列出来，下次重启就知道有没有实时通道。
        """
        def names(obj: Any) -> str:
            try:
                return ",".join(sorted(n for n in dir(obj) if not n.startswith("_"))[:26])
            except Exception:
                return "（读不出来）"

        try:
            bus = getattr(self.ctx, "bus", None)
        except Exception as exc:
            return "ctx.bus 读不到：%s" % exc
        if bus is None:
            return "ctx.bus 不存在"
        parts = ["ctx.bus=%s[%s]" % (type(bus).__name__, names(bus))]
        memory = getattr(bus, "memory", None)
        if memory is None:
            parts.append("ctx.bus.memory 不存在")
        else:
            parts.append("ctx.bus.memory=%s[%s]" % (type(memory).__name__, names(memory)))
        for label, obj in (("ctx", self.ctx), ("ctx.bus", bus), ("ctx.bus.memory", memory)):
            if obj is None:
                continue
            if callable(getattr(obj, "get_sync", None)):
                parts.append("%s.get_sync 可调用" % label)
        return "；".join(parts)

    def _compat_log_channels(self) -> None:
        """把三条读回通道各能看到什么写进插件日志。

        上一版"她答了却读不到"排查了很久——因为通道失败被 `except: pass` 吞了，
        日志里一片空白。这一行是给自己留的证据，重启后直接看日志就够了。
        """
        try:
            bus = self._yui_bus()
            bus_info = "未建" if bus is None else (
                "不可用" if not bus.available else
                "type=%s 记录%d条" % (bus.kinds(20) or "（空）", len(bus.records(20))))
        except Exception as exc:
            bus_info = "异常：%s" % exc
        feed_info = "未建"
        feed = self._yui_feed()
        if feed is not None:
            try:
                base = feed._base() if feed.base == "" else feed.base
                feed_info = ("连上 %s，看到 %d 轮" % (base, len(feed.turns()))
                             if base else "连不上宿主记忆服务")
            except Exception as exc:
                feed_info = "异常：%s" % exc
        dlg = self._yui_dialog()
        db_info = "未定位到"
        if dlg is not None:
            try:
                st = dlg.stats()
                db_info = ("%s，%s 条，最后 %s"
                           % ("可读" if st.get("available") else "不可读",
                              st.get("messages", "?"), st.get("latest") or "—"))
            except Exception as exc:
                db_info = "异常：%s" % exc
        self.logger.info("[assessment] 默契读回通道自查 → 实时总线：{}；对话流：{}；对话库：{}",
                         bus_info, feed_info, db_info)
        try:
            self.logger.info("[assessment] 总线各通道实调：{}",
                             _yui_link.bus_probe(self.ctx, self._char_name()))
        except Exception as exc:
            self.logger.warning("[assessment] 总线探测失败：{}", exc)
        try:
            self.logger.info("[assessment] ctx.bus 门面：{}", _yui_link.bus_report(self.ctx))
        except Exception as exc:
            self.logger.warning("[assessment] 读 ctx.bus 门面失败：{}", exc)

    # ── 接口 ─────────────────────────────────────────────────
    def _compat_limits(self) -> dict:
        """三个时间刻度原样交给面板——用户最关心的就是"等多久"。"""
        return {
            "per_q_wait": self._COMPAT_PER_Q_WAIT,
            "probe_wait": self._COMPAT_PROBE_WAIT,
            "gap": self._COMPAT_GAP_SECONDS,
            "round_window": self._COMPAT_ROUND_WINDOW_SECONDS,
        }

    def _api_compat(self, body: dict) -> dict:
        """默契测试：开回合 / 交卷 / 揭晓 / 重问 / 历史。双方交卷前绝不返回她的答案。"""
        if self._compat is None:
            return {"ok": False, "error": "默契存储未就绪。"}
        payload = body if isinstance(body, dict) else {}
        action = str(payload.get("action") or "info").strip() or "info"

        if action == "start":
            qids = _compat.sample_questions()
            entry = self._compat_start_round(qids)
            return {
                "ok": True,
                "round": {
                    "id": entry.get("id"),
                    "questions": [_compat.question_public(q) for q in qids],
                },
                "job": dict(self._compat_job),
                "progress": dict(self._compat_progress.get(entry.get("id")) or {}),
                "limits": self._compat_limits(),
                "note": "题目一题一条发给她，她在对话里用自己的话答；你在这边同时答你的。",
            }

        if action == "ask_again":
            round_id = str(payload.get("round_id") or "").strip()
            entry = self._compat.get(round_id)
            if entry is None:
                return {"ok": False, "error": "没有找到这一轮，可能已经换新一轮了。"}
            prog = self._compat_progress.get(round_id)
            prog = prog if isinstance(prog, dict) else {}
            missing = [i for i, it in enumerate(prog.get("items") or [])
                       if not (it.get("picks") or [])]
            if not missing:
                self._compat_start_round(entry["question_ids"], round_id=round_id)
                return {"ok": True, "job": dict(self._compat_job),
                        "note": "她说过的都记着，整轮重问了一遍。",
                        "progress": dict(self._compat_progress.get(round_id) or {})}
            self._compat_start_round(entry["question_ids"], only=missing, round_id=round_id)
            return {"ok": True, "job": dict(self._compat_job),
                    "note": f"只重问没答上的 {len(missing)} 题。",
                    "progress": dict(self._compat_progress.get(round_id) or {})}

        if action == "probe":
            # 一次性取证：这一刻每条读回通道到底给了什么，原样返回。
            # 前面几轮全是靠轮询日志间接推断，浪费了很多时间；有这一条就不用猜了。
            return {"ok": True, "dump": _yui_link.bus_dump(self.ctx, self._char_name())}

        if action == "paste":
            round_id = str(payload.get("round_id") or "").strip()
            entry = self._compat.get(round_id)
            if entry is None:
                return {"ok": False, "error": "没有找到这一轮，可能已经换新一轮了。"}
            got = self._compat_apply_pasted(round_id, payload.get("text") or "")
            return {"ok": True, "job": dict(self._compat_job),
                    "progress": dict(self._compat_progress.get(round_id) or {}),
                    "limits": self._compat_limits(),
                    "note": got.get("note")}

        if action == "harvest":
            # 她的回答**晚一步**才可读（宿主落盘是懒触发的）。与其判她"没答上"，
            # 不如回头再收一次：后台跑，面板照旧轮询 reveal 看进度。
            round_id = str(payload.get("round_id") or "").strip()
            entry = self._compat.get(round_id)
            if entry is None:
                return {"ok": False, "error": "没有找到这一轮，可能已经换新一轮了。"}
            if entry.get("status") == "revealed":
                return {"ok": True, "job": dict(self._compat_job),
                        "progress": dict(self._compat_progress.get(round_id) or {}),
                        "note": "这一轮已经揭晓了，补收不会改分。"}
            threading.Thread(
                target=self._compat_harvest_worker,
                args=(round_id, self._COMPAT_HARVEST_SECONDS),
                daemon=True, name="neko-assess-harvest",
            ).start()
            return {"ok": True, "job": dict(self._compat_job),
                    "progress": dict(self._compat_progress.get(round_id) or {}),
                    "note": "正在从对话里补收她的回答，进度会自己往上走。"}

        if action == "submit":
            round_id = str(payload.get("round_id") or "").strip()
            entry = self._compat.get(round_id)
            if entry is None:
                return {"ok": False, "error": "没有找到这一轮，可能已经换新一轮了。"}
            answers = _compat.validate_answers(entry["question_ids"], payload.get("answers"))
            if answers is None:
                return {"ok": False, "error": "答案格式不对：每题都要选自己的、再猜 YUI 的。"}
            entry = self._compat.set_self(round_id, answers)
            if entry is None:
                return {"ok": False, "error": "这一轮已经结束了，重新开一轮吧。"}
            return {"ok": True, "status": entry["status"], "job": dict(self._compat_job),
                    "progress": dict(self._compat_progress.get(round_id) or {})}

        if action == "reveal":
            round_id = str(payload.get("round_id") or "").strip()
            prog = dict(self._compat_progress.get(round_id) or {})
            entry = self._compat.reveal(round_id)
            if entry is None:
                return {"ok": False, "error": "没有找到这一轮。"}
            if entry.get("status") != "revealed":
                # 双方没交齐：只说进度，不给任何答案
                return {"ok": True, "status": "waiting", "job": dict(self._compat_job),
                        "progress": prog, "limits": self._compat_limits(),
                        "yui_answered": bool(entry.get("yui"))}
            return {
                "ok": True,
                "status": "revealed",
                "round_id": round_id,
                "yui_source": entry.get("yui_source") or "",
                # 她逐题的原话。面板会显示出来——这是"真的她在答"最直接的证据
                "yui_reply": self._compat_reply.get(round_id) or {},
                "result": entry["result"],
                "job": dict(self._compat_job),
                "progress": prog,
                "limits": self._compat_limits(),
            }

        return {
            "ok": True,
            "history": self._compat.history(),
            "job": dict(self._compat_job),
            "progress": dict(self._compat_progress.get(
                str(self._compat_job.get("round_id") or "")) or {}),
            "limits": self._compat_limits(),
            "memory": self._yui_stats(),
        }

    # ── 猫娘塔罗 ───────────────────────────────────────────────
    _TAROT_SYSTEM = (
        "你是猫娘塔罗解读师，用猫娘口吻解读塔罗牌。规则："
        "1. 只依据给出的牌名、正逆位与传统关键词解读，不许编造关键词以外的信息；"
        "2. 语气温柔俏皮，自称本喵，称呼用户为主人；"
        "3. 逐张牌简短解读后，给一段整体总结和一条具体建议；"
        "4. 全文 300 字以内，纯文本，不要任何列表符号或标题格式。"
    )

    def _tarot_path(self) -> Path:
        return self._data_dir() / "tarot.json"

    def _tarot_read(self) -> dict:
        import json

        try:
            data = json.loads(self._tarot_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _tarot_write(self, state: dict) -> None:
        """原子写：.tmp → os.replace，绝不出现半截 JSON。"""
        import json
        import os

        self._tarot_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = self._tarot_path().with_name("tarot.json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._tarot_path())

    def _tarot_update(self, mutate) -> None:
        with self._tarot_lock:
            state = self._tarot_read()
            try:
                mutate(state)
                self._tarot_write(state)
            except Exception as exc:
                self.logger.warning("[assessment] 塔罗状态写入失败：{}", exc)

    def _api_tarot_info(self, _body: dict) -> dict:
        state = self._tarot_read()
        history = state.get("history")
        history = history if isinstance(history, list) else []
        readings = state.get("readings")
        readings = readings if isinstance(readings, dict) else {}
        interprets = {
            str(k): (v.get("interpret") if isinstance(v, dict) else "")
            for k, v in readings.items()
        }
        return {
            "ok": True,
            "spreads": _tarot.spread_meta(),
            "history": history[:_tarot._MAX_HISTORY],
            "interprets": interprets,
            "job": self._tarot_job,
        }

    def _api_tarot_draw(self, body: dict) -> dict:
        payload = body if isinstance(body, dict) else {}
        spread_id = str(payload.get("spread") or "").strip() or "three"
        question = str(payload.get("question") or "").strip()
        drawn = _tarot.draw(spread_id, question)
        if drawn is None:
            return {"ok": False, "error": f"未知牌阵：{spread_id}", "spreads": _tarot.spread_meta()}
        draw_id = uuid.uuid4().hex[:10]
        entry = {
            "id": draw_id,
            "ts": int(time.time()),
            "interpret": "",
            "interpret_kind": "",
            **drawn,
        }

        def mutate(state: dict) -> None:
            history = state.setdefault("history", [])
            if isinstance(history, list):
                history.insert(0, {
                    k: entry[k] for k in
                    ("id", "ts", "spread", "spread_name", "question", "cards")
                })
                del history[_tarot._MAX_HISTORY:]
            readings = state.setdefault("readings", {})
            readings[draw_id] = entry
            keep = {h.get("id") for h in history if isinstance(h, dict)}
            for k in [k for k in readings if k not in keep]:
                readings.pop(k, None)

        self._tarot_update(mutate)
        return {"ok": True, "draw": entry, "job": self._tarot_job}

    def _api_tarot_interpret(self, body: dict) -> dict:
        payload = body if isinstance(body, dict) else {}
        draw_id = str(payload.get("draw_id") or "").strip()
        entry = self._tarot_read().get("readings", {}).get(draw_id)
        if not isinstance(entry, dict):
            return {"ok": False, "error": "没有找到这次占卜的记录喵，重新抽一次吧。", "job": self._tarot_job}
        if self._tarot_job.get("status") == "running" and self._tarot_job.get("draw_id") == draw_id:
            return {"ok": True, "job": self._tarot_job}
        if str(entry.get("interpret") or "").strip():
            # 已有解读：直接回，不再花一次模型钱
            self._tarot_job = {
                "status": "done", "draw_id": draw_id,
                "text": entry["interpret"], "kind": entry.get("interpret_kind") or "llm",
            }
            return {"ok": True, "job": self._tarot_job}
        self._tarot_job = {"status": "running", "draw_id": draw_id, "text": ""}
        threading.Thread(
            target=self._tarot_worker, args=(draw_id,), daemon=True, name="neko-assess-tarot"
        ).start()
        return {"ok": True, "job": self._tarot_job}

    def _tarot_worker(self, draw_id: str) -> None:
        """后台线程：私有事件循环调 LLM，绝不碰宿主循环。"""
        try:
            entry = self._tarot_read().get("readings", {}).get(draw_id) or {}
            drawn = {
                "question": entry.get("question", ""),
                "spread_name": entry.get("spread_name", ""),
                "cards": entry.get("cards", []),
            }
            text, kind = "", "llm"
            try:
                loop = asyncio.new_event_loop()
                try:
                    text = loop.run_until_complete(self._tarot_llm(drawn))
                finally:
                    loop.close()
            except Exception as exc:
                self.logger.warning("[assessment] 塔罗 LLM 解读失败，降级为牌义摆盘：{}", exc)
                text, kind = _tarot.fallback_reading(drawn), "fallback"
            if not str(text or "").strip():
                text, kind = _tarot.fallback_reading(drawn), "fallback"
            self._tarot_job = {
                "status": "done", "draw_id": draw_id, "text": text, "kind": kind,
            }

            def mutate(state: dict) -> None:
                readings = state.setdefault("readings", {})
                if isinstance(readings.get(draw_id), dict):
                    readings[draw_id]["interpret"] = text
                    readings[draw_id]["interpret_kind"] = kind

            self._tarot_update(mutate)
        except Exception as exc:
            self.logger.warning("[assessment] 塔罗解读线程异常：{}", traceback.format_exc())
            self._tarot_job = {"status": "error", "draw_id": draw_id, "text": f"解读出了点问题：{exc}"}

    async def _llm_chat(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        """塔罗与默契共用的模型调用：llm_client 优先，失败直连 /chat/completions。"""
        cfg: dict[str, Any] = {}
        try:
            from utils.config_manager import get_config_manager

            cfg = get_config_manager().get_model_api_config("conversation")
            cfg = cfg if isinstance(cfg, dict) else {}
        except Exception as exc:
            self.logger.info("[assessment] 读取模型配置失败：{}", exc)
        model = str(cfg.get("model") or "").strip()
        base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
        api_key = str(cfg.get("api_key") or "").strip()
        if not (model and base_url and api_key):
            raise RuntimeError("尚未配置会话模型")

        # 优先官方 llm_client；TypeError 兼容无 provider_type 参数的旧签名
        try:
            from utils.llm_client import create_chat_llm_async

            kwargs: dict[str, Any] = {
                "model": model, "base_url": base_url, "api_key": api_key,
                "max_completion_tokens": max_tokens, "timeout": 60.0,
            }
            try:
                llm = create_chat_llm_async(**kwargs)
            except TypeError:
                provider_type = str(cfg.get("provider_type") or "").strip() or None
                if provider_type:
                    kwargs["provider_type"] = provider_type
                llm = create_chat_llm_async(**kwargs)
            result = await asyncio.wait_for(
                llm.ainvoke([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]),
                timeout=60.0,
            )
            text = getattr(result, "content", None) or str(result or "")
            if str(text).strip():
                return str(text).strip()
            raise RuntimeError("模型返回为空")
        except ImportError:
            pass
        except Exception as exc:
            self.logger.warning("[assessment] llm_client 调用失败，降级直连：{}", exc)
        return await asyncio.to_thread(self._llm_chat_http, base_url, api_key, model, system, user, max_tokens)

    def _llm_chat_http(self, base_url: str, api_key: str, model: str,
                       system: str, user: str, max_tokens: int) -> str:
        import json
        import urllib.error
        import urllib.request

        endpoint = f"{base_url}/chat/completions"
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_completion_tokens": max_tokens,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60.0) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"模型请求失败：HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"模型请求失败：{exc}") from exc
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("模型没有返回内容")
        return str((choices[0].get("message") or {}).get("content") or "").strip()

    async def _tarot_llm(self, drawn: dict) -> str:
        return await self._llm_chat(self._TAROT_SYSTEM, "\n".join(_tarot.card_lines(drawn)))

    # ── 聊天入口（轻量，方便在对话框里唤起）────────────────────
    @message(id="assessment_chat", source="chat")
    def on_chat_message(self, **kwargs) -> dict:
        """对话框里发「/测评」就把量表清单回给主人（同步方法，与内置插件一致）。

        另外：**把每一次收到的事件原样记一笔**（只记前 40 次，免得刷屏）。
        宿主的插件事件 API 没有公开文档，这条日志是我们判断"插件到底能不能收到
        她（猫娘）的话"的唯一证据——万一能收到，实时读回就有了着落，不必再靠
        盯梢等落盘。
        """
        try:
            self._chat_events = int(getattr(self, "_chat_events", 0) or 0) + 1
            if self._chat_events <= 40:
                preview = {str(k): str(kwargs[k])[:60] for k in list(kwargs)[:8]}
                self.logger.info("[assessment] 收到 chat 事件 #{}：{}",
                                 self._chat_events, preview)
        except Exception:
            pass
        try:
            content = str(kwargs.get("text") or kwargs.get("content") or kwargs.get("message") or "").strip()
            lowered = content.lower()
            triggers = ("/测评", "/心理测评", "/人格测评", "/测试")
            if not any(lowered.startswith(t) or lowered == t for t in triggers):
                return {"ok": True}
            rows = list_scales()
            lines = ["心理测评室 · 可用量表："]
            for row in rows:
                lines.append(f"· {row['name']}（{row['category']}，{row['count']} 题 / 约 {row['minutes']} 分钟）")
            lines.append("")
            lines.append("打开插件面板就能做，结果只存在你本机。")
            lines.append("提醒：这些只作自我了解，不是医学诊断哦～")
            return Ok("\n".join(lines))
        except Exception:
            self.logger.warning("[assessment] 聊天入口异常：{}", traceback.format_exc())
            return {"ok": True}

    @llm_tool(
        name="list_assessments",
        description="列出本机可用的心理 / 人格 / 趣味测评及其简介、题数与来源。",
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    async def llm_list_assessments(self, **_) -> dict:
        return {"ok": True, "scales": list_scales()}



    def warning(self, *_a, **_k):
        pass


__all__ = ["AssessmentPlugin"]
