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


class CommanderMixin:
    """Extracted from CockpitController (controller.py modularization).

    __init__ setup for these attributes stays in CockpitController.__init__
    itself - it's genuinely interleaved with other domains' setup (fleet
    rebuild, credit-snapshot history, UI config), so only the
    Property/Signal/method definitions move here; the attributes they
    read/write continue to be initialized exactly where they already were.
    """

    commanderCardsChanged = Signal()
    fleetChanged = Signal()


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


    commanderKnown = Property(
        bool, lambda self: bool(self._get("commanderKnown", False)), notify=CoreControllerMixin.stateChanged
    )


    commander = Property(
        str, lambda self: str(self._get("commander", "")), notify=CoreControllerMixin.stateChanged
    )


    commanderOverview = Property(
        "QVariantMap", lambda self: self._get("commanderOverview", {}),
        notify=CoreControllerMixin.stateChanged,
    )


    powerplayOverview = Property(
        "QVariantMap", lambda self: self._get("powerplayOverview", {}),
        notify=CoreControllerMixin.stateChanged,
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
        notify=CoreControllerMixin.uiChanged,
    )


    commanderUpdatePopups = Property(
        bool, lambda self: self._commander_update_popups, notify=CoreControllerMixin.uiChanged,
    )


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


    @Slot(bool)
    def setCommanderUpdatePopups(self, enabled):
        self._commander_update_popups = bool(enabled)
        self._save_ui_config()
        self.uiChanged.emit()


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
