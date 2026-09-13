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


class InaraMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_inara() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    inaraFinished = Signal(object)


    inaraJournalScanReady = Signal(object)


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


    inaraCommander = Property(
        str, lambda self: str(self._inara_config.get("commander_name") or ""),
        notify=CoreControllerMixin.connectionChanged,
    )


    inaraApiKey = Property(
        str, lambda self: str(self._inara_config.get("api_key") or ""),
        notify=CoreControllerMixin.connectionChanged,
    )


    inaraConsent = Property(
        bool, lambda self: bool(self._inara_config.get("consent")),
        notify=CoreControllerMixin.connectionChanged,
    )


    inaraAutoSync = Property(
        bool, lambda self: bool(self._inara_config.get("auto_sync")),
        notify=CoreControllerMixin.connectionChanged,
    )


    inaraKeyConfigured = Property(
        bool, lambda self: bool(self._inara_config.get("api_key")),
        notify=CoreControllerMixin.connectionChanged,
    )


    inaraStatus = Property(
        str, lambda self: self._inara_status, notify=CoreControllerMixin.connectionChanged,
    )


    inaraBusy = Property(
        bool, lambda self: self._inara_busy, notify=CoreControllerMixin.connectionChanged,
    )


    inaraReceipts = Property(
        "QVariantList", lambda self: self._inara_receipts,
        notify=CoreControllerMixin.connectionChanged,
    )


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



    def _init_inara(self):
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
