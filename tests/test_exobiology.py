import json
import math
import unittest
from pathlib import Path

from ed_companion.exobiology import (
    best_find,
    colony_range_for_genus,
    exobiology_carried_summary,
    exobiology_distance_check,
    exobiology_findings,
    exobiology_lifetime_earned,
    exobiology_scan_progress,
    exobiology_session_summary,
    exobiology_summary,
    footfalled_bodies,
    great_circle_distance_m,
    landing_targets,
    populated_systems,
    remaining_signals_at_body,
)

REFERENCE_DATA_DIR = Path(__file__).resolve().parents[1] / "ed_data"
SPECIES_CATALOG = json.loads(
    (REFERENCE_DATA_DIR / "exobiology_species.json").read_text(encoding="utf-8")
)
COLONY_RANGES = json.loads(
    (REFERENCE_DATA_DIR / "exobiology_colony_ranges.json").read_text(encoding="utf-8")
)


def _scan_event(scan_type, timestamp, **overrides):
    event = {
        "timestamp": timestamp, "event": "ScanOrganic", "ScanType": scan_type,
        "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
        "Species": "$Codex_Ent_Aleoids_01_Name;",
        "Body": "Erda 1 a", "BodyID": 3,
    }
    event.update(overrides)
    return event


class ExobiologyFindingsTests(unittest.TestCase):
    def test_three_step_scan_completes_and_reports_the_catalog_value(self):
        events = [
            _scan_event("Log", "2026-09-12T10:00:00Z"),
            _scan_event("Sample", "2026-09-12T10:02:00Z"),
            _scan_event("Analyse", "2026-09-12T10:04:00Z"),
        ]

        findings = exobiology_findings(events, SPECIES_CATALOG)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["displayName"], "Aleoida Arcus")
        self.assertEqual(findings[0]["value"], 7252500)
        self.assertTrue(findings[0]["complete"])
        self.assertEqual(findings[0]["samplesDone"], 3)

    def test_partial_scan_keeps_the_value_visible_but_not_complete(self):
        findings = exobiology_findings(
            [_scan_event("Log", "2026-09-12T10:00:00Z")], SPECIES_CATALOG,
        )

        self.assertEqual(findings[0]["samplesDone"], 1)
        self.assertFalse(findings[0]["complete"])
        self.assertEqual(findings[0]["value"], 7252500)

    def test_same_species_on_different_bodies_tracked_separately(self):
        events = [
            _scan_event("Log", "2026-09-12T10:00:00Z", Body="Erda 1 a", BodyID=3),
            _scan_event("Log", "2026-09-12T11:00:00Z", Body="Erda 2 b", BodyID=5),
        ]

        progress = exobiology_scan_progress(events)

        self.assertEqual(len(progress), 2)

    def test_unrecognized_species_still_reports_a_row_with_a_fallback_name(self):
        findings = exobiology_findings([
            _scan_event(
                "Log", "2026-09-12T10:00:00Z",
                Genus="$Codex_Ent_Made_Up_Genus_Name;",
                Species="$Codex_Ent_Made_Up_01_Name;",
            ),
        ], SPECIES_CATALOG)

        self.assertFalse(findings[0]["valueKnown"])
        self.assertEqual(findings[0]["value"], 0)
        self.assertEqual(findings[0]["displayName"], "Made Up 01")
        self.assertEqual(findings[0]["genusDisplay"], "Made Up")

    def test_journal_localised_name_wins_over_the_english_catalog(self):
        findings = exobiology_findings([
            _scan_event(
                "Log", "2026-09-12T10:00:00Z",
                Genus_Localised="Aleoids", Species_Localised="Aleoide Arcus",
            ),
        ], SPECIES_CATALOG)

        self.assertEqual(findings[0]["displayName"], "Aleoide Arcus")
        self.assertEqual(findings[0]["genusDisplay"], "Aleoids")
        # The credit value still comes from the catalog match by raw key.
        self.assertEqual(findings[0]["value"], 7252500)

    def test_body_is_shown_as_a_readable_label(self):
        findings = exobiology_findings(
            [_scan_event("Log", "2026-09-12T10:00:00Z", Body=7)], SPECIES_CATALOG,
        )

        self.assertEqual(findings[0]["bodyDisplay"], "Body 7")

    def test_summary_rolls_up_banked_and_potential_value(self):
        events = [
            _scan_event("Log", "2026-09-12T10:00:00Z"),
            _scan_event("Sample", "2026-09-12T10:02:00Z"),
            _scan_event("Analyse", "2026-09-12T10:04:00Z"),
            _scan_event(
                "Log", "2026-09-12T10:10:00Z", Body="Erda 2 b", BodyID=5,
                Genus="$Codex_Ent_Aleoids_Genus_Name;",
                Species="$Codex_Ent_Aleoids_02_Name;",
            ),
        ]

        summary = exobiology_summary(exobiology_findings(events, SPECIES_CATALOG))

        self.assertEqual(summary["totalSpecies"], 2)
        self.assertEqual(summary["completeSpecies"], 1)
        self.assertEqual(summary["inProgressSpecies"], 1)
        self.assertEqual(summary["bankedValue"], 7252500)
        self.assertEqual(summary["potentialValue"], 6284600)


class SessionAndCarriedSummaryTests(unittest.TestCase):
    """Two different "how much am I sitting on" questions, both keyed off
    real Journal events rather than the app's own play-session tracking:
    a completion counts toward "this session" from the most recent
    LoadGame, and toward "carried, unsold" from the most recent
    SellOrganicData - whichever of those actually happened last."""

    def _completed_species(self, timestamp, body="Erda 1 a", **overrides):
        return [
            _scan_event("Log", timestamp, Body=body, **overrides),
            _scan_event("Sample", timestamp, Body=body, **overrides),
            _scan_event("Analyse", timestamp, Body=body, **overrides),
        ]

    def test_a_species_completed_before_login_is_not_part_of_this_session(self):
        events = [
            *self._completed_species("2026-09-10T10:00:00Z"),
            {"event": "LoadGame", "timestamp": "2026-09-12T09:00:00Z"},
        ]

        summary = exobiology_session_summary(events, SPECIES_CATALOG)

        self.assertEqual(summary["speciesCount"], 0)
        self.assertEqual(summary["totalValue"], 0)

    def test_a_species_completed_after_login_counts_toward_this_session(self):
        events = [
            {"event": "LoadGame", "timestamp": "2026-09-12T09:00:00Z"},
            *self._completed_species("2026-09-12T10:00:00Z"),
        ]

        summary = exobiology_session_summary(events, SPECIES_CATALOG)

        self.assertEqual(summary["speciesCount"], 1)
        self.assertEqual(summary["totalValue"], 7252500)

    def test_with_no_loadgame_on_record_everything_counts_as_this_session(self):
        events = self._completed_species("2026-09-12T10:00:00Z")

        summary = exobiology_session_summary(events, SPECIES_CATALOG)

        self.assertEqual(summary["speciesCount"], 1)

    def test_everything_is_carried_until_the_first_sale_ever_happens(self):
        events = self._completed_species("2026-09-12T10:00:00Z")

        summary = exobiology_carried_summary(events, SPECIES_CATALOG)

        self.assertEqual(summary["speciesCount"], 1)
        self.assertEqual(summary["totalValue"], 7252500)

    def test_a_species_completed_before_the_last_sale_is_no_longer_carried(self):
        events = [
            *self._completed_species("2026-09-10T10:00:00Z"),
            {"event": "SellOrganicData", "timestamp": "2026-09-11T08:00:00Z"},
        ]

        summary = exobiology_carried_summary(events, SPECIES_CATALOG)

        self.assertEqual(summary["speciesCount"], 0)
        self.assertEqual(summary["totalValue"], 0)

    def test_a_species_completed_after_the_last_sale_stays_carried(self):
        events = [
            {"event": "SellOrganicData", "timestamp": "2026-09-11T08:00:00Z"},
            *self._completed_species("2026-09-12T10:00:00Z"),
        ]

        summary = exobiology_carried_summary(events, SPECIES_CATALOG)

        self.assertEqual(summary["speciesCount"], 1)
        self.assertEqual(summary["totalValue"], 7252500)

    def test_carried_and_session_are_independent_of_each_other(self):
        # Found two days ago (so not "this session"), never sold since (so
        # still fully "carried") - the two numbers are allowed to disagree.
        events = [
            {"event": "LoadGame", "timestamp": "2026-09-12T09:00:00Z"},
            *self._completed_species("2026-09-10T10:00:00Z"),
        ]

        session = exobiology_session_summary(events, SPECIES_CATALOG)
        carried = exobiology_carried_summary(events, SPECIES_CATALOG)

        self.assertEqual(session["speciesCount"], 0)
        self.assertEqual(carried["speciesCount"], 1)


def _signals_event(system_address, body_id, count, signal_type="$SAA_SignalType_Biological;"):
    return {
        "event": "FSSBodySignals", "SystemAddress": system_address, "BodyID": body_id,
        "Signals": [{"Type": signal_type, "Count": count}],
    }


def _planet_scan_event(system_address, body_id, **overrides):
    event = {
        "event": "Scan", "SystemAddress": system_address, "BodyID": body_id,
        "BodyName": "Test Body", "StarSystem": "Test System",
        "PlanetClass": "Rocky body", "AtmosphereType": "CarbonDioxide",
        "Volcanism": "", "SurfaceGravity": 0.15 * 9.80665,
        "SurfaceTemperature": 178.0, "SurfacePressure": 0.02 * 101325.0,
        "Landable": True, "DistanceFromArrivalLS": 500.0,
    }
    event.update(overrides)
    return event


class LandingTargetsTests(unittest.TestCase):
    """Aleoida Arcus: CarbonDioxide atmosphere, gravity 0.04-0.276 G,
    temperature 175-180 K, min pressure 0.0161 atm, Rocky/HMC body,
    no volcanism - the fixture body above matches it exactly."""

    def test_a_matching_unscanned_body_predicts_candidates_from_conditions(self):
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3),
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["confidence"], "predicted")
        self.assertEqual(targets[0]["signalCount"], 2)
        names = [c["name"] for c in targets[0]["candidates"]]
        self.assertIn("Aleoida Arcus", names)

    def test_a_dss_confirmed_genus_is_used_over_guessed_conditions(self):
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3, PlanetClass="Icy body", AtmosphereType="None"),
            {
                "event": "SAASignalsFound", "SystemAddress": 1, "BodyID": 3,
                "Signals": [{"Type": "$SAA_SignalType_Biological;", "Count": 2}],
                "Genuses": [{"Genus": "$Codex_Ent_Aleoids_Genus_Name;"}],
            },
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        self.assertEqual(targets[0]["confidence"], "confirmed_genus")
        names = [c["name"] for c in targets[0]["candidates"]]
        self.assertIn("Aleoida Arcus", names)
        self.assertIn("Aleoida Coronamus", names)

    def test_a_body_without_biological_signals_is_not_a_target(self):
        events = [
            _signals_event(1, 3, 4, signal_type="$SAA_SignalType_Geological;"),
            _planet_scan_event(1, 3),
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        self.assertEqual(targets, [])

    def test_conditions_that_no_longer_match_drop_that_species_as_a_candidate(self):
        # Same fixture body, but with an atmosphere and gravity Aleoida
        # Arcus does not tolerate - other genera with looser rulesets may
        # still legitimately match this body; Arcus specifically must not.
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3, AtmosphereType="Methane", SurfaceGravity=5.0),
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        names = [c["name"] for t in targets for c in t["candidates"]]
        self.assertNotIn("Aleoida Arcus", names)

    def test_a_fully_completed_genus_at_that_body_is_excluded(self):
        # The player already banked this genus at this body - nothing left
        # to gain from a return visit. Other, not-yet-found genera the body
        # could still hold remain.
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3),
            {
                "event": "ScanOrganic", "timestamp": "2026-09-12T10:00:00Z",
                "ScanType": "Log", "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
                "Species": "$Codex_Ent_Aleoids_01_Name;",
                "SystemAddress": 1, "Body": 3,
            },
            {
                "event": "ScanOrganic", "timestamp": "2026-09-12T10:02:00Z",
                "ScanType": "Sample", "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
                "Species": "$Codex_Ent_Aleoids_01_Name;",
                "SystemAddress": 1, "Body": 3,
            },
            {
                "event": "ScanOrganic", "timestamp": "2026-09-12T10:04:00Z",
                "ScanType": "Analyse", "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
                "Species": "$Codex_Ent_Aleoids_01_Name;",
                "SystemAddress": 1, "Body": 3,
            },
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        names = [c["name"] for t in targets for c in t["candidates"]]
        self.assertNotIn("Aleoida Arcus", names)

    def test_a_genus_completed_at_the_same_body_number_in_another_system_is_not_excluded(self):
        # Regression: BodyID is only unique within one system. A genus
        # completed at "body 3" in system 2 must not blank out an
        # unrelated, still-open signal at "body 3" in system 1.
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3),
            {
                "event": "ScanOrganic", "timestamp": "2026-07-01T10:00:00Z",
                "ScanType": "Log", "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
                "Species": "$Codex_Ent_Aleoids_01_Name;",
                "SystemAddress": 2, "Body": 3,
            },
            {
                "event": "ScanOrganic", "timestamp": "2026-07-01T10:02:00Z",
                "ScanType": "Sample", "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
                "Species": "$Codex_Ent_Aleoids_01_Name;",
                "SystemAddress": 2, "Body": 3,
            },
            {
                "event": "ScanOrganic", "timestamp": "2026-07-01T10:04:00Z",
                "ScanType": "Analyse", "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
                "Species": "$Codex_Ent_Aleoids_01_Name;",
                "SystemAddress": 2, "Body": 3,
            },
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        names = [c["name"] for t in targets for c in t["candidates"]]
        self.assertIn("Aleoida Arcus", names)

    def test_a_merely_logged_genus_still_under_way_stays_a_target(self):
        # Regression: taking just the first ("Log") sample of a signal must
        # not make it vanish from Survey Targets before the Commander has
        # actually finished sampling it - it is still "in progress", not
        # "nothing left to check here".
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3),
            {
                "event": "ScanOrganic", "timestamp": "2026-09-12T10:00:00Z",
                "ScanType": "Log", "Genus": "$Codex_Ent_Aleoids_Genus_Name;",
                "Species": "$Codex_Ent_Aleoids_01_Name;",
                "SystemAddress": 1, "Body": 3,
            },
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        names = [c["name"] for t in targets for c in t["candidates"]]
        self.assertIn("Aleoida Arcus", names)

    def test_targets_are_ranked_confirmed_first_then_by_value(self):
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3),
            _signals_event(2, 9, 1),
            _planet_scan_event(
                2, 9, AtmosphereType="Ammonia", SurfaceGravity=0.1 * 9.80665,
                SurfaceTemperature=175.0, SurfacePressure=0.01 * 101325.0,
            ),
            {
                "event": "SAASignalsFound", "SystemAddress": 2, "BodyID": 9,
                "Signals": [{"Type": "$SAA_SignalType_Biological;", "Count": 1}],
                "Genuses": [{"Genus": "$Codex_Ent_Aleoids_Genus_Name;"}],
            },
        ]

        targets = landing_targets(events, SPECIES_CATALOG)

        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0]["confidence"], "confirmed_genus")
        self.assertEqual(targets[0]["bodyId"], 9)

    def test_current_system_is_ranked_first_even_over_a_confirmed_genus_elsewhere(self):
        events = [
            _signals_event(1, 3, 2),
            _planet_scan_event(1, 3),
            _signals_event(2, 9, 1),
            _planet_scan_event(
                2, 9, AtmosphereType="Ammonia", SurfaceGravity=0.1 * 9.80665,
                SurfaceTemperature=175.0, SurfacePressure=0.01 * 101325.0,
            ),
            {
                "event": "SAASignalsFound", "SystemAddress": 2, "BodyID": 9,
                "Signals": [{"Type": "$SAA_SignalType_Biological;", "Count": 1}],
                "Genuses": [{"Genus": "$Codex_Ent_Aleoids_Genus_Name;"}],
            },
        ]

        targets = landing_targets(events, SPECIES_CATALOG, current_system_address=1)

        self.assertTrue(targets[0]["inCurrentSystem"])
        self.assertEqual(targets[0]["bodyId"], 3)


class FirstFootfallEligibilityTests(unittest.TestCase):
    """firstFootfallPossible is a pre-filter, never a promise: it can only
    ever rule the bonus OUT (already landed here, or a populated system),
    never confirm it IN - whether another Commander already has is not
    knowable from a local Journal at all."""

    def _base_events(self, **jump_overrides):
        jump = {
            "event": "FSDJump", "SystemAddress": 1, "StarSystem": "Test System",
            "Population": 0,
        }
        jump.update(jump_overrides)
        return [jump, _signals_event(1, 3, 2), _planet_scan_event(1, 3)]

    def test_an_untouched_body_in_an_unpopulated_system_is_possible(self):
        targets = landing_targets(self._base_events(), SPECIES_CATALOG)
        self.assertTrue(targets[0]["firstFootfallPossible"])

    def test_a_body_the_commander_has_already_touched_down_on_is_not_possible(self):
        events = self._base_events() + [{
            "event": "Touchdown", "PlayerControlled": True,
            "SystemAddress": 1, "BodyID": 3,
        }]
        targets = landing_targets(events, SPECIES_CATALOG)
        self.assertFalse(targets[0]["firstFootfallPossible"])

    def test_a_populated_system_is_never_possible(self):
        targets = landing_targets(self._base_events(Population=1_000_000), SPECIES_CATALOG)
        self.assertFalse(targets[0]["firstFootfallPossible"])

    def test_an_npc_controlled_touchdown_does_not_count_as_the_commanders_own(self):
        events = self._base_events() + [{
            "event": "Touchdown", "PlayerControlled": False,
            "SystemAddress": 1, "BodyID": 3,
        }]
        targets = landing_targets(events, SPECIES_CATALOG)
        self.assertTrue(targets[0]["firstFootfallPossible"])

    def test_footfalled_bodies_tracks_system_and_body_together(self):
        events = [{
            "event": "Touchdown", "PlayerControlled": True,
            "SystemAddress": 1, "BodyID": 3,
        }]
        self.assertEqual(footfalled_bodies(events), {(1, 3)})

    def test_populated_systems_ignores_a_system_with_no_population(self):
        events = [
            {"event": "FSDJump", "SystemAddress": 1, "Population": 0},
            {"event": "FSDJump", "SystemAddress": 2, "Population": 500},
        ]
        self.assertEqual(populated_systems(events), {2})


class RemainingSignalsAtBodyTests(unittest.TestCase):
    def test_no_body_name_in_status_yields_no_result(self):
        events = [_signals_event(1, 3, 2), _planet_scan_event(1, 3)]
        self.assertIsNone(remaining_signals_at_body(events, SPECIES_CATALOG, {}))

    def test_a_body_never_fss_scanned_yields_no_result(self):
        result = remaining_signals_at_body(
            [], SPECIES_CATALOG, {"BodyName": "Nowhere"},
        )
        self.assertIsNone(result)

    def test_an_untouched_body_reports_its_full_signal_and_candidate_count(self):
        events = [_signals_event(1, 3, 2), _planet_scan_event(1, 3)]
        result = remaining_signals_at_body(
            events, SPECIES_CATALOG, {"BodyName": "Test Body"},
        )
        self.assertEqual(result["totalSignals"], 2)
        self.assertGreater(result["remaining"], 0)

    def test_remaining_is_never_inflated_past_the_actual_signal_count(self):
        # Regression: two confirmed genera can list dozens of species
        # between them in the catalog (Tussocks alone has 15) - "remaining"
        # must never report more distinct organisms than the body's own
        # FSS-detected signal count, no matter how many catalog species
        # match those genera.
        events = [
            _signals_event(1, 3, 2), _planet_scan_event(1, 3),
            {
                "event": "SAASignalsFound", "SystemAddress": 1, "BodyID": 3,
                "Signals": [{"Type": "$SAA_SignalType_Biological;", "Count": 2}],
                "Genuses": [
                    {"Genus": "$Codex_Ent_Tussocks_Genus_Name;"},
                    {"Genus": "$Codex_Ent_Bacterial_Genus_Name;"},
                ],
            },
        ]
        result = remaining_signals_at_body(
            events, SPECIES_CATALOG, {"BodyName": "Test Body"},
        )
        self.assertEqual(result["totalSignals"], 2)
        self.assertEqual(result["remaining"], 2)

    def test_a_fully_claimed_body_reports_zero_remaining_not_none(self):
        # Every genus this body could hold has already been completed -
        # genuinely "0 left here", which must read differently from
        # "nothing detected here at all" (None).
        events = [
            _signals_event(1, 3, 2), _planet_scan_event(1, 3),
            {
                "event": "SAASignalsFound", "SystemAddress": 1, "BodyID": 3,
                "Signals": [{"Type": "$SAA_SignalType_Biological;", "Count": 2}],
                "Genuses": [{"Genus": "$Codex_Ent_Aleoids_Genus_Name;"}],
            },
            _scan_event("Log", "2026-09-12T10:00:00Z", Body="3", BodyID=3, SystemAddress=1),
            _scan_event("Sample", "2026-09-12T10:02:00Z", Body="3", BodyID=3, SystemAddress=1),
            _scan_event("Analyse", "2026-09-12T10:04:00Z", Body="3", BodyID=3, SystemAddress=1),
        ]
        result = remaining_signals_at_body(
            events, SPECIES_CATALOG, {"BodyName": "Test Body"},
        )
        self.assertEqual(result, {"totalSignals": 2, "remaining": 0})


class LifetimeEarnedTests(unittest.TestCase):
    def test_no_sales_ever_is_zero(self):
        self.assertEqual(exobiology_lifetime_earned([]), 0)

    def test_sums_value_and_bonus_across_every_sale(self):
        events = [
            {
                "event": "SellOrganicData",
                "BioData": [
                    {"Value": 1000000, "Bonus": 0},
                    {"Value": 500000, "Bonus": 2000000},
                ],
            },
            {
                "event": "SellOrganicData",
                "BioData": [{"Value": 250000, "Bonus": 0}],
            },
        ]
        self.assertEqual(exobiology_lifetime_earned(events), 3750000)


class BestFindTests(unittest.TestCase):
    def _finding(self, value, complete=True, value_known=True, **overrides):
        row = {
            "displayName": "Some Species", "value": value,
            "complete": complete, "valueKnown": value_known,
        }
        row.update(overrides)
        return row

    def test_no_findings_yields_none(self):
        self.assertIsNone(best_find([]))

    def test_picks_the_highest_value_completed_finding(self):
        findings = [
            self._finding(1000, displayName="Cheap"),
            self._finding(9000000, displayName="Expensive"),
            self._finding(5000, displayName="Middling"),
        ]
        self.assertEqual(best_find(findings)["displayName"], "Expensive")

    def test_an_incomplete_finding_never_wins_even_if_valuable(self):
        findings = [
            self._finding(9000000, complete=False, displayName="Unfinished"),
            self._finding(1000, displayName="Finished"),
        ]
        self.assertEqual(best_find(findings)["displayName"], "Finished")

    def test_an_unrecognized_species_never_wins_despite_a_zero_value(self):
        findings = [
            self._finding(0, value_known=False, displayName="Unknown"),
            self._finding(1000, displayName="Known"),
        ]
        self.assertEqual(best_find(findings)["displayName"], "Known")

    def test_only_unrecognized_or_incomplete_findings_yields_none(self):
        findings = [
            self._finding(9000000, value_known=False),
            self._finding(9000000, complete=False),
        ]
        self.assertIsNone(best_find(findings))


class GreatCircleDistanceTests(unittest.TestCase):
    def test_the_same_point_is_zero_distance_away(self):
        self.assertEqual(great_circle_distance_m(10, 20, 10, 20, 1_000_000), 0)

    def test_a_quarter_of_the_way_around_is_a_quarter_circumference(self):
        radius = 1_000_000.0
        distance = great_circle_distance_m(0, 0, 0, 90, radius)
        self.assertAlmostEqual(distance, math.pi / 2 * radius, places=3)


class ColonyRangeLookupTests(unittest.TestCase):
    def test_a_catalogued_genus_resolves_its_module_names_range(self):
        self.assertEqual(
            colony_range_for_genus(
                "$Codex_Ent_Aleoids_Genus_Name;", SPECIES_CATALOG, COLONY_RANGES,
            ),
            150,
        )

    def test_an_unrecognized_genus_has_no_known_range(self):
        self.assertIsNone(
            colony_range_for_genus(
                "$Codex_Ent_Made_Up_Genus_Name;", SPECIES_CATALOG, COLONY_RANGES,
            )
        )


def _status(body_name, lat, lon):
    return {"BodyName": body_name, "Latitude": lat, "Longitude": lon}


class ExobiologyDistanceCheckTests(unittest.TestCase):
    """Aleoida's colony range is 150 m (see ed_data/exobiology_colony_ranges.json)."""

    def _in_progress_finding(self, samples_done=1, **overrides):
        finding = {
            "systemAddress": 1, "body": "3",
            "genus": "$Codex_Ent_Aleoids_Genus_Name;",
            "species": "$Codex_Ent_Aleoids_01_Name;",
            "displayName": "Aleoida Arcus", "genusDisplay": "Aleoida",
            "samplesDone": samples_done, "complete": False,
            "lastSeen": "2026-09-12T10:00:00Z",
        }
        finding.update(overrides)
        return finding

    def test_no_status_position_yields_no_check(self):
        result = exobiology_distance_check(
            [self._in_progress_finding()], {}, SPECIES_CATALOG, COLONY_RANGES,
            current_system_address=1, status={},
        )
        self.assertEqual(result, {})

    def test_no_in_progress_finding_in_the_current_system_yields_no_check(self):
        result = exobiology_distance_check(
            [self._in_progress_finding(systemAddress=2)], {}, SPECIES_CATALOG,
            COLONY_RANGES, current_system_address=1,
            status=_status("Body A", 0.0, 0.0),
        )
        self.assertEqual(result, {})

    def test_no_recorded_baseline_position_yields_no_check(self):
        result = exobiology_distance_check(
            [self._in_progress_finding()], {}, SPECIES_CATALOG, COLONY_RANGES,
            current_system_address=1, status=_status("Body A", 0.0, 0.0),
        )
        self.assertEqual(result, {})

    def test_a_baseline_on_a_different_body_yields_no_check(self):
        key = (1, "3", "$Codex_Ent_Aleoids_Genus_Name;", "$Codex_Ent_Aleoids_01_Name;")
        positions = {key: {"lat": 0.0, "lon": 0.0, "radius": 1_000_000.0, "bodyName": "Body B"}}
        result = exobiology_distance_check(
            [self._in_progress_finding()], positions, SPECIES_CATALOG, COLONY_RANGES,
            current_system_address=1, status=_status("Body A", 0.0, 0.0),
        )
        self.assertEqual(result, {})

    def test_still_too_close_reports_not_ready(self):
        key = (1, "3", "$Codex_Ent_Aleoids_Genus_Name;", "$Codex_Ent_Aleoids_01_Name;")
        positions = {key: {"lat": 0.0, "lon": 0.0, "radius": 1_000_000.0, "bodyName": "Body A"}}
        result = exobiology_distance_check(
            [self._in_progress_finding(samples_done=1)], positions, SPECIES_CATALOG,
            COLONY_RANGES, current_system_address=1,
            status=_status("Body A", 0.0, 0.0001),  # ~11 m away
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["requiredM"], 150)
        self.assertEqual(result["nextStep"], "Sample")

    def test_far_enough_away_reports_ready(self):
        key = (1, "3", "$Codex_Ent_Aleoids_Genus_Name;", "$Codex_Ent_Aleoids_01_Name;")
        positions = {key: {"lat": 0.0, "lon": 0.0, "radius": 1_000_000.0, "bodyName": "Body A"}}
        result = exobiology_distance_check(
            [self._in_progress_finding(samples_done=2)], positions, SPECIES_CATALOG,
            COLONY_RANGES, current_system_address=1,
            status=_status("Body A", 0.0, 0.01),  # ~1745 m away
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["nextStep"], "Analyse")


if __name__ == "__main__":
    unittest.main()
