"""Real canonical model rounds consume the adapter's atomic file publication."""

import copy
import json
from types import SimpleNamespace

from agent_mc_autonomy import controller as framework
from agent_mc_autonomy.ownership import session_lease
from test_recent_context import SCOPE, attach_bus, mod, prepare, record


def packed(value):
    return {'content': [{'type': 'text', 'text': json.dumps(value)}]}


class Body:
    def __init__(self):
        self.calls = []
        self.tools = [{'name': 'mc_help', 'inputSchema': {'type': 'object'}},
                      {'name': 'mc_do', 'inputSchema': {'type': 'object'}, '_meta': {'agent-mc/v1': {'tags': ['act']}}}]

    async def call(self, name, args, **kwargs):
        self.calls.append((name, args))
        if name == 'mc_events':
            return packed({'run': 'native-1', 'nextCursor': len(self.calls), 'events': []})
        if name == 'mc_protocol':
            return packed({'run': 'native-1', 'parts': [
                {'key': 'identity', 'text': json.dumps(SCOPE)},
                {'key': 'body', 'text': 'CURRENT NATIVE HEALTH'},
                {'key': 'gear', 'text': 'CURRENT NATIVE INVENTORY'}]} if args['action'] == 'facts' else {})
        if name == 'mc_help':
            return {'structuredContent': {'protocol': 'agent-mc/v1', 'text': 'Synthetic reference'}}
        if name == 'mc_do':
            return {'structuredContent': {'protocol': 'agent-mc/v1', 'text': 'Accepted, not completed',
                    'execution': {'kind': 'task', 'id': 'native-work', 'phase': 'accepted', 'terminal': False, 'goalCompleted': False},
                    'control': {'requestId': kwargs['request_id'], 'barrierAfter': True, 'endTurnRequested': False}}}
        if name == 'mc_artifact' and args['action'] == 'put':
            return packed({'handle': 'log:synthetic'})
        raise AssertionError((name, args))


def test_two_actual_canonical_rounds_read_updated_file_and_local_originals(plugin_instance, monkeypatch, run_async):
    port, snapshot, raw, settings, clock, save, host, _ = prepare(plugin_instance, monkeypatch)
    monkeypatch.setattr(framework, 'time', SimpleNamespace(**(vars(framework.time) | {'time': lambda: clock[0]})))
    body, captured, published = Body(), [], []
    attach_bus(plugin_instance, [record('First committed wording')])
    def publish(**values):
        published.append(copy.deepcopy(values))
        snapshot.update(values)
        save()
    async def decide(messages, tools):
        captured.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        if len(captured) == 1:
            clock[0] += 10
            save()
            attach_bus(plugin_instance, [record('Second committed wording', clock[0])])
            result = await mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0)
            assert result['available']
        return {'content': None, 'tool_calls': [{'id': 'call-' + str(len(captured)), 'type': 'function',
                'function': {'name': 'mc_help' if len(captured) == 1 else 'mc_do', 'arguments': '{}' if len(captured) == 1 else '{"steps":[]}'}}]}
    controller = framework.Controller(body, decide, settings, port.directory, publish, lambda: False,
                                      commands=port.mailbox, runtime_session='runtime-1')
    controller.event_run = 'native-1'
    controller.host_context_port.now = port.now
    # Default providers remain actual canonical bound file methods.
    assert controller.recent_context_provider.__self__ is controller.host_context_port
    async def scenario():
        assert (await mod(plugin_instance).refresh_recent(plugin_instance, raw, settings, 'TestRole', port, 0))['available']
        await controller.turn_once('ACTUAL CURRENT GOAL', 'Stable body rules', framework.model_tools(body.tools))
        assert len(captured) == 2
        tails = [json.loads(messages[-1]['content']) for messages, _ in captured]
        assert tails[0]['host_recent_context']['recent_messages'][-1]['text'] == 'First committed wording'
        assert tails[1]['host_recent_context']['recent_messages'][-1]['text'] == 'Second committed wording'
        assert captured[0][0][0] == captured[1][0][0] and captured[0][1] == captured[1][1]
        assert all('CURRENT NATIVE HEALTH' in json.dumps(t['current_facts']) for t in tails)
        assert 'ACTUAL CURRENT GOAL' in captured[0][0][1]['content']
        handle = tails[1]['host_recent_context']['recent_messages'][-1]['original_ref']['handle']
        before = len(body.calls)
        original = json.loads((await controller.original_page({'handle': handle}))['text'])
        assert original['text'] == 'Second committed wording' and len(body.calls) == before
    with session_lease(port.directory):
        run_async(scenario())
    assert 'committed wording' not in json.dumps(published)
    assert 'committed wording' not in json.dumps(controller.groups)
    assert 'committed wording' not in json.dumps(controller.recent)
    assert host.path.exists() and controller.pending[('task', 'native-work')]['terminal'] is False
