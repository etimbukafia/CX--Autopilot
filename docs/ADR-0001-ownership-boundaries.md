# ADR-0001: System ownership boundaries

Status: accepted for the initial build.

## Decision

CX Autopilot owns operational signal normalization, opportunity and cluster
records, operational diagnosis, exact change targeting and strategy, typed
change proposals, no-change dispositions, candidate and evaluation references,
pilot recommendations, and decision lineage.

The AI-native CX Platform remains the source of truth for customer-service
operations and source evidence. Enterprise Agent Harness remains the authority
for governed component contracts, permissions, policy, approvals, registries,
runtime execution, and resolved build manifests. Enterprise Agent Improvement
Lab remains the authority for evaluation, evaluated-failure diagnosis,
comparison, regression evidence, and promotion evidence. A human owns final
pilot and production authority.

Autopilot stores references to those systems. It does not copy their runtime,
business-truth, registry, policy, evaluator, or promotion implementations.

## Consequences

- A diagnosis or recommendation never grants execution authority.
- A Skill dependency never becomes Agent tool authority.
- Prompt changes never grant authority.
- `NO_CHANGE` is a terminal Autopilot disposition and does not call Harness or
  the Improvement Lab.
- Source evidence stays immutable and every derived record keeps evidence
  references.
- The core package uses only Autopilot-owned provider-neutral types.
