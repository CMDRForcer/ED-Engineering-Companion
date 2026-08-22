"""Repeatable integrity checks for ship-engineering material references."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ed_companion.journal import journal_material_name


MATERIAL_CATEGORIES = frozenset({"Raw", "Manufactured", "Encoded"})


def material_key(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def material_integrity_report(
    metadata: Mapping[str, Mapping[str, Any]],
    blueprints: Iterable[Mapping[str, Any]],
    journal_events: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    unknown_blueprints: list[str] = []
    checked_blueprints = 0
    for row in blueprints:
        if row.get("Grade") is None or not any(
            engineer and not str(engineer).startswith("@")
            for engineer in row.get("Engineers", []) or []
        ):
            continue
        checked_blueprints += 1
        missing = sorted({
            material_key(item.get("Name"))
            for item in row.get("Ingredients", []) or []
            if isinstance(item, Mapping)
            and material_key(item.get("Name")) not in metadata
        })
        if missing:
            unknown_blueprints.append(
                f"{row.get('Type')} / {row.get('Name')} / G{row.get('Grade')}: "
                + ", ".join(missing)
            )

    unknown_journal: set[str] = set()
    for event in journal_events:
        if event.get("event") != "Materials":
            continue
        for category in MATERIAL_CATEGORIES:
            for item in event.get(category, []) or []:
                key = material_key(journal_material_name(item))
                if key and key not in metadata:
                    unknown_journal.add(key)

    uncategorized = sorted(
        key for key, row in metadata.items()
        if row.get("Category") not in MATERIAL_CATEGORIES
    )
    return {
        "checked_blueprints": checked_blueprints,
        "unknown_journal_materials": sorted(unknown_journal),
        "unresolved_blueprints": unknown_blueprints,
        "uncategorized_materials": uncategorized,
    }
