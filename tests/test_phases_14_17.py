from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cx_autopilot.contracts import (
    CandidateReference,
    ChangeStrategy,
    ChangeTarget,
    ComponentChangeOperation,
    ComponentType,
    DiagnosisType,
    EvidenceQuality,
    OperationalDisposition,
)
from cx_autopilot.integrations import (
    HarnessCandidateAdapter,
    HarnessCandidateError,
    ImprovementLabEvaluationAdapter,
    ImprovementLabEvaluationError,
)
from cx_autopilot.reference import (
    REFERENCE_AGENT,
    REFERENCE_AGENT_AFTER,
    REFERENCE_PAYMENT_TOOL,
    REFERENCE_POLICY,
    REFERENCE_PROMPT,
    REFERENCE_SKILL,
    REFERENCE_TENANT_ID,
    REFERENCE_TRANSACTION_TOOL,
    run_reference_cycle,
)
from cx_autopilot.storage import SQLiteStore
from cx_autopilot.strategy import ChangePlanner
from test_phases_6_9 import diagnose, harness_ref, inventory_graph, ref


def test_transaction_history_reference_cycle_proves_the_governed_path() -> None:
    with SQLiteStore() as store:
        result = run_reference_cycle(store)

        source_signals = tuple(
            signal for signal in result.ingestion.signals if signal.source_record_type == "cx_event"
        )
        assert len(source_signals) == 3
        assert all(signal.source_reference.startswith("cx-platform:") for signal in source_signals)
        assert all(
            signal.interaction_id and signal.journey_id and signal.trace_id
            for signal in source_signals
        )
        assert result.duplicate_ingestion.duplicate_signal_ids
        assert len(store.signals.list(tenant_id=REFERENCE_TENANT_ID)) == len(
            result.ingestion.signals
        )

        assert len(result.opportunities) == 1
        opportunity = result.opportunities[0]
        assert opportunity.pattern_key == "operation:get_transaction_history"
        assert opportunity.frequency_estimate == 3.0
        assert opportunity.impact_estimate is None
        assert opportunity.operational_effort_estimate is None
        assert opportunity.risk_estimate is None

        assert len(result.clusters) == 1
        cluster = result.clusters[0]
        assert cluster.opportunity_ids == (opportunity.opportunity_id,)
        assert cluster.frequency == 3.0
        assert cluster.priority_rank == 1
        assert cluster.prioritization_factors.available_factors == (
            "frequency",
            "confidence",
        )
        assert "impact" in cluster.prioritization_factors.unavailable_factors
        assert "operational_effort" in cluster.prioritization_factors.unavailable_factors
        assert "risk" in cluster.prioritization_factors.unavailable_factors

        inventory = result.inventory
        assert result.inventory_snapshot_calls == 1
        assert REFERENCE_SKILL in inventory.skill_refs
        assert REFERENCE_TRANSACTION_TOOL in inventory.tool_refs
        assert any(
            edge.agent_ref == REFERENCE_AGENT and edge.skill_ref == REFERENCE_SKILL
            for edge in inventory.agent_to_skill_edges
        )
        assert not any(
            edge.agent_ref == REFERENCE_AGENT and edge.tool_ref == REFERENCE_TRANSACTION_TOOL
            for edge in inventory.agent_to_tool_authority_edges
        )
        assert result.diagnosis.diagnosis_type is DiagnosisType.TOOL_GAP
        assert REFERENCE_TRANSACTION_TOOL in result.diagnosis.affected_tool_refs

        proposal = result.proposal
        assert proposal.change_target is ChangeTarget.TOOL
        assert proposal.strategy is ChangeStrategy.EXTEND
        assert len(proposal.proposed_component_changes) == 1
        change = proposal.proposed_component_changes[0]
        assert change.operation is ComponentChangeOperation.ADD_AGENT_TOOL_REF
        assert change.subject_before_ref == REFERENCE_AGENT
        assert change.subject_after_ref == REFERENCE_AGENT_AFTER
        assert change.related_before_ref is None
        assert change.related_after_ref == REFERENCE_TRANSACTION_TOOL
        assert all(
            item.operation is not ComponentChangeOperation.CREATE_SKILL
            for item in proposal.proposed_component_changes
        )

        candidate = result.candidate.candidate_reference
        assert result.harness_build_calls == 1
        assert candidate.agent_ref == REFERENCE_AGENT_AFTER
        assert candidate.prompt_ref == REFERENCE_PROMPT
        assert candidate.skill_refs == (REFERENCE_SKILL,)
        assert candidate.tool_refs == (REFERENCE_PAYMENT_TOOL, REFERENCE_TRANSACTION_TOOL)
        assert candidate.policy_refs == (REFERENCE_POLICY,)
        assert result.candidate.manifest.manifest_id == candidate.manifest_id
        assert result.candidate.manifest.manifest_digest == candidate.manifest_digest

        evaluation = result.evaluation.evaluation_reference
        assert result.lab_run_candidate_ids == (
            "candidate-transaction-history-baseline",
            candidate.candidate_id,
        )
        assert result.lab_comparison_calls == 1
        assert evaluation.status == "EVALUATION_SUCCEEDED"
        assert evaluation.baseline_candidate_id == "candidate-transaction-history-baseline"
        assert evaluation.candidate_id == candidate.candidate_id
        assert evaluation.comparison_id
        assert "cx-platform:cases:transaction-history@1.0.0" in evaluation.evidence_refs
        assert any(ref.startswith("harness:digest:") for ref in evaluation.evidence_refs)

        recommendation = result.recommendation
        assert recommendation.status == "READY_FOR_HUMAN_APPROVAL"
        assert recommendation.requires_human_approval is True
        assert recommendation.candidate_reference == candidate
        assert recommendation.evaluation_reference == evaluation
        assert recommendation.evaluation_reference.comparison_id == getattr(
            result.evaluation.comparison,
            "comparison_id",
        )

        assert result.decision.decision == "APPROVE"
        assert result.audit.recommendation == recommendation
        assert result.audit.proposal == proposal
        assert result.audit.diagnosis == result.diagnosis
        assert result.audit.inventory == inventory
        assert result.audit.cluster == cluster
        assert result.audit.candidate == candidate
        assert result.audit.evaluation == evaluation
        assert result.production_authority_before == result.production_authority_after


def test_phase_15_component_gap_taxonomy_selects_the_primary_target() -> None:
    planner = ChangePlanner()
    inventory, _, _ = inventory_graph(
        agent_tools=(harness_ref("tool", "get_payment"),),
    )
    agent = ref(ComponentType.AGENT, "support-agent")

    agent_gap = diagnose(
        {"agent_gap": True},
        inventory=inventory,
        target_agent_ref=ref(ComponentType.AGENT, "missing-agent"),
    )
    assert agent_gap.diagnosis_type is DiagnosisType.AGENT_GAP
    assert planner.select(
        agent_gap,
        inventory,
        target_agent_ref=ref(ComponentType.AGENT, "missing-agent"),
    ) == (ChangeTarget.AGENT, ChangeStrategy.CREATE)

    skill_gap = diagnose(
        {"skill_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_skill_ref=ref(ComponentType.SKILL, "duplicate-charge-resolution"),
        required_tool_ref=ref(ComponentType.TOOL, "get_payment"),
    )
    assert skill_gap.diagnosis_type is DiagnosisType.SKILL_GAP
    assert planner.select(
        skill_gap,
        inventory,
        target_agent_ref=agent,
        required_skill_ref=ref(ComponentType.SKILL, "duplicate-charge-resolution"),
        required_tool_ref=ref(ComponentType.TOOL, "get_payment"),
    ) == (ChangeTarget.SKILL, ChangeStrategy.CREATE)

    prompt_gap = diagnose(
        {"prompt_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_prompt_ref=REFERENCE_PROMPT,
    )
    assert prompt_gap.diagnosis_type is DiagnosisType.PROMPT_GAP
    assert planner.select(
        prompt_gap,
        inventory,
        target_agent_ref=agent,
        required_prompt_ref=REFERENCE_PROMPT,
    ) == (ChangeTarget.PROMPT, ChangeStrategy.EXTEND)


class _NoCallHarnessFactory:
    def __init__(self) -> None:
        self.agent_registry = object()
        self.calls = 0

    def build(
        self,
        config: object,
        *,
        dry_run: bool = False,
        activate: bool = True,
        register: bool = True,
    ) -> object:
        del config, dry_run, activate, register
        self.calls += 1
        raise AssertionError("Harness must not be called for NO_CHANGE")


class _NoCallLabRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_sync(
        self,
        dataset: object,
        candidate: object,
        manifest: object,
        *,
        repeat: int = 1,
    ) -> object:
        del dataset, candidate, manifest, repeat
        self.calls += 1
        raise AssertionError("Lab must not be called for NO_CHANGE")


class _NoCallLabComparator:
    def __init__(self) -> None:
        self.calls = 0

    def compare(
        self,
        baseline_report: object,
        candidate_report: object,
        **kwargs: object,
    ) -> object:
        del baseline_report, candidate_report, kwargs
        self.calls += 1
        raise AssertionError("Lab comparison must not be called for NO_CHANGE")


def _candidate_reference(candidate_id: str) -> CandidateReference:
    return CandidateReference(
        candidate_id=candidate_id,
        tenant_id=REFERENCE_TENANT_ID,
        agent_ref=REFERENCE_AGENT,
        manifest_id=f"manifest-{candidate_id}",
        manifest_digest=f"digest-{candidate_id}",
        registry_snapshot_id=f"registry-{candidate_id}",
        prompt_ref=REFERENCE_PROMPT,
        skill_refs=(REFERENCE_SKILL,),
        tool_refs=(REFERENCE_PAYMENT_TOOL,),
        policy_refs=(REFERENCE_POLICY,),
    )


def _assert_no_change_short_circuits(
    disposition: OperationalDisposition,
    inventory: object,
) -> None:
    factory = _NoCallHarnessFactory()
    with pytest.raises(HarnessCandidateError, match="NO_CHANGE"):
        HarnessCandidateAdapter(
            factory,
            evaluation_registry=factory.agent_registry,
        ).construct(disposition, inventory, {})  # type: ignore[arg-type]
    assert factory.calls == 0

    runner = _NoCallLabRunner()
    comparator = _NoCallLabComparator()
    with pytest.raises(ImprovementLabEvaluationError, match="NO_CHANGE"):
        ImprovementLabEvaluationAdapter(runner, comparator).evaluate(
            SimpleNamespace(candidate_id="candidate-baseline"),
            disposition,
            baseline_reference=_candidate_reference("candidate-baseline"),
            candidate_reference=_candidate_reference("candidate-change"),
            dataset=SimpleNamespace(dataset_id="unused", version="1.0.0"),
            baseline_manifest=SimpleNamespace(),
            candidate_manifest=SimpleNamespace(),
        )
    assert runner.calls == 0
    assert comparator.calls == 0


def test_phase_15_external_and_governance_causes_are_terminal_no_change() -> None:
    inventory, _, _ = inventory_graph()
    cases = (
        (
            diagnose({"policy_denied": True}),
            DiagnosisType.POLICY_CONSTRAINT,
            "policy",
        ),
        (
            diagnose({"approval_status": "pending"}, source_record_type="approval"),
            DiagnosisType.APPROVAL_FRICTION,
            "bypass",
        ),
        (
            diagnose({"business_service_available": False}),
            DiagnosisType.BUSINESS_DEPENDENCY,
            "external",
        ),
        (
            diagnose(
                {"observed": "conflicting source records"},
                quality=EvidenceQuality.CONFLICTING,
            ),
            DiagnosisType.DATA_QUALITY_ISSUE,
            "evidence",
        ),
        (
            diagnose({"knowledge_source_available": False}),
            DiagnosisType.KNOWLEDGE_SOURCE_ISSUE,
            "knowledge",
        ),
    )
    planner = ChangePlanner()
    for diagnosis, expected_type, expected_text in cases:
        assert diagnosis.diagnosis_type is expected_type
        assert planner.select(diagnosis) == (ChangeTarget.NO_CHANGE, ChangeStrategy.NO_CHANGE)
        disposition = planner.plan(diagnosis)
        assert isinstance(disposition, OperationalDisposition)
        assert disposition.strategy is ChangeStrategy.NO_CHANGE
        assert expected_text in (disposition.reason + " " + disposition.recommended_action).lower()
        _assert_no_change_short_circuits(disposition, inventory)


def test_cli_reference_cycle_and_lineage_inspection(tmp_path: Path) -> None:
    database = tmp_path / "reference.sqlite"
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    source_path = str(repository_root / "src")
    environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")

    cycle = subprocess.run(
        [
            sys.executable,
            "-m",
            "cx_autopilot",
            "--db",
            str(database),
            "run",
            "reference",
            "cycle",
        ],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert cycle.returncode == 0, cycle.stderr
    cycle_record = json.loads(cycle.stdout)
    assert cycle_record["decision"] == "APPROVE"
    assert cycle_record["production_authority_unchanged"] is True

    lineage = subprocess.run(
        [
            sys.executable,
            "-m",
            "cx_autopilot",
            "--db",
            str(database),
            "inspect",
            "lineage",
            cycle_record["decision_id"],
        ],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert lineage.returncode == 0, lineage.stderr
    lineage_record = json.loads(lineage.stdout)
    assert lineage_record["decision"]["decision"] == "APPROVE"
    assert (
        lineage_record["recommendation"]["evaluation_reference"]["comparison_id"]
        == cycle_record["comparison_id"]
    )
    assert lineage_record["candidate"]["agent_ref"]["version"] == "1.1.0"
