"""Harness boundary for evaluation-scoped candidate construction.

The Harness owns component resolution, registration, activation, runtime
authority, and manifest digests.  This adapter only translates an exact
Autopilot proposal into a Harness ``AgentConfig``-shaped value, calls the
injected evaluation factory, and keeps the resulting manifest provenance.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, cast

from ..contracts import (
    AgentSystemInventorySnapshot,
    CandidateReference,
    ChangeProposal,
    ComponentType,
    ExactComponentReference,
    OperationalDisposition,
)
from ..contracts.common import non_blank
from ..graph import (
    AgentGraph,
    GraphValidationError,
    graph_digest,
)
from ..graph import (
    apply_proposal as _apply_graph_proposal,
)
from ..graph import (
    require_graph_match as _require_graph_match_impl,
)
from ..graph import (
    validate_candidate_graph as _validate_graph,
)
from ..storage.ports import CandidateReferenceStore


class HarnessCandidateError(ValueError):
    """Raised when Harness cannot build the exact proposed graph."""


class HarnessFactoryPort(Protocol):
    """Minimal public Harness factory boundary used by Autopilot."""

    def build(
        self,
        config: object,
        *,
        dry_run: bool = False,
        activate: bool = True,
        register: bool = True,
    ) -> object:
        """Build one exact Agent through the supplied Harness scope."""


@dataclass(frozen=True)
class HarnessCandidateBuild:
    """A Harness-built candidate and its Autopilot-safe provenance."""

    candidate_reference: CandidateReference
    built_agent: object
    manifest: object
    agent_config: object

    @property
    def candidate(self) -> CandidateReference:
        """Return the Autopilot candidate reference."""

        return self.candidate_reference

    @property
    def config(self) -> object:
        """Return the opaque Harness configuration sent to the factory."""

        return self.agent_config


class HarnessCandidateAdapter:
    """Construct one proposal in an isolated Harness registry/factory scope.

    ``evaluation_factory`` must be configured with an evaluation registry.  A
    production registry is accepted only as a guard: it is never called by
    this adapter and must not be the registry used by the factory.
    """

    def __init__(
        self,
        evaluation_factory: HarnessFactoryPort,
        *,
        evaluation_registry: object | None = None,
        production_registry: object | None = None,
        source_system: str = "harness",
        candidate_store: CandidateReferenceStore | None = None,
    ) -> None:
        self.evaluation_factory = evaluation_factory
        self.source_system = non_blank(source_system, "source_system")
        self.candidate_store = candidate_store

        factory_registry = getattr(evaluation_factory, "agent_registry", None)
        if evaluation_registry is not None and factory_registry is not None:
            if factory_registry is not evaluation_registry:
                raise HarnessCandidateError(
                    "evaluation_factory must use the supplied evaluation_registry"
                )
        self.evaluation_registry = (
            evaluation_registry if evaluation_registry is not None else factory_registry
        )
        if production_registry is not None and self.evaluation_registry is production_registry:
            raise HarnessCandidateError(
                "production_registry must not be used for candidate construction"
            )
        if production_registry is not None and factory_registry is production_registry:
            raise HarnessCandidateError(
                "evaluation_factory is configured with the production registry"
            )
        if self.evaluation_registry is None:
            raise HarnessCandidateError(
                "an explicit evaluation registry or a factory.agent_registry is required"
            )

    def construct(
        self,
        proposal: ChangeProposal | OperationalDisposition,
        inventory: AgentSystemInventorySnapshot,
        baseline_agent_config: object,
        *,
        candidate_id: str | None = None,
        candidate_store: CandidateReferenceStore | None = None,
    ) -> HarnessCandidateBuild:
        """Build and validate an exact candidate from one change proposal."""

        if isinstance(proposal, OperationalDisposition):
            raise HarnessCandidateError("NO_CHANGE dispositions must not enter Harness")
        if not isinstance(proposal, ChangeProposal):
            raise HarnessCandidateError("candidate construction requires a ChangeProposal")
        if not isinstance(inventory, AgentSystemInventorySnapshot):
            raise HarnessCandidateError("candidate construction requires an inventory snapshot")
        if proposal.tenant_id != inventory.tenant_id:
            raise HarnessCandidateError("proposal and inventory must belong to the same tenant")
        if proposal.baseline_inventory_snapshot_id != inventory.snapshot_id:
            raise HarnessCandidateError(
                "proposal baseline must match the supplied inventory snapshot"
            )
        if inventory.source_system != self.source_system:
            raise HarnessCandidateError(f"inventory source_system must be {self.source_system!r}")
        if any(
            reference.source_system != inventory.source_system
            for reference in proposal.target_agent_refs
        ):
            raise HarnessCandidateError(
                "proposal target Agent references use a different source system"
            )

        baseline = _config_graph(baseline_agent_config, self.source_system)
        expected = _apply_proposal(proposal, inventory, baseline)
        config = _materialize_config(
            baseline_agent_config,
            expected,
            source_system=self.source_system,
        )
        try:
            built = self.evaluation_factory.build(
                config,
                dry_run=False,
                activate=True,
                register=True,
            )
        except Exception as exc:
            raise HarnessCandidateError("Harness evaluation candidate construction failed") from exc

        manifest = _required_field(built, "manifest")
        actual = _manifest_graph(manifest, self.source_system)
        _require_graph_match(expected, actual)
        manifest_id = _required_text(manifest, "manifest_id")
        manifest_digest = _required_text(manifest, "manifest_digest")
        registry_snapshot_id = _required_text(manifest, "registry_snapshot_id")
        final_candidate_id = (
            non_blank(candidate_id, "candidate_id")
            if candidate_id is not None
            else _stable_id(
                "candidate",
                {
                    "tenant_id": proposal.tenant_id,
                    "proposal_id": proposal.proposal_id,
                    "manifest_id": manifest_id,
                },
            )
        )
        reference = CandidateReference(
            candidate_id=final_candidate_id,
            tenant_id=proposal.tenant_id,
            agent_ref=actual.agent_ref,
            manifest_id=manifest_id,
            manifest_digest=manifest_digest,
            registry_snapshot_id=registry_snapshot_id,
            prompt_ref=actual.prompt_ref,
            skill_refs=actual.skill_refs,
            tool_refs=actual.tool_refs,
            policy_refs=actual.policy_refs,
            proposal_id=proposal.proposal_id,
            baseline_inventory_snapshot_id=inventory.snapshot_id,
            resolved_graph_digest=graph_digest(actual),
        )
        validate_candidate_graph(proposal, inventory, reference)
        store = candidate_store or self.candidate_store
        if store is not None:
            try:
                store.insert(reference)
            except Exception as exc:
                raise HarnessCandidateError("candidate reference could not be stored") from exc
        return HarnessCandidateBuild(
            candidate_reference=reference,
            built_agent=built,
            manifest=manifest,
            agent_config=config,
        )


def validate_candidate_graph(
    proposal: ChangeProposal,
    inventory: AgentSystemInventorySnapshot,
    candidate: CandidateReference,
) -> None:
    """Validate a candidate through the provider-neutral graph boundary."""

    try:
        _validate_graph(proposal, inventory, candidate)
    except GraphValidationError as exc:
        raise HarnessCandidateError(str(exc)) from exc


def _require_graph_match(expected: AgentGraph, actual: AgentGraph) -> None:
    try:
        _require_graph_match_impl(expected, actual)
    except GraphValidationError as exc:
        raise HarnessCandidateError(str(exc)) from exc


def _config_graph(config: object, source_system: str) -> AgentGraph:
    raw_identity = _field(config, "identity")
    if raw_identity is None:
        raw_identity = config
    agent_ref = _agent_reference(raw_identity, source_system)
    prompt_ref = _component_reference(
        _required_field(config, "prompt_ref"), ComponentType.PROMPT, source_system
    )
    skill_refs = _component_sequence(config, "skill_refs", ComponentType.SKILL, source_system)
    tool_refs = _component_sequence(config, "tool_refs", ComponentType.TOOL, source_system)
    policy_refs = _component_sequence(config, "policy_refs", ComponentType.POLICY, source_system)
    return AgentGraph(agent_ref, prompt_ref, skill_refs, tool_refs, policy_refs)


def _apply_proposal(
    proposal: ChangeProposal,
    inventory: AgentSystemInventorySnapshot,
    baseline: AgentGraph,
) -> AgentGraph:
    try:
        return _apply_graph_proposal(proposal, inventory, baseline)
    except GraphValidationError as exc:
        raise HarnessCandidateError(str(exc)) from exc


def _materialize_config(config: object, graph: AgentGraph, *, source_system: str) -> object:
    payload = _config_payload(config)
    templates = _reference_templates(config)
    identity_template = _field(config, "identity")
    payload.pop("agent_id", None)
    payload.pop("version", None)
    payload["identity"] = _external_identity(identity_template, graph.agent_ref)
    payload["prompt_ref"] = _external_reference(
        graph.prompt_ref, templates.get(ComponentType.PROMPT), source_system
    )
    for field_name, component_type, values in (
        ("skill_refs", ComponentType.SKILL, graph.skill_refs),
        ("tool_refs", ComponentType.TOOL, graph.tool_refs),
        ("policy_refs", ComponentType.POLICY, graph.policy_refs),
    ):
        original = _field(config, field_name, None)
        converted = [
            _external_reference(reference, templates.get(component_type), source_system)
            for reference in values
        ]
        payload[field_name] = tuple(converted) if isinstance(original, tuple) else converted
    return payload


def _manifest_graph(manifest: object, source_system: str) -> AgentGraph:
    agent_ref = _agent_reference(_required_field(manifest, "agent"), source_system)
    graph = AgentGraph(
        agent_ref=agent_ref,
        prompt_ref=_component_reference(
            _required_field(manifest, "prompt_ref"), ComponentType.PROMPT, source_system
        ),
        skill_refs=_component_sequence(manifest, "skill_refs", ComponentType.SKILL, source_system),
        tool_refs=_component_sequence(manifest, "tool_refs", ComponentType.TOOL, source_system),
        policy_refs=_component_sequence(
            manifest, "policy_refs", ComponentType.POLICY, source_system
        ),
    )
    definition = _field(manifest, "agent")
    nested_fields = (
        ("prompt_ref", ComponentType.PROMPT, graph.prompt_ref),
        ("skill_refs", ComponentType.SKILL, graph.skill_refs),
        ("tool_refs", ComponentType.TOOL, graph.tool_refs),
        ("policy_refs", ComponentType.POLICY, graph.policy_refs),
    )
    for field_name, component_type, expected in nested_fields:
        raw = _field(definition, field_name, None)
        if raw is None:
            continue
        nested = (
            _component_reference(raw, component_type, source_system)
            if field_name == "prompt_ref"
            else tuple(
                sorted(
                    _component_sequence(definition, field_name, component_type, source_system),
                    key=lambda reference: reference.identity,
                )
            )
        )
        expected_identities = _identities(expected if isinstance(expected, tuple) else (expected,))
        nested_identities = _identities(nested if isinstance(nested, tuple) else (nested,))
        if expected_identities != nested_identities:
            raise HarnessCandidateError(
                f"resolved manifest Agent {field_name} does not match top-level graph"
            )
    return graph


def _component_sequence(
    value: object,
    field_name: str,
    expected: ComponentType,
    source_system: str,
) -> tuple[ExactComponentReference, ...]:
    raw = _field(value, field_name, ())
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise HarnessCandidateError(f"Harness field must be a sequence: {field_name}")
    return tuple(_component_reference(item, expected, source_system) for item in raw)


def _component_reference(
    value: object,
    expected: ComponentType,
    source_system: str,
) -> ExactComponentReference:
    if isinstance(value, ExactComponentReference):
        reference = value
    else:
        raw_type = _field(value, "component_type", _field(value, "component_kind", expected.value))
        component_type = _component_type(raw_type)
        component_id = _field(value, "component_id", _field(value, "id"))
        version = _field(value, "version")
        if (component_id is None or version is None) and isinstance(value, str):
            component_type, component_id, version = _parse_identity(value, expected)
        if not isinstance(component_id, str) or not isinstance(version, str):
            raise HarnessCandidateError("exact Harness component reference is incomplete")
        try:
            reference = ExactComponentReference(
                component_type=component_type,
                component_id=component_id,
                version=version,
                source_system=source_system,
            )
        except ValueError as exc:
            raise HarnessCandidateError("exact Harness component reference is invalid") from exc
    if reference.component_type is not expected:
        raise HarnessCandidateError(
            f"expected {expected.value} reference, received {reference.component_type.value}"
        )
    if reference.source_system != source_system:
        raise HarnessCandidateError("Harness reference source system does not match inventory")
    return reference


def _agent_reference(value: object, source_system: str) -> ExactComponentReference:
    nested_identity = _field(value, "identity", None)
    if nested_identity is not None:
        return _agent_reference(nested_identity, source_system)
    if isinstance(value, Mapping):
        component_id = value.get("agent_id", value.get("component_id"))
        version = value.get("version")
        if component_id is not None and version is not None:
            return _component_reference(
                {
                    "component_type": ComponentType.AGENT,
                    "component_id": component_id,
                    "version": version,
                },
                ComponentType.AGENT,
                source_system,
            )
    component_id = _field(value, "agent_id", _field(value, "component_id"))
    version = _field(value, "version")
    if component_id is not None and version is not None:
        return _component_reference(
            {
                "component_type": ComponentType.AGENT,
                "component_id": component_id,
                "version": version,
            },
            ComponentType.AGENT,
            source_system,
        )
    return _component_reference(value, ComponentType.AGENT, source_system)


def _parse_identity(value: str, expected: ComponentType) -> tuple[ComponentType, str, str]:
    text = value.strip()
    if ":" in text:
        raw_type, text = text.split(":", 1)
        component_type = _component_type(raw_type)
    else:
        component_type = expected
    if "@" not in text:
        raise HarnessCandidateError("exact reference identity must contain a version")
    component_id, version = text.rsplit("@", 1)
    return component_type, component_id, version


def _component_type(value: object) -> ComponentType:
    if isinstance(value, ComponentType):
        return value
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str):
        try:
            return ComponentType(value.upper())
        except ValueError as exc:
            raise HarnessCandidateError(f"unknown component type: {value!r}") from exc
    raise HarnessCandidateError("component type is required")


def _config_payload(config: object) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return cast(dict[str, Any], copy.deepcopy(dict(config)))
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        try:
            return cast(dict[str, Any], model_dump(mode="python"))
        except TypeError:
            return cast(dict[str, Any], model_dump())
    values: dict[str, Any] = {}
    for field_name in (
        "identity",
        "agent_id",
        "version",
        "goal",
        "supported_intents",
        "supported_languages",
        "prompt_ref",
        "skill_refs",
        "tool_refs",
        "policy_refs",
        "provider_profile",
        "runtime_profile",
        "runtime_limits",
        "risk_level",
        "approval_requirements",
        "state_strategy",
        "memory_strategy",
        "owner_id",
        "template",
        "performance_metadata",
    ):
        raw = _field(config, field_name, None)
        if raw is not None:
            values[field_name] = copy.deepcopy(raw)
    if not values:
        raise HarnessCandidateError("baseline Agent config is not a mapping or model")
    return values


def _reference_templates(config: object) -> dict[ComponentType, object]:
    result: dict[ComponentType, object] = {}
    for field_name, component_type in (
        ("prompt_ref", ComponentType.PROMPT),
        ("skill_refs", ComponentType.SKILL),
        ("tool_refs", ComponentType.TOOL),
        ("policy_refs", ComponentType.POLICY),
    ):
        raw = _field(config, field_name, None)
        values = raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else ()
        value = raw if field_name == "prompt_ref" else (values[0] if values else None)
        if value is not None:
            result[component_type] = value
    fallback = next(iter(result.values()), None)
    if fallback is not None:
        for component_type in ComponentType:
            result.setdefault(component_type, fallback)
    return result


def _external_reference(
    reference: ExactComponentReference,
    template: object | None,
    source_system: str,
) -> object:
    del source_system
    if template is None or isinstance(template, Mapping):
        return {
            "component_type": reference.component_type.value.lower(),
            "component_id": reference.component_id,
            "version": reference.version,
        }
    template_type = _field(template, "component_type", None)
    if hasattr(template, "model_copy"):
        try:
            enum_value = cast(Any, type(template_type))(reference.component_type.value.lower())
        except (TypeError, ValueError):
            enum_value = reference.component_type.value.lower()
        try:
            template_class = cast(Any, template.__class__)
            return template_class(
                component_type=enum_value,
                component_id=reference.component_id,
                version=reference.version,
            )
        except Exception:
            try:
                return cast(Any, template).model_copy(
                    update={
                        "component_type": enum_value,
                        "component_id": reference.component_id,
                        "version": reference.version,
                    }
                )
            except Exception:
                pass
    try:
        template_class = cast(Any, template.__class__)
        return template_class(
            component_type=reference.component_type.value.lower(),
            component_id=reference.component_id,
            version=reference.version,
        )
    except Exception:
        copied = copy.copy(template)
        for name, value in (
            ("component_type", reference.component_type.value.lower()),
            ("component_id", reference.component_id),
            ("version", reference.version),
        ):
            try:
                setattr(copied, name, value)
            except Exception:
                return {
                    "component_type": reference.component_type.value.lower(),
                    "component_id": reference.component_id,
                    "version": reference.version,
                }
        return copied


def _external_identity(template: object | None, reference: ExactComponentReference) -> object:
    if template is None or isinstance(template, Mapping):
        return {"agent_id": reference.component_id, "version": reference.version}
    if hasattr(template, "model_copy"):
        try:
            template_class = cast(Any, template.__class__)
            return template_class(agent_id=reference.component_id, version=reference.version)
        except Exception:
            try:
                return cast(Any, template).model_copy(
                    update={"agent_id": reference.component_id, "version": reference.version}
                )
            except Exception:
                pass
    try:
        template_class = cast(Any, template.__class__)
        return template_class(agent_id=reference.component_id, version=reference.version)
    except Exception:
        copied = copy.copy(template)
        for name, value in (
            ("agent_id", reference.component_id),
            ("version", reference.version),
        ):
            try:
                setattr(copied, name, value)
            except Exception:
                return {"agent_id": reference.component_id, "version": reference.version}
        return copied


def _field(value: object, name: str, default: object | None = None) -> object | None:
    if isinstance(value, Mapping):
        return cast(object | None, value.get(name, default))
    return cast(object | None, getattr(value, name, default))


def _required_field(value: object, name: str) -> object:
    result = _field(value, name)
    if result is None:
        raise HarnessCandidateError(f"Harness field is missing: {name}")
    return result


def _required_text(value: object, name: str) -> str:
    result = _field(value, name)
    if not isinstance(result, str) or not result.strip():
        raise HarnessCandidateError(f"Harness field must be non-blank: {name}")
    return result


def _identities(values: Sequence[ExactComponentReference]) -> tuple[str, ...]:
    return tuple(value.identity for value in values)


def _stable_id(prefix: str, value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


__all__ = [
    "HarnessCandidateAdapter",
    "HarnessCandidateBuild",
    "HarnessCandidateError",
    "HarnessFactoryPort",
    "graph_digest",
    "validate_candidate_graph",
]
