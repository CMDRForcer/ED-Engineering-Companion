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



def _engineer_leg_distance(left, right):
    """Return an exact inter-Engineer distance when both coordinates exist."""
    left_position = left.get("coordinates")
    right_position = right.get("coordinates")
    if not (
        isinstance(left_position, list) and len(left_position) == 3
        and isinstance(right_position, list) and len(right_position) == 3
    ):
        return None
    return math.sqrt(sum(
        (float(a) - float(b)) ** 2
        for a, b in zip(left_position, right_position)
    ))



def _shortest_engineer_route(names, engineer_index):
    """Solve the open Hamiltonian route from the current Journal position."""
    ordered_names = sorted(set(names), key=str.casefold)
    if len(ordered_names) < 2:
        return ordered_names, (
            max(0.0, float(engineer_index[ordered_names[0]].get("distance", 0) or 0))
            if ordered_names else 0.0
        )
    count = len(ordered_names)
    # (visited mask, final index) -> (distance, path tuple). Unknown legs are
    # penalised but remain routable instead of silently dropping a stop.
    states = {}
    current_indices = [
        index for index, name in enumerate(ordered_names)
        if 0 <= float(
            engineer_index[name].get("distance")
            if engineer_index[name].get("distance") is not None else -1
        ) < 0.05
        and bool(engineer_index[name].get("statusGroup") == "unlocked")
    ]
    start_indices = current_indices or list(range(count))
    for index in start_indices:
        name = ordered_names[index]
        start_value = engineer_index[name].get("distance")
        start = float(start_value if start_value is not None else -1)
        states[(1 << index, index)] = (
            start if start >= 0 else 1_000_000.0, (index,)
        )
    for mask in range(1, 1 << count):
        for last in range(count):
            current = states.get((mask, last))
            if current is None:
                continue
            for nxt in range(count):
                if mask & (1 << nxt):
                    continue
                leg = _engineer_leg_distance(
                    engineer_index[ordered_names[last]],
                    engineer_index[ordered_names[nxt]],
                )
                candidate = (
                    current[0] + (leg if leg is not None else 1_000_000.0),
                    current[1] + (nxt,),
                )
                key = (mask | (1 << nxt), nxt)
                if key not in states or candidate < states[key]:
                    states[key] = candidate
    full_mask = (1 << count) - 1
    distance, path = min(
        value for (mask, _last), value in states.items()
        if mask == full_mask
    )
    return [ordered_names[index] for index in path], distance



def _minimum_engineer_cover(candidate_sets, engineer_index):
    """Minimize stops first and the complete route distance second."""
    requirements = [set(values) for values in candidate_sets if values]
    if not requirements:
        return set()
    best = None

    def engineer_order(name):
        row = engineer_index.get(name, {})
        distance_value = row.get("distance")
        distance = float(distance_value if distance_value is not None else -1)
        return (
            distance < 0,
            distance if distance >= 0 else 0,
            str(name).casefold(),
        )

    def search(chosen, remaining):
        nonlocal best
        if not remaining:
            candidate = set(chosen)
            route, distance = _shortest_engineer_route(
                candidate, engineer_index
            )
            score = (len(candidate), distance, tuple(
                name.casefold() for name in route
            ))
            if best is None or score < best[0]:
                best = (score, route)
            return
        if best is not None and len(chosen) >= best[0][0]:
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
    return best[1] if best else []



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
        next_grade = int(plan.get("nextGrade", 0) or target_grade)
        usable = [
            name for name in eligible
            if engineer_index[name].get("statusGroup") == "unlocked"
            and int(engineer_index[name].get("rank", 0) or 0) >= next_grade
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
                if 0 <= float(
                    engineer_index[name].get("distance")
                    if engineer_index[name].get("distance") is not None else -1
                ) < 0.05
            ]
            if current:
                candidates = current
            elif selected in candidates:
                candidates = [selected]
        elif selected in candidates:
            candidates = [selected]
        prepared.append((
            plan, target_grade, next_grade, candidates, bool(usable)
        ))

    cover_route = _minimum_engineer_cover(
        [
            candidates
            for _plan, _target, _next, candidates, _usable in prepared
        ],
        engineer_index,
    )
    cover = set(cover_route)
    route_rank = {name: index for index, name in enumerate(cover_route)}
    assignments = {}
    for plan, target_grade, next_grade, candidates, craftable in prepared:
        covered = [name for name in candidates if name in cover]
        selection = covered or candidates
        selection.sort(key=lambda name: (
            route_rank.get(name, len(route_rank)), str(name).casefold()
        ))
        chosen = engineer_index[selection[0]]
        rank = int(chosen.get("rank", 0) or 0)
        block_reason = "" if craftable else (
            f"Engineer access/rank insufficient: requires unlocked G{next_grade} now "
            f"for progressive target G{target_grade}, "
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
        if craftable and rank < target_grade:
            bucket["progressiveRankUp"] = True
            bucket["progressiveTargetGrade"] = max(
                int(bucket.get("progressiveTargetGrade", 0) or 0),
                target_grade,
            )
    return sorted(assignments.values(), key=lambda row: (
        route_rank.get(row["name"], len(route_rank)),
        str(row["name"]).casefold(),
    ))



def partition_engineer_assignments(assignments: object) -> tuple[list[dict], list[dict]]:
    """Separate executable travel from future access work and re-route both."""
    rows = [dict(row) for row in (assignments or []) if isinstance(row, dict)]
    engineer_index = {
        str(row.get("name") or ""): row for row in rows if row.get("name")
    }

    def ordered(craftable: bool) -> list[dict]:
        names = [
            str(row.get("name") or "") for row in rows
            if bool(row.get("craftable")) == craftable and row.get("name")
        ]
        route, _distance = _shortest_engineer_route(names, engineer_index)
        return [engineer_index[name] for name in route]

    return ordered(True), ordered(False)



def engineer_options_for_plan(plan, engineer_rows, blueprint_records=None):
    """List every Engineer capable of this blueprint at the target Grade."""
    plan = plan or {}
    target = int(plan.get("targetGrade", 0) or plan.get("grade", 0) or 0)
    next_grade = int(plan.get("nextGrade", 0) or target)
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
        elif rank >= next_grade and rank < target:
            code, text = "rank_progression", "RANK UP HERE"
        elif rank < next_grade:
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
            "craftable": code in {"craftable", "rank_progression"},
            "distance": float(row.get("distance", -1) or -1),
            "portraitUrl": str(row.get("portraitUrl") or ""),
        })
    order = {
        "craftable": 0, "rank_progression": 1,
        "rank_too_low": 2, "unlock_required": 3,
    }
    return sorted(options, key=lambda row: (
        order.get(row["status"], 9),
        -int(row["rank"]),
        row["distance"] < 0,
        row["distance"] if row["distance"] >= 0 else 0,
        row["name"].casefold(),
    ))

