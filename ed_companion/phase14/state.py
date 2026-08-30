"""Read-only Phase 14 view model built from existing app and Journal data."""

import json
import hashlib
import logging
import math
import os
import re
import threading
import uuid
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .session_views import (
    SESSION_HISTORY_LIMIT,
    apply_session_event,
    normalize_session_history,
    public_session,
)

from ed_companion.journal import (
    is_completed_engineer_craft,
    journal_material_name,
    material_event_changes,
    rebuild_fleet,
    trader_type_evidence_from_event,
)
from ed_companion.engineering import engineer_unlock_signals, load_unlock_catalog
from ed_companion.navigation import (
    build_trader_route,
    find_nearest_catalog_trader,
    extract_local_hge_sightings,
    extract_local_state_finds,
    is_hge_material,
    local_hge_scan_status,
    local_state_find_scan_status,
    merge_trader_catalog,
    plan_material_trades,
    spansh_trader_type_evidence,
    trade_batch,
    trade_matches_trader,
    TraderTypeCache,
    resolve_trader_type,
)
from ed_companion.navigation.trader import is_material_tradeable
from ed_companion.navigation.trader_type_cache import normalize_timestamp
from ed_companion.trader_config import HEURISTIC_TRADER_WARNING
from ed_companion.material_integrity import material_key
from ed_companion.persistence import atomic_write
from ed_companion.build_import import JOURNAL_EXPERIMENTAL_NAMES


LOGGER = logging.getLogger(__name__)

MATERIAL_STATUS = ("READY", "PARTIAL", "MISSING")
PROGRESS_STATUS = ("NOT STARTED", "IN PROGRESS", "COMPLETE")
GRADE_STATUS_LABELS = {
    "not_applicable": "NOT APPLICABLE",
    "not_started": "NOT STARTED",
    "in_progress": "IN PROGRESS",
    "completed": "COMPLETE",
}
EXPERIMENTAL_STATUS_LABELS = {
    "not_applicable": "NOT PLANNED",
    "pending": "PENDING",
    "completed": "APPLIED",
}
BLUEPRINT_ID_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "ed_data" / "blueprint_id_catalog.json"
)
MATERIAL_CATEGORIES = {"Raw", "Manufactured", "Encoded"}
# Odyssey microresources deliberately use their own Data/Item/Component/
# Consumable model and are outside ship engineering's three categories.

ENGINEERING_CATEGORY_ORDER = [
    "Core Internals",
    "Optional Internals",
    "Weapons / Hardpoints",
    "Utility Mounts",
    "Limpets / Controllers",
]

ENGINEERING_MODULE_CATEGORIES = {
    "Core Internals": {
        "Armour", "Frame Shift Drive", "Life Support", "Power Distributor",
        "Power Plant", "Sensors", "Thrusters",
    },
    "Optional Internals": {
        "Auto Field-Maintenance Unit", "Frame Shift Drive Interdictor",
        "Fuel Scoop", "Hull Reinforcement Package", "Refinery",
        "Shield Cell Bank", "Shield Generator", "Surface Scanner",
    },
    "Weapons / Hardpoints": {
        "Beam Laser", "Burst Laser", "Cannon", "Fragment Cannon",
        "Mine Launcher", "Missile Rack", "Multi-cannon",
        "Plasma Accelerator", "Pulse Laser", "Rail Gun", "Torpedo Pylon",
    },
    "Utility Mounts": {
        "Chaff Launcher", "Electronic Countermeasure", "Heat Sink Launcher",
        "Kill Warrant Scanner", "Manifest Scanner", "Point Defence",
        "Shield Booster", "Wake Scanner",
    },
    "Limpets / Controllers": {
        "Collector Limpet Controller", "Fuel Transfer Limpet Controller",
        "Hatch Breaker Limpet Controller", "Prospector Limpet Controller",
    },
}

# Journal/CAPI module symbols use Frontier's internal family names, which are
# often unrelated to the player-facing blueprint type.  Prefixes are explicit
# so similarly named families (Cannon/Multi-cannon and Pulse/Burst Laser) can
# never bind to one another.  These families cover every engineering module
# type exposed by ENGINEERING_MODULE_CATEGORIES.
ENGINEERING_MODULE_ID_PREFIXES: dict[str, tuple[str, ...]] = {
    "frameshiftdrive": ("inthyperdrive",),
    "lifesupport": ("intlifesupport",),
    "powerdistributor": ("intpowerdistributor",),
    "powerplant": ("intpowerplant",),
    "sensors": ("intsensors",),
    "thrusters": ("intengine",),
    "autofieldmaintenanceunit": ("intrepairer",),
    "frameshiftdriveinterdictor": ("intfsdinterdictor",),
    "fuelscoop": ("intfuelscoop",),
    "hullreinforcementpackage": ("inthullreinforcement",),
    "refinery": ("intrefinery",),
    "shieldcellbank": ("intshieldcellbank",),
    "shieldgenerator": ("intshieldgenerator",),
    "surfacescanner": ("intdetailedsurfacescanner",),
    "beamlaser": ("hptbeamlaser",),
    "burstlaser": ("hptpulselaserburst",),
    "cannon": ("hptcannon",),
    "fragmentcannon": ("hptslugshot",),
    "minelauncher": ("hptminelauncher",),
    "missilerack": (
        "hptbasicmissilerack", "hptdumbfiremissilerack",
        "hptdrunkmissilerack",
    ),
    "multicannon": ("hptmulticannon",),
    "plasmaaccelerator": ("hptplasmaaccelerator",),
    "pulselaser": ("hptpulselaser",),
    "railgun": ("hptrailgun",),
    "torpedopylon": ("hptadvancedtorppylon",),
    "chafflauncher": ("hptchafflauncher",),
    "electroniccountermeasure": ("hptelectroniccountermeasure",),
    "heatsinklauncher": ("hptheatsinklauncher",),
    "killwarrantscanner": ("hptcrimescanner",),
    "manifestscanner": ("hptcargoscanner",),
    "pointdefence": ("hptplasmapointdefence",),
    "shieldbooster": ("hptshieldbooster",),
    "wakescanner": ("hptcloudscanner",),
    "collectorlimpetcontroller": ("intdronecontrolcollection",),
    "fueltransferlimpetcontroller": ("intdronecontrolfueltransfer",),
    "hatchbreakerlimpetcontroller": ("intdronecontrolresourcesiphon",),
    "prospectorlimpetcontroller": ("intdronecontrolprospector",),
}


def real_engineers(record):
    return [
        str(engineer) for engineer in (record.get("Engineers", []) or [])
        if engineer and not str(engineer).startswith("@")
    ]


def load_blueprint_id_catalog(
    path: Path = BLUEPRINT_ID_CATALOG_PATH,
    learned_path: Path | None = None,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load immutable bundled IDs plus one profile-isolated learned overlay."""
    catalog: dict[tuple[str, int], dict[str, Any]] = {}
    for source_path in (path, learned_path):
        if source_path is None:
            continue
        records = read_json(source_path, [])
        seen: set[tuple[str, int]] = set()
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            key = (
                str(record.get("blueprint_name") or ""),
                int(record.get("level", 0) or 0),
            )
            if (
                not key[0] or key[1] <= 0
                or record.get("source") != "journal_confirmed"
                or record.get("blueprint_id") is None
            ):
                raise ValueError(f"Invalid BlueprintID catalog record: {record!r}")
            if key in seen:
                raise ValueError(f"Duplicate BlueprintID catalog key: {key!r}")
            seen.add(key)
            if key in catalog:
                if str(catalog[key]["blueprint_id"]) != str(record["blueprint_id"]):
                    LOGGER.warning(
                        "Learned BlueprintID conflicts with bundled catalog; "
                        "catalog unchanged: %s / G%s / bundled=%s / learned=%s",
                        key[0], key[1], catalog[key]["blueprint_id"],
                        record["blueprint_id"],
                    )
                continue
            catalog[key] = record
    return catalog


def learn_blueprint_id_catalog(
    events: list[dict[str, Any]], learned_path: Path,
    base_path: Path = BLUEPRINT_ID_CATALOG_PATH,
) -> dict[str, int]:
    """Persist only unambiguous completed Journal craft identities per profile."""
    bundled = load_blueprint_id_catalog(base_path)
    existing_records = read_json(learned_path, [])
    existing_records = existing_records if isinstance(existing_records, list) else []
    existing = load_blueprint_id_catalog(learned_path) if learned_path.exists() else {}
    evidence: dict[tuple[str, int], set[str]] = defaultdict(set)
    raw_ids: dict[tuple[str, int, str], Any] = {}
    for event in events or []:
        if not isinstance(event, dict) or not is_completed_engineer_craft(event):
            continue
        name = str(event.get("BlueprintName") or "").strip()
        level = int(event.get("Level", 0) or 0)
        blueprint_id = event.get("BlueprintID")
        if not name or level <= 0 or blueprint_id in (None, ""):
            continue
        key = (name, level)
        identity = str(blueprint_id)
        evidence[key].add(identity)
        raw_ids[(name, level, identity)] = blueprint_id

    learned = conflicts = ambiguous = 0
    additions = []
    for key, ids in sorted(evidence.items()):
        if len(ids) != 1:
            ambiguous += 1
            LOGGER.warning(
                "Ambiguous Journal BlueprintID evidence ignored: %s / G%s / %s",
                key[0], key[1], sorted(ids),
            )
            continue
        identity = next(iter(ids))
        known = bundled.get(key) or existing.get(key)
        if known:
            if str(known["blueprint_id"]) != identity:
                conflicts += 1
                LOGGER.warning(
                    "BlueprintID catalog contradiction; Journal wins at runtime, "
                    "catalog unchanged: %s / G%s / catalog=%s / journal=%s",
                    key[0], key[1], known["blueprint_id"], identity,
                )
            continue
        additions.append({
            "blueprint_name": key[0], "level": key[1],
            "blueprint_id": raw_ids[(key[0], key[1], identity)],
            "source": "journal_confirmed",
        })
        learned += 1
    if additions:
        merged = existing_records + additions
        merged.sort(key=lambda row: (
            str(row.get("blueprint_name") or "").casefold(),
            int(row.get("level", 0) or 0),
        ))
        _write_json_if_changed(learned_path, merged)
    return {"learned": learned, "conflicts": conflicts, "ambiguous": ambiguous}


def blueprint_id_evidence(
    event: dict[str, Any],
    catalog: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify Journal BlueprintID evidence without mutating the catalog."""
    name = str(event.get("BlueprintName") or "")
    level = int(event.get("Level", 0) or 0)
    journal_id = event.get("BlueprintID")
    entry = (catalog or load_blueprint_id_catalog()).get((name, level))
    if entry is None:
        LOGGER.warning(
            "Unknown BlueprintID learned from Journal: %s / G%s / %s",
            name, level, journal_id,
        )
        return {
            "status": "unknown", "source": "journal_learned_unknown",
            "blueprint_id": journal_id,
        }
    if str(entry["blueprint_id"]) != str(journal_id):
        # The local Journal is authoritative evidence of what the game
        # actually applied. Keep the static catalog immutable for diagnosis.
        LOGGER.warning(
            "BlueprintID catalog contradiction; Journal wins: %s / G%s / "
            "catalog=%s / journal=%s",
            name, level, entry["blueprint_id"], journal_id,
        )
        return {
            "status": "conflict", "source": "journal_override_conflict",
            "blueprint_id": journal_id, "catalog_id": entry["blueprint_id"],
        }
    return {
        "status": "confirmed", "source": "journal_confirmed",
        "blueprint_id": journal_id,
    }


def engineering_module_category(module):
    for category in ENGINEERING_CATEGORY_ORDER:
        if module in ENGINEERING_MODULE_CATEGORIES[category]:
            return category
    return "Other"


def normalize(name: object) -> str:
    return material_key(name)


def canonical_module_id(value: object) -> str:
    """Normalize Frontier's wrapped and plain module symbols identically."""
    symbol = str(value or "").strip().strip("$;")
    if symbol.casefold().endswith("_name"):
        symbol = symbol[:-5]
    return symbol.casefold()


def app_data_dir() -> Path:
    """Return the writable application root, never the installation tree."""
    root = Path(
        os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    ) / "EDEngineeringCompanion"
    root.mkdir(parents=True, exist_ok=True)
    return root


_JOURNAL_EVENT_CACHE: dict[str, Any] = {
    "root": "", "revision": 0, "files": {}, "profile_views": {},
    "logbook_views": {}, "session_views": {}, "last_rebuild_revision": 0,
}
_JOURNAL_EVENT_CACHE_LOCK = threading.RLock()
_CRAFT_BATCH_LOCK = threading.RLock()
_JOURNAL_POLL_FILE_LIMIT = 32


def clear_journal_event_cache() -> None:
    """Invalidate parsed Journal data after an explicit source change."""
    with _JOURNAL_EVENT_CACHE_LOCK:
        _JOURNAL_EVENT_CACHE.update({
            "root": "", "revision": 0, "files": {}, "profile_views": {},
            "logbook_views": {}, "session_views": {},
            "last_rebuild_revision": 0,
        })


def journal_change_signature() -> tuple[str, tuple[tuple[str, int, int], ...]]:
    """Return metadata for the most recent Journal files without reading them."""
    root = journal_dir()
    try:
        paths = sorted(
            root.glob("Journal.*.log"), key=lambda path: path.name,
        )[-_JOURNAL_POLL_FILE_LIMIT:]
        files = []
        for path in paths:
            stat = path.stat()
            files.append((path.name, int(stat.st_size), int(stat.st_mtime_ns)))
    except OSError:
        return str(root), ()
    return str(root), tuple(files)


def journal_paths_for_profile(identity: str) -> list[Path]:
    """Return cached Journal files that contain sessions for one identity."""
    identity = str(identity or "").strip()
    if not identity:
        return []
    _journal_snapshot()
    root = journal_dir()
    paths: list[Path] = []
    with _JOURNAL_EVENT_CACHE_LOCK:
        for name in sorted(_JOURNAL_EVENT_CACHE["files"]):
            session_identity = ""
            matched = False
            for event in _JOURNAL_EVENT_CACHE["files"][name]["events"]:
                if event.get("event") == "LoadGame":
                    session_identity = str(
                        event.get("FID") or event.get("Commander") or ""
                    ).strip()
                if session_identity == identity:
                    matched = True
                    break
            if matched:
                paths.append(root / name)
    return paths


def _read_journal_tail(
    path: Path, offset: int, existing: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    """Append complete JSON lines and retain an incomplete trailing line."""
    events = list(existing)
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        handle.seek(max(0, offset))
        committed = handle.tell()
        while True:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith(("\n", "\r")):
                committed = line_start
                break
            committed = handle.tell()
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(event, dict):
                events.append(event)
    return committed, events


def _journal_guard(path: Path, offset: int) -> tuple[int, str]:
    """Fingerprint bytes before the append cursor to detect rewrites."""
    start = max(0, int(offset) - 256)
    with path.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(max(0, int(offset) - start))
    return start, hashlib.sha256(payload).hexdigest()


def _journal_snapshot() -> tuple[int, list[dict[str, Any]]]:
    """Return chronologically ordered events, parsing only changed file tails."""
    root = str(journal_dir().resolve())
    with _JOURNAL_EVENT_CACHE_LOCK:
        if _JOURNAL_EVENT_CACHE["root"] != root:
            _JOURNAL_EVENT_CACHE.update({
                "root": root, "revision": 0, "files": {}, "profile_views": {},
                "logbook_views": {}, "session_views": {},
                "last_rebuild_revision": 0,
            })
        try:
            paths = sorted(journal_dir().glob("Journal.*.log"), key=lambda path: path.name)
        except OSError:
            paths = []
        cached_files = _JOURNAL_EVENT_CACHE["files"]
        current_names = {path.name for path in paths}
        changed = any(name not in current_names for name in cached_files)
        append_only = not changed and bool(cached_files)
        for name in list(cached_files):
            if name not in current_names:
                del cached_files[name]
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            old = cached_files.get(path.name)
            signature = (int(stat.st_size), int(stat.st_mtime_ns))
            if old and old.get("signature") == signature:
                continue
            offset = int(old.get("offset", 0)) if old else 0
            existing = list(old.get("events", [])) if old else []
            if old is None:
                if not cached_files or path.name < max(cached_files):
                    append_only = False
            elif path.name != paths[-1].name or int(stat.st_size) <= offset:
                append_only = False
            if old and int(stat.st_size) > offset:
                try:
                    guard = _journal_guard(path, offset)
                except OSError:
                    continue
                if tuple(old.get("guard", ())) != guard:
                    offset, existing = 0, []
                    append_only = False
            if old and int(stat.st_size) < offset:
                offset, existing = 0, []
            elif old and int(stat.st_size) == offset:
                # Same-size rewrites cannot be appended safely.
                offset, existing = 0, []
            try:
                offset, events = _read_journal_tail(path, offset, existing)
            except OSError:
                continue
            cached_files[path.name] = {
                "signature": signature, "offset": offset, "events": events,
                "guard": _journal_guard(path, offset),
            }
            changed = True
        if changed:
            _JOURNAL_EVENT_CACHE["revision"] += 1
            _JOURNAL_EVENT_CACHE["profile_views"] = {}
            if not append_only:
                _JOURNAL_EVENT_CACHE["last_rebuild_revision"] = int(
                    _JOURNAL_EVENT_CACHE["revision"]
                )
        events = [
            event for path in paths
            for event in cached_files.get(path.name, {}).get("events", [])
        ]
        return int(_JOURNAL_EVENT_CACHE["revision"]), events


def _journal_profile_identity() -> tuple[str, str]:
    """Return the selected Frontier identity and display name from LoadGame."""
    requested = str(os.environ.get("EDOPS_PROFILE_FID") or "").strip()
    _revision, events = _journal_snapshot()
    candidates: list[tuple[str, str, str]] = []
    for event in events:
        if event.get("event") == "LoadGame":
            identity = str(event.get("FID") or event.get("Commander") or "").strip()
            if identity:
                candidates.append((
                    str(event.get("timestamp") or ""), identity,
                    str(event.get("Commander") or "Commander"),
                ))
    if requested:
        match = max((row for row in candidates if row[1] == requested), default=None)
        if match:
            return match[1], match[2]
        return "", ""
    latest = max(candidates, default=None)
    return (latest[1], latest[2]) if latest else ("", "")


def active_profile_key() -> str:
    identity, _name = _journal_profile_identity()
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16] if identity else "unidentified"


def active_profile_identity() -> tuple[str, str]:
    """Expose the selected Journal identity without leaking it into filenames."""
    return _journal_profile_identity()


def runtime_data_dir(package_root: Path | None = None) -> Path:
    """Return a profile-isolated writable directory under LOCALAPPDATA."""
    candidate = app_data_dir() / f"profile-{active_profile_key()}"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def reference_data_dir(package_root: Path) -> Path:
    """Return immutable reference data shipped with this exact app release.

    A legacy installer copied the complete ``ed_data`` directory into
    LOCALAPPDATA.  That directory also contains writable commander data, so it
    cannot simply be deleted, but its old material and blueprint catalogs must
    never override the version-coherent catalogs bundled with a newer release.
    """
    return Path(package_root) / "ed_data"


def journal_dir() -> Path:
    configured = str(os.environ.get("EDOPS_JOURNAL_DIR") or "").strip()
    config_file = app_data_dir() / "journal_path.txt"
    if not configured:
        try:
            configured = config_file.read_text(encoding="utf-8").strip()
        except OSError:
            configured = ""
    return Path(configured) if configured else (
        Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
    )


def set_journal_dir(path: object) -> bool:
    value = Path(str(path or "").strip()).expanduser()
    if not value.is_dir():
        return False
    atomic_write(app_data_dir() / "journal_path.txt", str(value))
    clear_journal_event_cache()
    return True


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return default


def material_metadata(data_dir: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(data_dir / "engineering_materials.json", {})
    entry_payload = read_json(data_dir / "entryData.json", {})
    raw_database = read_json(data_dir / "raw_materials_database.json", {})
    raw_materials = raw_database.get("materials", {})
    raw_sites = raw_database.get("sites", {})
    storage_limits = raw_database.get("storage_limits", {})
    entry_index = {}
    values = (
        entry_payload.values() if isinstance(entry_payload, dict)
        else entry_payload if isinstance(entry_payload, list) else []
    )
    for entry in values:
        if not isinstance(entry, dict):
            continue
        for value in (entry.get("Name"), entry.get("FormattedName")):
            key = normalize(value)
            if key:
                entry_index[key] = entry
    result = {}
    for item in payload.get("materials", []):
        key = normalize(item.get("canonical_key") or item.get("journal_name") or item.get("name"))
        if key:
            display_key = normalize(item.get("name"))
            entry = entry_index.get(key) or entry_index.get(display_key, {})
            raw_info = raw_materials.get(key) or raw_materials.get(display_key) or {}
            category = str(item.get("category") or "unknown")
            if category not in MATERIAL_CATEGORIES:
                LOGGER.warning("Material %s has unresolved category %s", key, category)
                category = "unknown"
            grade = int(raw_info.get("grade", item.get("grade", 0)) or 0)
            raw_capacity = storage_limits.get(str(grade)) if raw_info else None
            raw_sources = []
            for site_id in raw_info.get("site_ids", []) or []:
                site = raw_sites.get(site_id)
                if isinstance(site, dict):
                    raw_sources.append({**site, "site_id": site_id})
                else:
                    LOGGER.warning("Raw material %s references unknown site %s", key, site_id)
            availability_labels = {
                "surface": "Surface prospecting",
                "asteroids": "Asteroid mining",
            }
            raw_origins = [
                availability_labels.get(str(value), str(value))
                for value in (raw_info.get("availability", []) or [])
            ]
            result[key] = {
                "Name": item.get("name") or key,
                "Category": category,
                "Rarity": item.get("rarity") or "",
                "Grade": grade,
                "MaxCapacity": (
                    int(raw_capacity)
                    if raw_capacity not in (None, "") else
                    int(item["max_capacity"])
                    if item.get("max_capacity") not in (None, "") else None
                ),
                "TraderGroup": str(
                    f"Category{raw_info['trader_category']}" if raw_info else
                    item.get("trader_group") or entry.get("Group")
                    or item.get("subkind") or ""
                ),
                "Tradeable": bool(item.get("tradeable", True)),
                "Origins": [
                    str(value) for value in (
                        raw_origins
                        or item.get("origin_details")
                        or entry.get("OriginDetails") or []
                    )
                    if value
                ],
                "ExactSources": raw_sources,
                "RawAvailability": list(raw_info.get("availability", []) or []),
                "PreferredFarmMethod": str(raw_info.get("preferred_method") or ""),
                "RawTraderCategory": int(raw_info.get("trader_category", 0) or 0),
                "UsedIn": [
                    {
                        "module": str(usage.get("type") or "Module"),
                        "blueprint": str(usage.get("blueprint") or "Blueprint"),
                        "grade": int(usage.get("grade", 0) or 0),
                        "amount": int(usage.get("amount", 0) or 0),
                        "engineers": ", ".join(
                            str(engineer)
                            for engineer in (usage.get("engineers", []) or [])
                            if engineer and not str(engineer).startswith("@")
                        ),
                    }
                    for usage in (item.get("used_in", []) or [])
                    if isinstance(usage, dict)
                ],
            }
            result[key]["Guidance"] = actionable_source_card(key, result[key])
    top_raw_by_group = {
        info["TraderGroup"]: info
        for info in result.values()
        if info.get("Category") == "Raw"
        and int(info.get("Grade", 0) or 0) == 4
        and info.get("ExactSources")
    }
    for info in result.values():
        if info.get("Category") != "Raw" or int(info.get("Grade", 0) or 0) >= 4:
            continue
        top = top_raw_by_group.get(info.get("TraderGroup"))
        if not top:
            continue
        trade_sources = []
        for source in top.get("ExactSources", []):
            candidate = dict(source)
            candidate["kind"] = "TRADE_DOWN"
            candidate["label"] = "NEAREST FAST ROUTE · RAW TRADER"
            candidate["verified"] = False
            candidate["confidence"] = "derived"
            candidate["target"] = str(top.get("Name") or "")
            candidate["method"] = (
                f"Collect {top.get('Name')} here, then use the nearest Raw "
                f"Material Trader to trade within {info.get('TraderGroup')} "
                f"down to {info.get('Name')}."
            )
            trade_sources.append(candidate)
        info["ExactSources"] = list(info.get("ExactSources", []) or []) + trade_sources
    return result


def canonical_cargo_materials(data_dir: Path) -> set[str]:
    """Return cargo commodities explicitly required by bundled recipes."""
    entries = read_json(data_dir / "entryData.json", {})
    values = (
        entries.values() if isinstance(entries, dict)
        else entries if isinstance(entries, list) else []
    )
    commodities = {
        normalize(entry.get("FormattedName") or entry.get("Name"))
        for entry in values if isinstance(entry, dict)
        and entry.get("Kind") == "Commodity"
    }
    blueprints = read_json(data_dir / "blueprints.json", [])
    required = {
        normalize(ingredient.get("Name"))
        for blueprint in blueprints if isinstance(blueprints, list)
        and isinstance(blueprint, dict)
        for ingredient in (blueprint.get("Ingredients", []) or [])
        if isinstance(ingredient, dict)
    }
    return {key for key in required if key and key in commodities}


def _newer_station_location(
    candidate: dict[str, Any], previous: dict[str, Any] | None,
) -> bool:
    """Prefer timestamped Journal evidence; otherwise retain event order."""
    if not previous:
        return True
    candidate_time = str(candidate.get("timestamp") or "")
    previous_time = str(previous.get("timestamp") or "")
    if candidate_time:
        return not previous_time or candidate_time >= previous_time
    return not previous_time


def technology_broker_unlock_guide(
    package_root: Path,
    metadata: dict[str, dict[str, Any]],
    inventory: dict[str, int],
    events: list[dict[str, Any]],
    broker_catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build Human and Guardian Tech Broker guides from canonical recipes."""
    config = read_json(
        reference_data_dir(package_root) / "tech_broker_unlocks.json", {}
    )
    guidance_by_broker = config.get("guidance", {}) if isinstance(config, dict) else {}
    origin_guidance = config.get("origins", {}) if isinstance(config, dict) else {}
    special_destinations = (
        config.get("special_destinations", {}) if isinstance(config, dict) else {}
    )
    journal_unlock_aliases = (
        config.get("journal_unlock_aliases", {})
        if isinstance(config, dict) else {}
    )
    recipes: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    recipe_names: dict[str, str] = {}
    canonical_records = read_json(
        reference_data_dir(package_root) / "blueprints.json", []
    )
    for record in canonical_records:
        if not isinstance(record, dict):
            continue
        broker = str(record.get("Type") or "").strip().title()
        if broker not in {"Guardian", "Human"} \
                or "@Technology" not in (record.get("Engineers", []) or []):
            continue
        blueprint = str(record.get("Name") or "").strip()
        for ingredient in record.get("Ingredients", []) or []:
            if not isinstance(ingredient, dict):
                continue
            material_name = str(
                ingredient.get("Name_Localised")
                or ingredient.get("Name") or ""
            ).strip()
            key = normalize(ingredient.get("Name") or material_name)
            amount = max(0, int(ingredient.get("Size", 0) or 0))
            if blueprint and key and amount:
                recipes[(broker, blueprint)][key] = amount
                recipe_names[key] = material_name or key
    if not recipes:
        for key, material in metadata.items():
            for usage in material.get("UsedIn", []) or []:
                broker = str(usage.get("module") or "").strip().title()
                blueprint = str(usage.get("blueprint") or "").strip()
                amount = max(0, int(usage.get("amount", 0) or 0))
                if broker in {"Guardian", "Human"} and blueprint and amount:
                    recipes[(broker, blueprint)][key] = amount
                    recipe_names[key] = str(material.get("Name") or key)
    entry_payload = read_json(
        reference_data_dir(package_root) / "entryData.json", []
    )
    entry_values = (
        entry_payload.values() if isinstance(entry_payload, dict)
        else entry_payload if isinstance(entry_payload, list) else []
    )
    ingredient_entries = {
        normalize(entry.get("FormattedName") or entry.get("Name")): entry
        for entry in entry_values if isinstance(entry, dict)
    }

    unlocked_tokens: set[str] = set()
    unlocked_tokens_by_broker: dict[str, set[str]] = defaultdict(set)
    unlocked_recipe_keys: set[tuple[str, str]] = set()
    owned_tokens: set[str] = set()
    configured_destinations = (
        config.get("recommended_destinations", {})
        if isinstance(config, dict) else {}
    )
    broker_destinations: dict[str, dict[str, str]] = {
        broker: {
            "system": str(value.get("system") or "").strip(),
            "station": str(value.get("station") or "").strip(),
            "evidence": str(
                value.get("evidence") or "Bundled recommendation"
            ).strip(),
        }
        for broker, value in configured_destinations.items()
        if broker in {"Human", "Guardian"}
        and isinstance(value, dict)
        and str(value.get("system") or "").strip()
    }
    station_locations_by_id: dict[str, dict[str, Any]] = {}
    station_locations_by_name: dict[str, dict[str, Any]] = {}
    current_system = ""
    current_station = ""
    for event in events:
        if event.get("StarSystem"):
            next_system = str(event.get("StarSystem") or "").strip()
            if next_system != current_system:
                current_station = ""
            current_system = next_system
        if event.get("StationName"):
            current_station = str(event.get("StationName") or "").strip()
        station_name = str(event.get("StationName") or "").strip()
        station_system = str(event.get("StarSystem") or current_system).strip()
        if station_name and station_system and event.get("event") in {
            "Docked", "Location", "Market", "Outfitting", "Shipyard",
            "StoredModules", "TechnologyBroker",
        }:
            station_location = {
                "system": station_system,
                "station": station_name,
                "marketId": str(event.get("MarketID") or "").strip(),
                "distance_ls": event.get("DistFromStarLS"),
                "timestamp": str(event.get("timestamp") or "").strip(),
                "evidence": "Journal-confirmed station location",
            }
            station_token = normalize(station_name)
            previous = station_locations_by_name.get(station_token)
            if _newer_station_location(station_location, previous):
                station_locations_by_name[station_token] = station_location
            if station_location["marketId"]:
                previous = station_locations_by_id.get(
                    station_location["marketId"]
                )
                if _newer_station_location(station_location, previous):
                    station_locations_by_id[
                        station_location["marketId"]
                    ] = station_location
        if event.get("event") == "TechnologyBroker":
            broker_type_raw = str(event.get("BrokerType") or "").strip()
            broker_type = broker_type_raw.title()
            if broker_type in {"Human", "Guardian"} and current_system:
                broker_destinations[broker_type] = {
                    "system": current_system,
                    "station": current_station,
                    "evidence": "Journal-confirmed TechnologyBroker visit",
                }
            items_unlocked = event.get("ItemsUnlocked", []) or []
            if isinstance(items_unlocked, dict):
                items_unlocked = [items_unlocked]
            elif not isinstance(items_unlocked, list):
                items_unlocked = [items_unlocked]
            for item in items_unlocked:
                value = item.get("Name") if isinstance(item, dict) else item
                token = normalize(value)
                if token:
                    unlocked_tokens.add(token)
                    if broker_type_raw:
                        unlocked_tokens_by_broker[
                            broker_type_raw.upper()
                        ].add(token)
            consumed: dict[str, int] = {}
            for item in (
                list(event.get("Materials", []) or [])
                + list(event.get("Commodities", []) or [])
            ):
                if not isinstance(item, dict):
                    continue
                key = normalize(item.get("Name") or item.get("Name_Localised"))
                count = max(0, int(item.get("Count", 0) or 0))
                if key and count:
                    consumed[key] = consumed.get(key, 0) + count
            event_broker = broker_type_raw.upper()
            if consumed:
                for recipe_key, requirement in recipes.items():
                    recipe_broker, recipe_name = recipe_key
                    recipe_subtype = (
                        "SIRIUS"
                        if recipe_broker == "Human"
                        and recipe_name.casefold().startswith("sirius ")
                        else recipe_broker.upper()
                    )
                    if recipe_subtype == event_broker \
                            and requirement == consumed:
                        unlocked_recipe_keys.add(recipe_key)
        module_values = []
        if event.get("event") == "Loadout":
            module_values.extend(
                item.get("Item") for item in event.get("Modules", []) or []
                if isinstance(item, dict)
            )
        elif event.get("event") == "StoredModules":
            module_values.extend(
                item.get("Name") for item in event.get("Items", []) or []
                if isinstance(item, dict)
            )
        else:
            for field in (
                "BuyItem", "RetrievedItem", "StoredItem", "SellItem",
            ):
                module_values.append(event.get(field))
        owned_tokens.update(normalize(value) for value in module_values if value)

    rows = []
    for (broker, name), requirement in recipes.items():
        guidance = guidance_by_broker.get(broker, {})
        name_token = normalize(name)
        direct_unlock = any(
            token == name_token or name_token in token or token in name_token
            for token in unlocked_tokens | owned_tokens
            if token
        )
        broker_subtype = (
            "SIRIUS" if broker == "Human" and name.casefold().startswith("sirius ")
            else broker.upper()
        )
        alias_tokens = {
            normalize(value)
            for value in journal_unlock_aliases.get(name, [])
            if value
        }
        unlocked = (broker, name) in unlocked_recipe_keys or direct_unlock or bool(
            alias_tokens
            & unlocked_tokens_by_broker.get(broker_subtype, set())
        )
        materials = []
        for key, need in requirement.items():
            material = metadata.get(key, {})
            ingredient_entry = ingredient_entries.get(key, {})
            have = max(0, int(inventory.get(key, 0) or 0))
            material_name = str(material.get("Name") or recipe_names.get(key) or key)
            category = str(
                material.get("Category") or ingredient_entry.get("Kind") or ""
            )
            if "blueprint" in key:
                origin = str(origin_guidance.get("blueprint") or "Guardian structure")
            elif category.casefold() == "encoded" or "pattern" in key:
                origin = str(origin_guidance.get("data") or "Guardian obelisk scans")
            elif "guardian" in key or "guardian" in material_name.casefold():
                origin = str(origin_guidance.get("component") or "Guardian structure")
            else:
                origins = [
                    str(value) for value in (
                        material.get("Origins")
                        or ingredient_entry.get("OriginDetails") or []
                    ) if value
                ]
                origin = " · ".join(origins[:2]) or str(
                    origin_guidance.get("conventional") or "Standard material sources"
                )
            materials.append({
                "key": key,
                "name": material_name,
                "category": category or "Commodity",
                "have": have,
                "need": need,
                "missing": max(0, need - have),
                "ready": have >= need,
                "blueprint": "blueprint" in key,
                "origin": origin,
            })
        materials.sort(key=lambda row: (not row["blueprint"], row["name"].casefold()))
        ready = bool(materials) and all(row["ready"] for row in materials)
        has_progress = any(row["have"] > 0 for row in materials)
        status = (
            "unlocked" if unlocked else "ready" if ready
            else "pending" if has_progress else "locked"
        )
        blueprint_ready = all(
            row["ready"] for row in materials if row["blueprint"]
        )
        components_ready = all(
            row["ready"] for row in materials if not row["blueprint"]
        )
        if broker == "Guardian":
            steps = [
                {
                    "label": "1 · Acquire blueprint segment",
                    "detail": str(guidance.get("blueprint") or "Visit a Guardian structure."),
                    "state": "complete" if blueprint_ready or unlocked else "active",
                },
                {
                    "label": "2 · Collect Guardian materials",
                    "detail": str(guidance.get("components") or "Collect the recipe materials."),
                    "state": (
                        "complete" if components_ready or unlocked
                        else "active" if blueprint_ready else "blocked"
                    ),
                },
                {
                    "label": "3 · Unlock at Guardian Tech Broker",
                    "detail": str(guidance.get("broker") or "Visit a Guardian Technology Broker."),
                    "state": "complete" if unlocked else "active" if ready else "blocked",
                },
            ]
        else:
            steps = [
                {
                    "label": "1 · Collect recipe materials",
                    "detail": str(guidance.get("components") or "Collect the listed materials."),
                    "state": "complete" if components_ready or unlocked else "active",
                },
                {
                    "label": "2 · Unlock at Human Tech Broker",
                    "detail": str(guidance.get("broker") or "Visit a Human Technology Broker."),
                    "state": "complete" if unlocked else "active" if ready else "blocked",
                },
            ]
        category = "HUMAN TECH" if broker == "Human" else (
            "FIGHTERS" if "fighter" in name.casefold()
            else "WEAPONS" if any(
                value in name.casefold()
                for value in ("cannon", "charger", "gauss")
            ) else "MODULES"
        )
        candidates = [
            dict(row) for row in (broker_catalog or [])
            if isinstance(row, dict)
            and str(row.get("brokerType") or "").upper() == broker_subtype
            and row.get("system") and row.get("station")
        ]
        if broker_subtype == "SIRIUS":
            candidates.extend({
                **dict(row),
                "brokerType": "SIRIUS",
                "source": "Bundled Sirius Tech Broker catalog",
                "verified": "2026-08-15",
            } for row in special_destinations.get("SIRIUS", [])
              if isinstance(row, dict) and row.get("system") and row.get("station"))
        resolved_candidates = []
        for candidate in candidates:
            resolved = dict(candidate)
            market_id = str(
                resolved.get("marketId") or resolved.get("market_id")
                or resolved.get("MarketID") or ""
            ).strip()
            station_token = normalize(resolved.get("station"))
            journal_location = (
                station_locations_by_id.get(market_id) if market_id else None
            ) or station_locations_by_name.get(station_token)
            if journal_location:
                resolved["system"] = journal_location["system"]
                resolved["station"] = journal_location["station"]
                resolved["source"] = journal_location["evidence"]
                resolved["verified"] = journal_location["timestamp"]
                if journal_location.get("distance_ls") is not None:
                    resolved["distance_ls"] = journal_location["distance_ls"]
            resolved_candidates.append(resolved)
        candidates = resolved_candidates
        if broker_subtype != "SIRIUS":
            candidates.sort(key=lambda row: (
                float(row.get("distance_ly") or 1e12),
                float(row.get("distance_ls") or 1e12),
                str(row.get("station") or "").casefold(),
            ))
        destination = dict(broker_destinations.get(broker, {}))
        if candidates:
            destination = {
                "system": str(candidates[0].get("system") or ""),
                "station": str(candidates[0].get("station") or ""),
                "evidence": str(candidates[0].get("source") or "Broker catalog"),
            }
        elif destination.get("system"):
            candidates = [{
                "brokerType": broker_subtype,
                "system": str(destination.get("system") or ""),
                "station": str(destination.get("station") or ""),
                "source": str(destination.get("evidence") or "Bundled recommendation"),
            }]
        next_step = next(
            (step["label"].split(" · ", 1)[-1] for step in steps
             if step["state"] == "active"),
            "Unlocked",
        )
        active_step = next(
            (step for step in steps if step["state"] == "active"),
            steps[-1] if steps else {},
        )
        blueprint_names = [
            row["name"] for row in materials if row["blueprint"]
        ]
        required_total = sum(row["need"] for row in materials)
        owned_required = sum(min(row["have"], row["need"]) for row in materials)
        rows.append({
            "name": name,
            "broker": broker.upper(),
            "brokerSubtype": broker_subtype,
            "category": category,
            "status": status,
            "statusText": status.upper(),
            "readyMaterials": sum(row["ready"] for row in materials),
            "totalMaterials": len(materials),
            "missingTotal": sum(row["missing"] for row in materials),
            "missingKinds": sum(not row["ready"] for row in materials),
            "requiredTotal": required_total,
            "ownedRequired": owned_required,
            "progress": owned_required / required_total if required_total else 1.0,
            "materials": materials,
            "steps": steps,
            "nextAction": next_step,
            "nextActionDetail": str(active_step.get("detail") or ""),
            "prerequisite": (
                " + ".join(blueprint_names) if blueprint_names
                else "Complete Human Tech Broker material recipe"
            ),
            "source": str(config.get("source") or "Bundled Guardian catalog"),
            "destinationSystem": str(destination.get("system") or ""),
            "destinationStation": str(destination.get("station") or ""),
            "destinationEvidence": str(destination.get("evidence") or ""),
            "brokerDestinations": candidates[:8],
        })
    broker_order = {
        name: index for index, name in enumerate(
            config.get("broker_order", []) if isinstance(config, dict) else []
        )
    }
    category_order = {
        name: index for index, name in enumerate(
            config.get("category_order", []) if isinstance(config, dict) else []
        )
    }
    status_order = {"ready": 0, "pending": 1, "locked": 2, "unlocked": 3}
    ordered = sorted(rows, key=lambda row: (
        broker_order.get(row["broker"], 99),
        category_order.get(row["category"], 99),
        status_order.get(row["status"], 99),
        row["name"].casefold(),
    ))
    for index, row in enumerate(ordered, 1):
        row["sequence"] = index
    return ordered


RAW_GROUP_FARMS = {
    "Category1": ("Yttrium", "Outotz LS-K d8-3", "B 5 A"),
    "Category2": ("Technetium", "HIP 36601", "C 5 A"),
    "Category3": ("Ruthenium", "HIP 36601", "C 1 D"),
    "Category4": ("Selenium", "HR 3230", "3 A A"),
    "Category5": ("Tellurium", "HIP 36601", "C 3 B"),
    "Category6": ("Polonium", "HIP 36601", "C 1 A"),
    "Category7": ("Antimony", "Outotz LS-K d8-3", "B 5 C"),
}


def actionable_source_card(material_key, info):
    """Return practical guidance even when the upstream database is generic."""
    name = str(info.get("Name") or material_key)
    category = str(info.get("Category") or "")
    grade = int(info.get("Grade", 0) or 0)
    group = str(info.get("TraderGroup") or "")
    origins = " · ".join(str(value) for value in info.get("Origins", []) or [])
    folded = origins.casefold()

    if category == "Raw":
        farm = RAW_GROUP_FARMS.get(group)
        if farm:
            top_name, system, body = farm
            if name == top_name:
                target = (
                    "Phloem Excretion on Brain Trees"
                    if name == "Selenium" else "Crystalline Shards"
                )
                return {
                    "kind": "RAW_FARM",
                    "label": "DIRECT RAW FARM",
                    "detail": (
                        f"{system} · {body} → {target} · Map the body with "
                        "the DSS, land in the highlighted biological terrain, "
                        "shoot the material-bearing growth with the SRV and scoop it."
                    ),
                    "system": system,
                    "body": body,
                    "target": target,
                    "verified": False,
                    "confidence": "derived",
                }
            return {
                "kind": "TRADE_DOWN",
                "label": "FAST ROUTE · RAW TRADER",
                "detail": (
                    f"Collect {top_name} at {system} · {body}, then visit a Raw "
                    f"Material Trader and trade within {group} down to {name}. "
                    "The trade planner calculates the protected amount."
                ),
                "system": system,
                "body": body,
                "target": top_name,
                "verified": False,
                "confidence": "derived",
            }

    if "guardian" in folded or group.startswith("Guardian"):
        action = (
            "At a Guardian structure, deploy the SRV. Scan active obelisks for "
            "pattern data; shoot destructible panels for components. For a "
            "blueprint segment, charge all pylons, defeat the Sentinels, jettison "
            "the required relic at the altar and scan the activated data core."
        )
        return {
            "kind": "GUARDIAN",
            "label": "GUARDIAN STRUCTURE",
            "detail": f"Target: {name} · {action}",
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    if "thargoid" in folded or group.startswith("Thargoid"):
        return {
            "kind": "THARGOID",
            "label": "THARGOID SALVAGE",
            "detail": (
                f"Target: {name} · Use a known Thargoid surface site or salvage "
                "from the specified Thargoid vessel type. Scan first, destroy only "
                "the named growth/component, then collect it with the SRV or cargo scoop."
            ),
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    if category == "Manufactured":
        if "high grade emission" in folded or grade >= 5:
            condition = origins or "High Grade Emissions or mission rewards"
            return {
                "kind": "HGE",
                "label": "DYNAMIC HGE TARGET",
                "detail": (
                    f"Target: {name} · Required source condition: {condition}. "
                    "Use the Galaxy Map to find a populated system matching the "
                    "allegiance/state, scan the Nav Beacon or use the FSS, then "
                    "enter HIGH GRADE EMISSIONS and collect with limpets."
                ),
                "target": name,
                "verified": False,
                "confidence": "derived",
            }
        return {
            "kind": "SALVAGE",
            "label": "SHIP / SIGNAL SALVAGE",
            "detail": (
                f"Target: {name} · {origins or 'Ship and signal-source salvage'}. "
                "Visit a busy Nav Beacon or Resource Extraction Site, scan ships, "
                "collect debris from the specified ship class with limpets. "
                "Faster alternative: collect the G5 material in the same trader "
                f"group ({group}) and trade down at a Manufactured Trader."
            ),
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    if category == "Encoded":
        if "wake" in folded:
            return {
                "kind": "WAKE_SCAN",
                "label": "HIGH-WAKE SCANNING",
                "detail": (
                    f"Target: {name} · Fit a Frame Shift Wake Scanner, wait outside "
                    "a busy station or at a Distribution Centre, target HIGH ENERGY "
                    "WAKES left by departing ships and scan them. Mission rewards "
                    "and an Encoded Trader are the alternatives."
                ),
                "target": "High Energy Wake",
                "verified": False,
                "confidence": "derived",
            }
        if "surface data point" in folded:
            return {
                "kind": "DATA_POINT",
                "label": "SURFACE DATA POINT",
                "detail": (
                    f"Target: {name} · Land at a surface settlement or data-point "
                    "POI, deploy the SRV, select the DATA POINT in Contacts and scan "
                    "it with the Data Link Scanner. Observe trespass/security warnings."
                ),
                "target": "Surface Data Point",
                "verified": False,
                "confidence": "derived",
            }
        if "ship scanning" in folded or "deep space data beacon" in folded:
            return {
                "kind": "SHIP_SCAN",
                "label": "SHIP / BEACON SCANNING",
                "detail": (
                    f"Target: {name} · {origins}. Visit a busy Nav Beacon or "
                    "Supercruise traffic lane, target the specified ship class and "
                    "complete the normal sensor scan. No special scanner is required "
                    "unless the source explicitly names a wake or data beacon."
                ),
                "target": name,
                "verified": False,
                "confidence": "derived",
            }
        return {
            "kind": "ENCODED_FARM",
            "label": "ENCODED DATA ROUTE",
            "detail": (
                f"Target: {name} · {origins or 'Encoded signal and mission data'}. "
                "For repeatable trade stock, scan the four beacons at the Jameson "
                "Crash Site on HIP 12099 · 1 B, then use an Encoded Material Trader "
                f"to trade into {group}. Direct mission rewards remain lossless."
            ),
            "system": "HIP 12099",
            "body": "1 B",
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    return {
        "kind": "SOURCE",
        "label": "DOCUMENTED SOURCE",
        "detail": f"Target: {name} · {origins or 'Open the material database for sources.'}",
        "target": name,
        "verified": False,
        "confidence": "derived" if origins else "heuristic",
    }


def _source_distance(source, current_position):
    star_pos = source.get("star_pos")
    if (
        not isinstance(current_position, (list, tuple))
        or len(current_position) != 3
        or not isinstance(star_pos, (list, tuple))
        or len(star_pos) != 3
    ):
        return None
    try:
        return math.sqrt(sum(
            (float(left) - float(right)) ** 2
            for left, right in zip(current_position, star_pos)
        ))
    except (TypeError, ValueError):
        return None


def source_cards(material_key, info, current_position=None):
    cards = []
    role_priority = {
        "PRIMARY": 0,
        "NEARER_ALTERNATIVE": 1,
        "RELOG_ALTERNATIVE": 2,
    }
    indexed_sources = list(enumerate(info.get("ExactSources", []) or []))

    def source_priority(indexed_source):
        index, source = indexed_source
        distance = _source_distance(source, current_position)
        role_rank = role_priority.get(str(source.get("role") or ""), 3)
        if distance is not None:
            return (0, distance, role_rank, index)
        return (1, role_rank, index, 0)

    exact_sources = [source for _index, source in sorted(
        indexed_sources, key=source_priority
    )]
    for source in exact_sources:
        if not isinstance(source, dict):
            continue
        location = " · ".join(
            value for value in (
                str(source.get("system") or ""),
                str(source.get("body") or ""),
                str(source.get("coordinates") or ""),
            ) if value
        )
        alternate_coordinates = [
            str(value) for value in (source.get("alternate_coordinates", []) or [])
            if value
        ]
        if alternate_coordinates:
            location += " · Alt: " + ", ".join(alternate_coordinates)
        target = str(source.get("target") or "")
        method = str(source.get("method") or "")
        distance = _source_distance(source, current_position)
        if distance is not None:
            location += (
                (" · " if location else "")
                + f"{distance:.1f} ly from current position"
            )
        summary = location
        if target:
            summary += (" → " if summary else "") + target
        if method:
            summary += (" · " if summary else "") + method
        verified = bool(source.get("verified", False))
        cards.append({
            "kind": str(source.get("kind") or "SOURCE"),
            "role": str(source.get("role") or ""),
            "label": str(source.get("label") or "VERIFIED LOCATION"),
            "detail": summary,
            "system": str(source.get("system") or ""),
            "body": str(source.get("body") or ""),
            "coordinates": str(source.get("coordinates") or ""),
            "alternateCoordinates": alternate_coordinates,
            "target": target,
            "method": method,
            "verified": verified,
            "confidence": "verified" if verified else str(
                source.get("confidence") or "derived"
            ),
            "distanceLy": round(distance, 1) if distance is not None else -1,
        })
    if not cards:
        cards.append(info.get("Guidance") or actionable_source_card(material_key, info))
    for origin in info.get("Origins", []) or []:
        text = str(origin)
        lowered = text.casefold()
        if "high grade emission" in lowered or "hge" in lowered:
            kind, label = "HGE", "HIGH GRADE EMISSIONS"
        elif "mission" in lowered:
            kind, label = "MISSION", "MISSION REWARD"
        elif "wake" in lowered or "scan" in lowered:
            kind, label = "SCAN", "SCANNING"
        elif "surface" in lowered or "geological" in lowered:
            kind, label = "SURFACE", "PLANET SURFACE"
        elif "mining" in lowered:
            kind, label = "MINING", "MINING"
        elif "signal" in lowered or "ship" in lowered:
            kind, label = "SALVAGE", "SIGNAL / SHIP SALVAGE"
        else:
            kind, label = "SOURCE", "DOCUMENTED SOURCE"
        cards.append({
            "kind": kind, "label": label, "detail": text,
            "verified": False, "confidence": "heuristic",
        })
    if info.get("Category") == "Raw" and int(info.get("Grade", 0) or 0) < 4:
        cards.append({
            "kind": "TRADE_DOWN",
            "label": "FAST ALTERNATIVE · RAW MATERIAL TRADER",
            "verified": False,
            "confidence": "derived",
            "detail": (
                "Collect a higher-grade Raw material at a suggested farm, then "
                "trade down within the same material group. The app protects "
                "materials required by the active build."
            ),
        })
    if is_hge_material(material_key) and not any(
        card["kind"] == "HGE" for card in cards
    ):
        cards.insert(0, {
            "kind": "HGE",
            "label": "HIGH GRADE EMISSIONS",
            "verified": False,
            "confidence": "derived",
            "detail": "Derived HGE guidance; use live signal intelligence when available.",
        })
    return cards


def material_trade_options(target, required, inventory, metadata, limit=8):
    target_meta = metadata.get(target, {})
    if not is_material_tradeable(target_meta):
        return []
    target_grade = int(target_meta.get("Grade", 0) or 0)
    target_group = target_meta.get("TraderGroup")
    deficit = (
        int((required or {}).get(target, 0) or 0)
        - int((inventory or {}).get(target, 0) or 0)
    )
    if deficit <= 0:
        return []
    options = []
    for source, stock in (inventory or {}).items():
        source_meta = metadata.get(source, {})
        protected = int((required or {}).get(source, 0) or 0)
        surplus = max(0, int(stock or 0) - protected)
        if (
            source == target or surplus <= 0
            or source_meta.get("Category") != target_meta.get("Category")
            or not is_material_tradeable(source_meta)
        ):
            continue
        same_group = source_meta.get("TraderGroup") == target_group
        batch = trade_batch(
            source_meta.get("Grade"), target_grade, same_group
        )
        if not batch:
            continue
        batch_in, batch_out = batch
        possible = surplus // batch_in
        if possible <= 0:
            continue
        batches = min(possible, max(1, (deficit + batch_out - 1) // batch_out))
        spend, receive = batches * batch_in, batches * batch_out
        options.append({
            "sourceKey": source,
            "sourceName": str(source_meta.get("Name") or source),
            "spend": spend,
            "receive": receive,
            "batchIn": batch_in,
            "batchOut": batch_out,
            "stock": int(stock or 0),
            "protected": protected,
            "surplus": surplus,
            "sameGroup": same_group,
            "reason": (
                "Same trader row; best exchange efficiency."
                if same_group else
                "Cross-row exchange; protected build stock remains untouched."
            ),
        })
    options.sort(key=lambda row: (
        not row["sameGroup"],
        -(row["batchOut"] / row["batchIn"]),
        -row["receive"],
        row["sourceName"].casefold(),
    ))
    return options[:limit]


def ship_journal_events(
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Read career-wide ship events using Frontier filename chronology."""
    return [
        event for event in (
            events if events is not None else profiled_journal_events()
        )
        if event.get("event") in {
            "LoadGame", "Loadout", "ShipyardBuy", "ShipyardSell",
            "ShipyardSwap", "ShipyardTransfer", "StoredShips",
            "SetUserShipName", "Docked", "Undocked",
            "ModuleBuy", "ModuleRetrieve", "ModuleSell", "ModuleStore",
            "ModuleSwap",
        }
    ]


def _write_json_if_changed(path: Path, payload: object) -> None:
    if read_json(path, None) == payload:
        return
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def reconcile_fleet_cache(
    data_dir: Path, fleet_state: dict[str, Any]
) -> tuple[dict[str, list[Any]], dict[str, str]]:
    """Migrate wishlist labels by ShipID and replace stale fleet metadata."""
    old_metadata = read_json(data_dir / "ship_metadata.json", {})
    old_plans = read_json(data_dir / "ship_blueprints.json", {})
    old_plans = old_plans if isinstance(old_plans, dict) else {}
    labels_by_id = {
        str(row["id"]): str(label)
        for label, row in (old_metadata.items() if isinstance(old_metadata, dict) else [])
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    live_by_id = {str(row["id"]): row for row in fleet_state.get("ships", [])}
    aliases: dict[str, str] = {}
    migrated: dict[str, list[Any]] = {}
    for old_label, plans in old_plans.items():
        ship_id = next(
            (key for key, label in labels_by_id.items() if label == old_label), ""
        )
        target = str(live_by_id.get(ship_id, {}).get("label") or old_label)
        aliases[str(old_label)] = target
        migrated.setdefault(target, []).extend(plans if isinstance(plans, list) else [])
    metadata = {
        str(row["label"]): {
            "id": int(row["id"]), "type": row["type"], "name": row["name"],
            "status": row["status"],
            "is_current": str(row["id"]) == str(fleet_state.get("active_id") or ""),
        }
        for row in fleet_state.get("ships", [])
    }
    _write_json_if_changed(data_dir / "ship_blueprints.json", migrated)
    _write_json_if_changed(data_dir / "ship_metadata.json", metadata)
    return migrated, aliases


def module_matches_type(module_id: object, blueprint_type: object) -> bool:
    module = normalize(canonical_module_id(module_id))
    wanted = normalize(blueprint_type)
    if not module or not wanted:
        return False
    if wanted == "armour":
        # Ship armour symbols are ship-specific (for example
        # federationcorvette_armour_grade5), so there is no common prefix.
        return "armour" in module
    prefixes = ENGINEERING_MODULE_ID_PREFIXES.get(wanted, ())
    if wanted == "pulselaser" and module.startswith("hptpulselaserburst"):
        return False
    return any(module.startswith(prefix) for prefix in prefixes)


def engineering_loadout_rows(
    module_slots: object, catalog_rows: object,
) -> list[dict[str, Any]]:
    """Project installed, engineerable modules into slot-first planner rows."""
    catalog = [row for row in (catalog_rows or []) if isinstance(row, dict)]
    modules: dict[str, dict[str, Any]] = {}
    for row in catalog:
        module = str(row.get("module") or "").strip()
        if not module:
            continue
        target = modules.setdefault(module, {
            "module": module,
            "category": str(row.get("category") or "Other"),
            "blueprintCount": 0,
        })
        target["blueprintCount"] += 1
    rating_letters = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}
    category_rank = {
        category: index for index, category in enumerate(ENGINEERING_CATEGORY_ORDER)
    }
    core_slot_labels = {
        "Armour": "CORE · ARMOUR",
        "PowerPlant": "CORE · POWER PLANT",
        "MainEngines": "CORE · THRUSTERS",
        "FrameShiftDrive": "CORE · FRAME SHIFT DRIVE",
        "LifeSupport": "CORE · LIFE SUPPORT",
        "PowerDistributor": "CORE · POWER DISTRIBUTOR",
        "Radar": "CORE · SENSORS",
        "FuelTank": "CORE · FUEL TANK",
    }
    result = []
    for slot_row in module_slots or []:
        if not isinstance(slot_row, dict):
            continue
        module_id = str(slot_row.get("moduleId") or "")
        match = next(
            (
                value for value in modules.values()
                if module_matches_type(module_id, value["module"])
            ),
            None,
        )
        if not match:
            continue
        size_rating = ""
        symbol = canonical_module_id(module_id)
        identity = re.search(r"_size(\d+)_class(\d+)", symbol)
        if identity:
            size = int(identity.group(1))
            module_class = int(identity.group(2))
            size_rating = f"{size}{rating_letters.get(module_class, '')}"
        slot = str(slot_row.get("slot") or "")
        result.append({
            **match,
            "slot": slot,
            "moduleId": module_id,
            "sizeRating": size_rating,
            "displaySlot": core_slot_labels.get(slot, slot),
            "bindingKey": f"{slot}\u241f{module_id}",
            "engineered": bool(slot_row.get("engineered")),
            "engineeringGrade": int(slot_row.get("engineeringGrade") or 0),
            "engineeringBlueprint": str(
                slot_row.get("engineeringBlueprint") or ""
            ),
            "experimentalEffect": str(
                slot_row.get("experimentalEffect") or ""
            ),
        })
    result.sort(key=lambda row: (
        category_rank.get(row["category"], len(category_rank)),
        row["slot"].casefold(),
        row["module"].casefold(),
    ))
    return result


def ship_slot_layout(
    ship_data: object, module_slots: object, catalog_rows: object,
    desired_modules: object = None,
) -> list[dict[str, Any]]:
    """Build the selected hull's physical slots and overlay known modules."""
    ship = ship_data if isinstance(ship_data, dict) else {}
    installed_rows = {
        str(row.get("slot") or ""): row
        for row in (module_slots or []) if isinstance(row, dict)
        and row.get("slot")
    }
    engineerable = {
        str(row.get("slot") or ""): row
        for row in engineering_loadout_rows(module_slots, catalog_rows)
    }
    desired_by_slot = (
        desired_modules if isinstance(desired_modules, dict) else {}
    )
    rating_letters = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}
    core_specs = (
        ("Armour", "ARMOUR", 0),
        ("PowerPlant", "POWER PLANT", ship.get("core", {}).get("powerPlant")),
        ("MainEngines", "THRUSTERS", ship.get("core", {}).get("thrusters")),
        ("FrameShiftDrive", "FRAME SHIFT DRIVE", ship.get("core", {}).get("frameShiftDrive")),
        ("LifeSupport", "LIFE SUPPORT", ship.get("core", {}).get("lifeSupport")),
        ("PowerDistributor", "POWER DISTRIBUTOR", ship.get("core", {}).get("powerDistributor")),
        ("Radar", "SENSORS", ship.get("core", {}).get("sensors")),
        ("FuelTank", "FUEL TANK", ship.get("core", {}).get("fuelTank")),
    )
    size_names = {1: "SMALL", 2: "MEDIUM", 3: "LARGE", 4: "HUGE"}

    def module_identity(module_id: str) -> tuple[str, str]:
        symbol = canonical_module_id(module_id)
        normalized = normalize(symbol)
        identity = re.search(r"_size(\d+)_class(\d+)", symbol.casefold())
        size_rating = ""
        if identity:
            size_rating = f"{identity.group(1)}{rating_letters.get(int(identity.group(2)), '')}"
        labels = (
            ("intpowerplant", "POWER PLANT"),
            ("intengine", "THRUSTERS"),
            ("inthyperdrive", "FRAME SHIFT DRIVE"),
            ("intlifesupport", "LIFE SUPPORT"),
            ("intpowerdistributor", "POWER DISTRIBUTOR"),
            ("intsensors", "SENSORS"),
            ("intfueltank", "FUEL TANK"),
            ("intcargorack", "CARGO RACK"),
            ("inthullreinforcement", "HULL REINFORCEMENT PACKAGE"),
            ("intmodulereinforcement", "MODULE REINFORCEMENT PACKAGE"),
            ("intshieldgenerator", "SHIELD GENERATOR"),
            ("intfuelscoop", "FUEL SCOOP"),
            ("intfighterbay", "FIGHTER HANGAR"),
            ("intbuggybay", "PLANETARY VEHICLE HANGAR"),
            ("planetaryapproachsuite", "PLANETARY APPROACH SUITE"),
            ("dronecontrol", "LIMPET CONTROLLER"),
            ("multicannon", "MULTI-CANNON"),
            ("pulselaser", "PULSE LASER"),
            ("beamlaser", "BEAM LASER"),
            ("shieldbooster", "SHIELD BOOSTER"),
            ("heatsink", "HEAT SINK LAUNCHER"),
            ("armour", "ARMOUR"),
        )
        name = (
            "BI-WEAVE SHIELD GENERATOR"
            if normalized.startswith("intshieldgenerator")
            and normalized.endswith("fast") else
            next((label for marker, label in labels if marker in normalized), "")
        )
        if not name:
            name = re.sub(r"[_-]+", " ", symbol).upper()
        return name, size_rating

    rows: list[dict[str, Any]] = []

    def append_slot(
        group: str, slot: str, size: object, fallback_name: str = "",
        restriction: str = "",
    ) -> None:
        installed = installed_rows.get(slot, {})
        module_id = str(installed.get("moduleId") or "")
        module_name, size_rating = module_identity(module_id) if module_id else ("", "")
        engineering = engineerable.get(slot, {})
        desired_module_id = str(desired_by_slot.get(slot) or "")
        desired_name, desired_size_rating = (
            module_identity(desired_module_id)
            if desired_module_id else ("", "")
        )
        module_change = bool(
            desired_module_id
            and canonical_module_id(desired_module_id) != canonical_module_id(module_id)
        )
        rows.append({
            "group": group,
            "slot": slot,
            "slotSize": int(size or 0),
            "slotBadge": str(size or ("U" if group == "UTILITY MOUNTS" else "—")),
            "moduleId": module_id,
            "module": str(engineering.get("module") or module_name or fallback_name),
            "sizeRating": str(engineering.get("sizeRating") or size_rating),
            "empty": not bool(module_id),
            "restriction": restriction,
            "engineerable": bool(engineering),
            "engineered": bool(installed.get("engineered")),
            "engineeringGrade": int(installed.get("engineeringGrade") or 0),
            "engineeringBlueprint": str(
                installed.get("engineeringBlueprint") or ""
            ),
            "experimentalEffect": str(
                installed.get("experimentalEffect") or ""
            ),
            "category": str(engineering.get("category") or ""),
            "blueprintCount": int(engineering.get("blueprintCount") or 0),
            "bindingKey": f"{slot}\u241f{module_id}" if module_id else slot,
            "moduleChange": module_change,
            "desiredModuleId": desired_module_id,
            "desiredModule": desired_name,
            "desiredSizeRating": desired_size_rating,
            "planPending": False,
            "planTargetGrade": 0,
            "planBlueprint": "",
            "planExperimental": "",
        })

    for slot, label, size in core_specs:
        append_slot("CORE INTERNALS", slot, size, label)
    for index, spec in enumerate(ship.get("optional", []) or [], 1):
        if not isinstance(spec, dict):
            continue
        size = int(spec.get("size") or 0)
        append_slot(
            "OPTIONAL INTERNALS", f"Slot{index:02d}_Size{size}", size,
            restriction=str(spec.get("restriction") or ""),
        )
    hardpoint_counts: dict[int, int] = {}
    for spec in ship.get("hardpoints", []) or []:
        if not isinstance(spec, dict):
            continue
        size = int(spec.get("size") or 0)
        hardpoint_counts[size] = hardpoint_counts.get(size, 0) + 1
        append_slot(
            "HARDPOINTS",
            f"{size_names.get(size, 'UNKNOWN').title()}Hardpoint{hardpoint_counts[size]}",
            size,
        )
    for index in range(1, int(ship.get("utility") or 0) + 1):
        append_slot("UTILITY MOUNTS", f"TinyHardpoint{index}", 0)
    return rows


MANDATORY_CORE_STOCK_FAMILIES = {
    "PowerPlant": "int_powerplant",
    "MainEngines": "int_engine",
    "FrameShiftDrive": "int_hyperdrive",
    "LifeSupport": "int_lifesupport",
    "PowerDistributor": "int_powerdistributor",
    "Radar": "int_sensors",
}


def module_store_core_replacement(event: dict[str, Any]) -> str:
    """Return Elite's implicit stock replacement for a stored core module."""
    slot = str(event.get("Slot") or "")
    if slot == "Armour":
        ship = str(event.get("Ship") or "").strip("$;")
        return f"{ship}_armour_grade1" if ship else ""
    stock_family = MANDATORY_CORE_STOCK_FAMILIES.get(slot, "")
    if not stock_family:
        return ""
    stored = str(event.get("StoredItem") or "").strip("$;")
    if stored.endswith("_name"):
        stored = stored[:-5]
    _family, size_marker, size_tail = stored.partition("_size")
    size, separator, _variant = size_tail.partition("_")
    if not size_marker or not separator or not size.isdigit():
        return ""
    return f"{stock_family}_size{size}_class1"


def latest_loadout_slots(
    events: list[dict[str, Any]], ship_id: object
) -> list[dict[str, Any]]:
    """Rebuild current physical bindings from snapshots and module changes."""
    wanted_ship = str(ship_id or "")
    slots: dict[str, dict[str, Any]] = {}

    def slot_record(module_id: object, engineering: object = None) -> dict[str, Any]:
        details = engineering if isinstance(engineering, dict) else {}
        level = details.get("Level")
        try:
            grade = max(0, min(5, int(level or 0)))
        except (TypeError, ValueError):
            grade = 0
        return {
            "moduleId": canonical_module_id(module_id),
            "engineered": bool(details and grade > 0),
            "engineeringGrade": grade,
            "engineeringBlueprint": str(
                details.get("BlueprintName_Localised")
                or details.get("BlueprintName") or ""
            ),
            "experimentalEffect": str(
                details.get("ExperimentalEffect_Localised")
                or details.get("ExperimentalEffect") or ""
            ),
        }
    ordered = sorted(
        (
            (sequence, event) for sequence, event in enumerate(events or [])
            if isinstance(event, dict)
        ),
        key=lambda row: (str(row[1].get("timestamp") or ""), row[0]),
    )
    current_ship_id = ""
    for _sequence, event in ordered:
        event_name = str(event.get("event") or "")
        if event.get("ShipID") not in (None, "") and event_name in {
            "LoadGame", "Loadout", "ShipyardSwap", "SetUserShipName",
            "EngineerCraft",
        }:
            current_ship_id = str(event.get("ShipID"))
        elif (
            event_name == "ShipyardBuy"
            and event.get("NewShipID") not in (None, "")
        ):
            current_ship_id = str(event.get("NewShipID"))
        resolved_ship_id = str(event.get("ShipID") or "")
        if event_name == "EngineerCraft" and not resolved_ship_id:
            resolved_ship_id = current_ship_id
        if resolved_ship_id != wanted_ship:
            continue
        if event_name == "Loadout":
            slots = {
                str(module.get("Slot")): slot_record(
                    module.get("Item"), module.get("Engineering")
                )
                for module in (event.get("Modules") or [])
                if isinstance(module, dict)
                and module.get("Slot") and module.get("Item")
            }
        elif event_name in {"ModuleBuy", "ModuleRetrieve"}:
            item_key = "BuyItem" if event_name == "ModuleBuy" else "RetrievedItem"
            slot = str(event.get("Slot") or "")
            item = str(event.get(item_key) or "")
            if slot and item and normalize(item) != "null":
                slots[slot] = slot_record(item)
        elif event_name in {"ModuleSell", "ModuleStore"}:
            slot = str(event.get("Slot") or "")
            if slot:
                replacement = (
                    module_store_core_replacement(event)
                    if event_name == "ModuleStore" else ""
                )
                if replacement:
                    slots[slot] = slot_record(replacement)
                else:
                    slots.pop(slot, None)
        elif event_name == "ModuleSwap":
            from_slot = str(event.get("FromSlot") or "")
            to_slot = str(event.get("ToSlot") or "")
            from_item = str(event.get("FromItem") or "")
            to_item = str(event.get("ToItem") or "")
            previous_from = slots.get(from_slot, slot_record(from_item))
            previous_to = slots.get(to_slot, slot_record(to_item))
            if to_slot:
                if from_item and normalize(from_item) != "null":
                    slots[to_slot] = previous_from
                else:
                    slots.pop(to_slot, None)
            if from_slot:
                if to_item and normalize(to_item) != "null":
                    slots[from_slot] = previous_to
                else:
                    slots.pop(from_slot, None)
        elif event_name == "EngineerCraft":
            slot = str(event.get("Slot") or "")
            module_id = str(event.get("Module") or "")
            installed = slots.get(slot, {})
            if (
                slot and module_id and installed
                and normalize(installed.get("moduleId")) == normalize(module_id)
            ):
                engineering = dict(event)
                previous_effect = str(installed.get("experimentalEffect") or "")
                applied_effect = str(
                    event.get("ExperimentalEffect")
                    or event.get("ApplyExperimentalEffect") or previous_effect
                )
                if applied_effect:
                    engineering["ExperimentalEffect"] = applied_effect
                slots[slot] = slot_record(module_id, engineering)
    return [
        {"slot": slot, **record}
        for slot, record in slots.items()
    ]


def migrate_wishlist_bindings(
    data_dir: Path, fleet_state: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    """Bind legacy plans uniquely or preserve them with a visible warning."""
    path = data_dir / "ship_blueprints.json"
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return
    ids_by_label = {
        str(row["label"]): str(row["id"])
        for row in fleet_state.get("ships", [])
    }
    changed = False
    for label, tasks in payload.items():
        ship_id = ids_by_label.get(str(label), "")
        slots = latest_loadout_slots(events, ship_id)
        for task in tasks if isinstance(tasks, list) else []:
            if not isinstance(task, list) or not task:
                continue
            first = task[0] if isinstance(task[0], dict) else {}
            planner = first.get("_Planner", {})
            if not planner:
                continue
            if not planner.get("plan_mode"):
                planner["plan_mode"] = planner_mode(planner)
                changed = True
            if (
                planner.get("ship_id") and planner.get("slot")
                and planner.get("module_id")
            ):
                continue
            # A module identity is explicit/manual evidence even if an older
            # record is otherwise incomplete. Never replace it automatically.
            if planner.get("module_id"):
                continue
            candidates = [
                row for row in slots
                if module_matches_type(row["moduleId"], first.get("Type"))
            ]
            exact_slot = [
                row for row in candidates
                if planner.get("slot")
                and str(row["slot"]) == str(planner.get("slot"))
            ]
            if len(exact_slot) == 1:
                candidates = exact_slot
            planner["ship_id"] = ship_id
            if len(candidates) == 1:
                planner.update({
                    "slot": candidates[0]["slot"],
                    "module_id": candidates[0]["moduleId"],
                    "binding_required": False,
                })
            else:
                planner["binding_required"] = True
            changed = True
    if changed:
        _write_json_if_changed(path, payload)


def remaining_grade_rolls(
    planner: dict[str, Any], grade: dict[str, Any]
) -> int:
    """Return rolls still needed without treating an estimate as completion."""
    level = int(grade.get("Grade", 0) or 0)
    if level <= 0:
        return 0
    progress = planner.get("grade_progress", {}) or {}
    completed = planner.get("crafts_completed", {}) or {}
    quality = float(progress.get(str(level), 0) or 0)
    if quality >= 0.999:
        return 0
    if any(
        int(other_level) > level
        and (
            float(progress.get(str(other_level), 0) or 0) > 0
            or int(completed.get(str(other_level), 0) or 0) > 0
        )
        for other_level in {
            *(str(value) for value in progress),
            *(str(value) for value in completed),
        }
        if str(other_level).isdigit()
    ):
        return 0
    planned = max(1, int(grade.get("_Rolls", 1) or 1))
    done = max(0, int(completed.get(str(level), 0) or 0))
    estimated_remaining = max(0, planned - done)
    target = int(planner.get("target_grade", 0) or 0)
    if level == target:
        return max(1, estimated_remaining)
    return estimated_remaining


def current_ship(
    data_dir: Path, fleet_state: dict[str, Any], preferred: str = "",
    events: list[dict[str, Any]] | None = None,
) -> tuple[str, list[Any], list[str]]:
    """Select a wishlist exclusively from the rebuilt, existing fleet."""
    blueprints, aliases = reconcile_fleet_cache(data_dir, fleet_state)
    migrate_wishlist_bindings(data_dir, fleet_state, events or [])
    blueprints = read_json(data_dir / "ship_blueprints.json", {})
    rows = fleet_state.get("ships", [])
    ships = [str(row["label"]) for row in rows]
    active = next(
        (str(row["label"]) for row in rows
         if str(row["id"]) == str(fleet_state.get("active_id") or "")), ""
    )
    current = aliases.get(str(preferred or ""), str(preferred or ""))
    if current not in ships:
        current = active or (ships[0] if ships else "")
    return current, blueprints.get(current, []), ships


def required_materials(
    tasks: object,
    metadata: dict[str, dict[str, Any]] | None = None,
    consistency_issues: list[str] | None = None,
) -> dict[str, int]:
    display_keys: dict[str, list[str]] = defaultdict(list)
    if metadata is not None:
        for candidate, info in metadata.items():
            display_key = material_key(info.get("Name"))
            if display_key:
                display_keys[display_key].append(candidate)
    required = defaultdict(int)
    for task in tasks or []:
        if not isinstance(task, list):
            continue
        first = next((item for item in task if isinstance(item, dict)), {})
        planner = first.get("_Planner", {})
        if (
            first.get("Kind") == "ExperimentalEffect"
            and first.get("_Completed")
        ):
            continue
        for grade in task:
            if not isinstance(grade, dict):
                continue
            if grade.get("Kind") == "ExperimentalEffect":
                rolls = max(1, int(grade.get("_Rolls", 1) or 1))
            else:
                rolls = remaining_grade_rolls(planner, grade)
            if rolls <= 0:
                continue
            for ingredient in grade.get("Ingredients", []) or []:
                key = normalize(ingredient.get("Name") or ingredient.get("Name_Localised"))
                if metadata is not None and key not in metadata:
                    # Persisted plans from pre-20.7 stored translated display
                    # labels as IDs. Use such a label only as a unique migration
                    # fallback for blueprint data; Journal identity remains
                    # strictly Material/Name based.
                    display_key = normalize(
                        ingredient.get("Name_Localised") or ingredient.get("Name")
                    )
                    matches = display_keys.get(display_key, [])
                    if len(matches) == 1:
                        key = matches[0]
                if key:
                    required[key] += max(0, int(ingredient.get("Size", 1) or 1)) * rolls
                    if metadata is not None and key not in metadata:
                        message = (
                            f"Unresolved blueprint ingredient {key} in "
                            f"{grade.get('Type') or first.get('Type') or 'unknown module'} / "
                            f"{grade.get('Name') or first.get('Name') or 'unknown blueprint'}."
                        )
                        LOGGER.warning(message)
                        if consistency_issues is not None:
                            consistency_issues.append(message)
    return dict(required)


def reserve_material_pool(
    requirements: list[dict[str, int]],
    inventory: dict[str, int],
    priorities: list[bool] | None = None,
) -> list[dict[str, int]]:
    """Fair-share one inventory, with the single tracked plan served first."""
    available = {
        key: max(0, int(amount or 0))
        for key, amount in (inventory or {}).items()
    }
    allocations = [
        {key: 0 for key in requirement}
        for requirement in requirements
    ]
    priority_flags = list(priorities or [])
    priority_flags.extend([False] * (len(requirements) - len(priority_flags)))
    for key, stock in available.items():
        if stock <= 0:
            continue
        priority_indices = [
            index for index, requirement in enumerate(requirements)
            if priority_flags[index] and int(requirement.get(key, 0) or 0) > 0
        ]
        normal_indices = [
            index for index, requirement in enumerate(requirements)
            if not priority_flags[index] and int(requirement.get(key, 0) or 0) > 0
        ]
        for indices in (priority_indices, normal_indices):
            while stock > 0:
                open_indices = [
                    index for index in indices
                    if allocations[index].get(key, 0)
                    < max(0, int(requirements[index].get(key, 0) or 0))
                ]
                if not open_indices:
                    break
                for index in open_indices:
                    if stock <= 0:
                        break
                    allocations[index][key] = allocations[index].get(key, 0) + 1
                    stock -= 1
            if stock <= 0:
                break
    return allocations


def material_status_label(missing_kinds: int, covered: int) -> str:
    """Return the one material vocabulary used by every plan surface."""
    return (
        MATERIAL_STATUS[0] if int(missing_kinds or 0) == 0 else
        MATERIAL_STATUS[1] if int(covered or 0) > 0 else MATERIAL_STATUS[2]
    )


def material_completion(covered: int, total: int, reliable: bool = True) -> float:
    """Treat an empty, reliable requirement set as fully satisfied."""
    if not reliable:
        return 0.0
    return float(covered) / float(total) if int(total or 0) > 0 else 1.0


def progress_status_label(target_code: str) -> str:
    """Return aggregate craft progress without material terminology."""
    return (
        PROGRESS_STATUS[2] if target_code == "completed" else
        PROGRESS_STATUS[0] if target_code == "not_started" else
        PROGRESS_STATUS[1]
    )


def blueprint_rows(
    tasks: object,
    inventory: dict[str, int],
    metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    experimental_engineers = {}
    experimental_requirements: dict[str, dict[str, int]] = {}
    plan_requirements: dict[int, tuple[dict[str, int], list[str]]] = {}
    allocation_order: list[tuple[int, str, dict[str, int]]] = []
    material_plan_counts: dict[str, int] = defaultdict(int)
    priority_plan_id = next((
        str(task[0].get("_Planner", {}).get("plan_id") or "")
        for task in tasks or []
        if isinstance(task, list) and task and isinstance(task[0], dict)
        and not (
            task[0].get("Kind") == "ExperimentalEffect"
            and task[0].get("_ParentPlanId")
        )
        and task[0].get("_Planner", {}).get("priority")
        and wishlist_target_status(task[0].get("_Planner", {}))["code"] != "completed"
    ), "")
    for task in tasks or []:
        if not isinstance(task, list) or not task:
            continue
        first = next((item for item in task if isinstance(item, dict)), {})
        parent = str(first.get("_ParentPlanId") or "")
        if first.get("Kind") != "ExperimentalEffect" or not parent:
            continue
        experimental_engineers[parent] = {
            str(engineer)
            for item in task if isinstance(item, dict)
            for engineer in (item.get("Engineers", []) or [])
            if engineer and not str(engineer).startswith("@")
        }
        experimental_requirements[parent] = required_materials(
            [task], metadata
        )
    for task_index, task in enumerate(tasks or []):
        if not isinstance(task, list) or not task:
            continue
        first = next((item for item in task if isinstance(item, dict)), {})
        if first.get("Kind") == "ExperimentalEffect" and first.get("_ParentPlanId"):
            continue
        unresolved: list[str] = []
        requirement = required_materials([task], metadata, unresolved)
        plan_requirements[task_index] = (requirement, unresolved)
        planner = first.get("_Planner", {})
        mode = planner_mode(planner)
        plan_id = str(planner.get("plan_id") or "")
        if mode == "experimental_only":
            # A standalone Experimental carries its recipe in its only task.
            # Treating that recipe as a Grade allocation makes the Wishlist
            # look READY while Operations sees no executable Experimental.
            experimental_requirements[plan_id] = requirement
            allocation_order.append((task_index, "experimental", requirement))
        else:
            allocation_order.append((task_index, "grade", requirement))
        experimental_requirement = experimental_requirements.get(plan_id, {})
        if (
            mode != "experimental_only"
            and
            experimental_requirement
            and planner.get("experimental_name")
            and not planner.get("experimental_complete")
        ):
            allocation_order.append(
                (task_index, "experimental", experimental_requirement)
            )
        for key, amount in requirement.items():
            if int(amount or 0) > 0:
                material_plan_counts[key] += 1
    reserved_allocations = reserve_material_pool(
        [item[2] for item in allocation_order], inventory,
        [
            str((tasks[task_index][0].get("_Planner", {}) or {}).get("plan_id") or "")
            == priority_plan_id
            for task_index, _, _ in allocation_order
        ],
    )
    allocations = {
        (task_index, kind): reserved_allocations[index]
        for index, (task_index, kind, _requirement) in enumerate(allocation_order)
    }
    for task_index, task in enumerate(tasks or []):
        if not isinstance(task, list) or not task:
            continue
        requirement, unresolved = plan_requirements.get(task_index, ({}, []))
        first = next((item for item in task if isinstance(item, dict)), {})
        planner = first.get("_Planner", {}) if isinstance(first, dict) else {}
        mode = planner_mode(planner)
        allocation = allocations.get(
            (task_index, "experimental" if mode == "experimental_only" else "grade"),
            {},
        )
        total = sum(requirement.values())
        covered = sum(
            allocation.get(key, 0)
            for key in requirement
            if metadata is None or key in metadata
        )
        is_experimental = first.get("Kind") == "ExperimentalEffect"
        if is_experimental and first.get("_ParentPlanId"):
            continue
        plan_id = str(planner.get("plan_id") or first.get("_ParentPlanId") or "")
        unfinished_grades = [
            item for item in task
            if isinstance(item, dict)
            and item.get("Grade") is not None
            and remaining_grade_rolls(planner, item) > 0
        ]
        target_record = max(
            unfinished_grades,
            key=lambda item: int(item.get("Grade", 0) or 0),
            default=None,
        )
        engineer_set = set(real_engineers(target_record or {}))
        if mode == "experimental_only":
            engineer_set = {
                engineer for item in task if isinstance(item, dict)
                for engineer in real_engineers(item)
            }
        effect_engineers = experimental_engineers.get(plan_id)
        experimental_pending = bool(
            planner.get("experimental_name")
            and not planner.get("experimental_complete")
        )
        next_craft_ingredients = {
            normalize(ingredient.get("Name") or ingredient.get("Name_Localised")):
            max(0, int(ingredient.get("Size", 1) or 1))
            for ingredient in ((target_record or {}).get("Ingredients", []) or [])
            if normalize(ingredient.get("Name") or ingredient.get("Name_Localised"))
        }
        can_craft_next = bool(target_record) and all(
            int(allocation.get(key, 0) or 0) >= amount
            for key, amount in next_craft_ingredients.items()
        )
        experimental_requirement = (
            experimental_requirements.get(plan_id, {}) if experimental_pending else {}
        )
        experimental_allocation = allocations.get(
            (task_index, "experimental"), {}
        )
        experimental_material_progress = []
        for key, amount in experimental_requirement.items():
            need = max(0, int(amount or 0))
            have = max(0, int(experimental_allocation.get(key, 0) or 0))
            details = (metadata or {}).get(key, {})
            experimental_material_progress.append({
                "key": key,
                "name": str(details.get("Name") or key),
                "have": have,
                "need": need,
                "missing": max(0, need - have),
            })
        if target_record is None and experimental_pending and effect_engineers:
            engineer_set = set(effect_engineers)
        elif experimental_pending and effect_engineers:
            engineer_set &= effect_engineers
        engineers = sorted(engineer_set)
        selected_engineer = str(
            (first.get("_SelectedEngineer") or {}).get("name") or ""
        )
        grades = [
            int(item.get("Grade"))
            for item in task
            if isinstance(item, dict) and item.get("Grade") is not None
        ]
        target_status = wishlist_target_status(planner)
        is_priority = bool(
            priority_plan_id and str(planner.get("plan_id") or "") == priority_plan_id
        )
        material_progress = []
        for key, amount in requirement.items():
            need = max(0, int(amount or 0))
            have = max(0, int(allocation.get(key, 0) or 0))
            missing = max(0, need - have)
            status = "ready" if missing == 0 else "empty" if have == 0 else "partial"
            details = (metadata or {}).get(key, {})
            material_progress.append({
                "key": key,
                "name": str(details.get("Name") or key),
                "category": str(details.get("Category") or "Unknown"),
                "have": have,
                "need": need,
                "missing": missing,
                "progress": min(1.0, have / need) if need else 1.0,
                "status": status,
                "sharedPlanCount": int(material_plan_counts.get(key, 0)),
            })
        material_progress.sort(key=lambda item: (
            {"empty": 0, "partial": 1, "ready": 2}[item["status"]],
            item["category"].casefold(), item["name"].casefold(),
        ))
        complete_material_kinds = sum(
            1 for item in material_progress if item["status"] == "ready"
        )
        missing_kinds = sum(
            1 for key, amount in requirement.items()
            if allocation.get(key, 0) < amount
        )
        material_status = material_status_label(missing_kinds, covered)
        progress_status = progress_status_label(target_status["code"])
        rows.append({
            "index": task_index,
            "planId": plan_id,
            "priority": is_priority,
            "deferred": bool(priority_plan_id and not is_priority),
            "instance": str(planner.get("instance") or ""),
            "editable": bool(planner) and (
                mode == "experimental_only"
                or bool(first.get("Type") and first.get("Name"))
            ),
            "planMode": mode,
            "experimental": str(planner.get("experimental_name") or ""),
            "experimentalComplete": bool(planner.get("experimental_complete")),
            "bindingRequired": bool(planner.get("binding_required")),
            "boundSlot": str(planner.get("slot") or ""),
            "boundModule": str(planner.get("module_id") or ""),
            "moduleType": str(first.get("Type") or planner.get("module_type") or ""),
            "blueprintNames": sorted({
                str(value) for value in (planner.get("blueprint_names", {}) or {}).values()
                if value
            } | {
                str(item.get("BlueprintName")) for item in task
                if isinstance(item, dict) and item.get("BlueprintName")
            }),
            "blueprintIds": sorted({
                str(value) for value in (planner.get("blueprint_ids", {}) or {}).values()
                if value not in (None, "")
            } | {
                str(item.get("BlueprintID")) for item in task
                if isinstance(item, dict) and item.get("BlueprintID") not in (None, "")
            }),
            "experimentalId": str(planner.get("experimental_id") or ""),
            "targetStatus": target_status["code"],
            "targetStatusText": target_status["text"],
            "materialStatus": material_status,
            "progressStatus": progress_status,
            "gradeReached": target_status["gradeReached"],
            "targetGrade": target_status["targetGrade"],
            "gradeStatus": target_status["gradeStatus"],
            "gradeStatusLabel": target_status["gradeStatusLabel"],
            "experimentalStatus": target_status["experimentalStatus"],
            "experimentalStatusLabel": target_status["experimentalStatusLabel"],
            "canCraftNext": can_craft_next,
            "experimentalReady": bool(experimental_pending and experimental_requirement) and all(
                row["missing"] == 0 for row in experimental_material_progress
            ),
            "experimentalMaterialProgress": experimental_material_progress,
            "craftsDone": sum(
                int(value or 0)
                for value in (planner.get("crafts_completed", {}) or {}).values()
            ),
            "craftsPlanned": int(planner.get("estimated_total_rolls", 0) or 0),
            "craftReason": str(planner.get("last_change_reason") or ""),
            "module": str(
                first.get("Type_Localised") or first.get("Type")
                or planner.get("module_type") or "Module"
            ),
            "blueprint": str(
                first.get("Name_Localised") or first.get("Name")
                or "Experimental Effect"
            ),
            "engineer": ", ".join(engineers) or "Engineer not listed",
            "eligibleEngineers": engineers,
            "selectedEngineer": (
                selected_engineer if selected_engineer in engineer_set else ""
            ),
            "grade": (
                int(target_record.get("Grade", 0) or 0)
                if target_record else max(grades, default=0)
            ),
            "required": total,
            "covered": covered,
            "completion": covered / total if total else 1.0,
            "completionPercent": int(round(covered / total * 100)) if total else 100,
            "completeMaterialKinds": complete_material_kinds,
            "totalMaterialKinds": len(material_progress),
            "materialProgress": material_progress,
            "calculationWarning": (
                "Materialbedarf unvollständig berechenbar – unbekanntes Material: "
                + ", ".join(sorted({
                    message.split(" ingredient ", 1)[1].split(" in ", 1)[0]
                    for message in unresolved
                }))
                if unresolved else ""
            ),
            "completionReliable": not unresolved,
            "missingKinds": missing_kinds,
        })
    return rows


def aggregate_plan_progress(rows: object) -> str:
    """Summarize craft progress without mixing it with material coverage."""
    plans = [row for row in (rows or []) if isinstance(row, dict)]
    if not plans:
        return PROGRESS_STATUS[0]
    statuses = [str(row.get("progressStatus") or PROGRESS_STATUS[0]) for row in plans]
    if all(status == PROGRESS_STATUS[2] for status in statuses):
        return PROGRESS_STATUS[2]
    if any(status != PROGRESS_STATUS[0] for status in statuses):
        return PROGRESS_STATUS[1]
    return PROGRESS_STATUS[0]


def craft_tracking_issues_for_ship(rows: object, ship_id: object) -> list[dict]:
    """Expose unmatched evidence only to its physical ship within the profile."""
    wanted = str(ship_id or "")
    return [
        row for row in (rows or [])
        if isinstance(row, dict) and str(row.get("shipId") or "") == wanted
    ]


def craft_issue_matches_plan(issue: dict, plan: dict) -> bool:
    """Conservatively reject an NBA blocker when exact identities conflict."""
    if bool(issue.get("historical")):
        return False
    issue_slot = str(issue.get("slot") or "").casefold()
    plan_slot = str(plan.get("boundSlot") or "").casefold()
    if issue_slot and plan_slot and issue_slot != plan_slot:
        return False
    issue_module = str(issue.get("module") or "")
    plan_module = str(plan.get("boundModule") or "")
    plan_type = str(plan.get("moduleType") or plan.get("module") or "")
    if issue_module and (plan_module or plan_type) and not (
        normalize(issue_module) == normalize(plan_module)
        or module_matches_type(issue_module, plan_type)
        or module_matches_type(plan_module, issue_module)
    ):
        return False
    issue_experimental = str(issue.get("experimentalId") or "")
    plan_experimental = str(plan.get("experimentalId") or "")
    if issue_experimental:
        return bool(
            plan_experimental
            and normalize(issue_experimental) == normalize(plan_experimental)
            and str(plan.get("experimentalStatus") or "") != "completed"
        )
    if str(plan.get("planMode") or "") == "experimental_only":
        return False
    issue_blueprint_id = str(issue.get("blueprintId") or "")
    plan_blueprint_ids = {str(value) for value in (plan.get("blueprintIds") or [])}
    if issue_blueprint_id and plan_blueprint_ids and issue_blueprint_id not in plan_blueprint_ids:
        return False
    issue_blueprint = str(issue.get("blueprintName") or "")
    plan_blueprints = {str(value) for value in (plan.get("blueprintNames") or [])}
    if issue_blueprint and plan_blueprints and issue_blueprint not in plan_blueprints:
        return False
    return True


def classify_craft_tracking_issues(rows: object, plans: object) -> list[dict]:
    """Separate loud plan conflicts from quiet fresh and historical evidence."""
    open_plans = [
        row for row in (plans or [])
        if isinstance(row, dict)
        and str(row.get("targetStatus") or "") != "completed"
    ]
    classified_rows = []
    for issue in rows or []:
        if not isinstance(issue, dict):
            continue
        classified = dict(issue)
        relevant = bool(
            not classified.get("historical")
            and any(
                craft_issue_matches_plan(classified, plan)
                for plan in open_plans
            )
        )
        classified["relevant"] = relevant
        classified["relevanceLabel"] = (
            "HISTORICAL" if classified.get("historical") else
            "RELEVANT" if relevant else
            "NO PLAN" if not open_plans else "UNRELATED"
        )
        classified["displayReasonCode"] = (
            "HISTORICAL" if classified.get("historical") else
            "NO PLAN" if not open_plans else
            "UNRELATED" if not relevant else
            str(classified.get("reasonCode") or "UNMATCHED")
        )
        classified_rows.append(classified)
    return classified_rows


def _minimum_engineer_cover(candidate_sets, engineer_index):
    """Return a deterministic minimum Engineer set covering every plan."""
    requirements = [set(values) for values in candidate_sets if values]
    if not requirements:
        return set()
    best = None

    def engineer_order(name):
        row = engineer_index.get(name, {})
        distance = float(row.get("distance", -1) or -1)
        return (
            distance < 0,
            distance if distance >= 0 else 0,
            str(name).casefold(),
        )

    def search(chosen, remaining):
        nonlocal best
        if not remaining:
            candidate = set(chosen)
            if best is None or len(candidate) < len(best) or (
                len(candidate) == len(best)
                and tuple(sorted(engineer_order(name) for name in candidate))
                < tuple(sorted(engineer_order(name) for name in best))
            ):
                best = candidate
            return
        if best is not None and len(chosen) >= len(best):
            return
        requirement = min(remaining, key=lambda values: (len(values), sorted(values)))
        coverage = {
            name: sum(name in values for values in remaining)
            for name in requirement
        }
        for name in sorted(
            requirement,
            key=lambda value: (-coverage[value], engineer_order(value)),
        ):
            search(
                chosen | {name},
                [values for values in remaining if name not in values],
            )

    search(set(), requirements)
    return best or set()


def assign_plans_to_nearest_engineers(plans, engineer_rows):
    """Globally minimize Engineer visits, then prefer access and distance."""
    engineer_index = {
        row["name"]: dict(row)
        for row in engineer_rows or []
        if row.get("name")
    }
    prepared = []
    for plan in plans or []:
        if str(plan.get("targetStatus") or "") == "completed":
            continue
        eligible = [
            name for name in (plan.get("eligibleEngineers") or [])
            if name in engineer_index
        ]
        if not eligible:
            continue
        target_grade = int(plan.get("grade", 0) or 0)
        usable = [
            name for name in eligible
            if engineer_index[name].get("statusGroup") == "unlocked"
            and int(engineer_index[name].get("rank", 0) or 0) >= target_grade
        ]
        selected = str(plan.get("selectedEngineer") or "")
        candidates = usable or eligible
        if usable:
            # An Engineer in the commander's current system is a mandatory
            # consolidation stop.  Complete every compatible job here before
            # optimizing the remaining route, otherwise an equal or smaller
            # global cover (or a stale prior selection) can send the commander
            # away and back again.
            current = [
                name for name in usable
                if 0 <= float(engineer_index[name].get("distance", -1)) < 0.05
            ]
            if current:
                candidates = current
            elif selected in candidates:
                candidates = [selected]
        elif selected in candidates:
            candidates = [selected]
        prepared.append((plan, target_grade, candidates, bool(usable)))

    cover = _minimum_engineer_cover(
        [candidates for _plan, _grade, candidates, _usable in prepared],
        engineer_index,
    )
    assignments = {}
    for plan, target_grade, candidates, craftable in prepared:
        covered = [name for name in candidates if name in cover]
        selection = covered or candidates
        selection.sort(key=lambda name: (
            float(engineer_index[name].get("distance", -1) or -1) < 0,
            float(engineer_index[name].get("distance", 0) or 0)
            if float(engineer_index[name].get("distance", -1) or -1) >= 0 else 0,
            str(name).casefold(),
        ))
        chosen = engineer_index[selection[0]]
        rank = int(chosen.get("rank", 0) or 0)
        block_reason = "" if craftable else (
            f"Engineer access/rank insufficient: requires unlocked G{target_grade}, "
            f"Journal reports {chosen.get('status', 'UNKNOWN')} G{rank}"
        )
        bucket = assignments.setdefault(chosen["name"], {
            **chosen,
            "openJobs": 0,
            "readyJobs": 0,
            "jobNames": [],
            "craftable": True,
            "blockReasons": [],
        })
        bucket["openJobs"] += 1
        actionable = not bool(plan.get("deferred"))
        if actionable:
            bucket["craftable"] = bucket["craftable"] and craftable
        if block_reason and actionable:
            bucket["blockReasons"].append(block_reason)
        if actionable and craftable and float(plan.get("completion", 0) or 0) >= 1:
            bucket["readyJobs"] += 1
        bucket["jobNames"].append(
            f"{plan.get('module', 'Module')} · "
            f"{plan.get('blueprint', 'Blueprint')} · G{target_grade}"
        )
    return list(assignments.values())


def engineer_options_for_plan(plan, engineer_rows, blueprint_records=None):
    """List every Engineer capable of this blueprint at the target Grade."""
    plan = plan or {}
    target = int(plan.get("targetGrade", 0) or plan.get("grade", 0) or 0)
    module_key = normalize(plan.get("module"))
    blueprint_key = normalize(plan.get("blueprint"))
    capabilities: dict[str, int] = {}
    for record in blueprint_records or []:
        if not isinstance(record, dict):
            continue
        record_module = normalize(record.get("Type_Localised") or record.get("Type"))
        record_blueprint = normalize(record.get("Name_Localised") or record.get("Name"))
        if record_module != module_key or record_blueprint != blueprint_key:
            continue
        grade = int(record.get("Grade", 0) or 0)
        for name in real_engineers(record):
            capabilities[name] = max(capabilities.get(name, 0), grade)
    if not capabilities:
        capabilities = {
            str(name): target for name in (plan.get("eligibleEngineers") or [])
            if name
        }
    engineer_index = {
        str(row.get("name") or ""): row
        for row in engineer_rows or [] if row.get("name")
    }
    options = []
    for name, maximum in capabilities.items():
        if maximum < target:
            continue
        row = engineer_index.get(name, {})
        rank = int(row.get("rank", 0) or 0)
        unlocked = str(row.get("statusGroup") or "") == "unlocked"
        if not unlocked:
            code, text = "unlock_required", "UNLOCK REQUIRED"
        elif rank < target:
            code, text = "rank_too_low", "RANK TOO LOW"
        else:
            code, text = "craftable", "CRAFTABLE NOW"
        options.append({
            "name": name,
            "system": str(row.get("system") or "System not stored"),
            "station": str(row.get("station") or ""),
            "maxGrade": maximum,
            "rank": rank,
            "status": code,
            "statusText": text,
            "craftable": code == "craftable",
            "distance": float(row.get("distance", -1) or -1),
            "portraitUrl": str(row.get("portraitUrl") or ""),
        })
    order = {"craftable": 0, "rank_too_low": 1, "unlock_required": 2}
    return sorted(options, key=lambda row: (
        order.get(row["status"], 9),
        -int(row["rank"]),
        row["distance"] < 0,
        row["distance"] if row["distance"] >= 0 else 0,
        row["name"].casefold(),
    ))


def select_operation_action(
    state, engineer_route, engineer_rows=None, blueprint_records=None
):
    """Select one truthful, executable Commander action from current state."""
    state = state or {}
    plans = list(state.get("blueprints") or [])
    tracking_issues = list(state.get("craftTrackingIssues") or [])
    open_plans = [
        row for row in plans
        if str(row.get("targetStatus") or "") != "completed"
    ]
    relevant_issue = next((
        issue for issue in tracking_issues
        if any(craft_issue_matches_plan(issue, plan) for plan in open_plans)
    ), None)
    if relevant_issue:
        issue = relevant_issue
        return {
            "kind": "CRAFT_MATCH_BLOCKER",
            "title": "Resolve an unmatched Journal craft",
            "detail": str(issue.get("reason") or "Craft matching is ambiguous."),
            "reason": (
                "The Journal craft is retained and will be retried; no plan was "
                "guessed or silently marked complete."
            ),
            "after": "Bind the correct ship slot or select Track next, then refresh.",
            "system": "", "station": "", "buttonLabel": "OPEN WISHLIST",
            "targetPage": 1, "executable": True,
        }
    binding_blockers = [
        row for row in plans
        if row.get("bindingRequired")
        and str(row.get("targetStatus") or "") != "completed"
    ]
    if binding_blockers:
        plan = binding_blockers[0]
        module = str(plan.get("module") or "imported module")
        return {
            "kind": "BINDING_BLOCKER",
            "title": f"Bind {module} to a ship slot",
            "detail": "Open the Wishlist and select the matching physical module slot.",
            "reason": (
                "This plan cannot safely match Journal crafts until its module "
                "instance is unambiguous. No slot will be guessed."
            ),
            "after": "Then return here; the best executable trade or craft will appear automatically.",
            "system": "",
            "station": "",
            "buttonLabel": "OPEN WISHLIST",
            "targetPage": 1,
            "executable": True,
        }
    calculation_warning = str(state.get("calculationWarning") or "")
    if calculation_warning:
        return {
            "kind": "CALCULATION_BLOCKER",
            "title": "Resolve incomplete material data",
            "detail": calculation_warning,
            "reason": "A reliable trade or craft cannot be recommended from an incomplete recipe.",
            "after": "Once the Journal confirms the recipe, the next executable action will replace this blocker.",
            "system": "",
            "station": "",
            "buttonLabel": "OPEN WISHLIST",
            "targetPage": 1,
            "executable": True,
        }
    trades = [
        row for row in (state.get("trades") or [])
        if not row.get("confirmed")
        and str(row.get("system") or "")
        and str(row.get("station") or "")
    ]
    missing = [
        row for row in (state.get("materials") or [])
        if int(row.get("missing", 0) or 0) > 0
    ]
    tech_track = dict(state.get("techBrokerTrack") or {})
    if tech_track:
        track_missing_by_key = {
            str(row.get("key") or ""): int(row.get("missing", 0) or 0)
            for row in (tech_track.get("materials") or [])
            if row.get("key") and int(row.get("missing", 0) or 0) > 0
        }
        track_trades = [
            row for row in trades
            if str(row.get("targetKey") or "") in track_missing_by_key
        ]
        if track_trades:
            trade = track_trades[0]
            system = str(trade.get("system") or "")
            station = str(trade.get("station") or "")
            return {
                "kind": "TECH_BROKER_TRADE",
                "title": str(trade.get("instruction") or "Complete material trade"),
                "detail": (
                    f"ACTIVE TECH BROKER TRACK · {tech_track.get('name', 'unlock')}"
                    + (f" · {station} · {system}" if station and system else "")
                ),
                "reason": "The tracked Tech Broker recipe has material priority.",
                "after": "Continue the tracked recipe until it is READY, then travel to its broker.",
                "system": system, "station": station,
                "buttonLabel": "COPY TRADER SYSTEM" if system else "OPEN MATERIALS",
                "targetPage": 2 if not system else -1,
                "executable": bool(system and station),
            }
        if track_missing_by_key:
            material_by_key = {
                str(row.get("key") or ""): row
                for row in (state.get("materials") or [])
            }
            key, amount = next(iter(track_missing_by_key.items()))
            material = material_by_key.get(key, {})
            name = str(material.get("name") or key or "material")
            return {
                "kind": "TECH_BROKER_COLLECT",
                "title": f"Collect {amount} × {name}",
                "detail": f"ACTIVE TECH BROKER TRACK · {tech_track.get('name', 'unlock')}",
                "reason": "This missing material belongs to the active Tech Broker priority.",
                "after": "When the recipe is READY, Operations will switch to the broker destination.",
                "system": "", "station": "",
                "buttonLabel": "OPEN FARM MISSING",
                "targetPage": 2, "farmMissing": True, "executable": True,
            }
        destination_system = str(tech_track.get("destinationSystem") or "")
        destination_station = str(tech_track.get("destinationStation") or "")
        return {
            "kind": "TECH_BROKER_TRAVEL",
            "title": f"Unlock {tech_track.get('name', 'tracked technology')}",
            "detail": " · ".join(
                value for value in (
                    str(tech_track.get("brokerSubtype") or "Tech Broker"),
                    destination_station, destination_system,
                ) if value
            ),
            "reason": "The active Tech Broker recipe is material-ready.",
            "after": "Use the broker, then let the Journal confirm the unlock.",
            "system": destination_system, "station": destination_station,
            "buttonLabel": (
                "COPY BROKER SYSTEM" if destination_system else "OPEN TECH BROKERS"
            ),
            "targetPage": -1 if destination_system else 4,
            "executable": True,
        }
    route = list(engineer_route or [])
    active_plans = [
        row for row in plans
        if str(row.get("targetStatus") or "") != "completed"
    ]
    if active_plans:
        planned_missing_keys = {
            str(material.get("key") or "")
            for plan in active_plans
            for progress_key in (
                "materialProgress", "experimentalMaterialProgress"
            )
            for material in (plan.get(progress_key) or [])
            if int(material.get("missing", 0) or 0) > 0
        }
        missing = [
            material for material in missing
            if str(material.get("key") or "") in planned_missing_keys
        ]
    def assigned_stop_index(plan):
        expected_job = (
            f"{str(plan.get('module') or '')} · "
            f"{str(plan.get('blueprint') or '')} · G"
        )
        for index, stop in enumerate(route):
            if any(
                str(job).startswith(expected_job)
                for job in (stop.get("jobNames") or [])
            ):
                return index
        return len(route)

    active_plans.sort(key=lambda row: (
        assigned_stop_index(row),
        not bool(row.get("priority")),
        {
            "experimental_pending": 0,
            "in_progress": 1,
            "not_started": 2,
        }.get(str(row.get("targetStatus") or ""), 3),
        int(row.get("index", 0) or 0),
    ))
    active_plan = active_plans[0] if active_plans else {}
    active_stop = None
    if active_plan:
        module = str(active_plan.get("module") or "")
        blueprint = str(active_plan.get("blueprint") or "")
        expected_job = f"{module} · {blueprint} · G"
        active_stop = next((
            stop for stop in route
            if any(
                str(job).startswith(expected_job)
                for job in (stop.get("jobNames") or [])
            )
        ), None)

    def craft_action(plan, stop, experimental=False):
        system = str((stop or {}).get("system") or "")
        station = str((stop or {}).get("station") or "")
        engineer = str((stop or {}).get("name") or plan.get("engineer") or "Engineer")
        module = str(plan.get("module") or "Module")
        blueprint = str(plan.get("blueprint") or "Blueprint")
        grade = int(plan.get("targetGrade", 0) or 0)
        identity = (
            f"{module} · {blueprint} · G{grade}"
            if grade > 0 else f"{module} · {blueprint}"
        )
        engineer_options = engineer_options_for_plan(
            plan, engineer_rows, blueprint_records
        )
        if not stop or not stop.get("craftable", False):
            return {
                "kind": "ENGINEER_PREPARE",
                "title": f"Prepare {identity}",
                "detail": "Open Engineer Navigation for the required access, rank and destination.",
                "reason": "The active craft is material-ready, but no executable Engineer stop is confirmed yet.",
                "after": "Once access is confirmed, continue this same craft without switching plans.",
                "system": "", "station": "", "buttonLabel": "OPEN ENGINEERS",
                "targetPage": 4, "executable": True,
                "engineerOptions": engineer_options,
                "portraitUrl": str((stop or {}).get("portraitUrl") or ""),
                "engineerName": engineer,
            }
        label = str(plan.get("experimental") or "Experimental Effect")
        if experimental:
            title = (
                f"Experimental · {identity}"
                if str(plan.get("planMode") or "") == "experimental_only"
                else f"Experimental · {identity} · {label}"
            )
            reason = "The target Grade is complete and the planned Experimental materials are ready."
            after = "After the Journal confirms the Experimental, continue with the next unfinished plan."
            kind = "EXPERIMENTAL_CRAFT"
        else:
            title = f"Continue {identity}"
            reason = "The active Grade still needs progress and at least one next roll is material-ready."
            after = (
                "Continue this Grade until the target is reached; then apply its planned Experimental."
                if plan.get("experimentalStatus") == "pending" else
                "Continue this Grade until complete; then the next unfinished plan becomes primary."
            )
            kind = "GRADE_CRAFT"
        return {
            "kind": kind, "title": title,
            "detail": " · ".join(value for value in (engineer, station, system) if value),
            "moduleName": module,
            "blueprintName": blueprint,
            "targetGrade": grade,
            "experimentalName": str(plan.get("experimental") or ""),
            "physicalSlot": str(plan.get("boundSlot") or ""),
            "reason": reason, "after": after,
            "system": system, "station": station,
            "buttonLabel": "COPY TARGET SYSTEM" if system else "OPEN ENGINEERS",
            "targetPage": -1 if system else 4, "executable": True,
            "engineerOptions": engineer_options,
            "portraitUrl": str((stop or {}).get("portraitUrl") or ""),
            "engineerName": engineer,
        }

    active_status = str(active_plan.get("targetStatus") or "")
    active_craft_ready = False
    active_craft_experimental = False
    if active_plan and active_status in {"not_started", "in_progress"}:
        active_craft_ready = bool(active_plan.get("canCraftNext"))
        active_progress = list(active_plan.get("materialProgress") or [])
    elif active_plan and active_status == "experimental_pending":
        active_craft_ready = bool(active_plan.get("experimentalReady"))
        active_craft_experimental = active_craft_ready
        active_progress = list(active_plan.get("experimentalMaterialProgress") or [])
    else:
        active_progress = []

    if active_plan and active_status == "experimental_pending" and not active_progress:
        return {
            "kind": "EXPERIMENTAL_BLOCKER",
            "title": f"Review Experimental for {active_plan.get('module', 'module')}",
            "detail": "The Experimental is still open, but its material recipe is not resolved.",
            "reason": "The plan must not be marked complete or replaced by an unrelated trade.",
            "after": "Once the Experimental recipe is resolved, its materials or craft become primary.",
            "system": "", "station": "", "buttonLabel": "OPEN WISHLIST",
            "targetPage": 1, "executable": True,
        }
    # Engineering is deliberately a two-phase workflow: acquire enough
    # materials for every open plan first, then visit Engineers.  A tracked or
    # otherwise prioritised plan controls the later craft order, but must never
    # hide shortages belonging to another physical module slot.
    if trades and missing:
        trade = trades[0]
        system = str(trade.get("system") or "")
        station = str(trade.get("station") or "")
        return {
            "kind": "TRADE",
            "title": str(trade.get("instruction") or "Complete material trade"),
            "detail": " · ".join(value for value in (
                str(trade.get("category") or "").title() + " Material Trader",
                station, system,
            ) if value),
            "reason": (
                f"This safe trade covers {int(trade.get('receiveAmount', 0) or 0)} "
                f"required units while protected build stock remains reserved."
            ),
            "after": "After the Journal confirms it, continue with the next highlighted trade or craft.",
            "system": system,
            "station": station,
            "buttonLabel": "COPY TRADER SYSTEM" if system else "OPEN MATERIALS",
            "targetPage": 2 if not system else -1,
            "executable": bool(system and station),
        }
    if missing:
        material = missing[0]
        amount = int(material.get("missing", 0) or 0)
        name = str(material.get("name") or material.get("key") or "material")
        return {
            "kind": "COLLECT",
            "title": f"Collect {amount} × {name}",
            "detail": "Open Material Details for verified acquisition methods.",
            "reason": (
                f"{name} is still missing and no safe inventory-protected "
                "Material Trader exchange is currently available."
            ),
            "after": "When the material arrives in the Journal, the next trade or craft will appear automatically.",
            "system": "",
            "station": "",
            "buttonLabel": "OPEN MATERIALS",
            "targetPage": 2,
            "executable": True,
        }
    if active_craft_ready:
        return craft_action(
            active_plan, active_stop, experimental=active_craft_experimental
        )
    if route:
        stop = route[0]
        if not stop.get("craftable", False):
            guide = dict(stop.get("unlockGuide") or {})
            system = str(guide.get("navigationSystem") or "")
            station = str(guide.get("navigationStation") or "")
            next_step = str(
                guide.get("nextAction")
                or (stop.get("blockReasons") or [
                    "Unlock or rank up this Engineer."
                ])[0]
            )
            return {
                "kind": "ENGINEER_UNLOCK",
                "title": f"Unlock {stop.get('name', 'required Engineer')}",
                "detail": next_step,
                "reason": (
                    f"The selected blueprint requires an unlocked Engineer with "
                    f"sufficient rank; {stop.get('name', 'this Engineer')} is the "
                    "best eligible route target but is not craftable yet."
                ),
                "after": "After access and rank are confirmed, the ready craft becomes the primary action.",
                "system": system,
                "station": station,
                "portraitUrl": str(stop.get("portraitUrl") or ""),
                "engineerName": str(stop.get("name") or ""),
                "buttonLabel": "COPY TARGET SYSTEM" if system else "OPEN ENGINEERS",
                "targetPage": -1 if system else 4,
                "executable": True,
            }
        distance = float(stop.get("distance", -1) or -1)
        system = str(stop.get("system") or "")
        station = str(stop.get("station") or "")
        jobs = int(stop.get("readyJobs", 0) or 0)
        return {
            "kind": "ENGINEER_TRAVEL",
            "title": f"Craft {jobs} ready job{'s' if jobs != 1 else ''} at {stop.get('name', 'Engineer')}",
            "detail": (
                f"{station} · {jobs} material-ready "
                f"job{'s' if jobs != 1 else ''}"
                + (f" · {distance:.1f} ly" if distance >= 0 else "")
            ),
            "reason": (
                "All required materials are present, this Engineer is unlocked, "
                "and the Journal rank meets every assigned target grade."
            ),
            "after": "After the Journal records the craft, the next unfinished plan becomes primary.",
            "system": system,
            "station": station,
            "portraitUrl": str(stop.get("portraitUrl") or ""),
            "engineerName": str(stop.get("name") or ""),
            "buttonLabel": "COPY TARGET SYSTEM" if system else "OPEN ENGINEERS",
            "targetPage": -1 if system else 4,
            "executable": True,
        }
    return {
        "kind": "COMPLETE",
        "title": "Engineering plan complete",
        "detail": "No open material or Engineer steps remain.",
        "reason": "The active wishlist has no unfinished engineering jobs.",
        "after": "Add another blueprint plan when you are ready to continue engineering.",
        "system": "",
        "station": "",
        "buttonLabel": "OPEN WISHLIST",
        "targetPage": 1,
        "executable": True,
    }


def blueprint_catalog(data_dir):
    groups = defaultdict(list)
    for record in read_json(data_dir / "blueprints.json", []):
        if (
            not isinstance(record, dict)
            or record.get("Grade") is None
            or not real_engineers(record)
        ):
            continue
        module = str(record.get("Type") or "").strip()
        name = str(record.get("Name") or "").strip()
        if module and name:
            groups[(module, name)].append(record)
    rows = []
    for (module, name), grades in groups.items():
        engineers = sorted({
            engineer
            for grade in grades
            for engineer in real_engineers(grade)
        })
        rows.append({
            "id": f"{module}\u241f{name}",
            "category": engineering_module_category(module),
            "module": module,
            "name": name,
            "maxGrade": max(int(grade.get("Grade", 0) or 0) for grade in grades),
            "engineers": ", ".join(engineers),
        })
    category_rank = {
        category: index for index, category in enumerate(ENGINEERING_CATEGORY_ORDER)
    }
    rows.sort(key=lambda row: (
        category_rank.get(row["category"], len(category_rank)),
        row["module"].casefold(),
        row["name"].casefold(),
    ))
    return rows


def build_engineering_plan(
    grades, current_grade, target_grade, *, plan_id="", instance="",
    experimental_id="", experimental_name="", ship_id="", slot="", module_id="",
    plan_mode="", journal_baseline=None,
):
    """Build a Classic-compatible deterministic Rank-5 engineering task."""
    current_grade = max(0, int(current_grade or 0))
    target_grade = max(1, int(target_grade or 1))
    start = 1 if current_grade <= 0 else current_grade + 1
    plan = []
    rolls = {}
    for source in sorted(
        (value for value in grades or [] if isinstance(value, dict)),
        key=lambda value: int(value.get("Grade", 0) or 0),
    ):
        grade = int(source.get("Grade", 0) or 0)
        if start <= grade <= target_grade:
            record = deepcopy(source)
            record["_Rolls"] = grade
            rolls[str(grade)] = grade
            plan.append(record)
    if plan:
        mode = str(plan_mode or ("combined" if experimental_id else "grade_only"))
        plan[0]["_Planner"] = {
            "plan_id": str(plan_id or uuid.uuid4()),
            "instance": str(instance or "Module 1"),
            "current_grade": current_grade,
            "current_label": (
                "Not engineered" if current_grade <= 0 else f"G{current_grade}"
            ),
            "target_grade": target_grade,
            "profile": "Fixed Rank-5 system",
            "rolls": rolls,
            "estimated_total_rolls": sum(rolls.values()),
            "experimental_id": str(experimental_id or ""),
            "experimental_name": str(experimental_name or ""),
            "plan_mode": mode,
            "ship_id": str(ship_id or ""),
            "slot": str(slot or ""),
            "module_id": str(module_id or ""),
            "binding_required": not bool(ship_id and slot and module_id),
            "journal_baseline": deepcopy(
                journal_baseline if journal_baseline is not None else {
                    "fingerprint": "__START__", "timestamp": "",
                    "source": "plan_created_no_prior_craft",
                }
            ),
            "grade_progress": {},
            "blueprint_names": {
                str(item.get("Grade")): str(item.get("BlueprintName"))
                for item in plan if item.get("BlueprintName")
            },
            "blueprint_ids": {
                str(item.get("Grade")): str(item.get("BlueprintID"))
                for item in plan if item.get("BlueprintID") is not None
            },
            "blueprint_sources": {
                str(item.get("Grade")): str(item.get("BlueprintSource"))
                for item in plan if item.get("BlueprintSource")
            },
        }
    return plan


def build_experimental_plan(
    effect: dict[str, Any], *, plan_id: str = "", instance: str = "",
    ship_id: object = "", slot: str = "", module_id: str = "",
    current_grade: int = 0, module_type: str = "", blueprint_group_id: str = "",
    journal_baseline=None,
) -> list[dict[str, Any]]:
    """Build one standalone Experimental Effect target without a Grade task."""
    if not isinstance(effect, dict):
        return []
    experimental_id = str(effect.get("ExperimentalId") or effect.get("Name") or "")
    if not experimental_id:
        return []
    record = deepcopy(effect)
    record.update({"Kind": "ExperimentalEffect", "Grade": None})
    record["_Planner"] = {
        "plan_id": str(plan_id or uuid.uuid4()),
        "instance": str(instance or "Module 1"),
        "plan_mode": "experimental_only",
        "current_grade": max(0, int(current_grade or 0)),
        "target_grade": 0,
        "experimental_id": experimental_id,
        "experimental_name": str(effect.get("Name") or "Experimental Effect"),
        "experimental_complete": False,
        "ship_id": str(ship_id or ""),
        "slot": str(slot or ""),
        "module_id": str(module_id or ""),
        "module_type": str(module_type or ""),
        "blueprint_group_id": str(blueprint_group_id or ""),
        "binding_required": not bool(ship_id and slot and module_id),
        "journal_baseline": deepcopy(
            journal_baseline if journal_baseline is not None else {
                "fingerprint": "__START__", "timestamp": "",
                "source": "plan_created_no_prior_craft",
            }
        ),
        "grade_progress": {}, "blueprint_names": {}, "blueprint_ids": {},
        "blueprint_sources": {}, "rolls": {}, "estimated_total_rolls": 0,
    }
    return [record]


def planner_mode(planner: dict[str, Any]) -> str:
    """Migrate legacy plans lazily without rewriting persisted user data."""
    mode = str(planner.get("plan_mode") or "")
    if mode in {"grade_only", "experimental_only", "combined"}:
        return mode
    return "combined" if planner.get("experimental_id") else "grade_only"


def planner_physical_identity(planner: dict[str, Any]) -> tuple[str, ...]:
    """Identify one bound module without deduplicating across ship slots."""
    ship_id = str(planner.get("ship_id") or "")
    slot = str(planner.get("slot") or "")
    module_id = normalize(planner.get("module_id"))
    if ship_id and slot:
        return "bound", ship_id, slot.casefold(), module_id
    return (
        "unbound",
        str(planner.get("instance") or "Module 1").strip().casefold(),
    )


def task_signature(task):
    if not isinstance(task, list) or not task:
        return ()
    first = task[0]
    planner = first.get("_Planner", {}) if isinstance(first, dict) else {}
    if first.get("Kind") == "ExperimentalEffect":
        return (
            "experimental",
            first.get("ExperimentalId") or first.get("Name"),
            first.get("_ParentPlanId") or planner.get("instance"),
            planner_physical_identity(planner),
        )
    return (
        first.get("Type"),
        first.get("Name"),
        planner_physical_identity(planner),
        tuple(
            (item.get("Grade"), item.get("_Rolls", 1))
            for item in task if isinstance(item, dict)
        ),
        tuple(
            item.get("ExperimentalId") or item.get("Name")
            for item in task if isinstance(item, dict)
            and item.get("Kind") == "ExperimentalEffect"
        ),
    )


def write_ship_tasks(path, ship, tasks_to_add):
    """Atomically append one physical module plan and its linked effect."""
    payload = read_json(path, {})
    existing = payload.setdefault(ship, [])
    signatures = {task_signature(task) for task in existing}
    tasks_to_add = [
        task for task in tasks_to_add
        if isinstance(task, list) and task
    ]
    if not tasks_to_add:
        return 0
    primary_signature = task_signature(tasks_to_add[0])
    primary = tasks_to_add[0][0]
    is_plan_group = (
        isinstance(primary, dict)
        and primary.get("Kind") != "ExperimentalEffect"
        and bool(primary.get("_Planner"))
    )
    # Blueprint and experimental form one transaction. If this exact physical
    # module is already planned, append neither (especially no orphan effect).
    if is_plan_group and primary_signature in signatures:
        return 0
    added = 0
    for task in tasks_to_add:
        signature = task_signature(task)
        if signature and signature not in signatures:
            existing.append(task)
            signatures.add(signature)
            added += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return added


def remove_ship_task(path, ship, index):
    payload = read_json(path, {})
    tasks = payload.get(ship, [])
    if not (0 <= int(index) < len(tasks)):
        return False
    removed = tasks.pop(int(index))
    first = removed[0] if isinstance(removed, list) and removed else {}
    plan_id = str(first.get("_Planner", {}).get("plan_id") or "")
    if plan_id:
        tasks[:] = [
            task for task in tasks
            if not (
                isinstance(task, list) and task
                and task[0].get("_ParentPlanId") == plan_id
            )
        ]
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def set_prioritized_ship_plan(path, ship, plan_id):
    """Toggle exactly one open plan as the persistent Track-now target."""
    payload = read_json(path, {})
    tasks = payload.get(ship, [])
    wanted = str(plan_id or "")
    selected = next((
        task[0].get("_Planner", {})
        for task in tasks
        if isinstance(task, list) and task and isinstance(task[0], dict)
        and str(task[0].get("_Planner", {}).get("plan_id") or "") == wanted
        and wishlist_target_status(task[0].get("_Planner", {}))["code"] != "completed"
    ), None)
    if wanted and selected is None:
        return False
    toggle_off = bool(selected and selected.get("priority"))
    changed = False
    for task in tasks:
        if not isinstance(task, list) or not task or not isinstance(task[0], dict):
            continue
        planner = task[0].get("_Planner", {})
        if not planner:
            continue
        should_prioritize = bool(
            not toggle_off and wanted
            and str(planner.get("plan_id") or "") == wanted
        )
        if bool(planner.get("priority")) != should_prioritize:
            planner["priority"] = should_prioritize
            changed = True
    if not changed:
        return bool(selected)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def replace_ship_plan(path, ship, index, replacement, experimental=None):
    """Replace one editable plan and its linked experimental atomically."""
    payload = read_json(path, {})
    tasks = payload.get(ship, [])
    if not (0 <= int(index) < len(tasks)) or not replacement:
        return False
    old = tasks[int(index)]
    first = old[0] if isinstance(old, list) and old else {}
    old_plan_id = str(first.get("_Planner", {}).get("plan_id") or "")
    old_priority = bool(first.get("_Planner", {}).get("priority"))
    if replacement and isinstance(replacement[0], dict):
        replacement[0].setdefault("_Planner", {})["priority"] = old_priority
    tasks[int(index)] = replacement
    if old_plan_id:
        tasks[:] = [
            task for position, task in enumerate(tasks)
            if position == int(index) or not (
                isinstance(task, list) and task
                and task[0].get("_ParentPlanId") == old_plan_id
            )
        ]
    if experimental:
        tasks.insert(int(index) + 1, experimental)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def duplicate_ship_plan(path, ship, index, journal_baseline=None):
    """Duplicate a plan as a distinct physical module instance."""
    payload = read_json(path, {})
    tasks = payload.get(ship, [])
    if not (0 <= int(index) < len(tasks)):
        return False
    source = tasks[int(index)]
    if not isinstance(source, list) or not source:
        return False
    copy = deepcopy(source)
    planner = copy[0].setdefault("_Planner", {})
    if (
        copy[0].get("Kind") == "ExperimentalEffect"
        and planner_mode(planner) != "experimental_only"
    ):
        return False
    old_id = str(planner.get("plan_id") or "")
    new_id = str(uuid.uuid4())
    planner["plan_id"] = new_id
    planner["priority"] = False
    planner["journal_baseline"] = deepcopy(journal_baseline or {})
    base = str(planner.get("instance") or "Module 1")
    planner["instance"] = f"{base} copy"
    additions = [copy]
    paired = next(
        (
            deepcopy(task) for task in tasks
            if isinstance(task, list) and task
            and old_id and task[0].get("_ParentPlanId") == old_id
        ),
        None,
    )
    if paired:
        paired[0]["_ParentPlanId"] = new_id
        additions.append(paired)
    tasks[int(index) + 1:int(index) + 1] = additions
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def move_ship_plan(path, source_ship, index, target_ship):
    payload = read_json(path, {})
    source = payload.get(source_ship, [])
    target = payload.get(target_ship)
    if target is None or source_ship == target_ship or not (0 <= int(index) < len(source)):
        return False
    task = source.pop(int(index))
    additions = [task]
    first = task[0] if isinstance(task, list) and task else {}
    plan_id = str(first.get("_Planner", {}).get("plan_id") or "")
    if plan_id:
        paired = [
            value for value in source
            if isinstance(value, list) and value
            and value[0].get("_ParentPlanId") == plan_id
        ]
        additions.extend(paired)
        source[:] = [value for value in source if value not in paired]
    moved_priority = bool(first.get("_Planner", {}).get("priority"))
    if moved_priority:
        for existing in target:
            if isinstance(existing, list) and existing and isinstance(existing[0], dict):
                existing[0].get("_Planner", {}).update({"priority": False})
    target.extend(additions)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def _ingredient_signature(items: object, amount_key: str) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(
        (
            normalize(journal_material_name(item)),
            int(item.get(amount_key, 0) or 0),
        )
        for item in (items or []) if isinstance(item, dict)
    ))


def _ingredient_display_signature(
    items: object, amount_key: str
) -> tuple[tuple[str, int], ...]:
    """Match legacy saved plans without weakening Journal material identity."""
    return tuple(sorted(
        (
            normalize(item.get("Name_Localised") or journal_material_name(item)),
            int(item.get(amount_key, 0) or 0),
        )
        for item in (items or []) if isinstance(item, dict)
    ))


def wishlist_target_status(planner: dict[str, Any]) -> dict[str, Any]:
    """Return separate Grade, Experimental and aggregate target states."""
    mode = planner_mode(planner)
    target = int(planner.get("target_grade", 0) or 0)
    progress = planner.get("grade_progress", {}) or {}
    reached = max(
        (int(grade) for grade, quality in progress.items() if float(quality or 0) > 0),
        default=int(planner.get("current_grade", 0) or 0),
    )
    experimental_required = bool(planner.get("experimental_id"))
    experimental_complete = bool(planner.get("experimental_complete"))
    grade_complete = mode == "experimental_only" or (
        reached >= target and float(progress.get(str(target), 0) or 0) >= 0.999
    )
    if mode == "experimental_only":
        grade_status = "not_applicable"
    elif reached <= int(planner.get("current_grade", 0) or 0) and not progress:
        grade_status = "not_started"
    elif grade_complete:
        grade_status = "completed"
    else:
        grade_status = "in_progress"
    experimental_status = (
        "not_applicable" if not experimental_required else
        "completed" if experimental_complete else "pending"
    )
    if mode == "experimental_only":
        code = "completed" if experimental_complete else "experimental_pending"
        text = "Fully completed" if experimental_complete else "Experimental pending"
    elif not grade_complete:
        code = grade_status
        text = (
            "Not started" if grade_status == "not_started" else
            f"In progress · Grade {max(reached, 0)} of {target} reached"
        )
    elif mode == "combined" and not experimental_complete:
        code, text = "experimental_pending", "Target grade reached · Experimental pending"
    else:
        code, text = "completed", "Fully completed"
    return {
        "code": code, "text": text, "gradeReached": reached,
        "targetGrade": target, "gradeStatus": grade_status,
        "gradeStatusLabel": GRADE_STATUS_LABELS[grade_status],
        "experimentalStatus": experimental_status,
        "experimentalStatusLabel": EXPERIMENTAL_STATUS_LABELS[experimental_status],
        "planMode": mode,
    }


def _craft_matches_binding(
    planner: dict[str, Any], event: dict[str, Any], ship_id: object,
    module_type: object = "",
) -> bool:
    exact_slot = bool(
        not planner.get("binding_required")
        and str(planner.get("ship_id") or "") == str(ship_id or "")
        and str(planner.get("slot") or "") == str(event.get("Slot") or "")
    )
    if not exact_slot:
        return False
    if normalize(planner.get("module_id")) == normalize(event.get("Module")):
        return True
    wanted = module_type or planner.get("module_type")
    return bool(
        wanted
        and module_matches_type(planner.get("module_id"), wanted)
        and module_matches_type(event.get("Module"), wanted)
    )


def _craft_can_bind(
    planner: dict[str, Any], first: dict[str, Any], event: dict[str, Any],
    ship_id: object,
) -> bool:
    """Allow one unbound plan to claim exact craft evidence, never a manual ID."""
    module_type = first.get("Type") or planner.get("module_type")
    if _craft_matches_binding(planner, event, ship_id, module_type):
        return True
    if not planner.get("binding_required") or planner.get("module_id"):
        return False
    planned_ship = str(planner.get("ship_id") or "")
    if planned_ship and planned_ship != str(ship_id or ""):
        return False
    return bool(
        ship_id and event.get("Slot") and event.get("Module")
        and module_matches_type(event.get("Module"), module_type)
    )


def _experimental_craft_matches(
    planner: dict[str, Any], event: dict[str, Any]
) -> bool:
    """Match Frontier machine IDs, EDEC IDs and localized effect names."""
    journal_value = str(
        event.get("ApplyExperimentalEffect")
        or event.get("ExperimentalEffect") or ""
    )
    journal_key = normalize(journal_value)
    canonical_name = JOURNAL_EXPERIMENTAL_NAMES.get(journal_key, "")
    event_keys = {
        normalize(value) for value in (
            journal_value, canonical_name,
            event.get("ExperimentalEffect_Localised"),
        ) if value
    }
    planner_keys = {
        normalize(value) for value in (
            planner.get("experimental_id"),
            planner.get("experimental_name"),
        ) if value
    }
    return bool(event_keys.intersection(planner_keys))


def apply_engineer_craft(
    path: Path, ship: str, event: dict[str, Any], preferred_plan_id: str = "",
    ship_id: object = "", eligible_plan_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Apply one Journal craft to exactly one unambiguous pinned plan."""
    if not isinstance(event, dict) or event.get("event") != "EngineerCraft":
        return {"status": "ignored", "reason": "Not an EngineerCraft event."}
    if not is_completed_engineer_craft(event):
        return {
            "status": "ignored",
            "reason": "EngineerCraft event has no complete applied-craft evidence.",
            "completed": False,
        }
    learned_catalog_path = path.parent / "blueprint_id_catalog_learned.json"
    learn_blueprint_id_catalog([event], learned_catalog_path)
    identity = blueprint_id_evidence(
        event,
        load_blueprint_id_catalog(learned_path=learned_catalog_path),
    )
    if identity["status"] != "confirmed":
        diagnostic_path = path.parent / "blueprint_diagnostics.json"
        diagnostics = read_json(diagnostic_path, [])
        diagnostics = diagnostics if isinstance(diagnostics, list) else []
        message = (
            f"Blueprint {event.get('BlueprintName') or 'unknown'} G"
            f"{int(event.get('Level', 0) or 0)} uses Journal ID "
            f"{event.get('BlueprintID')} ({identity['source']})."
        )
        if message not in diagnostics:
            diagnostics.append(message)
            _write_json_if_changed(diagnostic_path, diagnostics[-100:])
    payload = read_json(path, {})
    tasks = payload.get(ship, [])
    event_key = engineer_craft_fingerprint(event, ship_id)
    if event.get("ApplyExperimentalEffect"):
        wanted = str(event.get("ApplyExperimentalEffect") or "")
        candidates = []
        for index, task in enumerate(tasks):
            if not isinstance(task, list) or not task:
                continue
            first = task[0]
            planner = first.get("_Planner", {})
            if (
                eligible_plan_ids is not None
                and str(planner.get("plan_id") or "") not in eligible_plan_ids
            ):
                continue
            mode = planner_mode(planner)
            if (
                planner
                and mode in {"experimental_only", "combined"}
                and not planner.get("experimental_complete")
                and _experimental_craft_matches(planner, event)
                and _craft_can_bind(planner, first, event, ship_id)
                and (
                    mode == "experimental_only"
                    or (
                        int(event.get("Level", 0) or 0)
                            == int(planner.get("target_grade", 0) or 0)
                        and float(
                            (planner.get("grade_progress", {}) or {}).get(
                                str(event.get("Level")), 0
                            ) or 0
                        ) >= 0.999
                    )
                )
                and (
                    mode == "experimental_only"
                    or
                    not planner.get("blueprint_names", {}).get(str(event.get("Level")))
                    or planner["blueprint_names"][str(event.get("Level"))]
                        == str(event.get("BlueprintName") or "")
                )
                and (
                    mode == "experimental_only"
                    or
                    not planner.get("blueprint_ids", {}).get(str(event.get("Level")))
                    or str(planner["blueprint_ids"][str(event.get("Level"))])
                        == str(event.get("BlueprintID") or "")
                    or identity["status"] == "conflict"
                )
            ):
                candidates.append((index, planner))
        action = "experimental"
    else:
        level = int(event.get("Level", 0) or 0)
        engineer = str(event.get("Engineer") or "")
        signature = _ingredient_signature(event.get("Ingredients"), "Count")
        candidates = []
        for index, task in enumerate(tasks):
            if not isinstance(task, list) or not task:
                continue
            first = task[0]
            planner = first.get("_Planner", {})
            if (
                not planner or planner_mode(planner) == "experimental_only"
                or event_key in (planner.get("processed_crafts", []) or [])
                or (
                    eligible_plan_ids is not None
                    and str(planner.get("plan_id") or "") not in eligible_plan_ids
                )
            ):
                continue
            expected_name = str(
                (planner.get("blueprint_names", {}) or {}).get(str(level)) or ""
            )
            expected_id = str(
                (planner.get("blueprint_ids", {}) or {}).get(str(level)) or ""
            )
            grade = next(
                (
                    item for item in task if isinstance(item, dict)
                    and int(item.get("Grade", 0) or 0) == level
                    and _craft_can_bind(planner, first, event, ship_id)
                    and (not expected_name or expected_name == str(event.get("BlueprintName") or ""))
                    and (
                        not expected_id
                        or expected_id == str(event.get("BlueprintID") or "")
                        or identity["status"] == "conflict"
                    )
                    # Multiple Engineers can offer the same blueprint. The
                    # selected Engineer is routing intent, not an exclusion;
                    # compatibility is sufficient after exact module identity.
                    and (not engineer or engineer in real_engineers(item))
                    and (
                        _ingredient_signature(item.get("Ingredients"), "Size")
                        == signature
                        or _ingredient_display_signature(
                            item.get("Ingredients"), "Size"
                        ) == _ingredient_display_signature(
                            event.get("Ingredients"), "Count"
                        )
                        # Once the physical ship slot is exact, the Journal is
                        # authoritative if Frontier changed a recipe. Unbound
                        # plans still require the catalog signature for safety.
                        or _craft_matches_binding(
                            planner, event, ship_id,
                            first.get("Type") or planner.get("module_type"),
                        )
                    )
                ),
                None,
            )
            if grade:
                if remaining_grade_rolls(planner, grade) > 0:
                    candidates.append((index, planner))
        action = "grade"
    exact_candidates = [
        candidate for candidate in candidates
        if _craft_matches_binding(
            candidate[1], event, ship_id,
            tasks[candidate[0]][0].get("Type")
            or candidate[1].get("module_type"),
        )
    ]
    if len(exact_candidates) == 1:
        # Exact Journal ShipID/Slot/Module evidence is authoritative. An armed
        # plan is routing priority, never permission to block another slot.
        candidates = exact_candidates
    if preferred_plan_id:
        preferred_candidates = [
            candidate for candidate in candidates
            if str(candidate[1].get("plan_id") or "") == str(preferred_plan_id)
        ]
        if preferred_candidates:
            candidates = preferred_candidates
    if not candidates:
        return {
            "status": "unmatched",
            "reason": "No matching incomplete pinned plan.",
        }
    if len(candidates) != 1:
        return {
            "status": "ambiguous",
            "reason": (
                f"{len(candidates)} module instances match this craft; "
                "no plan was changed."
            ),
        }
    index, planner = candidates[0]
    if planner.get("binding_required"):
        planner.update({
            "ship_id": str(ship_id),
            "slot": str(event.get("Slot") or ""),
            "module_id": str(event.get("Module") or ""),
            "binding_required": False,
        })
    instance = str(planner.get("instance") or "module")
    if action == "experimental":
        planner["experimental_complete"] = True
        if tasks[index][0].get("Kind") == "ExperimentalEffect":
            tasks[index][0]["_Completed"] = True
        level = int(event.get("Level", 0) or 0)
        planner.setdefault("blueprint_names", {})[str(level)] = str(
            event.get("BlueprintName") or ""
        )
        planner.setdefault("blueprint_ids", {})[str(level)] = str(
            event.get("BlueprintID") or ""
        )
        planner.setdefault("blueprint_sources", {})[str(level)] = str(
            identity["source"]
        )
        reason = (
            f"{event.get('ExperimentalEffect_Localised') or 'Experimental'} "
            f"applied to {instance}."
        )
        plan_id = str(planner.get("plan_id") or "")
        for task in tasks:
            if (
                isinstance(task, list) and task
                and task[0].get("_ParentPlanId") == plan_id
            ):
                task[0]["_Completed"] = True
        planner.setdefault("processed_crafts", []).append(event_key)
    else:
        level = int(event.get("Level", 0) or 0)
        grade = next(
            item for item in tasks[index] if isinstance(item, dict)
            and int(item.get("Grade", 0) or 0) == level
        )
        journal_ingredients = [
            {
                "Name": journal_material_name(item),
                **(
                    {"Name_Localised": str(item.get("Name_Localised"))}
                    if item.get("Name_Localised") else {}
                ),
                "Size": int(item.get("Count", 0) or 0),
            }
            for item in (event.get("Ingredients") or [])
            if isinstance(item, dict) and journal_material_name(item)
        ]
        if journal_ingredients:
            grade["Ingredients"] = journal_ingredients
        planned = int(grade.get("_Rolls", 1) or 1)
        completed = planner.setdefault("crafts_completed", {})
        done = int(completed.get(str(level), 0) or 0) + 1
        completed[str(level)] = done
        progress = planner.setdefault("grade_progress", {})
        progress[str(level)] = max(
            float(progress.get(str(level), 0) or 0),
            float(event.get("Quality", 0) or 0),
        )
        planner.setdefault("blueprint_names", {})[str(level)] = str(
            event.get("BlueprintName") or ""
        )
        planner.setdefault("blueprint_ids", {})[str(level)] = str(
            event.get("BlueprintID") or ""
        )
        planner.setdefault("blueprint_sources", {})[str(level)] = str(
            identity["source"]
        )
        planner.setdefault("processed_crafts", []).append(event_key)
        grade_complete = float(progress[str(level)] or 0) >= 0.999
        reason = f"{instance}: G{level} craft {done}"
        if done <= planned:
            reason += f"/{planned} estimated"
        reason += " · grade complete." if grade_complete else " · more progress required."
    planner["last_change_reason"] = reason
    target_status = wishlist_target_status(planner)
    if target_status["code"] == "completed":
        planner["priority"] = False
    planner["last_craft_status"] = target_status["code"]
    planner["last_craft_event"] = event_key
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return {
        "status": "applied",
        "index": index,
        "instance": instance,
        "reason": reason,
        "completed": target_status["code"] == "completed",
        "targetStatus": target_status,
    }


def engineer_craft_fingerprint(
    event: dict[str, Any], resolved_ship_id: object = "",
) -> str:
    """Return the complete stable identity used by cursor and plan dedupe."""
    return "|".join((
        str(event.get("timestamp") or ""),
        str(event.get("ShipID") or resolved_ship_id or ""),
        str(event.get("Slot") or ""),
        str(event.get("Module") or ""),
        str(event.get("BlueprintID") or ""),
        str(event.get("BlueprintName") or ""),
        str(event.get("Level") or ""),
        str(event.get("Quality") or ""),
        str(event.get("ApplyExperimentalEffect") or ""),
        str(event.get("ExperimentalEffect") or ""),
    ))


def _craft_events_with_ship_context(events: list[dict[str, Any]]):
    """Yield completed crafts chronologically with the ship active at the event."""
    current_ship_id = ""
    rows = []
    for sequence, source in enumerate(events or []):
        if not isinstance(source, dict):
            continue
        event_name = str(source.get("event") or "")
        if source.get("ShipID") not in (None, "") and event_name in {
            "LoadGame", "Loadout", "ShipyardSwap", "SetUserShipName",
            "EngineerCraft",
        }:
            current_ship_id = str(source.get("ShipID"))
        elif event_name == "ShipyardBuy" and source.get("NewShipID") not in (None, ""):
            current_ship_id = str(source.get("NewShipID"))
        if not is_completed_engineer_craft(source):
            continue
        event = dict(source)
        event["_ResolvedShipID"] = str(event.get("ShipID") or current_ship_id)
        rows.append((str(event.get("timestamp") or ""), sequence, event))
    return [event for _timestamp, _sequence, event in sorted(rows)]


def journal_craft_baseline(
    events: list[dict[str, Any]], ship_id: object,
) -> dict[str, str]:
    """Capture the immutable last-seen craft boundary for one physical ship."""
    wanted = str(ship_id or "")
    rows = [
        event for event in _craft_events_with_ship_context(events)
        if str(event.get("_ResolvedShipID") or "") == wanted
    ]
    if not rows:
        return {"fingerprint": "__START__", "timestamp": ""}
    event = rows[-1]
    return {
        "fingerprint": engineer_craft_fingerprint(event, wanted),
        "timestamp": str(event.get("timestamp") or ""),
    }


def _eligible_plan_ids_after_baseline(
    plan_payload: object, ship: str, ship_id: str,
    event: dict[str, Any], ordered_ship_fingerprints: list[str],
) -> set[str]:
    """Return plans whose immutable Journal boundary precedes this craft."""
    event_fingerprint = engineer_craft_fingerprint(event, ship_id)
    try:
        event_position = ordered_ship_fingerprints.index(event_fingerprint)
    except ValueError:
        return set()
    eligible = set()
    tasks = plan_payload.get(ship, []) if isinstance(plan_payload, dict) else []
    for task in tasks:
        if not isinstance(task, list) or not task or not isinstance(task[0], dict):
            continue
        planner = task[0].get("_Planner", {}) or {}
        plan_id = str(planner.get("plan_id") or "")
        if not plan_id:
            continue
        baseline = planner.get("journal_baseline")
        if not isinstance(baseline, dict) or not baseline:
            # Legacy plans predate baselines and retain their existing retry behavior.
            eligible.add(plan_id)
            continue
        boundary = str(baseline.get("fingerprint") or "")
        if boundary == "__START__":
            eligible.add(plan_id)
            continue
        try:
            boundary_position = ordered_ship_fingerprints.index(boundary)
            after_boundary = event_position > boundary_position
        except ValueError:
            after_boundary = bool(
                str(event.get("timestamp") or "")
                > str(baseline.get("timestamp") or "")
            )
        if after_boundary:
            eligible.add(plan_id)
    return eligible


def migrate_legacy_plan_baselines(
    plan_path: Path, plan_payload: object, craft_rows: list[dict[str, Any]],
    acknowledged: set[str], by_ship_id: dict[str, str],
) -> int:
    """Anchor legacy plans at the latest proven processed craft per ship."""
    if not isinstance(plan_payload, dict):
        return 0
    ship_ids_by_label = {label: ship_id for ship_id, label in by_ship_id.items()}
    last_acknowledged: dict[str, dict[str, str]] = {}
    latest_seen: dict[str, dict[str, str]] = {}
    for event in craft_rows:
        ship_id = str(event.get("_ResolvedShipID") or "")
        fingerprint = engineer_craft_fingerprint(event, ship_id)
        latest_seen[ship_id] = {
            "fingerprint": fingerprint,
            "timestamp": str(event.get("timestamp") or ""),
            "source": "legacy_unconfirmed_history",
        }
        if fingerprint in acknowledged:
            last_acknowledged[ship_id] = {
                "fingerprint": fingerprint,
                "timestamp": str(event.get("timestamp") or ""),
                "source": "legacy_acknowledged_cursor",
            }
    migrated = 0
    for ship, tasks in plan_payload.items():
        if not isinstance(tasks, list):
            continue
        fallback_ship_id = str(ship_ids_by_label.get(str(ship), ""))
        for task in tasks:
            if not isinstance(task, list) or not task or not isinstance(task[0], dict):
                continue
            planner = task[0].get("_Planner", {}) or {}
            if not planner:
                continue
            ship_id = str(planner.get("ship_id") or fallback_ship_id)
            baseline = planner.get("journal_baseline")
            boundary = (
                str(baseline.get("fingerprint") or "")
                if isinstance(baseline, dict) else ""
            )
            safe = last_acknowledged.get(ship_id)
            if boundary and boundary != "__START__":
                continue
            if boundary == "__START__" and safe:
                planner["journal_baseline"] = deepcopy(safe)
            elif not baseline:
                planner["journal_baseline"] = deepcopy(
                    safe or latest_seen.get(ship_id) or {
                        "fingerprint": "__START__", "timestamp": "",
                        "source": "legacy_waiting_for_first_craft",
                    }
                )
            else:
                continue
            migrated += 1
    if migrated:
        _write_json_if_changed(plan_path, plan_payload)
    return migrated


def is_unconfirmed_legacy_history(
    plan_payload: object, ship: str, event: dict[str, Any],
    ordered_ship_fingerprints: list[str],
) -> bool:
    """Identify pre-migration history that requires Commander confirmation."""
    event_fingerprint = engineer_craft_fingerprint(
        event, event.get("_ResolvedShipID")
    )
    try:
        event_position = ordered_ship_fingerprints.index(event_fingerprint)
    except ValueError:
        return False
    tasks = plan_payload.get(ship, []) if isinstance(plan_payload, dict) else []
    for task in tasks:
        if not isinstance(task, list) or not task or not isinstance(task[0], dict):
            continue
        first = task[0]
        if first.get("Kind") == "ExperimentalEffect" and first.get("_ParentPlanId"):
            continue
        planner = first.get("_Planner", {}) or {}
        if not planner.get("plan_id"):
            continue
        baseline = planner.get("journal_baseline")
        if not isinstance(baseline, dict):
            continue
        if str(baseline.get("source") or "") != "legacy_unconfirmed_history":
            continue
        boundary = str(baseline.get("fingerprint") or "")
        try:
            if event_position <= ordered_ship_fingerprints.index(boundary):
                return True
        except ValueError:
            if str(event.get("timestamp") or "") <= str(baseline.get("timestamp") or ""):
                return True
    return False


def is_craft_before_safe_plan_baseline(
    plan_payload: object, ship: str, event: dict[str, Any],
    ordered_ship_fingerprints: list[str],
) -> bool:
    """Recognize Journal evidence proven to predate every usable plan boundary."""
    event_fingerprint = engineer_craft_fingerprint(
        event, event.get("_ResolvedShipID")
    )
    try:
        event_position = ordered_ship_fingerprints.index(event_fingerprint)
    except ValueError:
        return False
    safe_boundaries = []
    tasks = plan_payload.get(ship, []) if isinstance(plan_payload, dict) else []
    for task in tasks:
        if not isinstance(task, list) or not task or not isinstance(task[0], dict):
            continue
        first = task[0]
        if first.get("Kind") == "ExperimentalEffect" and first.get("_ParentPlanId"):
            # Combined plans own one immutable boundary on their Grade parent.
            # The paired Experimental row must never veto that safe boundary.
            continue
        planner = first.get("_Planner", {}) or {}
        if not planner.get("plan_id"):
            continue
        baseline = planner.get("journal_baseline")
        if not isinstance(baseline, dict):
            return False
        boundary = str(baseline.get("fingerprint") or "")
        if not boundary or boundary == "__START__":
            return False
        try:
            safe_boundaries.append(ordered_ship_fingerprints.index(boundary))
        except ValueError:
            event_timestamp = str(event.get("timestamp") or "")
            boundary_timestamp = str(baseline.get("timestamp") or "")
            if not event_timestamp or not boundary_timestamp:
                return False
            if event_timestamp > boundary_timestamp:
                return False
    return bool(safe_boundaries) and all(
        event_position <= boundary for boundary in safe_boundaries
    )


def craft_issue_row(
    fingerprint: str, ship_id: str, event: dict[str, Any], reason: str,
    ship: str = "", historical: bool = False, reason_code: str = "",
) -> dict[str, Any]:
    """Keep enough exact Journal identity to judge NBA relevance later."""
    normalized_reason = str(reason or "unmatched")
    derived_reason_code = (
        str(reason_code).strip().upper() if reason_code else
        "HISTORICAL" if historical else
        "AMBIGUOUS" if "ambiguous" in normalized_reason.casefold() else
        "BINDING" if "bind" in normalized_reason.casefold() else
        "NO PLAN" if "no matching" in normalized_reason.casefold() else
        "UNMATCHED"
    )
    return {
        "fingerprint": fingerprint,
        "timestamp": str(event.get("timestamp") or ""),
        "ship": ship,
        "shipId": ship_id,
        "slot": str(event.get("Slot") or ""),
        "module": str(event.get("Module") or event.get("Module_Localised") or ""),
        "blueprintId": str(event.get("BlueprintID") or ""),
        "blueprintName": str(event.get("BlueprintName") or ""),
        "experimentalId": str(event.get("ApplyExperimentalEffect") or ""),
        "level": int(event.get("Level", 0) or 0),
        "historical": bool(historical),
        "reasonCode": derived_reason_code,
        "reason": normalized_reason,
    }


def reconcile_engineer_craft_batch(
    data_dir: Path, fleet_state: dict[str, Any], events: list[dict[str, Any]],
    preferred_plan_id: str = "",
) -> dict[str, Any]:
    """Apply every unseen craft in order before one final state is built."""
    with _CRAFT_BATCH_LOCK:
        return _reconcile_engineer_craft_batch_locked(
            data_dir, fleet_state, events, preferred_plan_id
        )


def dismiss_craft_tracking_issue(
    data_dir: Path, fingerprint: str, ship_id: object,
) -> bool:
    """Explicitly retire one unmatched craft without clearing other evidence."""
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        return False
    with _CRAFT_BATCH_LOCK:
        diagnostics = read_json(data_dir / "craft_batch_diagnostics.json", [])
        matching_rows = [
            row for row in (diagnostics if isinstance(diagnostics, list) else [])
            if isinstance(row, dict)
            and str(row.get("fingerprint") or "") == fingerprint
        ]
        if matching_rows and not any(
            str(row.get("shipId") or "") == str(ship_id or "")
            for row in matching_rows
        ):
            return False
        return bool(_dismiss_craft_tracking_issues_locked(
            data_dir, str(ship_id or ""),
            lambda row: str(row.get("fingerprint") or "") == fingerprint,
            {fingerprint},
        ))


def dismiss_historical_craft_tracking_issues(
    data_dir: Path, ship_id: object,
) -> int:
    """Retire only explicitly classified historical issues for one ship."""
    with _CRAFT_BATCH_LOCK:
        return _dismiss_craft_tracking_issues_locked(
            data_dir, str(ship_id or ""),
            lambda row: bool(row.get("historical")),
        )


def dismiss_selected_craft_tracking_issues(
    data_dir: Path, ship_id: object, fingerprints: object,
) -> int:
    """Retire only the explicitly selected unmatched evidence for one ship."""
    selected = {
        str(value).strip() for value in (fingerprints or []) if str(value).strip()
    }
    if not selected:
        return 0
    with _CRAFT_BATCH_LOCK:
        return _dismiss_craft_tracking_issues_locked(
            data_dir, str(ship_id or ""),
            lambda row: str(row.get("fingerprint") or "") in selected,
            selected,
        )


def _dismiss_craft_tracking_issues_locked(
    data_dir: Path, ship_id: str, predicate,
    explicit_fingerprints: set[str] | None = None,
) -> int:
    diagnostics_path = data_dir / "craft_batch_diagnostics.json"
    diagnostics = read_json(diagnostics_path, [])
    diagnostics = diagnostics if isinstance(diagnostics, list) else []
    selected = [
        row for row in diagnostics
        if isinstance(row, dict)
        and str(row.get("shipId") or "") == ship_id
        and predicate(row)
    ]
    fingerprints = {
        str(row.get("fingerprint") or "") for row in selected
        if row.get("fingerprint")
    }
    # Live state is authoritative for a Commander-initiated dismiss. A current
    # issue may not yet be mirrored in the bounded diagnostics file; still
    # persist its exact fingerprint so refresh cannot resurrect the row.
    fingerprints.update(
        str(value).strip() for value in (explicit_fingerprints or set())
        if str(value).strip()
    )
    if not fingerprints:
        return 0
    cursor_path = data_dir / "engineer_craft_cursor.json"
    cursor = read_json(cursor_path, {})
    cursor = cursor if isinstance(cursor, dict) else {}
    acknowledged = {
        str(value) for value in (cursor.get("acknowledged") or []) if value
    }
    acknowledged.update(fingerprints)
    cursor.update({
        "initialized": True,
        "acknowledged": sorted(acknowledged)[-10000:],
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _write_json_if_changed(cursor_path, cursor)
    retained = [
        row for row in diagnostics
        if not isinstance(row, dict)
        or str(row.get("fingerprint") or "") not in fingerprints
        or str(row.get("shipId") or "") != ship_id
    ]
    _write_json_if_changed(diagnostics_path, retained)
    return len(fingerprints)


def _reconcile_engineer_craft_batch_locked(
    data_dir: Path, fleet_state: dict[str, Any], events: list[dict[str, Any]],
    preferred_plan_id: str = "",
) -> dict[str, Any]:
    plan_path = data_dir / "ship_blueprints.json"
    cursor_path = data_dir / "engineer_craft_cursor.json"
    cursor = read_json(cursor_path, {})
    cursor = cursor if isinstance(cursor, dict) else {}
    cursor_was_initialized = bool(cursor.get("initialized"))
    acknowledged = {
        str(value) for value in (cursor.get("acknowledged") or []) if value
    }
    plan_payload = read_json(plan_path, {})
    if isinstance(plan_payload, dict):
        acknowledged.update(
            str(fingerprint)
            for tasks in plan_payload.values() if isinstance(tasks, list)
            for task in tasks if isinstance(task, list) and task
            for fingerprint in (
                (task[0].get("_Planner", {}) or {}).get("processed_crafts", [])
                if isinstance(task[0], dict) else []
            )
            if fingerprint
        )
    craft_rows = _craft_events_with_ship_context(events)
    fingerprints_by_ship: dict[str, list[str]] = defaultdict(list)
    for craft_event in craft_rows:
        craft_ship_id = str(craft_event.get("_ResolvedShipID") or "")
        fingerprints_by_ship[craft_ship_id].append(
            engineer_craft_fingerprint(craft_event, craft_ship_id)
        )
    by_ship_id = {
        str(row.get("id")): str(row.get("label") or "")
        for row in (fleet_state.get("ships") or []) if row.get("id") is not None
    }
    migrate_legacy_plan_baselines(
        plan_path, plan_payload, craft_rows, acknowledged, by_ship_id
    )

    # A missing cursor is not evidence that historical Journal crafts were
    # processed. Replay them through the exact matcher; only successfully
    # applied fingerprints may enter the acknowledgement set.
    if not cursor.get("initialized"):
        cursor["initialized"] = True

    applied = []
    unresolved = []
    preferred = str(preferred_plan_id or "")
    for event in craft_rows:
        ship_id = str(event.get("_ResolvedShipID") or "")
        fingerprint = engineer_craft_fingerprint(event, ship_id)
        if fingerprint in acknowledged:
            continue
        ship = by_ship_id.get(ship_id, "")
        if not ship:
            unresolved.append(craft_issue_row(
                fingerprint, ship_id, event,
                f"No fleet ship label for ShipID {ship_id or 'unknown'}.",
                reason_code="BINDING",
            ))
            continue
        clean_event = {
            key: value for key, value in event.items() if key != "_ResolvedShipID"
        }
        ship_fingerprints = fingerprints_by_ship.get(ship_id, [])
        eligible_plan_ids = _eligible_plan_ids_after_baseline(
            plan_payload, ship, ship_id, event, ship_fingerprints,
        )
        if not eligible_plan_ids:
            legacy_history = is_unconfirmed_legacy_history(
                plan_payload, ship, event, ship_fingerprints
            )
            safe_history = is_craft_before_safe_plan_baseline(
                plan_payload, ship, event, ship_fingerprints
            )
            if legacy_history or (safe_history and cursor_was_initialized):
                unresolved.append(craft_issue_row(
                    fingerprint, ship_id, event,
                    "Historical craft predates the confirmed plan boundary; "
                    "Commander confirmation is required.",
                    ship, historical=True, reason_code="HISTORICAL",
                ))
                continue
            if not cursor_was_initialized:
                # A safe baseline proves this event is pre-plan history.
                acknowledged.add(fingerprint)
                continue
        result = apply_engineer_craft(
            plan_path, ship, clean_event, preferred, ship_id,
            eligible_plan_ids=eligible_plan_ids,
        )
        if result.get("status") == "applied":
            acknowledged.add(fingerprint)
            applied.append({
                "fingerprint": fingerprint,
                "ship": ship,
                "event": clean_event,
                "result": result,
            })
            preferred = ""
        else:
            unresolved.append(craft_issue_row(
                fingerprint, ship_id, event,
                str(result.get("reason") or result.get("status") or "unmatched"),
                ship, reason_code=(
                    "NO PLAN" if str(result.get("status") or "") == "unmatched"
                    else str(result.get("status") or "UNMATCHED")
                ),
            ))

    cursor.update({
        "acknowledged": sorted(acknowledged)[-10000:],
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _write_json_if_changed(cursor_path, cursor)
    diagnostics_path = data_dir / "craft_batch_diagnostics.json"
    diagnostics = read_json(diagnostics_path, [])
    diagnostics = diagnostics if isinstance(diagnostics, list) else []
    known = {str(row.get("fingerprint") or "") for row in diagnostics if isinstance(row, dict)}
    additions = [row for row in unresolved if row["fingerprint"] not in known]
    if additions:
        _write_json_if_changed(diagnostics_path, (diagnostics + additions)[-100:])
    return {
        "applied": applied,
        "unresolved": unresolved,
        "preferredPlanApplied": bool(preferred_plan_id and not preferred),
    }


def profiled_journal_events() -> list[dict[str, Any]]:
    """Read only events belonging to the selected LoadGame identity."""
    revision, _events = _journal_snapshot()
    selected, _name = _journal_profile_identity()
    if not selected:
        return []
    cache_key = (revision, selected)
    with _JOURNAL_EVENT_CACHE_LOCK:
        cached = _JOURNAL_EVENT_CACHE["profile_views"].get(cache_key)
        if cached is not None:
            return list(cached)
        events: list[dict[str, Any]] = []
        for name in sorted(_JOURNAL_EVENT_CACHE["files"]):
            file_events = _JOURNAL_EVENT_CACHE["files"][name]["events"]
            first_load = next(
                (
                    event for event in file_events
                    if event.get("event") == "LoadGame"
                ),
                {},
            )
            session_identity = str(
                first_load.get("FID") or first_load.get("Commander") or ""
            ).strip()
            for event in file_events:
                if event.get("event") == "LoadGame":
                    session_identity = str(
                        event.get("FID") or event.get("Commander") or ""
                    ).strip()
                if session_identity == selected:
                    events.append(event)
        _JOURNAL_EVENT_CACHE["profile_views"][cache_key] = list(events)
    return events


def latest_profile_location(
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the newest exact location for the selected Journal profile."""
    source = events if events is not None else profiled_journal_events()
    event = next(
        (
            row for row in reversed(source)
            if isinstance(row, dict)
            and row.get("event") in {"Location", "FSDJump", "CarrierJump"}
            and str(row.get("StarSystem") or "").strip()
            and isinstance(row.get("StarPos"), (list, tuple))
            and len(row.get("StarPos")) == 3
        ),
        {},
    )
    if not event:
        return {}
    return {
        "system": str(event.get("StarSystem") or "").strip(),
        "currentPosition": [float(value) for value in event["StarPos"]],
        "currentSystemAddress": event.get("SystemAddress"),
        "timestamp": str(event.get("timestamp") or ""),
    }


LOGBOOK_LIMIT = 2500
LOGBOOK_NOTE_LIMIT = 500
LOGBOOK_FILTERS = (
    "ALL", "TRAVEL", "DOCKING", "ENGINEERING", "MATERIALS",
    "STATION SERVICES", "PROGRESS", "SESSION",
)
LOGBOOK_CATEGORIES = {
    "FSDJump": "TRAVEL", "CarrierJump": "TRAVEL",
    "Docked": "DOCKING", "Undocked": "DOCKING",
    "EngineerCraft": "ENGINEERING",
    "MaterialTrade": "MATERIALS", "MaterialCollected": "MATERIALS",
    "Market": "STATION SERVICES", "Shipyard": "STATION SERVICES",
    "Outfitting": "STATION SERVICES",
    "EngineerContribution": "PROGRESS", "EngineerProgress": "PROGRESS",
    "Rank": "PROGRESS", "Promotion": "PROGRESS",
    "LoadGame": "SESSION", "Shutdown": "SESSION",
}


def _logbook_material_is_interesting(
    event: dict[str, Any], metadata: dict[str, dict[str, Any]],
) -> bool:
    info = metadata.get(normalize(event.get("Name")), {})
    if not info:
        return True
    return int(info.get("Grade", 0) or 0) >= 4 or not bool(
        info.get("Tradeable", True)
    )


def _logbook_entry(
    event: dict[str, Any], index: int, context: dict[str, str],
    metadata: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    name = str(event.get("event") or "")
    category = LOGBOOK_CATEGORIES.get(name)
    if not category:
        return None
    if name == "MaterialCollected" and not _logbook_material_is_interesting(
        event, metadata
    ):
        return None
    timestamp = str(event.get("timestamp") or "")
    system = str(event.get("StarSystem") or context.get("system") or "")
    station = str(event.get("StationName") or context.get("station") or "")
    ship = str(
        event.get("ShipName") or event.get("Ship") or context.get("ship") or ""
    )
    title = name
    summary = ""
    details: dict[str, Any] = {}
    if name in {"FSDJump", "CarrierJump"}:
        title = "Carrier jump" if name == "CarrierJump" else "Hyperspace jump"
        distance = event.get("JumpDist")
        summary = system + (f" · {float(distance):.2f} ly" if distance is not None else "")
        details = {"System": system, "Distance": f"{distance} ly" if distance is not None else ""}
    elif name in {"Docked", "Undocked"}:
        title = "Docked" if name == "Docked" else "Undocked"
        summary = " · ".join(value for value in (station, system) if value)
        details = {"System": system, "Station": station}
    elif name == "EngineerCraft":
        blueprint = str(
            event.get("BlueprintName_Localised") or event.get("BlueprintName")
            or "Engineering modification"
        )
        grade = int(event.get("Level", 0) or 0)
        effect = str(
            event.get("ExperimentalEffect_Localised")
            or event.get("ExperimentalEffect") or ""
        )
        title = blueprint
        summary = f"Grade {grade}" + (f" · {effect}" if effect else "")
        details = {"Blueprint": blueprint, "Grade": grade, "Experimental": effect}
    elif name == "MaterialTrade":
        paid, received = event.get("Paid") or {}, event.get("Received") or {}
        paid_name = str(paid.get("Material_Localised") or paid.get("Material") or "material")
        received_name = str(received.get("Material_Localised") or received.get("Material") or "material")
        title = "Material trade"
        summary = f"{paid.get('Quantity', '?')} {paid_name} → {received.get('Quantity', '?')} {received_name}"
        details = {"Given": f"{paid.get('Quantity', '?')} {paid_name}", "Received": f"{received.get('Quantity', '?')} {received_name}"}
    elif name == "MaterialCollected":
        material = str(event.get("Name_Localised") or event.get("Name") or "material")
        info = metadata.get(normalize(event.get("Name")), {})
        grade = int(info.get("Grade", 0) or 0)
        title = material
        summary = f"Collected ×{event.get('Count', 1)}" + (f" · Grade {grade}" if grade else "")
        details = {"Material": material, "Amount": event.get("Count", 1), "Grade": grade}
    elif name in {"Market", "Shipyard", "Outfitting"}:
        title = {"Market": "Commodity market", "Shipyard": "Shipyard", "Outfitting": "Outfitting"}[name]
        summary = " · ".join(value for value in (station, system) if value)
        details = {"System": system, "Station": station, "Market ID": event.get("MarketID", "")}
    elif name == "EngineerContribution":
        engineer = str(event.get("Engineer") or event.get("EngineerName") or "Engineer")
        material = str(event.get("Material_Localised") or event.get("Material") or event.get("Commodity") or "contribution")
        title = f"Engineer contribution · {engineer}"
        summary = f"{material} ×{event.get('Quantity', 0)}"
        details = {"Engineer": engineer, "Contribution": summary}
    elif name == "EngineerProgress":
        records = event.get("Engineers") if isinstance(event.get("Engineers"), list) else [event]
        first = records[0] if records and isinstance(records[0], dict) else {}
        engineer = str(first.get("Engineer") or first.get("EngineerName") or "Engineer progress")
        progress = str(first.get("Progress") or "updated")
        title, summary = engineer, progress
        details = {"Engineer": engineer, "Progress": progress, "Rank": first.get("Rank", "")}
    elif name in {"Rank", "Promotion"}:
        ranks = {
            key: value for key, value in event.items()
            if key not in {"timestamp", "event"} and isinstance(value, (int, str))
        }
        title = "Commander promotion" if name == "Promotion" else "Commander ranks"
        summary = " · ".join(f"{key} {value}" for key, value in ranks.items())
        details = ranks
    elif name == "LoadGame":
        title = "Session started"
        summary = " · ".join(value for value in (ship, system) if value) or "Commander loaded"
        details = {"Ship": ship, "System": system, "Game mode": event.get("GameMode", "")}
    elif name == "Shutdown":
        title, summary = "Session ended", system
        details = {"Last system": system, "Ship": ship}
    details = {str(key): value for key, value in details.items() if value not in (None, "")}
    search_text = " ".join(str(value) for value in (
        name, category, title, summary, system, station, ship, *details.values()
    ) if value).casefold()
    return {
        "id": f"{timestamp}|{name}|{index}", "timestamp": timestamp,
        "date": timestamp[:10], "time": timestamp[11:19],
        "event": name, "category": category, "title": title,
        "summary": summary, "system": system, "station": station,
        "ship": ship, "details": details, "searchText": search_text,
    }


def logbook_entries(package_root: Path) -> list[dict[str, Any]]:
    """Return a bounded, incrementally derived Logbook for the active profile."""
    revision, _all_events = _journal_snapshot()
    selected, _commander = _journal_profile_identity()
    if not selected:
        return []
    events = profiled_journal_events()
    metadata = material_metadata(reference_data_dir(package_root))
    with _JOURNAL_EVENT_CACHE_LOCK:
        cached = dict(_JOURNAL_EVENT_CACHE["logbook_views"].get(selected, {}))
        last_rebuild_revision = int(
            _JOURNAL_EVENT_CACHE.get("last_rebuild_revision", 0)
        )
    processed = int(cached.get("processed", 0) or 0)
    last_key = cached.get("last_key")
    append_only = (
        0 <= processed <= len(events)
        and int(cached.get("revision", 0) or 0) >= last_rebuild_revision
        and (
            processed == 0
            or last_key == (
                str(events[processed - 1].get("timestamp") or ""),
                str(events[processed - 1].get("event") or ""),
            )
        )
    )
    if append_only:
        rows = list(cached.get("rows", []))
        context = dict(cached.get("context", {}))
        start = processed
    else:
        rows, context, start = [], {}, 0
    for index in range(start, len(events)):
        event = events[index]
        name = str(event.get("event") or "")
        if event.get("StarSystem"):
            context["system"] = str(event["StarSystem"])
        if event.get("StationName"):
            context["station"] = str(event["StationName"])
        if event.get("ShipName") or event.get("Ship"):
            context["ship"] = str(event.get("ShipName") or event.get("Ship"))
        row = _logbook_entry(event, index, context, metadata)
        if row:
            rows.append(row)
            rows = rows[-LOGBOOK_LIMIT:]
        if name == "Undocked":
            context["station"] = ""
    last_key = (
        (str(events[-1].get("timestamp") or ""), str(events[-1].get("event") or ""))
        if events else None
    )
    with _JOURNAL_EVENT_CACHE_LOCK:
        _JOURNAL_EVENT_CACHE["logbook_views"][selected] = {
            "revision": revision, "processed": len(events), "last_key": last_key,
            "rows": rows, "context": context,
        }
    return list(reversed(rows))


def filter_logbook_entries(
    rows: list[dict[str, Any]], category: str = "ALL", query: str = "",
) -> list[dict[str, Any]]:
    """Filter normalized Logbook rows without touching Journal data."""
    category = str(category or "ALL").upper()
    query = str(query or "").strip().casefold()
    return [
        row for row in rows
        if (category == "ALL" or row.get("category") == category)
        and (not query or query in str(row.get("searchText") or ""))
    ]


def load_logbook_notes(data_dir: Path) -> dict[str, str]:
    """Load bounded Commander notes from the active profile directory."""
    payload = read_json(data_dir / "logbook_notes.json", {})
    if not isinstance(payload, dict):
        return {}
    return {
        entry_id: note.strip()[:LOGBOOK_NOTE_LIMIT]
        for entry_id, note in payload.items()
        if isinstance(entry_id, str) and entry_id.strip()
        and isinstance(note, str) and note.strip()
    }


def write_logbook_note(
    data_dir: Path, entry_id: str, note: str,
) -> dict[str, str]:
    """Atomically create, update or remove one profile-isolated note."""
    entry_id = str(entry_id or "").strip()
    notes = load_logbook_notes(data_dir)
    if not entry_id:
        return notes
    normalized = str(note or "").strip()[:LOGBOOK_NOTE_LIMIT]
    if normalized:
        notes[entry_id] = normalized
    else:
        notes.pop(entry_id, None)
    _write_json_if_changed(data_dir / "logbook_notes.json", notes)
    return notes


def load_session_history(data_dir: Path) -> list[dict[str, Any]]:
    """Load the bounded completed-session history for one profile."""
    return normalize_session_history(
        read_json(data_dir / "session_history.json", [])
    )


def session_statistics(data_dir: Path) -> dict[str, Any]:
    """Incrementally derive and persist sessions for the active profile."""
    revision, _events = _journal_snapshot()
    selected, _name = _journal_profile_identity()
    if not selected:
        return {"current": {}, "recent": []}
    events = profiled_journal_events()
    with _JOURNAL_EVENT_CACHE_LOCK:
        cached = dict(_JOURNAL_EVENT_CACHE["session_views"].get(selected, {}))
        last_rebuild_revision = int(
            _JOURNAL_EVENT_CACHE.get("last_rebuild_revision", 0)
        )
    processed = int(cached.get("processed", 0) or 0)
    last_key = cached.get("last_key")
    append_only = (
        0 <= processed <= len(events)
        and int(cached.get("revision", 0) or 0) >= last_rebuild_revision
        and (
            processed == 0
            or last_key == (
                str(events[processed - 1].get("timestamp") or ""),
                str(events[processed - 1].get("event") or ""),
            )
        )
    )
    if append_only and cached:
        history = list(cached.get("history", []))
        current = deepcopy(cached.get("current"))
        if current and isinstance(current.get("_systems"), list):
            current["_systems"] = set(current["_systems"])
        start = processed
    else:
        history = list(reversed(load_session_history(data_dir)))
        current, start = None, 0
    for index in range(start, len(events)):
        current = apply_session_event(current, history, events[index], index)
    recent = list(reversed(history[-SESSION_HISTORY_LIMIT:]))
    _write_json_if_changed(data_dir / "session_history.json", recent)
    cache_current = deepcopy(current)
    if cache_current and isinstance(cache_current.get("_systems"), set):
        cache_current["_systems"] = sorted(cache_current["_systems"])
    last_key = (
        (str(events[-1].get("timestamp") or ""), str(events[-1].get("event") or ""))
        if events else None
    )
    with _JOURNAL_EVENT_CACHE_LOCK:
        _JOURNAL_EVENT_CACHE["session_views"][selected] = {
            "revision": revision, "processed": len(events), "last_key": last_key,
            "history": history, "current": cache_current,
        }
    return {
        "current": public_session(
            current, datetime.now(timezone.utc).isoformat()
        ) if current else {},
        "recent": recent,
    }


def current_cargo_event() -> dict[str, Any]:
    """Read Elite's authoritative current cargo snapshot, if available."""
    try:
        event = json.loads(
            (journal_dir() / "Cargo.json").read_text(
                encoding="utf-8-sig", errors="replace"
            )
        )
    except (OSError, TypeError, ValueError):
        return {}
    if (
        isinstance(event, dict)
        and event.get("event") == "Cargo"
        and isinstance(event.get("Inventory"), list)
    ):
        return event
    return {}


def journal_events(
    events: list[dict[str, Any]] | None = None,
    include_current_cargo: bool = False,
) -> list[dict[str, Any]]:
    events = events if events is not None else profiled_journal_events()
    snapshot = next(
        (index for index in range(len(events) - 1, -1, -1)
         if events[index].get("event") == "Materials"),
        -1,
    )
    selected = list(events[max(0, snapshot):])
    if include_current_cargo:
        cargo_event = current_cargo_event()
        if cargo_event:
            selected.append(cargo_event)
    return selected


_UNLOCK_EVENT_CACHE = {"signature": None, "events": []}


def journal_unlock_events(
    profiled_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return cached career-wide evidence needed by Engineer unlock chains."""
    try:
        revision, _events = _journal_snapshot()
        signature: list[object] = [
            str(journal_dir()), active_profile_key(), revision,
        ]
        cargo_path = journal_dir() / "Cargo.json"
        if cargo_path.is_file():
            cargo_stat = cargo_path.stat()
            signature.append(
                (cargo_path.name, cargo_stat.st_size, cargo_stat.st_mtime_ns)
            )
        signature = tuple(signature)
    except OSError:
        return []
    if _UNLOCK_EVENT_CACHE["signature"] == signature:
        return list(_UNLOCK_EVENT_CACHE["events"])
    watched = {
        "Rank", "Reputation", "Statistics", "Loadout", "Cargo",
        "EngineerContribution", "EngineerProgress", "Docked", "Location",
        "FSDJump", "CarrierJump", "MissionAccepted", "MissionCompleted",
        "MissionFailed", "MissionAbandoned",
    }
    events = [
        event for event in (
            profiled_events
            if profiled_events is not None else profiled_journal_events()
        )
        if event.get("event") in watched
    ]
    cargo_event = current_cargo_event()
    if cargo_event:
        events.append(cargo_event)
    _UNLOCK_EVENT_CACHE.update({
        "signature": signature,
        "events": events,
    })
    return list(events)


def inventory_from_events(
    events: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
    consistency_issues: list[str] | None = None,
    cargo_materials: set[str] | None = None,
) -> dict[str, int]:
    inventory = defaultdict(int)
    snapshot_index = next(
        (index for index, event in enumerate(events)
         if event.get("event") == "Materials"),
        -1,
    )
    if snapshot_index >= 0:
        snapshot = events[snapshot_index]
        for category in ("Raw", "Manufactured", "Encoded"):
            for item in snapshot.get(category, []) or []:
                key = normalize(journal_material_name(item))
                if key:
                    inventory[key] = max(0, int(item.get("Count", 0) or 0))
                    if key not in metadata:
                        message = (
                            f"Unknown material {key} in Materials/{category}: "
                            f"{item!r}."
                        )
                        LOGGER.warning(message)
                        if consistency_issues is not None:
                            consistency_issues.append(message)
    for event in events[snapshot_index + 1:]:
        for name, _category, delta in material_event_changes(event):
            key = normalize(name)
            if key not in metadata:
                message = (
                    f"Unknown material {key} in {event.get('event')}: "
                    f"{event!r}."
                )
                LOGGER.warning(message)
                if consistency_issues is not None:
                    consistency_issues.append(message)
            result = inventory[key] + delta
            if result < 0 and consistency_issues is not None:
                consistency_issues.append(
                    f"Material underflow after {event.get('event')}: "
                    f"{key} {inventory[key]} {delta:+d}."
                )
            inventory[key] = max(0, result)
    cargo_snapshot = next(
        (
            event for event in reversed(events)
            if event.get("event") == "Cargo"
            and isinstance(event.get("Inventory"), list)
        ),
        None,
    )
    if cargo_snapshot:
        for item in cargo_snapshot.get("Inventory", []) or []:
            if not isinstance(item, dict):
                continue
            key = normalize(item.get("Name") or item.get("Name_Localised"))
            # Cargo includes limpets, trade goods and mission freight. Only
            # canonical Engineering/Tech Broker recipe items belong in the
            # shared material inventory; unknown Materials events above stay
            # visible for forward-compatibility diagnostics.
            if key and (
                cargo_materials is None
                or key in metadata or key in cargo_materials
            ):
                inventory[key] = max(0, int(item.get("Count", 0) or 0))
    for key, value in list(inventory.items()):
        if key in metadata:
            cap = metadata[key].get("MaxCapacity")
            if cap is None:
                inventory[key] = max(0, value)
                if metadata[key].get("Category") in MATERIAL_CATEGORIES:
                    message = f"Known material {key} has no resolved capacity."
                    LOGGER.warning(message)
                    if consistency_issues is not None:
                        consistency_issues.append(message)
            else:
                inventory[key] = min(int(cap), max(0, value))
        else:
            inventory[key] = max(0, value)
    return dict(inventory)


def journal_change_summary(event, metadata):
    """Explain a Journal material movement in one readable sentence."""
    if not isinstance(event, dict):
        return ""
    names = []
    for name, _category, delta in material_event_changes(event):
        key = normalize(name)
        label = str(metadata.get(key, {}).get("Name") or name)
        names.append(f"{delta:+d} {label}")
    event_name = str(event.get("event") or "")
    if event_name == "EngineerCraft":
        blueprint = str(
            event.get("BlueprintName_Localised")
            or event.get("BlueprintName") or "engineering modification"
        )
        grade = int(event.get("Level", 0) or 0)
        effect = str(
            event.get("ExperimentalEffect_Localised")
            or event.get("ExperimentalEffect") or ""
        )
        return (
            f"Crafted {blueprint}"
            + (f" G{grade}" if grade else "")
            + (f" · {effect}" if effect else "")
            + (f" · inventory {'; '.join(names)}" if names else "")
        )
    labels = {
        "MaterialTrade": "Material trade",
        "MaterialCollected": "Collected",
        "MaterialDiscarded": "Discarded",
        "Synthesis": "Synthesis",
    }
    prefix = labels.get(event_name, event_name)
    return f"{prefix} · {'; '.join(names)}" if names else ""


ENGINEER_NAME_ALIASES = {
    "Tod 'The Blaster' McQuinn": "Tod McQuinn",
    'Tod "The Blaster" McQuinn': "Tod McQuinn",
}


def engineer_progress_from_events(events):
    """Merge both Journal EngineerProgress payload shapes chronologically."""
    progress = {}
    for event in events or []:
        if event.get("event") != "EngineerProgress":
            continue
        records = event.get("Engineers")
        if not isinstance(records, list):
            records = [event]
        for record in records:
            if not isinstance(record, dict):
                continue
            raw_name = str(
                record.get("Engineer") or record.get("EngineerName") or ""
            ).strip()
            name = ENGINEER_NAME_ALIASES.get(raw_name, raw_name)
            if not name:
                continue
            previous = progress.get(name, {})
            progress[name] = {
                "progress": str(
                    record.get("Progress")
                    or previous.get("progress") or "Unknown"
                ),
                "rank": int(
                    record.get("Rank", previous.get("rank", 0)) or 0
                ),
                "rankProgress": int(
                    record.get(
                        "RankProgress", previous.get("rankProgress", 0)
                    ) or 0
                ),
            }
    return progress


def update_trader_type_evidence(
    cache: TraderTypeCache,
    profile_events: list[dict[str, Any]],
    spansh_rows: list[dict[str, Any]],
    spansh_timestamp: str = "",
) -> bool:
    """Merge complete profile evidence and the persisted Spansh snapshot."""
    changed = False
    external_updated_at = normalize_timestamp(spansh_timestamp)
    for row in spansh_rows:
        evidence = spansh_trader_type_evidence(row, spansh_timestamp or None)
        if evidence and cache.update(evidence, now=external_updated_at):
            changed = True
    # Trader identity is career evidence.  Unlike inventory, it must not be
    # truncated at the latest Materials snapshot.
    for event in profile_events:
        evidence = trader_type_evidence_from_event(event)
        if evidence and cache.update(evidence):
            changed = True
    return changed


def set_tech_broker_track(path: Path, name: str, broker_subtype: str) -> bool:
    """Persist exactly one profile-isolated Tech Broker material priority."""
    name = str(name or "").strip()
    broker_subtype = str(broker_subtype or "").strip().upper()
    document = {
        "name": name,
        "brokerSubtype": broker_subtype,
    } if name else {}
    current = read_json(path, {})
    if current == document:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(document, indent=2))
    return True


COMMANDER_RANK_CATEGORIES = (
    ("Combat", "COMBAT", "combat.svg", 13),
    ("Trade", "TRADE", "trade.svg", 13),
    ("Explore", "EXPLORATION", "exploration.svg", 13),
    ("CQC", "CQC", "cqc.svg", 8),
    ("Federation", "FEDERATION", "federation.svg", 14),
    ("Empire", "EMPIRE", "empire.svg", 14),
    ("Soldier", "MERCENARY", "soldier.svg", 13),
    ("Exobiologist", "EXOBIOLOGY", "exobiology.svg", 13),
)
COMMANDER_REPUTATIONS = (
    ("Federation", "FEDERATION"),
    ("Empire", "EMPIRE"),
    ("Alliance", "ALLIANCE"),
    ("Independent", "INDEPENDENT"),
)


def commander_journal_overview(events):
    """Build a display-only Commander snapshot from existing Journal events."""
    ranks = {key: {"rank": None, "progress": None} for key, *_rest in COMMANDER_RANK_CATEGORIES}
    reputations = {key: None for key, _label in COMMANDER_REPUTATIONS}
    credits = {"value": None, "timestamp": "", "basis": "SESSION START"}
    assets = {"value": None, "timestamp": "", "basis": "LAST JOURNAL UPDATE"}
    relevant_timestamps = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        timestamp = str(event.get("timestamp") or "")
        if name == "Rank":
            for key in ranks:
                value = event.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    ranks[key]["rank"] = max(0, int(value))
            relevant_timestamps.append(timestamp)
        elif name == "Progress":
            for key in ranks:
                value = event.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    ranks[key]["progress"] = max(0, min(100, int(value)))
            relevant_timestamps.append(timestamp)
        elif name == "Reputation":
            for key in reputations:
                value = event.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    reputations[key] = max(-100.0, min(100.0, float(value)))
            relevant_timestamps.append(timestamp)
        elif name == "LoadGame":
            value = event.get("Credits")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                credits.update({"value": max(0, int(value)), "timestamp": timestamp})
                relevant_timestamps.append(timestamp)
        elif name == "Statistics":
            bank = event.get("Bank_Account")
            bank = bank if isinstance(bank, dict) else {}
            value = bank.get("Current_Wealth")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                assets.update({"value": max(0, int(value)), "timestamp": timestamp})
                relevant_timestamps.append(timestamp)
    rank_rows = []
    for key, label, icon, maximum in COMMANDER_RANK_CATEGORIES:
        rank = ranks[key]["rank"]
        progress = ranks[key]["progress"]
        rank_rows.append({
            "key": key, "label": label, "icon": f"assets/cmdr/{icon}",
            "known": rank is not None,
            "rank": int(rank) if rank is not None else -1,
            "progressKnown": progress is not None,
            "progress": int(progress) if progress is not None else 0,
            "maxRank": maximum,
        })
    reputation_rows = [{
        "key": key, "label": label,
        "known": reputations[key] is not None,
        "value": float(reputations[key] or 0.0),
    } for key, label in COMMANDER_REPUTATIONS]
    return {
        "ranks": rank_rows,
        "reputations": reputation_rows,
        "credits": {**credits, "known": credits["value"] is not None,
                    "value": int(credits["value"] or 0)},
        "assets": {**assets, "known": assets["value"] is not None,
                   "value": int(assets["value"] or 0)},
        "lastUpdated": max((value for value in relevant_timestamps if value), default=""),
    }


def powerplay_journal_overview(events):
    """Build one honest Powerplay snapshot exclusively from Journal events."""
    membership = {"power": "", "rank": None, "merits": None,
                  "timePledged": None, "timePledgedObservedAt": "",
                  "timestamp": ""}
    location = {}
    salary = {}
    cargo_rows = []
    current_system = ""
    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        timestamp = str(event.get("timestamp") or "")
        if name == "LoadGame":
            # A new session must prove the current pledge again. Otherwise a
            # historic Powerplay event could incorrectly survive after leaving.
            membership = {"power": "", "rank": None, "merits": None,
                          "timePledged": None, "timePledgedObservedAt": "",
                          "timestamp": timestamp}
        elif name in {"Location", "FSDJump", "CarrierJump"}:
            current_system = str(event.get("StarSystem") or current_system)
            location = {
                "system": current_system,
                "controllingPower": str(event.get("ControllingPower") or ""),
                "powers": [str(value) for value in event.get("Powers", []) or []],
                "state": str(event.get("PowerplayState") or ""),
                "controlProgressKnown": isinstance(
                    event.get("PowerplayStateControlProgress"), (int, float)
                ) and not isinstance(event.get("PowerplayStateControlProgress"), bool),
                "controlProgress": max(0.0, min(
                    1.0, float(event.get("PowerplayStateControlProgress", 0) or 0)
                )),
                "reinforcementKnown": "PowerplayStateReinforcement" in event,
                "reinforcement": max(0, int(
                    event.get("PowerplayStateReinforcement", 0) or 0
                )),
                "underminingKnown": "PowerplayStateUndermining" in event,
                "undermining": max(0, int(
                    event.get("PowerplayStateUndermining", 0) or 0
                )),
                "timestamp": timestamp,
            }
        elif name == "Powerplay" and str(event.get("Power") or "").strip():
            membership.update({
                "power": str(event.get("Power") or ""),
                "rank": int(event["Rank"]) if isinstance(
                    event.get("Rank"), (int, float)
                ) and not isinstance(event.get("Rank"), bool) else membership["rank"],
                "merits": int(event["Merits"]) if isinstance(
                    event.get("Merits"), (int, float)
                ) and not isinstance(event.get("Merits"), bool) else membership["merits"],
                "timePledged": int(event["TimePledged"]) if isinstance(
                    event.get("TimePledged"), (int, float)
                ) and not isinstance(event.get("TimePledged"), bool) else membership["timePledged"],
                "timePledgedObservedAt": timestamp if isinstance(
                    event.get("TimePledged"), (int, float)
                ) and not isinstance(event.get("TimePledged"), bool)
                else membership["timePledgedObservedAt"],
                "timestamp": timestamp,
            })
        elif name == "PowerplayRank" and str(event.get("Power") or "").strip():
            membership["power"] = str(event.get("Power") or "")
            if isinstance(event.get("Rank"), (int, float)) \
                    and not isinstance(event.get("Rank"), bool):
                membership["rank"] = max(0, int(event["Rank"]))
            membership["timestamp"] = timestamp
        elif name == "PowerplayMerits" and str(event.get("Power") or "").strip():
            membership["power"] = str(event.get("Power") or "")
            if isinstance(event.get("TotalMerits"), (int, float)) \
                    and not isinstance(event.get("TotalMerits"), bool):
                membership["merits"] = max(0, int(event["TotalMerits"]))
            membership["timestamp"] = timestamp
        elif name == "PowerplaySalary" and isinstance(
            event.get("Amount"), (int, float)
        ) and not isinstance(event.get("Amount"), bool):
            salary = {
                "amount": max(0, int(event["Amount"])),
                "timestamp": timestamp,
            }
        elif name in {"PowerplayDeliver", "PowerplayCollect"}:
            item_type = str(
                event.get("Type_Localised") or event.get("Type") or ""
            ).strip()
            count = event.get("Count")
            if item_type and isinstance(count, (int, float)) \
                    and not isinstance(count, bool):
                cargo_rows.append({
                    "direction": "DELIVER" if name == "PowerplayDeliver" else "COLLECT",
                    "type": item_type,
                    "count": max(0, int(count)),
                    "system": current_system,
                    "timestamp": timestamp,
                })
    reinforcement = int(location.get("reinforcement", 0) or 0)
    undermining = int(location.get("undermining", 0) or 0)
    location["tugKnown"] = bool(
        location.get("reinforcementKnown")
        and location.get("underminingKnown")
        and (reinforcement > 0 or undermining > 0)
    )
    return {
        "pledged": bool(membership["power"]),
        "power": membership["power"],
        "rankKnown": membership["rank"] is not None,
        "rank": int(membership["rank"] or 0),
        "meritsKnown": membership["merits"] is not None,
        "merits": int(membership["merits"] or 0),
        "timePledgedKnown": membership["timePledged"] is not None,
        "timePledgedSeconds": max(0, int(membership["timePledged"] or 0)),
        "timePledgedObservedAt": membership["timePledgedObservedAt"],
        "location": location,
        "salaryKnown": bool(salary),
        "salary": salary,
        "cargoHistory": list(reversed(cargo_rows[-10:])),
        "lastUpdated": max(
            str(membership.get("timestamp") or ""),
            str(location.get("timestamp") or ""),
            str(salary.get("timestamp") or ""),
            max((str(row.get("timestamp") or "") for row in cargo_rows), default=""),
        ),
    }


def build_state(
    package_root, selected_ship="", preferred_plan_id="",
    trader_preference="confirmed",
):
    data_dir = runtime_data_dir(package_root)
    profile_identity, commander_name = _journal_profile_identity()
    journal_path_valid = journal_dir().is_dir()
    metadata = material_metadata(reference_data_dir(package_root))
    profile_events = profiled_journal_events()
    commander_overview = commander_journal_overview(profile_events)
    powerplay_overview = powerplay_journal_overview(profile_events)
    learn_blueprint_id_catalog(
        profile_events, data_dir / "blueprint_id_catalog_learned.json"
    )
    sessions = session_statistics(data_dir)
    events = journal_events(profile_events, include_current_cargo=True)
    unlock_events = journal_unlock_events(profile_events)
    consistency_issues = []
    consistency_issues.extend(
        str(message) for message in read_json(
            data_dir / "blueprint_diagnostics.json", []
        ) if message
    )
    ship_events = ship_journal_events(profile_events)
    latest_ship = max(
        ship_events, key=lambda event: str(event.get("timestamp") or ""),
        default={},
    )
    fleet_state = rebuild_fleet(ship_events)
    craft_batch = reconcile_engineer_craft_batch(
        data_dir, fleet_state, profile_events, preferred_plan_id
    )
    active_ship = next(
        (str(row["label"]) for row in fleet_state.get("ships", [])
         if str(row["id"]) == str(fleet_state.get("active_id") or "")), "",
    )
    ship, tasks, ships = current_ship(
        data_dir, fleet_state, selected_ship, ship_events
    )
    selected_ship_id = next(
        (str(row["id"]) for row in fleet_state.get("ships", [])
         if row.get("label") == ship), "",
    )
    selected_ship_type = next(
        (str(row.get("type") or "") for row in fleet_state.get("ships", [])
         if row.get("label") == ship), "",
    )
    # Physical module state starts with fleet/loadout events but must also see
    # later EngineerCraft events, which ship_journal_events deliberately omits.
    module_slots = latest_loadout_slots(profile_events, selected_ship_id)
    engineering_slots = engineering_loadout_rows(
        module_slots, blueprint_catalog(reference_data_dir(package_root)),
    )
    ship_catalog = read_json(package_root / "ed_data" / "ships.json", [])
    selected_ship_data = next(
        (
            row for row in (ship_catalog if isinstance(ship_catalog, list) else [])
            if isinstance(row, dict) and normalize(
                row.get("symbol") or ""
            ) == normalize(selected_ship_type)
            or isinstance(row, dict) and normalize(
                row.get("name") or ""
            ) == normalize(selected_ship_type)
        ),
        {},
    )
    engineering_ship_slots = ship_slot_layout(
        selected_ship_data, module_slots,
        blueprint_catalog(reference_data_dir(package_root)),
        read_json(data_dir / "desired_outfitting.json", {}).get(
            selected_ship_id, {}
        ),
    )
    pending_plans_by_slot = {}
    for task in tasks or []:
        if not isinstance(task, list) or not task:
            continue
        first = next((row for row in task if isinstance(row, dict)), {})
        planner = first.get("_Planner", {}) if isinstance(first, dict) else {}
        slot = str(planner.get("slot") or "")
        if not slot or wishlist_target_status(planner)["code"] == "completed":
            continue
        pending_plans_by_slot[slot] = {
            "planPending": True,
            "planTargetGrade": int(planner.get("target_grade") or 0),
            "planBlueprint": str(first.get("Name") or ""),
            "planExperimental": str(planner.get("experimental_name") or ""),
        }
    for row in engineering_ship_slots:
        row.update(pending_plans_by_slot.get(str(row.get("slot") or ""), {}))
    selected_loadout = next(
        (
            event for event in reversed(ship_events)
            if event.get("event") == "Loadout"
            and str(event.get("ShipID") or event.get("_ResolvedShipID") or "")
            == selected_ship_id
        ),
        {},
    )
    selected_ship_stats = {
        "jumpRange": selected_loadout.get("MaxJumpRange"),
        "unladenMass": selected_loadout.get("UnladenMass"),
        "cargoCapacity": selected_loadout.get("CargoCapacity"),
        "fuelCapacity": (
            selected_loadout.get("FuelCapacity", {}).get("Main")
            if isinstance(selected_loadout.get("FuelCapacity"), dict)
            else selected_loadout.get("FuelCapacity")
        ),
    }
    wishlist_required = required_materials(tasks, metadata, consistency_issues)
    unlock_catalog = load_unlock_catalog(data_dir, package_root)
    unlock_signals = engineer_unlock_signals(unlock_events, unlock_catalog)
    inventory = inventory_from_events(
        events, metadata, consistency_issues,
        canonical_cargo_materials(reference_data_dir(package_root)),
    )
    tech_broker_catalog = read_json(
        data_dir / "tech_broker_catalog_user.json", {}
    )
    tech_broker_guide = technology_broker_unlock_guide(
        package_root, metadata, inventory, profile_events,
        tech_broker_catalog.get("stations", [])
        if isinstance(tech_broker_catalog, dict) else [],
    )
    for guide_row in tech_broker_guide:
        for item in guide_row.get("materials", []) or []:
            key = str(item.get("key") or "")
            if not key or key in metadata:
                continue
            metadata[key] = {
                "Name": str(item.get("name") or key),
                "Category": str(item.get("category") or "Commodity"),
                "Rarity": "",
                "Grade": 0,
                "MaxCapacity": None,
                "TraderGroup": "",
                "Tradeable": False,
                "Origins": [str(item.get("origin") or "Commodity source")],
                "ExactSources": [],
                "UsedIn": [],
            }
            metadata[key]["Guidance"] = actionable_source_card(
                key, metadata[key]
            )
    tech_track = read_json(data_dir / "tech_broker_track.json", {})
    tracked_name = str(
        tech_track.get("name") or ""
    ) if isinstance(tech_track, dict) else ""
    tracked_guide_row = next(
        (row for row in tech_broker_guide if row.get("name") == tracked_name),
        None,
    )
    if tracked_name and tracked_guide_row \
            and tracked_guide_row.get("status") == "unlocked":
        set_tech_broker_track(data_dir / "tech_broker_track.json", "", "")
        tech_track = {}
        tracked_name = ""
    tracked_row = next(
        (
            row for row in tech_broker_guide
            if row.get("name") == tracked_name
            and row.get("status") != "unlocked"
        ),
        None,
    )
    for row in tech_broker_guide:
        row["isTracked"] = bool(tracked_row and row is tracked_row)
    tech_track_requirement = {
        str(item.get("key") or ""): max(0, int(item.get("need", 0) or 0))
        for item in (tracked_row.get("materials", []) if tracked_row else [])
        if item.get("key") and int(item.get("need", 0) or 0) > 0
    }
    required = dict(wishlist_required)
    for key, amount in tech_track_requirement.items():
        required[key] = required.get(key, 0) + amount
    tech_reserved = (
        reserve_material_pool([tech_track_requirement], inventory, [True])[0]
        if tech_track_requirement else {}
    )
    wishlist_inventory = {
        key: max(0, int(amount or 0) - int(tech_reserved.get(key, 0) or 0))
        for key, amount in inventory.items()
    }
    missing = {}
    for key, amount in required.items():
        if key not in metadata:
            missing[key] = amount
            continue
        have = inventory[key] if key in inventory else 0
        missing[key] = max(0, amount - have)
    missing = {key: amount for key, amount in missing.items() if amount}
    unresolved_required = sorted(key for key in required if key not in metadata)
    covered = sum(
        min(amount, inventory[key])
        for key, amount in required.items()
        if key in metadata and key in inventory
    )
    total = sum(required.values())
    trades = plan_material_trades(missing, required, inventory, metadata)
    latest_location = next(
        (event for event in reversed(events)
         if event.get("event") in {"Location", "FSDJump", "CarrierJump"}),
        {},
    )
    position = latest_location.get("StarPos")
    trader_catalog = read_json(
        reference_data_dir(package_root) / "material_trader_catalog.json", {}
    )
    base_stations = (
        trader_catalog.get("stations", [])
        if isinstance(trader_catalog, dict) else []
    )
    user_catalog = read_json(
        Path(
            os.environ.get("LOCALAPPDATA")
            or (Path.home() / "AppData" / "Local")
        ) / "EDEngineeringCompanion" / "material_trader_catalog_user.json",
        {},
    )
    stations = merge_trader_catalog(
        base_stations,
        user_catalog.get("stations", [])
        if isinstance(user_catalog, dict) else [],
    )
    type_cache = TraderTypeCache().load()
    cache_changed = update_trader_type_evidence(
        type_cache,
        profile_events,
        user_catalog.get("stations", [])
        if isinstance(user_catalog, dict) else [],
        str(user_catalog.get("fetched_at") or "")
        if isinstance(user_catalog, dict) else "",
    )
    if cache_changed:
        type_cache.save()
    resolved_stations = []
    for station in stations:
        market_id = station.get("market_id")
        try:
            market_id = int(market_id)
        except (TypeError, ValueError):
            continue
        resolution = resolve_trader_type(market_id, type_cache)
        trader_type = resolution.trader_type
        confidence = resolution.confidence
        source = resolution.source or ""
        # Catalog stations already carry Spansh category. Use it when the
        # local evidence cache has no live type yet (or only a stale one),
        # so Manufactured/Raw/Encoded trades are not dropped as unresolved.
        if not trader_type:
            catalog_type = str(station.get("category") or "").strip().title()
            if catalog_type in {"Raw", "Manufactured", "Encoded"}:
                trader_type = catalog_type
                confidence = "external"
                source = str(station.get("source") or "Local trader catalog")
            else:
                continue
        resolved = dict(station)
        resolved.update({
            "traderType": trader_type,
            "traderConfidence": confidence,
            "traderSource": source,
            "traderWarning": (
                HEURISTIC_TRADER_WARNING
                if confidence == "heuristic" else ""
            ),
        })
        resolved_stations.append(resolved)
    trader_by_category = {
        category: find_nearest_catalog_trader(
            category, position, resolved_stations, trader_preference
        )
        if isinstance(position, list) and len(position) == 3 else None
        for category in ("Raw", "Manufactured", "Encoded")
    }
    active_trader_locations = {
        category: trader
        for category, trader in trader_by_category.items()
        if trader
    }
    material_rows = []
    for key in sorted(set(metadata) | set(inventory) | set(required)):
        info = metadata.get(key)
        known = bool(
            info
            and info.get("Category") in MATERIAL_CATEGORIES
            and info.get("MaxCapacity") is not None
        )
        info = info or {
            "Name": f"Unbekanntes Material: {key}",
            "Category": "unknown", "Grade": 0, "Rarity": "Unknown",
            "Tradeable": False, "Origins": [], "UsedIn": [],
        }
        have = int(inventory[key] if key in inventory else 0)
        need = int(required.get(key, 0) or 0)
        capacity = int(info["MaxCapacity"]) if known else 0
        surplus = max(0, have - need)
        status = (
            "missing" if have < need
            else "ready" if need > 0
            else "surplus" if surplus > 0
            else "empty"
        )
        material_source_cards = source_cards(key, info, position)
        farm_source = next(
            (
                card for card in material_source_cards
                if card.get("role") == "PRIMARY"
                and card.get("verified") and card.get("system")
                and card.get("coordinates")
            ),
            next(
                (
                    card for card in material_source_cards
                if card.get("verified") and card.get("system")
                and card.get("coordinates")
                ),
                next(
                    (card for card in material_source_cards if card.get("system")),
                    {},
                ),
            ),
        )
        material_rows.append({
            "key": key,
            "name": str(info.get("Name") or key),
            "category": str(info.get("Category") or "unknown"),
            "grade": int(info.get("Grade", 0) or 0),
            "rarity": str(info.get("Rarity") or "Unknown"),
            "have": have,
            "need": need,
            "missing": max(0, need - have),
            "protected": need,
            "surplus": surplus,
            "status": status,
            "tradeable": is_material_tradeable(info),
            "capacity": capacity,
            "capacityKnown": known,
            "unknownMaterial": not known,
            "warning": (
                f"Unbekanntes Material: {key} – Kontakt Support/Update Referenzdaten"
                if not known else ""
            ),
            "capacityProgress": min(1.0, have / capacity) if capacity else 0.0,
            "needProgress": min(1.0, have / need) if need else 0.0,
            "origins": list(info.get("Origins", [])),
            "rawAvailability": list(info.get("RawAvailability", [])),
            "rawTraderCategory": int(info.get("RawTraderCategory", 0) or 0),
            "sourceCards": material_source_cards,
            "farmSource": farm_source,
            "tradeOptions": material_trade_options(
                key, required, inventory, metadata
            ),
            "trader": trader_by_category.get(
                str(info.get("Category") or "")
            ) or {},
            "usedIn": list(info.get("UsedIn", [])),
        })
    material_rows.sort(
        key=lambda row: (
            row["missing"] <= 0,
            -row["missing"],
            row["category"],
            row["name"].casefold(),
        )
    )
    latest_trade = next(
        (event for event in reversed(events) if event.get("event") == "MaterialTrade"),
        {},
    )
    latest_craft = next(
        (event for event in reversed(events) if is_completed_engineer_craft(event)),
        {},
    )
    latest_change_event = next(
        (
            event for event in reversed(events)
            if event.get("event") in {
                "MaterialTrade", "MaterialCollected", "MaterialDiscarded",
                "Synthesis", "EngineerCraft",
            }
            and (
                event.get("event") != "EngineerCraft"
                or is_completed_engineer_craft(event)
            )
        ),
        {},
    )
    recent_crafts = []
    for event in reversed(events):
        if not is_completed_engineer_craft(event):
            continue
        recent_crafts.append({
            "timestamp": str(event.get("timestamp") or ""),
            "blueprint": str(
                event.get("BlueprintName_Localised")
                or event.get("BlueprintName") or "Engineering modification"
            ),
            "grade": int(event.get("Level", 0) or 0),
            "experimental": str(
                event.get("ExperimentalEffect_Localised")
                or event.get("ExperimentalEffect") or ""
            ),
            "summary": journal_change_summary(event, metadata),
            "completed": True,
        })
        if len(recent_crafts) >= 10:
            break
    engineer_progress = engineer_progress_from_events(unlock_events)
    cards = []
    validated_trades = []
    for trade in trades:
        category = str(trade.get("category") or "").title()
        trader = trader_by_category.get(category) or {}
        if not trade_matches_trader(trade, trader, metadata):
            consistency_issues.append(
                f"Rejected {category or 'unknown'} trade at "
                f"{trader.get('station') or 'unresolved trader'}: "
                "trader and material categories do not match."
            )
            continue
        validated_trades.append(trade)
        give_name = metadata.get(
            trade["source"], {}
        ).get("Name", trade["source"])
        receive_name = metadata.get(
            trade["target"], {}
        ).get("Name", trade["target"])
        cards.append({
            "id": f"{trade['source']}->{trade['target']}",
            "targetKey": trade["target"],
            "category": category.upper(),
            "giveName": give_name,
            "giveAmount": int(trade.get("source_spent", 0) or 0),
            "receiveName": receive_name,
            "receiveAmount": int(trade.get("target_received", 0) or 0),
            "remaining": int(trade.get("remaining", 0) or 0),
            "instruction": (
                f"WANTED · {int(trade.get('target_received', 0) or 0)} "
                f"{receive_name} · GIVE · "
                f"{int(trade.get('source_spent', 0) or 0)} {give_name}"
            ),
            "system": str(trader.get("system") or ""),
            "station": str(trader.get("station") or ""),
            "traderConfidence": str(trader.get("traderConfidence") or "unknown"),
            "traderSource": str(trader.get("traderSource") or ""),
            "traderWarning": str(trader.get("traderWarning") or ""),
            # Every card is rebuilt from the post-Journal inventory and
            # therefore represents work that is still open. Completed trades
            # belong exclusively to tradeHistory; carrying their historical
            # confirmation onto a new deficit creates a contradictory state.
            "status": "open",
        })
    route = build_trader_route(
        validated_trades, position, locations=active_trader_locations
    )
    trade_history = []
    for event in reversed(events):
        if event.get("event") != "MaterialTrade":
            continue
        changes = material_event_changes(event)
        paid = next(
            ((normalize(name), -delta) for name, _category, delta in changes
             if delta < 0),
            ("", 0),
        )
        received = next(
            ((normalize(name), delta) for name, _category, delta in changes
             if delta > 0),
            ("", 0),
        )
        if not paid[0] or not received[0]:
            continue
        trade_history.append({
            "timestamp": str(event.get("timestamp") or ""),
            "giveName": metadata.get(paid[0], {}).get("Name", paid[0]),
            "giveAmount": int(paid[1]),
            "receiveName": metadata.get(
                received[0], {}
            ).get("Name", received[0]),
            "receiveAmount": int(received[1]),
            "summary": (
                f"Traded {paid[1]} "
                f"{metadata.get(paid[0], {}).get('Name', paid[0])} for "
                f"{received[1]} "
                f"{metadata.get(received[0], {}).get('Name', received[0])}"
            ),
        })
        if len(trade_history) >= 20:
            break
    source_events = {
        "ship": {
            "event": str(latest_ship.get("event") or ""),
            "timestamp": str(latest_ship.get("timestamp") or ""),
            "shipId": latest_ship.get("ShipID") or latest_ship.get("NewShipID"),
        },
        "material": {
            "event": str(latest_change_event.get("event") or ""),
            "timestamp": str(latest_change_event.get("timestamp") or ""),
        },
        "craft": {
            "event": "EngineerCraft" if latest_craft else "",
            "timestamp": str(latest_craft.get("timestamp") or ""),
            "completed": bool(latest_craft),
        },
    }
    if any(int(row.get("have", 0) or 0) < 0 for row in material_rows):
        consistency_issues.append("A material inventory became negative.")

    blueprint_state = blueprint_rows(tasks, wishlist_inventory, metadata)
    tracked_items = [
        {
            "kind": "WISHLIST",
            "id": str(row.get("planId") or ""),
            "title": f"{row.get('module', 'Module')} · {row.get('blueprint', 'Blueprint')}",
            "subtitle": f"{ship} · G{int(row.get('targetGrade', 0) or 0)}",
            "status": str(row.get("targetStatusText") or "PENDING"),
            "missingKinds": sum(
                int(item.get("missing", 0) or 0) > 0
                for item in (
                    row.get("experimentalMaterialProgress")
                    if row.get("targetStatus") == "experimental_pending"
                    else row.get("materialProgress")
                ) or []
            ),
            "brokerSubtype": "",
        }
        for row in blueprint_state
        if row.get("priority") and row.get("targetStatus") != "completed"
    ]
    if tracked_row:
        tracked_items.append({
            "kind": "TECH BROKER",
            "id": str(tracked_row.get("name") or ""),
            "title": str(tracked_row.get("name") or "Tech Broker unlock"),
            "subtitle": str(tracked_row.get("brokerSubtype") or "TECH BROKER"),
            "status": str(tracked_row.get("statusText") or "PENDING"),
            "missingKinds": int(tracked_row.get("missingKinds", 0) or 0),
            "brokerSubtype": str(tracked_row.get("brokerSubtype") or ""),
        })
    material_status = material_status_label(len(missing), covered)
    selected_craft_issues = craft_tracking_issues_for_ship(
        craft_batch.get("unresolved"), selected_ship_id
    )
    classified_craft_issues = classify_craft_tracking_issues(
        selected_craft_issues, blueprint_state
    )

    return {
        "_craftBatch": craft_batch,
        "ship": ship or "No ship selected",
        "commander": commander_name,
        "commanderKnown": bool(profile_identity),
        "commanderOverview": commander_overview,
        "powerplayOverview": powerplay_overview,
        "fleetKnown": bool(ships),
        "journalPathValid": journal_path_valid,
        "emptyStateReason": (
            "No Journal directory found. Configure the path or start Elite Dangerous."
            if not journal_path_valid else
            "No Commander detected yet. Waiting for a LoadGame event."
            if not profile_identity else
            "Commander detected. Waiting for a ShipID-bearing fleet event."
            if not ships else ""
        ),
        "ships": ships,
        "activeShip": active_ship,
        "activeShipId": str(fleet_state.get("active_id") or ""),
        "activeShipKnown": active_ship in ships,
        "selectedShipId": selected_ship_id,
        "selectedShipType": selected_ship_type,
        "selectedShipStats": selected_ship_stats,
        "moduleSlots": module_slots,
        "engineeringModuleSlots": engineering_slots,
        "engineeringShipSlots": engineering_ship_slots,
        "system": latest_location.get("StarSystem") or "Unknown system",
        "currentPosition": position or [],
        "techBrokerTrack": dict(tracked_row) if tracked_row else {},
        "trackedItems": tracked_items,
        "localHgeSightings": extract_local_hge_sightings(events),
        "localStateFinds": extract_local_state_finds(events),
        "localHgeScan": local_hge_scan_status(events),
        "localStateFindScan": local_state_find_scan_status(events),
        "currentSession": sessions["current"],
        "recentSessions": sessions["recent"],
        "required": total,
        "covered": covered,
        "completion": material_completion(
            covered, total, reliable=not unresolved_required
        ),
        "completionReliable": not unresolved_required,
        "materialStatus": material_status,
        "planProgressStatus": aggregate_plan_progress(blueprint_state),
        "craftTrackingIssues": classified_craft_issues,
        "freshCraftTrackingIssues": [
            row for row in classified_craft_issues if not row.get("historical")
        ],
        "relevantCraftTrackingIssues": [
            row for row in classified_craft_issues if row.get("relevant")
        ],
        "unrelatedCraftTrackingIssues": [
            row for row in classified_craft_issues
            if not row.get("historical") and not row.get("relevant")
        ],
        "historicalCraftTrackingIssues": [
            row for row in classified_craft_issues if row.get("historical")
        ],
        "calculationWarning": (
            "Materialbedarf unvollständig berechenbar – unbekanntes Material: "
            + ", ".join(unresolved_required)
            if unresolved_required else ""
        ),
        "missingKinds": len(missing),
        "trades": cards,
        "traderRoute": route.get("stops", []),
        "traderPreference": trader_preference,
        "routeDistance": float(route.get("total_distance_ly", 0.0) or 0.0),
        "tradeHistory": trade_history,
        "blueprints": blueprint_state,
        "materials": material_rows,
        "nextAction": (
            "Waiting for Commander Journal data"
            if not profile_identity or not ships else
            f"Track Tech Broker unlock · {tracked_row.get('name')}"
            if tracked_row else
            f"Complete {len(cards)} material trade{'s' if len(cards) != 1 else ''}"
            if cards else
            ("Collect missing materials" if missing else "Open Engineering")
        ),
        "latestTrade": latest_trade,
        "recentCrafts": recent_crafts,
        "stateSourceEvents": source_events,
        "consistencyIssues": list(dict.fromkeys(consistency_issues)),
        "lastChangeReason": journal_change_summary(
            latest_change_event, metadata
        ),
        "engineerProgress": engineer_progress,
        "engineerUnlockSignals": unlock_signals,
        "techBrokerGuide": tech_broker_guide,
    }
