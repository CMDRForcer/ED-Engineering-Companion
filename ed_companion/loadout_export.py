"""Anonymous, round-trip-safe exports of an Elite Journal ship loadout."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ed_companion.build_import import resolve_experimental_name


FORMAT_ID = "EDOPS_LOADOUT_V1"


def _module_id(value: object) -> str:
    symbol = str(value or "").strip().strip("$;")
    if symbol.casefold().endswith("_name"):
        symbol = symbol[:-5]
    return symbol.casefold()


def _ordered(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event for _index, event in sorted(
            enumerate(event for event in (events or []) if isinstance(event, dict)),
            key=lambda row: (str(row[1].get("timestamp") or ""), row[0]),
        )
    ]


def build_loadout_export(
    events: list[dict[str, Any]], ship_id: object, ship_type: str,
    partial_slots: list[dict[str, Any]], experimentals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return outfitting truth without Commander, profile, or local ShipID data."""
    wanted = str(ship_id or "")
    ordered = _ordered(events)
    snapshots = [
        event for event in ordered
        if event.get("event") == "Loadout"
        and str(event.get("ShipID") or "") == wanted
        and isinstance(event.get("Modules"), list)
    ]
    snapshot = snapshots[-1] if snapshots else {}
    snapshot_modules = {
        str(module.get("Slot") or ""): module
        for module in (snapshot.get("Modules") or [])
        if isinstance(module, dict) and module.get("Slot") and module.get("Item")
    }
    current_slots = {
        str(row.get("slot") or ""): str(row.get("moduleId") or "")
        for row in (partial_slots or []) if row.get("slot") and row.get("moduleId")
    }
    snapshot_index = max(
        (index for index, event in enumerate(ordered) if event is snapshot),
        default=-1,
    )
    later_craft = any(
        event.get("event") == "EngineerCraft"
        and str(event.get("ShipID") or "") == wanted
        for event in ordered[snapshot_index + 1:]
    ) if snapshot_index >= 0 else False
    physical_match = bool(snapshot_modules) and {
        slot: _module_id(module.get("Item"))
        for slot, module in snapshot_modules.items()
    } == {slot: _module_id(item) for slot, item in current_slots.items()}
    complete = physical_match and not later_craft
    modules = []
    unresolved_experimental = False
    for slot, item in current_slots.items():
        snapshot_module = snapshot_modules.get(slot, {})
        if _module_id(snapshot_module.get("Item")) == _module_id(item):
            exported_module = deepcopy(snapshot_module)
            engineering = exported_module.get("Engineering")
            engineering = engineering if isinstance(engineering, dict) else {}
            raw_effect = str(
                engineering.get("ExperimentalEffect")
                or exported_module.get("ExperimentalEffect") or ""
            ).strip()
            localized_effect = str(
                engineering.get("ExperimentalEffect_Localised")
                or exported_module.get("ExperimentalEffect_Localised") or ""
            ).strip()
            if raw_effect or localized_effect:
                readable_effect = resolve_experimental_name(
                    raw_effect, experimentals or [], localized_effect
                )
                for container in (engineering, exported_module):
                    container.pop("ExperimentalEffect", None)
                    container.pop("ExperimentalEffect_Localised", None)
                if readable_effect:
                    target = engineering if engineering else exported_module
                    target["ExperimentalEffect"] = readable_effect
                    target["ExperimentalEffect_Localised"] = readable_effect
                else:
                    target = engineering if engineering else exported_module
                    target["ExperimentalEffectStatus"] = "UNRESOLVED"
                    unresolved_experimental = True
            modules.append(exported_module)
        else:
            modules.append({"Slot": slot, "Item": item})
    journal_ship = next((
        str(event.get("Ship") or event.get("ShipType") or "").strip()
        for event in reversed(ordered)
        if str(event.get("ShipID") or event.get("NewShipID") or "") == wanted
        and (event.get("Ship") or event.get("ShipType"))
    ), "")
    resolved_ship = str(snapshot.get("Ship") or journal_ship or ship_type or "Unknown").strip()
    complete = complete and not unresolved_experimental
    return {
        "format": FORMAT_ID,
        "status": "COMPLETE" if complete else "PARTIAL",
        "exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "latest complete Journal Loadout" if complete else "current slots with partial Journal detail",
        "Ship": resolved_ship,
        "Modules": modules,
    }


def loadout_text(payload: dict[str, Any]) -> str:
    lines = [
        "EDEC SHIP OUTFITTING",
        f"STATUS · {payload.get('status', 'PARTIAL')}",
        f"SHIP · {payload.get('Ship') or 'Unknown'}",
        f"MODULES · {len(payload.get('Modules') or [])}",
        "",
    ]
    for module in payload.get("Modules") or []:
        if not isinstance(module, dict):
            continue
        slot = str(module.get("Slot") or "UNKNOWN SLOT")
        item = str(module.get("Item") or module.get("Name") or "UNKNOWN MODULE")
        detail = [f"{slot} · {item}"]
        engineering = module.get("Engineering")
        engineering = engineering if isinstance(engineering, dict) else {}
        blueprint = str(
            engineering.get("BlueprintName") or module.get("BlueprintName") or ""
        )
        grade = engineering.get("Level", module.get("Level"))
        quality = engineering.get("Quality", module.get("Quality"))
        experimental = str(
            engineering.get("ExperimentalEffect_Localised")
            or engineering.get("ExperimentalEffect")
            or module.get("ExperimentalEffect_Localised")
            or module.get("ExperimentalEffect") or ""
        )
        if blueprint:
            detail.append(f"ENGINEERING {blueprint}" + (f" G{grade}" if grade not in (None, "") else ""))
        if quality not in (None, ""):
            detail.append(f"QUALITY {quality}")
        if experimental:
            detail.append(f"EXPERIMENTAL {experimental}")
        lines.append("  ·  ".join(detail))
    if payload.get("status") == "PARTIAL":
        lines.extend(("", "PARTIAL · No complete Journal Loadout was available; missing engineering details were not guessed."))
    return "\n".join(lines) + "\n"


def write_loadout_export(
    export_dir: Path, safe_ship: str, payload: dict[str, Any]
) -> tuple[Path, Path]:
    export_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_ship or "ship"
    json_path = export_dir / f"{stem}_outfitting.json"
    text_path = export_dir / f"{stem}_outfitting.txt"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    text_path.write_text(loadout_text(payload), encoding="utf-8")
    return json_path, text_path
