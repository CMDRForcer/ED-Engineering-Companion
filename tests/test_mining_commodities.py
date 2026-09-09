import unittest

from ed_companion.navigation.mining_commodities import (
    mining_commodity_catalog,
    mining_commodity_id,
    mining_commodities_for_method,
)
from ed_companion.navigation.mining_finder import project_local_mining_evidence
from ed_companion.phase14.controller import CockpitController


class MiningCommodityCatalogTests(unittest.TestCase):
    def test_catalog_contains_asteroid_and_rhino_commodities(self):
        rows = mining_commodity_catalog()
        names = {row["name"] for row in rows}

        self.assertGreaterEqual(len(rows), 49)
        self.assertTrue({
            "Bauxite", "Bertrandite", "Hydrogen Peroxide", "Osmium",
            "Thorium", "Water", "Haematite", "Bastnäsite", "Deuterium",
            "Diamond", "Helium", "Helium-3", "Iridium", "Magnesite",
            "Olivine", "Periclase Dunite", "Quartz Pyroxenite", "Ruby",
            "Sapphire", "Thortveitite",
        }.issubset(names))

    def test_frontier_aliases_have_one_canonical_identity(self):
        self.assertEqual(mining_commodity_id("$opal_name;"), "opal")
        self.assertEqual(mining_commodity_id("Void Opals"), "opal")
        self.assertEqual(
            mining_commodity_id("Low Temperature Diamonds"),
            "lowtemperaturediamond",
        )

    def test_method_catalogs_are_distinct_and_complete_for_workflow(self):
        laser = {row["name"] for row in mining_commodities_for_method("LASER")}
        core = {row["name"] for row in mining_commodities_for_method("CORE")}
        rhino = {
            row["name"] for row in mining_commodities_for_method("RHINO SURFACE")
        }

        self.assertIn("Bauxite", laser)
        self.assertNotIn("Bauxite", core)
        self.assertIn("Void Opal", core)
        self.assertIn("Thortveitite", rhino)
        self.assertNotIn("Thortveitite", laser)
        self.assertEqual(len(rhino), 37)
        self.assertTrue({
            "Alexandrite", "Copper", "Gold", "Haematite", "Jadeite",
            "Low Temperature Diamonds", "Monazite", "Platinum", "Tantalum",
            "Titanium", "Tritium", "Uranium", "Water",
        }.issubset(rhino))
        self.assertIn("Haematite", laser)

    def test_unknown_observed_commodity_is_never_dropped(self):
        rows = mining_commodity_catalog([{"id": "$future_ore_name;"}])
        future = next(row for row in rows if row["id"] == "futureore")

        self.assertFalse(future["catalogued"])
        self.assertTrue(future["observed"])

    def test_projection_retains_refined_counts_and_rhino_method(self):
        result = project_local_mining_evidence([
            {"event": "LaunchSRV", "SRVType": "mev_rhino"},
            {"event": "MiningRefined", "timestamp": "2026-09-02T10:00:00Z",
             "Type": "$thortveitite_name;"},
            {"event": "MiningRefined", "timestamp": "2026-09-02T10:01:00Z",
             "Type": "$future_ore_name;"},
            {"event": "DockSRV"},
        ])

        self.assertEqual(
            [row["id"] for row in result["refinedCommodities"]],
            ["futureore", "thortveitite"],
        )
        self.assertTrue(all(
            row["methods"] == ["RHINO SURFACE"]
            for row in result["refinedCommodities"]
        ))

    def test_controller_uses_ring_type_for_non_hotspot_laser_ore(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {"system": "Test", "localMiningEvidence": {}}
        controller._mining_catalog = {"candidates": [{
            "system": "Test", "ring": "Test A Ring", "ringType": "Rocky",
            "evidence": "LOCAL_CONFIRMED", "observedAt": "2026-09-05T10:00:00Z",
            "hotspots": [],
        }]}

        rows = controller.miningFindPageForMethod(
            "Bauxite", 100, "ALL EVIDENCE", "ALL RESERVES", "LASER"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["targetMatch"], "RING_TYPE")

    def test_controller_presents_only_reported_planetary_rhino_destinations(self):
        controller = CockpitController.__new__(CockpitController)
        controller._state = {"system": "Test", "localMiningEvidence": {}}
        controller._mining_catalog = {"candidates": [{
            "system": "Test", "ring": "Test A Ring", "ringType": "Rocky",
            "evidence": "LOCAL_CONFIRMED", "hotspots": [],
        }, {
            "system": "Rhino Test", "ring": "Rhino Test 2 b",
            "evidence": "LIVE_REPORTED", "hotspots": [],
            "planetaryMiningLocationCount": 17,
        }]}

        rows = controller.miningFindPageForMethod(
            "Thortveitite", 0, "ALL EVIDENCE", "ALL RESERVES",
            "RHINO SURFACE",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["system"], "Rhino Test")
        self.assertEqual(rows[0]["planetaryMiningLocationCount"], 17)
        self.assertIn("COMMODITY UNCONFIRMED", rows[0]["targetMatchName"])

    def test_fss_body_signals_project_planetary_mining_locations(self):
        result = project_local_mining_evidence([{
            "event": "Location", "StarSystem": "Rhino Test",
            "SystemAddress": 42, "StarPos": [1, 2, 3],
        }, {
            "event": "FSSBodySignals", "timestamp": "2026-09-05T10:00:00Z",
            "BodyName": "Rhino Test 2 b", "BodyID": 8,
            "Signals": [{
                "Type": "$PlanetaryMiningLocation_Name;", "Count": 17,
            }],
        }])

        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(
            result["candidates"][0]["planetaryMiningLocationCount"], 17
        )


if __name__ == "__main__":
    unittest.main()
