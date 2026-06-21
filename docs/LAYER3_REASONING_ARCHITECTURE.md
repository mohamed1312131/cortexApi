# Layer 3 Reasoning Architecture

Layer 3 is the reasoning and safety layer of the Cortex system.

It receives the Layer 2 `FactPackage`, turns it into a compact reasoning
context, ranks the possible transport path families deterministically, asks an
LLM Analyst to explain that fixed ranking, optionally asks an LLM Critic to
review the explanation, and then runs a Python Safety Gate before anything is
allowed to cross into Layer 4.

The most important idea is this:

> Layer 3 does not let the LLM decide the ranking. The deterministic engine owns
> the ranking. The LLM may explain it, but Python validates and gates the result.

Layer 3 produces a `Layer3Result`. When successful, that result contains a frozen
`ReasoningDecision`, which is the safe contract passed to Layer 4.

---

## 1. What Layer 3 Does

Layer 3 answers:

- Which transport path family looks most ready based on the evidence?
- Which path families are blocked, capped, or uncertain?
- Which hard gates, unknowns, conflicts, and missing fields must be shown?
- Is the reasoning safe enough to pass to Layer 4?
- If not, should the Analyst revise, should the user clarify, should Layer 2
  refetch, or should the case be blocked?

Layer 3 does not:

- Fetch operational data.
- Replace Layer 1 intake.
- Replace Layer 2 fact building.
- Produce the final customer-facing report.
- Produce final legal, customs, carrier, or booking approval.
- Expose raw numeric scores to Layer 4 or users.

---

## 2. Big Picture Flow

```text
Layer 2 FactPackage
        |
        v
prepare_reasoning_context
        |
        v
Deterministic Decision Engine
        |
        v
Analyst Agent
        |
        v
Critic Agent, only when risk conditions require it
        |
        v
Safety Gate
        |
        v
Routing
        |
        +--> revise Analyst, bounded by max_revisions
        +--> request user clarification
        +--> request Layer 2 fetch
        +--> blocked
        +--> pass to Layer 4 with ReasoningDecision
```

Layer 3 is implemented as a LangGraph state machine in
`app/services/layer3/graph.py`.

---

## 3. Main Entry Points

### Full Orchestrator Entry Point

The normal production path is the full Cortex orchestrator:

```text
Layer 1 -> Layer 2 -> Layer 3 -> Layer 4
```

The full graph calls Layer 3 from:

```text
app/services/orchestrator/full_graph.py
```

The method `_layer3_node`:

1. Checks the artifact cache for an existing Layer 3 result.
2. Reloads Layer 2 from cache if Layer 2 was dropped from graph state.
3. Calls `run_layer3(...)`.
4. Stores the Layer 3 result back in the artifact cache.
5. Passes Layer 3 onward to Layer 4.

Layer 3 cache access is implemented in:

```text
app/services/orchestrator/artifact_cache.py
```

### Developer / Debug Endpoint

Layer 3 also has a standalone developer endpoint:

```text
POST /api/v1/layer3/reason
```

Implemented in:

```text
app/api/v1/routes_layer3.py
```

This endpoint accepts a pre-built `FactPackage`.

Important: this endpoint does not call Layer 1 or Layer 2. It is useful for
debugging Layer 3 directly.

---

## 4. Input and Output

### Input

Layer 3 input is a Layer 2 `FactPackage`.

The `FactPackage` already contains:

- the normalized shipment request,
- candidate/requested modes,
- block responses from connectors,
- hard gates,
- unknowns,
- missing fields,
- conflicts,
- completeness information,
- active profiles,
- modes covered by Layer 2,
- evidence references from deterministic data.

Layer 3 does not own separate JSON data files. Unlike Layer 2, it does not read
the static `data/` catalog directly. It reasons over the already-built
`FactPackage`.

### Output

Layer 3 returns:

```text
Layer3Result
```

The important statuses are:

- `pass_to_layer4`
- `request_user_clarification`
- `request_layer2_fetch`
- `blocked`
- `error`

When status is `pass_to_layer4`, `Layer3Result.reasoning_decision` must exist.

The `ReasoningDecision` is the frozen Layer 3 -> Layer 4 contract.

---

## 5. Important Schema Objects

### `ReasoningContext`

Defined in:

```text
app/schemas/layer3.py
```

Built by:

```text
app/services/layer3/context_builder.py
```

This is a deterministic read model of the `FactPackage`.

It contains:

- case id,
- request summary,
- candidate modes,
- active profiles,
- modes covered,
- block statuses,
- hard gates,
- unknowns,
- missing fields,
- conflicts,
- confidence cap reasons,
- evidence refs,
- completeness status.

This is the main object that Layer 3 uses internally before ranking.

### `DeterministicDecision`

Defined in:

```text
app/schemas/layer3.py
```

Built by:

```text
app/services/layer3/deterministic_decision_engine.py
```

This is the internal deterministic ranking truth.

It contains:

- overall readiness band,
- ranking type,
- ranked path families,
- hard gate summary,
- critical unknowns,
- confidence report,
- must-show warnings,
- internal trace reference.

The Analyst is not allowed to change this decision.

### `InternalScoringTrace`

Defined in:

```text
app/schemas/internal_scoring_trace.py
```

This object contains raw numeric scoring details.

Important rule:

> `InternalScoringTrace` is internal only. It must never be embedded in
> `ReasoningDecision`, Layer 4 prompts, or user-facing output.

Layer 3 may reference it by id through `internal_trace_ref`, but the raw numbers
stay inside internal diagnostics.

### `AnalystDraft`

Defined in:

```text
app/schemas/layer3.py
```

Built by:

```text
app/services/layer3/agents/analyst_agent.py
```

This is the LLM Analyst's explanation of the deterministic ranking.

It must:

- use the same path families as the deterministic decision,
- preserve the same rank order,
- preserve the same modes,
- cite only allowed evidence refs,
- include one narrative per ranked path family,
- surface required hard gates, unknowns, conflicts, and missing fields,
- avoid forbidden claims,
- avoid raw score leakage.

The Analyst can dispute the ranking only explicitly, with a dispute reason. That
does not change the deterministic ranking; it only causes extra review pressure.

### `CriticReview`

Defined in:

```text
app/schemas/layer3.py
```

Built by:

```text
app/services/layer3/agents/critic_agent.py
```

The Critic is an advisory LLM reviewer.

It checks:

- unsupported claims,
- hidden uncertainty,
- hidden hard gates,
- hidden conflicts,
- vague or overconfident language,
- evidence misuse,
- contradictions,
- forbidden claims,
- raw score leakage.

The Critic can recommend `pass`, `revise`, or `block`, but the Python Safety Gate
is still authoritative.

### `SafetyGateReport`

Defined in:

```text
app/schemas/layer3.py
```

Built by:

```text
app/services/layer3/safety_gate.py
```

This is the deterministic Python safety verdict.

It re-checks the Analyst draft against the deterministic decision and reasoning
context from scratch. It does not trust the Analyst's own validation.

### `ReasoningDecision`

Defined in:

```text
app/schemas/reasoning_decision.py
```

Built by:

```text
app/services/layer3/decision_builder.py
```

This is the frozen Layer 3 -> Layer 4 seam.

It contains:

- ranking type,
- ranked readiness options,
- readiness bands,
- confidence band,
- allowed claims,
- forbidden claims,
- global unknowns,
- global next actions,
- must-show warnings.

It does not contain:

- raw scores,
- evidence refs,
- internal scoring trace,
- connector block dumps,
- hidden model analysis.

---

## 6. File-by-File Map

### `app/services/layer3/__init__.py`

Exports:

- `Layer3ReasoningGraph`
- `run_layer3`

This is the public service import surface for Layer 3.

### `app/services/layer3/state.py`

Defines the internal graph state type, `Layer3State`.

It carries:

- input `FactPackage`,
- `ReasoningContext`,
- `DeterministicDecision`,
- `InternalScoringTrace`,
- `AnalystDraft`,
- `CriticReview`,
- `SafetyGateReport`,
- final `Layer3Result`,
- revision counters,
- routing flags,
- trace/conversation ids.

### `app/services/layer3/graph.py`

Owns the Layer 3 LangGraph workflow.

It wires these nodes:

- prepare reasoning context,
- deterministic decision engine,
- analyst agent,
- critic agent or skip,
- safety gate,
- revise,
- pass terminal,
- blocked terminal,
- clarification terminal,
- Layer 2 fetch terminal.

It also decides whether to run the Critic.

The Critic runs when risk is higher, including:

- Analyst disputes the ranking,
- deterministic overall readiness is high,
- serious hard gates exist,
- high or critical unknowns exist,
- conflicts exist,
- dangerous goods profile is active,
- lithium battery profile is active.

### `app/services/layer3/context_builder.py`

Builds `ReasoningContext` from the Layer 2 `FactPackage`.

Important properties:

- deterministic,
- no LLM,
- no randomness,
- no I/O,
- does not mutate the `FactPackage`,
- deduplicates evidence refs,
- excludes `RequestedMode.unknown` from concrete candidate modes.

It converts Layer 2 facts into Layer 3 factors:

- hard gates,
- unknowns,
- missing fields,
- conflicts,
- confidence cap reasons,
- evidence references.

### `app/services/layer3/deterministic_decision_engine.py`

This is the core ranking engine.

It builds:

- `DeterministicDecision`,
- `InternalScoringTrace`.

It ranks path families such as:

- `pure_road_preparation`,
- `sea_road_preparation`,
- `air_road_preparation`.

It starts paths from a high readiness assumption, then applies caps based on:

- triggered hard gates,
- hard gate severity,
- unknowns,
- dangerous goods uncertainty,
- lithium battery uncertainty,
- missing fields,
- conflicts,
- completeness status.

The engine sorts options deterministically using:

- readiness band,
- fewer blocking/high gates,
- fewer critical unknowns,
- fewer missing fields,
- stable mode tie-breaker: road, then sea, then air.

This file is where ranking logic belongs.

### `app/services/layer3/agents/analyst_agent.py`

Builds the Analyst prompt and validates the returned `AnalystDraft`.

The Analyst prompt tells the model:

- the deterministic decision is fixed truth,
- do not rerank,
- do not change readiness bands,
- do not invent evidence,
- cite only allowed evidence refs,
- surface uncertainty and blockers,
- avoid final customer prose,
- avoid forbidden claims,
- avoid raw scores.

The agent prefers structured output. If the model/provider does not support it
or returns `None`, it falls back to JSON parsing.

It also strips thinking/reasoning parts before parsing.

### `app/services/layer3/agents/critic_agent.py`

Builds the Critic prompt and validates the returned `CriticReview`.

The Critic checks whether the Analyst draft is safe and faithful.

The Critic is not allowed to:

- rerank,
- change readiness bands,
- write final user output,
- access internal scoring trace,
- decide the final safety result.

### `app/services/layer3/safety_gate.py`

The deterministic Python safety gate.

This is the most important safety enforcement file in Layer 3.

It can block or request revision for violations such as:

- case id mismatch,
- duplicate narrative,
- omitted narrative,
- extra narrative,
- Analyst changed ranking,
- empty evidence,
- evidence outside allowed refs,
- hidden triggered hard gates,
- hidden high/critical unknowns,
- hidden conflicts,
- readiness band contradiction,
- unqualified ready language,
- forbidden claims,
- raw score leakage,
- percentage leakage,
- inconsistent high readiness.

Blocking violations result in `block_unsafe`.

Non-blocking violations usually result in `revise_analyst`, as long as revision
budget remains.

### `app/services/layer3/safety_rules.py`

Shared safety helper functions and forbidden vocabulary.

Contains the forbidden claim vocabulary, including phrases such as:

- approved,
- compliant,
- guaranteed,
- carrier accepted,
- customs cleared,
- booking confirmed,
- final legal clearance,
- final customs clearance,
- final carrier approval.

Also contains raw score leakage detection, including:

- raw score keys,
- internal scoring trace tokens,
- percentage-like outputs.

### `app/services/layer3/routing.py`

Decides the next action after Critic and Safety Gate review.

Priority order:

1. Safety Gate block wins.
2. Safety Gate revise with no revision budget becomes block.
3. Critic block can block when the gate did not pass.
4. Critic revise can trigger revision if budget remains.
5. Safety Gate revise can trigger revision if budget remains.
6. Analyst clarification questions can request user clarification.
7. Analyst refetch requests can request Layer 2 fetch.
8. Safety Gate pass allows pass to Layer 4.
9. Unknown fallback blocks.

### `app/services/layer3/decision_builder.py`

Builds the final `ReasoningDecision`.

Important rules:

- Safety Gate must have passed.
- Critic cannot be in revise/block state.
- Analyst cannot change deterministic rank, mode, path family, or band.
- Raw scores cannot leak.
- Unknown mode cannot appear.
- Every option needs `why_ranked_here` and `why_not_higher`.
- Forbidden claims are allowed only in the explicit `forbidden_claims` list.

This file adapts rich Layer 3 internals into the narrower frozen Layer 4 seam.

### `app/services/layer3/prompt_compaction.py`

Keeps Analyst and Critic prompts small.

It compacts:

- context,
- deterministic decision,
- evidence refs,
- warnings,
- hard gates,
- unknowns,
- missing fields,
- conflicts.

It also filters irrelevant general-cargo unknowns when the active profile is
plain general cargo.

### `app/services/layer3/llm_response.py`

Shared LLM response sanitizer.

It removes or ignores:

- thinking tags,
- reasoning parts,
- code fences,
- thought-only content.

Layer 1 and Layer 4 also reuse some helpers from this file.

### `app/api/v1/routes_layer3.py`

Developer/debug endpoint for Layer 3.

It:

- accepts a `FactPackage`,
- calls `run_layer3`,
- guards the serialized result against raw score and forbidden claim leakage,
- maps runtime errors to HTTP 503,
- maps validation/output-safety errors to HTTP 422.

### `app/schemas/layer3.py`

Defines most internal Layer 3 schemas:

- `Layer3Status`,
- `Layer3NextAction`,
- `CriticVerdict`,
- `SafetyGateStatus`,
- `EvidenceRef`,
- `ReasoningFactor`,
- `ReasoningContext`,
- `RankedPathFamilyDecision`,
- `DeterministicDecision`,
- `AnalystPathNarrative`,
- `AnalystDraft`,
- `CriticFinding`,
- `CriticReview`,
- `SafetyViolation`,
- `SafetyGateReport`,
- `Layer3Result`.

### `app/schemas/reasoning_decision.py`

Defines the frozen contract passed from Layer 3 to Layer 4.

This schema is intentionally user-safe by construction.

It uses bands, labels, warnings, next actions, and claim boundaries. It does not
contain raw numeric scores.

### `app/schemas/internal_scoring_trace.py`

Defines internal numeric scoring diagnostics.

This file is intentionally separate from the public schemas. The internal trace
must not be exported into Layer 4 output.

---

## 7. Context Builder Details

The context builder is the first Layer 3 step.

It takes Layer 2's `FactPackage` and prepares a simpler read model for ranking
and prompting.

It builds a structured request summary from:

- cargo,
- weight,
- lane,
- requested mode,
- candidate modes,
- active profiles,
- dangerous goods flags,
- priority,
- readiness.

It determines candidate modes using this fallback order:

1. concrete request candidate modes,
2. concrete requested mode,
3. concrete modes covered by Layer 2,
4. concrete modes from block responses.

It excludes:

```text
RequestedMode.unknown
```

from candidate modes and modes covered.

It converts hard gates into `ReasoningFactor` objects. Hard gate status is
preserved, including:

- triggered,
- not triggered,
- unknown.

It converts unknowns, missing fields, and conflicts into factors too.

Evidence refs are deterministic and deduplicated. Layer 3 does not invent random
evidence ids.

---

## 8. Deterministic Decision Engine Details

The deterministic engine is the ranking brain.

The engine is designed to be:

- deterministic,
- side-effect free,
- independent from LLMs,
- independent from clocks,
- independent from network calls.

For the same context and trace id, it should produce the same decision and trace.

### Readiness Bands

The engine uses banded readiness:

- `BLOCKED`
- `SPECIALIZED_STUDY_REQUIRED`
- `LOW`
- `MEDIUM_LOW`
- `MEDIUM`
- `HIGH`

These are safer than exposing raw numeric scores.

### Ranking Types

Layer 3 may produce ranking types such as:

- `preparation_ranking`
- `screening_ranking`
- `low_data_ranking`
- `blocked_ranking`
- `booking_ranking`

The current deterministic engine mainly uses:

- `preparation_ranking`
- `low_data_ranking`
- `blocked_ranking`

### Path Families

The engine maps concrete modes to path families:

```text
road -> pure_road_preparation
sea  -> sea_road_preparation
air  -> air_road_preparation
```

This makes multimodal reasoning explicit. Sea and air paths still include road
preparation legs.

### Caps

Every path starts from a high-readiness baseline and is capped downward when
evidence requires it.

Examples:

- triggered blocking gate -> `BLOCKED`
- triggered high/critical gate -> `LOW`
- unknown blocking/high/critical gate -> `SPECIALIZED_STUDY_REQUIRED`
- dangerous goods unresolved unknown -> `LOW`
- lithium battery unresolved unknown -> `LOW`
- blocking missing field -> `SPECIALIZED_STUDY_REQUIRED`
- high-value missing field -> `MEDIUM`
- can-wait missing field -> no cap on clean path
- conflict -> `SPECIALIZED_STUDY_REQUIRED`
- insufficient completeness -> `MEDIUM_LOW`
- incomplete but usable completeness -> `MEDIUM`

### Warnings

The engine generates must-show warnings such as:

- `BLOCKING_HARD_GATE`
- `CRITICAL_UNKNOWN`
- `CONFLICT_PRESENT`
- `LOW_COMPLETENESS`
- `DANGEROUS_GOODS_UNRESOLVED`
- `NOT_FINAL_APPROVAL`

`NOT_FINAL_APPROVAL` is always included because Layer 3 is not final regulatory,
customs, carrier, or booking approval.

---

## 9. Analyst Agent Details

The Analyst is an LLM agent, but it has a narrow job:

> Explain the deterministic decision without changing it.

The Analyst receives:

- compact reasoning context,
- compact deterministic decision,
- allowed evidence refs,
- required narratives,
- ranked path evidence refs,
- optional revision feedback.

The Analyst must return an `AnalystDraft`.

The draft must include exactly one narrative per deterministic ranked path.

The Analyst validation checks:

- case id matches,
- no omitted narratives,
- no extra narratives,
- no duplicate narratives,
- no silent reranking,
- evidence refs are allowed,
- evidence refs are non-empty,
- forbidden claims are not used,
- raw scores are not leaked,
- percentage-like score leakage is not present.

Provider behavior:

- structured output is preferred,
- JSON fallback is supported,
- code fences are stripped,
- hidden reasoning/thought parts are ignored,
- bad fallback output raises controlled errors without exposing raw model output.

---

## 10. Critic Agent Details

The Critic is conditional. It does not always run.

The graph skips Critic for simpler low-risk cases. It runs Critic when risk is
higher or when the Analyst did something suspicious.

The Critic checks the Analyst against:

- the reasoning context,
- the deterministic decision,
- the safety rules.

The Critic can return:

- `pass`,
- `revise`,
- `block`,
- `skipped`.

Revise/block verdicts require findings.

Contradictions cannot pass.

Unsupported claims cannot pass.

The Critic may quote or describe unsafe phrases in findings, so its validation
is slightly different from the Analyst validation. The final Safety Gate still
decides whether the result can proceed.

---

## 11. Safety Gate Details

The Safety Gate is Python, not LLM.

It is authoritative.

It runs after the Analyst and after the Critic path, whether the Critic was
skipped or not.

The Safety Gate checks the draft from scratch against:

- `ReasoningContext`,
- `DeterministicDecision`,
- allowed evidence refs,
- shared forbidden claim rules,
- raw score leakage rules.

### Blocking Violations

Examples of blocking violations:

- wrong case id,
- evidence outside allowed refs,
- hidden triggered serious hard gate,
- hidden conflict,
- readiness contradiction,
- unqualified ready language when readiness is blocked/specialized,
- forbidden claim,
- raw score leakage,
- percentage leakage,
- inconsistent high readiness.

### Revision Violations

Examples of revision-level violations:

- omitted narrative,
- extra narrative,
- duplicate narrative,
- Analyst disputes ranking,
- empty evidence,
- hidden high/critical unknown.

If revision budget remains, Layer 3 loops back to the Analyst with feedback.

If the budget is exhausted, Layer 3 blocks.

---

## 12. Revision Loop

Layer 3 has a bounded revision loop.

Default:

```text
max_revisions = 1
```

This prevents infinite LLM repair loops.

Revision can be triggered by:

- Analyst validation failure,
- Critic revise verdict,
- Safety Gate revise verdict.

When revision happens, the graph increments `revision_count` and sends feedback
back into the Analyst prompt.

If the Analyst still fails after the budget is spent, Layer 3 returns a blocked
result.

---

## 13. Routing Rules

Routing happens in:

```text
app/services/layer3/routing.py
```

The important priority is:

1. Safety Gate block wins.
2. Revision with exhausted budget becomes block.
3. Critic block can block when the gate did not pass.
4. Critic revise can revise if budget remains.
5. Safety Gate revise can revise if budget remains.
6. Analyst clarification request can ask the user.
7. Analyst Layer 2 refetch request can ask Layer 2 for more data.
8. Safety Gate pass allows pass to Layer 4.
9. Anything unclear blocks.

The route design is intentionally conservative.

---

## 14. Decision Builder and the Frozen Seam

After the Safety Gate passes, `decision_builder.py` creates the final
`ReasoningDecision`.

This is the object Layer 4 should trust.

The builder copies deterministic ranking structure and uses Analyst narrative
only for explanatory fields.

It verifies:

- ranks match,
- path family ids match,
- modes match,
- readiness bands match,
- no unknown mode,
- no raw scores,
- forbidden claims appear only in the forbidden list,
- all required explanation fields exist.

The builder intentionally drops some internal details at the seam:

- evidence refs,
- raw scoring trace,
- raw connector block detail,
- internal LLM drafts beyond what is needed.

This prevents Layer 4 from accidentally treating internal diagnostics as public
claims.

---

## 15. Layer 3 and Layer 4 Relationship

Layer 4 receives:

```text
Layer4ReportRequest
```

Defined in:

```text
app/schemas/layer4.py
```

That request includes:

- `Layer3Result`,
- optional `FactPackage`,
- optional `Layer2Summary`,
- optional `OperationalEvidence`.

Layer 4 uses `ReasoningDecision` as the authority for:

- ranking order,
- readiness bands,
- confidence,
- warnings,
- allowed claims,
- forbidden claims.

Layer 4 should not invent a different ranking.

If Layer 3 did not produce a `ReasoningDecision`, Layer 4 must produce a
clarification or blocked-assessment style answer, not a confident final report.

---

## 16. Endpoint Safety Guard

The standalone endpoint in `routes_layer3.py` has an extra output guard.

Before returning the result, it scans the serialized output for:

- raw score tokens,
- internal trace leakage,
- forbidden claims outside the explicit `forbidden_claims` list,
- percentage-style score leakage.

This is a second layer of protection around the developer endpoint.

---

## 17. Agent Run Tracing

Layer 3 records agent runs for observability.

Recorded components include:

- deterministic decision,
- Analyst,
- Critic,
- Safety Gate.

The tests verify that tracing does not store the full prompt by default.

When full prompt tracing is enabled, a prompt artifact reference can be stored
instead.

This protects sensitive prompt content while still making debugging possible.

---

## 18. Prompt Budget and Compaction

Prompt compaction is important because Layer 2 fact packages can be large.

Layer 3 does not send the full `FactPackage` to the Analyst or Critic.

Instead, `prompt_compaction.py` builds compact prompt inputs with limits for:

- text length,
- list length,
- hard gates,
- unknowns,
- missing fields,
- conflicts,
- evidence refs,
- warnings.

Tests verify:

- Analyst prompt stays under budget,
- Critic prompt stays under budget,
- prompts do not include the full fact package,
- unknowns are capped or grouped.

---

## 19. LLM Configuration

Layer 3 agents request the Layer 3 chat model by calling:

```text
get_chat_model(layer3=True)
```

Tests verify:

- Layer 3 model settings are read correctly,
- Layer 3 can use a separate Google model,
- Layer 3 falls back to the base model when needed,
- intake model selection remains separate,
- debug config redacts keys.

This allows Layer 3 to use a different model from intake when desired.

---

## 20. Test Coverage Map

Layer 3 has extensive tests under:

```text
tests/layer3/
```

### `test_context_builder.py`

Verifies:

- minimal context creation,
- candidate mode fallback,
- unknown mode exclusion,
- block status mapping,
- hard gate factor conversion,
- unknown/missing/conflict conversion,
- evidence ref determinism,
- no mutation of `FactPackage`,
- no LLM imports.

### `test_deterministic_decision_engine.py`

Verifies:

- deterministic output,
- trace determinism,
- hard gate caps,
- mode-specific gates,
- global gates,
- unknown caps,
- missing field caps,
- conflict caps,
- low completeness caps,
- stable tiebreaking,
- no unknown modes,
- every ranked path has evidence refs,
- raw scores stay out of the decision,
- raw scores stay inside the trace,
- DG/lithium warnings and caps.

### `test_analyst_agent.py`

Verifies:

- Analyst draft building,
- prompt contents,
- one narrative per path,
- no reranking,
- no extra/omitted narratives,
- evidence subset enforcement,
- no empty evidence,
- structured output path,
- JSON fallback path,
- thinking-part stripping,
- forbidden claim detection,
- raw score and percentage leakage detection,
- no internal scoring trace usage,
- no live LLM call in tests.

### `test_critic_agent.py`

Verifies:

- Critic review building,
- structured output and JSON fallback,
- revise/block finding requirements,
- contradiction handling,
- unsupported claim handling,
- raw score leakage handling,
- no internal scoring trace usage,
- prompt rules,
- no live LLM call in tests.

### `test_safety_gate.py`

Verifies:

- valid draft passes,
- case mismatch blocks,
- omitted/extra/duplicate narratives revise,
- changed rank revises,
- outside evidence blocks,
- empty evidence revises,
- hidden hard gates block,
- hidden high unknowns revise,
- hidden conflicts block,
- forbidden claims block,
- raw scores and percentages block,
- inconsistent high readiness blocks,
- not-triggered gates do not require surfacing,
- inputs are not mutated.

### `test_decision_builder.py`

Verifies:

- final `ReasoningDecision` construction,
- gate must pass,
- critic revise/block rejected,
- rank/order/band preservation,
- required why fields,
- global factors remain visible,
- warning deduplication,
- banded confidence,
- no raw score dump,
- forbidden claim placement,
- no unknown modes,
- deterministic output,
- no evidence refs field in the frozen seam.

### `test_graph.py`

Verifies:

- simple cargo can skip Critic and pass,
- dangerous goods and lithium cases run Critic,
- Analyst disputes trigger Critic,
- revision loop works,
- revision budget blocks,
- safety gate block produces blocked result,
- Critic revise/block interaction,
- no mutation of fact package,
- internal trace ref appears in debug without raw scores,
- trace id is carried through,
- conflicts run Critic,
- terminal nodes produce valid `Layer3Result`,
- end-to-end determinism.

### `test_layer3_endpoint.py`

Verifies:

- endpoint result shape,
- pass and blocked responses,
- endpoint calls `run_layer3` with the provided `FactPackage`,
- error mapping,
- no raw scores in response,
- endpoint does not import or invoke Layer 1 or Layer 2,
- route registration,
- real graph behavior with stub models,
- invalid fact package returns 422.

### `test_layer3_prompt_budget.py`

Verifies prompt size and compaction behavior.

### `test_agent_run_tracing.py`

Verifies Layer 3 run recording and prompt artifact handling.

### `test_llm_config.py`

Verifies Layer 3 model configuration and selection.

### `test_llm_response.py`

Verifies response sanitization for hidden thinking/reasoning content.

### `test_schemas.py`

Verifies schema constraints and ensures `ReasoningDecision` is not redefined in
Layer 3.

---

## 21. Developer Rules

### Rule 1: Do not let the LLM own ranking.

Ranking belongs in:

```text
app/services/layer3/deterministic_decision_engine.py
```

The Analyst can explain. It cannot decide.

### Rule 2: Do not expose raw scores.

Raw scores belong only in:

```text
app/schemas/internal_scoring_trace.py
```

and related internal diagnostics.

They must not appear in:

- `ReasoningDecision`,
- `Layer4ReportRequest`,
- Layer 4 prompt,
- user-facing report,
- endpoint response.

### Rule 3: Do not bypass the Safety Gate.

The Safety Gate is the authoritative final checker before Layer 4.

Even if the Critic passes, the Safety Gate must still run.

### Rule 4: Add new safety vocabulary centrally.

Forbidden claim and raw-score leakage rules belong in:

```text
app/services/layer3/safety_rules.py
```

This keeps Analyst, Critic, Safety Gate, and endpoint guards consistent.

### Rule 5: Add new ranking caps in the deterministic engine.

If a new profile or operational rule should change readiness, implement it in:

```text
app/services/layer3/deterministic_decision_engine.py
```

Then add tests proving:

- the cap applies,
- it is mode-specific if needed,
- warnings are shown,
- confidence is lowered when appropriate,
- raw scores do not leak.

### Rule 6: Keep `ReasoningDecision` stable.

`ReasoningDecision` is the Layer 3 -> Layer 4 seam.

Changing it affects Layer 4 and any external consumer of reasoning output.

Avoid adding internal fields to it. If Layer 4 needs more evidence, prefer a
separate controlled evidence object rather than dumping internals into the seam.

### Rule 7: Keep prompts compact.

Layer 3 should not send the full `FactPackage` to the LLM agents.

Use the compaction helpers.

### Rule 8: Tests should prove safety, not only happy paths.

For any Layer 3 change, consider tests for:

- deterministic behavior,
- no mutation,
- no raw scores,
- no forbidden claims,
- no hidden hard gates,
- no hidden unknowns,
- revision budget behavior,
- endpoint guard behavior,
- prompt size behavior.

---

## 22. Common Failure Modes

### Analyst returns partial narratives

Layer 3 detects omitted required path narratives.

If revision budget remains, the Analyst gets feedback and tries again.

If not, Layer 3 blocks.

### Analyst cites unknown evidence

The Safety Gate blocks when evidence refs are outside the allowed set.

### Analyst says a path is approved

Forbidden claim detection blocks this.

Layer 3 may say a path has a readiness band. It may not claim final approval.

### Analyst leaks scores

Raw score leakage blocks.

This includes explicit raw score tokens and percentage-style score language.

### Critic says revise

If revision budget remains, the Analyst revises.

If budget is exhausted, Layer 3 blocks.

### Safety Gate says block

Safety Gate block wins.

The result does not pass to Layer 4 as a normal confident decision.

### Layer 2 artifact is missing in full graph

The full orchestrator tries to reload Layer 2 from artifact cache before running
Layer 3.

If it cannot reload Layer 2, Layer 3 fails with a graph error.

---

## 23. Mental Model for New Developers

Think of Layer 3 as three stacked protections:

```text
1. Deterministic truth
   Python ranks paths and decides readiness bands.

2. LLM explanation
   Analyst explains the fixed truth.
   Critic may review risky cases.

3. Python safety enforcement
   Safety Gate checks the explanation before Layer 4 sees it.
```

The safest way to work on Layer 3 is to preserve this separation.

Do not move deterministic authority into the Analyst.

Do not move safety authority into the Critic.

Do not move raw score details into the public `ReasoningDecision`.

---

## 24. Layer 3 in One Sentence

Layer 3 turns Layer 2 evidence into a safe, deterministic, LLM-explained,
Python-gated `ReasoningDecision` that Layer 4 can use to write the final report
without inventing rankings, hiding uncertainty, or claiming final approval.
