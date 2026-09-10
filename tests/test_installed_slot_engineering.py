"""Regression cover for _apply_installed_slot_engineering.

The projection of an installed Loadout roll onto the selected plan was dead
code from 1.0.0 to 1.1.1 (its body sat unreachable after an early return in
another method). These tests pin the behaviour so it cannot silently break
again.
"""

import unittest

from ed_companion.phase14.controller import CockpitController


def _controller(slot_options, selected_slot, blueprint_name, target_grade=5):
    controller = CockpitController.__new__(CockpitController)
    controller._module_slot_options = slot_options
    controller._selected_module_slot = selected_slot
    controller._selected_blueprint = {"name": blueprint_name}
    controller._target_grade = target_grade
    controller._current_grade = target_grade
    return controller


class InstalledSlotEngineeringTests(unittest.TestCase):
    def test_matching_installed_roll_is_projected_onto_the_plan(self):
        controller = _controller(
            [{
                "slot": "MainEngines",
                "engineeringBlueprint": "Custom_Tuning",
                "engineeringGrade": 3,
                "engineeringQuality": 0.5,
                "engineeringQualityKnown": True,
                "experimentalEffect": "special_engine_overloaded",
            }],
            "MainEngines", "Custom Tuning", target_grade=5,
        )

        controller._apply_installed_slot_engineering()
        plan = controller._selected_blueprint

        self.assertTrue(plan["installedEngineeringKnown"])
        self.assertTrue(plan["installedMatchesSelection"])
        self.assertEqual(plan["installedBlueprint"], "Custom Tuning")
        self.assertEqual(plan["installedGrade"], 3)
        self.assertEqual(plan["installedQualityPercent"], 50)
        self.assertEqual(
            plan["installedExperimentalEffect"], "special_engine_overloaded"
        )
        # Quality known -> clamp current grade to the installed grade.
        self.assertEqual(controller._current_grade, 3)

    def test_unengineered_slot_reports_nothing_installed(self):
        controller = _controller(
            [{"slot": "MainEngines", "engineeringBlueprint": "",
              "engineeringGrade": 0}],
            "MainEngines", "Dirty Drive Tuning",
        )

        controller._apply_installed_slot_engineering()

        self.assertFalse(
            controller._selected_blueprint["installedEngineeringKnown"]
        )
        self.assertFalse(
            controller._selected_blueprint["installedMatchesSelection"]
        )
        self.assertEqual(controller._current_grade, 0)

    def test_different_installed_blueprint_does_not_match(self):
        controller = _controller(
            [{"slot": "MainEngines", "engineeringBlueprint": "Engine_Clean",
              "engineeringGrade": 5, "engineeringQualityKnown": False}],
            "MainEngines", "Dirty Drive Tuning",
        )

        controller._apply_installed_slot_engineering()

        self.assertTrue(
            controller._selected_blueprint["installedEngineeringKnown"]
        )
        self.assertFalse(
            controller._selected_blueprint["installedMatchesSelection"]
        )
        self.assertEqual(controller._current_grade, 0)

    def test_missing_slot_selection_is_safe(self):
        controller = _controller([], "", "Anything")
        controller._apply_installed_slot_engineering()
        self.assertFalse(
            controller._selected_blueprint["installedMatchesSelection"]
        )


if __name__ == "__main__":
    unittest.main()
