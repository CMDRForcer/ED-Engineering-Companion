import time
import json
import hashlib
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ed_companion import APP_VERSION

INARA_API_URL = "https://inara.cz/inapi/v1/"
INARA_APP_NAME = "ED Engineering Companion"
MAX_EVENTS = 50
INARA_MAX_REQUESTS_PER_MINUTE = 2
INARA_BATCH_WINDOW_SECONDS = 45
INARA_MIN_REQUEST_INTERVAL_SECONDS = 300
INARA_RATE_LIMIT_COOLDOWN_SECONDS = 3700
INARA_RETRY_BASE_SECONDS = 60
INARA_RETRY_MAX_SECONDS = 900
INARA_PENDING_EVENT_LIMIT = 2000

_RANK_KEYS = {
    "Combat": "combat", "Trade": "trade", "Explore": "explore",
    "CQC": "cqc", "Federation": "federation", "Empire": "empire",
    "Soldier": "soldier", "Exobiologist": "exobiologist",
}
_REPUTATION_KEYS = {
    "Federation": "federation", "Empire": "empire",
    "Alliance": "alliance", "Independent": "independent",
}
AUTO_UPLOAD_EVENT_NAMES = frozenset({
    "addCommanderPermit",
    "setCommanderInventoryMaterials", "setCommanderInventoryCargo",
    "resetCommanderInventory", "setCommanderInventory",
    "setCommanderTravelLocation", "addCommanderTravelDock",
    "addCommanderTravelFSDJump", "addCommanderTravelCarrierJump",
    "addCommanderTravelLand", "setCommanderRankPilot",
    "setCommanderRankEngineer", "setCommanderRankPower",
    "setCommanderReputationMajorFaction", "setCommanderReputationMinorFaction",
    "setCommanderCredits", "setCommanderGameStatistics",
    "setCommanderStorageModules", "addCommanderShip", "delCommanderShip",
    "setCommanderShip", "setCommanderShipLoadout", "setCommanderShipTransfer",
    "setCommanderSuitLoadout", "updateCommanderSuitLoadout",
    "delCommanderSuitLoadout", "addCommanderMission",
    "setCommanderMissionAbandoned", "setCommanderMissionCompleted",
    "setCommanderMissionFailed", "addCommanderCombatDeath",
    "addCommanderCombatInterdicted", "addCommanderCombatInterdiction",
    "addCommanderCombatInterdictionEscape", "addCommanderCombatKill",
    "setCommanderCommunityGoalProgress",
})


class InaraError(RuntimeError):
    """A safe, user-displayable INARA error without credentials."""

    def __init__(self, message, retryable=True, status_code=None, schema_error=False):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.schema_error = bool(schema_error)


def _response_error_detail(response):
    """Return a short API error without leaking request credentials."""
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        header = payload.get("header")
        if isinstance(header, dict):
            detail = header.get("eventStatusText")
            if detail:
                return str(detail)[:300]
        for key in ("error", "message", "detail"):
            if payload.get(key):
                return str(payload[key])[:300]
    return ""


class _UrlResponse:
    def __init__(self, response, body):
        self.status_code = response.status
        self.headers = response.headers
        self._body = body

    def json(self):
        return json.loads(self._body.decode("utf-8"))


def _default_post(url, json=None, timeout=25, headers=None):
    body = __import__("json").dumps(json).encode("utf-8")
    request = Request(url, data=body, headers=headers or {}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return _UrlResponse(response, response.read())


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def build_event(name, data=None, timestamp=None):
    return {
        "eventName": str(name),
        "eventTimestamp": str(timestamp or _timestamp()),
        "eventData": data if data is not None else {},
    }


def build_payload(config, events, app_version=APP_VERSION):
    events = list(events)
    if not events or len(events) > MAX_EVENTS:
        raise InaraError("INARA requests require 1 to 50 events.")
    return {
        "header": {
            "appName": INARA_APP_NAME,
            "appVersion": str(app_version),
            "isBeingDeveloped": True,
            "APIkey": str(config.get("api_key") or ""),
            "commanderName": str(config.get("commander_name") or ""),
            "commanderFrontierID": str(config.get("frontier_id") or ""),
        },
        "events": events,
    }


def material_event(materials):
    inventory = [
        {
            "itemName": str(row.get("key") or row.get("name") or ""),
            "itemCount": max(0, int(row.get("have", 0) or 0)),
        }
        for row in materials
        if isinstance(row, dict)
        and (row.get("key") or row.get("name"))
        and int(row.get("have", 0) or 0) > 0
    ]
    return build_event("setCommanderInventoryMaterials", inventory)


def profile_event(commander_name):
    return build_event(
        "getCommanderProfile", {"searchName": str(commander_name or "")}
    )


def community_goals_event(timestamp=None):
    return build_event("getCommunityGoalsRecent", [], timestamp)


def _journal_timestamp(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _is_live_version(event):
    marker = " ".join(str(event.get(key) or "") for key in (
        "gameversion", "build", "GameVersion", "GameBuild",
    )).casefold()
    if event.get("Beta") is True or any(
        word in marker for word in ("alpha", "beta", "test", "legacy")
    ):
        return False
    version = re.search(r"(?<!\d)(\d+)\.(\d+)", marker)
    return not version or int(version.group(1)) >= 4


def _travel_data(event, context, include_station=False, include_jump=False):
    data = {}
    system = event.get("StarSystem") or context.get("StarSystem")
    if system:
        data["starsystemName"] = str(system)
    coords = event.get("StarPos") or context.get("StarPos")
    if isinstance(coords, list) and len(coords) == 3:
        data["starsystemCoords"] = coords
    if include_station:
        station = event.get("StationName") or context.get("StationName")
        if station:
            data["stationName"] = str(station)
        if event.get("MarketID") is not None:
            data["marketID"] = event["MarketID"]
    if event.get("Body"):
        data["starsystemBodyName"] = str(event["Body"])
    latitude, longitude = event.get("Latitude"), event.get("Longitude")
    if latitude is not None and longitude is not None:
        data["starsystemBodyCoords"] = [latitude, longitude]
    if include_jump and event.get("JumpDist") is not None:
        data["jumpDistance"] = event["JumpDist"]
    ship = event.get("Ship") or context.get("Ship")
    ship_id = event.get("ShipID") or context.get("ShipID")
    if ship:
        data["shipType"] = str(ship)
    if ship_id is not None:
        data["shipGameID"] = ship_id
    if event.get("Taxi") is not None:
        data["isTaxiShuttle"] = bool(event["Taxi"])
    return data


def _materials_data(event):
    rows = []
    for category in ("Raw", "Manufactured", "Encoded"):
        for item in event.get(category, []) or []:
            if not isinstance(item, dict) or not item.get("Name"):
                continue
            count = max(0, int(item.get("Count", 0) or 0))
            if count:
                rows.append({"itemName": str(item["Name"]), "itemCount": count})
    return sorted(rows, key=lambda row: row["itemName"])


def _rank_data(event, progress=False):
    field = "rankProgress" if progress else "rankValue"
    rows = []
    for journal_key, inara_name in _RANK_KEYS.items():
        if event.get(journal_key) is None:
            continue
        value = float(event[journal_key]) / 100.0 if progress else int(event[journal_key])
        rows.append({"rankName": inara_name, field: value})
    return rows


def _reputation_data(event):
    return [
        {
            "majorfactionName": inara_name,
            "majorfactionReputation": float(event[journal_key]) / 100.0,
        }
        for journal_key, inara_name in _REPUTATION_KEYS.items()
        if event.get(journal_key) is not None
    ]


def _cargo_data(event):
    inventory = {}
    for item in event.get("Inventory", []) or []:
        if not isinstance(item, dict) or not item.get("Name"):
            continue
        count = max(0, int(item.get("Count", 0) or 0))
        if count:
            key = (
                str(item["Name"]),
                int(item["MissionID"]) if item.get("MissionID") is not None else None,
                bool(item.get("Stolen", False)),
            )
            inventory[key] = inventory.get(key, 0) + count
    rows = []
    for (item_name, mission_id, is_stolen), count in inventory.items():
        row = {
            "itemName": item_name, "itemCount": count,
            "isStolen": is_stolen,
        }
        if mission_id is not None:
            row["missionGameID"] = mission_id
        rows.append(row)
    return sorted(rows, key=lambda row: (
        row["itemName"], row.get("missionGameID", 0), row.get("isStolen", False),
    ))


def _engineering_data(value):
    if not isinstance(value, dict):
        return None
    mapping = {
        "BlueprintName": "blueprintName", "Level": "blueprintLevel",
        "Quality": "blueprintQuality",
        "ExperimentalEffect": "experimentalEffect",
    }
    data = {
        target: value[source] for source, target in mapping.items()
        if value.get(source) is not None
    }
    modifiers = []
    for modifier in value.get("Modifiers", []) or []:
        if not isinstance(modifier, dict) or not modifier.get("Label"):
            continue
        row = {"name": str(modifier["Label"])}
        if modifier.get("Value") is not None:
            row["value"] = modifier["Value"]
        if modifier.get("OriginalValue") is not None:
            row["originalValue"] = modifier["OriginalValue"]
        if modifier.get("LessIsGood") is not None:
            row["lessIsGood"] = bool(modifier["LessIsGood"])
        modifiers.append(row)
    if modifiers:
        data["modifiers"] = modifiers
    return data or None


def _ship_loadout_data(event):
    if event.get("Ship") is None or event.get("ShipID") is None:
        return {}
    modules = []
    mapping = {
        "Slot": "slotName", "Item": "itemName", "Value": "itemValue",
        "Health": "itemHealth", "On": "isOn", "Hot": "isHot",
        "Priority": "itemPriority", "AmmoInClip": "itemAmmoClip",
        "AmmoInHopper": "itemAmmoHopper",
    }
    for module in event.get("Modules", []) or []:
        if not isinstance(module, dict) or not module.get("Slot") or not module.get("Item"):
            continue
        row = {
            target: module[source] for source, target in mapping.items()
            if module.get(source) is not None
        }
        engineering = _engineering_data(module.get("Engineering"))
        if engineering:
            row["engineering"] = engineering
        modules.append(row)
    return {
        "shipType": str(event["Ship"]), "shipGameID": int(event["ShipID"]),
        "shipLoadout": modules,
    }


def _stored_module_row(item, name_field="Name", location=None):
    if not isinstance(item, dict) or not item.get(name_field):
        return None
    row = {"itemName": str(item[name_field])}
    location = location or {}
    mapping = {
        "BuyPrice": "itemValue", "Hot": "isHot",
        "StarSystem": "starsystemName", "StationName": "stationName",
        "MarketID": "marketID",
    }
    row.update({
        target: item[source] for source, target in mapping.items()
        if item.get(source) is not None
    })
    row.update({
        target: location[source] for source, target in mapping.items()
        if target not in row and location.get(source) is not None
    })
    if item.get("EngineerModifications"):
        row["engineering"] = {
            "blueprintName": item["EngineerModifications"],
            **({"blueprintLevel": item["Level"]} if item.get("Level") is not None else {}),
            **({"blueprintQuality": item["Quality"]} if item.get("Quality") is not None else {}),
        }
    if item.get("StorageSlot") is not None:
        row["_storageSlot"] = int(item["StorageSlot"])
    return row


def _stored_modules_public(rows):
    public = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    return sorted(public, key=lambda row: json.dumps(
        row, sort_keys=True, separators=(",", ":"),
    ))


def _stored_modules_data(event):
    rows = []
    for item in event.get("Items", []) or []:
        row = _stored_module_row(item)
        if row:
            rows.append(row)
    return _stored_modules_public(rows)


def _engineers_data(event):
    rows = []
    for engineer in event.get("Engineers", []) or []:
        if not isinstance(engineer, dict) or not engineer.get("Engineer"):
            continue
        row = {"engineerName": str(engineer["Engineer"])}
        if engineer.get("Progress") in {"Invited", "Acquainted", "Unlocked", "Barred"}:
            row["rankStage"] = engineer["Progress"]
        if engineer.get("Rank") is not None:
            row["rankValue"] = max(1, min(5, int(engineer["Rank"])))
        if len(row) > 1:
            rows.append(row)
    return rows


def _mission_data(event, context):
    mapping = {
        "Name": "missionName", "MissionID": "missionGameID",
        "Expiry": "missionExpiry", "Influence": "influenceGain",
        "Reputation": "reputationGain", "Faction": "minorfactionNameOrigin",
        "DestinationSystem": "starsystemNameTarget",
        "DestinationStation": "stationNameTarget",
        "TargetFaction": "minorfactionNameTarget", "Commodity": "commodityName",
        "Count": "commodityCount", "Target": "targetName",
        "TargetType": "targetType", "KillCount": "killCount",
        "PassengerType": "passengerType", "PassengerCount": "passengerCount",
        "PassengerVIPs": "passengerIsVIP", "PassengerWanted": "passengerIsWanted",
    }
    data = {
        target: event[source] for source, target in mapping.items()
        if event.get(source) is not None
    }
    if context.get("StarSystem"):
        data["starsystemNameOrigin"] = context["StarSystem"]
    if context.get("StationName"):
        data["stationNameOrigin"] = context["StationName"]
    return data if data.get("missionName") and data.get("missionGameID") is not None else {}


def _mission_completion_data(event):
    if event.get("MissionID") is None:
        return {}
    data = {"missionGameID": event["MissionID"]}
    if event.get("Donation") is not None:
        data["donationCredits"] = event["Donation"]
    if event.get("Reward") is not None:
        data["rewardCredits"] = event["Reward"]
    data["rewardPermits"] = [
        {"starsystemName": name}
        for value in event.get("PermitsAwarded", []) or []
        if (name := _permit_name(value))
    ]
    for source, target in (("CommodityReward", "rewardCommodities"),
                           ("MaterialsReward", "rewardMaterials")):
        rows = []
        for item in event.get(source, []) or []:
            if isinstance(item, dict) and item.get("Name"):
                rows.append({"itemName": item["Name"], "itemCount": int(item.get("Count", 0) or 0)})
        if rows:
            data[target] = rows
    effects = []
    for effect in event.get("FactionEffects", []) or []:
        if not isinstance(effect, dict) or not effect.get("Faction"):
            continue
        row = {"minorfactionName": effect["Faction"]}
        if isinstance(effect.get("Influence"), str):
            row["influenceGain"] = effect["Influence"]
        if isinstance(effect.get("Reputation"), str):
            row["reputationGain"] = effect["Reputation"]
        if len(row) > 1:
            effects.append(row)
    if effects:
        data["minorfactionEffects"] = effects
    return data


def _permit_name(value):
    """Return the documented permit system name, tolerating object-shaped imports."""
    if isinstance(value, dict):
        value = value.get("System") or value.get("Name") or value.get("StarSystem")
    value = str(value or "").strip()
    return value


def _minor_reputation_data(event):
    rows = []
    for faction in event.get("Factions", []) or []:
        if not isinstance(faction, dict) or not faction.get("Name") or faction.get("MyReputation") is None:
            continue
        rows.append({
            "minorfactionName": faction["Name"],
            "minorfactionReputation": float(faction["MyReputation"]) / 100.0,
        })
    return rows


def _locker_data(event):
    rows = []
    for source, item_type in (("Items", "Item"), ("Components", "Component"),
                              ("Consumables", "Consumable"), ("Data", "Data")):
        for item in event.get(source, []) or []:
            if not isinstance(item, dict) or not item.get("Name"):
                continue
            row = {
                "itemName": item["Name"], "itemCount": int(item.get("Count", 0) or 0),
                "itemType": item_type, "itemLocation": "ShipLocker",
            }
            if item.get("MissionID") is not None:
                row["missionGameID"] = int(item["MissionID"])
            if row["itemCount"] > 0:
                rows.append(row)
    return sorted(rows, key=lambda row: (
        row["itemType"], row["itemName"], row.get("missionGameID", 0),
    ))


def _suit_loadout_data(event):
    if event.get("LoadoutID") is None:
        return {}
    data = {"loadoutGameID": int(event["LoadoutID"])}
    mapping = {
        "LoadoutName": "loadoutName", "SuitID": "suitGameID",
        "SuitName": "suitType",
    }
    data.update({target: event[source] for source, target in mapping.items()
                 if event.get(source) is not None})
    modules = []
    for module in event.get("Modules", []) or []:
        if not isinstance(module, dict) or not module.get("SlotName") or not module.get("ModuleName"):
            continue
        row = {"slotName": module["SlotName"], "itemName": module["ModuleName"]}
        if module.get("Class") is not None:
            row["itemClass"] = int(module["Class"])
        if module.get("SuitModuleID") is not None:
            row["itemGameID"] = int(module["SuitModuleID"])
        if isinstance(module.get("WeaponMods"), list):
            row["engineering"] = [
                {"blueprintName": value} for value in module["WeaponMods"] if value
            ]
        modules.append(row)
    if modules or isinstance(event.get("Modules"), list):
        data["suitLoadout"] = modules
    if isinstance(event.get("SuitMods"), list):
        data["suitMods"] = list(event["SuitMods"])
    return data


def prepare_journal_batch(events, known_fingerprints=(), expected_identity="",
                          now=None, max_events=MAX_EVENTS):
    """Convert explicitly approved live Journal changes into a safe INARA batch."""
    known = {str(value) for value in known_fingerprints}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    context = {}
    live_file = False
    live_session = False
    identity = {"commander_name": "", "frontier_id": ""}
    prepared = []
    fingerprints = []
    latest_credits = None
    pending_loadgame_credits = None
    material_inventory = None
    cargo_inventory = None
    fleet_state = {}
    fleet_initialized = False
    pending_ship_purchase = None
    storage_inventory = None
    locker_inventory = None
    known_ship_types = set()
    powerplay_state = {"power": "", "rank": None, "merits": None}
    suit_loadouts = {}
    weapon_loadouts = {}
    for candidate in events:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("event") == "Loadout" and candidate.get("Ship"):
            known_ship_types.add(str(candidate["Ship"]).casefold())
        if candidate.get("event") in {
            "ShipyardNew", "ShipyardSell", "ShipyardSwap", "ShipyardTransfer",
        } and candidate.get("ShipType"):
            known_ship_types.add(str(candidate["ShipType"]).casefold())
        if candidate.get("event") == "StoredShips":
            for group in ("ShipsHere", "ShipsRemote"):
                for ship in candidate.get(group, []) or []:
                    if isinstance(ship, dict) and ship.get("ShipType"):
                        known_ship_types.add(str(ship["ShipType"]).casefold())

    def append(name, data, timestamp, allow_empty=False, fingerprint_data=None):
        if name not in AUTO_UPLOAD_EVENT_NAMES:
            return
        if not timestamp or (not data and not allow_empty):
            return
        event = build_event(name, data, timestamp)
        fingerprint_value = fingerprint_data if fingerprint_data is not None else (
            {"eventName": name, "eventData": data}
            if name.startswith("setCommander") else event
        )
        fingerprint = hashlib.sha256(json.dumps(
            fingerprint_value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if fingerprint in known or fingerprint in fingerprints:
            return
        prepared.append(event)
        fingerprints.append(fingerprint)

    def append_powerplay(timestamp):
        if not powerplay_state["power"] or powerplay_state["rank"] is None:
            return
        append("setCommanderRankPower", {
            "powerName": powerplay_state["power"],
            "rankValue": int(powerplay_state["rank"]),
            **({"meritsValue": int(powerplay_state["merits"])}
               if powerplay_state["merits"] is not None else {}),
        }, timestamp)

    def adjust_material(item, count_field, direction):
        if material_inventory is None or not isinstance(item, dict) or not item.get("Name"):
            return False
        try:
            count = int(item.get(count_field))
        except (TypeError, ValueError):
            return False
        material = str(item["Name"])
        material_inventory[material] = max(
            0, material_inventory.get(material, 0) + direction * count,
        )
        return True

    def append_material_snapshot(timestamp):
        append(
            "setCommanderInventoryMaterials",
            [
                {"itemName": material, "itemCount": count}
                for material, count in sorted(material_inventory.items()) if count > 0
            ],
            timestamp, allow_empty=True,
        )

    def adjust_cargo(item_name, count, direction, is_stolen=None, mission_id=None):
        if cargo_inventory is None or not item_name:
            return False
        try:
            remaining = int(count)
        except (TypeError, ValueError):
            return False
        if remaining <= 0:
            return False
        item_name = str(item_name)
        mission_id = int(mission_id) if mission_id is not None else None
        if direction > 0:
            key = (item_name, mission_id, bool(is_stolen) if is_stolen is not None else False)
            cargo_inventory[key] = cargo_inventory.get(key, 0) + remaining
            return True
        candidates = sorted(
            (
                key for key in cargo_inventory
                if key[0] == item_name
                and (mission_id is None or key[1] == mission_id)
                and (is_stolen is None or key[2] == bool(is_stolen))
            ),
            key=lambda key: (
                key[1] is not None,
                key[2],
            ),
        )
        changed = False
        for key in candidates:
            if remaining <= 0:
                break
            available = cargo_inventory.get(key, 0)
            removed = min(available, remaining)
            if removed:
                cargo_inventory[key] = available - removed
                remaining -= removed
                changed = True
        return changed

    def append_cargo_snapshot(timestamp):
        rows = []
        for (item_name, mission_id, is_stolen), count in cargo_inventory.items():
            if count <= 0:
                continue
            row = {
                "itemName": item_name, "itemCount": count,
                "isStolen": is_stolen,
            }
            if mission_id is not None:
                row["missionGameID"] = mission_id
            rows.append(row)
        append("setCommanderInventoryCargo", sorted(rows, key=lambda row: (
            row["itemName"], row.get("missionGameID", 0), row.get("isStolen", False),
        )), timestamp, allow_empty=True)

    def storage_location(source):
        return {
            "StarSystem": source.get("StarSystem") or context.get("StarSystem"),
            "StationName": source.get("StationName") or context.get("StationName"),
            "MarketID": (source.get("MarketID") if source.get("MarketID") is not None
                         else context.get("MarketID")),
        }

    def append_storage_snapshot(timestamp):
        append(
            "setCommanderStorageModules",
            _stored_modules_public(storage_inventory),
            timestamp, allow_empty=True,
        )

    def remove_stored_module(item_name=None, storage_slot=None, hot=None,
                             engineering=None):
        if storage_inventory is None:
            return False
        matches = []
        for index, row in enumerate(storage_inventory):
            if storage_slot is not None and row.get("_storageSlot") != int(storage_slot):
                continue
            if item_name and row.get("itemName") != str(item_name):
                continue
            if hot is not None and bool(row.get("isHot", False)) != bool(hot):
                continue
            if (engineering and row.get("engineering", {}).get("blueprintName")
                    != str(engineering)):
                continue
            matches.append(index)
        if not matches:
            return False
        storage_inventory.pop(matches[0])
        return True

    def locker_item_type(value):
        return {
            "item": "Item", "items": "Item",
            "component": "Component", "components": "Component",
            "consumable": "Consumable", "consumables": "Consumable",
            "data": "Data",
        }.get(str(value or "").casefold())

    def adjust_locker(item, direction, name_field="Name", count_field="Count"):
        if locker_inventory is None or not isinstance(item, dict):
            return False
        item_name = str(item.get(name_field) or "").strip()
        item_type = locker_item_type(item.get("Category") or item.get("Type"))
        if not item_name:
            return False
        if not item_type:
            known_types = {
                key[1] for key in locker_inventory if key[0] == item_name
            }
            item_type = next(iter(known_types)) if len(known_types) == 1 else None
        if not item_type:
            return False
        try:
            remaining = int(item.get(count_field, 1) or 1)
        except (TypeError, ValueError):
            return False
        if remaining <= 0:
            return False
        mission_id = item.get("MissionID")
        mission_id = int(mission_id) if mission_id is not None else None
        if direction > 0:
            key = (item_name, item_type, mission_id)
            locker_inventory[key] = locker_inventory.get(key, 0) + remaining
            return True
        candidates = sorted(
            (
                key for key in locker_inventory
                if key[0] == item_name and key[1] == item_type
                and (mission_id is None or key[2] == mission_id)
            ),
            key=lambda key: key[2] is not None,
        )
        changed = False
        for key in candidates:
            if remaining <= 0:
                break
            available = locker_inventory.get(key, 0)
            removed = min(available, remaining)
            if removed:
                locker_inventory[key] = available - removed
                remaining -= removed
                changed = True
        return changed

    def locker_snapshot_rows():
        rows = []
        for (item_name, item_type, mission_id), count in locker_inventory.items():
            if count <= 0:
                continue
            row = {
                "itemName": item_name, "itemCount": count,
                "itemType": item_type, "itemLocation": "ShipLocker",
            }
            if mission_id is not None:
                row["missionGameID"] = mission_id
            rows.append(row)
        return sorted(rows, key=lambda row: (
            row["itemType"], row["itemName"], row.get("missionGameID", 0),
        ))

    def append_locker_snapshot(timestamp):
        rows = locker_snapshot_rows()
        reset = [
            {"itemType": item_type, "itemLocation": "ShipLocker"}
            for item_type in ("Item", "Component", "Consumable", "Data")
        ]
        append(
            "resetCommanderInventory", reset, timestamp,
            fingerprint_data={"eventName": "resetCommanderInventory", "lockerSnapshot": rows},
        )
        if rows:
            append("setCommanderInventory", rows, timestamp)

    for source in events:
        if not isinstance(source, dict):
            continue
        name = str(source.get("event") or "")
        if name == "Fileheader":
            live_file = _is_live_version(source)
            live_session = False
            context = {}
            material_inventory = None
            cargo_inventory = None
            fleet_state = {}
            fleet_initialized = False
            pending_ship_purchase = None
            storage_inventory = None
            locker_inventory = None
            continue
        if name == "LoadGame":
            frontier_id = str(source.get("FID") or "").strip()
            commander = str(source.get("Commander") or "").strip()
            matches = not expected_identity or expected_identity in {
                frontier_id, commander,
            }
            live_session = live_file and _is_live_version(source) and matches
            if live_session:
                identity = {
                    "commander_name": commander,
                    "frontier_id": frontier_id,
                }
                journal_ship = str(source.get("Ship") or "")
                confirmed_ship = journal_ship.casefold() in known_ship_types
                if confirmed_ship:
                    context["Ship"] = source["Ship"]
                    if source.get("ShipID") is not None:
                        context["ShipID"] = source["ShipID"]
                if source.get("Credits") is not None:
                    latest_credits = {
                        "commanderCredits": int(source.get("Credits") or 0),
                        "commanderLoan": int(source.get("Loan", 0) or 0),
                    }
                timestamp = _journal_timestamp(source.get("timestamp"))
                if source.get("Credits") is not None:
                    pending_loadgame_credits = ({
                        "commanderCredits": int(source.get("Credits") or 0),
                        "commanderLoan": int(source.get("Loan", 0) or 0),
                    }, timestamp)
                if confirmed_ship and source.get("ShipID") is not None:
                    append("setCommanderShip", {
                        "shipType": str(source["Ship"]),
                        "shipGameID": int(source["ShipID"]),
                        "isCurrentShip": True,
                    }, timestamp)
            continue
        if not live_session:
            continue
        for key in ("StarSystem", "StarPos", "StationName", "MarketID",
                    "Ship", "ShipID"):
            if source.get(key) is not None:
                context[key] = source[key]
        timestamp = _journal_timestamp(source.get("timestamp"))
        if not timestamp:
            continue
        event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if (now.astimezone(timezone.utc) - event_time).days > 30:
            continue
        mapping = {
            "Location": ("setCommanderTravelLocation", True, False),
            "Docked": ("addCommanderTravelDock", True, False),
            "FSDJump": ("addCommanderTravelFSDJump", False, True),
        }.get(name)
        if mapping:
            event_name, station, jump = mapping
            data = _travel_data(source, context, station, jump)
            if data.get("starsystemName"):
                append(event_name, data, timestamp)
            reputation = _minor_reputation_data(source)
            if reputation:
                append("setCommanderReputationMinorFaction", reputation, timestamp)
        elif name == "Materials":
            material_rows = _materials_data(source)
            material_inventory = {
                row["itemName"]: int(row["itemCount"]) for row in material_rows
            }
            append(
                "setCommanderInventoryMaterials", material_rows,
                timestamp, allow_empty=True,
            )
        elif name == "MaterialTrade" and material_inventory is not None:
            changed = False
            for item, direction in ((source.get("Paid"), -1), (source.get("Received"), 1)):
                if isinstance(item, dict) and item.get("Material"):
                    changed = adjust_material(
                        {"Name": item["Material"], "Quantity": item.get("Quantity")},
                        "Quantity", direction,
                    ) or changed
            if changed:
                append_material_snapshot(timestamp)
        elif name in {"MaterialCollected", "MaterialDiscarded"} and material_inventory is not None:
            if adjust_material(
                source, "Count", 1 if name == "MaterialCollected" else -1,
            ):
                append_material_snapshot(timestamp)
        elif name in {"EngineerCraft", "Synthesis"} and material_inventory is not None:
            field = "Ingredients" if name == "EngineerCraft" else "Materials"
            changed = False
            for item in source.get(field, []) or []:
                changed = adjust_material(item, "Count", -1) or changed
            if changed:
                append_material_snapshot(timestamp)
        elif name == "Rank":
            append("setCommanderRankPilot", _rank_data(source), timestamp)
        elif name == "Progress":
            append("setCommanderRankPilot", _rank_data(source, True), timestamp)
        elif name == "Reputation":
            append("setCommanderReputationMajorFaction", _reputation_data(source), timestamp)
        elif name == "Statistics":
            statistics = {
                key: value for key, value in source.items()
                if key not in {"event", "timestamp"} and isinstance(value, dict)
            }
            append("setCommanderGameStatistics", statistics, timestamp)
            bank = source.get("Bank_Account")
            if latest_credits is not None and isinstance(bank, dict) and bank.get("Current_Wealth") is not None:
                append("setCommanderCredits", {
                    **latest_credits,
                    "commanderAssets": int(bank.get("Current_Wealth") or 0),
                }, timestamp)
                pending_loadgame_credits = None
        elif name == "EngineerProgress":
            append("setCommanderRankEngineer", _engineers_data(source), timestamp)
        elif name == "Powerplay" and source.get("Power") and source.get("Rank") is not None:
            powerplay_state.update({
                "power": str(source["Power"]), "rank": int(source["Rank"]),
                "merits": (int(source["Merits"])
                           if source.get("Merits") is not None else None),
            })
            append_powerplay(timestamp)
        elif name == "PowerplayRank" and source.get("Power") and source.get("Rank") is not None:
            powerplay_state.update({
                "power": str(source["Power"]), "rank": int(source["Rank"]),
            })
            append_powerplay(timestamp)
        elif (name == "PowerplayMerits" and source.get("Power")
              and source.get("TotalMerits") is not None):
            powerplay_state.update({
                "power": str(source["Power"]),
                "merits": int(source["TotalMerits"]),
            })
            append_powerplay(timestamp)
        elif name == "Cargo" and isinstance(source.get("Inventory"), list):
            cargo_rows = _cargo_data(source)
            cargo_inventory = {
                (
                    row["itemName"], row.get("missionGameID"),
                    bool(row.get("isStolen", False)),
                ): int(row["itemCount"])
                for row in cargo_rows
            }
            append("setCommanderInventoryCargo", cargo_rows, timestamp, allow_empty=True)
        elif name in {"MarketBuy", "MarketSell"} and cargo_inventory is not None:
            stolen = bool(source.get("StolenGoods")) if name == "MarketSell" else False
            if adjust_cargo(
                source.get("Type"), source.get("Count"),
                1 if name == "MarketBuy" else -1, stolen,
            ):
                append_cargo_snapshot(timestamp)
        elif name in {"MiningRefined", "CollectCargo"} and cargo_inventory is not None:
            stolen = source.get("Stolen") if name == "CollectCargo" else False
            if adjust_cargo(
                source.get("Type"), 1, 1, stolen, source.get("MissionID"),
            ):
                append_cargo_snapshot(timestamp)
        elif name == "EjectCargo" and cargo_inventory is not None:
            if adjust_cargo(
                source.get("Type"), source.get("Count"), -1,
                mission_id=source.get("MissionID"),
            ):
                append_cargo_snapshot(timestamp)
        elif name == "CargoTransfer" and cargo_inventory is not None:
            changed = False
            for transfer in source.get("Transfers", []) or []:
                if not isinstance(transfer, dict):
                    continue
                direction = str(transfer.get("Direction") or "").casefold()
                if direction not in {"toship", "tocarrier", "tosrv"}:
                    continue
                changed = adjust_cargo(
                    transfer.get("Type"), transfer.get("Count"),
                    1 if direction == "toship" else -1,
                ) or changed
            if changed:
                append_cargo_snapshot(timestamp)
        elif name == "SearchAndRescue" and cargo_inventory is not None:
            if adjust_cargo(source.get("Name"), source.get("Count"), -1):
                append_cargo_snapshot(timestamp)
        elif name in {"PowerplayDeliver", "PowerplayCollect"} and cargo_inventory is not None:
            if adjust_cargo(
                source.get("Type"), source.get("Count"),
                1 if name == "PowerplayCollect" else -1,
            ):
                append_cargo_snapshot(timestamp)
        elif name == "Loadout":
            ship_data = {
                "shipType": str(source["Ship"]),
                "shipGameID": int(source["ShipID"]),
                "isCurrentShip": True,
            }
            loadout_mapping = {
                "ShipName": "shipName", "ShipIdent": "shipIdent",
                "Hot": "isHot", "HullValue": "shipHullValue",
                "ModulesValue": "shipModulesValue", "Rebuy": "shipRebuyCost",
                "MaxJumpRange": "shipMaxJumpRange",
                "CargoCapacity": "shipCargoCapacity",
            }
            ship_data.update({
                target: source[key] for key, target in loadout_mapping.items()
                if source.get(key) is not None
            })
            fleet_initialized = True
            fleet_state[int(source["ShipID"])] = dict(ship_data)
            append("setCommanderShip", ship_data, timestamp)
            append("setCommanderShipLoadout", _ship_loadout_data(source), timestamp)
        elif name == "StoredModules" and isinstance(source.get("Items"), list):
            storage_inventory = []
            for item in source.get("Items", []) or []:
                row = _stored_module_row(item)
                if row:
                    storage_inventory.append(row)
            append_storage_snapshot(timestamp)
        elif name == "ModuleBuy" and storage_inventory is not None and source.get("StoredItem"):
            row = _stored_module_row(
                {"StoredItem": source["StoredItem"]},
                "StoredItem", storage_location(source),
            )
            if row:
                storage_inventory.append(row)
                append_storage_snapshot(timestamp)
        elif name == "ModuleStore" and storage_inventory is not None and source.get("StoredItem"):
            row = _stored_module_row(
                source, "StoredItem", storage_location(source),
            )
            if row:
                storage_inventory.append(row)
                append_storage_snapshot(timestamp)
        elif (name == "ModuleSellRemote" and storage_inventory is not None
              and source.get("StorageSlot") is not None):
            if remove_stored_module(
                source.get("SellItem"), storage_slot=source["StorageSlot"],
            ):
                append_storage_snapshot(timestamp)
        elif (name == "ModuleRetrieve" and storage_inventory is not None
              and source.get("RetrievedItem")):
            changed = remove_stored_module(
                source["RetrievedItem"], hot=source.get("Hot"),
                engineering=source.get("EngineerModifications"),
            )
            if source.get("SwapOutItem"):
                row = _stored_module_row(
                    {"SwapOutItem": source["SwapOutItem"]},
                    "SwapOutItem", storage_location(source),
                )
                if row:
                    storage_inventory.append(row)
                    changed = True
            if changed:
                append_storage_snapshot(timestamp)
        elif name == "MassModuleStore" and storage_inventory is not None:
            additions = []
            for item in source.get("Items", []) or []:
                row = _stored_module_row(item, location=storage_location(source))
                if row:
                    additions.append(row)
            if additions:
                storage_inventory.extend(additions)
                append_storage_snapshot(timestamp)
        elif name in {"ShipLocker", "ShipLockerMaterials"}:
            locker = _locker_data(source)
            locker_inventory = {
                (
                    row["itemName"], row["itemType"], row.get("missionGameID"),
                ): int(row["itemCount"])
                for row in locker
            }
            append_locker_snapshot(timestamp)
        elif name == "BuyMicroResources" and locker_inventory is not None:
            items = source.get("MicroResources")
            items = items if isinstance(items, list) else [source]
            changed = False
            for item in items:
                changed = adjust_locker(item, 1) or changed
            if changed:
                append_locker_snapshot(timestamp)
        elif name == "SellMicroResources" and locker_inventory is not None:
            changed = False
            for item in source.get("MicroResources", []) or []:
                changed = adjust_locker(item, -1) or changed
            if changed:
                append_locker_snapshot(timestamp)
        elif name == "TradeMicroResources" and locker_inventory is not None:
            changed = False
            for item in source.get("Offered", []) or []:
                changed = adjust_locker(item, -1) or changed
            received = {
                "Name": source.get("Received"), "Category": source.get("Category"),
                "Count": source.get("Count"),
            }
            changed = adjust_locker(received, 1) or changed
            if changed:
                append_locker_snapshot(timestamp)
        elif name == "TransferMicroResources" and locker_inventory is not None:
            changed = False
            for item in source.get("Transfers", []) or []:
                if not isinstance(item, dict):
                    continue
                direction = str(item.get("Direction") or "").casefold()
                if direction not in {"tobackpack", "toshiplocker"}:
                    continue
                changed = adjust_locker(
                    item, -1 if direction == "tobackpack" else 1,
                ) or changed
            if changed:
                append_locker_snapshot(timestamp)
        elif name == "ShipyardBuy" and fleet_initialized and source.get("ShipType"):
            if source.get("SellOldShip") and source.get("SellShipID") is not None:
                old_id = int(source["SellShipID"])
                append("delCommanderShip", {
                    "shipType": source["SellOldShip"], "shipGameID": old_id,
                }, timestamp)
                fleet_state.pop(old_id, None)
            elif source.get("StoreOldShip") and source.get("StoreShipID") is not None:
                old_id = int(source["StoreShipID"])
                old_ship = dict(fleet_state.get(old_id, {
                    "shipType": source["StoreOldShip"], "shipGameID": old_id,
                }))
                old_ship["isCurrentShip"] = False
                if context.get("StarSystem"):
                    old_ship["starsystemName"] = context["StarSystem"]
                if context.get("StationName"):
                    old_ship["stationName"] = context["StationName"]
                if context.get("MarketID") is not None:
                    old_ship["marketID"] = context["MarketID"]
                fleet_state[old_id] = old_ship
                append("setCommanderShip", old_ship, timestamp)
            pending_ship_purchase = {"shipType": source["ShipType"]}
        elif (name == "ShipyardNew" and fleet_initialized and source.get("ShipType")
              and source.get("NewShipID") is not None):
            ship_id = int(source["NewShipID"])
            ship_data = {
                "shipType": source["ShipType"], "shipGameID": ship_id,
                "isCurrentShip": True,
            }
            if (pending_ship_purchase
                    and pending_ship_purchase.get("shipType") == source["ShipType"]):
                ship_data.update(pending_ship_purchase)
            fleet_state[ship_id] = dict(ship_data)
            pending_ship_purchase = None
            append("addCommanderShip", ship_data, timestamp)
        elif (name == "ShipyardSell" and fleet_initialized and source.get("ShipType")
              and source.get("SellShipID") is not None):
            ship_id = int(source["SellShipID"])
            append("delCommanderShip", {
                "shipType": source["ShipType"], "shipGameID": ship_id,
            }, timestamp)
            fleet_state.pop(ship_id, None)
        elif (name == "SellShipOnRebuy" and source.get("ShipType")
              and source.get("SellShipId") is not None and fleet_initialized):
            ship_id = int(source["SellShipId"])
            append("delCommanderShip", {
                "shipType": source["ShipType"],
                "shipGameID": ship_id,
            }, timestamp)
            fleet_state.pop(ship_id, None)
        elif (name == "ShipyardSwap" and fleet_initialized and source.get("ShipType")
              and source.get("ShipID") is not None):
            if source.get("SellOldShip") and source.get("SellShipID") is not None:
                old_id = int(source["SellShipID"])
                append("delCommanderShip", {
                    "shipType": source["SellOldShip"],
                    "shipGameID": old_id,
                }, timestamp)
                fleet_state.pop(old_id, None)
            elif source.get("StoreOldShip") and source.get("StoreShipID") is not None:
                old_id = int(source["StoreShipID"])
                old_ship = dict(fleet_state.get(old_id, {
                    "shipType": source["StoreOldShip"], "shipGameID": old_id,
                }))
                old_ship["isCurrentShip"] = False
                if context.get("StarSystem"):
                    old_ship["starsystemName"] = context["StarSystem"]
                if context.get("StationName"):
                    old_ship["stationName"] = context["StationName"]
                if context.get("MarketID") is not None:
                    old_ship["marketID"] = context["MarketID"]
                fleet_state[old_id] = old_ship
                append("setCommanderShip", old_ship, timestamp)
            new_ship = {
                "shipType": source["ShipType"], "shipGameID": int(source["ShipID"]),
                "isCurrentShip": True,
            }
            fleet_state[int(source["ShipID"])] = dict(new_ship)
            append("setCommanderShip", new_ship, timestamp)
        elif name == "StoredShips":
            fleet_initialized = True
            for ship in source.get("ShipsHere", []) or []:
                if not isinstance(ship, dict) or not ship.get("ShipType") or ship.get("ShipID") is None:
                    continue
                ship_data = {
                    "shipType": ship["ShipType"], "shipGameID": int(ship["ShipID"]),
                    **({"shipName": ship["Name"]} if ship.get("Name") else {}),
                    **({"isHot": bool(ship["Hot"])} if ship.get("Hot") is not None else {}),
                    **({"starsystemName": context["StarSystem"]} if context.get("StarSystem") else {}),
                    **({"stationName": source["StationName"]} if source.get("StationName") else {}),
                    **({"marketID": source["MarketID"]} if source.get("MarketID") is not None else {}),
                }
                fleet_state[int(ship["ShipID"])] = dict(ship_data)
                append("setCommanderShip", ship_data, timestamp)
            for ship in source.get("ShipsRemote", []) or []:
                if (not isinstance(ship, dict) or not ship.get("ShipType")
                        or ship.get("ShipID") is None or not ship.get("StarSystem")):
                    continue
                transfer_data = {
                    "shipType": ship["ShipType"], "shipGameID": int(ship["ShipID"]),
                    "starsystemName": ship["StarSystem"],
                    **({"marketID": ship["ShipMarketID"]} if ship.get("ShipMarketID") is not None else {}),
                    **({"transferTime": int(ship["TransferTime"])} if ship.get("TransferTime") is not None else {}),
                }
                fleet_state[int(ship["ShipID"])] = dict(transfer_data)
                append("setCommanderShipTransfer", transfer_data, timestamp)
        elif (name == "SetUserShipName" and fleet_initialized and source.get("Ship")
              and source.get("ShipID") is not None
              and int(source["ShipID"]) in fleet_state):
            ship_id = int(source["ShipID"])
            ship_data = dict(fleet_state[ship_id])
            ship_data.update({
                "shipType": source["Ship"], "shipGameID": int(source["ShipID"]),
                **({"shipName": source["UserShipName"]} if source.get("UserShipName") is not None else {}),
                **({"shipIdent": source["UserShipId"]} if source.get("UserShipId") is not None else {}),
            })
            fleet_state[ship_id] = dict(ship_data)
            append("setCommanderShip", ship_data, timestamp)
        elif (name == "ShipyardTransfer" and fleet_initialized and source.get("ShipType")
              and source.get("ShipID") is not None and context.get("StarSystem")
              and int(source["ShipID"]) in fleet_state):
            transfer_data = {
                "shipType": source["ShipType"], "shipGameID": int(source["ShipID"]),
                "starsystemName": context["StarSystem"],
                **({"stationName": context["StationName"]} if context.get("StationName") else {}),
                **({"marketID": context["MarketID"]} if context.get("MarketID") is not None else {}),
                **({"transferTime": int(source["TransferTime"])} if source.get("TransferTime") is not None else {}),
            }
            fleet_state[int(source["ShipID"])] = dict(transfer_data)
            append("setCommanderShipTransfer", transfer_data, timestamp)
        elif name in {"SuitLoadout", "CreateSuitLoadout"}:
            suit_data = _suit_loadout_data(source)
            append("setCommanderSuitLoadout", suit_data, timestamp)
            loadout_id = suit_data.get("loadoutGameID")
            if loadout_id is not None:
                suit_loadouts[loadout_id] = suit_data
                for module in suit_data.get("suitLoadout", []) or []:
                    if module.get("itemGameID") is not None:
                        weapon_loadouts[module["itemGameID"]] = loadout_id
        elif name in {"RenameSuitLoadout", "SwitchSuitLoadout"} and source.get("LoadoutID") is not None:
            suit_data = _suit_loadout_data(source)
            append("updateCommanderSuitLoadout", suit_data, timestamp)
            loadout_id = suit_data.get("loadoutGameID")
            if loadout_id in suit_loadouts:
                suit_loadouts[loadout_id].update(suit_data)
        elif name == "UpgradeWeapon" and source.get("SuitModuleID") is not None:
            weapon_id = int(source["SuitModuleID"])
            loadout_id = weapon_loadouts.get(weapon_id)
            cached = suit_loadouts.get(loadout_id)
            if cached:
                updated = {
                    key: ([dict(row) for row in value] if key == "suitLoadout" else value)
                    for key, value in cached.items()
                }
                for module in updated.get("suitLoadout", []) or []:
                    if module.get("itemGameID") == weapon_id:
                        if source.get("Name"):
                            module["itemName"] = source["Name"]
                        if source.get("Class") is not None:
                            module["itemClass"] = int(source["Class"])
                        break
                suit_loadouts[loadout_id] = updated
                append("updateCommanderSuitLoadout", updated, timestamp)
        elif name == "DeleteSuitLoadout" and source.get("LoadoutID") is not None:
            append("delCommanderSuitLoadout", {"loadoutGameID": int(source["LoadoutID"])}, timestamp)
        elif name == "MissionAccepted":
            append("addCommanderMission", _mission_data(source, context), timestamp)
        elif name in {"MissionAbandoned", "MissionFailed"} and source.get("MissionID") is not None:
            event_name = "setCommanderMissionAbandoned" if name == "MissionAbandoned" else "setCommanderMissionFailed"
            append(event_name, {"missionGameID": source["MissionID"]}, timestamp)
        elif name == "MissionCompleted":
            append("setCommanderMissionCompleted", _mission_completion_data(source), timestamp)
            for permit in source.get("PermitsAwarded", []) or []:
                system = _permit_name(permit)
                if system:
                    permit_data = {"starsystemName": str(system)}
                    append(
                        "addCommanderPermit", permit_data, timestamp,
                        fingerprint_data={
                            "eventName": "addCommanderPermit",
                            "eventData": permit_data,
                        },
                    )
        elif name == "PVPKill" and source.get("Victim") and context.get("StarSystem"):
            append("addCommanderCombatKill", {
                "starsystemName": context.get("StarSystem", ""),
                "opponentName": source["Victim"],
            }, timestamp)
        elif name == "Died":
            data = {"starsystemName": context.get("StarSystem", "")}
            if source.get("KillerName"):
                data["opponentName"] = source["KillerName"]
            killers = source.get("Killers")
            if isinstance(killers, list):
                names = [row.get("Name") for row in killers if isinstance(row, dict) and row.get("Name")]
                if names:
                    data["wingOpponentNames"] = names
            if data.get("starsystemName") and (data.get("opponentName") or data.get("wingOpponentNames")):
                append("addCommanderCombatDeath", data, timestamp)
        elif name in {"Interdicted", "EscapeInterdiction", "Interdiction"}:
            opponent = source.get("Interdictor") or source.get("Interdicted") or source.get("Power") or source.get("Faction")
            if opponent and context.get("StarSystem"):
                data = {"starsystemName": context["StarSystem"], "opponentName": opponent}
                if source.get("IsPlayer") is not None:
                    data["isPlayer"] = bool(source["IsPlayer"])
                if name == "Interdicted" and source.get("Submitted") is not None:
                    data["isSubmit"] = bool(source["Submitted"])
                if name == "Interdiction" and source.get("Success") is not None:
                    data["isSuccess"] = bool(source["Success"])
                event_name = {
                    "Interdicted": "addCommanderCombatInterdicted",
                    "EscapeInterdiction": "addCommanderCombatInterdictionEscape",
                    "Interdiction": "addCommanderCombatInterdiction",
                }[name]
                append(event_name, data, timestamp)
        elif name == "CarrierJump":
            data = _travel_data(source, context, True, False)
            if data.get("starsystemName") and data.get("stationName"):
                append("addCommanderTravelCarrierJump", data, timestamp)
        elif name == "Touchdown":
            data = _travel_data(source, context, False, False)
            if data.get("starsystemName") and data.get("starsystemBodyName"):
                append("addCommanderTravelLand", data, timestamp)
        elif name == "CommunityGoal":
            for goal in source.get("CurrentGoals", []) or []:
                if not isinstance(goal, dict) or goal.get("CGID") is None:
                    continue
                append("setCommanderCommunityGoalProgress", {
                    "communitygoalGameID": int(goal["CGID"]),
                    "contribution": int(goal.get("PlayerContribution", 0) or 0),
                    "percentileBand": int(goal.get("PlayerPercentileBand", 0) or 0),
                    "percentileBandReward": int(goal.get("Bonus", 0) or 0),
                    "isTopRank": bool(goal.get("PlayerInTopRank", False)),
                }, timestamp)
        if max_events is not None and len(prepared) >= max_events:
            del prepared[max_events:]
            del fingerprints[max_events:]
            break
    if pending_loadgame_credits is not None:
        data, timestamp = pending_loadgame_credits
        append("setCommanderCredits", data, timestamp)
    if max_events is not None:
        del prepared[max_events:]
        del fingerprints[max_events:]
    return identity, prepared, fingerprints


def _status_ok(value):
    try:
        return 200 <= int(value) < 300
    except (TypeError, ValueError):
        return False


def send_events(config, events, post=None, timeout=25):
    if not bool(config.get("consent")):
        raise InaraError("Privacy consent is required before contacting INARA.")
    if not str(config.get("api_key") or "").strip():
        raise InaraError("An INARA API key is required.")
    if not str(config.get("commander_name") or "").strip():
        raise InaraError("A commander name is required.")
    payload = build_payload(config, events)
    started = time.monotonic()
    post = post or _default_post
    try:
        response = post(
            INARA_API_URL,
            json=payload,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": f"ED-Engineering-Companion/{APP_VERSION}",
            },
        )
    except HTTPError as exc:
        status = int(exc.code)
        retryable = status in {408, 425, 429} or status >= 500
        schema_error = status in {400, 404, 409, 422}
        raise InaraError(
            f"INARA returned HTTP {status}"
            + (" · request/schema rejected; update EDEC before retrying."
               if schema_error else "."),
            retryable=retryable, status_code=status, schema_error=schema_error,
        ) from None
    except (OSError, URLError) as exc:
        raise InaraError(f"INARA connection failed: {exc}") from None
    status = int(getattr(response, "status_code", 0) or 0)
    if status >= 400:
        retryable = status in {408, 425, 429} or status >= 500
        schema_error = status in {400, 404, 409, 422}
        detail = _response_error_detail(response)
        raise InaraError(
            f"INARA returned HTTP {status}"
            + (f" · {detail}" if detail else "")
            + (" · request/schema rejected; update EDEC before retrying."
               if schema_error else "."),
            retryable=retryable, status_code=status, schema_error=schema_error,
        )
    content_type = str(getattr(response, "headers", {}).get(
        "Content-Type", ""
    )).lower()
    if "html" in content_type:
        raise InaraError("INARA returned HTML instead of an API response.")
    try:
        body = response.json()
    except (TypeError, ValueError):
        raise InaraError("INARA returned invalid JSON.") from None
    if not isinstance(body, dict) or not isinstance(body.get("header"), dict):
        raise InaraError("INARA returned an incomplete response.")
    header = body["header"]
    header_status = header.get("eventStatus")
    if not _status_ok(header_status):
        schema_error = str(header_status) in {"202", "204", "400", "409", "422"}
        raise InaraError(
            "INARA rejected the request"
            f" ({header_status}: {header.get('eventStatusText', 'unknown')}).",
            retryable=not schema_error, schema_error=schema_error,
        )
    results = body.get("events")
    if not isinstance(results, list) or len(results) != len(events):
        raise InaraError("INARA returned an incomplete event receipt.")
    safe_results = []
    for result in results:
        if not isinstance(result, dict) or not _status_ok(
            result.get("eventStatus")
        ):
            rejected_name = (
                str(result.get("eventName") or "event")
                if isinstance(result, dict) else "event"
            )
            raise InaraError(
                f"INARA rejected {rejected_name}"
                f" ({result.get('eventStatus', 'unknown') if isinstance(result, dict) else 'invalid'}: "
                f"{result.get('eventStatusText', 'unknown') if isinstance(result, dict) else 'invalid response'}).",
                retryable=False, schema_error=True,
            )
        safe_results.append({
            "name": str(result.get("eventName") or "event"),
            "status": int(result.get("eventStatus")),
            "text": str(result.get("eventStatusText") or "Accepted"),
        })
    receipt = {
        "timestamp": _timestamp(),
        "httpStatus": int(getattr(response, "status_code", 200) or 200),
        "headerStatus": int(header_status),
        "headerStatusText": str(
            header.get("eventStatusText") or "Accepted"
        ),
        "elapsedMs": int((time.monotonic() - started) * 1000),
        "events": safe_results,
    }
    return receipt, body


def extract_profile_ships(body):
    found = []

    def visit(value, hinted=False):
        if isinstance(value, dict):
            name = (
                value.get("shipName") or value.get("shipType")
                or value.get("shipTypeName")
            )
            ident = value.get("shipIdent") or value.get("shipID")
            if hinted and name:
                label = str(name).strip()
                if ident and str(ident).strip() not in label:
                    label = f"{label} [{str(ident).strip()}]"
                if label and label not in found:
                    found.append(label[:64])
            for key, child in value.items():
                visit(
                    child,
                    hinted or str(key).casefold() in {
                        "commanderships", "commanderfleet", "ships", "fleet"
                    },
                )
        elif isinstance(value, list):
            for child in value:
                visit(child, hinted)

    visit(body)
    return found


def extract_community_goals(body):
    for result in body.get("events", []) if isinstance(body, dict) else []:
        if not isinstance(result, dict):
            continue
        if result.get("eventName") != "getCommunityGoalsRecent":
            continue
        rows = result.get("eventData")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []
