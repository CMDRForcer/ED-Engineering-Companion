"""Pure session projections derived from normalized Journal events."""

from datetime import datetime, timezone
from typing import Any


SESSION_HISTORY_LIMIT = 30
SESSION_COUNTER_KEYS = (
    "fsdJumps", "dockings", "engineerCrafts", "gradeCrafts",
    "experimentalCrafts", "materialTrades", "materialCollectedEvents",
)


def _timestamp_seconds(value: object) -> float | None:
    try:
        parsed = datetime.fromisoformat(
            str(value or "").strip().replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def _duration(start: object, end: object) -> int:
    start_seconds = _timestamp_seconds(start)
    end_seconds = _timestamp_seconds(end)
    if start_seconds is None or end_seconds is None:
        return 0
    return max(0, int(end_seconds - start_seconds))


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _nonnegative_float(value: object) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _new_session(event: dict[str, Any], index: int) -> dict[str, Any]:
    start = str(event.get("timestamp") or "")
    return {
        "id": start or f"session-{index}",
        "start": start, "end": "", "active": True,
        "durationSeconds": 0, "fsdJumps": 0, "distanceLy": 0.0,
        "dockings": 0, "engineerCrafts": 0, "gradeCrafts": 0,
        "experimentalCrafts": 0, "materialTrades": 0,
        "materialCollectedEvents": 0, "visitedSystems": 0,
        "_systems": set(),
    }


def public_session(
    session: dict[str, Any], end: object | None = None,
) -> dict[str, Any]:
    """Return a QML-safe session without its internal system set."""
    result = {key: value for key, value in session.items() if key != "_systems"}
    result["visitedSystems"] = len(session.get("_systems", set()))
    if end is not None:
        result["durationSeconds"] = _duration(result.get("start"), end)
    result["distanceLy"] = round(float(result.get("distanceLy", 0.0) or 0.0), 2)
    return result


def _append_history(
    history: list[dict[str, Any]], session: dict[str, Any], end: object,
) -> None:
    completed = public_session(session, end)
    completed["end"] = str(end or "")
    completed["active"] = False
    history[:] = [row for row in history if row.get("id") != completed["id"]]
    history.append(completed)
    del history[:-SESSION_HISTORY_LIMIT]


def apply_session_event(
    current: dict[str, Any] | None,
    history: list[dict[str, Any]],
    event: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    """Apply one Journal event to the current session accumulator."""
    name = str(event.get("event") or "")
    timestamp = str(event.get("timestamp") or "")
    if name == "LoadGame":
        if current:
            _append_history(history, current, timestamp)
        current = _new_session(event, index)
    if current is None:
        return None
    system = str(event.get("StarSystem") or "").strip()
    if system:
        current["_systems"].add(system)
    if name == "FSDJump":
        current["fsdJumps"] += 1
        try:
            current["distanceLy"] += max(
                0.0, float(event.get("JumpDist", 0) or 0)
            )
        except (TypeError, ValueError):
            pass
    elif name == "Docked":
        current["dockings"] += 1
    elif name == "EngineerCraft":
        current["engineerCrafts"] += 1
        if _nonnegative_int(event.get("Level")) > 0:
            current["gradeCrafts"] += 1
        if event.get("ExperimentalEffect") or event.get(
            "ExperimentalEffect_Localised"
        ):
            current["experimentalCrafts"] += 1
    elif name == "MaterialTrade":
        current["materialTrades"] += 1
    elif name == "MaterialCollected":
        current["materialCollectedEvents"] += 1
    if name == "Shutdown":
        _append_history(history, current, timestamp)
        return None
    return current


def normalize_session_history(payload: object) -> list[dict[str, Any]]:
    """Validate and bound persisted session rows."""
    if not isinstance(payload, list):
        return []
    rows = []
    for row in payload[-SESSION_HISTORY_LIMIT:]:
        if not isinstance(row, dict) or not str(row.get("id") or ""):
            continue
        normalized = {
            "id": str(row.get("id") or ""),
            "start": str(row.get("start") or ""),
            "end": str(row.get("end") or ""),
            "active": False,
            "durationSeconds": _nonnegative_int(row.get("durationSeconds")),
            "distanceLy": round(_nonnegative_float(row.get("distanceLy")), 2),
            "visitedSystems": _nonnegative_int(row.get("visitedSystems")),
        }
        for key in SESSION_COUNTER_KEYS:
            normalized[key] = _nonnegative_int(row.get(key))
        rows.append(normalized)
    return rows
