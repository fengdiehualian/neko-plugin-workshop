"""fail-to-pass 证据：把三处**旧行为**重新注入，确认新测试真的会红。

`tests/test_qq_bored_emotion.py` 与 `test_qq_emotion_vocabulary.py` 首次运行就全绿，
这本身不构成证据 —— 全绿的测试可能什么都没测。这个脚本把每个修复点改回旧实现，
然后重跑对应的断言，期望它们**失败**。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_bored_fail_to_pass.py
"""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # N.E.K.O 仓库根

from plugin.plugins.qq_auto_reply import attention_service  # noqa: E402
from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_qq_bored_emotion import LIVE, STALE_CONFIG_TABLE, _focus_of, _seed  # noqa: E402

_results: list[tuple[str, bool, str]] = []


def _check(name: str, *, old_behaviour_reproduced: bool, detail: str) -> None:
    _results.append((name, old_behaviour_reproduced, detail))
    mark = "OK  " if old_behaviour_reproduced else "MISS"
    print(f"[{mark}] {name}: {detail}")


def _service(emotion_table):
    settings = dict(LIVE)
    settings["attention_emotion_multipliers"] = dict(emotion_table)
    plugin = SimpleNamespace(
        group_permission_mgr=SimpleNamespace(
            list_groups=lambda: [{"group_id": g} for g in ("A", "B")]
        ),
        _qq_settings=settings,
        backlog_store=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    svc._current_time = lambda: 100_000
    return svc


# ── 1. 配置表「没有键 = 倍率 0」→ 新情绪静默失效 ──────────────────────

def old_emotion_multipliers(self):
    """修复前的实现：不做覆盖表补齐，只有用户写过的键。"""
    raw = self._setting("attention_emotion_multipliers", None)
    if not isinstance(raw, dict) or not raw:
        return dict(attention_service._EMOTION_MULTIPLIER)
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            return dict(attention_service._EMOTION_MULTIPLIER)
    return out


def check_stale_config():
    svc = _service(STALE_CONFIG_TABLE)
    svc._emotion_multipliers = old_emotion_multipliers.__get__(svc, QQAttentionService)
    assert "bored" not in STALE_CONFIG_TABLE
    _seed(svc, "A", 8.0, now=100_000)
    asyncio.run(svc.set_emotion("A", "bored"))
    got = svc._load_state("A").emotion
    _check(
        "老配置缺 bored 键 → 旧实现下 bored 完全无反应",
        old_behaviour_reproduced=(got == "calm"),
        detail=f"emotion={got!r}（新实现应为 'bored'）",
    )


# ── 2. 降温阶梯漏掉 proud + 硬编码上升侧名单 → 一步归零 ───────────────

def old_decay_emotion(self, state, now):
    """修复前的实现：阶梯里没有 proud，且上升侧名单是硬编码的四个。"""
    if state.emotion == "calm":
        return
    if now - state.emotion_updated_at < attention_service._EMOTION_DECAY_SECONDS:
        return
    old_order = [n for n in attention_service._EMOTION_DECAY_ORDER if n != "proud"]
    order = old_order
    idx = order.index(state.emotion) if state.emotion in order else -1
    if idx < 0:
        state.emotion = "calm"
    elif state.emotion in ("arguing", "annoyed", "playful", "curious"):
        if idx + 1 >= order.index("calm"):
            state.emotion = "calm"
        else:
            state.emotion = order[idx + 1]
    else:
        calm_idx = order.index("calm")
        if idx - 1 <= calm_idx:
            state.emotion = "calm"
        else:
            state.emotion = order[idx - 1]
    state.emotion_updated_at = now
    state.emotion_display = state.emotion


def check_proud_decay():
    svc = _service(copy.deepcopy(attention_service._EMOTION_MULTIPLIER))
    svc._decay_emotion = old_decay_emotion.__get__(svc, QQAttentionService)
    st = svc._load_state("A")
    st.emotion = "proud"
    st.emotion_updated_at = 100_000
    svc._decay_emotion(st, 100_000 + attention_service._EMOTION_DECAY_SECONDS + 1)
    _check(
        "旧实现下 proud 一步归零（跳过 annoyed/playful/curious）",
        old_behaviour_reproduced=(st.emotion == "calm"),
        detail=f"proud → {st.emotion!r}（新实现应为 'annoyed'）",
    )


# ── 3. bored 不在让焦点集合 → 焦点不动 ───────────────────────────────

def check_bored_drop_focus():
    svc = _service(copy.deepcopy(attention_service._EMOTION_MULTIPLIER))
    svc.plugin._qq_settings["attention_emotion_multipliers"]["bored"] = (
        attention_service._EMOTION_MULTIPLIER["bored"]
    )
    now = 100_000
    _seed(svc, "A", 6.0, now=now)
    _seed(svc, "B", 5.0, now=now)
    # 只摘掉「让焦点」这一档行为，保留倍率 —— 复现修复前 bored 根本不存在的样子
    original = set(attention_service._EMOTION_DROP_FOCUS)
    attention_service._EMOTION_DROP_FOCUS.discard("bored")
    try:
        asyncio.run(svc.set_emotion("A", "bored"))
        focus = _focus_of(svc, SimpleNamespace(now=now))
        score = svc._load_state("A").attention_score
    finally:
        attention_service._EMOTION_DROP_FOCUS.clear()
        attention_service._EMOTION_DROP_FOCUS.update(original)
    _check(
        "bored 不在让焦点集合时，焦点留在没兴趣的群",
        old_behaviour_reproduced=(focus == "A"),
        detail=f"focus={focus!r} score={score:.2f}（新实现应为 'B'）",
    )


def main() -> int:
    check_stale_config()
    check_proud_decay()
    check_bored_drop_focus()
    print()
    missed = [name for name, ok, _ in _results if not ok]
    if missed:
        print(f"[FAIL] {len(missed)} 项未能复现旧行为，说明对应测试可能是空测：")
        for name in missed:
            print(f"   - {name}")
        return 1
    print(f"[PASS] {len(_results)} 项旧行为全部复现 —— 对应测试确实会因修复而由红转绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
