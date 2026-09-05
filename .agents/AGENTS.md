# CX Autopilot Agent Instructions

## Product boundary

CX Autopilot is an enterprise system that discovers worthwhile CX automation opportunities and proposes governed agent-system changes.

It does not directly modify or deploy production agents.

The system must preserve these ownership boundaries:

- The CX Platform owns operational CX records, events, outcomes, and source evidence.
- CX Autopilot owns opportunity discovery, problem diagnosis, inventory analysis, change strategy, change proposals, and pilot recommendations.
- Enterprise Agent Harness owns governed component construction, runtime authority, permissions, policies, approvals, tool execution, and resolved build provenance.
- Enterprise Agent Improvement Lab owns candidate evaluation, evaluated-failure diagnosis, comparison, regression evidence, and promotion evidence.
- A human remains the final authority for pilot and production decisions.

Enterprise Agent Harness:
https://github.com/etimbukafia/enterprise-agent-harness

Enterprise Agent Improvement Lab:
https://github.com/etimbukafia/enterprise-agent_improvement_lab

AI-native CX Platform:
https://github.com/etimbukafia/AI-native-CX-platform

## Engineering rules

- Write documentation in ASD-STE100 Issue 9 Simplified Technical English.
- Keep modules small, explicit, typed, and testable.
- Prefer the simplest architecture that fully meets the current requirement.
- Do not add speculative abstractions, compatibility layers, duplicate frameworks, or stopgap architecture.
- External systems must stay behind adapters.
- Keep domain contracts independent of external SDKs and runtime-specific model types.
- Treat model output as untrusted proposals. Deterministic code owns validation and control flow.
- Keep source evidence immutable. Preserve lineage with exact evidence references.
- Keep score, inference, and evidence separate.
- Do not let a diagnosis or recommendation create execution authority.
- Do not infer facts that are not present in source evidence or authoritative system state.
- Ask before making a consequential architecture choice that the current contracts and plan do not resolve.
- Do not ask for routine implementation choices that can be resolved from the existing architecture.
- Research the codebase and current external documentation when a contract is unclear. Do not guess.

## Domain rules

Use this problem-diagnosis taxonomy for Autopilot-owned operational analysis:

- `SKILL_GAP`
- `PROMPT_GAP`
- `AGENT_GAP`
- `TOOL_GAP`
- `POLICY_CONSTRAINT`
- `APPROVAL_FRICTION`
- `BUSINESS_DEPENDENCY`
- `DATA_QUALITY_ISSUE`
- `KNOWLEDGE_SOURCE_ISSUE`

Keep change strategy separate from diagnosis:

- `REUSE`
- `EXTEND`
- `COMPOSE`
- `CREATE`
- `NO_CHANGE`

A Skill is reusable job competence.
A Tool is an atomic executable operation.
A Prompt is a versioned behavioral instruction artifact.
An Agent composes prompt, skills, tools, policies, and runtime configuration.
Policy determines what may execute.

Skill dependencies do not grant tool authority.
Prompt content does not grant tool, policy, permission, approval, principal, or tenant authority.

## Integration rules

- Use exact versioned component identities when Autopilot reads or proposes agent-system changes.
- Do not reconstruct Harness build truth when a resolved Harness manifest exists.
- Do not duplicate Improvement Lab failure taxonomy, root-cause logic, candidate evaluation, comparison, or promotion logic.
- Do not embed production credentials or business secrets in Autopilot artifacts.
- Do not copy full prompt text into operational evidence unless a specific reviewed workflow requires it.
- Do not claim a skill was selected unless an authoritative runtime signal explicitly says so.

## Testing rules

- Test behavior through public boundaries.
- Test evidence lineage, exact references, deterministic decisions, and safety boundaries.
- Test negative paths and authority boundaries.
- Do not test private helper implementation, source-file inventories, import text, or arbitrary internal constants.
- Keep external integrations replaceable with fakes or deterministic local adapters in tests.

## Change discipline

- Work only on the requested phase or batch unless a required dependency is missing.
- Keep plans and current implementation status aligned.
- Remove obsolete migration residue instead of leaving permanent dual architecture.
- Do not introduce backward compatibility before a real compatibility requirement exists.
- Finish changes with repository tests, lint, formatting, type checks, compile checks, and `git diff --check` when those tools are configured.
