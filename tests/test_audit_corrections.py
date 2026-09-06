from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from cx_autopilot.contracts import (
    ComponentType,
    DiagnosisType,
    EvaluationReference,
    EvidenceQuality,
    OperationalSignal,
    Opportunity,
    OpportunityCluster,
    OpportunityPattern,
)
from cx_autopilot.diagnosis import DiagnosisError, OperationalDiagnoser
from cx_autopilot.integrations import CXPlatformEvidenceAdapter
from cx_autopilot.recommendations import PilotRecommender, RecommendationError
from cx_autopilot.storage import SQLiteStore
from cx_autopilot.strategy import ChangePlanner
from test_phases_6_9 import NOW, cluster, diagnose, harness_ref, inventory_graph, ref, signal
from test_phases_10_13 import FakeHarnessFactory, _baseline_config, _build_tool_candidate


class _DiagnosticEventSource:
    def __init__(
        self,
        *,
        event_type: str,
        facts: dict[str, object] | None = None,
        evidence_quality: str | None = None,
    ) -> None:
        self.event = {
            "event_id": "diagnostic-event-1",
            "event_type": event_type,
            "occurred_at": NOW.isoformat(),
            **(facts or {}),
        }
        if evidence_quality is not None:
            self.event["evidence_quality"] = evidence_quality

    def list_events(self, *, after: str | None, limit: int) -> tuple[dict[str, object], ...]:
        del limit
        return (self.event,) if after is None else ()

    def list_tickets(self, *, after: str | None, limit: int) -> tuple[dict[str, object], ...]:
        del after, limit
        return ()

    def read_ticket(self, ticket_id: str) -> None:
        del ticket_id
        return None

    def read_conversation(self, conversation_id: str) -> None:
        del conversation_id
        return None

    def list_outcomes(self, *, after: str | None, limit: int) -> tuple[dict[str, object], ...]:
        del after, limit
        return ()

    def read_execution(self, execution_id: str) -> None:
        del execution_id
        return None


def _lookup_opportunity(
    source_signal: OperationalSignal, source_cluster: OpportunityCluster
) -> Opportunity:
    return Opportunity(
        opportunity_id=source_cluster.opportunity_ids[0],
        tenant_id=source_cluster.tenant_id,
        title="Repeated lookup",
        description="A lookup opportunity linked to one exact source signal.",
        source_signal_ids=(source_signal.signal_id,),
        evidence_refs=source_cluster.evidence_refs,
        frequency_estimate=1.0,
        confidence=source_cluster.confidence,
        status="DISCOVERED",
        created_at=NOW,
        pattern_type=OpportunityPattern.REPEATED_LOOKUP,
        pattern_key="operation:get_transaction_history",
        window_start=NOW,
        window_end=NOW,
        occurrence_keys=("source:diagnostic-event-1",),
    )


def _store_lookup_lineage(store: SQLiteStore) -> tuple[OpportunityCluster, OperationalSignal]:
    source_signal = signal(attributes={"tool_id": "get_transaction_history"})
    source_cluster = cluster()
    store.signals.insert(source_signal)
    store.opportunities.insert(_lookup_opportunity(source_signal, source_cluster))
    store.opportunity_clusters.insert(source_cluster)
    return source_cluster, source_signal


def _scoped_diagnoser(store: SQLiteStore) -> OperationalDiagnoser:
    return OperationalDiagnoser(
        cluster_store=store.opportunity_clusters,
        opportunity_store=store.opportunities,
        signal_store=store.signals,
    )


def test_cluster_diagnosis_ignores_unrelated_tenant_signals() -> None:
    inventory, _, _ = inventory_graph(
        required_component_refs=(ref(ComponentType.TOOL, "get_transaction_history"),),
    )
    agent = ref(ComponentType.AGENT, "support-agent")
    transaction_tool = ref(ComponentType.TOOL, "get_transaction_history")

    with SQLiteStore() as store:
        source_cluster, source_signal = _store_lookup_lineage(store)
        unrelated = signal(
            "unrelated-policy",
            attributes={"policy_denied": True},
        )
        store.signals.insert(unrelated)

        diagnosis = _scoped_diagnoser(store).diagnose_cluster(
            source_cluster.cluster_id,
            "tenant-a",
            inventory,
            target_agent_ref=agent,
            required_tool_ref=transaction_tool,
        )

        assert diagnosis.diagnosis_type is DiagnosisType.TOOL_GAP
        with pytest.raises(DiagnosisError, match="exactly match cluster source lineage"):
            OperationalDiagnoser().diagnose(
                source_cluster,
                (source_signal, unrelated),
                inventory,
                target_agent_ref=agent,
                required_tool_ref=transaction_tool,
            )


def test_cluster_diagnosis_ignores_unrelated_stale_signals() -> None:
    inventory, _, _ = inventory_graph(
        required_component_refs=(ref(ComponentType.TOOL, "get_transaction_history"),),
    )
    with SQLiteStore() as store:
        source_cluster, _ = _store_lookup_lineage(store)
        store.signals.insert(
            signal(
                "unrelated-stale",
                attributes={"knowledge_source_status": "stale"},
                quality=EvidenceQuality.STALE,
            )
        )

        diagnosis = _scoped_diagnoser(store).diagnose_cluster(
            source_cluster.cluster_id,
            "tenant-a",
            inventory,
            target_agent_ref=ref(ComponentType.AGENT, "support-agent"),
            required_tool_ref=ref(ComponentType.TOOL, "get_transaction_history"),
        )

        assert diagnosis.diagnosis_type is DiagnosisType.TOOL_GAP


def test_cluster_diagnosis_fails_closed_for_missing_or_cross_tenant_evidence() -> None:
    with SQLiteStore() as store:
        source_cluster = cluster()
        source_signal = signal()
        store.opportunities.insert(_lookup_opportunity(source_signal, source_cluster))
        store.opportunity_clusters.insert(source_cluster)
        with pytest.raises(DiagnosisError, match="missing contributing signal"):
            _scoped_diagnoser(store).diagnose_cluster(source_cluster.cluster_id, "tenant-a")

        foreign_signal = source_signal.model_copy(update={"tenant_id": "tenant-b"})
        with pytest.raises(DiagnosisError, match="cluster tenant"):
            OperationalDiagnoser().diagnose(source_cluster, (foreign_signal,))


@pytest.mark.parametrize(
    ("event_type", "facts", "quality", "expected"),
    (
        (
            "business.service.outage",
            {},
            None,
            DiagnosisType.BUSINESS_DEPENDENCY,
        ),
        (
            "diagnostic.fact",
            {"policy_denied": True},
            None,
            DiagnosisType.POLICY_CONSTRAINT,
        ),
        (
            "diagnostic.fact",
            {"approval_status": "pending"},
            None,
            DiagnosisType.APPROVAL_FRICTION,
        ),
        (
            "diagnostic.fact",
            {"knowledge_source_status": "stale"},
            None,
            DiagnosisType.KNOWLEDGE_SOURCE_ISSUE,
        ),
        (
            "diagnostic.fact",
            {},
            "STALE",
            DiagnosisType.DATA_QUALITY_ISSUE,
        ),
    ),
)
def test_cx_adapter_preserves_authoritative_diagnostic_facts_and_precedence(
    event_type: str,
    facts: dict[str, object],
    quality: str | None,
    expected: DiagnosisType,
) -> None:
    source = _DiagnosticEventSource(
        event_type=event_type,
        facts=facts,
        evidence_quality=quality,
    )
    with SQLiteStore() as store:
        result = CXPlatformEvidenceAdapter(source, tenant_id="tenant-a").ingest(
            store.signals,
            as_of=NOW + datetime.resolution,
        )
        source_signal = result.signals[0]
        source_cluster = OpportunityCluster(
            cluster_id="diagnostic-cluster",
            tenant_id="tenant-a",
            window_start=NOW,
            window_end=NOW,
            opportunity_ids=("diagnostic-opportunity",),
            source_signal_ids=(source_signal.signal_id,),
            pattern_summary="One diagnostic source fact.",
            evidence_refs=source_signal.evidence_refs,
            frequency=1.0,
            confidence=0.9,
        )

        diagnosis = OperationalDiagnoser().diagnose(source_cluster, (source_signal,))

        assert diagnosis.diagnosis_type is expected
        if facts:
            for key, value in facts.items():
                assert source_signal.normalized_attributes[key] == value
        assert source_signal.normalized_attributes["event_type"] == event_type


def test_cx_adapter_keeps_multiple_diagnostic_facts_for_later_precedence() -> None:
    facts = {
        "business_service_available": False,
        "knowledge_source_status": "stale",
        "prompt_gap": True,
    }
    source = _DiagnosticEventSource(event_type="diagnostic.fact", facts=facts)
    with SQLiteStore() as store:
        result = CXPlatformEvidenceAdapter(source, tenant_id="tenant-a").ingest(store.signals)
    normalized = result.signals[0].normalized_attributes
    assert {key: normalized[key] for key in facts} == facts


def _recommendation_for_candidate(
    proposal: object,
    diagnosis: object,
    inventory: object,
    candidate: object,
    *,
    evaluation_binding: object | None = None,
) -> object:
    candidate_reference = candidate
    assert hasattr(candidate_reference, "candidate_id")
    assert hasattr(candidate_reference, "proposal_id")
    assert hasattr(candidate_reference, "baseline_inventory_snapshot_id")
    assert hasattr(candidate_reference, "resolved_graph_digest")
    evaluation = EvaluationReference(
        evaluation_id="evaluation-graph-audit",
        tenant_id="tenant-a",
        baseline_candidate_id="candidate-baseline-1",
        candidate_id=candidate_reference.candidate_id,
        comparison_id="comparison-graph-audit",
        status="EVALUATION_SUCCEEDED",
        evidence_refs=("lab:comparison:graph-audit",),
        proposal_id=(
            candidate_reference.proposal_id if evaluation_binding is None else evaluation_binding[0]
        ),
        baseline_inventory_snapshot_id=(
            candidate_reference.baseline_inventory_snapshot_id
            if evaluation_binding is None
            else evaluation_binding[1]
        ),
        resolved_graph_digest=(
            candidate_reference.resolved_graph_digest
            if evaluation_binding is None
            else evaluation_binding[2]
        ),
    )
    return PilotRecommender().recommend(
        proposal=proposal,  # type: ignore[arg-type]
        diagnosis=diagnosis,  # type: ignore[arg-type]
        inventory=inventory,  # type: ignore[arg-type]
        candidate=candidate_reference,  # type: ignore[arg-type]
        evaluation=evaluation,
        comparison=SimpleNamespace(comparison_id="comparison-graph-audit", verdict="improved"),
        summary="Review a bounded pilot.",
        expected_operational_impact="Reduce repeated lookup work from observed evidence.",
        known_risks=("Policy remains enforced.",),
        pilot_scope={"agent_ref": candidate_reference.agent_ref.identity, "traffic_percentage": 5},
        success_criteria=("Completion improves.",),
        rollback_conditions=("Stop on regression.",),
        cluster=cluster(),
        risk_evidence_refs=("risk:graph-audit",),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda candidate: {"tool_refs": ()},
        lambda candidate: {"prompt_ref": ref(ComponentType.PROMPT, "support-prompt", "2.0.0")},
        lambda candidate: {
            "tool_refs": candidate.tool_refs + (ref(ComponentType.TOOL, "undeclared-tool"),)
        },
        lambda candidate: {
            "policy_refs": candidate.policy_refs + (ref(ComponentType.POLICY, "undeclared-policy"),)
        },
    ),
)
def test_recommendation_rejects_an_agent_identity_with_an_incorrect_graph(mutation: object) -> None:
    inventory, diagnosis, proposal, built, _ = _build_tool_candidate()
    candidate = built.candidate_reference
    mutated = candidate.model_copy(update=mutation(candidate))  # type: ignore[operator]

    with pytest.raises(RecommendationError, match="graph"):
        _recommendation_for_candidate(proposal, diagnosis, inventory, mutated)


def test_recommendation_rejects_a_candidate_with_a_stale_graph_digest() -> None:
    inventory, diagnosis, proposal, built, _ = _build_tool_candidate()
    candidate = built.candidate_reference.model_copy(
        update={"resolved_graph_digest": "stale-graph-digest"}
    )

    with pytest.raises(RecommendationError, match="digest"):
        _recommendation_for_candidate(proposal, diagnosis, inventory, candidate)


def test_recommendation_rejects_an_evaluation_that_drops_graph_binding() -> None:
    inventory, diagnosis, proposal, built, _ = _build_tool_candidate()
    candidate = built.candidate_reference

    with pytest.raises(RecommendationError, match="evaluation does not preserve"):
        _recommendation_for_candidate(
            proposal,
            diagnosis,
            inventory,
            candidate,
            evaluation_binding=(
                candidate.proposal_id,
                candidate.baseline_inventory_snapshot_id,
                "different-graph-digest",
            ),
        )


def test_recommendation_rejects_a_skill_graph_that_retains_the_removed_version() -> None:
    inventory, _, _ = inventory_graph(
        agent_tools=(harness_ref("tool", "get_payment"),),
        required_component_refs=(ref(ComponentType.TOOL, "get_transaction_history"),),
    )
    agent = ref(ComponentType.AGENT, "support-agent")
    old_skill = ref(ComponentType.SKILL, "payment-skill")
    new_skill = ref(ComponentType.SKILL, "payment-skill", "1.1.0")
    transaction_tool = ref(ComponentType.TOOL, "get_transaction_history")
    diagnosis = diagnose(
        {"skill_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_skill_ref=old_skill,
        required_tool_ref=transaction_tool,
    )
    proposal = ChangePlanner().plan(
        diagnosis,
        inventory,
        target_agent_after_ref=ref(ComponentType.AGENT, "support-agent", "1.1.0"),
        required_skill_ref=old_skill,
        skill_after_ref=new_skill,
        required_tool_ref=transaction_tool,
    )
    factory = FakeHarnessFactory()
    from cx_autopilot.integrations import HarnessCandidateAdapter

    built = HarnessCandidateAdapter(
        factory,
        evaluation_registry=factory.agent_registry,
    ).construct(proposal, inventory, _baseline_config())
    candidate = built.candidate_reference.model_copy(
        update={"skill_refs": (old_skill, *built.candidate_reference.skill_refs)}
    )

    with pytest.raises(RecommendationError, match="graph"):
        _recommendation_for_candidate(proposal, diagnosis, inventory, candidate)
