import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.phase14.controller import CockpitController
from ed_companion.phase14.state import ProfileContext


class _Signal:
    def emit(self, *_args):
        pass


class InaraJournalRecoveryTests(unittest.TestCase):
    def _journals(self, root, count=7):
        paths = []
        for index in range(count):
            path = root / f"Journal.{index + 1:02d}.log"
            events = [
                {
                    "timestamp": f"2026-09-{index + 1:02d}T10:00:00Z",
                    "event": "Fileheader", "gameversion": "4.2.0.0",
                    "build": "r0",
                },
                {
                    "timestamp": f"2026-09-{index + 1:02d}T10:00:01Z",
                    "event": "LoadGame", "Commander": "Recovery Test",
                    "FID": "F-RECOVERY", "Horizons": True, "Odyssey": True,
                },
                {
                    "timestamp": f"2026-09-{index + 1:02d}T10:00:02Z",
                    "event": "FSDJump", "StarSystem": f"Recovery {index}",
                    "StarPos": [float(index), 2.0, 3.0],
                    "SystemAddress": 1000 + index,
                },
            ]
            path.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            paths.append(path)
        return paths

    def _controller(self, root, cache=None):
        profile = root / "profile-recovery"
        profile.mkdir(exist_ok=True)
        controller = CockpitController.__new__(CockpitController)
        controller.profile_context = ProfileContext(
            "F-RECOVERY", "recovery", profile, str(root)
        )
        controller._inara_cache = cache or {
            "initialized": True,
            "journal_root": str(root),
            "fingerprints": [],
        }
        controller._inara_config = {
            "consent": True, "auto_sync": True,
            "api_key": "test-key", "commander_name": "Recovery Test",
        }
        controller._inara_pending_events = []
        controller._inara_pending_fingerprints = []
        controller._inara_inflight_fingerprints = []
        controller._inara_recovery_candidate_file = ""
        controller._inara_pending_since = 0.0
        controller._inara_failure_count = 0
        controller._sync_eddn_profile = lambda: True
        controller._save_inara_config = mock.Mock()
        controller._save_inara_journal_cache = mock.Mock()
        controller.connectionChanged = _Signal()
        return controller

    def test_restart_recovers_pending_events_from_more_than_five_journals(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._journals(root, 7)
            controller = self._controller(root)

            with mock.patch(
                "ed_companion.phase14.controller.journal_paths_for_profile",
                return_value=paths,
            ):
                self.assertTrue(controller._scan_inara_journal())

            recovered_systems = {
                event.get("eventData", {}).get("starsystemName")
                for event in controller._inara_pending_events
                if event.get("eventName") == "addCommanderTravelFSDJump"
            }
            self.assertEqual(
                recovered_systems,
                {f"Recovery {index}" for index in range(7)},
            )

    def test_confirmed_boundary_reads_only_boundary_and_newer_journals(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._journals(root, 7)
            controller = self._controller(root, {
                "initialized": True,
                "journal_root": str(root),
                "fingerprints": [],
                "journal_recovery_file": paths[5].name,
            })

            with mock.patch(
                "ed_companion.phase14.controller.journal_paths_for_profile",
                return_value=paths,
            ):
                self.assertTrue(controller._scan_inara_journal())

            recovered_systems = {
                event.get("eventData", {}).get("starsystemName")
                for event in controller._inara_pending_events
                if event.get("eventName") == "addCommanderTravelFSDJump"
            }
            self.assertEqual(recovered_systems, {"Recovery 5", "Recovery 6"})


if __name__ == "__main__":
    unittest.main()
