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


class NavigationMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_navigation() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    miningChanged = Signal()


    miningSyncFinished = Signal(object)


    miningCatalogLoaded = Signal(object)


    miningRowsReady = Signal(object)


    traderSyncFinished = Signal(bool, str)


    def _load_trader_sync_status(self):
        data = self._read_local_json(self.trader_catalog_file, {})
        count = len(data.get("stations", [])) if isinstance(data, dict) else 0
        fetched = str(data.get("fetched_at") or "") if isinstance(data, dict) else ""
        return (
            f"Offline catalog active · 1,622 bundled + {count} live updates"
            + (f" · {fetched}" if fetched else "")
        )


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
            reason = "ED-Frame cannot currently prove reliable Journal processing. Keep EDMC until the Journal status is LIVE or READY."
        elif not upload_enabled:
            verdict = "YES — EDDN SHARING IS OFF"
            tone = "WARNING"
            reason = "ED-Frame reads the Journal, but anonymous EDDN upload is disabled. EDMC is still needed if you want to contribute community data."
        elif failed:
            verdict = "RECOMMENDED — EDDN ERRORS PENDING"
            tone = "WARNING"
            reason = f"ED-Frame has {failed} failed EDDN delivery job(s). Resolve or safely retry them before retiring EDMC."
        else:
            verdict = "NO — FOR JOURNAL + EDDN"
            tone = "READY"
            reason = "ED-Frame is processing the Journal and its EDDN sender is enabled. Running EDMC in parallel is not required for these paths."
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


    @Slot()
    def _invalidate_hge_cache(self) -> None:
        self._hge_revision += 1
        self._drop_derived({"hge_targets", "hge_finder_rows"})


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


    traderRoute = Property(
        "QVariantList", lambda self: self._get("traderRoute", []),
        notify=CoreControllerMixin.materialsChanged,
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


    pinnedMiningSystems = Property(
        "QVariantList", lambda self: sorted(self._mining_pins),
        notify=miningChanged,
    )


    @Slot(str)
    def toggleMiningPin(self, key):
        key = str(key or "").strip()
        if not key:
            return
        if key in self._mining_pins:
            self._mining_pins.discard(key)
        else:
            self._mining_pins.add(key)
        self._persist_json(
            self.mining_pins_file, sorted(self._mining_pins), "Mining Finder pins",
        )
        self.miningChanged.emit()


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
        notify=CoreControllerMixin.hgeChanged,
    )


    hgeFinderRows = Property(
        "QVariantList", lambda self: self._hge_finder_rows(),
        notify=CoreControllerMixin.hgeChanged,
    )


    hgeCandidateRows = Property(
        "QVariantList", lambda self: self._hge_candidate_rows(),
        notify=CoreControllerMixin.hgeChanged,
    )


    hgeUnverifiedSummary = Property(
        "QVariantMap",
        lambda self: recent_unverified_hge_summary(self._hge_sightings),
        notify=CoreControllerMixin.hgeChanged,
    )


    traderPreference = Property(
        str, lambda self: self._trader_preference, notify=CoreControllerMixin.uiChanged,
    )


    @Slot()
    def updateSpanshCatalogs(self):
        """Refresh every catalog supported by the shared Spansh station API."""
        if self._trader_sync_busy or self._tech_broker_sync_busy:
            return
        self.updateTraderCatalog()
        self.updateTechBrokerCatalog()


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
