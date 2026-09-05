# CX Autopilot Architecture and Build Plan

Status: proposed implementation plan

## 1. Product goal

CX Autopilot is an enterprise system that inspects customer-experience operations, discovers worthwhile automation opportunities, determines what agent-system change is appropriate, builds governed candidates through Enterprise Agent Harness, evaluates them through Enterprise Agent Improvement Lab, and produces a controlled pilot recommendation for human review.

CX Autopilot proposes system changes.

It does not directly modify or deploy production agents.

## 2. Product boundary

The system must preserve these responsibilities.

### AI-native CX Platform

Repository:

https://github.com/etimbukafia/AI-native-CX-platform

Owns:

- conversations;
- tickets;
- customers;
- CX events;
- business-operation references;
- outcomes;
- escalations;
- CSAT and service metrics;
- execution references;
- operational evidence exports.

It tells Autopilot what happened operationally.

### CX Autopilot

Repository:

https://github.com/etimbukafia/CX--Autopilot

Owns:

- operational signal normalization;
- opportunity discovery;
- opportunity clustering;
- operational problem diagnosis;
- agent, prompt, skill, tool, and policy inventory analysis;
- `REUSE`, `EXTEND`, `COMPOSE`, `CREATE`, or `NO_CHANGE` strategy selection;
- typed change proposals;
- candidate construction requests;
- evaluation orchestration;
- pilot recommendations;
- decision records and audit lineage.

### Enterprise Agent Harness

Repository:

https://github.com/etimbukafia/enterprise-agent-harness

Owns:

- governed component definitions;
- exact versioned Prompt, Skill, Tool, Policy, and Agent contracts;
- runtime authority;
- permissions;
- policies;
- approvals;
- tool execution;
- provider/runtime configuration;
- state;
- registry/factory construction;
- resolved manifests;
- execution traces and audit evidence.

### Enterprise Agent Improvement Lab

Repository:

https://github.com/etimbukafia/enterprise-agent_improvement_lab

Owns:

- evaluation datasets and cases;
- deterministic evaluators;
- evaluated-failure diagnosis;
- root-cause hypotheses after evaluation;
- bounded candidate improvement;
- baseline versus candidate comparison;
- regression evidence;
- promotion evidence;
- human-controlled promotion decisions.

### Human authority

A human remains the final authority for:

- accepting a pilot recommendation;
- changing production authority;
- promoting a candidate;
- production deployment.

---

## 3. Core system flow

```text
CX operational evidence
        |
        v
Signal ingestion and normalization
        |
        v
Opportunity discovery
        |
        v
Opportunity clustering and prioritization
        |
        v
Operational problem diagnosis
        |
        v
Agent-system inventory inspection
        |
        v
REUSE / EXTEND / COMPOSE / CREATE / NO_CHANGE
        |
        v
Typed ChangeProposal
        |
        v
Enterprise Agent Harness
        |
        v
Governed candidate + ResolvedAgentManifest
        |
        v
Enterprise Agent Improvement Lab
        |
        v
Evaluation + comparison + promotion evidence
        |
        v
CX Autopilot PilotRecommendation
        |
        v
Human decision
```

The control flow must be deterministic at decision boundaries where rules can be explicit.

Model output may suggest classifications or proposals, but typed validation and policy logic must decide what enters the next stage.

---

## 4. First reference case

The first end-to-end reference case is repeated transaction-history lookup.

### Operational pattern

CX evidence shows repeated customer-service work where agents must inspect transaction history.

The current support agent does not have `get_transaction_history` as executable authority.

### Expected Autopilot flow

```text
Repeated transaction-history evidence
        |
        v
Opportunity discovered
        |
        v
Current agent inventory inspected
        |
        v
Existing payment skill exists
        |
        v
Required executable operation is missing
        |
        v
TOOL_GAP
        |
        v
EXTEND
        |
        v
ChangeProposal
  -> keep existing agent
  -> keep existing prompt unless evidence says otherwise
  -> keep existing payment skill
  -> add exact get_transaction_history tool authority
  -> update skill dependency only if required
        |
        v
Harness builds governed candidate
        |
        v
Lab evaluates and compares
        |
        v
PilotRecommendation
        |
        v
Human approval required
```

This case must prove that Autopilot can identify the smallest valid change instead of creating a new agent or skill unnecessarily.

---

## 5. Domain model

The initial domain model should include these immutable typed contracts.

### OperationalSignal

Represents one normalized source-owned observation.

Required concepts:

```text
signal_id
source
signal_type
occurred_at
tenant_id
interaction_id
customer_id optional
agent_id optional
execution_id optional
trace_id optional
source_reference
payload_reference or bounded normalized attributes
evidence_refs
```

The signal must preserve the original source reference.

Do not copy large source payloads into Autopilot when a stable reference is enough.

### Opportunity

Represents one candidate automation opportunity supported by evidence.

Required concepts:

```text
opportunity_id
title
description
source_signal_ids
evidence_refs
frequency estimate
impact estimate
confidence
status
created_at
```

Opportunity is not yet a diagnosis or change proposal.

### OpportunityCluster

Groups related opportunities or repeated evidence patterns.

Required concepts:

```text
cluster_id
opportunity_ids
pattern_summary
evidence_refs
frequency
impact
confidence
```

### ProblemDiagnosis

Represents Autopilot's operational diagnosis.

Diagnosis taxonomy:

```text
SKILL_GAP
PROMPT_GAP
AGENT_GAP
TOOL_GAP
POLICY_CONSTRAINT
APPROVAL_FRICTION
BUSINESS_DEPENDENCY
DATA_QUALITY_ISSUE
KNOWLEDGE_SOURCE_ISSUE
```

Required concepts:

```text
diagnosis_id
cluster_id
diagnosis_type
summary
supporting_evidence_refs
conflicting_evidence_refs
confidence
affected_agent_refs
affected_prompt_refs
affected_skill_refs
affected_tool_refs
affected_policy_refs
created_at
```

A diagnosis must not grant authority and must not create a candidate by itself.

### AgentSystemInventorySnapshot

Represents exact inspected component state.

Required concepts:

```text
snapshot_id
captured_at
agent_refs
prompt_refs
skill_refs
tool_refs
policy_refs
registry_snapshot_ids
manifest_refs
source_system
```

All component references must be exact and versioned.

### ChangeStrategy

Allowed values:

```text
REUSE
EXTEND
COMPOSE
CREATE
NO_CHANGE
```

Strategy is separate from diagnosis.

### ChangeProposal

Represents the smallest bounded system change Autopilot recommends.

Required concepts:

```text
proposal_id
opportunity_id or cluster_id
diagnosis_id
strategy
baseline_inventory_snapshot_id
target_agent_refs
proposed_component_changes
rationale
evidence_refs
risk_classification
requires_human_review
created_at
```

Component changes must use exact typed references and explicit operations.

Examples:

```text
ADD_AGENT_TOOL_REF
REMOVE_AGENT_TOOL_REF
ADD_AGENT_SKILL_REF
REMOVE_AGENT_SKILL_REF
CHANGE_AGENT_PROMPT_REF
CREATE_PROMPT
CREATE_SKILL
CREATE_TOOL
CREATE_AGENT
COMPOSE_AGENT
NO_CHANGE
```

Do not let a skill change imply tool authority.

Do not let a prompt change imply authority.

### CandidateReference

References the governed candidate produced through Enterprise Agent Harness.

Required concepts:

```text
candidate_id
agent_ref
manifest_id
manifest_digest
registry_snapshot_id
prompt_ref
skill_refs
tool_refs
policy_refs
```

Harness resolved manifest is authoritative for what was built.

### EvaluationReference

References the Improvement Lab evaluation and comparison evidence.

Required concepts:

```text
evaluation_id
baseline_candidate_id
candidate_id
comparison_id optional
promotion_evidence_id optional
status
evidence_refs
```

Autopilot must not duplicate Lab evaluation content when exact references are enough.

### PilotRecommendation

Represents Autopilot's final controlled recommendation.

Required concepts:

```text
recommendation_id
proposal_id
candidate_reference
evaluation_reference
summary
expected_operational_impact
known_risks
pilot_scope
rollback_conditions
evidence_refs
requires_human_approval
status
created_at
```

### DecisionRecord

Represents the human or system decision at a controlled boundary.

Required concepts:

```text
decision_id
recommendation_id
decision
actor_ref
occurred_at
reason
evidence_refs
```

---

## 6. Evidence model

Autopilot must operate on the complete CX journey, not only conversation text.

Supported signal families should include:

### Conversation evidence

- customer messages;
- agent responses;
- intent or topic classification when source-owned;
- clarifications;
- repeated questions;
- escalation requests.

### Agent and workflow evidence

- tool calls;
- tool failures;
- retries;
- approval requests;
- approval delays;
- policy denials;
- escalation causes;
- handoffs;
- repeated manual action sequences;
- workflow transitions.

### Outcome evidence

- resolution;
- unresolved contact;
- repeat contact;
- escalation;
- refund/return/cancellation outcome;
- CSAT;
- SLA result;
- handle-time or effort metrics.

### Business-operation evidence

- order lookup;
- shipment lookup;
- payment lookup;
- transaction-history lookup;
- refund requests;
- returns;
- cancellations;
- business-service outages.

### Knowledge and policy evidence

- knowledge-source lookup;
- missing knowledge;
- stale knowledge;
- policy conflict;
- policy denial;
- approval requirements.

### Evidence rule

Autopilot must preserve source evidence references.

It may derive signals and diagnoses, but derived records must always retain lineage to source evidence.

Score, confidence, inference, and evidence are different concepts.

---

## 7. Opportunity discovery rules

Opportunity discovery should start with deterministic signals before deeper model analysis.

Strong initial patterns include:

- repeated action sequences;
- high-volume repeated contacts;
- high handle time with predictable outcome;
- repeated escalations with the same cause;
- repeated customer contact after failed resolution;
- consistent human workaround;
- repeated operator correction;
- repeated missing executable operation;
- repeated missing reusable skill;
- repeated prompt-behavior correction;
- repeated approval delay;
- repeated policy denial;
- repeated missing or stale knowledge evidence.

Do not send every event or conversation token through a large model.

Use lightweight deterministic extraction first.

Use deeper model analysis only for evidence sets that pass relevance thresholds.

---

## 8. Diagnosis semantics

### TOOL_GAP

Use when the required atomic executable operation is unavailable to the relevant agent or does not exist.

Example:

```text
payment skill exists
payment evidence tools exist
transaction-history operation is missing
```

### SKILL_GAP

Use when required tools may exist but there is no coherent reusable competence for the job.

Example:

```text
payment tools exist
duplicate-charge resolution skill does not exist
```

### PROMPT_GAP

Use when the needed competence and authority exist, but behavior instructions repeatedly cause incorrect or incomplete behavior.

### AGENT_GAP

Use when the required governed actor or composition does not exist and the problem cannot be solved by extending an existing agent safely.

### POLICY_CONSTRAINT

Use when policy intentionally blocks an operation or workflow.

Do not classify an intentional policy control as a missing tool or skill.

### APPROVAL_FRICTION

Use when approval requirements are valid but operational evidence shows avoidable delay or poor approval flow.

Autopilot may recommend review of approval design, but it must not bypass approval authority.

### BUSINESS_DEPENDENCY

Use when the limitation is in an external business system or service.

### DATA_QUALITY_ISSUE

Use when source data is missing, inconsistent, invalid, or too stale for reliable automation.

### KNOWLEDGE_SOURCE_ISSUE

Use when knowledge needed for safe resolution is absent, stale, contradictory, or inaccessible.

---

## 9. Change strategy semantics

### REUSE

Select when an existing agent, prompt, skill, tool, or composition already solves the opportunity without changing the governed graph.

### EXTEND

Select when an existing agent or component should gain a bounded capability such as a new exact tool reference, skill reference, or prompt version.

### COMPOSE

Select when existing components should be assembled into a new governed composition without creating unnecessary new primitives.

### CREATE

Select only when no existing component can safely satisfy the requirement through reuse, extension, or composition.

### NO_CHANGE

Select when evidence is insufficient, the problem is external, expected value is too low, risk is too high, or no agent-system change is justified.

Prefer the smallest valid strategy.

---

## 10. Integration architecture

Use ports and adapters.

Suggested package layout:

```text
src/cx_autopilot/
  contracts/
  domain/
  evidence/
  opportunities/
  diagnosis/
  inventory/
  strategy/
  proposals/
  orchestration/
  recommendations/
  integrations/
    cx_platform/
    enterprise_agent_harness/
    enterprise_agent_improvement_lab/
  storage/
  cli.py
```

The exact layout may be simplified during implementation if fewer modules are sufficient.

Do not create framework layers without a current use.

### CX Platform adapter

Reads source-owned CX evidence and outcome records.

The adapter must not redefine CX Platform business truth.

### Harness adapter

Reads exact registry/component state and submits governed candidate construction requests.

The adapter must use current public contracts from:

https://github.com/etimbukafia/enterprise-agent-harness

Do not copy Harness runtime logic into Autopilot.

### Improvement Lab adapter

Submits candidate evaluation requests and reads evaluation/comparison/promotion evidence.

The adapter must use current public contracts from:

https://github.com/etimbukafia/enterprise-agent_improvement_lab

Do not copy Lab failure taxonomy, evaluation, candidate builders, comparison, or promotion logic into Autopilot.

---

## 11. Storage

Start with SQLite for local deterministic development unless an existing repository requirement proves a different choice.

Persist:

- normalized operational signals;
- opportunities;
- opportunity clusters;
- diagnoses;
- inventory snapshots;
- change proposals;
- candidate references;
- evaluation references;
- pilot recommendations;
- decision records;
- evidence lineage references.

Do not persist raw secrets, credentials, or unnecessary prompt/source payloads.

Use a forward-only schema during the initial build.

---

## 12. Orchestration model

Use explicit workflow stages.

Suggested states:

```text
EVIDENCE_COLLECTED
OPPORTUNITY_DISCOVERED
OPPORTUNITY_PRIORITIZED
DIAGNOSED
INVENTORY_RESOLVED
STRATEGY_SELECTED
PROPOSAL_READY
CANDIDATE_BUILT
EVALUATED
PILOT_RECOMMENDED
AWAITING_HUMAN_DECISION
APPROVED
REJECTED
CLOSED
```

Do not let model output skip required stages.

State transitions must be deterministic and auditable.

---

## 13. Prioritization

Initial prioritization should be deterministic and evidence-backed.

Consider:

- frequency;
- customer impact;
- operational effort;
- predictability of the current workflow;
- expected automation value;
- evidence confidence;
- safety risk;
- policy constraints;
- external dependency risk.

Do not optimize a single score as if it were evidence.

Store the factors separately.

A ranking function may combine them, but the underlying evidence must remain inspectable.

---

## 14. Safety and governance invariants

The system must preserve these rules.

1. Autopilot cannot directly change production agents.
2. Autopilot cannot grant tool authority.
3. Autopilot cannot bypass permissions, policy, or approvals.
4. Prompt changes cannot grant authority.
5. Skill changes cannot grant authority.
6. Tool authority changes must be explicit in a proposal.
7. Harness owns the resolved build graph.
8. Lab owns evaluated-failure diagnosis after candidate evaluation.
9. Autopilot owns operational problem diagnosis before candidate construction.
10. Source evidence remains immutable and source-owned.
11. Derived records keep exact evidence lineage.
12. Model output is untrusted until validated.
13. A recommendation is not a deployment command.
14. Human approval remains final for pilot and production decisions.
15. External business truth remains outside Autopilot.
16. No inferred skill-selection claim is allowed without an explicit authoritative signal.

---

# Build phases

## Phase 0 - Architecture baseline

### Goal

Create the repository foundation and lock the product boundaries.

### Tasks

- [ ] Add package scaffold.
- [ ] Add `pyproject.toml`.
- [ ] Configure pytest, Ruff, mypy, and formatting.
- [ ] Add CI quality checks.
- [ ] Add README with product boundary.
- [ ] Add architecture decision record for repository ownership boundaries.
- [ ] Define source package name `cx_autopilot`.
- [ ] Confirm Python version with the current connected repositories.

### Exit criteria

The repository imports, tests, type checks, lints, and formats with no product logic yet.

---

## Phase 1 - Core contracts and evidence lineage

### Goal

Define immutable domain contracts before implementing discovery logic.

### Tasks

- [ ] Implement `OperationalSignal`.
- [ ] Implement `Opportunity`.
- [ ] Implement `OpportunityCluster`.
- [ ] Implement `ProblemDiagnosis`.
- [ ] Implement exact versioned component reference contracts.
- [ ] Implement `AgentSystemInventorySnapshot`.
- [ ] Implement `ChangeStrategy`.
- [ ] Implement `ChangeProposal`.
- [ ] Implement `CandidateReference`.
- [ ] Implement `EvaluationReference`.
- [ ] Implement `PilotRecommendation`.
- [ ] Implement `DecisionRecord`.
- [ ] Add evidence-reference validation.
- [ ] Add immutable lineage validation.

### Exit criteria

All core records are typed, immutable, serializable, and independent of external SDKs.

---

## Phase 2 - Persistence

### Goal

Persist Autopilot-owned state without duplicating source-system data.

### Tasks

- [ ] Define storage ports.
- [ ] Add SQLite adapter.
- [ ] Persist all core domain records.
- [ ] Preserve timestamps and exact references.
- [ ] Add transaction boundaries.
- [ ] Add repository behavior tests.

### Exit criteria

All core records round-trip through storage with stable identity and lineage.

---

## Phase 3 - CX Platform evidence adapter

### Goal

Read operational evidence from the CX Platform through a stable adapter.

### Tasks

- [ ] Inspect current AI-native CX Platform evidence/export contracts.
- [ ] Define a minimal Autopilot evidence port.
- [ ] Implement the CX Platform adapter.
- [ ] Normalize conversations, events, outcomes, escalations, and execution references into `OperationalSignal`.
- [ ] Preserve source references instead of copying large payloads.
- [ ] Link CX execution references to Harness execution/trace references when available.
- [ ] Add deterministic local fixtures for tests.

### Exit criteria

Autopilot can ingest a complete operational journey for the reference case.

---

## Phase 4 - Opportunity discovery

### Goal

Detect useful automation opportunities from normalized operational evidence.

### Initial discovery rules

Implement deterministic detection for:

- repeated action sequences;
- repeated escalations;
- repeat-contact patterns;
- repeated tool or operation lookup patterns;
- repeated approval waits;
- repeated policy denials;
- repeated human workaround patterns.

### Tasks

- [ ] Implement rule-based detectors.
- [ ] Produce `Opportunity` records with evidence refs.
- [ ] Keep detector outputs explainable.
- [ ] Add threshold configuration through typed settings.
- [ ] Add behavior tests.

### Exit criteria

The transaction-history reference evidence produces a real opportunity without model-only inference.

---

## Phase 5 - Opportunity clustering and prioritization

### Goal

Group repeated opportunities and rank them for diagnosis.

### Tasks

- [ ] Implement deterministic clustering keys for the first reference patterns.
- [ ] Preserve contributing opportunity IDs.
- [ ] Calculate separate frequency, impact, confidence, and risk factors.
- [ ] Add deterministic prioritization.
- [ ] Do not hide evidence behind one opaque score.

### Exit criteria

Repeated transaction-history evidence becomes one prioritized cluster with inspectable factors.

---

## Phase 6 - Agent-system inventory adapter

### Goal

Inspect the current governed agent graph before proposing changes.

### Tasks

- [ ] Inspect the current Harness registry and manifest APIs.
- [ ] Implement Harness inventory port.
- [ ] Read exact agent, prompt, skill, tool, and policy references.
- [ ] Read registry snapshot identity.
- [ ] Read resolved manifest provenance when available.
- [ ] Preserve exact versions.
- [ ] Add tests for direct tool authority versus skill tool dependencies.

### Exit criteria

Autopilot can state exactly what the current support agent has and does not have.

---

## Phase 7 - Operational problem diagnosis

### Goal

Classify why the operational opportunity exists.

### Tasks

- [ ] Implement the diagnosis taxonomy.
- [ ] Add deterministic diagnosis rules where evidence is sufficient.
- [ ] Add bounded model-assisted diagnosis only for unresolved cases.
- [ ] Validate model output against the typed taxonomy.
- [ ] Preserve supporting and conflicting evidence.
- [ ] Require an inventory snapshot for agent-system diagnoses.
- [ ] Add confidence rules.

### Reference acceptance

The transaction-history case must classify as:

```text
TOOL_GAP
```

because the existing payment-related skill exists but the required executable operation is absent.

### Exit criteria

Autopilot produces a reviewable evidence-backed `ProblemDiagnosis`.

---

## Phase 8 - Change strategy selection

### Goal

Choose the smallest valid change strategy.

### Tasks

- [ ] Implement `REUSE` rules.
- [ ] Implement `EXTEND` rules.
- [ ] Implement `COMPOSE` rules.
- [ ] Implement `CREATE` rules.
- [ ] Implement `NO_CHANGE` rules.
- [ ] Prefer reuse before extension, extension before composition, and composition before creation when all are safe and sufficient.
- [ ] Block strategy selection when evidence is insufficient.

### Reference acceptance

The transaction-history case must select:

```text
EXTEND
```

### Exit criteria

The strategy is deterministic, explainable, and evidence-linked.

---

## Phase 9 - Typed change proposal

### Goal

Produce a bounded exact proposal that another system can build and evaluate.

### Tasks

- [ ] Define typed component-change operations.
- [ ] Require exact versioned baseline component references.
- [ ] Require explicit target component relationships.
- [ ] Keep skill dependency changes separate from agent tool authority changes.
- [ ] Keep prompt changes separate from authority.
- [ ] Add risk classification.
- [ ] Add human-review requirement.
- [ ] Add proposal validation.

### Reference acceptance

The transaction-history proposal should make the minimum change:

```text
ADD_AGENT_TOOL_REF get_transaction_history@<exact-version>
```

Update the relevant skill dependency only when the inspected skill contract requires that dependency change.

Do not create a new agent or skill for this reference case.

### Exit criteria

The proposal is complete enough for governed candidate construction without hidden assumptions.

---

## Phase 10 - Harness candidate construction integration

### Goal

Turn an approved Autopilot proposal into a governed evaluation candidate.

### Tasks

- [ ] Implement a Harness construction port.
- [ ] Use current public Harness contracts.
- [ ] Build through Harness registry/factory boundaries.
- [ ] Do not construct authority outside Harness.
- [ ] Capture `ResolvedAgentManifest`.
- [ ] Capture manifest digest and registry snapshot identity.
- [ ] Validate proposed versus resolved component graph.
- [ ] Store only `CandidateReference` and evidence needed by Autopilot.

### Exit criteria

Autopilot obtains a governed candidate with exact manifest provenance.

---

## Phase 11 - Improvement Lab evaluation integration

### Goal

Evaluate the governed candidate without duplicating Lab logic.

### Tasks

- [ ] Inspect the current Improvement Lab public API.
- [ ] Define a minimal evaluation port.
- [ ] Submit baseline and candidate references/evidence.
- [ ] Receive evaluation and comparison references.
- [ ] Preserve Lab evidence IDs.
- [ ] Do not reproduce Lab root-cause or promotion logic in Autopilot.
- [ ] Handle evaluation failure as a first-class outcome.

### Exit criteria

Autopilot can request evaluation and retain exact Lab evaluation/comparison evidence references.

---

## Phase 12 - Pilot recommendation

### Goal

Convert operational opportunity evidence and Lab evaluation results into a controlled pilot recommendation.

### Tasks

- [ ] Implement recommendation rules.
- [ ] Require successful evaluation evidence for agent-system changes.
- [ ] Include expected operational impact.
- [ ] Include known risks.
- [ ] Include bounded pilot scope.
- [ ] Include rollback conditions.
- [ ] Include candidate manifest provenance.
- [ ] Require human approval.

### Exit criteria

The transaction-history reference case ends with a reviewable pilot recommendation, not deployment.

---

## Phase 13 - Human decision and audit trail

### Goal

Record the final decision without turning Autopilot into a deployment system.

### Tasks

- [ ] Implement `DecisionRecord` persistence.
- [ ] Support approve, reject, request-change, and close outcomes.
- [ ] Preserve actor and evidence references.
- [ ] Prevent an approval record from directly invoking production deployment.
- [ ] Add audit retrieval.

### Exit criteria

The complete decision path is traceable from source evidence to final human decision.

---

## Phase 14 - End-to-end reference scenario

### Goal

Prove the full architecture with one realistic enterprise case.

### Scenario

Repeated transaction-history lookup.

### Required assertions

- [ ] CX evidence is ingested with source references.
- [ ] Repeated lookup pattern becomes one opportunity cluster.
- [ ] Current support agent inventory is inspected.
- [ ] Existing relevant skill is detected.
- [ ] Missing `get_transaction_history` executable authority is detected.
- [ ] Diagnosis is `TOOL_GAP`.
- [ ] Strategy is `EXTEND`.
- [ ] Proposal adds the exact tool reference and no unnecessary new component.
- [ ] Harness builds a governed candidate.
- [ ] Proposed graph matches the resolved manifest.
- [ ] Lab evaluates baseline and candidate.
- [ ] Autopilot receives evaluation/comparison evidence.
- [ ] Autopilot creates a pilot recommendation.
- [ ] Human approval is required.
- [ ] No production deployment occurs.

### Exit criteria

The complete reference slice runs deterministically in tests.

---

## Phase 15 - Secondary diagnosis acceptance cases

### Goal

Prove that the taxonomy distinguishes different problem types.

Add small deterministic fixtures for:

### SKILL_GAP

Payment tools exist, but no duplicate-charge resolution skill exists.

Expected:

```text
SKILL_GAP
```

### PROMPT_GAP

Required skill and tools exist, but repeated evidence shows a correctable behavioral-instruction failure.

Expected:

```text
PROMPT_GAP
```

### POLICY_CONSTRAINT

A required operation is explicitly denied by policy.

Expected:

```text
POLICY_CONSTRAINT
```

### BUSINESS_DEPENDENCY

The external business service is unavailable.

Expected:

```text
BUSINESS_DEPENDENCY
```

### Exit criteria

Autopilot does not confuse agent-system gaps with external constraints.

---

## Phase 16 - Documentation and operational interface

### Goal

Make the system inspectable without adding unnecessary product surface.

### Tasks

- [ ] Document architecture and boundaries.
- [ ] Document domain contracts.
- [ ] Document evidence lineage.
- [ ] Document diagnosis taxonomy.
- [ ] Document change strategy semantics.
- [ ] Document Harness and Lab integration contracts.
- [ ] Add a small CLI for local reference workflows and audit inspection if useful.
- [ ] Do not add a frontend during this build.

### Exit criteria

A reviewer can understand and run the reference workflow from repository documentation.

---

## Phase 17 - Quality gate and cleanup

### Goal

Finish with one clean architecture and no migration residue.

### Required checks

Run the repository-configured equivalents of:

```text
python -m ruff format --check src tests examples
python -m ruff check src tests examples
python -m mypy src
python -m compileall -q src tests examples
python -m pytest -q
git diff --check
```

### Cleanup

- [ ] Remove unused abstractions.
- [ ] Remove placeholder adapters.
- [ ] Remove duplicated taxonomy logic.
- [ ] Remove dead compatibility code.
- [ ] Confirm plans and docs match implementation.

### Exit criteria

All configured quality checks pass with no known failures.

---

## 18. Testing strategy

Tests must focus on public behavior.

Prioritize:

- exact evidence lineage;
- deterministic opportunity discovery;
- diagnosis correctness;
- exact versioned inventory references;
- strategy selection;
- proposal minimality;
- skill versus tool distinction;
- prompt versus authority distinction;
- Harness manifest validation;
- Lab evidence references;
- human approval boundary;
- negative paths;
- external-service failure handling.

Do not test:

- private helper functions;
- source-file text;
- import inventories;
- arbitrary implementation constants;
- internal container shapes that do not protect behavior.

---

## 19. Non-goals

Do not build:

- a customer-support chatbot;
- another agent runtime;
- another evaluation framework;
- another policy engine;
- another approval engine;
- production deployment automation;
- autonomous self-modification;
- unbounded agent generation;
- unbounded prompt optimization;
- business truth storage;
- a duplicate CX event store;
- a duplicate Harness registry;
- a duplicate Improvement Lab failure taxonomy;
- a frontend during the initial Autopilot build.

---

## 20. Final acceptance criteria

The initial CX Autopilot build is complete when all statements are true:

- CX Platform evidence is ingested through an adapter.
- Operational evidence preserves source lineage.
- Opportunities are discovered from deterministic signals.
- Opportunity clusters preserve frequency, impact, confidence, and evidence separately.
- The diagnosis taxonomy is implemented exactly.
- Agent-system inventory uses exact versioned component references.
- `SKILL_GAP` and `TOOL_GAP` are distinct in behavior and tests.
- Strategy selection is separate from diagnosis.
- `REUSE`, `EXTEND`, `COMPOSE`, `CREATE`, and `NO_CHANGE` are implemented.
- Change proposals are typed, minimal, and evidence-backed.
- Skill dependencies do not grant tool authority.
- Prompt changes do not grant authority.
- Harness owns candidate construction and resolved build provenance.
- Autopilot validates proposal intent against the Harness resolved manifest.
- Improvement Lab owns candidate evaluation and evaluated-failure diagnosis.
- Autopilot stores exact evaluation/comparison references instead of duplicating Lab logic.
- Pilot recommendations contain operational evidence, candidate provenance, evaluation evidence, risk, scope, and rollback conditions.
- Human approval is required before any pilot or production action.
- The repeated transaction-history case works end to end.
- Secondary diagnosis fixtures prove the taxonomy boundary.
- The repository quality gate passes.
- Current documentation matches the implemented system.
