# Cortex Layer 1 Intake Architecture

## Summary

Layer 1 is the conversational intake layer for Cortex. Its job is to turn a
user's freight message into a structured `ValidatedShipmentRequest`, decide what
is still missing, and decide whether the case is ready to move to the
deterministic data-checking step in Layer 2.

Layer 1 does not produce a final logistics decision, booking approval,
compliance approval, quote, route, or report. It only prepares and validates the
shipment request enough for later layers to work safely.

The most important implementation rule is:

```text
Layer 1 = one LLM intake agent turn + mechanical Python plumbing
```

The LLM agent owns language understanding and intake policy. Python around the
agent owns persistence, schema validation, diffing, rerun metadata, sanitization,
and API boundaries.

## Main Files

| File | Responsibility |
| --- | --- |
| `app/services/layer1/intake_agent.py` | The single Layer 1 LLM agent, prompt, model call, output parsing, retry, and schema validation. |
| `app/services/layer1/graph.py` | Layer 1 service entry point. Loads case state, runs the agent, computes deterministic fields, diffs changes, builds rerun scope, persists state, and returns `IntakeResult`. |
| `app/services/layer1/case_state_manager.py` | Case-state persistence in Redis with an optional in-memory development fallback. |
| `app/services/layer1/conversation_lock.py` | Per-conversation serialization guard to prevent concurrent messages from clobbering the same case state. |
| `app/services/layer1/response_sanitizer.py` | Cleans user-facing text returned by the agent without changing machine fields. |
| `app/schemas/intake.py` | Layer 1 API and state schemas such as `IntakeResult`, `CaseState`, `CaseAction`, `IntakeIntent`, and `IntakeDecision`. |
| `app/schemas/shipment_request.py` | The frozen Layer 1 -> Layer 2 shipment request contract: `ValidatedShipmentRequest`. |
| `app/api/v1/routes_intake.py` | `/api/v1/intake/message`, the Layer 1-only endpoint. |
| `app/api/v1/routes_cortex.py` | `/api/v1/cortex/message` and `/api/v1/cortex/full-message`, which start from Layer 1 and may continue to later layers. |
| `app/services/orchestrator/cortex_orchestrator.py` | Contains the defensive Layer 1 -> Layer 2 gate `_is_safe_for_layer_2`. |

## Endpoint Behavior

### `/api/v1/intake/message`

This endpoint runs Layer 1 only.

```text
User message
-> conversation_guard
-> handle_intake_message
-> sanitize_intake_result
-> IntakeResult
```

It never calls Layer 2. It is useful when the product only needs to continue the
intake conversation, ask clarification questions, or inspect the structured
shipment request produced by Layer 1.

### `/api/v1/cortex/message`

This endpoint runs Layer 1, then conditionally runs Layer 2.

```text
User message
-> conversation_guard
-> handle_cortex_message
-> Layer 1 IntakeResult
-> _is_safe_for_layer_2
-> if safe: build_fact_package_for_request
-> CortexOrchestratorResult
```

If Layer 1 is not safe for Layer 2, the response has `layer2 = null` and
`next_action = ASK_USER`.

If Layer 1 is safe for Layer 2, the orchestrator builds a Layer 2 `FactPackage`
and returns `next_action = SHOW_FACT_PACKAGE`.

### `/api/v1/cortex/full-message`

This endpoint starts the full orchestration path.

```text
Layer 1 -> Layer 2 -> Layer 3 -> Layer 4
```

Layer 1 is still the first gate. If Layer 1 needs more user input, the full graph
routes to `ASK_USER` instead of continuing.

## Runtime Flow Inside Layer 1

The main service is `Layer1AgentIntake.handle_message`.

```text
handle_message
-> load existing case by case_id or active conversation
-> create a working case id when needed
-> run_intake_agent
-> choose case lifecycle action
-> inject the real case_id into the intake object
-> derive ready_for_layer_2 from the decision
-> compute intake_quality_score deterministically
-> diff previous request vs current request
-> build rerun_scope from changed fields
-> append user message to conversation_summary
-> bump shipment_request_version when facts changed
-> persist CaseState
-> build IntakeResult
-> sanitize user-facing text
```

The service deliberately does not interpret natural language. It does not use
keyword matching, regex extraction, or post-hoc rewriting of facts. That policy
lives in the agent prompt and the model output contract.

## The Intake Agent

The intake agent lives in `app/services/layer1/intake_agent.py`.

It owns the complete intake turn:

- Understand the user's message in any language.
- Merge the new message with the previous shipment request.
- Extract shipment facts.
- Normalize stated geography where safe.
- Activate cargo profiles.
- Detect blocking gaps.
- Detect high-value and can-wait gaps.
- Ask up to three blocking-field questions.
- Decide whether the case can proceed.
- Reply to the user in the user's language.

The agent must return one JSON object matching `AgentTurn`:

```text
{
  "case_action": "...",
  "intent": "...",
  "decision": "...",
  "assistant_message": "...",
  "intake": { ... full ValidatedShipmentRequest ... }
}
```

The output is parsed, stripped of markdown fences if needed, and validated by
Pydantic. If the model returns invalid JSON or fails schema validation, Layer 1
retries once with the validation error included in the prompt. If the second
attempt fails, `IntakeAgentError` is raised.

## Agent Responsibilities vs Python Responsibilities

| Area | Owner |
| --- | --- |
| Language understanding | Agent |
| Extraction from user text | Agent |
| Merging with previous case facts | Agent |
| Profile activation | Agent |
| Missing-field triage | Agent |
| Readiness decision | Agent chooses `decision`; Python derives the boolean |
| User-facing reply | Agent, then sanitizer cleans spacing issues |
| Case id authority | Python |
| Schema validation | Python/Pydantic |
| `ready_for_layer_2` boolean | Python |
| `intake_quality_score` | Python |
| Changed-field diff | Python |
| Rerun scope | Python |
| Case persistence | Python |
| Concurrency guard | Python |
| Defensive Layer 2 gate | Orchestrator/Python |

This boundary is intentional. A past multi-node deterministic Layer 1 pipeline
was replaced by the single-agent design, so new code should not reintroduce
parallel Python NLP extractors or validators that second-guess the agent's
language interpretation.

## Core Contracts

### `IntakeMessageRequest`

The request body used by Layer 1 endpoints.

Fields:

- `conversation_id`
- `case_id`
- `user_id`
- `company_id`
- `message`

`conversation_id` lets the system continue an active case across multiple
messages. `case_id` can be supplied to target a specific known case.

### `IntakeResult`

The boundary-visible Layer 1 result.

Important fields:

- `conversation_id`
- `case_id`
- `case_action`
- `intent`
- `decision`
- `assistant_message`
- `intake_json`
- `ready_for_layer_2`
- `requires_layer_2_rerun`
- `changed_fields`
- `rerun_scope`
- `questions_to_user`

This object is returned directly by `/api/v1/intake/message` and embedded inside
the orchestrator responses from `/api/v1/cortex/message` and
`/api/v1/cortex/full-message`.

### `ValidatedShipmentRequest`

This is the frozen Layer 1 -> Layer 2 contract.

Top-level groups:

- `user_goal`
- `core_shipment`
- `lane`
- `mode`
- `cargo_flags`
- `active_profiles`
- `profiles`
- `commercial`
- `facts_from_user`
- `inferred_flags`
- `missing_fields`
- `questions_to_user`
- `ready_for_layer_2`
- `field_confidence`
- `intake_quality_score`

Layer 2 receives this object as input. Layer 1 should not casually change this
shape because it is the seam between intake and deterministic fact building.

## Readiness Decisions

The agent emits an `IntakeDecision`.

Ready decisions are:

- `ready_for_layer_2`
- `ready_for_layer_2_with_unknowns`
- `update_case_and_rerun`

Python derives `ready_for_layer_2 = true` only for those decisions.

Non-ready or non-forwarding decisions include:

- `ask_user`
- `answer_user_explanation`
- `start_new_case`

The readiness boolean is not trusted from the model. Even if the model outputs
`ready_for_layer_2`, Python overwrites it from the decision so the two cannot
drift.

## Missing Field Tiers

Layer 1 uses three missing-field tiers.

| Tier | Meaning | Can Layer 2 run? |
| --- | --- | --- |
| `blocking` | Required before any safe Layer 2 fact build. | No |
| `high_value` | Useful for better downstream checks, but not mandatory. | Yes |
| `can_wait` | Helpful commercial or preference details that can be collected later. | Yes |

Examples of blocking gaps from the agent prompt include:

- missing cargo description
- missing weight or quantity
- missing origin or destination
- dangerous goods without a valid UN number or classification
- lithium battery shipment without origin and destination cities
- oversized cargo without dimensions
- live animals without species
- contradictions the user must resolve
- multiple distinct shipments in one message

When blocking gaps exist, the agent should set `decision = ask_user` and include
questions for the user. Questions are limited to blocking fields and capped at
three.

## Defensive Layer 2 Gate

Layer 1 owns the readiness decision, but the orchestrator still protects the
Layer 1 -> Layer 2 seam with `_is_safe_for_layer_2`.

Layer 2 may run only when:

```text
intake_json is not None
and layer1.ready_for_layer_2 is true
and intake_json.missing_fields.blocking is empty
```

`high_value` and `can_wait` gaps are allowed.

This means an inconsistent or adversarial agent output cannot reach Layer 2 if
blocking gaps remain.

## Case State

Layer 1 persists conversation state as `CaseState`.

Important fields:

- `case_id`
- `conversation_id`
- `user_id`
- `company_id`
- `status`
- `shipment_request_version`
- `current_shipment_request`
- `conversation_summary`
- `last_missing_questions`
- `active_profiles`
- `created_at`
- `updated_at`

State is saved in Redis by `RedisCaseStateStore`.

Redis keys:

```text
cortex:layer1:case:{case_id}
cortex:layer1:conversation:{conversation_id}:active_case
```

The TTL is one day.

There is an in-memory fallback for local development. This fallback is
per-process and is not safe for multi-worker production because different
workers will not share the same conversation state.

Production should set:

```text
CORTEX_REDIS_FALLBACK_ENABLED=false
```

With fallback disabled, Redis failures raise `RedisFallbackDisabledError` instead
of silently degrading to unsafe per-process memory.

## Case Lifecycle

Layer 1 either loads an existing case or creates a new one.

Case selection order:

```text
explicit case_id
-> active case for conversation_id
-> new SHIP-XXXXXXXX case id
```

If the agent returns `case_action = start_new_case` while an existing case is
active, Python mints a fresh case id and starts a new `CaseState`.

The case status is derived from the decision:

| Condition | Status |
| --- | --- |
| `decision = ask_user` | `waiting_for_user_clarification` |
| ready decision | `ready_for_layer_2` |
| intake question / report filtering actions | keep current status |
| otherwise | `intake_in_progress` |

`shipment_request_version` increments when changed fields exist or when the case
gets its first shipment request.

## Changed Fields

Layer 1 mechanically diffs the previous and current `ValidatedShipmentRequest`.

The diff checks:

- `core_shipment`
- `lane`
- `commercial`
- `cargo_flags`
- `mode.requested_mode`
- `mode.candidate_modes`
- `active_profiles`
- nested `profiles`

The result is a list such as:

```text
core_shipment.weight_kg
lane.origin_city
profiles.dangerous_goods.un_number
active_profiles
```

This diff is structural. It does not interpret language and does not decide
whether the new facts are correct.

## Rerun Scope

`_build_rerun_scope` maps changed fields to advisory Layer 2 scope names.

Examples:

| Changed field | Example scope |
| --- | --- |
| `lane.*` | `node_resolution`, `mode_specific_readiness`, `documents_border_rules`, `confidence_completeness` |
| `profiles.dangerous_goods.*` | `dg_checks`, `mode_specific_readiness`, `documents_border_rules`, `confidence_completeness` |
| `profiles.lithium_battery.*` | `dg_checks`, `air_readiness`, `documents_border_rules`, `confidence_completeness` |
| `core_shipment.*` | `container_fit`, `chargeable_weight`, `vehicle_fit`, `cost_planning`, `mode_specific_readiness`, `confidence_completeness` |
| `commercial.*` | `documents`, `cost_planning`, `timing_planning`, `schedule_readiness`, `confidence_completeness` |
| `mode.*` | `mode_selection`, `mode_specific_readiness`, `confidence_completeness` |

In v1, this metadata is advisory. The `/api/v1/cortex/message` path still builds
a full Layer 2 fact package when the request is safe. Partial Layer 2 reruns are
not wired into the product path.

## Conversation Locking

Layer 1 uses `conversation_guard` at the route/service boundary.

Purpose:

```text
Two messages for the same conversation_id must not interleave through
load -> agent -> persist, or one turn can overwrite the other.
```

Behavior:

- Same `conversation_id`: serialized.
- Different `conversation_id` values: concurrent.
- Missing `conversation_id`: no-op guard.
- Local/dev mode: in-process `threading.Lock`.
- Production mode: Redis lock when Redis fallback is disabled.

The route handlers run blocking Layer 1 work inside `asyncio.to_thread`, so the
guard is a synchronous context manager.

## Sanitization

The response sanitizer fixes common spacing and formatting issues in
user-facing strings. It touches:

- `assistant_message`
- `missing_fields`
- question text
- question reasons

It deliberately preserves machine fields such as:

- `changed_fields`
- `rerun_scope.changed_fields`
- `QuestionToUser.field_target`
- technical tokens like UN numbers, country codes, and incoterms

This keeps the product output cleaner without corrupting contract fields used by
code.

## Cargo Profiles

The agent can activate multiple profiles.

Important profiles include:

- `dangerous_goods`
- `lithium_battery`
- `pharma`
- `temperature_controlled`
- `food_perishable`
- `oversized`
- `high_value`
- `live_animals`
- `vehicle`
- `liquid_bulk`
- `dry_bulk`
- `containerized`
- `general_cargo`

`general_cargo` should only be active when a cargo description exists and no
more specific profile applies.

Some profiles imply other profiles or flags. For example:

- lithium batteries also activate dangerous goods
- pharma can activate temperature controlled
- food perishable can activate temperature controlled

The agent records explicit user facts in `facts_from_user` and inferred facts in
`inferred_flags`.

## Prompt Budget

Layer 1 has a prompt budget test:

```text
tests/layer1/test_prompt_budget.py
```

The prompt produced by `_turn_payload` is expected to stay under the configured
budget. If the prompt grows, update it carefully and keep the test meaningful.

## Observability

Layer 1 logs a coarse outcome through `log_layer1_outcome`.

The log includes:

- `trace_id`
- endpoint
- `case_id`
- `case_action`
- `intent`
- `ready_for_layer_2`
- changed-field count
- blocking-field count
- blocking-field names

Prompt size is logged by `log_prompt_size` when the agent turn is built.

The logs are intentionally boundary-level. They do not modify routing or
extraction behavior.

## Error Behavior

Common failures:

| Failure | Result |
| --- | --- |
| No configured LLM model | `IntakeAgentError` |
| Model returns invalid JSON twice | `IntakeAgentError` |
| Model output is missing `intake` | `IntakeAgentError` |
| Pydantic schema validation fails twice | `IntakeAgentError` |
| Redis unavailable with fallback disabled | `RedisFallbackDisabledError` |

API routes map runtime failures to controlled HTTP errors:

- `RuntimeError` -> HTTP 503
- `ValueError` -> HTTP 422
- other exceptions -> HTTP 503

## Test Map

| Test file | What it protects |
| --- | --- |
| `tests/layer1/test_intake_agent.py` | Offline tests for JSON parsing, retry, case-id injection, persistence, changed fields, rerun scope, lifecycle, and status. |
| `tests/layer1/test_intake_agent_live.py` | Live LLM behavior for extraction, geography normalization, profile activation, and negation handling. |
| `tests/layer1/test_concurrency_hardening.py` | Per-conversation locking, Redis fallback behavior, and runtime settings. |
| `tests/layer1/test_response_sanitizer.py` | User-facing text cleanup while preserving machine fields. |
| `tests/layer1/test_prompt_budget.py` | Layer 1 prompt budget. |
| `tests/orchestrator/test_cortex_seam.py` | Layer 1 -> Layer 2 seam, including "ask user" blocking, ready flow, multi-shipment blocker, and adversarial ready-with-blocking protection. |
| `tests/orchestrator/test_cortex_sanitization.py` | Sanitization parity for embedded `layer1` responses from cortex endpoints. |
| `tests/orchestrator/test_observability.py` | Layer 1 outcome logging and confirmation that the old multi-node Layer 1 pipeline remains removed. |

## Developer Rules

- Do not add Python keyword or regex NLP back into Layer 1.
- Do not rewrite model facts after the agent returns them.
- Do not trust the model's `ready_for_layer_2` boolean; Python derives it.
- Do not trust the model's `intake_quality_score`; Python computes it.
- Do not let Layer 2 run while `missing_fields.blocking` is non-empty.
- Do not use in-memory state fallback in production multi-worker deployments.
- Do not casually change `ValidatedShipmentRequest`; it is the frozen Layer 1 ->
  Layer 2 seam.
- Keep questions focused on blocking fields.
- Keep high-value and can-wait gaps non-blocking.
- Keep `/api/v1/intake/message` Layer 1-only.

## Minimal Example Flow

First user message:

```text
Ship lithium batteries to Germany.
```

Likely Layer 1 result:

```text
decision = ask_user
ready_for_layer_2 = false
missing_fields.blocking = [
  "weight or quantity",
  "origin and destination",
  "valid UN number or dangerous-goods classification"
]
questions_to_user = [...]
```

Layer 2 does not run.

Follow-up user message:

```text
It is UN3480, 8000 kg, Shenzhen to Frankfurt by air.
```

Likely Layer 1 result:

```text
decision = update_case_and_rerun
ready_for_layer_2 = true
missing_fields.blocking = []
changed_fields = [
  "core_shipment.weight_kg",
  "lane.origin_city",
  "lane.destination_city",
  "profiles.dangerous_goods.un_number"
]
```

The orchestrator gate allows Layer 2 to build a `FactPackage`.

