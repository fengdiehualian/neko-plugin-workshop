from __future__ import annotations

import re
from typing import Any

from .heuristic_planner import STS2HeuristicPlanner

# 地图节点在 mod 侧是**引擎枚举**（`RestSite` / `Monster` / `SecondBoss`）。以前这个枚举被直接
# 塞进台词，实测线上念出的是「可以优先考虑：RestSite」—— 直播里这就是穿帮。这里给它一套人话。
# 键与 `heuristic_planner._MAP_NODE_TYPE_ALIASES` 的**取值**对齐（权重键 = 显示键的规范化形式），
# 由 `tests/test_companion_text_leaks.py` 盯着两边不脱节。
_MAP_NODE_TYPE_LABELS: dict[str, str] = {
    "monster": "普通敌人",
    "elite": "精英",
    "shop": "商店",
    "rest": "营地",
    "treasure": "宝箱",
    "question": "未知房间",
    "event": "事件",
    "boss": "Boss",
    "ancient": "远古",
}
_MAP_NODE_TYPE_ALIASES: dict[str, str] = {
    "monster": "monster",
    "enemy": "monster",
    "fight": "monster",
    "elite": "elite",
    "shop": "shop",
    "merchant": "shop",
    "restsite": "rest",
    "rest": "rest",
    "campfire": "rest",
    "treasure": "treasure",
    "chest": "treasure",
    "unknown": "question",
    "question": "question",
    "event": "event",
    "boss": "boss",
    "secondboss": "boss",
    "ancient": "ancient",
}

# 纯 ASCII 的 snake_case / kebab-case = 内部 id（`end_turn`、`GUILDING_STAR`、`play_card`）。
_RAW_ID_RE = re.compile(r"^[A-Za-z0-9]+(?:[_\-\s][A-Za-z0-9]+)*$")

# 模板句尾自带标点，名字自带标点就会叠成「下去！。」或「药水的未来？，建议…」。
_TRAILING_PUNCT = "。．.，,、；;：:！!？?~～…"


def _trim_trailing_punct(text: Any) -> str:
    """剥掉名字末尾的标点，交给模板去收句（`下去！` + `。` = 「下去！」）。"""
    return str(text or "").strip().rstrip(_TRAILING_PUNCT).strip()


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _normalize_node_type(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    for char in ("_", "-", " "):
        key = key.replace(char, "")
    return _MAP_NODE_TYPE_ALIASES.get(key, key)


def _display_node_type(raw: Any) -> str:
    """把节点枚举翻成人话；认不出来就退化成去掉下划线的可读写法（至少不是枚举名）。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if _has_cjk(text):
        return text
    label = _MAP_NODE_TYPE_LABELS.get(_normalize_node_type(text), "")
    return label or _display_name(text)


def _display_name(raw: Any) -> str:
    """卡牌/遗物名：中文原样保留，纯 id 退化成 `guiding star` 这种可读写法（别念 GUILDING_STAR）。

    CamelCase 也要拆（`SomeNewNode` -> `Some New Node`）：引擎将来加了新枚举，至少不会原样穿帮。
    """
    text = str(raw or "").strip()
    if not text or _has_cjk(text):
        return text
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text.replace("_", " ").replace("-", " "))
    # 全大写的 id（`GUILDING_STAR`）拆完还是在大喊，一并降下来：`guiding star`。
    if not any(char.islower() for char in spaced):
        spaced = spaced.lower()
    return " ".join(spaced.split())


def _clean_preference(raw: Any) -> str:
    """策略偏好串**只有在是说给人听的时候**才能念。

    实测线上出现过 `偏好这类选择：end_turn` —— 前半句是脚手架、后半句是动作 id，
    念出来就是穿帮。判据：取最后一段，若是纯 ASCII 内部 id 或整条不含中文，就整条作废，
    让调用方退到"念具体牌名"或默认短句。
    """
    text = str(raw or "").strip()
    if not text or not _has_cjk(text):
        return ""
    tail = re.split(r"[：:]", text)[-1].strip()
    if tail and not _has_cjk(tail) and _RAW_ID_RE.match(tail):
        return ""
    return tail if _has_cjk(tail) else text


# 敌人这一手要干什么。mod 给的是 `intents[].intent_type`（结构化）+ `enemy.intent`（招式 id）。
_INTENT_TYPE_LABELS: dict[str, str] = {
    "attack": "攻击",
    "deathblow": "致命一击",
    "defend": "防御",
    "block": "防御",
    "buff": "强化",
    "debuff": "弱化",
    "statuscard": "塞牌",
    "sleep": "睡眠",
    "stun": "眩晕",
    "escape": "逃跑",
}
# "还没决定 / 没数据"的哨兵值 —— 这些**什么都不说**，别把 `UNSET_MOVE` 念出来给人听。
_INTENT_UNKNOWN_KEYS = frozenset({"", "unset", "unsetmove", "none", "null", "unknown", "unknownmove"})


def _normalize_token(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    for char in ("_", "-", " "):
        token = token.replace(char, "")
    return token


def _enemy_intent_label(enemy: Any, raw_enemy: Any = None) -> str:
    """敌人意图的说人话版本（线上实测念过 `敌:树叶史莱姆（中）/STICKY_SHOT`）。

    优先用 `raw_state` 里的结构化 `intents[]`（`intent_type` + 伤害）—— 那是 mod 的权威来源，
    能给出「攻击 9」这种玩家真看得懂的信息；拿不到才退到招式 id 的可读化写法；
    `UNSET_MOVE` 这类哨兵值直接不念。
    """
    structured = raw_enemy if isinstance(raw_enemy, dict) else enemy
    intents = structured.get("intents") if isinstance(structured, dict) and isinstance(structured.get("intents"), list) else []
    # 有结构化数据就以它为准：它说"不知道"就**不猜**，别再退回招式 id 硬编一句。
    if intents:
        for item in intents:
            if not isinstance(item, dict):
                continue
            key = _normalize_token(item.get("intent_type"))
            # `Unknown` 也是"还没定"——说"意图不明"等于没说，不如只留敌人名字。
            if not key or key in _INTENT_UNKNOWN_KEYS:
                continue
            label = _INTENT_TYPE_LABELS.get(key, "")
            if not label:
                continue
            total = item.get("total_damage")
            if isinstance(total, (int, float)) and not isinstance(total, bool) and float(total) > 0:
                hits = item.get("hits")
                if isinstance(hits, (int, float)) and not isinstance(hits, bool) and int(hits) > 1:
                    return f"{label} {int(total)}x{int(hits)}"
                return f"{label} {int(total)}"
            return label
        return ""
    if not isinstance(enemy, dict):
        return ""
    raw = str(enemy.get("intent_type") or enemy.get("intent") or enemy.get("move_id") or "").strip()
    key = _normalize_token(raw)
    if key in _INTENT_UNKNOWN_KEYS:
        return ""
    return _INTENT_TYPE_LABELS.get(key, "") or _display_name(raw)


class STS2CompanionEvaluator:
    #: ════════════════════════════════════════════════════════════════════════
    #: ★★ 人称契约（VOICE CONTRACT）—— 冻结区，改文案前必读
    #: ════════════════════════════════════════════════════════════════════════
    #:
    #: 玩家指示（2026-10-18）：
    #:   「整个插件都需要是第一人称，注意一下包裹关系」
    #:   「插件就是猫娘的反射弧，没人会把反射作为第三者吧」
    #:
    #: 核心不变量：**猫娘是唯一主体。插件不是她的代言人，是她的一部分。**
    #:   绝不出现「插件」「求解器」作为第三方在说话；也不出现「建议…理由：…」
    #:   这种**转达/汇报**口吻 —— 那等于把反射弧说成身外之物。
    #:
    #: ── 三层包裹关系（**只有 A/B 用第一人称，C 必须保持第三人称**）──────
    #:
    #: 【A 层】猫娘 → 玩家 直接说的话          ⇒ **第一人称「我」**
    #:     companion.suggestion.* / companion.eval.* / companion.reminder.*
    #:     判据：这段话是猫娘对玩家说的。
    #:       ✗「建议这回合先出 打击。理由：…」   ← 汇报口吻
    #:       ✓「这回合我先打『打击』」           ← 她自己的动作
    #:
    #: 【B 层】喂给猫娘自己的上下文（她读的局面）  ⇒ **第一人称「我」**
    #:     summary.*
    #:     判据：猫娘就在这个局面里，「玩家」是把自己当外人的错误视角。
    #:       ✗「当前战斗状态，玩家血量 {hp}…」
    #:       ✓「当前战斗状态，我的血量 {hp}…」
    #:
    #: 【C 层】宿主/系统在**描述**猫娘              ⇒ **保持第三人称「她」**
    #:     entries.*（NEKO 的按钮标签）/ plugin.* / tools.* / errors.* / status.*
    #:     判据：这是**玩家在操作猫娘**的界面文字，不是猫娘说的话。
    #:       ✓「让她自己玩起来」  ← 玩家按的按钮
    #:       ✗「我要自己玩起来」  ← 主语错位（那是猫娘的自言自语，不是按钮）
    #:
    #: ★ 判别口诀：**这段字是"猫娘对玩家说"还是"宿主在对玩家描述猫娘"？**
    #:   前者第一人称，后者第三人称。**不要无差别全局替换。**
    #:
    #: ⚠️ 修改文案时**必须同时改两处**，否则会静默漂移：
    #:   1) i18n/*.json 里的值
    #:   2) 代码里的 `default="..."` 内联兜底（`self.t(key, default=...)`）
    #:   两者不一致时，i18n 命中就用 JSON、缺失就用 default —— 漏改一处
    #:   会在语言切换/键缺失时露出错误的旧口吻。
    #: ════════════════════════════════════════════════════════════════════════

    def __init__(self, i18n: Any) -> None:
        self._i18n = i18n
        self._planner = STS2HeuristicPlanner()

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    def evaluate(
        self,
        *,
        summary_context: dict[str, Any],
        situation_summary: dict[str, Any],
        strategy_context: dict[str, Any],
        runtime_state: Any | None = None,
    ) -> dict[str, Any]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        summary_kind = str(situation_summary.get("kind") or summary_context.get("summary_kind") or "general")
        strategy_name = str(strategy_context.get("strategy_name") or "unknown")
        directives = strategy_context.get("strategy_directives") if isinstance(strategy_context.get("strategy_directives"), dict) else {}
        player_operation_observation = dict(getattr(runtime_state, "latest_player_operation_observation", {}) or {}) if runtime_state is not None else {}

        risk_level = self._risk_level(summary_kind=summary_kind, payload=payload)
        focus = self._focus(summary_kind=summary_kind, payload=payload)
        trigger, turn_key, scene_key, evaluation_key = self._trigger_state(
            summary_kind=summary_kind,
            payload=payload,
            runtime_state=runtime_state,
            player_operation_observation=player_operation_observation,
        )
        suggestion = self._suggestion(summary_kind=summary_kind, payload=payload, directives=directives, trigger=trigger, player_operation_observation=player_operation_observation)
        evaluation = self._evaluation(summary_kind=summary_kind, risk_level=risk_level, focus=focus, strategy_name=strategy_name, trigger=trigger, player_operation_observation=player_operation_observation)
        reminder = self._reminder(summary_kind=summary_kind, payload=payload, directives=directives, trigger=trigger)
        should_comment = self._should_comment(
            trigger=trigger,
            turn_key=turn_key,
            scene_key=scene_key,
            evaluation_key=evaluation_key,
            runtime_state=runtime_state,
            player_operation_observation=player_operation_observation,
        )

        monster_intel = self._monster_intel(summary_kind=summary_kind, payload=payload)
        pieces = [piece for piece in [monster_intel, suggestion] if piece]
        commentary = "；".join(pieces)
        if len(commentary) > 220:
            commentary = commentary[:217].rstrip("；， ") + "..."

        primary_message = commentary
        primary_message = self._with_card_cost(primary_message, payload=payload)
        final_should_comment = bool(primary_message) and should_comment

        if runtime_state is not None and hasattr(runtime_state, "last_companion_evaluation_key"):
            try:
                runtime_state.last_companion_evaluation_key = str(evaluation_key or runtime_state.last_companion_evaluation_key)
            except Exception:
                pass

        return {
            "strategy_name": strategy_name,
            "summary_kind": summary_kind,
            "risk_level": risk_level,
            "focus": focus,
            "evaluation": evaluation,
            "reminder": reminder,
            "suggestion": suggestion,
            "commentary": commentary,
            "primary_message": primary_message,
            "should_comment": final_should_comment,
            "trigger": trigger,
            "turn_key": turn_key,
            "scene_key": scene_key,
            "evaluation_key": evaluation_key,
            "source": "strategy_companion",
            "player_operation_observation": dict(player_operation_observation),
        }

    def _risk_level(self, *, summary_kind: str, payload: dict[str, Any]) -> str:
        if summary_kind == "combat":
            player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
            enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
            current_hp = self._safe_int(player.get("current_hp"))
            max_hp = max(1, self._safe_int(player.get("max_hp"), default=1))
            block = self._safe_int(player.get("block"))
            incoming = sum(self._safe_int(enemy.get("intent_damage")) for enemy in enemies if isinstance(enemy, dict))
            hp_ratio = current_hp / max_hp if max_hp > 0 else 0.0
            if incoming - block >= current_hp or hp_ratio <= 0.25:
                return "high"
            if incoming > block or hp_ratio <= 0.45:
                return "medium"
            return "low"
        if summary_kind in {"event", "reward", "selection", "shop", "rest", "map"}:
            return "medium"
        return "low"

    def _focus(self, *, summary_kind: str, payload: dict[str, Any]) -> str:
        """★ 2026-10-19 第一人称改造：这一组原来有**主语残缺**的旁白腔
        （"先看…"、"优先看…"），读起来像系统在列检查项，不像猫娘在说自己想什么。
        ⇒ 统一补上「我想」。

        ⚠️ 这 8 个键**在 JSON 里没有条目**（只存在于内联 default），
           所以只改 JSON 的手法是碰不到它们的 —— 它们是靠契约测试
           （`test_ab_layer_has_first_person_viewpoint`）才被查出来的。
        """
        if summary_kind == "combat":
            return self.t("companion.focus.combat", default="我想先看生存、格挡和怪物意图")
        if summary_kind == "reward":
            return self.t("companion.focus.reward", default="我想看它贴不贴合当前构筑方向")
        if summary_kind == "selection":
            return self.t("companion.focus.selection", default="我想看长期精简和构筑质量")
        if summary_kind == "map":
            return self.t("companion.focus.map", default="我想看路线风险和成长窗口")
        if summary_kind == "event":
            return self.t("companion.focus.event", default="我想看代价和长期收益")
        if summary_kind == "shop":
            return self.t("companion.focus.shop", default="我想看金币效率和删牌机会")
        if summary_kind == "rest":
            return self.t("companion.focus.rest", default="我想看保血还是升级更值")
        return self.t("companion.focus.general", default="我想看当前局势和下一步选择")

    def _evaluation(self, *, summary_kind: str, risk_level: str, focus: str, strategy_name: str, trigger: str, player_operation_observation: dict[str, Any]) -> str:
        if player_operation_observation:
            observation_summary = str(player_operation_observation.get("summary") or "").strip()
            if observation_summary:
                # ★ "玩家刚完成操作" → "你刚完成操作"：猫娘在对玩家说话，不是旁白在第三人称报告
                return self.t("companion.eval.player_operation", default="你刚完成操作：{summary}。", summary=observation_summary)
        prefix = {
            "high": self.t("companion.eval.high", default="当前局势偏危险"),
            "medium": self.t("companion.eval.medium", default="当前局势需要仔细取舍"),
            "low": self.t("companion.eval.low", default="当前局势相对平稳"),
        }.get(risk_level, self.t("companion.eval.default", default="当前局势需要观察"))
        template = self.t("companion.eval.template", default="{prefix}，按 {strategy_name} 策略我想 {focus}。", prefix=prefix, strategy_name=strategy_name, focus=focus)
        if trigger == "combat_turn":
            return self.t("companion.eval.combat_turn", default="回合更新：{template}", template=template)
        if trigger == "scene_entry":
            return self.t("companion.eval.scene_entry", default="新局面提示：{template}", template=template)
        if trigger == "post_action_eval":
            return self.t("companion.eval.post_action", default="这一步之后看，{template}", template=template)
        return template

    def _monster_intel(self, *, summary_kind: str, payload: dict[str, Any]) -> str:
        if summary_kind != "combat":
            return ""
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        parts: list[str] = []
        for enemy in enemies[:2]:
            if not isinstance(enemy, dict):
                continue
            name = str(enemy.get("name") or enemy.get("enemy_id") or "敌人")
            intent = str(enemy.get("intent") or enemy.get("move_id") or "")
            intent_damage = self._safe_int(enemy.get("intent_damage"))
            if intent_damage > 0:
                parts.append(f"{name}{intent_damage}伤")
            elif intent:
                parts.append(f"{name}{intent}")
            else:
                parts.append(name)
        return "；".join(parts)

    def _combat_tactical_advice(self, payload: dict[str, Any]) -> str:
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        block = self._safe_int(player.get("block"))
        incoming = sum(self._safe_int(enemy.get("intent_damage")) for enemy in enemies if isinstance(enemy, dict))
        enemy_intents = [str(enemy.get("intent") or enemy.get("move_id") or "") for enemy in enemies if isinstance(enemy, dict)]
        if any(intent in {"SHRINKER_MOVE", "CHARGE_UP_MOVE", "ENERGY_ORB_MOVE"} for intent in enemy_intents):
            return self.t("companion.suggestion.combat_intent_window", default="这回合我想抓机会输出。")
        if incoming > block:
            return self.t("companion.suggestion.combat_defense", default="我想先垫防御，或者找条减伤线。")
        return self.t("companion.suggestion.combat_value", default="我想找收益更高的出牌线。")

    def _combat_solver_suggestion(self, payload: dict[str, Any], directives: dict[str, Any]) -> str:
        """战斗台词：**说求解器的选择**，用第一人称。

        ★ 2026-10-18 重写（原 `_combat_heuristic_suggestion`）。
        玩家指示：
          「战斗完全交给求解器。战斗以外的才需要启发式评分。」
          「猫娘点评不能出现插件说这种转达的字眼，插件就是猫娘的反射弧，
            没人会把反射作为第三者吧」

        ⇒ 两处语义修正：
          1. **意见源**：不再用启发式评分挑牌（那是第二个意见源，会和实际出牌打架），
             改为念**求解器**真正要打的那一步 —— 她说的话和她的手是同一个决定。
          2. **人称**：不再有「建议……理由：……」这种报告口吻，改为
             「这回合我打『X』」—— 她是主体，不是分析工具的输出端。

        ★ 求解器不可用时的降级（`_combat_voice`）：
          拿不到求解器计划时**不编造具体牌名**（编了就会和实际出牌不符，
          反而暴露"她说的和做的不是一回事"）。此时只说局势层面的短句。
        """
        plan = payload.get("solver_plan") if isinstance(payload.get("solver_plan"), dict) else None
        step = None
        if plan and plan.get("in_combat"):
            line = plan.get("line") if isinstance(plan.get("line"), list) else []
            steps = (line[0].get("steps") if line and isinstance(line[0], dict) else None) or []
            if steps and isinstance(steps[0], dict):
                step = steps[0]
        if not isinstance(step, dict):
            return ""
        kind = str(step.get("kind") or "").strip()

        if kind == "end_turn":
            return self.t(
                "companion.solver.end_turn",
                default="这回合就这样，我收手了。",
            )
        if kind == "use_potion":
            potion_name = _trim_trailing_punct(
                _display_name(step.get("card_name") or step.get("choice_card_name") or step.get("card_id") or "")
            )
            if not potion_name:
                return ""
            return self.t(
                "companion.solver.use_potion",
                default="我先喝口『{potion_name}』。",
                potion_name=potion_name,
            )
        if kind != "play_card":
            return ""

        card_name = _trim_trailing_punct(_display_name(step.get("card_name") or step.get("card_id") or ""))
        if not card_name:
            return ""
        target_name = self._solver_step_target_name(payload, step)
        if target_name:
            return self.t(
                "companion.solver.play_card_with_target",
                default="这回合我打『{card_name}』，招呼 {target_name}。",
                card_name=card_name,
                target_name=target_name,
            )
        return self.t(
            "companion.solver.play_card",
            default="这回合我打『{card_name}』。",
            card_name=card_name,
        )

    def _solver_step_target_name(self, payload: dict[str, Any], step: dict[str, Any]) -> str:
        """把求解器步里的 target_index 翻成敌人名（念名字才有"在场"感）。

        ★★ 缺陷防御（2026-10-18 实测复现后修）：**必须区分"没目标"与"目标是 0 号"**。

        原实现用 `self._safe_int(step.get("target_index"))`，而 `_safe_int(None) == 0`
        （实测），于是：
            防御牌 + target_index=None  →  被念成「招呼 异螨」  ❌ 编造目标
            防御牌 + 无 target_index 键 →  同样被念成「招呼 异螨」❌
            打击牌 + target_index=0     →  「招呼 异螨」         ✅ 唯一正确的情况

        两种输入被折叠成同一个 0，**信息在进函数前就丢了**，所以判据不能是"数值"，
        必须是"键在不在 / 值是不是 null"。

        为什么这条特别要紧：弹幕是陪玩玩家**唯一**能看到的信息渠道（玩家自己打、
        看不到求解器）。念错目标 = 让他去打错敌人。宁可不念目标，也不能念错。
        """
        if "target_index" not in step:
            return ""
        raw = step.get("target_index")
        if raw is None or raw == "":
            return ""
        idx = self._safe_int(raw)
        if idx is None:
            return ""
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        if not (0 <= idx < len(enemies)):
            return ""
        enemy = enemies[idx]
        if not isinstance(enemy, dict):
            return ""
        return _trim_trailing_punct(_display_name(enemy.get("name") or enemy.get("enemy_id") or ""))

    def _suggestion(self, *, summary_kind: str, payload: dict[str, Any], directives: dict[str, Any], trigger: str, player_operation_observation: dict[str, Any]) -> str:
        """★ 2026-10-19 第一人称改造：这一组原来是「**建议**先看…」的旁白腔。

        ⚠️ 与 `_focus` 一样，这 4 个键**在 JSON 里没有条目**（只在内联 default），
           靠契约测试（`test_ab_layer_code_defaults_are_also_first_person`）查出。
        """
        if player_operation_observation:
            event_type = str(player_operation_observation.get("event_type") or "")
            if event_type == "combat_ended":
                return self.t("companion.suggestion.player_operation.combat_ended", default="我想先看看奖励、遗物和后面的路线，不急着无脑往下走。")
            if event_type == "choice_committed":
                return self.t("companion.suggestion.player_operation.choice_committed", default="我想顺着这次选择继续看局势，确认它贴不贴合当前构筑方向。")
            if event_type == "combat_turn_advanced":
                return self.t("companion.suggestion.player_operation.turn_advanced", default="新回合开始了，我想先看敌方意图、能量和能打的牌。")
            if event_type == "player_card_or_action_committed":
                return self.t("companion.suggestion.player_operation.action_committed", default="这步已经落下，我想接着看伤害交换、护盾变化和下一张牌的收益。")
        if summary_kind == "combat":
            # ★ 战斗台词 = 求解器要打的那一步（见 _combat_solver_suggestion 的说明）。
            #   拿不到求解器计划就退到局势短句，**不编造牌名**。
            solver_suggestion = self._combat_solver_suggestion(payload, directives)
            if solver_suggestion:
                return self._post_action_wrap(solver_suggestion, trigger)
            return self._combat_voice(payload)
        if summary_kind == "reward":
            reward_cards = payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []
            reward_card_names = payload.get("reward_card_names") if isinstance(payload.get("reward_card_names"), list) else []
            preferred = directives.get("prefer") if isinstance(directives.get("prefer"), list) else []
            # 策略偏好先过一遍"能不能念"的闸：念不出来就退到下面的具体牌名（线上实测过
            # `偏好这类选择：end_turn` 这种漏 id 的串）。
            preferred_text = _clean_preference(preferred[0]) if preferred else ""
            preferred_index = self._preferred_reward_index(payload, directives)
            if preferred_text:
                base = self.t("companion.suggestion.reward_preferred", default="按我的策略，我更想要：{preferred}。", preferred=preferred_text)
            elif preferred_index is not None and 0 <= preferred_index < len(reward_cards):
                reward_card = reward_cards[preferred_index] if isinstance(reward_cards[preferred_index], dict) else {}
                reward_name = _trim_trailing_punct(_display_name(reward_card.get("name") or reward_card.get("card_id") or ""))
                if reward_name:
                    base = self.t("companion.suggestion.reward_named", default="这几个奖励里，我想要 {card_name}。", card_name=reward_name)
                elif preferred_index < len(reward_card_names):
                    base = self.t("companion.suggestion.reward_named", default="这几个奖励里，我想要 {card_name}。", card_name=_trim_trailing_punct(_display_name(reward_card_names[preferred_index])))
                else:
                    base = self.t("companion.suggestion.reward_default", default="我想拿能补当前构筑短板的奖励。")
            else:
                base = self.t("companion.suggestion.reward_default", default="我想拿能补当前构筑短板的奖励。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "selection":
            selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
            selection_card_names = payload.get("selection_card_names") if isinstance(payload.get("selection_card_names"), list) else []
            preferred_index = self._preferred_selection_index(payload, directives)
            if preferred_index is not None and 0 <= preferred_index < len(selection_cards):
                selection_card = selection_cards[preferred_index] if isinstance(selection_cards[preferred_index], dict) else {}
                card_name = _trim_trailing_punct(_display_name(selection_card.get("name") or selection_card.get("card_id") or ""))
                if card_name:
                    base = self.t("companion.suggestion.selection_named", default="这些牌里我想处理 {card_name}。", card_name=card_name)
                elif preferred_index < len(selection_card_names):
                    base = self.t("companion.suggestion.selection_named", default="这些牌里我想处理 {card_name}。", card_name=_trim_trailing_punct(_display_name(selection_card_names[preferred_index])))
                else:
                    base = self.t("companion.suggestion.selection", default="我想先精简掉低价值的牌，保持牌组干净。")
            else:
                base = self.t("companion.suggestion.selection", default="我想先精简掉低价值的牌，保持牌组干净。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "map":
            travelable_nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
            preferred_index = self._preferred_map_index(payload, directives)
            if preferred_index is not None:
                preferred_node = next(
                    (
                        node for node in travelable_nodes
                        if isinstance(node, dict) and self._safe_int(node.get("index"), default=-1) == preferred_index
                    ),
                    None,
                )
                node_type = str(preferred_node.get("type") or preferred_node.get("node_type") or "") if isinstance(preferred_node, dict) else ""
                node_label = _display_node_type(node_type)
                if node_label:
                    base = self.t("companion.suggestion.map_named", default="能走的路线里，我更想走 {node_type}。", node_type=node_label)
                else:
                    base = self.t("companion.suggestion.map", default="我更想走稳一点的成长路线，没必要贪这几场。")
            else:
                base = self.t("companion.suggestion.map", default="我更想走稳一点的成长路线，没必要贪这几场。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "event":
            event_name = _trim_trailing_punct(payload.get("event_name") or payload.get("event_id") or "")
            if event_name:
                base = self.t("companion.suggestion.event_named", default="遇到 {event_name} 了，我想挑代价可控、长期更划算的那个。", event_name=event_name)
            else:
                base = self.t("companion.suggestion.event", default="我会挑代价可控、长期更划算的那个。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "shop":
            shop_cards = payload.get("shop_cards") if isinstance(payload.get("shop_cards"), list) else []
            shop_relics = payload.get("shop_relics") if isinstance(payload.get("shop_relics"), list) else []
            shop_card_names = payload.get("shop_card_names") if isinstance(payload.get("shop_card_names"), list) else []
            shop_relic_names = payload.get("shop_relic_names") if isinstance(payload.get("shop_relic_names"), list) else []
            preferred_card_index = self._preferred_shop_card_index(payload, directives)
            preferred_relic_index = self._preferred_shop_relic_index(payload, directives)
            if preferred_card_index is not None and 0 <= preferred_card_index < len(shop_cards):
                shop_card = shop_cards[preferred_card_index] if isinstance(shop_cards[preferred_card_index], dict) else {}
                item_name = str(shop_card.get("name") or shop_card.get("card_id") or "")
                if item_name:
                    base = self.t("companion.suggestion.shop_card_named", default="商店里我想要 {item_name}。", item_name=item_name)
                elif preferred_card_index < len(shop_card_names):
                    base = self.t("companion.suggestion.shop_card_named", default="商店里我想要 {item_name}。", item_name=shop_card_names[preferred_card_index])
                else:
                    base = self.t("companion.suggestion.shop", default="我想买真有用的，或者干脆删张牌，不乱花钱。")
            elif preferred_relic_index is not None and 0 <= preferred_relic_index < len(shop_relics):
                shop_relic = shop_relics[preferred_relic_index] if isinstance(shop_relics[preferred_relic_index], dict) else {}
                item_name = str(shop_relic.get("name") or shop_relic.get("relic_id") or "")
                if item_name:
                    base = self.t("companion.suggestion.shop_relic_named", default="商店里我想要那件遗物 {item_name}。", item_name=item_name)
                elif preferred_relic_index < len(shop_relic_names):
                    base = self.t("companion.suggestion.shop_relic_named", default="商店里我想要那件遗物 {item_name}。", item_name=shop_relic_names[preferred_relic_index])
                else:
                    base = self.t("companion.suggestion.shop", default="我想买真有用的，或者干脆删张牌，不乱花钱。")
            else:
                base = self.t("companion.suggestion.shop", default="我想买真有用的，或者干脆删张牌，不乱花钱。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "rest":
            return self._post_action_wrap(self.t("companion.suggestion.rest", default="我想先看血线，再定是歇一会儿还是升级。"), trigger)
        return self._post_action_wrap(self.t("companion.suggestion.general", default="我先看看局势，再决定动不动。"), trigger)

    def _combat_voice(self, payload: dict[str, Any]) -> str:
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        current_hp = self._safe_int(player.get("current_hp"))
        max_hp = max(1, self._safe_int(player.get("max_hp"), default=1))
        block = self._safe_int(player.get("block"))
        incoming = sum(self._safe_int(enemy.get("intent_damage")) for enemy in enemies if isinstance(enemy, dict))
        hp_ratio = current_hp / max_hp if max_hp > 0 else 0.0
        if incoming > block or hp_ratio <= 0.45:
            return self._combat_tactical_advice(payload)
        enemy_intel = self._monster_intel(summary_kind="combat", payload=payload)
        if enemy_intel:
            return self.t("companion.commentary.combat_pressure", default="这波怪的意图一点都不客气：{enemy_intel}。", enemy_intel=enemy_intel)
        if hp_ratio <= 0.65:
            return self.t("companion.commentary.combat_low_hp", default="这血线看着就不太适合贪刀，先稳一稳。")
        return self.t("companion.commentary.combat_general", default="这手牌还能打，但别太上头。")

    def _combat_has_lethal_play(self, payload: dict[str, Any]) -> bool:
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        cards = payload.get("playable_card_summaries") if isinstance(payload.get("playable_card_summaries"), list) else []
        if not enemies or not cards:
            return False
        for card in cards:
            if not isinstance(card, dict):
                continue
            damage = self._planner._card_damage(card)
            if damage <= 0:
                continue
            for enemy in enemies:
                if not isinstance(enemy, dict):
                    continue
                enemy_hp = self._safe_int(enemy.get("current_hp"), default=0)
                if enemy_hp > 0 and damage >= enemy_hp:
                    return True
        return False

    def _combat_needs_defense(self, payload: dict[str, Any]) -> bool:
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        current_hp = self._safe_int(player.get("current_hp"), default=0)
        max_hp = max(1, self._safe_int(player.get("max_hp"), default=1))
        block = self._safe_int(player.get("block"), default=0)
        incoming = sum(self._safe_int(enemy.get("intent_damage"), default=0) for enemy in enemies if isinstance(enemy, dict))
        hp_ratio = current_hp / max_hp if max_hp > 0 else 0.0
        return incoming > block or hp_ratio <= 0.45

    def _is_defensive_card(self, card: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("effect") or ""),
                str(card.get("description") or ""),
            ]
        ).lower()
        return any(token in text for token in ["block", "defend", "护盾", "格挡"])

    def _combat_hand_from_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        cards = payload.get("playable_card_summaries") if isinstance(payload.get("playable_card_summaries"), list) else []
        hand: list[dict[str, Any]] = []
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            hand.append(
                {
                    "index": index,
                    "name": card.get("name"),
                    "card_id": card.get("name"),
                    "energy_cost": card.get("energy_cost"),
                    "star_cost": card.get("star_cost"),
                    "costs_x": card.get("costs_x"),
                    "star_costs_x": card.get("star_costs_x"),
                    "playable": bool(card.get("playable", True)),
                    "effect": card.get("effect"),
                    "description": card.get("effect"),
                    "requires_target": card.get("requires_target"),
                    "valid_target_indices": list(range(len(payload.get("enemies") if isinstance(payload.get("enemies"), list) else []))) if card.get("requires_target") else [],
                }
            )
        return hand

    def _combat_preferences(self, directives: dict[str, Any]) -> dict[str, Any]:
        preferred = directives.get("preferred_option_index")
        if preferred is None:
            return {"records": []}
        return {"records": [{"value": {"preferred_option_index": preferred}}]}

    def _shop_card_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "shop_cards": list(payload.get("shop_cards") if isinstance(payload.get("shop_cards"), list) else []),
            "gold": payload.get("gold"),
        }

    def _shop_relic_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "shop_relics": list(payload.get("shop_relics") if isinstance(payload.get("shop_relics"), list) else []),
            "gold": payload.get("gold"),
        }

    def _preferred_reward_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        summary_context = {
            "payload": {
                "reward_cards": list(payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []),
                "selection_cards": list(payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []),
                "deck": dict(payload.get("deck") if isinstance(payload.get("deck"), dict) else {}),
                "decision_payload": {
                    "guidance": {"pending": []},
                    "instructions": [],
                },
            }
        }
        if directives.get("preferred_option_index") is not None:
            preferred = int(directives["preferred_option_index"])
            return preferred
        return self._planner._preferred_reward_option(summary_context, {"records": []})

    def _preferred_selection_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        preferred = directives.get("preferred_option_index")
        if preferred is not None:
            return self._safe_int(preferred)
        summary_context = {
            "payload": {
                "selection_cards": list(payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []),
            }
        }
        return self._planner._preferred_remove_option(summary_context, {"records": []})

    def _preferred_map_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        summary_context = {
            "payload": {
                "travelable_nodes": list(payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []),
                "current_hp": payload.get("current_hp"),
                "max_hp": payload.get("max_hp"),
                "gold": payload.get("gold"),
            }
        }
        preferences = {"records": []}
        if directives.get("preferred_option_index") is not None:
            preferences = {"records": [{"value": {"preferred_option_index": directives["preferred_option_index"]}}]}
        return self._planner._preferred_map_index(summary_context, preferences)

    def _preferred_shop_card_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        summary_context = {
            "payload": {
                "reward_cards": list(payload.get("shop_cards") if isinstance(payload.get("shop_cards"), list) else []),
                "deck": dict(payload.get("deck") if isinstance(payload.get("deck"), dict) else {}),
                "decision_payload": {
                    "guidance": {"pending": []},
                    "instructions": [],
                },
            }
        }
        if directives.get("preferred_option_index") is not None:
            return self._safe_int(directives.get("preferred_option_index"))
        return self._planner._preferred_reward_option(summary_context, {"records": []})

    def _preferred_shop_relic_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        preferred = directives.get("preferred_option_index")
        if preferred is not None:
            return self._safe_int(preferred)
        relics = payload.get("shop_relics") if isinstance(payload.get("shop_relics"), list) else []
        if len(relics) == 1:
            return 0
        return None

    def _reminder(self, *, summary_kind: str, payload: dict[str, Any], directives: dict[str, Any], trigger: str) -> str:
        must = directives.get("must") if isinstance(directives.get("must"), list) else []
        if summary_kind == "combat":
            player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
            current_hp = self._safe_int(player.get("current_hp"))
            max_hp = max(1, self._safe_int(player.get("max_hp"), default=1))
            if max_hp > 0 and current_hp / max_hp <= 0.3:
                return self.t("companion.reminder.low_hp", default="你血不多了，别为了贪输出白挨这一下。")
        if must:
            return self.t("companion.reminder.must", default="按我的策略，{must} 这条得守住。", must=must[0])
        return ""

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _trigger_state(self, *, summary_kind: str, payload: dict[str, Any], runtime_state: Any | None, player_operation_observation: dict[str, Any]) -> tuple[str, str, str, str]:
        screen = str(payload.get("screen") or summary_kind or "unknown")
        floor = self._safe_int(payload.get("floor") or 0)
        act = self._safe_int(payload.get("act") or 0)
        turn_value = payload.get("turn") if "turn" in payload else payload.get("turn_count")
        turn_key = f"{act}:{floor}:{screen}:{turn_value}" if summary_kind == "combat" and turn_value is not None else ""
        scene_key = f"{act}:{floor}:{screen}"

        if player_operation_observation:
            fingerprint = str(player_operation_observation.get("fingerprint") or scene_key)
            return "player_operation", turn_key, scene_key, fingerprint
        if summary_kind == "combat" and turn_key:
            return "combat_turn", turn_key, scene_key, turn_key
        if summary_kind in {"event", "map", "shop", "reward", "selection", "rest"}:
            return "scene_entry", turn_key, scene_key, scene_key
        return "general", turn_key, scene_key, ""

    def _should_comment(self, *, trigger: str, turn_key: str, scene_key: str, evaluation_key: str, runtime_state: Any | None, player_operation_observation: dict[str, Any]) -> bool:
        if runtime_state is None:
            return True
        if trigger == "combat_turn" and turn_key:
            return turn_key != getattr(runtime_state, "last_companion_turn_key", "")
        if trigger == "scene_entry" and scene_key:
            return scene_key != getattr(runtime_state, "last_companion_scene_key", "")
        if trigger == "player_operation" and player_operation_observation:
            # 弹幕模式：玩家操作也值得点评，不再一律拦。上游 _should_emit_player_operation 已按
            # fingerprint 去重 + 节流，这里只对同指纹的重复操作再挡一次，防止同一操作连发。
            fingerprint = str(player_operation_observation.get("fingerprint") or "")
            if fingerprint:
                return fingerprint != getattr(runtime_state, "last_companion_player_op_fingerprint", "")
            return True
        return True

    def _with_card_cost(self, text: str, *, payload: dict[str, Any]) -> str:
        base = str(text or "").strip()
        if not base:
            return base
        cards = payload.get("playable_card_summaries") if isinstance(payload.get("playable_card_summaries"), list) else []
        if not cards:
            return base
        first = cards[0] if isinstance(cards[0], dict) else {}
        name = str(first.get("name") or "").strip()
        if not name:
            return base
        if bool(first.get("costs_x")):
            cost_text = "X费"
        else:
            cost_text = f"{self._safe_int(first.get('energy_cost'), default=0)}费"
        star_cost = self._safe_int(first.get("star_cost"), default=0)
        if bool(first.get("star_costs_x")):
            cost_text += "，耗星X"
        elif star_cost and star_cost > 0:
            cost_text += f"，耗星{star_cost}"
        return f"{base} 可考虑 {name}（{cost_text}）。"

    def _post_action_wrap(self, text: str, trigger: str) -> str:
        if not text:
            return text
        if trigger == "post_action_eval":
            return self.t("companion.suggestion.post_action", default="从这一步结果看，{text}", text=text)
        return text


__all__ = ["STS2CompanionEvaluator"]
