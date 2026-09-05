# CX Autopilot Architecture and Build Plan

Status: proposed implementation plan

## 1. Product goal

CX Autopilot is an enterprise system that inspects customer-experience operations, discovers worthwhile automation opportunities, determines whether an Agent, Tool, Skill, or Prompt change is justified, builds governed evaluation candidates through Enterprise Agent Harness, evaluates them through Enterprise Agent Improvement Lab, and produces a controlled pilot recommendation for human review.

CX Autopilot proposes system changes.

It does not directly modify or deploy production agents.

The governed change target for this build is intentionally limited to:

```text
AGENT
TOOL
SKILL
PROMPT
NO_CHANGE
```

Policy, approval, business dependency, data quality, and knowledge issues are valid diagnoses and constraints. They are not first-class Autopilot change targets in this build.

A key invariant is:

> Not every CX problem is an agent-system problem. Autopilot must be able to conclude that no Agent, Tool, Skill, or Prompt change is justified and terminate the candidate path without invoking Harness candidate construction or Improvement Lab candidate evaluation.

---

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
- evidence correlation and quality assessment;
- opportunity discovery;
- opportunity clustering;
- opportunity prioritization;
- operational problem diagnosis;
- exact Agent, Prompt, Skill, Tool, and Policy inventory analysis;
- Agent, Tool, Skill, Prompt, or `NO_CHANGE` change targeting;
- `REUSE`, `EXTEND`, `COMPOSE`, `CREATE`, or `NO_CHANGE` strategy selection;
- typed Agent, Tool, Skill, and Prompt change proposals;
- non-agent operational dispositions;
- candidate construction orchestration;
- evaluation orchestration;
- pilot recommendations;
- decision records and audit lineage.

### Enterprise Agent Harness

Repository:

https://github.com/etimbukafia/enterprise-agent-harness

Owns:

- governed component definitions;
- exact versioned Agent, Prompt, Skill, Tool, and Policy contracts;
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
Signal ingestion, correlation, and quality checks
        |
        v
Opportunity discovery
        |
        v
Opportunity clustering and prioritization
        |
        v
Agent-system inventory inspection
        |
        v
Operational problem diagnosis
        |
        v
Change eligibility + strategy selection
        |
        +-------------------------------------+
        |                                     |
        | Agent / Tool / Skill / Prompt       | NO_CHANGE to agent system
        | change justified                    |
        v                                     v
Typed ChangeProposal                  OperationalDisposition
        |                                     |
        v                                     v
Enterprise Agent Harness              Human/source-system owner
        |
        v
Governed evaluation candidate
+ ResolvedAgentManifest
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

Model output may suggest classifications or proposals, but typed validation and deterministic control logic decide what enters the next stage.

---

## 4. First reference case

The first end-to-end reference case is repeated transaction-history lookup.

### Operational pattern

CX evidence shows repeated customer-service work where agents must inspect transaction history.

The current support agent does not have `get_transaction_history` as executable authority.

The relevant payment skill already exists.

### Expected Autopilot flow

```text
Repeated transaction-history evidence
        |
        v
Opportunity discovered
        |
        v
Current support-agent inventory inspected
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
Change target = TOOL
        |
        v
EXTEND
        |
        v
ChangeProposal
  -> keep existing agent identity
  -> keep existing prompt unless evidence says otherwise
  -> keep existing skill unless its declared dependency must change
  -> add exact get_transaction_history tool authority
  -> update skill dependency only through an explicit skill change if required
        |
        v
Harness builds evaluation-scoped governed candidate
        |
        v
Resolved graph matches proposal intent
        |
        v
Lab evaluates and compares baseline versus candidate
        |
        v
PilotRecommendation
        |
        v
Human approval required
```

This case must prove that Autopilot selects the smallest valid Agent, Tool, Skill, or Prompt change instead of creating unnecessary components.

---

## 5. Domain model

The initial domain model should use immutable typed contracts.

### ExactComponentReference

Represents an exact provider-neutral component identity.

Required concepts:

```text
component_type
component_id
version
source_system
```

Allowed component types for change targeting:

```text
AGENT
PROMPT
SKILL
TOOL
```

Policy references may be captured for diagnosis and inventory, but policy is not a first-class Autopilot change target in this build.

Every Agent, Prompt, Skill, Tool, and Policy reference used for inventory or change proposals must be exact and versioned.

### OperationalSignal

Represents one normalized source-owned observation.

Required concepts:

```text
signal_id
source_system
source_record_type
source_record_id
source_record_version optional
signal_type
occurred_at
tenant_id
interaction_id optional
journey_id optional
customer_id optional
agent_id optional
execution_id optional
trace_id optional
source_reference
payload_reference or bounded normalized attributes
evidence_quality
evidence_refs
```

The signal must preserve the original source reference.

Do not copy large source payloads into Autopilot when a stable reference is enough.

### EvidenceQuality

Represents the quality of evidence used for downstream reasoning.

Allowed initial states:

```text
COMPLETE
PARTIAL
STALE
CONFLICTING
UNAVAILABLE
```

Evidence quality is not confidence.

### Opportunity

Represents one candidate automation opportunity supported by evidence.

Required concepts:

```text
opportunity_id
title
description
source_signal_ids
evidence_refs
frequency_estimate
impact_estimate
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
tenant_id
window_start
window_end
opportunity_ids
pattern_summary
evidence_refs
frequency
impact
confidence
risk_factors
```

Cluster identity and membership must be reproducible for the same evidence window.

### AgentSystemInventorySnapshot

Represents exact inspected governed state relevant to an opportunity.

Required concepts:

```text
snapshot_id
captured_at
tenant_id
agent_refs
prompt_refs
skill_refs
tool_refs
policy_refs
agent_to_prompt_edges
agent_to_skill_edges
agent_to_tool_authority_edges
skill_to_required_tool_edges
skill_to_optional_tool_edges
registry_snapshot_ids
manifest_refs
source_system
```

The snapshot must distinguish:

- agent references skill;
- skill depends on tool;
- agent has direct executable tool authority;
- policy permits or constrains execution.

These relationships are not interchangeable.

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
inventory_snapshot_id optional
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

### ChangeTarget

Allowed values:

```text
AGENT
TOOL
SKILL
PROMPT
NO_CHANGE
```

Change target answers:

> What type of governed component should change, if any?

### ChangeStrategy

Allowed values:

```text
REUSE
EXTEND
COMPOSE
CREATE
NO_CHANGE
```

Strategy answers:

> How should the selected change target be satisfied?

Strategy is separate from diagnosis and change target.

### ComponentChangeOperation

The initial explicit operations are:

```text
# Agent
CREATE_AGENT
COMPOSE_AGENT
EXTEND_AGENT

# Agent-tool authority
ADD_AGENT_TOOL_REF
REMOVE_AGENT_TOOL_REF

# Agent-skill composition
ADD_AGENT_SKILL_REF
REMOVE_AGENT_SKILL_REF

# Agent-prompt composition
CHANGE_AGENT_PROMPT_REF

# Tool
CREATE_TOOL

# Skill
CREATE_SKILL
ADD_SKILL_REQUIRED_TOOL_REF
ADD_SKILL_OPTIONAL_TOOL_REF
REMOVE_SKILL_TOOL_REF

# Prompt
CREATE_PROMPT

# No change
NO_CHANGE
```

Every operation must state the exact baseline component reference and the intended target relationship.

A Skill change must not imply agent tool authority.

A Prompt change must not imply authority.

### ChangeProposal

Represents the smallest bounded Agent, Tool, Skill, or Prompt change Autopilot recommends.

Required concepts:

```text
proposal_id
opportunity_id or cluster_id
diagnosis_id
change_target
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

A proposal exists only when an Agent, Tool, Skill, or Prompt change is justified.

### OperationalDisposition

Represents a valid Autopilot conclusion that no Agent, Tool, Skill, or Prompt candidate should be created.

Required concepts:

```text
disposition_id
diagnosis_id
strategy = NO_CHANGE
reason
owner_boundary
recommended_action
evidence_refs
status
created_at
```

Examples:

```text
BUSINESS_DEPENDENCY
  -> source/business-system owner action

DATA_QUALITY_ISSUE
  -> data owner action

KNOWLEDGE_SOURCE_ISSUE
  -> knowledge owner action

POLICY_CONSTRAINT
  -> governance review or accept-as-designed

APPROVAL_FRICTION
  -> approval-process owner review

insufficient evidence
  -> gather evidence / close
```

Creating an `OperationalDisposition` must not invoke Harness candidate construction or Lab candidate evaluation.

### CandidateReference

References the governed evaluation candidate produced through Enterprise Agent Harness.

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

Harness resolved manifest is authoritative for what was actually built.

### EvaluationReference

References Improvement Lab evaluation and comparison evidence.

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
success_criteria
rollback_conditions
evidence_refs
requires_human_approval
status
created_at
```

Pilot scope should be able to express:

```text
tenant or customer segment
interaction type
traffic percentage or case limit
time window
exact candidate agent version
success criteria
abort conditions
```

### DecisionRecord

Represents a controlled human decision.

Required concepts:

```text
decision_id
subject_type
subject_id
decision
actor_ref
occurred_at
reason
evidence_refs
```

`subject_type` may identify a pilot recommendation or operational disposition.

---

## 6. Evidence model

Autopilot must operate on the complete CX journey, not only conversation text.

Supported signal families should include:

### Conversation evidence

- customer messages;
- agent responses;
- source-owned intent or topic classification;
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

### Evidence rules

Autopilot must preserve source evidence references.

It may derive signals, opportunities, clusters, diagnoses, proposals, and recommendations, but every derived record must retain lineage to source evidence.

Score, rank, confidence, inference, and evidence are different concepts.

Autopilot must distinguish source facts from derived interpretation.

Example:

```text
Source fact:
get_payment was called.

Derived interpretation:
The agent was investigating a payment-history issue.
```

The derived interpretation must never replace the source fact.

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

## 8. Diagnosis semantics and precedence

### DATA_QUALITY_ISSUE

Use when source data is missing, inconsistent, invalid, conflicting, or too stale for reliable automation.

### BUSINESS_DEPENDENCY

Use when the required external business capability or service is unavailable or is the actual blocking dependency.

### POLICY_CONSTRAINT

Use when policy intentionally blocks the required action or workflow.

Do not classify an intentional policy control as a Tool or Skill gap.

### APPROVAL_FRICTION

Use when approval requirements are valid, but operational evidence shows avoidable delay or poor approval flow.

Autopilot must not bypass approval authority.

### KNOWLEDGE_SOURCE_ISSUE

Use when knowledge needed for safe resolution is absent, stale, contradictory, or inaccessible.

### AGENT_GAP

Use when the required governed actor or composition does not exist and the problem cannot be solved by safely extending an existing agent.

### SKILL_GAP

Use when the required reusable competence does not exist, even if the relevant atomic tools exist.

Example:

```text
payment tools exist
duplicate-charge resolution skill does not exist
```

### TOOL_GAP

Use when the required atomic executable operation does not exist or is not available as executable authority to the relevant agent, after external, policy, approval, knowledge, and data causes are excluded.

Example:

```text
payment skill exists
payment evidence tools exist
transaction-history operation is missing from executable authority
```

### PROMPT_GAP

Use when the required governed actor, competence, tools, and authority exist, but repeated evidence shows that the behavioral instructions cause incorrect or incomplete behavior.

### Diagnosis precedence

Autopilot should apply deterministic guards in this order when evidence supports them:

```text
1. Is evidence reliable enough?
   no -> DATA_QUALITY_ISSUE

2. Is an external business capability/service the blocker?
   yes -> BUSINESS_DEPENDENCY

3. Does policy intentionally block the required action?
   yes -> POLICY_CONSTRAINT

4. Is the valid approval flow the operational bottleneck?
   yes -> APPROVAL_FRICTION

5. Is required knowledge absent, stale, contradictory, or inaccessible?
   yes -> KNOWLEDGE_SOURCE_ISSUE

6. Does the required governed actor/composition exist?
   no -> AGENT_GAP

7. Does the required reusable competence exist?
   no -> SKILL_GAP

8. Does the required executable operation exist and does the agent have authority to use it?
   no -> TOOL_GAP

9. Are the needed components and authority present, but behavioral instructions repeatedly fail?
   yes -> PROMPT_GAP
```

Do not hide this precedence in a model prompt.

Model-assisted diagnosis may be used only after deterministic checks cannot resolve the case.

---

## 9. Change target and strategy semantics

### ChangeTarget.AGENT

Use when the governed actor or composition itself must be created, composed, or extended.

### ChangeTarget.TOOL

Use when an atomic executable operation must be created or the agent must gain or lose exact tool authority.

### ChangeTarget.SKILL

Use when reusable competence must be created or changed.

### ChangeTarget.PROMPT

Use when behavioral instructions must change while competence and authority remain sufficient.

### ChangeTarget.NO_CHANGE

Use when no Agent, Tool, Skill, or Prompt change is justified.

### REUSE

Select when existing Agent, Tool, Skill, or Prompt components already satisfy the opportunity without changing the governed graph.

### EXTEND

Select when an existing Agent, Tool, Skill, or Prompt should receive a bounded change.

### COMPOSE

Select when existing components should be assembled into a governed agent composition without creating unnecessary new primitives.

### CREATE

Select only when no existing component can safely satisfy the requirement through reuse, extension, or composition.

### NO_CHANGE

Select when:

- evidence is insufficient;
- the problem is external;
- policy is intentionally constraining the operation;
- approval design is the relevant owner boundary;
- data or knowledge quality is the blocker;
- expected value is too low;
- risk is too high;
- no Agent, Tool, Skill, or Prompt change is justified.

Prefer the smallest safe strategy.

---

## 10. Integration architecture

Use ports and adapters.

Suggested package layout:

```text
src/cx_autopilot/
  contracts/
  evidence/
  opportunities/
  inventory/
  diagnosis/
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

Reads exact registry/component state and submits governed evaluation-candidate construction requests.

The adapter must use current public contracts from:

https://github.com/etimbukafia/enterprise-agent-harness

Do not copy Harness runtime logic into Autopilot.

### Improvement Lab adapter

Submits governed candidates for evaluation and reads evaluation/comparison/promotion evidence.

The adapter must use current public contracts from:

https://github.com/etimbukafia/enterprise-agent_improvement_lab

Do not copy Lab failure taxonomy, evaluators, candidate builders, comparison, or promotion logic into Autopilot.

---

## 11. Storage

Start with SQLite for local deterministic development unless a repository requirement proves a different choice.

Persist:

- normalized operational signals;
- evidence-quality state;
- opportunities;
- opportunity clusters;
- diagnoses;
- inventory snapshots;
- change proposals;
- operational dispositions;
- candidate references;
- evaluation references;
- pilot recommendations;
- decision records;
- evidence lineage references.

Required storage behavior:

- source-record ingestion is idempotent;
- tenant scope is explicit;
- duplicate ingestion does not inflate frequency;
- derived records preserve immutable source references;
- writes use explicit transaction boundaries;
- raw secrets and credentials are never persisted;
- unnecessary prompt text and source payloads are not duplicated.

Use a forward-only schema during the initial build.

---

## 12. Orchestration model

Use explicit workflow stages.

Suggested states:

```text
EVIDENCE_COLLECTED
OPPORTUNITY_DISCOVERED
OPPORTUNITY_PRIORITIZED
INVENTORY_RESOLVED
DIAGNOSED
CHANGE_ELIGIBILITY_RESOLVED
STRATEGY_SELECTED

# Candidate branch
PROPOSAL_READY
CANDIDATE_BUILT
EVALUATION_REQUESTED
EVALUATED
EVALUATION_FAILED
PILOT_RECOMMENDED
AWAITING_HUMAN_DECISION
APPROVED
REJECTED
CLOSED

# No-change branch
DISPOSITION_READY
AWAITING_DISPOSITION_DECISION
DISPOSITION_ACCEPTED
DISPOSITION_REJECTED
CLOSED
```

Do not let model output skip required stages.

State transitions must be deterministic and auditable.

`EVALUATION_FAILED` must not automatically trigger autonomous candidate modification.

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

Store these factors separately.

A ranking function may combine them, but the underlying evidence and factors must remain inspectable.

Do not treat one ranking score as evidence.

---

## 14. Safety and governance invariants

The system must preserve these rules.

1. Autopilot cannot directly change production agents.
2. Autopilot cannot directly grant tool authority.
3. Autopilot cannot bypass permissions, policy, or approvals.
4. Prompt changes cannot grant authority.
5. Skill changes cannot grant authority.
6. Tool authority changes must be explicit in a proposal.
7. Skill dependency changes must be explicit and separate from agent tool authority.
8. Harness owns the resolved build graph.
9. Harness manifest provenance is authoritative for what was built.
10. Autopilot proposal lineage is authoritative for what was proposed.
11. Lab owns evaluated-failure diagnosis after candidate evaluation.
12. Autopilot owns operational problem diagnosis before candidate construction.
13. Source evidence remains immutable and source-owned.
14. Derived records keep exact evidence lineage.
15. Model output is untrusted until typed validation and deterministic checks accept it.
16. A recommendation is not a deployment command.
17. Human approval remains final for pilot and production decisions.
18. External business truth remains outside Autopilot.
19. No inferred skill-selection claim is allowed without an explicit authoritative signal.
20. Not every CX problem results in an Agent, Tool, Skill, or Prompt change.
21. Operational dispositions must not invoke Harness candidate construction or Lab candidate evaluation.
22. Evaluation failure must not cause autonomous self-modification.

---

# Build phases

## Phase 0 - Architecture baseline and external contract verification

### Goal

Create the repository foundation, lock the product boundaries, and verify the current external contracts before domain implementation begins.

### Tasks

- [ ] Add package scaffold.
- [ ] Add `pyproject.toml`.
- [ ] Configure pytest, Ruff, mypy, formatting, and compile checks.
- [ ] Add CI quality checks.
- [ ] Add README with the product boundary.
- [ ] Add an architecture decision record for repository ownership boundaries.
- [ ] Define source package name `cx_autopilot`.
- [ ] Confirm the Python version with the connected repositories.
- [ ] Inspect the current AI-native CX Platform evidence/export contracts.
- [ ] Inspect CX event identity, tenant identity, outcome identity, and execution/trace linkage.
- [ ] Inspect the current Enterprise Agent Harness public contracts at https://github.com/etimbukafia/enterprise-agent-harness.
- [ ] Verify current Agent, Prompt, Skill, Tool, Policy, ComponentReference, registry snapshot, AgentConfig, factory, and ResolvedAgentManifest contracts.
- [ ] Inspect the current Enterprise Agent Improvement Lab public contracts at https://github.com/etimbukafia/enterprise-agent_improvement_lab.
- [ ] Verify current candidate, evaluation, comparison, promotion-evidence, and manifest-reference contracts.
- [ ] Record only the external contracts required by the reference slice.
- [ ] Do not guess external field names or constructor behavior.

### Exit criteria

- The repository imports, tests, type checks, lints, formats, and compiles.
- The product ownership ADR is committed.
- The exact external contract boundaries required by the first reference slice are documented.
- No product logic exists yet.

---

## Phase 1 - Core contracts and exact evidence lineage

### Goal

Define immutable domain contracts before implementing discovery or diagnosis logic.

### Tasks

- [ ] Implement `ExactComponentReference`.
- [ ] Implement `EvidenceQuality`.
- [ ] Implement `OperationalSignal`.
- [ ] Implement `Opportunity`.
- [ ] Implement `OpportunityCluster`.
- [ ] Implement `AgentSystemInventorySnapshot`.
- [ ] Implement `ProblemDiagnosis`.
- [ ] Implement `ChangeTarget`.
- [ ] Implement `ChangeStrategy`.
- [ ] Implement `ComponentChangeOperation`.
- [ ] Implement `ChangeProposal`.
- [ ] Implement `OperationalDisposition`.
- [ ] Implement `CandidateReference`.
- [ ] Implement `EvaluationReference`.
- [ ] Implement `PilotRecommendation`.
- [ ] Implement `DecisionRecord`.
- [ ] Add evidence-reference validation.
- [ ] Add immutable lineage validation.
- [ ] Add exact-version validation for component references.
- [ ] Keep all contracts independent of external SDKs and Harness/Lab types.

### Exit criteria

All core records are typed, immutable, serializable, tenant-aware where required, exact where required, and evidence-linked.

---

## Phase 2 - Persistence and idempotency

### Goal

Persist Autopilot-owned state without duplicating source-system truth or inflating operational evidence.

### Tasks

- [ ] Define storage ports.
- [ ] Add SQLite adapter.
- [ ] Persist all core domain records.
- [ ] Preserve timestamps and exact references.
- [ ] Add explicit tenant scope.
- [ ] Add source-record uniqueness constraints.
- [ ] Make source ingestion idempotent.
- [ ] Add explicit transaction boundaries.
- [ ] Ensure duplicate ingestion does not create duplicate signals or frequency inflation.
- [ ] Add repository behavior tests.

### Exit criteria

- All core records round-trip through storage with stable identity and lineage.
- Re-ingesting the same source record creates no second logical observation.
- Tenant-scoped queries do not cross tenant boundaries.

---

## Phase 3 - CX evidence ingestion, correlation, and quality

### Goal

Read operational evidence from the CX Platform and normalize a complete, trustworthy operational journey.

### Tasks

- [ ] Implement the minimal CX Platform evidence port based on the verified Phase 0 contracts.
- [ ] Implement the CX Platform adapter.
- [ ] Normalize conversations, events, outcomes, escalations, and execution references into `OperationalSignal`.
- [ ] Preserve stable source identity.
- [ ] Preserve source references instead of copying large payloads.
- [ ] Correlate conversation, ticket, tool, approval, escalation, business operation, outcome, and repeat-contact evidence into a journey when supported by source identity.
- [ ] Do not assume `interaction_id` is the only correlation key unless the source contract guarantees it.
- [ ] Link CX execution references to Harness execution/trace references when available.
- [ ] Assign evidence-quality state from source facts.
- [ ] Keep source facts separate from derived interpretations.
- [ ] Add deterministic local fixtures for tests.

### Exit criteria

Autopilot can ingest, deduplicate, correlate, quality-assess, and trace the complete operational journey for the reference case without duplicating CX Platform truth.

---

## Phase 4 - Opportunity discovery

### Goal

Detect useful automation opportunities from normalized operational evidence.

### Initial deterministic detectors

Implement narrowly scoped detection for:

- repeated operation sequences;
- repeated escalations with the same cause;
- repeat contact after the same unresolved path;
- repeated tool or operation lookup patterns;
- repeated approval waits;
- repeated policy denials;
- repeated human workaround patterns;
- repeated operator corrections.

### Tasks

- [ ] Implement rule-based detectors.
- [ ] Produce `Opportunity` records with exact evidence refs.
- [ ] Keep detector outputs explainable.
- [ ] Add typed threshold configuration.
- [ ] Make repeated processing deterministic and idempotent.
- [ ] Add behavior tests.

### Reference acceptance

Repeated transaction-history lookup evidence produces a real `Opportunity` without model-only inference.

### Exit criteria

The same normalized evidence produces the same opportunity set every run.

---

## Phase 5 - Opportunity clustering and prioritization

### Goal

Group repeated opportunities and rank them for diagnosis without hiding evidence behind one score.

### Tasks

- [ ] Implement tenant-scoped deterministic clustering keys for the first reference patterns.
- [ ] Add explicit clustering windows.
- [ ] Preserve contributing opportunity IDs.
- [ ] Preserve stable cluster membership for the same evidence window.
- [ ] Calculate separate frequency, impact, confidence, operational-effort, predictability, and risk factors.
- [ ] Add deterministic prioritization.
- [ ] Persist the underlying factors and final rank separately.
- [ ] Add tests for window boundaries and duplicate evidence.

### Reference acceptance

Repeated transaction-history opportunities become one prioritized cluster with inspectable evidence and ranking factors.

### Exit criteria

A reviewer can explain both why the cluster exists and why it ranked where it did.

---

## Phase 6 - Governed agent-system inventory

### Goal

Inspect the current governed Agent, Prompt, Skill, Tool, and Policy graph before diagnosing an agent-system problem or proposing a change.

### Tasks

- [ ] Implement the Harness inventory port from the verified Phase 0 contracts.
- [ ] Read exact agent references.
- [ ] Read exact prompt references.
- [ ] Read exact skill references.
- [ ] Read exact tool references.
- [ ] Read exact policy references required for diagnosis.
- [ ] Read agent-to-prompt relationships.
- [ ] Read agent-to-skill relationships.
- [ ] Read direct agent tool authority.
- [ ] Read skill required-tool dependencies.
- [ ] Read skill optional-tool dependencies.
- [ ] Read lifecycle/active state where exposed.
- [ ] Read registry snapshot identity.
- [ ] Read resolved manifest provenance when available.
- [ ] Preserve exact versions.
- [ ] Add tests proving that skill dependency and direct tool authority are different facts.

### Exit criteria

Given an agent and opportunity, Autopilot can explain the exact governed component graph relevant to the opportunity.

---

## Phase 7 - Operational problem diagnosis and precedence

### Goal

Classify why the operational opportunity exists without confusing external constraints with Agent, Tool, Skill, or Prompt gaps.

### Tasks

- [ ] Implement the diagnosis taxonomy exactly.
- [ ] Implement deterministic diagnosis precedence guards.
- [ ] Use evidence quality before agent-system diagnosis.
- [ ] Check external business dependency before classifying a component gap.
- [ ] Check policy constraint before classifying a Tool or Skill gap.
- [ ] Check approval friction before component-gap classification where approval is the bottleneck.
- [ ] Check knowledge-source condition before component-gap classification.
- [ ] Check Agent existence/composition before Skill, Tool, and Prompt gaps.
- [ ] Check Skill existence before Tool gap.
- [ ] Check Tool existence and direct agent authority before Prompt gap.
- [ ] Add bounded model-assisted diagnosis only when deterministic guards cannot resolve the case.
- [ ] Validate model output against typed taxonomy.
- [ ] Preserve supporting and conflicting evidence.
- [ ] Require an inventory snapshot for `AGENT_GAP`, `SKILL_GAP`, `TOOL_GAP`, and `PROMPT_GAP`.
- [ ] Add explicit confidence rules.

### Reference acceptance

The transaction-history case classifies as:

```text
TOOL_GAP
```

because:

- evidence is reliable;
- the business service is available;
- policy is not the blocker;
- approval is not the blocker;
- the relevant agent exists;
- the relevant payment skill exists;
- the required executable operation is missing from agent authority.

### Exit criteria

Autopilot produces one reviewable primary `ProblemDiagnosis` through inspectable precedence rules.

---

## Phase 8 - Change eligibility and strategy selection

### Goal

Decide whether an Agent, Tool, Skill, or Prompt change is justified and choose the smallest safe strategy.

### Tasks

- [ ] Map eligible diagnoses to `ChangeTarget.AGENT`, `TOOL`, `SKILL`, or `PROMPT` only when evidence supports that target.
- [ ] Route non-agent causes to `ChangeTarget.NO_CHANGE` unless independent evidence supports a component change.
- [ ] Implement `REUSE` rules.
- [ ] Implement `EXTEND` rules.
- [ ] Implement `COMPOSE` rules.
- [ ] Implement `CREATE` rules.
- [ ] Implement `NO_CHANGE` rules.
- [ ] Prefer reuse before extension, extension before composition, and composition before creation when all are safe and sufficient.
- [ ] Block candidate-path selection when evidence is insufficient.
- [ ] Produce `OperationalDisposition` for no-change cases.
- [ ] Ensure `POLICY_CONSTRAINT` does not automatically become a policy-change proposal.
- [ ] Ensure `APPROVAL_FRICTION` does not bypass approval authority.

### Reference acceptance

The transaction-history case must produce:

```text
change_target = TOOL
strategy = EXTEND
```

### Exit criteria

Every diagnosed cluster deterministically enters either:

- the Agent/Tool/Skill/Prompt candidate branch; or
- the `OperationalDisposition` no-change branch.

---

## Phase 9 - Typed Agent, Tool, Skill, and Prompt change proposal

### Goal

Produce a bounded exact proposal that Harness can build without hidden graph mutations.

### Tasks

- [ ] Implement typed component-change operations.
- [ ] Require exact versioned baseline component references.
- [ ] Require explicit target component relationships.
- [ ] Keep Agent change operations explicit.
- [ ] Keep agent tool authority changes explicit.
- [ ] Keep agent skill-reference changes explicit.
- [ ] Keep agent prompt-reference changes explicit.
- [ ] Keep Skill tool-dependency changes explicit.
- [ ] Keep Prompt changes separate from authority.
- [ ] Add risk classification.
- [ ] Add human-review requirement.
- [ ] Add proposal validation.
- [ ] Reject proposals that contain implied or undeclared authority expansion.

### Reference acceptance

The transaction-history proposal should make the minimum change:

```text
change_target = TOOL
strategy = EXTEND
operation = ADD_AGENT_TOOL_REF
reference = get_transaction_history@<exact-version>
```

If the existing skill contract itself must change, add a separate explicit skill operation such as:

```text
ADD_SKILL_REQUIRED_TOOL_REF
```

or:

```text
ADD_SKILL_OPTIONAL_TOOL_REF
```

That skill change must produce a new exact skill version.

Do not create a new Agent, Skill, or Prompt for this reference case unless evidence proves it is necessary.

### Exit criteria

A human can inspect the proposal and state exactly which governed graph edges or components would change before a candidate is built.

---

## Phase 10 - Harness evaluation-candidate construction

### Goal

Turn a validated Autopilot proposal into a governed evaluation candidate without changing production authority.

### Tasks

- [ ] Implement the Harness construction port from the verified Phase 0 contracts.
- [ ] Use current public Harness contracts only.
- [ ] Build through Harness registry/factory boundaries.
- [ ] Construct the candidate in an evaluation-safe scope.
- [ ] Do not mutate the production agent or production registry as a side effect of candidate construction.
- [ ] Do not construct authority outside Harness.
- [ ] Capture `ResolvedAgentManifest`.
- [ ] Capture manifest ID.
- [ ] Capture manifest digest.
- [ ] Capture registry snapshot identity.
- [ ] Capture exact agent, prompt, skill, tool, and policy refs.
- [ ] Validate proposal intent against the resolved component graph.
- [ ] Fail candidate construction if a required resolved identity does not match proposal intent.
- [ ] Store only `CandidateReference` and evidence needed by Autopilot.

### Exit criteria

- Autopilot obtains a governed evaluation candidate with exact manifest provenance.
- Proposed graph intent matches the resolved Harness manifest.
- Production authority remains unchanged.

---

## Phase 11 - Improvement Lab candidate evaluation

### Goal

Evaluate the governed candidate without duplicating Improvement Lab logic or introducing autonomous self-improvement.

### Tasks

- [ ] Implement the minimal evaluation port from the verified Phase 0 contracts.
- [ ] Submit baseline candidate identity.
- [ ] Submit governed candidate identity.
- [ ] Submit evaluation case/data references required by the Lab contract.
- [ ] Submit relevant operational evidence references where supported.
- [ ] Preserve environment and manifest identity.
- [ ] Receive evaluation reference.
- [ ] Receive comparison reference.
- [ ] Preserve regression evidence references.
- [ ] Preserve promotion evidence reference when produced.
- [ ] Do not reproduce Lab root-cause logic in Autopilot.
- [ ] Do not reproduce Lab candidate builders in Autopilot.
- [ ] Do not reproduce Lab comparison or promotion logic in Autopilot.
- [ ] Handle evaluation failure as a first-class `EVALUATION_FAILED` outcome.
- [ ] Do not automatically modify the candidate after evaluation failure.

### Exit criteria

Autopilot knows whether the proposed Agent, Tool, Skill, or Prompt change survived evaluation without becoming the evaluator or self-improvement engine.

---

## Phase 12 - Pilot recommendation

### Goal

Combine operational opportunity evidence and Lab evaluation evidence into a controlled pilot recommendation.

### Required evidence chain

The recommendation must derive from:

```text
Opportunity/cluster evidence
+ ProblemDiagnosis
+ baseline inventory snapshot
+ ChangeProposal
+ Harness manifest provenance
+ Lab evaluation/comparison evidence
+ risk evidence
```

### Tasks

- [ ] Implement recommendation rules.
- [ ] Require successful evaluation evidence for Agent, Tool, Skill, or Prompt candidate changes.
- [ ] Include expected operational impact.
- [ ] Include known risks.
- [ ] Include bounded pilot scope.
- [ ] Include success criteria.
- [ ] Include machine-inspectable rollback/abort conditions where possible.
- [ ] Include exact candidate manifest provenance.
- [ ] Preserve all relevant evidence refs.
- [ ] Require human approval.

### Reference acceptance

The transaction-history case ends with a reviewable pilot recommendation, not deployment.

### Exit criteria

The recommendation contains enough evidence and exact provenance for a human to decide without reconstructing the chain manually.

---

## Phase 13 - Human decision and audit

### Goal

Record controlled decisions without turning Autopilot into a deployment system.

### Tasks

- [ ] Implement `DecisionRecord` persistence.
- [ ] Support approve, reject, request-change, and close outcomes for pilot recommendations.
- [ ] Support accept, reject, and close outcomes for operational dispositions.
- [ ] Preserve actor references.
- [ ] Preserve evidence references.
- [ ] Prevent an approval record from directly invoking production deployment.
- [ ] Add audit retrieval from final decision back to source evidence.

### Exit criteria

Every terminal decision can be traced from source evidence through derived records to the human decision.

---

## Phase 14 - Primary end-to-end transaction-history acceptance

### Goal

Prove the full architecture with one realistic Agent/Tool/Skill/Prompt change case.

### Scenario

Repeated transaction-history lookup.

### Required assertions

- [ ] CX evidence is ingested with stable source references.
- [ ] Duplicate ingestion does not duplicate signals.
- [ ] Evidence is correlated into the relevant journey.
- [ ] Repeated lookup pattern becomes one opportunity cluster.
- [ ] Current support-agent inventory is inspected.
- [ ] Existing relevant payment skill is detected.
- [ ] Missing `get_transaction_history` executable authority is detected.
- [ ] Diagnosis is `TOOL_GAP`.
- [ ] Change target is `TOOL`.
- [ ] Strategy is `EXTEND`.
- [ ] Proposal adds the exact tool reference and no unnecessary component.
- [ ] No new SkillDefinition is created unless the existing skill dependency actually requires a change.
- [ ] Harness builds an evaluation-scoped governed candidate.
- [ ] Proposal intent matches the resolved manifest.
- [ ] Lab evaluates baseline and candidate.
- [ ] Autopilot receives exact evaluation/comparison evidence references.
- [ ] Autopilot creates a pilot recommendation.
- [ ] Human approval is required.
- [ ] No production deployment occurs.
- [ ] Production agent authority remains unchanged after the full test.

### Exit criteria

The complete reference slice runs deterministically in tests and proves the governed Tool-change path end to end.

---

## Phase 15 - Taxonomy and no-change acceptance cases

### Goal

Prove that Autopilot distinguishes Agent, Tool, Skill, and Prompt change cases from external or governance causes and knows when not to build a candidate.

### AGENT_GAP

Required governed actor/composition does not exist and safe extension is insufficient.

Expected:

```text
change_target = AGENT
```

### SKILL_GAP

Payment tools exist, but no duplicate-charge resolution skill exists.

Expected:

```text
diagnosis = SKILL_GAP
change_target = SKILL
```

### PROMPT_GAP

Required Agent, Skill, Tool, and authority exist, but repeated evidence shows correctable behavioral-instruction failure.

Expected:

```text
diagnosis = PROMPT_GAP
change_target = PROMPT
```

### POLICY_CONSTRAINT

A required operation is intentionally denied by policy.

Expected:

```text
diagnosis = POLICY_CONSTRAINT
change_target = NO_CHANGE
OperationalDisposition created
no candidate constructed
```

### APPROVAL_FRICTION

Approval is valid, but operational evidence shows the approval process is the bottleneck.

Expected:

```text
diagnosis = APPROVAL_FRICTION
change_target = NO_CHANGE
OperationalDisposition created
no approval bypass
```

### BUSINESS_DEPENDENCY

The external business service is unavailable.

Expected:

```text
diagnosis = BUSINESS_DEPENDENCY
change_target = NO_CHANGE
OperationalDisposition created
no Harness candidate
no Lab evaluation
```

### DATA_QUALITY_ISSUE

Source evidence is missing, conflicting, or too stale for reliable diagnosis.

Expected:

```text
diagnosis = DATA_QUALITY_ISSUE
change_target = NO_CHANGE
OperationalDisposition created
no candidate constructed
```

### KNOWLEDGE_SOURCE_ISSUE

Required knowledge is missing, stale, contradictory, or inaccessible.

Expected:

```text
diagnosis = KNOWLEDGE_SOURCE_ISSUE
change_target = NO_CHANGE
OperationalDisposition created
```

### Exit criteria

Autopilot proves that:

- Agent, Tool, Skill, and Prompt gaps lead to the correct change targets;
- external and governance causes do not become fabricated agent changes;
- `NO_CHANGE` is a first-class correct outcome.

---

## Phase 16 - Documentation and minimal operational interface

### Goal

Make the system inspectable and runnable without adding unnecessary product surface.

### Tasks

- [ ] Document architecture and ownership boundaries.
- [ ] Document Agent/Tool/Skill/Prompt change scope.
- [ ] Document core domain contracts.
- [ ] Document evidence lineage.
- [ ] Document evidence-quality semantics.
- [ ] Document diagnosis taxonomy and precedence.
- [ ] Document change-target semantics.
- [ ] Document change-strategy semantics.
- [ ] Document Harness and Lab integration contracts.
- [ ] Document the no-change/disposition branch.
- [ ] Add a small CLI for local reference workflows and audit inspection.

### CLI scope

The CLI should support only the reference workflow and inspection needs, such as:

```text
ingest fixture
discover
inspect opportunity
inspect inventory
inspect diagnosis
inspect proposal
run reference cycle
inspect lineage
record decision
```

Do not create a general administration framework.

### Exit criteria

A reviewer can understand, run, and inspect the reference workflow from repository documentation and the minimal CLI.

---

## Phase 17 - Final quality gate and architecture cleanup

### Goal

Finish with one clean architecture and no duplicate Harness/Lab logic or obsolete scaffolding.

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

Adjust only when repository configuration requires a different exact command.

### Cleanup

- [ ] Remove unused abstractions.
- [ ] Remove placeholder adapters.
- [ ] Remove duplicated taxonomy logic.
- [ ] Remove dead compatibility code.
- [ ] Confirm current plans and docs match implementation.
- [ ] Manually review for copied Harness runtime/governance logic.
- [ ] Manually review for copied Improvement Lab evaluation/root-cause/comparison/promotion logic.
- [ ] Confirm no code path can turn a diagnosis or recommendation directly into production deployment.
- [ ] Confirm no code path can infer tool authority from Skill dependencies.
- [ ] Confirm no code path can infer skill selection without an authoritative signal.

### Exit criteria

All configured quality checks pass with no known failures, and the final architecture still matches the product boundary.

---

## 15. Testing strategy

Tests must focus on public behavior.

Prioritize:

- exact source evidence lineage;
- idempotent ingestion;
- journey correlation;
- evidence-quality handling;
- deterministic opportunity discovery;
- deterministic clustering windows;
- inspectable prioritization factors;
- exact versioned inventory references;
- direct tool authority versus Skill dependency;
- diagnosis precedence;
- Agent versus Tool versus Skill versus Prompt diagnosis correctness;
- change-target correctness;
- strategy selection;
- proposal minimality;
- explicit Skill dependency changes;
- Prompt versus authority distinction;
- Harness manifest validation;
- production-authority non-mutation during candidate build;
- Lab evaluation evidence references;
- evaluation-failure handling without self-modification;
- human approval boundary;
- no-change/disposition branch;
- external-service failure handling;
- tenant isolation.

Do not test:

- private helper functions;
- source-file text;
- import inventories;
- arbitrary implementation constants;
- internal container shapes that do not protect behavior.

---

## 16. Non-goals

Do not build:

- a customer-support chatbot;
- another agent runtime;
- another evaluation framework;
- another policy engine;
- another approval engine;
- policy-change automation as a first-class Autopilot target;
- approval-rule-change automation as a first-class Autopilot target;
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

## 17. Final acceptance criteria

The initial CX Autopilot build is complete when all statements are true:

- CX Platform evidence is ingested through an adapter.
- Operational evidence preserves stable source identity and lineage.
- Duplicate ingestion does not duplicate logical evidence.
- Journey evidence is correlated where source contracts support it.
- Evidence quality is explicit and separate from confidence.
- Opportunities are discovered from deterministic signals.
- Opportunity clusters are tenant-scoped, time-bounded, and reproducible.
- Prioritization factors remain inspectable and separate from the final rank.
- Agent-system inventory uses exact versioned component references.
- Inventory distinguishes Agent→Skill, Skill→Tool dependency, and Agent→Tool authority.
- The diagnosis taxonomy is implemented exactly.
- Diagnosis precedence prevents external, policy, approval, knowledge, and data causes from being misclassified as component gaps.
- `AGENT_GAP`, `SKILL_GAP`, `TOOL_GAP`, and `PROMPT_GAP` are distinct in behavior and tests.
- Change target is explicitly one of `AGENT`, `TOOL`, `SKILL`, `PROMPT`, or `NO_CHANGE`.
- Strategy selection is separate from diagnosis and change target.
- `REUSE`, `EXTEND`, `COMPOSE`, `CREATE`, and `NO_CHANGE` are implemented.
- Change proposals are typed, minimal, exact, and evidence-backed.
- Agent changes are explicit.
- Tool authority changes are explicit.
- Skill dependency changes are explicit and do not grant agent tool authority.
- Prompt changes do not grant authority.
- External/governance causes can terminate in `OperationalDisposition` without creating a candidate.
- Harness owns evaluation-candidate construction and resolved build provenance.
- Candidate construction does not mutate production authority.
- Autopilot validates proposal intent against the Harness resolved manifest.
- Improvement Lab owns candidate evaluation and evaluated-failure diagnosis.
- Autopilot stores exact Lab evaluation/comparison references instead of duplicating Lab logic.
- Evaluation failure does not trigger autonomous self-modification.
- Pilot recommendations contain operational evidence, candidate provenance, evaluation evidence, risk, scope, success criteria, and rollback conditions.
- Human approval is required before any pilot or production action.
- The repeated transaction-history Tool-change case works end to end.
- Secondary acceptance cases prove Agent, Skill, Prompt, and no-change boundaries.
- No production deployment occurs in the reference build.
- The repository quality gate passes.
- Current documentation matches the implemented system.
