from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from cx_autopilot.contracts import (
    AgentSystemInventorySnapshot,
    ChangeProposal,
    ChangeStrategy,
    ChangeTarget,
    ComponentChangeOperation,
    ComponentType,
    DiagnosisType,
    EvidenceQuality,
    ExactComponentReference,
    OperationalDisposition,
    OperationalSignal,
    OpportunityCluster,
    ProblemDiagnosis,
)
from cx_autopilot.diagnosis import OperationalDiagnoser
from cx_autopilot.integrations import HarnessInventoryAdapter, HarnessInventoryError
from cx_autopilot.strategy import ChangePlanner, ChangePlanningError

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def ref(
    component_type: ComponentType,
    component_id: str,
    version: str = "1.0.0",
) -> ExactComponentReference:
    return ExactComponentReference(
        component_type=component_type,
        component_id=component_id,
        version=version,
        source_system="harness",
    )


def harness_ref(component_type: str, component_id: str, version: str = "1.0.0") -> object:
    return SimpleNamespace(
        component_type=component_type,
        component_id=component_id,
        version=version,
    )


def harness_agent(
    *,
    tool_refs: tuple[object, ...] = (),
    tenant_id: str = "tenant-a",
) -> object:
    return SimpleNamespace(
        identity=SimpleNamespace(agent_id="support-agent", version="1.0.0"),
        prompt_ref=harness_ref("prompt", "support-prompt"),
        skill_refs=(harness_ref("skill", "payment-skill"),),
        tool_refs=tool_refs,
        policy_refs=(harness_ref("policy", "support-policy"),),
        lifecycle="active",
        tenant_id=tenant_id,
    )


class FakeHarnessRegistry:
    def __init__(self, snapshot: object) -> None:
        self.snapshot_value = snapshot
        self.calls: list[bool] = []

    def snapshot(self, *, include_inactive: bool = False) -> object:
        self.calls.append(include_inactive)
        return self.snapshot_value


def harness_registry(
    *,
    agent_tools: tuple[object, ...] = (),
    include_transaction_tool: bool = True,
    tenant_id: str = "tenant-a",
) -> tuple[FakeHarnessRegistry, object, object]:
    agent = harness_agent(tool_refs=agent_tools, tenant_id=tenant_id)
    payment_skill = SimpleNamespace(
        skill_id="payment-skill",
        version="1.0.0",
        required_tool_refs=(harness_ref("tool", "get_payment"),),
        optional_tool_refs=(),
        lifecycle="active",
        tenant_id=tenant_id,
    )
    tools = [
        SimpleNamespace(
            tool_id="get_payment",
            version="1.0.0",
            lifecycle="active",
            tenant_id=tenant_id,
        )
    ]
    if include_transaction_tool:
        tools.append(
            SimpleNamespace(
                tool_id="get_transaction_history",
                version="1.0.0",
                lifecycle="active",
                tenant_id=tenant_id,
            )
        )
    prompt = SimpleNamespace(
        prompt_id="support-prompt",
        version="1.0.0",
        lifecycle="active",
        tenant_id=tenant_id,
    )
    policy = SimpleNamespace(
        policy_id="support-policy",
        version="1.0.0",
        lifecycle="active",
        tenant_id=tenant_id,
    )
    snapshot = SimpleNamespace(
        snapshot_id="registry-1",
        generated_at=NOW,
        agents=(agent,),
        prompts=(prompt,),
        skills=(payment_skill,),
        tools=tuple(tools),
        policies=(policy,),
    )
    manifest = SimpleNamespace(
        manifest_id="manifest-1",
        manifest_digest="digest-1",
        registry_snapshot_id="registry-1",
        tenant_id=tenant_id,
        agent=SimpleNamespace(agent_id="support-agent", version="1.0.0"),
        prompt_ref=harness_ref("prompt", "support-prompt"),
        skill_refs=(harness_ref("skill", "payment-skill"),),
        tool_refs=agent_tools,
        policy_refs=(harness_ref("policy", "support-policy"),),
    )
    return FakeHarnessRegistry(snapshot), manifest, agent


def inventory_graph(
    *,
    agent_tools: tuple[object, ...] = (),
    include_transaction_tool: bool = True,
    required_component_refs: tuple[ExactComponentReference, ...] = (),
) -> tuple[AgentSystemInventorySnapshot, FakeHarnessRegistry, object]:
    source, manifest, _ = harness_registry(
        agent_tools=agent_tools,
        include_transaction_tool=include_transaction_tool,
    )
    agent = ref(ComponentType.AGENT, "support-agent")
    inventory = HarnessInventoryAdapter(source, tenant_id="tenant-a").inspect(
        agent,
        resolved_manifest=manifest,
        required_component_refs=required_component_refs,
    )
    return inventory, source, manifest


def cluster(cluster_id: str = "cluster-1") -> OpportunityCluster:
    return OpportunityCluster(
        cluster_id=cluster_id,
        tenant_id="tenant-a",
        window_start=NOW,
        window_end=NOW,
        opportunity_ids=(f"opportunity-{cluster_id}",),
        source_signal_ids=("signal-1",),
        pattern_summary="Repeated operational work.",
        evidence_refs=("evidence:" + cluster_id, "evidence:signal-1"),
        frequency=3.0,
        confidence=0.84,
    )


def signal(
    signal_id: str = "signal-1",
    *,
    attributes: dict[str, object] | None = None,
    quality: EvidenceQuality = EvidenceQuality.COMPLETE,
    source_record_type: str = "cx_event",
) -> OperationalSignal:
    return OperationalSignal(
        signal_id=signal_id,
        source_system="cx-platform",
        source_record_type=source_record_type,
        source_record_id=signal_id,
        source_record_version="1",
        signal_type="agent.operation_failed",
        occurred_at=NOW,
        tenant_id="tenant-a",
        agent_id="support-agent",
        source_reference=f"cx-platform:{source_record_type}:{signal_id}@1",
        normalized_attributes=attributes or {"tool_id": "get_payment"},
        evidence_quality=quality,
        evidence_refs=(f"evidence:{signal_id}",),
    )


def transaction_refs() -> tuple[ExactComponentReference, ExactComponentReference]:
    return (
        ref(ComponentType.AGENT, "support-agent"),
        ref(ComponentType.TOOL, "get_transaction_history"),
    )


def diagnose(
    attributes: dict[str, object],
    *,
    inventory: AgentSystemInventorySnapshot | None = None,
    quality: EvidenceQuality = EvidenceQuality.COMPLETE,
    target_agent_ref: ExactComponentReference | None = None,
    required_prompt_ref: ExactComponentReference | None = None,
    required_skill_ref: ExactComponentReference | None = None,
    required_tool_ref: ExactComponentReference | None = None,
    source_record_type: str = "cx_event",
) -> ProblemDiagnosis:
    return OperationalDiagnoser().diagnose(
        cluster(),
        (signal(attributes=attributes, quality=quality, source_record_type=source_record_type),),
        inventory,
        target_agent_ref=target_agent_ref,
        required_prompt_ref=required_prompt_ref,
        required_skill_ref=required_skill_ref,
        required_tool_ref=required_tool_ref,
    )


def test_harness_inventory_is_exact_and_read_only() -> None:
    agent, transaction_tool = transaction_refs()
    inventory, source, manifest = inventory_graph(
        required_component_refs=(transaction_tool,),
    )

    assert source.calls == [True]
    assert inventory.snapshot_id.startswith("inventory_")
    assert inventory.registry_snapshot_ids == ("registry-1",)
    assert inventory.manifest_refs == ("manifest-1",)
    assert inventory.manifest_digests == {"manifest-1": "digest-1"}
    assert agent in inventory.agent_refs
    assert transaction_tool in inventory.tool_refs
    assert inventory.component_lifecycles[transaction_tool.identity] == "ACTIVE"
    assert inventory.agent_to_prompt_edges[0].prompt_ref == ref(
        ComponentType.PROMPT, "support-prompt"
    )
    assert inventory.agent_to_skill_edges[0].skill_ref == ref(ComponentType.SKILL, "payment-skill")
    assert not inventory.agent_to_tool_authority_edges
    assert source.calls == [True]
    assert manifest.manifest_id in inventory.manifest_refs


def test_harness_inventory_keeps_direct_authority_separate_from_skill_dependency() -> None:
    payment_tool = ref(ComponentType.TOOL, "get_payment")
    inventory, _, _ = inventory_graph(
        agent_tools=(harness_ref("tool", "get_payment"),),
    )
    assert inventory.agent_to_tool_authority_edges[0].tool_ref == payment_tool
    assert inventory.skill_to_required_tool_edges[0].tool_ref == payment_tool

    dependency_only, _, _ = inventory_graph()
    assert dependency_only.skill_to_required_tool_edges[0].tool_ref == payment_tool
    assert not dependency_only.agent_to_tool_authority_edges


def test_harness_inventory_rejects_wrong_scope_and_inexact_selection() -> None:
    source, _, _ = harness_registry(tenant_id="tenant-b")
    adapter = HarnessInventoryAdapter(source, tenant_id="tenant-a")
    with pytest.raises(HarnessInventoryError):
        adapter.inspect(ref(ComponentType.AGENT, "support-agent"))
    with pytest.raises(HarnessInventoryError):
        adapter.inspect(ref(ComponentType.TOOL, "get_payment"))  # type: ignore[arg-type]


def test_diagnosis_precedence_is_deterministic() -> None:
    inventory, _, _ = inventory_graph()
    cases = (
        (
            {"business_service_available": False},
            EvidenceQuality.COMPLETE,
            DiagnosisType.BUSINESS_DEPENDENCY,
        ),
        (
            {"business_service_available": False, "policy_denied": True},
            EvidenceQuality.COMPLETE,
            DiagnosisType.BUSINESS_DEPENDENCY,
        ),
        (
            {"policy_denied": True, "approval_status": "pending"},
            EvidenceQuality.COMPLETE,
            DiagnosisType.POLICY_CONSTRAINT,
        ),
        (
            {"approval_status": "pending", "knowledge_source_available": False},
            EvidenceQuality.COMPLETE,
            DiagnosisType.APPROVAL_FRICTION,
        ),
        (
            {"knowledge_source_available": False, "agent_gap": True},
            EvidenceQuality.COMPLETE,
            DiagnosisType.KNOWLEDGE_SOURCE_ISSUE,
        ),
    )
    for index, (attributes, quality, expected) in enumerate(cases):
        result = diagnose(attributes, inventory=inventory, quality=quality)
        assert result.diagnosis_type is expected, index
        assert result.precedence_rule

    result = diagnose(
        {"business_service_available": False},
        inventory=inventory,
        quality=EvidenceQuality.PARTIAL,
    )
    assert result.diagnosis_type is DiagnosisType.DATA_QUALITY_ISSUE
    assert result.precedence_rule == "evidence_quality"


def test_diagnoser_distinguishes_all_component_gap_types() -> None:
    inventory, _, _ = inventory_graph(
        required_component_refs=(ref(ComponentType.TOOL, "get_transaction_history"),),
    )
    agent = ref(ComponentType.AGENT, "support-agent")
    missing_agent = ref(ComponentType.AGENT, "missing-agent")
    payment_skill = ref(ComponentType.SKILL, "payment-skill")
    missing_skill = ref(ComponentType.SKILL, "missing-skill")
    payment_prompt = ref(ComponentType.PROMPT, "support-prompt")
    transaction_tool = ref(ComponentType.TOOL, "get_transaction_history")

    assert (
        diagnose(
            {"agent_gap": True},
            inventory=inventory,
            target_agent_ref=missing_agent,
        ).diagnosis_type
        is DiagnosisType.AGENT_GAP
    )
    assert (
        diagnose(
            {"skill_gap": True},
            inventory=inventory,
            target_agent_ref=agent,
            required_skill_ref=missing_skill,
        ).diagnosis_type
        is DiagnosisType.SKILL_GAP
    )
    transaction_diagnosis = diagnose(
        {"tool_id": "get_transaction_history"},
        inventory=inventory,
        target_agent_ref=agent,
        required_tool_ref=transaction_tool,
    )
    assert transaction_diagnosis.diagnosis_type is DiagnosisType.TOOL_GAP
    assert transaction_diagnosis.affected_tool_refs == (transaction_tool,)
    assert (
        diagnose(
            {"prompt_gap": True},
            inventory=inventory,
            target_agent_ref=agent,
            required_prompt_ref=payment_prompt,
        ).diagnosis_type
        is DiagnosisType.PROMPT_GAP
    )
    assert payment_skill in inventory.skill_refs


def test_diagnoser_requires_inventory_for_component_gaps_and_bounds_fallback() -> None:
    agent = ref(ComponentType.AGENT, "support-agent")
    complete_signal = signal(attributes={"unclassified": "observation"})
    no_inventory = OperationalDiagnoser().diagnose(
        cluster(),
        (complete_signal,),
        target_agent_ref=agent,
    )
    assert no_inventory.diagnosis_type is DiagnosisType.DATA_QUALITY_ISSUE
    assert no_inventory.precedence_rule == "inventory_required"

    inventory, _, _ = inventory_graph(agent_tools=(harness_ref("tool", "get_payment"),))
    prompt = ref(ComponentType.PROMPT, "support-prompt")
    fallback = OperationalDiagnoser(lambda *_: DiagnosisType.PROMPT_GAP)
    result = fallback.diagnose(
        cluster(),
        (complete_signal,),
        inventory,
        target_agent_ref=agent,
        required_prompt_ref=prompt,
    )
    assert result.diagnosis_type is DiagnosisType.PROMPT_GAP
    assert result.precedence_rule == "validated_model_fallback"

    invalid_fallback = OperationalDiagnoser(lambda *_: "not-a-taxonomy-value")
    with pytest.raises(ValueError):
        invalid_fallback.diagnose(
            cluster("cluster-invalid-fallback"),
            (signal("signal-invalid-fallback", attributes={"unclassified": "observation"}),),
            inventory,
            target_agent_ref=agent,
            required_prompt_ref=prompt,
        )


@pytest.mark.parametrize(
    ("attributes", "record_type", "expected"),
    (
        (
            {"business_service_available": False},
            "cx_event",
            DiagnosisType.BUSINESS_DEPENDENCY,
        ),
        ({"policy_denied": True}, "cx_event", DiagnosisType.POLICY_CONSTRAINT),
        ({"approval_status": "pending"}, "approval", DiagnosisType.APPROVAL_FRICTION),
        (
            {"knowledge_source_available": False},
            "cx_event",
            DiagnosisType.KNOWLEDGE_SOURCE_ISSUE,
        ),
    ),
)
def test_external_and_governance_diagnoses_do_not_need_inventory(
    attributes: dict[str, object],
    record_type: str,
    expected: DiagnosisType,
) -> None:
    result = diagnose(attributes, source_record_type=record_type)
    assert result.diagnosis_type is expected
    assert result.inventory_snapshot_id is None


def test_change_selection_returns_correct_target_and_every_strategy() -> None:
    agent, transaction_tool = transaction_refs()
    inventory, _, _ = inventory_graph(required_component_refs=(transaction_tool,))
    planner = ChangePlanner()
    transaction_diagnosis = diagnose(
        {"tool_id": "get_transaction_history"},
        inventory=inventory,
        target_agent_ref=agent,
        required_tool_ref=transaction_tool,
    )
    assert planner.select(
        transaction_diagnosis,
        inventory,
        target_agent_ref=agent,
        required_tool_ref=transaction_tool,
    ) == (ChangeTarget.TOOL, ChangeStrategy.EXTEND)

    authority_inventory, _, _ = inventory_graph(
        agent_tools=(harness_ref("tool", "get_payment"),),
        required_component_refs=(ref(ComponentType.TOOL, "get_payment"),),
    )
    satisfied = ProblemDiagnosis(
        diagnosis_id="diagnosis-reuse",
        tenant_id="tenant-a",
        cluster_id="cluster-reuse",
        inventory_snapshot_id=authority_inventory.snapshot_id,
        diagnosis_type=DiagnosisType.TOOL_GAP,
        summary="A tool authority was rechecked.",
        supporting_evidence_refs=("evidence:reuse",),
        confidence=0.9,
        affected_agent_refs=(agent,),
        affected_tool_refs=(ref(ComponentType.TOOL, "get_payment"),),
        created_at=NOW,
    )
    assert planner.select(satisfied, authority_inventory) == (
        ChangeTarget.TOOL,
        ChangeStrategy.REUSE,
    )

    composition_diagnosis = diagnose(
        {"agent_composition_missing": True},
        inventory=inventory,
        target_agent_ref=agent,
    )
    assert planner.select(composition_diagnosis, inventory) == (
        ChangeTarget.AGENT,
        ChangeStrategy.COMPOSE,
    )

    missing_agent_diagnosis = diagnose(
        {"agent_gap": True},
        inventory=inventory,
        target_agent_ref=ref(ComponentType.AGENT, "new-agent"),
    )
    assert planner.select(missing_agent_diagnosis, inventory) == (
        ChangeTarget.AGENT,
        ChangeStrategy.CREATE,
    )

    missing_tool_diagnosis = diagnose(
        {"tool_id": "new_tool"},
        inventory=inventory,
        target_agent_ref=agent,
        required_tool_ref=ref(ComponentType.TOOL, "new_tool"),
    )
    assert planner.select(missing_tool_diagnosis, inventory) == (
        ChangeTarget.TOOL,
        ChangeStrategy.CREATE,
    )

    skill_diagnosis = diagnose(
        {"skill_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_skill_ref=ref(ComponentType.SKILL, "payment-skill"),
        required_tool_ref=transaction_tool,
    )
    assert planner.select(skill_diagnosis, inventory, required_tool_ref=transaction_tool) == (
        ChangeTarget.SKILL,
        ChangeStrategy.EXTEND,
    )

    prompt_diagnosis = diagnose(
        {"prompt_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_prompt_ref=ref(ComponentType.PROMPT, "support-prompt"),
    )
    assert planner.select(prompt_diagnosis, inventory) == (
        ChangeTarget.PROMPT,
        ChangeStrategy.EXTEND,
    )
    no_change = diagnose({"business_service_available": False})
    assert planner.select(no_change) == (ChangeTarget.NO_CHANGE, ChangeStrategy.NO_CHANGE)


def test_transaction_history_produces_one_exact_tool_authority_operation() -> None:
    agent, transaction_tool = transaction_refs()
    inventory, _, _ = inventory_graph(required_component_refs=(transaction_tool,))
    diagnosis = diagnose(
        {"tool_id": "get_transaction_history"},
        inventory=inventory,
        target_agent_ref=agent,
        required_tool_ref=transaction_tool,
    )
    updated_agent = ref(ComponentType.AGENT, "support-agent", "1.1.0")
    proposal = ChangePlanner().plan(
        diagnosis,
        inventory,
        target_agent_after_ref=updated_agent,
        required_tool_ref=transaction_tool,
    )

    assert proposal.change_target is ChangeTarget.TOOL  # type: ignore[union-attr]
    assert proposal.strategy is ChangeStrategy.EXTEND  # type: ignore[union-attr]
    assert len(proposal.proposed_component_changes) == 1  # type: ignore[union-attr]
    change = proposal.proposed_component_changes[0]  # type: ignore[union-attr]
    assert change.operation is ComponentChangeOperation.ADD_AGENT_TOOL_REF
    assert change.subject_before_ref == agent
    assert change.subject_after_ref == updated_agent
    assert change.related_after_ref == transaction_tool
    assert proposal.target_agent_refs == (agent, updated_agent)  # type: ignore[union-attr]
    assert proposal.requires_human_review is True  # type: ignore[union-attr]

    payload = proposal.model_dump(mode="python")  # type: ignore[union-attr]
    payload["requires_human_review"] = False
    with pytest.raises(ValidationError):
        ChangeProposal.model_validate(payload)

    with pytest.raises(ChangePlanningError):
        ChangePlanner().plan(diagnosis, inventory, required_tool_ref=transaction_tool)


def test_skill_dependency_change_updates_agent_graph_without_tool_authority() -> None:
    inventory, _, _ = inventory_graph()
    agent = ref(ComponentType.AGENT, "support-agent")
    skill = ref(ComponentType.SKILL, "payment-skill")
    transaction_tool = ref(ComponentType.TOOL, "get_transaction_history")
    diagnosis = diagnose(
        {"skill_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_skill_ref=skill,
        required_tool_ref=transaction_tool,
    )
    updated_skill = ref(ComponentType.SKILL, "payment-skill", "1.1.0")
    proposal = ChangePlanner().plan(
        diagnosis,
        inventory,
        target_agent_after_ref=ref(ComponentType.AGENT, "support-agent", "1.1.0"),
        required_skill_ref=skill,
        skill_after_ref=updated_skill,
        required_tool_ref=transaction_tool,
    )
    assert proposal.change_target is ChangeTarget.SKILL  # type: ignore[union-attr]
    assert proposal.strategy is ChangeStrategy.EXTEND  # type: ignore[union-attr]
    assert [
        change.operation
        for change in proposal.proposed_component_changes  # type: ignore[union-attr]
    ] == [
        ComponentChangeOperation.ADD_SKILL_REQUIRED_TOOL_REF,
        ComponentChangeOperation.REMOVE_AGENT_SKILL_REF,
        ComponentChangeOperation.ADD_AGENT_SKILL_REF,
    ]
    dependency_change = proposal.proposed_component_changes[0]  # type: ignore[union-attr]
    assert dependency_change.subject_before_ref == skill
    assert dependency_change.subject_after_ref == updated_skill
    assert dependency_change.related_after_ref == transaction_tool
    remove_change = proposal.proposed_component_changes[1]  # type: ignore[union-attr]
    add_change = proposal.proposed_component_changes[2]  # type: ignore[union-attr]
    updated_agent = ref(ComponentType.AGENT, "support-agent", "1.1.0")
    assert remove_change.subject_before_ref == agent
    assert remove_change.subject_after_ref == updated_agent
    assert remove_change.related_before_ref == skill
    assert add_change.subject_before_ref == agent
    assert add_change.subject_after_ref == updated_agent
    assert add_change.related_after_ref == updated_skill
    assert not any(
        change.operation
        in {
            ComponentChangeOperation.ADD_AGENT_TOOL_REF,
            ComponentChangeOperation.REMOVE_AGENT_TOOL_REF,
        }
        for change in proposal.proposed_component_changes  # type: ignore[union-attr]
    )


def test_prompt_change_versions_the_agent_with_the_new_prompt_reference() -> None:
    inventory, _, _ = inventory_graph(agent_tools=(harness_ref("tool", "get_payment"),))
    agent = ref(ComponentType.AGENT, "support-agent")
    prompt = ref(ComponentType.PROMPT, "support-prompt")
    updated_agent = ref(ComponentType.AGENT, "support-agent", "1.1.0")
    updated_prompt = ref(ComponentType.PROMPT, "support-prompt", "1.1.0")
    diagnosis = diagnose(
        {"prompt_gap": True},
        inventory=inventory,
        target_agent_ref=agent,
        required_prompt_ref=prompt,
    )

    proposal = ChangePlanner().plan(
        diagnosis,
        inventory,
        target_agent_after_ref=updated_agent,
        required_prompt_ref=prompt,
        prompt_after_ref=updated_prompt,
    )

    assert proposal.change_target is ChangeTarget.PROMPT  # type: ignore[union-attr]
    assert proposal.strategy is ChangeStrategy.EXTEND  # type: ignore[union-attr]
    assert len(proposal.proposed_component_changes) == 1  # type: ignore[union-attr]
    change = proposal.proposed_component_changes[0]  # type: ignore[union-attr]
    assert change.operation is ComponentChangeOperation.CHANGE_AGENT_PROMPT_REF
    assert change.subject_before_ref == prompt
    assert change.subject_after_ref == updated_prompt
    assert change.related_before_ref == agent
    assert change.related_after_ref == updated_agent
    assert proposal.target_agent_refs == (agent, updated_agent)  # type: ignore[union-attr]


def test_create_tool_path_declares_creation_and_authority_separately() -> None:
    inventory, _, _ = inventory_graph(include_transaction_tool=False)
    agent = ref(ComponentType.AGENT, "support-agent")
    missing_tool = ref(ComponentType.TOOL, "new_tool")
    diagnosis = diagnose(
        {"tool_id": "new_tool"},
        inventory=inventory,
        target_agent_ref=agent,
        required_tool_ref=missing_tool,
    )
    proposal = ChangePlanner().plan(
        diagnosis,
        inventory,
        target_agent_after_ref=ref(ComponentType.AGENT, "support-agent", "1.1.0"),
        tool_after_ref=missing_tool,
        required_tool_ref=missing_tool,
    )
    assert proposal.strategy is ChangeStrategy.CREATE  # type: ignore[union-attr]
    assert [
        change.operation
        for change in proposal.proposed_component_changes  # type: ignore[union-attr]
    ] == [
        ComponentChangeOperation.CREATE_TOOL,
        ComponentChangeOperation.ADD_AGENT_TOOL_REF,
    ]


def test_no_change_returns_operational_disposition_and_not_a_proposal() -> None:
    diagnosis = diagnose({"policy_denied": True})
    result = ChangePlanner().plan(diagnosis)
    assert isinstance(result, OperationalDisposition)
    assert result.strategy is ChangeStrategy.NO_CHANGE
    assert result.owner_boundary == "harness-governance-owner"
    assert "bypass" in result.recommended_action
