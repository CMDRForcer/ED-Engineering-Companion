import json
import hashlib
import logging
import math
import os
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


from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow

from ed_companion import APP_VERSION
from ed_companion.i18n import (
    DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, TranslationCatalog,
)
from ed_companion.persistence import atomic_write
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
    EDDN_RELAY_URL,
    EddnError,
    EddnRelayDecodeError,
    decode_relay_frame,
    load_navroute_source,
    navroute_rejection_reason,
    prepare_event as prepare_eddn_event,
    prepare_station_snapshot,
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
    compact_hge_observations,
    extract_signal_finds,
    extract_system_bgs_snapshot,
    infer_hge_materials,
    hge_match_class,
    is_hge_route_relevant,
    is_hge_material,
    merge_hge_observation_batch,
    purge_legacy_signal_classifications,
    readable_faction_state,
    recent_unverified_hge_summary,
    rank_all_hge_sightings,
    rank_hge_candidate_systems,
    rank_hge_sightings,
    rank_state_find_systems,
)

HGE_OBSERVATION_LIMIT = 10000
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
    "state-finds", "cmdr", "logbook", "settings", "powerplay",
)


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
    compact_upload_queue,
    normalize_upload_queue,
)
from ed_companion.diagnostics import filtered_log_lines

from .dashboard_views import (
    build_commander_cards,
    build_logbook_view,
    decorate_logbook_entry,
)

from .state import (
    active_profile_identity,
    assign_plans_to_nearest_engineers,
    blueprint_catalog,
    build_engineering_plan,
    build_experimental_plan,
    build_state,
    dismiss_craft_tracking_issue,
    dismiss_historical_craft_tracking_issues,
    dismiss_selected_craft_tracking_issues,
    duplicate_ship_plan,
    journal_dir,
    journal_change_signature,
    journal_craft_baseline,
    journal_paths_for_profile,
    latest_profile_location,
    latest_loadout_slots,
    profiled_journal_events,
    LOGBOOK_FILTERS,
    load_logbook_notes,
    logbook_entries,
    normalize,
    move_ship_plan,
    module_matches_type,
    planner_mode,
    real_engineers,
    read_json,
    remove_ship_task,
    replace_ship_plan,
    runtime_data_dir,
    set_journal_dir,
    reference_data_dir,
    select_operation_action,
    set_prioritized_ship_plan,
    set_tech_broker_track,
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


class CockpitController(QObject):
    stateChanged = Signal()
    materialsChanged = Signal()
    fleetChanged = Signal()
    wishlistChanged = Signal()
    operationsChanged = Signal()
    hgeChanged = Signal()
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
    inaraFinished = Signal(str, bool, str, object)
    eddnFinished = Signal(str, bool, str)
    eddnRelay = Signal(object)
    traderSyncFinished = Signal(bool, str)
    techBrokerSyncFinished = Signal(bool, str)
    startupStateReady = Signal(object)
    startupStateFailed = Signal(str)
    refreshStateReady = Signal(object)
    refreshStateFailed = Signal(object)
    exitRequested = Signal()
    restartRequested = Signal()

    def __init__(self):
        super().__init__()
        self.package_root = Path(__file__).resolve().parents[2]
        self.config_dir = runtime_data_dir(self.package_root)
        self.config_file = self.config_dir / "phase14_graphics.json"
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
        self._last_page = max(0, min(11, int(ui_config.get("last_page", 0) or 0)))
        configured_cards = ui_config.get("commander_card_order", [])
        configured_cards = configured_cards if isinstance(configured_cards, list) else []
        self._commander_card_order = list(dict.fromkeys(
            card for card in configured_cards if card in COMMANDER_CARD_IDS
        ))
        self._commander_card_order.extend(
            card for card in COMMANDER_CARD_IDS
            if card not in self._commander_card_order
        )
        configured_navigation = ui_config.get("navigation_order", [])
        configured_navigation = (
            configured_navigation if isinstance(configured_navigation, list) else []
        )
        self._navigation_order = list(dict.fromkeys(
            item for item in configured_navigation if item in NAVIGATION_IDS
        ))
        self._navigation_order.extend(
            item for item in NAVIGATION_IDS if item not in self._navigation_order
        )
        self._renderer_active = self._detect_renderer()
        self._restart_required = False
        self._state = {}
        self._state_revision = 0
        self._hge_revision = 0
        self._eddn_revision = 0
        self._connection_revision = 0
        self._derived_cache = {}
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
        self.inara_config_file = self.config_dir / "inara_config.json"
        self.inara_receipts_file = self.config_dir / "inara_receipts.json"
        self.inara_journal_cache_file = (
            self.config_dir / "inara_journal_cache.json"
        )
        self._inara_config = self._load_inara_config()
        journal_identity, journal_commander = active_profile_identity()
        if journal_commander:
            self._inara_config["commander_name"] = journal_commander
        if journal_identity.upper().startswith("F"):
            self._inara_config["frontier_id"] = journal_identity
        self._save_inara_config()
        self._inara_status = "Ready. No network request has been made."
        self._inara_busy = False
        self._inara_pending_since = 0.0
        self._inara_request_times = []
        self._inara_last_request_at = 0.0
        self._inara_retry_not_before = 0.0
        self._inara_failure_count = 0
        self._inara_pending_events = []
        self._inara_pending_fingerprints = []
        self._inara_inflight_fingerprints = []
        self._inara_material_fingerprint = ""
        self._inara_cache = self._read_local_json(
            self.inara_journal_cache_file, {}
        )
        if not isinstance(self._inara_cache, dict):
            self._inara_cache = {}
        if self._inara_cache.get("journal_root") != str(journal_dir().resolve()):
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
        self.inaraFinished.connect(self._finish_inara)
        self.eddn_config_file = self.config_dir / "eddn_config.json"
        self.eddn_queue_file = self.config_dir / "community_upload_queue.json"
        self.eddn_cursor_file = self.config_dir / "eddn_journal_cursor.json"
        self._eddn_profile_identity = self._detected_eddn_identity()
        self._eddn_profile_key = self._profile_key(
            self._eddn_profile_identity
        )
        self._eddn_journal_root = str(journal_dir().resolve())
        self.hge_cache_file = self.config_dir / "hge_live_sightings.json"
        self.trader_catalog_file = (
            self.config_dir / "material_trader_catalog_user.json"
        )
        self.tech_broker_catalog_file = (
            self.config_dir / "tech_broker_catalog_user.json"
        )
        self._eddn_config = self._load_eddn_config()
        self._eddn_queue = normalize_upload_queue(
            self._read_local_json(self.eddn_queue_file, [])
        )
        self._hge_sightings = self._read_local_json(self.hge_cache_file, [])
        if not isinstance(self._hge_sightings, list):
            self._hge_sightings = []
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
            self._hge_sightings, _removed = purge_legacy_signal_classifications(
                self._hge_sightings
            )
            self._eddn_config["hge_classifier_version"] = HGE_CLASSIFIER_VERSION
            self._save_hge_cache()
            self._save_eddn()
        self._hge_candidate_cache_key = None
        self._hge_candidate_cache_rows = []
        self._hge_material_filter_cache = ["ALL HGE MATERIALS"]
        self._eddn_context = {}
        self._journal_offsets = self._read_local_json(
            self.eddn_cursor_file, {}
        )
        if not isinstance(self._journal_offsets, dict):
            self._journal_offsets = {}
        self._station_fingerprints = self._journal_offsets.pop(
            "__station_files__", {}
        )
        self._navroute_fingerprint = str(
            self._journal_offsets.pop("__navroute_file__", "") or ""
        )
        stored_journal_root = str(
            self._journal_offsets.pop("__journal_root__", "") or ""
        )
        if stored_journal_root != self._eddn_journal_root:
            self._journal_offsets = {}
            self._station_fingerprints = {}
            self._navroute_fingerprint = ""
        if not isinstance(self._station_fingerprints, dict):
            self._station_fingerprints = {}
        self._station_rejections: dict[str, str] = {}
        self._navroute_rejections: dict[str, str] = {}
        self._eddn_profile_paths_signature = None
        self._eddn_profile_paths_cache = []
        self._eddn_context = self._rebuild_eddn_context()
        self._eddn_busy = False
        self._eddn_status = "EDDN network access is disabled."
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
        self.eddnFinished.connect(self._finish_eddn)
        self.eddnRelay.connect(self._accept_eddn_relay)
        self.traderSyncFinished.connect(self._finish_trader_catalog_sync)
        self.techBrokerSyncFinished.connect(self._finish_tech_broker_catalog_sync)
        self._data_dir = runtime_data_dir(self.package_root)
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
        self._activity = "Connecting to Elite Journal…"
        self._last_journal_stamp = None
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
        package_root = self.package_root
        selected_ship = self._selected_ship

        def worker():
            try:
                state = build_state(
                    package_root, selected_ship,
                    trader_preference=self._trader_preference,
                )
                state["_logbookEntries"] = logbook_entries(package_root)
                rows = self._build_hge_candidate_rows(
                    state, list(self._hge_sightings)
                )
                self.startupStateReady.emit((state, rows))
            except Exception as exc:
                self.startupStateFailed.emit(str(exc))

        threading.Thread(
            target=worker, name="initial-journal-state", daemon=True
        ).start()

    @Slot(object)
    def _finish_startup_state(self, payload):
        state, startup_rows = payload
        if not isinstance(state, dict):
            self._fail_startup_state("Initial Journal state was not a mapping.")
            return
        self._logbook_entries = list(state.pop("_logbookEntries", []))
        state.pop("_craftBatch", None)
        self._logbook_revision += 1
        self._state = state
        self._hge_candidate_cache_key = (
            id(self._hge_sightings), len(self._hge_sightings), id(self._state)
        )
        self._hge_candidate_cache_rows = startup_rows
        self._hge_material_filter_cache = None
        self._selected_ship = str(state.get("ship") or "")
        self._activity = "Journal synchronized · live inventory loaded"
        self._log_consistency_issues(state)
        self._publish_full_state()
        self.activityChanged.emit()
        self.connectionChanged.emit()

    @Slot(str)
    def _fail_startup_state(self, message):
        self._activity = f"Journal startup sync failed · {message}"
        self.activityChanged.emit()

    def _log_consistency_issues(self, state):
        issues = tuple(str(item) for item in state.get("consistencyIssues", []))
        if not issues or issues == self._last_consistency_signature:
            return
        self._last_consistency_signature = issues
        for issue in issues:
            self._write_log(f"CONSISTENCY · {issue}")

    def _publish_full_state(self) -> None:
        """Notify each state domain once after an atomic state replacement."""
        self._state_revision += 1
        self._derived_cache.clear()
        self.stateChanged.emit()
        self.materialsChanged.emit()
        self.fleetChanged.emit()
        self.wishlistChanged.emit()
        self.operationsChanged.emit()
        self.hgeChanged.emit()
        self.journalHealthChanged.emit()
        self.logbookChanged.emit()

    def _load_ui_config(self):
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

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

    def _save_ui_config(self):
        atomic_write(self.config_file, json.dumps({
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
        }, indent=2))

    def _load_inara_config(self):
        defaults = {
            "api_key": "", "commander_name": "", "frontier_id": "",
            "consent": False, "auto_sync": False, "request_times": [],
        }
        try:
            loaded = json.loads(
                self.inara_config_file.read_text(encoding="utf-8")
            )
            if isinstance(loaded, dict):
                defaults.update({
                    key: loaded.get(key, defaults[key]) for key in defaults
                })
        except (OSError, ValueError, TypeError):
            pass
        return defaults

    @staticmethod
    def _read_local_json(path, fallback):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return deepcopy(fallback)

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

    def _save_eddn(self):
        self._eddn_queue = compact_upload_queue(self._eddn_queue)
        atomic_write(self.eddn_config_file, json.dumps(self._eddn_config, indent=2))
        atomic_write(self.eddn_queue_file, json.dumps(self._eddn_queue, indent=2))

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

    def _eddn_delivery_summary(self):
        counts = {key: 0 for key in ("queued", "retry", "sending", "sent", "failed")}
        for job in self._eddn_queue:
            status = str(job.get("status") or "")
            if status in counts:
                counts[status] += 1
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
        return {
            **counts,
            "waiting": counts["queued"] + counts["retry"] + counts["sending"],
            "lastSuccessAt": str(persisted.get("sentAt") or queued_sent.get("sent_at") or ""),
            "lastSuccessSchema": str(persisted.get("schema") or sent_event.get("schema") or ""),
            "lastSuccessEvent": str(persisted.get("eventName") or sent_message.get("event") or ""),
            "lastError": str(failed.get("last_error") or ""),
            "lastNotShareable": str(self._eddn_config.get("last_not_shareable") or ""),
            "nextRetryAt": next_retry_at,
        }

    def _eddn_station_snapshot_view(self, directory=None):
        """Describe the three Elite station snapshots without exposing contents."""
        directory = Path(directory) if directory is not None else journal_dir()
        rows = []
        for kind, filename, schema in (
            ("MARKET", "Market.json", "commodity/3"),
            ("OUTFITTING", "Outfitting.json", "outfitting/2"),
            ("SHIPYARD", "Shipyard.json", "shipyard/2"),
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
                and job["event"].get("schema") == schema
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
                receipt = receipts.get(schema, {}) if isinstance(receipts, dict) else {}
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

    def _save_hge_cache(self):
        atomic_write(
            self.hge_cache_file,
            json.dumps(self._hge_sightings[-HGE_OBSERVATION_LIMIT:], indent=2),
        )

    def _save_inara_config(self):
        atomic_write(self.inara_config_file, json.dumps(self._inara_config, indent=2))

    def _save_inara_journal_cache(self):
        atomic_write(self.inara_journal_cache_file, json.dumps(self._inara_cache, indent=2))

    def _load_inara_receipts(self):
        try:
            rows = json.loads(
                self.inara_receipts_file.read_text(encoding="utf-8")
            )
            return rows[:100] if isinstance(rows, list) else []
        except (OSError, ValueError, TypeError):
            return []

    def _save_inara_receipts(self):
        atomic_write(self.inara_receipts_file, json.dumps(self._inara_receipts[:100], indent=2))

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
            profiled_journal_events(),
        )
        self._derived_cache["commander_cards"] = (cache_key, cards)
        return cards

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
                lines = latest.read_text(
                    encoding="utf-8-sig", errors="replace"
                ).splitlines()
                if lines:
                    record = json.loads(lines[-1])
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
        rows = assign_plans_to_nearest_engineers(
            self._state.get("blueprints", []),
            self._engineer_index(),
        )
        rows.sort(key=lambda row: (
            row.get("distance", -1) < 0,
            row.get("distance", 0) if row.get("distance", -1) >= 0 else 0,
            -row.get("readyJobs", 0),
            row.get("name", "").casefold(),
        ))
        if self._deferred_engineers:
            rows = [
                row for row in rows
                if row.get("name") not in self._deferred_engineers
            ] + [
                row for row in rows
                if row.get("name") in self._deferred_engineers
            ]
        return [
            {
                **row,
                "sequence": index,
                "summary": (
                    f"{row['openJobs']} job{'s' if row['openJobs'] != 1 else ''}"
                    f" · {row['readyJobs']} material-ready"
                ),
            }
            for index, row in enumerate(rows, 1)
        ]

    def _next_action(self):
        return str(self._operation_action().get("title") or "Open Engineering")

    def _operation_action(self):
        return self._cached_derived(
            "operation_action", (
                self._state_revision, tuple(sorted(self._deferred_engineers))
            ),
            lambda: select_operation_action(
                self._state,
                self._engineer_mission_route(),
                self._engineer_index(),
                [
                    record
                    for records in self._blueprint_groups.values()
                    for record in records
                ],
            ),
        )

    def _hge_targets(self):
        return self._cached_derived(
            "hge_targets", (self._state_revision, self._hge_revision),
            self._build_hge_targets,
        )

    def _build_hge_targets(self):
        rows = []
        for material in self._state.get("materials", []):
            key = str(material.get("key") or "")
            if int(material.get("missing", 0) or 0) <= 0 or not is_hge_material(key):
                continue
            ranked = rank_hge_sightings(
                self._hge_sightings, normalize(key),
                self._state.get("currentPosition"),
            )
            target = ranked[0] if ranked else {}
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

    def _eddn_delivery_for_candidate(self, candidate):
        evidence = str(candidate.get("evidence_kind") or "")
        if evidence not in {"LOCAL_JOURNAL", "ENTERED"}:
            return ""
        if candidate.get("details_unknown"):
            return "EDDN NOT SHAREABLE"
        if not eddn_upload_allowed(self._eddn_config):
            return "EDDN OFF"
        address = candidate.get("system_address")
        timestamp = self._state_find_timestamp(candidate.get("latest_timestamp"))
        for job in reversed(self._eddn_queue):
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

    def _state_find_origin(self, coordinates):
        """Resolve the Commander position from evidence, never estimation."""
        journal_position = self._valid_star_position(
            self._state.get("currentPosition")
        )
        if journal_position is not None:
            return journal_position
        eddn_position = self._valid_star_position(self._eddn_context.get("StarPos"))
        if eddn_position is not None:
            return eddn_position
        system = str(
            self._state.get("system") or self._eddn_context.get("StarSystem") or ""
        ).strip().casefold()
        return self._valid_star_position(coordinates.get(system))

    def _state_find_observations(self):
        """Add exact catalog coordinates where source rows omitted StarPos."""
        coordinates = self._system_coordinate_index()
        source = list(self._hge_sightings)
        source.extend(self._state.get("localStateFinds", []))
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
        return observations, self._state_find_origin(coordinates)

    def _build_state_find_rows(self):
        material_names = {
            normalize(row.get("key")): str(row.get("name") or row.get("key") or "")
            for row in self._state.get("materials", [])
        }
        observations, origin = self._state_find_observations()
        local_scan = self._state.get("localStateFindScan", {}) or {}
        current_system = str(self._state.get("system") or "").strip().casefold()
        rows = []
        for candidate in rank_state_find_systems(
            observations, origin,
            current_system=self._state.get("system", ""),
            current_system_address=self._state.get("currentSystemAddress"),
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
                "BGS_PREDICTION": "PREDICTED",
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
                "eddnDelivery": self._eddn_delivery_for_candidate(candidate),
            })
        return rows

    def _state_find_filter_values(self, field, all_label):
        values = set()
        for row in self._state_find_rows():
            source = row.get(field, [])
            if isinstance(source, list):
                values.update(str(value) for value in source if value)
        return [all_label] + sorted(values, key=str.casefold)

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

    @Slot(str, str, str, int, str, str, int, result="QVariantList")
    def stateFindPage(self, find_type, state_filter, allegiance_filter,
                      nearby_ly, material_filter, evidence_filter, limit):
        return self._filtered_state_finds(
            find_type, state_filter, allegiance_filter, nearby_ly,
            material_filter, evidence_filter,
        )[:max(1, int(limit or 250))]

    @Slot(str, str, str, int, str, str, result=int)
    def stateFindCount(self, find_type, state_filter, allegiance_filter,
                       nearby_ly, material_filter, evidence_filter):
        return len(self._filtered_state_finds(
            find_type, state_filter, allegiance_filter, nearby_ly,
            material_filter, evidence_filter,
        ))

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
    materials = Property("QVariantList", lambda self: self._get("materials", []), notify=materialsChanged)
    missingMaterials = Property(
        "QVariantList",
        lambda self: [
            row for row in self._get("materials", [])
            if int(row.get("missing", 0) or 0) > 0
        ],
        notify=materialsChanged,
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
    inaraReceipts = Property(
        "QVariantList", lambda self: self._inara_receipts,
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
    eddnBusy = Property(
        bool, lambda self: self._eddn_busy, notify=connectionChanged,
    )
    eddnQueue = Property(
        "QVariantList", lambda self: self._eddn_queue_view(),
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
            if not any(
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
                self.refreshStateReady.emit((revision, state))
            except Exception as exc:
                self.refreshStateFailed.emit((revision, str(exc)))

        threading.Thread(
            target=worker, name=f"journal-state-{revision}", daemon=True,
        ).start()

    @Slot(object)
    def _finish_refresh_state(self, payload: object) -> None:
        revision, state = payload
        self._refresh_in_flight = False
        if revision != self._refresh_revision or self._refresh_dirty:
            self._refresh_dirty = True
            self._launch_state_refresh()
            return
        if not isinstance(state, dict):
            self._fail_refresh_state((revision, "Journal state was not a mapping."))
            return
        previous = self._state
        self._data_dir = runtime_data_dir(self.package_root)
        self._logbook_entries = list(state.pop("_logbookEntries", []))
        self._logbook_revision += 1
        craft_batch = dict(state.pop("_craftBatch", {}) or {})
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
        self._log_consistency_issues(self._state)
        self._publish_full_state()
        self.activityChanged.emit()

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
        selected_name = str(self._selected_blueprint.get("name") or "")
        matches = bool(
            installed_grade > 0 and installed_name and selected_name
            and normalize(installed_name) == normalize(selected_name)
        )
        self._selected_blueprint.update({
            "installedEngineeringKnown": installed_grade > 0,
            "installedBlueprint": installed_name,
            "installedGrade": installed_grade,
            "installedExperimentalEffect": str(
                selected.get("experimentalEffect") or ""
            ),
            "installedMatchesSelection": matches,
        })
        self._current_grade = min(installed_grade, self._target_grade) if matches else 0

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
                # Imported target modules may not be installed yet. Preserve
                # the slot but require a truthful future Journal binding.
                "module_id": "",
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
                    grades, 0, int(row.get("grade", 0) or 0),
                    instance=instance,
                    experimental_id=effect_id if mode == "combined" else "",
                    experimental_name=(
                        str(effect.get("Name") or "")
                        if mode == "combined" and effect else ""
                    ),
                    plan_mode=mode, **binding,
                    journal_baseline=import_baseline,
                )
                if plan:
                    tasks.append(plan)
                    if mode == "combined" and effect:
                        effect_record = deepcopy(effect)
                        effect_record.update({
                            "Kind": "ExperimentalEffect", "Grade": None,
                            "_ParentPlanId": plan[0]["_Planner"]["plan_id"],
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
            + (f" · {duplicates} duplicate(s) skipped" if duplicates else "")
            + "."
        )
        if applied == 0 and duplicates == 0:
            self._engineering_status = (
                "Build import applied no plans. Review the preview warnings and "
                "select at least one safely mapped engineered module."
            )
        self._build_import_preview["applied"] = applied
        self._build_import_preview["duplicates"] = duplicates
        self._build_import_preview["actionMessage"] = self._engineering_status
        self._build_import_preview["actionError"] = applied == 0 and duplicates == 0
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

    @Slot(str, str, bool, bool)
    def saveInaraConfig(self, api_key, commander, consent, auto_sync):
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
        self._save_inara_config()
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
        self._inara_config["api_key"] = ""
        self._inara_config["auto_sync"] = False
        self._save_inara_config()
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
            "journal_root": str(journal_dir().resolve()),
            "fingerprints": discarded[-5000:],
        })
        self._inara_pending_events = []
        self._inara_pending_fingerprints = []
        self._inara_inflight_fingerprints = []
        self._inara_pending_since = 0.0
        self._inara_retry_not_before = 0.0
        self._inara_failure_count = 0
        self._save_inara_journal_cache()

    def _scan_inara_journal(self):
        identity, _commander = active_profile_identity()
        paths = journal_paths_for_profile(identity)[-5:] if identity else []
        events = []
        for path in paths:
            try:
                with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
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
                LOGGER.warning("INARA Journal read failed for %s: %s", path, exc)
        journal_root = str(journal_dir().resolve())
        if self._inara_cache.get("journal_root") != journal_root:
            self._inara_cache = {
                key: self._inara_cache[key]
                for key in ("last_request_at", "rate_limit_until")
                if key in self._inara_cache
            }
            self._inara_pending_events = []
            self._inara_pending_fingerprints = []
        delivered = list(self._inara_cache.get("fingerprints", []))
        known = delivered + self._inara_pending_fingerprints
        detected, prepared, fingerprints = prepare_journal_batch(
            events, known, identity,
            max_events=max(0, INARA_PENDING_EVENT_LIMIT - len(
                self._inara_pending_events
            )),
        )
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
            self._save_inara_journal_cache()
            return False
        if not self._inara_auto_enabled():
            self._inara_cache["fingerprints"] = (delivered + fingerprints)[-5000:]
            self._save_inara_journal_cache()
            return False
        if (
            any(event.get("event") == "CommunityGoal" for event in events)
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
                self.inaraFinished.emit(
                    operation, True, json.dumps(receipt), result_data
                )
            except InaraError as exc:
                self.inaraFinished.emit(operation, False, json.dumps({
                    "message": str(exc),
                    "retryable": exc.retryable,
                    "statusCode": exc.status_code,
                    "schemaError": exc.schema_error,
                }), [])
            except Exception as exc:
                LOGGER.exception("INARA worker failed (%s)", operation)
                self.inaraFinished.emit(
                    operation, False,
                    f"Unexpected local connector error: {type(exc).__name__}: {exc}",
                    [],
                )

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

    @Slot(str, bool, str, object)
    def _finish_inara(self, operation, success, message, ships):
        self._inara_busy = False
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
                rate_limited = any(marker in error_message.casefold() for marker in (
                    "too much requests", "temporarily revoked", "rate limit",
                ))
                if rate_limited:
                    self._inara_retry_not_before = (
                        time.monotonic() + INARA_RATE_LIMIT_COOLDOWN_SECONDS
                    )
                    self._inara_cache["rate_limit_until"] = (
                        time.time() + INARA_RATE_LIMIT_COOLDOWN_SECONDS
                    )
                    self._save_inara_journal_cache()
                retry_note = (
                    f" · {len(self._inara_pending_events)} journal event(s) retained; "
                    + (
                        "INARA cooldown active for at least 60 minutes"
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
            delivered = list(self._inara_cache.get("fingerprints", []))
            delivered.extend(self._inara_inflight_fingerprints)
            self._inara_cache.update({
                "initialized": True,
                "journal_root": str(journal_dir().resolve()),
                "fingerprints": delivered[-5000:],
            })
            if isinstance(ships, dict) and ships.get("communityGoalsQueried"):
                self._inara_cache["community_goals_timestamp"] = time.time()
                self._inara_cache["community_goals"] = list(
                    ships.get("communityGoals") or []
                )
            del self._inara_pending_events[:count]
            del self._inara_pending_fingerprints[:count]
            self._inara_inflight_fingerprints = []
            self._inara_pending_since = (
                time.monotonic() if self._inara_pending_events else 0.0
            )
            self._save_inara_journal_cache()
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
        self._inara_receipts = self._inara_receipts[:100]
        self._save_inara_receipts()
        self._inara_status = (
            f"{receipt['operation']} · HTTP {receipt['httpStatus']} · "
            f"{receipt['elapsedMs']} ms"
        )
        self.connectionChanged.emit()

    @Slot(bool, bool, bool)
    def saveEddnConfig(self, consent, upload_enabled, listener_enabled):
        consent = bool(consent)
        self._eddn_config.update({
            "consent": consent,
            "upload_enabled": consent and bool(upload_enabled),
            "listener_enabled": consent and bool(listener_enabled),
        })
        if self._eddn_config["upload_enabled"] and not self._journal_offsets:
            for path in self._eddn_profile_journal_paths():
                try:
                    self._journal_offsets[path.name] = path.stat().st_size
                except OSError:
                    LOGGER.warning("EDDN could not initialize cursor for %s", path)
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

    @staticmethod
    def _profile_key(identity: object) -> str:
        value = str(identity or "").strip()
        return (
            hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            if value else "unidentified"
        )

    def _detected_eddn_identity(self) -> str:
        requested = str(os.environ.get("EDOPS_PROFILE_FID") or "").strip()
        if requested:
            return requested
        identity, _commander = active_profile_identity()
        return identity

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
        events = []
        for path in self._eddn_profile_journal_paths():
            try:
                with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                    for line_number, line in enumerate(handle, 1):
                        try:
                            event = json.loads(line)
                        except (TypeError, ValueError):
                            LOGGER.warning(
                                "EDDN context replay skipped invalid JSON: %s:%s",
                                path.name, line_number,
                            )
                            continue
                        if isinstance(event, dict):
                            events.append(event)
            except OSError as exc:
                LOGGER.warning("EDDN context replay failed for %s: %s", path, exc)
        context = rebuild_eddn_context(events, self._eddn_profile_identity)
        if self._eddn_profile_identity and not context.get("StarSystem"):
            LOGGER.warning(
                "EDDN context replay found no current system for profile %s",
                self._eddn_profile_key,
            )
        return context

    def _sync_eddn_profile(self) -> bool:
        profile_identity = self._detected_eddn_identity()
        profile_key = self._profile_key(profile_identity)
        journal_root = str(journal_dir().resolve())
        if (
            profile_key == self._eddn_profile_key
            and journal_root == self._eddn_journal_root
        ):
            return True
        if self._eddn_busy:
            LOGGER.warning("EDDN profile switch deferred while an upload is active")
            return False
        profile_dir = runtime_data_dir(self.package_root)
        self._eddn_profile_key = profile_key
        self._eddn_profile_identity = profile_identity
        self._eddn_journal_root = journal_root
        self.eddn_config_file = profile_dir / "eddn_config.json"
        self.eddn_queue_file = profile_dir / "community_upload_queue.json"
        self.eddn_cursor_file = profile_dir / "eddn_journal_cursor.json"
        self._eddn_config = self._load_eddn_config()
        self._eddn_queue = normalize_upload_queue(
            self._read_local_json(self.eddn_queue_file, [])
        )
        cursor = self._read_local_json(self.eddn_cursor_file, {})
        if not isinstance(cursor, dict):
            cursor = {}
        stored_root = str(cursor.pop("__journal_root__", "") or "")
        self._station_fingerprints = cursor.pop("__station_files__", {})
        self._navroute_fingerprint = str(
            cursor.pop("__navroute_file__", "") or ""
        )
        self._journal_offsets = cursor if stored_root == journal_root else {}
        if stored_root != journal_root:
            self._station_fingerprints = {}
            self._navroute_fingerprint = ""
        if not isinstance(self._station_fingerprints, dict):
            self._station_fingerprints = {}
        self._station_rejections = {}
        self._navroute_rejections = {}
        self._eddn_context = self._rebuild_eddn_context()
        self._save_eddn()
        LOGGER.info(
            "EDDN context switched to isolated profile %s at %s",
            profile_key, journal_root,
        )
        return True

    def _save_eddn_cursor(self):
        cursor = dict(self._journal_offsets)
        cursor["__station_files__"] = dict(self._station_fingerprints)
        cursor["__navroute_file__"] = self._navroute_fingerprint
        cursor["__journal_root__"] = self._eddn_journal_root
        atomic_write(self.eddn_cursor_file, json.dumps(cursor, indent=2))

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
        if any(job.get("id") == job_id for job in self._eddn_queue):
            return
        pending_count = sum(
            row.get("status") != "sent" for row in self._eddn_queue
            if isinstance(row, dict)
        )
        if pending_count >= EDDN_PENDING_JOB_LIMIT:
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
        for path in paths:
            try:
                size = path.stat().st_size
                offset = int(self._journal_offsets.get(path.name, size))
                if offset > size:
                    offset = 0
                with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                    handle.seek(offset)
                    lines = handle.readlines()
                    self._journal_offsets[path.name] = handle.tell()
                changed = changed or bool(lines)
            except OSError:
                LOGGER.warning("EDDN Journal read failed for %s", path)
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    LOGGER.warning("EDDN skipped malformed Journal JSON in %s", path.name)
                    continue
                if not isinstance(event, dict):
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
                        continue
                prepared = prepare_eddn_event(event, self._eddn_context)
                if prepared:
                    self._enqueue_eddn(prepared)
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
        if changed:
            self._save_eddn_cursor()
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
            self._enqueue_eddn(prepared)
            self._station_fingerprints[filename] = fingerprint
            self._station_rejections.pop(filename, None)
            count = len(
                prepared["message"].get("commodities")
                or prepared["message"].get("modules")
                or prepared["message"].get("ships")
                or []
            )
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
                delay = max(
                    60, min(900, 60 * (2 ** max(0, job["attempts"] - 1)))
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
        self._eddn_queue = compact_upload_queue(self._eddn_queue)
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self.connectionChanged.emit()

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
                self.techBrokerSyncFinished.emit(True, json.dumps(result))
            except Exception as exc:
                self.techBrokerSyncFinished.emit(
                    False, f"{type(exc).__name__}: {exc}"
                )

        self._start_network_worker(worker, "tech-broker-catalog-sync")

    @Slot(bool, str)
    def _finish_tech_broker_catalog_sync(self, success, payload):
        self._tech_broker_sync_busy = False
        if not success:
            self._tech_broker_sync_status = (
                f"Spansh update failed · bundled recommendations remain active · {payload}"
            )
            self.connectionChanged.emit()
            return
        try:
            result = json.loads(payload)
            existing = self._read_local_json(self.tech_broker_catalog_file, {})
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
            atomic_write(self.tech_broker_catalog_file, json.dumps(document, indent=2))
        except (OSError, TypeError, ValueError) as exc:
            self._tech_broker_sync_status = (
                f"Live results received, but local merge failed · {exc}"
            )
            self.connectionChanged.emit()
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
                self.traderSyncFinished.emit(True, json.dumps(result))
            except Exception as exc:
                self.traderSyncFinished.emit(
                    False, f"{type(exc).__name__}: {exc}"
                )

        self._start_network_worker(worker, "trader-catalog-sync")

    @Slot(bool, str)
    def _finish_trader_catalog_sync(self, success, payload):
        self._trader_sync_busy = False
        if not success:
            self._trader_sync_status = (
                f"Spansh update failed · offline catalog remains active · {payload}"
            )
            self.connectionChanged.emit()
            return
        try:
            result = json.loads(payload)
            existing = self._read_local_json(self.trader_catalog_file, {})
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
            atomic_write(self.trader_catalog_file, json.dumps(document, indent=2))
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
        except (OSError, TypeError, ValueError) as exc:
            self._trader_sync_status = (
                f"Live results received, but local merge failed · {exc}"
            )
            self.connectionChanged.emit()
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
        self._eddn_queue = [
            row for row in self._eddn_queue if row.get("status") != "sent"
        ]
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self.connectionChanged.emit()

    @Slot()
    def refreshHgeFinderLifetime(self):
        """Re-evaluate HGE expiry without rebuilding Journal-derived state."""
        self.hgeChanged.emit()
        if self._selected_material:
            key = str(self._selected_material.get("key") or "")
            self.selectMaterial(key)

    @Slot()
    def refreshStateFinds(self):
        """Refresh every local/live State Finds source without inventing history."""
        self.flushHgeObservationBatch()
        compacted, removed = compact_hge_observations(self._hge_sightings)
        if removed:
            self._hge_sightings = compacted[-HGE_OBSERVATION_LIMIT:]
            self._save_hge_cache()
        self._ensure_eddn_listener()
        self._scan_eddn_journal()
        self.refresh()
        listener = self._eddn_listener_status
        self._state_find_refresh_status = (
            f"REFRESHED {time.strftime('%H:%M')} · {listener}"
            + (f" · {removed} EXPIRED REMOVED" if removed else "")
        )
        self.hgeChanged.emit()

    @Slot(object)
    def _accept_eddn_relay(self, payload):
        snapshot = extract_system_bgs_snapshot(payload)
        if snapshot:
            self._pending_bgs_snapshots.append(snapshot)
        self._pending_hge_observations.extend(extract_signal_finds(payload))

    @Slot()
    def flushHgeObservationBatch(self):
        snapshots = self._pending_bgs_snapshots
        hge_rows = self._pending_hge_observations
        self._pending_bgs_snapshots = []
        self._pending_hge_observations = []
        updated, applied = apply_system_bgs_snapshot_batch(
            self._hge_sightings, snapshots, HGE_OBSERVATION_LIMIT
        )
        updated, hge_changed = merge_hge_observation_batch(
            updated, hge_rows, HGE_OBSERVATION_LIMIT
        )
        updated, removed = compact_hge_observations(updated)
        changed = bool(applied or hge_changed or removed)
        if changed:
            self._hge_sightings = updated[-HGE_OBSERVATION_LIMIT:]
            self._save_hge_cache()
            self.hgeChanged.emit()
            if self._selected_material:
                self.selectMaterial(str(self._selected_material.get("key") or ""))
        self._eddn_listener_status = (
            f"Connected · {len(self._hge_sightings)} cached HGE/BGS observations"
        )
        if snapshots or hge_rows or removed:
            self.connectionChanged.emit()

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
        if getattr(self, "hgeBatchTimer", None):
            self.hgeBatchTimer.stop()
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
            self.flushHgeObservationBatch()
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
        self._save_ui_config()
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
        self._save_ui_config()
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
        self._save_ui_config()
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
            self._selected_ship = ""
            self.refresh()
            self._activity = "Journal directory updated."
        else:
            self._activity = "Journal directory does not exist."
        self.activityChanged.emit()

    @Slot()
    def pollJournal(self):
        if not self._journal_auto:
            self._maybe_start_inara_auto()
            self._process_eddn_queue()
            return
        stamp = journal_change_signature()
        if self._last_journal_stamp is None:
            self._last_journal_stamp = stamp
            self._scan_inara_journal()
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
            self._scan_inara_journal()
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
        page = max(0, min(10, int(page)))
        if page != self._last_page:
            if self._last_page == 3 and page != 3:
                self.clearCraftConfirmation()
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
            self._commander_card_order = order
            self._save_ui_config()
            self.uiChanged.emit()

    @Slot("QVariantList")
    def setNavigationOrder(self, order):
        order = list(dict.fromkeys(
            str(item) for item in list(order or [])
            if str(item) in NAVIGATION_IDS
        ))
        order.extend(item for item in NAVIGATION_IDS if item not in order)
        if order != self._navigation_order:
            self._navigation_order = order
            self._save_ui_config()
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
