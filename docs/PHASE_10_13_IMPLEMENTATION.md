# Phases 10–13 implementation

This document records the candidate, evaluation, recommendation, and human
decision boundaries. Autopilot does not deploy or promote a production Agent.

## Phase 10: Harness candidate construction

`HarnessCandidateAdapter` accepts a validated `ChangeProposal`, the matching
`AgentSystemInventorySnapshot`, and a baseline Harness `AgentConfig` value.
The factory must use an evaluation registry. The adapter rejects a factory
that uses the supplied production registry.

The adapter applies only the exact operations in the proposal:

- Agent transitions change the exact Agent version.
- Agent Tool and Skill relationship operations change the corresponding
  exact lists.
- Prompt changes change both the Prompt reference and the exact Agent
  version.
- A Skill dependency change does not grant direct Agent Tool authority. If
  the baseline Agent uses the old Skill version, the proposal must also
  contain the old-edge removal and new-edge addition.

The adapter sends the resulting configuration to Harness with registration
and activation enabled in the evaluation scope. It then checks the resolved
manifest ID, digest, registry snapshot, Agent identity, Prompt reference,
Skill references, Tool references, and Policy references. A mismatch fails
construction. The provider-neutral graph validator also binds the resulting
graph to the proposal and baseline inventory snapshot with a stable graph
digest. Only `CandidateReference` and the opaque built result are returned to
the caller; production state is not called.

## Phase 11: Improvement Lab evaluation

`ImprovementLabEvaluationAdapter` accepts opaque Lab baseline and candidate
objects, their exact Autopilot `CandidateReference` values, the dataset, and
the two manifest values required by the runner. It calls the Lab runner once
for the baseline and once for the candidate. It calls the Lab comparator once
with both reports and their provenance.

The adapter does not implement evaluators, failure taxonomy, root-cause
analysis, comparison, or promotion logic. It preserves explicit Lab evidence
references, manifest provenance, and the candidate graph binding in
`EvaluationReference`.

If either run or the comparison fails, the result is terminal
`EVALUATION_FAILED`. The adapter does not retry, edit the candidate, or start
another evaluation. A successful run produces `EVALUATION_SUCCEEDED`; the
comparison verdict remains Lab-owned.

## Phase 12: Pilot recommendation

`PilotRecommender` requires the complete evidence chain:

```text
Opportunity or cluster
  → ProblemDiagnosis
  → baseline inventory
  → ChangeProposal
  → CandidateReference and Harness provenance
  → EvaluationReference and Lab comparison
  → explicit risk evidence
```

It accepts a recommendation only when the evaluation succeeded, the Lab
comparison verdict is `improved`, and the complete candidate graph matches the
proposal applied to the baseline inventory. Expected operational impact,
known risks, operational evidence, and risk evidence are supplied by the
caller. The recommender does not invent impact or risk values.

The pilot scope must name the exact candidate Agent and contain a finite
positive traffic, case, interaction, or time bound. Success criteria and
rollback conditions are required. The result is
`READY_FOR_HUMAN_APPROVAL` and always requires human approval.

## Phase 13: human decision and audit

`DecisionService` persists canonical human decisions:

- pilot: `APPROVE`, `REJECT`, `REQUEST_CHANGE`, or `CLOSE`;
- operational disposition: `ACCEPT`, `REJECT`, or `CLOSE`.

Every decision preserves the actor and evidence references. `audit()` follows
the decision to the recommendation or disposition, then to the proposal,
diagnosis, inventory, opportunity or cluster, candidate, and Lab evaluation
where those records exist. Approval only creates a `DecisionRecord`. It does
not call Harness, the Lab, a deployment system, or a promotion system.

## Behavior coverage

`tests/test_phases_10_13.py` covers exact manifest graph matching, Skill and
Prompt Agent version transitions, production isolation, Lab runner and
comparator boundaries, terminal evaluation failure, recommendation evidence
and scope rules, no-change exclusion, decision outcomes, and audit lineage.
