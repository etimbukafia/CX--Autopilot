"""Domain-focused persistence ports.

These interfaces describe Autopilot behavior. They expose no SQLite types or
SQL concepts.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

from ..contracts import (
    AgentSystemInventorySnapshot,
    CandidateReference,
    ChangeProposal,
    DecisionRecord,
    EvaluationReference,
    OperationalDisposition,
    OperationalSignal,
    Opportunity,
    OpportunityCluster,
    PilotRecommendation,
    ProblemDiagnosis,
)

RecordT = TypeVar("RecordT")


class RecordRepository(Protocol[RecordT]):
    """Minimal immutable, tenant-scoped repository behavior."""

    def insert(self, record: RecordT) -> RecordT:
        """Insert one record or return the identical stored record."""

    def get(self, record_id: str, *, tenant_id: str) -> RecordT | None:
        """Return one record inside the required tenant scope."""

    def list(self, *, tenant_id: str, limit: int | None = None) -> Sequence[RecordT]:
        """List records inside the required tenant scope."""


class OperationalSignalStore(RecordRepository[OperationalSignal], Protocol):
    """Idempotent source-signal ingestion boundary."""

    def ingest(self, signal: OperationalSignal) -> OperationalSignal:
        """Ingest one source identity without creating a duplicate signal."""

    def get_by_source_identity(
        self,
        *,
        tenant_id: str,
        source_system: str,
        source_record_type: str,
        source_record_id: str,
        source_record_version: str | None = None,
    ) -> OperationalSignal | None:
        """Return the signal for one stable source identity, when present."""


class OpportunityStore(RecordRepository[Opportunity], Protocol):
    """Opportunity persistence boundary."""


class OpportunityClusterStore(RecordRepository[OpportunityCluster], Protocol):
    """Opportunity-cluster persistence boundary."""


class AgentSystemInventorySnapshotStore(RecordRepository[AgentSystemInventorySnapshot], Protocol):
    """Inventory snapshot persistence boundary."""


class ProblemDiagnosisStore(RecordRepository[ProblemDiagnosis], Protocol):
    """Diagnosis persistence boundary."""


class ChangeProposalStore(RecordRepository[ChangeProposal], Protocol):
    """Change-proposal persistence boundary."""


class OperationalDispositionStore(RecordRepository[OperationalDisposition], Protocol):
    """No-change disposition persistence boundary."""


class CandidateReferenceStore(RecordRepository[CandidateReference], Protocol):
    """Candidate-reference persistence boundary."""


class EvaluationReferenceStore(RecordRepository[EvaluationReference], Protocol):
    """Evaluation-reference persistence boundary."""


class PilotRecommendationStore(RecordRepository[PilotRecommendation], Protocol):
    """Pilot-recommendation persistence boundary."""


class DecisionRecordStore(RecordRepository[DecisionRecord], Protocol):
    """Decision-record persistence boundary."""


__all__ = [
    "AgentSystemInventorySnapshotStore",
    "CandidateReferenceStore",
    "ChangeProposalStore",
    "DecisionRecordStore",
    "EvaluationReferenceStore",
    "OperationalDispositionStore",
    "OperationalSignalStore",
    "OpportunityClusterStore",
    "OpportunityStore",
    "PilotRecommendationStore",
    "ProblemDiagnosisStore",
    "RecordRepository",
]
