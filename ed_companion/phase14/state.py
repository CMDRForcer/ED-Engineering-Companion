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








def engineering_run_preflight(state: object, engineer_route: object) -> dict[str, Any]:
    """Audit the complete Engineering run without changing its next action."""
    state = state if isinstance(state, dict) else {}
    plans = [
        row for row in (state.get("blueprints") or [])
        if isinstance(row, dict)
        and str(row.get("targetStatus") or "") != "completed"
    ]
    route = [row for row in (engineer_route or []) if isinstance(row, dict)]
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def add(target, code: str, title: str, detail: str) -> None:
        target.append({"code": code, "title": title, "detail": detail})

    missing_rows = [
        material
        for plan in plans
        for key in ("materialProgress", "experimentalMaterialProgress")
        for material in (plan.get(key) or [])
        if isinstance(material, dict) and int(material.get("missing", 0) or 0) > 0
    ]
    missing_by_key: dict[str, int] = {}
    missing_names: dict[str, str] = {}
    for material in missing_rows:
        key = str(material.get("key") or material.get("name") or "material")
        missing_by_key[key] = max(
            missing_by_key.get(key, 0), int(material.get("missing", 0) or 0)
        )
        missing_names[key] = str(material.get("name") or key)
    if missing_by_key:
        units = sum(missing_by_key.values())
        preview = ", ".join(
            f"{missing_names[key]} ×{missing_by_key[key]}"
            for key in list(missing_by_key)[:3]
        )
        add(blockers, "MATERIALS", "Materials incomplete",
            f"{len(missing_by_key)} missing types · {units} units · {preview}")

    binding = [plan for plan in plans if plan.get("bindingRequired")]
    if binding:
        add(blockers, "MODULE_BINDING", "Module slots not confirmed",
            f"{len(binding)} plan(s) still need an exact physical slot binding.")
    conflicts = [plan for plan in plans if plan.get("targetConflict")]
    if conflicts:
        add(blockers, "MODULE_CONFLICT", "Installed engineering differs",
            f"{len(conflicts)} installed module(s) conflict with their target blueprint.")
    missing_modules = [
        plan for plan in plans
        if plan.get("boundSlot") and not plan.get("bindingRequired")
        and not plan.get("installedModule")
    ]
    if missing_modules:
        add(blockers, "MODULE_MISSING", "Target modules are not installed",
            f"{len(missing_modules)} bound slot(s) have no matching installed module.")

    calculation_warnings = [
        str(plan.get("calculationWarning") or "")
        for plan in plans if plan.get("calculationWarning")
    ]
    state_warning = str(state.get("calculationWarning") or "")
    if state_warning or calculation_warnings:
        add(blockers, "RECIPE_DATA", "Material calculation is incomplete",
            state_warning or calculation_warnings[0])
    uncertain = [
        plan for plan in plans
        if str(plan.get("targetStatus") or "") in {"not_started", "in_progress"}
        and not bool(plan.get("rollEstimateReliable"))
    ]
    if uncertain:
        add(warnings, "ROLL_ESTIMATE", "Roll demand is still estimated",
            f"{len(uncertain)} plan(s) have no matching live or Journal-history roll evidence.")

    locked_stops = [row for row in route if not bool(row.get("craftable"))]
    if locked_stops:
        names = ", ".join(str(row.get("name") or "Engineer") for row in locked_stops)
        add(blockers, "ENGINEER_ACCESS", "Engineer access or rank is insufficient",
            f"{len(locked_stops)} blocked stop(s): {names}")
    routed_jobs = sum(int(row.get("openJobs", 0) or 0) for row in route)
    if plans and routed_jobs < len(plans):
        add(blockers, "ENGINEER_ROUTE", "Some plans have no Engineer stop",
            f"{len(plans) - routed_jobs} open plan(s) cannot be assigned safely.")

    if not plans:
        status, label, summary = "IDLE", "NO OPEN RUN", "No unfinished Engineering plans."
    elif blockers:
        status, label = "BLOCKED", "RUN BLOCKED"
        summary = f"{len(blockers)} blocker(s) · {len(warnings)} estimate warning(s)"
    elif warnings:
        status, label = "CAUTION", "RUN READY · ESTIMATES"
        summary = f"{len(plans)} plans · {len(route)} stops · verify {len(warnings)} estimate warning(s)"
    else:
        status, label = "READY", "RUN READY"
        summary = f"{len(plans)} plans · {len(route)} Engineer stops · all checks passed"
    return {
        "status": status, "label": label, "summary": summary,
        "blockers": blockers, "warnings": warnings,
        "openPlans": len(plans), "engineerStops": len(route),
        "ready": status == "READY",
    }


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


def annotate_installed_target_conflicts(
    rows: list[dict[str, Any]], module_slots: object, *, loadout_known=True,
) -> None:
    """Expose verified Loadout-vs-wishlist conflicts without auto-deleting."""
    installed_by_slot = {
        str(row.get("slot") or ""): row
        for row in (module_slots or [])
        if isinstance(row, dict) and row.get("slot")
    }
    for row in rows:
        installed = installed_by_slot.get(str(row.get("boundSlot") or ""), {})
        installed_module = str(installed.get("moduleId") or "")
        raw_blueprint = str(installed.get("engineeringBlueprint") or "")
        installed_blueprint = JOURNAL_BLUEPRINT_NAMES.get(
            normalize(raw_blueprint), raw_blueprint
        )
        same_module = bool(
            installed_module and row.get("boundModule")
            and same_module_identity(installed_module, row.get("boundModule"))
        )
        slot_state_known = bool(loadout_known or installed_module)
        loadout_unknown = bool(
            row.get("boundSlot") and row.get("boundModule")
            and not slot_state_known
        )
        conflict = bool(
            same_module and installed_blueprint and row.get("blueprint")
            and normalize(installed_blueprint) != normalize(row.get("blueprint"))
        )
        installation_required = bool(
            row.get("boundSlot") and row.get("boundModule")
            and slot_state_known and not same_module
        )
        row.update({
            "installedModule": installed_module,
            "installedModuleMatches": same_module,
            "installationRequired": installation_required,
            "loadoutKnown": slot_state_known,
            "loadoutUnknown": loadout_unknown,
            "installedBlueprint": installed_blueprint,
            "installedGrade": int(installed.get("engineeringGrade") or 0),
            "installedQuality": float(installed.get("engineeringQuality") or 0),
            "installedQualityKnown": bool(installed.get("engineeringQualityKnown")),
            "targetConflict": conflict,
            "targetConflictText": (
                f"INSTALLED · {installed_blueprint} G"
                f"{int(installed.get('engineeringGrade') or 0)} · TARGET · "
                f"{row.get('blueprint')} G{int(row.get('targetGrade') or 0)}"
                if conflict else ""
            ),
        })


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








def operation_physical_slot_label(slot: object) -> str:
    """Return the cockpit-facing identity of one exact physical ship slot."""
    value = str(slot or "").strip()
    if not value:
        return ""
    core_labels = {
        "Armour": "CORE · ARMOUR",
        "PowerPlant": "CORE · POWER PLANT",
        "MainEngines": "CORE · THRUSTERS",
        "FrameShiftDrive": "CORE · FRAME SHIFT DRIVE",
        "LifeSupport": "CORE · LIFE SUPPORT",
        "PowerDistributor": "CORE · POWER DISTRIBUTOR",
        "Radar": "CORE · SENSORS",
        "FuelTank": "CORE · FUEL TANK",
    }
    if value in core_labels:
        return core_labels[value]
    optional = re.fullmatch(r"Slot(\d+)_Size(\d+)", value, re.IGNORECASE)
    if optional:
        return (
            f"OPTIONAL SLOT {int(optional.group(1))} · "
            f"SIZE {int(optional.group(2))}"
        )
    utility = re.fullmatch(r"TinyHardpoint(\d+)", value, re.IGNORECASE)
    if utility:
        return f"UTILITY SLOT {int(utility.group(1))}"
    hardpoint = re.fullmatch(
        r"(Small|Medium|Large|Huge)Hardpoint(\d+)", value, re.IGNORECASE
    )
    if hardpoint:
        sizes = {"small": 1, "medium": 2, "large": 3, "huge": 4}
        size_name = hardpoint.group(1).upper()
        return (
            f"{size_name} HARDPOINT {int(hardpoint.group(2))} · "
            f"SIZE {sizes[hardpoint.group(1).casefold()]}"
        )
    return value


def operation_plan_identity(plan: object) -> dict:
    """Return one canonical module identity for every Engineering plan shape."""
    plan = plan if isinstance(plan, dict) else {}
    physical_slot = str(plan.get("boundSlot") or "")
    return {
        "moduleName": str(plan.get("module") or ""),
        "blueprintName": str(plan.get("blueprint") or ""),
        "targetGrade": int(plan.get("targetGrade", 0) or 0),
        "experimentalName": str(plan.get("experimental") or ""),
        "experimentalId": str(plan.get("experimentalId") or ""),
        "targetStatus": str(plan.get("targetStatus") or ""),
        "physicalSlot": physical_slot,
        "physicalSlotLabel": operation_physical_slot_label(physical_slot),
    }


def scope_operation_action_materials(state: object, action: object) -> dict:
    """Show priority-plan readiness on the priority-plan next action."""
    state = state if isinstance(state, dict) else {}
    result = dict(action) if isinstance(action, dict) else {}
    priority = next((
        row for row in (state.get("blueprints") or [])
        if isinstance(row, dict) and row.get("priority")
        and str(row.get("targetStatus") or "") != "completed"
    ), None)
    if not priority:
        return result
    progress = list(
        priority.get("experimentalMaterialProgress") or []
        if priority.get("targetStatus") == "experimental_pending"
        else priority.get("materialProgress") or []
    )
    required = sum(max(0, int(row.get("need", 0) or 0)) for row in progress)
    covered = sum(min(
        max(0, int(row.get("have", 0) or 0)),
        max(0, int(row.get("need", 0) or 0)),
    ) for row in progress)
    reliable = bool(priority.get("completionReliable", True))
    identity = operation_plan_identity(priority)
    result.update({
        "priority": True,
        "moduleName": str(result.get("moduleName") or identity["moduleName"]),
        "blueprintName": str(result.get("blueprintName") or identity["blueprintName"]),
        "targetGrade": int(result.get("targetGrade") or identity["targetGrade"]),
        "experimentalName": str(result.get("experimentalName") or identity["experimentalName"]),
        "experimentalId": str(result.get("experimentalId") or identity["experimentalId"]),
        "targetStatus": str(priority.get("targetStatus") or ""),
        "physicalSlot": str(result.get("physicalSlot") or identity["physicalSlot"]),
        "physicalSlotLabel": str(
            result.get("physicalSlotLabel") or identity["physicalSlotLabel"]
        ),
        "materialCompletion": (
            covered / required if required > 0 else 1.0
        ) if reliable else 0.0,
        "materialStatus": (
            "READY" if reliable and covered >= required else
            "PARTIAL" if covered > 0 else "MISSING"
        ),
        "materialCompletionReliable": reliable,
        "materialCovered": covered,
        "materialRequired": required,
        "missingMaterials": [
            dict(row) for row in progress
            if int(row.get("missing", 0) or 0) > 0
        ],
        "planProgressStatus": str(priority.get("progressStatus") or ""),
        "calculationWarning": str(priority.get("calculationWarning") or ""),
        "materialScope": "PRIORITY PLAN",
    })
    return result


def attach_operation_experimental_effects(action: object, records: object) -> dict:
    """Attach catalog-backed Experimental effects to an Engineering action."""
    result = dict(action) if isinstance(action, dict) else {}
    wanted_id = str(result.get("experimentalId") or "")
    wanted_name = normalize(result.get("experimentalName"))
    module_name = str(result.get("moduleName") or "")
    candidates = [row for row in (records or []) if isinstance(row, dict)]
    record = next((
        row for row in candidates
        if wanted_id and str(row.get("ExperimentalId") or "") == wanted_id
    ), None)
    if record is None and wanted_name:
        matches = [
            row for row in candidates
            if normalize(row.get("Name")) == wanted_name
            and (
                not module_name
                or normalize(row.get("Type")) == normalize(module_name)
                or module_matches_type(module_name, row.get("Type"))
            )
        ]
        record = matches[0] if len(matches) == 1 else None
    result["experimentalEffects"] = [
        {
            "property": str(effect.get("Property") or "Effect"),
            "effect": str(effect.get("Effect") or ""),
            "isGood": bool(effect.get("IsGood")),
            "summary": " ".join(value for value in (
                str(effect.get("Property") or ""),
                str(effect.get("Effect") or ""),
            ) if value),
        }
        for effect in ((record or {}).get("Effects") or [])
        if isinstance(effect, dict)
    ]
    return result


def attach_operation_plan_context(
    action: object, state: object, engineer_rows: object, blueprint_records: object
) -> dict:
    """Attach the exact owning module plan to every Engineering action shape."""
    result = dict(action) if isinstance(action, dict) else {}
    kind = str(result.get("kind") or "")
    if kind == "COMPLETE" or kind.startswith("TECH_BROKER"):
        return result
    plans = sorted([
        row for row in ((state or {}).get("blueprints") or [])
        if isinstance(row, dict) and str(row.get("targetStatus") or "") != "completed"
    ], key=lambda row: (
        not bool(row.get("priority")),
        {"experimental_pending": 0, "in_progress": 1, "not_started": 2}.get(
            str(row.get("targetStatus") or ""), 3
        ),
        int(row.get("index", 0) or 0),
    ))
    if not plans:
        return result

    module_name = normalize(result.get("moduleName"))
    blueprint_name = normalize(result.get("blueprintName"))
    plan = next((
        row for row in plans
        if module_name and normalize(row.get("module")) == module_name
        and (not blueprint_name or normalize(row.get("blueprint")) == blueprint_name)
    ), None)

    material_key = str(result.get("materialKey") or "")
    if plan is None and material_key:
        plan = next((
            row for row in plans
            if any(
                str(material.get("key") or "") == material_key
                and int(material.get("missing", 0) or 0) > 0
                for field in ("materialProgress", "experimentalMaterialProgress")
                for material in (row.get(field) or [])
            )
        ), None)

    engineer_name = normalize(result.get("engineerName"))
    if plan is None and engineer_name:
        plan = next((
            row for row in plans
            if any(
                normalize(option.get("name")) == engineer_name
                for option in engineer_options_for_plan(
                    row, engineer_rows, blueprint_records
                )
            )
        ), None)

    plan_action_kinds = {
        "CRAFT_MATCH_BLOCKER", "BINDING_BLOCKER", "CALCULATION_BLOCKER",
        "LOADOUT_BLOCKER", "OUTFITTING_BLOCKER", "EXPERIMENTAL_BLOCKER",
        "TRADE", "COLLECT", "GRADE_CRAFT",
        "EXPERIMENTAL_CRAFT", "ENGINEER_PREPARE", "ENGINEER_UNLOCK",
        "ENGINEER_TRAVEL",
    }
    if plan is None and kind in plan_action_kinds:
        plan = plans[0]
    if plan is None:
        return result

    identity = operation_plan_identity(plan)
    options = engineer_options_for_plan(plan, engineer_rows, blueprint_records)
    engineer = options[0] if options else {}
    result.update({
        "moduleName": str(result.get("moduleName") or identity["moduleName"]),
        "blueprintName": str(result.get("blueprintName") or identity["blueprintName"]),
        "targetGrade": int(result.get("targetGrade") or identity["targetGrade"]),
        "experimentalName": str(result.get("experimentalName") or identity["experimentalName"]),
        "experimentalId": str(result.get("experimentalId") or identity["experimentalId"]),
        "targetStatus": str(result.get("targetStatus") or identity["targetStatus"]),
        "physicalSlot": str(result.get("physicalSlot") or identity["physicalSlot"]),
        "physicalSlotLabel": str(
            result.get("physicalSlotLabel") or identity["physicalSlotLabel"]
        ),
        "engineerOptions": options,
        "portraitUrl": str(result.get("portraitUrl") or engineer.get("portraitUrl") or ""),
        "engineerName": str(result.get("engineerName") or engineer.get("name") or ""),
        "system": str(result.get("system") or engineer.get("system") or ""),
        "station": str(result.get("station") or engineer.get("station") or ""),
    })
    return result


def select_operation_action(
    state, engineer_route, engineer_rows=None, blueprint_records=None
):
    """Select one truthful, executable Commander action from current state."""
    state = state or {}
    plans = list(state.get("blueprints") or [])
    priority_plan = next((plan for plan in plans
                          if plan.get("priority")
                          and plan.get("targetStatus") != "completed"), None)
    if priority_plan is not None:
        plans = [priority_plan]
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
    # A plan already in progress - or only waiting on its planned
    # Experimental - and material-ready right now is a workflow already
    # underway at an Engineer. An unrelated, untouched plan's own data
    # gap (an ambiguous binding, an unconfirmed loadout, a module not yet
    # installed) must not interrupt it; that gap surfaces once its own
    # plan becomes primary. If the ready plan is itself the one with the
    # gap, the gap still applies to it.
    ready_in_progress = next((
        row for row in open_plans
        if str(row.get("targetStatus") or "") in {"in_progress", "experimental_pending"}
        and bool(
            row.get("experimentalReady")
            if row.get("targetStatus") == "experimental_pending"
            else row.get("canCraftNext")
        )
    ), None)

    def _defer_unless_it_blocks_the_ready_plan(rows):
        if ready_in_progress is None:
            return rows
        return [row for row in rows if row is ready_in_progress]

    binding_blockers = _defer_unless_it_blocks_the_ready_plan([
        row for row in plans
        if row.get("bindingRequired")
        and str(row.get("targetStatus") or "") != "completed"
    ])
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
    loadout_blockers = _defer_unless_it_blocks_the_ready_plan([
        row for row in open_plans if row.get("loadoutUnknown")
    ])
    if loadout_blockers:
        plan = min(
            loadout_blockers,
            key=lambda row: str(row.get("boundSlot") or ""),
        )
        module = str(plan.get("module") or "planned module")
        slot = str(plan.get("boundSlot") or "")
        slot_label = operation_physical_slot_label(slot)
        return {
            "kind": "LOADOUT_BLOCKER",
            "title": f"Confirm the loadout for {module}",
            "detail": (
                "No authoritative Loadout has been observed for this ship and "
                "module slot."
            ),
            "reason": (
                "EDEC cannot tell whether this remote slot is empty or already "
                "contains the planned module."
            ),
            "after": (
                "Activate this ship in Elite to emit a Loadout; the saved "
                "Engineering and material plan will then continue automatically."
            ),
            "system": "",
            "station": "",
            "buttonLabel": "OPEN ENGINEERING",
            "targetPage": 3,
            "executable": True,
            "moduleName": module,
            "blueprintName": str(plan.get("blueprint") or ""),
            "targetGrade": int(plan.get("targetGrade", 0) or 0),
            "physicalSlot": slot,
            "physicalSlotLabel": slot_label,
            "installationState": "UNKNOWN",
        }
    installation_blockers = _defer_unless_it_blocks_the_ready_plan([
        row for row in open_plans if row.get("installationRequired")
    ])
    if installation_blockers:
        # Pick the slot to fill first by a stable key so the guidance does not
        # depend on the incidental order of the plan list.
        plan = min(
            installation_blockers,
            key=lambda row: str(row.get("boundSlot") or ""),
        )
        module = str(plan.get("module") or "planned module")
        slot = str(plan.get("boundSlot") or "")
        slot_label = operation_physical_slot_label(slot)
        installed_module = str(plan.get("installedModule") or "")
        return {
            "kind": "OUTFITTING_BLOCKER",
            "title": f"Install {module} in {slot_label or 'its planned slot'}",
            "detail": (
                "The target slot is empty."
                if not installed_module else
                "The installed module does not match the planned module."
            ),
            "reason": (
                "Engineering cannot begin until the exact planned module is "
                "installed in its bound ship slot."
            ),
            "after": (
                "The Journal Loadout will confirm the installation; the saved "
                "Engineering and material plan will then continue automatically."
            ),
            "system": "",
            "station": "",
            "buttonLabel": "OPEN WISHLIST",
            "targetPage": 1,
            "executable": True,
            "moduleName": module,
            "blueprintName": str(plan.get("blueprint") or ""),
            "targetGrade": int(plan.get("targetGrade", 0) or 0),
            "physicalSlot": slot,
            "physicalSlotLabel": slot_label,
            "installationState": "MISMATCH" if installed_module else "EMPTY",
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
    if priority_plan is not None:
        priority_missing = {}
        for field in ("materialProgress", "experimentalMaterialProgress"):
            for row in priority_plan.get(field) or []:
                amount = max(0, int(row.get("missing", 0) or 0))
                key = str(row.get("key") or "")
                if amount and key:
                    entry = priority_missing.setdefault(key, {**row, "missing": 0})
                    entry["missing"] += amount
        missing = list(priority_missing.values())
        trades = [row for row in trades
                  if str(row.get("targetKey") or "") in priority_missing]
        scoped_trades = []
        for trade in trades:
            give = int(trade.get("giveAmount", 0) or 0)
            receive = int(trade.get("receiveAmount", 0) or 0)
            if give <= 0 or receive <= 0:
                continue
            divisor = math.gcd(give, receive)
            receive_step, give_step = receive // divisor, give // divisor
            needed = priority_missing[str(trade["targetKey"])]["missing"]
            batches = min(divisor, math.ceil(needed / receive_step))
            scoped_trades.append({
                **trade, "giveAmount": batches * give_step,
                "receiveAmount": batches * receive_step,
                "remaining": max(0, needed - batches * receive_step),
                "instruction": (
                    f"WANTED · {batches * receive_step} {trade.get('receiveName', '')}"
                    f" · GIVE · {batches * give_step} {trade.get('giveName', '')}"
                ),
            })
        trades = scoped_trades
    tech_track = dict(state.get("techBrokerTrack") or {})
    if tech_track and priority_plan is None:
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

    # A plan already underway (its Grade rolls started, or only its
    # Experimental remains) must be finished - Grade reached and any
    # planned Experimental applied - before Operations recommends moving
    # on. Route/stop order is only a tie-break within the same urgency
    # tier; it must never let a not-started plan at an earlier stop
    # preempt a plan already in progress at a later one.
    active_plans.sort(key=lambda row: (
        {
            "experimental_pending": 0,
            "in_progress": 1,
            "not_started": 2,
        }.get(str(row.get("targetStatus") or ""), 3),
        assigned_stop_index(row),
        not bool(row.get("priority")),
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
        physical_slot = str(plan.get("boundSlot") or "")
        physical_slot_label = operation_physical_slot_label(physical_slot)
        blueprint = str(plan.get("blueprint") or "Blueprint")
        target_grade = int(plan.get("targetGrade", 0) or 0)
        action_grade = (
            target_grade if experimental else
            int(plan.get("nextGrade", 0) or target_grade)
        )
        module_identity = " · ".join(
            value for value in (module, physical_slot_label) if value
        )
        identity = (
            f"{module_identity} · {blueprint} · G{action_grade}"
            if action_grade > 0 else f"{module_identity} · {blueprint}"
        )
        engineer_options = engineer_options_for_plan(
            plan, engineer_rows, blueprint_records
        )
        if not stop or not stop.get("craftable", False):
            return {
                "kind": "ENGINEER_PREPARE",
                "title": f"Prepare {identity}",
                "shortTitle": f"Prepare {module}",
                "detail": "Open Engineer Navigation for the required access, rank and destination.",
                "reason": "The active craft is material-ready, but no executable Engineer stop is confirmed yet.",
                "after": "Once access is confirmed, continue this same craft without switching plans.",
                "system": "", "station": "", "buttonLabel": "OPEN ENGINEERS",
                "targetPage": 4, "executable": True,
                "moduleName": module,
                "blueprintName": blueprint,
                "targetGrade": target_grade,
                "actionGrade": action_grade,
                "experimentalName": str(plan.get("experimental") or ""),
                "experimentalId": str(plan.get("experimentalId") or ""),
                "physicalSlot": physical_slot,
                "physicalSlotLabel": physical_slot_label,
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
            "shortTitle": (
                f"Experimental · {module}" if experimental
                else f"Continue {module}"
            ),
            "detail": " · ".join(value for value in (engineer, station, system) if value),
            "moduleName": module,
            "blueprintName": blueprint,
            "targetGrade": target_grade,
            "actionGrade": action_grade,
            "experimentalName": str(plan.get("experimental") or ""),
            "experimentalId": str(plan.get("experimentalId") or ""),
            "physicalSlot": physical_slot,
            "physicalSlotLabel": physical_slot_label,
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
    # A plan already underway - a Grade roll banked, or only its planned
    # Experimental left - is a workflow already in progress at an Engineer.
    # Its own materials being ready must keep it primary; the global
    # gather-everything gate below is for a not-yet-started plan only, so
    # a craft that could happen right now is never deferred in favor of
    # trading for a completely different, untouched plan's materials.
    if active_craft_ready and active_status in {
        "in_progress", "experimental_pending",
    }:
        return craft_action(
            active_plan, active_stop, experimental=active_craft_experimental
        )
    # A priority plan completes its own material/craft workflow first. Without
    # one, acquire materials for all open plans before visiting Engineers.
    if trades and missing:
        trade = trades[0]
        system = str(trade.get("system") or "")
        station = str(trade.get("station") or "")
        return {
            "kind": "TRADE",
            "materialKey": str(trade.get("targetKey") or ""),
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
            "materialKey": str(material.get("key") or ""),
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
