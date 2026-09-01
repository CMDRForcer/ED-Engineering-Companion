import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.integrations.eddn import update_context
from ed_companion.phase14.controller import CockpitController


def _line(event):
    return json.dumps(event, separators=(",", ":")) + "\n"


class EddnJournalCursorTests(unittest.TestCase):
    def _controller(self, paths):
        controller = CockpitController.__new__(CockpitController)
        controller._eddn_config = {"consent": True, "upload_enabled": True}
        controller._eddn_profile_identity = "F-CURSOR-TEST"
        controller._eddn_profile_key = "cursor-test"
        controller._eddn_journal_root = str(paths[0].parent)
        controller._eddn_baseline_established = True
        controller._journal_offsets = {}
        controller._station_fingerprints = {}
        controller._navroute_fingerprint = ""
        controller._navroute_rejections = {}
        controller._eddn_context = {}
        for event in (
            {"event": "Fileheader", "gameversion": "4.2.0.0", "build": "r0"},
            {"event": "LoadGame", "Horizons": True, "Odyssey": True},
            {
                "event": "Location", "StarSystem": "Cursor Test",
                "StarPos": [1.0, 2.0, 3.0], "SystemAddress": 42,
            },
        ):
            controller._eddn_context = update_context(
                controller._eddn_context, event
            )
        controller._sync_eddn_profile = lambda: True
        controller._eddn_profile_journal_paths = lambda: list(paths)
        controller._scan_eddn_station_files = lambda: None
        controller._save_eddn_cursor = lambda: None
        controller._record_eddn_not_shareable = lambda *_args: None
        controller._queued_for_test = []
        controller._enqueue_eddn = controller._queued_for_test.append
        return controller

    @staticmethod
    def _signature(paths):
        return (str(paths[0].parent), tuple(
            (path.name, path.stat().st_size, path.stat().st_mtime_ns)
            for path in paths
        ))

    def _scan(self, controller, paths):
        with mock.patch(
            "ed_companion.phase14.controller.journal_change_signature",
            return_value=self._signature(paths),
        ):
            controller._scan_eddn_journal()

    def test_eddn_partial_line_is_retried_and_queued_exactly_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "Journal.01.log"
            event = {
                "timestamp": "2026-08-30T10:00:00Z", "event": "FSDJump",
                "StarSystem": "Cursor Destination", "StarPos": [4, 5, 6],
                "SystemAddress": 84,
            }
            encoded = json.dumps(event, separators=(",", ":"))
            split = len(encoded) // 2
            path.write_text(encoded[:split], encoding="utf-8")
            controller = self._controller([path])

            self._scan(controller, [path])
            self.assertEqual(controller._journal_offsets[path.name], 0)
            self.assertEqual(controller._queued_for_test, [])

            with path.open("a", encoding="utf-8") as handle:
                handle.write(encoded[split:] + "\n")
            self._scan(controller, [path])
            self._scan(controller, [path])

            self.assertEqual(len(controller._queued_for_test), 1)
            self.assertEqual(
                controller._queued_for_test[0]["message"]["event"], "FSDJump"
            )

    def test_eddn_opt_in_baselines_existing_journal_without_replay(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "Journal.01.log"
            path.write_text(_line({
                "timestamp": "2026-08-30T10:00:00Z", "event": "FSDJump",
                "StarSystem": "Old Destination", "StarPos": [4, 5, 6],
                "SystemAddress": 84,
            }), encoding="utf-8")
            controller = self._controller([path])
            controller._eddn_baseline_established = False

            self._scan(controller, [path])

            self.assertTrue(controller._eddn_baseline_established)
            self.assertEqual(
                controller._journal_offsets[path.name], path.stat().st_size
            )
            self.assertEqual(controller._queued_for_test, [])

    def test_eddn_rotated_journal_starts_at_zero_and_queues_each_event_once(self):
        with TemporaryDirectory() as directory:
            old_path = Path(directory) / "Journal.01.log"
            old_path.write_text(_line({"event": "Fileheader"}), encoding="utf-8")
            controller = self._controller([old_path])
            controller._eddn_baseline_established = False
            self._scan(controller, [old_path])

            new_path = Path(directory) / "Journal.02.log"
            events = [
                {
                    "timestamp": "2026-08-30T10:01:00Z", "event": "FSDJump",
                    "StarSystem": "Rotation One", "StarPos": [7, 8, 9],
                    "SystemAddress": 126,
                },
                {
                    "timestamp": "2026-08-30T10:02:00Z", "event": "ScanOrganic",
                    "ScanType": "Sample", "Genus": "$Genus;",
                    "Species": "$Species;", "Body": 3,
                    "SystemAddress": 126,
                },
            ]
            new_path.write_text("".join(map(_line, events)), encoding="utf-8")
            controller._eddn_profile_journal_paths = lambda: [old_path, new_path]

            self._scan(controller, [old_path, new_path])
            self._scan(controller, [old_path, new_path])

            self.assertEqual(len(controller._queued_for_test), 2)
            self.assertEqual(
                [row["message"]["event"] for row in controller._queued_for_test],
                ["FSDJump", "ScanOrganic"],
            )

    def test_eddn_completed_tail_then_next_event_are_both_retained(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "Journal.01.log"
            first = {
                "timestamp": "2026-08-30T10:01:00Z", "event": "FSDJump",
                "StarSystem": "First", "StarPos": [7, 8, 9],
                "SystemAddress": 126,
            }
            second = {
                "timestamp": "2026-08-30T10:02:00Z", "event": "FSDJump",
                "StarSystem": "Second", "StarPos": [10, 11, 12],
                "SystemAddress": 168,
            }
            encoded = json.dumps(first, separators=(",", ":"))
            split = len(encoded) - 4
            path.write_text(encoded[:split], encoding="utf-8")
            controller = self._controller([path])

            self._scan(controller, [path])
            with path.open("a", encoding="utf-8") as handle:
                handle.write(encoded[split:] + "\n" + _line(second))
            self._scan(controller, [path])

            self.assertEqual(
                [row["message"]["StarSystem"] for row in controller._queued_for_test],
                ["First", "Second"],
            )


if __name__ == "__main__":
    unittest.main()
