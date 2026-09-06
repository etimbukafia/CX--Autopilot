"""Rules for turning evaluated evidence into a human-gated pilot proposal."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast

from .contracts import (
    AgentSystemInventorySnapshot,
    CandidateReference,
    ChangeProposal,
    EvaluationReference,
    Opportunity,
    OpportunityCluster,
    PilotRecommendation,
    ProblemDiagnosis,
)
from .contracts.common import aware_timestamp, non_blank, unique_values
from .graph import GraphValidationError, validate_candidate_graph
from .storage.ports import PilotRecommendationStore


class RecommendationError(ValueError):
    """Raised when the evidence chain is not sufficient for a pilot."""


class PilotRecommender:
    """Combine Autopilot provenance and Lab evidence without deploying."""

    def __init__(self, recommendation_store: PilotRecommendationStore | None = None) -> None:
        self.recommendation_store = recommendation_store

    def recommend(
        self,
        *,
        proposal: ChangeProposal,
        diagnosis: ProblemDiagnosis,
        inventory: AgentSystemInventorySnapshot,
        candidate: CandidateReference | object,
        evaluation: EvaluationReference | object,
        comparison: object,
        summary: str,
        expected_operational_impact: str,
        known_risks: Iterable[str],
        pilot_scope: Mapping[str, Any],
        success_criteria: Iterable[str],
        rollback_conditions: Iterable[str],
        opportunity: Opportunity | None = None,
        cluster: OpportunityCluster | None = None,
        operational_evidence_refs: Iterable[str] = (),
        risk_evidence_refs: Iterable[str] = (),
        created_at: datetime | None = None,
        recommendation_store: PilotRecommendationStore | None = None,
    ) -> PilotRecommendation:
        """Return a reviewable recommendation when every required link holds."""

        if not isinstance(proposal, ChangeProposal):
            raise RecommendationError("recommendation requires a ChangeProposal")
        if not isinstance(diagnosis, ProblemDiagnosis):
            raise RecommendationError("recommendation requires a ProblemDiagnosis")
        if not isinstance(inventory, AgentSystemInventorySnapshot):
            raise RecommendationError("recommendation requires an inventory snapshot")
        if proposal.tenant_id != diagnosis.tenant_id or proposal.tenant_id != inventory.tenant_id:
            raise RecommendationError("proposal, diagnosis, and inventory must share a tenant")
        if proposal.diagnosis_id != diagnosis.diagnosis_id:
            raise RecommendationError("proposal does not refer to the supplied diagnosis")
        if proposal.baseline_inventory_snapshot_id != inventory.snapshot_id:
            raise RecommendationError("proposal does not use the supplied baseline inventory")
        candidate_reference = _candidate_reference(candidate)
        evaluation_reference = _evaluation_reference(evaluation)
        if candidate_reference.tenant_id != proposal.tenant_id:
            raise RecommendationError("candidate reference tenant does not match proposal")
        if evaluation_reference.tenant_id != proposal.tenant_id:
            raise RecommendationError("evaluation reference tenant does not match proposal")
        if evaluation_reference.candidate_id != candidate_reference.candidate_id:
            raise RecommendationError("evaluation does not refer to the supplied candidate")
        if evaluation_reference.status != "EVALUATION_SUCCEEDED":
            raise RecommendationError("a successful Lab evaluation is required")
        if evaluation_reference.comparison_id is None:
            raise RecommendationError("evaluation must contain a Lab comparison reference")
        comparison_record = _comparison_record(comparison)
        comparison_id = _optional_text(comparison_record, "comparison_id")
        if comparison_id != evaluation_reference.comparison_id:
            raise RecommendationError("comparison does not match the evaluation reference")
        if _comparison_verdict(comparison_record) != "improved":
            raise RecommendationError("an improved Lab comparison is required for a pilot")
        try:
            validate_candidate_graph(proposal, inventory, candidate_reference)
        except GraphValidationError as exc:
            raise RecommendationError(str(exc)) from exc
        if (
            evaluation_reference.proposal_id != candidate_reference.proposal_id
            or evaluation_reference.baseline_inventory_snapshot_id
            != candidate_reference.baseline_inventory_snapshot_id
            or evaluation_reference.resolved_graph_digest
            != candidate_reference.resolved_graph_digest
        ):
            raise RecommendationError("evaluation does not preserve candidate graph binding")

        source_evidence = _operational_evidence(
            proposal,
            diagnosis,
            opportunity=opportunity,
            cluster=cluster,
            explicit=operational_evidence_refs,
        )
        risks = _text_values(known_risks, "known_risks", required=True)
        risk_evidence = _references(risk_evidence_refs, "risk_evidence_refs", required=True)
        if not source_evidence:
            raise RecommendationError(
                "an opportunity, cluster, or explicit operational evidence is required"
            )
        impact = non_blank(expected_operational_impact, "expected_operational_impact")
        summary_text = non_blank(summary, "summary")
        scope = _validate_scope(pilot_scope, candidate_reference)
        success = _text_values(success_criteria, "success_criteria", required=True)
        rollback = _text_values(rollback_conditions, "rollback_conditions", required=True)
        recommendation_time = aware_timestamp(created_at or datetime.now(UTC), "created_at")

        evidence = _append_evidence(
            *source_evidence,
            *risk_evidence,
            *proposal.evidence_refs,
            *diagnosis.supporting_evidence_refs,
            f"proposal:{proposal.proposal_id}",
            f"diagnosis:{diagnosis.diagnosis_id}",
            f"inventory:{inventory.snapshot_id}",
            f"candidate:{candidate_reference.candidate_id}",
            f"harness:manifest:{candidate_reference.manifest_id}",
            f"harness:digest:{candidate_reference.manifest_digest}",
            f"harness:registry:{candidate_reference.registry_snapshot_id}",
            f"evaluation:{evaluation_reference.evaluation_id}",
            *evaluation_reference.evidence_refs,
            f"comparison:{comparison_id}",
        )
        recommendation = PilotRecommendation(
            recommendation_id=_stable_id(
                "recommendation",
                {
                    "tenant_id": proposal.tenant_id,
                    "proposal_id": proposal.proposal_id,
                    "candidate_id": candidate_reference.candidate_id,
                    "evaluation_id": evaluation_reference.evaluation_id,
                    "comparison_id": comparison_id,
                    "pilot_scope": dict(scope),
                },
            ),
            tenant_id=proposal.tenant_id,
            proposal_id=proposal.proposal_id,
            candidate_reference=candidate_reference,
            evaluation_reference=evaluation_reference,
            summary=summary_text,
            expected_operational_impact=impact,
            known_risks=risks,
            pilot_scope=dict(scope),
            success_criteria=success,
            rollback_conditions=rollback,
            evidence_refs=evidence,
            requires_human_approval=True,
            status="READY_FOR_HUMAN_APPROVAL",
            created_at=recommendation_time,
        )
        store = recommendation_store or self.recommendation_store
        if store is not None:
            try:
                return store.insert(recommendation)
            except Exception as exc:
                raise RecommendationError("pilot recommendation could not be stored") from exc
        return recommendation


def _candidate_reference(value: CandidateReference | object) -> CandidateReference:
    if isinstance(value, CandidateReference):
        return value
    nested = getattr(value, "candidate_reference", None)
    if isinstance(nested, CandidateReference):
        return nested
    raise RecommendationError("candidate must expose a CandidateReference")


def _evaluation_reference(value: EvaluationReference | object) -> EvaluationReference:
    if isinstance(value, EvaluationReference):
        return value
    nested = getattr(value, "evaluation_reference", None)
    if isinstance(nested, EvaluationReference):
        return nested
    raise RecommendationError("evaluation must expose an EvaluationReference")


def _operational_evidence(
    proposal: ChangeProposal,
    diagnosis: ProblemDiagnosis,
    *,
    opportunity: Opportunity | None,
    cluster: OpportunityCluster | None,
    explicit: Iterable[str],
) -> tuple[str, ...]:
    result = list(_references(explicit, "operational_evidence_refs"))
    if opportunity is not None:
        if proposal.opportunity_id != opportunity.opportunity_id:
            raise RecommendationError("opportunity does not match the proposal")
        if opportunity.tenant_id != proposal.tenant_id:
            raise RecommendationError("opportunity tenant does not match proposal")
        result.extend(opportunity.evidence_refs)
        result.append(f"opportunity:{opportunity.opportunity_id}")
    if cluster is not None:
        if proposal.cluster_id != cluster.cluster_id or diagnosis.cluster_id != cluster.cluster_id:
            raise RecommendationError("cluster does not match the proposal and diagnosis")
        if cluster.tenant_id != proposal.tenant_id:
            raise RecommendationError("cluster tenant does not match proposal")
        result.extend(cluster.evidence_refs)
        result.append(f"cluster:{cluster.cluster_id}")
    return _append_evidence(*result)


def _validate_scope(value: Mapping[str, Any], candidate: CandidateReference) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise RecommendationError("pilot_scope must be a non-empty mapping")
    scope = dict(value)
    agent_values = [
        scope.get(name)
        for name in ("agent_ref", "candidate_agent_ref", "agent_identity")
        if scope.get(name) is not None
    ]
    if not agent_values:
        raise RecommendationError("pilot_scope must include the exact candidate Agent identity")
    if any(_identity(value) != candidate.agent_ref.identity for value in agent_values):
        raise RecommendationError("pilot_scope Agent identity does not match the candidate")
    for name in ("tenant_id", "tenant_ref"):
        if name in scope and scope[name] != candidate.tenant_id:
            raise RecommendationError("pilot_scope tenant does not match the candidate")

    bounded = False
    for name, maximum in (
        ("traffic_percentage", 100.0),
        ("traffic_fraction", 1.0),
    ):
        if name not in scope:
            continue
        raw = scope[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise RecommendationError(f"pilot_scope {name} must be numeric")
        if not math.isfinite(float(raw)) or not 0.0 < float(raw) <= maximum:
            raise RecommendationError(f"pilot_scope {name} must be finite and positive")
        bounded = True
    for name in ("case_limit", "max_cases", "interaction_limit"):
        if name not in scope:
            continue
        raw = scope[name]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise RecommendationError(f"pilot_scope {name} must be a positive integer")
        bounded = True
    for name in ("duration_seconds", "max_duration_seconds", "time_limit_seconds"):
        if name not in scope:
            continue
        raw = scope[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise RecommendationError(f"pilot_scope {name} must be numeric")
        if not math.isfinite(float(raw)) or float(raw) <= 0.0:
            raise RecommendationError(f"pilot_scope {name} must be finite and positive")
        bounded = True
    if not bounded:
        raise RecommendationError(
            "pilot_scope must contain a finite traffic, case, interaction, or time bound"
        )
    return scope


def _identity(value: object) -> str | None:
    if isinstance(value, str):
        return value
    raw = getattr(value, "identity", None)
    if isinstance(raw, str):
        return raw
    if isinstance(value, Mapping):
        component_type = value.get("component_type", value.get("type", "AGENT"))
        component_id = value.get("component_id", value.get("agent_id"))
        version = value.get("version", value.get("agent_version"))
        if component_id is not None and version is not None:
            prefix = (
                component_type.value if isinstance(component_type, Enum) else str(component_type)
            )
            return f"{prefix.upper()}:{component_id}@{version}"
    return None


def _comparison_verdict(value: object) -> str | None:
    raw = _field(value, "verdict")
    if isinstance(raw, Enum):
        raw = raw.value
    return raw.lower() if isinstance(raw, str) else None


def _comparison_record(value: object) -> object:
    nested = _field(value, "comparison", None)
    return value if _field(value, "verdict", None) is not None else (nested or value)


def _text_values(values: Iterable[str], field_name: str, *, required: bool) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    result = tuple(non_blank(value, field_name) for value in values)
    unique_values(result, field_name)
    if required and not result:
        raise RecommendationError(f"{field_name} must not be empty")
    return result


def _references(
    values: Iterable[str], field_name: str, *, required: bool = False
) -> tuple[str, ...]:
    result = _text_values(values, field_name, required=required)
    return result


def _append_evidence(*values: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _optional_text(value: object | None, name: str) -> str | None:
    raw = _field(value, name)
    if isinstance(raw, Enum):
        raw = raw.value
    return raw if isinstance(raw, str) and raw.strip() else None


def _field(value: object | None, name: str, default: object | None = None) -> object | None:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return cast(object | None, value.get(name, default))
    return cast(object | None, getattr(value, name, default))


def _stable_id(prefix: str, value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


__all__ = ["PilotRecommender", "RecommendationError"]
