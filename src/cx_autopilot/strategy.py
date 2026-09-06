"""Deterministic change eligibility, strategy selection, and proposals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime

from .contracts import (
    AgentSystemInventorySnapshot,
    ChangeProposal,
    ChangeStrategy,
    ChangeTarget,
    ComponentChange,
    ComponentChangeOperation,
    ComponentType,
    DiagnosisType,
    ExactComponentReference,
    OperationalDisposition,
    ProblemDiagnosis,
)

_NO_CHANGE_DIAGNOSES = {
    DiagnosisType.BUSINESS_DEPENDENCY,
    DiagnosisType.POLICY_CONSTRAINT,
    DiagnosisType.APPROVAL_FRICTION,
    DiagnosisType.DATA_QUALITY_ISSUE,
    DiagnosisType.KNOWLEDGE_SOURCE_ISSUE,
}

_COMPONENT_DIAGNOSES = {
    DiagnosisType.AGENT_GAP,
    DiagnosisType.TOOL_GAP,
    DiagnosisType.SKILL_GAP,
    DiagnosisType.PROMPT_GAP,
}

_COMPONENT_GROUPS = {
    ComponentType.AGENT: "agent_refs",
    ComponentType.PROMPT: "prompt_refs",
    ComponentType.SKILL: "skill_refs",
    ComponentType.TOOL: "tool_refs",
    ComponentType.POLICY: "policy_refs",
}
_USABLE_LIFECYCLES = {"ACTIVE", "VALIDATED"}


class ChangePlanningError(ValueError):
    """Raised when a diagnosed change cannot be represented exactly."""


class ChangePlanner:
    """Select the smallest safe path and construct only explicit operations.

    The planner is deliberately provider-neutral. It consumes the exact
    inventory facts produced by an integration adapter and requires callers to
    provide any new exact component identities. It never invents a version,
    registers a component, or constructs a Harness candidate.
    """

    def select(
        self,
        diagnosis: ProblemDiagnosis,
        inventory: AgentSystemInventorySnapshot | None = None,
        *,
        target_agent_ref: ExactComponentReference | None = None,
        required_prompt_ref: ExactComponentReference | None = None,
        required_skill_ref: ExactComponentReference | None = None,
        required_tool_ref: ExactComponentReference | None = None,
        composition_available: bool = False,
    ) -> tuple[ChangeTarget, ChangeStrategy]:
        """Return the target and strategy without constructing a proposal."""

        if diagnosis.diagnosis_type in _NO_CHANGE_DIAGNOSES:
            return ChangeTarget.NO_CHANGE, ChangeStrategy.NO_CHANGE
        if diagnosis.diagnosis_type not in _COMPONENT_DIAGNOSES:
            raise ChangePlanningError(
                f"unsupported diagnosis type: {diagnosis.diagnosis_type.value}"
            )
        checked_inventory = _require_inventory(diagnosis, inventory)
        agent = _resolve_ref(
            target_agent_ref,
            diagnosis.affected_agent_refs,
            ComponentType.AGENT,
            "target Agent",
            inventory=checked_inventory,
        )

        if diagnosis.diagnosis_type is DiagnosisType.AGENT_GAP:
            if agent is None or not _available(checked_inventory, agent):
                return ChangeTarget.AGENT, ChangeStrategy.CREATE
            if diagnosis.precedence_rule == "agent_composition" or composition_available:
                return ChangeTarget.AGENT, ChangeStrategy.COMPOSE
            return ChangeTarget.AGENT, ChangeStrategy.EXTEND

        if diagnosis.diagnosis_type is DiagnosisType.TOOL_GAP:
            tool = _resolve_ref(
                required_tool_ref,
                diagnosis.affected_tool_refs,
                ComponentType.TOOL,
                "required Tool",
                inventory=checked_inventory,
            )
            if tool is None:
                raise ChangePlanningError(
                    "TOOL_GAP requires an exact required_tool_ref before selection"
                )
            if agent is not None and _authority_exists(checked_inventory, agent, tool):
                return ChangeTarget.TOOL, ChangeStrategy.REUSE
            if _available(checked_inventory, tool):
                return ChangeTarget.TOOL, ChangeStrategy.EXTEND
            return ChangeTarget.TOOL, ChangeStrategy.CREATE

        if diagnosis.diagnosis_type is DiagnosisType.SKILL_GAP:
            skill = _resolve_ref(
                required_skill_ref,
                diagnosis.affected_skill_refs,
                ComponentType.SKILL,
                "required Skill",
                inventory=checked_inventory,
            )
            if skill is None:
                raise ChangePlanningError(
                    "SKILL_GAP requires an exact required_skill_ref before selection"
                )
            tool = _resolve_ref(
                required_tool_ref,
                diagnosis.affected_tool_refs,
                ComponentType.TOOL,
                "required dependency Tool",
                inventory=checked_inventory,
            )
            if _available(checked_inventory, skill):
                if tool is None or _skill_satisfies(checked_inventory, skill, tool, agent):
                    return ChangeTarget.SKILL, ChangeStrategy.REUSE
                return ChangeTarget.SKILL, ChangeStrategy.EXTEND
            return ChangeTarget.SKILL, ChangeStrategy.CREATE

        prompt = _resolve_prompt(
            required_prompt_ref,
            diagnosis,
            checked_inventory,
            agent,
        )
        if prompt is None:
            raise ChangePlanningError(
                "PROMPT_GAP requires an exact prompt reference or an inspected Agent prompt"
            )
        if _available(checked_inventory, prompt):
            return ChangeTarget.PROMPT, ChangeStrategy.EXTEND
        return ChangeTarget.PROMPT, ChangeStrategy.CREATE

    def plan(
        self,
        diagnosis: ProblemDiagnosis,
        inventory: AgentSystemInventorySnapshot | None = None,
        *,
        opportunity_id: str | None = None,
        cluster_id: str | None = None,
        target_agent_ref: ExactComponentReference | None = None,
        target_agent_before_ref: ExactComponentReference | None = None,
        target_agent_after_ref: ExactComponentReference | None = None,
        agent_after_ref: ExactComponentReference | None = None,
        required_prompt_ref: ExactComponentReference | None = None,
        prompt_after_ref: ExactComponentReference | None = None,
        required_skill_ref: ExactComponentReference | None = None,
        skill_after_ref: ExactComponentReference | None = None,
        required_tool_ref: ExactComponentReference | None = None,
        tool_after_ref: ExactComponentReference | None = None,
        skill_dependency: str = "required",
        composition_available: bool = False,
        risk_classification: str = "REVIEW_REQUIRED",
        created_at: datetime | None = None,
    ) -> ChangeProposal | OperationalDisposition:
        """Return an exact proposal or a terminal operational disposition.

        ``*_after_ref`` values are mandatory whenever an existing governed
        component is versioned by an operation. This method intentionally does
        not derive a version from a before reference.
        """

        agent_before = _coalesce_ref(
            target_agent_ref,
            target_agent_before_ref,
            "target_agent_ref and target_agent_before_ref",
        )
        agent_after = _coalesce_ref(
            target_agent_after_ref,
            agent_after_ref,
            "target_agent_after_ref and agent_after_ref",
        )
        target, strategy = self.select(
            diagnosis,
            inventory,
            target_agent_ref=agent_before,
            required_prompt_ref=required_prompt_ref,
            required_skill_ref=required_skill_ref,
            required_tool_ref=required_tool_ref,
            composition_available=composition_available,
        )
        if target is ChangeTarget.NO_CHANGE or strategy in {
            ChangeStrategy.NO_CHANGE,
            ChangeStrategy.REUSE,
        }:
            return self._disposition(
                diagnosis,
                strategy=strategy,
                created_at=created_at,
            )

        checked_inventory = _require_inventory(diagnosis, inventory)
        _validate_baseline_scope(diagnosis, checked_inventory)
        if not risk_classification.strip():
            raise ChangePlanningError("risk_classification must not be blank")
        exact_agent = _resolve_ref(
            agent_before,
            diagnosis.affected_agent_refs,
            ComponentType.AGENT,
            "target Agent",
            inventory=checked_inventory,
        )
        prompt = _resolve_prompt(
            required_prompt_ref,
            diagnosis,
            checked_inventory,
            exact_agent,
        )
        skill = _resolve_ref(
            required_skill_ref,
            diagnosis.affected_skill_refs,
            ComponentType.SKILL,
            "required Skill",
        )
        tool = _resolve_ref(
            required_tool_ref,
            diagnosis.affected_tool_refs,
            ComponentType.TOOL,
            "required Tool",
        )
        exact_agent_after = _validate_after_ref(
            agent_after,
            ComponentType.AGENT,
            "target_agent_after_ref",
            checked_inventory.source_system,
        )
        operations: tuple[ComponentChange, ...]
        if target is ChangeTarget.AGENT:
            operations = self._agent_operations(
                strategy,
                exact_agent,
                exact_agent_after,
                risk_classification,
            )
        elif target is ChangeTarget.TOOL:
            operations = self._tool_operations(
                strategy,
                checked_inventory,
                exact_agent,
                exact_agent_after,
                tool,
                tool_after_ref,
            )
        elif target is ChangeTarget.SKILL:
            operations = self._skill_operations(
                strategy,
                checked_inventory,
                exact_agent,
                exact_agent_after,
                skill,
                skill_after_ref,
                tool,
                skill_dependency,
            )
        elif target is ChangeTarget.PROMPT:
            operations = self._prompt_operations(
                strategy,
                checked_inventory,
                exact_agent,
                prompt,
                prompt_after_ref,
            )
        else:  # pragma: no cover - selection exhausts the target enum
            raise ChangePlanningError(f"unsupported change target: {target.value}")

        proposal_identity = {
            "tenant_id": diagnosis.tenant_id,
            "opportunity_id": opportunity_id,
            "cluster_id": cluster_id or diagnosis.cluster_id,
            "diagnosis_id": diagnosis.diagnosis_id,
            "target": target.value,
            "strategy": strategy.value,
            "baseline_inventory_snapshot_id": checked_inventory.snapshot_id,
            "operations": [_operation_identity(operation) for operation in operations],
        }
        proposal_id = _stable_id("proposal", proposal_identity)
        target_agents = _target_agents(exact_agent, exact_agent_after, operations)
        return ChangeProposal(
            proposal_id=proposal_id,
            tenant_id=diagnosis.tenant_id,
            opportunity_id=opportunity_id,
            cluster_id=None if opportunity_id is not None else (cluster_id or diagnosis.cluster_id),
            diagnosis_id=diagnosis.diagnosis_id,
            change_target=target,
            strategy=strategy,
            baseline_inventory_snapshot_id=checked_inventory.snapshot_id,
            target_agent_refs=target_agents,
            proposed_component_changes=operations,
            rationale=_proposal_rationale(target, strategy, operations),
            evidence_refs=diagnosis.supporting_evidence_refs,
            risk_classification=risk_classification,
            requires_human_review=True,
            created_at=created_at or diagnosis.created_at,
        )

    def _agent_operations(
        self,
        strategy: ChangeStrategy,
        before: ExactComponentReference | None,
        after: ExactComponentReference | None,
        risk_classification: str,
    ) -> tuple[ComponentChange, ...]:
        if strategy is ChangeStrategy.CREATE:
            return (
                ComponentChange(
                    operation=ComponentChangeOperation.CREATE_AGENT,
                    subject_after_ref=_required_after(after, ComponentType.AGENT),
                    rationale=(
                        "Create the explicitly requested exact Agent version; human review is "
                        f"required under {risk_classification}."
                    ),
                ),
            )
        operation = (
            ComponentChangeOperation.COMPOSE_AGENT
            if strategy is ChangeStrategy.COMPOSE
            else ComponentChangeOperation.EXTEND_AGENT
        )
        return (
            ComponentChange(
                operation=operation,
                subject_before_ref=_required_before(before, ComponentType.AGENT),
                subject_after_ref=_required_after(after, ComponentType.AGENT),
                rationale="Version the existing Agent with the bounded diagnosed change.",
            ),
        )

    def _tool_operations(
        self,
        strategy: ChangeStrategy,
        inventory: AgentSystemInventorySnapshot,
        agent: ExactComponentReference | None,
        agent_after: ExactComponentReference | None,
        tool: ExactComponentReference | None,
        tool_after: ExactComponentReference | None,
    ) -> tuple[ComponentChange, ...]:
        exact_tool = _required_ref(tool, ComponentType.TOOL, "required_tool_ref")
        if strategy is ChangeStrategy.EXTEND:
            if not _available(inventory, exact_tool):
                raise ChangePlanningError("TOOL EXTEND requires an available exact Tool")
            return (self._agent_tool_change(agent, agent_after, exact_tool),)
        new_tool = _validate_after_ref(
            tool_after,
            ComponentType.TOOL,
            "tool_after_ref",
            inventory.source_system,
        )
        operations = [
            ComponentChange(
                operation=ComponentChangeOperation.CREATE_TOOL,
                subject_after_ref=_required_after(new_tool, ComponentType.TOOL),
                rationale="Create the explicitly requested exact Tool version.",
            )
        ]
        if agent is not None:
            operations.append(
                self._agent_tool_change(
                    agent,
                    agent_after,
                    _required_after(new_tool, ComponentType.TOOL),
                )
            )
        return tuple(operations)

    def _skill_operations(
        self,
        strategy: ChangeStrategy,
        inventory: AgentSystemInventorySnapshot,
        agent: ExactComponentReference | None,
        agent_after: ExactComponentReference | None,
        skill: ExactComponentReference | None,
        skill_after: ExactComponentReference | None,
        tool: ExactComponentReference | None,
        dependency: str,
    ) -> tuple[ComponentChange, ...]:
        exact_skill = _required_ref(skill, ComponentType.SKILL, "required_skill_ref")
        if dependency not in {"required", "optional"}:
            raise ChangePlanningError("skill_dependency must be 'required' or 'optional'")
        if strategy is ChangeStrategy.EXTEND and tool is not None:
            new_skill = _validate_after_ref(
                skill_after,
                ComponentType.SKILL,
                "skill_after_ref",
                inventory.source_system,
            )
            operation = (
                ComponentChangeOperation.ADD_SKILL_REQUIRED_TOOL_REF
                if dependency == "required"
                else ComponentChangeOperation.ADD_SKILL_OPTIONAL_TOOL_REF
            )
            return (
                ComponentChange(
                    operation=operation,
                    subject_before_ref=exact_skill,
                    subject_after_ref=_required_after(new_skill, ComponentType.SKILL),
                    related_after_ref=tool,
                    rationale="Version the Skill and record its exact Tool dependency.",
                ),
            )
        if strategy is ChangeStrategy.EXTEND:
            return (
                self._agent_skill_change(
                    _required_before(agent, ComponentType.AGENT),
                    _required_after(agent_after, ComponentType.AGENT),
                    exact_skill,
                ),
            )
        new_skill = _validate_after_ref(
            skill_after,
            ComponentType.SKILL,
            "skill_after_ref",
            inventory.source_system,
        )
        operations = [
            ComponentChange(
                operation=ComponentChangeOperation.CREATE_SKILL,
                subject_after_ref=_required_after(new_skill, ComponentType.SKILL),
                rationale="Create the explicitly requested exact Skill version.",
            )
        ]
        if agent is not None:
            operations.append(
                self._agent_skill_change(
                    _required_before(agent, ComponentType.AGENT),
                    _required_after(agent_after, ComponentType.AGENT),
                    _required_after(new_skill, ComponentType.SKILL),
                )
            )
        return tuple(operations)

    def _prompt_operations(
        self,
        strategy: ChangeStrategy,
        inventory: AgentSystemInventorySnapshot,
        agent: ExactComponentReference | None,
        prompt: ExactComponentReference | None,
        prompt_after: ExactComponentReference | None,
    ) -> tuple[ComponentChange, ...]:
        exact_prompt = _required_ref(prompt, ComponentType.PROMPT, "required_prompt_ref")
        exact_agent = _required_before(agent, ComponentType.AGENT)
        new_prompt = _validate_after_ref(
            prompt_after,
            ComponentType.PROMPT,
            "prompt_after_ref",
            inventory.source_system,
        )
        operations: list[ComponentChange] = []
        if strategy is ChangeStrategy.CREATE:
            operations.append(
                ComponentChange(
                    operation=ComponentChangeOperation.CREATE_PROMPT,
                    subject_after_ref=_required_after(new_prompt, ComponentType.PROMPT),
                    rationale="Create the explicitly requested exact Prompt version.",
                )
            )
        operations.append(
            ComponentChange(
                operation=ComponentChangeOperation.CHANGE_AGENT_PROMPT_REF,
                subject_before_ref=exact_prompt,
                subject_after_ref=_required_after(new_prompt, ComponentType.PROMPT),
                related_before_ref=exact_agent,
                rationale="Change only the Agent's explicit Prompt reference.",
            )
        )
        return tuple(operations)

    @staticmethod
    def _agent_tool_change(
        agent: ExactComponentReference | None,
        agent_after: ExactComponentReference | None,
        tool: ExactComponentReference,
    ) -> ComponentChange:
        return ComponentChange(
            operation=ComponentChangeOperation.ADD_AGENT_TOOL_REF,
            subject_before_ref=_required_before(agent, ComponentType.AGENT),
            subject_after_ref=_required_after(agent_after, ComponentType.AGENT),
            related_after_ref=tool,
            rationale="Add only the exact direct executable Tool authority.",
        )

    @staticmethod
    def _agent_skill_change(
        agent: ExactComponentReference,
        agent_after: ExactComponentReference,
        skill: ExactComponentReference,
    ) -> ComponentChange:
        return ComponentChange(
            operation=ComponentChangeOperation.ADD_AGENT_SKILL_REF,
            subject_before_ref=agent,
            subject_after_ref=agent_after,
            related_after_ref=skill,
            rationale="Add the exact Skill to the Agent composition explicitly.",
        )

    @staticmethod
    def _disposition(
        diagnosis: ProblemDiagnosis,
        *,
        strategy: ChangeStrategy,
        created_at: datetime | None,
    ) -> OperationalDisposition:
        reason, owner, action = _disposition_details(diagnosis.diagnosis_type, strategy)
        identity = {
            "tenant_id": diagnosis.tenant_id,
            "diagnosis_id": diagnosis.diagnosis_id,
            "strategy": strategy.value,
            "reason": reason,
        }
        return OperationalDisposition(
            disposition_id=_stable_id("disposition", identity),
            tenant_id=diagnosis.tenant_id,
            diagnosis_id=diagnosis.diagnosis_id,
            reason=reason,
            owner_boundary=owner,
            recommended_action=action,
            evidence_refs=diagnosis.supporting_evidence_refs,
            status="NO_CANDIDATE",
            created_at=created_at or diagnosis.created_at,
        )


def select_change_path(
    diagnosis: ProblemDiagnosis,
    inventory: AgentSystemInventorySnapshot | None = None,
    **kwargs: object,
) -> tuple[ChangeTarget, ChangeStrategy]:
    """Convenience boundary for deterministic target/strategy selection."""

    return ChangePlanner().select(diagnosis, inventory, **kwargs)  # type: ignore[arg-type]


def plan_change(
    diagnosis: ProblemDiagnosis,
    inventory: AgentSystemInventorySnapshot | None = None,
    **kwargs: object,
) -> ChangeProposal | OperationalDisposition:
    """Convenience boundary for exact proposals or no-change dispositions."""

    return ChangePlanner().plan(diagnosis, inventory, **kwargs)  # type: ignore[arg-type]


def _require_inventory(
    diagnosis: ProblemDiagnosis,
    inventory: AgentSystemInventorySnapshot | None,
) -> AgentSystemInventorySnapshot:
    if inventory is None:
        raise ChangePlanningError("component-gap planning requires an inventory snapshot")
    if inventory.tenant_id != diagnosis.tenant_id:
        raise ChangePlanningError("diagnosis and inventory must belong to the same tenant")
    if diagnosis.inventory_snapshot_id != inventory.snapshot_id:
        raise ChangePlanningError("diagnosis must use the supplied baseline inventory snapshot")
    return inventory


def _validate_baseline_scope(
    diagnosis: ProblemDiagnosis,
    inventory: AgentSystemInventorySnapshot,
) -> None:
    if diagnosis.inventory_snapshot_id != inventory.snapshot_id:
        raise ChangePlanningError("proposal baseline must match diagnosis inventory snapshot")


def _resolve_ref(
    explicit: ExactComponentReference | None,
    affected: Iterable[ExactComponentReference],
    expected: ComponentType,
    label: str,
    *,
    inventory: AgentSystemInventorySnapshot | None = None,
) -> ExactComponentReference | None:
    if explicit is not None:
        _check_type(explicit, expected, label)
        if inventory is not None and explicit.source_system != inventory.source_system:
            raise ChangePlanningError(f"{label} must use source_system {inventory.source_system!r}")
        return explicit
    candidates = tuple(ref for ref in affected if ref.component_type is expected)
    if len(candidates) > 1:
        raise ChangePlanningError(f"{label} is ambiguous; supply one exact reference")
    if candidates:
        if inventory is not None and candidates[0].source_system != inventory.source_system:
            raise ChangePlanningError(f"{label} must use source_system {inventory.source_system!r}")
        return candidates[0]
    return None


def _resolve_prompt(
    explicit: ExactComponentReference | None,
    diagnosis: ProblemDiagnosis,
    inventory: AgentSystemInventorySnapshot,
    agent: ExactComponentReference | None,
) -> ExactComponentReference | None:
    prompt = _resolve_ref(
        explicit,
        diagnosis.affected_prompt_refs,
        ComponentType.PROMPT,
        "required Prompt",
        inventory=inventory,
    )
    if prompt is not None:
        return prompt
    if agent is None:
        return None
    prompts = tuple(
        edge.prompt_ref
        for edge in inventory.agent_to_prompt_edges
        if edge.agent_ref.identity == agent.identity
    )
    return prompts[0] if len(prompts) == 1 else None


def _available(
    inventory: AgentSystemInventorySnapshot,
    reference: ExactComponentReference,
) -> bool:
    if reference.source_system != inventory.source_system:
        return False
    group = getattr(inventory, _COMPONENT_GROUPS[reference.component_type])
    if reference.identity not in {ref.identity for ref in group}:
        return False
    return inventory.component_lifecycles.get(reference.identity, "ACTIVE") in _USABLE_LIFECYCLES


def _authority_exists(
    inventory: AgentSystemInventorySnapshot,
    agent: ExactComponentReference,
    tool: ExactComponentReference,
) -> bool:
    return any(
        edge.agent_ref.identity == agent.identity and edge.tool_ref.identity == tool.identity
        for edge in inventory.agent_to_tool_authority_edges
    )


def _skill_satisfies(
    inventory: AgentSystemInventorySnapshot,
    skill: ExactComponentReference,
    tool: ExactComponentReference,
    agent: ExactComponentReference | None,
) -> bool:
    dependency = any(
        edge.skill_ref.identity == skill.identity and edge.tool_ref.identity == tool.identity
        for edge in (
            *inventory.skill_to_required_tool_edges,
            *inventory.skill_to_optional_tool_edges,
        )
    )
    attached = agent is None or any(
        edge.agent_ref.identity == agent.identity and edge.skill_ref.identity == skill.identity
        for edge in inventory.agent_to_skill_edges
    )
    return dependency and attached


def _coalesce_ref(
    first: ExactComponentReference | None,
    second: ExactComponentReference | None,
    label: str,
) -> ExactComponentReference | None:
    if first is not None and second is not None and first != second:
        raise ChangePlanningError(f"conflicting values for {label}")
    return first or second


def _check_type(
    reference: ExactComponentReference,
    expected: ComponentType,
    label: str,
) -> None:
    if reference.component_type is not expected:
        raise ChangePlanningError(f"{label} must identify {expected.value}")


def _validate_after_ref(
    reference: ExactComponentReference | None,
    expected: ComponentType,
    label: str,
    source_system: str,
) -> ExactComponentReference | None:
    if reference is None:
        return None
    _check_type(reference, expected, label)
    if reference.source_system != source_system:
        raise ChangePlanningError(f"{label} must use source_system {source_system!r}")
    return reference


def _required_before(
    reference: ExactComponentReference | None,
    expected: ComponentType,
) -> ExactComponentReference:
    if reference is None:
        raise ChangePlanningError(f"an exact baseline {expected.value} reference is required")
    _check_type(reference, expected, "baseline reference")
    return reference


def _required_after(
    reference: ExactComponentReference | None,
    expected: ComponentType,
) -> ExactComponentReference:
    if reference is None:
        raise ChangePlanningError(f"an exact target {expected.value} reference is required")
    _check_type(reference, expected, "target reference")
    return reference


def _required_ref(
    reference: ExactComponentReference | None,
    expected: ComponentType,
    label: str,
) -> ExactComponentReference:
    if reference is None:
        raise ChangePlanningError(f"{label} must be exact")
    _check_type(reference, expected, label)
    return reference


def _target_agents(
    before: ExactComponentReference | None,
    after: ExactComponentReference | None,
    operations: Iterable[ComponentChange],
) -> tuple[ExactComponentReference, ...]:
    refs = {
        ref.identity: ref
        for ref in (before, after)
        if ref is not None and ref.component_type is ComponentType.AGENT
    }
    for operation in operations:
        for ref in (
            operation.subject_before_ref,
            operation.subject_after_ref,
            operation.related_before_ref,
            operation.related_after_ref,
        ):
            if ref is not None and ref.component_type is ComponentType.AGENT:
                refs[ref.identity] = ref
    return tuple(sorted(refs.values(), key=lambda ref: ref.identity))


def _operation_identity(operation: ComponentChange) -> dict[str, str | None]:
    return {
        "operation": operation.operation.value,
        "subject_before": _identity(operation.subject_before_ref),
        "subject_after": _identity(operation.subject_after_ref),
        "related_before": _identity(operation.related_before_ref),
        "related_after": _identity(operation.related_after_ref),
    }


def _identity(reference: ExactComponentReference | None) -> str | None:
    return None if reference is None else reference.identity


def _stable_id(prefix: str, value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:32]}"


def _proposal_rationale(
    target: ChangeTarget,
    strategy: ChangeStrategy,
    operations: Iterable[ComponentChange],
) -> str:
    operation_names = ", ".join(operation.operation.value for operation in operations)
    return (
        f"Select the smallest {strategy.value} change for {target.value}; "
        f"explicit operations: {operation_names}."
    )


def _disposition_details(
    diagnosis_type: DiagnosisType,
    strategy: ChangeStrategy,
) -> tuple[str, str, str]:
    if strategy is ChangeStrategy.REUSE:
        return (
            "The inspected governed graph already satisfies the diagnosed requirement.",
            "cx-autopilot",
            "Close the candidate path and continue monitoring evidence.",
        )
    details = {
        DiagnosisType.BUSINESS_DEPENDENCY: (
            "An external business dependency is the blocking cause.",
            "business-system-owner",
            "Restore or verify the external dependency, then collect new evidence.",
        ),
        DiagnosisType.POLICY_CONSTRAINT: (
            "A policy or permission constraint is the blocking cause.",
            "harness-governance-owner",
            "Review the governing policy; do not bypass it through a component proposal.",
        ),
        DiagnosisType.APPROVAL_FRICTION: (
            "Approval workflow friction is the blocking cause.",
            "approval-process-owner",
            "Resolve the approval workflow without bypassing approval authority.",
        ),
        DiagnosisType.DATA_QUALITY_ISSUE: (
            "Evidence quality is insufficient for a governed component change.",
            "cx-platform-data-owner",
            "Repair or complete source evidence and rerun diagnosis.",
        ),
        DiagnosisType.KNOWLEDGE_SOURCE_ISSUE: (
            "The required knowledge source is the blocking cause.",
            "knowledge-source-owner",
            "Repair or refresh the knowledge source and collect new evidence.",
        ),
    }
    try:
        return details[diagnosis_type]
    except KeyError as exc:
        raise ChangePlanningError(
            f"no terminal disposition exists for {diagnosis_type.value}"
        ) from exc


__all__ = [
    "ChangePlanner",
    "ChangePlanningError",
    "plan_change",
    "select_change_path",
]
