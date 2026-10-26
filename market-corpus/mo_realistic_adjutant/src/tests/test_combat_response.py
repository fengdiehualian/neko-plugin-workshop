from __future__ import annotations

import unittest

from mo_companion.combat_response import (
    combat_matchup_score,
    contact_enemies,
    damaged_buildings,
    is_economic_unit,
    is_deployable_combat_unit,
    partition_force,
    prioritized_counter_assignments,
    threatened_harvesters,
)
from mo_companion.contact_defense_doctrine import select_contact_defense_rule


def entity(object_id, relation, kind, x, y, **extra):
    return {
        "object_id": object_id,
        "relation": relation,
        "kind": kind,
        "cell": {"x": x, "y": y},
        "alive": True,
        "visible": True,
        "controllable": relation == "self",
        "health_ratio": 1.0,
        **extra,
    }


class CombatResponseTests(unittest.TestCase):
    def test_force_is_split_into_defence_reserve_and_assault(self):
        own = [entity(i, "self", "infantry", i, 2) for i in range(1, 21)]
        base = entity(100, "self", "building", 2, 2, building_class="resource")
        state = {"visible_entities": [base, *own], "harvesters": []}
        roles = partition_force(state, {"tactical_controls": {"reserve_fraction": 0.2}})
        ids = [
            {int(item["object_id"]) for item in roles[name]}
            for name in ("defenders", "reserve", "assault")
        ]
        self.assertTrue(ids[0])
        self.assertTrue(ids[1])
        self.assertTrue(ids[2])
        self.assertFalse(ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])

    def test_harvester_never_enters_any_combat_partition(self):
        miner = entity(90, "self", "vehicle", 3, 3, type_id="CMIN")
        infantry = [entity(i, "self", "infantry", i, 2, type_id="E1") for i in range(1, 10)]
        roles = partition_force({"visible_entities": [miner, *infantry], "harvesters": []})
        assigned = {
            int(item["object_id"])
            for name in ("defenders", "reserve", "assault")
            for item in roles[name]
        }
        self.assertTrue(is_economic_unit(miner))
        self.assertNotIn(90, assigned)

    def test_harvester_never_receives_counter_assignment(self):
        miner = entity(90, "self", "vehicle", 3, 3, type_id="CMIN")
        enemy = entity(91, "enemy", "infantry", 4, 3, type_id="E1")
        self.assertEqual(prioritized_counter_assignments([miner], [enemy]), [])

    def test_gi_and_guardian_gi_are_deployable_but_miner_is_not(self):
        self.assertTrue(is_deployable_combat_unit(entity(1, "self", "infantry", 1, 1, type_id="E1")))
        self.assertTrue(is_deployable_combat_unit(entity(2, "self", "infantry", 1, 1, type_id="GGI")))
        self.assertFalse(is_deployable_combat_unit(entity(3, "self", "vehicle", 1, 1, type_id="CMIN")))
        self.assertTrue(is_deployable_combat_unit(entity(4, "self", "infantry", 1, 1, type_id="OTHER", can_deploy=True)))

    def test_large_army_is_not_locked_into_oversized_base_guard(self):
        own = [entity(i, "self", "infantry", i, 2) for i in range(1, 121)]
        base = entity(500, "self", "building", 2, 2, building_class="resource")
        enemies = [entity(600 + i, "enemy", "infantry", 5 + i, 2) for i in range(3)]
        roles = partition_force({"visible_entities": [base, *own, *enemies], "harvesters": []})
        self.assertLessEqual(len(roles["defenders"]), 12)
        self.assertGreaterEqual(len(roles["assault"]), 80)

    def test_t3_keeps_real_defenders_and_reserve_at_home(self):
        own = [entity(i, "self", "infantry", i, 2, type_id="E1") for i in range(1, 31)]
        base = entity(500, "self", "building", 2, 2, type_id="GACNST")
        tech = entity(501, "self", "building", 3, 2, type_id="GATECH")
        roles = partition_force({"visible_entities": [base, tech, *own], "harvesters": []})
        self.assertTrue(roles["t3_established"])
        self.assertGreaterEqual(len(roles["defenders"]), 8)
        self.assertGreaterEqual(len(roles["reserve"]), 4)
        self.assertGreaterEqual(len(roles["assault"]), 12)

    def test_loading_or_assembly_units_can_be_excluded_from_every_role(self):
        own = [entity(i, "self", "vehicle", i, 2, type_id="FV") for i in range(1, 21)]
        roles = partition_force(
            {"visible_entities": own, "harvesters": []},
            excluded_ids={1, 2, 3, 4},
        )
        assigned = {
            int(item["object_id"])
            for name in ("defenders", "reserve", "assault")
            for item in roles[name]
        }
        self.assertFalse({1, 2, 3, 4} & assigned)

    def test_attack_wave_detects_blocking_enemy_before_distant_building(self):
        units = [entity(1, "self", "infantry", 10, 10)]
        blocker = entity(2, "enemy", "vehicle", 13, 10)
        distant = entity(3, "enemy", "building", 50, 50)
        self.assertEqual(contact_enemies(units, [blocker, distant], 8), [blocker])

    def test_damaged_building_and_threatened_harvester_are_actionable(self):
        building = entity(
            10, "self", "building", 5, 5, health_ratio=0.6,
            building_class="vehicle_production",
        )
        enemy = entity(20, "enemy", "vehicle", 9, 5)
        state = {
            "visible_entities": [building, enemy],
            "harvesters": [{
                "unit_id": 30, "alive": True, "controllable": True,
                "health_ratio": 0.9, "cell": {"x": 8, "y": 5},
            }],
        }
        self.assertEqual(damaged_buildings(state), [building])
        threatened = threatened_harvesters(state, [enemy])
        self.assertEqual(threatened[0]["unit_id"], 30)
        self.assertEqual(threatened[0]["nearby_threats"], [enemy])

    def test_realtime_rule_prefers_harvester_protection(self):
        state = {"harvesters": [{"unit_id": 30}]}
        roles = {
            "local_threats": [entity(20, "enemy", "vehicle", 9, 5)],
            "defenders": [entity(1, "self", "infantry", 5, 5)],
            "anchors": [],
            "threatened_harvesters": [{"unit_id": 30, "nearby_threats": [20]}],
        }
        rule = select_contact_defense_rule(state, roles)
        self.assertTrue(rule["id"].startswith("contact_harvester_"))
        self.assertEqual(rule["reserve_release"], 1.0)

    def test_damaged_harvester_without_current_enemy_keeps_mining(self):
        state = {
            "harvesters": [{
                "unit_id": 30, "alive": True, "controllable": True,
                "health_ratio": 0.4, "cell": {"x": 8, "y": 5},
            }],
        }
        self.assertEqual(threatened_harvesters(state, []), [])

    def test_enemy_outside_protection_radius_does_not_interrupt_mining(self):
        state = {
            "harvesters": [{
                "unit_id": 30, "alive": True, "controllable": True,
                "health_ratio": 1.0, "cell": {"x": 8, "y": 5},
            }],
        }
        distant = entity(20, "enemy", "vehicle", 17, 5)
        self.assertEqual(threatened_harvesters(state, [distant]), [])

    def test_frontline_contact_is_detected_away_from_base(self):
        base = entity(100, "self", "building", 2, 2, building_class="resource")
        seal = entity(1, "self", "vehicle", 60, 60, type_id="FV", effective_type_id="GHOST")
        prism = entity(2, "enemy", "infantry", 64, 60, type_id="SNIPE")
        roles = partition_force({"visible_entities": [base, seal, prism], "harvesters": []})
        self.assertEqual([item["object_id"] for item in roles["local_threats"]], [2])

    def test_seal_ifv_clears_infantry_while_ggi_engages_armor(self):
        seal_ifv = entity(1, "self", "vehicle", 10, 10, type_id="FV", effective_type_id="GHOST")
        ggi = entity(2, "self", "infantry", 10, 11, type_id="GGI")
        infantry = entity(20, "enemy", "infantry", 13, 10, type_id="E1")
        armor = entity(21, "enemy", "vehicle", 13, 11, type_id="MTNK")
        self.assertGreater(
            combat_matchup_score(seal_ifv, infantry),
            combat_matchup_score(seal_ifv, armor),
        )
        self.assertGreater(
            combat_matchup_score(ggi, armor),
            combat_matchup_score(ggi, infantry),
        )
        assignments = prioritized_counter_assignments(
            [seal_ifv, ggi], [infantry, armor], maximum_responders=2
        )
        self.assertEqual(
            {(unit["object_id"], target["object_id"]) for unit, target in assignments},
            {(1, 20), (2, 21)},
        )

    def test_only_ggi_still_returns_fire_against_infantry(self):
        ggi = entity(2, "self", "infantry", 10, 11, type_id="GGI")
        infantry = entity(20, "enemy", "infantry", 13, 10, type_id="E1")
        assignments = prioritized_counter_assignments([ggi], [infantry])
        self.assertEqual(assignments[0][1]["object_id"], 20)


if __name__ == "__main__":
    unittest.main()
