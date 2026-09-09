from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timedelta, timezone
import json

from ed_companion.navigation.hge import rank_hge_sightings
from ed_companion.phase14.controller import CockpitController


def _row(system, faction, state, materials, evidence="BGS_PREDICTION"):
    return {
        "findType": "HGE",
        "findLabel": "High Grade Emissions",
        "system": system,
        "distance": 9.0,
        "state": state,
        "stateValues": [state],
        "allegiance": "Federation",
        "allegianceValues": ["Federation"],
        "faction": faction,
        "materials": materials,
        "reportCount": 1,
        "evidenceKind": evidence,
        "isCurrentSystem": False,
    }


class StateFindGroupingTests(unittest.TestCase):
    def test_hge_cache_writes_are_deferred_and_isolated_per_profile_path(self):
        with TemporaryDirectory() as directory:
            controller = CockpitController.__new__(CockpitController)
            controller._shutdown_complete = False
            workers = []
            controller._start_network_worker = (
                lambda target, _name: workers.append(target) or True
            )
            first_path = Path(directory) / "alpha.json"
            second_path = Path(directory) / "bravo.json"

            controller.hge_cache_file = first_path
            controller._hge_sightings = [{"system": "Alpha"}]
            controller._save_hge_cache(already_partitioned=True)
            controller.hge_cache_file = second_path
            controller._hge_sightings = [{"system": "Bravo"}]
            controller._save_hge_cache(already_partitioned=True)

            self.assertFalse(first_path.exists())
            self.assertFalse(second_path.exists())
            for worker in workers:
                worker()

            self.assertEqual(
                json.loads(first_path.read_text(encoding="utf-8"))[0]["system"],
                "Alpha",
            )
            self.assertEqual(
                json.loads(second_path.read_text(encoding="utf-8"))[0]["system"],
                "Bravo",
            )

    def test_batched_hge_targets_match_single_material_ranking(self):
        now = datetime.now(timezone.utc)
        sightings = [
            {
                "system": "Near", "system_address": 1,
                "star_pos": [1, 0, 0], "faction": "A", "state": "Boom",
                "signal_timestamp": (now - timedelta(minutes=2)).isoformat(),
                "time_remaining": 1800,
                "materials": [{"material": "fedcorecomposites", "confidence": .7}],
            },
            {
                "system": "Strong", "system_address": 2,
                "star_pos": [5, 0, 0], "faction": "B", "state": "Boom",
                "signal_timestamp": (now - timedelta(minutes=1)).isoformat(),
                "time_remaining": 1800,
                "materials": [{"material": "fedcorecomposites", "confidence": .9}],
            },
        ]
        controller = CockpitController.__new__(CockpitController)
        controller._state = {
            "currentPosition": [0, 0, 0],
            "materials": [{
                "key": "fedcorecomposites", "name": "Core Dynamics Composites",
                "missing": 12,
            }],
        }
        controller._hge_sightings = sightings

        expected = rank_hge_sightings(
            sightings, "fedcorecomposites", [0, 0, 0]
        )[0]
        actual = controller._build_hge_targets()[0]

        self.assertEqual(actual["system"], expected["system"])
        self.assertAlmostEqual(actual["confidence"], expected["confidence"], places=3)

    def test_state_find_cards_size_to_wrapped_content(self):
        qml = Path(__file__).resolve().parents[1].joinpath("Main.qml").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn(
            "height: Math.max(118, stateFindCardContent.implicitHeight + 28)",
            qml,
        )
        self.assertIn("id: stateFindCardContent", qml)
        self.assertIn("wrapMode: Text.WordWrap; Layout.fillWidth: true", qml)

    def test_cache_summary_separates_bgs_signals_and_time_bounds(self):
        controller = CockpitController.__new__(CockpitController)
        controller._hge_sightings = [
            {
                "evidence_kind": "BGS_PREDICTION",
                "received_at": "2026-09-03T10:00:00Z",
            },
            {
                "evidence_kind": "EDDN_SIGNAL",
                "signal_timestamp": "2026-09-03T11:00:00Z",
            },
            {
                "evidence_kind": "LOCAL_JOURNAL",
                "signal_timestamp": "2026-09-03T12:00:00Z",
            },
        ]

        summary = controller._state_find_cache_summary()

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["bgs"], 1)
        self.assertEqual(summary["signals"], 2)
        self.assertEqual(summary["oldestAt"], "2026-09-03 10:00 UTC")
        self.assertEqual(summary["newestAt"], "2026-09-03 12:00 UTC")
        self.assertEqual(summary["retentionHours"], 24)

    def test_bgs_predictions_share_one_travel_card_with_distinct_variants(self):
        rows = [
            _row("LTT 4428", "Faction A", "Election", "Material A"),
            _row("LTT 4428", "Faction B", "Boom", "Material B"),
        ]

        grouped = CockpitController._group_state_find_travel_targets(rows)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["system"], "LTT 4428")
        self.assertEqual(grouped[0]["variantCount"], 2)
        self.assertEqual(
            [(item["faction"], item["state"], item["materials"])
             for item in grouped[0]["variants"]],
            [
                ("Faction A", "Election", "Material A"),
                ("Faction B", "Boom", "Material B"),
            ],
        )

    def test_live_signals_remain_separate_destinations(self):
        rows = [
            _row("LTT 4428", "Faction A", "Boom", "Material A", "EDDN_SIGNAL"),
            _row("LTT 4428", "Faction A", "Boom", "Material A", "EDDN_SIGNAL"),
        ]

        grouped = CockpitController._group_state_find_travel_targets(rows)

        self.assertEqual(len(grouped), 2)
        self.assertTrue(all(row["variantCount"] == 1 for row in grouped))


if __name__ == "__main__":
    unittest.main()
