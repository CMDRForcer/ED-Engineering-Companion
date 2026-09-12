import unittest

from ed_companion.journal.fleet import catalog_symbol_names, rebuild_fleet


class FleetProjectionTests(unittest.TestCase):
    def test_fleet_preserves_value_ident_location_and_current_rebuy(self):
        fleet = rebuild_fleet([
            {
                "event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z",
                "ShipID": 7, "Ship": "Mandalay", "ShipName": "Alma",
                "ShipIdent": "ALM-01",
            },
            {
                "event": "Loadout", "timestamp": "2026-01-01T10:00:01Z",
                "ShipID": 7, "Ship": "Mandalay", "ShipName": "Alma",
                "ShipIdent": "ALM-01", "HullValue": 100, "ModulesValue": 250,
                "Rebuy": 18,
            },
            {
                "event": "Docked", "timestamp": "2026-01-01T10:00:02Z",
                "StarSystem": "Shinrarta Dezhra", "StationName": "Jameson Memorial",
            },
            {
                "event": "StoredShips", "timestamp": "2026-01-01T10:00:03Z",
                "ShipsHere": [{
                    "ShipID": 9, "ShipType": "krait_mkii",
                    "Name": "Mechthild", "Value": 900,
                    "StarSystem": "Shinrarta Dezhra",
                    "StationName": "Jameson Memorial",
                }],
                "ShipsRemote": [],
            },
        ])

        rows = {row["id"]: row for row in fleet["ships"]}
        self.assertTrue(rows["7"]["isCurrent"])
        self.assertEqual(rows["7"]["ident"], "ALM-01")
        self.assertEqual(rows["7"]["value"], 350)
        self.assertEqual(rows["7"]["rebuy"], 18)
        self.assertEqual(rows["7"]["station"], "Jameson Memorial")
        self.assertEqual(rows["9"]["value"], 900)
        self.assertEqual(rows["9"]["type"], "Krait Mk II")

    def test_ship_catalog_resolves_a_symbol_the_guess_would_get_wrong(self):
        """Without a catalog, the display-name guess mangles any hull whose
        symbol does not literally spell out its marketing name - not just
        the handful of base-game exceptions once hard-coded here. The full
        ships.json catalog must resolve every one of them, current and
        future, without a per-ship exception."""
        ship_catalog = [
            {"symbol": "Explorer_NX", "name": "Caspian Explorer"},
            {"symbol": "Asp", "name": "Asp Explorer"},
        ]

        fleet = rebuild_fleet([
            {
                "event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z",
                "ShipID": 28, "Ship": "explorer_nx", "ShipName": "Erda",
            },
            {
                "event": "StoredShips", "timestamp": "2026-01-01T10:00:01Z",
                "ShipsHere": [{
                    "ShipID": 12, "ShipType": "Asp", "Name": "",
                }],
                "ShipsRemote": [],
            },
        ], ship_catalog)

        rows = {row["id"]: row for row in fleet["ships"]}
        self.assertEqual(rows["28"]["type"], "Caspian Explorer")
        self.assertEqual(rows["12"]["type"], "Asp Explorer")

    def test_a_localized_ship_name_still_wins_over_the_catalog(self):
        """Frontier's own localization reflects the player's client
        language; the English catalog name must not override it."""
        ship_catalog = [{"symbol": "Explorer_NX", "name": "Caspian Explorer"}]

        fleet = rebuild_fleet([{
            "event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z",
            "ShipID": 28, "Ship": "explorer_nx",
            "Ship_Localised": "Explorateur Caspienne",
        }], ship_catalog)

        self.assertEqual(fleet["ships"][0]["type"], "Explorateur Caspienne")

    def test_catalog_symbol_names_ignores_malformed_entries(self):
        self.assertEqual(
            catalog_symbol_names([
                {"symbol": "Asp", "name": "Asp Explorer"},
                {"symbol": "", "name": "Missing symbol"},
                {"symbol": "NoName"},
                "not a dict",
                None,
            ]),
            {"asp": "Asp Explorer"},
        )


if __name__ == "__main__":
    unittest.main()
