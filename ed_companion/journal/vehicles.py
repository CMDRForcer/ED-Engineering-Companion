"""Project vehicle lifecycle events without reparsing the Journal."""

from typing import Any, Iterable


# Only identifiers observed in Frontier Journal data belong here.  Prefer the
# event's localized label because Frontier can add vehicles without an EDEC
# release; unknown identifiers deliberately remain visible instead of being
# guessed or discarded.
VEHICLE_NAMES = {
    "combat_multicrew_srv_01": "Scorpion (SRV)",
    "lander01": "Nomad",
    "mev_rhino": "SRV Rhino",
    "testbuggy": "SRV Scarab",
}


def vehicle_display_name(symbol: Any, localized: Any = "") -> str:
    """Return a player-facing name while preserving unknown Frontier IDs."""
    label = str(localized or "").strip()
    if label:
        return label
    key = str(symbol or "").strip()
    return VEHICLE_NAMES.get(key.casefold(), key or "Unknown vehicle")


def project_vehicle_state(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Replay the documented vehicle lifecycle events for one profile."""
    vehicles: dict[str, dict[str, Any]] = {}
    pending_restock: dict[str, dict[str, Any]] = {}
    current_id = ""
    last_event = ""
    last_updated = ""

    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name not in {"RestockVehicle", "LaunchSRV", "DockSRV"}:
            continue
        symbol = str(event.get("SRVType") or event.get("Type") or "").strip()
        vehicle_id = str(event.get("ID") or "").strip()
        if not symbol and not vehicle_id:
            continue
        key = vehicle_id or f"type:{symbol.casefold()}"
        # RestockVehicle is shared by SRVs and ship-launched fighters.  A
        # restock alone is therefore accepted only for an observed SRV symbol;
        # otherwise retain it privately until LaunchSRV/DockSRV proves its
        # category.  This avoids maintaining a fragile fighter denylist.
        if name == "RestockVehicle" and symbol.casefold() not in VEHICLE_NAMES:
            pending_restock[key] = dict(event)
            continue
        if name in {"LaunchSRV", "DockSRV"} and key in pending_restock:
            restock = pending_restock.pop(key)
            vehicles[key] = {
                "id": vehicle_id,
                "type": str(restock.get("Type") or symbol),
                "name": vehicle_display_name(
                    restock.get("Type") or symbol,
                    restock.get("Type_Localised"),
                ),
                "loadout": str(restock.get("Loadout") or ""),
                "lastEvent": "RestockVehicle",
                "lastUpdated": str(restock.get("timestamp") or ""),
                "deployed": False,
            }
            if "Count" in restock:
                try:
                    vehicles[key]["observedCount"] = max(
                        0, int(restock["Count"])
                    )
                except (TypeError, ValueError):
                    pass
        previous = vehicles.get(key, {})
        localized = event.get("SRVType_Localised") or event.get("Type_Localised")
        row = {
            **previous,
            "id": vehicle_id,
            "type": symbol,
            "name": vehicle_display_name(symbol, localized),
            "loadout": str(event.get("Loadout") or previous.get("loadout") or ""),
            "lastEvent": name,
            "lastUpdated": str(event.get("timestamp") or ""),
        }
        if name == "RestockVehicle":
            # Count is retained exactly as Frontier emitted it.  It is not used
            # to invent additional vehicle instances or hangar occupancy.
            if "Count" in event:
                try:
                    row["observedCount"] = max(0, int(event["Count"]))
                except (TypeError, ValueError):
                    pass
            row["deployed"] = False
        elif name == "LaunchSRV":
            row["deployed"] = True
            row["playerControlled"] = bool(event.get("PlayerControlled", False))
            current_id = key
        else:
            row["deployed"] = False
            if current_id == key:
                current_id = ""
        vehicles[key] = row
        last_event = name
        last_updated = str(event.get("timestamp") or "")

    rows = sorted(
        vehicles.values(),
        key=lambda row: (str(row.get("name") or "").casefold(), str(row.get("id") or "")),
    )
    current = vehicles.get(current_id, {})
    return {
        "known": bool(rows),
        "deployed": bool(current),
        "current": dict(current),
        "vehicles": rows,
        "lastEvent": last_event,
        "lastUpdated": last_updated,
    }
