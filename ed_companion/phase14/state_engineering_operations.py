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
from .state_engineering_routing import engineer_options_for_plan



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

