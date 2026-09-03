import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.integrations.eddn import prepare_event, send, update_context
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

    def test_legacy_journal_job_is_rebuilt_without_resending_sent_receipt(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            legacy = _prepared()
            legacy["message"].update({
                "FuturePrivateField": "must-not-survive",
                "Taxi": True,
                "SystemFaction": {
                    "Name": "Public faction",
                    "MyReputation": 99,
                    "FutureNestedPrivate": "must-not-survive",
                },
            })
            quarantined = [{
                "quarantined_at": "2026-09-03T00:00:00+00:00",
                "reason": "old allowlist rejection",
                "job": {
                    "id": "old-id", "target": "EDDN", "status": "sent",
                    "event": legacy, "receipt": {"httpStatus": 200},
                },
            }]
            controller.eddn_queue_file.write_text("[]", encoding="utf-8")
            controller.eddn_quarantine_file.write_text(
                json.dumps(quarantined), encoding="utf-8"
            )

            loaded = controller._load_eddn_queue()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["status"], "sent")
            self.assertEqual(loaded[0]["receipt"], {"httpStatus": 200})
            message = loaded[0]["event"]["message"]
            self.assertNotIn("FuturePrivateField", message)
            self.assertNotIn("Taxi", message)
            self.assertEqual(
                message["SystemFaction"], {"Name": "Public faction"}
            )
            self.assertEqual(
                json.loads(controller.eddn_quarantine_file.read_text()), []
            )

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
        controller._eddn_profile_key = "diagnostic-profile"
        controller.config_dir = Path("profile-diagnostic-profile")
        controller.eddn_queue_file = (
            controller.config_dir / "community_upload_queue.json"
        )
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

    def test_later_success_hides_stale_not_shareable_notice(self):
        controller = CockpitController.__new__(CockpitController)
        controller._eddn_queue = []
        controller._eddn_config = {
            "last_success": {"sentAt": "2026-09-03T18:16:44+00:00"},
            "last_not_shareable": "FCMaterials · local-only",
            "last_not_shareable_at": "2026-09-03T18:10:00+00:00",
        }
        controller._eddn_profile_key = "diagnostic-profile"
        controller.config_dir = Path("profile-diagnostic-profile")
        controller.eddn_queue_file = (
            controller.config_dir / "community_upload_queue.json"
        )
        controller._eddn_quarantine_error_groups = {}

        summary = controller._eddn_delivery_summary()

        self.assertEqual(summary["lastNotShareable"], "")

    def test_outfitting_v3_receipt_marks_station_snapshot_sent(self):
        with TemporaryDirectory() as directory:
            timestamp = "2026-09-03T18:20:00Z"
            snapshot = {
                "timestamp": timestamp,
                "StarSystem": "Queue Test",
                "StationName": "Test Station",
            }
            Path(directory, "Outfitting.json").write_text(
                json.dumps(snapshot), encoding="utf-8"
            )
            controller = CockpitController.__new__(CockpitController)
            controller._eddn_context = {}
            controller._eddn_queue = []
            controller._eddn_config = {"station_receipts": {
                "outfitting/3": {
                    "stationName": "Test Station", "timestamp": timestamp,
                    "result": "Gateway accepted HTTP 200",
                },
            }}

            with mock.patch(
                "ed_companion.phase14.controller.station_snapshot_mismatch_reason",
                return_value="",
            ), mock.patch(
                "ed_companion.phase14.controller.prepare_station_snapshot",
                return_value={"schema": "outfitting/3", "message": {}},
            ):
                rows = controller._eddn_station_snapshot_view(directory)

            outfitting = next(row for row in rows if row["kind"] == "OUTFITTING")
            self.assertEqual(outfitting["status"], "SENT")

    def test_new_public_event_reaches_mock_gateway_exactly_once(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"OK"

        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return Response()

        prepared = _prepared()
        receipt = send(
            prepared,
            {"gameversion": "4.2.0.0", "gamebuild": "r0"},
            "anonymous-test-uploader",
            opener=opener,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt["httpStatus"], 200)
        self.assertEqual(receipt["schema"], "journal/1")
        self.assertEqual(receipt["event"], "FSDJump")

    def test_queue_worker_starts_before_follow_up_drain_is_scheduled(self):
        class Signal:
            def emit(self, *_args):
                pass

        controller = CockpitController.__new__(CockpitController)
        controller._eddn_queue = [{
            "id": "job", "target": "EDDN", "status": "queued",
            "event": _prepared(), "context": {}, "attempts": 0,
            "profile_key": "profile",
        }]
        controller._eddn_profile_key = "profile"
        controller._eddn_config = {
            "consent": True, "upload_enabled": True,
            "uploader_id": "anonymous-test-uploader",
            "last_not_shareable": "FCMaterials · local-only",
            "last_not_shareable_at": "2026-09-03T10:00:00+00:00",
        }
        controller._eddn_busy = False
        controller._sync_eddn_profile = lambda: True
        controller._save_eddn = lambda: None
        controller._publish_eddn_delivery_change = lambda: None
        controller.connectionChanged = Signal()
        workers = []
        controller._start_network_worker = (
            lambda target, name: workers.append((target, name)) or True
        )

        controller._process_eddn_queue()

        self.assertEqual(controller._eddn_queue[0]["status"], "sending")
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0][1], "eddn-upload")

        with mock.patch(
            "ed_companion.phase14.controller.QTimer.singleShot"
        ) as single_shot:
            controller._finish_eddn(
                "job", True,
                json.dumps({
                    "httpStatus": 200, "event": "FSDJump", "elapsedMs": 1,
                }),
            )

        self.assertEqual(controller._eddn_queue[0]["status"], "sent")
        self.assertEqual(controller._eddn_config["last_not_shareable"], "")
        self.assertEqual(controller._eddn_config["last_not_shareable_at"], "")
        single_shot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
