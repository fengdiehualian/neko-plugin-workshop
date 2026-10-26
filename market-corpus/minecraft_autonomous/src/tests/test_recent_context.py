import asyncio
import copy
import dataclasses
import importlib
import json
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent_mc_autonomy import host_recent
from agent_mc_autonomy.configuration import Settings
from agent_mc_autonomy.host_context import HostContextPort
from agent_mc_autonomy.ownership import session_lease
from plugin.core.bus.conversations import ConversationClient, ConversationRecord
from plugin.sdk.plugin import Err, Ok
from plugin.sdk.shared.core.bus_context import SdkBusContext, SdkConversationsBus
from test_heartbeat import setup

SCOPE = {'realm': 'test', 'account': 'TestBot', 'agent': 'test-agent'}


def mod(plugin):
    return importlib.import_module(plugin.__class__.__module__ + '.neko_recent_context')


def record(text='历史一句', at=10000, role='cat', source='main_logic.core', turn='assistant_message'):
    return {'id': 'delivery', 'source': source, 'timestamp': at, 'content': text,
            'metadata': {'conversation_id': 'shared-conversation', 'ts': at, 'role': role,
                         'lanlan_name': 'TestRole', 'turn_type': turn, 'message_count': 1}}


def prepare(plugin, monkeypatch):
    monkeypatch.setattr(plugin.__class__, 'bus', property(lambda self: self._test_bus))
    plugin._test_bus = SdkBusContext(None, host_ctx=plugin.ctx)
    port, snapshot, push = setup(plugin, monkeypatch)
    raw = plugin.config.dump.return_value
    settings = Settings.parse(raw)
    clock = [10000]
    port.now = lambda: clock[0]
    request = {'identity': settings.identity, 'actor': 'TestRole', 'session': 'runtime-1', 'run': 'native-1',
               'scope': SCOPE, 'intent_revision': 0, 'death_episode_id': None, 'as_of': clock[0]}
    snapshot.update(runtime_session=request['session'], native_run=request['run'], game_identity=SCOPE,
                    settings_identity=settings.identity, body_death_episode_id=None, host_recent_request=request)
    snapshot['facts']['run'] = 'native-1'
    def save():
        snapshot['updated_at'] = datetime.fromtimestamp(clock[0], timezone.utc).isoformat()
        if snapshot.get('host_recent_request'):
            snapshot['host_recent_request']['as_of'] = clock[0]
        (port.directory / 'status.json').write_text(json.dumps(snapshot), encoding='utf-8')
    save()
    host = HostContextPort(port.directory, now=port.now)
    host.publish({'agent_name': 'TestRole', 'persona': 'Synthetic', 'current_intent': 'Keep building',
                  'memories': [{'id': 'm', 'source': 'selected', 'text': 'Protect house'}]}, session='123:', revision=0)
    return port, snapshot, raw, settings, clock, save, host, push


def attach_bus(plugin, rows):
    query = AsyncMock(return_value=[ConversationRecord.from_raw(r) for r in rows])
    plugin._test_bus = SdkBusContext(SimpleNamespace(conversations=SimpleNamespace(get=query)), host_ctx=plugin.ctx)
    return query


def test_full_context_publication_keeps_available_recent_language(plugin_instance, monkeypatch, run_async):
    port, snapshot, raw, settings, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    settings = dataclasses.replace(settings, context_mode='full', context_tokens=1048576)
    raw = copy.deepcopy(raw)
    raw.setdefault('autonomy', {})['context_mode'] = 'full'
    snapshot['host_recent_request']['context_mode'] = 'full'
    save()
    rows = [record(text=f'{i}: ' + 'Original recent language. ' * 160, at=9990 + i / 10) for i in range(12)]
    attach_bus(plugin_instance, rows)
    with session_lease(port.directory):
        result = run_async(mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, plugin_instance._generation))
        value = host.read_recent(snapshot['host_recent_request'])
    assert result['messages'] == 12 and len(value['recent_messages']) == 12
    assert not any(row['truncated'] for row in value['recent_messages'])
    assert {row['text'] for row in value['recent_messages']} == {row['content'] for row in rows}


@pytest.mark.parametrize('shape', ['mapping', 'sdk', 'ok', 'iterable'])
def test_genuine_producers_keep_conversation_id_and_roles(plugin_instance, shape):
    module = mod(plugin_instance)
    source = record(role='master', turn='user_message')
    converted = SdkConversationsBus(SimpleNamespace(get=lambda **_: [ConversationRecord.from_raw(source)]), host_ctx=plugin_instance.ctx).get()
    result = {'mapping': source, 'sdk': converted, 'ok': Ok(converted), 'iterable': iter(converted)}[shape]
    item = module.normalize_record(module.public_records(result)[0], 'TestRole', 9900, 10000)
    assert item['conversation_id'] == 'shared-conversation' and item['role'] == 'user'
    assistant = module.normalize_record(record(), 'TestRole', 9900, 10000)
    assert assistant['id'] != item['id']
    proactive = record(source='proactive', turn='proactive_reply')
    del proactive['metadata']['role']
    assert module.normalize_record(proactive, 'TestRole', 9900, 10000)['role'] == 'assistant'


@pytest.mark.parametrize('change', [
    lambda r: r.update(source='unknown'), lambda r: r.update(lanlan_name='Other'),
    lambda r: r['metadata'].update(role='master'), lambda r: r['metadata'].pop('role'),
    lambda r: r['metadata'].update(turn_type='user'), lambda r: r['metadata'].update(turn_type='proactive_instruction'),
    lambda r: r['metadata'].update(ts=float('nan')), lambda r: r['metadata'].update(ts=True),
    lambda r: r['metadata'].update(ts='10000'), lambda r: r['metadata'].update(ts=10001),
    lambda r: r['metadata'].update(ts=9000), lambda r: r.update(timestamp=float('inf')),
    lambda r: r.update(role='conflict'), lambda r: r.update(content={'text': 'not text'}),
    lambda r: r['metadata'].update(message_count=True), lambda r: r.update(conversation_id='conflict'),
])
def test_bad_or_conflicting_source_evidence_is_not_repaired(plugin_instance, change):
    value = record()
    change(value)
    assert mod(plugin_instance).normalize_record(value, 'TestRole', 9900, 10000) is None


def test_occurrence_clock_dedup_and_bounded_originals(plugin_instance):
    module = mod(plugin_instance)
    first = record(text='长' * 4000)
    normalized = module.normalize_record(first, 'TestRole', 9900, 10000)
    assert len(normalized['text'].encode()) <= 1600 and normalized['truncated']
    assert normalized['original_ref']['full_text_available'] is False
    late = copy.deepcopy(first)
    late['timestamp'] = 10010
    assert module.normalize_record(late, 'TestRole', 9900, 10010)['id'] == normalized['id']
    late['metadata']['ts'] = 10010
    assert module.normalize_record(late, 'TestRole', 9900, 10010)['id'] != normalized['id']
    del first['metadata']['ts']
    assert module.normalize_record(first, 'TestRole', 9900, 10000)['clock_source'] == 'arrival'


def test_observer_refreshes_without_review_and_preserves_base_leases(plugin_instance, monkeypatch, run_async):
    port, _, _, _, clock, save, host, push = prepare(plugin_instance, monkeypatch)
    rows = [record(role='master', turn='user_message')]
    query = attach_bus(plugin_instance, rows)
    before = host.path.read_bytes()
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
        assert result['submitted'] is False and not push.called
        assert query.await_args.kwargs == {'max_count': 60, 'since_ts': 10000, 'timeout': 0.35}
        assert host.read_recent(json.loads((port.directory / 'status.json').read_text())['host_recent_request'])['recent_messages'][0]['role'] == 'user'
        clock[0] += 3
        run_async(plugin_instance.game_decision_heartbeat())
        assert query.await_count == 1
        clock[0] += 3
        save()
        run_async(plugin_instance.game_decision_heartbeat())
        assert query.await_count == 2
        assert host.path.read_bytes() == before
    assert '历史一句' not in json.dumps(port.state(), ensure_ascii=False)


@pytest.mark.parametrize('failure', ['empty', 'absent', 'err', 'mapping'])
def test_empty_is_unknown_coverage_and_query_failures_are_optional(plugin_instance, monkeypatch, run_async, failure):
    port, _, _, _, _, _, host, _ = prepare(plugin_instance, monkeypatch)
    namespace = SimpleNamespace() if failure == 'absent' else SimpleNamespace(get=AsyncMock(return_value={'err': Err('synthetic'), 'mapping': {'items': []}}.get(failure, [])))
    plugin_instance._test_bus = SdkBusContext(SimpleNamespace(conversations=namespace), host_ctx=plugin_instance.ctx)
    with session_lease(port.directory):
        assert not run_async(plugin_instance.game_decision_heartbeat()).value['submitted']
    if failure in {'empty', 'absent'}:
        value = json.loads(host.recent_path.read_text())
        assert value['coverage']['state'] == 'unavailable'
        assert 'coverage_unknown' in value['coverage']['reasons']
    else:
        assert not host.recent_path.exists()


@pytest.mark.parametrize('boundary', ['stop', 'generation', 'writes', 'run', 'session', 'scope', 'revision', 'death', 'actor', 'as_of'])
def test_inflight_query_cannot_cross_binding_or_config_boundary(plugin_instance, monkeypatch, run_async, boundary):
    port, snapshot, raw, settings, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    module = mod(plugin_instance)
    async def query(**_):
        if boundary == 'stop':
            plugin_instance._operator_state(True)
        elif boundary == 'generation':
            plugin_instance._generation += 1
        elif boundary == 'writes':
            plugin_instance._configuration_writes = 1
        elif boundary == 'actor':
            host.publish({'agent_name': 'OtherRole'}, session='123:', revision=0)
        elif boundary == 'revision':
            import uuid
            port.mailbox.submit('goal', {'goal': 'new user', 'origin': 'user'}, str(uuid.uuid4()))
        elif boundary == 'as_of':
            clock[0] += 1
            save()
        else:
            snapshot[{'run': 'native_run', 'session': 'runtime_session', 'scope': 'game_identity', 'death': 'body_death_episode_id'}[boundary]] = 'changed'
            save()
        return [record()]
    plugin_instance._test_bus = SdkBusContext(SimpleNamespace(conversations=SimpleNamespace(get=query)), host_ctx=plugin_instance.ctx)
    with session_lease(port.directory):
        result = run_async(module.refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
    assert not result['available'] and not host.recent_path.exists()


def test_new_as_of_requeries_without_resetting_same_run_floor(plugin_instance, monkeypatch, run_async):
    port, _, raw, settings, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    query = attach_bus(plugin_instance, [record()])
    module = mod(plugin_instance)
    with session_lease(port.directory):
        run_async(module.refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
        floor = plugin_instance._recent_session_floor
        clock[0] += 1
        save()
        run_async(module.refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
    assert query.await_count == 2
    assert plugin_instance._recent_session_floor == floor
    assert host.recent_path.exists()


def test_timeout_awaits_wrapper_cancellation_without_retry(plugin_instance, monkeypatch, run_async):
    port, _, raw, settings, _, _, host, _ = prepare(plugin_instance, monkeypatch)
    cancelled = []
    async def query(**_):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    query_mock = AsyncMock(side_effect=query)
    plugin_instance._test_bus = SdkBusContext(SimpleNamespace(conversations=SimpleNamespace(get=query_mock)), host_ctx=plugin_instance.ctx)
    with session_lease(port.directory):
        result = run_async(mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
    assert not result['available'] and cancelled == [True] and query_mock.await_count == 1
    assert not host.recent_path.exists()


def test_real_sdk_executor_wrapper_cancellation_does_not_claim_thread_kill(plugin_instance, monkeypatch, run_async):
    port, _, raw, settings, _, _, _, _ = prepare(plugin_instance, monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    client = ConversationClient(SimpleNamespace())
    def bounded_worker(**kwargs):
        assert kwargs['timeout'] == 0.35
        entered.set()
        release.wait(1)
        finished.set()
        return []
    monkeypatch.setattr(client, '_get_impl', bounded_worker)
    plugin_instance._test_bus = SdkBusContext(SimpleNamespace(conversations=client), host_ctx=plugin_instance.ctx)
    async def scenario():
        task = asyncio.create_task(mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
        while not entered.is_set():
            await asyncio.sleep(0)
        result = await task
        assert not result['available'] and not finished.is_set()
        release.set()
    with session_lease(port.directory):
        run_async(scenario())
    assert finished.wait(1)


def test_fresh_profile_recovers_expired_actor_without_reviving_dynamic_context(plugin_instance, monkeypatch, run_async):
    port, snapshot, raw, _, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    raw['host_context'] = {'character_api_url': 'http://127.0.0.1:1234/api/characters'}
    clock[0] = 10610
    snapshot['host_recent_request'] = None
    save()
    attach_bus(plugin_instance, [])
    client_class = httpx.AsyncClient
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'猫娘': {'TestRole': {'system_prompt': 'New public persona'}}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))
    before = host.path.read_bytes()
    with session_lease(port.directory):
        run_async(plugin_instance.game_decision_heartbeat())
        assert host.read(0)['stable']['persona'] == 'New public persona'
        assert host.read(0)['dynamic']['current_intent'] == ''
        assert host.path.read_bytes() == before and not host.recent_path.exists()
        issued = json.loads((port.directory / 'host-character.json').read_text())['issued_at']
        clock[0] += 10
        save()
        run_async(plugin_instance.game_decision_heartbeat())
        assert len(calls) == 1 and json.loads((port.directory / 'host-character.json').read_text())['issued_at'] == issued


@pytest.mark.parametrize('failure', ['http', 'stop', 'generation', 'scope'])
def test_profile_failure_or_changed_owner_cannot_publish_late_persona(plugin_instance, monkeypatch, run_async, failure):
    port, snapshot, raw, settings, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    raw['host_context'] = {'character_api_url': 'http://localhost:1234/api/characters'}
    clock[0] = 10610
    save()
    client_class = httpx.AsyncClient
    async def handler(request):
        if failure == 'stop':
            plugin_instance._operator_state(True)
        elif failure == 'generation':
            plugin_instance._generation += 1
        elif failure == 'scope':
            snapshot['game_identity'] = {**SCOPE, 'realm': 'different'}
            save()
        return httpx.Response(500 if failure == 'http' else 200, json={'猫娘': {'TestRole': {'system_prompt': 'Late'}}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))
    with session_lease(port.directory):
        run_async(mod(plugin_instance).refresh_profile(plugin_instance, raw, settings, 'TestRole', port, 0))
    assert not (port.directory / 'host-character.json').exists() and host.read(0) is None


def test_selection_saturation_latest_user_hint_and_private_bounds(plugin_instance, monkeypatch, run_async):
    port, _, raw, settings, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    attach_bus(plugin_instance, [])
    with session_lease(port.directory):
        run_async(plugin_instance.game_decision_heartbeat())
        clock[0] = 10060
        save()
        rows = [record('latest user', 10001, 'master', turn='user_message')]
        rows += [record('same repeated assistant' if i % 2 else 'different assistant', 10002 + i) for i in range(59)]
        query = attach_bus(plugin_instance, rows)
        result = run_async(mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
        assert result['available'] and result['coverage']['saturated']
        value = json.loads(host.recent_path.read_text(encoding='utf-8'))
        assert len(value['recent_messages']) == 4 and value['recent_messages'][0]['role'] == 'user'
        assert host_recent.size(value) <= 8192 and value['coverage']['received_count'] == 60
        hint = mod(plugin_instance).historical_hint(plugin_instance, settings, 'TestRole', port, 0)
        assert len(hint['utterances']) == 2 and host_recent.size(hint) <= 600
        assert query.await_args.kwargs['since_ts'] == 10000
        assert 'latest user' not in json.dumps(port.state())


def test_cached_profile_is_not_a_fresh_get_for_observer(plugin_instance, monkeypatch, run_async):
    port, _, raw, settings, clock, save, _, _ = prepare(plugin_instance, monkeypatch)
    config = {'character_api_url': 'http://localhost:1234/api/characters'}
    raw['host_context'] = config
    plugin_instance._host_profile_cache = ((config['character_api_url'], 'TestRole'), time.monotonic(),
        {'agent_name': 'TestRole', 'persona': 'Cached old', 'persona_source': 'character_profile'})
    clock[0] = 10610
    save()
    client_class = httpx.AsyncClient
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(503)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw))
    with session_lease(port.directory):
        for _ in range(2):
            run_async(mod(plugin_instance).refresh_profile(plugin_instance, raw, settings, 'TestRole', port, 0))
    assert len(calls) == 1 and not (port.directory / 'host-character.json').exists()


def test_stop_cancels_owned_inflight_wrapper_before_its_deadline(plugin_instance, monkeypatch, run_async):
    port, _, raw, settings, _, _, _, _ = prepare(plugin_instance, monkeypatch)
    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def query(**_):
            entered.set()
            try:
                await asyncio.sleep(20)
            finally:
                cancelled.set()
        plugin_instance._test_bus = SdkBusContext(SimpleNamespace(conversations=SimpleNamespace(get=query)), host_ctx=plugin_instance.ctx)
        task = asyncio.create_task(mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
        await entered.wait()
        plugin_instance._operator_state(True)
        result = await asyncio.wait_for(task, 0.3)
        assert not result['available'] and cancelled.is_set()
    with session_lease(port.directory):
        run_async(scenario())


@pytest.mark.parametrize('boundary', ['run', 'actor', 'generation'])
def test_observed_floor_resets_on_actual_run_actor_generation(plugin_instance, monkeypatch, run_async, boundary):
    port, snapshot, raw, settings, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    query = attach_bus(plugin_instance, [])
    with session_lease(port.directory):
        run_async(mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))
        clock[0] += 10
        target, generation = 'TestRole', 0
        if boundary == 'run':
            snapshot['native_run'] = snapshot['host_recent_request']['run'] = 'native-2'
        elif boundary == 'actor':
            target = 'OtherRole'
            snapshot['host_recent_request']['actor'] = target
            host.publish({'agent_name': target}, session='123:', revision=0)
        else:
            generation = plugin_instance._generation = 1
        save()
        run_async(mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, target, port, generation))
        assert query.await_args.kwargs['since_ts'] == 10010


def test_ordinary_inspect_detail_and_final_do_not_query_public_bus(plugin_instance, monkeypatch, run_async):
    port, snapshot, _, _, _, _, _, _ = prepare(plugin_instance, monkeypatch)
    query = attach_bus(plugin_instance, [record()])
    port.observe(snapshot)
    with session_lease(port.directory):
        review = run_async(plugin_instance.game_decision_heartbeat()).value['heartbeat_id']
        detail = run_async(plugin_instance.minecraft_autonomy_review(review, 'inspect'))
        run_async(plugin_instance.minecraft_autonomy_review(review, 'inspect', evidence_ref=detail['evidence_ref']))
        run_async(plugin_instance.minecraft_autonomy_review(review, 'inspect', lineage_offset=0))
        run_async(plugin_instance.minecraft_autonomy_review(review, 'continue', 'Keep working'))
        assert query.await_count == 1


def test_review_history_keeps_substantive_tail_instead_of_repeated_process_prelude(plugin_instance, monkeypatch, run_async):
    port, snapshot, _, settings, _, _, host, _ = prepare(plugin_instance, monkeypatch)
    text = '本喵先查一下现场再判断。本喵查完了，直接提交判断。' * 8 + '洞口有一朵小蘑菇，我想给它留盏灯。'
    query = attach_bus(plugin_instance, [record(text)])
    port.observe(snapshot)
    with session_lease(port.directory):
        review = run_async(plugin_instance.game_decision_heartbeat()).value['heartbeat_id']
        detail = run_async(plugin_instance.minecraft_autonomy_review(review, 'inspect'))
        hint = detail['recent_language_hint']
        assert '小蘑菇' in hint['utterances'][-1]['text']
        assert '先查一下现场' not in hint['utterances'][-1]['text']
        assert hint['purpose'] == 'avoid_repetition'
        assert hint['utterances'][-1]['excerpt'] == 'tail'
        assert hint['historical_only'] and not hint['audio_verified']
        assert host_recent.size(hint) <= 600 and query.await_count == 1
        original = json.loads(host.recent_path.read_text(encoding='utf-8'))['recent_messages'][-1]
        assert original['text'] == text and original['text_sha256'] == hint['utterances'][-1]['sha256']
        assert not port.state()['review'].get('result')
