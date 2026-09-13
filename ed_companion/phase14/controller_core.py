"""Shared Qt Signal declarations for CockpitController's mixin split
(controller.py modularization, no behavior change).

Property(..., notify=someSignal) needs someSignal to be a real object at
the time the Property is defined - a bare name isn't visible across
sibling mixin class bodies (each executes as its own independent
namespace), so a signal used by more than one domain mixin must live
somewhere all of them can reference it explicitly as
CoreControllerMixin.someSignal, before their own class body runs.
connectionChanged and stateChanged are used this way by nearly every
domain (35 and 31 Properties respectively at the time of this refactor)
- domain-exclusive signals stay defined in their own domain mixin instead.
"""

from PySide6.QtCore import Signal


THEME_IDS = frozenset({
    "arctic_alloy", "navy", "neon_vector", "orbital_dawn",
    "crimson_dark", "crimson_light",
})
LEGACY_THEME_IDS = frozenset({
    "imperial_gold", "thargoid_amber", "midnight", "black",
    "teal_void", "federal_steel", "cupcake_nebula",
})


class CoreControllerMixin:
    connectionChanged = Signal()
    stateChanged = Signal()
    uiChanged = Signal()
    engineeringChanged = Signal()
    operationsChanged = Signal()
    wishlistChanged = Signal()
    hgeChanged = Signal()
    materialsChanged = Signal()
