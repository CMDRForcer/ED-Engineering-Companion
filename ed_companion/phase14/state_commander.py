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
from .state_fleet import _cached_profile_loadout_slots_by_ship, module_matches_type
from .state_engineering_operations import annotate_installed_target_conflicts



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



def commander_status_credits(status):
    """Return the live credit balance written by Elite's Status.json."""
    status = status if isinstance(status, dict) else {}
    value = status.get("Balance")
    known = isinstance(value, (int, float)) and not isinstance(value, bool)
    return {
        "value": max(0, int(value)) if known else 0,
        "known": known,
        "timestamp": str(status.get("timestamp") or ""),
        "basis": "LIVE STATUS",
    }



def commander_journal_overview(events, status=None):
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
    live_credits = commander_status_credits(status)
    journal_time = normalize_timestamp(credits.get("timestamp"))
    status_time = normalize_timestamp(live_credits.get("timestamp"))
    if live_credits["known"] and (
        journal_time is None or status_time is None or status_time >= journal_time
    ):
        credits = live_credits
        relevant_timestamps.append(live_credits["timestamp"])
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



def merge_capi_commander_overview(overview, capi_profile):
    """Use a newer CAPI balance without replacing newer local truth."""
    result = deepcopy(overview) if isinstance(overview, dict) else {}
    capi_profile = capi_profile if isinstance(capi_profile, dict) else {}
    remote = capi_profile.get("credits")
    remote = remote if isinstance(remote, dict) else {}
    local = result.get("credits")
    local = local if isinstance(local, dict) else {}
    local_time = normalize_timestamp(local.get("timestamp"))
    remote_time = normalize_timestamp(remote.get("timestamp"))
    may_supplement = not local.get("known") or (
        local_time is not None
        and remote_time is not None
        and remote_time > local_time
    )
    if remote.get("known") and may_supplement:
        result["credits"] = {
            "known": True,
            "value": max(0, int(remote.get("value") or 0)),
            "timestamp": str(remote.get("timestamp") or ""),
            "basis": "FRONTIER CAPI",
        }
        if remote.get("timestamp"):
            result["lastUpdated"] = max(
                str(result.get("lastUpdated") or ""),
                str(remote.get("timestamp") or ""),
            )
    _supplement_capi_ranks(result, capi_profile.get("ranks"))
    _supplement_capi_ship_value(result, capi_profile.get("activeShip"))
    return result



def _supplement_capi_ranks(result, capi_ranks):
    """Fill only ranks the Journal has not established; never downgrade one."""
    if not isinstance(capi_ranks, dict):
        return
    rows = result.get("ranks")
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict) or row.get("known"):
            continue
        value = capi_ranks.get(str(row.get("key") or "").casefold())
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            row.update({"known": True, "rank": value, "rankBasis": "FRONTIER CAPI"})



def _supplement_capi_ship_value(result, active_ship):
    """Expose a CAPI rebuy estimate when the Journal offers no ship value."""
    if not isinstance(active_ship, dict):
        return
    total = active_ship.get("value")
    rebuy = active_ship.get("rebuy")
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        existing = result.get("shipValue")
        if not (isinstance(existing, dict) and existing.get("known")):
            result["shipValue"] = {
                "known": True, "value": total,
                "rebuy": int(rebuy) if isinstance(rebuy, (int, float))
                and not isinstance(rebuy, bool) else round(total * 0.05),
                "basis": "FRONTIER CAPI",
                "timestamp": str(active_ship.get("observedAt") or ""),
            }



def merge_capi_fleet(fleet_state, capi_profile):
    """Supplement one CAPI current ship without deleting Journal fleet rows."""
    result = deepcopy(fleet_state) if isinstance(fleet_state, dict) else {}
    rows = [
        dict(row) for row in result.get("ships", [])
        if isinstance(row, dict)
    ]
    capi_profile = capi_profile if isinstance(capi_profile, dict) else {}
    remote = capi_profile.get("activeShip")
    remote = remote if isinstance(remote, dict) else {}
    if remote.get("known") and remote.get("id"):
        ship_id = str(remote.get("id"))
        existing = next(
            (row for row in rows if str(row.get("id")) == ship_id), None
        )
        remote_time = normalize_timestamp(remote.get("observedAt"))
        local_time = (
            normalize_timestamp(existing.get("observedAt")) if existing else None
        )
        remote_is_newer = bool(
            remote_time is not None
            and (local_time is None or remote_time > local_time)
        )
        if existing is None:
            existing = {"id": ship_id, "status": "active", "isCurrent": True}
            rows.append(existing)
            remote_is_newer = True
        for field_name in ("type", "name", "ident", "value"):
            value = remote.get(field_name)
            if value not in (None, "") and (
                remote_is_newer or existing.get(field_name) in (None, "")
            ):
                existing[field_name] = value
        if remote_is_newer:
            for row in rows:
                row["isCurrent"] = row is existing
                if row is not existing and row.get("status") == "active":
                    row["status"] = "stored"
            existing.update({
                "status": "active",
                "isCurrent": True,
                "observedAt": str(remote.get("observedAt") or ""),
                "source": "frontier_capi",
            })
            result["active_id"] = ship_id
        if not existing.get("label"):
            existing["label"] = (
                f"{existing.get('type')} – {existing.get('name')}"
                if existing.get("type") and existing.get("name")
                else str(existing.get("name") or existing.get("type")
                         or f"Ship #{ship_id}")
            )
    _merge_capi_fleet_roster(
        rows, capi_profile.get("fleet"), result.get("active_id")
    )
    rows.sort(key=lambda row: (
        str(row.get("type") or "").casefold(),
        str(row.get("name") or "").casefold(),
        str(row.get("id") or ""),
    ))
    result["ships"] = rows
    return result



def _merge_capi_fleet_roster(rows, capi_fleet, active_id):
    """Add or fill stored-ship rows from CAPI without touching Journal truth."""
    if not isinstance(capi_fleet, list):
        return
    by_id = {str(row.get("id")): row for row in rows if isinstance(row, dict)}
    supplement = ("type", "name", "ident", "systemName", "stationName")
    for entry in capi_fleet:
        if not isinstance(entry, dict) or entry.get("id") in (None, ""):
            continue
        ship_id = str(entry.get("id"))
        existing = by_id.get(ship_id)
        remote_time = normalize_timestamp(entry.get("observedAt"))
        if existing is None:
            new_row = {
                "id": ship_id,
                "status": "active" if ship_id == str(active_id or "") else "remote",
                "isCurrent": ship_id == str(active_id or ""),
                "observedAt": str(entry.get("observedAt") or ""),
                "source": "frontier_capi",
            }
            for field_name in supplement:
                if entry.get(field_name) not in (None, ""):
                    new_row[field_name] = entry.get(field_name)
            for field_name in ("value", "hullValue", "modulesValue"):
                if isinstance(entry.get(field_name), int):
                    new_row[field_name] = entry.get(field_name)
            new_row["label"] = (
                f"{new_row.get('type')} – {new_row.get('name')}"
                if new_row.get("type") and new_row.get("name")
                else str(new_row.get("name") or new_row.get("type")
                         or f"Ship #{ship_id}")
            )
            rows.append(new_row)
            by_id[ship_id] = new_row
            continue
        local_time = normalize_timestamp(existing.get("observedAt"))
        remote_is_newer = bool(
            remote_time is not None
            and (local_time is None or remote_time > local_time)
        )
        for field_name in supplement:
            value = entry.get(field_name)
            if value in (None, ""):
                continue
            if remote_is_newer or existing.get(field_name) in (None, ""):
                existing[field_name] = value
        for field_name in ("value", "hullValue", "modulesValue"):
            value = entry.get(field_name)
            if isinstance(value, int) and (
                remote_is_newer or not isinstance(existing.get(field_name), int)
            ):
                existing[field_name] = value
        if entry.get("systemName") or entry.get("stationName"):
            existing.setdefault("source", "frontier_capi")



def capi_loadout_slots(capi_modules):
    """Convert CAPI ``ship.modules`` rows into the Journal loadout-slot shape."""
    slots = []
    for entry in capi_modules if isinstance(capi_modules, list) else []:
        if not isinstance(entry, dict):
            continue
        slot = str(entry.get("slot") or "")
        module_id = canonical_module_id(entry.get("moduleName"))
        if not slot or not module_id:
            continue
        try:
            grade = max(0, min(5, int(entry.get("grade") or 0)))
        except (TypeError, ValueError):
            grade = 0
        blueprint = str(entry.get("blueprint") or "")
        slots.append({
            "slot": slot,
            "moduleId": module_id,
            "engineered": bool(blueprint and grade > 0),
            "engineeringGrade": grade,
            # CAPI never reports within-grade quality; mark it unknown so the
            # planner treats the roll conservatively, exactly as it does for a
            # Journal Loadout that also omits Quality.
            "engineeringQuality": 0.0,
            "engineeringQualityKnown": False,
            "engineeringBlueprint": blueprint,
            "experimentalEffect": str(entry.get("experimental") or ""),
        })
    return slots



def merge_capi_loadout(state, capi_profile):
    """Answer "what is installed?" from CAPI only when the Journal cannot.

    The Journal Loadout always wins. This fills the install guard for the
    active ship when this machine has never seen a Loadout for it (fresh
    install, ship engineered on another PC, rotated Journal files).
    """
    state = dict(state) if isinstance(state, dict) else {}
    capi_profile = capi_profile if isinstance(capi_profile, dict) else {}
    if state.get("moduleSlots"):
        return state
    active = capi_profile.get("activeShip")
    active = active if isinstance(active, dict) else {}
    selected_id = str(state.get("selectedShipId") or "")
    if not selected_id or str(active.get("id") or "") != selected_id:
        return state
    capi_slots = capi_loadout_slots(capi_profile.get("activeShipModules"))
    if not capi_slots:
        return state
    observed_at = str(active.get("observedAt") or "")
    for slot in capi_slots:
        slot["source"] = "frontier_capi"
        slot["observedAt"] = observed_at
    blueprints = [
        dict(row) for row in state.get("blueprints", []) if isinstance(row, dict)
    ]
    annotate_installed_target_conflicts(
        blueprints, capi_slots, loadout_known=True
    )
    for row in blueprints:
        row["loadoutSource"] = "frontier_capi"
        row["loadoutObservedAt"] = observed_at
    return {
        **state,
        "moduleSlots": capi_slots,
        "blueprints": blueprints,
        "loadoutSource": "frontier_capi",
        "loadoutObservedAt": observed_at,
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
        if name == "PowerplayLeave":
            # Leaving is the authoritative negative membership signal. Elite
            # does not reliably repeat Powerplay after every LoadGame within a
            # journal session, so LoadGame itself must not erase valid proof.
            membership = {"power": "", "rank": None, "merits": None,
                          "timePledged": None, "timePledgedObservedAt": "",
                          "timestamp": timestamp}
        elif name == "PowerplayJoin" and str(event.get("Power") or "").strip():
            power = str(event.get("Power") or "")
            if membership["power"] and membership["power"] != power:
                membership.update({
                    "rank": None, "merits": None, "timePledged": None,
                    "timePledgedObservedAt": "",
                })
            membership.update({"power": power, "timestamp": timestamp})
        elif name == "PowerplayDefect" and str(
            event.get("ToPower") or event.get("Power") or ""
        ).strip():
            membership = {
                "power": str(event.get("ToPower") or event.get("Power") or ""),
                "rank": None, "merits": None, "timePledged": None,
                "timePledgedObservedAt": "", "timestamp": timestamp,
            }
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

