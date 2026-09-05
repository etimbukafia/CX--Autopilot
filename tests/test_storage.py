from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cx_autopilot.contracts import (
    AgentSystemInventorySnapshot,
    CandidateReference,
    ChangeProposal,
    DecisionRecord,
    EvaluationReference,
    EvidenceQuality,
    OperationalDisposition,
    OperationalSignal,
    Opportunity,
    OpportunityCluster,
    PilotRecommendation,
    ProblemDiagnosis,
)
from cx_autopilot.storage import DuplicateRecordError, SourceIdentityConflict, SQLiteStore
from test_contracts import core_records

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_signal(
    *,
    signal_id: str = "signal-1",
    tenant_id: str = "tenant-a",
    source_record_id: str = "event-1",
    source_record_version: str | None = "1",
) -> OperationalSignal:
    return OperationalSignal(
        signal_id=signal_id,
        source_system="cx-platform",
        source_record_type="cx_event",
        source_record_id=source_record_id,
        source_record_version=source_record_version,
        signal_type="agent.tool_failed",
        occurred_at=NOW,
        tenant_id=tenant_id,
        interaction_id="conversation-1",
        journey_id="ticket-1",
        execution_id="execution-1",
        trace_id="trace-1",
        source_reference=f"cx-platform:cx_event:{source_record_id}",
        normalized_attributes={"tool_id": "get_transaction_history"},
        evidence_quality=EvidenceQuality.COMPLETE,
        evidence_refs=(f"evidence:{source_record_id}",),
    )


def make_opportunity() -> Opportunity:
    return Opportunity(
        opportunity_id="opportunity-1",
        tenant_id="tenant-a",
        title="Repeated transaction history lookup",
        description="The same lookup is repeated across support contacts.",
        source_signal_ids=("signal-1",),
        evidence_refs=("evidence:event-1",),
        frequency_estimate=4,
        impact_estimate=0.8,
        confidence=0.9,
        status="DISCOVERED",
        created_at=NOW,
    )


def test_signal_ingestion_is_idempotent_by_stable_source_identity() -> None:
    with SQLiteStore() as store:
        first = make_signal()
        assert store.signals.ingest(first) == first
        assert store.signals.ingest(first) == first
        assert store.signals.list(tenant_id="tenant-a") == (first,)
        assert (
            store.signals.get_by_source_identity(
                tenant_id="tenant-a",
                source_system="cx-platform",
                source_record_type="cx_event",
                source_record_id="event-1",
                source_record_version="1",
            )
            == first
        )

        with pytest.raises(SourceIdentityConflict):
            store.signals.ingest(make_signal(signal_id="signal-other"))

        # A new source version is a new source fact, not a duplicate.
        store.signals.ingest(make_signal(signal_id="signal-2", source_record_version="2"))
        assert len(store.signals.list(tenant_id="tenant-a")) == 2


def test_tenant_scoped_reads_do_not_cross_tenants() -> None:
    with SQLiteStore() as store:
        tenant_a = make_signal(tenant_id="tenant-a")
        tenant_b = make_signal(
            signal_id="signal-b",
            tenant_id="tenant-b",
            source_record_id="event-b",
        )
        store.signals.ingest(tenant_a)
        store.signals.ingest(tenant_b)

        assert store.signals.get("signal-1", tenant_id="tenant-b") is None
        assert store.signals.list(tenant_id="tenant-a") == (tenant_a,)
        assert store.signals.list(tenant_id="tenant-b") == (tenant_b,)
        assert store.opportunities.get("opportunity-1", tenant_id="tenant-b") is None


def test_core_record_round_trip_preserves_timestamp_identity_and_lineage(tmp_path) -> None:
    database_path = tmp_path / "autopilot.db"
    opportunity = make_opportunity()
    with SQLiteStore(database_path) as store:
        store.opportunities.insert(opportunity)

    with SQLiteStore(database_path) as reopened:
        restored = reopened.opportunities.get("opportunity-1", tenant_id="tenant-a")

    assert restored == opportunity
    assert restored is not None
    assert restored.created_at == NOW
    assert restored.source_signal_ids == ("signal-1",)
    assert restored.evidence_refs == ("evidence:event-1",)


def test_non_signal_records_keep_immutable_serialized_identity() -> None:
    with SQLiteStore() as store:
        original = make_opportunity()
        assert store.opportunities.insert(original) == original
        assert store.opportunities.insert(original) == original

        changed = original.model_copy(update={"title": "A different opportunity"})
        with pytest.raises(DuplicateRecordError):
            store.opportunities.insert(changed)


def test_every_phase_one_record_round_trips_through_its_repository() -> None:
    with SQLiteStore() as store:
        records = core_records()
        repository_records = (
            (
                store.signals,
                tuple(record for record in records if isinstance(record, OperationalSignal)),
                "signal_id",
            ),
            (
                store.opportunities,
                tuple(record for record in records if isinstance(record, Opportunity)),
                "opportunity_id",
            ),
            (
                store.opportunity_clusters,
                tuple(record for record in records if isinstance(record, OpportunityCluster)),
                "cluster_id",
            ),
            (
                store.inventory_snapshots,
                tuple(
                    record for record in records if isinstance(record, AgentSystemInventorySnapshot)
                ),
                "snapshot_id",
            ),
            (
                store.diagnoses,
                tuple(record for record in records if isinstance(record, ProblemDiagnosis)),
                "diagnosis_id",
            ),
            (
                store.change_proposals,
                tuple(record for record in records if isinstance(record, ChangeProposal)),
                "proposal_id",
            ),
            (
                store.operational_dispositions,
                tuple(record for record in records if isinstance(record, OperationalDisposition)),
                "disposition_id",
            ),
            (
                store.candidate_references,
                tuple(record for record in records if isinstance(record, CandidateReference)),
                "candidate_id",
            ),
            (
                store.evaluation_references,
                tuple(record for record in records if isinstance(record, EvaluationReference)),
                "evaluation_id",
            ),
            (
                store.pilot_recommendations,
                tuple(record for record in records if isinstance(record, PilotRecommendation)),
                "recommendation_id",
            ),
            (
                store.decision_records,
                tuple(record for record in records if isinstance(record, DecisionRecord)),
                "decision_id",
            ),
        )

        for repository, repository_items, id_field in repository_records:
            assert len(repository_items) == 1
            record = repository_items[0]
            repository.insert(record)
            record_id = getattr(record, id_field)
            assert repository.get(record_id, tenant_id=record.tenant_id) == record


def test_explicit_transaction_rolls_back_all_record_writes() -> None:
    with SQLiteStore() as store:
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.opportunities.insert(make_opportunity())
                store.signals.ingest(make_signal())
                raise RuntimeError("abort the transaction")

        assert store.opportunities.get("opportunity-1", tenant_id="tenant-a") is None
        assert store.signals.get("signal-1", tenant_id="tenant-a") is None


def test_public_query_requires_tenant_scope() -> None:
    with SQLiteStore() as store:
        with pytest.raises(ValueError):
            store.signals.list(tenant_id="")
        with pytest.raises(ValueError):
            store.opportunities.list(tenant_id=" ")
