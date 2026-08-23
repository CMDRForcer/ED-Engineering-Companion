import unittest

from ed_companion.phase14.session_views import (
    apply_session_event,
    normalize_session_history,
    public_session,
)


class SessionViewTests(unittest.TestCase):
    def test_journal_events_preserve_all_session_counters(self):
        events = [
            {"event": "LoadGame", "timestamp": "2026-08-23T10:00:00Z"},
            {"event": "FSDJump", "timestamp": "2026-08-23T10:01:00Z", "StarSystem": "Cubeo", "JumpDist": 12.345},
            {"event": "Docked", "timestamp": "2026-08-23T10:02:00Z", "StarSystem": "Cubeo"},
            {"event": "EngineerCraft", "timestamp": "2026-08-23T10:03:00Z", "Level": 5, "ExperimentalEffect": "special"},
            {"event": "MaterialTrade", "timestamp": "2026-08-23T10:04:00Z"},
            {"event": "MaterialCollected", "timestamp": "2026-08-23T10:05:00Z"},
        ]
        current = None
        history = []
        for index, event in enumerate(events):
            current = apply_session_event(current, history, event, index)

        result = public_session(current, "2026-08-23T10:10:00Z")

        self.assertEqual(result["durationSeconds"], 600)
        self.assertEqual(result["fsdJumps"], 1)
        self.assertEqual(result["distanceLy"], 12.35)
        self.assertEqual(result["dockings"], 1)
        self.assertEqual(result["engineerCrafts"], 1)
        self.assertEqual(result["gradeCrafts"], 1)
        self.assertEqual(result["experimentalCrafts"], 1)
        self.assertEqual(result["materialTrades"], 1)
        self.assertEqual(result["materialCollectedEvents"], 1)
        self.assertEqual(result["visitedSystems"], 1)

    def test_shutdown_moves_current_session_to_history(self):
        history = []
        current = apply_session_event(None, history, {
            "event": "LoadGame", "timestamp": "2026-08-23T10:00:00Z",
        }, 0)
        current = apply_session_event(current, history, {
            "event": "Shutdown", "timestamp": "2026-08-23T10:30:00Z",
        }, 1)

        self.assertIsNone(current)
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["active"])
        self.assertEqual(history[0]["durationSeconds"], 1800)

    def test_persisted_history_is_bounded_and_sanitized(self):
        payload = [{
            "id": str(index), "durationSeconds": -5, "distanceLy": "bad",
            "fsdJumps": -1,
        } for index in range(35)]

        rows = normalize_session_history(payload)

        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[0]["id"], "5")
        self.assertEqual(rows[0]["durationSeconds"], 0)
        self.assertEqual(rows[0]["distanceLy"], 0.0)
        self.assertEqual(rows[0]["fsdJumps"], 0)


if __name__ == "__main__":
    unittest.main()
