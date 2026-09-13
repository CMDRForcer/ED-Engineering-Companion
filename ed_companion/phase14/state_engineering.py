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

from .state_fleet import _cached_profile_loadout_slots_by_ship, module_matches_type

LOGGER = logging.getLogger(__name__)



MATERIAL_STATUS = ("READY", "PARTIAL", "MISSING")


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



def migrate_wishlist_bindings(
    data_dir: Path, fleet_state: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Bind legacy plans uniquely or preserve them with a visible warning."""
    path = data_dir / "ship_blueprints.json"
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {}
    ids_by_label = {
        str(row["label"]): str(row["id"])
        for row in fleet_state.get("ships", [])
    }
    changed = False
    loadout_slots_by_ship = _cached_profile_loadout_slots_by_ship(events)
    observed_rolls: dict[tuple[str, int], int] = {}
    for event in events or []:
        if not isinstance(event, dict) or event.get("event") != "EngineerCraft":
            continue
        level = int(event.get("Level", 0) or 0)
        quality = float(event.get("Quality", 0) or 0)
        blueprint = JOURNAL_BLUEPRINT_NAMES.get(
            normalize(event.get("BlueprintName")),
            str(event.get("BlueprintName") or ""),
        )
        if level <= 0 or not blueprint or not 0 < quality < 0.999:
            continue
        key = (normalize(blueprint), level)
        observed_rolls[key] = max(
            observed_rolls.get(key, 0),
            max(1, min(5, round(1 / quality))),
        )
    for label, tasks in payload.items():
        ship_id = ids_by_label.get(str(label), "")
        slots = loadout_slots_by_ship.get(ship_id, [])
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
            target_name = normalize(first.get("Name"))
            learned_any = False
            for grade_record in task:
                if not isinstance(grade_record, dict) or grade_record.get("Grade") is None:
                    continue
                level = int(grade_record.get("Grade", 0) or 0)
                learned = observed_rolls.get((target_name, level), 0)
                if learned:
                    sources = planner.setdefault("roll_estimate_sources", {})
                    if (
                        int(grade_record.get("_Rolls", 0) or 0) != learned
                        or str(sources.get(str(level)) or "") != "journal_history"
                    ):
                        grade_record["_Rolls"] = learned
                        planner.setdefault("rolls", {})[str(level)] = learned
                        sources[str(level)] = "journal_history"
                        learned_any = True
            if learned_any:
                planner["estimated_total_rolls"] = sum(
                    int(row.get("_Rolls", 0) or 0)
                    for row in task if isinstance(row, dict)
                    and row.get("Grade") is not None
                )
                changed = True
            installed_at_slot = next((
                row for row in slots
                if str(row.get("slot") or "") == str(planner.get("slot") or "")
                and same_module_identity(
                    row.get("moduleId"), planner.get("module_id")
                )
            ), None)
            if installed_at_slot and first.get("Kind") != "ExperimentalEffect":
                installed_blueprint = str(
                    installed_at_slot.get("engineeringBlueprint") or ""
                )
                canonical_blueprint = JOURNAL_BLUEPRINT_NAMES.get(
                    normalize(installed_blueprint), installed_blueprint
                )
                blueprint_matches = bool(
                    canonical_blueprint and first.get("Name")
                    and normalize(canonical_blueprint) == normalize(first.get("Name"))
                )
                installed_grade = int(
                    installed_at_slot.get("engineeringGrade") or 0
                )
                target_grade = int(planner.get("target_grade", 0) or 0)
                installed_quality_known = bool(
                    installed_at_slot.get("engineeringQualityKnown")
                )
                installed_quality = max(0.0, min(1.0, float(
                    installed_at_slot.get("engineeringQuality") or 0
                )))
                if not blueprint_matches:
                    current_grade = 0
                elif installed_grade > target_grade:
                    current_grade = target_grade
                elif installed_quality_known:
                    current_grade = min(installed_grade, target_grade)
                else:
                    current_grade = max(
                        0, min(installed_grade, target_grade) - 1
                    )
                # Journal crafts are stronger evidence than Loadout. Only
                # initialize/reset progress while this plan has not accepted
                # any exact craft evidence yet.
                if not (planner.get("processed_crafts") or []):
                    if int(planner.get("current_grade", 0) or 0) != current_grade:
                        planner["current_grade"] = current_grade
                        planner["current_label"] = (
                            f"G{current_grade}" if current_grade
                            else "Not engineered"
                        )
                        changed = True
                    grade_progress = {}
                    crafts_completed = {}
                    if (
                        blueprint_matches and installed_grade > 0
                        and installed_quality_known
                        and installed_grade <= target_grade
                    ):
                        quality = installed_quality
                        grade_progress[str(installed_grade)] = quality
                        grade_record = next((
                            row for row in task if isinstance(row, dict)
                            and int(row.get("Grade", 0) or 0) == installed_grade
                        ), {})
                        planned_rolls = max(
                            1, int(grade_record.get("_Rolls", installed_grade) or installed_grade)
                        )
                        crafts_completed[str(installed_grade)] = max(
                            0, min(planned_rolls, round(quality * planned_rolls))
                        )
                    if planner.get("grade_progress", {}) != grade_progress:
                        planner["grade_progress"] = grade_progress
                        changed = True
                    if planner.get("crafts_completed", {}) != crafts_completed:
                        planner["crafts_completed"] = crafts_completed
                        changed = True
                if planner.get("experimental_id"):
                    effect_matches = _experimental_craft_matches(
                        planner,
                        {
                            "ExperimentalEffect": installed_at_slot.get(
                                "experimentalEffect"
                            )
                        },
                    )
                    if bool(planner.get("experimental_complete")) != effect_matches:
                        planner["experimental_complete"] = effect_matches
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
    return loadout_slots_by_ship



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
    target = int(planner.get("target_grade", 0) or 0)
    # Elite unlocks the next grade before the intermediate progress ring is
    # visually full. Do not budget another lower-grade roll once Journal
    # quality has crossed that usable boundary; the final target grade still
    # requires complete quality.
    completion_quality = 0.999 if level == target else 0.8
    if quality >= completion_quality:
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
    if 0 < quality < 0.999 and done > 0:
        # Quality is stronger evidence than a catalog estimate. Derive the
        # observed total roll count (for example 1 craft / 25% = 4 rolls).
        observed_total = max(done + 1, round(done / quality))
        planned = max(done, min(planned, observed_total))
    estimated_remaining = max(0, planned - done)
    if level == target:
        return max(1, estimated_remaining)
    return estimated_remaining



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
    task_rows = list(tasks or [])
    completed_experimental_plans = {
        str(planner.get("plan_id") or "")
        for task in task_rows
        if isinstance(task, list) and task and isinstance(task[0], dict)
        for planner in [task[0].get("_Planner", {})]
        if isinstance(planner, dict)
        and planner.get("experimental_complete")
        and planner.get("plan_id")
    }
    required = defaultdict(int)
    for task in task_rows:
        if not isinstance(task, list):
            continue
        first = next((item for item in task if isinstance(item, dict)), {})
        planner = first.get("_Planner", {})
        if (
            first.get("Kind") == "ExperimentalEffect"
            and (
                first.get("_Completed")
                or str(first.get("_ParentPlanId") or "")
                in completed_experimental_plans
                or (
                    isinstance(planner, dict)
                    and planner.get("experimental_complete")
                )
            )
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
        next_grade_record = min(
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
        estimate_sources = planner.get("roll_estimate_sources", {}) or {}
        active_grade = int(
            target_record.get("Grade", 0) or 0
        ) if target_record else 0
        live_grade_progress = float(
            (planner.get("grade_progress", {}) or {}).get(
                str(active_grade), 0
            ) or 0
        )
        roll_estimate_source = str(
            estimate_sources.get(str(active_grade)) or ""
        )
        roll_estimate_reliable = bool(
            target_status["code"] in {"completed", "experimental_pending"}
            or live_grade_progress > 0
            or roll_estimate_source == "journal_history"
        )
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
            "rollEstimateReliable": roll_estimate_reliable,
            "rollEstimateSource": (
                "live_progress" if live_grade_progress > 0
                else roll_estimate_source or "catalog_default"
            ),
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
            "craftsPlanned": sum(
                max(0, int(
                    (planner.get("crafts_completed", {}) or {}).get(
                        str(item.get("Grade")), 0
                    ) or 0
                )) + remaining_grade_rolls(planner, item)
                for item in task if isinstance(item, dict)
                and item.get("Grade") is not None
            ),
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
            "nextGrade": int(
                next_grade_record.get("Grade", 0) or 0
            ) if next_grade_record else 0,
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
    plan_mode="", journal_baseline=None, grade_progress=None,
    crafts_completed=None,
):
    """Build a Classic-compatible deterministic Rank-5 engineering task."""
    current_grade = max(0, int(current_grade or 0))
    target_grade = max(1, int(target_grade or 1))
    initial_progress = (
        deepcopy(grade_progress) if isinstance(grade_progress, dict) else {}
    )
    current_quality = float(
        initial_progress.get(str(current_grade), 0) or 0
    )
    start = (
        1 if current_grade <= 0 else
        current_grade if current_quality < 0.999 else
        current_grade + 1
    )
    plan = []
    rolls = {}
    for source in sorted(
        (value for value in grades or [] if isinstance(value, dict)),
        key=lambda value: int(value.get("Grade", 0) or 0),
    ):
        grade = int(source.get("Grade", 0) or 0)
        if start <= grade <= target_grade:
            record = deepcopy(source)
            planned_rolls = grade
            record["_Rolls"] = planned_rolls
            rolls[str(grade)] = planned_rolls
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
            "grade_progress": initial_progress,
            "crafts_completed": deepcopy(
                crafts_completed if isinstance(crafts_completed, dict) else {}
            ),
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
    module_id = module_identity_key(planner.get("module_id"))
    if ship_id and slot:
        return "bound", ship_id, slot.casefold(), module_id
    return (
        "unbound",
        str(planner.get("instance") or "Module 1").strip().casefold(),
    )



def task_signature(task):
    """Identify a plan by its physical target and requested outcome.

    Grade progress - which rolls remain, how much quality is already
    banked - is live state that shifts every time the module is crafted or
    an import is re-applied. Keying deduplication on it would treat the
    same wishlist target as a brand-new plan every time progress moves
    forward, instead of recognizing it as already tracked and skipping it.
    """
    if not isinstance(task, list) or not task:
        return ()
    first = task[0]
    planner = first.get("_Planner", {}) if isinstance(first, dict) else {}
    if first.get("Kind") == "ExperimentalEffect":
        if planner:
            # A standalone Experimental (build_experimental_plan) carries
            # its own full _Planner with a stable physical identity.
            anchor = planner_physical_identity(planner)
        elif first.get("_BoundShipId") and first.get("_BoundSlot"):
            anchor = (
                "bound", str(first.get("_BoundShipId")),
                str(first.get("_BoundSlot")).casefold(),
                module_identity_key(first.get("_BoundModuleId")),
            )
        else:
            # A combined-mode effect saved before _BoundShipId existed has
            # no stable anchor beyond its parent plan_id or instance label.
            anchor = ("legacy", first.get("_ParentPlanId") or planner.get("instance"))
        return ("experimental", first.get("ExperimentalId") or first.get("Name"), anchor)
    return (
        first.get("Type"),
        first.get("Name"),
        planner_physical_identity(planner),
        int(planner.get("target_grade") or 0),
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



def discard_bound_module_plans(tasks, ship_id, slots, module_id):
    """Remove plans bound to one accepted outfitting replacement."""
    wanted_ship = str(ship_id or "")
    wanted_slots = {
        str(slot or "").strip().casefold() for slot in (slots or []) if slot
    }
    wanted_module = canonical_module_id(module_id)
    if not wanted_ship or not wanted_slots or not wanted_module:
        return list(tasks or []), 0

    matched_plan_ids = set()
    matched_indexes = set()
    for index, task in enumerate(tasks or []):
        first = task[0] if isinstance(task, list) and task else {}
        planner = first.get("_Planner", {}) if isinstance(first, dict) else {}
        planner = planner if isinstance(planner, dict) else {}
        if (
            str(planner.get("ship_id") or "") == wanted_ship
            and str(planner.get("slot") or "").strip().casefold() in wanted_slots
            and same_module_identity(planner.get("module_id"), wanted_module)
        ):
            matched_indexes.add(index)
            plan_id = str(planner.get("plan_id") or "")
            if plan_id:
                matched_plan_ids.add(plan_id)

    kept = []
    for index, task in enumerate(tasks or []):
        first = task[0] if isinstance(task, list) and task else {}
        parent_id = (
            str(first.get("_ParentPlanId") or "")
            if isinstance(first, dict) else ""
        )
        if index in matched_indexes or (parent_id and parent_id in matched_plan_ids):
            continue
        kept.append(task)
    return kept, len(list(tasks or [])) - len(kept)



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
    if same_module_identity(planner.get("module_id"), event.get("Module")):
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



def _craft_matches_unique_equivalent_slot(
    planner: dict[str, Any], first: dict[str, Any], event: dict[str, Any],
    ship_id: object, tasks: list[Any],
) -> bool:
    """Allow a uniquely planned identical module to follow the crafted slot."""
    event_slot = str(event.get("Slot") or "")
    planned_slot = str(planner.get("slot") or "")
    if (
        not event_slot or not planned_slot or event_slot == planned_slot
        or planner.get("binding_required")
        or str(planner.get("ship_id") or "") != str(ship_id or "")
        or not same_module_identity(planner.get("module_id"), event.get("Module"))
    ):
        return False
    # Never steal a physical slot that already owns another primary plan.
    return not any(
        isinstance(task, list) and task and isinstance(task[0], dict)
        and task[0].get("Kind") != "ExperimentalEffect"
        and task[0].get("_Planner")
        and task[0]["_Planner"] is not planner
        and str(task[0]["_Planner"].get("slot") or "") == event_slot
        for task in tasks
    )



def _singular_effect_key(value: object) -> str:
    """Normalize an Experimental Effect name tolerant of a trailing plural.

    A Journal Loadout's ``ExperimentalEffect`` can name the same effect as
    the EngineerCraft catalog only with a trailing "s" added (Frontier
    reports "Super Capacitors" there; the catalog and every EngineerCraft
    event call it "Super Capacitor") - stripped here so that difference
    alone never reads as "a different effect is installed". Applies to
    every Experimental Effect this matches, not one module's exception.
    """
    key = normalize(value)
    return key[:-1] if len(key) > 1 and key.endswith("s") else key



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
        _singular_effect_key(value) for value in (
            journal_value, canonical_name,
            event.get("ExperimentalEffect_Localised"),
        ) if value
    }
    planner_keys = {
        _singular_effect_key(value) for value in (
            planner.get("experimental_id"),
            planner.get("experimental_name"),
        ) if value
    }
    return bool(event_keys.intersection(planner_keys))



def _grade_craft_matches_blueprint(
    first: dict[str, Any], event: dict[str, Any]
) -> bool:
    """Keep a physical slot match from changing the pinned blueprint target."""
    planned_name = str(first.get("Name") or "").strip()
    journal_name = str(event.get("BlueprintName") or "").strip()
    if not planned_name or not journal_name:
        return False
    canonical_journal_name = JOURNAL_BLUEPRINT_NAMES.get(
        normalize(journal_name), journal_name
    )
    return normalize(canonical_journal_name) == normalize(planned_name)



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
            # Older builds could persist a foreign same-slot craft here. Do
            # not let that poisoned observation prevent the intended
            # blueprint from repairing the plan on the next replay.
            poisoned_observation = bool(
                expected_name and not _grade_craft_matches_blueprint(
                    first, {"BlueprintName": expected_name}
                )
            )
            if (
                poisoned_observation
                and _grade_craft_matches_blueprint(first, event)
            ):
                for field in (
                    "blueprint_names", "blueprint_ids", "blueprint_sources",
                    "crafts_completed", "grade_progress",
                ):
                    values = planner.get(field, {}) or {}
                    if isinstance(values, dict):
                        values.pop(str(level), None)
                expected_name = ""
                expected_id = ""
            equivalent_slot = _craft_matches_unique_equivalent_slot(
                planner, first, event, ship_id, tasks
            )
            grade = next(
                (
                    item for item in task if isinstance(item, dict)
                    and int(item.get("Grade", 0) or 0) == level
                    # ShipID/slot/module identify the physical module, but do
                    # not prove that the crafted blueprint is the pinned one.
                    # A player can switch blueprints on that same module. In
                    # that case the foreign craft must remain unmatched so it
                    # cannot overwrite progress and material readiness for the
                    # intended plan.
                    and _grade_craft_matches_blueprint(first, event)
                    and (
                        _craft_can_bind(planner, first, event, ship_id)
                        or equivalent_slot
                    )
                    and (not expected_name or expected_name == str(event.get("BlueprintName") or ""))
                    and (
                        not expected_id
                        or expected_id == str(event.get("BlueprintID") or "")
                        or identity["status"] == "conflict"
                    )
                    # Multiple Engineers can offer the same blueprint. The
                    # selected Engineer is routing intent, not an exclusion;
                    # compatibility is sufficient after exact module identity.
                    and (
                        not engineer
                        or engineer in real_engineers(item)
                        # Frontier may include nicknames that are absent from
                        # the static catalog (for example Tod 'The Blaster'
                        # McQuinn). Exact ship/slot/module evidence is stronger
                        # than a display-name mismatch.
                        or _craft_matches_binding(
                            planner, event, ship_id,
                            first.get("Type") or planner.get("module_type"),
                        )
                        or equivalent_slot
                    )
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
                        or equivalent_slot
                    )
                ),
                None,
            )
            if grade:
                recorded_quality = float(
                    (planner.get("grade_progress", {}) or {}).get(
                        str(level), 0
                    ) or 0
                )
                # A newer Journal roll is authoritative progress even when a
                # stale catalog estimate already reached zero remaining.
                if (
                    remaining_grade_rolls(planner, grade) > 0
                    or float(event.get("Quality", 0) or 0) > recorded_quality
                ):
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
    chosen_first = tasks[index][0]
    equivalent_slot = _craft_matches_unique_equivalent_slot(
        planner, chosen_first, event, ship_id, tasks
    )
    if planner.get("binding_required") or equivalent_slot:
        planner.update({
            "ship_id": str(ship_id),
            "slot": str(event.get("Slot") or ""),
            "module_id": str(event.get("Module") or ""),
            "binding_required": False,
        })
        if equivalent_slot:
            planner["instance"] = str(event.get("Slot") or planner.get("instance") or "")
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
    # Successfully replayed evidence is no longer an unresolved diagnostic.
    diagnostics = [
        row for row in diagnostics
        if not isinstance(row, dict)
        or str(row.get("fingerprint") or "") not in acknowledged
    ]
    _write_json_if_changed(diagnostics_path, diagnostics)
    known = {str(row.get("fingerprint") or "") for row in diagnostics if isinstance(row, dict)}
    additions = [row for row in unresolved if row["fingerprint"] not in known]
    if additions:
        _write_json_if_changed(diagnostics_path, (diagnostics + additions)[-100:])
    return {
        "applied": applied,
        "unresolved": unresolved,
        "preferredPlanApplied": bool(preferred_plan_id and not preferred),
    }

