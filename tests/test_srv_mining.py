import unittest
from datetime import datetime, timedelta, timezone

from ed_companion.integrations.eddn import supports_event
from ed_companion.integrations.inara import prepare_journal_batch
from ed_companion.journal import (
    mining_commodity_display_name,
    project_latest_srv_mining_session,
)


MINERALS = (
    ("$haematite_name;", "Haematite"),
    ("$samarium_name;", "Samarium"),
    ("$thortveitite_name;", "Thortveitite"),
)


class SrvMiningTests(unittest.TestCase):
    def test_latest_rhino_trip_separates_refined_cargo_from_materials(self):
        events = [{
            "timestamp": "2026-09-02T18:05:02Z", "event": "LaunchSRV",
            "SRVType": "mev_rhino", "SRVType_Localised": "SRV Rhino",
            "ID": 42,
        }]
        events.extend({
            "timestamp": "2026-09-02T18:10:00Z", "event": "MiningRefined",
            "Type": symbol, "Type_Localised": label,
        } for symbol, label in MINERALS)
        events.extend([
            {
                "timestamp": "2026-09-02T18:11:00Z",
                "event": "MaterialCollected", "Name": "tin",
                "Category": "Raw", "Count": 2,
            },
            {
                "timestamp": "2026-09-02T18:30:07Z", "event": "DockSRV",
                "SRVType": "mev_rhino", "ID": 42,
            },
            {
                "timestamp": "2026-09-02T18:31:00Z", "event": "MiningRefined",
                "Type": "$future_ore_name;",
            },
        ])

        state = project_latest_srv_mining_session(events)

        self.assertFalse(state["active"])
        self.assertEqual(
            {row["id"] for row in state["refinedMinerals"]},
            {symbol for symbol, _label in MINERALS},
        )
        self.assertEqual(state["engineeringMaterials"], [{
            "id": "tin", "name": "tin", "count": 2,
        }])

    def test_mineral_resolver_preserves_future_unknown_symbol(self):
        for symbol, label in MINERALS:
            self.assertEqual(mining_commodity_display_name(symbol), label)
        self.assertEqual(
            mining_commodity_display_name("$future_ore_name;"),
            "$future_ore_name;",
        )

    def test_inara_cargo_snapshot_normalizes_observed_mineral_ids(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        timestamp = now.isoformat().replace("+00:00", "Z")
        events = [
            {"timestamp": timestamp, "event": "Fileheader", "gameversion": "4.2.0.0", "Odyssey": True},
            {"timestamp": timestamp, "event": "LoadGame", "Commander": "Test", "FID": "F0000000"},
            {"timestamp": timestamp, "event": "Cargo", "Vessel": "Ship", "Inventory": []},
        ] + [
            {"timestamp": timestamp, "event": "MiningRefined", "Type": symbol}
            for symbol, _label in MINERALS
        ]

        _identity, prepared, _fingerprints = prepare_journal_batch(
            events, now=now, max_events=None
        )
        cargo = [
            row for row in prepared
            if row["eventName"] == "setCommanderInventoryCargo"
        ][-1]["eventData"]

        self.assertEqual(
            {row["itemName"] for row in cargo},
            {"haematite", "samarium", "thortveitite"},
        )
        self.assertTrue(all(row["itemCount"] == 1 for row in cargo))

    def test_market_sell_removes_wrapped_mining_refined_cargo(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        timestamp = now.isoformat().replace("+00:00", "Z")
        refined_at = (now + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        sold_at = (now + timedelta(seconds=2)).isoformat().replace(
            "+00:00", "Z"
        )
        events = [
            {"timestamp": timestamp, "event": "Fileheader", "gameversion": "4.2.0.0", "Odyssey": True},
            {"timestamp": timestamp, "event": "LoadGame", "Commander": "Test", "FID": "F0000000"},
            {"timestamp": timestamp, "event": "Cargo", "Vessel": "Ship", "Inventory": []},
            {"timestamp": refined_at, "event": "MiningRefined", "Type": "$haematite_name;"},
            {"timestamp": sold_at, "event": "MarketSell", "Type": "haematite", "Count": 1},
        ]

        _identity, prepared, _fingerprints = prepare_journal_batch(
            events, now=now, max_events=None
        )
        cargo = [
            row for row in prepared
            if row["eventName"] == "setCommanderInventoryCargo"
        ]

        self.assertEqual(cargo[-2]["eventData"], [{
            "itemName": "haematite", "itemCount": 1, "isStolen": False,
        }])
        self.assertEqual(cargo[-1]["eventData"], [])

    def test_later_empty_cargo_snapshot_is_not_deduped_by_old_empty_state(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        timestamp = now.isoformat().replace("+00:00", "Z")
        later = (now + timedelta(seconds=3)).isoformat().replace(
            "+00:00", "Z"
        )
        events = [
            {"timestamp": timestamp, "event": "Fileheader", "gameversion": "4.2.0.0", "Odyssey": True},
            {"timestamp": timestamp, "event": "LoadGame", "Commander": "Test", "FID": "F0000000"},
            {"timestamp": timestamp, "event": "Cargo", "Vessel": "Ship", "Inventory": []},
            {"timestamp": later, "event": "Cargo", "Vessel": "Ship", "Inventory": []},
        ]

        _identity, prepared, _fingerprints = prepare_journal_batch(
            events, now=now, max_events=None
        )
        cargo = [
            row for row in prepared
            if row["eventName"] == "setCommanderInventoryCargo"
        ]

        self.assertEqual(len(cargo), 2)
        self.assertNotEqual(cargo[0]["eventTimestamp"], cargo[1]["eventTimestamp"])

    def test_authoritative_itemized_cargo_replaces_mining_delta_replay(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        timestamp = now.isoformat().replace("+00:00", "Z")
        later = (now + timedelta(seconds=3)).isoformat().replace(
            "+00:00", "Z"
        )
        events = [
            {"timestamp": timestamp, "event": "Fileheader", "gameversion": "4.2.0.0", "Odyssey": True},
            {"timestamp": timestamp, "event": "LoadGame", "Commander": "Test", "FID": "F0000000"},
            {"timestamp": timestamp, "event": "Cargo", "Inventory": []},
            {"timestamp": timestamp, "event": "MiningRefined", "Type": "$haematite_name;"},
            {"timestamp": later, "event": "Cargo", "Inventory": [{"Name": "drones", "Count": 4}]},
        ]

        _identity, prepared, _fingerprints = prepare_journal_batch(
            events, now=now, max_events=None
        )
        cargo = [
            row for row in prepared
            if row["eventName"] == "setCommanderInventoryCargo"
        ][-1]["eventData"]

        self.assertEqual(cargo, [{
            "itemName": "drones", "itemCount": 4, "isStolen": False,
        }])

    def test_mining_events_remain_outside_eddn_supported_contract(self):
        self.assertFalse(supports_event({"event": "MiningRefined"}))
        self.assertFalse(supports_event({"event": "Cargo"}))


if __name__ == "__main__":
    unittest.main()
