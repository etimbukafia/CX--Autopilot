# CX Autopilot

CX Autopilot finds worthwhile CX operational opportunities and proposes small,
governed changes to an Agent, Tool, Skill, or Prompt. It does not deploy or
modify production agents.

The AI-native CX Platform owns conversations, tickets, outcomes, escalations,
CX events, business-operation references, and source evidence. Autopilot owns
signal normalization, diagnosis, change proposals, dispositions, candidate and
evaluation orchestration, pilot recommendations, and decision lineage.
Enterprise Agent Harness owns runtime authority, policies, permissions,
registries, factory construction, and resolved manifests. Enterprise Agent
Improvement Lab owns evaluation, comparison, regression evidence, and
promotion evidence. A human remains the final pilot and production authority.

The controlled flow is:

```text
CX evidence -> normalized signal -> opportunity -> diagnosis
    -> exact change proposal or NO_CHANGE disposition
    -> governed evaluation candidate -> evaluation evidence
    -> pilot recommendation -> human decision
```

This repository implements the immutable provider-neutral contracts and SQLite
persistence foundation from Phases 0–2, the Phase 3–5 CX Platform evidence
pipeline, and the Phase 6–9 governed inventory, diagnosis, eligibility,
strategy, and exact proposal boundaries, and the Phase 10-13 candidate,
evaluation, recommendation, decision, and audit boundaries.

The completed reference acceptance, taxonomy, CLI, documentation, and quality
gate for Phases 14-17 are described below.

Phase 3–5 semantics are documented in
[`docs/PHASE_3_5_IMPLEMENTATION.md`](docs/PHASE_3_5_IMPLEMENTATION.md).

Phases 6–9 semantics are documented in
[`docs/PHASE_6_9_IMPLEMENTATION.md`](docs/PHASE_6_9_IMPLEMENTATION.md).

Phases 10-13 semantics are documented in
[`docs/PHASE_10_13_IMPLEMENTATION.md`](docs/PHASE_10_13_IMPLEMENTATION.md).

Phases 14-17 semantics are documented in
[`docs/PHASE_14_17_IMPLEMENTATION.md`](docs/PHASE_14_17_IMPLEMENTATION.md).

The deterministic reference cycle can be run with:

```text
python -m cx_autopilot --db .cx-autopilot.sqlite run reference cycle
```

## Development

```text
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy src
python -m compileall -q src tests
python -m pytest -q
git diff --check
```

The package name is `cx_autopilot` and supports Python 3.11 through 3.14.
