# Changelog

## 1.2.1 — 2026-09-12

### Fixed

- Importing a build whose `Ship` field is the raw Frontier/Coriolis hull
  symbol (e.g. `Explorer_NX`) instead of the display name shown in the
  fleet (`Caspian Explorer`) was rejected as "incompatible with target
  ship", even though it names the exact same hull. Ship-type matching now
  resolves against the full `ed_data/ships.json` catalog's symbol-to-name
  pairs for every hull EDEC knows, not only the small, hand-picked list of
  base-game exceptions it previously relied on.
- Applying a build import, or pinning an engineering plan a second time,
  could add a duplicate wishlist entry for the same physical module
  instead of recognizing it as already tracked. The de-duplication check
  compared the plan's exact remaining-roll snapshot, which shifts as soon
  as any progress is made in-game; re-applying the same target afterward
  no longer matched and was added again. Plans are now identified by their
  physical target (ship, slot, module, blueprint, target grade) instead,
  which stays stable as progress advances. Affects every ship and both the
  build-import Apply and the manual "pin to wishlist" action.

## 1.2.0 — 2026-09-11

### Added

- **Interface Activity** on the Diagnostics page: a merged, newest-first
  record of what was actually sent to or received from INARA, EDDN and the
  Frontier Companion API, and when — service, direction, a short summary
  (schema name / operation), and a timestamp. Only completed deliveries
  are listed; in-flight/retrying/failed EDDN jobs keep their existing live
  view on the Connections page. Every field is already public-safe (schema
  names, HTTP outcomes, operation labels) — never raw message content.

### Changed

- INARA auto-sync now batches at least every 3 minutes instead of 5
  (`INARA_MIN_REQUEST_INTERVAL_SECONDS` 300 → 180). The burst limit
  (2 requests/minute) and the 429 cooldown are unchanged, so a real rate
  limit from INARA is still respected automatically.
- The INARA card's startup detail text now reflects whether an API key and
  consent were already saved, instead of always showing the generic
  first-run message — the same fix applied to EDDN's card in 1.1.8.

## 1.1.8 — 2026-09-11

### Fixed

- The Connections page's EDDN card could show `ENABLED` next to "EDDN
  network access is disabled." on every startup where EDDN was already
  enabled from a previous session. The status badge was always computed
  live from the saved consent flag, but the detail text under it was
  hardcoded to the disabled message at controller start, regardless of
  what was actually loaded from disk.

## 1.1.7 — 2026-09-11

### Changed

- The system tray icon, tooltip and menu ("Open ED·OPS", "Exit ED·OPS",
  "Restart ED·OPS", the "still running" notice) used a leftover "ED·OPS"
  name from before the project was named. They now say EDEC, matching the
  app's window title, About dialog, and the identity it already sends to
  INARA and EDDN ("ED Engineering Companion").

## 1.1.6 — 2026-09-11

### Fixed

- The system tray's "Status" line got permanently stuck on whatever
  one-shot toast last fired (e.g. "WINDOW OPEN · Windows autostart
  enabled.") instead of showing anything about the Journal watcher it
  claims to report on, because it reused `controller.activity` - designed
  as a transient action confirmation, not a persistent status. It now
  shows the live Journal health (`LIVE`/`READY`/`ERROR`/`NO JOURNAL`).

## 1.1.5 — 2026-09-11

### Fixed

- The CMDR finance ticker could raise a `ZeroDivisionError` if ever asked
  to downsample its history to a single point (`limit=1`); it now returns
  the most recent point instead of dividing by zero. Not reachable through
  today's UI (the only caller uses the default limit of 180), fixed as a
  latent landmine found during a full manual code review.

### Changed

- Closing pass of the full manual code-quality review started in 1.1.2:
  read every module under `ed_companion/`, including the two largest
  files (`state.py`, `controller.py`) in targeted high-risk slices
  (journal caching/rewrite detection, engineer-craft reconciliation, the
  EDDN and INARA privacy-filtering pipelines, engineering-slot
  projection), plus whole-codebase mechanical scans (ruff's correctness
  ruleset, duplicate-line detection, dead-code-after-return detection,
  None/bool comparison anti-patterns). No further defects found beyond
  the fix above; several suspected issues were investigated and confirmed
  correct as written.

## 1.1.4 — 2026-09-11

### Fixed

- The unconfirmed-trader warning ("Unverified – confirm on site") was
  hardcoded German text handed straight to Main.qml's display binding, so
  it showed untranslated in every interface language, including English.
  It is now a translation key resolved through the same catalog as the
  rest of the interface, with entries in all four supported languages.

## 1.1.3 — 2026-09-11

### Fixed

- The engineering overlay no longer fails to start if its saved position
  was ever recorded as `null` (e.g. a hand-edited or partially written
  `overlay_settings.json`); it now falls back to the default corner like a
  missing value already did.
- Spansh trader lookups now pace requests consistently: `fetch_nearest_traders`
  was missing the pause every other multi-request lookup in the same module
  applies, and `fetch_trader_catalog_updates` was pausing twice per request
  instead of once.

### Changed

- First slice of a full manual code-quality pass: removed a stray
  `__import__("os")`, an unused loop-ordinal variable, and two duplicated
  `_pause_between_requests` calls. No behaviour change beyond the two fixes
  above.

## 1.1.2 — 2026-09-10

### Fixed

- The engineering view's **installed roll panel** and the automatic current
  grade selection now work. `_apply_installed_slot_engineering` computed the
  installed blueprint, grade, quality and experimental of the selected slot
  but a misplaced early `return` left the code that applies them unreachable
  — dead since 1.0.0. A regression test now covers it.

### Changed

- Internal tidy: removed dead local variables and two unused imports, folded
  a duplicated sort/merge path in the CAPI fleet merge, hoisted the ship-type
  name table to a module constant. No behaviour change from these.

## 1.1.1 — 2026-09-10

### Added

- When this machine has never seen a Journal `Loadout` for the active ship
  (fresh install, ship engineered on another PC, rotated Journal files), the
  install-before-engineering guard now reads the ship's modules from the
  Frontier CAPI profile instead of blocking every plan with
  `MODULE · INSTALLATION REQUIRED`. Blueprint conflict detection works from
  the CAPI loadout too.
- The priority is Journal `Loadout` → CAPI loadout → unknown. A Journal
  loadout always wins; without a CAPI connection the behaviour is unchanged.
  CAPI-sourced slots are marked so the UI can show the profile snapshot
  time. Within-grade roll quality is treated as unknown, exactly as it is
  for a Journal `Loadout` that omits it.

## 1.1.0 — 2026-09-10

### Added

- The Frontier CAPI profile snapshot now also imports the **stored fleet**:
  every ship Frontier knows about, with its current system and station,
  hull/module value and total value. Ships already seen in the Journal keep
  their Journal identity; ships only Frontier knows are added as remote
  rows and never overwrite or delete Journal fleet entries.
- Commander **ranks** (Combat, Trade, Exploration, CQC, Federation, Empire,
  Mercenary, Exobiology) are filled from the CAPI profile when the Journal
  has not established them yet. A Journal rank is never downgraded, and rank
  progress and reputation are not part of the CAPI profile.
- A **rebuy estimate** and hull/module value for the active ship, derived
  from the CAPI ship value when the Journal offers none.

### Changed

- CAPI-only ship type names now split camelCase (`PantherMkII` reads as
  `Panther Clipper Mk II`); a few current-generation hulls were added to the
  readable-name table.
- All of the above only augments local Journal and Status.json data; newer
  Journal observations stay authoritative and survive a state rebuild.

## 1.0.4 — 2026-09-10

### Added

- The Frontier CAPI tab now requires an explicit consent tick before the
  first login. The choice is stored per Commander profile; clearing it
  cancels any pending login and leaves existing local tokens untouched.
- `EDEC_FRONTIER_CLIENT_ID` and `EDEC_FRONTIER_REDIRECT_URI` let an operator
  who runs their own instance point EDEC at a self-registered OAuth client
  instead of the bundled one.

### Changed

- The authorization request now asks for `audience=all`, matching Frontier's
  documented default, so Steam, Epic, Xbox and PSN logins all resolve.

### Fixed

- A Frontier `error` / `error_description` returned to the OAuth callback is
  now shown on the Connections tab instead of a generic "authorization was
  not completed". An error whose `state` does not match echoes only the
  bounded error code, never unverified free text.

## 1.0.3 — 2026-09-10

### Fixed

- The Frontier CAPI tab can no longer strand itself in a permanent
  "CONTACTING FRONTIER…" state. An unexpected error inside the background
  worker now always reports back with a privacy-safe message instead of
  silently ending the thread, and a watchdog releases the tab if a request
  still never returns. Connect, Refresh and Disconnect stay usable.

## 1.0.2 — 2026-09-10

### Added

- Opt-in Frontier Companion API connection on the Connections page. A new
  FRONTIER CAPI tab authorises through Frontier's PKCE OAuth flow (no shared
  secret), then imports an authenticated Commander profile snapshot —
  currently credits and the active ship — on demand via Connect, Refresh and
  Disconnect.
- Frontier OAuth tokens are encrypted for the current Windows account with
  DPAPI and stored outside the diagnostics log; they never appear in Journal
  data, logs or Git.
- The hosted GitHub Pages callback returns the authorisation response to the
  running desktop instance through the `edec://` handler and the existing
  single-instance channel, so an in-progress login is never handed to a
  second process.

### Changed

- Frontier CAPI data only augments local Journal and Status.json values;
  newer Journal observations always remain authoritative. Imported profile
  fields survive a Journal-driven state rebuild without overwriting fleet
  entries or engineering plans.

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
