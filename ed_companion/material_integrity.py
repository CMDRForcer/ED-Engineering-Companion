"""Repeatable integrity checks for ship-engineering material references."""

from __future__ import annotations

from typing import Any


def material_key(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())
