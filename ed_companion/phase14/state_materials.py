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



def material_metadata(data_dir: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(data_dir / "engineering_materials.json", {})
    entry_payload = read_json(data_dir / "entryData.json", {})
    raw_database = read_json(data_dir / "raw_materials_database.json", {})
    raw_materials = raw_database.get("materials", {})
    raw_sites = raw_database.get("sites", {})
    storage_limits = raw_database.get("storage_limits", {})
    entry_index = {}
    values = (
        entry_payload.values() if isinstance(entry_payload, dict)
        else entry_payload if isinstance(entry_payload, list) else []
    )
    for entry in values:
        if not isinstance(entry, dict):
            continue
        for value in (entry.get("Name"), entry.get("FormattedName")):
            key = normalize(value)
            if key:
                entry_index[key] = entry
    result = {}
    for item in payload.get("materials", []):
        key = normalize(item.get("canonical_key") or item.get("journal_name") or item.get("name"))
        if key:
            display_key = normalize(item.get("name"))
            entry = entry_index.get(key) or entry_index.get(display_key, {})
            raw_info = raw_materials.get(key) or raw_materials.get(display_key) or {}
            category = str(item.get("category") or "unknown")
            if category not in MATERIAL_CATEGORIES:
                LOGGER.warning("Material %s has unresolved category %s", key, category)
                category = "unknown"
            grade = int(raw_info.get("grade", item.get("grade", 0)) or 0)
            raw_capacity = storage_limits.get(str(grade)) if raw_info else None
            raw_sources = []
            for site_id in raw_info.get("site_ids", []) or []:
                site = raw_sites.get(site_id)
                if isinstance(site, dict):
                    raw_sources.append({**site, "site_id": site_id})
                else:
                    LOGGER.warning("Raw material %s references unknown site %s", key, site_id)
            availability_labels = {
                "surface": "Surface prospecting",
                "asteroids": "Asteroid mining",
            }
            raw_origins = [
                availability_labels.get(str(value), str(value))
                for value in (raw_info.get("availability", []) or [])
            ]
            result[key] = {
                "Name": item.get("name") or key,
                "Category": category,
                "Rarity": item.get("rarity") or "",
                "Grade": grade,
                "MaxCapacity": (
                    int(raw_capacity)
                    if raw_capacity not in (None, "") else
                    int(item["max_capacity"])
                    if item.get("max_capacity") not in (None, "") else None
                ),
                "TraderGroup": str(
                    f"Category{raw_info['trader_category']}" if raw_info else
                    item.get("trader_group") or entry.get("Group")
                    or item.get("subkind") or ""
                ),
                "Tradeable": bool(item.get("tradeable", True)),
                "Origins": [
                    str(value) for value in (
                        raw_origins
                        or item.get("origin_details")
                        or entry.get("OriginDetails") or []
                    )
                    if value
                ],
                "ExactSources": raw_sources,
                "RawAvailability": list(raw_info.get("availability", []) or []),
                "PreferredFarmMethod": str(raw_info.get("preferred_method") or ""),
                "RawTraderCategory": int(raw_info.get("trader_category", 0) or 0),
                "UsedIn": [
                    {
                        "module": str(usage.get("type") or "Module"),
                        "blueprint": str(usage.get("blueprint") or "Blueprint"),
                        "grade": int(usage.get("grade", 0) or 0),
                        "amount": int(usage.get("amount", 0) or 0),
                        "engineers": ", ".join(
                            str(engineer)
                            for engineer in (usage.get("engineers", []) or [])
                            if engineer and not str(engineer).startswith("@")
                        ),
                    }
                    for usage in (item.get("used_in", []) or [])
                    if isinstance(usage, dict)
                ],
            }
            result[key]["Guidance"] = actionable_source_card(key, result[key])
    top_raw_by_group = {
        info["TraderGroup"]: info
        for info in result.values()
        if info.get("Category") == "Raw"
        and int(info.get("Grade", 0) or 0) == 4
        and info.get("ExactSources")
    }
    for info in result.values():
        if info.get("Category") != "Raw" or int(info.get("Grade", 0) or 0) >= 4:
            continue
        top = top_raw_by_group.get(info.get("TraderGroup"))
        if not top:
            continue
        trade_sources = []
        for source in top.get("ExactSources", []):
            candidate = dict(source)
            candidate["kind"] = "TRADE_DOWN"
            candidate["label"] = "NEAREST FAST ROUTE · RAW TRADER"
            candidate["verified"] = False
            candidate["confidence"] = "derived"
            candidate["target"] = str(top.get("Name") or "")
            candidate["method"] = (
                f"Collect {top.get('Name')} here, then use the nearest Raw "
                f"Material Trader to trade within {info.get('TraderGroup')} "
                f"down to {info.get('Name')}."
            )
            trade_sources.append(candidate)
        info["ExactSources"] = list(info.get("ExactSources", []) or []) + trade_sources
    return result



def canonical_cargo_materials(data_dir: Path) -> set[str]:
    """Return cargo commodities explicitly required by bundled recipes."""
    entries = read_json(data_dir / "entryData.json", {})
    values = (
        entries.values() if isinstance(entries, dict)
        else entries if isinstance(entries, list) else []
    )
    commodities = {
        normalize(entry.get("FormattedName") or entry.get("Name"))
        for entry in values if isinstance(entry, dict)
        and entry.get("Kind") == "Commodity"
    }
    blueprints = read_json(data_dir / "blueprints.json", [])
    required = {
        normalize(ingredient.get("Name"))
        for blueprint in blueprints if isinstance(blueprints, list)
        and isinstance(blueprint, dict)
        for ingredient in (blueprint.get("Ingredients", []) or [])
        if isinstance(ingredient, dict)
    }
    return {key for key in required if key and key in commodities}



def _newer_station_location(
    candidate: dict[str, Any], previous: dict[str, Any] | None,
) -> bool:
    """Prefer timestamped Journal evidence; otherwise retain event order."""
    if not previous:
        return True
    candidate_time = str(candidate.get("timestamp") or "")
    previous_time = str(previous.get("timestamp") or "")
    if candidate_time:
        return not previous_time or candidate_time >= previous_time
    return not previous_time



def technology_broker_unlock_guide(
    package_root: Path,
    metadata: dict[str, dict[str, Any]],
    inventory: dict[str, int],
    events: list[dict[str, Any]],
    broker_catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build Human and Guardian Tech Broker guides from canonical recipes."""
    config = read_json(
        reference_data_dir(package_root) / "tech_broker_unlocks.json", {}
    )
    guidance_by_broker = config.get("guidance", {}) if isinstance(config, dict) else {}
    origin_guidance = config.get("origins", {}) if isinstance(config, dict) else {}
    special_destinations = (
        config.get("special_destinations", {}) if isinstance(config, dict) else {}
    )
    journal_unlock_aliases = (
        config.get("journal_unlock_aliases", {})
        if isinstance(config, dict) else {}
    )
    recipes: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    recipe_names: dict[str, str] = {}
    canonical_records = read_json(
        reference_data_dir(package_root) / "blueprints.json", []
    )
    for record in canonical_records:
        if not isinstance(record, dict):
            continue
        broker = str(record.get("Type") or "").strip().title()
        if broker not in {"Guardian", "Human"} \
                or "@Technology" not in (record.get("Engineers", []) or []):
            continue
        blueprint = str(record.get("Name") or "").strip()
        for ingredient in record.get("Ingredients", []) or []:
            if not isinstance(ingredient, dict):
                continue
            material_name = str(
                ingredient.get("Name_Localised")
                or ingredient.get("Name") or ""
            ).strip()
            key = normalize(ingredient.get("Name") or material_name)
            amount = max(0, int(ingredient.get("Size", 0) or 0))
            if blueprint and key and amount:
                recipes[(broker, blueprint)][key] = amount
                recipe_names[key] = material_name or key
    if not recipes:
        for key, material in metadata.items():
            for usage in material.get("UsedIn", []) or []:
                broker = str(usage.get("module") or "").strip().title()
                blueprint = str(usage.get("blueprint") or "").strip()
                amount = max(0, int(usage.get("amount", 0) or 0))
                if broker in {"Guardian", "Human"} and blueprint and amount:
                    recipes[(broker, blueprint)][key] = amount
                    recipe_names[key] = str(material.get("Name") or key)
    entry_payload = read_json(
        reference_data_dir(package_root) / "entryData.json", []
    )
    entry_values = (
        entry_payload.values() if isinstance(entry_payload, dict)
        else entry_payload if isinstance(entry_payload, list) else []
    )
    ingredient_entries = {
        normalize(entry.get("FormattedName") or entry.get("Name")): entry
        for entry in entry_values if isinstance(entry, dict)
    }

    unlocked_tokens: set[str] = set()
    unlocked_tokens_by_broker: dict[str, set[str]] = defaultdict(set)
    unlocked_recipe_keys: set[tuple[str, str]] = set()
    owned_tokens: set[str] = set()
    configured_destinations = (
        config.get("recommended_destinations", {})
        if isinstance(config, dict) else {}
    )
    broker_destinations: dict[str, dict[str, str]] = {
        broker: {
            "system": str(value.get("system") or "").strip(),
            "station": str(value.get("station") or "").strip(),
            "evidence": str(
                value.get("evidence") or "Bundled recommendation"
            ).strip(),
        }
        for broker, value in configured_destinations.items()
        if broker in {"Human", "Guardian"}
        and isinstance(value, dict)
        and str(value.get("system") or "").strip()
    }
    station_locations_by_id: dict[str, dict[str, Any]] = {}
    station_locations_by_name: dict[str, dict[str, Any]] = {}
    current_system = ""
    current_station = ""
    for event in events:
        if event.get("StarSystem"):
            next_system = str(event.get("StarSystem") or "").strip()
            if next_system != current_system:
                current_station = ""
            current_system = next_system
        if event.get("StationName"):
            current_station = str(event.get("StationName") or "").strip()
        station_name = str(event.get("StationName") or "").strip()
        station_system = str(event.get("StarSystem") or current_system).strip()
        if station_name and station_system and event.get("event") in {
            "Docked", "Location", "Market", "Outfitting", "Shipyard",
            "StoredModules", "TechnologyBroker",
        }:
            station_location = {
                "system": station_system,
                "station": station_name,
                "marketId": str(event.get("MarketID") or "").strip(),
                "distance_ls": event.get("DistFromStarLS"),
                "timestamp": str(event.get("timestamp") or "").strip(),
                "evidence": "Journal-confirmed station location",
            }
            station_token = normalize(station_name)
            previous = station_locations_by_name.get(station_token)
            if _newer_station_location(station_location, previous):
                station_locations_by_name[station_token] = station_location
            if station_location["marketId"]:
                previous = station_locations_by_id.get(
                    station_location["marketId"]
                )
                if _newer_station_location(station_location, previous):
                    station_locations_by_id[
                        station_location["marketId"]
                    ] = station_location
        if event.get("event") == "TechnologyBroker":
            broker_type_raw = str(event.get("BrokerType") or "").strip()
            broker_type = broker_type_raw.title()
            if broker_type in {"Human", "Guardian"} and current_system:
                broker_destinations[broker_type] = {
                    "system": current_system,
                    "station": current_station,
                    "evidence": "Journal-confirmed TechnologyBroker visit",
                }
            items_unlocked = event.get("ItemsUnlocked", []) or []
            if isinstance(items_unlocked, dict):
                items_unlocked = [items_unlocked]
            elif not isinstance(items_unlocked, list):
                items_unlocked = [items_unlocked]
            for item in items_unlocked:
                value = item.get("Name") if isinstance(item, dict) else item
                token = normalize(value)
                if token:
                    unlocked_tokens.add(token)
                    if broker_type_raw:
                        unlocked_tokens_by_broker[
                            broker_type_raw.upper()
                        ].add(token)
            consumed: dict[str, int] = {}
            for item in (
                list(event.get("Materials", []) or [])
                + list(event.get("Commodities", []) or [])
            ):
                if not isinstance(item, dict):
                    continue
                key = normalize(item.get("Name") or item.get("Name_Localised"))
                count = max(0, int(item.get("Count", 0) or 0))
                if key and count:
                    consumed[key] = consumed.get(key, 0) + count
            event_broker = broker_type_raw.upper()
            if consumed:
                for recipe_key, requirement in recipes.items():
                    recipe_broker, recipe_name = recipe_key
                    recipe_subtype = (
                        "SIRIUS"
                        if recipe_broker == "Human"
                        and recipe_name.casefold().startswith("sirius ")
                        else recipe_broker.upper()
                    )
                    if recipe_subtype == event_broker \
                            and requirement == consumed:
                        unlocked_recipe_keys.add(recipe_key)
        module_values = []
        if event.get("event") == "Loadout":
            module_values.extend(
                item.get("Item") for item in event.get("Modules", []) or []
                if isinstance(item, dict)
            )
        elif event.get("event") == "StoredModules":
            module_values.extend(
                item.get("Name") for item in event.get("Items", []) or []
                if isinstance(item, dict)
            )
        else:
            for field in (
                "BuyItem", "RetrievedItem", "StoredItem", "SellItem",
            ):
                module_values.append(event.get(field))
        owned_tokens.update(normalize(value) for value in module_values if value)

    rows = []
    for (broker, name), requirement in recipes.items():
        guidance = guidance_by_broker.get(broker, {})
        name_token = normalize(name)
        direct_unlock = any(
            token == name_token or name_token in token or token in name_token
            for token in unlocked_tokens | owned_tokens
            if token
        )
        broker_subtype = (
            "SIRIUS" if broker == "Human" and name.casefold().startswith("sirius ")
            else broker.upper()
        )
        alias_tokens = {
            normalize(value)
            for value in journal_unlock_aliases.get(name, [])
            if value
        }
        unlocked = (broker, name) in unlocked_recipe_keys or direct_unlock or bool(
            alias_tokens
            & unlocked_tokens_by_broker.get(broker_subtype, set())
        )
        materials = []
        for key, need in requirement.items():
            material = metadata.get(key, {})
            ingredient_entry = ingredient_entries.get(key, {})
            have = max(0, int(inventory.get(key, 0) or 0))
            material_name = str(material.get("Name") or recipe_names.get(key) or key)
            category = str(
                material.get("Category") or ingredient_entry.get("Kind") or ""
            )
            if "blueprint" in key:
                origin = str(origin_guidance.get("blueprint") or "Guardian structure")
            elif category.casefold() == "encoded" or "pattern" in key:
                origin = str(origin_guidance.get("data") or "Guardian obelisk scans")
            elif "guardian" in key or "guardian" in material_name.casefold():
                origin = str(origin_guidance.get("component") or "Guardian structure")
            else:
                origins = [
                    str(value) for value in (
                        material.get("Origins")
                        or ingredient_entry.get("OriginDetails") or []
                    ) if value
                ]
                origin = " · ".join(origins[:2]) or str(
                    origin_guidance.get("conventional") or "Standard material sources"
                )
            materials.append({
                "key": key,
                "name": material_name,
                "category": category or "Commodity",
                "have": have,
                "need": need,
                "missing": max(0, need - have),
                "ready": have >= need,
                "blueprint": "blueprint" in key,
                "origin": origin,
            })
        materials.sort(key=lambda row: (not row["blueprint"], row["name"].casefold()))
        ready = bool(materials) and all(row["ready"] for row in materials)
        has_progress = any(row["have"] > 0 for row in materials)
        status = (
            "unlocked" if unlocked else "ready" if ready
            else "pending" if has_progress else "locked"
        )
        blueprint_ready = all(
            row["ready"] for row in materials if row["blueprint"]
        )
        components_ready = all(
            row["ready"] for row in materials if not row["blueprint"]
        )
        if broker == "Guardian":
            steps = [
                {
                    "label": "1 · Acquire blueprint segment",
                    "detail": str(guidance.get("blueprint") or "Visit a Guardian structure."),
                    "state": "complete" if blueprint_ready or unlocked else "active",
                },
                {
                    "label": "2 · Collect Guardian materials",
                    "detail": str(guidance.get("components") or "Collect the recipe materials."),
                    "state": (
                        "complete" if components_ready or unlocked
                        else "active" if blueprint_ready else "blocked"
                    ),
                },
                {
                    "label": "3 · Unlock at Guardian Tech Broker",
                    "detail": str(guidance.get("broker") or "Visit a Guardian Technology Broker."),
                    "state": "complete" if unlocked else "active" if ready else "blocked",
                },
            ]
        else:
            steps = [
                {
                    "label": "1 · Collect recipe materials",
                    "detail": str(guidance.get("components") or "Collect the listed materials."),
                    "state": "complete" if components_ready or unlocked else "active",
                },
                {
                    "label": "2 · Unlock at Human Tech Broker",
                    "detail": str(guidance.get("broker") or "Visit a Human Technology Broker."),
                    "state": "complete" if unlocked else "active" if ready else "blocked",
                },
            ]
        category = "HUMAN TECH" if broker == "Human" else (
            "FIGHTERS" if "fighter" in name.casefold()
            else "WEAPONS" if any(
                value in name.casefold()
                for value in ("cannon", "charger", "gauss")
            ) else "MODULES"
        )
        candidates = [
            dict(row) for row in (broker_catalog or [])
            if isinstance(row, dict)
            and str(row.get("brokerType") or "").upper() == broker_subtype
            and row.get("system") and row.get("station")
        ]
        if broker_subtype == "SIRIUS":
            candidates.extend({
                **dict(row),
                "brokerType": "SIRIUS",
                "source": "Bundled Sirius Tech Broker catalog",
                "verified": "2026-08-15",
            } for row in special_destinations.get("SIRIUS", [])
              if isinstance(row, dict) and row.get("system") and row.get("station"))
        resolved_candidates = []
        for candidate in candidates:
            resolved = dict(candidate)
            market_id = str(
                resolved.get("marketId") or resolved.get("market_id")
                or resolved.get("MarketID") or ""
            ).strip()
            station_token = normalize(resolved.get("station"))
            journal_location = (
                station_locations_by_id.get(market_id) if market_id else None
            ) or station_locations_by_name.get(station_token)
            if journal_location:
                resolved["system"] = journal_location["system"]
                resolved["station"] = journal_location["station"]
                resolved["source"] = journal_location["evidence"]
                resolved["verified"] = journal_location["timestamp"]
                if journal_location.get("distance_ls") is not None:
                    resolved["distance_ls"] = journal_location["distance_ls"]
            resolved_candidates.append(resolved)
        candidates = resolved_candidates
        if broker_subtype != "SIRIUS":
            candidates.sort(key=lambda row: (
                float(row.get("distance_ly") or 1e12),
                float(row.get("distance_ls") or 1e12),
                str(row.get("station") or "").casefold(),
            ))
        destination = dict(broker_destinations.get(broker, {}))
        if candidates:
            destination = {
                "system": str(candidates[0].get("system") or ""),
                "station": str(candidates[0].get("station") or ""),
                "evidence": str(candidates[0].get("source") or "Broker catalog"),
            }
        elif destination.get("system"):
            candidates = [{
                "brokerType": broker_subtype,
                "system": str(destination.get("system") or ""),
                "station": str(destination.get("station") or ""),
                "source": str(destination.get("evidence") or "Bundled recommendation"),
            }]
        next_step = next(
            (step["label"].split(" · ", 1)[-1] for step in steps
             if step["state"] == "active"),
            "Unlocked",
        )
        active_step = next(
            (step for step in steps if step["state"] == "active"),
            steps[-1] if steps else {},
        )
        blueprint_names = [
            row["name"] for row in materials if row["blueprint"]
        ]
        required_total = sum(row["need"] for row in materials)
        owned_required = sum(min(row["have"], row["need"]) for row in materials)
        rows.append({
            "name": name,
            "broker": broker.upper(),
            "brokerSubtype": broker_subtype,
            "category": category,
            "status": status,
            "statusText": status.upper(),
            "readyMaterials": sum(row["ready"] for row in materials),
            "totalMaterials": len(materials),
            "missingTotal": sum(row["missing"] for row in materials),
            "missingKinds": sum(not row["ready"] for row in materials),
            "requiredTotal": required_total,
            "ownedRequired": owned_required,
            "progress": owned_required / required_total if required_total else 1.0,
            "materials": materials,
            "steps": steps,
            "nextAction": next_step,
            "nextActionDetail": str(active_step.get("detail") or ""),
            "prerequisite": (
                " + ".join(blueprint_names) if blueprint_names
                else "Complete Human Tech Broker material recipe"
            ),
            "source": str(config.get("source") or "Bundled Guardian catalog"),
            "destinationSystem": str(destination.get("system") or ""),
            "destinationStation": str(destination.get("station") or ""),
            "destinationEvidence": str(destination.get("evidence") or ""),
            "brokerDestinations": candidates[:8],
        })
    broker_order = {
        name: index for index, name in enumerate(
            config.get("broker_order", []) if isinstance(config, dict) else []
        )
    }
    category_order = {
        name: index for index, name in enumerate(
            config.get("category_order", []) if isinstance(config, dict) else []
        )
    }
    status_order = {"ready": 0, "pending": 1, "locked": 2, "unlocked": 3}
    ordered = sorted(rows, key=lambda row: (
        broker_order.get(row["broker"], 99),
        category_order.get(row["category"], 99),
        status_order.get(row["status"], 99),
        row["name"].casefold(),
    ))
    for index, row in enumerate(ordered, 1):
        row["sequence"] = index
    return ordered



RAW_GROUP_FARMS = {
    "Category1": ("Yttrium", "Outotz LS-K d8-3", "B 5 A"),
    "Category2": ("Technetium", "HIP 36601", "C 5 A"),
    "Category3": ("Ruthenium", "HIP 36601", "C 1 D"),
    "Category4": ("Selenium", "HR 3230", "3 A A"),
    "Category5": ("Tellurium", "HIP 36601", "C 3 B"),
    "Category6": ("Polonium", "HIP 36601", "C 1 A"),
    "Category7": ("Antimony", "Outotz LS-K d8-3", "B 5 C"),
}



def actionable_source_card(material_key, info):
    """Return practical guidance even when the upstream database is generic."""
    name = str(info.get("Name") or material_key)
    category = str(info.get("Category") or "")
    grade = int(info.get("Grade", 0) or 0)
    group = str(info.get("TraderGroup") or "")
    origins = " · ".join(str(value) for value in info.get("Origins", []) or [])
    folded = origins.casefold()

    if category == "Raw":
        farm = RAW_GROUP_FARMS.get(group)
        if farm:
            top_name, system, body = farm
            if name == top_name:
                target = (
                    "Phloem Excretion on Brain Trees"
                    if name == "Selenium" else "Crystalline Shards"
                )
                return {
                    "kind": "RAW_FARM",
                    "label": "DIRECT RAW FARM",
                    "detail": (
                        f"{system} · {body} → {target} · Map the body with "
                        "the DSS, land in the highlighted biological terrain, "
                        "shoot the material-bearing growth with the SRV and scoop it."
                    ),
                    "system": system,
                    "body": body,
                    "target": target,
                    "verified": False,
                    "confidence": "derived",
                }
            return {
                "kind": "TRADE_DOWN",
                "label": "FAST ROUTE · RAW TRADER",
                "detail": (
                    f"Collect {top_name} at {system} · {body}, then visit a Raw "
                    f"Material Trader and trade within {group} down to {name}. "
                    "The trade planner calculates the protected amount."
                ),
                "system": system,
                "body": body,
                "target": top_name,
                "verified": False,
                "confidence": "derived",
            }

    if "guardian" in folded or group.startswith("Guardian"):
        action = (
            "At a Guardian structure, deploy the SRV. Scan active obelisks for "
            "pattern data; shoot destructible panels for components. For a "
            "blueprint segment, charge all pylons, defeat the Sentinels, jettison "
            "the required relic at the altar and scan the activated data core."
        )
        return {
            "kind": "GUARDIAN",
            "label": "GUARDIAN STRUCTURE",
            "detail": f"Target: {name} · {action}",
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    if "thargoid" in folded or group.startswith("Thargoid"):
        return {
            "kind": "THARGOID",
            "label": "THARGOID SALVAGE",
            "detail": (
                f"Target: {name} · Use a known Thargoid surface site or salvage "
                "from the specified Thargoid vessel type. Scan first, destroy only "
                "the named growth/component, then collect it with the SRV or cargo scoop."
            ),
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    if category == "Manufactured":
        if "high grade emission" in folded or grade >= 5:
            condition = origins or "High Grade Emissions or mission rewards"
            return {
                "kind": "HGE",
                "label": "DYNAMIC HGE TARGET",
                "detail": (
                    f"Target: {name} · Required source condition: {condition}. "
                    "Use the Galaxy Map to find a populated system matching the "
                    "allegiance/state, scan the Nav Beacon or use the FSS, then "
                    "enter HIGH GRADE EMISSIONS and collect with limpets."
                ),
                "target": name,
                "verified": False,
                "confidence": "derived",
            }
        return {
            "kind": "SALVAGE",
            "label": "SHIP / SIGNAL SALVAGE",
            "detail": (
                f"Target: {name} · {origins or 'Ship and signal-source salvage'}. "
                "Visit a busy Nav Beacon or Resource Extraction Site, scan ships, "
                "collect debris from the specified ship class with limpets. "
                "Faster alternative: collect the G5 material in the same trader "
                f"group ({group}) and trade down at a Manufactured Trader."
            ),
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    if category == "Encoded":
        if "wake" in folded:
            return {
                "kind": "WAKE_SCAN",
                "label": "HIGH-WAKE SCANNING",
                "detail": (
                    f"Target: {name} · Fit a Frame Shift Wake Scanner, wait outside "
                    "a busy station or at a Distribution Centre, target HIGH ENERGY "
                    "WAKES left by departing ships and scan them. Mission rewards "
                    "and an Encoded Trader are the alternatives."
                ),
                "target": "High Energy Wake",
                "verified": False,
                "confidence": "derived",
            }
        if "surface data point" in folded:
            return {
                "kind": "DATA_POINT",
                "label": "SURFACE DATA POINT",
                "detail": (
                    f"Target: {name} · Land at a surface settlement or data-point "
                    "POI, deploy the SRV, select the DATA POINT in Contacts and scan "
                    "it with the Data Link Scanner. Observe trespass/security warnings."
                ),
                "target": "Surface Data Point",
                "verified": False,
                "confidence": "derived",
            }
        if "ship scanning" in folded or "deep space data beacon" in folded:
            return {
                "kind": "SHIP_SCAN",
                "label": "SHIP / BEACON SCANNING",
                "detail": (
                    f"Target: {name} · {origins}. Visit a busy Nav Beacon or "
                    "Supercruise traffic lane, target the specified ship class and "
                    "complete the normal sensor scan. No special scanner is required "
                    "unless the source explicitly names a wake or data beacon."
                ),
                "target": name,
                "verified": False,
                "confidence": "derived",
            }
        return {
            "kind": "ENCODED_FARM",
            "label": "ENCODED DATA ROUTE",
            "detail": (
                f"Target: {name} · {origins or 'Encoded signal and mission data'}. "
                "For repeatable trade stock, scan the four beacons at the Jameson "
                "Crash Site on HIP 12099 · 1 B, then use an Encoded Material Trader "
                f"to trade into {group}. Direct mission rewards remain lossless."
            ),
            "system": "HIP 12099",
            "body": "1 B",
            "target": name,
            "verified": False,
            "confidence": "derived",
        }

    return {
        "kind": "SOURCE",
        "label": "DOCUMENTED SOURCE",
        "detail": f"Target: {name} · {origins or 'Open the material database for sources.'}",
        "target": name,
        "verified": False,
        "confidence": "derived" if origins else "heuristic",
    }



def _source_distance(source, current_position):
    star_pos = source.get("star_pos")
    if (
        not isinstance(current_position, (list, tuple))
        or len(current_position) != 3
        or not isinstance(star_pos, (list, tuple))
        or len(star_pos) != 3
    ):
        return None
    try:
        return math.sqrt(sum(
            (float(left) - float(right)) ** 2
            for left, right in zip(current_position, star_pos)
        ))
    except (TypeError, ValueError):
        return None



def source_cards(material_key, info, current_position=None):
    cards = []
    role_priority = {
        "PRIMARY": 0,
        "NEARER_ALTERNATIVE": 1,
        "RELOG_ALTERNATIVE": 2,
    }
    indexed_sources = list(enumerate(info.get("ExactSources", []) or []))

    def source_priority(indexed_source):
        index, source = indexed_source
        distance = _source_distance(source, current_position)
        role_rank = role_priority.get(str(source.get("role") or ""), 3)
        if distance is not None:
            return (0, distance, role_rank, index)
        return (1, role_rank, index, 0)

    exact_sources = [source for _index, source in sorted(
        indexed_sources, key=source_priority
    )]
    for source in exact_sources:
        if not isinstance(source, dict):
            continue
        location = " · ".join(
            value for value in (
                str(source.get("system") or ""),
                str(source.get("body") or ""),
                str(source.get("coordinates") or ""),
            ) if value
        )
        alternate_coordinates = [
            str(value) for value in (source.get("alternate_coordinates", []) or [])
            if value
        ]
        if alternate_coordinates:
            location += " · Alt: " + ", ".join(alternate_coordinates)
        target = str(source.get("target") or "")
        method = str(source.get("method") or "")
        distance = _source_distance(source, current_position)
        if distance is not None:
            location += (
                (" · " if location else "")
                + f"{distance:.1f} ly from current position"
            )
        summary = location
        if target:
            summary += (" → " if summary else "") + target
        if method:
            summary += (" · " if summary else "") + method
        verified = bool(source.get("verified", False))
        cards.append({
            "kind": str(source.get("kind") or "SOURCE"),
            "role": str(source.get("role") or ""),
            "label": str(source.get("label") or "VERIFIED LOCATION"),
            "detail": summary,
            "system": str(source.get("system") or ""),
            "body": str(source.get("body") or ""),
            "coordinates": str(source.get("coordinates") or ""),
            "alternateCoordinates": alternate_coordinates,
            "target": target,
            "method": method,
            "verified": verified,
            "confidence": "verified" if verified else str(
                source.get("confidence") or "derived"
            ),
            "distanceLy": round(distance, 1) if distance is not None else -1,
        })
    if not cards:
        cards.append(info.get("Guidance") or actionable_source_card(material_key, info))
    for origin in info.get("Origins", []) or []:
        text = str(origin)
        lowered = text.casefold()
        if "high grade emission" in lowered or "hge" in lowered:
            kind, label = "HGE", "HIGH GRADE EMISSIONS"
        elif "mission" in lowered:
            kind, label = "MISSION", "MISSION REWARD"
        elif "wake" in lowered or "scan" in lowered:
            kind, label = "SCAN", "SCANNING"
        elif "surface" in lowered or "geological" in lowered:
            kind, label = "SURFACE", "PLANET SURFACE"
        elif "mining" in lowered:
            kind, label = "MINING", "MINING"
        elif "signal" in lowered or "ship" in lowered:
            kind, label = "SALVAGE", "SIGNAL / SHIP SALVAGE"
        else:
            kind, label = "SOURCE", "DOCUMENTED SOURCE"
        cards.append({
            "kind": kind, "label": label, "detail": text,
            "verified": False, "confidence": "heuristic",
        })
    if info.get("Category") == "Raw" and int(info.get("Grade", 0) or 0) < 4:
        cards.append({
            "kind": "TRADE_DOWN",
            "label": "FAST ALTERNATIVE · RAW MATERIAL TRADER",
            "verified": False,
            "confidence": "derived",
            "detail": (
                "Collect a higher-grade Raw material at a suggested farm, then "
                "trade down within the same material group. The app protects "
                "materials required by the active build."
            ),
        })
    if is_hge_material(material_key) and not any(
        card["kind"] == "HGE" for card in cards
    ):
        cards.insert(0, {
            "kind": "HGE",
            "label": "HIGH GRADE EMISSIONS",
            "verified": False,
            "confidence": "derived",
            "detail": "Derived HGE guidance; use live signal intelligence when available.",
        })
    return cards



def material_trade_options(target, required, inventory, metadata, limit=8):
    target_meta = metadata.get(target, {})
    if not is_material_tradeable(target_meta):
        return []
    target_grade = int(target_meta.get("Grade", 0) or 0)
    target_group = target_meta.get("TraderGroup")
    deficit = (
        int((required or {}).get(target, 0) or 0)
        - int((inventory or {}).get(target, 0) or 0)
    )
    if deficit <= 0:
        return []
    options = []
    for source, stock in (inventory or {}).items():
        source_meta = metadata.get(source, {})
        protected = int((required or {}).get(source, 0) or 0)
        surplus = max(0, int(stock or 0) - protected)
        if (
            source == target or surplus <= 0
            or source_meta.get("Category") != target_meta.get("Category")
            or not is_material_tradeable(source_meta)
        ):
            continue
        same_group = source_meta.get("TraderGroup") == target_group
        batch = trade_batch(
            source_meta.get("Grade"), target_grade, same_group
        )
        if not batch:
            continue
        batch_in, batch_out = batch
        possible = surplus // batch_in
        if possible <= 0:
            continue
        batches = min(possible, max(1, (deficit + batch_out - 1) // batch_out))
        spend, receive = batches * batch_in, batches * batch_out
        options.append({
            "sourceKey": source,
            "sourceName": str(source_meta.get("Name") or source),
            "spend": spend,
            "receive": receive,
            "batchIn": batch_in,
            "batchOut": batch_out,
            "stock": int(stock or 0),
            "protected": protected,
            "surplus": surplus,
            "sameGroup": same_group,
            "reason": (
                "Same trader row; best exchange efficiency."
                if same_group else
                "Cross-row exchange; protected build stock remains untouched."
            ),
        })
    options.sort(key=lambda row: (
        not row["sameGroup"],
        -(row["batchOut"] / row["batchIn"]),
        -row["receive"],
        row["sourceName"].casefold(),
    ))
    return options[:limit]

