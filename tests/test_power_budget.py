import unittest

from ed_companion.phase14.state import (
    power_modifier_multiplier,
    ship_power_budget,
    slot_power_mw,
)


def _slot(
    slot, module_id, priority_group=1, powered_on=True,
    engineering_grade=0, engineering_blueprint="", experimental_effect="",
    empty=False,
):
    return {
        "slot": slot,
        "moduleId": module_id,
        "empty": empty,
        "engineeringGrade": engineering_grade,
        "engineeringBlueprint": engineering_blueprint,
        "experimentalEffect": experimental_effect,
        "priorityGroup": priority_group,
        "poweredOn": powered_on,
        "module": "Test Module",
    }


class PowerModifierMultiplierTests(unittest.TestCase):
    def test_unengineered_module_has_no_modifier(self):
        self.assertEqual(
            power_modifier_multiplier("int_powerplant_size6_class5", "", 0), 1.0,
        )

    def test_known_grade_blueprint_applies_percent(self):
        # Power Plant "Armoured" Grade 3 -> Power Generation +8%
        self.assertAlmostEqual(
            power_modifier_multiplier(
                "int_powerplant_size6_class5", "Armoured", 3,
            ),
            1.08,
        )

    def test_known_experimental_effect_applies_percent(self):
        # Multi-cannon "Flow Control" experimental -> Power Draw -10%
        self.assertAlmostEqual(
            power_modifier_multiplier(
                "hpt_multicannon_gimbal_medium", "", 0,
                experimental_effect="Flow Control",
            ),
            0.90,
        )

    def test_grade_and_experimental_stack_multiplicatively(self):
        # Efficient Weapon Grade 3 (-24%) combined with Flow Control (-10%)
        # must multiply, not add: 0.76 * 0.90, not 1 - 0.24 - 0.10.
        self.assertAlmostEqual(
            power_modifier_multiplier(
                "hpt_multicannon_gimbal_medium", "Efficient Weapon", 3,
                experimental_effect="Flow Control",
            ),
            0.76 * 0.90,
        )

    def test_unrecognized_blueprint_name_never_guesses_a_modifier(self):
        self.assertEqual(
            power_modifier_multiplier(
                "int_powerplant_size6_class5", "Not A Real Blueprint", 3,
            ),
            1.0,
        )


class SlotPowerMwTests(unittest.TestCase):
    def test_known_module_returns_base_draw(self):
        draw, generated = slot_power_mw(_slot("Slot01", "hpt_pulselaser_fixed_small"))
        self.assertAlmostEqual(draw, 0.39)
        self.assertIsNone(generated)

    def test_known_power_plant_returns_generation(self):
        draw, generated = slot_power_mw(_slot("PowerPlant", "int_powerplant_size2_class3"))
        self.assertIsNone(draw)
        self.assertAlmostEqual(generated, 8.0)

    def test_unknown_module_returns_none_none(self):
        self.assertEqual(
            slot_power_mw(_slot("Slot01", "int_totally_unknown_module")),
            (None, None),
        )

    def test_empty_slot_returns_none_none(self):
        self.assertEqual(
            slot_power_mw(_slot("Slot01", "", empty=True)),
            (None, None),
        )

    def test_engineered_module_scales_base_draw(self):
        draw, _generated = slot_power_mw(_slot(
            "Slot01", "hpt_multicannon_gimbal_medium",
            engineering_grade=3, engineering_blueprint="Efficient Weapon",
        ))
        self.assertAlmostEqual(draw, 0.64 * 0.76)


class ShipPowerBudgetTests(unittest.TestCase):
    def _base_slots(self):
        return [
            _slot("PowerPlant", "int_powerplant_size2_class3"),
            _slot("FrameShiftDrive", "int_hyperdrive_size2_class3", priority_group=5),
            _slot("Slot01_Size2", "int_shieldgenerator_size2_class3", priority_group=2),
            _slot("MediumHardpoint1", "hpt_multicannon_gimbal_medium", priority_group=1),
            _slot("MediumHardpoint2", "hpt_multicannon_gimbal_medium", priority_group=1),
            _slot("MediumHardpoint3", "hpt_multicannon_gimbal_medium", priority_group=1),
            _slot("Slot02_Size7", "int_shieldgenerator_size7_class5", priority_group=4),
        ]

    def test_under_capacity_nothing_is_shed(self):
        slots = [
            _slot("PowerPlant", "int_powerplant_size2_class3"),
            _slot("FrameShiftDrive", "int_hyperdrive_size2_class3", priority_group=5),
            _slot("Slot01_Size2", "int_shieldgenerator_size2_class3", priority_group=2),
        ]
        budget = ship_power_budget(slots)
        self.assertEqual(budget["capacityMW"], 8.0)
        self.assertTrue(budget["capacityKnown"])
        self.assertAlmostEqual(budget["totalDrawMW"], 0.2 + 1.5)
        self.assertFalse(budget["overloaded"])
        self.assertEqual(budget["groups"][4]["shedByCascade"], False)  # priority 5 entry
        for consumer in budget["consumers"]:
            self.assertFalse(consumer["shutDown"])

    def test_overload_sheds_lowest_priority_groups_first(self):
        budget = ship_power_budget(self._base_slots())
        expected_total = 0.2 + 1.5 + 3 * 0.64 + 4.9
        self.assertAlmostEqual(budget["totalDrawMW"], expected_total)
        self.assertTrue(budget["overloaded"])
        shed = {g["priorityGroup"] for g in budget["groups"] if g["shedByCascade"]}
        self.assertEqual(shed, {4, 5})
        self.assertLessEqual(budget["usedDrawMW"], budget["capacityMW"])
        shut_down_slots = {
            row["slot"] for row in budget["consumers"] if row["shutDown"]
        }
        self.assertEqual(shut_down_slots, {"FrameShiftDrive", "Slot02_Size7"})
        # Priority 1 and 2 must survive the cascade.
        surviving_slots = {
            row["slot"] for row in budget["consumers"] if not row["shutDown"]
        }
        self.assertEqual(
            surviving_slots,
            {"Slot01_Size2", "MediumHardpoint1", "MediumHardpoint2", "MediumHardpoint3"},
        )

    def test_manually_powered_off_module_is_excluded_from_draw(self):
        slots = [
            _slot("PowerPlant", "int_powerplant_size2_class3"),
            _slot(
                "Slot01_Size2", "int_shieldgenerator_size7_class5",
                priority_group=1, powered_on=False,
            ),
        ]
        budget = ship_power_budget(slots)
        self.assertAlmostEqual(budget["totalDrawMW"], 0.0)
        self.assertFalse(budget["overloaded"])
        self.assertEqual(budget["consumers"][0]["poweredOn"], False)
        self.assertFalse(budget["consumers"][0]["shutDown"])

    def test_unknown_module_is_reported_not_assumed_zero(self):
        slots = [
            _slot("PowerPlant", "int_powerplant_size2_class3"),
            _slot("Slot01_Size2", "int_totally_unknown_module"),
        ]
        budget = ship_power_budget(slots)
        self.assertEqual(budget["unknownModuleSlots"], ["Slot01_Size2"])
        self.assertAlmostEqual(budget["totalDrawMW"], 0.0)

    def test_missing_power_plant_leaves_capacity_unknown(self):
        slots = [
            _slot("PowerPlant", "", empty=True),
            _slot("Slot01_Size2", "int_shieldgenerator_size7_class5"),
        ]
        budget = ship_power_budget(slots)
        self.assertFalse(budget["capacityKnown"])
        self.assertEqual(budget["capacityMW"], 0.0)
        self.assertFalse(budget["overloaded"])
        for consumer in budget["consumers"]:
            self.assertFalse(consumer["shutDown"])


if __name__ == "__main__":
    unittest.main()
