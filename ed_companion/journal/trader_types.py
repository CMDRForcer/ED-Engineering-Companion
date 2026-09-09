"""Extract material-trader type evidence from Elite Journal events."""

from __future__ import annotations

from typing import Any, Literal, Mapping, TypedDict


MATERIAL_TRADER_SERVICE = "materialtrader"

TraderType = Literal["raw", "manufactured", "encoded"]
TraderConfidence = Literal["confirmed", "heuristic"]
TraderEvidenceSource = Literal["journal_confirmed", "heuristic_economy"]


class JournalTraderTypeEvidence(TypedDict):
    market_id: int
    trader_type: TraderType
    confidence: TraderConfidence
    source: TraderEvidenceSource
    event_timestamp: str
    system: str | None
    station: str | None


_ECONOMY_TYPE_INDICATORS: tuple[tuple[str, TraderType], ...] = (
    ("refinery", "raw"),
    ("extraction", "raw"),
    ("industrial", "manufactured"),
    ("high tech", "encoded"),
    ("hightech", "encoded"),
    ("military", "encoded"),
)


def _market_id(event: Mapping[str, Any]) -> int | None:
    value = event.get("MarketID")
    if isinstance(value, bool):
        return None
    try:
        market_id = int(value)
    except (TypeError, ValueError):
        return None
    return market_id if market_id > 0 else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _economy_names(event: Mapping[str, Any]) -> set[str]:
    values: list[Any] = [
        event.get("StationEconomy_Localised"),
        event.get("StationEconomy"),
    ]
    economies = event.get("StationEconomies")
    if isinstance(economies, list):
        for economy in economies:
            if isinstance(economy, Mapping):
                values.extend((economy.get("Name_Localised"), economy.get("Name")))

    names: set[str] = set()
    for value in values:
        name = str(value or "").strip().casefold()
        if name.startswith("$economy_"):
            name = name.removeprefix("$economy_").removesuffix(";")
        if name:
            names.add(name.replace("_", " "))
    return names


def _heuristic_trader_type(event: Mapping[str, Any]) -> TraderType | None:
    indications = {
        trader_type
        for economy in _economy_names(event)
        for indicator, trader_type in _ECONOMY_TYPE_INDICATORS
        if indicator in economy
    }
    return next(iter(indications)) if len(indications) == 1 else None


def trader_type_evidence_from_event(
    event: Mapping[str, Any],
) -> JournalTraderTypeEvidence | None:
    """Translate one Journal event into confirmed or heuristic evidence."""
    if not isinstance(event, Mapping):
        return None

    market_id = _market_id(event)
    event_timestamp = _optional_text(event.get("timestamp"))
    if market_id is None or event_timestamp is None:
        return None

    event_name = event.get("event")
    if event_name == "MaterialTrade":
        trader_type = str(event.get("TraderType") or "").strip().casefold()
        if trader_type not in {"raw", "manufactured", "encoded"}:
            return None
        confidence: TraderConfidence = "confirmed"
        source: TraderEvidenceSource = "journal_confirmed"
    elif event_name == "Docked":
        services = event.get("StationServices")
        if not isinstance(services, list) or not any(
            isinstance(service, str)
            and service == MATERIAL_TRADER_SERVICE
            for service in services
        ):
            return None
        inferred_type = _heuristic_trader_type(event)
        if inferred_type is None:
            return None
        trader_type = inferred_type
        confidence = "heuristic"
        source = "heuristic_economy"
    else:
        return None

    return {
        "market_id": market_id,
        "trader_type": trader_type,  # type: ignore[typeddict-item]
        "confidence": confidence,
        "source": source,
        "event_timestamp": event_timestamp,
        "system": _optional_text(event.get("StarSystem")),
        "station": _optional_text(event.get("StationName")),
    }
