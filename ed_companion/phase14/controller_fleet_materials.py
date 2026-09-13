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


class FleetMaterialsMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_fleet_materials() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    materialSelectionChanged = Signal()


    def _load_fleet_images(self):
        loaded = load_json_file(self.fleet_images_file, {}, encoding="utf-8")
        return {
            str(ship_id): str(filename)
            for ship_id, filename in (
                loaded.items() if isinstance(loaded, dict) else []
            )
            if str(ship_id).strip() and str(filename).strip()
        }


    def _save_fleet_images(self):
        return self._persist_json(
            self.fleet_images_file, self._fleet_images, "Fleet images"
        )


    @Slot(str, str, result=bool)
    def setFleetShipImage(self, ship_id, source):
        ship_id = str(ship_id or "").strip()
        source_path = Path(QUrl(str(source or "")).toLocalFile())
        if (
            not ship_id or not source_path.is_file()
            or source_path.suffix.casefold()
            not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        ):
            return False
        try:
            self.fleet_images_dir.mkdir(parents=True, exist_ok=True)
            safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", ship_id)[:64] or "ship"
            filename = (
                f"{safe_id}-{uuid.uuid4().hex[:10]}"
                f"{source_path.suffix.casefold()}"
            )
            shutil.copy2(source_path, self.fleet_images_dir / filename)
            previous = dict(self._fleet_images)
            self._fleet_images[ship_id] = filename
            if not self._save_fleet_images():
                self._fleet_images = previous
                return False
        except OSError as exc:
            LOGGER.error(
                "Fleet image import failed for ShipID %s: %s", ship_id, exc
            )
            return False
        self.fleetChanged.emit()
        return True


    @Slot(str, result=bool)
    def clearFleetShipImage(self, ship_id):
        ship_id = str(ship_id or "").strip()
        if ship_id not in self._fleet_images:
            return True
        previous = dict(self._fleet_images)
        self._fleet_images.pop(ship_id, None)
        if not self._save_fleet_images():
            self._fleet_images = previous
            return False
        self.fleetChanged.emit()
        return True


    def _hge_material_filters(self):
        self._hge_candidate_rows()
        if self._hge_material_filter_cache is not None:
            return self._hge_material_filter_cache
        names = set()
        for row in self._hge_candidate_cache_rows:
            for name in str(row.get("materials") or "").split(","):
                name = name.strip()
                if name and "not predictable" not in name.casefold():
                    names.add(name)
        self._hge_material_filter_cache = (
            ["ALL HGE MATERIALS"] + sorted(names, key=str.casefold)
        )
        return self._hge_material_filter_cache


    def _material_source_routes(self, material):
        """Add live, distance-sorted collection routes to a material card."""
        routes = [dict(card) for card in material.get("sourceCards", [])]
        # HGE farming is only a valid direct or farm-and-trade route for
        # materials in the standard Manufactured Material Trader table.
        # Thargoid, Guardian and other special manufactured materials share
        # the broad journal category but cannot be obtained through that table.
        if not is_hge_route_relevant(material):
            return routes

        name = str(material.get("name") or material.get("key") or "material")
        live_rows = [
            row for row in self._hge_finder_rows()
            if not row.get("selfTest") and row.get("system")
        ]
        direct_rows = [
            row for row in live_rows
            if name.casefold() in str(row.get("materials") or "").casefold()
        ]
        target = (direct_rows or live_rows or [{}])[0]
        if not target:
            candidates = [
                row for row in self._hge_candidate_rows()
                if name.casefold() in str(row.get("materials") or "").casefold()
            ] or self._hge_candidate_rows()
            if not candidates:
                return routes
            candidate = candidates[0]
            distance = float(candidate.get("distance", -1) or -1)
            location = str(candidate.get("system") or "")
            if distance >= 0:
                location += f" · {distance:.1f} ly"
            routes.insert(0, {
                "kind": "HGE_CANDIDATE",
                "label": "NEAREST HGE CANDIDATE · SCAN REQUIRED",
                "detail": (
                    f"{location} · last community report "
                    f"{candidate.get('lastReportedMinutes', 0)} min ago · "
                    f"{candidate.get('states')}. Possible contents: "
                    f"{candidate.get('materials')}. Farm those standard "
                    f"Manufactured materials and exchange them for {name} at "
                    "a Manufactured Material Trader. Jump there, scan the Nav "
                    "Beacon or use the FSS; the app will then show locally "
                    "verified signals and their real lifetime."
                ),
                "system": str(candidate.get("system") or ""),
                "distanceLy": distance,
                "candidateOnly": True,
                "verified": False,
            })
            return routes

        direct = bool(direct_rows)
        distance = float(target.get("distance", -1) or -1)
        location = str(target.get("system") or "")
        if distance >= 0:
            location += f" · {distance:.1f} ly"
        location += f" · {int(target.get('remainingMinutes', 0) or 0)} min left"
        probable = str(target.get("materials") or "Contents not predictable")
        if direct:
            instruction = (
                f"Likely direct source for {name}. Scan the system Nav Beacon, "
                "enter the HGE and collect with limpets."
            )
            label = "NEAREST LIVE HGE · DIRECT SOURCE"
        else:
            trader = material.get("trader") or {}
            trader_route = ""
            if trader.get("system"):
                trader_route = (
                    f" Then trade at {trader.get('station') or 'the trader'} "
                    f"in {trader.get('system')}."
                )
            instruction = (
                f"Collect the listed high-grade Manufactured materials, then "
                f"exchange them for {name} at a Manufactured Material Trader."
                f"{trader_route}"
            )
            label = "NEAREST LIVE HGE · FARM & TRADE"
        routes.insert(0, {
            "kind": "LIVE_HGE",
            "label": label,
            "detail": (
                f"{location} · {target.get('faction') or 'Unknown faction'} · "
                f"{target.get('state') or 'Unknown state'}. "
                f"Likely contents: {probable}. {instruction}"
            ),
            "system": str(target.get("system") or ""),
            "distanceLy": distance,
            "remainingMinutes": int(target.get("remainingMinutes", 0) or 0),
            "live": True,
            "verified": direct,
        })
        return routes


    fleetKnown = Property(
        bool, lambda self: bool(self._get("fleetKnown", False)), notify=CoreControllerMixin.stateChanged
    )


    materialStatus = Property(
        str, lambda self: str(self._get("materialStatus", "MISSING")),
        notify=CoreControllerMixin.stateChanged,
    )


    hgeMaterialFilters = Property(
        "QStringList", lambda self: self._hge_material_filters(),
        notify=CoreControllerMixin.hgeChanged,
    )


    selectedMaterial = Property(
        "QVariantMap",
        lambda self: self._selected_material,
        notify=materialSelectionChanged,
    )


    fleetStatus = Property(
        str, lambda self: self._fleet_status, notify=CoreControllerMixin.engineeringChanged
    )


    moduleInstance = Property(
        str, lambda self: self._module_instance, notify=CoreControllerMixin.engineeringChanged
    )


    selectedModuleSlot = Property(
        str, lambda self: self._selected_module_slot, notify=CoreControllerMixin.engineeringChanged
    )


    moduleSlotOptions = Property(
        "QVariantList", lambda self: self._module_slot_options,
        notify=CoreControllerMixin.engineeringChanged,
    )


    @Slot(str)
    def selectMaterial(self, key):
        self._selected_material = next(
            (
                dict(row) for row in self._state.get("materials", [])
                if row.get("key") == str(key)
            ),
            {},
        )
        if self._selected_material:
            self._selected_material["sourceCards"] = (
                self._material_source_routes(self._selected_material)
            )
        self.materialSelectionChanged.emit()


    @Slot()
    def clearSelectedMaterial(self):
        self._selected_material = {}
        self.materialSelectionChanged.emit()


    @Slot(str)
    def setModuleInstance(self, label):
        self.clearCraftConfirmation()
        value = str(label or "").strip()
        self._module_instance = value[:48] or "Module 1"
        self.engineeringChanged.emit()


    @Slot(str)
    def setSelectedModuleSlot(self, slot):
        self.clearCraftConfirmation()
        selected = next(
            (row for row in self._module_slot_options if row.get("slot") == slot),
            {},
        )
        self._selected_module_slot = str(selected.get("slot") or "")
        self._selected_module_id = str(selected.get("moduleId") or "")
        self._apply_installed_slot_engineering()
        self.engineeringChanged.emit()
