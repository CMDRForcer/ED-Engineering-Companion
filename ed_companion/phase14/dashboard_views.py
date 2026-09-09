"""Pure display projections for the Phase 14 dashboards."""

from datetime import datetime, timezone
from typing import Any

from .state import filter_logbook_entries


FINANCE_PERIOD_SECONDS = {
    "1h": 60 * 60,
    "6h": 6 * 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


def _finance_timestamp(value: Any) -> datetime | None:
    try:
        observed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        return None
    return observed.astimezone(timezone.utc)


def build_finance_history(
    events: list[dict[str, Any]], limit: int = 180,
    current_credits: dict[str, Any] | None = None,
    credit_snapshots: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build an ordered ticker from authoritative Journal and live snapshots."""
    timeline = []
    sequence = 0

    def append(timestamp, source, credit_value=None, asset_value=None):
        nonlocal sequence
        observed = _finance_timestamp(timestamp)
        if observed is None:
            return
        timeline.append((
            observed, sequence, str(timestamp or ""), str(source or ""),
            credit_value, asset_value,
        ))
        sequence += 1

    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name == "LoadGame":
            value = event.get("Credits")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                append(event.get("timestamp"), "session_start", max(0, int(value)))
        elif name == "Statistics":
            bank = event.get("Bank_Account")
            bank = bank if isinstance(bank, dict) else {}
            value = bank.get("Current_Wealth")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                append(event.get("timestamp"), "asset_snapshot", None, max(0, int(value)))
    for snapshot in credit_snapshots or []:
        if not isinstance(snapshot, dict):
            continue
        value = snapshot.get("credits")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            append(
                snapshot.get("timestamp") or snapshot.get("observedAt"),
                snapshot.get("source") or "live_balance",
                max(0, int(value)),
            )
    live = current_credits if isinstance(current_credits, dict) else {}
    value = live.get("value")
    if (
        live.get("known")
        and isinstance(value, (int, float)) and not isinstance(value, bool)
    ):
        append(live.get("timestamp"), "live_balance", max(0, int(value)))

    timeline.sort(key=lambda item: (item[0], item[1]))
    credits: int | None = None
    assets: int | None = None
    rows: list[dict[str, Any]] = []
    for _observed, _sequence, timestamp, source, credit_value, asset_value in timeline:
        changed = False
        if credit_value is not None and credit_value != credits:
            credits = credit_value
            changed = True
        if asset_value is not None and asset_value != assets:
            assets = asset_value
            changed = True
        if not changed:
            continue
        row = {
            "timestamp": timestamp,
            "credits": credits if credits is not None else -1,
            "assets": assets if assets is not None else -1,
            "source": source,
        }
        rows.append(row)
    if limit <= 0 or len(rows) <= limit:
        return rows
    step = (len(rows) - 1) / float(limit - 1)
    return [rows[round(index * step)] for index in range(limit)]


def filter_finance_history(
    rows: list[dict[str, Any]], period: str,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Limit finance points to a fixed window or the latest game session."""
    period = str(period or "all").casefold()
    if period == "all":
        return list(rows or [])
    points = sorted(
        (
            (observed, row)
            for row in rows or [] if isinstance(row, dict)
            if (observed := _finance_timestamp(row.get("timestamp"))) is not None
        ),
        key=lambda item: item[0],
    )
    if not points:
        return []
    if period == "session":
        session_starts = [
            observed
            for event in events or [] if isinstance(event, dict)
            and str(event.get("event") or "") == "LoadGame"
            if (observed := _finance_timestamp(event.get("timestamp"))) is not None
        ]
        if not session_starts:
            return []
        boundary = max(session_starts)
        return [dict(row) for observed, row in points if observed >= boundary]
    duration = FINANCE_PERIOD_SECONDS.get(period)
    if duration is None:
        return list(rows or [])
    boundary = points[-1][0].timestamp() - duration
    included = [(observed, row) for observed, row in points
                if observed.timestamp() >= boundary]
    prior = next(
        ((observed, row) for observed, row in reversed(points)
         if observed.timestamp() < boundary),
        None,
    )
    if prior:
        anchor = dict(prior[1])
        anchor["timestamp"] = datetime.fromtimestamp(
            boundary, timezone.utc
        ).isoformat().replace("+00:00", "Z")
        anchor["source"] = "period_anchor"
        included.insert(0, (datetime.fromtimestamp(boundary, timezone.utc), anchor))
    return [dict(row) for _observed, row in included]


def build_finance_summary(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe credit change over the real wall-clock span in the chart."""
    points = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = row.get("credits")
        if not isinstance(value, (int, float)) or isinstance(value, bool) \
                or value < 0:
            continue
        observed = _finance_timestamp(row.get("timestamp"))
        if observed is None:
            continue
        points.append((observed, int(value), row))
    points.sort(key=lambda item: item[0])
    if not points:
        return {
            "known": False, "rateKnown": False, "sampleCount": 0,
            "startTimestamp": "", "endTimestamp": "",
            "durationSeconds": 0, "change": 0, "averagePerHour": 0.0,
        }
    start, start_value, start_row = points[0]
    end, end_value, end_row = points[-1]
    duration = max(0.0, (end - start).total_seconds())
    change = end_value - start_value
    return {
        "known": True,
        "rateKnown": duration > 0,
        "sampleCount": len(points),
        "startTimestamp": str(start_row.get("timestamp") or ""),
        "endTimestamp": str(end_row.get("timestamp") or ""),
        "durationSeconds": int(duration),
        "change": change,
        "averagePerHour": change / (duration / 3600.0) if duration else 0.0,
    }


def build_commander_cards(
    overview: dict[str, Any], events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build display-only CMDR cards from normalized state and Journal rows."""
    ship = {"type": "", "name": "", "system": "", "station": ""}
    minor: dict[str, float] = {}
    squadron = {"name": "", "role": ""}
    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or "")
        if name in {"LoadGame", "Loadout"}:
            ship["type"] = str(event.get("Ship") or ship["type"])
            ship["name"] = str(event.get("ShipName") or ship["name"])
        if name in {"Location", "Docked", "FSDJump", "CarrierJump"}:
            ship["system"] = str(event.get("StarSystem") or ship["system"])
            if name in {"Location", "Docked"}:
                ship["station"] = str(event.get("StationName") or "")
            elif name in {"FSDJump", "CarrierJump"}:
                ship["station"] = ""
            for faction in event.get("Factions", []) or []:
                if (
                    isinstance(faction, dict)
                    and faction.get("Name")
                    and faction.get("MyReputation") is not None
                ):
                    minor[str(faction["Name"])] = float(
                        faction["MyReputation"]
                    )
        if name in {"SquadronStartup", "SquadronCreated"}:
            squadron["name"] = str(
                event.get("SquadronName")
                or event.get("Name")
                or squadron["name"]
            )
            squadron["role"] = str(
                event.get("CurrentRank")
                or event.get("Rank")
                or squadron["role"]
            )

    def card(title: str, tone: str, rows: list[dict[str, Any]], empty: str):
        return {"title": title, "tone": tone, "rows": rows, "empty": empty}

    return {
        "ranks": card("RANKS", "cyan", [{
            "label": row.get("label", "RANK"),
            "value": (
                f"RANK {row.get('rank')}" if row.get("known") else "UNKNOWN"
            ),
            "detail": (
                f"{row.get('progress', 0)}% TO NEXT RANK"
                if row.get("progressKnown") else "PROGRESS UNKNOWN"
            ),
        } for row in overview.get("ranks", [])], "NO RANK SNAPSHOT"),
        "major-reputation": card("MAJOR-FACTION REPUTATION", "green", [{
            "label": row.get("label", "FACTION"),
            "value": (
                f"{float(row.get('value', 0)):.1f}%"
                if row.get("known") else "UNKNOWN"
            ),
            "detail": "JOURNAL REPUTATION",
        } for row in overview.get("reputations", [])], "NO MAJOR-FACTION DATA"),
        "finances": card("FINANCIAL SNAPSHOTS", "orange", [{
            "label": label,
            "value": (
                f"{int(snapshot.get('value', 0)):,} CR"
                if snapshot.get("known") else "UNKNOWN"
            ),
            "detail": str(snapshot.get("timestamp") or "NO JOURNAL SNAPSHOT"),
        } for label, snapshot in (
            ("CREDITS", overview.get("credits", {})),
            ("ASSETS", overview.get("assets", {})),
        )], "NO FINANCIAL SNAPSHOT"),
        "current-ship": card("CURRENT SHIP", "cyan", ([{
            "label": ship["type"] or "SHIP",
            "value": ship["name"] or "UNNAMED",
            "detail": " · ".join(
                value for value in (ship["system"], ship["station"]) if value
            ),
        }] if ship["type"] or ship["name"] else []), "NO CURRENT SHIP DATA"),
        "minor-reputation": card("MINOR-FACTION REPUTATION", "green", [
            {"label": name, "value": f"{value:.1f}%", "detail": "LOCAL JOURNAL"}
            for name, value in sorted(minor.items())
        ], "NO MINOR-FACTION DATA"),
        "squadron": card("SQUADRON", "cyan", ([{
            "label": "SQUADRON",
            "value": squadron["name"],
            "detail": squadron["role"] or "ROLE UNKNOWN",
        }] if squadron["name"] else []), "NO SQUADRON DATA"),
    }


def decorate_logbook_entry(
    row: dict[str, Any], notes: dict[str, str],
) -> dict[str, Any]:
    """Attach a profile note to one normalized Logbook row."""
    decorated = dict(row)
    note = notes.get(str(row.get("id") or ""), "")
    decorated["note"] = note
    if note:
        decorated["searchText"] = (
            f"{decorated.get('searchText', '')} {note.casefold()}".strip()
        )
    return decorated


def build_logbook_view(
    rows: list[dict[str, Any]], notes: dict[str, str],
    category: str, query: str,
) -> list[dict[str, Any]]:
    """Decorate and filter normalized Logbook rows for QML."""
    decorated = [decorate_logbook_entry(row, notes) for row in rows]
    return filter_logbook_entries(decorated, category, query)
