"""Resolve material-trader types from timestamped evidence sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Mapping, Protocol

from .trader_type_cache import TraderTypeCache, normalize_timestamp


LOGGER = logging.getLogger(__name__)


class TraderTypeProvider(Protocol):
    def resolve(self, market_id: int) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class TraderTypeResolution:
    trader_type: str | None
    confidence: str
    source: str | None
    market_id: int
    event_timestamp: str | None
    is_stale: bool
    contradiction_info: str | None = None


def _is_stale(entry: Mapping[str, Any], now: datetime) -> bool:
    days = entry.get("stale_after_days")
    timestamp = normalize_timestamp(entry.get("updated_at"))
    return bool(days is not None and timestamp and now > timestamp + timedelta(days=int(days)))


def _resolution(
    market_id: int,
    entry: Mapping[str, Any] | None,
    now: datetime,
    contradiction: str | None = None,
) -> TraderTypeResolution:
    if not entry:
        return TraderTypeResolution(None, "unknown", None, market_id, None, False, contradiction)
    stale = _is_stale(entry, now)
    return TraderTypeResolution(
        None if stale else str(entry["trader_type"]),
        str(entry["confidence"]),
        str(entry["source"]),
        market_id,
        str(entry["event_timestamp"]),
        stale,
        contradiction,
    )


def resolve_trader_type(
    market_id: int,
    cache: TraderTypeCache,
    external_provider: TraderTypeProvider | None = None,
    *,
    now: datetime | None = None,
) -> TraderTypeResolution:
    """Resolve with deliberate priority confirmed > external > heuristic.

    External station classification describes the concrete trader subtype and
    therefore outranks an indirect station-economy inference.
    """
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cached = cache.get(market_id)
    if cached and cached.get("confidence") == "confirmed":
        contradiction = None
        external = external_provider.resolve(market_id) if external_provider else None
        if (
            external
            and int(external.get("market_id", 0) or 0) == market_id
            and external.get("trader_type") != cached.get("trader_type")
        ):
            contradiction = (
                f"{cached.get('source')}={cached.get('trader_type')} vs "
                f"{external.get('source')}={external.get('trader_type')}"
            )
            LOGGER.warning("Trader type contradiction for %s: %s", market_id, contradiction)
        return _resolution(market_id, cached, current_time, contradiction)

    external = external_provider.resolve(market_id) if external_provider else None
    contradiction = None
    if external:
        if int(external.get("market_id", 0) or 0) != market_id:
            LOGGER.warning("Spansh trader response MarketID mismatch for %s", market_id)
            external = None
        elif cached and cached.get("trader_type") != external.get("trader_type"):
            contradiction = (
                f"{cached.get('source')}={cached.get('trader_type')} vs "
                f"{external.get('source')}={external.get('trader_type')}"
            )
            LOGGER.warning("Trader type contradiction for %s: %s", market_id, contradiction)
    if external and cache.update(external, now=current_time):
        cached = cache.get(market_id)
    elif external and cached is None:
        cached = external

    return _resolution(market_id, cached, current_time, contradiction)
