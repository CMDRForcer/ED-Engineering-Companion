import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.phase14.controller import CockpitController
from ed_companion.phase14.state import ProfileContext


class _Signal:
    def emit(self, *_args):
        pass


class StartupStateGenerationTests(unittest.TestCase):
    @staticmethod
    def _context(root, identity):
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        directory = Path(root) / f"profile-{key}"
        directory.mkdir(parents=True, exist_ok=True)
        return ProfileContext(identity, key, directory, str(Path(root) / "journal"))

    def _controller(self, context):
        controller = CockpitController.__new__(CockpitController)
        controller.profile_context = context
        controller._refresh_revision = 0
        controller._profile_generation = 1
        controller._state = {"marker": "previous"}
        controller._hge_sightings = []
        controller._logbook_entries = []
        controller._logbook_revision = 0
        controller._hge_candidate_cache_key = None
        controller._hge_candidate_cache_rows = []
        controller._hge_material_filter_cache = None
        controller._selected_ship = ""
        controller._activity = "Starting"
        controller._log_consistency_issues = mock.Mock()
        controller._publish_full_state = mock.Mock()
        controller._switch_profile_context = mock.Mock(return_value=True)
        controller.activityChanged = _Signal()
        controller.connectionChanged = _Signal()
        return controller

    def test_current_startup_state_is_applied_normally(self):
        with TemporaryDirectory() as directory:
            context = self._context(directory, "F-ALPHA")
            controller = self._controller(context)
            state = {
                "marker": "startup", "ship": "Test Ship",
                "_logbookEntries": [{"event": "LoadGame"}],
            }

            controller._finish_startup_state((0, 1, state, [{"candidate": 1}]))

            self.assertEqual(controller._state["marker"], "startup")
            self.assertEqual(controller._selected_ship, "Test Ship")
            self.assertEqual(controller._logbook_revision, 1)
            controller._publish_full_state.assert_called_once_with()

    def test_late_startup_state_cannot_overwrite_newer_refresh_or_profile(self):
        with TemporaryDirectory() as directory:
            alpha = self._context(directory, "F-ALPHA")
            bravo = self._context(directory, "F-BRAVO")
            controller = self._controller(bravo)
            controller._refresh_revision = 2
            controller._profile_generation = 2
            controller._state = {"marker": "newer-refresh", "ship": "Bravo Ship"}
            stale_state = {
                "marker": "stale-startup", "ship": "Alpha Ship",
                "_profileContext": alpha,
            }

            controller._finish_startup_state((1, 1, stale_state, []))
            controller._fail_startup_state((1, 1, "late startup failure"))

            self.assertEqual(controller.profile_context, bravo)
            self.assertEqual(controller._state["marker"], "newer-refresh")
            self.assertEqual(controller._state["ship"], "Bravo Ship")
            self.assertEqual(controller._activity, "Starting")
            controller._switch_profile_context.assert_not_called()
            controller._publish_full_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
