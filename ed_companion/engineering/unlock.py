import json
import re


MEETING_RULES = {
    "Felicity Farseer": ("rank", "Explore", 1, "Exploration rank"),
    "Juri Ishmaak": ("stat", "Combat", "Combat_Bonds", 50, "combat bonds"),
    "Colonel Bris Dekker": ("reputation", "Federation", 35, "Federation reputation"),
    "The Sarge": ("rank", "Federation", 1, "Federal Navy rank"),
    "Elvira Martuuk": ("stat", "Exploration", "Greatest_Distance_From_Start", 300, "ly from start"),
    "Mel Brandon": ("journal", "Colonia Council invitation"),
    "Zacariah Nemo": ("journal", "Party of Yoru invitation"),
    "Marco Qwent": ("journal", "Sirius Corporation invitation and permit"),
    "Chloe Sedesi": ("stat", "Exploration", "Greatest_Distance_From_Start", 5000, "ly from start"),
    "Lori Jameson": ("rank", "Combat", 6, "Combat rank"),
    "Professor Palin": ("stat", "Exploration", "Greatest_Distance_From_Start", 5000, "ly from start"),
    "The Dweller": ("stat", "Smuggling", "Black_Markets_Traded_With", 5, "black markets"),
    "Marsha Hicks": ("rank", "Explore", 3, "Exploration rank"),
    "Lei Cheung": ("stat", "Trading", "Markets_Traded_With", 50, "markets traded"),
    "Ram Tah": ("rank", "Explore", 3, "Exploration rank"),
    "Tod McQuinn": ("stat", "Combat", "Bounties_Claimed", 15, "bounties claimed"),
    "Petra Olmanova": ("rank", "Combat", 4, "Combat rank"),
    "Selene Jean": ("stat", "Mining", "Quantity_Mined", 500, "tonnes mined"),
    "Bill Turner": ("journal", "Alioth Independents Allied status and permit"),
    "Didi Vatermann": ("rank", "Trade", 4, "Trade rank"),
    "Liz Ryder": ("faction", "Eurybia Blue Mafia", 4, "faction reputation"),
    "Etienne Dorn": ("rank", "Trade", 3, "Trade rank"),
    "Hera Tani": ("rank", "Empire", 1, "Imperial Navy rank"),
    "Broo Tarquin": ("rank", "Combat", 3, "Combat rank"),
    "Tiana Fortune": ("reputation", "Empire", 35, "Empire reputation"),
}


def load_unlock_catalog(runtime_data_dir, package_root):
    """Load a runtime override when present, otherwise the bundled catalog."""
    candidates = [
        runtime_data_dir / "engineer_unlocks.json",
        package_root / "ed_data" / "engineer_unlocks.json",
    ]
    for path in candidates:
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, TypeError, ValueError):
            continue
        engineers = (
            document.get("engineers", {})
            if isinstance(document, dict) else {}
        )
        if isinstance(engineers, dict) and engineers:
            return engineers
    return {}


def _token(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _engineer_name(value, catalog):
    wanted = _token(value).replace("theblaster", "")
    for name in catalog or {}:
        if _token(name).replace("theblaster", "") == wanted:
            return name
    return ""


def _event_location(event):
    return (
        str(event.get("StarSystem") or ""),
        str(event.get("StationName") or event.get("Station") or ""),
    )


def _request_detail(record, evidence):
    request = dict(record.get("request") or {})
    target = int(request.get("quantity", 0) or 0)
    contributed = min(target, int(evidence.get("contributionTotal", 0) or 0))
    remaining = max(0, target - contributed)
    name = str(request.get("name") or record.get("unlock") or "request")
    unit = str(request.get("unit") or ("t" if request.get("cargo") else "units"))
    detail = str(record.get("unlock") or "Complete the Engineer request")
    if target:
        detail = (
            f"{remaining:,} {unit} {name} remaining"
            if remaining else f"{name} contribution complete"
        )
        if contributed:
            detail += f" · {contributed:,} / {target:,} confirmed"
        if request.get("cargo"):
            owned = int(evidence.get("cargoOwned", 0) or 0)
            capacity = int(evidence.get("cargoCapacity", 0) or 0)
            detail += f" · cargo {owned:,} carried"
            if capacity:
                detail += f", {capacity:,} t capacity"
            if remaining > capacity and capacity:
                detail += f" · NEED {remaining - capacity:,} t MORE CAPACITY"
    return detail


def engineer_unlock_signals(events, catalog):
    """Extract defensible unlock evidence from regular Journal events."""
    signals = {name: {} for name in (catalog or {})}
    faction_targets = {
        str(record.get("meetingFaction")): (
            name, float(record.get("meetingReputation", 0) or 0)
        )
        for name, record in (catalog or {}).items()
        if record.get("meetingFaction")
    }
    permit_factions = {
        str((record.get("permit") or {}).get("rule", {}).get("name")): name
        for name, record in (catalog or {}).items()
        if (record.get("permit") or {}).get("rule", {}).get("type")
        == "faction"
    }
    active_invitation_missions = {}
    active_permit_missions = {}
    latest_rank = {}
    latest_reputation = {}
    latest_statistics = {}
    cargo_inventory = {}
    cargo_capacity = 0
    for event in events or []:
        event_name = str(event.get("event") or "")
        if event_name == "Rank":
            latest_rank = dict(event)
        elif event_name == "Reputation":
            latest_reputation = dict(event)
        elif event_name == "Statistics":
            latest_statistics = dict(event)
        elif event_name == "Loadout":
            try:
                cargo_capacity = int(event.get("CargoCapacity", 0) or 0)
            except (TypeError, ValueError):
                cargo_capacity = 0
        elif event_name == "Cargo" and "Inventory" in event:
            cargo_inventory = {}
            for item in event.get("Inventory", []) or []:
                key = _token(item.get("Name") or item.get("Name_Localised"))
                try:
                    cargo_inventory[key] = int(item.get("Count", 0) or 0)
                except (TypeError, ValueError):
                    continue
        elif event_name == "EngineerContribution":
            engineer = _engineer_name(event.get("Engineer"), catalog)
            if engineer:
                request = dict((catalog.get(engineer) or {}).get("request") or {})
                event_type = _token(event.get("Type"))
                request_type = _token(request.get("type"))
                event_item = _token(
                    event.get("Commodity") or event.get("Material")
                )
                request_item = _token(
                    request.get("journalName") or request.get("name")
                )
                type_matches = (
                    event_type == request_type
                    or {event_type, request_type} <= {"bond", "bounty"}
                )
                item_matches = not request_item or not event_item or (
                    event_item == request_item
                )
                if type_matches and item_matches:
                    try:
                        total = int(
                            event.get("TotalQuantity")
                            if event.get("TotalQuantity") is not None
                            else event.get("Quantity", 0)
                        )
                    except (TypeError, ValueError):
                        total = 0
                    target = int(request.get("quantity", 0) or 0)
                    signals[engineer].update({
                        "contributionTotal": max(
                            total,
                            int(signals[engineer].get(
                                "contributionTotal", 0
                            ) or 0),
                        ),
                        "contributionTarget": target,
                        "contributionEvidence": (
                            f"{request.get('name') or request.get('type')}: "
                            f"{total:,} / {target:,}"
                        ),
                    })
                    if target and total >= target:
                        signals[engineer]["requestComplete"] = True
        if event_name in {"Docked", "Location", "FSDJump", "CarrierJump"}:
            system, station = _event_location(event)
            for engineer, record in (catalog or {}).items():
                permit = dict(record.get("permit") or {})
                if (
                    permit
                    and _token(system) == _token(permit.get("system"))
                ):
                    signals[engineer].update({
                        "permitConfirmed": True,
                        "permitEvidence": (
                            f"Journal confirms access to {system}"
                        ),
                    })
                if (
                    _token(system) == _token(record.get("system"))
                    and _token(station) == _token(record.get("station"))
                ):
                    signals[engineer].update({
                        "visited": True,
                        "visitEvidence": f"Docked at {station} · {system}",
                    })
        for faction in event.get("Factions", []) or []:
            faction_name = str(faction.get("Name") or "")
            try:
                reputation = float(faction.get("MyReputation", 0) or 0)
            except (TypeError, ValueError):
                continue
            if faction_name in faction_targets:
                engineer, threshold = faction_targets[faction_name]
                signals[engineer]["factionReputation"] = reputation
                if reputation >= threshold:
                    signals[engineer]["meetingComplete"] = True
                    signals[engineer]["meetingEvidence"] = (
                        f"{faction_name} reputation {reputation:.1f}%"
                    )
            permit_engineer = permit_factions.get(faction_name)
            if permit_engineer:
                permit = dict(
                    (catalog.get(permit_engineer) or {}).get("permit") or {}
                )
                minimum = float(
                    (permit.get("rule") or {}).get("minimum", 90) or 90
                )
                signals[permit_engineer].update({
                    "permitCurrent": reputation,
                    "permitTarget": minimum,
                })
                if reputation >= minimum:
                    signals[permit_engineer].update({
                        "permitEligible": True,
                        "permitEvidence": (
                            f"{faction_name}: Allied ({reputation:.1f}%). "
                            "Permit mission still requires Journal confirmation."
                        ),
                    })
        if (
            event_name == "MissionAccepted"
            and str(event.get("Name") or "").startswith(
                "Mission_Courier_Engineer"
            )
        ):
            faction_name = str(event.get("Faction") or "")
            target = faction_targets.get(faction_name)
            if target:
                engineer = target[0]
                mission_id = str(event.get("MissionID") or "")
                active_invitation_missions[mission_id] = engineer
                signals[engineer].update({
                    "meetingComplete": True,
                    "invitationMissionActive": True,
                    "invitationSystem": str(
                        event.get("DestinationSystem") or ""
                    ),
                    "invitationStation": str(
                        event.get("DestinationStation") or ""
                    ),
                    "meetingEvidence": (
                        f"Engineer Invitation Contract accepted from {faction_name}"
                    ),
                })
        if event_name == "MissionAccepted":
            mission_text = " ".join(str(event.get(field) or "") for field in (
                "Name", "LocalisedName", "Name_Localised"
            ))
            faction_name = str(event.get("Faction") or "")
            destination = str(event.get("DestinationSystem") or "")
            permit_engineer = permit_factions.get(faction_name, "")
            if not permit_engineer and "permit" in mission_text.casefold():
                permit_engineer = next(
                    (
                        engineer for engineer, record in (catalog or {}).items()
                        if _token(destination) == _token(
                            (record.get("permit") or {}).get("system")
                        )
                    ),
                    "",
                )
            if permit_engineer and "permit" in mission_text.casefold():
                mission_id = str(event.get("MissionID") or "")
                active_permit_missions[mission_id] = permit_engineer
                signals[permit_engineer].update({
                    "permitMissionActive": True,
                    "permitEvidence": (
                        f"{(catalog[permit_engineer].get('permit') or {}).get('name')} "
                        "mission accepted"
                    ),
                })
        elif event_name in {"MissionCompleted", "MissionFailed", "MissionAbandoned"}:
            mission_id = str(event.get("MissionID") or "")
            engineer = active_invitation_missions.pop(mission_id, "")
            if engineer:
                signals[engineer]["invitationMissionActive"] = False
                if event_name == "MissionCompleted":
                    signals[engineer]["invitationMissionComplete"] = True
            permit_engineer = active_permit_missions.pop(mission_id, "")
            if permit_engineer:
                signals[permit_engineer]["permitMissionActive"] = False
                if event_name == "MissionCompleted":
                    signals[permit_engineer].update({
                        "permitConfirmed": True,
                        "permitEvidence": "Permit mission completed",
                    })
    for engineer, rule in MEETING_RULES.items():
        if engineer not in signals:
            continue
        kind = rule[0]
        current = None
        target = None
        unit = ""
        if kind == "rank":
            _kind, field, target, unit = rule
            current = latest_rank.get(field)
        elif kind == "reputation":
            _kind, field, target, unit = rule
            current = latest_reputation.get(field)
        elif kind == "stat":
            _kind, section, field, target, unit = rule
            current = (latest_statistics.get(section) or {}).get(field)
        elif kind == "faction":
            # Already handled from Factions[].MyReputation above.
            continue
        else:
            signals[engineer].setdefault(
                "meetingEvidence",
                f"Waiting for Elite Engineer invitation confirmation · {rule[1]}",
            )
            continue
        try:
            current = float(current)
            target = float(target)
        except (TypeError, ValueError):
            continue
        signals[engineer].update({
            "meetingCurrent": current,
            "meetingTarget": target,
            "meetingEvidence": f"{unit}: {current:g} / {target:g}",
        })
        if current >= target:
            signals[engineer]["meetingComplete"] = True
    for engineer, record in (catalog or {}).items():
        permit = dict(record.get("permit") or {})
        if not permit or signals[engineer].get("permitConfirmed"):
            continue
        rule = dict(permit.get("rule") or {})
        kind = str(rule.get("type") or "")
        minimum = float(rule.get("minimum", 0) or 0)
        current = None
        if kind == "rank":
            current = latest_rank.get(str(rule.get("field") or ""))
        elif kind == "anyRank":
            values = [
                latest_rank.get(str(field))
                for field in (rule.get("fields") or [])
                if latest_rank.get(str(field)) is not None
            ]
            current = max(values) if values else None
        try:
            current = float(current)
        except (TypeError, ValueError):
            continue
        signals[engineer].update({
            "permitCurrent": current,
            "permitTarget": minimum,
            "permitEligible": current >= minimum,
            "permitEvidence": (
                f"{permit.get('name')}: rank {current:g} / {minimum:g}"
            ),
        })
        if current >= minimum:
            # Navy- and Elite-rank permits are granted automatically.
            signals[engineer]["permitConfirmed"] = True
    for engineer, record in (catalog or {}).items():
        request = dict(record.get("request") or {})
        item_key = _token(request.get("journalName") or request.get("name"))
        signals[engineer]["cargoOwned"] = int(
            cargo_inventory.get(item_key, 0) or 0
        )
        signals[engineer]["cargoCapacity"] = cargo_capacity
        target = int(request.get("quantity", 0) or 0)
        contributed = int(
            signals[engineer].get("contributionTotal", 0) or 0
        )
        signals[engineer]["requestRemaining"] = max(0, target - contributed)
        signals[engineer]["cargoRequired"] = (
            max(0, target - contributed) if request.get("cargo") else 0
        )
    return signals


def build_unlock_guide(name, status_group, progress, catalog, signals=None):
    """Build a conservative Journal-driven unlock checklist.

    Elite's EngineerProgress event exposes Known/Invited/Unlocked and rank,
    but not reliable counters for every meeting requirement. We therefore
    infer only states guaranteed by the Journal and never invent progress.
    """
    record = dict((catalog or {}).get(name) or {})
    evidence = dict((signals or {}).get(name) or {})
    if not record:
        return {
            "available": False,
            "title": "Unlock data not available",
            "nextAction": "No reviewed unlock chain is stored for this Engineer.",
            "steps": [],
            "completed": 0,
            "total": 0,
            "progress": 0.0,
            "source": "Unlock catalog unavailable",
            "navigationSystem": "",
            "navigationStation": "",
        }

    group = str(status_group or "unknown").casefold()
    unlocked = group == "unlocked"
    invited = group == "invited"
    known = group in {"known", "invited", "unlocked"}
    prerequisite = str(record.get("prerequisite") or "")
    prerequisite_progress = (progress or {}).get(prerequisite, {})
    prerequisite_rank = int(prerequisite_progress.get("rank", 0) or 0)
    prerequisite_unlocked = (
        str(prerequisite_progress.get("progress") or "").casefold() == "unlocked"
        or prerequisite_rank > 0
    )
    odyssey = str(record.get("progression") or "").casefold() == "odyssey"
    referral_ready = (
        not prerequisite
        or (known if odyssey else prerequisite_rank >= 3)
    )

    steps = []
    if prerequisite:
        steps.append({
            "label": f"Unlock {prerequisite}",
            "detail": (
                (
                    f"Unlock {prerequisite} and complete that Engineer's "
                    "referral request"
                ) if odyssey else
                f"Reach at least Grade 3 reputation with {prerequisite}"
            ),
            "state": "complete" if known or unlocked or referral_ready else (
                "active" if prerequisite_unlocked else "blocked"
            ),
            "automatic": True,
        })
    else:
        steps.append({
            "label": "Engineer discovered",
            "detail": str(record.get("discovery") or "Public knowledge"),
            "state": "complete" if known or unlocked else "active",
            "automatic": True,
        })

    meeting_complete = bool(
        invited or unlocked or evidence.get("meetingComplete")
    )
    steps.append({
        "label": "Meet invitation requirement",
        "detail": (
            str(record.get("meeting") or "Check invitation requirement")
            + (
                f" · {evidence['meetingEvidence']}"
                if evidence.get("meetingEvidence") else ""
            )
        ),
        "state": "complete" if meeting_complete else (
            "active" if (known or referral_ready) else "blocked"
        ),
        "automatic": meeting_complete,
    })
    if evidence.get("invitationMissionActive") and not invited and not unlocked:
        destination_system = str(evidence.get("invitationSystem") or "")
        destination_station = str(evidence.get("invitationStation") or "")
        destination = " · ".join(
            value for value in (destination_station, destination_system) if value
        )
        steps.append({
            "label": "Complete Engineer Invitation Contract",
            "detail": (
                (
                    f"Deliver the invitation data to {destination}. "
                    if destination else
                    "Finish the accepted invitation mission. "
                )
                + "The Journal will then promote the Engineer to INVITED."
            ),
            "state": "active",
            "automatic": True,
        })
    steps.append({
        "label": "Invitation received",
        "detail": (
            "Elite Journal status must report INVITED before travelling "
            "to the Engineer."
        ),
        "state": "complete" if invited or unlocked else (
            "active" if meeting_complete else "blocked"
        ),
        "automatic": invited or unlocked,
    })
    permit = dict(record.get("permit") or {})
    permit_confirmed = bool(
        unlocked or evidence.get("permitConfirmed")
    )
    if permit:
        permit_detail = str(
            permit.get("method") or "Obtain the required system permit."
        )
        if evidence.get("permitEvidence"):
            permit_detail += f" · {evidence['permitEvidence']}"
        steps.append({
            "label": f"Obtain {permit.get('name') or 'system permit'}",
            "detail": permit_detail,
            "state": "complete" if permit_confirmed else (
                "active" if (known or referral_ready) else "blocked"
            ),
            "automatic": permit_confirmed,
        })
    visited = bool(unlocked or evidence.get("visited"))
    system = str(record.get("system") or "")
    station = str(record.get("station") or record.get("base") or name)
    destination = " · ".join(value for value in (station, system) if value)
    steps.append({
        "label": f"Visit {station}",
        "detail": (
            f"Fly to {destination}. "
            + str(evidence.get("visitEvidence") or
                  "Docked/Location will confirm the visit automatically.")
        ),
        "state": "complete" if visited else (
            "active" if invited else "blocked"
        ),
        "automatic": visited,
    })
    request = dict(record.get("request") or {})
    request_target = 0
    request_total = 0
    request_complete = True
    if request:
        request_target = int(request.get("quantity", 0) or 0)
        request_total = int(evidence.get("contributionTotal", 0) or 0)
        referral_request = record.get("requestPurpose") == "referral"
        request_complete = bool(
            evidence.get("requestComplete")
            or (request_target and request_total >= request_target)
            or (unlocked and not referral_request)
        )
        steps.append({
            "label": (
                "Complete referral request" if referral_request else
                "Complete Engineer request"
            ),
            "detail": _request_detail(record, evidence),
            "state": "complete" if request_complete else (
                "active" if visited else "blocked"
            ),
            "automatic": request_complete,
        })
    steps.append({
        "label": "Engineer unlocked",
        "detail": "EngineerProgress confirms UNLOCKED and the available rank.",
        "state": "complete" if unlocked else "blocked",
        "automatic": unlocked,
    })

    active = next(
        (step for step in steps if step["state"] == "active"),
        None,
    )
    blocked = next(
        (step for step in steps if step["state"] == "blocked"),
        None,
    )
    next_step = active or blocked
    completed = sum(step["state"] == "complete" for step in steps)
    return {
        "available": True,
        "title": "UNLOCKED" if unlocked else "UNLOCK IN PROGRESS",
        "nextAction": (
            str((next_step or {}).get("detail") or "Engineer is unlocked.")
            if unlocked and not request_complete else
            "Engineer is unlocked."
            if unlocked else str((next_step or {}).get("detail") or "")
        ),
        "steps": steps,
        "completed": completed,
        "total": len(steps),
        "progress": completed / len(steps) if steps else 0.0,
        "prerequisite": prerequisite,
        "source": "Complete local unlock catalog · Journal automatic",
        "navigationSystem": (
            str(evidence.get("invitationSystem") or "")
            if evidence.get("invitationMissionActive")
            else system
            if invited and not unlocked and (not permit or permit_confirmed)
            else ""
        ),
        "navigationStation": (
            str(evidence.get("invitationStation") or "")
            if evidence.get("invitationMissionActive")
            else station
            if invited and not unlocked and (not permit or permit_confirmed)
            else ""
        ),
        "permitRequired": bool(permit),
        "permitName": str(permit.get("name") or ""),
        "permitSystem": str(permit.get("system") or ""),
        "permitConfirmed": permit_confirmed,
        "permitEligible": bool(evidence.get("permitEligible")),
        "cargoRequired": int(evidence.get("cargoRequired", 0) or 0),
        "cargoOwned": int(evidence.get("cargoOwned", 0) or 0),
        "cargoCapacity": int(evidence.get("cargoCapacity", 0) or 0),
        "requestRemaining": int(evidence.get(
            "requestRemaining", request_target
        ) or 0),
        "requestType": str(request.get("type") or ""),
        "requestName": str(request.get("name") or ""),
    }
