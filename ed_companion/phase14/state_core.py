"""Extracted from ed_companion/phase14/state.py as part of the state.py
modularization refactor (no behavior change). Re-exported by state.py so
every existing import path keeps working unchanged."""

import json
import hashlib
import logging
import math
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from functools import lru_cache
from collections import defaultdict
from dataclasses import dataclass
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
    project_vehicle_state,
    project_latest_srv_mining_session,
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
from ed_companion.navigation.mining_finder import project_local_mining_evidence
from ed_companion.navigation.trader_type_cache import normalize_timestamp
from ed_companion.trader_config import HEURISTIC_TRADER_WARNING_KEY
from ed_companion.material_integrity import material_key
from ed_companion.module_identity import (
    canonical_module_id,
    module_identity_key,
    same_module_identity,
)
from ed_companion.persistence import (
    atomic_write, load_json_file, migrate_app_dir_if_needed, persistence_issues,
)
from ed_companion.build_import import (
    JOURNAL_BLUEPRINT_NAMES,
    JOURNAL_EXPERIMENTAL_NAMES,
)
from ed_companion.exobiology import (
    augmented_species_catalog,
    best_find,
    exobiology_carried_summary,
    exobiology_findings,
    exobiology_lifetime_earned,
    exobiology_session_summary,
    exobiology_summary,
    genus_completion,
    landing_targets,
    remaining_signals_at_body,
)


LOGGER = logging.getLogger(__name__)

PROGRESS_STATUS = ("NOT STARTED", "IN PROGRESS", "COMPLETE")


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



def blueprint_module_family(event: dict[str, Any]) -> str:
    """Resolve the engineering family proven by an EngineerCraft module ID."""
    symbol = module_identity_key(event.get("Module"))
    if not symbol:
        return ""
    if symbol.startswith("shiparmour"):
        return "armour"
    for family, prefixes in ENGINEERING_MODULE_ID_PREFIXES.items():
        if any(symbol.startswith(normalize(prefix)) for prefix in prefixes):
            return family
    # Preserve an unknown Frontier symbol as its own conservative scope.  Do
    # not merge it with another family by guessing at suffix structure.
    return symbol



def load_blueprint_id_catalog(
    path: Path = BLUEPRINT_ID_CATALOG_PATH,
    learned_path: Path | None = None,
) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Load immutable bundled IDs plus one profile-isolated learned overlay."""
    catalog: dict[tuple[str, int, str], dict[str, Any]] = {}
    for source_path in (path, learned_path):
        if source_path is None:
            continue
        records = read_json(source_path, [])
        seen: set[tuple[str, int, str]] = set()
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            key = (
                str(record.get("blueprint_name") or ""),
                int(record.get("level", 0) or 0),
                str(record.get("module_family") or ""),
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
                    LOGGER.info(
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
    evidence: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    raw_ids: dict[tuple[str, int, str, str], Any] = {}
    for event in events or []:
        if not isinstance(event, dict) or not is_completed_engineer_craft(event):
            continue
        name = str(event.get("BlueprintName") or "").strip()
        level = int(event.get("Level", 0) or 0)
        blueprint_id = event.get("BlueprintID")
        if not name or level <= 0 or blueprint_id in (None, ""):
            continue
        family = blueprint_module_family(event)
        if not family:
            continue
        key = (name, level, family)
        identity = str(blueprint_id)
        evidence[key].add(identity)
        raw_ids[(name, level, family, identity)] = blueprint_id

    learned = conflicts = ambiguous = 0
    additions = []
    for key, ids in sorted(evidence.items()):
        if len(ids) != 1:
            ambiguous += 1
            LOGGER.info(
                "Ambiguous Journal BlueprintID evidence ignored: %s / G%s / %s",
                key[0], key[1], sorted(ids),
            )
            continue
        identity = next(iter(ids))
        known = bundled.get(key) or existing.get(key)
        if known:
            if str(known["blueprint_id"]) != identity:
                conflicts += 1
                LOGGER.info(
                    "BlueprintID catalog contradiction; Journal wins at runtime, "
                    "catalog unchanged: %s / G%s / catalog=%s / journal=%s",
                    key[0], key[1], known["blueprint_id"], identity,
                )
            continue
        additions.append({
            "blueprint_name": key[0], "level": key[1],
            "module_family": key[2],
            "blueprint_id": raw_ids[(key[0], key[1], key[2], identity)],
            "source": "journal_confirmed",
        })
        learned += 1
    if additions:
        merged = existing_records + additions
        merged.sort(key=lambda row: (
            str(row.get("blueprint_name") or "").casefold(),
            int(row.get("level", 0) or 0),
            str(row.get("module_family") or ""),
        ))
        _write_json_if_changed(learned_path, merged)
    return {"learned": learned, "conflicts": conflicts, "ambiguous": ambiguous}



def blueprint_id_evidence(
    event: dict[str, Any],
    catalog: dict[tuple[str, int, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify Journal BlueprintID evidence without mutating the catalog."""
    name = str(event.get("BlueprintName") or "")
    level = int(event.get("Level", 0) or 0)
    journal_id = event.get("BlueprintID")
    family = blueprint_module_family(event)
    entries = catalog or load_blueprint_id_catalog()
    entry = entries.get((name, level, family))
    if entry is None:
        entry = entries.get((name, level, ""))
    if entry is None:
        LOGGER.info(
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
        LOGGER.info(
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



APP_DATA_DIR_NAME = "ED-Frame"
LEGACY_APP_DATA_DIR_NAME = "EDEngineeringCompanion"


def app_data_dir() -> Path:
    """Return the writable application root, never the installation tree."""
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    )
    root = local_app_data / APP_DATA_DIR_NAME
    # migrate_app_dir_if_needed() short-circuits on a single Path.exists()
    # check once the new root is present, so calling it unconditionally
    # here - rather than gating it behind a do-once flag - costs nothing
    # on the hot path while staying correct if LOCALAPPDATA ever changes
    # within one process (as tests do between cases).
    migrate_app_dir_if_needed(
        local_app_data / LEGACY_APP_DATA_DIR_NAME, root, log=LOGGER.info,
    )
    root.mkdir(parents=True, exist_ok=True)
    return root



_JOURNAL_EVENT_CACHE: dict[str, Any] = {
    "root": "", "revision": 0, "files": {}, "profile_views": {},
    "logbook_views": {}, "session_views": {}, "last_rebuild_revision": 0,
    "events": [], "identity_views": {}, "loadout_views": {},
}


_JOURNAL_EVENT_CACHE_LOCK = threading.RLock()


_CRAFT_BATCH_LOCK = threading.RLock()


_JOURNAL_POLL_FILE_LIMIT = 32


_JOURNAL_GLOB_TTL_SECONDS = 5.0


_JOURNAL_GLOB_CACHE_LOCK = threading.Lock()


_JOURNAL_GLOB_CACHE: dict[str, Any] = {"root": None, "at": 0.0, "names": ()}



def clear_journal_event_cache() -> None:
    """Invalidate parsed Journal data after an explicit source change."""
    with _JOURNAL_EVENT_CACHE_LOCK:
        _JOURNAL_EVENT_CACHE.update({
            "root": "", "revision": 0, "files": {}, "profile_views": {},
            "logbook_views": {}, "session_views": {},
            "last_rebuild_revision": 0, "events": [], "identity_views": {},
            "loadout_views": {},
        })
    with _JOURNAL_GLOB_CACHE_LOCK:
        _JOURNAL_GLOB_CACHE.update({"root": None, "at": 0.0, "names": ()})



def _recent_journal_names(root: Path) -> tuple[str, ...]:
    """Names of the most recent Journal files, re-globbed at most once every
    few seconds so the 1.2 s poller does not enumerate the directory each tick.
    A newly rotated Journal file therefore enters the signature within the TTL;
    growth of existing files is still seen every tick by the caller's stat().
    """
    key = str(root)
    now = time.monotonic()
    with _JOURNAL_GLOB_CACHE_LOCK:
        if (
            _JOURNAL_GLOB_CACHE["root"] == key
            and now - _JOURNAL_GLOB_CACHE["at"] < _JOURNAL_GLOB_TTL_SECONDS
        ):
            return _JOURNAL_GLOB_CACHE["names"]
    try:
        names = tuple(
            path.name for path in sorted(
                root.glob("Journal.*.log"), key=lambda path: path.name,
            )[-_JOURNAL_POLL_FILE_LIMIT:]
        )
    except OSError:
        names = ()
    with _JOURNAL_GLOB_CACHE_LOCK:
        _JOURNAL_GLOB_CACHE.update({"root": key, "at": now, "names": names})
    return names



def journal_change_signature() -> tuple[str, tuple[tuple[str, int, int], ...]]:
    """Return metadata for the most recent Journal files without reading them."""
    root = journal_dir()
    files = []
    for name in _recent_journal_names(root):
        try:
            stat = (root / name).stat()
        except OSError:
            continue
        files.append((name, int(stat.st_size), int(stat.st_mtime_ns)))
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



def read_journal_tail_records(
    path: Path, offset: int,
) -> tuple[int, list[tuple[int, int, dict[str, Any] | None]]]:
    """Read complete lines with offsets, retaining an incomplete trailing line."""
    records = []
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
                event = None
            records.append((line_start, committed, event if isinstance(event, dict) else None))
    return committed, records



def read_journal_tail(
    path: Path, offset: int, existing: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    """Append complete JSON lines and retain an incomplete trailing line."""
    committed, records = read_journal_tail_records(path, offset)
    events = list(existing)
    events.extend(event for _start, _end, event in records if event is not None)
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
                "last_rebuild_revision": 0, "events": [],
                "identity_views": {}, "loadout_views": {},
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
                offset, events = read_journal_tail(path, offset, existing)
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
            _JOURNAL_EVENT_CACHE["identity_views"] = {}
            if not append_only:
                _JOURNAL_EVENT_CACHE["last_rebuild_revision"] = int(
                    _JOURNAL_EVENT_CACHE["revision"]
                )
                _JOURNAL_EVENT_CACHE["loadout_views"] = {}
            # Flatten the immutable per-file event lists once per Journal
            # revision. A state build asks for the same snapshot repeatedly;
            # rebuilding a 100k+ item list each time creates avoidable GIL and
            # allocation pressure.
            _JOURNAL_EVENT_CACHE["events"] = [
                event for path in paths
                for event in cached_files.get(path.name, {}).get("events", [])
            ]
        events = _JOURNAL_EVENT_CACHE.get("events", [])
        return int(_JOURNAL_EVENT_CACHE["revision"]), events



def _fast_journal_profile_identity(requested: str = "") -> tuple[str, str]:
    """Resolve startup identity from LoadGame lines without parsing history."""
    try:
        paths = sorted(
            journal_dir().glob("Journal.*.log"), key=lambda path: path.name,
            reverse=True,
        )
    except OSError:
        paths = []
    for path in paths:
        candidates = []
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                for line in handle:
                    if "LoadGame" not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(event, dict) or event.get("event") != "LoadGame":
                        continue
                    identity = str(
                        event.get("FID") or event.get("Commander") or ""
                    ).strip()
                    if identity and (not requested or identity == requested):
                        candidates.append((
                            str(event.get("timestamp") or ""), identity,
                            str(event.get("Commander") or "Commander"),
                        ))
        except OSError:
            continue
        latest = max(candidates, default=None)
        if latest:
            return latest[1], latest[2]
    return (requested, "") if requested else ("", "")



def _journal_profile_identity() -> tuple[str, str]:
    """Return the selected Frontier identity and display name from LoadGame."""
    requested = str(os.environ.get("ED_FRAME_PROFILE_FID") or "").strip()
    root = str(journal_dir().resolve())
    with _JOURNAL_EVENT_CACHE_LOCK:
        snapshot_ready = (
            _JOURNAL_EVENT_CACHE.get("root") == root
            and bool(_JOURNAL_EVENT_CACHE.get("files"))
        )
    if not snapshot_ready:
        # The window only needs the active profile namespace at startup. The
        # complete Journal snapshot is built by the existing background state
        # worker, so avoid parsing every historical event on the GUI thread.
        return _fast_journal_profile_identity(requested)
    revision, events = _journal_snapshot()
    cache_key = (revision, requested)
    with _JOURNAL_EVENT_CACHE_LOCK:
        cached = _JOURNAL_EVENT_CACHE["identity_views"].get(cache_key)
        if cached is not None:
            return cached
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
            result = (match[1], match[2])
            with _JOURNAL_EVENT_CACHE_LOCK:
                _JOURNAL_EVENT_CACHE["identity_views"][cache_key] = result
            return result
        # Keep an explicitly requested but currently absent FID in its own
        # deterministic namespace. No Journal events match it, so it cannot
        # silently fall back to another Commander's files.
        result = (requested, "")
        with _JOURNAL_EVENT_CACHE_LOCK:
            _JOURNAL_EVENT_CACHE["identity_views"][cache_key] = result
        return result
    latest = max(candidates, default=None)
    result = (latest[1], latest[2]) if latest else ("", "")
    with _JOURNAL_EVENT_CACHE_LOCK:
        _JOURNAL_EVENT_CACHE["identity_views"][cache_key] = result
    return result



@dataclass(frozen=True)
class ProfileContext:
    identity: str
    key: str
    directory: Path
    journal_root: str



def resolve_profile_context() -> ProfileContext:
    """Resolve identity, key and paths once for one coherent operation."""
    identity, _name = _journal_profile_identity()
    key = (
        hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        if identity else "unidentified"
    )
    directory = app_data_dir() / f"profile-{key}"
    directory.mkdir(parents=True, exist_ok=True)
    return ProfileContext(
        identity=identity,
        key=key,
        directory=directory,
        journal_root=str(journal_dir().resolve()),
    )



def active_profile_key() -> str:
    return resolve_profile_context().key



def active_profile_identity() -> tuple[str, str]:
    """Expose the selected Journal identity without leaking it into filenames."""
    return _journal_profile_identity()



def runtime_data_dir(context: ProfileContext) -> Path:
    """Return the directory of an already resolved profile context."""
    if not isinstance(context, ProfileContext):
        raise TypeError("runtime_data_dir requires a ProfileContext")
    context.directory.mkdir(parents=True, exist_ok=True)
    return context.directory



def user_trader_catalog_path(context: ProfileContext) -> Path:
    """Return the sole writable trader catalog path for one profile."""
    return runtime_data_dir(context) / "material_trader_catalog_user.json"



def load_user_trader_catalog(context: ProfileContext) -> dict[str, Any]:
    """Load the profile catalog, claiming the legacy global copy at most once."""
    canonical = user_trader_catalog_path(context)
    if canonical.is_file():
        loaded = read_json(canonical, {})
        return loaded if isinstance(loaded, dict) else {}

    legacy = app_data_dir() / "material_trader_catalog_user.json"
    migration_marker = app_data_dir() / "material_trader_catalog_user.migrated.json"
    owner = read_json(migration_marker, {})
    claimed_key = str(owner.get("profile_key") or "") \
        if isinstance(owner, dict) else ""
    if legacy.is_file() and (not claimed_key or claimed_key == context.key):
        loaded = read_json(legacy, {})
        if isinstance(loaded, dict):
            atomic_write(canonical, json.dumps(loaded, indent=2))
            atomic_write(migration_marker, json.dumps({
                "profile_key": context.key,
                "source": legacy.name,
            }, indent=2))
            return loaded
    return {}



def reference_data_dir(package_root: Path) -> Path:
    """Return immutable reference data shipped with this exact app release.

    A legacy installer copied the complete ``ed_data`` directory into
    LOCALAPPDATA.  That directory also contains writable commander data, so it
    cannot simply be deleted, but its old material and blueprint catalogs must
    never override the version-coherent catalogs bundled with a newer release.
    """
    return Path(package_root) / "ed_data"



def journal_dir() -> Path:
    configured = str(os.environ.get("ED_FRAME_JOURNAL_DIR") or "").strip()
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
    try:
        saved = atomic_write(app_data_dir() / "journal_path.txt", str(value))
    except OSError as exc:
        LOGGER.error("Journal directory configuration save failed: %s", exc)
        return False
    if not saved:
        LOGGER.error("Journal directory configuration could not be persisted")
        return False
    clear_journal_event_cache()
    return True



def read_json(path, default):
    path = Path(path)
    try:
        path.resolve().relative_to(app_data_dir().resolve())
    except ValueError:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            return default
    return load_json_file(path, default)



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
            "Location", "FSDJump", "CarrierJump",
            "ModuleBuy", "ModuleRetrieve", "ModuleSell", "ModuleStore",
            "ModuleSwap",
        }
    ]



def _write_json_if_changed(path: Path, payload: object) -> None:
    if read_json(path, None) == payload:
        return
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))



def profiled_journal_events(start_file: str = "") -> list[dict[str, Any]]:
    """Read selected-profile events, optionally from a cached file boundary."""
    revision, _events = _journal_snapshot()
    selected, _name = _journal_profile_identity()
    if not selected:
        return []
    cache_key = (revision, selected)
    with _JOURNAL_EVENT_CACHE_LOCK:
        cached = _JOURNAL_EVENT_CACHE["profile_views"].get(cache_key)
        if not start_file and cached is not None:
            return list(cached)
        events: list[dict[str, Any]] = []
        names = sorted(_JOURNAL_EVENT_CACHE["files"])
        if start_file in names:
            names = names[names.index(start_file):]
        for name in names:
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
        if not start_file:
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

