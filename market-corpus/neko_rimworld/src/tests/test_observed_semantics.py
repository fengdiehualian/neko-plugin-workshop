import copy

from plugin.plugins.neko_rimworld.agent.world.query_readings import profile_trait_semantics


def test_trait_codes_stay_with_values_without_mutating_evidence():
    data = {
        "facts": [
            {
                "object": {"id": "p"},
                "facts": {
                    "profile": {
                        "traits": {"rows": [{"degree": 2, "description": "Move speed +0.4 c/s", "suppressed": False}]}
                    },
                },
            }
        ]
    }
    before = copy.deepcopy(data)
    result = profile_trait_semantics(data)
    traits = result["facts"][0]["facts"]["profile"]["traits"]
    assert traits["rows"][0]["degree"] == 2 and "variant code" in traits["field_semantics"]["degree"]
    assert data == before
    unknown = {"facts": [{"facts": {"health": {"summary": {"state": "unavailable", "reason": "not captured"}}}}]}
    assert profile_trait_semantics(unknown) == unknown
