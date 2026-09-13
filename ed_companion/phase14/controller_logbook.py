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


class LogbookMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_logbook() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    logbookChanged = Signal()


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



    def _init_logbook(self):
        self._logbook_entries = []
        self._logbook_filter = "ALL"
        self._logbook_query = ""
        self._selected_logbook_entry = {}
        self._logbook_revision = 0
        self._logbook_notes = load_logbook_notes(self.config_dir)
