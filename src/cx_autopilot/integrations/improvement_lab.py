"""Improvement Lab boundary for candidate evaluation and comparison.

The Lab owns evaluation cases, evaluator semantics, failure taxonomy, root
cause analysis, comparison, and promotion evidence.  Autopilot submits opaque
Lab candidates and manifests, then stores only stable references and lineage.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, cast

from ..contracts import CandidateReference, EvaluationReference, OperationalDisposition
from ..contracts.common import non_blank, unique_values
from ..storage.ports import EvaluationReferenceStore

EVALUATION_SUCCEEDED = "EVALUATION_SUCCEEDED"
EVALUATION_FAILED = "EVALUATION_FAILED"


class ImprovementLabEvaluationError(ValueError):
    """Raised when the Lab request is invalid before an evaluation starts."""


class LabEvaluationRunnerPort(Protocol):
    """Minimal public Lab runner boundary."""

    def run_sync(
        self,
        dataset: object,
        candidate: object,
        manifest: object,
        *,
        repeat: int = 1,
    ) -> object:
        """Evaluate one opaque Lab candidate."""


class LabComparisonPort(Protocol):
    """Minimal public Lab comparison boundary."""

    def compare(
        self, baseline_report: object, candidate_report: object, **kwargs: object
    ) -> object:
        """Compare two Lab-owned reports."""


@dataclass(frozen=True)
class LabEvaluationResult:
    """Autopilot references plus opaque Lab-owned evaluation outputs."""

    evaluation_reference: EvaluationReference
    baseline_report: object | None
    candidate_report: object | None
    comparison: object | None

    @property
    def evaluation(self) -> EvaluationReference:
        """Return the stored evaluation reference."""

        return self.evaluation_reference

    @property
    def comparison_reference(self) -> object | None:
        """Return the opaque Lab comparison record."""

        return self.comparison


class ImprovementLabEvaluationAdapter:
    """Submit baseline and candidate identities to the public Lab boundary."""

    def __init__(
        self,
        runner: LabEvaluationRunnerPort,
        comparator: LabComparisonPort,
        *,
        evaluation_store: EvaluationReferenceStore | None = None,
    ) -> None:
        self.runner = runner
        self.comparator = comparator
        self.evaluation_store = evaluation_store

    def evaluate(
        self,
        baseline_candidate: object,
        candidate: object,
        *,
        baseline_reference: CandidateReference,
        candidate_reference: CandidateReference,
        dataset: object,
        baseline_manifest: object,
        candidate_manifest: object,
        dataset_reference: str | None = None,
        case_data_refs: Iterable[str] = (),
        operational_evidence_refs: Iterable[str] = (),
        repeat: int = 1,
        promotion_evidence_id: str | None = None,
        baseline_snapshot: object | None = None,
        candidate_snapshot: object | None = None,
    ) -> LabEvaluationResult:
        """Evaluate exactly once per identity and compare through the Lab."""

        if isinstance(baseline_candidate, OperationalDisposition) or isinstance(
            candidate, OperationalDisposition
        ):
            raise ImprovementLabEvaluationError("NO_CHANGE dispositions must not enter the Lab")
        if not isinstance(baseline_reference, CandidateReference):
            raise ImprovementLabEvaluationError("baseline_reference must be a CandidateReference")
        if not isinstance(candidate_reference, CandidateReference):
            raise ImprovementLabEvaluationError("candidate_reference must be a CandidateReference")
        if baseline_reference.tenant_id != candidate_reference.tenant_id:
            raise ImprovementLabEvaluationError("baseline and candidate must share a tenant")
        if baseline_reference.candidate_id == candidate_reference.candidate_id:
            raise ImprovementLabEvaluationError("baseline and candidate identities must differ")
        if repeat < 1:
            raise ImprovementLabEvaluationError("repeat must be at least 1")
        if dataset is None:
            raise ImprovementLabEvaluationError("dataset is required")

        self._validate_candidate_identity(baseline_candidate, baseline_reference, "baseline")
        self._validate_candidate_identity(candidate, candidate_reference, "candidate")
        _validate_manifest_provenance(baseline_reference, baseline_manifest, "baseline")
        _validate_manifest_provenance(candidate_reference, candidate_manifest, "candidate")
        dataset_ref = _dataset_reference(dataset, dataset_reference)
        case_refs = _references(case_data_refs, "case_data_refs")
        operational_refs = _references(operational_evidence_refs, "operational_evidence_refs")
        common_evidence = _evaluation_evidence(
            baseline_reference=baseline_reference,
            candidate_reference=candidate_reference,
            dataset_reference=dataset_ref,
            case_data_refs=case_refs,
            operational_evidence_refs=operational_refs,
            baseline_manifest=baseline_manifest,
            candidate_manifest=candidate_manifest,
        )
        evaluation_id = _stable_id(
            "evaluation",
            {
                "tenant_id": candidate_reference.tenant_id,
                "baseline_candidate_id": baseline_reference.candidate_id,
                "candidate_id": candidate_reference.candidate_id,
                "dataset_reference": dataset_ref,
            },
        )

        baseline_report: object | None = None
        candidate_report: object | None = None
        try:
            baseline_report = _report(
                self._run(baseline_candidate, baseline_manifest, dataset, repeat=repeat)
            )
            candidate_report = _report(
                self._run(candidate, candidate_manifest, dataset, repeat=repeat)
            )
        except Exception as exc:
            reference = self._store_reference(
                EvaluationReference(
                    evaluation_id=evaluation_id,
                    tenant_id=candidate_reference.tenant_id,
                    baseline_candidate_id=baseline_reference.candidate_id,
                    candidate_id=candidate_reference.candidate_id,
                    comparison_id=None,
                    promotion_evidence_id=None,
                    status=EVALUATION_FAILED,
                    evidence_refs=_append_evidence(
                        *common_evidence,
                        *_lab_evidence_refs(baseline_report),
                        *_lab_evidence_refs(candidate_report),
                        f"lab:evaluation-failure:{type(exc).__name__}",
                    ),
                    proposal_id=candidate_reference.proposal_id,
                    baseline_inventory_snapshot_id=candidate_reference.baseline_inventory_snapshot_id,
                    resolved_graph_digest=candidate_reference.resolved_graph_digest,
                )
            )
            return LabEvaluationResult(reference, baseline_report, candidate_report, None)

        try:
            comparison = self._compare(
                baseline_report,
                candidate_report,
                baseline_snapshot=baseline_snapshot,
                candidate_snapshot=candidate_snapshot,
                baseline_candidate=baseline_candidate,
                candidate_candidate=candidate,
                baseline_manifest=baseline_manifest,
                candidate_manifest=candidate_manifest,
            )
            if comparison is None:
                raise ImprovementLabEvaluationError("Lab comparison returned no comparison record")
        except Exception as exc:
            reference = self._store_reference(
                EvaluationReference(
                    evaluation_id=evaluation_id,
                    tenant_id=candidate_reference.tenant_id,
                    baseline_candidate_id=baseline_reference.candidate_id,
                    candidate_id=candidate_reference.candidate_id,
                    comparison_id=None,
                    promotion_evidence_id=None,
                    status=EVALUATION_FAILED,
                    evidence_refs=_append_evidence(
                        *common_evidence,
                        *_lab_evidence_refs(baseline_report),
                        *_lab_evidence_refs(candidate_report),
                        f"lab:comparison-failure:{type(exc).__name__}",
                    ),
                    proposal_id=candidate_reference.proposal_id,
                    baseline_inventory_snapshot_id=candidate_reference.baseline_inventory_snapshot_id,
                    resolved_graph_digest=candidate_reference.resolved_graph_digest,
                )
            )
            return LabEvaluationResult(reference, baseline_report, candidate_report, None)

        comparison_id = _optional_text(comparison, "comparison_id")
        promotion_id = promotion_evidence_id or _optional_text(comparison, "promotion_evidence_id")
        evidence = _append_evidence(
            *common_evidence,
            f"lab:comparison:{comparison_id}" if comparison_id else "",
            f"lab:promotion:{promotion_id}" if promotion_id else "",
            _reference_marker(_report_run_id(baseline_report), "lab:run"),
            _reference_marker(_report_run_id(candidate_report), "lab:run"),
            *_lab_evidence_refs(baseline_report),
            *_lab_evidence_refs(candidate_report),
            *_lab_evidence_refs(comparison),
        )
        reference = self._store_reference(
            EvaluationReference(
                evaluation_id=evaluation_id,
                tenant_id=candidate_reference.tenant_id,
                baseline_candidate_id=baseline_reference.candidate_id,
                candidate_id=candidate_reference.candidate_id,
                comparison_id=comparison_id,
                promotion_evidence_id=promotion_id,
                status=EVALUATION_SUCCEEDED,
                evidence_refs=evidence,
                proposal_id=candidate_reference.proposal_id,
                baseline_inventory_snapshot_id=candidate_reference.baseline_inventory_snapshot_id,
                resolved_graph_digest=candidate_reference.resolved_graph_digest,
            )
        )
        return LabEvaluationResult(reference, baseline_report, candidate_report, comparison)

    def _run(
        self,
        candidate: object,
        manifest: object,
        dataset: object,
        *,
        repeat: int,
    ) -> object:
        run_sync = getattr(self.runner, "run_sync", None)
        if callable(run_sync):
            return _call_with_supported_keywords(
                run_sync,
                (dataset, candidate, manifest),
                {"repeat": repeat},
            )
        run_async = getattr(self.runner, "run", None)
        if not callable(run_async):
            raise ImprovementLabEvaluationError("Lab runner must expose run_sync or run")
        result = _call_with_supported_keywords(
            run_async,
            (dataset, candidate, manifest),
            {"repeat": repeat},
        )
        return _await_sync(result)

    def _compare(
        self,
        baseline_report: object,
        candidate_report: object,
        **kwargs: object,
    ) -> object:
        compare = getattr(self.comparator, "compare", None)
        if not callable(compare):
            raise ImprovementLabEvaluationError("Lab comparator must expose compare")
        return _call_with_supported_keywords(compare, (baseline_report, candidate_report), kwargs)

    def _validate_candidate_identity(
        self,
        candidate: object,
        reference: CandidateReference,
        label: str,
    ) -> None:
        candidate_id = _optional_text(candidate, "candidate_id")
        nested = _field(candidate, "candidate")
        if candidate_id is None and nested is not None:
            candidate_id = _optional_text(nested, "candidate_id")
        if candidate_id != reference.candidate_id:
            raise ImprovementLabEvaluationError(
                f"{label} Lab candidate identity does not match CandidateReference"
            )

    def _store_reference(self, reference: EvaluationReference) -> EvaluationReference:
        if self.evaluation_store is None:
            return reference
        try:
            return self.evaluation_store.insert(reference)
        except Exception as exc:
            raise ImprovementLabEvaluationError("evaluation reference could not be stored") from exc


def _validate_manifest_provenance(
    reference: CandidateReference,
    manifest: object,
    label: str,
) -> None:
    for field_name, expected in (
        (("manifest_id", "resolved_manifest_id"), reference.manifest_id),
        (("manifest_digest", "resolved_manifest_digest"), reference.manifest_digest),
        ("registry_snapshot_id", reference.registry_snapshot_id),
    ):
        names = field_name if isinstance(field_name, tuple) else (field_name,)
        actual = next(
            (value for name in names if (value := _optional_text(manifest, name)) is not None),
            None,
        )
        if actual is None:
            raise ImprovementLabEvaluationError(f"{label} manifest is missing provenance")
        if actual != expected:
            raise ImprovementLabEvaluationError(
                f"{label} manifest provenance does not match CandidateReference"
            )


def _dataset_reference(dataset: object, supplied: str | None) -> str:
    if supplied is not None:
        return non_blank(supplied, "dataset_reference")
    dataset_id = _optional_text(dataset, "dataset_id")
    version = _optional_text(dataset, "version") or _optional_text(dataset, "dataset_version")
    if dataset_id is None or version is None:
        raise ImprovementLabEvaluationError(
            "dataset_reference or dataset_id and version are required"
        )
    return f"{dataset_id}@{version}"


def _evaluation_evidence(
    *,
    baseline_reference: CandidateReference,
    candidate_reference: CandidateReference,
    dataset_reference: str,
    case_data_refs: tuple[str, ...],
    operational_evidence_refs: tuple[str, ...],
    baseline_manifest: object,
    candidate_manifest: object,
) -> tuple[str, ...]:
    values = [
        *operational_evidence_refs,
        *case_data_refs,
        f"dataset:{dataset_reference}",
    ]
    for reference in (baseline_reference, candidate_reference):
        values.extend(
            (
                f"candidate:{reference.candidate_id}",
                f"harness:manifest:{reference.manifest_id}",
                f"harness:digest:{reference.manifest_digest}",
                f"harness:registry:{reference.registry_snapshot_id}",
            )
        )
    for manifest in (baseline_manifest, candidate_manifest):
        environment_id = _optional_text(manifest, "environment_snapshot_id")
        if environment_id is not None:
            values.append(f"lab:environment:{environment_id}")
    return _append_evidence(*values)


def _references(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    result = tuple(non_blank(value, field_name) for value in values)
    unique_values(result, field_name)
    return result


def _append_evidence(*values: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _reference_marker(value: str | None, prefix: str) -> str:
    return f"{prefix}:{value}" if value else ""


def _report(result: object) -> object:
    report = _field(result, "report")
    return result if report is None else report


def _report_run_id(report: object | None) -> str | None:
    return _optional_text(report, "run_id") if report is not None else None


def _lab_evidence_refs(value: object | None) -> tuple[str, ...]:
    """Copy only explicit evidence references from Lab-owned result records."""

    if value is None:
        return ()
    values: list[str] = []
    environment_id = _optional_text(value, "environment_snapshot_id")
    if environment_id is not None:
        values.append(f"lab:environment:{environment_id}")
    for field_name in ("evidence_refs",):
        raw = _field(value, field_name, ())
        if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Iterable):
            continue
        values.extend(item for item in raw if isinstance(item, str) and item.strip())
    for field_name in ("failures", "scores", "case_results"):
        raw = _field(value, field_name, ())
        if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Iterable):
            continue
        for item in raw:
            values.extend(_lab_evidence_refs(item))
    return _append_evidence(*values)


def _optional_text(value: object | None, name: str) -> str | None:
    raw = _field(value, name, None) if value is not None else None
    if isinstance(raw, Enum):
        raw = raw.value
    return raw if isinstance(raw, str) and raw.strip() else None


def _field(value: object | None, name: str, default: object | None = None) -> object | None:
    if isinstance(value, Mapping):
        return cast(object | None, value.get(name, default))
    return cast(object | None, getattr(value, name, default))


def _call_with_supported_keywords(
    function: Any,
    args: tuple[object, ...],
    keywords: Mapping[str, object],
) -> object:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(*args, **dict(keywords))
    parameters = signature.parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return function(*args, **dict(keywords))
    supported = {
        name: value
        for name, value in keywords.items()
        if name in parameters
        and parameters[name].kind
        in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    return function(*args, **supported)


def _await_sync(value: object) -> object:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(cast(Any, value))
    raise ImprovementLabEvaluationError(
        "synchronous Lab adapter cannot run an async runner inside an active event loop"
    )


def _stable_id(prefix: str, value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


__all__ = [
    "EVALUATION_FAILED",
    "EVALUATION_SUCCEEDED",
    "ImprovementLabEvaluationAdapter",
    "ImprovementLabEvaluationError",
    "LabComparisonPort",
    "LabEvaluationResult",
    "LabEvaluationRunnerPort",
]
