from datetime import datetime, timezone
import unittest

from ed_companion.integrations.eddn import (
    prepare_event,
    schema_parity_report,
    update_context,
    validate_prepared,
)
from ed_companion.integrations.inara import (
    INARA_BATCH_WINDOW_SECONDS,
    INARA_MAX_REQUESTS_PER_MINUTE,
    INARA_MIN_REQUEST_INTERVAL_SECONDS,
    MAX_EVENTS,
    prepare_journal_batch,
)
from ed_companion.phase14.state import powerplay_journal_overview


class ReleaseContractTests(unittest.TestCase):
    def test_inara_safety_limits_remain_strict(self):
        self.assertEqual(INARA_MAX_REQUESTS_PER_MINUTE, 2)
        self.assertEqual(INARA_BATCH_WINDOW_SECONDS, 45)
        self.assertEqual(INARA_MIN_REQUEST_INTERVAL_SECONDS, 300)
        self.assertEqual(MAX_EVENTS, 50)

    def test_powerplay_snapshot_uses_only_observed_values(self):
        events = [
            {"timestamp": "2026-08-23T17:00:00Z", "event": "LoadGame"},
            {
                "timestamp": "2026-08-23T17:00:01Z",
                "event": "Powerplay",
                "Power": "Aisling Duval",
                "Rank": 5,
                "Merits": 14230,
                "TimePledged": 1713600,
            },
        ]
        overview = powerplay_journal_overview(events)
        self.assertTrue(overview["pledged"])
        self.assertEqual(overview["power"], "Aisling Duval")
        self.assertEqual(overview["rank"], 5)
        self.assertEqual(overview["merits"], 14230)
        self.assertEqual(overview["timePledgedSeconds"], 1713600)

        unpledged = powerplay_journal_overview([
            {"timestamp": "2026-08-23T17:00:00Z", "event": "LoadGame"}
        ])
        self.assertFalse(unpledged["pledged"])
        self.assertFalse(unpledged["rankKnown"])
        self.assertFalse(unpledged["meritsKnown"])

    def test_scanorganic_contract_filters_private_fields(self):
        context = {}
        for event in (
            {
                "event": "Fileheader",
                "gameversion": "4.2.0.0",
                "build": "r0",
            },
            {"event": "LoadGame", "Horizons": True, "Odyssey": True},
            {
                "event": "Location",
                "StarSystem": "Demo System",
                "SystemAddress": 1000000001,
                "StarPos": [0.0, 0.0, 0.0],
            },
        ):
            context = update_context(context, event)
        prepared = prepare_event({
            "timestamp": "2026-08-23T17:00:02Z",
            "event": "ScanOrganic",
            "ScanType": "Sample",
            "Genus": "$Codex_Ent_Bacterial_Genus_Name;",
            "Genus_Localised": "Bacterium",
            "Species": "$Codex_Ent_Bacterial_01_Name;",
            "Species_Localised": "Bacterium Aurasus",
            "Variant": "$Codex_Ent_Bacterial_01_A_Name;",
            "Variant_Localised": "Bacterium Aurasus Aquamarine",
            "SystemAddress": 1000000001,
            "Body": 4,
        }, context)
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared["schema"], "scanorganic/1")
        self.assertNotIn("Genus_Localised", prepared["message"])
        self.assertEqual(prepared["message"]["BodyID"], 4)
        self.assertTrue(validate_prepared(prepared))

    def test_schema_report_keeps_capi_boundary_honest(self):
        report = schema_parity_report()
        self.assertEqual(
            report["capiRequired"],
            ["blackmarket/1", "fcmaterials_capi/1"],
        )
        self.assertEqual(report["total"], report["supported"] + 2)

    def test_storage_delta_still_uses_authoritative_baseline(self):
        now = datetime.now(timezone.utc)
        timestamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        header = [
            {
                "timestamp": timestamp,
                "event": "Fileheader",
                "gameversion": "4.2.0.0",
                "Odyssey": True,
            },
            {
                "timestamp": timestamp,
                "event": "LoadGame",
                "Commander": "Demo Commander",
                "FID": "F0000000",
            },
            {
                "timestamp": timestamp,
                "event": "Location",
                "StarSystem": "Demo System",
                "StationName": "Demo Station",
                "MarketID": 1000000001,
            },
        ]
        baseline = {
            "timestamp": timestamp,
            "event": "StoredModules",
            "Items": [{
                "Name": "int_cargorack_size2_class1",
                "StorageSlot": 10,
                "BuyPrice": 1000,
                "Hot": False,
                "StarSystem": "Demo System",
                "StationName": "Demo Station",
                "MarketID": 1000000001,
            }],
        }
        _retained, _prepared, known = prepare_journal_batch(
            header + [baseline], now=now, max_events=None
        )
        module_store = {
            "timestamp": timestamp,
            "event": "ModuleStore",
            "MarketID": 1000000001,
            "StoredItem": "hpt_heat_sink_launcher",
            "Ship": "cobra",
            "ShipID": 42,
            "Hot": False,
        }
        _retained, prepared, _fingerprints = prepare_journal_batch(
            header + [baseline, module_store], known, now=now, max_events=None
        )
        snapshots = [
            event for event in prepared
            if event["eventName"] == "setCommanderStorageModules"
        ]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(
            {row["itemName"] for row in snapshots[0]["eventData"]},
            {"int_cargorack_size2_class1", "hpt_heat_sink_launcher"},
        )


if __name__ == "__main__":
    unittest.main()

