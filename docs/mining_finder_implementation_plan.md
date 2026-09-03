# Mining Finder implementation plan

Basis: the contract and pure projections in `mining_contract.py` and
`mining_finder.py`. The existing EDEC navigation shell and left menu styling
must remain unchanged.

## Phase 1 — freshness and merge policy

- Define source-specific age bands with tests; do not imply guaranteed yield.
- Merge identical system/body/ring candidates by stable identity.
- Prefer local confirmation over live EDDN reports and catalog candidates.
- Preserve every source timestamp and retain unbound prospector samples.
- Keep unknown Frontier identifiers and missing values explicit.

Exit: deterministic ranking/merge tests cover conflicting and stale sources.

## Phase 2 — profile cache and Spansh query boundary

- Add a profile-bound mining catalog cache using existing atomic JSON handling.
- Pin the minimal `/dump/{id64}` response fields already documented in the
  contract; reject malformed records without replacing valid cache data.
- Add request IDs, profile key and path generation to asynchronous completion.
- Apply bounded caching/rate limiting and expose last refresh/error state.
- Never send Journal, Commander, FID or private-group data to Spansh.

Exit: A→B→A isolation, stale completion and corrupt-cache tests pass.

## Phase 3 — controller read model

- Expose read-only candidate, commodity, method, distance and evidence filters.
- Join current ship/Rhino readiness from existing loadout/state projections.
- Do not parse the Journal again and do not mutate cargo, plans or materials.
- Provide one destination row per system/body/ring with source details nested.

Exit: controller tests prove identical underlying state and no mutations.

## Phase 4 — Mining Finder page

- Add one content page using existing EDEC components and unchanged sidebar.
- Implement target commodity, method, range, evidence and reserve filters.
- Show `LOCAL CONFIRMED`, `LIVE REPORTED`, `CATALOG CANDIDATE` or `STALE`.
- Use content-sized cards, one `COPY SYSTEM` action and accessible labels.
- Keep EN/DE/ES/FR keys synchronized.

Exit: QML smoke, keyboard interaction and narrow-window layout tests pass.

## Phase 5 — live validation

- Validate a natural local scan/hotspot and one synthetic Spansh response.
- Confirm a prospector sample is never promoted to ring-wide yield evidence.
- Confirm refresh/restart/profile switch preserve cache and evidence ordering.
- Run the full unit, hygiene, privacy and QML-smoke release checks.

Release remains a separate explicitly approved operation.
