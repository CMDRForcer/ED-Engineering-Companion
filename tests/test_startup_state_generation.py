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
        controller._state_revision = 0
        controller._hge_revision = 0
        controller._eddn_revision = 0
        controller._derived_cache = {}
        controller._eddn_context = {}
        controller._journal_auto = False
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

            state_find_rows = [{"system": "Test System"}]
            controller._finish_startup_state((
                0, 1, state, [{"candidate": 1}], context.key,
                {"StarSystem": "Test System"}, state_find_rows,
            ))

            self.assertEqual(controller._state["marker"], "startup")
            self.assertEqual(controller._selected_ship, "Test Ship")
            self.assertEqual(controller._logbook_revision, 1)
            self.assertEqual(
                controller._eddn_context["StarSystem"], "Test System"
            )
            self.assertEqual(
                controller._derived_cache["state_find_rows"][1],
                state_find_rows,
            )
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


class _RecordingSignal:
    def __init__(self):
        self.calls = 0

    def emit(self, *_args):
        self.calls += 1


class PublishFullStateTests(unittest.TestCase):
    """A plain Journal poll (e.g. a jump) very often leaves materials and
    the wishlist untouched. Re-emitting their change signal anyway makes
    QML tear down and rebuild every bound list's delegates - replaying the
    Materials page's fill-in bar animation for no reason, several times
    per jump. `_publish_full_state` must skip a domain's signal when its
    exposed keys are unchanged from `previous`, and still emit normally
    when nothing to compare against is given."""

    @staticmethod
    def _controller(state):
        controller = CockpitController.__new__(CockpitController)
        controller._state = state
        controller._state_revision = 0
        controller._derived_cache = {}
        for name in (
            "stateChanged", "materialsChanged", "fleetChanged",
            "wishlistChanged", "exobiologyChanged", "operationsChanged",
            "hgeChanged", "journalHealthChanged", "logbookChanged",
        ):
            setattr(controller, name, _RecordingSignal())
        return controller

    def test_unchanged_materials_and_wishlist_do_not_re_emit(self):
        state = {
            "materials": [{"key": "iron", "have": 50}],
            "blueprints": [{"planId": "p1"}],
            "exobiologyFindings": [{"displayName": "Aleoida Arcus"}],
            "system": "Colonia",
        }
        controller = self._controller(dict(state))

        controller._publish_full_state(dict(state))

        self.assertEqual(controller.stateChanged.calls, 1)
        self.assertEqual(controller.materialsChanged.calls, 0)
        self.assertEqual(controller.wishlistChanged.calls, 0)
        self.assertEqual(controller.exobiologyChanged.calls, 0)
        # Domains without a cheap identity check still always notify.
        self.assertEqual(controller.fleetChanged.calls, 1)
        self.assertEqual(controller.operationsChanged.calls, 1)

    def test_changed_exobiology_findings_still_notifies(self):
        previous = {"materials": [], "blueprints": [], "exobiologyFindings": []}
        controller = self._controller({
            "materials": [], "blueprints": [],
            "exobiologyFindings": [{"displayName": "Aleoida Arcus"}],
        })

        controller._publish_full_state(previous)

        self.assertEqual(controller.exobiologyChanged.calls, 1)
        self.assertEqual(controller.materialsChanged.calls, 0)
        self.assertEqual(controller.wishlistChanged.calls, 0)

    def test_changed_materials_still_notifies(self):
        previous = {"materials": [{"key": "iron", "have": 50}], "blueprints": []}
        controller = self._controller({
            "materials": [{"key": "iron", "have": 51}], "blueprints": [],
        })

        controller._publish_full_state(previous)

        self.assertEqual(controller.materialsChanged.calls, 1)
        self.assertEqual(controller.wishlistChanged.calls, 0)

    def test_changed_wishlist_still_notifies(self):
        previous = {"materials": [], "blueprints": []}
        controller = self._controller({
            "materials": [], "blueprints": [{"planId": "new"}],
        })

        controller._publish_full_state(previous)

        self.assertEqual(controller.materialsChanged.calls, 0)
        self.assertEqual(controller.wishlistChanged.calls, 1)

    def test_no_previous_state_always_emits_everything(self):
        controller = self._controller({"materials": [], "blueprints": []})

        controller._publish_full_state()

        self.assertEqual(controller.materialsChanged.calls, 1)
        self.assertEqual(controller.wishlistChanged.calls, 1)


if __name__ == "__main__":
    unittest.main()
