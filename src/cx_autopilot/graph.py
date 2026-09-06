"""Provider-neutral exact Agent graph intent and validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from .contracts import (
    AgentSystemInventorySnapshot,
    CandidateReference,
    ChangeProposal,
    ComponentChangeOperation,
    ComponentType,
    ExactComponentReference,
)
from .contracts.common import unique_values


class GraphValidationError(ValueError):
    """Raised when a proposal and an exact resolved Agent graph disagree."""


@dataclass(frozen=True)
class AgentGraph:
    """Canonical exact graph owned by one Agent definition."""

    agent_ref: ExactComponentReference
    prompt_ref: ExactComponentReference
    skill_refs: tuple[ExactComponentReference, ...]
    tool_refs: tuple[ExactComponentReference, ...]
    policy_refs: tuple[ExactComponentReference, ...]

    def __post_init__(self) -> None:
        for field_name in ("skill_refs", "tool_refs", "policy_refs"):
            object.__setattr__(self, field_name, _canonical_refs(getattr(self, field_name)))


def validate_candidate_graph(
    proposal: ChangeProposal,
    inventory: AgentSystemInventorySnapshot,
    candidate: CandidateReference,
) -> None:
    """Validate a candidate against its proposal and exact inventory baseline."""

    if not isinstance(proposal, ChangeProposal):
        raise GraphValidationError("graph validation requires a ChangeProposal")
    if not isinstance(inventory, AgentSystemInventorySnapshot):
        raise GraphValidationError("graph validation requires an inventory snapshot")
    if not isinstance(candidate, CandidateReference):
        raise GraphValidationError("graph validation requires a CandidateReference")
    if proposal.tenant_id != inventory.tenant_id or proposal.tenant_id != candidate.tenant_id:
        raise GraphValidationError("proposal, inventory, and candidate must share a tenant")
    if proposal.baseline_inventory_snapshot_id != inventory.snapshot_id:
        raise GraphValidationError("proposal baseline does not match inventory snapshot")
    if candidate.proposal_id != proposal.proposal_id:
        raise GraphValidationError("candidate graph binding does not match the proposal")
    if candidate.baseline_inventory_snapshot_id != inventory.snapshot_id:
        raise GraphValidationError("candidate graph binding does not match the inventory")
    if candidate.resolved_graph_digest is None:
        raise GraphValidationError("candidate graph binding is missing")

    baseline_agent = _baseline_agent_from_proposal(proposal, inventory)
    baseline = graph_from_inventory(inventory, baseline_agent)
    expected = apply_proposal(proposal, inventory, baseline)
    actual = graph_from_candidate(candidate)
    require_graph_match(expected, actual)
    _require_created_components_in_graph(proposal, actual)
    if candidate.resolved_graph_digest != graph_digest(actual):
        raise GraphValidationError("candidate graph digest does not match the resolved graph")


def graph_from_inventory(
    inventory: AgentSystemInventorySnapshot,
    agent_ref: ExactComponentReference,
) -> AgentGraph:
    """Resolve one exact Agent graph from an immutable inventory snapshot."""

    if agent_ref.source_system != inventory.source_system:
        raise GraphValidationError("inventory baseline Agent source system is inconsistent")
    if agent_ref.identity not in {reference.identity for reference in inventory.agent_refs}:
        raise GraphValidationError("proposal baseline Agent is not in the inventory snapshot")
    prompts = tuple(
        edge.prompt_ref
        for edge in inventory.agent_to_prompt_edges
        if edge.agent_ref.identity == agent_ref.identity
    )
    if len(prompts) != 1:
        raise GraphValidationError("inventory must contain exactly one baseline Agent Prompt")
    graph = AgentGraph(
        agent_ref,
        prompts[0],
        tuple(
            edge.skill_ref
            for edge in inventory.agent_to_skill_edges
            if edge.agent_ref.identity == agent_ref.identity
        ),
        tuple(
            edge.tool_ref
            for edge in inventory.agent_to_tool_authority_edges
            if edge.agent_ref.identity == agent_ref.identity
        ),
        inventory.policy_refs,
    )
    _validate_graph_references(graph)
    return graph


def graph_from_candidate(candidate: CandidateReference) -> AgentGraph:
    """Resolve the complete graph represented by a CandidateReference."""

    return AgentGraph(
        candidate.agent_ref,
        candidate.prompt_ref,
        candidate.skill_refs,
        candidate.tool_refs,
        candidate.policy_refs,
    )


def apply_proposal(
    proposal: ChangeProposal,
    inventory: AgentSystemInventorySnapshot,
    baseline: AgentGraph,
) -> AgentGraph:
    """Apply every supported proposal operation to one exact baseline graph."""

    operations = proposal.proposed_component_changes
    agent_before_refs: list[ExactComponentReference] = []
    agent_after_refs: list[ExactComponentReference] = []
    skill_dependency_transitions: list[tuple[ExactComponentReference, ExactComponentReference]] = []
    for change in operations:
        for reference in (
            change.subject_before_ref,
            change.subject_after_ref,
            change.related_before_ref,
            change.related_after_ref,
        ):
            if reference is not None and reference.source_system != inventory.source_system:
                raise GraphValidationError(
                    f"proposal reference uses a different source system: {reference.identity}"
                )
        if change.operation in {
            ComponentChangeOperation.EXTEND_AGENT,
            ComponentChangeOperation.COMPOSE_AGENT,
        }:
            agent_before_refs.append(cast(ExactComponentReference, change.subject_before_ref))
            agent_after_refs.append(cast(ExactComponentReference, change.subject_after_ref))
        elif change.operation is ComponentChangeOperation.CREATE_AGENT:
            agent_after_refs.append(cast(ExactComponentReference, change.subject_after_ref))
        elif change.operation in {
            ComponentChangeOperation.ADD_AGENT_TOOL_REF,
            ComponentChangeOperation.REMOVE_AGENT_TOOL_REF,
            ComponentChangeOperation.ADD_AGENT_SKILL_REF,
            ComponentChangeOperation.REMOVE_AGENT_SKILL_REF,
        }:
            agent_before_refs.append(cast(ExactComponentReference, change.subject_before_ref))
            agent_after_refs.append(cast(ExactComponentReference, change.subject_after_ref))
        elif change.operation is ComponentChangeOperation.CHANGE_AGENT_PROMPT_REF:
            agent_before_refs.append(cast(ExactComponentReference, change.related_before_ref))
            agent_after_refs.append(cast(ExactComponentReference, change.related_after_ref))
        elif change.operation in {
            ComponentChangeOperation.ADD_SKILL_REQUIRED_TOOL_REF,
            ComponentChangeOperation.ADD_SKILL_OPTIONAL_TOOL_REF,
            ComponentChangeOperation.REMOVE_SKILL_TOOL_REF,
        }:
            skill_dependency_transitions.append(
                (
                    cast(ExactComponentReference, change.subject_before_ref),
                    cast(ExactComponentReference, change.subject_after_ref),
                )
            )

    baseline_agent_refs = _unique_identities(agent_before_refs)
    if baseline_agent_refs and baseline.agent_ref.identity not in baseline_agent_refs:
        raise GraphValidationError(
            "baseline Agent config does not match the proposal Agent before reference"
        )
    final_agent_refs = _unique_identities(agent_after_refs)
    if len(final_agent_refs) > 1:
        raise GraphValidationError("proposal contains more than one resulting Agent identity")
    final_agent = (
        next(
            reference for reference in agent_after_refs if reference.identity == final_agent_refs[0]
        )
        if final_agent_refs
        else baseline.agent_ref
    )

    prompt_ref = baseline.prompt_ref
    skill_refs = list(baseline.skill_refs)
    tool_refs = list(baseline.tool_refs)
    policy_refs = list(baseline.policy_refs)
    for change in operations:
        operation = change.operation
        if operation is ComponentChangeOperation.ADD_AGENT_TOOL_REF:
            _require_agent_subject(change, baseline.agent_ref)
            related = cast(ExactComponentReference, change.related_after_ref)
            if related.identity in {item.identity for item in tool_refs}:
                raise GraphValidationError("proposal adds a Tool already in Agent authority")
            tool_refs.append(related)
        elif operation is ComponentChangeOperation.REMOVE_AGENT_TOOL_REF:
            _require_agent_subject(change, baseline.agent_ref)
            related = cast(ExactComponentReference, change.related_before_ref)
            _remove_reference(tool_refs, related, "Agent Tool authority")
        elif operation is ComponentChangeOperation.ADD_AGENT_SKILL_REF:
            _require_agent_subject(change, baseline.agent_ref)
            related = cast(ExactComponentReference, change.related_after_ref)
            if related.identity in {item.identity for item in skill_refs}:
                raise GraphValidationError("proposal adds a Skill already in Agent composition")
            skill_refs.append(related)
        elif operation is ComponentChangeOperation.REMOVE_AGENT_SKILL_REF:
            _require_agent_subject(change, baseline.agent_ref)
            related = cast(ExactComponentReference, change.related_before_ref)
            _remove_reference(skill_refs, related, "Agent Skill composition")
        elif operation is ComponentChangeOperation.CHANGE_AGENT_PROMPT_REF:
            agent_before = cast(ExactComponentReference, change.related_before_ref)
            agent_after = cast(ExactComponentReference, change.related_after_ref)
            if agent_before != baseline.agent_ref:
                raise GraphValidationError("prompt change Agent before ref does not match config")
            if agent_after != final_agent:
                raise GraphValidationError("prompt change Agent after ref is inconsistent")
            prompt_before = cast(ExactComponentReference, change.subject_before_ref)
            prompt_after = cast(ExactComponentReference, change.subject_after_ref)
            if prompt_ref != prompt_before:
                raise GraphValidationError("prompt change baseline does not match config")
            prompt_ref = prompt_after
        elif operation in {
            ComponentChangeOperation.EXTEND_AGENT,
            ComponentChangeOperation.COMPOSE_AGENT,
        }:
            before = cast(ExactComponentReference, change.subject_before_ref)
            if before != baseline.agent_ref:
                raise GraphValidationError("Agent transition baseline does not match config")
        elif operation is ComponentChangeOperation.CREATE_AGENT:
            continue

    for old_skill, new_skill in skill_dependency_transitions:
        if old_skill.identity not in {item.identity for item in baseline.skill_refs}:
            continue
        has_remove = any(
            change.operation is ComponentChangeOperation.REMOVE_AGENT_SKILL_REF
            and change.related_before_ref == old_skill
            and change.subject_before_ref == baseline.agent_ref
            and change.subject_after_ref == final_agent
            for change in operations
        )
        has_add = any(
            change.operation is ComponentChangeOperation.ADD_AGENT_SKILL_REF
            and change.related_after_ref == new_skill
            and change.subject_before_ref == baseline.agent_ref
            and change.subject_after_ref == final_agent
            for change in operations
        )
        if not has_remove or not has_add:
            raise GraphValidationError(
                "a Skill version change must also version the Agent Skill graph edge"
            )

    final = AgentGraph(
        final_agent, prompt_ref, tuple(skill_refs), tuple(tool_refs), tuple(policy_refs)
    )
    _validate_graph_references(final)
    return final


def require_graph_match(expected: AgentGraph, actual: AgentGraph) -> None:
    """Require two canonical graphs to contain exactly the same references."""

    if expected == actual:
        return
    differences = []
    for name in ("agent_ref", "prompt_ref", "skill_refs", "tool_refs", "policy_refs"):
        if getattr(expected, name) != getattr(actual, name):
            differences.append(name)
    raise GraphValidationError(
        "resolved graph does not match proposal graph: " + ", ".join(differences)
    )


def graph_digest(value: AgentGraph | CandidateReference) -> str:
    """Return a stable digest for the complete resolved Agent graph."""

    graph = graph_from_candidate(value) if isinstance(value, CandidateReference) else value
    payload = {
        "agent_ref": graph.agent_ref.identity,
        "prompt_ref": graph.prompt_ref.identity,
        "skill_refs": _identities(graph.skill_refs),
        "tool_refs": _identities(graph.tool_refs),
        "policy_refs": _identities(graph.policy_refs),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _baseline_agent_from_proposal(
    proposal: ChangeProposal,
    inventory: AgentSystemInventorySnapshot,
) -> ExactComponentReference:
    inventory_agents = {reference.identity: reference for reference in inventory.agent_refs}
    before_refs = {
        reference.identity: reference
        for change in proposal.proposed_component_changes
        for reference in (
            change.subject_before_ref,
            change.related_before_ref,
        )
        if reference is not None and reference.component_type is ComponentType.AGENT
    }
    candidates = [
        inventory_agents[identity] for identity in before_refs if identity in inventory_agents
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise GraphValidationError("proposal has more than one baseline Agent in the inventory")

    declared = [
        reference
        for reference in proposal.target_agent_refs
        if reference.identity in inventory_agents
    ]
    if len(declared) == 1:
        return declared[0]
    if len(inventory_agents) == 1:
        return next(iter(inventory_agents.values()))
    raise GraphValidationError("proposal does not identify one exact baseline Agent graph")


def _validate_graph_references(graph: AgentGraph) -> None:
    if graph.agent_ref.component_type is not ComponentType.AGENT:
        raise GraphValidationError("candidate graph Agent reference is not an Agent")
    if graph.prompt_ref.component_type is not ComponentType.PROMPT:
        raise GraphValidationError("candidate graph Prompt reference is not a Prompt")
    for name, values, expected in (
        ("skill_refs", graph.skill_refs, ComponentType.SKILL),
        ("tool_refs", graph.tool_refs, ComponentType.TOOL),
        ("policy_refs", graph.policy_refs, ComponentType.POLICY),
    ):
        if any(reference.component_type is not expected for reference in values):
            raise GraphValidationError(f"candidate graph {name} has an invalid component type")
        try:
            unique_values(_identities(values), name)
        except ValueError as exc:
            raise GraphValidationError(str(exc)) from exc


def _require_created_components_in_graph(proposal: ChangeProposal, graph: AgentGraph) -> None:
    graph_identities = {
        ComponentType.AGENT: {graph.agent_ref.identity},
        ComponentType.PROMPT: {graph.prompt_ref.identity},
        ComponentType.SKILL: set(_identities(graph.skill_refs)),
        ComponentType.TOOL: set(_identities(graph.tool_refs)),
    }
    for change in proposal.proposed_component_changes:
        if change.operation is ComponentChangeOperation.CREATE_AGENT:
            expected_type = ComponentType.AGENT
        elif change.operation is ComponentChangeOperation.CREATE_PROMPT:
            expected_type = ComponentType.PROMPT
        elif change.operation is ComponentChangeOperation.CREATE_SKILL:
            expected_type = ComponentType.SKILL
        elif change.operation is ComponentChangeOperation.CREATE_TOOL:
            expected_type = ComponentType.TOOL
        else:
            continue
        created = change.subject_after_ref
        if created is None or created.identity not in graph_identities[expected_type]:
            raise GraphValidationError(
                f"created {expected_type.value} is not present in the resolved Agent graph"
            )


def _require_agent_subject(change: object, baseline_agent: ExactComponentReference) -> None:
    subject_before = cast(ExactComponentReference, _required_field(change, "subject_before_ref"))
    if subject_before != baseline_agent:
        raise GraphValidationError("Agent relationship baseline does not match config")


def _remove_reference(
    values: list[ExactComponentReference],
    reference: ExactComponentReference,
    label: str,
) -> None:
    for index, current in enumerate(values):
        if current == reference:
            values.pop(index)
            return
    raise GraphValidationError(f"proposal removes a missing {label} reference")


def _required_field(value: object, name: str) -> object:
    result = getattr(value, name, None)
    if result is None:
        raise GraphValidationError(f"graph field is missing: {name}")
    return result


def _unique_identities(values: Sequence[ExactComponentReference]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_identities(values)))


def _identities(values: Sequence[ExactComponentReference]) -> tuple[str, ...]:
    return tuple(value.identity for value in values)


def _canonical_refs(
    values: Sequence[ExactComponentReference],
) -> tuple[ExactComponentReference, ...]:
    return tuple(sorted(values, key=lambda value: value.identity))


__all__ = [
    "AgentGraph",
    "GraphValidationError",
    "apply_proposal",
    "graph_digest",
    "graph_from_candidate",
    "graph_from_inventory",
    "require_graph_match",
    "validate_candidate_graph",
]
