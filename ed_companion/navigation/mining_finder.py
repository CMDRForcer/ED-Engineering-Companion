"""Pure projections for Mining Finder evidence; no I/O or UI dependencies."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from math import sqrt
from typing import Any, Iterable

from .mining_contract import MINING_FRESHNESS_SECONDS
from .mining_commodities import RHINO_SURFACE, mining_commodity_id

SPANSH_DUMP_URL = "https://spansh.co.uk/api/dump/{system_address}"

MINING_EVIDENCE_RANK = {
    "STALE": 0,
    "CATALOG_CANDIDATE": 1,
    "LIVE_REPORTED": 2,
    "LOCAL_CONFIRMED": 3,
}

NON_COMMODITY_SAA_SIGNALS = frozenset({
    "biological", "geological", "human", "guardian", "thargoid", "other",
    "planetarymininglocation",
})


def fetch_spansh_system_dump(system_address: int, get: Any, timeout: int = 20):
    """Fetch one documented public-system dump without sending private data."""
    try:
        address = int(system_address)
    except (TypeError, ValueError) as exc:
        raise ValueError("A numeric current system address is required") from exc
    if address <= 0:
        raise ValueError("A positive current system address is required")
    response = get(SPANSH_DUMP_URL.format(system_address=address), timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    system = payload.get("system") if isinstance(payload, dict) else None
    if not isinstance(system, dict) or int(system.get("id64") or 0) != address:
        raise ValueError("Spansh returned no matching system dump")
    return payload


def _text(value: Any) -> str:
    return str(value or "").strip()


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == () or value == []


def _commodity_id(value: Any) -> str:
    value = _text(value)
    if value.startswith("$") and value.endswith("_Name;"):
        folded = value[1:-6].casefold()
    else:
        folded = value.casefold()
    prefix = "$saa_signaltype_"
    if folded.startswith(prefix) and folded.endswith(";"):
        folded = folded[len(prefix):-1]
    identifier = mining_commodity_id(folded)
    return "" if identifier in NON_COMMODITY_SAA_SIGNALS else identifier


def is_mining_commodity_signal(value: Any) -> bool:
    """Reject known DSS category markers while retaining mineral identifiers."""
    folded = _text(value).casefold()
    if folded.startswith("$saa_signaltype_") and folded.endswith(";"):
        folded = folded[len("$saa_signaltype_"):-1]
    return bool(folded and folded not in NON_COMMODITY_SAA_SIGNALS)


def _coordinates(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return []
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []


def _distance(origin: Any, destination: Any) -> float | None:
    start = _coordinates(origin)
    end = _coordinates(destination)
    if not start or not end:
        return None
    return round(sqrt(sum((a - b) ** 2 for a, b in zip(start, end))), 1)


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _candidate_identity(candidate: dict[str, Any]) -> tuple[Any, ...]:
    system = candidate.get("systemAddress")
    system_key = ("address", system) if system is not None else (
        "name", _text(candidate.get("system")).casefold()
    )
    body = candidate.get("bodyId")
    body_key = ("id", body) if body is not None else (
        "name", _text(candidate.get("body")).casefold()
    )
    return system_key + body_key + (
        _text(candidate.get("ring")).casefold(),
    )


def merge_mining_candidate_batch(
    existing: Iterable[dict[str, Any]],
    additions: Iterable[dict[str, Any]],
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge a relay batch without rebuilding every unaffected ring."""
    rows = [row for row in existing or [] if isinstance(row, dict)]
    positions = {
        _candidate_identity(row): index for index, row in enumerate(rows)
        if _text(row.get("ring"))
    }
    displaced = []
    for incoming in merge_mining_candidates(additions, now=now):
        key = _candidate_identity(incoming)
        index = positions.get(key)
        if index is None:
            positions[key] = len(rows)
            rows.append(incoming)
            continue
        previous = rows[index]
        merged = merge_mining_candidates([previous, incoming], now=now)
        if merged:
            displaced.append(previous)
            rows[index] = merged[0]
    return rows, displaced


def mining_candidate_freshness(
    candidate: dict[str, Any], now: datetime | None = None,
    policy: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Apply an explicit EDEC age policy without asserting game persistence."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_evidence = _text(candidate.get("sourceEvidence")) or _text(
        candidate.get("evidence")
    )
    observed = _timestamp(candidate.get("observedAt"))
    limits = policy or MINING_FRESHNESS_SECONDS
    limit = limits.get(source_evidence)
    age_seconds = max(0, int((now - observed).total_seconds())) if observed else None
    stale = observed is None or limit is None or age_seconds > limit
    row = dict(candidate)
    row.update({
        "sourceEvidence": source_evidence,
        "evidence": source_evidence,
        "stale": stale,
        "recheckRecommended": stale,
        "confirmationStatus": (
            "CONFIRMATION_TIME_UNKNOWN" if observed is None
            else "RECHECK_RECOMMENDED" if stale
            else "RECENTLY_CONFIRMED"
        ),
        "ageSeconds": age_seconds,
        "freshnessLimitSeconds": limit,
    })
    return row


def merge_mining_candidates(
    candidates: Iterable[dict[str, Any]], now: datetime | None = None,
    policy: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Merge identical rings while retaining every source observation."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for candidate in candidates or []:
        if not isinstance(candidate, dict) or not _text(candidate.get("ring")):
            continue
        row = mining_candidate_freshness(candidate, now=now, policy=policy)
        groups.setdefault(_candidate_identity(row), []).append(row)

    merged = []
    fill_fields = (
        "system", "systemAddress", "coordinates", "distanceLy", "body",
        "bodyId", "ring", "ringType", "reserveLevel", "distanceToArrivalLs",
    )
    for observations in groups.values():
        observations.sort(key=lambda row: (
            not row.get("stale"),
            MINING_EVIDENCE_RANK.get(row["sourceEvidence"], 0),
            _timestamp(row.get("observedAt")) or datetime.min.replace(
                tzinfo=timezone.utc
            ),
        ), reverse=True)
        strongest = dict(observations[0])
        for field in fill_fields:
            if not _missing(strongest.get(field)):
                continue
            for source in observations[1:]:
                value = source.get(field)
                if not _missing(value):
                    strongest[field] = value
                    break

        hotspots: dict[str, dict[str, Any]] = {}
        for source in observations:
            for hotspot in source.get("hotspots") or []:
                if not isinstance(hotspot, dict):
                    continue
                commodity = _text(hotspot.get("commodity")).casefold()
                if commodity and commodity not in hotspots:
                    hotspots[commodity] = dict(hotspot)
        strongest["hotspots"] = [hotspots[key] for key in sorted(hotspots)]
        strongest["planetaryMiningLocationCount"] = max(
            int(source.get("planetaryMiningLocationCount", 0) or 0)
            for source in observations
        )
        strongest["learnedAt"] = max(
            (_text(source.get("learnedAt")) for source in observations),
            default="",
        )
        evidence_rows = []
        evidence_keys = set()
        for source in observations:
            summaries = [{
                "evidence": source.get("evidence"),
                "sourceEvidence": source.get("sourceEvidence"),
                "observedAt": source.get("observedAt"),
                "source": source.get("source"),
                "stale": source.get("stale"),
            }]
            summaries.extend(
                row for row in (source.get("observations") or [])
                if isinstance(row, dict)
            )
            for summary in summaries:
                stable_summary = {
                    key: value for key, value in summary.items()
                    if key != "stale"
                }
                key = json.dumps(
                    stable_summary, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                )
                if key not in evidence_keys:
                    evidence_keys.add(key)
                    evidence_rows.append(dict(summary))
        strongest["observations"] = evidence_rows
        strongest["sourceCount"] = len(evidence_rows)
        merged.append(strongest)

    return sorted(merged, key=lambda row: (
        row.get("distanceLy") is None,
        float(row.get("distanceLy") or 0),
        _text(row.get("system")).casefold(),
        _text(row.get("ring")).casefold(),
    ))


def _signal_rows(signals: Any) -> list[dict[str, Any]]:
    rows = []
    if isinstance(signals, dict):
        iterable = signals.items()
    elif isinstance(signals, list):
        iterable = (
            (row.get("Type"), row.get("Count"))
            for row in signals if isinstance(row, dict)
        )
    else:
        iterable = ()
    for raw_type, raw_count in iterable:
        commodity = _commodity_id(raw_type)
        if not commodity:
            continue
        try:
            count = max(0, int(raw_count or 0))
        except (TypeError, ValueError):
            count = 0
        rows.append({"commodity": commodity, "count": count})
    return sorted(rows, key=lambda row: row["commodity"])


def _named_signal_count(signals: Any, signal_id: str) -> int:
    """Return the reported count for a non-commodity DSS/FSS signal."""
    wanted = mining_commodity_id(signal_id)
    total = 0
    if isinstance(signals, dict):
        iterable = signals.items()
    elif isinstance(signals, list):
        iterable = (
            (row.get("Type"), row.get("Count"))
            for row in signals if isinstance(row, dict)
        )
    else:
        iterable = ()
    for raw_type, raw_count in iterable:
        if mining_commodity_id(raw_type) != wanted:
            continue
        try:
            total += max(0, int(raw_count or 0))
        except (TypeError, ValueError):
            continue
    return total


def project_local_mining_evidence(
    events: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Project rings, samples and every locally refined commodity."""
    system = ""
    system_address = None
    star_pos: list[float] = []
    rings: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    refined: dict[str, dict[str, Any]] = {}
    active_srv = ""

    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = _text(event.get("event"))
        if name == "LaunchSRV":
            active_srv = _text(event.get("SRVType")).casefold()
            continue
        if name == "DockSRV":
            active_srv = ""
            continue
        if name in {"Location", "FSDJump", "CarrierJump"}:
            system = _text(event.get("StarSystem")) or system
            system_address = event.get("SystemAddress", system_address)
            star_pos = _coordinates(event.get("StarPos")) or star_pos
            continue
        if name == "Scan":
            reserve = _text(event.get("ReserveLevel"))
            body = _text(event.get("BodyName"))
            for ring in event.get("Rings") or []:
                if not isinstance(ring, dict) or not _text(ring.get("Name")):
                    continue
                ring_name = _text(ring["Name"])
                row = {
                    "system": system,
                    "systemAddress": system_address,
                    "coordinates": list(star_pos),
                    "body": body,
                    "bodyId": event.get("BodyID"),
                    "ring": ring_name,
                    "ringType": _text(ring.get("RingClass")),
                    "reserveLevel": reserve,
                    "distanceToArrivalLs": event.get("DistanceFromArrivalLS"),
                    "hotspots": [],
                    "evidence": "LOCAL_CONFIRMED",
                    "observedAt": _text(event.get("timestamp")),
                    "source": "Frontier Journal · Scan",
                }
                rings[ring_name.casefold()] = row
                candidates.append(row)
            continue
        if name in {"SAASignalsFound", "FSSBodySignals"}:
            ring_name = _text(event.get("BodyName"))
            row = rings.get(ring_name.casefold())
            if row is None:
                row = {
                    "system": system,
                    "systemAddress": system_address,
                    "coordinates": list(star_pos),
                    "body": "",
                    "bodyId": event.get("BodyID"),
                    "ring": ring_name,
                    "ringType": "",
                    "reserveLevel": "",
                    "distanceToArrivalLs": None,
                    "hotspots": [],
                    "evidence": "LOCAL_CONFIRMED",
                    "observedAt": _text(event.get("timestamp")),
                    "source": "Frontier Journal · SAASignalsFound",
                }
                rings[ring_name.casefold()] = row
                candidates.append(row)
            row["hotspots"] = _signal_rows(event.get("Signals"))
            planetary_count = _named_signal_count(
                event.get("Signals"), "planetarymininglocation"
            )
            if planetary_count:
                row["planetaryMiningLocationCount"] = planetary_count
            row["observedAt"] = _text(event.get("timestamp")) or row["observedAt"]
            row["source"] = f"Frontier Journal · Scan + {name}"
            continue
        if name == "ProspectedAsteroid":
            materials = []
            for material in event.get("Materials") or []:
                if not isinstance(material, dict):
                    continue
                commodity = _commodity_id(material.get("Name"))
                if not commodity:
                    continue
                try:
                    proportion = float(material.get("Proportion"))
                except (TypeError, ValueError):
                    proportion = None
                materials.append({
                    "commodity": commodity, "proportion": proportion,
                })
            samples.append({
                "system": system,
                "systemAddress": system_address,
                "observedAt": _text(event.get("timestamp")),
                "content": _text(event.get("Content")),
                "remaining": event.get("Remaining"),
                "motherlode": _commodity_id(event.get("MotherlodeMaterial")),
                "materials": materials,
                "boundToRing": False,
                "evidence": "LOCAL_CONFIRMED",
                "source": "Frontier Journal · ProspectedAsteroid",
            })
            continue
        if name == "MiningRefined":
            commodity = _commodity_id(event.get("Type"))
            if not commodity:
                continue
            row = refined.setdefault(commodity, {
                "id": commodity,
                "count": 0,
                "lastObservedAt": "",
                "methods": [],
                "source": "Frontier Journal · MiningRefined",
            })
            row["count"] += 1
            row["lastObservedAt"] = max(
                row["lastObservedAt"], _text(event.get("timestamp"))
            )
            if "rhino" in active_srv and RHINO_SURFACE not in row["methods"]:
                row["methods"].append(RHINO_SURFACE)
    return {
        "candidates": candidates,
        "prospectorSamples": samples,
        "refinedCommodities": sorted(
            refined.values(), key=lambda row: row["id"]
        ),
    }


def project_eddn_mining_candidates(
    payload: dict[str, Any], received_at: str = "",
) -> list[dict[str, Any]]:
    """Project only explicit public mining fields from an EDDN journal frame."""
    if not isinstance(payload, dict):
        return []
    schema = _text(payload.get("$schemaRef")).casefold()
    message = payload.get("message")
    supported_schema = (
        "/journal/1" in schema or "/fssbodysignals/1" in schema
    )
    if not supported_schema or not isinstance(message, dict):
        return []
    event_name = _text(message.get("event"))
    if event_name not in {"Scan", "SAASignalsFound", "FSSBodySignals"}:
        return []
    location = {
        "event": "Location",
        "StarSystem": _text(message.get("StarSystem")),
        "SystemAddress": message.get("SystemAddress"),
        "StarPos": _coordinates(message.get("StarPos")),
    }
    event = {
        "event": event_name,
        "timestamp": _text(message.get("timestamp")) or received_at,
        "BodyName": _text(message.get("BodyName")),
        "BodyID": message.get("BodyID"),
    }
    if event_name == "Scan":
        event.update({
            "ReserveLevel": _text(message.get("ReserveLevel")),
            "DistanceFromArrivalLS": message.get("DistanceFromArrivalLS"),
            "Rings": [{
                "Name": _text(ring.get("Name")),
                "RingClass": _text(ring.get("RingClass")),
            } for ring in message.get("Rings") or [] if isinstance(ring, dict)],
        })
    else:
        event["Signals"] = [{
            "Type": _text(signal.get("Type")),
            "Count": signal.get("Count"),
        } for signal in message.get("Signals") or [] if isinstance(signal, dict)]
    rows = project_local_mining_evidence([location, event])["candidates"]
    source_schema = (
        "fssbodysignals/1" if "/fssbodysignals/1" in schema else "journal/1"
    )
    for row in rows:
        row.update({
            "evidence": "LIVE_REPORTED",
            "source": f"EDDN {source_schema} · {event_name}",
            "learnedAt": _text(received_at) or _text(message.get("timestamp")),
        })
    return rows


def project_spansh_mining_candidates(
    payload: dict[str, Any], origin: Any = None,
) -> list[dict[str, Any]]:
    """Project the documented Spansh dump response into catalog candidates."""
    system = payload.get("system") if isinstance(payload, dict) else None
    if not isinstance(system, dict):
        return []
    coordinates_object = system.get("coords")
    coordinates = _coordinates([
        coordinates_object.get("x"),
        coordinates_object.get("y"),
        coordinates_object.get("z"),
    ]) if isinstance(coordinates_object, dict) else []
    rows = []
    for body in system.get("bodies") or []:
        if not isinstance(body, dict):
            continue
        for ring in body.get("rings") or []:
            if not isinstance(ring, dict) or not _text(ring.get("name")):
                continue
            signal_block = ring.get("signals")
            signal_block = signal_block if isinstance(signal_block, dict) else {}
            rows.append({
                "system": _text(system.get("name")),
                "systemAddress": system.get("id64"),
                "coordinates": coordinates,
                "distanceLy": _distance(origin, coordinates),
                "body": _text(body.get("name")),
                "bodyId": body.get("bodyId"),
                "ring": _text(ring.get("name")),
                "ringType": _text(ring.get("type")),
                "reserveLevel": _text(body.get("reserveLevel")),
                "distanceToArrivalLs": body.get("distanceToArrival"),
                "hotspots": _signal_rows(signal_block.get("signals")),
                "evidence": "CATALOG_CANDIDATE",
                "observedAt": _text(
                    signal_block.get("updateTime")
                    or body.get("updateTime") or system.get("date")
                ),
                "source": "Spansh dump catalog",
            })
    return rows
