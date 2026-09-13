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


class FrontierCapiMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_frontier_capi() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    frontierFinished = Signal(object)


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


    frontierConnected = Property(
        bool, lambda self: self._frontier_tokens is not None,
        notify=CoreControllerMixin.connectionChanged,
    )


    frontierBusy = Property(
        bool, lambda self: self._frontier_busy,
        notify=CoreControllerMixin.connectionChanged,
    )


    frontierStatus = Property(
        str, lambda self: self._frontier_status,
        notify=CoreControllerMixin.connectionChanged,
    )


    frontierLastSync = Property(
        str, lambda self: self._frontier_last_sync,
        notify=CoreControllerMixin.connectionChanged,
    )


    frontierConsent = Property(
        bool, lambda self: bool(self._frontier_config.get("consent")),
        notify=CoreControllerMixin.connectionChanged,
    )


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



    def _init_frontier_capi(self):
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
