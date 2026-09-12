import unittest
from unittest import mock

from ed_companion.phase14.controller import CockpitController


def _controller():
    controller = CockpitController.__new__(CockpitController)
    controller._exobiology_step_positions = {}
    return controller


class RecordExobiologyStepPositionsTests(unittest.TestCase):
    """``_record_exobiology_step_positions`` is best-effort, in-memory only
    telemetry the Journal itself has no way to reconstruct: a ScanOrganic
    event never carries the Commander's position, so the app has to catch
    it live, the moment a scan step's sample count actually advances."""

    def _finding(self, samples_done, complete=False, **overrides):
        row = {
            "systemAddress": 1, "body": "3",
            "genus": "$Codex_Ent_Aleoids_Genus_Name;",
            "species": "$Codex_Ent_Aleoids_01_Name;",
            "samplesDone": samples_done, "complete": complete,
        }
        row.update(overrides)
        return row

    def test_a_new_step_records_the_current_status_position(self):
        controller = _controller()
        with mock.patch(
            "ed_companion.phase14.controller.read_json",
            return_value={
                "Latitude": 1.5, "Longitude": 2.5, "PlanetRadius": 1000.0,
                "BodyName": "Some Body",
            },
        ):
            controller._record_exobiology_step_positions(
                [self._finding(1)], [self._finding(2)],
            )

        key = (1, "3", "$Codex_Ent_Aleoids_Genus_Name;", "$Codex_Ent_Aleoids_01_Name;")
        self.assertEqual(controller._exobiology_step_positions[key], {
            "lat": 1.5, "lon": 2.5, "radius": 1000.0, "bodyName": "Some Body",
        })

    def test_no_position_data_in_status_records_nothing(self):
        controller = _controller()
        with mock.patch(
            "ed_companion.phase14.controller.read_json", return_value={},
        ):
            controller._record_exobiology_step_positions(
                [self._finding(1)], [self._finding(2)],
            )
        self.assertEqual(controller._exobiology_step_positions, {})

    def test_an_unchanged_step_count_does_not_overwrite_the_baseline(self):
        controller = _controller()
        key = (1, "3", "$Codex_Ent_Aleoids_Genus_Name;", "$Codex_Ent_Aleoids_01_Name;")
        controller._exobiology_step_positions[key] = {
            "lat": 9, "lon": 9, "radius": 9, "bodyName": "Old",
        }
        with mock.patch(
            "ed_companion.phase14.controller.read_json",
            return_value={
                "Latitude": 1, "Longitude": 1, "PlanetRadius": 1, "BodyName": "New",
            },
        ):
            controller._record_exobiology_step_positions(
                [self._finding(2)], [self._finding(2)],
            )
        self.assertEqual(controller._exobiology_step_positions[key]["bodyName"], "Old")

    def test_a_completed_find_does_not_get_a_baseline(self):
        controller = _controller()
        with mock.patch(
            "ed_companion.phase14.controller.read_json",
            return_value={
                "Latitude": 1, "Longitude": 1, "PlanetRadius": 1, "BodyName": "X",
            },
        ):
            controller._record_exobiology_step_positions(
                [self._finding(2)], [self._finding(3, complete=True)],
            )
        self.assertEqual(controller._exobiology_step_positions, {})


class NewCurrentSystemExobiologyTargetTests(unittest.TestCase):
    """The Journal-driven toast that fires the moment a fresh, unclaimed
    biological signal appears in the Commander's current system."""

    def _target(self, **overrides):
        row = {
            "systemAddress": 1, "bodyId": 3, "bodyName": "Body C",
            "inCurrentSystem": True, "signalCount": 2,
        }
        row.update(overrides)
        return row

    def test_a_brand_new_current_system_target_is_announced(self):
        controller = CockpitController.__new__(CockpitController)
        message = controller._new_current_system_exobiology_target(
            {"exobiologyLandingTargets": []},
            {"exobiologyLandingTargets": [self._target()]},
        )
        self.assertIn("Body C", message)
        self.assertIn("2", message)

    def test_a_target_already_present_last_time_is_not_re_announced(self):
        controller = CockpitController.__new__(CockpitController)
        target = self._target()
        message = controller._new_current_system_exobiology_target(
            {"exobiologyLandingTargets": [target]},
            {"exobiologyLandingTargets": [target]},
        )
        self.assertEqual(message, "")

    def test_a_career_wide_target_outside_the_current_system_is_not_announced(self):
        controller = CockpitController.__new__(CockpitController)
        message = controller._new_current_system_exobiology_target(
            {"exobiologyLandingTargets": []},
            {"exobiologyLandingTargets": [self._target(inCurrentSystem=False)]},
        )
        self.assertEqual(message, "")


if __name__ == "__main__":
    unittest.main()
