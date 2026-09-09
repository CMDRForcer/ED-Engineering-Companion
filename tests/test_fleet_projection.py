import unittest

from ed_companion.journal.fleet import rebuild_fleet


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


if __name__ == "__main__":
    unittest.main()
