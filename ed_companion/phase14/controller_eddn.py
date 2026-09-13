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
EDDN_ACTIVE_RECEIPT_LIMIT = 100
from .controller_core import CoreControllerMixin


def _eddn_relay_relevant(payload: Any) -> bool:
    """Keep only relay frames consumed by State Finds or Mining Finder."""
    if not isinstance(payload, dict):
        return False
    schema = str(payload.get("$schemaRef") or "").casefold()
    if "/fsssignaldiscovered/" in schema:
        return True
    if "/fssbodysignals/" in schema:
        return True
    message = payload.get("message")
    return (
        "/journal/1" in schema
        and isinstance(message, dict)
        and str(message.get("event") or "") in {
            "FSDJump", "Location", "CarrierJump", "Scan", "SAASignalsFound",
        }
    )


class EddnMixin:
    """Extracted from CockpitController (controller.py modularization).

    Call self._init_eddn() from CockpitController.__init__() at the
    exact point the extracted lines used to occupy - this avoids relying
    on cooperative super().__init__() ordering across mixins, which would
    be fragile here given real temporal setup dependencies between domains.
    """

    eddnFinished = Signal(str, bool, str)


    eddnRelay = Signal(object)


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


    def _load_eddn_cursor_state(self) -> None:
        cursor = self._read_local_json(self.eddn_cursor_file, {})
        if not isinstance(cursor, dict):
            cursor = {}
        self._station_fingerprints = cursor.pop("__station_files__", {})
        self._navroute_fingerprint = str(
            cursor.pop("__navroute_file__", "") or ""
        )
        self._eddn_baseline_established = bool(
            cursor.pop("__baseline_established__", False)
        )
        stored_root = str(cursor.pop("__journal_root__", "") or "")
        self._journal_offsets = cursor if stored_root == self._eddn_journal_root else {}
        if stored_root == self._eddn_journal_root:
            # Pre-marker cursors with the same root already represent opt-in.
            self._eddn_baseline_established = True
        else:
            self._station_fingerprints = {}
            self._navroute_fingerprint = ""
            self._eddn_baseline_established = False
        if not isinstance(self._station_fingerprints, dict):
            self._station_fingerprints = {}


    def _save_eddn(self):
        active, historical = partition_upload_queue(
            self._eddn_queue, EDDN_ACTIVE_RECEIPT_LIMIT
        )
        if self._archive_history("eddn_sent", historical, key_field="id"):
            self._eddn_queue = active
        config_saved = atomic_write(
            self.eddn_config_file, json.dumps(self._eddn_config, indent=2)
        )
        queue_saved = atomic_write(
            self.eddn_queue_file, json.dumps(self._eddn_queue, indent=2)
        )
        self._eddn_queue_persist_pending = not queue_saved
        if not config_saved:
            LOGGER.error("EDDN config could not be persisted")
        if not queue_saved:
            self._eddn_status = (
                "EDDN queue could not be persisted; Journal cursor was not advanced."
            )
            LOGGER.error(self._eddn_status)
            signal = getattr(self, "connectionChanged", None)
            if signal is not None:
                signal.emit()
        return queue_saved


    def _load_eddn_queue(self):
        """Repair safe legacy jobs and isolate only irrecoverable records."""
        stored = self._read_local_json(self.eddn_queue_file, [])
        interrupted_count = sum(
            1 for job in stored
            if isinstance(job, dict) and job.get("status") == "sending"
        ) if isinstance(stored, list) else 0
        if interrupted_count:
            LOGGER.warning(
                "EDDN recovered %d interrupted sending job(s); gateway acceptance "
                "is unknown and retry may produce a duplicate",
                interrupted_count,
            )
        jobs = normalize_upload_queue(stored)
        previous = self._read_local_json(self.eddn_quarantine_file, [])
        if not isinstance(previous, list):
            previous = []
        candidates = [(job, None) for job in jobs]
        candidates.extend(
            (row.get("job"), row)
            for row in previous if isinstance(row, dict)
        )
        valid_by_id = {}
        quarantine = []
        repaired_count = 0
        for job, quarantine_row in candidates:
            try:
                if str(job.get("target") or "EDDN") != "EDDN":
                    raise EddnError("Queue target is not EDDN.", terminal=True)
                if str(job.get("status") or "queued") not in {
                    "queued", "retry", "sending", "sent", "failed",
                }:
                    raise EddnError("Queue status is unknown.", terminal=True)
                validate_eddn_prepared(job.get("event"))
            except (EddnError, AttributeError, TypeError) as exc:
                repaired = repair_legacy_eddn_prepared(
                    job.get("event") if isinstance(job, dict) else None
                )
                if repaired is None:
                    quarantine.append(quarantine_row or {
                        "quarantined_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                        "reason": str(exc),
                        "job": job,
                    })
                    continue
                job = dict(job)
                job["event"] = repaired
                digest = hashlib.sha256(json.dumps(
                    repaired, sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest()
                job["id"] = f"EDDN-{digest}"
                job.pop("last_error", None)
                job.pop("terminal_error", None)
                repaired_count += 1
            job_id = str(job.get("id") or "")
            if not job_id:
                digest = hashlib.sha256(json.dumps(
                    job["event"], sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest()
                job_id = f"EDDN-{digest}"
                job["id"] = job_id
            existing = valid_by_id.get(job_id)
            if existing is None or (
                existing.get("status") != "sent" and job.get("status") == "sent"
            ):
                valid_by_id[job_id] = job
        valid = list(valid_by_id.values())
        queue_changed = valid != stored
        quarantine_changed = quarantine != previous
        if queue_changed:
            atomic_write(self.eddn_queue_file, json.dumps(valid, indent=2))
        if quarantine_changed:
            atomic_write(
                self.eddn_quarantine_file,
                json.dumps(quarantine, indent=2),
            )
        if repaired_count:
            LOGGER.warning(
                "EDDN repaired %d legacy queue job(s) with the current public allowlist",
                repaired_count,
            )
        self._eddn_quarantine_error_groups = {}
        for row in quarantine:
            if not isinstance(row, dict):
                continue
            job = row.get("job") if isinstance(row.get("job"), dict) else {}
            prepared = job.get("event") if isinstance(job.get("event"), dict) else {}
            key = f"{prepared.get('schema') or 'unknown'} | {row.get('reason') or 'unknown'}"
            self._eddn_quarantine_error_groups[key] = (
                self._eddn_quarantine_error_groups.get(key, 0) + 1
            )
        return valid


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


    def _eddn_quarantine_view(self):
        """Expose grouped reasons only; quarantined payloads remain private."""
        rows = []
        for key, count in sorted(
            getattr(self, "_eddn_quarantine_error_groups", {}).items(),
            key=lambda item: (-item[1], item[0]),
        ):
            schema, separator, reason = key.partition(" | ")
            rows.append({
                "schema": schema,
                "reason": reason if separator else "Validation failed",
                "count": int(count),
                "status": "IRREPARABLE",
            })
        return rows


    def _eddn_delivery_summary(self):
        counts = {key: 0 for key in ("queued", "retry", "sending", "sent", "failed")}
        history_counts = self._history_counts()
        archived_sent = int(history_counts.get("eddn_sent", 0) or 0)
        schema_counts = {}
        error_groups = {}
        for job in self._eddn_queue:
            status = str(job.get("status") or "")
            if status in counts:
                counts[status] += 1
            prepared = job.get("event") if isinstance(job.get("event"), dict) else {}
            schema = str(prepared.get("schema") or "unknown")
            schema_counts[schema] = schema_counts.get(schema, 0) + 1
            error = str(job.get("last_error") or "").strip()
            if error:
                key = f"{schema} | {error}"
                error_groups[key] = error_groups.get(key, 0) + 1
        counts["sent"] += archived_sent
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
        last_success_at = str(
            persisted.get("sentAt") or queued_sent.get("sent_at") or ""
        )
        last_not_shareable = str(
            self._eddn_config.get("last_not_shareable") or ""
        )
        last_not_shareable_at = str(
            self._eddn_config.get("last_not_shareable_at") or ""
        )
        if last_success_at and last_not_shareable_at:
            try:
                success_time = datetime.fromisoformat(
                    last_success_at.replace("Z", "+00:00")
                )
                rejected_time = datetime.fromisoformat(
                    last_not_shareable_at.replace("Z", "+00:00")
                )
                if success_time >= rejected_time:
                    last_not_shareable = ""
            except (TypeError, ValueError):
                pass
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
        sent_times = []
        for job in self._eddn_queue:
            if job.get("status") != "sent" or not job.get("sent_at"):
                continue
            try:
                sent_times.append(datetime.fromisoformat(
                    str(job["sent_at"]).replace("Z", "+00:00")
                ).timestamp())
            except (TypeError, ValueError, OverflowError):
                continue
        sent_times = sorted(sent_times)[-30:]
        throughput = 0.0
        if len(sent_times) >= 2 and sent_times[-1] > sent_times[0]:
            throughput = 60.0 * (len(sent_times) - 1) / (
                sent_times[-1] - sent_times[0]
            )
        waiting = counts["queued"] + counts["retry"] + counts["sending"]
        eta_seconds = int(waiting * 60 / throughput) if throughput > 0 else 0
        current = next((
            job for job in self._eddn_queue if job.get("status") == "sending"
        ), {})
        current_event = current.get("event") if isinstance(current.get("event"), dict) else {}
        profile_consistent = (
            self.eddn_queue_file.parent == self.config_dir
            and all(
                job.get("profile_key") in {None, "", self._eddn_profile_key}
                for job in self._eddn_queue if isinstance(job, dict)
            )
        )
        return {
            **counts,
            "waiting": waiting,
            "lastSuccessAt": last_success_at,
            "lastSuccessSchema": str(persisted.get("schema") or sent_event.get("schema") or ""),
            "lastSuccessEvent": str(persisted.get("eventName") or sent_message.get("event") or ""),
            "lastError": str(failed.get("last_error") or ""),
            "lastNotShareable": last_not_shareable,
            "nextRetryAt": next_retry_at,
            "schemaCounts": schema_counts,
            "errorGroups": error_groups,
            "quarantineErrorGroups": dict(
                getattr(self, "_eddn_quarantine_error_groups", {})
            ),
            "quarantined": sum(
                getattr(self, "_eddn_quarantine_error_groups", {}).values()
            ),
            "cooldownActive": bool(next_retry_epoch > time.time()),
            "currentSchema": str(current_event.get("schema") or ""),
            "throughputPerMinute": round(throughput, 1),
            "etaSeconds": eta_seconds,
            "profileKey": self._eddn_profile_key,
            "storageFile": f"profile-{self._eddn_profile_key}/community_upload_queue.json",
            "historyFile": f"profile-{self._eddn_profile_key}/data_history.sqlite3",
            "archived": sum(history_counts.values()),
            "archivedEddn": archived_sent,
            "archivedHge": int(history_counts.get("hge_observations", 0) or 0),
            "archivedInara": int(history_counts.get("inara_receipts", 0) or 0),
            "archivedMining": (
                int(history_counts.get("mining_observations", 0) or 0)
                + int(history_counts.get("mining_catalog", 0) or 0)
            ),
            "profileConsistent": profile_consistent,
        }


    def _eddn_station_snapshot_view(self, directory=None):
        """Describe the three Elite station snapshots without exposing contents."""
        directory = Path(directory) if directory is not None else journal_dir()
        rows = []
        for kind, filename, schemas in (
            ("MARKET", "Market.json", ("commodity/3",)),
            ("OUTFITTING", "Outfitting.json", ("outfitting/2", "outfitting/3")),
            ("SHIPYARD", "Shipyard.json", ("shipyard/2",)),
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
                and job["event"].get("schema") in schemas
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
                receipt = next((
                    receipts.get(schema, {}) for schema in schemas
                    if isinstance(receipts, dict)
                    and isinstance(receipts.get(schema), dict)
                    and str(receipts[schema].get("stationName") or "").casefold()
                        == row["station"].casefold()
                    and str(receipts[schema].get("timestamp") or "") == timestamp
                ), {})
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


    def _eddn_delivery_for_candidate(
        self, candidate, eddn_queue=None, eddn_config=None,
    ):
        evidence = str(candidate.get("evidence_kind") or "")
        if evidence not in {"LOCAL_JOURNAL", "ENTERED"}:
            return ""
        if candidate.get("details_unknown"):
            return "EDDN NOT SHAREABLE"
        config = self._eddn_config if eddn_config is None else eddn_config
        queue = self._eddn_queue if eddn_queue is None else eddn_queue
        if not eddn_upload_allowed(config):
            return "EDDN OFF"
        address = candidate.get("system_address")
        timestamp = self._state_find_timestamp(candidate.get("latest_timestamp"))
        for job in reversed(queue):
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


    eddnConsent = Property(
        bool, lambda self: bool(self._eddn_config.get("consent")),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnUploadEnabled = Property(
        bool, lambda self: bool(self._eddn_config.get("upload_enabled")),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnListenerEnabled = Property(
        bool, lambda self: bool(self._eddn_config.get("listener_enabled")),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnStatus = Property(
        str, lambda self: self._eddn_status, notify=CoreControllerMixin.connectionChanged,
    )


    eddnParity = Property(
        "QVariantMap", lambda self: schema_parity_report(),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnStationStatus = Property(
        str, lambda self: self._eddn_station_status_summary(),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnListenerStatus = Property(
        str, lambda self: self._eddn_listener_status,
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnBusy = Property(
        bool, lambda self: self._eddn_busy, notify=CoreControllerMixin.connectionChanged,
    )


    eddnQueue = Property(
        "QVariantList", lambda self: self._eddn_queue_view(),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnQuarantine = Property(
        "QVariantList", lambda self: self._eddn_quarantine_view(),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnDeliverySummary = Property(
        "QVariantMap", lambda self: self._eddn_delivery_summary(),
        notify=CoreControllerMixin.connectionChanged,
    )


    eddnStationSnapshots = Property(
        "QVariantList", lambda self: self._eddn_station_snapshot_view(),
        notify=CoreControllerMixin.connectionChanged,
    )


    @staticmethod
    def _eddn_initial_status(consent):
        """Match the Connections card's status badge from the very first frame.

        The badge is derived live from _eddn_config.get("consent"); the
        detail text must start from the same value instead of a hardcoded
        "disabled" default, or a returning user with EDDN already enabled
        sees ENABLED contradicted by "EDDN network access is disabled."
        until an unrelated status update happens to overwrite it.
        """
        return (
            "EDDN enabled from saved settings."
            if consent else "EDDN network access is disabled."
        )


    @Slot(bool, bool, bool)
    def saveEddnConfig(self, consent, upload_enabled, listener_enabled):
        consent = bool(consent)
        self._eddn_config.update({
            "consent": consent,
            "upload_enabled": consent and bool(upload_enabled),
            "listener_enabled": consent and bool(listener_enabled),
        })
        if (
            self._eddn_config["upload_enabled"]
            and not self._eddn_baseline_established
        ):
            self._baseline_eddn_journal_files()
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
        # State projection already maintains an incremental, profile-isolated
        # Journal cache. Reuse it instead of reparsing every Journal file.
        events = profiled_journal_events()
        context = rebuild_eddn_context(events, self._eddn_profile_identity)
        if self._eddn_profile_identity and not context.get("StarSystem"):
            LOGGER.warning(
                "EDDN context replay found no current system for profile %s",
                self._eddn_profile_key,
            )
        return context


    def _sync_eddn_profile(self) -> bool:
        return self._switch_profile_context(resolve_profile_context())


    def _save_eddn_cursor(self):
        cursor = dict(self._journal_offsets)
        cursor["__station_files__"] = dict(self._station_fingerprints)
        cursor["__navroute_file__"] = self._navroute_fingerprint
        cursor["__baseline_established__"] = self._eddn_baseline_established
        cursor["__journal_root__"] = self._eddn_journal_root
        atomic_write(self.eddn_cursor_file, json.dumps(cursor, indent=2))


    def _baseline_eddn_journal_files(self):
        """Start opt-in after existing Journal bytes; rotations start at zero."""
        for path in self._eddn_profile_journal_paths():
            try:
                self._journal_offsets[path.name] = path.stat().st_size
            except OSError:
                LOGGER.warning("EDDN could not initialize cursor for %s", path)
        self._eddn_baseline_established = True


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
        scan_ids = getattr(self, "_eddn_scan_job_ids", None)
        if (
            job_id in scan_ids if isinstance(scan_ids, set) else
            any(job.get("id") == job_id for job in self._eddn_queue)
        ):
            return
        scan_pending = getattr(self, "_eddn_scan_pending_count", None)
        pending_count = int(scan_pending) if isinstance(scan_pending, int) else sum(
            row.get("status") != "sent" for row in self._eddn_queue
            if isinstance(row, dict)
        )
        if pending_count >= EDDN_PENDING_JOB_LIMIT:
            if (
                getattr(self, "_eddn_batching_scan", False)
                and getattr(self, "_eddn_scan_queue_full_reported", False)
            ):
                return False
            self._eddn_scan_queue_full_reported = True
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
        if isinstance(scan_ids, set):
            scan_ids.add(job_id)
            self._eddn_scan_pending_count = pending_count + 1
        if not getattr(self, "_eddn_batching_scan", False):
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
        if not self._eddn_baseline_established:
            self._baseline_eddn_journal_files()
            self._save_eddn_cursor()
            self._scan_eddn_station_files()
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
        queue_changed = False
        offsets_before_scan = dict(self._journal_offsets)
        navroute_fingerprint_before_scan = self._navroute_fingerprint
        self._eddn_batching_scan = True
        self._eddn_scan_queue_full_reported = False
        scan_queue = getattr(self, "_eddn_queue", [])
        self._eddn_scan_job_ids = {
            str(job.get("id") or "") for job in scan_queue
            if isinstance(job, dict)
        }
        self._eddn_scan_pending_count = sum(
            job.get("status") != "sent" for job in scan_queue
            if isinstance(job, dict)
        )
        saturated = False
        for path in paths:
            if saturated:
                break
            try:
                size = path.stat().st_size
                # Files appearing after the opt-in baseline are Journal
                # rotations and must be consumed from their first byte.
                offset = int(self._journal_offsets.get(path.name, 0))
                if offset > size:
                    offset = 0
                _tail_committed, records = read_journal_tail_records(path, offset)
            except OSError:
                LOGGER.warning("EDDN Journal read failed for %s", path)
                continue
            committed = offset
            for line_start, line_end, event in records:
                if event is None:
                    committed = line_end
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
                        committed = line_end
                        continue
                prepared = prepare_eddn_event(event, self._eddn_context)
                if prepared:
                    if int(self._eddn_scan_pending_count or 0) >= EDDN_PENDING_JOB_LIMIT:
                        self._eddn_status = (
                            f"EDDN offline queue full ({EDDN_PENDING_JOB_LIMIT}); "
                            "Journal cursor paused before the next unsaved event."
                        )
                        if not self._eddn_scan_queue_full_reported:
                            LOGGER.warning(self._eddn_status)
                            self._eddn_scan_queue_full_reported = True
                        self.connectionChanged.emit()
                        committed = line_start
                        saturated = True
                        break
                    queued = self._enqueue_eddn(prepared)
                    queue_changed = queued is True or queue_changed
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
                committed = line_end
            self._journal_offsets[path.name] = committed
            changed = changed or committed != offset
        self._eddn_batching_scan = False
        self._eddn_scan_job_ids = None
        self._eddn_scan_pending_count = None
        self._eddn_scan_queue_full_reported = False
        queue_save_required = queue_changed or bool(
            getattr(self, "_eddn_queue_persist_pending", False)
        )
        queue_saved = True
        if queue_save_required:
            queue_saved = self._save_eddn()
            self._publish_eddn_delivery_change()
            self.connectionChanged.emit()
        if not queue_saved:
            self._journal_offsets = offsets_before_scan
            self._navroute_fingerprint = navroute_fingerprint_before_scan
            return
        if changed:
            self._save_eddn_cursor()
        if not saturated:
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
            queued = self._enqueue_eddn(prepared)
            if queued is not True:
                continue
            self._station_fingerprints[filename] = fingerprint
            self._station_rejections.pop(filename, None)
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
                    "retryAfter": exc.retry_after,
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
            # Persist the gateway acceptance before status/proof/UI work. A
            # process exit after this checkpoint will not replay the job.
            self._save_eddn()
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
            self._eddn_config["last_not_shareable"] = ""
            self._eddn_config["last_not_shareable_at"] = ""
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
                try:
                    status_code = int(result.get("statusCode"))
                except (TypeError, ValueError):
                    status_code = None
                try:
                    retry_after = max(0, int(result.get("retryAfter")))
                except (TypeError, ValueError):
                    retry_after = None
                delay = (
                    retry_after
                    if status_code == 429 and retry_after is not None
                    else max(
                        60,
                        min(900, 60 * (2 ** max(0, job["attempts"] - 1))),
                    )
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
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self.connectionChanged.emit()
        if success or job.get("status") == "failed":
            QTimer.singleShot(EDDN_REPLAY_DELAY_MS, self._process_eddn_queue)


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
    def clearEddnSent(self):
        proof = latest_delivery_proof(self._eddn_queue)
        if proof:
            self._eddn_config["last_success"] = proof
        self._eddn_queue = [
            row for row in self._eddn_queue if row.get("status") != "sent"
        ]
        try:
            self._history_archive.clear("eddn_sent")
        except (OSError, sqlite3.Error) as exc:
            LOGGER.error("EDDN sent history could not be cleared: %s", exc)
        self._save_eddn()
        self._publish_eddn_delivery_change()
        self.connectionChanged.emit()


    @Slot(object)
    def _accept_eddn_relay(self, payload):
        snapshot = extract_system_bgs_snapshot(payload)
        if snapshot:
            self._pending_bgs_snapshots.append(snapshot)
        self._pending_hge_observations.extend(extract_signal_finds(payload))
        self._pending_mining_candidates.extend(
            project_eddn_mining_candidates(
                payload, datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        )


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
                        if _eddn_relay_relevant(payload):
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
