"""Pure projections for Mining Finder evidence; no I/O or UI dependencies."""

from __future__ import annotations

from math import sqrt
from typing import Any, Iterable


def _text(value: Any) -> str:
    return str(value or "").strip()


def _commodity_id(value: Any) -> str:
    value = _text(value)
    if value.startswith("$") and value.endswith("_Name;"):
        return value[1:-6].casefold()
    return value.casefold()


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


def project_local_mining_evidence(
    events: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Project locally confirmed rings/hotspots and unbound prospector samples."""
    system = ""
    system_address = None
    star_pos: list[float] = []
    rings: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = _text(event.get("event"))
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
        if name == "SAASignalsFound":
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
            row["observedAt"] = _text(event.get("timestamp")) or row["observedAt"]
            row["source"] = "Frontier Journal · Scan + SAASignalsFound"
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
    return {"candidates": candidates, "prospectorSamples": samples}


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
