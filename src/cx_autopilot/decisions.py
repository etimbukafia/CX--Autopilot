"""Human decision recording and source-to-decision audit retrieval."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from .contracts import (
    AgentSystemInventorySnapshot,
    CandidateReference,
    ChangeProposal,
    DecisionRecord,
    DecisionSubjectType,
    EvaluationReference,
    OperationalDisposition,
    Opportunity,
    OpportunityCluster,
    PilotRecommendation,
    ProblemDiagnosis,
)
from .contracts.common import aware_timestamp, non_blank, unique_values
from .storage.ports import DecisionRecordStore


class DecisionError(ValueError):
    """Raised when a decision cannot be recorded or audited safely."""


PILOT_DECISIONS = frozenset({"APPROVE", "REJECT", "REQUEST_CHANGE", "CLOSE"})
DISPOSITION_DECISIONS = frozenset({"ACCEPT", "REJECT", "CLOSE"})


@dataclass(frozen=True)
class DecisionAuditTrail:
    """Read view of the immutable chain that led to one decision."""

    decision: DecisionRecord
    recommendation: PilotRecommendation | None
    disposition: OperationalDisposition | None
    proposal: ChangeProposal | None
    diagnosis: ProblemDiagnosis
    inventory: AgentSystemInventorySnapshot | None
    opportunity: Opportunity | None
    cluster: OpportunityCluster | None
    candidate: CandidateReference | None
    evaluation: EvaluationReference | None
    evidence_refs: tuple[str, ...]

    @property
    def decision_record(self) -> DecisionRecord:
        """Return the terminal decision record."""

        return self.decision

    @property
    def pilot_recommendation(self) -> PilotRecommendation | None:
        """Return the linked pilot recommendation, when present."""

        return self.recommendation

    @property
    def operational_disposition(self) -> OperationalDisposition | None:
        """Return the linked no-change disposition, when present."""

        return self.disposition

    @property
    def candidate_reference(self) -> CandidateReference | None:
        """Return the candidate provenance in the chain, when present."""

        return self.candidate

    @property
    def evaluation_reference(self) -> EvaluationReference | None:
        """Return the Lab evidence reference in the chain, when present."""

        return self.evaluation


class DecisionService:
    """Persist human decisions without any deployment or promotion side effect."""

    def __init__(
        self,
        store: object | None = None,
        *,
        decision_store: DecisionRecordStore | None = None,
        recommendation_store: object | None = None,
        disposition_store: object | None = None,
        proposal_store: object | None = None,
        diagnosis_store: object | None = None,
        inventory_store: object | None = None,
        opportunity_store: object | None = None,
        cluster_store: object | None = None,
        candidate_store: object | None = None,
        evaluation_store: object | None = None,
    ) -> None:
        self.decisions = decision_store or _store_attr(store, "decisions", "decision_records")
        self.recommendations = recommendation_store or _store_attr(
            store, "recommendations", "pilot_recommendations"
        )
        self.dispositions = disposition_store or _store_attr(
            store, "dispositions", "operational_dispositions"
        )
        self.proposals = proposal_store or _store_attr(store, "proposals", "change_proposals")
        self.diagnoses = diagnosis_store or _store_attr(store, "diagnoses", "problem_diagnoses")
        self.inventory = inventory_store or _store_attr(store, "inventory", "inventory_snapshots")
        self.opportunities = opportunity_store or _store_attr(store, "opportunities")
        self.clusters = cluster_store or _store_attr(store, "opportunity_clusters", "clusters")
        self.candidates = candidate_store or _store_attr(
            store, "candidates", "candidate_references"
        )
        self.evaluations = evaluation_store or _store_attr(
            store, "evaluations", "evaluation_references"
        )
        if self.decisions is None:
            raise DecisionError("a DecisionRecord store is required")

    def record_pilot_decision(
        self,
        recommendation: PilotRecommendation,
        decision: str,
        actor_ref: str,
        reason: str,
        *,
        evidence_refs: Iterable[str] = (),
        occurred_at: datetime | None = None,
    ) -> DecisionRecord:
        """Record an approve, reject, request-change, or close decision."""

        if not isinstance(recommendation, PilotRecommendation):
            raise DecisionError("pilot decisions require a PilotRecommendation")
        canonical = _canonical_decision(decision, PILOT_DECISIONS, "pilot")
        return self._record(
            subject_type=DecisionSubjectType.PILOT_RECOMMENDATION,
            subject_id=recommendation.recommendation_id,
            tenant_id=recommendation.tenant_id,
            decision=canonical,
            actor_ref=actor_ref,
            reason=reason,
            evidence_refs=_append_evidence(
                *recommendation.evidence_refs,
                f"recommendation:{recommendation.recommendation_id}",
                *_references(evidence_refs, "evidence_refs"),
            ),
            occurred_at=occurred_at,
        )

    def record_disposition_decision(
        self,
        disposition: OperationalDisposition,
        decision: str,
        actor_ref: str,
        reason: str,
        *,
        evidence_refs: Iterable[str] = (),
        occurred_at: datetime | None = None,
    ) -> DecisionRecord:
        """Record an accept, reject, or close decision for no-change work."""

        if not isinstance(disposition, OperationalDisposition):
            raise DecisionError("disposition decisions require an OperationalDisposition")
        canonical = _canonical_decision(decision, DISPOSITION_DECISIONS, "disposition")
        return self._record(
            subject_type=DecisionSubjectType.OPERATIONAL_DISPOSITION,
            subject_id=disposition.disposition_id,
            tenant_id=disposition.tenant_id,
            decision=canonical,
            actor_ref=actor_ref,
            reason=reason,
            evidence_refs=_append_evidence(
                *disposition.evidence_refs,
                f"disposition:{disposition.disposition_id}",
                *_references(evidence_refs, "evidence_refs"),
            ),
            occurred_at=occurred_at,
        )

    def _record(
        self,
        *,
        subject_type: DecisionSubjectType,
        subject_id: str,
        tenant_id: str,
        decision: str,
        actor_ref: str,
        reason: str,
        evidence_refs: tuple[str, ...],
        occurred_at: datetime | None,
    ) -> DecisionRecord:
        actor = non_blank(actor_ref, "actor_ref")
        decision_reason = non_blank(reason, "reason")
        timestamp = aware_timestamp(occurred_at or datetime.now(UTC), "occurred_at")
        record = DecisionRecord(
            decision_id=_stable_id(
                "decision",
                {
                    "tenant_id": tenant_id,
                    "subject_type": subject_type.value,
                    "subject_id": subject_id,
                    "decision": decision,
                    "actor_ref": actor,
                    "occurred_at": timestamp.isoformat(),
                },
            ),
            tenant_id=tenant_id,
            subject_type=subject_type,
            subject_id=subject_id,
            decision=decision,
            actor_ref=actor,
            occurred_at=timestamp,
            reason=decision_reason,
            evidence_refs=evidence_refs,
        )
        try:
            return cast(DecisionRecordStore, self.decisions).insert(record)
        except Exception as exc:
            raise DecisionError("decision record could not be stored") from exc

    def audit(self, decision_id: str, *, tenant_id: str) -> DecisionAuditTrail:
        """Return the source-to-decision chain inside one tenant scope."""

        decision_key = non_blank(decision_id, "decision_id")
        tenant = non_blank(tenant_id, "tenant_id")
        decision = _get(self.decisions, decision_key, tenant, "decision")
        if decision is None:
            raise DecisionError("decision was not found in the requested tenant")
        if decision.subject_type is DecisionSubjectType.PILOT_RECOMMENDATION:
            recommendation = _get(
                self.recommendations,
                decision.subject_id,
                tenant,
                "pilot recommendation",
            )
            if recommendation is None:
                raise DecisionError("pilot recommendation is missing from the decision chain")
            proposal = _get(self.proposals, recommendation.proposal_id, tenant, "change proposal")
            if proposal is None:
                raise DecisionError("change proposal is missing from the decision chain")
            diagnosis, inventory = self._diagnosis_and_inventory(proposal.diagnosis_id, tenant)
            opportunity, cluster = self._source_records(proposal, diagnosis, tenant)
            evidence = _chain_evidence(
                decision,
                recommendation=recommendation,
                proposal=proposal,
                diagnosis=diagnosis,
                inventory=inventory,
                opportunity=opportunity,
                cluster=cluster,
                candidate=recommendation.candidate_reference,
                evaluation=recommendation.evaluation_reference,
            )
            return DecisionAuditTrail(
                decision=decision,
                recommendation=recommendation,
                disposition=None,
                proposal=proposal,
                diagnosis=diagnosis,
                inventory=inventory,
                opportunity=opportunity,
                cluster=cluster,
                candidate=recommendation.candidate_reference,
                evaluation=recommendation.evaluation_reference,
                evidence_refs=evidence,
            )

        if decision.subject_type is DecisionSubjectType.OPERATIONAL_DISPOSITION:
            disposition = _get(
                self.dispositions,
                decision.subject_id,
                tenant,
                "operational disposition",
            )
            if disposition is None:
                raise DecisionError("operational disposition is missing from the decision chain")
            diagnosis, inventory = self._diagnosis_and_inventory(disposition.diagnosis_id, tenant)
            evidence = _chain_evidence(
                decision,
                disposition=disposition,
                diagnosis=diagnosis,
                inventory=inventory,
            )
            return DecisionAuditTrail(
                decision=decision,
                recommendation=None,
                disposition=disposition,
                proposal=None,
                diagnosis=diagnosis,
                inventory=inventory,
                opportunity=None,
                cluster=None,
                candidate=None,
                evaluation=None,
                evidence_refs=evidence,
            )
        raise DecisionError(f"unsupported decision subject: {decision.subject_type.value}")

    def audit_decision(self, decision_id: str, *, tenant_id: str) -> DecisionAuditTrail:
        """Explicitly named alias for the audit retrieval boundary."""

        return self.audit(decision_id, tenant_id=tenant_id)

    def _diagnosis_and_inventory(
        self, diagnosis_id: str, tenant_id: str
    ) -> tuple[ProblemDiagnosis, AgentSystemInventorySnapshot | None]:
        diagnosis = _get(self.diagnoses, diagnosis_id, tenant_id, "problem diagnosis")
        if diagnosis is None:
            raise DecisionError("problem diagnosis is missing from the decision chain")
        inventory = None
        if diagnosis.inventory_snapshot_id is not None:
            inventory = _get(
                self.inventory,
                diagnosis.inventory_snapshot_id,
                tenant_id,
                "inventory snapshot",
            )
            if inventory is None:
                raise DecisionError("inventory snapshot is missing from the decision chain")
        return diagnosis, inventory

    def _source_records(
        self,
        proposal: ChangeProposal,
        diagnosis: ProblemDiagnosis,
        tenant_id: str,
    ) -> tuple[Opportunity | None, OpportunityCluster | None]:
        opportunity = (
            _get(self.opportunities, proposal.opportunity_id, tenant_id, "opportunity")
            if proposal.opportunity_id is not None
            else None
        )
        cluster_id = proposal.cluster_id or diagnosis.cluster_id
        cluster = _get(self.clusters, cluster_id, tenant_id, "opportunity cluster")
        if (
            proposal.opportunity_id is not None
            and opportunity is None
            and self.opportunities is not None
        ):
            raise DecisionError("opportunity is missing from the decision chain")
        if cluster is None and self.clusters is not None:
            raise DecisionError("opportunity cluster is missing from the decision chain")
        return opportunity, cluster


def _store_attr(store: object | None, *names: str) -> object | None:
    if store is None:
        return None
    for name in names:
        value = getattr(store, name, None)
        if value is not None:
            return cast(object, value)
    return None


def _get(repository: object | None, record_id: str, tenant_id: str, label: str) -> Any:
    if repository is None:
        raise DecisionError(f"{label} store is required for audit")
    getter = getattr(repository, "get", None)
    if not callable(getter):
        raise DecisionError(f"{label} store must expose get")
    try:
        return getter(record_id, tenant_id=tenant_id)
    except Exception as exc:
        raise DecisionError(f"{label} could not be read") from exc


def _canonical_decision(value: str, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str):
        raise DecisionError(f"{label} decision must be text")
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "APPROVE_PILOT": "APPROVE",
        "REQUEST_CHANGES": "REQUEST_CHANGE",
        "REQUEST_CHANGE": "REQUEST_CHANGE",
        "ACCEPT_DISPOSITION": "ACCEPT",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in allowed:
        raise DecisionError(f"unsupported {label} decision: {value!r}")
    return normalized


def _references(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    result = tuple(non_blank(value, field_name) for value in values)
    unique_values(result, field_name)
    return result


def _append_evidence(*values: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _chain_evidence(
    decision: DecisionRecord,
    *,
    recommendation: PilotRecommendation | None = None,
    disposition: OperationalDisposition | None = None,
    proposal: ChangeProposal | None = None,
    diagnosis: ProblemDiagnosis,
    inventory: AgentSystemInventorySnapshot | None,
    opportunity: Opportunity | None = None,
    cluster: OpportunityCluster | None = None,
    candidate: CandidateReference | None = None,
    evaluation: EvaluationReference | None = None,
) -> tuple[str, ...]:
    values = list(decision.evidence_refs)
    if recommendation is not None:
        values.extend(recommendation.evidence_refs)
        values.append(f"recommendation:{recommendation.recommendation_id}")
    if disposition is not None:
        values.extend(disposition.evidence_refs)
        values.append(f"disposition:{disposition.disposition_id}")
    if proposal is not None:
        values.extend(proposal.evidence_refs)
        values.append(f"proposal:{proposal.proposal_id}")
    values.extend(diagnosis.supporting_evidence_refs)
    values.extend(diagnosis.conflicting_evidence_refs)
    values.append(f"diagnosis:{diagnosis.diagnosis_id}")
    if inventory is not None:
        values.append(f"inventory:{inventory.snapshot_id}")
        values.extend(f"harness:registry:{item}" for item in inventory.registry_snapshot_ids)
        values.extend(f"harness:manifest:{item}" for item in inventory.manifest_refs)
        values.extend(f"harness:digest:{digest}" for digest in inventory.manifest_digests.values())
    if opportunity is not None:
        values.extend(opportunity.evidence_refs)
        values.append(f"opportunity:{opportunity.opportunity_id}")
    if cluster is not None:
        values.extend(cluster.evidence_refs)
        values.append(f"cluster:{cluster.cluster_id}")
    if candidate is not None:
        values.extend(
            (
                f"candidate:{candidate.candidate_id}",
                f"harness:manifest:{candidate.manifest_id}",
                f"harness:digest:{candidate.manifest_digest}",
                f"harness:registry:{candidate.registry_snapshot_id}",
            )
        )
    if evaluation is not None:
        values.extend(evaluation.evidence_refs)
        values.append(f"evaluation:{evaluation.evaluation_id}")
        if evaluation.comparison_id is not None:
            values.append(f"comparison:{evaluation.comparison_id}")
        if evaluation.promotion_evidence_id is not None:
            values.append(f"promotion:{evaluation.promotion_evidence_id}")
    return _append_evidence(*values)


def _stable_id(prefix: str, value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


__all__ = [
    "DISPOSITION_DECISIONS",
    "DecisionAuditTrail",
    "DecisionError",
    "DecisionService",
    "PILOT_DECISIONS",
]
