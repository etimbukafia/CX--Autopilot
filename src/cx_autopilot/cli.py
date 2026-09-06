"""Minimal CLI for the local reference workflow and lineage inspection."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from typing import Any, cast

from .clustering import OpportunityClusterer
from .contracts.common import to_jsonable
from .decisions import DecisionService
from .integrations.cx_platform import CXPlatformEvidenceAdapter
from .opportunities import OpportunityDiscoverer
from .reference import (
    REFERENCE_NOW,
    REFERENCE_TENANT_ID,
    build_reference_cx_source,
    run_reference_cycle,
)
from .storage import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    """Build the intentionally small reference-workflow parser."""

    parser = argparse.ArgumentParser(prog="cx-autopilot")
    parser.add_argument(
        "--db",
        default=".cx-autopilot.sqlite",
        help="SQLite path; place global options before the command.",
    )
    parser.add_argument("--tenant-id", default=REFERENCE_TENANT_ID)
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest a local fixture")
    ingest_commands = ingest.add_subparsers(dest="ingest_command", required=True)
    ingest_fixture = ingest_commands.add_parser("fixture", help="ingest the CX fixture")
    ingest_fixture.set_defaults(action="ingest_fixture")

    discover = commands.add_parser("discover", help="discover and cluster stored signals")
    discover.set_defaults(action="discover")

    inspect = commands.add_parser("inspect", help="inspect one stored record or lineage")
    inspect_commands = inspect.add_subparsers(dest="inspect_command", required=True)
    for name in ("opportunity", "inventory", "diagnosis", "proposal"):
        record_parser = inspect_commands.add_parser(name)
        record_parser.add_argument("record_id")
        record_parser.set_defaults(action="inspect_record", record_type=name)
    lineage = inspect_commands.add_parser("lineage")
    lineage.add_argument("decision_id")
    lineage.set_defaults(action="inspect_lineage")

    run = commands.add_parser("run", help="run one planned reference cycle")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    reference = run_commands.add_parser("reference")
    reference_commands = reference.add_subparsers(dest="reference_command", required=True)
    cycle = reference_commands.add_parser("cycle", help="run the transaction-history cycle")
    cycle.set_defaults(action="reference_cycle")

    record = commands.add_parser("record", help="record a human decision")
    record_commands = record.add_subparsers(dest="record_command", required=True)
    decision = record_commands.add_parser("decision")
    decision.add_argument("--subject-type", choices=("pilot", "disposition"), required=True)
    decision.add_argument("--subject-id", required=True)
    decision.add_argument("--decision", required=True)
    decision.add_argument("--actor-ref", required=True)
    decision.add_argument("--reason", required=True)
    decision.add_argument("--evidence-ref", action="append", default=[])
    decision.set_defaults(action="record_decision")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one supported CLI command and return a process status."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    with SQLiteStore(args.db) as store:
        if args.action == "ingest_fixture":
            source = build_reference_cx_source()
            result = CXPlatformEvidenceAdapter(
                source,
                tenant_id=args.tenant_id,
            ).ingest(store.signals, as_of=REFERENCE_NOW + timedelta(days=1))
            _print_json(_ingestion_summary(result))
            return 0
        if args.action == "discover":
            return _discover_and_print(store, args.tenant_id)
        if args.action == "inspect_record":
            return _inspect_record(store, args.tenant_id, args.record_type, args.record_id)
        if args.action == "inspect_lineage":
            audit = DecisionService(store).audit(args.decision_id, tenant_id=args.tenant_id)
            _print_json(audit)
            return 0
        if args.action == "reference_cycle":
            cycle_result = run_reference_cycle(store, tenant_id=args.tenant_id)
            _print_json(
                {
                    "signal_count": len(cycle_result.ingestion.signals),
                    "inserted_signal_count": len(cycle_result.ingestion.inserted_signal_ids),
                    "duplicate_signal_count": len(
                        cycle_result.duplicate_ingestion.duplicate_signal_ids
                    ),
                    "opportunity_ids": [item.opportunity_id for item in cycle_result.opportunities],
                    "cluster_ids": [item.cluster_id for item in cycle_result.clusters],
                    "inventory_snapshot_id": cycle_result.inventory.snapshot_id,
                    "diagnosis_id": cycle_result.diagnosis.diagnosis_id,
                    "proposal_id": cycle_result.proposal.proposal_id,
                    "candidate_id": cycle_result.candidate.candidate_reference.candidate_id,
                    "evaluation_id": cycle_result.evaluation.evaluation_reference.evaluation_id,
                    "comparison_id": cycle_result.evaluation.evaluation_reference.comparison_id,
                    "recommendation_id": cycle_result.recommendation.recommendation_id,
                    "decision_id": cycle_result.decision.decision_id,
                    "decision": cycle_result.decision.decision,
                    "production_authority_unchanged": (
                        cycle_result.production_authority_before
                        == cycle_result.production_authority_after
                    ),
                }
            )
            return 0
        if args.action == "record_decision":
            return _record_decision(store, args)
    raise ValueError("unsupported CLI command")


def _discover_and_print(store: SQLiteStore, tenant_id: str) -> int:
    signals = tuple(store.signals.list(tenant_id=tenant_id))
    if not signals:
        raise ValueError("no stored signals are available; run ingest fixture first")
    opportunities = OpportunityDiscoverer().discover(signals, tenant_id=tenant_id)
    for opportunity in opportunities:
        store.opportunities.insert(opportunity)
    clusters = OpportunityClusterer().cluster(opportunities, tenant_id=tenant_id)
    for cluster in clusters:
        store.opportunity_clusters.insert(cluster)
    _print_json(
        {
            "opportunity_ids": [item.opportunity_id for item in opportunities],
            "cluster_ids": [item.cluster_id for item in clusters],
        }
    )
    return 0


def _inspect_record(
    store: SQLiteStore,
    tenant_id: str,
    record_type: str,
    record_id: str,
) -> int:
    repositories = {
        "opportunity": store.opportunities,
        "inventory": store.inventory,
        "diagnosis": store.diagnoses,
        "proposal": store.proposals,
    }
    repository = cast(Any, repositories[record_type])
    record = repository.get(record_id, tenant_id=tenant_id)
    if record is None:
        raise ValueError(f"{record_type} was not found in the requested tenant")
    _print_json(record)
    return 0


def _record_decision(store: SQLiteStore, args: argparse.Namespace) -> int:
    service = DecisionService(store)
    if args.subject_type == "pilot":
        recommendation = store.recommendations.get(
            args.subject_id,
            tenant_id=args.tenant_id,
        )
        if recommendation is None:
            raise ValueError("pilot recommendation was not found in the requested tenant")
        decision = service.record_pilot_decision(
            recommendation,
            args.decision,
            args.actor_ref,
            args.reason,
            evidence_refs=args.evidence_ref,
        )
    else:
        disposition = store.dispositions.get(
            args.subject_id,
            tenant_id=args.tenant_id,
        )
        if disposition is None:
            raise ValueError("operational disposition was not found in the requested tenant")
        decision = service.record_disposition_decision(
            disposition,
            args.decision,
            args.actor_ref,
            args.reason,
            evidence_refs=args.evidence_ref,
        )
    _print_json(decision)
    return 0


def _ingestion_summary(result: object) -> dict[str, object]:
    return {
        "tenant_id": getattr(result, "tenant_id"),
        "signal_count": len(getattr(result, "signals")),
        "inserted_signal_ids": list(getattr(result, "inserted_signal_ids")),
        "duplicate_signal_ids": list(getattr(result, "duplicate_signal_ids")),
        "unavailable_source_refs": list(getattr(result, "unavailable_source_refs")),
    }


def _print_json(value: object) -> None:
    if is_dataclass(value):
        value = asdict(cast(Any, value))
    if hasattr(value, "model_dump"):
        value = cast(Any, value).model_dump(mode="python")
    print(json.dumps(to_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True))


__all__ = ["build_parser", "main"]
