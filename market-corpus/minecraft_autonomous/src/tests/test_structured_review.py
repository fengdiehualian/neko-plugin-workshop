import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_mc_autonomy.ownership import session_lease
from test_recent_context import prepare


def response(arguments, content='I need to inspect first.</think>', *, finish_reason='tool_calls'):
    call = SimpleNamespace(id='review-call', function=SimpleNamespace(name='submit_game_review', arguments=json.dumps(arguments)))
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(
        content=content, tool_calls=[call]))])


def configured(plugin, monkeypatch):
    port, snapshot, raw, settings, clock, save, host, push = prepare(plugin, monkeypatch)
    raw['heartbeat']['structured_reviews'] = True
    create = AsyncMock()
    plugin._test_review_gateway = SimpleNamespace(aclose=AsyncMock(), get_client=AsyncMock(return_value=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    monkeypatch.setattr(type(plugin.ctx), 'models', property(lambda _: plugin._test_review_gateway))
    port.observe(snapshot)
    return port, snapshot, raw, settings, clock, save, host, push, create


def test_model_prelude_never_enters_host_chat_or_voice(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, push, create = configured(plugin_instance, monkeypatch)
    create.return_value = response({'decision': 'continue', 'reason': 'Keep useful work',
                                      'spoken_text': '这片树林真漂亮，沿着河走走吧。'})
    speech = importlib.import_module(plugin_instance.__class__.__module__ + '.neko_speech')
    lines = []
    def enqueue(*args, **kwargs):
        lines.append(args[6])
        return {'status': 'enqueued'}
    monkeypatch.setattr(speech, 'enqueue_speech', enqueue)
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is True
    assert lines == ['这片树林真漂亮，沿着河走走吧。']
    assert create.await_count == 1
    call = create.await_args.kwargs
    assert call['model'] == 'decision' and call['stream'] is False
    assert call['tool_choice'] == 'auto'
    assert 'Synthetic' in json.dumps(call['messages'], ensure_ascii=False)
    assert 'Protect house' in json.dumps(call['messages'], ensure_ascii=False)
    plugin_instance._test_review_gateway.aclose.assert_awaited_once()
    assert all(call.kwargs['ai_behavior'] == 'read' for call in push.call_args_list)
    assert 'I need' not in json.dumps([call.kwargs for call in push.call_args_list], ensure_ascii=False)
    assert port.state()['review']['result']['acknowledged']


def test_free_text_cannot_replace_a_structured_decision(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, push, create = configured(plugin_instance, monkeypatch)
    create.return_value = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content='I need to inspect first.', tool_calls=[]))])
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is False
    assert result['reason'] == 'structured_review_invalid_response'
    assert not port.state()['review'].get('result')
    assert all(call.kwargs['ai_behavior'] == 'read' for call in push.call_args_list)
    assert not getattr(plugin_instance, '_speech_output_tasks', None)


@pytest.mark.parametrize('finish_reason', ['stop', 'tool_calls'])
def test_accepts_complete_structured_review_with_provider_stop(plugin_instance, monkeypatch, run_async, finish_reason):
    port, _, _, _, _, _, _, push, create = configured(plugin_instance, monkeypatch)
    create.return_value = response({'decision': 'continue', 'reason': 'Keep current work'},
                                   content=None, finish_reason=finish_reason)
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is True
    assert create.await_count == 1
    assert port.state()['review']['result']['acknowledged']
    assert not getattr(plugin_instance, '_speech_output_tasks', None)
    assert all(call.kwargs['ai_behavior'] == 'read' for call in push.call_args_list)


@pytest.mark.parametrize('finish_reason', ['length', 'content_filter', 'error', None])
def test_rejects_incomplete_review_even_with_a_tool_call(plugin_instance, monkeypatch, run_async, finish_reason):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    create.return_value = response({'decision': 'continue', 'reason': 'Do not adopt truncated work'},
                                   content=None, finish_reason=finish_reason)
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is False
    assert result['reason'] == 'structured_review_invalid_response'
    assert not port.state()['review'].get('result')
    assert not getattr(plugin_instance, '_speech_output_tasks', None)


@pytest.mark.parametrize('budget', [3072, 8192])
def test_review_honors_configured_output_budget(plugin_instance, monkeypatch, run_async, budget):
    port, _, raw, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    raw['autonomy']['max_output_tokens'] = budget
    create.return_value = response({'decision': 'continue', 'reason': 'Complete within the configured budget'})
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is True
    assert create.await_args.kwargs['max_completion_tokens'] == min(budget, 3072)
    assert create.await_count == 1


def test_review_budget_is_independent_of_body_budget(plugin_instance, monkeypatch, run_async):
    port, _, raw, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    raw['autonomy']['max_output_tokens'] = 8192
    raw['heartbeat']['review_max_output_tokens'] = 1536
    create.return_value = response({'decision': 'continue', 'reason': 'Keep the current stage'})
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is True
    assert create.await_args.kwargs['max_completion_tokens'] == 1536
    assert raw['autonomy']['max_output_tokens'] == 8192


def test_canonical_brief_checkpoint_is_before_persona_without_provider_extensions(plugin_instance, monkeypatch, run_async):
    port, _, raw, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    raw['autonomy']['max_output_tokens'] = 8192
    async def answer(**_):
        assert attempt_journal(port)[0]['event'] == 'model_request_started'
        return response({'decision': 'continue', 'reason': 'Keep useful work'})
    create.side_effect = answer
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    args = create.await_args.kwargs
    packet = json.loads(args['messages'][1]['content'])
    guidance = packet['inspection']['completion_contract']['reasoning_guidance']
    output = packet['inspection']['completion_contract']['output_guidance']
    assert args['messages'][0]['content'].startswith(guidance+'\n'+output+'\n')
    assert packet['actor']['persona'] == 'Synthetic'
    assert result['model_attempts'][0]['brief_checkpoint_guidance'] is True
    started = attempt_journal(port)[0]
    assert started['event'] == 'model_request_started' and started['brief_checkpoint_guidance'] is True
    assert args['max_completion_tokens'] == 3072 and args['tool_choice'] == 'auto'
    assert not {'reasoning_effort', 'thinking', 'chat_template_kwargs', 'extra_body'} & args.keys()
    assert result['main_brain_acknowledged'] and not port.mailbox.ready()


@pytest.mark.parametrize('outcome',['success','timeout','cancelled'])
def test_shared_model_window_brackets_actual_review_and_releases_on_every_exit(plugin_instance,monkeypatch,run_async,outcome):
    port,_,raw,_,_,_,_,_,create=configured(plugin_instance,monkeypatch)
    raw['autonomy']['review_model_pool']='shared-local-test'
    transport=importlib.import_module(plugin_instance.__class__.__module__+'.neko_structured_review')
    seen=[]
    async def answer(**_):
        window=port.model_inference_window('shared-local-test')
        assert window is not None
        seen.append(window['id'])
        if outcome=='timeout':
            raise TimeoutError()
        if outcome=='cancelled':
            raise transport.asyncio.CancelledError()
        return response({'decision':'continue','reason':'Keep ongoing work'})
    create.side_effect=answer
    with session_lease(port.directory):
        if outcome=='cancelled':
            with pytest.raises(transport.asyncio.CancelledError):
                run_async(plugin_instance.game_decision_heartbeat())
        else:
            result=run_async(plugin_instance.game_decision_heartbeat()).value
            assert result['main_brain_acknowledged'] is (outcome=='success')
            assert result['shared_model_window']['released'] is True
    assert seen and port.model_inference_window('shared-local-test') is None
    assert 'model_inference_window' not in port.state()
    attempt=next(row for row in attempt_journal(port) if row['event']=='model_attempt')
    assert attempt['shared_model_window_id']==seen[0]
    assert create.await_count==1 and not port.mailbox.ready()


def test_shared_window_release_error_does_not_change_an_adopted_review(plugin_instance,monkeypatch,run_async):
    port,_,raw,_,_,_,_,_,create=configured(plugin_instance,monkeypatch)
    raw['autonomy']['review_model_pool']='shared-local-test'
    def fail(_):raise OSError('PRIVATE_SQLITE_PATH')
    monkeypatch.setattr(port,'end_model_inference',fail)
    create.return_value=response({'decision':'continue','reason':'Valid final choice'})
    with session_lease(port.directory):
        result=run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] and create.await_count==1
    assert result['shared_model_window']['released'] is False
    assert result['shared_model_window']['release_error']=='review_store_unavailable'
    assert port.model_inference_window('shared-local-test') is None  # Acknowledged review cannot hold the body.
    assert 'PRIVATE_' not in json.dumps(result)


def test_coordination_time_is_charged_to_the_original_review_deadline(plugin_instance,monkeypatch,run_async):
    port,_,raw,_,clock,save,_,_,create=configured(plugin_instance,monkeypatch)
    raw['autonomy']['review_model_pool']='shared-local-test'
    begin=port.begin_model_inference
    def delayed(*args):
        result=begin(*args)
        clock[0]+=5
        save()
        return result
    monkeypatch.setattr(port,'begin_model_inference',delayed)
    create.return_value=response({'decision':'continue','reason':'Keep current work'})
    with session_lease(port.directory):
        result=run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged']
    request=next(row for row in attempt_journal(port) if row['event']=='model_request_started')
    assert request['wait_budget_seconds']<=173


def test_optional_coordination_failure_keeps_the_existing_review_path(plugin_instance,monkeypatch,run_async):
    port,_,raw,_,_,_,_,_,create=configured(plugin_instance,monkeypatch)
    raw['autonomy']['review_model_pool']='shared-local-test'
    def fail(*_):
        raise OSError('PRIVATE_COORDINATION_PATH')
    monkeypatch.setattr(port,'begin_model_inference',fail)
    create.return_value=response({'decision':'continue','reason':'Valid original review'})
    with session_lease(port.directory):
        result=run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] and create.await_count==1
    assert result['shared_model_window']=={'enabled':True,'granted':False,'released':None,'coordination_error':'review_store_unavailable'}
    assert 'PRIVATE_' not in json.dumps(result)


def test_truncated_review_records_budget_failure_without_adoption(plugin_instance, monkeypatch, run_async):
    port, _, raw, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    raw['autonomy']['max_output_tokens'] = 3072
    create.return_value = response({'decision': 'continue', 'reason': 'Never adopt partial reasoning'},
                                   finish_reason='length')
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['failure_category'] == 'output_budget_exhausted'
    assert result['max_output_tokens'] == 3072
    assert not result['main_brain_acknowledged']
    assert not port.state()['review'].get('result')
    assert not getattr(plugin_instance, '_speech_output_tasks', None)
    assert create.await_count == 1


@pytest.mark.parametrize('change', ['stop', 'generation', 'run', 'intent'])
def test_late_review_cannot_change_a_stopped_or_replaced_body(plugin_instance, monkeypatch, run_async, change):
    port, snapshot, _, _, _, save, _, _, create = configured(plugin_instance, monkeypatch)
    async def answer(**kwargs):
        if change == 'stop':
            plugin_instance._operator_state(True)
        elif change == 'generation':
            plugin_instance._generation += 1
        elif change == 'run':
            snapshot['native_run'] = 'other-run'
            save()
        else:
            port.mailbox.submit_user_intent('feedback', {'goal': 'New operator goal', 'mode': 'insert'},
                                           '11111111-1111-4111-8111-111111111111')
        return response({'decision': 'insert', 'reason': 'Try a different plan', 'goal': 'Old stale plan'})
    create.side_effect = answer
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is False
    assert result['reason'] == 'structured_review_superseded'
    assert not port.state()['review'].get('result')


def test_missing_gateway_does_not_fall_back_to_an_unfiltered_reply(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, push, _ = configured(plugin_instance, monkeypatch)
    plugin_instance._test_review_gateway = None
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['reason'] == 'structured_review_unavailable'
    assert not result['main_brain_acknowledged']
    assert all(call.kwargs['ai_behavior'] == 'read' for call in push.call_args_list)


def test_control_text_in_explicit_voice_is_not_speakable(plugin_module):
    speech = importlib.import_module(plugin_module.__name__ + '.neko_speech')
    assert speech.sanitize_speech('<think>I need to inspect first.</think>我们去找棵树吧。') == '我们去找棵树吧。'


def test_rejected_stage_choice_returns_actual_error_once(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    first = response({'decision': 'revise', 'reason': 'Change the stage', 'goal': 'New stage'})
    first.choices[0].message.reasoning_content = 'Private provider reasoning; never transport as free text.'
    create.side_effect = [first,
                          response({'decision': 'continue', 'reason': 'No stage exists to revise'})]
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is True
    assert result['correction_attempts'] == 1
    assert create.await_count == 2
    messages = create.await_args.kwargs['messages']
    assert [m['role'] for m in messages] == ['system', 'user', 'user']
    correction = json.loads(messages[-1]['content'])
    assert correction['rejected_candidate']['decision'] == 'revise'
    assert correction['rejection']['error'] == 'no_active_review_stage'
    assert 'Private provider reasoning' not in json.dumps(messages)
    assert 'I need' not in json.dumps(messages)
    assert port.state()['last_decision']['decision'] == 'continue'
    assert port.mailbox.intent_revision() == 0


def test_continue_with_corrective_goal_must_be_corrected_before_adoption(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    goal = 'Inspect why the same planting attempt has no effect'
    create.side_effect = [response({'decision': 'continue', 'reason': 'Change ineffective work', 'goal': goal}),
                          response({'decision': 'insert', 'reason': 'Change ineffective work', 'goal': goal})]
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged']
    assert result['correction_attempts'] == 1
    assert create.await_count == 2
    correction = json.loads(create.await_args.kwargs['messages'][-1]['content'])
    assert correction['rejection']['error'] == 'continue_cannot_deliver_new_goal'
    command = port.mailbox.claim()
    assert command['payload']['goal'] == goal
    assert command['payload']['review_decision'] == 'insert'
    assert port.mailbox.claim() is None


def evidence_response(path, offset=0):
    value = response({'detail_path': path, 'detail_offset': offset})
    value.choices[0].message.tool_calls[0].function.name = 'read_game_review_evidence'
    return value


def test_structured_review_can_read_referenced_original_before_final_choice(plugin_instance, monkeypatch, run_async):
    port, snapshot, _, _, _, save, _, _, create = configured(plugin_instance, monkeypatch)
    detail = 'ordinary prefix ' * 100 + 'FLOWING_WATER_CANNOT_BE_COLLECTED'
    snapshot['facts']['parts'].append({'key': 'serverFeedback', 'text': detail})
    save()
    create.side_effect = [evidence_response(['evidence', 'facts', 'serverFeedback'], 1000),
                          response({'decision': 'continue', 'reason': 'No safe new stage yet'})]
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged']
    assert result['evidence_reads'] == 1 and result['correction_attempts'] == 0
    assert create.await_count == 2
    delivered = json.loads(create.await_args.kwargs['messages'][-1]['content'])['evidence_read']
    assert delivered['historical'] and not delivered['grants_inspection']
    assert delivered['path'] == ['evidence', 'facts', 'serverFeedback']
    assert delivered['offset'] == 1000
    assert 'FLOWING_WATER_CANNOT_BE_COLLECTED' in delivered['json_chunk']
    assert port.mailbox.intent_revision() == 0
    assert not getattr(plugin_instance, '_speech_output_tasks', None)


def test_long_original_tail_is_reachable_with_one_bounded_read(plugin_instance, monkeypatch, run_async):
    port, snapshot, _, _, _, save, _, _, create = configured(plugin_instance, monkeypatch)
    snapshot['facts']['parts'].append({'key': 'serverFeedback',
        'text': 'historical ordinary data ' * 350 + 'ACTION_NOT_COUNTED_AT_THIS_SITE'})
    save()
    async def answer(**kwargs):
        if create.await_count == 1:
            inspection = json.loads(kwargs['messages'][1]['content'])['inspection']
            query = inspection['fact_detail_queries']['serverFeedback']
            return evidence_response(query['detail_path'], max(0, query['json_characters'] - 768))
        detail = json.loads(kwargs['messages'][-1]['content'])['evidence_read']
        assert 'ACTION_NOT_COUNTED_AT_THIS_SITE' in detail['json_chunk']
        return response({'decision': 'insert', 'reason': 'Read the omitted refusal',
                         'goal': 'Reassess the site before repeating the operation'})
    create.side_effect = answer
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] and result['evidence_reads'] == 1
    assert create.await_count == 2
    assert port.mailbox.claim()['payload']['goal'] == 'Reassess the site before repeating the operation'


def test_review_original_reads_are_bounded_and_keep_original_deadline(plugin_instance, monkeypatch, run_async):
    port, _, _, _, clock, save, _, _, create = configured(plugin_instance, monkeypatch)
    transport = importlib.import_module(plugin_instance.__class__.__module__ + '.neko_structured_review')
    actual_wait, waits = transport.asyncio.wait_for, []
    async def wait_for(coro, timeout):
        if timeout > 1:
            waits.append(timeout)
        return await actual_wait(coro, timeout)
    monkeypatch.setattr(transport.asyncio, 'wait_for', wait_for)
    async def answer(**_):
        clock[0] += 10
        save()
        return evidence_response(['evidence', 'standing_goal'])
    create.side_effect = answer
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert not result['main_brain_acknowledged']
    assert result['reason'] == 'structured_review_evidence_budget_exhausted'
    assert result['evidence_reads'] == 4
    assert create.await_count == 5
    assert waits[-1] == waits[0] - 40
    assert not port.state()['review'].get('choice')
    assert not port.mailbox.ready()
    assert not getattr(plugin_instance, '_speech_output_tasks', None)


def test_read_after_refused_goal_keeps_both_the_rejection_and_original_evidence(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    goal = 'Use a revised approach'
    create.side_effect = [response({'decision': 'continue', 'reason': 'Old plan is ineffective', 'goal': goal}),
                          evidence_response(['evidence', 'standing_goal']),
                          response({'decision': 'insert', 'reason': 'Evidence checked', 'goal': goal})]
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] and result['evidence_reads'] == 1
    assert result['correction_attempts'] == 1 and create.await_count == 3
    contexts = [json.loads(m['content']) for m in create.await_args.kwargs['messages'][2:]]
    assert contexts[0]['rejection']['error'] == 'continue_cannot_deliver_new_goal'
    assert contexts[1]['evidence_read']['historical']
    assert port.mailbox.claim()['payload']['goal'] == goal


def test_evidence_read_cannot_switch_to_another_reference(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    query = evidence_response(['evidence', 'standing_goal'])
    query.choices[0].message.tool_calls[0].function.arguments = json.dumps({
        'detail_path': [], 'evidence_ref': '0' * 24})
    create.return_value = query
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert not result['main_brain_acknowledged'] and create.await_count == 1
    assert result['reason'] == 'structured_review_invalid_response'
    assert not port.mailbox.ready()


def test_rejected_review_correction_is_bounded(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    create.return_value = response({'decision': 'revise', 'reason': 'Change the stage', 'goal': 'New stage'})
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is False
    assert result['decision_error'] == 'no_active_review_stage'
    assert result['correction_attempts'] == 1
    assert create.await_count == 2
    assert not port.state()['review'].get('choice')


def test_transport_timeout_does_not_retry_an_unknown_review(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    create.side_effect = TimeoutError()
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['reason'] == 'structured_review_unavailable'
    assert create.await_count == 1
    assert result['failure_category'] == 'deadline_exceeded'
    assert not port.state()['review'].get('choice')


def attempt_journal(port):
    return [json.loads(line) for line in (port.directory / 'structured-review-attempts.jsonl').read_text('utf-8').splitlines()]


def test_actual_gateway_finish_and_usage_survive_later_timeout(plugin_instance, monkeypatch, run_async):
    port, snapshot, raw, _, clock, save, _, _, create = configured(plugin_instance, monkeypatch)
    raw['autonomy']['max_output_tokens'] = 8192
    truncated = response({'decision': 'continue', 'reason': 'PRIVATE_TOOL_ARGUMENT'},
                         content='PRIVATE_MODEL_CONTENT', finish_reason='length')
    truncated.choices[0].message.reasoning_content = 'PRIVATE_REASONING'
    truncated.usage = SimpleNamespace(prompt_tokens=4200, completion_tokens=3072, total_tokens=7272,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=3072),
        prompt_tokens_details=SimpleNamespace(cached_tokens=4096))
    create.side_effect = [truncated, TimeoutError('PRIVATE_TRANSPORT_DETAIL')]
    with session_lease(port.directory):
        first = run_async(plugin_instance.game_decision_heartbeat()).value
        clock[0] += 100  # A second offer must respect the canonical 90s backoff.
        snapshot['native_outcomes'] = [{'tool': 'mc_do', 'execution_id': 'task-7', 'phase': 'blocked',
            'receipt': 'Actual path refusal', 'observation_only': False,
            'original_event': {'run': 'native-1', 'cursor': 7}}]
        snapshot['tool_failure'] = {'tool': 'mc_do', 'error': 'Actual new refusal',
            'native_execution': {'phase': 'blocked'}, 'native_event': {'run': 'native-1', 'cursor': 7}}
        save()
        port.observe(snapshot)  # The independent body publishes the next real boundary.
        second = run_async(plugin_instance.game_decision_heartbeat()).value
    assert first['failure_category'] == 'output_budget_exhausted'
    assert second.get('failure_category') == 'deadline_exceeded', second
    latest = json.loads((port.directory / 'structured-review.json').read_text('utf-8'))
    assert latest['failure_category'] == 'deadline_exceeded'
    rows = attempt_journal(port)
    attempts = [row for row in rows if row['event'] == 'model_attempt']
    assert [row['outcome'] for row in attempts] == ['response_received', 'deadline_exceeded']
    assert attempts[0]['finish_reason'] == 'length'
    assert attempts[0]['usage'] == {'prompt_tokens': 4200, 'completion_tokens': 3072, 'total_tokens': 7272,
                                   'reasoning_tokens': 3072, 'cached_tokens': 4096}
    assert attempts[0]['tool_names'] == ['submit_game_review']
    assert attempts[0]['reasoning_characters'] == len('PRIVATE_REASONING')
    assert attempts[0]['max_output_tokens'] == 3072
    assert attempts[1]['finish_reason'] is None
    deliveries = [row for row in rows if row['event'] == 'review_delivery']
    assert [row['failure_category'] for row in deliveries] == ['output_budget_exhausted', 'deadline_exceeded']
    assert 'PRIVATE_' not in json.dumps(rows)
    assert create.await_count == 2 and not port.mailbox.ready()


def test_every_evidence_and_correction_round_gets_metadata_only(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    create.side_effect = [evidence_response(['evidence', 'standing_goal']),
        response({'decision': 'continue', 'reason': 'PRIVATE_REASON', 'goal': 'PRIVATE_NEW_STAGE'}),
        response({'decision': 'insert', 'reason': 'PRIVATE_REASON', 'goal': 'PRIVATE_NEW_STAGE'})]
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] and result['evidence_reads'] == 1
    rows = attempt_journal(port)
    attempts = [row for row in rows if row['event'] == 'model_attempt']
    assert [row['attempt'] for row in attempts] == [1, 2, 3]
    assert [row['tool_names'] for row in attempts] == [
        ['read_game_review_evidence'], ['submit_game_review'], ['submit_game_review']]
    assert all(row['usage'] == {} for row in attempts)  # Older gateways may omit usage.
    assert len(result['model_attempts']) == 3
    assert 'PRIVATE_' not in json.dumps(rows)
    assert port.mailbox.claim()['payload']['goal'] == 'PRIVATE_NEW_STAGE'


def test_cancelled_gateway_attempt_is_retained_without_adoption(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    transport = importlib.import_module(plugin_instance.__class__.__module__ + '.neko_structured_review')
    create.side_effect = transport.asyncio.CancelledError()
    with session_lease(port.directory), pytest.raises(transport.asyncio.CancelledError):
        run_async(plugin_instance.game_decision_heartbeat())
    rows = attempt_journal(port)
    assert [row['event'] for row in rows] == ['model_request_started', 'model_attempt']
    assert rows[0]['attempt_id'] == rows[1]['attempt_id']
    assert rows[1]['outcome'] == 'caller_cancelled'
    assert not port.state()['review'].get('choice') and not port.mailbox.ready()
    assert not plugin_instance._structured_review_busy


def test_untrusted_provider_labels_are_not_persisted(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    value = response({'decision': 'continue', 'reason': 'unused'}, finish_reason='PRIVATE_FINISH_LABEL')
    value.choices[0].message.tool_calls[0].function.name = 'PRIVATE_TOOL_LABEL'
    value.usage = SimpleNamespace(prompt_tokens=True, completion_tokens=-1, total_tokens=float('inf'))
    create.return_value = value
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert not result['main_brain_acknowledged']
    row = next(row for row in attempt_journal(port) if row['event'] == 'model_attempt')
    assert row['finish_reason'] == 'other' and row['tool_names'] == ['other'] and row['usage'] == {}
    assert 'PRIVATE_' not in json.dumps(attempt_journal(port))


def test_optional_audit_io_failure_cannot_change_review_adoption(plugin_instance, monkeypatch, run_async):
    port, _, _, _, _, _, _, _, create = configured(plugin_instance, monkeypatch)
    (port.directory / 'structured-review-attempts.jsonl').mkdir()
    create.return_value = response({'decision': 'continue', 'reason': 'Keep safe work'})
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] and create.await_count == 1
    assert result['model_attempts'][0]['journal_recorded'] is False
    assert result['delivery_journal_recorded'] is False
    assert port.state()['review']['result']['acknowledged']


def test_review_wait_uses_remaining_native_lease(plugin_instance, monkeypatch, run_async):
    port, _, _, _, clock, _, _, _, create = configured(plugin_instance, monkeypatch)
    transport = importlib.import_module(plugin_instance.__class__.__module__ + '.neko_structured_review')
    actual_wait = transport.asyncio.wait_for
    waits = []
    async def wait_for(coro, timeout):
        waits.append(timeout)
        return await actual_wait(coro, timeout)
    monkeypatch.setattr(transport.asyncio, 'wait_for', wait_for)
    async def answer(**kwargs):
        clock[0] += 30  # A valid, slow inference must fit the offered lease.
        return response({'decision': 'continue', 'reason': 'Continue after the queued inference'})
    create.side_effect = answer
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is True
    assert 25 < waits[-1] <= 180
    assert create.await_count == 1


def test_correction_shares_original_review_deadline(plugin_instance, monkeypatch, run_async):
    port, _, _, _, clock, _, _, _, create = configured(plugin_instance, monkeypatch)
    transport = importlib.import_module(plugin_instance.__class__.__module__ + '.neko_structured_review')
    actual_wait = transport.asyncio.wait_for
    waits = []
    async def wait_for(coro, timeout):
        waits.append(timeout)
        return await actual_wait(coro, timeout)
    monkeypatch.setattr(transport.asyncio, 'wait_for', wait_for)
    async def answer(**kwargs):
        clock[0] += 10
        return response({'decision': 'revise', 'reason': 'Change stage', 'goal': 'New stage'} if create.await_count == 1
                        else {'decision': 'continue', 'reason': 'Continue original goal'})
    create.side_effect = answer
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is True
    assert waits[-1] == waits[-2] - 10
    assert 25 < waits[-1] <= 170
    assert create.await_count == 2


@pytest.mark.parametrize('change', ['stop', 'generation', 'run', 'intent'])
def test_corrected_review_rechecks_ownership(plugin_instance, monkeypatch, run_async, change):
    port, snapshot, _, _, _, save, _, _, create = configured(plugin_instance, monkeypatch)
    async def answer(**kwargs):
        if create.await_count == 1:
            return response({'decision': 'revise', 'reason': 'No stage', 'goal': 'New stage'})
        if change == 'stop':
            plugin_instance._operator_state(True)
        elif change == 'generation':
            plugin_instance._generation += 1
        elif change == 'run':
            snapshot['native_run'] = 'other-run'
            save()
        else:
            port.mailbox.submit_user_intent('feedback', {'goal': 'New operator goal', 'mode': 'insert'},
                                           '11111111-1111-4111-8111-111111111111')
        return response({'decision': 'continue', 'reason': 'Stale correction'})
    create.side_effect = answer
    with session_lease(port.directory):
        result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result['main_brain_acknowledged'] is False
    assert result['reason'] == 'structured_review_superseded'
    assert create.await_count == 2
    assert not port.state()['review'].get('choice')
