"""Read-only Phase 14 view model built from existing app and Journal data."""

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

# Journal/profile plumbing, plus a handful of constants shared across
# domains - extracted to state_core.py so every domain module below can
# depend on it without importing back from this file (which would be
# circular, since this file imports the domain modules for re-export).
# Every name here stays importable from ed_companion.phase14.state exactly
# as before the refactor.
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

# Materials and technology-broker unlock logic - a leaf domain, only
# depends on state_core.
from .state_materials import (
    RAW_GROUP_FARMS,
    _newer_station_location,
    _source_distance,
    actionable_source_card,
    canonical_cargo_materials,
    material_metadata,
    material_trade_options,
    source_cards,
    technology_broker_unlock_guide,
)

# Fleet, module slots and loadout projection - a leaf domain, only
# depends on state_core.
from .state_fleet import (
    LOADOUT_PROJECTION_EVENTS,
    MANDATORY_CORE_STOCK_FAMILIES,
    _cached_profile_loadout_slots_by_ship,
    _module_display_catalog,
    engineering_loadout_rows,
    latest_loadout_slots,
    latest_loadout_slots_by_ship,
    module_matches_type,
    module_purchase_identity,
    module_store_core_replacement,
    reconcile_fleet_cache,
    ship_slot_layout,
)

# Logbook and session history - depends on state_core and
# state_materials (material_metadata, for filtering MaterialCollected
# entries down to notable grades).
from .state_logbook import (
    LOGBOOK_CATEGORIES,
    LOGBOOK_FILTERS,
    LOGBOOK_LIMIT,
    LOGBOOK_NOTE_LIMIT,
    _logbook_entry,
    _logbook_material_is_interesting,
    filter_logbook_entries,
    load_logbook_notes,
    load_session_history,
    logbook_entries,
    session_statistics,
    write_logbook_note,
)

# Engineering wishlist plan + live craft tracking - one file, not two:
# blueprint_rows (plan) calls wishlist_target_status (craft tracking)
# and craft tracking calls remaining_grade_rolls/planner_mode (plan) -
# splitting them would only create a circular import between the two
# files for a boundary the code itself doesn't actually have. Depends
# on state_core and state_fleet.
from .state_engineering import (
    EXPERIMENTAL_STATUS_LABELS,
    GRADE_STATUS_LABELS,
    MATERIAL_STATUS,
    _craft_can_bind,
    _craft_events_with_ship_context,
    _craft_matches_binding,
    _craft_matches_unique_equivalent_slot,
    _dismiss_craft_tracking_issues_locked,
    _eligible_plan_ids_after_baseline,
    _experimental_craft_matches,
    _grade_craft_matches_blueprint,
    _ingredient_display_signature,
    _ingredient_signature,
    _reconcile_engineer_craft_batch_locked,
    _singular_effect_key,
    apply_engineer_craft,
    blueprint_catalog,
    blueprint_rows,
    build_engineering_plan,
    build_experimental_plan,
    craft_issue_row,
    discard_bound_module_plans,
    dismiss_craft_tracking_issue,
    dismiss_historical_craft_tracking_issues,
    dismiss_selected_craft_tracking_issues,
    duplicate_ship_plan,
    engineer_craft_fingerprint,
    is_craft_before_safe_plan_baseline,
    is_unconfirmed_legacy_history,
    journal_craft_baseline,
    material_completion,
    material_status_label,
    migrate_legacy_plan_baselines,
    migrate_wishlist_bindings,
    move_ship_plan,
    planner_mode,
    planner_physical_identity,
    progress_status_label,
    reconcile_engineer_craft_batch,
    remaining_grade_rolls,
    remove_ship_task,
    replace_ship_plan,
    required_materials,
    reserve_material_pool,
    set_prioritized_ship_plan,
    task_signature,
    wishlist_target_status,
    write_ship_tasks,
)

# Engineer routing/assignment (nearest-engineer route planning) - a leaf
# domain, only depends on state_core.
from .state_engineering_routing import (
    _engineer_leg_distance,
    _minimum_engineer_cover,
    _shortest_engineer_route,
    assign_plans_to_nearest_engineers,
    engineer_options_for_plan,
    partition_engineer_assignments,
)

# Operations page action-selection logic - depends on state_core,
# state_fleet (module_matches_type) and state_engineering_routing
# (engineer_options_for_plan).
from .state_engineering_operations import (
    aggregate_plan_progress,
    annotate_installed_target_conflicts,
    attach_operation_experimental_effects,
    attach_operation_plan_context,
    classify_craft_tracking_issues,
    craft_issue_matches_plan,
    craft_tracking_issues_for_ship,
    engineering_run_preflight,
    operation_physical_slot_label,
    operation_plan_identity,
    scope_operation_action_materials,
    select_operation_action,
)

# Commander overview (ranks/reputation/credits), CAPI-merge and
# Powerplay - depends on state_core and state_engineering_operations
# (annotate_installed_target_conflicts, used by merge_capi_loadout).
from .state_commander import (
    COMMANDER_RANK_CATEGORIES,
    COMMANDER_REPUTATIONS,
    _merge_capi_fleet_roster,
    _supplement_capi_ranks,
    _supplement_capi_ship_value,
    capi_loadout_slots,
    commander_journal_overview,
    commander_status_credits,
    merge_capi_commander_overview,
    merge_capi_fleet,
    merge_capi_loadout,
    powerplay_journal_overview,
)


LOGGER = logging.getLogger(__name__)
























































def current_ship(
    data_dir: Path, fleet_state: dict[str, Any], preferred: str = "",
    events: list[dict[str, Any]] | None = None,
    bindings_migrated: bool = False,
) -> tuple[str, list[Any], list[str]]:
    """Select a wishlist exclusively from the rebuilt, existing fleet."""
    blueprints, aliases = reconcile_fleet_cache(data_dir, fleet_state)
    if not bindings_migrated:
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































































































def build_state(
    package_root, selected_ship="", preferred_plan_id="",
    trader_preference="confirmed",
):
    profile_context = resolve_profile_context()
    data_dir = runtime_data_dir(profile_context)
    journal_path_valid = journal_dir().is_dir()
    metadata = material_metadata(reference_data_dir(package_root))
    profile_events = profiled_journal_events()
    commander_name = next((
        str(event.get("Commander") or "")
        for event in reversed(profile_events)
        if event.get("event") == "LoadGame" and event.get("Commander")
    ), "")
    commander_overview = commander_journal_overview(
        profile_events, read_json(journal_dir() / "Status.json", {})
    )
    powerplay_overview = powerplay_journal_overview(profile_events)
    vehicle_state = project_vehicle_state(profile_events)
    vehicle_state["latestMiningSession"] = project_latest_srv_mining_session(
        profile_events
    )
    blueprint_learning = learn_blueprint_id_catalog(
        profile_events, data_dir / "blueprint_id_catalog_learned.json"
    )
    if (
        not blueprint_learning["conflicts"]
        and not blueprint_learning["ambiguous"]
        and (data_dir / "blueprint_diagnostics.json").exists()
    ):
        diagnostic_path = data_dir / "blueprint_diagnostics.json"
        diagnostics = read_json(diagnostic_path, [])
        if isinstance(diagnostics, list):
            diagnostics = [
                message for message in diagnostics
                if "(journal_override_conflict)" not in str(message)
            ]
            _write_json_if_changed(diagnostic_path, diagnostics)
    sessions = session_statistics(data_dir)
    events = journal_events(profile_events, include_current_cargo=True)
    unlock_events = journal_unlock_events(profile_events)
    consistency_issues = []
    consistency_issues.extend(persistence_issues(data_dir))
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
    ship_catalog = read_json(package_root / "ed_data" / "ships.json", [])
    fleet_state = rebuild_fleet(ship_events, ship_catalog)
    # Reconcile physical bindings and the installed grade boundary before
    # replaying pending EngineerCraft events. Doing this later leaves an exact
    # first craft falsely unmatched until another unrelated refresh occurs.
    loadout_slots_by_ship = migrate_wishlist_bindings(data_dir, fleet_state, profile_events)
    craft_batch = reconcile_engineer_craft_batch(
        data_dir, fleet_state, profile_events, preferred_plan_id
    )
    active_ship = next(
        (str(row["label"]) for row in fleet_state.get("ships", [])
         if str(row["id"]) == str(fleet_state.get("active_id") or "")), "",
    )
    ship, tasks, ships = current_ship(
        data_dir, fleet_state, selected_ship, profile_events,
        bindings_migrated=True,
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
    module_slots = [
        dict(row)
        for row in loadout_slots_by_ship.get(selected_ship_id, [])
    ]
    engineering_slots = engineering_loadout_rows(
        module_slots, blueprint_catalog(reference_data_dir(package_root)),
    )
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
    tech_broker_event_names = {
        "TechnologyBroker", "Loadout", "StoredModules",
        "ModuleBuy", "ModuleRetrieve", "ModuleStore", "ModuleSell",
        "Docked", "Location", "Market", "Outfitting", "Shipyard",
    }
    tech_broker_events = [
        event for event in profile_events
        if event.get("event") in tech_broker_event_names
        or event.get("StarSystem") or event.get("StationName")
    ]
    tech_broker_guide = technology_broker_unlock_guide(
        package_root, metadata, inventory, tech_broker_events,
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
    user_catalog = load_user_trader_catalog(profile_context)
    stations = merge_trader_catalog(
        base_stations,
        user_catalog.get("stations", [])
        if isinstance(user_catalog, dict) else [],
    )
    type_cache = TraderTypeCache().load()
    trader_evidence_events = [
        event for event in profile_events
        if event.get("event") in {"MaterialTrade", "Docked"}
    ]
    cache_changed = update_trader_type_evidence(
        type_cache,
        trader_evidence_events,
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
                HEURISTIC_TRADER_WARNING_KEY
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
    annotate_installed_target_conflicts(
        blueprint_state, module_slots, loadout_known=bool(selected_loadout)
    )
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
    consistency_issues.extend(
        issue for issue in persistence_issues(data_dir)
        if issue not in consistency_issues
    )
    exobiology_bundled_catalog = read_json(
        package_root / "ed_data" / "exobiology_species.json", []
    )
    # Filled out with anything learned from the Commander's own sales for a
    # species the bundled catalog doesn't know - see augmented_species_catalog().
    exobiology_species_catalog = augmented_species_catalog(
        exobiology_bundled_catalog, profile_events
    )
    exobiology_rows = exobiology_findings(profile_events, exobiology_species_catalog)
    exobiology_targets = landing_targets(
        profile_events, exobiology_species_catalog,
        current_system_address=latest_location.get("SystemAddress"),
    )
    exobiology_status_snapshot = read_json(journal_dir() / "Status.json", {})

    return {
        "_profileContext": profile_context,
        "_craftBatch": craft_batch,
        "exobiologyFindings": exobiology_rows,
        "exobiologySummary": exobiology_summary(exobiology_rows),
        "exobiologySessionSummary": exobiology_session_summary(
            profile_events, exobiology_species_catalog
        ),
        "exobiologyCarriedSummary": exobiology_carried_summary(
            profile_events, exobiology_species_catalog
        ),
        "exobiologyLandingTargets": exobiology_targets,
        "exobiologyLifetimeEarned": exobiology_lifetime_earned(profile_events),
        "exobiologyBestFind": best_find(exobiology_rows),
        "exobiologyRemainingOnBody": remaining_signals_at_body(
            profile_events, exobiology_species_catalog, exobiology_status_snapshot,
        ),
        "exobiologyGenusCompletion": genus_completion(
            exobiology_rows, exobiology_species_catalog
        ),
        "ship": ship or "No ship selected",
        "commander": commander_name,
        "commanderKnown": bool(commander_name),
        "commanderOverview": commander_overview,
        "powerplayOverview": powerplay_overview,
        "vehicleState": vehicle_state,
        "fleetKnown": bool(ships),
        "fleet": fleet_state.get("ships", []),
        "journalPathValid": journal_path_valid,
        "emptyStateReason": (
            "No Journal directory found. Configure the path or start Elite Dangerous."
            if not journal_path_valid else
            "No Commander detected yet. Waiting for a LoadGame event."
            if not commander_name else
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
        "localMiningEvidence": project_local_mining_evidence(events),
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
            if not commander_name or not ships else
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
