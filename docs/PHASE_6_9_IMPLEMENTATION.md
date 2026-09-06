# Phases 6–9 implementation

This document describes the governed inventory, diagnosis, change-path, and
proposal boundaries. These phases stop before Harness candidate construction
and before Improvement Lab evaluation.

## Harness inventory boundary

`HarnessInventoryAdapter` accepts a small read-only port with the public
Harness `AgentRegistry.snapshot(include_inactive=...)` shape. It requires an
explicit tenant and `ExactComponentReference` for every selected Agent. The
adapter records:

- exact Agent, Prompt, Skill, Tool, and Policy references;
- Agent→Prompt composition;
- Agent→Skill composition;
- direct Agent→Tool executable authority;
- Skill→Tool required dependency;
- Skill→Tool optional dependency;
- component lifecycle state;
- registry snapshot identity;
- optional resolved-manifest identity and digest.

The adapter can receive `required_component_refs` for an opportunity-specific
exact reference. This is important when a Tool exists in the Harness registry
but is not a direct Agent `tool_ref`: the inventory includes the Tool without
inventing an authority edge. Missing declared references are retained with a
`MISSING` lifecycle, so absence is inspectable rather than silently omitted.

No Harness class is imported into Autopilot contracts, and the adapter has no
registration, activation, build, or runtime side effect.

## Diagnosis and precedence

`OperationalDiagnoser` produces one typed `ProblemDiagnosis`. The fixed order
is:

1. evidence quality → `DATA_QUALITY_ISSUE`;
2. external business dependency → `BUSINESS_DEPENDENCY`;
3. policy or permission constraint → `POLICY_CONSTRAINT`;
4. approval bottleneck → `APPROVAL_FRICTION`;
5. knowledge-source condition → `KNOWLEDGE_SOURCE_ISSUE`;
6. Agent existence or composition → `AGENT_GAP`;
7. Skill existence → `SKILL_GAP`;
8. Tool existence or direct authority → `TOOL_GAP`;
9. behavioral instruction failure → `PROMPT_GAP`.

The first matching rule wins. Supporting and conflicting evidence references
remain on the diagnosis, and component-gap diagnoses require the exact
inventory snapshot ID. A bounded fallback may run only when deterministic
rules do not resolve the case; its output is validated against
`DiagnosisType`.

External, data, policy, approval, and knowledge causes remain diagnoses. They
are not converted into invented Agent, Tool, Skill, or Prompt gaps.

## Change target and strategy

`ChangePlanner.select` keeps diagnosis separate from the change path. The
component diagnosis mapping is:

| Diagnosis | Target | Selection rule |
| --- | --- | --- |
| `AGENT_GAP` | `AGENT` | `COMPOSE` for an existing composition gap, `EXTEND` for an existing Agent, otherwise `CREATE` |
| `TOOL_GAP` | `TOOL` | `REUSE` when direct authority already exists, `EXTEND` when the exact Tool exists, otherwise `CREATE` |
| `SKILL_GAP` | `SKILL` | `REUSE` when the Skill and dependency already satisfy the requirement, `EXTEND` for an existing Skill, otherwise `CREATE` |
| `PROMPT_GAP` | `PROMPT` | `EXTEND` for an existing Prompt, otherwise `CREATE` |

`BUSINESS_DEPENDENCY`, `POLICY_CONSTRAINT`, `APPROVAL_FRICTION`,
`DATA_QUALITY_ISSUE`, and `KNOWLEDGE_SOURCE_ISSUE` select
`NO_CHANGE`. `REUSE` also terminates without a mutation because the existing
graph already satisfies the requirement. Both paths produce an
`OperationalDisposition`; neither enters candidate construction.

## Exact proposals

`ChangePlanner.plan` creates the existing immutable `ChangeProposal` only for
a justified mutation. Every versioned operation requires an explicit exact
after reference. The planner never increments or otherwise derives a
version.

The transaction-history path is:

```text
TOOL_GAP -> TOOL -> EXTEND -> ADD_AGENT_TOOL_REF
```

The resulting operation states:

```text
subject_before_ref = support-agent@1.0.0
subject_after_ref  = support-agent@1.1.0
related_after_ref  = get_transaction_history@1.0.0
```

The Tool authority operation is separate from any Skill dependency operation.
A Skill dependency mutation instead uses
`ADD_SKILL_REQUIRED_TOOL_REF` or `ADD_SKILL_OPTIONAL_TOOL_REF` with an exact
before/after Skill identity. When the baseline Agent has the old exact Skill
reference, the proposal also contains `REMOVE_AGENT_SKILL_REF` for the old
reference and `ADD_AGENT_SKILL_REF` for the new reference. Both operations
carry the Agent's exact before/after versions.

Prompt changes use `CHANGE_AGENT_PROMPT_REF`. The operation carries both the
Prompt before/after references and the Agent before/after references. Prompt
changes do not grant Tool authority. Creation and relationship operations are
separate when both are needed. Proposals always require human review and
reject operations whose target or undeclared Agent relationship is
inconsistent.

Phase 10 now translates a validated proposal into an evaluation-scoped
Harness candidate and verifies the resolved manifest against the resulting
exact graph. Phase 11 submits the baseline and candidate to Improvement Lab
through an adapter. Candidate construction, evaluation, recommendation, and
human decision details are documented in
[`docs/PHASE_10_13_IMPLEMENTATION.md`](PHASE_10_13_IMPLEMENTATION.md).
