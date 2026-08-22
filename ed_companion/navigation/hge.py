import math
import re
from datetime import datetime, timezone


STATE_MATERIALS = {
    "outbreak": (("pharmaceuticalisolators", 0.90),),
    "civil unrest": (("improvisedcomponents", 0.88),),
    "civilunrest": (("improvisedcomponents", 0.88),),
    "war": (
        ("militarygradealloys", 0.72),
        ("militarysupercapacitors", 0.72),
    ),
    "civil war": (
        ("militarygradealloys", 0.72),
        ("militarysupercapacitors", 0.72),
    ),
    "civilwar": (
        ("militarygradealloys", 0.72),
        ("militarysupercapacitors", 0.72),
    ),
    "boom": (
        ("protoheatradiators", 0.62),
        ("protoradiolicalloys", 0.62),
        ("protolightalloys", 0.62),
    ),
}

ALLEGIANCE_MATERIALS = {
    "federation": (
        ("fedcorecomposites", 0.86),
        ("fedproprietarycomposites", 0.82),
    ),
    "empire": (("imperialshielding", 0.90),),
}

HGE_MATERIAL_KEYS = frozenset(
    material
    for candidates in (
        list(STATE_MATERIALS.values()) + list(ALLEGIANCE_MATERIALS.values())
    )
    for material, _confidence in candidates
)

FIND_TYPES = {
    "HGE": "High Grade Emissions",
    "CONFLICT_ZONE": "Conflict Zone",
    "SEEKING_MEDS": "Seeking Meds",
    "SEEKING_FOODS": "Seeking Foods",
}

STATE_FIND_TYPES = {
    "war": ("CONFLICT_ZONE",),
    "civil war": ("CONFLICT_ZONE",),
    "civilwar": ("CONFLICT_ZONE",),
    "outbreak": ("SEEKING_MEDS",),
    "famine": ("SEEKING_FOODS",),
}

EVIDENCE_RANK = {
    "BGS_PREDICTION": 0,
    "EDDN_SIGNAL": 1,
    "LOCAL_JOURNAL": 2,
    "ENTERED": 3,
}


def is_hge_material(material):
    """Return whether the material is part of the supported HGE source map."""
    return _fold(material).replace(" ", "") in HGE_MATERIAL_KEYS


def _fold(value):
    return str(value or "").replace("_", " ").strip().casefold()


def readable_faction_state(value):
    """Convert Elite localization tokens into human-readable faction states."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.strip("$;")
    text = re.sub(r"^FactionState_", "", text, flags=re.IGNORECASE)
    text = re.sub(r"_desc$", "", text, flags=re.IGNORECASE)
    text = text.replace("_", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    folded = " ".join(text.split()).casefold()
    aliases = {
        "civilwar": "Civil War",
        "civil war": "Civil War",
        "civilunrest": "Civil Unrest",
        "civil unrest": "Civil Unrest",
        "none": "No special faction state",
    }
    return aliases.get(folded, folded.title())


def infer_hge_materials(spawning_state="", allegiance=""):
    """Return probable HGE materials without claiming deterministic contents."""
    # A material-defining active state is more specific than broad allegiance.
    # Allegiance remains the fallback when no supported state family is known.
    candidates = list(STATE_MATERIALS.get(_fold(spawning_state), ()))
    if not candidates:
        candidates.extend(ALLEGIANCE_MATERIALS.get(_fold(allegiance), ()))
    best = {}
    for material, confidence in candidates:
        best[material] = max(best.get(material, 0.0), float(confidence))
    return [
        {"material": material, "confidence": confidence}
        for material, confidence in sorted(
            best.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def hge_match_class(evidence_kind, materials):
    """Classify prediction precision without converting probability to certainty."""
    if str(evidence_kind or "") == "BGS_PREDICTION":
        return "BGS POSSIBLE"
    count = len(list(materials or []))
    if count == 1:
        return "EXACT MATCH"
    if count > 1:
        return "FAMILY MATCH"
    return "UNRESOLVED"


def _is_hge(signal):
    """Classify HGE with structured USS type ahead of unreliable display names."""
    uss_type = str(signal.get("USSType") or "").strip()
    fallback_type = str(signal.get("Type") or "").strip()
    authoritative_type = uss_type or (
        fallback_type if "uss_type_" in fallback_type.casefold() else ""
    )
    if authoritative_type:
        return (
            authoritative_type.casefold()
            == "$uss_type_veryvaluablesalvage;".casefold()
        )
    names = " ".join(str(signal.get(key) or "") for key in (
        "SignalName", "SignalName_Localised", "USSType_Localised",
        "Type_Localised",
    )).replace(" ", "").casefold()
    return "highgradeemissions" in names


def _signal_find_type(signal):
    """Classify supported signals without guessing beyond known names."""
    if _is_hge(signal):
        return "HGE", "UNKNOWN"
    text = " ".join(str(signal.get(key) or "") for key in (
        "SignalName", "SignalName_Localised", "USSType", "USSType_Localised",
        "Type", "Type_Localised",
    )).replace("_", " ").casefold()
    if "conflict zone" in text or "conflictzone" in text:
        intensity = "UNKNOWN"
        for label, value in (("low", "LOW"), ("medium", "MEDIUM"),
                             ("high", "HIGH")):
            if label in text:
                intensity = value
                break
        return "CONFLICT_ZONE", intensity
    if "seeking meds" in text or "seekingmeds" in text:
        return "SEEKING_MEDS", "UNKNOWN"
    if "seeking foods" in text or "seekingfoods" in text:
        return "SEEKING_FOODS", "UNKNOWN"
    return "", "UNKNOWN"


def _find_row(base, find_type, evidence_kind, intensity="UNKNOWN"):
    row = dict(base)
    row.update({
        "find_type": find_type,
        "find_label": FIND_TYPES[find_type],
        "evidence_kind": evidence_kind,
        "intensity": intensity,
        "candidate_only": evidence_kind == "BGS_PREDICTION",
    })
    if find_type != "HGE":
        row["materials"] = []
    return row


def extract_hge_sightings(payload, received_at=None, faction_allegiances=None):
    """Extract normalized HGE sightings from one validated EDDN relay message."""
    if not isinstance(payload, dict):
        return []
    schema = str(payload.get("$schemaRef") or "").casefold()
    if "/fsssignaldiscovered/" not in schema:
        return []
    message = payload.get("message") or {}
    system = str(message.get("StarSystem") or "").strip()
    star_pos = message.get("StarPos")
    if not system or not isinstance(star_pos, list) or len(star_pos) != 3:
        return []
    received_at = received_at or datetime.now(timezone.utc).isoformat()
    faction_allegiances = faction_allegiances or {}
    result = []
    for signal in message.get("signals") or []:
        if not isinstance(signal, dict) or not _is_hge(signal):
            continue
        faction = str(signal.get("SpawningFaction") or "").strip()
        raw_state = str(signal.get("SpawningState") or "").strip()
        state = readable_faction_state(raw_state)
        allegiance = str(faction_allegiances.get(faction) or "").strip()
        result.append({
            "system": system,
            "system_address": message.get("SystemAddress"),
            "star_pos": [float(value) for value in star_pos],
            "faction": faction,
            "state": state,
            "state_raw": raw_state,
            "allegiance": allegiance,
            "signal_timestamp": signal.get("timestamp") or message.get("timestamp"),
            "time_remaining": int(signal.get("TimeRemaining", 0) or 0),
            "received_at": received_at,
            "materials": infer_hge_materials(state, allegiance),
        })
    return result


def extract_signal_finds(payload, received_at=None):
    """Extract every supported state-dependent signal from an EDDN frame."""
    if not isinstance(payload, dict):
        return []
    schema = str(payload.get("$schemaRef") or "").casefold()
    if "/fsssignaldiscovered/" not in schema:
        return []
    message = payload.get("message") or {}
    system = str(message.get("StarSystem") or "").strip()
    star_pos = message.get("StarPos")
    if not system or not isinstance(star_pos, list) or len(star_pos) != 3:
        return []
    received_at = received_at or datetime.now(timezone.utc).isoformat()
    rows = []
    for signal in message.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        find_type, intensity = _signal_find_type(signal)
        if not find_type:
            continue
        raw_state = str(signal.get("SpawningState") or "").strip()
        state = readable_faction_state(raw_state)
        base = {
            "system": system,
            "system_address": message.get("SystemAddress"),
            "star_pos": [float(value) for value in star_pos],
            "faction": str(signal.get("SpawningFaction") or "").strip(),
            "state": state,
            "state_raw": raw_state,
            "allegiance": "",
            "signal_timestamp": signal.get("timestamp") or message.get("timestamp"),
            "time_remaining": int(float(signal.get("TimeRemaining", 0) or 0)),
            "received_at": received_at,
            "materials": infer_hge_materials(state, "") if find_type == "HGE" else [],
            "source": "EDDN FSS",
        }
        rows.append(_find_row(base, find_type, "EDDN_SIGNAL", intensity))
    return rows


def extract_system_bgs_candidates(payload, received_at=None):
    """Extract HGE prerequisite observations from system-wide EDDN Journal data."""
    if not isinstance(payload, dict):
        return []
    schema = str(payload.get("$schemaRef") or "").casefold()
    if "/journal/1" not in schema:
        return []
    message = payload.get("message") or {}
    if str(message.get("event") or "") not in {
        "FSDJump", "Location", "CarrierJump"
    }:
        return []
    system = str(message.get("StarSystem") or "").strip()
    star_pos = message.get("StarPos")
    if not system or not isinstance(star_pos, list) or len(star_pos) != 3:
        return []
    observed_at = str(message.get("timestamp") or received_at or "")
    if not observed_at:
        observed_at = datetime.now(timezone.utc).isoformat()
    system_allegiance = str(message.get("SystemAllegiance") or "").strip()
    result = []
    for faction in message.get("Factions") or []:
        if not isinstance(faction, dict):
            continue
        faction_name = str(faction.get("Name") or "").strip()
        allegiance = str(
            faction.get("Allegiance") or system_allegiance
        ).strip()
        states = []
        for item in faction.get("ActiveStates") or []:
            state = item.get("State") if isinstance(item, dict) else item
            readable = readable_faction_state(state)
            if readable:
                states.append(readable)
        if not states:
            readable = readable_faction_state(faction.get("FactionState"))
            if readable:
                states.append(readable)
        # Allegiance alone can predict Federation/Empire HGE material families.
        if not states:
            states = [""]
        for state in dict.fromkeys(states):
            materials = infer_hge_materials(state, allegiance)
            if not materials:
                continue
            result.append({
                "system": system,
                "system_address": message.get("SystemAddress"),
                "star_pos": [float(value) for value in star_pos],
                "faction": faction_name,
                "state": state,
                "state_raw": state,
                "allegiance": allegiance,
                "signal_timestamp": observed_at,
                "received_at": received_at or observed_at,
                "time_remaining": 0,
                "materials": materials,
                "source": "EDDN System BGS",
                "candidate_only": True,
            })
    return result


def extract_system_find_candidates(payload, received_at=None):
    """Return HGE-compatible rows plus predictions for supported find types."""
    hge_rows = [
        _find_row(row, "HGE", "BGS_PREDICTION")
        for row in extract_system_bgs_candidates(payload, received_at)
    ]
    if not isinstance(payload, dict):
        return hge_rows
    schema = str(payload.get("$schemaRef") or "").casefold()
    message = payload.get("message") or {}
    if "/journal/1" not in schema or str(message.get("event") or "") not in {
        "FSDJump", "Location", "CarrierJump"
    }:
        return hge_rows
    system = str(message.get("StarSystem") or "").strip()
    star_pos = message.get("StarPos")
    if not system or not isinstance(star_pos, list) or len(star_pos) != 3:
        return hge_rows
    observed_at = str(message.get("timestamp") or received_at or "") or datetime.now(
        timezone.utc
    ).isoformat()
    result = list(hge_rows)
    for faction in message.get("Factions") or []:
        if not isinstance(faction, dict):
            continue
        raw_states = [
            item.get("State") if isinstance(item, dict) else item
            for item in faction.get("ActiveStates") or []
        ]
        if not raw_states:
            raw_states = [faction.get("FactionState")]
        for state in dict.fromkeys(
            readable_faction_state(value) for value in raw_states if value
        ):
            for find_type in STATE_FIND_TYPES.get(_fold(state), ()):
                base = {
                    "system": system,
                    "system_address": message.get("SystemAddress"),
                    "star_pos": [float(value) for value in star_pos],
                    "faction": str(faction.get("Name") or "").strip(),
                    "state": state,
                    "state_raw": state,
                    "allegiance": str(
                        faction.get("Allegiance") or message.get("SystemAllegiance") or ""
                    ).strip(),
                    "signal_timestamp": observed_at,
                    "received_at": received_at or observed_at,
                    "time_remaining": 0,
                    "materials": [],
                    "source": "EDDN System BGS",
                }
                result.append(_find_row(base, find_type, "BGS_PREDICTION"))
    return result


def extract_system_bgs_snapshot(payload, received_at=None):
    """Return system identity plus its complete current relevant BGS subset."""
    if not isinstance(payload, dict):
        return None
    schema = str(payload.get("$schemaRef") or "").casefold()
    message = payload.get("message") or {}
    if (
        "/journal/1" not in schema
        or str(message.get("event") or "") not in {
            "FSDJump", "Location", "CarrierJump"
        }
    ):
        return None
    system = str(message.get("StarSystem") or "").strip()
    star_pos = message.get("StarPos")
    if not system or not isinstance(star_pos, list) or len(star_pos) != 3:
        return None
    observed_at = str(message.get("timestamp") or received_at or "")
    if not observed_at:
        observed_at = datetime.now(timezone.utc).isoformat()
    return {
        "system": system,
        "system_address": message.get("SystemAddress"),
        "observed_at": observed_at,
        "observations": extract_system_find_candidates(payload, received_at),
    }


def replace_system_bgs_snapshot(observations, snapshot, limit=10000):
    """Atomically replace one system's BGS rows unless the snapshot is older."""
    current = list(observations or [])
    if not snapshot:
        return current, False
    address = snapshot.get("system_address")
    system = _fold(snapshot.get("system"))

    def belongs(row):
        if row.get("source") != "EDDN System BGS":
            return False
        if address is not None and row.get("system_address") is not None:
            return row.get("system_address") == address
        return _fold(row.get("system")) == system

    existing = [row for row in current if belongs(row)]
    incoming_time = _parse_timestamp(snapshot.get("observed_at"))
    existing_times = [
        parsed for parsed in (
            _parse_timestamp(row.get("signal_timestamp") or row.get("received_at"))
            for row in existing
        ) if parsed is not None
    ]
    if existing_times and incoming_time is not None and max(existing_times) > incoming_time:
        return current, False
    replaced = [row for row in current if not belongs(row)]
    replaced.extend(snapshot.get("observations") or [])
    return replaced[-max(1, int(limit)):], True


def apply_system_bgs_snapshot_batch(observations, snapshots, limit=10000):
    """Apply the newest snapshot per system with one cache traversal."""
    current = list(observations or [])

    def identity(source):
        address = source.get("system_address")
        return ("address", address) if address is not None else (
            "name", _fold(source.get("system"))
        )

    newest = {}
    for snapshot in snapshots or []:
        if not snapshot:
            continue
        key = identity(snapshot)
        old = newest.get(key)
        old_time = _parse_timestamp(old.get("observed_at")) if old else None
        new_time = _parse_timestamp(snapshot.get("observed_at"))
        if old is None or old_time is None or (
            new_time is not None and new_time >= old_time
        ):
            newest[key] = snapshot
    if not newest:
        return current, 0

    cached_latest = {}
    for row in current:
        if row.get("source") != "EDDN System BGS":
            continue
        key = identity(row)
        timestamp = _parse_timestamp(
            row.get("signal_timestamp") or row.get("received_at")
        )
        if timestamp is not None and (
            key not in cached_latest or timestamp > cached_latest[key]
        ):
            cached_latest[key] = timestamp

    accepted = {}
    for key, snapshot in newest.items():
        incoming = _parse_timestamp(snapshot.get("observed_at"))
        cached = cached_latest.get(key)
        if cached is None or incoming is None or incoming >= cached:
            accepted[key] = snapshot
    if not accepted:
        return current, 0

    updated = [
        row for row in current
        if not (
            row.get("source") == "EDDN System BGS"
            and identity(row) in accepted
        )
    ]
    for snapshot in accepted.values():
        updated.extend(snapshot.get("observations") or [])
    return updated[-max(1, int(limit)):], len(accepted)


def merge_hge_observation_batch(observations, additions, limit=10000):
    """Deduplicate a group of FSS observations with one cache traversal."""
    current = list(observations or [])
    additions = list(additions or [])
    if not additions:
        return current, False
    keys = {
        (row.get("system"), row.get("signal_timestamp"),
         row.get("faction"), row.get("state"), row.get("find_type", "HGE"))
        for row in additions
    }
    updated = [
        row for row in current
        if (row.get("system"), row.get("signal_timestamp"),
            row.get("faction"), row.get("state"),
            row.get("find_type", "HGE")) not in keys
    ]
    updated.extend(additions)
    return updated[-max(1, int(limit)):], True


def compact_hge_observations(observations, now=None, max_age_seconds=86400):
    """Drop expired signals and old intelligence; never extend a reported life."""
    now = now or datetime.now(timezone.utc)
    kept = []
    removed = 0
    for row in observations or []:
        if row.get("self_test"):
            removed += 1
            continue
        timestamp = _parse_timestamp(
            row.get("signal_timestamp") or row.get("received_at")
        )
        age = (now - timestamp).total_seconds() if timestamp is not None else None
        reported_lifetime = int(row.get("time_remaining", 0) or 0)
        evidence = str(row.get("evidence_kind") or "")
        expired_signal = (
            age is not None
            and evidence in {"EDDN_SIGNAL", "LOCAL_JOURNAL", "ENTERED"}
            and reported_lifetime > 0
            and age >= reported_lifetime
        )
        if expired_signal or (age is not None and age > max_age_seconds):
            removed += 1
        else:
            kept.append(row)
    return kept, removed


def purge_legacy_signal_classifications(observations):
    """One-time migration: retain BGS facts, discard signals classified by old rules."""
    kept = []
    removed = 0
    for row in observations or []:
        evidence = str(row.get("evidence_kind") or "")
        is_signal = (
            evidence in {"EDDN_SIGNAL", "LOCAL_JOURNAL", "ENTERED"}
            or int(row.get("time_remaining", 0) or 0) > 0
        )
        if is_signal:
            removed += 1
        else:
            kept.append(row)
    return kept, removed


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _distance(left, right):
    if not (
        isinstance(left, (list, tuple)) and len(left) == 3
        and isinstance(right, (list, tuple)) and len(right) == 3
    ):
        return None
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def intelligence_freshness(evidence_kind, age_seconds, remaining_seconds=0):
    """Return an honest freshness label shared by every State Finds view."""
    evidence = str(evidence_kind or "BGS_PREDICTION")
    age = max(0.0, float(age_seconds or 0.0))
    remaining = max(0, int(remaining_seconds or 0))
    if evidence in {"EDDN_SIGNAL", "LOCAL_JOURNAL", "ENTERED"} and remaining > 0:
        return "LIVE"
    if age <= 3600:
        return "RECENT"
    return "STALE"


def rank_hge_sightings(
    sightings, material, current_pos=None, now=None, max_age_seconds=2700
):
    """Rank matching live sightings by confidence, freshness and distance."""
    now = now or datetime.now(timezone.utc)
    ranked = []
    for sighting in rank_all_hge_sightings(
        sightings, current_pos=current_pos, now=now
    ):
        candidate = next(
            (
                item for item in sighting.get("materials", [])
                if item.get("material") == material
            ),
            None,
        )
        if not candidate:
            continue
        age = float(sighting.get("age_seconds", max_age_seconds))
        if age > max_age_seconds:
            continue
        freshness = max(0.20, 1.0 - age / max_age_seconds)
        confidence = float(candidate.get("confidence", 0.0)) * freshness
        distance = sighting.get("distance_ly")
        score = confidence * 100.0 - (distance or 0.0) * 0.08
        row = dict(sighting)
        row.update({
            "age_seconds": round(age),
            "distance_ly": distance,
            "confidence": confidence,
            "score": score,
        })
        ranked.append(row)
    return sorted(
        ranked,
        key=lambda row: (
            -row["score"],
            row["distance_ly"] if row["distance_ly"] is not None else float("inf"),
        ),
    )


def rank_all_hge_sightings(
    sightings, current_pos=None, now=None
):
    """Rank only HGEs with a reported, still-positive lifetime.

    EDDN reports without TimeRemaining can establish that a signal was seen,
    but cannot establish that it is still active. They must never receive an
    invented fallback lifetime.
    """
    now = now or datetime.now(timezone.utc)
    deduplicated = {}
    for sighting in sightings or []:
        if sighting.get("self_test"):
            continue
        reported_lifetime = int(sighting.get("time_remaining", 0) or 0)
        if reported_lifetime <= 0:
            continue
        timestamp = _parse_timestamp(
            sighting.get("signal_timestamp") or sighting.get("received_at")
        )
        if timestamp is None:
            continue
        age = max(0.0, (now - timestamp).total_seconds())
        remaining = max(0, reported_lifetime - round(age))
        if remaining <= 0:
            continue
        distance = _distance(current_pos, sighting.get("star_pos"))
        row = dict(sighting)
        row.update({
            "age_seconds": round(age),
            "remaining_seconds": remaining,
            "distance_ly": distance,
            "lifetime_verified": True,
        })
        key = (
            sighting.get("system_address") or _fold(sighting.get("system")),
            _fold(sighting.get("faction")),
            _fold(sighting.get("state_raw") or sighting.get("state")),
        )
        previous = deduplicated.get(key)
        if previous is None or row["remaining_seconds"] > previous["remaining_seconds"]:
            deduplicated[key] = row
    ranked = list(deduplicated.values())
    return sorted(
        ranked,
        key=lambda row: (
            row["distance_ly"] if row["distance_ly"] is not None else float("inf"),
            -row["remaining_seconds"],
            str(row.get("system") or "").casefold(),
        ),
    )


def recent_unverified_hge_summary(
    sightings, now=None, max_age_seconds=2700
):
    """Summarize recent HGE reports that lack a usable lifetime."""
    now = now or datetime.now(timezone.utc)
    reports = 0
    systems = set()
    for sighting in sightings or []:
        if sighting.get("self_test"):
            continue
        if int(sighting.get("time_remaining", 0) or 0) > 0:
            continue
        timestamp = _parse_timestamp(
            sighting.get("signal_timestamp") or sighting.get("received_at")
        )
        if timestamp is None:
            continue
        age = max(0.0, (now - timestamp).total_seconds())
        if age > max_age_seconds:
            continue
        reports += 1
        system = str(sighting.get("system") or "").strip()
        if system:
            systems.add(system.casefold())
    return {"reports": reports, "systems": len(systems)}


def extract_local_hge_sightings(events):
    """Extract locally verified HGEs for the Commander's current system."""
    current = next(({
        "system": str(event.get("StarSystem") or "").strip(),
        "system_address": event.get("SystemAddress"),
    } for event in reversed(events or []) if isinstance(event, dict)
        and event.get("event") in {"Location", "FSDJump", "CarrierJump"}), {})
    sightings = [
        row for row in extract_local_state_finds(events)
        if row.get("find_type") == "HGE"
        and int(row.get("time_remaining", 0) or 0) > 0
        and not row.get("details_unknown")
    ]
    return [
        row for row in sightings
        if (
            row.get("system_address") == current.get("system_address")
            if current.get("system_address") is not None
            else _fold(row.get("system")) == _fold(current.get("system"))
        )
    ]


def extract_local_state_finds(events):
    """Extract local signals, assigning out-of-order rows by SystemAddress."""
    current = {"system": "", "system_address": None, "star_pos": []}
    current_key = None
    contexts = {}
    pending = {}
    rows = []
    latest_row_by_type = {}

    def system_key(address, system=""):
        return (
            ("address", str(address)) if address is not None
            else ("system", _fold(system))
        )

    def append_signal(event, context, allegiances, active):
        find_type, intensity = _signal_find_type(event)
        if not find_type:
            return
        raw_state = str(event.get("SpawningState") or "").strip()
        state = readable_faction_state(raw_state)
        faction = str(event.get("SpawningFaction") or "").strip()
        allegiance = allegiances.get(faction, "")
        base = {
            **context,
            "faction": faction,
            "state": state,
            "state_raw": raw_state,
            "allegiance": allegiance,
            "signal_timestamp": event.get("timestamp"),
            "time_remaining": int(float(event.get("TimeRemaining", 0) or 0)),
            "received_at": event.get("timestamp"),
            "materials": infer_hge_materials(state, allegiance)
            if find_type == "HGE" else [],
            "source": "Local Elite Journal",
            "local_verified": True,
        }
        rows.append(_find_row(base, find_type, "LOCAL_JOURNAL", intensity))
        if active:
            latest_row_by_type[find_type] = len(rows) - 1

    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name in {"Location", "FSDJump", "CarrierJump"}:
            current = {
                "system": str(event.get("StarSystem") or "").strip(),
                "system_address": event.get("SystemAddress"),
                "star_pos": list(event.get("StarPos") or []),
            }
            # A system change ends the association window for a later USSDrop,
            # but it must not erase still-live local evidence from prior systems.
            latest_row_by_type = {}
            allegiances = {
                str(item.get("Name") or ""): str(item.get("Allegiance") or "")
                for item in event.get("Factions", []) or []
                if isinstance(item, dict) and item.get("Name")
            }
            current_key = system_key(
                current.get("system_address"), current.get("system")
            )
            contexts[current_key] = (dict(current), allegiances)
            for buffered in pending.pop(current_key, []):
                append_signal(buffered, current, allegiances, True)
            continue
        if name in {"USSDrop", "SupercruiseDestinationDrop"}:
            find_type, intensity = _signal_find_type(event)
            row_index = latest_row_by_type.get(find_type)
            event_timestamp = str(event.get("timestamp") or "")
            if (
                find_type and current["system"]
                and (
                    row_index is None
                    or (
                        rows[row_index].get("evidence_kind") == "ENTERED"
                        and rows[row_index].get("entered_timestamp")
                        != event_timestamp
                    )
                )
            ):
                # Elite can expose and enter a USS without writing the earlier
                # FSS detail event. Preserve that direct evidence once, but do
                # not guess its faction, state, materials or lifetime.
                base = {
                    **current,
                    "faction": "",
                    "state": "",
                    "state_raw": "",
                    "allegiance": "",
                    "signal_timestamp": event.get("timestamp"),
                    "time_remaining": 0,
                    "received_at": event.get("timestamp"),
                    "materials": [],
                    "source": "Local Elite Journal drop",
                    "local_verified": True,
                    "details_unknown": True,
                }
                rows.append(_find_row(base, find_type, "ENTERED", intensity))
                row_index = len(rows) - 1
                latest_row_by_type[find_type] = row_index
            if find_type and row_index is not None:
                row = rows[row_index]
                row["evidence_kind"] = "ENTERED"
                row["entered_timestamp"] = event_timestamp
                if row.get("intensity") == "UNKNOWN":
                    row["intensity"] = intensity
            continue
        if name != "FSSSignalDiscovered":
            continue
        address = event.get("SystemAddress")
        if address is None:
            if current_key is None or not current.get("system"):
                continue
            context, allegiances = contexts.get(current_key, (current, {}))
            append_signal(event, context, allegiances, True)
            continue
        key = system_key(address, current.get("system"))
        if key not in contexts:
            pending.setdefault(key, []).append(event)
            continue
        context, allegiances = contexts.get(key, (current, {}))
        if not context.get("system"):
            continue
        append_signal(event, context, allegiances, key == current_key)
    return rows


def local_state_find_scan_status(events):
    """Describe only the active system's evidenced FSS completeness and signals."""
    source = [event for event in events or [] if isinstance(event, dict)]
    current = {"system": "", "system_address": None, "star_pos": []}
    location_indexes = [
        index for index, event in enumerate(source)
        if event.get("event") in {"Location", "FSDJump", "CarrierJump"}
    ]
    if not location_indexes:
        return {
            **current, "progress": 0.0, "complete": False,
            "scan_timestamp": "", "signal_count": 0, "hge_count": 0,
        }
    latest_index = location_indexes[-1]
    previous_index = location_indexes[-2] if len(location_indexes) > 1 else -1
    location = source[latest_index]
    current = {
        "system": str(location.get("StarSystem") or "").strip(),
        "system_address": location.get("SystemAddress"),
        "star_pos": list(location.get("StarPos") or []),
    }
    progress = 0.0
    scan_timestamp = ""
    signal_count = 0
    hge_count = 0
    for index, event in enumerate(source):
        if index <= previous_index:
            continue
        name = str(event.get("event") or "")
        address = event.get("SystemAddress")
        same_address = (
            address is not None and current["system_address"] is not None
            and address == current["system_address"]
        )
        implicit_current = address is None and index >= latest_index
        if name == "FSSDiscoveryScan" and current["system"]:
            if same_address or implicit_current:
                progress = max(progress, float(event.get("Progress", 0) or 0))
                scan_timestamp = str(event.get("timestamp") or scan_timestamp)
            continue
        if name == "FSSSignalDiscovered" and current["system"] \
                and (same_address or implicit_current):
            signal_count += 1
            if _is_hge(event):
                hge_count += 1
    return {
        **current,
        "progress": min(1.0, max(0.0, progress)),
        "complete": progress >= 0.999,
        "scan_timestamp": scan_timestamp,
        "signal_count": signal_count,
        "hge_count": hge_count,
    }


def _same_system(row, current_system="", current_system_address=None):
    """Match the live Journal system by address first, then by exact name."""
    row_address = row.get("system_address")
    if row_address is not None and current_system_address is not None:
        return row_address == current_system_address
    return bool(
        str(current_system or "").strip()
        and _fold(row.get("system")) == _fold(current_system)
    )


def rank_state_find_systems(observations, current_pos=None, now=None,
                            max_age_seconds=86400, current_system="",
                            current_system_address=None):
    """Aggregate finds per system and type, preserving strongest evidence."""
    now = now or datetime.now(timezone.utc)
    groups = {}
    for source in observations or []:
        if source.get("self_test"):
            continue
        timestamp = _parse_timestamp(
            source.get("signal_timestamp") or source.get("received_at")
        )
        if timestamp is None:
            continue
        age = max(0.0, (now - timestamp).total_seconds())
        if age > max_age_seconds:
            continue
        reported_lifetime = int(source.get("time_remaining", 0) or 0)
        evidence = str(source.get("evidence_kind") or "BGS_PREDICTION")
        signal_evidence = evidence in {
            "EDDN_SIGNAL", "LOCAL_JOURNAL", "ENTERED"
        }
        if signal_evidence and reported_lifetime > 0 and age >= reported_lifetime:
            continue
        remaining = max(0, reported_lifetime - round(age))
        system = str(source.get("system") or "").strip()
        find_type = str(source.get("find_type") or "HGE")
        if not system or find_type not in FIND_TYPES:
            continue
        # An HGE's likely contents follow its spawning faction/state. Combining
        # every faction in a system creates materially misleading predictions.
        faction_key = _fold(source.get("faction")) if find_type == "HGE" else ""
        state_key = _fold(source.get("state")) if find_type == "HGE" else ""
        key = (
            source.get("system_address") or _fold(system),
            find_type, faction_key, state_key,
        )
        row = groups.setdefault(key, {
            "system": system, "system_address": source.get("system_address"),
            "star_pos": source.get("star_pos") or [], "find_type": find_type,
            "find_label": FIND_TYPES[find_type], "states": set(),
            "allegiances": set(), "factions": set(), "materials": {},
            "intensity": "UNKNOWN", "evidence_kind": "BGS_PREDICTION",
            "report_count": 0, "latest_timestamp": timestamp,
            "age_seconds": age, "remaining_seconds": remaining,
            "details_unknown": bool(source.get("details_unknown")),
        })
        row["details_unknown"] = bool(
            row.get("details_unknown") and source.get("details_unknown")
        )
        row["report_count"] += 1
        for field, target in (("state", "states"), ("allegiance", "allegiances"),
                              ("faction", "factions")):
            value = str(source.get(field) or "").strip()
            if value:
                row[target].add(readable_faction_state(value) if field == "state" else value)
        for item in source.get("materials") or []:
            material = str(item.get("material") or "")
            if material:
                row["materials"][material] = max(
                    row["materials"].get(material, 0.0),
                    float(item.get("confidence", 0.0) or 0.0),
                )
        evidence = str(source.get("evidence_kind") or "BGS_PREDICTION")
        if EVIDENCE_RANK.get(evidence, 0) > EVIDENCE_RANK.get(row["evidence_kind"], 0):
            row["evidence_kind"] = evidence
        if str(source.get("intensity") or "UNKNOWN") != "UNKNOWN":
            row["intensity"] = str(source["intensity"])
        if timestamp > row["latest_timestamp"]:
            row["latest_timestamp"] = timestamp
            row["age_seconds"] = age
            row["star_pos"] = source.get("star_pos") or row["star_pos"]
            row["remaining_seconds"] = remaining
    result = []
    for row in groups.values():
        is_current_system = _same_system(
            row, current_system, current_system_address
        )
        distance = 0.0 if is_current_system else _distance(
            current_pos, row["star_pos"]
        )
        freshness = intelligence_freshness(
            row["evidence_kind"], row["age_seconds"], row["remaining_seconds"]
        )
        result.append({
            **row,
            "states": sorted(row["states"], key=str.casefold),
            "allegiances": sorted(row["allegiances"], key=str.casefold),
            "factions": sorted(row["factions"], key=str.casefold),
            "materials": [
                {"material": key, "confidence": value}
                for key, value in sorted(row["materials"].items(),
                                         key=lambda item: (-item[1], item[0]))
            ],
            "distance_ly": distance,
            "last_reported_minutes": max(0, int(row["age_seconds"] // 60)),
            "latest_timestamp": row["latest_timestamp"].isoformat(),
            "remaining_seconds": row["remaining_seconds"],
            "freshness": freshness,
            "is_current_system": is_current_system,
        })
    freshness_rank = {"LIVE": 0, "RECENT": 1, "STALE": 2}
    return sorted(result, key=lambda row: (
        not row.get("is_current_system", False),
        freshness_rank.get(row.get("freshness"), 3),
        row["distance_ly"] is None,
        row["distance_ly"] if row["distance_ly"] is not None else 0,
        -EVIDENCE_RANK.get(row["evidence_kind"], 0),
        row["last_reported_minutes"], row["system"].casefold(),
    ))


def rank_hge_candidate_systems(
    sightings, current_pos=None, now=None, max_age_seconds=86400,
    current_system="", current_system_address=None,
):
    """Aggregate BGS prerequisite snapshots into candidate systems, never signals."""
    now = now or datetime.now(timezone.utc)
    systems = {}
    for sighting in sightings or []:
        if sighting.get("self_test"):
            continue
        timestamp = _parse_timestamp(
            sighting.get("signal_timestamp") or sighting.get("received_at")
        )
        if timestamp is None:
            continue
        age = max(0.0, (now - timestamp).total_seconds())
        if age > max_age_seconds:
            continue
        system = str(sighting.get("system") or "").strip()
        if not system:
            continue
        key = sighting.get("system_address") or _fold(system)
        row = systems.setdefault(key, {
            "system": system,
            "system_address": sighting.get("system_address"),
            "star_pos": sighting.get("star_pos") or [],
            "report_count": 0,
            "factions": set(),
            "states": set(),
            "allegiances": set(),
            "materials": {},
            "latest_timestamp": timestamp,
            "age_seconds": age,
        })
        row["report_count"] += 1
        for field, target in (
            ("faction", "factions"), ("state", "states"),
            ("allegiance", "allegiances"),
        ):
            value = str(sighting.get(field) or "").strip()
            if field == "state":
                value = readable_faction_state(value)
            if value:
                row[target].add(value)
        for item in sighting.get("materials", []) or []:
            material = str(item.get("material") or "")
            if material:
                row["materials"][material] = max(
                    row["materials"].get(material, 0.0),
                    float(item.get("confidence", 0.0) or 0.0),
                )
        if timestamp > row["latest_timestamp"]:
            row["latest_timestamp"] = timestamp
            row["age_seconds"] = age
            row["star_pos"] = sighting.get("star_pos") or row["star_pos"]
    ranked = []
    for row in systems.values():
        # Recompute predictions from normalized BGS facts so older cached rows
        # containing Elite localization tokens are upgraded automatically.
        for state in row["states"] or {""}:
            for allegiance in row["allegiances"] or {""}:
                for item in infer_hge_materials(state, allegiance):
                    material = item["material"]
                    row["materials"][material] = max(
                        row["materials"].get(material, 0.0),
                        float(item["confidence"]),
                    )
        if not row["materials"]:
            continue
        is_current_system = _same_system(
            row, current_system, current_system_address
        )
        distance = 0.0 if is_current_system else _distance(
            current_pos, row["star_pos"]
        )
        ranked.append({
            **row,
            "factions": sorted(row["factions"], key=str.casefold),
            "states": sorted(row["states"], key=str.casefold),
            "allegiances": sorted(row["allegiances"], key=str.casefold),
            "materials": [
                {"material": material, "confidence": confidence}
                for material, confidence in sorted(
                    row["materials"].items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ],
            "distance_ly": distance,
            "last_reported_minutes": max(0, int(row["age_seconds"] // 60)),
            "prediction_basis": " · ".join(filter(None, (
                "States: " + ", ".join(sorted(row["states"], key=str.casefold))
                if row["states"] else "",
                "Allegiances: " + ", ".join(sorted(row["allegiances"], key=str.casefold))
                if row["allegiances"] else "",
            ))),
            "candidate_only": True,
            "is_current_system": is_current_system,
        })
    return sorted(ranked, key=lambda row: (
        not row.get("is_current_system", False),
        row["distance_ly"] is None,
        row["distance_ly"] if row["distance_ly"] is not None else 0,
        row["last_reported_minutes"],
        row["system"].casefold(),
    ))


def local_hge_scan_status(events, now=None):
    """Report only positive HGE proof; NavBeaconScan has no negative USS list."""
    system = ""
    system_address = None
    scanned = False
    scan_timestamp = ""
    entered_hge = False
    entered_timestamp = ""
    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name in {"Location", "FSDJump", "CarrierJump"}:
            system = str(event.get("StarSystem") or "").strip()
            system_address = event.get("SystemAddress")
            scanned = False
            scan_timestamp = ""
            entered_hge = False
            entered_timestamp = ""
        elif name == "NavBeaconScan" and system:
            scanned = True
            scan_timestamp = str(event.get("timestamp") or "")
        elif name == "USSDrop" and (
            str(event.get("USSType") or "")
            == "$USS_Type_VeryValuableSalvage;"
        ):
            entered_hge = True
            entered_timestamp = str(event.get("timestamp") or "")
        elif name == "SupercruiseDestinationDrop" and (
            str(event.get("Type") or "")
            == "$USS_Type_VeryValuableSalvage;"
        ):
            entered_hge = True
            entered_timestamp = str(event.get("timestamp") or "")
    active = rank_all_hge_sightings(
        extract_local_hge_sightings(events), now=now
    )
    return {
        "system": system,
        "system_address": system_address,
        "scanned": scanned,
        "scan_timestamp": scan_timestamp,
        "entered_hge": entered_hge,
        "entered_timestamp": entered_timestamp,
        "active_count": len(active),
        "status": (
            "VERIFIED" if active else
            "VERIFIED · ENTERED" if entered_hge else
            "SCANNED · UNCONFIRMED" if scanned else "UNKNOWN"
        ),
    }
def is_hge_route_relevant(material):
    """Return whether HGE farming can directly or indirectly supply material."""
    material = material or {}
    category = material.get("category", material.get("Category", ""))
    tradeable = material.get("tradeable", material.get("Tradeable", False))
    return str(category) == "Manufactured" and bool(tradeable)
