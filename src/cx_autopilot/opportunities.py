"""Deterministic opportunity detectors for normalized CX signals."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field, model_validator

from .contracts import (
    EvidenceQuality,
    OperationalSignal,
    Opportunity,
    OpportunityPattern,
)
from .contracts.common import ImmutableModel

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_EXCLUDED_QUALITIES = {EvidenceQuality.CONFLICTING, EvidenceQuality.UNAVAILABLE}
_QUALITY_SCORE = {
    EvidenceQuality.COMPLETE: 1.0,
    EvidenceQuality.PARTIAL: 0.65,
    EvidenceQuality.STALE: 0.5,
    EvidenceQuality.CONFLICTING: 0.0,
    EvidenceQuality.UNAVAILABLE: 0.0,
}
_UNRESOLVED_CODES = {
    "UNRESOLVED",
    "DEPENDENCY_UNAVAILABLE",
    "ESCALATED_TO_HUMAN",
    "FAILED",
    "UNKNOWN",
}


class OpportunityDetectionConfig(ImmutableModel):
    """Typed thresholds for the initial deterministic detector set."""

    window_size: timedelta = timedelta(days=7)
    minimum_repetitions: int = Field(default=2, ge=2)
    minimum_sequence_length: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def thresholds_are_valid(self) -> "OpportunityDetectionConfig":
        if self.window_size <= timedelta(0):
            raise ValueError("window_size must be positive")
        return self


@dataclass(frozen=True)
class _Candidate:
    detector_name: str
    pattern_type: OpportunityPattern
    pattern_key: str
    title: str
    operation_mode: str
    signals: tuple[OperationalSignal, ...]
    base_impact: float
    base_effort: float
    base_risk: float
    risk_factors: tuple[str, ...]


@dataclass(frozen=True)
class _Detector:
    name: str
    pattern_type: OpportunityPattern
    title: str
    extractor: Callable[[OperationalSignal], str | None]
    operation_mode: str
    base_impact: float
    base_effort: float
    base_risk: float
    risk_factors: tuple[str, ...]


class OpportunityDiscoverer:
    """Discover the same opportunity set for the same normalized signal set."""

    def __init__(self, config: OpportunityDetectionConfig | None = None) -> None:
        self.config = config or OpportunityDetectionConfig()
        self._detectors = (
            _Detector(
                name="repeated_operation_sequence",
                pattern_type=OpportunityPattern.REPEATED_OPERATION_SEQUENCE,
                title="Repeated operation sequence",
                extractor=self._sequence_key,
                operation_mode="journey",
                base_impact=0.65,
                base_effort=0.7,
                base_risk=0.15,
                risk_factors=(),
            ),
            _Detector(
                name="repeated_escalation",
                pattern_type=OpportunityPattern.REPEATED_ESCALATION,
                title="Repeated escalation cause",
                extractor=self._escalation_key,
                operation_mode="source",
                base_impact=0.9,
                base_effort=0.8,
                base_risk=0.2,
                risk_factors=("human_handoff_boundary",),
            ),
            _Detector(
                name="repeat_contact_after_unresolved_path",
                pattern_type=OpportunityPattern.REPEAT_CONTACT_UNRESOLVED,
                title="Repeat contact after an unresolved path",
                extractor=self._unresolved_key,
                operation_mode="journey",
                base_impact=0.9,
                base_effort=0.75,
                base_risk=0.2,
                risk_factors=(),
            ),
            _Detector(
                name="repeated_lookup",
                pattern_type=OpportunityPattern.REPEATED_LOOKUP,
                title="Repeated lookup operation",
                extractor=self._lookup_key,
                operation_mode="source",
                base_impact=0.55,
                base_effort=0.55,
                base_risk=0.1,
                risk_factors=(),
            ),
            _Detector(
                name="repeated_approval_wait",
                pattern_type=OpportunityPattern.REPEATED_APPROVAL_WAIT,
                title="Repeated approval wait",
                extractor=self._approval_key,
                operation_mode="source",
                base_impact=0.7,
                base_effort=0.65,
                base_risk=0.25,
                risk_factors=("approval_boundary",),
            ),
            _Detector(
                name="repeated_policy_denial",
                pattern_type=OpportunityPattern.REPEATED_POLICY_DENIAL,
                title="Repeated policy denial",
                extractor=self._policy_denial_key,
                operation_mode="source",
                base_impact=0.75,
                base_effort=0.45,
                base_risk=0.4,
                risk_factors=("governance_boundary",),
            ),
            _Detector(
                name="repeated_human_workaround",
                pattern_type=OpportunityPattern.REPEATED_HUMAN_WORKAROUND,
                title="Repeated human workaround",
                extractor=self._workaround_key,
                operation_mode="journey",
                base_impact=0.8,
                base_effort=0.9,
                base_risk=0.25,
                risk_factors=("manual_workaround",),
            ),
            _Detector(
                name="repeated_operator_correction",
                pattern_type=OpportunityPattern.REPEATED_OPERATOR_CORRECTION,
                title="Repeated operator correction",
                extractor=self._correction_key,
                operation_mode="journey",
                base_impact=0.8,
                base_effort=0.85,
                base_risk=0.2,
                risk_factors=("human_correction_boundary",),
            ),
        )

    def discover(
        self,
        signals: Iterable[OperationalSignal],
        *,
        tenant_id: str | None = None,
    ) -> tuple[Opportunity, ...]:
        """Return deterministic opportunities grouped by tenant and fixed window."""

        unique_signals = _deduplicate_signals(signals)
        if tenant_id is not None and any(
            signal.tenant_id != tenant_id for signal in unique_signals
        ):
            raise ValueError("all signals must belong to the requested tenant")
        windows: dict[tuple[str, datetime, datetime], list[OperationalSignal]] = defaultdict(list)
        for signal in unique_signals:
            window_start, window_end = _window(signal.occurred_at, self.config.window_size)
            windows[(signal.tenant_id, window_start, window_end)].append(signal)

        opportunities: list[Opportunity] = []
        for (window_tenant, window_start, window_end), window_signals in sorted(windows.items()):
            usable = tuple(
                signal
                for signal in sorted(
                    window_signals, key=lambda item: (item.occurred_at, item.signal_id)
                )
                if signal.evidence_quality not in _EXCLUDED_QUALITIES
            )
            for detector in self._detectors:
                grouped: dict[str, list[OperationalSignal]] = defaultdict(list)
                for signal in usable:
                    key = detector.extractor(signal)
                    if key is not None:
                        grouped[key].append(signal)
                for pattern_key, grouped_signals in sorted(grouped.items()):
                    candidate = _Candidate(
                        detector_name=detector.name,
                        pattern_type=detector.pattern_type,
                        pattern_key=pattern_key,
                        title=detector.title,
                        operation_mode=detector.operation_mode,
                        signals=tuple(grouped_signals),
                        base_impact=detector.base_impact,
                        base_effort=detector.base_effort,
                        base_risk=detector.base_risk,
                        risk_factors=detector.risk_factors,
                    )
                    opportunity = self._build_opportunity(
                        candidate,
                        tenant_id=window_tenant,
                        window_start=window_start,
                        window_end=window_end,
                    )
                    if opportunity is not None:
                        opportunities.append(opportunity)
        return tuple(sorted(opportunities, key=lambda item: item.opportunity_id))

    def _build_opportunity(
        self,
        candidate: _Candidate,
        *,
        tenant_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> Opportunity | None:
        signals = tuple(
            sorted(candidate.signals, key=lambda item: (item.occurred_at, item.signal_id))
        )
        occurrence_keys = tuple(
            sorted(
                {
                    _source_occurrence(signal)
                    if candidate.operation_mode == "source"
                    else _journey_occurrence(signal)
                    for signal in signals
                }
            )
        )
        if len(occurrence_keys) < self.config.minimum_repetitions:
            return None
        source_signal_ids = tuple(sorted({signal.signal_id for signal in signals}))
        evidence_refs = tuple(
            sorted({evidence_ref for signal in signals for evidence_ref in signal.evidence_refs})
        )
        frequency = float(len(occurrence_keys))
        quality = sum(_QUALITY_SCORE[signal.evidence_quality] for signal in signals) / len(signals)
        confidence = min(1.0, round(0.5 * quality + 0.5 * min(1.0, frequency / 4.0), 6))
        impact = min(1.0, round(candidate.base_impact + 0.05 * max(0.0, frequency - 2.0), 6))
        effort = min(1.0, round(candidate.base_effort + 0.03 * max(0.0, frequency - 2.0), 6))
        predictability = min(1.0, round(0.55 + 0.1 * min(4.0, frequency) * quality, 6))
        risk = min(
            1.0,
            round(
                candidate.base_risk
                + 0.15 * any(signal.evidence_quality is EvidenceQuality.STALE for signal in signals)
                + 0.1
                * any(signal.evidence_quality is EvidenceQuality.PARTIAL for signal in signals),
                6,
            ),
        )
        risk_factors = set(candidate.risk_factors)
        if any(signal.evidence_quality is EvidenceQuality.STALE for signal in signals):
            risk_factors.add("stale_evidence")
        if any(signal.evidence_quality is EvidenceQuality.PARTIAL for signal in signals):
            risk_factors.add("partial_correlation")
        source_identities = tuple(sorted(_source_identity(signal) for signal in signals))
        identity_payload = {
            "tenant_id": tenant_id,
            "detector_name": candidate.detector_name,
            "pattern_key": candidate.pattern_key,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "source_identities": source_identities,
        }
        opportunity_id = (
            "opportunity_"
            + hashlib.sha256(
                json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:32]
        )
        latest_signal = max(signals, key=lambda item: (item.occurred_at, item.signal_id))
        return Opportunity(
            opportunity_id=opportunity_id,
            tenant_id=tenant_id,
            title=f"{candidate.title}: {candidate.pattern_key}",
            description=(
                f"The deterministic {candidate.detector_name} detector found "
                f"{len(occurrence_keys)} repeated occurrences for {candidate.pattern_key} "
                f"in the half-open evidence window."
            ),
            source_signal_ids=source_signal_ids,
            evidence_refs=evidence_refs,
            frequency_estimate=frequency,
            impact_estimate=impact,
            confidence=confidence,
            status="DISCOVERED",
            created_at=latest_signal.occurred_at,
            detector_name=candidate.detector_name,
            pattern_type=candidate.pattern_type,
            pattern_key=candidate.pattern_key,
            window_start=window_start,
            window_end=window_end,
            occurrence_keys=occurrence_keys,
            operational_effort_estimate=effort,
            predictability_estimate=predictability,
            risk_estimate=risk,
            risk_factors=tuple(sorted(risk_factors)),
        )

    def _sequence_key(self, signal: OperationalSignal) -> str | None:
        for field_name in ("operation_sequence", "sequence", "action_sequence"):
            value = signal.normalized_attributes.get(field_name)
            sequence = _string_sequence(value)
            if sequence is not None and len(sequence) >= self.config.minimum_sequence_length:
                return "sequence:" + ">".join(sequence)
        return None

    @staticmethod
    def _escalation_key(signal: OperationalSignal) -> str | None:
        if (
            signal.source_record_type != "escalation"
            and "escalat" not in signal.signal_type.lower()
        ):
            return None
        reason = _attribute_text(signal.normalized_attributes, ("reason", "cause"))
        return None if reason is None else "reason:" + _token(reason)

    @staticmethod
    def _unresolved_key(signal: OperationalSignal) -> str | None:
        attributes = signal.normalized_attributes
        resolved = attributes.get("resolved")
        unresolved = attributes.get("unresolved")
        resolution_code = _attribute_text(attributes, ("resolution_code", "outcome_type"))
        is_unresolved = (
            _is_false(resolved)
            or _is_true(unresolved)
            or (
                resolution_code is not None
                and _token(resolution_code) in {code.lower() for code in _UNRESOLVED_CODES}
            )
            or "repeat.contact" in signal.signal_type.lower()
        )
        if not is_unresolved:
            return None
        operation = _operation_value(signal)
        return "path:" + _token(operation or resolution_code or "unresolved")

    @staticmethod
    def _lookup_key(signal: OperationalSignal) -> str | None:
        operation = _operation_value(signal)
        if operation is None:
            return None
        signal_type = signal.signal_type.lower()
        has_lookup_shape = any(
            value in signal_type
            for value in ("lookup", "tool_called", "tool_succeeded", "tool_failed")
        ) or any(
            key in signal.normalized_attributes
            for key in ("tool_id", "business_operation", "lookup_type")
        )
        return None if not has_lookup_shape else "operation:" + _token(operation)

    @staticmethod
    def _approval_key(signal: OperationalSignal) -> str | None:
        attributes = signal.normalized_attributes
        signal_type = signal.signal_type.lower()
        pending = _is_true(attributes.get("approval_required")) or _token(
            _attribute_text(attributes, ("status",)) or ""
        ) in {"pending", "waiting_approval"}
        if signal.source_record_type != "approval" and "approval.requested" not in signal_type:
            return None
        if not pending and signal.source_record_type == "approval":
            return None
        operation = _operation_value(signal) or _attribute_text(attributes, ("action_summary",))
        return None if operation is None else "operation:" + _token(operation)

    @staticmethod
    def _policy_denial_key(signal: OperationalSignal) -> str | None:
        attributes = signal.normalized_attributes
        signal_type = signal.signal_type.lower()
        reason = _attribute_text(attributes, ("permission_reason_code", "policy_id"))
        policy_shaped = (
            reason is not None
            or "permission" in signal_type
            or "policy" in signal_type
            or "denied" in signal_type
            or "denial" in signal_type
            or _is_true(attributes.get("policy_denied"))
        )
        if reason is None and policy_shaped:
            reason = _attribute_text(attributes, ("cause", "reason"))
        denied = policy_shaped or _token(_attribute_text(attributes, ("result_status",)) or "") in {
            "denied",
            "rejected",
        }
        if denied and reason is None:
            reason = _attribute_text(attributes, ("cause", "reason")) or "denied"
        return None if not denied or reason is None else "policy:" + _token(reason)

    @staticmethod
    def _workaround_key(signal: OperationalSignal) -> str | None:
        attributes = signal.normalized_attributes
        signal_type = signal.signal_type.lower()
        value = _attribute_text(
            attributes,
            ("workaround_type", "manual_action", "human_workaround", "operator_action"),
        )
        if value is None and not any(token in signal_type for token in ("workaround", "manual")):
            return None
        return "workaround:" + _token(value or "manual_action")

    @staticmethod
    def _correction_key(signal: OperationalSignal) -> str | None:
        attributes = signal.normalized_attributes
        signal_type = signal.signal_type.lower()
        value = _attribute_text(
            attributes,
            ("correction_type", "operator_correction", "correction", "corrected_field"),
        )
        if value is None and "correction" not in signal_type:
            return None
        return "correction:" + _token(value or "operator_correction")


def discover_opportunities(
    signals: Iterable[OperationalSignal],
    *,
    config: OpportunityDetectionConfig | None = None,
    tenant_id: str | None = None,
) -> tuple[Opportunity, ...]:
    """Convenience boundary for deterministic opportunity discovery."""

    return OpportunityDiscoverer(config).discover(signals, tenant_id=tenant_id)


def _deduplicate_signals(signals: Iterable[OperationalSignal]) -> tuple[OperationalSignal, ...]:
    by_source: dict[tuple[str, str, str, str, str | None], OperationalSignal] = {}
    for signal in signals:
        source_key = (signal.tenant_id, *signal.source_identity)
        previous = by_source.get(source_key)
        if previous is not None and previous != signal:
            raise ValueError(
                f"source identity has conflicting signal content: {signal.source_identity!r}"
            )
        by_source[source_key] = signal
    return tuple(sorted(by_source.values(), key=lambda item: (item.occurred_at, item.signal_id)))


def _window(timestamp: datetime, size: timedelta) -> tuple[datetime, datetime]:
    elapsed = timestamp - _EPOCH
    slot = elapsed // size
    start = _EPOCH + slot * size
    return start, start + size


def _source_identity(signal: OperationalSignal) -> str:
    source_system, record_type, record_id, version = signal.source_identity
    return ":".join((source_system, record_type, record_id, version or ""))


def _source_occurrence(signal: OperationalSignal) -> str:
    return "source:" + _source_identity(signal)


def _journey_occurrence(signal: OperationalSignal) -> str:
    if signal.journey_id is not None:
        return "journey:" + signal.journey_id
    if signal.interaction_id is not None:
        return "interaction:" + signal.interaction_id
    if signal.customer_id is not None:
        return "customer:" + signal.customer_id
    return _source_occurrence(signal)


def _operation_value(signal: OperationalSignal) -> str | None:
    return _attribute_text(
        signal.normalized_attributes,
        ("operation", "business_operation", "lookup_type", "tool_id"),
    )


def _attribute_text(attributes: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _string_sequence(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    values = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    return values if len(values) == len(value) else None


def _is_true(value: object) -> bool:
    return value is True or isinstance(value, str) and value.strip().lower() in {"true", "yes", "1"}


def _is_false(value: object) -> bool:
    return (
        value is False or isinstance(value, str) and value.strip().lower() in {"false", "no", "0"}
    )


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9_.:-]+", "_", value.strip().lower()).strip("_") or "unspecified"


__all__ = [
    "OpportunityDetectionConfig",
    "OpportunityDiscoverer",
    "discover_opportunities",
]
