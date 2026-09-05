"""SQLite persistence adapter for Autopilot-owned immutable records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, Iterator, TypeVar

from pydantic import BaseModel

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
from ..contracts.common import to_jsonable


class StorageError(RuntimeError):
    """Base error for persistence boundary failures."""


class DuplicateRecordError(StorageError):
    """Raised when an immutable ID is reused with different content."""


class SourceIdentityConflict(StorageError):
    """Raised when one source identity is re-ingested with different content."""


RecordModelT = TypeVar("RecordModelT", bound=BaseModel)


@dataclass(frozen=True)
class _RecordSpec(Generic[RecordModelT]):
    table: str
    id_field: str
    model_type: type[RecordModelT]


_SPECS: tuple[_RecordSpec[Any], ...] = (
    _RecordSpec("operational_signals", "signal_id", OperationalSignal),
    _RecordSpec("opportunities", "opportunity_id", Opportunity),
    _RecordSpec("opportunity_clusters", "cluster_id", OpportunityCluster),
    _RecordSpec("inventory_snapshots", "snapshot_id", AgentSystemInventorySnapshot),
    _RecordSpec("problem_diagnoses", "diagnosis_id", ProblemDiagnosis),
    _RecordSpec("change_proposals", "proposal_id", ChangeProposal),
    _RecordSpec("operational_dispositions", "disposition_id", OperationalDisposition),
    _RecordSpec("candidate_references", "candidate_id", CandidateReference),
    _RecordSpec("evaluation_references", "evaluation_id", EvaluationReference),
    _RecordSpec("pilot_recommendations", "recommendation_id", PilotRecommendation),
    _RecordSpec("decision_records", "decision_id", DecisionRecord),
)


class SQLiteStore:
    """Initial deterministic SQLite adapter with explicit transaction scope."""

    schema_version = 1

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

        specs = {spec.table: spec for spec in _SPECS}
        self.signals: _SignalRepository = _SignalRepository(self, specs["operational_signals"])
        self.opportunities: _JsonRepository[Opportunity] = _JsonRepository(
            self, specs["opportunities"]
        )
        self.opportunity_clusters: _JsonRepository[OpportunityCluster] = _JsonRepository(
            self, specs["opportunity_clusters"]
        )
        self.inventory_snapshots: _JsonRepository[AgentSystemInventorySnapshot] = _JsonRepository(
            self, specs["inventory_snapshots"]
        )
        self.diagnoses: _JsonRepository[ProblemDiagnosis] = _JsonRepository(
            self, specs["problem_diagnoses"]
        )
        self.change_proposals: _JsonRepository[ChangeProposal] = _JsonRepository(
            self, specs["change_proposals"]
        )
        self.operational_dispositions: _JsonRepository[OperationalDisposition] = _JsonRepository(
            self, specs["operational_dispositions"]
        )
        self.candidate_references: _JsonRepository[CandidateReference] = _JsonRepository(
            self, specs["candidate_references"]
        )
        self.evaluation_references: _JsonRepository[EvaluationReference] = _JsonRepository(
            self, specs["evaluation_references"]
        )
        self.pilot_recommendations: _JsonRepository[PilotRecommendation] = _JsonRepository(
            self, specs["pilot_recommendations"]
        )
        self.decision_records: _JsonRepository[DecisionRecord] = _JsonRepository(
            self, specs["decision_records"]
        )

        # Short aliases keep the domain names visible to callers.
        self.inventory = self.inventory_snapshots
        self.proposals = self.change_proposals
        self.dispositions = self.operational_dispositions
        self.candidates = self.candidate_references
        self.evaluations = self.evaluation_references
        self.recommendations = self.pilot_recommendations
        self.decisions = self.decision_records

    def close(self) -> None:
        """Close the adapter connection."""

        self._connection.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator["SQLiteStore"]:
        """Run public repository operations in one atomic transaction."""

        if self._connection.in_transaction:
            savepoint = "cx_autopilot_nested"
            self._connection.execute(f"SAVEPOINT {savepoint}")
            try:
                yield self
            except BaseException:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return

        self._connection.execute("BEGIN")
        try:
            yield self
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    @contextmanager
    def _write(self) -> Iterator[None]:
        """Use an existing transaction or create a method-level transaction."""

        if self._connection.in_transaction:
            yield
            return
        self._connection.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cx_autopilot_schema (
                version INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operational_signals (
                signal_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                source_system TEXT NOT NULL,
                source_record_type TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_record_version TEXT,
                source_record_version_key TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                record_json TEXT NOT NULL,
                UNIQUE (
                    tenant_id,
                    source_system,
                    source_record_type,
                    source_record_id,
                    source_record_version_key
                )
            );

            CREATE TABLE IF NOT EXISTS opportunities (
                opportunity_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS opportunity_clusters (
                cluster_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inventory_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS problem_diagnoses (
                diagnosis_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS change_proposals (
                proposal_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operational_dispositions (
                disposition_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidate_references (
                candidate_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_references (
                evaluation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pilot_recommendations (
                recommendation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_records (
                decision_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                stored_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            """
        )
        row = self._connection.execute("SELECT version FROM cx_autopilot_schema LIMIT 1").fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO cx_autopilot_schema(version) VALUES (?)",
                (self.schema_version,),
            )
        elif row["version"] != self.schema_version:
            raise StorageError(f"Unsupported Autopilot schema version: {row['version']}")

    def insert_signal(self, signal: OperationalSignal) -> OperationalSignal:
        """Convenience wrapper for idempotent signal ingestion."""

        return self.signals.ingest(signal)

    def insert_opportunity(self, record: Opportunity) -> Opportunity:
        return self.opportunities.insert(record)

    def get_signal(self, signal_id: str, *, tenant_id: str) -> OperationalSignal | None:
        return self.signals.get(signal_id, tenant_id=tenant_id)

    def list_signals(
        self, *, tenant_id: str, limit: int | None = None
    ) -> tuple[OperationalSignal, ...]:
        return tuple(self.signals.list(tenant_id=tenant_id, limit=limit))


class _JsonRepository(Generic[RecordModelT]):
    """Private JSON-record repository shared by explicit tables."""

    def __init__(self, store: SQLiteStore, spec: _RecordSpec[RecordModelT]) -> None:
        self._store = store
        self._spec = spec

    def insert(self, record: RecordModelT) -> RecordModelT:
        record_id = _record_id(record, self._spec.id_field)
        tenant_id = _tenant_id(record)
        payload = _serialize(record)
        stored_at = _stored_at(record)
        with self._store._write():
            existing = self._store._connection.execute(
                f"SELECT tenant_id, record_json FROM {self._spec.table} "
                f"WHERE {self._spec.id_field}=?",
                (record_id,),
            ).fetchone()
            if existing is not None:
                if existing["tenant_id"] != tenant_id:
                    raise DuplicateRecordError(
                        f"record ID {record_id!r} is already used by another tenant"
                    )
                if existing["record_json"] != payload:
                    raise DuplicateRecordError(
                        f"immutable record {record_id!r} has different content"
                    )
                return _deserialize(self._spec.model_type, existing["record_json"])
            self._store._connection.execute(
                f"INSERT INTO {self._spec.table}"
                f"({self._spec.id_field}, tenant_id, stored_at, record_json) "
                "VALUES (?, ?, ?, ?)",
                (record_id, tenant_id, stored_at, payload),
            )
        return record

    def get(self, record_id: str, *, tenant_id: str) -> RecordModelT | None:
        _require_scope(tenant_id)
        row = self._store._connection.execute(
            f"SELECT record_json FROM {self._spec.table} "
            f"WHERE {self._spec.id_field}=? AND tenant_id=?",
            (record_id, tenant_id),
        ).fetchone()
        return None if row is None else _deserialize(self._spec.model_type, row["record_json"])

    def list(self, *, tenant_id: str, limit: int | None = None) -> tuple[RecordModelT, ...]:
        _require_scope(tenant_id)
        limit_sql, params = _limit_sql(limit, tenant_id)
        rows = self._store._connection.execute(
            f"SELECT record_json FROM {self._spec.table} "
            f"WHERE tenant_id=? ORDER BY stored_at, {self._spec.id_field}{limit_sql}",
            params,
        ).fetchall()
        return tuple(_deserialize(self._spec.model_type, row["record_json"]) for row in rows)


class _SignalRepository(_JsonRepository[OperationalSignal]):
    """Operational signal repository with source-identity idempotency."""

    def ingest(self, signal: OperationalSignal) -> OperationalSignal:
        source_version_key = signal.source_record_version or ""
        payload = _serialize(signal)
        with self._store._write():
            row = self._store._connection.execute(
                "SELECT record_json FROM operational_signals WHERE "
                "tenant_id=? AND source_system=? AND source_record_type=? "
                "AND source_record_id=? AND source_record_version_key=?",
                (
                    signal.tenant_id,
                    signal.source_system,
                    signal.source_record_type,
                    signal.source_record_id,
                    source_version_key,
                ),
            ).fetchone()
            if row is not None:
                existing = _deserialize(OperationalSignal, row["record_json"])
                if row["record_json"] != payload:
                    raise SourceIdentityConflict(
                        "source identity was already ingested with different content"
                    )
                return existing
            try:
                self._store._connection.execute(
                    "INSERT INTO operational_signals("
                    "signal_id, tenant_id, source_system, source_record_type, "
                    "source_record_id, source_record_version, source_record_version_key, "
                    "occurred_at, record_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        signal.signal_id,
                        signal.tenant_id,
                        signal.source_system,
                        signal.source_record_type,
                        signal.source_record_id,
                        signal.source_record_version,
                        source_version_key,
                        signal.occurred_at.isoformat(),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateRecordError("signal identity or ID already exists") from exc
        return signal

    def insert(self, record: OperationalSignal) -> OperationalSignal:
        return self.ingest(record)

    def get_by_source_identity(
        self,
        *,
        tenant_id: str,
        source_system: str,
        source_record_type: str,
        source_record_id: str,
        source_record_version: str | None = None,
    ) -> OperationalSignal | None:
        _require_scope(tenant_id)
        row = self._store._connection.execute(
            "SELECT record_json FROM operational_signals WHERE tenant_id=? "
            "AND source_system=? AND source_record_type=? AND source_record_id=? "
            "AND source_record_version_key=?",
            (
                tenant_id,
                source_system,
                source_record_type,
                source_record_id,
                source_record_version or "",
            ),
        ).fetchone()
        return None if row is None else _deserialize(OperationalSignal, row["record_json"])

    def get(self, record_id: str, *, tenant_id: str) -> OperationalSignal | None:
        _require_scope(tenant_id)
        row = self._store._connection.execute(
            "SELECT record_json FROM operational_signals WHERE signal_id=? AND tenant_id=?",
            (record_id, tenant_id),
        ).fetchone()
        return None if row is None else _deserialize(OperationalSignal, row["record_json"])

    def list(
        self,
        *,
        tenant_id: str,
        limit: int | None = None,
    ) -> tuple[OperationalSignal, ...]:
        _require_scope(tenant_id)
        limit_sql, params = _limit_sql(limit, tenant_id)
        rows = self._store._connection.execute(
            "SELECT record_json FROM operational_signals WHERE tenant_id=? "
            f"ORDER BY occurred_at, signal_id{limit_sql}",
            params,
        ).fetchall()
        return tuple(_deserialize(OperationalSignal, row["record_json"]) for row in rows)


def _record_id(record: BaseModel, field_name: str) -> str:
    value = getattr(record, field_name)
    if not isinstance(value, str) or not value:
        raise StorageError(f"{field_name} must be a non-empty string")
    return value


def _tenant_id(record: BaseModel) -> str:
    value = getattr(record, "tenant_id", None)
    if not isinstance(value, str) or not value:
        raise StorageError("tenant_id is required for tenant-scoped records")
    return value


def _stored_at(record: BaseModel) -> str:
    for field_name in ("created_at", "captured_at", "occurred_at", "window_start"):
        value = getattr(record, field_name, None)
        if isinstance(value, datetime):
            return value.isoformat()
    # Some cross-system references intentionally carry no domain timestamp. The
    # persistence timestamp is metadata and does not become part of the record.
    return datetime.now(UTC).isoformat()


def _serialize(record: BaseModel) -> str:
    value = to_jsonable(record.__dict__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _deserialize(model_type: type[RecordModelT], payload: str) -> RecordModelT:
    try:
        return model_type.model_validate(json.loads(payload))
    except Exception as exc:  # pragma: no cover - corruption path is defensive
        raise StorageError("stored record failed domain validation") from exc


def _require_scope(tenant_id: str) -> None:
    if not tenant_id or not tenant_id.strip():
        raise ValueError("tenant_id is required")


def _limit_sql(limit: int | None, tenant_id: str) -> tuple[str, tuple[Any, ...]]:
    if limit is None:
        return "", (tenant_id,)
    if limit < 1:
        raise ValueError("limit must be positive")
    return " LIMIT ?", (tenant_id, limit)


__all__ = [
    "DuplicateRecordError",
    "SQLiteStore",
    "SourceIdentityConflict",
    "StorageError",
]
