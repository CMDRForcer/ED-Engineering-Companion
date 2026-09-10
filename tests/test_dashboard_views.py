import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from ed_companion.phase14.dashboard_views import (
    build_commander_cards,
    build_finance_history,
    build_finance_summary,
    filter_finance_history,
    build_logbook_view,
)
from ed_companion.phase14.state import (
    commander_journal_overview,
    merge_capi_commander_overview,
    merge_capi_fleet,
)
from ed_companion.phase14.controller import CockpitController


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

    def test_capi_credits_supplement_but_never_replace_newer_local_credits(self):
        local = commander_journal_overview([{
            "event": "LoadGame", "timestamp": "2026-01-01T10:05:00Z",
            "Credits": 145,
        }])
        older_capi = {
            "credits": {
                "known": True, "value": 100,
                "timestamp": "2026-01-01T10:00:00Z",
            },
        }
        newer_capi = {
            "credits": {
                "known": True, "value": 175,
                "timestamp": "2026-01-01T10:10:00Z",
            },
        }

        preserved = merge_capi_commander_overview(local, older_capi)
        supplemented = merge_capi_commander_overview(local, newer_capi)

        self.assertEqual(preserved["credits"]["value"], 145)
        self.assertEqual(preserved["credits"]["basis"], "SESSION START")
        self.assertEqual(supplemented["credits"]["value"], 175)
        self.assertEqual(supplemented["credits"]["basis"], "FRONTIER CAPI")

    def test_capi_current_ship_never_deletes_or_downgrades_journal_fleet(self):
        journal_fleet = {
            "active_id": "7",
            "ships": [
                {
                    "id": "7", "type": "Krait Mk II", "name": "Mechthild",
                    "value": 900, "observedAt": "2026-01-01T10:05:00Z",
                    "status": "active", "isCurrent": True,
                },
                {
                    "id": "9", "type": "Fer-de-Lance", "name": "Signe",
                    "observedAt": "2026-01-01T09:00:00Z",
                    "status": "remote", "isCurrent": False,
                },
            ],
        }
        older_capi = {
            "activeShip": {
                "known": True, "id": "7", "type": "Krait_MkII",
                "name": "stale", "value": 100,
                "observedAt": "2026-01-01T10:00:00Z",
            },
        }

        merged = merge_capi_fleet(journal_fleet, older_capi)
        rows = {row["id"]: row for row in merged["ships"]}

        self.assertEqual(set(rows), {"7", "9"})
        self.assertEqual(rows["7"]["name"], "Mechthild")
        self.assertEqual(rows["7"]["value"], 900)
        self.assertEqual(merged["active_id"], "7")

    def test_capi_can_add_a_newer_current_ship_without_erasing_fleet(self):
        merged = merge_capi_fleet({
            "active_id": "7",
            "ships": [{
                "id": "7", "type": "Krait Mk II", "name": "Mechthild",
                "observedAt": "2026-01-01T10:00:00Z",
                "status": "active", "isCurrent": True,
            }],
        }, {
            "activeShip": {
                "known": True, "id": "11", "type": "Panther Clipper Mk II",
                "name": "Hauler", "value": 1000,
                "observedAt": "2026-01-01T10:10:00Z",
            },
        })
        rows = {row["id"]: row for row in merged["ships"]}

        self.assertEqual(set(rows), {"7", "11"})
        self.assertEqual(merged["active_id"], "11")
        self.assertEqual(rows["7"]["status"], "stored")
        self.assertTrue(rows["11"]["isCurrent"])

    def test_capi_roster_adds_stored_ships_with_location_without_deleting(self):
        journal_fleet = {
            "active_id": "7",
            "ships": [{
                "id": "7", "type": "Krait Mk II", "name": "Mechthild",
                "value": 900, "observedAt": "2026-01-01T10:05:00Z",
                "status": "active", "isCurrent": True,
            }, {
                "id": "9", "type": "Fer-de-Lance", "name": "Signe",
                "observedAt": "2026-01-01T09:00:00Z",
                "status": "remote", "isCurrent": False,
            }],
        }
        capi = {
            "activeShip": {
                "known": True, "id": "7", "type": "Krait_MkII",
                "name": "stale", "value": 100,
                "observedAt": "2026-01-01T10:00:00Z",
            },
            "fleet": [
                {"id": "9", "type": "Fer-de-Lance", "systemName": "Deciat",
                 "stationName": "Garay Terminal", "value": 50,
                 "observedAt": "2026-01-01T11:00:00Z"},
                {"id": "12", "type": "Type-10 Defender", "name": "Anvil",
                 "systemName": "Leesti", "stationName": "George Lucas",
                 "value": 77, "observedAt": "2026-01-01T11:00:00Z"},
            ],
        }

        merged = merge_capi_fleet(journal_fleet, capi)
        rows = {row["id"]: row for row in merged["ships"]}

        self.assertEqual(set(rows), {"7", "9", "12"})
        self.assertEqual(rows["7"]["name"], "Mechthild")
        self.assertEqual(rows["9"]["systemName"], "Deciat")
        self.assertEqual(rows["9"]["value"], 50)
        self.assertEqual(rows["9"]["name"], "Signe")
        self.assertEqual(rows["12"]["type"], "Type-10 Defender")
        self.assertEqual(rows["12"]["stationName"], "George Lucas")
        self.assertEqual(rows["12"]["source"], "frontier_capi")
        self.assertFalse(rows["12"]["isCurrent"])

    def test_capi_ranks_fill_only_unknown_rows_and_never_downgrade(self):
        local = {
            "ranks": [
                {"key": "Combat", "known": True, "rank": 8},
                {"key": "Trade", "known": False, "rank": -1},
                {"key": "Exobiologist", "known": False, "rank": -1},
            ],
            "credits": {"known": True, "value": 5, "timestamp": "2026-01-02T00:00:00Z"},
        }
        merged = merge_capi_commander_overview(local, {
            "credits": {"known": False},
            "ranks": {"combat": 2, "trade": 6, "exobiologist": 4},
            "activeShip": {"value": 2000000, "observedAt": "2026-01-02T00:00:00Z"},
        })
        rows = {row["key"]: row for row in merged["ranks"]}

        self.assertEqual(rows["Combat"]["rank"], 8)
        self.assertNotIn("rankBasis", rows["Combat"])
        self.assertTrue(rows["Trade"]["known"])
        self.assertEqual(rows["Trade"]["rank"], 6)
        self.assertEqual(rows["Trade"]["rankBasis"], "FRONTIER CAPI")
        self.assertEqual(rows["Exobiologist"]["rank"], 4)
        self.assertEqual(merged["shipValue"]["rebuy"], 100000)
        self.assertEqual(merged["shipValue"]["basis"], "FRONTIER CAPI")

    def test_cached_capi_profile_survives_a_journal_state_rebuild(self):
        refreshed = CockpitController._state_with_frontier_profile({
            "commanderOverview": {
                "credits": {
                    "known": True, "value": 100,
                    "timestamp": "2026-01-01T10:00:00Z",
                },
            },
            "fleet": [],
            "activeShipId": "",
        }, {
            "credits": {
                "known": True, "value": 175,
                "timestamp": "2026-01-01T10:10:00Z",
            },
            "activeShip": {
                "known": True, "id": "11", "type": "Panther Clipper Mk II",
                "name": "Hauler", "observedAt": "2026-01-01T10:10:00Z",
            },
        })

        self.assertEqual(
            refreshed["commanderOverview"]["credits"]["value"], 175
        )
        self.assertEqual(refreshed["activeShipId"], "11")
        self.assertEqual(len(refreshed["fleet"]), 1)

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


class _FakeTokens:
    access_token = "access"
    refresh_token = "refresh"
    token_type = "Bearer"

    def expires_within(self, _seconds, *, now=None):
        return False


class FrontierRequestResilienceTests(unittest.TestCase):
    def _controller(self):
        controller = CockpitController.__new__(CockpitController)
        controller._frontier_busy = False
        controller._frontier_request_token = 0
        controller._profile_generation = 1
        controller._frontier_tokens = _FakeTokens()
        controller._frontier_client = Mock()
        controller._frontier_authorization = None
        controller._frontier_status = ""
        controller._frontier_last_sync = ""
        controller._frontier_watchdog = Mock()
        controller._frontier_credential_store = Mock()
        controller.connectionChanged = Mock()
        controller.frontierFinished = Mock()
        return controller

    def test_unexpected_worker_error_releases_the_busy_state_without_leaking(self):
        controller = self._controller()
        controller._frontier_client.query.side_effect = RuntimeError(
            "boom token=SUPERSECRET"
        )
        workers = []
        controller._start_network_worker = (
            lambda target, _name: workers.append(target) or True
        )

        controller._start_frontier_profile_request(
            tokens=controller._frontier_tokens
        )
        self.assertTrue(controller._frontier_busy)
        controller._frontier_watchdog.start.assert_called_once()

        workers[0]()
        payload = controller.frontierFinished.emit.call_args.args[0]
        self.assertEqual(payload["profile"], {})
        self.assertIn("RuntimeError", payload["error"])
        self.assertNotIn("SUPERSECRET", payload["error"])

        controller._finish_frontier(payload)
        self.assertFalse(controller._frontier_busy)
        controller._frontier_watchdog.stop.assert_called_once()
        self.assertNotIn("SUPERSECRET", controller._frontier_status)
        self.assertNotIn("TIMED OUT", controller._frontier_status)

    def test_watchdog_releases_a_request_that_never_reports(self):
        controller = self._controller()
        controller._frontier_busy = True
        controller._frontier_authorization = object()
        controller._frontier_status = "CONTACTING FRONTIER…"

        controller._frontier_request_timed_out()

        self.assertFalse(controller._frontier_busy)
        self.assertIsNone(controller._frontier_authorization)
        self.assertIn("TIMED OUT", controller._frontier_status)
        controller.connectionChanged.emit.assert_called_once()

    def test_watchdog_expiry_is_a_noop_once_a_result_arrived(self):
        controller = self._controller()
        controller._frontier_busy = False
        controller._frontier_status = "CONNECTED · COMMANDER PROFILE UPDATED"

        controller._frontier_request_timed_out()

        self.assertEqual(
            controller._frontier_status, "CONNECTED · COMMANDER PROFILE UPDATED"
        )
        controller.connectionChanged.emit.assert_not_called()

    def test_worker_that_cannot_start_does_not_arm_the_watchdog(self):
        controller = self._controller()
        controller._start_network_worker = lambda _target, _name: False

        controller._start_frontier_profile_request(
            tokens=controller._frontier_tokens
        )

        self.assertFalse(controller._frontier_busy)
        controller._frontier_watchdog.start.assert_not_called()
        self.assertEqual(
            controller._frontier_status, "FRONTIER REQUEST COULD NOT START"
        )


class FrontierConsentTests(unittest.TestCase):
    def _controller(self, directory):
        controller = CockpitController.__new__(CockpitController)
        controller.frontier_config_file = (
            Path(directory) / "frontier_config.json"
        )
        controller._frontier_config = controller._load_frontier_config()
        controller._frontier_busy = False
        controller._frontier_authorization = None
        controller._frontier_status = ""
        controller.connectionChanged = Mock()
        return controller

    def test_connect_is_refused_until_consent_is_recorded(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)

            controller.connectFrontier()

            self.assertIn("CONSENT REQUIRED", controller._frontier_status)
            self.assertIsNone(controller._frontier_authorization)
            controller.connectionChanged.emit.assert_called_once()

    def test_consent_choice_persists_for_the_next_start(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)

            controller.setFrontierConsent(True)
            self.assertTrue(controller._frontier_config["consent"])
            self.assertTrue(controller.frontier_config_file.exists())

            restarted = self._controller(directory)
            self.assertTrue(restarted._frontier_config["consent"])

    def test_withdrawing_consent_drops_a_pending_login(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            controller.setFrontierConsent(True)
            controller._frontier_authorization = object()

            controller.setFrontierConsent(False)

            self.assertFalse(controller._frontier_config["consent"])
            self.assertIsNone(controller._frontier_authorization)
            self.assertIn("CONSENT WITHDRAWN", controller._frontier_status)


if __name__ == "__main__":
    unittest.main()
