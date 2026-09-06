# Phases 14-17 implementation

Phases 14-17 are complete. The repository now has one deterministic reference
slice, secondary taxonomy acceptance cases, a small local CLI, and a final
quality and architecture review. The reference slice ends at a human decision;
it does not deploy or modify a production Agent.

## Core contracts and evidence semantics

The important immutable records are:

- `OperationalSignal`: one bounded source observation with stable source
  identity, optional journey links, normalized attributes, evidence quality,
  and evidence references;
- `Opportunity` and `OpportunityCluster`: deterministic derived work and its
  tenant-scoped time window, with inspectable prioritization factors;
- `AgentSystemInventorySnapshot`: exact versioned components and separate
  Agent-to-Skill, Skill-to-Tool dependency, and Agent-to-Tool authority edges;
- `ProblemDiagnosis`: one taxonomy result with precedence, confidence, and
  supporting/conflicting evidence;
- `ChangeProposal` or `OperationalDisposition`: an exact governed mutation or
  a terminal no-change result;
- `CandidateReference` and `EvaluationReference`: stable Harness and Lab
  provenance rather than copied provider-owned records;
- `PilotRecommendation` and `DecisionRecord`: the human-gated review and its
  canonical outcome.

Evidence quality is separate from confidence. Unsupported prioritization
factors are `None`, not zero and not detector defaults. The record exposes
available and unavailable factors, and the prioritizer normalizes configured
weights only across available factors. This keeps a ranking explainable when
CX evidence does not yet contain impact, effort, or risk measurements.

The change target answers what is changing: `AGENT`, `TOOL`, `SKILL`, or
`PROMPT`. Relationship endpoints do not change that primary classification.
Every component operation carries exact before and after subject references,
plus exact related references where needed. A Skill or Prompt change can also
version the Agent graph that owns the exact Skill or Prompt reference; that
required graph mutation remains classified by its primary Skill or Prompt
target.

## Phase 14: transaction-history acceptance

`run_reference_cycle()` uses a read-only CX fixture and the existing public
adapter boundaries. The fixture contains three repeated failed
`get_transaction_history` operations. Ingestion keeps stable source identity,
correlates ticket, conversation, execution, interaction, journey, and trace
references, and remains idempotent when the same fixture is ingested again.

The accepted path is:

```text
CX fixture
  -> normalized evidence
  -> repeated lookup opportunity
  -> one prioritized OpportunityCluster
  -> exact Harness inventory
  -> TOOL_GAP
  -> TOOL / EXTEND
  -> ADD_AGENT_TOOL_REF
  -> evaluation-scoped Harness candidate
  -> baseline and candidate Lab evaluation
  -> improved comparison
  -> READY_FOR_HUMAN_APPROVAL
  -> human APPROVE decision
```

The inventory keeps these facts separate:

- the payment Skill is attached to the support Agent;
- the payment Skill depends on `get_payment`;
- the support Agent directly owns `get_payment` authority;
- `get_transaction_history` exists but is not direct Agent authority.

Therefore the primary target is `TOOL`, not `AGENT`, and the proposal contains
one exact operation:

```text
ADD_AGENT_TOOL_REF
support-agent@1.0.0 -> support-agent@1.1.0
related_after_ref = get_transaction_history@1.0.0
```

No Skill is created or changed. Harness candidate construction is evaluation
scoped, validates the complete resolved manifest graph, and preserves manifest
and registry provenance. Improvement Lab receives opaque baseline and
candidate values; Autopilot stores exact evaluation and comparison references.
The recommendation verifies that the supplied comparison ID equals the ID in
the EvaluationReference before accepting an `improved` verdict. The final
decision and audit trail link back to source evidence, opportunity, cluster,
inventory, diagnosis, proposal, candidate, evaluation, and recommendation.

The reference test snapshots production authority before and after candidate
construction. The snapshots are equal and no deployment call is available in
the fixture path.

## Audit corrections

Diagnosis now has a storage-backed `diagnose_cluster()` boundary. It resolves
the cluster opportunities, verifies their exact source-signal and evidence
lineage, fetches only those signal IDs, and fails closed when a contributing
record is missing or outside the tenant. Direct diagnosis also rejects signal
IDs and evidence references that are not declared by the cluster.

`DiagnosticFactKey` is the shared normalized-fact vocabulary for the CX event
adapter and diagnosis guards. The event adapter preserves diagnostic facts
from both the event envelope and its data payload. It also preserves the
authoritative event type so known outage, denial, approval, and knowledge
events can participate in diagnosis precedence.

Harness candidate references now bind the proposal, baseline inventory
snapshot, and a digest of the complete resolved Agent graph. The same graph
intent validator runs after candidate construction and before a pilot
recommendation. It compares the exact Agent, Prompt, Skill, direct Tool
authority, and Policy sets after applying every proposal operation. Lab
evaluation references preserve the same binding, so an improved comparison
cannot authorize an unrelated or incomplete candidate graph.

## Phase 15: taxonomy and no-change cases

`tests/test_phases_14_17.py` covers the secondary boundaries:

- `AGENT_GAP` selects `AGENT / CREATE` when the required governed Agent is
  absent;
- `SKILL_GAP` selects `SKILL / CREATE` when payment tools exist but the
  duplicate-charge resolution Skill is absent;
- `PROMPT_GAP` selects `PROMPT / EXTEND` when the governed graph and direct
  authority exist but behavioral instructions repeatedly fail;
- `POLICY_CONSTRAINT`, `APPROVAL_FRICTION`, `BUSINESS_DEPENDENCY`,
  `DATA_QUALITY_ISSUE`, and `KNOWLEDGE_SOURCE_ISSUE` each select
  `NO_CHANGE` and create an `OperationalDisposition`.

The no-change tests pass those dispositions to both candidate and Lab adapter
boundaries and verify that neither external boundary is called. Approval
friction remains an approval-owner responsibility; it cannot be bypassed by a
component proposal.

## Phase 16: documentation and CLI

The ownership model remains explicit:

- CX Platform owns source evidence and business-operation facts;
- Autopilot owns normalized evidence, diagnosis, proposals, dispositions,
  candidate/evaluation references, recommendations, and audit lineage;
- Harness owns registries, authority, candidate construction, and resolved
  manifests;
- Improvement Lab owns evaluation, comparison, failure, regression, and
  promotion evidence;
- a human owns pilot and production decisions.

The CLI is intentionally limited to the reference workflow and inspection:

```text
ingest fixture
discover
inspect opportunity <id>
inspect inventory <id>
inspect diagnosis <id>
inspect proposal <id>
run reference cycle
inspect lineage <decision-id>
record decision
```

Global options must come before the command. For example:

```text
python -m cx_autopilot --db .cx-autopilot.sqlite ingest fixture
python -m cx_autopilot --db .cx-autopilot.sqlite discover
python -m cx_autopilot --db .cx-autopilot.sqlite run reference cycle
python -m cx_autopilot --db .cx-autopilot.sqlite inspect lineage <decision-id>
```

`record decision` requires an explicit subject type, subject ID, decision,
actor, and reason. The CLI does not expose registry administration, deployment,
promotion, or general orchestration commands.

## Phase 17: quality and cleanup

The public-behavior suite is in `tests/test_phases_14_17.py`. The final gate
runs Ruff formatting and linting, mypy, compilation, the complete pytest suite,
and `git diff --check`. The unused candidate-construction aliases were removed
so `HarnessCandidateAdapter.construct()` is the single candidate boundary.
Recommendation status handling uses the one explicit Lab success status,
`EVALUATION_SUCCEEDED`.

The reference cycle uses deterministic local fakes at the CX, Harness, and Lab
adapter ports. It proves Autopilot boundary behavior and lineage; it is not a
live external evaluation or a production deployment test.
