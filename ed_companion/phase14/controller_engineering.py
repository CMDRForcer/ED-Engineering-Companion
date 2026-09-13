"""Extracted from ed_companion/phase14/controller.py as part of the
controller.py modularization (no behavior change)."""

import json
import hashlib
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import requests
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtQuick import QQuickWindow

from ed_companion import APP_VERSION
from ed_companion.i18n import (
    DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, TranslationCatalog,
)
from ed_companion.persistence import atomic_write, load_json_file
from ed_companion.history_archive import HistoryArchive
from ed_companion.integrations.inara import (
    INARA_BATCH_WINDOW_SECONDS,
    INARA_MAX_REQUESTS_PER_MINUTE,
    INARA_MIN_REQUEST_INTERVAL_SECONDS,
    INARA_PENDING_EVENT_LIMIT,
    INARA_RATE_LIMIT_COOLDOWN_SECONDS,
    INARA_RETRY_BASE_SECONDS,
    INARA_RETRY_MAX_SECONDS,
    InaraError,
    community_goals_event,
    extract_community_goals,
    extract_profile_ships,
    material_event,
    prepare_journal_batch,
    profile_event,
    send_events,
)
from ed_companion.integrations.frontier_capi import (
    FRONTIER_CLIENT_ID,
    FRONTIER_REDIRECT_URI,
    FrontierAuthError,
    FrontierCapiClient,
    FrontierCapiError,
    build_pkce_authorization,
    exchange_authorization_code,
    parse_authorization_callback,
    project_profile_snapshot,
    refresh_frontier_tokens,
)
from ed_companion.integrations.frontier_credentials import (
    FrontierCredentialError,
    FrontierCredentialStore,
)
from ed_companion.build_import import (
    BuildImportError, JOURNAL_BLUEPRINT_NAMES, empty_build_import_preview,
    preview_build,
)
from ed_companion.loadout_export import build_loadout_export, write_loadout_export
from ed_companion.engineering import (
    build_unlock_guide,
    describe_engineering_effect,
    load_unlock_catalog,
)
from ed_companion.engineering.portraits import engineer_portrait_url
from ed_companion.integrations.eddn import (
    EDDN_PENDING_JOB_LIMIT,
    EDDN_REPLAY_DELAY_MS,
    EDDN_RELAY_URL,
    EddnError,
    EddnRelayDecodeError,
    decode_relay_frame,
    load_navroute_source,
    navroute_rejection_reason,
    prepare_event as prepare_eddn_event,
    prepare_station_snapshot,
    repair_legacy_prepared as repair_legacy_eddn_prepared,
    rebuild_context as rebuild_eddn_context,
    send as send_eddn_event,
    supports_event as supports_eddn_event,
    station_snapshot_mismatch_reason,
    should_log_station_rejection,
    should_log_rejection,
    schema_parity_report,
    update_context as update_eddn_context,
    upload_allowed as eddn_upload_allowed,
    validate_prepared as validate_eddn_prepared,
)
from ed_companion.navigation.hge import (
    apply_system_bgs_snapshot_batch,
    extract_signal_finds,
    extract_system_bgs_snapshot,
    infer_hge_materials,
    hge_match_class,
    is_hge_route_relevant,
    is_hge_material,
    merge_hge_observation_batch,
    partition_hge_observations,
    purge_legacy_signal_classifications,
    readable_faction_state,
    recent_unverified_hge_summary,
    rank_all_hge_sightings,
    rank_hge_candidate_systems,
    rank_state_find_systems,
)
from ed_companion.navigation.mining_finder import (
    fetch_spansh_system_dump,
    is_mining_commodity_signal,
    merge_mining_candidate_batch,
    mining_candidate_freshness,
    project_eddn_mining_candidates,
    project_spansh_mining_candidates,
)
from ed_companion.navigation.mining_commodities import (
    MINING_COMMODITIES,
    RHINO_SURFACE,
    mining_commodity_catalog,
    mining_commodity_id,
    mining_commodities_for_method,
)
from ed_companion.navigation.trader_type_cache import normalize_timestamp
from ed_companion.navigation.trader_search import (
    fetch_tech_broker_catalog_updates,
    fetch_trader_catalog_updates,
    merge_tech_broker_catalog,
    merge_trader_catalog,
    spansh_trader_type_evidence,
)
from ed_companion.navigation.trader_type_cache import TraderTypeCache
from ed_companion.trader_config import (
    SPANSH_MINIMUM_AGE_HOURS,
    SPANSH_TIMEOUT_SECONDS,
)
from ed_companion.services import (
    latest_delivery_proof,
    normalize_upload_queue,
    partition_upload_queue,
)
from ed_companion.diagnostics import filtered_log_lines
from ed_companion.exobiology import exobiology_distance_check

from .dashboard_views import (
    build_commander_cards,
    build_finance_history,
    build_finance_summary,
    build_interface_activity_feed,
    filter_finance_history,
    build_logbook_view,
    decorate_logbook_entry,
)

from .state import (
    active_profile_identity,
    attach_operation_experimental_effects,
    attach_operation_plan_context,
    assign_plans_to_nearest_engineers,
    blueprint_catalog,
    build_engineering_plan,
    build_experimental_plan,
    build_state,
    commander_status_credits,
    dismiss_craft_tracking_issue,
    dismiss_historical_craft_tracking_issues,
    dismiss_selected_craft_tracking_issues,
    discard_bound_module_plans,
    duplicate_ship_plan,
    engineering_run_preflight,
    journal_dir,
    journal_change_signature,
    journal_craft_baseline,
    journal_paths_for_profile,
    latest_profile_location,
    latest_loadout_slots,
    merge_capi_commander_overview,
    merge_capi_fleet,
    merge_capi_loadout,
    profiled_journal_events,
    ProfileContext,
    LOGBOOK_FILTERS,
    load_logbook_notes,
    logbook_entries,
    normalize,
    move_ship_plan,
    module_matches_type,
    partition_engineer_assignments,
    planner_mode,
    real_engineers,
    read_json,
    read_journal_tail_records,
    remove_ship_task,
    replace_ship_plan,
    runtime_data_dir,
    resolve_profile_context,
    set_journal_dir,
    reference_data_dir,
    select_operation_action,
    scope_operation_action_materials,
    set_prioritized_ship_plan,
    set_tech_broker_track,
    user_trader_catalog_path,
    write_logbook_note,
    write_ship_tasks,
)

LOGGER = logging.getLogger(__name__)

INARA_ACTIVE_RECEIPT_LIMIT = 100
from .controller_core import CoreControllerMixin
EDDN_ACTIVE_RECEIPT_LIMIT = 100
FRONTIER_REQUEST_WATCHDOG_MS = 120_000
COMMANDER_CARD_IDS = (
    "ranks", "major-reputation", "finances", "current-ship",
    "minor-reputation", "squadron",
)


class EngineeringMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_engineering() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    techBrokerSyncFinished = Signal(bool, str)


    def _load_tech_broker_sync_status(self):
        data = self._read_local_json(self.tech_broker_catalog_file, {})
        count = len(data.get("stations", [])) if isinstance(data, dict) else 0
        fetched = str(data.get("fetched_at") or "") if isinstance(data, dict) else ""
        return (
            f"Tech Broker cache · {count} nearby stations"
            + (f" · {fetched}" if fetched else " · update via Spansh")
        )


    def _engineer_index(self):
        return self._cached_derived(
            "engineer_index", self._state_revision,
            self._build_engineer_index,
        )


    def _build_engineer_index(self):
        coordinates = {
            **read_json(self._reference_data_dir / "system_coordinates.json", {}),
            **read_json(self._data_dir / "system_coordinates.json", {}),
        }
        origin = self._state.get("currentPosition", [])
        progress = self._state.get("engineerProgress", {})
        rows = {}
        for records in self._blueprint_groups.values():
            for record in records:
                module = str(
                    record.get("Type_Localised") or record.get("Type") or "Module"
                )
                blueprint = str(
                    record.get("Name_Localised") or record.get("Name")
                    or "Modification"
                )
                grade = int(record.get("Grade", 0) or 0)
                for name in real_engineers(record):
                    unlock_record = self._engineer_unlock_catalog.get(name, {})
                    row = rows.setdefault(name, {
                        "name": name,
                        "system": str(
                            unlock_record.get("system")
                            or ENGINEER_SYSTEMS.get(name, "System not stored")
                        ),
                        "station": str(
                            unlock_record.get("station")
                            or unlock_record.get("base") or ""
                        ),
                        "rank": 0,
                        "rankProgress": 0,
                        "status": "NO JOURNAL DATA",
                        "statusGroup": "unknown",
                        "maxGrade": 0,
                        "moduleCount": 0,
                        "blueprintCount": 0,
                        "_modules": set(),
                        "_blueprints": set(),
                    })
                    row["maxGrade"] = max(row["maxGrade"], grade)
                    row["_modules"].add(module)
                    row["_blueprints"].add(f"{module} · {blueprint}")
        for name, row in rows.items():
            journal = progress.get(name, {})
            status = str(journal.get("progress") or "No Journal data")
            rank = int(journal.get("rank", 0) or 0)
            lowered = status.casefold()
            group = (
                "unlocked" if lowered == "unlocked" or rank > 0
                else "invited" if lowered == "invited"
                else "known" if lowered == "known"
                else "locked" if lowered == "locked" else "unknown"
            )
            app_root = Path(__file__).resolve().parents[2]
            row.update({
                "rank": rank,
                "rankProgress": int(journal.get("rankProgress", 0) or 0),
                "status": status.upper(),
                "statusGroup": group,
                "moduleCount": len(row["_modules"]),
                "blueprintCount": len(row["_blueprints"]),
                "modules": sorted(row["_modules"], key=str.casefold),
                "blueprints": sorted(row["_blueprints"], key=str.casefold),
                "portraitUrl": engineer_portrait_url(app_root, name),
            })
            jobs = [
                plan for plan in self._state.get("blueprints", [])
                if name in {
                    value.strip()
                    for value in str(plan.get("engineer") or "").split(",")
                }
            ]
            row["openJobs"] = len(jobs)
            row["readyJobs"] = sum(
                1 for plan in jobs if float(plan.get("completion", 0) or 0) >= 1
            )
            target = coordinates.get(row["system"])
            distance = None
            if (
                isinstance(origin, list) and len(origin) == 3
                and isinstance(target, list) and len(target) == 3
            ):
                distance = math.sqrt(sum(
                    (float(left) - float(right)) ** 2
                    for left, right in zip(origin, target)
                ))
            row["distance"] = distance if distance is not None else -1.0
            row["coordinates"] = target if isinstance(target, list) else []
            row["unlockGuide"] = build_unlock_guide(
                name, group, progress, self._engineer_unlock_catalog,
                self._state.get("engineerUnlockSignals", {}),
            )
            row.pop("_modules", None)
            row.pop("_blueprints", None)
        order = {
            "unlocked": 0, "invited": 1, "known": 2,
            "unknown": 3, "locked": 4,
        }
        return sorted(
            rows.values(),
            key=lambda row: (
                order.get(row["statusGroup"], 9),
                row["distance"] < 0,
                row["distance"] if row["distance"] >= 0 else 0,
                row["name"].casefold(),
            ),
        )


    def _engineer_mission_route(self):
        revision = (self._state_revision, tuple(sorted(self._deferred_engineers)))
        return self._cached_derived(
            "engineer_mission_route", revision,
            self._build_engineer_mission_route,
        )


    def _build_engineer_mission_route(self):
        return self._engineer_assignment_routes()["route"]


    def _engineer_unlock_tasks(self):
        return self._engineer_assignment_routes()["unlocks"]


    def _engineer_assignment_routes(self):
        revision = (self._state_revision, tuple(sorted(self._deferred_engineers)))
        return self._cached_derived(
            "engineer_assignment_routes", revision,
            self._build_engineer_assignment_routes,
        )


    def _build_engineer_assignment_routes(self):
        assignments = assign_plans_to_nearest_engineers(
            self._state.get("blueprints", []),
            self._engineer_index(),
        )
        route, unlocks = partition_engineer_assignments(assignments)
        # Assignment already solves the complete route globally. Reordering it
        # here with a nearest-neighbour pass can reintroduce zig-zag flights.
        if self._deferred_engineers:
            route = [
                row for row in route
                if row.get("name") not in self._deferred_engineers
            ] + [
                row for row in route
                if row.get("name") in self._deferred_engineers
            ]

        def annotate(rows):
            origin = self._state.get("currentPosition")
            cumulative_distance = 0.0
            result = []
            for index, row in enumerate(rows, 1):
                target = row.get("coordinates")
                leg_distance = -1.0
                if (
                    isinstance(origin, list) and len(origin) == 3
                    and isinstance(target, list) and len(target) == 3
                ):
                    leg_distance = math.sqrt(sum(
                        (float(left) - float(right)) ** 2
                        for left, right in zip(origin, target)
                    ))
                    cumulative_distance += leg_distance
                    origin = target
                row["legDistance"] = leg_distance
                row["cumulativeDistance"] = (
                    cumulative_distance if leg_distance >= 0 else -1.0
                )
                result.append({
                    **row,
                    "sequence": index,
                    "summary": (
                        f"{row['openJobs']} job{'s' if row['openJobs'] != 1 else ''}"
                        f" · {row['readyJobs']} material-ready"
                        + (
                            f" · leg {leg_distance:.1f} ly"
                            f" · total {cumulative_distance:.1f} ly"
                            if leg_distance >= 0 else ""
                        )
                    ),
                })
            return result

        return {"route": annotate(route), "unlocks": annotate(unlocks)}


    planProgressStatus = Property(
        str, lambda self: str(self._get("planProgressStatus", "NOT STARTED")),
        notify=CoreControllerMixin.stateChanged,
    )


    craftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("craftTrackingIssues", []),
        notify=CoreControllerMixin.wishlistChanged,
    )


    freshCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("freshCraftTrackingIssues", []),
        notify=CoreControllerMixin.wishlistChanged,
    )


    historicalCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("historicalCraftTrackingIssues", []),
        notify=CoreControllerMixin.wishlistChanged,
    )


    relevantCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("relevantCraftTrackingIssues", []),
        notify=CoreControllerMixin.wishlistChanged,
    )


    unrelatedCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("unrelatedCraftTrackingIssues", []),
        notify=CoreControllerMixin.wishlistChanged,
    )


    techBrokerGuide = Property(
        "QVariantList", lambda self: self._get("techBrokerGuide", []),
        notify=CoreControllerMixin.operationsChanged,
    )


    techBrokerTrack = Property(
        "QVariantMap", lambda self: self._get("techBrokerTrack", {}),
        notify=CoreControllerMixin.operationsChanged,
    )


    engineerMissionRoute = Property(
        "QVariantList", lambda self: self._engineer_mission_route(),
        notify=CoreControllerMixin.operationsChanged,
    )


    engineerUnlockTasks = Property(
        "QVariantList", lambda self: self._engineer_unlock_tasks(),
        notify=CoreControllerMixin.operationsChanged,
    )


    engineeringRunPreflight = Property(
        "QVariantMap", lambda self: self._engineering_run_preflight(),
        notify=CoreControllerMixin.operationsChanged,
    )


    nextEngineerStop = Property(
        "QVariantMap",
        lambda self: (
            self._engineer_mission_route()[0]
            if self._engineer_mission_route() else {}
        ),
        notify=CoreControllerMixin.operationsChanged,
    )


    techBrokerSyncBusy = Property(
        bool, lambda self: self._tech_broker_sync_busy, notify=CoreControllerMixin.connectionChanged,
    )


    techBrokerSyncStatus = Property(
        str, lambda self: self._tech_broker_sync_status, notify=CoreControllerMixin.connectionChanged,
    )


    blueprintCatalog = Property(
        "QVariantList", lambda self: self._blueprint_catalog,
        notify=CoreControllerMixin.engineeringChanged,
    )


    selectedBlueprint = Property(
        "QVariantMap", lambda self: self._selected_blueprint,
        notify=CoreControllerMixin.engineeringChanged,
    )


    planMode = Property(str, lambda self: self._plan_mode, notify=CoreControllerMixin.engineeringChanged)


    canPinEngineeringPlan = Property(
        bool, lambda self: self._can_pin_engineering_plan(),
        notify=CoreControllerMixin.engineeringChanged,
    )


    selectedEngineer = Property(
        str, lambda self: self._selected_engineer, notify=CoreControllerMixin.engineeringChanged
    )


    engineeringStatus = Property(
        str, lambda self: self._engineering_status, notify=CoreControllerMixin.engineeringChanged
    )


    craftConfirmation = Property(
        str, lambda self: self._craft_confirmation, notify=CoreControllerMixin.engineeringChanged
    )


    def _can_pin_engineering_plan(self) -> bool:
        """Return whether the active plan mode has all mandatory inputs."""
        grades = self._blueprint_groups.get(self._selected_blueprint_id, [])
        if not grades or not self._selected_ship:
            return False
        if self._plan_mode in {"experimental_only", "combined"}:
            if not self._selected_experimental_id:
                return False
        if self._plan_mode in {"grade_only", "combined"}:
            installed_partial_target = bool(
                self._selected_blueprint.get("installedMatchesSelection")
                and self._selected_blueprint.get("installedQualityKnown")
                and int(self._selected_blueprint.get("installedGrade") or 0)
                == self._target_grade
                and float(self._selected_blueprint.get("installedQuality") or 0)
                < 0.999
            )
            if not installed_partial_target and not any(
                self._current_grade < int(row.get("Grade", 0) or 0)
                <= self._target_grade
                for row in grades if isinstance(row, dict)
            ):
                return False
        return self._plan_mode in {
            "grade_only", "experimental_only", "combined",
        }


    armedPlanId = Property(
        str, lambda self: self._armed_plan_id, notify=CoreControllerMixin.engineeringChanged
    )


    editingPlanIndex = Property(
        int, lambda self: self._editing_plan_index, notify=CoreControllerMixin.engineeringChanged
    )


    engineeringInstalledModules = Property(
        "QVariantList",
        lambda self: self._state.get("engineeringModuleSlots", []),
        notify=CoreControllerMixin.stateChanged,
    )


    engineeringShipSlots = Property(
        "QVariantList",
        lambda self: self._state.get("engineeringShipSlots", []),
        notify=CoreControllerMixin.stateChanged,
    )


    engineeringShipCatalog = Property(
        "QVariantList", lambda self: self._ship_catalog, constant=True,
    )


    @Slot()
    def clearCraftConfirmation(self):
        self.craftConfirmationTimer.stop()
        if self._craft_confirmation:
            self._craft_confirmation = ""
            self.engineeringChanged.emit()


    @Slot(str)
    def dismissCraftTrackingIssue(self, fingerprint):
        fingerprint = str(fingerprint or "").strip()
        selected_ship_id = str(self._state.get("selectedShipId") or "")
        if not dismiss_craft_tracking_issue(
            self._data_dir, fingerprint, selected_ship_id
        ):
            return
        self._state["craftTrackingIssues"] = [
            row for row in self._state.get("craftTrackingIssues", [])
            if str(row.get("fingerprint") or "") != fingerprint
        ]
        self._state["freshCraftTrackingIssues"] = [
            row for row in self._state.get("freshCraftTrackingIssues", [])
            if str(row.get("fingerprint") or "") != fingerprint
        ]
        self._state["historicalCraftTrackingIssues"] = [
            row for row in self._state.get("historicalCraftTrackingIssues", [])
            if str(row.get("fingerprint") or "") != fingerprint
        ]
        self._state["relevantCraftTrackingIssues"] = [
            row for row in self._state.get("relevantCraftTrackingIssues", [])
            if str(row.get("fingerprint") or "") != fingerprint
        ]
        self._state["unrelatedCraftTrackingIssues"] = [
            row for row in self._state.get("unrelatedCraftTrackingIssues", [])
            if str(row.get("fingerprint") or "") != fingerprint
        ]
        self._activity = "Unmatched Journal craft dismissed."
        self._publish_full_state()
        self.activityChanged.emit()
        self.refresh()


    @Slot()
    def dismissAllUnrelatedCraftIssues(self):
        selected_ship_id = str(self._state.get("selectedShipId") or "")
        fingerprints = [
            str(row.get("fingerprint") or "")
            for row in self._state.get("unrelatedCraftTrackingIssues", [])
            if row.get("fingerprint")
        ]
        count = dismiss_selected_craft_tracking_issues(
            self._data_dir, selected_ship_id, fingerprints
        )
        if count <= 0:
            return
        selected = set(fingerprints)
        self._state["craftTrackingIssues"] = [
            row for row in self._state.get("craftTrackingIssues", [])
            if str(row.get("fingerprint") or "") not in selected
        ]
        self._state["freshCraftTrackingIssues"] = [
            row for row in self._state.get("freshCraftTrackingIssues", [])
            if str(row.get("fingerprint") or "") not in selected
        ]
        self._state["unrelatedCraftTrackingIssues"] = []
        self._activity = f"Dismissed {count} unrelated Journal craft issue(s)."
        self._publish_full_state()
        self.activityChanged.emit()
        self.refresh()


    @Slot()
    def dismissAllHistoricalCraftIssues(self):
        selected_ship_id = str(self._state.get("selectedShipId") or "")
        count = dismiss_historical_craft_tracking_issues(
            self._data_dir, selected_ship_id
        )
        if count <= 0:
            return
        self._state["craftTrackingIssues"] = [
            row for row in self._state.get("craftTrackingIssues", [])
            if not row.get("historical")
        ]
        self._state["historicalCraftTrackingIssues"] = []
        self._activity = f"Dismissed {count} historical Journal craft issue(s)."
        self._publish_full_state()
        self.activityChanged.emit()
        self.refresh()


    @Slot(int, str)
    def movePinnedPlan(self, index, target_ship):
        if move_ship_plan(
            self._data_dir / "ship_blueprints.json",
            self._selected_ship, index, str(target_ship),
        ):
            self._fleet_status = f"Moved plan to {target_ship}."
            self.refresh()
        else:
            self._fleet_status = "Could not move this plan."
        self.engineeringChanged.emit()


    @Slot(str)
    def selectBlueprint(self, identifier):
        identifier = str(identifier or "")
        grades = sorted(
            self._blueprint_groups.get(identifier, []),
            key=lambda record: int(record.get("Grade", 0) or 0),
        )
        if not grades:
            return
        self.clearCraftConfirmation()
        module = str(grades[0].get("Type") or "Module")
        name = str(grades[0].get("Name") or "Blueprint")
        inventory = {
            row.get("key"): int(row.get("have", 0) or 0)
            for row in self._state.get("materials", [])
        }
        grade_rows = []
        for grade in grades:
            ingredients = []
            for item in grade.get("Ingredients", []) or []:
                key = normalize(item.get("Name"))
                need = int(item.get("Size", 1) or 1)
                have = inventory.get(key, 0)
                ingredients.append({
                    "name": str(item.get("Name") or key),
                    "need": need,
                    "have": have,
                    "missing": max(0, need - have),
                })
            guide = describe_engineering_effect(
                name, grade.get("Effects", [])
            )
            grade_rows.append({
                "grade": int(grade.get("Grade", 0) or 0),
                "ingredients": ingredients,
                "description": guide["summary"],
                "benefits": guide["benefits"],
                "tradeoffs": guide["tradeoffs"],
                "effects": [
                    {
                        "property": str(effect.get("Property") or ""),
                        "effect": str(effect.get("Effect") or ""),
                        "good": bool(effect.get("IsGood")),
                    }
                    for effect in (grade.get("Effects", []) or [])
                    if isinstance(effect, dict)
                ],
            })
        compatible = []
        wanted = module.casefold()
        for effect in self._experimentals:
            module_types = [
                str(value).casefold()
                for value in (effect.get("ModuleTypes", []) or [])
            ]
            if wanted not in module_types:
                continue
            guide = describe_engineering_effect(
                str(effect.get("Name") or "Experimental"),
                effect.get("Effects", []),
                experimental=True,
            )
            compatible.append({
                "id": str(effect.get("ExperimentalId") or effect.get("Name")),
                "name": str(effect.get("Name") or "Experimental"),
                "engineers": ", ".join(
                    str(value) for value in (effect.get("Engineers", []) or [])
                    if value and not str(value).startswith("@")
                ),
                "description": guide["summary"],
                "benefits": guide["benefits"],
                "tradeoffs": guide["tradeoffs"],
            })
        engineer_names = sorted({
            str(engineer)
            for grade in grades
            for engineer in (grade.get("Engineers", []) or [])
            if engineer and not str(engineer).startswith("@")
        })
        progress = self._state.get("engineerProgress", {})
        self._selected_blueprint_id = identifier
        self._editing_grade_complete = False
        self._selected_experimental_id = ""
        self._plan_mode = "grade_only"
        installed_rows = {
            str(row.get("slot") or ""): row
            for row in self._state.get("engineeringModuleSlots", [])
            if isinstance(row, dict)
        }
        compatible_slots = []
        for row in self._state.get("moduleSlots", []):
            if not module_matches_type(row.get("moduleId"), module):
                continue
            candidate = dict(row)
            candidate["slotLabel"] = str(
                installed_rows.get(str(row.get("slot") or ""), {}).get(
                    "displaySlot"
                ) or row.get("slot") or ""
            )
            compatible_slots.append(candidate)
        # Only exact module-type candidates are safe binding choices. Unknown
        # catalog identities remain visibly unbound instead of exposing the
        # complete ship Loadout and inviting a wrong manual selection.
        self._module_slot_options = compatible_slots
        if len(self._module_slot_options) == 1:
            self._selected_module_slot = str(
                self._module_slot_options[0].get("slot") or ""
            )
            self._selected_module_id = str(
                self._module_slot_options[0].get("moduleId") or ""
            )
        else:
            self._selected_module_slot = ""
            self._selected_module_id = ""
        self._current_grade = 0
        self._target_grade = max(int(value.get("Grade", 0) or 0) for value in grades)
        engineer_options = [
            {
                "name": engineer,
                "system": ENGINEER_SYSTEMS.get(engineer, "System not stored"),
                "capabilityGrade": max(
                    int(grade.get("Grade", 0) or 0) for grade in grades
                    if engineer in real_engineers(grade)
                ),
                "unlockState": str(
                    progress.get(engineer, {}).get("progress") or "No Journal data"
                ),
                "commanderRank": int(
                    progress.get(engineer, {}).get("rank", 0) or 0
                ),
            }
            for engineer in engineer_names
        ]

        def engineer_priority(option):
            capability = int(option.get("capabilityGrade", 0) or 0)
            rank = int(option.get("commanderRank", 0) or 0)
            unlocked = str(option.get("unlockState") or "").casefold() == "unlocked"
            return (
                capability < self._target_grade,
                not unlocked,
                bool(rank and rank < self._target_grade),
                -capability,
                str(option.get("name") or "").casefold(),
            )

        preferred_engineer = min(
            engineer_options, key=engineer_priority, default={}
        )
        self._selected_engineer = str(preferred_engineer.get("name") or "")
        matching_instances = sum(
            1 for row in self._state.get("blueprints", [])
            if row.get("module") == module and row.get("editable")
        )
        self._editing_plan_index = -1
        self._module_instance = f"Module {matching_instances + 1}"
        self._selected_blueprint = {
            "id": identifier,
            "module": module,
            "name": name,
            "maxGrade": self._target_grade,
            "engineers": ", ".join(engineer_names),
            "engineerOptions": engineer_options,
            "grades": grade_rows,
            "experimentals": compatible,
        }
        self._apply_installed_slot_engineering()
        self._engineering_status = "Choose current grade, target grade and optional experimental."
        self.engineeringChanged.emit()


    def _apply_installed_slot_engineering(self) -> None:
        """Project authoritative Loadout engineering onto the selected plan."""
        selected = next(
            (
                row for row in self._module_slot_options
                if str(row.get("slot") or "") == self._selected_module_slot
            ),
            {},
        )
        raw_blueprint = str(selected.get("engineeringBlueprint") or "")
        installed_name = JOURNAL_BLUEPRINT_NAMES.get(
            normalize(raw_blueprint), raw_blueprint.replace("_", " ")
        )
        installed_grade = int(selected.get("engineeringGrade") or 0)
        installed_quality = max(0.0, min(1.0, float(
            selected.get("engineeringQuality") or 0
        )))
        installed_quality_known = bool(
            selected.get("engineeringQualityKnown")
        )
        selected_name = str(self._selected_blueprint.get("name") or "")
        matches = bool(
            installed_grade > 0 and installed_name and selected_name
            and normalize(installed_name) == normalize(selected_name)
        )
        self._selected_blueprint.update({
            "installedEngineeringKnown": installed_grade > 0,
            "installedBlueprint": installed_name,
            "installedGrade": installed_grade,
            "installedQuality": installed_quality,
            "installedQualityKnown": installed_quality_known,
            "installedQualityPercent": round(installed_quality * 100),
            "installedRemainingRolls": max(
                0,
                installed_grade - round(installed_quality * installed_grade),
            ) if installed_quality_known else 0,
            "installedExperimentalEffect": str(
                selected.get("experimentalEffect") or ""
            ),
            "installedMatchesSelection": matches,
        })
        if not matches:
            self._current_grade = 0
        elif installed_grade > self._target_grade:
            self._current_grade = self._target_grade
        elif installed_quality_known:
            self._current_grade = min(installed_grade, self._target_grade)
        else:
            self._current_grade = max(
                0, min(installed_grade, self._target_grade) - 1
            )


    def _engineering_run_preflight(self):
        return self._cached_derived(
            "engineering_run_preflight", (
                self._state_revision, tuple(sorted(self._deferred_engineers))
            ),
            lambda: engineering_run_preflight(
                self._state,
                self._engineer_mission_route() + self._engineer_unlock_tasks(),
            ),
        )


    @Slot(str)
    def setPlanMode(self, mode: str) -> None:
        selected = str(mode or "")
        if selected not in {"grade_only", "experimental_only", "combined"}:
            return
        self.clearCraftConfirmation()
        self._plan_mode = selected
        if selected == "grade_only":
            self._selected_experimental_id = ""
        self._engineering_status = {
            "grade_only": "Grade target only.",
            "experimental_only": "Experimental Effect only; no Grade target required.",
            "combined": "Grade target followed by Experimental Effect.",
        }[selected]
        self.engineeringChanged.emit()


    @Slot(str)
    def setSelectedEngineer(self, engineer):
        self._selected_engineer = str(engineer or "")
        option = next(
            (
                value for value in self._selected_blueprint.get("engineerOptions", [])
                if value.get("name") == self._selected_engineer
            ),
            {},
        )
        state = str(option.get("unlockState") or "No Journal data")
        rank = int(option.get("commanderRank", 0) or 0)
        if state.casefold() not in {"unlocked", "no journal data"}:
            self._engineering_status = (
                f"{self._selected_engineer}: {state}. You can plan now, "
                "but must unlock this engineer before crafting."
            )
        elif rank and self._target_grade > rank:
            self._engineering_status = (
                f"{self._selected_engineer} is currently Rank {rank}; "
                f"the G{self._target_grade} target requires more reputation."
            )
        else:
            self._engineering_status = (
                f"{self._selected_engineer} selected · capable to "
                f"G{int(option.get('capabilityGrade', 0) or 0)}."
            )
        self.engineeringChanged.emit()


    @Slot(int)
    def editPinnedPlan(self, index):
        tasks = read_json(
            self._data_dir / "ship_blueprints.json", {}
        ).get(self._selected_ship, [])
        if not (0 <= int(index) < len(tasks)):
            return
        task = tasks[int(index)]
        if not isinstance(task, list) or not task:
            return
        first = task[0]
        planner = first.get("_Planner", {})
        mode = planner_mode(planner)
        if first.get("Kind") == "ExperimentalEffect":
            if mode != "experimental_only":
                return
            identifier = str(planner.get("blueprint_group_id") or "")
            if not identifier:
                return
        else:
            identifier = f"{first.get('Type', '')}\u241f{first.get('Name', '')}"
        self.selectBlueprint(identifier)
        self._editing_plan_index = int(index)
        self._plan_mode = mode
        self._module_instance = str(planner.get("instance") or "Module 1")
        self._current_grade = int(planner.get("current_grade", 0) or 0)
        self._target_grade = int(
            planner.get("target_grade", self._target_grade) or self._target_grade
        )
        self._selected_experimental_id = str(
            planner.get("experimental_id") or ""
        )
        self._selected_module_slot = str(planner.get("slot") or "")
        self._selected_module_id = str(planner.get("module_id") or "")
        progress = planner.get("grade_progress", {}) or {}
        target = int(planner.get("target_grade", 0) or 0)
        self._editing_grade_complete = mode != "experimental_only" and (
            float(progress.get(str(target), 0) or 0) >= 0.999
        )
        selected = first.get("_SelectedEngineer", {})
        if selected.get("name"):
            self._selected_engineer = str(selected["name"])
        self._engineering_status = (
            f"Editing {self._module_instance}. Save replaces this plan."
        )
        self.engineeringChanged.emit()


    @Slot()
    def cancelPlanEdit(self):
        self._editing_plan_index = -1
        self._editing_grade_complete = False
        self._engineering_status = "Edit cancelled. New plans will be appended."
        self.engineeringChanged.emit()


    @Slot(int)
    def duplicatePinnedPlan(self, index):
        self.clearCraftConfirmation()
        if duplicate_ship_plan(
            self._data_dir / "ship_blueprints.json", self._selected_ship, index,
            journal_craft_baseline(
                profiled_journal_events(), self._state.get("selectedShipId", "")
            ),
        ):
            self._engineering_status = "Plan duplicated as a separate module."
            self.refresh()
            self.engineeringChanged.emit()


    @Slot(str)
    def armPlanForNextCraft(self, plan_id):
        plan_id = str(plan_id or "")
        self._armed_plan_id = "" if self._armed_plan_id == plan_id else plan_id
        self._engineering_status = (
            "Automatic matching enabled."
            if not self._armed_plan_id else
            "This module is selected for the next matching Journal craft."
        )
        self.engineeringChanged.emit()


    @Slot(str)
    def prioritizePinnedPlan(self, plan_id):
        self.clearCraftConfirmation()
        if set_prioritized_ship_plan(
            self._data_dir / "ship_blueprints.json",
            self._selected_ship,
            str(plan_id or ""),
        ):
            self._engineering_status = "Track-now priority updated."
            self.refresh()
            self.engineeringChanged.emit()


    @Slot(str, str)
    def trackTechBrokerUnlock(self, name, broker_subtype):
        name = str(name or "").strip()
        active = self._state.get("techBrokerTrack", {})
        clear = name and str(active.get("name") or "") == name
        if set_tech_broker_track(
            self._data_dir / "tech_broker_track.json",
            "" if clear else name,
            "" if clear else str(broker_subtype or ""),
        ):
            self._engineering_status = (
                "Tech Broker material priority cleared."
                if clear else
                "Tech Broker unlock is now tracked with material priority."
            )
            self.refresh()
            self.engineeringChanged.emit()


    @Slot()
    def pinEngineeringPlan(self):
        self.clearCraftConfirmation()
        grades = self._blueprint_groups.get(self._selected_blueprint_id, [])
        ship = self._selected_ship
        if not grades or not ship:
            self._engineering_status = "Select a blueprint and ship first."
            self.engineeringChanged.emit()
            return
        old_plan_id = ""
        old_journal_baseline = None
        if self._editing_plan_index >= 0:
            tasks = read_json(
                self._data_dir / "ship_blueprints.json", {}
            ).get(ship, [])
            if self._editing_plan_index < len(tasks) and tasks[self._editing_plan_index]:
                old_plan_id = str(
                    tasks[self._editing_plan_index][0]
                    .get("_Planner", {}).get("plan_id") or ""
                )
                old_journal_baseline = deepcopy(
                    tasks[self._editing_plan_index][0]
                    .get("_Planner", {}).get("journal_baseline") or {}
                )
        selected_effect = next(
            (
                value for value in self._experimentals
                if str(value.get("ExperimentalId") or value.get("Name"))
                == self._selected_experimental_id
            ),
            None,
        )
        if self._plan_mode in {"experimental_only", "combined"} and not selected_effect:
            self._engineering_status = "Select an Experimental Effect first."
            self.engineeringChanged.emit()
            return
        binding = {
            "ship_id": self._state.get("selectedShipId", ""),
            "slot": self._selected_module_slot,
            "module_id": self._selected_module_id,
        }
        plan_baseline = old_journal_baseline or journal_craft_baseline(
            profiled_journal_events(), binding["ship_id"]
        )
        if self._plan_mode == "experimental_only":
            plan = build_experimental_plan(
                selected_effect or {}, plan_id=old_plan_id,
                instance=self._module_instance, current_grade=self._current_grade,
                module_type=str(self._selected_blueprint.get("module") or ""),
                blueprint_group_id=self._selected_blueprint_id,
                journal_baseline=plan_baseline, **binding,
            )
        else:
            installed_grade = int(
                self._selected_blueprint.get("installedGrade") or 0
            )
            installed_quality = float(
                self._selected_blueprint.get("installedQuality") or 0
            )
            installed_quality_known = bool(
                self._selected_blueprint.get("installedQualityKnown")
            )
            installed_matches = bool(
                self._selected_blueprint.get("installedMatchesSelection")
            )
            initial_progress = {}
            initial_completed = {}
            if (
                installed_matches and installed_quality_known
                and 0 < installed_grade <= self._target_grade
            ):
                planned_rolls = installed_grade
                initial_progress[str(installed_grade)] = installed_quality
                initial_completed[str(installed_grade)] = max(
                    0, min(
                        planned_rolls,
                        round(installed_quality * planned_rolls),
                    ),
                )
            plan = build_engineering_plan(
                grades, self._current_grade, self._target_grade,
                plan_id=old_plan_id, instance=self._module_instance,
                experimental_id=(
                    self._selected_experimental_id if self._plan_mode == "combined" else ""
                ),
                experimental_name=(
                    str(selected_effect.get("Name") or "")
                    if selected_effect and self._plan_mode == "combined" else ""
                ),
                plan_mode=self._plan_mode, journal_baseline=plan_baseline,
                grade_progress=initial_progress,
                crafts_completed=initial_completed,
                **binding,
            )
        if not plan:
            self._engineering_status = "No unfinished grades in this range."
            self.engineeringChanged.emit()
            return
        if self._selected_engineer and self._plan_mode != "experimental_only":
            plan[0]["_SelectedEngineer"] = {
                "name": self._selected_engineer,
                "system": ENGINEER_SYSTEMS.get(
                    self._selected_engineer, "System not stored"
                ),
            }
        tasks_to_add = [plan]
        experimental_task = None
        if self._plan_mode == "combined":
            effect = next(
                (
                    deepcopy(value) for value in self._experimentals
                    if str(value.get("ExperimentalId") or value.get("Name"))
                    == self._selected_experimental_id
                ),
                None,
            )
            if effect:
                effect["Kind"] = "ExperimentalEffect"
                effect["Grade"] = None
                effect["_ParentPlanId"] = plan[0]["_Planner"]["plan_id"]
                # A stable anchor independent of the parent plan_id (fresh
                # every save), so pinning the same module again after
                # progress advances is recognized as the same target.
                effect["_BoundShipId"] = str(binding.get("ship_id") or "")
                effect["_BoundSlot"] = str(binding.get("slot") or "")
                effect["_BoundModuleId"] = str(binding.get("module_id") or "")
                experimental_task = [effect]
                tasks_to_add.append(experimental_task)
        if self._editing_plan_index >= 0:
            saved = replace_ship_plan(
                self._data_dir / "ship_blueprints.json", ship,
                self._editing_plan_index, plan, experimental_task,
            )
            self._engineering_status = (
                f"Updated {self._module_instance} ({self._plan_mode})." if saved
                else "Could not update this plan."
            )
            self._editing_plan_index = -1
        else:
            added = write_ship_tasks(
                self._data_dir / "ship_blueprints.json",
                ship,
                tasks_to_add,
            )
            self._engineering_status = (
                f"Pinned {self._module_instance} ({self._plan_mode}) to {ship}."
                if added else "This engineering plan is already pinned."
            )
        self.refresh()
        self.engineeringChanged.emit()


    @Slot(int)
    def removePinnedPlan(self, index):
        self.clearCraftConfirmation()
        if remove_ship_task(
            self._data_dir / "ship_blueprints.json",
            self._selected_ship,
            index,
        ):
            self._engineering_status = "Pinned plan removed."
            self.refresh()
            self.engineeringChanged.emit()


    @Slot(int)
    def acceptInstalledForPlan(self, index):
        """Explicitly discard a conflicting target in favour of Loadout."""
        row = next((
            value for value in self._state.get("blueprints", [])
            if int(value.get("index", -1)) == int(index)
        ), {})
        if not row.get("targetConflict"):
            self._engineering_status = (
                "Installed state was not accepted: no verified target conflict."
            )
            self.engineeringChanged.emit()
            return
        if remove_ship_task(
            self._data_dir / "ship_blueprints.json",
            self._selected_ship, int(index),
        ):
            self._engineering_status = (
                f"Installed {row.get('installedBlueprint', 'engineering')} "
                "accepted; the conflicting target was removed."
            )
            self.refresh()
            self.engineeringChanged.emit()


    @Slot()
    def updateTechBrokerCatalog(self):
        if self._tech_broker_sync_busy:
            return
        position = self._state.get("currentPosition") or []
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            self._tech_broker_sync_status = (
                "Cannot update: no current three-dimensional Journal position."
            )
            self.connectionChanged.emit()
            return
        reference = tuple(float(value) for value in position)
        request_context = {
            "request_id": uuid.uuid4().hex,
            "profile_key": self.profile_context.key,
            "path_generation": self._profile_generation,
            "catalog_path": str(self.tech_broker_catalog_file.resolve()),
        }
        self._tech_broker_sync_busy = True
        self._tech_broker_sync_status = (
            "Querying Spansh for nearby Human and Guardian Tech Brokers…"
        )
        self.connectionChanged.emit()

        def worker():
            try:
                result = fetch_tech_broker_catalog_updates(
                    reference, post=requests.post,
                    timeout=SPANSH_TIMEOUT_SECONDS, size=100,
                )
                if not result.get("stations"):
                    errors = "; ".join(
                        f"{key}: {value}"
                        for key, value in result.get("errors", {}).items()
                    )
                    raise LookupError(errors or "No valid Tech Broker rows returned")
                self.techBrokerSyncFinished.emit(True, json.dumps({
                    "request": request_context, "result": result,
                }))
            except Exception as exc:
                self.techBrokerSyncFinished.emit(False, json.dumps({
                    "request": request_context,
                    "error": f"{type(exc).__name__}: {exc}",
                }))

        self._start_network_worker(worker, "tech-broker-catalog-sync")


    @Slot(bool, str)
    def _finish_tech_broker_catalog_sync(self, success, payload):
        self._tech_broker_sync_busy = False
        try:
            envelope = json.loads(payload)
            request_context = envelope["request"]
            target_path = Path(request_context["catalog_path"])
            current_request = (
                request_context.get("profile_key") == self.profile_context.key
                and request_context.get("path_generation") == self._profile_generation
                and target_path == self.tech_broker_catalog_file.resolve()
            )
        except (KeyError, TypeError, ValueError):
            LOGGER.error("Tech Broker Spansh completion has no valid request context")
            return
        if not success:
            if not current_request:
                LOGGER.warning(
                    "Discarded stale Tech Broker status for Spansh request %s",
                    request_context.get("request_id", ""),
                )
                return
            self._tech_broker_sync_status = (
                "Spansh update failed · bundled recommendations remain active · "
                f"{envelope.get('error', 'unknown error')}"
            )
            self.connectionChanged.emit()
            return
        try:
            result = envelope["result"]
            existing = self._read_local_json(target_path, {})
            rows = merge_tech_broker_catalog(
                existing.get("stations", []) if isinstance(existing, dict) else [],
                result.get("stations", []),
            )
            document = {
                "source": "Spansh live Technology Broker station search",
                "fetched_at": result.get("fetched_at"),
                "reference_coords": result.get("reference_coords"),
                "stations": rows,
            }
            if not self._persist_json(
                target_path, document, "Tech Broker catalog"
            ):
                raise OSError("Tech Broker catalog could not be saved to disk")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            if not current_request:
                LOGGER.error(
                    "Stale Tech Broker Spansh result could not be saved to %s: %s",
                    target_path, exc,
                )
                return
            self._tech_broker_sync_status = (
                f"Live results received, but local merge failed · {exc}"
            )
            self.connectionChanged.emit()
            return
        if not current_request:
            LOGGER.info(
                "Saved stale Tech Broker Spansh request %s to original profile %s",
                request_context.get("request_id", ""), target_path,
            )
            return
        errors = result.get("errors", {})
        self._tech_broker_sync_status = (
            f"Tech Broker catalog updated · {len(rows)} nearby stations"
            + (" · partial: " + ", ".join(sorted(errors)) if errors else "")
        )
        self.refresh()
        self.connectionChanged.emit()


    @Slot()
    def deferNextEngineer(self):
        route = self._engineer_mission_route()
        if len(route) < 2:
            self._activity = "No alternative engineer stop is available."
        else:
            engineer = route[0].get("name")
            self._deferred_engineers.add(engineer)
            if len(self._deferred_engineers) >= len(route):
                self._deferred_engineers.clear()
            self._activity = f"Moved {engineer} to later in this session."
            self.operationsChanged.emit()
        self.activityChanged.emit()
