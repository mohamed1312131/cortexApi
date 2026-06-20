# Cortex Layer 2 Fact Builder Architecture

## Summary

Layer 2 is the deterministic fact-building layer for Cortex. It receives the
structured shipment request produced by Layer 1 and builds a `FactPackage` from
road, sea, and air preparation blocks.

Layer 2 does not use an LLM. It does not make the final readiness decision. It
does not produce a customer-facing report, mode ranking, booking instruction,
legal compliance approval, live route, or quote.

Its job is narrower and very important:

```text
Take a ValidatedShipmentRequest
-> plan which data blocks should be checked
-> run deterministic connectors
-> surface evidence, hard gates, unknowns, missing fields, confidence caps, and provenance
-> return a FactPackage for later reasoning/report layers
```

The central safety rule is:

```text
Missing data is unknown, never clear.
```

## Main Files

| File | Responsibility |
| --- | --- |
| `app/services/layer2/service.py` | Public Layer 2 service entry point: builds plan, executes it, builds package. |
| `app/services/layer2/fetch_planner.py` | Creates the auditable `FetchPlan` from the shipment request. |
| `app/services/layer2/fetch_executor.py` | Runs every planned connector through the registry. |
| `app/services/layer2/registry.py` | Maps block ids such as `SEA-A` or `AIR-I` to connector functions. |
| `app/services/layer2/fact_package_builder.py` | Rolls block responses into a `FactPackage`, computes completeness, and attaches conflicts. |
| `app/services/layer2/conflict_detector.py` | Detects deterministic consistency issues across block responses. |
| `app/services/layer2/data_catalog.py` | Inspects JSON assets under `data/` and classifies files by mode, block, role, shape, and connector status. |
| `app/services/layer2/provider_config.py` | Controls mock/live provenance labels through environment variables. |
| `app/services/layer2/summary.py` | Builds compact `Layer2Summary` objects for the full orchestrator response. |
| `app/services/layer2/trace_writer.py` | Builds safe debug traces without raw connector data dumps. |
| `app/services/layer2/rerun_scope.py` | Future/inactive partial-rerun helper. Not wired into product execution. |
| `app/services/layer2/connectors/*.py` | Deterministic block connectors for road, sea, and air. |
| `app/schemas/fetch_plan.py` | `FetchPlan`, `FetchPlanItem`, required inputs, priority, fallback and empty-response policies. |
| `app/schemas/block_response.py` | `BlockResponse`, `HardGate`, `Unknown`, `BlockConfidence`, `Provenance`. |
| `app/schemas/fact_package.py` | `FactPackage`, completeness, rollup, conflicts, confidence caps. |
| `app/schemas/layer2_summary.py` | Compact summary contract used by full orchestration. |

## Runtime Flow

The public entry point is `build_fact_package_for_request`.

```text
ValidatedShipmentRequest
-> build_fetch_plan(request)
-> execute_fetch_plan(request, plan)
-> build_fact_package(request, plan, responses)
-> FactPackage
```

In code:

```python
def build_fact_package_for_request(request):
    plan = build_fetch_plan(request)
    responses = execute_fetch_plan(request, plan)
    return build_fact_package(request, plan, responses)
```

Layer 2 assumes Layer 1 and the orchestrator have already decided the request is
safe to run. Layer 2 still treats missing request fields as unknown inside each
block.

## Core Contracts

### `FetchPlan`

`FetchPlan` is an auditable list of planned connector calls.

Important fields:

- `case_id`
- `items`

Each `FetchPlanItem` contains:

- `block_id`
- `mode`
- `reason`
- `priority`
- `required_inputs`
- `skip_condition`
- `empty_behavior`
- `fallback_policy`

Important rule:

```text
FetchPlanItem.mode must be a concrete mode: sea, air, or road.
It cannot be unknown.
```

`requested_mode = unknown` can exist in the user request, but planned connector
calls must be concrete.

### `BlockResponse`

Every connector returns a normalized `BlockResponse`.

Important fields:

- `block_id`
- `mode`
- `status`
- `data`
- `hard_gates`
- `planning_factors`
- `unknowns`
- `missing_fields`
- `confidence`
- `provenance`

`data` is intentionally loose because each block has different evidence. Safety
critical fields are typed:

- hard gates use `HardGate`
- unknowns use `Unknown`
- confidence uses `BlockConfidence`
- source/provenance uses `Provenance`

Connectors must return selected evidence and summaries. They must not dump raw
dataset files into `BlockResponse.data`.

### `HardGate`

`HardGate` surfaces authored or deterministic blocking conditions.

Important fields:

- `gate_id`
- `mode`
- `severity`
- `status`
- `message`
- `source_block`
- `basis`

A blocking triggered gate makes the package completeness `blocked`.

### `Unknown`

`Unknown` represents a gap that must stay visible.

Important fields:

- `field`
- `reason`
- `impact`

Layer 2 uses unknowns instead of silently treating missing data as safe.

### `BlockConfidence`

`BlockConfidence` records the source confidence and optional cap.

Important fields:

- `source_confidence`
- `cap`
- `reasons`

Examples:

- planning-only cost blocks cap confidence
- missing dimensions cap fit confidence
- unknown or incomplete reference data keeps confidence unknown

### `Provenance`

`Provenance` records where the block came from and whether the configured
provider is `mock` or `live`.

Important fields:

- `source`
- `record_id`
- `provider_used`
- `fallback_used`
- `live_data_available`
- `extra`

In v1, `live` is only a provenance seam. The connectors do not call external
live APIs yet.

### `FactPackage`

`FactPackage` is the Layer 2 output and the Layer 2 -> Layer 3 contract.

Important fields:

- `case_id`
- `request`
- `fetch_plan`
- `block_responses`
- `global_hard_gates`
- `global_unknowns`
- `global_missing_fields`
- `conflicts`
- `completeness`
- `derived_rollup`

`derived_rollup` is regenerated from block responses and global fields. It is a
convenience snapshot, not a hand-edited source of truth.

## Planner Behavior

The planner reads `request.mode.requested_mode`.

If the requested mode is concrete, it plans only that mode.

If the requested mode is `unknown`, it reads `request.mode.candidate_modes`.

If `candidate_modes` is empty, Layer 2 treats that as unresolved mode and
defaults to:

```text
sea, air, road
```

Candidate modes are evaluated independently. This is not multimodal linked-leg
planning.

## Road Plan

Road order:

```text
ROAD-C -> ROAD-A -> ROAD-B -> ROAD-F -> ROAD-COST
```

| Block | Purpose |
| --- | --- |
| `ROAD-C` | Road corridor viability by country pair. |
| `ROAD-A` | Road dangerous goods / ADR intake and acceptance reference. |
| `ROAD-B` | Road vehicle/load fit planning reference. |
| `ROAD-F` | Road documents, driver-hours, border buffer, and transit preparation reference. |
| `ROAD-COST` | Road cost planning reference. Not a quote. |

`ROAD-C` has fail-fast priority in the plan metadata, but the current executor
still runs the later road blocks. A blocking road corridor gate is surfaced in
the package while the rest of the evidence is still collected.

## Sea Plan

Sea order for dangerous goods, likely dangerous goods, or unknown DG:

```text
SEA-C -> SEA-D -> SEA-A -> SEA-B -> SEA-F -> SEA-I -> SEA-COST
```

Sea order when dangerous goods is explicitly `no`:

```text
SEA-C -> SEA-D -> SEA-B -> SEA-F -> SEA-I -> SEA-COST
```

| Block | Purpose |
| --- | --- |
| `SEA-C` | Origin port capability. |
| `SEA-D` | Carrier and trade lane planning reference. |
| `SEA-A` | Sea dangerous goods / IMDG acceptance reference. |
| `SEA-B` | Container and load fit planning reference. |
| `SEA-F` | Maritime document checklist. Currently request-derived, not data-file backed. |
| `SEA-I` | Chokepoint and schedule readiness reference. |
| `SEA-COST` | Sea cost planning reference. Not a quote. |

## Air Plan

Air order:

```text
AIR-C -> AIR-D -> AIR-A if DG/likely/unknown -> AIR-B if special/unknown flags -> AIR-E -> AIR-F -> AIR-H -> AIR-I -> AIR-COST
```

| Block | Purpose |
| --- | --- |
| `AIR-C` | Origin airport capability. |
| `AIR-D` | Carrier capability planning reference. |
| `AIR-A` | Air dangerous goods / IATA-style acceptance reference. |
| `AIR-B` | Special handling categories and handling codes. |
| `AIR-E` | Aircraft and ULD fit planning reference. |
| `AIR-F` | Air border, permits, jurisdiction, and customs preparation reference. |
| `AIR-H` | Air security, screening, and PLACI readiness reference. |
| `AIR-I` | Air route, schedule, risk, and tracking milestone readiness reference. |
| `AIR-COST` | Air cost planning estimate. Not a quote. |

`AIR-B` is planned when any special flag is `yes`, `likely`, or `unknown` for:

- temperature controlled
- oversized
- high value
- pharma
- food perishable
- live animals
- dangerous goods

## Executor Behavior

The executor loops over every `FetchPlanItem`.

```text
for item in plan.items:
    connector = get_connector(item.block_id)
    if connector is missing:
        return an error BlockResponse for that block
    else:
        call connector(request)
```

Important current behavior:

```text
All planned blocks run, even if an earlier block returns a blocking hard gate.
```

This is intentional in the current code. A blocked path can still include useful
carrier, fit, document, security, and cost context. Layer 3 remains responsible
for deciding what can be claimed about blocked paths.

## Registry

`BLOCK_REGISTRY` maps block ids to connector callables.

Registered blocks:

- `ROAD-C`
- `ROAD-A`
- `ROAD-B`
- `ROAD-F`
- `ROAD-COST`
- `SEA-C`
- `SEA-D`
- `SEA-A`
- `SEA-B`
- `SEA-F`
- `SEA-I`
- `SEA-COST`
- `AIR-C`
- `AIR-D`
- `AIR-A`
- `AIR-B`
- `AIR-E`
- `AIR-F`
- `AIR-H`
- `AIR-I`
- `AIR-COST`

If a planned block is not registered, the executor returns a `BlockResponse`
with:

- `status = error`
- an unknown explaining the missing connector
- provenance source `fetch_executor`

## Connector Pattern

Most connectors follow this pattern:

```text
load relevant JSON data
-> read request fields
-> match records or rules
-> produce selected evidence in data
-> add unknowns for missing request fields or unmatched/malformed data
-> add hard gates when authored rules trigger
-> add planning factors
-> attach confidence
-> attach provenance
-> return BlockResponse
```

Connectors are conservative. If the data is missing, malformed, not matched, or
not enough to verify the request, the connector returns unknowns.

## Road Connectors

### `ROAD-C`

File:

- `app/services/layer2/connectors/road_c_connector.py`

Main data:

- `data/road/road_c_corridor_viability.json`

What it does:

- checks road corridor viability for origin/destination country pair
- surfaces authored hard gates from corridor data
- returns unknown when countries are missing
- returns unknown when no country-pair record exists
- returns unknown when `hard_gate` is malformed

Example hard gate:

```text
Intercontinental overland corridor impractical
```

### `ROAD-A`

File:

- `app/services/layer2/connectors/road_a_connector.py`

Main data:

- `data/road/road_a_dg_road_acceptance.json`

What it does:

- handles road dangerous goods / ADR references
- returns not applicable when DG is explicitly `no`
- returns unknown when DG status is unknown
- requires `profiles.dangerous_goods.un_number` when DG is yes/likely
- normalizes UN numbers
- looks up ADR records
- surfaces prohibited or not accepted records as blocking hard gates
- surfaces multi-entry/check-required records as unknown substance details

### `ROAD-B`

File:

- `app/services/layer2/connectors/road_b_connector.py`

Main/support data:

- `data/road/road_b_vehicle_fit_profiles.json`
- `data/road/road_b_standard_limits.json`
- `data/road/road_b_abnormal_load_rules.json`
- `data/road/road_b_confidence_rules.json`

What it does:

- checks road vehicle/load fit as a planning reference
- requires weight
- needs volume or dimensions for useful fit planning
- matches vehicle profiles by structured cargo flags or cargo-description tokens
- handles oversized cargo conservatively
- adds planning factors for DG and special cargo
- surfaces readiness gates and abnormal load gates

### `ROAD-F`

File:

- `app/services/layer2/connectors/road_f_connector.py`

Main/support data:

- `data/road/road_f_document_requirements.json`
- `data/road/road_f_driver_hours_rules.json`
- `data/road/road_f_border_buffer_reference.json`
- `data/road/road_f_realistic_transit_model.json`
- `data/road/road_f_confidence_rules.json`

What it does:

- builds road preparation evidence for documents, driver-hours, border buffers,
  and timing realism
- requires origin/destination countries for border/document context
- treats missing incoterm, ready date, and deadline as unknowns
- includes base documents such as CMR, commercial invoice, and packing list
- includes ADR/DG document requirements when DG is active
- surfaces oversized cargo as requiring route/permit/escort validation

### `ROAD-COST`

File:

- `app/services/layer2/connectors/road_cost_connector.py`

Main data:

- `data/road/road_cost_reference.json`

What it does:

- returns road cost planning context
- tries to match lane/corridor examples by country or region hints
- adds unknowns for missing lane, weight, volume/dimensions, incoterm, ready date
- never returns a quote

## Sea Connectors

### `SEA-C`

File:

- `app/services/layer2/connectors/sea_c_connector.py`

Main data:

- `data/sea/sea_c_port_capability.json`

What it does:

- looks up origin port/city capability
- uses country as a hint where possible
- checks container capability, DG handling, and draft capability
- surfaces known port constraints as hard gates with high severity

### `SEA-D`

File:

- `app/services/layer2/connectors/sea_d_connector.py`

Main data:

- `data/sea/sea_d_carrier_trade_lane_reference.json`

What it does:

- surfaces carrier and trade lane planning references
- requires origin/destination countries for useful lane context
- adds unknowns for unknown cargo flags
- adds planning factors for DG, temperature/perishable/pharma, and oversized cargo
- does not confirm carrier acceptance, space, or schedule

### `SEA-A`

File:

- `app/services/layer2/connectors/sea_a_connector.py`

Main data:

- `data/sea/sea_a_dg_sea_acceptance.json`

What it does:

- handles sea dangerous goods / IMDG-style records
- returns not applicable when DG is explicitly `no`
- returns unknown when DG status is unknown
- requires a UN number when DG is yes/likely
- normalizes numeric UN values
- returns stowage, segregation, acceptance status, and hard gate evidence
- treats missing/malformed hard gate as unknown

### `SEA-B`

File:

- `app/services/layer2/connectors/sea_b_connector.py`

Main/support data:

- `data/sea/sea_b_container_fit_rules.json`
- `data/sea/sea_b_container_specs.json`
- `data/sea/sea_b_readiness_rules.json`
- `data/sea/sea_b_cargo_type_equipment_mapping.json`
- `data/sea/sea_b_confidence_rules.json`

What it does:

- checks container/load fit as planning reference
- requires weight
- needs volume or dimensions for useful fit planning
- surfaces container examples and cargo/equipment mapping examples
- adds readiness unknowns when rules do not match
- adds planning factors for reefer/perishable/pharma and DG cargo

### `SEA-F`

File:

- `app/services/layer2/connectors/sea_f_connector.py`

Current behavior:

- request-derived planning checklist
- does not read the SEA-F JSON data files yet

What it does:

- starts with base sea documents:
  - commercial invoice
  - packing list
  - bill of lading
  - verified gross mass/VGM
- adds DG documents when DG is yes/likely
- adds unknown when DG status is unknown
- adds unknown when incoterm is missing
- adds missing field and unknown when weight is missing

### `SEA-I`

File:

- `app/services/layer2/connectors/sea_i_connector.py`

Main data:

- `data/sea/sea_i_chokepoints_schedule_readiness.json`

What it does:

- checks sea chokepoint and schedule readiness references
- requires origin/destination countries
- surfaces unknowns for missing cities, ready date, deadline, and unknown cargo flags
- matches schedule readiness rules by active cargo flags
- surfaces schedule hard gates when authored rules trigger
- returns chokepoint examples

### `SEA-COST`

File:

- `app/services/layer2/connectors/sea_cost_connector.py`

Main data:

- `data/sea/sea_cost_reference.json`

What it does:

- returns sea cost planning references
- matches lane benchmarks by country/region hints
- returns surcharge and local charge examples
- adds unknowns for missing lane, weight, volume/dimensions, incoterm, ready date
- never returns a quote

## Air Connectors

### `AIR-C`

File:

- `app/services/layer2/connectors/air_c_connector.py`

Main data:

- `data/air/cortex_air_block_c_dataset.json`

What it does:

- looks up origin airport/city capability
- checks cargo terminal, customs, DG handling, and temperature storage capability
- returns unknown when origin city is missing
- returns unknown when no airport capability record matches
- does not confirm handler or airline acceptance

### `AIR-D`

File:

- `app/services/layer2/connectors/air_d_connector.py`

Main data:

- `data/air/cortex_air_block_d_dataset.json`

What it does:

- surfaces carrier capability planning references
- checks capability examples for DG, pharma/temperature, oversized/freighter needs
- adds unknowns for unknown cargo flags
- returns hard gates if authored carrier records contain them
- does not confirm airline acceptance

### `AIR-A`

File:

- `app/services/layer2/connectors/air_a_connector.py`

Main data:

- `data/air/cortex_air_block_a_dg_records_REPAIRED.json`

What it does:

- handles air dangerous goods records
- returns not applicable when DG is explicitly `no`
- returns unknown when DG status is unknown
- requires a UN number when DG is yes/likely
- normalizes numeric UN values
- returns air acceptance status, passenger/cargo aircraft limits, CAO restriction,
  and hard gate evidence

### `AIR-B`

File:

- `app/services/layer2/connectors/air_b_connector.py`

Main data:

- `data/air/cortex_air_block_b_dataset.json`

What it does:

- maps active special cargo flags to handling categories and special handling
  codes
- handles temperature, pharma, perishables, live animals, oversized, high value,
  and DG-related flags
- surfaces unknown special flags
- returns not applicable if no special or unknown flags exist
- surfaces hard gates from category rules

### `AIR-E`

File:

- `app/services/layer2/connectors/air_e_connector.py`

Main data:

- `data/air/cortex_air_block_e_dataset.json`

What it does:

- checks aircraft and ULD fit as planning reference
- requires weight
- needs volume or dimensions for useful ULD/door fit planning
- returns reference aircraft and ULD counts
- returns possible ULD families
- adds oversized cargo unknown because airline/freighter validation is required

### `AIR-F`

File:

- `app/services/layer2/connectors/air_f_connector.py`

Main data:

- `data/air/cortex_air_block_f_dataset.json`

What it does:

- matches air border, permit, customs, and jurisdiction rules
- requires origin/destination countries
- maps request countries to jurisdictions
- treats missing incoterm as unknown
- returns required documents and authorities
- surfaces hard gates from border rules when triggered

### `AIR-H`

File:

- `app/services/layer2/connectors/air_h_connector.py`

Main data:

- `data/air/cortex_air_block_h_dataset.json`

What it does:

- checks air security, screening, and PLACI readiness references
- maps countries to jurisdictions such as EU, UK, US, UAE, Canada, or global
- requires origin/destination countries
- treats missing cargo description as unknown
- returns required security actions
- returns PLACI minimum data elements
- surfaces hard gates from security rules

### `AIR-I`

File:

- `app/services/layer2/connectors/air_i_connector.py`

Main data:

- `data/air/cortex_air_block_i_dataset.json`

What it does:

- checks air route feasibility, route risk, schedule input requirements, and
  tracking milestones
- requires origin/destination countries
- adds unknowns for missing city/airport, ready date, deadline, and live schedule data
- rolls many live schedule fields into one `schedule.live_schedule_data` unknown
- returns tracking milestones as curated planning content
- surfaces route feasibility hard gates

### `AIR-COST`

File:

- `app/services/layer2/connectors/air_cost_connector.py`

Main data:

- `data/air/air_reference.json`

What it does:

- computes air cost planning estimates from chargeable weight, rate buckets, and
  planning surcharges
- calculates volumetric weight from `volume_cbm` or dimensions
- uses chargeable weight as max(actual, volumetric)
- applies fuel/security and optional DG planning surcharges
- returns a planning cost range in USD when enough data exists
- always keeps confidence capped because a live carrier quote is required
- never returns a quote

## Fact Package Rollup

`build_rollup` aggregates:

- all block hard gates
- all block unknowns
- all block missing fields
- all confidence caps
- modes covered
- blocks called
- blocks failed
- blocks empty

The rollup de-duplicates missing fields while preserving first-seen order.

## Completeness

`compute_completeness` applies this order:

1. If any blocking hard gate is triggered:

   ```text
   blocked
   ```

2. Else if any block status is `error`:

   ```text
   insufficient
   ```

3. Else if any unknowns or missing fields exist:

   ```text
   incomplete_but_usable
   ```

4. Else:

   ```text
   complete_enough
   ```

Completeness is not a final shipment approval. It is a fact-package quality
signal for later layers.

## Conflict Detector

`detect_conflicts` currently checks:

- duplicate block responses for the same mode/block
- DG unclear but DG block was skipped/not applicable
- obvious field marked unknown in one block while another block appears to have
  the value

The current tests intentionally allow later blocks and cost references to exist
after a blocking gate. That is no longer treated as a conflict.

## Layer 2 Summary

The full orchestrator can return `Layer2Summary` instead of the full raw
`FactPackage`.

`build_layer2_summary` includes:

- request summary
- completeness status and reasons
- modes covered
- block statuses
- block counts
- hard gate summaries
- unknown summaries
- missing fields
- conflicts
- confidence cap reasons
- cost summaries
- compact block summaries
- omitted counts for truncated sections

This keeps full orchestration responses smaller while preserving useful
inspection details.

## Trace Writer

`build_layer2_trace` returns a safe debug trace with:

- planned blocks
- called blocks
- failed blocks
- empty blocks
- modes covered
- counts for gates, unknowns, missing fields, confidence caps, conflicts
- block summaries

It does not include raw `BlockResponse.data`.

`write_layer2_trace_json` can write the trace to disk when explicitly called.
Layer 2 does not automatically persist traces.

## Provider Config

Provider mode comes from:

```text
LAYER2_PROVIDER
LAYER2_PROVIDER_OVERRIDES
```

Examples:

```text
LAYER2_PROVIDER=mock
LAYER2_PROVIDER=live
LAYER2_PROVIDER_OVERRIDES=AIR-C=live,SEA-COST=mock
```

Invalid provider values fall back to `mock`.

In v1, provider mode is provenance metadata. The connectors still use local JSON
data and request-derived planning logic.

## Data Catalog

`data_catalog.py` scans `data/**/*.json` and infers:

- mode
- block id
- file role
- top-level JSON shape
- top-level keys
- first list key
- record count
- first record keys
- connector status

This is used by tests and by connector data-path helpers such as
`get_main_asset`.

The full per-file inventory is documented separately in
`docs/LAYER2_DATA_INVENTORY.md`.

## Rerun Scope

`app/services/layer2/rerun_scope.py` is explicitly marked inactive/future.

It maps changed Layer 1 fields to impacted Layer 2 block ids, but it is not
wired into product execution.

Current product behavior:

```text
If Layer 2 runs, it performs a full Layer 2 build.
```

Layer 1's `rerun_scope` and `requires_layer_2_rerun` are advisory metadata in
v1.

## Test Map

Layer 2 tests protect:

- planner order and mode selection
- unknown-mode fallback behavior
- registry completeness
- connector-specific unknowns, gates, and planning factors
- no raw dataset dumping
- fact package rollup and completeness
- all blocks still running after gates
- provider config provenance
- data catalog validity
- summary and trace behavior
- inactive rerun scope helper behavior

Important test areas:

- `tests/layer2/test_fetch_planner.py`
- `tests/layer2/test_fetch_executor.py`
- `tests/layer2/test_fact_package_builder.py`
- `tests/layer2/test_layer2_consistency.py`
- `tests/layer2/test_*_connector.py`
- `tests/layer2/test_*_planner_service.py`
- `tests/layer2/test_provider_config.py`
- `tests/layer2/test_data_catalog.py`
- `tests/layer2/test_trace_writer.py`
- `tests/layer2/test_rerun_scope.py`

## Developer Rules

- Do not add LLM calls to Layer 2.
- Do not make final readiness decisions in Layer 2.
- Do not claim booking readiness, legal compliance, carrier acceptance, live
  availability, or quote validity.
- Do not treat missing or unmatched data as clear.
- Do not dump raw JSON datasets into `BlockResponse.data`.
- Do not mutate the Layer 1 `ValidatedShipmentRequest`.
- Do not hide hard gates.
- Do not suppress unknowns just because a connector returns some data.
- Keep cost blocks planning-only.
- Keep provider mode explicit in provenance.
- Do not wire `rerun_scope.py` into product execution until partial rerun is
  formally designed and tested.

