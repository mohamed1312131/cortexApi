# Layer 3 — Implementation Plan

Cortex Layer 3 = **deterministic decision engine + Analyst Agent + Critic Agent + code safety gate**.
The deterministic engine computes readiness/ranking. The Analyst *explains* the deterministic
result. The Critic *validates* the Analyst. The Python safety gate is **final and authoritative**.

## Locked decisions

- **Standalone `run_layer3` first** — build/test Layer 3 in isolation.
- **Orchestrator cutover later** — wiring into `/api/v1/cortex/message` is a separate, deliberate PR. Layer 1 / Layer 2 stay frozen.
- **Stay pinned** on current versions: `langgraph==0.2.60`, `langchain==0.3.13`, `langchain-openai`, `langchain-google-genai`, `pydantic 2.10.x`. No `create_agent` (that is LangChain v1).
- **Structured output where supported + JSON fallback where needed** — `model.with_structured_output(Schema)` for OpenAI; the Layer‑1 prompt + JSON‑parse + `model_validate` fallback for Gemma/Gemini.
- **Deterministic engine before agents** — the engine is the ranking truth; agents never re-rank.
- **`ReasoningDecision` is the frozen external seam** to Layer 4. Never redefined. Contains **no raw scores** by construction.
- **`InternalScoringTrace` is internal only** — raw numerics live here, linked by `case_id` + `reasoning_decision_id`. It must never be embedded in `ReasoningDecision` and never cross to Layer 4.

## Frozen contracts Layer 3 must not break

- `ValidatedShipmentRequest` — `app/schemas/shipment_request.py`
- `FactPackage` (+ `BlockResponse`, `HardGate`, `Unknown`) — `app/schemas/fact_package.py`, `app/schemas/block_response.py` (Layer 2 → 3 input)
- `ReasoningDecision` — `app/schemas/reasoning_decision.py` (Layer 3 → 4 output; **do not redefine**)
- System invariant: concrete `mode` in gates/options is sea/air/road, never `unknown` (`_reject_unknown_mode`).

## Target architecture (build order)

1. **Schemas** (this step) — context / decision / draft / review / gate / envelope + internal scoring trace.
2. `prepare_reasoning_context` — pure FactPackage → `ReasoningContext` read-model.
3. `deterministic_decision_engine` — pure, no LLM, no randomness; emits `DeterministicDecision` + `InternalScoringTrace`.
4. `analyst_agent` — LLM structured output; explains only; `disputes_ranking` signal.
5. `critic_agent` — LLM structured output; conditional in v1; validates the draft.
6. `claim_check_gate` — pure Python; authoritative even if the Critic passed.
7. `route_after_review` + bounded revision loop (`MAX_REVISIONS`).
8. `build_reasoning_decision` — sole producer of the frozen `ReasoningDecision` (numerics stripped).

## Schema layout

- `app/schemas/internal_scoring_trace.py` — `ScoringStep`, `InternalScoringTrace` (internal only, **not** publicly exported).
- `app/schemas/layer3.py` — `Layer3Status`, `Layer3NextAction`, `CriticVerdict`, `SafetyGateStatus`, `EvidenceRef`, `ReasoningFactor`, `ReasoningContext`, `RankedPathFamilyDecision`, `DeterministicDecision`, `AnalystPathNarrative`, `AnalystDraft`, `CriticFinding`, `CriticReview`, `SafetyViolation`, `SafetyGateReport`, `Layer3Result`.
- `app/services/layer3/state.py` — `Layer3State` (`TypedDict, total=False`).

Only `Layer3Result` (and the already-exported `ReasoningDecision`) belong in the public
`app/schemas/__init__.py`. Internal drafts/decisions/traces stay out of the public namespace.

## Deferred (not in scope)

Context builder, decision engine, agents, safety gate, graph, API endpoint, orchestrator
integration, Layer 4, vector DB / memory.
