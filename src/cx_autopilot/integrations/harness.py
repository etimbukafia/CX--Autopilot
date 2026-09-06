"""Harness inventory adapter for exact, read-only graph inspection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol, cast

from ..contracts import (
    AgentPromptEdge,
    AgentSkillEdge,
    AgentSystemInventorySnapshot,
    AgentToolAuthorityEdge,
    ComponentType,
    ExactComponentReference,
    SkillToolDependencyEdge,
)
from ..contracts.common import aware_timestamp, non_blank, unique_values


class HarnessInventoryError(ValueError):
    """Raised when the public Harness inventory cannot be read exactly."""


class HarnessInventoryPort(Protocol):
    """Minimal read-only boundary implemented by the Harness AgentRegistry."""

    def snapshot(self, *, include_inactive: bool = False) -> object:
        """Return a Harness RegistrySnapshot."""


class HarnessInventoryAdapter:
    """Translate a Harness registry snapshot into Autopilot-owned facts.

    The adapter uses only the public ``snapshot`` method. It does not import
    Harness classes and it never registers, mutates, or activates a component.
    """

    def __init__(
        self,
        source: HarnessInventoryPort,
        *,
        tenant_id: str,
        source_system: str = "harness",
    ) -> None:
        self.source = source
        self.tenant_id = non_blank(tenant_id, "tenant_id")
        self.source_system = non_blank(source_system, "source_system")

    def inspect(
        self,
        agent_ref: ExactComponentReference,
        *,
        resolved_manifest: object | None = None,
        required_component_refs: Iterable[ExactComponentReference] = (),
        include_inactive: bool = True,
        captured_at: datetime | None = None,
        snapshot_id: str | None = None,
    ) -> AgentSystemInventorySnapshot:
        """Inspect one exact Agent and its reachable governed graph."""

        if agent_ref.component_type is not ComponentType.AGENT:
            raise HarnessInventoryError("agent_ref must identify an Agent")
        self._validate_source(agent_ref)
        return self.inspect_many(
            (agent_ref,),
            resolved_manifest=resolved_manifest,
            required_component_refs=required_component_refs,
            include_inactive=include_inactive,
            captured_at=captured_at,
            snapshot_id=snapshot_id,
        )

    def inspect_many(
        self,
        agent_refs: Iterable[ExactComponentReference],
        *,
        resolved_manifest: object | None = None,
        required_component_refs: Iterable[ExactComponentReference] = (),
        include_inactive: bool = True,
        captured_at: datetime | None = None,
        snapshot_id: str | None = None,
    ) -> AgentSystemInventorySnapshot:
        """Inspect several exact Agents in one immutable inventory snapshot."""

        requested_agents = _unique_refs(tuple(agent_refs), "agent_refs")
        if not requested_agents:
            raise HarnessInventoryError("at least one exact Agent reference is required")
        for agent_ref in requested_agents:
            if agent_ref.component_type is not ComponentType.AGENT:
                raise HarnessInventoryError("agent_refs must identify only Agents")
            self._validate_source(agent_ref)

        registry_snapshot = self.source.snapshot(include_inactive=include_inactive)
        registry_snapshot_id = _required_text(registry_snapshot, "snapshot_id")
        records = _index_records(registry_snapshot, self.source_system)
        selected_agents: list[tuple[ExactComponentReference, object]] = []
        for agent_ref in requested_agents:
            record = records[ComponentType.AGENT].get(_ref_key(agent_ref))
            if record is None:
                raise HarnessInventoryError(f"unknown exact Agent: {agent_ref.identity}")
            selected_agents.append((agent_ref, record))
        _validate_optional_tenant_fields(
            [record for _, record in selected_agents],
            self.tenant_id,
        )

        prompts: dict[str, ExactComponentReference] = {}
        skills: dict[str, ExactComponentReference] = {}
        tools: dict[str, ExactComponentReference] = {}
        policies: dict[str, ExactComponentReference] = {}
        agent_prompt_edges: list[AgentPromptEdge] = []
        agent_skill_edges: list[AgentSkillEdge] = []
        agent_tool_edges: list[AgentToolAuthorityEdge] = []
        required_skill_tools: list[SkillToolDependencyEdge] = []
        optional_skill_tools: list[SkillToolDependencyEdge] = []

        for selected_ref, agent in selected_agents:
            prompt = _component_ref(
                _required_field(agent, "prompt_ref"),
                ComponentType.PROMPT,
                self.source_system,
            )
            prompts[prompt.identity] = prompt
            agent_prompt_edges.append(AgentPromptEdge(agent_ref=selected_ref, prompt_ref=prompt))

            for value in _sequence_field(agent, "skill_refs"):
                skill = _component_ref(value, ComponentType.SKILL, self.source_system)
                skills[skill.identity] = skill
                agent_skill_edges.append(AgentSkillEdge(agent_ref=selected_ref, skill_ref=skill))

            for value in _sequence_field(agent, "tool_refs"):
                tool = _component_ref(value, ComponentType.TOOL, self.source_system)
                tools[tool.identity] = tool
                agent_tool_edges.append(
                    AgentToolAuthorityEdge(agent_ref=selected_ref, tool_ref=tool)
                )

            for value in _sequence_field(agent, "policy_refs"):
                policy = _component_ref(value, ComponentType.POLICY, self.source_system)
                policies[policy.identity] = policy

        for reference in _unique_refs(tuple(required_component_refs), "required_component_refs"):
            self._validate_source(reference)
            if reference.component_type is ComponentType.PROMPT:
                prompts[reference.identity] = reference
            elif reference.component_type is ComponentType.SKILL:
                skills[reference.identity] = reference
            elif reference.component_type is ComponentType.TOOL:
                tools[reference.identity] = reference
            elif reference.component_type is ComponentType.POLICY:
                policies[reference.identity] = reference
            elif reference.component_type is ComponentType.AGENT:
                raise HarnessInventoryError(
                    "required_component_refs cannot add an unselected Agent"
                )

        for skill in tuple(skills.values()):
            skill_record = records[ComponentType.SKILL].get(_ref_key(skill))
            if skill_record is None:
                continue
            for value in _sequence_field(skill_record, "required_tool_refs"):
                tool = _component_ref(value, ComponentType.TOOL, self.source_system)
                tools[tool.identity] = tool
                required_skill_tools.append(SkillToolDependencyEdge(skill_ref=skill, tool_ref=tool))
            for value in _sequence_field(skill_record, "optional_tool_refs"):
                tool = _component_ref(value, ComponentType.TOOL, self.source_system)
                tools[tool.identity] = tool
                optional_skill_tools.append(SkillToolDependencyEdge(skill_ref=skill, tool_ref=tool))

        manifest_refs: tuple[str, ...] = ()
        manifest_digests: dict[str, str] = {}
        registry_snapshot_ids = [registry_snapshot_id]
        if resolved_manifest is not None:
            manifest_id = _required_text(resolved_manifest, "manifest_id")
            manifest_digest = _required_text(resolved_manifest, "manifest_digest")
            manifest_agent = _record_ref(
                _required_field(resolved_manifest, "agent"),
                ComponentType.AGENT,
                self.source_system,
            )
            if manifest_agent != requested_agents[0] or len(requested_agents) != 1:
                raise HarnessInventoryError(
                    "resolved manifest must describe the inspected exact Agent"
                )
            _ensure_manifest_matches_agent(
                resolved_manifest,
                selected_agents[0][1],
                source_system=self.source_system,
            )
            _validate_optional_tenant_fields((resolved_manifest,), self.tenant_id)
            manifest_registry_snapshot_id = _required_text(
                resolved_manifest, "registry_snapshot_id"
            )
            registry_snapshot_ids.append(manifest_registry_snapshot_id)
            manifest_refs = (manifest_id,)
            manifest_digests[manifest_id] = manifest_digest

        all_refs = {
            ComponentType.AGENT: {ref.identity: ref for ref, _ in selected_agents},
            ComponentType.PROMPT: prompts,
            ComponentType.SKILL: skills,
            ComponentType.TOOL: tools,
            ComponentType.POLICY: policies,
        }
        for component_type, refs in all_refs.items():
            for reference in refs.values():
                record = records[component_type].get(_ref_key(reference))
                if record is not None:
                    _validate_optional_tenant_fields((record,), self.tenant_id)
        component_lifecycles = {
            ref.identity: _lifecycle(records[component_type].get(_ref_key(ref)))
            for component_type, refs in all_refs.items()
            for ref in sorted(refs.values(), key=lambda item: item.identity)
        }
        normalized_registry_snapshot_ids = tuple(sorted(set(registry_snapshot_ids)))
        normalized_manifest_refs = tuple(sorted(set(manifest_refs)))
        inventory_identity = {
            "registry_snapshot_ids": normalized_registry_snapshot_ids,
            "agents": tuple(sorted(all_refs[ComponentType.AGENT])),
            "prompts": tuple(sorted(all_refs[ComponentType.PROMPT])),
            "skills": tuple(sorted(all_refs[ComponentType.SKILL])),
            "tools": tuple(sorted(all_refs[ComponentType.TOOL])),
            "policies": tuple(sorted(all_refs[ComponentType.POLICY])),
            "component_lifecycles": tuple(sorted(component_lifecycles.items())),
            "manifest_refs": normalized_manifest_refs,
            "manifest_digests": tuple(sorted(manifest_digests.items())),
        }
        generated_snapshot_id = (
            "inventory_"
            + hashlib.sha256(
                json.dumps(inventory_identity, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()[:32]
        )
        final_snapshot_id = (
            non_blank(snapshot_id, "snapshot_id") if snapshot_id else generated_snapshot_id
        )
        final_captured_at = captured_at
        if final_captured_at is None:
            generated_at = _field(registry_snapshot, "generated_at")
            final_captured_at = (
                generated_at if isinstance(generated_at, datetime) else datetime.now(UTC)
            )
        final_captured_at = aware_timestamp(final_captured_at, "captured_at")
        return AgentSystemInventorySnapshot(
            snapshot_id=final_snapshot_id,
            captured_at=final_captured_at,
            tenant_id=self.tenant_id,
            agent_refs=tuple(
                sorted(all_refs[ComponentType.AGENT].values(), key=lambda ref: ref.identity)
            ),
            prompt_refs=tuple(sorted(prompts.values(), key=lambda ref: ref.identity)),
            skill_refs=tuple(sorted(skills.values(), key=lambda ref: ref.identity)),
            tool_refs=tuple(sorted(tools.values(), key=lambda ref: ref.identity)),
            policy_refs=tuple(sorted(policies.values(), key=lambda ref: ref.identity)),
            agent_to_prompt_edges=tuple(
                sorted(
                    agent_prompt_edges,
                    key=lambda edge: (edge.agent_ref.identity, edge.prompt_ref.identity),
                )
            ),
            agent_to_skill_edges=tuple(
                sorted(
                    agent_skill_edges,
                    key=lambda edge: (edge.agent_ref.identity, edge.skill_ref.identity),
                )
            ),
            agent_to_tool_authority_edges=tuple(
                sorted(
                    agent_tool_edges,
                    key=lambda edge: (edge.agent_ref.identity, edge.tool_ref.identity),
                )
            ),
            skill_to_required_tool_edges=tuple(
                sorted(
                    required_skill_tools,
                    key=lambda edge: (edge.skill_ref.identity, edge.tool_ref.identity),
                )
            ),
            skill_to_optional_tool_edges=tuple(
                sorted(
                    optional_skill_tools,
                    key=lambda edge: (edge.skill_ref.identity, edge.tool_ref.identity),
                )
            ),
            registry_snapshot_ids=normalized_registry_snapshot_ids,
            manifest_refs=normalized_manifest_refs,
            component_lifecycles=component_lifecycles,
            manifest_digests=manifest_digests,
            source_system=self.source_system,
        )

    def _validate_source(self, reference: ExactComponentReference) -> None:
        if reference.source_system != self.source_system:
            raise HarnessInventoryError(
                f"reference source_system must be {self.source_system!r}: {reference.identity}"
            )


def _index_records(
    snapshot: object,
    source_system: str,
) -> dict[ComponentType, dict[tuple[str, str], object]]:
    fields = {
        ComponentType.AGENT: "agents",
        ComponentType.PROMPT: "prompts",
        ComponentType.SKILL: "skills",
        ComponentType.TOOL: "tools",
        ComponentType.POLICY: "policies",
    }
    result: dict[ComponentType, dict[tuple[str, str], object]] = {}
    for component_type, field_name in fields.items():
        indexed: dict[tuple[str, str], object] = {}
        for record in _sequence_field(snapshot, field_name):
            reference = _record_ref(record, component_type, source_system)
            key = _ref_key(reference)
            if key in indexed:
                raise HarnessInventoryError(
                    f"registry snapshot contains duplicate {reference.identity}"
                )
            indexed[key] = record
        result[component_type] = indexed
    return result


def _ensure_manifest_matches_agent(
    manifest: object,
    agent: object,
    *,
    source_system: str,
) -> None:
    expected_prompt = _component_ref(
        _required_field(agent, "prompt_ref"), ComponentType.PROMPT, source_system
    )
    actual_prompt = _component_ref(
        _required_field(manifest, "prompt_ref"), ComponentType.PROMPT, source_system
    )
    if actual_prompt != expected_prompt:
        raise HarnessInventoryError("resolved manifest prompt does not match the Agent")
    for field_name, component_type in (
        ("skill_refs", ComponentType.SKILL),
        ("tool_refs", ComponentType.TOOL),
        ("policy_refs", ComponentType.POLICY),
    ):
        expected = tuple(
            sorted(
                [
                    _component_ref(value, component_type, source_system)
                    for value in _sequence_field(agent, field_name)
                ],
                key=lambda ref: ref.identity,
            )
        )
        actual = tuple(
            sorted(
                [
                    _component_ref(value, component_type, source_system)
                    for value in _sequence_field(manifest, field_name)
                ],
                key=lambda ref: ref.identity,
            )
        )
        if actual != expected:
            raise HarnessInventoryError(f"resolved manifest {field_name} does not match the Agent")


def _validate_optional_tenant_fields(records: Sequence[object], tenant_id: str) -> None:
    """Reject an explicit source tenant that disagrees with integration scope."""

    for record in records:
        value = _field(record, "tenant_id")
        if isinstance(value, str) and value.strip() and value != tenant_id:
            raise HarnessInventoryError("Harness record is outside the requested tenant scope")


def _record_ref(
    record: object,
    component_type: ComponentType,
    source_system: str,
) -> ExactComponentReference:
    if component_type is ComponentType.AGENT:
        identity = _field(record, "identity")
        component_id = (
            _field(identity, "agent_id") if identity is not None else _field(record, "agent_id")
        )
        version = _field(identity, "version") if identity is not None else _field(record, "version")
    else:
        component_id_field = {
            ComponentType.PROMPT: "prompt_id",
            ComponentType.SKILL: "skill_id",
            ComponentType.TOOL: "tool_id",
            ComponentType.POLICY: "policy_id",
        }[component_type]
        component_id = _field(record, component_id_field)
        version = _field(record, "version")
    return _make_ref(component_type, component_id, version, source_system)


def _component_ref(
    value: object,
    expected_type: ComponentType,
    source_system: str,
) -> ExactComponentReference:
    component_type = _component_type(_field(value, "component_type"))
    if component_type is not expected_type:
        raise HarnessInventoryError(
            f"expected {expected_type.value} reference, got {component_type.value}"
        )
    return _make_ref(
        expected_type,
        _field(value, "component_id"),
        _field(value, "version"),
        source_system,
    )


def _make_ref(
    component_type: ComponentType,
    component_id: object,
    version: object,
    source_system: str,
) -> ExactComponentReference:
    if not isinstance(component_id, str) or not component_id.strip():
        raise HarnessInventoryError("Harness component_id must be present")
    if not isinstance(version, str) or not version.strip():
        raise HarnessInventoryError(f"Harness version must be present for {component_id}")
    try:
        return ExactComponentReference(
            component_type=component_type,
            component_id=component_id,
            version=version,
            source_system=source_system,
        )
    except ValueError as exc:
        raise HarnessInventoryError("Harness component reference is invalid") from exc


def _component_type(value: object) -> ComponentType:
    if isinstance(value, ComponentType):
        return value
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        try:
            return ComponentType(value.upper())
        except ValueError:
            try:
                return ComponentType(value.lower())
            except ValueError as exc:
                raise HarnessInventoryError(f"unknown Harness component type: {value!r}") from exc
    raise HarnessInventoryError("Harness component_type must be present")


def _lifecycle(record: object | None) -> str:
    if record is None:
        return "MISSING"
    value = _field(record, "lifecycle")
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str) or not value.strip():
        return "UNKNOWN"
    return value.upper()


def _unique_refs(
    values: Sequence[ExactComponentReference], field_name: str
) -> tuple[ExactComponentReference, ...]:
    unique_values(tuple(ref.identity for ref in values), field_name)
    return tuple(values)


def _ref_key(reference: ExactComponentReference) -> tuple[str, str]:
    return reference.component_id, reference.version


def _field(value: object, name: str, default: object | None = None) -> object | None:
    if isinstance(value, Mapping):
        return cast(object | None, value.get(name, default))
    return cast(object | None, getattr(value, name, default))


def _required_field(value: object, name: str) -> object:
    result = _field(value, name)
    if result is None:
        raise HarnessInventoryError(f"Harness field is missing: {name}")
    return result


def _required_text(value: object, name: str) -> str:
    result = _field(value, name)
    if not isinstance(result, str) or not result.strip():
        raise HarnessInventoryError(f"Harness field must be non-blank: {name}")
    return result


def _sequence_field(value: object, name: str) -> tuple[object, ...]:
    result = _field(value, name, ())
    if result is None:
        return ()
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)):
        raise HarnessInventoryError(f"Harness field must be a sequence: {name}")
    return tuple(result)


__all__ = [
    "HarnessInventoryAdapter",
    "HarnessInventoryError",
    "HarnessInventoryPort",
]
