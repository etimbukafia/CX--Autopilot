from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cx_autopilot.clustering import OpportunityClusterer, OpportunityClusteringConfig
from cx_autopilot.contracts import (
    EvidenceQuality,
    OperationalSignal,
    Opportunity,
    OpportunityPattern,
)
from cx_autopilot.integrations import CXPlatformEvidenceAdapter, CXPlatformHTTPSource
from cx_autopilot.opportunities import OpportunityDetectionConfig, OpportunityDiscoverer
from cx_autopilot.storage import SQLiteStore

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


class FakeCXPlatform:
    def __init__(self) -> None:
        self.events = [
            {
                "event_id": f"event-{number}",
                "event_type": "agent.tool_called",
                "occurred_at": (NOW + timedelta(hours=number)).isoformat(),
                "customer_id": f"customer-{number}",
                "ticket_id": f"ticket-{number}",
                "conversation_id": f"conversation-{number}",
                "execution_id": f"execution-{number}",
                "actor_type": "AI_AGENT",
                "actor_id": "support-agent",
                "data": {
                    "tool_id": "get_transaction_history",
                    "tool_version": "1.0.0",
                    "result_status": "FAILED",
                    "large_payload": "do not copy this payload" * 100,
                },
            }
            for number in range(1, 4)
        ]
        self.tickets = [
            {
                "ticket_id": f"ticket-{number}",
                "customer_id": f"customer-{number}",
                "conversation_id": f"conversation-{number}",
                "status": "ESCALATED",
                "reason": "Payment history",
                "priority": "NORMAL",
                "created_at": NOW.isoformat(),
            }
            for number in range(1, 4)
        ]

    def list_events(self, *, after: str | None, limit: int) -> tuple[dict[str, object], ...]:
        return tuple(self.events if after is None else [])

    def list_tickets(self, *, after: str | None, limit: int) -> tuple[dict[str, object], ...]:
        return tuple(self.tickets if after is None else [])

    def read_ticket(self, ticket_id: str) -> dict[str, object]:
        ticket = next(item for item in self.tickets if item["ticket_id"] == ticket_id)
        number = ticket_id.removeprefix("ticket-")
        return {
            "ticket": ticket,
            "conversation": {
                "conversation_id": f"conversation-{number}",
                "ticket_id": ticket_id,
                "customer_id": ticket["customer_id"],
                "status": "ENDED",
                "started_at": NOW.isoformat(),
                "ended_at": (NOW + timedelta(minutes=5)).isoformat(),
            },
            "messages": [
                {
                    "message_id": f"message-{number}",
                    "conversation_id": f"conversation-{number}",
                    "actor_type": "CUSTOMER",
                    "actor_id": ticket["customer_id"],
                    "content": "Where is my transaction history?",
                    "created_at": NOW.isoformat(),
                }
            ],
            "escalations": [],
            "approvals": [],
            "outcomes": [],
            "csat": [],
        }

    def read_conversation(self, conversation_id: str) -> None:
        return None

    def list_outcomes(self, *, after: str | None, limit: int) -> tuple[dict[str, object], ...]:
        return ()

    def read_execution(self, execution_id: str) -> dict[str, object] | None:
        if execution_id == "execution-missing":
            return None
        number = execution_id.removeprefix("execution-")
        return {
            "execution_id": execution_id,
            "ticket_id": f"ticket-{number}",
            "conversation_id": f"conversation-{number}",
            "agent_id": "support-agent",
            "agent_version": "1.0.0",
            "trace_reference": f"harness-trace-{number}",
            "started_at": NOW.isoformat(),
            "completed_at": (NOW + timedelta(minutes=1)).isoformat(),
            "outcome_status": "FAILED",
        }


def make_signal(
    number: int,
    *,
    pattern_attributes: dict[str, object] | None = None,
    signal_type: str = "agent.tool_called",
    source_record_type: str = "cx_event",
    quality: EvidenceQuality = EvidenceQuality.COMPLETE,
    tenant_id: str = "tenant-a",
) -> OperationalSignal:
    return OperationalSignal(
        signal_id=f"signal-{number}",
        source_system="cx-platform",
        source_record_type=source_record_type,
        source_record_id=f"source-{number}",
        signal_type=signal_type,
        occurred_at=NOW + timedelta(hours=number),
        tenant_id=tenant_id,
        interaction_id=f"conversation-{number}",
        journey_id=f"ticket-{number}",
        customer_id=f"customer-{number}",
        execution_id=f"execution-{number}",
        trace_id=f"trace-{number}",
        source_reference=f"cx-platform:source:{number}",
        normalized_attributes=pattern_attributes or {"tool_id": "get_transaction_history"},
        evidence_quality=quality,
        evidence_refs=(f"cx-platform:evidence:{number}",),
    )


def make_opportunity(
    opportunity_id: str,
    *,
    created_at: datetime,
    pattern_key: str,
    pattern_type: OpportunityPattern = OpportunityPattern.REPEATED_LOOKUP,
    impact: float | None = None,
    confidence: float = 0.9,
    occurrence_keys: tuple[str, ...] = ("journey:one", "journey:two"),
    tenant_id: str = "tenant-a",
    operational_effort: float | None = None,
    predictability: float | None = None,
    risk: float | None = None,
) -> Opportunity:
    return Opportunity(
        opportunity_id=opportunity_id,
        tenant_id=tenant_id,
        title=f"Opportunity {opportunity_id}",
        description="A deterministic test opportunity.",
        source_signal_ids=tuple(f"signal-{item}" for item in occurrence_keys),
        evidence_refs=(f"evidence:{opportunity_id}",),
        frequency_estimate=float(len(occurrence_keys)),
        impact_estimate=impact,
        confidence=confidence,
        status="DISCOVERED",
        created_at=created_at,
        detector_name="test-detector",
        pattern_type=pattern_type,
        pattern_key=pattern_key,
        window_start=created_at,
        window_end=created_at + timedelta(hours=1),
        occurrence_keys=occurrence_keys,
        operational_effort_estimate=operational_effort,
        predictability_estimate=predictability,
        risk_estimate=risk,
    )


def test_cx_adapter_requires_tenant_scope_and_preserves_trace_lineage() -> None:
    source = FakeCXPlatform()
    source.events[0]["data"]["impact_score"] = 0.8
    with SQLiteStore() as store:
        adapter = CXPlatformEvidenceAdapter(source, tenant_id="tenant-a")
        first = adapter.ingest(store.signals, as_of=NOW + timedelta(days=1))
        second = adapter.ingest(store.signals, as_of=NOW + timedelta(days=1))

        assert len(first.signals) == 15
        assert len(first.inserted_signal_ids) == 15
        assert len(second.duplicate_signal_ids) == 15
        event_signals = [item for item in first.signals if item.source_record_type == "cx_event"]
        assert len(event_signals) == 3
        assert {item.trace_id for item in event_signals} == {
            "harness-trace-1",
            "harness-trace-2",
            "harness-trace-3",
        }
        assert all(item.interaction_id and item.journey_id for item in event_signals)
        assert all("large_payload" not in item.normalized_attributes for item in event_signals)
        assert (
            next(
                item for item in event_signals if item.source_record_id == "event-1"
            ).normalized_attributes["impact_score"]
            == 0.8
        )

        other_tenant = CXPlatformEvidenceAdapter(source, tenant_id="tenant-b")
        other = other_tenant.ingest(store.signals, as_of=NOW + timedelta(days=1))
        assert other.tenant_id == "tenant-b"
        assert {item.tenant_id for item in other.signals} == {"tenant-b"}


def test_cx_adapter_marks_stale_partial_and_conflicting_source_facts() -> None:
    source = FakeCXPlatform()
    source.events.append(
        {
            "event_id": "event-conflict",
            "event_type": "agent.tool_called",
            "occurred_at": (NOW - timedelta(days=40)).isoformat(),
            "customer_id": "customer-wrong",
            "ticket_id": "ticket-1",
            "conversation_id": "conversation-1",
            "execution_id": "execution-missing",
            "actor_type": "AI_AGENT",
            "actor_id": "support-agent",
            "data": {"tool_id": "get_transaction_history"},
        }
    )
    with SQLiteStore() as store:
        result = CXPlatformEvidenceAdapter(source, tenant_id="tenant-a").ingest(
            store.signals, as_of=NOW, page_size=10
        )
        conflict = next(
            item for item in result.signals if item.source_record_id == "event-conflict"
        )
        assert conflict.evidence_quality is EvidenceQuality.CONFLICTING
        assert "cx-platform:executions:execution-missing" in result.unavailable_source_refs


def test_all_initial_detectors_are_rule_based_and_duplicate_safe() -> None:
    signal_specs = (
        ("sequence", "workflow.sequence", "cx_event", {"operation_sequence": ["a", "b"]}),
        ("escalation", "ticket.escalated", "escalation", {"reason": "UNSUPPORTED_REQUEST"}),
        (
            "unresolved",
            "outcome.recorded",
            "outcome",
            {"resolved": False, "resolution_code": "UNRESOLVED"},
        ),
        ("lookup", "agent.tool_called", "cx_event", {"tool_id": "get_transaction_history"}),
        (
            "approval",
            "approval.requested",
            "approval",
            {"status": "PENDING", "tool_id": "request_refund"},
        ),
        (
            "policy",
            "agent.tool_failed",
            "cx_event",
            {"permission_reason_code": "POLICY_DENIED", "result_status": "FAILED"},
        ),
        ("workaround", "operator.workaround", "cx_event", {"workaround_type": "manual_lookup"}),
        ("correction", "operator.correction", "cx_event", {"correction_type": "missing_field"}),
    )
    signals: list[OperationalSignal] = []
    number = 1
    for _, signal_type, source_record_type, attributes in signal_specs:
        for _repeat in range(2):
            signals.append(
                make_signal(
                    number,
                    pattern_attributes=attributes,
                    signal_type=signal_type,
                    source_record_type=source_record_type,
                )
            )
            number += 1

    discoverer = OpportunityDiscoverer(OpportunityDetectionConfig(window_size=timedelta(days=7)))
    opportunities = discoverer.discover([*signals, signals[0]])
    assert {item.pattern_type for item in opportunities} == set(OpportunityPattern)
    lookup = next(
        item for item in opportunities if item.pattern_type is OpportunityPattern.REPEATED_LOOKUP
    )
    assert lookup.frequency_estimate == 2
    assert len(lookup.source_signal_ids) == 2
    assert lookup.detector_name == "repeated_lookup"

    with pytest.raises(ValueError):
        discoverer.discover([signals[0], signals[0].model_copy(update={"signal_id": "other"})])


def test_detectors_do_not_fabricate_unsupported_priority_factors() -> None:
    signals = [make_signal(1), make_signal(2)]
    lookup = next(
        opportunity
        for opportunity in OpportunityDiscoverer().discover(signals)
        if opportunity.pattern_type is OpportunityPattern.REPEATED_LOOKUP
    )

    assert lookup.impact_estimate is None
    assert lookup.operational_effort_estimate is None
    assert lookup.predictability_estimate is None
    assert lookup.risk_estimate is None

    measured_signals = [
        make_signal(
            3,
            pattern_attributes={
                "tool_id": "get_transaction_history",
                "impact_score": 0.8,
                "operational_effort_score": 0.6,
                "predictability_score": 0.7,
                "risk_score": 0.2,
            },
        ),
        make_signal(
            4,
            pattern_attributes={
                "tool_id": "get_transaction_history",
                "impact_score": 0.4,
                "operational_effort_score": 0.2,
                "predictability_score": 0.5,
                "risk_score": 0.1,
            },
        ),
    ]
    measured_lookup = next(
        opportunity
        for opportunity in OpportunityDiscoverer().discover(measured_signals)
        if opportunity.pattern_type is OpportunityPattern.REPEATED_LOOKUP
    )
    assert measured_lookup.impact_estimate == 0.6
    assert measured_lookup.operational_effort_estimate == 0.4
    assert measured_lookup.predictability_estimate == 0.6
    assert measured_lookup.risk_estimate == 0.15


def test_discovery_is_tenant_scoped_and_excludes_unreliable_evidence() -> None:
    signals = [
        make_signal(1),
        make_signal(2),
        make_signal(3, quality=EvidenceQuality.CONFLICTING),
    ]
    discoverer = OpportunityDiscoverer()
    tenant_a = discoverer.discover(signals, tenant_id="tenant-a")
    assert tenant_a
    assert {item.tenant_id for item in tenant_a} == {"tenant-a"}
    lookup = next(
        item for item in tenant_a if item.pattern_type is OpportunityPattern.REPEATED_LOOKUP
    )
    assert lookup.frequency_estimate == 2

    with pytest.raises(ValueError):
        discoverer.discover([*signals, make_signal(4, tenant_id="tenant-b")], tenant_id="tenant-a")

    same_source_ids = [
        make_signal(10),
        make_signal(11),
        make_signal(10, tenant_id="tenant-b"),
        make_signal(11, tenant_id="tenant-b"),
    ]
    assert {item.tenant_id for item in discoverer.discover(same_source_ids)} == {
        "tenant-a",
        "tenant-b",
    }


def test_clustering_uses_half_open_boundaries_and_separate_factor_rank() -> None:
    config = OpportunityClusteringConfig(window_size=timedelta(days=1))
    first = make_opportunity(
        "opportunity-first",
        created_at=datetime(2026, 1, 1, 23, 59, tzinfo=UTC),
        pattern_key="operation:lookup",
    )
    boundary = make_opportunity(
        "opportunity-boundary",
        created_at=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
        pattern_key="operation:lookup",
    )
    high_priority = make_opportunity(
        "opportunity-high",
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        pattern_key="operation:important",
        impact=1.0,
        occurrence_keys=("journey:1", "journey:2", "journey:3", "journey:4"),
    )
    clusterer = OpportunityClusterer(config)
    clusters = clusterer.cluster([first, first, boundary, high_priority])

    assert len(clusters) == 3
    assert {cluster.priority_rank for cluster in clusters} == {1, 2, 3}
    lookup_clusters = [cluster for cluster in clusters if cluster.pattern_key == "operation:lookup"]
    assert len(lookup_clusters) == 2
    assert all(
        cluster.window_end - cluster.window_start == timedelta(days=1) for cluster in clusters
    )
    assert all(cluster.prioritization_factors.frequency >= 0 for cluster in clusters)
    assert all(cluster.priority_score >= 0 for cluster in clusters)
    assert sum(cluster.frequency for cluster in lookup_clusters) == 4


def test_prioritization_renormalizes_weights_for_available_factors() -> None:
    opportunity = make_opportunity(
        "opportunity-unknown-factors",
        created_at=NOW,
        pattern_key="operation:lookup",
        occurrence_keys=("journey:one", "journey:two", "journey:three"),
    )
    cluster = OpportunityClusterer().cluster([opportunity])[0]
    factors = cluster.prioritization_factors

    assert factors.impact is None
    assert factors.operational_effort is None
    assert factors.predictability is None
    assert factors.risk is None
    assert factors.available_factors == ("frequency", "confidence")
    assert factors.unavailable_factors == (
        "impact",
        "operational_effort",
        "predictability",
        "risk",
    )
    assert factors.effective_weights == {
        "confidence": 0.444444444444,
        "frequency": 0.555555555556,
    }
    expected_score = (
        factors.effective_weights["frequency"] * factors.frequency
        + factors.effective_weights["confidence"] * factors.confidence
    )
    assert cluster.priority_score == round(expected_score, 6)
    assert cluster.impact is None


def test_observed_risk_is_penalized_but_unknown_risk_is_not_zero() -> None:
    unknown = make_opportunity(
        "opportunity-without-risk",
        created_at=NOW,
        pattern_key="operation:unknown-risk",
    )
    observed = make_opportunity(
        "opportunity-with-risk",
        created_at=NOW,
        pattern_key="operation:observed-risk",
        risk=0.5,
    )
    clusters = {
        cluster.pattern_key: cluster
        for cluster in OpportunityClusterer().cluster([unknown, observed])
    }

    assert clusters["operation:unknown-risk"].prioritization_factors.risk is None
    assert clusters["operation:observed-risk"].prioritization_factors.risk == 0.5
    assert (
        clusters["operation:observed-risk"].priority_score
        < clusters["operation:unknown-risk"].priority_score
    )


def test_cluster_round_trip_persists_factors_and_rank() -> None:
    opportunity = make_opportunity(
        "opportunity-persisted",
        created_at=NOW,
        pattern_key="operation:lookup",
    )
    cluster = OpportunityClusterer().cluster([opportunity])[0]
    with SQLiteStore() as store:
        store.opportunity_clusters.insert(cluster)
        restored = store.opportunity_clusters.get(cluster.cluster_id, tenant_id="tenant-a")
    assert restored == cluster
    assert restored is not None
    assert restored.prioritization_factors == cluster.prioritization_factors
    assert restored.priority_rank == cluster.priority_rank


def test_http_source_matches_current_cx_read_routes() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def get_json(path: str, params: dict[str, str]) -> object:
        calls.append((path, params))
        return [] if path == "/events" else None

    source = CXPlatformHTTPSource("http://cx.example", get_json=get_json)
    assert source.list_events(after="event-1", limit=3) == ()
    assert source.read_ticket("ticket/one") is None
    assert calls == [
        ("/events", {"after": "event-1", "limit": "3"}),
        ("/tickets/ticket%2Fone", {}),
    ]
