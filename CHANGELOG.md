# Changelog

## 21.181 — 2026-08-27

### Added

- Complete English, German, Spanish, and French interface catalogs with immediate in-app language switching and persistent selection.
- Translation contracts that keep all four catalogs aligned, preserve dynamic placeholders, and verify every Powerplay leader biography.

### Improved

- Localized navigation, Engineering, Wishlist, Materials, Engineers, Operations, Logbook, settings, status text, empty states, and Powerplay leader profiles.
- In-game names for modules, materials, blueprints, Engineers, systems, and powers remain in English where this improves findability in Elite Dangerous.
- Material filters wrap responsively for longer translated labels without clipping controls or reducing text size.
- README content now reflects slot-based Engineering, build interchange, Engineer navigation, Powerplay 2.0, and all supported languages, with new Crimson Dark screenshots from synthetic demo data.

### Safety

- Existing physical-slot binding, EDEC/EDSY/Coriolis interchange, Journal observation boundaries, INARA/EDDN limits, material allocation, and persistence behavior remain covered by the release contract suite.

## 21.180 — 2026-08-26

### Added

- Fleet-aware Engineering workspace with a selectable ship, top-view schematics, ship statistics, and the exact Core, Optional, Hardpoint, and Utility slot layout for all 48 supported hulls.
- Installed engineering state on physical module slots, including blueprint grade, experimental effect, and a wrench marker.
- Direct experimental-effect cards with benefit and trade-off summaries.
- Persisted trader-routing preference for confirmed catalog entries or the nearest catalog candidate.

### Improved

- Engineering plans remain attached to the selected physical module instance and automatically use its current grade when the installed blueprint matches.
- Engineer selection prioritizes a known unlocked Engineer who can complete the requested target grade.
- EDEC and EDSY/SLEF imports preserve exact Journal slots; Coriolis component paths are mapped only when the selected hull schema makes the result deterministic.
- Journal `EngineerCraft` events update the active ship's exact physical slot and reconcile canonical experimental-effect identities.
- Engineering layout, cards, buttons, progress bars, scrollbars, responsive spacing, and enhanced cockpit visuals received a global visual polish.

### Safety

- Ambiguous external module slots are blocked instead of being silently attached to the wrong module.
- Existing INARA/EDDN safety limits, Journal privacy boundaries, material allocation, and persistence contracts remain covered by release tests.
