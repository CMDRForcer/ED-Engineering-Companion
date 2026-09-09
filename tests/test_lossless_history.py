import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from ed_companion.history_archive import HistoryArchive
from ed_companion.navigation.hge import partition_hge_observations
from ed_companion.phase14.controller import CockpitController
from ed_companion.services.upload_queue import partition_upload_queue


class LosslessHistoryTests(unittest.TestCase):
    def test_archive_is_profile_local_deduplicated_and_exportable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = HistoryArchive(root / "profile-a" / "data_history.sqlite3")
            archive.archive("eddn_sent", [
                {"id": "one", "status": "sent", "sent_at": "2026-09-06T10:00:00Z"},
                {"id": "two", "status": "sent", "sent_at": "2026-09-06T10:01:00Z"},
            ], key_field="id")
            archive.archive("eddn_sent", [
                {"id": "one", "status": "sent", "sent_at": "2026-09-06T10:00:00Z"},
            ], key_field="id")

            destination = archive.export_json(root / "history.json", active={
                "eddn_active": [{"id": "three", "status": "queued"}],
            })
            payload = json.loads(destination.read_text(encoding="utf-8"))

            self.assertEqual(archive.count("eddn_sent"), 2)
            self.assertEqual(len(payload["records"]), 3)
            self.assertEqual(
                {row["data"]["id"] for row in payload["records"]},
                {"one", "two", "three"},
            )
            self.assertTrue((root / "profile-a" / "data_history.sqlite3").exists())
            self.assertFalse((root / "profile-b" / "data_history.sqlite3").exists())

    def test_archive_preserves_identical_rows_within_one_source_batch(self):
        with TemporaryDirectory() as directory:
            archive = HistoryArchive(Path(directory) / "history.sqlite3")
            duplicate = {"system": "Same", "received_at": "2026-09-06T10:00:00Z"}

            archive.archive("hge_observations", [duplicate, duplicate])
            archive.archive("hge_observations", [duplicate, duplicate])

            self.assertEqual(archive.count("hge_observations"), 2)

    def test_archive_reads_tail_limited_records_chronologically(self):
        with TemporaryDirectory() as directory:
            archive = HistoryArchive(Path(directory) / "history.sqlite3")
            archive.archive("commander_credit_snapshots", [
                {"observedAt": "2026-01-01T10:00:00Z", "credits": 100},
                {"observedAt": "2026-01-01T10:02:00Z", "credits": 300},
                {"observedAt": "2026-01-01T10:01:00Z", "credits": 200},
            ])

            rows = archive.records("commander_credit_snapshots", limit=2)

            self.assertEqual([row["credits"] for row in rows], [200, 300])

    def test_upload_partition_preserves_every_pending_job_and_old_receipt(self):
        jobs = [
            *({"id": f"sent-{index}", "status": "sent"} for index in range(105)),
            *({"id": f"queued-{index}", "status": "queued"} for index in range(150)),
            {"id": "failed", "status": "failed"},
        ]

        active, historical = partition_upload_queue(jobs, sent_limit=100)

        self.assertEqual(len(active), 251)
        self.assertEqual(len(historical), 5)
        self.assertEqual(len(active) + len(historical), len(jobs))
        self.assertEqual(
            {row["id"] for row in active + historical},
            {row["id"] for row in jobs},
        )

    def test_hge_partition_expires_into_history_instead_of_discarding(self):
        now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
        recent = {
            "system": "Recent",
            "received_at": (now - timedelta(minutes=5)).isoformat(),
        }
        stale = {
            "system": "Stale",
            "received_at": (now - timedelta(days=2)).isoformat(),
        }
        expired_signal = {
            "system": "Expired signal",
            "evidence_kind": "EDDN_SIGNAL",
            "signal_timestamp": (now - timedelta(minutes=20)).isoformat(),
            "time_remaining": 600,
        }

        active, historical = partition_hge_observations(
            [recent, stale, expired_signal], now=now
        )

        self.assertEqual(active, [recent])
        self.assertEqual(historical, [stale, expired_signal])
        self.assertEqual(len(active) + len(historical), 3)

    def test_idle_hge_sweep_archives_expired_rows_before_shrinking_active_set(self):
        with TemporaryDirectory() as directory:
            now = datetime.now(timezone.utc)
            recent = {
                "system": "Recent",
                "received_at": (now - timedelta(minutes=5)).isoformat(),
            }
            stale = {
                "system": "Stale",
                "received_at": (now - timedelta(days=2)).isoformat(),
            }
            controller = CockpitController.__new__(CockpitController)
            controller.config_dir = Path(directory)
            controller.history_archive_file = (
                controller.config_dir / "data_history.sqlite3"
            )
            controller._history_archive = HistoryArchive(
                controller.history_archive_file
            )
            controller._pending_bgs_snapshots = []
            controller._pending_hge_observations = []
            controller._pending_mining_candidates = []
            controller._hge_sightings = [recent, stale]
            controller._next_hge_expiry_epoch = 0
            controller._selected_material = {}
            controller._save_hge_cache = Mock(return_value=True)
            controller.hgeChanged = Mock()
            controller.connectionChanged = Mock()

            controller.flushHgeObservationBatch()

            self.assertEqual(controller._hge_sightings, [recent])
            self.assertEqual(
                controller._history_archive.count("hge_observations"), 1
            )
            controller._save_hge_cache.assert_called_once()

    def test_derived_view_replacement_identifies_only_prior_versions(self):
        unchanged = {"system": "A", "sources": ["LOCAL"]}
        prior = {"system": "B", "sources": ["CATALOG"]}
        enhanced = {"system": "B", "sources": ["CATALOG", "EDDN"]}

        displaced = CockpitController._displaced_history_rows(
            [unchanged, prior], [unchanged, enhanced]
        )

        self.assertEqual(displaced, [prior])

        age_only = CockpitController._displaced_history_rows(
            [{**unchanged, "ageSeconds": 60, "stale": False}],
            [{**unchanged, "ageSeconds": 120, "stale": False}],
            {"ageSeconds", "stale"},
        )
        self.assertEqual(age_only, [])

    def test_controller_moves_old_receipts_only_after_archiving_them(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            controller = CockpitController.__new__(CockpitController)
            controller.config_dir = root
            controller.history_archive_file = root / "data_history.sqlite3"
            controller._history_archive = HistoryArchive(
                controller.history_archive_file
            )
            controller.eddn_config_file = root / "eddn_config.json"
            controller.eddn_queue_file = root / "community_upload_queue.json"
            controller._eddn_config = {"consent": False}
            controller._eddn_queue = [
                *(
                    {"id": f"sent-{index}", "status": "sent"}
                    for index in range(105)
                ),
                {"id": "queued", "status": "queued"},
            ]

            self.assertTrue(controller._save_eddn())

            saved = json.loads(
                controller.eddn_queue_file.read_text(encoding="utf-8")
            )
            self.assertEqual(len(saved), 101)
            self.assertEqual(sum(row["status"] == "sent" for row in saved), 100)
            self.assertIn("queued", {row["id"] for row in saved})
            self.assertEqual(controller._history_archive.count("eddn_sent"), 5)

    def test_controller_archives_inara_overflow_before_trimming_view(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            controller = CockpitController.__new__(CockpitController)
            controller.config_dir = root
            controller.history_archive_file = root / "data_history.sqlite3"
            controller._history_archive = HistoryArchive(
                controller.history_archive_file
            )
            controller.inara_receipts_file = root / "inara_receipts.json"
            controller._inara_receipts = [
                {"requestId": f"receipt-{index}", "created": str(index)}
                for index in range(103)
            ]

            controller._save_inara_receipts()

            saved = json.loads(
                controller.inara_receipts_file.read_text(encoding="utf-8")
            )
            self.assertEqual(len(saved), 100)
            self.assertEqual(
                controller._history_archive.count("inara_receipts"), 3
            )

    def test_controller_exports_large_history_in_background(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            controller = CockpitController.__new__(CockpitController)
            controller.config_dir = root
            controller._history_archive = HistoryArchive(root / "history.sqlite3")
            controller._history_archive.archive(
                "hge_observations", [{"system": "Archived"}]
            )
            controller._history_export_busy = False
            controller._profile_generation = 3
            controller.profile_context = Mock(key="alpha")
            controller._eddn_queue = []
            controller._hge_sightings = []
            controller._inara_receipts = []
            controller._mining_catalog = {"candidates": []}
            controller.connectionChanged = Mock()
            controller.historyExportFinished = Mock()
            workers = []
            controller._start_network_worker = (
                lambda target, _name: workers.append(target) or True
            )

            controller.exportDataHistory()

            self.assertTrue(controller._history_export_busy)
            self.assertEqual(len(workers), 1)
            self.assertEqual(list((root / "exports").glob("*.json")), [])
            workers[0]()
            payload = controller.historyExportFinished.emit.call_args.args[0]
            self.assertTrue(payload[2])
            self.assertTrue(Path(payload[3]).is_file())


if __name__ == "__main__":
    unittest.main()
