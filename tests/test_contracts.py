from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cx_autopilot.contracts import (
    AgentPromptEdge,
    AgentSkillEdge,
    AgentSystemInventorySnapshot,
    AgentToolAuthorityEdge,
    CandidateReference,
    ChangeProposal,
    ChangeStrategy,
    ChangeTarget,
    ComponentChange,
    ComponentChangeOperation,
    ComponentType,
    DecisionRecord,
    DecisionSubjectType,
    DiagnosisType,
    EvaluationReference,
    EvidenceQuality,
    ExactComponentReference,
    OperationalDisposition,
    OperationalSignal,
    Opportunity,
    OpportunityCluster,
    PilotRecommendation,
    ProblemDiagnosis,
    SkillToolDependencyEdge,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def ref(kind: ComponentType, component_id: str, version: str = "1.0.0") -> ExactComponentReference:
    return ExactComponentReference(
        component_type=kind,
        component_id=component_id,
        version=version,
        source_system="harness",
    )


def signal() -> OperationalSignal:
    return OperationalSignal(
        signal_id="signal-1",
        source_system="cx-platform",
        source_record_type="cx_event",
        source_record_id="event-1",
        source_record_version="1",
        signal_type="agent.tool_failed",
        occurred_at=NOW,
        tenant_id="tenant-a",
        interaction_id="conversation-1",
        journey_id="ticket-1",
        customer_id="customer-1",
        agent_id="support-agent",
        execution_id="execution-1",
        trace_id="trace-1",
        source_reference="cx-platform:cx_event:event-1@1",
        normalized_attributes={"tool_id": "get_transaction_history"},
        evidence_quality=EvidenceQuality.COMPLETE,
        evidence_refs=("evidence:event-1",),
    )


def inventory() -> AgentSystemInventorySnapshot:
    agent = ref(ComponentType.AGENT, "support-agent")
    prompt = ref(ComponentType.PROMPT, "support-prompt")
    skill = ref(ComponentType.SKILL, "payment-skill")
    tool = ref(ComponentType.TOOL, "get_payment")
    policy = ref(ComponentType.POLICY, "support-policy")
    return AgentSystemInventorySnapshot(
        snapshot_id="inventory-1",
        captured_at=NOW,
        tenant_id="tenant-a",
        agent_refs=(agent,),
        prompt_refs=(prompt,),
        skill_refs=(skill,),
        tool_refs=(tool,),
        policy_refs=(policy,),
        agent_to_prompt_edges=(AgentPromptEdge(agent_ref=agent, prompt_ref=prompt),),
        agent_to_skill_edges=(AgentSkillEdge(agent_ref=agent, skill_ref=skill),),
        agent_to_tool_authority_edges=(AgentToolAuthorityEdge(agent_ref=agent, tool_ref=tool),),
        skill_to_required_tool_edges=(SkillToolDependencyEdge(skill_ref=skill, tool_ref=tool),),
        registry_snapshot_ids=("registry-1",),
        manifest_refs=("manifest-1",),
        source_system="harness",
    )


def core_records() -> tuple[object, ...]:
    agent = ref(ComponentType.AGENT, "support-agent")
    prompt = ref(ComponentType.PROMPT, "support-prompt")
    skill = ref(ComponentType.SKILL, "payment-skill")
    missing_tool = ref(ComponentType.TOOL, "get_transaction_history")
    proposed_change = ComponentChange(
        operation=ComponentChangeOperation.ADD_AGENT_TOOL_REF,
        source_ref=agent,
        target_ref=missing_tool,
        rationale="Grant the exact missing executable operation.",
    )
    opportunity = Opportunity(
        opportunity_id="opportunity-1",
        tenant_id="tenant-a",
        title="Repeated transaction history lookup",
        description="Customers repeatedly need a transaction history lookup.",
        source_signal_ids=("signal-1",),
        evidence_refs=("evidence:event-1",),
        frequency_estimate=4.0,
        impact_estimate=0.8,
        confidence=0.9,
        status="DISCOVERED",
        created_at=NOW,
    )
    cluster = OpportunityCluster(
        cluster_id="cluster-1",
        tenant_id="tenant-a",
        window_start=NOW,
        window_end=NOW,
        opportunity_ids=(opportunity.opportunity_id,),
        pattern_summary="Repeated transaction history lookup failures.",
        evidence_refs=("evidence:event-1",),
        frequency=4.0,
        impact=0.8,
        confidence=0.9,
    )
    diagnosis = ProblemDiagnosis(
        diagnosis_id="diagnosis-1",
        tenant_id="tenant-a",
        cluster_id=cluster.cluster_id,
        inventory_snapshot_id="inventory-1",
        diagnosis_type=DiagnosisType.TOOL_GAP,
        summary="The existing agent lacks the required executable tool authority.",
        supporting_evidence_refs=("evidence:event-1",),
        confidence=0.9,
        affected_agent_refs=(agent,),
        affected_skill_refs=(skill,),
        affected_tool_refs=(missing_tool,),
        created_at=NOW,
    )
    proposal = ChangeProposal(
        proposal_id="proposal-1",
        tenant_id="tenant-a",
        opportunity_id=opportunity.opportunity_id,
        diagnosis_id=diagnosis.diagnosis_id,
        change_target=ChangeTarget.TOOL,
        strategy=ChangeStrategy.EXTEND,
        baseline_inventory_snapshot_id="inventory-1",
        target_agent_refs=(agent,),
        proposed_component_changes=(proposed_change,),
        rationale="Add only the missing exact tool authority.",
        evidence_refs=("evidence:event-1",),
        risk_classification="READ_ONLY",
        created_at=NOW,
    )
    candidate = CandidateReference(
        candidate_id="candidate-1",
        tenant_id="tenant-a",
        agent_ref=agent,
        manifest_id="manifest-candidate-1",
        manifest_digest="digest-candidate-1",
        registry_snapshot_id="registry-candidate-1",
        prompt_ref=prompt,
        skill_refs=(skill,),
        tool_refs=(missing_tool,),
        policy_refs=(ref(ComponentType.POLICY, "support-policy"),),
    )
    evaluation = EvaluationReference(
        evaluation_id="evaluation-1",
        tenant_id="tenant-a",
        baseline_candidate_id="candidate-baseline-1",
        candidate_id=candidate.candidate_id,
        comparison_id="comparison-1",
        promotion_evidence_id="promotion-evidence-1",
        status="PASSED",
        evidence_refs=("lab:evaluation-1", "lab:comparison-1"),
    )
    recommendation = PilotRecommendation(
        recommendation_id="recommendation-1",
        tenant_id="tenant-a",
        proposal_id=proposal.proposal_id,
        candidate_reference=candidate,
        evaluation_reference=evaluation,
        summary="Pilot the exact tool-authority extension.",
        expected_operational_impact="Reduce repeated manual transaction lookups.",
        known_risks=("Read access must remain policy governed.",),
        pilot_scope={"traffic_percentage": 5, "interaction_type": "payment_history"},
        success_criteria=("Lookup completion rate improves.",),
        rollback_conditions=("Any tenant-boundary regression is observed.",),
        evidence_refs=("evidence:event-1", "lab:comparison-1"),
        status="AWAITING_HUMAN_APPROVAL",
        created_at=NOW,
    )
    disposition = OperationalDisposition(
        disposition_id="disposition-1",
        tenant_id="tenant-a",
        diagnosis_id="diagnosis-external-1",
        reason="The business service is unavailable.",
        owner_boundary="AI-native-CX-platform",
        recommended_action="Restore the business service and collect new evidence.",
        evidence_refs=("evidence:service-outage",),
        status="READY",
        created_at=NOW,
    )
    decision = DecisionRecord(
        decision_id="decision-1",
        tenant_id="tenant-a",
        subject_type=DecisionSubjectType.PILOT_RECOMMENDATION,
        subject_id=recommendation.recommendation_id,
        decision="APPROVE_PILOT",
        actor_ref="human:operator-1",
        occurred_at=NOW,
        reason="The bounded pilot is approved for review scope.",
        evidence_refs=("lab:comparison-1",),
    )
    return (
        signal(),
        opportunity,
        cluster,
        inventory(),
        diagnosis,
        proposal,
        disposition,
        candidate,
        evaluation,
        recommendation,
        decision,
    )


def test_phase_one_records_are_typed_immutable_and_serializable() -> None:
    records = core_records()

    assert len(records) == 11
    for record in records:
        payload = record.model_dump_json()  # type: ignore[union-attr]
        assert payload
        with pytest.raises((TypeError, ValidationError)):
            record.model_config = {}  # type: ignore[misc,union-attr]

    signal_record = records[0]
    assert isinstance(signal_record, OperationalSignal)
    with pytest.raises(TypeError):
        signal_record.normalized_attributes["new"] = "value"


def test_exact_references_preserve_identity_and_reject_inexact_values() -> None:
    exact = ref(ComponentType.TOOL, "get_transaction_history", "2.4.0")
    assert exact.identity == "TOOL:get_transaction_history@2.4.0"
    assert exact.version == "2.4.0"

    with pytest.raises(ValidationError):
        ExactComponentReference(
            component_type=ComponentType.TOOL,
            component_id="get@history",
            version="2.4.0",
            source_system="harness",
        )
    with pytest.raises(ValidationError):
        ExactComponentReference(
            component_type=ComponentType.TOOL,
            component_id="get_history",
            version=" ",
            source_system="harness",
        )


def test_inventory_keeps_skill_dependency_separate_from_agent_authority() -> None:
    snapshot = inventory()

    assert snapshot.agent_to_skill_edges[0].skill_ref == snapshot.skill_refs[0]
    assert snapshot.skill_to_required_tool_edges[0].tool_ref == snapshot.tool_refs[0]
    assert snapshot.agent_to_tool_authority_edges[0].tool_ref == snapshot.tool_refs[0]

    agent = ref(ComponentType.AGENT, "support-agent")
    tool = ref(ComponentType.TOOL, "get_transaction_history")
    with pytest.raises(ValidationError):
        ComponentChange(
            operation=ComponentChangeOperation.ADD_SKILL_REQUIRED_TOOL_REF,
            source_ref=agent,
            target_ref=tool,
            rationale="Invalidly use an Agent as a Skill source.",
        )


def test_change_target_strategy_and_no_change_boundaries_are_explicit() -> None:
    proposal = next(item for item in core_records() if isinstance(item, ChangeProposal))
    assert proposal.change_target is ChangeTarget.TOOL
    assert proposal.strategy is ChangeStrategy.EXTEND

    with pytest.raises(ValidationError):
        ChangeProposal(
            proposal_id="proposal-invalid",
            tenant_id="tenant-a",
            opportunity_id="opportunity-1",
            diagnosis_id="diagnosis-1",
            change_target=ChangeTarget.NO_CHANGE,
            strategy=ChangeStrategy.NO_CHANGE,
            baseline_inventory_snapshot_id="inventory-1",
            proposed_component_changes=proposal.proposed_component_changes,
            rationale="This must be a disposition.",
            evidence_refs=("evidence:event-1",),
            risk_classification="LOW",
            created_at=NOW,
        )

    with pytest.raises(ValidationError):
        OperationalDisposition(
            disposition_id="disposition-invalid",
            tenant_id="tenant-a",
            diagnosis_id="diagnosis-1",
            strategy=ChangeStrategy.EXTEND,  # type: ignore[arg-type]
            reason="Invalid strategy.",
            owner_boundary="owner",
            recommended_action="action",
            evidence_refs=("evidence:1",),
            status="READY",
            created_at=NOW,
        )


def test_timestamps_and_evidence_are_validated() -> None:
    with pytest.raises(ValidationError):
        OperationalSignal(
            signal_id="signal-1",
            source_system="cx-platform",
            source_record_type="event",
            source_record_id="event-1",
            signal_type="event",
            occurred_at=datetime(2026, 1, 1, 12, 0),
            tenant_id="tenant-a",
            source_reference="cx:event:event-1",
            normalized_attributes={"value": 1},
            evidence_quality=EvidenceQuality.COMPLETE,
        )

    with pytest.raises(ValidationError):
        Opportunity(
            opportunity_id="opportunity-1",
            tenant_id="tenant-a",
            title="Repeated work",
            description="Repeated work.",
            source_signal_ids=("signal-1", "signal-1"),
            evidence_refs=("evidence:1",),
            frequency_estimate=1,
            impact_estimate=1,
            confidence=0.5,
            status="DISCOVERED",
            created_at=NOW,
        )

    with pytest.raises(ValidationError):
        Opportunity(
            opportunity_id="opportunity-1",
            tenant_id="tenant-a",
            title="Repeated work",
            description="Repeated work.",
            source_signal_ids=("signal-1",),
            evidence_refs=(" ",),
            frequency_estimate=1,
            impact_estimate=1,
            confidence=0.5,
            status="DISCOVERED",
            created_at=NOW,
        )
