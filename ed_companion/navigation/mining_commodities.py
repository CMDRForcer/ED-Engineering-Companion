"""Versioned Mining Finder commodity knowledge with lossless observations."""

from __future__ import annotations

import re
from typing import Any, Iterable


LASER = "LASER"
CORE = "CORE"
SUBSURFACE = "SUBSURFACE"
RHINO_SURFACE = "RHINO SURFACE"


def _entry(
    identifier: str,
    name: str,
    ring_types: tuple[str, ...] = (),
    *,
    hotspot: bool = False,
    core: bool = False,
    asteroid_surface: bool = False,
    rhino: bool = False,
) -> dict[str, Any]:
    methods = []
    if asteroid_surface:
        # Frontier's asteroid availability is published as one combined
        # Laser/Abrasion/Sub-surface column. Keep both selectable workflows
        # without pretending the Journal distinguishes the extraction tool.
        methods.extend((LASER, SUBSURFACE))
    if core:
        methods.append(CORE)
    if rhino:
        methods.append(RHINO_SURFACE)
    return {
        "id": identifier,
        "name": name,
        "methods": tuple(methods),
        "ringTypes": ring_types,
        "hotspot": hotspot,
        "catalogued": True,
    }


# Ring availability follows the current published asteroid-mining matrix.
# Rhino additions come from Frontier's update notes. Existing Rhino surface
# commodities follow the live community hotspot matrix maintained on the
# Frontier forum; future Journal observations remain losslessly visible.
_ROWS = (
    _entry("alexandrite", "Alexandrite", ("Rocky", "Metal Rich", "Icy"), hotspot=True, core=True, rhino=True),
    _entry("bauxite", "Bauxite", ("Rocky",), asteroid_surface=True),
    _entry("benitoite", "Benitoite", ("Rocky",), hotspot=True, core=True),
    _entry("bertrandite", "Bertrandite", ("Metal Rich", "Metallic"), asteroid_surface=True),
    _entry("bromellite", "Bromellite", ("Icy",), hotspot=True, core=True, asteroid_surface=True),
    _entry("cobalt", "Cobalt", ("Rocky",), asteroid_surface=True),
    _entry("coltan", "Coltan", ("Rocky", "Metal Rich"), asteroid_surface=True),
    _entry("gallite", "Gallite", ("Rocky", "Metal Rich", "Metallic"), asteroid_surface=True),
    _entry("gold", "Gold", ("Metal Rich", "Metallic"), asteroid_surface=True, rhino=True),
    _entry("grandidierite", "Grandidierite", ("Icy",), hotspot=True, core=True, rhino=True),
    _entry("hydrogenperoxide", "Hydrogen Peroxide", ("Icy",), asteroid_surface=True),
    _entry("indite", "Indite", ("Rocky", "Metal Rich", "Metallic"), asteroid_surface=True),
    _entry("lepidolite", "Lepidolite", ("Rocky", "Metal Rich"), asteroid_surface=True),
    _entry("liquidoxygen", "Liquid Oxygen", ("Icy",), asteroid_surface=True),
    _entry("lithiumhydroxide", "Lithium Hydroxide", ("Icy",), asteroid_surface=True),
    _entry("lowtemperaturediamond", "Low Temperature Diamonds", ("Icy",), hotspot=True, core=True, asteroid_surface=True, rhino=True),
    _entry("methaneclathrate", "Methane Clathrate", ("Icy",), asteroid_surface=True),
    _entry("methanolmonohydratecrystals", "Methanol Monohydrate Crystals", ("Icy",), asteroid_surface=True, rhino=True),
    _entry("monazite", "Monazite", ("Rocky", "Metal Rich", "Metallic"), hotspot=True, core=True, rhino=True),
    _entry("musgravite", "Musgravite", ("Rocky",), hotspot=True, core=True),
    _entry("osmium", "Osmium", ("Metal Rich", "Metallic"), asteroid_surface=True, rhino=True),
    _entry("painite", "Painite", ("Metal Rich", "Metallic"), hotspot=True, core=True, asteroid_surface=True),
    _entry("palladium", "Palladium", ("Metallic",), asteroid_surface=True, rhino=True),
    _entry("platinum", "Platinum", ("Metal Rich", "Metallic"), hotspot=True, core=True, asteroid_surface=True, rhino=True),
    _entry("praseodymium", "Praseodymium", ("Metal Rich", "Metallic"), asteroid_surface=True),
    _entry("rhodplumsite", "Rhodplumsite", ("Rocky", "Metal Rich", "Metallic"), hotspot=True, core=True, rhino=True),
    _entry("rutile", "Rutile", ("Rocky",), asteroid_surface=True),
    _entry("samarium", "Samarium", ("Rocky", "Metal Rich", "Metallic"), asteroid_surface=True, rhino=True),
    _entry("serendibite", "Serendibite", ("Rocky", "Metal Rich", "Metallic"), hotspot=True, core=True, rhino=True),
    _entry("silver", "Silver", ("Metal Rich", "Metallic"), asteroid_surface=True, rhino=True),
    _entry("thorium", "Thorium", ("Metal Rich", "Metallic"), asteroid_surface=True, rhino=True),
    _entry("tritium", "Tritium", ("Icy",), hotspot=True, asteroid_surface=True, rhino=True),
    _entry("uraninite", "Uraninite", ("Rocky", "Metal Rich"), asteroid_surface=True, rhino=True),
    _entry("opal", "Void Opal", ("Icy",), hotspot=True, core=True),
    _entry("water", "Water", ("Icy",), asteroid_surface=True, rhino=True),
    _entry("haematite", "Haematite", ("Rocky",), asteroid_surface=True, rhino=True),
    _entry("bastnasite", "Bastnäsite", rhino=True),
    _entry("deuterium", "Deuterium", rhino=True),
    _entry("diamond", "Diamond", rhino=True),
    _entry("helium", "Helium", rhino=True),
    _entry("helium3", "Helium-3", rhino=True),
    _entry("iridium", "Iridium", rhino=True),
    _entry("magnesite", "Magnesite", rhino=True),
    _entry("olivine", "Olivine", rhino=True),
    _entry("periclasedunite", "Periclase Dunite", rhino=True),
    _entry("quartzpyroxenite", "Quartz Pyroxenite", rhino=True),
    _entry("ruby", "Ruby", rhino=True),
    _entry("sapphire", "Sapphire", rhino=True),
    _entry("thortveitite", "Thortveitite", rhino=True),
    _entry("copper", "Copper", rhino=True),
    _entry("jadeite", "Jadeite", rhino=True),
    _entry("lithium", "Lithium", rhino=True),
    _entry("tantalum", "Tantalum", rhino=True),
    _entry("titanium", "Titanium", rhino=True),
    _entry("uranium", "Uranium", rhino=True),
)

MINING_COMMODITIES = {row["id"]: row for row in _ROWS}
_ALIASES = {
    "voidopal": "opal", "voidopals": "opal",
    "lowtemperaturediamonds": "lowtemperaturediamond",
    "methanolmonohydrate": "methanolmonohydratecrystals",
    "bastnaesite": "bastnasite", "helium-3": "helium3",
}


def mining_commodity_id(value: Any) -> str:
    """Normalize Frontier symbols, display labels and future identifiers."""
    text = str(value or "").strip()
    folded = text.casefold()
    if folded.startswith("$") and folded.endswith("_name;"):
        folded = folded[1:-6]
    folded = re.sub(r"[^a-z0-9]+", "", folded)
    return _ALIASES.get(folded, folded)


def mining_commodity_name(value: Any) -> str:
    identifier = mining_commodity_id(value)
    known = MINING_COMMODITIES.get(identifier)
    if known:
        return str(known["name"])
    raw = str(value or "").strip()
    if raw.startswith("$") and raw.casefold().endswith("_name;"):
        raw = raw[1:-6]
    raw = raw.replace("_", " ")
    raw = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw)
    return raw.title() or "Unknown commodity"


def mining_commodity_catalog(
    observed: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return the catalog plus every future commodity observed in a Journal."""
    rows = {key: dict(value) for key, value in MINING_COMMODITIES.items()}
    for observation in observed or ():
        if not isinstance(observation, dict):
            continue
        identifier = mining_commodity_id(
            observation.get("id") or observation.get("commodity")
        )
        if not identifier:
            continue
        row = rows.setdefault(identifier, {
            "id": identifier,
            "name": mining_commodity_name(
                observation.get("name") or observation.get("id") or identifier
            ),
            "methods": (), "ringTypes": (), "hotspot": False,
            "catalogued": False,
        })
        methods = set(row.get("methods") or ())
        methods.update(observation.get("methods") or ())
        row["methods"] = tuple(sorted(methods))
        row["observed"] = True
    return sorted(rows.values(), key=lambda row: str(row["name"]).casefold())


def mining_commodities_for_method(
    method: str, observed: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    wanted = str(method or "").upper()
    result = []
    for row in mining_commodity_catalog(observed):
        methods = set(row.get("methods") or ())
        # Unknown future IDs remain visible until their method is known.
        if wanted in methods or (row.get("observed") and not methods):
            result.append(row)
    return result
