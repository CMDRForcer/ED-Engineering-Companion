import unittest

from ed_companion.journal import project_vehicle_state, vehicle_display_name


class VehicleStateTests(unittest.TestCase):
    def test_rhino_restock_launch_and_dock_are_projected(self):
        events = [
            {
                "timestamp": "2026-09-02T17:27:06Z",
                "event": "RestockVehicle",
                "Type": "mev_rhino",
                "Type_Localised": "SRV Rhino",
                "Loadout": "base",
                "Count": 1,
                "ID": 42,
            },
            {
                "timestamp": "2026-09-02T18:05:02Z",
                "event": "LaunchSRV",
                "SRVType": "mev_rhino",
                "SRVType_Localised": "SRV Rhino",
                "Loadout": "base",
                "PlayerControlled": True,
                "ID": 42,
            },
            {
                "timestamp": "2026-09-02T18:30:07Z",
                "event": "DockSRV",
                "SRVType": "mev_rhino",
                "SRVType_Localised": "SRV Rhino",
                "ID": 42,
            },
        ]

        state = project_vehicle_state(events)

        self.assertTrue(state["known"])
        self.assertFalse(state["deployed"])
        self.assertEqual(state["current"], {})
        self.assertEqual(state["lastEvent"], "DockSRV")
        self.assertEqual(len(state["vehicles"]), 1)
        self.assertEqual(state["vehicles"][0]["type"], "mev_rhino")
        self.assertEqual(state["vehicles"][0]["name"], "SRV Rhino")
        self.assertEqual(state["vehicles"][0]["observedCount"], 1)

    def test_launch_marks_exact_vehicle_as_current(self):
        state = project_vehicle_state([{
            "timestamp": "2026-09-02T18:05:02Z",
            "event": "LaunchSRV",
            "SRVType": "mev_rhino",
            "Loadout": "base",
            "PlayerControlled": True,
            "ID": 7,
        }])

        self.assertTrue(state["deployed"])
        self.assertEqual(state["current"]["id"], "7")
        self.assertTrue(state["current"]["playerControlled"])

    def test_unknown_vehicle_uses_frontier_identifier_without_guessing(self):
        self.assertEqual(
            vehicle_display_name("combat_multicrew_srv_01", ""),
            "Scorpion (SRV)",
        )
        self.assertEqual(vehicle_display_name("lander01", ""), "Nomad")
        self.assertEqual(vehicle_display_name("testbuggy", ""), "SRV Scarab")
        self.assertEqual(
            vehicle_display_name("future_vehicle", ""), "future_vehicle"
        )

    def test_hangar_module_does_not_create_vehicle_inventory(self):
        state = project_vehicle_state([{
            "event": "Loadout",
            "Modules": [{
                "Slot": "FighterBay01",
                "Item": "int_mkiilargebuggybay_size4_class3",
            }],
        }])

        self.assertFalse(state["known"])
        self.assertEqual(state["vehicles"], [])

    def test_fighter_restock_is_not_projected_as_srv(self):
        state = project_vehicle_state([
            {
                "event": "RestockVehicle",
                "Type": "empire_fighter",
                "Type_Localised": "Gu-97",
                "ID": 10,
            },
            {
                "event": "RestockVehicle",
                "Type": "independent_fighter",
                "Type_Localised": "Taipan",
                "ID": 11,
            },
        ])

        self.assertFalse(state["known"])
        self.assertEqual(state["vehicles"], [])

    def test_unknown_restock_is_promoted_only_after_srv_lifecycle_proof(self):
        state = project_vehicle_state([
            {
                "timestamp": "2026-09-02T10:00:00Z",
                "event": "RestockVehicle",
                "Type": "future_srv",
                "Type_Localised": "Future SRV",
                "Loadout": "base",
                "Count": 1,
                "ID": 99,
            },
            {
                "timestamp": "2026-09-02T10:05:00Z",
                "event": "LaunchSRV",
                "SRVType": "future_srv",
                "SRVType_Localised": "Future SRV",
                "PlayerControlled": True,
                "ID": 99,
            },
        ])

        self.assertTrue(state["known"])
        self.assertEqual(state["current"]["type"], "future_srv")
        self.assertEqual(state["current"]["observedCount"], 1)


if __name__ == "__main__":
    unittest.main()
