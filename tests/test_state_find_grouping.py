import unittest

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
