"""Canonical Frontier module identities shared by live and export workflows."""

from __future__ import annotations

import re


MODULE_ID_ALIASES = {
    # Early Type-11 build exports used a descriptive Mk II token; the shipped
    # Journal identifies the same controller as miningv2.
    "int_multidronecontrol_mining_mkii_size5_class5":
        "int_multidronecontrol_miningv2_size5_class5",
    # Early build data used the display-oriented volley-repeater token;
    # Frontier's shipped Journal identifies it as miningtoolv2.
    "hpt_miningvolleyrepeater_fixed_large":
        "hpt_miningtoolv2_fixed_large",
    "hpt_heatsinklauncher_tiny":
        "hpt_heatsinklauncher_turret_tiny",
}


def canonical_module_id(value: object) -> str:
    """Return one stable ID for wrapped, aliased and hull-specific symbols."""
    symbol = str(value or "").strip().strip("$;")
    if symbol.casefold().endswith("_name"):
        symbol = symbol[:-5]
    symbol = symbol.casefold()
    # Armour carries a hull prefix in Frontier data. Preserve its actual
    # bulkhead variant while removing only that unstable hull component.
    armour = re.fullmatch(
        r".+_armour_(grade[1-5](?:_default)?|reactive|mirrored)", symbol
    )
    if armour:
        variant = armour.group(1)
        if variant == "grade1_default":
            variant = "grade1"
        symbol = f"ship_armour_{variant}"
    return MODULE_ID_ALIASES.get(symbol, symbol)


def module_identity_key(value: object) -> str:
    """Return the punctuation-insensitive comparison key for a module ID."""
    return "".join(
        character for character in canonical_module_id(value)
        if character.isalnum()
    )


def same_module_identity(left: object, right: object) -> bool:
    """Compare module IDs only after applying every supported alias rule."""
    left_key = module_identity_key(left)
    return bool(left_key and left_key == module_identity_key(right))
