"""Versioned, atomic persistence for material-trader type evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from ed_companion.trader_config import TRADER_TYPE_STALE_DAYS, trader_type_cache_path
from ed_companion.persistence import atomic_write


LOGGER = logging.getLogger(__name__)
CACHE_VERSION = 1
TRADER_TYPES = {"raw", "manufactured", "encoded"}
CONFIDENCES = {"confirmed", "external", "heuristic"}
SOURCES = {"journal_confirmed", "external_api:spansh", "heuristic_economy"}


def normalize_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or isinstance(value.get("market_id"), bool):
        return None
    try:
        market_id = int(value.get("market_id"))
    except (TypeError, ValueError):
        return None
    confidence = value.get("confidence")
    source = value.get("source")
    stale_after = value.get("stale_after_days")
    if (
        market_id <= 0
        or value.get("trader_type") not in TRADER_TYPES
        or confidence not in CONFIDENCES
        or source not in SOURCES
        or normalize_timestamp(value.get("event_timestamp")) is None
        or normalize_timestamp(value.get("updated_at")) is None
        or stale_after != TRADER_TYPE_STALE_DAYS[confidence]
        or (value.get("system") is not None and not isinstance(value.get("system"), str))
        or (value.get("station") is not None and not isinstance(value.get("station"), str))
    ):
        return None
    entry = dict(value)
    entry["market_id"] = market_id
    return entry


class TraderTypeCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else trader_type_cache_path()
        self.entries: dict[str, dict[str, Any]] = {}

    def load(self) -> "TraderTypeCache":
        self.entries = {}
        if not self.path.exists():
            return self
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Trader type cache rejected: %s", exc)
            return self
        if not isinstance(document, Mapping) or document.get("version") != CACHE_VERSION:
            LOGGER.warning("Trader type cache rejected: unsupported schema")
            return self
        rows = document.get("entries")
        if not isinstance(rows, Mapping):
            LOGGER.warning("Trader type cache rejected: entries is not an object")
            return self
        for key, value in rows.items():
            entry = _valid_entry(value)
            if entry is None or str(entry["market_id"]) != str(key):
                LOGGER.warning("Invalid trader type cache entry discarded: %s", key)
                continue
            self.entries[str(key)] = entry
        return self

    def save(self) -> None:
        payload = {"version": CACHE_VERSION, "entries": self.entries}
        atomic_write(self.path, json.dumps(payload, indent=2, sort_keys=True))

    def get(self, market_id: int) -> dict[str, Any] | None:
        return self.entries.get(str(market_id))

    def update(self, evidence: Mapping[str, Any], now: datetime | None = None) -> bool:
        timestamp = normalize_timestamp(evidence.get("event_timestamp"))
        if timestamp is None:
            return False
        confidence = evidence.get("confidence")
        candidate = _valid_entry({
            **evidence,
            "updated_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
            "stale_after_days": TRADER_TYPE_STALE_DAYS.get(str(confidence)),
        })
        if candidate is None:
            return False
        existing = self.get(candidate["market_id"])
        if existing:
            old_time = normalize_timestamp(existing["event_timestamp"])
            old_rank = {"heuristic": 1, "external": 2, "confirmed": 3}[existing["confidence"]]
            new_rank = {"heuristic": 1, "external": 2, "confirmed": 3}[candidate["confidence"]]
            if old_rank > new_rank or (old_rank == new_rank and old_time >= timestamp):
                return False
        self.entries[str(candidate["market_id"])] = candidate
        return True
