from dataclasses import replace

from conftest import PNG_BYTES, FakeHostContext, build_plugin


def _plugin(tmp_path):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = replace(plugin._settings, enabled=True)
    plugin._library.load()
    return plugin, host


def _add(plugin, marker=b"one", **kwargs):
    sticker, error = plugin._library.add(data=PNG_BYTES + marker, desc="happy", tags=[], **kwargs)
    assert not error
    return sticker


def test_generic_agent_request_returns_candidates_without_sending(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    sticker = _add(plugin)
    result = run_async(plugin.send_entry())
    assert result.is_ok() and result.value["id"] == ""
    assert result.value["candidate_ids"] == [sticker.id]
    assert not host.push.calls and not plugin._library.read_usage()
    assert plugin._runstats.sent == 0


def test_agent_group_and_text_use_shared_sender(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    sticker = _add(plugin, group="happy")
    result = run_async(plugin.send_entry(group="happy", text="hello"))
    assert result.is_ok() and result.value["id"] == "" and not host.push.calls
    result = run_async(plugin.send_entry(id=sticker.id, group="happy", text="hello"))
    assert result.is_ok() and result.value["id"] == sticker.id
    assert host.push.calls[0]["parts"] == [{"type": "text", "text": "hello"}]
    assert [part["type"] for part in host.push.calls[1]["parts"]] == ["image"]
    assert all(call["ai_behavior"] == "read" for call in host.push.calls)
    assert plugin._runstats.sent == 1
    assert plugin._library.get(sticker.id).use_count == 1
    assert len(plugin._library.read_usage()) == 1
    second = run_async(plugin.send_entry(id=sticker.id, group="happy"))
    assert not second.is_ok()
    assert str(second.error) in {"send_cooldown", "recent_repeat"}
    assert len(host.push.calls) == 2


def test_agent_query_requires_explicit_selection_even_for_single_hit(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    sticker = _add(plugin)
    result = run_async(plugin.send_entry(query="happy"))
    assert result.is_ok() and result.value["id"] == ""
    assert result.value["candidate_ids"] == [sticker.id]
    assert not host.push.calls


def test_agent_candidates_only_use_active_enabled_nonrecent_images(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    old = _add(plugin)
    plugin._library.append_usage({"id": old.id, "lanlan": "K", "ok": True, "at": 1.0}, keep=200)
    disabled = _add(plugin, b"disabled")
    plugin._library.update(disabled.id, disabled=True)
    other, error = plugin._library.create_zone("other", "")
    assert not error
    _add(plugin, b"other", zone=other)
    chosen = _add(plugin, b"chosen")
    result = run_async(plugin.send_entry(_ctx={"lanlan_name": "K"}))
    assert result.is_ok() and result.value["id"] == ""
    assert result.value["candidate_ids"] == [chosen.id]
    assert not host.push.calls


def test_agent_disabled_empty_and_all_recent_fail_without_sending(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    assert not run_async(plugin.send_entry()).is_ok()
    _add(plugin)
    plugin._settings = replace(plugin._settings, enabled=False)
    assert str(run_async(plugin.send_entry()).error) == "not_enabled"
    assert not host.push.calls


def test_agent_cooldown_with_other_available_images(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    first = _add(plugin)
    second = _add(plugin, b"second")
    assert run_async(plugin.send_entry(id=first.id, query="happy", _ctx={"lanlan_name": "K"})).is_ok()
    result = run_async(plugin.send_entry(id=second.id, query="happy", _ctx={"lanlan_name": "K"}))
    assert not result.is_ok() and str(result.error) == "send_cooldown"
    assert len(host.push.calls) == 1
    assert plugin._library.read_usage()[-1]["source"] == "agent"


def test_agent_all_recent_does_not_bypass_dedup(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    sticker = _add(plugin)
    plugin._library.append_usage({"id": sticker.id, "lanlan": "K", "ok": True, "at": 1.0}, keep=200)
    result = run_async(plugin.send_entry(_ctx={"lanlan_name": "K"}))
    assert not result.is_ok() and str(result.error) == "recent_repeat"
    assert not host.push.calls


def test_agent_broken_catalog_blocks_send(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    _add(plugin)
    plugin._library.catalog_path.write_text("broken", encoding="utf-8")
    assert not plugin._library.load(force=True).ok
    result = run_async(plugin.send_entry())
    assert not result.is_ok() and str(result.error) == "library_io_error"
    assert not host.push.calls


def test_agent_does_not_ignore_invalid_explicit_selector(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    _add(plugin)
    result = run_async(plugin.send_entry(id="missing"))
    assert not result.is_ok() and str(result.error) == "sticker_not_found"
    assert not host.push.calls


def test_direct_llm_tool_still_requires_a_selector(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    _add(plugin)
    result = run_async(plugin.tool_sticker_send())
    assert result["reason"] == "id_or_query_required"
    assert not host.push.calls


def test_agent_probability_gate_remains_effective(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    sticker = _add(plugin)
    plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, probability=0.0))
    result = run_async(plugin.send_entry(id=sticker.id, query="happy"))
    assert not result.is_ok()
    assert not host.push.calls


def test_agent_long_caption_does_not_send_bare_image(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    _add(plugin)
    result = run_async(plugin.send_entry(text="x" * 501))
    assert not result.is_ok() and str(result.error) == "text_too_long"
    assert not host.push.calls


def test_agent_missing_group_does_not_send_unrelated_image(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    _add(plugin, group="happy")
    result = run_async(plugin.send_entry(group="missing"))
    assert not result.is_ok() and str(result.error) == "group_not_found"
    assert not host.push.calls


def test_agent_fuzzy_query_returns_candidates_without_false_success(tmp_path, run_async):
    plugin, host = _plugin(tmp_path)
    _add(plugin)
    _add(plugin, b"second")
    result = run_async(plugin.send_entry(query="happy"))
    assert result.is_ok() and result.value["note"] == "multi_candidates"
    assert result.value["id"] == ""
    assert plugin._runstats.sent == 0
    assert not host.push.calls
