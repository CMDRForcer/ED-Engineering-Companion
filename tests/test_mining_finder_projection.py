import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from ed_companion.navigation.mining_finder import (
    fetch_spansh_system_dump,
    merge_mining_candidates,
    mining_candidate_freshness,
    project_local_mining_evidence,
    project_spansh_mining_candidates,
    project_eddn_mining_candidates,
)
from ed_companion.phase14.controller import CockpitController


FIXTURE = json.loads(Path(__file__).with_name("fixtures").joinpath(
    "mining_finder_observations.json"
).read_text(encoding="utf-8"))


class MiningFinderProjectionTests(unittest.TestCase):
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

        self.assertEqual(controller._mining_commodity_filters(), [
            "ALL COMMODITIES", "Monazite"
        ])
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
