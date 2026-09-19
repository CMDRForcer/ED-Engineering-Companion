import unittest

from ed_companion.missions import (
    active_missions,
    community_goals_overview,
    missions_summary,
)


def _accepted(mission_id, **overrides):
    event = {
        "event": "MissionAccepted", "MissionID": mission_id,
        "Name": "Mission_Test", "LocalisedName": "Test Mission",
        "Faction": "Test Faction", "Expiry": "2026-09-20T00:00:00Z",
        "Wing": False,
    }
    event.update(overrides)
    return event


class ActiveMissionsTests(unittest.TestCase):
    def test_accepted_mission_is_active(self):
        missions = active_missions([_accepted(1)])
        self.assertEqual(len(missions), 1)
        self.assertEqual(missions[0]["missionId"], 1)
        self.assertEqual(missions[0]["name"], "Test Mission")

    def test_completed_mission_is_no_longer_active(self):
        missions = active_missions([
            _accepted(1),
            {"event": "MissionCompleted", "MissionID": 1},
        ])
        self.assertEqual(missions, [])

    def test_failed_mission_is_no_longer_active(self):
        missions = active_missions([
            _accepted(1),
            {"event": "MissionFailed", "MissionID": 1},
        ])
        self.assertEqual(missions, [])

    def test_abandoned_mission_is_no_longer_active(self):
        missions = active_missions([
            _accepted(1),
            {"event": "MissionAbandoned", "MissionID": 1},
        ])
        self.assertEqual(missions, [])

    def test_a_lagging_completion_before_its_own_acceptance_still_closes_it(self):
        # An imported/replayed log can carry these out of chronological
        # order - the closing event must win regardless of position.
        missions = active_missions([
            {"event": "MissionCompleted", "MissionID": 1},
            _accepted(1),
        ])
        self.assertEqual(missions, [])

    def test_redirected_mission_updates_its_destination(self):
        missions = active_missions([
            _accepted(1, DestinationSystem="Old Sys", DestinationStation="Old Stn"),
            {
                "event": "MissionRedirected", "MissionID": 1,
                "NewDestinationSystem": "New Sys", "NewDestinationStation": "New Stn",
            },
        ])
        self.assertEqual(missions[0]["destinationSystem"], "New Sys")
        self.assertEqual(missions[0]["destinationStation"], "New Stn")

    def test_results_are_sorted_by_nearest_expiry_first(self):
        missions = active_missions([
            _accepted(1, Expiry="2026-09-25T00:00:00Z"),
            _accepted(2, Expiry="2026-09-19T00:00:00Z"),
            _accepted(3, Expiry="2026-09-21T00:00:00Z"),
        ])
        self.assertEqual([row["missionId"] for row in missions], [2, 3, 1])

    def test_donation_amount_is_parsed_from_its_string_field(self):
        # Frontier reports Donation as a numeric string, not a number.
        missions = active_missions([_accepted(1, Donation="750000")])
        self.assertEqual(missions[0]["donation"], 750000)
        self.assertIsNone(missions[0]["reward"])

    def test_delivery_mission_carries_commodity_and_count(self):
        missions = active_missions([_accepted(
            1, Commodity_Localised="Fisch", Count=8,
        )])
        self.assertEqual(missions[0]["commodity"], "Fisch")
        self.assertEqual(missions[0]["commodityCount"], 8)

    def test_massacre_mission_carries_kill_count(self):
        missions = active_missions([_accepted(
            1, TargetFaction="Some Pirates", KillCount=6,
        )])
        self.assertEqual(missions[0]["killCount"], 6)
        self.assertEqual(missions[0]["targetFaction"], "Some Pirates")

    def test_permit_acquisition_mission_is_excluded(self):
        # Known Frontier bug: this mission grants its permit on acceptance
        # but never receives a closing event, so it must never show up as
        # something still to act on.
        missions = active_missions([_accepted(
            1, Name="MISSION_genericPermit1",
            LocalisedName="Permit Acquisition Opportunity",
        )])
        self.assertEqual(missions, [])

    def test_permit_acquisition_mission_is_excluded_case_insensitively(self):
        missions = active_missions([_accepted(1, Name="Mission_GenericPermit3")])
        self.assertEqual(missions, [])

    def test_assassination_mission_carries_named_target(self):
        missions = active_missions([_accepted(
            1, Target="cmdr_pirate", Target_Localised="Ava May Dickinson",
            TargetType_Localised="Politician",
        )])
        self.assertEqual(missions[0]["target"], "Ava May Dickinson")
        self.assertEqual(missions[0]["targetType"], "Politician")


class MissionProgressTests(unittest.TestCase):
    def test_no_progress_events_leaves_progress_unknown(self):
        missions = active_missions([_accepted(1, Reward=100)])
        self.assertFalse(missions[0]["progressKnown"])
        self.assertEqual(missions[0]["progressDone"], 0)
        self.assertEqual(missions[0]["progressTotal"], 0)

    def test_cargo_depot_event_reports_exact_progress(self):
        missions = active_missions([
            _accepted(1, Commodity_Localised="Bromellite", Count=28),
            {
                "event": "CargoDepot", "MissionID": 1, "UpdateType": "Deliver",
                "ItemsCollected": 0, "ItemsDelivered": 7, "TotalItemsToDeliver": 28,
            },
        ])
        self.assertTrue(missions[0]["progressKnown"])
        self.assertEqual(missions[0]["progressDone"], 7)
        self.assertEqual(missions[0]["progressTotal"], 28)

    def test_later_cargo_depot_event_overwrites_the_earlier_one(self):
        missions = active_missions([
            _accepted(1, Count=28),
            {"event": "CargoDepot", "MissionID": 1, "ItemsDelivered": 7, "TotalItemsToDeliver": 28},
            {"event": "CargoDepot", "MissionID": 1, "ItemsDelivered": 28, "TotalItemsToDeliver": 28},
        ])
        self.assertEqual(missions[0]["progressDone"], 28)

    def test_cargo_depot_for_an_unrelated_mission_id_is_ignored(self):
        missions = active_missions([
            _accepted(1, Count=28),
            {"event": "CargoDepot", "MissionID": 999, "ItemsDelivered": 5, "TotalItemsToDeliver": 10},
        ])
        self.assertFalse(missions[0]["progressKnown"])

    def test_salvage_collection_mission_tallies_collect_cargo_events(self):
        missions = active_missions([
            _accepted(
                1, Commodity="$USSCargoBlackBox_Name;",
                Commodity_Localised="Black Box", Count=3,
            ),
            {"event": "CollectCargo", "MissionID": 1, "Type": "USSCargoBlackBox", "Stolen": True},
            {"event": "CollectCargo", "MissionID": 1, "Type": "USSCargoBlackBox", "Stolen": True},
        ])
        self.assertTrue(missions[0]["progressKnown"])
        self.assertEqual(missions[0]["progressDone"], 2)
        self.assertEqual(missions[0]["progressTotal"], 3)

    def test_collect_cargo_tally_never_exceeds_the_required_count(self):
        events = [_accepted(
            1, Commodity="$USSCargoBlackBox_Name;", Count=2,
        )]
        events += [{"event": "CollectCargo", "MissionID": 1, "Type": "USSCargoBlackBox"}] * 5
        missions = active_missions(events)
        self.assertEqual(missions[0]["progressDone"], 2)

    def test_collect_cargo_on_a_plain_purchased_commodity_mission_is_not_progress(self):
        # A regular "buy and carry" delivery mission's Commodity is a normal
        # tradeable good, not a $USSCargo... salvage symbol - CollectCargo
        # (e.g. from an unrelated salvage run) must not be attributed to it.
        missions = active_missions([
            _accepted(1, Commodity="$Fish_Name;", Commodity_Localised="Fisch", Count=8),
            {"event": "CollectCargo", "MissionID": 1, "Type": "Fish"},
        ])
        self.assertFalse(missions[0]["progressKnown"])

    def test_massacre_mission_never_reports_progress(self):
        missions = active_missions([_accepted(1, KillCount=6)])
        self.assertFalse(missions[0]["progressKnown"])

    def test_assassination_mission_never_reports_progress(self):
        missions = active_missions([_accepted(1, Target="Some Target")])
        self.assertFalse(missions[0]["progressKnown"])


class MissionsSummaryTests(unittest.TestCase):
    def test_empty_list_summarizes_to_zero(self):
        summary = missions_summary([])
        self.assertEqual(summary, {
            "activeCount": 0, "totalReward": 0,
            "missionsWithKnownReward": 0, "nearestExpiry": "",
        })

    def test_sums_only_missions_with_a_known_reward(self):
        missions = active_missions([
            _accepted(1, Reward=100),
            _accepted(2, Donation="500"),  # no credit Reward field
            _accepted(3, Reward=200),
        ])
        summary = missions_summary(missions)
        self.assertEqual(summary["activeCount"], 3)
        self.assertEqual(summary["totalReward"], 300)
        self.assertEqual(summary["missionsWithKnownReward"], 2)

    def test_nearest_expiry_is_the_earliest_one(self):
        missions = active_missions([
            _accepted(1, Expiry="2026-09-25T00:00:00Z"),
            _accepted(2, Expiry="2026-09-19T08:00:00Z"),
        ])
        summary = missions_summary(missions)
        self.assertEqual(summary["nearestExpiry"], "2026-09-19T08:00:00Z")


def _cg_event(timestamp, goals):
    return {"event": "CommunityGoal", "timestamp": timestamp, "CurrentGoals": goals}


def _goal(cgid, **overrides):
    goal = {
        "CGID": cgid, "Title": "Test Goal", "SystemName": "Erda",
        "MarketName": "Test Market", "Expiry": "2026-09-25T00:00:00Z",
        "IsComplete": False, "CurrentTotal": 1000, "PlayerContribution": 10,
        "NumContributors": 5, "TopTier": {"Name": "Tier 5", "Bonus": ""},
        "TopRankSize": 10, "PlayerInTopRank": False, "TierReached": "Tier 2",
        "PlayerPercentileBand": 50,
    }
    goal.update(overrides)
    return goal


class CommunityGoalsOverviewTests(unittest.TestCase):
    def test_an_open_goal_is_returned(self):
        goals = community_goals_overview([_cg_event("t1", [_goal(1)])])
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["cgid"], 1)
        self.assertEqual(goals[0]["title"], "Test Goal")

    def test_a_concluded_goal_is_excluded(self):
        goals = community_goals_overview([
            _cg_event("t1", [_goal(1, IsComplete=True)]),
        ])
        self.assertEqual(goals, [])

    def test_only_the_most_recent_community_goal_event_is_used(self):
        # Frontier resends the full roster each time - an older event's
        # goals must never be merged with a newer, different roster.
        events = [
            _cg_event("t1", [_goal(1), _goal(2)]),
            _cg_event("t2", [_goal(2)]),
        ]
        goals = community_goals_overview(events)
        self.assertEqual([g["cgid"] for g in goals], [2])

    def test_no_community_goal_event_at_all_returns_empty(self):
        self.assertEqual(community_goals_overview([]), [])


if __name__ == "__main__":
    unittest.main()
