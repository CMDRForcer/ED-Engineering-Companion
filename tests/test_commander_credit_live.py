import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject

from ed_companion.phase14.controller import CockpitController


class CommanderCreditLiveTests(unittest.TestCase):
    def _controller(self, value=100, timestamp="2026-01-01T10:00:00Z"):
        controller = CockpitController.__new__(CockpitController)
        QObject.__init__(controller)
        controller._last_commander_status_stamp = None
        controller._state_revision = 4
        controller._derived_cache = {"commander_cards": (4, {})}
        controller._commander_credit_snapshots = []
        controller._archive_history = Mock(return_value=True)
        controller._state = {
            "commanderOverview": {
                "credits": {
                    "known": True, "value": value,
                    "timestamp": timestamp, "basis": "LIVE STATUS",
                },
                "lastUpdated": timestamp,
            },
        }
        return controller

    def test_timestamp_only_status_rewrite_does_not_invalidate_view(self):
        controller = self._controller()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Status.json"
            path.write_text(json.dumps({
                "timestamp": "2026-01-01T10:01:00Z", "Balance": 100,
            }), encoding="utf-8")
            with patch(
                "ed_companion.phase14.controller.journal_dir",
                return_value=Path(directory),
            ):
                controller._poll_commander_status_credits()

        self.assertEqual(controller._state_revision, 4)
        self.assertIn("commander_cards", controller._derived_cache)
        self.assertEqual(controller._commander_credit_snapshots[0]["credits"], 100)
        controller._archive_history.assert_called_once()

    def test_session_start_fallback_is_not_archived_as_live_balance(self):
        controller = self._controller()
        controller._state["commanderOverview"]["credits"]["basis"] = "SESSION START"

        recorded = controller._record_commander_credit_snapshot(
            controller._state["commanderOverview"]["credits"]
        )

        self.assertFalse(recorded)
        self.assertEqual(controller._commander_credit_snapshots, [])
        controller._archive_history.assert_not_called()

    def test_changed_balance_updates_state_and_invalidates_derived_views(self):
        controller = self._controller()
        emissions = []
        controller.stateChanged.connect(lambda: emissions.append(True))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Status.json"
            path.write_text(json.dumps({
                "timestamp": "2026-01-01T10:01:00Z", "Balance": 145,
            }), encoding="utf-8")
            with patch(
                "ed_companion.phase14.controller.journal_dir",
                return_value=Path(directory),
            ):
                controller._poll_commander_status_credits()

        credits = controller._state["commanderOverview"]["credits"]
        self.assertEqual(credits["value"], 145)
        self.assertEqual(credits["basis"], "LIVE STATUS")
        self.assertEqual(controller._state_revision, 5)
        self.assertEqual(controller._derived_cache, {})
        self.assertEqual(emissions, [True])
        self.assertEqual(controller._commander_credit_snapshots, [{
            "observedAt": "2026-01-01T10:01:00Z",
            "timestamp": "2026-01-01T10:01:00Z",
            "credits": 145,
            "source": "live_balance",
        }])
        controller._archive_history.assert_called_once_with(
            "commander_credit_snapshots",
            controller._commander_credit_snapshots,
        )


if __name__ == "__main__":
    unittest.main()
