import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from plugin.plugins.recall.core import (
    create_session,
    dashboard,
    initial_state,
    schedule_review,
    submit_review,
)
from plugin.plugins.recall.models import build_source_registry

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_recall_as_a_plugin() -> None:
    with (PLUGIN_ROOT / "plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)
    assert manifest["plugin"]["id"] == "recall"
    assert manifest["plugin"]["entry"] == "plugin.plugins.recall:RecallPlugin"
    assert manifest["plugin_runtime"]["enabled"] is True


def test_first_session_uses_bounded_builtin_vocabulary() -> None:
    source = build_source_registry()["official.cet4.demo"]
    state = initial_state()
    session = create_session(state, source, intent="learn_new", new_limit=5, review_limit=12)
    assert session["total"] == 5
    assert session["current_card"]["is_new"] is True
    assert state["active_session_id"] == session["id"]


def test_cet4_source_loads_the_versioned_offline_library() -> None:
    source = build_source_registry()["official.cet4.core.v1"]
    items = source.items()
    assert source.info.item_count >= 3800
    assert len(items) == source.info.item_count
    assert len({item.id for item in items}) == len(items)
    assert all(item.word and item.meaning for item in items)
    assert items[0].word == "state"
    assert items[0].word != "abandon"
    abandon = next(item for item in items if item.word == "abandon")
    assert abandon.example == "They had to abandon the plan."


def test_cet4_data_records_provenance_and_frequency_order() -> None:
    payload = json.loads(
        (PLUGIN_ROOT / "static" / "data" / "cet4-core.v1.json").read_text(encoding="utf-8-sig")
    )
    assert payload["license"] == "MIT"
    assert payload["upstream"] == "https://github.com/skywind3000/ECDICT"
    assert len(payload["upstream_commit"]) == 40
    ranks = [int(item["frequency_rank"]) for item in payload["items"]]
    assert ranks == sorted(ranks)


def test_review_result_stays_in_recall_learning_state() -> None:
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    source = build_source_registry()["official.cet4.demo"]
    state = initial_state()
    record = schedule_review(state, item_id="cet4.abandon", grade="good", now=now)
    assert record["status"] == "review"
    assert state["items"]["cet4.abandon"]["last_grade"] == "good"
    assert dashboard(state, source, now=now)["learned_count"] == 1
    assert "N.E.K.O 长期记忆" in dashboard(state, source, now=now)["memory_boundary"]


def test_forgotten_word_becomes_due_for_relearning() -> None:
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    state = initial_state()
    record = schedule_review(state, item_id="cet4.abandon", grade="forgot", now=now)
    assert record["status"] == "learning"
    assert record["lapses"] == 1
    assert record["interval_days"] < 1


def test_mixed_session_keeps_new_budget_when_review_backlog_is_full() -> None:
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    source = build_source_registry()["official.cet4.demo"]
    state = initial_state()
    for item in source.items()[:7]:
        schedule_review(state, item_id=item.id, grade="forgot", now=now)
    session = create_session(
        state,
        source,
        intent="mixed",
        now=now,
        review_limit=3,
        new_limit=2,
        resume=False,
    )
    queue = state["sessions"][session["id"]]["queue"]
    assert len([item for item in queue if item["kind"] == "review"]) == 3
    assert len([item for item in queue if item["kind"] == "new"]) == 2


def test_new_word_box_is_shuffled_without_leaving_the_near_term_pool() -> None:
    source = build_source_registry()["official.cet4.core.v1"]
    state = initial_state()
    source_ids = [item.id for item in source.items()]
    session = create_session(state, source, intent="learn_new", new_limit=5, resume=False)
    queue_ids = [item["item_id"] for item in state["sessions"][session["id"]]["queue"]]
    assert session["box_size"] == 5
    assert len(queue_ids) == len(set(queue_ids)) == 5
    assert set(queue_ids).issubset(set(source_ids[:30]))
    assert queue_ids != source_ids[:5]


def test_default_mixed_session_is_one_box_of_at_most_five() -> None:
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    source = build_source_registry()["official.cet4.demo"]
    state = initial_state()
    for item in source.items()[:3]:
        schedule_review(state, item_id=item.id, grade="forgot", now=now)
    session = create_session(state, source, intent="mixed", now=now, resume=False)
    assert session["box_size"] == 5
    assert session["total"] == 5


def test_submit_review_advances_and_is_idempotent() -> None:
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    source = build_source_registry()["official.cet4.demo"]
    state = initial_state()
    session = create_session(state, source, intent="learn_new", now=now, new_limit=2)
    item_id = session["current_card"]["id"]
    result = submit_review(
        state,
        source,
        session_id=session["id"],
        item_id=item_id,
        grade="good",
        client_event_id="browser-event-1",
        now=now,
    )
    again = submit_review(
        state,
        source,
        session_id=session["id"],
        item_id=item_id,
        grade="good",
        client_event_id="browser-event-1",
        now=now,
    )
    assert result == again
    assert result["session"]["position"] == 1
    assert len(state["review_events"]) == 1
    assert result["next_card"]["id"] != item_id


def test_day_one_due_state_survives_a_new_session() -> None:
    day_zero = datetime(2026, 9, 28, tzinfo=timezone.utc)
    day_three = datetime(2026, 10, 1, tzinfo=timezone.utc)
    source = build_source_registry()["official.cet4.demo"]
    state = initial_state()
    session = create_session(state, source, intent="learn_new", now=day_zero, new_limit=1)
    item_id = session["current_card"]["id"]
    submit_review(
        state,
        source,
        session_id=session["id"],
        item_id=item_id,
        grade="good",
        client_event_id="day-zero",
        now=day_zero,
    )
    due_session = create_session(state, source, intent="review_due", now=day_three, resume=False)
    assert due_session["current_card"]["id"] == item_id
    assert due_session["current_card"]["is_new"] is False


def test_static_ui_uses_separate_window_and_system_character_snapshot() -> None:
    manifest = (PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    implementation = (PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    script = (PLUGIN_ROOT / "static" / "main-v2.js").read_text(encoding="utf-8")
    assert "static/index.html" in manifest
    assert '"open_in": "new_tab"' in implementation
    assert "/api/card-drop/active-character?include_avatar=true" in implementation
    assert 'callEntry("get_companion_identity")' in script
    assert 'fetch("/api/card-drop/active-character' not in script


def test_market_copy_states_the_v02_scope_in_chinese() -> None:
    manifest = (PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    pyproject = (PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "Recall 陪伴式记忆与复习"' in manifest
    assert 'name = "WhiteFrostune"' in manifest
    assert 'version = "0.2.0"' in manifest
    assert "测试阶段" in manifest
    assert "v0.2 当前支持英语词汇" in manifest
    assert "当前状态：测试阶段（Beta）" in readme
    assert "./media/recall-demo.gif" in readme
    assert 'exclude_dirs = [".vscode", "media", "tests"]' in pyproject


def test_market_repository_includes_license_and_asset_attribution() -> None:
    notice = (PLUGIN_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert (PLUGIN_ROOT / "LICENSE").is_file()
    assert (PLUGIN_ROOT / "NOTICE").is_file()
    assert "Apache License" in (PLUGIN_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "N.E.K.O. shared interface assets" in notice
    assert "skywind3000/ECDICT" in notice


def test_v02_home_has_one_primary_start_and_hides_scheduler_modes() -> None:
    html = (PLUGIN_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (PLUGIN_ROOT / "static" / "main-v2.js").read_text(encoding="utf-8")
    assert html.count('id="startButton"') == 1
    assert "只认识 5 个新词" not in html
    assert "只复习到期词" not in html
    assert "先花几分钟建立印象" not in html + script
    assert 'id="homeLead"' not in html + script
    assert "data-start-intent" not in html + script
    assert 'id="continueNewButton"' in html
    assert 'beginSession("learn_new", false' in script


def test_home_and_source_picker_only_show_user_relevant_copy() -> None:
    html = (PLUGIN_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (PLUGIN_ROOT / "static" / "main-v2.js").read_text(encoding="utf-8")
    assert "学习记录只留在 Recall" not in html
    assert "换一个学习内容" not in html
    assert "addSourceButton" not in html + script
    assert "不用选参数" not in html + script
    assert "由社区直接提供" not in html
    assert "接口已预留" not in html
    assert html.count("暂未开放") == 2


def test_full_flow_does_not_explain_recall_internals_to_the_user() -> None:
    html = (PLUGIN_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (PLUGIN_ROOT / "static" / "main-v2.js").read_text(encoding="utf-8")
    visible_copy = html + script
    for phrase in (
        "之后什么时候复习交给 Recall",
        "Recall 已经替你排好了",
        "新词和复习混合",
        "不会重新从头",
        "不用为了连续打卡硬背",
        "顺序和下次什么时候再见交给 Recall",
        "写进 Recall 的学习状态",
        "下次回来时我会接着安排",
        "下次从真正该回来的词继续",
    ):
        assert phrase not in visible_copy
    for obsolete_id in (
        'id="startButtonDetail"',
        'id="sourceNotice"',
        'id="scheduleNote"',
        'id="reviewHint"',
        'id="completeLead"',
    ):
        assert obsolete_id not in visible_copy


def test_memory_box_ui_is_interactive_and_uses_real_assets() -> None:
    html = (PLUGIN_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (PLUGIN_ROOT / "static" / "main-v2.js").read_text(encoding="utf-8")
    assert html.count('class="blind-box"') == 5
    assert 'id="openBoxButton"' in html
    assert 'data-screen="draw"' in html
    assert "assets/memory-box/sealed-box.png" in html
    assert "assets/memory-box/memory-stage.png" in html
    assert "assets/system/neko_community_on.png" in html
    assert "assets/system/chat-minimized-yarn-ball.png" in html
    assert 'src="/static/' not in html
    assert "function selectBox" in script
    assert "function openSelectedBox" in script
    assert 'showScreen("review")' in script


def test_memory_box_and_word_card_share_one_continuous_interaction() -> None:
    html = (PLUGIN_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (PLUGIN_ROOT / "static" / "main-v2.js").read_text(encoding="utf-8")
    styles = (PLUGIN_ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert 'class="card-origin"' in html
    assert "boxOriginX" in script
    assert 'classList.add("is-from-box")' in script
    assert 'classList.add("is-storing")' in script
    assert "@keyframes card-from-box" in styles
    assert "@keyframes card-back-to-box" in styles
    assert "pointer-events: none" in styles
