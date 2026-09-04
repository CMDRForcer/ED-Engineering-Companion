"""Central, read-only data contract for a future Mining Finder."""

from types import MappingProxyType


MINING_EVIDENCE_LEVELS = (
    "LOCAL_CONFIRMED",
    "LIVE_REPORTED",
    "CATALOG_CANDIDATE",
)

# EDEC display policy only; these values are not Frontier yield guarantees.
MINING_FRESHNESS_SECONDS = MappingProxyType({
    "LOCAL_CONFIRMED": 30 * 86400,
    "LIVE_REPORTED": 24 * 3600,
    "CATALOG_CANDIDATE": 30 * 86400,
})

# Only fields documented by Frontier and already used or accepted by EDEC are
# listed. Localised and identity-bearing fields are deliberately absent.
LOCAL_MINING_EVENT_FIELDS = MappingProxyType({
    "Scan": frozenset({
        "timestamp", "SystemAddress", "BodyName", "BodyID",
        "DistanceFromArrivalLS", "PlanetClass", "Rings", "ReserveLevel",
    }),
    "SAASignalsFound": frozenset({
        "timestamp", "SystemAddress", "BodyName", "BodyID", "Signals",
    }),
    "ProspectedAsteroid": frozenset({
        "timestamp", "Materials", "Content", "MotherlodeMaterial", "Remaining",
    }),
    "MiningRefined": frozenset({"timestamp", "Type"}),
    "Cargo": frozenset({"timestamp", "Vessel", "Inventory"}),
    "MarketSell": frozenset({
        "timestamp", "MarketID", "Type", "Count", "SellPrice", "TotalSale",
    }),
    "LaunchSRV": frozenset({"timestamp", "SRVType", "ID"}),
    "DockSRV": frozenset({"timestamp", "SRVType", "ID"}),
})

MINING_SOURCE_POLICY = MappingProxyType({
    "frontier_journal": MappingProxyType({
        "role": "authoritative local observation and session history",
        "events": tuple(LOCAL_MINING_EVENT_FIELDS),
        "network": False,
    }),
    "eddn": MappingProxyType({
        "role": "live public ring and hotspot observations",
        "events": ("Scan", "SAASignalsFound"),
        "network": True,
        "privacy": "existing journal/1 allowlist and schema validation",
    }),
    "spansh": MappingProxyType({
        "role": "searchable system, body, ring and station catalog",
        "endpoint": "/dump/{id64}",
        "required_shape": "system.{id64,name,coords,date,bodies[].rings[]}",
        "classification": "CATALOG_CANDIDATE",
        "network": True,
        "freshness_required": True,
    }),
})

MINING_FINDER_OPEN_QUESTIONS = (
    "No Rhino-specific deposit or prospecting field is assumed without an observed Journal contract.",
    "No hotspot, ring reserve or prospector sample is treated as a guaranteed yield.",
    "No INARA endpoint is used as a general galaxy or mining-target search source.",
)


def mining_finder_contract():
    """Return a serialisable copy for diagnostics, tests and future UI work."""
    return {
        "evidenceLevels": list(MINING_EVIDENCE_LEVELS),
        "freshnessSeconds": dict(MINING_FRESHNESS_SECONDS),
        "localEvents": {
            event: sorted(fields)
            for event, fields in LOCAL_MINING_EVENT_FIELDS.items()
        },
        "sources": {
            name: dict(policy) for name, policy in MINING_SOURCE_POLICY.items()
        },
        "openQuestions": list(MINING_FINDER_OPEN_QUESTIONS),
    }
