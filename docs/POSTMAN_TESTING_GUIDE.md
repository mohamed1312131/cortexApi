# Cortex Platform — Postman Testing Guide (Layers 1 → 2 → 3)

This guide gives you **10 ready-to-send Postman requests** with expected responses,
covering the full flow:

```
POST /api/v1/intake/message    Layer 1 only   (extract + validate + decide)
POST /api/v1/cortex/message    Layer 1 + 2    (if ready -> builds the FactPackage)
POST /api/v1/layer3/reason     Layer 3 only   (FactPackage -> Analyst + Critic + Safety Gate)
```

Base URL: `http://localhost:8000` — all requests are `POST` with header
`Content-Type: application/json`.

> **Required LLM config (2026-06-12).** `gemma-4-31b-it` currently returns HTTP 500
> from the Google API for every request, and `gemma-4-26b-a4b-it` cannot drive the
> Layer 3 Analyst (thought-only responses). Working `.env`:
> ```
> GOOGLE_AI_MODEL=gemma-4-26b-a4b-it
> GOOGLE_AI_INTAKE_MODEL=gemma-4-26b-a4b-it
> GOOGLE_AI_LAYER3_MODEL=gemini-2.5-flash
> GOOGLE_AI_LAYER4_MODEL=gemini-2.5-flash
> INTAKE_MAX_OUTPUT_TOKENS=2048
> LAYER3_MAX_OUTPUT_TOKENS=4096
> LAYER4_MAX_OUTPUT_TOKENS=4096
> GOOGLE_AI_LAYER4_THINKING_BUDGET=0
> ```
> After editing `.env`: `docker compose up -d api`. Sanity check: `GET /health` → `{"status":"alive"}`.

> A ready-to-import collection is at `docs/postman/cortex_postman_collection.json`.
> It auto-saves `case_id` and the `layer2` FactPackage into collection variables,
> so the Layer 3 requests are pre-wired (no manual copy-paste).

---

## How the chain works (read this first)

1. **Intake / Cortex request body** (same schema for both):
   ```json
   {
     "conversation_id": "<any string you choose>",
     "case_id": "<omit on first message; reuse the returned one for follow-ups>",
     "user_id": "tester",
     "message": "<the shipment request text>"
   }
   ```
2. Layer 1 needs these **blocking fields** before it lets Layer 2 run:
   cargo description, weight **or** quantity, origin + destination.
   Profile extras: dangerous goods → a UN number; lithium batteries → origin **city**
   and destination **city**; oversized → dimensions; live animals → species.
3. `/api/v1/cortex/message` returns `next_action`:
   - `ASK_USER` → Layer 2 did **not** run (`layer2: null`), answer the questions and resend.
   - `SHOW_FACT_PACKAGE` → `layer2` holds the full **FactPackage**.
4. **Layer 3**: copy the **entire `layer2` object** (not the whole cortex response!)
   and send it as the raw body of `POST /api/v1/layer3/reason`.
5. The **Critic agent only runs when something is worth criticizing**. Triggers
   (`app/services/layer3/graph.py::_should_run_critic`):
   - `dangerous_goods` / `lithium_battery` profile is active, or
   - overall readiness band is `HIGH`, or
   - a triggered hard gate with severity blocking/high/critical, or
   - any high/critical unknown, or any conflict, or the Analyst disputes the ranking.
   Otherwise the Critic is skipped: `critic_review.verdict = "skipped"` and
   `debug.critic_verdict = "skipped"`.

> **LLM variability:** Layer 1 extraction and the Layer 3 Analyst/Critic are LLM
> calls (Google `gemma`). Exact wording of `assistant_message`, questions and
> narratives changes between runs. The **structural fields** asserted below
> (decisions, gates, block ids, statuses, bands) are deterministic.

---

## Request 1 — Intake: incomplete lithium request → `ask_user`

`POST http://localhost:8000/api/v1/intake/message`

```json
{
  "conversation_id": "pm-conv-01",
  "user_id": "tester",
  "message": "I need to ship lithium batteries from China to France."
}
```

**Expected response (key fields):**

```json
{
  "case_id": "SHIP-XXXXXXXX",
  "case_action": "create_new_case",
  "intent": "shipment_readiness",
  "decision": "ask_user",
  "ready_for_layer_2": false,
  "intake_json": {
    "active_profiles": ["dangerous_goods", "lithium_battery"],
    "missing_fields": {
      "blocking": [
        "weight or quantity",
        "valid UN number or dangerous-goods classification",
        "origin city",
        "destination city"
      ]
    }
  },
  "questions_to_user": [ "...one question per blocking gap..." ]
}
```

**Check:** `decision = ask_user`, `ready_for_layer_2 = false`, the two profiles are
active, blocking list contains weight + UN number + origin/destination **city**
(cities are blocking *because* it's lithium). **Save the `case_id`** for Request 2.

---

## Request 2 — Intake: follow-up answers → ready with unknowns

`POST http://localhost:8000/api/v1/intake/message` — **same conversation_id, case_id from R1**

```json
{
  "conversation_id": "pm-conv-01",
  "case_id": "<case_id from Request 1>",
  "user_id": "tester",
  "message": "It is UN3480 lithium-ion batteries, 8000 kg, from Shenzhen to Lyon."
}
```

**Expected response (key fields):**

```json
{
  "case_id": "<same case_id>",
  "case_action": "answer_intake_question",
  "decision": "ready_for_layer_2_with_unknowns",
  "ready_for_layer_2": true,
  "intake_json": {
    "core_shipment": { "weight_kg": 8000 },
    "lane": { "origin_city": "Shenzhen", "destination_city": "Lyon",
              "origin_country": "CN", "destination_country": "FR" },
    "profiles": { "dangerous_goods": { "un_number": "UN3480" } },
    "missing_fields": {
      "blocking": [],
      "high_value": ["battery packing configuration", "state of charge for air",
                     "UN38.3 availability", "..."]
    }
  }
}
```

**Check:** blocking is now **empty**, decision flips to
`ready_for_layer_2_with_unknowns` (high-value gaps like state-of-charge and UN38.3
remain), `ready_for_layer_2 = true`. The case is updated, not recreated.

---

## Request 3 — Cortex: clean road shipment → FactPackage

`POST http://localhost:8000/api/v1/cortex/message`

```json
{
  "conversation_id": "pm-conv-03",
  "user_id": "tester",
  "message": "Ship 500 kg of textiles from Milan to Paris by road. About 2 cbm, ready next Monday."
}
```

**Expected response (verified live):**

```json
{
  "case_id": "SHIP-XXXXXXXX",
  "next_action": "SHOW_FACT_PACKAGE",
  "layer1": {
    "decision": "ready_for_layer_2_with_unknowns",
    "ready_for_layer_2": true,
    "intent": "shipment_readiness",
    "case_action": "create_new_case"
  },
  "layer2": {
    "block_responses": [ "ROAD-C", "ROAD-A", "ROAD-B", "ROAD-F", "ROAD-COST" ],
    "completeness": { "status": "incomplete_but_usable" },
    "derived_rollup": {
      "modes_covered": ["road"],
      "hard_gates": [],
      "unknowns": "≈ 20+ informational unknowns"
    }
  },
  "debug": { "layer2_ran": true }
}
```

**Check:** `next_action = SHOW_FACT_PACKAGE`, 5 ROAD blocks called, **0 hard gates**
(Milan→Paris is a liberalized EU corridor in `data/road/road_c_corridor_viability.json`).
**Copy the whole `layer2` object** → body of Request 4.

---

## Request 4 — Layer 3 on the clean road package → Critic skipped

`POST http://localhost:8000/api/v1/layer3/reason`
Body = the **`layer2` object from Request 3** (raw JSON).

**Expected response (verified live):**

```json
{
  "case_id": "<same case_id>",
  "status": "pass_to_layer4",
  "reasoning_decision": {
    "ranking_type": "preparation_ranking",
    "ranked_readiness_options": [
      { "rank": 1, "path_family_id": "road_preparation", "mode": "road",
        "readiness_band": "MEDIUM", "why_ranked_here": "..." }
    ],
    "confidence": { "band": "MEDIUM",
                    "cap_reasons": ["completeness:incomplete_but_usable", "..."] },
    "allowed_claims": ["..."],
    "forbidden_claims": ["approved", "compliant", "guaranteed", "..."],
    "global_next_actions": ["Resolve missing field: incoterm", "..."],
    "must_show_warnings": [
      { "code": "NOT_FINAL_APPROVAL",
        "message": "This is a preparation-readiness assessment, not final legal, customs, or carrier approval." }
    ]
  },
  "analyst_draft": {
    "narratives": [
      { "path_family": "road_preparation", "mode": "road", "rank": 1,
        "why_ranked_here": "...", "why_not_higher": "...",
        "what_would_improve_readiness": ["Provide core_shipment.dimensions", "..."],
        "evidence_refs": ["..."] }
    ],
    "overall_summary": "..."
  },
  "critic_review": { "verdict": "skipped" },
  "safety_gate_report": { "status": "pass", "passed": true, "next_action": "pass_to_layer4" },
  "debug": { "route": "pass_to_layer4", "revision_count": 0,
             "critic_verdict": "skipped", "overall_readiness_band": "MEDIUM",
             "ranking_type": "preparation_ranking" }
}
```

**Check:** `analyst_draft.narratives` has one entry per ranked option with
`evidence_refs`; `critic_review.verdict = "skipped"` (nothing triggered it — this
is the **negative control** for the Critic); the safety gate still ran and passed.
`must_show_warnings` always carries `NOT_FINAL_APPROVAL`. No raw numeric scores
appear anywhere in the response (leak guard).

---

## Request 5 — Cortex: UN3480 air lithium → FactPackage with DG facts

`POST http://localhost:8000/api/v1/cortex/message`

```json
{
  "conversation_id": "pm-conv-05",
  "user_id": "tester",
  "message": "Ship 2000 kg of lithium-ion batteries UN3480 from Shenzhen to Frankfurt by air. State of charge is 30 percent and the UN38.3 test report is available."
}
```

**Expected response (key fields):**

```json
{
  "next_action": "SHOW_FACT_PACKAGE",
  "layer1": {
    "decision": "ready_for_layer_2_with_unknowns",
    "intake_json": { "active_profiles": ["dangerous_goods", "lithium_battery"] }
  },
  "layer2": {
    "block_responses": [ "AIR-A (DG acceptance), AIR-B, AIR-C, AIR-D, AIR-E, AIR-F, AIR-H, AIR-I, AIR-REF" ],
    "derived_rollup": {
      "modes_covered": ["air"],
      "hard_gates": [ "UN3480: passenger aircraft FORBIDDEN / cargo-aircraft-only" ]
    }
  }
}
```

**Check:** AIR blocks are called; the rollup carries the UN3480 facts from
`data/air/cortex_air_block_a_dg_records_REPAIRED.json` — passenger aircraft
**Forbidden**, cargo aircraft only 35 kg per package → expect a triggered DG /
CAO hard gate. **Copy `layer2`** → Request 6.

---

## Request 6 — Layer 3 on the DG package → **Critic runs**

`POST http://localhost:8000/api/v1/layer3/reason`
Body = the **`layer2` object from Request 5**.

**Expected response (key fields):**

```json
{
  "status": "pass_to_layer4 | request_user_clarification",
  "reasoning_decision": {
    "ranked_readiness_options": [ { "mode": "air", "readiness_band": "LOW | MEDIUM_LOW (DG-capped)" } ],
    "confidence": { "band": "...", "cap_reasons": ["...DG / missing UN38.3 caps..."] },
    "must_show_warnings": [ "...NOT_FINAL_APPROVAL + DG / cargo-aircraft-only warnings..." ]
  },
  "analyst_draft": { "narratives": ["..."] },
  "critic_review": {
    "verdict": "pass | revise",
    "findings": ["...present when verdict=revise..."],
    "contradiction_with_deterministic_ranking": false
  },
  "safety_gate_report": { "status": "pass", "passed": true }
}
```

**Check:** **`critic_review.verdict` is NOT `"skipped"`** — the lithium/DG profile
always wakes the Critic. If the Analyst hid a hard gate or over-claimed, verdict =
`revise` with `findings[]` + `required_changes[]`, and the graph loops the Analyst
once (`debug.revision_count = 1`). `must_show_warnings` must mention the DG restriction.

---

## Request 7 — Cortex: road with ECMT permit hard gate

`POST http://localhost:8000/api/v1/cortex/message`

```json
{
  "conversation_id": "pm-conv-07",
  "user_id": "tester",
  "message": "Ship 3000 kg of textile rolls from Istanbul to Berlin by road, about 12 cbm."
}
```

**Expected response (key fields):**

```json
{
  "next_action": "SHOW_FACT_PACKAGE",
  "layer2": {
    "derived_rollup": {
      "modes_covered": ["road"],
      "hard_gates": [
        { "rule": "TR→DE corridor: ecmt_permit_required", "status": "triggered" }
      ]
    }
  }
}
```

**Check:** Turkey→Germany road exists in the corridor data as
`ecmt_permit_required` with `hard_gate: true` — the FactPackage must surface it
(unlike Milan→Paris in R3). **Copy `layer2`** → Request 8.

---

## Request 8 — Layer 3 on the hard-gated package → Critic + capped band

`POST http://localhost:8000/api/v1/layer3/reason`
Body = the **`layer2` object from Request 7**.

**Expected response (key fields):**

```json
{
  "status": "pass_to_layer4 | request_user_clarification",
  "reasoning_decision": {
    "ranked_readiness_options": [
      { "rank": 1, "mode": "road", "readiness_band": "LOW | MEDIUM_LOW (gate-capped)" }
    ],
    "confidence": { "cap_reasons": ["...corridor / permit caps..."] },
    "must_show_warnings": [ "...permit requirement..." ]
  },
  "critic_review": { "verdict": "pass | revise" },
  "safety_gate_report": { "status": "pass" }
}
```

**Check:** the triggered serious gate forces the **Critic to run**; the readiness
band is **capped** versus R4's clean road case; `blocking_factors` /
`must_show_warnings` carry the permit gate. Compare R4 vs R8 side by side — same
mode, different evidence → different band. That comparison is the Layer 3 demo.

---

## Request 9 — Cortex: cargo/UN conflict → Layer 2 refused

`POST http://localhost:8000/api/v1/cortex/message`

```json
{
  "conversation_id": "pm-conv-09",
  "user_id": "tester",
  "message": "Ship perfume UN3480 from Grasse to Dubai, 500 kg."
}
```

**Expected response (key fields):**

```json
{
  "next_action": "ASK_USER",
  "layer1": {
    "decision": "ask_user",
    "ready_for_layer_2": false,
    "intake_json": {
      "missing_fields": { "blocking": ["...cargo/UN conflict resolution..."] }
    }
  },
  "layer2": null,
  "debug": { "layer2_ran": false }
}
```

**Check:** UN3480 is *lithium-ion batteries*, but the cargo says *perfume* →
the semantic validator raises a `cargo_un_conflict` blocking item. The orchestrator
gate refuses Layer 2: `layer2 = null`, `debug.layer2_ran = false`,
`next_action = ASK_USER`. The assistant message asks you to resolve the conflict.

---

## Request 10 — Cortex: two shipments in one message → rejected

`POST http://localhost:8000/api/v1/cortex/message`

```json
{
  "conversation_id": "pm-conv-10",
  "user_id": "tester",
  "message": "I have two shipments: 300 kg of shoes from Milan to Paris and 700 kg of toys from Madrid to Lisbon."
}
```

**Expected response (key fields):**

```json
{
  "next_action": "ASK_USER",
  "layer1": {
    "decision": "ask_user",
    "ready_for_layer_2": false,
    "intake_json": {
      "missing_fields": { "blocking": ["...one shipment per case..."] }
    },
    "assistant_message": "...asks you to split into separate cases..."
  },
  "layer2": null
}
```

**Check:** multi-shipment detection blocks the case (one case = one shipment);
`layer2` never runs. Send one of the two shipments alone in a new conversation
to confirm it then passes.

---

## Bonus scenarios (same pattern, more data coverage)

| Message | What it exercises |
|---|---|
| `Ship 18000 kg of furniture, about 45 cbm, from Shanghai to Rotterdam by sea. No rush, cost is the priority.` | SEA blocks (ports `CNSGH` → `NL RTM` exist in `data/sea/sea_c_port_capability.json`), `priority: cost` |
| `Ship 1200 kg of vaccines from Amsterdam to Tunis by air, temperature controlled 2 to 8 C.` | `pharma` + `temperature_controlled` profiles (pharma is *not* DG), cold-chain unknowns |
| `Ship an industrial machine, 12000 kg, dimensions 6 x 3.2 x 3.5 meters, from Hamburg to Marseille.` | `oversized` profile — dimensions are blocking if omitted; road abnormal-load rules in `data/road/road_b_abnormal_load_rules.json` |
| `Why is air not a good option for my batteries?` (same conversation/case as R5) | `ask_explanation` intent → `answer_user_explanation`, no Layer 2 rerun |

---

## Quick reference — what to assert where

| Stage | Field | Healthy values |
|---|---|---|
| Layer 1 | `decision` | `ask_user` → `ready_for_layer_2[_with_unknowns]` |
| Gate | `next_action` | `ASK_USER` / `SHOW_FACT_PACKAGE` / `ERROR` |
| Layer 2 | `completeness.status` | `complete_enough` / `incomplete_but_usable` |
| Layer 2 | `derived_rollup.hard_gates` | empty for liberalized EU road; populated for DG / permit corridors |
| Layer 3 | `status` | `pass_to_layer4` (or `request_user_clarification` / `request_layer2_fetch`) |
| Layer 3 | `critic_review.verdict` | `skipped` on clean cases; `pass`/`revise` on DG / gated / conflicted cases |
| Layer 3 | `safety_gate_report.status` | always present, `pass` |
| Layer 3 | whole body | must never contain `raw_score`, `internal_scoring_trace` (the endpoint 422s if it leaks) |

Error responses: `503` = LLM provider / internal failure (check `docker logs cortex-api-api-1`),
`422` = validation error (bad FactPackage body on `/layer3/reason`, or output-leak guard).
