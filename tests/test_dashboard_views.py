import unittest

from ed_companion.phase14.dashboard_views import (
    build_commander_cards,
    build_logbook_view,
)


class DashboardViewTests(unittest.TestCase):
    def test_commander_cards_preserve_latest_journal_projection(self):
        overview = {
            "ranks": [{
                "label": "COMBAT", "rank": 5, "known": True,
                "progress": 42, "progressKnown": True,
            }],
            "reputations": [{
                "label": "FEDERATION", "value": 75, "known": True,
            }],
            "credits": {"known": True, "value": 1234, "timestamp": "now"},
            "assets": {"known": False},
        }
        events = [
            {"event": "Loadout", "Ship": "Krait_MkII", "ShipName": "EDEC"},
            {
                "event": "Location", "StarSystem": "Cubeo",
                "StationName": "Chelomey Orbital",
                "Factions": [{"Name": "Cubeo Patron's Principles", "MyReputation": 42.5}],
            },
            {"event": "SquadronStartup", "SquadronName": "Test Wing", "CurrentRank": "Pilot"},
        ]

        cards = build_commander_cards(overview, events)

        self.assertEqual(cards["ranks"]["rows"][0]["value"], "RANK 5")
        self.assertEqual(cards["current-ship"]["rows"][0]["value"], "EDEC")
        self.assertEqual(
            cards["current-ship"]["rows"][0]["detail"],
            "Cubeo · Chelomey Orbital",
        )
        self.assertEqual(
            cards["minor-reputation"]["rows"][0]["value"], "42.5%",
        )
        self.assertEqual(cards["squadron"]["rows"][0]["detail"], "Pilot")

    def test_logbook_view_decorates_notes_before_filtering(self):
        rows = [
            {"id": "one", "category": "TRAVEL", "searchText": "cubeo"},
            {"id": "two", "category": "DOCKING", "searchText": "rhea"},
        ]

        result = build_logbook_view(
            rows, {"one": "Prismatic shields"}, "TRAVEL", "shields",
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "one")
        self.assertEqual(result[0]["note"], "Prismatic shields")
        self.assertIn("prismatic shields", result[0]["searchText"])


if __name__ == "__main__":
    unittest.main()
