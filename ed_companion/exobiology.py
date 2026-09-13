"""Exobiology scan progress derived purely from Journal ``ScanOrganic``
events, matched against the bundled species catalog.

Elite Dangerous' Genetic Sampler completes one species in exactly three
distinct steps, each its own ``ScanOrganic`` event with a different
``ScanType``:

    1. "Log"     - identifies the genus/species; no distance requirement.
    2. "Sample"  - first genetic sample; must be taken far enough from the
                   Log position (the genus' "colony range").
    3. "Analyse" - second genetic sample, far enough from step 2; this is
                   the step that actually pays out the credit value.

``ed_data/exobiology_species.json`` carries the genus/species catalog
(Frontier's own raw Codex keys, spawn-condition rules, and credit value);
``ed_data/exobiology_colony_ranges.json`` carries the minimum distance
per genus. Both are reference data injected by the caller, matching every
other pure-function module in this codebase - this module never reads a
file itself.
"""
from __future__ import annotations

import math
import re
from typing import Any

SCAN_STEP_LEVELS = {"Log": 1, "Sample": 2, "Analyse": 3}
REQUIRED_SAMPLES = 3


def _humanize_codex_key(raw_key: str) -> str:
    """Turn a raw, unmatched Codex key into a readable best guess.

    Frontier's Codex keys look like ``$Codex_Ent_Stratum_Genus_Name;`` or
    ``$Codex_Ent_Aleoids_01_Name;`` - strip the wrapper and separators so
    an unrecognized entry never shows raw Journal internals to the player.
    """
    text = re.sub(r"^\$Codex_Ent_|_Name;?$", "", raw_key)
    text = re.sub(r"_Genus$", "", text)
    return re.sub(r"[_\s]+", " ", text).strip() or raw_key


def exobiology_scan_progress(
    events: list[dict[str, Any]] | None,
) -> dict[tuple[object, str, str, str], dict[str, Any]]:
    """Fold ``ScanOrganic`` events into per-body-per-species scan state.

    Keyed by (SystemAddress, Body, Genus, Species) so the same species on
    two different bodies is tracked separately, matching how the game
    itself treats it - colony range only applies to samples of the same
    species on the same body. The system address has to be part of that
    key too: Frontier's ``BodyID`` is only unique within one system, and
    plenty of systems have a body #8 - without it, a completed find in one
    system would silently merge with an unrelated one sharing that same
    body number in a different system.
    """
    progress: dict[tuple[object, str, str, str], dict[str, Any]] = {}
    for event in events or []:
        if not isinstance(event, dict) or event.get("event") != "ScanOrganic":
            continue
        genus = str(event.get("Genus") or "")
        species = str(event.get("Species") or "")
        if not genus or not species:
            continue
        system_address = event.get("SystemAddress")
        body = str(event.get("Body") or event.get("BodyID") or "")
        key = (system_address, body, genus, species)
        row = progress.setdefault(key, {
            "genus": genus, "species": species,
            "variant": str(event.get("Variant") or ""), "body": body,
            "systemAddress": system_address,
            "genusLocalised": "", "speciesLocalised": "",
            "steps": set(), "firstTimestamp": "", "lastTimestamp": "",
        })
        level = SCAN_STEP_LEVELS.get(str(event.get("ScanType") or ""), 0)
        if level:
            row["steps"].add(level)
        # Frontier includes the player's-client-language name directly on
        # the event; prefer it over guessing from the raw Codex key.
        if not row["genusLocalised"] and event.get("Genus_Localised"):
            row["genusLocalised"] = str(event.get("Genus_Localised"))
        if not row["speciesLocalised"] and event.get("Species_Localised"):
            row["speciesLocalised"] = str(event.get("Species_Localised"))
        timestamp = str(event.get("timestamp") or "")
        if timestamp:
            row["lastTimestamp"] = timestamp
            if not row["firstTimestamp"]:
                row["firstTimestamp"] = timestamp
    return progress


def exobiology_findings(
    events: list[dict[str, Any]] | None,
    species_catalog: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return one display-ready row per tracked species, richest first.

    The Journal's ``ScanOrganic`` ``Genus``/``Species`` fields are
    Frontier's own raw Codex keys (e.g. ``$Codex_Ent_Aleoids_01_Name;``) -
    the same keys the catalog is indexed by, so this matches exactly
    rather than guessing from a display name.
    """
    by_species_key = {
        (row.get("genusCodexKey"), row.get("speciesCodexKey")): row
        for row in species_catalog or [] if isinstance(row, dict)
    }
    rows = []
    for (system_address, body, genus, species), scan in exobiology_scan_progress(events).items():
        catalog_row = by_species_key.get((genus, species))
        samples_done = max(scan["steps"], default=0)
        # Prefer the Journal's own client-language name (present on modern
        # events), then the English catalog, then a best-effort guess from
        # the raw Codex key so an unmatched/older entry never shows raw
        # Journal internals to the player.
        genus_display = (
            scan["genusLocalised"] or (
                catalog_row["genus"].replace("_", " ").title() if catalog_row else ""
            ) or _humanize_codex_key(genus)
        )
        display_name = (
            scan["speciesLocalised"]
            or (catalog_row["name"] if catalog_row else "")
            or _humanize_codex_key(species)
        )
        rows.append({
            "systemAddress": system_address,
            "body": body,
            "bodyDisplay": f"Body {body}" if body.isdigit() else body,
            "genus": genus,
            "genusDisplay": genus_display,
            "species": species,
            "displayName": display_name,
            "value": int(catalog_row["value"]) if catalog_row else 0,
            "valueKnown": catalog_row is not None,
            "samplesDone": samples_done,
            "samplesRequired": REQUIRED_SAMPLES,
            "complete": samples_done >= REQUIRED_SAMPLES,
            "firstSeen": scan["firstTimestamp"],
            "lastSeen": scan["lastTimestamp"],
        })
    rows.sort(key=lambda row: (row["complete"], -row["value"], row["displayName"]))
    return rows


def exobiology_summary(findings: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Roll up total banked value and outstanding progress across findings."""
    findings = findings or []
    complete = [row for row in findings if row.get("complete")]
    in_progress = [row for row in findings if not row.get("complete")]
    return {
        "totalSpecies": len(findings),
        "completeSpecies": len(complete),
        "inProgressSpecies": len(in_progress),
        "bankedValue": sum(int(row.get("value") or 0) for row in complete),
        "potentialValue": sum(int(row.get("value") or 0) for row in in_progress),
    }


def _latest_event_timestamp(events: list[dict[str, Any]], event_name: str) -> str:
    """Return the timestamp of the most recent event of this type, or ""."""
    timestamp = ""
    for event in events or []:
        if isinstance(event, dict) and event.get("event") == event_name:
            candidate = str(event.get("timestamp") or "")
            if candidate:
                timestamp = candidate
    return timestamp


def exobiology_session_summary(
    events: list[dict[str, Any]] | None,
    species_catalog: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Species completed since the most recent ``LoadGame`` - this play session.

    A species counts once its Analyse step (the one that carries the payout)
    has landed; ``lastSeen`` is that step's own timestamp. With no
    ``LoadGame`` on record (an incomplete or synthetic Journal), everything
    completed is treated as "this session" rather than hiding it.
    """
    events = events or []
    session_start = _latest_event_timestamp(events, "LoadGame")
    completed = [
        row for row in exobiology_findings(events, species_catalog)
        if row["complete"] and row["lastSeen"] >= session_start
    ]
    return {
        "speciesCount": len(completed),
        "totalValue": sum(int(row.get("value") or 0) for row in completed),
    }


def exobiology_carried_summary(
    events: list[dict[str, Any]] | None,
    species_catalog: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Completed species not yet sold at a Vista Genomics ``SellOrganicData``.

    This is the Commander's actual carried, at-risk value right now - it
    spans sessions on purpose: data collected last week and never sold is
    still sitting in the ship today. With no sale on record, the whole
    career's completed value counts as carried, which is correct - nothing
    has been banked yet.
    """
    events = events or []
    last_sale = _latest_event_timestamp(events, "SellOrganicData")
    carried = [
        row for row in exobiology_findings(events, species_catalog)
        if row["complete"] and row["lastSeen"] > last_sale
    ]
    return {
        "speciesCount": len(carried),
        "totalValue": sum(int(row.get("value") or 0) for row in carried),
    }


def exobiology_lifetime_earned(events: list[dict[str, Any]] | None) -> int:
    """Total credits actually banked from Exobiology sales, across the
    whole career - every ``SellOrganicData`` event's ``Value`` plus
    ``Bonus``. Unlike ``exobiology_summary()``'s ``bankedValue`` (the
    catalog's own value for every analysed species, sold or not), this is
    only what Frontier has actually paid out at a Vista Genomics terminal.
    """
    total = 0
    for event in events or []:
        if not isinstance(event, dict) or event.get("event") != "SellOrganicData":
            continue
        for row in event.get("BioData") or []:
            if isinstance(row, dict):
                total += int(row.get("Value") or 0) + int(row.get("Bonus") or 0)
    return total


def best_find(findings: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The single highest-value completed, catalog-known species found -
    a personal-record highlight.

    Excludes unrecognized species (``valueKnown`` False, reported as 0)
    so an uncatalogued find never looks like a record by default.
    """
    candidates = [
        row for row in findings or []
        if isinstance(row, dict) and row.get("complete") and row.get("valueKnown")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: int(row.get("value") or 0))


# --- Landing targets: which scanned bodies are worth visiting, and what
# is likely there before a single sample is taken. ------------------------

BIOLOGICAL_SIGNAL_TYPE = "$SAA_SignalType_Biological;"
_GRAVITY_G_IN_MS2 = 9.80665
_ATMOSPHERES_IN_PASCALS = 101325.0

# Fields this engine can verify from Journal Scan data. Every other
# ruleset field (regions, system, nebula, tuber, sibling "bodies",
# precise parent-star requirements) is intentionally NOT enforced - an
# unverifiable condition is treated as satisfied rather than as a reason
# to hide a genuinely possible species. That makes a prediction only
# ever over-inclusive, never a false "definitely not here".
_VERIFIABLE_RULESET_FIELDS = frozenset({
    "atmosphere", "min_gravity", "max_gravity", "min_temperature",
    "max_temperature", "min_pressure", "max_pressure", "body_type",
    "volcanism", "min_orbital_period", "max_orbital_period",
})


def _ruleset_matches(ruleset: dict[str, Any], body: dict[str, Any]) -> bool:
    atmosphere = ruleset.get("atmosphere")
    if atmosphere and body.get("atmosphereType") not in atmosphere:
        return False
    gravity_g = body.get("gravityG")
    if gravity_g is not None:
        if "min_gravity" in ruleset and gravity_g < ruleset["min_gravity"]:
            return False
        if "max_gravity" in ruleset and gravity_g > ruleset["max_gravity"]:
            return False
    temperature = body.get("temperatureK")
    if temperature is not None:
        if "min_temperature" in ruleset and temperature < ruleset["min_temperature"]:
            return False
        if "max_temperature" in ruleset and temperature > ruleset["max_temperature"]:
            return False
    pressure = body.get("pressureAtm")
    if pressure is not None:
        if "min_pressure" in ruleset and pressure < ruleset["min_pressure"]:
            return False
        if "max_pressure" in ruleset and pressure > ruleset["max_pressure"]:
            return False
    body_type = ruleset.get("body_type")
    if body_type and body.get("planetClass") not in body_type:
        return False
    volcanism_rule = ruleset.get("volcanism")
    if volcanism_rule and volcanism_rule != "Any":
        volcanism_text = str(body.get("volcanism") or "").casefold()
        if volcanism_rule == "None":
            if volcanism_text:
                return False
        else:
            keywords = volcanism_rule if isinstance(volcanism_rule, list) else [volcanism_rule]
            if not any(str(word).casefold() in volcanism_text for word in keywords):
                return False
    orbital_period = body.get("orbitalPeriodS")
    if orbital_period is not None:
        if "min_orbital_period" in ruleset and orbital_period < ruleset["min_orbital_period"]:
            return False
        if "max_orbital_period" in ruleset and orbital_period > ruleset["max_orbital_period"]:
            return False
    return True


def _species_candidates(
    body: dict[str, Any], species_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        row for row in species_catalog
        if isinstance(row, dict)
        and any(_ruleset_matches(rs, body) for rs in row.get("rulesets") or [{}])
    ]


def scanned_bodies(events: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Fold ``Scan`` events for planets into per-body physical properties.

    Keyed by ``"{SystemAddress}:{BodyID}"``. Star scans (no ``PlanetClass``)
    are skipped - biological signals never occur on a star.
    """
    bodies: dict[str, dict[str, Any]] = {}
    for event in events or []:
        if (
            not isinstance(event, dict) or event.get("event") != "Scan"
            or "PlanetClass" not in event
        ):
            continue
        key = f"{event.get('SystemAddress')}:{event.get('BodyID')}"
        gravity = event.get("SurfaceGravity")
        temperature = event.get("SurfaceTemperature")
        pressure = event.get("SurfacePressure")
        orbital_period = event.get("OrbitalPeriod")
        bodies[key] = {
            "systemAddress": event.get("SystemAddress"),
            "bodyId": event.get("BodyID"),
            "bodyName": str(event.get("BodyName") or ""),
            "starSystem": str(event.get("StarSystem") or ""),
            "planetClass": str(event.get("PlanetClass") or ""),
            "atmosphereType": str(event.get("AtmosphereType") or "None"),
            "volcanism": str(event.get("Volcanism") or ""),
            "gravityG": (
                float(gravity) / _GRAVITY_G_IN_MS2 if gravity is not None else None
            ),
            "temperatureK": float(temperature) if temperature is not None else None,
            "pressureAtm": (
                float(pressure) / _ATMOSPHERES_IN_PASCALS if pressure is not None else None
            ),
            "orbitalPeriodS": float(orbital_period) if orbital_period is not None else None,
            "landable": bool(event.get("Landable")),
            "distanceLs": float(event.get("DistanceFromArrivalLS") or 0.0),
        }
    return bodies


def footfalled_bodies(events: list[dict[str, Any]] | None) -> set[tuple[object, object]]:
    """(SystemAddress, BodyID) pairs the Commander has personally touched
    down on, ever.

    This is the only footfall fact a local Journal can answer. Whether
    any OTHER Commander has already landed there first - the fact that
    actually decides the First Footfall bonus - is not knowable locally
    at all; see the ``firstFootfallPossible`` note on ``landing_targets``.
    """
    result: set[tuple[object, object]] = set()
    for event in events or []:
        if (
            not isinstance(event, dict) or event.get("event") != "Touchdown"
            or event.get("PlayerControlled") is False
        ):
            continue
        address, body_id = event.get("SystemAddress"), event.get("BodyID")
        if address is not None and body_id is not None:
            result.add((address, body_id))
    return result


def populated_systems(events: list[dict[str, Any]] | None) -> set[object]:
    """System addresses with any recorded population.

    First Footfall never applies there - a populated system's bodies are
    already settled. Derived from every ``FSDJump``/``Location``/
    ``CarrierJump`` event ever, not just the current one, so a target
    body in a system visited long ago is still correctly judged.
    """
    result: set[object] = set()
    for event in events or []:
        if (
            not isinstance(event, dict)
            or event.get("event") not in ("FSDJump", "Location", "CarrierJump")
        ):
            continue
        population = event.get("Population")
        address = event.get("SystemAddress")
        if isinstance(population, (int, float)) and population > 0 and address is not None:
            result.add(address)
    return result


def _all_body_targets(
    events: list[dict[str, Any]], species_catalog: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Every scanned body with a recorded biological signal, keyed by
    ``"{SystemAddress}:{BodyID}"`` - including one with zero remaining
    candidates (fully claimed already).

    ``landing_targets()`` filters those out, since there is nothing left
    to suggest visiting; ``remaining_signals_at_body()`` needs to tell
    "nothing detected here" apart from "already fully explored here", so
    it needs them kept.
    """
    bodies = scanned_bodies(events)

    signal_counts: dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("event") != "FSSBodySignals":
            continue
        key = f"{event.get('SystemAddress')}:{event.get('BodyID')}"
        count = sum(
            int(signal.get("Count") or 0)
            for signal in event.get("Signals") or []
            if isinstance(signal, dict) and signal.get("Type") == BIOLOGICAL_SIGNAL_TYPE
        )
        if count:
            signal_counts[key] = max(signal_counts.get(key, 0), count)

    confirmed_genuses: dict[str, set[str]] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("event") != "SAASignalsFound":
            continue
        genuses = {
            str(entry.get("Genus"))
            for entry in event.get("Genuses") or []
            if isinstance(entry, dict) and entry.get("Genus")
        }
        if genuses:
            key = f"{event.get('SystemAddress')}:{event.get('BodyID')}"
            confirmed_genuses.setdefault(key, set()).update(genuses)

    # Only a fully paid-out species (Log + Sample + Analyse) has nothing
    # left to gain from a return visit. A genus that is merely Logged (or
    # Logged+Sampled) still needs its remaining steps - it must stay a
    # visible target, not disappear the moment the Commander starts on it.
    # The key must include the system address: Frontier's BodyID is only
    # unique within one system, and plenty of systems have a body #8 -
    # without it, a completed find in one system would wrongly blank out
    # an unrelated, still-open one sharing that same body number elsewhere.
    already_found = {
        (row["systemAddress"], row["body"], row["genus"])
        for row in exobiology_findings(events, species_catalog)
        if row["complete"]
    }
    footfalled = footfalled_bodies(events)
    populated = populated_systems(events)

    targets: dict[str, dict[str, Any]] = {}
    for key, signal_count in signal_counts.items():
        body = bodies.get(key)
        if body is None:
            continue
        confirmed = confirmed_genuses.get(key)
        if confirmed:
            candidates = [
                row for row in species_catalog if row.get("genusCodexKey") in confirmed
            ]
            confidence = "confirmed_genus"
        else:
            candidates = _species_candidates(body, species_catalog)
            confidence = "predicted"
        body_id_str = str(body["bodyId"])
        candidates = [
            row for row in candidates
            if (body["systemAddress"], body_id_str, row.get("genusCodexKey")) not in already_found
        ]
        best_value = max((int(row.get("value") or 0) for row in candidates), default=0)
        total_value = sum(int(row.get("value") or 0) for row in candidates)
        targets[key] = {
            "systemAddress": body["systemAddress"],
            "bodyId": body["bodyId"],
            "bodyName": body["bodyName"] or f"Body {body['bodyId']}",
            "starSystem": body["starSystem"],
            "planetClass": body["planetClass"],
            "landable": body["landable"],
            "distanceLs": body["distanceLs"],
            "signalCount": signal_count,
            "confidence": confidence,
            "candidateCount": len(candidates),
            "candidates": sorted(
                [{
                    "genus": row.get("genusCodexKey"),
                    "name": row.get("name"),
                    "value": int(row.get("value") or 0),
                } for row in candidates],
                key=lambda row: -row["value"],
            )[:8],
            "bestValue": best_value,
            "totalPotentialValue": total_value,
            # Never a promise, only a pre-filter: this Commander has not
            # personally landed here yet and the system carries no
            # recorded population. Whether anyone else already has is
            # unknowable from a local Journal - see footfalled_bodies().
            "firstFootfallPossible": (
                (body["systemAddress"], body["bodyId"]) not in footfalled
                and body["systemAddress"] not in populated
            ),
        }
    return targets


def landing_targets(
    events: list[dict[str, Any]] | None,
    species_catalog: list[dict[str, Any]] | None,
    current_system_address: object = None,
) -> list[dict[str, Any]]:
    """Rank scanned bodies with unclaimed biological signals to visit next.

    Combines three Journal sources: ``FSSBodySignals`` (how many
    biological signals a body has, from an FSS honk alone),
    ``SAASignalsFound`` (the confirmed genus list, once a Detailed
    Surface Scan probe has read that body), and ``Scan`` (the body's
    physical properties, used to predict candidate species via the
    catalog's rulesets when no DSS confirmation exists yet). A body
    already fully sampled for every signal it has is left out - nothing
    left to go there for.

    ``current_system_address`` (the Commander's present ``SystemAddress``)
    is optional context, not a filter: every known target across the
    whole Journal is still returned, but one in the current system is
    ranked to the top - "what should I check out right here" after an
    FSS honk, without discarding older, still-unvisited leads elsewhere.
    """
    events = events or []
    species_catalog = [row for row in species_catalog or [] if isinstance(row, dict)]
    targets = []
    for target in _all_body_targets(events, species_catalog).values():
        if target["candidateCount"] == 0:
            continue
        row = dict(target)
        row["inCurrentSystem"] = (
            current_system_address is not None
            and row["systemAddress"] == current_system_address
        )
        targets.append(row)
    targets.sort(key=lambda row: (
        not row["inCurrentSystem"],
        row["confidence"] != "confirmed_genus",
        -row["bestValue"], row["distanceLs"],
    ))
    return targets


def remaining_signals_at_body(
    events: list[dict[str, Any]] | None,
    species_catalog: list[dict[str, Any]] | None,
    status: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """How many of the biological signals at the Commander's current body
    (matched by ``Status.json``'s own ``BodyName``, the same identity the
    live distance check uses) are still unclaimed.

    Returns ``None`` when the current body has no detected biological
    signal at all - "nothing to report" - as opposed to a genuine ``0``
    once everything detected there has already been fully analysed.
    """
    status = status if isinstance(status, dict) else {}
    body_name = status.get("BodyName")
    if not body_name:
        return None
    events = events or []
    species_catalog = [row for row in species_catalog or [] if isinstance(row, dict)]
    for target in _all_body_targets(events, species_catalog).values():
        if target["bodyName"] == body_name:
            return {
                "totalSignals": target["signalCount"],
                "remaining": target["candidateCount"],
            }
    return None


# --- Live "distance to next sample" check: on-foot geometry, not Journal
# replay - the game never records where a sample was taken, only that it
# was. -----------------------------------------------------------------

def great_circle_distance_m(
    lat1: float, lon1: float, lat2: float, lon2: float, radius_m: float,
) -> float:
    """Surface distance in meters between two lat/long points on a body.

    Elite Dangerous' own colony-range check is exactly this: great-circle
    distance on the body's own sphere (``PlanetRadius`` from ``Status.json``),
    not a flat-map approximation.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_m * math.asin(min(1.0, math.sqrt(a)))


def colony_range_for_genus(
    genus_codex_key: str,
    species_catalog: list[dict[str, Any]] | None,
    colony_ranges: dict[str, Any] | None,
) -> float | None:
    """Minimum required distance (meters) between samples of this genus.

    ``species_catalog`` maps a raw Codex genus key to the catalog's own
    lowercase module name (e.g. "aleoida"); ``colony_ranges`` is keyed by
    that same module name. Returns ``None`` when either lookup misses -
    an unrecognized or not-yet-catalogued genus never blocks the rest of
    the check, it just has no known requirement to show.
    """
    module_name = next(
        (
            row.get("genus") for row in species_catalog or []
            if isinstance(row, dict) and row.get("genusCodexKey") == genus_codex_key
        ),
        None,
    )
    if not module_name:
        return None
    value = (colony_ranges or {}).get(module_name)
    return float(value) if isinstance(value, (int, float)) else None


def exobiology_distance_check(
    findings: list[dict[str, Any]] | None,
    step_positions: dict[tuple, dict[str, Any]] | None,
    species_catalog: list[dict[str, Any]] | None,
    colony_ranges: dict[str, Any] | None,
    current_system_address: object,
    status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Live "how far to the next sample" check for the one species the
    Commander is actively working on right now.

    ``step_positions`` is the caller's own in-memory, never-persisted
    record of where the Commander was standing at each find's most recent
    step - Journal replay has no position data to reconstruct this from,
    so this only ever covers scans made while the app has been running.
    ``status`` is the raw ``Status.json`` payload; only used while the
    Commander is on foot or in an SRV with a body underfoot.
    """
    status = status if isinstance(status, dict) else {}
    body_name = status.get("BodyName")
    lat, lon = status.get("Latitude"), status.get("Longitude")
    if (
        not body_name
        or not isinstance(lat, (int, float))
        or not isinstance(lon, (int, float))
    ):
        return {}
    candidates = [
        row for row in findings or []
        if isinstance(row, dict) and not row.get("complete")
        and row.get("systemAddress") == current_system_address
    ]
    if not candidates:
        return {}
    candidates.sort(key=lambda row: row.get("lastSeen") or "", reverse=True)
    row = candidates[0]
    key = (row.get("systemAddress"), row.get("body"), row.get("genus"), row.get("species"))
    baseline = (step_positions or {}).get(key)
    if not baseline or baseline.get("bodyName") != body_name:
        return {}
    required = colony_range_for_genus(row["genus"], species_catalog, colony_ranges)
    if required is None:
        return {}
    distance = great_circle_distance_m(
        float(lat), float(lon),
        float(baseline["lat"]), float(baseline["lon"]),
        float(baseline["radius"]),
    )
    return {
        "displayName": row.get("displayName"),
        "genusDisplay": row.get("genusDisplay"),
        "nextStep": "Analyse" if row.get("samplesDone") == 2 else "Sample",
        "distanceM": round(distance),
        "requiredM": round(required),
        "ready": distance >= required,
    }
