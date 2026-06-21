# Layer 4 Report Architecture

Layer 4 is the final report generation layer of the Cortex system.

It receives the safe reasoning output from Layer 3, combines it with compact
Layer 2 support and structured OperationalEvidence, builds a controlled prompt,
and asks the Layer 4 report LLM to write the final chat-style transport
readiness report.

The most important idea is this:

> Layer 4 writes the report. It does not decide the ranking, change readiness
> bands, invent live logistics facts, or override Layer 3.

Layer 4 produces a `Layer4Result`, and the full orchestrator exposes
`Layer4Result.assistant_message` as the final user-facing answer.

---

## 1. What Layer 4 Does

Layer 4 answers:

- How should the final transport readiness report be written?
- How should Layer 3 ranking and readiness be explained to the user?
- Which path details, cost boundaries, schedule boundaries, document checks,
  handling requirements, blockers, risks, and next actions should be shown?
- How can the answer be useful without overclaiming final approval?

Layer 4 does not:

- Run intake.
- Fetch data.
- Build the Layer 2 `FactPackage`.
- Rank modes.
- Change readiness bands.
- Change confidence bands.
- Decide whether a path is approved.
- Invent carrier, airline, vessel, flight, truck, port, airport, terminal,
  forwarder, customs, schedule, quote, or legal-clearance details.

Layer 4 is an LLM writer sitting behind strict structured inputs and prompt
rules.

---

## 2. Big Picture Flow

```text
Layer 3 Layer3Result
Layer 2 FactPackage / Layer2Summary
        |
        v
build_operational_evidence
        |
        v
Layer4ReportRequest
        |
        v
build_layer4_prompt
        |
        v
Layer 4 Report LLM
        |
        v
Layer4Result
        |
        v
Final assistant_message
```

In the full orchestrator, Layer 4 runs after Layer 3:

```text
Layer 1 -> Layer 2 -> Layer 3 -> OperationalEvidence -> Layer 4
```

---

## 3. Main Entry Points

### Full Orchestrator Entry Point

The normal production path is:

```text
app/services/orchestrator/full_graph.py
```

The method `_layer4_node`:

1. Reads `Layer3Result` from graph state.
2. Reads `Layer2Summary` from graph state.
3. Reloads Layer 2 from artifact cache if needed for OperationalEvidence.
4. Builds `OperationalEvidence`.
5. Builds `Layer4ReportRequest`.
6. Checks the Layer 4 artifact cache.
7. Calls `build_layer4_report(...)` if no cached report exists.
8. Stores the Layer 4 result in cache.
9. Passes the result to the final report node.

The final report node uses:

```text
layer4.assistant_message
```

as the public assistant message.

### Standalone Developer Endpoint

Layer 4 also has a standalone endpoint:

```text
POST /api/v1/layer4/report
```

Implemented in:

```text
app/api/v1/routes_layer4.py
```

This endpoint accepts a full `Layer4ReportRequest`.

It does not call Layer 1, Layer 2, or Layer 3 by itself. It expects the caller to
provide the needed upstream artifacts.

---

## 4. Input and Output

### Input: `Layer4ReportRequest`

Defined in:

```text
app/schemas/layer4.py
```

Fields:

- `report_type`
- `latest_user_message`
- `response_language`
- `fact_package`
- `layer2_summary`
- `layer3_result`
- `operational_evidence`

Layer 4 requires either:

- `fact_package`, or
- `layer2_summary`

This is enforced by the request schema.

The convenience property:

```text
request.reasoning_decision
```

returns:

```text
request.layer3_result.reasoning_decision
```

### Output: `Layer4Result`

Defined in:

```text
app/schemas/layer4.py
```

Fields:

- `case_id`
- `report_type`
- `assistant_message`
- `modes_reported`
- `warnings_shown`
- `source_reasoning_decision_id`
- `debug`

The final user-facing message is:

```text
assistant_message
```

---

## 5. Truth Hierarchy

Layer 4 prompt rules define a strict truth hierarchy:

1. `ReasoningDecision`
2. `OperationalEvidence`
3. `Layer2Summary`
4. Shipment request
5. Layer 4 wording and organization

This means:

- `ReasoningDecision` is the authority for ranking, readiness bands,
  confidence, hard gates, warnings, allowed claims, and forbidden claims.
- `OperationalEvidence` is the authority for path names, route legs, gateway
  validation status, cost boundaries, schedule boundaries, documents,
  handling/safety requirements, blockers, risks, next actions, and limitations.
- `Layer2Summary` is supporting rollup/debug context.
- Shipment request is the original user-provided fact source.
- The Layer 4 LLM controls only wording, grouping, clarity, and usefulness.

Layer 4 must not resolve conflicts by inventing a new truth. If upstream
structured objects do not support a claim, Layer 4 should not make that claim.

---

## 6. File-by-File Map

### `app/services/layer4/__init__.py`

Exports:

- `build_layer4_report`

This is the public service import surface for Layer 4.

### `app/services/layer4/report_agent.py`

Runs the Layer 4 report agent.

Responsibilities:

- require a Layer 4 LLM model,
- build the Layer 4 prompt,
- invoke the chat model,
- extract text from the model response,
- reject an empty response,
- build `Layer4Result`,
- record success or failure in agent run tracing.

Important helper behavior:

- `_modes_reported` prefers modes from `ReasoningDecision`.
- If no `ReasoningDecision` exists, `_modes_reported` falls back to
  `Layer2Summary.modes_covered` or `FactPackage.derived_rollup.modes_covered`.
- `_warnings_shown` copies must-show warnings from `ReasoningDecision`.

The model is loaded with:

```text
get_chat_model(layer4=True)
```

If no model is configured, Layer 4 raises a `RuntimeError`.

### `app/services/layer4/prompt.py`

Builds the full Layer 4 prompt.

This file contains:

- the system-style Layer 4 report prompt,
- truth hierarchy,
- hard report-writing rules,
- forbidden claims,
- safe wording guidance,
- blocked-case rules,
- compact input packet builders,
- OperationalEvidence compaction,
- ReasoningDecision compaction,
- Layer2Summary support compaction.

This is where most Layer 4 report behavior is controlled.

### `app/schemas/layer4.py`

Defines:

- `Layer4ReportType`
- `Layer4ReportRequest`
- `Layer4Result`

It also validates that Layer 4 receives either a `fact_package` or a
`layer2_summary`.

### `app/api/v1/routes_layer4.py`

Defines:

```text
POST /api/v1/layer4/report
```

The endpoint:

- creates a trace id,
- logs the incoming Layer 4 request,
- runs `build_layer4_report` in a worker thread,
- maps `RuntimeError` to HTTP 503,
- maps `ValueError` to HTTP 422,
- maps unexpected exceptions to HTTP 503.

### `app/schemas/operational_evidence.py`

Defines the structured OperationalEvidence contract.

Important schema objects:

- `OperationalEvidence`
- `OperationalPathEvidence`
- `GatewayEvidence`
- `RouteLegEvidence`
- `CostBoundaryEvidence`
- `ScheduleBoundaryEvidence`
- `DocumentEvidence`
- `HandlingSafetyEvidence`
- `OperationalRiskEvidence`
- `EvidenceSourceRef`

OperationalEvidence is designed for report writing. It turns raw-ish Layer 2 and
Layer 3 structures into path-oriented operational evidence.

### `app/services/operational_evidence/builder.py`

Builds `OperationalEvidence` from:

- `FactPackage`
- optional `ReasoningDecision`
- optional `Layer2Summary`

This is not an LLM. It is deterministic Python mapping.

It builds report-ready paths such as:

- `Sea + Road`
- `Air + Road`
- `Pure Road`
- `Rail / Multimodal`

It maps:

- route legs,
- gateways,
- cost boundaries,
- schedule boundaries,
- documents,
- handling and safety notes,
- blockers,
- risks,
- missing inputs,
- next actions,
- limitations.

### `app/services/orchestrator/full_graph.py`

Layer 4 orchestration happens in:

- `_layer4_node`
- `_layer4_error_node`
- `_final_report_node`
- `_run_layer4_report`

The orchestrator builds `OperationalEvidence` immediately before creating the
Layer 4 request.

### `app/services/orchestrator/artifact_cache.py`

Layer 4 cache behavior lives here.

The Layer 4 cache key includes:

- case id,
- shipment request version,
- report type,
- response language,
- latest user message,
- Layer 3 status,
- `reasoning_decision_id`.

This prevents stale report text from being reused for a different reasoning
decision or language/message context.

---

## 7. OperationalEvidence Role

OperationalEvidence is the structured bridge between Layer 2/Layer 3 and the
Layer 4 writer.

Layer 3 decides readiness and ranking. Layer 2 provides facts. OperationalEvidence
organizes those facts into report-friendly operational paths.

Example path object:

```text
OperationalPathEvidence
```

contains:

- `path_family_id`
- `rank`
- `primary_mode`
- `leg_modes`
- `display_name`
- `recommendation_role`
- `status`
- `readiness_band`
- `confidence_band`
- `evidence_quality`
- `route_legs`
- `gateways`
- `cost`
- `schedule`
- `documents`
- `handling_safety`
- `blockers`
- `risks`
- `missing_inputs`
- `next_actions`
- `limitations`

This gives Layer 4 enough structured material to write a useful report without
dumping the entire `FactPackage` into the prompt.

---

## 8. OperationalEvidence Path Mapping

The builder maps path families to display names:

```text
sea_road_preparation  -> Sea + Road
air_road_preparation  -> Air + Road
pure_road_preparation -> Pure Road
road_preparation      -> Pure Road
sea_preparation       -> Sea
air_preparation       -> Air
rail_multimodal_study -> Rail / Multimodal
```

It maps leg modes:

```text
Sea + Road -> road, sea, road
Air + Road -> road, air, road
Pure Road  -> road
```

For multimodal paths, route legs are represented as:

- first mile,
- main leg,
- last mile.

Gateway candidates are used when available. If a gateway cannot be resolved, the
builder creates validation language instead of inventing a gateway.

---

## 9. Recommendation Roles

OperationalEvidence assigns recommendation roles based on Layer 3 ranked options:

- `recommended`
- `fallback`
- `specialized_study`
- `blocked`
- `not_evaluated`
- `supporting_only`
- `unknown`

The first non-blocked, non-specialized ranked option becomes `recommended`.

The second non-blocked, non-specialized ranked option can become `fallback`.

Blocked options stay blocked.

Specialized-study options are not treated as normal recommendations.

---

## 10. Cost Evidence

Layer 4 must never treat planning references as live quotes.

OperationalEvidence cost mapping uses mode-specific Layer 2 blocks:

```text
Sea + Road -> SEA-COST
Air + Road -> AIR-COST
Pure Road  -> ROAD-COST
```

Air cost may include a normalized estimate if available from `AIR-COST`.

Sea and road costs are treated as planning-reference examples when the data
contains benchmark or reference examples.

If cost is missing or unknown, Layer 4 prompt rules require the report to say
cost is unavailable or not evidenced and to show the missing input reason.

Typical missing inputs:

- live freight quote,
- live carrier quote,
- live road quote,
- road cost reference evidence,
- sea cost benchmark evidence.

---

## 11. Schedule Evidence

Layer 4 must never treat planning schedules as confirmed live schedules.

OperationalEvidence schedule mapping uses:

```text
Sea + Road -> SEA-I
Air + Road -> AIR-I
Pure Road  -> ROAD-F
```

Schedule evidence includes:

- ready date,
- deadline,
- transit time estimate,
- feasibility statement,
- deadline fit,
- live schedule requirement,
- limitations,
- missing inputs.

Important rule:

```text
requires_live_schedule = true
```

means the final report must explicitly say the schedule requires live validation.

For blocked pure-road cases, road timing is not evaluated as a normal schedule.
The schedule evidence explains that Pure Road is blocked by corridor feasibility
evidence.

---

## 12. Gateway Evidence

Gateway evidence comes mostly from:

```text
Sea + Road -> SEA-C
Air + Road -> AIR-C
```

Sea gateway evidence may include:

- main port name,
- UN/LOCODE,
- alternate port name.

Air gateway evidence may include:

- airport name,
- airport code,
- known handlers.

If evidence is missing, the builder does not mirror origin into destination and
does not invent a destination gateway. It creates validation messages such as:

- validate export/import sea gateway candidates,
- destination airport candidate requires validation,
- gateway could not be resolved from current local evidence.

---

## 13. Document Evidence

Document evidence maps from:

```text
Sea + Road -> SEA-F documents
Air + Road -> AIR-F required_documents
Pure Road  -> ROAD-F documents
```

The builder filters profile-specific documents.

For example, for a general cargo shipment, it filters irrelevant human-remains,
pharma, veterinary, phytosanitary, perishable, and similar profile-specific
documents unless that profile is relevant.

This prevents the final report from confusing the user with irrelevant
documents.

---

## 14. Handling and Safety Evidence

Handling and safety evidence is built from Layer 2 planning factors for blocks
matching the path's main mode.

It may include:

- requirements,
- cargo fit notes,
- safety notes,
- source refs.

If no planning factors exist, status remains unknown rather than invented.

---

## 15. Risks, Blockers, and Missing Inputs

OperationalEvidence converts hard gates into blockers and unknowns into risks.

Hard gates become:

```text
OperationalRiskEvidence
```

with:

- category,
- severity,
- message,
- mitigation,
- source refs.

Unknowns become data-gap risks.

Missing fields and next actions are made friendlier for users. Examples:

```text
commercial.incoterm -> Confirm Incoterm.
core_shipment.dimensions -> Confirm pallet dimensions.
cost.live_quote -> Request a live forwarder quote.
schedule.deadline -> Validate schedule against the requested delivery deadline.
```

Irrelevant inactive profile gaps are filtered for general cargo cases.

---

## 16. Path-Scoped Road Blockers

This is a critical Layer 4 rule.

If Pure Road is blocked by an intercontinental overland road gate, but Sea + Road
or Air + Road remain evaluable, Layer 4 must not say:

```text
the whole shipment is blocked
```

Instead, it must say:

```text
Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road.
```

and:

```text
The case contains a blocked pure-road path, but other paths remain evaluable.
```

This protection appears in both:

- OperationalEvidence compaction,
- ReasoningDecision compaction.

The prompt also explicitly says:

- treat mode-specific hard gates as path-scoped,
- a road-mode hard gate blocks Pure Road only,
- do not call it a global blocker when Sea + Road or Air + Road remain evaluable.

---

## 17. Prompt Construction

The prompt is built by:

```text
build_layer4_prompt(request)
```

The prompt contains:

- mission,
- truth hierarchy,
- hard rules,
- forbidden claims,
- safe wording,
- specificity rules,
- compact context rules,
- report style,
- target sections,
- blocked-case rules,
- compact input packet JSON.

The final prompt asks the LLM to return only the final assistant message.

It specifically forbids:

- markdown code fences,
- debug notes,
- `<think>` reasoning blocks.

---

## 18. Prompt Input Packet

Layer 4 builds a compact packet:

```text
{
  "report_type": ...,
  "latest_user_message": ...,
  "response_language": ...,
  "layer2_support": ...,
  "layer3_result": ...,
  "reasoning_decision": ...,
  "operational_evidence": ...
}
```

Layer 4 does not send:

- full `block_responses`,
- full block summaries,
- full connector data dumps,
- Analyst draft,
- Critic review,
- raw internal scoring trace.

This keeps the prompt smaller and reduces the chance that the report LLM uses
internal or irrelevant material.

---

## 19. Prompt Compaction Rules

Important limits in `prompt.py`:

```text
_MAX_TEXT = 200
_MAX_LIST_ITEMS = 5
_MAX_DICT_ITEMS = 5
_MAX_BLOCK_DATA_DEPTH = 2
_MAX_BLOCKS = 24
_MAX_OPERATIONAL_PATHS = 4
_MAX_OPERATIONAL_ITEMS = 4
```

Compaction behavior:

- truncate long text,
- cap lists,
- cap dictionaries,
- summarize deeply nested objects,
- show counts for omitted data,
- include only operationally useful evidence,
- preserve required warnings and next actions.

The tests require the Layer 4 prompt to stay under budget for multimode cases.

---

## 20. Forbidden Claims

Layer 4 prompt forbids unsupported claims such as:

- approved,
- guaranteed,
- booking confirmed,
- customs cleared,
- carrier accepted,
- terminal accepted,
- final legal clearance,
- final customs clearance,
- exact price,
- confirmed live quote,
- confirmed live schedule,
- final booking approval,
- customs clearance confirmation,
- invented vessel details,
- invented flight details,
- invented truck details,
- will arrive,
- will clear,
- best route,
- optimal route.

These phrases matter because Layer 4 is the final user-facing writer. If it
overclaims, the user may treat planning evidence as operational approval.

---

## 21. Safe Wording

Layer 4 is instructed to prefer safe phrases such as:

- strongest preparation path,
- currently ranked first,
- requires carrier validation,
- requires forwarder confirmation,
- not booking-ready,
- not final approval,
- planning reference only,
- live schedule not verified,
- live quote not verified.

This lets the report be useful without pretending the system has completed live
booking, customs, legal, terminal, or carrier validation.

---

## 22. Blocked Case Rules

If the `ReasoningDecision` has:

```text
ranking_type = blocked_ranking
```

or every ranked option has:

```text
readiness_band = BLOCKED
```

Layer 4 must not describe any option as:

- best,
- recommended,
- preferred.

Instead, it should say there is no ready preparation path yet, then describe the
evaluated path and blockers.

If a mode is rank 1 only because it is the only evaluated option, Layer 4 must
say that clearly and avoid implying it is operationally better than unevaluated
modes.

---

## 23. Response Language

Layer 4 receives:

```text
response_language
```

If it is:

```text
auto
```

the prompt tells the model to answer in the language of `latest_user_message`.

If a specific response language is provided, the report should use that language.

---

## 24. Report Shape

Layer 4 writes a chat answer, not JSON and not a PDF.

The target sections are:

1. Executive Decision
2. Shipment Summary
3. Ranked Preparation Paths
4. Cost Comparison
5. Schedule Comparison
6. Document Checklist
7. Handling / Safety Requirements
8. Risks and Blockers
9. Recommended Next Actions
10. Limitations

The prompt says to use these sections when relevant, not mechanically every time.

---

## 25. Layer 4 Agent Run Tracing

Layer 4 records agent runs through:

```text
agent_run_recorder
```

On success, it records:

- case id,
- conversation id,
- trace id,
- layer number,
- agent name,
- run order,
- input summary,
- output,
- prompt,
- response text,
- model,
- start time.

On error, it records:

- the same context,
- the prompt,
- the model,
- the exception.

The Layer 4 agent name is:

```text
layer4_report
```

The full graph passes:

```text
run_order = 5
```

---

## 26. Layer 4 LLM Configuration

Layer 4 uses:

```text
get_chat_model(layer4=True)
```

Relevant settings include:

- `llm_layer4_provider`
- `google_ai_layer4_model`
- `layer4_max_output_tokens`
- `google_ai_layer4_thinking_budget`
- `ollama_layer4_model`

Layer 4 can therefore use a different provider/model from intake and Layer 3.

If no Layer 4-specific provider/model is configured, the model loader can fall
back according to the shared LLM configuration logic.

---

## 27. Cache Behavior

Layer 4 cache keys are more specific than Layer 2 and Layer 3 cache keys.

The base key includes:

- case id,
- shipment request version,
- artifact name.

Layer 4 then adds a fingerprint based on:

- report type,
- response language,
- latest user message,
- Layer 3 status,
- `reasoning_decision_id`.

This means the same reasoning decision can generate different cached reports for
different response language or latest user message context.

Layer 4 artifact lookup by API requires the exact artifact key from
`artifact_refs`.

---

## 28. Error Behavior

### Standalone Endpoint

`routes_layer4.py` maps:

- `RuntimeError` -> HTTP 503
- `ValueError` -> HTTP 422
- other exceptions -> HTTP 503

### Full Orchestrator

If Layer 4 fails inside the full graph, the orchestrator returns a Layer 4 error
result.

The message says Cortex completed fact building and reasoning, but the final
report agent failed before producing the user-facing report.

When configured to include artifacts, the Layer 3 result can be included for
debugging.

---

## 29. Test Coverage Map

### `tests/layer4/test_operational_evidence_prompt.py`

Verifies:

- `Layer4ReportRequest` accepts OperationalEvidence,
- prompt includes operational paths,
- prompt includes truth hierarchy,
- forbidden live claims appear in prompt rules,
- Layer 4 prompt does not depend on `full_response_include_artifacts`,
- road blockers are not treated as global blockers,
- pure-road blocker is marked path-scoped,
- whole case is not called blocked when alternatives remain evaluable,
- irrelevant general-cargo documents are filtered,
- irrelevant profile unknowns are filtered,
- dimensions/incoterm/gateway warnings remain visible.

### `tests/layer4/test_layer4_prompt_budget.py`

Verifies:

- prompt stays under budget for multimode cases,
- full block summaries are excluded,
- full block responses are excluded,
- data excerpts are excluded,
- Analyst draft is excluded,
- Critic review is excluded,
- OperationalEvidence and minimal Layer 2 support are used.

### `tests/layer4/test_layer4_agent_run_tracing.py`

Verifies:

- Layer 4 report run is recorded,
- layer number is 4,
- run order is 5,
- conversation id is preserved,
- prompt size and response size are recorded,
- output JSON includes the assistant message.

### `tests/operational_evidence/test_operational_evidence_schema.py`

Verifies:

- OperationalEvidence supports control-tower path structures,
- path evidence includes rank, modes, recommendation role, and status,
- multimodal leg modes serialize correctly.

### `tests/operational_evidence/test_operational_evidence_builder.py`

Verifies:

- paths are created from `ReasoningDecision`,
- first non-blocked option becomes recommended,
- inputs are not mutated,
- ready date and deadline are copied into schedules,
- documents populate by path,
- irrelevant profile documents are filtered,
- air cost estimates map correctly,
- sea cost benchmark examples become planning references,
- road gate only maps to Pure Road blockers,
- mode-specific road gates are not global blockers,
- route leg placeholders are created for sea/air/road paths,
- missing gateways are not invented,
- air gateway does not mirror origin as destination,
- next actions are user friendly,
- blocked road schedule limitations are human readable,
- full multimode snapshot remains stable.

### `tests/orchestrator/test_full_graph_operational_evidence.py`

Verifies:

- the full graph builds OperationalEvidence for Layer 4,
- OperationalEvidence is still built when `full_response_include_artifacts` is
  false,
- Layer 4 can receive OperationalEvidence even if the full response does not
  expose the raw Layer 2 artifact.

### `tests/orchestrator/test_orchestrator_agent_run_tracing.py`

Verifies:

- public full-message response hides internal Layer 3 details by default while
  still returning the final Layer 4 message.

---

## 30. Developer Rules

### Rule 1: Do not re-rank in Layer 4.

Ranking belongs to Layer 3 `ReasoningDecision`.

Layer 4 must follow the order it receives.

### Rule 2: Do not change readiness or confidence.

Readiness bands and confidence bands come from Layer 3.

Layer 4 can explain them, not alter them.

### Rule 3: Do not invent live logistics facts.

Layer 4 must not invent:

- live quotes,
- live schedules,
- booking confirmations,
- customs clearance,
- carrier acceptance,
- terminal acceptance,
- legal clearance,
- vessel details,
- flight details,
- truck details.

### Rule 4: Keep planning references labeled as planning references.

If evidence status is:

```text
planning_reference
```

the report must not call it confirmed, live, final, or exact.

### Rule 5: Keep validation status honest.

If evidence status is:

```text
requires_validation
```

the report must say validation is still required.

It must not turn validation-needed evidence into approval.

### Rule 6: Keep Pure Road blockers path-scoped.

A road-mode blocker blocks Pure Road.

It does not automatically block Sea + Road or Air + Road.

### Rule 7: Change report instructions in `prompt.py`.

If the final report style, wording, forbidden claims, safe wording, or section
structure must change, update:

```text
app/services/layer4/prompt.py
```

and add prompt tests.

### Rule 8: Change operational mapping in OperationalEvidence builder.

If cost, schedule, document, gateway, route-leg, or blocker mapping changes,
update:

```text
app/services/operational_evidence/builder.py
```

and add OperationalEvidence builder tests.

### Rule 9: Keep prompts compact.

Do not send full block responses, Analyst drafts, Critic reviews, or raw scoring
traces to Layer 4.

Layer 4 should receive compact support and report-ready evidence.

### Rule 10: Protect profile filtering.

General cargo reports should not show irrelevant pharma, live-animal,
perishable, veterinary, phytosanitary, or human-remains documents unless those
profiles are active or supported by cargo flags.

---

## 31. Common Failure Modes

### Layer 4 calls a planning reference a live quote

This is unsafe.

Fix prompt wording or OperationalEvidence status/limitations so planning
references remain clearly labeled.

### Layer 4 says the shipment is blocked when only Pure Road is blocked

This is incorrect when Sea + Road or Air + Road remain evaluable.

Check path-scoped road blocker logic in:

```text
app/services/layer4/prompt.py
app/services/operational_evidence/builder.py
```

### Layer 4 invents a gateway

This is unsafe.

Gateway evidence should show candidates only when present. Otherwise it should
say validation is required or the gateway could not be resolved.

### Layer 4 includes irrelevant documents

Check profile filtering in:

```text
app/services/operational_evidence/builder.py
app/services/layer4/prompt.py
```

### Layer 4 prompt becomes too large

Check:

```text
tests/layer4/test_layer4_prompt_budget.py
```

and reduce prompt packet size through compaction helpers.

### Layer 4 returns an empty response

`build_layer4_report` raises `ValueError`.

The standalone endpoint maps this to HTTP 422.

---

## 32. Mental Model for New Developers

Think of Layer 4 as a controlled report writer:

```text
Layer 3 says what is true about readiness.
OperationalEvidence says what operational facts can be shown.
Layer 4 says it clearly to the user.
```

Layer 4 is allowed to improve clarity.

Layer 4 is not allowed to improve the facts by imagination.

---

## 33. Layer 4 in One Sentence

Layer 4 turns the safe Layer 3 reasoning decision and structured operational
evidence into a clear final transport readiness report, while preserving ranking,
surfacing limitations, and avoiding unsupported claims of approval, live quotes,
confirmed schedules, or global blockers.
