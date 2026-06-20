# Cortex Layer 2 Implementation Status — Frozen v1

## Summary

Layer 2 v1 is complete for available data-backed modal fact building across
road, sea, and air preparation blocks. This is not global logistics, legal,
customs, aviation, maritime, or road compliance completion.

Layer 2 produces a `FactPackage` only. It does not produce final decisions,
mode rankings, routed itineraries, dossiers, booking instructions, or
customer-facing output. It uses deterministic workflow logic only; no LLM is
used in Layer 2.

## Architecture

```text
ValidatedShipmentRequest
-> FetchPlanner
-> FetchPlan
-> Registry
-> FetchExecutor
-> BlockResponse[]
-> FactPackageBuilder
-> ConflictDetector
-> FactPackage
-> TraceWriter / RerunScope helpers
```

`DataCatalog` supports connector data discovery and asset inventory. `ProviderConfig`
controls mock/live provenance settings. `TraceWriter` and `RerunScope` are
helpers around the fact package and fetch plan; they do not execute connectors.

## Contracts

- Input: `ValidatedShipmentRequest`
- Output: `FactPackage`
- Block unit: `BlockResponse`
- Layer 2 never emits `ReasoningDecision`.
- Layer 2 does not modify the frozen Layer 1 to Layer 2 request schema.
- Layer 2 does not call Layer 3 or Layer 4.

## Implemented Blocks

| Mode | Block | Purpose | Data source | Can emit hard gate? | Main unknowns | Scope limit |
| --- | --- | --- | --- | --- | --- | --- |
| Road | ROAD-C | Road corridor viability | `data/road/road_c_corridor_viability.json` | Yes | Missing origin/destination countries, missing pair record, malformed hard gate | Static corridor evidence only; no live border, permit, escort, or route availability |
| Road | ROAD-A | Road DG / ADR intake | Request cargo flags and DG profile | No | Unknown DG flag, missing DG UN number | ADR planning intake only; no final classification, carrier approval, or legal interpretation |
| Road | ROAD-B | Road vehicle/load fit | `data/road/road_b_vehicle_fit_profiles.json` and request cargo fields | Conditional | Missing weight, volume, dimensions, unknown cargo flags, malformed data | Planning vehicle/load references only; no vehicle assignment, axle validation, permits, escorts, or route survey |
| Road | ROAD-F | Road documents, driver-hours, and border buffers | `data/road/road_f_document_requirements.json` and request commercial fields | Conditional | Missing countries, incoterm, ready date, deadline, unknown cargo flags | Planning document and timing references only; no border clearance, legal driver-hours plan, or exact ETA |
| Road | ROAD-COST | Road planning cost reference | `data/road/road_cost_reference.json` | No | Missing lane, weight, volume/dimensions, incoterm, ready date, unknown cargo flags | Planning cost context only; no quote, rate validity, carrier availability, or payable charge |
| Sea | SEA-C | Origin port capability | `data/sea/sea_c_port_capability.json` | Yes | Missing origin city/country, unknown port, missing capability evidence | Origin port capability reference only; no live terminal status or booking acceptance |
| Sea | SEA-D | Carrier and trade lane reference | `data/sea/sea_d_carrier_trade_lane_reference.json` | Conditional | Missing countries, empty carrier/trade-lane data, unknown cargo flags | Planning carrier/trade-lane examples only; no space, schedule, carrier approval, or live capacity |
| Sea | SEA-A | Sea DG / IMDG acceptance | `data/sea/sea_a_dg_sea_acceptance.json` | Yes | Unknown DG flag, missing UN number, missing record, malformed hard gate | Static DG acceptance evidence only; no vessel-specific acceptance or final IMDG review |
| Sea | SEA-B | Container / load fit | `data/sea/sea_b_container_fit_rules.json` and request cargo fields | Conditional | Missing weight, volume, dimensions, no matched readiness rule, unknown cargo flags | Planning container/load references only; no equipment booking, stuffing approval, or engineering approval |
| Sea | SEA-F | Maritime document checklist | Request fields | No | Unknown DG flag, missing incoterm, missing weight | Base document planning only; no customs clearance, booking readiness, or carrier-specific acceptance |
| Sea | SEA-I | Chokepoints and schedule readiness | `data/sea/sea_i_chokepoints_schedule_readiness.json` | Conditional | Missing countries/cities, ready date, deadline, unknown cargo flags, no matched schedule rule | Planning schedule and chokepoint references only; no vessel schedule, route availability, or live disruption validation |
| Sea | SEA-COST | Sea planning cost reference | `data/sea/sea_cost_reference.json` | No | Missing lane, weight, volume/dimensions, incoterm, ready date, unknown cargo flags | Planning cost context only; no quote, rate validity, booking readiness, or final payable charge |
| Air | AIR-C | Origin airport capability | `data/air/cortex_air_block_c_dataset.json` | No | Missing origin city/country, unknown airport, unverified capability | Static airport capability reference only; no carrier acceptance, live station status, or booking readiness |
| Air | AIR-D | Carrier capability reference | `data/air/cortex_air_block_d_dataset.json` | Conditional | Empty carrier dataset, unknown special flags, no clear capability match | Planning carrier capability examples only; no airline approval, live capacity, or route-specific acceptance |
| Air | AIR-A | Air DG acceptance | `data/air/cortex_air_block_a_dg_records_REPAIRED.json` | Yes | Unknown DG flag, missing UN number, missing record, malformed hard gate | Static DG record evidence only; no airline approval, packing validation, or booking readiness |
| Air | AIR-B | Special handling / cargo category | `data/air/cortex_air_block_b_dataset.json` | Conditional | Unknown special flags, no matching category rule, missing handling code | Planning handling category reference only; no handler or airline acceptance |
| Air | AIR-E | Aircraft / ULD fit | `data/air/cortex_air_block_e_dataset.json` and request dimensions | No | Missing weight, volume, dimensions, oversized flag needing validation | Planning aircraft/ULD reference only; no aircraft assignment, ULD availability, or loadmaster approval |
| Air | AIR-F | Border, permits, and jurisdiction | `data/air/cortex_air_block_f_dataset.json` | Conditional | Missing countries, unknown cargo flags, missing incoterm, no matched rules | Planning permit and border evidence only; no customs clearance, permit issuance, or broker validation |
| Air | AIR-H | Security, screening, and PLACI readiness | `data/air/cortex_air_block_h_dataset.json` | Conditional | Missing countries, cargo description, unknown flags, missing PLACI reference | Planning security readiness only; no screening completion, clearance, or filing acceptance |
| Air | AIR-I | Route and schedule readiness | `data/air/cortex_air_block_i_dataset.json` | Conditional | Missing countries/cities, ready date, deadline, live schedule fields, no route match | Planning route/schedule reference only; no flight schedule, route availability, capacity, or flown status |

## Planner Order

Road:

`ROAD-C -> ROAD-A if DG -> ROAD-B -> ROAD-F -> ROAD-COST`

`ROAD-C` is fail-fast: a blocking road corridor gate protects against deeper
road preparation and cost reference calls for impossible road corridors.

Sea DG:

`SEA-C -> SEA-D -> SEA-A -> SEA-B -> SEA-F -> SEA-I -> SEA-COST`

Sea non-DG:

`SEA-C -> SEA-D -> SEA-B -> SEA-F -> SEA-I -> SEA-COST`

Air DG/special:

`AIR-C -> AIR-D -> AIR-A if DG -> AIR-B if special -> AIR-E -> AIR-F -> AIR-H -> AIR-I`

Air non-DG/non-special:

`AIR-C -> AIR-D -> AIR-E -> AIR-F -> AIR-H -> AIR-I`

Unknown mode uses `candidate_modes`. If `candidate_modes` is empty, the planner
treats it as unresolved mode and defaults to sea, air, and road. Candidate
modes are evaluated independently, not as multimodal linked legs.

## Safety Principles

- Missing data is unknown, never clear.
- Data decides; connectors surface authored or request-derived evidence.
- `BlockResponse.data` contains selected evidence and summaries, not raw dataset dumps.
- Layer 2 makes no booking-ready claims.
- COST blocks do not confirm quotes, rates, validity, availability, or payable charges.
- No LLM is used in Layer 2.
- `ProviderUsed` is explicit through `ProviderConfig`.
- Hard gates are surfaced, not hidden.
- `ROAD-C` fail-fast protects against impossible road corridors.

## ConflictDetector

`ConflictDetector` checks a built fact package for deterministic consistency
issues. It does not change connector output or override hard gates. It currently
detects these conflict classes:

- Later blocks after a blocking gate.
- Cost reference responses for a blocked mode.
- Duplicate block responses.
- DG unclear but DG block unresolved.
- Obvious field unknown/data contradictions.

## TraceWriter

`TraceWriter` builds a safe Layer 2 trace summary for inspection and debugging.
The trace excludes raw `BlockResponse.data` and includes planned blocks, called
blocks, rollup counts, conflict types, and provider/source summaries.

The JSON writer is optional only. Layer 2 does not automatically persist traces
to disk or a database.

## RerunScope

`RerunScope` is a deterministic dependency helper for deciding which Layer 2
blocks should be rerun when request fields change. It does not trigger reruns
automatically and does not call connectors.

- `FIELD_TO_BLOCKS` maps request field paths to impacted block IDs.
- `impacted_blocks_for_changed_fields` returns impacted block IDs for exact
  field changes.
- `impacted_fetch_plan_items` filters a `FetchPlan` to impacted items while
  preserving planner order.
- Parent-prefix fields such as `lane`, `cargo_flags`, `commercial`,
  `core_shipment`, and `mode` expand to their child dependencies.
- Unknown field paths have no impact rather than raising.

## ProviderConfig

`ProviderConfig` makes provider provenance explicit.

- `LAYER2_PROVIDER` defaults to `mock`.
- `LAYER2_PROVIDER_OVERRIDES` can assign provider mode by block ID.
- `live` is a provenance seam only in v1; no external live calls are made yet.

## Known v1 Limitations

- No integrated multimodal linked-leg support yet.
- Unknown mode evaluates modes independently.
- COST blocks are planning references only.
- No external live APIs yet.
- No database trace persistence yet.
- No Layer 3 scoring or decision yet.
- No Layer 4 dossier yet.

## Multimodal v2 Note

Do not add `RequestedMode.multimodal` as a quick patch. Proper multimodal design
requires shipment legs: road origin drayage, sea or air main leg, road final
mile, and other leg-specific handoffs as needed.

This is Contract #1 v2 work.

## Test Status

The full Layer 2 suite currently passes: 201 tests.

The checkpoint command is:

```bash
python -m pytest tests/layer2 -v
```

## Freeze Statement

Layer 2 v1 can now be considered frozen after this documentation update and the
final test run. Future work should start Layer 1 completion or Layer 3 reasoning
against this `FactPackage` contract.
