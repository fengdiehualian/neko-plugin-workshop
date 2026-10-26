
import copy
import json

import pytest
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditLedger
from plugin.plugins.neko_rimworld.agent.runtime.session import AgentRunSession

from .consultation_setup import semantic_tool_provider


def failed(identifier, patch):
    return [
        {'role':'assistant','content':'','tool_calls':[{'id':identifier,'type':'function',
            'function':{'name':'submit_response','arguments':json.dumps(patch)}}]},
        {'role':'tool','tool_call_id':identifier,'content':json.dumps({'tool':'submit_response','error':{'code':'invalid_submission'}})},
    ]


def test_only_current_superseded_drafts_are_removed_and_saved_detail_survives():
    old = failed('historical', {'answer':'Historical proposal.'})
    session = AgentRunSession.create(ledger=AgentAuditLedger('q'), question='q',context_messages=tuple(old))
    source = {'role':'tool','tool_call_id':'observation','content':'Original evidence with load epoch and actors.'}
    session.messages += [source, *failed('first', {'answer':'Old draft','detail':'SAVED DETAIL'}),
                         *failed('latest', {'answer':'Latest draft'})]
    session.submission.answer='Latest draft'
    session.submission.detail='SAVED DETAIL'
    session.submission.errors={'answer':{'code':'invalid_submission'}}
    saved=copy.deepcopy(session.messages)
    view=session.request_messages('repair')
    assert old[0] in view and old[1] in view and source in view
    assert not any(m.get('tool_call_id')=='first' for m in view)
    assert not any(c.get('id')=='first' for m in view for c in m.get('tool_calls',[]))
    latest=next(m for m in view if m.get('tool_call_id')=='latest')
    assert json.loads(latest['content'])['saved_draft']['detail']=='SAVED DETAIL'
    assert session.messages==saved and session.submission.detail=='SAVED DETAIL'


@pytest.mark.asyncio
async def test_runtime_repair_uses_same_calls_and_keeps_saved_report():
    from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditRegistry
    from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
    from plugin.plugins.neko_rimworld.message_contracts import ToolCall
    from plugin.plugins.neko_rimworld.tests.test_agent import FakeLogger, settings, turn

    # Structural failures retain valid detail and use the normal repair history,
    # while NLP diagnostics are no longer a reason to force three model calls.
    detail='本次未取得派系字段，保留未知。'
    class Model:
        calls=0
        async def complete(self,messages,tools,*,timeout):
            self.calls+=1
            if self.calls==1:
                return turn('',ToolCall('first','submit_response',json.dumps({'answer': [], 'detail':detail})))
            if self.calls==2:
                return turn('',ToolCall('latest','submit_response',json.dumps({'answer': {}})))
            assert self.calls==3
            assert not any(c.get('id')=='first' for m in messages for c in m.get('tool_calls',[]))
            carrier=next(m for m in messages if m.get('tool_call_id')=='latest')
            assert json.loads(carrier['content'])['saved_draft']['detail']==detail
            return turn('',ToolCall('final','submit_response',json.dumps({'answer':'本次没有取得派系字段，仅能给条件建议。'})))
    model=Model()
    runtime=AgentRuntime(tools_provider=semantic_tool_provider(),audit_registry=AgentAuditRegistry(),logger=FakeLogger(),model_factory=lambda _:model)
    result=await runtime.run('q',settings())
    assert result['status']=='ok' and model.calls==3 and detail in result['answer']
