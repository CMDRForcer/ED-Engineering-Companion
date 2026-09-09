import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ed_companion.navigation.mining_finder import (
    fetch_spansh_system_dump,
    merge_mining_candidate_batch,
    merge_mining_candidates,
    mining_candidate_freshness,
    project_local_mining_evidence,
    project_spansh_mining_candidates,
    project_eddn_mining_candidates,
)
from ed_companion.phase14.controller import CockpitController, _eddn_relay_relevant


FIXTURE = json.loads(Path(__file__).with_name("fixtures").joinpath(
    "mining_finder_observations.json"
).read_text(encoding="utf-8"))


class MiningFinderProjectionTests(unittest.TestCase):
    def test_mining_catalog_load_is_deferred_and_profile_scoped(self):
        with TemporaryDirectory() as directory:
            controller = CockpitController.__new__(CockpitController)
            controller._profile_generation = 4
            controller.profile_context = Mock(key="alpha")
            controller.mining_catalog_file = Path(directory) / "mining.json"
            controller.mining_catalog_file.write_text(
                json.dumps({"candidates": [{"ring": "Alpha A Ring"}]}),
                encoding="utf-8",
            )
            workers = []
            controller._start_network_worker = (
                lambda target, _name: workers.append(target) or True
            )
            controller.miningCatalogLoaded = Mock()

            self.assertTrue(controller._start_mining_catalog_load())
            controller.miningCatalogLoaded.emit.assert_not_called()
            workers[0]()
            payload = controller.miningCatalogLoaded.emit.call_args.args[0]

            self.assertEqual(payload[:4], (
                1, 4, "alpha", str(controller.mining_catalog_file),
            ))
            self.assertEqual(payload[4]["candidates"][0]["ring"], "Alpha A Ring")

    def test_filter_rejects_distant_rows_before_hotspot_projection_and_caches(self):
        controller = CockpitController.__new__(CockpitController)
        controller._mining_rows_cache_key = ("stable",)
        controller._mining_rows = Mock(return_value=[{
            "system": f"Far {index}", "ring": f"Far {index} A Ring",
            "distanceLy": 5000.0, "evidence": "LIVE_REPORTED",
            "reserveLevel": "PristineResources",
            "hotspots": [{"commodity": "osmium", "count": 1}],
        } for index in range(5000)])

        with patch(
            "ed_companion.phase14.controller.mining_commodity_id",
            wraps=__import__(
                "ed_companion.phase14.controller", fromlist=["mining_commodity_id"]
            ).mining_commodity_id,
        ) as commodity_id:
            first = controller.miningFindPageForMethod(
                "OSMIUM", 100, "ALL EVIDENCE", "ALL RESERVES", "LASER"
            )
            calls_after_first = commodity_id.call_count
            second = controller.miningFindPageForMethod(
                "OSMIUM", 100, "ALL EVIDENCE", "ALL RESERVES", "LASER"
            )

        self.assertEqual(first, [])
        self.assertIs(second, first)
        self.assertEqual(calls_after_first, 1)
        self.assertEqual(commodity_id.call_count, 2)

    def test_large_mining_view_is_built_off_the_ui_thread(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {
            "system": "Origin", "currentPosition": [0, 0, 0],
            "localMiningEvidence": {},
        }
        controller._mining_catalog = {"candidates": [{
            "system": "Nearby", "ring": "Nearby A Ring",
            "coordinates": [1, 2, 3], "hotspots": [],
            "evidence": "CATALOG_CANDIDATE",
        }]}
        controller._profile_generation = 2
        controller.profile_context = Mock(key="alpha")
        controller._network_threads_lock = object()
        controller._mining_rows_cache_key = None
        controller._mining_rows_cache = []
        controller._mining_rows_build_token = 0
        controller._mining_rows_build_in_flight = False
        controller._mining_rows_build_dirty = False
        workers = []
        controller._start_network_worker = (
            lambda target, _name: workers.append(target) or True
        )
        controller.miningRowsReady = Mock()
        controller.miningChanged = Mock()

        self.assertEqual(controller._mining_rows(), [])
        self.assertEqual(len(workers), 1)
        workers[0]()
        payload = controller.miningRowsReady.emit.call_args.args[0]
        controller._finish_mining_rows_build(payload)

        self.assertEqual(controller._mining_rows()[0]["system"], "Nearby")
        controller.miningChanged.emit.assert_called_once_with()

    def test_live_mining_observations_are_retained_until_batched(self):
        controller = CockpitController.__new__(CockpitController)
        pending = [{"ring": "Pending A Ring"}]
        controller._pending_bgs_snapshots = []
        controller._pending_hge_observations = []
        controller._pending_mining_candidates = pending
        controller._last_mining_batch_monotonic = time.monotonic()
        controller._next_hge_expiry_epoch = time.time() + 3600
        controller._shutdown_complete = False

        controller.flushHgeObservationBatch()

        self.assertIs(controller._pending_mining_candidates, pending)

    def test_bgs_snapshots_are_retained_until_batched(self):
        controller = CockpitController.__new__(CockpitController)
        pending = [{"system": "Pending", "observations": []}]
        controller._pending_bgs_snapshots = pending
        controller._pending_hge_observations = []
        controller._pending_mining_candidates = []
        controller._last_bgs_batch_monotonic = time.monotonic()
        controller._last_mining_batch_monotonic = time.monotonic()
        controller._next_hge_expiry_epoch = time.time() + 3600
        controller._shutdown_complete = False

        controller.flushHgeObservationBatch()

        self.assertIs(controller._pending_bgs_snapshots, pending)

    def test_leaving_mining_page_releases_large_derived_view(self):
        controller = CockpitController.__new__(CockpitController)
        controller._last_page = 12
        controller._mining_rows_build_token = 4
        controller._mining_rows_build_in_flight = True
        controller._mining_rows_build_dirty = True
        controller._mining_rows_cache_key = ("large",)
        controller._mining_rows_cache = [{"system": "Cached"}]
        controller._mining_find_cache_key = ("filtered",)
        controller._mining_find_cache = [{"system": "Cached"}]
        controller._save_ui_config = Mock(return_value=True)

        controller.setLastPage(0)

        self.assertEqual(controller._mining_rows_build_token, 5)
        self.assertEqual(controller._mining_rows_cache, [])
        self.assertEqual(controller._mining_find_cache, [])
        self.assertIsNone(controller._mining_rows_cache_key)

    def test_mining_catalog_save_is_deferred_and_only_latest_snapshot_wins(self):
        with TemporaryDirectory() as directory:
            controller = CockpitController.__new__(CockpitController)
            controller.mining_catalog_file = Path(directory) / "mining.json"
            controller._shutdown_complete = False
            controller._mining_catalog = {"candidates": [{"ring": "Old"}]}
            workers = []
            controller._start_network_worker = (
                lambda target, _name: workers.append(target) or True
            )

            controller._save_mining_catalog()
            controller._mining_catalog = {"candidates": [{"ring": "Latest"}]}
            controller._save_mining_catalog()

            self.assertFalse(controller.mining_catalog_file.exists())
            workers[0]()
            self.assertFalse(controller.mining_catalog_file.exists())
            workers[1]()
            saved = json.loads(
                controller.mining_catalog_file.read_text(encoding="utf-8")
            )

        self.assertEqual(saved["candidates"][0]["ring"], "Latest")

    def test_incremental_mining_merge_matches_full_merge_without_dropping_rows(self):
        now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        base = [
            {"systemAddress": 1, "bodyId": 1, "ring": "A Ring",
             "evidence": "CATALOG_CANDIDATE", "observedAt": "2026-09-04T10:00:00Z",
             "hotspots": [{"commodity": "platinum", "count": 1}]},
            {"systemAddress": 2, "bodyId": 2, "ring": "B Ring",
             "evidence": "LIVE_REPORTED", "observedAt": "2026-09-05T10:00:00Z",
             "hotspots": []},
        ]
        additions = [
            {"systemAddress": 1, "bodyId": 1, "ring": "A Ring",
             "evidence": "LIVE_REPORTED", "observedAt": "2026-09-05T11:00:00Z",
             "hotspots": [{"commodity": "painite", "count": 1}]},
            {"systemAddress": 3, "bodyId": 3, "ring": "C Ring",
             "evidence": "LIVE_REPORTED", "observedAt": "2026-09-05T11:00:00Z",
             "hotspots": []},
        ]
        prepared = merge_mining_candidates(base, now=now)

        incremental, displaced = merge_mining_candidate_batch(
            prepared, additions, now=now
        )
        expected = merge_mining_candidates([*base, *additions], now=now)

        self.assertEqual(
            sorted(incremental, key=lambda row: row["ring"]),
            sorted(expected, key=lambda row: row["ring"]),
        )
        self.assertEqual([row["ring"] for row in displaced], ["A Ring"])

    def test_relay_prefilter_keeps_every_consumed_schema_and_event(self):
        self.assertTrue(_eddn_relay_relevant({
            "$schemaRef": "https://eddn.edcd.io/schemas/fsssignaldiscovered/1",
            "message": {},
        }))
        self.assertTrue(_eddn_relay_relevant({
            "$schemaRef": "https://eddn.edcd.io/schemas/fssbodysignals/1",
            "message": {},
        }))
        for event in (
            "FSDJump", "Location", "CarrierJump", "Scan", "SAASignalsFound",
        ):
            self.assertTrue(_eddn_relay_relevant({
                "$schemaRef": "https://eddn.edcd.io/schemas/journal/1",
                "message": {"event": event},
            }))
        self.assertFalse(_eddn_relay_relevant({
            "$schemaRef": "https://eddn.edcd.io/schemas/journal/1",
            "message": {"event": "Docked"},
        }))
        self.assertFalse(_eddn_relay_relevant({
            "$schemaRef": "https://eddn.edcd.io/schemas/commodity/3",
            "message": {},
        }))

    def test_reset_removes_only_the_active_profile_mining_catalog(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mining_file = root / "mining_finder_catalog.json"
            queue_file = root / "community_upload_queue.json"
            mining_file.write_text(
                json.dumps({"candidates": [{"ring": "Old ring"}]}),
                encoding="utf-8",
            )
            queue_file.write_text('[{"status": "queued"}]', encoding="utf-8")
            controller = CockpitController.__new__(CockpitController)
            controller.mining_catalog_file = mining_file
            controller._mining_catalog = {"candidates": [{"ring": "Old ring"}]}
            controller._active_mining_request = {"id": "old"}
            controller._mining_sync_busy = True
            controller._pending_mining_candidates = [{"ring": "Pending"}]
            controller.miningChanged = Mock()
            controller.stateChanged = Mock()

            controller.resetMiningCatalog()

            self.assertEqual(
                json.loads(mining_file.read_text(encoding="utf-8"))["candidates"],
                [],
            )
            self.assertEqual(
                queue_file.read_text(encoding="utf-8"), '[{"status": "queued"}]'
            )
            self.assertEqual(controller._pending_mining_candidates, [])
            self.assertTrue(controller._mining_catalog["resetAt"])
            self.assertIsNone(controller._mining_rows_cache_key)

    def test_reset_cutoff_hides_old_projected_journal_results(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {
            "system": "Test", "localMiningEvidence": {"candidates": [{
                "system": "Old", "ring": "Old A Ring",
                "observedAt": "2026-09-01T10:00:00Z",
                "evidence": "LOCAL_CONFIRMED", "hotspots": [],
            }, {
                "system": "New", "ring": "New A Ring",
                "observedAt": "2026-09-07T10:00:00Z",
                "evidence": "LOCAL_CONFIRMED", "hotspots": [],
            }]},
        }
        controller._mining_catalog = {
            "resetAt": "2026-09-06T10:00:00+00:00", "candidates": [],
        }

        rows = controller.miningFindPageForMethod(
            "ALL COMMODITIES", 0, "ALL EVIDENCE", "ALL RESERVES", "LASER"
        )

        self.assertEqual([row["system"] for row in rows], ["New"])

    def test_reset_cutoff_hides_old_catalog_but_accepts_newly_learned_rows(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {"system": "Test", "localMiningEvidence": {}}
        controller._mining_catalog = {
            "resetAt": "2026-09-06T10:00:00+00:00", "candidates": [{
                "system": "Old", "ring": "Old A Ring",
                "learnedAt": "2026-09-06T09:00:00+00:00",
                "evidence": "LIVE_REPORTED", "hotspots": [],
            }, {
                "system": "New", "ring": "New A Ring",
                "learnedAt": "2026-09-06T11:00:00+00:00",
                "evidence": "LIVE_REPORTED", "hotspots": [],
            }],
        }

        rows = controller.miningFindPageForMethod(
            "ALL COMMODITIES", 0, "ALL EVIDENCE", "ALL RESERVES", "LASER"
        )

        self.assertEqual([row["system"] for row in rows], ["New"])

    def test_laser_readiness_uses_installed_modules_and_cargo_capacity(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {
            "moduleSlots": [
                {"moduleId": "intdronecontrol_prospector_size3_class5"},
                {"moduleId": "intdronecontrol_collection_size5_class5"},
                {"moduleId": "intrefinery_size4_class5"},
            ],
            "selectedShipStats": {"cargoCapacity": 128},
        }

        result = controller.miningLoadoutReadiness("LASER")

        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "READY")

    def test_method_readiness_does_not_claim_missing_modules(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {
            "moduleSlots": [], "selectedShipStats": {"cargoCapacity": 0},
            "vehicleState": {"vehicles": []},
        }

        for method in ("LASER", "CORE", "SUBSURFACE", "RHINO SURFACE"):
            with self.subTest(method=method):
                self.assertFalse(controller.miningLoadoutReadiness(method)["ready"])

    def test_mining_multi_limpet_fulfils_prospector_and_collector_roles(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {
            "moduleSlots": [
                {"moduleId": "int_multidronecontrolminingmkii_size5_class1"},
                {"moduleId": "intrefinery_size3_class5"},
            ],
            "selectedShipStats": {"cargoCapacity": 164},
        }

        result = controller.miningLoadoutReadiness("LASER")

        self.assertTrue(result["ready"])
        self.assertIn("✓ Prospector", result["summary"])
        self.assertIn("✓ Collector", result["summary"])

    def test_non_mining_dss_categories_are_not_commodity_filters(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {"system": "Test", "localMiningEvidence": {}}
        controller._mining_catalog = {"candidates": [{
            "system": "Test", "ring": "Test A Ring",
            "evidence": "LIVE_REPORTED",
            "observedAt": "2026-09-04T10:00:00Z",
            "hotspots": [
                {"commodity": "$saa_signaltype_biological;", "count": 1},
                {"commodity": "planetarymininglocation", "count": 1},
                {"commodity": "monazite", "count": 1},
            ],
        }]}

        filters = controller._mining_commodity_filters()
        self.assertIn("Monazite", filters)
        self.assertIn("Water", filters)
        self.assertIn("Thortveitite", filters)
        self.assertNotIn("Biological", filters)
        self.assertNotIn("Planetarymininglocation", filters)
        self.assertEqual(controller._mining_rows()[0]["hotspotNames"], "Monazite")

    def test_eddn_scan_and_hotspots_merge_without_private_fields(self):
        common = {
            "$schemaRef": "https://eddn.edcd.io/schemas/journal/1",
        }
        scan = {**common, "message": {
            "event": "Scan", "timestamp": "2026-09-04T10:00:00Z",
            "StarSystem": "Public System", "SystemAddress": 42,
            "StarPos": [1, 2, 3], "BodyName": "Public System 1",
            "BodyID": 1, "ReserveLevel": "PristineResources",
            "Rings": [{"Name": "Public System 1 A Ring",
                       "RingClass": "eRingClass_Metallic"}],
            "Commander": "PRIVATE", "FuturePrivateField": {"secret": "PRIVATE"},
        }}
        signals = {**common, "message": {
            "event": "SAASignalsFound", "timestamp": "2026-09-04T10:01:00Z",
            "StarSystem": "Public System", "SystemAddress": 42,
            "StarPos": [1, 2, 3], "BodyName": "Public System 1 A Ring",
            "BodyID": 1, "Signals": [
                {"Type": "$Platinum_Name;", "Count": 2,
                 "FuturePrivateField": "PRIVATE"}
            ], "FID": "PRIVATE",
        }}

        rows = merge_mining_candidates([
            *project_eddn_mining_candidates(scan),
            *project_eddn_mining_candidates(signals),
        ], now=datetime(2026, 9, 4, 10, 2, tzinfo=timezone.utc))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence"], "LIVE_REPORTED")
        self.assertEqual(rows[0]["hotspots"], [
            {"commodity": "platinum", "count": 2}
        ])
        self.assertNotIn("PRIVATE", json.dumps(rows))

    def test_eddn_fss_body_signals_project_rhino_destination(self):
        rows = project_eddn_mining_candidates({
            "$schemaRef": "https://eddn.edcd.io/schemas/fssbodysignals/1",
            "message": {
                "event": "FSSBodySignals",
                "timestamp": "2026-09-05T10:00:00Z",
                "StarSystem": "Rhino Test", "SystemAddress": 42,
                "StarPos": [1, 2, 3], "BodyName": "Rhino Test 2 b",
                "BodyID": 8, "Signals": [{
                    "Type": "$PlanetaryMiningLocation_Name;", "Count": 17,
                }],
            },
        })

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["planetaryMiningLocationCount"], 17)
        self.assertEqual(rows[0]["hotspots"], [])

    def test_spansh_fetch_sends_only_public_system_address(self):
        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"system": {"id64": 42, "bodies": []}}

        def get(url, **kwargs):
            calls.append((url, kwargs))
            return Response()

        payload = fetch_spansh_system_dump(42, get)

        self.assertEqual(payload["system"]["id64"], 42)
        self.assertEqual(calls, [(
            "https://spansh.co.uk/api/dump/42", {"timeout": 20}
        )])

    def test_controller_filters_the_same_projected_state_without_journal_io(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {
            "system": "Test System",
            "localMiningEvidence": {"candidates": [{
                "system": "Test System", "systemAddress": 7,
                "ring": "Test A Ring", "hotspots": [
                    {"commodity": "platinum", "count": 2}
                ], "evidence": "LOCAL_CONFIRMED",
                "observedAt": "2026-09-04T10:00:00Z", "source": "test",
            }]},
        }
        controller._mining_catalog = {"candidates": []}

        rows = controller.miningFindPage(
            "Platinum", 25, "ALL EVIDENCE", "ALL RESERVES"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["distanceLy"], 0.0)
        self.assertEqual(rows[0]["hotspotNames"], "Platinum")

    NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    def test_local_scan_and_saa_form_one_confirmed_ring_candidate(self):
        result = project_local_mining_evidence(FIXTURE["local_events"])

        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["system"], "Synthetic Mining System")
        self.assertEqual(candidate["ring"], "Synthetic Mining System 1 A Ring")
        self.assertEqual(candidate["evidence"], "LOCAL_CONFIRMED")
        self.assertEqual(candidate["hotspots"], [
            {"commodity": "painite", "count": 1},
            {"commodity": "platinum", "count": 2},
        ])
        serialized = json.dumps(candidate)
        self.assertNotIn("must-not-project", serialized)
        self.assertNotIn("private display", serialized)

    def test_prospector_sample_is_not_falsely_bound_to_a_ring(self):
        result = project_local_mining_evidence(FIXTURE["local_events"])

        self.assertEqual(len(result["prospectorSamples"]), 1)
        sample = result["prospectorSamples"][0]
        self.assertFalse(sample["boundToRing"])
        self.assertEqual(sample["motherlode"], "alexandrite")
        self.assertEqual(sample["materials"], [
            {"commodity": "platinum", "proportion": 22.4},
        ])

    def test_spansh_dump_projects_catalog_candidate_and_source_timestamp(self):
        candidates = project_spansh_mining_candidates(
            FIXTURE["spansh_dump"], origin=[10.0, 20.0, 30.0]
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["evidence"], "CATALOG_CANDIDATE")
        self.assertEqual(candidate["distanceLy"], 5.0)
        self.assertEqual(candidate["ringType"], "Metallic")
        self.assertEqual(candidate["reserveLevel"], "MajorResources")
        self.assertEqual(candidate["observedAt"], "2026-09-02T13:00:00Z")
        self.assertEqual(candidate["hotspots"], [
            {"commodity": "platinum", "count": 1},
        ])

    def test_invalid_or_partial_spansh_payload_stays_empty_or_unknown(self):
        self.assertEqual(project_spansh_mining_candidates({}), [])
        candidates = project_spansh_mining_candidates({
            "system": {
                "name": "Partial", "bodies": [{
                    "name": "Partial 1", "rings": [{"name": "A Ring"}],
                }],
            },
        })
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["hotspots"], [])
        self.assertEqual(candidates[0]["reserveLevel"], "")
        self.assertIsNone(candidates[0]["distanceLy"])

    def test_confirmation_age_never_invalidates_the_location_evidence(self):
        fresh = mining_candidate_freshness({
            "evidence": "LIVE_REPORTED",
            "observedAt": "2026-09-04T11:00:00Z",
        }, now=self.NOW)
        old = mining_candidate_freshness({
            "evidence": "LIVE_REPORTED",
            "observedAt": "2026-09-03T11:59:59Z",
        }, now=self.NOW)
        undated = mining_candidate_freshness({
            "evidence": "CATALOG_CANDIDATE", "observedAt": "",
        }, now=self.NOW)

        self.assertEqual(fresh["evidence"], "LIVE_REPORTED")
        self.assertFalse(fresh["stale"])
        self.assertEqual(old["evidence"], "LIVE_REPORTED")
        self.assertTrue(old["stale"])
        self.assertTrue(old["recheckRecommended"])
        self.assertEqual(old["confirmationStatus"], "RECHECK_RECOMMENDED")
        self.assertEqual(undated["evidence"], "CATALOG_CANDIDATE")
        self.assertEqual(
            undated["confirmationStatus"], "CONFIRMATION_TIME_UNKNOWN"
        )

    def test_merge_prefers_local_fields_and_preserves_all_sources(self):
        common = {
            "system": "Synthetic Mining System",
            "systemAddress": 123456789,
            "body": "Synthetic Mining System 1",
            "bodyId": 4,
            "ring": "Synthetic Mining System 1 A Ring",
            "distanceLy": 12.5,
        }
        catalog = {
            **common, "evidence": "CATALOG_CANDIDATE",
            "observedAt": "2026-09-04T11:30:00Z",
            "source": "Spansh dump catalog", "ringType": "Metallic",
            "reserveLevel": "MajorResources",
            "hotspots": [{"commodity": "painite", "count": 1}],
        }
        live = {
            **common, "evidence": "LIVE_REPORTED",
            "observedAt": "2026-09-04T11:40:00Z",
            "source": "EDDN journal/1", "ringType": "Metal Rich",
            "hotspots": [{"commodity": "platinum", "count": 2}],
        }
        local = {
            **common, "evidence": "LOCAL_CONFIRMED",
            "observedAt": "2026-09-04T11:20:00Z",
            "source": "Frontier Journal", "ringType": "eRingClass_Metalic",
            "reserveLevel": "", "hotspots": [
                {"commodity": "platinum", "count": 1},
            ],
        }

        merged = merge_mining_candidates(
            [catalog, live, local], now=self.NOW
        )

        self.assertEqual(len(merged), 1)
        candidate = merged[0]
        self.assertEqual(candidate["evidence"], "LOCAL_CONFIRMED")
        self.assertEqual(candidate["ringType"], "eRingClass_Metalic")
        self.assertEqual(candidate["reserveLevel"], "MajorResources")
        self.assertEqual(candidate["sourceCount"], 3)
        self.assertEqual(
            [row["sourceEvidence"] for row in candidate["observations"]],
            ["LOCAL_CONFIRMED", "LIVE_REPORTED", "CATALOG_CANDIDATE"],
        )
        self.assertEqual(candidate["hotspots"], [
            {"commodity": "painite", "count": 1},
            {"commodity": "platinum", "count": 1},
        ])

        remerged = merge_mining_candidates(
            [candidate], now=self.NOW + timedelta(minutes=5)
        )[0]
        self.assertEqual(remerged["sourceCount"], 3)
        self.assertEqual(remerged["observations"], candidate["observations"])

    def test_merge_uses_stable_identity_and_sorts_known_distance_first(self):
        candidates = [{
            "system": "Far", "systemAddress": 20, "bodyId": 1,
            "ring": "A Ring", "distanceLy": None,
            "evidence": "CATALOG_CANDIDATE",
            "observedAt": "2026-09-04T11:00:00Z",
        }, {
            "system": "Near", "systemAddress": 10, "bodyId": 1,
            "ring": "A Ring", "distanceLy": 4.0,
            "evidence": "CATALOG_CANDIDATE",
            "observedAt": "2026-09-04T11:00:00Z",
        }, {
            "system": "Renamed Near", "systemAddress": 10, "bodyId": 1,
            "ring": "A Ring", "distanceLy": 4.0,
            "evidence": "LIVE_REPORTED",
            "observedAt": "2026-09-04T11:30:00Z",
        }]

        merged = merge_mining_candidates(candidates, now=self.NOW)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["systemAddress"], 10)
        self.assertEqual(merged[0]["sourceCount"], 2)
        self.assertEqual(merged[1]["systemAddress"], 20)


if __name__ == "__main__":
    unittest.main()
