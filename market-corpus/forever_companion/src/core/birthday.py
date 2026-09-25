"""生日轻语（1.3.1）数据层：纯函数，零 SDK 依赖（与 core/ 其他模块同一纪律）。

主人生日日期存全局配置（``[birthday].date``，"YYYY-MM-DD"，时光页生日设置卡经
update_settings 写入 settings 覆盖层）。**年份只用于校验日期合法性，从不
参与年龄计算**——插件不告诉她几岁、第几个生日，只告诉她"今天是他的生日"。

当天判定按各角色的本地日期字符串（YYYY-MM-DD，与纪念日水位同一口径）：
- 去重水位复用 per-shard stats blob（``stats["birthday"]["last_pushed"]``），
  与 ``stats["anniversary"]`` 同构：只判定不改水位，推送成功后由调用方落盘；
- 闰日规则：生日是 02-29 时平年按 02-28 过（宁早勿漏，绝不拖到 03-01）；
- 未设置 / 空串 / 非法日期一律按"未设置"处理（fail-closed：宁可不提醒，
  不能对着坏数据提醒），合法性由面板写入时校验，读取端只做容忍。

纪念条目（keep_diary 开启时生日当天写进时光日记）以她的手记形态
（source=self）落在时间线里，插件代笔一事在 README/功能介绍里如实说明。
"""

from __future__ import annotations

from datetime import date
from typing import Any

JsonObject = dict[str, Any]

# 生日纪念手记的固定文案（进她的时间线留档；中文原文与注入文案同一语言面，
# 不进面板 i18n 通路——与阶段提示词/纪念日轻语同类：消费者是她和记忆管线）
_BIRTHDAY_DIARY_ENTRY = (
    "今天是主人的生日。我在心里认认真真祝了他一次生日快乐——"
    "这个日子我记下了，以后每年都记。"
)


def parse_birthday(raw: object) -> tuple[int, int] | None:
    """把配置的生日串解析为 (month, day)；空/非法返回 None。

    接受 "YYYY-MM-DD"（面板 date 输入的标准形态）与 "MM-DD" 两种写法；
    用真日历校验月日组合（2-30 这种必须倒下，不能到生日当天才发现）。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    parts = text.split("-")
    try:
        if len(parts) == 3:
            year, month, day = (int(p) for p in parts)
        elif len(parts) == 2:
            year, month, day = 2000, int(parts[0]), int(parts[1])  # 闰年探针
        else:
            return None
        date(year if len(parts) == 3 else 2000, month, day)
        return (month, day)
    except ValueError:
        return None


def normalize_birthday(raw: object) -> str:
    """校验并归一化待保存的生日串：零填充 "YYYY-MM-DD" 或 ""（清除）。

    抛 ValueError 由调用方（update_settings）转稳定码；"MM-DD" 输入补年份
    2000 存全（显示层只取月日，年份无年龄语义）；"1995-8-17" 补零成
    "1995-08-17"（面板原生 date input 只认零填充形，不归一编辑框会画空）。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    parts = text.split("-")
    parsed = parse_birthday(text)
    if parsed is None:
        raise ValueError(f"invalid birthday date: {text!r}")
    month, day = parsed
    year = int(parts[0]) if len(parts) == 3 else 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def _observed(year: int, month: int, day: int) -> date:
    """该年的实际生日日：闰日在平年回落 02-28。"""
    if month == 2 and day == 29 and not _is_leap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _as_date(today: object) -> date | None:
    """today 接受 "YYYY-MM-DD" 串或 date 对象（与 core/stats 水位口径同源）。"""
    if isinstance(today, date):
        return today
    try:
        return date.fromisoformat(str(today or "").strip())
    except ValueError:
        return None


def birthday_is_today(birthday: object, today: object) -> bool:
    """今天是否是（按闰日规则折算后的）生日。坏数据一律 False。"""
    parsed = parse_birthday(birthday)
    today_date = _as_date(today)
    if parsed is None or today_date is None:
        return False
    month, day = parsed
    observed = _observed(today_date.year, month, day)
    return today_date.month == observed.month and today_date.day == observed.day


def birthday_due(stats: JsonObject, today: object) -> bool:
    """当天是否还没推过生日轻语（只查水位不改水位；生日与否由调用方判定）。

    与 anniversary_due 同构：盖水位必须发生在推送成功之后，推送失败
    下趟（下一条消息）还能重试。
    """
    today_date = _as_date(today)
    if today_date is None:
        return False
    block = stats.get("birthday") if isinstance(stats.get("birthday"), dict) else {}
    return str(block.get("last_pushed") or "") != today_date.isoformat()


def mark_birthday_pushed(stats: JsonObject, today: object) -> JsonObject:
    """盖当天水位（返回新 dict，不原地改；与 mark_anniversary_pushed 同款）。"""
    fresh = dict(stats)
    today_date = _as_date(today)
    day_str = today_date.isoformat() if today_date else str(today or "")
    fresh["birthday"] = {
        **dict(fresh.get("birthday") if isinstance(fresh.get("birthday"), dict) else {}),
        "last_pushed": day_str,
    }
    return fresh


def make_birthday_diary_record(ts_iso: str, phase: str) -> JsonObject:
    """生日当天的时光日记纪念条目（她的手记形态，插件代笔、固定文案）。

    与 mood_drift_bottle 落的条目同形（ts/source/phase/mood/entry），额外带
    kind="birthday" 供未来按类检索；时间线对未知字段宽容（diary.tsx 只认既有键）。
    """
    return {
        "ts": str(ts_iso),
        "source": "self",
        "kind": "birthday",
        "phase": str(phase or ""),
        "mood": "开心",
        "entry": _BIRTHDAY_DIARY_ENTRY,
    }
