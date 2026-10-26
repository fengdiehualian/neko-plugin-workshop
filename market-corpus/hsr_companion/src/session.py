"""Session and correction primitives for the Star Rail companion.

The main dialog model is intentionally not treated as the source of truth here.
This module keeps a small, auditable session contract that can be injected into
N.E.K.O and resolves player corrections before a candidate reaches the profile.
"""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from typing import Any, Iterable

from .core import CharacterRegistry

SESSION_SCHEMA_VERSION = 1
DEFAULT_SESSION_TTL_SECONDS = 4 * 60 * 60
VALID_MODALITIES = {"chat", "voice", "vision"}


def utc_now_iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_observed_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s\-_.·•・,，、:：;；'\"“”‘’（）()\[\]【】]+", "", text)


def visual_fingerprint(image_bytes: bytes) -> str:
    """Return a non-reversible perceptual identifier without storing the image.

    Decodable screenshots use a 64-bit difference hash so a correction survives
    harmless recompression and tiny frame changes.  Invalid/test bytes retain a
    cryptographic fallback for backward compatibility.
    """

    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(image_bytes)) as opened:
            image = ImageOps.exif_transpose(opened).convert("L").resize((9, 8))
        pixels = list(image.getdata())
        value = 0
        for row in range(8):
            offset = row * 9
            for column in range(8):
                value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
        return f"dhash:{value:016x}"
    except Exception:
        return "sha256:" + sha256(image_bytes).hexdigest()[:24]


def visual_fingerprints_match(left: object, right: object, *, max_distance: int = 8) -> bool:
    first = str(left or "").strip().lower()
    second = str(right or "").strip().lower()
    if not first or not second:
        return False
    if first.startswith("dhash:") and second.startswith("dhash:"):
        try:
            distance = (int(first[6:], 16) ^ int(second[6:], 16)).bit_count()
        except ValueError:
            return False
        return distance <= max(0, min(int(max_distance), 16))
    return first == second


def session_is_active(
    session: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> bool:
    if not isinstance(session, dict) or not bool(session.get("active")):
        return False
    touched_at = _parse_time(session.get("last_activity_at") or session.get("started_at"))
    if touched_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - touched_at <= timedelta(
        seconds=max(60, int(ttl_seconds or DEFAULT_SESSION_TTL_SECONDS))
    )


def start_session(
    current: dict[str, Any] | None = None,
    *,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = utc_now_iso(now)
    previous = dict(current or {})
    active_before = session_is_active(previous, now=now)
    session_id = str(previous.get("session_id") or "").strip()
    if not active_before or not session_id:
        seed = f"{timestamp}|{reason}|{previous.get('session_id', '')}"
        session_id = sha256(seed.encode("utf-8")).hexdigest()[:20]
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "active": True,
        "started_at": str(previous.get("started_at") or timestamp) if active_before else timestamp,
        "last_activity_at": timestamp,
        "start_reason": str(previous.get("start_reason") or reason).strip(),
        "last_reason": str(reason or "unknown").strip(),
        "context_push_count": int(previous.get("context_push_count") or 0),
        "last_context_push_at": str(previous.get("last_context_push_at") or ""),
        "last_context_reason": str(previous.get("last_context_reason") or ""),
        "last_context_delivery": dict(previous.get("last_context_delivery") or {}),
    }


def stop_session(
    current: dict[str, Any] | None,
    *,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    session = dict(current or {})
    session.update(
        {
            "schema_version": SESSION_SCHEMA_VERSION,
            "active": False,
            "stopped_at": utc_now_iso(now),
            "stop_reason": str(reason or "user").strip(),
        }
    )
    return session


def _correction_scope(
    *,
    observed_name: str,
    modality: str,
    registry: CharacterRegistry,
    visual_id: str,
    session_id: str,
) -> tuple[str, str]:
    if modality == "vision" and visual_id:
        return "visual_fingerprint", visual_id
    # Two canonical character names are an ambiguity, not an alias. Remember
    # the note, but never silently rewrite one real character into another.
    if registry.resolve(observed_name) is not None:
        return "confirmation_only", session_id
    return "global_alias", ""


def sanitize_character_corrections(
    corrections: Iterable[dict[str, Any]],
    *,
    registry: CharacterRegistry,
) -> tuple[list[dict[str, Any]], int]:
    """Disable unsafe legacy canonical-to-canonical rewrites without deleting them."""

    result: list[dict[str, Any]] = []
    disabled = 0
    for raw in corrections:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("active", True)
        observed = registry.resolve(str(item.get("observed_name") or ""))
        canonical = registry.resolve(str(item.get("canonical_name") or ""))
        if (
            item.get("active") is not False
            and item.get("scope") == "current_session"
            and observed is not None
            and canonical is not None
            and observed.get("name") != canonical.get("name")
        ):
            item["active"] = False
            item["disabled_reason"] = "unsafe_legacy_canonical_collision"
            item["disabled_at"] = utc_now_iso()
            disabled += 1
        result.append(item)
    return result[-200:], disabled


def remember_character_correction(
    corrections: Iterable[dict[str, Any]],
    *,
    observed_name: str,
    canonical_name: str,
    modality: str,
    registry: CharacterRegistry,
    visual_id: str = "",
    session_id: str = "",
    note: str = "",
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    observed = str(observed_name or "").strip()
    if not observed:
        raise ValueError("请提供识别错或听错的名字")
    resolved = registry.resolve(canonical_name)
    if resolved is None:
        raise ValueError(f"角色数据库未收录：{canonical_name}")
    normalized_modality = str(modality or "chat").strip().lower()
    if normalized_modality not in VALID_MODALITIES:
        raise ValueError("纠错来源必须是 chat、voice 或 vision")
    normalized_session_id = str(session_id or "").strip()
    scope, scope_value = _correction_scope(
        observed_name=observed,
        modality=normalized_modality,
        registry=registry,
        visual_id=str(visual_id or "").strip(),
        session_id=normalized_session_id,
    )
    if scope in {"current_session", "confirmation_only"} and not normalized_session_id:
        raise ValueError("这条纠错需要先开启星铁陪玩会话")

    observed_key = normalize_observed_name(observed)
    canonical = str(resolved["name"])
    identity = f"{normalized_modality}|{observed_key}|{scope}|{scope_value}"
    correction_id = sha256(identity.encode("utf-8")).hexdigest()[:20]
    timestamp = utc_now_iso(now)
    result = [dict(item) for item in corrections if isinstance(item, dict)]
    existing_index = next(
        (index for index, item in enumerate(result) if item.get("correction_id") == correction_id),
        None,
    )
    confirmations = 1
    created_at = timestamp
    if existing_index is not None:
        previous = result.pop(existing_index)
        confirmations = int(previous.get("confirmations") or 0) + 1
        created_at = str(previous.get("created_at") or timestamp)
    record = {
        "correction_id": correction_id,
        "observed_name": observed,
        "observed_key": observed_key,
        "canonical_name": canonical,
        "modality": normalized_modality,
        "scope": scope,
        "scope_value": scope_value,
        "created_at": created_at,
        "updated_at": timestamp,
        "confirmations": confirmations,
        "active": True,
        "note": str(note or "").strip()[:240],
    }
    result.append(record)
    return result[-200:], deepcopy(record)


def resolve_character_candidate(
    candidate: str,
    *,
    registry: CharacterRegistry,
    corrections: Iterable[dict[str, Any]] = (),
    modality: str = "chat",
    visual_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    observed = str(candidate or "").strip()
    key = normalize_observed_name(observed)
    normalized_modality = str(modality or "chat").strip().lower()
    direct = registry.resolve(observed)
    matching: list[dict[str, Any]] = []
    confirmation_notes: list[dict[str, Any]] = []
    for raw in corrections:
        if not isinstance(raw, dict):
            continue
        if raw.get("active") is False:
            continue
        if str(raw.get("observed_key") or "") != key:
            continue
        if str(raw.get("modality") or "") not in {normalized_modality, "any"}:
            continue
        scope = str(raw.get("scope") or "")
        scope_value = str(raw.get("scope_value") or "")
        if scope == "global_alias" and direct is None:
            matching.append(raw)
        elif scope == "visual_fingerprint" and visual_id and visual_fingerprints_match(scope_value, visual_id):
            matching.append(raw)
        elif scope in {"current_session", "confirmation_only"} and session_id and scope_value == session_id:
            confirmation_notes.append(raw)
    if matching:
        matching.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        corrected = registry.resolve(str(matching[0].get("canonical_name") or ""))
        if corrected is not None:
            return {
                "status": "corrected",
                "observed_name": observed,
                "canonical_name": str(corrected["name"]),
                "correction": deepcopy(matching[0]),
            }

    if direct is not None:
        if confirmation_notes:
            confirmation_notes.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            alternative = registry.resolve(str(confirmation_notes[0].get("canonical_name") or ""))
            if alternative is not None and alternative.get("name") != direct.get("name"):
                return {
                    "status": "needs_confirmation",
                    "observed_name": observed,
                    "canonical_name": "",
                    "alternatives": [str(direct["name"]), str(alternative["name"])],
                    "correction": deepcopy(confirmation_notes[0]),
                }
        return {
            "status": "registry",
            "observed_name": observed,
            "canonical_name": str(direct["name"]),
            "correction": None,
        }
    return {
        "status": "rejected",
        "observed_name": observed,
        "canonical_name": "",
        "correction": None,
    }


def resolve_character_candidates(
    candidates: Iterable[str],
    *,
    registry: CharacterRegistry,
    corrections: Iterable[dict[str, Any]] = (),
    modality: str = "chat",
    visual_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    accepted: list[str] = []
    rejected: list[str] = []
    applied: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    seen_observed: set[str] = set()
    for raw in candidates:
        observed = str(raw or "").strip()
        observed_key = normalize_observed_name(observed)
        if not observed or observed_key in seen_observed:
            continue
        seen_observed.add(observed_key)
        result = resolve_character_candidate(
            observed,
            registry=registry,
            corrections=corrections,
            modality=modality,
            visual_id=visual_id,
            session_id=session_id,
        )
        canonical = str(result.get("canonical_name") or "")
        if result.get("status") == "needs_confirmation":
            ambiguous.append(
                {
                    "observed_name": observed,
                    "alternatives": list(result.get("alternatives") or []),
                    "correction_id": str((result.get("correction") or {}).get("correction_id") or ""),
                }
            )
            continue
        if canonical and canonical not in accepted:
            accepted.append(canonical)
        elif not canonical and observed not in rejected:
            rejected.append(observed)
        if result.get("status") == "corrected":
            applied.append(
                {
                    "observed_name": observed,
                    "canonical_name": canonical,
                    "correction_id": str((result.get("correction") or {}).get("correction_id") or ""),
                }
            )
    return {
        "accepted": accepted,
        "rejected": rejected,
        "applied_corrections": applied,
        "ambiguous_candidates": ambiguous,
    }


def _list_text(values: object, *, empty: str = "暂无") -> str:
    if not isinstance(values, list):
        return empty
    items = [str(item).strip() for item in values if str(item).strip()]
    return "、".join(items[:40]) if items else empty


def build_companion_context(
    *,
    session: dict[str, Any],
    profile: dict[str, Any],
    pending: dict[str, Any],
    corrections: Iterable[dict[str, Any]],
    registry_meta: dict[str, Any],
    registry_count: int,
    catalog_meta: dict[str, Any],
    game_state: dict[str, Any] | None = None,
    current_visual: dict[str, Any] | None = None,
) -> str:
    active_correction_count = sum(
        1 for item in corrections if isinstance(item, dict) and item.get("active") is not False
    )
    correction_text = (
        f"- 已保存 {active_correction_count} 条受限纠错。历史纠错不是当前画面事实，"
        "不得主动复述；只有本次工具结果明确列入 applied_corrections 时才能采用。"
        if active_correction_count
        else "- 暂无生效中的玩家纠错"
    )
    pending_text = _list_text(pending.get("recognized_characters")) if pending else "暂无"
    current_game_state = game_state if isinstance(game_state, dict) else {}
    if current_game_state.get("has_observation") and current_game_state.get("verified"):
        freshness = "当前有效" if current_game_state.get("fresh") else "已经过期，仅供历史参考"
        state_text = (
            f"{current_game_state.get('label') or '暂时无法判断'}"
            f"（{current_game_state.get('confidence') or 'low'}，{freshness}）"
        )
        if current_game_state.get("substate"):
            state_text += f"；细分：{current_game_state['substate']}"
        if current_game_state.get("summary"):
            state_text += f"；画面摘要：{current_game_state['summary']}"
        evidence = _list_text(current_game_state.get("evidence"), empty="暂无明确依据")
        state_text += f"；判断依据：{evidence}"
    else:
        state_text = "尚无插件验证的状态；不要猜测玩家当前在剧情、战斗或其他状态"
    visual_text = ""
    if current_visual is not None:
        names = _list_text([item.get("name") for item in current_visual.get("characters") or []], empty="无")
        visual_text = (
            f"当前角色识别：{current_visual.get('message') or ''}\n"
            f"已确认界面角色：{names}\n"
            f"{current_visual.get('instruction') or ''}\n"
        )
    return (
        "[星铁陪玩会话｜插件可信上下文 v0.9.1]\n"
        f"会话ID：{session.get('session_id', '')}；当前状态：已开启。\n"
        "事实边界：\n"
        "1. 涉及《崩坏：星穹铁道》的角色身份、玩家档案、队伍、培养或游戏事实时，"
        "先调用 hsr_companion_prepare；不要只凭模型记忆回答。\n"
        "2. 只有涉及角色列表或账号拥有角色的截图识别结果，才调用 hsr_stage_player_context 暂存并等待玩家确认；"
        "普通剧情、战斗或探索截图不得改写玩家角色档案。\n"
        "3. 角色名必须来自插件注册表。证据不足时说不确定，不得从相似外观、读音或旧知识猜测。\n"
        "4. 玩家明确纠正后调用 hsr_remember_character_correction；插件会按语音、聊天或截图范围安全记住。\n"
        "5. 插件返回与模型旧知识冲突时，以插件中带来源和确认状态的数据为准。\n"
        "6. hsr_report_game_state 只接收模型的未验证视觉线索，不会生成插件事实；"
        "当前场景和角色身份只能以插件本地识别或玩家明确确认的结果为准。\n"
        "7. 星铁自动观察来自玩家明确开启的独立游戏窗口采集，切换聊天不影响观察；"
        "只使用插件已确认的结构化事实，不应仅因收到观察更新而打断玩家。\n"
        "页面未知不等于游戏加载；插件未返回 followup.pending=true 时，禁止说‘等好了告诉你’。"
        "显式问题补答消息只回答所关联的问题，不当作新游戏事件或普通闲聊。\n"
        "8. 对‘这个技能、这件装备、当前任务、这些属性’先查询当前画面，使用 screen_context_answer；"
        "观察字段、资料解释和分析建议分开。多个候选无选中证据时先确认指代，不随机选取。\n"
        "9. 角色配装、配队、高难攻略也先调用同一工具并使用 guidance_answer；玩家已经说清角色或玩法时，"
        "不必先看到画面才能提供方案。仅需辨认眼前对象/关卡时读取画面。版本攻略、参考队伍、账号拥有分开，"
        "不能用过期期次冒充本期，不能保证最优或通关。对没有专武、缺队友、补一个角色名的追问也先调用工具，"
        "query保留原问题，可用discussion_character携带先前明确的讨论角色，但不能当成视觉确认。"
        "讨论对象过期先确认，不用通用加攻/回能模板自行配队，不泛搜其他游戏。\n"
        f"角色注册表：{registry_count} 名，版本 {registry_meta.get('upstream_revision', '未知')}，"
        f"状态 {registry_meta.get('verification_status', '未知')}。\n"
        f"知识目录状态：{catalog_meta.get('verification_status', '未知')}；"
        "若仅有 minimal_registry_only，只能确认角色身份，不能补写未提供带来源资料的技能或攻略；"
        "另有 guidance_answer 时仅按其中已覆盖且带来源的范围回答。\n"
        "已确认玩家档案：\n"
        f"- 拥有角色：{_list_text(profile.get('owned_characters'))}\n"
        f"- 喜欢角色：{_list_text(profile.get('favorite_characters'))}\n"
        f"- 培养目标：{_list_text(profile.get('training_targets'))}\n"
        f"- 当前目标：{_list_text(profile.get('current_goals'))}\n"
        f"当前游戏画面：{state_text}\n"
        f"{visual_text}"
        f"待确认识别：{pending_text}\n"
        "玩家纠错：\n"
        f"{correction_text}"
    )


def public_correction_summary(
    corrections: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in list(corrections)[-20:]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "correction_id": str(item.get("correction_id") or ""),
                "observed_name": str(item.get("observed_name") or ""),
                "canonical_name": str(item.get("canonical_name") or ""),
                "modality": str(item.get("modality") or ""),
                "scope": str(item.get("scope") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "confirmations": int(item.get("confirmations") or 0),
                "active": item.get("active") is not False,
                "disabled_reason": str(item.get("disabled_reason") or ""),
            }
        )
    return result


__all__ = [
    "DEFAULT_SESSION_TTL_SECONDS",
    "build_companion_context",
    "public_correction_summary",
    "remember_character_correction",
    "sanitize_character_corrections",
    "resolve_character_candidate",
    "resolve_character_candidates",
    "session_is_active",
    "start_session",
    "stop_session",
    "utc_now_iso",
    "visual_fingerprint",
    "visual_fingerprints_match",
]
