import json
import unittest
from pathlib import Path

from ed_companion.exobiology import (
    exobiology_carried_summary,
    exobiology_findings,
    exobiology_scan_progress,
    exobiology_session_summary,
    exobiology_summary,
    landing_targets,
)

REFERENCE_DATA_DIR = Path(__file__).resolve().parents[1] / "ed_data"
SPECIES_CATALOG = json.loads(
    (REFERENCE_DATA_DIR / "exobiology_species.json").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
