import gzip
import hashlib
import json
import re
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pathlib import Path

from ed_companion import APP_VERSION

EDDN_UPLOAD_URL = "https://eddn.edcd.io:4430/upload/"
EDDN_RELAY_URL = "tcp://eddn.edcd.io:9500"
EDDN_SCHEMA_VALIDATED_AT = "2026-09-03"
EDDN_CAPI_ONLY_SCHEMAS = frozenset({"blackmarket/1", "fcmaterials_capi/1"})
EDDN_PENDING_JOB_LIMIT = 2000
# EDMC's established replay cadence is roughly two sequential messages/second.
EDDN_REPLAY_DELAY_MS = 400


def should_log_rejection(
    cache: dict[str, str], key: str, fingerprint: str, reason: str,
) -> bool:
    """Return true once for each distinct rejected source revision."""
    signature = f"{fingerprint}:{reason}"
    if cache.get(key) == signature:
        return False
    cache[key] = signature
    return True


def should_log_station_rejection(
    cache: dict[str, str], filename: str, fingerprint: str, reason: str,
) -> bool:
    """Keep the existing station-file rejection contract."""
    return should_log_rejection(cache, filename, fingerprint, reason)
SCHEMAS = {
    "Docked": "journal/1", "FSDJump": "journal/1",
    "Scan": "journal/1", "Location": "journal/1",
    "SAASignalsFound": "journal/1", "CarrierJump": "journal/1",
    "ApproachSettlement": "approachsettlement/1",
    "CodexEntry": "codexentry/1", "DockingDenied": "dockingdenied/1",
    "DockingGranted": "dockinggranted/1",
    "FCMaterials": "fcmaterials_journal/1",
    "FSSAllBodiesFound": "fssallbodiesfound/1",
    "FSSBodySignals": "fssbodysignals/1",
    "FSSDiscoveryScan": "fssdiscoveryscan/1",
    "FSSSignalDiscovered": "fsssignaldiscovered/1",
    "NavBeaconScan": "navbeaconscan/1", "NavRoute": "navroute/1",
    "ScanBaryCentre": "scanbarycentre/1",
    "ScanOrganic": "scanorganic/1",
}
REQUIRED = {
    "journal/1": {"timestamp", "event", "StarSystem", "StarPos", "SystemAddress"},
    "approachsettlement/1": {"timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "Name", "BodyID", "BodyName", "Latitude", "Longitude"},
    "codexentry/1": {"timestamp", "event", "System", "StarPos", "SystemAddress", "EntryID"},
    "dockingdenied/1": {"timestamp", "event", "MarketID", "StationName", "Reason"},
    "dockinggranted/1": {"timestamp", "event", "MarketID", "StationName"},
    "fcmaterials_journal/1": {"timestamp", "event", "MarketID", "CarrierName", "CarrierID", "Items"},
    "fssallbodiesfound/1": {"timestamp", "event", "SystemName", "StarPos", "SystemAddress", "Count"},
    "fssbodysignals/1": {"timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "BodyID", "Signals"},
    "fssdiscoveryscan/1": {"timestamp", "event", "SystemName", "StarPos", "SystemAddress", "BodyCount", "NonBodyCount"},
    "fsssignaldiscovered/1": {"timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "signals"},
    "navbeaconscan/1": {"timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "NumBodies"},
    "navroute/1": {"timestamp", "event", "Route"},
    "scanbarycentre/1": {"timestamp", "event", "StarSystem", "StarPos", "SystemAddress", "BodyID"},
    "scanorganic/1": {"timestamp", "event", "StarSystem", "StarPos", "ScanType", "Genus", "Species", "SystemAddress", "BodyID"},
    "commodity/3": {"timestamp", "systemName", "stationName", "marketId", "commodities"},
    "outfitting/2": {"timestamp", "systemName", "stationName", "marketId", "modules"},
    "outfitting/3": {"timestamp", "systemName", "stationName", "marketId", "modules"},
    "shipyard/2": {"timestamp", "systemName", "stationName", "marketId", "ships"},
}
ALLOWED = {
    "approachsettlement/1": {"timestamp","event","StarSystem","StarPos","SystemAddress","Name","MarketID","BodyID","BodyName","Latitude","Longitude","StationEconomies","StationEconomy","StationFaction","StationGovernment","StationServices","StationAllegiance","horizons","odyssey"},
    "codexentry/1": {"timestamp","event","EntryID","Name","SubCategory","Category","Region","System","SystemAddress","StarPos","BodyID","BodyName","Latitude","Longitude","NearestDestination","Traits","VoucherAmount","horizons","odyssey"},
    "dockingdenied/1": {"timestamp","event","MarketID","StationName","StationType","Reason","horizons","odyssey"},
    "dockinggranted/1": {"timestamp","event","MarketID","StationName","StationType","LandingPad","horizons","odyssey"},
    "fcmaterials_journal/1": {"timestamp","event","MarketID","CarrierName","CarrierID","Items","horizons","odyssey"},
    "fssallbodiesfound/1": {"timestamp","event","SystemName","StarPos","SystemAddress","Count","horizons","odyssey"},
    "fssbodysignals/1": {"timestamp","event","StarSystem","StarPos","SystemAddress","BodyID","BodyName","Signals","horizons","odyssey"},
    "fssdiscoveryscan/1": {"timestamp","event","SystemName","StarPos","SystemAddress","BodyCount","NonBodyCount","horizons","odyssey"},
    "fsssignaldiscovered/1": {"timestamp","event","StarSystem","StarPos","SystemAddress","signals","horizons","odyssey"},
    "navbeaconscan/1": {"timestamp","event","StarSystem","StarPos","SystemAddress","NumBodies","horizons","odyssey"},
    "navroute/1": {"timestamp","event","Route","horizons","odyssey"},
    "scanbarycentre/1": {"timestamp","event","StarSystem","StarPos","SystemAddress","BodyID","SemiMajorAxis","Eccentricity","OrbitalInclination","Periapsis","OrbitalPeriod","AscendingNode","MeanAnomaly","horizons","odyssey"},
    "scanorganic/1": {"timestamp","event","StarSystem","StarPos","ScanType","Genus","Species","Variant","SystemAddress","Body","BodyID","BodyName","Latitude","Longitude","horizons","odyssey"},
    "outfitting/3": {"timestamp","systemName","stationName","marketId","modules","horizons","odyssey"},
}
PRIVATE_FIELDS = {
    "Commander", "FID", "PrivateGroup", "Multicrew", "HappiestSystem",
    "HomeSystem", "MyReputation", "SquadronFaction",
}
JOURNAL_COMMON_ALLOWED = {
    "timestamp", "event", "StarSystem", "StarPos", "SystemAddress",
    "horizons", "odyssey",
}
JOURNAL_ALLOWED_BY_EVENT = {
    "Docked": JOURNAL_COMMON_ALLOWED | {
        "StationName", "StationType", "MarketID", "StationFaction",
        "StationGovernment", "StationAllegiance", "StationServices",
        "StationEconomy", "StationEconomies", "DistFromStarLS", "LandingPads",
    },
    "FSDJump": JOURNAL_COMMON_ALLOWED | {
        "Body", "BodyID", "BodyType", "Powers", "PowerplayState",
        "ControllingPower", "PowerplayConflictProgress",
        "PowerplayStateControlProgress", "PowerplayStateReinforcement",
        "PowerplayStateUndermining", "SystemFaction",
        "SystemAllegiance", "SystemEconomy", "SystemSecondEconomy",
        "SystemGovernment", "SystemSecurity", "Population", "Factions",
        "Conflicts", "ThargoidWar",
    },
    "Scan": JOURNAL_COMMON_ALLOWED | {
        "ScanType", "BodyName", "BodyID", "DistanceFromArrivalLS",
        "TidalLock", "TerraformState", "PlanetClass", "Atmosphere",
        "AtmosphereType", "AtmosphereComposition", "Volcanism", "MassEM",
        "Radius", "SurfaceGravity", "SurfaceTemperature", "SurfacePressure",
        "Landable", "Materials", "Composition", "SemiMajorAxis",
        "Eccentricity", "OrbitalInclination", "Periapsis", "OrbitalPeriod",
        "AscendingNode", "MeanAnomaly", "RotationPeriod", "AxialTilt", "Rings",
        "ReserveLevel", "WasDiscovered", "WasMapped", "StarType", "Subclass",
        "StellarMass", "AbsoluteMagnitude", "Age_MY", "Luminosity",
        "Parents", "WasFootfalled",
    },
    "Location": JOURNAL_COMMON_ALLOWED | {
        "Body", "BodyID", "BodyType", "Docked", "StationName", "StationType",
        "MarketID", "StationFaction", "StationGovernment", "StationAllegiance",
        "StationServices", "StationEconomy", "StationEconomies", "DistFromStarLS",
        "Powers", "PowerplayState", "SystemFaction", "SystemAllegiance",
        "ControllingPower", "PowerplayConflictProgress",
        "PowerplayStateControlProgress", "PowerplayStateReinforcement",
        "PowerplayStateUndermining",
        "SystemEconomy", "SystemSecondEconomy", "SystemGovernment",
        "SystemSecurity", "Population", "Factions", "Conflicts", "ThargoidWar",
    },
    "SAASignalsFound": JOURNAL_COMMON_ALLOWED | {
        "BodyName", "BodyID", "Signals", "Genuses",
    },
    "CarrierJump": JOURNAL_COMMON_ALLOWED | {
        "Body", "BodyID", "BodyType", "Docked", "StationName", "StationType",
        "MarketID", "StationFaction", "StationGovernment", "StationAllegiance",
        "StationServices", "StationEconomy", "StationEconomies", "DistFromStarLS",
        "Powers", "PowerplayState", "SystemFaction", "SystemAllegiance",
        "ControllingPower", "PowerplayConflictProgress",
        "PowerplayStateControlProgress", "PowerplayStateReinforcement",
        "PowerplayStateUndermining",
        "SystemEconomy", "SystemSecondEconomy", "SystemGovernment",
        "SystemSecurity", "Population", "Factions", "Conflicts", "ThargoidWar",
    },
}
JOURNAL_NESTED_ALLOWED = {
    "StationFaction": {"Name", "FactionState"},
    "StationEconomies": {"Name", "Proportion"},
    "LandingPads": {"Small", "Medium", "Large"},
    "SystemFaction": {"Name", "FactionState"},
    "Factions": {
        "Name", "FactionState", "Government", "Influence", "Allegiance",
        "Happiness", "ActiveStates", "PendingStates", "RecoveringStates",
        "State", "Trend",
    },
    "ThargoidWar": {
        "CurrentState", "NextState", "SuccessState", "WarProgress",
        "RemainingPorts", "EstimatedRemainingTime",
    },
    "AtmosphereComposition": {"Name", "Percent"},
    "Materials": {"Name", "Percent"},
    "Composition": {"Ice", "Rock", "Metal"},
    "Rings": {"Name", "RingClass", "MassMT", "InnerRad", "OuterRad"},
    "Signals": {"Type", "Count"},
    "Genuses": {"Genus"},
    "Conflicts": {
        "WarType", "Status", "Faction1", "Faction2", "Name", "Stake", "WonDays",
    },
    "Parents": {"Null", "Ring", "Star", "Planet"},
}


class EddnError(RuntimeError):
    def __init__(self, message, terminal=False, status_code=None):
        super().__init__(message)
        self.terminal = bool(terminal)
        self.status_code = status_code


def supported_schema_names():
    """Return every journal/snapshot schema ED\u00b7OPS can truthfully produce."""
    return frozenset(SCHEMAS.values()) | frozenset({
        "commodity/3", "outfitting/2", "outfitting/3", "shipyard/2",
    })


def schema_parity_report():
    """Expose the EDMC parity boundary without pretending CAPI is available."""
    supported = supported_schema_names()
    total = len(supported | EDDN_CAPI_ONLY_SCHEMAS)
    return {
        "status": "SUPPORTED",
        "supported": len(supported),
        "total": total,
        "validatedAt": EDDN_SCHEMA_VALIDATED_AT,
        "capiRequired": sorted(EDDN_CAPI_ONLY_SCHEMAS),
        "journalSchemas": len(frozenset(SCHEMAS.values())),
        "stationSchemas": 3,
    }


def _walk_public_fields(value, path="message"):
    if isinstance(value, dict):
        for key, child in value.items():
            field = str(key)
            if field in PRIVATE_FIELDS or field.endswith("_Localised"):
                raise EddnError(
                    f"EDDN validation rejected private field {path}.{field}.",
                    terminal=True,
                )
            _walk_public_fields(child, f"{path}.{field}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_public_fields(child, f"{path}[{index}]")


def validate_prepared(prepared):
    """Validate the local EDDN contract before a message enters the queue."""
    if not isinstance(prepared, dict):
        raise EddnError("EDDN validation requires a prepared object.", terminal=True)
    schema = str(prepared.get("schema") or "")
    if schema not in supported_schema_names():
        suffix = " (Frontier CAPI required)" if schema in EDDN_CAPI_ONLY_SCHEMAS else ""
        raise EddnError(
            f"Unsupported EDDN schema {schema or '<missing>'}{suffix}.",
            terminal=True,
        )
    message = prepared.get("message")
    if not isinstance(message, dict):
        raise EddnError("EDDN validation requires a message object.", terminal=True)
    missing = sorted(
        field for field in REQUIRED.get(schema, {"timestamp"})
        if message.get(field) is None
    )
    if missing:
        raise EddnError(
            f"EDDN {schema} validation missing: {', '.join(missing)}.",
            terminal=True,
        )
    if not isinstance(message.get("timestamp"), str) or not message["timestamp"].strip():
        raise EddnError(f"EDDN {schema} requires a timestamp string.", terminal=True)
    event_name = message.get("event")
    if schema not in {"commodity/3", "outfitting/2", "outfitting/3", "shipyard/2"}:
        if SCHEMAS.get(event_name) != schema:
            raise EddnError(
                f"EDDN event {event_name or '<missing>'} does not match {schema}.",
                terminal=True,
            )
    if "StarPos" in message:
        position = message["StarPos"]
        if (
            not isinstance(position, list) or len(position) != 3
            or any(not isinstance(value, (int, float)) for value in position)
        ):
            raise EddnError(f"EDDN {schema} requires numeric StarPos[3].", terminal=True)
    collection_field = {
        "commodity/3": "commodities",
        "outfitting/2": "modules",
        "outfitting/3": "modules",
        "shipyard/2": "ships",
    }.get(schema)
    if collection_field and not isinstance(message.get(collection_field), list):
        raise EddnError(
            f"EDDN {schema} requires {collection_field} as a list.",
            terminal=True,
        )
    if schema in {"commodity/3", "outfitting/2", "outfitting/3", "shipyard/2"}:
        for field in ("systemName", "stationName"):
            if not isinstance(message.get(field), str) or not message[field].strip():
                raise EddnError(
                    f"EDDN {schema} requires a non-empty {field}.", terminal=True,
                )
        if not isinstance(message.get("marketId"), int):
            raise EddnError(f"EDDN {schema} requires integer marketId.", terminal=True)
    if schema == "outfitting/3":
        unknown = sorted(set(message) - ALLOWED[schema])
        if unknown:
            raise EddnError(
                f"EDDN outfitting/3 has unsupported fields: {', '.join(unknown)}.",
                terminal=True,
            )
        for index, module in enumerate(message["modules"]):
            if not isinstance(module, dict):
                raise EddnError(
                    f"EDDN outfitting/3 module {index} must be an object.",
                    terminal=True,
                )
            missing_module = sorted(
                field for field in ("id", "Name", "BuyPrice", "BuyMercCoinsPrice")
                if module.get(field) is None
            )
            if missing_module:
                raise EddnError(
                    f"EDDN outfitting/3 module {index} missing: "
                    f"{', '.join(missing_module)}.", terminal=True,
                )
            if not isinstance(module["Name"], str) or not module["Name"].strip():
                raise EddnError(
                    f"EDDN outfitting/3 module {index} requires Name.", terminal=True,
                )
            for field in ("id", "BuyPrice", "BuyMercCoinsPrice"):
                if not isinstance(module[field], int) or isinstance(module[field], bool):
                    raise EddnError(
                        f"EDDN outfitting/3 module {index} requires integer {field}.",
                        terminal=True,
                    )
    if schema == "journal/1":
        allowed = JOURNAL_ALLOWED_BY_EVENT.get(str(event_name or ""), set())
        unknown = sorted(set(message) - allowed)
        if unknown:
            raise EddnError(
                f"EDDN journal/1 {event_name} has unsupported fields: "
                f"{', '.join(unknown)}.", terminal=True,
            )
        if _allowlisted_journal_message(message, str(event_name or "")) != message:
            raise EddnError(
                f"EDDN journal/1 {event_name} has unsupported nested fields.",
                terminal=True,
            )
    if schema == "scanorganic/1":
        unknown = sorted(set(message) - ALLOWED[schema])
        if unknown:
            raise EddnError(
                f"EDDN scanorganic/1 has unsupported fields: {', '.join(unknown)}.",
                terminal=True,
            )
        if message.get("ScanType") not in {"Log", "Sample"}:
            raise EddnError(
                "EDDN scanorganic/1 accepts only Log or Sample scans.",
                terminal=True,
            )
        for field in ("StarSystem", "Genus", "Species"):
            if not isinstance(message.get(field), str) or not message[field].strip():
                raise EddnError(
                    f"EDDN scanorganic/1 requires a non-empty {field}.",
                    terminal=True,
                )
        for field in ("SystemAddress", "BodyID"):
            if not isinstance(message.get(field), int) or isinstance(message[field], bool):
                raise EddnError(
                    f"EDDN scanorganic/1 requires integer {field}.",
                    terminal=True,
                )
    if schema in {"outfitting/2", "outfitting/3", "shipyard/2"} and not message[collection_field]:
        raise EddnError(
            f"EDDN {schema} requires at least one {collection_field} entry.",
            terminal=True,
        )
    _walk_public_fields(message)
    return True


class EddnRelayDecodeError(ValueError):
    """Raised when an EDDN ZeroMQ relay frame cannot be decoded safely."""


def upload_allowed(config):
    """Require both privacy consent and the independent upload switch."""
    return bool(
        isinstance(config, dict)
        and config.get("consent")
        and config.get("upload_enabled")
    )


def decode_relay_frame(raw):
    """Decode the zlib-compressed UTF-8 JSON object sent by the EDDN relay."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise EddnRelayDecodeError("EDDN relay frame is not binary.")
    try:
        decoded = zlib.decompress(bytes(raw)).decode("utf-8")
        payload = json.loads(decoded)
    except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EddnRelayDecodeError(
            f"Invalid EDDN relay frame: {type(exc).__name__}."
        ) from None
    if not isinstance(payload, dict):
        raise EddnRelayDecodeError("EDDN relay payload is not a JSON object.")
    return payload


def sanitize(value):
    if isinstance(value, dict):
        return {
            key: sanitize(item) for key, item in value.items()
            if item is not None and key not in PRIVATE_FIELDS
            and not key.endswith("_Localised")
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def _allowlisted_journal_nested(value, allowed):
    if isinstance(value, dict):
        return {
            key: _allowlisted_journal_nested(item, allowed)
            for key, item in value.items()
            if item is not None and key in allowed
            and key not in PRIVATE_FIELDS and not key.endswith("_Localised")
        }
    if isinstance(value, list):
        return [_allowlisted_journal_nested(item, allowed) for item in value]
    return value


def _allowlisted_journal_message(event, event_name):
    message = {}
    for key in JOURNAL_ALLOWED_BY_EVENT.get(event_name, set()):
        if key not in event or event[key] is None:
            continue
        value = event[key]
        nested_allowed = JOURNAL_NESTED_ALLOWED.get(key)
        if nested_allowed is not None:
            value = _allowlisted_journal_nested(value, nested_allowed)
        elif isinstance(value, dict) or (
            isinstance(value, list) and any(isinstance(item, dict) for item in value)
        ):
            # Structured fields require their own explicit child-field contract.
            continue
        message[key] = value
    return message


def repair_legacy_prepared(prepared):
    """Rebuild an old journal/1 job from today's explicit public allowlist."""
    if not isinstance(prepared, dict) or prepared.get("schema") != "journal/1":
        return None
    source = prepared.get("message")
    if not isinstance(source, dict):
        return None
    source = sanitize(source)
    event_name = str(source.get("event") or "")
    if event_name not in JOURNAL_ALLOWED_BY_EVENT:
        return None
    repaired = {
        "schema": "journal/1",
        "message": _allowlisted_journal_message(source, event_name),
    }
    try:
        validate_prepared(repaired)
    except EddnError:
        return None
    return repaired


def navroute_rejection_reason(event):
    """Return a precise local reason when a NavRoute cannot match EDDN v1."""
    if not isinstance(event, dict):
        return "NavRoute payload is not an object"
    route = event.get("Route")
    if route is None:
        return "NavRoute.json did not provide Route"
    if not isinstance(route, list):
        return "Route is not an array"
    for index, row in enumerate(route):
        prefix = f"Route[{index}]"
        if not isinstance(row, dict):
            return f"{prefix} is not an object"
        if not str(row.get("StarSystem") or "").strip():
            return f"{prefix} is missing StarSystem"
        address = row.get("SystemAddress")
        if not isinstance(address, int) or isinstance(address, bool):
            return f"{prefix} is missing integer SystemAddress"
        position = row.get("StarPos")
        if (
            not isinstance(position, list) or len(position) != 3
            or any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in position
            )
        ):
            return f"{prefix} requires three numeric StarPos values"
        if not str(row.get("StarClass") or "").strip():
            return f"{prefix} is missing StarClass"
    return ""


def merge_navroute_notification(notification, snapshot):
    """Combine the Journal notification with its public NavRoute.json data."""
    merged = dict(notification or {})
    if not isinstance(snapshot, dict):
        return merged, "NavRoute.json root is not an object"
    merged["Route"] = snapshot.get("Route")
    return merged, navroute_rejection_reason(merged)


def load_navroute_source(notification, path):
    """Load one NavRoute.json revision without exposing Commander data."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        return (
            dict(notification or {}), "missing",
            f"NavRoute.json could not be read: {type(exc).__name__}",
        )
    fingerprint = hashlib.sha256(raw).hexdigest()
    try:
        snapshot = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return (
            dict(notification or {}), fingerprint,
            f"NavRoute.json is invalid: {type(exc).__name__}",
        )
    merged, reason = merge_navroute_notification(notification, snapshot)
    return merged, fingerprint, reason


def update_context(context, event):
    context = dict(context or {})
    name = event.get("event")
    if name == "Fileheader":
        context["gameversion"] = str(event.get("gameversion") or "")
        context["gamebuild"] = str(event.get("build") or "")
        version_marker = " ".join((
            context["gameversion"], context["gamebuild"],
        )).casefold()
        context["schema_environment"] = (
            "legacy" if "legacy" in version_marker else
            "test" if event.get("Beta") is True or any(
                marker in version_marker
                for marker in ("alpha", "beta", "test")
            ) else "live"
        )
    if name == "LoadGame":
        for source, target in (("Horizons", "horizons"), ("Odyssey", "odyssey")):
            if source in event:
                context[target] = bool(event[source])
    if name in {
        "Location", "FSDJump", "CarrierJump", "Docked",
        "Market", "Outfitting", "Shipyard",
    }:
        for key in ("StarSystem", "StarPos", "SystemAddress"):
            if event.get(key) is not None:
                context[key] = event[key]
    if name in {"Location", "CarrierJump", "Docked", "Market", "Outfitting", "Shipyard"}:
        for key in ("StationName", "MarketID"):
            if event.get(key) is not None:
                context[key] = event[key]
        for key in (
            "StationType", "CarrierDockingAccess", "StationEconomies",
        ):
            if event.get(key) is not None:
                context[key] = sanitize(event[key])
    if name in {"Market", "Outfitting", "Shipyard"} and event.get("timestamp"):
        kind = name.casefold()
        context[f"{kind}Timestamp"] = event["timestamp"]
        # Station snapshots are written asynchronously. Preserve the complete
        # trigger identity per data kind so a later Docked/Location event cannot
        # make a valid snapshot look as if it belonged to another Commander.
        context[f"{kind}Context"] = {
            key: event.get(key, context.get(key))
            for key in (
                "timestamp", "StarSystem", "StationName", "MarketID",
                "StationType", "CarrierDockingAccess", "StationEconomies",
                "horizons", "odyssey",
            )
            if event.get(key, context.get(key)) is not None
        }
    return context


def rebuild_context(events, expected_fid=""):
    """Replay one Commander's Journal events into a restart-safe context."""
    context = {}
    expected = str(expected_fid or "").strip()
    accepted_profile = not expected
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if event.get("event") == "Fileheader":
            context = update_context(context, event)
            continue
        if event.get("event") == "LoadGame":
            identity = str(event.get("FID") or "").strip()
            accepted_profile = not expected or identity == expected
            if not accepted_profile:
                continue
        if accepted_profile:
            context = update_context(context, event)
    return context


def _symbolic_name(value):
    value = str(value or "").strip()
    if value.startswith("$"):
        value = value[1:]
    if value.casefold().endswith("_name;"):
        value = value[:-6]
    return value


def _economy_name(value):
    value = str(value or "").strip()
    if value.startswith("$"):
        value = value[1:]
    if value.endswith(";"):
        value = value[:-1]
    if value.casefold().startswith("economy_"):
        value = value[8:]
    return value


def station_snapshot_mismatch_reason(kind, snapshot, context):
    """Explain why a station snapshot does not match its Journal trigger."""
    kind = str(kind or "").casefold()
    trigger = context.get(f"{kind}Context")
    if not isinstance(trigger, dict):
        trigger = context
    system = str(snapshot.get("StarSystem") or "")
    station = str(snapshot.get("StationName") or "")
    market_id = snapshot.get("MarketID")
    if not system or not station or not isinstance(market_id, int):
        return "snapshot station identity is incomplete"
    timestamp = snapshot.get("timestamp")
    trigger_timestamp = trigger.get("timestamp") or context.get(
        f"{kind}Timestamp"
    )
    if not trigger_timestamp:
        return f"no {kind} Journal trigger exists for the active profile"
    if not timestamp or trigger_timestamp != timestamp:
        return f"{kind} timestamp does not match its Journal trigger"
    for key, actual in (
        ("StarSystem", system), ("StationName", station), ("MarketID", market_id)
    ):
        expected = trigger.get(key)
        if expected is None or expected == "":
            return f"{kind} Journal trigger has no {key}"
        if str(expected).casefold() != str(actual).casefold():
            return f"{kind} {key} does not match its Journal trigger"
    return ""


def _station_identity(kind, snapshot, context):
    reason = station_snapshot_mismatch_reason(kind, snapshot, context)
    if reason:
        return None
    trigger = context.get(f"{kind}Context")
    if not isinstance(trigger, dict):
        trigger = context
    system = str(snapshot.get("StarSystem") or "")
    station = str(snapshot.get("StationName") or "")
    market_id = snapshot.get("MarketID")
    timestamp = snapshot.get("timestamp")
    message = {
        "timestamp": timestamp,
        "systemName": system,
        "stationName": station,
        "marketId": market_id,
    }
    for source, target in (
        ("Horizons", "horizons"), ("Odyssey", "odyssey"),
        ("horizons", "horizons"), ("odyssey", "odyssey"),
    ):
        if source in snapshot:
            message[target] = bool(snapshot[source])
        elif target in trigger:
            message[target] = bool(trigger[target])
        elif target in context:
            message[target] = bool(context[target])
    if kind == "market":
        for key, target in (
            ("StationType", "stationType"),
            ("CarrierDockingAccess", "carrierDockingAccess"),
        ):
            value = snapshot.get(key, trigger.get(key, context.get(key)))
            if isinstance(value, str) and value.strip():
                message[target] = value.strip()
    return message


def prepare_station_snapshot(kind, snapshot, context):
    """Build a schema-exact EDDN station message from an Elite JSON snapshot."""
    snapshot = sanitize(dict(snapshot or {}))
    kind = str(kind or "").casefold()
    message = _station_identity(kind, snapshot, context or {})
    if message is None:
        return None
    trigger = (context or {}).get(f"{kind}Context")
    if not isinstance(trigger, dict):
        trigger = context or {}
    if kind == "market":
        commodities = []
        prohibited = []
        for source in snapshot.get("Items") or []:
            if not isinstance(source, dict):
                continue
            category = str(
                source.get("categoryname") or source.get("Category") or ""
            ).casefold()
            legality = str(
                source.get("legality") or source.get("Legality") or ""
            ).strip()
            name = _symbolic_name(source.get("Name") or source.get("name"))
            if not name:
                continue
            if source.get("Prohibited") is True or legality:
                prohibited.append(name)
                continue
            if "nonmarketable" in category:
                continue
            required = (
                "MeanPrice", "BuyPrice", "Stock", "StockBracket",
                "SellPrice", "Demand", "DemandBracket",
            )
            if any(source.get(field) is None for field in required):
                continue
            row = {
                "name": name,
                "meanPrice": source["MeanPrice"],
                "buyPrice": source["BuyPrice"],
                "stock": source["Stock"],
                "stockBracket": source["StockBracket"],
                "sellPrice": source["SellPrice"],
                "demand": source["Demand"],
                "demandBracket": source["DemandBracket"],
            }
            flags = [
                flag for flag, field in (
                    ("Producer", "Producer"),
                    ("Consumer", "Consumer"),
                    ("Rare", "Rare"),
                ) if source.get(field) is True
            ]
            if flags:
                row["statusFlags"] = flags
            commodities.append(row)
        message["commodities"] = commodities
        economy_source = (
            snapshot.get("Economies") or snapshot.get("StationEconomies")
            or trigger.get("StationEconomies") or context.get("StationEconomies")
            or []
        )
        economies = []
        for source in economy_source if isinstance(economy_source, list) else []:
            if not isinstance(source, dict):
                continue
            name = _economy_name(source.get("Name") or source.get("name"))
            proportion = source.get("Proportion", source.get("proportion"))
            if name and isinstance(proportion, (int, float)):
                economies.append({"name": name, "proportion": proportion})
        if economies:
            message["economies"] = sorted(economies, key=lambda row: row["name"].casefold())
        if prohibited:
            message["prohibited"] = sorted(set(prohibited), key=str.casefold)
        return {"schema": "commodity/3", "message": message}
    if kind == "outfitting":
        modules_v2 = []
        modules_v3 = []
        v3_complete = True
        for source in snapshot.get("Items") or []:
            name = source.get("Name") if isinstance(source, dict) else source
            name = str(name or "").strip()
            if not name or name.casefold() == "int_planetapproachsuite":
                continue
            if not re.search(r"(^hpt_|^int_|_armour_)", name, re.I):
                continue
            if isinstance(source, dict) and source.get("sku") not in {
                None, "ELITE_HORIZONS_V_PLANETARY_LANDINGS",
            }:
                continue
            modules_v2.append(name)
            if not isinstance(source, dict):
                v3_complete = False
                continue
            module_id = source.get("id")
            buy_price = source.get("BuyPrice")
            merc_price = source.get("BuyMercCoinsPrice", 0)
            if any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in (module_id, buy_price, merc_price)
            ):
                v3_complete = False
                continue
            modules_v3.append({
                "id": module_id, "Name": name, "BuyPrice": buy_price,
                "BuyMercCoinsPrice": merc_price,
            })
        if not modules_v2:
            return None
        if v3_complete and len(modules_v3) == len(modules_v2):
            message["modules"] = sorted(modules_v3, key=lambda row: row["Name"].casefold())
            return {"schema": "outfitting/3", "message": message}
        message["modules"] = sorted(set(modules_v2), key=str.casefold)
        return {"schema": "outfitting/2", "message": message}
    if kind == "shipyard":
        ships = []
        for source in snapshot.get("PriceList") or []:
            name = source.get("ShipType") if isinstance(source, dict) else source
            name = str(name or "").strip()
            if name:
                ships.append(name)
        if not ships:
            return None
        message["ships"] = sorted(set(ships), key=str.casefold)
        if "AllowCobraMkIV" in snapshot:
            message["allowCobraMkIV"] = bool(snapshot["AllowCobraMkIV"])
        return {"schema": "shipyard/2", "message": message}
    return None


def prepare_event(event, context):
    if (context or {}).get("schema_environment") == "legacy":
        return None
    event = sanitize(dict(event or {}))
    name = event.get("event")
    schema = SCHEMAS.get(name)
    if not schema:
        return None
    if name == "ScanOrganic":
        if event.get("ScanType") not in {"Log", "Sample"}:
            return None
        if event.get("Body") is not None:
            event["BodyID"] = event.pop("Body")
        if any(context.get(key) is None for key in (
            "StarSystem", "StarPos", "SystemAddress",
        )):
            return None
        event["StarSystem"] = context["StarSystem"]
        event["StarPos"] = context["StarPos"]
    if name == "FSSSignalDiscovered":
        if event.get("USSType") == "$USS_Type_MissionTarget;":
            return None
        signal = dict(event)
        signal.pop("event", None)
        signal.pop("TimeRemaining", None)
        signal.pop("SystemAddress", None)
        event = {
            "timestamp": event.get("timestamp"),
            "event": name,
            "StarSystem": context.get("StarSystem"),
            "StarPos": context.get("StarPos"),
            "SystemAddress": context.get("SystemAddress"),
            "signals": [signal],
        }
    elif schema == "journal/1":
        event = _allowlisted_journal_message(event, name)
    else:
        event = {
            key: value for key, value in event.items()
            if key in ALLOWED.get(schema, set())
        }
    if name == "NavRoute" and isinstance(event.get("Route"), list):
        event["Route"] = [
            {
                key: row[key]
                for key in ("StarSystem", "SystemAddress", "StarPos", "StarClass")
                if isinstance(row, dict) and key in row
            }
            if isinstance(row, dict) else row
            for row in event["Route"]
        ]
    address = event.get("SystemAddress")
    if address is not None and context.get("SystemAddress") not in {None, address}:
        return None
    if name == "CodexEntry":
        event.setdefault("System", context.get("StarSystem"))
    elif name in {"FSSAllBodiesFound", "FSSDiscoveryScan"}:
        event.setdefault("SystemName", context.get("StarSystem"))
    elif name != "NavRoute":
        event.setdefault("StarSystem", context.get("StarSystem"))
    if name != "NavRoute":
        event.setdefault("StarPos", context.get("StarPos"))
        event.setdefault("SystemAddress", context.get("SystemAddress"))
    for flag in ("horizons", "odyssey"):
        if flag in context:
            event.setdefault(flag, bool(context[flag]))
    if any(event.get(field) is None for field in REQUIRED.get(schema, ())):
        return None
    if name == "NavRoute" and navroute_rejection_reason(event):
        return None
    return {"schema": schema, "message": event}


def supports_event(event):
    """Return whether an event is intended for an EDDN upload schema."""
    return isinstance(event, dict) and event.get("event") in SCHEMAS


def envelope(prepared, context, uploader_id, version=APP_VERSION):
    validate_prepared(prepared)
    schema_root = "test/" if context.get("schema_environment") == "test" else ""
    return {
        "$schemaRef": (
            f"https://eddn.edcd.io/schemas/{schema_root}{prepared['schema']}"
        ),
        "header": {
            "uploaderID": str(uploader_id),
            "softwareName": "ED Engineering Companion",
            "softwareVersion": str(version),
            "gameversion": str(context.get("gameversion") or ""),
            "gamebuild": str(context.get("gamebuild") or ""),
        },
        "message": sanitize(prepared["message"]),
    }


def send(prepared, context, uploader_id, opener=urlopen, timeout=20):
    payload = envelope(prepared, context, uploader_id)
    raw = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
    request = Request(
        EDDN_UPLOAD_URL, data=raw, method="POST",
        headers={
            "Content-Encoding": "gzip", "Content-Type": "application/json",
            "User-Agent": f"ED-Engineering-Companion/{APP_VERSION}",
        },
    )
    started = time.monotonic()
    try:
        with opener(request, timeout=timeout) as response:
            status = int(response.status)
            preview = response.read(700).decode("utf-8", errors="replace")
    except HTTPError as exc:
        status = int(exc.code)
        try:
            detail = exc.read(700).decode("utf-8", errors="replace").strip()
        except (OSError, AttributeError):
            detail = ""
        schema_note = (
            " Schema/message rejected; refresh EDEC schema support before retrying."
            if status in {400, 413, 426} else ""
        )
        raise EddnError(
            f"EDDN rejected the message (HTTP {status}).{schema_note}"
            + (f" Gateway: {detail[:300]}" if detail else ""),
            terminal=status in {400, 413, 426}, status_code=status,
        ) from None
    except (OSError, URLError) as exc:
        raise EddnError(f"EDDN network error: {exc}") from None
    if status != 200:
        raise EddnError(
            f"EDDN returned HTTP {status}.",
            terminal=status in {400, 413, 426}, status_code=status,
        )
    return {
        "httpStatus": status,
        "response": preview[:100],
        "elapsedMs": int((time.monotonic() - started) * 1000),
        "schema": prepared["schema"],
        "event": str(prepared["message"].get("event") or ""),
    }
