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
HGE_OBSERVATION_LIMIT = 10000
BGS_OBSERVATION_BATCH_SECONDS = 30
MINING_OBSERVATION_BATCH_SECONDS = 30
MINING_TRANSIENT_FIELDS = frozenset({
    "ageSeconds", "confirmationStatus", "freshnessLimitSeconds",
    "recheckRecommended", "stale",
})
HGE_CLASSIFIER_VERSION = 2


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


class JournalHealthMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_journal_health() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    journalHealthChanged = Signal()


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


    journalAuto = Property(
        bool, lambda self: self._journal_auto, notify=CoreControllerMixin.uiChanged,
    )


    journalHealth = Property(
        "QVariantMap", lambda self: self._journal_health(),
        notify=journalHealthChanged,
    )


    journalPath = Property(str, lambda self: str(journal_dir()), notify=CoreControllerMixin.stateChanged)


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
