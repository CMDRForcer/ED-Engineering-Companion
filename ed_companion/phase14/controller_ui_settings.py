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
from .controller_core import CoreControllerMixin, THEME_IDS
EDDN_ACTIVE_RECEIPT_LIMIT = 100
FRONTIER_REQUEST_WATCHDOG_MS = 120_000
COMMANDER_CARD_IDS = (
    "ranks", "major-reputation", "finances", "current-ship",
    "minor-reputation", "squadron",
)
HGE_OBSERVATION_LIMIT = 10000
BGS_OBSERVATION_BATCH_SECONDS = 30
MINING_OBSERVATION_BATCH_SECONDS = 30
MINING_TRANSIENT_FIELDS = frozenset({
    "ageSeconds", "confirmationStatus", "freshnessLimitSeconds",
    "recheckRecommended", "stale",
})
HGE_CLASSIFIER_VERSION = 2


class UiSettingsMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_ui_settings() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    rendererChanged = Signal()


    startupStateReady = Signal(object)


    startupStateFailed = Signal(object)


    restartRequested = Signal()


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


    def _load_ui_config(self):
        return load_json_file(self.config_file, {}, encoding="utf-8")


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
    def _detect_renderer():
        api = QQuickWindow.graphicsApi()
        return str(api).split(".")[-1]


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


    debugMode = Property(bool, lambda self: self._debug_mode, notify=CoreControllerMixin.uiChanged)


    backgroundMode = Property(
        bool, lambda self: self._background_mode, notify=CoreControllerMixin.uiChanged,
    )


    autostartEnabled = Property(
        bool, lambda self: self._autostart_enabled, notify=CoreControllerMixin.uiChanged,
    )


    backgroundRuntimeStatus = Property(
        str, lambda self: self._background_runtime_status, notify=CoreControllerMixin.uiChanged,
    )


    interfaceActivity = Property(
        "QVariantList",
        lambda self: build_interface_activity_feed(
            self._inara_receipts, self._eddn_queue, self._frontier_last_sync,
        ),
        notify=CoreControllerMixin.connectionChanged,
    )


    @Slot()
    def requestRestart(self):
        self.restartRequested.emit()


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


    @Slot(str)
    def setRendererMode(self, mode):
        mode = str(mode or "").lower()
        if mode not in {"auto", "gpu", "software"}:
            return
        self._renderer_mode = mode
        self._save_ui_config()
        self._restart_required = True
        self.rendererChanged.emit()
