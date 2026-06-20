# Cortex Platform — Session Handover

> Reference doc to continue work in a new chat. Written 2026-06-17.
> Hand this file to the new session as context. The repo also has a persistent
> memory dir (`~/.claude/projects/.../memory/`) that auto-loads, but this file is
> the self-contained source of truth for where things stand.

---

## 1. What this project is

**Cortex** is a freight-logistics **shipment-readiness** engine. A transport worker
describes a shipment in free text; the system returns a structured readiness
assessment per transport path, grounded in collected reference data (`data/*`).

**Layered architecture (the core principle: LLMs propose, Python decides):**

```
User free-text message
   │
   ▼
LAYER 1 — intake  (POST /api/v1/intake/message)
   ONE LLM agent call: extract + merge + profiles + missing-field triage +
   readiness decision + user reply (multilingual). Python around it = plumbing only.
   │   (gate: ready_for_layer_2 + no blocking missing fields)
   ▼
LAYER 2 — FactPackage  (runs inside POST /api/v1/cortex/message)
   NO LLM. Deterministic connectors read data/*.json → per-block facts, hard gates,
   planning factors, unknowns, confidence caps, cost. Builds the "FactPackage".
   │
   ▼
LAYER 3 — reasoning  (POST /api/v1/layer3/reason ; body = the layer2 FactPackage)
   Deterministic decision engine ranks readiness → Analyst (LLM) writes prose →
   Critic (LLM, only on risky cases) audits → Python Safety Gate has final say.
   STANDALONE debug endpoint — NOT wired into the cortex orchestrator yet.
```

**The product vision (user's framing):** a *detailed per-path report* — all viable
paths, each with a score/band, the permits/documents needed, **detailed cost**,
**time**, and supporting facts. Today the build is strong on readiness/compliance;
cost/time are deliberately conservative "planning references, not quotes."

---

## 2. Environment & ops (read before running anything)

- **Run the stack:** `docker compose up -d` (api on :8000, postgres pgvector, redis).
  Health: `curl http://localhost:8000/health` → `{"status":"alive"}`.
- **Rebuild after CODE change:** `docker compose up -d --build api`.
- **After a DATA-file change (`data/*.json`):** the connectors use `@lru_cache`, so the
  running process keeps the OLD data until you **`docker compose restart api`**. Data is
  volume-mounted read-only, so no rebuild needed — just restart to clear the cache.
- **Tests:** `.venv/bin/python -m pytest -q -m "not live_llm"` (full offline suite —
  currently **513 passing**). Live LLM tests: drop the `-m` filter and ensure
  `GOOGLE_AI_API_KEY` is set (`set -a; source .env; set +a`).
- **LLM config (`.env`) — important history:** the original `gemma-4-31b-it` is broken
  upstream (HTTP 500). Working config:
  - `GOOGLE_AI_INTAKE_MODEL=gemini-2.5-flash` (Layer 1 agent)
  - `GOOGLE_AI_LAYER3_MODEL=gemini-2.5-flash` (Layer 3 analyst/critic)
  - `INTAKE_MAX_OUTPUT_TOKENS=8192` (a single cap applied to ALL models in
    `app/core/llm.py:51` — Gemini thinking tokens need the headroom).
  - `.env.example` still shows the broken defaults — do NOT copy it blindly.
- **Docker Desktop on this machine is flaky** — the daemon quit on its own several times
  mid-session. If `curl` gives HTTP 000, run `open -a Docker`, wait, `docker compose up -d`.
  Data work is never at risk (it's on disk); only live HTTP tests need the daemon.
- **Git state:** all session work is COMMITTED at `718b053 "more cleaner version"`
  (on top of `efc54d9`). Working tree clean. The `38240cd "freez layer 1 and 2"` commit
  is the pre-rewrite baseline if you ever need to diff/revert Layer 1.

---

## 3. What was done this session

### 3.1 Layer 1 rewritten to a single agent (biggest change)
- **Why:** a two-model experiment proved the worst Layer-1 bugs were **Python, not the
  LLM** — a regex matcher inverted "ship alone, **not** inside equipment" →
  `contained_in_equipment`, and an equal-confidence merge downgraded "lithium-ion
  batteries" → "batteries". Both were English-only regex/keyword code.
- **Now:** `app/services/layer1/intake_agent.py` — one LLM call owns extraction, merge,
  profile activation, missing-field triage (blocking/high_value/can_wait), readiness
  decision, clarification questions, and the user reply (multilingual). The prompt holds
  ALL intake policy. `app/services/layer1/graph.py` (`Layer1AgentIntake`) is plumbing
  only: Redis case load/save, mechanical `_diff_changed_fields`, static rerun-scope table.
- **Deleted** ~12 modules + the `nodes/` package (deterministic_update_extractor,
  extractor, semantic_validator, missing_field_prioritizer, question_generator,
  cargo_profile_detector, intent_classifier, message_router, intake_decision_engine,
  intake_explanations, multi_shipment, state.py) + `merge_requests` from case_state_manager.
- **Contract & seam UNCHANGED:** same `IntakeResult` shape, same endpoints, the
  orchestrator's `_is_safe_for_layer_2` defensive gate still independently re-checks
  readiness. Layers 2 & 3 untouched by the rewrite.
- Tests: `tests/layer1/test_intake_agent.py` (offline, faked model),
  `test_intake_agent_live.py` (real model incl. the negation regression).
- **Known residue:** intake agent (gemini) is nondeterministic turn-to-turn — sometimes
  leaves `origin_city` null, and over-adds `general_cargo` alongside specific profiles.
  Harmless but worth a prompt tightening later.

### 3.2 ROAD-A dangerous-goods data: 0 → 105 records (+ connector wired)
- ROAD-A was a *framework with zero substance rows* (vs air 2,715 / sea 2,912). A DG road
  shipment couldn't be looked up at all.
- Built `data/road/road_a_dg_road_acceptance.json` — **105 verified ADR records** across
  all common classes (2.1/2.2/2.3/3/4.1/4.2/4.3/5.1/5.2/6.1/8/9 + subsidiary combos).
- **Wired the connector** `road_a_connector.py` to read it: known UN → real record
  (class/PG/tunnel/LQ/status, `ROAD_A_DG_HARD_GATE` only when prohibited; lithium UN3480/
  3481 correctly NOT gated on road); unknown UN → graceful fallback to the old
  "requires_specialist_validation" stub. Tests: `tests/layer2/test_road_a_connector.py`.
- **PAUSED here.** The ~2,700-entry long tail is a one-time **bulk import** job (like air/
  sea were machine-extracted), NOT hand-research.
- Note: connector still has status `"partial"` in `data_catalog.py` (intentional — partial
  coverage). Found-data dict carries both `un_number` (back-compat) and `identification_number`.

### 3.3 AIR-C airport capability coverage: 9 → 39 airports
- AIR-C had only 9 airports with capability data → Shenzhen returned "unknown". The
  connector lookup is **substring** over `airport_code`/`airport_name`/`city_country`, so
  records just need `city_country` to lead with the common city name (ASCII, not accented —
  normalize only lowercases; that's why GRU uses "Sao Paulo").
- Added 30 hubs (Asia manufacturing, freighter hubs, EU/ME/India/LatAm/Africa). Merged into
  BOTH `cortex_air_block_c_dataset.json` (what the connector reads) and the standalone
  `cortex_air_block_c_airport_capabilities.json`. No code change needed.
- Conservative data: `dangerous_goods_handling` mostly "unknown" (not publicly confirmable),
  `partial` used where evidence is limited.

### 3.4 AIR-COST: new Layer 2 block (air cost wiring)
- `air_reference.json` had a chargeable-weight formula + rate ranges but no connector.
- New `app/services/layer2/connectors/air_cost_connector.py` (BLOCK_ID `AIR-COST`): computes
  chargeable weight = max(actual, volume_cbm×167), picks a lane rate bucket
  (asia_to_europe/na/me/general_default), applies fuel+security(+DG) surcharges →
  `estimated_cost_usd` low/typical/high + transit days. Always `status=unknown`,
  `planning_reference`, cap 0.5, `cost_status="planning_reference_not_a_quote"`.
- Wired: `registry.py`, `fetch_planner.py` (`_air_cost_item`, appended last in `_air_items`),
  `data_catalog.py` (repointed `air_reference.json` from unused `AIR-REF` → `AIR-COST`).
  Tests: `tests/layer2/test_air_cost_connector.py` (7). Updated 6 planner tests' air block lists.
- Live: Shenzhen→Frankfurt 2000kg/12cbm → $8.7k–$14.9k–$26.2k, 2–4–7 days.

### 3.5 Cascade-skip REMOVED (full report even when blocked)
- Previously: a triggered blocking hard gate made `fetch_executor` replace all later
  same-mode blocks with `skipped` stubs → worker saw 1 gate + nothing.
- Now: `execute_fetch_plan` runs EVERY planned block. The gate is still recorded on its
  block + rollup, `completeness` stays `"blocked"`, Layer 3 still produces BLOCKED — but the
  report is complete (carriers, fit, docs, security, cost) alongside the gate.
- Coordinated change: retired the two `conflict_detector` policies that ENFORCED the skip
  (`mode_blocked_but_later_blocks_present`, `cost_reference_present_for_blocked_mode`) +
  dead `_has_blocking_gate` helper. Updated ~15 tests (skip→run; removed-conflict tests now
  assert absence; repointed builder/trace conflict tests to the live `duplicate_block_response`;
  dropped `tracking_milestones` from the consistency raw-key blocklist — AIR-I surfaces a
  curated enriched projection, not a raw dump).
- Live: blocked UN3480 air → AIR-A gate + AIR-D 10 carriers + AIR-COST $40k–$74k–$140k,
  completeness still "blocked", **zero skipped blocks**.

---

## 4. Reusable data-gathering workflow (for filling more data gaps)

The user collects reference data in a **separate web-LLM session** (with web search), then
pastes JSON back here for validation. The loop that works:

1. **Prompt** (schema-locked, copyright-safe, batched ~15 items, per-record sources,
   anti-hallucination: null + lower confidence when unsourced). The ROAD-A and AIR-C prompts
   are in the chat history — rebuild from the actual schema file, NOT prose summaries
   (a prompt built from prose got field names wrong once).
2. **Validate** the pasted array against the real schema (field names + allowed enum values).
3. **Normalize** (e.g. ROAD-A: `check_required` → confidence `estimated`; `factor_type`
   derived from `hard_gate`; `"-"`/no-restriction tunnel → null; lead names with a non-digit
   synonym, e.g. UN3159).
4. **Merge** with dedup (by UN number / IATA code) into the data file.
5. **`docker compose restart api`** to clear the lru_cache, then live-verify.

Schema/lookup gotchas:
- ROAD-A schema: `data/road/road_a_schema.json`; status enum = `accepted_with_conditions` |
  `restricted` | `prohibited_or_not_accepted` | `check_required`. PG/tunnel/LQ may be null.
- AIR-C: connector reads the **dataset** file's `airport_capabilities` array; flags are
  `yes`/`no`/`partial`/`unknown`; `confidence` is `high`/`medium`/`low`; `city_country` must
  lead with the city name in ASCII.

---

## 5. Remaining gaps (deep-check results — pick up here)

### Data gaps (web-research workflow)
- **ROAD-A long tail** (~2,700 ADR UN numbers) — do as a **bulk import**, not batches.
- **AIR-F jurisdictions = 9** and **AIR-D carriers = 10** — thin-ish; deepen if air report
  needs broader border/carrier coverage.
- **SEA side already deep** (DG 2,912 / ports 3,838) — likely fine; not re-audited closely.
- ROAD-A connector still `"partial"` and SEA-F `"partial"` in `data_catalog` status map.

### Code / architecture gaps
- **Layer 3 not wired into the orchestrator** — it's a standalone `/layer3/reason` debug
  endpoint taking a FactPackage body. Wiring it into `/cortex/message` is a real next step.
- **Layer 3 governance gaps (from the original audit, still open):**
  - *Dead critic trigger:* `context_builder.py` hardcodes every unknown's severity to
    `"unknown"`, but the critic-trigger checks for `high`/`critical` — so the "high/critical
    unknowns" trigger can never fire. Needs one severity taxonomy for unknowns.
  - *Nondeterministic clarification routing:* identical FactPackages can route to
    `pass_to_layer4` OR `request_user_clarification` depending on whether the Analyst chose
    to emit questions. A Python rule would make it reproducible.
  - *Config coupling:* `app/core/llm.py:51` applies one `INTAKE_MAX_OUTPUT_TOKENS` to all
    models — a dedicated `GOOGLE_AI_LAYER3_MAX_TOKENS` would decouple intake from Layer 3.
- **Layer 2 cosmetic:** `fetch_executor.blocking_gate` style duplication of unknowns in the
  rollup (the "60 unknowns" amplification) — dedup + per-unknown `affected_blocks`.
- **Intake agent nondeterminism:** sometimes null `origin_city`; over-adds `general_cargo`.
- **Cross-mode comparison ("all paths with scores") never exercised** — every test used a
  single mode. The multi-mode fetch+rank path is unproven; this is the heart of the vision.
- **Cost/time are planning references, not quotes** by design — becoming a real quote engine
  needs live carrier/routing APIs (out of current scope).

---

## 6. Key file map

| Path | Role |
|---|---|
| `app/services/layer1/intake_agent.py` | Layer 1 agent + prompt (the policy owner) |
| `app/services/layer1/graph.py` | Layer 1 service: agent turn + plumbing |
| `app/services/layer2/fetch_planner.py` | builds the per-mode fetch plan |
| `app/services/layer2/fetch_executor.py` | runs all blocks (cascade-skip removed) |
| `app/services/layer2/registry.py` | block_id → connector map |
| `app/services/layer2/connectors/*` | one per block (air/road/sea + *-COST) |
| `app/services/layer2/conflict_detector.py` | conflicts (skip-enforcing ones removed) |
| `app/services/layer2/fact_package_builder.py` | rollup + completeness (`blocked` logic) |
| `app/services/layer2/data_catalog.py` | file→block mapping + connector status |
| `app/services/layer3/graph.py` | Layer 3 LangGraph (`_should_run_critic` triggers) |
| `app/services/layer3/routing.py` | post-review routing (clarify-before-pass) |
| `app/services/orchestrator/cortex_orchestrator.py` | `_is_safe_for_layer_2` seam gate |
| `data/{air,road,sea}/*.json` | reference data (committed on purpose) |
| `docs/POSTMAN_TESTING_GUIDE.md` | 10-request Postman flow (may predate the rewrite) |

## 7. Persistent memory files (`~/.claude/projects/.../memory/`)
- `MEMORY.md` (index), `llm-model-constraints.md`, `layer1-agent-rewrite.md`,
  `detailed-report-vision.md` (the mode-by-mode data audit + all gaps-closed this session),
  `repo-handoff-cleanup.md`. These auto-load in a new session in this project.

## 8. Suggested next steps (in priority order)
1. Decide: keep filling data (ROAD-A bulk import / AIR-F-D depth) **or** move to code.
2. **Wire Layer 3 into the cortex orchestrator** (currently standalone) — unlocks the
   end-to-end product.
3. Exercise + build the **multi-mode "all paths with scores"** path (the vision's core).
4. Layer 3 governance fixes (severity taxonomy → revive critic trigger; deterministic
   clarify routing; per-layer max-tokens).
5. Layer 2 unknown-dedup cleanup.
