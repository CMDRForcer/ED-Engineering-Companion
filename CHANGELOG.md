# Changelog

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
