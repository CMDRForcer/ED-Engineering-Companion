"""ShipID-based Elite Dangerous fleet Journal replay."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


FLEET_EVENTS = frozenset({
    "LoadGame", "Loadout", "ShipyardBuy", "ShipyardSell", "ShipyardSwap",
    "ShipyardTransfer", "StoredShips", "SetUserShipName", "Docked", "Undocked",
})
_JOURNAL_STAMP = re.compile(r"Journal\.(\d{4}-\d{2}-\d{2}T\d{6})")


def event_timestamp(event: Mapping[str, Any]) -> datetime:
    """Return a comparable UTC timestamp; malformed/missing values are oldest."""
    value = str(event.get("timestamp") or "").strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def journal_file_timestamp(path: Path) -> datetime:
    """Sort Journal files by the timestamp embedded in Frontier's filename."""
    match = _JOURNAL_STAMP.search(path.name)
    if not match:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def read_fleet_events(journal_files: Iterable[Path]) -> list[dict[str, Any]]:
    """Read fleet events in filename chronology without relying on mtime."""
    events: list[dict[str, Any]] = []
    for journal_file in sorted(
        journal_files or [], key=lambda path: (journal_file_timestamp(path), path.name)
    ):
        try:
            lines = journal_file.read_text(
                encoding="utf-8-sig", errors="replace"
            ).splitlines()
        except (OSError, PermissionError):
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(event, dict) and event.get("event") in FLEET_EVENTS:
                events.append(event)
    return events


def _readable_type(record: Mapping[str, Any]) -> str:
    localized = str(
        record.get("Ship_Localised") or record.get("ShipType_Localised") or ""
    ).strip()
    if localized:
        return localized
    internal = str(record.get("Ship") or record.get("ShipType") or "").strip()
    if not internal:
        return ""
    conventional = {
        "ferdelance": "Fer-de-Lance",
        "krait_mkii": "Krait Mk II",
    }
    if internal.casefold() in conventional:
        return conventional[internal.casefold()]
    if "-" in internal and "_" not in internal:
        return internal
    words = re.sub(r"[_-]+", " ", internal).split()
    return " ".join(
        word.upper() if word.casefold() in {"ii", "iii", "iv", "mk"}
        else word.capitalize()
        for word in words
    )


def _ship_label(row: Mapping[str, Any]) -> str:
    ship_type = str(row.get("type") or "").strip()
    name = str(row.get("name") or "").strip()
    if ship_type and name:
        return f"{ship_type} – {name}"
    if name:
        return name
    if ship_type:
        return ship_type
    return f"Ship #{row['id']}"


def rebuild_fleet(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Rebuild the complete fleet from Journal truth, keyed only by ShipID."""
    fleet: dict[str, dict[str, Any]] = {}
    sold_at: dict[str, datetime] = {}
    active_id = ""
    active_at = datetime.min.replace(tzinfo=timezone.utc)

    def update(
        record: Mapping[str, Any], ship_id: object, status: str,
        stamp: datetime,
    ) -> str:
        if ship_id in (None, ""):
            return ""
        key = str(ship_id)
        if sold_at.get(key, datetime.min.replace(tzinfo=timezone.utc)) > stamp:
            return ""
        previous = fleet.get(key)
        if previous and previous["_timestamp"] > stamp:
            return key
        row = dict(previous or {"id": key, "name": "", "type": ""})
        ship_name = str(
            record.get("UserShipName") or record.get("ShipName")
            or record.get("Name") or ""
        ).strip()
        ship_type = _readable_type(record)
        if ship_name or any(field in record for field in ("UserShipName", "ShipName", "Name")):
            row["name"] = ship_name
        if ship_type:
            row["type"] = ship_type
        row.update({"status": status, "_timestamp": stamp})
        fleet[key] = row
        return key

    # Per-ShipID timestamp checks make the result independent of accidental
    # event/file ordering even though read_fleet_events already sorts files.
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        name = str(event.get("event") or "")
        stamp = event_timestamp(event)
        if name == "StoredShips":
            snapshot_ids: set[str] = set()
            for field in ("ShipsHere", "ShipsRemote"):
                status = "stored" if field == "ShipsHere" else "remote"
                for ship in event.get(field, []) or []:
                    if isinstance(ship, Mapping) and ship.get("ShipID") not in (None, ""):
                        key = update(ship, ship.get("ShipID"), status, stamp)
                        if key:
                            snapshot_ids.add(key)
            for key, row in list(fleet.items()):
                if (
                    key != active_id and row.get("status") in {"stored", "remote", "transferring"}
                    and row["_timestamp"] <= stamp and key not in snapshot_ids
                ):
                    del fleet[key]
            continue
        if name == "ShipyardSell":
            key = str(event.get("SellShipID") or event.get("ShipID") or "")
            if key and stamp >= sold_at.get(key, datetime.min.replace(tzinfo=timezone.utc)):
                sold_at[key] = stamp
                if not fleet.get(key) or fleet[key]["_timestamp"] <= stamp:
                    fleet.pop(key, None)
                if key == active_id and stamp >= active_at:
                    active_id = ""
            continue
        if name == "ShipyardTransfer":
            update(event, event.get("ShipID"), "transferring", stamp)
            continue
        if name == "SetUserShipName":
            update(event, event.get("ShipID"), fleet.get(str(event.get("ShipID")), {}).get("status", "stored"), stamp)
            continue
        if name in {"LoadGame", "Loadout", "ShipyardSwap", "ShipyardBuy"}:
            ship_id = event.get("NewShipID") if name == "ShipyardBuy" else event.get("ShipID")
            key = update(event, ship_id, "active", stamp)
            if key and stamp >= active_at:
                if active_id and active_id != key and active_id in fleet:
                    fleet[active_id]["status"] = "stored"
                active_id, active_at = key, stamp
            if name in {"ShipyardSwap", "ShipyardBuy"}:
                update({"ShipType": event.get("StoreOldShip")}, event.get("StoreShipID"), "stored", stamp)

    rows = []
    used_labels: set[str] = set()
    for key, row in fleet.items():
        public = {field: value for field, value in row.items() if not field.startswith("_")}
        label = _ship_label(public)
        if label in used_labels:
            label = f"{label} · #{key}"
        used_labels.add(label)
        public["label"] = label
        rows.append(public)
    rows.sort(key=lambda row: (str(row["type"]).casefold(), str(row["name"]).casefold(), int(row["id"])))
    return {"ships": rows, "active_id": active_id}
