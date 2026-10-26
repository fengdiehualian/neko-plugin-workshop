"""The retired implementation-dump option cannot regain a model route."""

import copy
import json

import jsonschema
import pytest
from plugin.plugins.neko_rimworld.agent.context.context_pruning import ContextPruner
from plugin.plugins.neko_rimworld.agent.context.context_tools import model_tools
from plugin.plugins.neko_rimworld.agent.world.look_tools import read_look
from plugin.plugins.neko_rimworld.agent.world.semantic_surface import SurfaceError
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import SemanticInteraction
from plugin.plugins.neko_rimworld.tests.test_activity import NativeInteraction
from plugin.plugins.neko_rimworld.tests.test_context_pruning import route
from plugin.plugins.neko_rimworld.tests.test_native_on_demand import route_recorder


@pytest.mark.parametrize('name,allowed', [
    ('look', ['body_parts']),
    ('survey_space', ['environment', 'objects', 'geometry']),
])
def test_published_include_rejects_diagnostics_and_preserves_real_selection(name, allowed):
    from plugin.plugins.neko_rimworld.agent.world.look_tools import look_spec
    from plugin.plugins.neko_rimworld.agent.world.survey_tools import survey_spec

    from .consultation_setup import consultation_setup
    directory = consultation_setup().directory
    spec = look_spec(directory) if name == 'look' else survey_spec(next(t for t in directory['world_observation']['tools'] if t['name'] == name))
    published = model_tools([{'type': 'function', 'function': spec}])[0]['function']
    validator = jsonschema.Draft202012Validator(published['parameters'])
    base = {'object': {'kind': 'pawn', 'id': 'a'}, 'aspects': ['health']} if name == 'look' else {'region': {'near': {'kind': 'cell', 'x': 0, 'z': 0}, 'radius': 2}}
    assert not validator.is_valid({**base, 'include': ['diagnostics']})
    for option in allowed:
        assert validator.is_valid({**base, 'include': [option]})
    assert 'diagnostics' not in json.dumps(published)


@pytest.mark.asyncio
async def test_native_look_rejects_dump_before_any_read():
    native = NativeInteraction()
    with pytest.raises(SurfaceError, match='include accepts body_parts'):
        await read_look(native, {'object': {'kind': 'pawn', 'id': 'a'},
                                        'aspects': ['health'], 'include': ['diagnostics']})
    assert native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['inspect_route', 'survey_space'])
@pytest.mark.parametrize('version', ['world_observation.v2.3'])
async def test_spatial_dump_rejected_before_supported_native_dispatch(operation, version):
    native, calls = route_recorder(version)
    with pytest.raises(SurfaceError, match='spatial include accepts'):
        await SemanticInteraction._world_read(native, {'include': ['diagnostics']}, operation)
    assert calls == []


def test_forged_historical_option_cannot_restore_projection_bypass_or_mutate_audit():
    raw = route()
    raw['data'].update(read_ms=17, cooperative_budget_overrun=False,
                       unsupported_components=['Verse.CompA', 'Verse.CompB'])
    raw['data']['objects'][0]['target'].update(position={'x': 1, 'z': 2},
                                             runtime_type='Verse.Thing', holder_chain=['tracker'])
    saved = copy.deepcopy(raw)
    options = {'include': ['environment', 'objects']}
    normal = ContextPruner().compile(raw, arguments=options)
    forged = ContextPruner().compile(raw, arguments={'include': [*options['include'], 'diagnostics']})
    assert forged.content == normal.content
    assert forged.source_sha256 == normal.source_sha256 and raw == saved
    visible = json.loads(forged.content)['data']
    assert 'read_ms' not in visible and 'cooperative_budget_overrun' not in visible
    assert visible['unsupported_components_count'] == 2
    assert 'runtime_type' not in visible['objects'][0]['target']
    assert 'holder_chain' not in visible['objects'][0]['target']
    assert visible['path_environment']['gases'] == saved['data']['path_environment']['gases']
    assert visible['sample'] == saved['data']['sample']
    assert visible['context'] == saved['data']['context']
    assert visible['extension'] == saved['data']['extension']
