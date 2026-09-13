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


class ExobiologyMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_exobiology() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    exobiologyChanged = Signal()


    exobiologyDistanceCheckChanged = Signal()


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


    exobiologyLifetimeEarned = Property(
        float, lambda self: float(self._get("exobiologyLifetimeEarned", 0) or 0),
        notify=exobiologyChanged,
    )


    exobiologyBestFind = Property(
        "QVariantMap", lambda self: self._get("exobiologyBestFind", {}) or {},
        notify=exobiologyChanged,
    )


    exobiologyRemainingOnBody = Property(
        "QVariantMap", lambda self: self._get("exobiologyRemainingOnBody", {}) or {},
        notify=exobiologyChanged,
    )


    exobiologyGenusCompletion = Property(
        "QVariantMap", lambda self: self._get("exobiologyGenusCompletion", {}) or {},
        notify=exobiologyChanged,
    )


    exobiologyDistanceCheck = Property(
        "QVariantMap", lambda self: self._exobiology_distance_check_value,
        notify=exobiologyDistanceCheckChanged,
    )


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



    def _init_exobiology(self):
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
