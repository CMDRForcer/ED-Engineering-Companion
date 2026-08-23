"""Pure display projections for the Phase 14 dashboards."""

from typing import Any

from .state import filter_logbook_entries


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
