from datetime import datetime, timezone
import math

from ed_companion import APP_VERSION
from ed_companion.trader_config import SPANSH_STATION_SEARCH_URL


TRADER_CATEGORIES = {"Raw", "Manufactured", "Encoded"}
TECH_BROKER_TYPES = {"Human", "Guardian"}


class SpanshError(RuntimeError):
    """A concise, user-displayable Spansh transport/response failure."""


def _spansh_json(post, payload, timeout):
    try:
        response = post(
            SPANSH_STATION_SEARCH_URL,
            json=payload,
            timeout=timeout,
            headers={"User-Agent": f"EDEngineeringCompanion/{APP_VERSION}"},
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        name = type(exc).__name__
        raise SpanshError(f"Spansh request failed ({name}): {exc}") from None
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise SpanshError("Spansh returned invalid or incomplete JSON (results missing).")
    return body
def find_nearest_catalog_trader(category, reference_coords, stations):
    category = str(category or "").strip().title()
    if category not in TRADER_CATEGORIES:
        return None
    if not reference_coords or len(reference_coords) != 3:
        return None
    origin = tuple(float(value) for value in reference_coords)
    candidates = []
    for row in stations or []:
        if (
            not isinstance(row, dict)
            or str(row.get("traderType") or "").title() != category
        ):
            continue
        coords = row.get("coordinates")
        if not isinstance(coords, (list, tuple)) or len(coords) != 3:
            continue
        try:
            coordinates = tuple(float(value) for value in coords)
            distance = math.sqrt(sum(
                (left - right) ** 2
                for left, right in zip(origin, coordinates)
            ))
            arrival = float(row.get("distance_ls") or 0)
        except (TypeError, ValueError):
            continue
        location = dict(row)
        location["coordinates"] = coordinates
        location["distance_ly"] = distance
        location["source"] = "Local trader catalog"
        confidence_rank = {"confirmed": 0, "external": 1, "heuristic": 2}.get(
            str(row.get("traderConfidence") or ""), 3
        )
        candidates.append((
            confidence_rank, distance, arrival,
            str(row.get("station") or "").casefold(),
            location,
        ))
    return min(candidates, key=lambda item: item[:4])[4] if candidates else None


def find_nearest_catalog_traders(categories, reference_coords, stations):
    return {
        category: location
        for category in sorted({
            str(value or "").strip().title() for value in categories or []
        } & TRADER_CATEGORIES)
        if (location := find_nearest_catalog_trader(
            category, reference_coords, stations
        ))
    }


def build_trader_search_payload(category, reference_coords, size=25):
    category = str(category or "").strip().title()
    if category not in TRADER_CATEGORIES:
        raise ValueError(f"Unsupported material trader category: {category}")
    if not reference_coords or len(reference_coords) != 3:
        raise ValueError("Three reference coordinates are required")
    x, y, z = (float(value) for value in reference_coords)
    return {
        "filters": {
            "services": {"value": ["Material Trader"]},
            "material_trader": {"value": [category]},
        },
        "sort": [{"distance": {"direction": "asc"}}],
        "size": max(5, min(100, int(size))),
        "page": 0,
        "reference_coords": {"x": x, "y": y, "z": z},
    }


def parse_nearest_trader(category, response_data):
    """Select the nearest correct, non-planetary, large-pad trader result."""
    candidates = parse_trader_results(category, response_data)
    return candidates[0] if candidates else None


def parse_trader_results(category, response_data):
    """Return every valid trader result in stable nearest-first order."""
    category = str(category or "").strip().title()
    results = (response_data or {}).get("results") or []
    candidates = []
    for row in results:
        if not isinstance(row, dict):
            continue
        if str(row.get("material_trader") or "").strip().title() != category:
            continue
        if not row.get("has_large_pad") or row.get("is_planetary"):
            continue
        system = str(row.get("system_name") or "").strip()
        station = str(row.get("name") or "").strip()
        coords = (
            row.get("system_x"), row.get("system_y"), row.get("system_z")
        )
        try:
            market_id = int(row.get("market_id"))
        except (TypeError, ValueError):
            continue
        if market_id <= 0 or not system or not station or any(value is None for value in coords):
            continue
        try:
            distance = float(row.get("distance"))
            distance_ls = float(row.get("distance_to_arrival") or 0)
            coordinates = tuple(float(value) for value in coords)
        except (TypeError, ValueError):
            continue
        candidates.append((distance, distance_ls, station.casefold(), {
            "category": category,
            "system": system,
            "station": station,
            "distance_ls": round(distance_ls),
            "pad": "Large",
            "coordinates": coordinates,
            "distance_ly": distance,
            "market_id": market_id,
            "verified": str(row.get("updated_at") or ""),
            "source": "Spansh live station search",
        }))
    return [item[3] for item in sorted(candidates, key=lambda item: item[:3])]


def spansh_trader_type_evidence(row, event_timestamp=None):
    """Validate a parsed Spansh station row before it enters the type cache."""
    if not isinstance(row, dict):
        return None
    try:
        market_id = int(row.get("market_id"))
    except (TypeError, ValueError):
        return None
    system = str(row.get("system") or "").strip()
    station = str(row.get("station") or "").strip()
    trader_type = str(row.get("category") or "").strip().casefold()
    timestamp = str(event_timestamp or row.get("verified") or "").strip()
    if market_id <= 0 or not system or not station or trader_type not in {
        "raw", "manufactured", "encoded"
    } or not timestamp:
        return None
    return {
        "market_id": market_id,
        "trader_type": trader_type,
        "confidence": "external",
        "source": "external_api:spansh",
        "event_timestamp": timestamp,
        "system": system,
        "station": station,
    }


def fetch_nearest_trader(category, reference_coords, post, timeout=12):
    payload = build_trader_search_payload(category, reference_coords)
    trader = parse_nearest_trader(category, _spansh_json(post, payload, timeout))
    if not trader:
        raise LookupError(f"No current {category} large-pad trader found")
    return trader


def fetch_nearest_traders(categories, reference_coords, post, timeout=12):
    locations = {}
    errors = {}
    for category in sorted({
        str(value or "").strip().title() for value in categories or []
    } & TRADER_CATEGORIES):
        try:
            locations[category] = fetch_nearest_trader(
                category, reference_coords, post=post, timeout=timeout
            )
        except Exception as exc:
            errors[category] = str(exc)
    return {
        "locations": locations,
        "errors": errors,
        "reference_coords": [float(value) for value in reference_coords],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_trader_catalog_updates(
    categories, reference_coords, post, timeout=12, size=100
):
    """Fetch and combine nearby valid trader rows without replacing local data."""
    locations = []
    errors = {}
    for category in sorted({
        str(value or "").strip().title() for value in categories or []
    } & TRADER_CATEGORIES):
        try:
            payload = build_trader_search_payload(
                category, reference_coords, size=size
            )
            rows = parse_trader_results(
                category, _spansh_json(post, payload, timeout)
            )
            if not rows:
                raise LookupError(
                    f"No current {category} large-pad trader found"
                )
            locations.extend(rows)
        except Exception as exc:
            errors[category] = str(exc)
    return {
        "stations": locations,
        "errors": errors,
        "reference_coords": [float(value) for value in reference_coords],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "Spansh live station search",
    }


def merge_trader_catalog(base_rows, update_rows):
    """Merge station locations; trader type authority belongs to the resolver."""
    merged = {}

    def key(row):
        market_id = row.get("market_id")
        if market_id not in (None, ""):
            return ("market", str(market_id))
        return (
            "name",
            str(row.get("system") or "").casefold(),
            str(row.get("station") or "").casefold(),
        )

    for row in list(base_rows or []) + list(update_rows or []):
        if (
            isinstance(row, dict)
            and row.get("system") and row.get("station")
            and row.get("category") in TRADER_CATEGORIES
        ):
            candidate = dict(row)
            identity = key(candidate)
            merged[identity] = candidate
    return sorted(merged.values(), key=lambda row: (
        str(row.get("category") or ""),
        str(row.get("system") or "").casefold(),
        str(row.get("station") or "").casefold(),
    ))


def build_tech_broker_search_payload(broker_type, reference_coords, size=100):
    broker_type = str(broker_type or "").strip().title()
    if broker_type not in TECH_BROKER_TYPES:
        raise ValueError(f"Unsupported Technology Broker type: {broker_type}")
    if not reference_coords or len(reference_coords) != 3:
        raise ValueError("Three reference coordinates are required")
    x, y, z = (float(value) for value in reference_coords)
    return {
        "filters": {
            "services": {"value": ["Technology Broker"]},
            "technology_broker": {"value": [broker_type]},
        },
        "sort": [{"distance": {"direction": "asc"}}],
        "size": max(5, min(100, int(size))),
        "page": 0,
        "reference_coords": {"x": x, "y": y, "z": z},
    }


def parse_tech_broker_results(broker_type, response_data):
    """Return truthful large-pad orbital Tech Broker stations, nearest first."""
    broker_type = str(broker_type or "").strip().title()
    if broker_type not in TECH_BROKER_TYPES:
        return []
    candidates = []
    for row in (response_data or {}).get("results", []) or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("technology_broker") or "").strip().title() != broker_type:
            continue
        if not row.get("has_large_pad") or row.get("is_planetary"):
            continue
        system = str(row.get("system_name") or "").strip()
        station = str(row.get("name") or "").strip()
        try:
            market_id = int(row.get("market_id"))
            distance = float(row.get("distance"))
            distance_ls = float(row.get("distance_to_arrival") or 0)
            coordinates = tuple(float(row.get(key)) for key in (
                "system_x", "system_y", "system_z"
            ))
        except (TypeError, ValueError):
            continue
        if market_id <= 0 or not system or not station:
            continue
        candidates.append((distance, distance_ls, station.casefold(), {
            "brokerType": broker_type.upper(),
            "system": system,
            "station": station,
            "distance_ly": distance,
            "distance_ls": round(distance_ls),
            "coordinates": coordinates,
            "market_id": market_id,
            "pad": "Large",
            "verified": str(row.get("updated_at") or ""),
            "source": "Spansh live station search",
        }))
    return [item[3] for item in sorted(candidates, key=lambda item: item[:3])]


def fetch_tech_broker_catalog_updates(reference_coords, post, timeout=12, size=100):
    stations = []
    errors = {}
    for broker_type in sorted(TECH_BROKER_TYPES):
        try:
            rows = parse_tech_broker_results(
                broker_type,
                _spansh_json(
                    post,
                    build_tech_broker_search_payload(
                        broker_type, reference_coords, size=size
                    ),
                    timeout,
                ),
            )
            if not rows:
                raise LookupError(f"No current {broker_type} Tech Broker found")
            stations.extend(rows)
        except Exception as exc:
            errors[broker_type] = str(exc)
    return {
        "stations": stations,
        "errors": errors,
        "reference_coords": [float(value) for value in reference_coords],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "Spansh live station search",
    }


def merge_tech_broker_catalog(base_rows, update_rows):
    merged = {}
    for row in list(base_rows or []) + list(update_rows or []):
        if not isinstance(row, dict):
            continue
        broker_type = str(row.get("brokerType") or "").strip().upper()
        system = str(row.get("system") or "").strip()
        station = str(row.get("station") or "").strip()
        if broker_type not in {"HUMAN", "GUARDIAN", "SIRIUS"} or not system or not station:
            continue
        identity = (
            broker_type,
            str(row.get("market_id") or ""),
            system.casefold(), station.casefold(),
        )
        merged[identity] = dict(row)
    return sorted(merged.values(), key=lambda row: (
        str(row.get("brokerType") or ""),
        float(row.get("distance_ly") or 1e12),
        float(row.get("distance_ls") or 1e12),
        str(row.get("station") or "").casefold(),
    ))
