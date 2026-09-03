"""Read-only projections for Journal-confirmed surface-mining output."""

from collections import Counter
from typing import Any, Iterable


MINING_COMMODITY_NAMES = {
    "$haematite_name;": "Haematite",
    "$samarium_name;": "Samarium",
    "$thortveitite_name;": "Thortveitite",
}


def mining_commodity_display_name(symbol: Any, localized: Any = "") -> str:
    """Resolve only observed mineral symbols and preserve future unknown IDs."""
    label = str(localized or "").strip()
    if label:
        return label
    key = str(symbol or "").strip()
    return MINING_COMMODITY_NAMES.get(key.casefold(), key or "Unknown mineral")


def project_latest_srv_mining_session(
    events: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize the latest SRV trip without replacing inventory projections."""
    session: dict[str, Any] | None = None
    refined: Counter[tuple[str, str]] = Counter()
    materials: Counter[tuple[str, str]] = Counter()

    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name == "LaunchSRV":
            symbol = str(event.get("SRVType") or "").strip()
            session = {
                "active": True,
                "vehicleId": str(event.get("ID") or ""),
                "vehicleType": symbol,
                "vehicleName": str(
                    event.get("SRVType_Localised") or symbol
                ),
                "startedAt": str(event.get("timestamp") or ""),
                "endedAt": "",
            }
            refined.clear()
            materials.clear()
            continue
        if session is None:
            continue
        if name == "DockSRV" and (
            not event.get("ID")
            or str(event.get("ID")) == session["vehicleId"]
        ):
            session["active"] = False
            session["endedAt"] = str(event.get("timestamp") or "")
            continue
        if not session["active"]:
            continue
        if name == "MiningRefined":
            symbol = str(event.get("Type") or "").strip()
            if symbol:
                label = mining_commodity_display_name(
                    symbol, event.get("Type_Localised")
                )
                refined[(symbol, label)] += 1
        elif name == "MaterialCollected":
            symbol = str(event.get("Name") or "").strip()
            if symbol:
                try:
                    count = max(0, int(event.get("Count", 1) or 0))
                except (TypeError, ValueError):
                    count = 0
                if count:
                    materials[(symbol, str(event.get("Name_Localised") or symbol))] += count
    if session is None:
        return {
            "known": False, "active": False,
            "refinedMinerals": [], "engineeringMaterials": [],
        }
    session.update({
        "known": True,
        "refinedMinerals": [
            {"id": symbol, "name": label, "count": count}
            for (symbol, label), count in sorted(refined.items())
        ],
        "engineeringMaterials": [
            {"id": symbol, "name": label, "count": count}
            for (symbol, label), count in sorted(materials.items())
        ],
    })
    return session
