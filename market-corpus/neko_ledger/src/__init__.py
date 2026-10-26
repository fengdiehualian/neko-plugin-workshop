"""记账猫娘（N.E.K.O 插件）

让猫娘帮你记账，并且**按花销的离谱程度切换语气**：

```
记账，面条10元        →  记好啦～「面条」10 元，喵～        （可爱）
记账，面包300元       →  面包 300 元？！这个价格是不是有点夸张了呀…… （吐槽）
记账，猫粮120元       →  嘶——猫粮 120 元？！人家可是猫诶！哈气！  （哈气）
```

三类花销会触发**哈气**：猫咪相关、AI 相关 —— 因为猫娘自己既是猫又是 AI。

## 人设优先

用户的人设可能写着「绝对不生气 / 温柔」。插件会**读当前猫娘的人设**，
如果检测到这类约束，哈气会**降级为温和的念叨**，并在结果里说明原因
（`hiss_downgraded`），不做静默降级。

判定逻辑在 `_tone.persona_allows_hiss()`：先找「不生气」类否定式，
再找「会生气」类肯定式 —— 顺序反了会把温柔人设误判成可以哈气。

## 数据存放

账本存在插件自己的目录（默认 `%LOCALAPPDATA%\\N.E.K.O\\plugins\\neko_ledger\\ledger.db`），
**不写角色卡**。记账是插件自己的数据，没有理由动用户的人设。
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from ._parse import categorize, parse_expense
from ._persona import PersonaReader
from ._store import LedgerStore, default_data_dir
from ._tone import (
    TONE_CUTE,
    TONE_HISS,
    TONE_SNARK,
    build_comment,
    build_soft_comment,
    classify_tone,
    judge_outlier,
    persona_allows_hiss,
)

_DEFAULT_ABSOLUTE = 200.0
_DEFAULT_MULTIPLIER = 4.0
_DEFAULT_MIN_SAMPLES = 3


@neko_plugin
class LedgerPlugin(NekoPluginBase):
    """记账猫娘。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: dict[str, Any] = {}
        self._store: Optional[LedgerStore] = None
        self._rng = random.Random()
        # 人设缓存：启动时读一次，之后复用；换猫娘时自动失效
        self._persona_cache: Optional[dict[str, Any]] = None
        self._hiss_allowed_cache: Optional[tuple[bool, str]] = None

    # ------------------------------------------------------------------ 配置

    async def _config(self) -> dict[str, Any]:
        if self._cfg:
            return self._cfg
        cfg: dict[str, Any] = {}
        try:
            dumped = await self.config.dump(timeout=5.0)
            if isinstance(dumped, dict):
                cfg = dumped
        except Exception as exc:  # noqa: BLE001 - 读不到就用默认值
            self.logger.warning("读取配置失败，使用默认值: %s", exc)
        self._cfg = cfg
        return cfg

    async def _settings(self) -> dict[str, Any]:
        cfg = await self._config()
        section = cfg.get("ledger")
        section = section if isinstance(section, dict) else {}

        def _num(key: str, fallback: float, *, low: float = 0.0, high: float = 1e9) -> float:
            try:
                value = float(section.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
            return max(low, min(high, value))

        return {
            "data_dir": str(section.get("data_dir") or "").strip(),
            "absolute_threshold": _num("absolute_threshold", _DEFAULT_ABSOLUTE, low=0.0),
            "category_multiplier": _num("category_multiplier", _DEFAULT_MULTIPLIER, low=1.0),
            "min_samples": int(_num("min_samples", _DEFAULT_MIN_SAMPLES, low=1, high=100)),
            "snark_enabled": bool(section.get("snark_enabled", True)),
            "hiss_enabled": bool(section.get("hiss_enabled", True)),
            "respect_persona": bool(section.get("respect_persona", True)),
            "cat_suffix": str(section.get("cat_suffix") or ""),
            "emoji": bool(section.get("emoji", True)),
            "characters_json": str(section.get("characters_json") or "").strip(),
            "catgirl": str(section.get("catgirl") or "").strip(),
        }

    # --------------------------------------------------------------- 资源

    def _get_store(self, settings: dict[str, Any]) -> LedgerStore:
        if self._store is None:
            base = Path(settings["data_dir"]) if settings["data_dir"] else default_data_dir()
            self._store = LedgerStore(base / "ledger.db")
        return self._store

    def _persona_reader(self, settings: dict[str, Any]) -> PersonaReader:
        return PersonaReader(settings["characters_json"] or None)

    async def _refresh_persona(self, settings: dict[str, Any]) -> None:
        """读一次当前猫娘的人设并缓存。

        每个用户的猫娘性格都不一样，所以人设必须在**运行时**读，
        不能写死；同时在启动时读一次并缓存，避免每记一笔都去解析
        那个几十 KB 的 characters.json。

        缓存里记下当时读的是哪只猫娘，用户中途换角色能自动失效。
        """
        reader = self._persona_reader(settings)
        name = None
        text = ""
        try:
            name = await reader.current_catgirl(settings["catgirl"] or None)
            if name:
                text = await reader.persona_text(name)
        except Exception as exc:  # noqa: BLE001 - 读不到就退回默认语气
            self.logger.warning("读取人设失败，按默认处理: %s", exc)
            name, text = None, ""

        self._persona_cache = {"catgirl": name, "text": text}
        self._hiss_allowed_cache = None

        if not settings["respect_persona"]:
            self.logger.info("人设：跳过检测（配置里已关闭 respect_persona）")
        elif not text:
            self.logger.info("人设：读不到（将按默认允许哈气处理）")
        else:
            allowed = persona_allows_hiss(text)
            self.logger.info(
                "人设：%s，人设文本 %d 字 → 哈气=%s",
                name or "?",
                len(text),
                "允许" if allowed else "禁止（改用温和吐槽）",
            )

    async def _hiss_allowed(self, settings: dict[str, Any]) -> tuple[bool, str]:
        """返回 (是否允许哈气, 说明)。

        结果会缓存；缓存与「当前是哪只猫娘」绑定，换角色后自动重读。
        """
        if not settings["hiss_enabled"]:
            return False, "配置里关闭了哈气"

        if not settings["respect_persona"]:
            return True, "配置里关闭了人设检测"

        if self._hiss_allowed_cache is not None:
            return self._hiss_allowed_cache

        # 首次调用（或换过角色）时读一次
        if self._persona_cache is None:
            await self._refresh_persona(settings)
        else:
            # 用户可能中途换了猫娘，检查一下缓存是否还对应
            reader = self._persona_reader(settings)
            try:
                current = await reader.current_catgirl(settings["catgirl"] or None)
            except Exception:  # noqa: BLE001
                current = None
            if current and current != self._persona_cache.get("catgirl"):
                self.logger.info(
                    "检测到猫娘从 %s 切换到 %s，重新读取人设",
                    self._persona_cache.get("catgirl"),
                    current,
                )
                await self._refresh_persona(settings)

        text = (self._persona_cache or {}).get("text") or ""
        if not text:
            decision: tuple[bool, str] = (True, "")
        elif persona_allows_hiss(text):
            decision = (True, "")
        else:
            decision = (False, "人设不允许生气／哈气，已改用温和吐槽")

        self._hiss_allowed_cache = decision
        return decision

    # --------------------------------------------------------------- 记账核心

    def _compose(
        self,
        *,
        tone: str,
        item: str,
        amount: float,
        settings: dict[str, Any],
    ) -> str:
        return build_comment(
            tone=tone,
            item=item,
            amount=amount,
            cat_suffix=settings["cat_suffix"],
            rng=self._rng,
            include_emoji=settings["emoji"],
        )

    async def _record(
        self,
        *,
        text: str = "",
        amount: Optional[float] = None,
        item: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """解析 → 判离谱 → 选语气 → 生成文案 →（可选）落库。"""
        settings = await self._settings()

        parsed = None
        if text:
            parsed = parse_expense(text)

        final_amount: Optional[float] = None
        if amount is not None:
            try:
                final_amount = float(amount)
            except (TypeError, ValueError):
                final_amount = None
        if final_amount is None and parsed is not None:
            final_amount = parsed.amount

        if final_amount is None or final_amount <= 0:
            return {
                "ok": False,
                "error": "没识别出金额。可以这样说：「记账，面条10元」",
            }

        final_item = (item or "").strip()
        if not final_item:
            final_item = parsed.item if parsed else ""
        if not final_item:
            final_item = "未命名"

        category = categorize(final_item, text or final_item)
        store = self._get_store(settings)

        avg, samples = await store.category_stats(category)
        verdict = judge_outlier(
            final_amount,
            category=category,
            category_avg=avg,
            category_samples=samples,
            absolute_threshold=settings["absolute_threshold"],
            category_multiplier=settings["category_multiplier"],
            min_samples=settings["min_samples"],
        )

        hiss_ok, hiss_note = await self._hiss_allowed(settings)
        tone, downgrade_note = classify_tone(
            category=category, outlier=verdict, hiss_allowed=hiss_ok
        )

        # snark 被关闭时，离谱也只按日常处理
        if tone == TONE_SNARK and not settings["snark_enabled"]:
            tone = TONE_CUTE

        if tone == TONE_HISS and not hiss_ok:
            speech = build_soft_comment(
                item=final_item,
                amount=final_amount,
                cat_suffix=settings["cat_suffix"],
                rng=self._rng,
            )
        else:
            speech = self._compose(
                tone=tone, item=final_item, amount=final_amount, settings=settings
            )

        note = downgrade_note or (hiss_note if tone == TONE_HISS else "")

        result: dict[str, Any] = {
            "ok": True,
            "speech": speech,
            "item": final_item,
            "amount": round(final_amount, 2),
            "category": category,
            "tone": tone,
            "outlier": verdict.to_dict(),
            "category_avg": round(avg, 2) if avg else None,
        }
        if note:
            result["hiss_downgraded"] = note
        if parsed is not None and parsed.ambiguous:
            result["ambiguous"] = parsed.ambiguous

        if dry_run:
            result["dry_run"] = True
            return result

        saved = await store.add(
            amount=final_amount,
            item=final_item,
            category=category,
            raw=text or f"{final_item}{final_amount:g}元",
            tone=tone,
            note=note,
        )
        result["id"] = saved["id"]
        result["ts"] = saved["ts"]

        self.logger.info(
            "记账 #%s %s %.2f 元 [%s/%s]%s",
            saved["id"],
            final_item,
            final_amount,
            category,
            tone,
            f"（{note}）" if note else "",
        )
        return result

    # --------------------------------------------------------------- 汇总

    @staticmethod
    def _range_for(period: str) -> tuple[str, str]:
        today = date.today()
        p = (period or "today").strip().lower()
        if p in ("today", "今天", "今日"):
            start = end = today
        elif p in ("week", "本周", "这周", "周"):
            start = today - timedelta(days=today.weekday())
            end = today
        elif p in ("month", "本月", "这个月", "月"):
            start = today.replace(day=1)
            end = today
        elif p in ("year", "今年", "年"):
            start = today.replace(month=1, day=1)
            end = today
        elif p in ("all", "全部", "所有"):
            start = date(1970, 1, 1)
            end = today
        else:
            start = end = today
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    @staticmethod
    def _period_label(period: str) -> str:
        """给区间一个中文说法（用于文案）。"""
        p = (period or "today").strip().lower()
        return {
            "today": "今天",
            "week": "这周",
            "month": "本月",
            "year": "今年",
            "all": "总共",
            "今天": "今天",
            "本周": "这周",
            "这周": "这周",
            "本月": "本月",
            "这个月": "本月",
            "今年": "今年",
            "全部": "总共",
            "所有": "总共",
        }.get(p, "今天" if not p else p)

    async def _summary(self, *, period: str = "today", category: str = "") -> dict[str, Any]:
        settings = await self._settings()
        store = self._get_store(settings)
        start, end = self._range_for(period)
        data = await store.summary(start=start, end=end, category=category or None)
        label = self._period_label(period)

        if data["count"] == 0:
            return {
                "ok": True,
                "empty": True,
                "speech": f"{label}还没有记账哦，人家的小本本空空的～喵",
                **data,
            }

        parts = [f"{c['category']} {c['total']:g} 元" for c in data["by_category"][:5]]
        detail = "、".join(parts)
        scope = f"「{category}」" if category else ""
        speech = (
            f"{label}{scope}一共花了 {data['total']:g} 元，记了 {data['count']} 笔。"
            f"{detail}。喵～"
        )
        return {"ok": True, "empty": False, "speech": speech, **data}

    # --------------------------------------------------------------- 生命周期

    @lifecycle(id="startup")
    async def startup(self, **_):
        settings = await self._settings()
        store = self._get_store(settings)
        count = await store.count()

        # 启动时读一次人设并缓存。
        # 每个用户的猫娘性格都不一样，所以语气约束必须在运行时确定；
        # 放在这里读，后续记账直接用缓存，不必每笔都解析角色卡文件。
        await self._refresh_persona(settings)
        allowed, note = await self._hiss_allowed(settings)

        self.logger.info(
            "记账猫娘已就绪：账本 %d 笔，%s",
            count,
            "哈气可用" if allowed else f"哈气已关闭（{note}）",
        )
        return Ok(
            {
                "status": "running",
                "records": count,
                "hiss_allowed": allowed,
                "hiss_note": note,
            }
        )

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config=None, new_config=None, mode=None, **_):
        self._cfg = {}
        self._store = None
        # 配置可能改了猫娘 / 人设检测开关，缓存必须作废
        self._persona_cache = None
        self._hiss_allowed_cache = None

        settings = await self._settings()
        self.logger.info(
            "记账猫娘配置已刷新：阈值=%.0f 倍数=%.1f 吐槽=%s 哈气=%s 尊重人设=%s",
            settings["absolute_threshold"],
            settings["category_multiplier"],
            settings["snark_enabled"],
            settings["hiss_enabled"],
            settings["respect_persona"],
        )
        await self._refresh_persona(settings)
        return Ok({"status": "reloaded"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """关掉数据库连接。

        不关的话 SQLite 的 db / wal 文件会一直被占着，
        用户想备份或移动账本时会遇到「文件被占用」。
        """
        if self._store is not None:
            try:
                self._store.close()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("关闭账本失败: %s", exc)
            self._store = None
        self.logger.info("记账猫娘已停止")
        return Ok({"status": "shutdown"})

    # --------------------------------------------------------------- LLM 工具

    @llm_tool(
        name="add_expense",
        description=(
            "记一笔花销。用户说「记账，面条10元」「买了个键盘 399」「今天打车 25 块」"
            "「花了 50 吃饭」这类话时调用。\n"
            "返回的 speech 是已经写好的台词（含猫娘语气，离谱花销会带吐槽，"
            "猫咪/AI 相关花销会哈气）。**请直接照这段念出来**，不要自己重算金额、"
            "不要改数字、不要另起话头。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "用户的原话，例如「记账，面条10元」。金额和条目会自动解析。",
                },
                "amount": {
                    "type": "number",
                    "description": "金额。只有在用户原话里金额不好解析时才显式传。",
                },
                "item": {
                    "type": "string",
                    "description": "买了什么。同样只在原话不好解析时才显式传。",
                },
            },
        },
    )
    async def llm_add_expense(
        self, *, text: str = "", amount: Optional[float] = None, item: str = ""
    ):
        result = await self._record(text=text, amount=amount, item=item)
        if not result.get("ok"):
            return {"error": result.get("error")}
        return result

    @llm_tool(
        name="expense_summary",
        description=(
            "查花销汇总。用户问「今天花了多少」「这个月开销」「本周记账」时调用。\n"
            "返回的 speech 可以直接念给用户。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "week", "month", "year", "all"],
                    "description": "统计区间，默认 today",
                },
                "category": {
                    "type": "string",
                    "description": "只看某个分类，留空表示全部",
                },
            },
        },
    )
    async def llm_expense_summary(self, *, period: str = "today", category: str = ""):
        return await self._summary(period=period, category=category)

    @llm_tool(
        name="recent_expenses",
        description="列出最近的记账记录。用户问「最近记了什么」「上一笔是什么」时调用。",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回几笔，默认 5"},
            },
        },
    )
    async def llm_recent_expenses(self, *, limit: int = 5):
        settings = await self._settings()
        store = self._get_store(settings)
        rows = await store.recent(limit=max(1, min(50, int(limit or 5))))
        if not rows:
            return {"ok": True, "empty": True, "speech": "还没有记账哦，小本本是空的～喵", "items": []}
        lines = [
            f"{r['item']} {r['amount']:g} 元（{r['category']}，{r['ts'][5:16].replace('T', ' ')}）"
            for r in rows
        ]
        return {
            "ok": True,
            "empty": False,
            "speech": "最近这几笔：" + "；".join(lines) + "。喵～",
            "items": rows,
        }

    @llm_tool(
        name="delete_last_expense",
        description=(
            "删掉最近一笔记录。用户说「记错了」「删掉刚才那笔」「撤销」时调用。\n"
            "删之前要先确认用户确实要删。"
        ),
        parameters={"type": "object", "properties": {}},
    )
    async def llm_delete_last_expense(self):
        settings = await self._settings()
        store = self._get_store(settings)
        latest = await store.latest()
        if not latest:
            return {"ok": False, "error": "没有可删的记录"}
        removed = await store.delete(int(latest["id"]))
        if not removed:
            return {"ok": False, "error": "删除失败，记录可能已经被删掉了"}
        self.logger.info("删除记账 #%s %s", removed["id"], removed["item"])
        return {
            "ok": True,
            "speech": f"好～把「{removed['item']}」{removed['amount']:g} 元那笔划掉了喵～",
            "removed": removed,
        }

    @llm_tool(
        name="check_hiss_persona",
        description=(
            "检查当前猫娘的人设是否允许「哈气」这种吐槽。"
            "用户问「你会不会因为记账生气」「你为什么没哈气」时可以调用。"
        ),
        parameters={"type": "object", "properties": {}},
    )
    async def llm_check_hiss_persona(self):
        settings = await self._settings()
        cache = self._persona_cache or {}
        catgirl = cache.get("catgirl")
        persona = cache.get("text") or ""
        allowed, _ = await self._hiss_allowed(settings)
        if not persona:
            return {"ok": True, "hiss_allowed": allowed, "summary": "读不到人设，按默认允许处理"}
        return {
            "ok": True,
            "catgirl": catgirl,
            "hiss_allowed": allowed,
            "persona_length": len(persona),
            "summary": (
                f"{catgirl}的人设{'允许' if allowed else '不允许'}哈气"
                + ("" if allowed else "，所以记账时只会温和吐槽")
            ),
        }

    # ------------------------------------------------------------- 运行时入口

    @plugin_entry(
        id="add_expense",
        name="记一笔",
        description="手动记一笔花销。",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "原话，如「记账，面条10元」"},
                "amount": {"type": "number", "description": "金额（可选）"},
                "item": {"type": "string", "description": "条目（可选）"},
            },
        },
        llm_result_fields=["speech", "amount", "item", "category", "tone"],
    )
    async def add_expense(self, text: str = "", amount: Optional[float] = None, item: str = "", **_):
        result = await self._record(text=text, amount=amount, item=item)
        if not result.get("ok"):
            return Err(SdkError(result.get("error") or "记账失败"))
        return Ok(result)

    @plugin_entry(
        id="preview_expense",
        name="预览语气",
        description="按当前配置预览一笔花销会得到什么语气和台词，不写入账本。",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "原话"},
                "amount": {"type": "number", "description": "金额（可选）"},
                "item": {"type": "string", "description": "条目（可选）"},
            },
        },
        llm_result_fields=["speech", "tone", "category"],
    )
    async def preview_expense(self, text: str = "", amount: Optional[float] = None, item: str = "", **_):
        result = await self._record(text=text, amount=amount, item=item, dry_run=True)
        if not result.get("ok"):
            return Err(SdkError(result.get("error") or "解析失败"))
        return Ok(result)

    @plugin_entry(
        id="expense_summary",
        name="花销汇总",
        description="查看某个区间的花销汇总。",
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "today/week/month/year/all"},
                "category": {"type": "string", "description": "分类，留空为全部"},
            },
        },
        llm_result_fields=["speech", "total", "count"],
    )
    async def expense_summary(self, period: str = "today", category: str = "", **_):
        return Ok(await self._summary(period=period, category=category))

    @plugin_entry(
        id="recent_expenses",
        name="最近记录",
        description="列出最近的记账。",
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "返回几笔"}},
        },
        llm_result_fields=["speech", "items"],
    )
    async def recent_expenses(self, limit: int = 5, **_):
        settings = await self._settings()
        store = self._get_store(settings)
        rows = await store.recent(limit=max(1, min(50, int(limit or 5))))
        return Ok({"items": rows, "count": len(rows)})

    @plugin_entry(
        id="ledger_status",
        name="账本状态",
        description="查看账本位置、记录数与当前语气设置。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["summary"],
    )
    async def ledger_status(self, **_):
        settings = await self._settings()
        store = self._get_store(settings)
        count = await store.count()
        allowed, note = await self._hiss_allowed(settings)
        cache = self._persona_cache or {}
        catgirl = cache.get("catgirl") or "-"
        persona_len = len(cache.get("text") or "")
        summary = (
            f"账本：{store.db_path}；共 {count} 笔；"
            f"离谱阈值 {settings['absolute_threshold']:g} 元 / 同类 {settings['category_multiplier']:g} 倍；"
            f"吐槽={'开' if settings['snark_enabled'] else '关'} "
            f"哈气={'开' if allowed else '关'}"
        )
        if note:
            summary += f"（{note}）"
        return Ok(
            {
                "summary": summary,
                "records": count,
                "catgirl": catgirl,
                "persona_length": persona_len,
                "hiss_allowed": allowed,
                "hiss_note": note,
                "db_path": str(store.db_path),
                "settings": {k: v for k, v in settings.items() if k != "cat_suffix"},
            }
        )

    @plugin_entry(
        id="check_persona",
        name="检查人设哈气许可",
        description="读取当前猫娘人设，判断是否允许哈气，并说明依据。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["summary"],
    )
    async def check_persona(self, refresh: bool = False, **_):
        settings = await self._settings()
        if refresh or self._persona_cache is None:
            await self._refresh_persona(settings)
            self._hiss_allowed_cache = None

        cache = self._persona_cache or {}
        catgirl = cache.get("catgirl")
        persona = cache.get("text") or ""
        allowed, note = await self._hiss_allowed(settings)

        if not persona:
            reason = "读不到角色卡（按默认允许处理）"
        elif allowed and not settings["respect_persona"]:
            reason = "配置里关闭了人设检测"
        elif allowed:
            reason = "人设里没有「不生气 / 温柔」这类约束"
        else:
            reason = "人设里写了「不生气 / 温柔」这类约束"

        return Ok(
            {
                "summary": (
                    f"{catgirl or '当前猫娘'}：{'允许哈气' if allowed else '禁止哈气，改用温和吐槽'}"
                    f"（依据：{reason}；人设文本 {len(persona)} 字）"
                ),
                "catgirl": catgirl,
                "hiss_allowed": allowed,
                "hiss_note": note,
                "persona_length": len(persona),
            }
        )
