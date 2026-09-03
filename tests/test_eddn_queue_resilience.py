import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ed_companion.integrations.eddn import prepare_event, update_context
from ed_companion.phase14.controller import CockpitController


def _prepared(index=0):
    context = {}
    for event in (
        {"event": "Fileheader", "gameversion": "4.2.0.0", "build": "r0"},
        {"event": "LoadGame", "Horizons": True, "Odyssey": True},
        {
            "event": "Location", "StarSystem": "Queue Test",
            "StarPos": [1, 2, 3], "SystemAddress": 42,
        },
    ):
        context = update_context(context, event)
    return prepare_event({
        "timestamp": f"2026-08-30T10:{index % 60:02d}:00Z",
        "event": "FSDJump", "StarSystem": f"Queue Test {index}",
        "StarPos": [1, 2, 3], "SystemAddress": 42,
    }, context)


class EddnQueueResilienceTests(unittest.TestCase):
    def _controller(self, directory):
        controller = CockpitController.__new__(CockpitController)
        controller.eddn_queue_file = Path(directory) / "community_upload_queue.json"
        controller.eddn_quarantine_file = (
            Path(directory) / "community_upload_quarantine.json"
        )
        return controller

    def test_restart_preserves_two_thousand_valid_jobs_and_recovers_sending(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            prepared = _prepared()
            jobs = [
                {
                    "id": f"EDDN-{index}", "target": "EDDN",
                    "status": "sending" if index == 0 else "queued",
                    "event": prepared,
                }
                for index in range(2000)
            ]
            controller.eddn_queue_file.write_text(
                json.dumps(jobs), encoding="utf-8"
            )

            loaded = controller._load_eddn_queue()

            self.assertEqual(len(loaded), 2000)
            self.assertEqual(loaded[0]["status"], "retry")
            self.assertTrue(loaded[0]["recovered_after_restart"])
            self.assertFalse(controller.eddn_quarantine_file.exists())

    def test_invalid_legacy_job_is_quarantined_while_valid_job_continues(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            jobs = [
                {
                    "id": "valid", "target": "EDDN", "status": "queued",
                    "event": _prepared(),
                },
                {
                    "id": "legacy-invalid", "target": "EDDN",
                    "status": "queued",
                    "event": {"schema": "future/private", "message": {}},
                },
            ]
            controller.eddn_queue_file.write_text(
                json.dumps(jobs), encoding="utf-8"
            )

            loaded = controller._load_eddn_queue()

            self.assertEqual([job["id"] for job in loaded], ["valid"])
            quarantine = json.loads(
                controller.eddn_quarantine_file.read_text(encoding="utf-8")
            )
            self.assertEqual(len(quarantine), 1)
            self.assertEqual(quarantine[0]["job"]["id"], "legacy-invalid")
            self.assertIn("Unsupported EDDN schema", quarantine[0]["reason"])

    def test_delivery_diagnostics_count_status_schema_and_recurring_error(self):
        controller = CockpitController.__new__(CockpitController)
        prepared = _prepared()
        controller._eddn_queue = [
            {
                "status": status, "event": prepared,
                "last_error": "schema rejected" if status in {"retry", "failed"} else "",
            }
            for status in ("queued", "retry", "failed", "sent")
        ]
        controller._eddn_config = {"last_success": {}}
        controller._eddn_quarantine_error_groups = {
            "journal/1 | unsupported legacy field": 2,
        }

        summary = controller._eddn_delivery_summary()

        self.assertEqual(
            [summary[key] for key in ("queued", "retry", "failed", "sent")],
            [1, 1, 1, 1],
        )
        self.assertEqual(summary["schemaCounts"], {"journal/1": 4})
        self.assertEqual(
            summary["errorGroups"], {"journal/1 | schema rejected": 2}
        )
        self.assertEqual(summary["quarantined"], 2)


if __name__ == "__main__":
    unittest.main()
