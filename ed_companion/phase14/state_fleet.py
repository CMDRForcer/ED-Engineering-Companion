"""Extracted from ed_companion/phase14/state.py as part of the state.py
modularization refactor (no behavior change). Re-exported by state.py so
every existing import path keeps working unchanged."""

import json
import hashlib
import logging
import math
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from functools import lru_cache
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .session_views import (
    SESSION_HISTORY_LIMIT,
    apply_session_event,
    normalize_session_history,
    public_session,
)

from ed_companion.journal import (
    is_completed_engineer_craft,
    journal_material_name,
    material_event_changes,
    rebuild_fleet,
    trader_type_evidence_from_event,
    project_vehicle_state,
    project_latest_srv_mining_session,
)
from ed_companion.engineering import engineer_unlock_signals, load_unlock_catalog
from ed_companion.navigation import (
    build_trader_route,
    find_nearest_catalog_trader,
    extract_local_hge_sightings,
    extract_local_state_finds,
    is_hge_material,
    local_hge_scan_status,
    local_state_find_scan_status,
    merge_trader_catalog,
    plan_material_trades,
    spansh_trader_type_evidence,
    trade_batch,
    trade_matches_trader,
    TraderTypeCache,
    resolve_trader_type,
)
from ed_companion.navigation.trader import is_material_tradeable
from ed_companion.navigation.mining_finder import project_local_mining_evidence
from ed_companion.navigation.trader_type_cache import normalize_timestamp
from ed_companion.trader_config import HEURISTIC_TRADER_WARNING_KEY
from ed_companion.material_integrity import material_key
from ed_companion.module_identity import (
    canonical_module_id,
    module_identity_key,
    same_module_identity,
)
from ed_companion.persistence import atomic_write, load_json_file, persistence_issues
from ed_companion.build_import import (
    JOURNAL_BLUEPRINT_NAMES,
    JOURNAL_EXPERIMENTAL_NAMES,
)
from ed_companion.exobiology import (
    augmented_species_catalog,
    best_find,
    exobiology_carried_summary,
    exobiology_findings,
    exobiology_lifetime_earned,
    exobiology_session_summary,
    exobiology_summary,
    genus_completion,
    landing_targets,
    remaining_signals_at_body,
)
from .state_core import (
    BLUEPRINT_ID_CATALOG_PATH,
    ENGINEERING_CATEGORY_ORDER,
    ENGINEERING_MODULE_CATEGORIES,
    ENGINEERING_MODULE_ID_PREFIXES,
    ENGINEER_NAME_ALIASES,
    MATERIAL_CATEGORIES,
    PROGRESS_STATUS,
    ProfileContext,
    _CRAFT_BATCH_LOCK,
    _JOURNAL_EVENT_CACHE,
    _JOURNAL_EVENT_CACHE_LOCK,
    _JOURNAL_GLOB_CACHE,
    _JOURNAL_GLOB_CACHE_LOCK,
    _JOURNAL_GLOB_TTL_SECONDS,
    _JOURNAL_POLL_FILE_LIMIT,
    _UNLOCK_EVENT_CACHE,
    _fast_journal_profile_identity,
    _journal_guard,
    _journal_profile_identity,
    _journal_snapshot,
    _recent_journal_names,
    _write_json_if_changed,
    active_profile_identity,
    active_profile_key,
    app_data_dir,
    blueprint_id_evidence,
    blueprint_module_family,
    clear_journal_event_cache,
    current_cargo_event,
    engineer_progress_from_events,
    engineering_module_category,
    inventory_from_events,
    journal_change_signature,
    journal_change_summary,
    journal_dir,
    journal_events,
    journal_paths_for_profile,
    journal_unlock_events,
    latest_profile_location,
    learn_blueprint_id_catalog,
    load_blueprint_id_catalog,
    load_user_trader_catalog,
    normalize,
    profiled_journal_events,
    read_journal_tail,
    read_journal_tail_records,
    read_json,
    real_engineers,
    reference_data_dir,
    resolve_profile_context,
    runtime_data_dir,
    set_journal_dir,
    set_tech_broker_track,
    ship_journal_events,
    update_trader_type_evidence,
    user_trader_catalog_path,
)

LOGGER = logging.getLogger(__name__)



def reconcile_fleet_cache(
    data_dir: Path, fleet_state: dict[str, Any]
) -> tuple[dict[str, list[Any]], dict[str, str]]:
    """Migrate wishlist labels by ShipID and replace stale fleet metadata."""
    old_metadata = read_json(data_dir / "ship_metadata.json", {})
    old_plans = read_json(data_dir / "ship_blueprints.json", {})
    old_plans = old_plans if isinstance(old_plans, dict) else {}
    labels_by_id = {
        str(row["id"]): str(label)
        for label, row in (old_metadata.items() if isinstance(old_metadata, dict) else [])
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    live_by_id = {str(row["id"]): row for row in fleet_state.get("ships", [])}
    aliases: dict[str, str] = {}
    migrated: dict[str, list[Any]] = {}
    for old_label, plans in old_plans.items():
        ship_id = next(
            (key for key, label in labels_by_id.items() if label == old_label), ""
        )
        target = str(live_by_id.get(ship_id, {}).get("label") or old_label)
        aliases[str(old_label)] = target
        migrated.setdefault(target, []).extend(plans if isinstance(plans, list) else [])
    metadata = {
        str(row["label"]): {
            "id": int(row["id"]), "type": row["type"], "name": row["name"],
            "status": row["status"],
            "is_current": str(row["id"]) == str(fleet_state.get("active_id") or ""),
        }
        for row in fleet_state.get("ships", [])
    }
    _write_json_if_changed(data_dir / "ship_blueprints.json", migrated)
    _write_json_if_changed(data_dir / "ship_metadata.json", metadata)
    return migrated, aliases



def module_matches_type(module_id: object, blueprint_type: object) -> bool:
    module = normalize(canonical_module_id(module_id))
    wanted = normalize(blueprint_type)
    if not module or not wanted:
        return False
    if wanted == "armour":
        # Ship armour symbols are ship-specific (for example
        # federationcorvette_armour_grade5), so there is no common prefix.
        return "armour" in module
    prefixes = ENGINEERING_MODULE_ID_PREFIXES.get(wanted, ())
    if wanted == "pulselaser" and module.startswith("hptpulselaserburst"):
        return False
    return any(module.startswith(prefix) for prefix in prefixes)



def engineering_loadout_rows(
    module_slots: object, catalog_rows: object,
) -> list[dict[str, Any]]:
    """Project installed, engineerable modules into slot-first planner rows."""
    catalog = [row for row in (catalog_rows or []) if isinstance(row, dict)]
    modules: dict[str, dict[str, Any]] = {}
    for row in catalog:
        module = str(row.get("module") or "").strip()
        if not module:
            continue
        target = modules.setdefault(module, {
            "module": module,
            "category": str(row.get("category") or "Other"),
            "blueprintCount": 0,
        })
        target["blueprintCount"] += 1
    category_rank = {
        category: index for index, category in enumerate(ENGINEERING_CATEGORY_ORDER)
    }
    core_slot_labels = {
        "Armour": "CORE · ARMOUR",
        "PowerPlant": "CORE · POWER PLANT",
        "MainEngines": "CORE · THRUSTERS",
        "FrameShiftDrive": "CORE · FRAME SHIFT DRIVE",
        "LifeSupport": "CORE · LIFE SUPPORT",
        "PowerDistributor": "CORE · POWER DISTRIBUTOR",
        "Radar": "CORE · SENSORS",
        "FuelTank": "CORE · FUEL TANK",
    }
    result = []
    for slot_row in module_slots or []:
        if not isinstance(slot_row, dict):
            continue
        module_id = str(slot_row.get("moduleId") or "")
        match = next(
            (
                value for value in modules.values()
                if module_matches_type(module_id, value["module"])
            ),
            None,
        )
        if not match:
            continue
        _, size_rating = module_purchase_identity(module_id)
        slot = str(slot_row.get("slot") or "")
        result.append({
            **match,
            "slot": slot,
            "moduleId": module_id,
            "sizeRating": size_rating,
            "displaySlot": core_slot_labels.get(slot, slot),
            "bindingKey": f"{slot}\u241f{module_id}",
            "engineered": bool(slot_row.get("engineered")),
            "engineeringGrade": int(slot_row.get("engineeringGrade") or 0),
            "engineeringQuality": float(
                slot_row.get("engineeringQuality") or 0
            ),
            "engineeringQualityKnown": bool(
                slot_row.get("engineeringQualityKnown")
            ),
            "engineeringBlueprint": str(
                slot_row.get("engineeringBlueprint") or ""
            ),
            "experimentalEffect": str(
                slot_row.get("experimentalEffect") or ""
            ),
        })
    result.sort(key=lambda row: (
        category_rank.get(row["category"], len(category_rank)),
        row["slot"].casefold(),
        row["module"].casefold(),
    ))
    return result



@lru_cache(maxsize=1)
def _module_display_catalog() -> dict:
    path = Path(__file__).resolve().parents[2] / "ed_data" / "module_display.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["modules"]
    except (OSError, ValueError, KeyError):
        LOGGER.warning("Module display catalog unavailable: %s", path, exc_info=True)
        return {}



def module_purchase_identity(module_id: object) -> tuple[str, str]:
    """Return a purchase label and only a rating proven by the module ID."""
    symbol = canonical_module_id(module_id)
    catalog_identity = _module_display_catalog().get(symbol.casefold())
    normalized = normalize(symbol)
    identity = re.search(r"_size(\d+)_class(\d+)", symbol.casefold())
    rating_letters = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}
    size_rating = ""
    module_class = int(identity.group(2)) if identity else -1
    special_rating = next((
        ratings.get(module_class, "")
        for marker, ratings in (
            ("intbuggybay", {1: "H", 2: "G"}),
            ("intlargebuggybay", {3: "F"}),
            ("intmkiilargebuggybay", {3: "F"}),
            ("intcorrosionproofcargorack", {2: "F"}),
            ("intexpmodulestabiliser", {3: "F"}),
            ("intfighterbaymk2", {1: "D"}),
            ("intfighterbay", {1: "D"}),
            ("intlargecargorack", {1: "D"}),
            ("intmkiipassengercabin", {1: "D", 2: "C"}),
            ("intpassengercabin", {0: "E"}),
        )
        if marker.replace(" ", "") in normalized and module_class in ratings
    ), "")
    rating = special_rating or rating_letters.get(module_class, "")
    if identity and rating:
        size_rating = (
            f"{identity.group(1)}"
            f"{rating}"
        )
    if not size_rating:
        fixed_identity = {
            "intdockingcomputerstandard": "1E",
            "intdockingcomputeradvanced": "1E",
            "intsupercruiseassist": "1E",
            "intdronecontrolresourcesiphon": "1I",
            "intdronecontrolunkvesselresearch": "1E",
            "intstellarbodydiscoveryscannerstandard": "1E",
            "intstellarbodydiscoveryscannerintermediate": "1D",
            "intstellarbodydiscoveryscanneradvanced": "1C",
            "intdetailedsurfacescannertiny": "1I",
        }.get(normalized, "")
        size_only = re.search(r"size(\d+)", normalized)
        if fixed_identity:
            size_rating = fixed_identity
        elif size_only and normalized.startswith((
            "intguardianpowerplant", "intguardianpowerdistributor"
        )):
            size_rating = f"{size_only.group(1)}A"
        elif size_only and normalized.startswith("intguardianfsdbooster"):
            size_rating = f"{size_only.group(1)}H"
    labels = (
        ("miningvolleyrepeater", "MINING VOLLEY REPEATER"),
        ("miningtoolv2", "MINING VOLLEY REPEATER"),
        ("miningsubsurfdispmisle", "SUB-SURFACE DISPLACEMENT MISSILE"),
        ("miningseismchrgwarhd", "SEISMIC CHARGE LAUNCHER"),
        ("multidronecontrolminingmkii", "MK II MINING MULTI-LIMPET CONTROLLER"),
        ("multidronecontrolminingv2", "MK II MINING MULTI-LIMPET CONTROLLER"),
        ("multidronecontrolmining", "MINING MULTI-LIMPET CONTROLLER"),
        ("multidronecontroloperations", "OPERATIONS MULTI-LIMPET CONTROLLER"),
        ("multidronecontrolrescue", "RESCUE MULTI-LIMPET CONTROLLER"),
        ("multidronecontroluniversal", "UNIVERSAL MULTI-LIMPET CONTROLLER"),
        ("multidronecontrolxeno", "XENO MULTI-LIMPET CONTROLLER"),
        ("dronecontrolcollection", "COLLECTOR LIMPET CONTROLLER"),
        ("dronecontroldecontamination", "DECONTAMINATION LIMPET CONTROLLER"),
        ("dronecontrolfueltransfer", "FUEL TRANSFER LIMPET CONTROLLER"),
        ("dronecontrolprospector", "PROSPECTOR LIMPET CONTROLLER"),
        ("dronecontrolrecon", "RECON LIMPET CONTROLLER"),
        ("dronecontrolrepair", "REPAIR LIMPET CONTROLLER"),
        ("dronecontrolresourcesiphon", "HATCH BREAKER LIMPET CONTROLLER"),
        ("dronecontrolunkvesselresearch", "RESEARCH LIMPET CONTROLLER"),
        ("guardianpowerplant", "GUARDIAN HYBRID POWER PLANT"),
        ("guardianpowerdistributor", "GUARDIAN HYBRID POWER DISTRIBUTOR"),
        ("guardianfsdbooster", "GUARDIAN FRAME SHIFT DRIVE BOOSTER"),
        ("dockingcomputeradvanced", "ADVANCED DOCKING COMPUTER"),
        ("dockingcomputerstandard", "STANDARD DOCKING COMPUTER"),
        ("supercruiseassist", "SUPERCRUISE ASSIST"),
        ("detailedsurfacescanner", "DETAILED SURFACE SCANNER"),
        ("stellarbodydiscoveryscannerstandard", "BASIC DISCOVERY SCANNER"),
        ("stellarbodydiscoveryscannerintermediate", "INTERMEDIATE DISCOVERY SCANNER"),
        ("stellarbodydiscoveryscanneradvanced", "ADVANCED DISCOVERY SCANNER"),
        ("buggybaymkii", "MK II PLANETARY VEHICLE HANGAR"),
        ("hyperdriveovercharge", "FRAME SHIFT DRIVE (SCO)"),
        ("cloudscanner", "FRAME SHIFT WAKE SCANNER"),
        ("mrascanner", "PULSE WAVE ANALYSER"),
        ("mininglaser", "MINING LASER"),
        ("intpowerplant", "POWER PLANT"),
        ("intengine", "THRUSTERS"),
        ("inthyperdrive", "FRAME SHIFT DRIVE"),
        ("intlifesupport", "LIFE SUPPORT"),
        ("intpowerdistributor", "POWER DISTRIBUTOR"),
        ("intsensors", "SENSORS"),
        ("intfueltank", "FUEL TANK"),
        ("intcargorack", "CARGO RACK"),
        ("inthullreinforcement", "HULL REINFORCEMENT PACKAGE"),
        ("intmodulereinforcement", "MODULE REINFORCEMENT PACKAGE"),
        ("intshieldgenerator", "SHIELD GENERATOR"),
        ("intfuelscoop", "FUEL SCOOP"),
        ("intrepairer", "AUTO FIELD-MAINTENANCE UNIT"),
        ("intfighterbay", "FIGHTER HANGAR"),
        ("intbuggybay", "PLANETARY VEHICLE HANGAR"),
        ("planetaryapproachsuite", "PLANETARY APPROACH SUITE"),
        ("dronecontrol", "LIMPET CONTROLLER"),
        ("multicannon", "MULTI-CANNON"),
        ("pulselaser", "PULSE LASER"),
        ("beamlaser", "BEAM LASER"),
        ("shieldbooster", "SHIELD BOOSTER"),
        ("heatsink", "HEAT SINK LAUNCHER"),
        ("armour", "ARMOUR"),
    )
    name = (
        "BI-WEAVE SHIELD GENERATOR"
        if normalized.startswith("intshieldgenerator")
        and normalized.endswith("fast") else
        next((label for marker, label in labels if marker in normalized), "")
    )
    mount = next((
        label for marker, label in (
            ("turret", "TURRETED"),
            ("gimbal", "GIMBALLED"),
            ("fixed", "FIXED"),
        ) if marker in normalized
    ), "")
    if not name:
        name = (catalog_identity[0] if catalog_identity else
                re.sub(r"[_-]+", " ", symbol).upper())
    if catalog_identity:
        # Keep our explicit limpet subtype wording; older catalog labels can
        # be generic ("Limpet Control") or omit "Multi".
        if "dronecontrol" not in normalized:
            name = catalog_identity[0]
        size_rating = catalog_identity[1]
    if mount:
        name = f"{name} · {mount}"
    return name, size_rating



def ship_slot_layout(
    ship_data: object, module_slots: object, catalog_rows: object,
    desired_modules: object = None,
) -> list[dict[str, Any]]:
    """Build the selected hull's physical slots and overlay known modules."""
    ship = ship_data if isinstance(ship_data, dict) else {}
    installed_rows = {
        str(row.get("slot") or ""): row
        for row in (module_slots or []) if isinstance(row, dict)
        and row.get("slot")
    }
    engineerable = {
        str(row.get("slot") or ""): row
        for row in engineering_loadout_rows(module_slots, catalog_rows)
    }
    desired_by_slot = dict(
        desired_modules if isinstance(desired_modules, dict) else {}
    )
    desired_source_by_slot = {
        str(slot): str(slot) for slot in desired_by_slot
    }
    size_names = {1: "SMALL", 2: "MEDIUM", 3: "LARGE", 4: "HUGE"}
    # Older projections numbered every optional row, even where Frontier uses
    # a stable reserved-slot name. Translate those positional keys without
    # merging or guessing across positions.
    for index, spec in enumerate(ship.get("optional", []) or [], 1):
        if not isinstance(spec, dict):
            continue
        size = int(spec.get("size") or 0)
        legacy_slot = f"Slot{index:02d}_Size{size}"
        physical_slot = str(spec.get("name") or legacy_slot)
        if (
            physical_slot != legacy_slot
            and legacy_slot in desired_by_slot
            and physical_slot not in desired_by_slot
        ):
            desired_by_slot[physical_slot] = desired_by_slot.pop(legacy_slot)
            desired_source_by_slot[physical_slot] = (
                desired_source_by_slot.pop(legacy_slot, legacy_slot)
            )
    hardpoint_counts: dict[int, int] = {}
    for spec in ship.get("hardpoints", []) or []:
        if not isinstance(spec, dict):
            continue
        size = int(spec.get("size") or 0)
        hardpoint_counts[size] = hardpoint_counts.get(size, 0) + 1
        legacy_slot = (
            f"{size_names.get(size, 'UNKNOWN').title()}Hardpoint"
            f"{hardpoint_counts[size]}"
        )
        physical_slot = str(spec.get("name") or legacy_slot)
        if (
            physical_slot != legacy_slot
            and legacy_slot in desired_by_slot
            and physical_slot not in desired_by_slot
        ):
            desired_by_slot[physical_slot] = desired_by_slot.pop(legacy_slot)
            desired_source_by_slot[physical_slot] = (
                desired_source_by_slot.pop(legacy_slot, legacy_slot)
            )
    core_specs = (
        ("Armour", "ARMOUR", 0),
        ("PowerPlant", "POWER PLANT", ship.get("core", {}).get("powerPlant")),
        ("MainEngines", "THRUSTERS", ship.get("core", {}).get("thrusters")),
        ("FrameShiftDrive", "FRAME SHIFT DRIVE", ship.get("core", {}).get("frameShiftDrive")),
        ("LifeSupport", "LIFE SUPPORT", ship.get("core", {}).get("lifeSupport")),
        ("PowerDistributor", "POWER DISTRIBUTOR", ship.get("core", {}).get("powerDistributor")),
        ("Radar", "SENSORS", ship.get("core", {}).get("sensors")),
        ("FuelTank", "FUEL TANK", ship.get("core", {}).get("fuelTank")),
    )
    rows: list[dict[str, Any]] = []

    def append_slot(
        group: str, slot: str, size: object, fallback_name: str = "",
        restriction: str = "",
    ) -> None:
        installed = installed_rows.get(slot, {})
        module_id = str(installed.get("moduleId") or "")
        module_name, size_rating = (
            module_purchase_identity(module_id) if module_id else ("", "")
        )
        engineering = engineerable.get(slot, {})
        desired_module_id = str(desired_by_slot.get(slot) or "")
        desired_name, desired_size_rating = (
            module_purchase_identity(desired_module_id)
            if desired_module_id else ("", "")
        )
        module_change = bool(
            desired_module_id
            and not same_module_identity(desired_module_id, module_id)
        )
        rows.append({
            "group": group,
            "slot": slot,
            "slotSize": int(size or 0),
            "slotBadge": str(size or ("U" if group == "UTILITY MOUNTS" else "—")),
            "moduleId": module_id,
            "module": str(engineering.get("module") or module_name or fallback_name),
            "sizeRating": str(engineering.get("sizeRating") or size_rating),
            "empty": not bool(module_id),
            "restriction": restriction,
            "engineerable": bool(engineering),
            "engineered": bool(installed.get("engineered")),
            "engineeringGrade": int(installed.get("engineeringGrade") or 0),
            "engineeringQuality": float(
                installed.get("engineeringQuality") or 0
            ),
            "engineeringQualityKnown": bool(
                installed.get("engineeringQualityKnown")
            ),
            "engineeringBlueprint": str(
                installed.get("engineeringBlueprint") or ""
            ),
            "experimentalEffect": str(
                installed.get("experimentalEffect") or ""
            ),
            "category": str(engineering.get("category") or ""),
            "blueprintCount": int(engineering.get("blueprintCount") or 0),
            "bindingKey": f"{slot}\u241f{module_id}" if module_id else slot,
            "moduleChange": module_change,
            "desiredModuleId": desired_module_id,
            "desiredSourceSlot": str(
                desired_source_by_slot.get(slot) or slot
            ) if desired_module_id else "",
            "desiredModule": desired_name,
            "desiredSizeRating": desired_size_rating,
            "planPending": False,
            "planTargetGrade": 0,
            "planBlueprint": "",
            "planExperimental": "",
        })

    for slot, label, size in core_specs:
        append_slot("CORE INTERNALS", slot, size, label)
    for index, spec in enumerate(ship.get("optional", []) or [], 1):
        if not isinstance(spec, dict):
            continue
        size = int(spec.get("size") or 0)
        slot = str(spec.get("name") or f"Slot{index:02d}_Size{size}")
        append_slot(
            "OPTIONAL INTERNALS", slot, size,
            restriction=str(spec.get("restriction") or ""),
        )
    hardpoint_counts = {}
    for spec in ship.get("hardpoints", []) or []:
        if not isinstance(spec, dict):
            continue
        size = int(spec.get("size") or 0)
        hardpoint_counts[size] = hardpoint_counts.get(size, 0) + 1
        fallback_slot = (
            f"{size_names.get(size, 'UNKNOWN').title()}Hardpoint"
            f"{hardpoint_counts[size]}"
        )
        append_slot(
            "HARDPOINTS",
            str(spec.get("name") or fallback_slot),
            size,
        )
    for index in range(1, int(ship.get("utility") or 0) + 1):
        append_slot("UTILITY MOUNTS", f"TinyHardpoint{index}", 0)
    return rows



MANDATORY_CORE_STOCK_FAMILIES = {
    "PowerPlant": "int_powerplant",
    "MainEngines": "int_engine",
    "FrameShiftDrive": "int_hyperdrive",
    "LifeSupport": "int_lifesupport",
    "PowerDistributor": "int_powerdistributor",
    "Radar": "int_sensors",
}



LOADOUT_PROJECTION_EVENTS = frozenset({
    "LoadGame", "Loadout", "ShipyardSwap", "SetUserShipName",
    "EngineerCraft", "ShipyardBuy", "ModuleBuy", "ModuleRetrieve",
    "ModuleSell", "ModuleStore", "ModuleSwap",
})



def module_store_core_replacement(event: dict[str, Any]) -> str:
    """Return Elite's implicit stock replacement for a stored core module."""
    slot = str(event.get("Slot") or "")
    if slot == "Armour":
        ship = str(event.get("Ship") or "").strip("$;")
        return f"{ship}_armour_grade1" if ship else ""
    stock_family = MANDATORY_CORE_STOCK_FAMILIES.get(slot, "")
    if not stock_family:
        return ""
    stored = str(event.get("StoredItem") or "").strip("$;")
    if stored.endswith("_name"):
        stored = stored[:-5]
    _family, size_marker, size_tail = stored.partition("_size")
    size, separator, _variant = size_tail.partition("_")
    if not size_marker or not separator or not size.isdigit():
        return ""
    return f"{stock_family}_size{size}_class1"



def latest_loadout_slots_by_ship(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Rebuild every ship's physical bindings in one chronological pass."""
    def slot_record(module_id: object, engineering: object = None) -> dict[str, Any]:
        details = engineering if isinstance(engineering, dict) else {}
        level = details.get("Level")
        try:
            grade = max(0, min(5, int(level or 0)))
        except (TypeError, ValueError):
            grade = 0
        quality_known = "Quality" in details and details.get("Quality") is not None
        try:
            quality = max(0.0, min(1.0, float(details.get("Quality") or 0)))
        except (TypeError, ValueError):
            quality = 0.0
        return {
            "moduleId": canonical_module_id(module_id),
            "engineered": bool(details and grade > 0),
            "engineeringGrade": grade,
            "engineeringQuality": quality,
            "engineeringQualityKnown": quality_known,
            "engineeringBlueprint": str(
                details.get("BlueprintName_Localised")
                or details.get("BlueprintName") or ""
            ),
            "experimentalEffect": str(
                details.get("ExperimentalEffect_Localised")
                or details.get("ExperimentalEffect") or ""
            ),
        }
    ordered = sorted(
        (
            (sequence, event) for sequence, event in enumerate(events or [])
            if isinstance(event, dict)
            and event.get("event") in LOADOUT_PROJECTION_EVENTS
        ),
        key=lambda row: (str(row[1].get("timestamp") or ""), row[0]),
    )
    current_ship_id = ""
    slots_by_ship: dict[str, dict[str, dict[str, Any]]] = {}
    for _sequence, event in ordered:
        event_name = str(event.get("event") or "")
        if event.get("ShipID") not in (None, "") and event_name in {
            "LoadGame", "Loadout", "ShipyardSwap", "SetUserShipName",
            "EngineerCraft",
        }:
            current_ship_id = str(event.get("ShipID"))
        elif (
            event_name == "ShipyardBuy"
            and event.get("NewShipID") not in (None, "")
        ):
            current_ship_id = str(event.get("NewShipID"))
        resolved_ship_id = str(event.get("ShipID") or "")
        if (
            not resolved_ship_id
            and event_name in {
                "ShipyardBuy", "ModuleBuy", "ModuleRetrieve", "ModuleSell",
                "ModuleStore", "ModuleSwap", "EngineerCraft",
            }
        ):
            resolved_ship_id = current_ship_id
        if not resolved_ship_id:
            continue
        slots = slots_by_ship.setdefault(resolved_ship_id, {})
        if event_name == "Loadout":
            previous_slots = slots
            rebuilt = {}
            for module in event.get("Modules") or []:
                if not isinstance(module, dict) or not module.get("Slot") or not module.get("Item"):
                    continue
                slot = str(module.get("Slot"))
                record = slot_record(module.get("Item"), module.get("Engineering"))
                previous = previous_slots.get(slot, {})
                # Loadout normally omits within-grade Quality. Preserve the
                # last authoritative EngineerCraft value only while the same
                # physical module, blueprint and grade remain installed.
                same_engineering = (
                    same_module_identity(
                        previous.get("moduleId"), record.get("moduleId")
                    )
                    and normalize(previous.get("engineeringBlueprint"))
                    == normalize(record.get("engineeringBlueprint"))
                    and int(previous.get("engineeringGrade") or 0)
                    == int(record.get("engineeringGrade") or 0)
                )
                if (
                    same_engineering
                    and previous.get("engineeringQualityKnown")
                    and not record.get("engineeringQualityKnown")
                ):
                    record["engineeringQuality"] = float(
                        previous.get("engineeringQuality") or 0
                    )
                    record["engineeringQualityKnown"] = True
                rebuilt[slot] = record
            slots = rebuilt
            slots_by_ship[resolved_ship_id] = slots
        elif event_name in {"ModuleBuy", "ModuleRetrieve"}:
            item_key = "BuyItem" if event_name == "ModuleBuy" else "RetrievedItem"
            slot = str(event.get("Slot") or "")
            item = str(event.get(item_key) or "")
            if slot and item and normalize(item) != "null":
                slots[slot] = slot_record(item)
        elif event_name in {"ModuleSell", "ModuleStore"}:
            slot = str(event.get("Slot") or "")
            if slot:
                replacement = (
                    module_store_core_replacement(event)
                    if event_name == "ModuleStore" else ""
                )
                if replacement:
                    slots[slot] = slot_record(replacement)
                else:
                    slots.pop(slot, None)
        elif event_name == "ModuleSwap":
            from_slot = str(event.get("FromSlot") or "")
            to_slot = str(event.get("ToSlot") or "")
            from_item = str(event.get("FromItem") or "")
            to_item = str(event.get("ToItem") or "")
            previous_from = slots.get(from_slot, slot_record(from_item))
            previous_to = slots.get(to_slot, slot_record(to_item))
            if to_slot:
                if from_item and normalize(from_item) != "null":
                    slots[to_slot] = previous_from
                else:
                    slots.pop(to_slot, None)
            if from_slot:
                if to_item and normalize(to_item) != "null":
                    slots[from_slot] = previous_to
                else:
                    slots.pop(from_slot, None)
        elif event_name == "EngineerCraft":
            slot = str(event.get("Slot") or "")
            module_id = str(event.get("Module") or "")
            installed = slots.get(slot, {})
            if (
                slot and module_id and installed
                and same_module_identity(installed.get("moduleId"), module_id)
            ):
                engineering = dict(event)
                previous_effect = str(installed.get("experimentalEffect") or "")
                applied_effect = str(
                    event.get("ExperimentalEffect")
                    or event.get("ApplyExperimentalEffect") or previous_effect
                )
                if applied_effect:
                    engineering["ExperimentalEffect"] = applied_effect
                slots[slot] = slot_record(module_id, engineering)
    return {
        ship_id: [
            {"slot": slot, **record}
            for slot, record in slots.items()
        ]
        for ship_id, slots in slots_by_ship.items()
    }



def _cached_profile_loadout_slots_by_ship(
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Reuse loadout state while appended events cannot affect modules."""
    revision, _all_events = _journal_snapshot()
    selected, _name = _journal_profile_identity()
    cache_key = (revision, selected)
    with _JOURNAL_EVENT_CACHE_LOCK:
        profile_events = _JOURNAL_EVENT_CACHE["profile_views"].get(cache_key)
    same_profile = bool(
        selected
        and profile_events is not None
        and len(profile_events) == len(events)
        and (
            not events
            or (
                profile_events[0] is events[0]
                and profile_events[-1] is events[-1]
            )
        )
    )
    if not same_profile:
        return latest_loadout_slots_by_ship(events)
    relevant = [
        event for event in events
        if isinstance(event, dict)
        and event.get("event") in LOADOUT_PROJECTION_EVENTS
    ]
    signature = (
        len(relevant),
        id(relevant[-1]) if relevant else 0,
        str(relevant[-1].get("timestamp") or "") if relevant else "",
    )
    with _JOURNAL_EVENT_CACHE_LOCK:
        cached = _JOURNAL_EVENT_CACHE["loadout_views"].get(selected)
        if cached and cached.get("signature") == signature:
            return cached["rows"]
    rows = latest_loadout_slots_by_ship(relevant)
    with _JOURNAL_EVENT_CACHE_LOCK:
        _JOURNAL_EVENT_CACHE["loadout_views"][selected] = {
            "signature": signature, "rows": rows,
        }
    return rows



def latest_loadout_slots(
    events: list[dict[str, Any]], ship_id: object
) -> list[dict[str, Any]]:
    """Return one ship's bindings from the single-pass loadout projection."""
    rows = latest_loadout_slots_by_ship(events)
    return [dict(row) for row in rows.get(str(ship_id or ""), [])]

