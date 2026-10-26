"""Separate schema defect; NOT a reconstruction of the 12:04 live call."""
import pytest
from jsonschema import Draft202012Validator
from plugin.plugins.hsr_companion.runtime.consultation_contract import CONSULTATION_SCHEMA, validate_declaration


@pytest.mark.parametrize("part", ["topics", "answer_parts"])
def test_explicit_empty_arrays_are_rejected_by_schema_and_runtime(part):
    value = {"scope": "current_relic", "subject_state": "specified", "character": "真珠", part: []}
    assert list(Draft202012Validator(CONSULTATION_SCHEMA).iter_errors(value))
    with pytest.raises(ValueError, match="invalid_consultation_" + part):
        validate_declaration(value, intent="assess_relic")


@pytest.mark.parametrize("extra", [{}, {"topics": ["relic"]}, {"answer_parts": ["assessment"]},
    {"topics": ["relic", "cone"], "answer_parts": ["rows", "assessment", "next_step", "options"]}])
def test_optional_omission_and_nonempty_lists_stay_compatible(extra):
    value = {"scope": "current_relic", "subject_state": "specified", "character": "真珠", **extra}
    assert not list(Draft202012Validator(CONSULTATION_SCHEMA).iter_errors(value))
    assert validate_declaration(value, intent="assess_relic") == value
