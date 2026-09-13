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

HGE_OBSERVATION_LIMIT = 10000
BGS_OBSERVATION_BATCH_SECONDS = 30
MINING_OBSERVATION_BATCH_SECONDS = 30
EDDN_ACTIVE_RECEIPT_LIMIT = 100
INARA_ACTIVE_RECEIPT_LIMIT = 100
MINING_TRANSIENT_FIELDS = frozenset({
    "ageSeconds", "confirmationStatus", "freshnessLimitSeconds",
    "recheckRecommended", "stale",
})
HGE_CLASSIFIER_VERSION = 2
THEME_IDS = frozenset({
    "arctic_alloy", "navy", "neon_vector", "orbital_dawn",
    "crimson_dark", "crimson_light",
})
LEGACY_THEME_IDS = frozenset({
    "imperial_gold", "thargoid_amber", "midnight", "black",
    "teal_void", "federal_steel", "cupcake_nebula",
})
COMMANDER_CARD_IDS = (
    "ranks", "major-reputation", "finances", "current-ship",
    "minor-reputation", "squadron",
)
NAVIGATION_IDS = (
    "operations", "engineering", "wishlist", "engineers", "materials",
    "mining-finder", "state-finds", "powerplay", "cmdr", "logbook",
    "exobiology", "settings",
)
LEGACY_DEFAULT_NAVIGATION_ORDERS = {
    (
        "operations", "engineering", "wishlist", "engineers", "materials",
        "state-finds", "cmdr", "logbook", "settings", "powerplay",
    ),
    (
        "operations", "engineering", "wishlist", "engineers", "materials",
        "state-finds", "mining-finder", "cmdr", "logbook", "settings", "powerplay",
    ),
    (
        "operations", "engineering", "wishlist", "engineers", "materials",
        "state-finds", "cmdr", "logbook", "settings", "powerplay", "mining-finder",
    ),
}


def initial_navigation_order(configured):
    order = list(dict.fromkeys(
        str(item) for item in list(configured or [])
        if str(item) in NAVIGATION_IDS
    ))
    if not order or tuple(order) in LEGACY_DEFAULT_NAVIGATION_ORDERS:
        return list(NAVIGATION_IDS)
    order.extend(item for item in NAVIGATION_IDS if item not in order)
    return order


def state_with_live_location(state, location):
    """Apply an exact Journal location without waiting for a full state build."""
    if not isinstance(location, dict):
        return state, False
    system = str(location.get("system") or "").strip()
    position = location.get("currentPosition")
    if (
        not system or not isinstance(position, (list, tuple))
        or len(position) != 3
    ):
        return state, False
    try:
        position = [float(value) for value in position]
    except (TypeError, ValueError):
        return state, False
    if not all(math.isfinite(value) for value in position):
        return state, False
    current = dict(state or {})
    changed = (
        str(current.get("system") or "").strip() != system
        or list(current.get("currentPosition") or []) != position
    )
    if not changed:
        return state, False
    current.update({
        "system": system,
        "currentPosition": position,
        "currentSystemAddress": location.get("currentSystemAddress"),
    })
    return current, True


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

from .controller_core import CoreControllerMixin

from .dashboard_views import (
    build_commander_cards,
    build_finance_history,
    build_finance_summary,
    build_interface_activity_feed,
    filter_finance_history,
    build_logbook_view,
    decorate_logbook_entry,
)

from .controller_commander import CommanderMixin
from .controller_eddn import EddnMixin
from .controller_logbook import LogbookMixin
from .controller_exobiology import ExobiologyMixin
from .controller_frontier_capi import FrontierCapiMixin
from .controller_inara import InaraMixin

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

ENGINEER_SYSTEMS = {
    "Felicity Farseer": "Deciat", "Elvira Martuuk": "Khun",
    "The Dweller": "Wyrd", "Tod McQuinn": "Wolf 397",
    "Liz Ryder": "Eurybia", "Hera Tani": "Kuwemaki",
    "Broo Tarquin": "Muang", "Selene Jean": "Kuk",
    "Didi Vatermann": "Leesti", "Lei Cheung": "Laksak",
    "Marco Qwent": "Sirius", "Ram Tah": "Meene",
    "The Sarge": "Beta-3 Tucani", "Tiana Fortune": "Achenar",
    "Bill Turner": "Alioth", "Juri Ishmaak": "Giryak",
    "Zacariah Nemo": "Yoru", "Lori Jameson": "Shinrarta Dezhra",
    "Professor Palin": "Arque", "Chloe Sedesi": "Shenve",
    "Colonel Bris Dekker": "Sol", "Mel Brandon": "Luchtaine",
    "Etienne Dorn": "Los", "Marsha Hicks": "Tir",
    "Petra Olmanova": "Asura",
}

LOGGER = logging.getLogger(__name__)

# Upper bound on how long the Frontier CAPI tab may sit in its busy state
# before the UI is released, in case a worker never reports back. Chosen
# above the worst realistic single request: a 60 s CAPI rate-limit wait plus
# the 25 s transport timeout, with margin.
FRONTIER_REQUEST_WATCHDOG_MS = 120_000


def _last_complete_json_record(path: Path) -> dict[str, Any]:
    """Read only the final newline-complete Journal record."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        if end <= 0:
            return {}
        handle.seek(end - 1)
        if handle.read(1) not in {b"\n", b"\r"}:
            return {}
        position = end
        buffer = b""
        while position > 0:
            chunk_size = min(65536, position)
            position -= chunk_size
            handle.seek(position)
            buffer = handle.read(chunk_size) + buffer
            lines = buffer.splitlines()
            if position == 0 or len(lines) >= 2:
                line = lines[-1] if lines else b""
                if line:
                    record = json.loads(line.decode("utf-8-sig", errors="replace"))
                    return record if isinstance(record, dict) else {}
        return {}


def _eddn_relay_relevant(payload: Any) -> bool:
    """Keep only relay frames consumed by State Finds or Mining Finder."""
    if not isinstance(payload, dict):
        return False
    schema = str(payload.get("$schemaRef") or "").casefold()
    if "/fsssignaldiscovered/" in schema:
        return True
    if "/fssbodysignals/" in schema:
        return True
    message = payload.get("message")
    return (
        "/journal/1" in schema
        and isinstance(message, dict)
        and str(message.get("event") or "") in {
            "FSDJump", "Location", "CarrierJump", "Scan", "SAASignalsFound",
        }
    )


class CockpitController(
    CommanderMixin, EddnMixin, ExobiologyMixin, FrontierCapiMixin, InaraMixin,
    LogbookMixin, CoreControllerMixin, QObject,
):
    materialsChanged = Signal()
    hgeChanged = Signal()
    miningChanged = Signal()
    miningSyncFinished = Signal(object)
    miningCatalogLoaded = Signal(object)
    miningRowsReady = Signal(object)
    journalHealthChanged = Signal()
    diagnosticsChanged = Signal()
    rendererChanged = Signal()
    activityChanged = Signal()
    materialSelectionChanged = Signal()
    traderSyncFinished = Signal(bool, str)
    techBrokerSyncFinished = Signal(bool, str)
    historyExportFinished = Signal(object)
    startupStateReady = Signal(object)
    startupStateFailed = Signal(object)
    refreshStateReady = Signal(object)
    refreshStateFailed = Signal(object)
    exitRequested = Signal()
    restartRequested = Signal()

    def _bind_profile_paths(self, context: ProfileContext) -> None:
        """Bind every Commander-local controller file to one context."""
        self.profile_context = context
        self.config_dir = runtime_data_dir(context)
        self._data_dir = self.config_dir
        self.config_file = self.config_dir / "phase14_graphics.json"
        self.inara_config_file = self.config_dir / "inara_config.json"
        self.inara_receipts_file = self.config_dir / "inara_receipts.json"
        self.inara_journal_cache_file = self.config_dir / "inara_journal_cache.json"
        self.frontier_credentials_file = (
            self.config_dir / "frontier_credentials.dat"
        )
        self.frontier_config_file = self.config_dir / "frontier_config.json"
        self.eddn_config_file = self.config_dir / "eddn_config.json"
        self.eddn_queue_file = self.config_dir / "community_upload_queue.json"
        self.eddn_quarantine_file = self.config_dir / "community_upload_quarantine.json"
        self.eddn_cursor_file = self.config_dir / "eddn_journal_cursor.json"
        self.hge_cache_file = self.config_dir / "hge_live_sightings.json"
        self.trader_catalog_file = user_trader_catalog_path(context)
        self.tech_broker_catalog_file = self.config_dir / "tech_broker_catalog_user.json"
        self.mining_catalog_file = self.config_dir / "mining_finder_catalog.json"
        self.history_archive_file = self.config_dir / "data_history.sqlite3"
        self.fleet_images_file = self.config_dir / "fleet_images.json"
        self.fleet_images_dir = self.config_dir / "fleet_images"

    def __init__(self):
        super().__init__()
        self.package_root = Path(__file__).resolve().parents[2]
        self.profile_context = resolve_profile_context()
        self._bind_profile_paths(self.profile_context)
        self._history_archive = HistoryArchive(self.history_archive_file)
        self._commander_credit_snapshots = self._history_archive.records(
            "commander_credit_snapshots"
        )
        self._history_export_busy = False
        self.historyExportFinished.connect(self._finish_history_export)
        self._profile_generation = 1
        ui_config = self._load_ui_config()
        configured_language = str(
            ui_config.get("interface_language") or DEFAULT_LANGUAGE
        ).casefold()
        self._interface_language = (
            configured_language
            if configured_language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
        )
        self._translations = TranslationCatalog(
            self.package_root / "ed_data" / "i18n"
        )
        self._renderer_mode = str(ui_config.get("renderer_mode") or "auto")
        if self._renderer_mode not in {"auto", "gpu", "software"}:
            self._renderer_mode = "auto"
        self._ui_scale = max(
            1.00,
            min(1.50, float(ui_config.get("ui_scale", 1.15) or 1.15)),
        )
        configured_theme = str(ui_config.get("theme") or "navy").lower()
        if configured_theme in LEGACY_THEME_IDS:
            configured_theme = "navy"
        self._theme = (
            configured_theme if configured_theme in THEME_IDS else "navy"
        )
        self._reduced_motion = bool(ui_config.get("reduced_motion", False))
        self._commander_update_popups = bool(
            ui_config.get("commander_update_popups", True)
        )
        preview_popups = os.environ.get("PHASE14_PREVIEW_POPUPS")
        if preview_popups in {"0", "1"}:
            self._commander_update_popups = preview_popups == "1"
        self._enhanced_visuals = bool(
            ui_config.get("enhanced_visuals", True)
        )
        preview_enhanced = os.environ.get("PHASE14_PREVIEW_ENHANCED")
        if preview_enhanced in {"0", "1"}:
            self._enhanced_visuals = preview_enhanced == "1"
        self._onboarding_complete = bool(
            ui_config.get("onboarding_complete", False)
        )
        if os.environ.get("PHASE14_PREVIEW_SKIP_ONBOARDING") == "1":
            self._onboarding_complete = True
        self._debug_mode = bool(ui_config.get("debug_mode", False))
        self._journal_auto = bool(ui_config.get("journal_auto", True))
        self._background_mode = bool(ui_config.get("background_mode", False))
        self._autostart_enabled = bool(ui_config.get("autostart_enabled", False))
        self._trader_preference = str(
            ui_config.get("trader_preference") or "confirmed"
        ).casefold()
        if self._trader_preference not in {"confirmed", "nearest"}:
            self._trader_preference = "confirmed"
        self._system_tray_available = False
        self._background_runtime_status = "WINDOW OPEN"
        self._shutdown_complete = False
        self._network_threads = set()
        self._network_threads_lock = threading.Lock()
        self._last_page = max(0, min(12, int(ui_config.get("last_page", 0) or 0)))
        configured_cards = ui_config.get("commander_card_order", [])
        configured_cards = configured_cards if isinstance(configured_cards, list) else []
        self._commander_card_order = list(dict.fromkeys(
            card for card in configured_cards if card in COMMANDER_CARD_IDS
        ))
        self._commander_card_order.extend(
            card for card in COMMANDER_CARD_IDS
            if card not in self._commander_card_order
        )
        self._commander_finance_period = "session"
        configured_navigation = ui_config.get("navigation_order", [])
        configured_navigation = (
            configured_navigation if isinstance(configured_navigation, list) else []
        )
        self._navigation_order = initial_navigation_order(configured_navigation)
        self._renderer_active = self._detect_renderer()
        self._restart_required = False
        self._state = {}
        self._state_revision = 0
        self._hge_revision = 0
        self._eddn_revision = 0
        self._connection_revision = 0
        self._derived_cache = {}
        self._fleet_images = self._load_fleet_images()
        self.connectionChanged.connect(self._invalidate_connection_cache)
        self.connectionChanged.connect(self.commanderCardsChanged.emit)
        self.stateChanged.connect(self.commanderCardsChanged.emit)
        self.hgeChanged.connect(self._invalidate_hge_cache)
        self.operationsChanged.connect(self._invalidate_operations_cache)
        self._selected_ship = ""
        # Keep the engineering mission visible while the Commander temporarily
        # flies a cargo or taxi ship. The UI can explicitly follow the live ship.
        self._follow_active_ship = False
        self._selected_material = {}
        self._selected_blueprint = {}
        self._selected_blueprint_id = ""
        self._selected_experimental_id = ""
        self._plan_mode = "grade_only"
        self._selected_engineer = ""
        self._current_grade = 0
        self._target_grade = 5
        self._engineering_status = "Select a blueprint."
        self._craft_confirmation = ""
        self._last_consistency_signature = ()
        self._refresh_revision = 0
        self._refresh_in_flight = False
        self._refresh_dirty = False
        self._journal_state_ready = False
        self._armed_plan_id = ""
        self._editing_plan_index = -1
        self._editing_grade_complete = False
        self._module_instance = "Module 1"
        self._selected_module_slot = ""
        self._selected_module_id = ""
        self._module_slot_options = []
        self._build_import_preview = empty_build_import_preview()
        self._build_import_target = ""
        self._fleet_status = "Fleet ready."
        self._deferred_engineers = set()
        self._init_logbook()
        self._init_inara()
        self._init_frontier_capi()
        self._eddn_profile_identity = self.profile_context.identity
        self._eddn_profile_key = self.profile_context.key
        self._eddn_journal_root = self.profile_context.journal_root
        self._eddn_config = self._load_eddn_config()
        self._eddn_queue = self._load_eddn_queue()
        self._hge_sightings = self._read_local_json(self.hge_cache_file, [])
        if not isinstance(self._hge_sightings, list):
            self._hge_sightings = []
        self._hge_file_lock = threading.Lock()
        self._hge_save_sequence = 0
        self._hge_save_sequences = {}
        # The mature Mining catalog can exceed 50 MB. It is loaded by the
        # existing startup worker so JSON parsing never delays the first frame.
        self._mining_catalog = {"candidates": []}
        self._mining_file_lock = threading.Lock()
        self._mining_save_sequence = 0
        self._mining_save_sequences = {}
        self._mining_catalog_load_token = 0
        self._mining_rows_build_token = 0
        self._mining_rows_build_in_flight = False
        self._mining_rows_build_dirty = False
        self.miningCatalogLoaded.connect(self._finish_mining_catalog_load)
        self.miningRowsReady.connect(self._finish_mining_rows_build)
        self._start_mining_catalog_load()
        self._mining_sync_busy = False
        self._mining_sync_status = "Ready"
        self._active_mining_request = None
        self.miningSyncFinished.connect(self._finish_mining_sync)
        self_test_count = sum(
            1 for row in self._hge_sightings
            if isinstance(row, dict) and row.get("self_test")
        )
        if self_test_count:
            self._hge_sightings = [
                row for row in self._hge_sightings
                if not (isinstance(row, dict) and row.get("self_test"))
            ]
            self._save_hge_cache()
        if int(self._eddn_config.get("hge_classifier_version", 0) or 0) < HGE_CLASSIFIER_VERSION:
            retained, removed = purge_legacy_signal_classifications(
                self._hge_sightings
            )
            removed_rows = [
                row for row in self._hge_sightings
                if isinstance(row, dict) and (
                    str(row.get("evidence_kind") or "") in {
                        "EDDN_SIGNAL", "LOCAL_JOURNAL", "ENTERED",
                    }
                    or int(row.get("time_remaining", 0) or 0) > 0
                )
            ]
            if not removed or self._archive_history(
                "hge_observations", removed_rows
            ):
                self._hge_sightings = retained
                self._eddn_config["hge_classifier_version"] = HGE_CLASSIFIER_VERSION
                self._save_hge_cache()
                self._save_eddn()
        self._hge_candidate_cache_key = None
        self._hge_candidate_cache_rows = []
        self._hge_material_filter_cache = ["ALL HGE MATERIALS"]
        self._next_hge_expiry_epoch = self._hge_next_expiry_epoch(
            self._hge_sightings
        )
        self._eddn_context = {}
        self._load_eddn_cursor_state()
        self._station_rejections: dict[str, str] = {}
        self._navroute_rejections: dict[str, str] = {}
        self._eddn_profile_paths_signature = None
        self._eddn_profile_paths_cache = []
        # Full Journal context is projected by the startup worker below.
        self._eddn_context = {}
        self._eddn_busy = False
        self._eddn_status = self._eddn_initial_status(
            self._eddn_config.get("consent")
        )
        self._save_eddn()
        self._eddn_listener_status = "Disabled"
        self._state_find_refresh_status = "NOT REFRESHED THIS SESSION"
        self._trader_sync_busy = False
        self._trader_sync_status = self._load_trader_sync_status()
        self._tech_broker_sync_busy = False
        self._tech_broker_sync_status = self._load_tech_broker_sync_status()
        self._eddn_stop = threading.Event()
        self._eddn_thread = None
        self._pending_bgs_snapshots = []
        self._pending_hge_observations = []
        self._pending_mining_candidates = []
        self._last_bgs_batch_monotonic = time.monotonic()
        self._last_mining_batch_monotonic = time.monotonic()
        self._last_hge_batch_stats = {
            "bgsApplied": 0, "signalsMerged": 0, "expiredRemoved": 0,
        }
        self._last_state_find_refresh_stats = {
            "refreshedAt": "", "bgsApplied": 0,
            "signalsMerged": 0, "expiredRemoved": 0,
        }
        self.eddnFinished.connect(self._finish_eddn)
        self.eddnRelay.connect(self._accept_eddn_relay)
        self.traderSyncFinished.connect(self._finish_trader_catalog_sync)
        self.techBrokerSyncFinished.connect(self._finish_tech_broker_catalog_sync)
        self._data_dir = runtime_data_dir(self.profile_context)
        self._reference_data_dir = reference_data_dir(self.package_root)
        self._ship_catalog = read_json(
            self._reference_data_dir / "ships.json", []
        )
        self._engineer_unlock_catalog = load_unlock_catalog(
            self._data_dir, self.package_root
        )
        self._blueprint_catalog = blueprint_catalog(self._reference_data_dir)
        self._blueprint_groups = {}
        for record in read_json(self._reference_data_dir / "blueprints.json", []):
            if (
                isinstance(record, dict)
                and record.get("Grade") is not None
                and real_engineers(record)
            ):
                key = f"{record.get('Type', '')}\u241f{record.get('Name', '')}"
                self._blueprint_groups.setdefault(key, []).append(record)
        self._experimentals = [
            record for record in read_json(
                self._reference_data_dir / "experimental_effects.json", []
            )
            if isinstance(record, dict)
        ]
        self._init_exobiology()
        self._activity = "Connecting to Elite Journal…"
        self._last_journal_stamp = None
        self._last_commander_status_stamp = None
        self.startupStateReady.connect(self._finish_startup_state)
        self.startupStateFailed.connect(self._fail_startup_state)
        self.refreshStateReady.connect(self._finish_refresh_state)
        self.refreshStateFailed.connect(self._fail_refresh_state)
        self._start_initial_state_load()
        self.timer = QTimer(self)
        self.timer.setInterval(1200)
        self.timer.timeout.connect(self.pollJournal)
        self.timer.start()
        self.refreshDebounceTimer = QTimer(self)
        self.refreshDebounceTimer.setInterval(180)
        self.refreshDebounceTimer.setSingleShot(True)
        self.refreshDebounceTimer.timeout.connect(self._launch_state_refresh)
        self.craftConfirmationTimer = QTimer(self)
        self.craftConfirmationTimer.setInterval(5500)
        self.craftConfirmationTimer.setSingleShot(True)
        self.craftConfirmationTimer.timeout.connect(self.clearCraftConfirmation)
        self.hgeBatchTimer = QTimer(self)
        self.hgeBatchTimer.setInterval(3000)
        self.hgeBatchTimer.timeout.connect(self.flushHgeObservationBatch)
        self.hgeBatchTimer.start()
        self._ensure_eddn_listener()

    def _start_initial_state_load(self):
        """Build the initial Journal state without blocking the Qt GUI thread."""
        revision = self._refresh_revision
        profile_generation = self._profile_generation
        package_root = self.package_root
        selected_ship = self._selected_ship
        profile_key = self.profile_context.key
        profile_identity = self.profile_context.identity
        hge_sightings = list(self._hge_sightings)
        eddn_queue = list(self._eddn_queue)
        eddn_config = dict(self._eddn_config)
        hge_revision = self._hge_revision
        eddn_revision = self._eddn_revision
        trader_preference = self._trader_preference

        # Threading contract: the worker runs off the Qt thread and must read
        # only the locals captured above, never live ``self._*`` mutable state.
        def worker():
            try:
                state = build_state(
                    package_root, selected_ship,
                    trader_preference=trader_preference,
                )
                state["_logbookEntries"] = logbook_entries(package_root)
                rows = self._build_hge_candidate_rows(
                    state, hge_sightings
                )
                eddn_context = rebuild_eddn_context(
                    profiled_journal_events(), profile_identity
                )
                state_find_rows = self._build_state_find_rows(
                    state, hge_sightings, eddn_context,
                    eddn_queue, eddn_config,
                )
                self.startupStateReady.emit((
                    revision, profile_generation, state, rows,
                    profile_key, eddn_context, state_find_rows,
                    hge_revision, eddn_revision,
                ))
            except Exception as exc:
                LOGGER.exception("Initial journal state build failed")
                self.startupStateFailed.emit((
                    revision, profile_generation, str(exc),
                ))

        threading.Thread(
            target=worker, name="initial-journal-state", daemon=True
        ).start()

    @Slot(object)
    def _finish_startup_state(self, payload):
        revision, profile_generation, state, startup_rows = payload[:4]
        startup_profile_key = payload[4] if len(payload) > 4 else ""
        startup_eddn_context = payload[5] if len(payload) > 5 else None
        startup_state_find_rows = payload[6] if len(payload) > 6 else None
        startup_hge_revision = payload[7] if len(payload) > 7 else self._hge_revision
        startup_eddn_revision = payload[8] if len(payload) > 8 else self._eddn_revision
        if (
            revision != self._refresh_revision
            or profile_generation != self._profile_generation
        ):
            LOGGER.info(
                "Discarded stale startup state revision %s for profile generation %s",
                revision, profile_generation,
            )
            return
        if not isinstance(state, dict):
            self._fail_startup_state((
                revision, profile_generation,
                "Initial Journal state was not a mapping.",
            ))
            return
        profile_context = state.pop("_profileContext", None)
        if (
            isinstance(profile_context, ProfileContext)
            and not self._switch_profile_context(profile_context)
        ):
            self._fail_startup_state((
                revision, profile_generation,
                "Profile switch is waiting for EDDN upload.",
            ))
            return
        self._logbook_entries = list(state.pop("_logbookEntries", []))
        state.pop("_craftBatch", None)
        self._logbook_revision += 1
        state = self._state_with_frontier_profile(
            state, getattr(self, "_frontier_profile", {})
        )
        self._state = state
        overview = state.get("commanderOverview", {})
        if isinstance(overview, dict):
            self._record_commander_credit_snapshot(overview.get("credits", {}))
        if startup_hge_revision == self._hge_revision:
            self._hge_candidate_cache_key = (
                id(self._hge_sightings), len(self._hge_sightings), id(self._state)
            )
            self._hge_candidate_cache_rows = startup_rows
        else:
            self._hge_candidate_cache_key = None
            self._hge_candidate_cache_rows = []
        self._hge_material_filter_cache = None
        if (
            startup_profile_key == self.profile_context.key
            and isinstance(startup_eddn_context, dict)
        ):
            self._eddn_context = startup_eddn_context
        self._journal_state_ready = True
        self._selected_ship = str(state.get("ship") or "")
        self._activity = "Journal synchronized · live inventory loaded"
        self._log_consistency_issues(state)
        self._publish_full_state()
        if (
            isinstance(startup_state_find_rows, list)
            and startup_hge_revision == self._hge_revision
            and startup_eddn_revision == self._eddn_revision
        ):
            self._derived_cache["state_find_rows"] = ((
                self._state_revision, self._hge_revision, self._eddn_revision,
            ), startup_state_find_rows)
        self.activityChanged.emit()
        self.connectionChanged.emit()
        if getattr(self, "_journal_auto", False):
            self._queue_inara_journal_scan()

    @Slot(object)
    def _fail_startup_state(self, payload):
        revision, profile_generation, message = payload
        if (
            revision != self._refresh_revision
            or profile_generation != self._profile_generation
        ):
            LOGGER.info(
                "Discarded stale startup failure revision %s for profile generation %s",
                revision, profile_generation,
            )
            return
        self._activity = f"Journal startup sync failed · {message}"
        self.activityChanged.emit()

    def _log_consistency_issues(self, state):
        issues = tuple(str(item) for item in state.get("consistencyIssues", []))
        if not issues or issues == self._last_consistency_signature:
            return
        self._last_consistency_signature = issues
        for issue in issues:
            self._write_log(f"CONSISTENCY · {issue}")

    def _publish_full_state(self, previous: dict | None = None) -> None:
        """Notify each state domain once after an atomic state replacement.

        A routine Journal update (a plain FSDJump, a passive Scan, ...)
        very often leaves whole domains - materials, the wishlist - exactly
        as they were. Emitting their change signal anyway makes QML treat
        every list bound to it as a brand-new model, tearing down and
        rebuilding every delegate and replaying its fill-in animation for
        no reason - visibly, several times per jump, since a jump's
        Journal lines usually land in more than one debounced refresh.
        Compare against ``previous`` and skip a domain whose exposed keys
        did not actually change; ``previous=None`` (first load, or a
        caller that already mutated ``self._state`` in place) always
        emits, matching the prior unconditional behavior.
        """
        self._state_revision += 1
        self._derived_cache.clear()
        self.stateChanged.emit()
        state = self._state
        if previous is None or any(
            previous.get(key) != state.get(key)
            for key in ("materials", "trades", "traderRoute", "tradeHistory")
        ):
            self.materialsChanged.emit()
        self.fleetChanged.emit()
        if previous is None or any(
            previous.get(key) != state.get(key)
            for key in (
                "blueprints", "craftTrackingIssues", "freshCraftTrackingIssues",
                "historicalCraftTrackingIssues", "relevantCraftTrackingIssues",
                "unrelatedCraftTrackingIssues",
            )
        ):
            self.wishlistChanged.emit()
        if previous is None or any(
            previous.get(key) != state.get(key)
            for key in (
                "exobiologyFindings", "exobiologyLandingTargets",
                "exobiologySessionSummary", "exobiologyCarriedSummary",
                "exobiologyLifetimeEarned", "exobiologyBestFind",
                "exobiologyRemainingOnBody", "exobiologyGenusCompletion",
            )
        ):
            self.exobiologyChanged.emit()
        self.operationsChanged.emit()
        self.hgeChanged.emit()
        self.journalHealthChanged.emit()
        self.logbookChanged.emit()

    def _load_ui_config(self):
        return load_json_file(self.config_file, {}, encoding="utf-8")

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

    def _load_trader_sync_status(self):
        data = self._read_local_json(self.trader_catalog_file, {})
        count = len(data.get("stations", [])) if isinstance(data, dict) else 0
        fetched = str(data.get("fetched_at") or "") if isinstance(data, dict) else ""
        return (
            f"Offline catalog active · 1,622 bundled + {count} live updates"
            + (f" · {fetched}" if fetched else "")
        )

    def _load_tech_broker_sync_status(self):
        data = self._read_local_json(self.tech_broker_catalog_file, {})
        count = len(data.get("stations", [])) if isinstance(data, dict) else 0
        fetched = str(data.get("fetched_at") or "") if isinstance(data, dict) else ""
        return (
            f"Tech Broker cache · {count} nearby stations"
            + (f" · {fetched}" if fetched else " · update via Spansh")
        )

    @staticmethod
    def _persist_json(path, payload, label):
        try:
            saved = atomic_write(path, json.dumps(payload, indent=2))
        except OSError as exc:
            LOGGER.error("%s save failed for %s: %s", label, path, exc)
            return False
        if not saved:
            LOGGER.error("%s could not be persisted to %s", label, path)
        return saved

    def _save_ui_config(self):
        saved = self._persist_json(self.config_file, {
            "renderer_mode": self._renderer_mode,
            "ui_scale": self._ui_scale,
            "theme": self._theme,
            "interface_language": self._interface_language,
            "reduced_motion": self._reduced_motion,
            "commander_update_popups": self._commander_update_popups,
            "enhanced_visuals": self._enhanced_visuals,
            "onboarding_complete": self._onboarding_complete,
            "last_page": self._last_page,
            "debug_mode": self._debug_mode,
            "journal_auto": self._journal_auto,
            "background_mode": self._background_mode,
            "autostart_enabled": self._autostart_enabled,
            "trader_preference": self._trader_preference,
            "commander_card_order": self._commander_card_order,
            "navigation_order": self._navigation_order,
        }, "UI configuration")
        if not saved:
            self._activity = (
                "Settings changed in memory but could not be saved to disk."
            )
            signal = getattr(self, "activityChanged", None)
            if signal is not None:
                signal.emit()
        return saved


    @staticmethod
    def _read_local_json(path, fallback):
        return load_json_file(path, fallback, encoding="utf-8")

    def _archive_history(self, category, records, key_field=""):
        """Persist displaced records before removing them from an active view."""
        rows = [row for row in (records or []) if isinstance(row, dict)]
        if not rows:
            return True
        try:
            archive = getattr(self, "_history_archive", None)
            if archive is None:
                archive_path = getattr(self, "history_archive_file", None)
                if archive_path is None:
                    config_dir = getattr(self, "config_dir", None)
                    if config_dir is None:
                        return True
                    archive_path = Path(config_dir) / "data_history.sqlite3"
                archive = HistoryArchive(archive_path)
                self._history_archive = archive
            archive.archive(category, rows, key_field=key_field)
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            LOGGER.error("History archive write failed for %s: %s", category, exc)
            self._eddn_status = (
                "History archive could not be written; active data was retained."
            )
            return False

    def _history_counts(self):
        try:
            archive = getattr(self, "_history_archive", None)
            if archive is None:
                archive_path = getattr(self, "history_archive_file", None)
                if archive_path is None:
                    config_dir = getattr(self, "config_dir", None)
                    if config_dir is None:
                        return {}
                    archive_path = Path(config_dir) / "data_history.sqlite3"
                archive = HistoryArchive(archive_path)
                self._history_archive = archive
            return archive.counts()
        except (OSError, sqlite3.Error):
            return {}

    @staticmethod
    def _displaced_history_rows(before, after, ignored_fields=frozenset()):
        """Return exact prior versions no longer present in a derived view."""
        def comparable(value):
            if isinstance(value, dict):
                return {
                    key: comparable(item) for key, item in value.items()
                    if key not in ignored_fields
                }
            if isinstance(value, list):
                return [comparable(item) for item in value]
            return value

        current_payloads = {
            json.dumps(comparable(row), ensure_ascii=False, sort_keys=True)
            for row in (after or []) if isinstance(row, dict)
        }
        return [
            row for row in (before or [])
            if isinstance(row, dict) and json.dumps(
                comparable(row), ensure_ascii=False, sort_keys=True
            ) not in current_payloads
        ]

    @classmethod
    def _hge_displaced_history_rows(
        cls, before, after, snapshots, additions,
    ):
        """Compare only systems/signals touched by the current relay batch."""
        system_addresses = {
            row.get("system_address") for row in snapshots or []
            if isinstance(row, dict) and row.get("system_address") is not None
        }
        system_names = {
            normalize(row.get("system")) for row in snapshots or []
            if isinstance(row, dict) and row.get("system_address") is None
        }
        signal_keys = {
            (
                row.get("system"), row.get("signal_timestamp"),
                row.get("faction"), row.get("state"),
                row.get("find_type", "HGE"),
            )
            for row in additions or [] if isinstance(row, dict)
        }

        def touched(row):
            if not isinstance(row, dict):
                return False
            bgs_match = row.get("source") == "EDDN System BGS" and (
                row.get("system_address") in system_addresses
                or (
                    row.get("system_address") is None
                    and normalize(row.get("system")) in system_names
                )
            )
            signal_match = (
                row.get("system"), row.get("signal_timestamp"),
                row.get("faction"), row.get("state"),
                row.get("find_type", "HGE"),
            ) in signal_keys
            return bgs_match or signal_match

        return cls._displaced_history_rows(
            (row for row in before or [] if touched(row)),
            (row for row in after or [] if touched(row)),
        )














    def _edmc_parallel_status(self, journal=None, delivery=None, snapshots=None):
        """Give a scoped, evidence-based EDMC replacement verdict."""
        journal = journal if journal is not None else self._journal_health()
        delivery = delivery if delivery is not None else self._eddn_delivery_summary()
        snapshots = snapshots if snapshots is not None else self._eddn_station_snapshot_view()
        upload_enabled = bool(
            self._eddn_config.get("consent")
            and self._eddn_config.get("upload_enabled")
        )
        journal_status = str(journal.get("status") or "NO JOURNAL")
        failed = int(delivery.get("failed", 0) or 0)
        station_attention = sum(
            1 for row in snapshots
            if row.get("status") in {"FAILED", "INVALID", "NOT CURRENT", "STALE"}
        )
        if journal_status not in {"LIVE", "READY"}:
            verdict = "YES — JOURNAL IS NOT HEALTHY"
            tone = "ERROR"
            reason = "EDEC cannot currently prove reliable Journal processing. Keep EDMC until the Journal status is LIVE or READY."
        elif not upload_enabled:
            verdict = "YES — EDDN SHARING IS OFF"
            tone = "WARNING"
            reason = "EDEC reads the Journal, but anonymous EDDN upload is disabled. EDMC is still needed if you want to contribute community data."
        elif failed:
            verdict = "RECOMMENDED — EDDN ERRORS PENDING"
            tone = "WARNING"
            reason = f"EDEC has {failed} failed EDDN delivery job(s). Resolve or safely retry them before retiring EDMC."
        else:
            verdict = "NO — FOR JOURNAL + EDDN"
            tone = "READY"
            reason = "EDEC is processing the Journal and its EDDN sender is enabled. Running EDMC in parallel is not required for these paths."
        station_note = (
            f"{station_attention} station snapshot(s) need attention. Opening the matching Elite station page refreshes them; EDMC cannot create data Elite has not exposed."
            if station_attention else
            "Station snapshots have no current error or stale-data warning."
        )
        return {
            "verdict": verdict,
            "tone": tone,
            "reason": reason,
            "stationNote": station_note,
            "capiNote": "Frontier CAPI is not covered. Keep a CAPI-capable companion only if you need CAPI-dependent account, fleet or carrier data.",
        }


    def _save_hge_cache(self, already_partitioned=False):
        if not already_partitioned:
            active, historical = partition_hge_observations(self._hge_sightings)
            overflow = active[:-HGE_OBSERVATION_LIMIT]
            retained = active[-HGE_OBSERVATION_LIMIT:]
            if self._archive_history("hge_observations", [*historical, *overflow]):
                self._hge_sightings = retained
        if not hasattr(self, "_hge_file_lock"):
            self._hge_file_lock = threading.Lock()
            self._hge_save_sequence = 0
            self._hge_save_sequences = {}
        if not hasattr(self, "_hge_save_sequences"):
            self._hge_save_sequences = {}
        if not hasattr(self, "_hge_save_sequence"):
            self._hge_save_sequence = 0
        self._hge_save_sequence += 1
        sequence = self._hge_save_sequence
        path = self.hge_cache_file
        path_key = str(path)
        self._hge_save_sequences[path_key] = sequence
        snapshot = self._hge_sightings

        def write_snapshot():
            with self._hge_file_lock:
                if self._hge_save_sequences.get(path_key) != sequence:
                    return
                try:
                    atomic_write(
                        path,
                        json.dumps(
                            snapshot, ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                except OSError as exc:
                    LOGGER.warning(
                        "HGE cache save failed: %s", type(exc).__name__
                    )

        if getattr(self, "_shutdown_complete", False):
            write_snapshot()
        elif not self._start_network_worker(write_snapshot, "hge-cache-save"):
            write_snapshot()





    def _start_network_worker(self, target, name):
        """Track external I/O so shutdown can wait without hanging forever."""
        if self._shutdown_complete:
            return False

        def guarded():
            try:
                target()
            finally:
                with self._network_threads_lock:
                    self._network_threads.discard(threading.current_thread())

        thread = threading.Thread(target=guarded, daemon=True, name=name)
        with self._network_threads_lock:
            self._network_threads.add(thread)
        thread.start()
        return True

    @staticmethod
    def _detect_renderer():
        api = QQuickWindow.graphicsApi()
        return str(api).split(".")[-1]

    def _get(self, key, default=None):
        return self._state.get(key, default)




    @staticmethod
    def _ship_asset_key(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


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

    def _cached_derived(
        self, name: str, revision: object, builder: Callable[[], Any],
    ) -> Any:
        key = (name, revision)
        if key not in self._derived_cache:
            self._derived_cache[key] = builder()
        return self._derived_cache[key]

    @Slot()
    def _invalidate_connection_cache(self) -> None:
        self._connection_revision += 1
        self._drop_derived({"service_status"})

    @Slot()
    def _invalidate_hge_cache(self) -> None:
        self._hge_revision += 1
        self._drop_derived({"hge_targets", "hge_finder_rows"})

    @Slot()
    def _invalidate_operations_cache(self) -> None:
        self._drop_derived({"engineer_mission_route", "operation_action"})

    def _drop_derived(self, names: set[str]) -> None:
        for key in list(self._derived_cache):
            if key[0] in names:
                self._derived_cache.pop(key, None)









    def _journal_health(self):
        directory = journal_dir()
        try:
            files = sorted(
                directory.glob("Journal.*.log"),
                key=lambda path: path.stat().st_mtime,
            )
        except OSError:
            files = []
        latest = files[-1] if files else None
        age = -1
        size = 0
        parser_ok = False
        last_event = ""
        error = ""
        if latest:
            try:
                stat = latest.stat()
                age = max(0, int(time.time() - stat.st_mtime))
                size = int(stat.st_size)
                record = _last_complete_json_record(latest)
                if record:
                    parser_ok = isinstance(record, dict)
                    last_event = str(record.get("event") or "")
            except (OSError, ValueError, TypeError) as exc:
                error = str(exc)
        status = (
            "LIVE" if latest and parser_ok and age <= 15
            else "READY" if latest and parser_ok
            else "ERROR" if latest else "NO JOURNAL"
        )
        return {
            "status": status,
            "directoryExists": directory.exists(),
            "fileCount": len(files),
            "latestFile": latest.name if latest else "",
            "ageSeconds": age,
            "sizeBytes": size,
            "parserOk": parser_ok,
            "lastEvent": last_event,
            "watcherActive": bool(
                self._journal_auto
                and
                getattr(self, "timer", None)
                and self.timer.isActive()
            ),
            "pollIntervalMs": 1200 if self._journal_auto else 0,
            "error": error,
            "renderer": self._renderer_active,
        }

    def _diagnostic_logs(self):
        path = self.config_dir / "phase14.log"
        try:
            lines = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            return filtered_log_lines(lines)[-100:]
        except OSError:
            return []

    def _crash_reports(self):
        directory = self.config_dir / "crashes"
        try:
            paths = sorted(
                directory.glob("crash-*.log"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            paths = []
        return [
            {
                "name": path.name,
                "path": str(path),
                "size": int(path.stat().st_size),
            }
            for path in paths[:20]
        ]

    def _write_log(self, message):
        if not self._debug_mode:
            return
        self.config_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (self.config_dir / "phase14.log").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(f"{stamp} · {message}\n")

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

    def _start_mining_catalog_load(self):
        """Load a potentially large profile catalog without blocking Qt."""
        if not hasattr(self, "_mining_catalog_load_token"):
            self._mining_catalog_load_token = 0
        self._mining_catalog_load_token += 1
        token = self._mining_catalog_load_token
        generation = self._profile_generation
        profile_key = self.profile_context.key
        path = self.mining_catalog_file

        def worker():
            catalog = load_json_file(
                path, {"candidates": []}, encoding="utf-8"
            )
            if not isinstance(catalog, dict):
                catalog = {"candidates": []}
            self._compact_mining_catalog_rows(catalog.get("candidates", []))
            self.miningCatalogLoaded.emit((
                token, generation, profile_key, str(path), catalog,
            ))

        return self._start_network_worker(worker, "mining-catalog-load")

    @staticmethod
    def _compact_mining_catalog_rows(rows):
        """Drop time-derived fields that are recalculated for every view."""
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            for field in MINING_TRANSIENT_FIELDS:
                row.pop(field, None)
        return rows

    @Slot(object)
    def _finish_mining_catalog_load(self, payload):
        token, generation, profile_key, path, catalog = payload
        if (
            token != self._mining_catalog_load_token
            or generation != self._profile_generation
            or profile_key != self.profile_context.key
            or path != str(self.mining_catalog_file)
            or not isinstance(catalog, dict)
        ):
            return
        loaded = catalog.get("candidates", [])
        current = self._mining_catalog.get("candidates", [])
        loaded = loaded if isinstance(loaded, list) else []
        current = current if isinstance(current, list) else []
        if current:
            merged, _displaced = merge_mining_candidate_batch(loaded, current)
        else:
            merged = loaded
        self._compact_mining_catalog_rows(merged)
        self._mining_catalog = {
            **catalog,
            "candidates": merged,
        }
        self._mining_rows_cache_key = None
        self._mining_rows_cache = []
        self.miningChanged.emit()

    def _save_mining_catalog(self):
        # Serializing a mature catalog can take hundreds of milliseconds.
        # Keep that work off the Qt thread and let only the newest queued
        # snapshot win. Shutdown still performs a final synchronous save.
        if not hasattr(self, "_mining_file_lock"):
            self._mining_file_lock = threading.Lock()
            self._mining_save_sequence = 0
            self._mining_save_sequences = {}
        if not hasattr(self, "_mining_save_sequences"):
            self._mining_save_sequences = {}
        if not hasattr(self, "_mining_save_sequence"):
            self._mining_save_sequence = 0
        self._mining_save_sequence += 1
        sequence = self._mining_save_sequence
        path = self.mining_catalog_file
        path_key = str(path)
        self._mining_save_sequences[path_key] = sequence
        snapshot = self._mining_catalog

        def write_snapshot():
            with self._mining_file_lock:
                if self._mining_save_sequences.get(path_key) != sequence:
                    return
                try:
                    atomic_write(
                        path,
                        json.dumps(
                            snapshot, ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                except OSError as exc:
                    LOGGER.warning(
                        "Mining catalog save failed: %s", type(exc).__name__
                    )

        if getattr(self, "_shutdown_complete", False):
            write_snapshot()
        elif not self._start_network_worker(
            write_snapshot, "mining-catalog-save"
        ):
            write_snapshot()

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

    def _next_action(self):
        return str(self._operation_action().get("title") or "Open Engineering")

    def _operation_action(self):
        def build_action():
            return attach_operation_experimental_effects(
                attach_operation_plan_context(
                    scope_operation_action_materials(
                        self._state,
                        select_operation_action(
                            self._state,
                            self._engineer_mission_route() + self._engineer_unlock_tasks(),
                            self._engineer_index(),
                            [record for records in self._blueprint_groups.values() for record in records],
                        ),
                    ),
                    self._state,
                    self._engineer_index(),
                    [record for records in self._blueprint_groups.values() for record in records],
                ),
                self._experimentals,
            )

        return self._cached_derived(
            "operation_action", (
                self._state_revision, tuple(sorted(self._deferred_engineers))
            ),
            build_action,
        )

    def _hge_targets(self):
        return self._cached_derived(
            "hge_targets", (self._state_revision, self._hge_revision),
            self._build_hge_targets,
        )

    def _build_hge_targets(self):
        wanted = {
            normalize(material.get("key")): material
            for material in self._state.get("materials", [])
            if int(material.get("missing", 0) or 0) > 0
            and is_hge_material(str(material.get("key") or ""))
        }
        best = {}
        for sighting in rank_all_hge_sightings(
            self._hge_sightings, self._state.get("currentPosition")
        ):
            age = float(sighting.get("age_seconds", 2700))
            if age > 2700:
                continue
            freshness = max(0.20, 1.0 - age / 2700)
            distance = sighting.get("distance_ly")
            for candidate in sighting.get("materials", []) or []:
                key = normalize(candidate.get("material"))
                if key not in wanted:
                    continue
                confidence = float(
                    candidate.get("confidence", 0.0) or 0.0
                ) * freshness
                score = confidence * 100.0 - (distance or 0.0) * 0.08
                rank = (
                    -score,
                    distance if distance is not None else float("inf"),
                )
                if key not in best or rank < best[key][0]:
                    best[key] = (rank, {**sighting, "confidence": confidence})
        rows = []
        for normalized_key, material in wanted.items():
            key = str(material.get("key") or "")
            target = best.get(normalized_key, ((), {}))[1]
            rows.append({
                "key": key,
                "name": material.get("name") or key,
                "missing": int(material.get("missing", 0) or 0),
                "active": bool(target),
                "system": target.get("system", ""),
                "state": target.get("state", ""),
                "ageMinutes": int(target.get("age_seconds", 0) // 60)
                if target else -1,
                "distance": float(target.get("distance_ly", -1) or -1)
                if target else -1,
                "confidence": float(target.get("confidence", 0) or 0),
            })
        return sorted(rows, key=lambda row: (
            not row["active"], -row["confidence"], row["name"].casefold()
        ))

    def _hge_finder_rows(self):
        return self._cached_derived(
            "hge_finder_rows", (self._state_revision, self._hge_revision),
            self._build_hge_finder_rows,
        )

    def _build_hge_finder_rows(self):
        material_names = {
            normalize(row.get("key")): str(row.get("name") or row.get("key") or "")
            for row in self._state.get("materials", [])
        }
        rows = []
        for sighting in rank_all_hge_sightings(
            self._state.get("localHgeSightings", []),
            self._state.get("currentPosition")
        ):
            state = readable_faction_state(
                sighting.get("state_raw") or sighting.get("state")
            )
            probable_materials = sighting.get("materials") or infer_hge_materials(
                state, sighting.get("allegiance")
            )
            materials = [
                material_names.get(
                    normalize(item.get("material")),
                    str(item.get("material") or "").replace("_", " ").title(),
                )
                for item in probable_materials
            ]
            rows.append({
                "system": str(sighting.get("system") or ""),
                "faction": str(sighting.get("faction") or "Unknown faction"),
                "state": state or "Unknown state",
                "allegiance": str(sighting.get("allegiance") or ""),
                "distance": (
                    round(float(sighting["distance_ly"]), 1)
                    if sighting.get("distance_ly") is not None else -1
                ),
                "remainingSeconds": int(sighting.get("remaining_seconds", 0)),
                "remainingMinutes": max(
                    1, int(sighting.get("remaining_seconds", 0) // 60)
                ),
                "materials": ", ".join(materials) if materials else
                "Contents not predictable from available state data",
                "selfTest": bool(sighting.get("self_test")),
                "localVerified": True,
                "status": "VERIFIED",
            })
        return rows

    def _hge_candidate_rows(self):
        cache_key = (
            id(self._hge_sightings), len(self._hge_sightings), id(self._state)
        )
        if cache_key == self._hge_candidate_cache_key:
            return self._hge_candidate_cache_rows
        rows = self._build_hge_candidate_rows(
            self._state, self._hge_sightings
        )
        self._hge_candidate_cache_key = cache_key
        self._hge_candidate_cache_rows = rows
        self._hge_material_filter_cache = None
        return rows

    def _build_hge_candidate_rows(self, state, sightings):
        material_names = {
            normalize(row.get("key")): str(row.get("name") or row.get("key") or "")
            for row in state.get("materials", [])
        }
        rows = []
        for candidate in rank_hge_candidate_systems(
            sightings, state.get("currentPosition"),
            current_system=state.get("system", ""),
            current_system_address=state.get("currentSystemAddress"),
        ):
            predictions = [
                {
                    "name": material_names.get(
                        normalize(item.get("material")),
                        str(item.get("material") or "").replace("_", " ").title(),
                    ),
                    "confidence": int(round(float(item.get("confidence", 0)) * 100)),
                }
                for item in candidate.get("materials", [])
            ]
            scan = state.get("localHgeScan", {}) or {}
            same_system = (
                str(candidate.get("system") or "").casefold()
                == str(scan.get("system") or "").casefold()
            )
            status = str(scan.get("status") or "UNKNOWN") if same_system else "UNKNOWN"
            rows.append({
                "system": candidate.get("system", ""),
                "distance": (
                    round(float(candidate["distance_ly"]), 1)
                    if candidate.get("distance_ly") is not None else -1
                ),
                "reportCount": int(candidate.get("report_count", 0) or 0),
                "lastReportedMinutes": int(
                    candidate.get("last_reported_minutes", 0) or 0
                ),
                "factions": ", ".join(candidate.get("factions", [])[:3])
                or "Faction not reported",
                "states": ", ".join(candidate.get("states", [])[:3])
                or "State not reported",
                "materials": ", ".join(item["name"] for item in predictions)
                if predictions else "Contents not predictable from available state data",
                "prediction": ", ".join(
                    f"{item['name']} ({item['confidence']}%)" for item in predictions
                ) if predictions else "No reliable material prediction",
                "predictionBasis": str(
                    candidate.get("prediction_basis") or "BGS data unavailable"
                ),
                "candidateOnly": True,
                "selfTest": False,
                "status": status,
            })
        return rows

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

    def _state_find_rows(self):
        return self._cached_derived(
            "state_find_rows", (
                self._state_revision, self._hge_revision, self._eddn_revision,
            ),
            self._build_state_find_rows,
        )


    @staticmethod
    def _valid_star_position(value):
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return None
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _state_find_timestamp(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError):
            return -1.0

    def _system_coordinate_index(self):
        coordinates = {
            **read_json(self._reference_data_dir / "system_coordinates.json", {}),
            **read_json(self._data_dir / "system_coordinates.json", {}),
        }
        return {
            str(system).strip().casefold(): position
            for system, position in coordinates.items()
            if str(system).strip() and self._valid_star_position(position)
        }

    def _state_find_origin(
        self, coordinates, state=None, eddn_context=None,
    ):
        """Resolve the Commander position from evidence, never estimation."""
        state = self._state if state is None else state
        eddn_context = self._eddn_context if eddn_context is None else eddn_context
        journal_position = self._valid_star_position(
            state.get("currentPosition")
        )
        if journal_position is not None:
            return journal_position
        eddn_position = self._valid_star_position(eddn_context.get("StarPos"))
        if eddn_position is not None:
            return eddn_position
        system = str(
            state.get("system") or eddn_context.get("StarSystem") or ""
        ).strip().casefold()
        return self._valid_star_position(coordinates.get(system))

    def _state_find_observations(
        self, state=None, sightings=None, eddn_context=None,
    ):
        """Add exact catalog coordinates where source rows omitted StarPos."""
        state = self._state if state is None else state
        sightings = self._hge_sightings if sightings is None else sightings
        coordinates = self._system_coordinate_index()
        source = list(sightings)
        source.extend(state.get("localStateFinds", []))
        observations = []
        for item in source:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if self._valid_star_position(row.get("star_pos")) is None:
                system = str(row.get("system") or "").strip().casefold()
                known = self._valid_star_position(coordinates.get(system))
                if known is not None:
                    row["star_pos"] = known
            observations.append(row)
        return observations, self._state_find_origin(
            coordinates, state, eddn_context,
        )

    def _build_state_find_rows(
        self, state=None, sightings=None, eddn_context=None,
        eddn_queue=None, eddn_config=None,
    ):
        state = self._state if state is None else state
        material_names = {
            normalize(row.get("key")): str(row.get("name") or row.get("key") or "")
            for row in state.get("materials", [])
        }
        observations, origin = self._state_find_observations(
            state, sightings, eddn_context,
        )
        local_scan = state.get("localStateFindScan", {}) or {}
        rows = []
        for candidate in rank_state_find_systems(
            observations, origin,
            current_system=state.get("system", ""),
            current_system_address=state.get("currentSystemAddress"),
        ):
            materials = [
                material_names.get(
                    normalize(item.get("material")),
                    str(item.get("material") or "").replace("_", " ").title(),
                )
                for item in candidate.get("materials", [])
            ]
            evidence = str(candidate.get("evidence_kind") or "BGS_PREDICTION")
            remaining = int(candidate.get("remaining_seconds", 0) or 0)
            status = {
                "BGS_PREDICTION": "POSSIBLE",
                "EDDN_SIGNAL": "EDDN LIVE" if remaining > 0 else "RECENT REPORT",
                "LOCAL_JOURNAL": "LOCAL LIVE",
                "ENTERED": "LOCAL ENTERED",
            }.get(evidence, "PREDICTED")
            candidate_address = candidate.get("system_address")
            scan_address = local_scan.get("system_address")
            same_system = (
                candidate_address is not None and scan_address is not None
                and candidate_address == scan_address
            ) or (
                str(candidate.get("system") or "").strip().casefold()
                == str(local_scan.get("system") or "").strip().casefold()
            )
            scan_stamp = self._state_find_timestamp(
                local_scan.get("scan_timestamp")
            )
            candidate_stamp = self._state_find_timestamp(
                candidate.get("latest_timestamp")
            )
            scan_is_newer = scan_stamp >= 0 and scan_stamp >= candidate_stamp
            local_not_confirmed = bool(
                candidate.get("find_type", "HGE") == "HGE"
                and evidence == "EDDN_SIGNAL"
                and same_system and local_scan.get("complete")
                and int(local_scan.get("hge_count", 0) or 0) == 0
                and scan_is_newer
            )
            if local_not_confirmed:
                status = "REMOTE · LOCALLY NOT CONFIRMED"
            match_class = hge_match_class(evidence, materials)
            states = candidate.get("states", [])
            allegiances = candidate.get("allegiances", [])
            distance = candidate.get("distance_ly")
            rows.append({
                "findType": candidate.get("find_type", "HGE"),
                "findLabel": candidate.get("find_label", "High Grade Emissions"),
                "system": candidate.get("system", ""),
                "isCurrentSystem": bool(candidate.get("is_current_system")),
                "distance": round(float(distance), 1) if distance is not None else -1,
                "state": ", ".join(states) if states else "State not reported",
                "stateValues": list(states),
                "allegiance": ", ".join(allegiances) if allegiances else "Not relevant",
                "allegianceValues": list(allegiances),
                "faction": ", ".join(candidate.get("factions", []))
                           or "Faction not reported",
                "intensity": candidate.get("intensity", "UNKNOWN"),
                "evidenceKind": evidence,
                "status": status,
                "materials": ", ".join(materials),
                "reportCount": int(candidate.get("report_count", 0) or 0),
                "lastReportedMinutes": int(
                    candidate.get("last_reported_minutes", 0) or 0
                ),
                "remainingSeconds": remaining,
                "freshness": str(candidate.get("freshness") or "STALE"),
                "localNotConfirmed": local_not_confirmed,
                "matchClass": match_class,
                "eddnDelivery": self._eddn_delivery_for_candidate(
                    candidate, eddn_queue, eddn_config,
                ),
            })
        return rows

    def _state_find_filter_values(self, field, all_label):
        values = set()
        for row in self._state_find_rows():
            source = row.get(field, [])
            if isinstance(source, list):
                values.update(str(value) for value in source if value)
        return [all_label] + sorted(values, key=str.casefold)

    def _state_find_cache_summary(self):
        rows = [row for row in self._hge_sightings if isinstance(row, dict)]
        bgs_count = sum(
            row.get("evidence_kind") == "BGS_PREDICTION" for row in rows
        )
        signal_count = sum(
            row.get("evidence_kind") in {
                "EDDN_SIGNAL", "LOCAL_JOURNAL", "ENTERED",
            }
            for row in rows
        )
        timestamps = []
        for row in rows:
            value = row.get("signal_timestamp") or row.get("received_at")
            timestamp = self._state_find_timestamp(value)
            if timestamp >= 0:
                timestamps.append(timestamp)

        def display(value):
            if value is None:
                return "NONE"
            return datetime.fromtimestamp(value, timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )

        return {
            "total": len(rows),
            "bgs": bgs_count,
            "signals": signal_count,
            "other": max(0, len(rows) - bgs_count - signal_count),
            "oldestAt": display(min(timestamps) if timestamps else None),
            "newestAt": display(max(timestamps) if timestamps else None),
            "retentionHours": 24,
        }

    def _filtered_state_finds(self, find_type, state_filter, allegiance_filter,
                              nearby_ly, material_filter,
                              evidence_filter="ALL EVIDENCE"):
        rows = self._state_find_rows()
        if find_type and find_type != "ALL FIND TYPES":
            rows = [row for row in rows if row.get("findType") == find_type]
        if state_filter and state_filter != "ALL STATES":
            rows = [row for row in rows if state_filter in row.get("stateValues", [])]
        if allegiance_filter and allegiance_filter != "ALL ALLEGIANCES":
            rows = [
                row for row in rows
                if allegiance_filter in row.get("allegianceValues", [])
            ]
        radius = max(0, int(nearby_ly or 0))
        if radius:
            rows = [
                row for row in rows
                if float(row.get("distance", -1)) >= 0
                and float(row.get("distance", -1)) <= radius
            ]
        if material_filter and material_filter != "ALL HGE MATERIALS":
            current_system = str(self._state.get("system") or "").strip().casefold()
            visible = []
            for source in rows:
                material_match = bool(
                    source.get("findType") == "HGE"
                    and material_filter
                    in str(source.get("materials") or "").split(", ")
                )
                local_current_find = bool(
                    source.get("evidenceKind") in {"LOCAL_JOURNAL", "ENTERED"}
                    and current_system
                    and str(source.get("system") or "").strip().casefold()
                    == current_system
                )
                if not material_match and not local_current_find:
                    continue
                row = dict(source)
                row["targetMaterialMatch"] = material_match
                if local_current_find and not material_match:
                    row["matchClass"] = (
                        "LOCAL ENTERED · DETAILS UNKNOWN"
                        if not str(row.get("materials") or "").strip()
                        else "LOCAL FIND · OTHER MATERIAL FAMILY"
                    )
                visible.append(row)
            rows = visible
        if evidence_filter == "LIVE ONLY":
            rows = [row for row in rows if row.get("freshness") == "LIVE"]
        elif evidence_filter == "LOCALLY VERIFIED":
            rows = [
                row for row in rows
                if row.get("evidenceKind") in {"LOCAL_JOURNAL", "ENTERED"}
            ]
        elif evidence_filter == "EDDN REPORTS":
            rows = [row for row in rows if row.get("evidenceKind") == "EDDN_SIGNAL"]
        elif evidence_filter == "BGS CANDIDATES":
            rows = [row for row in rows if row.get("evidenceKind") == "BGS_PREDICTION"]
        # Preserve the evidence/distance ordering inside both groups, while
        # keeping the system the Commander is currently visiting at the top.
        return sorted(rows, key=lambda row: not bool(row.get("isCurrentSystem")))

    @staticmethod
    def _group_state_find_travel_targets(rows):
        """Group BGS predictions by destination without merging their meaning."""
        grouped = []
        prediction_groups = {}
        for source in rows:
            if not (
                source.get("findType") == "HGE"
                and source.get("evidenceKind") == "BGS_PREDICTION"
            ):
                row = dict(source)
                row["variantCount"] = 1
                row["variants"] = []
                grouped.append(row)
                continue
            key = str(source.get("system") or "").strip().casefold()
            row = prediction_groups.get(key)
            if row is None:
                row = dict(source)
                row["variantCount"] = 0
                row["variants"] = []
                row["reportCount"] = 0
                prediction_groups[key] = row
                grouped.append(row)
            variant = {
                "state": str(source.get("state") or "State not reported"),
                "faction": str(source.get("faction") or "Faction not reported"),
                "allegiance": str(source.get("allegiance") or "Not relevant"),
                "materials": str(source.get("materials") or ""),
                "reportCount": int(source.get("reportCount", 0) or 0),
            }
            if variant not in row["variants"]:
                row["variants"].append(variant)
                row["variantCount"] += 1
            row["reportCount"] += int(source.get("reportCount", 0) or 0)
        return grouped

    @Slot(str, str, str, int, str, str, int, result="QVariantList")
    def stateFindPage(self, find_type, state_filter, allegiance_filter,
                      nearby_ly, material_filter, evidence_filter, limit):
        rows = self._filtered_state_finds(
            find_type, state_filter, allegiance_filter, nearby_ly,
            material_filter, evidence_filter,
        )
        return self._group_state_find_travel_targets(rows)[
            :max(1, int(limit or 250))
        ]

    @Slot(str, str, str, int, str, str, result=int)
    def stateFindCount(self, find_type, state_filter, allegiance_filter,
                       nearby_ly, material_filter, evidence_filter):
        rows = self._filtered_state_finds(
            find_type, state_filter, allegiance_filter, nearby_ly,
            material_filter, evidence_filter,
        )
        return len(self._group_state_find_travel_targets(rows))

    def _mining_rows(self):
        cache_key = self._mining_rows_identity(
            self._state, self._mining_catalog
        )
        if getattr(self, "_mining_rows_cache_key", None) == cache_key:
            return self._mining_rows_cache
        # Production controllers prepare the 60+ MB catalog view in a worker.
        # Lightweight test shells retain the deterministic synchronous path.
        if hasattr(self, "_network_threads_lock"):
            self._queue_mining_rows_build()
            return getattr(self, "_mining_rows_cache", [])
        rows = self._build_mining_rows(self._state, self._mining_catalog)
        self._mining_rows_cache_key = cache_key
        self._mining_rows_cache = rows
        return rows

    @staticmethod
    def _mining_rows_identity(state, catalog):
        local = state.get("localMiningEvidence", {})
        local_rows = local.get("candidates") if isinstance(local, dict) else ()
        local_rows = local_rows if isinstance(local_rows, list) else ()
        catalog_rows = catalog.get("candidates", [])
        catalog_rows = catalog_rows if isinstance(catalog_rows, list) else []
        origin = state.get("currentPosition")
        return (
            id(catalog), len(catalog_rows), id(state),
            id(local_rows), len(local_rows), str(catalog.get("resetAt") or ""),
            str(state.get("system") or "").casefold(),
            tuple(origin) if isinstance(origin, (list, tuple)) else (),
            # Invalidate cached result freshness hourly. Rebuilding a ~50 MB
            # catalog every minute stalls filter controls.
            int(time.time() // 3600),
        )

    def _build_mining_rows(self, state, mining_catalog):
        local = state.get("localMiningEvidence", {})
        local_rows = local.get("candidates", []) if isinstance(local, dict) else []
        catalog = mining_catalog.get("candidates", [])
        catalog_rows = catalog if isinstance(catalog, list) else []
        reset_at = str(mining_catalog.get("resetAt") or "")
        if reset_at:
            local_rows = [
                row for row in local_rows
                if str(row.get("observedAt") or "") > reset_at
            ]
            catalog_rows = [
                row for row in catalog_rows
                if str(row.get("learnedAt") or "") > reset_at
            ]
        origin = state.get("currentPosition")
        # The persisted catalog is already normalized on ingestion. Merge the
        # much smaller local delta into it instead of reprocessing every ring.
        rows, _displaced = merge_mining_candidate_batch(
            catalog_rows, local_rows
        )
        rows = [dict(row) for row in rows]
        current = str(state.get("system") or "").casefold()
        for row in rows:
            legacy_planetary_count = sum(
                int(item.get("count", 0) or 0)
                for item in row.get("hotspots", [])
                if isinstance(item, dict)
                and mining_commodity_id(item.get("commodity"))
                == "planetarymininglocation"
            )
            row["planetaryMiningLocationCount"] = max(
                int(row.get("planetaryMiningLocationCount", 0) or 0),
                legacy_planetary_count,
            )
            row["hotspots"] = [
                item for item in row.get("hotspots", [])
                if isinstance(item, dict)
                and is_mining_commodity_signal(item.get("commodity"))
            ]
            if current and str(row.get("system") or "").casefold() == current:
                row["distanceLy"] = 0.0
            elif (isinstance(origin, (list, tuple)) and len(origin) == 3
                  and isinstance(row.get("coordinates"), (list, tuple))
                  and len(row["coordinates"]) == 3):
                try:
                    row["distanceLy"] = round(math.sqrt(sum(
                        (float(left) - float(right)) ** 2
                        for left, right in zip(origin, row["coordinates"])
                    )), 1)
                except (TypeError, ValueError):
                    row["distanceLy"] = None
            row["hotspotNames"] = ", ".join(
                self._mining_display_name(item.get("commodity"))
                for item in row.get("hotspots", []) if isinstance(item, dict)
            ) or "No hotspot signals recorded"
            row["ringTypeName"] = self._mining_display_name(
                row.get("ringType")
            ).replace("E Ring Class ", "") or "Unknown"
            row["reserveName"] = self._mining_display_name(
                row.get("reserveLevel")
            ).replace(" Resources", "") or "Unknown"
        return rows

    def _queue_mining_rows_build(self):
        cache_key = self._mining_rows_identity(
            self._state, self._mining_catalog
        )
        if getattr(self, "_mining_rows_cache_key", None) == cache_key:
            return False
        if getattr(self, "_mining_rows_build_in_flight", False):
            self._mining_rows_build_dirty = True
            return False
        self._mining_rows_build_token = getattr(
            self, "_mining_rows_build_token", 0
        ) + 1
        token = self._mining_rows_build_token
        generation = self._profile_generation
        profile_key = self.profile_context.key
        state = self._state
        catalog = self._mining_catalog
        self._mining_rows_build_in_flight = True
        self._mining_rows_build_dirty = False

        def worker():
            try:
                rows = self._build_mining_rows(state, catalog)
                result = (token, generation, profile_key, cache_key, rows, "")
            except Exception as exc:
                result = (
                    token, generation, profile_key, cache_key, [],
                    f"{type(exc).__name__}: {exc}",
                )
            self.miningRowsReady.emit(result)

        if not self._start_network_worker(worker, "mining-rows-build"):
            self._mining_rows_build_in_flight = False
            return False
        return True

    @Slot(object)
    def _finish_mining_rows_build(self, payload):
        token, generation, profile_key, cache_key, rows, error = payload
        if token != self._mining_rows_build_token:
            return
        self._mining_rows_build_in_flight = False
        current = (
            generation == self._profile_generation
            and profile_key == self.profile_context.key
        )
        current_key = self._mining_rows_identity(
            self._state, self._mining_catalog
        ) if current else None
        if current and not error and cache_key == current_key:
            self._mining_rows_cache_key = cache_key
            self._mining_rows_cache = rows
            self._mining_find_cache_key = None
            self._mining_find_cache = []
            self.miningChanged.emit()
        elif current and error:
            LOGGER.warning("Mining rows background build failed: %s", error)
        dirty = getattr(self, "_mining_rows_build_dirty", False)
        self._mining_rows_build_dirty = False
        if current and (dirty or cache_key != current_key):
            self._queue_mining_rows_build()

    @staticmethod
    def _mining_display_name(value):
        commodity = MINING_COMMODITIES.get(mining_commodity_id(value))
        if commodity:
            return str(commodity["name"])
        text = str(value or "").replace("_", " ").strip()
        if text.casefold().startswith("$saa signaltype ") and text.endswith(";"):
            text = text[len("$saa signaltype "):-1]
        text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
        return text.title()

    @Slot(str, int, str, str, result="QVariantList")
    def miningFindPage(self, commodity, nearby_ly, evidence, reserve_filter):
        return self._mining_find_page(
            commodity, nearby_ly, evidence, reserve_filter, ""
        )

    @Slot(str, int, str, str, str, result="QVariantList")
    def miningFindPageForMethod(
        self, commodity, nearby_ly, evidence, reserve_filter, method,
    ):
        return self._mining_find_page(
            commodity, nearby_ly, evidence, reserve_filter, method
        )

    def _mining_find_page(
        self, commodity, nearby_ly, evidence, reserve_filter, method,
    ):
        commodity = normalize(str(commodity or "ALL COMMODITIES"))
        commodity_id = mining_commodity_id(commodity)
        selected = MINING_COMMODITIES.get(commodity_id)
        method = str(method or "").upper()
        evidence = str(evidence or "ALL EVIDENCE")
        reserve_filter = str(reserve_filter or "ALL RESERVES")
        nearby_limit = int(nearby_ly or 0)
        source_rows = self._mining_rows()
        cache_key = (
            getattr(self, "_mining_rows_cache_key", None), commodity_id,
            nearby_limit, evidence, reserve_filter, method,
        )
        if getattr(self, "_mining_find_cache_key", None) == cache_key:
            return self._mining_find_cache
        all_commodities = commodity == normalize("ALL COMMODITIES")
        result = []
        for source_row in source_rows:
            # Reject on scalar/index-like fields before allocating a dict or
            # walking hotspot lists. Most large catalogs fail distance first.
            distance = source_row.get("distanceLy")
            if nearby_limit > 0 and (
                distance is None or float(distance) > nearby_limit
            ):
                continue
            # Freshness is time-dependent, so update only rows that survived
            # the cheap distance test instead of rebuilding the full catalog.
            fresh_row = mining_candidate_freshness(source_row)
            if evidence == "RECHECK_RECOMMENDED":
                if not fresh_row.get("recheckRecommended"):
                    continue
            elif (
                evidence != "ALL EVIDENCE"
                and fresh_row.get("evidence") != evidence
            ):
                continue
            reserve = normalize(source_row.get("reserveLevel"))
            if reserve_filter == "PRISTINE + MAJOR" and not (
                "pristine" in reserve or "major" in reserve
            ):
                continue
            if reserve_filter == "PRISTINE" and "pristine" not in reserve:
                continue
            if reserve_filter == "MAJOR" and "major" not in reserve:
                continue
            if method == RHINO_SURFACE:
                if not int(
                    source_row.get("planetaryMiningLocationCount", 0) or 0
                ):
                    continue
            elif not all_commodities:
                hotspot_ids = {
                    mining_commodity_id(item.get("commodity"))
                    for item in source_row.get("hotspots", [])
                    if isinstance(item, dict)
                }
                if commodity_id not in hotspot_ids:
                    if not (
                        selected and method
                        and method in selected.get("methods", ())
                    ):
                        continue
                    ring_type = normalize(source_row.get("ringTypeName"))
                    eligible = {
                        normalize(value) for value in selected.get("ringTypes", ())
                    }
                    if not ring_type or ring_type not in eligible:
                        continue
            row = fresh_row
            if method == RHINO_SURFACE:
                row["targetMatch"] = "PLANETARY_MINING_LOCATION"
                row["targetMatchName"] = (
                    "PLANETARY MINING LOCATION · COMMODITY UNCONFIRMED"
                )
                row["ringTypeName"] = "Planetary surface"
                row["reserveName"] = "Unknown"
                row["hotspotNames"] = (
                    f"{int(row['planetaryMiningLocationCount'])} "
                    "planetary mining locations reported"
                )
            elif not all_commodities:
                hotspot_ids = {
                    mining_commodity_id(item.get("commodity"))
                    for item in row.get("hotspots", []) if isinstance(item, dict)
                }
                if commodity_id in hotspot_ids:
                    row["targetMatch"] = "HOTSPOT"
                    row["targetMatchName"] = "HOTSPOT SIGNAL"
                else:
                    row["targetMatch"] = "RING_TYPE"
                    row["targetMatchName"] = "RING-TYPE AVAILABILITY · NO HOTSPOT REQUIRED"
            else:
                row["targetMatch"] = "ANY"
                row["targetMatchName"] = "ALL RECORDED RING EVIDENCE"
            result.append(row)
        self._mining_find_cache_key = cache_key
        self._mining_find_cache = result
        return result

    @Slot(str, result="QVariantMap")
    def miningLoadoutReadiness(self, method):
        method = str(method or "LASER").upper()
        module_ids = [normalize(row.get("moduleId")) for row in
                      self._state.get("moduleSlots", []) if isinstance(row, dict)]
        cargo = int(self._state.get("selectedShipStats", {}).get(
            "cargoCapacity", 0
        ) or 0)
        checks = []

        def check(label, *markers):
            installed = any(any(marker in module for marker in markers)
                            for module in module_ids)
            checks.append({"label": label, "installed": installed})

        if method == "LASER":
            check("Prospector", "prospector", "multidronecontrolmining")
            check("Collector", "collector", "collection", "multidronecontrolmining")
            check("Refinery", "refinery")
        elif method == "CORE":
            check("Seismic charge launcher", "miningseismchrgwarhd")
            check("Abrasion blaster", "miningabrasionblaster")
            check("Pulse wave analyser", "cloudscanner", "mrascanner")
            check("Collector", "collector", "collection", "multidronecontrolmining")
            check("Refinery", "refinery")
        elif method == "SUBSURFACE":
            check("Sub-surface displacement missile", "miningsubsurfdispmisle")
            check("Prospector", "prospector", "multidronecontrolmining")
            check("Collector", "collector", "collection", "multidronecontrolmining")
            check("Refinery", "refinery")
        else:
            vehicle = self._state.get("vehicleState", {})
            rhino = any("rhino" in normalize(row.get("type")) for row in
                        vehicle.get("vehicles", []) if isinstance(row, dict))
            checks.append({"label": "Rhino observed in vehicle inventory",
                           "installed": rhino})
        checks.append({"label": f"Cargo capacity ({cargo} t)", "installed": cargo > 0})
        ready = bool(checks) and all(row["installed"] for row in checks)
        return {
            "method": method,
            "ready": ready,
            "status": "READY" if ready else "INCOMPLETE",
            "summary": " · ".join(
                ("✓ " if row["installed"] else "✕ ") + row["label"]
                for row in checks
            ),
        }

    def _mining_commodity_filters(self):
        return [
            "ALL COMMODITIES",
            *(row["name"] for row in self._mining_commodity_catalog()),
        ]

    def _mining_commodity_catalog(self):
        local = self._state.get("localMiningEvidence", {})
        observed = local.get("refinedCommodities", []) \
            if isinstance(local, dict) else []
        observed = list(observed) if isinstance(observed, list) else []
        for row in self._mining_rows():
            for hotspot in row.get("hotspots", []):
                if isinstance(hotspot, dict):
                    observed.append({"id": hotspot.get("commodity")})
        return mining_commodity_catalog(observed)

    @Slot(str, result="QStringList")
    def miningCommodityFiltersForMethod(self, method):
        local = self._state.get("localMiningEvidence", {})
        observed = local.get("refinedCommodities", []) \
            if isinstance(local, dict) else []
        rows = mining_commodities_for_method(method, observed)
        return ["ALL COMMODITIES", *(row["name"] for row in rows)]

    def _mining_cache_summary(self):
        rows = self._mining_rows()
        counts = {key: 0 for key in (
            "LOCAL_CONFIRMED", "LIVE_REPORTED", "CATALOG_CANDIDATE", "STALE"
        )}
        latest = ""
        for row in rows:
            evidence = str(row.get("evidence") or "STALE")
            counts[evidence] = counts.get(evidence, 0) + 1
            observed = str(row.get("observedAt") or "")
            if observed > latest:
                latest = observed
        return {
            "total": len(rows),
            "local": counts["LOCAL_CONFIRMED"],
            "live": counts["LIVE_REPORTED"],
            "catalog": counts["CATALOG_CANDIDATE"],
            "stale": counts["STALE"],
            "withHotspots": sum(bool(row.get("hotspots")) for row in rows),
            "latestAt": latest.replace("T", " ")[:16] if latest else "—",
        }

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

    def _service_status(self):
        return self._cached_derived(
            "service_status", self._connection_revision,
            self._build_service_status,
        )

    def _build_service_status(self):
        health = self._journal_health()
        queue_counts = {
            status: sum(
                1 for row in self._eddn_queue
                if row.get("status") == status
            )
            for status in ("queued", "retry", "sending", "failed")
        }
        return [
            {
                "name": "JOURNAL",
                "status": (
                    health["status"] if self._journal_auto else "PAUSED"
                ),
                "detail": (
                    f"{health['latestFile'] or 'No file'} · "
                    f"{health['ageSeconds']} s"
                    if self._journal_auto else "Automatic updates disabled"
                ),
                "healthy": bool(
                    self._journal_auto and health["parserOk"]
                ),
            },
            {
                "name": "INARA",
                "status": (
                    "WORKING" if self._inara_busy
                    else "ENABLED" if self._inara_config.get("consent")
                    else "OFF"
                ),
                "detail": self._inara_status,
                "healthy": (
                    not self._inara_busy
                    and not self._inara_status.startswith("FAILED")
                ),
            },
            {
                "name": "FRONTIER CAPI",
                "status": (
                    "WORKING" if self._frontier_busy
                    else "CONNECTED" if self._frontier_tokens is not None
                    else "OFF"
                ),
                "detail": self._frontier_status,
                "healthy": (
                    not self._frontier_busy
                    and "FAILED" not in self._frontier_status
                    and "ERROR" not in self._frontier_status
                ),
            },
            {
                "name": "EDDN",
                "status": (
                    "WORKING" if self._eddn_busy
                    else "ENABLED" if self._eddn_config.get("consent")
                    else "OFF"
                ),
                "detail": self._eddn_status,
                "healthy": not any((
                    queue_counts["failed"], queue_counts["retry"]
                )),
            },
            {
                "name": "QUEUE",
                "status": str(sum(queue_counts.values())),
                "detail": (
                    f"{queue_counts['queued']} queued · "
                    f"{queue_counts['retry']} retry · "
                    f"{queue_counts['failed']} failed"
                ),
                "healthy": queue_counts["failed"] == 0,
            },
        ]

    ship = Property(str, lambda self: self._get("ship", "No ship"), notify=CoreControllerMixin.stateChanged)
    ships = Property("QStringList", lambda self: self._get("ships", []), notify=CoreControllerMixin.stateChanged)
    navigationOrder = Property(
        "QVariantList", lambda self: list(self._navigation_order),
        notify=CoreControllerMixin.uiChanged,
    )
    fleetKnown = Property(
        bool, lambda self: bool(self._get("fleetKnown", False)), notify=CoreControllerMixin.stateChanged
    )
    emptyStateReason = Property(
        str, lambda self: str(self._get("emptyStateReason", "")), notify=CoreControllerMixin.stateChanged
    )
    activeShip = Property(
        str, lambda self: self._get("activeShip", ""), notify=CoreControllerMixin.stateChanged
    )
    followActiveShip = Property(
        bool, lambda self: self._follow_active_ship, notify=CoreControllerMixin.stateChanged
    )
    system = Property(str, lambda self: self._get("system", "Unknown"), notify=CoreControllerMixin.stateChanged)
    nextAction = Property(str, lambda self: self._next_action(), notify=CoreControllerMixin.stateChanged)
    operationAction = Property(
        "QVariantMap", lambda self: self._operation_action(),
        notify=CoreControllerMixin.operationsChanged,
    )
    completion = Property(float, lambda self: float(self._get("completion", 0.0)), notify=CoreControllerMixin.stateChanged)
    materialStatus = Property(
        str, lambda self: str(self._get("materialStatus", "MISSING")),
        notify=CoreControllerMixin.stateChanged,
    )
    completionReliable = Property(
        bool, lambda self: bool(self._get("completionReliable", False)),
        notify=CoreControllerMixin.stateChanged,
    )
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
    covered = Property(int, lambda self: int(self._get("covered", 0)), notify=CoreControllerMixin.stateChanged)
    required = Property(int, lambda self: int(self._get("required", 0)), notify=CoreControllerMixin.stateChanged)
    calculationWarning = Property(
        str, lambda self: str(self._get("calculationWarning", "")),
        notify=CoreControllerMixin.stateChanged,
    )
    missingKinds = Property(int, lambda self: int(self._get("missingKinds", 0)), notify=CoreControllerMixin.stateChanged)
    trades = Property("QVariantList", lambda self: self._get("trades", []), notify=materialsChanged)
    traderRoute = Property(
        "QVariantList", lambda self: self._get("traderRoute", []),
        notify=materialsChanged,
    )
    tradeHistory = Property(
        "QVariantList", lambda self: self._get("tradeHistory", []),
        notify=materialsChanged,
    )
    routeDistance = Property(
        float, lambda self: float(self._get("routeDistance", 0.0)),
        notify=CoreControllerMixin.stateChanged,
    )
    recentCrafts = Property(
        "QVariantList", lambda self: self._get("recentCrafts", []),
        notify=CoreControllerMixin.stateChanged,
    )
    lastChangeReason = Property(
        str, lambda self: self._get("lastChangeReason", ""),
        notify=CoreControllerMixin.stateChanged,
    )
    blueprints = Property("QVariantList", lambda self: self._get("blueprints", []), notify=CoreControllerMixin.wishlistChanged)
    activeBlueprints = Property(
        "QVariantList",
        lambda self: [
            row for row in self._get("blueprints", [])
            if str(row.get("targetStatus") or "") != "completed"
        ],
        notify=CoreControllerMixin.wishlistChanged,
    )
    materials = Property("QVariantList", lambda self: self._get("materials", []), notify=materialsChanged)
    missingMaterials = Property(
        "QVariantList",
        lambda self: [
            row for row in self._get("materials", [])
            if int(row.get("missing", 0) or 0) > 0
        ],
        notify=materialsChanged,
    )
    # float, not int: a career total can exceed the 32-bit range a plain
    # int Property would truncate to; QML's Number already handles this
    # exactly up to far more than any realistic credit balance.
    engineers = Property(
        "QVariantList", lambda self: self._engineer_index(),
        notify=CoreControllerMixin.operationsChanged,
    )
    techBrokerGuide = Property(
        "QVariantList", lambda self: self._get("techBrokerGuide", []),
        notify=CoreControllerMixin.operationsChanged,
    )
    techBrokerTrack = Property(
        "QVariantMap", lambda self: self._get("techBrokerTrack", {}),
        notify=CoreControllerMixin.operationsChanged,
    )
    trackedItems = Property(
        "QVariantList", lambda self: self._get("trackedItems", []),
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
    activity = Property(str, lambda self: self._activity, notify=activityChanged)
    rendererMode = Property(str, lambda self: self._renderer_mode, notify=rendererChanged)
    rendererActive = Property(str, lambda self: self._renderer_active, notify=rendererChanged)
    restartRequired = Property(bool, lambda self: self._restart_required, notify=rendererChanged)
    uiScale = Property(float, lambda self: self._ui_scale, notify=CoreControllerMixin.uiChanged)
    theme = Property(str, lambda self: self._theme, notify=CoreControllerMixin.uiChanged)
    interfaceLanguage = Property(
        str, lambda self: self._interface_language, notify=CoreControllerMixin.uiChanged,
    )
    interfaceLanguages = Property(
        "QVariantList",
        lambda self: [
            {
                "id": language,
                "label": self._translations.translate(
                    language, "language.name", language.upper()
                ),
            }
            for language in SUPPORTED_LANGUAGES
        ],
        constant=True,
    )
    reducedMotion = Property(bool, lambda self: self._reduced_motion, notify=CoreControllerMixin.uiChanged)
    enhancedVisuals = Property(
        bool, lambda self: self._enhanced_visuals, notify=CoreControllerMixin.uiChanged,
    )
    onboardingComplete = Property(
        bool, lambda self: self._onboarding_complete, notify=CoreControllerMixin.uiChanged
    )
    lastPage = Property(int, lambda self: self._last_page, notify=CoreControllerMixin.uiChanged)
    debugMode = Property(bool, lambda self: self._debug_mode, notify=CoreControllerMixin.uiChanged)
    journalAuto = Property(
        bool, lambda self: self._journal_auto, notify=CoreControllerMixin.uiChanged,
    )
    backgroundMode = Property(
        bool, lambda self: self._background_mode, notify=CoreControllerMixin.uiChanged,
    )
    autostartEnabled = Property(
        bool, lambda self: self._autostart_enabled, notify=CoreControllerMixin.uiChanged,
    )
    systemTrayAvailable = Property(
        bool, lambda self: self._system_tray_available, notify=CoreControllerMixin.uiChanged,
    )
    backgroundRuntimeStatus = Property(
        str, lambda self: self._background_runtime_status, notify=CoreControllerMixin.uiChanged,
    )
    historyExportBusy = Property(
        bool, lambda self: self._history_export_busy,
        notify=CoreControllerMixin.connectionChanged,
    )
    stateFindRefreshStatus = Property(
        str, lambda self: self._state_find_refresh_status,
        notify=hgeChanged,
    )
    stateFindCacheSummary = Property(
        "QVariantMap", lambda self: self._state_find_cache_summary(),
        notify=hgeChanged,
    )
    stateFindRefreshSummary = Property(
        "QVariantMap", lambda self: dict(self._last_state_find_refresh_stats),
        notify=hgeChanged,
    )
    miningCommodityFilters = Property(
        "QStringList", lambda self: self._mining_commodity_filters(),
        notify=CoreControllerMixin.stateChanged,
    )
    miningRevision = Property(
        int,
        lambda self: self._state_revision + len(
            self._mining_catalog.get("candidates", [])
            if isinstance(self._mining_catalog, dict) else []
        ),
        notify=CoreControllerMixin.stateChanged,
    )
    miningCacheSummary = Property(
        "QVariantMap", lambda self: self._mining_cache_summary(),
        notify=CoreControllerMixin.stateChanged,
    )
    miningSyncBusy = Property(
        bool, lambda self: self._mining_sync_busy, notify=miningChanged,
    )
    miningSyncStatus = Property(
        str, lambda self: self._mining_sync_status, notify=miningChanged,
    )
    edmcParallelStatus = Property(
        "QVariantMap", lambda self: self._edmc_parallel_status(),
        notify=CoreControllerMixin.connectionChanged,
    )
    traderSyncBusy = Property(
        bool, lambda self: self._trader_sync_busy, notify=CoreControllerMixin.connectionChanged,
    )
    traderSyncStatus = Property(
        str, lambda self: self._trader_sync_status, notify=CoreControllerMixin.connectionChanged,
    )
    techBrokerSyncBusy = Property(
        bool, lambda self: self._tech_broker_sync_busy, notify=CoreControllerMixin.connectionChanged,
    )
    techBrokerSyncStatus = Property(
        str, lambda self: self._tech_broker_sync_status, notify=CoreControllerMixin.connectionChanged,
    )
    spanshCatalogSyncBusy = Property(
        bool,
        lambda self: self._trader_sync_busy or self._tech_broker_sync_busy,
        notify=CoreControllerMixin.connectionChanged,
    )
    spanshCatalogSyncStatus = Property(
        str,
        lambda self: (
            f"MATERIAL TRADERS · {self._trader_sync_status}\n"
            f"TECH BROKERS · {self._tech_broker_sync_status}"
        ),
        notify=CoreControllerMixin.connectionChanged,
    )
    hgeTargets = Property(
        "QVariantList", lambda self: self._hge_targets(),
        notify=hgeChanged,
    )
    hgeFinderRows = Property(
        "QVariantList", lambda self: self._hge_finder_rows(),
        notify=hgeChanged,
    )
    hgeCandidateRows = Property(
        "QVariantList", lambda self: self._hge_candidate_rows(),
        notify=hgeChanged,
    )
    hgeMaterialFilters = Property(
        "QStringList", lambda self: self._hge_material_filters(),
        notify=hgeChanged,
    )
    stateFindTypeFilters = Property(
        "QStringList",
        lambda self: [
            "ALL FIND TYPES", "HGE", "CONFLICT_ZONE",
            "SEEKING_MEDS", "SEEKING_FOODS",
        ],
        notify=hgeChanged,
    )
    stateFindStateFilters = Property(
        "QStringList",
        lambda self: self._state_find_filter_values("stateValues", "ALL STATES"),
        notify=hgeChanged,
    )
    stateFindAllegianceFilters = Property(
        "QStringList",
        lambda self: self._state_find_filter_values(
            "allegianceValues", "ALL ALLEGIANCES"
        ),
        notify=hgeChanged,
    )
    hgeUnverifiedSummary = Property(
        "QVariantMap",
        lambda self: recent_unverified_hge_summary(self._hge_sightings),
        notify=hgeChanged,
    )
    serviceStatus = Property(
        "QVariantList", lambda self: self._service_status(),
        notify=CoreControllerMixin.connectionChanged,
    )
    interfaceActivity = Property(
        "QVariantList",
        lambda self: build_interface_activity_feed(
            self._inara_receipts, self._eddn_queue, self._frontier_last_sync,
        ),
        notify=CoreControllerMixin.connectionChanged,
    )
    journalHealth = Property(
        "QVariantMap", lambda self: self._journal_health(),
        notify=journalHealthChanged,
    )
    diagnosticLogs = Property(
        "QStringList", lambda self: self._diagnostic_logs(),
        notify=diagnosticsChanged,
    )
    crashReports = Property(
        "QVariantList", lambda self: self._crash_reports(),
        notify=diagnosticsChanged,
    )
    selectedMaterial = Property(
        "QVariantMap",
        lambda self: self._selected_material,
        notify=materialSelectionChanged,
    )
    journalPath = Property(str, lambda self: str(journal_dir()), notify=CoreControllerMixin.stateChanged)
    dataPath = Property(
        str,
        lambda self: str(self.package_root / "ed_data"),
        constant=True,
    )
    appVersion = Property(
        str, lambda self: APP_VERSION, constant=True
    )
    blueprintCatalog = Property(
        "QVariantList", lambda self: self._blueprint_catalog,
        notify=CoreControllerMixin.engineeringChanged,
    )
    selectedBlueprint = Property(
        "QVariantMap", lambda self: self._selected_blueprint,
        notify=CoreControllerMixin.engineeringChanged,
    )
    currentGrade = Property(
        int, lambda self: self._current_grade, notify=CoreControllerMixin.engineeringChanged
    )
    targetGrade = Property(
        int, lambda self: self._target_grade, notify=CoreControllerMixin.engineeringChanged
    )
    editingGradeComplete = Property(
        bool, lambda self: self._editing_grade_complete, notify=CoreControllerMixin.engineeringChanged
    )
    selectedExperimentalId = Property(
        str, lambda self: self._selected_experimental_id,
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
    fleetStatus = Property(
        str, lambda self: self._fleet_status, notify=CoreControllerMixin.engineeringChanged
    )
    armedPlanId = Property(
        str, lambda self: self._armed_plan_id, notify=CoreControllerMixin.engineeringChanged
    )
    editingPlanIndex = Property(
        int, lambda self: self._editing_plan_index, notify=CoreControllerMixin.engineeringChanged
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
    traderPreference = Property(
        str, lambda self: self._trader_preference, notify=CoreControllerMixin.uiChanged,
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
    selectedShipType = Property(
        str, lambda self: str(self._state.get("selectedShipType") or ""),
        notify=CoreControllerMixin.stateChanged,
    )
    selectedShipStats = Property(
        "QVariantMap",
        lambda self: self._state.get("selectedShipStats", {}),
        notify=CoreControllerMixin.stateChanged,
    )
    buildImportPreview = Property(
        "QVariantMap", lambda self: self._build_import_preview,
        notify=CoreControllerMixin.engineeringChanged,
    )

    @Slot()
    def refresh(self) -> None:
        """Coalesce requests; never rebuild Journal state on the GUI thread."""
        self._refresh_revision += 1
        self._refresh_dirty = True
        if not self._refresh_in_flight:
            self.refreshDebounceTimer.start()

    @Slot()
    def _launch_state_refresh(self) -> None:
        if self._refresh_in_flight or not self._refresh_dirty:
            return
        self._refresh_in_flight = True
        self._refresh_dirty = False
        revision = self._refresh_revision
        package_root = self.package_root
        selected_ship = self._selected_ship
        follow_active_ship = self._follow_active_ship
        preferred_plan_id = self._armed_plan_id
        trader_preference = self._trader_preference
        hge_sightings = list(self._hge_sightings)
        eddn_context = dict(self._eddn_context)
        eddn_queue = list(self._eddn_queue)
        eddn_config = dict(self._eddn_config)
        hge_revision = self._hge_revision
        eddn_revision = self._eddn_revision

        # Threading contract: the worker runs off the Qt thread and must read
        # only the locals captured above, never live ``self._*`` mutable state.
        def worker():
            try:
                state = build_state(
                    package_root, selected_ship, preferred_plan_id,
                    trader_preference,
                )
                craft_batch = state.get("_craftBatch", {})
                active_ship = str(state.get("activeShip") or "")
                if (
                    follow_active_ship and state.get("activeShipKnown")
                    and active_ship != state.get("ship")
                ):
                    state = build_state(
                        package_root, active_ship, preferred_plan_id,
                        trader_preference,
                    )
                    state["_craftBatch"] = craft_batch
                state["_logbookEntries"] = logbook_entries(package_root)
                state_find_rows = self._build_state_find_rows(
                    state, hge_sightings, eddn_context,
                    eddn_queue, eddn_config,
                )
                self.refreshStateReady.emit((
                    revision, state, state_find_rows,
                    hge_revision, eddn_revision,
                ))
            except Exception as exc:
                LOGGER.exception("Journal state refresh failed")
                self.refreshStateFailed.emit((revision, str(exc)))

        threading.Thread(
            target=worker, name=f"journal-state-{revision}", daemon=True,
        ).start()

    @Slot(object)
    def _finish_refresh_state(self, payload: object) -> None:
        revision, state = payload[:2]
        state_find_rows = payload[2] if len(payload) > 2 else None
        source_hge_revision = payload[3] if len(payload) > 3 else self._hge_revision
        source_eddn_revision = payload[4] if len(payload) > 4 else self._eddn_revision
        self._refresh_in_flight = False
        if revision != self._refresh_revision or self._refresh_dirty:
            self._refresh_dirty = True
            self._launch_state_refresh()
            return
        if not isinstance(state, dict):
            self._fail_refresh_state((revision, "Journal state was not a mapping."))
            return
        profile_context = state.pop("_profileContext", None)
        if (
            isinstance(profile_context, ProfileContext)
            and profile_context != self.profile_context
            and not self._switch_profile_context(profile_context)
        ):
            self._refresh_dirty = True
            self.refreshDebounceTimer.start()
            return
        previous = self._state
        self._data_dir = runtime_data_dir(self.profile_context)
        self._logbook_entries = list(state.pop("_logbookEntries", []))
        self._logbook_revision += 1
        craft_batch = dict(state.pop("_craftBatch", {}) or {})
        state = self._state_with_frontier_profile(
            state, getattr(self, "_frontier_profile", {})
        )
        self._state = state
        if (
            previous
            and str(previous.get("activeShipId") or "")
            != str(self._state.get("activeShipId") or "")
        ):
            self.clearCraftConfirmation()
        self._selected_ship = self._state.get("ship", "")
        applied_crafts = list(craft_batch.get("applied") or [])
        if craft_batch.get("preferredPlanApplied"):
            self._armed_plan_id = ""
        if applied_crafts:
            tracked = applied_crafts[-1]
            craft = dict(tracked.get("event") or {})
            tracking = dict(tracked.get("result") or {})
            blueprint = str(
                craft.get("BlueprintName_Localised")
                or craft.get("BlueprintName") or "Engineering modification"
            )
            level = int(craft.get("Level", 0) or 0)
            experimental = str(
                craft.get("ExperimentalEffect_Localised")
                or craft.get("ExperimentalEffect") or ""
            )
            tracking_reason = str(tracking.get("reason") or "")
            prefix = (
                "CRAFT TRACKED" if tracking.get("status") == "applied"
                else "CRAFT SEEN"
            )
            self._craft_confirmation = (
                f"{prefix} · {blueprint}"
                + (f" · G{level}" if level else "")
                + (f" · {experimental}" if experimental else "")
                + (f" · {tracking_reason}" if tracking_reason else "")
            )
            self._engineering_status = self._craft_confirmation
            self._activity = self._craft_confirmation
            self.activityChanged.emit()
            self.engineeringChanged.emit()
            self.craftConfirmationTimer.start()
        if previous and not applied_crafts:
            before = int(round(float(previous.get("completion", 0)) * 100))
            after = int(round(float(self._state.get("completion", 0)) * 100))
            reason = str(self._state.get("lastChangeReason") or "Journal update")
            self._activity = (
                f"Build readiness {before}% → {after}% · {reason}"
            )
            # Routine Journal polling is visible in Operations and History.
            # It must not repeatedly interrupt the Commander with a toast.
        elif not applied_crafts:
            self._activity = "Journal synchronized · live inventory loaded"
        if previous:
            self._record_exobiology_step_positions(
                previous.get("exobiologyFindings"),
                self._state.get("exobiologyFindings"),
            )
            new_target_alert = self._new_current_system_exobiology_target(
                previous, self._state,
            )
            if new_target_alert and not applied_crafts:
                self._activity = new_target_alert
                self.activityChanged.emit()
        self._log_consistency_issues(self._state)
        self._journal_state_ready = True
        self._publish_full_state(previous)
        if (
            isinstance(state_find_rows, list)
            and source_hge_revision == self._hge_revision
            and source_eddn_revision == self._eddn_revision
        ):
            self._derived_cache["state_find_rows"] = ((
                self._state_revision, self._hge_revision, self._eddn_revision,
            ), state_find_rows)
        self.activityChanged.emit()
        if getattr(self, "_journal_auto", False):
            self._queue_inara_journal_scan()

    @Slot(object)
    def _fail_refresh_state(self, payload: object) -> None:
        revision, message = payload
        self._refresh_in_flight = False
        if revision != self._refresh_revision or self._refresh_dirty:
            self._refresh_dirty = True
            self._launch_state_refresh()
            return
        self._activity = f"Journal refresh failed · {message}"
        self.activityChanged.emit()

    @Slot(str)
    def copySystem(self, system):
        system = str(system or "").strip()
        if not system:
            return
        QGuiApplication.clipboard().setText(system)
        self._activity = f"ROUTE · {system} copied to clipboard"
        self.activityChanged.emit()

    @Slot(str)
    def copyCoordinates(self, coordinates):
        coordinates = str(coordinates or "").strip()
        if not coordinates:
            return
        QGuiApplication.clipboard().setText(coordinates)
        self._activity = f"FARM · coordinates {coordinates} copied to clipboard"
        self.activityChanged.emit()

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

    @Slot(str)
    def setSelectedShip(self, ship):
        ship = str(ship or "")
        if ship and ship != self._selected_ship:
            self.clearCraftConfirmation()
            self._follow_active_ship = False
            self._selected_ship = ship
            self.refresh()

    @Slot()
    def followCurrentShip(self):
        self.clearCraftConfirmation()
        self._follow_active_ship = True
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

    @Slot()
    def exportShipOutfitting(self):
        ship_id = str(self._state.get("selectedShipId") or "")
        if not ship_id:
            self._fleet_status = "Outfitting export unavailable: selected ship has no Journal identity."
            self._engineering_status = self._fleet_status
            self.engineeringChanged.emit()
            return
        try:
            events = profiled_journal_events()
            payload = build_loadout_export(
                events, ship_id, "", latest_loadout_slots(events, ship_id),
                self._experimentals,
            )
            safe_ship = "".join(
                character if character.isalnum() else "_"
                for character in self._selected_ship
            ).strip("_") or "ship"
            json_path, text_path = write_loadout_export(
                self.config_dir / "exports", safe_ship, payload
            )
            QGuiApplication.clipboard().setText(str(json_path))
        except Exception as exc:
            logging.exception("Outfitting export failed")
            self._fleet_status = f"Outfitting export failed: {exc}"
            self._engineering_status = self._fleet_status
            self.engineeringChanged.emit()
            return
        self._fleet_status = (
            f"{payload['status']} outfitting exported; JSON path copied · "
            f"TXT: {text_path.name}"
        )
        self._engineering_status = self._fleet_status
        self.engineeringChanged.emit()

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

    @Slot(int)
    def setCurrentGrade(self, grade):
        self.clearCraftConfirmation()
        self._current_grade = max(0, min(int(grade), self._target_grade))
        self.engineeringChanged.emit()

    @Slot(int)
    def setTargetGrade(self, grade):
        self.clearCraftConfirmation()
        maximum = int(self._selected_blueprint.get("maxGrade", 5) or 5)
        self._target_grade = max(1, min(int(grade), maximum))
        self._current_grade = min(self._current_grade, self._target_grade)
        self.engineeringChanged.emit()

    @Slot(str)
    def setSelectedExperimental(self, identifier):
        self.clearCraftConfirmation()
        self._selected_experimental_id = str(identifier or "")
        self.engineeringChanged.emit()

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

    @Slot(str, str)
    def previewBuildImport(self, source, target_ship):
        target_ship = str(target_ship or "").strip()
        metadata = read_json(self._data_dir / "ship_metadata.json", {})
        target = metadata.get(target_ship, {}) if isinstance(metadata, dict) else {}
        target_type = str(target.get("type") or "").strip()
        if target_ship not in self._state.get("ships", []) or not target_type:
            self._build_import_preview = empty_build_import_preview(
                "Select a verified ship from the current Commander fleet."
            )
            self._build_import_target = ""
            self.engineeringChanged.emit()
            return
        try:
            preview = preview_build(
                source, target_type,
                read_json(self._reference_data_dir / "blueprints.json", []),
                self._experimentals, module_matches_type,
                physical_slots=self._state.get("engineeringShipSlots", []),
                ship_catalog=self._ship_catalog,
            )
        except BuildImportError as exc:
            preview = empty_build_import_preview(str(exc))
        except Exception as exc:
            logging.exception("Build import preview failed")
            preview = empty_build_import_preview(
                f"Build preview failed: {exc}"
            )
        preview["targetShip"] = target_ship
        preview["targetShipType"] = target_type
        self._build_import_preview = preview
        self._build_import_target = target_ship if preview.get("compatible") else ""
        self._engineering_status = (
            f"Build preview: {int(preview.get('recognized', 0) or 0)} "
            f"engineered module(s) mapped · {preview.get('status', 'PARTIAL')}."
            if preview.get("compatible") else "Build import needs attention."
        )
        self.engineeringChanged.emit()

    @Slot()
    def clearBuildImport(self):
        self._build_import_preview = empty_build_import_preview()
        self._build_import_target = ""
        self.engineeringChanged.emit()

    @Slot(str)
    def acceptCurrentOutfittingSlot(self, slot):
        """Accept the current slot and discard its bound replacement plan."""
        slot = str(slot or "").strip()
        ship_id = str(self._state.get("selectedShipId") or "")
        row = next((
            item for item in self._state.get("engineeringShipSlots", [])
            if isinstance(item, dict)
            and str(item.get("slot") or "") == slot
        ), {})
        source_slot = str(row.get("desiredSourceSlot") or slot)
        if not ship_id or not slot or not row.get("moduleChange"):
            return
        desired_path = self._data_dir / "desired_outfitting.json"
        payload = read_json(desired_path, {})
        payload = payload if isinstance(payload, dict) else {}
        original_payload = deepcopy(payload)
        desired_slots = payload.get(ship_id, {})
        desired_slots = desired_slots if isinstance(desired_slots, dict) else {}
        if source_slot not in desired_slots:
            return
        desired_module = str(desired_slots.get(source_slot) or "")
        desired_slots.pop(source_slot, None)
        if desired_slots:
            payload[ship_id] = desired_slots
        else:
            payload.pop(ship_id, None)
        blueprint_path = self._data_dir / "ship_blueprints.json"
        blueprint_payload = read_json(blueprint_path, {})
        blueprint_payload = (
            blueprint_payload if isinstance(blueprint_payload, dict) else {}
        )
        selected_ship = str(getattr(self, "_selected_ship", "") or "")
        existing_tasks = blueprint_payload.get(selected_ship, [])
        filtered_tasks, removed_plans = discard_bound_module_plans(
            existing_tasks if isinstance(existing_tasks, list) else [],
            ship_id, {slot, source_slot}, desired_module,
        )
        if removed_plans:
            blueprint_payload[selected_ship] = filtered_tasks

        if not atomic_write(
            desired_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        ):
            self._engineering_status = (
                "Outfitting change could not be saved."
            )
            self.engineeringChanged.emit()
            return
        if removed_plans and not atomic_write(
            blueprint_path,
            json.dumps(blueprint_payload, ensure_ascii=False, indent=2) + "\n",
        ):
            atomic_write(
                desired_path,
                json.dumps(original_payload, ensure_ascii=False, indent=2) + "\n",
            )
            self._engineering_status = (
                "Outfitting change could not be saved together with its "
                "engineering plan."
            )
            self.engineeringChanged.emit()
            return
        self._engineering_status = (
            f"Current state accepted for {slot}; outfitting request"
            + (
                " and linked engineering plan removed."
                if removed_plans else " removed."
            )
        )
        self.refresh()

    @Slot()
    def applyBuildImport(self):
        preview = self._build_import_preview
        target_ship = self._build_import_target
        metadata = read_json(self._data_dir / "ship_metadata.json", {})
        target = metadata.get(target_ship, {}) if isinstance(metadata, dict) else {}
        if (
            not preview.get("compatible") or not target_ship
            or target_ship not in self._state.get("ships", [])
            or normalize(target.get("type")) != normalize(preview.get("shipType"))
        ):
            self._engineering_status = (
                "Build import rejected: target ship no longer matches the preview."
            )
            self._build_import_preview["actionMessage"] = self._engineering_status
            self._build_import_preview["actionError"] = True
            self.engineeringChanged.emit()
            return
        applied = 0
        duplicates = 0
        desired_path = self._data_dir / "desired_outfitting.json"
        desired_payload = read_json(desired_path, {})
        if not isinstance(desired_payload, dict):
            desired_payload = {}
        ship_id = str(target.get("id") or "")
        desired_slots = desired_payload.get(ship_id, {})
        if not isinstance(desired_slots, dict):
            desired_slots = {}
        module_changes = 0
        for row in preview.get("rows", []):
            if not isinstance(row, dict) or not row.get("slotBound"):
                continue
            slot = str(row.get("slot") or "")
            desired_module = str(row.get("desiredModule") or "")
            if not slot or not desired_module:
                continue
            if row.get("moduleChange"):
                desired_slots[slot] = desired_module
                module_changes += 1
            else:
                desired_slots.pop(slot, None)
        if desired_slots:
            desired_payload[ship_id] = desired_slots
        else:
            desired_payload.pop(ship_id, None)
        atomic_write(
            desired_path,
            json.dumps(desired_payload, ensure_ascii=False, indent=2) + "\n",
        )
        import_baseline = journal_craft_baseline(
            profiled_journal_events(), target.get("id", "")
        )
        for row in preview.get("rows", []):
            if (
                not isinstance(row, dict)
                or row.get("status") not in {"ready", "partial"}
                or not row.get("slotBound")
            ):
                continue
            mode = str(row.get("planMode") or "")
            group_id = str(row.get("blueprintGroup") or "")
            grades = self._blueprint_groups.get(group_id, [])
            effect_id = str(row.get("experimentalId") or "")
            effect = next((
                value for value in self._experimentals
                if str(value.get("ExperimentalId") or value.get("Name")) == effect_id
            ), None)
            binding = {
                "ship_id": str(target.get("id") or ""),
                "slot": str(row.get("slot") or ""),
                # The import describes the desired physical module, not the
                # module currently occupying the slot. Binding to that target
                # prevents refresh reconciliation from moving the plan to an
                # old same-family module before the replacement is installed.
                "module_id": str(row.get("desiredModule") or ""),
            }
            instance = str(row.get("slot") or row.get("module") or "Module")[:48]
            tasks = []
            if mode == "experimental_only" and effect:
                plan = build_experimental_plan(
                    effect, instance=instance,
                    module_type=str(row.get("moduleType") or ""),
                    blueprint_group_id=group_id,
                    journal_baseline=import_baseline, **binding,
                )
                if plan:
                    tasks.append(plan)
            elif mode in {"grade_only", "combined"} and grades:
                plan = build_engineering_plan(
                    grades, int(row.get("currentGrade", 0) or 0),
                    int(row.get("grade", 0) or 0),
                    instance=instance,
                    experimental_id=effect_id if mode == "combined" else "",
                    experimental_name=(
                        str(effect.get("Name") or "")
                        if mode == "combined" and effect else ""
                    ),
                    plan_mode=mode, **binding,
                    journal_baseline=import_baseline,
                    grade_progress=row.get("gradeProgress"),
                    crafts_completed=row.get("craftsCompleted"),
                )
                if plan:
                    plan[0]["_Planner"]["experimental_complete"] = bool(
                        row.get("experimentalComplete")
                    )
                    tasks.append(plan)
                    if mode == "combined" and effect:
                        effect_record = deepcopy(effect)
                        effect_record.update({
                            "Kind": "ExperimentalEffect", "Grade": None,
                            "_ParentPlanId": plan[0]["_Planner"]["plan_id"],
                            # A stable anchor independent of the parent
                            # plan_id (fresh every apply), so re-applying an
                            # import after progress advances is recognized
                            # as the same physical target, not a duplicate.
                            "_BoundShipId": str(binding.get("ship_id") or ""),
                            "_BoundSlot": str(binding.get("slot") or ""),
                            "_BoundModuleId": str(binding.get("module_id") or ""),
                        })
                        tasks.append([effect_record])
            if not tasks:
                continue
            added = write_ship_tasks(
                self._data_dir / "ship_blueprints.json", target_ship, tasks
            )
            if added:
                applied += 1
            else:
                duplicates += 1
        self._engineering_status = (
            f"Build import applied to {target_ship}: {applied} module plan(s)"
            + (f" · {module_changes} module replacement(s) tracked"
               if module_changes else "")
            + (f" · {duplicates} duplicate(s) skipped" if duplicates else "")
            + "."
        )
        if applied == 0 and duplicates == 0 and module_changes == 0:
            self._engineering_status = (
                "Build import applied no plans. Review the preview warnings and "
                "select at least one safely mapped engineered module."
            )
        self._build_import_preview["applied"] = applied
        self._build_import_preview["duplicates"] = duplicates
        self._build_import_preview["moduleChangesApplied"] = module_changes
        self._build_import_preview["actionMessage"] = self._engineering_status
        self._build_import_preview["actionError"] = (
            applied == 0 and duplicates == 0 and module_changes == 0
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




































    def _switch_profile_context(self, context: ProfileContext) -> bool:
        if context == self.profile_context:
            return True
        if self._eddn_busy:
            LOGGER.warning("EDDN profile switch deferred while an upload is active")
            return False
        self._active_inara_request = None
        self._inara_busy = False
        self._active_mining_request = None
        self._mining_sync_busy = False
        self._mining_sync_status = "Ready"
        self._pending_mining_candidates = []
        self._last_commander_status_stamp = None
        self._last_bgs_batch_monotonic = time.monotonic()
        self._last_mining_batch_monotonic = time.monotonic()
        self._profile_generation += 1
        self._inara_scan_token = getattr(self, "_inara_scan_token", 0) + 1
        self._inara_scan_in_flight = False
        self._inara_scan_dirty = False
        self._frontier_request_token = getattr(
            self, "_frontier_request_token", 0
        ) + 1
        self._frontier_busy = False
        self._frontier_authorization = None
        self._frontier_profile = {}
        if getattr(self, "_frontier_watchdog", None) is not None:
            self._frontier_watchdog.stop()
        self._bind_profile_paths(context)
        self._frontier_credential_store = FrontierCredentialStore(
            self.frontier_credentials_file
        )
        self._frontier_config = self._load_frontier_config()
        self._frontier_last_sync = ""
        try:
            self._frontier_tokens = self._frontier_credential_store.load()
            self._frontier_status = (
                "CONNECTED LOCALLY · Refresh to verify the Frontier session."
                if self._frontier_tokens else
                "NOT CONNECTED · Frontier approval is required before first login."
            )
        except FrontierCredentialError:
            self._frontier_tokens = None
            self._frontier_status = "CREDENTIAL STORAGE ERROR"
        self._frontier_client = (
            FrontierCapiClient(
                self._frontier_tokens.access_token,
                token_type=self._frontier_tokens.token_type,
            )
            if self._frontier_tokens else None
        )
        self._history_archive = HistoryArchive(self.history_archive_file)
        self._commander_credit_snapshots = self._history_archive.records(
            "commander_credit_snapshots"
        )
        self._history_export_busy = False
        self._fleet_images = self._load_fleet_images()
        self._eddn_profile_key = context.key
        self._eddn_profile_identity = context.identity
        self._eddn_journal_root = context.journal_root

        self._logbook_notes = load_logbook_notes(self.config_dir)
        self._inara_config = self._load_inara_config()
        self._inara_cache = self._read_local_json(
            self.inara_journal_cache_file, {}
        )
        if not isinstance(self._inara_cache, dict):
            self._inara_cache = {}
        self._inara_receipts = self._load_inara_receipts()
        if len(self._inara_receipts) > INARA_ACTIVE_RECEIPT_LIMIT:
            self._save_inara_receipts()
        self._inara_pending_events = []
        self._inara_pending_fingerprints = []
        self._inara_inflight_fingerprints = []
        self._inara_recovery_candidate_file = ""
        self._inara_pending_since = 0.0
        self._inara_retry_not_before = 0.0
        self._inara_failure_count = 0
        self._inara_material_fingerprint = ""
        self._inara_request_times = []
        self._inara_last_request_at = 0.0
        now_wall = time.time()
        self._inara_request_wall_times = [
            float(value) for value in self._inara_config.get("request_times", [])
            if isinstance(value, (int, float)) and now_wall - float(value) < 60
        ]

        self._eddn_config = self._load_eddn_config()
        self._eddn_queue = self._load_eddn_queue()
        self._save_eddn()
        self._load_eddn_cursor_state()

        self._hge_sightings = self._read_local_json(self.hge_cache_file, [])
        if not isinstance(self._hge_sightings, list):
            self._hge_sightings = []
        self._mining_catalog = {"candidates": []}
        self._mining_rows_build_token = getattr(
            self, "_mining_rows_build_token", 0
        ) + 1
        self._mining_rows_build_in_flight = False
        self._mining_rows_build_dirty = False
        self._mining_rows_cache_key = None
        self._mining_rows_cache = []
        self._mining_find_cache_key = None
        self._mining_find_cache = []
        if hasattr(self, "_network_threads_lock"):
            self._start_mining_catalog_load()
        else:
            # Lightweight test/controller shells have no worker runtime.
            self._mining_catalog = self._read_local_json(
                self.mining_catalog_file, {"candidates": []}
            )
            if not isinstance(self._mining_catalog, dict):
                self._mining_catalog = {"candidates": []}
        self._trader_sync_status = self._load_trader_sync_status()
        self._tech_broker_sync_status = self._load_tech_broker_sync_status()
        self._engineer_unlock_catalog = load_unlock_catalog(
            self._data_dir, self.package_root
        )
        self._station_rejections = {}
        self._navroute_rejections = {}
        self._eddn_profile_paths_signature = None
        self._eddn_profile_paths_cache = []
        self._eddn_context = self._rebuild_eddn_context()
        self._save_eddn()
        LOGGER.info(
            "EDDN context switched to isolated profile %s at %s",
            context.key, context.journal_root,
        )
        return True











    @Slot()
    def updateSpanshCatalogs(self):
        """Refresh every catalog supported by the shared Spansh station API."""
        if self._trader_sync_busy or self._tech_broker_sync_busy:
            return
        self.updateTraderCatalog()
        self.updateTechBrokerCatalog()

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
    def updateTraderCatalog(self):
        if self._trader_sync_busy:
            return
        position = self._state.get("currentPosition") or []
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            self._trader_sync_status = (
                "Cannot update: no current three-dimensional Journal position."
            )
            self.connectionChanged.emit()
            return
        existing = self._read_local_json(self.trader_catalog_file, {})
        try:
            fetched_at = datetime.fromisoformat(
                str(existing.get("fetched_at") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (AttributeError, TypeError, ValueError):
            fetched_at = None
        if fetched_at and (
            datetime.now(timezone.utc) - fetched_at
        ).total_seconds() < SPANSH_MINIMUM_AGE_HOURS * 3600:
            self._trader_sync_status = "Spansh catalog is already current."
            self.connectionChanged.emit()
            return
        reference = tuple(float(value) for value in position)
        request_context = {
            "request_id": uuid.uuid4().hex,
            "profile_key": self.profile_context.key,
            "path_generation": self._profile_generation,
            "catalog_path": str(self.trader_catalog_file.resolve()),
        }
        self._trader_sync_busy = True
        self._trader_sync_status = (
            "Querying Spansh for nearby Raw, Manufactured and Encoded traders…"
        )
        self.connectionChanged.emit()

        def worker():
            try:
                result = fetch_trader_catalog_updates(
                    {"Raw", "Manufactured", "Encoded"},
                    reference,
                    post=requests.post,
                    timeout=SPANSH_TIMEOUT_SECONDS,
                    size=100,
                )
                if not result.get("stations"):
                    errors = "; ".join(
                        f"{key}: {value}"
                        for key, value in result.get("errors", {}).items()
                    )
                    raise LookupError(errors or "No valid trader rows returned")
                self.traderSyncFinished.emit(True, json.dumps({
                    "request": request_context, "result": result,
                }))
            except Exception as exc:
                self.traderSyncFinished.emit(False, json.dumps({
                    "request": request_context,
                    "error": f"{type(exc).__name__}: {exc}",
                }))

        self._start_network_worker(worker, "trader-catalog-sync")

    @Slot(bool, str)
    def _finish_trader_catalog_sync(self, success, payload):
        self._trader_sync_busy = False
        try:
            envelope = json.loads(payload)
            request_context = envelope["request"]
            target_path = Path(request_context["catalog_path"])
            current_request = (
                request_context.get("profile_key") == self.profile_context.key
                and request_context.get("path_generation") == self._profile_generation
                and target_path == self.trader_catalog_file.resolve()
            )
        except (KeyError, TypeError, ValueError):
            LOGGER.error("Trader Spansh completion has no valid request context")
            return
        if not success:
            if not current_request:
                LOGGER.warning(
                    "Discarded stale Trader status for Spansh request %s",
                    request_context.get("request_id", ""),
                )
                return
            self._trader_sync_status = (
                "Spansh update failed · offline catalog remains active · "
                f"{envelope.get('error', 'unknown error')}"
            )
            self.connectionChanged.emit()
            return
        try:
            result = envelope["result"]
            existing = self._read_local_json(target_path, {})
            rows = merge_trader_catalog(
                existing.get("stations", [])
                if isinstance(existing, dict) else [],
                result.get("stations", []),
            )
            document = {
                "source": "Local overlay merged from Spansh live station search",
                "fetched_at": result.get("fetched_at"),
                "reference_coords": result.get("reference_coords"),
                "stations": rows,
            }
            if not self._persist_json(target_path, document, "Trader catalog"):
                raise OSError("Trader catalog could not be saved to disk")
            type_cache = TraderTypeCache().load()
            cache_changed = False
            for row in result.get("stations", []):
                evidence = spansh_trader_type_evidence(
                    row, result.get("fetched_at")
                )
                if evidence and type_cache.update(evidence):
                    cache_changed = True
            if cache_changed:
                type_cache.save()
        except (KeyError, OSError, TypeError, ValueError) as exc:
            if not current_request:
                LOGGER.error(
                    "Stale Trader Spansh result could not be saved to %s: %s",
                    target_path, exc,
                )
                return
            self._trader_sync_status = (
                f"Live results received, but local merge failed · {exc}"
            )
            self.connectionChanged.emit()
            return
        if not current_request:
            LOGGER.info(
                "Saved stale Trader Spansh request %s to original profile %s",
                request_context.get("request_id", ""), target_path,
            )
            return
        errors = result.get("errors", {})
        self._trader_sync_status = (
            f"Catalog updated · 1,622 bundled + {len(rows)} saved live rows"
            + (
                " · partial: " + ", ".join(sorted(errors))
                if errors else " · all three trader types received"
            )
        )
        self.refresh()
        self.connectionChanged.emit()


    @Slot()
    def exportDataHistory(self):
        """Export archived and currently active records as portable JSON."""
        if self._history_export_busy:
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self.config_dir / "exports" / f"EDOPS_history_{timestamp}.json"
        candidates = self._mining_catalog.get("candidates", [])
        archive = self._history_archive
        generation = self._profile_generation
        profile_key = self.profile_context.key
        active = {
                "eddn_active": self._eddn_queue,
                "hge_active": self._hge_sightings,
                "inara_receipts_active": self._inara_receipts,
                "mining_catalog_active": (
                    candidates if isinstance(candidates, list) else []
                ),
            }
        self._history_export_busy = True
        self._eddn_status = "Exporting full history in background…"
        self.connectionChanged.emit()

        def worker():
            try:
                path = archive.export_json(destination, active=active)
                result = (generation, profile_key, True, str(path))
            except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
                result = (generation, profile_key, False, str(exc))
            self.historyExportFinished.emit(result)

        if not self._start_network_worker(worker, "history-export"):
            self._history_export_busy = False
            self._eddn_status = "History export unavailable during shutdown"
            self.connectionChanged.emit()

    @Slot(object)
    def _finish_history_export(self, payload):
        generation, profile_key, success, detail = payload
        if (
            generation != self._profile_generation
            or profile_key != self.profile_context.key
        ):
            LOGGER.info("Completed history export for an inactive profile")
            return
        self._history_export_busy = False
        if success:
            QGuiApplication.clipboard().setText(detail)
            self._eddn_status = f"Full history exported · {detail} · path copied"
        else:
            self._eddn_status = f"History export failed: {detail}"
            LOGGER.error(self._eddn_status)
        self.connectionChanged.emit()

    @Slot()
    def refreshHgeFinderLifetime(self):
        """Re-evaluate HGE expiry without rebuilding Journal-derived state."""
        self.hgeChanged.emit()
        if self._selected_material:
            key = str(self._selected_material.get("key") or "")
            self.selectMaterial(key)

    @Slot()
    def refreshMiningFinder(self):
        if self._mining_sync_busy:
            return
        address = self._state.get("currentSystemAddress")
        try:
            address = int(address)
        except (TypeError, ValueError):
            self._mining_sync_status = "Current system address unavailable"
            self.miningChanged.emit()
            return
        request = {
            "id": uuid.uuid4().hex,
            "profileKey": self.profile_context.key,
            "generation": self._profile_generation,
            "path": str(self.mining_catalog_file),
            "origin": list(self._state.get("currentPosition") or []),
        }
        self._active_mining_request = request
        self._mining_sync_busy = True
        self._mining_sync_status = "Refreshing current system from Spansh…"
        self.miningChanged.emit()

        def worker():
            result = dict(request)
            try:
                payload = fetch_spansh_system_dump(address, requests.get)
                result["candidates"] = project_spansh_mining_candidates(
                    payload, request["origin"]
                )
                result["success"] = True
            except Exception as exc:
                result.update({"success": False, "error": str(exc)})
            self.miningSyncFinished.emit(result)

        if not self._start_network_worker(worker, "mining-catalog-sync"):
            self._active_mining_request = None
            self._mining_sync_busy = False
            self._mining_sync_status = "Refresh unavailable during shutdown"
            self.miningChanged.emit()

    @Slot()
    def resetMiningCatalog(self):
        """Explicitly replace only the active profile's learned mining cache."""
        existing = self._mining_catalog.get("candidates", [])
        if isinstance(existing, list) and not self._archive_history(
            "mining_catalog", existing
        ):
            self._mining_sync_status = (
                "Mining catalog reset stopped because history could not be saved"
            )
            self.miningChanged.emit()
            return
        self._active_mining_request = None
        self._mining_sync_busy = False
        self._pending_mining_candidates = []
        if not hasattr(self, "_mining_catalog_load_token"):
            self._mining_catalog_load_token = 0
        self._mining_catalog_load_token += 1
        self._mining_catalog = {
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "resetAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidates": [],
        }
        self._mining_rows_build_token = getattr(
            self, "_mining_rows_build_token", 0
        ) + 1
        self._mining_rows_build_in_flight = False
        self._mining_rows_build_dirty = False
        self._mining_rows_cache_key = None
        self._mining_rows_cache = []
        self._mining_find_cache_key = None
        self._mining_find_cache = []
        try:
            if not hasattr(self, "_mining_file_lock"):
                self._mining_file_lock = threading.Lock()
                self._mining_save_sequence = 0
                self._mining_save_sequences = {}
            if not hasattr(self, "_mining_save_sequences"):
                self._mining_save_sequences = {}
            if not hasattr(self, "_mining_save_sequence"):
                self._mining_save_sequence = 0
            # Supersede any queued snapshot, then serialize the explicit reset
            # behind an in-flight writer so an older catalog cannot reappear.
            self._mining_save_sequence += 1
            self._mining_save_sequences[
                str(self.mining_catalog_file)
            ] = self._mining_save_sequence
            with self._mining_file_lock:
                self.mining_catalog_file.unlink(missing_ok=True)
                load_json_file(self.mining_catalog_file, {}, encoding="utf-8")
                saved = atomic_write(
                    self.mining_catalog_file,
                    json.dumps(self._mining_catalog, indent=2),
                )
        except OSError as exc:
            saved = False
            LOGGER.warning("Mining catalog reset failed: %s", type(exc).__name__)
        self._mining_sync_status = (
            "Mining catalog reset · awaiting new Journal and EDDN observations"
            if saved else "Mining catalog reset failed"
        )
        self.miningChanged.emit()
        self.stateChanged.emit()

    @Slot(object)
    def _finish_mining_sync(self, result):
        request = self._active_mining_request
        if not request or result.get("id") != request.get("id"):
            return
        self._active_mining_request = None
        self._mining_sync_busy = False
        context_matches = (
            result.get("profileKey") == self.profile_context.key
            and result.get("generation") == self._profile_generation
            and result.get("path") == str(self.mining_catalog_file)
        )
        if not context_matches:
            self._mining_sync_status = "Discarded stale profile response"
            self.miningChanged.emit()
            return
        if not result.get("success"):
            self._mining_sync_status = "Refresh failed: " + str(
                result.get("error") or "unknown error"
            )
            self.miningChanged.emit()
            return
        old = self._mining_catalog.get("candidates", [])
        incoming = list(result.get("candidates") or [])
        learned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for row in incoming:
            row["learnedAt"] = learned_at
        merged, _displaced = merge_mining_candidate_batch(
            old if isinstance(old, list) else [], incoming
        )
        preserved = self._archive_history("mining_observations", incoming)
        preserved = self._archive_history(
            "mining_catalog",
            self._displaced_history_rows(
                old, merged, MINING_TRANSIENT_FIELDS
            ),
        ) and preserved
        candidates = (
            merged if preserved
            else [*(old if isinstance(old, list) else []), *incoming]
        )
        self._compact_mining_catalog_rows(candidates)
        self._mining_catalog = {
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "resetAt": str(self._mining_catalog.get("resetAt") or ""),
            "candidates": candidates,
        }
        self._mining_rows_cache_key = None
        self._mining_find_cache_key = None
        self._mining_find_cache = []
        self._save_mining_catalog()
        self._mining_sync_status = f"Current system refreshed · {len(result.get('candidates') or [])} rings"
        self.miningChanged.emit()
        self.stateChanged.emit()

    @Slot()
    def refreshStateFinds(self):
        """Refresh every local/live State Finds source without inventing history."""
        self.flushHgeObservationBatch(True)
        batch = dict(self._last_hge_batch_stats)
        previous_count = len(self._hge_sightings)
        self._save_hge_cache()
        removed = max(0, previous_count - len(self._hge_sightings))
        self._ensure_eddn_listener()
        self._scan_eddn_journal()
        self.refresh()
        listener = self._eddn_listener_status
        self._last_state_find_refresh_stats = {
            "refreshedAt": time.strftime("%H:%M"),
            "bgsApplied": int(batch.get("bgsApplied", 0) or 0),
            "signalsMerged": int(batch.get("signalsMerged", 0) or 0),
            "expiredRemoved": int(
                batch.get("expiredRemoved", 0) or 0
            ) + int(removed or 0),
        }
        self._state_find_refresh_status = (
            f"REFRESHED {self._last_state_find_refresh_stats['refreshedAt']} · {listener}"
            f" · {batch.get('bgsApplied', 0)} BGS SNAPSHOTS APPLIED"
            f" · {batch.get('signalsMerged', 0)} SIGNALS MERGED"
            f" · {batch.get('expiredRemoved', 0) + removed} EXPIRED REMOVED"
        )
        self.hgeChanged.emit()


    @Slot()
    def flushHgeObservationBatch(self, force=False):
        force = bool(force or getattr(self, "_shutdown_complete", False))
        pending_snapshots = self._pending_bgs_snapshots
        snapshots_due = bool(pending_snapshots) and (
            force or time.monotonic() - getattr(
                self, "_last_bgs_batch_monotonic", 0.0
            ) >= BGS_OBSERVATION_BATCH_SECONDS
        )
        snapshots = pending_snapshots if snapshots_due else []
        if snapshots_due:
            self._pending_bgs_snapshots = []
            self._last_bgs_batch_monotonic = time.monotonic()
        hge_rows = self._pending_hge_observations
        self._pending_hge_observations = []
        pending_mining = getattr(self, "_pending_mining_candidates", [])
        mining_due = bool(pending_mining) and (
            force or time.monotonic() - getattr(
                self, "_last_mining_batch_monotonic", 0.0
            ) >= MINING_OBSERVATION_BATCH_SECONDS
        )
        mining_rows = pending_mining if mining_due else []
        if mining_due:
            self._pending_mining_candidates = []
            self._last_mining_batch_monotonic = time.monotonic()
        if (
            not snapshots and not hge_rows and not mining_rows
            and time.time() < getattr(self, "_next_hge_expiry_epoch", 0)
        ):
            return
        previous_sightings = list(self._hge_sightings)
        updated, applied = apply_system_bgs_snapshot_batch(
            self._hge_sightings, snapshots, None
        )
        updated, hge_changed = merge_hge_observation_batch(
            updated, hge_rows, None
        )
        displaced = self._hge_displaced_history_rows(
            previous_sightings, updated, snapshots, hge_rows
        )
        if displaced and not self._archive_history(
            "hge_observations", displaced
        ):
            updated.extend(displaced)
        active, historical = partition_hge_observations(updated)
        overflow_count = max(0, len(active) - HGE_OBSERVATION_LIMIT)
        overflow = active[:overflow_count]
        retained = active[overflow_count:]
        retired = [*historical, *overflow]
        if retired and self._archive_history("hge_observations", retired):
            updated = retained
            removed = len(retired)
        elif retired:
            # Keep every row active when the archive cannot prove persistence.
            removed = 0
        else:
            updated = retained
            removed = 0
        self._last_hge_batch_stats = {
            "bgsApplied": int(applied or 0),
            "signalsMerged": len(hge_rows) if hge_changed else 0,
            "expiredRemoved": int(removed or 0),
        }
        changed = bool(applied or hge_changed or removed)
        if changed:
            self._hge_sightings = updated
            self._save_hge_cache(already_partitioned=True)
            self.hgeChanged.emit()
            if self._selected_material:
                self.selectMaterial(str(self._selected_material.get("key") or ""))
        self._next_hge_expiry_epoch = self._hge_next_expiry_epoch(
            self._hge_sightings
        )
        if mining_rows:
            existing = self._mining_catalog.get("candidates", [])
            merged, displaced = merge_mining_candidate_batch(
                existing if isinstance(existing, list) else [], mining_rows
            )
            preserved = self._archive_history(
                "mining_observations", mining_rows
            )
            preserved = self._archive_history(
                "mining_catalog", displaced,
            ) and preserved
            candidates = (
                merged if preserved else [
                    *(existing if isinstance(existing, list) else []),
                    *mining_rows,
                ]
            )
            self._compact_mining_catalog_rows(candidates)
            self._mining_catalog = {
                "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "resetAt": str(self._mining_catalog.get("resetAt") or ""),
                "candidates": candidates,
            }
            self._mining_rows_cache_key = None
            self._mining_find_cache_key = None
            self._mining_find_cache = []
            self._save_mining_catalog()
            self._mining_sync_status = (
                f"EDDN live · {len(mining_rows)} observations merged · "
                f"{len(self._mining_catalog['candidates'])} rings cached"
            )
            self.miningChanged.emit()
            self.stateChanged.emit()
        self._eddn_listener_status = (
            f"Connected · {len(self._hge_sightings)} local observations · max 24 h"
        )
        if snapshots or hge_rows or mining_rows or removed:
            self.connectionChanged.emit()

    @staticmethod
    def _hge_next_expiry_epoch(observations):
        """Find the exact next retention deadline for the idle batch timer."""
        deadlines = []
        for row in observations or []:
            if not isinstance(row, dict) or row.get("self_test"):
                continue
            try:
                observed = datetime.fromisoformat(str(
                    row.get("signal_timestamp") or row.get("received_at") or ""
                ).replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            lifetime = 86400
            reported = int(row.get("time_remaining", 0) or 0)
            if (
                str(row.get("evidence_kind") or "") in {
                    "EDDN_SIGNAL", "LOCAL_JOURNAL", "ENTERED",
                }
                and reported > 0
            ):
                lifetime = min(lifetime, reported)
            deadlines.append(observed.timestamp() + lifetime)
        now = time.time()
        if any(value <= now for value in deadlines):
            return 0
        return min(deadlines, default=float("inf"))


    @Slot()
    def shutdown(self):
        """Persist queues and stop background work before Qt removes signals."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        # Stop every periodic timer before the final saves so a late tick
        # cannot mutate or re-persist queue state after this point.
        for timer_name in (
            "timer", "refreshDebounceTimer", "craftConfirmationTimer",
            "hgeBatchTimer", "_frontier_watchdog",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        self._eddn_stop.set()
        thread = self._eddn_thread
        if (
            thread and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.5)
        deadline = time.monotonic() + 3.0
        with self._network_threads_lock:
            network_threads = list(self._network_threads)
        for worker in network_threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=remaining)
        try:
            self.flushHgeObservationBatch(True)
            self._save_eddn()
            self._save_eddn_cursor()
            self._save_hge_cache()
            self._save_inara_journal_cache()
            self._save_inara_receipts()
            self._save_ui_config()
        except OSError as exc:
            LOGGER.warning("Final shutdown save failed: %s", exc)

    @Slot()
    def requestExit(self):
        self.exitRequested.emit()

    @Slot()
    def requestRestart(self):
        self.restartRequested.emit()

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

    @Slot(bool)
    def setJournalAuto(self, enabled):
        self._journal_auto = bool(enabled)
        saved = self._save_ui_config()
        if not saved:
            self.uiChanged.emit()
            self.journalHealthChanged.emit()
            return
        self._activity = (
            "Automatic Journal updates enabled."
            if self._journal_auto else "Automatic Journal updates paused."
        )
        self.uiChanged.emit()
        self.activityChanged.emit()
        self.journalHealthChanged.emit()

    @Slot(bool)
    def setBackgroundMode(self, enabled):
        enabled = bool(enabled)
        if enabled and not self._system_tray_available:
            self._activity = "System tray is unavailable; background mode remains disabled."
            self.activityChanged.emit()
            return
        self._background_mode = enabled
        if not enabled and self._autostart_enabled:
            self.setAutostartEnabled(False)
        if not self._save_ui_config():
            self.uiChanged.emit()
            return
        self._activity = (
            "Tray background mode enabled."
            if self._background_mode else "Tray background mode disabled."
        )
        self.uiChanged.emit()
        self.activityChanged.emit()

    @Slot(bool)
    def setSystemTrayAvailable(self, available):
        self._system_tray_available = bool(available)
        self.uiChanged.emit()

    @Slot(str)
    def setBackgroundRuntimeStatus(self, status):
        status = str(status or "").strip().upper()
        if status not in {
            "WINDOW OPEN", "RUNNING IN BACKGROUND", "TRAY UNAVAILABLE",
        }:
            return
        if status == self._background_runtime_status:
            return
        self._background_runtime_status = status
        self.uiChanged.emit()

    def _autostart_command(self):
        if getattr(sys, "frozen", False):
            parts = [sys.executable, "--background"]
        else:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            executable = pythonw if pythonw.exists() else python
            parts = [str(executable), str(self.package_root / "phase14_main.py"), "--background"]
        return subprocess.list2cmdline(parts)

    @Slot(bool)
    def setAutostartEnabled(self, enabled):
        enabled = bool(enabled)
        try:
            import winreg
            path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
            ) as key:
                if enabled:
                    winreg.SetValueEx(
                        key, "EDOPS", 0, winreg.REG_SZ, self._autostart_command()
                    )
                else:
                    try:
                        winreg.DeleteValue(key, "EDOPS")
                    except FileNotFoundError:
                        pass
        except (ImportError, OSError) as exc:
            self._activity = f"Windows autostart could not be changed: {type(exc).__name__}."
            self.activityChanged.emit()
            return
        self._autostart_enabled = enabled
        if not self._save_ui_config():
            self.uiChanged.emit()
            return
        self._activity = "Windows autostart enabled." if enabled else "Windows autostart disabled."
        self.uiChanged.emit()
        self.activityChanged.emit()

    @Slot()
    def reloadJournalNow(self):
        self.clearCraftConfirmation()
        self.refresh()
        self._scan_eddn_journal()

    @Slot(str)
    def setJournalPath(self, path):
        if set_journal_dir(path):
            self._last_journal_stamp = None
            self._last_commander_status_stamp = None
            self._selected_ship = ""
            self.refresh()
            self._activity = "Journal directory updated."
        else:
            value = Path(str(path or "").strip()).expanduser()
            self._activity = (
                "Journal directory could not be saved; previous path remains active."
                if value.is_dir()
                else "Journal directory does not exist."
            )
        self.activityChanged.emit()

    @Slot()
    def pollJournal(self):
        if self._shutdown_complete:
            return
        if not self._journal_auto:
            self._maybe_start_inara_auto()
            self._process_eddn_queue()
            return
        # The startup/refresh worker owns the large Journal parse. Avoid
        # contending for its cache lock from Qt while the first projection is
        # still being built.
        if not self._journal_state_ready:
            self._maybe_start_inara_auto()
            self._process_eddn_queue()
            return
        self._poll_commander_status_credits()
        self._poll_exobiology_distance_check()
        stamp = journal_change_signature()
        if self._last_journal_stamp is None:
            self._last_journal_stamp = stamp
            self._queue_inara_journal_scan()
        elif stamp != self._last_journal_stamp:
            self._last_journal_stamp = stamp
            live_state, location_changed = state_with_live_location(
                self._state, latest_profile_location()
            )
            if location_changed:
                self._state = live_state
                self._state_revision += 1
                self._derived_cache.clear()
                self.stateChanged.emit()
                self.hgeChanged.emit()
            self.refresh()
        self._maybe_start_inara_auto()
        self._scan_eddn_journal()
        self._process_eddn_queue()






    @Slot(float)
    def setUiScale(self, value):
        self._ui_scale = max(1.00, min(1.50, round(float(value), 2)))
        self._save_ui_config()
        self.uiChanged.emit()

    @Slot(str)
    def setTheme(self, value):
        value = str(value or "").lower()
        if value not in THEME_IDS:
            return
        self._theme = value
        self._save_ui_config()
        self.uiChanged.emit()

    @Slot(str)
    def setInterfaceLanguage(self, value):
        value = str(value or "").casefold()
        if value not in SUPPORTED_LANGUAGES or value == self._interface_language:
            return
        self._interface_language = value
        self._save_ui_config()
        self.uiChanged.emit()

    @Slot(str, str, result=str)
    def translate(self, key, fallback=""):
        return self._translations.translate(
            self._interface_language, key, fallback
        )

    @Slot(bool)
    def setReducedMotion(self, enabled):
        self._reduced_motion = bool(enabled)
        self._save_ui_config()
        self.uiChanged.emit()


    @Slot(bool)
    def setEnhancedVisuals(self, enabled):
        self._enhanced_visuals = bool(enabled)
        self._save_ui_config()
        self.uiChanged.emit()

    @Slot(str)
    def setTraderPreference(self, value):
        value = str(value or "").casefold()
        if value not in {"confirmed", "nearest"}:
            return
        if value == self._trader_preference:
            return
        self._trader_preference = value
        self._save_ui_config()
        self.uiChanged.emit()
        self.refresh()

    @Slot(int)
    def setLastPage(self, page):
        page = max(0, min(12, int(page)))
        if page != self._last_page:
            if self._last_page == 3 and page != 3:
                self.clearCraftConfirmation()
            if self._last_page == 12 and page != 12:
                # The lazy Mining page no longer needs its 70k+ enriched row
                # projection. Keep the persisted source catalog, but release
                # this derived view until Mining Finder is opened again.
                self._mining_rows_build_token = getattr(
                    self, "_mining_rows_build_token", 0
                ) + 1
                self._mining_rows_build_in_flight = False
                self._mining_rows_build_dirty = False
                self._mining_rows_cache_key = None
                self._mining_rows_cache = []
                self._mining_find_cache_key = None
                self._mining_find_cache = []
            self._last_page = page
            self._save_ui_config()



    @Slot("QVariantList")
    def setNavigationOrder(self, order):
        order = list(dict.fromkeys(
            str(item) for item in list(order or [])
            if str(item) in NAVIGATION_IDS
        ))
        order.extend(item for item in NAVIGATION_IDS if item not in order)
        if order != self._navigation_order:
            previous = self._navigation_order
            self._navigation_order = order
            if not self._save_ui_config():
                self._navigation_order = previous
            self.uiChanged.emit()

    @Slot()
    def completeOnboarding(self):
        self._onboarding_complete = True
        self._save_ui_config()
        self.uiChanged.emit()

    @Slot()
    def reopenOnboarding(self):
        self._onboarding_complete = False
        self._save_ui_config()
        self.uiChanged.emit()

    @Slot(bool)
    def setDebugMode(self, enabled):
        self._debug_mode = bool(enabled)
        self._save_ui_config()
        self._write_log(
            "Advanced diagnostics enabled"
            if self._debug_mode else "Advanced diagnostics disabled"
        )
        self.uiChanged.emit()
        self.diagnosticsChanged.emit()

    @Slot()
    def refreshDiagnostics(self):
        self._write_log("Manual diagnostics refresh")
        self.diagnosticsChanged.emit()

    @Slot()
    def copyDiagnostics(self):
        health = self._journal_health()
        text = "\n".join(
            f"{key}: {value}" for key, value in health.items()
        )
        text += "\n\nSERVICES\n" + "\n".join(
            f"{row['name']}: {row['status']} · {row['detail']}"
            for row in self._service_status()
        )
        QGuiApplication.clipboard().setText(text)
        self._activity = "Diagnostics copied to clipboard"
        self.activityChanged.emit()

    @Slot()
    def clearDiagnosticLog(self):
        path = self.config_dir / "phase14.log"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        self.diagnosticsChanged.emit()

    @Slot(str, result="QVariantList")
    def globalSearch(self, query):
        query = str(query or "").strip().casefold()
        if len(query) < 2:
            return []
        results = []
        for row in self._state.get("materials", []):
            if query in str(row.get("name") or "").casefold():
                results.append({
                    "kind": "MATERIAL", "title": row["name"],
                    "detail": (
                        f"{row['category']} · have {row['have']} · "
                        f"need {row['need']} · missing {row['missing']}"
                    ),
                    "page": 2, "key": row["key"],
                })
        for row in self._blueprint_catalog:
            hay = f"{row['module']} {row['name']} {row['engineers']}".casefold()
            if query in hay:
                results.append({
                    "kind": "BLUEPRINT",
                    "title": f"{row['module']} · {row['name']}",
                    "detail": f"Up to G{row['maxGrade']} · {row['engineers']}",
                    "page": 3, "key": row["id"],
                })
        for row in self._engineer_index():
            hay = (
                f"{row['name']} {row['system']} "
                f"{' '.join(row['modules'])} {' '.join(row['blueprints'])}"
            ).casefold()
            if query in hay:
                results.append({
                    "kind": "ENGINEER", "title": row["name"],
                    "detail": (
                        f"{row['system']} · {row['status']} · "
                        f"up to G{row['maxGrade']}"
                    ),
                    "page": 4, "key": row["name"],
                })
        return results[:30]

    @Slot(str)
    def setRendererMode(self, mode):
        mode = str(mode or "").lower()
        if mode not in {"auto", "gpu", "software"}:
            return
        self._renderer_mode = mode
        self._save_ui_config()
        self._restart_required = True
        self.rendererChanged.emit()
