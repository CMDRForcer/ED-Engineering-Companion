import math
from itertools import permutations


TRADEABLE_GROUPS = {
    "Raw": {
        "Category1", "Category2", "Category3", "Category4",
        "Category5", "Category6", "Category7",
    },
    "Manufactured": {
        "Alloys", "Capacitors", "Chemical", "Composite", "Conductive",
        "Crystals", "Heat", "MechanicalComponents", "Shielding", "Thermic",
    },
    "Encoded": {
        "DataArchives", "EmissionData", "EncodedFirmware",
        "EncryptionFiles", "ShieldData", "WakeScans",
    },
}


def is_material_tradeable(material_meta):
    """Return whether a material appears in a standard Material Trader table."""
    material_meta = material_meta or {}
    category = material_meta.get("Category")
    group = material_meta.get("TraderGroup")
    return group in TRADEABLE_GROUPS.get(category, set())


def trade_matches_trader(trade, trader, metadata):
    """Require one category across the trade, both materials and its station."""
    if not isinstance(trade, dict) or not isinstance(trader, dict):
        return False
    category = str(trade.get("category") or "").strip().title()
    trader_category = str(trader.get("traderType") or "").strip().title()
    source_category = str(
        (metadata.get(trade.get("source")) or {}).get("Category") or ""
    ).strip().title()
    target_category = str(
        (metadata.get(trade.get("target")) or {}).get("Category") or ""
    ).strip().title()
    return bool(
        category in {"Raw", "Manufactured", "Encoded"}
        and category == trader_category == source_category == target_category
        and trader.get("system") and trader.get("station")
    )


def _distance_ly(left, right):
    if not left or not right or len(left) != 3 or len(right) != 3:
        return None
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def build_trader_route(trades, current_position=None, locations=None):
    """Return the shortest visit order for trader categories used by a trade plan.

    With at most three material-trader categories an exhaustive permutation is
    both exact and effectively free. If the Commander position is unavailable,
    the route still lists every required concrete station in stable category
    order and marks the first leg as unknown.
    """
    locations = locations or {}
    categories = []
    for trade in trades or []:
        category = str(trade.get("category") or "").strip().title()
        if category in locations and category not in categories:
            categories.append(category)
    if not categories:
        return {"stops": [], "total_distance_ly": 0.0, "position_known": bool(current_position)}

    def route_distance(order):
        total = 0.0
        position = current_position
        for category in order:
            destination = locations[category].get("coordinates")
            leg = _distance_ly(position, destination)
            if leg is not None:
                total += leg
            position = destination
        return total

    if current_position and len(current_position) == 3:
        order = min(
            permutations(categories),
            key=lambda candidate: (route_distance(candidate), candidate),
        )
    else:
        order = tuple(categories)

    stops = []
    position = current_position
    cumulative = 0.0
    for index, category in enumerate(order, 1):
        stop = dict(locations[category])
        leg = _distance_ly(position, stop.get("coordinates"))
        if leg is not None:
            cumulative += leg
        stop.update({
            "sequence": index,
            "leg_distance_ly": leg,
            "cumulative_distance_ly": cumulative if leg is not None else None,
        })
        stops.append(stop)
        position = stop.get("coordinates")
    return {
        "stops": stops,
        "total_distance_ly": route_distance(order),
        "position_known": bool(current_position and len(current_position) == 3),
    }


def trade_batch(source_grade, target_grade, same_group):
    """Return the exact (input, output) batch used by Elite material traders."""
    source_grade = int(source_grade or 0)
    target_grade = int(target_grade or 0)
    if source_grade < 1 or target_grade < 1:
        return None
    delta = source_grade - target_grade
    if same_group:
        if delta > 0:
            return 1, 3 ** delta
        if delta < 0:
            return 6 ** (-delta), 1
        return 1, 1
    if delta > 0:
        return 2, 3 ** (delta - 1)
    return 6 ** (1 - delta), 1


def plan_material_trades(missing, required, inventory, metadata, max_steps=None):
    """Plan every safe trade without consuming stock reserved by the build.

    ``max_steps`` remains available for explicit callers, but the default plan
    is complete. A hidden display cap makes a completed trade reveal previously
    omitted work and therefore makes the live checklist appear not to shrink.
    """
    available = {
        key: max(0, int(value or 0) - int((required or {}).get(key, 0) or 0))
        for key, value in (inventory or {}).items()
    }
    deficits = {
        key: max(0, int(value or 0))
        for key, value in (missing or {}).items()
        if int(value or 0) > 0
    }
    recommendations = []

    targets = sorted(
        deficits,
        key=lambda key: (
            -int((metadata.get(key) or {}).get("Grade", 0) or 0),
            -deficits[key],
            key,
        ),
    )
    for target in targets:
        target_meta = metadata.get(target) or {}
        target_category = target_meta.get("Category")
        target_group = target_meta.get("TraderGroup")
        target_grade = int(target_meta.get("Grade", 0) or 0)
        if not is_material_tradeable(target_meta) or target_grade < 1:
            continue

        candidates = []
        for source, source_available in available.items():
            if source == target or source_available <= 0:
                continue
            source_meta = metadata.get(source) or {}
            if (
                source_meta.get("Category") != target_category
                or not is_material_tradeable(source_meta)
            ):
                continue
            source_group = source_meta.get("TraderGroup")
            if not source_group:
                continue
            source_grade = int(source_meta.get("Grade", 0) or 0)
            same_group = source_group == target_group
            batch = trade_batch(source_grade, target_grade, same_group)
            if not batch:
                continue
            batch_in, batch_out = batch
            possible_batches = source_available // batch_in
            if possible_batches <= 0:
                continue
            potential = possible_batches * batch_out
            candidates.append((
                0 if same_group else 1,
                0 if source_grade >= target_grade else 1,
                -(batch_out / batch_in),
                -min(deficits[target], potential),
                source,
                batch_in,
                batch_out,
            ))

        for (
            _cross_group, _trade_up, _efficiency, _coverage,
            source, batch_in, batch_out,
        ) in sorted(candidates):
            if (
                deficits[target] <= 0
                or (
                    max_steps is not None
                    and len(recommendations) >= int(max_steps)
                )
            ):
                break
            possible_batches = available[source] // batch_in
            wanted_batches = math.ceil(deficits[target] / batch_out)
            batches = min(possible_batches, wanted_batches)
            if batches <= 0:
                continue
            source_spent = batches * batch_in
            target_received = batches * batch_out
            useful_received = min(deficits[target], target_received)
            available[source] -= source_spent
            deficits[target] -= useful_received
            recommendations.append({
                "source": source,
                "target": target,
                "source_spent": source_spent,
                "target_received": target_received,
                "useful_received": useful_received,
                "remaining": deficits[target],
                "category": target_category,
                "same_group": (
                    (metadata.get(source) or {}).get("TraderGroup")
                    == target_group
                ),
            })
    return recommendations
