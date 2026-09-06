from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from cx_autopilot.contracts import (
    CandidateReference,
    ComponentType,
    EvaluationReference,
    OperationalDisposition,
)
from cx_autopilot.decisions import DecisionService
from cx_autopilot.integrations import (
    HarnessCandidateAdapter,
    HarnessCandidateError,
    ImprovementLabEvaluationAdapter,
    ImprovementLabEvaluationError,
)
from cx_autopilot.recommendations import PilotRecommender, RecommendationError
from cx_autopilot.storage import SQLiteStore
from cx_autopilot.strategy import ChangePlanner
from test_phases_6_9 import (
    NOW,
    cluster,
    diagnose,
    harness_ref,
    inventory_graph,
    ref,
)


def _ref_payload(reference: object) -> dict[str, str]:
    component_type = getattr(reference, "component_type")
    value = getattr(component_type, "value", component_type)
    return {
        "component_type": str(value).lower(),
        "component_id": getattr(reference, "component_id"),
        "version": getattr(reference, "version"),
    }


def _baseline_config(
    *, prompt_version: str = "1.0.0", include_tool: bool = True
) -> dict[str, object]:
    tools = [_ref_payload(ref(ComponentType.TOOL, "get_payment"))] if include_tool else []
    return {
        "identity": {"agent_id": "support-agent", "version": "1.0.0"},
        "goal": "Support payment customers.",
        "prompt_ref": _ref_payload(ref(ComponentType.PROMPT, "support-prompt", prompt_version)),
        "skill_refs": [_ref_payload(ref(ComponentType.SKILL, "payment-skill"))],
        "tool_refs": tools,
        "policy_refs": [_ref_payload(ref(ComponentType.POLICY, "support-policy"))],
    }


def _manifest_from_config(
    config: dict[str, object],
    *,
    manifest_id: str = "manifest-candidate-1",
    manifest_digest: str = "digest-candidate-1",
    registry_snapshot_id: str = "registry-evaluation-1",
    omit_tool: bool = False,
) -> object:
    identity = config["identity"]
    assert isinstance(identity, dict)
    tool_refs = tuple(config["tool_refs"] or ())
    if omit_tool:
        tool_refs = ()
    agent = SimpleNamespace(
        identity=SimpleNamespace(
            agent_id=identity["agent_id"],
            version=identity["version"],
        ),
        prompt_ref=config["prompt_ref"],
        skill_refs=tuple(config["skill_refs"] or ()),
        tool_refs=tool_refs,
        policy_refs=tuple(config["policy_refs"] or ()),
    )
    return SimpleNamespace(
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
        registry_snapshot_id=registry_snapshot_id,
        agent=agent,
        prompt_ref=config["prompt_ref"],
        skill_refs=tuple(config["skill_refs"] or ()),
        tool_refs=tool_refs,
        policy_refs=tuple(config["policy_refs"] or ()),
    )


class FakeEvaluationRegistry:
    pass


class FakeHarnessFactory:
    def __init__(self, *, omit_tool: bool = False) -> None:
        self.agent_registry = FakeEvaluationRegistry()
        self.omit_tool = omit_tool
        self.calls: list[dict[str, object]] = []

    def build(
        self,
        config: object,
        *,
        dry_run: bool = False,
        activate: bool = True,
        register: bool = True,
    ) -> object:
        assert isinstance(config, dict)
        self.calls.append(
            {
                "config": config,
                "dry_run": dry_run,
                "activate": activate,
                "register": register,
            }
        )
        return SimpleNamespace(manifest=_manifest_from_config(config, omit_tool=self.omit_tool))


class ProductionRegistry:
    def __init__(self) -> None:
        self.calls = 0


def _tool_proposal() -> tuple[object, object, object]:
    transaction_tool = ref(ComponentType.TOOL, "get_transaction_history")
    inventory, _, _ = inventory_graph(
        agent_tools=(harness_ref("tool", "get_payment"),),
        required_component_refs=(transaction_tool,),
    )
    agent = ref(ComponentType.AGENT, "support-agent")
    diagnosis = diagnose(
        {"tool_id": "get_transaction_history"},
        inventory=inventory,
        target_agent_ref=agent,
        required_tool_ref=transaction_tool,
    )
    proposal = ChangePlanner().plan(
        diagnosis,
        inventory,
        target_agent_after_ref=ref(ComponentType.AGENT, "support-agent", "1.1.0"),
        required_tool_ref=transaction_tool,
    )
    return inventory, diagnosis, proposal


def _build_tool_candidate(
    *,
    factory: FakeHarnessFactory | None = None,
    store: SQLiteStore | None = None,
) -> tuple[object, object, object, object, FakeHarnessFactory]:
    inventory, diagnosis, proposal = _tool_proposal()
    harness_factory = factory or FakeHarnessFactory()
    result = HarnessCandidateAdapter(
        harness_factory,
        evaluation_registry=harness_factory.agent_registry,
        production_registry=ProductionRegistry(),
        candidate_store=store.candidates if store is not None else None,
    ).construct(proposal, inventory, _baseline_config())
    return inventory, diagnosis, proposal, result, harness_factory


def test_harness_candidate_preserves_manifest_provenance_and_never_touches_production() -> None:
    production = ProductionRegistry()
    factory = FakeHarnessFactory()
    inventory, _, proposal = _tool_proposal()
    result = HarnessCandidateAdapter(
        factory,
        evaluation_registry=factory.agent_registry,
        production_registry=production,
    ).construct(proposal, inventory, _baseline_config())

    assert result.candidate_reference.manifest_id == "manifest-candidate-1"
    assert result.candidate_reference.manifest_digest == "digest-candidate-1"
    assert result.candidate_reference.registry_snapshot_id == "registry-evaluation-1"
    assert result.candidate_reference.agent_ref.identity == "AGENT:support-agent@1.1.0"
    assert result.candidate_reference.tool_refs[-1].identity == (
        "TOOL:get_transaction_history@1.0.0"
    )
    assert result.candidate_reference.proposal_id == proposal.proposal_id
    assert result.candidate_reference.baseline_inventory_snapshot_id == inventory.snapshot_id
    assert result.candidate_reference.resolved_graph_digest
    assert factory.calls[0]["dry_run"] is False
    assert factory.calls[0]["activate"] is True
    assert factory.calls[0]["register"] is True
    assert inventory.snapshot_id
    assert production.calls == 0


def test_harness_candidate_rejects_a_manifest_that_changes_the_proposed_graph() -> None:
    factory = FakeHarnessFactory(omit_tool=True)
    inventory, _, proposal = _tool_proposal()

    with pytest.raises(HarnessCandidateError, match="does not match proposal graph"):
        HarnessCandidateAdapter(
            factory,
            evaluation_registry=factory.agent_registry,
        ).construct(proposal, inventory, _baseline_config())


def test_skill_and_prompt_candidates_include_the_resulting_agent_graph() -> None:
    inventory, _, _ = inventory_graph(agent_tools=(harness_ref("tool", "get_payment"),))
    agent = ref(ComponentType.AGENT, "support-agent")
    old_skill = ref(ComponentType.SKILL, "payment-skill")
    transaction_tool = ref(ComponentType.TOOL, "get_transaction_history")
    skill_diagnosis = diagnose(
        {"skill_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_skill_ref=old_skill,
        required_tool_ref=transaction_tool,
    )
    skill_proposal = ChangePlanner().plan(
        skill_diagnosis,
        inventory,
        target_agent_after_ref=ref(ComponentType.AGENT, "support-agent", "1.1.0"),
        required_skill_ref=old_skill,
        skill_after_ref=ref(ComponentType.SKILL, "payment-skill", "1.1.0"),
        required_tool_ref=transaction_tool,
    )
    skill_factory = FakeHarnessFactory()
    skill_result = HarnessCandidateAdapter(
        skill_factory,
        evaluation_registry=skill_factory.agent_registry,
    ).construct(skill_proposal, inventory, _baseline_config())
    assert skill_result.candidate_reference.agent_ref.identity == "AGENT:support-agent@1.1.0"
    assert skill_result.candidate_reference.skill_refs[0].identity == "SKILL:payment-skill@1.1.0"
    assert [item.identity for item in skill_result.candidate_reference.tool_refs] == [
        "TOOL:get_payment@1.0.0"
    ]

    prompt = ref(ComponentType.PROMPT, "support-prompt")
    prompt_diagnosis = diagnose(
        {"prompt_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_prompt_ref=prompt,
    )
    prompt_proposal = ChangePlanner().plan(
        prompt_diagnosis,
        inventory,
        target_agent_after_ref=ref(ComponentType.AGENT, "support-agent", "1.1.0"),
        required_prompt_ref=prompt,
        prompt_after_ref=ref(ComponentType.PROMPT, "support-prompt", "1.1.0"),
    )
    prompt_factory = FakeHarnessFactory()
    prompt_result = HarnessCandidateAdapter(
        prompt_factory,
        evaluation_registry=prompt_factory.agent_registry,
    ).construct(prompt_proposal, inventory, _baseline_config())
    assert prompt_result.candidate_reference.agent_ref.identity == "AGENT:support-agent@1.1.0"
    assert prompt_result.candidate_reference.prompt_ref.identity == "PROMPT:support-prompt@1.1.0"


def test_no_change_does_not_enter_harness_or_lab() -> None:
    disposition = ChangePlanner().plan(diagnose({"policy_denied": True}))
    assert isinstance(disposition, OperationalDisposition)
    factory = FakeHarnessFactory()
    inventory, _, _ = _tool_proposal()
    with pytest.raises(HarnessCandidateError, match="NO_CHANGE"):
        HarnessCandidateAdapter(
            factory,
            evaluation_registry=factory.agent_registry,
        ).construct(disposition, inventory, _baseline_config())


class FakeLabRunner:
    def __init__(self, *, fail_candidate_id: str | None = None) -> None:
        self.fail_candidate_id = fail_candidate_id
        self.calls: list[str] = []

    def run_sync(
        self,
        dataset: object,
        candidate: object,
        manifest: object,
        *,
        repeat: int = 1,
    ) -> object:
        del dataset, manifest, repeat
        candidate_id = getattr(candidate, "candidate_id")
        self.calls.append(candidate_id)
        if candidate_id == self.fail_candidate_id:
            raise RuntimeError("runner failure")
        return SimpleNamespace(report=SimpleNamespace(run_id=f"run:{candidate_id}"))


class FakeLabComparator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def compare(
        self,
        baseline_report: object,
        candidate_report: object,
        **kwargs: object,
    ) -> object:
        self.calls.append(
            {
                "baseline_report": baseline_report,
                "candidate_report": candidate_report,
                **kwargs,
            }
        )
        return SimpleNamespace(comparison_id="comparison-1", verdict="improved")


def _baseline_reference() -> CandidateReference:
    return CandidateReference(
        candidate_id="candidate-baseline-1",
        tenant_id="tenant-a",
        agent_ref=ref(ComponentType.AGENT, "support-agent"),
        manifest_id="manifest-baseline-1",
        manifest_digest="digest-baseline-1",
        registry_snapshot_id="registry-baseline-1",
        prompt_ref=ref(ComponentType.PROMPT, "support-prompt"),
        skill_refs=(ref(ComponentType.SKILL, "payment-skill"),),
        tool_refs=(ref(ComponentType.TOOL, "get_payment"),),
        policy_refs=(ref(ComponentType.POLICY, "support-policy"),),
    )


def _manifest_for(reference: CandidateReference) -> object:
    return SimpleNamespace(
        resolved_manifest_id=reference.manifest_id,
        resolved_manifest_digest=reference.manifest_digest,
        registry_snapshot_id=reference.registry_snapshot_id,
    )


def test_lab_adapter_calls_runner_twice_and_preserves_comparison_inputs() -> None:
    _, _, _, built, _ = _build_tool_candidate()
    baseline = _baseline_reference()
    runner = FakeLabRunner()
    comparator = FakeLabComparator()
    result = ImprovementLabEvaluationAdapter(runner, comparator).evaluate(
        SimpleNamespace(candidate_id=baseline.candidate_id),
        SimpleNamespace(candidate_id=built.candidate_reference.candidate_id),
        baseline_reference=baseline,
        candidate_reference=built.candidate_reference,
        dataset=SimpleNamespace(dataset_id="payment-cases", version="1.0.0"),
        baseline_manifest=_manifest_for(baseline),
        candidate_manifest=built.manifest,
        case_data_refs=("case:payment-1",),
        operational_evidence_refs=("evidence:lookup-repeat",),
    )

    assert runner.calls == [baseline.candidate_id, built.candidate_reference.candidate_id]
    assert len(comparator.calls) == 1
    assert comparator.calls[0]["candidate_manifest"] is built.manifest
    assert result.evaluation_reference.status == "EVALUATION_SUCCEEDED"
    assert result.evaluation_reference.comparison_id == "comparison-1"
    assert result.evaluation_reference.proposal_id == built.candidate_reference.proposal_id
    assert (
        result.evaluation_reference.baseline_inventory_snapshot_id
        == built.candidate_reference.baseline_inventory_snapshot_id
    )
    assert (
        result.evaluation_reference.resolved_graph_digest
        == built.candidate_reference.resolved_graph_digest
    )
    assert "case:payment-1" in result.evaluation_reference.evidence_refs
    assert "harness:digest:digest-candidate-1" in result.evaluation_reference.evidence_refs


def test_lab_failure_is_terminal_and_never_retries_or_self_modifies() -> None:
    _, _, _, built, _ = _build_tool_candidate()
    baseline = _baseline_reference()
    runner = FakeLabRunner(fail_candidate_id=built.candidate_reference.candidate_id)
    comparator = FakeLabComparator()
    result = ImprovementLabEvaluationAdapter(runner, comparator).evaluate(
        SimpleNamespace(candidate_id=baseline.candidate_id),
        SimpleNamespace(candidate_id=built.candidate_reference.candidate_id),
        baseline_reference=baseline,
        candidate_reference=built.candidate_reference,
        dataset=SimpleNamespace(dataset_id="payment-cases", version="1.0.0"),
        baseline_manifest=_manifest_for(baseline),
        candidate_manifest=built.manifest,
    )

    assert result.evaluation_reference.status == "EVALUATION_FAILED"
    assert runner.calls == [baseline.candidate_id, built.candidate_reference.candidate_id]
    assert comparator.calls == []
    assert result.comparison is None
    with pytest.raises(ImprovementLabEvaluationError):
        ImprovementLabEvaluationAdapter(runner, comparator).evaluate(
            SimpleNamespace(candidate_id=baseline.candidate_id),
            OperationalDisposition(
                disposition_id="disposition-lab-no-change",
                tenant_id="tenant-a",
                diagnosis_id="diagnosis-no-change",
                reason="No candidate.",
                owner_boundary="cx-autopilot",
                recommended_action="Wait.",
                evidence_refs=("evidence:no-change",),
                status="NO_CANDIDATE",
                created_at=NOW,
            ),
            baseline_reference=baseline,
            candidate_reference=built.candidate_reference,
            dataset=SimpleNamespace(dataset_id="payment-cases", version="1.0.0"),
            baseline_manifest=_manifest_for(baseline),
            candidate_manifest=built.manifest,
        )


def test_recommendation_requires_improved_evidence_and_contains_the_full_chain() -> None:
    with SQLiteStore() as store:
        inventory, diagnosis, proposal, built, _ = _build_tool_candidate(store=store)
        baseline = _baseline_reference()
        evaluation = EvaluationReference(
            evaluation_id="evaluation-recommendation-1",
            tenant_id="tenant-a",
            baseline_candidate_id=baseline.candidate_id,
            candidate_id=built.candidate_reference.candidate_id,
            comparison_id="comparison-recommendation-1",
            status="EVALUATION_SUCCEEDED",
            evidence_refs=("lab:evaluation:1", "lab:comparison:1"),
            proposal_id=proposal.proposal_id,
            baseline_inventory_snapshot_id=inventory.snapshot_id,
            resolved_graph_digest=built.candidate_reference.resolved_graph_digest,
        )
        comparison = SimpleNamespace(
            comparison_id="comparison-recommendation-1", verdict="improved"
        )
        recommendation = PilotRecommender(store.recommendations).recommend(
            proposal=proposal,
            diagnosis=diagnosis,
            inventory=inventory,
            candidate=built.candidate_reference,
            evaluation=evaluation,
            comparison=comparison,
            summary="Review a bounded transaction-history pilot.",
            expected_operational_impact=(
                "Reduce repeated manual lookup work, based on the CX evidence."
            ),
            known_risks=("Read authority remains policy governed.",),
            pilot_scope={
                "agent_ref": built.candidate_reference.agent_ref.identity,
                "traffic_percentage": 5,
                "duration_seconds": 3600,
            },
            success_criteria=("Lookup completion improves without a safety regression.",),
            rollback_conditions=("Abort on any tenant-boundary regression.",),
            cluster=cluster(),
            risk_evidence_refs=("risk:policy-review",),
        )

        assert recommendation.status == "READY_FOR_HUMAN_APPROVAL"
        assert recommendation.requires_human_approval is True
        assert recommendation.candidate_reference == built.candidate_reference
        assert recommendation.evaluation_reference == evaluation
        assert "evidence:cluster-1" in recommendation.evidence_refs
        assert "risk:policy-review" in recommendation.evidence_refs
        assert "inventory:" + inventory.snapshot_id in recommendation.evidence_refs
        assert (
            store.recommendations.get(recommendation.recommendation_id, tenant_id="tenant-a")
            == recommendation
        )

        with pytest.raises(RecommendationError, match="successful Lab evaluation"):
            PilotRecommender().recommend(
                proposal=proposal,
                diagnosis=diagnosis,
                inventory=inventory,
                candidate=built.candidate_reference,
                evaluation=evaluation.model_copy(update={"status": "EVALUATION_FAILED"}),
                comparison=comparison,
                summary="Review a bounded pilot.",
                expected_operational_impact="Evidence-derived impact.",
                known_risks=("Risk is reviewed.",),
                pilot_scope={
                    "agent_ref": built.candidate_reference.agent_ref.identity,
                    "traffic_percentage": 5,
                },
                success_criteria=("It works.",),
                rollback_conditions=("Stop on regression.",),
                cluster=cluster(),
                risk_evidence_refs=("risk:review",),
            )


def test_recommendation_rejects_comparison_not_referenced_by_evaluation() -> None:
    inventory, diagnosis, proposal, built, _ = _build_tool_candidate()
    baseline = _baseline_reference()
    evaluation = EvaluationReference(
        evaluation_id="evaluation-comparison-match-1",
        tenant_id="tenant-a",
        baseline_candidate_id=baseline.candidate_id,
        candidate_id=built.candidate_reference.candidate_id,
        comparison_id="comparison-1",
        status="EVALUATION_SUCCEEDED",
        evidence_refs=("lab:comparison:1",),
    )

    with pytest.raises(RecommendationError, match="comparison does not match"):
        PilotRecommender().recommend(
            proposal=proposal,
            diagnosis=diagnosis,
            inventory=inventory,
            candidate=built.candidate_reference,
            evaluation=evaluation,
            comparison=SimpleNamespace(comparison_id="comparison-2", verdict="improved"),
            summary="Review a bounded transaction-history pilot.",
            expected_operational_impact="Reduce repeated manual lookup work from evidence.",
            known_risks=("Read authority remains policy governed.",),
            pilot_scope={
                "agent_ref": built.candidate_reference.agent_ref.identity,
                "traffic_percentage": 5,
            },
            success_criteria=("Lookup completion improves.",),
            rollback_conditions=("Abort on a safety regression.",),
            cluster=cluster(),
            risk_evidence_refs=("risk:comparison-match",),
        )


def test_decisions_persist_canonical_outcomes_and_audit_back_to_evidence() -> None:
    with SQLiteStore() as store:
        inventory, diagnosis, proposal, built, _ = _build_tool_candidate(store=store)
        source_cluster = cluster()
        store.opportunity_clusters.insert(source_cluster)
        store.inventory.insert(inventory)
        store.diagnoses.insert(diagnosis)
        store.proposals.insert(proposal)
        store.candidates.insert(built.candidate_reference)
        recommendation = PilotRecommender(store.recommendations).recommend(
            proposal=proposal,
            diagnosis=diagnosis,
            inventory=inventory,
            candidate=built.candidate_reference,
            evaluation=EvaluationReference(
                evaluation_id="evaluation-decision-1",
                tenant_id="tenant-a",
                baseline_candidate_id="candidate-baseline-1",
                candidate_id=built.candidate_reference.candidate_id,
                comparison_id="comparison-decision-1",
                status="EVALUATION_SUCCEEDED",
                evidence_refs=("lab:comparison:decision",),
                proposal_id=proposal.proposal_id,
                baseline_inventory_snapshot_id=inventory.snapshot_id,
                resolved_graph_digest=built.candidate_reference.resolved_graph_digest,
            ),
            comparison=SimpleNamespace(comparison_id="comparison-decision-1", verdict="improved"),
            summary="Review a bounded pilot.",
            expected_operational_impact="Reduce repeated lookup work from observed evidence.",
            known_risks=("Policy must remain enforced.",),
            pilot_scope={
                "agent_ref": built.candidate_reference.agent_ref.identity,
                "traffic_percentage": 5,
            },
            success_criteria=("Completion improves.",),
            rollback_conditions=("Stop on regression.",),
            cluster=source_cluster,
            risk_evidence_refs=("risk:decision-review",),
        )
        store.recommendations.insert(recommendation)
        service = DecisionService(store)
        approved = service.record_pilot_decision(
            recommendation,
            "approve",
            "human:reviewer-1",
            "Approve the bounded pilot for human-controlled execution.",
            occurred_at=NOW + timedelta(seconds=1),
        )
        requested = service.record_pilot_decision(
            recommendation,
            "request-change",
            "human:reviewer-2",
            "Request a narrower scope.",
            occurred_at=NOW + timedelta(seconds=2),
        )
        rejected = service.record_pilot_decision(
            recommendation,
            "reject",
            "human:reviewer-3",
            "Reject the current evidence.",
            occurred_at=NOW + timedelta(seconds=3),
        )
        closed = service.record_pilot_decision(
            recommendation,
            "close",
            "human:reviewer-4",
            "Close the review.",
            occurred_at=NOW + timedelta(seconds=4),
        )
        assert (approved.decision, requested.decision, rejected.decision, closed.decision) == (
            "APPROVE",
            "REQUEST_CHANGE",
            "REJECT",
            "CLOSE",
        )
        audit = service.audit(approved.decision_id, tenant_id="tenant-a")
        assert audit.recommendation == recommendation
        assert audit.proposal == proposal
        assert audit.diagnosis == diagnosis
        assert audit.inventory == inventory
        assert audit.cluster == source_cluster
        assert "evidence:cluster-1" in audit.evidence_refs
        assert "risk:decision-review" in audit.evidence_refs
        assert f"recommendation:{recommendation.recommendation_id}" in audit.evidence_refs

        no_change_diagnosis = diagnose({"policy_denied": True})
        no_change = ChangePlanner().plan(no_change_diagnosis)
        assert isinstance(no_change, OperationalDisposition)
        store.diagnoses.insert(no_change_diagnosis)
        store.dispositions.insert(no_change)
        disposition_decision = service.record_disposition_decision(
            no_change,
            "accept",
            "human:reviewer-5",
            "Accept the external ownership boundary.",
            occurred_at=NOW + timedelta(seconds=5),
        )
        disposition_audit = service.audit(
            disposition_decision.decision_id,
            tenant_id="tenant-a",
        )
        assert disposition_audit.disposition == no_change
        assert disposition_audit.diagnosis == no_change_diagnosis
        assert disposition_audit.inventory is None
