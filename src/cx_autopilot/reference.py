"""Deterministic transaction-history reference cycle for acceptance and CLI use."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from .clustering import OpportunityClusterer
from .contracts import (
    AgentSystemInventorySnapshot,
    CandidateReference,
    ChangeProposal,
    ChangeStrategy,
    ChangeTarget,
    ComponentChangeOperation,
    ComponentType,
    DecisionRecord,
    DiagnosisType,
    ExactComponentReference,
    Opportunity,
    OpportunityCluster,
    PilotRecommendation,
    ProblemDiagnosis,
)
from .contracts.common import aware_timestamp, non_blank
from .decisions import DecisionAuditTrail, DecisionService
from .diagnosis import OperationalDiagnoser
from .integrations.cx_platform import CXPlatformEvidenceAdapter, EvidenceIngestionResult
from .integrations.harness import HarnessInventoryAdapter
from .integrations.harness_candidate import HarnessCandidateAdapter, HarnessCandidateBuild
from .integrations.improvement_lab import (
    EVALUATION_SUCCEEDED,
    ImprovementLabEvaluationAdapter,
    LabEvaluationResult,
)
from .opportunities import OpportunityDiscoverer
from .recommendations import PilotRecommender
from .storage.sqlite import SQLiteStore
from .strategy import ChangePlanner

REFERENCE_TENANT_ID = "tenant-a"
REFERENCE_NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
REFERENCE_AGENT = ExactComponentReference(
    component_type=ComponentType.AGENT,
    component_id="support-agent",
    version="1.0.0",
    source_system="harness",
)
REFERENCE_AGENT_AFTER = REFERENCE_AGENT.model_copy(update={"version": "1.1.0"})
REFERENCE_PROMPT = ExactComponentReference(
    component_type=ComponentType.PROMPT,
    component_id="support-prompt",
    version="1.0.0",
    source_system="harness",
)
REFERENCE_SKILL = ExactComponentReference(
    component_type=ComponentType.SKILL,
    component_id="payment-skill",
    version="1.0.0",
    source_system="harness",
)
REFERENCE_PAYMENT_TOOL = ExactComponentReference(
    component_type=ComponentType.TOOL,
    component_id="get_payment",
    version="1.0.0",
    source_system="harness",
)
REFERENCE_TRANSACTION_TOOL = ExactComponentReference(
    component_type=ComponentType.TOOL,
    component_id="get_transaction_history",
    version="1.0.0",
    source_system="harness",
)
REFERENCE_POLICY = ExactComponentReference(
    component_type=ComponentType.POLICY,
    component_id="support-policy",
    version="1.0.0",
    source_system="harness",
)
REFERENCE_BASELINE_MANIFEST_ID = "manifest-support-agent-1.0.0"
REFERENCE_BASELINE_MANIFEST_DIGEST = "digest-support-agent-1.0.0"
REFERENCE_BASELINE_REGISTRY_ID = "registry-reference-production"
REFERENCE_CANDIDATE_ID = "candidate-transaction-history-reference"
REFERENCE_BASELINE_CANDIDATE_ID = "candidate-transaction-history-baseline"
REFERENCE_CANDIDATE_MANIFEST_ID = "manifest-support-agent-1.1.0"
REFERENCE_CANDIDATE_MANIFEST_DIGEST = "digest-support-agent-1.1.0"
REFERENCE_CANDIDATE_REGISTRY_ID = "registry-reference-evaluation"
REFERENCE_COMPARISON_ID = "comparison-transaction-history-reference"


class ReferenceCycleError(RuntimeError):
    """Raised when the deterministic acceptance fixture cannot complete."""


class ReferenceCXPlatformSource:
    """Small read-only CX Platform fixture with three repeated lookup events."""

    def __init__(self) -> None:
        events: list[Mapping[str, Any]] = []
        tickets: list[Mapping[str, Any]] = []
        for number in range(1, 4):
            ticket_id = f"ticket-transaction-{number}"
            conversation_id = f"conversation-transaction-{number}"
            execution_id = f"execution-transaction-{number}"
            occurred_at = (REFERENCE_NOW + timedelta(hours=number)).isoformat()
            events.append(
                {
                    "event_id": f"event-transaction-{number}",
                    "event_type": "agent.tool_failed",
                    "occurred_at": occurred_at,
                    "customer_id": f"customer-{number}",
                    "ticket_id": ticket_id,
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                    "actor_type": "AI_AGENT",
                    "actor_id": REFERENCE_AGENT.component_id,
                    "data": {
                        "tool_id": REFERENCE_TRANSACTION_TOOL.component_id,
                        "tool_version": REFERENCE_TRANSACTION_TOOL.version,
                        "result_status": "FAILED",
                    },
                }
            )
            tickets.append(
                {
                    "ticket_id": ticket_id,
                    "customer_id": f"customer-{number}",
                    "conversation_id": conversation_id,
                    "status": "OPEN",
                    "reason": "Transaction history",
                    "priority": "NORMAL",
                    "created_at": REFERENCE_NOW.isoformat(),
                }
            )
        self.events = tuple(events)
        self.tickets = tuple(tickets)
        self.read_calls: list[str] = []

    def list_events(self, *, after: str | None, limit: int) -> tuple[Mapping[str, Any], ...]:
        del limit
        return self.events if after is None else ()

    def list_tickets(self, *, after: str | None, limit: int) -> tuple[Mapping[str, Any], ...]:
        del limit
        return self.tickets if after is None else ()

    def list_outcomes(self, *, after: str | None, limit: int) -> tuple[Mapping[str, Any], ...]:
        del after, limit
        return ()

    def read_ticket(self, ticket_id: str) -> Mapping[str, Any] | None:
        self.read_calls.append(f"ticket:{ticket_id}")
        ticket = next((item for item in self.tickets if item["ticket_id"] == ticket_id), None)
        if ticket is None:
            return None
        conversation_id = str(ticket["conversation_id"])
        return {
            "ticket": ticket,
            "conversation": {
                "conversation_id": conversation_id,
                "ticket_id": ticket_id,
                "customer_id": ticket["customer_id"],
                "status": "ENDED",
                "started_at": REFERENCE_NOW.isoformat(),
                "ended_at": (REFERENCE_NOW + timedelta(minutes=5)).isoformat(),
            },
            "messages": [],
            "escalations": [],
            "approvals": [],
            "outcomes": [],
            "csat": [],
        }

    def read_conversation(self, conversation_id: str) -> Mapping[str, Any] | None:
        self.read_calls.append(f"conversation:{conversation_id}")
        return None

    def read_execution(self, execution_id: str) -> Mapping[str, Any] | None:
        self.read_calls.append(f"execution:{execution_id}")
        number = execution_id.rsplit("-", 1)[-1]
        return {
            "execution_id": execution_id,
            "ticket_id": f"ticket-transaction-{number}",
            "conversation_id": f"conversation-transaction-{number}",
            "agent_id": REFERENCE_AGENT.component_id,
            "agent_version": REFERENCE_AGENT.version,
            "trace_reference": f"harness-trace-transaction-{number}",
            "started_at": REFERENCE_NOW.isoformat(),
            "completed_at": (REFERENCE_NOW + timedelta(minutes=1)).isoformat(),
            "outcome_status": "FAILED",
        }


class ReferenceHarnessInventorySource:
    """Read-only Harness registry fixture for the baseline support Agent."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = non_blank(tenant_id, "tenant_id")
        self.snapshot_calls = 0
        self.snapshot_value = _baseline_registry_snapshot(self.tenant_id)

    def snapshot(self, *, include_inactive: bool = False) -> object:
        del include_inactive
        self.snapshot_calls += 1
        return self.snapshot_value


class ReferenceEvaluationRegistry:
    """Mutable evaluation-only registry used by the local Harness factory fake."""

    def __init__(self) -> None:
        self.registered_agent_identities: list[str] = []
        self.activated_agent_identities: list[str] = []

    def register(self, config: object) -> None:
        self.registered_agent_identities.append(_config_agent_identity(config))

    def activate(self, agent_id: str, version: str) -> None:
        self.activated_agent_identities.append(f"AGENT:{agent_id}@{version}")


class ReferenceProductionRegistry:
    """Production authority snapshot that candidate construction must not touch."""

    def __init__(self) -> None:
        self.deployment_calls = 0
        self._authority = (f"{REFERENCE_AGENT.identity}->{REFERENCE_PAYMENT_TOOL.identity}",)

    def authority_snapshot(self) -> tuple[str, ...]:
        return self._authority


class ReferenceHarnessFactory:
    """Deterministic factory fake at the public Harness construction boundary."""

    def __init__(self, registry: ReferenceEvaluationRegistry) -> None:
        self.agent_registry = registry
        self.calls: list[dict[str, object]] = []

    def build(
        self,
        config: object,
        *,
        dry_run: bool = False,
        activate: bool = True,
        register: bool = True,
    ) -> object:
        self.calls.append(
            {
                "dry_run": dry_run,
                "activate": activate,
                "register": register,
            }
        )
        if not isinstance(config, Mapping):
            raise TypeError("reference Harness factory requires a mapping config")
        if register:
            self.agent_registry.register(config)
        identity = _mapping(config.get("identity"))
        agent_id = _required_text(identity.get("agent_id"), "agent_id")
        version = _required_text(identity.get("version"), "version")
        if activate:
            self.agent_registry.activate(agent_id, version)
        prompt_ref = config.get("prompt_ref")
        skill_refs = _sequence(config.get("skill_refs"))
        tool_refs = _sequence(config.get("tool_refs"))
        policy_refs = _sequence(config.get("policy_refs"))
        agent = SimpleNamespace(
            identity=SimpleNamespace(agent_id=agent_id, version=version),
            prompt_ref=prompt_ref,
            skill_refs=skill_refs,
            tool_refs=tool_refs,
            policy_refs=policy_refs,
        )
        manifest = SimpleNamespace(
            manifest_id=REFERENCE_CANDIDATE_MANIFEST_ID,
            manifest_digest=REFERENCE_CANDIDATE_MANIFEST_DIGEST,
            registry_snapshot_id=REFERENCE_CANDIDATE_REGISTRY_ID,
            agent=agent,
            prompt_ref=prompt_ref,
            skill_refs=skill_refs,
            tool_refs=tool_refs,
            policy_refs=policy_refs,
        )
        return SimpleNamespace(manifest=manifest)


class ReferenceLabRunner:
    """Deterministic Lab runner fake; evaluator semantics remain Lab-owned."""

    def __init__(self) -> None:
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
        candidate_id = _required_text(getattr(candidate, "candidate_id", None), "candidate_id")
        self.calls.append(candidate_id)
        return SimpleNamespace(
            report=SimpleNamespace(
                run_id=f"run:{candidate_id}",
                environment_snapshot_id="environment:transaction-history:1",
                evidence_refs=(f"lab:run:{candidate_id}",),
            )
        )


class ReferenceLabComparator:
    """Deterministic comparator fake at the public Lab comparison boundary."""

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
        return SimpleNamespace(
            comparison_id=REFERENCE_COMPARISON_ID,
            verdict="improved",
            evidence_refs=(f"lab:comparison:{REFERENCE_COMPARISON_ID}",),
        )


@dataclass(frozen=True)
class ReferenceCycleResult:
    """All reviewable records produced by the reference acceptance cycle."""

    ingestion: EvidenceIngestionResult
    duplicate_ingestion: EvidenceIngestionResult
    opportunities: tuple[Opportunity, ...]
    clusters: tuple[OpportunityCluster, ...]
    inventory: AgentSystemInventorySnapshot
    diagnosis: ProblemDiagnosis
    proposal: ChangeProposal
    candidate: HarnessCandidateBuild
    evaluation: LabEvaluationResult
    recommendation: PilotRecommendation
    decision: DecisionRecord
    audit: DecisionAuditTrail
    production_authority_before: tuple[str, ...]
    production_authority_after: tuple[str, ...]
    inventory_snapshot_calls: int
    harness_build_calls: int
    lab_run_candidate_ids: tuple[str, ...]
    lab_comparison_calls: int


def build_reference_cx_source() -> ReferenceCXPlatformSource:
    """Return a fresh deterministic CX Platform fixture source."""

    return ReferenceCXPlatformSource()


def run_reference_cycle(
    store: SQLiteStore,
    *,
    tenant_id: str = REFERENCE_TENANT_ID,
    created_at: datetime = REFERENCE_NOW,
) -> ReferenceCycleResult:
    """Run the complete transaction-history path through human decision."""

    tenant = non_blank(tenant_id, "tenant_id")
    at = aware_timestamp(created_at, "created_at")

    source = build_reference_cx_source()
    evidence_adapter = CXPlatformEvidenceAdapter(source, tenant_id=tenant)
    ingestion = evidence_adapter.ingest(store.signals, as_of=at + timedelta(days=1))
    duplicate_ingestion = evidence_adapter.ingest(store.signals, as_of=at + timedelta(days=1))
    if not ingestion.inserted_signal_ids or not duplicate_ingestion.duplicate_signal_ids:
        raise ReferenceCycleError("reference ingestion did not prove idempotency")

    opportunities = OpportunityDiscoverer().discover(ingestion.signals, tenant_id=tenant)
    lookup_opportunities = tuple(
        opportunity
        for opportunity in opportunities
        if opportunity.pattern_key == "operation:get_transaction_history"
    )
    if len(opportunities) != 1 or len(lookup_opportunities) != 1:
        raise ReferenceCycleError("reference fixture did not produce one lookup opportunity")
    for opportunity in opportunities:
        store.opportunities.insert(opportunity)

    clusters = OpportunityClusterer().cluster(opportunities, tenant_id=tenant)
    if len(clusters) != 1:
        raise ReferenceCycleError("reference fixture did not produce one opportunity cluster")
    for cluster in clusters:
        store.opportunity_clusters.insert(cluster)

    baseline_manifest = _baseline_manifest(tenant)
    inventory_source = ReferenceHarnessInventorySource(tenant)
    inventory = HarnessInventoryAdapter(inventory_source, tenant_id=tenant).inspect(
        REFERENCE_AGENT,
        resolved_manifest=baseline_manifest,
        required_component_refs=(REFERENCE_TRANSACTION_TOOL,),
        captured_at=at,
        snapshot_id="inventory-transaction-history-reference",
    )
    store.inventory.insert(inventory)
    lookup_opportunity = lookup_opportunities[0]
    cluster = clusters[0]
    diagnosis = OperationalDiagnoser(
        cluster_store=store.opportunity_clusters,
        opportunity_store=store.opportunities,
        signal_store=store.signals,
    ).diagnose_cluster(
        cluster.cluster_id,
        tenant,
        inventory,
        target_agent_ref=REFERENCE_AGENT,
        required_tool_ref=REFERENCE_TRANSACTION_TOOL,
    )
    if diagnosis.diagnosis_type is not DiagnosisType.TOOL_GAP:
        raise ReferenceCycleError("reference fixture did not produce TOOL_GAP")
    store.diagnoses.insert(diagnosis)

    proposal_value = ChangePlanner().plan(
        diagnosis,
        inventory,
        opportunity_id=lookup_opportunity.opportunity_id,
        target_agent_ref=REFERENCE_AGENT,
        target_agent_after_ref=REFERENCE_AGENT_AFTER,
        required_tool_ref=REFERENCE_TRANSACTION_TOOL,
        created_at=at,
    )
    if not isinstance(proposal_value, ChangeProposal):
        raise ReferenceCycleError("reference TOOL_GAP did not produce a ChangeProposal")
    proposal = proposal_value
    if proposal.change_target is not ChangeTarget.TOOL:
        raise ReferenceCycleError("reference proposal target is not TOOL")
    if proposal.strategy is not ChangeStrategy.EXTEND:
        raise ReferenceCycleError("reference proposal strategy is not EXTEND")
    if len(proposal.proposed_component_changes) != 1:
        raise ReferenceCycleError("reference proposal is not minimal")
    if (
        proposal.proposed_component_changes[0].operation
        is not ComponentChangeOperation.ADD_AGENT_TOOL_REF
    ):
        raise ReferenceCycleError("reference proposal does not add direct Tool authority")
    store.proposals.insert(proposal)

    production_registry = ReferenceProductionRegistry()
    production_before = production_registry.authority_snapshot()
    evaluation_registry = ReferenceEvaluationRegistry()
    harness_factory = ReferenceHarnessFactory(evaluation_registry)
    candidate_build = HarnessCandidateAdapter(
        harness_factory,
        evaluation_registry=evaluation_registry,
        production_registry=production_registry,
        candidate_store=store.candidates,
    ).construct(
        proposal,
        inventory,
        _baseline_agent_config(),
        candidate_id=REFERENCE_CANDIDATE_ID,
    )
    production_after = production_registry.authority_snapshot()
    if production_before != production_after or production_registry.deployment_calls != 0:
        raise ReferenceCycleError("candidate construction changed production authority")

    baseline_candidate = _baseline_candidate_reference(tenant, baseline_manifest)
    baseline_lab_candidate = SimpleNamespace(candidate_id=baseline_candidate.candidate_id)
    candidate_lab_candidate = SimpleNamespace(
        candidate_id=candidate_build.candidate_reference.candidate_id,
        harness_agent=candidate_build.built_agent,
    )
    lab_runner = ReferenceLabRunner()
    lab_comparator = ReferenceLabComparator()
    evaluation = ImprovementLabEvaluationAdapter(
        lab_runner,
        lab_comparator,
        evaluation_store=store.evaluations,
    ).evaluate(
        baseline_lab_candidate,
        candidate_lab_candidate,
        baseline_reference=baseline_candidate,
        candidate_reference=candidate_build.candidate_reference,
        dataset=SimpleNamespace(
            dataset_id="transaction-history-reference-cases",
            version="1.0.0",
        ),
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_build.manifest,
        case_data_refs=("cx-platform:cases:transaction-history@1.0.0",),
        operational_evidence_refs=lookup_opportunity.evidence_refs,
        baseline_snapshot=SimpleNamespace(snapshot_id=REFERENCE_BASELINE_REGISTRY_ID),
        candidate_snapshot=SimpleNamespace(snapshot_id=REFERENCE_CANDIDATE_REGISTRY_ID),
    )
    if evaluation.evaluation_reference.status != EVALUATION_SUCCEEDED:
        raise ReferenceCycleError("reference Lab evaluation did not succeed")

    recommendation = PilotRecommender(store.recommendations).recommend(
        proposal=proposal,
        diagnosis=diagnosis,
        inventory=inventory,
        candidate=candidate_build.candidate_reference,
        evaluation=evaluation,
        comparison=evaluation.comparison,
        summary="Review a bounded transaction-history authority pilot.",
        expected_operational_impact=(
            "Reduce repeated manual transaction-history lookup work observed in CX evidence."
        ),
        known_risks=("The new read authority remains subject to the existing policy.",),
        pilot_scope={
            "agent_ref": candidate_build.candidate_reference.agent_ref.identity,
            "traffic_percentage": 5,
            "duration_seconds": 3600,
        },
        success_criteria=(
            "Transaction-history lookup completion improves without a policy regression.",
        ),
        rollback_conditions=(
            "Abort on a tenant-boundary, authorization, or reliability regression.",
        ),
        opportunity=lookup_opportunity,
        risk_evidence_refs=("risk:existing-policy-boundary",),
        created_at=at,
    )

    decision = DecisionService(store).record_pilot_decision(
        recommendation,
        "APPROVE",
        "human:reference-reviewer",
        "Approve the bounded pilot for human-controlled execution.",
        occurred_at=at + timedelta(minutes=1),
    )
    audit = DecisionService(store).audit(decision.decision_id, tenant_id=tenant)
    return ReferenceCycleResult(
        ingestion=ingestion,
        duplicate_ingestion=duplicate_ingestion,
        opportunities=opportunities,
        clusters=clusters,
        inventory=inventory,
        diagnosis=diagnosis,
        proposal=proposal,
        candidate=candidate_build,
        evaluation=evaluation,
        recommendation=recommendation,
        decision=decision,
        audit=audit,
        production_authority_before=production_before,
        production_authority_after=production_after,
        inventory_snapshot_calls=inventory_source.snapshot_calls,
        harness_build_calls=len(harness_factory.calls),
        lab_run_candidate_ids=tuple(lab_runner.calls),
        lab_comparison_calls=len(lab_comparator.calls),
    )


def _baseline_agent_config() -> dict[str, object]:
    return {
        "identity": {
            "agent_id": REFERENCE_AGENT.component_id,
            "version": REFERENCE_AGENT.version,
        },
        "goal": "Support payment customers.",
        "prompt_ref": _harness_ref(REFERENCE_PROMPT),
        "skill_refs": [_harness_ref(REFERENCE_SKILL)],
        "tool_refs": [_harness_ref(REFERENCE_PAYMENT_TOOL)],
        "policy_refs": [_harness_ref(REFERENCE_POLICY)],
    }


def _baseline_manifest(tenant_id: str) -> object:
    agent = SimpleNamespace(
        identity=SimpleNamespace(
            agent_id=REFERENCE_AGENT.component_id,
            version=REFERENCE_AGENT.version,
        ),
        prompt_ref=_harness_ref(REFERENCE_PROMPT),
        skill_refs=(_harness_ref(REFERENCE_SKILL),),
        tool_refs=(_harness_ref(REFERENCE_PAYMENT_TOOL),),
        policy_refs=(_harness_ref(REFERENCE_POLICY),),
    )
    return SimpleNamespace(
        manifest_id=REFERENCE_BASELINE_MANIFEST_ID,
        manifest_digest=REFERENCE_BASELINE_MANIFEST_DIGEST,
        registry_snapshot_id=REFERENCE_BASELINE_REGISTRY_ID,
        tenant_id=tenant_id,
        agent=agent,
        prompt_ref=_harness_ref(REFERENCE_PROMPT),
        skill_refs=(_harness_ref(REFERENCE_SKILL),),
        tool_refs=(_harness_ref(REFERENCE_PAYMENT_TOOL),),
        policy_refs=(_harness_ref(REFERENCE_POLICY),),
    )


def _baseline_registry_snapshot(tenant_id: str) -> object:
    return SimpleNamespace(
        snapshot_id=REFERENCE_BASELINE_REGISTRY_ID,
        generated_at=REFERENCE_NOW,
        tenant_id=tenant_id,
        agents=(
            SimpleNamespace(
                identity=SimpleNamespace(
                    agent_id=REFERENCE_AGENT.component_id,
                    version=REFERENCE_AGENT.version,
                ),
                prompt_ref=_harness_ref(REFERENCE_PROMPT),
                skill_refs=(_harness_ref(REFERENCE_SKILL),),
                tool_refs=(_harness_ref(REFERENCE_PAYMENT_TOOL),),
                policy_refs=(_harness_ref(REFERENCE_POLICY),),
                lifecycle="active",
                tenant_id=tenant_id,
            ),
        ),
        prompts=(
            SimpleNamespace(
                prompt_id=REFERENCE_PROMPT.component_id,
                version=REFERENCE_PROMPT.version,
                lifecycle="active",
                tenant_id=tenant_id,
            ),
        ),
        skills=(
            SimpleNamespace(
                skill_id=REFERENCE_SKILL.component_id,
                version=REFERENCE_SKILL.version,
                required_tool_refs=(_harness_ref(REFERENCE_PAYMENT_TOOL),),
                optional_tool_refs=(),
                lifecycle="active",
                tenant_id=tenant_id,
            ),
        ),
        tools=(
            SimpleNamespace(
                tool_id=REFERENCE_PAYMENT_TOOL.component_id,
                version=REFERENCE_PAYMENT_TOOL.version,
                lifecycle="active",
                tenant_id=tenant_id,
            ),
            SimpleNamespace(
                tool_id=REFERENCE_TRANSACTION_TOOL.component_id,
                version=REFERENCE_TRANSACTION_TOOL.version,
                lifecycle="active",
                tenant_id=tenant_id,
            ),
        ),
        policies=(
            SimpleNamespace(
                policy_id=REFERENCE_POLICY.component_id,
                version=REFERENCE_POLICY.version,
                lifecycle="active",
                tenant_id=tenant_id,
            ),
        ),
    )


def _baseline_candidate_reference(tenant_id: str, manifest: object) -> CandidateReference:
    return CandidateReference(
        candidate_id=REFERENCE_BASELINE_CANDIDATE_ID,
        tenant_id=tenant_id,
        agent_ref=REFERENCE_AGENT,
        manifest_id=_required_text(getattr(manifest, "manifest_id", None), "manifest_id"),
        manifest_digest=_required_text(
            getattr(manifest, "manifest_digest", None), "manifest_digest"
        ),
        registry_snapshot_id=_required_text(
            getattr(manifest, "registry_snapshot_id", None), "registry_snapshot_id"
        ),
        prompt_ref=REFERENCE_PROMPT,
        skill_refs=(REFERENCE_SKILL,),
        tool_refs=(REFERENCE_PAYMENT_TOOL,),
        policy_refs=(REFERENCE_POLICY,),
    )


def _harness_ref(reference: ExactComponentReference) -> dict[str, str]:
    return {
        "component_type": reference.component_type.value.lower(),
        "component_id": reference.component_id,
        "version": reference.version,
    }


def _config_agent_identity(config: object) -> str:
    payload = _mapping(config)
    identity = _mapping(payload.get("identity"))
    return (
        "AGENT:"
        + _required_text(identity.get("agent_id"), "agent_id")
        + "@"
        + _required_text(identity.get("version"), "version")
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("reference value must be a mapping")
    return value


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError("reference value must be a sequence")
    return tuple(value)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    return non_blank(value, field_name)


__all__ = [
    "REFERENCE_AGENT",
    "REFERENCE_AGENT_AFTER",
    "REFERENCE_NOW",
    "REFERENCE_TENANT_ID",
    "REFERENCE_TRANSACTION_TOOL",
    "ReferenceCycleError",
    "ReferenceCycleResult",
    "build_reference_cx_source",
    "run_reference_cycle",
]
