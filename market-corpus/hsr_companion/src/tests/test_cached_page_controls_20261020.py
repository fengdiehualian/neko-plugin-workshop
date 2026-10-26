"""Live failure's cache branch: weak redundant tab is not a changed number.

Real pixel guards, synthetic literal receipts; no OCR accuracy claim.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime.live_refresh import frame_stamp
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp, review_snapshot
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame
from plugin.plugins.hsr_companion.tests.test_equipment_turn_delivery import control, image


@pytest.mark.parametrize('path,proved,expected', [
    ('review', {'角色详情', '3335'}, True),
    ('review', {'光锥', '星魂', '3335'}, True),
    ('review', {'光锥', '3335'}, False),
    ('review', {'角色详情'}, False),  # Page proof cannot bless a disproved number.
    ('formal', {'角色详情', '3335'}, True),
    ('formal', {'光锥', '星魂', '3335'}, True),
])
def test_cache_uses_same_proved_page_classifier_as_producer(tmp_path, path, proved, expected):
    async def run():
        owner, _, _ = audit_owner(tmp_path)
        owner._observation_serial = 1
        controls = [control('角色详情', .03), control('光锥', .2), control('行迹', .3), control('星魂', .4)]
        literal = control('3335', .5, x=.75)
        literal.pop('control')
        fact = {'kind': 'stat', 'scope': 'attributes', 'label': '防御力', 'value': '3335', 'evidence': [literal]}
        evidence = {'page_type': 'character_detail', 'fresh': True, 'read_scope': 'full',
            'scene': {'verified': True, 'label': '角色详情', 'primary_state': 'menu'},
            'instruction': 'Only confirmed fields', 'observed_at': datetime.now(timezone.utc).isoformat(),
            'screen_facts': {'observation_serial': 1, 'facts': [fact], 'visible_text': []}, 'characters': []}
        original, latest = image('black'), image('white')
        regions = [*controls, literal]
        snapshot = {'evidence': evidence, 'stamp': frame_stamp(original), 'game_pid': 1, 'epoch': 0,
                    'guard': (regions, region_stamp(original, regions)), 'page_controls': controls}
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(latest))
        owner._vision.verify_text_regions = lambda data, proofs, **kw: {proof_key(p) for p in proofs if p['text'] in proved}
        if path == 'formal':
            owner._reusable_observations = {'full': snapshot}
            packet = await owner.hsr_companion_prepare(query='当前面板属性是什么？', intent='read_screen',
                                                       screen_fields=['attributes'])
            assert packet['query_answer']['state'] == 'ready', packet.get('query_timing')
            result = packet.get('current_visual_evidence')
        else:
            result = await review_snapshot(owner, snapshot, plan=build_query_plan('', 'assess_panel'))
        values = [f['value'] for f in (result or {}).get('screen_facts', {}).get('facts', [])]
        assert ('3335' in values) is expected
        assert snapshot['page_controls'] == controls  # Delivery-local proof subset only.
        assert snapshot['evidence']['screen_facts']['facts'] == [fact]
    asyncio.run(run())
