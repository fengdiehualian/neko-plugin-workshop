"""Read-only trace correctness, not recognition/latency acceptance."""
from types import SimpleNamespace

from plugin.plugins.hsr_companion.adapters import local_vision
from plugin.plugins.hsr_companion.scripts.trace_reader_budget import ReaderBudgetProbe
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog


def test_baseline_failure_keeps_its_trace_and_original_failure():
    from plugin.plugins.hsr_companion.scripts.trace_character_handoff import collect_probe_report
    probe = SimpleNamespace(events=[{'kind': 'reader_path_exit', 'exception': 'ObservationSuperseded'}],
                            dropped=0, errors=0)
    result = {'failure': {'type': 'RuntimeError', 'error': 'Exploration baseline absent'}}
    collect_probe_report(result, [probe], [{'observer_disabled': True}])
    assert result['failure']['error'] == 'Exploration baseline absent'
    assert result['pipeline_trace'] == probe.events
    assert result['probe_errors'] == result['probe_dropped'] == 0


def test_equal_pixels_do_not_borrow_another_frames_timestamp_and_right_boxes_are_logged(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._vision = local_vision.LocalVisionAdapter(catalog(tmp_path))
    first, second = bytes(bytearray(b'identical pixels')), bytes(bytearray(b'identical pixels'))
    assert first is not second
    frames = iter([SimpleNamespace(image_bytes=first, frame_seq=1, timestamp=100., game={'pid': 1}),
                   SimpleNamespace(image_bytes=second, frame_seq=2, timestamp=200., game={'pid': 1})])
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: next(frames))
    page = {'page_type': 'character_detail'}

    def extract(payload):
        value = local_vision.extract_screen_facts([box('防御力', 800, 300), box('3335', 950, 300)],
            width=1000, height=800, page=page, catalog=owner._vision.catalog)
        return {'screen_facts': value, 'page': page, 'recognition': {}}

    owner._vision.analyze = owner._vision._analyze_scene = extract
    with ReaderBudgetProbe(owner) as probe:
        one, two = owner._assistant_client.fetch_frame(0), owner._assistant_client.fetch_frame(0)
        original = owner._vision.analyze(one.image_bytes)
        later = owner._vision._analyze_scene(two.image_bytes)
    rows = [e for e in probe.events if e['kind'] == 'ocr_to_parser']
    assert [e['reader']['frame_seq'] for e in rows] == [1, 2]
    assert [e['captured_at'] for e in rows] == [100., 200.]
    assert [e['reader']['path'] for e in rows] == ['analyze', '_analyze_scene']
    assert all(any(b['left'] >= 800 for b in e['boxes']) for e in rows)
    assert original == later  # Observation did not replace recognition values.
    assert probe.errors == probe.dropped == 0
