def _source_action(material, amount, metadata):
    info = metadata.get(material) or {}
    exact_sources = [
        value for value in (info.get("ExactSources") or [])
        if isinstance(value, dict)
    ]
    origins = [
        str(value) for value in (
            info.get("Origins") or info.get("OriginDetails") or []
        ) if value
    ]
    joined = " · ".join(origins)
    folded = joined.casefold()
    category = info.get("Category", "Material")

    if exact_sources:
        source = exact_sources[0]
        system = str(source.get("system") or "")
        body = str(source.get("body") or "")
        coordinates = str(source.get("coordinates") or "")
        target = str(source.get("target") or "")
        detail = " · ".join(
            value for value in (system, body, coordinates) if value
        )
        if target:
            detail += (" → " if detail else "") + target
        method = str(source.get("method") or "")
        if method:
            detail += (" · " if detail else "") + method
        return {
            "kind": str(source.get("kind") or "source").casefold(),
            "priority": 80,
            "material": material,
            "amount": amount,
            "title": f"Collect {material_metadata_name(info, material)} at {system}",
            "detail": detail,
            "system": system,
        }
    guidance = info.get("Guidance") or {}
    if guidance.get("detail"):
        return {
            "kind": str(guidance.get("kind") or "source").casefold(),
            "priority": 75,
            "material": material,
            "amount": amount,
            "title": (
                f"Collect {material_metadata_name(info, material)} · "
                f"{guidance.get('label', 'documented source')}"
            ),
            "detail": str(guidance["detail"]),
            "system": str(guidance.get("system") or ""),
        }
    if "high wake" in folded or "wake scan" in folded:
        kind, title, priority = "scan", "Scan high-energy wakes", 70
    elif "surface prospecting" in folded or category == "Raw":
        kind, title, priority = "raw", "Gather on planetary surfaces", 65
    elif "ship scanning" in folded or "data point" in folded:
        kind, title, priority = "scan", "Scan ships and data points", 62
    elif "ship salvage" in folded or "combat ship" in folded:
        kind, title, priority = "salvage", "Salvage destroyed ships", 60
    elif "signal source" in folded:
        kind, title, priority = "signal", "Search signal sources", 58
    elif "mission reward" in folded:
        kind, title, priority = "mission", "Check mission rewards", 52
    else:
        kind, title, priority = "source", "Use documented material source", 45

    return {
        "kind": kind,
        "priority": priority,
        "material": material,
        "amount": amount,
        "title": title,
        "detail": joined or f"{category} material source",
        "system": "",
    }


def material_metadata_name(info, fallback):
    return str(info.get("Name") or fallback)


def build_acquisition_plan(missing, metadata, navigations=None, trades=None):
    """Combine live targets, safe trades and static sources into one action plan."""
    remaining = {
        key: max(0, int(value or 0))
        for key, value in (missing or {}).items()
        if int(value or 0) > 0
    }
    navigations = navigations or {}
    actions = []

    trader_stops = {}
    for trade in trades or []:
        target = trade.get("target")
        useful = min(remaining.get(target, 0), int(trade.get("useful_received", 0) or 0))
        if useful <= 0:
            continue
        remaining[target] -= useful
        category = str(trade.get("category") or "Material")
        exchange = {
            "material": target,
            "amount": useful,
            "source": trade.get("source"),
            "source_spent": int(trade.get("source_spent", 0) or 0),
            "target_received": int(trade.get("target_received", 0) or 0),
        }
        stop = trader_stops.setdefault(category, {
            "kind": "trade",
            "priority": 90,
            "material": target,
            "amount": 0,
            "title": f"Visit a {category} Material Trader",
            "detail": "Complete all planned exchanges in one visit",
            "category": category,
            "trades": [],
            "system": "",
        })
        stop["amount"] += useful
        stop["trades"].append(exchange)

    # A trader visit is a physical route stop. Multiple exchanges at the same
    # trader category must never become repeated back-and-forth flight legs.
    actions.extend(trader_stops.values())

    for material, amount in remaining.items():
        if amount <= 0:
            continue
        navigation = navigations.get(material) or {}
        if navigation.get("system"):
            actions.append({
                "kind": "hge",
                "priority": 100,
                "material": material,
                "amount": amount,
                "title": f"Fly to {navigation['system']}",
                "detail": navigation.get("detail", "Live EDDN HGE sighting"),
                "system": navigation["system"],
            })
        else:
            actions.append(_source_action(material, amount, metadata))

    actions.sort(key=lambda item: (
        -item["priority"], -item["amount"], item["material"]
    ))
    return {
        "actions": actions,
        "remaining": {key: value for key, value in remaining.items() if value > 0},
        "covered_by_trades": sum(
            item["amount"] for item in actions if item["kind"] == "trade"
        ),
    }
