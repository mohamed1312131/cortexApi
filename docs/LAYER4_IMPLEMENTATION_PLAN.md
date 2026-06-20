# Cortex Layer 4 Implementation Plan

Status: Draft v1  
Scope: Full report agent first  
Date: 2026-06-16

## 1. Layer 4 Purpose

Layer 4 is the Cortex Transport Readiness Report Agent.

Its job is to transform the structured shipment facts and Layer 3 reasoning into
a professional chat-style report for a logistics or transport user.

The user is not a casual end user. The user works in transport and normally
spends days checking possible modes, papers, constraints, restrictions, carrier
questions, schedule limits, cost boundaries, and operational risks. Layer 4 must
turn that research burden into a clear operational answer.

Layer 4 does not decide again. It presents, explains, organizes, and translates
the previous layers into a useful transport dossier.

## 2. Pipeline Position

```text
Layer 1 - Conversational intake and shipment request
Layer 2 - FactPackage and transport evidence
Layer 3 - ReasoningDecision and ranked readiness
Layer 4 - Professional transport report for the user
```

Layer 4 consumes:

```text
FactPackage from Layer 2
Layer3Result / ReasoningDecision from Layer 3
latest user message or report request
```

Layer 4 returns:

```text
Full chat-style transport readiness report
```

## 3. Product Goal

When the user asks how to ship a cargo, Cortex should not only answer:

```text
Sea is ranked first.
```

It should produce a useful report:

```text
Sea is currently the strongest preparation path.
Road was evaluated and has these restrictions.
Air remains possible but has these DG/equipment/carrier checks.
These documents are likely needed.
These facts are unknown.
Ask the forwarder these questions before booking.
This is not final approval or booking confirmation.
```

Layer 4 should feel like an expert transport assistant that has read the facts
and prepared an operational briefing.

## 4. Truth Hierarchy

The report agent must obey this order:

```text
1. ReasoningDecision = ranking and readiness truth
2. FactPackage = operational evidence and details
3. ShipmentRequest = shipment facts
4. Agent = wording, organization, and report quality only
5. Future memory/vector context = hints only, never truth
```

Layer 4 must never use its own judgment to override Layer 3 ranking or readiness
bands. It may explain why a mode is weak, blocked, unknown, or not evaluated only
when that is supported by Layer 2 or Layer 3 data.

## 5. V1 Report Type

V1 supports one report type:

```text
full_report
```

Later report views can reuse the same input packet:

```text
documents_only
risks_only
forwarder_questions_only
executive_summary
mode_detail
```

These are deferred.

## 6. Full Report Sections

The v1 full report should be a chat answer with clear sections.

Target sections:

```text
1. Executive Summary
2. Best Preparation Path
3. Evaluated Modes
4. Mode-by-Mode Details
5. Documents / Paperwork Needed
6. Hard Gates / Blockers
7. Unknowns / Missing Checks
8. Cost and Schedule Boundaries
9. Questions to Ask Forwarder / Carrier
10. Recommended Next Actions
11. Important Warnings
```

The exact wording can adapt to the shipment. The agent should not force empty
sections when no useful data exists, but must not hide warnings, hard gates, or
important unknowns.

## 7. Mode Behavior

Layer 4 does not blindly show road, sea, and air in every report.

It follows what the previous layers evaluated:

```text
If a mode is ranked by Layer 3:
  explain that mode using the Layer 3 rank, readiness band, gates, unknowns,
  next actions, and relevant Layer 2 details.

If a mode appears in the FactPackage but is not highly ranked:
  explain it as an evaluated alternative and why it is weaker, blocked, or less
  ready when the data supports that.

If a mode was skipped or not covered:
  do not invent a mode report. Mention that it was not evaluated only when useful
  and supported by the data.

If the user requested a concrete mode:
  focus the report around that mode while still surfacing Layer 3 warnings and
  alternatives that were actually evaluated.
```

The agent must not create fake multimodal coverage.

## 8. Language And Style

The response language is automatic.

```text
If the user writes in English, answer in English.
If the user writes in French, answer in French.
If mixed or unclear, prefer the language of the latest user message.
```

V1 output style:

```text
chat answer
professional
practical
scan-friendly
not a PDF
not raw JSON shown to the user
```

The user should be able to act from the answer.

## 9. Agent Identity

Layer 4 should be designed as an agent, not as a deterministic Python formatter.

The agent mission:

```text
You are Cortex Layer 4, the Transport Readiness Report Agent.
You transform structured shipment facts and Layer 3 reasoning into a professional
full transport readiness report for a logistics or transport user.
```

The agent can reason about presentation, grouping, priority, wording, and how to
make the report useful. It cannot reason again about transport readiness.

Any LangGraph wrapper should be an orchestration shell around the report agent,
not a replacement for the report agent's mission.

## 10. Prompt Contract

Draft system behavior:

```text
You are Cortex Layer 4, a Transport Readiness Report Agent.

Your job is to transform structured shipment facts and Layer 3 reasoning into a
professional full transport readiness report for a logistics/transport user.

You do not decide, rank, approve, or validate shipment feasibility yourself.
Use the ReasoningDecision as the authority for ranking, readiness bands, hard
gates, warnings, confidence, and next actions.

Use the FactPackage as supporting evidence for documents, operational factors,
unknowns, restrictions, cost boundaries, schedule boundaries, and preparation
details.

Write a clear chat-style full report.
Be practical and operational.
Show evaluated modes and explain skipped or unavailable modes only when the
provided data supports it.
Always surface must-show warnings.
Never make final approval, booking, customs, carrier, live price, or live
schedule claims.
```

## 11. Forbidden Behavior

Layer 4 must not:

```text
re-rank modes
change readiness bands
change confidence bands
hide hard gates
hide must-show warnings
hide important unknowns
invent documents
invent live quotes
invent live schedules
invent carrier acceptance
invent customs clearance
claim final legal approval
claim final booking readiness
turn memory into transport truth
```

Unsafe claims to avoid unless explicitly supported by prior layers:

```text
approved
guaranteed
booking confirmed
customs cleared
carrier accepted
terminal accepted
final legal clearance
final customs clearance
exact price
confirmed live schedule
will arrive
will clear
best route
optimal route
```

Prefer safer transport wording:

```text
strongest preparation path
currently ranked first
requires carrier validation
requires forwarder confirmation
not booking-ready
not final approval
planning reference only
live schedule not verified
live quote not verified
```

## 12. Layer 4 Input Packet

Conceptual input:

```text
Layer4Input
  report_type: full_report
  latest_user_message
  fact_package
  layer3_result
  reasoning_decision
  response_language: auto
```

The agent should receive a controlled context packet derived from these objects.
It should not receive unrelated old conversation history or unscoped future
memory.

## 13. Layer 4 Output

V1 user-facing output:

```text
assistant_message
```

Optional internal envelope for API/frontend use:

```text
Layer4Result
  case_id
  report_type
  assistant_message
  modes_reported
  warnings_shown
  source_reasoning_decision_id
```

The user sees the chat-style `assistant_message`. The extra fields are for the
API and future UI.

## 14. Future Memory And Vector Context

Memory and vector retrieval are not v1.

Later, Layer 4 can retrieve:

```text
company lane preferences
user style preferences
similar case notes
forwarder question templates
document templates
```

Memory must always be marked as a hint. It cannot override Layer 2 facts or
Layer 3 decisions.

Bad:

```text
Memory says sea is best, so sea is best.
```

Good:

```text
Memory suggests this company often asks for concise executive summaries, so the
report can be shorter while preserving all warnings.
```

## 15. Implementation Roadmap

Build Layer 4 in small steps:

```text
1. Freeze this Layer 4 agent contract.
2. Define the input packet and output envelope.
3. Write the report agent prompt.
4. Add a standalone Layer 4 service around the agent.
5. Add POST /api/v1/layer4/report.
6. Manually test with Layer 2 + Layer 3 outputs.
7. Tune report behavior and prompt quality.
8. Add optional report views after full_report is good.
9. Add memory/vector retrieval only after report quality is stable.
10. Wire Layer 4 into the full Cortex route after standalone behavior is trusted.
```

V1 should not start with pgVector, long-term memory, or complex follow-up routing.

## 16. Open Decisions

Open questions to answer before coding:

```text
1. Should Layer 4 receive the full Layer3Result or only ReasoningDecision plus
   selected Layer 3 metadata?
2. Should the API return only assistant_message or a small Layer4Result envelope?
3. Should the first implementation use LangChain direct invocation or a small
   LangGraph wrapper for future expansion?
4. Should the prompt ask for Markdown directly, or structured JSON with
   assistant_message inside?
```

Current recommendation:

```text
Use full Layer3Result + FactPackage as input.
Return Layer4Result with assistant_message.
Use LangChain model invocation first.
Keep LangGraph wrapper optional until there is real routing or memory.
```

