"""CX Platform evidence port, HTTP adapter, and source-fact normalizer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import Field, JsonValue, field_validator

from ..contracts import EvidenceQuality, OperationalSignal
from ..contracts.common import ImmutableModel, aware_timestamp, non_blank, unique_values
from ..storage.ports import OperationalSignalStore

SourceRecord = Mapping[str, Any]
JsonGetter = Callable[[str, Mapping[str, str]], object]

_SOURCE_SYSTEM = "cx-platform"
_MAX_ATTRIBUTE_TEXT = 256
_MAX_ATTRIBUTE_LIST = 20
_EVENT_ATTRIBUTE_KEYS = frozenset(
    {
        "action_digest",
        "agent_version",
        "approval_id",
        "action_sequence",
        "business_operation",
        "call_id",
        "cause",
        "customer_impact_score",
        "correction_type",
        "error_code",
        "escalation_id",
        "evidence_ids",
        "effort_score",
        "external_dependency_risk_score",
        "harness_request_id",
        "human_workaround",
        "impact_score",
        "lookup_type",
        "manual_action",
        "operation",
        "operation_sequence",
        "operator_action",
        "operator_correction",
        "outcome_id",
        "outcome_status",
        "operational_effort_score",
        "permission_reason_code",
        "policy_id",
        "policy_denied",
        "predictability_score",
        "reason",
        "result_status",
        "risk_score",
        "safety_risk_score",
        "sequence",
        "status",
        "tool_id",
        "tool_version",
        "trace_reference",
        "unresolved",
        "workaround_type",
    }
)


class CXPlatformSourceError(RuntimeError):
    """Raised when the CX Platform cannot provide a requested source read."""


class CXPlatformDataError(ValueError):
    """Raised when a CX Platform response violates its verified read contract."""


class CXPlatformEvidencePort(Protocol):
    """Minimal typed read boundary for the current CX Platform API."""

    def list_events(self, *, after: str | None, limit: int) -> Sequence[SourceRecord]:
        """Read append-only CX events using the event cursor."""

    def list_tickets(self, *, after: str | None, limit: int) -> Sequence[SourceRecord]:
        """Read tickets using the stable ticket cursor."""

    def read_ticket(self, ticket_id: str) -> SourceRecord | None:
        """Read one ticket detail, or return None when it is no longer available."""

    def read_conversation(self, conversation_id: str) -> SourceRecord | None:
        """Read one conversation export, or return None when unavailable."""

    def list_outcomes(
        self,
        *,
        after: str | None,
        limit: int,
    ) -> Sequence[SourceRecord]:
        """Read structured CX outcome views using the outcome cursor."""

    def read_execution(self, execution_id: str) -> SourceRecord | None:
        """Read one CX-owned execution reference, or return None when unavailable."""


class CXPlatformHTTPSource:
    """Read the current CX Platform HTTP export without importing its SDK."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        get_json: JsonGetter | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be blank")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._get_json = get_json

    def list_events(self, *, after: str | None, limit: int) -> tuple[SourceRecord, ...]:
        return _records(self._get("/events", _cursor_params(after, limit)), "events")

    def list_tickets(self, *, after: str | None, limit: int) -> tuple[SourceRecord, ...]:
        return _records(self._get("/tickets", _cursor_params(after, limit)), "tickets")

    def read_ticket(self, ticket_id: str) -> SourceRecord | None:
        return _optional_record(
            self._get_optional(f"/tickets/{quote(ticket_id, safe='')}"), "ticket"
        )

    def read_conversation(self, conversation_id: str) -> SourceRecord | None:
        return _optional_record(
            self._get_optional(f"/conversations/{quote(conversation_id, safe='')}"),
            "conversation",
        )

    def list_outcomes(
        self,
        *,
        after: str | None,
        limit: int,
    ) -> tuple[SourceRecord, ...]:
        return _records(self._get("/outcomes", _cursor_params(after, limit)), "outcomes")

    def read_execution(self, execution_id: str) -> SourceRecord | None:
        return _optional_record(
            self._get_optional(f"/executions/{quote(execution_id, safe='')}"), "execution"
        )

    def _get(self, path: str, params: Mapping[str, str]) -> object:
        if self._get_json is not None:
            return self._get_json(path, params)
        url = f"{self.base_url}{path}"
        query = urlencode(dict(params))
        request = Request(
            f"{url}?{query}" if query else url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return cast(object, json.loads(response.read().decode("utf-8")))
        except HTTPError as exc:
            raise CXPlatformSourceError(
                f"CX Platform GET {path} failed with HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CXPlatformSourceError(f"CX Platform GET {path} failed") from exc

    def _get_optional(self, path: str) -> object | None:
        if self._get_json is not None:
            return self._get_json(path, {})
        url = f"{self.base_url}{path}"
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return cast(object, json.loads(response.read().decode("utf-8")))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise CXPlatformSourceError(
                f"CX Platform GET {path} failed with HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CXPlatformSourceError(f"CX Platform GET {path} failed") from exc


class EvidenceIngestionResult(ImmutableModel):
    """Inspectable result of one tenant-scoped source ingestion run."""

    tenant_id: str = Field(min_length=1)
    signals: tuple[OperationalSignal, ...]
    inserted_signal_ids: tuple[str, ...] = ()
    duplicate_signal_ids: tuple[str, ...] = ()
    unavailable_source_refs: tuple[str, ...] = ()
    quality_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("tenant_id")
    @classmethod
    def tenant_id_is_non_blank(cls, value: str) -> str:
        return non_blank(value, "tenant_id")

    @field_validator("inserted_signal_ids", "duplicate_signal_ids", "unavailable_source_refs")
    @classmethod
    def result_references_are_unique(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        unique_values(value, getattr(info, "field_name", "references"))
        return value


class CXPlatformEvidenceAdapter:
    """Normalize current CX Platform reads into Autopilot source signals."""

    def __init__(
        self,
        source: CXPlatformEvidencePort,
        *,
        tenant_id: str,
        stale_after: timedelta = timedelta(days=30),
    ) -> None:
        self.source = source
        self.tenant_id = non_blank(tenant_id, "tenant_id")
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        self.stale_after = stale_after

    def ingest(
        self,
        signal_store: OperationalSignalStore,
        *,
        as_of: datetime | None = None,
        page_size: int = 100,
    ) -> EvidenceIngestionResult:
        """Read, normalize, enrich, and idempotently store one tenant's evidence."""

        if page_size < 1:
            raise ValueError("page_size must be positive")
        if as_of is not None:
            as_of = aware_timestamp(as_of, "as_of")

        records: dict[tuple[str, str, str], SourceRecord] = {}
        unavailable: set[str] = set()
        self._collect_pages("event", records, self.source.list_events, page_size)
        self._collect_pages("ticket", records, self.source.list_tickets, page_size)
        self._collect_pages("outcome", records, self.source.list_outcomes, page_size)

        ticket_ids = sorted(record_id for resource, record_id, _ in records if resource == "ticket")
        for ticket_id in ticket_ids:
            detail = self.source.read_ticket(ticket_id)
            if detail is None:
                unavailable.add(f"{_SOURCE_SYSTEM}:tickets:{ticket_id}")
                continue
            self._collect_ticket_detail(records, detail, ticket_id)

        conversation_ids = self._correlation_ids(records, "conversation_id")
        known_conversations = {
            record_id for resource, record_id, _ in records if resource == "conversation"
        }
        for conversation_id in sorted(conversation_ids - known_conversations):
            conversation = self.source.read_conversation(conversation_id)
            if conversation is None:
                unavailable.add(f"{_SOURCE_SYSTEM}:conversations:{conversation_id}")
                continue
            self._collect_conversation_read(records, conversation, conversation_id)

        execution_ids = self._correlation_ids(records, "execution_id")
        known_executions = {
            record_id for resource, record_id, _ in records if resource == "execution"
        }
        for execution_id in sorted(execution_ids - known_executions):
            execution = self.source.read_execution(execution_id)
            if execution is None:
                unavailable.add(f"{_SOURCE_SYSTEM}:executions:{execution_id}")
                continue
            self._add_record(records, "execution", execution, "execution_id")

        signals = self._normalize_records(records, unavailable, as_of)
        stored_signals: list[OperationalSignal] = []
        inserted: list[str] = []
        duplicates: list[str] = []
        for signal in sorted(signals, key=lambda item: (item.occurred_at, item.signal_id)):
            existing = signal_store.get_by_source_identity(
                tenant_id=self.tenant_id,
                source_system=signal.source_system,
                source_record_type=signal.source_record_type,
                source_record_id=signal.source_record_id,
                source_record_version=signal.source_record_version,
            )
            if existing is None:
                stored = signal_store.ingest(signal)
                inserted.append(stored.signal_id)
            else:
                if existing != signal:
                    raise CXPlatformDataError(
                        f"normalized source identity changed: {signal.source_identity!r}"
                    )
                stored = existing
                duplicates.append(existing.signal_id)
            stored_signals.append(stored)

        quality_counts: dict[str, int] = {}
        for signal in stored_signals:
            key = signal.evidence_quality.value
            quality_counts[key] = quality_counts.get(key, 0) + 1
        return EvidenceIngestionResult(
            tenant_id=self.tenant_id,
            signals=tuple(stored_signals),
            inserted_signal_ids=tuple(inserted),
            duplicate_signal_ids=tuple(duplicates),
            unavailable_source_refs=tuple(sorted(unavailable)),
            quality_counts=quality_counts,
        )

    def _collect_pages(
        self,
        resource: str,
        records: dict[tuple[str, str, str], SourceRecord],
        reader: Callable[..., Sequence[SourceRecord]],
        page_size: int,
    ) -> None:
        after: str | None = None
        while True:
            page = tuple(
                _as_mapping(item, resource) for item in reader(after=after, limit=page_size)
            )
            if not page:
                return
            for record in page:
                self._add_record(records, resource, record, _id_field(resource))
            next_after = _text(page[-1].get(_id_field(resource)))
            if next_after is None or next_after == after:
                raise CXPlatformDataError(f"{resource} cursor did not advance")
            after = next_after
            if len(page) < page_size:
                return

    def _collect_ticket_detail(
        self,
        records: dict[tuple[str, str, str], SourceRecord],
        detail_value: object,
        ticket_id: str,
    ) -> None:
        detail = _as_mapping(detail_value, f"ticket detail {ticket_id}")
        for field_name, resource, id_field in (
            ("ticket", "ticket", "ticket_id"),
            ("conversation", "conversation", "conversation_id"),
        ):
            child = _as_mapping(detail.get(field_name), f"ticket detail {ticket_id}.{field_name}")
            self._add_record(records, resource, child, id_field)
        self._collect_conversation_children(records, detail)

    def _collect_conversation_children(
        self,
        records: dict[tuple[str, str, str], SourceRecord],
        parent: SourceRecord,
    ) -> None:
        for resource, id_field in (
            ("message", "message_id"),
            ("escalation", "escalation_id"),
            ("approval", "approval_id"),
            ("outcome", "outcome_id"),
            ("csat", "csat_id"),
        ):
            values = parent.get(f"{resource}s", ())
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
                raise CXPlatformDataError(f"{resource}s must be a sequence")
            for value in values:
                self._add_record(records, resource, _as_mapping(value, resource), id_field)

    def _collect_conversation_read(
        self,
        records: dict[tuple[str, str, str], SourceRecord],
        conversation_value: object,
        conversation_id: str,
    ) -> None:
        payload = _as_mapping(conversation_value, f"conversation {conversation_id}")
        conversation = payload.get("conversation")
        if conversation is not None:
            self._add_record(
                records,
                "conversation",
                _as_mapping(conversation, f"conversation {conversation_id}.conversation"),
                "conversation_id",
            )
            ticket = payload.get("ticket")
            if ticket is not None:
                self._add_record(
                    records,
                    "ticket",
                    _as_mapping(ticket, f"conversation {conversation_id}.ticket"),
                    "ticket_id",
                )
        else:
            self._add_record(records, "conversation", payload, "conversation_id")
        self._collect_conversation_children(records, payload)

    def _add_record(
        self,
        records: dict[tuple[str, str, str], SourceRecord],
        resource: str,
        value: object,
        id_field: str,
    ) -> None:
        record = _as_mapping(value, resource)
        record_id = _required_text(record.get(id_field), f"{resource}.{id_field}")
        version = _text(record.get("source_record_version")) or _text(record.get("version")) or ""
        key = (resource, record_id, version)
        previous = records.get(key)
        if previous is not None and _canonical_json(previous) != _canonical_json(record):
            raise CXPlatformDataError(f"conflicting {resource} source record: {record_id}")
        records[key] = record

    def _correlation_ids(
        self,
        records: Mapping[tuple[str, str, str], SourceRecord],
        field_name: str,
    ) -> set[str]:
        values: set[str] = set()
        for record in records.values():
            value = _text(record.get(field_name))
            if value is not None:
                values.add(value)
        return values

    def _normalize_records(
        self,
        records: Mapping[tuple[str, str, str], SourceRecord],
        unavailable: set[str],
        as_of: datetime | None,
    ) -> tuple[OperationalSignal, ...]:
        ticket_context, conversation_context = _contexts(records)
        execution_context: dict[str, SourceRecord] = {}
        for (resource, record_id, _), record in records.items():
            if resource == "execution":
                execution_context[record_id] = record
        trace_by_execution = {
            execution_id: trace
            for execution_id, record in execution_context.items()
            for trace in [_text(record.get("trace_reference"))]
            if trace is not None
        }
        agent_by_execution = {
            execution_id: agent
            for execution_id, record in execution_context.items()
            for agent in [_text(record.get("agent_id"))]
            if agent is not None
        }
        normalized: list[OperationalSignal] = []
        for (resource, record_id, version), record in sorted(records.items()):
            normalized.append(
                self._normalize_one(
                    resource,
                    record_id,
                    version or None,
                    record,
                    ticket_context,
                    conversation_context,
                    trace_by_execution,
                    agent_by_execution,
                    unavailable,
                    as_of,
                )
            )
        return tuple(normalized)

    def _normalize_one(
        self,
        resource: str,
        record_id: str,
        version: str | None,
        record: SourceRecord,
        ticket_context: Mapping[str, tuple[str | None, str | None]],
        conversation_context: Mapping[str, tuple[str | None, str | None]],
        trace_by_execution: Mapping[str, str],
        agent_by_execution: Mapping[str, str],
        unavailable: set[str],
        as_of: datetime | None,
    ) -> OperationalSignal:
        occurred_at = _required_datetime(record.get(_timestamp_field(resource)), resource)
        ticket_id = _text(record.get("ticket_id"))
        conversation_id = _text(record.get("conversation_id"))
        customer_id = _text(record.get("customer_id"))
        execution_id = _text(record.get("execution_id"))
        if ticket_id is not None:
            context_customer, context_conversation = ticket_context.get(ticket_id, (None, None))
            customer_id, conversation_id = _merge_identity(
                customer_id,
                context_customer,
                conversation_id,
                context_conversation,
            )
        if conversation_id is not None:
            context_customer, context_ticket = conversation_context.get(
                conversation_id, (None, None)
            )
            customer_id, ticket_id = _merge_identity(
                customer_id,
                context_customer,
                ticket_id,
                context_ticket,
            )
        trace_id = _record_value(record, resource, "trace_reference")
        agent_id = _text(record.get("agent_id"))
        if resource == "event" and _text(record.get("actor_type")) == "AI_AGENT":
            agent_id = agent_id or _text(record.get("actor_id"))
        if execution_id is not None:
            trace_id = trace_id or trace_by_execution.get(execution_id)
            agent_id = agent_id or agent_by_execution.get(execution_id)
        source_reference = _source_reference(resource, record_id, version)
        conflict = _has_context_conflict(
            record,
            ticket_id=ticket_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            ticket_context=ticket_context,
            conversation_context=conversation_context,
        )
        quality = _quality(
            record,
            resource=resource,
            occurred_at=occurred_at,
            as_of=as_of,
            stale_after=self.stale_after,
            partial=_is_partial(resource, record, ticket_id, conversation_id, execution_id),
            conflicting=conflict,
        )
        if (
            quality is EvidenceQuality.COMPLETE
            and execution_id is not None
            and f"{_SOURCE_SYSTEM}:executions:{execution_id}" in unavailable
        ):
            quality = EvidenceQuality.PARTIAL
        attributes = _normalized_attributes(resource, record)
        evidence_refs = [source_reference]
        for evidence_id in _record_text_list(record, resource, "evidence_ids"):
            evidence_refs.append(f"{_SOURCE_SYSTEM}:evidence:{evidence_id}")
        signal_type = _signal_type(resource, record)
        return OperationalSignal(
            signal_id=_stable_signal_id(self.tenant_id, resource, record_id, version),
            source_system=_SOURCE_SYSTEM,
            source_record_type=_record_type(resource),
            source_record_id=record_id,
            source_record_version=version,
            signal_type=signal_type,
            occurred_at=occurred_at,
            tenant_id=self.tenant_id,
            interaction_id=conversation_id,
            journey_id=ticket_id,
            customer_id=customer_id,
            agent_id=agent_id,
            execution_id=execution_id,
            trace_id=trace_id,
            source_reference=source_reference,
            payload_reference=source_reference,
            normalized_attributes=attributes,
            evidence_quality=quality,
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        )


def _cursor_params(after: str | None, limit: int) -> dict[str, str]:
    if limit < 1:
        raise ValueError("limit must be positive")
    return {
        **({"after": after} if after is not None else {}),
        "limit": str(limit),
    }


def _records(value: object, resource: str) -> tuple[SourceRecord, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CXPlatformDataError(f"CX Platform {resource} response must be a list")
    return tuple(_as_mapping(item, resource) for item in value)


def _optional_record(value: object | None, resource: str) -> SourceRecord | None:
    return None if value is None else _as_mapping(value, resource)


def _as_mapping(value: object, label: str) -> SourceRecord:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    raise CXPlatformDataError(f"CX Platform {label} response must be an object")


def _id_field(resource: str) -> str:
    return {
        "event": "event_id",
        "ticket": "ticket_id",
        "outcome": "outcome_id",
    }.get(resource, f"{resource}_id")


def _timestamp_field(resource: str) -> str:
    return {
        "event": "occurred_at",
        "ticket": "created_at",
        "conversation": "started_at",
        "message": "created_at",
        "escalation": "created_at",
        "approval": "requested_at",
        "outcome": "created_at",
        "csat": "submitted_at",
        "execution": "started_at",
    }[resource]


def _record_type(resource: str) -> str:
    return {
        "event": "cx_event",
        "ticket": "ticket",
        "conversation": "conversation",
        "message": "message",
        "escalation": "escalation",
        "approval": "approval",
        "outcome": "outcome",
        "csat": "csat",
        "execution": "execution_reference",
    }[resource]


def _source_name(resource: str) -> str:
    return {
        "event": "events",
        "ticket": "tickets",
        "conversation": "conversations",
        "message": "messages",
        "escalation": "escalations",
        "approval": "approvals",
        "outcome": "outcomes",
        "csat": "csat",
        "execution": "executions",
    }[resource]


def _contexts(
    records: Mapping[tuple[str, str, str], SourceRecord],
) -> tuple[
    dict[str, tuple[str | None, str | None]],
    dict[str, tuple[str | None, str | None]],
]:
    tickets: dict[str, tuple[str | None, str | None]] = {}
    conversations: dict[str, tuple[str | None, str | None]] = {}
    for (resource, record_id, _), record in records.items():
        if resource == "ticket":
            tickets[record_id] = (
                _text(record.get("customer_id")),
                _text(record.get("conversation_id")),
            )
        elif resource == "conversation":
            conversations[record_id] = (
                _text(record.get("customer_id")),
                _text(record.get("ticket_id")),
            )
    return tickets, conversations


def _source_reference(resource: str, record_id: str, version: str | None) -> str:
    base = f"{_SOURCE_SYSTEM}:{_source_name(resource)}:{record_id}"
    return f"{base}@{version}" if version is not None else base


def _merge_identity(
    first_value: str | None,
    context_first: str | None,
    second_value: str | None,
    context_second: str | None,
) -> tuple[str | None, str | None]:
    return first_value or context_first, second_value or context_second


def _has_context_conflict(
    record: SourceRecord,
    *,
    ticket_id: str | None,
    conversation_id: str | None,
    customer_id: str | None,
    ticket_context: Mapping[str, tuple[str | None, str | None]],
    conversation_context: Mapping[str, tuple[str | None, str | None]],
) -> bool:
    original_ticket = _text(record.get("ticket_id"))
    original_conversation = _text(record.get("conversation_id"))
    original_customer = _text(record.get("customer_id"))
    if original_ticket is not None and ticket_id != original_ticket:
        return True
    if original_conversation is not None and conversation_id != original_conversation:
        return True
    if original_customer is not None and customer_id != original_customer:
        return True
    if ticket_id is not None:
        context_customer, context_conversation = ticket_context.get(ticket_id, (None, None))
        if (
            context_customer is not None
            and original_customer is not None
            and context_customer != original_customer
        ):
            return True
        if (
            context_conversation is not None
            and original_conversation is not None
            and context_conversation != original_conversation
        ):
            return True
    if conversation_id is not None:
        context_customer, context_ticket = conversation_context.get(conversation_id, (None, None))
        if (
            context_customer is not None
            and original_customer is not None
            and context_customer != original_customer
        ):
            return True
        if (
            context_ticket is not None
            and original_ticket is not None
            and context_ticket != original_ticket
        ):
            return True
    return False


def _is_partial(
    resource: str,
    record: SourceRecord,
    ticket_id: str | None,
    conversation_id: str | None,
    execution_id: str | None,
) -> bool:
    if resource == "message":
        return conversation_id is None
    if resource in {"escalation", "approval", "outcome"}:
        return ticket_id is None
    if resource == "execution":
        return ticket_id is None or conversation_id is None
    if resource == "event":
        event_type = (_text(record.get("event_type")) or "").lower()
        if event_type.startswith(("agent.", "approval.", "outcome.")):
            return ticket_id is None or conversation_id is None or execution_id is None
    return False


def _quality(
    record: SourceRecord,
    *,
    resource: str,
    occurred_at: datetime,
    as_of: datetime | None,
    stale_after: timedelta,
    partial: bool,
    conflicting: bool,
) -> EvidenceQuality:
    explicit = (_text(record.get("evidence_quality")) or "").upper()
    if explicit in {quality.value for quality in EvidenceQuality}:
        return EvidenceQuality(explicit)
    if conflicting:
        return EvidenceQuality.CONFLICTING
    if as_of is not None and occurred_at <= as_of - stale_after:
        return EvidenceQuality.STALE
    if partial:
        return EvidenceQuality.PARTIAL
    return EvidenceQuality.COMPLETE


def _normalized_attributes(resource: str, record: SourceRecord) -> dict[str, JsonValue]:
    if resource == "event":
        raw_data = record.get("data", {})
        data = raw_data if isinstance(raw_data, Mapping) else {}
        attributes = _bounded_attributes(data, _EVENT_ATTRIBUTE_KEYS)
        event_type = _text(record.get("event_type"))
        if event_type is not None:
            attributes["event_type"] = event_type
        return attributes
    keys_by_resource: dict[str, frozenset[str]] = {
        "ticket": frozenset({"priority", "resolution_code", "status"}),
        "conversation": frozenset({"status"}),
        "message": frozenset({"actor_type", "actor_id"}),
        "escalation": frozenset({"reason", "status", "actions_attempted", "tool_result_refs"}),
        "approval": frozenset({"status", "tool_id", "harness_request_id"}),
        "outcome": frozenset(
            {
                "resolution_code",
                "resolved",
                "escalated",
                "turn_count",
                "tool_call_count",
                "tool_failure_count",
                "approval_required",
                "approval_result",
                "duration",
                "csat_score",
                "escalation_id",
                "tool_ids",
                "evidence_ids",
            }
        ),
        "csat": frozenset({"score"}),
        "execution": frozenset({"agent_id", "agent_version", "trace_reference", "outcome_status"}),
    }
    return _bounded_attributes(record, keys_by_resource[resource])


def _bounded_attributes(record: Mapping[str, Any], keys: frozenset[str]) -> dict[str, JsonValue]:
    attributes: dict[str, JsonValue] = {}
    for key in sorted(keys):
        value = record.get(key)
        bounded = _bounded_json_value(value)
        if bounded is not None:
            attributes[key] = bounded
    return attributes


def _bounded_json_value(value: object) -> JsonValue | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > _MAX_ATTRIBUTE_TEXT:
            return None
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_ATTRIBUTE_LIST:
            return None
        items: list[JsonValue] = []
        for item in value:
            bounded = _bounded_json_value(item)
            if bounded is None or isinstance(bounded, (dict, list)):
                return None
            items.append(bounded)
        return items
    return None


def _signal_type(resource: str, record: SourceRecord) -> str:
    if resource == "event":
        return _required_text(record.get("event_type"), "event.event_type")
    if resource == "message":
        actor_type = (_text(record.get("actor_type")) or "system").lower()
        return f"message.{actor_type}"
    return {
        "ticket": "ticket.recorded",
        "conversation": "conversation.recorded",
        "escalation": "ticket.escalated",
        "approval": "approval.recorded",
        "outcome": "outcome.recorded",
        "csat": "csat.received",
        "execution": "agent.execution_reference",
    }[resource]


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value if value.strip() else None
    return None


def _record_value(record: SourceRecord, resource: str, field_name: str) -> str | None:
    direct = _text(record.get(field_name))
    if direct is not None or resource != "event":
        return direct
    data = record.get("data")
    return _text(data.get(field_name)) if isinstance(data, Mapping) else None


def _record_text_list(record: SourceRecord, resource: str, field_name: str) -> tuple[str, ...]:
    direct = _text_list(record.get(field_name))
    if direct or resource != "event":
        return direct
    data = record.get("data")
    return _text_list(data.get(field_name)) if isinstance(data, Mapping) else ()


def _required_text(value: object, field_name: str) -> str:
    text = _text(value)
    if text is None:
        raise CXPlatformDataError(f"{field_name} must be a non-blank string")
    return text


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in (_text(item) for item in value) if item is not None)


def _required_datetime(value: object, resource: str) -> datetime:
    if isinstance(value, datetime):
        return aware_timestamp(value, f"{resource} timestamp")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CXPlatformDataError(f"{resource} timestamp is invalid") from exc
        return aware_timestamp(parsed, f"{resource} timestamp")
    raise CXPlatformDataError(f"{resource} timestamp is required")


def _stable_signal_id(
    tenant_id: str,
    resource: str,
    record_id: str,
    version: str | None,
) -> str:
    value = "|".join((tenant_id, _SOURCE_SYSTEM, resource, record_id, version or ""))
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"signal_{digest}"


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "CXPlatformDataError",
    "CXPlatformEvidenceAdapter",
    "CXPlatformEvidencePort",
    "CXPlatformHTTPSource",
    "CXPlatformSourceError",
    "EvidenceIngestionResult",
]
