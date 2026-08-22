def journal_material_name(item: object) -> str:
    """Return only Elite's stable internal material identifier.

    Material movement records use ``Material`` while inventory, collection
    and ingredient records use ``Name``. Localised fields are display text and
    must never participate in identity, cache or blueprint comparisons.
    """
    if not isinstance(item, dict):
        return ""
    return str(item.get("Material") or item.get("Name") or "")


def is_completed_engineer_craft(event):
    """Accept only an applied craft carrying Elite's completion evidence."""
    if not isinstance(event, dict) or event.get("event") != "EngineerCraft":
        return False
    module = event.get("Module") or event.get("Module_Localised")
    slot = event.get("Slot")
    ingredients = event.get("Ingredients")
    try:
        level = int(event.get("Level", 0) or 0)
    except (TypeError, ValueError):
        return False
    if not (
        (module or slot)
        and event.get("BlueprintID") not in (None, "")
        and level > 0
        and isinstance(ingredients, list)
        and ingredients
    ):
        return False
    try:
        return all(
            isinstance(item, dict)
            and journal_material_name(item)
            and int(item.get("Count", 0) or 0) > 0
            for item in ingredients
        )
    except (TypeError, ValueError):
        return False


def material_event_changes(event: object) -> list[tuple[str, str, int]]:
    """Return inventory changes from one Elite Journal material event.

    Each result is ``(material_name, explicit_category, signed_quantity)``.
    Keeping this translation pure makes complete collect/trade/craft sequences
    testable without constructing the Tk application.
    """
    if not isinstance(event, dict):
        return []

    event_name = event.get("event")
    changes: list[tuple[str, str, int]] = []

    def add(item, quantity_key, sign):
        if not isinstance(item, dict):
            return
        name = journal_material_name(item)
        try:
            quantity = max(0, int(item.get(quantity_key, 0) or 0))
        except (TypeError, ValueError):
            quantity = 0
        if name and quantity:
            changes.append((
                name,
                str(item.get("Category") or ""),
                sign * quantity,
            ))

    if event_name == "MaterialCollected":
        # Count is normally present, but Elite historically implied one.
        item = dict(event)
        item["Count"] = event.get("Count", 1)
        add(item, "Count", 1)
    elif event_name == "MaterialDiscarded":
        item = dict(event)
        item["Count"] = event.get("Count", 1)
        add(item, "Count", -1)
    elif event_name == "MaterialTrade":
        add(event.get("Paid"), "Quantity", -1)
        add(event.get("Received"), "Quantity", 1)
    elif event_name == "Synthesis":
        for item in event.get("Materials", []) or []:
            add(item, "Count", -1)
    elif event_name == "EngineerCraft":
        if not is_completed_engineer_craft(event):
            return []
        for item in event.get("Ingredients", []) or []:
            add(item, "Count", -1)
    elif event_name == "EngineerContribution":
        if str(event.get("Type") or "").casefold() in {"materials", "data"}:
            add(event, "Quantity", -1)
    elif event_name == "TechnologyBroker":
        # Commodities are tracked by Elite's authoritative Cargo.json snapshot.
        # This list contains only Raw, Manufactured and Encoded expenditure.
        for item in event.get("Materials", []) or []:
            add(item, "Count", -1)
    elif event_name == "MissionCompleted":
        for item in event.get("MaterialsReward", []) or []:
            add(item, "Count", 1)

    return changes
