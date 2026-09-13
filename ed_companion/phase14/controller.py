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

from .controller_core import CoreControllerMixin, LEGACY_THEME_IDS, THEME_IDS

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
from .controller_eddn import EddnMixin, _eddn_relay_relevant
from .controller_journal_health import JournalHealthMixin, _last_complete_json_record
from .controller_ui_settings import UiSettingsMixin
from .controller_navigation import NavigationMixin
from .controller_fleet_materials import FleetMaterialsMixin
from .controller_engineering import EngineeringMixin
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

LOGGER = logging.getLogger(__name__)

# Upper bound on how long the Frontier CAPI tab may sit in its busy state
# before the UI is released, in case a worker never reports back. Chosen
# above the worst realistic single request: a 60 s CAPI rate-limit wait plus
# the 25 s transport timeout, with margin.
FRONTIER_REQUEST_WATCHDOG_MS = 120_000


class CockpitController(
    CommanderMixin, EddnMixin, EngineeringMixin, ExobiologyMixin,
    FleetMaterialsMixin, FrontierCapiMixin, InaraMixin, JournalHealthMixin,
    LogbookMixin, NavigationMixin, UiSettingsMixin, CoreControllerMixin,
    QObject,
):
    diagnosticsChanged = Signal()
    activityChanged = Signal()
    historyExportFinished = Signal(object)
    refreshStateReady = Signal(object)
    refreshStateFailed = Signal(object)
    exitRequested = Signal()

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


    def _get(self, key, default=None):
        return self._state.get(key, default)




    @staticmethod
    def _ship_asset_key(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())




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
    def _invalidate_operations_cache(self) -> None:
        self._drop_derived({"engineer_mission_route", "operation_action"})

    def _drop_derived(self, names: set[str]) -> None:
        for key in list(self._derived_cache):
            if key[0] in names:
                self._derived_cache.pop(key, None)










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
    completionReliable = Property(
        bool, lambda self: bool(self._get("completionReliable", False)),
        notify=CoreControllerMixin.stateChanged,
    )
    covered = Property(int, lambda self: int(self._get("covered", 0)), notify=CoreControllerMixin.stateChanged)
    required = Property(int, lambda self: int(self._get("required", 0)), notify=CoreControllerMixin.stateChanged)
    calculationWarning = Property(
        str, lambda self: str(self._get("calculationWarning", "")),
        notify=CoreControllerMixin.stateChanged,
    )
    missingKinds = Property(int, lambda self: int(self._get("missingKinds", 0)), notify=CoreControllerMixin.stateChanged)
    trades = Property("QVariantList", lambda self: self._get("trades", []), notify=CoreControllerMixin.materialsChanged)
    tradeHistory = Property(
        "QVariantList", lambda self: self._get("tradeHistory", []),
        notify=CoreControllerMixin.materialsChanged,
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
    materials = Property("QVariantList", lambda self: self._get("materials", []), notify=CoreControllerMixin.materialsChanged)
    missingMaterials = Property(
        "QVariantList",
        lambda self: [
            row for row in self._get("materials", [])
            if int(row.get("missing", 0) or 0) > 0
        ],
        notify=CoreControllerMixin.materialsChanged,
    )
    # float, not int: a career total can exceed the 32-bit range a plain
    # int Property would truncate to; QML's Number already handles this
    # exactly up to far more than any realistic credit balance.
    engineers = Property(
        "QVariantList", lambda self: self._engineer_index(),
        notify=CoreControllerMixin.operationsChanged,
    )
    trackedItems = Property(
        "QVariantList", lambda self: self._get("trackedItems", []),
        notify=CoreControllerMixin.operationsChanged,
    )
    activity = Property(str, lambda self: self._activity, notify=activityChanged)
    lastPage = Property(int, lambda self: self._last_page, notify=CoreControllerMixin.uiChanged)
    systemTrayAvailable = Property(
        bool, lambda self: self._system_tray_available, notify=CoreControllerMixin.uiChanged,
    )
    historyExportBusy = Property(
        bool, lambda self: self._history_export_busy,
        notify=CoreControllerMixin.connectionChanged,
    )
    stateFindRefreshStatus = Property(
        str, lambda self: self._state_find_refresh_status,
        notify=CoreControllerMixin.hgeChanged,
    )
    stateFindCacheSummary = Property(
        "QVariantMap", lambda self: self._state_find_cache_summary(),
        notify=CoreControllerMixin.hgeChanged,
    )
    stateFindRefreshSummary = Property(
        "QVariantMap", lambda self: dict(self._last_state_find_refresh_stats),
        notify=CoreControllerMixin.hgeChanged,
    )
    stateFindTypeFilters = Property(
        "QStringList",
        lambda self: [
            "ALL FIND TYPES", "HGE", "CONFLICT_ZONE",
            "SEEKING_MEDS", "SEEKING_FOODS",
        ],
        notify=CoreControllerMixin.hgeChanged,
    )
    stateFindStateFilters = Property(
        "QStringList",
        lambda self: self._state_find_filter_values("stateValues", "ALL STATES"),
        notify=CoreControllerMixin.hgeChanged,
    )
    stateFindAllegianceFilters = Property(
        "QStringList",
        lambda self: self._state_find_filter_values(
            "allegianceValues", "ALL ALLEGIANCES"
        ),
        notify=CoreControllerMixin.hgeChanged,
    )
    serviceStatus = Property(
        "QVariantList", lambda self: self._service_status(),
        notify=CoreControllerMixin.connectionChanged,
    )
    diagnosticLogs = Property(
        "QStringList", lambda self: self._diagnostic_logs(),
        notify=diagnosticsChanged,
    )
    crashReports = Property(
        "QVariantList", lambda self: self._crash_reports(),
        notify=diagnosticsChanged,
    )
    dataPath = Property(
        str,
        lambda self: str(self.package_root / "ed_data"),
        constant=True,
    )
    appVersion = Property(
        str, lambda self: APP_VERSION, constant=True
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





    @Slot(bool)
    def setSystemTrayAvailable(self, available):
        self._system_tray_available = bool(available)
        self.uiChanged.emit()















    @Slot(str, str, result=str)
    def translate(self, key, fallback=""):
        return self._translations.translate(
            self._interface_language, key, fallback
        )





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

