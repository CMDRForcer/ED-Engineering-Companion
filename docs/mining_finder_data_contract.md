# Mining Finder data contract

Status: implemented read-only contract for QML, navigation and the
profile-bound runtime catalog. It never initiates uploads.

## Evidence shown to users

- `LOCAL_CONFIRMED`: the Commander's own Journal directly observed the fact.
- `LIVE_REPORTED`: a schema-valid public EDDN event was received live. It is a
  report, not a guarantee that a hotspot or deposit still exists.
- `CATALOG_CANDIDATE`: searchable Spansh body/ring data with its source update
  time. It is suitable for finding destinations, not confirming current yield.

Location evidence does not expire. When its last confirmation exceeds EDEC's
display policy (24 hours for live reports and 30 days for local/catalog
observations), it remains searchable with its original evidence and receives
`RECHECK_RECOMMENDED`. A missing timestamp becomes
`CONFIRMATION_TIME_UNKNOWN`. Neither state claims that a ring disappeared.

The UI must never label a destination or yield as guaranteed.

## Source responsibilities

### Local Frontier Journal

`Scan` supplies body/ring structure and reported reserve level.
`SAASignalsFound` supplies SAA signal types and counts for a body or ring.
`ProspectedAsteroid` is a direct sample of one asteroid: materials/proportions,
content, remaining percentage and an optional motherlode material.
`MiningRefined`, `Cargo` and `MarketSell` describe actual collection, current
cargo and sale results. `LaunchSRV`/`DockSRV` bound an observed Rhino session.

A prospector sample proves only that asteroid. A hotspot or reserve label does
not prove a particular yield.

### EDDN

Only EDEC's already-supported `journal/1` `Scan` and `SAASignalsFound` events
are candidates. Existing allowlists, private-field stripping and schema
validation remain mandatory before queueing and sending. `ProspectedAsteroid`,
`MiningRefined`, cargo and sales remain local and are not added to EDDN.

### Spansh

The documented `/dump/{id64}` response is the catalog contract because its
`system.bodies[].rings[]` records contain ring type and optional signal data;
the smaller `/system/{id64}` response does not carry this ring detail. EDEC
projects only system identity/coordinates, body name/arrival distance/reserve,
ring name/type and the ring signal map/update time. Missing properties stay
unknown rather than receiving inferred defaults.

### INARA

INARA remains a Commander synchronization integration. Its API is not treated
as a bulk galaxy/mining search service and is outside the finder data path.

## Rhino boundary

EDEC has observed Rhino session boundaries plus refined cargo and engineering
material events. No Rhino-specific deposit taxonomy, range, yield, vehicle
capacity or new Frontier identifier is inferred here. New fields must first be
observed in a redacted Journal sample and covered by a fixture.

## Next implementation gate

The pure local/Spansh projection and synthetic fixtures are now present. The
next gate is selecting and testing explicit freshness rules plus a query/cache
boundary before any controller, QML or navigation integration.
