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
persistence foundation from Phases 0–2, plus the Phase 3–5 CX Platform
evidence adapter, deterministic opportunity detectors, and opportunity
clustering/prioritization. Harness inventory, candidate construction, and Lab
adapters are intentionally outside this pass.

Phase 3–5 semantics are documented in
[`docs/PHASE_3_5_IMPLEMENTATION.md`](docs/PHASE_3_5_IMPLEMENTATION.md).

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
