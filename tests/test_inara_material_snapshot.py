from datetime import datetime, timezone
import unittest

from ed_companion.integrations.inara import prepare_journal_batch


class MaterialSnapshotTests(unittest.TestCase):
    def events(self):
        return [
            {"event": "Fileheader", "gameversion": "4.2.0.0"},
            {"event": "LoadGame", "FID": "TEST", "Commander": "Test",
             "Odyssey": True, "timestamp": "2026-09-04T10:00:00Z"},
            {"event": "Materials", "timestamp": "2026-09-04T10:00:01Z",
             "Raw": [{"Name": "iron", "Count": 5}],
             "Manufactured": [], "Encoded": []},
            {"event": "FSDJump", "timestamp": "2026-09-04T10:01:00Z",
             "StarSystem": "Sol", "StarPos": [0, 0, 0]},
            {"event": "MaterialCollected", "timestamp": "2026-09-04T10:02:00Z",
             "Name": "iron", "Count": 2},
        ]

    def prepare(self, events, known=(), limit=50):
        return prepare_journal_batch(
            events, known, "TEST", datetime(2026, 9, 5, tzinfo=timezone.utc),
            max_events=limit,
        )[1:]

    def test_latest_inventory_precedes_backlog_even_with_one_slot(self):
        events, fingerprints = self.prepare(self.events(), limit=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eventName"], "setCommanderInventoryMaterials")
        self.assertEqual(events[0]["eventTimestamp"], "2026-09-04T10:02:00Z")
        self.assertEqual(events[0]["eventData"], [{"itemName": "iron", "itemCount": 7}])
        remaining, _ = self.prepare(self.events(), fingerprints)
        self.assertFalse(any(e["eventName"] == "setCommanderInventoryMaterials" for e in remaining))
        self.assertTrue(any(e["eventName"] == "addCommanderTravelFSDJump" for e in remaining))

    def test_new_observation_of_same_stock_updates_date_once(self):
        source = self.events()
        _, known = self.prepare(source)
        source.append({"event": "Materials", "timestamp": "2026-09-05T00:01:00Z",
                       "Raw": [{"Name": "iron", "Count": 7}],
                       "Manufactured": [], "Encoded": []})
        events, fingerprints = self.prepare(source, known)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["eventTimestamp"], "2026-09-05T00:01:00Z")
        self.assertEqual(self.prepare(source, known + fingerprints)[0], [])

    def test_zero_capacity_and_foreign_profile_do_not_send(self):
        self.assertEqual(self.prepare(self.events(), limit=0)[0], [])
        source = self.events()
        source[1]["FID"] = "OTHER"
        self.assertEqual(self.prepare(source)[0], [])
