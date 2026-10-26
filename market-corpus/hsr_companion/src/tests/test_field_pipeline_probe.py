"""Diagnostic wrappers must not change acceptance or formal output."""
import asyncio
from types import SimpleNamespace

from plugin.plugins.hsr_companion.scripts.field_pipeline_probe import FieldPipelineProbe
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, raw_page


def test_probe_preserves_formal_equipment_payload_and_records_commit(tmp_path):
    async def run():
        owner, _, _ = audit_owner(tmp_path)
        owner._assistant_client = None
        with FieldPipelineProbe(owner) as probe:
            await commit_raw(owner, raw_page())
            with_probe = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么？")
        owner2, _, _ = audit_owner(tmp_path)
        owner2._assistant_client = None
        await commit_raw(owner2, raw_page())
        without_probe = await owner2.hsr_companion_prepare(query="这件遗器的主副词条是什么？")
        # Receipts/timing are expected to differ; the actual facts/card must not.
        assert with_probe["equipment_answer"]["main_stat"] == without_probe["equipment_answer"]["main_stat"]
        assert with_probe["equipment_answer"]["sub_stats"] == without_probe["equipment_answer"]["sub_stats"]
        assert with_probe["query_answer"]["state"] == without_probe["query_answer"]["state"]
        assert any(e["kind"] == "store_commit" for e in probe.events)
        assert probe.errors == probe.dropped == 0
    asyncio.run(run())


def test_probe_preserves_parser_result_and_raw_boxes(tmp_path):
    from plugin.plugins.hsr_companion.adapters import local_vision
    from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes
    from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog
    owner, _, _ = audit_owner(tmp_path)
    kwargs = dict(width=1280, height=800, page={"page_type": "relic_detail"}, catalog=catalog())
    expected = local_vision.extract_screen_facts(relic_boxes(), **kwargs)
    with FieldPipelineProbe(owner) as probe:
        assert local_vision.extract_screen_facts(relic_boxes(), **kwargs) == expected
    assert probe.events[-1]["kind"] == "ocr_to_parser"
    assert probe.events[-1]["boxes"]


def test_probe_bounds_log_and_does_not_keep_mutable_caller_values():
    probe = FieldPipelineProbe(SimpleNamespace(), limit=1)
    values = {"value": "4"}
    probe.emit("one", values=values)
    values["value"] = "99"
    probe.emit("two")
    assert probe.events[0]["values"] == {"value": "4"} and probe.dropped == 1


def test_probe_preserves_numeric_backend_result_and_call_count(tmp_path):
    from PIL import Image
    from plugin.plugins.hsr_companion.adapters import relic_values
    from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes
    owner, _, _ = audit_owner(tmp_path)
    boxes = relic_boxes()
    boxes[8]["text"] = "8.2%0"

    class Backend:
        calls = 0

        def extract_text_with_boxes(self, sample):
            self.calls += 1
            return "8.2%", [{"text": "8.2%", "score": .99,
                "left": 20, "top": 20, "right": 90, "bottom": 50}]

    backend = Backend()
    kwargs = dict(page={"page_type": "relic_detail"}, backend=backend)
    image = Image.new("RGB", (1280, 800))
    expected = relic_values.refine_relic_values(image, boxes, **kwargs)
    with FieldPipelineProbe(owner) as probe:
        actual = relic_values.refine_relic_values(image, boxes, **kwargs)
    # Elapsed time is instrumentation, not recognition semantics.
    assert actual[0] == expected[0]
    assert actual[1]["confirmed"] == expected[1]["confirmed"]
    assert backend.calls == 2  # Exactly one call with and one without tracing.
    assert probe.events[-1]["backend_outputs"][0]["boxes"][0]["text"] == "8.2%"
