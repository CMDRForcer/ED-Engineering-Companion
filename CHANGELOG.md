# Changelog

## Unreleased

### Added

- Define the evidence and source contract for a future Mining Finder without
  connecting it to UI, persistence or network operations.
- Add pure, offline projections for locally confirmed ring/hotspot evidence,
  unbound prospector samples and documented Spansh dump catalog candidates.

### Fixed

- Group HGE BGS predictions into one travel card per system while preserving
  each faction, state and predicted material variant inside that destination.
- Label BGS observation age as an update and its HGE outcome as possible,
  reserving reported/live wording for directly observed signals, and describe
  the rolling local observation cache by its 24-hour retention.
- Split State Finds cache diagnostics into BGS and signal observations, expose
  oldest/newest timestamps, and report applied, merged and expired counts after
  a manual refresh.
- Size State Finds cards from their wrapped content so long faction, state and
  material variants remain inside the card at every supported window width.
- Rebuild legacy EDDN `journal/1` queue records through the current public
  allowlist, preserving completed receipts without resending them and keeping
  only irrecoverable records in quarantine.
- Preserve schema-valid public astronomy, BGS and Powerplay fields observed in
  `Scan`, `FSDJump`, `Location` and `CarrierJump`, while continuing to omit
  transient Commander-mode flags and all private or localized data.
- Align EDDN queue diagnostics with the active profile storage, expose
  payload-free quarantine groups, delivery rate, cooldown and ETA, verify the
  journal allowlist against a pinned public-schema contract, and drain valid
  backlog sequentially at the established replay cadence.
- Clear an obsolete EDDN not-shareable notice after a later event is accepted
  by the gateway, without making the rejected local event uploadable.
- Recognize both supported Outfitting schema versions when matching station
  snapshots to delivery receipts, avoiding a false `FRESH` status after send.

## 21.200 — 2026-09-03

### Added

- Added a presentation-only Engineering overlay window that shares the existing
  Operations state, remembers monitor-safe geometry and display preferences,
  and can be shown or locked from the existing tray menu.
- Added a generic SRV lifecycle projection from `RestockVehicle`, `LaunchSRV`
  and `DockSRV`, including the Journal-confirmed Rhino identifier without
  inferring vehicle inventory from an installed hangar module.
- Added a read-only summary of the latest SRV mining trip for the observed
  Haematite, Samarium and Thortveitite cargo, while keeping refined cargo
  separate from Engineering materials and outside the EDDN contract.

### Fixed

- Normalize wrapped `MiningRefined` commodity identifiers to the same cargo
  keys used by `MarketSell`, so sold mined cargo is removed from subsequent
  INARA inventory snapshots.
- Bind INARA cargo-snapshot deduplication to its Journal timestamp, allowing a
  later legitimately empty inventory to replace an older non-empty state.
- Use Frontier's itemized `Cargo.json` as the authoritative final INARA cargo
  snapshot when compact post-sale Journal events contain only a total count.
- Read only the final complete Journal record for health diagnostics instead
  of repeatedly loading the entire active Journal file from QML properties.
- Persist EDDN jobs once per Journal scan batch instead of deep-copying and
  atomically rewriting a full offline queue after every individual event.
- Pause the EDDN Journal cursor before an event when the durable queue reaches
  capacity, resume without replay after space becomes available, and quarantine
  invalid legacy jobs separately while valid deliveries continue.
- Scope learned Journal BlueprintIDs by blueprint, grade and module family,
  allowing Frontier's distinct IDs for the same weapon modification while
  retaining legacy catalog records and genuine same-family contradictions.

## 21.199 — 2026-09-01

### Fixed

- EDDN Journal cursors now retain incomplete trailing lines for the next poll
  and read newly rotated Journal files from their first event without replaying
  files that existed when upload was enabled.
- Profile identity, storage key, Commander directory and Journal root now come
  from one resolved context, keeping controller persistence and EDDN queue jobs
  bound to the same profile across restarts and Commander switches.
- INARA scans, manual syncs and asynchronous completions now remain bound to
  their originating profile context; stale completions after a Commander switch
  are discarded before they can update another profile's cache or receipts.
- Spansh material-trader updates now use one profile-local catalog path for
  both persistence and state projection; a legacy global catalog is assigned
  to at most one profile instead of leaking into multiple Commanders.
- INARA payloads now derive `isBeingDeveloped` from the centralized build
  channel, reporting false for release builds and true for explicit development
  or preview artifacts.
- EDDN `journal/1` messages now use event-specific allowlists, dropping unknown,
  localized and private fields before queueing and rejecting unsafe legacy queue
  messages again before retry or send.
- Corrupt persistent JSON files are now preserved with recovery copies and
  protected from automatic empty-state writes, while their filenames and error
  status are surfaced through state consistency diagnostics.

## 21.198 — 2026-08-30

### Fixed

- Treat an unlocked Engineer as an executable progressive stop when the currently available rank can craft the next unfinished grade and raise reputation toward the final target.
- Reserve `RUN BLOCKED` for genuinely inaccessible workshops or ranks below the next required grade.

## 21.197 — 2026-08-30

### Fixed

- Prevent a craft for another blueprint on the same physical module from hijacking a pinned engineering plan.
- Recover plans already affected by that mismatch when the correct pending Journal craft is replayed, restoring accurate remaining-material readiness.

## 21.191 — 2026-08-30

### Fixed

- Preserve a Journal-confirmed Powerplay pledge across subsequent `LoadGame` events when Elite does not repeat the `Powerplay` snapshot.
- Apply explicit `PowerplayJoin`, `PowerplayDefect`, and `PowerplayLeave` events as the authoritative membership changes.

## 21.190 — 2026-08-30

### Fixed

- Consolidate every compatible engineering job at the Engineer in the current system before routing to another stop.
- Keep all jobs assigned to the active route stop ahead of unrelated plan priorities, preventing unnecessary departure and return flights.

## 21.189 — 2026-08-29

### Changed

- Reworked Commander Operations so module, blueprint, target grade, and experimental effect are readable at a glance.
- Compacted the action card and marked the route-optimized engineer as the first recommendation.

## 21.188 — 2026-08-29

### Fixed

- Recognize the live `Commander` Journal preamble before `LoadGame`, so current Elite Dangerous journals upload their complete material inventory snapshot to INARA instead of accepting unrelated batches while leaving material timestamps stale.

## 21.187 — 2026-08-29

### Fixed

- Isolate local Windows packaging from third-party DLL directories injected into `PATH`, preventing an incompatible Poppler ICU runtime from breaking QtCore at application startup.
- Reject locally built release archives if the known foreign ICU DLLs are present.

## 21.186 — 2026-08-29

### Improved

- Windows builds now carry explicit EDEC product/version metadata, and bundled manual filenames follow the current application release instead of the historical 21.164 source-document name.

## 21.185 — 2026-08-29

### Improved

- Spansh trader and Technology Broker catalog requests now use a longer timeout, bounded transport retries, and short gaps between category requests to tolerate intermittent connection resets without discarding the offline catalog.

## 21.184 — 2026-08-29

### Fixed

- Clearing delivered EDDN queue entries now preserves the latest accepted gateway proof for the Delivery Proof summary.

### Safety

- The retained proof contains only delivery metadata such as time, schema, event type, station label, and gateway result; Journal payloads and Commander identifiers are not persisted in it.

## 21.183 — 2026-08-29

### Added

- Next Best Action now identifies the physical module's blueprint, target grade, and planned Experimental Effect.
- Material Trades can be filtered by Raw, Manufactured, or Encoded, while Auto follows the next category on the shortest Journal-position-aware trader route.

### Improved

- Multi-plan Engineering minimizes repeated Engineer visits globally; a single plan still selects the nearest unlocked Engineer with sufficient rank.
- Operations keeps the complete build in material acquisition until every open grade and Experimental is covered, then switches to the bundled Engineer route.
- Obsolete documentation screenshots, generated bytecode caches, and an unused repository-check import were removed after reference verification.

### Safety

- Prioritizing one plan changes only the later craft order and can no longer hide shortages belonging to another physical module slot.
- Existing Journal authority, slot binding, import/export, material allocation, INARA/EDDN limits, persistence, and public import paths remain unchanged.

## 21.182 — 2026-08-28

### Added

- Explorer file selection and drag-and-drop for Coriolis, EDSY/SLEF, and EDEC JSON build imports.
- Slot-bound desired-outfitting tracking that shows the exact module, class, and rating to install.
- Engineering target summaries directly below installed modules, including target grade, blueprint, and experimental effect.

### Improved

- Complete builds now retain non-engineered modules instead of silently ignoring pure module replacements.
- Journal module identifiers are canonicalized across live loadouts, imports, exports, and desired-module matching.
- Installed Bi-Weave shields and newly purchased modules immediately satisfy matching desired slots.
- New import and outfitting guidance is localized in English, German, Spanish, and French.

### Safety

- Desired outfitting remains a planning overlay; the Journal remains the sole authority for actually installed modules.
- Existing engineering plans are preserved, exact duplicates remain skipped, and external slot mappings stay hull-validated.

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
