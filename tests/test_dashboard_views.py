import unittest

from ed_companion.phase14.dashboard_views import (
    build_commander_cards,
    build_finance_history,
    build_finance_summary,
    filter_finance_history,
    build_logbook_view,
)
from ed_companion.phase14.state import commander_journal_overview


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

    def test_finance_history_uses_only_authoritative_snapshots(self):
        rows = build_finance_history([
            {"event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z", "Credits": 100},
            {"event": "MarketBuy", "timestamp": "2026-01-01T10:01:00Z", "TotalCost": 20},
            {"event": "Statistics", "timestamp": "2026-01-01T10:02:00Z", "Bank_Account": {"Current_Wealth": 500}},
            {"event": "LoadGame", "timestamp": "2026-01-02T10:00:00Z", "Credits": 125},
        ])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {
            "timestamp": "2026-01-01T10:00:00Z", "credits": 100, "assets": -1,
            "source": "session_start",
        })
        self.assertEqual(rows[-1]["credits"], 125)
        self.assertEqual(rows[-1]["assets"], 500)
        self.assertEqual(rows[1]["source"], "asset_snapshot")

    def test_live_status_balance_overrides_session_start_credits(self):
        overview = commander_journal_overview(
            [{
                "event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z",
                "Credits": 100,
            }],
            {"timestamp": "2026-01-01T10:05:00Z", "Balance": 145},
        )

        self.assertEqual(overview["credits"], {
            "value": 145,
            "known": True,
            "timestamp": "2026-01-01T10:05:00Z",
            "basis": "LIVE STATUS",
        })

    def test_stale_status_balance_does_not_override_newer_load_game(self):
        overview = commander_journal_overview(
            [{
                "event": "LoadGame", "timestamp": "2026-01-01T10:05:00Z",
                "Credits": 145,
            }],
            {"timestamp": "2026-01-01T10:00:00Z", "Balance": 100},
        )

        self.assertEqual(overview["credits"]["value"], 145)
        self.assertEqual(overview["credits"]["basis"], "SESSION START")

    def test_finance_history_appends_changed_live_balance(self):
        rows = build_finance_history(
            [{
                "event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z",
                "Credits": 100,
            }],
            current_credits={
                "known": True, "value": 145,
                "timestamp": "2026-01-01T10:05:00Z",
            },
        )

        self.assertEqual(rows[-1], {
            "timestamp": "2026-01-01T10:05:00Z",
            "credits": 145,
            "assets": -1,
            "source": "live_balance",
        })

    def test_finance_history_merges_persisted_balances_in_time_order(self):
        rows = build_finance_history(
            [{
                "event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z",
                "Credits": 100,
            }],
            current_credits={
                "known": True, "value": 180,
                "timestamp": "2026-01-01T10:03:00Z",
            },
            credit_snapshots=[
                {
                    "timestamp": "2026-01-01T10:02:00Z", "credits": 140,
                    "source": "live_balance",
                },
                {
                    "timestamp": "2026-01-01T10:01:00Z", "credits": 120,
                    "source": "live_balance",
                },
                {
                    "timestamp": "2026-01-01T10:03:00Z", "credits": 180,
                    "source": "live_balance",
                },
            ],
        )

        self.assertEqual(
            [row["credits"] for row in rows],
            [100, 120, 140, 180],
        )
        self.assertEqual(
            [row["timestamp"] for row in rows],
            [
                "2026-01-01T10:00:00Z", "2026-01-01T10:01:00Z",
                "2026-01-01T10:02:00Z", "2026-01-01T10:03:00Z",
            ],
        )

    def test_finance_summary_uses_real_elapsed_time(self):
        summary = build_finance_summary([
            {
                "timestamp": "2026-01-01T10:00:00Z",
                "credits": 1000, "assets": -1,
            },
            {
                "timestamp": "2026-01-01T12:30:00Z",
                "credits": 6000, "assets": -1,
            },
        ])

        self.assertTrue(summary["known"])
        self.assertTrue(summary["rateKnown"])
        self.assertEqual(summary["durationSeconds"], 9000)
        self.assertEqual(summary["change"], 5000)
        self.assertEqual(summary["averagePerHour"], 2000.0)

    def test_finance_summary_preserves_negative_credit_rate(self):
        summary = build_finance_summary([
            {"timestamp": "2026-01-01T10:00:00Z", "credits": 5000},
            {"timestamp": "2026-01-01T11:00:00Z", "credits": 3000},
        ])

        self.assertEqual(summary["change"], -2000)
        self.assertEqual(summary["averagePerHour"], -2000.0)

    def test_finance_history_filters_fixed_period_with_boundary_anchor(self):
        rows = [
            {"timestamp": "2026-01-01T10:00:00Z", "credits": 1000},
            {"timestamp": "2026-01-01T12:00:00Z", "credits": 3000},
        ]

        filtered = filter_finance_history(rows, "1h")

        self.assertEqual(filtered, [
            {
                "timestamp": "2026-01-01T11:00:00Z", "credits": 1000,
                "source": "period_anchor",
            },
            rows[1],
        ])

    def test_finance_history_filters_current_session_from_latest_load_game(self):
        rows = [
            {"timestamp": "2026-01-01T10:00:00Z", "credits": 1000},
            {"timestamp": "2026-01-02T10:00:00Z", "credits": 2000},
            {"timestamp": "2026-01-02T11:00:00Z", "credits": 3500},
        ]
        events = [
            {"event": "LoadGame", "timestamp": "2026-01-01T10:00:00Z"},
            {"event": "LoadGame", "timestamp": "2026-01-02T10:00:00Z"},
        ]

        self.assertEqual(
            filter_finance_history(rows, "session", events),
            rows[1:],
        )


if __name__ == "__main__":
    unittest.main()
