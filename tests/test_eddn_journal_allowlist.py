import json
from pathlib import Path
import unittest

from ed_companion.integrations.eddn import (
    EddnError,
    EDDN_SCHEMA_VALIDATED_AT,
    JOURNAL_ALLOWED_BY_EVENT,
    envelope,
    prepare_event,
    validate_prepared,
)
from ed_companion.phase14.controller import CockpitController


class _Signal:
    def emit(self, *_args):
        pass


class EddnJournalAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "StarSystem": "Public System",
            "StarPos": [1.0, 2.0, 3.0],
            "SystemAddress": 123456,
            "gameversion": "4.2.0.0",
            "gamebuild": "r0",
            "schema_environment": "live",
            "odyssey": True,
        }

    def _queue(self, prepared):
        controller = CockpitController.__new__(CockpitController)
        controller._eddn_queue = []
        controller._eddn_context = dict(self.context)
        controller._eddn_profile_key = "allowlist-profile"
        controller._eddn_status = ""
        controller.connectionChanged = _Signal()
        controller._save_eddn = lambda: None
        controller._publish_eddn_delivery_change = lambda: None
        self.assertTrue(CockpitController._enqueue_eddn(controller, prepared))
        return controller._eddn_queue[0]

    def test_journal_allowlist_strips_future_and_nested_private_fields_everywhere(self):
        secret = "must-never-leave"
        prepared = prepare_event({
            "timestamp": "2026-08-30T10:00:00Z",
            "event": "FSDJump",
            "StarSystem": "Public System",
            "StarPos": [1.0, 2.0, 3.0],
            "SystemAddress": 123456,
            "Body": "Public Star",
            "Population": 42,
            "Commander": secret,
            "FID": secret,
            "FuturePrivateField": secret,
            "SystemFaction": {
                "Name": "Public Faction",
                "Commander": secret,
                "FuturePrivateField": secret,
            },
            "FuturePrivateObject": {
                "account": secret,
                "PrivateGroup": secret,
            },
        }, self.context)

        self.assertIsNotNone(prepared)
        self.assertEqual(
            prepared["message"]["SystemFaction"], {"Name": "Public Faction"}
        )
        prepared_json = json.dumps(prepared, sort_keys=True)
        self.assertNotIn(secret, prepared_json)
        self.assertNotIn("FuturePrivate", prepared_json)

        job = self._queue(prepared)
        queued_json = json.dumps(job, sort_keys=True)
        self.assertNotIn(secret, queued_json)
        self.assertNotIn("FuturePrivate", queued_json)

        payload = envelope(prepared, self.context, "anonymous-uploader")
        envelope_json = json.dumps(payload, sort_keys=True)
        self.assertNotIn(secret, envelope_json)
        self.assertNotIn("FuturePrivate", envelope_json)

    def test_journal_allowlist_preserves_reference_public_fields(self):
        prepared = prepare_event({
            "timestamp": "2026-08-30T10:00:00Z",
            "event": "FSDJump",
            "StarSystem": "Public System",
            "StarPos": [1.0, 2.0, 3.0],
            "SystemAddress": 123456,
            "Body": "Public Star",
            "BodyID": 7,
            "Population": 424242,
            "Powers": ["Aisling Duval"],
            "PowerplayState": "Exploited",
        }, self.context)

        self.assertEqual(prepared["message"]["Body"], "Public Star")
        self.assertEqual(prepared["message"]["BodyID"], 7)
        self.assertEqual(prepared["message"]["Population"], 424242)
        self.assertEqual(prepared["message"]["Powers"], ["Aisling Duval"])
        self.assertEqual(prepared["message"]["PowerplayState"], "Exploited")
        self.assertTrue(validate_prepared(prepared))

    def test_useful_observed_astronomy_bgs_and_powerplay_fields_are_preserved(self):
        prepared = prepare_event({
            "timestamp": "2026-08-30T10:00:00Z",
            "event": "FSDJump",
            "StarSystem": "Public System",
            "StarPos": [1.0, 2.0, 3.0],
            "SystemAddress": 123456,
            "BodyType": "Star",
            "ControllingPower": "Public Power",
            "PowerplayConflictProgress": 0.25,
            "PowerplayStateControlProgress": 0.5,
            "PowerplayStateReinforcement": 10,
            "PowerplayStateUndermining": 20,
            "Conflicts": [{
                "WarType": "war", "Status": "active",
                "Faction1": {"Name": "not explicitly allowed"},
                "FutureNestedField": "must-not-survive",
            }],
            "Taxi": True,
        }, self.context)

        message = prepared["message"]
        for field in (
            "BodyType", "ControllingPower", "PowerplayConflictProgress",
            "PowerplayStateControlProgress", "PowerplayStateReinforcement",
            "PowerplayStateUndermining", "Conflicts",
        ):
            self.assertIn(field, message)
        self.assertNotIn("Taxi", message)
        self.assertNotIn("FutureNestedField", json.dumps(message))
        self.assertTrue(validate_prepared(prepared))

    def test_scan_parents_and_footfall_survive_with_explicit_nested_keys(self):
        prepared = prepare_event({
            "timestamp": "2026-08-30T10:00:00Z",
            "event": "Scan",
            "StarSystem": "Public System",
            "StarPos": [1.0, 2.0, 3.0],
            "SystemAddress": 123456,
            "BodyName": "Public System 1 a",
            "BodyID": 4,
            "Parents": [
                {"Planet": 3, "Commander": "must-not-survive"},
                {"Star": 1, "FutureParent": 99},
            ],
            "WasFootfalled": False,
        }, self.context)

        self.assertEqual(
            prepared["message"]["Parents"], [{"Planet": 3}, {"Star": 1}]
        )
        self.assertIs(prepared["message"]["WasFootfalled"], False)
        self.assertTrue(validate_prepared(prepared))

    def test_journal_validation_rejects_unknown_fields_from_old_queue_data(self):
        prepared = {
            "schema": "journal/1",
            "message": {
                "timestamp": "2026-08-30T10:00:00Z",
                "event": "FSDJump",
                "StarSystem": "Public System",
                "StarPos": [1.0, 2.0, 3.0],
                "SystemAddress": 123456,
                "SystemFaction": {
                    "Name": "Public Faction",
                    "FuturePrivateField": "must-never-leave",
                },
            },
        }

        with self.assertRaises(EddnError):
            validate_prepared(prepared)
        with self.assertRaises(EddnError):
            envelope(prepared, self.context, "anonymous-uploader")

    def test_local_allowlists_honor_checked_live_schema_contract(self):
        contract = json.loads((
            Path(__file__).parent / "fixtures" / "eddn_journal1_contract.json"
        ).read_text(encoding="utf-8"))

        self.assertEqual(contract["checked_at"], EDDN_SCHEMA_VALIDATED_AT)
        required = set(contract["required"])
        disallowed = set(contract["explicitly_disallowed"])
        for event, allowed in JOURNAL_ALLOWED_BY_EVENT.items():
            self.assertTrue(required.issubset(allowed), event)
            self.assertTrue(set(allowed).isdisjoint(disallowed), event)
            self.assertIn(event, contract["events"])


if __name__ == "__main__":
    unittest.main()
