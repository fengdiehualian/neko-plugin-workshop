import asyncio
import importlib
import json
import uuid

import httpx
import pytest
from agent_mc_autonomy.ownership import session_lease
from test_recent_context import SCOPE, attach_bus, prepare


def install_output(monkeypatch):
    requests = []
    client_class = httpx.AsyncClient
    async def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'paths': {'/api/game/{game_type}/speak': {'post': {}}}})
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'ok': True, 'speech_id': 'speech-' + str(len(requests)), 'audio_queued': True})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))
    return requests


async def opportunity(plugin, port, snapshot, clock, save, *, text='再看看附近吧。', origin='automatic', decision='continue'):
    save()
    port.observe(snapshot)
    response = await (plugin.game_decision_heartbeat() if origin == 'automatic' else plugin.review_game())
    review_id = response.value['heartbeat_id']
    inspected = await plugin.minecraft_autonomy_review(review_id, 'inspect')
    assert not inspected.get('is_error')
    result = await plugin.minecraft_autonomy_review(review_id, decision, 'Useful plan', 'Find food' if decision == 'insert' else '', spoken_text=text)
    assert result['acknowledged']
    if getattr(plugin, '_speech_output_tasks', None):
        await asyncio.gather(*plugin._speech_output_tasks)
    clock[0] += 45
    return result, review_id


def test_five_real_automatic_opportunities_send_once_and_keep_actions_independent(plugin_instance, monkeypatch, run_async):
    port, snapshot, raw, _, clock, save, _, _ = prepare(plugin_instance, monkeypatch)
    raw['heartbeat']['idle_presence_seconds'] = 30
    raw['speech_output'] = {'enabled': True, 'base_url': 'http://localhost:1234'}
    attach_bus(plugin_instance, [])
    sent = install_output(monkeypatch)
    async def scenario():
        results = []
        for _ in range(5):
            result, review_id = await opportunity(plugin_instance, port, snapshot, clock, save)
            results.append(result)
            again = await plugin_instance.minecraft_autonomy_review(review_id, 'continue', 'Useful plan', spoken_text='再看看附近吧。')
            assert again['speech']['status'] == result['speech']['status']
        assert [r['speech']['status'] for r in results] == ['enqueued'] + ['repeat_suppressed'] * 4
        assert len(sent) == 1
        for result in results[1:]:
            assert result['speech']['prior_speech_id'] == 'speech-1'
            assert result['speech']['audio_queued'] is False and not result['speech']['playback_verified']
        # New wording and expiration are independent of suppression records.
        await opportunity(plugin_instance, port, snapshot, clock, save, text='换个方向。')
        clock[0] = 10301
        await opportunity(plugin_instance, port, snapshot, clock, save)
        assert len(sent) == 3
    with session_lease(port.directory):
        run_async(scenario())


@pytest.mark.parametrize('bypass', ['manual', 'mixed', 'unknown', 'urgent_health', 'urgent_runtime', 'death', 'fresh_user'])
def test_actual_provenance_bypasses_ordinary_guard(plugin_instance, monkeypatch, run_async, bypass):
    port, snapshot, raw, settings, clock, save, _, _ = prepare(plugin_instance, monkeypatch)
    raw['heartbeat']['idle_presence_seconds'] = 30
    raw['speech_output'] = {'enabled': True, 'base_url': 'http://localhost:1234'}
    attach_bus(plugin_instance, [])
    sent = install_output(monkeypatch)
    module = importlib.import_module(plugin_instance.__class__.__module__ + '.neko_repeat_admission')
    async def scenario():
        await opportunity(plugin_instance, port, snapshot, clock, save)
        clock[0] += 15
        if bypass == 'urgent_health':
            snapshot['facts']['parts'][0]['text'] = '生命2/20'
        elif bypass == 'urgent_runtime':
            snapshot.update(phase='failed', error='synthetic_runtime_failure')
        elif bypass == 'death':
            from agent_mc_autonomy.death_memory import DeathMemory
            snapshot['facts']['parts'].append({'key': 'deathLifecycle', 'text': json.dumps({'phase': 'alive', 'episodeId': 'new-death'})})
            event = {'run': 'native-1', 'cursor': 1, 'ts': '2026-10-16T01:02:03Z',
                     'meta': {'death': {'id': 'new-death', 'scope': SCOPE, 'position': {'x': 1, 'y': 64, 'z': 2}}}}
            snapshot['death_episode'] = DeathMemory(port.directory).record(event, snapshot['facts'], [], [], [])
            snapshot['body_death_episode_id'] = 'new-death'
            snapshot['host_recent_request']['death_episode_id'] = 'new-death'
        if bypass == 'fresh_user':
            receipt = port.mailbox.submit('feedback', {'goal': 'User request', 'mode': 'insert', 'origin': 'user'}, str(uuid.uuid4()))
            port.mailbox.activate_feedback(port.mailbox.claim())
            snapshot['current_goal'] = port.mailbox.active_goal()
            assert snapshot['current_goal']['id'] == receipt['request_id']
            snapshot['host_recent_request']['intent_revision'] = port.mailbox.intent_revision()
        save()
        port.observe(snapshot)
        result = await (plugin_instance.review_game() if bypass == 'manual' else plugin_instance.game_decision_heartbeat())
        review_id = result.value['heartbeat_id']
        if bypass == 'mixed':
            await plugin_instance.review_game()  # Existing delivered opportunity becomes mixed.
            await plugin_instance.game_decision_heartbeat()
        elif bypass == 'unknown':
            plugin_instance._review_origins.clear()
        inspected = await plugin_instance.minecraft_autonomy_review(review_id, 'inspect')
        admission = module.classification(plugin_instance, settings, 'TestRole', port, 0)
        assert admission is None or not admission.ordinary
        reflection = {'evidence_ref': inspected['evidence_ref'], 'outcome': 'deferred', 'reason': 'Need actual cause'} if bypass == 'death' else None
        final = await plugin_instance.minecraft_autonomy_review(review_id, 'continue', 'Useful plan', spoken_text='再看看附近吧。', death_reflection=reflection)
        assert final['speech']['status'] == 'enqueued'
        await asyncio.gather(*plugin_instance._speech_output_tasks)
        assert len(sent) == 2
    with session_lease(port.directory):
        run_async(scenario())


@pytest.mark.parametrize('prior_state', ['accepted', 'queued', 'unknown', 'reserved_unknown', 'rejected', 'disabled', 'unavailable', 'unbound'])
def test_only_possible_bound_submissions_suppress_and_mailbox_still_dispatches(plugin_instance, monkeypatch, run_async, prior_state):
    port, snapshot, raw, _, clock, save, _, _ = prepare(plugin_instance, monkeypatch)
    raw['heartbeat']['idle_presence_seconds'] = 30
    raw['speech_output'] = {'enabled': True, 'base_url': 'http://localhost:1234'}
    attach_bus(plugin_instance, [])
    sent = install_output(monkeypatch)
    async def scenario():
        await opportunity(plugin_instance, port, snapshot, clock, save)
        path = port.directory / 'speech-output.json'
        state = json.loads(path.read_text())
        first = next(iter(state['records'].values()))
        if prior_state == 'unbound':
            first.pop('runtime_binding')
        else:
            first['status'] = prior_state
        path.write_text(json.dumps(state), encoding='utf-8')
        result, _ = await opportunity(plugin_instance, port, snapshot, clock, save, decision='insert')
        suppressed = prior_state in {'accepted', 'queued', 'unknown', 'reserved_unknown'}
        assert result['speech']['status'] == ('repeat_suppressed' if suppressed else 'enqueued')
        assert result['dispatched'] and port.mailbox.get(result['request_id'])['delivery'] == 'queued'
        assert len(sent) == (1 if suppressed else 2)
    with session_lease(port.directory):
        run_async(scenario())


@pytest.mark.parametrize('boundary', ['actor', 'identity', 'session', 'run', 'scope'])
def test_repeat_history_does_not_cross_actual_binding(plugin_instance, monkeypatch, run_async, boundary):
    port, snapshot, raw, _, clock, save, _, _ = prepare(plugin_instance, monkeypatch)
    raw['heartbeat']['idle_presence_seconds'] = 30
    raw['speech_output'] = {'enabled': True, 'base_url': 'http://localhost:1234'}
    attach_bus(plugin_instance, [])
    sent = install_output(monkeypatch)
    async def scenario():
        await opportunity(plugin_instance, port, snapshot, clock, save)
        path = port.directory / 'speech-output.json'
        state = json.loads(path.read_text())
        first = next(iter(state['records'].values()))
        first['runtime_binding'][boundary] = {**SCOPE, 'agent': 'other'} if boundary == 'scope' else 'other'
        path.write_text(json.dumps(state), encoding='utf-8')
        result, _ = await opportunity(plugin_instance, port, snapshot, clock, save)
        assert result['speech']['status'] == 'enqueued' and len(sent) == 2
    with session_lease(port.directory):
        run_async(scenario())


def test_durable_same_opportunity_lookup_precedes_disabled_and_sanitizer(plugin_instance, monkeypatch, run_async):
    port, snapshot, raw, settings, clock, save, _, _ = prepare(plugin_instance, monkeypatch)
    raw['speech_output'] = {'enabled': True, 'base_url': 'http://localhost:1234'}
    attach_bus(plugin_instance, [])
    install_output(monkeypatch)
    module = importlib.import_module(plugin_instance.__class__.__module__ + '.neko_speech')
    async def scenario():
        result, _ = await opportunity(plugin_instance, port, snapshot, clock, save)
        original = json.loads((port.directory / 'speech-output.json').read_text())['records'][result['speech']['opportunity_id']]
        raw['speech_output']['enabled'] = False
        receipt = module.enqueue_speech(plugin_instance, raw, settings, 'TestRole', port, 0, '', original)
        assert receipt == original and receipt['status'] == 'accepted'
    with session_lease(port.directory):
        run_async(scenario())
