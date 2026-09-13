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
from ed_companion.persistence import atomic_write, load_json_file, persistence_issues
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
from .state_core import (
    BLUEPRINT_ID_CATALOG_PATH,
    ENGINEERING_CATEGORY_ORDER,
    ENGINEERING_MODULE_CATEGORIES,
    ENGINEERING_MODULE_ID_PREFIXES,
    ENGINEER_NAME_ALIASES,
    MATERIAL_CATEGORIES,
    PROGRESS_STATUS,
    ProfileContext,
    _CRAFT_BATCH_LOCK,
    _JOURNAL_EVENT_CACHE,
    _JOURNAL_EVENT_CACHE_LOCK,
    _JOURNAL_GLOB_CACHE,
    _JOURNAL_GLOB_CACHE_LOCK,
    _JOURNAL_GLOB_TTL_SECONDS,
    _JOURNAL_POLL_FILE_LIMIT,
    _UNLOCK_EVENT_CACHE,
    _fast_journal_profile_identity,
    _journal_guard,
    _journal_profile_identity,
    _journal_snapshot,
    _recent_journal_names,
    _write_json_if_changed,
    active_profile_identity,
    active_profile_key,
    app_data_dir,
    blueprint_id_evidence,
    blueprint_module_family,
    clear_journal_event_cache,
    current_cargo_event,
    engineer_progress_from_events,
    engineering_module_category,
    inventory_from_events,
    journal_change_signature,
    journal_change_summary,
    journal_dir,
    journal_events,
    journal_paths_for_profile,
    journal_unlock_events,
    latest_profile_location,
    learn_blueprint_id_catalog,
    load_blueprint_id_catalog,
    load_user_trader_catalog,
    normalize,
    profiled_journal_events,
    read_journal_tail,
    read_journal_tail_records,
    read_json,
    real_engineers,
    reference_data_dir,
    resolve_profile_context,
    runtime_data_dir,
    set_journal_dir,
    set_tech_broker_track,
    ship_journal_events,
    update_trader_type_evidence,
    user_trader_catalog_path,
)

LOGGER = logging.getLogger(__name__)
from .state_materials import material_metadata



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

