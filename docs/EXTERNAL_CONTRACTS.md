# Verified external contracts for the first reference slice

Verified on 2026-09-05 from the current `main` branches. The temporary source
copies used for inspection are not part of this repository.

## AI-native CX Platform

Sources inspected:

- `cx_platform/domain/models.py`
- `cx_platform/services/events.py`
- `cx_platform/services/outcomes.py`
- `cx_platform/api.py`
- `tests/test_cx_events.py`
- `tests/test_phase12_exports.py`
- `cx_platform/README.md`

Verified facts needed by a later adapter:

- `CXEvent` is an append-only source fact with `event_id`, `event_type`,
  `occurred_at`, optional `customer_id`, `ticket_id`, `conversation_id`,
  `message_id`, and `execution_id`, plus bounded JSON `data`.
- `Conversation` is linked to a `ticket_id` and `customer_id`. The current
  export uses `conversation_id` as the available interaction-level identity;
  the repository has no separate `journey_id` field.
- `OutcomeRead` links `outcome_id` and `ticket_id` to an optional
  `execution_id`, resolution code, resolved/escalated state, tool counts and
  IDs, approval result, CSAT, escalation ID, and `evidence_ids`.
- `Escalation` preserves `escalation_id`, `ticket_id`, optional
  `conversation_id` and `execution_id`, attempted actions, tool-result refs,
  reason, summary, and status.
- `ExecutionReference` preserves `execution_id`, ticket and conversation
  identity, `agent_id`, `agent_version`, optional `trace_reference`, timing,
  and outcome status. The CX platform exposes the trace reference but does not
  export the full Harness trace in the execution response.
- Business-operation identity is carried by source event data and the
  business-system event feed. Autopilot must retain the source reference and
  must not copy business truth into its own store.
- The current CX-owned models do not carry `tenant_id`. The Phase 3 adapter
  receives tenant identity in its constructor; it does not infer a tenant from
  customer, ticket, conversation, or event IDs.
- The adapter follows the typed HTTP read routes for events, tickets, ticket
  detail, conversation detail, outcomes, and execution references. It keeps
  source references and bounded normalized attributes in `OperationalSignal`.
  It does not copy message content, business truth, or full Harness traces.

## Enterprise Agent Harness

Sources inspected:

- `docs/public-api.md`
- `src/enterprise_agent_harness/contracts.py`
- `src/enterprise_agent_harness/registries.py`
- `src/enterprise_agent_harness/factory.py`

Verified facts needed later:

- `ComponentType` is the closed set `AGENT`, `PROMPT`, `SKILL`, `TOOL`, and
  `POLICY`. `ComponentReference` has exact `component_type`, `component_id`,
  and `version` identity.
- `AgentDefinition` and `AgentConfig` use one exact `prompt_ref`, exact
  `skill_refs`, exact executable `tool_refs`, and exact `policy_refs`.
  `PromptDefinition`, `SkillDefinition`, `ToolDefinition`/`ToolDescriptor`,
  and `PolicyDefinition` are distinct versioned contracts.
- `SkillDefinition.required_tool_refs` and `optional_tool_refs` describe
  dependencies only. They never grant execution authority. Direct executable
  authority is the Agent's `tool_refs`.
- `RegistrySnapshot` records snapshot identity, registry revisions, exact
  component records, and dependency edges. `AgentRegistry`, `PromptRegistry`,
  and `SkillRegistry` are public registries; the tool registry is exposed by
  the tool layer. Policy access is through policy definitions and the
  declarative policy engine rather than a separate Autopilot-owned policy
  registry.
- `ResolvedAgentManifest` records `manifest_id`, `manifest_digest`, exact
  prompt/skill/tool/policy refs, the resolved agent, runtime provenance, and
  `registry_snapshot_id`.
- `AgentFactory.validate()` is read-only. A dry-run build resolves and returns
  a manifest without registering or constructing a runtime. Active builds and
  runtime authority remain Harness responsibilities.

## Enterprise Agent Improvement Lab

Sources inspected:

- `contracts/candidates.py`
- `contracts/experiments.py`
- `contracts/evaluation.py`
- `contracts/promotion.py`
- `integrations/enterprise_agent_harness/contracts.py`
- `enterprise_runner.py`
- package exports in `__init__.py`

Verified facts needed later:

- `EnterpriseAgentCandidate` is the current candidate contract. It carries a
  candidate identity, agent identity/version, pinned artifact references,
  optional exact prompt/skill/tool/policy component references, candidate
  lineage, status, and creation time.
- Lab/Harness integration contracts carry `candidate_id`, `manifest_id`,
  `manifest_digest`, `registry_snapshot_id`, `agent_ref`, `prompt_ref`, and
  exact skill/tool/policy refs without importing Harness types into the Lab
  boundary.
- Evaluation runs use `EnterpriseEvaluationRunner.run(dataset, candidate,
  manifest)`, produce `EnterpriseEvaluationReport`, and preserve candidate,
  run, environment, trace, score, failure, and evidence references.
- `BaselineComparison` is the comparison record. It contains baseline and
  candidate run/candidate identities, verdict and regressions, environment
  compatibility, exact manifest identities/digests, and component changes.
- `PromotionEvaluation`/`PromotionDecision` and the risk-aware promotion
  contracts carry promotion evidence references. They remain Lab-owned human
  promotion evidence; Autopilot stores only exact references.

## Autopilot dependency rule

Autopilot core contracts use its own `ExactComponentReference`, evidence
references, and immutable records. No Harness, CX Platform, or Improvement Lab
class is imported into `src/cx_autopilot`. Later adapters may translate at the
boundary and must use the verified contracts above.
