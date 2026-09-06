"""Deterministic operational diagnosis and precedence guards."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .contracts import (
    AgentSystemInventorySnapshot,
    ComponentType,
    DiagnosisType,
    DiagnosticFactKey,
    EvidenceQuality,
    ExactComponentReference,
    OperationalSignal,
    OpportunityCluster,
    ProblemDiagnosis,
)
from .contracts.common import non_blank
from .storage.ports import (
    OperationalSignalStore,
    OpportunityClusterStore,
    OpportunityStore,
)

DiagnosisFallback = Callable[
    [OpportunityCluster, tuple[OperationalSignal, ...], AgentSystemInventorySnapshot | None],
    DiagnosisType | str | None,
]

_COMPONENT_GAPS = {
    DiagnosisType.AGENT_GAP,
    DiagnosisType.SKILL_GAP,
    DiagnosisType.TOOL_GAP,
    DiagnosisType.PROMPT_GAP,
}
_UNRELIABLE_QUALITY = {
    EvidenceQuality.PARTIAL,
    EvidenceQuality.STALE,
    EvidenceQuality.CONFLICTING,
    EvidenceQuality.UNAVAILABLE,
}
_UNAVAILABLE_TOKENS = {
    "unavailable",
    "unknown",
    "down",
    "failed",
    "failure",
    "outage",
    "offline",
    "inaccessible",
}
_KNOWLEDGE_ISSUE_TOKENS = {
    "missing",
    "stale",
    "contradictory",
    "inaccessible",
    "unavailable",
    "failed",
}
_BUSINESS_BLOCKER_EVENT_TYPES = {
    "business_service_outage",
    "business_dependency_unavailable",
    "external_service_outage",
    "external_dependency_unavailable",
}
_POLICY_BLOCKER_EVENT_TYPES = {
    "policy_denied",
    "policy_denial",
    "permission_denied",
    "permission_forbidden",
}
_APPROVAL_FRICTION_EVENT_TYPES = {
    "approval_pending",
    "approval_waiting",
    "approval_requested",
}
_KNOWLEDGE_ISSUE_EVENT_TYPES = {
    "knowledge_source_missing",
    "knowledge_source_stale",
    "knowledge_source_unavailable",
    "knowledge_source_inaccessible",
    "knowledge_source_contradictory",
    "retrieval_failed",
}
_AGENT_GAP_EVENT_TYPES = {"agent_gap", "agent_composition_missing"}
_SKILL_GAP_EVENT_TYPES = {"skill_gap", "skill_missing", "skill_insufficient"}
_PROMPT_GAP_EVENT_TYPES = {
    "prompt_gap",
    "prompt_failure",
    "behavioral_instruction_failure",
}
_USABLE_LIFECYCLES = {"ACTIVE", "VALIDATED"}


class DiagnosisError(ValueError):
    """Base error for an invalid or unresolved diagnosis request."""


class DiagnosisUndeterminedError(DiagnosisError):
    """Raised when no deterministic guard or validated fallback can decide."""


class OperationalDiagnoser:
    """Apply the build-plan diagnosis precedence in a fixed order."""

    def __init__(
        self,
        fallback: DiagnosisFallback | None = None,
        *,
        cluster_store: OpportunityClusterStore | None = None,
        opportunity_store: OpportunityStore | None = None,
        signal_store: OperationalSignalStore | None = None,
    ) -> None:
        self.fallback = fallback
        self.cluster_store = cluster_store
        self.opportunity_store = opportunity_store
        self.signal_store = signal_store

    def diagnose_cluster(
        self,
        cluster_id: str,
        tenant_id: str,
        inventory: AgentSystemInventorySnapshot | None = None,
        *,
        target_agent_ref: ExactComponentReference | None = None,
        required_skill_ref: ExactComponentReference | None = None,
        required_tool_ref: ExactComponentReference | None = None,
        required_prompt_ref: ExactComponentReference | None = None,
    ) -> ProblemDiagnosis:
        """Diagnose a stored cluster from its exact contributing evidence only."""

        tenant = non_blank(tenant_id, "tenant_id")
        cluster_key = non_blank(cluster_id, "cluster_id")
        cluster_store = self.cluster_store
        opportunity_store = self.opportunity_store
        signal_store = self.signal_store
        if cluster_store is None or opportunity_store is None or signal_store is None:
            raise DiagnosisError(
                "diagnose_cluster requires cluster, opportunity, and signal stores"
            )
        cluster = cluster_store.get(cluster_key, tenant_id=tenant)
        if cluster is None:
            raise DiagnosisError("diagnosis cluster was not found in the requested tenant")
        if cluster.tenant_id != tenant:
            raise DiagnosisError("diagnosis cluster is outside the requested tenant")

        opportunities = []
        for opportunity_id in cluster.opportunity_ids:
            opportunity = opportunity_store.get(opportunity_id, tenant_id=tenant)
            if opportunity is None:
                raise DiagnosisError(
                    f"missing opportunity contributing to cluster: {opportunity_id}"
                )
            if opportunity.tenant_id != cluster.tenant_id:
                raise DiagnosisError("cluster opportunity is outside the cluster tenant")
            opportunities.append(opportunity)

        linked_signal_ids = {
            signal_id
            for opportunity in opportunities
            for signal_id in opportunity.source_signal_ids
        }
        linked_evidence_refs = {
            evidence_ref
            for opportunity in opportunities
            for evidence_ref in opportunity.evidence_refs
        }
        if linked_signal_ids != set(cluster.source_signal_ids):
            raise DiagnosisError("cluster source signal lineage does not match its opportunities")
        if linked_evidence_refs != set(cluster.evidence_refs):
            raise DiagnosisError("cluster evidence lineage does not match its opportunities")
        if not linked_signal_ids:
            raise DiagnosisError("cluster must declare contributing source signals")

        signals: list[OperationalSignal] = []
        for signal_id in sorted(linked_signal_ids):
            signal = signal_store.get(signal_id, tenant_id=tenant)
            if signal is None:
                raise DiagnosisError(f"missing contributing signal: {signal_id}")
            if signal.tenant_id != cluster.tenant_id:
                raise DiagnosisError("contributing signal is outside the cluster tenant")
            if not set(signal.evidence_refs).issubset(linked_evidence_refs):
                raise DiagnosisError(
                    f"signal evidence is not declared by cluster lineage: {signal_id}"
                )
            signals.append(signal)

        return self.diagnose(
            cluster,
            tuple(signals),
            inventory,
            target_agent_ref=target_agent_ref,
            required_skill_ref=required_skill_ref,
            required_tool_ref=required_tool_ref,
            required_prompt_ref=required_prompt_ref,
        )

    def diagnose(
        self,
        cluster: OpportunityCluster,
        signals: Iterable[OperationalSignal],
        inventory: AgentSystemInventorySnapshot | None = None,
        *,
        target_agent_ref: ExactComponentReference | None = None,
        required_skill_ref: ExactComponentReference | None = None,
        required_tool_ref: ExactComponentReference | None = None,
        required_prompt_ref: ExactComponentReference | None = None,
    ) -> ProblemDiagnosis:
        """Return one primary diagnosis for one cluster.

        Requirement references are optional because a source may identify an
        operation without an exact component version. The diagnoser never
        invents a missing version; proposal construction must receive an exact
        reference before it can produce a candidate-path proposal.
        """

        normalized_signals = _deduplicate_signals(signals)
        _validate_scope(cluster, normalized_signals, inventory)
        requirements = _resolve_requirements(
            normalized_signals,
            inventory,
            target_agent_ref=target_agent_ref,
            required_skill_ref=required_skill_ref,
            required_tool_ref=required_tool_ref,
            required_prompt_ref=required_prompt_ref,
        )

        quality_issue = _quality_issue(normalized_signals)
        if quality_issue is not None:
            return self._build(
                cluster,
                normalized_signals,
                inventory,
                diagnosis_type=DiagnosisType.DATA_QUALITY_ISSUE,
                precedence_rule="evidence_quality",
                summary=quality_issue,
                confidence=_quality_confidence(normalized_signals),
                requirements=requirements,
            )

        guards: tuple[tuple[DiagnosisType, Callable[[], bool], str, float, str], ...] = (
            (
                DiagnosisType.BUSINESS_DEPENDENCY,
                lambda: _has_business_dependency_blocker(normalized_signals),
                (
                    "The required external business capability is unavailable or is the "
                    "blocking dependency."
                ),
                0.9,
                "business_dependency",
            ),
            (
                DiagnosisType.POLICY_CONSTRAINT,
                lambda: _has_policy_constraint(normalized_signals),
                "Policy or permission evidence intentionally blocks the required operation.",
                0.9,
                "policy_constraint",
            ),
            (
                DiagnosisType.APPROVAL_FRICTION,
                lambda: _has_approval_friction(normalized_signals),
                "A valid approval flow is the operational bottleneck.",
                0.85,
                "approval_friction",
            ),
            (
                DiagnosisType.KNOWLEDGE_SOURCE_ISSUE,
                lambda: _has_knowledge_source_issue(normalized_signals),
                "Required knowledge is missing, stale, contradictory, or inaccessible.",
                0.85,
                "knowledge_source",
            ),
        )
        for diagnosis_type, guard, summary, confidence, precedence_rule in guards:
            if guard():
                return self._build(
                    cluster,
                    normalized_signals,
                    inventory,
                    diagnosis_type=diagnosis_type,
                    precedence_rule=precedence_rule,
                    summary=summary,
                    confidence=confidence,
                    requirements=requirements,
                )

        if inventory is None:
            return self._build(
                cluster,
                normalized_signals,
                inventory,
                diagnosis_type=DiagnosisType.DATA_QUALITY_ISSUE,
                precedence_rule="inventory_required",
                summary="An exact Harness inventory is required before component-gap diagnosis.",
                confidence=0.0,
                requirements=requirements,
            )

        if requirements.target_agent_ref is None:
            if _explicit_agent_gap(normalized_signals):
                return self._build(
                    cluster,
                    normalized_signals,
                    inventory,
                    diagnosis_type=DiagnosisType.AGENT_GAP,
                    precedence_rule="agent_existence",
                    summary="The required governed Agent or composition is not available.",
                    confidence=0.9,
                    requirements=requirements,
                )
            return self._fallback_or_raise(cluster, normalized_signals, inventory, requirements)

        if not _available(inventory, requirements.target_agent_ref):
            return self._build(
                cluster,
                normalized_signals,
                inventory,
                diagnosis_type=DiagnosisType.AGENT_GAP,
                precedence_rule="agent_existence",
                summary="The required governed Agent or composition is not available.",
                confidence=0.9,
                requirements=requirements,
            )

        if _explicit_agent_gap(normalized_signals):
            return self._build(
                cluster,
                normalized_signals,
                inventory,
                diagnosis_type=DiagnosisType.AGENT_GAP,
                precedence_rule="agent_composition",
                summary="The existing Agent composition is not sufficient for the operation.",
                confidence=0.85,
                requirements=requirements,
            )

        if _explicit_skill_gap(normalized_signals) or (
            requirements.required_skill_ref is not None
            and not _available(inventory, requirements.required_skill_ref)
        ):
            return self._build(
                cluster,
                normalized_signals,
                inventory,
                diagnosis_type=DiagnosisType.SKILL_GAP,
                precedence_rule="skill_existence",
                summary="The required reusable competence is not available.",
                confidence=0.9,
                requirements=requirements,
            )

        if _tool_gap(
            normalized_signals,
            inventory,
            required_tool_ref=requirements.required_tool_ref,
            target_agent_ref=requirements.target_agent_ref,
        ):
            return self._build(
                cluster,
                normalized_signals,
                inventory,
                diagnosis_type=DiagnosisType.TOOL_GAP,
                precedence_rule="tool_existence_and_authority",
                summary=(
                    "The required atomic operation is missing or is not direct executable "
                    "authority for the Agent."
                ),
                confidence=0.9,
                requirements=requirements,
            )

        if _explicit_prompt_gap(normalized_signals):
            return self._build(
                cluster,
                normalized_signals,
                inventory,
                diagnosis_type=DiagnosisType.PROMPT_GAP,
                precedence_rule="prompt_behavior",
                summary=(
                    "The governed components and authority exist, but behavioral instructions "
                    "repeatedly fail."
                ),
                confidence=0.85,
                requirements=requirements,
            )

        return self._fallback_or_raise(cluster, normalized_signals, inventory, requirements)

    def _fallback_or_raise(
        self,
        cluster: OpportunityCluster,
        signals: tuple[OperationalSignal, ...],
        inventory: AgentSystemInventorySnapshot,
        requirements: _Requirements,
    ) -> ProblemDiagnosis:
        if self.fallback is None:
            raise DiagnosisUndeterminedError(
                "deterministic diagnosis guards could not resolve the cluster"
            )
        suggestion = self.fallback(cluster, signals, inventory)
        if suggestion is None:
            raise DiagnosisUndeterminedError("diagnosis fallback did not classify the cluster")
        try:
            diagnosis_type = DiagnosisType(suggestion)
        except (TypeError, ValueError) as exc:
            raise DiagnosisError("diagnosis fallback returned an invalid taxonomy value") from exc
        return self._build(
            cluster,
            signals,
            inventory,
            diagnosis_type=diagnosis_type,
            precedence_rule="validated_model_fallback",
            summary="A bounded fallback classified the unresolved operational pattern.",
            confidence=0.5,
            requirements=requirements,
        )

    def _build(
        self,
        cluster: OpportunityCluster,
        signals: tuple[OperationalSignal, ...],
        inventory: AgentSystemInventorySnapshot | None,
        *,
        diagnosis_type: DiagnosisType,
        precedence_rule: str,
        summary: str,
        confidence: float,
        requirements: _Requirements,
    ) -> ProblemDiagnosis:
        if diagnosis_type in _COMPONENT_GAPS and inventory is None:
            raise DiagnosisError("component-gap diagnoses require an inventory snapshot")
        supporting, conflicting = _evidence_partition(cluster, signals)
        affected = _affected_refs(diagnosis_type, requirements, inventory, signals)
        identity_payload = {
            "tenant_id": cluster.tenant_id,
            "cluster_id": cluster.cluster_id,
            "diagnosis_type": diagnosis_type.value,
            "precedence_rule": precedence_rule,
            "inventory_snapshot_id": inventory.snapshot_id if inventory else None,
            "affected": {
                name: tuple(ref.identity for ref in refs) for name, refs in affected.items()
            },
            "supporting": supporting,
            "conflicting": conflicting,
        }
        diagnosis_id = (
            "diagnosis_"
            + hashlib.sha256(
                json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:32]
        )
        return ProblemDiagnosis(
            diagnosis_id=diagnosis_id,
            tenant_id=cluster.tenant_id,
            cluster_id=cluster.cluster_id,
            inventory_snapshot_id=inventory.snapshot_id if inventory else None,
            diagnosis_type=diagnosis_type,
            summary=summary,
            precedence_rule=precedence_rule,
            supporting_evidence_refs=supporting,
            conflicting_evidence_refs=conflicting,
            confidence=confidence,
            affected_agent_refs=affected["agent"],
            affected_prompt_refs=affected["prompt"],
            affected_skill_refs=affected["skill"],
            affected_tool_refs=affected["tool"],
            affected_policy_refs=affected["policy"],
            created_at=cluster.window_end,
        )


@dataclass(frozen=True)
class _Requirements:
    target_agent_ref: ExactComponentReference | None
    required_prompt_ref: ExactComponentReference | None
    required_skill_ref: ExactComponentReference | None
    required_tool_ref: ExactComponentReference | None


def _resolve_requirements(
    signals: Sequence[OperationalSignal],
    inventory: AgentSystemInventorySnapshot | None,
    *,
    target_agent_ref: ExactComponentReference | None,
    required_skill_ref: ExactComponentReference | None,
    required_tool_ref: ExactComponentReference | None,
    required_prompt_ref: ExactComponentReference | None,
) -> _Requirements:
    agent = _validated_ref(target_agent_ref, ComponentType.AGENT, "target_agent_ref")
    prompt = _validated_ref(required_prompt_ref, ComponentType.PROMPT, "required_prompt_ref")
    skill = _validated_ref(required_skill_ref, ComponentType.SKILL, "required_skill_ref")
    tool = _validated_ref(required_tool_ref, ComponentType.TOOL, "required_tool_ref")
    if inventory is not None:
        agent = agent or _infer_agent(signals, inventory)
        if agent is not None:
            agent_prompt = {
                edge.agent_ref.identity: edge.prompt_ref for edge in inventory.agent_to_prompt_edges
            }.get(agent.identity)
            prompt = prompt or agent_prompt
            if skill is None:
                skill = _infer_skill(signals, inventory, agent)
            if tool is None:
                tool = _infer_tool(signals, inventory)
    if tool is None:
        tool = _parse_tool_ref(signals)
    return _Requirements(agent, prompt, skill, tool)


def _validated_ref(
    reference: ExactComponentReference | None,
    expected: ComponentType,
    name: str,
) -> ExactComponentReference | None:
    if reference is None:
        return None
    if reference.component_type is not expected:
        raise DiagnosisError(f"{name} must identify {expected.value}")
    return reference


def _infer_agent(
    signals: Sequence[OperationalSignal], inventory: AgentSystemInventorySnapshot
) -> ExactComponentReference | None:
    candidates = list(inventory.agent_refs)
    ids = {signal.agent_id for signal in signals if signal.agent_id is not None}
    if ids:
        candidates = [candidate for candidate in candidates if candidate.component_id in ids]
    versions = {
        value
        for signal in signals
        for value in [_text(_fact(signal.normalized_attributes, DiagnosticFactKey.AGENT_VERSION))]
        if value is not None
    }
    if versions:
        candidates = [candidate for candidate in candidates if candidate.version in versions]
    return candidates[0] if len(candidates) == 1 else None


def _infer_skill(
    signals: Sequence[OperationalSignal],
    inventory: AgentSystemInventorySnapshot,
    agent: ExactComponentReference,
) -> ExactComponentReference | None:
    candidates = [
        edge.skill_ref
        for edge in inventory.agent_to_skill_edges
        if edge.agent_ref.identity == agent.identity
    ]
    ids = {
        value
        for signal in signals
        for value in [_text(_fact(signal.normalized_attributes, DiagnosticFactKey.SKILL_ID))]
        if value is not None
    }
    if ids:
        candidates = [candidate for candidate in candidates if candidate.component_id in ids]
    return candidates[0] if len(candidates) == 1 else None


def _infer_tool(
    signals: Sequence[OperationalSignal], inventory: AgentSystemInventorySnapshot
) -> ExactComponentReference | None:
    tool_id = _tool_id(signals)
    tool_version = _tool_version(signals)
    candidates = list(inventory.tool_refs)
    if tool_id is not None:
        candidates = [candidate for candidate in candidates if candidate.component_id == tool_id]
    if tool_version is not None:
        candidates = [candidate for candidate in candidates if candidate.version == tool_version]
    return candidates[0] if len(candidates) == 1 else None


def _parse_tool_ref(signals: Sequence[OperationalSignal]) -> ExactComponentReference | None:
    raw = next(
        (
            _text(_fact(signal.normalized_attributes, DiagnosticFactKey.TOOL_REF))
            for signal in signals
            if _text(_fact(signal.normalized_attributes, DiagnosticFactKey.TOOL_REF)) is not None
        ),
        None,
    )
    if raw is None:
        return None
    value = raw.removeprefix("TOOL:")
    if "@" not in value:
        return None
    component_id, version = value.rsplit("@", 1)
    try:
        return ExactComponentReference(
            component_type=ComponentType.TOOL,
            component_id=component_id,
            version=version,
            source_system="harness",
        )
    except ValueError:
        return None


def _tool_gap(
    signals: Sequence[OperationalSignal],
    inventory: AgentSystemInventorySnapshot,
    *,
    required_tool_ref: ExactComponentReference | None,
    target_agent_ref: ExactComponentReference,
) -> bool:
    tool_id = required_tool_ref.component_id if required_tool_ref else _tool_id(signals)
    if tool_id is None:
        return False
    matching_tools = [ref for ref in inventory.tool_refs if ref.component_id == tool_id]
    if required_tool_ref is not None:
        matching_tools = [
            ref for ref in matching_tools if ref.identity == required_tool_ref.identity
        ]
    if not matching_tools:
        return True
    authority = {
        edge.tool_ref.identity
        for edge in inventory.agent_to_tool_authority_edges
        if edge.agent_ref.identity == target_agent_ref.identity
    }
    if required_tool_ref is not None:
        return required_tool_ref.identity not in authority
    return not bool(authority & {ref.identity for ref in matching_tools})


def _available(
    inventory: AgentSystemInventorySnapshot,
    reference: ExactComponentReference,
) -> bool:
    groups = {
        ComponentType.AGENT: inventory.agent_refs,
        ComponentType.PROMPT: inventory.prompt_refs,
        ComponentType.SKILL: inventory.skill_refs,
        ComponentType.TOOL: inventory.tool_refs,
        ComponentType.POLICY: inventory.policy_refs,
    }
    if reference.identity not in {item.identity for item in groups[reference.component_type]}:
        return False
    lifecycle = inventory.component_lifecycles.get(reference.identity, "ACTIVE")
    return lifecycle in _USABLE_LIFECYCLES


def _quality_issue(signals: Sequence[OperationalSignal]) -> str | None:
    if not signals:
        return "No source signals were supplied for diagnosis."
    if any(signal.evidence_quality in _UNRELIABLE_QUALITY for signal in signals):
        return "Source evidence is missing, incomplete, stale, or conflicting."
    return None


def _quality_confidence(signals: Sequence[OperationalSignal]) -> float:
    if not signals:
        return 0.0
    if any(
        signal.evidence_quality in {EvidenceQuality.CONFLICTING, EvidenceQuality.UNAVAILABLE}
        for signal in signals
    ):
        return 0.0
    return 0.25


def _has_business_dependency_blocker(signals: Sequence[OperationalSignal]) -> bool:
    for signal in signals:
        attrs = signal.normalized_attributes
        event_type = _token(_text(_fact(attrs, DiagnosticFactKey.EVENT_TYPE)) or "")
        if event_type in _BUSINESS_BLOCKER_EVENT_TYPES:
            return True
        for key in (
            DiagnosticFactKey.BUSINESS_SERVICE_AVAILABLE,
            DiagnosticFactKey.EXTERNAL_SERVICE_AVAILABLE,
            DiagnosticFactKey.BUSINESS_DEPENDENCY_AVAILABLE,
        ):
            value = _boolean(_fact(attrs, key))
            if value is False:
                return True
        if any(
            _boolean(_fact(attrs, key)) is True
            for key in (
                DiagnosticFactKey.BUSINESS_DEPENDENCY_BLOCKED,
                DiagnosticFactKey.EXTERNAL_DEPENDENCY_BLOCKED,
            )
        ):
            return True
        status = _token(
            _text(
                _fact(attrs, DiagnosticFactKey.BUSINESS_SERVICE_STATUS)
                or _fact(attrs, DiagnosticFactKey.EXTERNAL_DEPENDENCY_STATUS)
                or _fact(attrs, DiagnosticFactKey.DEPENDENCY_STATUS)
            )
            or ""
        )
        if status in _UNAVAILABLE_TOKENS:
            return True
    return False


def _has_policy_constraint(signals: Sequence[OperationalSignal]) -> bool:
    for signal in signals:
        attrs = signal.normalized_attributes
        event_type = _token(_text(_fact(attrs, DiagnosticFactKey.EVENT_TYPE)) or "")
        if event_type in _POLICY_BLOCKER_EVENT_TYPES:
            return True
        if any(
            _boolean(_fact(attrs, key)) is True
            for key in (
                DiagnosticFactKey.POLICY_DENIED,
                DiagnosticFactKey.POLICY_CONSTRAINT,
                DiagnosticFactKey.POLICY_BLOCKED,
            )
        ):
            return True
        reason = _token(
            _text(
                _fact(attrs, DiagnosticFactKey.PERMISSION_REASON_CODE)
                or _fact(attrs, DiagnosticFactKey.POLICY_ID)
            )
            or ""
        )
        result = _token(_text(_fact(attrs, DiagnosticFactKey.RESULT_STATUS)) or "")
        if reason in {"policy_denied", "permission_denied", "denied", "forbidden"}:
            return True
        if result in {"policy_denied", "permission_denied", "denied", "forbidden"}:
            return True
    return False


def _has_approval_friction(signals: Sequence[OperationalSignal]) -> bool:
    for signal in signals:
        attrs = signal.normalized_attributes
        event_type = _token(_text(_fact(attrs, DiagnosticFactKey.EVENT_TYPE)) or "")
        if event_type in _APPROVAL_FRICTION_EVENT_TYPES:
            return True
        if _boolean(_fact(attrs, DiagnosticFactKey.APPROVAL_FRICTION)) is True:
            return True
        if _boolean(_fact(attrs, DiagnosticFactKey.APPROVAL_WAIT)) is True:
            return True
        status = _token(
            _text(
                _fact(attrs, DiagnosticFactKey.APPROVAL_STATUS)
                or _fact(attrs, DiagnosticFactKey.STATUS)
            )
            or ""
        )
        result = _token(_text(_fact(attrs, DiagnosticFactKey.APPROVAL_RESULT)) or "")
        waiting = status in {"pending", "waiting", "waiting_approval", "queued"} or result in {
            "pending",
            "waiting",
            "waiting_approval",
        }
        if DiagnosticFactKey.APPROVAL_STATUS.value in attrs and waiting:
            return True
        if _boolean(_fact(attrs, DiagnosticFactKey.APPROVAL_REQUIRED)) is True and waiting:
            return True
        if signal.source_record_type == "approval" and waiting:
            return True
    return False


def _has_knowledge_source_issue(signals: Sequence[OperationalSignal]) -> bool:
    for signal in signals:
        attrs = signal.normalized_attributes
        event_type = _token(_text(_fact(attrs, DiagnosticFactKey.EVENT_TYPE)) or "")
        if event_type in _KNOWLEDGE_ISSUE_EVENT_TYPES:
            return True
        if any(
            _boolean(_fact(attrs, key)) is True
            for key in (DiagnosticFactKey.KNOWLEDGE_SOURCE_ISSUE, DiagnosticFactKey.KNOWLEDGE_ISSUE)
        ):
            return True
        if _boolean(_fact(attrs, DiagnosticFactKey.KNOWLEDGE_SOURCE_AVAILABLE)) is False:
            return True
        status = _token(
            _text(
                _fact(attrs, DiagnosticFactKey.KNOWLEDGE_SOURCE_STATUS)
                or _fact(attrs, DiagnosticFactKey.RETRIEVAL_STATUS)
            )
            or ""
        )
        if status in _KNOWLEDGE_ISSUE_TOKENS:
            return True
    return False


def _explicit_agent_gap(signals: Sequence[OperationalSignal]) -> bool:
    return any(
        _boolean(_fact(signal.normalized_attributes, key)) is True
        for signal in signals
        for key in (
            DiagnosticFactKey.AGENT_GAP,
            DiagnosticFactKey.AGENT_COMPOSITION_MISSING,
            DiagnosticFactKey.REQUIRED_AGENT_MISSING,
        )
    ) or any(
        _token(_text(_fact(signal.normalized_attributes, DiagnosticFactKey.EVENT_TYPE)) or "")
        in _AGENT_GAP_EVENT_TYPES
        for signal in signals
    )


def _explicit_skill_gap(signals: Sequence[OperationalSignal]) -> bool:
    return any(
        _boolean(_fact(signal.normalized_attributes, key)) is True
        for signal in signals
        for key in (
            DiagnosticFactKey.SKILL_GAP,
            DiagnosticFactKey.REQUIRED_SKILL_MISSING,
            DiagnosticFactKey.SKILL_INSUFFICIENT,
        )
    ) or any(
        _token(_text(_fact(signal.normalized_attributes, DiagnosticFactKey.EVENT_TYPE)) or "")
        in _SKILL_GAP_EVENT_TYPES
        for signal in signals
    )


def _explicit_prompt_gap(signals: Sequence[OperationalSignal]) -> bool:
    return any(
        _boolean(_fact(signal.normalized_attributes, key)) is True
        for signal in signals
        for key in (
            DiagnosticFactKey.PROMPT_GAP,
            DiagnosticFactKey.PROMPT_FAILURE,
            DiagnosticFactKey.BEHAVIORAL_INSTRUCTION_FAILURE,
        )
    ) or any(
        _token(_text(_fact(signal.normalized_attributes, DiagnosticFactKey.EVENT_TYPE)) or "")
        in _PROMPT_GAP_EVENT_TYPES
        for signal in signals
    )


def _affected_refs(
    diagnosis_type: DiagnosisType,
    requirements: _Requirements,
    inventory: AgentSystemInventorySnapshot | None,
    signals: Sequence[OperationalSignal],
) -> dict[str, tuple[ExactComponentReference, ...]]:
    values: dict[str, tuple[ExactComponentReference, ...]] = {
        "agent": (),
        "prompt": (),
        "skill": (),
        "tool": (),
        "policy": (),
    }
    if requirements.target_agent_ref is not None:
        values["agent"] = (requirements.target_agent_ref,)
    if diagnosis_type is DiagnosisType.PROMPT_GAP and requirements.required_prompt_ref is not None:
        values["prompt"] = (requirements.required_prompt_ref,)
    if diagnosis_type is DiagnosisType.SKILL_GAP and requirements.required_skill_ref is not None:
        values["skill"] = (requirements.required_skill_ref,)
    if diagnosis_type is DiagnosisType.TOOL_GAP and requirements.required_tool_ref is not None:
        values["tool"] = (requirements.required_tool_ref,)
    if diagnosis_type is DiagnosisType.POLICY_CONSTRAINT and inventory is not None:
        policy_ids = {
            _text(_fact(signal.normalized_attributes, DiagnosticFactKey.POLICY_ID))
            for signal in signals
            if _text(_fact(signal.normalized_attributes, DiagnosticFactKey.POLICY_ID)) is not None
        }
        values["policy"] = tuple(
            sorted(
                (ref for ref in inventory.policy_refs if ref.component_id in policy_ids),
                key=lambda ref: ref.identity,
            )
        )
    return values


def _evidence_partition(
    cluster: OpportunityCluster,
    signals: Sequence[OperationalSignal],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    all_refs = {ref for signal in signals for ref in signal.evidence_refs} | set(
        cluster.evidence_refs
    )
    conflicting = {
        ref
        for signal in signals
        if signal.evidence_quality in _UNRELIABLE_QUALITY
        for ref in signal.evidence_refs
    }
    supporting = all_refs - conflicting
    if not supporting:
        supporting = {
            signal.source_reference
            for signal in signals
            if signal.source_reference not in conflicting
        }
    if not supporting and conflicting:
        first = sorted(conflicting)[0]
        supporting = {first}
        conflicting.remove(first)
    if not supporting:
        raise DiagnosisError("diagnosis requires at least one evidence reference")
    return tuple(sorted(supporting)), tuple(sorted(conflicting))


def _validate_scope(
    cluster: OpportunityCluster,
    signals: Sequence[OperationalSignal],
    inventory: AgentSystemInventorySnapshot | None,
) -> None:
    if any(signal.tenant_id != cluster.tenant_id for signal in signals):
        raise DiagnosisError("all diagnosis signals must belong to the cluster tenant")
    if inventory is not None and inventory.tenant_id != cluster.tenant_id:
        raise DiagnosisError("inventory and cluster must belong to the same tenant")
    signal_ids = tuple(signal.signal_id for signal in signals)
    if len(signal_ids) != len(set(signal_ids)):
        raise DiagnosisError("diagnosis evidence must not contain duplicate signal IDs")
    if set(signal_ids) != set(cluster.source_signal_ids):
        raise DiagnosisError("diagnosis signals must exactly match cluster source lineage")
    declared_evidence_refs = set(cluster.evidence_refs)
    if any(not set(signal.evidence_refs).issubset(declared_evidence_refs) for signal in signals):
        raise DiagnosisError("diagnosis signal evidence is not declared by cluster lineage")


def _deduplicate_signals(
    signals: Iterable[OperationalSignal],
) -> tuple[OperationalSignal, ...]:
    by_identity: dict[tuple[str, str, str, str, str | None], OperationalSignal] = {}
    for signal in signals:
        key = (signal.tenant_id, *signal.source_identity)
        previous = by_identity.get(key)
        if previous is not None and previous != signal:
            raise DiagnosisError(f"source identity has conflicting signal content: {key!r}")
        by_identity[key] = signal
    return tuple(
        sorted(by_identity.values(), key=lambda signal: (signal.occurred_at, signal.signal_id))
    )


def _tool_id(signals: Sequence[OperationalSignal]) -> str | None:
    for signal in signals:
        for key in (
            DiagnosticFactKey.TOOL_ID,
            DiagnosticFactKey.BUSINESS_OPERATION,
            DiagnosticFactKey.LOOKUP_TYPE,
            DiagnosticFactKey.OPERATION,
        ):
            value = _text(_fact(signal.normalized_attributes, key))
            if value is not None:
                return value
    return None


def _tool_version(signals: Sequence[OperationalSignal]) -> str | None:
    for signal in signals:
        value = _text(_fact(signal.normalized_attributes, DiagnosticFactKey.TOOL_VERSION))
        if value is not None:
            return value
    return None


def _boolean(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "yes", "1"}:
            return True
        if token in {"false", "no", "0"}:
            return False
    return None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")


def _fact(attributes: Mapping[str, object], key: DiagnosticFactKey) -> object | None:
    return attributes.get(key.value)


__all__ = [
    "DiagnosisError",
    "DiagnosisFallback",
    "DiagnosisUndeterminedError",
    "OperationalDiagnoser",
]
