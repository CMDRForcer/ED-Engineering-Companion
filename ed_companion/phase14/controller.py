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


class CockpitController(QObject):
    stateChanged = Signal()
    materialsChanged = Signal()
    fleetChanged = Signal()
    wishlistChanged = Signal()
    exobiologyChanged = Signal()
    exobiologyDistanceCheckChanged = Signal()
    operationsChanged = Signal()
    hgeChanged = Signal()
    miningChanged = Signal()
    miningSyncFinished = Signal(object)
    miningCatalogLoaded = Signal(object)
    miningRowsReady = Signal(object)
    journalHealthChanged = Signal()
    diagnosticsChanged = Signal()
    logbookChanged = Signal()
    rendererChanged = Signal()
    activityChanged = Signal()
    materialSelectionChanged = Signal()
    engineeringChanged = Signal()
    uiChanged = Signal()
    connectionChanged = Signal()
    commanderCardsChanged = Signal()
    inaraFinished = Signal(object)
    inaraJournalScanReady = Signal(object)
    frontierFinished = Signal(object)
    eddnFinished = Signal(str, bool, str)
    eddnRelay = Signal(object)
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
        self._logbook_entries = []
        self._logbook_filter = "ALL"
        self._logbook_query = ""
        self._selected_logbook_entry = {}
        self._logbook_revision = 0
        self._logbook_notes = load_logbook_notes(self.config_dir)
        self._inara_config = self._load_inara_config()
        journal_identity = self.profile_context.identity
        detected_identity, journal_commander = active_profile_identity()
        if detected_identity != journal_identity:
            journal_commander = ""
        if journal_commander:
            self._inara_config["commander_name"] = journal_commander
        if journal_identity.upper().startswith("F"):
            self._inara_config["frontier_id"] = journal_identity
        self._save_inara_config()
        self._inara_status = self._inara_initial_status(self._inara_config)
        self._inara_busy = False
        self._active_inara_request = None
        self._inara_pending_since = 0.0
        self._inara_request_times = []
        self._inara_last_request_at = 0.0
        self._inara_retry_not_before = 0.0
        self._inara_failure_count = 0
        self._inara_pending_events = []
        self._inara_pending_fingerprints = []
        self._inara_inflight_fingerprints = []
        self._inara_recovery_candidate_file = ""
        self._inara_material_fingerprint = ""
        self._inara_scan_token = 0
        self._inara_scan_in_flight = False
        self._inara_scan_dirty = False
        self._inara_cache = self._read_local_json(
            self.inara_journal_cache_file, {}
        )
        if not isinstance(self._inara_cache, dict):
            self._inara_cache = {}
        if self._inara_cache.get("journal_root") != self.profile_context.journal_root:
            self._inara_cache = {
                key: self._inara_cache[key]
                for key in ("last_request_at", "rate_limit_until")
                if key in self._inara_cache
            }
        last_request_wall = float(
            self._inara_cache.get("last_request_at", 0) or 0
        )
        elapsed_since_request = max(0.0, time.time() - last_request_wall)
        if last_request_wall and elapsed_since_request < INARA_MIN_REQUEST_INTERVAL_SECONDS:
            self._inara_last_request_at = (
                time.monotonic() - elapsed_since_request
            )
        now_wall = time.time()
        self._inara_request_wall_times = [
            float(value) for value in self._inara_config.get("request_times", [])
            if isinstance(value, (int, float)) and now_wall - float(value) < 60
        ]
        self._inara_receipts = self._load_inara_receipts()
        if len(self._inara_receipts) > INARA_ACTIVE_RECEIPT_LIMIT:
            self._save_inara_receipts()
        self.inaraFinished.connect(self._finish_inara)
        self.inaraJournalScanReady.connect(self._finish_inara_journal_scan)
        self._frontier_credential_store = FrontierCredentialStore(
            self.frontier_credentials_file
        )
        self._frontier_tokens = None
        self._frontier_authorization = None
        self._frontier_busy = False
        self._frontier_request_token = 0
        self._frontier_last_sync = ""
        self._frontier_profile = {}
        self._frontier_config = self._load_frontier_config()
        self._frontier_watchdog = QTimer(self)
        self._frontier_watchdog.setSingleShot(True)
        self._frontier_watchdog.setInterval(FRONTIER_REQUEST_WATCHDOG_MS)
        self._frontier_watchdog.timeout.connect(self._frontier_request_timed_out)
        try:
            self._frontier_tokens = self._frontier_credential_store.load()
            self._frontier_status = (
                "CONNECTED LOCALLY · Refresh to verify the Frontier session."
                if self._frontier_tokens else
                "NOT CONNECTED · Frontier approval is required before first login."
            )
        except FrontierCredentialError:
            self._frontier_status = (
                "CREDENTIAL STORAGE ERROR · Reconnect after removing the local token file."
            )
        self._frontier_client = (
            FrontierCapiClient(
                self._frontier_tokens.access_token,
                token_type=self._frontier_tokens.token_type,
            )
            if self._frontier_tokens else None
        )
        self.frontierFinished.connect(self._finish_frontier)
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
        self._exobiology_species_catalog = read_json(
            self._reference_data_dir / "exobiology_species.json", []
        )
        self._exobiology_colony_ranges = read_json(
            self._reference_data_dir / "exobiology_colony_ranges.json", {}
        )
        # Best-effort, in-memory only: where the Commander was standing at
        # each in-progress find's most recent scan step, keyed by
        # (systemAddress, body, genus, species). Journal replay has no
        # position data to reconstruct this from, so a restart mid-scan
        # simply waits for the next step to re-establish a baseline.
        self._exobiology_step_positions = {}
        self._exobiology_distance_check_value = {}
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

    def _load_inara_config(self):
        defaults = {
            "api_key": "", "commander_name": "", "frontier_id": "",
            "consent": False, "auto_sync": False, "request_times": [],
        }
        loaded = load_json_file(self.inara_config_file, {}, encoding="utf-8")
        if isinstance(loaded, dict):
            defaults.update({
                key: loaded.get(key, defaults[key]) for key in defaults
            })
        return defaults

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

    def _load_eddn_config(self):
        defaults = {
            "consent": False, "upload_enabled": False,
            "listener_enabled": False, "retry_failed": True,
            "uploader_id": uuid.uuid4().hex,
            "hge_classifier_version": 0,
            "last_success": {}, "station_receipts": {},
            "last_not_shareable": "", "last_not_shareable_at": "",
        }
        loaded = self._read_local_json(self.eddn_config_file, {})
        if isinstance(loaded, dict):
            defaults.update({
                key: loaded.get(key, defaults[key]) for key in defaults
            })
        return defaults

    def _load_frontier_config(self):
        defaults = {"consent": False}
        loaded = self._read_local_json(self.frontier_config_file, {})
        if isinstance(loaded, dict):
            defaults["consent"] = bool(loaded.get("consent", False))
        return defaults

    def _save_frontier_config(self):
        return self._persist_json(
            self.frontier_config_file, self._frontier_config,
            "Frontier configuration",
        )

    def _load_eddn_cursor_state(self) -> None:
        cursor = self._read_local_json(self.eddn_cursor_file, {})
        if not isinstance(cursor, dict):
            cursor = {}
        self._station_fingerprints = cursor.pop("__station_files__", {})
        self._navroute_fingerprint = str(
            cursor.pop("__navroute_file__", "") or ""
        )
        self._eddn_baseline_established = bool(
            cursor.pop("__baseline_established__", False)
        )
        stored_root = str(cursor.pop("__journal_root__", "") or "")
        self._journal_offsets = cursor if stored_root == self._eddn_journal_root else {}
        if stored_root == self._eddn_journal_root:
            # Pre-marker cursors with the same root already represent opt-in.
            self._eddn_baseline_established = True
        else:
            self._station_fingerprints = {}
            self._navroute_fingerprint = ""
            self._eddn_baseline_established = False
        if not isinstance(self._station_fingerprints, dict):
            self._station_fingerprints = {}

    def _save_eddn(self):
        active, historical = partition_upload_queue(
            self._eddn_queue, EDDN_ACTIVE_RECEIPT_LIMIT
        )
        if self._archive_history("eddn_sent", historical, key_field="id"):
            self._eddn_queue = active
        config_saved = atomic_write(
            self.eddn_config_file, json.dumps(self._eddn_config, indent=2)
        )
        queue_saved = atomic_write(
            self.eddn_queue_file, json.dumps(self._eddn_queue, indent=2)
        )
        self._eddn_queue_persist_pending = not queue_saved
        if not config_saved:
            LOGGER.error("EDDN config could not be persisted")
        if not queue_saved:
            self._eddn_status = (
                "EDDN queue could not be persisted; Journal cursor was not advanced."
            )
            LOGGER.error(self._eddn_status)
            signal = getattr(self, "connectionChanged", None)
            if signal is not None:
                signal.emit()
        return queue_saved

    def _load_eddn_queue(self):
        """Repair safe legacy jobs and isolate only irrecoverable records."""
        stored = self._read_local_json(self.eddn_queue_file, [])
        interrupted_count = sum(
            1 for job in stored
            if isinstance(job, dict) and job.get("status") == "sending"
        ) if isinstance(stored, list) else 0
        if interrupted_count:
            LOGGER.warning(
                "EDDN recovered %d interrupted sending job(s); gateway acceptance "
                "is unknown and retry may produce a duplicate",
                interrupted_count,
            )
        jobs = normalize_upload_queue(stored)
        previous = self._read_local_json(self.eddn_quarantine_file, [])
        if not isinstance(previous, list):
            previous = []
        candidates = [(job, None) for job in jobs]
        candidates.extend(
            (row.get("job"), row)
            for row in previous if isinstance(row, dict)
        )
        valid_by_id = {}
        quarantine = []
        repaired_count = 0
        for job, quarantine_row in candidates:
            try:
                if str(job.get("target") or "EDDN") != "EDDN":
                    raise EddnError("Queue target is not EDDN.", terminal=True)
                if str(job.get("status") or "queued") not in {
                    "queued", "retry", "sending", "sent", "failed",
                }:
                    raise EddnError("Queue status is unknown.", terminal=True)
                validate_eddn_prepared(job.get("event"))
            except (EddnError, AttributeError, TypeError) as exc:
                repaired = repair_legacy_eddn_prepared(
                    job.get("event") if isinstance(job, dict) else None
                )
                if repaired is None:
                    quarantine.append(quarantine_row or {
                        "quarantined_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                        "reason": str(exc),
                        "job": job,
                    })
                    continue
                job = dict(job)
                job["event"] = repaired
                digest = hashlib.sha256(json.dumps(
                    repaired, sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest()
                job["id"] = f"EDDN-{digest}"
                job.pop("last_error", None)
                job.pop("terminal_error", None)
                repaired_count += 1
            job_id = str(job.get("id") or "")
            if not job_id:
                digest = hashlib.sha256(json.dumps(
                    job["event"], sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest()
                job_id = f"EDDN-{digest}"
                job["id"] = job_id
            existing = valid_by_id.get(job_id)
            if existing is None or (
                existing.get("status") != "sent" and job.get("status") == "sent"
            ):
                valid_by_id[job_id] = job
        valid = list(valid_by_id.values())
        queue_changed = valid != stored
        quarantine_changed = quarantine != previous
        if queue_changed:
            atomic_write(self.eddn_queue_file, json.dumps(valid, indent=2))
        if quarantine_changed:
            atomic_write(
                self.eddn_quarantine_file,
                json.dumps(quarantine, indent=2),
            )
        if repaired_count:
            LOGGER.warning(
                "EDDN repaired %d legacy queue job(s) with the current public allowlist",
                repaired_count,
            )
        self._eddn_quarantine_error_groups = {}
        for row in quarantine:
            if not isinstance(row, dict):
                continue
            job = row.get("job") if isinstance(row.get("job"), dict) else {}
            prepared = job.get("event") if isinstance(job.get("event"), dict) else {}
            key = f"{prepared.get('schema') or 'unknown'} | {row.get('reason') or 'unknown'}"
            self._eddn_quarantine_error_groups[key] = (
                self._eddn_quarantine_error_groups.get(key, 0) + 1
            )
        return valid

    def _publish_eddn_delivery_change(self):
        self._eddn_revision += 1
        self._derived_cache.clear()
        self.hgeChanged.emit()

    def _eddn_job_retryable(self, job):
        if not isinstance(job, dict) or job.get("status") != "failed":
            return False
        if job.get("terminal_error"):
            return False
        if job.get("profile_key") not in {None, "", self._eddn_profile_key}:
            return False
        try:
            validate_eddn_prepared(job.get("event"))
        except EddnError:
            return False
        return True

    def _eddn_queue_view(self):
        """Expose delivery metadata to QML without Journal/message payloads."""
        rows = []
        for job in reversed(self._eddn_queue[-100:]):
            prepared = job.get("event") if isinstance(job.get("event"), dict) else {}
            message = prepared.get("message") if isinstance(prepared.get("message"), dict) else {}
            rows.append({
                "id": str(job.get("id") or ""),
                "eventName": str(message.get("event") or prepared.get("schema") or "EDDN EVENT"),
                "schema": str(prepared.get("schema") or ""),
                "status": str(job.get("status") or "queued"),
                "attempts": int(job.get("attempts", 0) or 0),
                "created": str(job.get("created") or ""),
                "sentAt": str(job.get("sent_at") or ""),
                "result": str(job.get("last_result") or ""),
                "error": str(job.get("last_error") or ""),
                "retryable": self._eddn_job_retryable(job),
            })
        return rows

    def _eddn_quarantine_view(self):
        """Expose grouped reasons only; quarantined payloads remain private."""
        rows = []
        for key, count in sorted(
            getattr(self, "_eddn_quarantine_error_groups", {}).items(),
            key=lambda item: (-item[1], item[0]),
        ):
            schema, separator, reason = key.partition(" | ")
            rows.append({
                "schema": schema,
                "reason": reason if separator else "Validation failed",
                "count": int(count),
                "status": "IRREPARABLE",
            })
        return rows

    def _eddn_delivery_summary(self):
        counts = {key: 0 for key in ("queued", "retry", "sending", "sent", "failed")}
        history_counts = self._history_counts()
        archived_sent = int(history_counts.get("eddn_sent", 0) or 0)
        schema_counts = {}
        error_groups = {}
        for job in self._eddn_queue:
            status = str(job.get("status") or "")
            if status in counts:
                counts[status] += 1
            prepared = job.get("event") if isinstance(job.get("event"), dict) else {}
            schema = str(prepared.get("schema") or "unknown")
            schema_counts[schema] = schema_counts.get(schema, 0) + 1
            error = str(job.get("last_error") or "").strip()
            if error:
                key = f"{schema} | {error}"
                error_groups[key] = error_groups.get(key, 0) + 1
        counts["sent"] += archived_sent
        queued_sent = next((
            job for job in reversed(self._eddn_queue)
            if job.get("status") == "sent"
        ), {})
        failed = next((
            job for job in reversed(self._eddn_queue)
            if job.get("last_error")
        ), {})
        sent_event = queued_sent.get("event") if isinstance(queued_sent.get("event"), dict) else {}
        sent_message = sent_event.get("message") if isinstance(sent_event.get("message"), dict) else {}
        persisted = self._eddn_config.get("last_success")
        if not isinstance(persisted, dict):
            persisted = {}
        last_success_at = str(
            persisted.get("sentAt") or queued_sent.get("sent_at") or ""
        )
        last_not_shareable = str(
            self._eddn_config.get("last_not_shareable") or ""
        )
        last_not_shareable_at = str(
            self._eddn_config.get("last_not_shareable_at") or ""
        )
        if last_success_at and last_not_shareable_at:
            try:
                success_time = datetime.fromisoformat(
                    last_success_at.replace("Z", "+00:00")
                )
                rejected_time = datetime.fromisoformat(
                    last_not_shareable_at.replace("Z", "+00:00")
                )
                if success_time >= rejected_time:
                    last_not_shareable = ""
            except (TypeError, ValueError):
                pass
        next_retry_epoch = min((
            float(job.get("next_retry_at", 0) or 0)
            for job in self._eddn_queue
            if job.get("status") == "retry"
            and float(job.get("next_retry_at", 0) or 0) > 0
        ), default=0.0)
        next_retry_at = (
            datetime.fromtimestamp(next_retry_epoch, timezone.utc).isoformat(
                timespec="seconds"
            ) if next_retry_epoch else ""
        )
        sent_times = []
        for job in self._eddn_queue:
            if job.get("status") != "sent" or not job.get("sent_at"):
                continue
            try:
                sent_times.append(datetime.fromisoformat(
                    str(job["sent_at"]).replace("Z", "+00:00")
                ).timestamp())
            except (TypeError, ValueError, OverflowError):
                continue
        sent_times = sorted(sent_times)[-30:]
        throughput = 0.0
        if len(sent_times) >= 2 and sent_times[-1] > sent_times[0]:
            throughput = 60.0 * (len(sent_times) - 1) / (
                sent_times[-1] - sent_times[0]
            )
        waiting = counts["queued"] + counts["retry"] + counts["sending"]
        eta_seconds = int(waiting * 60 / throughput) if throughput > 0 else 0
        current = next((
            job for job in self._eddn_queue if job.get("status") == "sending"
        ), {})
        current_event = current.get("event") if isinstance(current.get("event"), dict) else {}
        profile_consistent = (
            self.eddn_queue_file.parent == self.config_dir
            and all(
                job.get("profile_key") in {None, "", self._eddn_profile_key}
                for job in self._eddn_queue if isinstance(job, dict)
            )
        )
        return {
            **counts,
            "waiting": waiting,
            "lastSuccessAt": last_success_at,
            "lastSuccessSchema": str(persisted.get("schema") or sent_event.get("schema") or ""),
            "lastSuccessEvent": str(persisted.get("eventName") or sent_message.get("event") or ""),
            "lastError": str(failed.get("last_error") or ""),
            "lastNotShareable": last_not_shareable,
            "nextRetryAt": next_retry_at,
            "schemaCounts": schema_counts,
            "errorGroups": error_groups,
            "quarantineErrorGroups": dict(
                getattr(self, "_eddn_quarantine_error_groups", {})
            ),
            "quarantined": sum(
                getattr(self, "_eddn_quarantine_error_groups", {}).values()
            ),
            "cooldownActive": bool(next_retry_epoch > time.time()),
            "currentSchema": str(current_event.get("schema") or ""),
            "throughputPerMinute": round(throughput, 1),
            "etaSeconds": eta_seconds,
            "profileKey": self._eddn_profile_key,
            "storageFile": f"profile-{self._eddn_profile_key}/community_upload_queue.json",
            "historyFile": f"profile-{self._eddn_profile_key}/data_history.sqlite3",
            "archived": sum(history_counts.values()),
            "archivedEddn": archived_sent,
            "archivedHge": int(history_counts.get("hge_observations", 0) or 0),
            "archivedInara": int(history_counts.get("inara_receipts", 0) or 0),
            "archivedMining": (
                int(history_counts.get("mining_observations", 0) or 0)
                + int(history_counts.get("mining_catalog", 0) or 0)
            ),
            "profileConsistent": profile_consistent,
        }

    def _eddn_station_snapshot_view(self, directory=None):
        """Describe the three Elite station snapshots without exposing contents."""
        directory = Path(directory) if directory is not None else journal_dir()
        rows = []
        for kind, filename, schemas in (
            ("MARKET", "Market.json", ("commodity/3",)),
            ("OUTFITTING", "Outfitting.json", ("outfitting/2", "outfitting/3")),
            ("SHIPYARD", "Shipyard.json", ("shipyard/2",)),
        ):
            path = directory / filename
            row = {
                "kind": kind, "status": "NOT VISITED", "station": "",
                "system": "", "age": "", "detail": f"{filename} is not available",
            }
            try:
                snapshot = json.loads(path.read_text(
                    encoding="utf-8-sig", errors="strict"
                ))
                if not isinstance(snapshot, dict):
                    raise ValueError("snapshot is not an object")
            except FileNotFoundError:
                rows.append(row)
                continue
            except (OSError, UnicodeError, ValueError, TypeError):
                row.update({"status": "INVALID", "detail": f"{filename} cannot be read"})
                rows.append(row)
                continue
            row["station"] = str(snapshot.get("StationName") or "")
            row["system"] = str(snapshot.get("StarSystem") or "")
            timestamp = str(snapshot.get("timestamp") or "")
            try:
                observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                age_minutes = max(0, int(
                    (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() // 60
                ))
                row["age"] = f"{age_minutes} min ago" if age_minutes < 120 else f"{age_minutes // 60} h ago"
            except (TypeError, ValueError, OverflowError):
                age_minutes = None
                row["age"] = "time unknown"
            reason = station_snapshot_mismatch_reason(
                kind.casefold(), snapshot, self._eddn_context
            )
            if reason:
                row.update({"status": "NOT CURRENT", "detail": reason})
                rows.append(row)
                continue
            prepared = prepare_station_snapshot(
                kind.casefold(), snapshot, self._eddn_context
            )
            if not prepared:
                row.update({"status": "INVALID", "detail": "No schema-valid public records"})
                rows.append(row)
                continue
            job = next((
                job for job in reversed(self._eddn_queue)
                if isinstance(job.get("event"), dict)
                and job["event"].get("schema") in schemas
                and isinstance(job["event"].get("message"), dict)
                and str(job["event"]["message"].get("stationName") or "").casefold()
                    == row["station"].casefold()
                and str(job["event"]["message"].get("timestamp") or "") == timestamp
            ), None)
            if job:
                status = str(job.get("status") or "queued").upper()
                row.update({
                    "status": status,
                    "detail": str(job.get("last_result") or job.get("last_error") or "EDDN delivery pending"),
                })
            else:
                receipts = self._eddn_config.get("station_receipts")
                receipt = next((
                    receipts.get(schema, {}) for schema in schemas
                    if isinstance(receipts, dict)
                    and isinstance(receipts.get(schema), dict)
                    and str(receipts[schema].get("stationName") or "").casefold()
                        == row["station"].casefold()
                    and str(receipts[schema].get("timestamp") or "") == timestamp
                ), {})
                if (
                    isinstance(receipt, dict)
                    and str(receipt.get("stationName") or "").casefold() == row["station"].casefold()
                    and str(receipt.get("timestamp") or "") == timestamp
                ):
                    row.update({"status": "SENT", "detail": str(receipt.get("result") or "Gateway accepted")})
                else:
                    row.update({
                        "status": "FRESH" if age_minutes is not None and age_minutes <= 60 else "STALE",
                        "detail": "No local EDDN receipt; revisit this station page to refresh",
                    })
            rows.append(row)
        return rows

    def _eddn_station_status_summary(self):
        rows = self._eddn_station_snapshot_view()
        if not any(row.get("station") for row in rows):
            return "No Market, Outfitting or Shipyard snapshot is available yet."
        return " · ".join(
            f"{row.get('kind')} {row.get('status')}" for row in rows
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

    def _record_eddn_not_shareable(self, event_name, reason):
        decision = (
            f"{event_name or 'Journal event'} · {reason}"
        )
        if self._eddn_config.get("last_not_shareable") == decision:
            return
        self._eddn_config["last_not_shareable"] = decision
        self._eddn_config["last_not_shareable_at"] = datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds")
        self._save_eddn()

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

    def _save_inara_config(self):
        return self._persist_json(
            self.inara_config_file, self._inara_config, "INARA configuration"
        )

    def _save_inara_journal_cache(self):
        atomic_write(self.inara_journal_cache_file, json.dumps(self._inara_cache, indent=2))

    def _load_inara_receipts(self):
        rows = load_json_file(self.inara_receipts_file, [], encoding="utf-8")
        return rows if isinstance(rows, list) else []

    def _save_inara_receipts(self):
        active = self._inara_receipts[:INARA_ACTIVE_RECEIPT_LIMIT]
        historical = self._inara_receipts[INARA_ACTIVE_RECEIPT_LIMIT:]
        if self._archive_history("inara_receipts", historical):
            self._inara_receipts = active
        atomic_write(
            self.inara_receipts_file,
            json.dumps(self._inara_receipts, indent=2),
        )

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

    def _commander_cards(self):
        """Build display-only CMDR cards from local Journal/cache state."""
        cache_key = self._state_revision
        cached = self._derived_cache.get("commander_cards")
        if cached and cached[0] == cache_key:
            return cached[1]
        cards = build_commander_cards(
            self._state.get("commanderOverview", {}) or {},
            profiled_journal_events()
            if getattr(self, "_journal_state_ready", False) else [],
        )
        self._derived_cache["commander_cards"] = (cache_key, cards)
        return cards

    def _commander_finance_history(self):
        cache_key = (self._state_revision, self._commander_finance_period)
        cached = self._derived_cache.get("commander_finance_history")
        if cached and cached[0] == cache_key:
            return cached[1]
        events = (
            profiled_journal_events()
            if getattr(self, "_journal_state_ready", False) else []
        )
        rows = filter_finance_history(build_finance_history(
            events,
            current_credits=(
                self._state.get("commanderOverview", {}).get("credits", {})
                if isinstance(self._state.get("commanderOverview"), dict) else {}
            ),
            credit_snapshots=getattr(self, "_commander_credit_snapshots", []),
        ), self._commander_finance_period, events)
        self._derived_cache["commander_finance_history"] = (cache_key, rows)
        return rows

    def _commander_finance_summary(self):
        cache_key = (self._state_revision, self._commander_finance_period)
        cached = self._derived_cache.get("commander_finance_summary")
        if cached and cached[0] == cache_key:
            return cached[1]
        summary = build_finance_summary(self._commander_finance_history())
        self._derived_cache["commander_finance_summary"] = (cache_key, summary)
        return summary

    @staticmethod
    def _ship_asset_key(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    def _commander_fleet(self):
        asset_symbols = {}
        asset_names = {}
        for record in self._ship_catalog if isinstance(self._ship_catalog, list) else []:
            if not isinstance(record, dict):
                continue
            symbol = str(record.get("symbol") or "").strip()
            if not symbol:
                continue
            for value in (symbol, record.get("name")):
                key = self._ship_asset_key(value)
                if key:
                    asset_symbols[key] = symbol
                    asset_names[key] = str(record.get("name") or "").strip()
        rows = []
        for source in self._state.get("fleet", []) or []:
            if not isinstance(source, dict):
                continue
            row = dict(source)
            ship_id = str(row.get("id") or "")
            filename = self._fleet_images.get(ship_id, "")
            image_path = self.fleet_images_dir / filename if filename else None
            row["customImageSource"] = (
                QUrl.fromLocalFile(str(image_path.resolve())).toString()
                if image_path and image_path.is_file() else ""
            )
            type_key = self._ship_asset_key(row.get("type"))
            symbol = asset_symbols.get(type_key, "")
            if asset_names.get(type_key):
                row["type"] = asset_names[type_key]
            row["schematicSource"] = (
                f"assets/ships/{symbol}.svg" if symbol else ""
            )
            row["valueKnown"] = isinstance(row.get("value"), int)
            row["rebuyKnown"] = isinstance(row.get("rebuy"), int)
            rows.append(row)
        rows.sort(key=lambda row: (
            not bool(row.get("isCurrent")),
            str(row.get("type") or "").casefold(),
            str(row.get("name") or "").casefold(),
        ))
        return rows

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

    def _filtered_logbook_entries(self) -> list[dict[str, object]]:
        revision = (
            self._logbook_revision, self._logbook_filter, self._logbook_query,
        )

        def build() -> list[dict[str, object]]:
            return build_logbook_view(
                self._logbook_entries, self._logbook_notes,
                self._logbook_filter, self._logbook_query,
            )

        return self._cached_derived("logbook", revision, build)

    def _logbook_entry_with_note(
        self, row: dict[str, object],
    ) -> dict[str, object]:
        return decorate_logbook_entry(row, self._logbook_notes)

    @Slot(str)
    def setLogbookFilter(self, value: str) -> None:
        value = str(value or "ALL").upper()
        if value not in LOGBOOK_FILTERS:
            value = "ALL"
        if value != self._logbook_filter:
            self._logbook_filter = value
            self._drop_derived({"logbook"})
            self.logbookChanged.emit()

    @Slot(str)
    def setLogbookQuery(self, value: str) -> None:
        value = str(value or "").strip().casefold()
        if value != self._logbook_query:
            self._logbook_query = value
            self._drop_derived({"logbook"})
            self.logbookChanged.emit()

    @Slot(str)
    def selectLogbookEntry(self, entry_id: str) -> None:
        entry_id = str(entry_id or "")
        selected = next(
            (row for row in self._logbook_entries if row.get("id") == entry_id),
            {},
        )
        self._selected_logbook_entry = (
            self._logbook_entry_with_note(selected) if selected else {}
        )
        self.logbookChanged.emit()

    @Slot(str, str)
    def setLogbookNote(self, entry_id: str, note: str) -> None:
        entry_id = str(entry_id or "").strip()
        if not any(row.get("id") == entry_id for row in self._logbook_entries):
            return
        self._logbook_notes = write_logbook_note(
            self.config_dir, entry_id, note,
        )
        if self._selected_logbook_entry.get("id") == entry_id:
            selected = next(
                row for row in self._logbook_entries if row.get("id") == entry_id
            )
            self._selected_logbook_entry = self._logbook_entry_with_note(selected)
        self._logbook_revision += 1
        self._drop_derived({"logbook"})
        self._activity = (
            "Logbook note saved."
            if self._logbook_notes.get(entry_id) else "Logbook note removed."
        )
        self.logbookChanged.emit()
        self.activityChanged.emit()

    @Slot(str)
    def deleteLogbookNote(self, entry_id: str) -> None:
        self.setLogbookNote(entry_id, "")

    @Slot()
    def clearSelectedLogbookEntry(self) -> None:
        if self._selected_logbook_entry:
            self._selected_logbook_entry = {}
            self.logbookChanged.emit()

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

    def _eddn_delivery_for_candidate(
        self, candidate, eddn_queue=None, eddn_config=None,
    ):
        evidence = str(candidate.get("evidence_kind") or "")
        if evidence not in {"LOCAL_JOURNAL", "ENTERED"}:
            return ""
        if candidate.get("details_unknown"):
            return "EDDN NOT SHAREABLE"
        config = self._eddn_config if eddn_config is None else eddn_config
        queue = self._eddn_queue if eddn_queue is None else eddn_queue
        if not eddn_upload_allowed(config):
            return "EDDN OFF"
        address = candidate.get("system_address")
        timestamp = self._state_find_timestamp(candidate.get("latest_timestamp"))
        for job in reversed(queue):
            prepared = job.get("event") or {}
            if prepared.get("schema") != "fsssignaldiscovered/1":
                continue
            message = prepared.get("message") or {}
            if address is not None and message.get("SystemAddress") != address:
                continue
            if timestamp >= 0 and abs(
                self._state_find_timestamp(message.get("timestamp")) - timestamp
            ) > 0.5:
                continue
            return {
                "queued": "EDDN QUEUED", "retry": "EDDN RETRY",
                "sending": "EDDN SENDING", "sent": "EDDN SENT",
                "failed": "EDDN FAILED",
            }.get(str(job.get("status") or ""), "EDDN PENDING")
        return "EDDN PENDING"

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

    ship = Property(str, lambda self: self._get("ship", "No ship"), notify=stateChanged)
    ships = Property("QStringList", lambda self: self._get("ships", []), notify=stateChanged)
    commanderKnown = Property(
        bool, lambda self: bool(self._get("commanderKnown", False)), notify=stateChanged
    )
    commander = Property(
        str, lambda self: str(self._get("commander", "")), notify=stateChanged
    )
    commanderOverview = Property(
        "QVariantMap", lambda self: self._get("commanderOverview", {}),
        notify=stateChanged,
    )
    powerplayOverview = Property(
        "QVariantMap", lambda self: self._get("powerplayOverview", {}),
        notify=stateChanged,
    )
    commanderCards = Property(
        "QVariantMap", lambda self: self._commander_cards(),
        notify=commanderCardsChanged,
    )
    commanderFinanceHistory = Property(
        "QVariantList", lambda self: self._commander_finance_history(),
        notify=commanderCardsChanged,
    )
    commanderFinanceSummary = Property(
        "QVariantMap", lambda self: self._commander_finance_summary(),
        notify=commanderCardsChanged,
    )
    commanderFinancePeriod = Property(
        str, lambda self: self._commander_finance_period,
        notify=commanderCardsChanged,
    )
    commanderFleet = Property(
        "QVariantList", lambda self: self._commander_fleet(),
        notify=fleetChanged,
    )
    commanderCardOrder = Property(
        "QVariantList", lambda self: list(self._commander_card_order),
        notify=uiChanged,
    )
    navigationOrder = Property(
        "QVariantList", lambda self: list(self._navigation_order),
        notify=uiChanged,
    )
    fleetKnown = Property(
        bool, lambda self: bool(self._get("fleetKnown", False)), notify=stateChanged
    )
    emptyStateReason = Property(
        str, lambda self: str(self._get("emptyStateReason", "")), notify=stateChanged
    )
    activeShip = Property(
        str, lambda self: self._get("activeShip", ""), notify=stateChanged
    )
    followActiveShip = Property(
        bool, lambda self: self._follow_active_ship, notify=stateChanged
    )
    system = Property(str, lambda self: self._get("system", "Unknown"), notify=stateChanged)
    nextAction = Property(str, lambda self: self._next_action(), notify=stateChanged)
    operationAction = Property(
        "QVariantMap", lambda self: self._operation_action(),
        notify=operationsChanged,
    )
    completion = Property(float, lambda self: float(self._get("completion", 0.0)), notify=stateChanged)
    materialStatus = Property(
        str, lambda self: str(self._get("materialStatus", "MISSING")),
        notify=stateChanged,
    )
    completionReliable = Property(
        bool, lambda self: bool(self._get("completionReliable", False)),
        notify=stateChanged,
    )
    planProgressStatus = Property(
        str, lambda self: str(self._get("planProgressStatus", "NOT STARTED")),
        notify=stateChanged,
    )
    craftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("craftTrackingIssues", []),
        notify=wishlistChanged,
    )
    freshCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("freshCraftTrackingIssues", []),
        notify=wishlistChanged,
    )
    historicalCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("historicalCraftTrackingIssues", []),
        notify=wishlistChanged,
    )
    relevantCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("relevantCraftTrackingIssues", []),
        notify=wishlistChanged,
    )
    unrelatedCraftTrackingIssues = Property(
        "QVariantList", lambda self: self._get("unrelatedCraftTrackingIssues", []),
        notify=wishlistChanged,
    )
    covered = Property(int, lambda self: int(self._get("covered", 0)), notify=stateChanged)
    required = Property(int, lambda self: int(self._get("required", 0)), notify=stateChanged)
    calculationWarning = Property(
        str, lambda self: str(self._get("calculationWarning", "")),
        notify=stateChanged,
    )
    missingKinds = Property(int, lambda self: int(self._get("missingKinds", 0)), notify=stateChanged)
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
        notify=stateChanged,
    )
    recentCrafts = Property(
        "QVariantList", lambda self: self._get("recentCrafts", []),
        notify=stateChanged,
    )
    lastChangeReason = Property(
        str, lambda self: self._get("lastChangeReason", ""),
        notify=stateChanged,
    )
    blueprints = Property("QVariantList", lambda self: self._get("blueprints", []), notify=wishlistChanged)
    activeBlueprints = Property(
        "QVariantList",
        lambda self: [
            row for row in self._get("blueprints", [])
            if str(row.get("targetStatus") or "") != "completed"
        ],
        notify=wishlistChanged,
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
    exobiologyFindings = Property(
        "QVariantList", lambda self: self._get("exobiologyFindings", []),
        notify=exobiologyChanged,
    )
    exobiologySummary = Property(
        "QVariantMap", lambda self: self._get("exobiologySummary", {}),
        notify=exobiologyChanged,
    )
    exobiologySessionSummary = Property(
        "QVariantMap", lambda self: self._get("exobiologySessionSummary", {}),
        notify=exobiologyChanged,
    )
    exobiologyCarriedSummary = Property(
        "QVariantMap", lambda self: self._get("exobiologyCarriedSummary", {}),
        notify=exobiologyChanged,
    )
    exobiologyLandingTargets = Property(
        "QVariantList", lambda self: self._get("exobiologyLandingTargets", []),
        notify=exobiologyChanged,
    )
    exobiologyDistanceCheck = Property(
        "QVariantMap", lambda self: self._exobiology_distance_check_value,
        notify=exobiologyDistanceCheckChanged,
    )
    engineers = Property(
        "QVariantList", lambda self: self._engineer_index(),
        notify=operationsChanged,
    )
    techBrokerGuide = Property(
        "QVariantList", lambda self: self._get("techBrokerGuide", []),
        notify=operationsChanged,
    )
    techBrokerTrack = Property(
        "QVariantMap", lambda self: self._get("techBrokerTrack", {}),
        notify=operationsChanged,
    )
    trackedItems = Property(
        "QVariantList", lambda self: self._get("trackedItems", []),
        notify=operationsChanged,
    )
    engineerMissionRoute = Property(
        "QVariantList", lambda self: self._engineer_mission_route(),
        notify=operationsChanged,
    )
    engineerUnlockTasks = Property(
        "QVariantList", lambda self: self._engineer_unlock_tasks(),
        notify=operationsChanged,
    )
    engineeringRunPreflight = Property(
        "QVariantMap", lambda self: self._engineering_run_preflight(),
        notify=operationsChanged,
    )
    nextEngineerStop = Property(
        "QVariantMap",
        lambda self: (
            self._engineer_mission_route()[0]
            if self._engineer_mission_route() else {}
        ),
        notify=operationsChanged,
    )
    activity = Property(str, lambda self: self._activity, notify=activityChanged)
    rendererMode = Property(str, lambda self: self._renderer_mode, notify=rendererChanged)
    rendererActive = Property(str, lambda self: self._renderer_active, notify=rendererChanged)
    restartRequired = Property(bool, lambda self: self._restart_required, notify=rendererChanged)
    uiScale = Property(float, lambda self: self._ui_scale, notify=uiChanged)
    theme = Property(str, lambda self: self._theme, notify=uiChanged)
    interfaceLanguage = Property(
        str, lambda self: self._interface_language, notify=uiChanged,
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
    reducedMotion = Property(bool, lambda self: self._reduced_motion, notify=uiChanged)
    commanderUpdatePopups = Property(
        bool, lambda self: self._commander_update_popups, notify=uiChanged,
    )
    enhancedVisuals = Property(
        bool, lambda self: self._enhanced_visuals, notify=uiChanged,
    )
    onboardingComplete = Property(
        bool, lambda self: self._onboarding_complete, notify=uiChanged
    )
    lastPage = Property(int, lambda self: self._last_page, notify=uiChanged)
    debugMode = Property(bool, lambda self: self._debug_mode, notify=uiChanged)
    journalAuto = Property(
        bool, lambda self: self._journal_auto, notify=uiChanged,
    )
    backgroundMode = Property(
        bool, lambda self: self._background_mode, notify=uiChanged,
    )
    autostartEnabled = Property(
        bool, lambda self: self._autostart_enabled, notify=uiChanged,
    )
    systemTrayAvailable = Property(
        bool, lambda self: self._system_tray_available, notify=uiChanged,
    )
    backgroundRuntimeStatus = Property(
        str, lambda self: self._background_runtime_status, notify=uiChanged,
    )
    inaraCommander = Property(
        str, lambda self: str(self._inara_config.get("commander_name") or ""),
        notify=connectionChanged,
    )
    inaraApiKey = Property(
        str, lambda self: str(self._inara_config.get("api_key") or ""),
        notify=connectionChanged,
    )
    inaraConsent = Property(
        bool, lambda self: bool(self._inara_config.get("consent")),
        notify=connectionChanged,
    )
    inaraAutoSync = Property(
        bool, lambda self: bool(self._inara_config.get("auto_sync")),
        notify=connectionChanged,
    )
    inaraKeyConfigured = Property(
        bool, lambda self: bool(self._inara_config.get("api_key")),
        notify=connectionChanged,
    )
    inaraStatus = Property(
        str, lambda self: self._inara_status, notify=connectionChanged,
    )
    inaraBusy = Property(
        bool, lambda self: self._inara_busy, notify=connectionChanged,
    )
    historyExportBusy = Property(
        bool, lambda self: self._history_export_busy,
        notify=connectionChanged,
    )
    inaraReceipts = Property(
        "QVariantList", lambda self: self._inara_receipts,
        notify=connectionChanged,
    )
    frontierConnected = Property(
        bool, lambda self: self._frontier_tokens is not None,
        notify=connectionChanged,
    )
    frontierBusy = Property(
        bool, lambda self: self._frontier_busy,
        notify=connectionChanged,
    )
    frontierStatus = Property(
        str, lambda self: self._frontier_status,
        notify=connectionChanged,
    )
    frontierLastSync = Property(
        str, lambda self: self._frontier_last_sync,
        notify=connectionChanged,
    )
    frontierConsent = Property(
        bool, lambda self: bool(self._frontier_config.get("consent")),
        notify=connectionChanged,
    )
    eddnConsent = Property(
        bool, lambda self: bool(self._eddn_config.get("consent")),
        notify=connectionChanged,
    )
    eddnUploadEnabled = Property(
        bool, lambda self: bool(self._eddn_config.get("upload_enabled")),
        notify=connectionChanged,
    )
    eddnListenerEnabled = Property(
        bool, lambda self: bool(self._eddn_config.get("listener_enabled")),
        notify=connectionChanged,
    )
    eddnStatus = Property(
        str, lambda self: self._eddn_status, notify=connectionChanged,
    )
    eddnParity = Property(
        "QVariantMap", lambda self: schema_parity_report(),
        notify=connectionChanged,
    )
    eddnStationStatus = Property(
        str, lambda self: self._eddn_station_status_summary(),
        notify=connectionChanged,
    )
    eddnListenerStatus = Property(
        str, lambda self: self._eddn_listener_status,
        notify=connectionChanged,
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
        notify=stateChanged,
    )
    miningRevision = Property(
        int,
        lambda self: self._state_revision + len(
            self._mining_catalog.get("candidates", [])
            if isinstance(self._mining_catalog, dict) else []
        ),
        notify=stateChanged,
    )
    miningCacheSummary = Property(
        "QVariantMap", lambda self: self._mining_cache_summary(),
        notify=stateChanged,
    )
    miningSyncBusy = Property(
        bool, lambda self: self._mining_sync_busy, notify=miningChanged,
    )
    miningSyncStatus = Property(
        str, lambda self: self._mining_sync_status, notify=miningChanged,
    )
    eddnBusy = Property(
        bool, lambda self: self._eddn_busy, notify=connectionChanged,
    )
    eddnQueue = Property(
        "QVariantList", lambda self: self._eddn_queue_view(),
        notify=connectionChanged,
    )
    eddnQuarantine = Property(
        "QVariantList", lambda self: self._eddn_quarantine_view(),
        notify=connectionChanged,
    )
    eddnDeliverySummary = Property(
        "QVariantMap", lambda self: self._eddn_delivery_summary(),
        notify=connectionChanged,
    )
    eddnStationSnapshots = Property(
        "QVariantList", lambda self: self._eddn_station_snapshot_view(),
        notify=connectionChanged,
    )
    edmcParallelStatus = Property(
        "QVariantMap", lambda self: self._edmc_parallel_status(),
        notify=connectionChanged,
    )
    traderSyncBusy = Property(
        bool, lambda self: self._trader_sync_busy, notify=connectionChanged,
    )
    traderSyncStatus = Property(
        str, lambda self: self._trader_sync_status, notify=connectionChanged,
    )
    techBrokerSyncBusy = Property(
        bool, lambda self: self._tech_broker_sync_busy, notify=connectionChanged,
    )
    techBrokerSyncStatus = Property(
        str, lambda self: self._tech_broker_sync_status, notify=connectionChanged,
    )
    spanshCatalogSyncBusy = Property(
        bool,
        lambda self: self._trader_sync_busy or self._tech_broker_sync_busy,
        notify=connectionChanged,
    )
    spanshCatalogSyncStatus = Property(
        str,
        lambda self: (
            f"MATERIAL TRADERS · {self._trader_sync_status}\n"
            f"TECH BROKERS · {self._tech_broker_sync_status}"
        ),
        notify=connectionChanged,
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
    logbookEntries = Property(
        "QVariantList", lambda self: self._filtered_logbook_entries(),
        notify=logbookChanged,
    )
    logbookFilters = Property(
        "QStringList", lambda self: list(LOGBOOK_FILTERS), constant=True,
    )
    logbookFilter = Property(
        str, lambda self: self._logbook_filter, notify=logbookChanged,
    )
    logbookQuery = Property(
        str, lambda self: self._logbook_query, notify=logbookChanged,
    )
    selectedLogbookEntry = Property(
        "QVariantMap", lambda self: self._selected_logbook_entry,
        notify=logbookChanged,
    )
    currentSession = Property(
        "QVariantMap", lambda self: self._state.get("currentSession", {}),
        notify=logbookChanged,
    )
    recentSessions = Property(
        "QVariantList", lambda self: self._state.get("recentSessions", []),
        notify=logbookChanged,
    )
    serviceStatus = Property(
        "QVariantList", lambda self: self._service_status(),
        notify=connectionChanged,
    )
    interfaceActivity = Property(
        "QVariantList",
        lambda self: build_interface_activity_feed(
            self._inara_receipts, self._eddn_queue, self._frontier_last_sync,
        ),
        notify=connectionChanged,
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
    journalPath = Property(str, lambda self: str(journal_dir()), notify=stateChanged)
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
        notify=engineeringChanged,
    )
    selectedBlueprint = Property(
        "QVariantMap", lambda self: self._selected_blueprint,
        notify=engineeringChanged,
    )
    currentGrade = Property(
        int, lambda self: self._current_grade, notify=engineeringChanged
    )
    targetGrade = Property(
        int, lambda self: self._target_grade, notify=engineeringChanged
    )
    editingGradeComplete = Property(
        bool, lambda self: self._editing_grade_complete, notify=engineeringChanged
    )
    selectedExperimentalId = Property(
        str, lambda self: self._selected_experimental_id,
        notify=engineeringChanged,
    )
    planMode = Property(str, lambda self: self._plan_mode, notify=engineeringChanged)
    canPinEngineeringPlan = Property(
        bool, lambda self: self._can_pin_engineering_plan(),
        notify=engineeringChanged,
    )
    selectedEngineer = Property(
        str, lambda self: self._selected_engineer, notify=engineeringChanged
    )
    engineeringStatus = Property(
        str, lambda self: self._engineering_status, notify=engineeringChanged
    )
    craftConfirmation = Property(
        str, lambda self: self._craft_confirmation, notify=engineeringChanged
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
        str, lambda self: self._fleet_status, notify=engineeringChanged
    )
    armedPlanId = Property(
        str, lambda self: self._armed_plan_id, notify=engineeringChanged
    )
    editingPlanIndex = Property(
        int, lambda self: self._editing_plan_index, notify=engineeringChanged
    )
    moduleInstance = Property(
        str, lambda self: self._module_instance, notify=engineeringChanged
    )
    selectedModuleSlot = Property(
        str, lambda self: self._selected_module_slot, notify=engineeringChanged
    )
    moduleSlotOptions = Property(
        "QVariantList", lambda self: self._module_slot_options,
        notify=engineeringChanged,
    )
    traderPreference = Property(
        str, lambda self: self._trader_preference, notify=uiChanged,
    )
    engineeringInstalledModules = Property(
        "QVariantList",
        lambda self: self._state.get("engineeringModuleSlots", []),
        notify=stateChanged,
    )
    engineeringShipSlots = Property(
        "QVariantList",
        lambda self: self._state.get("engineeringShipSlots", []),
        notify=stateChanged,
    )
    engineeringShipCatalog = Property(
        "QVariantList", lambda self: self._ship_catalog, constant=True,
    )
    selectedShipType = Property(
        str, lambda self: str(self._state.get("selectedShipType") or ""),
        notify=stateChanged,
    )
    selectedShipStats = Property(
        "QVariantMap",
        lambda self: self._state.get("selectedShipStats", {}),
        notify=stateChanged,
    )
    buildImportPreview = Property(
        "QVariantMap", lambda self: self._build_import_preview,
        notify=engineeringChanged,
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

    @Slot(bool)
    def setFrontierConsent(self, consent):
        consent = bool(consent)
        if bool(self._frontier_config.get("consent")) == consent:
            return
        self._frontier_config["consent"] = consent
        self._save_frontier_config()
        if not consent:
            self._frontier_authorization = None
            if not self._frontier_busy:
                self._frontier_status = (
                    "CONSENT WITHDRAWN · Existing local tokens are unaffected."
                )
        self.connectionChanged.emit()

    @Slot()
    def connectFrontier(self):
        if self._frontier_busy:
            return
        if not self._frontier_config.get("consent"):
            self._frontier_status = (
                "CONSENT REQUIRED · Tick the Companion API consent box first."
            )
            self.connectionChanged.emit()
            return
        try:
            authorization = build_pkce_authorization(
                FRONTIER_CLIENT_ID, FRONTIER_REDIRECT_URI
            )
        except ValueError:
            self._frontier_status = "AUTHORIZATION SETUP FAILED"
            self.connectionChanged.emit()
            return
        self._frontier_authorization = authorization
        if QDesktopServices.openUrl(QUrl(authorization.authorize_url)):
            self._frontier_status = (
                "BROWSER OPENED · Complete the Frontier login there."
            )
        else:
            self._frontier_authorization = None
            self._frontier_status = "BROWSER COULD NOT BE OPENED"
        self.connectionChanged.emit()

    @Slot(str)
    def acceptFrontierOAuthCallback(self, callback_url):
        authorization = self._frontier_authorization
        if authorization is None:
            self._frontier_status = (
                "NO LOGIN WAITING · Start a new Frontier connection."
            )
            self.connectionChanged.emit()
            return
        try:
            code = parse_authorization_callback(
                callback_url, authorization.state
            )
        except FrontierAuthError as exc:
            self._frontier_authorization = None
            self._frontier_status = f"AUTHORIZATION FAILED · {exc}"
            self.connectionChanged.emit()
            return
        self._frontier_authorization = None
        self._start_frontier_profile_request(
            authorization=authorization, authorization_code=code
        )

    @Slot()
    def refreshFrontierProfile(self):
        if self._frontier_busy:
            return
        if self._frontier_tokens is None:
            self._frontier_status = "NOT CONNECTED · Connect Frontier first."
            self.connectionChanged.emit()
            return
        self._start_frontier_profile_request(tokens=self._frontier_tokens)

    @Slot()
    def disconnectFrontier(self):
        if self._frontier_busy:
            return
        try:
            self._frontier_credential_store.clear()
        except FrontierCredentialError as exc:
            self._frontier_status = f"DISCONNECT FAILED · {exc}"
            self.connectionChanged.emit()
            return
        self._frontier_tokens = None
        self._frontier_client = None
        self._frontier_authorization = None
        self._frontier_last_sync = ""
        self._frontier_status = "NOT CONNECTED · Local Frontier tokens removed."
        self.connectionChanged.emit()

    def _start_frontier_profile_request(
        self, *, tokens=None, authorization=None, authorization_code=""
    ):
        if self._frontier_busy:
            return
        self._frontier_busy = True
        self._frontier_request_token += 1
        request_token = self._frontier_request_token
        profile_generation = self._profile_generation
        existing_client = self._frontier_client
        self._frontier_status = "CONTACTING FRONTIER…"
        self.connectionChanged.emit()

        def worker():
            active_tokens = tokens
            client = existing_client
            try:
                if authorization is not None:
                    active_tokens = exchange_authorization_code(
                        FRONTIER_CLIENT_ID, authorization,
                        authorization_code,
                    )
                    client = None
                elif active_tokens.expires_within(60):
                    active_tokens = refresh_frontier_tokens(
                        FRONTIER_CLIENT_ID, active_tokens.refresh_token
                    )
                    client = None
                if client is None:
                    client = FrontierCapiClient(
                        active_tokens.access_token,
                        token_type=active_tokens.token_type,
                    )
                snapshot = client.query("/profile")
                self.frontierFinished.emit({
                    "requestToken": request_token,
                    "profileGeneration": profile_generation,
                    "tokens": active_tokens,
                    "client": client,
                    "profile": project_profile_snapshot(snapshot),
                    "error": "",
                })
            except FrontierCapiError as exc:
                self.frontierFinished.emit({
                    "requestToken": request_token,
                    "profileGeneration": profile_generation,
                    "tokens": active_tokens,
                    "client": client,
                    "profile": {},
                    "error": str(exc),
                })
            except Exception as exc:
                # Any unexpected failure must still report back, or the tab
                # stays pinned in its busy state until the app restarts.
                LOGGER.exception("Frontier CAPI worker failed")
                self.frontierFinished.emit({
                    "requestToken": request_token,
                    "profileGeneration": profile_generation,
                    "tokens": active_tokens,
                    "client": client,
                    "profile": {},
                    "error": (
                        "Unexpected local Frontier connector error: "
                        f"{type(exc).__name__}"
                    ),
                })

        if self._start_network_worker(worker, "frontier-capi-profile"):
            self._frontier_watchdog.start()
        else:
            self._frontier_busy = False
            self._frontier_status = "FRONTIER REQUEST COULD NOT START"
            self.connectionChanged.emit()

    @Slot(object)
    def _finish_frontier(self, result):
        if not isinstance(result, dict):
            return
        if (
            int(result.get("requestToken", -1)) != self._frontier_request_token
            or int(result.get("profileGeneration", -1))
            != self._profile_generation
        ):
            return
        self._frontier_watchdog.stop()
        self._frontier_busy = False
        tokens = result.get("tokens")
        storage_error = ""
        if tokens is not None:
            self._frontier_tokens = tokens
            self._frontier_client = result.get("client")
            try:
                self._frontier_credential_store.save(tokens)
            except FrontierCredentialError as exc:
                storage_error = str(exc)
        error = str(result.get("error") or "")
        profile = result.get("profile")
        if isinstance(profile, dict) and profile:
            self._apply_frontier_profile(profile)
            self._frontier_last_sync = str(profile.get("observedAt") or "")
        if storage_error:
            self._frontier_status = (
                "CONNECTED FOR THIS RUN · Secure token storage failed."
            )
        elif error and self._frontier_tokens is not None:
            self._frontier_status = f"CONNECTED · PROFILE SYNC FAILED · {error}"
        elif error:
            self._frontier_status = (
                "AUTHORIZATION FAILED · Frontier approval may still be pending."
            )
        else:
            self._frontier_status = "CONNECTED · COMMANDER PROFILE UPDATED"
        self.connectionChanged.emit()

    @Slot()
    def _frontier_request_timed_out(self):
        """Release the CAPI tab if a worker never reported a result."""
        if not self._frontier_busy:
            return
        LOGGER.warning("Frontier CAPI request exceeded the watchdog interval")
        self._frontier_busy = False
        self._frontier_authorization = None
        self._frontier_status = (
            "FRONTIER REQUEST TIMED OUT · Check your connection and try again."
        )
        self.connectionChanged.emit()

    def _apply_frontier_profile(self, profile):
        self._frontier_profile = dict(profile)
        updated = self._state_with_frontier_profile(self._state, profile)
        overview = updated.get("commanderOverview", {})
        self._record_commander_credit_snapshot(overview.get("credits", {}))
        self._state = updated
        self._publish_full_state()

    @staticmethod
    def _state_with_frontier_profile(state, profile):
        state = dict(state) if isinstance(state, dict) else {}
        if not isinstance(profile, dict) or not profile:
            return state
        overview = merge_capi_commander_overview(
            state.get("commanderOverview", {}), profile
        )
        fleet_state = merge_capi_fleet({
            "active_id": state.get("activeShipId", ""),
            "ships": state.get("fleet", []),
        }, profile)
        merged = {
            **state,
            "commanderOverview": overview,
            "fleet": fleet_state.get("ships", []),
            "fleetKnown": bool(fleet_state.get("ships")),
            "activeShipId": str(fleet_state.get("active_id") or ""),
        }
        return merge_capi_loadout(merged, profile)

    @staticmethod
    def _inara_initial_status(config):
        """Match the Connections card's status badge from the very first frame.

        Mirrors the branching saveInaraConfig already uses, worded for a
        session start rather than a just-completed save.
        """
        config = config if isinstance(config, dict) else {}
        consent = bool(config.get("consent"))
        has_key = bool(str(config.get("api_key") or "").strip())
        if consent and has_key:
            return "Configured from saved settings. Ready to sync."
        if consent:
            return "Consent enabled, but no API key stored yet."
        if has_key:
            return "API key stored. Network access remains disabled."
        return "Ready. No network request has been made."

    @Slot(str, str, bool, bool)
    def saveInaraConfig(self, api_key, commander, consent, auto_sync):
        if not self._sync_eddn_profile():
            return
        previous = dict(self._inara_config)
        api_key = str(api_key or "").strip()
        # Only overwrite the stored key when the user actually provided one.
        # An empty field means "keep the existing key" (use CLEAR KEY to remove).
        if api_key:
            self._inara_config["api_key"] = api_key
        self._inara_config.update({
            "commander_name": str(commander or "").strip(),
            "consent": bool(consent),
            "auto_sync": bool(auto_sync),
        })
        if not self._save_inara_config():
            self._inara_config = previous
            self._inara_status = (
                "INARA configuration could not be saved; previous settings remain active."
            )
            self.connectionChanged.emit()
            return
        if not self._inara_auto_enabled():
            self._discard_inara_pending()
        has_key = bool(self._inara_config.get("api_key"))
        if self._inara_config["consent"] and has_key:
            self._inara_status = "Configuration saved locally. Ready to connect."
        elif self._inara_config["consent"] and not has_key:
            self._inara_status = "Consent enabled, but no API key stored yet."
        elif has_key:
            self._inara_status = "Configuration saved. Network access remains disabled."
        else:
            self._inara_status = (
                "Configuration saved. Add an API key and enable consent to connect."
            )
        self.connectionChanged.emit()

    @Slot()
    def clearInaraKey(self):
        if not self._sync_eddn_profile():
            return
        previous = dict(self._inara_config)
        self._inara_config["api_key"] = ""
        self._inara_config["auto_sync"] = False
        if not self._save_inara_config():
            self._inara_config = previous
            self._inara_status = (
                "INARA API key could not be removed from disk; previous settings remain active."
            )
            self.connectionChanged.emit()
            return
        self._discard_inara_pending()
        self._inara_status = "API key removed from local storage."
        self.connectionChanged.emit()

    def _inara_auto_enabled(self):
        return bool(
            self._inara_config.get("consent")
            and self._inara_config.get("auto_sync")
            and self._inara_config.get("api_key")
            and self._inara_config.get("commander_name")
        )

    def _inara_connection_enabled(self):
        return bool(
            self._inara_config.get("consent")
            and self._inara_config.get("api_key")
            and self._inara_config.get("commander_name")
        )

    def _discard_inara_pending(self):
        discarded = list(self._inara_cache.get("fingerprints", []))
        discarded.extend(self._inara_pending_fingerprints)
        discarded.extend(self._inara_inflight_fingerprints)
        self._inara_cache.update({
            "initialized": True,
            "journal_root": self.profile_context.journal_root,
            "fingerprints": discarded[-5000:],
        })
        self._inara_pending_events = []
        self._inara_pending_fingerprints = []
        self._inara_inflight_fingerprints = []
        self._inara_pending_since = 0.0
        self._inara_retry_not_before = 0.0
        self._inara_failure_count = 0
        self._save_inara_journal_cache()

    @staticmethod
    def _prepare_inara_journal_scan(
        identity, journal_root, recovery_file, known, max_events,
        use_cached_events=False,
    ):
        """Read and project Journal data without touching Qt/controller state."""
        paths = journal_paths_for_profile(identity) if identity else []
        recovery_complete = True
        cached_start_file = ""
        if recovery_file:
            recovery_index = next((
                index for index, path in enumerate(paths)
                if path.name == recovery_file
            ), None)
            if recovery_index is not None:
                # Include the confirmed boundary file because Frontier may
                # append more complete records to the current Journal.
                paths = paths[recovery_index:]
                cached_start_file = recovery_file
            else:
                LOGGER.warning(
                    "INARA recovery boundary %s is unavailable; scanning all "
                    "profile Journals",
                    recovery_file,
                )
        if use_cached_events:
            # The state projector already parsed and profile-filtered the full
            # history. Reusing it avoids a second 40+ MB Journal read.
            events = list(profiled_journal_events(cached_start_file))
        else:
            events = []
            for path in paths:
                try:
                    with path.open(
                        "r", encoding="utf-8-sig", errors="replace"
                    ) as handle:
                        for line_number, line in enumerate(handle, 1):
                            try:
                                event = json.loads(line)
                            except (TypeError, ValueError):
                                LOGGER.warning(
                                    "INARA skipped malformed Journal JSON: %s:%s",
                                    path.name, line_number,
                                )
                                continue
                            if isinstance(event, dict):
                                events.append(event)
                except OSError as exc:
                    recovery_complete = False
                    LOGGER.warning(
                        "INARA Journal read failed for %s: %s", path, exc
                    )
        # Cargo.json is Frontier's authoritative itemized current snapshot.
        cargo_path = Path(journal_root) / "Cargo.json"
        try:
            cargo_snapshot = json.loads(cargo_path.read_text(
                encoding="utf-8-sig", errors="strict"
            ))
            if (
                isinstance(cargo_snapshot, dict)
                and cargo_snapshot.get("event") == "Cargo"
                and isinstance(cargo_snapshot.get("Inventory"), list)
            ):
                events.append(cargo_snapshot)
        except FileNotFoundError:
            pass
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning("INARA Cargo snapshot read failed: %s", exc)
        detected, prepared, fingerprints = prepare_journal_batch(
            events, known, identity, max_events=max_events,
        )
        return {
            "detected": detected,
            "prepared": prepared,
            "fingerprints": fingerprints,
            "hasCommunityGoal": any(
                event.get("event") == "CommunityGoal"
                for event in events if isinstance(event, dict)
            ),
            "lastPath": paths[-1].name if paths else "",
            "recoveryComplete": recovery_complete,
            "journalRoot": journal_root,
        }

    def _scan_inara_journal(self):
        """Synchronous compatibility path used by explicit recovery/tests."""
        if not self._sync_eddn_profile():
            return False
        delivered = list(self._inara_cache.get("fingerprints", []))
        known = delivered + self._inara_pending_fingerprints
        result = self._prepare_inara_journal_scan(
            self.profile_context.identity,
            self.profile_context.journal_root,
            str(self._inara_cache.get("journal_recovery_file") or ""),
            known,
            max(0, INARA_PENDING_EVENT_LIMIT - len(
                self._inara_pending_events
            )),
        )
        return self._apply_inara_journal_scan(result)

    def _queue_inara_journal_scan(self):
        """Coalesce INARA history scans and keep them off the GUI thread."""
        if not getattr(self, "_journal_state_ready", False):
            return False
        if self._inara_scan_in_flight:
            self._inara_scan_dirty = True
            return False
        if not self._sync_eddn_profile():
            return False
        self._inara_scan_token = getattr(self, "_inara_scan_token", 0) + 1
        token = self._inara_scan_token
        generation = self._profile_generation
        profile_key = self.profile_context.key
        identity = self.profile_context.identity
        journal_root = self.profile_context.journal_root
        recovery_file = str(
            self._inara_cache.get("journal_recovery_file") or ""
        )
        known = (
            list(self._inara_cache.get("fingerprints", []))
            + list(self._inara_pending_fingerprints)
            + list(self._inara_inflight_fingerprints)
        )
        max_events = max(0, INARA_PENDING_EVENT_LIMIT - len(
            self._inara_pending_events
        ))
        self._inara_scan_in_flight = True
        self._inara_scan_dirty = False

        def worker():
            try:
                result = self._prepare_inara_journal_scan(
                    identity, journal_root, recovery_file, known, max_events,
                    use_cached_events=True,
                )
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            self.inaraJournalScanReady.emit((
                token, generation, profile_key, result,
            ))

        if not self._start_network_worker(worker, "inara-journal-scan"):
            self._inara_scan_in_flight = False
            return False
        return True

    @Slot(object)
    def _finish_inara_journal_scan(self, payload):
        token, generation, profile_key, result = payload
        if token != self._inara_scan_token:
            return
        self._inara_scan_in_flight = False
        current = (
            generation == self._profile_generation
            and profile_key == self.profile_context.key
        )
        if current and isinstance(result, dict) and not result.get("error"):
            self._apply_inara_journal_scan(result)
        elif current:
            LOGGER.warning("INARA background Journal scan failed: %s", result)
        dirty = self._inara_scan_dirty
        self._inara_scan_dirty = False
        if dirty and current:
            self._queue_inara_journal_scan()

    def _apply_inara_journal_scan(self, result):
        journal_root = str(result.get("journalRoot") or "")
        if journal_root != self.profile_context.journal_root:
            return False
        last_path = str(result.get("lastPath") or "")
        recovery_complete = bool(result.get("recoveryComplete", True))
        if self._inara_cache.get("journal_root") != journal_root:
            self._inara_cache = {
                key: self._inara_cache[key]
                for key in ("last_request_at", "rate_limit_until")
                if key in self._inara_cache
            }
            self._inara_pending_events = []
            self._inara_pending_fingerprints = []
        delivered = list(self._inara_cache.get("fingerprints", []))
        known = set(
            delivered + self._inara_pending_fingerprints
            + self._inara_inflight_fingerprints
        )
        pairs = [
            (event, fingerprint)
            for event, fingerprint in zip(
                result.get("prepared", []), result.get("fingerprints", [])
            )
            if fingerprint not in known
        ][:max(0, INARA_PENDING_EVENT_LIMIT - len(self._inara_pending_events))]
        prepared = [event for event, _fingerprint in pairs]
        fingerprints = [fingerprint for _event, fingerprint in pairs]
        detected = result.get("detected", {})
        detected = detected if isinstance(detected, dict) else {}
        config_changed = False
        for key in ("commander_name", "frontier_id"):
            value = str(detected.get(key) or "").strip()
            if value and self._inara_config.get(key) != value:
                self._inara_config[key] = value
                config_changed = True
        if config_changed:
            self._save_inara_config()
            self.connectionChanged.emit()
        if not self._inara_cache.get("initialized"):
            self._inara_cache.update({
                "initialized": True,
                "journal_root": journal_root,
                "fingerprints": (delivered + fingerprints)[-5000:],
            })
            if last_path and recovery_complete:
                self._inara_cache["journal_recovery_file"] = last_path
            self._save_inara_journal_cache()
            return False
        if not self._inara_auto_enabled():
            self._inara_cache["fingerprints"] = (delivered + fingerprints)[-5000:]
            if last_path and recovery_complete:
                self._inara_cache["journal_recovery_file"] = last_path
            self._save_inara_journal_cache()
            return False
        if (
            result.get("hasCommunityGoal")
            and time.time() - float(
                self._inara_cache.get("community_goals_timestamp", 0) or 0
            ) >= 21600
        ):
            bucket = int(time.time() // 21600)
            fingerprint = hashlib.sha256(
                f"getCommunityGoalsRecent:{bucket}".encode("utf-8")
            ).hexdigest()
            if fingerprint not in known and fingerprint not in fingerprints:
                prepared.append(community_goals_event())
                fingerprints.append(fingerprint)
        if prepared and not self._inara_pending_events:
            self._inara_pending_since = time.monotonic()
        self._inara_pending_events.extend(prepared)
        self._inara_pending_fingerprints.extend(fingerprints)
        if (
            last_path and recovery_complete
            and len(self._inara_pending_events) < INARA_PENDING_EVENT_LIMIT
        ):
            self._inara_recovery_candidate_file = last_path
        if (
            not self._inara_pending_events
            and self._inara_recovery_candidate_file
        ):
            self._inara_cache["journal_recovery_file"] = (
                self._inara_recovery_candidate_file
            )
            self._inara_recovery_candidate_file = ""
            self._save_inara_journal_cache()
        if len(self._inara_pending_events) >= INARA_PENDING_EVENT_LIMIT:
            self._inara_status = (
                f"INARA offline queue full ({INARA_PENDING_EVENT_LIMIT}); "
                "new Journal events remain recoverable from the Journal and "
                "will be collected after queued events are delivered."
            )
            LOGGER.warning(self._inara_status)
            self.connectionChanged.emit()
        return bool(prepared)

    def _inara_auto_due(self, now=None):
        now = time.monotonic() if now is None else float(now)
        now_wall = time.time()
        self._inara_request_times = [
            value for value in self._inara_request_times if now - value < 60
        ]
        self._inara_request_wall_times = [
            value for value in self._inara_request_wall_times
            if now_wall - value < 60
        ]
        return bool(
            self._inara_auto_enabled()
            and self._inara_pending_events
            and not self._inara_busy
            and self._inara_pending_since
            and now - self._inara_pending_since >= INARA_BATCH_WINDOW_SECONDS
            and now >= self._inara_retry_not_before
            and time.time() >= float(
                self._inara_cache.get("rate_limit_until", 0) or 0
            )
            and (
                not self._inara_last_request_at
                or now - self._inara_last_request_at
                >= INARA_MIN_REQUEST_INTERVAL_SECONDS
            )
            and len(self._inara_request_times) < INARA_MAX_REQUESTS_PER_MINUTE
            and len(self._inara_request_wall_times) < INARA_MAX_REQUESTS_PER_MINUTE
        )

    def _maybe_start_inara_auto(self, now=None):
        now = time.monotonic() if now is None else float(now)
        if not self._inara_auto_due(now):
            return False
        self._reserve_inara_request(now)
        return bool(self._start_inara("journal", now, rate_reserved=True))

    def _inara_rate_wait_seconds(self, now=None):
        now = time.monotonic() if now is None else float(now)
        now_wall = time.time()
        self._inara_request_times = [
            value for value in self._inara_request_times if now - value < 60
        ]
        self._inara_request_wall_times = [
            value for value in self._inara_request_wall_times
            if now_wall - value < 60
        ]
        waits = [
            max(0.0, self._inara_retry_not_before - now),
            max(0.0, float(self._inara_cache.get("rate_limit_until", 0) or 0)
                - time.time()),
        ]
        if self._inara_last_request_at:
            waits.append(max(
                0.0,
                INARA_MIN_REQUEST_INTERVAL_SECONDS
                - (now - self._inara_last_request_at),
            ))
        if len(self._inara_request_times) >= INARA_MAX_REQUESTS_PER_MINUTE:
            waits.append(max(0.0, 60 - (now - self._inara_request_times[0])))
        if len(self._inara_request_wall_times) >= INARA_MAX_REQUESTS_PER_MINUTE:
            waits.append(max(
                0.0, 60 - (now_wall - self._inara_request_wall_times[0])
            ))
        return int(math.ceil(max(waits)))

    def _reserve_inara_request(self, now=None):
        now = time.monotonic() if now is None else float(now)
        self._inara_request_times.append(now)
        self._inara_last_request_at = now
        now_wall = time.time()
        self._inara_request_wall_times = [
            value for value in self._inara_request_wall_times
            if now_wall - value < 60
        ]
        self._inara_request_wall_times.append(now_wall)
        self._inara_config["request_times"] = self._inara_request_wall_times
        self._inara_cache["last_request_at"] = now_wall
        self._save_inara_config()
        self._save_inara_journal_cache()

    def _start_inara(self, operation, now=None, rate_reserved=False):
        if not self._sync_eddn_profile():
            return False
        if self._inara_busy:
            return False
        now = time.monotonic() if now is None else float(now)
        if not self._inara_connection_enabled():
            return False
        if not rate_reserved:
            wait_seconds = self._inara_rate_wait_seconds(now)
            if wait_seconds:
                self._inara_status = (
                    f"INARA request not sent · shared cooldown active · "
                    f"wait {wait_seconds} seconds"
                )
                self.connectionChanged.emit()
                return False
        config = dict(self._inara_config)
        materials = deepcopy(self._state.get("materials", []))
        # Prepare the event batch outside the worker so every branch has a
        # concrete local value (avoids UnboundLocalError on "journal").
        if operation == "journal":
            if not self._inara_auto_enabled():
                return False
            batch_events = list(self._inara_pending_events[:50])
            if not batch_events:
                return False
            self._inara_inflight_fingerprints = list(
                self._inara_pending_fingerprints[:len(batch_events)]
            )
        elif operation == "materials":
            batch_events = [material_event(materials)]
            self._inara_material_fingerprint = hashlib.sha256(json.dumps(
                batch_events[0].get("eventData", []),
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if self._inara_material_fingerprint == str(
                self._inara_cache.get("material_snapshot_fingerprint") or ""
            ):
                self._inara_status = (
                    "Material snapshot unchanged; no INARA request sent."
                )
                self.connectionChanged.emit()
                return False
        else:
            if operation == "fleet" and time.time() - float(
                self._inara_cache.get("fleet_cache_timestamp", 0) or 0
            ) < 900:
                self._inara_status = (
                    "Fleet profile cache is still current; no INARA request sent."
                )
                self.connectionChanged.emit()
                return False
            batch_events = [profile_event(config.get("commander_name"))]
        if not rate_reserved:
            self._reserve_inara_request(now)
        request_context = {
            "request_id": uuid.uuid4().hex,
            "profile_key": self.profile_context.key,
            "path_generation": self._profile_generation,
            "directory": str(self.profile_context.directory.resolve()),
        }
        self._active_inara_request = request_context
        self._inara_busy = True
        self._inara_status = "Contacting INARA…"
        self.connectionChanged.emit()

        def worker():
            try:
                # batch_events is always bound above for every operation
                local_events = list(batch_events)
                receipt, body = send_events(config, local_events)
                if operation == "fleet":
                    result_data = extract_profile_ships(body)
                elif operation == "journal":
                    queried = any(
                        event.get("eventName") == "getCommunityGoalsRecent"
                        for event in local_events
                    )
                    result_data = {
                        "communityGoalsQueried": queried,
                        "communityGoals": extract_community_goals(body),
                    }
                else:
                    result_data = []
                self.inaraFinished.emit({
                    "context": request_context,
                    "operation": operation,
                    "success": True,
                    "message": json.dumps(receipt),
                    "ships": result_data,
                })
            except InaraError as exc:
                self.inaraFinished.emit({
                    "context": request_context,
                    "operation": operation,
                    "success": False,
                    "message": json.dumps({
                        "message": str(exc),
                        "retryable": exc.retryable,
                        "statusCode": exc.status_code,
                        "schemaError": exc.schema_error,
                        "retryAfter": exc.retry_after,
                    }),
                    "ships": [],
                })
            except Exception as exc:
                LOGGER.exception("INARA worker failed (%s)", operation)
                self.inaraFinished.emit({
                    "context": request_context,
                    "operation": operation,
                    "success": False,
                    "message": (
                        "Unexpected local connector error: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "ships": [],
                })

        self._start_network_worker(worker, f"inara-{operation}")
        return True

    def _inara_last_success_label(self):
        receipt = next((
            row for row in self._inara_receipts
            if isinstance(row, dict) and row.get("timestamp")
        ), {})
        if not receipt:
            return "no successful request recorded"
        operation = str(receipt.get("operation") or "request")
        return f"{receipt['timestamp']} · {operation}"

    @Slot()
    def testInaraConnection(self):
        self._start_inara("test")

    @Slot()
    def syncInaraMaterials(self):
        self._start_inara("materials")

    @Slot()
    def importInaraFleet(self):
        self._start_inara("fleet")

    @Slot(object)
    def _finish_inara(self, result):
        if not isinstance(result, dict):
            LOGGER.warning("Discarded malformed INARA completion")
            return
        request_context = result.get("context")
        active_context = self._active_inara_request
        current_directory = str(self.profile_context.directory.resolve())
        valid_context = bool(
            isinstance(request_context, dict)
            and isinstance(active_context, dict)
            and request_context.get("request_id")
            == active_context.get("request_id")
            and request_context.get("profile_key") == self.profile_context.key
            and request_context.get("path_generation") == self._profile_generation
            and request_context.get("directory") == current_directory
        )
        if not valid_context:
            LOGGER.warning(
                "Discarded stale INARA completion for request %s",
                request_context.get("request_id")
                if isinstance(request_context, dict) else "unknown",
            )
            return
        self._active_inara_request = None
        self._inara_busy = False
        operation = result.get("operation")
        success = bool(result.get("success"))
        message = result.get("message", "")
        ships = result.get("ships", [])
        if operation == "journal" and not self._inara_auto_enabled():
            self._inara_inflight_fingerprints = []
            self.connectionChanged.emit()
            return
        if not success:
            try:
                failure = json.loads(message)
            except (TypeError, ValueError):
                failure = {"message": str(message), "retryable": True}
            if not isinstance(failure, dict):
                failure = {"message": str(message), "retryable": True}
            error_message = str(failure.get("message") or message)
            retryable = bool(failure.get("retryable", True))
            try:
                status_code = int(failure.get("statusCode"))
            except (TypeError, ValueError):
                status_code = None
            retry_note = ""
            if operation == "journal" and self._inara_pending_events and retryable:
                self._inara_failure_count = getattr(
                    self, "_inara_failure_count", 0
                ) + 1
                delay = min(
                    INARA_RETRY_MAX_SECONDS,
                    INARA_RETRY_BASE_SECONDS * (2 ** (self._inara_failure_count - 1)),
                )
                self._inara_retry_not_before = time.monotonic() + delay
                rate_limited = status_code == 429 or any(
                    marker in error_message.casefold() for marker in (
                        "too much requests", "temporarily revoked", "rate limit",
                    )
                )
                if rate_limited:
                    try:
                        cooldown = max(0, int(failure.get("retryAfter")))
                    except (TypeError, ValueError):
                        cooldown = INARA_RATE_LIMIT_COOLDOWN_SECONDS
                    self._inara_retry_not_before = (
                        time.monotonic() + cooldown
                    )
                    self._inara_cache["rate_limit_until"] = (
                        time.time() + cooldown
                    )
                    self._save_inara_journal_cache()
                retry_note = (
                    f" · {len(self._inara_pending_events)} journal event(s) retained; "
                    + (
                        f"INARA cooldown active for {cooldown} seconds"
                        if rate_limited else "automatic sync will retry"
                    )
                )
            elif operation == "journal" and self._inara_pending_events:
                self._inara_retry_not_before = float("inf")
                retry_note = (
                    f" · {len(self._inara_pending_events)} journal event(s) retained; "
                    "automatic retry stopped until the schema/request problem is reviewed"
                )
            self._inara_status = (
                f"FAILED · {error_message} · LAST ACCEPTED · "
                f"{self._inara_last_success_label()}{retry_note}"
            )
            LOGGER.warning("INARA operation %s failed: %s", operation, error_message)
            self.connectionChanged.emit()
            return
        receipt = json.loads(message)
        labels = {
            "test": "Connection accepted",
            "materials": "Material snapshot accepted",
            "fleet": "Fleet profile accepted",
            "journal": "Journal batch accepted",
        }
        receipt["operation"] = labels.get(operation, operation)
        if operation == "journal":
            self._inara_failure_count = 0
            self._inara_retry_not_before = 0.0
            self._inara_cache.pop("rate_limit_until", None)
            count = len(self._inara_inflight_fingerprints)
            accepted_indexes = set(receipt.get("acceptedIndexes", range(count)))
            failed_indexes = set(receipt.get("failedIndexes", []))
            retryable_failed = set(receipt.get("retryableFailedIndexes", []))
            accepted_indexes = {
                index for index in accepted_indexes
                if isinstance(index, int) and 0 <= index < count
            }
            failed_indexes = {
                index for index in failed_indexes
                if isinstance(index, int) and 0 <= index < count
            }
            if accepted_indexes & failed_indexes or (
                accepted_indexes | failed_indexes
            ) != set(range(count)):
                LOGGER.warning("Discarded malformed partial INARA receipt")
                accepted_indexes = set()
                failed_indexes = set(range(count))
                retryable_failed = set(range(count))
            retryable_failed &= failed_indexes
            permanently_rejected = failed_indexes - retryable_failed
            delivered = list(self._inara_cache.get("fingerprints", []))
            delivered.extend(
                fingerprint
                for index, fingerprint in enumerate(
                    self._inara_inflight_fingerprints
                )
                if index in accepted_indexes or index in permanently_rejected
            )
            self._inara_cache.update({
                "initialized": True,
                "journal_root": self.profile_context.journal_root,
                "fingerprints": delivered[-5000:],
            })
            if isinstance(ships, dict) and ships.get("communityGoalsQueried"):
                self._inara_cache["community_goals_timestamp"] = time.time()
                self._inara_cache["community_goals"] = list(
                    ships.get("communityGoals") or []
                )
            failed_events = [
                self._inara_pending_events[index]
                for index in sorted(retryable_failed)
            ]
            failed_fingerprints = [
                self._inara_pending_fingerprints[index]
                for index in sorted(retryable_failed)
            ]
            remaining_events = self._inara_pending_events[count:]
            remaining_fingerprints = self._inara_pending_fingerprints[count:]
            self._inara_pending_events = failed_events + remaining_events
            self._inara_pending_fingerprints = (
                failed_fingerprints + remaining_fingerprints
            )
            self._inara_inflight_fingerprints = []
            self._inara_pending_since = (
                time.monotonic() if self._inara_pending_events else 0.0
            )
            if (
                not self._inara_pending_events
                and self._inara_recovery_candidate_file
            ):
                self._inara_cache["journal_recovery_file"] = (
                    self._inara_recovery_candidate_file
                )
                self._inara_recovery_candidate_file = ""
            self._save_inara_journal_cache()
            if failed_indexes:
                receipt["operation"] = "Journal batch partially accepted"
                parts = [f"{len(accepted_indexes)} accepted"]
                if permanently_rejected:
                    parts.append(
                        f"{len(permanently_rejected)} permanently rejected"
                    )
                if retryable_failed:
                    parts.append(f"{len(retryable_failed)} retained for retry")
                receipt["detail"] = "; ".join(parts)
        if operation == "fleet":
            self._inara_cache["fleet_cache_timestamp"] = time.time()
            self._inara_cache["fleet_cache_count"] = len(list(ships or []))
            receipt["detail"] = (
                f"Received {len(list(ships or []))} INARA ship label(s). "
                "Fleet identity remains authoritative from local Journal ShipIDs."
            )
            self._fleet_status = receipt["detail"]
            self.refresh()
            self.engineeringChanged.emit()
        elif operation == "materials":
            self._inara_cache["material_snapshot_fingerprint"] = (
                self._inara_material_fingerprint
            )
        if operation in {"fleet", "materials"}:
            self._save_inara_journal_cache()
        self._inara_receipts.insert(0, receipt)
        self._save_inara_receipts()
        self._inara_status = (
            f"{receipt['operation']} · HTTP {receipt['httpStatus']} · "
            f"{receipt['elapsedMs']} ms"
        )
        self.connectionChanged.emit()

    @staticmethod
    def _eddn_initial_status(consent):
        """Match the Connections card's status badge from the very first frame.

        The badge is derived live from _eddn_config.get("consent"); the
        detail text must start from the same value instead of a hardcoded
        "disabled" default, or a returning user with EDDN already enabled
        sees ENABLED contradicted by "EDDN network access is disabled."
        until an unrelated status update happens to overwrite it.
        """
        return (
            "EDDN enabled from saved settings."
            if consent else "EDDN network access is disabled."
        )

    @Slot(bool, bool, bool)
    def saveEddnConfig(self, consent, upload_enabled, listener_enabled):
        consent = bool(consent)
        self._eddn_config.update({
            "consent": consent,
            "upload_enabled": consent and bool(upload_enabled),
            "listener_enabled": consent and bool(listener_enabled),
        })
        if (
            self._eddn_config["upload_enabled"]
            and not self._eddn_baseline_established
        ):
            self._baseline_eddn_journal_files()
            for filename in ("Market.json", "Outfitting.json", "Shipyard.json"):
                path = journal_dir() / filename
                try:
                    self._station_fingerprints[filename] = (
                        f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
                    )
                except OSError:
                    pass
            self._save_eddn_cursor()
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self._eddn_status = (
            "EDDN settings saved. New supported Journal events will be queued."
            if consent else "EDDN network access is disabled."
        )
        self._ensure_eddn_listener()
        self.connectionChanged.emit()

    def _eddn_profile_journal_paths(self) -> list[Path]:
        identity = str(self._eddn_profile_identity or "")
        if not identity:
            return []
        signature = (identity, journal_change_signature())
        if signature != self._eddn_profile_paths_signature:
            self._eddn_profile_paths_cache = journal_paths_for_profile(identity)
            self._eddn_profile_paths_signature = signature
        return list(self._eddn_profile_paths_cache)

    def _rebuild_eddn_context(self) -> dict:
        # State projection already maintains an incremental, profile-isolated
        # Journal cache. Reuse it instead of reparsing every Journal file.
        events = profiled_journal_events()
        context = rebuild_eddn_context(events, self._eddn_profile_identity)
        if self._eddn_profile_identity and not context.get("StarSystem"):
            LOGGER.warning(
                "EDDN context replay found no current system for profile %s",
                self._eddn_profile_key,
            )
        return context

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

    def _sync_eddn_profile(self) -> bool:
        return self._switch_profile_context(resolve_profile_context())

    def _save_eddn_cursor(self):
        cursor = dict(self._journal_offsets)
        cursor["__station_files__"] = dict(self._station_fingerprints)
        cursor["__navroute_file__"] = self._navroute_fingerprint
        cursor["__baseline_established__"] = self._eddn_baseline_established
        cursor["__journal_root__"] = self._eddn_journal_root
        atomic_write(self.eddn_cursor_file, json.dumps(cursor, indent=2))

    def _baseline_eddn_journal_files(self):
        """Start opt-in after existing Journal bytes; rotations start at zero."""
        for path in self._eddn_profile_journal_paths():
            try:
                self._journal_offsets[path.name] = path.stat().st_size
            except OSError:
                LOGGER.warning("EDDN could not initialize cursor for %s", path)
        self._eddn_baseline_established = True

    def _enqueue_eddn(self, prepared):
        try:
            validate_eddn_prepared(prepared)
        except EddnError as exc:
            self._eddn_status = str(exc)
            LOGGER.warning("EDDN message rejected before queueing: %s", exc)
            self.connectionChanged.emit()
            return False
        digest = hashlib.sha256(json.dumps(
            prepared, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        job_id = f"EDDN-{digest}"
        scan_ids = getattr(self, "_eddn_scan_job_ids", None)
        if (
            job_id in scan_ids if isinstance(scan_ids, set) else
            any(job.get("id") == job_id for job in self._eddn_queue)
        ):
            return
        scan_pending = getattr(self, "_eddn_scan_pending_count", None)
        pending_count = int(scan_pending) if isinstance(scan_pending, int) else sum(
            row.get("status") != "sent" for row in self._eddn_queue
            if isinstance(row, dict)
        )
        if pending_count >= EDDN_PENDING_JOB_LIMIT:
            if (
                getattr(self, "_eddn_batching_scan", False)
                and getattr(self, "_eddn_scan_queue_full_reported", False)
            ):
                return False
            self._eddn_scan_queue_full_reported = True
            self._eddn_status = (
                f"EDDN offline queue full ({EDDN_PENDING_JOB_LIMIT}); "
                "this event was not queued. Upload or clear reviewed failures "
                "before further community events can be retained."
            )
            LOGGER.error(self._eddn_status)
            self.connectionChanged.emit()
            return False
        self._eddn_queue.append({
            "id": job_id, "target": "EDDN", "event": prepared,
            "context": dict(self._eddn_context), "attempts": 0,
            "profile_key": self._eddn_profile_key,
            "status": "queued",
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        if isinstance(scan_ids, set):
            scan_ids.add(job_id)
            self._eddn_scan_pending_count = pending_count + 1
        if not getattr(self, "_eddn_batching_scan", False):
            self._save_eddn()
            self._publish_eddn_delivery_change()
            self.connectionChanged.emit()
        return True

    def _scan_eddn_journal(self):
        if not self._sync_eddn_profile():
            return
        if not eddn_upload_allowed(self._eddn_config):
            return
        if not self._eddn_profile_identity:
            LOGGER.warning("EDDN upload skipped: no active Commander FID")
            return
        if not self._eddn_baseline_established:
            self._baseline_eddn_journal_files()
            self._save_eddn_cursor()
            self._scan_eddn_station_files()
            return
        journal_signature = journal_change_signature()
        monitored_names = {
            row[0] for row in journal_signature[1]
            if isinstance(row, tuple) and row
        }
        paths = [
            path for path in self._eddn_profile_journal_paths()
            if path.name in monitored_names
        ]
        changed = False
        queue_changed = False
        offsets_before_scan = dict(self._journal_offsets)
        navroute_fingerprint_before_scan = self._navroute_fingerprint
        self._eddn_batching_scan = True
        self._eddn_scan_queue_full_reported = False
        scan_queue = getattr(self, "_eddn_queue", [])
        self._eddn_scan_job_ids = {
            str(job.get("id") or "") for job in scan_queue
            if isinstance(job, dict)
        }
        self._eddn_scan_pending_count = sum(
            job.get("status") != "sent" for job in scan_queue
            if isinstance(job, dict)
        )
        saturated = False
        for path in paths:
            if saturated:
                break
            try:
                size = path.stat().st_size
                # Files appearing after the opt-in baseline are Journal
                # rotations and must be consumed from their first byte.
                offset = int(self._journal_offsets.get(path.name, 0))
                if offset > size:
                    offset = 0
                _tail_committed, records = read_journal_tail_records(path, offset)
            except OSError:
                LOGGER.warning("EDDN Journal read failed for %s", path)
                continue
            committed = offset
            for line_start, line_end, event in records:
                if event is None:
                    committed = line_end
                    continue
                self._eddn_context = update_eddn_context(
                    self._eddn_context, event
                )
                navroute_fingerprint = ""
                navroute_reason = ""
                if event.get("event") == "NavRoute":
                    event, navroute_fingerprint, navroute_reason = (
                        load_navroute_source(
                            event, path.parent / "NavRoute.json"
                        )
                    )
                    if (
                        not navroute_reason
                        and navroute_fingerprint == self._navroute_fingerprint
                    ):
                        LOGGER.debug(
                            "EDDN ignored unchanged NavRoute.json revision"
                        )
                        committed = line_end
                        continue
                prepared = prepare_eddn_event(event, self._eddn_context)
                if prepared:
                    if int(self._eddn_scan_pending_count or 0) >= EDDN_PENDING_JOB_LIMIT:
                        self._eddn_status = (
                            f"EDDN offline queue full ({EDDN_PENDING_JOB_LIMIT}); "
                            "Journal cursor paused before the next unsaved event."
                        )
                        if not self._eddn_scan_queue_full_reported:
                            LOGGER.warning(self._eddn_status)
                            self._eddn_scan_queue_full_reported = True
                        self.connectionChanged.emit()
                        committed = line_start
                        saturated = True
                        break
                    queued = self._enqueue_eddn(prepared)
                    queue_changed = queued is True or queue_changed
                    if navroute_fingerprint:
                        self._navroute_fingerprint = navroute_fingerprint
                        self._navroute_rejections.pop("NavRoute.json", None)
                elif supports_eddn_event(event):
                    reason = navroute_reason
                    if not reason and event.get("event") == "NavRoute":
                        reason = navroute_rejection_reason(event)
                    reason = reason or "schema requirements or public context were not satisfied"
                    self._record_eddn_not_shareable(event.get("event"), reason)
                    if event.get("event") != "NavRoute" or should_log_rejection(
                        self._navroute_rejections, "NavRoute.json",
                        navroute_fingerprint or "journal", reason,
                    ):
                        LOGGER.warning(
                            "EDDN dropped supported event %s: %s",
                            event.get("event"), reason,
                        )
                else:
                    LOGGER.debug(
                        "EDDN intentionally ignores unsupported event %s",
                        event.get("event"),
                    )
                committed = line_end
            self._journal_offsets[path.name] = committed
            changed = changed or committed != offset
        self._eddn_batching_scan = False
        self._eddn_scan_job_ids = None
        self._eddn_scan_pending_count = None
        self._eddn_scan_queue_full_reported = False
        queue_save_required = queue_changed or bool(
            getattr(self, "_eddn_queue_persist_pending", False)
        )
        queue_saved = True
        if queue_save_required:
            queue_saved = self._save_eddn()
            self._publish_eddn_delivery_change()
            self.connectionChanged.emit()
        if not queue_saved:
            self._journal_offsets = offsets_before_scan
            self._navroute_fingerprint = navroute_fingerprint_before_scan
            return
        if changed:
            self._save_eddn_cursor()
        if not saturated:
            self._scan_eddn_station_files()

    def _scan_eddn_station_files(self):
        directory = journal_dir()
        changed = False
        for kind, filename in (
            ("market", "Market.json"),
            ("outfitting", "Outfitting.json"),
            ("shipyard", "Shipyard.json"),
        ):
            path = directory / filename
            try:
                stat = path.stat()
                fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
                if self._station_fingerprints.get(filename) == fingerprint:
                    continue
                snapshot = json.loads(path.read_text(
                    encoding="utf-8-sig", errors="strict"
                ))
            except (OSError, UnicodeError, ValueError, TypeError):
                LOGGER.warning("EDDN station snapshot could not be read: %s", path)
                continue
            prepared = prepare_station_snapshot(
                kind, snapshot, self._eddn_context
            )
            if not prepared:
                reason = station_snapshot_mismatch_reason(
                    kind, snapshot, self._eddn_context
                )
                if should_log_station_rejection(
                    self._station_rejections, filename, fingerprint, reason,
                ):
                    LOGGER.warning(
                        "EDDN deferred %s snapshot for active profile: %s",
                        kind, reason,
                    )
                continue
            queued = self._enqueue_eddn(prepared)
            if queued is not True:
                continue
            self._station_fingerprints[filename] = fingerprint
            self._station_rejections.pop(filename, None)
            changed = True
        if changed:
            self._save_eddn_cursor()
            self.connectionChanged.emit()

    def _process_eddn_queue(self):
        if not self._sync_eddn_profile():
            return
        if self._eddn_busy or not eddn_upload_allowed(self._eddn_config):
            return
        now = time.time()
        job = next((
            row for row in self._eddn_queue
            if row.get("status") in {"queued", "retry"}
            and float(row.get("next_retry_at", 0) or 0) <= now
        ), None)
        if not job:
            return
        if job.get("profile_key") not in {None, "", self._eddn_profile_key}:
            job.update({
                "status": "failed",
                "terminal_error": True,
                "last_error": "EDDN profile isolation rejected a foreign job.",
            })
            LOGGER.error(
                "EDDN refused queue job %s from profile %s while %s is active",
                job.get("id"), job.get("profile_key"), self._eddn_profile_key,
            )
            self._save_eddn()
            return
        job.setdefault("profile_key", self._eddn_profile_key)
        job["status"] = "sending"
        job["attempts"] = int(job.get("attempts", 0) or 0) + 1
        self._eddn_busy = True
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self.connectionChanged.emit()
        job_id = str(job["id"])
        prepared = deepcopy(job["event"])
        context = deepcopy(job.get("context") or {})
        uploader_id = str(self._eddn_config["uploader_id"])

        def worker():
            try:
                receipt = send_eddn_event(
                    prepared, context, uploader_id
                )
                self.eddnFinished.emit(job_id, True, json.dumps(receipt))
            except EddnError as exc:
                self.eddnFinished.emit(job_id, False, json.dumps({
                    "message": str(exc), "terminal": exc.terminal,
                    "statusCode": exc.status_code,
                    "retryAfter": exc.retry_after,
                }))
            except Exception as exc:
                self.eddnFinished.emit(job_id, False, json.dumps({
                    "message": f"Local EDDN error: {type(exc).__name__}",
                    "terminal": False,
                }))

        self._start_network_worker(worker, "eddn-upload")

    @Slot(str, bool, str)
    def _finish_eddn(self, job_id, success, message):
        self._eddn_busy = False
        job = next(
            (row for row in self._eddn_queue if row.get("id") == job_id),
            None,
        )
        if not job:
            return
        result = json.loads(message)
        if success:
            sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            job.update({
                "status": "sent", "receipt": result,
                "sent_at": sent_at,
                "last_result": (
                    f"Gateway accepted HTTP {result.get('httpStatus')}"
                ),
            })
            # Persist the gateway acceptance before status/proof/UI work. A
            # process exit after this checkpoint will not replay the job.
            self._save_eddn()
            self._eddn_status = (
                f"{result.get('event')} accepted · HTTP "
                f"{result.get('httpStatus')} · {result.get('elapsedMs')} ms"
            )
            prepared = job.get("event") if isinstance(job.get("event"), dict) else {}
            public_message = prepared.get("message") if isinstance(prepared.get("message"), dict) else {}
            proof = {
                "sentAt": sent_at,
                "schema": str(prepared.get("schema") or ""),
                "eventName": str(public_message.get("event") or ""),
                "stationName": str(public_message.get("stationName") or ""),
                "timestamp": str(public_message.get("timestamp") or ""),
                "result": job["last_result"],
            }
            self._eddn_config["last_success"] = proof
            self._eddn_config["last_not_shareable"] = ""
            self._eddn_config["last_not_shareable_at"] = ""
            if proof["schema"] in {
                "commodity/3", "outfitting/2", "outfitting/3", "shipyard/2",
            }:
                receipts = self._eddn_config.setdefault("station_receipts", {})
                if isinstance(receipts, dict):
                    receipts[proof["schema"]] = proof
        else:
            job["last_error"] = str(result.get("message") or "Upload failed")
            terminal = bool(result.get("terminal"))
            if (
                not terminal and self._eddn_config.get("retry_failed", True)
                and int(job.get("attempts", 0)) < 7
            ):
                try:
                    status_code = int(result.get("statusCode"))
                except (TypeError, ValueError):
                    status_code = None
                try:
                    retry_after = max(0, int(result.get("retryAfter")))
                except (TypeError, ValueError):
                    retry_after = None
                delay = (
                    retry_after
                    if status_code == 429 and retry_after is not None
                    else max(
                        60,
                        min(900, 60 * (2 ** max(0, job["attempts"] - 1))),
                    )
                )
                job.update({
                    "status": "retry", "next_retry_at": time.time() + delay,
                    "next_retry_seconds": delay,
                })
                retry_state = f"RETRY SCHEDULED · attempt {job['attempts']}/7 · in {delay} s"
            else:
                job["status"] = "failed"
                job["terminal_error"] = terminal
                retry_state = (
                    "STOPPED · schema/send rejection requires review"
                    if terminal else "STUCK · automatic retries exhausted"
                )
            last_success = self._eddn_delivery_summary()["lastSuccessAt"]
            self._eddn_status = (
                f"FAILED · {job['last_error']} · {retry_state} · LAST ACCEPTED · "
                f"{last_success or 'no gateway receipt recorded'}"
            )
            LOGGER.warning("EDDN upload failed for %s: %s", job_id, job["last_error"])
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self.connectionChanged.emit()
        if success or job.get("status") == "failed":
            QTimer.singleShot(EDDN_REPLAY_DELAY_MS, self._process_eddn_queue)

    @Slot()
    def retryEddnFailed(self):
        retried = 0
        for job in self._eddn_queue:
            if self._eddn_job_retryable(job):
                job.update({"status": "retry", "attempts": 0})
                job.pop("next_retry_at", None)
                job.pop("next_retry_seconds", None)
                retried += 1
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self._eddn_status = (
            f"{retried} valid EDDN job(s) queued for retry."
            if retried else "No valid non-terminal EDDN job is eligible for retry."
        )
        self.connectionChanged.emit()

    @Slot(str)
    def retryEddnJob(self, job_id):
        job = next((
            row for row in self._eddn_queue
            if str(row.get("id") or "") == str(job_id or "")
        ), None)
        if not self._eddn_job_retryable(job):
            self._eddn_status = "This EDDN job cannot be retried safely."
            self.connectionChanged.emit()
            return
        job.update({"status": "retry", "attempts": 0})
        job.pop("next_retry_at", None)
        job.pop("next_retry_seconds", None)
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self._eddn_status = "Validated EDDN job queued for retry."
        self.connectionChanged.emit()

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
    def clearEddnSent(self):
        proof = latest_delivery_proof(self._eddn_queue)
        if proof:
            self._eddn_config["last_success"] = proof
        self._eddn_queue = [
            row for row in self._eddn_queue if row.get("status") != "sent"
        ]
        try:
            self._history_archive.clear("eddn_sent")
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("EDDN sent history could not be cleared: %s", exc)
        self._save_eddn()
        self._publish_eddn_delivery_change()
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

    @Slot(object)
    def _accept_eddn_relay(self, payload):
        snapshot = extract_system_bgs_snapshot(payload)
        if snapshot:
            self._pending_bgs_snapshots.append(snapshot)
        self._pending_hge_observations.extend(extract_signal_finds(payload))
        self._pending_mining_candidates.extend(
            project_eddn_mining_candidates(
                payload, datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        )

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

    def _ensure_eddn_listener(self):
        enabled = bool(
            self._eddn_config.get("consent")
            and self._eddn_config.get("listener_enabled")
        )
        if not enabled:
            self._eddn_stop.set()
            self._eddn_listener_status = "Disabled"
            return
        if self._eddn_thread and self._eddn_thread.is_alive():
            return
        self._eddn_stop.clear()
        self._eddn_listener_status = "Connecting…"

        def listener():
            try:
                import zmq
                context = zmq.Context()
                socket = context.socket(zmq.SUB)
                socket.setsockopt(zmq.SUBSCRIBE, b"")
                socket.setsockopt(zmq.RCVTIMEO, 1000)
                socket.connect(EDDN_RELAY_URL)
                self._eddn_listener_status = "Connected"
                self.connectionChanged.emit()
                while not self._eddn_stop.is_set():
                    try:
                        payload = decode_relay_frame(socket.recv())
                        if _eddn_relay_relevant(payload):
                            self.eddnRelay.emit(payload)
                    except zmq.Again:
                        continue
                    except EddnRelayDecodeError:
                        self._eddn_listener_status = (
                            "Connected · ignored malformed relay frame"
                        )
                        self.connectionChanged.emit()
                        continue
                socket.close(0)
                context.term()
            except ImportError:
                self._eddn_listener_status = "pyzmq is not installed"
                self.connectionChanged.emit()
            except Exception as exc:
                self._eddn_listener_status = (
                    f"Disconnected: {type(exc).__name__}"
                )
                self.connectionChanged.emit()

        self._eddn_thread = threading.Thread(
            target=listener, daemon=True, name="eddn-hge-listener"
        )
        self._eddn_thread.start()

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

    def _record_commander_credit_snapshot(self, credits):
        credits = credits if isinstance(credits, dict) else {}
        value = credits.get("value")
        timestamp = str(credits.get("timestamp") or "")
        source = {
            "LIVE STATUS": "live_balance",
            "FRONTIER CAPI": "frontier_capi",
        }.get(str(credits.get("basis") or ""))
        if not source or not credits.get("known") or not timestamp \
                or not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        snapshot = {
            "observedAt": timestamp,
            "timestamp": timestamp,
            "credits": max(0, int(value)),
            "source": source,
        }
        snapshots = list(getattr(self, "_commander_credit_snapshots", []))
        if snapshots and all(
            snapshots[-1].get(field) == snapshot[field]
            for field in ("timestamp", "credits")
        ):
            return False
        snapshots.append(snapshot)
        self._commander_credit_snapshots = snapshots
        self._archive_history("commander_credit_snapshots", [snapshot])
        return True

    def _poll_commander_status_credits(self):
        """Apply Balance changes without rebuilding the complete Journal state."""
        path = journal_dir() / "Status.json"
        try:
            stat = path.stat()
            stamp = (int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            return
        if stamp == getattr(self, "_last_commander_status_stamp", None):
            return
        first_status_poll = getattr(
            self, "_last_commander_status_stamp", None
        ) is None
        live = commander_status_credits(read_json(path, {}))
        if not live.get("known"):
            return
        self._last_commander_status_stamp = stamp
        overview = self._state.get("commanderOverview", {})
        overview = dict(overview) if isinstance(overview, dict) else {}
        previous = overview.get("credits", {})
        previous = previous if isinstance(previous, dict) else {}
        # Status.json is rewritten for many cockpit changes.  Only a real
        # balance change should invalidate CMDR cards and the chart.
        unchanged = (
            previous.get("known")
            and previous.get("value") == live.get("value")
        )
        previous_time = normalize_timestamp(previous.get("timestamp"))
        live_time = normalize_timestamp(live.get("timestamp"))
        if previous_time is not None and live_time is not None \
                and live_time < previous_time:
            return
        if first_status_poll or not unchanged:
            self._record_commander_credit_snapshot(live)
        if unchanged:
            return
        overview["credits"] = live
        if live.get("timestamp"):
            overview["lastUpdated"] = max(
                str(overview.get("lastUpdated") or ""), live["timestamp"]
            )
        self._state = {**self._state, "commanderOverview": overview}
        self._state_revision += 1
        self._derived_cache.clear()
        self.stateChanged.emit()

    def _record_exobiology_step_positions(self, previous_findings, new_findings):
        """Snapshot where the Commander is standing the moment a scan step
        lands, so the next poll can tell how far they still need to move.

        Best-effort and in-memory only - see the note on
        ``self._exobiology_step_positions`` in ``__init__``.
        """
        if not isinstance(new_findings, list):
            return
        status = read_json(journal_dir() / "Status.json", {})
        lat, lon = status.get("Latitude"), status.get("Longitude")
        radius = status.get("PlanetRadius")
        body_name = status.get("BodyName")
        if (
            not body_name
            or not isinstance(lat, (int, float))
            or not isinstance(lon, (int, float))
            or not isinstance(radius, (int, float))
        ):
            return
        previous_by_key = {
            (row.get("systemAddress"), row.get("body"), row.get("genus"), row.get("species")): row
            for row in previous_findings or [] if isinstance(row, dict)
        }
        for row in new_findings:
            if not isinstance(row, dict) or row.get("complete"):
                continue
            key = (row.get("systemAddress"), row.get("body"), row.get("genus"), row.get("species"))
            previous_row = previous_by_key.get(key)
            previous_done = previous_row.get("samplesDone") if previous_row else 0
            if row.get("samplesDone", 0) > (previous_done or 0):
                self._exobiology_step_positions[key] = {
                    "lat": float(lat), "lon": float(lon),
                    "radius": float(radius), "bodyName": str(body_name),
                }

    def _new_current_system_exobiology_target(self, previous, state):
        """A short activity message the instant a fresh, unclaimed
        biological signal appears in the Commander's current system - so
        they do not have to keep tabbing to Exobiology after every FSS
        honk to notice one. Only ever for a target brand new since the
        last published state; never for career-wide leads elsewhere.
        """
        new_targets = state.get("exobiologyLandingTargets")
        if not isinstance(new_targets, list):
            return ""
        old_keys = {
            (row.get("systemAddress"), row.get("bodyId"))
            for row in previous.get("exobiologyLandingTargets") or []
            if isinstance(row, dict) and row.get("inCurrentSystem")
        }
        for row in new_targets:
            if not isinstance(row, dict) or not row.get("inCurrentSystem"):
                continue
            key = (row.get("systemAddress"), row.get("bodyId"))
            if key in old_keys:
                continue
            return (
                f"EXOBIOLOGY · {int(row.get('signalCount') or 0)} biological "
                f"signal(s) detected on {row.get('bodyName') or 'a nearby body'}"
            )
        return ""

    def _poll_exobiology_distance_check(self):
        """Refresh the live "distance to next sample" check every tick.

        Unlike the credits poll, a real position change must be reflected
        immediately while the Commander is walking - Status.json's own
        stat signature changes on essentially every heartbeat regardless,
        so this recomputes unconditionally and only emits when the
        resulting, small dict actually differs from what QML already has.
        """
        status = read_json(journal_dir() / "Status.json", {})
        # self._state["currentSystemAddress"] is only ever set transiently
        # by state_with_live_location() and gets wiped by the very next
        # full refresh, which never carries it - it is essentially always
        # stale here. latest_profile_location() derives it fresh from the
        # Journal every time, the same way build_state() does for
        # landing_targets() itself.
        current_system_address = latest_profile_location().get("currentSystemAddress")
        value = exobiology_distance_check(
            self._state.get("exobiologyFindings"),
            self._exobiology_step_positions,
            self._exobiology_species_catalog,
            self._exobiology_colony_ranges,
            current_system_address,
            status,
        )
        if value == self._exobiology_distance_check_value:
            return
        self._exobiology_distance_check_value = value
        self.exobiologyDistanceCheckChanged.emit()

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
    def setCommanderUpdatePopups(self, enabled):
        self._commander_update_popups = bool(enabled)
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
    def setCommanderCardOrder(self, order):
        order = list(dict.fromkeys(
            str(card) for card in list(order or [])
            if str(card) in COMMANDER_CARD_IDS
        ))
        order.extend(card for card in COMMANDER_CARD_IDS if card not in order)
        if order != self._commander_card_order:
            previous = self._commander_card_order
            self._commander_card_order = order
            if not self._save_ui_config():
                self._commander_card_order = previous
            self.uiChanged.emit()

    @Slot(str)
    def setCommanderFinancePeriod(self, period):
        period = str(period or "").casefold()
        if period not in {"session", "1h", "6h", "24h", "7d", "30d", "all"}:
            return
        if period != self._commander_finance_period:
            self._commander_finance_period = period
            self.commanderCardsChanged.emit()

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
