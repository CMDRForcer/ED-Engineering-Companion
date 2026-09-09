# ED Engineering Companion

**Plan Elite Dangerous engineering against the real slots of your real ships — without leaving the cockpit.**

[![Latest release](https://img.shields.io/github/v/release/CMDRForcer/ED-Engineering-Companion?sort=semver&label=release)](https://github.com/CMDRForcer/ED-Engineering-Companion/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/CMDRForcer/ED-Engineering-Companion/total?label=downloads)](https://github.com/CMDRForcer/ED-Engineering-Companion/releases)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-informational)
![Languages: EN · DE · ES · FR](https://img.shields.io/badge/languages-EN%20%C2%B7%20DE%20%C2%B7%20ES%20%C2%B7%20FR-informational)

ED Engineering Companion (EDEC) is a free, open-source Windows companion for [Elite Dangerous](https://www.elitedangerous.com/). It reads your local Journal and turns it into a ship-aware engineering workspace: every hull in your fleet, every physical module slot, every Journal-confirmed blueprint and experimental effect, and a running "what to do next" that never asks you to swap ships in-game to find out.

[**Download v1.0.0**](https://github.com/CMDRForcer/ED-Engineering-Companion/releases/latest) · [Changelog](CHANGELOG.md) · [Report a bug](https://github.com/CMDRForcer/ED-Engineering-Companion/issues) · [Support EDEC on Ko-fi](https://ko-fi.com/cmdrforcer)

![EDEC ship engineering in the Crimson Dark theme](docs/images/edec-engineering-crimson.png)

## Highlights

- **Your ships, not ship types** — plan on the exact physical slots of every hull EDEC has seen in your fleet, without switching to it in-game.
- **Install-before-engineering guard** — EDEC never pretends an empty slot is ready; if the planned module isn't fitted, it says so and pauses the plan.
- **The Journal is the source of truth** — confirmed blueprints, grades and experimental effects sit on the module they belong to. Nothing is invented to fill a gap.
- **One next action, always on screen** — collect these materials, fly to this Engineer, craft this grade.
- **Material intelligence** — live Raw / Manufactured / Encoded stock, protected build reserves, verified acquisition routes, nearest-vs-Journal trader choice.
- **Engineer & Tech Broker navigation** — capability search, Journal-backed unlock progress, guided prerequisite chains, Human & Guardian broker tracking.
- **Powerplay 2.0, honestly** — pledged leader, rank, merits, salary and system state, showing only values EDEC has actually observed.
- **Safe build interchange** — import and export EDEC, EDSY/SLEF and Coriolis builds with physical slot identities intact.
- **Commander tools** — live Credits ticker and finance timeline, Logbook, State Finds, live High-Grade Emission assistance.
- **Offline-first and private** — everything runs from local files; optional INARA and EDDN sync is opt-in, rate-limited and privacy-filtered.

## Why EDEC

Most build tools model a *type* of ship. EDEC models *your* ship — the specific hull with the specific ShipID, its exact Core Internal, Optional Internal, Hardpoint, Utility Mount and Limpet/Controller layout, and the modules currently bolted into it according to your latest `Loadout` and `EngineerCraft` events.

That precision is the whole point:

- A plan stays attached to the **slot**, so two identical Multi-cannons never get confused with each other.
- Engineering that Elite has already confirmed shows up **on the module it belongs to** — grade, blueprint, experimental effect.
- If a planned module isn't actually installed, EDEC says **install it first** instead of pretending the slot is ready.
- You can plan for a ship parked on the other side of the bubble **without switching to it**.

Everything is derived from local files. Nothing is invented to fill a gap.

## What it does

### Slot-based ship engineering

Pick any ship EDEC has seen in your Journal and work on its actual physical slots. The module wrench, grade and experimental effect come straight from authoritative `Loadout` / `EngineerCraft` events; pinned plans survive you browsing other catalog blueprints.

### An actionable Wishlist

Material readiness, craft progress, Engineer destinations and trader routes — all computed from the selected ship and its pinned plans. The next useful action is always on screen: collect these materials, fly to this Engineer, craft this grade, or *install this module before anything else*.

### Material intelligence

Live Raw / Manufactured / Encoded inventory straight from the Journal, with per-grade caps, stock bars, and surplus flags. Build reserves are protected so a plan can't spend materials it still needs; the rest is offered for trading. Filter to what a build needs, what's missing, what's ready, or what's tradeable, and route material runs by nearest-known or Journal-confirmed trader.

![Live material inventory with build-aware filters](docs/images/edec-materials-crimson.png)

### Engineer navigation

A searchable index of Engineer capabilities with Journal-backed unlock state, guided prerequisite chains, and Human / Guardian Technology Broker tracking. Entries sort by state, distance and name; Journal evidence advances unlock progress without guessing history it can't see.

![Engineer navigation and capability index](docs/images/edec-engineers-crimson.png)

### Powerplay 2.0

Derived entirely from local Journal events. EDEC picks your pledged leader automatically and shows an offline profile and portrait alongside only the values it has actually observed — rank, merits, pledge duration, salary, cargo activity, system-control state. No fabricated rewards, no invented numbers.

![Journal-driven Powerplay 2.0 overview](docs/images/edec-powerplay-crimson.png)

### Safe build interchange

Import EDEC, EDSY/SLEF and Coriolis builds through exact hull-slot validation. Export the selected ship's outfitting with its physical slot identities intact.

### Commander: Credits and Fleet

The CMDR workspace reads your career from the Journal. The **Credits** view is a live ticker: rising balance segments are green, spending is red, and total assets track alongside. Pick a period — session to 30 days — and it reports net change, average credits per hour, and the delta on any point you hover. Old gaps aren't fabricated; the ticker gets denser as Elite reports real balance changes.

![Live Credits ticker with period selection and hover detail](docs/images/edec-credits-crimson.png)

The **Fleet** view shows every ship EDEC has seen — value, rebuy, and where each one is parked (here, stored, or remote) — with the current hull marked. This is the same fleet you plan engineering against, without switching in-game.

![Fleet overview with per-ship value, rebuy and location](docs/images/edec-fleet-crimson.png)

Alongside these: Logbook sessions and notes, State Finds, live High-Grade Emission assistance, configurable navigation, and persistent settings across four interface languages — English, German, Spanish and French. In-game names stay in English where that makes them easier to find in Elite.

### Optional community connections

Rate-limited INARA synchronization and privacy-filtered EDDN contributions, both with offline-aware queues and retry handling. Off by default; you opt in per service.

> All screenshots use the **Crimson Dark** theme and synthetic demo data. They contain no real Commander profile, Journal history, service credentials, or API keys.

## Install on Windows

**Requirements:** Windows 10 or 11, and Elite Dangerous Journal files for live Commander data.

### Portable (recommended)

1. Open the [latest release](https://github.com/CMDRForcer/ED-Engineering-Companion/releases/latest).
2. Under **Assets**, download `EDEC-<version>-Windows.zip`.
3. Extract the whole ZIP to a writable folder.
4. Run `EDEC.exe`, keeping the `_internal` folder beside it.

The Windows package bundles its runtime and is Explorer-compatible. A full project archive and `SHA256SUMS.txt` are published alongside it. Windows SmartScreen may warn because the executable is not code-signed.

### From source

Install Python 3, clone the repository, run `INSTALL_REQUIREMENTS.bat` once, then launch `START_APP.bat`.

Personal settings, Journal cursors, caches, plans and service credentials live outside the program directory and are never included in release archives.

## Data and privacy

EDEC works locally from Elite Dangerous Journal files. Every network integration is optional and opt-in:

- **INARA** — supported Commander events are batched, deduplicated, rate-limited, and written to local receipts before any upload.
- **EDDN** — supported public market, station, exploration and exobiology messages are validated and stripped of private or unsupported fields before transmission.
- **Spansh** — optional read-only catalog data assists navigation and material guidance, with bundled offline fallbacks.

A build run from a source checkout identifies itself to INARA and EDDN as a development build, so ad-hoc runs are never counted as a released version.

## Reliability

- A headless load of the real `Main.qml` in CI whose QML runtime errors fail the build, plus state-based QML interaction tests.
- Contract tests for physical slot binding, EDEC/EDSY/Coriolis interchange, Powerplay observation boundaries, and external-service safety limits.
- Atomic local persistence with corruption quarantine, bounded history, multi-process protection, retry backoff, and durable queues.
- Journal processing that follows recently active files without replaying known lines.
- Translation contracts that keep the EN/DE/ES/FR catalogs and their placeholders in sync.

The full test suite and a source/packaged smoke test run on every release.

## Development

```text
INSTALL_REQUIREMENTS.bat
START_APP.bat
```

See [`requirements.txt`](requirements.txt) for runtime dependencies. EDEC is under active development; bug reports, translations and feature suggestions are welcome through [GitHub Issues](https://github.com/CMDRForcer/ED-Engineering-Companion/issues).

## License and attribution

EDEC is licensed under the [GNU General Public License v3.0](LICENSE) and is free to use.

ED Engineering Companion is an independent third-party project and is not affiliated with Frontier Developments. Elite Dangerous is a trademark of Frontier Developments plc.
