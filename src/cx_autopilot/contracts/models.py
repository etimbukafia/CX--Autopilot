"""Immutable provider-neutral domain contracts for the initial phases."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

from .common import (
    ImmutableModel,
    aware_timestamp,
    non_blank,
    to_jsonable,
    unique_refs,
    unique_values,
)


def _valid_evidence_reference(value: str) -> str:
    return non_blank(value, "evidence_ref")


EvidenceRef = Annotated[str, Field(min_length=1), AfterValidator(_valid_evidence_reference)]


class ComponentType(StrEnum):
    """Exact component kinds known to Autopilot."""

    AGENT = "AGENT"
    TOOL = "TOOL"
    SKILL = "SKILL"
    PROMPT = "PROMPT"
    POLICY = "POLICY"


class ExactComponentReference(ImmutableModel):
    """Exact, versioned identity of one governed component."""

    component_type: ComponentType
    component_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_system: str = Field(min_length=1)

    @field_validator("component_type", mode="before")
    @classmethod
    def normalize_component_type(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("component_id", "version", "source_system")
    @classmethod
    def identities_are_exact(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return non_blank(value, field_name)

    @field_validator("component_id")
    @classmethod
    def component_id_has_no_identity_separators(cls, value: str) -> str:
        if ":" in value or "@" in value:
            raise ValueError("component_id must not contain ':' or '@'")
        return value

    @property
    def identity(self) -> str:
        """Return a stable provider-neutral identity string."""

        return f"{self.component_type.value}:{self.component_id}@{self.version}"


class EvidenceQuality(StrEnum):
    """Quality of source evidence, separate from confidence."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"


class OperationalSignal(ImmutableModel):
    """One normalized source-owned operational observation."""

    signal_id: str = Field(min_length=1)
    source_system: str = Field(min_length=1)
    source_record_type: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    source_record_version: str | None = Field(default=None, min_length=1)
    signal_type: str = Field(min_length=1)
    occurred_at: datetime
    tenant_id: str = Field(min_length=1)
    interaction_id: str | None = Field(default=None, min_length=1)
    journey_id: str | None = Field(default=None, min_length=1)
    customer_id: str | None = Field(default=None, min_length=1)
    agent_id: str | None = Field(default=None, min_length=1)
    execution_id: str | None = Field(default=None, min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)
    source_reference: str = Field(min_length=1)
    payload_reference: str | None = Field(default=None, min_length=1)
    normalized_attributes: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_quality: EvidenceQuality
    evidence_refs: tuple[EvidenceRef, ...] = ()

    @field_validator(
        "signal_id",
        "source_system",
        "source_record_type",
        "source_record_id",
        "source_record_version",
        "signal_type",
        "tenant_id",
        "interaction_id",
        "journey_id",
        "customer_id",
        "agent_id",
        "execution_id",
        "trace_id",
        "source_reference",
        "payload_reference",
    )
    @classmethod
    def string_fields_are_non_blank(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_is_aware(cls, value: datetime) -> datetime:
        return aware_timestamp(value, "occurred_at")

    @field_validator("evidence_refs")
    @classmethod
    def evidence_refs_are_unique(cls, value: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
        unique_values(value, "evidence_refs")
        return value

    @model_validator(mode="after")
    def preserve_a_bounded_payload_reference(self) -> "OperationalSignal":
        if self.payload_reference is None and not self.normalized_attributes:
            raise ValueError("payload_reference or normalized_attributes is required")
        if len(str(to_jsonable(self.__dict__))) > 64_000:
            raise ValueError("normalized operational signal must remain bounded")
        return self

    @property
    def source_identity(self) -> tuple[str, str, str, str | None]:
        """Return the stable source identity used for idempotent ingestion."""

        return (
            self.source_system,
            self.source_record_type,
            self.source_record_id,
            self.source_record_version,
        )


class OpportunityPattern(StrEnum):
    """Deterministic operational patterns that can become opportunities."""

    REPEATED_OPERATION_SEQUENCE = "REPEATED_OPERATION_SEQUENCE"
    REPEATED_ESCALATION = "REPEATED_ESCALATION"
    REPEAT_CONTACT_UNRESOLVED = "REPEAT_CONTACT_UNRESOLVED"
    REPEATED_LOOKUP = "REPEATED_LOOKUP"
    REPEATED_APPROVAL_WAIT = "REPEATED_APPROVAL_WAIT"
    REPEATED_POLICY_DENIAL = "REPEATED_POLICY_DENIAL"
    REPEATED_HUMAN_WORKAROUND = "REPEATED_HUMAN_WORKAROUND"
    REPEATED_OPERATOR_CORRECTION = "REPEATED_OPERATOR_CORRECTION"


class Opportunity(ImmutableModel):
    """One evidence-backed automation opportunity, before diagnosis."""

    opportunity_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_signal_ids: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    frequency_estimate: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    status: str = Field(min_length=1)
    created_at: datetime
    impact_estimate: float | None = Field(default=None, ge=0.0, le=1.0)
    detector_name: str = "unspecified"
    pattern_type: OpportunityPattern = OpportunityPattern.REPEATED_LOOKUP
    pattern_key: str = "unspecified"
    window_start: datetime | None = None
    window_end: datetime | None = None
    occurrence_keys: tuple[str, ...] = ()
    operational_effort_estimate: float | None = Field(default=None, ge=0.0, le=1.0)
    predictability_estimate: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_estimate: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_factors: tuple[str, ...] = ()

    @field_validator(
        "opportunity_id",
        "tenant_id",
        "title",
        "description",
        "status",
        "detector_name",
        "pattern_key",
    )
    @classmethod
    def required_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("source_signal_ids", "evidence_refs", "occurrence_keys", "risk_factors")
    @classmethod
    def references_are_unique(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        unique_values(value, getattr(info, "field_name", "references"))
        return value

    @field_validator("created_at", "window_start", "window_end")
    @classmethod
    def opportunity_timestamps_are_aware(
        cls, value: datetime | None, info: object
    ) -> datetime | None:
        return (
            None
            if value is None
            else aware_timestamp(value, getattr(info, "field_name", "timestamp"))
        )

    @model_validator(mode="after")
    def opportunity_window_is_ordered(self) -> "Opportunity":
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end < self.window_start
        ):
            raise ValueError("window_end must not precede window_start")
        return self


_PRIORITY_FACTOR_NAMES = (
    "frequency",
    "impact",
    "confidence",
    "operational_effort",
    "predictability",
    "risk",
)


class OpportunityPriorityFactors(ImmutableModel):
    """Inspectable normalized factors used for cluster prioritization.

    ``None`` means that the evidence does not support a factor. It is not a
    zero score. The availability lists make that distinction explicit at the
    persistence boundary.
    """

    frequency: float | None = Field(default=None, ge=0.0, le=1.0)
    impact: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    operational_effort: float | None = Field(default=None, ge=0.0, le=1.0)
    predictability: float | None = Field(default=None, ge=0.0, le=1.0)
    risk: float | None = Field(default=None, ge=0.0, le=1.0)
    available_factors: tuple[str, ...] = ()
    unavailable_factors: tuple[str, ...] = ()
    effective_weights: dict[str, float] = Field(default_factory=dict)

    @field_validator("available_factors", "unavailable_factors")
    @classmethod
    def factor_names_are_known_and_unique(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        unique_values(value, getattr(info, "field_name", "factors"))
        unknown = set(value).difference(_PRIORITY_FACTOR_NAMES)
        if unknown:
            raise ValueError(f"unknown prioritization factors: {sorted(unknown)}")
        return value

    @field_validator("effective_weights")
    @classmethod
    def effective_weights_are_bounded(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value).difference(_PRIORITY_FACTOR_NAMES)
        if unknown:
            raise ValueError(f"unknown effective prioritization weights: {sorted(unknown)}")
        if any(weight < 0.0 or weight > 1.0 for weight in value.values()):
            raise ValueError("effective prioritization weights must be between 0 and 1")
        return value

    @field_serializer("effective_weights")
    def serialize_effective_weights(self, value: Mapping[str, float]) -> dict[str, float]:
        return dict(value)

    @model_validator(mode="after")
    def factor_availability_matches_values(self) -> "OpportunityPriorityFactors":
        values = {name: getattr(self, name) for name in _PRIORITY_FACTOR_NAMES}
        expected_available = tuple(
            name for name in _PRIORITY_FACTOR_NAMES if values[name] is not None
        )
        expected_unavailable = tuple(
            name for name in _PRIORITY_FACTOR_NAMES if values[name] is None
        )
        supplied = set(self.available_factors) | set(self.unavailable_factors)
        if not supplied:
            object.__setattr__(self, "available_factors", expected_available)
            object.__setattr__(self, "unavailable_factors", expected_unavailable)
        elif (
            self.available_factors != expected_available
            or self.unavailable_factors != expected_unavailable
        ):
            raise ValueError("factor availability must match nullable factor values")
        if self.effective_weights:
            if set(self.effective_weights).difference(self.available_factors):
                raise ValueError("effective weights require available factors")
            if abs(sum(self.effective_weights.values()) - 1.0) > 1e-6:
                raise ValueError("effective prioritization weights must sum to 1")
        return self


class OpportunityCluster(ImmutableModel):
    """Tenant-scoped, explicitly time-bounded opportunity grouping."""

    cluster_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    window_start: datetime
    window_end: datetime
    opportunity_ids: tuple[str, ...] = Field(min_length=1)
    pattern_summary: str = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    frequency: float = Field(ge=0.0)
    impact: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    risk_factors: tuple[str, ...] = ()
    pattern_type: OpportunityPattern = OpportunityPattern.REPEATED_LOOKUP
    pattern_key: str = "unspecified"
    prioritization_factors: OpportunityPriorityFactors = Field(
        default_factory=OpportunityPriorityFactors
    )
    priority_score: float = Field(ge=0.0, le=1.0, default=0.0)
    priority_rank: int = Field(ge=1, default=1)

    @field_validator("cluster_id", "tenant_id", "pattern_summary", "pattern_key")
    @classmethod
    def cluster_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("window_start", "window_end")
    @classmethod
    def window_timestamps_are_aware(cls, value: datetime, info: object) -> datetime:
        return aware_timestamp(value, getattr(info, "field_name", "timestamp"))

    @field_validator("opportunity_ids", "evidence_refs", "risk_factors")
    @classmethod
    def cluster_lists_are_unique(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        unique_values(value, getattr(info, "field_name", "values"))
        return value

    @model_validator(mode="after")
    def window_is_ordered(self) -> "OpportunityCluster":
        if self.window_end < self.window_start:
            raise ValueError("window_end must not precede window_start")
        return self


class AgentPromptEdge(ImmutableModel):
    """Explicit Agent to Prompt composition fact."""

    agent_ref: ExactComponentReference
    prompt_ref: ExactComponentReference

    @model_validator(mode="after")
    def types_are_correct(self) -> "AgentPromptEdge":
        _require_type(self.agent_ref, ComponentType.AGENT, "agent_ref")
        _require_type(self.prompt_ref, ComponentType.PROMPT, "prompt_ref")
        _require_distinct(self.agent_ref, self.prompt_ref)
        return self


class AgentSkillEdge(ImmutableModel):
    """Explicit Agent to Skill composition fact."""

    agent_ref: ExactComponentReference
    skill_ref: ExactComponentReference

    @model_validator(mode="after")
    def types_are_correct(self) -> "AgentSkillEdge":
        _require_type(self.agent_ref, ComponentType.AGENT, "agent_ref")
        _require_type(self.skill_ref, ComponentType.SKILL, "skill_ref")
        _require_distinct(self.agent_ref, self.skill_ref)
        return self


class AgentToolAuthorityEdge(ImmutableModel):
    """Explicit Agent executable Tool authority fact."""

    agent_ref: ExactComponentReference
    tool_ref: ExactComponentReference

    @model_validator(mode="after")
    def types_are_correct(self) -> "AgentToolAuthorityEdge":
        _require_type(self.agent_ref, ComponentType.AGENT, "agent_ref")
        _require_type(self.tool_ref, ComponentType.TOOL, "tool_ref")
        _require_distinct(self.agent_ref, self.tool_ref)
        return self


class SkillToolDependencyEdge(ImmutableModel):
    """Explicit Skill to Tool dependency fact, not authority."""

    skill_ref: ExactComponentReference
    tool_ref: ExactComponentReference

    @model_validator(mode="after")
    def types_are_correct(self) -> "SkillToolDependencyEdge":
        _require_type(self.skill_ref, ComponentType.SKILL, "skill_ref")
        _require_type(self.tool_ref, ComponentType.TOOL, "tool_ref")
        _require_distinct(self.skill_ref, self.tool_ref)
        return self


class AgentSystemInventorySnapshot(ImmutableModel):
    """Exact inspected component inventory and relationship facts."""

    snapshot_id: str = Field(min_length=1)
    captured_at: datetime
    tenant_id: str = Field(min_length=1)
    agent_refs: tuple[ExactComponentReference, ...] = ()
    prompt_refs: tuple[ExactComponentReference, ...] = ()
    skill_refs: tuple[ExactComponentReference, ...] = ()
    tool_refs: tuple[ExactComponentReference, ...] = ()
    policy_refs: tuple[ExactComponentReference, ...] = ()
    agent_to_prompt_edges: tuple[AgentPromptEdge, ...] = ()
    agent_to_skill_edges: tuple[AgentSkillEdge, ...] = ()
    agent_to_tool_authority_edges: tuple[AgentToolAuthorityEdge, ...] = ()
    skill_to_required_tool_edges: tuple[SkillToolDependencyEdge, ...] = ()
    skill_to_optional_tool_edges: tuple[SkillToolDependencyEdge, ...] = ()
    registry_snapshot_ids: tuple[str, ...] = ()
    manifest_refs: tuple[str, ...] = ()
    component_lifecycles: dict[str, str] = Field(default_factory=dict)
    manifest_digests: dict[str, str] = Field(default_factory=dict)
    source_system: str = Field(min_length=1)

    @field_validator("snapshot_id", "tenant_id", "source_system")
    @classmethod
    def snapshot_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("captured_at")
    @classmethod
    def captured_at_is_aware(cls, value: datetime) -> datetime:
        return aware_timestamp(value, "captured_at")

    @field_serializer("component_lifecycles", "manifest_digests")
    def serialize_inventory_metadata(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

    @model_validator(mode="after")
    def inventory_references_are_consistent(self) -> "AgentSystemInventorySnapshot":
        groups = (
            ("agent_refs", self.agent_refs, ComponentType.AGENT),
            ("prompt_refs", self.prompt_refs, ComponentType.PROMPT),
            ("skill_refs", self.skill_refs, ComponentType.SKILL),
            ("tool_refs", self.tool_refs, ComponentType.TOOL),
            ("policy_refs", self.policy_refs, ComponentType.POLICY),
        )
        for name, refs, expected in groups:
            unique_refs(refs, name)
            for ref in refs:
                _require_type(ref, expected, name)
        for name, values in (
            ("registry_snapshot_ids", self.registry_snapshot_ids),
            ("manifest_refs", self.manifest_refs),
        ):
            unique_values(values, name)
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must not contain blank values")
        known_component_identities = {
            reference.identity for _, refs, _ in groups for reference in refs
        }
        for identity, lifecycle in self.component_lifecycles.items():
            if identity not in known_component_identities:
                raise ValueError("component_lifecycles contains an unknown component identity")
            if not identity.strip() or not lifecycle.strip():
                raise ValueError("component_lifecycles keys and values must not be blank")
        for manifest_id, digest in self.manifest_digests.items():
            if manifest_id not in self.manifest_refs:
                raise ValueError("manifest_digests must refer to manifest_refs")
            if not manifest_id.strip() or not digest.strip():
                raise ValueError("manifest_digests keys and values must not be blank")
        for edge in (
            *self.agent_to_prompt_edges,
            *self.agent_to_skill_edges,
            *self.agent_to_tool_authority_edges,
            *self.skill_to_required_tool_edges,
            *self.skill_to_optional_tool_edges,
        ):
            _require_edge_membership(edge, self)
        required = {
            (edge.skill_ref.identity, edge.tool_ref.identity)
            for edge in self.skill_to_required_tool_edges
        }
        optional = {
            (edge.skill_ref.identity, edge.tool_ref.identity)
            for edge in self.skill_to_optional_tool_edges
        }
        if required & optional:
            raise ValueError("a Skill dependency cannot be both required and optional")
        return self


class DiagnosisType(StrEnum):
    """Autopilot operational diagnosis taxonomy."""

    SKILL_GAP = "SKILL_GAP"
    PROMPT_GAP = "PROMPT_GAP"
    AGENT_GAP = "AGENT_GAP"
    TOOL_GAP = "TOOL_GAP"
    POLICY_CONSTRAINT = "POLICY_CONSTRAINT"
    APPROVAL_FRICTION = "APPROVAL_FRICTION"
    BUSINESS_DEPENDENCY = "BUSINESS_DEPENDENCY"
    DATA_QUALITY_ISSUE = "DATA_QUALITY_ISSUE"
    KNOWLEDGE_SOURCE_ISSUE = "KNOWLEDGE_SOURCE_ISSUE"


class ProblemDiagnosis(ImmutableModel):
    """Evidence-backed operational diagnosis without authority."""

    diagnosis_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    inventory_snapshot_id: str | None = Field(default=None, min_length=1)
    diagnosis_type: DiagnosisType
    summary: str = Field(min_length=1)
    precedence_rule: str = "deterministic_precedence"
    supporting_evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    conflicting_evidence_refs: tuple[EvidenceRef, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    affected_agent_refs: tuple[ExactComponentReference, ...] = ()
    affected_prompt_refs: tuple[ExactComponentReference, ...] = ()
    affected_skill_refs: tuple[ExactComponentReference, ...] = ()
    affected_tool_refs: tuple[ExactComponentReference, ...] = ()
    affected_policy_refs: tuple[ExactComponentReference, ...] = ()
    created_at: datetime

    @field_validator("diagnosis_id", "tenant_id", "cluster_id", "summary", "precedence_rule")
    @classmethod
    def diagnosis_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("supporting_evidence_refs", "conflicting_evidence_refs")
    @classmethod
    def diagnosis_evidence_is_unique(
        cls, value: tuple[EvidenceRef, ...], info: object
    ) -> tuple[EvidenceRef, ...]:
        unique_values(value, getattr(info, "field_name", "evidence_refs"))
        return value

    @field_validator("created_at")
    @classmethod
    def diagnosis_created_at_is_aware(cls, value: datetime) -> datetime:
        return aware_timestamp(value, "created_at")

    @model_validator(mode="after")
    def diagnosis_is_typed_and_lineaged(self) -> "ProblemDiagnosis":
        if set(self.supporting_evidence_refs) & set(self.conflicting_evidence_refs):
            raise ValueError("supporting and conflicting evidence must be disjoint")
        groups = (
            ("affected_agent_refs", self.affected_agent_refs, ComponentType.AGENT),
            ("affected_prompt_refs", self.affected_prompt_refs, ComponentType.PROMPT),
            ("affected_skill_refs", self.affected_skill_refs, ComponentType.SKILL),
            ("affected_tool_refs", self.affected_tool_refs, ComponentType.TOOL),
            ("affected_policy_refs", self.affected_policy_refs, ComponentType.POLICY),
        )
        for name, refs, expected in groups:
            unique_refs(refs, name)
            for ref in refs:
                _require_type(ref, expected, name)
        if (
            self.diagnosis_type
            in {
                DiagnosisType.AGENT_GAP,
                DiagnosisType.SKILL_GAP,
                DiagnosisType.TOOL_GAP,
                DiagnosisType.PROMPT_GAP,
            }
            and self.inventory_snapshot_id is None
        ):
            raise ValueError("component-gap diagnoses require inventory_snapshot_id")
        return self


class ChangeTarget(StrEnum):
    """First-class Autopilot change target."""

    AGENT = "AGENT"
    TOOL = "TOOL"
    SKILL = "SKILL"
    PROMPT = "PROMPT"
    NO_CHANGE = "NO_CHANGE"


class ChangeStrategy(StrEnum):
    """How the selected target should be satisfied."""

    REUSE = "REUSE"
    EXTEND = "EXTEND"
    COMPOSE = "COMPOSE"
    CREATE = "CREATE"
    NO_CHANGE = "NO_CHANGE"


class ComponentChangeOperation(StrEnum):
    """Explicit graph and component operations allowed in proposals."""

    CREATE_AGENT = "CREATE_AGENT"
    COMPOSE_AGENT = "COMPOSE_AGENT"
    EXTEND_AGENT = "EXTEND_AGENT"
    ADD_AGENT_TOOL_REF = "ADD_AGENT_TOOL_REF"
    REMOVE_AGENT_TOOL_REF = "REMOVE_AGENT_TOOL_REF"
    ADD_AGENT_SKILL_REF = "ADD_AGENT_SKILL_REF"
    REMOVE_AGENT_SKILL_REF = "REMOVE_AGENT_SKILL_REF"
    CHANGE_AGENT_PROMPT_REF = "CHANGE_AGENT_PROMPT_REF"
    CREATE_TOOL = "CREATE_TOOL"
    CREATE_SKILL = "CREATE_SKILL"
    ADD_SKILL_REQUIRED_TOOL_REF = "ADD_SKILL_REQUIRED_TOOL_REF"
    ADD_SKILL_OPTIONAL_TOOL_REF = "ADD_SKILL_OPTIONAL_TOOL_REF"
    REMOVE_SKILL_TOOL_REF = "REMOVE_SKILL_TOOL_REF"
    CREATE_PROMPT = "CREATE_PROMPT"
    NO_CHANGE = "NO_CHANGE"


class ComponentChange(ImmutableModel):
    """One exact before-to-after governed component or relationship change."""

    operation: ComponentChangeOperation
    subject_before_ref: ExactComponentReference | None = None
    subject_after_ref: ExactComponentReference | None = None
    related_before_ref: ExactComponentReference | None = None
    related_after_ref: ExactComponentReference | None = None
    rationale: str = Field(min_length=1)

    @field_validator("rationale")
    @classmethod
    def rationale_is_non_blank(cls, value: str) -> str:
        return non_blank(value, "rationale")

    @model_validator(mode="after")
    def operation_is_explicit(self) -> "ComponentChange":
        operation = self.operation
        if operation is ComponentChangeOperation.NO_CHANGE:
            if any(
                ref is not None
                for ref in (
                    self.subject_before_ref,
                    self.subject_after_ref,
                    self.related_before_ref,
                    self.related_after_ref,
                )
            ):
                raise ValueError("NO_CHANGE must not contain component references")
            return self

        if operation is ComponentChangeOperation.CREATE_AGENT:
            self._require_creation(ComponentType.AGENT)
        elif operation is ComponentChangeOperation.CREATE_TOOL:
            self._require_creation(ComponentType.TOOL)
        elif operation is ComponentChangeOperation.CREATE_SKILL:
            self._require_creation(ComponentType.SKILL)
        elif operation is ComponentChangeOperation.CREATE_PROMPT:
            self._require_creation(ComponentType.PROMPT)
        elif operation in {
            ComponentChangeOperation.COMPOSE_AGENT,
            ComponentChangeOperation.EXTEND_AGENT,
        }:
            self._require_subject_transition(ComponentType.AGENT)
        elif operation in {
            ComponentChangeOperation.ADD_AGENT_TOOL_REF,
            ComponentChangeOperation.REMOVE_AGENT_TOOL_REF,
        }:
            self._require_subject_transition(ComponentType.AGENT)
            if operation is ComponentChangeOperation.ADD_AGENT_TOOL_REF:
                self._require_related_add(ComponentType.TOOL)
            else:
                self._require_related_remove(ComponentType.TOOL)
        elif operation in {
            ComponentChangeOperation.ADD_AGENT_SKILL_REF,
            ComponentChangeOperation.REMOVE_AGENT_SKILL_REF,
        }:
            self._require_subject_transition(ComponentType.AGENT)
            if operation is ComponentChangeOperation.ADD_AGENT_SKILL_REF:
                self._require_related_add(ComponentType.SKILL)
            else:
                self._require_related_remove(ComponentType.SKILL)
        elif operation is ComponentChangeOperation.CHANGE_AGENT_PROMPT_REF:
            self._require_subject_transition(ComponentType.PROMPT)
            self._require_related_transition(ComponentType.AGENT)
        elif operation in {
            ComponentChangeOperation.ADD_SKILL_REQUIRED_TOOL_REF,
            ComponentChangeOperation.ADD_SKILL_OPTIONAL_TOOL_REF,
            ComponentChangeOperation.REMOVE_SKILL_TOOL_REF,
        }:
            self._require_subject_transition(ComponentType.SKILL)
            if operation is ComponentChangeOperation.REMOVE_SKILL_TOOL_REF:
                self._require_related_remove(ComponentType.TOOL)
            else:
                self._require_related_add(ComponentType.TOOL)
        return self

    def _require_creation(self, expected: ComponentType) -> None:
        if self.subject_before_ref is not None:
            raise ValueError("creation operations must not have subject_before_ref")
        _require_ref_type(self.subject_after_ref, expected, "subject_after_ref")
        if self.related_before_ref is not None or self.related_after_ref is not None:
            raise ValueError("creation operations must not contain related component references")

    def _require_subject_transition(self, expected: ComponentType) -> None:
        before = _require_ref_type(self.subject_before_ref, expected, "subject_before_ref")
        after = _require_ref_type(self.subject_after_ref, expected, "subject_after_ref")
        if before.identity == after.identity:
            raise ValueError("subject_before_ref and subject_after_ref must differ")

    def _require_related_add(self, expected: ComponentType) -> None:
        if self.related_before_ref is not None:
            raise ValueError("add operations must not have related_before_ref")
        _require_ref_type(self.related_after_ref, expected, "related_after_ref")

    def _require_related_remove(self, expected: ComponentType) -> None:
        _require_ref_type(self.related_before_ref, expected, "related_before_ref")
        if self.related_after_ref is not None:
            raise ValueError("remove operations must not have related_after_ref")

    def _require_related_transition(self, expected: ComponentType) -> None:
        before = _require_ref_type(self.related_before_ref, expected, "related_before_ref")
        after = _require_ref_type(self.related_after_ref, expected, "related_after_ref")
        if before.identity == after.identity:
            raise ValueError("related_before_ref and related_after_ref must differ")


ProposedComponentChange = ComponentChange


class ChangeProposal(ImmutableModel):
    """Smallest exact, evidence-backed governed change proposal."""

    proposal_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    opportunity_id: str | None = Field(default=None, min_length=1)
    cluster_id: str | None = Field(default=None, min_length=1)
    diagnosis_id: str = Field(min_length=1)
    change_target: ChangeTarget
    strategy: ChangeStrategy
    baseline_inventory_snapshot_id: str = Field(min_length=1)
    target_agent_refs: tuple[ExactComponentReference, ...] = ()
    proposed_component_changes: tuple[ComponentChange, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    risk_classification: str = Field(min_length=1)
    requires_human_review: Literal[True] = True
    created_at: datetime

    @field_validator(
        "proposal_id",
        "tenant_id",
        "diagnosis_id",
        "baseline_inventory_snapshot_id",
        "rationale",
        "risk_classification",
    )
    @classmethod
    def proposal_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("created_at")
    @classmethod
    def proposal_created_at_is_aware(cls, value: datetime) -> datetime:
        return aware_timestamp(value, "created_at")

    @model_validator(mode="after")
    def proposal_is_consistent(self) -> "ChangeProposal":
        if (self.opportunity_id is None) == (self.cluster_id is None):
            raise ValueError("exactly one of opportunity_id or cluster_id is required")
        if self.change_target is ChangeTarget.NO_CHANGE:
            raise ValueError("ChangeProposal cannot target NO_CHANGE")
        if self.strategy is ChangeStrategy.NO_CHANGE:
            raise ValueError("ChangeProposal cannot use NO_CHANGE strategy")
        if self.strategy is ChangeStrategy.REUSE:
            raise ValueError("REUSE must terminate as an OperationalDisposition")
        unique_refs(self.target_agent_refs, "target_agent_refs")
        for ref in self.target_agent_refs:
            _require_type(ref, ComponentType.AGENT, "target_agent_refs")
        unique_values(self.evidence_refs, "evidence_refs")
        operations = self.proposed_component_changes
        if any(change.operation is ComponentChangeOperation.NO_CHANGE for change in operations):
            raise ValueError("NO_CHANGE is not a proposal operation")
        keys = [
            (
                change.operation,
                _ref_identity(change.subject_before_ref),
                _ref_identity(change.subject_after_ref),
                _ref_identity(change.related_before_ref),
                _ref_identity(change.related_after_ref),
            )
            for change in operations
        ]
        unique_values(keys, "proposed_component_changes")
        allowed = _allowed_operations(self.change_target)
        if any(change.operation not in allowed for change in operations):
            raise ValueError("a proposed operation is inconsistent with change_target")
        create_operations = {
            ComponentChangeOperation.CREATE_AGENT,
            ComponentChangeOperation.CREATE_TOOL,
            ComponentChangeOperation.CREATE_SKILL,
            ComponentChangeOperation.CREATE_PROMPT,
        }
        if self.strategy is ChangeStrategy.CREATE and not any(
            change.operation in create_operations for change in operations
        ):
            raise ValueError("CREATE proposals must contain a component creation operation")
        if self.strategy is ChangeStrategy.EXTEND and any(
            change.operation in create_operations for change in operations
        ):
            raise ValueError("EXTEND proposals must not create a component")
        if self.strategy is ChangeStrategy.COMPOSE and not any(
            change.operation is ComponentChangeOperation.COMPOSE_AGENT for change in operations
        ):
            raise ValueError("COMPOSE proposals must contain COMPOSE_AGENT")
        relationship_operations = {
            ComponentChangeOperation.ADD_AGENT_TOOL_REF,
            ComponentChangeOperation.REMOVE_AGENT_TOOL_REF,
            ComponentChangeOperation.ADD_AGENT_SKILL_REF,
            ComponentChangeOperation.REMOVE_AGENT_SKILL_REF,
            ComponentChangeOperation.CHANGE_AGENT_PROMPT_REF,
        }
        if any(change.operation in relationship_operations for change in operations):
            target_agent_keys = {
                (ref.source_system, ref.component_id) for ref in self.target_agent_refs
            }
            operation_agent_keys = {
                (ref.source_system, ref.component_id)
                for change in operations
                for ref in (
                    change.subject_before_ref,
                    change.subject_after_ref,
                    change.related_before_ref,
                    change.related_after_ref,
                )
                if ref is not None and ref.component_type is ComponentType.AGENT
            }
            if not operation_agent_keys.issubset(target_agent_keys):
                raise ValueError(
                    "target_agent_refs must declare every Agent used by a relationship change"
                )
        return self


class OperationalDisposition(ImmutableModel):
    """Terminal no-change record for external, governance, or insufficient causes."""

    disposition_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    diagnosis_id: str = Field(min_length=1)
    strategy: Literal[ChangeStrategy.NO_CHANGE] = ChangeStrategy.NO_CHANGE
    reason: str = Field(min_length=1)
    owner_boundary: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    status: str = Field(min_length=1)
    created_at: datetime

    @field_validator(
        "disposition_id",
        "tenant_id",
        "diagnosis_id",
        "reason",
        "owner_boundary",
        "recommended_action",
        "status",
    )
    @classmethod
    def disposition_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("evidence_refs")
    @classmethod
    def disposition_evidence_is_unique(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        unique_values(value, "evidence_refs")
        return value

    @field_validator("created_at")
    @classmethod
    def disposition_created_at_is_aware(cls, value: datetime) -> datetime:
        return aware_timestamp(value, "created_at")


class CandidateReference(ImmutableModel):
    """Autopilot-owned reference to a Harness-built evaluation candidate."""

    candidate_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    agent_ref: ExactComponentReference
    manifest_id: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    registry_snapshot_id: str = Field(min_length=1)
    prompt_ref: ExactComponentReference
    skill_refs: tuple[ExactComponentReference, ...] = ()
    tool_refs: tuple[ExactComponentReference, ...] = ()
    policy_refs: tuple[ExactComponentReference, ...] = ()

    @field_validator(
        "candidate_id", "tenant_id", "manifest_id", "manifest_digest", "registry_snapshot_id"
    )
    @classmethod
    def candidate_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def candidate_refs_are_typed_and_unique(self) -> "CandidateReference":
        groups = (
            ("agent_ref", (self.agent_ref,), ComponentType.AGENT),
            ("prompt_ref", (self.prompt_ref,), ComponentType.PROMPT),
            ("skill_refs", self.skill_refs, ComponentType.SKILL),
            ("tool_refs", self.tool_refs, ComponentType.TOOL),
            ("policy_refs", self.policy_refs, ComponentType.POLICY),
        )
        for name, refs, expected in groups:
            unique_refs(refs, name)
            for ref in refs:
                _require_type(ref, expected, name)
        return self


class EvaluationReference(ImmutableModel):
    """Autopilot-owned reference to Lab evaluation evidence."""

    evaluation_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    baseline_candidate_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    comparison_id: str | None = Field(default=None, min_length=1)
    promotion_evidence_id: str | None = Field(default=None, min_length=1)
    status: str = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @field_validator(
        "evaluation_id", "tenant_id", "baseline_candidate_id", "candidate_id", "status"
    )
    @classmethod
    def evaluation_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("evidence_refs")
    @classmethod
    def evaluation_evidence_is_unique(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        unique_values(value, "evidence_refs")
        return value

    @model_validator(mode="after")
    def baseline_and_candidate_differ(self) -> "EvaluationReference":
        if self.baseline_candidate_id == self.candidate_id:
            raise ValueError("baseline_candidate_id and candidate_id must differ")
        return self


class PilotRecommendation(ImmutableModel):
    """Evidence-backed recommendation that still requires human approval."""

    recommendation_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    candidate_reference: CandidateReference
    evaluation_reference: EvaluationReference
    summary: str = Field(min_length=1)
    expected_operational_impact: str = Field(min_length=1)
    known_risks: tuple[str, ...] = ()
    pilot_scope: dict[str, JsonValue] = Field(min_length=1)
    success_criteria: tuple[str, ...] = Field(min_length=1)
    rollback_conditions: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    requires_human_approval: bool = True
    status: str = Field(min_length=1)
    created_at: datetime

    @field_validator(
        "recommendation_id",
        "tenant_id",
        "proposal_id",
        "summary",
        "expected_operational_impact",
        "status",
    )
    @classmethod
    def recommendation_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("known_risks", "success_criteria", "rollback_conditions")
    @classmethod
    def recommendation_lists_are_unique(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        unique_values(value, getattr(info, "field_name", "values"))
        if any(not item.strip() for item in value):
            raise ValueError(
                f"{getattr(info, 'field_name', 'values')} must not contain blank values"
            )
        return value

    @field_validator("evidence_refs")
    @classmethod
    def recommendation_evidence_is_unique(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        unique_values(value, "evidence_refs")
        return value

    @field_validator("created_at")
    @classmethod
    def recommendation_created_at_is_aware(cls, value: datetime) -> datetime:
        return aware_timestamp(value, "created_at")

    @model_validator(mode="after")
    def recommendation_is_human_gated(self) -> "PilotRecommendation":
        if not self.requires_human_approval:
            raise ValueError("pilot recommendations always require human approval")
        if self.candidate_reference.tenant_id != self.tenant_id:
            raise ValueError("candidate reference tenant does not match recommendation tenant")
        if self.evaluation_reference.tenant_id != self.tenant_id:
            raise ValueError("evaluation reference tenant does not match recommendation tenant")
        if self.evaluation_reference.candidate_id != self.candidate_reference.candidate_id:
            raise ValueError("evaluation candidate does not match candidate reference")
        return self


class DecisionSubjectType(StrEnum):
    """Allowed terminal decision subjects."""

    PILOT_RECOMMENDATION = "PILOT_RECOMMENDATION"
    OPERATIONAL_DISPOSITION = "OPERATIONAL_DISPOSITION"


class DecisionRecord(ImmutableModel):
    """Human or accountable-actor decision with preserved evidence lineage."""

    decision_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    subject_type: DecisionSubjectType
    subject_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    occurred_at: datetime
    reason: str = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @field_validator("decision_id", "tenant_id", "subject_id", "decision", "actor_ref", "reason")
    @classmethod
    def decision_text_is_non_blank(cls, value: str, info: object) -> str:
        return non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("evidence_refs")
    @classmethod
    def decision_evidence_is_unique(cls, value: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
        unique_values(value, "evidence_refs")
        return value

    @field_validator("occurred_at")
    @classmethod
    def decision_timestamp_is_aware(cls, value: datetime) -> datetime:
        return aware_timestamp(value, "occurred_at")

    @model_validator(mode="after")
    def decision_subject_is_not_self(self) -> "DecisionRecord":
        if self.decision_id == self.subject_id:
            raise ValueError("decision_id must differ from subject_id")
        return self


def _require_type(
    reference: ExactComponentReference,
    expected: ComponentType,
    field_name: str,
) -> None:
    if reference.component_type is not expected:
        raise ValueError(f"{field_name} must contain {expected.value} references")


def _require_ref_type(
    reference: ExactComponentReference | None,
    expected: ComponentType,
    field_name: str,
) -> ExactComponentReference:
    if reference is None:
        raise ValueError(f"{field_name} is required")
    _require_type(reference, expected, field_name)
    return reference


def _require_distinct(first: ExactComponentReference, second: ExactComponentReference) -> None:
    if first.identity == second.identity:
        raise ValueError("relationship endpoints must differ")


def _ref_identity(reference: ExactComponentReference | None) -> str | None:
    return None if reference is None else reference.identity


def _require_edge_membership(edge: object, snapshot: AgentSystemInventorySnapshot) -> None:
    refs = {
        ref.identity
        for group in (
            snapshot.agent_refs,
            snapshot.prompt_refs,
            snapshot.skill_refs,
            snapshot.tool_refs,
            snapshot.policy_refs,
        )
        for ref in group
    }
    edge_refs = (
        getattr(edge, "agent_ref", None),
        getattr(edge, "prompt_ref", None),
        getattr(edge, "skill_ref", None),
        getattr(edge, "tool_ref", None),
    )
    if any(ref is not None and ref.identity not in refs for ref in edge_refs):
        raise ValueError("inventory relationship endpoints must exist in the snapshot")


def _allowed_operations(target: ChangeTarget) -> set[ComponentChangeOperation]:
    if target is ChangeTarget.AGENT:
        return {
            ComponentChangeOperation.CREATE_AGENT,
            ComponentChangeOperation.COMPOSE_AGENT,
            ComponentChangeOperation.EXTEND_AGENT,
        }
    if target is ChangeTarget.TOOL:
        return {
            ComponentChangeOperation.CREATE_TOOL,
            ComponentChangeOperation.ADD_AGENT_TOOL_REF,
            ComponentChangeOperation.REMOVE_AGENT_TOOL_REF,
        }
    if target is ChangeTarget.SKILL:
        return {
            ComponentChangeOperation.CREATE_SKILL,
            ComponentChangeOperation.ADD_SKILL_REQUIRED_TOOL_REF,
            ComponentChangeOperation.ADD_SKILL_OPTIONAL_TOOL_REF,
            ComponentChangeOperation.REMOVE_SKILL_TOOL_REF,
            ComponentChangeOperation.ADD_AGENT_SKILL_REF,
            ComponentChangeOperation.REMOVE_AGENT_SKILL_REF,
        }
    if target is ChangeTarget.PROMPT:
        return {
            ComponentChangeOperation.CREATE_PROMPT,
            ComponentChangeOperation.CHANGE_AGENT_PROMPT_REF,
        }
    return set()


__all__ = [
    "AgentPromptEdge",
    "AgentSkillEdge",
    "AgentSystemInventorySnapshot",
    "AgentToolAuthorityEdge",
    "CandidateReference",
    "ChangeProposal",
    "ChangeStrategy",
    "ChangeTarget",
    "ComponentChange",
    "ComponentChangeOperation",
    "ComponentType",
    "DecisionRecord",
    "DecisionSubjectType",
    "DiagnosisType",
    "EvaluationReference",
    "EvidenceQuality",
    "EvidenceRef",
    "ExactComponentReference",
    "OperationalDisposition",
    "OperationalSignal",
    "Opportunity",
    "OpportunityCluster",
    "OpportunityPattern",
    "OpportunityPriorityFactors",
    "PilotRecommendation",
    "ProblemDiagnosis",
    "ProposedComponentChange",
    "SkillToolDependencyEdge",
]
