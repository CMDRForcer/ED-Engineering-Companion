# Changelog

## 1.0.1 — 2026-09-10

### Fixed

- Remote ships whose current loadout has not been observed are no longer
  mistaken for ships with confirmed empty or mismatched slots. Operations now
  asks for a Journal loadout confirmation before recommending installation.
- The Credits chart no longer draws an artificial Assets series, legend or
  right-hand scale when no authoritative asset snapshot is available.
- Standalone QML delegate failures remain visible in diagnostics while the
  known paired delegate-incubation teardown noise is suppressed narrowly.

### Changed

- Refreshed the application with the Orbital Dawn visual design and expanded
  the public documentation and screenshots.

## 1.0.0 — 2026-09-09

First public release of ED Engineering Companion under versioned releases.

### Ship engineering

- Slot-based engineering for every supported hull: Core Internal, Optional
  Internal, Hardpoint, Utility Mount and Limpet/Controller layouts are the
  ship's own.
- Fleet-wide planning — select any ship EDEC has seen in the Journal without
  switching to it in Elite Dangerous.
- Journal-confirmed blueprints, grades and experimental effects are shown on
  the exact physical module slot they belong to; pinned plans stay attached
  to the slot when other catalog blueprints are viewed.
- Install-before-engineering guard: when a planned module is missing from its
  bound slot, Operations shows `MODULE · INSTALLATION REQUIRED` and pauses the
  material and engineering flow until a Journal `Loadout` confirms the module.

### Wishlist, materials and navigation

- Actionable Wishlist: material readiness, craft progress, Engineer
  destinations and trader routes derived from the selected ship and its
  pinned plans.
- Live Raw / Manufactured / Encoded inventory with protected build stock,
  verified acquisition guidance and nearest-vs-Journal-confirmed trader
  routing.
- Engineer navigation: searchable capability index, Journal-backed unlock
  state, guided prerequisite chains, and Human/Guardian Technology Broker
  tracking.

### Commander tools

- CMDR overview with a persistent live Credits ticker and finance timeline
  built from a lossless local history archive; genuine `LIVE STATUS` balance
  changes are persisted, a session-start fallback is not.
- Logbook sessions and notes, State Finds, live High-Grade Emission
  assistance, and configurable navigation.
- Powerplay 2.0 view derived entirely from observed Journal values.
- Four interface languages: English, German, Spanish, French.

### Interchange and community

- Import EDEC, EDSY/SLEF and Coriolis builds through exact hull-slot
  validation; export outfitting with physical slot identities intact.
- Optional, opt-in INARA synchronization and privacy-filtered EDDN
  contributions with offline-aware queues and retry handling.
- A source checkout reports itself to INARA and EDDN as a development build.

### Engineering and reliability

- Atomic local persistence with corruption quarantine, bounded history,
  multi-process protection and durable retry queues.
- Background Journal projection kept off the Qt thread; all periodic timers
  stop on shutdown before the final save.
- Headless load of the real `Main.qml` in the test suite so QML runtime
  errors fail the build; full suite plus a source and packaged smoke test
  run on every release.
